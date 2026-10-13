"""WF-118 domain tests: the vocabulary, the pure rules, and the engine.

Everything here runs without an HTTP request. The feature module is a set of
route declarations; the domain it delegates to is ``dsr.guide_prompts``, and this
file exercises that package directly against a fresh in-memory store.

The clock is frozen on purpose. Every eligibility test passes an explicit
``datetime``, so no test depends on today's date or on which weekday it is. The
``store`` fixture from ``conftest.py`` gives each test its own empty database, so
this file passes on its own and under ``pytest-xdist`` with no ordering
assumption.
"""

from __future__ import annotations

import ast
import inspect
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from dsr.guide_prompts import GuidePrompts, rules, seed, vocabulary
from dsr.guide_prompts.errors import (
    EventInvalid,
    GuideInvalid,
    GuideNotFound,
    KeyImmutable,
    KeyInvalid,
    KeyTaken,
    OrderInvalid,
)
from dsr.guide_prompts.vocabulary import (
    DELAY_MAX_SECONDS,
    DISMISSAL_CAUSES,
    DISMISSAL_EVENTS,
    GUIDES,
    INTERACTION_EVENTS,
    MIN_STOP_AFTER,
    ORDER_MOVES,
    ORDER_TIERS,
    SECONDS_PER_UNIT,
    SOURCES,
    STOP_AFTER_COUNT,
    STOP_AFTER_NEVER,
)
from dsr.store import RecordStore

DOMAIN_PACKAGE = "dsr.guide_prompts"

#: A frozen clock. Every test that cares about time passes a moment derived from
#: this, so no test can pass or fail because of the day it ran on.
BASE = datetime(2026, 3, 1, 9, 0, tzinfo=timezone.utc)


def at(**offset: int) -> datetime:
    return BASE + timedelta(**offset)


def author(engine: GuidePrompts, key="welcome_tour", name="Welcome tour", activation=None, **extra):
    fields = {"status": "public"}
    if activation is not None:
        fields["activation"] = activation
    fields.update(extra)
    return engine.create(key=key, name=name, actor="dana", source="test", fields=fields)


def activation(**overrides):
    base = {
        "repeat": {"enabled": True, "every": 1, "unit": "day"},
        "stop_after": {"mode": "count", "count": 3},
        "ignore_throttling": False,
        "delay_seconds": 0,
    }
    base.update(overrides)
    return base


# --------------------------------------------------------------------------- #
# Vocabulary
# --------------------------------------------------------------------------- #


def test_vocabulary_names_the_two_mechanisms_on_separate_scopes():
    v = vocabulary()
    assert v["ticket"] == "WF-118"
    assert v["mechanisms"]["throttle"] == "application"
    assert v["mechanisms"]["repeat"] == "guide_visitor"
    assert v["throttle_scope"] != v["repeat_scope"]


def test_vocabulary_keeps_the_two_unit_sets_apart():
    v = vocabulary()
    assert v["repeat_units"] == ["day", "hour"]
    assert v["throttle_units"] == ["minute", "hour", "day"]
    assert v["repeat_range"] == [1, 10_000]
    assert v["throttle_range"] == [1, 10_000]
    assert "minute" not in v["repeat_units"]
    assert "minute" in v["throttle_units"]


def test_vocabulary_records_the_floor_and_the_delay_ceiling():
    v = vocabulary()
    assert v["min_stop_after"] == MIN_STOP_AFTER == 2
    assert v["delay_max_seconds"] == DELAY_MAX_SECONDS == 60
    assert v["stop_after_modes"] == ["never", "count"]


def test_vocabulary_names_the_two_tier_order_and_the_row_arrows():
    v = vocabulary()
    assert v["order_tiers"] == list(ORDER_TIERS) == ["ignore_throttling", "throttled"]
    assert v["order_moves"] == list(ORDER_MOVES)
    assert v["order_filters"] == ["product_area", "category", "status", "page_location"]


def test_dismissal_events_are_three_and_shown_is_not_one():
    v = vocabulary()
    assert v["interaction_events"] == list(INTERACTION_EVENTS)
    assert set(v["dismissal_events"]) == set(DISMISSAL_EVENTS)
    assert "shown" not in v["dismissal_events"]
    assert set(DISMISSAL_EVENTS) == {"dismissed", "advanced", "completed"}


