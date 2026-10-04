"""WF-089: quote in a transaction currency with FX conversion, and what it refuses.

The researched specification is ``docs/research/digital-sales-room-workflows/wf/WF-089.md``,
quoted in full in issue 146. These tests are organised by the researched rule each one
defends, because the point of this workflow is that the rules were sourced rather than
chosen, so a rule with no test is a rule the next person to touch it will quietly drop.

The sections, and the sourced or derived rule each one pins:

``one base currency, and how a rate is found``
    "organization.basecurrencyid" and "the system's default currency". One base currency per
    deployment, the identity rate for a quote already in it, a Custom record's stamped rate,
    and a Standard record's platform rate. A rate on a Standard record is refused.
``money is decimal and quantised to the currency``
    ``CurrencyPrecision`` is the currency's own number of decimal places. Float is rejected
    because ``0.1 + 0.2`` is ``0.30000000000000004``.
``the rate runs one way``
    "the exchange rate is used to convert all money fields in the record from the local
    currency to the system's default currency", so one rate is base per transaction unit.
``the transaction figure is authoritative``
    The issue asks which of the two stored figures is the truth and demands the answer be
    recorded. Totals are computed in the transaction currency; every base figure is derived
    from them, and nothing reads a base figure as an input.
``code 34``
    "34 Invalid Price Level Currency". A price list in a different currency from the header
    refuses the run.
``code 38``
    "38 Transaction currency is not set for the product price list item". A line item with no
    price row in the transaction currency refuses the run.
``a refusal is an outcome``
    "the platform refuses to price and sets pricingerrorcode". The run answers as data, writes
    an audit row, and clears every figure so no stale total survives.
``a price comes from the price row``
    "line-item unit prices resolve from the pricelevelproduct row in that currency". A payload
    unit price is refused with the field named.
``the currency cannot change under line items``
    "You can't change the currency of the base record ... unless you remove all the line items
    associated with the record."
``the rate is an event, and it is stamped``
    "the RetrieveExchangeRate message/event on the Currency table (RetrieveExchangeRateRequest,
    \"Event: True\")", on "record open, create, update, and on product add/update/delete".
``a stamped rate does not rewrite a priced quote``
    The stored base figure is a record. Re-stamping a currency rate names the affected quotes
    and leaves their figures alone.
``the domain imports nothing but the store``
    The architectural guard, checked with an AST walk.
``the seed return string``
    Every character encodable by cp1252, and the states the seeder claims actually exist.

The HTTP surface is in ``test_wf089_http.py``.
"""

from __future__ import annotations

import ast
import importlib
import inspect
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from dsr.quote_currency import QuoteCurrencyEngine, inferences, rules, vocabulary as vocab
from dsr.quote_currency.errors import (
    CurrencyChangeRefused,
    CurrencyRefusal,
    QuoteNotFound,
)
from dsr.store import RecordStore

# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

NOW = datetime(2026, 10, 4, 9, 0, 0, tzinfo=timezone.utc)

FEATURE_MODULE = "dsr.features.wf089_quote_in_a_transaction_currency_with_fx"
DOMAIN_PACKAGE = "dsr.quote_currency"

#: The rate the EUR record carries. 1.08 is base USD per one unit of EUR, so EUR 15,401.00
#: is USD 16,633.08. The two numbers are asserted exactly, because a rate test that rounds is
#: not a rate test.
EUR_RATE = "1.08"


class Clock:
    """A clock the test moves by hand.

    Every stamp this workflow writes comes from here, which is what makes "each run reads the
    rate again" a claim about distinct instants rather than about a list length.
    """

    def __init__(self, start: datetime = NOW) -> None:
        self.at = start

    def __call__(self) -> datetime:
        return self.at

    def advance(self, **kwargs: float) -> datetime:
        self.at = self.at + timedelta(**kwargs)
        return self.at


@pytest.fixture()
def clock() -> Clock:
    return Clock()


@pytest.fixture()
def engine(clock: Clock, store: RecordStore) -> QuoteCurrencyEngine:
    """An engine over an empty store, with the two currencies the tests price in.

    USD is the organisation's base currency. EUR is a ``Custom`` record, because that is the
    record type the research says a deployment stamps its own rate on, and most of these
    tests are about a rate that came from somewhere specific.
    """

    store.create(
        "room", {"name": "Northwind data room"}, record_id="room_a", actor="dana", source="fixture"
    )
    store.create(
        "room", {"name": "Halcyon data room"}, record_id="room_b", actor="dana", source="fixture"
    )
    built = QuoteCurrencyEngine(store, now=clock)
    built.register_currency("USD", is_base_currency=True, actor="dana", source="fixture")
    built.register_currency(
        "EUR", currency_type="Custom", exchange_rate=EUR_RATE, actor="dana", source="fixture"
    )
    return built


def price_lists(engine: QuoteCurrencyEngine) -> dict[str, dict]:
    """One price list per currency, each with the same two products."""

    made = {}
    for code, seat, premium in (("USD", "1320.00", "1980.00"), ("EUR", "1200.00", "1800.50")):
        price_list = engine.create_price_list(f"{code} standard list", code, actor="dana")
        engine.create_price_item(price_list["id"], "SEAT-STD", seat, actor="dana")
        engine.create_price_item(price_list["id"], "SEAT-PRE", premium, actor="dana")
        made[code] = price_list
    return made


def eur_quote(engine: QuoteCurrencyEngine, *, lines: bool = True) -> dict:
    """A EUR quote with two priced lines, priced once."""

    lists = price_lists(engine)
    quote = engine.create_quote(
        "Northwind renewal", "EUR", price_list_id=lists["EUR"]["id"], actor="dana"
    )
    if lines:
        engine.add_line(quote["id"], "SEAT-STD", 10, actor="dana")
        engine.add_line(
            quote["id"], "SEAT-PRE", 2, discount_amount="500.00", tax_amount="300.00", actor="dana"
        )
        engine.price_quote(quote["id"], trigger=vocab.TRIGGER_CREATE, actor="dana")
    return quote


def domain_paths() -> list[Path]:
    package = Path(importlib.import_module(DOMAIN_PACKAGE).__file__).parent
    return sorted(path for path in package.glob("*.py"))


def feature_path() -> Path:
    return Path(importlib.import_module(FEATURE_MODULE).__file__)


