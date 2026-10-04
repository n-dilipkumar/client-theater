"""WF-052 domain tests: the pure rules, the engine, the seed, and the guarantee.

What is under test, and why
---------------------------

The HTTP surface is in ``tests/test052_http.py``. What is here is the layer under
it, where the claims this workflow exists to make actually live:

* ``qualify`` **writes nothing**. Counted, not asserted: the row count and the
  audit count are compared across the call.
* The ``routeId`` is **derived, not stored**, so the same lead answers with the
  same id and no session exists for it.
* A body carrying an ``interval`` is **refused**, because that one field is the
  researched difference between this workflow and the booking one.
* A chain with **no catch-all cannot be saved**, because the floor is what makes
  every inbound lead acknowledged.
* ``availability is not queried``: no read in this package touches a calendar
  collection, and the counters say so in the answer itself.
* The seed returns a string **cp1252 can encode**, because the seeder prints it
  to a Windows console.

Isolation: the fixtures come from ``conftest.py``. ``store`` is a fresh, empty,
in-memory audited database per test, so this file passes on its own and under
``pytest-xdist``, and no test here depends on the order another ran in.
"""

from __future__ import annotations

from typing import Any

import pytest
from dsr.db.audited import AuditedDatabase
from dsr.lead_qualification import (
    ASSIGNMENT_TYPES,
    COLLECTIONS,
    NO_SIDE_EFFECTS,
    OPERATORS,
    QUOTES,
    RULE_KINDS,
    VERDICTS,
    LeadQualificationEngine,
    QualificationError,
    compare,
    describe,
    dotted_get,
    evaluate,
    parse_rules,
    published_vocabulary,
    refuses_interval,
    require_room,
    route_id_for,
    routing_link_for,
    validate_router,
)
from dsr.lead_qualification.errors import (
    AssigneeNotFound,
    AssigneeRefused,
    IntervalSupplied,
    PayloadRefused,
    RoomRequired,
    RouterAlreadyExists,
    RouterDisabled,
    RouterNotFound,
    RouterRefused,
    VerdictNotFound,
)
from dsr.lead_qualification.rules import CATCH_ALL, Condition, Rule, canonical
from dsr.store import RecordStore

CLOCK = "2026-10-04T09:00:00.000+00:00"

NADIA = "005-nadia"
DESK = "005-desk"
PRIYA = "005-priya"

ENTERPRISE_RULE: dict[str, Any] = {
    "kind": "data_field",
    "name": "Enterprise seat count",
    "conditions": [
        {
            "kind": "data_field",
            "source": "form",
            "field": "seats",
            "operator": "gte",
            "value": 200,
        }
    ],
    "assign_user_id": NADIA,
}

HOT_RULE: dict[str, Any] = {
    "kind": "crm_field",
    "name": "Hot contact is handled without a scheduler",
    "conditions": [
        {
            "kind": "crm_field",
            "source": "lead",
            "field": "rating",
            "operator": "equals",
            "value": "hot",
        }
    ],
    "assign_user_id": PRIYA,
    "scheduling_allowed": False,
    "crm_writeback": {"rating": "hot"},
}

CATCH_ALL_RULE: dict[str, Any] = {
    "kind": CATCH_ALL,
    "name": "Deal desk takes the rest",
    "assign_user_id": DESK,
}

CHAIN = [ENTERPRISE_RULE, HOT_RULE, CATCH_ALL_RULE]


def count_rows(store: RecordStore, collection: str) -> int:
    """``RecordStore`` exposes ``count_where``; an empty filter counts the collection."""
    return store.count_where(collection, {})


def router_spec(**overrides: Any) -> dict[str, Any]:
    """A valid router declaration, so a test only states the field it is about."""
    spec: dict[str, Any] = {
        "router_slug": "demo-request",
        "name": "Demo requests",
        "tenant": "soluspring.chilipiper.example",
        "rules": [dict(rule) for rule in CHAIN],
    }
    spec.update(overrides)
    return spec


@pytest.fixture
def engine(store: RecordStore) -> LeadQualificationEngine:
    """An engine on an empty database, with a clock the test holds still."""
    return LeadQualificationEngine(store, clock=lambda: CLOCK)


@pytest.fixture
def workspace(engine: LeadQualificationEngine) -> LeadQualificationEngine:
    """Two assignees and one valid router, which is the smallest useful world."""
    engine.create_assignee({"user_id": NADIA, "name": "Nadia"}, actor="t", source="test")
    engine.create_assignee({"user_id": DESK, "name": "Deal Desk"}, actor="t", source="test")
    engine.create_router(router_spec(), actor="t", source="test")
    return engine


# --------------------------------------------------------------------------- #
# The vocabulary
# --------------------------------------------------------------------------- #


def test_the_vocabulary_publishes_every_picker_the_page_needs():
    published = published_vocabulary()
    assert published["verdicts"] == list(VERDICTS)
    assert published["rule_kinds"] == list(RULE_KINDS)
    assert published["operators"] == list(OPERATORS)
    assert published["assignment_types"] == list(ASSIGNMENT_TYPES)
    assert published["interval_field"] == "interval"
    assert published["edge_call"].endswith("/rest")


def test_the_two_access_patterns_are_published_with_their_consequences():
    patterns = published_vocabulary()["access_patterns"]
    assert patterns["return_a_booking_url"]["request"] == "form data (no interval)"
    assert patterns["return_a_booking_url"]["consumes_a_session"] is False
    assert patterns["schedule_programmatically"]["consumes_a_session"] is True
    assert patterns["return_a_booking_url"]["in_this_workflow"] is True
    assert patterns["schedule_programmatically"]["in_this_workflow"] is False


def test_the_guarantees_are_published_as_data_not_as_a_comment():
    guarantees = published_vocabulary()["guarantees"]
    assert guarantees == {
        "consumes_no_session": True,
        "queries_no_availability": True,
        "computes_no_slots": True,
        "fires_no_automation": True,
        "assigns_nobody": True,
        "reads_no_calendar": True,
    }


def test_every_quote_is_attributed_to_the_research():
    assert "No routing session is consumed" in QUOTES["no_session"]
    assert "interval" in QUOTES["distinguishing_field"]
    assert "schedulingAllowed" in QUOTES["gate_signal"]
    assert "Catch All" in QUOTES["catch_all"]


def test_the_two_caller_paths_are_both_published():
    paths = published_vocabulary()["caller_paths"]
    assert set(paths) == {"redirect_later", "crm_writeback"}
    assert paths["redirect_later"]["writes_here"] is False
    assert paths["crm_writeback"]["writes_here"] is True


