"""WF-107's domain rules, as pure functions over values.

Every test here calls something in ``dsr.conversation_chase.rules`` or
``vocabulary`` with two integers, a list of dicts and a clock, and checks the answer.
No test in this file touches HTTP, and none opens a database.

**No test in this file carries a fixed date.**

Every instant is derived from ``CLOCK`` or from a weekday computed off it, and every
duration is an offset from one of those. That is deliberate: a payload carrying a literal
timestamp is a test that passes on the day it was written and fails after that date
moves on, and one such payload blocked four pull requests before it was repaired. The
office-hours cases need a weekday and a weekend, so ``monday_morning`` and
``saturday_morning`` walk back from ``CLOCK`` by ``weekday()`` rather than naming a date.

The clock is a parameter rather than a global so that a test which needs "now" to be a
particular instant says so at the call site and cannot leak that choice into the next.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from dsr.conversation_chase import rules, vocabulary as vocab
from dsr.conversation_chase.rules import ChaseRefusal

# --------------------------------------------------------------------------- #
# A clock every test controls
# --------------------------------------------------------------------------- #


def _monday_at(hour: int, minute: int) -> datetime:
    """A Monday at a given wall-clock time, derived from today rather than written down.

    Walking back from ``CLOCK`` by ``weekday()`` means the working-day office-hours cases
    are working-day cases whenever the suite runs, which is the whole reason the seed and
    these tests derive their instants.
    """

    today = datetime(2026, 3, 2, tzinfo=timezone.utc)
    monday = today - timedelta(days=today.weekday())
    return monday.replace(hour=hour, minute=minute)


#: A fixed anchor for the tests that do not care what day of the week it is.
CLOCK = _monday_at(9, 0)

#: Ten minutes, the number the research names as the example inactivity timer.
TEN_MINUTES = 10 * 60
#: Fifteen minutes, the worked example in the office-hours quote.
QUARTER_HOUR = 15 * 60
SECOND = 1


def part(author_kind: str, at: datetime, body: str = "hello") -> dict[str, object]:
    """One conversation part, as the rules receive it."""

    return {"author_kind": author_kind, "at": rules.stamp(at), "body": body}


def customer_at(minutes: float, body: str = "hello") -> dict[str, object]:
    return part(vocab.AUTHOR_CUSTOMER, CLOCK - timedelta(minutes=minutes), body)


def teammate_at(minutes: float, body: str = "on it") -> dict[str, object]:
    return part(vocab.AUTHOR_TEAMMATE, CLOCK - timedelta(minutes=minutes), body)


def system_at(minutes: float, body: str = "just checking") -> dict[str, object]:
    return part(vocab.AUTHOR_SYSTEM, CLOCK - timedelta(minutes=minutes), body)


# --------------------------------------------------------------------------- #
# The duration bounds, both ends
# --------------------------------------------------------------------------- #


class TestDurationBounds:
    """The sentence under test: "longer than 30 seconds and shorter than 14 days."

    Both bounds are exclusive, so exactly 30 and exactly 14 days are both refused. That is
    the sentence read literally: "longer than" and "shorter than" are both strict.
    """

    def test_quote_is_published(self):
        assert vocab.DURATION_BOUNDS == (
            "The duration must be longer than 30 seconds and shorter than 14 days."
        )

    def test_the_named_minimum_is_30(self):
        assert vocab.MIN_DURATION_SECONDS == 30

    def test_the_named_maximum_is_fourteen_days(self):
        assert vocab.MAX_DURATION_SECONDS == 14 * 24 * 60 * 60

    # -- the lower bound ----------------------------------------------------- #

    def test_exactly_thirty_seconds_is_refused(self):
        with pytest.raises(ChaseRefusal) as caught:
            rules.require_duration(30)
        assert caught.value.code == "duration_out_of_range"
        assert caught.value.status == 422

    @pytest.mark.parametrize("value", [0, 1, 29, 30])
    def test_everything_at_or_below_thirty_is_refused(self, value):
        with pytest.raises(ChaseRefusal) as caught:
            rules.require_duration(value)
        assert caught.value.code == "duration_out_of_range"

    def test_one_second_above_the_bound_is_accepted(self):
        assert rules.require_duration(31) == 31

    # -- the upper bound ----------------------------------------------------- #

    def test_exactly_fourteen_days_is_refused(self):
        with pytest.raises(ChaseRefusal) as caught:
            rules.require_duration(14 * 24 * 60 * 60)
        assert caught.value.code == "duration_out_of_range"

    @pytest.mark.parametrize("value", [14 * 24 * 60 * 60, 15 * 24 * 60 * 60, 30 * 24 * 60 * 60])
    def test_fourteen_days_and_beyond_are_refused(self, value):
        with pytest.raises(ChaseRefusal) as caught:
            rules.require_duration(value)
        assert caught.value.code == "duration_out_of_range"

    def test_one_second_below_the_bound_is_accepted(self):
        assert rules.require_duration(14 * 24 * 60 * 60 - 1) == 14 * 24 * 60 * 60 - 1

    # -- the inside ---------------------------------------------------------- #

    @pytest.mark.parametrize(
        "value",
        [31, TEN_MINUTES, 60 * 60, 24 * 60 * 60, 13 * 24 * 60 * 60],
    )
    def test_durations_inside_the_bounds_are_accepted(self, value):
        assert rules.require_duration(value) == value

    def test_the_researched_example_of_ten_minutes_is_legal(self):
        """The shipped default of ten minutes is inside both bounds, so it must validate."""

        assert vocab.DEFAULT_TRIGGER_SECONDS == TEN_MINUTES
        assert rules.require_duration(vocab.DEFAULT_TRIGGER_SECONDS) == TEN_MINUTES

    # -- not a number at all ------------------------------------------------- #

    @pytest.mark.parametrize("value", ["600", None, [600], {}, True, False])
    def test_non_numbers_are_refused(self, value):
        with pytest.raises(ChaseRefusal) as caught:
            rules.require_duration(value)
        assert caught.value.code == "duration_not_a_number"

    def test_a_fractional_second_is_refused_rather_than_rounded(self):
        """Rounding would make 29.6 legal and 30.4 legal, disagreeing about the bound."""

        with pytest.raises(ChaseRefusal) as caught:
            rules.require_duration(30.4)
        assert caught.value.code == "duration_not_a_number"

    def test_a_float_whole_number_is_accepted(self):
        assert rules.require_duration(600.0) == 600

    def test_the_field_name_is_reported_for_the_page(self):
        with pytest.raises(ChaseRefusal) as caught:
            rules.require_duration(10, field="wait_seconds")
        assert caught.value.errors == {"wait_seconds": caught.value.detail}

    def test_a_step_duration_uses_the_same_bounds(self):
        """A Wait's own duration is a duration the builder accepts, so it is bound too."""

        assert rules.require_step_duration(TEN_MINUTES) == TEN_MINUTES
        with pytest.raises(ChaseRefusal) as caught:
            rules.require_step_duration(30)
        assert caught.value.code == "duration_out_of_range"

    def test_every_refusal_code_is_published(self):
        assert "duration_out_of_range" in vocab.ERROR_CODES
        assert "duration_not_a_number" in vocab.ERROR_CODES
        assert vocab.error_status("duration_out_of_range") == 422


# --------------------------------------------------------------------------- #
# Each trigger reads its own clock
# --------------------------------------------------------------------------- #


