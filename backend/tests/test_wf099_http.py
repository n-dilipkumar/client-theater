"""WF-099 HTTP tests: the mounted router, its errors and its audit trail.

The client is the conftest module-scoped one, repointed at a fresh database per
test, so each test sees only its own rows and the file passes alone. Records are
created through the generic ``/api/records`` route, which is how a quote and its
line items already exist when this workflow runs.
"""

from __future__ import annotations

PREFIX = "/api/wf-099"
QUOTE_COLLECTION = "wf086_quote"
LINE_COLLECTION = "wf086_line_item"


def make_quote(client, *, room_id="room_1", status="published", **overrides):
    """A quote in a room, created through the generic records route."""

    payload = {
        "title": "Northwind annual licence",
        "status": status,
        "template_type": "CPQ_QUOTE",
        "currency": "EUR",
        "currency_label": "EUR",
        "account": "Northwind Traders",
        "company_name": "Northwind Traders",
        "owner": "dana",
        "deal_id": "deal_1",
        "term_length": 12,
        "totals": {"total": 14500.0, "total_contract_value": 14500.0},
    }
    payload.update(overrides)
    response = client.post(
        f"/api/records/{QUOTE_COLLECTION}", json=payload, params={"room_id": room_id}
    )
    assert response.status_code == 201, response.text
    return response.json()


def make_line(client, quote_id, position, *, room_id="room_1", **overrides):
    payload = {
        "quote_id": quote_id,
        "position": position,
        "name": f"Line {position}",
        "sku": f"SKU-{position:02d}",
        "quantity": 1,
        "unit_price": 100.0 * position,
        "amounts": {"total": 100.0 * position},
    }
    payload.update(overrides)
    response = client.post(
        f"/api/records/{LINE_COLLECTION}", json=payload, params={"room_id": room_id}
    )
    assert response.status_code == 201, response.text
    return response.json()


def enable_settings(client, *, room_id="room_1", **overrides):
    body = {
        "create_contracts_from_accepted_quotes": True,
        "connected_cpq": True,
        "direct_contracts_enabled": False,
    }
    body.update(overrides)
    response = client.post(f"{PREFIX}/settings", json=body, params={"room_id": room_id})
    assert response.status_code == 200, response.text
    return response.json()


def given_a_quote(client, **overrides):
    quote = make_quote(client, **overrides)
    make_line(client, quote["id"], 1)
    make_line(client, quote["id"], 2)
    return quote


# --------------------------------------------------------------------------- #
# Discovery and registry
# --------------------------------------------------------------------------- #


def test_the_router_is_mounted_by_discovery(client):
    response = client.get(f"{PREFIX}/summary")
    assert response.status_code == 200
    assert "contracts" in response.json()


def test_the_feature_appears_in_the_registry(client):
    response = client.get("/api/features")
    assert response.status_code == 200
    ids = [f.get("id") for f in response.json().get("features", [])]
    assert "wf-099-auto-create-a-contract-from-an-accepted-quote" in ids
    assert response.json().get("failed_count", 0) == 0


def test_the_vocabulary_serves_the_modes_and_the_legacy_rule(client):
    payload = client.get(f"{PREFIX}/vocabulary").json()
    assert payload["ticket"] == "WF-099"
    assert set(payload["effective_start_date_modes"]) == {
        "ON_AGREEMENT",
        "CUSTOM",
        "DELAYED_BY_DAYS",
        "DELAYED_BY_MONTHS",
    }
    assert payload["flows"] == ["contract_and_order", "contract_only", "none"]
    assert "legacy" in payload["legacy_rule"].lower()
    assert payload["collections"]["contract"] == "contract"


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #


def test_the_setting_defaults_to_off(client):
    payload = client.get(f"{PREFIX}/settings", params={"room_id": "room_1"}).json()
    assert payload["create_contracts_from_accepted_quotes"] is False
    assert payload["explicit"] is False


def test_the_setting_can_be_turned_on(client):
    updated = enable_settings(client)
    assert updated["create_contracts_from_accepted_quotes"] is True
    stored = client.get(f"{PREFIX}/settings", params={"room_id": "room_1"}).json()
    assert stored["create_contracts_from_accepted_quotes"] is True
    assert stored["explicit"] is True


def test_an_unknown_setting_key_is_ignored(client):
    payload = client.post(
        f"{PREFIX}/settings", json={"future_field": "x"}, params={"room_id": "room_1"}
    ).json()
    assert "future_field" not in payload


