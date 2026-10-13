"""WF-099 domain tests: the rules, the engine and the audit trail.

The file builds its own records and its own clock, so it passes alone and under
pytest-xdist without depending on another module's fixture. The clock is frozen
at :data:`FROZEN`, because a rule that resolves "on agreement" to today must not
be tested against a date that moves.
"""

from __future__ import annotations

import importlib
from datetime import date

import pytest
from dsr.contract_generation import ContractEngine, rules, vocabulary as vocab
from dsr.contract_generation.errors import ContractConflict, ContractError, ContractNotFound
from dsr.store import RecordStore

FROZEN = date(2026, 4, 1)
FEATURE_MODULE = "dsr.features.wf099_auto_create_a_contract_from_an_accepted_quote"
SOURCE = "POST /api/wf-099/quotes/q_1/acceptance"


# --------------------------------------------------------------------------- #
# Fixtures and helpers
# --------------------------------------------------------------------------- #


@pytest.fixture
def engine(db) -> ContractEngine:
    """An engine over the file-backed test database, with a frozen clock."""

    return ContractEngine(RecordStore(db), today=lambda: FROZEN)


@pytest.fixture
def store(db) -> RecordStore:
    return RecordStore(db)


def make_quote(store: RecordStore, **overrides):
    """A published, non-legacy quote with two line items and a term."""

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
        "term_length": 12,
        "totals": {"total": 14500.0, "total_contract_value": 14500.0},
    }
    payload.update(overrides)
    quote = store.create("wf086_quote", payload, room_id="room_1", actor="dana", source="test")
    store.create(
        "wf086_line_item",
        {
            "quote_id": quote["id"],
            "position": 1,
            "name": "Platform licence",
            "sku": "SKU-01",
            "quantity": 1,
            "unit_price": 12000.0,
            "tax_rate": 0.21,
            "amounts": {"total": 12000.0},
        },
        room_id="room_1",
        actor="dana",
        source="test",
    )
    store.create(
        "wf086_line_item",
        {
            "quote_id": quote["id"],
            "position": 2,
            "name": "Onboarding",
            "sku": "SKU-02",
            "quantity": 2,
            "unit_price": 1250.0,
            "tax_rate": 0.21,
        },
        room_id="room_1",
        actor="dana",
        source="test",
    )
    return quote["id"]


def enable(engine: ContractEngine, **setting):
    """Turn the feature on for room_1 with the given overrides."""

    base = {
        vocab.FIELD_CREATE_FROM_ACCEPTED_QUOTES: True,
        vocab.FIELD_CONNECTED_CPQ: True,
        vocab.FIELD_DIRECT_CONTRACTS: False,
    }
    base.update(setting)
    return engine.update_settings(base, room_id="room_1", actor="dana", source="test")


# --------------------------------------------------------------------------- #
# Coercion
# --------------------------------------------------------------------------- #


def test_coercion_helpers_accept_the_spellings_a_store_row_may_carry():
    assert rules.as_text(None) == ""
    assert rules.as_text("  x  ") == "x"
    assert rules.as_number("2.5") == 2.5
    assert rules.as_number("nope") == 0.0
    assert rules.as_number(True) == 1.0
    assert rules.as_int("3") == 3
    assert rules.as_int(3.0) == 3
    assert rules.as_int("x") is None
    assert rules.as_bool("yes") is True
    assert rules.as_bool("0") is False
    assert rules.as_bool(None) is False
    assert rules.as_bool(1) is True


def test_parse_date_accepts_three_formats_and_rejects_junk():
    assert rules.parse_date("2026-04-01") == date(2026, 4, 1)
    assert rules.parse_date("2026/04/01") == date(2026, 4, 1)
    assert rules.parse_date("2026-04-01T09:00:00") == date(2026, 4, 1)
    assert rules.parse_date("not a date") is None
    assert rules.parse_date("") is None