class TestTriggerAnchors:
    """The two triggers anchor differently, and the research says so in both places.

    The customer trigger is anchored to the last message of *any* kind, because the data
    flow is "Last-message timestamp on the Conversation object". The teammate trigger is
    anchored to the customer's *first* message, because "if the customer sends 3 messages
    in a row, the timer will be set against their first message, not last".
    """

    def test_the_quote_is_published(self):
        assert "timer will be set against their first message, not last" in (
            vocab.FIRST_MESSAGE_ANCHOR
        )

    def test_customer_idle_reads_the_last_activity(self):
        assert vocab.anchor_for(vocab.CUSTOMER_IDLE) == vocab.ANCHOR_LAST_ACTIVITY

    def test_teammate_idle_reads_the_first_customer_message(self):
        assert vocab.anchor_for(vocab.TEAMMATE_IDLE) == vocab.ANCHOR_FIRST_CUSTOMER_MESSAGE

    def test_the_two_triggers_use_different_anchors(self):
        assert vocab.anchor_for(vocab.CUSTOMER_IDLE) != vocab.anchor_for(vocab.TEAMMATE_IDLE)

    def test_the_mapping_has_exactly_the_two_triggers(self):
        assert set(vocab.TRIGGER_ANCHORS) == set(vocab.TRIGGER_KINDS)

    # -- three messages in a row, which is the researched case --------------- #

    def test_three_customer_messages_in_a_row(self):
        parts = [
            customer_at(40, "first"),
            customer_at(35, "second"),
            customer_at(30, "third"),
        ]
        assert len(parts) == 3

    def test_teammate_anchor_is_the_first_of_three(self):
        parts = [
            customer_at(40, "first"),
            customer_at(35, "second"),
            customer_at(30, "third"),
        ]
        anchor, kind = rules.anchor_instant(parts, vocab.TEAMMATE_IDLE)
        assert kind == vocab.ANCHOR_FIRST_CUSTOMER_MESSAGE
        assert anchor == rules.coerce_instant(parts[0]["at"])

    def test_customer_anchor_is_the_last_of_three(self):
        parts = [
            customer_at(40, "first"),
            customer_at(35, "second"),
            customer_at(30, "third"),
        ]
        anchor, kind = rules.anchor_instant(parts, vocab.CUSTOMER_IDLE)
        assert kind == vocab.ANCHOR_LAST_ACTIVITY
        assert anchor == rules.coerce_instant(parts[2]["at"])

    def test_a_burst_does_not_reset_the_teammate_clock(self):
        """The researched sentence, as arithmetic: the first message anchors, always."""

        parts = [customer_at(40), customer_at(35), customer_at(30)]
        first_anchor, _ = rules.anchor_instant(parts, vocab.TEAMMATE_IDLE)

        # A fourth message arrives. The teammate clock must not move.
        parts.append(customer_at(25))
        again, _ = rules.anchor_instant(parts, vocab.TEAMMATE_IDLE)
        assert again == first_anchor

    def test_the_customer_clock_does_move_with_a_burst(self):
        """The other half of the sentence: the two triggers do not share an anchor."""

        parts = [customer_at(40), customer_at(35)]
        before, _ = rules.anchor_instant(parts, vocab.CUSTOMER_IDLE)
        parts.append(customer_at(30))
        after, _ = rules.anchor_instant(parts, vocab.CUSTOMER_IDLE)
        assert after > before

    def test_a_teammate_reply_moves_the_customer_clock(self):
        """A rep answering means the buyer is no longer being left waiting."""

        parts = [customer_at(40)]
        before, _ = rules.anchor_instant(parts, vocab.CUSTOMER_IDLE)
        parts.append(teammate_at(38))
        after, _ = rules.anchor_instant(parts, vocab.CUSTOMER_IDLE)
        assert after == rules.coerce_instant(parts[-1]["at"])
        assert after > before

    def test_a_teammate_reply_does_not_move_the_teammate_clock(self):
        parts = [customer_at(40)]
        before, _ = rules.anchor_instant(parts, vocab.TEAMMATE_IDLE)
        parts.append(teammate_at(38))
        after, _ = rules.anchor_instant(parts, vocab.TEAMMATE_IDLE)
        assert after == before

    # -- no message of the kind the anchor needs ----------------------------- #

    def test_a_conversation_with_no_customer_message_has_no_teammate_anchor(self):
        anchor, kind = rules.anchor_instant([teammate_at(10)], vocab.TEAMMATE_IDLE)
        assert anchor is None
        assert kind == vocab.ANCHOR_FIRST_CUSTOMER_MESSAGE

    def test_an_empty_conversation_has_no_anchor_at_all(self):
        for kind in vocab.TRIGGER_KINDS:
            anchor, _ = rules.anchor_instant([], kind)
            assert anchor is None

    def test_an_unsorted_list_still_yields_first_and_last(self):
        """Order comes from the instants, not from the order the list arrived in."""

        parts = [customer_at(30), customer_at(40), customer_at(35)]
        first, _ = rules.anchor_instant(parts, vocab.TEAMMATE_IDLE)
        last, _ = rules.anchor_instant(parts, vocab.CUSTOMER_IDLE)
        assert first == rules.coerce_instant(rules.stamp(CLOCK - timedelta(minutes=40)))
        assert last == rules.coerce_instant(rules.stamp(CLOCK - timedelta(minutes=30)))

    def test_an_unknown_trigger_kind_is_refused(self):
        with pytest.raises(ChaseRefusal) as caught:
            rules.require_trigger_kind("sales_idle")
        assert caught.value.code == "unknown_trigger_kind"


# --------------------------------------------------------------------------- #
# The inactivity window
# --------------------------------------------------------------------------- #


class TestInactivity:
    """When has enough of the window passed for the trigger to be due."""

    def test_not_yet_due_reports_the_seconds_remaining(self):
        anchor = CLOCK - timedelta(minutes=5)
        result = rules.inactivity(anchor, CLOCK, TEN_MINUTES)
        assert result["due"] is False
        assert result["seconds_remaining"] == pytest.approx(300.0)
        assert result["seconds_overdue"] == 0.0

    def test_exactly_on_the_boundary_is_due(self):
        anchor = CLOCK - timedelta(seconds=TEN_MINUTES)
        result = rules.inactivity(anchor, CLOCK, TEN_MINUTES)
        assert result["due"] is True
        assert result["seconds_remaining"] == 0.0

    def test_one_second_short_is_not_due(self):
        anchor = CLOCK - timedelta(seconds=TEN_MINUTES - 1)
        assert rules.inactivity(anchor, CLOCK, TEN_MINUTES)["due"] is False

    def test_overdue_reports_how_far_past(self):
        anchor = CLOCK - timedelta(minutes=15)
        result = rules.inactivity(anchor, CLOCK, TEN_MINUTES)
        assert result["due"] is True
        assert result["seconds_overdue"] == pytest.approx(300.0)
        assert result["seconds_remaining"] == 0.0

    def test_no_anchor_is_never_due_and_reports_no_numbers(self):
        result = rules.inactivity(None, CLOCK, TEN_MINUTES)
        assert result["due"] is False
        assert result["seconds_remaining"] is None
        assert result["seconds_elapsed"] is None

    def test_a_future_anchor_reads_as_zero_elapsed_not_negative(self):
        """A client clock slightly ahead must not fire every trigger immediately."""

        anchor = CLOCK + timedelta(minutes=5)
        result = rules.inactivity(anchor, CLOCK, TEN_MINUTES)
        assert result["seconds_elapsed"] == 0.0
        assert result["due"] is False

    def test_elapsed_is_never_negative(self):
        assert rules.elapsed_seconds(CLOCK + timedelta(hours=1), CLOCK) == 0.0

    def test_the_anchor_is_reported_as_an_iso_string(self):
        result = rules.inactivity(CLOCK - timedelta(minutes=5), CLOCK, TEN_MINUTES)
        assert result["anchor"].endswith("+00:00")


# --------------------------------------------------------------------------- #
# One trigger per customer message
# --------------------------------------------------------------------------- #