def test_the_dismissal_causes_are_the_ones_the_research_names():
    assert "esc" in DISMISSAL_CAUSES
    assert "advanced" in DISMISSAL_CAUSES
    assert "completed" in DISMISSAL_CAUSES
    assert "counts as a dismissal" in DISMISSAL_CAUSES["completed"]


def test_every_decision_records_its_alternative_and_its_gate():
    decisions = vocabulary()["decisions"]
    assert decisions
    for item in decisions:
        assert item["decision"]
        assert item["alternative_rejected"]
        assert item["verdict"] in {"pass", "recorded"}
        assert item["id"]


def test_the_two_jev_decisions_carry_their_audit_ids():
    by_id = {item["id"]: item for item in vocabulary()["decisions"]}
    programmatic = by_id["programmatic_launch"]
    platform = by_id["order_list_platform_scope"]
    assert programmatic["audit_id"].startswith("jev-")
    assert platform["audit_id"].startswith("jev-")
    assert programmatic["selected"] == "bypasses_gate"
    assert platform["selected"] == "shared"
    assert programmatic["confidence"] >= 0.7
    assert platform["confidence"] >= 0.7


def test_the_sources_block_is_the_five_the_research_cites():
    assert len(SOURCES) == 5
    assert all(url.startswith("https://") for url in SOURCES)
    assert SOURCES[-1].startswith("https://web-sdk.pendo.io")
    assert "360031864452" in SOURCES[0]


# --------------------------------------------------------------------------- #
# Key
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "key, expected",
    [
        ("welcome_tour", None),
        ("abc", None),
        ("a_b_123", None),
        ("", "required"),
        (None, "required"),
        ("1abc", "start with a letter"),
        ("Abc", "must match"),
        ("ab", "3 to 64"),
        ("a" * 65, "3 to 64"),
        ("has space", "must match"),
        ("has-dash", "must match"),
    ],
)
def test_key_problem(key, expected):
    problem = rules.key_problem(key)
    if expected is None:
        assert problem is None
    else:
        assert problem and expected in problem


# --------------------------------------------------------------------------- #
# Intervals
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "every, unit, expected",
    [
        (1, "day", None),
        (10_000, "hour", None),
        (0, "day", "between 1 and 10000"),
        (10_001, "hour", "between 1 and 10000"),
        (5, "minute", "unit must be one of day, hour"),
        (5, None, "unit must be one of day, hour"),
    ],
)
def test_repeat_interval_problem(every, unit, expected):
    problem = rules.repeat_interval_problem(every, unit)
    if expected is None:
        assert problem is None
    else:
        assert problem and expected in problem


@pytest.mark.parametrize(
    "count, unit, expected",
    [
        (1, "day", None),
        (2, "minute", None),
        (10_000, "hour", None),
        (0, "day", "between 1 and 10000"),
        (5, "second", "unit must be one of minute, hour, day"),
    ],
)
def test_throttle_interval_problem(count, unit, expected):
    problem = rules.throttle_interval_problem(count, unit)
    if expected is None:
        assert problem is None
    else:
        assert problem and expected in problem


def test_interval_seconds_uses_the_unit_table():
    assert rules.interval_seconds(1, "day") == SECONDS_PER_UNIT["day"] == 86_400
    assert rules.interval_seconds(2, "hour") == 7_200
    assert rules.repeat_interval_seconds(3, "hour") == 10_800
    assert rules.throttle_interval_seconds(1, "minute") == 60


# --------------------------------------------------------------------------- #
# Activation
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "seconds, expected",
    [(0, None), (60, None), (None, None), (-1, "between 0 and 60"), (61, "between 0 and 60")],
)
def test_delay_problem(seconds, expected):
    problem = rules.delay_problem(seconds)
    if expected is None:
        assert problem is None
    else:
        assert problem and expected in problem


def test_delay_problem_rejects_truthy_non_numbers():
    assert rules.delay_problem(True) == "delay must be a whole number of seconds"
    assert rules.delay_problem("soon") == "delay must be a whole number of seconds"


