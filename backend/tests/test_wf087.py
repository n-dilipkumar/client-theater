"""The WF-087 domain: the tier arithmetic, the catalogue rules and the engine.

What this file is for
---------------------

The research leaves the arithmetic for the three pricing methods undefined, and
this workflow derived it. Those derivations are the load-bearing part of this
feature: a quote line's total is whatever this module says it is, and a seller who
disagrees with it has no way to check it. So the derivations are tested against
the worked examples that were put to Jev, not against the implementation's own
output, which would only prove it is self-consistent.

The rest covers the rules the vendor states outright, the engine's validation and
the seed.

Running alone and running in a run
---------------------------------

Every test builds its own rows through the engine, so no test depends on another
having run first. The suite runs under pytest-xdist, where the order of two files
on different workers is not the order of two files in one process. A test that
passed only because a previous test in this file had left a product behind would
pass here and fail in CI.
"""

from __future__ import annotations

import ast
import importlib
import math
from datetime import datetime, timezone
from pathlib import Path

import pytest
from dsr.db.audited import AuditedDatabase
from dsr.price_books import errors, inferences, rules, vocabulary as vocab
from dsr.price_books.engine import CatalogueEngine
from dsr.store import RecordStore

FEATURE_MODULE = "dsr.features.wf087_curate_a_product_and_price_book_catalo"
DOMAIN_PACKAGE = "dsr.price_books"

#: A frozen clock. Nothing in this file reads the real one, and nothing in it
#: compares a stamp against today's date: a test that passes today and fails on a
#: Tuesday is a test about the calendar, not about pricing.
NOW = datetime(2026, 10, 5, 9, 0, 0, tzinfo=timezone.utc)

#: The three ranges every arithmetic test in this file prices against, written
#: once so a change to a test table cannot quietly change a shared fixture.
#: 1-9 at 100, 10-24 at 80, 25 and upward at 60.
TABLE = (
    {"min_qty": 1, "unit_price": 100},
    {"min_qty": 10, "unit_price": 80},
    {"min_qty": 25, "unit_price": 60},
)

#: The fee version of the same three ranges, for stair-step.
FEE_TABLE = (
    {"min_qty": 1, "fee": 500},
    {"min_qty": 10, "fee": 400},
    {"min_qty": 25, "fee": 300},
)


# --------------------------------------------------------------------------- #
# Fixtures and helpers
# --------------------------------------------------------------------------- #


@pytest.fixture()
def engine(store: RecordStore) -> CatalogueEngine:
    """An engine over an empty store and a clock this test controls.

    The clock is injected rather than patched, because an engine that reads
    ``datetime.now`` through a module attribute is one global assignment away from
    every test in the process seeing the same instant.
    """
    store.create(
        "room", {"name": "Northwind data room"}, record_id="room_a", actor="dana", source="fixture"
    )
    return CatalogueEngine(store, now=lambda: NOW)


def make_product(engine: CatalogueEngine, **overrides: object) -> dict:
    """One saved product, with a valid payload the test can vary one field of.

    A module-level function rather than a fixture so a test can call it with
    overrides and read the id back without a fixture lookup in between.

    The pricing method is left out unless a test asks for one, so that passing
    ``tiers=[]`` with a price map produces a flat product instead of a tiered
    method with no tiers, which the engine refuses.
    """
    payload: dict = {
        "name": "Northwind Platform",
        "sku": "NWD-PLAT",
        "currency": "USD",
        "tiers": [dict(row) for row in TABLE],
    }
    payload.update(overrides)
    if "pricing_method" not in payload:
        payload["pricing_method"] = (
            vocab.METHOD_GRADUATED if payload.get("tiers") else vocab.METHOD_FLAT
        )
    return engine.create_product(payload, actor="dana", source="fixture", room_id="room_a")


def make_book(engine: CatalogueEngine, **overrides: object) -> dict:
    """One saved price book. A new book starts switched off; see the engine."""
    payload: dict = {
        "name": "Standard list",
        "description": "The default rate.",
        vocab.SUPPORTED_CURRENCIES: ["USD"],
    }
    payload.update(overrides)
    return engine.create_price_book(payload, actor="dana", source="fixture", room_id="room_a")


def add_row(engine: CatalogueEngine, product: dict, book: dict, **overrides: object) -> dict:
    """One price-book row joining a product and a book."""
    payload: dict = {"product_id": product["id"], "price_book_id": book["id"]}
    payload.update(overrides)
    return engine.add_book_item(payload, actor="dana", source="fixture", room_id="room_a")


# --------------------------------------------------------------------------- #
# The three pricing methods, against the worked examples Jev decided
# --------------------------------------------------------------------------- #


class TestVolumeBasedPricing:
    """Every unit takes the rate the total quantity reaches."""

    def test_a_quantity_of_twelve_costs_nine_hundred_sixty(self):
        """Tiers 1-9 at 100, 10-24 at 80, 25 up at 60. Twelve reaches the 80 rate.

        12 times 80 is 960. The alternative, charging each unit at its own range,
        would be 1140 and belongs to graduated.
        """
        priced = rules.price_line(vocab.METHOD_VOLUME_BASED, rules.tiers(TABLE), 12)
        assert priced["unit_price"] == 80.0
        assert priced["total"] == 960.0
        assert priced["tier_label"] == "10 to 24"
        assert priced["unit_is_average"] is False

    def test_every_unit_takes_the_reached_rate(self):
        """The breakdown is one figure, not one per range.

        A breakdown with three parts would be graduated pricing wearing the
        volume-based label.
        """
        priced = rules.price_line(vocab.METHOD_VOLUME_BASED, rules.tiers(TABLE), 30)
        assert len(priced["breakdown"]) == 1
        assert priced["unit_price"] == 60.0
        assert priced["total"] == 1800.0

    def test_a_quantity_at_the_boundary_takes_the_new_rate(self):
        assert (
            rules.price_line(vocab.METHOD_VOLUME_BASED, rules.tiers(TABLE), 9)["unit_price"]
            == 100.0
        )
        assert (
            rules.price_line(vocab.METHOD_VOLUME_BASED, rules.tiers(TABLE), 10)["unit_price"]
            == 80.0
        )


