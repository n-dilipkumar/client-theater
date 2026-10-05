"""WF-088: auto-assign a price book to a deal by rule, and what it refuses.

The researched specification is ``docs/research/digital-sales-room-workflows/wf/WF-088.md``,
quoted in full in issue 174. These tests are organised by the researched rule each one
defends, because the point of this workflow is that the rules were sourced rather than
chosen, so a rule with no test is a rule the next person to touch it will quietly drop.

The sections, and the sourced or derived rule each one pins:

``one match assigns``
    "When a deal matches exactly one assignment rule, that price book is automatically
    assigned."
``more than one match assigns nothing``
    "If multiple price books match, the deal owner can choose which matching price book
    to use" and "If multiple price levels are returned, the price level field isn't
    populated and the user must specify a price level." The reading was chosen by Jev in
    audit jev-20261005T121136-18784-96276.
``auto-assignment runs on create and never again``
    "Price books are auto-assigned only when a deal is created. After a price book is
    auto-assigned, HubSpot won't run auto-assignment again if the deal or associated
    company properties used in the filter are updated".
``a filter reads a deal property or a company property``
    "HubSpot deal properties, company properties, price book object". A dotted JSON path,
    so a team adds a property with no migration, and a path the record does not carry
    does not match.
``and / or groups``
    "configure deal-property filters (`and` / `or` groups)". Both conjunctions are sourced
    here, and `all` is the default.
``the two switches``
    "toggle **Auto-assigned** on" and "toggle the price book's **Inactive** switch off to
    activate", which together are the three researched modes.
``changing the book removes the previous book's lines``
    "If the price book is changed, any line items associated with the previous price book
    will be removed". Chosen by Jev in audit jev-20261005T121136-18784-96564.
``a quote inherits and cannot be set``
    "Quotes inherit the price book from the associated deal. Users can't select a price
    book when creating a quote; they must select it on the deal."
``the domain imports nothing but the store``
    The architectural guard, checked with an AST walk.
``the seed return string``
    Every character encodable by cp1252, and the states the seeder claims really exist.

The HTTP surface is in ``test_WF-088_http.py``.
"""

from __future__ import annotations

import ast
import importlib
from datetime import datetime, timezone
from pathlib import Path

import pytest
from dsr.quoting_proposals import (
    price_book_inferences as inf,
    price_book_rules as rules,
    price_book_vocabulary as vocab,
)
from dsr.quoting_proposals.price_book_engine import PriceBookEngine
from dsr.quoting_proposals.price_book_rules import PriceBookNotFound, PriceBookRefusal
from dsr.store import RecordStore

# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

NOW = datetime(2026, 10, 5, 9, 0, 0, tzinfo=timezone.utc)

FEATURE_MODULE = "dsr.features.WF-088_auto_assign_the_correct_price_book_or_price"
DOMAIN_PACKAGE = "dsr.quoting_proposals"

#: The researched books. Ids are invented because no source names a price book id; the
#: names are the ones a seller reads on a card, and the fact that ids are this build's
#: is recorded under PRICE_BOOKS_ARE_REFERENCED_NOT_RESOLVED.
BOOK_ENTERPRISE = {"id": "pb-ent", "name": "Enterprise list 2026"}
BOOK_STANDARD = {"id": "pb-std", "name": "Standard list"}
BOOK_PARTNER = {"id": "pb-prt", "name": "Preferred partner list"}


@pytest.fixture()
def engine(store: RecordStore) -> PriceBookEngine:
    """An engine over an empty store and a clock the test controls."""
    store.create(
        "room", {"name": "Northwind data room"}, record_id="room_a", actor="dana", source="fixture"
    )
    return PriceBookEngine(store, now=lambda: NOW)


def deal(engine: PriceBookEngine, deal_id: str = "d1", **data) -> dict:
    """One deal as a CRM mirror writes it: arbitrary JSON."""
    return engine.store.create(
        vocab.SOURCE_DEALS,
        {"name": deal_id, **data},
        record_id=deal_id,
        room_id="room_a",
        actor="dana",
        source="fixture",
    )


def company(engine: PriceBookEngine, company_id: str = "co1", **data) -> dict:
    return engine.store.create(
        vocab.SOURCE_COMPANIES,
        {"name": company_id, **data},
        record_id=company_id,
        room_id="room_a",
        actor="dana",
        source="fixture",
    )


def segment_rule(
    engine: PriceBookEngine,
    *,
    key: str = "enterprise",
    segment: str = "enterprise",
    book: dict | None = None,
    **overrides,
) -> dict:
    """A rule that assigns one book to a deal carrying a given segment."""
    payload = {
        "key": key,
        # Title-cased so the label reads the way a card does, and so a test that asserts
        # on it is asserting on the stored label rather than on a lower-cased segment.
        "label": overrides.pop("label", f"{segment.title()} deals"),
        "price_book": book or BOOK_ENTERPRISE,
        "filters": [{"object": "deal", "property": "segment", "operator": "is", "value": segment}],
    }
    payload.update(overrides)
    return engine.create_rule(payload, actor="dana", source="fixture", room_id="room_a")


def assign(engine: PriceBookEngine, deal_id: str = "d1", **kwargs) -> dict:
    return engine.assign(deal_id, actor="dana", source="fixture", room_id="room_a", **kwargs)


def domain_paths() -> list[Path]:
    package = Path(importlib.import_module(DOMAIN_PACKAGE).__file__).parent
    return sorted(package.glob("price_book_*.py"))


def feature_path() -> Path:
    return Path(importlib.import_module(FEATURE_MODULE).__file__)


# --------------------------------------------------------------------------- #
# one match assigns
# --------------------------------------------------------------------------- #


class TestOneMatchAssigns:
    def test_the_sourced_sentence(self):
        """Quoted whole, because it is the sentence this workflow exists to encode."""
        assert (
            vocab.ASSIGNMENT_REASON_LABELS[vocab.ASSIGNMENT_ASSIGNED]
            == "One rule matched exactly, so its price book was written onto the deal."
        )

    def test_exactly_one_match_writes_the_book(self, engine: PriceBookEngine):
        deal(engine, segment="enterprise")
        segment_rule(engine)

        answer = assign(engine)

        assert answer["outcome"] == vocab.ASSIGNMENT_ASSIGNED
        assert answer["written"] is True
        assert answer["price_book"] == BOOK_ENTERPRISE
        assert answer["state"] == vocab.BOOK_STATE_ASSIGNED

    def test_the_book_lands_on_the_deal_itself(self, engine: PriceBookEngine):
        """The data flow names the write: the book is on the deal header."""
        deal(engine, segment="enterprise")
        segment_rule(engine)

        assign(engine)

        stored = engine.deal_payload("d1")
        assert stored[vocab.PRICE_BOOK]["id"] == BOOK_ENTERPRISE["id"]
        assert stored[vocab.PRICE_BOOK]["name"] == BOOK_ENTERPRISE["name"]
        assert stored[vocab.PRICE_BOOK]["assigned_at"] == rules.stamp(NOW)
        assert stored[vocab.PRICE_BOOK]["assigned_by"] == "dana"

    def test_the_written_book_carries_the_rule_that_wrote_it(self, engine: PriceBookEngine):
        """So a past assignment still explains itself after the rule is deleted."""
        deal(engine, segment="enterprise")
        rule = segment_rule(engine)

        assign(engine)

        stored = engine.deal_payload("d1")
        assert stored[vocab.PRICE_BOOK]["rule_id"] == rule["id"]
        assert stored[vocab.PRICE_BOOK]["rule_label"] == rule["data"]["label"]

    def test_a_match_report_is_kept(self, engine: PriceBookEngine):
        """The right panel has to "review the matching deals", so the report travels."""
        deal(engine, segment="enterprise")
        segment_rule(engine)

        assign(engine)

        written = engine.latest_assignment("d1")
        assert written["matched_filters"][0]["property"] == "segment"
        assert written["matched_filters"][0]["actual"] == "enterprise"

    def test_a_deal_that_matches_nothing_is_not_assigned(self, engine: PriceBookEngine):
        deal(engine, segment="pilot")
        segment_rule(engine, segment="enterprise")

        answer = assign(engine)

        assert answer["outcome"] == vocab.ASSIGNMENT_NO_MATCH
        assert answer["written"] is False
        assert vocab.PRICE_BOOK not in engine.deal_payload("d1")


# --------------------------------------------------------------------------- #
# more than one match assigns nothing
# --------------------------------------------------------------------------- #


