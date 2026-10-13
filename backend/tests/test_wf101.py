"""WF-101 domain tests: the rules, the engine and the audit trail.

The file builds its own records and its own clock, so it passes alone and under
pytest-xdist without depending on another module's fixture. The clock is frozen
at :data:`FROZEN`, because the conversion stamps a date and a rule that reads
"today" must not be tested against a date that moves.
"""

from __future__ import annotations

import importlib
import pathlib
from datetime import datetime, timezone

import pytest
from dsr.order_conversion import OrderConversionEngine, rules, vocabulary as vocab
from dsr.order_conversion.errors import OrderConflict, OrderError, OrderNotFound
from dsr.store import RecordStore

FROZEN = datetime(2026, 4, 1, 9, 0, tzinfo=timezone.utc)
FEATURE_MODULE = "dsr.features.wf101_convert_an_accepted_quote_into_an_order"
SOURCE = "POST /api/wf-101/quotes/q_1/order"


# --------------------------------------------------------------------------- #
# Fixtures and helpers
# --------------------------------------------------------------------------- #


@pytest.fixture
def engine(db) -> OrderConversionEngine:
    return OrderConversionEngine(RecordStore(db), now=lambda: FROZEN)


@pytest.fixture
def store(db) -> RecordStore:
    return RecordStore(db)


def enable(engine: OrderConversionEngine, **setting):
    """Turn creation on for room_1 with the given overrides."""

    base = {
        vocab.FIELD_CREATE_ORDERS: True,
        vocab.FIELD_SALES_ORDER_PROCESSING: False,
        vocab.FIELD_ISPRICELOCKED_DEFAULT: False,
    }
    base.update(setting)
    return engine.update_settings(base, room_id="room_1", actor="dana", source="test")


def make_quote(store: RecordStore, *, lines: int = 2, **overrides):
    """A published, accepted quote with ``lines`` line items."""

    payload = {
        "title": "Northwind annual licence",
        "status": "published",
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
        "shipping_amount": 120.0,
        "totals": {"total": 14500.0, "total_contract_value": 14500.0},
    }
    payload.update(overrides)
    quote = store.create("wf086_quote", payload, room_id="room_1", actor="dana", source="test")
    catalogue = [
        ("Platform licence", "SKU-01", 1, 12000.0, "catalog"),
        ("Onboarding", "SKU-02", 2, 1250.0, "manual"),
    ]
    for position, (name, sku, qty, price, source) in enumerate(catalogue[:lines]):
        store.create(
            "wf086_line_item",
            {
                "quote_id": quote["id"],
                "position": position,
                "name": name,
                "sku": sku,
                "quantity": qty,
                "unit_price": price,
                "tax_rate": 21.0,
                "price_source": source,
                "amounts": {},
            },
            room_id="room_1",
            actor="dana",
            source="test",
        )
    return quote["id"]


def add_catalogue(store: RecordStore, *, price: float = 500.0) -> dict[str, str]:
    """A product, a price book and a price-book row, for a reprice to read."""

    product = store.create(
        "wf087_product",
        {
            "name": "Platform licence",
            "sku": "SKU-01",
            "pricing_method": "flat",
            "prices": {"EUR": 1000.0},
            "active": True,
        },
        room_id="room_1",
        actor="dana",
        source="test",
    )
    book = store.create(
        "wf087_price_book",
        {"name": "Enterprise EUR", "supported_currencies": ["EUR"], "active": True},
        room_id="room_1",
        actor="dana",
        source="test",
    )
    store.create(
        "wf087_price_book_item",
        {
            "price_book_id": book["id"],
            "product_id": product["id"],
            "state": "active",
            "prices": {"EUR": price},
        },
        room_id="room_1",
        actor="dana",
        source="test",
    )
    return {"product_id": product["id"], "book_id": book["id"]}


def convert_ok(engine: OrderConversionEngine, quote_id: str):
    return engine.convert(quote_id, {}, actor="dana", source=SOURCE)


def catalog_line_id(store: RecordStore, quote_id: str) -> str:
    """The quote's catalogue-priced line, named by position rather than by find order."""

    rows = store.find("wf086_line_item", {"quote_id": quote_id})
    return next(row["id"] for row in rows if (row["data"] or {}).get("price_source") == "catalog")


