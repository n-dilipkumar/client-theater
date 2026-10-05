"""WF-091: route a discounted quote for standard approval, and what it refuses.

The researched specification is ``docs/research/digital-sales-room-workflows/wf/WF-091.md``,
quoted in full in issue 179. These tests are organised by the researched rule each one
defends, because the point of this workflow is that the rules were sourced rather than
chosen, so a rule with no test is a rule the next person to touch it will quietly drop.

The sections, and the sourced or derived rule each one pins:

``filters combine with AND``
    "*And these conditions are met*". Every filter in a rule must hold, and the
    per-filter report is what the flow's "**View approval conditions**" shows.
``a filter reads any property``
    "**[Object] properties** -> search and pick a property (e.g. quote amount,
    discount level, SKU)". A dotted JSON path, so a team adds a property with no
    migration, and a path the record does not carry does not match.
``a line item filter matches any line``
    "Require approval on quotes where a specific line item is above a certain
    discount amount". One discounted line is enough.
``ten approvers``
    "Assign up to 10 approvers to review quotes that match your configured filters."
``all or at least one``
    "All approvers required or At least one approver required". The requirement is a
    count, not a position.
``the creator is removed from the approver list``
    Two sourced sentences that do not agree. The reading this build implements was
    chosen by Jev in audit jev-20261005T064607-13024-67413.
``re-submission clears every decision``
    "every approver will need to approve the quote again when the quote is
    re-submitted".
``only an approved quote may be shared``
    "only on approval can the quote be **Share**d (state ``Shared``) and sent to the
    buyer".
``three statuses release a lock``
    "you must first update the ``hs_status`` of the quote back to ``DRAFT``,
    ``PENDING_APPROVAL``, or ``REJECTED``".
``the activity log``
    "Quote approval requested", "Quote approval rejected / requested changes",
    "Quote approved", and the two this build added.
``the domain imports nothing but the store``
    The architectural guard, checked with an AST walk.
``the seed return string``
    Every character encodable by cp1252, and the states the seeder claims really exist.

The HTTP surface is in ``test_wf091_http.py``.
"""

from __future__ import annotations

import ast
import importlib
from datetime import datetime, timezone
from pathlib import Path

import pytest
from dsr.quoting_proposals import (
    quote_approval_inferences as inf,
    quote_approval_rules as rules,
    quote_approval_vocabulary as vocab,
)
from dsr.quoting_proposals.quote_approval_engine import QuoteApprovalEngine
from dsr.quoting_proposals.quote_approval_rules import ApprovalRefusal
from dsr.store import RecordStore

# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

NOW = datetime(2026, 10, 5, 9, 0, 0, tzinfo=timezone.utc)

FEATURE_MODULE = "dsr.features.WF-091_route_a_discounted_quote_for_standard_approval"
DOMAIN_PACKAGE = "dsr.quoting_proposals"


@pytest.fixture()
def engine(store: RecordStore) -> QuoteApprovalEngine:
    """An engine over an empty store and a clock the test controls."""
    store.create(
        "room", {"name": "Northwind data room"}, record_id="room_a", actor="dana", source="fixture"
    )
    return QuoteApprovalEngine(store, now=lambda: NOW)


def quote(engine: QuoteApprovalEngine, quote_id: str, **data) -> dict:
    """One quote as WF-086 writes it: arbitrary JSON, with a creator."""
    payload = {
        "name": quote_id,
        "hs_status": vocab.STATE_DRAFT,
        "creator": "dana",
        **data,
    }
    return engine.store.create(
        vocab.SOURCE_QUOTES,
        payload,
        record_id=quote_id,
        room_id="room_a",
        actor="dana",
        source="fixture",
    )


def line(engine: QuoteApprovalEngine, quote_id: str, **data) -> dict:
    return engine.store.create(
        "wf086_line_item",
        {"quote_id": quote_id, "sku": "SEAT-STD", **data},
        room_id="room_a",
        actor="dana",
        source="fixture",
    )


def discount_rule(engine: QuoteApprovalEngine, **overrides) -> dict:
    """A rule on a line item discounted above 25 percent, three approvers, all required."""
    payload = {
        "key": "deep-line-discount",
        "label": "Deep discount on any line item",
        "filters": [{"object": "line_item", "property": "discount", "operator": "gt", "value": 25}],
        "approvers": ["dana", "sam", "priya"],
        "requirement": vocab.REQUIREMENT_ALL,
        "approval_note": "Check the margin.",
    }
    payload.update(overrides)
    return engine.create_rule(payload, actor="dana", source="fixture", room_id="room_a")


def domain_paths() -> list[Path]:
    package = Path(importlib.import_module(DOMAIN_PACKAGE).__file__).parent
    return sorted(package.glob("quote_approval_*.py"))


def feature_path() -> Path:
    return Path(importlib.import_module(FEATURE_MODULE).__file__)


# --------------------------------------------------------------------------- #
# filters combine with AND
# --------------------------------------------------------------------------- #


class TestFiltersCombineWithAnd:
    def test_every_filter_must_hold(self):
        """The word in the source is and, so a rule is a conjunction."""
        filters = [
            {"object": "quote", "property": "amount", "operator": "gt", "value": 1000},
            {"object": "quote", "property": "tier", "operator": "is", "value": "gold"},
        ]
        one_short = rules.matches_filters(filters, {"amount": 5000, "tier": "silver"})

        assert one_short["matched"] is False
        assert one_short["matched_count"] == 1
        assert one_short["total"] == 2

    def test_all_matching_is_a_match(self):
        filters = [
            {"object": "quote", "property": "amount", "operator": "gt", "value": 1000},
            {"object": "quote", "property": "tier", "operator": "is", "value": "gold"},
        ]
        assert rules.matches_filters(filters, {"amount": 5000, "tier": "gold"})["matched"] is True

    def test_the_default_mode_is_all(self):
        assert vocab.FILTER_MATCH_ALL == "all"

    def test_the_report_names_what_each_filter_read(self):
        """The flow's "**View approval conditions**" has to say why, not only that."""
        report = rules.matches_filters(
            [{"object": "quote", "property": "amount", "operator": "gt", "value": 1000}],
            {"amount": 5000},
        )
        one = report["filters"][0]

        assert one["matched"] is True
        assert one["expected"] == 1000
        assert one["actual"] == 5000
        assert one["present"] is True

    def test_a_rule_with_no_filters_matches_nothing(self):
        """A rule that matched everything would approve every quote. It is refused at save."""
        assert rules.matches_filters([], {"amount": 5000})["matched"] is False

    def test_a_rule_with_no_filter_is_refused(self):
        with pytest.raises(ApprovalRefusal) as caught:
            rules.validate_rule({"key": "empty", "approvers": ["sam"]})

        assert caught.value.code == "rule_needs_a_filter"


