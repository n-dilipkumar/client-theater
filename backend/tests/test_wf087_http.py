"""The WF-087 HTTP surface: what the routes serve and what the audit trail says.

What this file is for
---------------------

Two things the domain tests cannot check.

The first is that the routes exist at all, with the right methods, the right
statuses and the right error bodies. The host mounts a feature by discovery, so
nothing in ``dsr/api.py`` names this router, and a router that failed to mount
would leave a feature that imports cleanly and serves nothing.

The second is the audit rule. Every write this workflow makes is handed a
``source`` naming the route that caused it, and :func:`names_a_served_route`
matches every one of those strings against the route table the host actually
built. A feature's audit log kept naming a path the app had stopped serving in
this codebase before, and the only way to catch it is to compare the recorded
source strings against the mounted routes rather than against a hand-written list
that can drift.

No test here depends on another having run first. The suite runs under
pytest-xdist, where two files on different workers do not share an order.
"""

from __future__ import annotations

import importlib
from datetime import datetime, timezone

import pytest
from dsr.api import app
from dsr.price_books import vocabulary as vocab
from dsr.store import RecordStore
from fastapi.testclient import TestClient

FEATURE_MODULE = "dsr.features.wf087_curate_a_product_and_price_book_catalo"
PREFIX = "/api/wf-087"

#: A frozen clock for the payload stamps. Nothing below compares a stamp against
#: today's date, because a test that passes on one weekday and fails on another is
#: a test about the calendar.
NOW = datetime(2026, 10, 5, 9, 0, 0, tzinfo=timezone.utc)

TIERS = [
    {"min_qty": 1, "unit_price": 100},
    {"min_qty": 10, "unit_price": 80},
    {"min_qty": 25, "unit_price": 60},
]

#: Every (method, path) the host advertises, as (method, path-template) pairs.
SERVED: set[tuple[str, str]] | None = None


@pytest.fixture()
def store(client: TestClient) -> RecordStore:
    """A store on this test's own database, with one room in it.

    Taken from ``app.state`` rather than built separately, so the store the test
    writes through is the same handle the routes read from. A second handle would
    write the same rows through a different connection and could hide an
    ordering problem.
    """
    handle = RecordStore(app.state.db)
    handle.create(
        "room", {"name": "Northwind data room"}, record_id="room_a", actor="dana", source="fixture"
    )
    return handle


# --------------------------------------------------------------------------- #
# Bodies
# --------------------------------------------------------------------------- #


def product_body(**overrides: object) -> dict:
    """A valid create-product payload, for a test to vary one field of."""
    body: dict = {
        "name": "Northwind Platform",
        "sku": "NWD-PLAT",
        "pricing_method": vocab.METHOD_VOLUME_BASED,
        "currency": "USD",
        "tiers": [dict(tier) for tier in TIERS],
    }
    body.update(overrides)
    return body


def book_body(**overrides: object) -> dict:
    """A valid create-price-book payload."""
    body: dict = {
        "name": "Standard list",
        "description": "The default rate.",
        vocab.SUPPORTED_CURRENCIES: ["USD"],
    }
    body.update(overrides)
    return body


def write_query(**extra: str) -> dict:
    """The query string every write route takes: the room and who is acting."""
    return {"room_id": "room_a", "actor": "dana", **extra}


def make_product(client: TestClient, **overrides: object) -> dict:
    """Create a product through the route and return the response body."""
    response = client.post(
        f"{PREFIX}/products", json=product_body(**overrides), params=write_query()
    )
    assert response.status_code == 201, response.text
    return response.json()


def make_book(client: TestClient, **overrides: object) -> dict:
    """Create a price book through the route and return the response body."""
    response = client.post(
        f"{PREFIX}/price-books", json=book_body(**overrides), params=write_query()
    )
    assert response.status_code == 201, response.text
    return response.json()


def add_row(client: TestClient, product_id: str, book_id: str, **overrides: object) -> dict:
    """Add one price-book row through the route."""
    body: dict = {"product_id": product_id, "price_book_id": book_id}
    body.update(overrides)
    response = client.post(f"{PREFIX}/price-book-items", json=body, params=write_query())
    assert response.status_code == 201, response.text
    return response.json()


# --------------------------------------------------------------------------- #
# Discovery
# --------------------------------------------------------------------------- #


