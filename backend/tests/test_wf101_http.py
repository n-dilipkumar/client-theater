"""WF-101 HTTP tests: the mounted router, its errors and its audit trail.

The client is the conftest module-scoped one, repointed at a fresh database per
test, so each test sees only its own rows and the file passes alone. Quotes are
created through the generic ``/api/records`` route, which is how a quote and its
line items already exist when this workflow runs.
"""

from __future__ import annotations

PREFIX = "/api/wf-101"
QUOTE_COLLECTION = "wf086_quote"
LINE_COLLECTION = "wf086_line_item"


def make_quote(client, *, room_id="room_1", status="published", **overrides):
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
        "hs_status": "ACCEPTED",
        "price_list": "book_1",
        "billing_address": "1 Northwind Way",
        "shipping_method": "courier",
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
        "price_source": "catalog",
        "amounts": {},
    }
    payload.update(overrides)
    response = client.post(
        f"/api/records/{LINE_COLLECTION}", json=payload, params={"room_id": room_id}
    )
    assert response.status_code == 201, response.text
    return response.json()


def enable_settings(client, *, room_id="room_1", **overrides):
    body = {
        "enable_creation_of_orders_from_quotes": True,
        "enable_sales_order_processing_integration": False,
        "ispricelocked_default": False,
    }
    body.update(overrides)
    response = client.post(f"{PREFIX}/settings", json=body, params={"room_id": room_id})
    assert response.status_code == 200, response.text
    return response.json()


def given_a_quote(client, *, room_id="room_1", **overrides):
    quote = make_quote(client, room_id=room_id, **overrides)
    make_line(client, quote["id"], 1, room_id=room_id)
    make_line(client, quote["id"], 2, room_id=room_id, price_source="manual")
    return quote


# --------------------------------------------------------------------------- #
# Discovery and registry
# --------------------------------------------------------------------------- #


def test_the_router_is_mounted_by_discovery(client):
    response = client.get(f"{PREFIX}/summary")
    assert response.status_code == 200
    assert "orders" in response.json()


def test_the_feature_appears_in_the_registry(client):
    response = client.get("/api/features")
    assert response.status_code == 200
    ids = [feature.get("id") for feature in response.json().get("features", [])]
    assert "wf-101-quote-to-order" in ids
    assert response.json().get("failed_count", 0) == 0


def test_the_vocabulary_serves_the_modes_and_the_settings(client):
    payload = client.get(f"{PREFIX}/vocabulary").json()
    assert payload["ticket"] == "WF-101"
    assert set(payload["pricing_modes"]) == {"lock_pricing", "use_current_pricing"}
    assert len(payload["clone_components"]) == 7
    assert payload["collections"]["order"] == "wf101_sales_order"
    assert payload["refusal_reasons"]["quote_not_activated"]


def test_the_decisions_are_served(client):
    payload = client.get(f"{PREFIX}/decisions").json()
    assert payload["count"] >= 5
    assert all(entry["rejected"] for entry in payload["entries"])


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #


def test_the_setting_defaults_to_off(client):
    payload = client.get(f"{PREFIX}/settings", params={"room_id": "room_1"}).json()
    assert payload["enable_creation_of_orders_from_quotes"] is False
    assert payload["explicit"] is False


def test_the_setting_can_be_turned_on(client):
    updated = enable_settings(client)
    assert updated["enable_creation_of_orders_from_quotes"] is True
    stored = client.get(f"{PREFIX}/settings", params={"room_id": "room_1"}).json()
    assert stored["explicit"] is True


def test_an_unknown_setting_key_is_ignored(client):
    payload = client.post(
        f"{PREFIX}/settings", json={"future_field": "x"}, params={"room_id": "room_1"}
    ).json()
    assert "future_field" not in payload


# --------------------------------------------------------------------------- #
# The conversion end to end
# --------------------------------------------------------------------------- #