# --------------------------------------------------------------------------- #
# Pure: comparing one field
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "operator,actual,expected,expected_result",
    [
        ("equals", "Hot", "hot", True),
        ("equals", " hot ", "HOT", True),
        ("equals", "warm", "hot", False),
        ("not_equals", "warm", "hot", True),
        ("not_equals", "hot", "hot", False),
        ("contains", "Enterprise trial", "prise", True),
        ("contains", "SMB", "prise", False),
        ("in", "emea", ["emea", "apac"], True),
        ("in", "latam", ["emea", "apac"], False),
        ("in", "apac", "apac", True),
        ("exists", "anything", None, True),
        ("exists", None, None, False),
        ("gt", 400, 200, True),
        ("gt", 100, 200, False),
        ("gte", 200, 200, True),
        ("lt", 100, 200, True),
        ("lt", 300, 200, False),
        ("lte", 200, 200, True),
        ("unknown_operator", "a", "a", False),
    ],
)
def test_compare_covers_every_operator(operator, actual, expected, expected_result):
    assert compare(operator, actual, expected) is expected_result


def test_an_ordered_comparison_never_matches_a_non_number():
    """`True` is not a lead count, and a rule about size must not match it."""
    assert compare("gte", "many", 200) is False
    assert compare("gte", None, 200) is False
    assert compare("gte", True, 0) is False


def test_a_comparison_against_a_missing_field_is_false():
    assert compare("equals", None, "hot") is False
    assert compare("contains", None, "x") is False
    assert compare("gt", None, 1) is False


def test_dotted_get_separates_a_missing_field_from_a_none_field():
    assert dotted_get({"a": {"b": 1}}, "a.b") == (True, 1)
    assert dotted_get({"a": {"b": None}}, "a.b") == (True, None)
    assert dotted_get({"a": 1}, "a.b") == (False, None)
    assert dotted_get({}, "a") == (False, None)
    assert dotted_get(None, "a") == (False, None)


def test_canonical_is_stable_across_key_order():
    assert canonical({"b": 1, "a": 2}) == canonical({"a": 2, "b": 1})


# --------------------------------------------------------------------------- #
# Pure: the derived route id and the link
# --------------------------------------------------------------------------- #


def test_the_route_id_is_derived_so_the_same_lead_answers_with_the_same_one():
    payload = {"form": {"email": "a@northwind.example", "seats": 400}, "crm": {}}
    first = route_id_for("demo-request", payload)
    second = route_id_for("demo-request", dict(reversed(list(payload.items()))))
    assert first == second
    assert first != route_id_for("other-router", payload)
    assert first != route_id_for("demo-request", {"form": {"email": "b@northwind.example"}})


def test_the_route_id_is_shaped_like_the_researched_sample():
    route_id = route_id_for("demo-request", {"form": {"email": "a@x.example"}})
    parts = route_id.split("-")
    assert [len(part) for part in parts] == [8, 4, 4, 4, 12]


def test_the_routing_link_is_built_the_way_the_researched_sample_is():
    link = routing_link_for("soluspring.chilipiper.example", "demo-request", "9413f879-1")
    assert (
        link
        == "https://soluspring.chilipiper.example/concierge-router/demo-request/routing/9413f879-1"
    )


def test_the_routing_link_falls_back_to_the_researched_tenant_and_adds_a_scheme():
    assert routing_link_for("", "slug", "r").startswith("https://your-tenant.chilipiper.com/")
    assert routing_link_for("https://host.example/", "slug", "r") == (
        "https://host.example/concierge-router/slug/routing/r"
    )


# --------------------------------------------------------------------------- #
# Pure: the chain
# --------------------------------------------------------------------------- #


def test_the_first_rule_that_holds_wins_and_nothing_after_it_is_evaluated():
    rules = parse_rules(CHAIN)
    match = evaluate(rules, {"seats": 400, "email": "a@x.example"}, {"lead": {"rating": "hot"}})
    assert match.matched is True
    assert match.rule.name == ENTERPRISE_RULE["name"]
    assert match.evaluated == 1, "a rule after the match must not be read"


def test_a_rule_with_no_conditions_is_the_catch_all_and_matches_anything():
    rules = parse_rules([CATCH_ALL_RULE])
    match = evaluate(rules, {}, {})
    assert match.matched is True
    assert match.fallthrough is False
    assert match.rule.is_catch_all is True


def test_a_chain_with_nothing_to_match_reports_the_fallthrough():
    match = evaluate(parse_rules([ENTERPRISE_RULE]), {"seats": 1}, {})
    assert match.matched is False
    assert match.fallthrough is True
    assert match.evaluated == 1
    assert match.conditions[0]["matched"] is False


def test_a_chain_with_no_rules_at_all_is_an_empty_fallthrough():
    match = evaluate(parse_rules(None), {"seats": 1}, {})
    assert match.matched is False
    assert match.conditions == []


def test_a_malformed_rule_entry_is_skipped_rather_than_raising():
    rules = parse_rules([CATCH_ALL_RULE, "not an object", ENTERPRISE_RULE])
    assert [rule.name for rule in rules] == [CATCH_ALL_RULE["name"], ENTERPRISE_RULE["name"]]


def test_a_crm_field_condition_reads_the_object_the_caller_supplied():
    condition = Condition.parse({"kind": "crm_field", "source": "account", "field": "tier"})
    outcome = condition.evaluate({}, {"account": {"tier": "strategic"}})
    assert outcome["matched"] is False, "exists with no value is false"
    found = Condition.parse(
        {
            "kind": "crm_field",
            "source": "account",
            "field": "tier",
            "operator": "equals",
            "value": "strategic",
        }
    ).evaluate({}, {"account": {"tier": "strategic"}})
    assert found["matched"] is True
    assert found["actual"] == "strategic"


def test_a_rule_parse_names_an_unnamed_rule_by_its_position():
    rules = parse_rules(
        [{"kind": CATCH_ALL, "assign_user_id": DESK}, {"kind": CATCH_ALL, "assign_user_id": DESK}]
    )
    assert [rule.name for rule in rules] == ["rule 1", "rule 2"]
    assert [rule.index for rule in rules] == [0, 1]


def test_a_rule_parses_without_a_writeback_and_with_one():
    assert parse_rules([CATCH_ALL_RULE])[0].crm_writeback is None
    assert parse_rules([HOT_RULE])[0].crm_writeback == {"rating": "hot"}
    assert parse_rules([{"kind": CATCH_ALL, "crm_writeback": "no"}])[0].crm_writeback is None


