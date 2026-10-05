"""WF-090: the quote-rules grammar, the evaluator and the engine.

The specification is a small SQL-like DSL with a hard reading: "a true evaluation is a
violation". Most of what this file proves is that the reading is not inverted, that a
question the quote cannot answer is reported rather than guessed, and that the two
stated limitations are refused with their own reason codes instead of being silently
accepted.

Nothing here starts an HTTP client. The grammar is pure, so a test hands it a context
dictionary, and the engine tests hand the engine a ``RecordStore`` over an in-memory
database. That keeps the behaviour under test visible in the assertion rather than
behind a request.
"""

from __future__ import annotations

import pytest
from dsr.quoting_proposals import (
    quote_guardrail_rules as rules,
    quote_guardrail_vocabulary as vocab,
)
from dsr.quoting_proposals.quote_guardrail_engine import QuoteGuardrailEngine
from dsr.store import RecordStore


def _quote_rule(**data) -> dict:
    """A rule record in the hydrated shape the engine hands the evaluator."""
    return {"id": data.pop("id", "rule_1"), "data": data}


def _block(definition: str, **extra) -> dict:
    return _quote_rule(
        rule_definition=definition,
        outcome=vocab.OUTCOME_BLOCK,
        message=extra.pop("message", "This quote breaks a rule."),
        status=vocab.STATUS_ENABLED,
        name=extra.pop("name", "A rule"),
        **extra,
    )


def _warn(definition: str, **extra) -> dict:
    return _quote_rule(
        rule_definition=definition,
        outcome=vocab.OUTCOME_WARNING,
        message=extra.pop("message", "This quote is unusual."),
        status=vocab.STATUS_ENABLED,
        name=extra.pop("name", "A warning rule"),
        **extra,
    )


LINE_ITEMS = [
    {"hs_product_id": "ENT-LICENSE", "platform": "modern", "discount": 40},
    {"hs_product_id": "SUPPORT-ADDON", "platform": "modern", "discount": 30},
]


# --------------------------------------------------------------------------- #
# The grammar
# --------------------------------------------------------------------------- #


def test_every_quoted_example_parses():
    """The five forms are the specification's own test corpus, so all five must read."""
    for example in vocab.DSL_EXAMPLES:
        parsed = rules.parse(example)
        assert parsed["kind"] in {"aggregate", "quantifier", "property"}
        assert parsed["normalised"]


def test_the_two_spellings_of_equality_normalise_to_one():
    """`=` and `==` are the same operator, and the stored form is the SQL spelling."""
    first = rules.parse('[quote.region] = "EMEA"')
    second = rules.parse('[quote.region] == "EMEA"')
    assert first["normalised"] == second["normalised"]
    assert "==" not in second["normalised"]


def test_a_true_evaluation_is_a_violation():
    """The one reading that must not be inverted."""
    context = {"quote": {"hs_quote_amount": 150000}, "line_item": []}
    verdict = rules.evaluate_rule(_block("[quote.hs_quote_amount] > 100000"), context)
    assert verdict["violation"] is True
    assert verdict["verdict"] == vocab.VERDICT_VIOLATION
    assert verdict["reason_code"] == vocab.REASON_BLOCKED


def test_a_false_evaluation_is_clear():
    context = {"quote": {"hs_quote_amount": 10}, "line_item": []}
    verdict = rules.evaluate_rule(_block("[quote.hs_quote_amount] > 100000"), context)
    assert verdict["violation"] is False
    assert verdict["verdict"] == vocab.VERDICT_CLEAR
    assert verdict["reason_code"] == vocab.REASON_SATISFIED


# --------------------------------------------------------------------------- #
# The two stated limitations, refused with their own code
# --------------------------------------------------------------------------- #


def test_arithmetic_inside_an_aggregate_is_refused_with_the_vendors_limitation():
    with pytest.raises(rules.UnsupportedRuleForm) as caught:
        rules.parse("SUM([quantity] * [price]) FROM line_item > 5")
    assert caught.value.code == vocab.REASON_ARITHMETIC_UNSUPPORTED
    assert "Arithmetic inside aggregate functions" in caught.value.detail


def test_a_quote_level_discount_property_is_refused():
    with pytest.raises(rules.UnsupportedRuleForm) as caught:
        rules.parse("[quote.hs_discount_percentage] > 10")
    assert caught.value.code == vocab.REASON_QUOTE_DISCOUNT_UNAVAILABLE
    assert "quote-level discount properties" in caught.value.detail


