"""WF-129 over HTTP: the surface this feature's own router serves.

The domain rules are in ``test_wf129.py``. This file is the other half, and it is
organised by what a caller can observe:

``the route table``
    That the host mounted every route by discovery alone, with no shared file
    edited, and that no two of them collide.
``the board``
    The summary, the vocabulary and the decisions the page reads.
``the order form``
    Create, read, list, edit a line, redline, accept, sign and file, in the order
    the research puts them.
``the refusals``
    Every status code and body this router can produce, and the field-keyed map
    that lands next to each input.
``the audit-source rule``
    Every ``source=`` this workflow records names a route the host mounted.
``the read-path rule``
    The reads write no audit rows.

Every test here passes with the file run on its own.
"""

from __future__ import annotations

import inspect
from typing import Any

from dsr.inroom_ordering import vocabulary as vocab
from dsr.store import RecordStore
from fastapi.testclient import TestClient

FEATURE_MODULE = "dsr.features.wf129_quote_to_order_form_inside_the_room"
PREFIX = "/api/wf-129"
FEATURE_ID = "wf-129-inroom-order-form"
SIGNER = {"name": "Ada Byron", "email": "ada@northwind.example"}


def _store(client: TestClient) -> RecordStore:
    from dsr.api import app

    return app.state.store


def seed_quote(store: RecordStore, *, lines: int = 2) -> str:
    """A quote with lines, written the way WF-086 would write it."""
    quote = store.create(
        vocab.QUOTE_COLLECTION,
        {
            "title": "Northwind annual order",
            "status": "draft",
            "currency": "USD",
            "account": "Northwind Traders",
            "deal_id": "deal_1",
        },
        room_id="room_a",
        actor="dana",
        source="test",
    )
    catalogue = [
        ("Northwind Platform, annual", "NWD-PLAT-ANN", 10, 4800.0, "recurring", 10.0),
        ("Onboarding workshop", "NWD-IMP-ONB", 2, 1500.0, "one_time", 0.0),
    ]
    for position, (name, sku, qty, price, billing, tax) in enumerate(catalogue[:lines]):
        store.create(
            vocab.QUOTE_LINE_ITEM_COLLECTION,
            {
                "quote_id": quote["id"],
                "position": position,
                "name": name,
                "sku": sku,
                "quantity": qty,
                "unit_price": price,
                "billing_frequency": billing,
                "tax_rate": tax,
            },
            room_id="room_a",
            actor="dana",
            source="test",
        )
    return quote["id"]


def create_form(client: TestClient, **extra: Any) -> dict[str, Any]:
    store = _store(client)
    payload = {"quote_id": seed_quote(store)}
    payload.update(extra)
    response = client.post(f"{PREFIX}/order-forms", params={"room_id": "room_a"}, json=payload)
    assert response.status_code == 201, response.text
    return response.json()["order_form"]


def accepted(client: TestClient, **extra: Any) -> dict[str, Any]:
    form = create_form(client, **extra)
    response = client.post(
        f"{PREFIX}/order-forms/{form['id']}/accept", json={"accepted_by": "Ada Byron"}
    )
    assert response.status_code == 200, response.text
    return response.json()["order_form"]


# --------------------------------------------------------------------------- #
# The route table
# --------------------------------------------------------------------------- #


def test_the_host_mounted_the_router_by_discovery_alone(client: TestClient):
    from dsr.api import app
    from dsr.features import load_features

    registry = load_features(app)
    feature = registry.by_id(FEATURE_ID)

    assert feature is not None
    assert feature.loaded is True, feature.error
    assert feature.prefix == PREFIX
    assert feature.module == FEATURE_MODULE


def test_the_feature_exports_the_three_things_a_feature_contributes(client: TestClient):
    from dsr.features import wf129_quote_to_order_form_inside_the_room as feature

    assert feature.FEATURE["id"] == FEATURE_ID
    assert feature.FEATURE["ticket"] == "WF-129"
    assert feature.FEATURE["name"]
    assert callable(feature.seed)
    assert feature.EXCEPTION_HANDLERS


