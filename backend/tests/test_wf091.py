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
from dsr.quoting_proposals import quote_approval_inferences as inf
from dsr.quoting_proposals import quote_approval_rules as rules
from dsr.quoting_proposals import quote_approval_vocabulary as vocab
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
    store.create("room", {"name": "Northwind data room"}, record_id="room_a", actor="dana", source="fixture")
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
        "wf086_line_item", {"quote_id": quote_id, "sku": "SEAT-STD", **data},
        room_id="room_a", actor="dana", source="fixture",
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
        filters = [{"object": "quote", "property": "commercial.discount_band", "operator": "is", "value": "gold"}]
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
                {"object": "quote", "property": "value" if operator in ("is", "is_not", "contains", "does_not_contain", "in", "not_in") else "count",
                 "operator": operator, "value": expected},
                quote_side,
            )
            assert result["matched"] is wanted, operator

    def test_an_unknown_operator_is_refused_and_names_the_set(self):
        with pytest.raises(ApprovalRefusal) as caught:
            rules.validate_filter({"object": "quote", "property": "amount", "operator": "roughly", "value": 1})

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
        record = quote(engine, "q1", amount=5000)
        line(engine, "q1", discount=40)
        discount_rule(engine)
        enrolled = engine.submit("q1", actor="dana", source="fixture", room_id="room_a")

        with pytest.raises(ApprovalRefusal) as caught:
            engine.decide(
                enrolled["enrolment"]["id"], "approve", actor="dana", source="fixture", approver="dana"
            )

        assert caught.value.code == "approver_is_not_on_this_request"
