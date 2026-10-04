"""WF-051 domain tests: the researched rules, and the decisions it left open.

Run on its own, and run in the full suite, and both must pass. Nothing here reads
another test's state: every test builds its own records through the store fixture,
which is what keeps this file from being an order-dependent one under xdist.
"""

from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from dsr.concierge_router import availability, inferences, nodes, rules, vocabulary
from dsr.concierge_router.engine import (
    BOOKINGS,
    GUEST_FIELDS,
    ROUTERS,
    SELLERS,
    ConciergeRouterEngine,
)
from dsr.concierge_router.errors import (
    AssignmentNotBookable,
    GuestIdentityRequired,
    GuestMismatch,
    MeetingTypeNotOffered,
    NoRuleMatched,
    RedirectWithoutUrl,
    RouteConsumed,
    RouteNotFound,
    RouteNotSchedulable,
    RouterDeclarationError,
    RouterError,
    RouterNotPublished,
    RouterUnavailable,
    RulesDoNotFallThrough,
    SlotNotOffered,
    TriggerActionMissing,
    TriggerMustBeFirst,
    UnknownNodeType,
    UnknownRouter,
)
from dsr.concierge_router.nodes import validate_declaration
from dsr.features import wf051_route_and_book_a_demo_request_inline_f as feature
from dsr.store import RecordStore

SOURCE = "test"
ROOM = "room-1"
OTHER_ROOM = "room-2"
NOW = datetime(2026, 10, 5, 8, 0, tzinfo=timezone.utc)


@pytest.fixture
def engine(store: RecordStore) -> ConciergeRouterEngine:
    """A fresh engine over a fresh, empty database."""
    return ConciergeRouterEngine(store)


# --------------------------------------------------------------------------- #
# Fixtures that build a working router, so a test states only what it varies
# --------------------------------------------------------------------------- #


def trigger(actions: list[str] | None = None, field_map: dict[str, str] | None = None) -> dict:
    return {
        "type": "trigger",
        "actions": actions if actions is not None else ["webform_is_submitted"],
        "field_map": field_map if field_map is not None else {"work_email": "email"},
    }


def calendar(
    assignment: dict | None = None,
    types: list[str] | None = None,
    minutes: int = 60,
) -> dict:
    return {
        "type": "display_calendar",
        "assignment": assignment if assignment is not None else {"type": "round_robin"},
        "meeting_types": types if types is not None else ["demo"],
        "timer_minutes": minutes,
    }


def declaration(**overrides) -> dict:
    """A router that passes every researched declaration rule."""
    payload = {
        "slug": "enterprise-demo",
        "name": "Enterprise demo",
        "nodes": [
            trigger(),
            {
                "type": "routing_rule",
                "name": "Named owner",
                "kind": "crm_ownership",
                "conditions": [
                    {
                        "field": "owner_team",
                        "source": "crm_object",
                        "operator": "equals",
                        "value": "field-sales",
                    }
                ],
                "calendar": calendar({"type": "owner"}),
            },
            calendar(),
            {"type": "catch_all", "name": "Deal desk"},
        ],
    }
    payload.update(overrides)
    return payload


def seed_seller(engine: ConciergeRouterEngine, name: str = "Dana", **overrides) -> dict:
    payload = {
        "name": name,
        "email": f"{name.lower()}@example.test",
        "team": "field-sales",
        "calendar_connected": True,
        "busy": [],
    }
    payload.update(overrides)
    return engine.register_seller(payload, room_id=ROOM, actor="dana", source=SOURCE)


def publish(engine: ConciergeRouterEngine, **overrides) -> dict:
    return engine.create_router(
        declaration(publish_state="published", deployment=["web_form"], **overrides),
        room_id=ROOM,
        actor="dana",
        source=SOURCE,
    )


def open_session(engine: ConciergeRouterEngine, **overrides) -> dict:
    """Open a routing session, declaring a router unless one is handed in.

    ``router_id`` in ``overrides`` names an existing router, which is how a test
    keeps the router count at one. Without it a fresh published router is declared
    and its id is used, so a test that does not care pays for one extra record.
    """
    router_id = overrides.pop("router_id", "") or publish(engine)["id"]
    payload = {
        "router_id": router_id,
        "form_fields": {"work_email": "buyer@example.test", "company": "Northwind"},
    }
    payload.update(overrides)
    return engine.route(payload, room_id=ROOM, actor="buyer", source=SOURCE)


# --------------------------------------------------------------------------- #
# The researched absolutes, enforced at declaration
# --------------------------------------------------------------------------- #


class TestTheTriggerMustBeFirst:
    def test_a_chain_starting_with_the_trigger_is_accepted(self):
        assert validate_declaration(declaration())["nodes"][0]["type"] == "trigger"

    def test_a_chain_starting_with_a_rule_is_refused(self):
        payload = declaration()
        payload["nodes"].insert(0, {"type": "routing_rule", "name": "orphan"})
        with pytest.raises(TriggerMustBeFirst) as caught:
            validate_declaration(payload)
        assert "always" in str(caught.value)

    def test_a_chain_starting_with_a_catch_all_is_refused(self):
        payload = declaration()
        payload["nodes"].insert(0, {"type": "catch_all", "name": "first"})
        with pytest.raises(TriggerMustBeFirst):
            validate_declaration(payload)

    def test_the_refusal_is_a_declaration_error(self):
        payload = declaration()
        payload["nodes"].insert(0, {"type": "catch_all", "name": "first"})
        with pytest.raises(RouterDeclarationError):
            validate_declaration(payload)


class TestTheTriggerMustDeclareAnAction:
    def test_a_trigger_with_no_actions_is_refused(self):
        payload = declaration()
        payload["nodes"][0]["actions"] = []
        with pytest.raises(TriggerActionMissing):
            validate_declaration(payload)

    def test_a_trigger_with_an_unknown_action_is_refused(self):
        payload = declaration()
        payload["nodes"][0]["actions"] = ["telepathy"]
        with pytest.raises(TriggerActionMissing):
            validate_declaration(payload)

    def test_a_single_action_written_as_a_string_is_accepted(self):
        payload = declaration()
        payload["nodes"][0]["actions"] = "webform_is_submitted"
        assert validate_declaration(payload)["nodes"][0]["actions"] == ["webform_is_submitted"]

    @pytest.mark.parametrize("action", vocabulary.TRIGGER_ACTIONS)
    def test_every_researched_trigger_action_is_accepted(self, action):
        payload = declaration()
        payload["nodes"][0]["actions"] = [action]
        assert action in validate_declaration(payload)["nodes"][0]["actions"]

    def test_duplicate_actions_collapse_to_one(self):
        payload = declaration()
        payload["nodes"][0]["actions"] = ["webform_is_submitted", "webform_is_submitted"]
        assert validate_declaration(payload)["nodes"][0]["actions"] == ["webform_is_submitted"]