def test_stop_after_never_needs_no_count():
    assert rules.stop_after_problem(STOP_AFTER_NEVER, None) is None
    assert rules.stop_after_problem(STOP_AFTER_NEVER, 0) is None


def test_stop_after_count_has_a_floor_of_two():
    assert rules.stop_after_problem(STOP_AFTER_COUNT, 2) is None
    problem = rules.stop_after_problem(STOP_AFTER_COUNT, 1)
    assert problem and "at least 2" in problem
    assert rules.stop_after_problem("sometimes", 2)


def test_normalise_activation_fills_the_off_state():
    normalised = rules.normalise_activation(None)
    assert normalised["repeat"]["enabled"] is False
    assert normalised["stop_after"]["mode"] == STOP_AFTER_NEVER
    assert normalised["ignore_throttling"] is False
    assert normalised["delay_seconds"] == 0


def test_activation_errors_skips_the_interval_when_repeat_is_off():
    assert (
        rules.activation_errors({"repeat": {"enabled": False, "every": 999_999, "unit": "x"}}) == []
    )


def test_activation_errors_names_every_problem_at_once():
    problems = rules.activation_errors(
        {
            "repeat": {"enabled": True, "every": 0, "unit": "minute"},
            "stop_after": {"mode": "count", "count": 1},
            "delay_seconds": 90,
        }
    )
    assert len(problems) == 3
    assert any("repeat interval" in p for p in problems)
    assert any("at least 2" in p for p in problems)
    assert any("0 and 60" in p for p in problems)


# --------------------------------------------------------------------------- #
# Guides and orders
# --------------------------------------------------------------------------- #


def test_guide_errors_requires_a_name_and_a_valid_status():
    assert rules.guide_errors({"key": "okay_key", "name": "", "status": "nonsense"}) == [
        "name is required",
        "status must be one of staging, public",
    ]


def test_guide_errors_accepts_a_minimal_guide():
    assert rules.guide_errors({"key": "okay_key", "name": "A tour"}) == []


def test_order_errors_rejects_an_unknown_key_and_a_repeat():
    problems = rules.order_errors(["a", "ghost", "a"], None, ["a", "b"])
    assert any("unknown guides: ghost" in p for p in problems)
    assert any("must not repeat" in p for p in problems)


def test_order_errors_validates_the_throttle_only_when_enabled():
    assert rules.order_errors(["a"], {"enabled": False, "count": 0}, ["a"]) == []
    problems = rules.order_errors(["a"], {"enabled": True, "count": 0, "unit": "day"}, ["a"])
    assert any("throttle interval" in p for p in problems)


def test_order_errors_requires_a_list():
    assert rules.order_errors("nope", None, []) == ["guide_keys must be a list"]


# --------------------------------------------------------------------------- #
# Ranking and the row arrows
# --------------------------------------------------------------------------- #


def test_rank_puts_the_unthrottled_tier_first():
    guides = [
        {"key": "a", "name": "A", "activation": {}},
        {"key": "b", "name": "B", "activation": {"ignore_throttling": True}},
        {"key": "c", "name": "C", "activation": {}},
    ]
    ranked = rules.rank(guides, ["a", "b", "c"])
    assert [row["key"] for row in ranked] == ["b", "a", "c"]
    assert [row["tier"] for row in ranked] == [0, 1, 1]
    assert [row["position"] for row in ranked] == [0, 1, 2]


def test_rank_appends_a_guide_the_order_list_has_not_named():
    guides = [
        {"key": "a", "name": "A", "activation": {}},
        {"key": "z", "name": "Z", "activation": {}},
    ]
    ranked = rules.rank(guides, ["z"])
    assert [row["key"] for row in ranked] == ["z", "a"]


def test_move_supports_every_row_arrow():
    keys = ["a", "b", "c", "d"]
    assert rules.move(keys, "c", "top") == ["c", "a", "b", "d"]
    assert rules.move(keys, "c", "bottom") == ["a", "b", "d", "c"]
    assert rules.move(keys, "c", "up") == ["a", "c", "b", "d"]
    assert rules.move(keys, "c", "down") == ["a", "b", "d", "c"]


def test_move_at_an_end_is_a_no_op():
    keys = ["a", "b", "c"]
    assert rules.move(keys, "a", "up") == keys
    assert rules.move(keys, "c", "down") == keys


