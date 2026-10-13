"""WF-108's domain: the pure rules and the engine, driven with a clock the test owns.

No test here carries a fixed date. Every instant comes from ``CLOCK`` or from a weekday
walked back off it, and every duration is an offset from one of those. The ``store``
fixture points at a fresh in-memory database per test, so nothing here can see another
test's rows and nothing depends on the day the suite runs.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from dsr.conversation_chase import vocabulary as chase_vocab
from dsr.sla_timers import rules, vocabulary as vocab
from dsr.sla_timers.engine import SlaEngine
from dsr.sla_timers.rules import SlaRefusal
from dsr.store import RecordStore


def _monday_at(hour: int, minute: int) -> datetime:
    today = datetime(2026, 3, 2, tzinfo=timezone.utc)
    monday = today - timedelta(days=today.weekday())
    return monday.replace(hour=hour, minute=minute)


CLOCK = _monday_at(14, 0)
QUARTER_HOUR = 15 * 60
SCHEDULE = rules.default_schedule()
PREFIX = "/api/wf-108"


def part(author: str, at: datetime, body: str = "x") -> dict[str, Any]:
    return {"author_kind": author, "at": rules.stamp(at), "body": body}


# --------------------------------------------------------------------------- #
# Vocabulary
# --------------------------------------------------------------------------- #


class TestVocabulary:
    def test_the_conversation_states_are_wf107s_and_not_a_second_copy(self):
        assert vocab.CONVERSATION_STATES == chase_vocab.CONVERSATION_STATES
        assert vocab.STATE_SNOOZED == chase_vocab.STATE_SNOOZED

    def test_it_publishes_the_four_metrics_and_their_labels(self):
        published = vocab.published_vocabulary()
        assert published["metrics"] == list(vocab.METRIC_KINDS)
        assert set(published["metric_labels"]) == set(vocab.METRIC_KINDS)
        assert published["metric_short"]["frt"] == "FRT"

    def test_it_publishes_the_three_badges_and_no_fourth(self):
        published = vocab.published_vocabulary()
        assert published["badges"] == ["grey", "orange", "red"]

    def test_it_publishes_the_three_outcomes(self):
        assert vocab.OUTCOMES == ("hit", "miss", "fixed")

    def test_every_evidence_quote_is_the_researched_sentence(self):
        evidence = vocab.published_vocabulary()["evidence"]
        assert "continuous background timer" in evidence["continuous_timer"]
        assert "5:50pm" in evidence["office_hours_example"]
        assert "Red: Overdue" in evidence["badge_thresholds"]
        assert "automatically unsnoozed" in evidence["auto_unsnooze"]

    def test_a_conversation_earns_frt_nrt_ttc_and_a_ticket_earns_ttr(self):
        assert vocab.metrics_for_kind("conversation") == ("frt", "nrt", "ttc")
        assert vocab.metrics_for_kind("ticket") == ("frt", "nrt", "ttr")

    def test_every_refusal_code_carries_a_status(self):
        for code, (status, detail) in vocab.ERROR_CODES.items():
            assert status in (404, 409, 422), code
            assert detail and detail.endswith("."), code


# --------------------------------------------------------------------------- #
# Targets and policy normalisation
# --------------------------------------------------------------------------- #


class TestTargets:
    def test_a_target_below_the_floor_is_refused(self):
        with pytest.raises(SlaRefusal) as caught:
            rules.require_target(30, "frt")
        assert caught.value.code == "target_out_of_range"
        assert caught.value.status == 422

    def test_a_target_above_the_ceiling_is_refused(self):
        with pytest.raises(SlaRefusal):
            rules.require_target(vocab.MAX_TARGET_SECONDS + 1, "frt")

    def test_a_non_numeric_target_is_refused(self):
        with pytest.raises(SlaRefusal) as caught:
            rules.require_target("fifteen minutes", "frt")
        assert caught.value.code == "target_not_a_number"

    def test_a_missing_targets_object_takes_the_derived_defaults(self):
        assert rules.normalise_targets(None) == vocab.default_targets()

    def test_an_empty_targets_object_is_refused(self):
        with pytest.raises(SlaRefusal) as caught:
            rules.normalise_targets({})
        assert caught.value.code == "policy_needs_targets"

    def test_an_unknown_metric_is_refused(self):
        with pytest.raises(SlaRefusal) as caught:
            rules.normalise_targets({"first_reply": 600})
        assert caught.value.code == "unknown_metric"

    def test_a_partial_targets_object_keeps_the_defaults_for_the_rest(self):
        targets = rules.normalise_targets({"frt": 600})
        assert targets["frt"] == 600
        assert targets["ttc"] == vocab.DEFAULT_TARGETS["ttc"]


class TestPolicyNormalisation:
    def test_a_policy_needs_a_name(self):
        with pytest.raises(SlaRefusal) as caught:
            rules.normalise_policy({"targets": {"frt": 600}})
        assert caught.value.code == "policy_needs_a_name"

    def test_the_pause_rules_default_to_both_on(self):
        policy = rules.normalise_policy({"name": "Standard"})
        assert policy["pause_reasons"] == list(vocab.DEFAULT_PAUSE_REASONS)

    def test_the_pause_rules_can_both_be_turned_off(self):
        policy = rules.normalise_policy({"name": "Standard", "pause_reasons": []})
        assert policy["pause_reasons"] == []

    def test_an_unknown_pause_reason_is_refused(self):
        with pytest.raises(SlaRefusal) as caught:
            rules.normalise_policy({"name": "Standard", "pause_reasons": ["whenever"]})
        assert caught.value.code == "unknown_pause_reason"


# --------------------------------------------------------------------------- #
# Office hours
# --------------------------------------------------------------------------- #


class TestOfficeHours:
    def test_the_researched_worked_example_holds(self):
        """17:50 plus a 15-minute first response is 09:05 the next working day."""

        anchor = _monday_at(17, 50)
        due = rules.chase_expected_reply_time(anchor, QUARTER_HOUR, SCHEDULE)
        assert due == _monday_at(0, 0) + timedelta(days=1, hours=9, minutes=5)

    def test_the_worked_example_reads_as_tuesday_morning(self):
        anchor = _monday_at(17, 50)
        due = rules.chase_expected_reply_time(anchor, QUARTER_HOUR, SCHEDULE)
        assert due.weekday() == 1  # Tuesday
        assert (due.hour, due.minute) == (9, 5)

    def test_friday_evening_walks_over_the_weekend(self):
        friday = _monday_at(17, 50) + timedelta(days=4)
        due = rules.chase_expected_reply_time(friday, QUARTER_HOUR, SCHEDULE)
        assert due.weekday() == 0  # the following Monday
        assert (due.hour, due.minute) == (9, 5)

    def test_a_closed_day_contributes_no_working_seconds(self):
        saturday = _monday_at(10, 0) + timedelta(days=5)
        assert rules.working_seconds(saturday, saturday + timedelta(hours=8), SCHEDULE) == 0

    def test_only_the_open_minutes_of_a_span_count(self):
        start = _monday_at(17, 50)
        assert rules.working_seconds(start, start + timedelta(hours=1), SCHEDULE) == 10 * 60

    def test_a_pause_subtracts_its_overlap(self):
        start = _monday_at(9, 0)
        end = _monday_at(11, 0)
        pause = [(start + timedelta(minutes=30), start + timedelta(minutes=60))]
        assert rules.working_seconds(start, end, SCHEDULE, pause) == 90 * 60

    def test_an_open_pause_stops_the_clock_now(self):
        start = _monday_at(9, 0)
        pauses = [(start + timedelta(minutes=10), None)]
        assert rules.paused_now(pauses, start + timedelta(minutes=20)) is True
        assert rules.paused_now(pauses, start + timedelta(minutes=5)) is False


# --------------------------------------------------------------------------- #
# Anchors, pauses and one metric
# --------------------------------------------------------------------------- #


class TestPauseIntervals:
    def test_a_snoozed_conversation_has_an_open_pause(self):
        window = rules.snooze_interval({"state": "snoozed", "snoozed_at": rules.stamp(CLOCK)})
        assert window == (CLOCK, None)

    def test_an_unsnoozed_conversation_has_a_closed_pause(self):
        later = CLOCK + timedelta(minutes=30)
        window = rules.snooze_interval(
            {
                "state": "open",
                "snoozed_at": rules.stamp(CLOCK),
                "unsnoozed_at": rules.stamp(later),
            }
        )
        assert window == (CLOCK, later)

    def test_a_waiting_window_opens_at_a_teammate_reply(self):
        closed, open_since = rules.waiting_intervals(
            [part("customer", CLOCK), part("teammate", CLOCK + timedelta(minutes=5))]
        )
        assert closed == []
        assert open_since == CLOCK + timedelta(minutes=5)

    def test_a_customer_reply_closes_the_waiting_window(self):
        closed, open_since = rules.waiting_intervals(
            [
                part("customer", CLOCK),
                part("teammate", CLOCK + timedelta(minutes=5)),
                part("customer", CLOCK + timedelta(minutes=20)),
            ]
        )
        assert closed == [(CLOCK + timedelta(minutes=5), CLOCK + timedelta(minutes=20))]
        assert open_since is None

    def test_the_policy_selects_which_pauses_apply(self):
        conversation = {"state": "snoozed", "snoozed_at": rules.stamp(CLOCK)}
        parts = [part("customer", CLOCK)]
        off = rules.pause_intervals({"pause_reasons": []}, conversation, parts)
        assert off == []
        snooze_only = rules.pause_intervals(
            {"pause_reasons": [vocab.PAUSE_SNOOZED]}, conversation, parts
        )
        assert snooze_only == [(CLOCK, None)]


class TestMetricProgress:
    def test_frt_is_met_by_a_reply_inside_the_target(self):
        parts = [part("customer", CLOCK), part("teammate", CLOCK + timedelta(minutes=5))]
        progress = rules.progress_for_policy(
            {
                "targets": {"frt": QUARTER_HOUR},
                "object_kind": "conversation",
                "applied_at": rules.stamp(CLOCK),
            },
            {"state": "open"},
            parts,
            CLOCK + timedelta(minutes=30),
        )
        frt = next(entry for entry in progress if entry["metric"] == "frt")
        assert frt["state"] == vocab.METRIC_MET
        assert frt["elapsed_seconds"] == 5 * 60

    def test_frt_is_breached_by_no_reply(self):
        parts = [part("customer", CLOCK)]
        progress = rules.progress_for_policy(
            {
                "targets": {"frt": QUARTER_HOUR},
                "object_kind": "conversation",
                "applied_at": rules.stamp(CLOCK),
            },
            {"state": "open"},
            parts,
            CLOCK + timedelta(hours=1),
        )
        frt = next(entry for entry in progress if entry["metric"] == "frt")
        assert frt["state"] == vocab.METRIC_BREACHED
        assert frt["overdue_seconds"] == 45 * 60

    def test_a_pending_metric_carries_a_deadline_inside_office_hours(self):
        parts = [part("customer", CLOCK)]
        progress = rules.progress_for_policy(
            {
                "targets": {"frt": QUARTER_HOUR},
                "object_kind": "conversation",
                "applied_at": rules.stamp(CLOCK),
            },
            {"state": "open"},
            parts,
            CLOCK + timedelta(minutes=5),
        )
        frt = next(entry for entry in progress if entry["metric"] == "frt")
        assert frt["state"] == vocab.METRIC_PENDING
        assert rules.coerce_instant(frt["deadline"]) == CLOCK + timedelta(minutes=15)

    def test_a_metric_with_no_anchor_is_not_applicable(self):
        progress = rules.progress_for_policy(
            {
                "targets": {"frt": QUARTER_HOUR},
                "object_kind": "conversation",
                "applied_at": rules.stamp(CLOCK),
            },
            {"state": "open"},
            [],
            CLOCK,
        )
        frt = next(entry for entry in progress if entry["metric"] == "frt")
        assert frt["state"] == vocab.METRIC_NOT_APPLICABLE

    def test_the_ticket_kind_reports_ttr_and_not_ttc(self):
        progress = rules.progress_for_policy(
            {"targets": {"ttr": 3600}, "object_kind": "ticket", "applied_at": rules.stamp(CLOCK)},
            {"state": "open"},
            [part("customer", CLOCK)],
            CLOCK + timedelta(minutes=10),
        )
        metrics = {entry["metric"] for entry in progress}
        assert "ttr" in metrics
        assert "ttc" not in metrics


class TestBadgeAndOutcome:
    def test_the_three_badge_boundaries(self):
        assert rules.badge_for(600) == vocab.BADGE_GREY
        assert rules.badge_for(5 * 60) == vocab.BADGE_GREY
        assert rules.badge_for(5 * 60 - 1) == vocab.BADGE_ORANGE
        assert rules.badge_for(0) == vocab.BADGE_RED
        assert rules.badge_for(120, breached=True) == vocab.BADGE_RED

    def test_a_breach_outranks_a_running_metric(self):
        metrics = [
            {"metric": "frt", "state": "breached", "overdue_seconds": 300, "short": "FRT"},
            {"metric": "ttc", "state": "pending", "remaining_seconds": 60, "short": "TTC"},
        ]
        assert rules.timer_state(metrics) == vocab.TIMER_BREACHED
        assert rules.nearest_target(metrics)["metric"] == "frt"

    def test_the_nearest_running_target_is_the_least_time_left(self):
        metrics = [
            {"metric": "frt", "state": "pending", "remaining_seconds": 300, "short": "FRT"},
            {"metric": "ttc", "state": "pending", "remaining_seconds": 60, "short": "TTC"},
        ]
        nearest = rules.nearest_target(metrics)
        assert nearest["metric"] == "ttc"
        assert nearest["badge"] == vocab.BADGE_ORANGE

    def test_a_fully_met_timer_is_met(self):
        metrics = [
            {"metric": "frt", "state": "met", "short": "FRT"},
            {"metric": "ttc", "state": "met", "short": "TTC"},
        ]
        assert rules.timer_state(metrics) == vocab.TIMER_MET
        assert rules.nearest_target(metrics) is None

    def test_the_outcome_rule_is_hit_miss_or_fixed(self):
        assert rules.classify_outcome({"state": "met"}, False) == vocab.OUTCOME_HIT
        assert rules.classify_outcome({"state": "breached"}, False) == vocab.OUTCOME_MISS
        assert rules.classify_outcome({"state": "breached"}, True) == vocab.OUTCOME_FIXED
        assert rules.classify_outcome({"state": "pending"}, False) is None

    def test_a_snoozed_already_breached_timer_reopens(self):
        metrics = [{"metric": "frt", "state": "breached"}]
        assert rules.is_reopening_breach(metrics, {"state": "snoozed"}) is True
        assert rules.is_reopening_breach(metrics, {"state": "open"}) is False


# --------------------------------------------------------------------------- #
# The engine
# --------------------------------------------------------------------------- #


@pytest.fixture
def engine(store: RecordStore) -> SlaEngine:
    return SlaEngine(store, now=lambda: CLOCK)


def at_engine(store: RecordStore, minutes: float) -> SlaEngine:
    frozen = CLOCK + timedelta(minutes=minutes)
    return SlaEngine(store, now=lambda: frozen)


def make_room(store: RecordStore) -> str:
    return store.create("room", {"name": "Northwind", "account": "Northwind"}, actor="dana")["id"]


def make_conversation(
    store: RecordStore, room_id: str, *, state: str = "open", kind: str | None = None
) -> str:
    data: dict[str, Any] = {
        vocab.ROOM_REF: room_id,
        "state": state,
        "origin": "inbox",
        "inbox": "sales",
    }
    if kind:
        data["kind"] = kind
    return store.create(vocab.CONVERSATIONS, data, room_id=room_id, actor="dana")["id"]


def add_part(
    store: RecordStore, room_id: str, conversation_id: str, author: str, at: datetime
) -> None:
    store.create(
        vocab.PARTS,
        {
            vocab.ROOM_REF: room_id,
            "conversation_id": conversation_id,
            "author_kind": author,
            "at": rules.stamp(at),
            "body": "x",
        },
        room_id=room_id,
        actor="dana",
    )


def make_policy(engine: SlaEngine, room_id: str, **overrides: Any) -> dict[str, Any]:
    payload = {
        "name": "Enterprise first response",
        "targets": {"frt": QUARTER_HOUR, "nrt": 30 * 60, "ttc": 8 * 60 * 60},
        "teams": ["enterprise"],
        "workflow_name": "Escalate enterprise leads",
        "live": True,
    }
    payload.update(overrides)
    return engine.create_policy(
        room_id, payload, source=f"POST {PREFIX}/rooms/{{room_id}}/policies", actor="dana"
    )


class TestEnginePolicies:
    def test_a_created_policy_carries_its_targets_and_metrics(self, engine: SlaEngine):
        room_id = make_room(engine.store)
        policy = make_policy(engine, room_id)
        assert policy["targets"]["frt"] == QUARTER_HOUR
        assert policy["metrics"] == ["frt", "nrt", "ttc"]
        assert policy["live"] is True

    def test_editing_a_policy_revalidates_the_target(self, engine: SlaEngine):
        room_id = make_room(engine.store)
        policy = make_policy(engine, room_id)
        with pytest.raises(SlaRefusal):
            engine.update_policy(
                policy["id"],
                {"targets": {"frt": 1}},
                source=f"PATCH {PREFIX}/rooms/{{room_id}}/policies/{{policy_id}}",
            )

    def test_archiving_a_policy_hides_it_but_keeps_it_readable(self, engine: SlaEngine):
        room_id = make_room(engine.store)
        policy = make_policy(engine, room_id)
        engine.delete_policy(
            policy["id"], source=f"DELETE {PREFIX}/rooms/{{room_id}}/policies/{{policy_id}}"
        )
        assert engine.policies(room_id) == []
        assert engine.policy_view(policy["id"])["archived"] is True

    def test_an_archived_policy_cannot_be_applied(self, engine: SlaEngine):
        room_id = make_room(engine.store)
        policy = make_policy(engine, room_id)
        conversation = make_conversation(engine.store, room_id)
        engine.delete_policy(
            policy["id"], source=f"DELETE {PREFIX}/rooms/{{room_id}}/policies/{{policy_id}}"
        )
        with pytest.raises(SlaRefusal) as caught:
            engine.apply(
                conversation,
                {"policy_id": policy["id"]},
                source=f"POST {PREFIX}/rooms/{{room_id}}/conversations/{{conversation_id}}/sla",
            )
        assert caught.value.code == "policy_archived"


class TestEngineApply:
    def test_applying_creates_an_active_timer_with_the_policy_snapshot(self, engine: SlaEngine):
        room_id = make_room(engine.store)
        policy = make_policy(engine, room_id)
        conversation = make_conversation(engine.store, room_id)
        timer = engine.apply(
            conversation,
            {"policy_id": policy["id"]},
            source=f"POST {PREFIX}/rooms/{{room_id}}/conversations/{{conversation_id}}/sla",
        )
        assert timer["active"] is True
        assert timer["policy_name"] == "Enterprise first response"
        assert engine.timer_for(conversation)["id"] == timer["id"]

    def test_only_one_active_sla_survives_a_second_apply(self, engine: SlaEngine):
        room_id = make_room(engine.store)
        first = make_policy(engine, room_id)
        second = make_policy(engine, room_id, name="Standard")
        conversation = make_conversation(engine.store, room_id)
        source = f"POST {PREFIX}/rooms/{{room_id}}/conversations/{{conversation_id}}/sla"
        engine.apply(conversation, {"policy_id": first["id"]}, source=source)
        second_timer = engine.apply(conversation, {"policy_id": second["id"]}, source=source)
        active = engine.store.find(vocab.TIMERS, {"conversation_id": conversation}, limit=10)
        assert len(active) == 2, "the replaced timer is kept as history"
        assert engine.timer_for(conversation)["id"] == second_timer["id"]
        assert engine.timer_for(conversation)["policy_name"] == "Standard"

    def test_a_closed_conversation_cannot_take_an_sla(self, engine: SlaEngine):
        room_id = make_room(engine.store)
        policy = make_policy(engine, room_id)
        conversation = make_conversation(engine.store, room_id, state="closed")
        with pytest.raises(SlaRefusal) as caught:
            engine.apply(
                conversation,
                {"policy_id": policy["id"]},
                source=f"POST {PREFIX}/rooms/{{room_id}}/conversations/{{conversation_id}}/sla",
            )
        assert caught.value.code == "conversation_closed"

    def test_removing_with_no_active_sla_is_a_conflict(self, engine: SlaEngine):
        room_id = make_room(engine.store)
        conversation = make_conversation(engine.store, room_id)
        with pytest.raises(SlaRefusal) as caught:
            engine.remove(
                conversation,
                source=f"POST {PREFIX}/rooms/{{room_id}}/conversations/{{conversation_id}}/sla/remove",
            )
        assert caught.value.code == "no_active_sla"
        assert caught.value.status == 409

    def test_removing_writes_an_activity_row(self, engine: SlaEngine):
        room_id = make_room(engine.store)
        policy = make_policy(engine, room_id)
        conversation = make_conversation(engine.store, room_id)
        engine.apply(
            conversation,
            {"policy_id": policy["id"]},
            source=f"POST {PREFIX}/rooms/{{room_id}}/conversations/{{conversation_id}}/sla",
        )
        engine.remove(
            conversation,
            source=f"POST {PREFIX}/rooms/{{room_id}}/conversations/{{conversation_id}}/sla/remove",
        )
        entries = engine.activity(room_id, conversation_id=conversation)
        assert any(entry["type"] == vocab.ACTIVITY_SLA_REMOVED for entry in entries)


class TestEngineTimerAndEvaluate:
    def _conversation_with_timer(self, engine: SlaEngine, **policy: Any) -> tuple[str, str]:
        room_id = make_room(engine.store)
        created = make_policy(engine, room_id, **policy)
        conversation = make_conversation(engine.store, room_id)
        engine.apply(
            conversation,
            {"policy_id": created["id"]},
            source=f"POST {PREFIX}/rooms/{{room_id}}/conversations/{{conversation_id}}/sla",
        )
        return room_id, conversation

    def test_a_met_frt_leaves_the_close_metric_running(self, engine: SlaEngine):
        # With the waiting pause off, a teammate as the last author does not stop the clock.
        room_id, conversation = self._conversation_with_timer(
            engine, pause_reasons=[vocab.PAUSE_SNOOZED]
        )
        add_part(engine.store, room_id, conversation, "customer", CLOCK - timedelta(minutes=10))
        add_part(engine.store, room_id, conversation, "teammate", CLOCK - timedelta(minutes=5))
        timer = engine.timer_for(conversation)
        assert timer["metrics"][0]["state"] == vocab.METRIC_MET
        assert timer["state"] == vocab.TIMER_RUNNING
        assert timer["badge"] == vocab.BADGE_GREY

    def test_the_badge_goes_red_when_a_metric_is_overdue(self, engine: SlaEngine):
        room_id, conversation = self._conversation_with_timer(engine)
        add_part(engine.store, room_id, conversation, "customer", CLOCK - timedelta(minutes=40))
        timer = engine.timer_for(conversation)
        assert timer["badge"] == vocab.BADGE_RED
        assert timer["state"] == vocab.TIMER_BREACHED

    def test_the_waiting_pause_stops_the_clock(self, engine: SlaEngine):
        room_id, conversation = self._conversation_with_timer(engine)
        add_part(engine.store, room_id, conversation, "customer", CLOCK - timedelta(minutes=10))
        add_part(engine.store, room_id, conversation, "teammate", CLOCK - timedelta(minutes=5))
        timer = engine.timer_for(conversation)
        ttc = next(entry for entry in timer["metrics"] if entry["metric"] == "ttc")
        assert ttc["state"] == vocab.METRIC_PAUSED
        assert ttc["deadline"] is None

    def test_the_sweep_persists_the_state_and_names_the_transitions(self, engine: SlaEngine):
        room_id, conversation = self._conversation_with_timer(engine)
        add_part(engine.store, room_id, conversation, "customer", CLOCK - timedelta(minutes=40))
        result = at_engine(engine.store, 60).evaluate(
            room_id,
            {"now": rules.stamp(CLOCK + timedelta(hours=1))},
            source=f"POST {PREFIX}/rooms/{{room_id}}/timers/evaluate",
            actor="scheduler",
        )
        assert result["checked"] == 1
        assert result["counts"][vocab.TIMER_BREACHED] == 1
        assert any(t["type"] == vocab.ACTIVITY_SLA_BREACHED for t in result["transitions"])

    def test_a_second_sweep_does_not_re_log_the_transition(self, engine: SlaEngine):
        room_id, conversation = self._conversation_with_timer(engine)
        add_part(engine.store, room_id, conversation, "customer", CLOCK - timedelta(minutes=40))
        soon = at_engine(engine.store, 60)
        first = soon.evaluate(
            room_id,
            {"now": rules.stamp(CLOCK + timedelta(hours=1))},
            source=f"POST {PREFIX}/rooms/{{room_id}}/timers/evaluate",
        )
        second = soon.evaluate(
            room_id,
            {"now": rules.stamp(CLOCK + timedelta(hours=2))},
            source=f"POST {PREFIX}/rooms/{{room_id}}/timers/evaluate",
        )
        assert first["transitions"]
        assert second["transitions"] == []

    def test_a_snoozed_already_breached_timer_reopens_the_conversation(self, engine: SlaEngine):
        room_id = make_room(engine.store)
        policy = make_policy(engine, room_id)
        conversation = make_conversation(engine.store, room_id)
        add_part(engine.store, room_id, conversation, "customer", CLOCK - timedelta(hours=4))
        engine.apply(
            conversation,
            {"policy_id": policy["id"]},
            source=f"POST {PREFIX}/rooms/{{room_id}}/conversations/{{conversation_id}}/sla",
        )
        engine.store.update(
            conversation,
            {"state": "snoozed", "snoozed_at": rules.stamp(CLOCK - timedelta(minutes=30))},
            actor="dana",
        )
        result = at_engine(engine.store, 1).evaluate(
            room_id,
            {"now": rules.stamp(CLOCK)},
            source=f"POST {PREFIX}/rooms/{{room_id}}/timers/evaluate",
        )
        assert result["reopened"] == [conversation]
        assert engine.store.get(conversation)["data"]["state"] == "open"
        assert any(t["type"] == vocab.ACTIVITY_SLA_REOPENED for t in result["transitions"])

    def test_a_reopened_breach_is_classified_as_fixed(self, engine: SlaEngine):
        room_id = make_room(engine.store)
        policy = make_policy(engine, room_id)
        conversation = make_conversation(engine.store, room_id)
        add_part(engine.store, room_id, conversation, "customer", CLOCK - timedelta(hours=4))
        engine.apply(
            conversation,
            {"policy_id": policy["id"]},
            source=f"POST {PREFIX}/rooms/{{room_id}}/conversations/{{conversation_id}}/sla",
        )
        engine.store.update(
            conversation,
            {"state": "snoozed", "snoozed_at": rules.stamp(CLOCK - timedelta(minutes=30))},
            actor="dana",
        )
        at_engine(engine.store, 1).evaluate(
            room_id,
            {"now": rules.stamp(CLOCK)},
            source=f"POST {PREFIX}/rooms/{{room_id}}/timers/evaluate",
        )
        report = engine.report(room_id)
        assert report["totals"][vocab.OUTCOME_FIXED] >= 1


class TestEngineViews:
    def test_the_office_hours_default_is_derived_and_flagged(self, engine: SlaEngine):
        room_id = make_room(engine.store)
        hours = engine.office_hours(room_id)
        assert hours["derived"] is True
        assert hours["schedule"]["saturday"]["open"] is None

    def test_saving_office_hours_marks_it_stored(self, engine: SlaEngine):
        room_id = make_room(engine.store)
        engine.save_office_hours(
            room_id,
            {"schedule": rules.default_schedule(), "timezone": "UTC"},
            source=f"PUT {PREFIX}/rooms/{{room_id}}/office-hours",
        )
        assert engine.office_hours(room_id)["stored"] is True

    def test_the_inbox_sorts_a_breach_before_a_running_timer(self, engine: SlaEngine):
        room_id = make_room(engine.store)
        policy = make_policy(engine, room_id)
        source = f"POST {PREFIX}/rooms/{{room_id}}/conversations/{{conversation_id}}/sla"
        near = make_conversation(engine.store, room_id)
        far = make_conversation(engine.store, room_id)
        engine.apply(near, {"policy_id": policy["id"]}, source=source)
        engine.apply(far, {"policy_id": policy["id"]}, source=source)
        add_part(engine.store, room_id, near, "customer", CLOCK - timedelta(minutes=2))
        add_part(engine.store, room_id, far, "customer", CLOCK - timedelta(hours=4))
        rows = engine.inbox(room_id, sort="next_target")["conversations"]
        assert rows[0]["conversation_id"] == far
        assert rows[0]["badge"] == vocab.BADGE_RED

    def test_the_report_rates_sum_to_one_over_decided_metrics(self, engine: SlaEngine):
        room_id = make_room(engine.store)
        policy = make_policy(engine, room_id)
        conversation = make_conversation(engine.store, room_id)
        engine.apply(
            conversation,
            {"policy_id": policy["id"]},
            source=f"POST {PREFIX}/rooms/{{room_id}}/conversations/{{conversation_id}}/sla",
        )
        add_part(engine.store, room_id, conversation, "customer", CLOCK - timedelta(hours=4))
        report = engine.report(room_id)
        assert report["totals"][vocab.OUTCOME_MISS] >= 1
        assert report["slas"][0]["rates"]["miss"] == 1.0

    def test_used_by_lists_the_workflow(self, engine: SlaEngine):
        room_id = make_room(engine.store)
        policy = make_policy(engine, room_id)
        conversation = make_conversation(engine.store, room_id)
        engine.apply(
            conversation,
            {"policy_id": policy["id"]},
            source=f"POST {PREFIX}/rooms/{{room_id}}/conversations/{{conversation_id}}/sla",
        )
        used = engine.used_by(policy["id"])
        assert used["workflows"] == ["Escalate enterprise leads"]
        assert used["active_timers"] == 1

    def test_the_summary_counts_policies_and_timers(self, engine: SlaEngine):
        room_id = make_room(engine.store)
        make_policy(engine, room_id)
        summary = engine.summary(room_id)
        assert summary["policies"] == 1
        assert summary["live_policies"] == 1
        assert summary["active_timers"] == 0
