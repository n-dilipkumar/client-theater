"""WF-086: the quoting routes over HTTP.

These tests use the real application, mounted by discovery, so the assertions
are about what a caller gets rather than about what a function returns. That
distinction matters for four things a TestClient is the only way to check:

* the **status code** each error maps to. 422 for a malformed field, 409 for a
  quote whose state refuses the change, 404 for a record that is not there.
* the **error body shape**. A 400 carrying ``errors`` is a form problem; a 409
  carrying ``reason`` is a conflict the page branches on. A caller that gets
  neither has to parse an English sentence.
* that **the routes are mounted at all**. A feature whose import failed is
  reported in the registry's ``failed`` list and skipped, so a broken module
  looks like a feature that simply has no routes. These tests fail instead.
* that the **audit row names a route the app serves**, read back out of the log
  rather than taken on trust.

Run this file on its own and it passes. It runs under pytest-xdist alongside the
rest of the suite, so nothing here depends on another module having written a
row first: every test builds the deal it needs.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from dsr.features import REGISTRY, iter_feature_modules, load_feature
from dsr.store import RecordStore  # noqa: F401  (documented fixture dependency)
from fastapi.testclient import TestClient

MODULE = "wf086_author_a_quote_from_a_deal_or_opportunity"
PREFIX = "/api/wf-086"

#: A date far enough ahead that no run of this file can put it in the past.
#:
#: The routes use the real clock, not a pinned one: ``_engine`` builds a
#: ``QuoteEngine`` with its production default so a caller cannot pass its own
#: ``now`` and make an expired quote look live. A date derived from a pinned
#: ``NOW`` constant is therefore wrong the moment the real clock passes it, and
#: the failure reads as "future payments are not counted" rather than as "this
#: test's date is stale". The domain tests in ``test_wf086.py`` inject the clock
#: and can pin it; these cannot.
LATER = "2099-06-13"

#: Yesterday, for the same reason. Computed rather than written down.
YESTERDAY = (date.today() - timedelta(days=1)).isoformat()


@pytest.fixture()
def deal(client: TestClient, db) -> dict:
    """A deal in the CRM mirror with three priced line items.

    Built over ``db`` rather than over the ``store`` fixture, and that is not a
    detail. ``client`` points ``app.state.store`` at the ``db`` fixture's
    database; ``store`` is backed by a *separate* in-memory one. A deal written
    through ``store`` is invisible to every route, and the failure reads as "the
    deal lookup does not work" rather than "the test wrote to the wrong file".
    ``backend/tests/conftest.py`` says this in its module docstring.

    The line items go in as records too: this feature only reads them. The deal
    is somebody else's collection, so nothing is written through this feature's
    routes.
    """
    store = RecordStore(db)
    record = store.create(
        "crm_deal",
        {
            "name": "Northwind renewal",
            "account": "Northwind Traders",
            "owner": "dana",
            "currency": "EUR",
            "amount": 24000.0,
            "stage": "negotiation",
        },
        room_id="room-1",
        actor="test",
        source="test",
    )
    for position, (name, price, quantity) in enumerate(
        [
            ("Platform, annual", 4800.0, 30),
            ("Support, annual", 2400.0, 1),
            ("Onboarding", 1500.0, 2),
        ]
    ):
        store.create(
            "crm_deal_line_item",
            {
                "deal_id": record["id"],
                "position": position,
                "name": name,
                "quantity": quantity,
                "unit_price": price,
                "tax_rate": 10.0,
            },
            room_id="room-1",
            actor="test",
            source="test",
        )
    return record


@pytest.fixture()
def live_store(client: TestClient, db) -> RecordStore:
    """A store over the same database the routes read.

    Tests that need to assert on what a route wrote read through this, never
    through ``store``: ``store`` is a different database and every such
    assertion would pass vacuously.
    """
    return RecordStore(db)


def _quote(client: TestClient, deal: dict, **body: object) -> dict:
    """Create a quote and return it, failing loudly on anything but a 200."""
    response = client.post(
        f"{PREFIX}/quotes", params={"room_id": "room-1"}, json={"deal_id": deal["id"], **body}
    )
    assert response.status_code == 200, response.text
    return response.json()["quote"]


# --------------------------------------------------------------------------- #
# Mounting
# --------------------------------------------------------------------------- #


def test_the_feature_is_mounted_rather_than_skipped(client: TestClient):
    """A feature that raises on import is reported in ``failed`` and skipped.

    Without this the whole file would 404 on every call and the failures would
    read as "the routes do not exist" instead of "the module is broken".
    """
    failures = [f for f in REGISTRY.failed if MODULE in f.module or f.id.startswith("wf086")]
    assert not failures, [f.error for f in failures]
    assert client.get(f"{PREFIX}/summary").status_code == 200


def test_the_prefix_is_unique_across_the_product(client: TestClient):
    """Starlette matches in registration order, so a duplicate prefix with a
    duplicate concrete path would be dead code."""
    owners = [
        feature
        for feature in REGISTRY.features
        for route in feature.routes
        if route["path"] == f"{PREFIX}/summary"
    ]
    assert [feature.id for feature in owners] == [
        "wf-086-author-a-quote-from-a-deal-or-opportunity"
    ]


def test_the_module_exports_the_contract_the_host_reads():
    module = load_feature(MODULE)
    assert module.FEATURE["ticket"] == "WF-086"
    assert module.router.prefix == PREFIX
    assert sorted(error.__name__ for error in module.EXCEPTION_HANDLERS) == [
        "QuoteConflict",
        "QuoteError",
        "QuoteNotFound",
    ]
    assert callable(module.seed)


def test_the_module_does_not_import_the_application():
    """The dependency direction, on the source.

    ``tests/test_features.py`` refuses ``dsr.api`` across the whole feature
    package. This asserts the same property for the one module this ticket
    names, so a failure names the file rather than the package.
    """
    from pathlib import Path

    module = load_feature(MODULE)
    text = Path(module.__file__).read_text(encoding="utf-8")
    assert "from dsr.api" not in text
    assert "import dsr.api" not in text
    assert "sqlite3" not in text


# --------------------------------------------------------------------------- #
# Reading
# --------------------------------------------------------------------------- #


def test_the_summary_answers_before_anything_exists(client: TestClient):
    """An empty board is a state the page must render, not an error."""
    body = client.get(f"{PREFIX}/summary").json()
    assert body["quotes"] == 0
    assert body["total_contract_value"] == 0.0
    assert body["catalogue_available"] is False


def test_the_vocabulary_serves_the_names_the_page_needs(client: TestClient):
    """So the page cannot call a state the API does not serve."""
    body = client.get(f"{PREFIX}/vocabulary").json()
    assert body["ticket"] == "WF-086"
    assert body["statuses"] == ["draft", "published"]
    assert body["discount_types"] == ["percentage", "currency"]
    assert "line_items" in body["builtin_module_keys"]
    assert body["totals"]["total_contract_value"]
    assert body["not_implemented"], "what was deliberately not built must be reported"
    assert body["catalogue_available"] is False


def test_a_quote_created_from_a_deal_comes_back_with_its_cloned_lines(client: TestClient, deal):
    quote = _quote(client, deal, title="Northwind quote")
    assert quote["status"] == "draft"
    assert quote["account"] == "Northwind Traders"
    assert quote["currency"] == "EUR"
    assert len(quote["line_items"]) == 3
    assert quote["totals"]["subtotal"] == 30 * 4800.0 + 2400.0 + 2 * 1500.0


def test_reading_one_quote_by_id_matches_the_list(client: TestClient, deal):
    quote = _quote(client, deal)
    one = client.get(f"{PREFIX}/quotes/{quote['id']}").json()
    listed = client.get(f"{PREFIX}/quotes").json()["quotes"]
    assert one["id"] == quote["id"]
    assert [q["id"] for q in listed] == [quote["id"]]
    assert one["totals"] == listed[0]["totals"]


def test_a_quote_that_does_not_exist_answers_404(client: TestClient):
    """Not a 200 with an empty body, and not a 403 that would confirm an id."""
    response = client.get(f"{PREFIX}/quotes/no-such-quote")
    assert response.status_code == 404
    assert response.json()["error"] == "not_found"


def test_the_line_items_route_404s_for_a_quote_that_does_not_exist(client: TestClient):
    """Otherwise it answers with an empty list, which reads as a quote with no
    lines rather than as no such quote."""
    assert client.get(f"{PREFIX}/quotes/no-such-quote/line-items").status_code == 404


def test_the_catalogue_route_answers_when_there_is_no_catalogue(client: TestClient):
    """WF-087 has not shipped, so this is the normal case.

    An empty list with the flag is a correct answer the page can explain. A 404
    would look like a broken route.
    """
    body = client.get(f"{PREFIX}/catalog").json()
    assert body["catalogue_available"] is False
    assert body["products"] == []


def test_the_catalogue_route_searches_when_there_is_one(client: TestClient, live_store):
    live_store.create(
        "crm_product",
        {
            "name": "Platform, annual",
            "sku": "NWD-PLAT",
            "unit_price": 4800.0,
            "description": "Per year.",
            "tiers": [{"min_qty": 25, "unit_price": 4200.0}],
        },
        actor="test",
        source="test",
    )
    body = client.get(f"{PREFIX}/catalog", params={"term": "nwd-plat"}).json()
    assert body["catalogue_available"] is True
    assert body["products"][0]["sku"] == "NWD-PLAT"
    assert body["products"][0]["tier_count"] == 1


# --------------------------------------------------------------------------- #
# Writing
# --------------------------------------------------------------------------- #


def test_creating_a_quote_from_a_deal_that_does_not_exist_is_422_with_the_field_named(
    client: TestClient,
):
    """422, not 404 or 409: nothing about the request is a conflict, and the
    field is named so a form can put the message beside its input."""
    response = client.post(f"{PREFIX}/quotes", json={"deal_id": "nope"})
    assert response.status_code == 422
    body = response.json()
    assert body["error"] == "quote_invalid"
    assert "deal_id" in body["errors"]


def test_a_malformed_line_names_every_offending_field(client: TestClient, deal):
    """One message per field, not one sentence about a row of inputs."""
    quote = _quote(client, deal)
    response = client.post(
        f"{PREFIX}/quotes/{quote['id']}/line-items",
        json={"name": "", "quantity": -1, "tax_rate": 500},
    )
    assert response.status_code == 422
    assert set(response.json()["errors"]) == {"name", "quantity", "tax_rate"}


def test_a_line_can_be_added_and_the_quote_total_moves(client: TestClient, deal):
    quote = _quote(client, deal)
    before = client.get(f"{PREFIX}/quotes/{quote['id']}").json()["totals"]["total"]
    response = client.post(
        f"{PREFIX}/quotes/{quote['id']}/line-items",
        json={"name": "Consulting day", "quantity": 2, "unit_price": 250.0, "tax_rate": 20},
    )
    assert response.status_code == 200
    after = response.json()["quote"]
    assert after["totals"]["total"] == before + 600.0
    assert len(after["line_items"]) == 4


def test_a_line_added_to_the_wrong_quote_is_404_rather_than_a_silent_orphan(client, deal):
    """A line with a quote_id nobody can read is a row with no parent."""
    response = client.post(
        f"{PREFIX}/quotes/no-such-quote/line-items", json={"name": "X", "unit_price": 1.0}
    )
    assert response.status_code == 404


def test_changing_the_quantity_re_resolves_the_tier_over_http(client, live_store, deal):
    """The rule the research states, checked through the route a caller uses."""
    product = live_store.create(
        "crm_product",
        {
            "name": "Platform, annual",
            "sku": "PLAT",
            "unit_price": 5000.0,
            "tiers": [{"min_qty": 1, "unit_price": 5000.0}, {"min_qty": 25, "unit_price": 4200.0}],
        },
        actor="test",
        source="test",
    )
    quote = _quote(client, deal)
    added = client.post(
        f"{PREFIX}/quotes/{quote['id']}/line-items",
        json={"name": "Platform, annual", "product_id": product["id"], "quantity": 30},
    ).json()["quote"]
    line = added["line_items"][-1]
    assert line["unit_price"] == 4200.0

    below = client.patch(f"{PREFIX}/line-items/{line['id']}", json={"quantity": 24})
    assert below.status_code == 200
    assert below.json()["quote"]["line_items"][-1]["unit_price"] == 5000.0


def test_a_line_can_be_removed_and_the_total_shrinks(client: TestClient, deal):
    quote = _quote(client, deal)
    line_id = quote["line_items"][0]["id"]
    before = client.get(f"{PREFIX}/quotes/{quote['id']}").json()["totals"]["total"]
    response = client.delete(f"{PREFIX}/line-items/{line_id}")
    assert response.status_code == 200
    assert response.json()["quote"]["totals"]["total"] < before


def test_removing_a_line_that_does_not_exist_is_404(client: TestClient):
    assert client.delete(f"{PREFIX}/line-items/no-such-line").status_code == 404


def test_the_header_edits_route_changes_a_title_and_a_schedule(client: TestClient, deal):
    """A payment schedule is a fact about when money moves, so it moves the
    contract value without touching a line."""
    quote = _quote(client, deal)
    response = client.patch(
        f"{PREFIX}/quotes/{quote['id']}",
        json={
            "title": "Renamed",
            "expires_on": LATER,
            "payment_schedule": [{"due_on": LATER, "amount": 1000.0}],
        },
    )
    assert response.status_code == 200
    served = response.json()["quote"]
    assert served["title"] == "Renamed"
    assert served["expires_on"] == LATER
    assert served["totals"]["future_payments"] == 1000.0


def test_a_malformed_expiration_date_is_422(client: TestClient, deal):
    quote = _quote(client, deal)
    response = client.patch(f"{PREFIX}/quotes/{quote['id']}", json={"expires_on": "31/12/2099"})
    assert response.status_code == 422
    assert "expires_on" in response.json()["errors"]


# --------------------------------------------------------------------------- #
# Modules
# --------------------------------------------------------------------------- #


def test_a_custom_coded_module_is_409_with_a_stable_reason(client: TestClient, deal):
    """The research says the API cannot add one.

    The status is 409 rather than 422: the request is well-formed and the
    workflow's rules refuse it. ``reason`` is a token so the page does not have
    to parse the sentence.
    """
    quote = _quote(client, deal)
    response = client.patch(
        f"{PREFIX}/quotes/{quote['id']}",
        json={"modules": [{"key": "footer", "custom_coded": True}]},
    )
    assert response.status_code == 409
    body = response.json()
    assert body["error"] == "quote_conflict"
    assert body["reason"] == "custom_module_not_api_authorable"


def test_a_custom_module_the_editor_authored_is_accepted(client: TestClient, deal):
    quote = _quote(client, deal)
    response = client.patch(
        f"{PREFIX}/quotes/{quote['id']}",
        json={"modules": [{"key": "footer", "kind": "custom", "authored_via": "ui"}]},
    )
    assert response.status_code == 200
    modules = response.json()["quote"]["modules"]
    assert modules[0]["authored_via"] == "ui"


def test_hiding_and_reordering_modules_lands_in_the_order_sent(client: TestClient, deal):
    quote = _quote(client, deal)
    response = client.patch(
        f"{PREFIX}/quotes/{quote['id']}",
        json={
            "modules": [
                {"key": "totals", "visible": False},
                {"key": "header"},
                {"key": "terms", "visible": False},
            ]
        },
    )
    assert response.status_code == 200
    modules = response.json()["quote"]["modules"]
    assert [m["key"] for m in modules] == ["totals", "header", "terms"]
    assert [m["visible"] for m in modules] == [False, True, False]


# --------------------------------------------------------------------------- #
# Publish
# --------------------------------------------------------------------------- #


def test_publishing_copies_the_contract_value_onto_the_deal_amount(client, live_store, deal):
    """The write-back the research describes."""
    quote = _quote(client, deal)
    client.patch(
        f"{PREFIX}/quotes/{quote['id']}",
        json={"payment_schedule": [{"due_on": LATER, "amount": 5000.0}]},
    )
    response = client.post(f"{PREFIX}/quotes/{quote['id']}/publish")
    assert response.status_code == 200
    body = response.json()
    served = client.get(f"{PREFIX}/quotes/{quote['id']}").json()
    assert body["deal_amount_written"] == served["totals"]["total_contract_value"]
    assert live_store.get(deal["id"])["data"]["amount"] == body["deal_amount_written"]


def test_publishing_twice_is_409_with_a_stable_reason(client, deal):
    quote = _quote(client, deal)
    assert client.post(f"{PREFIX}/quotes/{quote['id']}/publish").status_code == 200
    response = client.post(f"{PREFIX}/quotes/{quote['id']}/publish")
    assert response.status_code == 409
    assert response.json()["reason"] == "already_published"


def test_publishing_an_expired_quote_is_409(client, live_store, deal):
    """A buyer cannot be held to a date that has passed, and the deal amount is
    left alone."""
    quote = _quote(client, deal, expires_on=YESTERDAY)
    response = client.post(f"{PREFIX}/quotes/{quote['id']}/publish")
    assert response.status_code == 409
    assert response.json()["reason"] == "quote_expired"
    assert live_store.get(deal["id"])["data"]["amount"] == 24000.0


def test_publishing_a_quote_with_no_lines_is_409(client, live_store):
    """Its contract value is zero, so publishing would write zero onto the deal."""
    record = live_store.create(
        "crm_deal", {"name": "Bare", "amount": 5000.0}, room_id="room-1", actor="t", source="t"
    )
    response = client.post(
        f"{PREFIX}/quotes", params={"room_id": "room-1"}, json={"deal_id": record["id"]}
    )
    quote_id = response.json()["quote"]["id"]
    publish = client.post(f"{PREFIX}/quotes/{quote_id}/publish")
    assert publish.status_code == 409
    assert publish.json()["reason"] == "quote_is_empty"
    assert live_store.get(record["id"])["data"]["amount"] == 5000.0


def test_a_published_quote_will_not_accept_a_line_change(client, deal):
    quote = _quote(client, deal)
    line_id = quote["line_items"][0]["id"]
    client.post(f"{PREFIX}/quotes/{quote['id']}/publish")
    response = client.patch(f"{PREFIX}/line-items/{line_id}", json={"quantity": 999})
    assert response.status_code == 409
    assert response.json()["reason"] == "quote_frozen"
    assert client.delete(f"{PREFIX}/line-items/{line_id}").json()["reason"] == "quote_frozen"


def test_publishing_replaces_the_deal_lines_with_rows_that_have_their_own_ids(
    client, live_store, deal
):
    """Replace, and every replacement carries a quote line id that is not its
    own record id."""
    original = {
        row["id"]
        for row in live_store.find("crm_deal_line_item", {"deal_id": deal["id"]}, limit=10)
    }
    quote = _quote(client, deal)
    quote_line_ids = {line["id"] for line in quote["line_items"]}
    response = client.post(f"{PREFIX}/quotes/{quote['id']}/publish")
    assert response.json()["replaced_deal_line_items"] == 3

    live = live_store.find("crm_deal_line_item", {"deal_id": deal["id"]}, limit=10)
    live_ids = {row["id"] for row in live}
    assert live_ids.isdisjoint(original)
    assert live_ids.isdisjoint(quote_line_ids)
    assert {row["data"]["quote_line_item_id"] for row in live} == quote_line_ids


# --------------------------------------------------------------------------- #
# Templates
# --------------------------------------------------------------------------- #


def test_a_template_is_recorded_for_the_dropdown(client: TestClient, deal):
    """And the quote picks up its module list."""
    created = client.post(
        f"{PREFIX}/templates",
        json={"name": "Standard quote", "modules": ["header", "line_items", "totals"]},
    )
    assert created.status_code == 200
    template = created.json()["template"]
    assert template["authored_via"] == "ui"

    quote = _quote(client, deal, template_id=template["id"])
    assert [m["key"] for m in quote["modules"]] == ["header", "line_items", "totals"]
    listed = client.get(f"{PREFIX}/templates").json()["templates"]
    assert [t["id"] for t in listed] == [template["id"]]


def test_a_template_without_a_name_is_422(client: TestClient):
    response = client.post(f"{PREFIX}/templates", json={"description": "no name"})
    assert response.status_code == 422
    assert "name" in response.json()["errors"]


def test_an_unknown_module_key_in_a_template_is_422(client: TestClient):
    """422 not 409: a template is authored, not in conflict with a quote."""
    response = client.post(f"{PREFIX}/templates", json={"name": "X", "modules": ["weather"]})
    assert response.status_code == 422
    assert "modules" in response.json()["errors"]


# --------------------------------------------------------------------------- #
# The audit trail
# --------------------------------------------------------------------------- #


def test_every_write_names_a_route_the_app_serves(client: TestClient, live_store, deal):
    """The guarantee the product is built on, checked against the mounted table.

    The source is derived from ``router.prefix`` at each call site, so it cannot
    name a route this feature stopped serving. Reading the rows back out of the
    audit log is the part that matters: it checks what was written, not what the
    route intended to write.
    """
    served = {
        (method, route.path)
        for route in load_feature(MODULE).router.routes
        for method in (getattr(route, "methods", None) or set())
    }
    quote = _quote(client, deal)
    client.post(
        f"{PREFIX}/quotes/{quote['id']}/line-items", json={"name": "Extra", "unit_price": 10.0}
    )
    client.post(f"{PREFIX}/quotes/{quote['id']}/publish")

    rows = live_store.audit(collection="wf086_quote", limit=50) + live_store.audit(
        collection="wf086_line_item", limit=50
    )
    assert rows, "publishing a quote writes audit rows, so the log cannot be empty"
    for row in rows:
        method, _, path = row["source"].partition(" ")
        assert (method, path) in served, row["source"]


def test_the_publish_write_row_names_the_publish_route(client: TestClient, live_store, deal):
    """The one write whose source a reviewer will go looking for."""
    quote = _quote(client, deal)
    client.post(f"{PREFIX}/quotes/{quote['id']}/publish")
    sources = {row["source"] for row in live_store.audit(collection="wf086_quote", limit=50)}
    assert f"POST {PREFIX}/quotes/{{quote_id}}/publish" in sources


# --------------------------------------------------------------------------- #
# The demo seed
# --------------------------------------------------------------------------- #


def test_the_seed_creates_the_states_the_research_names(store: RecordStore):
    """A feature whose page is empty in the demo is a feature nobody can review.

    Driven through the real engine, so the demo cannot show a shape the routes
    would not produce.
    """
    room = store.create("room", {"name": "Northwind"}, actor="test", source="test")["id"]
    module = load_feature(MODULE)
    reported = module.seed(
        store.db,
        {"room_ids": [(room, "Northwind Traders")], "now": datetime.now(timezone.utc)},
    )
    assert reported, "seed returned nothing, so the seeder prints nothing for this feature"


def test_the_seed_string_can_be_printed_on_a_windows_console(store: RecordStore):
    """Every character encodable by cp1252.

    A single U+2192 RIGHTWARDS ARROW in one recovered feature broke the entire
    seeder on a Windows console. The seeder prints this string, so this is the
    test that keeps one feature from taking the demo down.
    """
    room = store.create("room", {"name": "Northwind"}, actor="test", source="test")["id"]
    module = load_feature(MODULE)
    reported = module.seed(
        store.db,
        {"room_ids": [(room, "Northwind Traders")], "now": datetime.now(timezone.utc)},
    )
    reported.encode("cp1252")
    assert reported.isascii(), reported


def test_the_seed_leaves_a_populated_crm_mirror_alone(store: RecordStore):
    """The guard that stops this feature moving another workflow's numbers.

    A rollup over ``crm_deal`` counts rows this feature did not write. Adding a
    deal to a populated mirror to make this page look fuller would move that
    rollup, so the seed reuses the deal that is already there.
    """
    room = store.create("room", {"name": "Northwind"}, actor="test", source="test")["id"]
    store.create(
        "crm_deal",
        {"name": "Already mirrored", "amount": 100.0},
        room_id=room,
        actor="t",
        source="t",
    )
    module = load_feature(MODULE)
    reported = module.seed(
        store.db,
        {"room_ids": [(room, "Northwind Traders")], "now": datetime.now(timezone.utc)},
    )
    assert "reused from the CRM mirror" in reported
    # The property, not the message: the mirror still holds exactly the one deal
    # it held before. A rollup over ``crm_deal`` divides by those rows.
    assert len(store.list("crm_deal", limit=100)) == 1


def test_the_seed_provisions_a_deal_when_the_mirror_is_empty(store: RecordStore):
    """So the page has something to quote from on a fresh database."""
    room = store.create("room", {"name": "Northwind"}, actor="test", source="test")["id"]
    module = load_feature(MODULE)
    reported = module.seed(
        store.db,
        {"room_ids": [(room, "Northwind Traders")], "now": datetime.now(timezone.utc)},
    )
    assert "provisioned by this feature" in reported
    assert store.find("crm_deal", {}, limit=5)


def test_the_seed_returns_nothing_when_there_are_no_rooms(store: RecordStore):
    """Rather than raising, which the seeder reports as a loud failure."""
    module = load_feature(MODULE)
    assert module.seed(store.db, {"room_ids": [], "now": datetime.now(timezone.utc)}) == ""


def test_the_seed_is_reachable_through_the_seeder_s_own_walk(store: RecordStore):
    """``iter_feature_modules`` is how the seeder finds it.

    A feature that exports ``seed`` but is not returned by the walk would be
    silently absent from every demo.
    """
    found = {name for name, module, error in iter_feature_modules() if not error}
    assert MODULE in found
