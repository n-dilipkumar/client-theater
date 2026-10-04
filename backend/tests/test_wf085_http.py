"""WF-085 over HTTP: the surface this feature's own router serves.

The domain rules are in ``test_wf085.py``. This file is the other half, and it is
organised by what a caller can observe:

``the route table``
    That the host mounted every route by discovery alone, with no shared file edited,
    and that no two of them collide.
``residency``
    The region record, the re-stamp, and the refusal for a role below the administrator
    tier.
``retention``
    The policy, the schedule, the purge, and the refusal to configure a window past the
    researched ceiling.
``consent``
    The gate's three answers, and the deny branch that fails closed.
``the DSAR``
    Per-subject erasure, and the residue it reports rather than hides.
``the error shapes``
    Every status code and body this router can produce.
``the audit-source rule``
    Every ``source=`` this workflow records names a route the host actually mounted.
``the read-path rule``
    The reads write no audit rows, because a route that logged every read would fill this
    product's own guarantee with entries describing no change.
"""

from __future__ import annotations

import importlib
import inspect
import json
from datetime import datetime, timezone
from typing import Any

import dsr.features as host
from dsr.permissions import INSTANCE_ADMIN
from dsr.security_governance import privacy_rules as rules, residency as vocab
from fastapi.testclient import TestClient

FEATURE_MODULE = "dsr.features.wf085_meet_gdpr_ccpa_residency_retention_dsar"
PREFIX = "/api/wf-085"
FEATURE_ID = "wf-085-meet-gdpr-ccpa-residency-retention-dsar"

NOW = datetime(2026, 10, 4, 9, 0, tzinfo=timezone.utc)
BASE_MS = int(NOW.timestamp() * 1000)
DAY_MS = 86400000
ADMIN = INSTANCE_ADMIN
ADMIN_PARAMS = {"role": ADMIN, "actor": "dana"}


def make_view(
    client: TestClient,
    email: str = "buyer@northwind.example",
    *,
    days_ago: float = 2.0,
    room_id: str = "room_a",
) -> dict[str, Any]:
    """One engagement row, written through the audited store rather than this router.

    WF-075 provisions these rows and this workflow is not that producer, so the fixture
    writes the row as data rather than through a route that would claim to serve it.
    """

    store = _store(client)
    return store.create(
        "wf075_view",
        {
            "link_id": "link_a",
            "dataroom_id": room_id,
            "viewer_email": email,
            "view_type": "link",
            "viewed_at": BASE_MS - int(days_ago * DAY_MS),
            "page_durations": [{"page_number": 1, "duration_seconds": 45}],
            "location": {"country": "Germany", "city": "Berlin"},
            "client": {"browser": "Edge", "os": "Windows", "device": "desktop"},
        },
        room_id=room_id,
        actor="system",
        source="wf-085 test fixture",
    )


def _store(client: TestClient):
    from dsr.api import app

    return app.state.store


def pin(client: TestClient, region: str = "eu-west", room_id: str = "room_a", **extra: str):
    response = client.post(
        f"{PREFIX}/residency",
        params={"room_id": room_id, **ADMIN_PARAMS},
        json={"region": region, "transfer_mechanism": vocab.TRANSFER_SCC, **extra},
    )
    assert response.status_code == 201, response.text
    return response.json()


def consent(client: TestClient, subject: str, *, signal: str | None = None, **body: Any):
    response = client.post(
        f"{PREFIX}/consent",
        params={"room_id": "room_a"},
        json={"region": "eu-west", "subject": subject, "signal": signal, **body},
    )
    assert response.status_code == 201, response.text
    return response.json()


def open_dsar(client: TestClient, subject: str):
    response = client.post(
        f"{PREFIX}/dsar/requests",
        params={"room_id": "room_a", **ADMIN_PARAMS},
        json={"subject": subject},
    )
    assert response.status_code == 201, response.text
    return response.json()


def fulfil(client: TestClient, request_id: str):
    return client.post(f"{PREFIX}/dsar/requests/{request_id}/fulfil", params=dict(ADMIN_PARAMS))


# --------------------------------------------------------------------------- #
# the route table
# --------------------------------------------------------------------------- #