def test_move_refuses_an_unknown_arrow_and_an_absent_key():
    with pytest.raises(OrderInvalid):
        rules.move(["a"], "a", "sideways")
    with pytest.raises(OrderInvalid):
        rules.move(["a"], "ghost", "top")


# --------------------------------------------------------------------------- #
# Time helpers
# --------------------------------------------------------------------------- #


def test_parse_time_reads_a_naive_stamp_as_utc_and_round_trips():
    naive = rules.parse_time("2026-03-01T09:00:00")
    assert naive is not None and naive.tzinfo is not None
    assert rules.to_iso(naive) == "2026-03-01T09:00:00+00:00"
    assert rules.to_iso(None) is None
    assert rules.to_iso("") is None
    assert rules.parse_time(BASE) == BASE


# --------------------------------------------------------------------------- #
# Sessions: the two clocks
# --------------------------------------------------------------------------- #


def test_default_session_starts_both_clocks_unset():
    session = rules.default_session("app", "dana", BASE)
    assert session["active_guide_key"] is None
    assert session["last_dismissed_at"] is None
    assert session["last_shown_at"] is None
    assert session["guides"] == {}


def test_is_dismissal_counts_advance_and_completion_but_not_shown():
    assert rules.is_dismissal("dismissed")
    assert rules.is_dismissal("advanced")
    assert rules.is_dismissal("completed")
    assert not rules.is_dismissal("shown")


def test_validate_event_refuses_an_unknown_event():
    assert rules.validate_event("shown") == "shown"
    with pytest.raises(EventInvalid):
        rules.validate_event("clicked")


def test_a_shown_event_starts_no_clock():
    session = rules.apply_shown(rules.default_session("app", "dana", BASE), "a", BASE)
    assert session["last_shown_at"] == rules.to_iso(BASE)
    assert session["last_dismissed_at"] is None
    assert session["active_guide_key"] == "a"
    assert session["guides"]["a"]["shown_count"] == 1
    assert session["guides"]["a"]["last_dismissed_at"] is None


def test_a_dismissal_restarts_both_clocks_and_clears_the_active_guide():
    session = rules.apply_shown(rules.default_session("app", "dana", BASE), "a", BASE)
    session = rules.apply_dismissal(session, "a", "dismissed", at(hours=1))
    assert session["active_guide_key"] is None
    assert session["last_dismissed_at"] == rules.to_iso(at(hours=1))
    assert session["guides"]["a"]["last_dismissed_at"] == rules.to_iso(at(hours=1))
    assert session["guides"]["a"]["dismissed_count"] == 1


def test_a_step_advance_is_a_dismissal_too():
    session = rules.default_session("app", "dana", BASE)
    session = rules.apply_dismissal(session, "a", "advanced", at(hours=2))
    assert session["guides"]["a"]["dismissed_count"] == 1
    with pytest.raises(EventInvalid):
        rules.apply_dismissal(session, "a", "shown", at(hours=3))


def test_apply_shown_does_not_mutate_its_input():
    original = rules.default_session("app", "dana", BASE)
    rules.apply_shown(original, "a", BASE)
    assert original["guides"] == {}
    assert original["active_guide_key"] is None


# --------------------------------------------------------------------------- #
# Eligibility
# --------------------------------------------------------------------------- #


def test_repeat_is_the_gate_when_throttling_is_off():
    guide = {
        "key": "a",
        "name": "A",
        "activation": activation(repeat={"enabled": True, "every": 1, "unit": "hour"}),
    }
    session = rules.default_session("app", "dana", BASE)
    session = rules.apply_dismissal(session, "a", "dismissed", BASE)
    order = {"guide_keys": ["a"], "throttling": {"enabled": False}}

    just_before = rules.evaluate(guide, order, session, at(minutes=59))
    assert just_before["eligible"] is False
    assert just_before["eligible_at"] == rules.to_iso(at(hours=1))
    assert rules.evaluate(guide, order, session, at(hours=1))["eligible"] is True