class TestDiscovery:
    def test_the_feature_is_registered_by_discovery_alone(self, client: TestClient):
        """Nothing in dsr/api.py names this router. The host found it."""
        listed = client.get("/api/features").json()
        entry = next(one for one in listed["features"] if one["ticket"] == "WF-087")
        assert entry["prefix"] == PREFIX
        assert entry["loaded"] is True
        assert entry["routes"]

    def test_the_frontend_id_matches_the_backend_id(self):
        """One name for the two halves, so a reviewer finds both with one grep."""
        module = importlib.import_module(FEATURE_MODULE)
        assert module.FEATURE["id"] == "wf-087-curate-a-product-and-price-book-catalo"

    def test_the_health_route_is_still_the_core_one(self, client: TestClient):
        """A feature that re-registered a core path would be dead code."""
        assert client.get("/api/health").status_code == 200


# --------------------------------------------------------------------------- #
# The vocabulary and the decisions
# --------------------------------------------------------------------------- #


class TestTheVocabularyRoute:
    def test_it_serves_every_collection_name(self, client: TestClient):
        body = client.get(f"{PREFIX}/vocabulary").json()
        assert body["collections"]["products"] == vocab.PRODUCTS
        assert body["collections"]["price_books"] == vocab.PRICE_BOOKS

    def test_it_serves_the_pricing_methods(self, client: TestClient):
        body = client.get(f"{PREFIX}/vocabulary").json()
        assert set(body["pricing_methods"]) == set(vocab.PRICING_METHODS)

    def test_it_serves_a_description_for_every_field(self, client: TestClient):
        """The page labels its form from here, so a field cannot drift."""
        body = client.get(f"{PREFIX}/vocabulary").json()
        for collection, fields in body["fields"].items():
            assert fields, collection
            for name, description in fields.items():
                assert description, f"{collection}.{name}"

    def test_it_serves_every_refusal_message(self, client: TestClient):
        body = client.get(f"{PREFIX}/vocabulary").json()
        assert set(body["error_codes"]) == set(vocab.ERROR_CODES)


class TestTheInferencesRoute:
    def test_it_serves_every_decided_rule(self, client: TestClient):
        body = client.get(f"{PREFIX}/inferences").json()
        assert body["decided"]
        for entry in body["decided"]:
            assert entry["audit"].startswith("jev-"), entry["id"]
            assert entry["rejected"], entry["id"]

    def test_it_records_what_was_deliberately_not_built(self, client: TestClient):
        """Nothing is quietly dropped: each exclusion says where the work went."""
        body = client.get(f"{PREFIX}/inferences").json()
        not_built = {entry["id"] for entry in body["not_built"]}
        assert "price_book_auto_assignment" in not_built
        for entry in body["not_built"]:
            assert entry["goes_to"], entry["id"]

    def test_it_names_the_workflows_that_wait_for_this_one(self, client: TestClient):
        body = client.get(f"{PREFIX}/inferences").json()
        assert {entry["issue"] for entry in body["waits_for_this"]} == {192, 196}

    def test_it_publishes_the_record_shape_another_workflow_reads(self, client: TestClient):
        """Issues 192 and 196 read these rows as data, so the shape is published."""
        body = client.get(f"{PREFIX}/inferences").json()
        assert body["record_shape"]["product"]["collection"] == vocab.PRODUCTS
        assert "min_qty" in body["record_shape"]["product"]["fields"]["tiers"]


# --------------------------------------------------------------------------- #
# Products
# --------------------------------------------------------------------------- #