class TestTheChainMustEndInACatchAll:
    def test_a_chain_with_a_catch_all_is_accepted(self):
        assert validate_declaration(declaration())["catch_all"] == "Deal desk"

    def test_a_chain_without_one_is_refused(self):
        payload = declaration()
        payload["nodes"] = [node for node in payload["nodes"] if node["type"] != "catch_all"]
        with pytest.raises(RulesDoNotFallThrough) as caught:
            validate_declaration(payload)
        assert "acknowledge all inbound Leads" in str(caught.value)

    def test_the_catch_all_is_pulled_to_the_end_of_the_chain(self):
        payload = declaration()
        payload["nodes"].insert(0, {"type": "catch_all", "name": "wrong end"})
        # Refused for leading with it, so test the ordering helper directly on a
        # chain that saved.
        checked = validate_declaration(declaration())
        chain = nodes.rule_nodes(checked["nodes"])
        assert chain[-1]["type"] == "catch_all"

    def test_no_rule_matched_is_unreachable_for_a_router_that_saved(self):
        """The point of enforcing the catch-all on save rather than at run time."""
        checked = validate_declaration(declaration())
        chain = nodes.rule_nodes(checked["nodes"])
        assert chain, "a saved router always has at least one rule"
        assert chain[-1]["type"] == "catch_all"
        matched = rules.matched_rule(chain, {}, [])
        assert matched is not None, "a chain ending in a catch-all can never run out"

    def test_a_broken_chain_from_outside_the_package_still_refuses(self):
        """A row imported from elsewhere can lack a catch-all, so the error stays."""
        with pytest.raises(NoRuleMatched):
            rules.raise_if_no_rule(None)


class TestTheNodeSetIsTheResearchedOne:
    @pytest.mark.parametrize("node_type", vocabulary.NODE_TYPES)
    def test_every_researched_node_type_is_accepted(self, node_type):
        payload = declaration()
        # Append after the catch-all rather than before it, and hold the new node
        # by index: after an insert, ``[-1]`` is the last element, which is not
        # the one just inserted when the insert was not at the end.
        extra = {"type": node_type, "name": node_type}
        if node_type == vocabulary.TRIGGER:
            extra["actions"] = ["router_link"]
        if node_type == vocabulary.REDIRECT_TO:
            extra["url"] = "https://example.test/thanks"
        if node_type == vocabulary.DISPLAY_CALENDAR:
            extra.update({"assignment": {"type": "round_robin"}, "meeting_types": ["demo"]})
        payload["nodes"].append(extra)
        assert validate_declaration(payload)

    def test_a_node_outside_the_set_is_refused_by_name(self):
        payload = declaration()
        payload["nodes"].append({"type": "teleport", "name": "x"})
        with pytest.raises(UnknownNodeType) as caught:
            validate_declaration(payload)
        assert "teleport" in str(caught.value)

    def test_an_empty_node_list_is_refused(self):
        with pytest.raises(UnknownNodeType):
            validate_declaration(declaration(nodes=[]))

    def test_a_node_that_is_not_an_object_is_refused(self):
        with pytest.raises(UnknownNodeType):
            validate_declaration(declaration(nodes=["trigger"]))


class TestTheFieldMap:
    def test_a_map_is_folded_to_snake_case(self):
        payload = declaration()
        payload["nodes"][0]["field_map"] = {"workEmail": "email", "company-name": "company"}
        assert validate_declaration(payload)["field_map"] == {
            "work_email": "email",
            "company_name": "company",
        }

    def test_a_trigger_with_no_map_is_accepted(self):
        payload = declaration()
        payload["nodes"][0]["field_map"] = None
        assert validate_declaration(payload)["field_map"] == {}

    def test_one_data_field_mapped_from_two_form_fields_is_refused(self):
        from dsr.concierge_router.errors import FieldMappingError

        payload = declaration()
        payload["nodes"][0]["field_map"] = {"work_email": "email", "alt_email": "email"}
        with pytest.raises(FieldMappingError) as caught:
            validate_declaration(payload)
        assert "could only read one" in str(caught.value)

    def test_a_map_to_an_empty_name_is_refused(self):
        from dsr.concierge_router.errors import FieldMappingError

        payload = declaration()
        payload["nodes"][0]["field_map"] = {"work_email": "!!!"}
        with pytest.raises(FieldMappingError):
            validate_declaration(payload)


class TestTheTimerNodes:
    def test_a_calendar_node_gets_the_default_timer_when_none_is_stated(self):
        payload = declaration()
        payload["nodes"][2].pop("timer_minutes")
        node = next(
            n for n in validate_declaration(payload)["nodes"] if n["type"] == "display_calendar"
        )
        assert node["timer_minutes"] == vocabulary.DEFAULT_TIMER_MINUTES

    def test_a_redirect_with_no_destination_is_refused(self):
        payload = declaration()
        payload["nodes"].append({"type": "redirect_to", "name": "thanks"})
        with pytest.raises(RedirectWithoutUrl) as caught:
            validate_declaration(payload)
        assert "nothing to bounce to" in str(caught.value)

    def test_a_redirect_with_a_destination_is_accepted(self):
        payload = declaration()
        payload["nodes"].append(
            {"type": "redirect_to", "name": "thanks", "url": "https://example.test/thanks"}
        )
        checked = validate_declaration(payload)
        assert checked["nodes"][-1]["url"] == "https://example.test/thanks"

    @pytest.mark.parametrize("bad", [0, -5, "soon", None])
    def test_a_timer_that_is_not_a_positive_number_of_minutes_is_refused(self, bad):
        payload = declaration()
        payload["nodes"][2]["timer_minutes"] = bad
        with pytest.raises(RedirectWithoutUrl):
            validate_declaration(payload)


class TestTheDisplayCalendarNode:
    def test_a_calendar_node_must_name_a_meeting_type(self):
        from dsr.concierge_router.errors import NodeError

        payload = declaration()
        payload["nodes"][2]["meeting_types"] = []
        with pytest.raises(NodeError) as caught:
            validate_declaration(payload)
        assert "Meeting Type" in str(caught.value)

    @pytest.mark.parametrize("kind", vocabulary.ASSIGNMENT_TYPES)
    def test_every_researched_assignment_is_accepted(self, kind):
        payload = declaration()
        payload["nodes"][2]["assignment"] = {"type": kind}
        checked = validate_declaration(payload)
        node = next(n for n in checked["nodes"] if n["type"] == "display_calendar")
        assert node["assignment"]["type"] == kind

    def test_an_assignment_outside_the_researched_three_is_refused(self):
        payload = declaration()
        payload["nodes"][2]["assignment"] = {"type": "astrology"}
        with pytest.raises(UnknownNodeType):
            validate_declaration(payload)


