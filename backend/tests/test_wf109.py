"""WF-109's domain rules, as pure functions over values.

Every test here calls the rules module with a workflow, a timer view, a conversation and a
list of parts. Nothing reads or writes a database and nothing depends on the day the suite
runs: the clock is passed in, so a lead window is a number this file chooses.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from dsr.sla_escalation import inferences, rules, vocabulary as vocab

FIXED = datetime(2026, 3, 2, tzinfo=timezone.utc)  # a Monday
NOW = FIXED.replace(hour=10, minute=0)
MINUTE = 60


def stamp(moment: datetime) -> str:
    return rules.stamp(moment)


def workflow(**over):
    """A normalised before-breach workflow watching FRT with a ten-minute lead."""

    body = {
        "name": "Pre-warn before first response",
        "trigger": vocab.TRIGGER_BEFORE_BREACH,
        "metric": vocab.METRIC_FRT,
        "lead_seconds": 10 * MINUTE,
        "actions": [{"kind": vocab.ACTION_TAG, "tag": "escalated"}],
        "live": True,
    }
    body.update(over)
    return rules.normalise_workflow(body)


def view(**over):
    """A minimal WF-108 timer view carrying one FRT entry."""

    entry = {
        "metric": vocab.METRIC_FRT,
        "label": vocab.METRIC_LABELS[vocab.METRIC_FRT],
        "short": "FRT",
        "target_seconds": 15 * MINUTE,
        "state": "pending",
        "state_label": "Countdown running",
        "anchor": stamp(NOW - timedelta(hours=1)),
        "completion": None,
        "deadline": stamp(NOW + timedelta(minutes=5)),
        "elapsed_seconds": 600.0,
        "remaining_seconds": 300.0,
        "overdue_seconds": 0.0,
    }
    entry.update(over.pop("entry", {}))
    return {
        "id": "timer_1",
        "room_id": "room_1",
        "conversation_id": "conv_1",
        "policy_id": "policy_1",
        "policy_name": "Enterprise first response",
        "state": "running",
        "nearest": None,
        "metrics": [entry],
        **over,
    }


OPEN = {"state": vocab.STATE_OPEN, "tags": []}


# --------------------------------------------------------------------------- #
# Vocabulary
# --------------------------------------------------------------------------- #


class TestVocabulary:
    def test_the_two_triggers_are_the_researched_ones(self):
        assert vocab.TRIGGER_KINDS == ("before_breach", "on_breach")
        assert vocab.TRIGGER_LABELS[vocab.TRIGGER_ON_BREACH] == "When SLA breaches"

    def test_the_four_targets_are_wf108s_and_keep_the_specs_names(self):
        assert vocab.METRIC_KINDS == ("frt", "nrt", "ttc", "ttr")
        assert vocab.METRIC_SPEC_LABELS[vocab.METRIC_FRT] == "First Response Time"
        assert vocab.METRIC_SPEC_LABELS[vocab.METRIC_TTC] == "Time to Close"
        assert vocab.METRIC_SPEC_LABELS[vocab.METRIC_TTR] == "Resolution Time"

    def test_the_conversation_states_are_wf107s_not_a_second_copy(self):
        from dsr.conversation_chase import vocabulary as chase

        assert vocab.CONVERSATION_STATES is chase.CONVERSATION_STATES
        assert vocab.STATE_SNOOZED == chase.STATE_SNOOZED

    def test_the_three_cancellations_are_all_present(self):
        assert set(vocab.CANCEL_REASONS) == {
            "teammate_replied",
            "conversation_snoozed",
            "conversation_closed",
        }

    def test_the_five_actions_include_the_data_connector(self):
        assert set(vocab.ACTION_KINDS) == {"assign", "notify", "tag", "note", "alert"}
        assert vocab.ACTION_ALERT in vocab.CONNECTOR_ACTIONS

    def test_the_lead_rule_is_strict(self):
        assert vocab.lead_is_shorter(600, 900) is True
        assert vocab.lead_is_shorter(900, 900) is False
        assert vocab.lead_is_shorter(0, 900) is False
        assert vocab.lead_is_shorter(1200, 900) is False

    def test_a_breach_outranks_a_pre_warning(self):
        assert vocab.LEVEL_RANK[vocab.LEVEL_BREACH] > vocab.LEVEL_RANK[vocab.LEVEL_PRE_WARNING]

    def test_every_refusal_code_carries_a_status_and_a_sentence(self):
        for code, (status, detail) in vocab.ERROR_CODES.items():
            assert status in (404, 409, 422), code
            assert detail and detail.endswith("."), code

    def test_the_published_vocabulary_is_serialisable_data(self):
        published = vocab.published_vocabulary()
        assert published["trigger_category_label"] == "During a conversation"
        assert published["collections"]["workflows"] == "wf109_escalation_workflow"
        assert published["evidence"]["one_minute_check"]
        assert published["evidence"]["gated"]

    def test_the_name_is_cp1252_encodable(self):
        vocab.NAME.encode("cp1252")
        vocab.BEFORE_BREACH_QUOTE.encode("cp1252")
        vocab.GATED_QUOTE.encode("cp1252")


# --------------------------------------------------------------------------- #
# Validators and normalisation
# --------------------------------------------------------------------------- #


class TestNormalisation:
    def test_a_workflow_with_no_name_is_refused(self):
        with pytest.raises(rules.EscalationRefusal) as caught:
            rules.normalise_workflow({"actions": [{"kind": "tag", "tag": "x"}]})
        assert caught.value.code == "workflow_needs_a_name"
        assert caught.value.status == 422

    def test_a_workflow_with_no_actions_is_refused(self):
        with pytest.raises(rules.EscalationRefusal) as caught:
            rules.normalise_workflow({"name": "W", "actions": []})
        assert caught.value.code == "workflow_needs_actions"

    def test_an_on_breach_workflow_drops_any_lead_time(self):
        fields = rules.normalise_workflow(
            {
                "name": "W",
                "trigger": vocab.TRIGGER_ON_BREACH,
                "metric": "frt",
                "lead_seconds": 600,
                "actions": [{"kind": "tag", "tag": "x"}],
            }
        )
        assert fields["lead_seconds"] is None

    def test_a_before_breach_workflow_defaults_its_lead_time(self):
        fields = rules.normalise_workflow(
            {"name": "W", "trigger": "before_breach", "actions": [{"kind": "tag", "tag": "x"}]}
        )
        assert fields["lead_seconds"] == vocab.DEFAULT_LEAD_SECONDS

    def test_an_unknown_trigger_is_refused(self):
        with pytest.raises(rules.EscalationRefusal) as caught:
            rules.require_trigger_kind("when the sun rises")
        assert caught.value.code == "unknown_trigger_kind"

    def test_an_unknown_target_is_refused(self):
        with pytest.raises(rules.EscalationRefusal) as caught:
            rules.require_metric("vibes")
        assert caught.value.code == "unknown_metric"

    def test_a_lead_time_below_the_floor_is_refused(self):
        with pytest.raises(rules.EscalationRefusal) as caught:
            rules.require_lead_seconds(5)
        assert caught.value.code == "lead_out_of_range"

    def test_a_lead_time_that_is_not_a_number_is_refused(self):
        with pytest.raises(rules.EscalationRefusal) as caught:
            rules.require_lead_seconds("ten minutes")
        assert caught.value.code == "lead_not_a_number"

    def test_a_lead_time_equal_to_the_target_is_refused(self):
        with pytest.raises(rules.EscalationRefusal) as caught:
            rules.require_lead_shorter(900, 900, "frt")
        assert caught.value.code == "lead_not_shorter_than_target"

    def test_a_lead_time_shorter_than_the_target_passes(self):
        assert rules.require_lead_shorter(600, 900, "frt") == 600

    def test_triggers_normalise_case_and_separators(self):
        assert rules.require_trigger_kind("  BEFORE-BREACH ") == "before_breach"
        assert rules.require_trigger_kind("On Breach") == "on_breach"

    def test_require_state_translates_wf107_refusals(self):
        with pytest.raises(rules.EscalationRefusal) as caught:
            rules.require_state("napping")
        assert caught.value.code == "unknown_conversation_state"


class TestActions:
    def test_an_assign_action_needs_somebody_to_assign(self):
        with pytest.raises(rules.EscalationRefusal) as caught:
            rules.normalise_action({"kind": "assign"})
        assert caught.value.code == "action_needs_a_target"

    def test_a_notify_action_normalises_its_channel(self):
        action = rules.normalise_action(
            {"kind": "notify", "via": "Slack", "channel": "#sales", "message": "look"}
        )
        assert action["via"] == "slack"
        assert action["channel"] == "#sales"

    def test_a_note_action_needs_its_text(self):
        with pytest.raises(rules.EscalationRefusal):
            rules.normalise_action({"kind": "note", "body": "  "})

    def test_an_unknown_action_kind_is_refused(self):
        with pytest.raises(rules.EscalationRefusal) as caught:
            rules.normalise_action({"kind": "teleport"})
        assert caught.value.code == "unknown_action_kind"

    def test_an_alert_action_carries_its_connector(self):
        action = rules.normalise_action(
            {"kind": "alert", "connector_id": "conn_1", "url": "https://x", "method": "post"}
        )
        assert action["method"] == "POST"
        assert action["connector_id"] == "conn_1"

    def test_an_action_summary_reads_as_a_sentence(self):
        action = rules.normalise_action({"kind": "tag", "tag": "SLA Breach"})
        assert "sla breach" in rules.action_summary(action)


# --------------------------------------------------------------------------- #
# The watched target and the trigger instant
# --------------------------------------------------------------------------- #


class TestWatchedTarget:
    def test_the_watched_entry_is_the_metric_asked_for(self):
        entry = rules.watched_entry(view(), "frt")
        assert entry is not None and entry["metric"] == "frt"

    def test_a_missing_timer_has_no_entry(self):
        assert rules.watched_entry(None, "frt") is None

    def test_the_trigger_instant_is_the_deadline_minus_the_lead(self):
        entry = rules.watched_entry(
            view(entry={"deadline": stamp(NOW + timedelta(minutes=5))}), "frt"
        )
        point = rules.trigger_instant(entry, 600)
        assert point == NOW - timedelta(minutes=5)

    def test_a_paused_timer_has_no_trigger_instant(self):
        entry = rules.watched_entry(view(entry={"deadline": None}), "frt")
        assert rules.trigger_instant(entry, 600) is None

    def test_a_teammate_reply_inside_the_window_is_found(self):
        parts = [{"author_kind": "teammate", "at": stamp(NOW - timedelta(minutes=5))}]
        found = rules.teammate_reply_between(parts, NOW - timedelta(hours=1), NOW)
        assert found == NOW - timedelta(minutes=5)

    def test_a_teammate_reply_before_the_anchor_is_ignored(self):
        parts = [{"author_kind": "teammate", "at": stamp(NOW - timedelta(hours=2))}]
        assert rules.teammate_reply_between(parts, NOW - timedelta(hours=1), NOW) is None

    def test_a_customer_reply_does_not_cancel(self):
        parts = [{"author_kind": "customer", "at": stamp(NOW - timedelta(minutes=5))}]
        assert rules.teammate_reply_between(parts, NOW - timedelta(hours=1), NOW) is None


# --------------------------------------------------------------------------- #
# The decision
# --------------------------------------------------------------------------- #


class TestBeforeBreachDecision:
    def test_it_fires_inside_the_lead_window(self):
        decision = rules.decide(workflow(), view(entry={"remaining_seconds": 300.0}), OPEN, [], NOW)
        assert decision["fire"] is True
        assert decision["level"] == vocab.LEVEL_PRE_WARNING
        assert decision["trigger"] == "before_breach"

    def test_it_does_not_fire_before_the_window(self):
        decision = rules.decide(workflow(), view(entry={"remaining_seconds": 900.0}), OPEN, [], NOW)
        assert decision["fire"] is False
        assert decision["reason"] == vocab.SKIP_LEAD_WINDOW_NOT_REACHED

    def test_a_missed_window_is_recorded_rather_than_fired_late(self):
        decision = rules.decide(
            workflow(),
            view(entry={"state": "breached", "remaining_seconds": 0.0}),
            OPEN,
            [],
            NOW,
        )
        assert decision["fire"] is False
        assert decision["reason"] == vocab.SKIP_HEAD_START_MISSED

    def test_a_lead_not_shorter_than_the_target_is_skipped(self):
        wide = workflow(lead_seconds=1200)
        decision = rules.decide(wide, view(), OPEN, [], NOW)
        assert decision["fire"] is False
        assert decision["reason"] == vocab.SKIP_LEAD_NOT_SHORTER

    def test_a_met_target_never_fires(self):
        decision = rules.decide(
            workflow(), view(entry={"state": "met", "completion": stamp(NOW)}), OPEN, [], NOW
        )
        assert decision["fire"] is False
        assert decision["reason"] == vocab.SKIP_METRIC_ALREADY_MET

    def test_an_already_fired_target_does_not_fire_again(self):
        decision = rules.decide(workflow(), view(), OPEN, [], NOW, already_fired=True)
        assert decision["fire"] is False
        assert decision["reason"] == vocab.SKIP_ALREADY_FIRED

    def test_a_not_applicable_target_is_skipped(self):
        decision = rules.decide(workflow(), view(entry={"state": "not_applicable"}), OPEN, [], NOW)
        assert decision["reason"] == vocab.SKIP_METRIC_NOT_APPLICABLE

    def test_a_conversation_with_no_timer_is_skipped(self):
        decision = rules.decide(workflow(), None, OPEN, [], NOW)
        assert decision["reason"] == vocab.SKIP_NO_ACTIVE_SLA

    def test_the_decision_names_its_trigger_point(self):
        decision = rules.decide(
            workflow(),
            view(entry={"deadline": stamp(NOW + timedelta(minutes=5))}),
            OPEN,
            [],
            NOW,
        )
        assert decision["fire_at"] == stamp(NOW - timedelta(minutes=5))


class TestOnBreachDecision:
    def test_it_fires_the_moment_the_target_is_missed(self):
        decision = rules.decide(
            workflow(trigger="on_breach", lead_seconds=None),
            view(entry={"state": "breached", "remaining_seconds": 0.0}),
            OPEN,
            [],
            NOW,
        )
        assert decision["fire"] is True
        assert decision["level"] == vocab.LEVEL_BREACH

    def test_it_does_not_fire_before_the_breach(self):
        decision = rules.decide(
            workflow(trigger="on_breach", lead_seconds=None), view(), OPEN, [], NOW
        )
        assert decision["fire"] is False
        assert decision["reason"] == vocab.SKIP_METRIC_NOT_BREACHED

    def test_an_on_breach_workflow_needs_no_lead_time(self):
        assert workflow(trigger="on_breach", lead_seconds=None)["lead_seconds"] is None


class TestCancellations:
    def _decide(self, conversation, parts, **entry_over):
        entry = {"remaining_seconds": 300.0, "anchor": stamp(NOW - timedelta(hours=1))}
        entry.update(entry_over)
        return rules.decide(workflow(), view(entry=entry), conversation, parts, NOW)

    def test_a_teammate_reply_before_the_point_cancels(self):
        parts = [{"author_kind": "teammate", "at": stamp(NOW - timedelta(minutes=20))}]
        decision = self._decide(OPEN, parts)
        assert decision["fire"] is False
        assert decision["cancel"] == vocab.CANCEL_TEAMMATE_REPLY

    def test_a_snoozed_conversation_cancels(self):
        decision = self._decide({"state": "snoozed", "tags": []}, [])
        assert decision["cancel"] == vocab.CANCEL_SNOOZED

    def test_a_closed_conversation_cancels(self):
        decision = self._decide({"state": "closed", "tags": []}, [])
        assert decision["cancel"] == vocab.CANCEL_CLOSED

    def test_the_three_cancellations_are_checked_on_the_on_breach_trigger_too(self):
        decision = rules.decide(
            workflow(trigger="on_breach", lead_seconds=None),
            view(entry={"state": "breached", "remaining_seconds": 0.0}),
            {"state": "snoozed", "tags": []},
            [],
            NOW,
        )
        assert decision["fire"] is False
        assert decision["cancel"] == vocab.CANCEL_SNOOZED

    def test_a_customer_reply_does_not_cancel(self):
        parts = [{"author_kind": "customer", "at": stamp(NOW - timedelta(minutes=20))}]
        assert self._decide(OPEN, parts)["fire"] is True


# --------------------------------------------------------------------------- #
# The Data Connector execution result
# --------------------------------------------------------------------------- #


class TestAlertOutcome:
    def test_a_configured_alert_succeeds(self):
        outcome = rules.alert_outcome({"connector_id": "c", "url": "https://x", "method": "POST"})
        assert outcome["success"] is True
        assert outcome["http_status"] == 200
        assert outcome["error_type"] == vocab.ERROR_NONE

    def test_an_alert_with_no_url_is_the_quoted_configuration_error(self):
        outcome = rules.alert_outcome({"connector_id": "c", "method": "POST"})
        assert outcome["success"] is False
        assert outcome["http_status"] == 400
        assert outcome["error_type"] == "request_configuration_error"

    def test_the_execution_names_its_source_type(self):
        outcome = rules.alert_outcome({"connector_id": "c", "url": "https://x"})
        assert outcome["source_type"] == "workflow"
        assert outcome["sent_by_this_product"] is False

    def test_a_forced_status_is_recorded_as_given(self):
        outcome = rules.alert_outcome(
            {"connector_id": "c", "url": "https://x", "simulate_http_status": 503}
        )
        assert outcome["http_status"] == 503
        assert outcome["success"] is False
        assert outcome["error_type"] == vocab.ERROR_HTTP

    def test_the_execution_time_is_never_a_bare_zero(self):
        outcome = rules.alert_outcome({"connector_id": "c", "url": "https://x"})
        assert outcome["execution_time_ms"] > 0


# --------------------------------------------------------------------------- #
# Ranking and small helpers
# --------------------------------------------------------------------------- #


class TestRanking:
    def test_a_breach_ranks_above_a_pre_warning(self):
        assert rules.escalation_rank(vocab.LEVEL_BREACH) > rules.escalation_rank(
            vocab.LEVEL_PRE_WARNING
        )

    def test_an_un_escalated_conversation_ranks_zero(self):
        assert rules.escalation_rank(None) == 0
        assert rules.priority_for(None) is False

    def test_both_levels_are_priority(self):
        assert rules.priority_for(vocab.LEVEL_PRE_WARNING) is True
        assert rules.priority_for(vocab.LEVEL_BREACH) is True

    def test_merge_tag_adds_once(self):
        assert rules.merge_tag(["a"], "B") == ["a", "b"]
        assert rules.merge_tag(["a", "b"], "B") == ["a", "b"]


# --------------------------------------------------------------------------- #
# The inferences register
# --------------------------------------------------------------------------- #


class TestInferences:
    def test_every_decision_states_its_consequence_and_evidence(self):
        for entry in inferences.DECISIONS:
            assert entry["consequence"], entry["id"]
            assert entry["evidence"], entry["id"]

    def test_the_register_lists_its_unsourced_entries(self):
        report = inferences.inferences_report()
        assert report["count"] == len(inferences.DECISIONS)
        assert "lead-time-enforcement" in report["unsourced"]

    def test_the_jev_map_is_a_mapping_and_its_status_is_explained(self):
        assert isinstance(inferences.JEV_DESIGN_AUDITS, dict)
        assert inferences.JEV_STATUS

    def test_a_decision_can_be_read_by_id(self):
        assert inferences.decision_by_id("cancellation-model")["id"] == "cancellation-model"
        assert inferences.decision_by_id("nope") is None