# --------------------------------------------------------------------------- #
# one base currency, and how a rate is found
# --------------------------------------------------------------------------- #


class TestTheBaseCurrencyAndTheRate:
    def test_a_quote_in_the_base_currency_converts_to_itself(self, engine: QuoteCurrencyEngine):
        lists = price_lists(engine)
        quote = engine.create_quote(
            "Halcyon renewal", "USD", price_list_id=lists["USD"]["id"], actor="dana"
        )
        engine.add_line(quote["id"], "SEAT-STD", 2, actor="dana")

        run = engine.price_quote(quote["id"], actor="dana")["run"]

        assert run["rate"] == vocab.IDENTITY_RATE
        assert run["rate_source"] == vocab.RATE_SOURCE_IDENTITY
        assert run["totals"]["totalamount"] == "2640.00"
        assert run["totals_base"]["totalamount_base"] == "2640.00"

    def test_a_custom_record_supplies_its_own_rate(self, engine: QuoteCurrencyEngine):
        lists = price_lists(engine)
        quote = engine.create_quote(
            "Northwind renewal", "EUR", price_list_id=lists["EUR"]["id"], actor="dana"
        )
        engine.add_line(quote["id"], "SEAT-STD", 10, actor="dana")

        run = engine.price_quote(quote["id"], actor="dana")["run"]

        assert run["rate"] == pytest.approx(1.08)
        assert run["rate_source"] == vocab.RATE_SOURCE_CUSTOM
        assert run["base_iso_code"] == "USD"

    def test_a_rate_on_a_standard_record_is_refused(self, store: RecordStore, clock: Clock):
        """The reconciliation of the research's two statements about ``exchangerate``."""

        built = QuoteCurrencyEngine(store, now=clock)

        with pytest.raises(CurrencyRefusal) as caught:
            built.register_currency("GBP", exchange_rate="1.27", actor="dana")

        assert "exchange_rate" in caught.value.errors
        assert "Custom" in caught.value.errors["exchange_rate"]

    def test_a_custom_record_with_no_rate_is_refused(self, store: RecordStore, clock: Clock):
        built = QuoteCurrencyEngine(store, now=clock)

        with pytest.raises(CurrencyRefusal) as caught:
            built.register_currency("GBP", currency_type="Custom", actor="dana")

        assert "exchange_rate" in caught.value.errors

    def test_a_standard_record_with_no_rate_is_allowed_and_says_so(
        self, store: RecordStore, clock: Clock
    ):
        """A platform-maintained record is not required to carry its rate at creation.

        The platform writes it later, so refusing here would make a currency unregistrable
        until the rate happened to exist. The projection reports the absence rather than
        inventing a rate.
        """

        built = QuoteCurrencyEngine(store, now=clock)
        built.register_currency("GBP", actor="dana")

        projected = built.currencies()
        gbp = next(row for row in projected if row["iso_code"] == "GBP")

        assert gbp["exchange_rate"] is None
        assert gbp["exchange_rate_present"] is False
        assert gbp["round_trip_example"]["in_base"] is None

    def test_a_second_base_currency_is_refused(self, store: RecordStore, clock: Clock):
        """The system's default currency is singular."""

        built = QuoteCurrencyEngine(store, now=clock)
        built.register_currency("USD", is_base_currency=True, actor="dana")

        with pytest.raises(CurrencyRefusal) as caught:
            built.register_currency("EUR", is_base_currency=True, actor="dana")

        assert vocab.BASE_CURRENCY_FIELD in caught.value.errors

    def test_a_custom_record_cannot_claim_to_be_the_base_currency(
        self, store: RecordStore, clock: Clock
    ):
        built = QuoteCurrencyEngine(store, now=clock)

        with pytest.raises(CurrencyRefusal) as caught:
            built.register_currency(
                "EUR", currency_type="Custom", exchange_rate="1.08", is_base_currency=True
            )

        assert "currency_type" in caught.value.errors

    def test_a_quote_in_a_currency_with_no_record_is_refused_at_the_runs_source(self):
        """The rule at :func:`dsr.quote_currency.rules.resolve_rate`, exercised directly.

        The engine refuses an unregistered currency at quote creation, so this state is only
        reachable by a record deleted underneath a live quote. It is a distinct failure from
        "a record with no rate" and it deserves a distinct message.
        """

        with pytest.raises(CurrencyRefusal) as caught:
            rules.resolve_rate(None, transaction_iso_code="ZZZ", base_iso_code="USD")

        assert "no currency record" in str(caught.value)

    def test_the_resolution_names_where_its_rate_came_from(self, engine: QuoteCurrencyEngine):
        currencies = {row["iso_code"]: row for row in engine.currencies()}

        assert currencies["EUR"]["rate_is_writable"] is True
        assert currencies["USD"]["rate_is_writable"] is False
        assert currencies["EUR"]["round_trip_example"]["in_base"] == "108.00"


# --------------------------------------------------------------------------- #
# money is decimal and quantised to the currency
# --------------------------------------------------------------------------- #


class TestMoney:
    def test_a_decimal_amount_survives_where_a_float_would_not(self):
        """The defect float money has, stated as the assertion it prevents."""

        assert Decimal("0.1") + Decimal("0.2") == Decimal("0.3")
        assert 0.1 + 0.2 != 0.3

    def test_an_amount_is_quantised_to_the_currencys_precision(self):
        assert str(rules.as_money("1200.005", 2, "unit_price")) == "1200.01"
        assert str(rules.as_money("1200.004", 2, "unit_price")) == "1200.00"

    def test_a_zero_precision_currency_keeps_whole_units(self):
        assert str(rules.as_money("1200.6", 0, "unit_price")) == "1201"

    def test_precision_outside_the_bound_is_refused(self):
        with pytest.raises(CurrencyRefusal) as caught:
            rules.validate_precision(9)

        assert "currency_precision" in caught.value.errors

    def test_a_non_numeric_amount_is_refused_with_the_field_named(self):
        with pytest.raises(CurrencyRefusal) as caught:
            rules.as_money("a lot", 2, "unit_price")

        assert caught.value.errors == {"unit_price": "unit_price must be a number, not 'a lot'."}

    def test_a_boolean_is_not_an_amount(self):
        """``True`` is 1 in Python, and a boolean in a money field is a bug, not one euro."""

        with pytest.raises(CurrencyRefusal) as caught:
            rules.as_money(True, 2, "quantity")

        assert "quantity" in caught.value.errors

    def test_a_negative_discount_is_accepted_because_it_is_a_surcharge(
        self, engine: QuoteCurrencyEngine
    ):
        """A discount is a signed amount. Refusing a negative would refuse a surcharge."""

        lists = price_lists(engine)
        quote = engine.create_quote(
            "Northwind renewal", "EUR", price_list_id=lists["EUR"]["id"], actor="dana"
        )
        engine.add_line(quote["id"], "SEAT-STD", 1, discount_amount="-50.00", actor="dana")

        run = engine.price_quote(quote["id"], actor="dana")["run"]

        assert run["totals"]["totaldiscountamount"] == "-50.00"
        assert run["totals"]["totalamount"] == "1250.00"

    def test_a_rate_at_or_below_zero_is_refused(self):
        for bad in ("0", "-1.2"):
            with pytest.raises(CurrencyRefusal):
                rules.as_rate(bad)

    def test_a_missing_rate_is_refused_rather_than_defaulted_to_one(self):
        with pytest.raises(CurrencyRefusal) as caught:
            rules.as_rate(None)

        assert "exchange_rate" in caught.value.errors


