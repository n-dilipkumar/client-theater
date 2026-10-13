"""WF-102 HTTP tests: the mounted router, its errors and its audit trail.

The client is the conftest module-scoped one, repointed at a fresh database per
test, so each test sees only its own rows and the file passes alone. A quote and
its line items are created through the generic ``/api/records`` route, which is
how they already exist when this workflow runs.
"""

from __future__ import annotations

PREFIX = "/api/wf-102"
QUOTE = "wf086_quote"
LINE = "wf086_line_item"


def make_quote(client, *, status="draft", room_id="room_1", **overrides):
    payload = {
        "title": "Northwind annual quote",
        "status": status,
        "revision_number": 0,
        "payment_schedule": [],
        "totals": {},
    }
    payload.update(overrides)
    response = client.post(f"/api/records/{QUOTE}", json=payload, params={"room_id": room_id})
    assert response.status_code == 201, response.text
    return response.json()


def make_line(client, quote_id, *, position=0, room_id="room_1", **overrides):
    payload = {
        "quote_id": quote_id,
        "position": position,
        "name": "Platform licence",
        "sku": "NWD-PLAT-ANN",
        "quantity": 2,
        "unit_price": 100.0,
        "discount_type": "percentage",
        "discount_value": 10.0,
        "tax_rate": 10.0,
        "price_source": "manual",
        "amounts": {},
    }
    payload.update(overrides)
    response = client.post(f"/api/records/{LINE}", json=payload, params={"room_id": room_id})
    assert response.status_code == 201, response.text
    return response.json()


def make_product(client, *, unit_price=250.0, room_id="room_1"):
    response = client.post(
        "/api/records/wf087_product",
        json={"name": "Platform licence", "sku": "NWD-PLAT-ANN", "unit_price": unit_price},
        params={"room_id": room_id},
    )
    assert response.status_code == 201, response.text
    return response.json()


def given_a_quote(client, **overrides):
    quote = make_quote(client, **overrides)
    make_line(client, quote["id"])
    return quote


# --------------------------------------------------------------------------- #
# Discovery and registry
# --------------------------------------------------------------------------- #


def test_the_router_is_mounted_by_discovery(client):
    response = client.get(f"{PREFIX}/summary")
    assert response.status_code == 200
    assert "quotes" in response.json()


def test_the_feature_appears_in_the_registry(client):
    body = client.get("/api/features").json()
    ids = {feature.get("id") for feature in body.get("features", [])}
    assert "wf-102-quote-revision" in ids
    assert body.get("failed_count", 0) == 0


def test_the_vocabulary_serves_the_reasons_and_option_values(client):
    payload = client.get(f"{PREFIX}/vocabulary").json()
    assert payload["ticket"] == "WF-102"
    assert payload["close_reasons"] == ["lost", "canceled", "revised"]
    assert payload["quote_creation_methods"]["776160001"] == "Revision"
    assert payload["collections"]["quote"] == "wf086_quote"
    assert payload["recall"]["blocked_states"] == ["approved", "accepted", "paid"]


# --------------------------------------------------------------------------- #
# Revise
# --------------------------------------------------------------------------- #


def test_revising_creates_the_next_revision_and_closes_the_prior(client):
    quote = given_a_quote(client)

    response = client.post(f"{PREFIX}/quotes/{quote['id']}/revise", json={})
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["new_revision_number"] == 1
    assert body["quote"]["status"] == "draft"
    assert body["quote"]["revision_number"] == 1
    assert body["prior"]["status"] == "revised"
    assert body["prior"]["closedon"]

    prior = client.get(f"{PREFIX}/quotes/{quote['id']}").json()
    assert prior["status"] == "revised"
    assert prior["closed"] is True


def test_the_prior_line_items_stay_readable_after_a_revision(client):
    quote = given_a_quote(client)
    original_lines = client.get(f"/api/records/{LINE}", params={"where": f"quote_id={quote['id']}"})

    client.post(f"{PREFIX}/quotes/{quote['id']}/revise", json={})

    still_one = client.get(f"/api/records/{LINE}", params={"where": f"quote_id={quote['id']}"})
    assert still_one.status_code == 200
    assert original_lines.json()["count"] == still_one.json()["count"] == 1