def test_a_rule_without_conditions_reports_itself_as_the_catch_all():
    assert Rule.parse(CATCH_ALL_RULE, 0).is_catch_all is True
    assert Rule.parse(ENTERPRISE_RULE, 0).is_catch_all is False


# --------------------------------------------------------------------------- #
# Pure: what a router declaration may say
# --------------------------------------------------------------------------- #


def test_a_valid_router_has_no_problems():
    assert validate_router(router_spec()) == []


@pytest.mark.parametrize(
    "override,expected",
    [
        ({"router_slug": ""}, "router_slug is required"),
        ({"router_slug": "   "}, "router_slug is required"),
        ({"name": ""}, "name is required"),
        ({"rules": []}, "rules is required"),
        ({"rules": "not a list"}, "rules is required"),
        ({"rules": ["not an object"]}, "rule 1 is not an object"),
    ],
)
def test_a_malformed_router_is_named_field_by_field(override, expected):
    assert any(expected in problem for problem in validate_router(router_spec(**override)))


def test_a_chain_with_no_catch_all_is_refused_with_the_researched_sentence():
    problems = validate_router(router_spec(rules=[ENTERPRISE_RULE]))
    assert any("Catch All" in problem for problem in problems)


def test_two_catch_alls_are_refused_because_the_floor_is_one_node():
    problems = validate_router(router_spec(rules=[CATCH_ALL_RULE, dict(CATCH_ALL_RULE)]))
    assert any("Only one catch_all" in problem for problem in problems)


def test_a_catch_all_that_is_not_last_is_refused_because_nothing_reaches_it():
    problems = validate_router(router_spec(rules=[dict(CATCH_ALL_RULE), {**ENTERPRISE_RULE}]))
    assert any("must be the last rule" in problem for problem in problems)


def test_an_unknown_rule_kind_is_refused_and_names_the_kinds_this_workflow_reads():
    problems = validate_router(
        router_spec(rules=[{"kind": "crm_ownership", "assign_user_id": NADIA}])
    )
    assert any("Unknown rule kind" in problem for problem in problems)
    assert any("data_field" in problem for problem in problems)


@pytest.mark.parametrize(
    "condition,expected",
    [
        ({"kind": "wat", "source": "form", "field": "a"}, "unknown kind"),
        ({"kind": CATCH_ALL, "source": "form", "field": "a"}, "unknown kind"),
        ({"kind": "data_field", "source": "website", "field": "a"}, "not readable"),
        ({"kind": "data_field", "source": "form", "field": ""}, "names no field"),
        (
            {"kind": "data_field", "source": "form", "field": "a", "operator": "sounds_like"},
            "unknown operator",
        ),
    ],
)
def test_a_malformed_condition_is_refused(condition, expected):
    rule = {"kind": "data_field", "name": "R", "conditions": [condition], "assign_user_id": NADIA}
    problems = validate_router(router_spec(rules=[rule, dict(CATCH_ALL_RULE)]))
    assert any(expected in problem for problem in problems), problems


def test_a_condition_that_is_not_an_object_is_refused():
    rule = {"kind": "data_field", "name": "R", "conditions": ["nope"], "assign_user_id": NADIA}
    problems = validate_router(router_spec(rules=[rule, dict(CATCH_ALL_RULE)]))
    assert any("is not an object" in problem for problem in problems)


def test_a_rule_with_no_conditions_is_refused_because_it_would_match_every_lead():
    rule = {"kind": "data_field", "name": "R", "assign_user_id": NADIA}
    problems = validate_router(router_spec(rules=[rule, dict(CATCH_ALL_RULE)]))
    assert any("would match every lead" in problem for problem in problems)


def test_a_rule_with_more_conditions_than_the_limit_is_refused():
    conditions = [
        {
            "kind": "data_field",
            "source": "form",
            "field": f"f{index}",
            "operator": "equals",
            "value": 1,
        }
        for index in range(6)
    ]
    rule = {"kind": "data_field", "name": "R", "conditions": conditions, "assign_user_id": NADIA}
    problems = validate_router(router_spec(rules=[rule, dict(CATCH_ALL_RULE)]))
    assert any("the limit is" in problem for problem in problems)


def test_a_rule_that_names_no_owner_is_refused():
    rule = {**ENTERPRISE_RULE, "assign_user_id": ""}
    problems = validate_router(router_spec(rules=[rule, dict(CATCH_ALL_RULE)]))
    assert any("names no assign_user_id" in problem for problem in problems)


def test_a_catch_all_that_carries_conditions_is_refused():
    rule = {**CATCH_ALL_RULE, "conditions": ENTERPRISE_RULE["conditions"]}
    problems = validate_router(router_spec(rules=[rule]))
    assert any("must not carry conditions" in problem for problem in problems)


def test_a_writeback_that_is_not_an_object_is_refused():
    rule = {**ENTERPRISE_RULE, "crm_writeback": "nope"}
    problems = validate_router(router_spec(rules=[rule, dict(CATCH_ALL_RULE)]))
    assert any("crm_writeback that is not an object" in problem for problem in problems)


def test_an_unreachable_problem_list_returns_early_when_there_are_no_rules():
    """No rules means no chain to walk, so the per-rule loop adds nothing."""
    assert validate_router({"router_slug": "x", "name": "X", "rules": []}) == [
        "rules is required; a router with no rules routes no lead."
    ]


# --------------------------------------------------------------------------- #
# The interval, the one field that switches workflow
# --------------------------------------------------------------------------- #


def test_refuses_interval_sees_the_field_at_the_top_level_only():
    assert refuses_interval({"interval": {"start": "s"}}) is True
    assert refuses_interval({"form": {"interval": "their own field name"}}) is False
    assert refuses_interval({}) is False


def test_a_body_carrying_an_interval_is_refused_before_the_router_is_even_read():
    with pytest.raises(IntervalSupplied) as caught:
        LeadQualificationEngine(RecordStore(AuditedDatabase(":memory:"))).qualify(
            "absent", {"form": {"a": 1}, "interval": {"start": "s"}}
        )
    assert caught.value.status == 400
    assert caught.value.code == "interval_supplied"
    assert "difference is whether you pass an interval" in caught.value.detail


# --------------------------------------------------------------------------- #
# The guarantee: qualify writes nothing
# --------------------------------------------------------------------------- #