def test_an_unknown_scope_is_refused():
    with pytest.raises(rules.UnsupportedRuleForm) as caught:
        rules.parse("SOLD_TOGETHER FROM contract WHERE [x] IN (1)")
    assert caught.value.code == vocab.REASON_UNKNOWN_SCOPE


@pytest.mark.parametrize(
    "definition",
    [
        "",
        "   ",
        "[quote.amount] > 5 AND [quote.other] > 1",
        "[quote.amount] >",
        "SUM([quantity]) FROM",  # truncated after the scope keyword
        "[quote.amount] 5",
        "BOGUS FROM quote",
    ],
)
def test_a_definition_the_grammar_cannot_read_is_refused(definition):
    with pytest.raises(rules.GuardrailRefusal):
        rules.parse(definition)


def test_an_unscoped_top_level_property_is_refused():
    """`[hs_sku] > 5` names no scope, so there is no record to read it from."""
    with pytest.raises(rules.RuleSyntaxError):
        rules.parse("[hs_sku] > 5")


# --------------------------------------------------------------------------- #
# Aggregates
# --------------------------------------------------------------------------- #


def test_sum_over_line_items_counts_only_the_where_matches():
    context = {"quote": {}, "line_item": LINE_ITEMS + [{"discount": 5, "hs_sku": "OTHER"}]}
    parsed = rules.parse('SUM([discount]) FROM line_item WHERE [hs_sku] = "OTHER" > 1')
    result = rules.evaluate_expression(parsed, context)
    assert result["observed"] == 5


def test_each_aggregate_computes_its_own_value():
    context = {"line_item": [{"discount": 10}, {"discount": 20}, {"discount": 30}]}
    observed = {
        function: rules.evaluate_expression(
            rules.parse(f"{function}([discount]) FROM line_item > -1"), context
        )["observed"]
        for function in ("SUM", "MIN", "MAX", "AVG", "COUNT")
    }
    assert observed["SUM"] == 60
    assert observed["MIN"] == 10
    assert observed["MAX"] == 30
    assert observed["AVG"] == 20
    assert observed["COUNT"] == 3


def test_an_aggregate_over_an_empty_set_is_unverifiable_not_a_violation():
    context = {"line_item": []}
    verdict = rules.evaluate_rule(_block("SUM([discount]) FROM line_item > 60"), context)
    assert verdict["verdict"] == vocab.VERDICT_UNVERIFIABLE
    assert verdict["violation"] is False
    assert verdict["reason_code"] == vocab.REASON_UNVERIFIABLE


# --------------------------------------------------------------------------- #
# Quantifiers
# --------------------------------------------------------------------------- #


def test_sold_together_is_true_when_every_named_value_is_present():
    context = {
        "line_item": [
            {"hs_product_id": "A"},
            {"hs_product_id": "B"},
            {"hs_product_id": "C"},
        ]
    }
    parsed = rules.parse('SOLD_TOGETHER FROM line_item WHERE [hs_product_id] IN ("A","B","C")')
    assert rules.evaluate_expression(parsed, context)["verdict"] is True


def test_sold_together_is_false_when_one_named_value_is_missing():
    context = {"line_item": [{"hs_product_id": "A"}, {"hs_product_id": "B"}]}
    parsed = rules.parse('SOLD_TOGETHER FROM line_item WHERE [hs_product_id] IN ("A","B","C")')
    assert rules.evaluate_expression(parsed, context)["verdict"] is False


def test_incompatible_is_true_when_two_named_values_are_mixed():
    context = {"line_item": [{"platform": "legacy"}, {"platform": "modern"}]}
    parsed = rules.parse('INCOMPATIBLE FROM line_item WHERE [platform] IN ("legacy","modern")')
    assert rules.evaluate_expression(parsed, context)["verdict"] is True


def test_incompatible_is_false_when_only_one_value_is_present():
    context = {"line_item": [{"platform": "modern"}, {"platform": "modern"}]}
    parsed = rules.parse('INCOMPATIBLE FROM line_item WHERE [platform] IN ("legacy","modern")')
    assert rules.evaluate_expression(parsed, context)["verdict"] is False


def test_the_two_quantifiers_are_not_the_same_predicate():
    """All three present: SOLD_TOGETHER fires, INCOMPATIBLE does not."""
    context = {"line_item": [{"platform": "legacy"}, {"platform": "modern"}, {"platform": "other"}]}
    sold = rules.parse('SOLD_TOGETHER FROM line_item WHERE [platform] IN ("legacy","modern")')
    mixed = rules.parse('INCOMPATIBLE FROM line_item WHERE [platform] IN ("legacy","modern")')
    assert rules.evaluate_expression(sold, context)["verdict"] is True
    assert rules.evaluate_expression(mixed, context)["verdict"] is True