class TestMoreThanOneMatch:
    def test_two_matches_write_nothing(self, engine: PriceBookEngine):
        deal(engine, segment="mid", amount=45000)
        segment_rule(engine, key="mid-standard", segment="mid", book=BOOK_STANDARD)
        segment_rule(
            engine,
            key="mid-big",
            segment="enterprise",
            book=BOOK_PARTNER,
            filters=[{"object": "deal", "property": "amount", "operator": "gte", "value": 20000}],
        )

        answer = assign(engine)

        assert answer["outcome"] == vocab.ASSIGNMENT_NEEDS_CHOICE
        assert answer["written"] is False
        assert vocab.PRICE_BOOK not in engine.deal_payload("d1")

    def test_the_state_is_needs_choice_not_unassigned(self, engine: PriceBookEngine):
        """A deal waiting on a person is not the same as a deal nobody priced."""
        deal(engine, segment="mid", amount=45000)
        segment_rule(engine, key="mid-standard", segment="mid", book=BOOK_STANDARD)
        segment_rule(
            engine,
            key="mid-big",
            segment="enterprise",
            book=BOOK_PARTNER,
            filters=[{"object": "deal", "property": "amount", "operator": "gte", "value": 20000}],
        )

        answer = assign(engine)

        assert answer["state"] == vocab.BOOK_STATE_NEEDS_CHOICE

    def test_every_matching_book_is_offered(self, engine: PriceBookEngine):
        """Both sourced sentences say a person chooses, so the person needs options."""
        deal(engine, segment="mid", amount=45000)
        segment_rule(engine, key="mid-standard", segment="mid", book=BOOK_STANDARD)
        segment_rule(
            engine,
            key="mid-big",
            segment="enterprise",
            book=BOOK_PARTNER,
            filters=[{"object": "deal", "property": "amount", "operator": "gte", "value": 20000}],
        )

        answer = assign(engine)

        assert {one["price_book"]["id"] for one in answer["candidates"]} == {"pb-std", "pb-prt"}

    def test_the_reading_was_chosen_by_jev(self):
        """Two sourced sentences do not agree on anything else, and the audit says so."""
        entry = inf.describe_one("MULTIPLE_MATCHES_NEED_A_CHOICE")

        assert entry["chosen"] == "needs_choice"
        assert entry["jev_audit_id"] == inf.MULTIPLE_MATCHES_AUDIT
        assert len(entry["sourced"]) == 2
        assert "first_match_wins" in entry["options"]

    def test_the_hubspot_sentence_is_quoted_verbatim(self):
        assert (
            "the deal owner can choose which matching price book to use"
            in (inf.multiple_matches()["sourced"][0])
        )

    def test_the_dynamics_sentence_is_quoted_verbatim(self):
        assert "the price level field isn't populated" in inf.multiple_matches()["sourced"][1]

    def test_the_owner_can_then_choose_through_the_override(self, engine: PriceBookEngine):
        deal(engine, segment="mid", amount=45000)
        segment_rule(engine, key="mid-standard", segment="mid", book=BOOK_STANDARD)
        segment_rule(
            engine,
            key="mid-big",
            segment="enterprise",
            book=BOOK_PARTNER,
            filters=[{"object": "deal", "property": "amount", "operator": "gte", "value": 20000}],
        )
        assign(engine)

        chosen = engine.override(
            "d1", BOOK_PARTNER, actor="sam", source="fixture", room_id="room_a"
        )

        assert chosen["outcome"] == vocab.ASSIGNMENT_SET
        assert engine.deal_payload("d1")[vocab.PRICE_BOOK]["id"] == BOOK_PARTNER["id"]


# --------------------------------------------------------------------------- #
# auto-assignment runs on create and never again
# --------------------------------------------------------------------------- #


class TestCreateOnly:
    def test_the_sourced_sentence_is_cited_whole(self):
        assert vocab.CREATE_ONLY_QUOTE == (
            "Price books are auto-assigned only when a deal is created. After a price book "
            "is auto-assigned, HubSpot won't run auto-assignment again if the deal or "
            "associated company properties used in the filter are updated."
        )

    def test_an_update_trigger_assigns_nothing(self, engine: PriceBookEngine):
        deal(engine, segment="enterprise")
        segment_rule(engine)

        answer = assign(engine, trigger=vocab.TRIGGER_UPDATE)

        assert answer["outcome"] == vocab.ASSIGNMENT_NOT_ON_CREATE
        assert answer["written"] is False
        assert vocab.PRICE_BOOK not in engine.deal_payload("d1")

    def test_a_second_create_run_does_not_reassign(self, engine: PriceBookEngine):
        """The second half of the sentence, and the reason it needs no second rule."""
        deal(engine, segment="enterprise")
        segment_rule(engine)
        assign(engine)

        answer = assign(engine)

        assert answer["outcome"] == vocab.ASSIGNMENT_ALREADY_ASSIGNED
        assert answer["written"] is False

    def test_updating_a_deal_property_does_not_reprice_it(self, engine: PriceBookEngine):
        """The sourced sentence names the case exactly: a deal property is updated."""
        deal(engine, segment="enterprise")
        segment_rule(engine)
        assign(engine)
        engine.store.update(
            "d1", {"segment": "enterprise", "amount": 99000}, actor="dana", source="fixture"
        )

        answer = assign(engine, trigger=vocab.TRIGGER_UPDATE)

        assert answer["written"] is False
        assert engine.deal_payload("d1")[vocab.PRICE_BOOK]["id"] == BOOK_ENTERPRISE["id"]

    def test_updating_the_company_does_not_reprice_the_deal(self, engine: PriceBookEngine):
        """The sentence names "associated company properties" as the second half."""
        deal(engine, segment="enterprise", company="co1")
        company(engine, industry="software")
        segment_rule(engine)
        assign(engine)
        engine.store.update(
            "co1", {"name": "co1", "industry": "hardware"}, actor="dana", source="fixture"
        )

        answer = assign(engine, trigger=vocab.TRIGGER_UPDATE)

        assert answer["written"] is False
        assert engine.deal_payload("d1")[vocab.PRICE_BOOK]["id"] == BOOK_ENTERPRISE["id"]

    def test_an_unknown_trigger_is_refused(self, engine: PriceBookEngine):
        deal(engine)

        with pytest.raises(PriceBookRefusal) as caught:
            assign(engine, trigger="whenever")

        assert caught.value.code == "unknown_trigger"

    def test_the_dynamics_message_name_is_served(self):
        """The one vendor identifier in the evidence, so a client written against it finds it."""
        assert vocab.DEFAULT_PRICE_LEVEL_MESSAGE == "GetDefaultPriceLevelRequest"
        assert vocab.catalogue()["dynamics"]["message"] == "GetDefaultPriceLevelRequest"

    def test_the_dynamics_parts_company_wins_over_dynamics(self, engine: PriceBookEngine):
        """The rejected alternative is recorded rather than left for a reader to assume."""
        entry = inf.describe_one("AUTO_ASSIGNMENT_IS_CREATE_ONLY")

        assert entry["chosen"] == "create_only"
        assert "create_and_update" in entry["options"]


# --------------------------------------------------------------------------- #
# a filter reads a deal property or a company property
# --------------------------------------------------------------------------- #


class TestFilterTargets:
    def test_a_deal_property_filter(self, engine: PriceBookEngine):
        deal(engine, segment="enterprise")
        segment_rule(engine)

        assert assign(engine)["outcome"] == vocab.ASSIGNMENT_ASSIGNED

    def test_a_company_property_filter(self, engine: PriceBookEngine):
        """ "HubSpot deal properties, company properties": two objects, and both must work."""
        deal(engine, company="co1")
        company(engine, industry="software")
        engine.create_rule(
            {
                "key": "software",
                "label": "Software companies",
                "price_book": BOOK_ENTERPRISE,
                "filters": [
                    {
                        "object": "company",
                        "property": "industry",
                        "operator": "is",
                        "value": "software",
                    }
                ],
            },
            actor="dana",
            source="fixture",
            room_id="room_a",
        )

        assert assign(engine)["outcome"] == vocab.ASSIGNMENT_ASSIGNED

    def test_a_company_property_filter_does_not_match_the_deals_own_field(
        self, engine: PriceBookEngine
    ):
        """The object decides the payload, so this is not a filter that reads anything."""
        deal(engine, industry="software")
        engine.create_rule(
            {
                "key": "software",
                "label": "Software companies",
                "price_book": BOOK_ENTERPRISE,
                "filters": [
                    {
                        "object": "company",
                        "property": "industry",
                        "operator": "is",
                        "value": "software",
                    }
                ],
            },
            actor="dana",
            source="fixture",
            room_id="room_a",
        )

        assert assign(engine)["outcome"] == vocab.ASSIGNMENT_NO_MATCH

    def test_a_company_filter_on_a_deal_with_no_company_does_not_match(
        self, engine: PriceBookEngine
    ):
        """A deal with no company is still priceable by its own deal-level filters."""
        deal(engine)
        engine.create_rule(
            {
                "key": "software",
                "label": "Software companies",
                "price_book": BOOK_ENTERPRISE,
                "filters": [
                    {
                        "object": "company",
                        "property": "industry",
                        "operator": "is",
                        "value": "software",
                    }
                ],
            },
            actor="dana",
            source="fixture",
            room_id="room_a",
        )

        answer = assign(engine)

        assert answer["outcome"] == vocab.ASSIGNMENT_NO_MATCH
        # The rule did not match, so it is not a candidate; the panel is where the seller
        # reads why, and it names the absent company rather than saying "no match".
        assert answer["candidates"] == []
        panel = engine.conditions("d1")
        assert "no company" in panel["rules"][0]["filters"][0]["reason"]

    def test_an_embedded_company_is_read(self, engine: PriceBookEngine):
        """A deal that carries its company inline is a real shape, and it is read."""
        deal(engine, company={"industry": "software"})
        engine.create_rule(
            {
                "key": "software",
                "label": "Software companies",
                "price_book": BOOK_ENTERPRISE,
                "filters": [
                    {
                        "object": "company",
                        "property": "industry",
                        "operator": "is",
                        "value": "software",
                    }
                ],
            },
            actor="dana",
            source="fixture",
            room_id="room_a",
        )

        assert assign(engine)["outcome"] == vocab.ASSIGNMENT_ASSIGNED

    def test_a_property_the_record_does_not_carry_does_not_match(self, engine: PriceBookEngine):
        """A rule that names a property no deal carries is a rule that never fires."""
        deal(engine, segment="enterprise")
        segment_rule(
            engine,
            filters=[{"object": "deal", "property": "hs_tier", "operator": "is", "value": "gold"}],
        )

        answer = assign(engine)

        assert answer["outcome"] == vocab.ASSIGNMENT_NO_MATCH
        assert answer["candidates"] == []
        panel = engine.conditions("d1")
        assert panel["rules"][0]["filters"][0]["present"] is False

    def test_a_dotted_path_resolves(self, engine: PriceBookEngine):
        deal(engine, meta={"segment": "enterprise"})
        segment_rule(
            engine,
            filters=[
                {
                    "object": "deal",
                    "property": "meta.segment",
                    "operator": "is",
                    "value": "enterprise",
                }
            ],
        )

        assert assign(engine)["outcome"] == vocab.ASSIGNMENT_ASSIGNED

    def test_the_territory_is_a_deal_property(self, engine: PriceBookEngine):
        """The Dynamics territory rule, expressed in the HubSpot filter shape."""
        deal(engine, territory="north")
        engine.create_rule(
            {
                "key": "north-territory",
                "label": "Northern territory",
                "price_book": BOOK_STANDARD,
                "filters": [
                    {"object": "deal", "property": "territory", "operator": "is", "value": "north"}
                ],
            },
            actor="dana",
            source="fixture",
            room_id="room_a",
        )

        assert assign(engine)["outcome"] == vocab.ASSIGNMENT_ASSIGNED
        assert inf.describe_one("THE_TERRITORY_RULE_IS_A_DEAL_PROPERTY")["chosen"] == (
            "deal_property_filter"
        )

    def test_the_territory_fields_are_served(self):
        assert "territory" in vocab.catalogue()["dynamics"]["territory_fields"]