def test_qualify_writes_no_record_and_no_audit_row(workspace: LeadQualificationEngine):
    """The researched guarantee, counted rather than asserted."""
    before_rows = sum(count_rows(workspace.store, name) for name in COLLECTIONS)
    before_audit = len(workspace.store.audit(limit=1000))

    for _ in range(3):
        workspace.qualify("demo-request", {"form": {"email": "a@x.example", "seats": 400}})

    assert sum(count_rows(workspace.store, name) for name in COLLECTIONS) == before_rows
    assert len(workspace.store.audit(limit=1000)) == before_audit


def test_qualify_reads_no_collection_but_its_own(workspace: LeadQualificationEngine):
    """``availability is not queried``: no calendar collection is read or written."""
    read_collections: set[str] = set()
    original = workspace.store.list

    def spy(collection, **kwargs):
        read_collections.add(collection)
        return original(collection, **kwargs)

    workspace.store.list = spy  # type: ignore[method-assign]
    workspace.qualify("demo-request", {"form": {"email": "a@x.example", "seats": 400}})
    workspace.store.list = original  # type: ignore[method-assign]

    assert read_collections <= {COLLECTIONS[0], COLLECTIONS[1]}


def test_the_answer_carries_the_counters_so_the_guarantee_is_visible(workspace):
    answer = workspace.qualify("demo-request", {"form": {"email": "a@x.example", "seats": 400}})
    assert answer["side_effects"] == NO_SIDE_EFFECTS
    assert answer["interval_supplied"] is False
    assert answer["side_effects"]["routing_sessions_consumed"] == 0
    assert answer["side_effects"]["availability_queries"] == 0
    assert answer["side_effects"]["slots_computed"] == 0
    assert answer["side_effects"]["automations_fired"] == 0


def test_no_record_in_any_collection_carries_a_route_id_as_its_id(workspace):
    """The id is a digest, so nothing in the store is *the* session."""
    workspace.qualify("demo-request", {"form": {"email": "a@x.example", "seats": 400}})
    assert count_rows(workspace.store, "routing_session") == 0
    assert workspace.store.list("lead_qualification_verdict") == []


# --------------------------------------------------------------------------- #
# The researched call
# --------------------------------------------------------------------------- #


def test_a_qualified_answer_carries_the_researched_fields_in_both_spellings(workspace):
    answer = workspace.qualify("demo-request", {"form": {"email": "a@x.example", "seats": 400}})
    assert answer["verdict"] == "qualified"
    assert answer["scheduling_allowed"] is True
    assert answer["schedulingAllowed"] is True
    assert answer["assignment"] == {"userId": NADIA, "type": "user"}
    assert answer["route_id"] == answer["routeId"]
    assert answer["routing_link"] == answer["routingLink"]
    assert answer["routing_link"].endswith(f"/routing/{answer['route_id']}")
    assert answer["router_slug"] == "demo-request"
    assert answer["evaluated_at"] == CLOCK
    assert answer["evaluated_rules"] == 1
    assert answer["fallthrough"] is False


def test_a_refused_scheduler_is_the_gate_the_research_names(workspace):
    workspace.create_assignee({"user_id": PRIYA, "name": "Priya"}, actor="t", source="test")
    answer = workspace.qualify(
        "demo-request",
        {"form": {"email": "a@x.example", "seats": 5}, "crm": {"lead": {"rating": "hot"}}},
    )
    assert answer["verdict"] == "not_scheduled"
    assert answer["scheduling_allowed"] is False
    assert answer["schedulingAllowed"] is False
    assert answer["assignment"] == {"userId": PRIYA, "type": "user"}
    assert "refuses a scheduler" in answer["reason"]


def test_a_qualified_lead_stages_no_crm_writeback(workspace):
    answer = workspace.qualify("demo-request", {"form": {"email": "a@x.example", "seats": 400}})
    assert answer["crm_writeback"] is None


def test_a_refused_lead_stages_the_writeback_the_research_names_and_applies_nothing(workspace):
    workspace.create_assignee({"user_id": PRIYA, "name": "Priya"}, actor="t", source="test")
    answer = workspace.qualify(
        "demo-request",
        {"form": {"email": "a@x.example", "seats": 5}, "crm": {"lead": {"rating": "hot"}}},
    )
    writeback = answer["crm_writeback"]
    assert writeback["object"] == "lead"
    assert writeback["applied"] is False
    assert writeback["fields"]["qualification_verdict"] == "not_scheduled"
    assert writeback["fields"]["proposed_owner_id"] == PRIYA
    assert writeback["fields"]["scheduling_allowed"] is False
    assert writeback["fields"]["rating"] == "hot"


def test_a_lead_that_falls_through_reaches_the_catch_all(workspace):
    answer = workspace.qualify("demo-request", {"form": {"email": "a@x.example", "seats": 5}})
    assert answer["verdict"] == "qualified"
    assert answer["assignment"] == {"userId": DESK, "type": "user"}
    assert answer["matched_rule"]["name"] == CATCH_ALL_RULE["name"]


def test_a_catch_all_naming_a_rep_who_does_not_exist_is_unroutable(workspace):
    workspace.create_router(
        router_spec(
            router_slug="broken-desk",
            rules=[{**CATCH_ALL_RULE, "assign_user_id": "005-former-employee"}],
        ),
        actor="t",
        source="test",
    )
    answer = workspace.qualify("broken-desk", {"form": {"email": "a@x.example"}})
    assert answer["verdict"] == "unroutable"
    assert answer["scheduling_allowed"] is False
    assert answer["assignment"] == {"userId": "", "type": "unassigned"}
    assert "005-former-employee" in answer["reason"]
    assert answer["route_id"], "an unroutable lead still answers with its own route id"
    assert answer["crm_writeback"]["fields"]["qualification_verdict"] == "unroutable"


def test_a_rule_naming_no_owner_at_all_is_unroutable(workspace):
    """Reachable through a record written outside the validator, not through a save."""
    record = workspace.get_router("demo-request")
    workspace.store.update(
        record["id"],
        {
            "rules": [
                {"kind": "data_field", "name": "No owner", "conditions": [], "assign_user_id": ""}
            ]
        },
        actor="t",
        source="test",
    )
    answer = workspace.qualify("demo-request", {"form": {"email": "a@x.example"}})
    assert answer["verdict"] == "unroutable"
    assert "names no owner" in answer["reason"]


def test_a_chain_with_no_catch_all_reaching_a_lead_is_unroutable(workspace):
    record = workspace.get_router("demo-request")
    workspace.store.update(
        record["id"],
        {"rules": [dict(ENTERPRISE_RULE)]},
        actor="t",
        source="test",
    )
    answer = workspace.qualify("demo-request", {"form": {"email": "a@x.example", "seats": 1}})
    assert answer["verdict"] == "unroutable"
    assert "no catch-all" in answer["reason"]