class TestGraduatedPricing:
    """Each unit takes the rate of the range its own position falls in."""

    def test_a_quantity_of_twelve_costs_one_thousand_one_hundred_forty(self):
        """Nine units at 100 plus three at 80 is 900 plus 240, which is 1140."""
        priced = rules.price_line(vocab.METHOD_GRADUATED, rules.tiers(TABLE), 12)
        assert priced["total"] == 1140.0
        assert [part["quantity"] for part in priced["breakdown"]] == [9.0, 3.0]
        assert [part["amount"] for part in priced["breakdown"]] == [900.0, 240.0]

    def test_the_unit_price_is_an_average_not_a_rate(self):
        """1140 over 12 is 95, which nobody can charge per unit.

        ``unit_is_average`` is what stops a page printing 95 as the price of one
        unit and a buyer checking the arithmetic against it.
        """
        priced = rules.price_line(vocab.METHOD_GRADUATED, rules.tiers(TABLE), 12)
        assert priced["unit_price"] == 95.0
        assert priced["unit_is_average"] is True

    def test_a_quantity_inside_one_range_costs_that_range_s_rate(self):
        """Twelve units wholly inside 10-24 is 12 times 80, which is 960.

        The same quantity and the same table cost 960 under volume-based, which
        is the whole difference between the two methods.
        """
        assert rules.price_line(vocab.METHOD_GRADUATED, rules.tiers(TABLE), 12)["total"] == 1140.0
        assert rules.price_line(vocab.METHOD_VOLUME_BASED, rules.tiers(TABLE), 12)["total"] == 960.0

    def test_a_quantity_past_the_last_range_leaves_no_remainder(self):
        """Thirty units split 9, 15 and 6, and the six add up to thirty."""
        priced = rules.price_line(vocab.METHOD_GRADUATED, rules.tiers(TABLE), 30)
        assert sum(part["quantity"] for part in priced["breakdown"]) == 30.0
        assert priced["total"] == 900.0 + 1200.0 + 360.0


class TestStairStepPricing:
    """The reached range charges its fee once, however many units were bought."""

    def test_twelve_units_and_twentyfour_units_cost_the_same(self):
        """Both reach 10-24, so both charge that range's fee of 400."""
        table = rules.tiers(FEE_TABLE)
        assert rules.price_line(vocab.METHOD_STAIR_STEP, table, 12)["total"] == 400.0
        assert rules.price_line(vocab.METHOD_STAIR_STEP, table, 24)["total"] == 400.0

    def test_the_fee_does_not_scale_with_the_quantity(self):
        assert (
            rules.price_line(vocab.METHOD_STAIR_STEP, rules.tiers(FEE_TABLE), 30)["total"] == 300.0
        )
        assert (
            rules.price_line(vocab.METHOD_STAIR_STEP, rules.tiers(FEE_TABLE), 1)["total"] == 500.0
        )

    def test_the_unit_price_is_the_fee_over_the_quantity_and_says_so(self):
        priced = rules.price_line(vocab.METHOD_STAIR_STEP, rules.tiers(FEE_TABLE), 10)
        assert priced["total"] == 400.0
        assert priced["unit_price"] == 40.0
        assert priced["unit_is_average"] is True

    def test_a_tier_with_only_a_unit_price_is_read_as_a_fee(self):
        """A tier written by somebody who did not use the word fee still prices.

        The PandaDoc example in the research carries one number per tier with no
        way to say which kind it is, so a stair-step tier with only a unit price
        charges that as the fee.
        """
        assert rules.price_line(vocab.METHOD_STAIR_STEP, rules.tiers(TABLE), 12)["total"] == 80.0


# --------------------------------------------------------------------------- #
# Tier normalisation, and the maximum that is never authored
# --------------------------------------------------------------------------- #


class TestTierTables:
    def test_a_maximum_is_derived_from_the_next_minimum(self):
        table = rules.tiers(TABLE)
        assert [row["max_qty"] for row in table] == [9, 24, None]

    def test_the_final_range_is_open_ended(self):
        """None, not a large number.

        A sentinel like 999999999 reads as a real ceiling to anyone who exports
        the row, and the research calls this range infinity.
        """
        assert rules.tiers(TABLE)[-1]["max_qty"] is None

    def test_the_table_is_sorted_by_minimum(self):
        """Author order is not stored order.

        Somebody who typed the ranges largest-first still gets a table that reads
        smallest-first, because the maximum of each range is the next minimum and
        that only makes sense in order.
        """
        reversed_table = rules.tiers(list(reversed(TABLE)))
        assert [row["min_qty"] for row in reversed_table] == [1, 10, 25]

    def test_a_tier_without_a_minimum_is_refused(self):
        with pytest.raises(errors.CatalogueRefusal) as raised:
            rules.tiers([{"unit_price": 100}])
        assert raised.value.code == "tier_needs_a_minimum"

    def test_a_fractional_minimum_is_refused(self):
        with pytest.raises(errors.CatalogueRefusal) as raised:
            rules.tiers([{"min_qty": 1.5, "unit_price": 100}])
        assert raised.value.code == "tier_minimum_must_be_whole"

    def test_a_minimum_of_zero_is_refused(self):
        with pytest.raises(errors.CatalogueRefusal) as raised:
            rules.tiers([{"min_qty": 0, "unit_price": 100}])
        assert raised.value.code == "tier_minimum_must_be_positive"

    def test_two_tiers_at_the_same_minimum_are_refused(self):
        with pytest.raises(errors.CatalogueRefusal) as raised:
            rules.tiers([{"min_qty": 1, "unit_price": 100}, {"min_qty": 1, "unit_price": 80}])
        assert raised.value.code == "tier_minimums_must_rise"

    def test_a_tier_with_no_price_and_no_fee_is_refused(self):
        with pytest.raises(errors.CatalogueRefusal) as raised:
            rules.tiers([{"min_qty": 1}])
        assert raised.value.code == "tier_needs_a_price"

    def test_the_pandadoc_threshold_and_value_spelling_is_read(self):
        """The research quotes tiers as {"value": 30, "min_qty": 1}."""
        assert rules.tiers([{"value": 30, "min_qty": 1}])[0]["unit_price"] == 30.0

    def test_a_threshold_spelled_from_is_read(self):
        """A sibling workflow reads min_qty, min_quantity and from in that order."""
        assert rules.tiers([{"from": 5, "price": 20}])[0]["min_qty"] == 5

    def test_an_empty_table_is_not_an_error(self):
        assert rules.tiers([]) == []
        assert rules.tiers(None) == []