# --------------------------------------------------------------------------- #
# a filter reads any property
# --------------------------------------------------------------------------- #


class TestAFilterReadsAnyProperty:
    def test_a_team_defined_property_is_filtered_without_a_migration(self):
        """No schema declares this path. The store holds arbitrary JSON and so must a rule."""
        filters = [
            {
                "object": "quote",
                "property": "commercial.discount_band",
                "operator": "is",
                "value": "gold",
            }
        ]
        payload = {"commercial": {"discount_band": "gold"}}

        assert rules.matches_filters(filters, payload)["matched"] is True

    def test_a_path_the_record_does_not_carry_does_not_match(self):
        filters = [{"object": "quote", "property": "not.here", "operator": "is", "value": "x"}]

        report = rules.matches_filters(filters, {"amount": 1})

        assert report["matched"] is False
        assert report["filters"][0]["present"] is False

    def test_an_equality_filter_on_a_missing_property_does_not_match(self):
        """A quote that does not carry the property is not a quote where it equals x."""
        filters = [{"object": "quote", "property": "tier", "operator": "is", "value": "gold"}]

        assert rules.matches_filters(filters, {})["matched"] is False

    def test_a_dotted_path_reaches_into_a_list_by_index(self):
        assert rules.read_path({"lines": [{"sku": "SEAT-STD"}]}, "lines.0.sku") == "SEAT-STD"

    def test_every_operator_is_checked_against_the_same_pair(self):
        """One pair chosen so every operator has a distinct answer, so a swapped one fails."""
        quote_side = {"value": "gold", "count": 10}
        cases = {
            "is": ("gold", True),
            "is_not": ("gold", False),
            "gt": (5, True),
            "gte": (10, True),
            "lt": (5, False),
            "lte": (10, True),
            "contains": ("ol", True),
            "does_not_contain": ("zz", True),
            "in": (["gold", "silver"], True),
            "not_in": (["silver"], True),
        }
        for operator, (expected, wanted) in cases.items():
            result = rules.filter_matches(
                {
                    "object": "quote",
                    "property": "value"
                    if operator in ("is", "is_not", "contains", "does_not_contain", "in", "not_in")
                    else "count",
                    "operator": operator,
                    "value": expected,
                },
                quote_side,
            )
            assert result["matched"] is wanted, operator

    def test_an_unknown_operator_is_refused_and_names_the_set(self):
        with pytest.raises(ApprovalRefusal) as caught:
            rules.validate_filter(
                {"object": "quote", "property": "amount", "operator": "roughly", "value": 1}
            )

        assert caught.value.code == "unknown_operator"
        assert "gte" in caught.value.errors["operator"]


# --------------------------------------------------------------------------- #
# a line item filter matches any line
# --------------------------------------------------------------------------- #


class TestALineItemFilterMatchesAnyLine:
    def test_one_deeply_discounted_line_is_enough(self):
        """The sourced sentence is about a specific line item."""
        items = [{"sku": "A", "discount": 5}, {"sku": "B", "discount": 40}]
        result = rules.filter_matches(
            {"object": "line_item", "property": "discount", "operator": "gt", "value": 25},
            {},
            items,
        )

        assert result["matched"] is True
        assert result["actual"] == 40
        assert result["considered"] == 2

    def test_a_quote_with_no_line_items_reports_why(self):
        result = rules.filter_matches(
            {"object": "line_item", "property": "discount", "operator": "gt", "value": 25}, {}, []
        )

        assert result["matched"] is False
        assert "no line item" in result["reason"]

    def test_a_quote_filter_reads_the_quote_not_the_lines(self):
        result = rules.filter_matches(
            {"object": "quote", "property": "amount", "operator": "gt", "value": 100},
            {"amount": 5},
            [{"amount": 9999}],
        )

        assert result["matched"] is False

    def test_a_deal_filter_reads_the_related_deal(self):
        result = rules.filter_matches(
            {"object": "deal", "property": "stage", "operator": "is", "value": "closed_lost"},
            {},
            (),
            {"stage": "closed_lost"},
        )

        assert result["matched"] is True

    def test_a_deal_filter_on_a_quote_with_no_deal_does_not_match(self):
        result = rules.filter_matches(
            {"object": "deal", "property": "stage", "operator": "is", "value": "x"}, {}, (), {}
        )

        assert result["matched"] is False


# --------------------------------------------------------------------------- #
# ten approvers
# --------------------------------------------------------------------------- #