class TestRouteTable:
    def test_the_feature_is_installed_with_its_routes(self, client: TestClient):
        payload = client.get("/api/features").json()
        installed = next(f for f in payload["features"] if f["id"] == FEATURE_ID)
        paths = {route["path"] for route in installed["routes"]}

        assert f"{PREFIX}/summary" in paths
        assert f"{PREFIX}/residency" in paths
        assert f"{PREFIX}/retention/schedule" in paths
        assert f"{PREFIX}/consent" in paths
        assert f"{PREFIX}/dsar/requests/{{request_id}}/fulfil" in paths

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
        assert len(mounted) == 16

    def test_the_prefix_is_ticket_derived(self):
        assert PREFIX == "/api/wf-085"

    def test_no_feature_imports_the_app(self):
        """Dependencies come from ``dsr.deps``. A test enforces it project-wide."""

        module = importlib.import_module(FEATURE_MODULE)

        assert "dsr.api" not in module.__dict__

    def test_the_feature_never_opens_the_database_itself(self):
        source = open(importlib.import_module(FEATURE_MODULE).__file__, encoding="utf-8").read()

        assert "sqlite3" not in source
        assert ".connect(" not in source

    def test_the_error_types_are_this_features_own(self):
        """A handler for a shared type would intercept that exception across the product."""

        module = importlib.import_module(FEATURE_MODULE)

        assert set(module.EXCEPTION_HANDLERS) == {
            rules.PrivacyRefusal,
            rules.PrivacyNotFound,
            rules.PrivacyAdministratorRequired,
        }
        for handler in module.EXCEPTION_HANDLERS.values():
            assert list(inspect.signature(handler).parameters) == ["request", "exc"]


# --------------------------------------------------------------------------- #
# residency
# --------------------------------------------------------------------------- #


class TestResidencyOverHttp:
    def test_the_region_is_readable_with_its_jurisdiction(self, client: TestClient):
        pin(client, "eu-west")

        payload = client.get(f"{PREFIX}/residency", params={"room_id": "room_a"}).json()

        assert payload["residency"]["residency_region"] == "eu-west"
        assert payload["residency"]["jurisdiction"] == vocab.JURISDICTION_EEA
        assert payload["residency"]["transfer_declared"] is True

    def test_a_move_reports_what_it_moved_over_http(self, client: TestClient):
        make_view(client, days_ago=2)

        payload = pin(client, "eu-west")

        assert payload["relocation"]["records_moved"] >= 1
        assert payload["relocation"]["by_collection"]["wf075_view"] == 1

    def test_a_move_from_a_role_below_the_administrator_tier_is_a_403(self, client: TestClient):
        response = client.post(
            f"{PREFIX}/residency",
            params={"room_id": "room_a", "role": "room_collaborator", "actor": "dana"},
            json={"region": "eu-west"},
        )

        assert response.status_code == 403
        body = response.json()
        assert body["error"] == "administrator_required"
        assert body["required_role"] == ADMIN
        assert body["presented_role"] == "room_collaborator"

    def test_a_move_with_no_role_at_all_is_a_403(self, client: TestClient):
        response = client.post(f"{PREFIX}/residency", json={"region": "eu-west"})

        assert response.status_code == 403

    def test_an_unknown_region_is_a_400_that_names_the_known_set(self, client: TestClient):
        response = client.post(
            f"{PREFIX}/residency",
            params={"room_id": "room_a", **ADMIN_PARAMS},
            json={"region": "mars-central"},
        )

        assert response.status_code == 400
        body = response.json()
        assert body["error"] == "invalid_privacy_request"
        assert "eu-west" in body["errors"]["region"]

    def test_the_response_names_the_vendor_claims_as_unverified(self, client: TestClient):
        """The research is preserved; it is not rendered as this room's status."""

        payload = client.get(f"{PREFIX}/residency").json()

        assert payload["vendor_claims"]
        for claim in payload["vendor_claims"]:
            assert claim["verified"] is False
        assert "no certification is claimed" in payload["certification_note"].lower()

    def test_the_jurisdictions_route_says_which_of_them_need_consent(self, client: TestClient):
        payload = client.get(f"{PREFIX}/residency").json()
        listed = {row["id"]: row["consent_required"] for row in payload["jurisdictions"]}

        assert listed[vocab.JURISDICTION_EEA] is True
        assert listed[vocab.JURISDICTION_UK] is True
        assert listed[vocab.JURISDICTION_CH] is True
        assert listed[vocab.JURISDICTION_US] is False

    def test_an_unconfigured_deployment_answers_with_a_null_record_and_not_an_error(
        self, client: TestClient
    ):
        payload = client.get(f"{PREFIX}/residency", params={"room_id": "no-such-room"}).json()

        assert payload["residency"] is None
        assert payload["records_unstamped"] == 0