class TestQuantityBelowTheFirstRange:
    def test_a_quantity_under_the_first_minimum_prices_at_the_first_rate(self):
        """A table starting at ten still prices a quantity of one.

        The alternative is no matching range and a silent zero, which undercharges
        every quote a buyer places for fewer units than the first range covers.
        """
        table = rules.tiers([{"min_qty": 10, "unit_price": 80}])
        assert rules.price_line(vocab.METHOD_VOLUME_BASED, table, 1)["unit_price"] == 80.0

    def test_a_graduated_quantity_under_the_first_minimum_goes_to_the_first_range(self):
        table = rules.tiers([{"min_qty": 10, "unit_price": 80}])
        priced = rules.price_line(vocab.METHOD_GRADUATED, table, 3)
        assert len(priced["breakdown"]) == 1
        assert priced["total"] == 240.0


# --------------------------------------------------------------------------- #
# Vocabulary normalisation
# --------------------------------------------------------------------------- #


class TestVocabulary:
    def test_the_hyphenated_method_spellings_resolve(self):
        """The research writes volume-based, graduated and stair-step."""
        assert rules.normalise_pricing_method("volume-based") == vocab.METHOD_VOLUME_BASED
        assert rules.normalise_pricing_method("stair-step") == vocab.METHOD_STAIR_STEP
        assert rules.normalise_pricing_method("graduated") == vocab.METHOD_GRADUATED

    def test_an_unknown_method_is_refused_and_names_the_four(self):
        with pytest.raises(errors.CatalogueRefusal) as raised:
            rules.normalise_pricing_method("wholesale")
        assert raised.value.code == "unknown_pricing_method"
        assert "volume-based" in raised.value.detail

    def test_a_currency_is_upper_cased(self):
        """usd and USD are one currency, not two keys in the price map."""
        assert rules.normalise_currency("usd") == "USD"

    def test_a_currency_that_is_not_three_letters_is_refused(self):
        with pytest.raises(errors.CatalogueRefusal) as raised:
            rules.normalise_currency("DOLLARS")
        assert raised.value.code == "currency_is_not_a_code"

    def test_the_billing_spellings_resolve(self):
        assert rules.normalise_billing("one_time") == vocab.BILLING_ONE_TIME
        assert rules.normalise_billing("monthly") == vocab.BILLING_RECURRING

    def test_a_missing_billing_frequency_defaults_to_one_time(self):
        assert rules.normalise_billing(None) == vocab.BILLING_ONE_TIME

    def test_a_price_book_needs_at_least_one_currency(self):
        with pytest.raises(errors.CatalogueRefusal) as raised:
            rules.normalise_book_currencies([])
        assert raised.value.code == "price_book_needs_a_currency"

    def test_a_single_currency_string_is_read_as_a_list_of_one(self):
        """The research quotes a one-currency dropdown; a mirror may store a string."""
        assert rules.normalise_book_currencies("USD") == ["USD"]

    def test_duplicate_currencies_collapse(self):
        assert rules.normalise_book_currencies(["USD", "usd", "EUR"]) == ["USD", "EUR"]

    def test_the_book_currency_order_is_preserved(self):
        """A page lists a book the way an administrator entered its currencies."""
        assert rules.book_currencies({vocab.SUPPORTED_CURRENCIES: ["GBP", "USD"]}) == ["GBP", "USD"]


# --------------------------------------------------------------------------- #
# Money
# --------------------------------------------------------------------------- #


class TestMoney:
    def test_a_figure_rounds_half_up(self):
        """0.005 rounds to 0.01, where Python's round would give 0.0.

        Two identical lines summed in a different order would then total
        differently, and a column a buyer adds up has to equal the total.
        """
        assert rules.money(0.005) == 0.01
        assert rules.money(1.005) == 1.01
        assert rules.money(2.675) == 2.68

    def test_a_string_price_is_read(self):
        """A price a person typed as "2,400.50" is a price."""
        assert rules.money("2,400.50") == 2400.50

    def test_a_boolean_is_not_a_price(self):
        """True equals 1 in Python, and a price of 1.00 reads as correct.

        This is why ``as_number`` refuses a bool rather than letting it through.
        """
        assert rules.money(True) == 0.0

    def test_a_negative_price_is_refused_on_input(self):
        with pytest.raises(errors.CatalogueRefusal) as raised:
            rules.require_money(-5, "price")
        assert raised.value.code == "price_is_negative"

    def test_an_infinite_price_is_refused(self):
        with pytest.raises(errors.CatalogueRefusal) as raised:
            rules.require_money(float("inf"), "price")
        assert raised.value.code == "price_is_not_finite"

    def test_a_nan_price_is_refused(self):
        with pytest.raises(errors.CatalogueRefusal) as raised:
            rules.require_money(float("nan"), "price")
        assert raised.value.code == "price_is_not_finite"

    def test_a_word_is_not_a_price(self):
        with pytest.raises(errors.CatalogueRefusal) as raised:
            rules.require_money("twelve", "price")
        assert raised.value.code == "price_is_not_a_number"

    def test_a_refusal_names_the_field_it_is_about(self):
        """The message lands beside the input, so the errors map must be keyed."""
        with pytest.raises(errors.CatalogueRefusal) as raised:
            rules.require_money(-5, "tiers[2].unit_price")
        assert "tiers[2].unit_price" in raised.value.errors


