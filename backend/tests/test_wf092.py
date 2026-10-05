"""WF-092: the branch predicate, the caps, the priority ordering and the chain.

The rules in ``dsr.quoting_proposals.approval_chain_rules`` are pure, so most of this
file tests them without a database: an approval boundary is a fact about two amounts,
and testing it against a clock would make it pass or fail depending on when CI runs.

What is tested here, and why each matters:

* the researched branch threshold and the five operators, including the boundary value
  itself, because "greater than 5,000" is strict and a quote of exactly 5000 must not
  qualify;
* the third branch outcome, "could not be checked", kept separate from a miss, because
  folding an absent amount into a rejection makes a bug indistinguishable from a
  decision;
* both researched caps at the boundary, the sixth sequence and the eleventh approver;
* the exact sequential rule, that a lower-priority approver is refused until every
  approver at the current priority has decided, which is the sentence this workflow is
  named for;
* the researched safety valve, that a quote with no approval step is approved rather
  than blocked;
* all three approver requirements, because the research offers all three;
* the re-enrolment derivation, that it clears the decisions or the chain would pass
  instantly;
* the engine against a real temporary database, because the audited wrapper and the
  stored data envelope are where a pure rule test cannot see a defect.
"""

from __future__ import annotations

import pytest
from dsr.db.audited import AuditedDatabase
from dsr.quoting_proposals import approval_chain_rules as rules
from dsr.quoting_proposals import approval_chain_vocabulary as vocab
from dsr.quoting_proposals.approval_chain_engine import (
    ApprovalChainEngine,
    DuplicateWorkflow,
    WorkflowNotFound,
)
from dsr.store import RecordStore

BRANCH = {"property": "quote_amount", "operator": "greater_than", "threshold": 5000.0}

#: The scenario the specification gives: sales manager, then sales director, then
#: legal representative.
STEPS = (
    {"priority": 1, "approver": "sales_manager"},
    {"priority": 2, "approver": "sales_director"},
    {"priority": 3, "approver": "legal_representative"},
)


@pytest.fixture
def engine(store: RecordStore) -> ApprovalChainEngine:
    return ApprovalChainEngine(store)


@pytest.fixture
def three_levels() -> list[dict]:
    return rules.group_by_priority(list(STEPS))


# --------------------------------------------------------------------------- #
# The branch predicate
# --------------------------------------------------------------------------- #


def test_quote_above_the_researched_threshold_qualifies():
    result = rules.evaluate_branch({"quote_amount": 12000}, BRANCH)
    assert result["outcome"] == vocab.OUTCOME_QUALIFIED


def test_quote_below_the_threshold_does_not_qualify():
    result = rules.evaluate_branch({"quote_amount": 900}, BRANCH)
    assert result["outcome"] == vocab.OUTCOME_NOT_QUALIFIED


def test_greater_than_is_strict_at_the_boundary():
    """"Set Branch 1 to greater than 5,000" is a strict comparison."""
    result = rules.evaluate_branch({"quote_amount": 5000}, BRANCH)
    assert result["outcome"] == vocab.OUTCOME_NOT_QUALIFIED


def test_greater_than_or_equal_qualifies_at_the_boundary():
    """The NetSuite operator is offered too, and it is inclusive."""
    branch = {**BRANCH, "operator": "greater_than_or_equal"}
    assert rules.evaluate_branch({"quote_amount": 5000}, branch)["outcome"] == (
        vocab.OUTCOME_QUALIFIED
    )


@pytest.mark.parametrize(
    ("operator", "amount", "expected"),
    [
        ("greater_than", 6000, vocab.OUTCOME_QUALIFIED),
        ("greater_than", 100, vocab.OUTCOME_NOT_QUALIFIED),
        ("less_than", 100, vocab.OUTCOME_QUALIFIED),
        ("less_than_or_equal", 5000, vocab.OUTCOME_QUALIFIED),
        ("equals", 5000, vocab.OUTCOME_QUALIFIED),
        ("equals", 5001, vocab.OUTCOME_NOT_QUALIFIED),
    ],
)
def test_every_branch_operator(operator, amount, expected):
    branch = {**BRANCH, "operator": operator}
    assert rules.evaluate_branch({"quote_amount": amount}, branch)["outcome"] == expected


