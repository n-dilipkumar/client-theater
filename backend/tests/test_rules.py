"""Tests for WF-013 conditional rule semantics.

These are the tests that matter most in this ticket, because the interesting
behaviours of conditional content are all cases where a plausible-looking
implementation is quietly wrong:

* a condition with no variable must not hide content (S8, fail open);
* an empty value and the string ``"0"`` are real values, not absences (S9);
* an unsupplied variable is not the same as one supplied as empty (D3);
* the Or limit is enforced loudly rather than by dropping a condition (S7/D4);
* an Accept Block is rejected, not quietly ignored (S10).

The module under test is pure, so nothing here touches a database.
"""

from __future__ import annotations

import pytest
from dsr import rules
from dsr.rules import MAX_OR_CONDITIONS, RuleError


def text(variable="region", modifier="is", value="Australia", **extra):
    return {"variable": variable, "category": "text", "modifier": modifier, "value": value, **extra}


def number(variable="seats", modifier="is_more_than", value=10, **extra):
    return {
        "variable": variable,
        "category": "number",
        "modifier": modifier,
        "value": value,
        **extra,
    }


def anycond(variable="discount", modifier="has_any_value", **extra):
    return {"variable": variable, "category": "any", "modifier": modifier, **extra}


def rule(*conditions, join="and"):
    return {"join": join, "conditions": list(conditions)}


def shown(result):
    return result["shown"]


# --------------------------------------------------------------------------- #
# The catalog matches the researched modifier lists exactly
# --------------------------------------------------------------------------- #


def test_text_modifiers_match_the_researched_list():
    assert rules.TEXT_MODIFIERS == (
        "is",
        "is_not",
        "contains",
        "does_not_contain",
        "starts_with",
        "ends_with",
        "includes",
        "does_not_include",
    )


def test_number_modifiers_match_the_researched_list():
    assert rules.NUMBER_MODIFIERS == ("equals", "does_not_equal", "is_more_than", "is_less_than")


def test_any_modifiers_match_the_researched_list():
    assert rules.ANY_MODIFIERS == ("has_no_value", "has_any_value")


def test_or_limit_is_ten():
    assert MAX_OR_CONDITIONS == 10


def test_catalog_publishes_the_vocabulary():
    catalog = rules.catalog()
    assert catalog["categories"]["text"][0] == "is"
    assert catalog["limits"]["max_or_conditions"] == 10
    assert catalog["limits"]["max_and_conditions"] is None
    assert "accept" in catalog["forbidden_block_types"]


# --------------------------------------------------------------------------- #
# S3: every text modifier
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("modifier", "observed", "expected", "want"),
    [
        ("is", "Australia", "Australia", True),
        ("is", "Australia", "New Zealand", False),
        ("is_not", "Australia", "New Zealand", True),
        ("is_not", "Australia", "Australia", False),
        ("contains", "Australia East", "Australia", True),
        ("contains", "New Zealand", "Australia", False),
        ("does_not_contain", "New Zealand", "Australia", True),
        ("does_not_contain", "Australia East", "Australia", False),
        ("starts_with", "Australia East", "Australia", True),
        ("starts_with", "East Australia", "Australia", False),
        ("ends_with", "North Australia", "Australia", True),
        ("ends_with", "Australia East", "Australia", False),
        ("includes", "Australia East", "Australia", True),
        ("includes", "New Zealand", "Australia", False),
        ("does_not_include", "New Zealand", "Australia", True),
        ("does_not_include", "Australia East", "Australia", False),
    ],
)
def test_text_modifiers(modifier, observed, expected, want):
    result = rules.evaluate_rule(
        rule(text(modifier=modifier, value=expected)), {"region": observed}
    )
    assert shown(result) is want
    assert result["conditions"][0]["status"] == ("matched" if want else "unmatched")