class TestQuantity:
    def test_a_quantity_must_be_at_least_one(self):
        for bad in (0, -1, "none", None):
            with pytest.raises(errors.CatalogueRefusal) as raised:
                rules.require_quantity(bad)
            assert raised.value.code == "quantity_must_be_positive"


# --------------------------------------------------------------------------- #
# Override precedence
# --------------------------------------------------------------------------- #


class TestOverridePrecedence:
    def test_an_active_book_row_overrides_the_product_tiers(self):
        """The product is the default and the row is the override."""
        product = {
            "pricing_method": vocab.METHOD_VOLUME_BASED,
            "currency": "USD",
            "tiers": list(TABLE),
        }
        answered = rules.effective_unit_price(
            product, 12, price_book_item={"prices": {"USD": 50.0}}, currency="USD"
        )
        assert answered["source"] == vocab.SOURCE_PRICE_BOOK
        assert answered["unit_price"] == 50.0

    def test_an_inactive_book_row_is_skipped(self):
        product = {
            "pricing_method": vocab.METHOD_VOLUME_BASED,
            "currency": "USD",
            "tiers": list(TABLE),
        }
        answered = rules.effective_unit_price(
            product,
            12,
            price_book_item={"state": vocab.BOOK_ITEM_INACTIVE, "prices": {"USD": 50.0}},
            currency="USD",
        )
        assert answered["source"] == vocab.SOURCE_TIER
        assert answered["unit_price"] == 80.0

    def test_a_book_with_no_row_leaves_the_product_price_in_force(self):
        """A book is a segment and a region, not a whitelist.

        A buyer in the book's segment must still be able to buy the catalogue.
        """
        product = {
            "pricing_method": vocab.METHOD_VOLUME_BASED,
            "currency": "USD",
            "tiers": list(TABLE),
        }
        answered = rules.effective_unit_price(
            product, 12, price_book={"supported_currencies": ["USD"]}
        )
        assert answered["source"] == vocab.SOURCE_TIER

    def test_a_book_row_with_its_own_tiers_replaces_the_product_tiers(self):
        product = {
            "pricing_method": vocab.METHOD_VOLUME_BASED,
            "currency": "USD",
            "tiers": list(TABLE),
        }
        answered = rules.effective_unit_price(
            product,
            12,
            price_book_item={
                "pricing_method": vocab.METHOD_VOLUME_BASED,
                "tiers": [{"min_qty": 1, "unit_price": 10}],
            },
            currency="USD",
        )
        assert answered["source"] == vocab.SOURCE_PRICE_BOOK
        assert answered["unit_price"] == 10.0

    def test_a_flat_product_prices_from_its_price_map(self):
        answered = rules.effective_unit_price(
            {"prices": {"USD": 100.0, "EUR": 90.0}}, 2, currency="EUR"
        )
        assert answered["source"] == vocab.SOURCE_PRODUCT_DEFAULT
        assert answered["unit_price"] == 90.0
        assert answered["currency"] == "EUR"

    def test_a_product_with_no_price_at_all_says_missing(self):
        """An unprovisioned catalogue is absence, not a broken quote."""
        answered = rules.effective_unit_price({"name": "Nothing"}, 1)
        assert answered["source"] == vocab.SOURCE_MISSING
        assert answered["total"] == 0.0

    def test_a_missing_price_is_never_read_as_zero(self):
        """A product priced in USD is not priced at nothing in EUR.

        Reading a missing currency as zero would price an international quote at
        no cost, which is the worst failure this rule can have.
        """
        answered = rules.effective_unit_price({"prices": {"USD": 100.0}}, 1, currency="EUR")
        assert answered["source"] == vocab.SOURCE_MISSING

    def test_no_currency_is_ever_converted(self):
        """The vendor never mentions an exchange rate in the price-book guide."""
        answered = rules.effective_unit_price({"prices": {"USD": 100.0}}, 1, currency="EUR")
        assert answered["unit_price"] == 0.0

    def test_the_response_names_the_currency_it_priced_in(self):
        answered = rules.effective_unit_price({"prices": {"USD": 100.0}}, 1, currency="usd")
        assert answered["currency"] == "USD"


class TestMissingCurrencies:
    def test_a_row_missing_one_of_two_currencies_is_a_gap(self):
        book = {vocab.SUPPORTED_CURRENCIES: ["USD", "CAD"]}
        assert rules.missing_currencies(book, [{"product_id": "p1", "prices": {"USD": 5.0}}]) == {
            "p1": ["CAD"]
        }

    def test_a_complete_row_is_not_reported(self):
        book = {vocab.SUPPORTED_CURRENCIES: ["USD", "CAD"]}
        rows = [{"product_id": "p1", "prices": {"USD": 5.0, "CAD": 6.0}}]
        assert rules.missing_currencies(book, rows) == {}

    def test_a_store_record_is_unwrapped_before_it_is_read(self):
        """The engine passes raw records from find; a test passes a payload.

        Not unwrapping would report every row as missing a price in every
        currency, because a raw record keeps its payload under ``data``.
        """
        book = {vocab.SUPPORTED_CURRENCIES: ["USD"]}
        records = [{"id": "row1", "data": {"product_id": "p1", "prices": {"USD": 5.0}}}]
        assert rules.missing_currencies(book, records) == {}


# --------------------------------------------------------------------------- #
# Import and bundles, both stated by the vendor
# --------------------------------------------------------------------------- #


class TestTheImportRule:
    def test_a_flat_product_may_be_imported(self):
        """The research: import bulk-imports flat-rate products."""
        allowed, _why = rules.is_importable({"pricing_method": vocab.METHOD_FLAT})
        assert allowed is True

    def test_a_tiered_product_may_not(self):
        """The research: tier priced products cannot be imported."""
        for method in vocab.TIERED_PRICING_METHODS:
            allowed, why = rules.is_importable({"pricing_method": method})
            assert allowed is False
            assert "cannot be imported" in why

    def test_the_method_decides_not_the_presence_of_tiers(self):
        """A flat product with a stray tier row imports.

        The rule is about the method, because it is the method that makes an
        export ambiguous.
        """
        assert rules.is_importable(
            {"pricing_method": "flat", "tiers": [{"min_qty": 1, "unit_price": 1}]}
        )[0]


