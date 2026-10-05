"""WF-096 over HTTP: the surface this feature's own router serves.

The domain rules are in ``test_wf096.py``. This file is the other half, organised by what a
caller can observe:

``the route table``
    That the host mounted every route by discovery alone, with no shared file edited, that no
    two collide, and that the prefix is the ticket-derived ``/api/wf-096``.
``the board and the research``
    Summary, vocabulary and decisions, because the page reads its words from the server rather
    than hard-coding them.
``the flow``
    Create a quote, add a line, publish the payment configuration, accept without a signature,
    and take payment, in the order the research puts them.
``the refusals``
    Every status code and body this router can produce, and the field-keyed map that lands next
    to each input.
``a declined charge is an outcome and not an error``
    "the total amount due must be more than $0.50" is a processor outcome, so a below-minimum
    charge is a 200 with ``outcome: declined`` and a charge row.
``the audit-source rule``
    Every ``source=`` this workflow records names a route the host actually mounted.
``the not-found rule``
    A route asked about a quote that does not exist answers 404, never a 500, because a feature
    must never turn a missing row into a server fault.
``the read-path rule``
    The reads write no audit rows, because a route that logged every read would fill this
    product's own guarantee with entries describing no change.

Every test here passes with the file run on its own.
"""

from __future__ import annotations

from typing import Any

import dsr.features
from fastapi.testclient import TestClient

FEATURE_MODULE = "dsr.features.wf096_accept_a_quote_without_a_signature_and_take"
PREFIX = "/api/wf-096"
FEATURE_ID = "wf-096-accept-a-quote-without-a-signature-and-take"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _store(client: TestClient):
    from dsr.api import app

    return app.state.store


def _feature():
    import importlib

    return importlib.import_module(FEATURE_MODULE)