def test_an_unroutable_lead_with_no_rule_at_all_is_unroutable(workspace):
    record = workspace.get_router("demo-request")
    workspace.store.update(record["id"], {"rules": []}, actor="t", source="test")
    answer = workspace.qualify("demo-request", {"form": {"email": "a@x.example"}})
    assert answer["verdict"] == "unroutable"
    assert answer["assignment"]["type"] == "unassigned"


def test_a_body_with_no_form_is_refused_rather_than_qualified_as_nothing(workspace):
    with pytest.raises(PayloadRefused) as caught:
        workspace.qualify("demo-request", {"crm": {"lead": {"rating": "hot"}}})
    assert caught.value.status == 400
    assert caught.value.code == "form_payload_required"


@pytest.mark.parametrize("payload", [{"form": {}}, {"form": []}, {"form": None}, {}])
def test_an_empty_or_mistyped_form_is_the_same_refusal(workspace, payload):
    with pytest.raises(PayloadRefused):
        workspace.qualify("demo-request", payload)


def test_an_unknown_router_is_a_404_that_names_the_slug(workspace):
    with pytest.raises(RouterNotFound) as caught:
        workspace.qualify("absent", {"form": {"a": 1}})
    assert caught.value.status == 404
    assert "absent" in caught.value.detail


def test_an_unpublished_router_refuses_rather_than_qualifying(workspace):
    """The research publishes a router before a prospect meets it."""
    workspace.update_router("demo-request", {"enabled": False}, actor="t", source="test")
    with pytest.raises(RouterDisabled) as caught:
        workspace.qualify("demo-request", {"form": {"email": "a@x.example"}})
    assert caught.value.status == 409
    assert caught.value.code == "router_disabled"


def test_a_router_may_be_passed_in_so_a_caller_can_run_a_draft(workspace, engine):
    draft = router_spec(router_slug="draft", rules=[{**CATCH_ALL_RULE, "assign_user_id": DESK}])
    answer = engine.qualify("draft", {"form": {"email": "a@x.example"}}, router=draft)
    assert answer["router_slug"] == "draft"
    assert answer["assignment"]["userId"] == DESK
    assert engine.get_router("draft") is None, "a draft must not be stored by answering it"


def test_the_evaluated_rules_count_says_how_far_the_walk_went(workspace):
    answer = workspace.qualify("demo-request", {"form": {"email": "a@x.example", "seats": 5}})
    assert answer["evaluated_rules"] == 3
    assert answer["matched_rule"]["name"] == CATCH_ALL_RULE["name"]


# --------------------------------------------------------------------------- #
# Routers
# --------------------------------------------------------------------------- #


def test_a_declared_router_answers_with_its_stored_shape(engine):
    record = engine.create_router(router_spec(), actor="t", source="test")
    data = record["data"]
    assert data["router_slug"] == "demo-request"
    assert data["tenant"] == "soluspring.chilipiper.example"
    assert data["enabled"] is True
    assert data["notes"] == ""
    assert len(data["rules"]) == 3


def test_a_router_without_a_tenant_falls_back_to_the_researched_one(engine):
    record = engine.create_router(router_spec(tenant=""), actor="t", source="test")
    assert record["data"]["tenant"] == "your-tenant.chilipiper.com"


def test_a_second_router_at_the_same_slug_is_a_409(workspace):
    with pytest.raises(RouterAlreadyExists) as caught:
        workspace.create_router(router_spec(), actor="t", source="test")
    assert caught.value.status == 409


def test_a_chain_with_no_catch_all_cannot_be_saved(workspace):
    with pytest.raises(RouterRefused) as caught:
        workspace.create_router(
            router_spec(rules=[dict(ENTERPRISE_RULE)]), actor="t", source="test"
        )
    assert caught.value.status == 422
    assert "Catch All" in caught.value.detail


def test_a_refused_router_leaves_nothing_behind(workspace):
    before = len(workspace.routers())
    with pytest.raises(RouterRefused):
        workspace.create_router(router_spec(router_slug="half", rules=[]), actor="t", source="test")
    assert len(workspace.routers()) == before


def test_a_router_is_patchable_and_the_whole_result_is_revalidated(workspace):
    record = workspace.update_router(
        "demo-request", {"notes": "Reviewed"}, actor="t", source="test"
    )
    assert record["data"]["notes"] == "Reviewed"
    assert len(record["data"]["rules"]) == 3
    with pytest.raises(RouterRefused):
        workspace.update_router(
            "demo-request", {"rules": [dict(ENTERPRISE_RULE)]}, actor="t", source="test"
        )


def test_a_patch_cannot_move_the_slug_off_its_own_path(workspace):
    workspace.update_router("demo-request", {"router_slug": "stolen"}, actor="t", source="test")
    assert workspace.get_router("demo-request")["data"]["router_slug"] == "demo-request"


def test_patching_an_unknown_router_is_a_404(workspace):
    with pytest.raises(RouterNotFound):
        workspace.update_router("absent", {"notes": "x"}, actor="t", source="test")


def test_deleting_a_router_keeps_its_recorded_verdicts_readable(workspace, store):
    room = store.create("room", {"name": "R"}, actor="t", source="test")
    workspace.record_verdict(
        "demo-request",
        {"form": {"email": "a@x.example", "seats": 400}},
        room_id=room["id"],
        actor="t",
        source="test",
    )
    workspace.delete_router("demo-request", actor="t", source="test")
    assert workspace.get_router("demo-request") is None
    assert len(workspace.verdicts()) == 1


def test_deleting_an_unknown_router_is_a_404(workspace):
    with pytest.raises(RouterNotFound):
        workspace.delete_router("absent", actor="t", source="test")


def test_routers_can_be_listed_and_filtered_on_enabled(engine):
    engine.create_router(router_spec(), actor="t", source="test")
    engine.create_router(router_spec(router_slug="draft"), actor="t", source="test")
    engine.update_router("draft", {"enabled": False}, actor="t", source="test")
    assert len(engine.routers()) == 2
    assert len(engine.routers(enabled=True)) == 1
    assert len(engine.routers(enabled=False)) == 1
    assert engine.routers(enabled=False)[0]["data"]["router_slug"] == "draft"


# --------------------------------------------------------------------------- #
# The preview route's domain: advise without saving
# --------------------------------------------------------------------------- #