class TestTheApproverCap:
    def test_ten_approvers_are_allowed(self):
        names = [f"user{index}" for index in range(10)]

        assert len(rules.validate_approvers(names)) == 10

    def test_an_eleventh_approver_is_refused(self):
        names = [f"user{index}" for index in range(11)]

        with pytest.raises(ApprovalRefusal) as caught:
            rules.validate_approvers(names)

        assert caught.value.code == "too_many_approvers"
        assert "up to 10" in caught.value.errors["approvers"]

    def test_the_cap_is_the_researched_number(self):
        assert vocab.MAX_APPROVERS == 10
        assert "up to 10 approvers" in vocab.APPROVER_CAP_QUOTE

    def test_no_approvers_is_refused(self):
        """A rule with no approver can never be satisfied and would hold every quote."""
        with pytest.raises(ApprovalRefusal) as caught:
            rules.validate_approvers([])

        assert caught.value.code == "rule_needs_at_least_one_approver"

    def test_the_same_approver_twice_is_refused(self):
        """Otherwise one person is waited for twice under an all-approvers rule."""
        with pytest.raises(ApprovalRefusal) as caught:
            rules.validate_approvers(["sam", "sam"])

        assert caught.value.code == "approver_repeated"

    def test_a_user_record_is_accepted_as_well_as_a_bare_id(self):
        """The flow's approver dropdown picks users, not bare strings."""
        assert rules.validate_approvers([{"id": "u1", "email": "sam@example.com"}]) == ["u1"]

    def test_the_cap_is_enforced_on_the_rule_not_at_enrolment(self, engine: QuoteApprovalEngine):
        """A rule with eleven approvers is wrong at save time."""
        with pytest.raises(ApprovalRefusal) as caught:
            discount_rule(engine, approvers=[f"user{i}" for i in range(11)])

        assert caught.value.code == "too_many_approvers"


# --------------------------------------------------------------------------- #
# all or at least one
# --------------------------------------------------------------------------- #


class TestTheApproverRequirement:
    def test_all_needs_every_approver(self):
        result = rules.requirement_met(vocab.REQUIREMENT_ALL, ["a", "b"], ["a"])

        assert result["met"] is False
        assert result["outstanding"] == ["b"]

    def test_all_is_met_when_everybody_approves(self):
        assert rules.requirement_met(vocab.REQUIREMENT_ALL, ["a", "b"], ["a", "b"])["met"] is True

    def test_at_least_one_is_met_by_a_single_approval(self):
        assert rules.requirement_met(vocab.REQUIREMENT_ANY, ["a", "b"], ["a"])["met"] is True

    def test_a_rejection_never_meets_the_requirement(self):
        """One person asking for changes is not a signature."""
        for requirement in vocab.APPROVER_REQUIREMENTS:
            result = rules.requirement_met(requirement, ["a", "b"], ["a"], ["b"])
            assert result["met"] is False, requirement
            assert result["rejected"] == ["b"]

    def test_an_approval_from_somebody_outside_the_list_is_not_counted(self):
        result = rules.requirement_met(vocab.REQUIREMENT_ALL, ["a"], ["a", "stranger"])

        assert result["approved"] == ["a"]
        assert result["met"] is True

    def test_an_empty_approver_list_is_never_met(self):
        """A vote from nobody must not satisfy an all-approvers rule."""
        assert rules.requirement_met(vocab.REQUIREMENT_ALL, [], [])["met"] is False

    def test_both_requirements_are_the_researched_words(self):
        assert vocab.APPROVER_REQUIREMENTS == ("all", "any")
        assert "All approvers required" in vocab.APPROVER_REQUIREMENT_LABELS["all"]


# --------------------------------------------------------------------------- #
# the creator is removed from the approver list
# --------------------------------------------------------------------------- #


class TestTheCreatorIsRemoved:
    def test_the_creator_is_dropped_when_others_remain(self):
        """The first half of the sourced sentence, with several approvers."""
        result = rules.resolve_approvers(["dana", "sam", "priya"], "dana")

        assert result["approvers"] == ["sam", "priya"]
        assert result["removed"] == ["dana"]
        assert result["creator_removed"] is True
        assert result["exempt"] is False

    def test_the_only_approver_being_the_creator_needs_no_approval(self):
        """The first half of the sourced sentence, with one approver."""
        result = rules.resolve_approvers(["dana"], "dana")

        assert result["approvers"] == []
        assert result["exempt"] is True

    def test_a_creator_who_is_not_an_approver_changes_nothing(self):
        result = rules.resolve_approvers(["sam", "priya"], "marcus")

        assert result["approvers"] == ["sam", "priya"]
        assert result["creator_removed"] is False
        assert result["exempt"] is False

    def test_removal_is_what_makes_the_second_sentence_true(self):
        """A creator who was removed has no vote to cast, so the two sentences agree."""
        resolved = rules.resolve_approvers(["dana", "sam"], "dana")

        assert "dana" not in resolved["approvers"]
        assert vocab.NO_SELF_APPROVAL_QUOTE == "Approvers can't approve their own quotes."

    def test_the_rule_is_stated_for_a_page_to_show(self):
        assert "removed from the approver list" in vocab.SELF_APPROVAL_RULE_TEXT

    def test_both_sourced_sentences_are_carried(self):
        assert "won't require approval" in vocab.SELF_APPROVAL_EXEMPTION_QUOTE
        assert vocab.NO_SELF_APPROVAL_QUOTE

    def test_the_decision_records_the_rejected_alternative(self):
        decision = inf.describe_one("SELF_APPROVAL_IS_RESOLVED_AT_ENROLMENT")

        assert decision["chosen"] == "enrolment_removal"
        assert "strict_no_own_vote" in decision["options"]
        assert decision["jev_audit_id"] == "jev-20261005T064607-13024-67413"
        assert decision["jev_confidence"] == 1.0

    def test_the_uncertain_first_question_is_recorded_rather_than_hidden(self):
        decision = inf.describe_one("SELF_APPROVAL_IS_RESOLVED_AT_ENROLMENT")

        assert decision["supersedes"] == "jev-20261005T064542-8564-42977"
        assert "uncertain" in decision["superseded_note"]

    def test_the_creator_cannot_vote_even_if_a_row_is_forged(self, engine: QuoteApprovalEngine):
        """The enrolment drops the creator, and decide refuses them as well.

        Two checks rather than one, because the list is data and a caller could
        post an approval naming somebody who was removed.
        """
        quote(engine, "q1", amount=5000)
        line(engine, "q1", discount=40)
        discount_rule(engine)
        enrolled = engine.submit("q1", actor="dana", source="fixture", room_id="room_a")

        with pytest.raises(ApprovalRefusal) as caught:
            engine.decide(
                enrolled["enrolment"]["id"],
                "approve",
                actor="dana",
                source="fixture",
                approver="dana",
            )

        assert caught.value.code == "approver_is_not_on_this_request"