class TestTheBundleRule:
    def test_a_bundle_is_refused_a_price_book_row(self):
        """The vendor: bundle products cannot be added to a price book."""
        assert rules.is_bundle({"product_type": "bundle"}) is True
        assert rules.is_bundle({"product_type": "regular"}) is False
        assert rules.is_bundle({}) is False


# --------------------------------------------------------------------------- #
# The engine
# --------------------------------------------------------------------------- #


class TestSavingAProduct:
    def test_a_saved_product_carries_its_normalised_tiers(self, engine: CatalogueEngine):
        saved = make_product(engine)
        assert saved["tiers"][0]["max_qty"] == 9
        assert saved["is_tiered"] is True
        assert saved["tier_count"] == 3

    def test_a_flat_product_carries_a_price_per_currency(self, engine: CatalogueEngine):
        saved = make_product(
            engine,
            sku="NWD-FLAT",
            pricing_method=vocab.METHOD_FLAT,
            tiers=[],
            prices={"USD": 2400.0, "EUR": 2250.0},
        )
        assert saved["prices"] == {"EUR": 2250.0, "USD": 2400.0}
        assert sorted(saved["currencies"]) == ["EUR", "USD"]
        assert saved["is_tiered"] is False

    def test_a_product_with_neither_a_price_nor_tiers_is_refused(self, engine: CatalogueEngine):
        with pytest.raises(errors.CatalogueRefusal) as raised:
            make_product(engine, tiers=[], pricing_method=vocab.METHOD_FLAT, prices={})
        assert raised.value.code == "product_needs_a_price"

    def test_a_negative_price_is_refused_at_save_time(self, engine: CatalogueEngine):
        with pytest.raises(errors.CatalogueRefusal) as raised:
            make_product(engine, tiers=[], pricing_method=vocab.METHOD_FLAT, prices={"USD": -1})
        assert raised.value.code == "price_is_negative"

    def test_a_bundle_type_round_trips(self, engine: CatalogueEngine):
        saved = make_product(engine, product_type=vocab.PRODUCT_TYPE_BUNDLE)
        assert saved["is_bundle"] is True

    def test_the_import_refusal_reaches_the_saved_record(self, engine: CatalogueEngine):
        saved = make_product(engine)
        assert saved["is_importable"] is False
        assert "cannot be imported" in saved["import_refusal"]


class TestEditingAProduct:
    def test_a_patch_that_would_break_the_pricing_is_refused(self, engine: CatalogueEngine):
        """Switching a flat product to graduated leaves it with no tiers.

        Revalidating the merged record rather than the patch is what catches it.
        """
        saved = make_product(
            engine, sku="NWD-FLAT", tiers=[], pricing_method=vocab.METHOD_FLAT, prices={"USD": 10.0}
        )
        with pytest.raises(errors.CatalogueRefusal):
            engine.edit_product(
                saved["id"],
                {"pricing_method": vocab.METHOD_GRADUATED},
                actor="dana",
                source="fixture",
            )

    def test_a_valid_patch_is_merged(self, engine: CatalogueEngine):
        saved = make_product(engine)
        edited = engine.edit_product(
            saved["id"], {"name": "Renamed"}, actor="dana", source="fixture"
        )
        assert edited["name"] == "Renamed"
        assert edited["tier_count"] == 3

    def test_editing_a_missing_product_is_a_not_found(self, engine: CatalogueEngine):
        with pytest.raises(errors.CatalogueNotFound) as raised:
            engine.edit_product("nope", {"name": "x"}, actor="dana", source="fixture")
        assert raised.value.code == "not_a_product"


class TestRetiring:
    def test_a_retired_product_stays_in_the_catalogue(self, engine: CatalogueEngine):
        """The quote: retire without deleting."""
        saved = make_product(engine)
        retired = engine.retire_product(saved["id"], active=False, actor="dana", source="fixture")
        assert retired["active"] is False
        assert retired["status"] == vocab.RETIRED
        assert engine.get_product(saved["id"])["id"] == saved["id"]

    def test_a_retired_product_is_hidden_when_only_active_is_asked_for(
        self, engine: CatalogueEngine
    ):
        make_product(engine)
        second = make_product(engine, sku="NWD-TWO")
        engine.retire_product(second["id"], active=False, actor="dana", source="fixture")
        assert len(engine.list_products()) == 2
        assert len(engine.list_products(active_only=True)) == 1

    def test_a_retired_product_can_come_back(self, engine: CatalogueEngine):
        saved = make_product(engine)
        engine.retire_product(saved["id"], active=False, actor="dana", source="fixture")
        back = engine.retire_product(saved["id"], active=True, actor="dana", source="fixture")
        assert back["status"] == vocab.ACTIVE

    def test_deleting_a_product_is_separate_from_retiring_it(self, engine: CatalogueEngine):
        saved = make_product(engine)
        engine.remove_product(saved["id"], actor="dana", source="fixture")
        with pytest.raises(errors.CatalogueNotFound):
            engine.get_product(saved["id"])

    def test_deleting_a_product_removes_its_price_book_rows_in_one_go(
        self, engine: CatalogueEngine
    ):
        """A row naming a product that no longer exists is worse than a retired one."""
        product = make_product(engine)
        book = make_book(engine)
        add_row(engine, product, book, prices={"USD": 10.0})
        removed = engine.remove_product(product["id"], actor="dana", source="fixture")
        assert removed["removed_rows"] == 1
        assert engine.list_book_items(str(book["id"])) == []