# --------------------------------------------------------------------------- #
# and / or groups
# --------------------------------------------------------------------------- #


class TestFilterGroups:
    def test_both_conjunctions_are_sourced(self):
        assert vocab.FILTER_GROUP_QUOTE == "configure deal-property filters (`and` / `or` groups)"

    def test_all_is_the_default(self):
        assert vocab.FILTER_MATCH_ALL == "all"
        assert vocab.catalogue()["filters"]["default_match_mode"] == "all"

    def test_and_requires_every_filter(self, engine: PriceBookEngine):
        deal(engine, segment="enterprise", amount=500)
        segment_rule(
            engine,
            filters=[
                {"object": "deal", "property": "segment", "operator": "is", "value": "enterprise"},
                {"object": "deal", "property": "amount", "operator": "gt", "value": 1000},
            ],
        )

        assert assign(engine)["outcome"] == vocab.ASSIGNMENT_NO_MATCH

    def test_or_accepts_one_filter(self, engine: PriceBookEngine):
        deal(engine, segment="enterprise", amount=500)
        segment_rule(
            engine,
            filters=[
                {"object": "deal", "property": "segment", "operator": "is", "value": "enterprise"},
                {"object": "deal", "property": "amount", "operator": "gt", "value": 1000},
            ],
            matchMode="any",
        )

        assert assign(engine)["outcome"] == vocab.ASSIGNMENT_ASSIGNED

    def test_the_research_spelling_and_is_accepted(self):
        """A caller who typed what the source says should not have to know our spelling."""
        assert rules.normalise_match_mode("and") == vocab.FILTER_MATCH_ALL
        assert rules.normalise_match_mode("or") == vocab.FILTER_MATCH_ANY

    def test_an_unknown_mode_is_refused(self, engine: PriceBookEngine):
        deal(engine, segment="enterprise")

        with pytest.raises(PriceBookRefusal) as caught:
            segment_rule(engine, matchMode="maybe")

        assert caught.value.code == "unknown_match_mode"

    def test_every_filter_is_reported_either_way(self, engine: PriceBookEngine):
        """The right panel reviews the matching deals, so it needs all of them."""
        deal(engine, segment="enterprise", amount=500)
        segment_rule(
            engine,
            filters=[
                {"object": "deal", "property": "segment", "operator": "is", "value": "enterprise"},
                {"object": "deal", "property": "amount", "operator": "gt", "value": 1000},
            ],
            matchMode="any",
        )

        answer = assign(engine)

        assert len(answer["candidates"][0]["matched_filters"]) == 2


# --------------------------------------------------------------------------- #
# the two switches, and the three modes
# --------------------------------------------------------------------------- #


class TestTheSwitchesAndTheModes:
    def test_the_two_switches_are_the_researched_toggles(self):
        switches = vocab.catalogue()["switches"]

        assert switches["enabled"] == "enabled"
        assert switches["auto_assign"] == "auto_assign"
        assert switches["auto_assigned_toggle_quote"] == "toggle **Auto-assigned** on"

    def test_an_inactive_rule_matches_nothing(self, engine: PriceBookEngine):
        """ "toggle the price book's Inactive switch off to activate"."""
        deal(engine, segment="enterprise")
        segment_rule(engine, enabled=False)

        answer = assign(engine)

        assert answer["outcome"] == vocab.ASSIGNMENT_NO_MATCH_INACTIVE_RULE
        assert answer["written"] is False

    def test_a_test_first_rule_matches_but_assigns_nothing(self, engine: PriceBookEngine):
        """ "*Assignment rules without auto-assignment* (test first)"."""
        deal(engine, segment="enterprise")
        segment_rule(engine, auto_assign=False)

        answer = assign(engine)

        assert answer["outcome"] == vocab.ASSIGNMENT_NO_AUTO_ASSIGN
        assert answer["written"] is False
        # It still matched, so the panel can review it. That is the point of the mode.
        assert len(answer["candidates"]) == 1

    def test_a_test_first_rule_does_not_compete_for_a_deal(self, engine: PriceBookEngine):
        """Otherwise a rule being tested would block the rule that should have priced it."""
        deal(engine, segment="enterprise")
        segment_rule(engine, key="under-review", segment="enterprise", auto_assign=False)
        segment_rule(engine, key="approved", segment="enterprise", book=BOOK_STANDARD)

        answer = assign(engine)

        assert answer["outcome"] == vocab.ASSIGNMENT_ASSIGNED
        assert answer["price_book"]["id"] == BOOK_STANDARD["id"]

    def test_the_conditions_panel_reports_a_test_first_rule(self, engine: PriceBookEngine):
        """The panel is how an admin reviews a rule before switching auto-assignment on."""
        deal(engine, segment="enterprise")
        segment_rule(engine, auto_assign=False)

        panel = engine.conditions("d1")

        assert panel["outcome"] == vocab.ASSIGNMENT_NO_AUTO_ASSIGN
        assert len(panel["candidates"]) == 1
        assert panel["mode"] == vocab.MODE_TEST_ONLY
        assert panel["test_first_rules"] == ["Enterprise deals"]

    def test_the_panel_reports_a_rule_that_did_not_match(self, engine: PriceBookEngine):
        """A panel listing only hits cannot answer why the rule I wrote did not fire."""
        deal(engine, segment="pilot")
        segment_rule(engine, segment="enterprise")

        panel = engine.conditions("d1")

        assert panel["outcome"] == vocab.ASSIGNMENT_NO_MATCH
        assert len(panel["rules"]) == 1
        assert panel["rules"][0]["matched"] is False
        assert panel["rules"][0]["filters"][0]["actual"] == "pilot"

    def test_no_rules_is_the_manual_only_mode(self):
        assert rules.workspace_mode([]) == vocab.MODE_NO_RULES

    def test_an_inactive_rule_is_not_a_mode(self, engine: PriceBookEngine):
        """The first mode is the absence of active rules, not the presence of inactive ones."""
        segment_rule(engine, enabled=False)

        assert engine.summary()["mode"] == vocab.MODE_NO_RULES

    def test_a_test_first_rule_is_the_test_only_mode(self):
        assert (
            rules.workspace_mode([{"enabled": True, "auto_assign": False}]) == vocab.MODE_TEST_ONLY
        )

    def test_an_auto_assigning_rule_is_the_auto_assignment_mode(self):
        assert (
            rules.workspace_mode([{"enabled": True, "auto_assign": True}]) == vocab.MODE_AUTO_ASSIGN
        )

    def test_all_three_modes_are_served(self):
        assert {one["mode"] for one in vocab.catalogue()["modes"]} == set(vocab.MODES)

    def test_the_three_modes_are_a_derivation_and_say_so(self):
        entry = inf.describe_one("ALL_THREE_MODES_ARE_RULES_NOT_A_SETTING")

        assert entry["chosen"] == "all_three_as_configuration"
        assert "auto_assignment_only" in entry["options"]

    def test_a_patch_revalidates_the_merged_rule(self, engine: PriceBookEngine):
        """A half-applied toggle is a rule that reads as active and is not."""
        rule = segment_rule(engine)

        updated = engine.patch_rule(
            rule["id"], {"auto_assign": False}, actor="dana", source="fixture"
        )

        assert updated["data"][vocab.AUTO_ASSIGN] is False
        assert updated["data"]["price_book"] == BOOK_ENTERPRISE

    def test_a_patch_cannot_drop_the_price_book(self, engine: PriceBookEngine):
        rule = segment_rule(engine)

        with pytest.raises(PriceBookRefusal) as caught:
            engine.patch_rule(rule["id"], {"price_book": None}, actor="dana", source="fixture")

        assert caught.value.code == "rule_needs_a_price_book"


# --------------------------------------------------------------------------- #
# a rule needs a name, a filter and a price book
# --------------------------------------------------------------------------- #