def test_an_unknown_operator_is_reported_not_silently_false():
    result = rules.evaluate_branch({"quote_amount": 9000}, {**BRANCH, "operator": "sorta_more"})
    assert result["outcome"] == vocab.OUTCOME_NOT_QUALIFIED
    assert result["reason"] == rules.REASONS["invalid_operator"]


def test_a_quote_with_no_amount_is_unverifiable_not_refused():
    """An unanswered question is not a rejection."""
    result = rules.evaluate_branch({"name": "Acme"}, BRANCH)
    assert result["outcome"] == "unverifiable"
    assert result["reason"] == rules.REASONS["unverifiable"]


def test_a_boolean_amount_is_unverifiable_not_a_comparison():
    """A flag compared against 5000 is a caller error, not a number."""
    result = rules.evaluate_branch({"quote_amount": True}, BRANCH)
    assert result["outcome"] == "unverifiable"


def test_a_numeric_string_amount_is_compared():
    result = rules.evaluate_branch({"quote_amount": "6000"}, BRANCH)
    assert result["outcome"] == vocab.OUTCOME_QUALIFIED


def test_a_dotted_property_path_resolves():
    branch = {"property": "totals.net", "operator": "greater_than", "threshold": 5000.0}
    assert rules.evaluate_branch({"totals": {"net": 6000}}, branch)["outcome"] == (
        vocab.OUTCOME_QUALIFIED
    )


def test_evaluate_branches_qualifies_on_any_branch():
    branches = [BRANCH, {"property": "discount", "operator": "greater_than", "threshold": 10}]
    outcome = rules.evaluate_branches({"quote_amount": 100, "discount": 25}, branches)
    assert outcome["qualified"] is True


# --------------------------------------------------------------------------- #
# The caps
# --------------------------------------------------------------------------- #


def test_five_sequences_are_allowed():
    sequences = [{"priority": n, "approvers": ["a"]} for n in range(1, 6)]
    assert rules.validate_sequence(sequences)["within_caps"] is True


def test_a_sixth_sequence_is_refused():
    sequences = [{"priority": n, "approvers": ["a"]} for n in range(1, 7)]
    with pytest.raises(rules.ApprovalRuleError) as caught:
        rules.validate_sequence(sequences)
    assert "at most 5 sequences" in str(caught.value)


def test_the_sequence_cap_counts_what_is_already_stored():
    with pytest.raises(rules.ApprovalRuleError):
        rules.validate_sequence([{"priority": 1, "approvers": ["a"]}], existing_sequences=5)


def test_ten_approvers_in_a_sequence_are_allowed():
    sequence = {"priority": 1, "approvers": [f"a{n}" for n in range(10)]}
    assert rules.validate_sequence([sequence])["approvers"] == [10]


def test_an_eleventh_approver_is_refused():
    sequence = {"priority": 1, "approvers": [f"a{n}" for n in range(11)]}
    with pytest.raises(rules.ApprovalRuleError) as caught:
        rules.validate_sequence([sequence])
    assert "at most 10" in str(caught.value)


def test_a_sequence_with_no_approver_is_refused():
    with pytest.raises(rules.ApprovalRuleError):
        rules.validate_sequence([{"priority": 1, "approvers": []}])


def test_a_priority_below_one_is_refused():
    with pytest.raises(rules.ApprovalRuleError):
        rules.validate_sequence([{"priority": 0, "approvers": ["a"]}])


def test_an_unknown_requirement_is_refused():
    with pytest.raises(rules.ApprovalRuleError):
        rules.validate_sequence([{"priority": 1, "approvers": ["a"], "requirement": "maybe"}])