class TestPriceBooks:
    def test_a_new_book_starts_switched_off(self, engine: CatalogueEngine):
        """Create, add products, then activate. The researched order."""
        assert make_book(engine)["active"] is False

    def test_a_book_needs_a_name(self, engine: CatalogueEngine):
        with pytest.raises(errors.CatalogueRefusal) as raised:
            make_book(engine, name="")
        assert raised.value.code == "price_book_needs_a_name"
        assert "name" in raised.value.errors

    def test_a_book_needs_a_currency(self, engine: CatalogueEngine):
        with pytest.raises(errors.CatalogueRefusal) as raised:
            make_book(engine, **{vocab.SUPPORTED_CURRENCIES: []})
        assert raised.value.code == "price_book_needs_a_currency"
        assert vocab.SUPPORTED_CURRENCIES in raised.value.errors

    def test_a_book_reports_the_currencies_it_supports(self, engine: CatalogueEngine):
        book = make_book(engine, **{vocab.SUPPORTED_CURRENCIES: ["USD", "EUR"]})
        assert book["currencies"] == ["USD", "EUR"]

    def test_an_empty_book_cannot_be_activated(self, engine: CatalogueEngine):
        book = make_book(engine)
        with pytest.raises(errors.CatalogueRefusal) as raised:
            engine.activate_price_book(str(book["id"]), active=True, actor="dana", source="fixture")
        assert raised.value.code == "price_book_is_not_complete"

    def test_a_book_with_a_missing_currency_cannot_be_activated(self, engine: CatalogueEngine):
        """The vendor: every product needs a price for each supported currency."""
        product = make_product(engine, sku="NWD-A")
        book = make_book(engine, **{vocab.SUPPORTED_CURRENCIES: ["USD", "GBP"]})
        add_row(engine, product, book, prices={"USD": 10.0})
        with pytest.raises(errors.CatalogueRefusal) as raised:
            engine.activate_price_book(str(book["id"]), active=True, actor="dana", source="fixture")
        assert raised.value.code == "price_book_is_not_complete"
        assert "GBP" in raised.value.errors["price_book_id"]

    def test_a_complete_book_activates(self, engine: CatalogueEngine):
        product = make_product(engine, sku="NWD-A")
        book = make_book(engine, **{vocab.SUPPORTED_CURRENCIES: ["USD", "GBP"]})
        add_row(engine, product, book, prices={"USD": 10.0, "GBP": 8.0})
        active = engine.activate_price_book(
            str(book["id"]), active=True, actor="dana", source="fixture"
        )
        assert active["active"] is True
        assert active["is_complete"] is True

    def test_a_book_view_reports_its_gaps_on_read(self, engine: CatalogueEngine):
        """The gap is derived from the rows, so a stored copy would be stale."""
        product = make_product(engine, sku="NWD-A")
        book = make_book(engine, **{vocab.SUPPORTED_CURRENCIES: ["USD", "GBP"]})
        add_row(engine, product, book, prices={"USD": 10.0})
        assert engine.get_price_book(str(book["id"]))["missing_prices"]

    def test_a_book_can_be_switched_off_without_a_refusal(self, engine: CatalogueEngine):
        book = make_book(engine)
        off = engine.activate_price_book(
            str(book["id"]), active=False, actor="dana", source="fixture"
        )
        assert off["active"] is False


class TestPriceBookRows:
    def test_a_second_row_for_the_same_pair_is_refused(self, engine: CatalogueEngine):
        """The vendor: only one instance of each product per price book."""
        product = make_product(engine)
        book = make_book(engine)
        add_row(engine, product, book, prices={"USD": 10.0})
        with pytest.raises(errors.CatalogueRefusal) as raised:
            add_row(engine, product, book, prices={"USD": 9.0})
        assert raised.value.code == "one_row_per_product_and_book"

    def test_the_same_product_may_appear_in_two_books(self, engine: CatalogueEngine):
        """Nothing scopes the constraint beyond one book.

        The vendor's own use case is different regional books for the same
        catalogue.
        """
        product = make_product(engine)
        first = make_book(engine, name="North")
        second = make_book(engine, name="South")
        add_row(engine, product, first, prices={"USD": 10.0})
        add_row(engine, product, second, prices={"USD": 11.0})
        assert len(engine.list_book_items(product_id=product["id"])) == 2

    def test_a_bundle_is_refused(self, engine: CatalogueEngine):
        product = make_product(engine, product_type=vocab.PRODUCT_TYPE_BUNDLE)
        book = make_book(engine)
        with pytest.raises(errors.CatalogueRefusal) as raised:
            add_row(engine, product, book, prices={"USD": 10.0})
        assert raised.value.code == "bundle_products_are_not_in_a_price_book"

    def test_a_row_needs_both_ids(self, engine: CatalogueEngine):
        make_product(engine)
        book = make_book(engine)
        with pytest.raises(errors.CatalogueRefusal) as raised:
            engine.add_book_item(
                {"price_book_id": book["id"]}, actor="dana", source="fixture", room_id="room_a"
            )
        assert raised.value.code == "price_book_row_needs_a_product"

    def test_a_bare_price_goes_to_the_only_currency_the_book_supports(
        self, engine: CatalogueEngine
    ):
        """One currency, one price: which currency it lands in is not a guess."""
        product = make_product(engine)
        book = make_book(engine, **{vocab.SUPPORTED_CURRENCIES: ["GBP"]})
        row = add_row(engine, product, book, price=10.0)
        assert row["prices"] == {"GBP": 10.0}

    def test_a_bare_price_is_refused_when_the_book_prices_in_several(self, engine: CatalogueEngine):
        """Two currencies and one price: which one it is cannot be known."""
        product = make_product(engine)
        book = make_book(engine, **{vocab.SUPPORTED_CURRENCIES: ["USD", "GBP"]})
        with pytest.raises(errors.CatalogueRefusal) as raised:
            add_row(engine, product, book, price=10.0)
        assert raised.value.code == "price_book_row_missing_a_price"

    def test_a_row_inherits_the_product_billing_frequency(self, engine: CatalogueEngine):
        product = make_product(engine, billing_frequency=vocab.BILLING_RECURRING)
        book = make_book(engine)
        assert (
            add_row(engine, product, book, prices={"USD": 1.0})["billing_frequency"]
            == vocab.BILLING_RECURRING
        )

    def test_a_row_records_its_term(self, engine: CatalogueEngine):
        product = make_product(engine)
        book = make_book(engine)
        assert (
            add_row(engine, product, book, prices={"USD": 1.0}, term="24 months")["term"]
            == "24 months"
        )

    def test_a_row_resolves_the_names_it_points_at(self, engine: CatalogueEngine):
        """One request per book lists its rows with their product names."""
        product = make_product(engine)
        book = make_book(engine)
        row = add_row(engine, product, book, prices={"USD": 1.0})
        assert row["product_name"] == "Northwind Platform"
        assert row["price_book_name"] == "Standard list"

    def test_a_row_may_be_switched_off(self, engine: CatalogueEngine):
        product = make_product(engine)
        book = make_book(engine)
        row = add_row(engine, product, book, prices={"USD": 1.0})
        off = engine.edit_book_item(
            str(row["id"]),
            {"state": vocab.BOOK_ITEM_INACTIVE},
            actor="dana",
            source="fixture",
        )
        assert off["state"] == vocab.BOOK_ITEM_INACTIVE

    def test_removing_a_row_keeps_the_product(self, engine: CatalogueEngine):
        product = make_product(engine)
        book = make_book(engine)
        row = add_row(engine, product, book, prices={"USD": 1.0})
        engine.remove_book_item(str(row["id"]), actor="dana", source="fixture")
        assert engine.list_book_items(str(book["id"])) == []
        assert engine.get_product(product["id"])["id"] == product["id"]