# --------------------------------------------------------------------------- #
# the rate runs one way
# --------------------------------------------------------------------------- #


class TestRateDirection:
    def test_the_rate_converts_transaction_currency_into_base(self):
        assert str(rules.to_base(Decimal("15401.00"), 1.08, 2)) == "16633.08"

    def test_a_rate_below_one_converts_downwards(self):
        assert str(rules.to_base(Decimal("100.00"), 0.5, 2)) == "50.00"

    def test_the_round_trip_returns_the_original_amount(self):
        forward = rules.to_base(Decimal("15401.00"), 1.08, 2)
        back = rules.to_transaction(forward, 1.08, 2)

        assert abs(back - Decimal("15401.00")) <= Decimal("0.01")

    def test_a_float_rate_does_not_inject_its_error_into_a_money_figure(self):
        """The multiplication goes through the rate's decimal form, not its binary one.

        The two figures differ because ``repr(0.30000000000000004)`` is exactly that string
        and :class:`~decimal.Decimal` reads it as 0.30000000000000004, where a float
        multiplication carries the binary error. Both paths are shown so the assertion is
        about the conversion and not about the library refusing to multiply.
        """

        rate = 0.1 + 0.2

        assert str(rate) == "0.30000000000000004"
        assert str(rules.to_base(Decimal("100.00"), rate, 2)) == "30.00"
        assert repr(100.00 * rate) != "30.0"

    def test_the_stored_totals_are_the_directional_ones(self, engine: QuoteCurrencyEngine):
        run = engine.price_quote(eur_quote(engine)["id"], actor="dana")["run"]

        assert run["totals"]["totalamount"] == "15401.00"
        assert run["totals_base"]["totalamount_base"] == "16633.08"


# --------------------------------------------------------------------------- #
# the transaction figure is authoritative
# --------------------------------------------------------------------------- #


class TestTheAuthoritativeFigure:
    def test_the_vocabulary_names_the_transaction_figure_and_says_why(self):
        assert vocab.AUTHORITATIVE == "transaction_currency"
        assert "computed in the transaction currency" in vocab.AUTHORITATIVE_REASON

    def test_every_base_figure_is_derived_from_the_transaction_figure(
        self, engine: QuoteCurrencyEngine
    ):
        run = engine.price_quote(eur_quote(engine)["id"], actor="dana")["run"]

        for name, value in run["totals"].items():
            derived = rules.to_base(Decimal(value), run["rate"], 2)
            assert run["totals_base"][f"{name}_base"] == str(derived)

    def test_the_run_names_which_figure_is_authoritative(self, engine: QuoteCurrencyEngine):
        result = engine.price_quote(eur_quote(engine)["id"], actor="dana")

        assert result["authoritative"] == vocab.AUTHORITATIVE
        assert result["quote"]["authoritative"] == vocab.AUTHORITATIVE

    def test_no_rule_takes_a_base_figure_as_an_input(self):
        """The claim, checked on the signatures rather than on prose.

        Every parameter of the two conversion and pricing functions is named here. A design
        that read a stored base figure as an input would have to add a parameter ending in
        ``_base``, so this assertion fails the day one appears.
        """

        parameters = set(inspect.signature(rules.price_quote).parameters) | set(
            inspect.signature(rules.resolve_unit_price).parameters
        )

        assert not [name for name in parameters if name.endswith(vocab.BASE_SUFFIX)]

    def test_the_decision_records_the_alternative_it_rejected(self):
        decision = inferences.describe_one("AUTHORITATIVE_IS_THE_TRANSACTION_FIGURE")

        assert decision["chosen"] == "transaction_currency"
        assert "base_currency" in decision["options"]
        assert "reporting view" in decision["rejected_because"]


# --------------------------------------------------------------------------- #
# code 34 and code 38
# --------------------------------------------------------------------------- #


class TestPricingError34:
    def test_a_price_list_in_another_currency_refuses_the_run(self, engine: QuoteCurrencyEngine):
        lists = price_lists(engine)
        quote = engine.create_quote(
            "Wrong price list", "EUR", price_list_id=lists["USD"]["id"], actor="dana"
        )
        engine.add_line(quote["id"], "SEAT-STD", 1, actor="dana")

        run = engine.price_quote(quote["id"], actor="dana")["run"]

        assert run["outcome"] == vocab.OUTCOME_REFUSED
        assert run["pricing_error_code"] == vocab.PRICING_ERROR_INVALID_PRICE_LEVEL_CURRENCY
        assert run["pricing_error"] == "Invalid Price Level Currency"

    def test_the_refusal_names_both_currencies(self, engine: QuoteCurrencyEngine):
        lists = price_lists(engine)
        quote = engine.create_quote(
            "Wrong price list", "EUR", price_list_id=lists["USD"]["id"], actor="dana"
        )

        refusal = engine.price_quote(quote["id"], actor="dana")["refusal"]

        assert refusal["expected"] == "EUR"
        assert refusal["found"] == "USD"

    def test_a_quote_with_no_price_list_is_refused_as_code_38(self, engine: QuoteCurrencyEngine):
        """A quote with nothing to price against cannot resolve a unit price."""

        quote = engine.create_quote("No price list", "EUR", actor="dana")
        engine.add_line(quote["id"], "SEAT-STD", 1, actor="dana")

        run = engine.price_quote(quote["id"], actor="dana")["run"]

        assert run["pricing_error_code"] == vocab.PRICING_ERROR_TRANSACTION_CURRENCY_NOT_SET

    def test_the_mismatch_is_allowed_at_stamping_and_refused_at_pricing(
        self, engine: QuoteCurrencyEngine
    ):
        """The decision that makes code 34 observable at all."""

        lists = price_lists(engine)
        quote = engine.create_quote(
            "Wrong price list", "EUR", price_list_id=lists["USD"]["id"], actor="dana"
        )

        assert quote["price_list_currency_matches"] is False
        assert quote["pricing_outcome"] is None

    def test_the_two_codes_are_separate_values(self):
        assert (
            vocab.PRICING_ERROR_INVALID_PRICE_LEVEL_CURRENCY
            != vocab.PRICING_ERROR_TRANSACTION_CURRENCY_NOT_SET
        )
        assert vocab.PRICING_ERROR_INVALID_PRICE_LEVEL_CURRENCY == "34"
        assert vocab.PRICING_ERROR_TRANSACTION_CURRENCY_NOT_SET == "38"