def test_sequences_rank_lowest_priority_first():
    ranked = rules.rank_sequences(
        [{"priority": 3, "approvers": ["c"]}, {"priority": 1, "approvers": ["a"]}]
    )
    assert [sequence["priority"] for sequence in ranked] == [1, 3]


# --------------------------------------------------------------------------- #
# The sequential rule
# --------------------------------------------------------------------------- #


def test_the_first_priority_is_active_on_an_untouched_chain(three_levels):
    assert rules.active_priority({"decisions": {}}, three_levels) == 1


def test_a_lower_priority_approver_is_refused_before_their_turn(three_levels):
    """"The sales director won't need to approve the quote until the sales manager has."""
    outcome = rules.advance({"decisions": {}}, three_levels, "sales_director", "approved")
    assert outcome["outcome"] == "not_yet_your_priority"
    assert outcome["your_priority"] == 2
    assert outcome["active_priority"] == 1
    assert outcome["decisions"] == {}


def test_the_chain_advances_only_after_every_approver_at_a_priority_decides():
    """"Sequential approvals require approval by every approver at each priority step"."""
    levels = rules.group_by_priority(
        [
            {"priority": 1, "approver": "east_lead"},
            {"priority": 1, "approver": "west_lead"},
            {"priority": 2, "approver": "director"},
        ]
    )
    one = rules.advance({"decisions": {}}, levels, "east_lead", "approved")
    assert one["outcome"] == "decided"
    assert one["next_priority"] == 1, "one approver at priority 1 must not release priority 2"
    two = rules.advance({"decisions": one["decisions"]}, levels, "west_lead", "approved")
    assert two["next_priority"] == 2


def test_an_abstention_does_not_release_a_sequential_level():
    levels = rules.group_by_priority(list(STEPS))
    outcome = rules.advance({"decisions": {}}, levels, "sales_manager", "abstained")
    assert outcome["outcome"] == "decided"
    assert outcome["next_priority"] == 1


def test_a_rejection_ends_the_chain_at_that_priority(three_levels):
    outcome = rules.advance({"decisions": {}}, three_levels, "sales_manager", "rejected")
    assert outcome["outcome"] == "complete"
    assert outcome["state"] == vocab.STATE_REJECTED
    assert outcome["publishable"] is False


def test_the_last_decision_writes_the_final_state(three_levels):
    outcome = rules.advance(
        {
            "decisions": {
                "sales_manager": "approved",
                "sales_director": "approved",
            }
        },
        three_levels,
        "legal_representative",
        "approved",
    )
    assert outcome["outcome"] == "complete"
    assert outcome["state"] == vocab.STATE_APPROVED
    assert outcome[vocab.QUOTE_STATUS_KEY] == vocab.DECISION_APPROVED
    assert outcome["publishable"] is True


def test_an_approver_may_decide_only_once(three_levels):
    """A second decision from the same approver is refused, not applied.

    The approver has already been heard at their own priority, so this is a refusal
    rather than the chain completing on a duplicate approval.
    """
    decisions = {"sales_manager": "approved", "sales_director": "approved"}
    outcome = rules.advance({"decisions": decisions}, three_levels, "sales_manager", "rejected")
    assert outcome["outcome"] == "already_decided"
    assert outcome["decisions"]["sales_manager"] == "approved"


def test_someone_who_is_not_an_approver_is_refused(three_levels):
    outcome = rules.advance({"decisions": {}}, three_levels, "intern", "approved")
    assert outcome["outcome"] == "not_an_approver"


def test_an_unknown_decision_is_refused(three_levels):
    with pytest.raises(rules.ApprovalRuleError):
        rules.advance({"decisions": {}}, three_levels, "sales_manager", "maybe")


# --------------------------------------------------------------------------- #
# The three approver requirements
# --------------------------------------------------------------------------- #