# --------------------------------------------------------------------------- #
# Coercion
# --------------------------------------------------------------------------- #


def test_coercion_helpers_accept_the_spellings_a_record_may_carry():
    assert rules.as_text(None) == ""
    assert rules.as_text("  x  ") == "x"
    assert rules.as_number("2.5") == 2.5
    assert rules.as_number("nope") == 0.0
    assert rules.as_bool("yes") is True
    assert rules.as_bool("off") is False
    assert rules.as_bool(1) is True
    assert rules.as_bool(None) is False
    assert rules.as_int("3") == 3
    assert rules.as_int("x") is None


# --------------------------------------------------------------------------- #
# The input state: activated and accepted
# --------------------------------------------------------------------------- #


def test_a_published_quote_is_activated():
    activated, marker = rules.is_activated({"status": "published"})
    assert activated is True
    assert marker == "status=published"


@pytest.mark.parametrize("status", ["active", "activated", "ACTIVATED"])
def test_the_longer_activation_spellings_are_read(status):
    activated, _ = rules.is_activated({"status": status})
    assert activated is True


def test_an_activation_flag_activates_a_draft_quote():
    activated, marker = rules.is_activated({"status": "draft", "activated": True})
    assert activated is True
    assert marker == "activated"


def test_a_draft_quote_is_not_activated():
    activated, _ = rules.is_activated({"status": "draft"})
    assert activated is False


@pytest.mark.parametrize(
    "quote",
    [
        {"hs_status": "ACCEPTED"},
        {"hs_status": "accepted"},
        {"status": "accepted"},
        {"accepted": True},
        {"accepted_at": "2026-04-01"},
        {"accepted_by": "buyer@northwind.example"},
    ],
)
def test_each_acceptance_marker_is_read(quote):
    accepted, marker = rules.is_accepted(quote)
    assert accepted is True
    assert marker


def test_a_quote_with_no_acceptance_marker_is_not_accepted():
    accepted, marker = rules.is_accepted({"status": "published"})
    assert accepted is False
    assert marker == ""


def test_quote_state_reports_all_four_facts():
    state = rules.quote_state({"status": "published", "hs_status": "ACCEPTED"})
    assert state == {
        "activated": True,
        "activated_by": "status=published",
        "accepted": True,
        "accepted_by": "hs_status",
        "convertible": True,
    }


# --------------------------------------------------------------------------- #
# The price-lock rule: all four combinations of the two settings
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "integration,table_default,expected",
    [
        (False, False, True),
        (False, True, True),
        (True, False, False),
        (True, True, True),
    ],
)
def test_price_lock_all_four_combinations(integration, table_default, expected):
    lock = rules.price_lock_for(
        {
            vocab.FIELD_SALES_ORDER_PROCESSING: integration,
            vocab.FIELD_ISPRICELOCKED_DEFAULT: table_default,
        }
    )
    assert lock["locked"] is expected
    assert lock[vocab.FIELD_PRICES_LOCKED] == ("Yes" if expected else "No")


def test_price_lock_names_its_branch():
    off = rules.price_lock_for({vocab.FIELD_SALES_ORDER_PROCESSING: False})
    assert off["branch"] == "integration_disabled"
    assert off["locked"] is True
    on = rules.price_lock_for({vocab.FIELD_SALES_ORDER_PROCESSING: True})
    assert on["branch"] == "table_default"
    assert on["locked"] is False


def test_price_lock_carries_the_quoted_rule():
    lock = rules.price_lock_for({})
    assert "ispricelocked" in lock["quote"]


# --------------------------------------------------------------------------- #
# The refusal order
# --------------------------------------------------------------------------- #


def test_a_missing_quote_is_refused_first():
    assert rules.conversion_refusal(None, {vocab.FIELD_CREATE_ORDERS: True}) == (
        vocab.REASON_QUOTE_NOT_FOUND
    )


def test_creation_disabled_is_refused_before_activation():
    refusal = rules.conversion_refusal({"status": "draft"}, {vocab.FIELD_CREATE_ORDERS: False})
    assert refusal == vocab.REASON_CREATION_DISABLED