class TestPricingError38:
    def test_a_line_with_no_price_row_refuses_the_run(self, engine: QuoteCurrencyEngine):
        lists = price_lists(engine)
        quote = engine.create_quote(
            "Unknown product", "EUR", price_list_id=lists["EUR"]["id"], actor="dana"
        )
        engine.add_line(quote["id"], "SEAT-PLATINUM", 1, actor="dana")

        run = engine.price_quote(quote["id"], actor="dana")["run"]

        assert run["outcome"] == vocab.OUTCOME_REFUSED
        assert run["pricing_error_code"] == vocab.PRICING_ERROR_TRANSACTION_CURRENCY_NOT_SET
        assert run["pricing_error"] == (
            "Transaction currency is not set for the product price list item"
        )
        assert "SEAT-PLATINUM" in run["detail"]

    def test_a_product_priced_on_another_lists_price_list_does_not_resolve(
        self, engine: QuoteCurrencyEngine
    ):
        """The product exists. Its price does not, for this list and currency."""

        lists = price_lists(engine)
        engine.create_price_item(lists["USD"]["id"], "SEAT-EXTRA", "2100.00", actor="dana")
        quote = engine.create_quote(
            "Cross list", "EUR", price_list_id=lists["EUR"]["id"], actor="dana"
        )
        engine.add_line(quote["id"], "SEAT-EXTRA", 1, actor="dana")

        run = engine.price_quote(quote["id"], actor="dana")["run"]

        assert run["outcome"] == vocab.OUTCOME_REFUSED
        assert run["pricing_error_code"] == "38"

    def test_one_priced_line_and_one_unpriced_line_refuse_the_whole_quote(
        self, engine: QuoteCurrencyEngine
    ):
        """A refusal is total. A half-priced quote cannot be told from a priced one."""

        lists = price_lists(engine)
        quote = engine.create_quote(
            "One good line", "EUR", price_list_id=lists["EUR"]["id"], actor="dana"
        )
        engine.add_line(quote["id"], "SEAT-STD", 10, actor="dana")
        engine.add_line(quote["id"], "SEAT-PLATINUM", 1, actor="dana")

        run = engine.price_quote(quote["id"], actor="dana")["run"]

        assert run["outcome"] == vocab.OUTCOME_REFUSED
        assert run["totals"] is None
        assert run["totals_base"] is None


# --------------------------------------------------------------------------- #
# a refusal is an outcome
# --------------------------------------------------------------------------- #


class TestARefusalIsAnOutcome:
    def test_a_refusal_carries_the_authority_note(self, engine: QuoteCurrencyEngine):
        lists = price_lists(engine)
        quote = engine.create_quote(
            "Wrong price list", "EUR", price_list_id=lists["USD"]["id"], actor="dana"
        )

        refusal = engine.price_quote(quote["id"], actor="dana")["refusal"]

        assert refusal["outcome"] == vocab.OUTCOME_REFUSED
        assert refusal["authority"] == vocab.REFUSAL_IS_OUTCOME_NOT_ERROR

    def test_a_refusal_writes_an_audit_row_as_a_success_does(self, engine: QuoteCurrencyEngine):
        """A refusal that left no row would be invisible to anybody reading the log."""

        lists = price_lists(engine)
        quote = engine.create_quote(
            "Wrong price list", "EUR", price_list_id=lists["USD"]["id"], actor="dana"
        )

        before = len(engine.store.audit(collection=vocab.PRICING_COLLECTION))
        engine.price_quote(quote["id"], actor="dana")
        after = len(engine.store.audit(collection=vocab.PRICING_COLLECTION))

        assert after == before + 1

    def test_a_refusal_records_the_code_on_the_quote(self, engine: QuoteCurrencyEngine):
        lists = price_lists(engine)
        quote = engine.create_quote(
            "Wrong price list", "EUR", price_list_id=lists["USD"]["id"], actor="dana"
        )
        engine.price_quote(quote["id"], actor="dana")

        reread = engine.quote(quote["id"])

        assert reread["pricing_outcome"] == vocab.OUTCOME_REFUSED
        assert reread["pricing_error_code"] == "34"

    def test_a_refusal_clears_a_previously_priced_total(self, engine: QuoteCurrencyEngine):
        """A stale total beside a refusal reads as priced. So the figures go."""

        lists = price_lists(engine)
        quote = engine.create_quote(
            "Northwind renewal", "EUR", price_list_id=lists["EUR"]["id"], actor="dana"
        )
        engine.add_line(quote["id"], "SEAT-STD", 10, actor="dana")
        engine.price_quote(quote["id"], actor="dana")
        assert engine.quote(quote["id"])["totals"]["totalamount"] == "12000.00"

        engine.add_line(quote["id"], "SEAT-PLATINUM", 1, actor="dana")
        engine.price_quote(quote["id"], trigger=vocab.TRIGGER_PRODUCT_ADD, actor="dana")

        reread = engine.quote(quote["id"])
        assert reread["pricing_outcome"] == vocab.OUTCOME_REFUSED
        assert reread["totals"]["totalamount"] is None
        assert reread["totals_base"]["totalamount_base"] is None
        assert reread["base_totals_present"] is False

    def test_a_refused_quote_is_excluded_from_the_base_total_not_counted_as_zero(
        self, engine: QuoteCurrencyEngine
    ):
        priced = eur_quote(engine)
        lists = price_lists(engine)
        refused = engine.create_quote(
            "Wrong price list", "EUR", price_list_id=lists["USD"]["id"], actor="dana"
        )
        engine.price_quote(refused["id"], actor="dana")

        base = engine.summary()["totals_in_base_currency"]

        assert engine.quote(priced["id"])["pricing_outcome"] == vocab.OUTCOME_PRICED
        assert base["amount"] == "16633.08"
        assert base["quotes_counted"] == 1
        assert base["quotes_without_figures"] == 0

    def test_the_board_counts_the_two_refusals_apart(self, engine: QuoteCurrencyEngine):
        lists = price_lists(engine)
        wrong = engine.create_quote(
            "Wrong price list", "EUR", price_list_id=lists["USD"]["id"], actor="dana"
        )
        engine.price_quote(wrong["id"], actor="dana")
        unknown = engine.create_quote(
            "Unknown product", "EUR", price_list_id=lists["EUR"]["id"], actor="dana"
        )
        engine.add_line(unknown["id"], "SEAT-PLATINUM", 1, actor="dana")
        engine.price_quote(unknown["id"], actor="dana")

        board = engine.summary()

        assert board["refused_quotes"] == 2
        assert board["refusals_by_code"] == {"34": 1, "38": 1}

    def test_a_refusal_still_read_the_rate(self, engine: QuoteCurrencyEngine):
        """The event fires on every run, so a refused quote's figures are traceable."""

        lists = price_lists(engine)
        quote = engine.create_quote(
            "Wrong price list", "EUR", price_list_id=lists["USD"]["id"], actor="dana"
        )
        engine.price_quote(quote["id"], actor="dana")

        reads = engine.rate_reads(quote["id"])

        assert len(reads) == 1
        assert reads[0]["event"] == vocab.RATE_EVENT