# --------------------------------------------------------------------------- #
# Missing properties and disabled rules
# --------------------------------------------------------------------------- #


def test_a_missing_property_is_unverifiable_and_never_blocks():
    context = {"quote": {"hs_quote_amount": 100}, "line_item": []}
    verdict = rules.evaluate_rule(_block('[quote.region] = "EMEA"'), context)
    assert verdict["verdict"] == vocab.VERDICT_UNVERIFIABLE
    assert verdict["unverifiable"] is True
    assert verdict["reason_code"] == vocab.REASON_MISSING_PROPERTY


def test_a_disabled_rule_is_skipped_with_its_own_reason_code():
    context = {"quote": {"hs_quote_amount": 999999}, "line_item": []}
    record = _block("[quote.hs_quote_amount] > 1")
    record["data"][vocab.FIELD_STATUS] = vocab.STATUS_DISABLED
    record["data"][vocab.FIELD_ENABLED] = False
    verdict = rules.evaluate_rule(record, context)
    assert verdict["verdict"] == vocab.VERDICT_SKIPPED
    assert verdict["reason_code"] == vocab.REASON_RULE_DISABLED
    assert verdict["violation"] is False


def test_a_definition_that_no_longer_parses_is_unverifiable_not_a_crash():
    """A rule stored before a grammar change must degrade, not 500 the page."""
    context = {"quote": {}, "line_item": []}
    verdict = rules.evaluate_rule(_block("AND AND AND"), context)
    assert verdict["verdict"] == vocab.VERDICT_UNVERIFIABLE
    assert verdict["reason_code"] == vocab.REASON_RULE_UNPARSEABLE


# --------------------------------------------------------------------------- #
# Splitting the verdicts
# --------------------------------------------------------------------------- #


def test_evaluate_rules_separates_blocks_from_warnings():
    context = {
        "quote": {"hs_quote_amount": 150000},
        "line_item": [{"discount": 40}, {"discount": 30}],
    }
    outcome = rules.evaluate_rules(
        [
            _block("SUM([discount]) FROM line_item > 60", name="deep"),
            _warn("[quote.hs_quote_amount] > 100000", name="large"),
            _block("[quote.absent] > 1", name="unverifiable"),
        ],
        context,
    )
    assert outcome["blocked"] is True
    assert [one["rule_name"] for one in outcome["blocking"]] == ["deep"]
    assert [one["rule_name"] for one in outcome["warnings"]] == ["large"]
    assert [one["rule_name"] for one in outcome["unverifiable"]] == ["unverifiable"]
    assert outcome["reason_code"] == vocab.REASON_BLOCKED


def test_a_warning_does_not_block_a_publish():
    context = {"quote": {"hs_quote_amount": 150000}, "line_item": []}
    outcome = rules.evaluate_rules([_warn("[quote.hs_quote_amount] > 100000")], context)
    assert outcome["blocked"] is False
    assert outcome["publishable"] is True
    assert outcome["reason_code"] == vocab.REASON_WARNING


def test_the_refusal_message_names_the_rule_and_the_count():
    context = {"quote": {"hs_quote_amount": 150000}, "line_item": []}
    outcome = rules.evaluate_rules(
        [
            _block("[quote.hs_quote_amount] > 1", message="First rule."),
            _block("[quote.hs_quote_amount] > 2", message="Second rule."),
        ],
        context,
    )
    message = rules.refusal_message(outcome["blocking"])
    assert "First rule." in message
    assert "2 blocking rules" in message


# --------------------------------------------------------------------------- #
# The engine over the store
# --------------------------------------------------------------------------- #


@pytest.fixture
def engine(store: RecordStore) -> QuoteGuardrailEngine:
    return QuoteGuardrailEngine(store)


def _make_quote(engine: QuoteGuardrailEngine, items=None, _room=None, **quote):
    record = engine.store.create(
        vocab.SOURCE_QUOTES,
        {"name": "Test quote", "hs_quote_amount": 1000, **quote},
        room_id=_room,
        actor="dana",
        source="POST /api/records",
    )
    for item in items or []:
        engine.store.create(
            vocab.LINE_ITEMS,
            {"quote_id": record["id"], **item},
            room_id=record.get("room_id"),
            actor="dana",
            source="POST /api/records",
        )
    return record