def test_preview_reports_the_problems_without_saving(engine):
    answer = engine.preview_router({"router_slug": "draft", "name": "Draft", "rules": []})
    assert answer["valid"] is False
    assert answer["saved"] is False
    assert answer["rules"] == 0
    assert answer["sample_verdict"] is None
    assert answer["sample_conditions"] == []
    assert engine.get_router("draft") is None


def test_preview_says_which_rule_a_sample_lead_would_hit(workspace):
    spec = router_spec(
        sample={"form": {"email": "a@x.example", "seats": 400}, "crm": {"lead": {"rating": "hot"}}}
    )
    answer = workspace.preview_router(spec)
    assert answer["valid"] is True
    assert answer["sample_verdict"] == "qualified"
    assert answer["sample_rule"] == ENTERPRISE_RULE["name"]


def test_preview_reports_the_number_of_rules_even_when_the_chain_is_bad(engine):
    answer = engine.preview_router(router_spec(rules=[dict(ENTERPRISE_RULE)]))
    assert answer["rules"] == 1
    assert answer["valid"] is False
    assert answer["sample_verdict"] is None


def test_preview_of_a_chain_with_no_rules_key_reports_zero(workspace):
    answer = workspace.preview_router(router_spec(rules=None, sample={"form": {"a": 1}}))
    assert answer["rules"] == 0
    assert any("rules is required" in problem for problem in answer["problems"])


def test_preview_of_a_spec_that_is_not_a_list_of_rules_is_zero(workspace):
    answer = workspace.preview_router(router_spec(rules="chain"))
    assert answer["rules"] == 0


# --------------------------------------------------------------------------- #
# Assignees
# --------------------------------------------------------------------------- #


def test_an_assignee_needs_a_user_id_and_a_name(engine):
    with pytest.raises(AssigneeRefused) as no_id:
        engine.create_assignee({"name": "Nobody"}, actor="t", source="test")
    assert no_id.value.status == 422
    with pytest.raises(AssigneeRefused) as no_name:
        engine.create_assignee({"user_id": NADIA}, actor="t", source="test")
    assert "needs a name" in no_name.value.detail


def test_an_assignee_carries_the_fields_the_page_lists(engine):
    record = engine.create_assignee(
        {"user_id": NADIA, "name": "Nadia", "team": "Enterprise"}, actor="t", source="test"
    )
    assert record["data"]["user_id"] == NADIA
    assert record["data"]["team"] == "Enterprise"


def test_the_same_assignee_twice_is_refused(workspace):
    with pytest.raises(AssigneeRefused) as caught:
        workspace.create_assignee({"user_id": NADIA, "name": "Again"}, actor="t", source="test")
    assert "already declared" in caught.value.detail


def test_deleting_an_assignee_leaves_the_router_working_and_the_lead_unroutable(workspace):
    workspace.create_assignee({"user_id": PRIYA, "name": "Priya"}, actor="t", source="test")
    workspace.delete_assignee(DESK, actor="t", source="test")
    assert workspace.get_assignee(DESK) is None
    answer = workspace.qualify("demo-request", {"form": {"email": "a@x.example", "seats": 5}})
    assert answer["verdict"] == "unroutable"
    assert "005-desk" in answer["reason"]


def test_reading_an_unknown_assignee_is_a_404(workspace):
    with pytest.raises(AssigneeNotFound) as caught:
        workspace.require_assignee("absent")
    assert caught.value.status == 404


def test_assignees_are_listed(engine):
    engine.create_assignee({"user_id": NADIA, "name": "Nadia"}, actor="t", source="test")
    assert [row["data"]["user_id"] for row in engine.assignees()] == [NADIA]
    assert engine.assignee_ids() == {NADIA}
    assert engine.get_assignee(NADIA)["data"]["name"] == "Nadia"


# --------------------------------------------------------------------------- #
# The recorded path
# --------------------------------------------------------------------------- #


def test_recording_a_verdict_writes_exactly_one_row(workspace, store):
    room = store.create("room", {"name": "R"}, actor="t", source="test")
    created = workspace.record_verdict(
        "demo-request",
        {"form": {"email": "a@x.example", "seats": 400}},
        room_id=room["id"],
        actor="t",
        source="test",
    )
    assert count_rows(store, "lead_qualification_verdict") == 1
    assert created["collection"] == "lead_qualification_verdict"
    assert created["room_id"] == room["id"]
    assert created["data"]["verdict"] == "qualified"
    assert created["qualification"]["route_id"] == created["data"]["route_id"]


def test_a_recorded_verdict_carries_the_evidence_a_rep_reads(workspace, store):
    room = store.create("room", {"name": "R"}, actor="t", source="test")
    created = workspace.record_verdict(
        "demo-request",
        {"form": {"email": "a@x.example", "seats": 400}, "crm": {}},
        room_id=room["id"],
        actor="t",
        source="test",
    )
    data = created["data"]
    assert data["form"] == {"email": "a@x.example", "seats": 400}
    assert data["matched_rule"]["name"] == ENTERPRISE_RULE["name"]
    assert data["assignment"] == {"userId": NADIA, "type": "user"}
    assert data["routing_link"].endswith(data["route_id"])
    assert data["evaluated_at"] == CLOCK
    assert data["fallthrough"] is False
    assert data["side_effects"]["records_written"] == 1
    assert data["side_effects"]["routing_sessions_consumed"] == 0


def test_a_recorded_verdict_stages_the_writeback_and_applies_nothing(workspace, store):
    workspace.create_assignee({"user_id": PRIYA, "name": "Priya"}, actor="t", source="test")
    room = store.create("room", {"name": "R"}, actor="t", source="test")
    created = workspace.record_verdict(
        "demo-request",
        {"form": {"email": "a@x.example", "seats": 5}, "crm": {"lead": {"rating": "hot"}}},
        room_id=room["id"],
        actor="t",
        source="test",
    )
    writeback = created["data"]["crm_writeback"]
    assert writeback["applied"] is False
    assert writeback["fields"]["qualification_verdict"] == "not_scheduled"


def test_a_recorded_verdict_still_consumes_no_session_and_queries_no_availability(workspace, store):
    room = store.create("room", {"name": "R"}, actor="t", source="test")
    created = workspace.record_verdict(
        "demo-request",
        {"form": {"email": "a@x.example", "seats": 400}},
        room_id=room["id"],
        actor="t",
        source="test",
    )
    assert created["qualification"]["side_effects"]["routing_sessions_consumed"] == 0
    assert created["qualification"]["side_effects"]["availability_queries"] == 0
    assert count_rows(store, "routing_session") == 0