# --------------------------------------------------------------------------- #
# a price comes from the price row
# --------------------------------------------------------------------------- #


class TestUnitPriceComesFromThePriceRow:
    def test_adding_a_line_takes_no_price_at_all(self, engine: QuoteCurrencyEngine):
        """The shape is the rule. ``add_line`` has no ``unit_price`` parameter to pass.

        Asserted on the signature because that is where the rule lives: a second price source
        cannot be introduced through this call without a test failing here first. The refusal
        for a caller who sends one anyway is in the HTTP layer, exercised in
        ``test_wf089_http.py``.
        """

        assert "unit_price" not in inspect.signature(QuoteCurrencyEngine.add_line).parameters

    def test_the_resolution_refuses_a_caller_supplied_price_with_the_field_named(self):
        items = [{"price_list_id": "pl1", "product_code": "SEAT-STD", "unit_price": "1200.00"}]
        line = {"product_code": "SEAT-STD", "unit_price": "1.00"}

        with pytest.raises(CurrencyRefusal) as caught:
            rules.resolve_unit_price(line, items, price_list_id="pl1")

        assert "unit_price" in caught.value.errors

    def test_the_refusal_names_the_price_row_as_the_source(self):
        items = [{"price_list_id": "pl1", "product_code": "SEAT-STD", "unit_price": "1200.00"}]

        with pytest.raises(CurrencyRefusal) as caught:
            rules.resolve_unit_price(
                {"product_code": "SEAT-STD", "unit_price": "1.00"}, items, price_list_id="pl1"
            )

        assert "price list row" in caught.value.errors["unit_price"]

    def test_the_price_row_is_read_when_no_price_is_supplied(self):
        items = [{"price_list_id": "pl1", "product_code": "SEAT-STD", "unit_price": "1200.00"}]

        assert rules.resolve_unit_price(
            {"product_code": "SEAT-STD"}, items, price_list_id="pl1"
        ) == Decimal("1200.00")

    def test_a_stored_line_carries_no_unit_price(self, engine: QuoteCurrencyEngine):
        """The line is a product and a quantity. The price is read at pricing time."""

        line = engine.lines(eur_quote(engine)["id"])[0]

        assert line["unit_price"] is None
        assert "resolved from the price list row" in line["unit_price_note"]

    def test_a_missing_price_is_reported_as_code_38_by_the_pricing_rule(self):
        result = rules.price_quote(
            [{"product_code": "SEAT-PLATINUM", "quantity": "1"}],
            [],
            quote_id="q1",
            price_list_id="pl1",
            price_list_iso_code="EUR",
            header_iso_code="EUR",
            base_iso_code="USD",
            base_precision=2,
            rate=1.08,
            precision=2,
        )

        assert result["outcome"] == vocab.OUTCOME_REFUSED
        assert result["pricing_error_code"] == "38"


# --------------------------------------------------------------------------- #
# the currency cannot change under line items
# --------------------------------------------------------------------------- #


