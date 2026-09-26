"""Tests for white-label rooms on a custom domain, end to end through the API.

These exercise the researched flow in the researched order -- configure DNS,
enter the domain, verify it, save it, share the link -- and pin the three
properties the feature actually promises:

* every write lands in the audit log, because the whole point of the store is
  that it cannot be bypassed;
* the link secret is non-removable, and the domain cannot be stolen;
* changing or releasing a domain does not break a link already shared.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dsr.api import app

CNAME_TARGET = "cname.dsr.test"
BASE_URL = "http://127.0.0.1:8000"

# A domain whose CNAME points at us, one that points somewhere else, and one
# that has not propagated at all.
FIXTURES = {
    "proposals.acme.com": [CNAME_TARGET],
    "northwind.acme.com": [CNAME_TARGET],
    "wrong.acme.com": ["somewhere.else.test"],
    "notpropagated.acme.com": ["198.51.100.10"],
}


@pytest.fixture()
def client(monkeypatch):
    import dsr.api as api_module

    tmp = tempfile.TemporaryDirectory()
    monkeypatch.setenv("DSR_DB_PATH", str(Path(tmp.name) / "api.db"))
    monkeypatch.setenv("DSR_AUDIT_DIR", str(Path(tmp.name) / "audit"))
    monkeypatch.setenv("DSR_CNAME_TARGET", CNAME_TARGET)
    monkeypatch.setenv("DSR_PUBLIC_BASE_URL", BASE_URL)
    # Inline resolver fixtures let the whole DNS flow run offline and
    # deterministically, which is why verification is behind a resolver seam.
    monkeypatch.setenv("DSR_CNAME_FIXTURES", json.dumps(FIXTURES))
    monkeypatch.setattr(api_module, "FRONTEND_DIST", Path(tmp.name) / "absent-frontend")
    with TestClient(app) as test_client:
        yield test_client
    tmp.cleanup()


@pytest.fixture()
def room(client):
    return client.post("/api/records/room", json={"name": "Proposal Name", "account": "Acme"}).json()


def _propagate(client, host, values):
    """Simulate a CNAME finishing propagation on the live resolver.

    The resolver is built once when the app starts, which is what a real
    deployment does, so the honest way to model "time passed and DNS changed" is
    to change the resolver's view rather than the environment.
    """
    client.app.state.domain_service.resolver.table[host.lower()] = [v.lower() for v in values]


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #


def test_config_surfaces_the_deployment_cname_target(client):
    body = client.get("/api/white-label/config").json()

    assert body["cname_target"] == CNAME_TARGET
    assert body["record_type"] == "CNAME"
    assert body["base_url"] == BASE_URL


def test_config_carries_the_propagation_and_cloudflare_caveats(client):
    """Both are sourced: 24-hour propagation, and Cloudflare proxying off."""
    body = client.get("/api/white-label/config").json()

    assert "24 hours" in body["propagation_note"]
    assert "default-host" in body["propagation_note"]
    assert "DNS only" in body["cloudflare_note"]
    assert "subdomain format" in body["format_note"]


# --------------------------------------------------------------------------- #
# Link secrets
# --------------------------------------------------------------------------- #


def test_link_secret_is_minted_once_and_reused(room, client):
    first = client.post(f"/api/rooms/{room['id']}/white-label/link-secret").json()
    second = client.post(f"/api/rooms/{room['id']}/white-label/link-secret").json()

    assert first["has_link_secret"] is True
    assert first["slug"] == second["slug"]
    assert first["share_url"] == second["share_url"]


def test_share_url_uses_the_default_host_before_a_domain_is_set(room, client):
    """Sourced: default links can be shared during propagation and keep working."""
    body = client.post(f"/api/rooms/{room['id']}/white-label/link-secret").json()
    secret = body["slug"].rsplit("-", 1)[1]

    assert body["share_url"] == f"{BASE_URL}/r/Proposal-Name-{secret}"
    assert body["domain"] is None


def test_share_url_is_stable_when_the_domain_changes(room, client):
    """The whole point of `secret_is_identity`: re-pointing breaks nothing.

    This is the hazard the research attributes to the vendor -- "all shared
    links would need to be reshared otherwise the links will appear broken".
    Because the secret is the identity, only the host moves.
    """
    client.post(f"/api/rooms/{room['id']}/white-label/link-secret")
    before = client.get(f"/api/rooms/{room['id']}/white-label").json()

    client.post(f"/api/rooms/{room['id']}/white-label/domain", json={"domain": "proposals.acme.com"})
    after = client.get(f"/api/rooms/{room['id']}/white-label").json()

    assert after["slug"] == before["slug"]
    assert after["default_host_share_url"] == before["share_url"]
    assert after["share_url"] == f"https://proposals.acme.com{before['path']}"


def test_the_link_secret_cannot_be_cleared_through_the_generic_record_route(room, client):
    """Sourced: the secret is a "non-removable identifier"."""
    client.post(f"/api/rooms/{room['id']}/white-label/link-secret")
    secret = client.get(f"/api/rooms/{room['id']}/white-label").json()["slug"].rsplit("-", 1)[1]

    client.patch(f"/api/records/room/{room['id']}", json={"link_secret": None, "name": "Renamed"})

    stored = client.get(f"/api/records/room/{room['id']}").json()["data"]
    assert stored["link_secret"] == secret
    # The rest of the patch is honoured; only the reserved field is dropped.
    assert stored["name"] == "Renamed"


def test_reserved_fields_cannot_be_overwritten_by_the_generic_route(room, client):
    client.patch(
        f"/api/records/room/{room['id']}",
        json={"collaborator_token": "stolen", "link_secret": "stolen"},
    )

    data = client.get(f"/api/records/room/{room['id']}").json()["data"]
    assert data.get("collaborator_token") is None
    assert data.get("link_secret") is None


# --------------------------------------------------------------------------- #
# Verification
# --------------------------------------------------------------------------- #


def test_verification_passes_for_a_propagated_cname(client):
    body = client.post("/api/white-label/verify", json={"domain": "proposals.acme.com"}).json()

    assert body["ready"] is True
    assert body["status"] == "verified"
    assert body["observed"] == [CNAME_TARGET]
    assert {check["name"] for check in body["checks"]} == {"cname", "format", "available"}


def test_verification_fails_and_reports_what_it_saw_for_a_wrong_cname(client):
    body = client.post("/api/white-label/verify", json={"domain": "wrong.acme.com"}).json()

    assert body["ready"] is False
    cname = next(check for check in body["checks"] if check["name"] == "cname")
    assert cname["ok"] is False
    # The operator has to be able to see what DNS actually returned.
    assert "somewhere.else.test" in cname["detail"]


def test_verification_fails_for_an_unpropagated_cname(client):
    body = client.post("/api/white-label/verify", json={"domain": "notpropagated.acme.com"}).json()

    assert body["ready"] is False


def test_verification_rejects_a_bare_registrable_domain(client):
    response = client.post("/api/white-label/verify", json={"domain": "acme.com"})

    assert response.status_code == 422
    assert "subdomain format" in response.json()["detail"]


def test_verification_requires_a_domain(client):
    assert client.post("/api/white-label/verify", json={}).status_code == 400


def test_verification_normalises_pasted_input(client):
    body = client.post(
        "/api/white-label/verify", json={"domain": "https://Proposals.Acme.com/Proposal-Name"}
    ).json()

    assert body["domain"] == "proposals.acme.com"
    assert body["ready"] is True


# --------------------------------------------------------------------------- #
# Claiming
# --------------------------------------------------------------------------- #


def test_claiming_a_verified_domain_switches_the_share_link_host(room, client):
    claimed = client.post(
        f"/api/rooms/{room['id']}/white-label/domain", json={"domain": "proposals.acme.com"}
    ).json()

    assert claimed["domain"] == "proposals.acme.com"
    assert claimed["domain_status"] == "verified"
    assert claimed["share_url"].startswith("https://proposals.acme.com/")


def test_claiming_preserves_the_slug_and_appends_the_mandatory_secret(room, client):
    claimed = client.post(
        f"/api/rooms/{room['id']}/white-label/domain", json={"domain": "proposals.acme.com"}
    ).json()

    assert claimed["slug"] == f"Proposal-Name-{claimed['slug'].rsplit('-', 1)[1]}"
    assert claimed["share_url"].endswith(claimed["slug"])


def test_claiming_mints_the_link_secret_if_the_room_has_none(room, client):
    assert "link_secret" not in client.get(f"/api/records/room/{room['id']}").json()["data"]

    claimed = client.post(
        f"/api/rooms/{room['id']}/white-label/domain", json={"domain": "proposals.acme.com"}
    ).json()

    assert claimed["has_link_secret"] is True
    assert claimed["has_collaborator_token"] is True


def test_claiming_an_unpropagated_domain_is_refused(room, client):
    response = client.post(
        f"/api/rooms/{room['id']}/white-label/domain", json={"domain": "notpropagated.acme.com"}
    )

    assert response.status_code == 409
    assert "not ready" in response.json()["detail"]


def test_force_saves_a_domain_that_has_not_propagated_yet(room, client):
    """The researched flow has a real waiting period; the operator must be able
    to set the binding first and poll afterwards."""
    response = client.post(
        f"/api/rooms/{room['id']}/white-label/domain",
        json={"domain": "notpropagated.acme.com"},
        params={"force": True},
    )

    assert response.status_code == 200
    assert response.json()["domain_status"] == "unverified"
    # An unverified domain must never appear in a share link.
    assert response.json()["share_url"].startswith(BASE_URL)


def test_a_domain_already_used_by_another_room_is_refused(client, room):
    other = client.post("/api/records/room", json={"name": "Second"}).json()
    client.post(f"/api/rooms/{room['id']}/white-label/domain", json={"domain": "proposals.acme.com"})

    response = client.post(
        f"/api/rooms/{other['id']}/white-label/domain",
        json={"domain": "proposals.acme.com"},
        params={"force": True},
    )

    assert response.status_code == 409
    assert "already in use" in response.json()["detail"]


def test_verification_reports_a_domain_that_is_already_taken(client, room):
    client.post(f"/api/rooms/{room['id']}/white-label/domain", json={"domain": "proposals.acme.com"})

    body = client.post("/api/white-label/verify", json={"domain": "proposals.acme.com"}).json()

    available = next(check for check in body["checks"] if check["name"] == "available")
    assert available["ok"] is False
    assert room["id"] in available["detail"]


def test_a_room_can_reclaim_its_own_domain(client, room):
    client.post(f"/api/rooms/{room['id']}/white-label/domain", json={"domain": "proposals.acme.com"})

    response = client.post(
        f"/api/rooms/{room['id']}/white-label/domain", json={"domain": "proposals.acme.com"}
    )

    assert response.status_code == 200


def test_claiming_a_domain_for_an_unknown_room_is_404(client):
    assert (
        client.post(
            "/api/rooms/room_nope/white-label/domain", json={"domain": "proposals.acme.com"}
        ).status_code
        == 404
    )


def test_claiming_requires_a_domain(client, room):
    assert client.post(f"/api/rooms/{room['id']}/white-label/domain", json={}).status_code == 400


# --------------------------------------------------------------------------- #
# Changing and releasing
# --------------------------------------------------------------------------- #


def test_changing_the_domain_records_history_and_keeps_the_secret(room, client):
    client.post(f"/api/rooms/{room['id']}/white-label/domain", json={"domain": "proposals.acme.com"})
    client.post(f"/api/rooms/{room['id']}/white-label/domain", json={"domain": "northwind.acme.com"})

    data = client.get(f"/api/records/room/{room['id']}").json()["data"]
    assert data["domain"] == "northwind.acme.com"
    assert [entry["domain"] for entry in data["domain_history"]] == ["proposals.acme.com"]


def test_releasing_the_domain_returns_to_the_default_host_without_breaking_links(room, client):
    client.post(f"/api/rooms/{room['id']}/white-label/domain", json={"domain": "proposals.acme.com"})
    before = client.get(f"/api/rooms/{room['id']}/white-label").json()

    after = client.delete(f"/api/rooms/{room['id']}/white-label/domain").json()

    assert after["domain"] is None
    assert after["domain_status"] == "unverified"
    assert after["slug"] == before["slug"]
    assert after["share_url"] == before["default_host_share_url"]


def test_a_released_domain_can_be_claimed_by_another_room(client, room):
    other = client.post("/api/records/room", json={"name": "Second"}).json()
    client.post(f"/api/rooms/{room['id']}/white-label/domain", json={"domain": "proposals.acme.com"})
    client.delete(f"/api/rooms/{room['id']}/white-label/domain")

    response = client.post(
        f"/api/rooms/{other['id']}/white-label/domain", json={"domain": "proposals.acme.com"}
    )

    assert response.status_code == 200


def test_releasing_when_there_is_no_domain_is_422(room, client):
    response = client.delete(f"/api/rooms/{room['id']}/white-label/domain")

    assert response.status_code == 422


def test_recheck_promotes_an_unverified_domain_once_it_propagates(client, room):
    client.post(
        f"/api/rooms/{room['id']}/white-label/domain",
        json={"domain": "notpropagated.acme.com"},
        params={"force": True},
    )
    assert client.get(f"/api/rooms/{room['id']}/white-label").json()["domain_status"] == "unverified"

    _propagate(client, "notpropagated.acme.com", [CNAME_TARGET])
    rechecked = client.post(f"/api/rooms/{room['id']}/white-label/recheck").json()

    # Re-checking alone must be enough to promote the room, so an operator can
    # poll through the researched propagation wait instead of retyping anything.
    assert rechecked["domain_status"] == "verified"
    assert rechecked["share_url"].startswith("https://notpropagated.acme.com/")
    assert rechecked["domain_activated_at"]


def test_recheck_without_a_domain_is_422(room, client):
    assert client.post(f"/api/rooms/{room['id']}/white-label/recheck").status_code == 422


# --------------------------------------------------------------------------- #
# Resolution
# --------------------------------------------------------------------------- #


def test_a_secret_resolves_to_its_room(room, client):
    claimed = client.post(
        f"/api/rooms/{room['id']}/white-label/domain", json={"domain": "proposals.acme.com"}
    ).json()
    secret = claimed["slug"].rsplit("-", 1)[1]

    resolved = client.get(f"/api/white-label/links/{secret}", params={"host": "proposals.acme.com"}).json()

    assert resolved["room_id"] == room["id"]
    assert resolved["name"] == "Proposal Name"
    assert resolved["served_on_custom_domain"] is True


def test_the_same_secret_resolves_on_the_default_host(room, client):
    """Sourced: default-host links "will continue working" after setup."""
    claimed = client.post(
        f"/api/rooms/{room['id']}/white-label/domain", json={"domain": "proposals.acme.com"}
    ).json()
    secret = claimed["slug"].rsplit("-", 1)[1]

    resolved = client.get(f"/api/white-label/links/{secret}", params={"host": "127.0.0.1:8000"}).json()

    assert resolved["room_id"] == room["id"]
    assert resolved["served_on_custom_domain"] is False


def test_a_secret_resolves_on_a_host_we_do_not_serve_with_a_404(room, client):
    """Routing, not identity: the secret is real, the host is not ours."""
    claimed = client.post(
        f"/api/rooms/{room['id']}/white-label/domain", json={"domain": "proposals.acme.com"}
    ).json()
    secret = claimed["slug"].rsplit("-", 1)[1]

    response = client.get(f"/api/white-label/links/{secret}", params={"host": "evil.example.net"})

    assert response.status_code == 404
    assert response.json()["error"] == "host_not_served"


def test_an_unknown_secret_is_404(client):
    assert client.get("/api/white-label/links/zzzzzzzzzz").status_code == 404


def test_a_path_resolves_to_its_secret(room, client):
    claimed = client.post(
        f"/api/rooms/{room['id']}/white-label/domain", json={"domain": "proposals.acme.com"}
    ).json()

    resolved = client.get(
        "/api/white-label/resolve",
        params={"path": claimed["path"], "host": "proposals.acme.com"},
    ).json()

    assert resolved["room_id"] == room["id"]


def test_a_path_without_a_secret_is_404(client):
    assert client.get("/api/white-label/resolve", params={"path": "/r/nothing-here"}).status_code == 404


# --------------------------------------------------------------------------- #
# Branding
# --------------------------------------------------------------------------- #


def test_branding_tokens_round_trip(room, client):
    updated = client.patch(
        f"/api/rooms/{room['id']}/white-label/branding",
        json={
            "primary": "#0f172a",
            "accent": "#22c55e",
            "heading_font": "Fira Code, monospace",
            "body_font": "'Fira Sans', sans-serif",
        },
    ).json()

    branding = updated["branding"]
    assert branding["primary"] == "#0f172a"
    assert branding["heading_font"] == "Fira Code, monospace"


def test_branding_merges_rather_than_replaces(room, client):
    """`branding` is team-owned: a partial write must not delete another field."""
    client.patch(
        f"/api/rooms/{room['id']}/white-label/branding",
        json={"logo_url": "https://cdn.acme.com/logo.svg", "accent": "#22c55e"},
    )
    client.patch(f"/api/rooms/{room['id']}/white-label/branding", json={"accent": "#0ea5e9"})

    branding = client.get(f"/api/rooms/{room['id']}/white-label").json()["branding"]
    assert branding["accent"] == "#0ea5e9"
    assert branding["logo_url"] == "https://cdn.acme.com/logo.svg"


def test_branding_accepts_a_field_it_does_not_know_about(room, client):
    """The schema-flexibility promise: no migration, no coordination."""
    client.patch(
        f"/api/rooms/{room['id']}/white-label/branding",
        json={"email_footer": "Confidential - Acme Ltd", "typekit_id": "abc123"},
    )

    branding = client.get(f"/api/rooms/{room['id']}/white-label").json()["branding"]
    assert branding["email_footer"] == "Confidential - Acme Ltd"
    assert branding["typekit_id"] == "abc123"


@pytest.mark.parametrize(
    "token",
    [
        "url(https://evil.example.net/beacon.png)",
        "red; background-image: url(https://evil.example.net/x)",
        "expression(alert(1))",
    ],
)
def test_a_colour_that_could_inject_css_is_refused(room, client, token):
    response = client.patch(
        f"/api/rooms/{room['id']}/white-label/branding", json={"accent": token}
    )

    assert response.status_code == 422


def test_a_font_stack_that_could_escape_its_property_is_refused(room, client):
    response = client.patch(
        f"/api/rooms/{room['id']}/white-label/branding",
        json={"body_font": "@import url(https://evil.example.net/x.css)"},
    )

    assert response.status_code == 422


def test_a_rejected_brand_token_changes_nothing(room, client):
    client.patch(f"/api/rooms/{room['id']}/white-label/branding", json={"accent": "#22c55e"})

    client.patch(f"/api/rooms/{room['id']}/white-label/branding", json={"accent": "url(https://x.test)"})
    client.patch(f"/api/rooms/{room['id']}/white-label/branding", json={"primary": "#0ea5e9"})

    branding = client.get(f"/api/rooms/{room['id']}/white-label").json()["branding"]
    assert branding["accent"] == "#22c55e"
    assert branding["primary"] == "#0ea5e9"


# --------------------------------------------------------------------------- #
# Schema flexibility
# --------------------------------------------------------------------------- #


def test_domain_state_lives_in_data_with_no_migration(client, room):
    client.post(f"/api/rooms/{room['id']}/white-label/domain", json={"domain": "proposals.acme.com"})

    stored = client.get(f"/api/records/room/{room['id']}").json()
    assert stored["collection"] == "room"
    assert stored["data"]["domain"] == "proposals.acme.com"
    assert stored["data"]["domain_status"] == "verified"


def test_rooms_can_be_grouped_by_domain_through_the_dynamic_index(client, room):
    """`domain` is not a declared column anywhere; `find` still resolves it."""
    client.post(f"/api/rooms/{room['id']}/white-label/domain", json={"domain": "proposals.acme.com"})

    found = client.get(
        "/api/records/room", params={"where": json.dumps({"domain": "proposals.acme.com"})}
    ).json()

    assert [r["id"] for r in found["records"]] == [room["id"]]


def test_domains_appear_in_the_schema_discovery_endpoint(client, room):
    client.post(f"/api/rooms/{room['id']}/white-label/domain", json={"domain": "proposals.acme.com"})
    # A second, different domain so the change history exists to discover too.
    client.post(f"/api/rooms/{room['id']}/white-label/domain", json={"domain": "northwind.acme.com"})

    body = client.get("/api/collections").json()
    room_fields = {f["path"] for f in next(c for c in body["collections"] if c["collection"] == "room")["fields"]}

    assert {
        "domain",
        "domain_status",
        "link_secret",
        "collaborator_token",
        # A list of retired domains is indexed by position, which is what makes
        # a history queryable without a column.
        "domain_history.0.domain",
    } <= room_fields


# --------------------------------------------------------------------------- #
# Audit
# --------------------------------------------------------------------------- #


def test_every_white_label_mutation_is_audited(client, room):
    client.post(f"/api/rooms/{room['id']}/white-label/link-secret")
    client.post(f"/api/rooms/{room['id']}/white-label/domain", json={"domain": "proposals.acme.com"})
    client.patch(f"/api/rooms/{room['id']}/white-label/branding", json={"accent": "#22c55e"})
    client.post(f"/api/rooms/{room['id']}/white-label/recheck")
    client.delete(f"/api/rooms/{room['id']}/white-label/domain")

    sources = [e["source"] for e in client.get("/api/audit", params={"limit": 50}).json()["entries"]]
    assert any("mint link secret" in s for s in sources)
    assert any("claim custom domain" in s for s in sources)
    assert any("update branding" in s for s in sources)
    assert any("recheck custom domain" in s for s in sources)
    assert any("release custom domain" in s for s in sources)


def test_the_claim_is_audited_with_before_and_after_state(client, room):
    client.post(f"/api/rooms/{room['id']}/white-label/domain", json={"domain": "proposals.acme.com"})

    entry = client.get(
        "/api/audit", params={"record_id": room["id"], "action": "update", "limit": 50}
    ).json()["entries"][0]

    assert entry["before_state"].get("domain") is None
    assert entry["after_state"]["domain"] == "proposals.acme.com"
    assert entry["diff"]["domain"] == {"from": None, "to": "proposals.acme.com"}


def test_reads_and_verification_do_not_appear_in_the_audit_log(client, room):
    client.post("/api/white-label/verify", json={"domain": "proposals.acme.com"})
    client.get(f"/api/rooms/{room['id']}/white-label")
    client.get("/api/white-label/config")

    sources = [e["source"] for e in client.get("/api/audit", params={"limit": 50}).json()["entries"]]
    # Only the room creation from the fixture. A verification is a read: it
    # looks at DNS and reports, and must not manufacture an audit row.
    assert sources == ["POST /api/records/room"]


def test_a_failed_claim_writes_nothing(client, room):
    client.post(
        f"/api/rooms/{room['id']}/white-label/domain", json={"domain": "notpropagated.acme.com"}
    )

    data = client.get(f"/api/records/room/{room['id']}").json()["data"]
    assert data.get("domain") is None
    # Only the room creation itself is in the log.
    assert client.get("/api/audit").json()["count"] == 1