def test_an_unactivated_quote_is_refused():
    refusal = rules.conversion_refusal(
        {"status": "draft", "hs_status": "ACCEPTED"}, {vocab.FIELD_CREATE_ORDERS: True}
    )
    assert refusal == vocab.REASON_QUOTE_NOT_ACTIVATED


def test_the_rule_refuses_an_unaccepted_quote():
    refusal = rules.conversion_refusal({"status": "published"}, {vocab.FIELD_CREATE_ORDERS: True})
    assert refusal == vocab.REASON_QUOTE_NOT_ACCEPTED


def test_an_already_converted_quote_is_refused():
    refusal = rules.conversion_refusal(
        {"status": "published", "hs_status": "ACCEPTED"},
        {vocab.FIELD_CREATE_ORDERS: True},
        already_converted=True,
    )
    assert refusal == vocab.REASON_ALREADY_CONVERTED


def test_a_convertible_quote_is_allowed():
    refusal = rules.conversion_refusal(
        {"status": "published", "hs_status": "ACCEPTED"}, {vocab.FIELD_CREATE_ORDERS: True}
    )
    assert refusal is None


# --------------------------------------------------------------------------- #
# The seven clone components
# --------------------------------------------------------------------------- #


def test_there_are_seven_clone_components():
    assert len(vocab.CLONE_COMPONENTS) == 7


def test_order_payload_carries_every_clone_component():
    quote = {
        "title": "T",
        "currency": "EUR",
        "account": "Northwind",
        "price_list": "book_1",
        "billing_address": "1 Way",
        "shipping_method": "courier",
        "totals": {"total": 10.0},
        "payment_schedule": [],
    }
    quote_lines = [{"name": "L", "quantity": 2, "unit_price": 5.0, "sku": "S1"}]
    payload = rules.order_payload(
        quote,
        "q_1",
        quote_lines,
        price_lock={"locked": True, "branch": "integration_disabled", "reason": "x"},
        won_on="2026-04-01",
        description="From the accepted quote.",
        converted_at="2026-04-01T09:00:00",
        today="2026-04-01",
    )
    assert payload["cloned_components"] == list(vocab.CLONE_COMPONENTS)
    assert payload["price_list"] == "book_1"
    assert payload["currency"] == "EUR"
    assert payload["products"][0]["sku"] == "S1"
    assert payload["addresses"]["billing"] == "1 Way"
    assert payload["shipping"]["shipping_method"] == "courier"
    assert payload["totals"]["total"] == 10.0
    assert payload[vocab.FIELD_IS_PRICE_LOCKED] is True


def test_order_line_marks_a_typed_price_as_overridden():
    manual = rules.order_line_from_quote(
        {"name": "L", "quantity": 1, "unit_price": 5, "price_source": "manual"}, 0
    )
    catalog = rules.order_line_from_quote(
        {"name": "L", "quantity": 1, "unit_price": 5, "price_source": "catalog"}, 0
    )
    assert manual["price_overridden"] is True
    assert catalog["price_overridden"] is False


def test_order_line_keeps_the_quote_line_id_as_provenance_only():
    line = rules.order_line_from_quote(
        {"id": "ql_1", "name": "L", "quantity": 1, "unit_price": 5}, 0
    )
    assert line["source_quote_line_item_id"] == "ql_1"
    assert line["amounts"]["total"] == 5.0


# --------------------------------------------------------------------------- #
# The conversion end to end
# --------------------------------------------------------------------------- #


def test_converting_creates_an_order_with_its_lines(engine, store):
    quote_id = make_quote(store)
    enable(engine)
    result = convert_ok(engine, quote_id)
    assert result["created"] is True
    assert result["cloned_line_items"] == 2
    order = result["order"]
    assert order["source_quote_id"] == quote_id
    assert order["currency"] == "EUR"
    assert order["price_list"] == "book_1"
    assert order["line_item_count"] == 2
    assert order["status"] == "open"


def test_conversion_with_the_integration_off_locks_the_price(engine, store):
    quote_id = make_quote(store)
    enable(engine, enable_sales_order_processing_integration=False)
    order = convert_ok(engine, quote_id)["order"]
    assert order["locked"] is True
    assert order["prices_locked"] == "Yes"
    assert order["pricing_mode"] == vocab.PRICING_MODE_LOCKED