class TestTheCurrencyCannotChangeUnderLineItems:
    def test_a_change_is_refused_while_line_items_exist(self, engine: QuoteCurrencyEngine):
        quote = eur_quote(engine)

        with pytest.raises(CurrencyChangeRefused) as caught:
            engine.change_currency(quote["id"], "USD", actor="dana")

        assert caught.value.line_count == 2
        assert caught.value.quote_id == quote["id"]

    def test_the_refusal_body_names_the_sourced_sentence_and_the_remedy(
        self, engine: QuoteCurrencyEngine
    ):
        quote = eur_quote(engine)

        with pytest.raises(CurrencyChangeRefused) as caught:
            engine.change_currency(quote["id"], "USD", actor="dana")

        body = caught.value.to_dict()
        assert "unless you remove all the line items" in body["evidence"]
        assert body["remedy"].startswith("Remove every line item")
        assert body["line_items"] == 2

    def test_a_change_succeeds_with_no_line_items(self, engine: QuoteCurrencyEngine):
        lists = price_lists(engine)
        quote = engine.create_quote(
            "Empty quote", "EUR", price_list_id=lists["EUR"]["id"], actor="dana"
        )

        changed = engine.change_currency(quote["id"], "USD", price_list_id=lists["USD"]["id"])

        assert changed["iso_code"] == "USD"
        assert changed["price_list_currency_matches"] is True
        assert changed["pricing_outcome"] is None

    def test_a_change_to_an_unregistered_currency_is_refused(self, engine: QuoteCurrencyEngine):
        lists = price_lists(engine)
        quote = engine.create_quote(
            "Empty quote", "EUR", price_list_id=lists["EUR"]["id"], actor="dana"
        )

        with pytest.raises(CurrencyRefusal) as caught:
            engine.change_currency(quote["id"], "ZZZ")

        assert "iso_code" in caught.value.errors

    def test_a_change_clears_the_figures_that_were_computed_from_the_old_rate(
        self, engine: QuoteCurrencyEngine
    ):
        lists = price_lists(engine)
        quote = engine.create_quote(
            "Priced then emptied", "EUR", price_list_id=lists["EUR"]["id"], actor="dana"
        )
        engine.add_line(quote["id"], "SEAT-STD", 1, actor="dana")
        engine.price_quote(quote["id"], actor="dana")
        engine.store.bulk_delete(
            [row["id"] for row in engine.lines(quote["id"])],
            source="fixture",
        )

        changed = engine.change_currency(quote["id"], "USD", price_list_id=lists["USD"]["id"])

        assert changed["totals"]["totalamount"] is None
        assert changed["totals_base"]["totalamount_base"] is None
        assert changed["base_totals_present"] is False
        assert changed["currency_changed_at"]


# --------------------------------------------------------------------------- #
# the rate is an event, and it is stamped
# --------------------------------------------------------------------------- #


class TestTheRateIsAnEvent:
    def test_every_trigger_the_research_names_is_accepted(self, engine: QuoteCurrencyEngine):
        quote = eur_quote(engine)

        for trigger in vocab.RECALCULATION_TRIGGERS:
            assert (
                engine.price_quote(quote["id"], trigger=trigger, actor="dana")["run"]["trigger"]
                == trigger
            )

    def test_an_unknown_trigger_is_refused_with_the_known_set_named(
        self, engine: QuoteCurrencyEngine
    ):
        quote = eur_quote(engine)

        with pytest.raises(CurrencyRefusal) as caught:
            engine.price_quote(quote["id"], trigger="whenever")

        assert "record_open" in caught.value.errors["trigger"]

    def test_each_run_reads_the_rate_again(self, clock: Clock, engine: QuoteCurrencyEngine):
        quote = eur_quote(engine)

        first = engine.price_quote(quote["id"], trigger=vocab.TRIGGER_OPEN, actor="dana")
        clock.advance(hours=1)
        second = engine.price_quote(quote["id"], trigger=vocab.TRIGGER_PRODUCT_ADD, actor="dana")

        assert len(engine.rate_reads(quote["id"])) == 3  # the create run, then these two
        assert first["run"]["rate_read_at"] != second["run"]["rate_read_at"]

    def test_every_rate_read_records_the_event_and_its_trigger(self, engine: QuoteCurrencyEngine):
        quote = eur_quote(engine)
        engine.price_quote(quote["id"], trigger=vocab.TRIGGER_PRODUCT_UPDATE, actor="dana")

        read = engine.rate_reads(quote["id"])[0]

        assert read["event"] == vocab.RATE_EVENT
        assert read["trigger"] == vocab.TRIGGER_PRODUCT_UPDATE
        assert read["trigger_label"] == vocab.RECALCULATION_TRIGGER_LABELS[read["trigger"]]

    def test_the_quote_carries_the_rate_it_was_priced_with(self, engine: QuoteCurrencyEngine):
        quote = eur_quote(engine)

        reread = engine.quote(quote["id"])

        assert reread[vocab.RATE_FIELD] == pytest.approx(1.08)
        assert reread[vocab.STAMPED_RATE_FIELD] == reread[vocab.RATE_FIELD]
        assert reread["rate_source"] == vocab.RATE_SOURCE_CUSTOM

    def test_adding_a_line_recalculates_under_the_product_add_trigger(
        self, engine: QuoteCurrencyEngine
    ):
        lists = price_lists(engine)
        quote = engine.create_quote(
            "Northwind renewal", "EUR", price_list_id=lists["EUR"]["id"], actor="dana"
        )
        engine.add_line(quote["id"], "SEAT-STD", 10, actor="dana")
        engine.price_quote(quote["id"], actor="dana")

        engine.add_line(quote["id"], "SEAT-PRE", 2, actor="dana")
        run = engine.price_quote(quote["id"], trigger=vocab.TRIGGER_PRODUCT_ADD, actor="dana")[
            "run"
        ]

        assert run["trigger"] == vocab.TRIGGER_PRODUCT_ADD
        assert run["totals"]["totalamount"] == "15601.00"

    def test_the_runs_are_readable_newest_first(self, clock: Clock, engine: QuoteCurrencyEngine):
        quote = eur_quote(engine)
        engine.price_quote(quote["id"], actor="dana")
        clock.advance(hours=1)
        engine.price_quote(quote["id"], trigger=vocab.TRIGGER_UPDATE, actor="dana")

        runs = engine.pricing_runs(quote["id"])

        assert len(runs) == 3
        assert runs[0]["ran_at"] >= runs[1]["ran_at"]


# --------------------------------------------------------------------------- #
# a stamped rate does not rewrite a priced quote
# --------------------------------------------------------------------------- #