# --------------------------------------------------------------------------- #
# re-submission clears every decision
# --------------------------------------------------------------------------- #


class TestResubmissionClearsEveryDecision:
    def test_a_re_submission_carries_no_decision_forward(self, engine: QuoteApprovalEngine):
        """The sourced sentence: every approver approves again on a re-submission."""
        quote(engine, "q1", amount=5000)
        line(engine, "q1", discount=40)
        discount_rule(engine, requirement=vocab.REQUIREMENT_ANY)

        first = engine.submit("q1", actor="dana", source="fixture", room_id="room_a")
        engine.decide(
            first["enrolment"]["id"],
            vocab.DECISION_REQUEST_CHANGES,
            actor="sam",
            source="fixture",
            approver="sam",
            message="Too deep.",
        )
        second = engine.submit("q1", actor="dana", source="fixture", room_id="room_a")

        assert first["enrolment"]["id"] != second["enrolment"]["id"]
        # A row per approver is created outstanding. What must not carry forward is
        # a recorded decision, so that is what the assertion is about.
        assert all(one["decision"] is None for one in second["enrolment"]["decisions"])
        assert second["enrolment"]["tally"]["approved"] == []
        assert second["enrolment"]["status"] == vocab.STATE_PENDING_APPROVAL

    def test_an_earlier_approval_does_not_satisfy_a_later_round(self, engine: QuoteApprovalEngine):
        """The defect the sentence forbids: sam approved round one, so round two passes."""
        quote(engine, "q1", amount=5000)
        line(engine, "q1", discount=40)
        discount_rule(engine, requirement=vocab.REQUIREMENT_ALL)

        first = engine.submit("q1", actor="dana", source="fixture", room_id="room_a")
        engine.decide(
            first["enrolment"]["id"],
            vocab.DECISION_APPROVE,
            actor="sam",
            source="fixture",
            approver="sam",
        )
        engine.decide(
            first["enrolment"]["id"],
            vocab.DECISION_REQUEST_CHANGES,
            actor="priya",
            source="fixture",
            approver="priya",
            message="No.",
        )
        second = engine.submit("q1", actor="dana", source="fixture", room_id="room_a")
        approved = engine.decide(
            second["enrolment"]["id"],
            vocab.DECISION_APPROVE,
            actor="sam",
            source="fixture",
            approver="sam",
        )

        assert approved["tally"]["approved"] == ["sam"]
        assert approved["tally"]["outstanding"] == ["priya"]
        assert approved["state"] == vocab.STATE_PENDING_APPROVAL

    def test_a_single_approver_round_is_satisfied_at_once(self, engine: QuoteApprovalEngine):
        """The edge the sourced sentence leaves open, decided and recorded."""
        quote(engine, "q1", amount=5000)
        line(engine, "q1", discount=40)
        # One approver in total, and he is not the creator, so exactly one person has
        # to decide each round.
        discount_rule(engine, approvers=["sam"], requirement=vocab.REQUIREMENT_ALL)
        first = engine.submit("q1", actor="dana", source="fixture", room_id="room_a")
        engine.decide(
            first["enrolment"]["id"],
            vocab.DECISION_REQUEST_CHANGES,
            actor="sam",
            source="fixture",
            approver="sam",
            message="No.",
        )
        second = engine.submit("q1", actor="dana", source="fixture", room_id="room_a")
        decided = engine.decide(
            second["enrolment"]["id"],
            vocab.DECISION_APPROVE,
            actor="sam",
            source="fixture",
            approver="sam",
        )

        assert decided["state"] == vocab.STATE_APPROVED
        assert inf.describe_one("DERIVED_RESUBMISSION_SINGLE_APPROVER")["chosen"] == (
            "requirement_met_at_once"
        )


# --------------------------------------------------------------------------- #
# only an approved quote may be shared
# --------------------------------------------------------------------------- #


class TestOnlyAnApprovedQuoteMayBeShared:
    @pytest.mark.parametrize(
        "state", [vocab.STATE_DRAFT, vocab.STATE_PENDING_APPROVAL, vocab.STATE_REJECTED]
    )
    def test_an_unapproved_state_is_refused(self, state: str):
        with pytest.raises(ApprovalRefusal) as caught:
            rules.require_shareable(state)

        assert "only on approval" in str(caught.value)

    @pytest.mark.parametrize("state", vocab.SHAREABLE_STATES)
    def test_an_approved_state_is_shareable(self, state: str):
        assert rules.require_shareable(state) == state

    def test_the_refusal_quotes_the_research(self):
        """The refusal message is the researched sentence, so an API caller reads the rule."""
        with pytest.raises(ApprovalRefusal) as caught:
            rules.require_shareable("DRAFT")

        assert vocab.SHARE_ON_APPROVAL_QUOTE in caught.value.detail

    def test_a_share_before_approval_is_refused_over_the_engine(self, engine: QuoteApprovalEngine):
        quote(engine, "q1", amount=5000)

        with pytest.raises(ApprovalRefusal):
            engine.share("q1", actor="dana", source="fixture", room_id="room_a")

    def test_a_refused_share_is_written_to_the_activity_log(self, engine: QuoteApprovalEngine):
        """A stop nobody can find is a stop nobody can check."""
        quote(engine, "q1", amount=5000)

        with pytest.raises(ApprovalRefusal):
            engine.share("q1", actor="dana", source="fixture", room_id="room_a")

        assert [one["activity"] for one in engine.activities("q1")] == [
            vocab.ACTIVITY_SHARE_REFUSED
        ]

    def test_a_share_after_approval_succeeds(self, engine: QuoteApprovalEngine):
        quote(engine, "q1", amount=5000)
        line(engine, "q1", discount=40)
        discount_rule(engine, approvers=["sam", "priya"], requirement=vocab.REQUIREMENT_ALL)
        enrolled = engine.submit("q1", actor="dana", source="fixture", room_id="room_a")
        for identity in ("sam", "priya"):
            engine.decide(
                enrolled["enrolment"]["id"],
                vocab.DECISION_APPROVE,
                actor=identity,
                source="fixture",
                approver=identity,
            )

        assert engine.share("q1", actor="dana", source="fixture", room_id="room_a")["state"] == (
            vocab.STATE_SHARED
        )