def test_conversion_with_the_integration_on_takes_the_table_default(engine, store):
    quote_id = make_quote(store)
    enable(
        engine,
        enable_sales_order_processing_integration=True,
        ispricelocked_default=False,
    )
    order = convert_ok(engine, quote_id)["order"]
    assert order["locked"] is False
    assert order["pricing_mode"] == vocab.PRICING_MODE_CURRENT


def test_the_date_won_defaults_to_today(engine, store):
    quote_id = make_quote(store)
    enable(engine)
    order = convert_ok(engine, quote_id)["order"]
    assert order["won_on"] == "2026-04-01"


def test_the_date_won_can_be_set(engine, store):
    quote_id = make_quote(store)
    enable(engine)
    order = engine.convert(quote_id, {"date_won": "2026-05-05"}, source=SOURCE)["order"]
    assert order["won_on"] == "2026-05-05"


def test_an_unactivated_quote_is_refused_and_records_why(engine, store, db):
    quote_id = make_quote(store, status="draft")
    enable(engine)
    result = convert_ok(engine, quote_id)
    assert result["created"] is False
    assert result["order"] is None
    assert result["reason"] == vocab.REASON_QUOTE_NOT_ACTIVATED
    entries = engine.summary(room_id="room_1")["refusals"]
    assert entries[vocab.REASON_QUOTE_NOT_ACTIVATED] == 1


def test_an_unaccepted_quote_is_refused(engine, store):
    quote_id = make_quote(store, hs_status=None)
    enable(engine)
    assert convert_ok(engine, quote_id)["reason"] == vocab.REASON_QUOTE_NOT_ACCEPTED


def test_creation_disabled_refuses_and_creates_no_order(engine, store):
    quote_id = make_quote(store)
    engine.update_settings(
        {vocab.FIELD_CREATE_ORDERS: False}, room_id="room_1", actor="dana", source="test"
    )
    result = convert_ok(engine, quote_id)
    assert result["reason"] == vocab.REASON_CREATION_DISABLED
    assert engine.orders(room_id="room_1") == []


def test_conversion_is_idempotent(engine, store):
    quote_id = make_quote(store)
    enable(engine)
    first = convert_ok(engine, quote_id)
    second = convert_ok(engine, quote_id)
    assert second["created"] is False
    assert second["reason"] == vocab.REASON_ALREADY_CONVERTED
    assert second["order"]["id"] == first["order"]["id"]
    assert len(engine.orders(room_id="room_1")) == 1


def test_the_order_is_a_copy_not_a_live_view(engine, store):
    quote_id = make_quote(store)
    enable(engine)
    order = convert_ok(engine, quote_id)["order"]
    store.update(
        [row for row in store.find("wf086_line_item", {"quote_id": quote_id})][0]["id"],
        {"unit_price": 1.0},
        actor="dana",
        source="test",
    )
    again = engine.order(order["id"])
    assert again["line_items"][0]["unit_price"] == 12000.0


# --------------------------------------------------------------------------- #
# Repricing
# --------------------------------------------------------------------------- #


def test_repricing_an_unlocked_order_applies_the_price_list(engine, store):
    catalogue = add_catalogue(store, price=777.0)
    quote_id = make_quote(store, price_list=catalogue["book_id"])
    store.update(
        catalog_line_id(store, quote_id),
        {"product_id": catalogue["product_id"]},
        actor="dana",
        source="test",
    )
    enable(engine, enable_sales_order_processing_integration=True, ispricelocked_default=False)
    order = convert_ok(engine, quote_id)["order"]
    result = engine.reprice(order["id"], source=SOURCE)
    assert result["applied_lines"] == 1
    assert result["reason"] is None
    assert result["order"]["line_items"][0]["unit_price"] == 777.0