class TestOncePerCustomerMessage:
    """Once per customer message, stated as arithmetic.

    The token is the instant of the newest customer message. A second message mints a new
    token and re-arms the trigger exactly once; re-evaluating against a spent token does
    not fire.
    """

    def test_the_quote_is_published(self):
        assert vocab.ONCE_PER_MESSAGE == "can only trigger once per customer message"

    def test_the_token_is_the_newest_customer_message(self):
        parts = [customer_at(40), customer_at(35), customer_at(30)]
        assert rules.arm_token(parts) == rules.stamp(CLOCK - timedelta(minutes=30))

    def test_a_conversation_with_no_customer_message_has_no_token(self):
        assert rules.arm_token([teammate_at(10)]) is None
        assert rules.arm_token([]) is None

    def test_a_teammate_message_does_not_re_arm(self):
        """The limit is per *customer* message; a rep's reply is not one."""

        first = rules.arm_token([customer_at(40)])
        parts = [customer_at(40), teammate_at(35)]
        assert rules.arm_token(parts) == first

    def test_a_workflow_message_does_not_re_arm(self):
        first = rules.arm_token([customer_at(40)])
        parts = [customer_at(40), system_at(35)]
        assert rules.arm_token(parts) == first

    def test_an_unspent_token_may_fire(self):
        token = rules.arm_token([customer_at(40)])
        assert rules.should_fire(token, []) is True

    def test_a_spent_token_does_not_fire_again(self):
        token = rules.arm_token([customer_at(40)])
        assert rules.should_fire(token, [token]) is False

    def test_a_second_customer_message_re_arms_exactly_once(self):
        parts = [customer_at(40)]
        first = rules.arm_token(parts)

        # The first evaluation spends the first token.
        assert rules.should_fire(first, []) is True
        consumed = [first]
        assert rules.should_fire(first, consumed) is False

        # A second customer message mints a new token, and it fires once.
        parts.append(customer_at(35))
        second = rules.arm_token(parts)
        assert second != first
        assert rules.should_fire(second, consumed) is True
        consumed.append(second)

        # And not a second time.
        assert rules.should_fire(second, consumed) is False

    def test_three_evaluations_against_one_token_fire_once(self):
        """Evaluated one at a time: each result is spent before the next is asked for.

        The whole rule is that a firing consumes the token, so the second and third calls
        see a spent token. Building all three answers before spending anything would test
        nothing.
        """

        token = rules.arm_token([customer_at(40)])
        consumed: list[str] = []
        outcomes = []
        for _ in range(3):
            outcome = rules.should_fire(token, consumed)
            outcomes.append(outcome)
            if outcome:
                consumed.append(token)
        assert outcomes == [True, False, False]

    def test_the_third_evaluation_against_one_token_still_does_not_fire(self):
        token = rules.arm_token([customer_at(40)])
        assert rules.should_fire(token, [token, token]) is False

    def test_a_conversation_with_no_token_never_fires(self):
        assert rules.should_fire(None, []) is False

    def test_a_new_conversation_has_a_new_token(self):
        assert rules.arm_token([customer_at(40)]) != rules.arm_token([customer_at(30)])


# --------------------------------------------------------------------------- #
# The API-created exemption
# --------------------------------------------------------------------------- #


class TestEligibility:
    """A conversation created via the REST API is exempt. The research says:"

    Reported as a skip with the published code rather than raised, because a conversation
    created through the API is exempt whatever its state, and "this one is exempt" is an
    outcome of the sweep rather than a fault in the caller's request.
    """

    def test_the_quote_is_published(self):
        assert vocab.API_CREATED_EXEMPT == (
            "This workflow won't trigger for conversations created via our REST API."
        )

    def test_an_api_created_conversation_is_exempt(self):
        result = rules.trigger_eligible({"origin": vocab.ORIGIN_API, "state": vocab.STATE_OPEN})
        assert result["eligible"] is False
        assert result["reason"] == vocab.SKIP_API_CREATED

    def test_the_exemption_code_carries_the_quote_as_its_text(self):
        assert vocab.SKIP_REASON_TEXT[vocab.SKIP_API_CREATED] == vocab.API_CREATED_EXEMPT

    def test_the_exemption_wins_over_the_state_checks(self):
        """An api-origin closed conversation reports the exemption, not the closed skip."""

        result = rules.trigger_eligible({"origin": vocab.ORIGIN_API, "state": vocab.STATE_CLOSED})
        assert result["reason"] == vocab.SKIP_API_CREATED

    def test_the_exemption_is_flagged_as_a_specification_rule(self):
        assert vocab.SKIP_API_CREATED in vocab.SPECIFICATION_SKIPS

    def test_an_inbox_conversation_is_eligible(self):
        result = rules.trigger_eligible({"origin": vocab.ORIGIN_INBOX, "state": vocab.STATE_OPEN})
        assert result["eligible"] is True
        assert result["reason"] is None

    def test_a_snoozed_conversation_is_skipped_for_being_snoozed(self):
        result = rules.trigger_eligible(
            {"origin": vocab.ORIGIN_INBOX, "state": vocab.STATE_SNOOZED}
        )
        assert result["eligible"] is False
        assert result["reason"] == vocab.SKIP_SNOOZED

    def test_a_closed_conversation_is_skipped_for_being_closed(self):
        result = rules.trigger_eligible({"origin": vocab.ORIGIN_INBOX, "state": vocab.STATE_CLOSED})
        assert result["reason"] == vocab.SKIP_CLOSED

    def test_the_three_reasons_are_distinct(self):
        reasons = {
            rules.trigger_eligible({"origin": vocab.ORIGIN_API, "state": vocab.STATE_OPEN})[
                "reason"
            ],
            rules.trigger_eligible({"origin": vocab.ORIGIN_INBOX, "state": vocab.STATE_SNOOZED})[
                "reason"
            ],
            rules.trigger_eligible({"origin": vocab.ORIGIN_INBOX, "state": vocab.STATE_CLOSED})[
                "reason"
            ],
        }
        assert len(reasons) == 3

    def test_a_missing_origin_defaults_to_the_inbox(self):
        result = rules.trigger_eligible({"state": vocab.STATE_OPEN})
        assert result["origin"] == vocab.ORIGIN_INBOX
        assert result["eligible"] is True

    def test_an_unknown_origin_is_refused(self):
        with pytest.raises(ChaseRefusal) as caught:
            rules.trigger_eligible({"origin": "carrier_pigeon", "state": vocab.STATE_OPEN})
        assert caught.value.code == "unknown_origin"

    def test_every_skip_reason_is_published(self):
        assert len(vocab.SKIP_REASONS) == len(vocab.SKIP_REASON_TEXT)
        for reason in vocab.SKIP_REASONS:
            assert vocab.SKIP_REASON_TEXT[reason]

    def test_every_skip_reason_is_a_single_lowercase_token(self):
        for reason in vocab.SKIP_REASONS:
            assert reason == reason.lower()
            assert " " not in reason

    def test_only_the_two_researched_rules_are_specification_skips(self):
        assert vocab.SPECIFICATION_SKIPS == {
            vocab.SKIP_API_CREATED,
            vocab.SKIP_ALREADY_FIRED,
        }


# --------------------------------------------------------------------------- #
# Wait and Snooze precedence over the global auto-close setting
# --------------------------------------------------------------------------- #