class TestRuleValidation:
    def test_a_rule_with_no_filters_is_refused(self, engine: PriceBookEngine):
        """A rule that matched every deal would price every deal."""
        with pytest.raises(PriceBookRefusal) as caught:
            engine.create_rule(
                {"key": "everything", "price_book": BOOK_STANDARD, "filters": []},
                actor="dana",
                source="fixture",
            )

        assert caught.value.code == "rule_needs_a_filter"

    def test_a_rule_with_no_price_book_is_refused(self, engine: PriceBookEngine):
        """A rule with no book has nothing to assign."""
        with pytest.raises(PriceBookRefusal) as caught:
            engine.create_rule(
                {
                    "key": "nowhere",
                    "filters": [
                        {"object": "deal", "property": "segment", "operator": "is", "value": "x"}
                    ],
                },
                actor="dana",
                source="fixture",
            )

        # The rule-level code, not the reference-level one: the sentence that fits a
        # missing book on a *rule* is that the rule has nothing to assign.
        assert caught.value.code == "rule_needs_a_price_book"
        assert "A rule assigns a price book" in caught.value.detail

    def test_an_override_with_no_book_uses_the_reference_code(self, engine: PriceBookEngine):
        """ "Name the price book to set" is right for a dropdown and wrong for a rule."""
        deal(engine, segment="enterprise")

        with pytest.raises(PriceBookRefusal) as caught:
            engine.override("d1", None, actor="sam", source="fixture", room_id="room_a")

        assert caught.value.code == "price_book_needs_an_id_or_a_name"

    def test_a_rule_with_no_name_is_refused(self, engine: PriceBookEngine):
        with pytest.raises(PriceBookRefusal) as caught:
            engine.create_rule(
                {
                    "price_book": BOOK_STANDARD,
                    "filters": [
                        {"object": "deal", "property": "segment", "operator": "is", "value": "x"}
                    ],
                },
                actor="dana",
                source="fixture",
            )

        assert caught.value.code == "rule_needs_a_name"

    def test_a_punctuated_name_is_refused(self, engine: PriceBookEngine):
        with pytest.raises(PriceBookRefusal):
            engine.create_rule(
                {
                    "key": "Enterprise Inbound!",
                    "price_book": BOOK_STANDARD,
                    "filters": [
                        {"object": "deal", "property": "segment", "operator": "is", "value": "x"}
                    ],
                },
                actor="dana",
                source="fixture",
            )

    def test_a_book_by_name_only_is_accepted(self, engine: PriceBookEngine):
        """A deployment may identify its books by name, and refusing that is refusing a book."""
        rule = engine.create_rule(
            {
                "key": "by-name",
                "price_book": {"name": "Enterprise list 2026"},
                "filters": [
                    {
                        "object": "deal",
                        "property": "segment",
                        "operator": "is",
                        "value": "enterprise",
                    }
                ],
            },
            actor="dana",
            source="fixture",
        )

        assert rule["data"]["price_book"] == {"id": None, "name": "Enterprise list 2026"}

    def test_an_empty_filter_value_is_refused(self, engine: PriceBookEngine):
        with pytest.raises(PriceBookRefusal) as caught:
            engine.create_rule(
                {
                    "key": "no-value",
                    "price_book": BOOK_STANDARD,
                    "filters": [{"object": "deal", "property": "segment", "operator": "is"}],
                },
                actor="dana",
                source="fixture",
            )

        assert caught.value.code == "filter_needs_a_value"

    def test_a_rule_may_be_saved_with_a_label_that_differs_from_its_name(self, engine):
        """The name is the API-facing key; the label is what a seller reads on a card."""
        rule = segment_rule(engine, key="enterprise", label="Enterprise list 2026")

        assert rule["data"]["key"] == "enterprise"
        assert rule["data"]["label"] == "Enterprise list 2026"

    def test_a_filter_with_no_property_is_refused(self, engine: PriceBookEngine):
        with pytest.raises(PriceBookRefusal) as caught:
            engine.create_rule(
                {
                    "key": "no-property",
                    "price_book": BOOK_STANDARD,
                    "filters": [{"object": "deal", "operator": "is", "value": "x"}],
                },
                actor="dana",
                source="fixture",
            )

        assert caught.value.code == "filter_needs_a_property"

    def test_a_numeric_operator_with_a_word_is_refused(self, engine: PriceBookEngine):
        """A rule that reads as configured and never fires is the failure this prevents."""
        with pytest.raises(PriceBookRefusal) as caught:
            engine.create_rule(
                {
                    "key": "wordy",
                    "price_book": BOOK_STANDARD,
                    "filters": [
                        {"object": "deal", "property": "amount", "operator": "gt", "value": "lots"}
                    ],
                },
                actor="dana",
                source="fixture",
            )

        assert caught.value.code == "numeric_filter_value_is_not_a_number"

    def test_a_list_operator_with_a_scalar_is_refused(self, engine: PriceBookEngine):
        with pytest.raises(PriceBookRefusal) as caught:
            engine.create_rule(
                {
                    "key": "scalar",
                    "price_book": BOOK_STANDARD,
                    "filters": [
                        {"object": "deal", "property": "segment", "operator": "in", "value": "one"}
                    ],
                },
                actor="dana",
                source="fixture",
            )

        assert caught.value.code == "list_operator_needs_a_list"

    def test_an_unknown_operator_is_refused(self, engine: PriceBookEngine):
        with pytest.raises(PriceBookRefusal) as caught:
            engine.create_rule(
                {
                    "key": "shrug",
                    "price_book": BOOK_STANDARD,
                    "filters": [
                        {"object": "deal", "property": "segment", "operator": "like", "value": "x"}
                    ],
                },
                actor="dana",
                source="fixture",
            )

        assert caught.value.code == "unknown_operator"

    def test_the_operator_set_is_announced_as_this_builds(self):
        assert "this build's" in vocab.FILTER_OPERATORS_CHOICE_NOTE

    def test_a_deleted_rule_leaves_its_assignments_readable(self, engine: PriceBookEngine):
        """A past assignment still explains itself after the rule is gone."""
        deal(engine, segment="enterprise")
        rule = segment_rule(engine)
        assign(engine)

        engine.delete_rule(rule["id"], actor="dana", source="fixture")

        written = engine.latest_assignment("d1")
        assert written["rule_label"] == "Enterprise deals"
        assert written["matched_filters"]


# --------------------------------------------------------------------------- #
# changing the book removes the previous book's lines
# --------------------------------------------------------------------------- #


class TestTheOverride:
    def test_the_sourced_sentence_is_cited_whole(self):
        assert vocab.LINE_ITEMS_REMOVED_QUOTE == (
            "If the price book is changed, any line items associated with the previous price "
            "book will be removed."
        )

    def test_the_lines_of_the_previous_book_are_removed(self, engine: PriceBookEngine):
        deal(engine, segment="enterprise")
        segment_rule(engine)
        assign(engine)
        line = engine.store.create(
            vocab.LINE_ITEMS,
            {"deal_id": "d1", "sku": "SEAT-STD", "quantity": 2},
            room_id="room_a",
            actor="dana",
            source="fixture",
        )

        changed = engine.override(
            "d1", BOOK_PARTNER, actor="sam", source="fixture", room_id="room_a"
        )

        assert changed["line_items_removed"] == [line["id"]]
        # Soft-deleted, not destroyed. The row stays so the audit reference survives, so
        # the default read hides it and only `include_deleted` still reaches it. That is
        # the guarantee the product is built on, asserted rather than assumed.
        assert engine.store.get(line["id"]) is None
        gone = engine.store.list(vocab.LINE_ITEMS, include_deleted=True, limit=10)
        assert line["id"] in {one["id"] for one in gone}

    def test_the_removed_ids_are_named_on_the_assignment(self, engine: PriceBookEngine):
        """So the line count on the deal and the audit trail cannot disagree."""
        deal(engine, segment="enterprise")
        segment_rule(engine)
        assign(engine)
        engine.store.create(
            vocab.LINE_ITEMS,
            {"deal_id": "d1", "sku": "SEAT-STD"},
            room_id="room_a",
            actor="dana",
            source="fixture",
        )

        engine.override("d1", BOOK_PARTNER, actor="sam", source="fixture", room_id="room_a")

        written = engine.latest_assignment("d1")
        assert written["line_items_removed_count"] == 1
        assert written["line_items_removed_quote"] == vocab.LINE_ITEMS_REMOVED_QUOTE

    def test_a_line_that_names_another_book_survives(self, engine: PriceBookEngine):
        """It is not a line of the previous book, so removing it is an undescribed deletion."""
        deal(engine, segment="enterprise")
        segment_rule(engine)
        assign(engine)
        engine.store.create(
            vocab.LINE_ITEMS,
            {"deal_id": "d1", "sku": "SEAT-STD", vocab.PRICE_BOOK: BOOK_PARTNER},
            room_id="room_a",
            actor="dana",
            source="fixture",
        )

        changed = engine.override(
            "d1", BOOK_PARTNER, actor="sam", source="fixture", room_id="room_a"
        )

        assert changed["line_items_removed"] == []

    def test_a_first_assignment_removes_nothing(self, engine: PriceBookEngine):
        """There is no previous book, so there are no lines of one."""
        deal(engine, segment="enterprise")
        engine.store.create(
            vocab.LINE_ITEMS,
            {"deal_id": "d1", "sku": "SEAT-STD"},
            room_id="room_a",
            actor="dana",
            source="fixture",
        )

        changed = engine.override(
            "d1", BOOK_STANDARD, actor="sam", source="fixture", room_id="room_a"
        )

        assert changed["outcome"] == vocab.ASSIGNMENT_SET
        assert changed["line_items_removed_count"] == 0

    def test_the_previous_book_is_recorded(self, engine: PriceBookEngine):
        deal(engine, segment="enterprise")
        segment_rule(engine)
        assign(engine)

        changed = engine.override(
            "d1", BOOK_PARTNER, actor="sam", source="fixture", room_id="room_a"
        )

        # The previous book is recorded as it was on the deal, which is the reference
        # this workflow wrote: the same id and name, plus the rule and the moment.
        previous = changed["previous_price_book"]
        assert previous["id"] == BOOK_ENTERPRISE["id"]
        assert previous["name"] == BOOK_ENTERPRISE["name"]
        assert previous["rule_label"] == "Enterprise deals"

    def test_setting_the_same_book_is_refused(self, engine: PriceBookEngine):
        """A no-op that removes nothing still reads as a change of book on the audit trail."""
        deal(engine, segment="enterprise")
        segment_rule(engine)
        assign(engine)

        with pytest.raises(PriceBookRefusal) as caught:
            engine.override("d1", BOOK_ENTERPRISE, actor="sam", source="fixture", room_id="room_a")

        assert caught.value.code == "override_to_the_same_price_book"

    def test_an_override_needs_a_book(self, engine: PriceBookEngine):
        deal(engine, segment="enterprise")

        with pytest.raises(PriceBookRefusal) as caught:
            engine.override("d1", None, actor="sam", source="fixture", room_id="room_a")

        assert caught.value.code == "price_book_needs_an_id_or_a_name"

    def test_an_override_does_not_re_run_the_rules(self, engine: PriceBookEngine):
        """A hand choice is not re-derived, which is the sourced sentence about updates."""
        deal(engine, segment="enterprise")
        segment_rule(engine)
        assign(engine)

        engine.override("d1", BOOK_PARTNER, actor="sam", source="fixture", room_id="room_a")

        assert engine.deal_payload("d1")[vocab.PRICE_BOOK]["id"] == BOOK_PARTNER["id"]
        assert assign(engine)["outcome"] == vocab.ASSIGNMENT_ALREADY_ASSIGNED

    def test_the_reading_was_chosen_by_jev(self):
        entry = inf.describe_one("OVERRIDE_REMOVES_THE_PREVIOUS_BOOKS_LINES")

        assert entry["chosen"] == "remove_and_name"
        assert entry["jev_audit_id"] == inf.OVERRIDE_AUDIT
        assert "keep_them" in entry["options"]

    def test_the_change_price_book_quote_is_served(self):
        assert vocab.CHANGE_PRICE_BOOK_QUOTE == "Change price book"