def test_a_locked_order_is_not_repriced(engine, store):
    catalogue = add_catalogue(store, price=777.0)
    quote_id = make_quote(store, price_list=catalogue["book_id"])
    store.update(
        catalog_line_id(store, quote_id),
        {"product_id": catalogue["product_id"]},
        actor="dana",
        source="test",
    )
    enable(engine, enable_sales_order_processing_integration=False)
    order = convert_ok(engine, quote_id)["order"]
    result = engine.reprice(order["id"], source=SOURCE)
    assert result["applied_lines"] == 0
    assert result["reason"] == vocab.REASON_PRICES_LOCKED
    assert result["order"]["line_items"][0]["unit_price"] == 12000.0
    assert result["ledger_id"]


def test_an_overridden_line_survives_a_reprice(engine, store):
    catalogue = add_catalogue(store, price=777.0)
    quote_id = make_quote(store, price_list=catalogue["book_id"])
    store.update(
        catalog_line_id(store, quote_id),
        {"product_id": catalogue["product_id"]},
        actor="dana",
        source="test",
    )
    enable(engine, enable_sales_order_processing_integration=True, ispricelocked_default=False)
    order = convert_ok(engine, quote_id)["order"]
    # The seller types a price, which makes the line an override.
    engine.update_line(
        order["id"], order["line_items"][0]["id"], {"unit_price": 5.0}, source=SOURCE
    )
    result = engine.reprice(order["id"], source=SOURCE)
    assert result["applied_lines"] == 0
    assert result["order"]["line_items"][0]["unit_price"] == 5.0


def test_the_second_quote_line_is_overridden_and_never_repriced(engine, store):
    catalogue = add_catalogue(store, price=777.0)
    quote_id = make_quote(store, price_list=catalogue["book_id"])
    enable(engine, enable_sales_order_processing_integration=True, ispricelocked_default=False)
    order = convert_ok(engine, quote_id)["order"]
    # The fixture's second line is priced manually, so it is already overridden.
    assert order["line_items"][1]["price_overridden"] is True
    engine.reprice(order["id"], source=SOURCE)
    assert engine.order(order["id"])["line_items"][1]["unit_price"] == 1250.0


# --------------------------------------------------------------------------- #
# Pricing mode
# --------------------------------------------------------------------------- #


def test_lock_pricing_sets_the_flag(engine, store):
    quote_id = make_quote(store)
    enable(engine, enable_sales_order_processing_integration=True, ispricelocked_default=False)
    order = convert_ok(engine, quote_id)["order"]
    locked = engine.set_pricing_mode(order["id"], vocab.PRICING_MODE_LOCKED, source=SOURCE)
    assert locked["locked"] is True
    unlocked = engine.set_pricing_mode(order["id"], vocab.PRICING_MODE_CURRENT, source=SOURCE)
    assert unlocked["locked"] is False


def test_an_unknown_pricing_mode_is_refused(engine, store):
    quote_id = make_quote(store)
    enable(engine)
    order = convert_ok(engine, quote_id)["order"]
    with pytest.raises(OrderError):
        engine.set_pricing_mode(order["id"], "sideways", source=SOURCE)


# --------------------------------------------------------------------------- #
# Write-in products
# --------------------------------------------------------------------------- #


def test_a_write_in_is_addable_while_prices_are_unlocked(engine, store):
    quote_id = make_quote(store)
    enable(engine, enable_sales_order_processing_integration=True, ispricelocked_default=False)
    order = convert_ok(engine, quote_id)["order"]
    updated = engine.add_line(
        order["id"],
        {"name": "Expedited delivery", "quantity": 1, "unit_price": 450.0},
        source=SOURCE,
    )
    assert updated["line_item_count"] == 3
    write_in = updated["line_items"][-1]
    assert write_in["write_in"] is True
    assert write_in["price_overridden"] is True


def test_a_write_in_is_refused_while_prices_are_locked(engine, store):
    quote_id = make_quote(store)
    enable(engine, enable_sales_order_processing_integration=False)
    order = convert_ok(engine, quote_id)["order"]
    with pytest.raises(OrderConflict) as caught:
        engine.add_line(order["id"], {"name": "X", "quantity": 1, "unit_price": 1.0}, source=SOURCE)
    assert caught.value.reason == vocab.REASON_WRITE_IN_NOT_ALLOWED


