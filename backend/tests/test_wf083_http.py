"""WF-083 over HTTP: the surface this feature's own router serves.

The domain rules are in ``test_wf083.py``. This file is the other half, and it is
organised by what a caller can observe:

``the route table``
    That the host mounted every route by discovery alone, with no shared file edited, and
    that no two of them collide.
``consent``
    The four axis combinations, the signal that grants nothing, and the denial that
    destroys the session.
``ingest``
    The recording, the refusal with no grant, the refusal from a partial grant, and the
    blocked visitor that produces no row.
``masking and IP exclusion``
    The administrator-only writes, and the IPv6 refusal.
``recordings, labels and links``
    The five-label cap, the two link kinds, and the per-recording delete refusal.
``retention``
    Both windows and the sweep.
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
import pytest
from dsr.permissions import INSTANCE_ADMIN, VIEWER
from dsr.security_governance import session_consent as vocab
from fastapi.testclient import TestClient

FEATURE_MODULE = "dsr.features.wf083_record_buyer_sessions_behind_a_consent_gate"
PREFIX = "/api/wf-083"
FEATURE_ID = "wf-083-record-buyer-sessions-behind-a-consent-gate"

NOW = datetime(2026, 10, 4, 9, 0, tzinfo=timezone.utc)
DAY_MS = 86_400_000
ADMIN = INSTANCE_ADMIN
ADMIN_PARAMS = {"role": ADMIN, "actor": "dana"}


def feature_module():
    return importlib.import_module(FEATURE_MODULE)


def make_project(client: TestClient, name: str = "Q4 enterprise rooms") -> dict[str, Any]:
    """A recording project, written through the audited store rather than this router.

    The rule is deliberate: a test that creates its own fixture rows through the same code
    it then asserts on proves the assertions are consistent with itself, not with the
    routes. These rows are data, and a route may read data the fixture wrote.
    """

    module = feature_module()
    engine = module.SessionConsentEngine(
        module.RecordStore(client.app.state.db), administrator=ADMIN, now=lambda: NOW
    )
    return engine.create_project(
        name=name, room_id="room_a", actor="dana", source="wf-083 http test fixture"
    )


def record_a_session(
    client: TestClient, *, visitor: str = "v1", ip: str = "203.0.113.9", region: str = "EEA"
) -> dict[str, Any]:
    """One recorded visit, so the labels and link routes have a recording to act on."""

    response = client.post(
        f"{PREFIX}/ingest",
        json={
            "visitor_id": visitor,
            "page_view_id": f"pv_{visitor}",
            "ip_address": ip,
            "region": region,
            "consent_call": {
                "api": vocab.CONSENT_CALL,
                "ad_Storage": vocab.GRANTED,
                "analytics_Storage": vocab.GRANTED,
            },
            "frame": {"page_path": "/pricing", "title": "FY27 pricing"},
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["recording"]


# --------------------------------------------------------------------------- #
# The route table
# --------------------------------------------------------------------------- #


def test_the_feature_is_registered_by_discovery(client: TestClient):
    """The host finds the module, reads FEATURE and mounts its router. No shared file."""

    found = [entry for entry in host.REGISTRY.features if entry.id == FEATURE_ID]
    assert found, "the feature was not discovered"
    assert found[0].prefix == PREFIX


def test_the_feature_id_and_prefix_agree_with_the_ticket():
    module = feature_module()
    assert module.FEATURE["ticket"] == "WF-083"
    assert module.router.prefix == PREFIX


def test_no_two_routes_on_this_router_collide():
    seen: set[tuple[str, str]] = set()
    for route in feature_module().router.routes:
        for method in route.methods:
            key = (method, route.path)
            assert key not in seen, f"duplicate route {key}"
            seen.add(key)


def test_the_routes_the_page_calls_are_all_served():
    served = {
        (method, route.path) for route in feature_module().router.routes for method in route.methods
    }
    for expected in [
        ("GET", f"{PREFIX}/summary"),
        ("GET", f"{PREFIX}/vocabulary"),
        ("GET", f"{PREFIX}/decisions"),
        ("GET", f"{PREFIX}/project"),
        ("POST", f"{PREFIX}/consent"),
        ("POST", f"{PREFIX}/ingest"),
        ("GET", f"{PREFIX}/recordings"),
        ("POST", f"{PREFIX}/share-links"),
        ("GET", f"{PREFIX}/retention"),
    ]:
        assert expected in served, f"{expected} is not served"


# --------------------------------------------------------------------------- #
# Reads
# --------------------------------------------------------------------------- #


def test_the_vocabulary_route_serves_every_researched_term(client: TestClient):
    response = client.get(f"{PREFIX}/vocabulary")
    assert response.status_code == 200
    body = response.json()
    assert body["axes"] == list(vocab.CONSENT_AXES)
    assert body["default_masking_mode"] == vocab.SUPPRESS_ALL
    assert body["ordinary_retention_days"] == 30
    assert body["favourite_retention_days"] == 270
    assert body["max_labels_per_recording"] == 5
    assert body["authentication"] == vocab.AUTHENTICATION
    assert body["ipv4_only"] is True


def test_the_vocabulary_route_names_the_consent_call_the_evidence_quotes(client: TestClient):
    body = client.get(f"{PREFIX}/vocabulary").json()
    assert body["consent_call"] == "consentv2"
    assert body["axis_keys"]["ad_storage"] == ["ad_Storage", "ad_storage"]


def test_the_decisions_route_serves_the_derivation_register(client: TestClient):
    body = client.get(f"{PREFIX}/decisions").json()
    assert body["count"] >= 5
    assert any(d["id"] == "DERIVED_DEFAULT_MASKING_IS_TOTAL_SUPPRESSION" for d in body["decisions"])


def test_one_decision_is_readable_by_id_and_an_unknown_id_is_404(client: TestClient):
    found = client.get(f"{PREFIX}/decisions/DERIVED_NO_SSO_PATH")
    assert found.status_code == 200
    assert found.json()["chosen"]
    assert client.get(f"{PREFIX}/decisions/NOPE").status_code == 404


def test_the_project_route_answers_200_on_a_fresh_room(client: TestClient):
    """A board that 500s or 404s on a room with no project is a broken feature."""

    assert client.get(f"{PREFIX}/project").json() == {}
    assert client.get(f"{PREFIX}/summary").status_code == 200


def test_the_reads_record_no_audit_rows(client: TestClient):
    """Resolving what the room holds changes nothing, so it audits nothing."""

    make_project(client)
    before = len(client.app.state.db.audit(limit=500))
    client.get(f"{PREFIX}/summary")
    client.get(f"{PREFIX}/project")
    client.get(f"{PREFIX}/recordings")
    client.get(f"{PREFIX}/vocabulary")
    assert len(client.app.state.db.audit(limit=500)) == before


# --------------------------------------------------------------------------- #
# Consent
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("ad", "analytics", "outcome"),
    [
        (vocab.GRANTED, vocab.GRANTED, vocab.GRANTED),
        (vocab.GRANTED, vocab.DENIED, vocab.DENIED),
        (vocab.DENIED, vocab.GRANTED, vocab.DENIED),
        (vocab.DENIED, vocab.DENIED, vocab.DENIED),
    ],
)
def test_all_four_consent_combinations_are_recorded(client: TestClient, ad, analytics, outcome):
    """The issue asks for all four, because the axes are independent."""

    make_project(client)
    response = client.post(
        f"{PREFIX}/consent",
        json={"ad_Storage": ad, "analytics_Storage": analytics, "visitor_id": "v1"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["outcome"] == outcome
    assert body["consent_call"]["ad_Storage"] == ad
    assert body["consent_call"]["analytics_Storage"] == analytics


def test_a_signal_stores_no_decision(client: TestClient):
    make_project(client)
    body = client.post(f"{PREFIX}/consent", json={"type": vocab.CONSENT_SIGNAL}).json()
    assert body["signal_only"] is True
    assert body["granted_axes"] == []


def test_a_denial_returns_the_revoke_call_and_the_identity_it_forces(client: TestClient):
    make_project(client)
    body = client.post(
        f"{PREFIX}/consent",
        json={"ad_Storage": vocab.DENIED, "analytics_Storage": vocab.GRANTED, "visitor_id": "v1"},
    ).json()
    assert body["outcome"] == vocab.DENIED
    assert body["revoke_call"]["api"] == "consent"
    assert body["identity_kind"] == vocab.PER_PAGE_VIEW
    assert body["cookies_persist"] is False


def test_a_denial_over_http_tears_the_stored_session_down(client: TestClient):
    make_project(client)
    recording = record_a_session(client)

    body = client.post(
        f"{PREFIX}/consent",
        json={"ad_Storage": vocab.DENIED, "analytics_Storage": vocab.DENIED, "visitor_id": "v1"},
    ).json()
    assert recording["id"] in body["destroyed_sessions"]
    assert client.get(f"{PREFIX}/recordings").json()["recordings"] == []


def test_an_unknown_consent_api_is_a_400(client: TestClient):
    make_project(client)
    response = client.post(
        f"{PREFIX}/consent", json={"api": "consentv99", "ad_Storage": vocab.GRANTED}
    )
    assert response.status_code == 400
    assert "api" in response.json()["errors"]


# --------------------------------------------------------------------------- #
# Ingest
# --------------------------------------------------------------------------- #


def test_a_grant_on_both_axes_records_a_session(client: TestClient):
    make_project(client)
    response = client.post(
        f"{PREFIX}/ingest",
        json={
            "visitor_id": "v1",
            "page_view_id": "pv1",
            "ip_address": "203.0.113.9",
            "region": "EEA",
            "consent_call": {"ad_Storage": vocab.GRANTED, "analytics_Storage": vocab.GRANTED},
            "frame": {"page_path": "/pricing"},
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["recorded"] is True
    assert body["state"] == vocab.RECORDED


def test_a_visit_with_no_grant_records_nothing(client: TestClient):
    make_project(client)
    body = client.post(
        f"{PREFIX}/ingest",
        json={
            "visitor_id": "v1",
            "page_view_id": "pv1",
            "ip_address": "203.0.113.9",
            "region": "EEA",
            "consent_call": {"ad_Storage": vocab.DENIED, "analytics_Storage": vocab.DENIED},
            "frame": {"page_path": "/pricing"},
        },
    ).json()
    assert body["recorded"] is False
    assert body["state"] == vocab.SCRUBBED
    assert client.get(f"{PREFIX}/recordings").json()["recordings"] == []


def test_a_partial_grant_records_no_replay(client: TestClient):
    """One axis granted is not enough to record a session."""

    make_project(client)
    body = client.post(
        f"{PREFIX}/ingest",
        json={
            "visitor_id": "v1",
            "page_view_id": "pv1",
            "ip_address": "203.0.113.9",
            "region": "EEA",
            "consent_call": {"ad_Storage": vocab.GRANTED, "analytics_Storage": vocab.DENIED},
            "frame": {"page_path": "/pricing"},
        },
    ).json()
    assert body["recorded"] is False
    assert body["state"] == vocab.SCRUBBED


def test_a_masked_frame_never_reaches_the_stored_record(client: TestClient):
    """The evidence: "Is masked data uploaded to Clarity? No." """

    make_project(client)
    recording = record_a_session(client)
    body = client.get(f"{PREFIX}/recordings/{recording['id']}").json()
    assert body["frame"]["title"] == vocab.MASK
    assert body["frame"]["masking_mode"] == vocab.SUPPRESS_ALL
    assert "FY27 pricing" not in response_text(client, recording["id"])


def response_text(client: TestClient, recording_id: str) -> str:
    return client.get(f"{PREFIX}/recordings/{recording_id}").text


def test_a_blocked_visitor_produces_no_recording_row(client: TestClient):
    make_project(client)
    assert (
        client.post(
            f"{PREFIX}/ip-blocks", json={"cidr": "10.0.0.0/8"}, params=ADMIN_PARAMS
        ).status_code
        == 201
    )
    body = client.post(
        f"{PREFIX}/ingest",
        json={
            "visitor_id": "internal",
            "page_view_id": "pv9",
            "ip_address": "10.4.2.9",
            "region": "EEA",
            "consent_call": {"ad_Storage": vocab.GRANTED, "analytics_Storage": vocab.GRANTED},
            "frame": {"page_path": "/pricing"},
        },
    ).json()
    assert body["recorded"] is False
    assert body["state"] == vocab.BLOCKED
    assert body["blocked"]["console_signal"] == vocab.BLOCKED_SIGNAL
    assert client.get(f"{PREFIX}/recordings").json()["recordings"] == []


# --------------------------------------------------------------------------- #
# IP exclusion and the administrator check
# --------------------------------------------------------------------------- #


def test_blocking_an_ip_requires_the_administrator_role(client: TestClient):
    make_project(client)
    response = client.post(
        f"{PREFIX}/ip-blocks",
        json={"cidr": "10.0.0.0/8"},
        params={"role": VIEWER, "actor": "dana"},
    )
    assert response.status_code == 403
    body = response.json()
    assert body["error"] == "administrator_required"
    assert body["presented_role"] == VIEWER
    assert body["required_role"] == INSTANCE_ADMIN
    assert body["remediation"]


def test_the_admin_tier_is_the_products_own_and_not_the_vendors(client: TestClient):
    """The derivation ``DERIVED_ADMINISTRATOR_ROLE_IS_READ_NOT_INVENTED``.

    The evidence names the vendor's tier, "administrator". This product already has a role
    surface, so the workflow reads its administrator tier rather than inventing a second
    vocabulary, and the vocabulary route reports both spellings side by side.
    """

    body = client.get(f"{PREFIX}/vocabulary").json()
    assert body["ip_blocking_role"] == vocab.IP_BLOCKING_ROLE == "admin"
    assert body["admin_role"] == INSTANCE_ADMIN
    # The vendor's word is reported next to the product's tier and is deliberately absent
    # from the product's own role list. Putting it there would be the second-vocabulary
    # failure this derivation exists to avoid.
    assert body["ip_blocking_role"] not in {entry["id"] for entry in body["roles"]}
    assert body["admin_role"] in {entry["id"] for entry in body["roles"]}


def test_an_ipv6_range_is_refused_with_its_reason(client: TestClient):
    """The evidence: "Clarity only supports IPv4 addresses." """

    make_project(client)
    response = client.post(
        f"{PREFIX}/ip-blocks", json={"cidr": "2001:db8::/32"}, params=ADMIN_PARAMS
    )
    assert response.status_code == 400
    assert "IPv6" in response.json()["errors"]["cidr"]


def test_a_blocked_range_is_listed_and_can_be_removed(client: TestClient):
    make_project(client)
    created = client.post(
        f"{PREFIX}/ip-blocks", json={"cidr": "10.0.0.0/8"}, params=ADMIN_PARAMS
    ).json()
    listed = client.get(f"{PREFIX}/ip-blocks").json()["blocks"]
    assert [entry["cidr"] for entry in listed] == ["10.0.0.0/8"]
    assert (
        client.delete(f"{PREFIX}/ip-blocks/{created['id']}", params=ADMIN_PARAMS).status_code == 200
    )
    assert client.get(f"{PREFIX}/ip-blocks").json()["blocks"] == []


def test_removing_a_range_that_does_not_exist_is_404(client: TestClient):
    make_project(client)
    assert client.delete(f"{PREFIX}/ip-blocks/nope", params=ADMIN_PARAMS).status_code == 404


# --------------------------------------------------------------------------- #
# Project and masking
# --------------------------------------------------------------------------- #


def test_a_project_is_created_with_the_documented_defaults(client: TestClient):
    response = client.post(f"{PREFIX}/project", json={"name": "Q4 rooms"})
    assert response.status_code == 201
    body = response.json()
    assert body["masking_mode"] == vocab.SUPPRESS_ALL
    assert body[vocab.CONSENT_GATE] is True
    assert body["enforcement"]["regions"] == list(vocab.CONSENT_ENFORCED_REGIONS)


def test_a_project_without_a_name_is_400(client: TestClient):
    response = client.post(f"{PREFIX}/project", json={})
    assert response.status_code == 400
    assert "name" in response.json()["errors"]


def test_changing_the_masking_mode_is_administrator_only(client: TestClient):
    make_project(client)
    response = client.put(
        f"{PREFIX}/project/masking",
        json={"masking_mode": "element_selector", "masking_selectors": ["title"]},
        params={"role": VIEWER, "actor": "dana"},
    )
    assert response.status_code == 403


def test_a_masking_mode_change_is_reflected_on_the_project(client: TestClient):
    make_project(client)
    response = client.put(
        f"{PREFIX}/project/masking",
        json={"masking_mode": "element_selector", "masking_selectors": ["title"]},
        params=ADMIN_PARAMS,
    )
    assert response.status_code == 200
    assert response.json()["masking_mode"] == "element_selector"


# --------------------------------------------------------------------------- #
# Recordings, labels and links
# --------------------------------------------------------------------------- #


def test_five_labels_are_accepted_and_the_sixth_is_refused(client: TestClient):
    make_project(client)
    recording = record_a_session(client)
    ok = client.post(
        f"{PREFIX}/recordings/{recording['id']}/labels", json={"labels": ["a", "b", "c", "d", "e"]}
    )
    assert ok.status_code == 201
    assert ok.json()["labels"] == ["a", "b", "c", "d", "e"]

    refused = client.post(f"{PREFIX}/recordings/{recording['id']}/labels", json={"labels": ["f"]})
    assert refused.status_code == 400
    assert "label 6 of 5" in refused.json()["errors"]["labels"]


def test_marking_a_favourite_reports_the_longer_window(client: TestClient):
    make_project(client)
    recording = record_a_session(client)
    body = client.post(
        f"{PREFIX}/recordings/{recording['id']}/favourite", json={"favourite": True}
    ).json()
    assert body["retention_days"] == vocab.FAVOURITE_RETENTION_DAYS


def test_a_guest_link_expires_and_a_team_link_does_not(client: TestClient):
    make_project(client)
    recording = record_a_session(client)
    guest = client.post(
        f"{PREFIX}/share-links", json={"recording_id": recording["id"], "kind": "guest"}
    ).json()
    team = client.post(
        f"{PREFIX}/share-links", json={"recording_id": recording["id"], "kind": "team"}
    ).json()
    assert guest["expires_at"] is not None
    assert team["expires_at"] is None


def test_a_window_on_a_team_link_is_refused(client: TestClient):
    make_project(client)
    recording = record_a_session(client)
    response = client.post(
        f"{PREFIX}/share-links",
        json={"recording_id": recording["id"], "kind": "team", "expires_in_days": 3},
    )
    assert response.status_code == 400
    assert "expires_in_days" in response.json()["errors"]


def test_sharing_a_recording_that_does_not_exist_is_404(client: TestClient):
    make_project(client)
    response = client.post(f"{PREFIX}/share-links", json={"recording_id": "nope", "kind": "guest"})
    assert response.status_code == 404


def test_a_single_recording_delete_is_refused_with_the_evidence(client: TestClient):
    """The refusal is the feature, not a gap in it."""

    make_project(client)
    recording = record_a_session(client)
    response = client.delete(f"{PREFIX}/recordings/{recording['id']}")
    assert response.status_code == 409
    body = response.json()
    assert body["error"] == "project_granularity_required"
    assert "entire project" in body["evidence"]
    assert client.get(f"{PREFIX}/recordings").json()["recordings"], "the recording must still exist"


def test_the_segment_filter_narrows_the_list(client: TestClient):
    make_project(client)
    for visitor in ("v_pricing", "v_deck"):
        client.post(
            f"{PREFIX}/ingest",
            json={
                "visitor_id": visitor,
                "page_view_id": f"pv_{visitor}",
                "ip_address": "203.0.113.9",
                "region": "EEA",
                "consent_call": {"ad_Storage": vocab.GRANTED, "analytics_Storage": vocab.GRANTED},
                "frame": {"page_path": f"/{visitor.split('_')[1]}"},
            },
        )
    filtered = client.get(
        f"{PREFIX}/recordings", params={"dimension": "page_path", "value": "/deck"}
    )
    assert len(filtered.json()["recordings"]) == 1


def test_an_unknown_segment_dimension_is_400(client: TestClient):
    make_project(client)
    response = client.get(f"{PREFIX}/recordings", params={"dimension": "browser", "value": "edge"})
    assert response.status_code == 400
    assert "dimension" in response.json()["errors"]


# --------------------------------------------------------------------------- #
# Retention
# --------------------------------------------------------------------------- #


def test_the_retention_route_shows_both_windows(client: TestClient):
    make_project(client)
    body = client.get(f"{PREFIX}/retention").json()
    assert body["ordinary_days"] == 30
    assert body["favourite_days"] == 270


def test_the_sweep_requires_the_administrator_role(client: TestClient):
    make_project(client)
    response = client.post(f"{PREFIX}/retention/run", params={"role": VIEWER, "actor": "dana"})
    assert response.status_code == 403


def test_the_sweep_reports_what_it_removed(client: TestClient):
    make_project(client)
    body = client.post(f"{PREFIX}/retention/run", params=ADMIN_PARAMS).json()
    assert body["removed"] == 0


def test_the_project_purge_requires_the_administrator_role(client: TestClient):
    make_project(client)
    assert client.post(f"{PREFIX}/project/purge", params={"role": VIEWER}).status_code == 403


def test_the_project_purge_removes_every_recording(client: TestClient):
    make_project(client)
    record_a_session(client)
    body = client.post(f"{PREFIX}/project/purge", params=ADMIN_PARAMS).json()
    assert body["removed"][vocab.RECORDING_COLLECTION] == 1
    assert client.get(f"{PREFIX}/recordings").json()["recordings"] == []


# --------------------------------------------------------------------------- #
# The summary board
# --------------------------------------------------------------------------- #


def test_the_summary_reports_the_counts_and_the_vendor_limit(client: TestClient):
    make_project(client)
    record_a_session(client)
    body = client.get(f"{PREFIX}/summary").json()
    assert body["counts"]["recorded"] == 1
    assert body["ceiling"]["label"] == "within the ceiling"
    assert body["authentication"] == vocab.AUTHENTICATION
    assert body["deletion"]["supported"] is False


def test_the_summary_reports_the_enforcement_state(client: TestClient):
    make_project(client)
    enforcement = client.get(f"{PREFIX}/summary").json()["enforcement"]
    assert enforcement["enforcement_start"] == vocab.CONSENT_ENFORCEMENT_START
    assert enforcement["region_enforced"] == list(vocab.CONSENT_ENFORCED_REGIONS)


# --------------------------------------------------------------------------- #
# The audit-source rule
# --------------------------------------------------------------------------- #


def test_every_source_this_router_records_names_a_route_the_host_mounted(client: TestClient):
    """A domain function that hardcodes a URL leaves the audit log naming a dead route."""

    make_project(client)
    record_a_session(client)
    client.post(f"{PREFIX}/ip-blocks", json={"cidr": "10.0.0.0/8"}, params=ADMIN_PARAMS)
    recording = client.get(f"{PREFIX}/recordings").json()["recordings"][0]
    client.post(f"{PREFIX}/recordings/{recording['id']}/labels", json={"labels": ["q4"]})
    client.post(f"{PREFIX}/share-links", json={"recording_id": recording["id"], "kind": "guest"})
    client.post(f"{PREFIX}/retention/run", params=ADMIN_PARAMS)

    registered = next(entry for entry in host.REGISTRY.features if entry.id == FEATURE_ID)
    served = {(method, route["path"]) for route in registered.routes for method in route["methods"]}

    seen = 0
    for row in client.app.state.db.audit(limit=500):
        source = row.get("source") or ""
        if not source.startswith(("POST ", "PUT ", "DELETE ")):
            continue
        method, path = source.split(" ", 1)
        assert (method, path) in served, (
            f"audit source {source!r} names a route the host did not mount"
        )
        seen += 1
    assert seen, "no write audited a source, so the check proved nothing"


def test_the_engine_is_built_per_request_and_carries_no_state(client: TestClient):
    """The engine holds a store and a clock, so nothing has to hang off app.state."""

    module = feature_module()
    signature = inspect.signature(module.get_engine)
    assert list(signature.parameters) == ["store"]


# --------------------------------------------------------------------------- #
# The error shapes
# --------------------------------------------------------------------------- #


def test_a_refusal_is_400_with_a_field_keyed_map(client: TestClient):
    make_project(client)
    response = client.post(f"{PREFIX}/project", json={"name": ""})
    assert response.status_code == 400
    body = response.json()
    assert body["error"] == "invalid_session_consent_request"
    assert "detail" in body
    assert "name" in body["errors"]


def test_a_missing_recording_is_404(client: TestClient):
    make_project(client)
    assert client.get(f"{PREFIX}/recordings/nope").status_code == 404


def test_every_error_type_this_workflow_raises_is_mapped():
    module = feature_module()
    assert set(module.EXCEPTION_HANDLERS) == {
        module.rules.SessionConsentRefusal,
        module.rules.SessionConsentNotFound,
        module.rules.SessionConsentAdministratorRequired,
    }


def test_no_response_claims_the_room_is_compliant(client: TestClient):
    """Nothing in this workflow may render a certification or a compliant flag."""

    make_project(client)
    record_a_session(client)
    for path in ("/summary", "/vocabulary", "/project", "/recordings", "/retention", "/decisions"):
        text = client.get(f"{PREFIX}{path}").text.lower()
        assert "compliant" not in text
        assert "certified" not in text
        assert "iso 27001" not in text
        assert "soc 2" not in text


def test_no_response_offers_an_sso_path(client: TestClient):
    """The evidence rules one out in its own words."""

    body = client.get(f"{PREFIX}/vocabulary").json()
    assert body["authentication"] == "email_invite"
    assert "sso" not in json.dumps(body).lower()


def test_the_feature_module_does_not_import_the_app():
    """The architectural guard. Dependencies come from dsr.deps."""

    text = inspect.getsource(feature_module())
    assert "from dsr.api" not in text
    assert "import dsr.api" not in text


# --------------------------------------------------------------------------- #
# The demo rows
# --------------------------------------------------------------------------- #


def test_the_seed_creates_the_states_the_flow_describes(tmp_path):
    """A feature whose page is empty in the demo is a feature nobody can review."""

    from dsr.db.audited import AuditedDatabase

    module = feature_module()
    database = AuditedDatabase(tmp_path / "seed.db", mirror_dir=tmp_path / "audit")
    try:
        message = module.seed(
            database,
            {"now": NOW, "room_ids": [("room_a", "Northwind")], "rng": None},
        )
    finally:
        database.close()
    assert message
    assert "1 recording project" in message
    assert "no recording" in message


def test_the_seed_return_string_is_encodable_by_cp1252(tmp_path):
    """One RIGHTWARDS ARROW in a recovered feature broke the whole seeder."""

    from dsr.db.audited import AuditedDatabase

    module = feature_module()
    database = AuditedDatabase(tmp_path / "seed.db", mirror_dir=tmp_path / "audit")
    try:
        message = module.seed(database, {"now": NOW, "room_ids": [("room_a", "Northwind")]})
    finally:
        database.close()
    assert message.encode("cp1252").decode("cp1252") == message


def test_the_seed_writes_every_collection_the_page_reads(tmp_path):
    from dsr.db.audited import AuditedDatabase

    module = feature_module()
    database = AuditedDatabase(tmp_path / "seed.db", mirror_dir=tmp_path / "audit")
    try:
        module.seed(database, {"now": NOW, "room_ids": [("room_a", "Northwind")]})
        store = module.RecordStore(database)
        for collection in (
            vocab.PROJECT_COLLECTION,
            vocab.RECORDING_COLLECTION,
            vocab.VISIT_COLLECTION,
            vocab.LABEL_COLLECTION,
            vocab.SHARE_COLLECTION,
            vocab.IP_COLLECTION,
        ):
            assert store.list(collection, limit=50), f"{collection} is empty after the seed"
    finally:
        database.close()


def test_the_seed_is_silent_without_a_room(tmp_path):
    from dsr.db.audited import AuditedDatabase

    module = feature_module()
    database = AuditedDatabase(tmp_path / "seed.db", mirror_dir=tmp_path / "audit")
    try:
        assert module.seed(database, {"now": NOW, "room_ids": []}) == ""
    finally:
        database.close()