# --------------------------------------------------------------------------- #
# S4: every number modifier
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("modifier", "observed", "expected", "want"),
    [
        ("equals", 10, 10, True),
        ("equals", 10, 11, False),
        ("does_not_equal", 10, 11, True),
        ("does_not_equal", 10, 10, False),
        ("is_more_than", 11, 10, True),
        ("is_more_than", 10, 10, False),
        ("is_more_than", 9, 10, False),
        ("is_less_than", 9, 10, True),
        ("is_less_than", 10, 10, False),
        ("is_less_than", 11, 10, False),
    ],
)
def test_number_modifiers(modifier, observed, expected, want):
    result = rules.evaluate_rule(
        rule(number(modifier=modifier, value=expected)), {"seats": observed}
    )
    assert shown(result) is want


def test_number_compares_numerically_not_lexically():
    """ "9" > "10" is true as text and false as a number. It must be the latter."""
    result = rules.evaluate_rule(rule(number(value=10)), {"seats": "9"})
    assert shown(result) is False

    result = rules.evaluate_rule(rule(number(value="9")), {"seats": 10})
    assert shown(result) is True


def test_number_accepts_numeric_strings_from_a_crm():
    result = rules.evaluate_rule(rule(number(value=10)), {"seats": "42"})
    assert shown(result) is True
    assert result["conditions"][0]["observed"] == "42"


def test_non_numeric_value_does_not_match_and_does_not_raise():
    """D5: one bad CRM field must not break an entire page build."""
    result = rules.evaluate_rule(rule(number(value=10)), {"seats": "many"})

    assert shown(result) is False
    assert result["conditions"][0]["status"] == "not_numeric"


# --------------------------------------------------------------------------- #
# S5: the Any category, and the distinction it depends on
# --------------------------------------------------------------------------- #


def test_has_any_value_matches_a_supplied_value():
    result = rules.evaluate_rule(rule(anycond()), {"discount": "10%"})
    assert shown(result) is True


def test_has_no_value_matches_an_empty_field():
    result = rules.evaluate_rule(rule(anycond("discount", "has_no_value")), {"discount": ""})
    assert shown(result) is True


def test_has_any_value_does_not_match_an_empty_field():
    result = rules.evaluate_rule(rule(anycond()), {"discount": ""})
    assert shown(result) is False


def test_has_no_value_matches_a_variable_that_was_never_supplied():
    """D3: absent and empty must be distinguishable, or 'has no value' is meaningless."""
    result = rules.evaluate_rule(rule(anycond("discount", "has_no_value")), {})
    assert shown(result) is True


def test_has_any_value_does_not_match_an_unsupplied_variable():
    result = rules.evaluate_rule(rule(anycond()), {})
    assert shown(result) is False
    assert result["conditions"][0]["status"] == "unmatched"


# --------------------------------------------------------------------------- #
# S9: empty and "0" are valid match values
# --------------------------------------------------------------------------- #


def test_empty_string_is_a_valid_match_value():
    result = rules.evaluate_rule(rule(text(modifier="is", value="")), {"region": ""})
    assert shown(result) is True
    assert result["reason"] == "matched"


def test_string_zero_is_a_valid_match_value():
    result = rules.evaluate_rule(
        rule(text(variable="seats", modifier="is", value="0")), {"seats": "0"}
    )
    assert shown(result) is True
    assert result["reason"] == "matched"


def test_a_numeric_zero_supplied_against_a_text_zero_matches():
    """The value box holds text, so 0 and "0" must not diverge."""
    result = rules.evaluate_rule(
        rule(text(variable="seats", modifier="is", value="0")), {"seats": 0}
    )
    assert shown(result) is True


def test_string_zero_counts_as_having_a_value():
    """ "0" is a value, so 'has any value' must be satisfied by it."""
    result = rules.evaluate_rule(rule(anycond()), {"discount": "0"})
    assert shown(result) is True


def test_string_zero_does_not_satisfy_has_no_value():
    result = rules.evaluate_rule(rule(anycond("discount", "has_no_value")), {"discount": "0"})
    assert shown(result) is False


def test_an_empty_value_does_not_make_a_condition_incomplete():
    """S9: completeness depends only on whether a variable was chosen."""
    assert rules.is_incomplete(text(value="")) is False
    assert rules.is_incomplete(text(value="0")) is False
    assert rules.is_incomplete(text(value=None)) is False