def test_the_error_handlers_are_this_workflows_own_three_types(client: TestClient):
    from dsr.api import app
    from dsr.features import load_features

    registry = load_features(app)
    assert sorted(registry.by_id(FEATURE_ID).exception_handlers) == [
        "OrderFormConflict",
        "OrderFormError",
        "OrderFormNotFound",
    ]


def test_no_two_of_this_routers_routes_collide(client: TestClient):
    from dsr.api import app
    from dsr.features import load_features

    registry = load_features(app)
    seen: set[tuple[str, str]] = set()
    for shape in registry.by_id(FEATURE_ID).routes:
        for method in shape["methods"]:
            pair = (method, shape["path"])
            assert pair not in seen, f"{pair} is declared twice on this router"
            seen.add(pair)


def test_the_router_owns_a_ticket_derived_prefix(client: TestClient):
    from dsr.features import wf129_quote_to_order_form_inside_the_room as feature

    assert feature.router.prefix == PREFIX
    assert feature.router.prefix.startswith("/api/wf-129")


# --------------------------------------------------------------------------- #
# The board
# --------------------------------------------------------------------------- #


def test_the_board_and_the_research_answer(client: TestClient):
    for path in ("/summary", "/vocabulary", "/decisions"):
        response = client.get(f"{PREFIX}{path}")
        assert response.status_code == 200, path


def test_the_vocabulary_serves_the_researched_vocabulary(client: TestClient):
    served = client.get(f"{PREFIX}/vocabulary").json()
    assert served["signature_modes"] == ["typed", "drawn", "typed_photo"]
    assert served["statuses"] == ["draft", "accepted", "signed", "filed"]
    assert served["render_formats_unavailable"] == ["pdf", "docx"]
    assert served["payment_in_scope"] is False
    assert served["map_advanced"] is False


def test_the_decisions_route_names_an_alternative_for_every_choice(client: TestClient):
    body = client.get(f"{PREFIX}/decisions").json()
    assert body["count"] >= 6
    for decision in body["decisions"]:
        assert decision["chosen"] in decision["options"]
        assert decision["rejected"] in decision["options"]
        assert len(decision["options"]) >= 2


def test_the_summary_reports_the_three_unbuilt_steps(client: TestClient):
    summary = client.get(f"{PREFIX}/summary").json()
    assert summary["map_advanced"] is False
    assert summary["payment_in_scope"] is False
    assert summary["render_formats_unavailable"] == ["pdf", "docx"]


# --------------------------------------------------------------------------- #
# Creating, reading and listing
# --------------------------------------------------------------------------- #


def test_creating_a_form_returns_it_at_draft_and_editable(client: TestClient):
    form = create_form(client)
    assert form["status"] == vocab.STATUS_DRAFT
    assert form["buyer_editable"] is True
    assert form["line_count"] == 2
    assert form["signed_in_room"] is True
    assert form["totals"]["total"] > 0


def test_creating_a_form_with_no_quote_is_a_422_naming_the_field(client: TestClient):
    response = client.post(f"{PREFIX}/order-forms", params={"room_id": "room_a"}, json={})
    assert response.status_code == 422
    body = response.json()
    assert body["error"] == "order_form_invalid"
    assert "quote_id" in body["errors"]


def test_creating_a_form_for_an_absent_quote_is_a_404(client: TestClient):
    response = client.post(
        f"{PREFIX}/order-forms", params={"room_id": "room_a"}, json={"quote_id": "q_absent"}
    )
    assert response.status_code == 404
    assert response.json()["error"] == "not_found"


def test_a_form_can_be_read_and_listed(client: TestClient):
    form = create_form(client)
    one = client.get(f"{PREFIX}/order-forms/{form['id']}")
    assert one.status_code == 200
    assert one.json()["id"] == form["id"]
    listed = client.get(f"{PREFIX}/order-forms", params={"room_id": "room_a"})
    assert listed.status_code == 200
    assert listed.json()["count"] == 1


