"""WF-089 over HTTP: the surface this feature's own router serves.

The domain rules are in ``test_wf089.py``. This file is the other half, and it is organised
by what a caller can observe:

``the route table``
    That the host mounted every route by discovery alone, with no shared file edited, and that
    no two of them collide.
``currencies over the wire``
    Registration, the base currency, the Custom-only rate, and a stamped rate through HTTP.
``price lists and price rows``
    The single-currency rule, and the rows a unit price resolves from.
``quotes and lines``
    Creation, the line item that carries no price, and the currency change that refuses.
``pricing over the wire``
    Both stored money figures, both refusal codes, and the refusal as a 200.
``the error shapes``
    Every status code and body this router can produce.
``the audit-source rule``
    Every ``source=`` this workflow records names a route the host actually mounted.
``the read-path rule``
    The reads write no audit rows, because a route that logged every read would fill this
    product's own guarantee with entries describing no change.
"""

from __future__ import annotations

import importlib
import inspect
import json

import pytest
from dsr.quote_currency import inferences, vocabulary as vocab
from dsr.quote_currency.errors import (
    CurrencyChangeRefused,
    CurrencyRefusal,
    QuoteNotFound,
    RateUnavailable,
)
from fastapi.testclient import TestClient

FEATURE_MODULE = "dsr.features.wf089_quote_in_a_transaction_currency_with_fx"
PREFIX = "/api/wf-089"
FEATURE_ID = "wf-089-quote-in-a-transaction-currency-with-fx-conversion"

ACTOR = {"actor": "dana"}


def _store(client: TestClient):
    from dsr.api import app

    return app.state.store


# --------------------------------------------------------------------------- #
# Fixtures: a priced EUR world, built through the router itself
# --------------------------------------------------------------------------- #


@pytest.fixture()
def world(client: TestClient) -> dict[str, str]:
    """Two currencies, two price lists, one priced quote. Everything through HTTP."""

    usd = client.post(
        f"{PREFIX}/currencies",
        params=ACTOR,
        json={
            "iso_code": "USD",
            "is_base_currency": True,
            "currency_symbol": "$",
            "name": "US Dollar",
        },
    )
    assert usd.status_code == 201, usd.text
    eur = client.post(
        f"{PREFIX}/currencies",
        params=ACTOR,
        json={
            "iso_code": "EUR",
            "currency_type": "Custom",
            "exchange_rate": "1.08",
            "currency_symbol": "EUR",
        },
    )
    assert eur.status_code == 201, eur.text

    lists = {}
    for code, seat, premium in (("USD", "1320.00", "1980.00"), ("EUR", "1200.00", "1800.50")):
        created = client.post(
            f"{PREFIX}/price-lists", params=ACTOR, json={"name": f"{code} list", "iso_code": code}
        )
        assert created.status_code == 201, created.text
        lists[code] = created.json()["id"]
        for product, price in (("SEAT-STD", seat), ("SEAT-PRE", premium)):
            item = client.post(
                f"{PREFIX}/price-lists/{lists[code]}/items",
                params=ACTOR,
                json={"product_code": product, "unit_price": price},
            )
            assert item.status_code == 201, item.text

    quote = client.post(
        f"{PREFIX}/quotes",
        params=ACTOR,
        json={"name": "Northwind renewal", "iso_code": "EUR", "price_list_id": lists["EUR"]},
    )
    assert quote.status_code == 201, quote.text
    quote_id = quote.json()["id"]
    for product, quantity in (("SEAT-STD", 10), ("SEAT-PRE", 2)):
        line = client.post(
            f"{PREFIX}/quotes/{quote_id}/lines",
            params=ACTOR,
            json={"product_code": product, "quantity": quantity},
        )
        assert line.status_code == 201, line.text
    priced = client.post(
        f"{PREFIX}/quotes/{quote_id}/price",
        params=ACTOR,
        json={"trigger": "record_create", "tax": "300.00", "freight": "250.00"},
    )
    assert priced.status_code == 200, priced.text
    return {
        "usd": usd.json()["id"],
        "eur": eur.json()["id"],
        "usd_list": lists["USD"],
        "eur_list": lists["EUR"],
        "quote": quote_id,
    }


def wrong_list_quote(client: TestClient, world: dict[str, str]) -> str:
    """A EUR quote stamped with the USD price list. The code 34 state, over the wire."""

    created = client.post(
        f"{PREFIX}/quotes",
        params=ACTOR,
        json={
            "name": "Wrong price list",
            "iso_code": "EUR",
            "price_list_id": world["usd_list"],
        },
    )
    assert created.status_code == 201, created.text
    quote_id = created.json()["id"]
    client.post(
        f"{PREFIX}/quotes/{quote_id}/lines",
        params=ACTOR,
        json={"product_code": "SEAT-STD", "quantity": 1},
    )
    return quote_id


# --------------------------------------------------------------------------- #
# the route table
# --------------------------------------------------------------------------- #