def test_the_revision_reprices_from_the_price_list(client):
    make_product(client, unit_price=250.0)
    quote = given_a_quote(client, title="Price list quote")

    body = client.post(f"{PREFIX}/quotes/{quote['id']}/revise", json={}).json()

    assert body["repriced_line_items"] == 1
    assert body["quote"]["line_items"][0]["unit_price"] == 250.0
    assert body["quote"]["line_items"][0]["price_source"] == "catalog"


def test_the_audit_row_names_the_revise_route(client):
    quote = given_a_quote(client)
    client.post(f"{PREFIX}/quotes/{quote['id']}/revise", json={})

    audit = client.get("/api/audit", params={"record_id": quote["id"]}).json()
    sources = {entry["source"] for entry in audit["entries"]}
    assert f"POST {PREFIX}/quotes/{quote['id']}/revise" in sources


def test_revising_a_closed_quote_is_a_conflict(client):
    quote = given_a_quote(client, status="lost")
    response = client.post(f"{PREFIX}/quotes/{quote['id']}/revise", json={})
    assert response.status_code == 409
    assert response.json()["reason"] == "quote_already_closed"


def test_revising_a_project_quote_is_a_conflict(client):
    quote = given_a_quote(client, quote_scope="project_quote")
    response = client.post(f"{PREFIX}/quotes/{quote['id']}/revise", json={})
    assert response.status_code == 409
    assert response.json()["reason"] == "project_quote_unsupported"


def test_revising_a_quote_with_no_lines_is_a_conflict(client):
    quote = make_quote(client)
    response = client.post(f"{PREFIX}/quotes/{quote['id']}/revise", json={})
    assert response.status_code == 409
    assert response.json()["reason"] == "quote_is_empty"


def test_revising_an_unknown_quote_is_a_404(client):
    response = client.post(f"{PREFIX}/quotes/q_absent/revise", json={})
    assert response.status_code == 404


# --------------------------------------------------------------------------- #
# Close
# --------------------------------------------------------------------------- #


def test_each_close_reason_closes_the_quote(client):
    for reason in ("lost", "canceled", "revised"):
        quote = given_a_quote(client, status="published", title=f"Close as {reason}")
        response = client.post(f"{PREFIX}/quotes/{quote['id']}/close", json={"reason": reason})
        assert response.status_code == 200, response.text
        assert response.json()["quote"]["status"] == reason
        assert response.json()["quote"]["closedon"]


def test_an_unknown_close_reason_is_a_422_with_the_field(client):
    quote = given_a_quote(client)
    response = client.post(f"{PREFIX}/quotes/{quote['id']}/close", json={"reason": "voided"})
    assert response.status_code == 422
    assert "reason" in response.json()["errors"]


def test_a_close_writes_the_quoteclose_activity(client):
    quote = given_a_quote(client, status="published")
    client.post(f"{PREFIX}/quotes/{quote['id']}/close", json={"reason": "lost"})

    activity = client.get(f"{PREFIX}/quotes/{quote['id']}/activity").json()
    assert activity["count"] == 1
    assert activity["entries"][0]["action"] == "quote_close"
    assert activity["entries"][0]["reason"] == "lost"


# --------------------------------------------------------------------------- #
# Clone and reprice
# --------------------------------------------------------------------------- #


def test_cloning_copies_the_price_and_leaves_the_original_open(client):
    make_product(client, unit_price=250.0)
    quote = given_a_quote(client)

    body = client.post(f"{PREFIX}/quotes/{quote['id']}/clone", json={}).json()

    assert body["prior_unchanged"] is True
    assert body["quote"]["revision_number"] == 0
    assert body["quote"]["line_items"][0]["unit_price"] == 100.0
    assert client.get(f"{PREFIX}/quotes/{quote['id']}").json()["status"] == "draft"


def test_repricing_a_draft_adjusts_the_price(client):
    make_product(client, unit_price=250.0)
    quote = given_a_quote(client)

    body = client.post(f"{PREFIX}/quotes/{quote['id']}/reprice", json={}).json()

    assert body["adjusted"] is True
    assert body["quote"]["line_items"][0]["unit_price"] == 250.0