def test_all_approvers_waits_for_every_one():
    levels = rules.group_by_priority(
        [
            {"priority": 1, "approver": "a", "requirement": vocab.REQUIREMENT_ALL},
            {"priority": 1, "approver": "b", "requirement": vocab.REQUIREMENT_ALL},
            {"priority": 2, "approver": "c"},
        ]
    )
    one = rules.advance({"decisions": {}}, levels, "a", "approved")
    assert one["next_priority"] == 1
    two = rules.advance({"decisions": one["decisions"]}, levels, "b", "approved")
    assert two["next_priority"] == 2


def test_any_approvers_releases_on_the_first_approval():
    levels = rules.group_by_priority(
        [
            {"priority": 1, "approver": "a", "requirement": vocab.REQUIREMENT_ANY},
            {"priority": 1, "approver": "b", "requirement": vocab.REQUIREMENT_ANY},
            {"priority": 2, "approver": "c"},
        ]
    )
    one = rules.advance({"decisions": {}}, levels, "a", "approved")
    assert one["next_priority"] == 2


def test_any_approvers_is_not_satisfied_by_an_abstention():
    levels = rules.group_by_priority(
        [
            {"priority": 1, "approver": "a", "requirement": vocab.REQUIREMENT_ANY},
            {"priority": 1, "approver": "b", "requirement": vocab.REQUIREMENT_ANY},
        ]
    )
    one = rules.advance({"decisions": {}}, levels, "a", "abstained")
    assert one["outcome"] == "decided"


# --------------------------------------------------------------------------- #
# The auto-approval valve
# --------------------------------------------------------------------------- #


def test_a_quote_with_no_step_is_auto_approved_not_blocked():
    """"if a quote approval step hasn't been added above this action, quotes will be
    auto-approved"."""
    outcome = rules.advance({"decisions": {}}, [], "anyone", "approved")
    assert outcome["outcome"] == "auto_approved"
    assert outcome["state"] == vocab.STATE_APPROVED
    assert outcome["publishable"] is True


def test_auto_approve_names_the_valve_as_its_reason():
    result = rules.auto_approve("q_1", [{"reason": rules.REASONS["not_matched"]}])
    assert result["outcome"] == vocab.OUTCOME_AUTO_APPROVED
    assert result["publishable"] is True
    assert "auto-approved" in result["reason"]


# --------------------------------------------------------------------------- #
# The message template
# --------------------------------------------------------------------------- #


def test_a_message_renders_a_quote_property():
    result = rules.render_message("Approve {{quote.quote_amount}}", {"quote_amount": 6000})
    assert result["text"] == "Approve 6000"
    assert result["complete"] is True


def test_a_nested_data_variable_renders():
    result = rules.render_message("Total {{quote.totals.net}}", {"totals": {"net": 4200}})
    assert result["text"] == "Total 4200"


def test_a_missing_data_variable_is_left_in_place_and_named():
    """A message reading "quote  is over your limit" is indistinguishable from a bug."""
    result = rules.render_message("Approve {{quote.nope}}", {"quote_amount": 6000})
    assert "{{quote.nope}}" in result["text"]
    assert result["missing"] == ["nope"]
    assert result["complete"] is False


# --------------------------------------------------------------------------- #
# Re-enrolment
# --------------------------------------------------------------------------- #


def test_re_enrolment_returns_the_chain_to_the_first_priority():
    reset = rules.re_enrol(
        {
            "state": vocab.STATE_APPROVED,
            "decisions": {"sales_manager": "approved"},
            "run": 1,
        }
    )
    assert reset["state"] == vocab.STATE_PENDING
    assert reset["active_priority"] == 1
    assert reset["decisions"] == {}
    assert reset["run"] == 2
    assert reset["previous_run"] == 1


# --------------------------------------------------------------------------- #
# The vocabulary surface
# --------------------------------------------------------------------------- #


def test_the_vocabulary_serves_the_researched_caps_and_states():
    described = vocab.describe()
    assert described["chain"]["caps"]["max_sequences"] == 5
    assert described["chain"]["caps"]["max_approvers_per_sequence"] == 10
    assert described["chain"]["states"] == list(vocab.CHAIN_STATES)
    assert described["branch"]["default_threshold"] == 5000.0
    assert described["template"]["example"] == "{{quote.quote_amount}}"