def test_add_months_clamps_the_day_to_the_month_end():
    assert rules.add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert rules.add_months(date(2026, 12, 15), 1) == date(2027, 1, 15)
    assert rules.add_months(date(2026, 1, 15), 12) == date(2027, 1, 15)


# --------------------------------------------------------------------------- #
# The quote state
# --------------------------------------------------------------------------- #


def test_is_published_is_case_insensitive():
    assert rules.is_published({"status": "published"})
    assert rules.is_published({"status": "PUBLISHED"})
    assert not rules.is_published({"status": "draft"})
    assert not rules.is_published({})


@pytest.mark.parametrize(
    "quote",
    [
        {"legacy_quote": True},
        {"quote_type": "legacy"},
        {"quote_type": "LEGACY_QUOTE"},
        {"accepted_via": "legacy"},
    ],
)
def test_is_legacy_accepts_each_explicit_marker(quote):
    legacy, marker = rules.is_legacy(quote)
    assert legacy is True
    assert marker


def test_is_legacy_is_false_for_a_modern_quote_that_omits_a_field():
    """The chosen rule never infers legacy from a missing modern field."""

    legacy, marker = rules.is_legacy({"status": "published", "template_type": "CPQ_QUOTE"})
    assert legacy is False
    assert marker == ""
    legacy, _ = rules.is_legacy({"status": "published"})
    assert legacy is False


# --------------------------------------------------------------------------- #
# The effective start date: all four modes
# --------------------------------------------------------------------------- #


def test_on_agreement_starts_on_the_acceptance_date():
    resolved = rules.resolve_effective_start(vocab.MODE_ON_AGREEMENT, accepted_on=FROZEN, quote={})
    assert resolved["start_date"] == "2026-04-01"
    assert resolved["accepted_on"] == "2026-04-01"
    assert vocab.MODE_REQUIRED_FIELDS[vocab.MODE_ON_AGREEMENT] == ()


def test_custom_starts_on_the_named_date():
    resolved = rules.resolve_effective_start(
        vocab.MODE_CUSTOM,
        accepted_on=FROZEN,
        quote={vocab.FIELD_EFFECTIVE_START_DATE: "2026-07-01"},
    )
    assert resolved["start_date"] == "2026-07-01"


def test_custom_without_its_date_names_the_missing_field():
    with pytest.raises(ContractError) as caught:
        rules.resolve_effective_start(vocab.MODE_CUSTOM, accepted_on=FROZEN, quote={})
    assert vocab.FIELD_EFFECTIVE_START_DATE in caught.value.errors


def test_custom_with_an_unparseable_date_is_refused():
    with pytest.raises(ContractError) as caught:
        rules.resolve_effective_start(
            vocab.MODE_CUSTOM,
            accepted_on=FROZEN,
            quote={vocab.FIELD_EFFECTIVE_START_DATE: "soon"},
        )
    assert vocab.FIELD_EFFECTIVE_START_DATE in caught.value.errors


def test_delayed_by_days_adds_the_offset():
    resolved = rules.resolve_effective_start(
        vocab.MODE_DELAYED_BY_DAYS,
        accepted_on=FROZEN,
        quote={vocab.FIELD_EFFECTIVE_START_DELAYED_DAYS: 30},
    )
    assert resolved["start_date"] == "2026-05-01"


def test_delayed_by_days_without_its_offset_names_the_field():
    with pytest.raises(ContractError) as caught:
        rules.resolve_effective_start(vocab.MODE_DELAYED_BY_DAYS, accepted_on=FROZEN, quote={})
    assert vocab.FIELD_EFFECTIVE_START_DELAYED_DAYS in caught.value.errors


def test_delayed_by_days_rejects_a_negative_offset():
    with pytest.raises(ContractError):
        rules.resolve_effective_start(
            vocab.MODE_DELAYED_BY_DAYS,
            accepted_on=FROZEN,
            quote={vocab.FIELD_EFFECTIVE_START_DELAYED_DAYS: -1},
        )