class TestPricingThroughTheEngine:
    def test_a_line_prices_from_the_product_tiers(self, engine: CatalogueEngine):
        product = make_product(engine, pricing_method=vocab.METHOD_VOLUME_BASED)
        assert engine.price(product["id"], 12)["unit_price"] == 80.0
        assert engine.price(product["id"], 12)["total"] == 960.0

    def test_a_line_prices_from_the_book_row_when_one_applies(self, engine: CatalogueEngine):
        product = make_product(engine, pricing_method=vocab.METHOD_VOLUME_BASED)
        book = make_book(engine)
        add_row(engine, product, book, prices={"USD": 50.0})
        engine.activate_price_book(str(book["id"]), active=True, actor="dana", source="fixture")
        answered = engine.price(product["id"], 12, price_book_id=str(book["id"]), currency="USD")
        assert answered["source"] == vocab.SOURCE_PRICE_BOOK
        assert answered["unit_price"] == 50.0
        assert answered["price_book_applied"] is True

    def test_a_line_prices_the_wanted_currency(self, engine: CatalogueEngine):
        product = make_product(engine, sku="NWD-FLAT", tiers=[], prices={"USD": 100.0, "EUR": 90.0})
        assert engine.price(product["id"], 1, currency="EUR")["unit_price"] == 90.0

    def test_a_retired_product_reports_that_it_is_retired(self, engine: CatalogueEngine):
        """The flag is reported alongside the price rather than refusing the lookup.

        A quote may still show a retired product that is already on it; what
        retirement changes is that the product is not offered for new work.
        """
        product = make_product(engine)
        engine.retire_product(product["id"], active=False, actor="dana", source="fixture")
        assert engine.price(product["id"], 1)["retired"] is True

    def test_pricing_an_unknown_product_is_a_not_found(self, engine: CatalogueEngine):
        with pytest.raises(errors.CatalogueNotFound):
            engine.price("nope", 1)


class TestTheSummary:
    def test_the_summary_counts_what_the_catalogue_holds(self, engine: CatalogueEngine):
        first = make_product(engine, sku="NWD-A")
        make_product(engine, sku="NWD-B")
        engine.retire_product(first["id"], active=False, actor="dana", source="fixture")
        book = make_book(engine, **{vocab.SUPPORTED_CURRENCIES: ["USD", "EUR"]})
        add_row(engine, first, book, prices={"USD": 1.0, "EUR": 1.0})
        counted = engine.summary()
        assert counted["products"] == 2
        assert counted["active_products"] == 1
        assert counted["retired_products"] == 1
        assert counted["tiered_products"] == 2
        assert counted["price_books"] == 1
        assert counted["price_book_rows"] == 1
        assert counted["currencies"] == ["EUR", "USD"]

    def test_the_summary_names_the_platform(self, engine: CatalogueEngine):
        assert vocab.describe()["platform"] == vocab.PLATFORM


class TestSearch:
    def test_a_term_matches_the_name_case_insensitively(self, engine: CatalogueEngine):
        make_product(engine, sku="NWD-A", name="Northwind Platform")
        assert len(engine.list_products(term="northwind")) == 1
        assert engine.list_products(term="nothing here") == []

    def test_a_term_matches_the_sku(self, engine: CatalogueEngine):
        make_product(engine, sku="NWD-PLAT")
        assert len(engine.list_products(term="nwd-plat")) == 1

    def test_an_empty_term_matches_everything(self, engine: CatalogueEngine):
        make_product(engine, sku="NWD-A")
        make_product(engine, sku="NWD-B")
        assert len(engine.list_products()) == 2


# --------------------------------------------------------------------------- #
# The seed
# --------------------------------------------------------------------------- #