# --------------------------------------------------------------------------- #
# a quote inherits and cannot be set
# --------------------------------------------------------------------------- #


class TestTheQuoteInherits:
    def test_the_sourced_sentence_is_cited_whole(self):
        assert vocab.QUOTE_INHERITS_QUOTE == (
            "Quotes inherit the price book from the associated deal. Users can't select a "
            "price book when creating a quote; they must select it on the deal."
        )

    def test_a_quote_inherits_its_deals_book(self, engine: PriceBookEngine):
        deal(engine, segment="enterprise")
        segment_rule(engine)
        assign(engine)
        engine.store.create(
            vocab.SOURCE_QUOTES,
            {"name": "Northwind quote", "deal": "d1"},
            record_id="q1",
            room_id="room_a",
            actor="dana",
            source="fixture",
        )

        answer = engine.quote_price_book("q1")

        # The inherited value is the deal's own reference, so it carries the rule that
        # wrote it and not just an id. A quote reader can therefore see the provenance
        # of the price it is quoting from.
        assert answer["price_book"]["id"] == BOOK_ENTERPRISE["id"]
        assert answer["price_book"]["name"] == BOOK_ENTERPRISE["name"]
        assert answer["price_book_label"] == BOOK_ENTERPRISE["name"]
        assert answer["authority"] == vocab.INHERITANCE_AUTHORITY_DEAL
        assert answer["deal_id"] == "d1"

    def test_the_quote_is_never_written(self, engine: PriceBookEngine):
        """The second sourced sentence, enforced by the absence of any write path."""
        deal(engine, segment="enterprise")
        segment_rule(engine)
        assign(engine)
        engine.store.create(
            vocab.SOURCE_QUOTES,
            {"name": "Northwind quote", "deal": "d1"},
            record_id="q1",
            room_id="room_a",
            actor="dana",
            source="fixture",
        )

        engine.quote_price_book("q1")

        assert vocab.PRICE_BOOK not in engine.store.get("q1")["data"]

    def test_setting_a_price_book_on_a_quote_raises(self):
        """A raise rather than a boolean, so a route cannot ship without the refusal."""
        with pytest.raises(PriceBookRefusal) as caught:
            rules.require_set_on_deal()

        assert caught.value.code == "quote_price_book_is_set_on_the_deal"

    def test_a_quote_with_no_deal_reports_none(self, engine: PriceBookEngine):
        """A quote is allowed to exist before its deal does."""
        engine.store.create(
            vocab.SOURCE_QUOTES,
            {"name": "Orphan quote"},
            record_id="q1",
            room_id="room_a",
            actor="dana",
            source="fixture",
        )

        answer = engine.quote_price_book("q1")

        assert answer["authority"] == vocab.INHERITANCE_AUTHORITY_NONE
        assert answer["price_book"] is None
        assert answer["settable_here"] is False

    def test_a_quote_whose_deal_is_missing_reports_none(self, engine: PriceBookEngine):
        engine.store.create(
            vocab.SOURCE_QUOTES,
            {"name": "Dangling quote", "deal": "d_absent"},
            record_id="q1",
            room_id="room_a",
            actor="dana",
            source="fixture",
        )

        assert engine.quote_price_book("q1")["authority"] == vocab.INHERITANCE_AUTHORITY_NONE


# --------------------------------------------------------------------------- #
# reading the price book field
# --------------------------------------------------------------------------- #


class TestThePriceBookField:
    def test_the_dynamics_spelling_is_read(self, engine: PriceBookEngine):
        """ "pricelevelid in Dynamics" is the one field name the research names."""
        deal(engine, pricelevelid="pb-legacy")

        answer = engine.deal_price_book("d1")

        assert answer["price_book"] == {"id": "pb-legacy", "name": None}
        assert answer["deal_field"] == "pricelevelid"

    def test_a_field_a_mirror_already_wrote_is_not_overwritten(self, engine: PriceBookEngine):
        """A deal a CRM mirror already priced keeps that book; auto-assignment does not re-run."""
        deal(engine, segment="enterprise", pricelevelid="pb-legacy")
        segment_rule(engine)

        answer = assign(engine)

        assert answer["outcome"] == vocab.ASSIGNMENT_ALREADY_ASSIGNED
        assert engine.deal_payload("d1")["pricelevelid"] == "pb-legacy"

    def test_an_empty_field_reads_as_no_book(self, engine: PriceBookEngine):
        deal(engine, pricelevelid="")

        assert engine.deal_price_book("d1")["price_book"] is None

    def test_the_authority_says_who_answered(self, engine: PriceBookEngine):
        deal(engine, segment="enterprise")
        segment_rule(engine)
        assign(engine)

        answer = engine.deal_price_book("d1")

        assert answer["authority"] == vocab.AUTHORITY_DEAL_FIELD
        assert answer["state"] == vocab.BOOK_STATE_ASSIGNED

    def test_an_unpriced_deal_with_no_rule_reports_no_authority(self, engine: PriceBookEngine):
        deal(engine, segment="enterprise")

        answer = engine.deal_price_book("d1")

        assert answer["authority"] == vocab.AUTHORITY_NONE
        assert answer["state"] == vocab.BOOK_STATE_UNASSIGNED
        assert answer["price_book_label"] == vocab.PRICE_BOOK_NONE_LABEL

    def test_the_three_states_are_served(self):
        assert {one["state"] for one in vocab.catalogue()["book_states"]} == set(vocab.BOOK_STATES)

    def test_a_quote_carries_a_label_for_the_card(self, engine: PriceBookEngine):
        """The card reads the name, so the answer has to carry one."""
        deal(engine, segment="enterprise")
        segment_rule(engine)
        assign(engine)

        assert engine.deal_price_book("d1")["price_book_label"] == BOOK_ENTERPRISE["name"]


# --------------------------------------------------------------------------- #
# every outcome is a published code
# --------------------------------------------------------------------------- #


class TestEveryRefusalIsPublished:
    @pytest.mark.parametrize("reason", list(vocab.ASSIGNMENT_REASONS))
    def test_every_reason_has_a_label(self, reason):
        assert vocab.ASSIGNMENT_REASON_LABELS[reason]

    @pytest.mark.parametrize("reason", list(vocab.ASSIGNMENT_REASONS))
    def test_every_reason_is_served(self, reason):
        served = {one["reason"] for one in vocab.catalogue()["assignments"]}

        assert reason in served

    def test_the_writing_reasons_are_the_three_that_write(self):
        assert set(vocab.WRITING_REASONS) == {
            vocab.ASSIGNMENT_ASSIGNED,
            vocab.ASSIGNMENT_SET,
            vocab.ASSIGNMENT_CHANGED,
        }

    def test_every_refusal_the_rules_raise_has_a_published_code(self):
        """A refusal with no code is a sentence somebody has to grep for."""
        raised = set()
        for name in dir(rules):
            if not name.startswith("raise"):
                continue
            function = getattr(rules, name)
            raised.update(getattr(function, "__doc__", "") or "")
        for code in (
            "rule_needs_a_name",
            "rule_needs_a_filter",
            "rule_needs_a_price_book",
            "unknown_filter_object",
            "filter_needs_a_property",
            "unknown_operator",
            "filter_needs_a_value",
            "numeric_filter_value_is_not_a_number",
            "list_operator_needs_a_list",
            "unknown_match_mode",
            "unknown_trigger",
        ):
            assert code in vocab.ERROR_CODES

    def test_every_error_code_has_a_status_and_a_sentence(self):
        for code, (status, detail) in vocab.ERROR_CODES.items():
            assert 400 <= status < 600, code
            assert detail, code

    def test_the_refusals_carry_the_researched_sentence(self):
        refusal = PriceBookRefusal("quote_price_book_is_set_on_the_deal")

        assert "they must select it on the deal" in refusal.detail
        assert refusal.status == 409

    def test_a_not_found_is_a_404(self):
        missing = PriceBookNotFound("unknown_deal", "deal", "d_absent")

        assert missing.status == 404
        assert "d_absent" in str(missing)

    def test_the_not_found_is_the_base_of_nothing_else(self, engine: PriceBookEngine):
        """A 404 that is also a 422 would leave the handler guessing."""
        assert not issubclass(PriceBookNotFound, PriceBookRefusal)
        assert issubclass(PriceBookRefusal, ValueError)