# --------------------------------------------------------------------------- #
# retention
# --------------------------------------------------------------------------- #


class TestRetentionOverHttp:
    def test_the_policy_names_each_class_window_and_evidence(self, client: TestClient):
        payload = client.get(f"{PREFIX}/retention/policy", params={"room_id": "room_a"}).json()

        classes = {row["class"]: row for row in payload["policy"]}
        assert classes[vocab.CLASS_SESSION_RECORDING]["configured_days"] == 30
        assert classes[vocab.CLASS_SESSION_RECORDING]["evidence"]
        assert payload["window_unit"] == "days"

    def test_a_shorter_window_is_accepted_and_reported_below_the_ceiling(self, client: TestClient):
        pin(client, "eu-west")

        response = client.post(
            f"{PREFIX}/retention/policy",
            params={"room_id": "room_a", **ADMIN_PARAMS},
            json={"windows": {vocab.CLASS_SESSION_RECORDING: 14}},
        )

        assert response.status_code == 200
        strict = next(
            row
            for row in response.json()["policy"]
            if row["class"] == vocab.CLASS_SESSION_RECORDING
        )
        assert strict["configured_days"] == 14
        assert strict["ceiling_days"] == 30

    def test_a_window_past_the_ceiling_is_a_400_that_names_the_ceiling(self, client: TestClient):
        pin(client, "eu-west")

        response = client.post(
            f"{PREFIX}/retention/policy",
            params={"room_id": "room_a", **ADMIN_PARAMS},
            json={"windows": {vocab.CLASS_SESSION_RECORDING: 365}},
        )

        assert response.status_code == 400
        assert "30" in response.json()["errors"][vocab.CLASS_SESSION_RECORDING]

    def test_a_window_from_a_role_below_the_administrator_tier_is_a_403(self, client: TestClient):
        pin(client, "eu-west")

        response = client.post(
            f"{PREFIX}/retention/policy",
            params={"room_id": "room_a", "role": "content_contributor"},
            json={"windows": {vocab.CLASS_SESSION_RECORDING: 14}},
        )

        assert response.status_code == 403

    def test_the_policy_before_a_region_is_a_404_not_a_500(self, client: TestClient):
        response = client.post(
            f"{PREFIX}/retention/policy",
            params={"room_id": "no-such-room", **ADMIN_PARAMS},
            json={"windows": {vocab.CLASS_SESSION_RECORDING: 14}},
        )

        assert response.status_code == 404
        assert response.json()["error"] == "not_found"

    def test_the_schedule_separates_due_from_retained(self, client: TestClient):
        make_view(client, days_ago=400, email="buyer@northwind.example")
        make_view(client, days_ago=2, email="buyer@halcyon.example")
        pin(client, "eu-west")

        payload = client.get(f"{PREFIX}/retention/schedule", params={"room_id": "room_a"}).json()
        strict = next(
            row for row in payload["classes"] if row["class"] == vocab.CLASS_SESSION_RECORDING
        )

        assert strict["due"] == 1
        assert strict["retained"] == 1
        assert payload["undated"] == 0

    def test_a_run_erases_what_is_due_over_http(self, client: TestClient):
        make_view(client, days_ago=400, email="buyer@northwind.example")
        make_view(client, days_ago=2, email="buyer@halcyon.example")
        pin(client, "eu-west")

        response = client.post(
            f"{PREFIX}/retention/run", params={"room_id": "room_a", **ADMIN_PARAMS}
        )

        assert response.status_code == 200
        assert response.json()["erased"] == 1
        rows = client.get("/api/records/wf075_view", params={"room_id": "room_a"}).json()
        assert [row["data"]["viewer_email"] for row in rows["records"]] == ["buyer@halcyon.example"]

    def test_a_run_from_a_role_below_the_administrator_tier_is_a_403(self, client: TestClient):
        response = client.post(f"{PREFIX}/retention/run", params={"room_id": "room_a"})

        assert response.status_code == 403

    def test_the_policy_names_a_collection_nothing_ages(self, client: TestClient):
        """The "no longer-lived side channels" check, visible over HTTP."""

        _store(client).create(
            "wf085_side_channel",
            {"subject": "buyer@northwind.example"},
            room_id="room_a",
            actor="dana",
            source="test",
        )

        payload = client.get(f"{PREFIX}/retention/policy", params={"room_id": "room_a"}).json()

        assert "wf085_side_channel" in payload["unmapped_collections"]
        assert "longer-lived copy" in payload["no_side_channel_rule"]