def test_a_refused_recording_writes_nothing(workspace, store):
    room = store.create("room", {"name": "R"}, actor="t", source="test")
    with pytest.raises(PayloadRefused):
        workspace.record_verdict(
            "demo-request", {"crm": {}}, room_id=room["id"], actor="t", source="test"
        )
    assert count_rows(store, "lead_qualification_verdict") == 0


def test_a_recording_into_a_room_that_does_not_exist_is_refused(workspace):
    with pytest.raises(RoomRequired) as caught:
        require_room(workspace, "room-absent")
    assert caught.value.status == 404
    assert "room-absent" in caught.value.detail


def test_a_recording_into_a_room_that_exists_is_allowed(workspace, store):
    room = store.create("room", {"name": "R"}, actor="t", source="test")
    require_room(workspace, room["id"])


def test_recorded_verdicts_can_be_narrowed_by_room_router_verdict_and_signal(workspace, store):
    first = store.create("room", {"name": "A"}, actor="t", source="test")
    second = store.create("room", {"name": "B"}, actor="t", source="test")
    workspace.create_assignee({"user_id": PRIYA, "name": "Priya"}, actor="t", source="test")
    workspace.record_verdict(
        "demo-request",
        {"form": {"email": "a@x.example", "seats": 400}},
        room_id=first["id"],
        actor="t",
        source="test",
    )
    workspace.record_verdict(
        "demo-request",
        {"form": {"email": "b@x.example", "seats": 5}, "crm": {"lead": {"rating": "hot"}}},
        room_id=second["id"],
        actor="t",
        source="test",
    )
    assert len(workspace.verdicts()) == 2
    assert len(workspace.verdicts(room_id=first["id"])) == 1
    assert len(workspace.verdicts(room_id=first["id"], verdict="not_scheduled")) == 0
    assert len(workspace.verdicts(verdict="not_scheduled")) == 1
    assert len(workspace.verdicts(scheduling_allowed=True)) == 1
    assert len(workspace.verdicts(router_slug="demo-request")) == 2
    assert len(workspace.verdicts(router_slug="absent")) == 0


def test_a_verdict_recorded_in_one_room_is_not_listed_under_another(workspace, store):
    first = store.create("room", {"name": "A"}, actor="t", source="test")
    second = store.create("room", {"name": "B"}, actor="t", source="test")
    workspace.record_verdict(
        "demo-request",
        {"form": {"email": "a@x.example", "seats": 400}},
        room_id=first["id"],
        actor="t",
        source="test",
    )
    found = workspace.verdicts(room_id=second["id"], verdict="qualified")
    assert found == [], "a room filter must not be widened by the index alone"


def test_one_verdict_can_be_read_back(workspace, store):
    room = store.create("room", {"name": "R"}, actor="t", source="test")
    created = workspace.record_verdict(
        "demo-request",
        {"form": {"email": "a@x.example", "seats": 400}},
        room_id=room["id"],
        actor="t",
        source="test",
    )
    assert workspace.get_verdict(created["id"])["id"] == created["id"]
    assert workspace.require_verdict(created["id"])["id"] == created["id"]


def test_reading_an_unknown_verdict_is_a_404(workspace):
    assert workspace.get_verdict("absent") is None
    with pytest.raises(VerdictNotFound) as caught:
        workspace.require_verdict("absent")
    assert caught.value.status == 404


# --------------------------------------------------------------------------- #
# The summary
# --------------------------------------------------------------------------- #


def test_the_summary_counts_the_routers_the_assignees_and_the_verdicts(workspace, store):
    room = store.create("room", {"name": "R"}, actor="t", source="test")
    workspace.record_verdict(
        "demo-request",
        {"form": {"email": "a@x.example", "seats": 400}},
        room_id=room["id"],
        actor="t",
        source="test",
    )
    summary = workspace.summary()
    assert summary["routers"] == 1
    assert summary["assignees"] == 2
    assert summary["verdicts"] == 1
    assert summary["scheduling_allowed"] == 1
    assert summary["scheduling_refused"] == 0
    assert summary["by_verdict"] == {"qualified": 1}
    assert summary["by_router"] == {"demo-request": 1}
    assert summary["collections"] == list(COLLECTIONS)
    assert summary["side_effects"] == NO_SIDE_EFFECTS


def test_the_summary_separates_the_refused_verdicts(workspace, store):
    workspace.create_assignee({"user_id": PRIYA, "name": "Priya"}, actor="t", source="test")
    room = store.create("room", {"name": "R"}, actor="t", source="test")
    workspace.record_verdict(
        "demo-request",
        {"form": {"email": "b@x.example", "seats": 5}, "crm": {"lead": {"rating": "hot"}}},
        room_id=room["id"],
        actor="t",
        source="test",
    )
    summary = workspace.summary()
    assert summary["verdicts"] == 1
    assert summary["scheduling_allowed"] == 0
    assert summary["scheduling_refused"] == 1
    assert summary["by_verdict"] == {"not_scheduled": 1}


def test_the_summary_of_one_room_counts_only_that_room(workspace, store):
    first = store.create("room", {"name": "A"}, actor="t", source="test")
    second = store.create("room", {"name": "B"}, actor="t", source="test")
    workspace.record_verdict(
        "demo-request",
        {"form": {"email": "a@x.example", "seats": 400}},
        room_id=first["id"],
        actor="t",
        source="test",
    )
    assert workspace.summary(room_id=first["id"])["verdicts"] == 1
    assert workspace.summary(room_id=second["id"])["verdicts"] == 0
    assert workspace.summary()["verdicts"] == 1
    assert workspace.summary(room_id=second["id"])["room_id"] == second["id"]


def test_the_summary_of_an_empty_world_is_all_zeroes(engine):
    summary = engine.summary()
    assert summary["verdicts"] == 0
    assert summary["by_verdict"] == {}
    assert summary["routers"] == 0
    assert summary["assignees"] == 0


# --------------------------------------------------------------------------- #
# The inferences, which is where the derivations are recorded
# --------------------------------------------------------------------------- #


def test_every_inference_names_its_decision_its_choice_and_its_research():
    described = describe()
    assert described["workflow"] == "WF-052"
    assert described["spec"].endswith("WF-052.md")
    decisions = {entry["decision"] for entry in described["inferred"]}
    assert {
        "derived_route_id",
        "interval_refused",
        "catch_all_required",
        "not_scheduled_collapses_disqualified",
        "unroutable_is_a_verdict",
        "crm_values_come_from_the_caller",
        "no_email_required",
        "recorded_path_stages_the_writeback",
        "operators_added",
        "publish_before_serving",
    } <= decisions
    for entry in described["inferred"]:
        assert entry["chose"] and entry["because"] and entry["researched"]