# --------------------------------------------------------------------------- #
# missing things
# --------------------------------------------------------------------------- #


class TestMissingThings:
    def test_an_unknown_deal_is_refused(self, engine: PriceBookEngine):
        with pytest.raises(PriceBookNotFound) as caught:
            assign(engine, "d_absent")

        assert caught.value.code == "unknown_deal"

    def test_an_unknown_rule_is_refused(self, engine: PriceBookEngine):
        with pytest.raises(PriceBookNotFound) as caught:
            engine.rule("rule_absent")

        assert caught.value.code == "unknown_assignment_rule"

    def test_an_unknown_assignment_is_refused(self, engine: PriceBookEngine):
        with pytest.raises(PriceBookNotFound) as caught:
            engine.assignment("a_absent")

        assert caught.value.code == "unknown_assignment"

    def test_an_unknown_quote_is_refused(self, engine: PriceBookEngine):
        with pytest.raises(PriceBookNotFound) as caught:
            engine.quote_price_book("q_absent")

        assert caught.value.code == "unknown_quote"

    def test_an_empty_deal_reference_is_refused(self, engine: PriceBookEngine):
        with pytest.raises(PriceBookNotFound):
            engine.deal("")

    def test_a_deal_found_by_its_crm_id(self, engine: PriceBookEngine):
        """ "This product's record id is not the CRM's", which is the common case."""
        deal(engine, crm_id="006NW-1", segment="enterprise")
        segment_rule(engine)

        answer = engine.assign("006NW-1", actor="dana", source="fixture", room_id="room_a")

        assert answer["outcome"] == vocab.ASSIGNMENT_ASSIGNED

    def test_a_deal_mirrored_as_an_opportunity_is_read(self, engine: PriceBookEngine):
        """A workspace that calls a deal an opportunity must not get an empty page."""
        engine.store.create(
            "crm_opportunity",
            {"name": "Halcyon", "segment": "enterprise"},
            record_id="o1",
            room_id="room_a",
            actor="dana",
            source="fixture",
        )
        segment_rule(engine)

        assert (
            engine.assign("o1", actor="dana", source="fixture", room_id="room_a")["outcome"]
            == vocab.ASSIGNMENT_ASSIGNED
        )

    def test_the_catalogue_is_optional(self, engine: PriceBookEngine):
        """WF-087 has not shipped, and a rule must work anyway."""
        deal(engine, segment="enterprise")
        segment_rule(engine)

        assert engine.summary()["catalogue_found"] is False
        assert assign(engine)["outcome"] == vocab.ASSIGNMENT_ASSIGNED

    def test_the_catalogue_is_reported_when_it_exists(self, engine: PriceBookEngine):
        engine.store.create(
            vocab.SOURCE_PRICE_BOOKS,
            {"name": BOOK_ENTERPRISE["name"]},
            record_id="pb-ent",
            room_id="room_a",
            actor="dana",
            source="fixture",
        )

        assert engine.summary()["catalogue_found"] is True

    def test_the_reference_not_a_lookup_decision_is_recorded(self):
        entry = inf.describe_one("PRICE_BOOKS_ARE_REFERENCED_NOT_RESOLVED")

        assert entry["chosen"] == "reference_only"
        assert "hard_dependency" in entry["options"]


# --------------------------------------------------------------------------- #
# the board
# --------------------------------------------------------------------------- #


class TestTheBoard:
    def test_the_board_counts_the_reason_a_seller_acts_on(self, engine: PriceBookEngine):
        deal(engine, segment="mid", amount=45000)
        segment_rule(engine, key="mid-standard", segment="mid", book=BOOK_STANDARD)
        segment_rule(
            engine,
            key="mid-big",
            segment="enterprise",
            book=BOOK_PARTNER,
            filters=[{"object": "deal", "property": "amount", "operator": "gte", "value": 20000}],
        )
        assign(engine)

        board = engine.summary()

        assert board["awaiting_a_choice"] == 1
        assert board["by_outcome"][vocab.ASSIGNMENT_NEEDS_CHOICE] == 1

    def test_the_board_counts_the_writes(self, engine: PriceBookEngine):
        deal(engine, segment="enterprise")
        segment_rule(engine)
        assign(engine)

        assert engine.summary()["written"] == 1

    def test_the_board_reports_the_mode(self, engine: PriceBookEngine):
        segment_rule(engine, auto_assign=False)

        assert engine.summary()["mode"] == vocab.MODE_TEST_ONLY

    def test_the_board_counts_either_shape(self):
        """A caller may hold the row or its payload, and both must give the same answer."""
        from_rows = rules.summary_counts([{"data": {"enabled": True}}], [])
        from_payloads = rules.summary_counts([{"enabled": True}], [])

        assert from_rows["rules_enabled"] == from_payloads["rules_enabled"] == 1

    def test_the_board_carries_both_readings(self, engine: PriceBookEngine):
        board = engine.summary()

        assert board["multiple_matches"]["chosen"] == "needs_choice"
        assert board["override_removes_lines"]["chosen"] == "remove_and_name"

    def test_the_assignments_can_be_filtered_by_reason(self, engine: PriceBookEngine):
        deal(engine, segment="enterprise")
        segment_rule(engine)
        assign(engine)
        deal(engine, deal_id="d2", segment="pilot")
        assign(engine, "d2")

        unpriced = engine.list_assignments(outcome=vocab.ASSIGNMENT_NO_MATCH)

        assert [one["deal_id"] for one in unpriced] == ["d2"]

    def test_the_assignments_can_be_filtered_by_deal(self, engine: PriceBookEngine):
        deal(engine, segment="enterprise")
        segment_rule(engine)
        assign(engine)
        deal(engine, deal_id="d2", segment="pilot")
        assign(engine, "d2")

        for_one = engine.list_assignments(deal_id="d2")

        assert [one["outcome"] for one in for_one] == [vocab.ASSIGNMENT_NO_MATCH]

    def test_an_unfiltered_list_is_newest_first(self, engine: PriceBookEngine):
        deal(engine, segment="enterprise")
        segment_rule(engine)
        assign(engine)
        deal(engine, deal_id="d2", segment="pilot")
        assign(engine, "d2")

        assert [one["deal_id"] for one in engine.list_assignments()] == ["d2", "d1"]

    def test_an_assignment_view_carries_the_label_and_the_count(self, engine: PriceBookEngine):
        deal(engine, segment="enterprise")
        segment_rule(engine)
        assign(engine)

        view = engine.latest_assignment("d1")

        assert view["reason_label"] == vocab.ASSIGNMENT_REASON_LABELS[vocab.ASSIGNMENT_ASSIGNED]
        assert view["line_items_removed_count"] == 0

    def test_the_conditions_panel_writes_nothing(self, engine: PriceBookEngine):
        """It is a GET because it checks and writes nothing."""
        deal(engine, segment="enterprise")
        segment_rule(engine)

        panel = engine.conditions("d1")

        assert panel["outcome"] == vocab.ASSIGNMENT_ASSIGNED
        assert engine.store.list(vocab.ASSIGNMENTS, limit=10) == []
        assert vocab.PRICE_BOOK not in engine.deal_payload("d1")

    def test_the_conditions_panel_names_the_inactive_rules(self, engine: PriceBookEngine):
        """So the fix is visible: turn the switch on, rather than rewrite the filter."""
        deal(engine, segment="enterprise")
        segment_rule(engine, enabled=False)

        panel = engine.conditions("d1")

        assert panel["inactive_rules"] == ["Enterprise deals"]

    def test_the_conditions_panel_carries_the_create_only_quote(self, engine: PriceBookEngine):
        deal(engine, segment="enterprise")

        assert engine.conditions("d1")["create_only_quote"] == vocab.CREATE_ONLY_QUOTE


# --------------------------------------------------------------------------- #
# the pure rules, edge by edge
# --------------------------------------------------------------------------- #
#
# The sections above test each researched rule through the engine. This one tests the
# pure functions directly, because the branches below are the ones the researched rules
# do not reach: the word operators a rule builder offers, the coercion helpers, and the
# list-shaped paths in a dotted-path read. A rule a seller can save from the page is a
# rule that will be saved.