def test_delayed_by_months_adds_the_offset():
    resolved = rules.resolve_effective_start(
        vocab.MODE_DELAYED_BY_MONTHS,
        accepted_on=FROZEN,
        quote={vocab.FIELD_EFFECTIVE_START_DELAYED_MONTHS: 3},
    )
    assert resolved["start_date"] == "2026-07-01"


def test_delayed_by_months_without_its_offset_names_the_field():
    with pytest.raises(ContractError) as caught:
        rules.resolve_effective_start(vocab.MODE_DELAYED_BY_MONTHS, accepted_on=FROZEN, quote={})
    assert vocab.FIELD_EFFECTIVE_START_DELAYED_MONTHS in caught.value.errors


def test_two_modes_need_an_extra_field_and_two_do_not():
    extra = [mode for mode, fields in vocab.MODE_EXTRA_FIELDS.items() if fields]
    none = [mode for mode, fields in vocab.MODE_EXTRA_FIELDS.items() if not fields]
    assert len(extra) == 2
    assert sorted(none) == sorted([vocab.MODE_ON_AGREEMENT, vocab.MODE_CUSTOM])
    assert vocab.MODE_REQUIRED_FIELDS[vocab.MODE_ON_AGREEMENT] == ()


def test_unknown_mode_is_refused_with_the_field_named():
    with pytest.raises(ContractError) as caught:
        rules.resolve_effective_start("SOMETIME", accepted_on=FROZEN, quote={})
    assert vocab.FIELD_EFFECTIVE_START_DATE_TYPE in caught.value.errors


# --------------------------------------------------------------------------- #
# Term length and the end date
# --------------------------------------------------------------------------- #


def test_term_length_reads_the_three_spellings():
    assert rules.term_length({"term_length": 12}) == 12
    assert rules.term_length({"term_months": 6}) == 6
    assert rules.term_length({"term": "3"}) == 3
    assert rules.term_length({"term_length": 0}) is None
    assert rules.term_length({}) is None


def test_contract_end_date_is_the_start_plus_the_term():
    assert rules.contract_end_date("2026-04-01", 12) == "2027-04-01"
    assert rules.contract_end_date("2026-04-01", None) is None
    assert rules.contract_end_date("nonsense", 12) is None


# --------------------------------------------------------------------------- #
# Inheritance
# --------------------------------------------------------------------------- #


def test_contract_line_items_read_the_amounts_block_and_by_arithmetic():
    items = rules.contract_line_items(
        [
            {
                "name": "A",
                "sku": "S1",
                "quantity": 1,
                "unit_price": 100,
                "amounts": {"total": 90.0},
            },
            {"name": "B", "sku": "S2", "quantity": 2, "unit_price": 50},
        ]
    )
    assert items[0]["amount"] == 90.0
    assert items[1]["amount"] == 100.0
    assert items[0]["sku"] == "S1"
    assert items[1]["quantity"] == 2.0


def test_contract_line_items_skip_non_mappings_and_carry_auto_renew():
    items = rules.contract_line_items(["not a line", {"name": "C", "auto_renew": "until_canceled"}])
    assert len(items) == 1
    assert items[0]["auto_renew"] == "until_canceled"
    assert items[0]["amount"] == 0.0


def test_contract_line_items_are_a_copy_not_a_reference():
    source = [{"name": "A", "quantity": 1, "unit_price": 10}]
    items = rules.contract_line_items(source)
    items[0]["name"] = "changed"
    assert source[0]["name"] == "A"


# --------------------------------------------------------------------------- #
# The scenario matrix
# --------------------------------------------------------------------------- #