class TestClosePrecedence:
    """Wait and Snooze outrank the global setting. The research says so in words:

    Read off the step list rather than stored as a flag, so a trigger cannot claim
    precedence it does not have.
    """

    WAIT_STEPS = [
        {"kind": vocab.STEP_MESSAGE},
        {"kind": vocab.STEP_WAIT},
        {"kind": vocab.STEP_CLOSE},
    ]
    PLAIN_STEPS = [{"kind": vocab.STEP_MESSAGE}, {"kind": vocab.STEP_CLOSE}]

    def test_the_quote_is_published(self):
        assert vocab.WAIT_PRECEDENCE == (
            "Any workflow containing a Wait or Snooze action will take precedence"
        )

    def test_a_wait_gives_the_workflow_precedence(self):
        assert rules.has_precedence_step(self.WAIT_STEPS) is True

    def test_a_snooze_gives_the_workflow_precedence(self):
        assert rules.has_precedence_step([{"kind": vocab.STEP_SNOOZE}]) is True

    def test_a_workflow_with_a_wait_owns_the_close(self):
        result = rules.close_authority(self.WAIT_STEPS, global_auto_close=False)
        assert result["authority"] == "workflow"
        assert result["workflow_owns_close"] is True

    def test_a_workflow_with_a_wait_owns_the_close_even_when_the_global_is_on(self):
        """The researched precedence is exactly this case: both want to close."""

        result = rules.close_authority(self.WAIT_STEPS, global_auto_close=True)
        assert result["authority"] == "workflow"
        assert result["global_auto_close"] is True

    def test_a_workflow_without_either_falls_back_to_the_global_setting(self):
        result = rules.close_authority(self.PLAIN_STEPS, global_auto_close=True)
        assert result["authority"] == "global"
        assert result["workflow_owns_close"] is False

    def test_with_no_global_setting_nothing_closes_the_conversation(self):
        result = rules.close_authority(self.PLAIN_STEPS, global_auto_close=False)
        assert result["authority"] == "none"

    def test_the_three_answers_are_distinguishable(self):
        authorities = {
            rules.close_authority(self.WAIT_STEPS, False)["authority"],
            rules.close_authority(self.PLAIN_STEPS, True)["authority"],
            rules.close_authority(self.PLAIN_STEPS, False)["authority"],
        }
        assert authorities == {"workflow", "global", "none"}

    def test_the_precedence_step_is_reported(self):
        result = rules.close_authority(self.WAIT_STEPS, False)
        assert result["precedence_steps"] == [vocab.STEP_WAIT]

    def test_the_quote_travels_with_the_answer(self):
        assert rules.close_authority(self.WAIT_STEPS, False)["quote"] == vocab.WAIT_PRECEDENCE

    def test_an_empty_step_list_has_no_precedence(self):
        assert rules.has_precedence_step([]) is False
        assert rules.close_authority([], True)["authority"] == "global"

    def test_a_close_step_alone_is_not_a_precedence_step(self):
        assert rules.has_precedence_step([{"kind": vocab.STEP_CLOSE}]) is False

    def test_the_precedence_steps_are_wait_and_snooze(self):
        assert vocab.PRECEDENCE_STEPS == {vocab.STEP_WAIT, vocab.STEP_SNOOZE}


# --------------------------------------------------------------------------- #
# Interruption events
# --------------------------------------------------------------------------- #


class TestInterruption:
    """Which events cancel a wait, and when.

    Two conditions, both required: the step listed the event, and the part is from the
    right author. A part the workflow itself wrote never cancels anything, and a part
    from before the wait started does not either.
    """

    def test_the_quote_is_published(self):
        assert "cancel the wait (teammate and customer messages)" in (
            vocab.INTERRUPTION_EVENTS_QUOTE
        )

    def test_only_the_two_researched_events_exist(self):
        assert vocab.INTERRUPTION_EVENTS == (
            vocab.INTERRUPTION_CUSTOMER_MESSAGE,
            vocab.INTERRUPTION_TEAMMATE_MESSAGE,
        )

    def test_a_step_with_no_configured_event_is_cancelled_by_nothing(self):
        step = {"kind": vocab.STEP_WAIT, "interruption_events": []}
        assert rules.interruption_cancels(step, {"author_kind": vocab.AUTHOR_CUSTOMER}) is False

    def test_a_listed_customer_event_cancels_on_a_customer_message(self):
        step = {
            "kind": vocab.STEP_WAIT,
            "interruption_events": [vocab.INTERRUPTION_CUSTOMER_MESSAGE],
        }
        assert rules.interruption_cancels(step, {"author_kind": vocab.AUTHOR_CUSTOMER}) is True

    def test_a_customer_event_does_not_cancel_on_a_teammate_message(self):
        step = {
            "kind": vocab.STEP_WAIT,
            "interruption_events": [vocab.INTERRUPTION_CUSTOMER_MESSAGE],
        }
        assert rules.interruption_cancels(step, {"author_kind": vocab.AUTHOR_TEAMMATE}) is False

    def test_both_events_listed_cancels_on_either(self):
        step = {"kind": vocab.STEP_WAIT, "interruption_events": list(vocab.INTERRUPTION_EVENTS)}
        assert rules.interruption_cancels(step, {"author_kind": vocab.AUTHOR_CUSTOMER}) is True
        assert rules.interruption_cancels(step, {"author_kind": vocab.AUTHOR_TEAMMATE}) is True

    def test_the_workflow_own_message_never_cancels_its_own_wait(self):
        """The reason a step 3 message does not stop the step 4 wait it precedes."""

        step = {"kind": vocab.STEP_WAIT, "interruption_events": list(vocab.INTERRUPTION_EVENTS)}
        assert rules.interruption_cancels(step, {"author_kind": vocab.AUTHOR_SYSTEM}) is False

    def test_a_missing_event_list_is_treated_as_none(self):
        assert (
            rules.interruption_cancels({"kind": vocab.STEP_WAIT}, {"author_kind": "customer"})
            is False
        )

    def test_an_unknown_event_is_refused_on_the_step(self):
        with pytest.raises(ChaseRefusal) as caught:
            rules.require_interruption_events(["customer_message", "earthquake"])
        assert caught.value.code == "unknown_interruption_event"

    def test_events_are_normalised_and_de_duplicated(self):
        assert rules.require_interruption_events(["Customer-Message", "customer_message"]) == [
            vocab.INTERRUPTION_CUSTOMER_MESSAGE
        ]

    def test_no_events_is_legal(self):
        assert rules.require_interruption_events(None) == []
        assert rules.require_interruption_events([]) == []

    def test_a_single_event_string_is_accepted(self):
        assert rules.require_interruption_events("teammate_message") == [
            vocab.INTERRUPTION_TEAMMATE_MESSAGE
        ]


class TestFirstInterruption:
    """The earliest part that cancels a wait, ignoring anything older than the wait."""

    def test_a_message_inside_the_window_cancels_it(self):
        step = {
            "kind": vocab.STEP_WAIT,
            "interruption_events": [vocab.INTERRUPTION_CUSTOMER_MESSAGE],
            "started_at": rules.stamp(CLOCK - timedelta(minutes=10)),
        }
        parts = [customer_at(5)]
        found = rules.first_interruption(step, parts)
        assert found is not None
        assert found["body"] == "hello"

    def test_a_message_older_than_the_wait_does_not(self):
        """A buyer who wrote before the wait began has not interrupted it."""

        step = {
            "kind": vocab.STEP_WAIT,
            "interruption_events": [vocab.INTERRUPTION_CUSTOMER_MESSAGE],
            "started_at": rules.stamp(CLOCK - timedelta(minutes=10)),
        }
        assert rules.first_interruption(step, [customer_at(30)]) is None

    def test_a_part_at_the_same_instant_as_the_wait_does_cancels(self):
        started = rules.stamp(CLOCK - timedelta(minutes=10))
        step = {
            "kind": vocab.STEP_WAIT,
            "interruption_events": [vocab.INTERRUPTION_CUSTOMER_MESSAGE],
            "started_at": started,
        }
        assert rules.first_interruption(step, [customer_at(10)]) is not None

    def test_the_earliest_cancelling_part_is_the_one_returned(self):
        step = {
            "kind": vocab.STEP_WAIT,
            "interruption_events": [vocab.INTERRUPTION_CUSTOMER_MESSAGE],
            "started_at": rules.stamp(CLOCK - timedelta(minutes=10)),
        }
        parts = [customer_at(2, "later"), customer_at(8, "earlier")]
        found = rules.first_interruption(step, parts)
        assert found is not None
        assert found["body"] == "earlier"

    def test_a_workflow_message_never_cancels(self):
        step = {
            "kind": vocab.STEP_WAIT,
            "interruption_events": list(vocab.INTERRUPTION_EVENTS),
            "started_at": rules.stamp(CLOCK - timedelta(minutes=10)),
        }
        assert rules.first_interruption(step, [system_at(1)]) is None