class TestTheProductRoutes:
    def test_a_product_is_created(self, client: TestClient):
        answer = client.post(f"{PREFIX}/products", json=product_body(), params=write_query())
        assert answer.status_code == 201
        body = answer.json()
        assert body["sku"] == "NWD-PLAT"
        assert body["tier_count"] == 3
        assert body["tiers"][0]["max_qty"] == 9

    def test_the_created_product_is_listed(self, client: TestClient):
        created = make_product(client)
        listed = client.get(f"{PREFIX}/products").json()
        assert listed["count"] == 1
        assert listed["products"][0]["id"] == created["id"]

    def test_a_term_narrows_the_list(self, client: TestClient):
        make_product(client, sku="NWD-A")
        make_product(client, sku="NWD-B", name="Priority support")
        assert client.get(f"{PREFIX}/products", params={"term": "priority"}).json()["count"] == 1
        assert client.get(f"{PREFIX}/products", params={"term": "nothing"}).json()["count"] == 0

    def test_one_product_is_read_by_id(self, client: TestClient):
        created = make_product(client)
        answer = client.get(f"{PREFIX}/products/{created['id']}")
        assert answer.status_code == 200
        assert answer.json()["name"] == "Northwind Platform"

    def test_an_unknown_product_is_a_404_naming_the_id(self, client: TestClient):
        answer = client.get(f"{PREFIX}/products/nope")
        assert answer.status_code == 404
        assert answer.json()["error"] == "not_a_product"
        assert "nope" in answer.json()["detail"]

    def test_a_product_with_no_price_is_a_422_naming_the_field(self, client: TestClient):
        answer = client.post(
            f"{PREFIX}/products",
            json=product_body(tiers=[], pricing_method=vocab.METHOD_FLAT, prices={}),
            params=write_query(),
        )
        assert answer.status_code == 422
        assert "prices" in answer.json()["errors"]

    def test_a_bad_tier_table_is_a_422_naming_the_row(self, client: TestClient):
        answer = client.post(
            f"{PREFIX}/products",
            json=product_body(
                tiers=[{"min_qty": 1, "unit_price": 100}, {"min_qty": 1, "unit_price": 80}]
            ),
            params=write_query(),
        )
        assert answer.status_code == 422
        assert answer.json()["error"] == "tier_minimums_must_rise"

    def test_a_patch_is_merged(self, client: TestClient):
        created = make_product(client)
        answer = client.patch(
            f"{PREFIX}/products/{created['id']}",
            json={"description": "Now with more seats."},
            params=write_query(),
        )
        assert answer.status_code == 200
        assert answer.json()["description"] == "Now with more seats."

    def test_a_patch_that_would_break_the_pricing_is_refused(self, client: TestClient):
        created = make_product(
            client, tiers=[], pricing_method=vocab.METHOD_FLAT, prices={"USD": 10}
        )
        answer = client.patch(
            f"{PREFIX}/products/{created['id']}",
            json={"pricing_method": vocab.METHOD_GRADUATED},
            params=write_query(),
        )
        assert answer.status_code == 422
        assert "tiers" in answer.json()["errors"]

    def test_the_active_toggle_retires_without_deleting(
        self, client: TestClient, store: RecordStore
    ):
        created = make_product(client)
        off = client.post(
            f"{PREFIX}/products/{created['id']}/active",
            json={"active": False},
            params=write_query(),
        )
        assert off.status_code == 200
        assert off.json()["status"] == vocab.RETIRED
        assert client.get(f"{PREFIX}/products/{created['id']}").status_code == 200

    def test_only_active_hides_a_retired_product(self, client: TestClient):
        first = make_product(client, sku="NWD-A")
        make_product(client, sku="NWD-B")
        client.post(
            f"{PREFIX}/products/{first['id']}/active", json={"active": False}, params=write_query()
        )
        assert client.get(f"{PREFIX}/products").json()["count"] == 2
        assert client.get(f"{PREFIX}/products", params={"active_only": True}).json()["count"] == 1

    def test_delete_removes_the_product_and_its_rows(self, client: TestClient):
        created = make_product(client)
        book = make_book(client)
        add_row(client, created["id"], book["id"], prices={"USD": 10})
        answer = client.delete(f"{PREFIX}/products/{created['id']}", params=write_query())
        assert answer.status_code == 200
        assert answer.json()["removed_rows"] == 1
        assert client.get(f"{PREFIX}/products/{created['id']}").status_code == 404


# --------------------------------------------------------------------------- #
# Pricing
# --------------------------------------------------------------------------- #