def test_the_inferences_report_what_this_workflow_deliberately_does_not_own():
    described = describe()
    assert described["sourced"]["no_session_consumed"] is True
    assert described["sourced"]["availability_not_queried"] is True
    assert "WF-051" in described["not_derived_here"]["webform_trigger_and_field_mapping"]
    assert "crm" in described["not_derived_here"]["crm_integration"].lower()
    assert described["vocabulary"]["verdicts"] == list(VERDICTS)


# --------------------------------------------------------------------------- #
# The import surface, which is an architectural guard not a style assertion
# --------------------------------------------------------------------------- #


def test_the_domain_package_depends_on_nothing_but_the_store():
    """No framework, no shared app, no direct connection to the database."""
    import ast
    import pathlib

    import dsr.lead_qualification as package

    allowed_roots = {"dsr.store", "dsr.db.audited"}
    for module in pathlib.Path(package.__file__).parent.glob("*.py"):
        tree = ast.parse(module.read_text(encoding="utf-8"), filename=module.name)
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                names.append(node.module)
            elif isinstance(node, ast.Import):
                names.extend(alias.name for alias in node.names)
            for name in names:
                assert not name.startswith("dsr.api"), f"{module.name} imports dsr.api"
                assert not name.startswith("sqlite3"), f"{module.name} imports sqlite3"
                assert not name.startswith("fastapi"), f"{module.name} imports fastapi"
                if name.startswith("dsr."):
                    own = name == "dsr.lead_qualification" or name.startswith(
                        "dsr.lead_qualification."
                    )
                    assert own or name in allowed_roots, f"{module.name} imports {name}"


def test_no_module_in_the_package_opens_a_database_connection():
    import pathlib

    import dsr.lead_qualification as package

    for module in pathlib.Path(package.__file__).parent.glob("*.py"):
        text = module.read_text(encoding="utf-8")
        assert "sqlite3" not in text
        assert ".connect(" not in text


# --------------------------------------------------------------------------- #
# The seeder
# --------------------------------------------------------------------------- #


def test_the_seed_creates_the_states_the_page_shows(db: AuditedDatabase):
    from datetime import datetime, timezone

    from dsr.features import (
        wf052_qualify_a_lead_without_offering_any_calen as feature,
    )

    store = RecordStore(db)
    rooms = [store.create("room", {"name": "R"}, actor="dana", source="test")["id"]]
    summary = feature.seed(
        db, {"room_ids": [(rooms[0], "Room")], "now": datetime.now(timezone.utc), "rng": None}
    )
    assert "3 assignees" in summary
    assert "3 routers" in summary
    assert "qualified" in summary
    assert count_rows(store, "lead_qualification_router") == 3
    assert count_rows(store, "lead_qualification_assignee") == 3
    assert count_rows(store, "lead_qualification_verdict") == 3


def test_the_seed_reports_a_roomless_database_rather_than_crashing(db: AuditedDatabase):
    from datetime import datetime, timezone

    from dsr.features import (
        wf052_qualify_a_lead_without_offering_any_calen as feature,
    )

    summary = feature.seed(db, {"room_ids": [], "now": datetime.now(timezone.utc), "rng": None})
    assert "0 recorded" in summary
    assert count_rows(RecordStore(db), "lead_qualification_router") == 3


def test_every_character_of_the_seed_return_string_is_encodable_by_cp1252(db: AuditedDatabase):
    """A single U+2192 in one recovered feature broke the whole seeder on Windows."""
    from datetime import datetime, timezone

    from dsr.features import (
        wf052_qualify_a_lead_without_offering_any_calen as feature,
    )

    store = RecordStore(db)
    room = store.create("room", {"name": "R"}, actor="dana", source="test")
    summary = feature.seed(
        db,
        {"room_ids": [(room["id"], "Room")], "now": datetime.now(timezone.utc), "rng": None},
    )
    assert summary.encode("cp1252")
    print(summary)


def test_the_seeded_demo_answers_every_verdict_a_reviewer_needs_to_read(db: AuditedDatabase):
    from datetime import datetime, timezone

    from dsr.features import (
        wf052_qualify_a_lead_without_offering_any_calen as feature,
    )

    store = RecordStore(db)
    room = store.create("room", {"name": "R"}, actor="dana", source="test")
    feature.seed(
        db, {"room_ids": [(room["id"], "Room")], "now": datetime.now(timezone.utc), "rng": None}
    )
    engine = LeadQualificationEngine(store)
    verdicts = {row["data"]["verdict"] for row in engine.verdicts(limit=100)}
    assert "qualified" in verdicts
    assert "not_scheduled" in verdicts, "the demo must show the path that refuses a scheduler"
    assert (
        engine.qualify("unprovisioned-desk", {"form": {"email": "a@x.example"}})["verdict"]
        == "unroutable"
    )


# --------------------------------------------------------------------------- #
# The error bodies
# --------------------------------------------------------------------------- #


def test_every_refusal_answers_with_its_own_status_code_and_a_body():
    refusals = [
        IntervalSupplied("x"),
        PayloadRefused("x"),
        RouterNotFound("x"),
        RouterRefused("x"),
        RouterAlreadyExists("x"),
        RouterDisabled("x"),
        AssigneeNotFound("x"),
        AssigneeRefused("x"),
        VerdictNotFound("x"),
        RoomRequired("x"),
    ]
    for refusal in refusals:
        body = refusal.as_response()
        assert body["error"] == refusal.code
        assert body["status"] == refusal.status
        assert body["detail"] == "x"
        assert isinstance(refusal, QualificationError)


def test_the_status_codes_separate_a_bad_request_from_a_conflict():
    assert IntervalSupplied("x").status == 400
    assert RouterRefused("x").status == 422
    assert RouterAlreadyExists("x").status == 409
    assert RouterDisabled("x").status == 409
    assert RouterNotFound("x").status == 404
    assert AssigneeNotFound("x").status == 404
    assert RoomRequired("x").status == 404
    assert VerdictNotFound("x").status == 404
    assert AssigneeRefused("x").status == 422
    assert PayloadRefused("x").status == 400


def test_the_base_refusal_carries_its_own_code_for_a_subclass_that_declares_none():
    class Custom(QualificationError):
        pass

    assert Custom("x").code == "qualification_error"
    assert Custom("x").status == 400