def test_creating_a_rule_stores_the_normalised_definition(engine: QuoteGuardrailEngine):
    record = engine.create_rule(
        {
            "name": "Deep discount",
            "rule_definition": "SUM([discount]) FROM line_item > 60",
            "outcome": vocab.OUTCOME_BLOCK,
            "message": "Too deep.",
        },
        source="POST /api/wf-090/rules",
    )
    data = record["data"]
    assert data["normalised_definition"]
    assert data["status"] == vocab.STATUS_ENABLED
    assert data["enabled"] is True


def test_a_rule_with_an_unknown_outcome_is_refused_with_every_problem(engine):
    with pytest.raises(rules.InvalidRule) as caught:
        engine.create_rule(
            {"name": "x", "rule_definition": "[quote.a] > 1", "outcome": "banish", "message": ""},
            source="POST /api/wf-090/rules",
        )
    fields = {one["field"] for one in caught.value.errors}
    assert vocab.FIELD_OUTCOME in fields
    assert vocab.FIELD_MESSAGE in fields


def test_a_rule_with_an_unreadable_definition_is_refused(engine):
    with pytest.raises(rules.InvalidRule) as caught:
        engine.create_rule(
            {
                "name": "x",
                "rule_definition": "SUM([a] * [b]) FROM line_item > 1",
                "outcome": vocab.OUTCOME_BLOCK,
                "message": "m",
            },
            source="POST /api/wf-090/rules",
        )
    assert any(
        one.get("error") == vocab.REASON_ARITHMETIC_UNSUPPORTED for one in caught.value.errors
    )


def test_patching_the_status_switch_toggles_without_touching_the_definition(engine):
    record = engine.create_rule(
        {
            "name": "Deep discount",
            "rule_definition": "SUM([discount]) FROM line_item > 60",
            "outcome": vocab.OUTCOME_BLOCK,
            "message": "Too deep.",
        },
        source="POST /api/wf-090/rules",
    )
    patched = engine.patch_rule(
        record["id"], {"status": vocab.STATUS_DISABLED}, source="PATCH /api/wf-090/rules/x"
    )
    data = patched["data"]
    assert data["status"] == vocab.STATUS_DISABLED
    assert data["enabled"] is False
    assert data["rule_definition"] == "SUM([discount]) FROM line_item > 60"


def test_a_patch_that_would_leave_an_invalid_rule_is_refused(engine):
    record = engine.create_rule(
        {
            "name": "Deep discount",
            "rule_definition": "SUM([discount]) FROM line_item > 60",
            "outcome": vocab.OUTCOME_BLOCK,
            "message": "Too deep.",
        },
        source="POST /api/wf-090/rules",
    )
    with pytest.raises(rules.InvalidRule):
        engine.patch_rule(record["id"], {"outcome": "nope"}, source="PATCH x")


def test_reading_a_missing_rule_is_a_typed_not_found(engine):
    with pytest.raises(rules.RuleNotFound):
        engine.rule("wf090_quote_rule_absent")


def test_deleting_a_rule_removes_it(engine):
    record = engine.create_rule(
        {
            "name": "Deep discount",
            "rule_definition": "SUM([discount]) FROM line_item > 60",
            "outcome": vocab.OUTCOME_BLOCK,
            "message": "Too deep.",
        },
        source="POST /api/wf-090/rules",
    )
    engine.delete_rule(record["id"], source="DELETE /api/wf-090/rules/x")
    with pytest.raises(rules.RuleNotFound):
        engine.rule(record["id"])


def test_validate_never_raises_and_answers_both_ways(engine: QuoteGuardrailEngine):
    good = engine.validate('SOLD_TOGETHER FROM line_item WHERE [hs_product_id] IN ("A")')
    assert good["valid"] is True
    bad = engine.validate("SUM([a] * [b]) FROM line_item > 1")
    assert bad["valid"] is False
    assert bad["error"] == vocab.REASON_ARITHMETIC_UNSUPPORTED


def test_evaluation_writes_nothing(engine: QuoteGuardrailEngine):
    engine.create_rule(
        {
            "name": "Deep discount",
            "rule_definition": "SUM([discount]) FROM line_item > 60",
            "outcome": vocab.OUTCOME_BLOCK,
            "message": "Too deep.",
        },
        source="POST /api/wf-090/rules",
    )
    quote = _make_quote(engine, items=[{"discount": 40}, {"discount": 30}])
    outcome = engine.evaluate_quote(quote["id"])
    assert outcome["blocked"] is True
    assert engine.evaluations(quote["id"]) == []