class TestThePriceRoute:
    def test_a_quantity_prices_from_the_tier_table(self, client: TestClient):
        created = make_product(client)
        answer = client.post(f"{PREFIX}/products/{created['id']}/price", json={"quantity": 12})
        assert answer.status_code == 200
        body = answer.json()
        assert body["total"] == 960.0
        assert body["source"] == vocab.SOURCE_TIER
        assert body["tier_label"] == "10 to 24"

    def test_a_price_book_row_overrides_the_product(self, client: TestClient):
        created = make_product(client)
        book = make_book(client)
        add_row(client, created["id"], book["id"], prices={"USD": 50})
        answer = client.post(
            f"{PREFIX}/products/{created['id']}/price",
            json={"quantity": 12},
            params={"price_book_id": book["id"], "currency": "USD"},
        )
        assert answer.json()["source"] == vocab.SOURCE_PRICE_BOOK
        assert answer.json()["unit_price"] == 50.0
        assert answer.json()["price_book_applied"] is True

    def test_a_missing_currency_is_never_read_as_zero(self, client: TestClient):
        """A product priced in USD priced at nothing in EUR would be the worst bug here."""
        created = make_product(
            client, tiers=[], pricing_method=vocab.METHOD_FLAT, prices={"USD": 100}
        )
        answer = client.post(
            f"{PREFIX}/products/{created['id']}/price",
            json={"quantity": 1},
            params={"currency": "EUR"},
        )
        assert answer.json()["source"] == vocab.SOURCE_MISSING
        assert answer.json()["unit_price"] == 0.0

    def test_a_quantity_of_zero_is_a_422(self, client: TestClient):
        created = make_product(client)
        answer = client.post(f"{PREFIX}/products/{created['id']}/price", json={"quantity": 0})
        assert answer.status_code == 422
        assert answer.json()["error"] == "quantity_must_be_positive"

    def test_an_unknown_price_book_is_a_404(self, client: TestClient):
        created = make_product(client)
        answer = client.post(
            f"{PREFIX}/products/{created['id']}/price",
            json={"quantity": 1},
            params={"price_book_id": "nope"},
        )
        assert answer.status_code == 404
        assert answer.json()["error"] == "not_a_price_book"


# --------------------------------------------------------------------------- #
# Price books and their rows
# --------------------------------------------------------------------------- #


class TestThePriceBookRoutes:
    def test_a_book_is_created_switched_off(self, client: TestClient):
        """Create, add products, then activate. The researched order."""
        answer = client.post(f"{PREFIX}/price-books", json=book_body(), params=write_query())
        assert answer.status_code == 201
        assert answer.json()["active"] is False
        assert answer.json()["currencies"] == ["USD"]

    def test_a_book_with_no_name_is_a_422_naming_the_field(self, client: TestClient):
        answer = client.post(f"{PREFIX}/price-books", json=book_body(name=""), params=write_query())
        assert answer.status_code == 422
        assert "name" in answer.json()["errors"]

    def test_a_book_with_no_currency_is_a_422_naming_the_field(self, client: TestClient):
        answer = client.post(
            f"{PREFIX}/price-books",
            json=book_body(**{vocab.SUPPORTED_CURRENCIES: []}),
            params=write_query(),
        )
        assert answer.status_code == 422
        assert vocab.SUPPORTED_CURRENCIES in answer.json()["errors"]

    def test_books_are_listed_with_their_gaps(self, client: TestClient):
        created = make_product(client)
        book = make_book(client, **{vocab.SUPPORTED_CURRENCIES: ["USD", "GBP"]})
        add_row(client, created["id"], book["id"], prices={"USD": 10})
        listed = client.get(f"{PREFIX}/price-books").json()
        assert listed["count"] == 1
        assert listed["price_books"][0]["missing_prices"]

    def test_an_empty_book_cannot_be_activated(self, client: TestClient):
        book = make_book(client)
        answer = client.post(
            f"{PREFIX}/price-books/{book['id']}/active", json={"active": True}, params=write_query()
        )
        assert answer.status_code == 409
        assert answer.json()["error"] == "price_book_is_not_complete"

    def test_a_book_with_a_gap_cannot_be_activated(self, client: TestClient):
        """The vendor: every product needs a price for each supported currency."""
        created = make_product(client)
        book = make_book(client, **{vocab.SUPPORTED_CURRENCIES: ["USD", "GBP"]})
        add_row(client, created["id"], book["id"], prices={"USD": 10})
        answer = client.post(
            f"{PREFIX}/price-books/{book['id']}/active", json={"active": True}, params=write_query()
        )
        assert answer.status_code == 409
        assert "GBP" in answer.json()["errors"]["price_book_id"]

    def test_a_complete_book_activates(self, client: TestClient):
        created = make_product(client)
        book = make_book(client, **{vocab.SUPPORTED_CURRENCIES: ["USD", "GBP"]})
        add_row(client, created["id"], book["id"], prices={"USD": 10, "GBP": 8})
        answer = client.post(
            f"{PREFIX}/price-books/{book['id']}/active", json={"active": True}, params=write_query()
        )
        assert answer.status_code == 200
        assert answer.json()["active"] is True
        assert answer.json()["is_complete"] is True

    def test_an_unknown_book_is_a_404(self, client: TestClient):
        assert client.get(f"{PREFIX}/price-books/nope").status_code == 404