class TestWaitState:
    """Is a wait satisfied by its clock, interrupted, or still running?"""

    def wait_step(self, minutes: int = 10, started_minutes: float = 10) -> dict[str, object]:
        return {
            "kind": vocab.STEP_WAIT,
            "duration_seconds": minutes * 60,
            "interruption_events": [vocab.INTERRUPTION_CUSTOMER_MESSAGE],
            "started_at": rules.stamp(CLOCK - timedelta(minutes=started_minutes)),
        }

    def test_a_wait_with_time_left_is_still_waiting(self):
        verdict = rules.wait_state(self.wait_step(started_minutes=5), [], CLOCK)
        assert verdict["state"] == "waiting"
        assert verdict["remaining_seconds"] == pytest.approx(300.0)

    def test_a_wait_whose_clock_ran_out_has_elapsed(self):
        verdict = rules.wait_state(self.wait_step(started_minutes=20), [], CLOCK)
        assert verdict["state"] == "elapsed"
        assert verdict["remaining_seconds"] == 0.0

    def test_a_cancelling_message_interrupts_a_wait_with_time_left(self):
        verdict = rules.wait_state(self.wait_step(started_minutes=5), [customer_at(1)], CLOCK)
        assert verdict["state"] == "interrupted"
        assert verdict["interrupted_by"] == vocab.AUTHOR_CUSTOMER

    def test_interruption_beats_the_clock(self):
        """Both at once still means interrupted: the buyer answered."""

        verdict = rules.wait_state(self.wait_step(started_minutes=30), [customer_at(1)], CLOCK)
        assert verdict["state"] == "interrupted"

    def test_a_wait_that_never_started_is_running(self):
        verdict = rules.wait_state({"kind": vocab.STEP_WAIT, "duration_seconds": 600}, [], CLOCK)
        assert verdict["state"] == "running"

    def test_the_interruption_reports_who_and_when(self):
        verdict = rules.wait_state(self.wait_step(started_minutes=5), [customer_at(1)], CLOCK)
        assert verdict["at"] == rules.stamp(CLOCK - timedelta(minutes=1))

    def test_an_interrupted_run_is_finished_and_there_is_no_resume(self):
        assert vocab.INTERRUPTED_IS_TERMINAL is True
        assert vocab.RUN_INTERRUPTED in vocab.CLOSED_RUN_STATES
        assert not any("resume" in kind for kind in vocab.STEP_KINDS)


# --------------------------------------------------------------------------- #
# Step normalisation
# --------------------------------------------------------------------------- #


class TestNormaliseSteps:
    """Order is preserved, because the research describes a sequence."""

    def test_the_researched_sequence_keeps_its_order(self):
        steps = rules.normalise_steps(
            [
                {"kind": vocab.STEP_MESSAGE, "body": "still there?"},
                {"kind": vocab.STEP_WAIT, "duration_seconds": 900},
                {"kind": vocab.STEP_CLOSE_MESSAGE, "body": "closing"},
                {"kind": vocab.STEP_CLOSE},
                {"kind": vocab.STEP_TAG, "tag": "no reply"},
            ]
        )
        assert [step["kind"] for step in steps] == [
            vocab.STEP_MESSAGE,
            vocab.STEP_WAIT,
            vocab.STEP_CLOSE_MESSAGE,
            vocab.STEP_CLOSE,
            vocab.STEP_TAG,
        ]

    def test_the_reroute_sequence_keeps_its_order(self):
        steps = rules.normalise_steps(
            [
                {"kind": vocab.STEP_SHOW_EXPECTED_REPLY_TIME, "duration_seconds": 900},
                {"kind": vocab.STEP_MARK_PRIORITY},
                {"kind": vocab.STEP_TAG, "tag": vocab.DELAYED_RESPONSE_TAG},
                {"kind": vocab.STEP_ASSIGN, "inbox": "escalations"},
            ]
        )
        assert [step["kind"] for step in steps] == [
            vocab.STEP_SHOW_EXPECTED_REPLY_TIME,
            vocab.STEP_MARK_PRIORITY,
            vocab.STEP_TAG,
            vocab.STEP_ASSIGN,
        ]

    def test_a_wait_inherits_the_default_duration_when_none_is_given(self):
        step = rules.normalise_step({"kind": vocab.STEP_WAIT})
        assert step["duration_seconds"] == vocab.DEFAULT_WAIT_SECONDS
        assert step["interruption_events"] == []

    def test_step_duration_reads_back_what_normalise_wrote(self):
        """One reader for "does this step carry a number", so the two cannot disagree."""

        wait = rules.normalise_step({"kind": vocab.STEP_WAIT, "duration_seconds": 900})
        assert rules.step_duration(wait) == 900
        assert rules.step_duration({"kind": vocab.STEP_CLOSE}) is None

    def test_an_out_of_range_wait_duration_is_refused(self):
        with pytest.raises(ChaseRefusal) as caught:
            rules.normalise_step({"kind": vocab.STEP_WAIT, "duration_seconds": 30})
        assert caught.value.code == "duration_out_of_range"

    def test_an_unknown_step_kind_is_refused(self):
        with pytest.raises(ChaseRefusal) as caught:
            rules.normalise_step({"kind": "escalate_to_ceo"})
        assert caught.value.code == "unknown_step_kind"

    def test_a_tag_step_needs_a_name(self):
        with pytest.raises(ChaseRefusal):
            rules.normalise_step({"kind": vocab.STEP_TAG})

    def test_an_assign_step_needs_an_inbox(self):
        with pytest.raises(ChaseRefusal) as caught:
            rules.normalise_step({"kind": vocab.STEP_ASSIGN})
        assert caught.value.code == "inbox_unknown"

    def test_a_tag_is_normalised_to_lowercase_single_spacing(self):
        assert (
            rules.normalise_step({"kind": vocab.STEP_TAG, "tag": "  Delayed   Response "})["tag"]
            == vocab.DELAYED_RESPONSE_TAG
        )

    def test_an_unknown_key_is_dropped_rather_than_refused(self):
        step = rules.normalise_step({"kind": vocab.STEP_MESSAGE, "vendor_extra": {"x": 1}})
        assert "vendor_extra" not in step

    def test_no_steps_is_legal(self):
        assert rules.normalise_steps(None) == []

    def test_a_single_step_mapping_is_wrapped(self):
        assert len(rules.normalise_steps({"kind": vocab.STEP_CLOSE})) == 1

    def test_mark_priority_is_always_true(self):
        assert rules.normalise_step({"kind": vocab.STEP_MARK_PRIORITY})["priority"] is True

    def test_every_step_kind_is_accepted(self):
        for kind in vocab.STEP_KINDS:
            payload: dict[str, object] = {"kind": kind}
            if kind == vocab.STEP_TAG:
                payload["tag"] = "no reply"
            if kind == vocab.STEP_ASSIGN:
                payload["inbox"] = "escalations"
            assert rules.normalise_step(payload)["kind"] == kind

    def test_show_expected_reply_time_carries_a_duration(self):
        """It reads the number from office hours, so it must be bound-checked like one."""

        step = rules.normalise_step(
            {"kind": vocab.STEP_SHOW_EXPECTED_REPLY_TIME, "duration_seconds": 900}
        )
        assert step["duration_seconds"] == 900
        with pytest.raises(ChaseRefusal) as caught:
            rules.normalise_step(
                {"kind": vocab.STEP_SHOW_EXPECTED_REPLY_TIME, "duration_seconds": 10}
            )
        assert caught.value.code == "duration_out_of_range"