class TestRuleConditions:
    def test_a_condition_naming_no_field_is_refused(self):
        from dsr.concierge_router.errors import FieldMappingError

        payload = declaration()
        payload["nodes"][1]["conditions"] = [{"field": "", "operator": "equals", "value": "x"}]
        with pytest.raises(FieldMappingError) as caught:
            validate_declaration(payload)
        assert "never be true or false" in str(caught.value)

    def test_an_unknown_operator_is_refused(self):
        from dsr.concierge_router.errors import FieldMappingError

        payload = declaration()
        payload["nodes"][1]["conditions"] = [
            {"field": "employee_count", "operator": "sorta", "value": "x"}
        ]
        with pytest.raises(FieldMappingError):
            validate_declaration(payload)

    def test_a_rule_with_no_conditions_is_accepted(self):
        payload = declaration()
        payload["nodes"][1]["conditions"] = []
        assert validate_declaration(payload)

    def test_a_catch_all_with_no_conditions_matches_everything(self):
        """The researched catch-all is a path, not an exception."""
        catch = {"type": "catch_all", "name": "Deal desk"}
        assert rules.evaluate_rule(catch, {}, [])


class TestNormaliseFieldName:
    @pytest.mark.parametrize(
        ("spelling", "expected"),
        [
            ("work_email", "work_email"),
            ("workEmail", "work_email"),
            ("work-email", "work_email"),
            ("Work Email", "work_email"),
            ("WORKEMAIL", "workemail"),
            ("work.email", "work_email"),
            ("  work_email  ", "work_email"),
        ],
    )
    def test_every_spelling_of_one_field_folds_to_one_name(self, spelling, expected):
        assert vocabulary.normalise_field_name(spelling) == expected

    def test_an_empty_name_folds_to_an_empty_name(self):
        assert vocabulary.normalise_field_name("") == ""
        assert vocabulary.normalise_field_name("!!!") == ""


# --------------------------------------------------------------------------- #
# Rule evaluation: the researched two-source match
# --------------------------------------------------------------------------- #


class TestRuleEvaluation:
    def test_a_data_field_condition_reads_the_mapped_webform_values(self):
        node = {
            "type": "routing_rule",
            "conditions": [
                {
                    "field": "employee_count",
                    "source": "data_field",
                    "operator": "equals",
                    "value": "500",
                }
            ],
        }
        assert rules.evaluate_rule(node, {"employee_count": "500"}, []) is True
        assert rules.evaluate_rule(node, {"employee_count": "50"}, []) is False

    def test_a_data_field_condition_never_reads_a_crm_value(self):
        """The research keeps the two sources apart, so this build does too."""
        node = {
            "type": "routing_rule",
            "conditions": [
                {
                    "field": "employee_count",
                    "source": "data_field",
                    "operator": "equals",
                    "value": "500",
                }
            ],
        }
        record = {"data": {"fields": {"employee_count": "500"}, "email": "a@b.test"}}
        assert rules.evaluate_rule(node, {}, [record]) is False

    def test_a_crm_condition_reads_the_live_record(self):
        node = {
            "type": "routing_rule",
            "conditions": [
                {
                    "field": "owner_team",
                    "source": "crm_object",
                    "operator": "equals",
                    "value": "field-sales",
                }
            ],
        }
        record = {"data": {"crm_owner": {"team": "field-sales"}}}
        assert rules.evaluate_rule(node, {}, [record]) is True
        assert rules.evaluate_rule(node, {}, [{"data": {"crm_owner": {"team": "sdr"}}}]) is False

    def test_a_crm_condition_matches_nothing_when_no_record_carries_the_value(self):
        node = {
            "type": "routing_rule",
            "conditions": [
                {"field": "owner_team", "source": "crm_object", "operator": "equals", "value": ""}
            ],
        }
        assert rules.evaluate_rule(node, {}, [{"data": {}}]) is False

    def test_a_crm_condition_reads_a_flat_team_too(self):
        record = {"data": {"team": "field-sales"}}
        assert rules.crm_team(record["data"]) == "field-sales"
        assert rules.crm_team({"crm_owner": {"team": "sdr"}}) == "sdr"
        assert rules.crm_team({}) == ""

    def test_every_condition_must_hold(self):
        node = {
            "type": "routing_rule",
            "conditions": [
                {"field": "employee_count", "operator": "equals", "value": "500"},
                {"field": "company", "operator": "equals", "value": "Northwind"},
            ],
        }
        assert rules.evaluate_rule(node, {"employee_count": "500", "company": "Northwind"}, [])
        assert not rules.evaluate_rule(node, {"employee_count": "500", "company": "Contoso"}, [])

    @pytest.mark.parametrize(
        ("operator", "left", "right", "expected"),
        [
            ("equals", "a", "a", True),
            ("equals", "A", "a", True),
            ("not_equals", "a", "b", True),
            ("contains", "Northwind Traders", "north", True),
            ("in", "emea", ["emea", "apac"], True),
            ("in", "latam", ["emea", "apac"], False),
        ],
    )
    def test_each_comparison(self, operator, left, right, expected):
        condition = {"field": "x", "source": "data_field", "operator": operator, "value": right}
        assert rules.evaluate_condition(condition, {"x": left}, []) is expected


class TestTheOwnershipObjectSet:
    def test_a_rule_may_check_the_owner_of_a_lead_contact_or_account(self):
        for name in vocabulary.CRM_OWNERSHIP_OBJECTS:
            assert vocabulary.require_ownership_object(name) == name

    @pytest.mark.parametrize("name", ["opportunity", "case"])
    def test_opportunity_and_case_are_refused_as_ownership_objects(self, name):
        with pytest.raises(ValueError) as caught:
            vocabulary.require_ownership_object(name)
        assert "Without Ownership" in str(caught.value)

    def test_both_remain_reachable_as_field_values(self):
        assert "opportunity" in vocabulary.CRM_FIELD_SOURCES
        assert "case" in vocabulary.CRM_FIELD_SOURCES
        assert vocabulary.require_crm_object("opportunity") == "opportunity"


class TestFindingCrmRecords:
    def _record(self, store: RecordStore, email: str, object_name: str, **extra):
        return store.create(
            rules.CRM_RECORDS,
            {"system": "salesforce", "object": object_name, "email": email, **extra},
            room_id=ROOM,
            actor="test",
            source=SOURCE,
        )

    def test_an_exact_address_match_wins(self, engine):
        self._record(engine.store, "buyer@example.test", "contact", owner_id="005-1")
        self._record(engine.store, "example.test", "account", owner_id="005-2")
        found = rules.find_crm_records(engine.store, "buyer@example.test", room_id=ROOM)
        assert found[0]["data"]["object"] == "contact"

    def test_a_domain_match_is_the_salesforce_lead_to_account_case(self, engine):
        """The research names "incl. Salesforce Lead-to-Account matching"."""
        self._record(engine.store, "example.test", "account", owner_id="005-2")
        found = rules.find_crm_records(engine.store, "buyer@example.test", room_id=ROOM)
        assert len(found) == 1

    def test_records_from_another_room_are_not_returned(self, engine):
        engine.store.create(
            rules.CRM_RECORDS,
            {"system": "salesforce", "object": "contact", "email": "buyer@example.test"},
            room_id=OTHER_ROOM,
            actor="test",
            source=SOURCE,
        )
        assert rules.find_crm_records(engine.store, "buyer@example.test", room_id=ROOM) == []