def test_a_write_in_needs_a_name(engine, store):
    quote_id = make_quote(store)
    enable(engine, enable_sales_order_processing_integration=True, ispricelocked_default=False)
    order = convert_ok(engine, quote_id)["order"]
    with pytest.raises(OrderError):
        engine.add_line(order["id"], {"quantity": 1, "unit_price": 1.0}, source=SOURCE)


# --------------------------------------------------------------------------- #
# Editing a line
# --------------------------------------------------------------------------- #


def test_a_line_price_can_be_edited_while_unlocked(engine, store):
    quote_id = make_quote(store)
    enable(engine, enable_sales_order_processing_integration=True, ispricelocked_default=False)
    order = convert_ok(engine, quote_id)["order"]
    line_id = order["line_items"][0]["id"]
    updated = engine.update_line(order["id"], line_id, {"unit_price": 42.0}, source=SOURCE)
    line = next(item for item in updated["line_items"] if item["id"] == line_id)
    assert line["unit_price"] == 42.0
    assert line["price_overridden"] is True


def test_a_line_price_edit_is_refused_while_locked(engine, store):
    quote_id = make_quote(store)
    enable(engine, enable_sales_order_processing_integration=False)
    order = convert_ok(engine, quote_id)["order"]
    with pytest.raises(OrderConflict) as caught:
        engine.update_line(
            order["id"], order["line_items"][0]["id"], {"unit_price": 42.0}, source=SOURCE
        )
    assert caught.value.reason == vocab.REASON_PRICES_LOCKED


def test_a_line_from_another_order_is_not_found(engine, store):
    enable(engine)
    first = convert_ok(engine, make_quote(store))["order"]
    second = convert_ok(engine, make_quote(store))["order"]
    with pytest.raises(OrderNotFound):
        engine.update_line(
            second["id"], first["line_items"][0]["id"], {"unit_price": 1.0}, source=SOURCE
        )


# --------------------------------------------------------------------------- #
# Fulfilment closes the order
# --------------------------------------------------------------------------- #


def test_fulfilling_an_order_writes_an_orderclose(engine, store, db):
    quote_id = make_quote(store)
    enable(engine)
    order = convert_ok(engine, quote_id)["order"]
    closed = engine.close_order(order["id"], {"status": "fulfilled"}, source=SOURCE)
    assert closed["status"] == "fulfilled"
    assert closed["closed"] is True
    closes = store.find(vocab.ORDER_CLOSE_COLLECTION, {"source_quote_id": quote_id})
    assert len(closes) == 1
    assert closes[0]["data"]["invoice_expected"] is True


def test_a_closed_order_refuses_an_edit(engine, store):
    quote_id = make_quote(store)
    enable(engine)
    order = convert_ok(engine, quote_id)["order"]
    engine.close_order(order["id"], {"status": "fulfilled"}, source=SOURCE)
    with pytest.raises(OrderConflict) as caught:
        engine.update_order(order["id"], {"description": "nope"}, source=SOURCE)
    assert caught.value.reason == vocab.REASON_ORDER_CLOSED


def test_a_closed_order_refuses_a_reprice(engine, store):
    quote_id = make_quote(store)
    enable(engine)
    order = convert_ok(engine, quote_id)["order"]
    engine.close_order(order["id"], {"status": "cancelled"}, source=SOURCE)
    with pytest.raises(OrderConflict) as caught:
        engine.reprice(order["id"], source=SOURCE)
    assert caught.value.reason == vocab.REASON_ORDER_CLOSED


def test_a_closed_order_refuses_a_second_close(engine, store):
    quote_id = make_quote(store)
    enable(engine)
    order = convert_ok(engine, quote_id)["order"]
    engine.close_order(order["id"], {"status": "fulfilled"}, source=SOURCE)
    with pytest.raises(OrderConflict):
        engine.close_order(order["id"], {"status": "cancelled"}, source=SOURCE)


def test_an_order_closes_only_as_fulfilled_or_cancelled(engine, store):
    quote_id = make_quote(store)
    enable(engine)
    order = convert_ok(engine, quote_id)["order"]
    with pytest.raises(OrderError):
        engine.close_order(order["id"], {"status": "shipped"}, source=SOURCE)