def test_the_throttle_is_the_gate_between_two_different_guides():
    guides = [
        {"key": "a", "name": "A", "activation": activation(ignore_throttling=True)},
        {"key": "b", "name": "B", "activation": activation()},
    ]
    order = {"guide_keys": ["a", "b"], "throttling": {"enabled": True, "count": 1, "unit": "day"}}
    session = rules.default_session("app", "dana", BASE)
    session = rules.apply_dismissal(session, "a", "dismissed", BASE)

    blocked = rules.evaluate(guides[1], order, session, at(hours=23))
    assert blocked["eligible"] is False
    assert blocked["throttle_due_at"] == rules.to_iso(at(days=1))
    released = rules.evaluate(guides[1], order, session, at(days=1))
    assert released["eligible"] is True


def test_a_guide_that_ignores_throttling_is_never_throttled():
    guide = {"key": "a", "name": "A", "activation": activation(ignore_throttling=True)}
    order = {"guide_keys": ["a"], "throttling": {"enabled": True, "count": 1, "unit": "day"}}
    session = rules.default_session("app", "dana", BASE)
    session = rules.apply_dismissal(session, "critical", "dismissed", BASE)
    assert rules.evaluate(guide, order, session, at(minutes=1))["eligible"] is True


def test_a_guide_nobody_interacts_with_does_not_block_the_next_one():
    guides = [
        {"key": "a", "name": "A", "activation": activation()},
        {"key": "b", "name": "B", "activation": activation()},
    ]
    order = {"guide_keys": ["a", "b"], "throttling": {"enabled": True, "count": 1, "unit": "day"}}
    session = rules.default_session("app", "dana", BASE)
    session = rules.apply_shown(session, "a", BASE)

    view = rules.evaluate(guides[1], order, session, at(minutes=1))
    assert view["eligible"] is True
    assert view["throttle_due_at"] is None


def test_stop_after_suppresses_a_guide_at_the_threshold():
    guide = {
        "key": "a",
        "name": "A",
        "activation": activation(stop_after={"mode": "count", "count": 2}),
    }
    order = {"guide_keys": ["a"], "throttling": {"enabled": False}}
    session = rules.default_session("app", "dana", BASE)
    session = rules.apply_dismissal(session, "a", "dismissed", BASE)
    still_due = rules.evaluate(guide, order, session, at(days=2))
    assert still_due["suppressed"] is False
    session = rules.apply_dismissal(session, "a", "completed", at(days=1))
    done = rules.evaluate(guide, order, session, at(days=3))
    assert done["suppressed"] is True
    assert "stop_after" in done["suppress_reason"]


def test_repeat_off_means_a_dismissed_guide_does_not_return():
    guide = {"key": "a", "name": "A", "activation": activation(repeat={"enabled": False})}
    order = {"guide_keys": ["a"], "throttling": {"enabled": False}}
    session = rules.default_session("app", "dana", BASE)
    session = rules.apply_dismissal(session, "a", "dismissed", BASE)
    view = rules.evaluate(guide, order, session, at(days=30))
    assert view["suppressed"] is True
    assert "repeat is off" in view["suppress_reason"]


def test_the_delay_holds_a_guide_back_from_the_landing_moment():
    guide = {"key": "a", "name": "A", "activation": activation(delay_seconds=30)}
    order = {"guide_keys": ["a"], "throttling": {"enabled": False}}
    session = rules.default_session("app", "dana", BASE)
    assert rules.evaluate(guide, order, session, BASE)["eligible"] is False
    assert rules.evaluate(guide, order, session, at(seconds=30))["eligible"] is True


def test_the_active_guide_is_not_a_candidate_again():
    guide = {"key": "a", "name": "A", "activation": activation()}
    order = {"guide_keys": ["a"], "throttling": {"enabled": False}}
    session = rules.default_session("app", "dana", BASE)
    session = rules.apply_shown(session, "a", BASE)
    view = rules.evaluate(guide, order, session, at(minutes=5))
    assert view["active"] is True
    assert view["eligible"] is False


def test_select_next_returns_the_first_eligible_in_tier_order():
    guides = [
        {"key": "a", "name": "A", "activation": activation()},
        {"key": "critical", "name": "Critical", "activation": activation(ignore_throttling=True)},
    ]
    order = {"guide_keys": ["a", "critical"], "throttling": {"enabled": False}}
    session = rules.default_session("app", "dana", BASE)
    chosen = rules.select_next(guides, order, session, BASE)
    assert chosen["guide"]["key"] == "critical"
    assert chosen["blocked"] is None