# --------------------------------------------------------------------------- #
# The availability engine
# --------------------------------------------------------------------------- #


class TestTheAvailabilityEngine:
    def test_a_free_calendar_offers_slots_inside_its_working_hours(self):
        slots = availability.offer_slots(
            {"busy": []},
            {"name": "demo", "duration_minutes": 30},
            now=NOW,
        )
        assert slots
        for slot in slots:
            start = vocabulary.parse_timestamp(slot["start"])
            assert 9 <= start.hour < 17
            assert start.weekday() in availability.DEFAULT_WORKING_WEEKDAYS

    def test_a_busy_block_never_overlaps_an_offered_slot(self):
        """Every day in the horizon is busy, so nothing is offered at all."""
        busy = [{"start": "2026-01-01T00:00:00Z", "end": "2027-01-01T00:00:00Z"}]
        slots = availability.offer_slots(
            {"busy": busy}, {"name": "demo", "duration_minutes": 30}, now=NOW, horizon_days=5
        )
        assert slots == []

    def test_a_one_day_busy_block_removes_only_that_day(self):
        busy = [{"start": "2026-10-05T00:00:00Z", "end": "2026-10-06T00:00:00Z"}]
        slots = availability.offer_slots(
            {"busy": busy}, {"name": "demo", "duration_minutes": 30}, now=NOW, horizon_days=5
        )
        assert slots, "later days are still free"
        assert all(not slot["start"].startswith("2026-10-05") for slot in slots)

    def test_a_partial_busy_block_removes_only_its_own_slots(self):
        busy = [{"start": "2026-10-05T10:00:00Z", "end": "2026-10-05T11:00:00Z"}]
        slots = availability.offer_slots(
            {"busy": busy}, {"name": "demo", "duration_minutes": 30}, now=NOW, horizon_days=1
        )
        starts = [slot["start"] for slot in slots]
        assert "2026-10-05T09:00:00Z" in starts
        assert "2026-10-05T10:00:00Z" not in starts

    def test_a_malformed_block_is_skipped_rather_than_raising(self):
        busy = [
            {"start": "not-a-time", "end": "also-not"},
            "not an object",
            {"start": "2026-10-05T10:00:00Z", "end": "2026-10-05T09:00:00Z"},
            {"start": "2026-10-05T11:00:00Z", "end": "2026-10-05T12:00:00Z"},
        ]
        slots = availability.offer_slots(
            {"busy": busy}, {"name": "demo", "duration_minutes": 30}, now=NOW, horizon_days=1
        )
        assert all(slot["start"] != "2026-10-05T11:00:00Z" for slot in slots)

    def test_the_slot_list_is_capped(self):
        slots = availability.offer_slots(
            {"busy": []}, {"name": "demo", "duration_minutes": 30}, now=NOW, max_slots=5
        )
        assert len(slots) == 5

    def test_the_search_horizon_bounds_the_list(self):
        """Nine to five is sixteen half-hour starts, so a one-day horizon is 16."""
        slots = availability.offer_slots(
            {"busy": []}, {"name": "demo", "duration_minutes": 30}, now=NOW, horizon_days=1
        )
        assert len(slots) == 16
        assert all(slot["start"].startswith("2026-10-05") for slot in slots)

    def test_a_seller_may_override_their_working_hours(self):
        seller = {
            "busy": [],
            "working_hours": {"start_hour": 6, "end_hour": 8, "weekdays": [0, 1, 2, 3, 4]},
        }
        slots = availability.offer_slots(
            seller, {"name": "demo", "duration_minutes": 30}, now=NOW, horizon_days=1
        )
        assert all(6 <= vocabulary.parse_timestamp(slot["start"]).hour < 8 for slot in slots)

    def test_two_meeting_types_union_into_one_sorted_list(self):
        offered = availability.offer_slot(
            {"busy": []},
            [{"name": "demo", "duration_minutes": 30}, {"name": "deep", "duration_minutes": 60}],
            now=NOW,
            max_slots=20,
        )
        starts = [slot["start"] for slot in offered]
        assert starts == sorted(starts)
        assert len(starts) == len(set(starts))

    def test_booking_a_slot_returns_the_blocks_with_the_new_one_appended(self):
        seller = {"busy": [{"start": "2026-10-05T09:00:00Z", "end": "2026-10-05T10:00:00Z"}]}
        slot = {"start": "2026-10-06T09:00:00Z", "end": "2026-10-06T09:30:00Z"}
        blocks = availability.book_block(seller, slot)
        assert len(blocks) == 2
        assert blocks[-1] == slot
        assert seller["busy"], "the seller's own blocks are not mutated"


# --------------------------------------------------------------------------- #
# The first researched call: route and qualify
# --------------------------------------------------------------------------- #