# --------------------------------------------------------------------------- #
# consent
# --------------------------------------------------------------------------- #


class TestConsentOverHttp:
    def test_an_explicit_grant_is_the_only_way_to_track(self, client: TestClient):
        payload = consent(client, "buyer@northwind.example", signal="granted")

        assert payload["outcome"] == vocab.GATE_TRACK
        assert payload["state"] == vocab.ACTIVE

    def test_a_missing_signal_denies_and_writes_no_cookies(self, client: TestClient):
        """The defect the issue names by name, exercised over the wire."""

        payload = consent(client, "buyer@northwind.example")

        assert payload["outcome"] == vocab.GATE_DENY
        assert payload["decision"]["effect"]["cookies"] is False
        assert payload["decision"]["effect"]["identifier"] == "unique_per_page_view"

    def test_a_global_privacy_control_opt_out_denies_despite_a_grant(self, client: TestClient):
        payload = consent(client, "buyer@northwind.example", signal="granted", gpc="1")

        assert payload["outcome"] == vocab.GATE_DENY
        assert payload["decision"]["opted_out"] == ["gpc"]

    def test_a_deny_after_a_grant_reads_as_a_revocation(self, client: TestClient):
        consent(client, "buyer@northwind.example", signal="granted")

        payload = consent(client, "buyer@northwind.example")

        assert payload["state"] == vocab.REVOKED
        assert payload["cookies_cleared"] is True
        assert payload["tracking_blocked"] is True

    def test_a_dnt_signal_is_reported_as_discarded(self, client: TestClient):
        payload = consent(client, "buyer@northwind.example", signal="dnt")

        assert payload["outcome"] == vocab.GATE_DENY
        assert [row["signal"] for row in payload["decision"]["signals_ignored"]] == ["dnt"]

    def test_an_unlisted_region_reports_not_required_rather_than_tracking_silently(
        self, client: TestClient
    ):
        response = client.post(
            f"{PREFIX}/consent",
            params={"room_id": "room_a"},
            json={"region": "us-east", "subject": "buyer@northwind.example"},
        )

        assert response.status_code == 201
        assert response.json()["outcome"] == vocab.CONSENT_NOT_REQUIRED

    def test_the_gate_can_be_evaluated_without_writing_anything(self, client: TestClient):
        response = client.get(
            f"{PREFIX}/consent", params={"region": "eu-west", "signal": "granted"}
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["evaluation"]["outcome"] == vocab.GATE_TRACK
        assert payload["recorded"] == []
        assert payload["fails_closed"] is True

    def test_a_consent_call_without_a_region_is_a_400(self, client: TestClient):
        response = client.post(f"{PREFIX}/consent", json={"subject": "buyer@northwind.example"})

        assert response.status_code == 400
        assert "region" in response.json()["errors"]

    def test_a_consent_call_without_a_subject_is_a_400(self, client: TestClient):
        response = client.post(f"{PREFIX}/consent", json={"region": "eu-west"})

        assert response.status_code == 400
        assert "subject" in response.json()["errors"]

    def test_recording_a_consent_signal_is_not_administrator_gated(self, client: TestClient):
        """The specification says consent is captured at the page, not by an operator."""

        response = client.post(
            f"{PREFIX}/consent",
            params={"room_id": "room_a"},
            json={"region": "eu-west", "subject": "buyer@northwind.example", "signal": "granted"},
        )

        assert response.status_code == 201

    def test_the_vocabulary_route_names_the_unsupported_signal(self, client: TestClient):
        payload = client.get(f"{PREFIX}/vocabulary").json()

        assert payload["unsupported_signals"] == list(vocab.UNSUPPORTED_SIGNALS)
        assert payload["grant_word"] == vocab.CONSENT_GRANTED
        assert payload["outcomes"] == list(vocab.GATE_OUTCOMES)


# --------------------------------------------------------------------------- #
# the DSAR
# --------------------------------------------------------------------------- #


class TestDsarOverHttp:
    def test_a_request_reports_what_it_found(self, client: TestClient):
        make_view(client, "buyer@northwind.example")
        pin(client, "eu-west")

        payload = open_dsar(client, "buyer@northwind.example")

        assert payload["found"] == 1
        assert payload["found_by_collection"] == {"wf075_view": 1}
        assert payload["state"] == vocab.DSAR_OPENED

    def test_a_request_reports_the_personal_fields_each_record_holds(self, client: TestClient):
        make_view(client, "buyer@northwind.example")
        pin(client, "eu-west")

        payload = open_dsar(client, "buyer@northwind.example")
        fields = payload["personal_data"][0]["personal_data"]["fields"]

        assert "viewer_email" in fields
        assert "location.city" in fields

    def test_fulfilment_erases_the_subject_only(self, client: TestClient):
        make_view(client, "buyer@northwind.example")
        keep = make_view(client, "buyer@halcyon.example")
        pin(client, "eu-west")
        request = open_dsar(client, "buyer@northwind.example")

        response = fulfil(client, request["id"])

        assert response.status_code == 200
        assert response.json()["erased"] == 1
        assert _store(client).get(keep["id"]) is not None

    def test_fulfilment_reports_the_audit_rows_it_could_not_remove(self, client: TestClient):
        """The non-success state a legal reviewer has to see."""

        make_view(client, "buyer@northwind.example")
        pin(client, "eu-west")
        request = open_dsar(client, "buyer@northwind.example")

        payload = fulfil(client, request["id"]).json()

        assert payload["residue"]["audit_rows"] > 0
        assert vocab.RESIDUE_AUDIT_TRAIL in payload["residue"]["reasons"]
        assert payload["state"] == vocab.DSAR_PARTIAL

    def test_fulfilment_keeps_the_request_row_and_says_why(self, client: TestClient):
        make_view(client, "buyer@northwind.example")
        pin(client, "eu-west")
        request = open_dsar(client, "buyer@northwind.example")

        payload = fulfil(client, request["id"]).json()

        assert payload["residue"]["erasure_records_kept"] == 1
        assert client.get(f"{PREFIX}/dsar/requests/{request['id']}").status_code == 200

    def test_a_request_that_finds_nothing_says_so(self, client: TestClient):
        pin(client, "eu-west")

        payload = open_dsar(client, "buyer@nowhere.example")

        assert payload["found"] == 0
        assert payload["state"] == vocab.DSAR_NOTHING_FOUND

    def test_opening_a_request_from_a_role_below_the_administrator_tier_is_a_403(
        self, client: TestClient
    ):
        response = client.post(
            f"{PREFIX}/dsar/requests",
            params={"room_id": "room_a", "role": "viewer"},
            json={"subject": "buyer@northwind.example"},
        )

        assert response.status_code == 403

    def test_fulfilling_from_a_role_below_the_administrator_tier_is_a_403(self, client: TestClient):
        pin(client, "eu-west")
        request = open_dsar(client, "buyer@northwind.example")

        response = client.post(
            f"{PREFIX}/dsar/requests/{request['id']}/fulfil", params={"role": "room_collaborator"}
        )

        assert response.status_code == 403

    def test_a_malformed_subject_is_a_400(self, client: TestClient):
        response = client.post(
            f"{PREFIX}/dsar/requests", params=dict(ADMIN_PARAMS), json={"subject": "nope"}
        )

        assert response.status_code == 400
        assert "subject" in response.json()["errors"]

    def test_an_unknown_request_is_a_404_not_a_500(self, client: TestClient):
        response = client.get(f"{PREFIX}/dsar/requests/no-such-request")

        assert response.status_code == 404
        assert response.json()["error"] == "not_found"

    def test_fulfilling_an_unknown_request_is_a_404(self, client: TestClient):
        response = client.post(
            f"{PREFIX}/dsar/requests/no-such-request/fulfil", params=dict(ADMIN_PARAMS)
        )

        assert response.status_code == 404

    def test_the_request_list_names_its_states_and_its_window(self, client: TestClient):
        payload = client.get(f"{PREFIX}/dsar/requests", params={"room_id": "room_a"}).json()

        assert payload["dsar_states"] == list(vocab.DSAR_STATES)
        assert payload["window_days"] == vocab.DSAR_WINDOW_DAYS


# --------------------------------------------------------------------------- #
# the board and the served research
# --------------------------------------------------------------------------- #


class TestTheBoardOverHttp:
    def test_an_empty_room_answers_with_empty_states(self, client: TestClient):
        payload = client.get(f"{PREFIX}/summary", params={"room_id": "room_a"}).json()

        assert payload["residency"] is None
        assert payload["consent_signals"] == 0
        assert payload["retention_due"] == 0
        assert payload["admin_role"] == ADMIN
        assert payload["screen_text"] == vocab.SCREEN_TEXT_DEFAULT

    def test_the_board_reports_whether_this_room_needs_consent(self, client: TestClient):
        pin(client, "eu-west", room_id="room_a")
        pin(client, "us-east", room_id="room_b")

        assert (
            client.get(f"{PREFIX}/summary", params={"room_id": "room_a"}).json()["consent_required"]
            is True
        )
        assert (
            client.get(f"{PREFIX}/summary", params={"room_id": "room_b"}).json()["consent_required"]
            is False
        )

    def test_the_decisions_are_served_with_their_alternatives(self, client: TestClient):
        payload = client.get(f"{PREFIX}/decisions").json()

        assert payload["count"] >= 10
        for decision in payload["decisions"]:
            assert decision["chosen"] in decision["options"]
            assert decision["cost_of_the_choice"]

    def test_the_region_move_derivation_names_its_jev_audit(self, client: TestClient):
        payload = client.get(f"{PREFIX}/decisions/DERIVED_REGION_MOVE_RESTAMPS_EVERY_RECORD").json()

        assert payload["chosen"] == "restamp_in_place"
        assert "jev-20261004T182818-16136-98999" in payload["rejected_because"]

    def test_an_unknown_decision_is_a_404(self, client: TestClient):
        assert client.get(f"{PREFIX}/decisions/NOT_A_DECISION").status_code == 404

    def test_the_vocabulary_carries_the_three_researched_windows(self, client: TestClient):
        payload = client.get(f"{PREFIX}/vocabulary").json()
        days = {row["class"]: row["days"] for row in payload["retention_classes"]}

        assert days[vocab.CLASS_SESSION_RECORDING] == 30
        assert days[vocab.CLASS_HEATMAP] == vocab.DAYS_PER_NINE_MONTHS

    def test_the_vocabulary_names_the_regions_and_their_jurisdictions(self, client: TestClient):
        payload = client.get(f"{PREFIX}/vocabulary").json()
        regions = {row["id"]: row["jurisdiction"] for row in payload["regions"]}

        assert regions["eu-west"] == vocab.JURISDICTION_EEA
        assert regions["us-east"] == vocab.JURISDICTION_US

    def test_the_vocabulary_declares_the_window_unit_and_the_instant_encodings(
        self, client: TestClient
    ):
        payload = client.get(f"{PREFIX}/vocabulary").json()

        assert payload["window_unit"] == "days"
        assert payload["instant_formats"] == list(vocab.INSTANT_FORMATS)

    def test_the_vocabulary_names_the_residue_reasons(self, client: TestClient):
        payload = client.get(f"{PREFIX}/vocabulary").json()

        assert payload["residue_reasons"] == list(vocab.RESIDUE_REASONS)


# --------------------------------------------------------------------------- #
# the audit-source rule and the read-path rule
# --------------------------------------------------------------------------- #


class TestTheAuditRules:
    def test_every_source_this_router_can_record_names_a_mounted_route(self):
        """The test the brief asks for by name.

        A hardcoded source string is the defect this prevents: it leaves the audit log
        naming a route the app stopped serving.
        """

        module = importlib.import_module(FEATURE_MODULE)
        mounted = {
            f"{method} {route.path}"
            for route in module.router.routes
            for method in route.methods
            if method not in ("HEAD", "OPTIONS")
        }

        for method, path in (
            ("POST", "/residency"),
            ("POST", "/retention/policy"),
            ("POST", "/retention/run"),
            ("POST", "/consent"),
            ("POST", "/dsar/requests"),
            ("POST", "/dsar/requests/{request_id}/fulfil"),
        ):
            assert module._source(method, path) in mounted

    def test_the_read_paths_record_no_audit_row(self, client: TestClient):
        """A route that wrote a row on every read would fill the audit log with noise."""

        make_view(client)
        pin(client, "eu-west")
        consent(client, "buyer@northwind.example", signal="granted")
        request = open_dsar(client, "buyer@northwind.example")

        before = client.get("/api/audit").json()["count"]
        for path in (
            f"{PREFIX}/summary",
            f"{PREFIX}/vocabulary",
            f"{PREFIX}/decisions",
            f"{PREFIX}/residency",
            f"{PREFIX}/retention/policy",
            f"{PREFIX}/retention/schedule",
            f"{PREFIX}/consent",
            f"{PREFIX}/dsar/requests",
            f"{PREFIX}/dsar/requests/{request['id']}",
        ):
            assert client.get(path, params={"room_id": "room_a"}).status_code == 200
        after = client.get("/api/audit").json()["count"]

        assert after == before

    def test_a_write_names_this_workflows_own_route(self, client: TestClient):
        pin(client, "eu-west")

        entries = client.get("/api/audit", params={"collection": vocab.RESIDENCY_COLLECTION}).json()

        assert entries["count"] >= 1
        assert {str(row.get("source")) for row in entries["entries"]} == {
            "POST /api/wf-085/residency"
        }

    def test_a_consent_write_names_the_consent_route(self, client: TestClient):
        consent(client, "buyer@northwind.example", signal="granted")

        entries = client.get("/api/audit", params={"collection": vocab.CONSENT_COLLECTION}).json()

        assert {str(row.get("source")) for row in entries["entries"]} == {
            "POST /api/wf-085/consent"
        }

    def test_an_erasure_write_names_the_fulfil_route(self, client: TestClient):
        make_view(client, "buyer@northwind.example")
        pin(client, "eu-west")
        request = open_dsar(client, "buyer@northwind.example")

        fulfil(client, request["id"])

        entries = client.get("/api/audit", params={"collection": vocab.DSAR_COLLECTION}).json()
        sources = {str(row.get("source")) for row in entries["entries"]}

        assert "POST /api/wf-085/dsar/requests/{request_id}/fulfil" in sources

    def test_every_write_this_router_makes_reaches_the_audit_log(self, client: TestClient):
        """The product's own guarantee, exercised over the wire."""

        make_view(client, days_ago=400)
        pin(client, "eu-west")
        before = client.get("/api/audit").json()["count"]

        client.post(
            f"{PREFIX}/retention/policy",
            params={"room_id": "room_a", **ADMIN_PARAMS},
            json={"windows": {vocab.CLASS_SESSION_RECORDING: 14}},
        )
        client.post(f"{PREFIX}/retention/run", params={"room_id": "room_a", **ADMIN_PARAMS})
        request = open_dsar(client, "buyer@northwind.example")
        fulfil(client, request["id"])
        after = client.get("/api/audit").json()["count"]

        assert after > before

    def test_the_removed_record_leaves_no_index_entry(self, client: TestClient):
        """A hard delete, because an index entry is a longer-lived copy of the address."""

        make_view(client, "buyer@northwind.example", days_ago=400)
        pin(client, "eu-west")
        client.post(f"{PREFIX}/retention/run", params={"room_id": "room_a", **ADMIN_PARAMS})

        response = client.get(
            "/api/records/wf075_view",
            params={"where": json.dumps({"viewer_email": "buyer@northwind.example"})},
        )

        assert response.json()["count"] == 0


# --------------------------------------------------------------------------- #
# the error shapes
# --------------------------------------------------------------------------- #


class TestErrorShapes:
    def test_no_route_leaks_a_500_for_a_missing_record(self, client: TestClient):
        for path in (
            f"{PREFIX}/dsar/requests/missing",
            f"{PREFIX}/decisions/missing",
        ):
            assert client.get(path).status_code == 404

    def test_every_advertised_route_answers_on_an_empty_store(self, client: TestClient):
        """A board that 500s on a fresh room is a broken feature.

        ``tools/verify_all_routes.py`` calls every advertised route with an empty body and
        fails on a 5xx, so each of these shapes is reproduced here rather than discovered
        on a runner.
        """

        module = importlib.import_module(FEATURE_MODULE)
        for route in module.router.routes:
            for method in sorted(m for m in route.methods if m not in ("HEAD", "OPTIONS")):
                path = route.path.replace("{inference_id}", "missing").replace(
                    "{request_id}", "missing"
                )
                response = client.request(method, f"{PREFIX}{path[len(PREFIX) :]}", json={})
                assert response.status_code < 500, f"{method} {path} -> {response.status_code}"