def test_an_absent_form_is_a_404_and_never_a_500(client: TestClient):
    for path in (
        "/order-forms/form_absent",
        "/order-forms/form_absent/revisions",
        "/order-forms/form_absent/certificate",
    ):
        response = client.get(f"{PREFIX}{path}")
        assert response.status_code == 404, path
        assert response.json()["error"] == "not_found"


# --------------------------------------------------------------------------- #
# Editing a line
# --------------------------------------------------------------------------- #


def test_a_draft_line_can_be_edited_and_the_totals_follow(client: TestClient):
    form = create_form(client)
    line = form["lines"][0]
    response = client.patch(
        f"{PREFIX}/order-forms/{form['id']}/lines/{line['id']}", json={"quantity": 20}
    )
    assert response.status_code == 200, response.text
    updated = response.json()["order_form"]
    assert updated["lines"][0]["quantity"] == 20.0
    assert updated["totals"]["total"] > form["totals"]["total"]


def test_a_line_of_a_frozen_form_is_a_409(client: TestClient):
    form = accepted(client)
    line = form["lines"][0]
    response = client.patch(
        f"{PREFIX}/order-forms/{form['id']}/lines/{line['id']}", json={"quantity": 1}
    )
    assert response.status_code == 409
    assert response.json()["reason"] == "form_not_editable"


def test_an_invalid_line_is_a_422_naming_the_field(client: TestClient):
    form = create_form(client)
    line = form["lines"][0]
    response = client.patch(
        f"{PREFIX}/order-forms/{form['id']}/lines/{line['id']}", json={"tax_rate": 500}
    )
    assert response.status_code == 422
    assert "tax_rate" in response.json()["errors"]


def test_an_absent_line_is_a_404(client: TestClient):
    form = create_form(client)
    response = client.patch(
        f"{PREFIX}/order-forms/{form['id']}/lines/line_absent", json={"quantity": 1}
    )
    assert response.status_code == 404


# --------------------------------------------------------------------------- #
# Redlining
# --------------------------------------------------------------------------- #


def test_a_redline_is_proposed_then_accepted_and_bumps_the_version(client: TestClient):
    form = create_form(client)
    line = form["lines"][0]
    proposed = client.post(
        f"{PREFIX}/order-forms/{form['id']}/revisions",
        json={"line_id": line["id"], "field": "unit_price", "proposed_value": 4000.0},
    )
    assert proposed.status_code == 201, proposed.text
    assert proposed.json()["revision"]["state"] == vocab.REVISION_PROPOSED

    resolved = client.post(
        f"{PREFIX}/revisions/{proposed.json()['revision']['id']}/resolve",
        json={"decision": "accept"},
    )
    assert resolved.status_code == 200, resolved.text
    body = resolved.json()
    assert body["revision"]["state"] == vocab.REVISION_ACCEPTED
    assert body["order_form"]["version"] == 2
    assert body["order_form"]["lines"][0]["unit_price"] == 4000.0


def test_a_second_open_redline_on_one_line_is_a_409(client: TestClient):
    form = create_form(client)
    line = form["lines"][0]
    client.post(
        f"{PREFIX}/order-forms/{form['id']}/revisions",
        json={"line_id": line["id"], "field": "quantity", "proposed_value": 1},
    )
    response = client.post(
        f"{PREFIX}/order-forms/{form['id']}/revisions",
        json={"line_id": line["id"], "field": "unit_price", "proposed_value": 1},
    )
    assert response.status_code == 409
    assert response.json()["reason"] == "revision_conflict"


def test_a_redline_on_an_unknown_field_is_a_422(client: TestClient):
    form = create_form(client)
    line = form["lines"][0]
    response = client.post(
        f"{PREFIX}/order-forms/{form['id']}/revisions",
        json={"line_id": line["id"], "field": "amounts", "proposed_value": 1},
    )
    assert response.status_code == 422
    assert response.json()["error"] == "order_form_invalid"