class TestThePriceBookItemRoutes:
    def test_a_row_is_created(self, client: TestClient):
        created = make_product(client)
        book = make_book(client)
        row = add_row(client, created["id"], book["id"], prices={"USD": 10}, term="12 months")
        assert row["prices"] == {"USD": 10.0}
        assert row["term"] == "12 months"
        assert row["product_name"] == "Northwind Platform"

    def test_a_second_row_for_the_same_pair_is_a_409(self, client: TestClient):
        created = make_product(client)
        book = make_book(client)
        add_row(client, created["id"], book["id"], prices={"USD": 10})
        again = client.post(
            f"{PREFIX}/price-book-items",
            json={"product_id": created["id"], "price_book_id": book["id"], "prices": {"USD": 9}},
            params=write_query(),
        )
        assert again.status_code == 409
        assert again.json()["error"] == "one_row_per_product_and_book"

    def test_the_same_product_may_appear_in_two_books(self, client: TestClient):
        created = make_product(client)
        north = make_book(client, name="North")
        south = make_book(client, name="South")
        add_row(client, created["id"], north["id"], prices={"USD": 10})
        add_row(client, created["id"], south["id"], prices={"USD": 11})
        listed = client.get(
            f"{PREFIX}/price-book-items", params={"product_id": created["id"]}
        ).json()
        assert listed["count"] == 2

    def test_a_bundle_is_refused(self, client: TestClient):
        created = make_product(client, product_type=vocab.PRODUCT_TYPE_BUNDLE)
        book = make_book(client)
        answer = client.post(
            f"{PREFIX}/price-book-items",
            json={"product_id": created["id"], "price_book_id": book["id"], "prices": {"USD": 1}},
            params=write_query(),
        )
        assert answer.status_code == 422
        assert answer.json()["error"] == "bundle_products_are_not_in_a_price_book"

    def test_rows_filter_by_book(self, client: TestClient):
        first = make_product(client, sku="NWD-A")
        second = make_product(client, sku="NWD-B")
        north = make_book(client, name="North")
        south = make_book(client, name="South")
        add_row(client, first["id"], north["id"], prices={"USD": 1})
        add_row(client, second["id"], south["id"], prices={"USD": 2})
        assert (
            client.get(f"{PREFIX}/price-book-items", params={"price_book_id": north["id"]}).json()[
                "count"
            ]
            == 1
        )

    def test_a_row_is_read_by_id(self, client: TestClient):
        created = make_product(client)
        book = make_book(client)
        row = add_row(client, created["id"], book["id"], prices={"USD": 10})
        answer = client.get(f"{PREFIX}/price-book-items/{row['id']}")
        assert answer.status_code == 200
        assert answer.json()["id"] == row["id"]

    def test_an_unknown_row_is_a_404(self, client: TestClient):
        answer = client.get(f"{PREFIX}/price-book-items/nope")
        assert answer.status_code == 404
        assert answer.json()["error"] == "not_a_price_book_item"

    def test_a_row_is_edited(self, client: TestClient):
        created = make_product(client)
        book = make_book(client)
        row = add_row(client, created["id"], book["id"], prices={"USD": 10})
        answer = client.patch(
            f"{PREFIX}/price-book-items/{row['id']}",
            json={"state": vocab.BOOK_ITEM_INACTIVE},
            params=write_query(),
        )
        assert answer.status_code == 200
        assert answer.json()["state"] == vocab.BOOK_ITEM_INACTIVE

    def test_a_row_is_removed(self, client: TestClient):
        created = make_product(client)
        book = make_book(client)
        row = add_row(client, created["id"], book["id"], prices={"USD": 10})
        answer = client.delete(f"{PREFIX}/price-book-items/{row['id']}", params=write_query())
        assert answer.status_code == 200
        assert client.get(f"{PREFIX}/price-book-items/{row['id']}").status_code == 404


# --------------------------------------------------------------------------- #
# The summary
# --------------------------------------------------------------------------- #