def test_an_unknown_renewal_alert_mode_is_refused_with_422(client):
    response = client.post(
        f"{PREFIX}/settings",
        json={"renewal_alert_mode": "whenever"},
        params={"room_id": "room_1"},
    )
    assert response.status_code == 422
    assert "renewal_alert_mode" in response.json()["errors"]


# --------------------------------------------------------------------------- #
# The scenario matrix end to end
# --------------------------------------------------------------------------- #


def test_accepting_with_the_beta_creates_a_contract_and_an_order(client):
    enable_settings(client)
    quote = given_a_quote(client)
    response = client.post(
        f"{PREFIX}/quotes/{quote['id']}/acceptance",
        json={"method": "esignature", "accepted_by": "buyer@northwind.example"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["flow"] == "contract_and_order"
    assert body["contract"]["id"]
    assert body["order"]["id"]
    listed = client.get(f"{PREFIX}/contracts", params={"room_id": "room_1"}).json()
    assert listed["count"] == 1
    assert listed["entries"][0]["derived"]["has_order"] is True


def test_accepting_without_the_beta_creates_a_contract_only(client):
    enable_settings(client, connected_cpq=False)
    quote = given_a_quote(client)
    body = client.post(f"{PREFIX}/quotes/{quote['id']}/acceptance", json={}).json()
    assert body["flow"] == "contract_only"
    assert body["contract"] is not None
    assert body["order"] is None


def test_accepting_with_the_setting_off_creates_no_contract(client):
    quote = given_a_quote(client, hs_billing_enabled=True)
    body = client.post(f"{PREFIX}/quotes/{quote['id']}/acceptance", json={}).json()
    assert body["contract"] is None
    assert body["flow"] == "none"
    assert client.get(f"{PREFIX}/contracts", params={"room_id": "room_1"}).json()["count"] == 0


def test_the_contract_inherits_the_line_items_over_http(client):
    enable_settings(client)
    quote = given_a_quote(client)
    body = client.post(f"{PREFIX}/quotes/{quote['id']}/acceptance", json={}).json()
    lines = body["contract"]["data"]["line_items"]
    assert [line["sku"] for line in lines] == ["SKU-01", "SKU-02"]
    assert lines[0]["amount"] == 100.0


def test_the_effective_start_property_flows_from_the_acceptance_body(client):
    enable_settings(client)
    quote = given_a_quote(client)
    body = client.post(
        f"{PREFIX}/quotes/{quote['id']}/acceptance",
        json={
            "effective_start_date_type": "DELAYED_BY_DAYS",
            "delayed_by_days": 30,
        },
    ).json()
    assert body["contract"]["data"]["effective_start_date_mode"] == "DELAYED_BY_DAYS"
    assert body["contract"]["data"]["start_date"]


def test_generation_over_http_is_idempotent(client):
    enable_settings(client)
    quote = given_a_quote(client)
    first = client.post(f"{PREFIX}/quotes/{quote['id']}/acceptance", json={}).json()
    second = client.post(f"{PREFIX}/quotes/{quote['id']}/contract", json={}).json()
    assert second["created"] is False
    assert second["contract"]["id"] == first["contract"]["id"]
    assert client.get(f"{PREFIX}/contracts", params={"room_id": "room_1"}).json()["count"] == 1


# --------------------------------------------------------------------------- #
# The arbitrary-write guard
# --------------------------------------------------------------------------- #


def test_a_raw_status_write_cannot_mint_a_contract_over_http(client):
    enable_settings(client)
    quote = given_a_quote(client)
    patched = client.patch(
        f"/api/records/{QUOTE_COLLECTION}/{quote['id']}",
        json={"hs_status": "ACCEPTED"},
    )
    assert patched.status_code == 200
    response = client.post(f"{PREFIX}/quotes/{quote['id']}/contract", json={})
    assert response.status_code == 409
    assert response.json()["reason"] == "acceptance_not_recorded"


# --------------------------------------------------------------------------- #
# Legacy
# --------------------------------------------------------------------------- #


def test_a_legacy_quote_creates_no_contract_over_http(client):
    enable_settings(client)
    quote = given_a_quote(client, legacy_quote=True)
    body = client.post(f"{PREFIX}/quotes/{quote['id']}/acceptance", json={}).json()
    assert body["contract"] is None
    assert body["reason"] == "legacy_quote_excluded"
    assert client.get(f"{PREFIX}/contracts", params={"room_id": "room_1"}).json()["count"] == 0


def test_generating_for_a_legacy_quote_is_a_conflict_over_http(client):
    enable_settings(client)
    quote = given_a_quote(client, quote_type="legacy")
    client.post(f"{PREFIX}/quotes/{quote['id']}/acceptance", json={})
    response = client.post(f"{PREFIX}/quotes/{quote['id']}/contract", json={})
    assert response.status_code == 409
    assert response.json()["reason"] == "legacy_quote_excluded"


# --------------------------------------------------------------------------- #
# The acceptance path guard
# --------------------------------------------------------------------------- #


def test_acceptance_needs_a_published_quote_over_http(client):
    enable_settings(client)
    quote = given_a_quote(client, status="draft")
    response = client.post(f"{PREFIX}/quotes/{quote['id']}/acceptance", json={})
    assert response.status_code == 409
    assert response.json()["reason"] == "quote_not_published"


def test_acceptance_refuses_an_unknown_method_over_http(client):
    enable_settings(client)
    quote = given_a_quote(client)
    response = client.post(
        f"{PREFIX}/quotes/{quote['id']}/acceptance", json={"method": "telepathy"}
    )
    assert response.status_code == 422


# --------------------------------------------------------------------------- #
# Standalone contracts
# --------------------------------------------------------------------------- #


def test_a_standalone_contract_needs_the_beta_over_http(client):
    response = client.post(f"{PREFIX}/contracts", json={"name": "X"}, params={"room_id": "room_1"})
    assert response.status_code == 409
    assert response.json()["reason"] == "direct_contracts_disabled"


def test_a_standalone_contract_is_created_when_the_beta_is_on(client):
    enable_settings(client, direct_contracts_enabled=True)
    body = client.post(
        f"{PREFIX}/contracts",
        json={"name": "Wingtip standalone", "term_length": 12, "start_date": "2026-05-01"},
        params={"room_id": "room_1"},
    ).json()
    assert body["data"]["source_quote_id"] is None
    assert body["data"]["contract_flow"] == "standalone"


def test_a_standalone_contract_without_a_name_is_refused(client):
    enable_settings(client, direct_contracts_enabled=True)
    response = client.post(f"{PREFIX}/contracts", json={}, params={"room_id": "room_1"})
    assert response.status_code == 422


# --------------------------------------------------------------------------- #
# Reads and errors
# --------------------------------------------------------------------------- #


def test_readiness_previews_the_quote(client):
    enable_settings(client)
    quote = given_a_quote(client)
    body = client.get(f"{PREFIX}/quotes/{quote['id']}").json()
    assert body["published"] is True
    assert body["accepted"] is False
    assert body["flow"] == "contract_and_order"
    assert body["line_item_count"] == 2


def test_an_unknown_quote_is_a_404(client):
    response = client.get(f"{PREFIX}/quotes/q_absent")
    assert response.status_code == 404


def test_an_unknown_contract_is_a_404(client):
    response = client.get(f"{PREFIX}/contracts/ct_absent")
    assert response.status_code == 404


def test_a_missing_custom_effective_start_date_is_a_422_with_the_field(client):
    enable_settings(client)
    quote = given_a_quote(client, hs_contract_effective_start_date_type="CUSTOM")
    response = client.post(f"{PREFIX}/quotes/{quote['id']}/acceptance", json={})
    assert response.status_code == 422
    assert response.json()["errors"]


# --------------------------------------------------------------------------- #
# The audit trail
# --------------------------------------------------------------------------- #


def test_the_audit_row_names_the_route_that_served_the_write(client):
    enable_settings(client)
    quote = given_a_quote(client)
    client.post(f"{PREFIX}/quotes/{quote['id']}/acceptance", json={})
    audit = client.get("/api/audit", params={"record_id": quote["id"]}).json()
    sources = [entry["source"] for entry in audit["entries"]]
    assert f"POST {PREFIX}/quotes/{quote['id']}/acceptance" in sources


def test_the_settings_write_is_audited_against_its_route(client):
    enable_settings(client)
    audit = client.get("/api/audit").json()
    sources = {entry["source"] for entry in audit["entries"]}
    assert f"POST {PREFIX}/settings" in sources


# --------------------------------------------------------------------------- #
# Route health
# --------------------------------------------------------------------------- #


def test_every_advertised_route_answers_without_a_5xx(client):
    paths = [
        "/summary",
        "/vocabulary",
        "/settings",
        "/contracts",
        "/contracts/ct_absent",
        "/quotes/q_absent",
    ]
    for path in paths:
        response = client.get(f"{PREFIX}{path}")
        assert response.status_code < 500, f"GET {path} answered {response.status_code}"
    for path in (
        "/settings",
        "/contracts",
        "/quotes/q_absent/acceptance",
        "/quotes/q_absent/contract",
    ):
        response = client.post(f"{PREFIX}{path}", json={})
        assert response.status_code < 500, f"POST {path} answered {response.status_code}"
