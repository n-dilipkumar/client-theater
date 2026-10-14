"""WF-138 over HTTP: the routes the host actually mounted.

``test_wf138.py`` proves the rules. This file proves the wiring: that discovery
mounted the router, that each error type became the status code a client should
branch on, that a token-to-token override propagates to every room of a brand,
that white-label mode swaps the presented identity, that a host another brand
holds is refused, and that every audit row a write left behind names a route the
app actually serves.

That last one is the property the whole feature host exists to protect, so it is
asserted against the mounted route table rather than against a literal written in
the test. A literal would agree with a typo.

DNS is never consulted. ``DSR_CNAME_FIXTURES`` maps a host to the names it
resolves to, so the whole verification flow is deterministic and offline, and a
re-check can be driven by changing what a host answers.

Each test builds its own brands and rooms. Nothing here depends on another test's
rows, because the suite runs under ``pytest-xdist`` and a test that only passes
in one order fails intermittently.
"""

from __future__ import annotations

import json

import pytest
from dsr.competitive_baseline import branded_domain as bd
from dsr.db.audited import AuditedDatabase
from dsr.features import load_feature
from dsr.store import RecordStore
from fastapi.testclient import TestClient

MODULE = "wf138_branded_custom_domain"
PREFIX = "/api/wf-138"
FEATURE_ID = "wf-138-branded-domain"

CNAME = "cname.dsr.test"
HOST = "deals.acme.example"
OTHER_HOST = "rooms.acme.example"
FREE_HOST = "pilot.acme.example"


@pytest.fixture()
def feature():
    return load_feature(MODULE)


@pytest.fixture()
def dns(monkeypatch: pytest.MonkeyPatch) -> str:
    """A deterministic DNS: the two hosts resolve, the third does not yet."""
    monkeypatch.setenv("DSR_CNAME_TARGET", CNAME)
    monkeypatch.setenv("DSR_CNAME_FIXTURES", json.dumps({HOST: [CNAME], OTHER_HOST: [CNAME]}))
    return CNAME


@pytest.fixture()
def room_id(db: AuditedDatabase) -> str:
    return db.create("room", {"name": "Northwind"}, actor="test", source="test")["id"]