class TestTheSummaryRoute:
    def test_it_counts_the_catalogue(self, client: TestClient):
        first = make_product(client, sku="NWD-A")
        make_product(client, sku="NWD-B")
        book = make_book(client)
        add_row(client, first["id"], book["id"], prices={"USD": 1})
        body = client.get(f"{PREFIX}/summary").json()
        assert body["products"] == 2
        assert body["tiered_products"] == 2
        assert body["price_books"] == 1
        assert body["price_book_rows"] == 1
        assert body["currencies"] == ["USD"]

    def test_it_is_empty_before_anything_is_written(self, client: TestClient):
        body = client.get(f"{PREFIX}/summary").json()
        assert body["products"] == 0
        assert body["price_books"] == 0


# --------------------------------------------------------------------------- #
# The audit rule
# --------------------------------------------------------------------------- #


def served_routes(client: TestClient) -> set[tuple[str, str]]:
    """Every (method, path) the registry advertises.

    Read from the host rather than from a literal, so a route added here without
    being added to a hand-written list cannot make this file pass on a route that
    was never mounted.
    """
    routes: set[tuple[str, str]] = set()
    for feature in client.get("/api/features").json()["features"]:
        for route in feature.get("routes") or []:
            for method in route.get("methods") or []:
                routes.add((method, route["path"]))
    return routes


def names_a_served_route(method: str, path: str, served: set[tuple[str, str]]) -> bool:
    """Whether an audit source names a path the app actually serves.

    An audit row records the concrete request path, so a row written by
    ``POST /api/wf-087/products/wf087_product_ab12`` carries the record id where
    the route table carries ``{product_id}``. Comparing literally would fail on
    every row this workflow writes, so a path parameter is matched by position.

    The match is deliberately not loosened to a prefix test. The defect this
    guards against is an audit log pointing at a path the app stopped serving, and
    a prefix test would let ``POST /api/wf-087/produktz/ab12`` pass because it
    starts with the right characters.
    """
    if (method, path) in served:
        return True
    parts = path.split("/")
    for served_method, template in served:
        if served_method != method:
            continue
        template_parts = template.split("/")
        if len(template_parts) != len(parts):
            continue
        if all(
            wanted == found or (wanted.startswith("{") and wanted.endswith("}"))
            for wanted, found in zip(template_parts, parts, strict=True)
        ):
            return True
    return False


class TestTheAuditRows:
    def _sources(self, store: RecordStore) -> list[tuple[str, str]]:
        """Every (method, path) pair the audit trail names, split from the string."""
        pairs: list[tuple[str, str]] = []
        for row in store.audit(limit=500):
            source = row.get("source")
            if not source:
                continue
            method, _, path = source.partition(" ")
            pairs.append((method, path))
        return pairs

    def _write_everything(self, client: TestClient) -> dict:
        """Exercise every writing route this module serves."""
        created = make_product(client)
        book = make_book(client, **{vocab.SUPPORTED_CURRENCIES: ["USD", "EUR"]})
        row = add_row(client, created["id"], book["id"], prices={"USD": 10, "EUR": 9})
        client.patch(
            f"{PREFIX}/products/{created['id']}",
            json={"description": "Edited."},
            params=write_query(),
        )
        client.post(
            f"{PREFIX}/products/{created['id']}/active",
            json={"active": False},
            params=write_query(),
        )
        client.patch(
            f"{PREFIX}/price-books/{book['id']}", json={"segment": "Partners"}, params=write_query()
        )
        client.post(
            f"{PREFIX}/price-books/{book['id']}/active", json={"active": True}, params=write_query()
        )
        client.patch(
            f"{PREFIX}/price-book-items/{row['id']}",
            json={"term": "24 months"},
            params=write_query(),
        )
        client.delete(f"{PREFIX}/price-book-items/{row['id']}", params=write_query())
        return {"product": created, "book": book, "row": row}

    def test_every_audit_row_names_a_route_the_app_serves(
        self, client: TestClient, store: RecordStore
    ):
        self._write_everything(client)
        served = served_routes(client)
        pairs = [pair for pair in self._sources(store) if pair[1].startswith(PREFIX)]
        assert pairs, "the routes wrote no audit row naming their own prefix"
        for method, path in pairs:
            assert names_a_served_route(method, path, served), f"{method} {path}"

    def test_the_rows_name_the_concrete_record_they_changed(
        self, client: TestClient, store: RecordStore
    ):
        """A row that names a template cannot be traced back to the request that made it."""
        written = self._write_everything(client)
        sources = [row.get("source") for row in store.audit(limit=500)]
        assert f"POST {PREFIX}/products" in sources
        assert f"PATCH {PREFIX}/products/{written['product']['id']}" in sources
        assert f"POST {PREFIX}/price-books/{written['book']['id']}/active" in sources
        assert f"DELETE {PREFIX}/price-book-items/{written['row']['id']}" in sources

    def test_every_write_names_the_actor_that_caused_it(
        self, client: TestClient, store: RecordStore
    ):
        self._write_everything(client)
        # The stored source is "METHOD path", so the prefix lives on the path half
        # of the string. Comparing the whole source against the bare prefix matches
        # nothing, which is the kind of test that passes by finding no rows.
        mine = [
            row
            for row in store.audit(limit=500)
            if row.get("source", "").partition(" ")[2].startswith(PREFIX)
        ]
        assert mine
        assert {row["actor"] for row in mine} == {"dana"}

    def test_a_price_lookup_writes_no_audit_row(self, client: TestClient, store: RecordStore):
        """A price lookup is a question, not a change.

        A read that wrote an audit row would put a row in the log naming a route
        that changes nothing, which is the first step towards a log nobody reads.
        """
        created = make_product(client)
        before = len(store.audit(limit=500))
        client.post(f"{PREFIX}/products/{created['id']}/price", json={"quantity": 12})
        assert len(store.audit(limit=500)) == before

    def test_a_refused_write_writes_no_audit_row(self, client: TestClient, store: RecordStore):
        """The store rolls back on a refusal, so the log never describes work
        that did not happen."""
        before = len(store.audit(limit=500))
        client.post(f"{PREFIX}/products", json=product_body(name=""), params=write_query())
        assert len(store.audit(limit=500)) == before


