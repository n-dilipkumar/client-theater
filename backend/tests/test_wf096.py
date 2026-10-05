"""WF-096's rules and engine, without a server.

The HTTP surface is in ``test_wf096_http.py``. This file tests the two halves the route table
only translates:

``the pure rules``
    Every function in :mod:`dsr.quote_payment.rules`, one behaviour at a time: the money
    rounding, the amount due, the whole-number quantity rule, the acceptance and payment
    method vocabularies, the derived payment type, the billing frequencies, the effective
    dates, the minimum charge, the tax-ID cap and the state gates.
``the engine``
    Every read and write in :mod:`dsr.quote_payment.engine` against a real (in-memory) audited
    store with a pinned clock, so the invoice schedule is a fact a test can name rather than
    something only a month can reveal.

The one invariant a rule test cannot state
------------------------------------------

The engine patches a quote WF-086 owns. The test that matters is not "it wrote
``hs_status``" but "it wrote *only* the eleven properties it published". So
:class:`RecordingStore` records every patch and the test asserts the union of patched keys is
exactly :data:`~dsr.quote_payment.vocabulary.QUOTE_PROPERTIES_WRITTEN`, which is what stops a
later change from silently widening the write surface to a quote's amounts.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

import pytest
from dsr.quote_payment import inferences, rules, vocabulary as vocab
from dsr.quote_payment.engine import QuotePaymentEngine
from dsr.quote_payment.errors import PaymentRefused, QuoteNotFound, SetupNotFound, StateConflict
from dsr.store import RecordStore

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def make_quote(store: RecordStore, **overrides: Any) -> str:
    """A published USD quote in a room, created the way the demo route creates one."""

    data = {
        "title": "Northwind renewal",
        "currency": "USD",
        "status": "published",
        "company_name": "Northwind Logistics",
    }
    data.update(overrides)
    record = store.create(vocab.QUOTE_COLLECTION, data, room_id="room_a", actor="dana", source="t")
    return record["id"]


def add_line(engine: QuotePaymentEngine, quote_id: str, **line: Any) -> dict[str, Any]:
    body = {"name": "Line", "quantity": 1, "unit_price": 100}
    body.update(line)
    return engine.create_line_item(quote_id, body, source="t", actor="dana")


def paid_engine(store: RecordStore) -> QuotePaymentEngine:
    return QuotePaymentEngine(store, now=lambda: NOW)


class RecordingStore(RecordStore):
    """A store that remembers every patch, so a test can pin a write surface."""

    def __init__(self, db: Any) -> None:
        super().__init__(db)
        self.patches: list[tuple[str, dict[str, Any]]] = []

    def update(self, record_id: str, patch: Any, **kwargs: Any) -> dict[str, Any]:
        self.patches.append((record_id, dict(patch)))
        return super().update(record_id, patch, **kwargs)


# --------------------------------------------------------------------------- #
# Coercion
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, False),
        (True, True),
        (False, False),
        (1, True),
        (0, False),
        ("true", True),
        (" no ", False),
        ("yes", True),
        ("maybe", False),
    ],
)
def test_truthy_reads_a_switch(value: Any, expected: bool) -> None:
    assert rules.truthy(value) is expected


def test_truthy_returns_the_default_for_an_absent_value() -> None:
    assert rules.truthy(None, True) is True
    assert rules.truthy("nonsense", True) is True


def test_as_number_parses_a_decimal_string_rather_than_zeroing_it() -> None:
    """The defect WF-086 records: a string price that silently became zero."""

    assert rules.as_number("1200.50") == 1200.50
    assert rules.as_number("not a number", 7.0) == 7.0
    assert rules.as_number(float("inf"), 3.0) == 3.0
    assert rules.as_number(None, 3.0) == 3.0


def test_as_text_trims_and_defaults() -> None:
    assert rules.as_text("  x ") == "x"
    assert rules.as_text(None, "d") == "d"


def test_money_rounds_half_up() -> None:
    """Binary floats cannot express half-up; ``round(2.675, 2)`` is 2.67."""

    assert rules.money(2.675) == 2.68
    assert rules.money("0.005") == 0.01
    assert rules.money(None) == 0.0


def test_as_date_reads_a_plain_date_and_an_instant() -> None:
    assert rules.as_date("2026-10-05") == date(2026, 10, 5)
    assert rules.as_date("2026-10-05T12:00:00Z") == date(2026, 10, 5)
    assert rules.as_date("not a date") is None
    assert rules.as_date("") is None


def test_add_months_clamps_to_the_last_valid_day() -> None:
    assert rules.add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert rules.add_months(date(2024, 1, 31), 1) == date(2024, 2, 29)
    assert rules.add_months(date(2026, 11, 15), 3) == date(2027, 2, 15)
    assert rules.add_months(date(2026, 1, 15), 12) == date(2027, 1, 15)


# --------------------------------------------------------------------------- #
# The amount due
# --------------------------------------------------------------------------- #


def test_line_amounts_prices_one_line() -> None:
    figures = rules.line_amounts(
        {
            "quantity": 2,
            "unit_price": 10,
            "discount_type": "percentage",
            "discount_value": 10,
            "tax_rate": 20,
        }
    )
    assert figures["subtotal"] == 20.0
    assert figures["discount"] == 2.0
    assert figures["net"] == 18.0
    assert figures["tax"] == 3.6
    assert figures["total"] == 21.6


def test_line_amounts_takes_a_currency_discount_off_the_subtotal() -> None:
    figures = rules.line_amounts(
        {"quantity": 1, "unit_price": 10, "discount_type": "currency", "discount_value": 4}
    )
    assert figures["discount"] == 4.0
    assert figures["total"] == 6.0


def test_line_amounts_clamps_a_percentage_above_one_hundred() -> None:
    figures = rules.line_amounts({"unit_price": 10, "discount_value": 150})
    assert figures["discount"] == 10.0
    assert figures["total"] == 0.0


def test_amount_due_sums_the_rounded_lines() -> None:
    lines = [
        {"quantity": 2, "unit_price": 10},
        {"quantity": 1, "unit_price": 5, "tax_rate": 10},
    ]
    due = rules.amount_due(lines)
    assert due["subtotal"] == 25.0
    assert due["tax"] == 0.5
    assert due["total"] == 25.5
    assert due["line_count"] == 2


def test_amount_due_of_nothing_is_zero() -> None:
    assert rules.amount_due([])["total"] == 0.0
    assert rules.amount_due([])["line_count"] == 0


# --------------------------------------------------------------------------- #
# Quantities
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("value", "expected"),
    [(3, True), (3.0, True), ("3", True), (1.5, False), (None, True), (True, True)],
)
def test_is_whole_quantity(value: Any, expected: bool) -> None:
    assert rules.is_whole_quantity(value) is expected


def test_whole_number_rule_fires_only_when_billing_is_on() -> None:
    lines = [{"name": "Consulting days", "quantity": 1.5}]
    rules.assert_whole_quantities(lines, billing_enabled=False, payment_enabled=False)
    with pytest.raises(PaymentRefused) as raised:
        rules.assert_whole_quantities(lines, billing_enabled=True, payment_enabled=False)
    assert raised.value.reason == vocab.REASON_FRACTIONAL_QUANTITY_WITH_BILLING
    assert "Consulting days" in raised.value.errors["quantity"]


# --------------------------------------------------------------------------- #
# Acceptance methods
# --------------------------------------------------------------------------- #


def test_normalise_acceptance_method_reads_a_label_and_a_token() -> None:
    assert rules.normalise_acceptance_method("Accept without signature") == "clickwrap"
    assert rules.normalise_acceptance_method("E-Signature") == "esignature"
    assert rules.normalise_acceptance_method("print-and-sign") == "print_and_sign"
    assert rules.normalise_acceptance_method(None) == "clickwrap"


def test_normalise_acceptance_method_refuses_an_unknown_value() -> None:
    with pytest.raises(PaymentRefused) as raised:
        rules.normalise_acceptance_method("carrier pigeon")
    assert raised.value.reason == vocab.REASON_UNKNOWN_ACCEPTANCE_METHOD


def test_print_and_sign_is_refused_with_online_payments() -> None:
    with pytest.raises(PaymentRefused) as raised:
        rules.assert_acceptance_method("print_and_sign", online_payments=True)
    assert raised.value.reason == vocab.REASON_PRINT_AND_SIGN_WITH_ONLINE_PAYMENTS
    assert (
        rules.assert_acceptance_method("print_and_sign", online_payments=False) == "print_and_sign"
    )


def test_clickwrap_route_refuses_e_signature_and_names_wf095() -> None:
    with pytest.raises(PaymentRefused) as raised:
        rules.assert_clickwrap("esignature")
    assert raised.value.reason == vocab.REASON_E_SIGNATURE_BELONGS_TO_WF095
    with pytest.raises(PaymentRefused) as raised:
        rules.assert_clickwrap("print_and_sign")
    assert raised.value.reason == vocab.REASON_PRINT_AND_SIGN_WITH_ONLINE_PAYMENTS
    assert rules.assert_clickwrap("clickwrap") == "clickwrap"


# --------------------------------------------------------------------------- #
# Payment methods and type
# --------------------------------------------------------------------------- #


def test_normalise_payment_method_reads_the_five_and_their_aliases() -> None:
    assert rules.normalise_payment_method("card") == vocab.PAYMENT_CARD
    assert rules.normalise_payment_method("ACH") == vocab.PAYMENT_ACH
    with pytest.raises(PaymentRefused) as raised:
        rules.normalise_payment_method("gold bullion")
    assert raised.value.reason == vocab.REASON_UNKNOWN_PAYMENT_METHOD


def test_validate_payment_methods_defaults_and_deduplicates() -> None:
    assert rules.validate_payment_methods(None) == (vocab.PAYMENT_CARD,)
    assert rules.validate_payment_methods(["ACH", "ach", "SEPA"]) == ("ACH", "SEPA")


def test_payment_type_is_derived_and_ignores_the_seller() -> None:
    assert rules.derive_payment_type({"hs_payment_type": "BYO_STRIPE"}) == "HUBSPOT"
    assert rules.derive_payment_type({"stripe_account_id": "acct_123"}) == "BYO_STRIPE"


# --------------------------------------------------------------------------- #
# Billing frequency
# --------------------------------------------------------------------------- #


def test_validate_billing_frequency_defaults_to_one_time_and_reads_aliases() -> None:
    assert rules.validate_billing_frequency(None) == "one_time"
    assert rules.validate_billing_frequency("Yearly") == "annual"
    with pytest.raises(PaymentRefused) as raised:
        rules.validate_billing_frequency("fortnightly")
    assert raised.value.reason == vocab.REASON_UNKNOWN_BILLING_FREQUENCY


def test_classify_lines_splits_per_line() -> None:
    lines = [
        {"id": "l1", "name": "Setup", "quantity": 1, "unit_price": 100},
        {
            "id": "l2",
            "name": "Seats",
            "quantity": 1,
            "unit_price": 50,
            "billing_frequency": "monthly",
        },
    ]
    split = rules.classify_lines(lines)
    assert [row["line_id"] for row in split["one_time"]] == ["l1"]
    assert [row["line_id"] for row in split["recurring"]] == ["l2"]


# --------------------------------------------------------------------------- #
# Effective date
# --------------------------------------------------------------------------- #


def test_effective_date_modes_resolve_to_concrete_dates() -> None:
    agreement = date(2026, 10, 5)
    assert rules.effective_date_on(rules.validate_effective_date({}), agreement) == "2026-10-05"
    custom = rules.validate_effective_date(
        {"effective_date_mode": "custom_date", "effective_date": "2026-11-01"}
    )
    assert rules.effective_date_on(custom, agreement) == "2026-11-01"
    days = rules.validate_effective_date(
        {"effective_date_mode": "delayed_days", "effective_delay_days": 30}
    )
    assert rules.effective_date_on(days, agreement) == "2026-11-04"
    months = rules.validate_effective_date(
        {"effective_date_mode": "delayed_months", "effective_delay_months": 2}
    )
    assert rules.effective_date_on(months, agreement) == "2026-12-05"


def test_effective_date_refuses_a_bad_mode_and_a_missing_date() -> None:
    with pytest.raises(PaymentRefused) as raised:
        rules.validate_effective_date({"effective_date_mode": "someday"})
    assert raised.value.reason == vocab.REASON_INVALID_EFFECTIVE_DATE
    with pytest.raises(PaymentRefused):
        rules.validate_effective_date({"effective_date_mode": "custom_date"})
    with pytest.raises(PaymentRefused):
        rules.validate_effective_date(
            {"effective_date_mode": "delayed_days", "effective_delay_days": 1.5}
        )


def test_a_bare_effective_date_means_custom_date() -> None:
    config = rules.validate_effective_date({"effective_date": "2027-01-01"})
    assert config["mode"] == "custom_date"


# --------------------------------------------------------------------------- #
# Collection process, net terms and the minimum charge
# --------------------------------------------------------------------------- #


def test_collection_process_and_net_terms_validate() -> None:
    assert rules.validate_collection_process(None) == "AUTO_PAYMENTS"
    assert rules.validate_net_payment_terms("net_30") == "NET_30"
    with pytest.raises(PaymentRefused) as raised:
        rules.validate_collection_process("MANUAL")
    assert raised.value.reason == vocab.REASON_UNKNOWN_COLLECTION_PROCESS
    with pytest.raises(PaymentRefused) as raised:
        rules.validate_net_payment_terms("NET_90")
    assert raised.value.reason == vocab.REASON_UNKNOWN_NET_PAYMENT_TERMS


def test_minimum_charge_is_strict() -> None:
    assert rules.meets_minimum_charge(0.51) is True
    assert rules.meets_minimum_charge(0.50) is False
    assert rules.charge_outcome(0.50)["outcome"] == vocab.OUTCOME_DECLINED
    assert rules.charge_outcome(0.50)["reason"] == vocab.REASON_AMOUNT_DUE_BELOW_MINIMUM
    assert rules.charge_outcome(0.51)["outcome"] == vocab.OUTCOME_RECORDED
    assert rules.minimum_charge_for("EUR") == 0.50
    assert vocab.minimum_charge("usd") == 0.50


# --------------------------------------------------------------------------- #
# Tax IDs and the state gates
# --------------------------------------------------------------------------- #


def test_normalise_tax_id_refuses_an_empty_value() -> None:
    assert rules.normalise_tax_id(" US-001 ") == "US-001"
    with pytest.raises(PaymentRefused) as raised:
        rules.normalise_tax_id("  ")
    assert raised.value.reason == vocab.REASON_TAX_ID_EMPTY


def test_tax_id_capacity_is_three() -> None:
    rules.assert_tax_id_capacity(2)
    with pytest.raises(PaymentRefused) as raised:
        rules.assert_tax_id_capacity(3)
    assert raised.value.reason == vocab.REASON_TAX_ID_LIMIT_REACHED


def test_state_gates_carry_their_reason_codes() -> None:
    with pytest.raises(StateConflict) as raised:
        rules.assert_can_accept({"id": "a"}, quote_id="q")
    assert raised.value.reason == vocab.REASON_QUOTE_ALREADY_ACCEPTED
    rules.assert_can_accept(None)
    with pytest.raises(StateConflict) as raised:
        rules.assert_can_void(True, quote_id="q")
    assert raised.value.reason == vocab.REASON_IRREVERSIBLE_AFTER_ACCEPTANCE
    with pytest.raises(StateConflict) as raised:
        rules.assert_can_pay(False, quote_id="q")
    assert raised.value.reason == vocab.REASON_PAYMENT_REQUIRES_ACCEPTANCE
    with pytest.raises(StateConflict) as raised:
        rules.assert_no_charge([{"id": "c"}], quote_id="q")
    assert raised.value.reason == vocab.REASON_CHARGE_ALREADY_RECORDED


# --------------------------------------------------------------------------- #
# Publishing
# --------------------------------------------------------------------------- #


def test_validate_publish_defaults_the_configuration() -> None:
    config = rules.validate_publish({}, [{"quantity": 1}], currency="usd")
    assert config["acceptance_method"] == "clickwrap"
    assert config["billing_enabled"] is True
    assert config["payment_enabled"] is True
    assert config["payment_type"] == "HUBSPOT"
    assert config["allowed_payment_methods"] == ["CREDIT_OR_DEBIT_CARD"]
    assert config["net_payment_terms"] == "NET_30"
    assert config["payment_status"] == "PENDING"


def test_validate_publish_gathers_every_error() -> None:
    with pytest.raises(PaymentRefused) as raised:
        rules.validate_publish(
            {"acceptance_method": "carrier pigeon", "allowed_payment_methods": ["gold"]},
            [{"quantity": 1}],
        )
    assert set(raised.value.errors) == {"acceptance_method", "payment_method"}


def test_validate_publish_refuses_print_and_sign_and_fractional_lines_and_empty_quotes() -> None:
    with pytest.raises(PaymentRefused) as raised:
        rules.validate_publish({"acceptance_method": "print_and_sign"}, [{"quantity": 1}])
    assert raised.value.reason == vocab.REASON_PRINT_AND_SIGN_WITH_ONLINE_PAYMENTS
    with pytest.raises(PaymentRefused) as raised:
        rules.validate_publish({}, [{"name": "Days", "quantity": 1.5}])
    assert raised.value.reason == vocab.REASON_FRACTIONAL_QUANTITY_WITH_BILLING
    with pytest.raises(PaymentRefused) as raised:
        rules.validate_publish({}, [])
    assert raised.value.reason == vocab.REASON_QUOTE_IS_EMPTY


# --------------------------------------------------------------------------- #
# Invoices
# --------------------------------------------------------------------------- #


def test_first_invoice_is_sent_today_whatever_its_schedule() -> None:
    lines = [{"id": "l1", "quantity": 2, "unit_price": 10}]
    invoice = rules.first_invoice(lines, currency="USD", today=date(2026, 10, 5))
    assert invoice["kind"] == "first"
    assert invoice["status"] == "sent"
    assert invoice["amount"] == 20.0
    assert invoice["invoice_date"] == "2026-10-05"
    assert invoice["send_on"] == "2026-10-05"
    assert invoice["line_item_ids"] == ["l1"]


def test_next_invoice_dates_skip_the_present_and_lead_by_ten_days() -> None:
    schedule = rules.next_invoice_dates("monthly", date(2026, 1, 1), today=date(2026, 3, 15))
    assert [slot["invoice_date"] for slot in schedule] == [
        "2026-04-01",
        "2026-05-01",
        "2026-06-01",
    ]
    assert schedule[0]["send_on"] == "2026-03-22"


def test_next_invoice_dates_for_a_one_time_line_is_empty() -> None:
    assert rules.next_invoice_dates("one_time", date(2026, 1, 1), today=date(2026, 1, 1)) == []


# --------------------------------------------------------------------------- #
# The engine
# --------------------------------------------------------------------------- #


def test_quote_lines_are_ordered_and_priced(store: RecordStore) -> None:
    engine = paid_engine(store)
    quote_id = make_quote(store)
    add_line(engine, quote_id, name="Second", position=2)
    add_line(engine, quote_id, name="First", position=1)
    assert [line["name"] for line in engine.quote_lines(quote_id)] == ["First", "Second"]


def test_create_line_item_needs_a_name(store: RecordStore) -> None:
    engine = paid_engine(store)
    quote_id = make_quote(store)
    with pytest.raises(PaymentRefused) as raised:
        engine.create_line_item(quote_id, {}, source="t")
    assert "name" in raised.value.errors


def test_publish_writes_the_setup_and_patches_only_the_published_properties(
    store: RecordStore,
) -> None:
    recording = RecordingStore(store.db)
    engine = paid_engine(recording)
    quote_id = make_quote(recording)
    add_line(engine, quote_id, name="Seats")

    picture = engine.publish(
        quote_id,
        {"acceptance_method": "clickwrap", "allowed_payment_methods": ["ACH"]},
        source="t",
        actor="dana",
    )
    assert picture["setup"]["acceptance_method"] == "clickwrap"
    assert picture["setup"]["hs_payment_status"] == "PENDING"
    assert picture["setup"]["payment_type"] == "HUBSPOT"

    engine.accept(quote_id, {}, source="t", actor="ada")

    patched = {
        key for record_id, patch in recording.patches if record_id == quote_id for key in patch
    }
    assert patched == set(vocab.QUOTE_PROPERTIES_WRITTEN)
    quote = store.get(quote_id)
    assert quote["data"]["hs_status"] == "ACCEPTED"
    assert quote["data"]["hs_payment_status"] == "PENDING"


def test_accept_creates_the_first_invoice_and_the_subscription_schedule(store: RecordStore) -> None:
    engine = paid_engine(store)
    quote_id = make_quote(store)
    add_line(engine, quote_id, name="Setup", unit_price=1200, position=0)
    add_line(
        engine,
        quote_id,
        name="Seats",
        quantity=25,
        unit_price=12,
        billing_frequency="monthly",
        position=1,
    )
    engine.publish(quote_id, {"acceptance_method": "clickwrap"}, source="t", actor="dana")
    result = engine.accept(quote_id, {"accepted_by": "Ada Byron"}, source="t", actor="ada")

    assert result["acceptance"]["accepted_by"] == "Ada Byron"
    assert result["acceptance"]["method"] == "clickwrap"
    assert result["quote"]["accepted"] is True
    assert [sub["frequency"] for sub in result["subscriptions"]] == ["monthly"]

    invoices = engine.invoices(quote_id)
    assert invoices[0]["kind"] == "first"
    assert invoices[0]["amount"] == 1500.0
    assert [i["status"] for i in invoices[1:]] == ["scheduled", "scheduled", "scheduled"]
    assert invoices[1]["send_on"] == ((NOW.date() + timedelta(days=21)).isoformat())


def test_accept_names_an_anonymous_buyer(store: RecordStore) -> None:
    engine = paid_engine(store)
    quote_id = make_quote(store)
    add_line(engine, quote_id)
    engine.publish(quote_id, {}, source="t")
    result = engine.accept(quote_id, {}, source="t")
    assert result["acceptance"]["accepted_by"] == vocab.CLICKWRAP_ANONYMOUS_BUYER
    assert (
        store.get(quote_id)["data"]["hs_clickwrap_accepted_by"] == vocab.CLICKWRAP_ANONYMOUS_BUYER
    )


def test_a_quote_can_be_accepted_once(store: RecordStore) -> None:
    engine = paid_engine(store)
    quote_id = make_quote(store)
    add_line(engine, quote_id)
    engine.publish(quote_id, {}, source="t")
    engine.accept(quote_id, {}, source="t")
    with pytest.raises(StateConflict) as raised:
        engine.accept(quote_id, {}, source="t")
    assert raised.value.reason == vocab.REASON_QUOTE_ALREADY_ACCEPTED


def test_accept_requires_a_published_setup(store: RecordStore) -> None:
    engine = paid_engine(store)
    quote_id = make_quote(store)
    with pytest.raises(SetupNotFound) as raised:
        engine.accept(quote_id, {}, source="t")
    assert raised.value.reason == vocab.REASON_QUOTE_NOT_PROVISIONED


def test_charge_is_recorded_above_the_minimum(store: RecordStore) -> None:
    engine = paid_engine(store)
    quote_id = make_quote(store)
    add_line(engine, quote_id, unit_price=40)
    engine.publish(quote_id, {"allowed_payment_methods": ["ACH"]}, source="t")
    engine.accept(quote_id, {}, source="t")
    result = engine.record_charge(quote_id, {"payment_method": "ACH"}, source="t", actor="ada")
    assert result["outcome"] == vocab.OUTCOME_RECORDED
    assert result["charge"]["amount"] == 40.0
    assert result["charge"]["settled_at"] is not None


def test_charge_below_the_minimum_is_a_declined_outcome(store: RecordStore) -> None:
    engine = paid_engine(store)
    quote_id = make_quote(store)
    add_line(engine, quote_id, unit_price=0.2)
    engine.publish(quote_id, {}, source="t")
    engine.accept(quote_id, {}, source="t")
    result = engine.record_charge(quote_id, {}, source="t")
    assert result["outcome"] == vocab.OUTCOME_DECLINED
    assert result["reason"] == vocab.REASON_AMOUNT_DUE_BELOW_MINIMUM
    assert result["charge"]["settled_at"] is None


def test_a_charge_before_acceptance_is_refused(store: RecordStore) -> None:
    engine = paid_engine(store)
    quote_id = make_quote(store)
    add_line(engine, quote_id, unit_price=40)
    engine.publish(quote_id, {}, source="t")
    with pytest.raises(StateConflict) as raised:
        engine.record_charge(quote_id, {}, source="t")
    assert raised.value.reason == vocab.REASON_PAYMENT_REQUIRES_ACCEPTANCE


def test_a_second_charge_is_refused(store: RecordStore) -> None:
    engine = paid_engine(store)
    quote_id = make_quote(store)
    add_line(engine, quote_id, unit_price=40)
    engine.publish(quote_id, {}, source="t")
    engine.accept(quote_id, {}, source="t")
    engine.record_charge(quote_id, {}, source="t")
    with pytest.raises(StateConflict) as raised:
        engine.record_charge(quote_id, {}, source="t")
    assert raised.value.reason == vocab.REASON_CHARGE_ALREADY_RECORDED


def test_a_disallowed_payment_method_is_refused(store: RecordStore) -> None:
    engine = paid_engine(store)
    quote_id = make_quote(store)
    add_line(engine, quote_id, unit_price=40)
    engine.publish(quote_id, {"allowed_payment_methods": ["ACH"]}, source="t")
    engine.accept(quote_id, {}, source="t")
    with pytest.raises(PaymentRefused) as raised:
        engine.record_charge(quote_id, {"payment_method": "SEPA"}, source="t")
    assert raised.value.reason == vocab.REASON_PAYMENT_METHOD_NOT_ALLOWED


def test_tax_ids_are_capped_at_three(store: RecordStore) -> None:
    engine = paid_engine(store)
    quote_id = make_quote(store)
    add_line(engine, quote_id)
    engine.publish(quote_id, {}, source="t")
    for index in range(3):
        result = engine.add_tax_id(quote_id, {"value": f"US-{index}"}, source="t")
    assert result["count"] == 3
    with pytest.raises(PaymentRefused) as raised:
        engine.add_tax_id(quote_id, {"value": "US-4"}, source="t")
    assert raised.value.reason == vocab.REASON_TAX_ID_LIMIT_REACHED


def test_void_and_delete_are_refused_after_acceptance(store: RecordStore) -> None:
    engine = paid_engine(store)
    quote_id = make_quote(store)
    add_line(engine, quote_id)
    engine.publish(quote_id, {}, source="t")
    engine.accept(quote_id, {}, source="t")
    with pytest.raises(StateConflict):
        engine.void(quote_id, source="t")
    with pytest.raises(StateConflict):
        engine.delete(quote_id, source="t")


def test_void_and_delete_work_before_acceptance(store: RecordStore) -> None:
    engine = paid_engine(store)
    voided = make_quote(store)
    add_line(engine, voided)
    engine.publish(voided, {}, source="t")
    assert engine.void(voided, source="t")["hs_status"] == vocab.QUOTE_STATUS_VOID

    deleted = make_quote(store)
    add_line(engine, deleted)
    engine.publish(deleted, {}, source="t")
    assert engine.delete(deleted, source="t")["deleted"] is True
    with pytest.raises(QuoteNotFound):
        engine.quote_view(deleted)


def test_publish_is_refused_after_acceptance(store: RecordStore) -> None:
    engine = paid_engine(store)
    quote_id = make_quote(store)
    add_line(engine, quote_id)
    engine.publish(quote_id, {}, source="t")
    engine.accept(quote_id, {}, source="t")
    with pytest.raises(StateConflict) as raised:
        engine.publish(quote_id, {}, source="t")
    assert raised.value.reason == vocab.REASON_IRREVERSIBLE_AFTER_ACCEPTANCE


def test_quote_detail_works_without_a_setup(store: RecordStore) -> None:
    engine = paid_engine(store)
    quote_id = make_quote(store)
    detail = engine.quote_detail(quote_id)
    assert detail["setup"] is None
    assert detail["invoices"] == []


def test_reads_of_a_missing_quote_answer_not_found(store: RecordStore) -> None:
    engine = paid_engine(store)
    with pytest.raises(QuoteNotFound):
        engine.quote_view("absent")
    with pytest.raises(QuoteNotFound):
        engine.publish("absent", {}, source="t")


def test_summary_counts_the_board(store: RecordStore) -> None:
    engine = paid_engine(store)
    quote_id = make_quote(store)
    add_line(engine, quote_id, unit_price=40)
    engine.publish(quote_id, {}, source="t")
    engine.accept(quote_id, {}, source="t")
    engine.record_charge(quote_id, {}, source="t")

    declined = make_quote(store)
    add_line(engine, declined, unit_price=0.2)
    engine.publish(declined, {}, source="t")
    engine.accept(declined, {}, source="t")
    engine.record_charge(declined, {}, source="t")

    summary = engine.summary()
    assert summary["setups"] == 2
    assert summary["accepted"] == 2
    assert summary["charges_recorded"] == 1
    assert summary["charges_declined"] == 1
    assert summary["charged_total"] == 40.0
    assert summary["pending_payment"] == 1
    assert summary["by_payment_type"][vocab.PAYMENT_TYPE_HUBSPOT] == 2
    assert summary["minimum_charge_usd"] == 0.50


def test_summary_skips_a_setup_whose_quote_was_deleted(store: RecordStore) -> None:
    engine = paid_engine(store)
    quote_id = make_quote(store)
    add_line(engine, quote_id)
    engine.publish(quote_id, {}, source="t")
    store.delete(quote_id, actor="dana", source="t")
    assert engine.summary()["setups"] == 0


def test_vocabulary_and_decisions_are_published(store: RecordStore) -> None:
    engine = paid_engine(store)
    published = engine.vocabulary()
    assert published["acceptance_methods"] == list(vocab.ACCEPTANCE_METHODS)
    assert published["minimum_charge_usd"] == 0.50
    decisions = engine.decisions()
    assert {entry["question"] for entry in decisions} == {
        entry["question"] for entry in inferences.decision_list()
    }


# --------------------------------------------------------------------------- #
# The research is recorded, not asserted
# --------------------------------------------------------------------------- #


def test_every_decision_names_at_least_two_options_and_a_choice() -> None:
    for key, entry in inferences.DECISIONS.items():
        assert len(entry["options"]) >= 2, key
        assert entry["chosen"] in entry["options"], key
        assert entry["rejected_because"], key


def test_decisions_with_an_audit_id_carry_a_probability() -> None:
    for key, entry in inferences.DECISIONS.items():
        if entry["audit_id"]:
            assert isinstance(entry["confidence"], float), key
            assert 0.0 <= entry["confidence"] <= 1.0, key


def test_reason_codes_are_unique_strings() -> None:
    codes = vocab.REASON_CODES
    assert len(codes) == len(set(codes))
    assert all(isinstance(code, str) and code for code in codes)


def test_seed_creates_the_states_and_returns_a_cp1252_string(db: Any) -> None:
    """The seeder prints this to a cp1252 console; one arrow broke the whole run."""

    from dsr.features import wf096_accept_a_quote_without_a_signature_and_take as feature

    summary = feature.seed(db, {"room_ids": [("room_a", "Northwind")], "now": NOW})
    summary.encode("cp1252")
    assert "published quotes" in summary
    assert "1 charged" in summary
    assert "1 declined" in summary
    assert "1 publish refused" in summary
    assert "1 tax ID refused" in summary