def test_publishing_a_blocked_quote_writes_an_attempt_and_refuses(engine):
    engine.create_rule(
        {
            "name": "Deep discount",
            "rule_definition": "SUM([discount]) FROM line_item > 60",
            "outcome": vocab.OUTCOME_BLOCK,
            "message": "Reduce the discount.",
        },
        source="POST /api/wf-090/rules",
    )
    quote = _make_quote(engine, items=[{"discount": 40}, {"discount": 30}])
    with pytest.raises(rules.PublishBlocked) as caught:
        engine.publish(quote["id"], source="POST /api/wf-090/quotes/x/publish")
    assert "Reduce the discount." in caught.value.detail
    attempts = engine.attempts(quote_id=quote["id"])
    assert len(attempts) == 1
    assert attempts[0]["data"]["allowed"] is False
    assert engine.evaluations(quote["id"]), "the publish must record its evaluation"


def test_publishing_a_clean_quote_is_allowed_and_reports_the_warnings(engine):
    engine.create_rule(
        {
            "name": "Large quote",
            "rule_definition": "[quote.hs_quote_amount] > 100000",
            "outcome": vocab.OUTCOME_WARNING,
            "message": "Large.",
        },
        source="POST /api/wf-090/rules",
    )
    quote = _make_quote(engine, hs_quote_amount=150000)
    answer = engine.publish(quote["id"], source="POST /api/wf-090/quotes/x/publish")
    assert answer["allowed"] is True
    assert [one["rule_name"] for one in answer["warnings"]] == ["Large quote"]


def test_a_global_rule_applies_in_every_room_and_a_scoped_one_only_there(engine):
    engine.create_rule(
        {
            "name": "Global",
            "rule_definition": '[quote.region] = "unsupported"',
            "outcome": vocab.OUTCOME_WARNING,
            "message": "m",
        },
        source="POST /api/wf-090/rules",
    )
    engine.create_rule(
        {
            "name": "Room one only",
            "rule_definition": "[quote.hs_quote_amount] > 1",
            "outcome": vocab.OUTCOME_WARNING,
            "message": "m",
        },
        source="POST /api/wf-090/rules",
        room_id="room-1",
    )
    assert {one["data"]["name"] for one in engine.rules_for("room-1")} == {
        "Global",
        "Room one only",
    }
    assert {one["data"]["name"] for one in engine.rules_for("room-2")} == {"Global"}


def test_context_for_reads_the_line_items_and_the_related_scopes(engine):
    quote = engine.store.create(
        vocab.SOURCE_QUOTES,
        {
            "name": "q",
            "deal": {"stage": "negotiation"},
            "company": {"industry": "saas"},
            "recipient_contact": [{"email": "buyer@example.com"}],
        },
        actor="dana",
        source="POST /api/records",
    )
    engine.store.create(
        vocab.LINE_ITEMS,
        {"quote_id": quote["id"], "discount": 12},
        actor="dana",
        source="POST /api/records",
    )
    context = engine.context_for(quote["id"])
    assert context["deal"] == {"stage": "negotiation"}
    assert context["company"]["industry"] == "saas"
    assert context["recipient_contact"][0]["email"] == "buyer@example.com"
    assert context["line_item"][0]["discount"] == 12
    assert context["current_user"] == []


def test_an_unknown_quote_is_a_typed_not_found(engine):
    from dsr.db.audited import RecordNotFound

    with pytest.raises(RecordNotFound):
        engine.quote("wf086_quote_absent")


def test_the_summary_counts_rules_and_attempts(engine):
    engine.create_rule(
        {
            "name": "Block rule",
            "rule_definition": "[quote.hs_quote_amount] > 1",
            "outcome": vocab.OUTCOME_BLOCK,
            "message": "m",
        },
        source="POST /api/wf-090/rules",
    )
    engine.create_rule(
        {
            "name": "Disabled rule",
            "rule_definition": "[quote.hs_quote_amount] > 1",
            "outcome": vocab.OUTCOME_WARNING,
            "message": "m",
            "status": vocab.STATUS_DISABLED,
        },
        source="POST /api/wf-090/rules",
    )
    quote = _make_quote(engine)
    try:
        engine.publish(quote["id"], source="POST /api/wf-090/quotes/x/publish")
    except rules.PublishBlocked:
        pass
    summary = engine.summary()
    assert summary["rules"]["total"] == 2
    assert summary["rules"]["enabled"] == 1
    assert summary["rules"]["disabled"] == 1
    assert summary["rules"]["block_publish"] == 1
    assert summary["publish_attempts"]["blocked"] == 1