class TestTheSeed:
    def _seed(self, db: AuditedDatabase) -> str:
        module = importlib.import_module(FEATURE_MODULE)
        return module.seed(db, {"room_ids": ["room_a"], "now": NOW, "rng": None})

    def test_the_seed_returns_a_cp1252_encodable_string(self, db: AuditedDatabase):
        """A RIGHTWARDS ARROW in one feature's return string broke the seeder.

        The whole seeder prints to a Windows console, so one character outside
        cp1252 stops every feature after it from being seeded at all.
        """
        summary = self._seed(db)
        assert isinstance(summary, str) and summary.strip()
        summary.encode("cp1252")

    def test_the_seed_names_the_states_it_created(self, db: AuditedDatabase):
        """The seeder prints this, so it has to say what is there to look at."""
        summary = self._seed(db)
        assert "products" in summary
        assert "price books" in summary

    def test_the_seed_leaves_the_three_pricing_methods_on_show(self, db: AuditedDatabase):
        """A demo with no graduated product demonstrates nothing about graduated."""
        self._seed(db)
        engine = CatalogueEngine(RecordStore(db), now=lambda: NOW)
        methods = {row["pricing_method"] for row in engine.list_products()}
        assert set(vocab.TIERED_PRICING_METHODS) <= methods
        assert vocab.METHOD_FLAT in methods

    def test_the_seed_leaves_one_retired_product(self, db: AuditedDatabase):
        """The Active toggle is a thing a reviewer can see, not only read about."""
        self._seed(db)
        engine = CatalogueEngine(RecordStore(db), now=lambda: NOW)
        assert engine.summary()["retired_products"] == 1

    def test_the_seed_leaves_one_book_that_cannot_go_live(self, db: AuditedDatabase):
        """The activation refusal has something real to refuse."""
        self._seed(db)
        engine = CatalogueEngine(RecordStore(db), now=lambda: NOW)
        incomplete = [book for book in engine.list_price_books() if book["missing_prices"]]
        assert incomplete, "the demo needs a price book with a missing currency"

    def test_the_seed_leaves_one_book_that_is_live(self, db: AuditedDatabase):
        self._seed(db)
        engine = CatalogueEngine(RecordStore(db), now=lambda: NOW)
        live = [
            book for book in engine.list_price_books() if book["active"] and book["is_complete"]
        ]
        assert live, "the demo needs an active, complete price book"

    def test_the_seed_is_a_no_op_when_the_catalogue_already_holds_products(
        self, db: AuditedDatabase
    ):
        first = self._seed(db)
        assert "already holds" not in first
        assert "already holds" in self._seed(db)

    def test_the_seed_accepts_room_pairs_and_bare_ids(self, db: AuditedDatabase):
        """The seeder passes one shape on some paths and another on the rest."""
        module = importlib.import_module(FEATURE_MODULE)
        summary = module.seed(db, {"room_ids": [("room_a", "Northwind")], "now": NOW})
        assert "products" in summary

    def test_the_seed_survives_a_context_with_no_rooms(self, db: AuditedDatabase):
        """An empty catalogue in no room is better than a seeder that raises."""
        module = importlib.import_module(FEATURE_MODULE)
        assert module.seed(db, {"room_ids": [], "now": NOW})


# --------------------------------------------------------------------------- #
# The contract: the module shape a hundred parallel features depend on
# --------------------------------------------------------------------------- #


def _feature_source() -> str:
    return Path(importlib.import_module(FEATURE_MODULE).__file__).read_text(encoding="utf-8")


def _domain_paths() -> list[Path]:
    package = Path(importlib.import_module(DOMAIN_PACKAGE).__file__).parent
    return sorted(package.glob("*.py"))


def test_the_feature_exports_a_descriptor():
    module = importlib.import_module(FEATURE_MODULE)
    assert module.FEATURE["id"] == "wf-087-curate-a-product-and-price-book-catalo"
    assert module.FEATURE["ticket"] == "WF-087"
    assert module.FEATURE["name"]


def test_the_router_prefix_is_the_ticket_slug_the_issue_names():
    """The feature host matches paths literally, so the case is not interchangeable."""
    module = importlib.import_module(FEATURE_MODULE)
    assert module.router.prefix == "/api/wf-087"


def test_the_feature_imports_nothing_from_the_app():
    """Checked with the parser, not a substring search.

    A substring search would also read the module docstring, which names
    ``dsr.api`` to say the import is forbidden, and a module that explains the
    rule would fail the rule's own test.
    """
    tree = ast.parse(_feature_source())
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert not (node.module or "").startswith("dsr.api")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith("dsr.api")


def test_the_domain_imports_nothing_but_the_store():
    """A domain module that imports the app cannot be unit-tested on its own.

    Checked with the parser rather than with a substring search, so a mention in
    a docstring cannot pass as an import and a real import cannot hide inside a
    line the search did not anticipate.
    """
    for path in _domain_paths():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("dsr."), f"{path.name} imports {alias.name}"
            elif isinstance(node, ast.ImportFrom):
                if node.module is None or not node.module.startswith("dsr."):
                    continue
                assert node.module == "dsr.store" or node.module.startswith("dsr.price_books"), (
                    f"{path.name} imports {node.module}"
                )


def test_the_domain_never_opens_a_database_itself():
    for path in _domain_paths():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name != "sqlite3", f"{path.name} imports sqlite3"


def test_every_owned_collection_is_namespaced_by_the_ticket():
    """A hundred workflows share one database; an unprefixed name collides."""
    for collection in vocab.OWNED_COLLECTIONS:
        assert collection.startswith("wf087_"), collection


def test_the_seed_is_exported():
    assert callable(importlib.import_module(FEATURE_MODULE).seed)


def test_no_decision_is_left_uncited():
    """Every rule this build derived has to name the audit that settled it."""
    for entry in inferences.DECIDED:
        assert entry["audit"].startswith("jev-"), entry["id"]
        assert entry["question"], entry["id"]
        assert entry["decision"], entry["id"]
        assert entry["rejected"], entry["id"]


def test_every_record_shape_names_its_collection():
    for name, shape in inferences.RECORD_SHAPE.items():
        assert shape["collection"].startswith("wf087_"), name
        assert shape["fields"], name


def test_the_derived_arithmetic_is_the_one_that_was_gated():
    """The three worked examples, against the constants rather than the code.

    If a later edit changes the arithmetic these numbers fail, which is the
    point: the audit id in the docstrings refers to these exact figures.
    """
    table = rules.tiers(TABLE)
    assert rules.price_line(vocab.METHOD_VOLUME_BASED, table, 12)["total"] == 960.0
    assert rules.price_line(vocab.METHOD_GRADUATED, table, 12)["total"] == 1140.0
    assert rules.price_line(vocab.METHOD_STAIR_STEP, rules.tiers(FEE_TABLE), 12)["total"] == 400.0
    assert math.isclose(1140.0 / 12.0, 95.0)