# --------------------------------------------------------------------------- #
# Office hours
# --------------------------------------------------------------------------- #


class TestOfficeHours:
    """The derived weekly schedule, and the walk that respects it.

    The derivation is recorded as unsourced in the inferences register, because the
    specification names office-hours configuration as a data source and sources no model.
    """

    def test_the_derivation_quote_is_published(self):
        assert "9:05am on the next working day" in vocab.OFFICE_HOURS_QUOTE

    def test_the_default_is_monday_to_friday_nine_to_six(self):
        schedule = rules.default_office_hours()
        assert set(schedule) == set(vocab.WEEKDAYS)
        assert schedule["monday"] == {vocab.OFFICE_OPEN: 540, vocab.OFFICE_CLOSE: 1080}
        assert schedule["friday"] == {vocab.OFFICE_OPEN: 540, vocab.OFFICE_CLOSE: 1080}

    def test_the_default_closes_at_the_weekend(self):
        schedule = rules.default_office_hours()
        assert schedule["saturday"][vocab.OFFICE_OPEN] is None
        assert schedule["sunday"][vocab.OFFICE_OPEN] is None

    def test_the_default_covers_every_named_day(self):
        assert len(rules.default_office_hours()) == 7

    # -- normalisation ------------------------------------------------------- #

    def test_a_missing_day_is_closed(self):
        schedule = rules.normalise_office_hours({"monday": {"open": 540, "close": 1080}})
        assert schedule["tuesday"][vocab.OFFICE_OPEN] is None

    def test_a_day_may_be_given_as_a_pair(self):
        assert rules.normalise_office_hours({"tuesday": [570, 1020]})["tuesday"] == {
            vocab.OFFICE_OPEN: 570,
            vocab.OFFICE_CLOSE: 1020,
        }

    def test_a_day_may_be_given_as_hh_mm_strings(self):
        assert rules.normalise_office_hours({"wednesday": {"open": "09:30", "close": "17:00"}})[
            "wednesday"
        ] == {vocab.OFFICE_OPEN: 570, vocab.OFFICE_CLOSE: 1020}

    def test_a_day_with_only_one_bound_is_refused(self):
        with pytest.raises(ChaseRefusal) as caught:
            rules.normalise_office_hours({"monday": {"open": 540}})
        assert caught.value.code == "invalid_office_hours"

    def test_a_day_that_closes_before_it_opens_is_refused(self):
        """A schedule with no inside would make the walk spin forever."""

        with pytest.raises(ChaseRefusal) as caught:
            rules.normalise_office_hours({"monday": {"open": "18:00", "close": "09:00"}})
        assert caught.value.code == "invalid_office_hours"

    def test_a_day_that_closes_when_it_opens_is_refused(self):
        with pytest.raises(ChaseRefusal):
            rules.normalise_office_hours({"monday": {"open": 540, "close": 540}})

    def test_an_out_of_range_minute_is_refused(self):
        with pytest.raises(ChaseRefusal):
            rules.normalise_office_hours({"monday": {"open": -1, "close": 600}})

    def test_a_junk_value_is_refused(self):
        with pytest.raises(ChaseRefusal):
            rules.normalise_office_hours({"monday": "whenever"})

    def test_the_field_reported_is_the_day(self):
        with pytest.raises(ChaseRefusal) as caught:
            rules.normalise_office_hours({"monday": {"open": 1080, "close": 540}})
        assert "monday" in caught.value.detail

    # -- the walk ------------------------------------------------------------ #

    def test_the_worked_example_a_weekday_evening_plus_a_quarter_hour(self):
        """The corpus's worked example: "a message received at 5:50pm will have an expected
        response time of 9:05am on the next working day".

        Ten of the fifteen minutes pass before the close and the remaining five land at
        09:05."""

        anchor = _monday_at(17, 50)
        due = rules.expected_reply_time(anchor, QUARTER_HOUR, rules.default_office_hours())
        assert due.date() == (_monday_at(9, 0) + timedelta(days=1)).date()
        assert (due.hour, due.minute) == (9, 5)

    def test_a_saturday_anchor_lands_on_monday_morning(self):
        saturday = _monday_at(10, 0) + timedelta(days=5)
        assert vocab.WEEKDAYS[saturday.weekday()] == "saturday"
        due = rules.expected_reply_time(saturday, QUARTER_HOUR, rules.default_office_hours())
        assert due.weekday() == 0  # Monday
        assert (due.hour, due.minute) == (9, 15)

    def test_an_anchor_whose_target_stays_inside_is_unchanged(self):
        anchor = _monday_at(9, 30)
        due = rules.expected_reply_time(anchor, QUARTER_HOUR, rules.default_office_hours())
        assert (due.hour, due.minute) == (9, 45)

    def test_a_target_landing_exactly_on_the_close_is_inside(self):
        anchor = _monday_at(17, 45)
        due = rules.expected_reply_time(anchor, QUARTER_HOUR, rules.default_office_hours())
        assert (due.hour, due.minute) == (18, 0)

    def test_an_anchor_before_opening_waits_for_the_open_minute(self):
        anchor = _monday_at(7, 0)
        due = rules.expected_reply_time(anchor, QUARTER_HOUR, rules.default_office_hours())
        assert (due.hour, due.minute) == (9, 15)

    def test_a_whole_day_off_moves_to_the_next_open_day(self):
        """A Friday evening target lands on Monday, because Saturday and Sunday are shut."""

        friday_evening = _monday_at(0, 0) + timedelta(days=4, hours=17, minutes=50)
        due = rules.expected_reply_time(friday_evening, QUARTER_HOUR, rules.default_office_hours())
        assert due.weekday() == 0
        assert (due.hour, due.minute) == (9, 5)

    def test_a_schedule_with_no_open_day_returns_the_plain_sum(self):
        """There is no instant inside such a schedule, so the arithmetic is the answer."""

        closed = {
            day: {vocab.OFFICE_OPEN: None, vocab.OFFICE_CLOSE: None} for day in vocab.WEEKDAYS
        }
        anchor = _monday_at(17, 50)
        due = rules.expected_reply_time(anchor, QUARTER_HOUR, closed)
        assert due == anchor + timedelta(seconds=QUARTER_HOUR)

    def test_a_zero_duration_returns_the_anchor(self):
        anchor = _monday_at(20, 0)
        assert rules.expected_reply_time(anchor, 0, rules.default_office_hours()) == anchor

    def test_a_custom_schedule_is_honoured(self):
        schedule = rules.normalise_office_hours(
            {day: {"open": "11:00", "close": "15:00"} for day in vocab.DEFAULT_OFFICE_WEEKDAYS}
        )
        due = rules.expected_reply_time(_monday_at(14, 50), QUARTER_HOUR, schedule)
        assert (due.hour, due.minute) == (11, 5)
        assert due.day == (_monday_at(14, 50) + timedelta(days=1)).day

    def test_the_walk_terminates_rather_than_spinning(self):
        """Bounded by the researched maximum duration in days, plus a margin."""

        assert vocab.MAX_WALK_DAYS > 14
        closed = {
            day: {vocab.OFFICE_OPEN: None, vocab.OFFICE_CLOSE: None} for day in vocab.WEEKDAYS
        }
        anchor = _monday_at(9, 0)
        assert rules.expected_reply_time(anchor, 13 * 24 * 3600, closed) is not None