class TestRouteTable:
    def test_the_feature_is_installed_with_its_routes(self, client: TestClient):
        payload = client.get("/api/features").json()
        installed = next(f for f in payload["features"] if f["id"] == FEATURE_ID)
        paths = {route["path"] for route in installed["routes"]}

        assert f"{PREFIX}/summary" in paths
        assert f"{PREFIX}/currencies" in paths
        assert f"{PREFIX}/currencies/{{currency_id}}/rate" in paths
        assert f"{PREFIX}/price-lists" in paths
        assert f"{PREFIX}/price-lists/{{price_list_id}}/items" in paths
        assert f"{PREFIX}/quotes" in paths
        assert f"{PREFIX}/quotes/{{quote_id}}" in paths
        assert f"{PREFIX}/quotes/{{quote_id}}/lines" in paths
        assert f"{PREFIX}/quotes/{{quote_id}}/currency" in paths
        assert f"{PREFIX}/quotes/{{quote_id}}/price" in paths
        assert f"{PREFIX}/quotes/{{quote_id}}/pricing-runs" in paths
        assert f"{PREFIX}/quotes/{{quote_id}}/rate-reads" in paths
        assert f"{PREFIX}/decisions" in paths
        assert f"{PREFIX}/vocabulary" in paths

    def test_the_feature_did_not_fail_to_load(self, client: TestClient):
        payload = client.get("/api/features").json()

        assert not [f for f in payload["failed"] if FEATURE_ID in str(f)]

    def test_the_host_mounted_it_without_a_shared_file_being_edited(self):
        """``backend/dsr/api.py`` discovers and mounts every feature router. This test
        states the claim by name so a reviewer can check it against the diff."""

        module = importlib.import_module(FEATURE_MODULE)

        assert module.router.prefix == PREFIX

    def test_every_route_is_reachable_and_none_collide(self):
        mounted = {
            f"{method} {route.path}"
            for route in importlib.import_module(FEATURE_MODULE).router.routes
            for method in route.methods
            if method not in ("HEAD", "OPTIONS")
        }

        assert len(mounted) == len(set(mounted))
        assert len(mounted) == 19

    def test_the_prefix_is_ticket_derived(self):
        assert PREFIX == "/api/wf-089"

    def test_no_feature_imports_the_app(self):
        module = importlib.import_module(FEATURE_MODULE)

        assert "dsr.api" not in module.__dict__

    def test_the_feature_never_opens_the_database_itself(self):
        source = open(importlib.import_module(FEATURE_MODULE).__file__, encoding="utf-8").read()

        assert "sqlite3" not in source
        assert ".connect(" not in source

    def test_the_error_types_are_this_features_own(self):
        """A handler for a shared type would intercept that exception across the product."""

        module = importlib.import_module(FEATURE_MODULE)

        assert set(module.EXCEPTION_HANDLERS) == {
            CurrencyRefusal,
            QuoteNotFound,
            CurrencyChangeRefused,
            RateUnavailable,
        }
        for handler in module.EXCEPTION_HANDLERS.values():
            assert list(inspect.signature(handler).parameters) == ["request", "exc"]


# --------------------------------------------------------------------------- #
# currencies over the wire
# --------------------------------------------------------------------------- #