def create_quote(client: TestClient, room_id: str = "room_a", **overrides: Any) -> dict[str, Any]:
    body = {"title": "Northwind renewal", "currency": "USD"}
    body.update(overrides)
    response = client.post(f"{PREFIX}/rooms/{room_id}/quotes", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def add_line(client: TestClient, quote_id: str, **line: Any) -> dict[str, Any]:
    body = {"name": "Line", "quantity": 1, "unit_price": 100}
    body.update(line)
    response = client.post(f"{PREFIX}/quotes/{quote_id}/line-items", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def publish(client: TestClient, quote_id: str, **config: Any) -> dict[str, Any]:
    response = client.post(f"{PREFIX}/quotes/{quote_id}/publish", json=config)
    assert response.status_code == 200, response.text
    return response.json()


def accept(client: TestClient, quote_id: str, **body: Any) -> dict[str, Any]:
    response = client.post(f"{PREFIX}/quotes/{quote_id}/accept", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def charge(client: TestClient, quote_id: str, **body: Any):
    return client.post(f"{PREFIX}/quotes/{quote_id}/payment", json=body)


def provisioned(client: TestClient, **line: Any) -> str:
    """A quote with one line, published for payments, ready to accept."""

    quote = create_quote(client)
    add_line(client, quote["id"], **line)
    publish(client, quote["id"])
    return quote["id"]


# --------------------------------------------------------------------------- #
# The route table
# --------------------------------------------------------------------------- #


def test_the_host_mounted_the_feature_by_discovery(client: TestClient) -> None:
    registry = dsr.features.REGISTRY
    record = next((f for f in registry.features if f.id == FEATURE_ID), None)
    assert record is not None, [f.id for f in registry.features if "096" in f.id]
    assert record.loaded is True
    assert record.prefix == PREFIX
    assert record.routes


def test_the_prefix_is_ticket_derived(client: TestClient) -> None:
    feature = _feature()
    assert feature.FEATURE["id"] == FEATURE_ID
    assert feature.FEATURE["prefix"] == PREFIX
    assert feature.router.prefix == PREFIX
    assert PREFIX.startswith("/api/wf-096")


def test_every_router_source_names_a_mounted_route(client: TestClient) -> None:
    """A ``source=`` built from the router must name a route the host actually mounted.

    FastAPI wraps an included router in a lazy ``_IncludedRouter``, so ``app.routes``
    does not list the concrete paths. The host's own registry is the record of what was
    mounted, and it is what this asserts against.
    """

    feature = _feature()
    record = dsr.features.REGISTRY.by_id(FEATURE_ID)
    assert record is not None
    mounted = {(method, shape["path"]) for shape in record.routes for method in shape["methods"]}
    for route in feature.router.routes:
        for method in route.methods:
            if method in ("HEAD", "OPTIONS"):
                continue
            source = f"{method} {route.path}"
            assert (method, route.path) in mounted, source


# --------------------------------------------------------------------------- #
# The board and the research
# --------------------------------------------------------------------------- #


def test_summary_starts_empty(client: TestClient) -> None:
    body = client.get(f"{PREFIX}/summary").json()
    assert body["setups"] == 0
    assert body["minimum_charge_usd"] == 0.50
    assert body["tax_id_limit"] == 3


def test_vocabulary_is_served(client: TestClient) -> None:
    body = client.get(f"{PREFIX}/vocabulary").json()
    assert body["acceptance_methods"] == ["esignature", "clickwrap", "print_and_sign"]
    assert body["clickwrap_anonymous_buyer"] == "Buyer (no contact specified)"
    assert "amount_due_below_minimum" in body["reason_codes"]
    assert body["evidence"]["minimum_charge"].startswith("when using HubSpot payments")


def test_decisions_are_served(client: TestClient) -> None:
    body = client.get(f"{PREFIX}/decisions").json()
    assert body["count"] == len(body["decisions"])
    assert body["count"] >= 2
    assert all(entry["chosen"] in entry["options"] for entry in body["decisions"])


# --------------------------------------------------------------------------- #
# The flow
# --------------------------------------------------------------------------- #


def test_the_whole_flow_through_the_routes(client: TestClient) -> None:
    quote_id = provisioned(client, name="Setup", unit_price=1200)
    add_line(
        client, quote_id, name="Seats", quantity=25, unit_price=12, billing_frequency="monthly"
    )

    accepted = accept(client, quote_id, accepted_by="Ada Byron")
    assert accepted["acceptance"]["accepted_by"] == "Ada Byron"
    assert accepted["quote"]["accepted"] is True
    assert accepted["quote"]["hs_status"] == "ACCEPTED"
    assert len(accepted["invoices"]) == 4
    assert [sub["frequency"] for sub in accepted["subscriptions"]] == ["monthly"]

    paid = charge(client, quote_id, payment_method="CREDIT_OR_DEBIT_CARD")
    assert paid.status_code == 200, paid.text
    body = paid.json()
    assert body["outcome"] == "recorded"
    assert body["charge"]["amount"] == 1500.0

    detail = client.get(f"{PREFIX}/quotes/{quote_id}").json()
    assert detail["setup"]["acceptance_method"] == "clickwrap"
    assert detail["quote"]["hs_status"] == "ACCEPTED"
    assert len(detail["charges"]) == 1
    assert detail["charges"][0]["outcome"] == "recorded"
    assert [a["activity"] for a in detail["activity"]] == [
        "payment_enabled",
        "quote_accepted",
        "payment_recorded",
    ]


def test_the_board_and_the_list_after_a_flow(client: TestClient) -> None:
    quote_id = provisioned(client, unit_price=40)
    accept(client, quote_id)
    charge(client, quote_id)

    listed = client.get(f"{PREFIX}/quotes").json()
    assert listed["count"] == 1
    assert listed["quotes"][0]["quote"]["id"] == quote_id

    summary = client.get(f"{PREFIX}/summary", params={"room_id": "room_a"}).json()
    assert summary["quotes"] == 1
    assert summary["accepted"] == 1
    assert summary["charges_recorded"] == 1
    assert summary["subscriptions"] == 0


def test_invoices_and_charges_routes(client: TestClient) -> None:
    quote_id = provisioned(client, unit_price=40)
    accept(client, quote_id)
    charge(client, quote_id)

    invoices = client.get(f"{PREFIX}/quotes/{quote_id}/invoices").json()
    assert invoices["count"] == 1
    assert invoices["invoices"][0]["kind"] == "first"

    charges = client.get(f"{PREFIX}/quotes/{quote_id}/charges").json()
    assert charges["count"] == 1
    assert charges["charges"][0]["outcome"] == "recorded"


def test_a_quote_without_a_setup_reads_with_a_null_setup(client: TestClient) -> None:
    quote = create_quote(client)
    body = client.get(f"{PREFIX}/quotes/{quote['id']}").json()
    assert body["setup"] is None
    assert body["invoices"] == []


# --------------------------------------------------------------------------- #
# A declined charge is an outcome and not an error
# --------------------------------------------------------------------------- #


def test_a_charge_below_the_minimum_is_a_200_decline_with_a_reason(client: TestClient) -> None:
    quote_id = provisioned(client, unit_price=0.2)
    accept(client, quote_id)
    response = charge(client, quote_id)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["outcome"] == "declined"
    assert body["reason"] == "amount_due_below_minimum"
    assert body["charge"]["settled_at"] is None
    assert "0.50" in body["detail"]
    # The quote is still accepted: a declined charge never undoes an acceptance.
    assert client.get(f"{PREFIX}/quotes/{quote_id}").json()["quote"]["accepted"] is True


# --------------------------------------------------------------------------- #
# The refusals
# --------------------------------------------------------------------------- #


def test_publish_refuses_an_unknown_acceptance_method(client: TestClient) -> None:
    quote = create_quote(client)
    add_line(client, quote["id"])
    response = client.post(
        f"{PREFIX}/quotes/{quote['id']}/publish", json={"acceptance_method": "carrier pigeon"}
    )
    assert response.status_code == 400
    body = response.json()
    assert body["reason"] == "unknown_acceptance_method"
    assert body["errors"]["acceptance_method"]


def test_publish_refuses_print_and_sign_with_online_payments(client: TestClient) -> None:
    quote = create_quote(client)
    add_line(client, quote["id"])
    response = client.post(
        f"{PREFIX}/quotes/{quote['id']}/publish", json={"acceptance_method": "print_and_sign"}
    )
    assert response.status_code == 400
    assert response.json()["reason"] == "print_and_sign_with_online_payments"


def test_publish_refuses_a_fractional_quantity(client: TestClient) -> None:
    quote = create_quote(client)
    add_line(client, quote["id"], name="Consulting days", quantity=1.5)
    response = client.post(f"{PREFIX}/quotes/{quote['id']}/publish", json={})
    assert response.status_code == 400
    assert response.json()["reason"] == "fractional_quantity_with_billing"


def test_publish_refuses_an_empty_quote(client: TestClient) -> None:
    quote = create_quote(client)
    response = client.post(f"{PREFIX}/quotes/{quote['id']}/publish", json={})
    assert response.status_code == 400
    assert response.json()["reason"] == "quote_has_no_line_items"


def test_accept_refuses_a_second_acceptance(client: TestClient) -> None:
    quote_id = provisioned(client)
    accept(client, quote_id)
    response = client.post(f"{PREFIX}/quotes/{quote_id}/accept", json={})
    assert response.status_code == 409
    assert response.json()["reason"] == "quote_already_accepted"


def test_accept_refuses_an_unpublished_quote(client: TestClient) -> None:
    quote = create_quote(client)
    add_line(client, quote["id"])
    response = client.post(f"{PREFIX}/quotes/{quote['id']}/accept", json={})
    assert response.status_code == 404
    assert response.json()["reason"] == "quote_is_not_published_with_payments"


def test_payment_refuses_before_acceptance(client: TestClient) -> None:
    quote_id = provisioned(client)
    response = charge(client, quote_id)
    assert response.status_code == 409
    assert response.json()["reason"] == "payment_requires_acceptance"


def test_payment_refuses_a_second_charge(client: TestClient) -> None:
    quote_id = provisioned(client, unit_price=40)
    accept(client, quote_id)
    charge(client, quote_id)
    response = charge(client, quote_id)
    assert response.status_code == 409
    assert response.json()["reason"] == "charge_already_recorded"


def test_payment_refuses_an_unknown_method(client: TestClient) -> None:
    quote_id = provisioned(client, unit_price=40)
    accept(client, quote_id)
    response = charge(client, quote_id, payment_method="gold bullion")
    assert response.status_code == 400
    assert response.json()["reason"] == "unknown_payment_method"


def test_payment_refuses_a_disallowed_method(client: TestClient) -> None:
    quote = create_quote(client)
    add_line(client, quote["id"], unit_price=40)
    publish(client, quote["id"], allowed_payment_methods=["ACH"])
    accept(client, quote["id"])
    response = charge(client, quote["id"], payment_method="SEPA")
    assert response.status_code == 400
    assert response.json()["reason"] == "payment_method_not_allowed"


def test_tax_ids_cap_at_three(client: TestClient) -> None:
    quote_id = provisioned(client)
    for index in range(3):
        response = client.post(f"{PREFIX}/quotes/{quote_id}/tax-ids", json={"value": f"US-{index}"})
        assert response.status_code == 201, response.text
    response = client.post(f"{PREFIX}/quotes/{quote_id}/tax-ids", json={"value": "US-4"})
    assert response.status_code == 400
    assert response.json()["reason"] == "tax_id_limit_reached"


def test_tax_id_refuses_an_empty_value(client: TestClient) -> None:
    quote_id = provisioned(client)
    response = client.post(f"{PREFIX}/quotes/{quote_id}/tax-ids", json={"value": "  "})
    assert response.status_code == 400
    assert response.json()["reason"] == "tax_id_value_is_empty"


def test_void_and_delete_are_refused_after_acceptance(client: TestClient) -> None:
    quote_id = provisioned(client)
    accept(client, quote_id)
    voided = client.post(f"{PREFIX}/quotes/{quote_id}/void")
    assert voided.status_code == 409
    assert voided.json()["reason"] == "quote_is_irreversible_after_acceptance"
    deleted = client.delete(f"{PREFIX}/quotes/{quote_id}")
    assert deleted.status_code == 409
    assert deleted.json()["reason"] == "quote_is_irreversible_after_acceptance"


def test_void_works_before_acceptance(client: TestClient) -> None:
    quote_id = provisioned(client)
    response = client.post(f"{PREFIX}/quotes/{quote_id}/void")
    assert response.status_code == 200
    assert response.json()["hs_status"] == "VOID"


def test_delete_works_before_acceptance(client: TestClient) -> None:
    quote_id = provisioned(client)
    response = client.delete(f"{PREFIX}/quotes/{quote_id}")
    assert response.status_code == 200
    assert response.json()["deleted"] is True


def test_a_line_item_needs_a_name(client: TestClient) -> None:
    quote = create_quote(client)
    response = client.post(f"{PREFIX}/quotes/{quote['id']}/line-items", json={})
    assert response.status_code == 400
    assert "name" in response.json()["errors"]


# --------------------------------------------------------------------------- #
# The not-found rule
# --------------------------------------------------------------------------- #


def test_every_quote_route_answers_404_for_an_absent_quote(client: TestClient) -> None:
    """Never a 500: a feature must not turn a missing row into a server fault."""

    absent = "no-such-quote"
    cases = [
        ("get", f"{PREFIX}/quotes/{absent}", None),
        ("post", f"{PREFIX}/quotes/{absent}/line-items", {}),
        ("post", f"{PREFIX}/quotes/{absent}/publish", {}),
        ("post", f"{PREFIX}/quotes/{absent}/accept", {}),
        ("post", f"{PREFIX}/quotes/{absent}/payment", {}),
        ("get", f"{PREFIX}/quotes/{absent}/invoices", None),
        ("get", f"{PREFIX}/quotes/{absent}/charges", None),
        ("post", f"{PREFIX}/quotes/{absent}/tax-ids", {"value": "US-1"}),
        ("post", f"{PREFIX}/quotes/{absent}/void", None),
        ("delete", f"{PREFIX}/quotes/{absent}", None),
    ]
    for method, path, body in cases:
        response = (
            getattr(client, method)(path, json=body)
            if body is not None
            else getattr(client, method)(path)
        )
        assert response.status_code == 404, (method, path, response.status_code, response.text)
        assert response.json()["error"] == "quote_not_found"


# --------------------------------------------------------------------------- #
# The audit-source rule and the read-path rule
# --------------------------------------------------------------------------- #


def test_writes_record_a_source_that_names_a_mounted_route(client: TestClient) -> None:
    quote_id = provisioned(client, unit_price=40)
    accept(client, quote_id)
    charge(client, quote_id)

    record = dsr.features.REGISTRY.by_id(FEATURE_ID)
    assert record is not None
    mounted = {(method, shape["path"]) for shape in record.routes for method in shape["methods"]}
    sources = {
        row["source"]
        for row in _store(client).audit(limit=1000)
        if (row.get("source") or "").startswith(("POST /api/wf-096", "DELETE /api/wf-096"))
    }
    assert sources
    for source in sources:
        method, _, path = source.partition(" ")
        assert (method, path) in mounted, source


def test_reads_write_no_audit_rows(client: TestClient) -> None:
    quote_id = provisioned(client)
    store = _store(client)
    before = len(store.audit(limit=1000))
    client.get(f"{PREFIX}/summary")
    client.get(f"{PREFIX}/vocabulary")
    client.get(f"{PREFIX}/decisions")
    client.get(f"{PREFIX}/quotes")
    client.get(f"{PREFIX}/quotes/{quote_id}")
    client.get(f"{PREFIX}/quotes/{quote_id}/invoices")
    client.get(f"{PREFIX}/quotes/{quote_id}/charges")
    after = len(store.audit(limit=1000))
    assert after == before