def test_scenario_one_setting_on_and_connected_cpq_gives_contract_and_order():
    plan = rules.generation_plan(
        {vocab.FIELD_CREATE_FROM_ACCEPTED_QUOTES: True, vocab.FIELD_CONNECTED_CPQ: True}
    )
    assert plan["flow"] == vocab.FLOW_CONTRACT_AND_ORDER
    assert plan["create_contract"] is True
    assert plan["create_order"] is True


def test_scenario_two_setting_on_without_the_beta_gives_a_contract_only():
    plan = rules.generation_plan(
        {vocab.FIELD_CREATE_FROM_ACCEPTED_QUOTES: True, vocab.FIELD_CONNECTED_CPQ: False}
    )
    assert plan["flow"] == vocab.FLOW_CONTRACT_ONLY
    assert plan["create_contract"] is True
    assert plan["create_order"] is False


def test_scenario_three_setting_off_gives_no_contract():
    plan = rules.generation_plan({vocab.FIELD_CREATE_FROM_ACCEPTED_QUOTES: False})
    assert plan["flow"] == vocab.FLOW_NONE
    assert plan["create_contract"] is False
    assert plan["reason"] == vocab.REASON_SETTING_OFF


def test_billing_flow_is_independent_of_the_contract_setting():
    assert rules.billing_flow({vocab.FIELD_HS_BILLING_ENABLED: True}) == (
        vocab.BILLING_SUBSCRIPTION
    )
    assert rules.billing_flow({vocab.FIELD_HS_PAYMENT_ENABLED: True}) == (
        vocab.BILLING_SUBSCRIPTION
    )
    assert rules.billing_flow({}) == vocab.BILLING_NONE


def test_contract_payload_copies_the_quote_and_names_its_source():
    quote = {
        "title": "T",
        "currency": "EUR",
        "account": "A",
        "totals": {"total": 10.0},
        "deal_id": "deal_1",
    }
    payload = rules.contract_payload(
        quote=quote,
        quote_id="q_1",
        quote_lines=[{"name": "L", "quantity": 1, "unit_price": 5}],
        plan={"flow": vocab.FLOW_CONTRACT_ONLY, "create_order": False},
        effective={
            "mode": vocab.MODE_ON_AGREEMENT,
            "start_date": "2026-04-01",
            "accepted_on": "2026-04-01",
        },
        term=12,
    )
    assert payload["source_quote_id"] == "q_1"
    assert payload["end_date"] == "2027-04-01"
    assert payload["buyer"] == "A"
    assert payload["contract_flow"] == vocab.FLOW_CONTRACT_ONLY


# --------------------------------------------------------------------------- #
# The setting
# --------------------------------------------------------------------------- #


def test_the_default_setting_is_off(engine):
    settings = engine.settings(room_id="room_1")
    assert settings[vocab.FIELD_CREATE_FROM_ACCEPTED_QUOTES] is False
    assert settings["explicit"] is False


def test_update_settings_turns_creation_on_and_marks_it_explicit(engine):
    updated = enable(engine)
    assert updated[vocab.FIELD_CREATE_FROM_ACCEPTED_QUOTES] is True
    assert updated["explicit"] is True


def test_update_settings_ignores_an_unknown_key(engine):
    updated = engine.update_settings(
        {"future_field": "x"}, room_id="room_1", actor="dana", source="test"
    )
    assert "future_field" not in updated


def test_update_settings_refuses_an_unknown_renewal_alert_mode(engine):
    with pytest.raises(ContractError):
        engine.update_settings(
            {vocab.FIELD_RENEWAL_ALERT_MODE: "whenever"},
            room_id="room_1",
            actor="dana",
            source="test",
        )


def test_update_settings_refuses_a_negative_renewal_alert_offset(engine):
    with pytest.raises(ContractError):
        engine.update_settings(
            {vocab.FIELD_RENEWAL_ALERT_DAYS: -5},
            room_id="room_1",
            actor="dana",
            source="test",
        )


def test_the_setting_is_per_room(engine, store):
    enable(engine)
    other = engine.settings(room_id="room_2")
    assert other[vocab.FIELD_CREATE_FROM_ACCEPTED_QUOTES] is False