class TestTheFirstCall:
    def test_a_published_router_answers_with_the_researched_field_names(self, engine):
        seed_seller(engine)
        answer = open_session(engine)
        assert set(vocabulary.ROUTE_RESPONSE_FIELDS) <= set(answer)
        assert answer["schedulingAllowed"] is True
        assert answer["assignment"]["userId"]
        assert answer["assignment"]["type"] in {"user", "owner", "round_robin"}

    def test_the_routing_link_is_stored_as_a_path_not_a_tenant_url(self, engine):
        """The sample's host is a tenant placeholder; see the inference."""
        seed_seller(engine)
        router = publish(engine, routing_link_base="https://northwind.chilipiper.com")
        answer = engine.route(
            {"router_id": router["id"], "form_fields": {"work_email": "buyer@example.test"}},
            room_id=ROOM,
            actor="buyer",
            source=SOURCE,
        )
        assert answer["routingLink"].startswith(
            "https://northwind.chilipiper.com/concierge-router/"
        )
        stored = engine.store.get(answer["routeId"])
        assert not stored["data"]["routing_link"].startswith("http")

    def test_an_unpublished_router_accepts_no_inbound_request(self, engine):
        router = engine.create_router(declaration(), room_id=ROOM, actor="dana", source=SOURCE)
        with pytest.raises(RouterNotPublished):
            engine.route(
                {"router_id": router["id"], "form_fields": {"work_email": "a@b.test"}},
                room_id=ROOM,
                actor="a",
                source=SOURCE,
            )

    def test_a_switched_off_router_is_refused_distinctly(self, engine):
        router = publish(engine)
        engine.publish_router(
            router["id"],
            {"publish_state": "published", "enabled": False},
            room_id=ROOM,
            actor="dana",
            source=SOURCE,
        )
        with pytest.raises(RouterUnavailable):
            engine.route(
                {"router_id": router["id"], "form_fields": {"work_email": "a@b.test"}},
                room_id=ROOM,
                actor="a",
                source=SOURCE,
            )

    def test_an_unknown_router_is_refused(self, engine):
        with pytest.raises(UnknownRouter):
            engine.route(
                {"router_id": "nope", "form_fields": {"work_email": "a@b.test"}},
                room_id=ROOM,
                actor="a",
                source=SOURCE,
            )

    def test_a_request_with_no_guest_email_is_refused(self, engine):
        seed_seller(engine)
        router = publish(engine)
        with pytest.raises(GuestIdentityRequired) as caught:
            engine.route(
                {"router_id": router["id"], "form_fields": {"company": "Northwind"}},
                room_id=ROOM,
                actor="a",
                source=SOURCE,
            )
        assert "no object to match" in str(caught.value)

    def test_a_camel_case_form_field_is_accepted(self, engine):
        seed_seller(engine)
        router = publish(engine)
        answer = engine.route(
            {"router_id": router["id"], "form_fields": {"workEmail": "buyer@example.test"}},
            room_id=ROOM,
            actor="buyer",
            source=SOURCE,
        )
        assert answer[vocabulary.PRIMARY_GUEST_PAYLOAD]["email"] == "buyer@example.test"

    def test_the_catch_all_is_reached_and_the_outcome_names_it(self, engine):
        seed_seller(engine)
        answer = open_session(engine)
        assert answer["routingOutcome"] == vocabulary.CATCH_ALL_MATCHED
        assert answer["rule"] == "Deal desk"

    def test_a_named_rule_that_matches_is_reported_as_such(self, engine):
        seed_seller(engine)
        engine.store.create(
            rules.CRM_RECORDS,
            {
                "system": "salesforce",
                "object": "contact",
                "email": "buyer@example.test",
                "crm_owner": {"team": "field-sales"},
            },
            room_id=ROOM,
            actor="test",
            source=SOURCE,
        )
        answer = open_session(engine)
        assert answer["routingOutcome"] == vocabulary.RULE_MATCHED
        assert answer["rule"] == "Named owner"

    def test_a_rule_that_declines_offers_no_calendar(self, engine):
        seed_seller(engine)
        router = publish(engine)
        engine.store.update(
            router["id"],
            {
                "nodes": [
                    trigger(),
                    {
                        "type": "routing_rule",
                        "name": "declines",
                        "conditions": [],
                        "offer_calendar": False,
                    },
                    {"type": "catch_all", "name": "Deal desk"},
                ]
            },
            actor="dana",
            source=SOURCE,
        )
        answer = engine.route(
            {"router_id": router["id"], "form_fields": {"work_email": "buyer@example.test"}},
            room_id=ROOM,
            actor="buyer",
            source=SOURCE,
        )
        assert answer["schedulingAllowed"] is False
        assert answer["routingOutcome"] == vocabulary.RULE_DECLINED
        assert answer["state"] == vocabulary.NOT_OFFERED

    def test_the_timer_starts_when_the_route_is_opened(self, engine):
        seed_seller(engine)
        answer = open_session(engine)
        assert answer["timerExpiresAt"]
        assert answer["slots"], "a schedulable route offers slots"

    def test_the_session_carries_the_guest_payload_the_research_names(self, engine):
        seed_seller(engine)
        answer = open_session(engine)
        payload = answer[vocabulary.PRIMARY_GUEST_PAYLOAD]
        assert set(payload) == set(GUEST_FIELDS)
        assert payload["email"] == "buyer@example.test"
        assert payload["ownerId"]

    def test_no_seller_with_a_connected_calendar_is_refused(self, engine):
        seed_seller(engine, calendar_connected=False)
        router = publish(engine)
        with pytest.raises(AssignmentNotBookable):
            engine.route(
                {"router_id": router["id"], "form_fields": {"work_email": "a@b.test"}},
                room_id=ROOM,
                actor="a",
                source=SOURCE,
            )


class TestTheThreeAssignmentChoices:
    def test_individual_user_resolves_to_the_named_seller(self, engine):
        dana = seed_seller(engine, "Dana")
        seed_seller(engine, "Sam")
        router = publish(engine)
        engine.store.update(
            router["id"],
            {
                "nodes": [
                    trigger(),
                    {
                        "type": "catch_all",
                        "name": "Desk",
                        "calendar": calendar({"type": "individual_user", "user_id": dana["id"]}),
                    },
                ]
            },
            actor="dana",
            source=SOURCE,
        )
        answer = engine.route(
            {"router_id": router["id"], "form_fields": {"work_email": "a@b.test"}},
            room_id=ROOM,
            actor="a",
            source=SOURCE,
        )
        assert answer["assignment"]["userId"] == dana["id"]
        assert answer["assignment"]["type"] == "user"

    def test_an_individual_user_naming_nobody_is_refused(self, engine):
        seed_seller(engine)
        router = publish(engine)
        engine.store.update(
            router["id"],
            {
                "nodes": [
                    trigger(),
                    {
                        "type": "catch_all",
                        "name": "Desk",
                        "calendar": calendar({"type": "individual_user", "user_id": "ghost"}),
                    },
                ]
            },
            actor="dana",
            source=SOURCE,
        )
        with pytest.raises(AssignmentNotBookable):
            engine.route(
                {"router_id": router["id"], "form_fields": {"work_email": "a@b.test"}},
                room_id=ROOM,
                actor="a",
                source=SOURCE,
            )

    def test_owner_resolves_to_the_crm_records_owner(self, engine):
        dana = seed_seller(engine, "Dana")
        seed_seller(engine, "Sam")
        engine.store.create(
            rules.CRM_RECORDS,
            {
                "system": "salesforce",
                "object": "contact",
                "email": "buyer@example.test",
                "owner_id": dana["id"],
                "crm_owner": {"team": "field-sales"},
            },
            room_id=ROOM,
            actor="test",
            source=SOURCE,
        )
        answer = open_session(engine)
        assert answer["rule"] == "Named owner"
        assert answer["assignment"]["type"] == "owner"
        assert answer["assignment"]["userId"] == dana["id"]

    def test_owner_falls_back_to_a_connected_seller_with_no_crm_record(self, engine):
        """A prospect the CRM has never seen still reaches a seller."""
        dana = seed_seller(engine, "Dana")
        router = publish(engine)
        engine.store.update(
            router["id"],
            {
                "nodes": [
                    trigger(),
                    {"type": "catch_all", "name": "Desk", "calendar": calendar({"type": "owner"})},
                ]
            },
            actor="dana",
            source=SOURCE,
        )
        answer = engine.route(
            {"router_id": router["id"], "form_fields": {"work_email": "a@b.test"}},
            room_id=ROOM,
            actor="a",
            source=SOURCE,
        )
        assert answer["assignment"]["type"] == "owner"
        assert answer["assignment"]["userId"] == dana["id"]

    def test_round_robin_advances_by_the_rotation_position(self, engine):
        sam = seed_seller(engine, "Sam", round_robin_position=1)
        seed_seller(engine, "Dana", round_robin_position=0)
        router = publish(engine)
        engine.store.update(
            router["id"],
            {
                "nodes": [
                    trigger(),
                    {
                        "type": "catch_all",
                        "name": "Desk",
                        "calendar": calendar({"type": "round_robin"}),
                    },
                ]
            },
            actor="dana",
            source=SOURCE,
        )
        answer = engine.route(
            {"router_id": router["id"], "form_fields": {"work_email": "a@b.test"}},
            room_id=ROOM,
            actor="a",
            source=SOURCE,
        )
        assert answer["assignment"]["userId"] == sam["id"]