class TestOfficeMinutes:
    """How many minutes of a calendar span fall inside the office's opening hours.

    This is a *different question* from :func:`expected_reply_time`. There, a
    fifteen-minute response target means fifteen minutes of working time, so the walk
    continues past the close until the budget is spent and a Saturday anchor answers
    09:15 Monday. Here the span is fixed by the caller, and the answer is the open minutes
    inside it: a Saturday quarter-hour contains none.
    """

    def test_inside_the_window_the_two_numbers_agree(self):
        anchor = _monday_at(9, 0)
        assert (
            rules.office_minutes_between(anchor, QUARTER_HOUR, rules.default_office_hours()) == 15
        )

    def test_a_saturday_span_contains_no_office_minutes(self):
        saturday = _monday_at(10, 0) + timedelta(days=5)
        assert vocab.WEEKDAYS[saturday.weekday()] == "saturday"
        assert (
            rules.office_minutes_between(saturday, QUARTER_HOUR, rules.default_office_hours()) == 0
        )

    def test_a_sunday_span_that_ends_before_monday_opening_contains_nothing(self):
        """Sunday 10:00 for exactly a day ends at Monday 10:00, so Monday's first hour is
        in the span and Sunday's twenty-four hours are not."""

        sunday = _monday_at(10, 0) + timedelta(days=6)
        assert rules.office_minutes_between(sunday, 86400, rules.default_office_hours()) == 60

    def test_a_span_covering_only_sunday_contains_nothing(self):
        """Midnight to midnight on a Sunday, which never touches an open window."""

        sunday_midnight = _monday_at(0, 0) + timedelta(days=6)
        assert vocab.WEEKDAYS[sunday_midnight.weekday()] == "sunday"
        assert (
            rules.office_minutes_between(sunday_midnight, 86400, rules.default_office_hours()) == 0
        )

    def test_a_span_that_starts_before_opening_counts_only_what_is_open(self):
        """08:00 to 09:00 is entirely shut, so the answer is zero, not sixty."""

        assert (
            rules.office_minutes_between(_monday_at(8, 0), 3600, rules.default_office_hours()) == 0
        )

    def test_a_span_that_ends_after_closing_counts_only_what_was_open(self):
        """17:50 to 18:50 is ten minutes open and fifty closed."""

        assert (
            rules.office_minutes_between(_monday_at(17, 50), 3600, rules.default_office_hours())
            == 10
        )

    def test_a_full_working_day_is_nine_hours(self):
        assert (
            rules.office_minutes_between(_monday_at(0, 0), 86400, rules.default_office_hours())
            == 540
        )

    def test_a_full_week_is_thirty_three_hours(self):
        """Five nine-hour days, and the weekend contributes none."""

        anchor = _monday_at(0, 0)
        assert (
            rules.office_minutes_between(anchor, 7 * 86400, rules.default_office_hours()) == 5 * 540
        )

    def test_a_multi_day_span_counts_much_less_than_elapsed(self):
        anchor = _monday_at(17, 50)
        elapsed_minutes = 3 * 24 * 60
        counted = rules.office_minutes_between(
            anchor, elapsed_minutes * 60, rules.default_office_hours()
        )
        assert counted < elapsed_minutes / 2

    def test_the_count_is_bounded_by_the_span(self):
        """Never more office minutes than the span has minutes, whatever the schedule."""

        anchor = _monday_at(9, 0)
        for minutes in (1, 15, 60, 600, 3600, 86400, 7 * 24 * 60):
            counted = rules.office_minutes_between(
                anchor, minutes * 60, rules.default_office_hours()
            )
            assert 0 <= counted <= minutes

    def test_a_closed_all_day_schedule_counts_nothing(self):
        closed = {
            day: {vocab.OFFICE_OPEN: None, vocab.OFFICE_CLOSE: None} for day in vocab.WEEKDAYS
        }
        assert rules.office_minutes_between(CLOCK, 3600, closed) == 0

    def test_a_zero_span_counts_nothing(self):
        assert rules.office_minutes_between(CLOCK, 0, rules.default_office_hours()) == 0

    def test_a_negative_span_counts_nothing_rather_than_going_backwards(self):
        assert rules.office_minutes_between(CLOCK, -3600, rules.default_office_hours()) == 0

    def test_the_two_questions_give_different_answers_for_a_weekend(self):
        """The distinction this class exists to pin down.

        A Saturday anchor plus a quarter hour: the calendar span contains no office time
        at all, while the working-time target is reached on Monday morning.
        """

        saturday = _monday_at(10, 0) + timedelta(days=5)
        assert (
            rules.office_minutes_between(saturday, QUARTER_HOUR, rules.default_office_hours()) == 0
        )
        due = rules.expected_reply_time(saturday, QUARTER_HOUR, rules.default_office_hours())
        assert due.weekday() == 0
        assert (due.hour, due.minute) == (9, 15)

    def test_a_longer_span_that_covers_two_working_days(self):
        anchor = _monday_at(9, 0)
        assert (
            rules.office_minutes_between(anchor, 2 * 86400, rules.default_office_hours()) == 2 * 540
        )


# --------------------------------------------------------------------------- #
# Instants
# --------------------------------------------------------------------------- #


class TestInstants:
    """Reading and writing timestamps, which every other rule depends on."""

    def test_a_datetime_is_stamped_in_utc_with_milliseconds(self):
        stamped = rules.stamp(_monday_at(9, 0))
        assert stamped.endswith("+00:00")
        assert ".000" in stamped

    def test_a_stored_string_round_trips(self):
        stamped = rules.stamp(_monday_at(9, 0))
        assert rules.coerce_instant(stamped) == _monday_at(9, 0)

    def test_a_trailing_z_is_accepted(self):
        assert rules.coerce_instant("2026-03-02T09:00:00Z") == _monday_at(9, 0)

    def test_a_naive_instant_is_read_as_utc(self):
        assert rules.coerce_instant("2026-03-02T09:00:00") == _monday_at(9, 0)

    def test_junk_is_not_an_instant(self):
        for value in ("", "not a time", None, [], {}):
            assert rules.coerce_instant(value) is None

    def test_an_unparseable_string_is_refused_when_stamping(self):
        with pytest.raises(ChaseRefusal) as caught:
            rules.stamp("the day before yesterday")
        assert caught.value.code == "not_an_instant"

    def test_a_number_is_read_as_a_timestamp(self):
        assert rules.coerce_instant(0) == datetime(1970, 1, 1, tzinfo=timezone.utc)

    def test_a_bool_is_not_a_timestamp(self):
        assert rules.coerce_instant(True) is None

    def test_reading_a_stored_value_never_raises(self):
        """A record from an older version may lack the field, and a list should still read."""

        assert rules.instant_or_none(None) is None
        assert rules.instant_or_none("rubbish") is None


# --------------------------------------------------------------------------- #
# The vocabulary, as published
# --------------------------------------------------------------------------- #