class TestAStampedRateIsNotRetroactive:
    def test_a_stamped_rate_names_the_quotes_holding_the_superseded_figures(
        self, engine: QuoteCurrencyEngine
    ):
        quote = eur_quote(engine)
        eur = engine.currency_by_code("EUR")

        stamped = engine.stamp_rate(eur["id"], "1.20", actor="dana")

        assert stamped["exchange_rate"] == pytest.approx(1.20)
        assert stamped["previous_rate"] == pytest.approx(1.08)
        assert stamped["quotes_holding_the_superseded_rate"] == 1
        assert stamped["quotes_holding_the_superseded_rate_ids"] == [quote["id"]]

    def test_the_priced_quote_keeps_the_figures_it_was_priced_with(
        self, engine: QuoteCurrencyEngine
    ):
        quote = eur_quote(engine)
        engine.stamp_rate(engine.currency_by_code("EUR")["id"], "1.20", actor="dana")

        reread = engine.quote(quote["id"])

        assert reread["totals_base"]["totalamount_base"] == "16633.08"
        assert reread[vocab.STAMPED_RATE_FIELD] == pytest.approx(1.08)

    def test_a_recalculation_picks_the_new_rate_up(self, engine: QuoteCurrencyEngine):
        quote = eur_quote(engine)
        engine.stamp_rate(engine.currency_by_code("EUR")["id"], "1.20", actor="dana")

        run = engine.price_quote(quote["id"], trigger=vocab.TRIGGER_UPDATE, actor="dana")["run"]

        assert run["rate"] == pytest.approx(1.20)
        assert run["totals_base"]["totalamount_base"] == "18481.20"

    def test_the_response_says_what_would_apply_the_new_rate(self, engine: QuoteCurrencyEngine):
        eur_quote(engine)

        stamped = engine.stamp_rate(engine.currency_by_code("EUR")["id"], "1.20", actor="dana")

        assert stamped["recalculate_to_apply_the_new_rate"] == list(vocab.RECALCULATION_TRIGGERS)

    def test_stamping_a_rate_on_a_standard_record_is_refused(self, engine: QuoteCurrencyEngine):
        usd = engine.currency_by_code("USD")

        with pytest.raises(CurrencyRefusal) as caught:
            engine.stamp_rate(usd["id"], "1.00", actor="dana")

        assert "exchange_rate" in caught.value.errors

    def test_stamping_a_rate_on_an_unknown_record_is_a_404(self, engine: QuoteCurrencyEngine):
        with pytest.raises(QuoteNotFound):
            engine.stamp_rate("no-such-currency", "1.20")


# --------------------------------------------------------------------------- #
# reading records this workflow does not own
# --------------------------------------------------------------------------- #


class TestReadingForeignRecords:
    def test_a_quote_written_as_data_by_another_workflow_is_priced(
        self, engine: QuoteCurrencyEngine
    ):
        """WF-086 and WF-087 are not implemented, so the records arrive as data.

        The shape is this workflow's, so a foreign writer that uses it produces a priced
        quote without any code change here.
        """

        lists = price_lists(engine)
        quote = engine.store.create(
            vocab.QUOTE_COLLECTION,
            {
                "name": "Written by WF-086",
                vocab.TRANSACTION_CURRENCY_FIELD: "EUR",
                "currency_record_id": engine.currency_by_code("EUR")["id"],
                "currency_precision": 2,
                vocab.PRICE_LIST_FIELD: lists["EUR"]["id"],
                vocab.ROOM_REF: None,
            },
            actor="wf-086",
            source="POST /api/records/wf089_quote",
        )
        engine.store.create(
            vocab.QUOTE_LINE_COLLECTION,
            {"quote_id": quote["id"], "product_code": "SEAT-STD", "quantity": "3"},
            actor="wf-086",
            source="POST /api/records/wf089_quote_line",
        )

        run = engine.price_quote(quote["id"], actor="dana")["run"]

        assert run["outcome"] == vocab.OUTCOME_PRICED
        assert run["totals"]["totalamount"] == "3600.00"

    def test_the_dependencies_are_recorded_with_what_this_build_does_instead(
        self, engine: QuoteCurrencyEngine
    ):
        described = {row["ticket"]: row for row in vocab.describe()["dependencies"]}

        assert set(described) == {"WF-086", "WF-087"}
        for entry in described.values():
            assert entry["status"] == "not implemented"
            assert entry["this_workflow_does"]

    def test_an_unknown_quote_is_a_404_rather_than_a_server_fault(
        self, engine: QuoteCurrencyEngine
    ):
        with pytest.raises(QuoteNotFound):
            engine.quote("no-such-quote")

    def test_a_quote_pointing_at_a_deleted_price_list_names_the_currency(
        self, engine: QuoteCurrencyEngine
    ):
        from dsr.quote_currency.errors import RateUnavailable

        lists = price_lists(engine)
        quote = engine.create_quote("Orphan", "EUR", price_list_id=lists["EUR"]["id"], actor="dana")
        engine.store.delete(lists["EUR"]["id"], source="fixture")

        with pytest.raises(RateUnavailable) as caught:
            engine.price_quote(quote["id"], actor="dana")

        assert caught.value.iso_code == "EUR"


# --------------------------------------------------------------------------- #
# the board and the served research
# --------------------------------------------------------------------------- #


class TestTheBoard:
    def test_an_empty_board_answers_with_empty_states(self, store: RecordStore, clock: Clock):
        built = QuoteCurrencyEngine(store, now=clock)

        board = built.summary()

        assert board["quotes"] == 0
        assert board["priced_quotes"] == 0
        assert board["refused_quotes"] == 0
        assert board["base_currency"] is None
        assert board["product_model"] == vocab.PRODUCT_MODEL
        assert board["rate_event"] == vocab.RATE_EVENT

    def test_the_board_counts_quotes_by_currency(self, engine: QuoteCurrencyEngine):
        eur_quote(engine)
        lists = price_lists(engine)
        usd = engine.create_quote(
            "Halcyon renewal", "USD", price_list_id=lists["USD"]["id"], actor="dana"
        )
        engine.add_line(usd["id"], "SEAT-STD", 1, actor="dana")
        engine.price_quote(usd["id"], actor="dana")

        board = engine.summary()

        assert board["quotes_by_currency"] == {"EUR": 1, "USD": 1}
        assert board["priced_quotes"] == 2

    def test_the_board_carries_the_jev_audit_id_its_decisions_rest_on(
        self, engine: QuoteCurrencyEngine
    ):
        board = engine.summary()

        assert board["jev_audit_ids"] == ["jev-20261004T205203-28472-23034"]

    def test_the_vocabulary_names_the_product_model_and_what_it_rejected(self):
        described = vocab.describe()

        assert described["product_model"] == "dynamics_dual_currency_rows"
        assert "hubspot_multiple_price_books" in described["product_model_rejected"]
        assert "rates_only_no_stored_base" in described["product_model_rejected"]

    def test_the_vocabulary_names_the_two_money_field_lists(self):
        described = vocab.describe()

        assert described["transaction_totals"] == list(vocab.TRANSACTION_TOTALS)
        assert described["base_totals"] == list(vocab.BASE_TOTALS)

    def test_the_vocabulary_carries_the_four_sourced_events(self):
        described = vocab.describe()

        assert described["recalculation_triggers"] == [
            "record_open",
            "record_create",
            "record_update",
            "product_add",
            "product_update",
            "product_delete",
        ]
        assert len(described["recalculation_triggers"]) == 6

    def test_the_vocabulary_names_both_refusal_codes_with_the_specification_wording(self):
        described = vocab.describe()

        assert described["pricing_error_codes"] == {
            "34": "Invalid Price Level Currency",
            "38": "Transaction currency is not set for the product price list item",
        }

    def test_the_vocabulary_carries_all_seven_sources(self):
        assert len(vocab.describe()["sources"]) == 7

    def test_the_decisions_are_served_with_their_alternatives(self):
        decisions = inferences.describe()

        assert inferences.count() == len(decisions)
        for decision in decisions:
            assert decision["chosen"] in decision["options"]
            assert decision["rejected_because"]

    def test_the_product_model_decision_names_its_jev_audit_and_confidence(self):
        decision = inferences.describe_one("PRODUCT_MODEL_DYNAMICS_OVER_HUBSPOT")

        assert decision["chosen"] == "dynamics_dual_currency_rows"
        assert decision["jev_audit_id"] == "jev-20261004T205203-28472-23034"
        assert decision["jev_confidence"] == 1.0

    def test_the_refusal_outcome_decision_names_its_jev_audit(self):
        decision = inferences.describe_one("REFUSAL_IS_AN_OUTCOME_NOT_AN_ERROR")

        assert decision["jev_audit_id"] == "jev-20261004T205203-28472-23034"

    def test_an_unknown_decision_is_none_rather_than_a_guess(self):
        assert inferences.describe_one("NOT_A_DECISION") is None