# --------------------------------------------------------------------------- #
# Get Products and the opportunity
# --------------------------------------------------------------------------- #


def test_get_products_copies_an_opportunitys_products(engine, store):
    quote_id = make_quote(store)
    enable(engine, enable_sales_order_processing_integration=True, ispricelocked_default=False)
    order = convert_ok(engine, quote_id)["order"]
    result = engine.get_products(
        order["id"],
        {
            "opportunity_id": "opp_1",
            "line_items": [
                {"name": "Support", "sku": "SKU-09", "quantity": 1, "unit_price": 300.0}
            ],
        },
        source=SOURCE,
    )
    assert result["added_lines"] == 1
    assert result["order"]["line_item_count"] == 3
    assert result["order"]["products_source_opportunity_id"] == "opp_1"


def test_a_closed_opportunity_records_its_final_revenue(engine, store):
    quote_id = make_quote(store)
    enable(engine, enable_sales_order_processing_integration=True, ispricelocked_default=False)
    order = convert_ok(engine, quote_id)["order"]
    result = engine.get_products(
        order["id"],
        {"opportunity_id": "opp_2", "state": "won", "final_revenue": 9000.0, "line_items": []},
        source=SOURCE,
    )
    assert result["opportunity_closed"] is True
    assert result["final_revenue"] == 9000.0


def test_get_products_is_refused_while_prices_are_locked(engine, store):
    quote_id = make_quote(store)
    enable(engine, enable_sales_order_processing_integration=False)
    order = convert_ok(engine, quote_id)["order"]
    with pytest.raises(OrderConflict) as caught:
        engine.get_products(order["id"], {"line_items": []}, source=SOURCE)
    assert caught.value.reason == vocab.REASON_PRICES_LOCKED


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #


def test_the_default_setting_is_off(engine):
    settings = engine.settings(room_id="room_1")
    assert settings[vocab.FIELD_CREATE_ORDERS] is False
    assert settings[vocab.FIELD_SALES_ORDER_PROCESSING] is False
    assert settings[vocab.FIELD_ISPRICELOCKED_DEFAULT] is False
    assert settings["explicit"] is False


def test_update_settings_marks_the_setting_explicit(engine):
    updated = enable(engine)
    assert updated[vocab.FIELD_CREATE_ORDERS] is True
    assert updated["explicit"] is True


def test_update_settings_ignores_an_unknown_key(engine):
    updated = engine.update_settings({"future": "x"}, room_id="room_1", actor="dana", source="test")
    assert "future" not in updated


def test_the_setting_is_per_room(engine):
    enable(engine)
    assert engine.settings(room_id="room_2")[vocab.FIELD_CREATE_ORDERS] is False


# --------------------------------------------------------------------------- #
# Reads
# --------------------------------------------------------------------------- #


def test_readiness_previews_the_conversion(engine, store):
    quote_id = make_quote(store)
    enable(engine, enable_sales_order_processing_integration=False)
    ready = engine.readiness(quote_id)
    assert ready["activated"] is True
    assert ready["accepted"] is True
    assert ready["convertible"] is True
    assert ready["refusal"] is None
    assert ready["price_lock"]["locked"] is True
    assert ready["line_item_count"] == 2
    assert ready["clone_components"] == list(vocab.CLONE_COMPONENTS)


def test_readiness_names_the_refusal(engine, store):
    quote_id = make_quote(store, status="draft")
    enable(engine)
    ready = engine.readiness(quote_id)
    assert ready["convertible"] is False
    assert ready["refusal"] == vocab.REASON_QUOTE_NOT_ACTIVATED
    assert ready["refusal_reason"]


def test_readiness_of_a_missing_quote_is_not_found(engine):
    with pytest.raises(OrderNotFound):
        engine.readiness("q_absent")


def test_order_lookup_of_a_missing_id_is_not_found(engine):
    with pytest.raises(OrderNotFound):
        engine.order("ord_absent")


def test_order_lookup_refuses_a_record_of_another_collection(engine, store):
    quote_id = make_quote(store)
    with pytest.raises(OrderNotFound):
        engine.order(quote_id)