# --------------------------------------------------------------------------- #
# three statuses release a lock
# --------------------------------------------------------------------------- #


class TestThreeStatusesReleaseALock:
    def test_the_three_releases_are_the_researched_ones(self):
        assert vocab.UNLOCK_TARGET_STATES == ("DRAFT", "PENDING_APPROVAL", "REJECTED")

    def test_the_vocabulary_quotes_the_sentence_whole(self):
        assert "hs_status" in vocab.UNLOCK_TARGET_QUOTE
        assert "DRAFT" in vocab.UNLOCK_TARGET_QUOTE

    def test_an_approved_quote_is_locked(self):
        assert rules.evaluate_locked(vocab.STATE_APPROVED, None)["locked"] is True

    def test_a_pending_quote_is_editable(self):
        """PENDING_APPROVAL is one of the three releases, so a seller can edit while waiting."""
        outcome = rules.evaluate_locked(vocab.STATE_PENDING_APPROVAL, None)

        assert outcome["locked"] is False
        assert vocab.STATE_PENDING_APPROVAL in outcome["unlock_targets"]

    def test_a_rejected_quote_is_editable(self):
        """The seller's next step after a change request is to edit."""
        assert rules.evaluate_locked(vocab.STATE_REJECTED, None)["locked"] is False

    def test_the_hs_locked_field_reads_true_when_published(self):
        assert rules.evaluate_locked(vocab.STATE_DRAFT, True)["locked"] is True

    def test_the_reason_names_the_three_releases(self):
        assert "REJECTED" in rules.evaluate_locked(vocab.STATE_SHARED, None)["reason"]


# --------------------------------------------------------------------------- #
# the workflow, over the store
# --------------------------------------------------------------------------- #


