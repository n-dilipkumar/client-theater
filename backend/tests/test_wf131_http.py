"""HTTP tests for WF-131: the mounted routes, the matrix, the log, the refusals.

These drive the mounted app through ``TestClient`` rather than calling the book,
so they exercise the same path a real client does. If the feature failed to
register, or collided with another feature's route, every test here would 404 -
which is the point: the router is mounted by discovery alone and
``backend/dsr/api.py`` is not edited.

Each test names the rule it protects. Two are worth reading twice: the audit test
asserts that every row this feature wrote carries a ``source`` that names a route
the host actually serves, and the plan test asserts the researched 403 on a flip
and *not* on an edit of an already-watermarked policy.

No test depends on a row another test wrote, so the file passes alone and under
``pytest-xdist`` in any order. No test asserts a value measured against the wall
clock; the evaluation boundary uses a fixed expiry where one matters.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from dsr.link_gating.agreement import AgreementEngine
from dsr.link_gating.gate import GateEngine
from fastapi.testclient import TestClient

PREFIX = "/api/wf-131"
FEATURE_ID = "wf-131-content-protection"

#: This workflow's own collections, whose audit rows are the ones to check.
OWN_COLLECTIONS = {"wf131_recipient_policy", "wf131_protection_action"}

NOW = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)

_PLACEHOLDER = re.compile(r"\{[^}]+\}")


# --------------------------------------------------------------------------- #
# Fixtures and helpers
# --------------------------------------------------------------------------- #


def make_room(client: TestClient, name: str = "Northwind") -> dict:
    response = client.post(
        "/api/records/room", json={"name": name, "account": name}, params={"actor": "dana"}
    )
    assert response.status_code == 201, response.text
    return response.json()


def make_link(client: TestClient, room_id: str, **overrides) -> dict:
    """A real WF-069 link, through the gate's own engine.

    Built through WF-069's own write rather than a hand-written row, so the matrix
    is evaluated against a link the gate itself would produce.
    """
    payload = {
        "title": "Northwind evaluation",
        "document_id": "doc-demo",
        "email_protected": True,
        "password": "northwind-2026",
    }
    payload.update(overrides)
    store = client.app.state.store
    return GateEngine(store, now=lambda: NOW).create_link(
        room_id, payload, actor="dana", source="POST /api/wf-069/links"
    )


def make_agreement(client: TestClient, room_id: str) -> dict:
    store = client.app.state.store
    return AgreementEngine(store, now=lambda: NOW).create_agreement(
        room_id,
        {"title": "Northwind mutual NDA", "body": "Keep it confidential.", "kind": "nda"},
        actor="dana",
        source="POST /api/wf-070/agreements",
    )


def make_policy(client: TestClient, link_id: str, room_id: str, **overrides) -> dict:
    payload = {"recipient": "a.buyer@northwind.example", **overrides}
    response = client.post(
        f"{PREFIX}/links/{link_id}/policies",
        json=payload,
        params={"room_id": room_id, "actor": "dana"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def mounted_routes(client: TestClient) -> set[tuple[str, str]]:
    body = client.get("/api/features").json()
    return {
        (method, route["path"])
        for feature in body["features"]
        for route in feature["routes"]
        for method in route["methods"]
    }


def source_matches_route(source: str, routes: set[tuple[str, str]]) -> bool:
    """Is an audit ``source`` a route the app serves?"""
    verb, _, path = source.partition(" ")
    pattern = _PLACEHOLDER.sub("[^/]+", path)
    return any(method == verb and re.fullmatch(pattern, path_) for method, path_ in routes)


# --------------------------------------------------------------------------- #
# Discovery and the published contract
# --------------------------------------------------------------------------- #


def test_the_router_is_mounted_by_discovery(client: TestClient):
    """The feature registers by adding a file. api.py is not edited."""
    response = client.get(f"{PREFIX}/summary")
    assert response.status_code == 200
    assert "policies" in response.json()


def test_the_feature_appears_in_the_registry(client: TestClient):
    body = client.get("/api/features").json()
    ids = [f.get("id") for f in body.get("features", [])]
    assert FEATURE_ID in ids
    assert body["failed_count"] == 0, body["failed"]


def test_the_prefix_is_this_workflows_own(client: TestClient):
    body = client.get("/api/features").json()
    mine = [f for f in body["features"] if f["id"] == FEATURE_ID]
    assert len(mine) == 1
    assert mine[0]["prefix"] == PREFIX
    assert mine[0]["ticket"] == "WF-131"
    assert set(mine[0]["exception_handlers"]) == {
        "ContentProtectionError",
        "ProtectionDenied",
        "PolicyNotFound",
    }
    prefixes = [f["prefix"] for f in body["features"] if f["prefix"]]
    assert prefixes.count(PREFIX) == 1


def test_the_feature_is_navigable_without_editing_app_jsx(client: TestClient):
    entry = next(f for f in client.get("/api/features").json()["features"] if f["prefix"] == PREFIX)
    assert entry["nav"] == [{"id": "content-protection", "label": "Content protection"}]


def test_the_vocabulary_serves_the_reused_values(client: TestClient):
    body = client.get(f"{PREFIX}/vocabulary").json()
    assert body["reused"]["link_collection"] == "wf069_gated_link"
    assert body["reused"]["agreement_collection"] == "wf070_agreement"
    sources = {entry["field"]: entry["source"] for entry in body["fields"]}
    assert "WF-069" in sources["email_protected"]
    assert "WF-071" in sources["enable_watermark"]
    assert "WF-072" in sources["allow_download"]
    assert body["plan"]["requires"] == "business"


def test_the_honesty_route_declines_the_three_unsourced_items(client: TestClient):
    body = client.get(f"{PREFIX}/honesty").json()
    claims = " ".join(item["claim"] for item in body["declined"])
    assert "end-to-end encryption" in claims
    assert "certifications" in claims
    assert "RBAC" in claims
    assert "no open-source project" in body["limitation"].lower()


def test_the_action_codes_route_names_the_four_verbs(client: TestClient):
    body = client.get(f"{PREFIX}/actions").json()
    assert {entry["action"] for entry in body["actions"]} == {
        "view",
        "download",
        "share",
        "revoke",
    }
    codes = {entry["action"]: entry["code"] for entry in body["actions"]}
    assert codes["view"] == 8 and codes["share"] == 12 and codes["revoke"] == 13
    assert codes["download"] >= 1000


# --------------------------------------------------------------------------- #
# The policy matrix
# --------------------------------------------------------------------------- #


def test_create_then_read_returns_the_saved_policy(client: TestClient):
    room = make_room(client)
    link = make_link(client, room["id"])
    created = make_policy(
        client, link["id"], room["id"], allow_download=True, enable_watermark=True
    )
    assert created["policy"]["allow_download"] is True
    assert created["policy"]["enable_watermark"] is True

    read = client.get(f"{PREFIX}/policies/{created['id']}")
    assert read.status_code == 200
    assert read.json()["policy"]["allow_download"] is True


def test_a_link_carries_one_policy_per_recipient(client: TestClient):
    room = make_room(client)
    link = make_link(client, room["id"])
    make_policy(client, link["id"], room["id"])
    make_policy(client, link["id"], room["id"], recipient="b.buyer@northwind.example")
    listed = client.get(f"{PREFIX}/links/{link['id']}/policies").json()
    assert listed["count"] == 2
    assert {p["recipient"] for p in listed["policies"]} == {
        "a.buyer@northwind.example",
        "b.buyer@northwind.example",
    }


def test_patch_moves_one_switch(client: TestClient):
    room = make_room(client)
    link = make_link(client, room["id"])
    created = make_policy(client, link["id"], room["id"])
    patched = client.patch(f"{PREFIX}/policies/{created['id']}", json={"allow_download": True})
    assert patched.status_code == 200, patched.text
    assert patched.json()["policy"]["allow_download"] is True
    assert patched.json()["policy"]["email_protected"] is False


def test_revoke_then_restore(client: TestClient):
    room = make_room(client)
    link = make_link(client, room["id"])
    created = make_policy(client, link["id"], room["id"])
    revoked = client.post(f"{PREFIX}/policies/{created['id']}/revoke", params={"actor": "dana"})
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["revoked"] is True
    restored = client.post(f"{PREFIX}/policies/{created['id']}/restore")
    assert restored.json()["revoked"] is False


def test_an_unknown_policy_is_not_found(client: TestClient):
    assert client.get(f"{PREFIX}/policies/nope").status_code == 404
    assert client.patch(f"{PREFIX}/policies/nope", json={}).status_code == 404


def test_the_scope_list_returns_every_policy_in_a_room(client: TestClient):
    room = make_room(client)
    link = make_link(client, room["id"])
    make_policy(client, link["id"], room["id"])
    make_policy(client, link["id"], room["id"], recipient="b.buyer@northwind.example")
    listed = client.get(f"{PREFIX}/policies", params={"room_id": room["id"]}).json()
    assert listed["count"] == 2
    assert listed["room_id"] == room["id"]


def test_the_scope_list_can_hide_revoked_policies(client: TestClient):
    room = make_room(client)
    link = make_link(client, room["id"])
    created = make_policy(client, link["id"], room["id"])
    client.post(f"{PREFIX}/policies/{created['id']}/revoke")
    hidden = client.get(
        f"{PREFIX}/policies", params={"room_id": room["id"], "include_revoked": False}
    ).json()
    assert hidden["count"] == 0
    shown = client.get(f"{PREFIX}/policies", params={"room_id": room["id"]}).json()
    assert shown["count"] == 1 and shown["policies"][0]["revoked"] is True


# --------------------------------------------------------------------------- #
# The refusals
# --------------------------------------------------------------------------- #


def test_a_policy_for_a_missing_link_is_refused(client: TestClient):
    response = client.post(
        f"{PREFIX}/links/not-a-link/policies",
        json={"recipient": "a.buyer@northwind.example"},
    )
    assert response.status_code == 404, response.text


def test_a_bad_allowlist_entry_is_refused_422(client: TestClient):
    room = make_room(client)
    link = make_link(client, room["id"])
    response = client.post(
        f"{PREFIX}/links/{link['id']}/policies",
        json={"recipient": "a.buyer@northwind.example", "ip_allowlist": ["not-an-ip"]},
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"] == "invalid_content_protection"
    assert "ip_allowlist" in response.json()["errors"]


def test_an_nda_gate_without_an_agreement_is_refused_422(client: TestClient):
    room = make_room(client)
    link = make_link(client, room["id"])
    response = client.post(
        f"{PREFIX}/links/{link['id']}/policies",
        json={"recipient": "a.buyer@northwind.example", "enable_agreement": True},
    )
    assert response.status_code == 422, response.text
    assert "agreement_id" in response.json()["errors"]


def test_the_plan_gate_fires_on_a_flip_and_not_on_an_edit(client: TestClient):
    """The researched 403 on enable, and no refusal while it stays on."""
    room = make_room(client)
    link = make_link(client, room["id"])
    refused = client.post(
        f"{PREFIX}/links/{link['id']}/policies",
        json={"recipient": "a.buyer@northwind.example", "enable_watermark": True},
        params={"plan": "starter"},
    )
    assert refused.status_code == 403, refused.text
    assert refused.json()["error"] == "forbidden_plan_feature"
    assert refused.json()["requires"] == "business"

    created = make_policy(client, link["id"], room["id"], enable_watermark=True)
    # Editing an already-watermarked policy with a lower plan is not a flip.
    edited = client.patch(
        f"{PREFIX}/policies/{created['id']}",
        json={"allow_download": True},
        params={"plan": "starter"},
    )
    assert edited.status_code == 200, edited.text


# --------------------------------------------------------------------------- #
# The evaluation and the viewer
# --------------------------------------------------------------------------- #


def test_evaluate_allows_when_everything_is_satisfied(client: TestClient):
    room = make_room(client)
    link = make_link(client, room["id"])
    created = make_policy(client, link["id"], room["id"])
    body = client.get(
        f"{PREFIX}/policies/{created['id']}/evaluate",
        params={"email_verified": True},
    ).json()
    assert body["allowed"] is True
    assert body["denied_by"] == []


def test_evaluate_denies_an_off_list_address_and_names_it(client: TestClient):
    room = make_room(client)
    link = make_link(client, room["id"])
    created = make_policy(client, link["id"], room["id"], ip_allowlist=["10.0.0.0/8"])
    body = client.get(
        f"{PREFIX}/policies/{created['id']}/evaluate",
        params={"email_verified": True, "ip": "198.51.100.7"},
    ).json()
    assert body["allowed"] is False
    assert "ip" in body["denied_by"]


def test_admit_returns_the_decision_when_allowed(client: TestClient):
    room = make_room(client)
    link = make_link(client, room["id"])
    created = make_policy(client, link["id"], room["id"])
    response = client.post(
        f"{PREFIX}/policies/{created['id']}/admit",
        json={"email_verified": True},
        params={"actor": "viewer"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["allowed"] is True


def test_admit_is_refused_403_with_the_reason(client: TestClient):
    room = make_room(client)
    link = make_link(client, room["id"])
    created = make_policy(client, link["id"], room["id"], ip_allowlist=["10.0.0.0/8"])
    response = client.post(
        f"{PREFIX}/policies/{created['id']}/admit",
        json={"email_verified": True, "ip": "198.51.100.7"},
    )
    assert response.status_code == 403, response.text
    assert response.json()["error"] == "protection_denied"
    assert response.json()["reason"] == "ip_not_allowed"


# --------------------------------------------------------------------------- #
# The action log
# --------------------------------------------------------------------------- #


def test_an_action_is_logged_with_its_wf079_code(client: TestClient):
    room = make_room(client)
    link = make_link(client, room["id"])
    created = client.post(
        f"{PREFIX}/links/{link['id']}/actions",
        json={"recipient": "a.buyer@northwind.example", "action": "share"},
        params={"room_id": room["id"]},
    )
    assert created.status_code == 201, created.text
    assert created.json()["event_code"] == 12
    listed = client.get(f"{PREFIX}/links/{link['id']}/actions").json()
    assert listed["count"] == 1
    assert listed["actions"][0]["action"] == "share"


def test_an_unknown_action_is_refused_422(client: TestClient):
    room = make_room(client)
    link = make_link(client, room["id"])
    response = client.post(
        f"{PREFIX}/links/{link['id']}/actions",
        json={"recipient": "a.buyer@northwind.example", "action": "delete"},
    )
    assert response.status_code == 422, response.text


def test_the_bulk_alert_reports_a_pattern_and_does_not_block(client: TestClient):
    room = make_room(client)
    link = make_link(client, room["id"])
    for _ in range(5):
        client.post(
            f"{PREFIX}/links/{link['id']}/actions",
            json={"recipient": "a.buyer@northwind.example", "action": "download"},
            params={"room_id": room["id"]},
        )
    alert = client.get(f"{PREFIX}/links/{link['id']}/bulk-alert", params={"threshold": 5}).json()
    assert alert["alert"] is True
    assert "block" in alert["note"].lower()


def test_the_compose_joins_the_bundle_onto_one_link(client: TestClient):
    room = make_room(client)
    link = make_link(client, room["id"])
    make_agreement(client, room["id"])
    make_policy(client, link["id"], room["id"], enable_watermark=True)
    composed = client.get(f"{PREFIX}/links/{link['id']}").json()
    assert composed["link"]["id"] == link["id"]
    assert composed["recipients"] == 1
    assert composed["scope_owner"] and composed["limitation"]


def test_the_summary_counts_the_scope(client: TestClient):
    room = make_room(client)
    link = make_link(client, room["id"])
    make_policy(client, link["id"], room["id"], enable_watermark=True, ip_allowlist=["10.0.0.0/8"])
    body = client.get(f"{PREFIX}/summary", params={"room_id": room["id"]}).json()
    assert body["policies"] == 1
    assert body["watermarked"] == 1
    assert body["allowlisted"] == 1


# --------------------------------------------------------------------------- #
# The audit trail, and the rule that every source names a served route
# --------------------------------------------------------------------------- #


def test_every_write_this_feature_made_names_a_route_the_app_serves(client: TestClient):
    """The recurring defect the brief names: an audit row pointing at a dead path."""
    room = make_room(client)
    link = make_link(client, room["id"])
    created = make_policy(client, link["id"], room["id"])
    client.patch(f"{PREFIX}/policies/{created['id']}", json={"allow_download": True})
    client.post(f"{PREFIX}/policies/{created['id']}/revoke")
    client.post(f"{PREFIX}/policies/{created['id']}/admit", json={"email_verified": True})

    entries = client.get("/api/audit", params={"limit": 1000}).json()["entries"]
    routes = mounted_routes(client)
    mine = [entry for entry in entries if PREFIX in str(entry.get("source") or "")]
    assert mine, "the feature wrote no audited rows, so this proves nothing"
    for entry in mine:
        assert source_matches_route(str(entry["source"]), routes), entry["source"]
    assert OWN_COLLECTIONS & {entry["collection"] for entry in mine}


def test_no_get_route_this_feature_advertises_returns_a_5xx(client: TestClient):
    """A light version of tools/verify_all_routes.py, scoped to this feature."""
    room = make_room(client)
    link = make_link(client, room["id"])
    created = make_policy(client, link["id"], room["id"])
    get_routes = [
        route
        for route in mounted_routes(client)
        if route[0] == "GET" and route[1].startswith(PREFIX)
    ]
    assert get_routes, "the feature advertises no GET route, so this proves nothing"
    for _method, path in get_routes:
        concrete = (
            path.replace("{link_id}", link["id"])
            .replace("{policy_id}", created["id"])
            .replace("{room_id}", room["id"])
        )
        response = client.get(concrete)
        assert response.status_code < 500, f"{concrete} -> {response.status_code}"