# --------------------------------------------------------------------------- #
# the architectural guards
# --------------------------------------------------------------------------- #


class TestArchitecture:
    def test_the_domain_package_imports_nothing_but_the_store(self):
        """The guard the brief names by name.

        The rule is about the dependency direction, not about banning the standard library.
        ``decimal`` and ``datetime`` are ordinary Python. What would be a defect is a domain
        module reaching for ``dsr.api`` or reaching into another workflow's package.
        """

        for path in domain_paths():
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                names: list[str] = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module]
                for name in names:
                    if not name.startswith("dsr"):
                        continue
                    assert (
                        name == "dsr.store"
                        or name == DOMAIN_PACKAGE
                        or name.startswith(f"{DOMAIN_PACKAGE}.")
                    ), (
                        f"{path.name} imports {name}. The domain module may depend on the "
                        "store and on itself, and on nothing else inside dsr."
                    )

    def test_the_domain_package_never_imports_the_app(self):
        for path in domain_paths():
            text = path.read_text(encoding="utf-8")
            assert "from dsr.api" not in text and "import dsr.api" not in text

    def test_the_domain_package_never_opens_sqlite(self):
        for path in domain_paths():
            assert "import sqlite3" not in path.read_text(encoding="utf-8")

    def test_the_feature_module_never_imports_the_app(self):
        assert "from dsr.api" not in feature_path().read_text(encoding="utf-8")

    def test_the_domain_package_imports_nothing_from_another_workflows_package(self):
        for path in domain_paths():
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                names: list[str] = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module]
                for name in names:
                    assert not name.startswith("dsr.features"), (
                        f"{path.name} imports {name}. A feature must not import a feature."
                    )

    def test_every_money_field_reaches_the_store_as_a_string(self, engine: QuoteCurrencyEngine):
        """A money value stored as a float would lose its trailing zero and its precision."""

        quote = eur_quote(engine)
        data = engine.store.require(quote["id"])["data"]

        for name in vocab.TRANSACTION_TOTALS + vocab.BASE_TOTALS:
            assert isinstance(data[name], str)
            Decimal(data[name])  # parses, so it is a number written as text


# --------------------------------------------------------------------------- #
# the seed return string
# --------------------------------------------------------------------------- #


class TestTheSeedString:
    def test_the_seeder_prints_a_string_and_every_character_survives_cp1252(self):
        """The defect that broke the whole seeder, stated as the assertion that prevents it.

        One RIGHTWARDS ARROW in a recovered feature's return string broke the entire seeder on
        a Windows console, because the seeder prints it to a cp1252 console.
        """

        module = importlib.import_module(FEATURE_MODULE)

        from dsr.db.audited import AuditedDatabase

        db = AuditedDatabase(":memory:", actor="seed-test")
        try:
            store = RecordStore(db)
            store.create("room", {"name": "Northwind data room"}, record_id="room_a", actor="dana")
            summary = module.seed(
                db, {"room_ids": [("room_a", "Northwind data room")], "now": NOW, "rng": None}
            )
        finally:
            db.close()

        assert isinstance(summary, str)
        assert summary
        summary.encode("cp1252")

    def test_the_seed_states_are_really_there(self):
        """A seed line that describes a state the seed did not produce is a lie in a demo."""

        module = importlib.import_module(FEATURE_MODULE)

        from dsr.db.audited import AuditedDatabase

        db = AuditedDatabase(":memory:", actor="seed-test")
        try:
            store = RecordStore(db)
            store.create("room", {"name": "Northwind data room"}, record_id="room_a", actor="dana")
            store.create("room", {"name": "Halcyon data room"}, record_id="room_b", actor="dana")
            module.seed(
                db,
                {
                    "room_ids": [("room_a", "Northwind"), ("room_b", "Halcyon")],
                    "now": NOW,
                    "rng": None,
                },
            )
            board = QuoteCurrencyEngine(store, now=lambda: NOW).summary()
        finally:
            db.close()

        assert board["currencies"] == 3
        assert board["price_lists"] == 2
        assert board["price_rows"] == 6
        assert board["quotes"] == 5
        assert board["priced_quotes"] >= 1
        assert board["refused_quotes"] >= 2
        assert board["refusals_by_code"]["34"] >= 1
        assert board["refusals_by_code"]["38"] >= 1
        assert board["custom_currencies"] >= 1
        assert board["base_currency"] == "USD"

    def test_a_seed_with_no_rooms_returns_an_empty_string_rather_than_failing(self):
        module = importlib.import_module(FEATURE_MODULE)

        from dsr.db.audited import AuditedDatabase

        db = AuditedDatabase(":memory:", actor="seed-test")
        try:
            assert module.seed(db, {"room_ids": [], "now": NOW, "rng": None}) == ""
        finally:
            db.close()