def test_evaluate_tolerates_a_malformed_throttle_document():
    guide = {"key": "a", "name": "A", "activation": activation()}
    order = {"guide_keys": ["a"], "throttling": {"enabled": True}}
    session = rules.default_session("app", "dana", BASE)
    session = rules.apply_dismissal(session, "a", "dismissed", BASE)
    view = rules.evaluate(guide, order, session, at(hours=1))
    # The count and unit default rather than raising, so a malformed stored
    # document cannot turn into a 5xx.
    assert view["throttle_due_at"] == rules.to_iso(at(days=1))
    assert view["eligible"] is False


def test_select_next_reports_why_none_is_eligible():
    guides = [{"key": "a", "name": "A", "activation": activation(repeat={"enabled": False})}]
    order = {"guide_keys": ["a"], "throttling": {"enabled": False}}
    session = rules.default_session("app", "dana", BASE)
    session = rules.apply_dismissal(session, "a", "dismissed", BASE)
    chosen = rules.select_next(guides, order, session, at(days=7))
    assert chosen["guide"] is None
    assert "no guide is eligible" in chosen["blocked"]


def test_stop_after_label_reads_the_mode():
    assert rules.stop_after_label(None) == "Never"
    assert rules.stop_after_label(activation(stop_after={"mode": "count", "count": 4})) == (
        "After 4 dismissals"
    )


# --------------------------------------------------------------------------- #
# The engine over a store
# --------------------------------------------------------------------------- #


def test_create_validates_and_reports_every_problem(store: RecordStore):
    engine = GuidePrompts(store)
    with pytest.raises(KeyInvalid):
        engine.create(key="bad key", name="X", actor="d", source="test", fields={})
    with pytest.raises(GuideInvalid) as caught:
        engine.create(key="okay_key", name="", actor="d", source="test", fields={})
    assert "name is required" in caught.value.problems


def test_create_refuses_a_duplicate_key(store: RecordStore):
    engine = GuidePrompts(store)
    author(engine)
    with pytest.raises(KeyTaken):
        author(engine)


def test_create_refuses_a_reserved_key_that_is_soft_deleted(store: RecordStore):
    engine = GuidePrompts(store)
    author(engine)
    engine.delete("welcome_tour", actor="d", source="test")
    with pytest.raises(KeyTaken):
        author(engine)


def test_a_guide_view_normalises_its_activation(store: RecordStore):
    engine = GuidePrompts(store)
    view = author(engine, activation={"ignore_throttling": True})
    assert view["activation"]["repeat"]["enabled"] is False
    assert view["activation"]["delay_seconds"] == 0
    assert view["stop_after_label"] == "Never"
    assert view["issues"] == []


def test_update_refuses_a_key_change(store: RecordStore):
    engine = GuidePrompts(store)
    author(engine)
    with pytest.raises(KeyImmutable):
        engine.update("welcome_tour", {"key": "renamed"}, actor="d", source="test")


def test_update_can_change_the_activation(store: RecordStore):
    engine = GuidePrompts(store)
    author(engine)
    updated = engine.update(
        "welcome_tour",
        {"activation": activation(stop_after={"mode": "count", "count": 5})},
        actor="d",
        source="test",
    )
    assert updated["stop_after_label"] == "After 5 dismissals"


def test_update_does_not_mutate_a_deleted_guide(store: RecordStore):
    engine = GuidePrompts(store)
    author(engine)
    engine.delete("welcome_tour", actor="d", source="test")
    with pytest.raises(GuideNotFound):
        engine.update("welcome_tour", {"name": "Nope"}, actor="d", source="test")


def test_delete_is_soft_and_restore_undoes_it(store: RecordStore):
    engine = GuidePrompts(store)
    author(engine)
    deleted = engine.delete("welcome_tour", actor="d", source="test")
    assert deleted["deleted"] is True
    assert engine.guides() == []
    assert len(engine.guides(include_deleted=True)) == 1
    restored = engine.restore("welcome_tour", actor="d", source="test")
    assert restored["deleted"] is False
    with pytest.raises(GuideInvalid):
        engine.restore("welcome_tour", actor="d", source="test")