def test_repricing_a_published_quote_is_frozen(client):
    make_product(client, unit_price=250.0)
    quote = given_a_quote(client, status="published")

    body = client.post(f"{PREFIX}/quotes/{quote['id']}/reprice", json={}).json()

    assert body["adjusted"] is False
    assert body["frozen"] is True
    assert body["reason"] == "quote_frozen"


# --------------------------------------------------------------------------- #
# Recall and re-publish
# --------------------------------------------------------------------------- #


def test_recall_and_republish_regenerate_the_link(client):
    quote = given_a_quote(client, status="published")

    recalled = client.post(f"{PREFIX}/quotes/{quote['id']}/recall", json={})
    assert recalled.status_code == 200
    assert recalled.json()["quote"]["status"] == "draft"

    republished = client.post(f"{PREFIX}/quotes/{quote['id']}/republish", json={})
    assert republished.status_code == 200
    kinds = {artefact["kind"] for artefact in republished.json()["artefacts"]}
    assert kinds == {"link", "pdf"}

    actions = [
        entry["action"]
        for entry in client.get(f"{PREFIX}/quotes/{quote['id']}/activity").json()["entries"]
    ]
    assert "quote_recalled" in actions
    assert "quote_republished" in actions


def test_recall_is_blocked_once_the_quote_is_paid(client):
    quote = given_a_quote(client, status="published", hs_payment_status="paid")
    response = client.post(f"{PREFIX}/quotes/{quote['id']}/recall", json={})
    assert response.status_code == 409
    assert response.json()["reason"] == "recall_blocked_after_approval"


def test_recall_is_blocked_when_the_quote_cannot_be_restored(client):
    quote = given_a_quote(client, status="published", esignature_enabled=True)
    response = client.post(f"{PREFIX}/quotes/{quote['id']}/recall", json={})
    assert response.status_code == 409
    assert response.json()["reason"] == "recall_blocked_no_restore"


def test_republishing_a_published_quote_is_a_conflict(client):
    quote = given_a_quote(client, status="published")
    response = client.post(f"{PREFIX}/quotes/{quote['id']}/republish", json={})
    assert response.status_code == 409
    assert response.json()["reason"] == "republish_needs_a_recall"


# --------------------------------------------------------------------------- #
# Reads
# --------------------------------------------------------------------------- #


def test_the_lineage_reads_the_chain(client):
    quote = given_a_quote(client)
    first = client.post(f"{PREFIX}/quotes/{quote['id']}/revise", json={}).json()["quote"]["id"]
    client.post(f"{PREFIX}/quotes/{first}/revise", json={})

    lineage = client.get(f"{PREFIX}/quotes/{quote['id']}/lineage").json()

    assert lineage["count"] == 3
    assert [step["revision_number"] for step in lineage["revisions"]] == [0, 1, 2]


def test_an_unknown_quote_is_a_404(client):
    assert client.get(f"{PREFIX}/quotes/q_absent").status_code == 404
    assert client.get(f"{PREFIX}/quotes/q_absent/lineage").status_code == 404
    assert (
        client.post(f"{PREFIX}/quotes/q_absent/close", json={"reason": "lost"}).status_code == 404
    )


# --------------------------------------------------------------------------- #
# Route health
# --------------------------------------------------------------------------- #


def test_every_advertised_route_answers_without_a_5xx(client):
    get_paths = [
        "/summary",
        "/vocabulary",
        "/quotes",
        "/quotes/q_absent",
        "/quotes/q_absent/lineage",
        "/quotes/q_absent/activity",
    ]
    for path in get_paths:
        response = client.get(f"{PREFIX}{path}")
        assert response.status_code < 500, f"GET {path} answered {response.status_code}"

    post_paths = [
        "/quotes/q_absent/revise",
        "/quotes/q_absent/close",
        "/quotes/q_absent/clone",
        "/quotes/q_absent/reprice",
        "/quotes/q_absent/recall",
        "/quotes/q_absent/republish",
    ]
    for path in post_paths:
        response = client.post(f"{PREFIX}{path}", json={})
        assert response.status_code < 500, f"POST {path} answered {response.status_code}"