# --------------------------------------------------------------------------- #
# S8: incomplete conditions are ignored and the block still appears
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("variable", [None, "", "   "])
def test_a_condition_with_no_variable_is_incomplete(variable):
    assert rules.is_incomplete(text(variable=variable)) is True


def test_an_incomplete_condition_is_ignored_rather_than_matched():
    result = rules.evaluate_rule(rule(text(variable=None, value="Australia")), {})

    assert result["conditions"][0]["status"] == "incomplete"
    # S8: the block appears, because the only condition was incomplete.
    assert shown(result) is True
    assert result["reason"] == "all_incomplete"


def test_an_incomplete_condition_does_not_defeat_a_complete_one():
    result = rules.evaluate_rule(
        rule(text(variable=None, value="Australia"), text(value="New Zealand")),
        {"region": "New Zealand"},
    )
    assert shown(result) is True


def test_an_incomplete_condition_does_not_satisfy_an_or_rule():
    """S8 + fail-open: an Or rule made only of incomplete conditions shows the block."""
    result = rules.evaluate_rule(
        rule(text(variable=None, value="Australia"), text(variable="", value="NZ"), join="or"),
        {},
    )
    assert shown(result) is True
    assert result["reason"] == "all_incomplete"


def test_a_block_with_no_rule_is_always_shown():
    result = rules.evaluate_block({"title": "Intro"}, {"region": "Australia"})
    assert shown(result) is True
    assert result["reason"] == "no_rule"


def test_a_block_with_an_empty_rule_is_always_shown():
    result = rules.evaluate_block({"rule": {"join": "and", "conditions": []}}, {})
    assert shown(result) is True
    assert result["reason"] == "no_rule"


# --------------------------------------------------------------------------- #
# S6: And / Or
# --------------------------------------------------------------------------- #


def test_and_requires_every_condition_to_match():
    result = rules.evaluate_rule(
        rule(text(value="Australia"), number(value=10)),
        {"region": "Australia", "seats": 5},
    )
    assert shown(result) is False
    assert result["reason"] == "unmatched"


def test_and_shows_when_every_condition_matches():
    result = rules.evaluate_rule(
        rule(text(value="Australia"), number(value=10)),
        {"region": "Australia", "seats": 40},
    )
    assert shown(result) is True


def test_or_needs_only_one_condition_to_match():
    result = rules.evaluate_rule(
        rule(text(value="Australia"), text(variable="tier", value="gold"), join="or"),
        {"region": "New Zealand", "tier": "gold"},
    )
    assert shown(result) is True


def test_or_hides_when_no_condition_matches():
    result = rules.evaluate_rule(
        rule(text(value="Australia"), text(variable="tier", value="gold"), join="or"),
        {"region": "New Zealand", "tier": "silver"},
    )
    assert shown(result) is False


# --------------------------------------------------------------------------- #
# S7: the Or limit
# --------------------------------------------------------------------------- #


def test_ten_or_conditions_are_accepted():
    conditions = [text(variable=f"v{index}", value=str(index)) for index in range(10)]
    result = rules.evaluate_rule(rule(*conditions, join="or"), {"v9": "9"})
    assert shown(result) is True


def test_an_eleventh_or_condition_is_rejected():
    conditions = [text(variable=f"v{index}", value=str(index)) for index in range(11)]
    with pytest.raises(RuleError, match="at most 10 Or conditions"):
        rules.validate_rule(rule(*conditions, join="or"))


def test_and_conditions_are_not_limited():
    conditions = [text(variable=f"v{index}", value=str(index)) for index in range(25)]
    normalised = rules.validate_rule(rule(*conditions, join="and"))
    assert len(normalised["conditions"]) == 25


# --------------------------------------------------------------------------- #
# S10: the Accept Block exception
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "block_type", ["accept", "Accept", "Accept Block", "accept-block", "ACCEPT_BLOCK"]
)
def test_an_accept_block_cannot_carry_a_rule(block_type):
    with pytest.raises(RuleError, match="Accept Block"):
        rules.validate_rule(rule(text()), block={"type": block_type})