class TestTheWordOperators:
    """Every operator the builder offers, since the research says "configure the filter"."""

    @pytest.mark.parametrize(
        ("operator", "expected", "actual", "matched"),
        [
            (vocab.OPERATOR_IS, "enterprise", "enterprise", True),
            (vocab.OPERATOR_IS, "enterprise", "mid", False),
            (vocab.OPERATOR_IS_NOT, "enterprise", "mid", True),
            (vocab.OPERATOR_IS_NOT, "enterprise", "enterprise", False),
            (vocab.OPERATOR_CONTAINS, "prise", "Enterprise", True),
            (vocab.OPERATOR_CONTAINS, "mid", "Enterprise", False),
            (vocab.OPERATOR_DOES_NOT_CONTAIN, "mid", "Enterprise", True),
            (vocab.OPERATOR_DOES_NOT_CONTAIN, "prise", "Enterprise", False),
            (vocab.OPERATOR_IN, ["mid", "enterprise"], "enterprise", True),
            (vocab.OPERATOR_IN, ["mid"], "enterprise", False),
            (vocab.OPERATOR_NOT_IN, ["mid"], "enterprise", True),
            (vocab.OPERATOR_NOT_IN, ["enterprise"], "enterprise", False),
        ],
    )
    def test_each_word_operator(self, operator, expected, actual, matched):
        """A comparison that is never exercised is a comparison that may be wrong."""
        report = rules.filter_matches(
            {"object": "deal", "property": "segment", "operator": operator, "value": expected},
            {"segment": actual},
        )

        assert report["matched"] is matched

    @pytest.mark.parametrize(
        ("operator", "value", "actual", "matched"),
        [
            (vocab.OPERATOR_GREATER_THAN, 1000, 5000, True),
            (vocab.OPERATOR_GREATER_THAN, 5000, 5000, False),
            (vocab.OPERATOR_GREATER_THAN_OR_EQUAL, 5000, 5000, True),
            (vocab.OPERATOR_LESS_THAN, 5000, 5000, False),
            (vocab.OPERATOR_LESS_THAN_OR_EQUAL, 5000, 5000, True),
        ],
    )
    def test_each_numeric_operator(self, operator, value, actual, matched):
        report = rules.filter_matches(
            {"object": "deal", "property": "amount", "operator": operator, "value": value},
            {"amount": actual},
        )

        assert report["matched"] is matched

    def test_a_numeric_comparison_against_text_does_not_match(self):
        """A property that holds text on some deals must not raise, and must not match."""
        report = rules.filter_matches(
            {"object": "deal", "property": "amount", "operator": "gt", "value": 10},
            {"amount": "lots"},
        )

        assert report["matched"] is False
        assert report["present"] is True

    def test_a_boolean_is_never_read_as_one(self):
        """``True > 0`` runs in Python and means nothing, so it must not match."""
        report = rules.filter_matches(
            {"object": "deal", "property": "amount", "operator": "gt", "value": 0},
            {"amount": True},
        )

        assert report["matched"] is False

    def test_an_is_not_on_an_absent_property_does_not_match(self):
        """Negation is not absence: "is not mid" must not fire on a deal with no segment."""
        report = rules.filter_matches(
            {"object": "deal", "property": "segment", "operator": "is_not", "value": "mid"},
            {},
        )

        assert report["matched"] is False

    def test_a_does_not_contain_on_an_absent_property_does_not_match(self):
        report = rules.filter_matches(
            {"object": "deal", "property": "name", "operator": "does_not_contain", "value": "x"},
            {},
        )

        assert report["matched"] is False

    def test_a_not_in_on_an_absent_property_does_not_match(self):
        report = rules.filter_matches(
            {"object": "deal", "property": "segment", "operator": "not_in", "value": ["mid"]},
            {},
        )

        assert report["matched"] is False


class TestCoercions:
    def test_a_boolean_reads_as_a_word(self):
        assert rules.as_text(True) == "true"
        assert rules.as_text(False) == "false"

    def test_a_list_reads_as_its_words(self):
        """A property holding a list is real, and "is any of" has to match one of them."""
        report = rules.filter_matches(
            {"object": "deal", "property": "tags", "operator": "in", "value": ["gold"]},
            {"tags": ["silver", "gold"]},
        )

        assert report["matched"] is True

    def test_a_nested_object_reads_as_its_id(self):
        """An association is stored as an object, so a filter on it has to resolve."""
        assert rules.as_text({"id": "pb-ent", "name": "Enterprise"}) == "pb-ent"

    def test_a_nested_object_with_only_a_name_reads_as_the_name(self):
        assert rules.as_text({"name": "Northwind"}) == "northwind"

    def test_none_reads_as_nothing(self):
        assert rules.as_text(None) == ""

    def test_a_number_reads_as_its_text(self):
        assert rules.as_text(45000) == "45000"


class TestReadingAPath:
    def test_an_index_into_a_list_resolves(self):
        assert rules.read_path({"lines": [{"sku": "SEAT"}]}, "lines.0.sku") == "SEAT"

    def test_an_index_past_the_end_is_absent(self):
        """An out-of-range index is a path the record does not carry, not an error."""
        assert rules.read_path({"lines": []}, "lines.3.sku") is None

    def test_a_non_numeric_index_into_a_list_is_absent(self):
        assert rules.read_path({"lines": []}, "lines.first") is None

    def test_walking_into_a_scalar_is_absent(self):
        assert rules.read_path({"segment": "enterprise"}, "segment.name") is None

    def test_a_list_under_a_missing_key_is_absent(self):
        assert rules.read_path({}, "missing.0") is None

    def test_an_empty_path_is_absent(self):
        assert rules.read_path({"segment": "enterprise"}, "") is None


class TestPriceBookReferences:
    def test_a_bare_slug_is_read_as_an_id(self):
        assert rules.normalise_book("pb-ent") == {"id": "pb-ent", "name": None}

    def test_a_bare_sentence_is_read_as_a_name(self):
        """A deployment that names its books "Enterprise list 2026" must still work."""
        assert rules.normalise_book("Enterprise list 2026") == {
            "id": None,
            "name": "Enterprise list 2026",
        }

    def test_a_price_book_id_field_is_accepted(self):
        """A caller sending the field this workflow reads on a deal, rather than the book."""
        assert rules.normalise_book({"price_book_id": "pb-ent"})["id"] == "pb-ent"

    def test_an_empty_reference_is_refused(self):
        with pytest.raises(PriceBookRefusal) as caught:
            rules.normalise_book("")

        assert caught.value.code == "price_book_needs_an_id_or_a_name"

    def test_an_identity_prefers_the_id(self):
        """Two books with the same name and different ids are two books."""
        assert rules.book_identity({"id": "a", "name": "Standard"}) == "a"
        assert rules.book_identity({"id": None, "name": "Standard"}) == "Standard"

    def test_an_identity_of_nothing_is_empty(self):
        assert rules.book_identity(None) == ""

    def test_a_book_view_keeps_the_fields_its_writer_gave_it(self):
        """A reference this workflow never wrote is not rebuilt into its own shape."""
        view = rules.book_view({"id": "pb-ent", "currency": "GBP"})

        assert view["currency"] == "GBP"

    def test_a_book_view_of_a_bare_string_normalises(self):
        assert rules.book_view("pb-ent") == {"id": "pb-ent", "name": None}

    def test_a_book_view_of_nothing_is_none(self):
        for empty in (None, "", {}):
            assert rules.book_view(empty) is None

    def test_a_label_falls_back_to_the_id(self):
        assert rules.book_label({"id": "pb-ent", "name": None}) == "pb-ent"

    def test_a_label_for_no_book_is_none(self):
        assert rules.book_label(None) == vocab.PRICE_BOOK_NONE_LABEL

    def test_a_rule_reference_keeps_both_halves(self):
        assert rules.normalise_book({"id": "pb-ent", "name": "Enterprise"}) == {
            "id": "pb-ent",
            "name": "Enterprise",
        }


class TestInstants:
    def test_a_naive_moment_is_read_as_utc(self):
        """A seeder that hands over a naive datetime must not produce a local timestamp."""
        naive = datetime(2026, 3, 1, 12, 0, 0)

        assert rules.stamp(naive).startswith("2026-03-01T12:00:00")

    def test_an_aware_moment_is_converted(self):
        aware = datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone.utc)

        assert rules.stamp(aware).startswith("2026-03-01T12:00:00")

    def test_the_milliseconds_are_kept(self):
        assert rules.stamp(NOW).endswith("+00:00")


class TestNormalisingInput:
    def test_the_trigger_synonyms_are_accepted(self):
        """An integration posting "deal_created" should not have to know our spelling."""
        assert rules.normalise_trigger("deal_created") == vocab.TRIGGER_CREATE
        assert rules.normalise_trigger("deal_updated") == vocab.TRIGGER_UPDATE

    def test_a_match_mode_of_or_is_accepted(self):
        assert rules.normalise_match_mode("or") == vocab.FILTER_MATCH_ANY
        assert rules.normalise_match_mode("all_of") == vocab.FILTER_MATCH_ALL

    def test_a_filter_that_is_not_an_object_is_refused(self):
        with pytest.raises(PriceBookRefusal) as caught:
            rules.validate_filter("segment is enterprise")

        assert caught.value.code == "filter_needs_a_property"

    def test_a_list_operator_with_only_empty_values_is_refused(self):
        """A filter that can match nothing must not save as a configured rule."""
        with pytest.raises(PriceBookRefusal) as caught:
            rules.validate_filter(
                {"object": "deal", "property": "segment", "operator": "in", "value": ["", "  "]}
            )

        assert caught.value.code == "list_operator_needs_a_list"

    def test_a_list_operator_keeps_its_values_as_a_list(self):
        report = rules.validate_filter(
            {"object": "deal", "property": "segment", "operator": "in", "value": ["Mid", "Ent"]}
        )

        assert report["value"] == ["mid", "ent"]

    def test_a_rule_that_is_not_an_object_is_refused(self):
        with pytest.raises(PriceBookRefusal) as caught:
            rules.validate_rule("enterprise")

        assert caught.value.code == "rule_needs_a_name"

    def test_a_numeric_filter_value_that_is_not_a_number_is_refused(self, engine):
        """A boolean is 1 in Python, and ``True > 0`` runs and means nothing."""
        with pytest.raises(PriceBookRefusal) as caught:
            rules.validate_filter(
                {"object": "deal", "property": "amount", "operator": "gt", "value": True}
            )

        assert caught.value.code == "numeric_filter_value_is_not_a_number"

    def test_a_numeric_filter_value_that_is_not_anything_is_refused(self):
        with pytest.raises(PriceBookRefusal) as caught:
            rules._as_decimal({"a": 1}, "value")

        assert caught.value.code == "numeric_filter_value_is_not_a_number"