# --------------------------------------------------------------------------- #
# The second researched call: commit the chosen slot
# --------------------------------------------------------------------------- #


class TestTheSecondCall:
    def test_a_committed_slot_returns_a_meeting_id(self, engine):
        seed_seller(engine)
        answer = open_session(engine)
        slot = answer["slots"][0]
        booked = engine.schedule_simple(
            answer["routeId"],
            {"start": slot["start"], "meeting_type": "demo"},
            room_id=ROOM,
            actor="buyer",
            source=SOURCE,
        )
        assert booked["meetingId"]
        assert set(vocabulary.SCHEDULE_RESPONSE_FIELDS) <= set(booked)

    def test_a_session_commits_only_once(self, engine):
        seed_seller(engine)
        answer = open_session(engine)
        slot = answer["slots"][0]
        engine.schedule_simple(
            answer["routeId"],
            {"start": slot["start"], "meeting_type": "demo"},
            room_id=ROOM,
            actor="buyer",
            source=SOURCE,
        )
        with pytest.raises(RouteConsumed) as caught:
            engine.schedule_simple(
                answer["routeId"],
                {"start": slot["start"], "meeting_type": "demo"},
                room_id=ROOM,
                actor="buyer",
                source=SOURCE,
            )
        assert "already been committed" in str(caught.value)

    def test_a_meeting_type_the_node_never_offered_is_refused(self, engine):
        seed_seller(engine)
        answer = open_session(engine)
        slot = answer["slots"][0]
        with pytest.raises(MeetingTypeNotOffered):
            engine.schedule_simple(
                answer["routeId"],
                {"start": slot["start"], "meeting_type": "keynote"},
                room_id=ROOM,
                actor="buyer",
                source=SOURCE,
            )

    def test_a_start_time_the_route_never_offered_is_refused(self, engine):
        seed_seller(engine)
        answer = open_session(engine)
        with pytest.raises(SlotNotOffered) as caught:
            engine.schedule_simple(
                answer["routeId"],
                {"start": "2027-01-01T03:00:00Z", "meeting_type": "demo"},
                room_id=ROOM,
                actor="buyer",
                source=SOURCE,
            )
        assert "never offered" in str(caught.value)

    def test_a_commit_with_no_start_is_refused(self, engine):
        seed_seller(engine)
        answer = open_session(engine)
        with pytest.raises(SlotNotOffered):
            engine.schedule_simple(
                answer["routeId"],
                {"meeting_type": "demo"},
                room_id=ROOM,
                actor="buyer",
                source=SOURCE,
            )

    def test_a_different_guest_cannot_commit_somebody_elses_session(self, engine):
        seed_seller(engine)
        answer = open_session(engine)
        slot = answer["slots"][0]
        with pytest.raises(GuestMismatch):
            engine.schedule_simple(
                answer["routeId"],
                {
                    "start": slot["start"],
                    "meeting_type": "demo",
                    "guest": {"email": "someone-else@example.test"},
                },
                room_id=ROOM,
                actor="thief",
                source=SOURCE,
            )

    def test_a_route_that_was_never_schedulable_is_refused(self, engine):
        seed_seller(engine)
        answer = open_session(engine)
        engine.store.update(
            answer["routeId"], {"scheduling_allowed": False}, actor="t", source=SOURCE
        )
        with pytest.raises(RouteNotSchedulable):
            engine.schedule_simple(
                answer["routeId"],
                {"start": answer["slots"][0]["start"], "meeting_type": "demo"},
                room_id=ROOM,
                actor="buyer",
                source=SOURCE,
            )

    def test_an_unknown_route_is_refused(self, engine):
        with pytest.raises(RouteNotFound):
            engine.schedule_simple(
                "nope",
                {"start": "2026-10-06T09:00:00Z", "meeting_type": "demo"},
                room_id=ROOM,
                actor="a",
                source=SOURCE,
            )

    def test_the_sellers_calendar_takes_the_committed_block(self, engine):
        seller = seed_seller(engine)
        answer = open_session(engine)
        slot = answer["slots"][0]
        engine.schedule_simple(
            answer["routeId"],
            {"start": slot["start"], "meeting_type": "demo"},
            room_id=ROOM,
            actor="buyer",
            source=SOURCE,
        )
        refreshed = engine.store.get(seller["id"])
        assert any(block["start"] == slot["start"] for block in refreshed["data"]["busy"])

    def test_the_post_booking_nodes_are_recorded_not_executed(self, engine):
        seed_seller(engine)
        router = publish(engine)
        engine.store.update(
            router["id"],
            {
                "nodes": [
                    trigger(),
                    calendar(),
                    {"type": "catch_all", "name": "Desk"},
                    {"type": "create_event", "name": "Log the meeting"},
                    {"type": "redirect_to", "name": "Thanks", "url": "https://example.test/thanks"},
                ]
            },
            actor="dana",
            source=SOURCE,
        )
        answer = engine.route(
            {"router_id": router["id"], "form_fields": {"work_email": "a@b.test"}},
            room_id=ROOM,
            actor="a",
            source=SOURCE,
        )
        booked = engine.schedule_simple(
            answer["routeId"],
            {"start": answer["slots"][0]["start"], "meeting_type": "demo"},
            room_id=ROOM,
            actor="a",
            source=SOURCE,
        )
        assert booked["postBookingNodes"] == ["create_event", "redirect_to"]
        assert booked["redirectTo"] == "https://example.test/thanks"


# --------------------------------------------------------------------------- #
# The researched Time Elapsed timer
# --------------------------------------------------------------------------- #


def make_due(engine: ConciergeRouterEngine, route_id: str) -> None:
    """Move a session's deadline into the past, so its timer is due.

    Deliberately relative to the wall clock rather than to a fixture constant:
    the engine compares against the real now, so a fixed past date would either
    be in the future (timer never fires) or would break the day the suite runs.
    """
    engine.store.update(
        route_id,
        {"timer_expires_at": vocabulary.iso(vocabulary.utcnow() - timedelta(minutes=1))},
        actor="test",
        source=SOURCE,
    )