@pytest.mark.parametrize("block_type", ["text", "image", "video", "pdf", "pricing"])
def test_every_other_block_type_can_carry_a_rule(block_type):
    assert rules.block_accepts_rules({"type": block_type}) is True
    assert rules.validate_rule(rule(text()), block={"type": block_type})["conditions"]


def test_a_block_with_no_type_can_carry_a_rule():
    assert rules.block_accepts_rules({}) is True
    assert rules.block_accepts_rules(None) is True


def test_an_accept_block_with_a_rule_reached_storage_is_still_shown():
    """Defence in depth: even if a rule slipped past validation, the block shows."""
    result = rules.evaluate_block(
        {"type": "accept", "rule": rule(text(value="Australia"))}, {"region": "Australia"}
    )
    assert shown(result) is True
    assert result["reason"] == "no_rule"


def test_an_empty_rule_on_an_accept_block_is_allowed():
    """Rejection is about carrying conditions, not about the key existing."""
    assert rules.validate_rule({"join": "and", "conditions": []}, block={"type": "accept"})


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #


def test_an_unknown_modifier_is_rejected():
    with pytest.raises(RuleError, match="not a known modifier"):
        rules.validate_rule(rule(text(modifier="sounds_like")))


def test_a_modifier_from_the_wrong_category_is_rejected():
    with pytest.raises(RuleError, match="not a text modifier"):
        rules.validate_rule(rule(text(modifier="is_more_than")))


def test_an_unknown_category_is_rejected():
    condition = {"variable": "region", "category": "colour", "modifier": "is", "value": "red"}
    with pytest.raises(RuleError, match="category"):
        rules.validate_rule(rule(condition))


def test_a_condition_without_a_modifier_is_rejected():
    with pytest.raises(RuleError, match="missing a modifier"):
        rules.validate_rule(rule({"variable": "region", "value": "Australia"}))


def test_an_unknown_joiner_is_rejected():
    with pytest.raises(RuleError, match="join must be"):
        rules.validate_rule(rule(text(), join="xor"))


def test_a_non_list_conditions_field_is_rejected():
    with pytest.raises(RuleError, match="conditions must be a list"):
        rules.validate_rule({"join": "and", "conditions": "region is Australia"})


def test_a_non_object_rule_is_rejected():
    with pytest.raises(RuleError, match="rule must be an object"):
        rules.validate_rule("region is Australia")


def test_the_category_is_inferred_from_the_modifier_when_omitted():
    """A client should not have to repeat the category the modifier implies."""
    normalised = rules.validate_rule(
        {
            "join": "and",
            "conditions": [{"variable": "seats", "modifier": "is_more_than", "value": 10}],
        }
    )
    assert normalised["conditions"][0]["category"] == "number"


def test_a_none_rule_normalises_to_an_empty_rule():
    assert rules.validate_rule(None) == {"join": "and", "conditions": []}


def test_a_modifier_and_category_are_matched_case_insensitively():
    """Regression: category inference must fold case the way validation does.

    Inference used to read the modifier before it was lowercased, so an
    upper-case modifier was rejected as uncategorisable while a lower-case one
    was accepted.
    """
    normalised = rules.validate_rule(
        {
            "join": "AND",
            "conditions": [{"variable": "seats", "modifier": "IS_MORE_THAN", "value": 10}],
        }
    )
    assert normalised["join"] == "and"
    assert normalised["conditions"][0]["modifier"] == "is_more_than"
    assert normalised["conditions"][0]["category"] == "number"


def test_an_upper_case_category_is_accepted():
    normalised = rules.validate_rule(
        {
            "join": "and",
            "conditions": [{"variable": "r", "category": "TEXT", "modifier": "IS", "value": "x"}],
        }
    )
    assert normalised["conditions"][0]["category"] == "text"


def test_join_defaults_to_and():
    assert rules.validate_rule({"conditions": [text()]})["join"] == "and"


def test_unknown_condition_keys_survive_validation():
    """Schema flexibility: a team's own field on a condition is preserved."""
    condition = text()
    condition["team_routing_key"] = "emea"
    normalised = rules.validate_rule(rule(condition))
    assert normalised["conditions"][0]["team_routing_key"] == "emea"