# --------------------------------------------------------------------------- #
# Acceptance
# --------------------------------------------------------------------------- #


def test_acceptance_needs_a_published_quote(engine, store):
    quote_id = make_quote(store, status="draft")
    with pytest.raises(ContractConflict) as caught:
        engine.record_acceptance(quote_id, {}, actor="dana", source=SOURCE)
    assert caught.value.reason == vocab.REASON_QUOTE_NOT_PUBLISHED


def test_acceptance_records_a_ledger_row_and_patches_the_quote(engine, store):
    quote_id = make_quote(store)
    engine.record_acceptance(
        quote_id, {"method": "esignature", "accepted_by": "buyer@northwind.example"}, source=SOURCE
    )
    ledger = engine.acceptance_for(quote_id)
    assert ledger is not None
    assert ledger["data"]["method"] == "esignature"
    assert ledger["data"]["accepted_by"] == "buyer@northwind.example"
    quote = store.require(quote_id)["data"]
    assert quote[vocab.FIELD_HS_STATUS] == vocab.QUOTE_STATUS_ACCEPTED
    assert quote[vocab.FIELD_HS_CLICKWRAP_ACCEPTED_BY] == "buyer@northwind.example"


def test_acceptance_refuses_an_unknown_method(engine, store):
    quote_id = make_quote(store)
    with pytest.raises(ContractError):
        engine.record_acceptance(quote_id, {"method": "telepathy"}, source=SOURCE)


def test_acceptance_is_idempotent(engine, store):
    quote_id = make_quote(store)
    first = engine.record_acceptance(quote_id, {"method": "clickwrap"}, source=SOURCE)
    second = engine.record_acceptance(quote_id, {"method": "esignature"}, source=SOURCE)
    assert first["id"] == second["id"]
    assert second["data"]["method"] == "esignature"


def test_acceptance_on_a_missing_quote_is_a_not_found(engine):
    with pytest.raises(ContractNotFound):
        engine.record_acceptance("q_absent", {}, source=SOURCE)


# --------------------------------------------------------------------------- #
# Generation: the three scenarios end to end
# --------------------------------------------------------------------------- #


def test_accepting_with_the_beta_creates_a_contract_and_an_order(engine, store):
    quote_id = make_quote(store)
    enable(engine)
    result = engine.accept(quote_id, {"method": "esignature"}, source=SOURCE)
    assert result["flow"] == vocab.FLOW_CONTRACT_AND_ORDER
    assert result["contract"] is not None
    assert result["order"] is not None
    assert result["contract"]["derived"]["has_order"] is True
    assert result["contract"]["data"]["order_id"] == result["order"]["id"]


def test_accepting_without_the_beta_creates_a_contract_only(engine, store):
    quote_id = make_quote(store)
    enable(engine, connected_cpq=False)
    result = engine.accept(quote_id, {"method": "esignature"}, source=SOURCE)
    assert result["flow"] == vocab.FLOW_CONTRACT_ONLY
    assert result["contract"] is not None
    assert result["order"] is None


def test_accepting_with_the_setting_off_creates_no_contract(engine, store):
    quote_id = make_quote(store, hs_billing_enabled=True)
    engine.update_settings(
        {vocab.FIELD_CREATE_FROM_ACCEPTED_QUOTES: False},
        room_id="room_1",
        actor="dana",
        source="test",
    )
    result = engine.accept(quote_id, {"method": "clickwrap"}, source=SOURCE)
    assert result["contract"] is None
    assert result["flow"] == vocab.FLOW_NONE
    assert result["reason"] in (vocab.REASON_SETTING_OFF, vocab.REASON_LEGACY_QUOTE)