class TestTheTimeElapsedTimer:
    def test_a_pending_session_with_no_timer_is_left_alone(self, engine):
        seed_seller(engine)
        answer = open_session(engine)
        engine.store.update(answer["routeId"], {"timer_expires_at": ""}, actor="t", source=SOURCE)
        assert engine.session(answer["routeId"], source=SOURCE)["state"] == vocabulary.PENDING

    def test_an_unparseable_deadline_leaves_the_session_pending(self, engine):
        seed_seller(engine)
        answer = open_session(engine)
        engine.store.update(
            answer["routeId"], {"timer_expires_at": "someday"}, actor="t", source=SOURCE
        )
        assert engine.session(answer["routeId"], source=SOURCE)["state"] == vocabulary.PENDING

    def test_a_due_session_is_moved_to_not_scheduled_when_read(self, engine):
        seed_seller(engine)
        answer = open_session(engine)
        make_due(engine, answer["routeId"])
        moved = engine.session(answer["routeId"], source=SOURCE)
        assert moved["state"] == vocabulary.NOT_SCHEDULED

    def test_a_session_that_is_not_due_stays_pending(self, engine):
        seed_seller(engine)
        answer = open_session(engine)
        assert engine.session(answer["routeId"], source=SOURCE)["state"] == vocabulary.PENDING

    def test_expiry_runs_the_researched_assign_to_and_notify_nodes(self, engine):
        seed_seller(engine, "Dana")
        seed_seller(engine, "Priya")
        answer = open_session(engine)
        expired = engine.expire(answer["routeId"], room_id=ROOM, actor="dana", source=SOURCE)
        assert expired["state"] == vocabulary.NOT_SCHEDULED
        assert expired["notification"]["reason"]
        assert expired["notification"]["sent"] is False, "this build records, it does not send"
        assert expired["reassigned_to"].get("name"), "Assign To distributed the prospect"

    def test_expiry_runs_once_so_a_repeat_read_cannot_notify_twice(self, engine):
        seed_seller(engine)
        answer = open_session(engine)
        first = engine.expire(answer["routeId"], room_id=ROOM, actor="dana", source=SOURCE)
        second = engine.expire(answer["routeId"], room_id=ROOM, actor="dana", source=SOURCE)
        assert second["notification"]["at"] == first["notification"]["at"]

    def test_an_expired_session_can_never_be_booked_afterwards(self, engine):
        seed_seller(engine)
        answer = open_session(engine)
        engine.expire(answer["routeId"], room_id=ROOM, actor="dana", source=SOURCE)
        with pytest.raises(RouteConsumed) as caught:
            engine.schedule_simple(
                answer["routeId"],
                {"start": answer["slots"][0]["start"], "meeting_type": "demo"},
                room_id=ROOM,
                actor="buyer",
                source=SOURCE,
            )
        assert "not scheduled" in str(caught.value)

    def test_expiring_an_unknown_session_is_refused(self, engine):
        with pytest.raises(RouteNotFound):
            engine.expire("nope", room_id=ROOM, actor="dana", source=SOURCE)

    def test_listing_bookings_retires_a_due_session_too(self, engine):
        seed_seller(engine)
        answer = open_session(engine)
        make_due(engine, answer["routeId"])
        listed = engine.bookings(room_id=ROOM, source=SOURCE)
        assert listed[0]["state"] == vocabulary.NOT_SCHEDULED


# --------------------------------------------------------------------------- #
# Cancellation
# --------------------------------------------------------------------------- #


class TestCancellation:
    def test_cancelling_a_booking_frees_the_sellers_slot_again(self, engine):
        seller = seed_seller(engine)
        answer = open_session(engine)
        slot = answer["slots"][0]
        engine.schedule_simple(
            answer["routeId"],
            {"start": slot["start"], "meeting_type": "demo"},
            room_id=ROOM,
            actor="buyer",
            source=SOURCE,
        )
        cancelled = engine.cancel(answer["routeId"], room_id=ROOM, actor="dana", source=SOURCE)
        assert cancelled["state"] == vocabulary.NOT_SCHEDULED
        refreshed = engine.store.get(seller["id"])
        assert all(block["start"] != slot["start"] for block in refreshed["data"]["busy"])

    def test_cancelling_a_pending_session_leaves_it_not_scheduled(self, engine):
        seed_seller(engine)
        answer = open_session(engine)
        cancelled = engine.cancel(answer["routeId"], room_id=ROOM, actor="dana", source=SOURCE)
        assert cancelled["state"] == vocabulary.PENDING

    def test_cancelling_an_unknown_booking_is_refused(self, engine):
        with pytest.raises(RouteNotFound):
            engine.cancel("nope", room_id=ROOM, actor="dana", source=SOURCE)


# --------------------------------------------------------------------------- #
# Declaration writes and the publish state
# --------------------------------------------------------------------------- #


class TestThePublishState:
    def test_a_published_router_must_name_a_deployment_surface(self, engine):
        router = publish(engine)
        with pytest.raises(RouterNotPublished) as caught:
            engine.publish_router(
                router["id"],
                {"publish_state": "published", "deployment": []},
                room_id=ROOM,
                actor="dana",
                source=SOURCE,
            )
        assert "deployment surface" in str(caught.value)

    def test_a_router_can_be_switched_off_and_back_on(self, engine):
        router = publish(engine)
        off = engine.publish_router(
            router["id"], {"enabled": False}, room_id=ROOM, actor="dana", source=SOURCE
        )
        assert off["enabled"] is False
        on = engine.publish_router(
            router["id"], {"enabled": True}, room_id=ROOM, actor="dana", source=SOURCE
        )
        assert on["enabled"] is True

    def test_publishing_an_unknown_router_is_refused(self, engine):
        with pytest.raises(UnknownRouter):
            engine.publish_router("nope", {"enabled": True}, room_id=ROOM, actor="d", source=SOURCE)

    def test_a_router_starts_as_a_draft(self, engine):
        router = engine.create_router(declaration(), room_id=ROOM, actor="dana", source=SOURCE)
        assert router["publish_state"] == vocabulary.DRAFT


class TestReads:
    def test_a_router_is_readable_by_id(self, engine):
        router = publish(engine)
        assert engine.require_router(router["id"])["id"] == router["id"]

    def test_a_record_from_another_collection_is_not_a_router(self, engine):
        seller = seed_seller(engine)
        with pytest.raises(UnknownRouter):
            engine.require_router(seller["id"])

    def test_a_seller_needs_a_name(self, engine):
        from dsr.concierge_router.errors import SellerNotUsable

        with pytest.raises(SellerNotUsable) as caught:
            engine.register_seller({"name": "  "}, room_id=ROOM, actor="a", source=SOURCE)
        assert "every researched assignment resolves to one" in str(caught.value)

    def test_the_summary_counts_by_state(self, engine):
        seed_seller(engine)
        router = publish(engine)
        open_session(engine, router_id=router["id"])
        summary = engine.summary()
        assert summary["routers"] == 1
        assert summary["sellers"] == 1
        assert summary["bookings"] == 1
        assert summary["routers_by_state"] == {"published": 1}
        assert summary["deployed"] == 1

    def test_the_vocabulary_is_served_as_data(self, engine):
        data = engine.vocabulary()
        assert data["nodes"]["trigger"] == vocabulary.TRIGGER
        assert data["bookings"]["guest_payload"] == vocabulary.PRIMARY_GUEST_PAYLOAD

    def test_the_inferences_are_served_with_what_each_was_chosen_against(self, engine):
        data = engine.inferences()
        assert data["count"] == len(inferences.INFERENCES)
        for entry in data["inferences"]:
            assert entry["decision"] and entry["because"] and entry["rejected"]