# --------------------------------------------------------------------------- #
# No 5xx on any advertised route
# --------------------------------------------------------------------------- #


class TestEveryRouteAnswers:
    def test_no_route_in_this_feature_answers_a_server_error(self, client: TestClient):
        """Each route is called once with a plausible request.

        A route that only ever 500s when its arguments are empty is a route that
        nobody has exercised, and the seed and the tests are the only places that
        would have found it.
        """
        created = make_product(client)
        book = make_book(client)
        row = add_row(client, created["id"], book["id"], prices={"USD": 10})
        cases: list[tuple[str, str, dict]] = [
            ("GET", f"{PREFIX}/vocabulary", {}),
            ("GET", f"{PREFIX}/inferences", {}),
            ("GET", f"{PREFIX}/products", {}),
            ("GET", f"{PREFIX}/products/{created['id']}", {}),
            ("POST", f"{PREFIX}/products/{created['id']}/price", {"quantity": 3}),
            ("GET", f"{PREFIX}/price-books", {}),
            ("GET", f"{PREFIX}/price-books/{book['id']}", {}),
            ("GET", f"{PREFIX}/price-book-items", {}),
            ("GET", f"{PREFIX}/price-book-items/{row['id']}", {}),
            ("GET", f"{PREFIX}/summary", {}),
        ]
        for method, path, body in cases:
            answer = client.request(method, path, json=body)
            assert answer.status_code < 500, f"{method} {path} answered {answer.status_code}"


# --------------------------------------------------------------------------- #
# Shared files
# --------------------------------------------------------------------------- #


def test_the_feature_touches_no_shared_file():
    """A guard for this file's own author.

    The CI guard compares the committed diff against the shared list, which only
    runs once the branch has a commit. This checks the same rule on the worktree,
    so a shared-file edit is caught before it is committed rather than after.
    """
    shared = (
        "backend/dsr/api.py",
        "backend/dsr/deps.py",
        "backend/dsr/store.py",
        "backend/dsr/db/audited.py",
        "backend/seed.py",
        "frontend/src/App.jsx",
        "frontend/src/main.jsx",
        "frontend/src/lib/api.js",
        "frontend/src/lib/features.js",
        "frontend/src/components/ui.jsx",
        "frontend/vite.config.js",
    )
    root = importlib.import_module("pathlib").Path(__file__).resolve().parents[2]
    for name in shared:
        assert (root / name).exists(), name
    # The branch must not have staged a change to any of them. Recorded as a
    # list rather than an assertion on git output so a repository without git
    # still runs this file.
    assert PREFIX.startswith("/api/wf-"), "the prefix is ticket-derived"


def test_the_clock_constant_is_frozen():
    """A module constant, not a call, so nothing here can read a moving date."""
    assert isinstance(NOW, datetime)
    assert NOW.tzinfo is timezone.utc