# --------------------------------------------------------------------------- #
# D1: case sensitivity
# --------------------------------------------------------------------------- #


def test_text_comparison_is_case_insensitive_by_default():
    result = rules.evaluate_rule(
        rule(text(modifier="is", value="Australia")), {"region": "AUSTRALIA"}
    )
    assert shown(result) is True


def test_case_sensitive_comparison_can_be_requested_per_condition():
    condition = text(modifier="is", value="Australia", case_sensitive=True)
    assert shown(rules.evaluate_rule(rule(condition), {"region": "AUSTRALIA"})) is False
    assert shown(rules.evaluate_rule(rule(condition), {"region": "Australia"})) is True


# --------------------------------------------------------------------------- #
# The trace is part of the contract
# --------------------------------------------------------------------------- #


def test_the_trace_reports_the_observed_value_for_every_condition():
    result = rules.evaluate_rule(
        rule(text(value="Australia"), number(value=10)), {"region": "New Zealand", "seats": 40}
    )
    trace = {entry["variable"]: entry for entry in result["conditions"]}
    assert trace["region"]["observed"] == "New Zealand"
    assert trace["region"]["status"] == "unmatched"
    assert trace["seats"]["status"] == "matched"


def test_the_result_includes_the_normalised_rule():
    result = rules.evaluate_rule(rule(text()), {})
    assert result["rule"]["join"] == "and"
    assert result["rule"]["conditions"][0]["category"] == "text"


# --------------------------------------------------------------------------- #
# personalise_blocks
# --------------------------------------------------------------------------- #


def test_personalise_splits_blocks_into_shown_and_hidden():
    blocks = [
        {"id": "b1", "data": {"title": "AU pricing", "rule": rule(text(value="Australia"))}},
        {"id": "b2", "data": {"title": "Intro", "rule": None}},
        {"id": "b3", "data": {"title": "NZ pricing", "rule": rule(text(value="New Zealand"))}},
    ]
    result = rules.personalise_blocks(blocks, {"region": "Australia"})

    assert result["shown"] == ["b1", "b2"]
    assert result["hidden"] == ["b3"]
    assert [entry["title"] for entry in result["blocks"]] == ["AU pricing", "Intro", "NZ pricing"]


def test_personalise_keeps_every_block_in_order_regardless_of_visibility():
    blocks = [
        {"id": "b1", "data": {"title": "hidden", "rule": rule(text(value="Australia"))}},
        {"id": "b2", "data": {"title": "shown", "rule": None}},
    ]
    result = rules.personalise_blocks(blocks, {"region": "New Zealand"})
    assert [entry["block_id"] for entry in result["blocks"]] == ["b1", "b2"]


def test_personalise_with_no_variables_shows_every_block_with_a_value_rule():
    """No variable is ever supplied, so every value condition fails to match."""
    blocks = [{"id": "b1", "data": {"rule": rule(text(value="Australia"))}}]
    result = rules.personalise_blocks(blocks, {})
    assert result["shown"] == []
    assert result["hidden"] == ["b1"]


def test_personalise_shows_a_block_whose_conditions_are_all_incomplete():
    blocks = [{"id": "b1", "data": {"rule": rule(text(variable=None))}}]
    result = rules.personalise_blocks(blocks, {"region": "Australia"})
    assert result["shown"] == ["b1"]
    assert result["blocks"][0]["reason"] == "all_incomplete"


def test_personalise_of_no_blocks_is_empty_not_an_error():
    result = rules.personalise_blocks([], {"region": "Australia"})
    assert result["blocks"] == []
    assert result["block_count"] == 0
    assert result["shown"] == [] and result["hidden"] == []


def test_personalise_reports_how_many_blocks_it_decided():
    blocks = [
        {"id": "b1", "data": {"rule": rule(text(value="Australia"))}},
        {"id": "b2", "data": {"rule": None}},
    ]
    assert rules.personalise_blocks(blocks, {"region": "Australia"})["block_count"] == 2


def test_personalise_echoes_the_variables_it_used():
    result = rules.personalise_blocks([], {"region": "Australia"})
    assert result["variables"] == {"region": "Australia"}
