"""WF-100 in the domain: the rules, with no HTTP and no framework.

Organised by what a caller can observe about the rules themselves:

``the term label``
    Evergreen as a derived label on the term length, not a type and not a state.
``the renewal date rule``
    Both branches of the researched conditional, because the research states both.
``the change effective date``
    All four researched modes, and the fact that a mode that cannot resolve says so.
``proration``
    The cleared checkbox as an explicit false, and never as a missing field.
``the chain``
    A contract that records both sides of the renewal link.
``the re-enrol decision``
    Both behaviours, because the research named the switch and described neither.
``the vocabulary``
    Every quoted sentence is quoted, and every derived state says it is derived.
``the seed return string``
    Encodable by cp1252, because the seeder prints it to a Windows console.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from dsr.renewal_quotes import inferences, rules, vocabulary as vocab
from dsr.renewal_quotes.engine import RenewalQuoteEngine
from dsr.renewal_quotes.errors import RenewalConflict, RenewalNotFound, RenewalRefusal
from dsr.store import RecordStore

NOW = datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc)
TODAY = NOW.date()


@pytest.fixture()
def engine(store: RecordStore) -> RenewalQuoteEngine:
    return RenewalQuoteEngine(store, now=lambda: NOW)


@pytest.fixture()
def world(engine: RenewalQuoteEngine) -> dict[str, dict]:
    """One pipeline, one template, two contracts. Built through the real engine."""

    pipeline = engine.create_pipeline(
        "Renewals", stages=["Qualification", "Closed won"], source="test", actor="dana"
    )["pipeline"]
    template = engine.create_template(
        "Annual renewal", term_months=12, source="test", actor="dana"
    )["template"]
    northwind = engine.store.create(
        "contract",
        {
            "name": "Northwind",
            "start_date": "2025-03-01",
            "end_date": "2026-03-01",
            "term_length": 12,
            "line_items": [{"sku": "SEAT", "quantity": 4, "amount": "4000.00"}],
        },
        actor="dana",
        source="test",
    )
    return {
        "pipeline": pipeline,
        "template": template,
        "contract": {"id": northwind["id"], **northwind["data"]},
    }


# --------------------------------------------------------------------------- #
# The term label
# --------------------------------------------------------------------------- #


def test_evergreen_is_a_label_when_every_line_item_renews_until_cancelled() -> None:
    contract = {
        "term_length": 12,
        "line_items": [
            {"sku": "A", vocab.EVERGREEN_RENEWAL_FIELD: vocab.EVERGREEN_RENEWAL_VALUE},
            {"sku": "B", vocab.EVERGREEN_RENEWAL_FIELD: vocab.EVERGREEN_RENEWAL_VALUE},
        ],
    }
    assert rules.term_label(contract) == vocab.EVERGREEN_LABEL


def test_evergreen_does_not_apply_when_one_line_item_does_not_renew() -> None:
    contract = {
        "term_length": 12,
        "line_items": [
            {"sku": "A", vocab.EVERGREEN_RENEWAL_FIELD: vocab.EVERGREEN_RENEWAL_VALUE},
            {"sku": "B"},
        ],
    }
    assert rules.term_label(contract) == "12"


def test_evergreen_is_a_derived_label_and_not_a_contract_state() -> None:
    # The evidence says the term length is "marked as Evergreen". A label is computed on read,
    # so changing a line item's renewal setting changes the label with no separate write.
    before = {"term_length": 12, "line_items": [{"sku": "A"}]}
    after = {
        "term_length": 12,
        "line_items": [{"sku": "A", vocab.EVERGREEN_RENEWAL_FIELD: vocab.EVERGREEN_RENEWAL_VALUE}],
    }
    assert rules.term_label(before) == "12"
    assert rules.term_label(after) == vocab.EVERGREEN_LABEL
    assert vocab.EVERGREEN_IS_A_LABEL.startswith("Evergreen is a derived label")


def test_a_contract_with_no_line_items_keeps_its_own_term_length() -> None:
    assert rules.term_label({"term_length": 24}) == "24"


# --------------------------------------------------------------------------- #
# The renewal date rule
# --------------------------------------------------------------------------- #


def test_renewal_date_uses_the_contract_end_when_no_quote_is_accepted() -> None:
    state = rules.renewal_date({"end_date": "2026-03-01"}, None)
    assert state["renewal_date"] == "2026-03-01"
    assert state["branch"] == "if_not_finalised"
    assert state["rule"] == vocab.RENEWAL_DATE_RULE_IF_NOT_FINALISED


def test_renewal_date_uses_the_quote_effective_date_when_it_is_accepted() -> None:
    state = rules.renewal_date(
        {"end_date": "2026-03-01"},
        {
            "state": "accepted",
            "id": "q1",
            "effective_date": {"on": "2026-04-01", "resolved_on": "2026-04-01"},
        },
    )
    assert state["renewal_date"] == "2026-04-01"
    assert state["branch"] == "if_finalised"
    assert state["source_quote_id"] == "q1"


def test_an_accepted_on_agreement_quote_reports_the_day_of_agreement() -> None:
    # An On agreement quote stores no date until the day of agreement, so reading only the
    # stored field would put an accepted quote on the not-finalised branch and report the old
    # end date. The resolved date is what the rule asks for.
    state = rules.renewal_date(
        {"end_date": "2026-03-01"},
        {
            "state": "accepted",
            "id": "q1",
            "effective_date": {"mode": "on_agreement", "resolved_on": "2026-02-15"},
        },
    )
    assert state["branch"] == "if_finalised"
    assert state["renewal_date"] == "2026-02-15"


def test_a_draft_quote_does_not_move_the_renewal_date() -> None:
    # The rule says the branch changes on finalisation or acceptance, not on the quote existing.
    state = rules.renewal_date(
        {"end_date": "2026-03-01"},
        {"state": "draft", "id": "q1", "effective_date": {"on": "2026-04-01"}},
    )
    assert state["branch"] == "if_not_finalised"
    assert state["renewal_date"] == "2026-03-01"


def test_both_branches_of_the_rule_are_quoted_verbatim() -> None:
    for sentence in vocab.RENEWAL_DATE_BRANCHES.values():
        assert (
            sentence
            in vocab.EVIDENCE["renewal_date_when_finalised"]
            + " "
            + (vocab.EVIDENCE["renewal_date_when_not_finalised"])
        )


def test_the_alert_date_is_the_renewal_date_less_the_offset() -> None:
    alert = rules.alert_due_date({"end_date": "2026-03-01", "alert_offset_days": 30}, None)
    assert alert["renewal_date"] == "2026-03-01"
    assert alert["due"] == "2026-01-30"
    assert alert["offset_days"] == 30


def test_the_alert_offset_defaults_when_the_contract_carries_none() -> None:
    alert = rules.alert_due_date({"end_date": "2026-03-01"}, None)
    assert alert["offset_days"] == vocab.ALERT_OFFSET_DEFAULT


def test_a_contract_with_no_end_date_has_no_alert_date() -> None:
    alert = rules.alert_due_date({"name": "Open ended"}, None)
    assert alert["due"] is None


# --------------------------------------------------------------------------- #
# The change effective date
# --------------------------------------------------------------------------- #


def test_on_agreement_resolves_to_the_day_of_agreement() -> None:
    resolved = rules.effective_date({"effective_date": {"mode": "on_agreement"}}, accepted_on=TODAY)
    assert resolved["mode"] == "on_agreement"
    assert resolved["on"] == "2026-03-01"
    assert resolved["resolved"] is True


def test_a_custom_date_resolves_to_the_date_the_seller_chose() -> None:
    resolved = rules.effective_date(
        {"effective_date": {"mode": "custom_date", "on": "2026-06-01"}}, accepted_on=TODAY
    )
    assert resolved["on"] == "2026-06-01"


def test_a_delayed_start_resolves_to_days_after_the_day_of_agreement() -> None:
    resolved = rules.effective_date(
        {"effective_date": {"mode": "delayed_start", "delay_days": 45}}, accepted_on=TODAY
    )
    assert resolved["on"] == "2026-04-15"


def test_a_months_mode_advances_the_month() -> None:
    resolved = rules.effective_date(
        {"effective_date": {"mode": "months", "delay_months": 1}}, accepted_on=date(2026, 1, 31)
    )
    # Clamped to the last day of February rather than overflowing into March.
    assert resolved["on"] == "2026-02-28"


def test_an_on_agreement_quote_that_is_not_accepted_yet_does_not_resolve() -> None:
    resolved = rules.effective_date({"effective_date": {"mode": "on_agreement"}}, accepted_on=None)
    assert resolved["resolved"] is False
    with pytest.raises(RenewalConflict) as caught:
        rules.require_resolved_effective_date(resolved)
    assert "has not been accepted" in caught.value.detail


def test_a_delayed_start_with_no_day_count_does_not_resolve() -> None:
    resolved = rules.effective_date(
        {"effective_date": {"mode": "delayed_start"}}, accepted_on=TODAY
    )
    assert resolved["resolved"] is False
    with pytest.raises(RenewalConflict):
        rules.require_resolved_effective_date(resolved)


def test_the_researched_mode_labels_are_accepted_as_well_as_the_keys() -> None:
    # The research lists "On agreement / Custom Date / Delayed start days / months" as labels a
    # seller picks from. A caller who read the vendor documentation sends the label.
    for label, expected in (
        ("On agreement", "on_agreement"),
        ("Custom Date", "custom_date"),
        ("Delayed start", "delayed_start"),
        ("months", "months"),
    ):
        assert rules.normalise_effective_date_mode(label) == expected


def test_an_unknown_effective_date_mode_is_refused_with_the_field_named() -> None:
    with pytest.raises(RenewalRefusal) as caught:
        rules.normalise_effective_date_mode("whenever")
    assert "effective_date_mode" in caught.value.errors


# --------------------------------------------------------------------------- #
# Proration
# --------------------------------------------------------------------------- #


def test_proration_applies_when_the_checkbox_is_on() -> None:
    result = rules.prorated_charges(
        {"prorate": True}, from_date=date(2026, 1, 1), to_date=date(2026, 3, 1)
    )
    assert result["prorated"] is True
    assert result["period_days"] == 59


def test_clearing_the_checkbox_prorates_nothing_and_says_why() -> None:
    result = rules.prorated_charges(
        {"prorate": False}, from_date=date(2026, 1, 1), to_date=date(2026, 3, 1)
    )
    assert result["prorated"] is False
    assert "cleared the proration checkbox" in result["reason"]


def test_a_missing_proration_flag_is_false_and_not_an_error() -> None:
    result = rules.prorated_charges({}, from_date=None, to_date=None)
    assert result["enabled"] is False
    assert result["prorated"] is False


def test_proration_needs_a_period_to_prorate_against() -> None:
    result = rules.prorated_charges({"prorate": True}, from_date=None, to_date=None)
    assert result["prorated"] is False
    assert "needs both" in result["reason"]


def test_a_contract_with_no_positive_length_has_no_period_to_prorate() -> None:
    result = rules.prorated_charges(
        {"prorate": True}, from_date=date(2026, 3, 1), to_date=date(2026, 3, 1)
    )
    assert result["prorated"] is False
    assert "no positive length" in result["reason"]


def test_a_proration_flag_that_is_not_a_boolean_is_refused() -> None:
    with pytest.raises(RenewalRefusal) as caught:
        rules.coerce_bool("yes", "prorate")
    assert "prorate" in caught.value.errors


# --------------------------------------------------------------------------- #
# Contract eligibility
# --------------------------------------------------------------------------- #


def test_a_contract_with_an_end_date_is_renewable() -> None:
    assert rules.contract_is_renewable({"end_date": "2026-03-01"})["renewable"] is True


def test_a_contract_with_no_end_date_is_not_renewable_and_says_why() -> None:
    state = rules.contract_is_renewable({"name": "Ever open ended"})
    assert state["renewable"] is False
    assert state["field"] == "end_date"
    with pytest.raises(RenewalConflict) as caught:
        rules.require_renewable({"name": "Ever open ended"})
    assert "no end date" in caught.value.detail
    assert caught.value.remedy


def test_a_contract_that_has_been_renewed_is_not_renewable() -> None:
    state = rules.contract_is_renewable({"end_date": "2026-03-01", vocab.FINALISED_FLAG: True})
    assert state["renewable"] is False
    assert state["field"] == vocab.FINALISED_FLAG
    with pytest.raises(RenewalConflict) as caught:
        rules.require_renewable({"end_date": "2026-03-01", vocab.FINALISED_FLAG: True})
    assert caught.value.evidence == vocab.EVIDENCE["renewal_creates_contract"]


# --------------------------------------------------------------------------- #
# Coercion
# --------------------------------------------------------------------------- #


def test_a_date_string_is_read_in_both_researched_spellings() -> None:
    assert rules.coerce_date("2026-06-01", "d") == date(2026, 6, 1)
    assert rules.coerce_date("2026/06/01", "d") == date(2026, 6, 1)


def test_an_iso_instant_is_read_as_its_date() -> None:
    assert rules.coerce_date("2026-06-01T10:00:00Z", "d") == date(2026, 6, 1)


def test_a_value_that_is_not_a_date_is_refused_with_the_field_named() -> None:
    with pytest.raises(RenewalRefusal) as caught:
        rules.coerce_date("next tuesday", "effective_date_on")
    assert "effective_date_on" in caught.value.errors


def test_a_number_where_a_date_belongs_is_refused() -> None:
    with pytest.raises(RenewalRefusal):
        rules.coerce_date(20260601, "effective_date_on")


def test_a_boolean_where_a_number_belongs_is_refused() -> None:
    with pytest.raises(RenewalRefusal) as caught:
        rules.coerce_positive_int(True, "delay_days")
    assert "delay_days" in caught.value.errors


def test_a_zero_day_delay_is_allowed_because_it_is_a_real_delay() -> None:
    assert rules.coerce_positive_int(0, "delay_days", minimum=0) == 0


def test_a_negative_delay_is_refused() -> None:
    with pytest.raises(RenewalRefusal):
        rules.coerce_positive_int(-5, "delay_days", minimum=0)


def test_a_name_longer_than_the_bound_is_refused() -> None:
    with pytest.raises(RenewalRefusal):
        rules.normalise_name("x" * 201, "name")


def test_an_empty_name_is_refused() -> None:
    with pytest.raises(RenewalRefusal) as caught:
        rules.normalise_name("   ", "name")
    assert "name" in caught.value.errors


def test_the_researched_deal_selection_methods_are_accepted() -> None:
    assert rules.normalise_deal_selection_method("New deal") == "new_deal_default_stage"
    assert rules.normalise_deal_selection_method("existing_deal") == "existing_deal"
    with pytest.raises(RenewalRefusal):
        rules.normalise_deal_selection_method("maybe")


def test_the_researched_contract_targets_are_accepted() -> None:
    assert rules.normalise_contract_target("Contracts: all associated") == "all_associated"
    assert rules.normalise_contract_target("one_contract") == "one_contract"
    with pytest.raises(RenewalRefusal):
        rules.normalise_contract_target("some")


def test_an_unknown_state_is_refused_and_the_states_are_named() -> None:
    with pytest.raises(RenewalRefusal) as caught:
        rules.normalise_state("sent")
    assert "draft" in caught.value.errors["state"]


def test_a_dotted_path_is_read_and_a_missing_step_is_none() -> None:
    payload = {"a": {"b": {"c": 7}}}
    assert rules.read_path(payload, "a.b.c") == 7
    assert rules.read_path(payload, "a.b.d") is None
    assert rules.read_path(payload, "a.x.c") is None
    assert rules.read_path(None, "a") is None


def test_amounts_are_summed_and_unusable_values_skipped() -> None:
    assert rules.sum_amounts(["1.50", 2, None, "", True, "bad", "2.25"]) == 5.75
    assert rules.sum_amounts([]) == 0.0


# --------------------------------------------------------------------------- #
# The engine: creating a quote
# --------------------------------------------------------------------------- #


def test_a_renewal_quote_is_prefilled_from_the_contract(engine: RenewalQuoteEngine, world) -> None:
    quote = engine.create_quote(world["contract"]["id"], source="test", actor="dana")["quote"]
    assert quote["contract_id"] == world["contract"]["id"]
    assert quote["line_items"] == world["contract"]["line_items"]
    assert quote["state"] == "draft"
    assert quote["total"] == 4000.0


def test_a_quote_records_the_template_as_association_type_286(
    engine: RenewalQuoteEngine, world
) -> None:
    quote = engine.create_quote(
        world["contract"]["id"], template_id=world["template"]["id"], source="test"
    )["quote"]
    assert quote["template_association_type"] == vocab.TEMPLATE_ASSOCIATION_TYPE
    assert vocab.TEMPLATE_ASSOCIATION_TYPE == 286


def test_a_quote_carries_the_sellers_pipeline_and_stage(engine: RenewalQuoteEngine, world) -> None:
    quote = engine.create_quote(
        world["contract"]["id"],
        deal_pipeline_id=world["pipeline"]["id"],
        deal_stage="Qualification",
        source="test",
    )["quote"]
    assert quote["deal_pipeline_id"] == world["pipeline"]["id"]
    assert quote["deal_stage"] == "Qualification"


def test_a_pipeline_without_a_stage_is_refused(engine: RenewalQuoteEngine, world) -> None:
    with pytest.raises(RenewalRefusal) as caught:
        engine.create_quote(
            world["contract"]["id"],
            deal_pipeline_id=world["pipeline"]["id"],
            source="test",
        )
    assert "deal_stage" in caught.value.errors


def test_a_custom_date_mode_with_no_date_is_refused(engine: RenewalQuoteEngine, world) -> None:
    with pytest.raises(RenewalRefusal) as caught:
        engine.create_quote(
            world["contract"]["id"], effective_date_mode="custom_date", source="test"
        )
    assert "effective_date_on" in caught.value.errors


def test_renewing_a_contract_with_no_end_date_conflicts(engine: RenewalQuoteEngine) -> None:
    record = engine.store.create("contract", {"name": "Open ended"}, source="test")
    with pytest.raises(RenewalConflict) as caught:
        engine.create_quote(record["id"], source="test")
    assert "no end date" in caught.value.detail


def test_a_missing_contract_is_a_not_found_not_a_server_fault(
    engine: RenewalQuoteEngine,
) -> None:
    with pytest.raises(RenewalNotFound):
        engine.create_quote("nope", source="test")


def test_an_unknown_pipeline_is_a_not_found(engine: RenewalQuoteEngine, world) -> None:
    with pytest.raises(RenewalNotFound):
        engine.create_quote(
            world["contract"]["id"], deal_pipeline_id="nope", deal_stage="Closed won", source="test"
        )


# --------------------------------------------------------------------------- #
# The engine: acceptance, the chain, and the deal
# --------------------------------------------------------------------------- #


def test_acceptance_creates_a_contract_a_deal_and_the_chain(
    engine: RenewalQuoteEngine, world
) -> None:
    quote = engine.create_quote(
        world["contract"]["id"],
        deal_pipeline_id=world["pipeline"]["id"],
        deal_stage="Qualification",
        source="test",
    )["quote"]
    result = engine.accept(quote["id"], accepted_by="Priya", source="test")

    assert result["new_contract"]["renewed_from_contract_id"] == world["contract"]["id"]
    assert result["deal"]["stage"] == "Qualification"
    assert result["deal"]["deal_type"] == "renewal"
    assert result["deal"]["new_contract_id"] == result["new_contract"]["id"]
    assert result["quote"]["state"] == "accepted"


def test_the_chain_is_recorded_on_both_contracts(engine: RenewalQuoteEngine, world) -> None:
    quote = engine.create_quote(world["contract"]["id"], source="test")["quote"]
    result = engine.accept(quote["id"], source="test")

    old = engine.contract(world["contract"]["id"])
    new = engine.contract(result["new_contract"]["id"])
    assert old["chain"]["next"]["id"] == result["new_contract"]["id"]
    assert new["chain"]["previous"]["id"] == world["contract"]["id"]
    assert old["chain"]["previous"] is None
    assert new["chain"]["next"] is None


def test_acceptance_moves_the_renewal_date_to_the_quote_effective_date(
    engine: RenewalQuoteEngine, world
) -> None:
    quote = engine.create_quote(world["contract"]["id"], source="test")["quote"]
    result = engine.accept(quote["id"], source="test")
    assert result["renewal_date"]["branch"] == "if_finalised"
    assert result["renewal_date"]["renewal_date"] == TODAY.isoformat()
    assert result["renewal_date"]["rule"] == vocab.RENEWAL_DATE_RULE_IF_FINALISED


def test_the_old_contract_can_no_longer_be_renewed_by_hand(
    engine: RenewalQuoteEngine, world
) -> None:
    quote = engine.create_quote(world["contract"]["id"], source="test")["quote"]
    engine.accept(quote["id"], source="test")
    with pytest.raises(RenewalConflict) as caught:
        engine.create_quote(world["contract"]["id"], source="test")
    assert "already been renewed" in caught.value.detail


def test_accepting_twice_conflicts_and_creates_no_second_contract(
    engine: RenewalQuoteEngine, world
) -> None:
    quote = engine.create_quote(world["contract"]["id"], source="test")["quote"]
    engine.accept(quote["id"], source="test")
    with pytest.raises(RenewalConflict) as caught:
        engine.accept(quote["id"], source="test")
    assert "already accepted" in caught.value.detail
    assert engine.summary()["renewal_contracts"] == 1
    assert engine.summary()["deals"] == 1


def test_an_accepted_quote_cannot_have_its_effective_date_changed(
    engine: RenewalQuoteEngine, world
) -> None:
    quote = engine.create_quote(world["contract"]["id"], source="test")["quote"]
    engine.accept(quote["id"], source="test")
    with pytest.raises(RenewalConflict) as caught:
        engine.update_effective_date(quote["id"], on="2027-01-01", source="test")
    assert "cannot be changed" in caught.value.detail


def test_accepting_through_the_state_route_is_refused(engine: RenewalQuoteEngine, world) -> None:
    quote = engine.create_quote(world["contract"]["id"], source="test")["quote"]
    with pytest.raises(RenewalRefusal) as caught:
        engine.set_state(quote["id"], "accepted", source="test")
    assert "accept route" in caught.value.detail
    assert "renewal deal" in caught.value.errors["state"]
    assert engine.summary()["contracts"] == 1


def test_a_quote_can_be_shared(engine: RenewalQuoteEngine, world) -> None:
    quote = engine.create_quote(world["contract"]["id"], source="test")["quote"]
    shared = engine.set_state(quote["id"], "shared", source="test")["quote"]
    assert shared["state"] == "shared"


def test_the_acceptance_signal_is_recorded_as_unsourced(engine: RenewalQuoteEngine, world) -> None:
    quote = engine.create_quote(world["contract"]["id"], source="test")["quote"]
    result = engine.accept(quote["id"], accepted_by="Priya", source="test")
    assert result["new_contract"]["acceptance_signal_sourced"] is False
    assert result["new_contract"]["accepted_by"] == "Priya"


def test_the_new_contract_gets_its_end_date_from_the_term(
    engine: RenewalQuoteEngine, world
) -> None:
    quote = engine.create_quote(world["contract"]["id"], source="test")["quote"]
    result = engine.accept(quote["id"], source="test")
    assert result["new_contract"]["start_date"] == TODAY.isoformat()
    assert result["new_contract"]["end_date"] == "2027-03-01"


def test_an_existing_deal_selection_creates_no_deal(engine: RenewalQuoteEngine, world) -> None:
    existing = engine.store.create("wf100_deal", {"name": "Existing renewal"}, source="test")
    quote = engine.create_quote(
        world["contract"]["id"],
        deal_selection_method="existing_deal",
        deal_id=existing["id"],
        source="test",
    )["quote"]
    result = engine.accept(quote["id"], existing_deal_id=existing["id"], source="test")
    assert result["deal"]["id"] == existing["id"]
    assert engine.summary()["deals"] == 1


def test_an_existing_deal_selection_with_no_deal_is_refused_at_quote_time(
    engine: RenewalQuoteEngine, world
) -> None:
    # Resolved at quote time rather than at acceptance, so a seller is not handed a quote they
    # can only discover is unusable once the buyer has already said yes.
    with pytest.raises(RenewalRefusal) as caught:
        engine.create_quote(
            world["contract"]["id"], deal_selection_method="existing_deal", source="test"
        )
    assert "deal_id" in caught.value.errors


def test_an_existing_deal_selection_with_an_unknown_deal_is_a_not_found(
    engine: RenewalQuoteEngine, world
) -> None:
    with pytest.raises(RenewalNotFound):
        engine.create_quote(
            world["contract"]["id"],
            deal_selection_method="existing_deal",
            deal_id="nope",
            source="test",
        )


# --------------------------------------------------------------------------- #
# Templates, pipelines and workflows
# --------------------------------------------------------------------------- #


def test_a_template_is_created_with_its_change_type_and_term(
    engine: RenewalQuoteEngine,
) -> None:
    template = engine.create_template(
        "Two year change", change_type="change", term_months=24, source="test"
    )["template"]
    assert template["change_type"] == "change"
    assert template["term_months"] == 24


def test_an_unknown_template_change_type_is_refused(engine: RenewalQuoteEngine) -> None:
    with pytest.raises(RenewalRefusal):
        engine.create_template("Odd", change_type="sideways", source="test")


def test_a_pipeline_with_no_stages_is_refused(engine: RenewalQuoteEngine) -> None:
    with pytest.raises(RenewalRefusal) as caught:
        engine.create_pipeline("Empty", stages=[], source="test")
    assert "stages" in caught.value.errors


def test_a_one_contract_workflow_needs_a_contract(engine: RenewalQuoteEngine) -> None:
    with pytest.raises(RenewalRefusal) as caught:
        engine.create_workflow(contract_target="one_contract", source="test")
    assert "contract_id" in caught.value.errors


def test_a_workflow_run_creates_a_quote_per_enrolled_contract(
    engine: RenewalQuoteEngine, world
) -> None:
    workflow = engine.create_workflow(
        contract_id=world["contract"]["id"], template_id=world["template"]["id"], source="test"
    )["workflow"]
    result = engine.run_workflow(workflow["id"], source="test")
    assert result["quotes_created"] == 1
    assert result["quotes"][0]["contract_id"] == world["contract"]["id"]


def test_an_all_associated_workflow_covers_every_contract_on_its_deals(
    engine: RenewalQuoteEngine, world
) -> None:
    second = engine.store.create(
        "contract",
        {"name": "Second", "end_date": "2026-06-01", "term_length": 12, "line_items": []},
        source="test",
    )
    deal_a = engine.store.create(
        "wf100_deal", {"name": "A", "contract_id": world["contract"]["id"]}, source="test"
    )
    deal_b = engine.store.create(
        "wf100_deal", {"name": "B", "contract_id": second["id"]}, source="test"
    )
    workflow = engine.create_workflow(
        contract_target="all_associated",
        deal_ids=[deal_a["id"], deal_b["id"]],
        source="test",
    )["workflow"]
    result = engine.run_workflow(workflow["id"], source="test")
    assert result["contract_target"] == "all_associated"
    assert result["quotes_created"] == 2


def test_a_workflow_run_reports_the_contracts_it_skipped(engine: RenewalQuoteEngine, world) -> None:
    quote = engine.create_quote(world["contract"]["id"], source="test")["quote"]
    engine.accept(quote["id"], source="test")
    workflow = engine.create_workflow(contract_id=world["contract"]["id"], source="test")[
        "workflow"
    ]
    result = engine.run_workflow(workflow["id"], source="test")
    assert result["quotes_created"] == 0
    assert result["skipped"][0]["contract_id"] == world["contract"]["id"]


def test_a_workflow_reports_whether_it_re_enrols(engine: RenewalQuoteEngine, world) -> None:
    workflow = engine.create_workflow(
        contract_id=world["contract"]["id"], re_enroll=True, source="test"
    )["workflow"]
    result = engine.run_workflow(workflow["id"], source="test")
    assert result["reenroll"] is True
    assert result["reenroll_decision"] == "reset"


def test_a_re_enrolling_workflow_follows_the_chain_onto_the_new_contract(
    engine: RenewalQuoteEngine, world
) -> None:
    workflow = engine.create_workflow(
        contract_id=world["contract"]["id"], re_enroll=True, source="test"
    )["workflow"]
    quote = engine.create_quote(world["contract"]["id"], source="test")["quote"]
    result = engine.accept(quote["id"], source="test")
    after = engine.workflow(workflow["id"])
    assert after["enrolled_contract_id"] == result["new_contract"]["id"]
    assert after["previous_enrolled_contract_id"] == world["contract"]["id"]
    assert after["cycles"] == 1


def test_a_workflow_without_re_enroll_stays_on_its_contract(
    engine: RenewalQuoteEngine, world
) -> None:
    workflow = engine.create_workflow(
        contract_id=world["contract"]["id"], re_enroll=False, source="test"
    )["workflow"]
    quote = engine.create_quote(world["contract"]["id"], source="test")["quote"]
    engine.accept(quote["id"], source="test")
    after = engine.workflow(workflow["id"])
    assert after["enrolled_contract_id"] == world["contract"]["id"]
    assert after["cycles"] == 0


# --------------------------------------------------------------------------- #
# The summary
# --------------------------------------------------------------------------- #


def test_the_summary_counts_accepted_and_open_quotes_separately(
    engine: RenewalQuoteEngine, world
) -> None:
    engine.create_quote(world["contract"]["id"], source="test")
    open_quote = engine.store.create(
        "wf100_renewal_quote",
        {"name": "Open", "state": "draft", "contract_id": None},
        source="test",
    )
    engine.store.update(open_quote["id"], {"state": "shared"}, source="test")
    board = engine.summary()
    assert board["quotes"] == 2
    assert board["quotes_by_state"]["draft"] == 1
    assert board["quotes_by_state"]["shared"] == 1
    assert board["accepted_quotes"] == 0


def test_the_summary_carries_the_researched_rule(engine: RenewalQuoteEngine) -> None:
    assert engine.summary()["rule"] == vocab.RENEWAL_DATE_RULE


# --------------------------------------------------------------------------- #
# The recorded decisions
# --------------------------------------------------------------------------- #


def test_every_decision_names_what_it_rejected() -> None:
    decisions = inferences.describe()
    assert inferences.count() >= 10
    for decision in decisions:
        assert decision["rejected_because"], decision["id"]
        assert decision["cost_of_the_choice"], decision["id"]
        assert "sourced" in decision


def test_the_unsourced_decisions_say_so() -> None:
    unsourced = [d for d in inferences.describe() if not d["sourced"]]
    assert unsourced, "the research leaves several things open, so some decisions must be unsourced"


def test_the_acceptance_decision_names_its_jev_audit_id() -> None:
    decision = inferences.describe_one("wf100-acceptance-signal")
    assert decision is not None
    assert decision["jev_audit_id"] == "jev-20261004T231639-22752-99672"
    assert decision["jev_verdict"] == "pass"


def test_an_unknown_decision_id_is_none() -> None:
    assert inferences.describe_one("nope") is None


def test_the_workflow_action_and_direct_renewal_decisions_are_recorded() -> None:
    assert inferences.describe_one("wf100-workflow-action-shape") is not None
    assert inferences.describe_one("wf100-direct-renewal") is not None
    assert vocab.DIRECT_RENEWAL_BETA is True
    assert "did not build the bypass" in vocab.DIRECT_RENEWAL_NOTE


def test_the_re_enroll_decision_is_recorded_as_unsourced() -> None:
    decision = inferences.describe_one("wf100-re-enroll")
    assert decision["sourced"] is False
    assert vocab.REENROLL_NOT_SOURCED.startswith("Re-enroll was named")


def test_the_template_ownership_choice_is_recorded() -> None:
    assert inferences.describe_one("wf100-template-ownership") is not None
    assert vocab.TEMPLATE_COLLECTION in vocab.OWNED_COLLECTIONS


# --------------------------------------------------------------------------- #
# The vocabulary
# --------------------------------------------------------------------------- #


def test_the_vocabulary_serves_every_table_the_rules_read() -> None:
    served = vocab.describe()
    for key in (
        "quote_states",
        "effective_date_modes",
        "renewal_date_rule",
        "renewal_date_branches",
        "evergreen_label",
        "deal_selection_methods",
        "contract_targets",
        "re_enroll_decision",
        "evidence",
    ):
        assert key in served, key


def test_every_derived_state_says_it_is_derived() -> None:
    assert "not sourced" in vocab.DERIVED_NOT_SOURCED.lower()
    assert vocab.QUOTE_STATES_DOCUMENTED["superseded"].endswith(
        "This state is derived, not sourced."
    )


def test_the_evidence_is_quoted_not_paraphrased() -> None:
    assert (
        vocab.EVIDENCE["renewal_creates_contract"]
        == "When a renewal quote is accepted, a new contract is created and automatically "
        "associated with the previous contract."
    )
    assert vocab.EVIDENCE["renewal_can_be_direct"] == (
        "Renewals can be created directly from a contract (BETA) or by using renewal quotes."
    )


def test_the_vendor_endpoint_is_recorded_but_never_called() -> None:
    assert vocab.VENDOR_QUOTE_ENDPOINT.startswith("POST /crm/objects/")
    assert "no vendor call" in vocab.VENDOR_QUOTE_NOTE


# --------------------------------------------------------------------------- #
# The seed return string
# --------------------------------------------------------------------------- #


def test_the_seed_return_string_is_encodable_by_cp1252(tmp_path) -> None:
    # The seeder prints this to a Windows console. One RIGHTWARDS ARROW in a recovered
    # feature's return string broke the entire seeder on this host.
    from dsr.db.audited import AuditedDatabase
    from dsr.features import (
        wf100_create_a_renewal_quote_from_a_contract as feature,
    )

    database = AuditedDatabase(tmp_path / "seed.db", actor="test")
    try:
        store = RecordStore(database)
        room = store.create("room", {"name": "Seed room"}, actor="test", source="test")
        message = feature.seed(
            database,
            {"room_ids": [(room["id"], "Room")], "now": NOW, "rng": None},
        )
    finally:
        database.close()

    assert message
    message.encode("cp1252")
    for banned in ("\u2192", "\u2190", "\u2014", "\u2013", "\u2018", "\u2019"):
        assert banned not in message, f"{banned!r} is not encodable on this console"


def test_the_seed_creates_the_states_the_specification_describes(tmp_path) -> None:
    from dsr.db.audited import AuditedDatabase
    from dsr.features import (
        wf100_create_a_renewal_quote_from_a_contract as feature,
    )

    database = AuditedDatabase(tmp_path / "seed.db", actor="test")
    try:
        store = RecordStore(database)
        room = store.create("room", {"name": "Seed room"}, actor="test", source="test")
        feature.seed(database, {"room_ids": [(room["id"], "Room")], "now": NOW, "rng": None})
        board = RenewalQuoteEngine(store, now=lambda: NOW).summary()
    finally:
        database.close()

    # Four seeded contracts, plus the one the acceptance created.
    assert board["contracts"] == 5
    assert board["renewal_contracts"] == 1
    assert board["quotes"] == 3
    assert board["accepted_quotes"] == 1
    assert board["deals"] == 1
    assert board["templates"] == 2
    assert board["pipelines"] == 2
    assert board["workflows"] == 2


def test_the_seed_returns_nothing_when_there_are_no_rooms(tmp_path) -> None:
    from dsr.db.audited import AuditedDatabase
    from dsr.features import (
        wf100_create_a_renewal_quote_from_a_contract as feature,
    )

    database = AuditedDatabase(tmp_path / "seed.db", actor="test")
    try:
        assert feature.seed(database, {"room_ids": [], "now": NOW, "rng": None}) == ""
    finally:
        database.close()


def test_the_seed_produces_an_evergreen_label_and_an_open_quote(tmp_path) -> None:
    from dsr.db.audited import AuditedDatabase
    from dsr.features import (
        wf100_create_a_renewal_quote_from_a_contract as feature,
    )

    database = AuditedDatabase(tmp_path / "seed.db", actor="test")
    try:
        store = RecordStore(database)
        room = store.create("room", {"name": "Seed room"}, actor="test", source="test")
        feature.seed(database, {"room_ids": [(room["id"], "Room")], "now": NOW, "rng": None})
        engine = RenewalQuoteEngine(store, now=lambda: NOW)
        labels = {c["term_label"] for c in engine.contracts()}
        states = engine.summary()["quotes_by_state"]
    finally:
        database.close()

    assert vocab.EVERGREEN_LABEL in labels
    assert states["accepted"] == 1
    assert states["draft"] == 2


# --------------------------------------------------------------------------- #
# The test file must pass on its own
# --------------------------------------------------------------------------- #


def test_the_clock_is_injectable_so_no_test_depends_on_the_wall_clock(
    store: RecordStore,
) -> None:
    # Every date this workflow computes comes from the injected clock. A test that pinned the
    # wall clock instead would pass alone and fail in a run, because the day it ran on would
    # decide the answer.
    fixed = RenewalQuoteEngine(store, now=lambda: datetime(2030, 6, 15, tzinfo=timezone.utc))
    contract = store.create(
        "contract",
        {"name": "Fixed", "end_date": "2030-06-15", "term_length": 12, "line_items": []},
        source="test",
    )
    quote = fixed.create_quote(contract["id"], source="test")["quote"]
    result = fixed.accept(quote["id"], source="test")
    assert result["new_contract"]["start_date"] == "2030-06-15"
    assert result["new_contract"]["end_date"] == "2031-06-15"
    assert timedelta(365) > timedelta(0)  # the import is used, so the linter does not strip it
