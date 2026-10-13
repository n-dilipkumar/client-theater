"""WF-115 domain tests: the vocabulary, the rules, and the Marketplace engine.

Everything here runs without an HTTP request. The feature module is a set of
route declarations; the domain it delegates to is ``dsr.marketplace_primitives``,
and this file exercises that package directly against a fresh in-memory store.

The ``store`` fixture from ``conftest.py`` gives each test its own empty database,
so this file passes on its own and under ``pytest-xdist`` with no ordering
assumption.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest
from dsr.marketplace_primitives import Marketplace, rules, seed, vocabulary
from dsr.marketplace_primitives.errors import (
    DynamicFilterLimitExceeded,
    KeyImmutable,
    KeyInvalid,
    KeyTaken,
    ModuleInvalid,
    ModuleNotFound,
    ModuleNotPublished,
    TransitionRefused,
)
from dsr.marketplace_primitives.vocabulary import (
    MODULES,
    REVIEW_STATES,
    STATE_APPROVED,
    STATE_CHANGES_REQUESTED,
    STATE_DELETED,
    STATE_DRAFT,
    STATE_IN_REVIEW,
    STATE_PUBLISHED,
    STATE_REJECTED,
    STATE_SUBMITTED,
    STATE_WITHDRAWN,
    STORED_STATES,
    TRANSITIONS,
)
from dsr.store import RecordStore

DOMAIN_PACKAGE = "dsr.marketplace_primitives"

TRIGGER_FIELDS = {
    "short_description": "Fires when a page is opened.",
    "sample_payload": {"contact": {"name": "Dana"}, "tags": ["pricing"], "page": {"url": "/x"}},
    "filters": [
        {"field": "contact.name", "title": "Name", "field_type": "String", "required": True}
    ],
    "subscription_url": "https://apps.example.com/subscribe",
}

ACTION_FIELDS = {
    "sample_payload": {"contact": {"name": "Dana"}},
    "execution": {"method": "url", "url": "https://apps.example.com/execute"},
}


def author(
    engine: Marketplace, kind="trigger", key="mycustomtrigger", name="My trigger", fields=None
):
    return engine.create(
        kind=kind,
        key=key,
        name=name,
        actor="dana",
        source="test",
        fields=dict(TRIGGER_FIELDS if fields is None else fields),
    )


def go_live(engine: Marketplace, module_id: str) -> dict:
    engine.submit(module_id, changelog="v1", actor="dana", source="test")
    engine.review(
        module_id, decision="approve", reviewer="r", note="ok", actor="dana", source="test"
    )
    return engine.publish(module_id, actor="dana", source="test")


# --------------------------------------------------------------------------- #
# Vocabulary
# --------------------------------------------------------------------------- #


def test_vocabulary_names_the_kinds_and_states_the_spec_describes():
    v = vocabulary()
    assert v["ticket"] == "WF-115"
    assert v["kinds"] == ["trigger", "action"]
    assert v["review_states"] == list(REVIEW_STATES)
    assert STATE_DELETED in v["review_states"]
    assert STATE_DELETED not in v["stored_states"]
    assert v["deleted_state"] == STATE_DELETED
    assert v["event_types"] == ["CREATED", "UPDATED", "DELETED"]
    assert v["filter_types"] == ["String", "Select", "Multi-Select", "Dynamic"]
    assert v["dynamic_filter_limit"] == 1


def test_the_derived_lists_agree_with_the_transition_map():
    """SUBMITTABLE_STATES and WITHDRAWABLE_STATES are read off TRANSITIONS."""
    for state, targets in TRANSITIONS.items():
        assert targets, state
        for target in targets:
            assert target in REVIEW_STATES or target == STATE_DELETED
    submittable = {s for s, targets in TRANSITIONS.items() if STATE_SUBMITTED in targets}
    assert set(vocabulary()["submittable_states"]) == submittable
    withdrawable = {s for s, targets in TRANSITIONS.items() if STATE_WITHDRAWN in targets}
    assert set(vocabulary()["withdrawable_states"]) == withdrawable


def test_every_decision_records_its_alternative_and_a_gate():
    decisions = vocabulary()["decisions"]
    assert decisions
    for item in decisions:
        assert item["decision"]
        assert item["alternative_rejected"]
        assert item["verdict"] in {"pass", "recorded"}
        assert item["id"]


def test_the_two_jev_decisions_carry_their_audit_ids():
    by_id = {item["id"]: item for item in vocabulary()["decisions"]}
    assert by_id["third_party_endpoints"]["audit_id"].startswith("jev-")
    assert by_id["review_state_machine"]["audit_id"].startswith("jev-")
    assert by_id["third_party_endpoints"]["selected"] == "option_a"
    assert by_id["review_state_machine"]["confidence"] >= 0.7


def test_the_sources_block_is_reproduced_verbatim():
    """Two entries repeat and one carries a stray backtick. That is the quotation."""
    sources = vocabulary()["sources"]
    assert len(sources) == 6
    assert sources[0] != sources[1]
    # The last two entries are the same URL; the final one carries a stray
    # trailing backtick. That is the quotation, reproduced as it is written.
    assert sources[4] + "`" == sources[5]
    assert sources[3] == (
        "https://marketplace.gohighlevel.com/docs/marketplace-modules/WorkflowActionsAndTriggers/"
    )


# --------------------------------------------------------------------------- #
# The pure rules
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "key, expected",
    [
        ("mycustomtrigger", None),
        ("abc", None),
        ("a_b_123", None),
        ("", "required"),
        (None, "required"),
        ("1abc", "key must match"),
        ("Abc", "key must match"),
        ("ab", "key must match"),
        ("a" * 65, "key must match"),
        ("has space", "key must match"),
    ],
)
def test_key_problem(key, expected):
    problem = rules.key_problem(key)
    if expected is None:
        assert problem is None
    else:
        assert problem and expected in problem


def test_dynamic_filter_count_ignores_a_malformed_list():
    assert rules.dynamic_filter_count("nope") == 0
    assert rules.dynamic_filter_count(None) == 0
    assert rules.dynamic_filter_count([{"field_type": "Dynamic"}]) == 1
    assert rules.dynamic_filter_count([{"field_type": "String"}]) == 0


def test_module_errors_rejects_an_unknown_kind():
    assert rules.module_errors("nonsense", {}) == ["kind must be one of trigger, action"]


def test_module_errors_names_every_problem_for_a_bare_trigger():
    problems = rules.module_errors("trigger", {"key": "abc", "name": "X"})
    assert any("subscription_url" in problem for problem in problems)


def test_module_errors_rejects_a_scalar_sample_payload():
    data = {
        "key": "abc",
        "name": "X",
        "sample_payload": 5,
        "subscription_url": "https://a.example.com/s",
    }
    assert any(
        "sample_payload must be a JSON object" in p for p in rules.module_errors("trigger", data)
    )


def test_module_errors_rejects_filters_that_are_not_a_list():
    data = {
        "key": "abc",
        "name": "X",
        "filters": "no",
        "subscription_url": "https://a.example.com/s",
    }
    assert any("filters must be a list" in p for p in rules.module_errors("trigger", data))


def test_module_errors_rejects_a_select_without_options_or_source():
    data = {
        "key": "abc",
        "name": "X",
        "subscription_url": "https://a.example.com/s",
        "filters": [{"field": "stage", "field_type": "Select"}],
    }
    assert any("non-empty options" in p for p in rules.module_errors("trigger", data))


def test_module_errors_rejects_a_dynamic_filter_without_an_external_source():
    data = {
        "key": "abc",
        "name": "X",
        "subscription_url": "https://a.example.com/s",
        "dynamic_filter_url": "https://a.example.com/dyn",
        "filters": [{"field": "x", "field_type": "Dynamic"}],
    }
    problems = rules.module_errors("trigger", data)
    assert any("option_source must be external_api" in p for p in problems)
    assert any("dynamic_url" in p for p in problems)


def test_module_errors_requires_a_dynamic_filter_url_on_a_trigger():
    data = {
        "key": "abc",
        "name": "X",
        "subscription_url": "https://a.example.com/s",
        "filters": [
            {
                "field": "x",
                "field_type": "Dynamic",
                "option_source": "external_api",
                "dynamic_url": "https://a.example.com/dyn",
            }
        ],
    }
    assert any("dynamic_filter_url" in p for p in rules.module_errors("trigger", data))


def test_module_errors_rejects_a_second_dynamic_filter():
    data = {
        "key": "abc",
        "name": "X",
        "subscription_url": "https://a.example.com/s",
        "dynamic_filter_url": "https://a.example.com/dyn",
        "filters": [
            {
                "field": "a",
                "field_type": "Dynamic",
                "option_source": "external_api",
                "dynamic_url": "https://a.example.com/1",
            },
            {
                "field": "b",
                "field_type": "Dynamic",
                "option_source": "external_api",
                "dynamic_url": "https://a.example.com/2",
            },
        ],
    }
    assert any("at most 1 Dynamic filter" in p for p in rules.module_errors("trigger", data))


def test_module_errors_validates_custom_variables():
    data = {
        "key": "abc",
        "name": "X",
        "subscription_url": "https://a.example.com/s",
        "custom_variables": [{"name": "A"}, "not-an-object"],
    }
    problems = rules.module_errors("trigger", data)
    assert any("custom_variables[0].key_path is required" in p for p in problems)
    assert any("custom_variables[1] must be an object" in p for p in problems)


def test_module_errors_rejects_a_bad_events_list():
    data = {
        "key": "abc",
        "name": "X",
        "subscription_url": "https://a.example.com/s",
        "events": ["CREATED", "NOPE"],
    }
    assert any("events may only be" in p for p in rules.module_errors("trigger", data))


def test_module_errors_requires_an_execution_for_an_action():
    assert "an action needs an execution object" in " ".join(
        rules.module_errors("action", {"key": "abc", "name": "X"})
    )
    assert any(
        "execution.method" in p
        for p in rules.module_errors(
            "action", {"key": "abc", "name": "X", "execution": {"method": "nope"}}
        )
    )
    assert any(
        "execution.url" in p
        for p in rules.module_errors(
            "action", {"key": "abc", "name": "X", "execution": {"method": "url"}}
        )
    )
    assert any(
        "execution.code" in p
        for p in rules.module_errors(
            "action", {"key": "abc", "name": "X", "execution": {"method": "custom_code"}}
        )
    )


def test_module_errors_rejects_a_bad_pause_webhook_and_distribution():
    data = {
        "key": "abc",
        "name": "X",
        "subscription_url": "https://a.example.com/s",
        "pause_resume_webhook": "ftp://nope",
        "distribution": "nope",
    }
    problems = rules.module_errors("trigger", data)
    assert any("pause_resume_webhook" in p for p in problems)
    assert any("distribution must be" in p for p in problems)


def test_module_errors_accepts_a_complete_trigger_and_action():
    assert rules.module_errors("trigger", {"key": "abc", "name": "X", **TRIGGER_FIELDS}) == []
    assert rules.module_errors("action", {"key": "abc", "name": "X", **ACTION_FIELDS}) == []


def test_module_warnings_flags_a_trigger_with_no_sample_and_a_custom_action():
    warnings = rules.module_warnings("trigger", {"key": "abc", "name": "X"})
    assert any("no sample_payload" in w for w in warnings)
    custom = rules.module_warnings(
        "action", {"key": "abc", "name": "X", "execution": {"method": "custom_code", "code": "x"}}
    )
    assert any("never executed" in w for w in custom)


def test_module_warnings_flags_a_required_filter_that_does_not_resolve():
    data = {
        "sample_payload": {"contact": {}},
        "filters": [{"field": "contact.email", "field_type": "String", "required": True}],
    }
    assert any("does not resolve" in w for w in rules.module_warnings("trigger", data))


def test_can_transition_and_targets_from_handle_bad_input():
    assert rules.can_transition(None, "x") is False
    assert rules.can_transition("draft", "nope") is False
    assert rules.can_transition(STATE_DRAFT, STATE_SUBMITTED) is True
    assert rules.targets_from(None) == ()
    assert STATE_DELETED in rules.targets_from(STATE_PUBLISHED)


def test_resolve_path_walks_objects_lists_and_missing_segments():
    payload = {"a": {"b": [{"c": 1}, {"c": 2}]}, "flat": 3}
    assert rules.resolve_path(payload, "flat") == 3
    assert rules.resolve_path(payload, "a.b.1.c") == 2
    assert rules.resolve_path(payload, "a.b.9.c", None) is None
    assert rules.resolve_path(payload, "a.missing", "fallback") == "fallback"
    assert rules.resolve_path(payload, "", "empty") == "empty"
    assert rules.resolve_path(payload, None, "none") == "none"


def test_evaluate_filters_matches_only_when_every_required_field_resolves():
    filters = [
        {"field": "contact.name", "field_type": "String", "required": True},
        {"field": "optional.x", "field_type": "String"},
        {"field": "tags", "field_type": "Multi-Select", "required": True},
    ]
    result = rules.evaluate_filters(filters, {"contact": {"name": "Dana"}, "tags": ["a"]})
    assert result["matched"] is True
    assert result["missing_required"] == []
    assert result["resolved"] == {"contact.name": "Dana", "tags": ["a"]}

    blocked = rules.evaluate_filters(filters, {"contact": {}})
    assert blocked["matched"] is False
    assert "contact.name" in blocked["missing_required"]
    assert "tags" in blocked["missing_required"]


def test_evaluate_filters_skips_malformed_entries():
    result = rules.evaluate_filters(["nope", {"no": "field"}], {})
    assert result["filters"] == []
    assert result["matched"] is True


def test_delivery_errors_names_each_problem():
    assert rules.delivery_errors("NOPE", "ftp://x", "no") != []
    assert rules.delivery_errors("CREATED", "https://a.example.com/x", None) == []


def test_filter_and_custom_variable_edges_are_reported():
    base = {"key": "abc", "name": "X", "subscription_url": "https://a.example.com/s"}
    # A filter that is not an object.
    assert any(
        "filters[0] must be an object" in p
        for p in rules.module_errors("trigger", {**base, "filters": ["nope"]})
    )
    # A filter with no field, and one with an unknown type.
    assert any(
        "filters[0].field is required" in p
        for p in rules.module_errors("trigger", {**base, "filters": [{"field_type": "String"}]})
    )
    assert any(
        "field_type must be one of" in p
        for p in rules.module_errors(
            "trigger", {**base, "filters": [{"field": "x", "field_type": "Nope"}]}
        )
    )


def test_custom_variable_shape_edges_are_reported():
    base = {"key": "abc", "name": "X", "subscription_url": "https://a.example.com/s"}
    assert any(
        "custom_variables must be a list" in p
        for p in rules.module_errors("trigger", {**base, "custom_variables": "no"})
    )
    assert any(
        "custom_variables[0].name is required" in p
        for p in rules.module_errors("trigger", {**base, "custom_variables": [{"key_path": "x"}]})
    )


def test_a_non_list_events_field_is_reported():
    data = {
        "key": "abc",
        "name": "X",
        "subscription_url": "https://a.example.com/s",
        "events": "CREATED",
    }
    assert any("events must be a list" in p for p in rules.module_errors("trigger", data))


def test_module_errors_also_reports_a_bad_key_directly():
    """The engine checks the key for a specific code; the rule still reports it."""
    data = {"key": "BAD KEY", "name": "X", "subscription_url": "https://a.example.com/s"}
    assert any("key must match" in p for p in rules.module_errors("trigger", data))


def test_resolve_path_stops_at_a_scalar_before_the_path_ends():
    assert rules.resolve_path({"a": 5}, "a.b", None) is None
    assert rules.resolve_path([1, 2], "9", None) is None
    assert rules.resolve_path([{"c": 3}], "0.c") == 3


# --------------------------------------------------------------------------- #
# The engine
# --------------------------------------------------------------------------- #


def test_create_authors_a_draft_and_audits_it(store):
    engine = Marketplace(store)
    module = author(engine)
    assert module["review_state"] == STATE_DRAFT
    assert module["version"] == 0
    assert module["available_actions"]["submit"] is True
    assert module["available_actions"]["deliver"] is False
    assert module["runs_custom_code"] is False
    audit = store.audit(collection=MODULES)
    assert audit and audit[0]["action"] == "insert"


def test_create_refuses_a_malformed_key(store):
    with pytest.raises(KeyInvalid) as caught:
        Marketplace(store).create(
            kind="trigger",
            key="Bad Key",
            name="X",
            actor="d",
            source="t",
            fields=dict(TRIGGER_FIELDS),
        )
    assert caught.value.code == "key_invalid"
    assert caught.value.status == 422


def test_create_refuses_a_duplicate_key(store):
    engine = Marketplace(store)
    author(engine, key="dupkey")
    with pytest.raises(KeyTaken) as caught:
        author(engine, key="dupkey", name="Another")
    assert caught.value.code == "key_taken"
    assert caught.value.status == 409


def test_create_refuses_an_invalid_payload(store):
    with pytest.raises(ModuleInvalid) as caught:
        Marketplace(store).create(
            kind="trigger", key="okaykey", name="", actor="d", source="t", fields={}
        )
    assert "name is required" in caught.value.problems


def test_create_refuses_a_second_dynamic_filter(store):
    with pytest.raises(DynamicFilterLimitExceeded):
        Marketplace(store).create(
            kind="trigger",
            key="dynkey",
            name="X",
            actor="d",
            source="t",
            fields={
                "subscription_url": "https://a.example.com/s",
                "dynamic_filter_url": "https://a.example.com/dyn",
                "filters": [
                    {
                        "field": "a",
                        "field_type": "Dynamic",
                        "option_source": "external_api",
                        "dynamic_url": "https://a.example.com/1",
                    },
                    {
                        "field": "b",
                        "field_type": "Dynamic",
                        "option_source": "external_api",
                        "dynamic_url": "https://a.example.com/2",
                    },
                ],
            },
        )


def test_update_edits_allowed_fields_and_audits_the_change(store):
    engine = Marketplace(store)
    module = author(engine)
    updated = engine.update(module["id"], {"name": "Renamed"}, actor="d", source="t")
    assert updated["name"] == "Renamed"
    assert updated["revision"] == 2
    actions = [row["action"] for row in store.audit(collection=MODULES)]
    assert "update" in actions


def test_update_refuses_a_changed_key(store):
    engine = Marketplace(store)
    module = author(engine)
    with pytest.raises(KeyImmutable) as caught:
        engine.update(module["id"], {"key": "different"}, actor="d", source="t")
    assert caught.value.code == "key_immutable"


def test_update_refuses_a_changed_kind(store):
    engine = Marketplace(store)
    module = author(engine)
    with pytest.raises(KeyImmutable):
        engine.update(module["id"], {"kind": "action"}, actor="d", source="t")


def test_update_refuses_to_set_the_review_state_directly(store):
    engine = Marketplace(store)
    module = author(engine)
    with pytest.raises(TransitionRefused):
        engine.update(module["id"], {"review_state": STATE_PUBLISHED}, actor="d", source="t")


def test_update_refuses_to_edit_a_published_module(store):
    engine = Marketplace(store)
    module = author(engine)
    go_live(engine, module["id"])
    with pytest.raises(TransitionRefused) as caught:
        engine.update(module["id"], {"name": "New name"}, actor="d", source="t")
    assert "published" in str(caught.value)


def test_update_revalidates_the_merged_payload(store):
    engine = Marketplace(store)
    module = author(engine)
    with pytest.raises(ModuleInvalid):
        engine.update(module["id"], {"subscription_url": "not-a-url"}, actor="d", source="t")


def test_submit_records_a_version_and_a_review_event(store):
    engine = Marketplace(store)
    module = author(engine)
    submitted = engine.submit(module["id"], changelog="First cut", actor="d", source="t")
    assert submitted["review_state"] == STATE_SUBMITTED
    assert submitted["version"] == 1
    assert submitted["changelog"] == "First cut"
    versions = engine.versions(module["id"])
    assert len(versions) == 1
    assert versions[0]["data"]["changelog"] == "First cut"
    assert versions[0]["data"]["version"] == 1
    reviews = engine.reviews(module["id"])
    assert [row["data"]["action"] for row in reviews] == ["submit"]


def test_submit_requires_a_changelog(store):
    engine = Marketplace(store)
    module = author(engine)
    with pytest.raises(ModuleInvalid) as caught:
        engine.submit(module["id"], changelog="  ", actor="d", source="t")
    assert "changelog" in str(caught.value)


def test_submit_is_refused_from_a_state_the_machine_does_not_allow(store):
    engine = Marketplace(store)
    module = author(engine)
    go_live(engine, module["id"])
    with pytest.raises(TransitionRefused):
        engine.submit(module["id"], changelog="again", actor="d", source="t")


def test_review_start_moves_a_submission_into_review(store):
    engine = Marketplace(store)
    module = author(engine)
    engine.submit(module["id"], changelog="v1", actor="d", source="t")
    reviewing = engine.review(
        module["id"], decision="start_review", reviewer="r", note=None, actor="d", source="t"
    )
    assert reviewing["review_state"] == STATE_IN_REVIEW


def test_review_request_changes_requires_a_note(store):
    engine = Marketplace(store)
    module = author(engine)
    engine.submit(module["id"], changelog="v1", actor="d", source="t")
    with pytest.raises(ModuleInvalid) as caught:
        engine.review(
            module["id"], decision="request_changes", reviewer="r", note="", actor="d", source="t"
        )
    assert "note" in str(caught.value)
    changed = engine.review(
        module["id"], decision="request_changes", reviewer="r", note="fix it", actor="d", source="t"
    )
    assert changed["review_state"] == STATE_CHANGES_REQUESTED


def test_review_rejects_an_unknown_decision(store):
    engine = Marketplace(store)
    module = author(engine)
    engine.submit(module["id"], changelog="v1", actor="d", source="t")
    with pytest.raises(ModuleInvalid) as caught:
        engine.review(module["id"], decision="maybe", reviewer="r", note="", actor="d", source="t")
    assert "decision must be one of" in str(caught.value)


def test_rejected_and_changes_requested_modules_can_be_resubmitted(store):
    engine = Marketplace(store)
    module = author(engine)
    engine.submit(module["id"], changelog="v1", actor="d", source="t")
    engine.review(module["id"], decision="reject", reviewer="r", note="no", actor="d", source="t")
    assert engine.module(module["id"])["review_state"] == STATE_REJECTED
    again = engine.submit(module["id"], changelog="v2", actor="d", source="t")
    assert again["review_state"] == STATE_SUBMITTED
    assert again["version"] == 2


def test_publish_is_refused_until_approved(store):
    engine = Marketplace(store)
    module = author(engine)
    with pytest.raises(TransitionRefused):
        engine.publish(module["id"], actor="d", source="t")
    submitted = engine.submit(module["id"], changelog="v1", actor="d", source="t")
    assert submitted["review_state"] == STATE_SUBMITTED
    with pytest.raises(TransitionRefused):
        engine.publish(module["id"], actor="d", source="t")


def test_approve_then_publish_reaches_published(store):
    engine = Marketplace(store)
    module = author(engine)
    published = go_live(engine, module["id"])
    assert published["review_state"] == STATE_PUBLISHED
    assert published["published_at"]
    assert published["available_actions"]["deliver"] is True
    assert published["available_actions"]["submit"] is False


def test_withdraw_pulls_a_submission_back(store):
    engine = Marketplace(store)
    module = author(engine)
    engine.submit(module["id"], changelog="v1", actor="d", source="t")
    withdrawn = engine.withdraw(module["id"], actor="d", source="t")
    assert withdrawn["review_state"] == STATE_WITHDRAWN
    assert withdrawn["available_actions"]["submit"] is True


def test_a_published_module_may_be_withdrawn_then_edited(store):
    engine = Marketplace(store)
    module = author(engine)
    go_live(engine, module["id"])
    engine.withdraw(module["id"], actor="d", source="t")
    edited = engine.update(module["id"], {"name": "Edited after withdraw"}, actor="d", source="t")
    assert edited["name"] == "Edited after withdraw"


def test_withdraw_is_refused_on_a_draft(store):
    engine = Marketplace(store)
    module = author(engine)
    with pytest.raises(TransitionRefused):
        engine.withdraw(module["id"], actor="d", source="t")


def test_delete_is_a_soft_delete_that_keeps_the_key(store):
    engine = Marketplace(store)
    module = author(engine, key="keepmekey")
    deleted = engine.delete(module["id"], actor="d", source="t")
    assert deleted["review_state"] == STATE_DELETED
    assert deleted["deleted"] is True
    # Gone from the live reads...
    with pytest.raises(ModuleNotFound):
        engine.module(module["id"])
    assert engine.modules() == []
    # ...but the key still resolves when a caller asks for deleted rows.
    resolved = engine.by_key("keepmekey", include_deleted=True)
    assert resolved is not None
    assert resolved["review_state"] == STATE_DELETED
    assert engine.by_key("keepmekey") is None


def test_delete_audits_the_soft_delete(store):
    engine = Marketplace(store)
    module = author(engine)
    engine.delete(module["id"], actor="d", source="t")
    actions = [row["action"] for row in store.audit(collection=MODULES)]
    assert actions[0] == "delete"


def test_restore_undoes_a_soft_delete(store):
    engine = Marketplace(store)
    module = author(engine)
    engine.delete(module["id"], actor="d", source="t")
    restored = engine.restore(module["id"], actor="d", source="t")
    assert restored["deleted"] is False
    assert restored["review_state"] == STATE_DRAFT
    assert engine.module(module["id"])["id"] == module["id"]


def test_module_and_by_key_raise_for_unknown_ids(store):
    engine = Marketplace(store)
    with pytest.raises(ModuleNotFound):
        engine.module("nope")
    assert engine.by_key("nope") is None


def test_getting_a_record_that_is_not_a_module_raises(store):
    engine = Marketplace(store)
    room = store.create("room", {"name": "Northwind"}, actor="d", source="t")
    with pytest.raises(ModuleNotFound):
        engine.module(room["id"])


def test_record_delivery_needs_a_published_module(store):
    engine = Marketplace(store)
    module = author(engine)
    with pytest.raises(ModuleNotPublished):
        engine.record_delivery(
            module["id"],
            event_type="CREATED",
            target_url="https://services.example.com/execute/a/b",
            filters=None,
            extras=None,
            actor="d",
            source="t",
        )


def test_record_delivery_validates_the_event_and_target(store):
    engine = Marketplace(store)
    module = author(engine)
    go_live(engine, module["id"])
    with pytest.raises(ModuleInvalid):
        engine.record_delivery(
            module["id"],
            event_type="NOPE",
            target_url="nope",
            filters=None,
            extras=None,
            actor="d",
            source="t",
        )


def test_record_delivery_evaluates_the_filters(store):
    engine = Marketplace(store)
    module = author(engine)
    go_live(engine, module["id"])
    result = engine.record_delivery(
        module["id"],
        event_type="UPDATED",
        target_url="https://services.example.com/execute/a/b",
        filters=[{"field": "contact.name", "required": True}],
        extras={"locationId": "loc_1"},
        actor="d",
        source="t",
    )
    assert result["evaluation"]["matched"] is True
    assert result["delivery"]["data"]["event_type"] == "UPDATED"
    assert engine.deliveries(module["id"])


def test_record_delivery_reports_an_unmatched_filter(store):
    engine = Marketplace(store)
    module = author(
        engine,
        fields={
            "subscription_url": "https://apps.example.com/subscribe",
            "sample_payload": {"contact": {}},
            "filters": [{"field": "contact.email", "field_type": "String", "required": True}],
        },
    )
    go_live(engine, module["id"])
    result = engine.record_delivery(
        module["id"],
        event_type="CREATED",
        target_url="https://services.example.com/execute/a/b",
        filters=None,
        extras=None,
        actor="d",
        source="t",
    )
    assert result["evaluation"]["matched"] is False
    assert "contact.email" in result["evaluation"]["missing_required"]


def test_modules_filters_by_kind_state_and_query(store):
    engine = Marketplace(store)
    author(engine, kind="trigger", key="alpha_trigger", name="Alpha trigger")
    author(engine, kind="action", key="beta_action", name="Beta action", fields=dict(ACTION_FIELDS))
    author(
        engine, kind="action", key="gamma_action", name="Gamma action", fields=dict(ACTION_FIELDS)
    )

    assert {m["key"] for m in engine.modules(kind="action")} == {"beta_action", "gamma_action"}
    assert [m["key"] for m in engine.modules(q="gamma")] == ["gamma_action"]
    assert {m["key"] for m in engine.modules(review_state=STATE_DRAFT)} >= {
        "alpha_trigger",
        "beta_action",
    }
    assert engine.modules(review_state=STATE_PUBLISHED) == []


def test_summary_counts_states_kinds_and_deleted(store):
    engine = Marketplace(store)
    trigger = author(engine, key="summary_trigger")
    go_live(engine, trigger["id"])
    author(engine, kind="action", key="summary_action", fields=dict(ACTION_FIELDS))
    gone = author(engine, key="summary_gone")
    engine.delete(gone["id"], actor="d", source="t")
    summary = engine.summary()
    assert summary["total"] == 2
    assert summary["by_state"][STATE_PUBLISHED] == 1
    assert summary["by_state"][STATE_DRAFT] == 1
    assert summary["by_kind"] == {"trigger": 1, "action": 1}
    assert summary["deleted"] == 1


def test_detail_embeds_versions_reviews_and_deliveries(store):
    engine = Marketplace(store)
    module = author(engine)
    go_live(engine, module["id"])
    detail = engine.detail(module["id"])
    assert len(detail["versions"]) == 1
    assert {row["data"]["action"] for row in detail["reviews"]} == {"submit", "approve", "publish"}
    assert detail["versions"][0]["data"]["snapshot"]["key"] == module["key"]


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #


def test_seed_builds_every_state_and_returns_a_cp1252_string(db):
    summary = seed(db, {"room_ids": [("room_1", "Northwind")], "now": None, "rng": None})
    assert isinstance(summary, str)
    assert summary
    # The whole point: the seeder prints this on a Windows console.
    assert summary.encode("cp1252")
    engine = Marketplace(RecordStore(db))
    states = {view["review_state"] for view in engine.modules(limit=100)}
    assert states == {
        STATE_PUBLISHED,
        STATE_SUBMITTED,
        STATE_IN_REVIEW,
        STATE_APPROVED,
        STATE_CHANGES_REQUESTED,
        STATE_DRAFT,
    }
    assert engine.summary()["deleted"] == 1


def test_seed_is_idempotent_on_a_second_call(db):
    first = seed(db, {"room_ids": [], "now": None, "rng": None})
    second = seed(db, {"room_ids": [], "now": None, "rng": None})
    assert first and second is None


# --------------------------------------------------------------------------- #
# The architectural guards the contract names
# --------------------------------------------------------------------------- #


def domain_paths() -> list[Path]:
    return sorted(Path(inspect.getfile(Marketplace)).parent.glob("*.py"))


def test_the_domain_package_imports_nothing_but_the_store():
    """The contract's rule, read off the package on disk rather than asserted."""
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


def test_the_domain_package_never_imports_the_app_or_sqlite():
    for path in domain_paths():
        text = path.read_text(encoding="utf-8")
        assert "from dsr.api" not in text and "import dsr.api" not in text, path.name
        assert "import sqlite3" not in text, path.name
        assert "sqlite3.connect" not in text, path.name


def test_the_state_machine_never_targets_a_state_it_does_not_define():
    for state, targets in TRANSITIONS.items():
        assert state in STORED_STATES, state
        for target in targets:
            assert target in REVIEW_STATES, target