class TestVocabulary:
    """The page renders its pickers from this and the validator raises from it."""

    @pytest.fixture
    def published(self):
        return vocab.published_vocabulary()

    def test_it_covers_every_collection_this_workflow_writes(self, published):
        collections = published["collections"]
        assert collections["conversations"] == vocab.CONVERSATIONS
        assert collections["parts"] == vocab.PARTS
        assert collections["triggers"] == vocab.TRIGGERS
        assert collections["runs"] == vocab.RUNS
        assert collections["activity"] == vocab.ACTIVITY
        assert collections["office_hours"] == vocab.OFFICE_HOURS

    def test_every_collection_is_prefixed_by_this_ticket(self, published):
        for name in published["collections"].values():
            assert name.startswith("wf107_"), name

    def test_it_publishes_both_trigger_kinds_with_their_labels(self, published):
        assert published["trigger_kinds"] == list(vocab.TRIGGER_KINDS)
        assert set(published["trigger_kind_labels"]) == set(vocab.TRIGGER_KINDS)
        assert (
            published["trigger_kind_labels"][vocab.CUSTOMER_IDLE]
            == "If customer has been unresponsive"
        )
        assert (
            published["trigger_kind_labels"][vocab.TEAMMATE_IDLE]
            == "If teammate has been unresponsive"
        )

    def test_it_publishes_all_nine_step_kinds(self, published):
        assert published["step_kinds"] == list(vocab.STEP_KINDS)
        assert len(vocab.STEP_KINDS) == 9
        assert set(published["step_labels"]) == set(vocab.STEP_KINDS)

    def test_the_researched_blocks_are_all_present(self, published):
        for kind in (
            vocab.STEP_MESSAGE,
            vocab.STEP_WAIT,
            vocab.STEP_SNOOZE,
            vocab.STEP_CLOSE_MESSAGE,
            vocab.STEP_SHOW_EXPECTED_REPLY_TIME,
            vocab.STEP_MARK_PRIORITY,
            vocab.STEP_TAG,
            vocab.STEP_CLOSE,
            vocab.STEP_ASSIGN,
        ):
            assert kind in published["step_kinds"]

    def test_it_publishes_the_step_2_trigger_fields(self, published):
        assert published["trigger_fields"] == ["channels", "audience", "scheduling", "goal"]

    def test_it_publishes_the_anchors_with_their_labels(self, published):
        assert published["trigger_anchors"] == dict(vocab.TRIGGER_ANCHORS)
        assert published["anchor_labels"] == dict(vocab.ANCHOR_LABELS)
        assert set(published["anchor_labels"]) == set(vocab.TRIGGER_ANCHORS.values())

    def test_the_anchor_labels_say_what_each_trigger_reads(self, published):
        labels = published["anchor_labels"]
        assert labels[vocab.ANCHOR_FIRST_CUSTOMER_MESSAGE] == "Customer's first message"
        assert labels[vocab.ANCHOR_LAST_ACTIVITY] == "Last message of any kind"

    def test_it_publishes_the_bounds_as_exclusive(self, published):
        bounds = published["bounds"]
        assert bounds["min_duration_seconds"] == 30
        assert bounds["max_duration_seconds"] == 14 * 24 * 60 * 60
        assert bounds["bounds_are_exclusive"] is True

    def test_it_publishes_every_refusal_code_with_its_status(self, published):
        codes = published["error_codes"]
        for code in (
            "duration_out_of_range",
            "unknown_trigger_kind",
            "unknown_step_kind",
            "unknown_interruption_event",
            "trigger_not_found",
            "conversation_not_found",
            "run_not_found",
            "run_not_advancing",
            "conversation_closed",
            "reroute_to_same_inbox",
            "inbox_unknown",
        ):
            assert code in codes
            assert codes[code]["detail"]
            assert codes[code]["status"] in (404, 409, 422)

    def test_a_not_found_code_is_404_and_a_conflict_is_409(self, published):
        assert published["error_codes"]["trigger_not_found"]["status"] == 404
        assert published["error_codes"]["run_not_advancing"]["status"] == 409
        assert published["error_codes"]["duration_out_of_range"]["status"] == 422

    def test_every_published_code_is_reachable_from_the_rules(self, published):
        """A code nothing raises is a code that could never be handled."""

        reachable = {
            "duration_out_of_range",
            "duration_not_a_number",
            "unknown_trigger_kind",
            "unknown_step_kind",
            "unknown_interruption_event",
            "unknown_channel",
            "unknown_tag",
            "unknown_author_kind",
            "unknown_origin",
            "unknown_conversation_state",
            "not_an_instant",
            "invalid_office_hours",
            "trigger_not_found",
            "conversation_not_found",
            "run_not_found",
            "trigger_already_live",
            "trigger_not_live",
            "run_not_advancing",
            "run_not_waiting",
            "conversation_closed",
            "reroute_to_same_inbox",
            "inbox_unknown",
        }
        assert set(published["error_codes"]) == reachable

    def test_it_publishes_the_skip_reasons_with_their_text(self, published):
        assert published["skip_reasons"] == list(vocab.SKIP_REASONS)
        assert set(published["skip_reason_text"]) == set(vocab.SKIP_REASONS)

    def test_it_publishes_the_create_without_contact_reply_default(self, published):
        """The spec says it "Defaults to false if not provided", stated not inherited."""

        assert vocab.CREATE_WITHOUT_CONTACT_REPLY_DEFAULT is False
        assert published["defaults"]["create_conversation_without_contact_reply"] is False

    def test_the_flag_name_is_the_vendor_spelling(self):
        assert vocab.CREATE_WITHOUT_CONTACT_REPLY == "create_conversation_without_contact_reply"

    def test_it_states_that_nothing_left_this_product(self, published):
        assert published["honesty"]["sent_by_this_product"] is False
        assert "no outbound HTTP" in vocab.SENT_BY_THIS_PRODUCT_QUOTE

    def test_it_publishes_the_evidence_the_rules_are_built_on(self, published):
        evidence = published["evidence"]
        for key in (
            "inactivity_timer",
            "duration_bounds",
            "first_message_anchor",
            "last_message_anchor",
            "once_per_message",
            "api_created_exempt",
            "wait_precedence",
            "interruption_events",
            "create_without_contact_reply",
            "assign_conversation",
            "office_hours_example",
        ):
            assert evidence[key]

    def test_it_publishes_the_webhooks_the_research_names(self, published):
        assert "conversation.admin.closed" in published["documented_webhooks"]
        assert "conversation.user.replied" in published["documented_webhooks"]

    def test_the_named_delay_tag_is_the_researched_spelling(self):
        assert vocab.DELAYED_RESPONSE_TAG == "delayed response"

    def test_every_constant_it_publishes_is_cp1252_encodable(self, published):
        """The seeder prints on a Windows console and one arrow broke the whole seed."""

        def walk(node):
            if isinstance(node, str):
                node.encode("cp1252")
            elif isinstance(node, dict):
                for key, value in node.items():
                    walk(key)
                    walk(value)
            elif isinstance(node, (list, tuple)):
                for entry in node:
                    walk(entry)

        walk(published)

    def test_the_channel_vocabulary_is_published(self, published):
        assert published["channels"] == ["messenger", "email", "api"]
        assert set(published["channel_labels"]) == set(vocab.CHANNELS)

    def test_an_unknown_channel_is_refused(self):
        with pytest.raises(ChaseRefusal) as caught:
            rules.require_channel("carrier_pigeon")
        assert caught.value.code == "unknown_channel"

    def test_channels_are_normalised_into_vocabulary_order(self):
        assert rules.require_channels(["api", "messenger"]) == ["messenger", "api"]
        assert rules.require_channels(["messenger", "messenger"]) == ["messenger"]

    def test_every_state_is_published_with_a_label(self, published):
        assert published["conversation_states"] == list(vocab.CONVERSATION_STATES)
        assert set(published["conversation_state_labels"]) == set(vocab.CONVERSATION_STATES)

    def test_only_an_open_conversation_is_swept(self):
        assert vocab.SWEEPABLE_STATES == {vocab.STATE_OPEN}

    def test_every_run_state_is_published(self, published):
        assert published["run_states"] == list(vocab.RUN_STATES)
        assert set(published["run_state_labels"]) == set(vocab.RUN_STATES)

    def test_the_four_run_states_are_the_documented_four(self):
        assert vocab.RUN_STATES == ("running", "waiting", "interrupted", "finished")

    def test_activity_codes_and_labels_line_up(self, published):
        assert len(published["activity_types"]) == len(published["activity_type_codes"])

    def test_every_activity_code_is_single_lowercase_token(self):
        for code in vocab.ACTIVITY_TYPE_CODES:
            assert code == code.lower()
            assert " " not in code