def test_no_contract_does_not_mean_no_subscription(engine, store):
    """The billing switch is separate; a billing-enabled quote still expects one."""

    quote_id = make_quote(store, hs_billing_enabled=True)
    enable(engine, connected_cpq=False)
    result = engine.accept(quote_id, {"method": "clickwrap"}, source=SOURCE)
    assert result["contract"] is not None
    assert result["contract"]["data"]["billing_flow"] == vocab.BILLING_SUBSCRIPTION
    # The subscription itself is WF-096's; this workflow writes no invoice or
    # subscription record, only the intent on the contract.
    assert engine.settings(room_id="room_1")[vocab.FIELD_CREATE_FROM_ACCEPTED_QUOTES]


# --------------------------------------------------------------------------- #
# Inheritance: a copy, not a live link
# --------------------------------------------------------------------------- #


def test_the_contract_inherits_the_quote_line_items(engine, store):
    quote_id = make_quote(store)
    enable(engine)
    result = engine.accept(quote_id, {}, source=SOURCE)
    contract_lines = result["contract"]["data"]["line_items"]
    quote_lines = engine._quote_lines(quote_id)
    assert len(contract_lines) == len(quote_lines)
    assert {line["sku"] for line in contract_lines} == {"SKU-01", "SKU-02"}
    assert contract_lines[0]["amount"] == 12000.0


def test_editing_the_quote_after_creation_does_not_change_the_contract(engine, store):
    quote_id = make_quote(store)
    enable(engine)
    contract = engine.accept(quote_id, {}, source=SOURCE)["contract"]
    line = engine._quote_lines(quote_id)[0]
    store.update(line["id"], {"unit_price": 1.0}, actor="dana", source="test")
    again = engine.contract(contract["id"])
    assert again["data"]["line_items"][0]["unit_price"] == 12000.0


def test_flipping_the_setting_later_does_not_rewrite_an_existing_contract(engine, store):
    quote_id = make_quote(store)
    enable(engine)
    contract = engine.accept(quote_id, {}, source=SOURCE)["contract"]
    assert contract["data"]["contract_flow"] == vocab.FLOW_CONTRACT_AND_ORDER

    engine.update_settings(
        {vocab.FIELD_CONNECTED_CPQ: False},
        room_id="room_1",
        actor="dana",
        source="test",
    )
    unchanged = engine.contract(contract["id"])
    assert unchanged["data"]["contract_flow"] == vocab.FLOW_CONTRACT_AND_ORDER


def test_generation_is_idempotent(engine, store):
    quote_id = make_quote(store)
    enable(engine)
    first = engine.accept(quote_id, {}, source=SOURCE)
    second = engine.generate(quote_id, source=SOURCE)
    assert second["created"] is False
    assert second["reason"] == vocab.REASON_ALREADY_GENERATED
    assert second["contract"]["id"] == first["contract"]["id"]
    assert len(engine.contracts(room_id="room_1")) == 1


# --------------------------------------------------------------------------- #
# The arbitrary-write guard
# --------------------------------------------------------------------------- #


def test_a_raw_status_write_cannot_mint_a_contract(engine, store):
    quote_id = make_quote(store)
    enable(engine)
    # A generic record write sets hs_status directly, with no acceptance ledger
    # row behind it.
    store.update(
        quote_id,
        {vocab.FIELD_HS_STATUS: vocab.QUOTE_STATUS_ACCEPTED},
        actor="intruder",
        source="test",
    )
    with pytest.raises(ContractConflict) as caught:
        engine.generate(quote_id, source=SOURCE)
    assert caught.value.reason == vocab.REASON_ACCEPTANCE_NOT_RECORDED
    assert engine.contracts(room_id="room_1") == []


def test_generate_requires_an_acceptance_row(engine, store):
    quote_id = make_quote(store)
    enable(engine)
    with pytest.raises(ContractConflict) as caught:
        engine.generate(quote_id, source=SOURCE)
    assert caught.value.reason == vocab.REASON_ACCEPTANCE_NOT_RECORDED