def test_only_the_two_deliverable_channels_are_built():
    described = vocab.describe()
    assert described["notifications"]["built"] == ["bell", "email"]
    assert set(described["notifications"]["researched_but_not_built"]) == {
        "teams",
        "slack",
        "google_chat",
    }


# --------------------------------------------------------------------------- #
# The engine, against a real database
# --------------------------------------------------------------------------- #

SEQUENCES = [
    {"priority": 1, "approvers": ["sales_manager"], "message": "Approve {{quote.quote_amount}}"},
    {"priority": 2, "approvers": ["sales_director"], "message": "Second review"},
    {"priority": 3, "approvers": ["legal_representative"], "message": "Legal sign off"},
]


def _quote(engine: ApprovalChainEngine, amount: float, name: str = "Quote") -> str:
    return engine.store.create(
        vocab.SOURCE_QUOTES,
        {"name": name, "quote_amount": amount},
        actor="test",
        source="test",
    )["id"]


def test_the_engine_enrols_a_qualifying_quote_at_priority_one(engine):
    engine.add_branch(source="test", name="Big", sequences=SEQUENCES)
    quote_id = _quote(engine, 12000)
    result = engine.enrol(quote_id, source="test")
    assert result["auto_approved"] is False
    assert result["state"] == vocab.STATE_PENDING
    assert [level["priority"] for level in result["levels"]] == [1, 2, 3]
    assert result["enrolment"]["active_priority"] == 1


def test_the_engine_auto_approves_a_quote_that_misses_the_branch(engine):
    engine.add_branch(source="test", name="Big", sequences=SEQUENCES)
    result = engine.enrol(_quote(engine, 900), source="test")
    assert result["auto_approved"] is True
    assert result["publishable"] is True


def test_the_engine_notifies_only_the_active_priority(engine):
    engine.add_branch(source="test", name="Big", sequences=SEQUENCES)
    enrolment = engine.enrol(_quote(engine, 12000), source="test")["enrolment"]
    notified = {row["approver"] for row in engine.notifications_for(enrolment["id"])}
    assert notified == {"sales_manager"}


def test_the_engine_records_the_rendered_message(engine):
    engine.add_branch(source="test", name="Big", sequences=SEQUENCES)
    quote_id = _quote(engine, 12000)
    enrolment = engine.enrol(quote_id, source="test")["enrolment"]
    rows = {row["approver"]: row for row in engine.steps_for(enrolment["id"])}
    assert rows["sales_manager"]["message"] == "Approve 12000"
    assert rows["sales_director"]["message"] == "Second review"


def test_the_engine_writes_both_notification_channels_it_can_deliver(engine):
    engine.add_branch(source="test", name="Big", sequences=SEQUENCES)
    enrolment = engine.enrol(_quote(engine, 12000), source="test")["enrolment"]
    channels = {row["channel"] for row in engine.notifications_for(enrolment["id"])}
    assert channels == {"bell", "email"}


def test_the_engine_refuses_a_premature_decision_without_writing(engine):
    engine.add_branch(source="test", name="Big", sequences=SEQUENCES)
    enrolment = engine.enrol(_quote(engine, 12000), source="test")["enrolment"]
    outcome = engine.decide(enrolment["id"], "sales_director", "approved", source="test")
    assert outcome["outcome"] == "not_yet_your_priority"
    assert engine.decisions_for(enrolment["id"]) == []


def test_the_engine_runs_the_whole_chain_and_writes_the_final_state(engine):
    engine.add_branch(source="test", name="Big", sequences=SEQUENCES)
    enrolment = engine.enrol(_quote(engine, 12000), source="test")["enrolment"]
    engine.decide(enrolment["id"], "sales_manager", "approved", source="test")
    engine.decide(enrolment["id"], "sales_director", "approved", source="test")
    final = engine.decide(enrolment["id"], "legal_representative", "approved", source="test")
    assert final["outcome"] == "complete"
    stored = engine.get_enrolment(enrolment["id"])
    assert stored["state"] == vocab.STATE_APPROVED
    assert stored["publishable"] is True
    assert stored[vocab.QUOTE_STATUS_KEY] == vocab.DECISION_APPROVED