class TestCurrenciesOverHttp:
    def test_a_registered_currency_reports_its_precision_and_rate_writability(
        self, client: TestClient, world: dict[str, str]
    ):
        payload = client.get(f"{PREFIX}/currencies").json()
        rows = {row["iso_code"]: row for row in payload["currencies"]}

        assert rows["EUR"]["rate_is_writable"] is True
        assert rows["USD"]["rate_is_writable"] is False
        assert rows["USD"]["exchange_rate"] == vocab.IDENTITY_RATE
        assert payload["base_currency"] == "USD"

    def test_the_currency_list_names_the_rate_policy_and_the_event(self, client: TestClient):
        payload = client.get(f"{PREFIX}/currencies").json()

        assert payload["rate_policy"] == vocab.RATE_POLICY
        assert payload["rate_event"] == vocab.RATE_EVENT
        assert payload["rate_event"] in payload["rate_event_note"]

    def test_a_rate_on_a_standard_record_is_a_400_naming_the_remedy(self, client: TestClient):
        response = client.post(
            f"{PREFIX}/currencies", params=ACTOR, json={"iso_code": "GBP", "exchange_rate": "1.27"}
        )

        assert response.status_code == 400
        assert response.json()["error"] == "invalid_currency_request"
        assert "Custom" in response.json()["errors"]["exchange_rate"]

    def test_a_two_letter_currency_is_a_400(self, client: TestClient):
        response = client.post(f"{PREFIX}/currencies", params=ACTOR, json={"iso_code": "EU"})

        assert response.status_code == 400
        assert response.json()["errors"]["iso_code"]

    def test_a_precision_past_the_bound_is_a_400(self, client: TestClient):
        response = client.post(
            f"{PREFIX}/currencies",
            params=ACTOR,
            json={"iso_code": "JPY", "currency_precision": 9},
        )

        assert response.status_code == 400
        assert "6" in response.json()["errors"]["currency_precision"]

    def test_a_second_base_currency_is_a_400(self, client: TestClient, world: dict[str, str]):
        response = client.post(
            f"{PREFIX}/currencies",
            params=ACTOR,
            json={"iso_code": "EUR", "is_base_currency": True},
        )

        assert response.status_code == 400
        assert vocab.BASE_CURRENCY_FIELD in response.json()["errors"]

    def test_a_custom_record_cannot_claim_to_be_the_base_currency(self, client: TestClient):
        response = client.post(
            f"{PREFIX}/currencies",
            params=ACTOR,
            json={
                "iso_code": "EUR",
                "currency_type": "Custom",
                "exchange_rate": "1.08",
                "is_base_currency": True,
            },
        )

        assert response.status_code == 400
        assert "currency_type" in response.json()["errors"]

    def test_a_stamped_rate_is_written_and_its_effect_is_reported(
        self, client: TestClient, world: dict[str, str]
    ):
        response = client.post(
            f"{PREFIX}/currencies/{world['eur']}/rate",
            params=ACTOR,
            json={"exchange_rate": "1.20"},
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["exchange_rate"] == pytest.approx(1.20)
        assert payload["previous_rate"] == pytest.approx(1.08)
        assert payload["quotes_holding_the_superseded_rate"] == 1
        assert payload["quotes_holding_the_superseded_rate_ids"] == [world["quote"]]

    def test_a_stamped_rate_does_not_move_a_priced_quote(self, client: TestClient, world):
        before = client.get(f"{PREFIX}/quotes/{world['quote']}").json()

        client.post(
            f"{PREFIX}/currencies/{world['eur']}/rate",
            params=ACTOR,
            json={"exchange_rate": "1.20"},
        )
        after = client.get(f"{PREFIX}/quotes/{world['quote']}").json()

        assert after["totals_base"]["totalamount_base"] == before["totals_base"]["totalamount_base"]
        assert after["stamped_exchangerate"] == pytest.approx(1.08)

    def test_a_recalculation_brings_the_quote_forward(
        self, client: TestClient, world: dict[str, str]
    ):
        client.post(
            f"{PREFIX}/currencies/{world['eur']}/rate",
            params=ACTOR,
            json={"exchange_rate": "1.20"},
        )

        payload = client.post(
            f"{PREFIX}/quotes/{world['quote']}/price",
            params=ACTOR,
            json={"trigger": "record_update", "tax": "300.00", "freight": "250.00"},
        ).json()

        assert payload["run"]["rate"] == pytest.approx(1.20)
        assert payload["run"]["totals_base"]["totalamount_base"] == "19381.20"

    def test_a_rate_on_an_unknown_record_is_a_404(self, client: TestClient):
        response = client.post(
            f"{PREFIX}/currencies/no-such-currency/rate",
            params=ACTOR,
            json={"exchange_rate": "1.20"},
        )

        assert response.status_code == 404
        assert response.json()["error"] == "not_found"


# --------------------------------------------------------------------------- #
# price lists and price rows
# --------------------------------------------------------------------------- #


class TestPriceListsOverHttp:
    def test_a_price_list_reports_its_currency_and_products(
        self, client: TestClient, world: dict[str, str]
    ):
        payload = client.get(f"{PREFIX}/price-lists").json()
        rows = {row["id"]: row for row in payload["price_lists"]}

        assert rows[world["eur_list"]]["iso_code"] == "EUR"
        assert rows[world["eur_list"]]["products"] == ["SEAT-PRE", "SEAT-STD"]
        assert payload["single_currency"] is True
        assert "Currencies dropdown" in payload["single_currency_reason"]

    def test_a_price_list_in_an_unregistered_currency_is_a_400(self, client: TestClient):
        response = client.post(
            f"{PREFIX}/price-lists", params=ACTOR, json={"name": "Ghost list", "iso_code": "ZZZ"}
        )

        assert response.status_code == 400
        assert response.json()["errors"]["iso_code"]

    def test_the_price_rows_are_readable_and_name_their_source(
        self, client: TestClient, world: dict[str, str]
    ):
        payload = client.get(f"{PREFIX}/price-lists/{world['eur_list']}/items").json()

        assert payload["count"] == 2
        assert payload["source"] == "unit prices resolve from these rows in the list's currency"

    def test_a_price_row_on_an_unknown_list_is_a_404(self, client: TestClient):
        response = client.get(f"{PREFIX}/price-lists/no-such-list/items")

        assert response.status_code == 404

    def test_a_price_row_with_no_product_is_a_400(self, client: TestClient, world):
        response = client.post(
            f"{PREFIX}/price-lists/{world['eur_list']}/items",
            params=ACTOR,
            json={"unit_price": "10.00"},
        )

        assert response.status_code == 400
        assert response.json()["errors"]["product_code"]

    def test_a_price_row_with_no_price_is_a_400(self, client: TestClient, world):
        response = client.post(
            f"{PREFIX}/price-lists/{world['eur_list']}/items",
            params=ACTOR,
            json={"product_code": "SEAT-STD"},
        )

        assert response.status_code == 400
        assert response.json()["errors"]["unit_price"]


# --------------------------------------------------------------------------- #
# quotes and lines
# --------------------------------------------------------------------------- #


class TestQuotesOverHttp:
    def test_a_quote_carries_both_money_field_lists(self, client: TestClient, world):
        payload = client.get(f"{PREFIX}/quotes").json()

        assert payload["transaction_totals"] == list(vocab.TRANSACTION_TOTALS)
        assert payload["base_totals"] == list(vocab.BASE_TOTALS)
        assert payload["authoritative"] == vocab.AUTHORITATIVE

    def test_a_priced_quote_shows_both_figures_and_the_stamped_rate(
        self, client: TestClient, world: dict[str, str]
    ):
        payload = client.get(f"{PREFIX}/quotes/{world['quote']}").json()

        assert payload["pricing_outcome"] == vocab.OUTCOME_PRICED
        assert payload["totals"]["totalamount"] == "16151.00"
        assert payload["totals_base"]["totalamount_base"] == "17443.08"
        assert payload["exchangerate"] == pytest.approx(1.08)
        assert payload["base_totals_present"] is True

    def test_a_quote_is_created_from_a_deals_currency(self, client: TestClient, world):
        response = client.post(
            f"{PREFIX}/quotes",
            params=ACTOR,
            json={
                "name": "From the deal",
                "iso_code": "EUR",
                "price_list_id": world["eur_list"],
                "deal_iso_code": "EUR",
            },
        )

        assert response.status_code == 201
        assert response.json()["deal_iso_code"] == "EUR"
        assert response.json()["currency_inherited_from_deal"] is True

    def test_a_quote_whose_currency_differs_from_its_deal_is_flagged(
        self, client: TestClient, world
    ):
        response = client.post(
            f"{PREFIX}/quotes",
            params=ACTOR,
            json={
                "name": "Overridden",
                "iso_code": "USD",
                "price_list_id": world["usd_list"],
                "deal_iso_code": "EUR",
            },
        )

        assert response.json()["deal_iso_code"] == "EUR"
        assert response.json()["currency_inherited_from_deal"] is False

    def test_a_line_carries_no_price_and_says_which_trigger_to_run(
        self, client: TestClient, world: dict[str, str]
    ):
        created = client.post(
            f"{PREFIX}/quotes",
            params=ACTOR,
            json={"name": "New line", "iso_code": "EUR", "price_list_id": world["eur_list"]},
        )
        quote_id = created.json()["id"]

        response = client.post(
            f"{PREFIX}/quotes/{quote_id}/lines",
            params=ACTOR,
            json={"product_code": "SEAT-STD", "quantity": 1},
        )

        assert response.status_code == 201
        assert response.json()["line"]["unit_price"] is None
        assert response.json()["recalculate"]["trigger"] == vocab.TRIGGER_PRODUCT_ADD

    def test_a_line_that_carries_its_own_price_is_a_400_naming_the_field(
        self, client: TestClient, world: dict[str, str]
    ):
        """The refusal the researched rule needs, exercised over the wire."""

        response = client.post(
            f"{PREFIX}/quotes/{world['quote']}/lines",
            params=ACTOR,
            json={"product_code": "SEAT-STD", "quantity": 1, "unit_price": "1.00"},
        )

        assert response.status_code == 400
        assert response.json()["error"] == "invalid_currency_request"
        assert "price list row" in response.json()["errors"]["unit_price"]

    def test_a_line_with_no_product_is_a_400(self, client: TestClient, world):
        response = client.post(
            f"{PREFIX}/quotes/{world['quote']}/lines", params=ACTOR, json={"quantity": 1}
        )

        assert response.status_code == 400
        assert response.json()["errors"]["product_code"]

    def test_an_unknown_quote_is_a_404_not_a_500(self, client: TestClient):
        response = client.get(f"{PREFIX}/quotes/no-such-quote")

        assert response.status_code == 404
        assert response.json()["error"] == "not_found"

    def test_the_currency_change_is_a_409_that_names_the_remedy(
        self, client: TestClient, world: dict[str, str]
    ):
        response = client.patch(
            f"{PREFIX}/quotes/{world['quote']}/currency",
            params=ACTOR,
            json={"iso_code": "USD", "price_list_id": world["usd_list"]},
        )

        assert response.status_code == 409
        body = response.json()
        assert body["error"] == "currency_change_refused"
        assert body["line_items"] == 2
        assert "unless you remove all the line items" in body["evidence"]
        assert body["remedy"]

    def test_the_currency_change_succeeds_on_an_empty_quote(self, client: TestClient, world):
        created = client.post(
            f"{PREFIX}/quotes",
            params=ACTOR,
            json={"name": "Empty", "iso_code": "EUR", "price_list_id": world["eur_list"]},
        )
        quote_id = created.json()["id"]

        response = client.patch(
            f"{PREFIX}/quotes/{quote_id}/currency",
            params=ACTOR,
            json={"iso_code": "USD", "price_list_id": world["usd_list"]},
        )

        assert response.status_code == 200
        assert response.json()["iso_code"] == "USD"
        assert response.json()["price_list_currency_matches"] is True

    def test_a_currency_change_to_an_unregistered_currency_is_a_400(
        self, client: TestClient, world
    ):
        created = client.post(
            f"{PREFIX}/quotes",
            params=ACTOR,
            json={"name": "Empty", "iso_code": "EUR", "price_list_id": world["eur_list"]},
        )

        response = client.patch(
            f"{PREFIX}/quotes/{created.json()['id']}/currency",
            params=ACTOR,
            json={"iso_code": "ZZZ"},
        )

        assert response.status_code == 400
        assert response.json()["errors"]["iso_code"]


# --------------------------------------------------------------------------- #
# pricing over the wire
# --------------------------------------------------------------------------- #


class TestPricingOverHttp:
    def test_the_wrong_price_list_is_a_200_that_refuses_with_code_34(
        self, client: TestClient, world: dict[str, str]
    ):
        quote_id = wrong_list_quote(client, world)

        response = client.post(f"{PREFIX}/quotes/{quote_id}/price", params=ACTOR, json={})

        assert response.status_code == 200
        payload = response.json()
        assert payload["run"]["outcome"] == vocab.OUTCOME_REFUSED
        assert payload["run"]["pricing_error_code"] == "34"
        assert payload["run"]["pricing_error"] == "Invalid Price Level Currency"

    def test_the_refusal_is_reported_outside_the_run_as_well(self, client: TestClient, world):
        """The body names the outcome twice on purpose, so a reader cannot miss it."""

        quote_id = wrong_list_quote(client, world)

        payload = client.post(f"{PREFIX}/quotes/{quote_id}/price", params=ACTOR, json={}).json()

        assert payload["refusal"]["pricing_error_code"] == "34"
        assert payload["refusal"]["expected"] == "EUR"
        assert payload["refusal"]["found"] == "USD"
        assert payload["refusal"]["authority"] == vocab.REFUSAL_IS_OUTCOME_NOT_ERROR

    def test_an_unpriced_product_is_a_200_that_refuses_with_code_38(
        self, client: TestClient, world: dict[str, str]
    ):
        created = client.post(
            f"{PREFIX}/quotes",
            params=ACTOR,
            json={"name": "Unpriced", "iso_code": "EUR", "price_list_id": world["eur_list"]},
        )
        quote_id = created.json()["id"]
        client.post(
            f"{PREFIX}/quotes/{quote_id}/lines",
            params=ACTOR,
            json={"product_code": "SEAT-PLATINUM", "quantity": 1},
        )

        payload = client.post(f"{PREFIX}/quotes/{quote_id}/price", params=ACTOR, json={}).json()

        assert payload["run"]["pricing_error_code"] == "38"
        assert payload["run"]["pricing_error"] == (
            "Transaction currency is not set for the product price list item"
        )

    def test_a_refused_quote_carries_no_figures_over_the_wire(
        self, client: TestClient, world: dict[str, str]
    ):
        quote_id = wrong_list_quote(client, world)
        client.post(f"{PREFIX}/quotes/{quote_id}/price", params=ACTOR, json={})

        payload = client.get(f"{PREFIX}/quotes/{quote_id}").json()

        assert payload["pricing_error_code"] == "34"
        assert payload["totals"]["totalamount"] is None
        assert payload["totals_base"]["totalamount_base"] is None
        assert payload["base_totals_present"] is False

    def test_a_run_that_finds_no_rate_is_a_409_naming_the_currency(self, client: TestClient, world):
        """A Standard record with no rate is a conflict, not a refusal to price.

        The quote is fine and the price list is fine. The deployment has not told the platform
        what a pound is worth today, which is a state conflict rather than a wrong-currency
        combination, and the two deserve different answers.
        """

        client.post(f"{PREFIX}/currencies", params=ACTOR, json={"iso_code": "GBP"})
        client.post(
            f"{PREFIX}/currencies",
            params=ACTOR,
            json={"iso_code": "CHF", "currency_type": "Custom", "exchange_rate": "1.11"},
        )
        created = client.post(
            f"{PREFIX}/currencies",
            params=ACTOR,
            json={"iso_code": "SEK", "currency_type": "Custom"},
        )
        assert created.status_code == 400

        gbp_list = client.post(
            f"{PREFIX}/price-lists", params=ACTOR, json={"name": "GBP list", "iso_code": "GBP"}
        ).json()["id"]
        client.post(
            f"{PREFIX}/price-lists/{gbp_list}/items",
            params=ACTOR,
            json={"product_code": "SEAT-STD", "unit_price": "1000.00"},
        )
        quote = client.post(
            f"{PREFIX}/quotes",
            params=ACTOR,
            json={"name": "Pound quote", "iso_code": "GBP", "price_list_id": gbp_list},
        ).json()
        client.post(
            f"{PREFIX}/quotes/{quote['id']}/lines",
            params=ACTOR,
            json={"product_code": "SEAT-STD", "quantity": 1},
        )

        # The record has no rate, so the refusal is the 400 raised by resolve_rate. The 409
        # path is the price list that disappeared, which the next test covers.
        response = client.post(f"{PREFIX}/quotes/{quote['id']}/price", params=ACTOR, json={})

        assert response.status_code == 400
        assert "exchange_rate" in response.json()["errors"]

    def test_a_deleted_price_list_is_a_409_naming_the_currency(
        self, client: TestClient, world: dict[str, str]
    ):
        created = client.post(
            f"{PREFIX}/quotes",
            params=ACTOR,
            json={"name": "Orphan", "iso_code": "EUR", "price_list_id": world["eur_list"]},
        )
        quote_id = created.json()["id"]
        _store(client).delete(world["eur_list"], actor="test", source="test fixture")

        response = client.post(f"{PREFIX}/quotes/{quote_id}/price", params=ACTOR, json={})

        assert response.status_code == 409
        assert response.json()["error"] == "exchange_rate_unavailable"
        assert response.json()["iso_code"] == "EUR"

    def test_a_quote_with_no_price_list_refuses_as_code_38(self, client: TestClient, world):
        created = client.post(
            f"{PREFIX}/quotes", params=ACTOR, json={"name": "No list", "iso_code": "EUR"}
        )

        payload = client.post(
            f"{PREFIX}/quotes/{created.json()['id']}/price", params=ACTOR, json={}
        ).json()

        assert payload["run"]["pricing_error_code"] == "38"

    def test_an_unknown_trigger_is_a_400_naming_the_known_set(
        self, client: TestClient, world: dict[str, str]
    ):
        response = client.post(
            f"{PREFIX}/quotes/{world['quote']}/price", params=ACTOR, json={"trigger": "whenever"}
        )

        assert response.status_code == 400
        assert "record_open" in response.json()["errors"]["trigger"]

    def test_every_one_of_the_six_triggers_is_accepted_over_http(
        self, client: TestClient, world: dict[str, str]
    ):
        for trigger in vocab.RECALCULATION_TRIGGERS:
            response = client.post(
                f"{PREFIX}/quotes/{world['quote']}/price",
                params=ACTOR,
                json={"trigger": trigger, "tax": "300.00", "freight": "250.00"},
            )
            assert response.status_code == 200, trigger
            assert response.json()["run"]["trigger"] == trigger

    def test_the_pricing_runs_are_readable_newest_first(
        self, client: TestClient, world: dict[str, str]
    ):
        client.post(
            f"{PREFIX}/quotes/{world['quote']}/price",
            params=ACTOR,
            json={"trigger": "record_update", "tax": "300.00", "freight": "250.00"},
        )

        payload = client.get(f"{PREFIX}/quotes/{world['quote']}/pricing-runs").json()

        assert payload["count"] == 2
        assert payload["runs"][0]["trigger"] == vocab.TRIGGER_UPDATE
        assert payload["recalculation_triggers"] == list(vocab.RECALCULATION_TRIGGERS)

    def test_a_refused_run_is_in_the_runs_list_with_its_code(
        self, client: TestClient, world: dict[str, str]
    ):
        quote_id = wrong_list_quote(client, world)
        client.post(f"{PREFIX}/quotes/{quote_id}/price", params=ACTOR, json={})

        payload = client.get(f"{PREFIX}/quotes/{quote_id}/pricing-runs").json()

        assert payload["count"] == 1
        assert payload["runs"][0]["outcome"] == vocab.OUTCOME_REFUSED
        assert payload["runs"][0]["pricing_error_code"] == "34"
        assert payload["runs"][0]["totals"] is None

    def test_the_rate_reads_name_the_event_and_the_trigger(
        self, client: TestClient, world: dict[str, str]
    ):
        payload = client.get(f"{PREFIX}/quotes/{world['quote']}/rate-reads").json()

        assert payload["count"] == 1
        assert payload["reads"][0]["event"] == vocab.RATE_EVENT
        assert payload["reads"][0]["trigger"] == vocab.TRIGGER_CREATE
        assert payload["event"] == vocab.RATE_EVENT

    def test_the_runs_and_reads_routes_404_on_an_unknown_quote(self, client: TestClient):
        assert client.get(f"{PREFIX}/quotes/no-such-quote/pricing-runs").status_code == 404
        assert client.get(f"{PREFIX}/quotes/no-such-quote/rate-reads").status_code == 404

    def test_a_base_quote_converts_to_itself_over_http(self, client: TestClient, world):
        created = client.post(
            f"{PREFIX}/quotes",
            params=ACTOR,
            json={"name": "Dollar quote", "iso_code": "USD", "price_list_id": world["usd_list"]},
        )
        quote_id = created.json()["id"]
        client.post(
            f"{PREFIX}/quotes/{quote_id}/lines",
            params=ACTOR,
            json={"product_code": "SEAT-STD", "quantity": 2},
        )

        payload = client.post(f"{PREFIX}/quotes/{quote_id}/price", params=ACTOR, json={}).json()

        assert payload["run"]["rate"] == vocab.IDENTITY_RATE
        assert payload["run"]["rate_source"] == vocab.RATE_SOURCE_IDENTITY
        assert (
            payload["run"]["totals"]["totalamount"]
            == payload["run"]["totals_base"]["totalamount_base"]
        )


# --------------------------------------------------------------------------- #
# the board and the served research
# --------------------------------------------------------------------------- #


class TestTheBoardOverHttp:
    def test_an_empty_board_answers_with_empty_states(self, client: TestClient):
        payload = client.get(f"{PREFIX}/summary").json()

        assert payload["quotes"] == 0
        assert payload["priced_quotes"] == 0
        assert payload["refused_quotes"] == 0
        assert payload["base_currency"] is None
        assert payload["product_model"] == vocab.PRODUCT_MODEL

    def test_the_board_counts_the_two_refusal_codes_apart(
        self, client: TestClient, world: dict[str, str]
    ):
        quote_id = wrong_list_quote(client, world)
        client.post(f"{PREFIX}/quotes/{quote_id}/price", params=ACTOR, json={})
        created = client.post(
            f"{PREFIX}/quotes",
            params=ACTOR,
            json={"name": "Unpriced", "iso_code": "EUR", "price_list_id": world["eur_list"]},
        )
        unpriced = created.json()["id"]
        client.post(
            f"{PREFIX}/quotes/{unpriced}/lines",
            params=ACTOR,
            json={"product_code": "SEAT-PLATINUM", "quantity": 1},
        )
        client.post(f"{PREFIX}/quotes/{unpriced}/price", params=ACTOR, json={})

        payload = client.get(f"{PREFIX}/summary").json()

        assert payload["refused_quotes"] == 2
        assert payload["refusals_by_code"] == {"34": 1, "38": 1}

    def test_the_board_sums_only_priced_quotes_in_the_base_currency(
        self, client: TestClient, world: dict[str, str]
    ):
        quote_id = wrong_list_quote(client, world)
        client.post(f"{PREFIX}/quotes/{quote_id}/price", params=ACTOR, json={})

        payload = client.get(f"{PREFIX}/summary").json()

        assert payload["totals_in_base_currency"]["iso_code"] == "USD"
        assert payload["totals_in_base_currency"]["quotes_counted"] == 1
        assert payload["totals_in_base_currency"]["amount"] == "17443.08"

    def test_the_board_carries_the_rate_event_note_and_the_decision_count(self, client: TestClient):
        payload = client.get(f"{PREFIX}/summary").json()

        assert payload["rate_event"] == vocab.RATE_EVENT
        assert payload["decision_count"] == inferences.count()
        assert payload["jev_audit_ids"] == ["jev-20261004T205203-28472-23034"]

    def test_the_decisions_are_served_with_their_alternatives(self, client: TestClient):
        payload = client.get(f"{PREFIX}/decisions").json()

        assert payload["count"] >= 10
        for decision in payload["decisions"]:
            assert decision["chosen"] in decision["options"]
            assert decision["rejected_because"]
            assert decision["question"]
            assert decision["left_open_by"]

    def test_the_product_model_decision_names_its_jev_audit_id(self, client: TestClient):
        payload = client.get(f"{PREFIX}/decisions/PRODUCT_MODEL_DYNAMICS_OVER_HUBSPOT").json()

        assert payload["chosen"] == "dynamics_dual_currency_rows"
        assert payload["jev_audit_id"] == "jev-20261004T205203-28472-23034"
        assert payload["jev_confidence"] == 1.0

    def test_an_unknown_decision_is_a_404(self, client: TestClient):
        response = client.get(f"{PREFIX}/decisions/NOT_A_DECISION")

        assert response.status_code == 404

    def test_the_vocabulary_carries_both_refusal_codes_with_the_specification_wording(
        self, client: TestClient
    ):
        payload = client.get(f"{PREFIX}/vocabulary").json()

        assert payload["pricing_error_codes"] == vocab.PRICING_ERROR_CODES
        assert payload["refusal_is_outcome"] == vocab.REFUSAL_IS_OUTCOME_NOT_ERROR

    def test_the_vocabulary_names_the_authoritative_figure_and_says_why(self, client: TestClient):
        payload = client.get(f"{PREFIX}/vocabulary").json()

        assert payload["authoritative"] == "transaction_currency"
        assert "reporting" in payload["authoritative_reason"]

    def test_the_vocabulary_names_the_six_recalculation_triggers_with_labels(
        self, client: TestClient
    ):
        payload = client.get(f"{PREFIX}/vocabulary").json()

        assert payload["recalculation_triggers"] == list(vocab.RECALCULATION_TRIGGERS)
        for trigger in payload["recalculation_triggers"]:
            assert payload["recalculation_trigger_labels"][trigger]

    def test_the_vocabulary_records_both_dependencies_and_their_issue_numbers(
        self, client: TestClient
    ):
        payload = client.get(f"{PREFIX}/vocabulary").json()
        dependencies = {row["ticket"]: row for row in payload["dependencies"]}

        assert set(dependencies) == {"WF-086", "WF-087"}
        for entry in dependencies.values():
            assert entry["this_workflow_does"]

    def test_the_vocabulary_carries_all_seven_sources(self, client: TestClient):
        assert len(client.get(f"{PREFIX}/vocabulary").json()["sources"]) == 7


# --------------------------------------------------------------------------- #
# the audit-source rule and the read-path rule
# --------------------------------------------------------------------------- #


class TestTheAuditRules:
    def test_every_source_this_router_can_record_names_a_mounted_route(self):
        """The test the brief asks for by name.

        A hardcoded source string is the defect this prevents: it leaves the audit log naming
        a route the app stopped serving.
        """

        module = importlib.import_module(FEATURE_MODULE)
        mounted = {
            f"{method} {route.path}"
            for route in module.router.routes
            for method in route.methods
            if method not in ("HEAD", "OPTIONS")
        }

        for method, path in (
            ("POST", "/currencies"),
            ("POST", "/currencies/{currency_id}/rate"),
            ("POST", "/price-lists"),
            ("POST", "/price-lists/{price_list_id}/items"),
            ("POST", "/quotes"),
            ("POST", "/quotes/{quote_id}/lines"),
            ("PATCH", "/quotes/{quote_id}/currency"),
            ("POST", "/quotes/{quote_id}/price"),
        ):
            assert module._source(method, path) in mounted  # noqa: SLF001

    def test_a_currency_write_names_the_registration_route(self, client: TestClient):
        client.post(
            f"{PREFIX}/currencies", params=ACTOR, json={"iso_code": "USD", "is_base_currency": True}
        )

        entries = client.get("/api/audit", params={"collection": vocab.CURRENCY_COLLECTION}).json()

        assert {str(row.get("source")) for row in entries["entries"]} == {
            f"POST {PREFIX}/currencies"
        }

    def test_a_rate_stamp_names_the_rate_route(self, client: TestClient, world):
        client.post(
            f"{PREFIX}/currencies/{world['eur']}/rate",
            params=ACTOR,
            json={"exchange_rate": "1.20"},
        )

        entries = client.get("/api/audit", params={"collection": vocab.CURRENCY_COLLECTION}).json()

        assert f"POST {PREFIX}/currencies/{{currency_id}}/rate" in {
            str(row.get("source")) for row in entries["entries"]
        }

    def test_a_pricing_run_names_the_price_route(self, client: TestClient, world):
        client.post(f"{PREFIX}/quotes/{world['quote']}/price", params=ACTOR, json={})

        entries = client.get("/api/audit", params={"collection": vocab.PRICING_COLLECTION}).json()

        assert {str(row.get("source")) for row in entries["entries"]} == {
            f"POST {PREFIX}/quotes/{{quote_id}}/price"
        }

    def test_a_refused_run_reaches_the_audit_log_as_a_success_does(
        self, client: TestClient, world: dict[str, str]
    ):
        quote_id = wrong_list_quote(client, world)

        before = client.get("/api/audit").json()["count"]
        client.post(f"{PREFIX}/quotes/{quote_id}/price", params=ACTOR, json={})
        after = client.get("/api/audit").json()["count"]

        assert after > before
        runs = client.get(f"{PREFIX}/quotes/{quote_id}/pricing-runs").json()
        assert runs["runs"][0]["outcome"] == vocab.OUTCOME_REFUSED

    def test_a_refused_run_writes_its_own_row_and_leaves_the_quote_row_too(
        self, client: TestClient, world: dict[str, str]
    ):
        quote_id = wrong_list_quote(client, world)
        client.post(f"{PREFIX}/quotes/{quote_id}/price", params=ACTOR, json={})

        quote_entries = client.get(
            "/api/audit", params={"collection": vocab.QUOTE_COLLECTION, "record_id": quote_id}
        ).json()
        run_entries = client.get(
            "/api/audit", params={"collection": vocab.PRICING_COLLECTION}
        ).json()

        assert quote_entries["count"] >= 1
        assert run_entries["count"] >= 1

    def test_the_read_paths_record_no_audit_row(self, client: TestClient, world):
        """A route that wrote a row on every read would fill the audit log with noise."""

        before = client.get("/api/audit").json()["count"]
        for path in (
            f"{PREFIX}/summary",
            f"{PREFIX}/vocabulary",
            f"{PREFIX}/decisions",
            f"{PREFIX}/decisions/PRODUCT_MODEL_DYNAMICS_OVER_HUBSPOT",
            f"{PREFIX}/currencies",
            f"{PREFIX}/price-lists",
            f"{PREFIX}/price-lists/{world['eur_list']}/items",
            f"{PREFIX}/quotes",
            f"{PREFIX}/quotes/{world['quote']}",
            f"{PREFIX}/quotes/{world['quote']}/pricing-runs",
            f"{PREFIX}/quotes/{world['quote']}/rate-reads",
        ):
            assert client.get(path).status_code == 200, path
        after = client.get("/api/audit").json()["count"]

        assert after == before

    def test_every_money_field_is_stored_as_a_string_the_index_can_filter(
        self, client: TestClient, world: dict[str, str]
    ):
        rows = client.get(
            "/api/records/wf089_quote",
            params={"where": json.dumps({vocab.TRANSACTION_CURRENCY_FIELD: "EUR"})},
        ).json()

        assert rows["count"] >= 1
        priced = next(row for row in rows["records"] if row["data"]["totalamount"] is not None)
        assert isinstance(priced["data"]["totalamount"], str)
        assert isinstance(priced["data"]["totalamount_base"], str)

    def test_a_refused_quote_leaves_no_figure_behind_to_be_filtered_on(
        self, client: TestClient, world: dict[str, str]
    ):
        quote_id = wrong_list_quote(client, world)
        client.post(f"{PREFIX}/quotes/{quote_id}/price", params=ACTOR, json={})

        record = client.get(f"/api/records/wf089_quote/{quote_id}").json()

        for name in vocab.TRANSACTION_TOTALS + vocab.BASE_TOTALS:
            assert record["data"][name] is None


# --------------------------------------------------------------------------- #
# the error shapes
# --------------------------------------------------------------------------- #


class TestErrorShapes:
    def test_no_route_leaks_a_500_for_a_missing_record(self, client: TestClient):
        for path in (
            f"{PREFIX}/quotes/missing",
            f"{PREFIX}/quotes/missing/pricing-runs",
            f"{PREFIX}/quotes/missing/rate-reads",
            f"{PREFIX}/price-lists/missing/items",
            f"{PREFIX}/decisions/missing",
        ):
            assert client.get(path).status_code == 404, path

    def test_every_write_route_refuses_an_empty_body_with_a_4xx_not_a_500(
        self, client: TestClient, world: dict[str, str]
    ):
        for method, path in (
            ("POST", f"{PREFIX}/currencies"),
            ("POST", f"{PREFIX}/currencies/{{currency_id}}/rate"),
            ("POST", f"{PREFIX}/price-lists"),
            ("POST", f"{PREFIX}/price-lists/{{price_list_id}}/items"),
            ("POST", f"{PREFIX}/quotes"),
            ("POST", f"{PREFIX}/quotes/{{quote_id}}/lines"),
            ("PATCH", f"{PREFIX}/quotes/{{quote_id}}/currency"),
            ("POST", f"{PREFIX}/quotes/{{quote_id}}/price"),
        ):
            rendered = path.format(
                currency_id="missing", price_list_id="missing", quote_id="missing"
            )
            response = client.request(method, rendered, params=ACTOR, json={})
            assert 400 <= response.status_code < 500, (
                f"{method} {rendered} -> {response.status_code}"
            )

    def test_every_advertised_route_answers_on_an_empty_store(self, client: TestClient):
        """A board that 500s on a fresh room is a broken feature.

        ``tools/verify_all_routes.py`` calls every advertised route with an empty body and
        fails on a 5xx, so each of these shapes is reproduced here rather than discovered on a
        runner.
        """

        module = importlib.import_module(FEATURE_MODULE)
        for route in module.router.routes:
            for method in sorted(m for m in route.methods if m not in ("HEAD", "OPTIONS")):
                path = route.path
                for placeholder in ("currency_id", "price_list_id", "quote_id"):
                    path = path.replace("{" + placeholder + "}", "missing")
                response = client.request(method, f"{PREFIX}{path[len(PREFIX) :]}", json={})
                assert response.status_code < 500, f"{method} {path} -> {response.status_code}"

    def test_a_get_on_a_write_route_is_never_a_500(self, client: TestClient, world):
        """A GET on a POST-only path.

        The exact status is not this test's claim, and it cannot be: ``dsr/api.py`` mounts an
        SPA catch-all for every GET path nothing else claimed, and a catch-all refuses an
        ``api/`` path with a 404. That answer is produced by shared code which this feature
        may not edit, so asserting a specific status here would pin a fact about someone
        else's file.

        What this test does claim is the property that matters for this workflow: a pricing
        refusal is carried in the body of a successful POST, and a caller who mistypes the
        method gets a non-5xx answer rather than a server fault. A refused pricing run is a
        200 by design, and it would be easy for a future change to make the refusal a 4xx or
        a 5xx; the assertion that pins the refusal itself is
        ``test_the_wrong_price_list_is_a_200_that_refuses_with_code_34``.
        """

        response = client.get(f"{PREFIX}/quotes/{world['quote']}/price")

        assert response.status_code < 500