def test_guides_filter_the_way_the_ordering_table_filters(store: RecordStore):
    engine = GuidePrompts(store)
    author(engine, key="welcome_tour", name="Welcome tour", product_area="Sales room")
    author(
        engine,
        key="pricing_nudge",
        name="Pricing nudge",
        product_area="Pricing",
        category="Nurture",
        page_location="Pricing",
    )
    assert [g["key"] for g in engine.guides(status="public")] == ["pricing_nudge", "welcome_tour"]
    assert [g["key"] for g in engine.guides(q="pricing")] == ["pricing_nudge"]
    assert [g["key"] for g in engine.guides(product_area="Sales room")] == ["welcome_tour"]
    assert [g["key"] for g in engine.guides(category="Nurture")] == ["pricing_nudge"]
    assert [g["key"] for g in engine.guides(page_location="Pricing")] == ["pricing_nudge"]


def test_summary_counts_the_states_that_matter(store: RecordStore):
    engine = GuidePrompts(store)
    author(engine, key="a_guide", activation=activation(ignore_throttling=True))
    author(engine, key="b_guide", activation=activation())
    summary = engine.summary()
    assert summary["total"] == 2
    assert summary["unthrottled"] == 1
    assert summary["repeating"] == 2
    assert summary["by_status"] == {"public": 2}


def test_set_order_refuses_an_unknown_key(store: RecordStore):
    engine = GuidePrompts(store)
    author(engine)
    with pytest.raises(OrderInvalid):
        engine.set_order(
            "app", guide_keys=["welcome_tour", "ghost"], throttling=None, actor="d", source="test"
        )


def test_set_order_then_read_it_back(store: RecordStore):
    engine = GuidePrompts(store)
    author(engine)
    engine.set_order(
        "app",
        guide_keys=["welcome_tour"],
        throttling={"enabled": True, "count": 2, "unit": "hour"},
        actor="d",
        source="test",
    )
    view = engine.order_view("app")
    assert view["configured"] is True
    assert view["guide_keys"] == ["welcome_tour"]
    assert view["throttling"] == {"enabled": True, "count": 2, "unit": "hour"}


def test_an_absent_order_reads_as_unconfigured(store: RecordStore):
    engine = GuidePrompts(store)
    view = engine.order_view("nothing")
    assert view["configured"] is False
    assert view["guide_keys"] == []
    assert view["throttling"]["enabled"] is False


def test_move_guide_reorders_the_stored_list(store: RecordStore):
    engine = GuidePrompts(store)
    author(engine, key="a_guide")
    author(engine, key="b_guide")
    author(engine, key="c_guide")
    engine.set_order(
        "app",
        guide_keys=["a_guide", "b_guide", "c_guide"],
        throttling=None,
        actor="d",
        source="test",
    )
    engine.move_guide("app", "c_guide", "top", actor="d", source="test")
    assert engine.order_view("app")["guide_keys"] == ["c_guide", "a_guide", "b_guide"]


def test_record_event_requires_a_live_guide(store: RecordStore):
    engine = GuidePrompts(store)
    with pytest.raises(GuideNotFound):
        engine.record_event(
            "app", "dana", event="shown", key="ghost", at=BASE, actor="d", source="test"
        )


def test_record_event_requires_a_known_event(store: RecordStore):
    engine = GuidePrompts(store)
    author(engine)
    with pytest.raises(EventInvalid):
        engine.record_event(
            "app", "dana", event="clicked", key="welcome_tour", at=BASE, actor="d", source="test"
        )


def test_a_programmatic_launch_is_counted_separately(store: RecordStore):
    engine = GuidePrompts(store)
    author(engine)
    engine.record_event(
        "app",
        "dana",
        event="shown",
        key="welcome_tour",
        at=BASE,
        programmatic=True,
        actor="d",
        source="test",
    )
    session = engine.session_view("app", "dana")
    assert session["programmatic_launches"] == 1
    assert session["active_guide_key"] == "welcome_tour"