def test_converting_an_accepted_quote_creates_an_order(client):
    enable_settings(client)
    quote = given_a_quote(client)
    response = client.post(f"{PREFIX}/quotes/{quote['id']}/order", json={"date_won": "2026-04-01"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["created"] is True
    assert body["order"]["line_item_count"] == 2
    assert body["order"]["source_quote_id"] == quote["id"]
    assert body["price_lock"]["locked"] is True
    listed = client.get(f"{PREFIX}/orders", params={"room_id": "room_1"}).json()
    assert listed["count"] == 1


def test_conversion_is_idempotent_over_http(client):
    enable_settings(client)
    quote = given_a_quote(client)
    first = client.post(f"{PREFIX}/quotes/{quote['id']}/order", json={}).json()
    second = client.post(f"{PREFIX}/quotes/{quote['id']}/order", json={}).json()
    assert second["created"] is False
    assert second["reason"] == "quote_already_converted"
    assert second["order"]["id"] == first["order"]["id"]


def test_an_unactivated_quote_is_refused_over_http(client):
    enable_settings(client)
    quote = given_a_quote(client, status="draft")
    response = client.post(f"{PREFIX}/quotes/{quote['id']}/order", json={})
    assert response.status_code == 200
    assert response.json()["reason"] == "quote_not_activated"


def test_an_unaccepted_quote_is_refused_over_http(client):
    enable_settings(client)
    quote = given_a_quote(client, hs_status=None)
    body = client.post(f"{PREFIX}/quotes/{quote['id']}/order", json={}).json()
    assert body["reason"] == "quote_not_accepted"


def test_creation_disabled_refuses_over_http(client):
    quote = given_a_quote(client)
    body = client.post(f"{PREFIX}/quotes/{quote['id']}/order", json={}).json()
    assert body["reason"] == "order_creation_disabled"


def test_readiness_previews_the_quote(client):
    enable_settings(client)
    quote = given_a_quote(client)
    body = client.get(f"{PREFIX}/quotes/{quote['id']}/readiness").json()
    assert body["convertible"] is True
    assert body["price_lock"]["locked"] is True
    assert body["line_item_count"] == 2


def test_an_unknown_quote_is_a_404(client):
    response = client.get(f"{PREFIX}/quotes/q_absent/readiness")
    assert response.status_code == 404


def test_an_unknown_order_is_a_404(client):
    response = client.get(f"{PREFIX}/orders/ord_absent")
    assert response.status_code == 404


# --------------------------------------------------------------------------- #
# The price lock and repricing
# --------------------------------------------------------------------------- #


def test_a_locked_order_is_not_repriced_over_http(client):
    enable_settings(client, enable_sales_order_processing_integration=False)
    quote = given_a_quote(client)
    order = client.post(f"{PREFIX}/quotes/{quote['id']}/order", json={}).json()["order"]
    body = client.post(f"{PREFIX}/orders/{order['id']}/reprice", json={}).json()
    assert body["applied_lines"] == 0
    assert body["reason"] == "prices_locked"


def test_use_current_pricing_unlocks_the_order(client):
    enable_settings(client, enable_sales_order_processing_integration=False)
    quote = given_a_quote(client)
    order = client.post(f"{PREFIX}/quotes/{quote['id']}/order", json={}).json()["order"]
    unlocked = client.post(
        f"{PREFIX}/orders/{order['id']}/pricing-mode", json={"mode": "use_current_pricing"}
    ).json()
    assert unlocked["locked"] is False
    assert unlocked["prices_locked"] == "No"


def test_an_unknown_pricing_mode_is_a_422(client):
    enable_settings(client)
    quote = given_a_quote(client)
    order = client.post(f"{PREFIX}/quotes/{quote['id']}/order", json={}).json()["order"]
    response = client.post(f"{PREFIX}/orders/{order['id']}/pricing-mode", json={"mode": "sideways"})
    assert response.status_code == 422


# --------------------------------------------------------------------------- #
# Write-in products and line edits
# --------------------------------------------------------------------------- #


def test_a_write_in_is_refused_while_locked_over_http(client):
    enable_settings(client, enable_sales_order_processing_integration=False)
    quote = given_a_quote(client)
    order = client.post(f"{PREFIX}/quotes/{quote['id']}/order", json={}).json()["order"]
    response = client.post(
        f"{PREFIX}/orders/{order['id']}/lines",
        json={"name": "Expedited", "quantity": 1, "unit_price": 10.0},
    )
    assert response.status_code == 409
    assert response.json()["reason"] == "write_in_product_not_allowed"


def test_a_write_in_is_added_while_unlocked_over_http(client):
    enable_settings(client, enable_sales_order_processing_integration=True)
    quote = given_a_quote(client)
    order = client.post(f"{PREFIX}/quotes/{quote['id']}/order", json={}).json()["order"]
    body = client.post(
        f"{PREFIX}/orders/{order['id']}/lines",
        json={"name": "Expedited", "quantity": 1, "unit_price": 10.0},
    ).json()
    assert body["line_item_count"] == 3


def test_a_line_price_edit_is_refused_while_locked_over_http(client):
    enable_settings(client, enable_sales_order_processing_integration=False)
    quote = given_a_quote(client)
    order = client.post(f"{PREFIX}/quotes/{quote['id']}/order", json={}).json()["order"]
    line_id = order["line_items"][0]["id"]
    response = client.patch(
        f"{PREFIX}/orders/{order['id']}/lines/{line_id}", json={"unit_price": 5.0}
    )
    assert response.status_code == 409
    assert response.json()["reason"] == "prices_locked"


# --------------------------------------------------------------------------- #
# Fulfilment
# --------------------------------------------------------------------------- #


def test_closing_the_order_makes_it_read_only_over_http(client):
    enable_settings(client)
    quote = given_a_quote(client)
    order = client.post(f"{PREFIX}/quotes/{quote['id']}/order", json={}).json()["order"]
    closed = client.post(
        f"{PREFIX}/orders/{order['id']}/close", json={"status": "fulfilled"}
    ).json()
    assert closed["status"] == "fulfilled"
    response = client.patch(f"{PREFIX}/orders/{order['id']}", json={"description": "nope"})
    assert response.status_code == 409
    assert response.json()["reason"] == "order_closed"


def test_a_closed_order_counts_in_the_summary(client):
    enable_settings(client)
    quote = given_a_quote(client)
    order = client.post(f"{PREFIX}/quotes/{quote['id']}/order", json={}).json()["order"]
    client.post(f"{PREFIX}/orders/{order['id']}/close", json={"status": "cancelled"})
    summary = client.get(f"{PREFIX}/summary", params={"room_id": "room_1"}).json()
    assert summary["orders"] == 1
    assert summary["closed"] == 1
    assert summary["cancelled"] == 1


# --------------------------------------------------------------------------- #
# Get Products
# --------------------------------------------------------------------------- #


def test_get_products_copies_an_opportunity_over_http(client):
    enable_settings(client, enable_sales_order_processing_integration=True)
    quote = given_a_quote(client)
    order = client.post(f"{PREFIX}/quotes/{quote['id']}/order", json={}).json()["order"]
    body = client.post(
        f"{PREFIX}/orders/{order['id']}/products",
        json={
            "opportunity_id": "opp_1",
            "line_items": [{"name": "Support", "quantity": 1, "unit_price": 300.0}],
        },
    ).json()
    assert body["added_lines"] == 1
    assert body["order"]["line_item_count"] == 3


def test_a_closed_opportunity_records_its_revenue_over_http(client):
    enable_settings(client, enable_sales_order_processing_integration=True)
    quote = given_a_quote(client)
    order = client.post(f"{PREFIX}/quotes/{quote['id']}/order", json={}).json()["order"]
    body = client.post(
        f"{PREFIX}/orders/{order['id']}/products",
        json={"opportunity_id": "opp_2", "state": "won", "final_revenue": 9000.0},
    ).json()
    assert body["opportunity_closed"] is True
    assert body["final_revenue"] == 9000.0


# --------------------------------------------------------------------------- #
# The audit trail
# --------------------------------------------------------------------------- #


def test_the_audit_row_names_the_route_that_served_the_write(client):
    enable_settings(client)
    quote = given_a_quote(client)
    client.post(f"{PREFIX}/quotes/{quote['id']}/order", json={})
    audit = client.get("/api/audit").json()
    sources = {entry["source"] for entry in audit["entries"]}
    assert f"POST {PREFIX}/quotes/{quote['id']}/order" in sources


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
        "/decisions",
        "/settings",
        "/orders",
        "/orders/ord_absent",
        "/quotes/q_absent/readiness",
    ]
    for path in paths:
        response = client.get(f"{PREFIX}{path}")
        assert response.status_code < 500, f"GET {path} answered {response.status_code}"
    for path in (
        "/settings",
        "/orders/ord_absent/pricing-mode",
        "/orders/ord_absent/reprice",
        "/orders/ord_absent/lines",
        "/orders/ord_absent/close",
        "/orders/ord_absent/products",
        "/quotes/q_absent/order",
    ):
        response = client.post(f"{PREFIX}{path}", json={})
        assert response.status_code < 500, f"POST {path} answered {response.status_code}"