def _brand(client: TestClient, name: str = "Northwind Studio", **extra: object) -> dict:
    payload = {"name": name, "owner_name": name, **extra}
    response = client.post(f"{PREFIX}/accounts", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def _claim(client: TestClient, account_id: str, host: str, **params: object) -> dict:
    response = client.post(
        f"{PREFIX}/accounts/{account_id}/domains", json={"host": host}, params=params
    )
    assert response.status_code == 200, response.text
    return response.json()


def _served_routes(client: TestClient) -> set[tuple[str, str]]:
    shapes = client.get(f"/api/features/{FEATURE_ID}").json()["routes"]
    return {(method, shape["path"]) for shape in shapes for method in shape["methods"]}


# --------------------------------------------------------------------------- #
# Discovery and mounting
# --------------------------------------------------------------------------- #


def test_discovery_mounted_this_feature_by_its_prefix(client: TestClient, feature):
    """The router was mounted by discovery alone. No shared file was edited."""
    listed = client.get("/api/features").json()
    found = [entry for entry in listed["features"] if entry["id"] == FEATURE_ID]
    assert found, "the feature registry did not list this feature"
    assert found[0]["prefix"] == PREFIX
    assert found[0]["loaded"] is True
    assert client.get(f"{PREFIX}/summary").status_code == 200


def test_no_feature_failed_to_load_because_of_this_workflow(client: TestClient):
    """A broken feature is reported and skipped, so a failure here would be silent."""
    failures = [
        entry
        for entry in client.get("/api/features").json()["failed"]
        if entry["module"].endswith(MODULE)
    ]
    assert not failures, failures


def test_the_feature_reports_every_route_it_mounts(client: TestClient):
    """The registry is what ``tools/verify_all_routes.py`` walks.

    Asserted exactly: adding a route without listing it here, or listing one the
    feature does not serve, both fail.
    """
    assert _served_routes(client) == {
        ("GET", f"{PREFIX}/summary"),
        ("GET", f"{PREFIX}/vocabulary"),
        ("GET", f"{PREFIX}/config"),
        ("GET", f"{PREFIX}/accounts"),
        ("POST", f"{PREFIX}/accounts"),
        ("GET", f"{PREFIX}/accounts/{{account_id}}/brand"),
        ("PUT", f"{PREFIX}/accounts/{{account_id}}/brand"),
        ("PUT", f"{PREFIX}/accounts/{{account_id}}/tokens"),
        ("POST", f"{PREFIX}/accounts/{{account_id}}/propagate"),
        ("GET", f"{PREFIX}/accounts/{{account_id}}/theme"),
        ("GET", f"{PREFIX}/accounts/{{account_id}}/events"),
        ("GET", f"{PREFIX}/accounts/{{account_id}}/domains"),
        ("POST", f"{PREFIX}/accounts/{{account_id}}/domains/verify"),
        ("POST", f"{PREFIX}/accounts/{{account_id}}/domains"),
        ("DELETE", f"{PREFIX}/accounts/{{account_id}}/domains"),
        ("POST", f"{PREFIX}/accounts/{{account_id}}/domains/recheck"),
        ("POST", f"{PREFIX}/rooms/{{room_id}}/brand"),
        ("GET", f"{PREFIX}/rooms/{{room_id}}/theme"),
        ("GET", f"{PREFIX}/accounts/{{account_id}}/rooms"),
        ("GET", f"{PREFIX}/resolve"),
    }


def test_route_parameter_names_are_the_ones_the_route_verifier_knows():
    """An unknown placeholder is left in the URL and fails as a request that never
    answered, which ``tools/verify_all_routes.py`` scores as a fault."""
    module = load_feature(MODULE)
    placeholders = {
        part.strip("{}")
        for route in module.router.routes
        for part in route.path.split("/")
        if part.startswith("{")
    }
    assert placeholders <= {"account_id", "room_id"}, placeholders


def test_no_route_answers_5xx_for_a_placeholder_value(client: TestClient):
    """The contract ``tools/verify_all_routes.py`` enforces, run through the app.

    Every route is called with the verifier's own absent values. A 4xx is the API
    correctly refusing; a 5xx means a mounted route has no working handler behind
    it, which the import check cannot see.
    """
    for method, path in sorted(_served_routes(client)):
        url = path.replace("{account_id}", "acct_absent").replace("{room_id}", "room_absent")
        call = getattr(client, method.lower())
        response = call(url, json={}) if method in ("POST", "PUT", "PATCH") else call(url)
        assert response.status_code < 500, (
            f"{method} {path} -> {response.status_code} {response.text}"
        )


# --------------------------------------------------------------------------- #
# The board
# --------------------------------------------------------------------------- #


def test_the_summary_route_counts_a_fresh_install_as_empty(client: TestClient):
    body = client.get(f"{PREFIX}/summary").json()
    assert body["brands"] == 0
    assert body["claims"] == 0
    assert body["rooms_themed"] == 0
    assert body["ambiguous_hosts"] == []


def test_the_summary_counts_brands_and_host_states(client: TestClient, dns: str, room_id: str):
    first = _brand(client, "Northwind Studio")
    claim = _claim(client, first["account_id"], HOST)
    assert claim["claim"]["state"] == bd.CLAIM_VERIFIED
    client.post(f"{PREFIX}/rooms/{room_id}/brand", json={"account_id": first["account_id"]})
    _brand(client, "Contoso Health", owner_name="Contoso Health")

    body = client.get(f"{PREFIX}/summary").json()
    assert body["brands"] == 2
    assert body["verified"] == 1
    assert body["rooms_themed"] == 1
    assert body["deployment_host"]


def test_the_vocabulary_route_publishes_the_tokens_the_states_and_the_decisions(client: TestClient):
    """A reviewer reads each decision without opening the source."""
    body = client.get(f"{PREFIX}/vocabulary").json()
    assert body["tenant_tokens"] == list(bd.TENANT_TOKENS)
    assert body["product_tokens"] == list(bd.PRODUCT_TOKENS)
    assert body["claim_states"] == list(bd.CLAIM_STATES)
    assert body["claim_expectations"][bd.CLAIM_UNVERIFIED]
    assert body["decisions"]["token_overrides"]
    assert any("TLS" in note for note in body["unverifiable_here"])


def test_the_config_route_answers_the_setup_questions_from_the_server(client: TestClient, dns: str):
    """The CNAME target comes from the deployment, never from the UI."""
    body = client.get(f"{PREFIX}/config").json()
    assert body["cname_target"] == CNAME
    assert body["record_type"] == "CNAME"
    assert "24 hours" in body["propagation_note"]
    assert "token" in body["token_note"].lower()
    assert any("reverse proxy" in note for note in body["cannot_verify"])


# --------------------------------------------------------------------------- #
# Brands
# --------------------------------------------------------------------------- #


def test_creating_a_brand_returns_it_and_lists_it(client: TestClient):
    created = _brand(client, "Northwind Studio")
    assert created["account_id"]
    assert created["owner_name"] == "Northwind Studio"
    assert created["presented"]["presented"] == "Northwind Studio"
    assert created["presented"]["white_labelled"] is False

    listed = client.get(f"{PREFIX}/accounts").json()
    assert [row["account_id"] for row in listed["accounts"]] == [created["account_id"]]
    assert listed["count"] == 1


def test_an_unknown_account_is_404(client: TestClient):
    assert client.get(f"{PREFIX}/accounts/acct_absent/brand").status_code == 404
    assert client.get(f"{PREFIX}/accounts/acct_absent/domains").status_code == 404
    assert client.get(f"{PREFIX}/accounts/acct_absent/theme").status_code == 404


def test_a_brand_without_a_name_is_422_with_a_field_keyed_map(client: TestClient):
    response = client.post(f"{PREFIX}/accounts", json={"owner_name": "Acme"})
    assert response.status_code == 422, response.text
    body = response.json()
    assert body["error"] == "branded_domain_invalid"
    assert "name" in body["errors"]


def test_white_label_mode_is_422_without_a_client_to_present(client: TestClient):
    brand = _brand(client, "Fabrikam Advisory")
    refused = client.put(
        f"{PREFIX}/accounts/{brand['account_id']}/brand", json={"white_label": True}
    )
    assert refused.status_code == 422, refused.text
    assert "client_name" in refused.json()["errors"]


def test_white_label_mode_swaps_the_presented_identity(client: TestClient):
    brand = _brand(client, "Fabrikam Advisory")
    updated = client.put(
        f"{PREFIX}/accounts/{brand['account_id']}/brand",
        json={"white_label": True, "client_name": "Fabrikam Logistics"},
    )
    assert updated.status_code == 200, updated.text
    presented = updated.json()["presented"]
    assert presented["presented"] == "Fabrikam Logistics"
    assert presented["role"] == "client"
    assert presented["withheld"] == "Fabrikam Advisory"


def test_updating_one_field_does_not_blank_the_others(client: TestClient):
    brand = _brand(client, "Fabrikam Advisory", owner_name="Fabrikam Advisory")
    updated = client.put(
        f"{PREFIX}/accounts/{brand['account_id']}/brand", json={"client_name": "Fabrikam Logistics"}
    ).json()
    assert updated["name"] == "Fabrikam Advisory"
    assert updated["owner_name"] == "Fabrikam Advisory"
    assert updated["client_name"] == "Fabrikam Logistics"


# --------------------------------------------------------------------------- #
# Token overrides
# --------------------------------------------------------------------------- #


def test_a_token_override_is_stored_as_a_pair_and_renderable(client: TestClient):
    brand = _brand(client)
    response = client.put(
        f"{PREFIX}/accounts/{brand['account_id']}/tokens", json={"accent": "accent-bright"}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["brand"]["tokens"] == {"accent": "accent-bright"}
    assert body["brand"]["css_variables"] == {"--color-accent": "var(--color-accent-bright)"}
    assert body["brand"]["brand_revision"] == 2
    # A token pair carries no colour value at all.
    assert "#" not in response.text


def test_a_colour_value_is_refused_and_writes_nothing(client: TestClient, db: AuditedDatabase):
    brand = _brand(client)
    refused = client.put(
        f"{PREFIX}/accounts/{brand['account_id']}/tokens", json={"accent": "#22c55e"}
    )
    assert refused.status_code == 422, refused.text
    assert "not a colour value" in refused.json()["errors"]["tokens.accent"]

    # The write was refused, so the revision never moved.
    assert (
        client.get(f"{PREFIX}/accounts/{brand['account_id']}/brand").json()["brand_revision"] == 1
    )
    writes = [
        row
        for row in db.audit()
        if str(row.get("collection") or "") == bd.BRAND_COLLECTION
        and str(row.get("action") or "") == "update"
    ]
    assert writes == []


def test_a_locked_token_is_refused_over_http(client: TestClient):
    brand = _brand(client)
    refused = client.put(
        f"{PREFIX}/accounts/{brand['account_id']}/tokens", json={"foreground": "accent"}
    )
    assert refused.status_code == 422, refused.text
    assert "locked by the product" in refused.json()["errors"]["tokens.foreground"]


def test_a_third_party_token_name_is_refused_over_http(client: TestClient):
    brand = _brand(client)
    refused = client.put(
        f"{PREFIX}/accounts/{brand['account_id']}/tokens", json={"accent": "brand-green"}
    )
    assert refused.status_code == 422, refused.text
    assert "brand-green" in refused.json()["errors"]["tokens.accent"]


# --------------------------------------------------------------------------- #
# Propagation
# --------------------------------------------------------------------------- #


def test_a_brand_change_reaches_every_room_of_the_brand(client: TestClient, db: AuditedDatabase):
    brand = _brand(client)
    account_id = brand["account_id"]
    rooms = [
        db.create("room", {"name": name}, actor="test", source="test")["id"]
        for name in ("One", "Two")
    ]
    for room in rooms:
        assert (
            client.post(f"{PREFIX}/rooms/{room}/brand", json={"account_id": account_id}).status_code
            == 200
        )

    written = client.put(f"{PREFIX}/accounts/{account_id}/tokens", json={"accent": "accent-bright"})
    assert written.status_code == 200, written.text
    propagation = written.json()["propagation"]
    assert propagation["rooms"] == 2
    assert propagation["updated"] == 2

    for room in rooms:
        themed = client.get(f"{PREFIX}/rooms/{room}/theme").json()
        assert themed["tokens"] == {"accent": "accent-bright"}
        assert themed["inherited"] is True
        assert themed["css_variables"] == {"--color-accent": "var(--color-accent-bright)"}


def test_a_new_room_inherits_the_tokens_the_brand_has_now(client: TestClient, db: AuditedDatabase):
    """The researched "auto-apply brand tokens to new rooms", over HTTP."""
    brand = _brand(client)
    account_id = brand["account_id"]
    client.put(f"{PREFIX}/accounts/{account_id}/tokens", json={"accent": "accent-bright"})

    room = db.create("room", {"name": "Late arrival"}, actor="test", source="test")["id"]
    attached = client.post(f"{PREFIX}/rooms/{room}/brand", json={"account_id": account_id}).json()
    assert attached["theme"]["tokens"] == {"accent": "accent-bright"}
    assert attached["theme"]["brand_revision"] == 2
    assert attached["theme"]["inherited"] is True


def test_a_room_override_beats_the_brand_and_survives_a_propagation(
    client: TestClient, db: AuditedDatabase
):
    brand = _brand(client)
    account_id = brand["account_id"]
    room = db.create("room", {"name": "Refined"}, actor="test", source="test")["id"]
    client.post(
        f"{PREFIX}/rooms/{room}/brand",
        json={"account_id": account_id, "tokens": {"accent": "accent-soft"}},
    )

    client.put(
        f"{PREFIX}/accounts/{account_id}/tokens",
        json={"accent": "accent-bright", "primary": "info"},
    )
    themed = client.get(f"{PREFIX}/rooms/{room}/theme").json()
    assert themed["tokens"] == {"accent": "accent-soft", "primary": "info"}
    assert themed["override"] == {"accent": "accent-soft"}
    assert themed["inherited"] is False


def test_propagation_is_idempotent_over_http(client: TestClient, db: AuditedDatabase):
    brand = _brand(client)
    account_id = brand["account_id"]
    room = db.create("room", {"name": "One"}, actor="test", source="test")["id"]
    client.post(f"{PREFIX}/rooms/{room}/brand", json={"account_id": account_id})
    client.put(f"{PREFIX}/accounts/{account_id}/tokens", json={"accent": "accent-bright"})

    again = client.post(f"{PREFIX}/accounts/{account_id}/propagate").json()
    assert again["rooms"] == 1
    assert again["updated"] == 0
    assert again["rows"][0]["changed"] is False


def test_a_room_that_has_joined_no_brand_renders_the_product_defaults(
    client: TestClient, db: AuditedDatabase
):
    room = db.create("room", {"name": "Unbranded"}, actor="test", source="test")["id"]
    themed = client.get(f"{PREFIX}/rooms/{room}/theme").json()
    assert themed["branded"] is False
    assert themed["tokens"] == {}
    assert themed["css_variables"] == {}


def test_the_brand_lists_every_room_it_carries(client: TestClient, db: AuditedDatabase):
    brand = _brand(client)
    account_id = brand["account_id"]
    room = db.create("room", {"name": "One"}, actor="test", source="test")["id"]
    client.post(f"{PREFIX}/rooms/{room}/brand", json={"account_id": account_id})

    listed = client.get(f"{PREFIX}/accounts/{account_id}/rooms").json()
    assert [row["room_ref"] for row in listed["rooms"]] == [room]
    assert listed["rooms"][0]["inherited"] is True
    assert client.get(f"{PREFIX}/accounts/acct_absent/rooms").status_code == 404


def test_attaching_a_room_without_a_brand_is_422_and_an_unknown_room_is_404(
    client: TestClient, feature
):
    brand = _brand(client)
    assert client.post(f"{PREFIX}/rooms/room_1/brand", json={}).status_code == 422
    assert (
        client.post(
            f"{PREFIX}/rooms/room_absent/brand", json={"account_id": brand["account_id"]}
        ).status_code
        == 404
    )


def test_the_account_theme_route_reports_the_presented_identity(client: TestClient):
    brand = _brand(client, "Fabrikam Advisory")
    client.put(
        f"{PREFIX}/accounts/{brand['account_id']}/brand",
        json={"white_label": True, "client_name": "Fabrikam Logistics"},
    )
    body = client.get(f"{PREFIX}/accounts/{brand['account_id']}/theme").json()
    assert body["presented"]["presented"] == "Fabrikam Logistics"
    assert body["presented"]["withheld"] == "Fabrikam Advisory"
    assert body["brand_revision"] == 1


# --------------------------------------------------------------------------- #
# Hostname claims
# --------------------------------------------------------------------------- #


def test_a_host_verifies_then_claims_and_then_serves(client: TestClient, dns: str):
    brand = _brand(client)
    report = client.post(
        f"{PREFIX}/accounts/{brand['account_id']}/domains/verify", json={"host": HOST}
    ).json()
    assert report["ready"] is True
    assert report["status"] == bd.CLAIM_VERIFIED
    assert report["observed"] == [CNAME]

    claimed = client.post(
        f"{PREFIX}/accounts/{brand['account_id']}/domains", json={"host": HOST}
    ).json()
    assert claimed["claim"]["state"] == bd.CLAIM_VERIFIED
    assert claimed["claim"]["serving"] is True
    assert claimed["claim"]["expectation"]

    listed = client.get(f"{PREFIX}/accounts/{brand['account_id']}/domains").json()
    assert [row["host"] for row in listed["hosts"]] == [HOST]

    resolved = client.get(f"{PREFIX}/resolve", params={"host": HOST}).json()
    assert resolved["served"] is True
    assert resolved["reason"] == "ok"
    assert resolved["brand"]["account_id"] == brand["account_id"]


def test_a_host_the_dns_does_not_answer_for_is_not_ready_and_needs_force(
    client: TestClient, dns: str
):
    brand = _brand(client)
    report = client.post(
        f"{PREFIX}/accounts/{brand['account_id']}/domains/verify", json={"host": FREE_HOST}
    ).json()
    assert report["ready"] is False
    assert report["checks"][0]["detail"] == f"{FREE_HOST} does not resolve yet"

    refused = client.post(
        f"{PREFIX}/accounts/{brand['account_id']}/domains", json={"host": FREE_HOST}
    )
    assert refused.status_code == 409, refused.text
    assert refused.json()["reason"] == bd.BrandConflict.REASON_NOT_READY

    forced = _claim(client, brand["account_id"], FREE_HOST, force=True)
    assert forced["claim"]["state"] == bd.CLAIM_UNVERIFIED
    assert forced["claim"]["serving"] is False
    assert "24 hours" in forced["claim"]["expectation"]


def test_the_verify_route_accepts_an_unrecognised_host_as_well_as_a_wrong_format(
    client: TestClient, dns: str
):
    brand = _brand(client)
    report = client.post(
        f"{PREFIX}/accounts/{brand['account_id']}/domains/verify", json={"host": HOST}
    ).json()
    assert report["checks"][1]["name"] == "format"
    assert report["checks"][1]["ok"] is True

    malformed = client.post(
        f"{PREFIX}/accounts/{brand['account_id']}/domains/verify", json={"host": "acme"}
    )
    assert malformed.status_code == 422, malformed.text
    assert "subdomain" in malformed.json()["errors"]["host"]


def test_a_host_another_brand_holds_verified_is_refused_and_force_cannot_pass_it(
    client: TestClient, dns: str
):
    first = _brand(client, "Northwind Studio")
    second = _brand(client, "Contoso Health")
    _claim(client, first["account_id"], HOST)

    refused = client.post(
        f"{PREFIX}/accounts/{second['account_id']}/domains",
        json={"host": HOST},
        params={"force": True},
    )
    assert refused.status_code == 409, refused.text
    assert refused.json()["reason"] == bd.BrandConflict.REASON_HOST_TAKEN

    # The first brand still holds it, and only it serves the host.
    listed = client.get(f"{PREFIX}/accounts/{first['account_id']}/domains").json()
    assert listed["hosts"][0]["state"] == bd.CLAIM_VERIFIED


def test_a_released_host_can_be_claimed_by_another_brand(client: TestClient, dns: str):
    first = _brand(client, "Northwind Studio")
    second = _brand(client, "Contoso Health")
    _claim(client, first["account_id"], HOST)

    released = client.request(
        "DELETE", f"{PREFIX}/accounts/{first['account_id']}/domains", params={"host": HOST}
    )
    assert released.status_code == 200, released.text
    assert released.json()["claim"]["state"] == bd.CLAIM_RELEASED
    assert released.json()["claim"]["serving"] is False

    taken = _claim(client, second["account_id"], HOST)
    assert taken["claim"]["brand_id"] == second["account_id"]


def test_releasing_a_host_the_brand_does_not_hold_is_409(client: TestClient, dns: str):
    brand = _brand(client)
    refused = client.request(
        "DELETE", f"{PREFIX}/accounts/{brand['account_id']}/domains", params={"host": HOST}
    )
    assert refused.status_code == 409, refused.text
    assert refused.json()["reason"] == bd.BrandConflict.REASON_NO_HOST


def test_a_re_check_moves_a_forced_host_to_verified_once_dns_answers(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    """The route an operator polls during the researched propagation wait."""
    monkeypatch.setenv("DSR_CNAME_TARGET", CNAME)
    monkeypatch.setenv("DSR_CNAME_FIXTURES", json.dumps({}))
    brand = _brand(client)
    forced = _claim(client, brand["account_id"], HOST, force=True)
    assert forced["claim"]["state"] == bd.CLAIM_UNVERIFIED

    monkeypatch.setenv("DSR_CNAME_FIXTURES", json.dumps({HOST: [CNAME]}))
    checked = client.post(f"{PREFIX}/accounts/{brand['account_id']}/domains/recheck")
    assert checked.status_code == 200, checked.text
    claim = checked.json()["checked"][0]["claim"]
    assert claim["state"] == bd.CLAIM_VERIFIED
    assert claim["verified_at"]


def test_a_re_check_with_no_host_to_check_is_409(client: TestClient, dns: str):
    brand = _brand(client)
    refused = client.post(f"{PREFIX}/accounts/{brand['account_id']}/domains/recheck")
    assert refused.status_code == 409, refused.text
    assert refused.json()["reason"] == bd.BrandConflict.REASON_NO_HOST


def test_claiming_a_host_twice_for_one_brand_keeps_one_live_claim(client: TestClient, dns: str):
    brand = _brand(client)
    first = _claim(client, brand["account_id"], HOST)
    second = _claim(client, brand["account_id"], HOST)
    assert second["claim"]["id"] == first["claim"]["id"]
    listed = client.get(f"{PREFIX}/accounts/{brand['account_id']}/domains").json()
    assert len(listed["hosts"]) == 1


def test_the_events_route_lists_what_happened_oldest_first(client: TestClient, dns: str):
    brand = _brand(client)
    _claim(client, brand["account_id"], HOST)
    client.put(f"{PREFIX}/accounts/{brand['account_id']}/tokens", json={"accent": "info"})

    events = client.get(f"{PREFIX}/accounts/{brand['account_id']}/events").json()["events"]
    kinds = [event["kind"] for event in events]
    assert kinds[0] == bd.EVENT_BRAND_CREATED
    assert bd.EVENT_DOMAIN_CLAIMED in kinds
    assert bd.EVENT_TOKENS_SET in kinds


# --------------------------------------------------------------------------- #
# Resolution by host
# --------------------------------------------------------------------------- #


def test_resolution_refuses_an_unclaimed_host_and_a_malformed_one(client: TestClient, dns: str):
    unclaimed = client.get(f"{PREFIX}/resolve", params={"host": FREE_HOST}).json()
    assert unclaimed["served"] is False
    assert unclaimed["reason"] == "unclaimed"
    assert unclaimed["deployment_host"]

    assert client.get(f"{PREFIX}/resolve").status_code == 422
    assert client.get(f"{PREFIX}/resolve", params={"host": "acme"}).status_code == 422


def test_resolution_refuses_a_host_two_verified_claims_hold(
    client: TestClient, db: AuditedDatabase, dns: str
):
    """The hazard the issue names, driven at the record layer, not through a route."""
    first = _brand(client, "Northwind Studio")
    second = _brand(client, "Contoso Health")
    for brand in (first, second):
        db.create(
            bd.CLAIM_COLLECTION,
            {
                "host": HOST,
                "brand_id": brand["account_id"],
                "state": bd.CLAIM_VERIFIED,
                "cname_target": CNAME,
                "observed": [CNAME],
            },
            actor="test",
            source="test",
        )

    resolved = client.get(f"{PREFIX}/resolve", params={"host": HOST}).json()
    assert resolved["served"] is False
    assert resolved["reason"] == "ambiguous"
    assert resolved["claim"] is None
    assert HOST in client.get(f"{PREFIX}/summary").json()["ambiguous_hosts"]


def test_resolution_names_the_deployment_host_without_branding_it(client: TestClient, dns: str):
    resolved = client.get(f"{PREFIX}/resolve", params={"host": "127.0.0.1"}).json()
    assert resolved["reason"] == "unclaimed"
    assert resolved["is_deployment_host"] is True
    assert resolved["served"] is False


def test_resolution_reports_what_it_cannot_verify(client: TestClient, dns: str):
    resolved = client.get(f"{PREFIX}/resolve", params={"host": HOST}).json()
    joined = " ".join(resolved["cannot_verify"])
    assert "TLS" in joined
    assert "Per-customer DNS" in joined


# --------------------------------------------------------------------------- #
# Every write's audit row names a route the app actually serves
# --------------------------------------------------------------------------- #


def test_every_audit_row_this_feature_wrote_names_a_route_the_app_serves(
    client: TestClient, db: AuditedDatabase, dns: str
):
    """The property the feature host exists to protect.

    Asserted against the mounted route table rather than a literal, because a
    literal in the test would agree with a typo in the module.
    """
    served = _served_routes(client)
    assert served, "the registry reports no route for this feature"

    account = _brand(client)["account_id"]
    room = db.create("room", {"name": "Audit"}, actor="test", source="test")["id"]
    client.put(f"{PREFIX}/accounts/{account}/brand", json={"owner_name": "Northwind Studio"})
    client.put(f"{PREFIX}/accounts/{account}/tokens", json={"accent": "accent-bright"})
    client.post(f"{PREFIX}/rooms/{room}/brand", json={"account_id": account})
    client.post(f"{PREFIX}/accounts/{account}/propagate")
    _claim(client, account, HOST)
    client.post(f"{PREFIX}/accounts/{account}/domains/recheck")
    client.request("DELETE", f"{PREFIX}/accounts/{account}/domains", params={"host": HOST})

    mine = (
        bd.BRAND_COLLECTION,
        bd.CLAIM_COLLECTION,
        bd.ROOM_THEME_COLLECTION,
        bd.EVENT_COLLECTION,
    )
    rows = [row for row in db.audit() if str(row.get("collection") or "") in mine]
    assert rows, "no audit rows were written for this feature's collections"

    checked = 0
    for row in rows:
        source = str(row.get("source") or "")
        if not source.startswith(("POST ", "PATCH ", "PUT ", "DELETE ")):
            continue
        method, _, path = source.partition(" ")
        assert (method, path) in served, f"audit row names a route the app does not serve: {source}"
        checked += 1

    assert checked >= 7, f"only {checked} route-sourced audit rows were checked"


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #


def _demo_rooms(db: AuditedDatabase) -> list[tuple[str, str]]:
    accounts = (
        "Northwind Traders",
        "Contoso Health",
        "Fabrikam Logistics",
        "Adventure Works",
    )
    return [
        (
            db.create("room", {"name": name, "account": name}, actor="seed", source="seed")["id"],
            name,
        )
        for name in accounts
    ]


def test_the_seed_prints_a_string_a_windows_console_can_encode(db: AuditedDatabase):
    """A single character outside cp1252 once broke the whole seeder."""
    from datetime import datetime, timezone

    summary = load_feature(MODULE).seed(
        db, {"room_ids": _demo_rooms(db), "now": datetime(2026, 10, 14, tzinfo=timezone.utc)}
    )
    assert isinstance(summary, str) and summary.strip()
    assert summary.encode("cp1252")
    assert all(ord(char) < 128 for char in summary), summary


def test_the_seed_creates_the_states_the_research_describes(db: AuditedDatabase):
    from datetime import datetime, timezone

    module = load_feature(MODULE)
    module.seed(
        db, {"room_ids": _demo_rooms(db), "now": datetime(2026, 10, 14, tzinfo=timezone.utc)}
    )
    store = RecordStore(db)

    brands = store.list(bd.BRAND_COLLECTION, limit=100)
    claims = store.list(bd.CLAIM_COLLECTION, limit=100)
    themes = store.list(bd.ROOM_THEME_COLLECTION, limit=100)

    assert len(brands) >= 4, "the seed should cover more than the happy path"
    states = sorted((claim.get("data") or {}).get("state") for claim in claims)
    assert states.count(bd.CLAIM_VERIFIED) >= 2, "a live brand and a white-labelled one"
    assert bd.CLAIM_UNVERIFIED in states, "the researched propagation window"
    assert "failed" in states, "a check that failed"
    assert len(themes) == len(brands), "one room per brand in the demo"

    white = [brand for brand in brands if (brand.get("data") or {}).get("white_label")]
    assert len(white) == 1
    assert (white[0].get("data") or {}).get("client_name")

    overridden = [theme for theme in themes if (theme.get("data") or {}).get("override")]
    assert overridden, "a room refine beside an inherited map must be visible"


def test_the_seed_writes_through_the_audited_store_with_the_seed_source(db: AuditedDatabase):
    from datetime import datetime, timezone

    load_feature(MODULE).seed(
        db, {"room_ids": _demo_rooms(db), "now": datetime(2026, 10, 14, tzinfo=timezone.utc)}
    )
    sources = {
        str(row.get("source") or "")
        for row in db.audit()
        if str(row.get("collection") or "").startswith("wf138")
    }
    # A demo write is served by no route at all, so it names no route.
    assert sources == {"seed"}, sources


def test_the_seed_says_so_when_there_is_nothing_to_attach_to(db: AuditedDatabase):
    from datetime import datetime, timezone

    summary = load_feature(MODULE).seed(
        db, {"room_ids": [], "now": datetime(2026, 10, 14, tzinfo=timezone.utc)}
    )
    assert "no rooms" in summary