def test_ranking_and_next_agree_over_the_store(store: RecordStore):
    engine = GuidePrompts(store)
    author(engine, key="welcome_tour", activation=activation())
    author(
        engine,
        key="critical",
        activation=activation(ignore_throttling=True, repeat={"enabled": False}),
    )
    engine.set_order(
        "app",
        guide_keys=["welcome_tour", "critical"],
        throttling={"enabled": True, "count": 1, "unit": "day"},
        actor="d",
        source="test",
    )
    ranking = engine.ranking("app", "dana", BASE)
    assert [row["key"] for row in ranking["ranking"]] == ["critical", "welcome_tour"]
    assert ranking["ranking"][0]["tier"] == 0
    chosen = engine.next_guide("app", "dana", BASE)
    assert chosen["guide"]["key"] == "critical"

    engine.record_event(
        "app", "dana", event="dismissed", key="critical", at=BASE, actor="d", source="test"
    )
    blocked = engine.next_guide("app", "dana", at(hours=1))
    assert blocked["guide"] is None
    assert "no guide is eligible" in blocked["blocked"]
    released = engine.next_guide("app", "dana", at(days=1))
    assert released["guide"]["key"] == "welcome_tour"


def test_the_throttle_starts_on_dismissal_not_on_display(store: RecordStore):
    engine = GuidePrompts(store)
    author(engine, key="a_guide", activation=activation())
    author(engine, key="b_guide", activation=activation())
    engine.set_order(
        "app",
        guide_keys=["a_guide", "b_guide"],
        throttling={"enabled": True, "count": 1, "unit": "day"},
        actor="d",
        source="test",
    )
    engine.record_event(
        "app", "dana", event="shown", key="a_guide", at=BASE, actor="d", source="test"
    )
    session = engine.session_view("app", "dana")
    assert session["last_dismissed_at"] is None
    chosen = engine.next_guide("app", "dana", at(minutes=1))
    assert chosen["guide"]["key"] == "b_guide"


def test_the_session_view_reads_as_unconfigured_before_any_event(store: RecordStore):
    engine = GuidePrompts(store)
    assert engine.session_view("app", "dana")["configured"] is False


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #


def test_seed_returns_a_cp1252_encodable_summary(store: RecordStore):
    summary = seed(store.db, {"now": BASE})
    assert seed(store.db, {"now": BASE}) == "already seeded"
    assert isinstance(summary, str) and summary
    # The seeder prints this on a Windows console; one non-encodable character
    # once broke the whole seed, so it is proven here.
    summary.encode("cp1252")
    assert "guides" in summary


def test_seed_creates_the_states_the_page_has_to_show(store: RecordStore):
    seed(store.db, {"now": BASE})
    engine = GuidePrompts(store)
    keys = {g["key"] for g in engine.guides()}
    assert keys == {"welcome_tour", "critical_security_notice", "pricing_nudge", "draft_survey"}
    order = engine.order_view("demo-app")
    assert order["configured"] is True
    assert order["throttling"]["enabled"] is True
    session = engine.session_view("demo-app", "dana")
    assert session["last_dismissed_at"] is not None
    assert session["guides"]["welcome_tour"]["dismissed_count"] == 1


# --------------------------------------------------------------------------- #
# The dependency direction
# --------------------------------------------------------------------------- #


def domain_paths() -> list[Path]:
    return sorted(Path(inspect.getfile(GuidePrompts)).parent.glob("*.py"))


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


def test_the_collections_are_all_prefixed_to_this_ticket():
    assert all(value.startswith("wf118_") for value in vocabulary()["collections"].values())
    assert vocabulary()["collections"]["guides"] == GUIDES


def test_the_error_hierarchy_maps_every_type_to_a_status_and_a_code():
    from dsr.guide_prompts.errors import REFUSAL_STATUS, GuidePromptsRefusal

    for error_type, (status, code) in REFUSAL_STATUS.items():
        assert issubclass(error_type, GuidePromptsRefusal)
        assert 400 <= status < 500
        assert code == error_type.code
    for error_type in (
        EventInvalid,
        GuideInvalid,
        GuideNotFound,
        KeyImmutable,
        KeyInvalid,
        KeyTaken,
        OrderInvalid,
    ):
        assert error_type in REFUSAL_STATUS, error_type.__name__