# --------------------------------------------------------------------------- #
# Legacy
# --------------------------------------------------------------------------- #


def test_a_legacy_quote_is_excluded_from_contract_creation(engine, store):
    quote_id = make_quote(store, legacy_quote=True)
    enable(engine)
    result = engine.accept(quote_id, {}, source=SOURCE)
    assert result["contract"] is None
    assert result["reason"] == vocab.REASON_LEGACY_QUOTE
    assert engine.contracts(room_id="room_1") == []


def test_the_legacy_acceptance_is_recorded_but_creates_no_contract(engine, store):
    quote_id = make_quote(store, quote_type="legacy")
    enable(engine)
    engine.accept(quote_id, {}, source=SOURCE)
    ledger = engine.acceptance_for(quote_id)
    assert ledger["data"]["legacy"] is True
    assert ledger["data"]["legacy_marker"] == vocab.FIELD_QUOTE_TYPE


def test_explicit_generate_on_a_legacy_quote_is_a_conflict(engine, store):
    quote_id = make_quote(store, legacy_quote=True)
    enable(engine)
    engine.record_acceptance(quote_id, {}, source=SOURCE)
    with pytest.raises(ContractConflict) as caught:
        engine.generate(quote_id, source=SOURCE)
    assert caught.value.reason == vocab.REASON_LEGACY_QUOTE


# --------------------------------------------------------------------------- #
# Standalone contracts
# --------------------------------------------------------------------------- #


def test_a_standalone_contract_needs_the_direct_beta(engine):
    with pytest.raises(ContractConflict) as caught:
        engine.create_standalone({"name": "X"}, room_id="room_1", source="test")
    assert caught.value.reason == vocab.REASON_DIRECT_CONTRACTS_DISABLED


def test_a_standalone_contract_with_no_quote_is_created(engine, store):
    enable(engine, direct_contracts_enabled=True)
    contract = engine.create_standalone(
        {
            "name": "Wingtip standalone",
            "currency": "EUR",
            "start_date": "2026-05-01",
            "term_length": 12,
            "line_items": [{"name": "Retainer", "quantity": 1, "unit_price": 6000.0}],
        },
        room_id="room_1",
        source="test",
    )
    assert contract["data"]["source_quote_id"] is None
    assert contract["data"]["contract_flow"] == "standalone"
    assert contract["data"]["end_date"] == "2027-05-01"


def test_a_standalone_contract_needs_a_name(engine):
    enable(engine, direct_contracts_enabled=True)
    with pytest.raises(ContractError):
        engine.create_standalone({}, room_id="room_1", source="test")


# --------------------------------------------------------------------------- #
# Reads
# --------------------------------------------------------------------------- #


def test_contract_lookup_of_a_missing_id_is_not_found(engine):
    with pytest.raises(ContractNotFound):
        engine.contract("ct_absent")


def test_contract_lookup_refuses_a_record_of_another_collection(engine, store):
    quote_id = make_quote(store)
    with pytest.raises(ContractNotFound):
        engine.contract(quote_id)


def test_readiness_previews_the_plan_before_acceptance(engine, store):
    quote_id = make_quote(store)
    enable(engine)
    readiness = engine.quote_readiness(quote_id)
    assert readiness["published"] is True
    assert readiness["legacy"] is False
    assert readiness["accepted"] is False
    assert readiness["flow"] == vocab.FLOW_CONTRACT_AND_ORDER
    assert readiness["effective_start"]["start_date"] == "2026-04-01"
    assert readiness["line_item_count"] == 2


def test_readiness_reports_the_effective_start_error(engine, store):
    quote_id = make_quote(store, hs_contract_effective_start_date_type=vocab.MODE_CUSTOM)
    readiness = engine.quote_readiness(quote_id)
    assert readiness["effective_start"] is None
    assert readiness["effective_start_error"] is not None


def test_readiness_of_a_missing_quote_is_not_found(engine):
    with pytest.raises(ContractNotFound):
        engine.quote_readiness("q_absent")