class TestTheWorkflowOverTheStore:
    def test_a_matching_quote_is_enrolled_as_pending(self, engine: QuoteApprovalEngine):
        quote(engine, "q1", amount=5000)
        line(engine, "q1", discount=40)
        discount_rule(engine)

        answer = engine.submit("q1", actor="dana", source="fixture", room_id="room_a")

        assert answer["enrolled"] is True
        assert answer["required"] is True
        assert answer["enrolment"]["status"] == vocab.STATE_PENDING_APPROVAL

    def test_the_creator_is_named_on_the_enrolment(self, engine: QuoteApprovalEngine):
        quote(engine, "q1", amount=5000, creator="marcus")
        line(engine, "q1", discount=40)
        discount_rule(engine)

        answer = engine.submit("q1", actor="marcus", source="fixture", room_id="room_a")

        assert answer["creator"] == "marcus"
        assert answer["removed_approvers"] == []

    def test_a_quote_matching_nothing_is_not_enrolled(self, engine: QuoteApprovalEngine):
        quote(engine, "q1", amount=5000)
        line(engine, "q1", discount=5)
        discount_rule(engine)

        answer = engine.submit("q1", actor="dana", source="fixture", room_id="room_a")

        assert answer["enrolled"] is False
        assert answer["reason"] == vocab.ENROLMENT_EXEMPT_NO_MATCH
        assert engine.store.list(vocab.APPROVAL_REQUESTS) == []

    def test_an_inactive_rule_matches_nothing_and_says_so(self, engine: QuoteApprovalEngine):
        """The two no-enrolment reasons have different fixes, so they are named apart."""
        quote(engine, "q1", amount=5000)
        line(engine, "q1", discount=40)
        rule = discount_rule(engine)
        engine.patch_rule(rule["id"], {"enabled": False}, actor="dana", source="fixture")

        answer = engine.submit("q1", actor="dana", source="fixture", room_id="room_a")

        assert answer["enrolled"] is False
        assert answer["reason"] == vocab.ENROLMENT_EXEMPT_RULE_INACTIVE
        assert answer["inactive_rules"] == ["Deep discount on any line item"]

    def test_the_sole_approver_author_is_exempt_and_recorded(self, engine: QuoteApprovalEngine):
        quote(engine, "q1", amount=5000)
        engine.create_rule(
            {
                "key": "sole",
                "filters": [
                    {"object": "quote", "property": "amount", "operator": "gt", "value": 0}
                ],
                "approvers": ["dana"],
            },
            actor="dana",
            source="fixture",
            room_id="room_a",
        )

        answer = engine.submit("q1", actor="dana", source="fixture", room_id="room_a")

        assert answer["enrolled"] is True
        assert answer["required"] is False
        assert answer["enrolment"]["exempt"] is True
        assert answer["enrolment"]["exemption_reason"] == vocab.ENROLMENT_EXEMPT_SOLE_APPROVER
        assert answer["enrolment"]["status"] == vocab.STATE_DRAFT

    def test_an_exempt_enrolment_notifies_nobody(self, engine: QuoteApprovalEngine):
        """Nobody has to approve it, so telling an approver would be noise."""
        quote(engine, "q1", amount=5000)
        engine.create_rule(
            {
                "key": "sole",
                "filters": [
                    {"object": "quote", "property": "amount", "operator": "gt", "value": 0}
                ],
                "approvers": ["dana"],
            },
            actor="dana",
            source="fixture",
            room_id="room_a",
        )

        engine.submit("q1", actor="dana", source="fixture", room_id="room_a")

        assert engine.notifications(quote_id="q1") == []

    def test_each_approver_gets_a_decision_row(self, engine: QuoteApprovalEngine):
        quote(engine, "q1", amount=5000)
        line(engine, "q1", discount=40)
        discount_rule(engine)

        answer = engine.submit("q1", actor="dana", source="fixture", room_id="room_a")

        rows = engine.decisions_for(answer["enrolment"]["id"])
        assert sorted(one["approver"] for one in rows) == ["priya", "sam"]
        assert all(one["outstanding"] is True for one in rows)

    def test_the_conditions_panel_is_the_flows_view(self, engine: QuoteApprovalEngine):
        """Step four: hover Request approval, View approval conditions."""
        quote(engine, "q1", amount=5000)
        line(engine, "q1", discount=40)
        discount_rule(engine)

        conditions = engine.conditions("q1")

        assert conditions["required"] is True
        panel = conditions["rules"][0]
        assert panel["rule_label"] == "Deep discount on any line item"
        assert panel["approvers"] == ["sam", "priya"]
        assert panel["matched_filters"][0]["actual"] == 40

    def test_the_conditions_panel_shows_the_self_approval_reading(
        self, engine: QuoteApprovalEngine
    ):
        quote(engine, "q1", amount=5000)

        assert engine.conditions("q1")["self_approval"]["jev_audit_id"] == (
            "jev-20261005T064607-13024-67413"
        )

    def test_every_write_names_a_route(self, engine: QuoteApprovalEngine):
        """An audit row naming a path the app stopped serving is a defect that shipped once."""
        quote(engine, "q1", amount=5000)
        line(engine, "q1", discount=40)
        discount_rule(engine)
        answer = engine.submit("q1", actor="dana", source="fixture", room_id="room_a")
        engine.decide(
            answer["enrolment"]["id"],
            vocab.DECISION_APPROVE,
            actor="sam",
            source="fixture",
            approver="sam",
        )

        for collection in (
            vocab.APPROVAL_RULES,
            vocab.APPROVAL_REQUESTS,
            vocab.APPROVAL_DECISIONS,
            vocab.ACTIVITIES,
            vocab.NOTIFICATIONS,
        ):
            entries = engine.store.audit(collection=collection, limit=200)
            assert entries, collection
            for entry in entries:
                assert entry["source"], entry

    def test_a_quote_this_workflow_does_not_own_is_never_written(self, engine: QuoteApprovalEngine):
        """WF-086 writes the quote. A second writer would break a field it owns."""
        quote(engine, "q1", amount=5000, hs_status=vocab.STATE_DRAFT)
        line(engine, "q1", discount=40)
        discount_rule(engine, approvers=["sam", "priya"], requirement=vocab.REQUIREMENT_ALL)
        answer = engine.submit("q1", actor="dana", source="fixture", room_id="room_a")
        for identity in ("sam", "priya"):
            engine.decide(
                answer["enrolment"]["id"],
                vocab.DECISION_APPROVE,
                actor=identity,
                source="fixture",
                approver=identity,
            )
        engine.share("q1", actor="dana", source="fixture", room_id="room_a")

        reread = engine.store.require("q1")["data"]
        assert reread["hs_status"] == vocab.STATE_DRAFT
        assert reread["amount"] == 5000

    def test_an_unknown_quote_is_a_refusal_not_a_crash(self, engine: QuoteApprovalEngine):
        with pytest.raises(rules.ApprovalNotFound) as caught:
            engine.conditions("no-such-quote")

        assert caught.value.code == "unknown_quote"

    def test_the_latest_enrolment_answers_the_state(self, engine: QuoteApprovalEngine):
        """A quote's own hs_status is the older fact and the enrolment is the newer one."""
        quote(engine, "q1", amount=5000, hs_status=vocab.STATE_DRAFT)
        line(engine, "q1", discount=40)
        discount_rule(engine)
        engine.submit("q1", actor="dana", source="fixture", room_id="room_a")

        state = engine.quote_state("q1")

        assert state["stated_status"] == vocab.STATE_DRAFT
        assert state["effective_status"] == vocab.STATE_PENDING_APPROVAL
        assert state["authority"] == "approval_enrolment"

    def test_the_board_counts_states_from_the_payload(self, engine: QuoteApprovalEngine):
        """The defect: reading status off the envelope reports every quote as DRAFT."""
        quote(engine, "q1", amount=5000)
        line(engine, "q1", discount=40)
        discount_rule(engine)
        engine.submit("q1", actor="dana", source="fixture", room_id="room_a")

        assert engine.summary(room_id="room_a")["by_state"][vocab.STATE_PENDING_APPROVAL] == 1


# --------------------------------------------------------------------------- #
# notifications and activities
# --------------------------------------------------------------------------- #


class TestNotifications:
    def test_each_approver_is_notified_on_the_rules_channels(self, engine: QuoteApprovalEngine):
        quote(engine, "q1", amount=5000)
        line(engine, "q1", discount=40)
        discount_rule(engine)

        engine.submit("q1", actor="dana", source="fixture", room_id="room_a")

        rows = engine.notifications(quote_id="q1")
        assert sorted({one["recipient"] for one in rows}) == ["priya", "sam"]
        assert sorted({one["channel"] for one in rows}) == ["email", "in_app"]

    def test_no_notification_claims_to_have_been_delivered(self, engine: QuoteApprovalEngine):
        """There is no mail transport here. A receipt-shaped row would mislead."""
        quote(engine, "q1", amount=5000)
        line(engine, "q1", discount=40)
        discount_rule(engine)

        engine.submit("q1", actor="dana", source="fixture", room_id="room_a")

        for row in engine.notifications(quote_id="q1"):
            assert row["delivered"] is False
            assert row["dispatched_by"] == vocab.DISPATCH_DISPATCHED_BY
            assert row["recorded_only_note"]

    def test_the_notification_carries_the_researched_note(self, engine: QuoteApprovalEngine):
        """Step three: "Enter **With this approval note**"."""
        quote(engine, "q1", amount=5000)
        line(engine, "q1", discount=40)
        discount_rule(engine)

        engine.submit("q1", actor="dana", source="fixture", room_id="room_a")

        assert engine.notifications(quote_id="q1")[0]["note"] == "Check the margin."

    def test_the_creator_is_told_the_outcome(self, engine: QuoteApprovalEngine):
        quote(engine, "q1", amount=5000)
        line(engine, "q1", discount=40)
        discount_rule(engine, approvers=["sam"], requirement=vocab.REQUIREMENT_ANY)
        answer = engine.submit("q1", actor="dana", source="fixture", room_id="room_a")
        engine.decide(
            answer["enrolment"]["id"],
            vocab.DECISION_APPROVE,
            actor="sam",
            source="fixture",
            approver="sam",
        )

        to_creator = [
            one for one in engine.notifications(quote_id="q1") if one["recipient_role"] == "creator"
        ]
        assert to_creator
        assert to_creator[0]["recipient"] == "dana"

    def test_all_five_researched_channels_are_served(self):
        assert set(vocab.NOTIFICATION_CHANNEL_LABELS) >= {
            "in_app",
            "email",
            "slack",
            "teams",
            "google_chat",
        }

    def test_the_notification_is_recorded_and_not_delivered_is_recorded(self):
        assert inf.describe_one("DERIVED_NOTIFICATIONS_ARE_RECORDED")["chosen"] == "record_only"