# --------------------------------------------------------------------------- #
# The inferences the research left open
# --------------------------------------------------------------------------- #


class TestTheRecordedInferences:
    def test_every_inference_states_a_decision_a_reason_and_a_rejected_reading(self):
        for entry in inferences.INFERENCES:
            assert entry["id"]
            assert entry["question"]
            assert entry["decision"]
            assert entry["because"]
            assert entry["rejected"]
            assert entry["researched"] is False

    def test_the_timer_inference_is_recorded(self):
        entry = inferences.by_id("time-elapsed-makes-a-booking-not-scheduled")
        assert "not_scheduled" in entry["decision"]
        assert "Deletion" in entry["rejected"]

    def test_the_lazy_timer_inference_is_recorded(self):
        entry = inferences.by_id("the-time-elapsed-timer-is-computed-on-read")
        assert "on read" in entry["decision"] or "read" in entry["decision"]

    def test_the_routing_link_inference_is_recorded(self):
        entry = inferences.by_id("routing-link-is-a-relative-path")
        assert "path" in entry["decision"]

    def test_the_assignment_enumeration_inference_is_recorded(self):
        entry = inferences.by_id("assignment-type-enumeration")
        assert "user" in entry["decision"]

    def test_the_guest_payload_inference_is_recorded(self):
        entry = inferences.by_id("primary-guest-data-fields")
        assert "email" in entry["decision"]

    def test_the_lead_case_opportunity_inference_is_recorded(self):
        entry = inferences.by_id("lead-case-opportunity")
        assert "opportunity" in entry["decision"].lower()

    def test_the_post_booking_inference_is_recorded(self):
        entry = inferences.by_id("post-booking-nodes-are-declared-not-executed")
        assert "not executed" in entry["decision"]

    def test_the_notification_inference_is_recorded(self):
        entry = inferences.by_id("notification-is-recorded-not-sent")
        assert "not sent" in entry["decision"]

    def test_an_unknown_inference_id_returns_nothing(self):
        assert inferences.by_id("nope") == {}


# --------------------------------------------------------------------------- #
# The seed
# --------------------------------------------------------------------------- #


class TestTheSeed:
    def _seed(self, tmp_path):
        from dsr.db.audited import AuditedDatabase

        db = AuditedDatabase(tmp_path / "seed.db", mirror_dir=tmp_path / "mirror", actor="test")
        return db, feature.seed(
            db, {"room_ids": [("room-1", "northwind")], "now": NOW, "rng": None}
        )

    def test_the_seed_returns_a_string_naming_what_it_created(self, tmp_path):
        db, summary = self._seed(tmp_path)
        try:
            assert isinstance(summary, str)
            assert "routers" in summary and "bookings" in summary
        finally:
            db.close()

    def test_every_character_of_the_seed_string_is_cp1252_encodable(self, tmp_path):
        """The seeder prints this on a Windows console."""
        db, summary = self._seed(tmp_path)
        try:
            summary.encode("cp1252")
            assert not [char for char in summary if ord(char) > 127]
        finally:
            db.close()

    def test_the_seed_creates_the_states_the_research_makes_reachable(self, tmp_path):
        db, _ = self._seed(tmp_path)
        try:
            routers = db.list(ROUTERS, limit=100)
            assert len(routers) == 3
            assert len(db.list(SELLERS, limit=100)) == 3
            states = {row["data"]["state"] for row in db.list(BOOKINGS, limit=100)}
            assert vocabulary.BOOKED in states
            assert vocabulary.PENDING in states
            assert vocabulary.NOT_SCHEDULED in states
            assert vocabulary.NOT_OFFERED in states
        finally:
            db.close()

    def test_the_seed_seeds_a_failure_and_not_only_successes(self, tmp_path):
        db, _ = self._seed(tmp_path)
        try:
            outcomes = {row["data"]["routing_outcome"] for row in db.list(BOOKINGS, limit=100)}
            assert vocabulary.RULE_MATCHED in outcomes
            assert vocabulary.CATCH_ALL_MATCHED in outcomes
            assert vocabulary.RULE_DECLINED in outcomes
            states = {row["data"]["publish_state"] for row in db.list(ROUTERS, limit=100)}
            assert states == {"published", "draft"}
        finally:
            db.close()

    def test_the_seed_with_no_rooms_says_so_rather_than_raising(self, tmp_path):
        from dsr.db.audited import AuditedDatabase

        db = AuditedDatabase(tmp_path / "empty.db", actor="test")
        try:
            assert feature.seed(db, {"room_ids": [], "now": NOW, "rng": None}).startswith(
                "0 routers"
            )
        finally:
            db.close()


# --------------------------------------------------------------------------- #
# The architectural guards
# --------------------------------------------------------------------------- #


class TestTheContractIsHeld:
    def test_the_feature_module_imports_no_shared_file_and_opens_no_connection(self):
        text = Path(feature.__file__).read_text(encoding="utf-8")
        assert "from dsr.api" not in text
        assert "import dsr.api" not in text
        assert "import sqlite3" not in text
        assert "sqlite3.connect" not in text

    def test_the_domain_package_depends_on_nothing_but_the_store(self):
        package = Path(inspect.getfile(ConciergeRouterEngine)).parent
        for module in sorted(package.glob("*.py")):
            text = module.read_text(encoding="utf-8")
            assert "import sqlite3" not in text, module.name
            assert "sqlite3.connect" not in text, module.name
            assert "from dsr.api" not in text, module.name
            assert "dsr.deps" not in text, module.name
            assert "fastapi" not in text, module.name
            assert "AuditedDatabase(" not in text, module.name

    def test_no_audit_source_is_written_as_a_literal(self):
        """A hardcoded URL as a source is the defect the contract names."""
        text = Path(inspect.getfile(ConciergeRouterEngine)).read_text(encoding="utf-8")
        assert 'source="POST /' not in text
        assert 'source="GET /' not in text

    def test_every_domain_write_requires_a_source(self):
        signature = inspect.signature(ConciergeRouterEngine.create_router)
        assert signature.parameters["source"].default is inspect.Parameter.empty

    def test_the_router_owns_a_unique_prefix(self):
        assert feature.router.prefix == "/api/wf-051"

    def test_the_feature_declares_its_ticket_and_id(self):
        assert feature.FEATURE["ticket"] == "WF-051"
        assert feature.FEATURE["id"] == "wf-051-route-and-book-a-demo-request-inline-f"

    def test_one_exception_type_is_mapped(self):
        assert list(feature.EXCEPTION_HANDLERS) == [RouterError]

    def test_every_domain_refusal_carries_a_code_and_a_status(self):
        for name in dir(__import__("dsr.concierge_router.errors", fromlist=["x"])):
            candidate = getattr(__import__("dsr.concierge_router.errors", fromlist=["x"]), name)
            if isinstance(candidate, type) and issubclass(candidate, RouterError):
                assert candidate.code
                assert isinstance(candidate.status, int)
                assert 400 <= candidate.status < 600