def test_summary_counts_the_records(engine, store):
    quote_id = make_quote(store)
    enable(engine)
    engine.accept(quote_id, {}, source=SOURCE)
    summary = engine.summary(room_id="room_1")
    assert summary["contracts"] == 1
    assert summary["with_order"] == 1
    assert summary["orders"] == 1
    assert summary["acceptances"] == 1


def test_term_label_reads_the_line_items():
    assert ContractEngine._term_label({"line_items": [{"auto_renew": "until_canceled"}]}) == (
        "Evergreen"
    )
    assert ContractEngine._term_label({"term_length": 12}) == "12 months"
    assert ContractEngine._term_label({}) == "unset"


# --------------------------------------------------------------------------- #
# The audit trail
# --------------------------------------------------------------------------- #


def test_every_write_names_the_route_that_served_it(engine, store, db):
    quote_id = make_quote(store)
    enable(engine)
    engine.accept(quote_id, {}, source=SOURCE)
    rows = db.audit(record_id=quote_id)
    assert any(row["source"] == SOURCE for row in rows)
    contract_id = engine.contracts(room_id="room_1")[0]["id"]
    contract_rows = db.audit(record_id=contract_id)
    assert any(row["source"] == SOURCE for row in contract_rows)


def test_the_effect_start_properties_are_written_to_the_quote(engine, store):
    quote_id = make_quote(store)
    enable(engine)
    engine.accept(
        quote_id,
        {
            "effective_start_date_type": vocab.MODE_DELAYED_BY_MONTHS,
            "delayed_by_months": 3,
        },
        source=SOURCE,
    )
    contract = engine.contracts(room_id="room_1")[0]
    assert contract["data"]["start_date"] == "2026-07-01"
    assert contract["data"]["effective_start_date_mode"] == vocab.MODE_DELAYED_BY_MONTHS


def test_delayed_by_days_flows_from_the_acceptance_body(engine, store):
    quote_id = make_quote(store)
    enable(engine)
    engine.accept(
        quote_id,
        {"effective_start_date_type": vocab.MODE_DELAYED_BY_DAYS, "delayed_by_days": 10},
        source=SOURCE,
    )
    contract = engine.contracts(room_id="room_1")[0]
    assert contract["data"]["start_date"] == "2026-04-11"


# --------------------------------------------------------------------------- #
# Architectural guards
# --------------------------------------------------------------------------- #


def test_the_domain_package_imports_nothing_but_the_store():
    import pathlib

    package = pathlib.Path(__file__).resolve().parents[1] / "dsr" / "contract_generation"
    for path in package.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "from dsr.api" not in text, path.name
        assert "import dsr.api" not in text, path.name
        assert "import sqlite3" not in text, path.name


def test_the_feature_module_does_not_import_the_app():
    import pathlib

    module = importlib.import_module(FEATURE_MODULE)
    assert module.FEATURE["ticket"] == "WF-099"
    assert module.router.prefix == "/api/wf-099"
    feature_path = (
        pathlib.Path(__file__).resolve().parents[1]
        / "dsr"
        / "features"
        / "wf099_auto_create_a_contract_from_an_accepted_quote.py"
    )
    text = feature_path.read_text(encoding="utf-8")
    assert "from dsr.api" not in text
    assert "import dsr.api" not in text
    assert "import sqlite3" not in text


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
    store = RecordStore(db)
    engine = ContractEngine(store)
    assert len(engine.contracts(room_id="room_seed")) >= 3
    assert engine.summary(room_id="room_seed")["orders"] >= 1
    assert engine.summary(room_id="room_seed")["legacy_acceptances"] >= 1


def test_the_seed_returns_none_without_a_room(db):
    module = importlib.import_module(FEATURE_MODULE)
    assert module.seed(db, {"room_ids": [], "now": FROZEN}) is None