class TestActivities:
    def test_the_three_sourced_activity_names_are_written(self, engine: QuoteApprovalEngine):
        quote(engine, "q1", amount=5000)
        line(engine, "q1", discount=40)
        discount_rule(engine, approvers=["sam", "priya"], requirement=vocab.REQUIREMENT_ALL)
        answer = engine.submit("q1", actor="dana", source="fixture", room_id="room_a")
        engine.decide(
            answer["enrolment"]["id"],
            vocab.DECISION_REQUEST_CHANGES,
            actor="sam",
            source="fixture",
            approver="sam",
            message="No.",
        )
        engine.submit("q1", actor="dana", source="fixture", room_id="room_a")
        second = engine.latest_enrolment("q1")
        for identity in ("sam", "priya"):
            engine.decide(
                second["id"],
                vocab.DECISION_APPROVE,
                actor=identity,
                source="fixture",
                approver=identity,
            )

        names = [one["activity"] for one in engine.activities("q1")]
        assert vocab.ACTIVITY_REQUESTED in names
        assert vocab.ACTIVITY_REJECTED in names
        assert vocab.ACTIVITY_APPROVED in names

    def test_a_sourced_activity_is_flagged_as_sourced(self, engine: QuoteApprovalEngine):
        quote(engine, "q1", amount=5000)
        line(engine, "q1", discount=40)
        discount_rule(engine)

        engine.submit("q1", actor="dana", source="fixture", room_id="room_a")

        assert engine.activities("q1")[0]["sourced"] is True

    def test_a_build_added_activity_is_flagged_as_not_sourced(self, engine: QuoteApprovalEngine):
        """The flow describes the moment and the research names the log without it."""
        quote(engine, "q1", amount=5000)
        engine.create_rule(
            {
                "key": "sole",
                "filters": [
                    {"object": "quote", "property": "amount", "operator": "gt", "value": 0}
                ],
                "approvers": ["dana"],
            },
            actor="dana",
            source="fixture",
            room_id="room_a",
        )

        engine.submit("q1", actor="dana", source="fixture", room_id="room_a")

        written = engine.activities("q1")[0]
        assert written["activity"] == vocab.ACTIVITY_ENROLLED_WITHOUT_APPROVAL
        assert written["sourced"] is False

    def test_the_three_sourced_names_are_the_research_spelling(self):
        assert vocab.ACTIVITY_REQUESTED == "Quote approval requested"
        assert vocab.ACTIVITY_APPROVED == "Quote approved"


# --------------------------------------------------------------------------- #
# decisions and their refusals
# --------------------------------------------------------------------------- #


class TestDecisionRefusals:
    def _enrol(self, engine: QuoteApprovalEngine, **rule):
        quote(engine, "q1", amount=5000)
        line(engine, "q1", discount=40)
        discount_rule(engine, **rule)
        return engine.submit("q1", actor="dana", source="fixture", room_id="room_a")["enrolment"][
            "id"
        ]

    def test_somebody_outside_the_approver_list_is_refused(self, engine: QuoteApprovalEngine):
        enrol_id = self._enrol(engine)

        with pytest.raises(ApprovalRefusal) as caught:
            engine.decide(
                enrol_id, "approve", actor="stranger", source="fixture", approver="stranger"
            )

        assert caught.value.code == "approver_is_not_on_this_request"

    def test_an_unnamed_approver_is_refused(self, engine: QuoteApprovalEngine):
        enrol_id = self._enrol(engine)

        with pytest.raises(ApprovalRefusal):
            engine.decide(enrol_id, "approve", actor=None, source="fixture", approver=None)

    def test_a_change_request_with_no_reason_is_refused(self, engine: QuoteApprovalEngine):
        """A change request with nothing in it is not an instruction the seller can act on."""
        enrol_id = self._enrol(engine)

        with pytest.raises(ApprovalRefusal) as caught:
            engine.decide(
                enrol_id,
                "request_changes",
                actor="sam",
                source="fixture",
                approver="sam",
                message="  ",
            )

        assert caught.value.code == "reason_required_to_request_changes"

    def test_a_second_decision_on_a_settled_request_is_refused(self, engine: QuoteApprovalEngine):
        enrol_id = self._enrol(
            engine, approvers=["sam", "priya"], requirement=vocab.REQUIREMENT_ANY
        )
        engine.decide(enrol_id, "approve", actor="sam", source="fixture", approver="sam")

        with pytest.raises(ApprovalRefusal) as caught:
            engine.decide(enrol_id, "approve", actor="priya", source="fixture", approver="priya")

        assert caught.value.code == "quote_is_not_pending"

    def test_the_wire_spelling_reject_is_accepted(self, engine: QuoteApprovalEngine):
        """The flow's button says Reject, not request_changes."""
        enrol_id = self._enrol(engine)

        answer = engine.decide(
            enrol_id,
            vocab.DECISION_REJECT_WIRE,
            actor="sam",
            source="fixture",
            approver="sam",
            message="No.",
        )

        assert answer["state"] == vocab.STATE_REJECTED

    def test_an_unknown_decision_is_refused(self, engine: QuoteApprovalEngine):
        enrol_id = self._enrol(engine)

        with pytest.raises(ApprovalRefusal) as caught:
            engine.decide(enrol_id, "shrug", actor="sam", source="fixture", approver="sam")

        assert caught.value.code == "unknown_decision"

    def test_an_unknown_trigger_is_refused(self, engine: QuoteApprovalEngine):
        quote(engine, "q1", amount=5000)

        with pytest.raises(ApprovalRefusal) as caught:
            engine.submit(
                "q1", actor="dana", source="fixture", room_id="room_a", trigger="whenever"
            )

        assert caught.value.code == "unknown_trigger"

    def test_the_publish_trigger_enrols_the_same_way(self, engine: QuoteApprovalEngine):
        """Enrolment fires on the publish attempt, not only on Request approval."""
        quote(engine, "q1", amount=5000)
        line(engine, "q1", discount=40)
        discount_rule(engine)

        answer = engine.submit(
            "q1", actor="dana", source="fixture", room_id="room_a", trigger=vocab.TRIGGER_PUBLISH
        )

        assert answer["enrolled"] is True
        assert answer["enrolment"]["trigger"] == vocab.TRIGGER_PUBLISH