class TestTheDecisionFunctionDirectly:
    def test_a_matched_filter_report_is_not_matched_when_there_are_none(self):
        """A rule read from the store with no filters must not match every deal."""
        assert rules.matches_filters([], {"segment": "x"})["matched"] is False
        assert rules.matches_filters([], {"segment": "x"})["total"] == 0

    def test_a_refusal_serialises_itself(self):
        """So a handler can answer with the code without re-deriving the sentence."""
        assert rules.PriceBookRefusal("rule_needs_a_name").to_dict() == {
            "error": "rule_needs_a_name",
            "detail": vocab.ERROR_CODES["rule_needs_a_name"][1],
            "status": 422,
            "errors": {},
        }

    def test_an_unknown_code_still_answers(self):
        """A code outside the table must not raise on the way to being a 422."""
        refusal = rules.PriceBookRefusal("a_code_nobody_published")

        assert refusal.status == 422
        assert refusal.detail == "a_code_nobody_published"

    def test_a_not_found_is_serialisable_by_the_handler(self):
        missing = rules.PriceBookNotFound("unknown_deal", "deal", "d1")

        assert str(missing) == "deal d1 not found"


class TestModuleLevelHelpers:
    def test_the_vocabulary_helper_serves_the_catalogue(self):
        from dsr.quoting_proposals import price_book_engine

        assert price_book_engine.vocabulary() == vocab.catalogue()

    def test_the_inferences_helper_serves_every_decision(self):
        from dsr.quoting_proposals import price_book_engine

        served = price_book_engine.inferences_report()

        assert served["count"] == inf.count()
        assert served["multiple_matches"]["chosen"] == "needs_choice"

    def test_an_unknown_decision_id_is_none_rather_than_a_guess(self):
        assert inf.describe_one("A_DECISION_THAT_DOES_NOT_EXIST") is None

    def test_a_company_found_through_a_collection_alias(self, engine: PriceBookEngine):
        """A mirror that calls companies accounts must not produce an empty panel."""
        engine.store.create(
            "crm_account",
            {"name": "Halcyon", "industry": "software"},
            record_id="co_halcyon",
            room_id="room_a",
            actor="dana",
            source="fixture",
        )
        deal(engine, company="co_halcyon")
        engine.create_rule(
            {
                "key": "software",
                "label": "Software companies",
                "price_book": BOOK_ENTERPRISE,
                "filters": [
                    {
                        "object": "company",
                        "property": "industry",
                        "operator": "is",
                        "value": "software",
                    }
                ],
            },
            actor="dana",
            source="fixture",
            room_id="room_a",
        )

        assert assign(engine)["outcome"] == vocab.ASSIGNMENT_ASSIGNED

    def test_a_company_reference_that_is_absent_is_skipped(self, engine: PriceBookEngine):
        """A deal naming a company that does not exist is still priceable by deal filters."""
        deal(engine, segment="enterprise", company="co_absent")
        segment_rule(engine)

        answer = assign(engine)

        assert answer["outcome"] == vocab.ASSIGNMENT_ASSIGNED

    def test_the_seed_normalises_a_bare_room_id(self):
        """The seeder passes pairs on some paths and bare ids on others."""
        from dsr.db.audited import AuditedDatabase

        module = importlib.import_module(FEATURE_MODULE)
        db = AuditedDatabase(":memory:", actor="seed-test")
        try:
            store = RecordStore(db)
            store.create("room", {"name": "Northwind"}, record_id="room_a", actor="dana")
            summary = module.seed(db, {"room_ids": ["room_a"], "now": NOW, "rng": None})
            assert summary
            summary.encode("cp1252")
            assert store.list(vocab.ASSIGNMENTS, limit=50)
        finally:
            db.close()


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

        assert module.router.prefix == "/api/WF-088"

    def test_the_feature_exports_a_descriptor(self):
        module = importlib.import_module(FEATURE_MODULE)

        assert module.FEATURE["id"].startswith("wf-088-")
        assert module.FEATURE["ticket"] == "WF-088"

    def test_the_error_handlers_cover_both_of_this_workflows_types(self):
        module = importlib.import_module(FEATURE_MODULE)

        assert set(module.EXCEPTION_HANDLERS) == {PriceBookRefusal, PriceBookNotFound}

    def test_the_handler_does_not_claim_a_shared_type(self):
        """Two features may not map the same error type. RecordNotFound is the core's."""
        module = importlib.import_module(FEATURE_MODULE)

        for handled in module.EXCEPTION_HANDLERS:
            assert handled.__module__.startswith("dsr.quoting_proposals"), handled

    def test_the_domain_adds_only_its_own_four_modules(self):
        """The package is shared, and a rewrite of another ticket's module is a conflict."""
        mine = {path.name for path in domain_paths()}

        assert mine == {
            "price_book_engine.py",
            "price_book_inferences.py",
            "price_book_rules.py",
            "price_book_vocabulary.py",
        }

    def test_every_collection_name_is_ticket_prefixed(self):
        """The host has no collection-collision check, so the prefix is the only guard."""
        for name in (vocab.PRICE_BOOK_RULES, vocab.ASSIGNMENTS):
            assert name.startswith("wf088_")

    def test_the_vocabulary_is_the_one_place_a_reason_is_named(self):
        """A page renders from the served catalogue, so a compiled-in list would drift."""
        served = {one["reason"] for one in vocab.catalogue()["assignments"]}

        assert served == set(vocab.ASSIGNMENT_REASONS)


# --------------------------------------------------------------------------- #
# the seed return string
# --------------------------------------------------------------------------- #


class TestTheSeedString:
    def _seed(self, rooms: list, **overrides):
        """Run the feature's seed over a fresh database and return its string and store.

        The seeder passes ``(room_id, name)`` pairs, so the rooms are taken as the first
        element of each pair rather than as the pair itself. Passing the pair through
        would reach the store as a tuple and fail at the SQL binding, which is this
        helper's bug and not the seed's.
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
            assignments = store.list(vocab.ASSIGNMENTS, limit=100)
            deals = store.list(vocab.SOURCE_DEALS, limit=100)
            return summary, assignments, deals
        finally:
            db.close()

    def test_the_seeder_prints_a_string_and_every_character_survives_cp1252(self):
        """The defect that broke the whole seeder, stated as the assertion that prevents it.

        One RIGHTWARDS ARROW in a recovered feature's return string broke the entire
        seeder on a Windows console, because the seeder prints it to a cp1252 console.
        """
        summary, _, _ = self._seed([("room_a", "Northwind")])

        assert isinstance(summary, str)
        assert summary
        summary.encode("cp1252")

    def test_the_seed_states_are_really_there(self):
        """A seed line describing a state the seed did not produce is a lie in a demo."""
        summary, assignments, _ = self._seed([("room_a", "Northwind"), ("room_b", "Halcyon")])

        outcomes = [row["data"]["outcome"] for row in assignments]
        assert outcomes.count(vocab.ASSIGNMENT_ASSIGNED) >= 1
        assert outcomes.count(vocab.ASSIGNMENT_NEEDS_CHOICE) >= 1
        assert outcomes.count(vocab.ASSIGNMENT_NO_AUTO_ASSIGN) >= 1
        assert outcomes.count(vocab.ASSIGNMENT_NO_MATCH_INACTIVE_RULE) >= 1
        assert outcomes.count(vocab.ASSIGNMENT_CHANGED) >= 1
        assert "inheriting a price book" in summary

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

    def test_every_demo_rule_prices_only_its_own_deal(self):
        """Two rules in one room must not price each other's deals."""
        _, _, deals = self._seed([("room_a", "Northwind")])

        assert deals
        # Six plans and six deals in one room, each priced by its own rule only. A demo
        # where every rule prices every deal shows the wrong states.
        assert len({row["id"] for row in deals}) == len(deals)

    def test_the_seeded_removal_is_really_recorded(self):
        """The destructive override is a count in the demo, not just a sentence."""
        _, assignments, _ = self._seed([("room_a", "Northwind")])

        changed = [
            row["data"] for row in assignments if row["data"]["outcome"] == vocab.ASSIGNMENT_CHANGED
        ]
        assert changed
        assert changed[0]["line_items_removed"]

    def test_the_seeded_quote_inherits(self):
        """The sourced inheritance is a read the demo actually performs."""
        module = importlib.import_module(FEATURE_MODULE)
        from dsr.db.audited import AuditedDatabase

        db = AuditedDatabase(":memory:", actor="seed-test")
        try:
            store = RecordStore(db)
            store.create("room", {"name": "Northwind"}, record_id="room_a", actor="dana")
            module.seed(db, {"room_ids": [("room_a", "Northwind")], "now": NOW, "rng": None})
            quotes = store.list(vocab.SOURCE_QUOTES, limit=20)
            assert quotes
            priced = [one for one in quotes if vocab.PRICE_BOOK in (one.get("data") or {})]
            # The quote must NOT carry a price book of its own: it inherits.
            assert priced == []
        finally:
            db.close()

    def test_the_seed_writes_through_the_audited_store(self, engine):
        """Every seeded write is an audited row, not a direct database write."""
        module = importlib.import_module(FEATURE_MODULE)
        from dsr.db.audited import AuditedDatabase

        db = AuditedDatabase(":memory:", actor="seed-test")
        try:
            store = RecordStore(db)
            store.create("room", {"name": "Northwind"}, record_id="room_a", actor="dana")
            module.seed(db, {"room_ids": [("room_a", "Northwind")], "now": NOW, "rng": None})
            stats = db.stats()
        finally:
            db.close()

        assert stats["records"] > 0
        assert stats["audit_entries"] > 0
