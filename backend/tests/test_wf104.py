"""WF-104's domain rules, as pure functions over values.

Every test here calls something in ``dsr.nurture_series.rules`` or ``vocabulary`` with a mapping
and a clock, and checks the answer. No test in this file touches HTTP, and none opens a database.

**No test in this file carries a fixed date.** Every instant derives from ``CLOCK``, and every
duration is an offset from it. A payload carrying a literal "today" is a test that passes on the
day it was written and fails after that date moves on; one such payload blocked four pull requests
before it was repaired. The clock is a constant the test controls, which is the freeze the brief
asks for, not a reading of the wall clock.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from dsr.nurture_series import inferences, rules, vocabulary as vocab
from dsr.nurture_series.errors import SeriesRefused

#: A frozen clock. Every instant below is an offset from it, so the file has no readable "today".
CLOCK = datetime(2026, 3, 2, 9, 0, tzinfo=timezone.utc)


def contact(**overrides):
    """A normalised contact, with attributes and company the filter evaluator reads."""

    base = {
        "contact_id": "c1",
        "attributes": {
            "email": "a.buyer@northwind.example",
            "plan": "enterprise",
            "lead_score": 82,
            "tags": ["vip", "pilot"],
            "event_counts": {"click": 3, "open": 9},
        },
        "company": {"name": "Northwind Traders", "industry": "logistics", "employees": 400},
        "online": True,
        "channels": ["in_app", "email"],
    }
    base.update(overrides)
    return rules.normalise_contact(base)


# --------------------------------------------------------------------------- #
# The four block kinds, and no others
# --------------------------------------------------------------------------- #


class TestBlockKinds:
    def test_the_four_kinds_are_the_researched_four(self):
        assert vocab.BLOCK_KINDS == ("rule", "content", "wait", "tag")

    def test_an_unknown_kind_is_refused_by_name(self):
        with pytest.raises(SeriesRefused) as caught:
            vocab.require_block_kind("webhook")
        assert caught.value.code == "unknown_block_kind"

    def test_kinds_normalise(self):
        assert vocab.require_block_kind(" Rule ") == "rule"

    def test_the_four_channels(self):
        assert vocab.CHANNELS == ("in_app", "email", "mobile_push", "sms")

    def test_the_seven_content_types(self):
        assert vocab.CONTENT_TYPES == (
            "post",
            "chat",
            "email",
            "tour",
            "push",
            "carousel",
            "banner",
        )

    def test_an_unknown_channel_is_refused(self):
        with pytest.raises(SeriesRefused) as caught:
            vocab.require_channel("carrier_pigeon")
        assert caught.value.code == "unknown_channel"

    def test_an_unknown_content_type_is_refused(self):
        with pytest.raises(SeriesRefused) as caught:
            vocab.require_content_type("fax")
        assert caught.value.code == "unknown_content_type"


# --------------------------------------------------------------------------- #
# The rule-block filter vocabulary, shared with WF-105
# --------------------------------------------------------------------------- #


class TestFilterVocabulary:
    def test_contact_and_company_fields_are_both_published(self):
        paths = vocab.filter_field_paths()
        assert "plan" in paths
        assert "event_counts.click" in paths
        assert "company.employees" in paths
        assert "company.name" in paths

    def test_company_fields_carry_the_prefix(self):
        assert all(
            row["path"].startswith("company.")
            for row in vocab.filter_fields()
            if row["scope"] == "company"
        )

    def test_an_unknown_filter_field_is_refused(self):
        with pytest.raises(SeriesRefused) as caught:
            vocab.require_filter_field("credit_card")
        assert caught.value.code == "unknown_filter_field"

    def test_the_nine_operators(self):
        assert vocab.FILTER_OPERATORS == (
            "eq",
            "neq",
            "gt",
            "gte",
            "lt",
            "lte",
            "contains",
            "in",
            "exists",
        )

    def test_an_unknown_operator_is_refused(self):
        with pytest.raises(SeriesRefused) as caught:
            vocab.require_filter_operator("matches_regex")
        assert caught.value.code == "unknown_filter_operator"

    def test_the_vocabulary_names_wf105_as_a_consumer(self):
        assert "WF-105" in vocab.published_vocabulary()["filter"]["shared_with"]


# --------------------------------------------------------------------------- #
# Filter evaluation
# --------------------------------------------------------------------------- #


class TestFilterEvaluation:
    def test_eq_matches_a_string(self):
        ok, _ = rules.matches_filter(
            contact(), {"field": "plan", "op": "eq", "value": "enterprise"}
        )
        assert ok

    def test_eq_is_numeric_aware(self):
        ok, _ = rules.matches_filter(contact(), {"field": "lead_score", "op": "eq", "value": 82.0})
        assert ok

    def test_neq(self):
        ok, _ = rules.matches_filter(contact(), {"field": "plan", "op": "neq", "value": "starter"})
        assert ok

    @pytest.mark.parametrize(
        "op,value,expected",
        [("gt", 50, True), ("gte", 82, True), ("lt", 82, False), ("lte", 82, True)],
    )
    def test_comparisons(self, op, value, expected):
        ok, _ = rules.matches_filter(contact(), {"field": "lead_score", "op": op, "value": value})
        assert ok is expected

    def test_a_comparison_on_a_non_number_does_not_match(self):
        ok, reason = rules.matches_filter(contact(), {"field": "plan", "op": "gt", "value": 5})
        assert ok is False
        assert "not comparable" in reason

    def test_contains_reads_a_list(self):
        ok, _ = rules.matches_filter(contact(), {"field": "tags", "op": "contains", "value": "vip"})
        assert ok

    def test_in_reads_a_list_of_options(self):
        ok, _ = rules.matches_filter(
            contact(), {"field": "plan", "op": "in", "value": ["starter", "enterprise"]}
        )
        assert ok

    def test_exists_true_and_false(self):
        present, _ = rules.matches_filter(
            contact(), {"field": "plan", "op": "exists", "value": True}
        )
        absent, _ = rules.matches_filter(
            contact(), {"field": "owner_id", "op": "exists", "value": True}
        )
        assert present is True
        assert absent is False

    def test_a_company_field_resolves_inside_company(self):
        ok, _ = rules.matches_filter(
            contact(), {"field": "company.employees", "op": "gt", "value": 100}
        )
        assert ok

    def test_an_event_count_field_resolves(self):
        ok, _ = rules.matches_filter(
            contact(), {"field": "event_counts.click", "op": "gte", "value": 1}
        )
        assert ok

    def test_filter_match_all_and_any(self):
        filters = [
            {"field": "plan", "op": "eq", "value": "enterprise"},
            {"field": "lead_score", "op": "gt", "value": 200},
        ]
        all_passed, matched, total = rules.filter_match(contact(), filters, "all")
        any_passed, _, _ = rules.filter_match(contact(), filters, "any")
        assert all_passed is False
        assert matched == 1
        assert total == 2
        assert any_passed is True

    def test_an_empty_filter_list_gates_nobody(self):
        passed, matched, total = rules.filter_match(contact(), [], "all")
        assert (passed, matched, total) == (True, 0, 0)

    def test_the_reason_names_the_field(self):
        ok, reason = rules.matches_filter(
            contact(), {"field": "plan", "op": "eq", "value": "starter"}
        )
        assert ok is False
        assert "plan" in reason


# --------------------------------------------------------------------------- #
# Audience normalisation
# --------------------------------------------------------------------------- #


class TestAudience:
    def test_a_contact_without_an_id_is_refused(self):
        with pytest.raises(SeriesRefused) as caught:
            rules.normalise_contacts([{"plan": "enterprise"}])
        assert caught.value.code == "bad_audience"

    def test_a_non_list_audience_is_refused(self):
        with pytest.raises(SeriesRefused) as caught:
            rules.normalise_contacts({"contact_id": "c1"})
        assert caught.value.code == "bad_audience"

    def test_an_empty_audience_is_no_contacts(self):
        assert rules.normalise_contacts(None) == []

    def test_channels_default_to_every_channel(self):
        normalised = rules.normalise_contact({"contact_id": "c1"})
        assert normalised["channels"] == list(vocab.CHANNELS)

    def test_a_single_channel_string_becomes_a_list(self):
        normalised = rules.normalise_contact({"contact_id": "c1", "channels": "email"})
        assert normalised["channels"] == ["email"]


# --------------------------------------------------------------------------- #
# Block validation
# --------------------------------------------------------------------------- #


class TestValidateBlocks:
    def _series(self, blocks, **extra):
        payload = {"name": "Nurture", "blocks": blocks}
        payload.update(extra)
        return rules.validate_series(payload)

    def test_a_series_needs_a_name(self):
        with pytest.raises(SeriesRefused) as caught:
            rules.validate_series({"blocks": [{"kind": "tag", "tag": "x"}]})
        assert caught.value.code == "series_name_required"

    def test_a_series_needs_at_least_one_block(self):
        with pytest.raises(SeriesRefused) as caught:
            rules.validate_series({"name": "N", "blocks": []})
        assert caught.value.code == "no_blocks"

    def test_the_first_rule_block_is_the_entry_rule(self):
        validated = self._series(
            [
                {"kind": "rule", "filters": [{"field": "plan", "op": "eq", "value": "enterprise"}]},
                {"kind": "tag", "tag": "nurtured"},
            ]
        )
        assert validated["entry_rule"] is not None
        assert validated["start_cursor"] == 1

    def test_a_series_with_no_entry_rule_starts_at_zero(self):
        validated = self._series([{"kind": "tag", "tag": "nurtured"}])
        assert validated["entry_rule"] is None
        assert validated["start_cursor"] == 0

    def test_a_branch_rule_needs_a_condition_or_a_filter(self):
        with pytest.raises(SeriesRefused):
            self._series(
                [
                    {"kind": "tag", "tag": "x"},
                    {"kind": "rule"},
                ]
            )

    def test_a_branch_rule_reads_a_published_condition(self):
        validated = self._series(
            [
                {"kind": "tag", "tag": "x"},
                {"kind": "rule", "condition": "clicked_link", "wait_mode": "for"},
            ]
        )
        branch = validated["blocks"][1]
        assert branch["condition"] == "clicked_link"
        assert branch["wait_mode"] == "for"
        assert branch["match_within_seconds"] == vocab.DEFAULT_MATCH_WITHIN_SECONDS

    def test_a_rule_for_mode_takes_a_custom_window(self):
        validated = self._series(
            [
                {"kind": "tag", "tag": "x"},
                {
                    "kind": "rule",
                    "condition": "replied",
                    "wait_mode": "for",
                    "match_within_seconds": 3600,
                },
            ]
        )
        assert validated["blocks"][1]["match_within_seconds"] == 3600

    def test_a_rule_once_mode_has_no_window(self):
        validated = self._series(
            [
                {"kind": "tag", "tag": "x"},
                {"kind": "rule", "condition": "replied", "wait_mode": "once"},
            ]
        )
        assert validated["blocks"][1]["match_within_seconds"] is None

    def test_an_unknown_rule_match_mode_is_refused(self):
        with pytest.raises(SeriesRefused) as caught:
            self._series(
                [
                    {"kind": "tag", "tag": "x"},
                    {"kind": "rule", "condition": "replied", "wait_mode": "forever"},
                ]
            )
        assert caught.value.code == "unknown_rule_match_mode"

    def test_a_content_block_with_a_fallback_needs_a_window(self):
        with pytest.raises(SeriesRefused) as caught:
            self._series([{"kind": "content", "fallback_enabled": True, "send_window_seconds": 0}])
        assert caught.value.code == "send_window_required"

    def test_a_content_block_defaults_its_window(self):
        validated = self._series([{"kind": "content", "fallback_channel": "email"}])
        block = validated["blocks"][0]
        assert block["fallback_enabled"] is True
        assert block["send_window_seconds"] == vocab.DEFAULT_SEND_WINDOW_SECONDS

    def test_a_content_block_without_a_fallback_has_no_window(self):
        validated = self._series([{"kind": "content"}])
        assert validated["blocks"][0]["fallback_enabled"] is False
        assert validated["blocks"][0]["send_window_seconds"] is None

    def test_a_wait_block_needs_a_positive_duration(self):
        with pytest.raises(SeriesRefused) as caught:
            self._series([{"kind": "wait"}])
        assert caught.value.code == "duration_out_of_range"

    def test_a_zero_duration_is_refused(self):
        with pytest.raises(SeriesRefused) as caught:
            self._series([{"kind": "wait", "duration_seconds": 0}])
        assert caught.value.code == "duration_out_of_range"

    def test_a_tag_block_needs_a_tag(self):
        with pytest.raises(SeriesRefused) as caught:
            self._series([{"kind": "tag", "tag": "  "}])
        assert caught.value.code == "bad_block"

    def test_every_block_kind_survives_a_round_trip(self):
        validated = self._series(
            [
                {"kind": "rule", "filters": [{"field": "plan", "op": "eq", "value": "enterprise"}]},
                {"kind": "content", "channel": "email", "content_type": "email", "body": "hi"},
                {"kind": "wait", "duration_seconds": 86400},
                {"kind": "tag", "tag": "nurtured"},
            ]
        )
        assert [block["kind"] for block in validated["blocks"]] == [
            "rule",
            "content",
            "wait",
            "tag",
        ]


# --------------------------------------------------------------------------- #
# The send window and the offline branch
# --------------------------------------------------------------------------- #


class TestSendWindowAndFallback:
    def test_the_send_window_is_a_retry_window(self):
        assert vocab.SEND_WINDOW_IS_RETRY is True

    def test_the_offline_branch_is_a_timeout(self):
        assert vocab.OFFLINE_BRANCH_IS_TIMEOUT is True

    def test_the_default_window_is_the_researched_thirty_days(self):
        assert vocab.DEFAULT_SEND_WINDOW_SECONDS == 30 * 24 * 60 * 60

    def test_an_online_buyer_on_the_channel_delivers(self):
        decision = rules.decide_channel({"channel": "email"}, contact())
        assert decision["can_deliver"] is True

    def test_an_offline_buyer_cannot_deliver(self):
        decision = rules.decide_channel({"channel": "in_app"}, contact(online=False))
        assert decision["can_deliver"] is False
        assert decision["reason"] == "buyer_offline"

    def test_a_channel_the_buyer_lacks_cannot_deliver(self):
        decision = rules.decide_channel({"channel": "sms"}, contact(channels=["email"]))
        assert decision["can_deliver"] is False
        assert decision["reason"] == "channel_unavailable"

    def test_the_timeout_reason_fires_only_once_the_window_elapses(self):
        assert rules.fallback_reason(False, 30 * 24 * 60 * 60, 30 * 24 * 60 * 60) == (
            vocab.OFFLINE_FALLBACK_REASON
        )
        assert rules.fallback_reason(False, 60, 30 * 24 * 60 * 60) == "buyer_offline_retrying"

    def test_an_online_buyer_falls_back_for_the_channel_not_the_timeout(self):
        assert rules.fallback_reason(True, 10**9, 30 * 24 * 60 * 60) == "channel_unavailable"


# --------------------------------------------------------------------------- #
# The two rule wait modes
# --------------------------------------------------------------------------- #


class TestRuleSatisfied:
    def test_a_condition_reads_its_event(self):
        ok, _ = rules.rule_satisfied(contact(), ["click"], {"condition": "clicked_link"})
        assert ok is True

    def test_a_missing_event_does_not_satisfy(self):
        ok, _ = rules.rule_satisfied(contact(), [], {"condition": "clicked_link"})
        assert ok is False

    def test_filters_and_the_condition_both_have_to_hold(self):
        block = {
            "condition": "clicked_link",
            "match": "all",
            "filters": [{"field": "plan", "op": "eq", "value": "starter"}],
        }
        ok, reason = rules.rule_satisfied(contact(), ["click"], block)
        assert ok is False
        assert "filters not matched" in reason

    def test_a_rule_with_no_condition_and_no_filters_holds(self):
        ok, _ = rules.rule_satisfied(contact(), [], {})
        assert ok is True

    def test_every_condition_maps_to_a_published_event(self):
        for condition, event in vocab.CONDITION_EVENTS.items():
            assert condition in vocab.RULE_CONDITIONS
            assert event in vocab.ENGAGEMENT_EVENTS


# --------------------------------------------------------------------------- #
# The engagement event vocabulary
# --------------------------------------------------------------------------- #


class TestEngagementVocabulary:
    def test_the_content_stat_events_are_canonical_for_the_lifecycle(self):
        assert vocab.CONTENT_STAT_EVENTS == (
            "receipt",
            "goal_success",
            "series_completion",
            "series_disengagement",
            "series_exit",
        )

    def test_the_data_flow_adds_the_three_interaction_events(self):
        assert vocab.DATA_FLOW_EVENTS == ("open", "click", "reply")
        assert set(vocab.DATA_FLOW_EVENTS) <= set(vocab.ENGAGEMENT_EVENTS)

    def test_the_canonical_list_is_recorded_not_left_to_a_reader(self):
        assert "content_stat.series" in vocab.EVENT_CANONICAL_SOURCE

    def test_an_unknown_event_is_refused(self):
        with pytest.raises(SeriesRefused) as caught:
            vocab.require_engagement_event("unsubscribed")
        assert caught.value.code == "unknown_engagement_event"

    def test_every_published_event_has_a_label(self):
        assert set(vocab.EVENT_LABELS) == set(vocab.ENGAGEMENT_EVENTS)


# --------------------------------------------------------------------------- #
# The poll
# --------------------------------------------------------------------------- #


class TestPoll:
    def test_the_poll_interval_is_thirty_minutes(self):
        assert vocab.POLL_INTERVAL_SECONDS == 30 * 60

    def test_the_poll_quote_is_the_researched_sentence(self):
        assert "every 30 minutes" in vocab.POLL_QUOTE


# --------------------------------------------------------------------------- #
# Export helpers
# --------------------------------------------------------------------------- #


class TestExportHelpers:
    def _series(self):
        return rules.validate_series(
            {
                "name": "Nurture",
                "blocks": [
                    {
                        "kind": "rule",
                        "filters": [{"field": "plan", "op": "eq", "value": "enterprise"}],
                    },
                    {"kind": "content", "channel": "in_app"},
                    {"kind": "rule", "condition": "clicked_link", "wait_mode": "for"},
                ],
            }
        )

    def test_embedded_rules_flattens_every_rule_block(self):
        embedded = rules.embedded_rules(self._series())
        assert len(embedded) == 2
        assert embedded[0]["role"] == "entry"
        assert embedded[1]["condition"] == "clicked_link"

    def test_attribute_paths_are_the_fields_the_series_reads(self):
        assert rules.attribute_paths_used(self._series()) == ["plan"]

    def test_the_snapshot_names_the_state_and_the_blocks(self):
        snapshot = rules.describe_snapshot(self._series())
        assert snapshot["name"] == "Nurture"
        assert len(snapshot["blocks"]) == 3

    def test_the_export_fields_are_the_researched_four(self):
        assert vocab.EXPORT_FIELDS == ("targeting", "snapshot", "attributes", "embedded_rules")


# --------------------------------------------------------------------------- #
# Instants
# --------------------------------------------------------------------------- #


class TestInstants:
    def test_an_instant_round_trips(self):
        assert rules.to_iso(CLOCK) == "2026-03-02T09:00:00Z"

    def test_a_naive_instant_is_not_parsed(self):
        assert rules.parse_instant("2026-03-02T09:00:00") is None

    def test_junk_is_not_parsed(self):
        assert rules.parse_instant("soon") is None

    def test_a_z_instant_parses_to_utc(self):
        assert rules.parse_instant("2026-03-02T09:00:00Z") == CLOCK

    def test_a_duration_offset_is_an_offset(self):
        assert rules.parse_instant(rules.to_iso(CLOCK + timedelta(days=3))) == CLOCK + timedelta(
            days=3
        )


# --------------------------------------------------------------------------- #
# The recorded decisions
# --------------------------------------------------------------------------- #


class TestInferences:
    def test_the_report_counts_every_decision(self):
        report = inferences.inferences_report()
        assert report["ticket"] == "WF-104"
        assert report["count"] == len(inferences.DECISIONS)

    def test_every_decision_names_a_question_and_a_rejected_alternative(self):
        for decision in inferences.DECISIONS:
            assert decision["question"]
            assert decision["chosen"]
            assert decision["rejected"]
            assert decision["consequence"]

    def test_the_audience_decision_carries_its_jev_audit(self):
        report = inferences.inferences_report()
        assert report["jev_audits"]["audience-mechanism"] == "jev-20261013T150622-7908-82240"

    def test_the_audience_mechanism_matches_the_vocabulary(self):
        assert vocab.AUDIENCE_MECHANISM == "caller_supplied_audience"

    def test_one_decision_can_be_read_by_id(self):
        entry = inferences.decision_by_id("poll-interval")
        assert entry is not None
        assert "30-minute poll" in entry["chosen"]

    def test_an_unknown_decision_is_none(self):
        assert inferences.decision_by_id("no-such-decision") is None