# --------------------------------------------------------------------------- #
# the architectural guards
# --------------------------------------------------------------------------- #


class TestArchitecture:
    def test_the_domain_package_imports_nothing_but_the_store(self):
        """The guard the brief names by name.

        The rule is about the dependency direction, not about banning the standard
        library. ``decimal`` and ``datetime`` are ordinary Python. A defect is a domain
        module reaching for ``dsr.api`` or for another workflow's package.
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
                    ), f"{path.name} imports {name}"

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
                    assert not name.startswith("dsr.features"), f"{path.name} imports {name}"

    def test_the_prefix_is_the_ticket_slug_the_issue_names(self):
        module = importlib.import_module(FEATURE_MODULE)

        assert module.router.prefix == "/api/WF-091"

    def test_the_feature_exports_a_descriptor(self):
        module = importlib.import_module(FEATURE_MODULE)

        assert module.FEATURE["id"].startswith("wf-091-")
        assert module.FEATURE["ticket"] == "WF-091"

    def test_the_error_handlers_cover_both_of_this_workflows_types(self):
        module = importlib.import_module(FEATURE_MODULE)

        assert set(module.EXCEPTION_HANDLERS) == {ApprovalRefusal, rules.ApprovalNotFound}

    def test_the_handler_does_not_claim_a_shared_type(self):
        """Two features may not map the same error type. RecordNotFound is the core's."""
        module = importlib.import_module(FEATURE_MODULE)

        for handled in module.EXCEPTION_HANDLERS:
            assert handled.__module__.startswith("dsr.quoting_proposals"), handled


# --------------------------------------------------------------------------- #
# the seed return string
# --------------------------------------------------------------------------- #


class TestTheSeedString:
    def _seed(self, rooms: list, **overrides):
        """Run the feature's seed over a fresh database and return its string and store.

        The seeder passes ``(room_id, name)`` pairs, so the rooms are taken as the
        first element of each pair rather than as the pair itself. Passing the pair
        through would reach the store as a tuple and fail at the SQL binding, which
        is this helper's bug and not the seed's.
        """
        module = importlib.import_module(FEATURE_MODULE)
        from dsr.db.audited import AuditedDatabase

        pairs = [entry if isinstance(entry, (tuple, list)) else (entry, "") for entry in rooms]
        db = AuditedDatabase(":memory:", actor="seed-test")
        try:
            store = RecordStore(db)
            for room_id, name in pairs:
                store.create(
                    "room", {"name": name}, record_id=room_id, actor="dana", source="fixture"
                )
            summary = module.seed(db, {"room_ids": pairs, "now": NOW, "rng": None, **overrides})
            # The rows are read inside the `try`, because the helper closes the
            # database in `finally` and a caller holding the store would be holding a
            # handle to a closed connection.
            enrolments = store.list(vocab.APPROVAL_REQUESTS, limit=200)
            return summary, enrolments
        finally:
            db.close()

    def test_the_seeder_prints_a_string_and_every_character_survives_cp1252(self):
        """The defect that broke the whole seeder, stated as the assertion that prevents it.

        One RIGHTWARDS ARROW in a recovered feature's return string broke the entire
        seeder on a Windows console, because the seeder prints it to a cp1252 console.
        """
        summary, _ = self._seed([("room_a", "Northwind")])

        assert isinstance(summary, str)
        assert summary
        summary.encode("cp1252")

    def test_the_seed_states_are_really_there(self):
        """A seed line describing a state the seed did not produce is a lie in a demo."""
        summary, enrolments = self._seed([("room_a", "Northwind"), ("room_b", "Halcyon")])

        assert len(enrolments) == 4
        states = [row["data"]["status"] for row in enrolments]
        assert states.count(vocab.STATE_PENDING_APPROVAL) >= 1
        assert states.count(vocab.STATE_APPROVED) >= 1
        assert states.count(vocab.STATE_REJECTED) >= 1
        assert len([row for row in enrolments if row["data"]["exempt"]]) >= 1
        assert "not delivered" in summary

    def test_a_seed_with_no_rooms_returns_a_string_rather_than_failing(self):
        module = importlib.import_module(FEATURE_MODULE)
        from dsr.db.audited import AuditedDatabase

        db = AuditedDatabase(":memory:", actor="seed-test")
        try:
            summary = module.seed(db, {"room_ids": [], "now": NOW, "rng": None})
        finally:
            db.close()

        assert isinstance(summary, str)
        summary.encode("cp1252")

    def test_every_demo_rule_enrols_only_its_own_quote(self):
        """Two rules in one room must not enrol each other's quotes."""
        _, enrolments = self._seed([("room_a", "Northwind")])

        assert enrolments
        # Four rules and four quotes in one room, each quote enrolled by its own rule
        # only. A demo where every plan enrols every quote shows wrong states.
        assert len({row["data"]["quote_id"] for row in enrolments}) == len(enrolments)