def test_the_revisions_route_lists_the_redlines(client: TestClient):
    form = create_form(client)
    line = form["lines"][0]
    client.post(
        f"{PREFIX}/order-forms/{form['id']}/revisions",
        json={"line_id": line["id"], "field": "quantity", "proposed_value": 5},
    )
    listed = client.get(f"{PREFIX}/order-forms/{form['id']}/revisions")
    assert listed.status_code == 200
    assert listed.json()["count"] == 1


# --------------------------------------------------------------------------- #
# Accepting, signing and filing
# --------------------------------------------------------------------------- #


def test_accepting_moves_the_form_to_accepted(client: TestClient):
    form = create_form(client)
    response = client.post(
        f"{PREFIX}/order-forms/{form['id']}/accept", json={"accepted_by": "Ada Byron"}
    )
    assert response.status_code == 200
    assert response.json()["order_form"]["status"] == vocab.STATUS_ACCEPTED


def test_signing_returns_the_certificate_and_the_artefact(client: TestClient):
    form = accepted(client)
    response = client.post(
        f"{PREFIX}/order-forms/{form['id']}/sign",
        json={"mode": "typed", "text": "Ada Byron", "signer": SIGNER},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["order_form"]["status"] == vocab.STATUS_SIGNED
    assert body["certificate"]["document_sha256"] == body["artefact"]["document_sha256"]
    assert body["certificate"]["verified_by_provider"] is False
    assert "No third-party" in body["certificate"]["statement"]


def test_signing_a_draft_form_is_a_409(client: TestClient):
    form = create_form(client)
    response = client.post(
        f"{PREFIX}/order-forms/{form['id']}/sign",
        json={"mode": "typed", "text": "Ada Byron", "signer": SIGNER},
    )
    assert response.status_code == 409
    assert response.json()["reason"] == "form_not_awaiting_signature"


def test_signing_with_an_unknown_mode_is_a_422(client: TestClient):
    form = accepted(client)
    response = client.post(
        f"{PREFIX}/order-forms/{form['id']}/sign",
        json={"mode": "telepathy", "signer": SIGNER},
    )
    assert response.status_code == 422
    assert response.json()["errors"]["mode"]


def test_signing_with_no_signer_is_a_422(client: TestClient):
    form = accepted(client)
    response = client.post(
        f"{PREFIX}/order-forms/{form['id']}/sign", json={"mode": "typed", "text": "Ada"}
    )
    assert response.status_code == 422
    assert "signer" in response.json()["errors"]


def test_the_certificate_is_404_before_signing_and_200_after(client: TestClient):
    form = accepted(client)
    before = client.get(f"{PREFIX}/order-forms/{form['id']}/certificate")
    assert before.status_code == 404
    client.post(
        f"{PREFIX}/order-forms/{form['id']}/sign",
        json={"mode": "drawn", "strokes": [[0, 0], [1, 1]], "signer": SIGNER},
    )
    after = client.get(f"{PREFIX}/order-forms/{form['id']}/certificate")
    assert after.status_code == 200
    assert after.json()["signer"]["email"] == SIGNER["email"]


def test_filing_writes_the_deal_and_advances_its_close_date(client: TestClient):
    store = _store(client)
    deal = store.create(
        vocab.DEAL_COLLECTION,
        {"name": "Northwind renewal", "currency": "USD", "amount": 0.0, "close_date": "2099-12-31"},
        room_id="room_a",
        actor="dana",
        source="test",
    )
    form = accepted(client, deal_id=deal["id"])
    client.post(
        f"{PREFIX}/order-forms/{form['id']}/sign",
        json={"mode": "typed", "text": "Ada Byron", "signer": SIGNER},
    )
    response = client.post(f"{PREFIX}/order-forms/{form['id']}/file", json={})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["order_form"]["status"] == vocab.STATUS_FILED
    assert body["deal_filed"] is True
    assert body["deal"]["amount"] == body["order_form"]["totals"]["total"]
    assert body["deal"]["close_date"] != "2099-12-31"
    assert body["map_advanced"] is False
    assert store.get(deal["id"])["data"]["amount"] == body["deal"]["amount"]


def test_filing_an_unsigned_form_is_a_409(client: TestClient):
    form = accepted(client)
    response = client.post(f"{PREFIX}/order-forms/{form['id']}/file", json={})
    assert response.status_code == 409
    assert response.json()["reason"] == "form_not_signed"


def test_filing_twice_is_a_409(client: TestClient):
    form = accepted(client)
    client.post(
        f"{PREFIX}/order-forms/{form['id']}/sign",
        json={"mode": "typed", "text": "Ada Byron", "signer": SIGNER},
    )
    client.post(f"{PREFIX}/order-forms/{form['id']}/file", json={})
    again = client.post(f"{PREFIX}/order-forms/{form['id']}/file", json={})
    assert again.status_code == 409
    assert again.json()["reason"] == "form_already_filed"


# --------------------------------------------------------------------------- #
# The audit-source rule
# --------------------------------------------------------------------------- #


def test_every_source_this_router_can_record_names_a_mounted_route(client: TestClient):
    from dsr.api import app
    from dsr.features import load_features, wf129_quote_to_order_form_inside_the_room as feature

    registry = load_features(app)
    mounted = {
        (method, shape["path"])
        for shape in registry.by_id(FEATURE_ID).routes
        for method in shape["methods"]
    }

    assert "router.prefix" in inspect.getsource(feature._source)

    for method, path in (
        ("POST", "/order-forms"),
        ("PATCH", "/order-forms/{form_id}/lines/{line_id}"),
        ("POST", "/order-forms/{form_id}/revisions"),
        ("POST", "/revisions/{revision_id}/resolve"),
        ("POST", "/order-forms/{form_id}/accept"),
        ("POST", "/order-forms/{form_id}/sign"),
        ("POST", "/order-forms/{form_id}/file"),
    ):
        source = feature._source(method, path)
        assert source.startswith(f"{method} {PREFIX}"), source
        assert (method, f"{PREFIX}{path}") in mounted, source


def test_a_write_records_the_route_that_served_it(client: TestClient):
    form = create_form(client)
    row = _store(client).audit(collection=vocab.ORDER_FORM_COLLECTION, limit=1)[0]
    assert row["source"] == f"POST {PREFIX}/order-forms"
    assert form["id"]


def test_a_signature_records_the_sign_route(client: TestClient):
    form = accepted(client)
    client.post(
        f"{PREFIX}/order-forms/{form['id']}/sign",
        json={"mode": "typed", "text": "Ada Byron", "signer": SIGNER},
    )
    rows = _store(client).audit(collection=vocab.SIGNATURE_COLLECTION, limit=10)
    assert rows
    assert rows[-1]["source"] == f"POST {PREFIX}/order-forms/{{form_id}}/sign"


# --------------------------------------------------------------------------- #
# The read-path rule
# --------------------------------------------------------------------------- #


def test_the_reads_write_no_audit_rows(client: TestClient):
    form = create_form(client)
    before = len(_store(client).audit(limit=2000))
    for path in (
        "/summary",
        "/vocabulary",
        "/decisions",
        "/order-forms",
        f"/order-forms/{form['id']}",
        f"/order-forms/{form['id']}/revisions",
    ):
        assert client.get(f"{PREFIX}{path}").status_code == 200, path
    assert len(_store(client).audit(limit=2000)) == before


# --------------------------------------------------------------------------- #
# Every route answers
# --------------------------------------------------------------------------- #


def test_no_route_on_this_router_answers_a_server_fault(client: TestClient):
    """The release pass asserts no 5xx. Every route here is called with absent ids."""
    from dsr.api import app
    from dsr.features import load_features

    registry = load_features(app)
    for shape in registry.by_id(FEATURE_ID).routes:
        path = shape["path"]
        for placeholder in ("{form_id}", "{line_id}", "{revision_id}"):
            path = path.replace(placeholder, "absent")
        response = client.request(shape["methods"][0], f"{PREFIX}{path}", json={})
        assert response.status_code < 500, (
            f"{shape['methods'][0]} {path} returned {response.status_code}"
        )