def test_a_protected_header_field_is_refused(engine, store):
    quote_id = make_quote(store)
    enable(engine)
    order = convert_ok(engine, quote_id)["order"]
    with pytest.raises(OrderError) as caught:
        engine.update_order(order["id"], {"ispricelocked": False}, source=SOURCE)
    assert "ispricelocked" in caught.value.errors


def test_summary_counts_the_orders(engine, store):
    enable(engine)
    convert_ok(engine, make_quote(store))
    summary = engine.summary(room_id="room_1")
    assert summary["orders"] == 1
    assert summary["conversions"] == 1


# --------------------------------------------------------------------------- #
# The audit trail
# --------------------------------------------------------------------------- #


def test_every_write_names_the_route_that_served_it(engine, store, db):
    quote_id = make_quote(store)
    enable(engine)
    order = convert_ok(engine, quote_id)["order"]
    order_rows = db.audit(record_id=order["id"])
    assert any(row["source"] == SOURCE for row in order_rows)
    line_rows = db.audit(record_id=order["line_items"][0]["id"])
    assert any(row["source"] == SOURCE for row in line_rows)


# --------------------------------------------------------------------------- #
# Architectural guards
# --------------------------------------------------------------------------- #


def test_the_domain_package_imports_nothing_but_the_store():
    package = pathlib.Path(__file__).resolve().parents[1] / "dsr" / "order_conversion"
    for path in package.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "from dsr.api" not in text, path.name
        assert "import dsr.api" not in text, path.name
        assert "import sqlite3" not in text, path.name


def test_the_feature_module_does_not_import_the_app_or_sqlite():
    module = importlib.import_module(FEATURE_MODULE)
    assert module.FEATURE["id"] == "wf-101-quote-to-order"
    assert module.FEATURE["ticket"] == "WF-101"
    assert module.router.prefix == "/api/wf-101"
    feature_path = (
        pathlib.Path(__file__).resolve().parents[1]
        / "dsr"
        / "features"
        / "wf101_convert_an_accepted_quote_into_an_order.py"
    )
    text = feature_path.read_text(encoding="utf-8")
    assert "from dsr.api" not in text
    assert "import dsr.api" not in text
    assert "import sqlite3" not in text


def test_the_decisions_record_each_open_question():
    module = importlib.import_module(FEATURE_MODULE)
    picks = OrderConversionEngine.decisions()
    assert len(picks) >= 5
    for pick in picks:
        assert pick["chosen"] and pick["rejected"]
    assert module.router.prefix == "/api/wf-101"


# --------------------------------------------------------------------------- #
# The seed
# --------------------------------------------------------------------------- #


def test_the_seed_creates_the_named_states_and_returns_a_cp1252_string(db):
    module = importlib.import_module(FEATURE_MODULE)
    context = {"room_ids": [("room_seed", "Northwind")], "now": FROZEN}
    message = module.seed(db, context)
    assert message
    # The seeder prints this on a Windows console; cp1252 cannot encode an arrow.
    assert message.encode("cp1252")
    engine = OrderConversionEngine(RecordStore(db))
    summary = engine.summary(room_id="room_seed")
    assert summary["orders"] == 2
    assert summary["locked"] == 1
    assert summary["closed"] == 1
    assert vocab.REASON_QUOTE_NOT_ACTIVATED in summary["refusals"]
    assert vocab.REASON_QUOTE_NOT_ACCEPTED in summary["refusals"]
    assert vocab.REASON_CREATION_DISABLED in summary["refusals"]


def test_the_seed_reprice_applies_and_the_override_survives(db):
    module = importlib.import_module(FEATURE_MODULE)
    module.seed(db, {"room_ids": [("room_seed", "Northwind")], "now": FROZEN})
    engine = OrderConversionEngine(RecordStore(db))
    repriced = next(order for order in engine.orders(room_id="room_seed") if not order["locked"])
    prices = {line["name"]: line["unit_price"] for line in repriced["line_items"]}
    assert prices["Platform licence"] == 900.0
    assert prices["Expedited delivery"] == 450.0


def test_the_seed_returns_none_without_a_room(db):
    module = importlib.import_module(FEATURE_MODULE)
    assert module.seed(db, {"room_ids": [], "now": FROZEN}) is None