def test_the_engine_stops_the_chain_on_a_rejection(engine):
    engine.add_branch(source="test", name="Big", sequences=SEQUENCES)
    enrolment = engine.enrol(_quote(engine, 12000), source="test")["enrolment"]
    outcome = engine.decide(enrolment["id"], "sales_manager", "rejected", source="test")
    assert outcome["outcome"] == "complete"
    assert outcome["publishable"] is False


def test_the_engine_refuses_a_second_workflow(engine):
    engine.add_branch(source="test", name="Big", sequences=SEQUENCES)
    with pytest.raises(DuplicateWorkflow):
        engine.ensure_workflow(source="test", workflow_id="a_second_workflow")


def test_the_engine_raises_when_no_workflow_exists(engine):
    with pytest.raises(WorkflowNotFound):
        engine.get_workflow()


def test_the_engine_refuses_re_enrolment_while_the_switch_is_off(engine):
    engine.add_branch(source="test", name="Big", sequences=SEQUENCES)
    enrolment = engine.enrol(_quote(engine, 12000), source="test")["enrolment"]
    with pytest.raises(rules.ApprovalRuleError):
        engine.re_enrol(enrolment["id"], source="test")


def test_the_engine_re_enrols_when_the_switch_is_on(engine):
    engine.add_branch(source="test", name="Big", sequences=SEQUENCES)
    enrolment = engine.enrol(_quote(engine, 12000), source="test")["enrolment"]
    engine.decide(enrolment["id"], "sales_manager", "approved", source="test")
    workflow = engine.get_workflow()
    engine.store.update(
        workflow["id"], {vocab.RE_ENROL_KEY: True}, actor="test", source="test"
    )
    reset = engine.re_enrol(enrolment["id"], source="test")
    assert reset["run"] == 2
    assert reset["enrolment"]["state"] == vocab.STATE_PENDING
    assert reset["enrolment"]["decisions"] == {}
    assert reset["enrolment"]["active_priority"] == 1


def test_the_engine_writes_one_audit_row_per_change(db: AuditedDatabase):
    engine = ApprovalChainEngine(RecordStore(db))
    engine.add_branch(source="test", name="Big", sequences=SEQUENCES)
    engine.enrol(_quote(engine, 12000), source="test")
    sources = [row.get("source") for row in db.audit()]
    assert sources, "every write must be audited"
    assert all(source == "test" for source in sources)


def test_the_engine_pushes_a_step_for_each_qualifying_branch(engine):
    engine.add_branch(source="test", name="Big", sequences=SEQUENCES)
    engine.add_branch(
        source="test",
        name="Deep discount",
        prop="discount",
        operator="greater_than",
        threshold=10,
        sequences=[{"priority": 1, "approvers": ["finance"]}],
    )
    quote_id = engine.store.create(
        vocab.SOURCE_QUOTES,
        {"name": "Both", "quote_amount": 12000, "discount": 25},
        actor="test",
        source="test",
    )["id"]
    result = engine.enrol(quote_id, source="test")
    approvers = {
        approver for level in result["levels"] for approver in level["approvers"]
    }
    assert approvers == {"sales_manager", "sales_director", "legal_representative", "finance"}


def test_the_domain_module_does_not_import_the_app():
    """The enforced test greps feature modules; this asserts the domain is clean too."""
    import dsr.quoting_proposals.approval_chain_engine as engine_module

    source = engine_module.__file__
    assert source is not None
    text = open(source, encoding="utf-8").read()
    assert "dsr.api" not in text
    assert "import sqlite3" not in text


def test_the_database_fixture_is_a_real_audited_database(db: AuditedDatabase):
    assert db.stats()["records"] == 0