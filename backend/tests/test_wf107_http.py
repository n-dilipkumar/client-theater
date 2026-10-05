"""WF-107's HTTP surface, its engine, its audit trail and its seed.

Two layers are exercised here. The engine is driven directly, with a clock the test owns,
because that is where the state machine lives. The router is then driven through the
application's own ``TestClient``, so the discovery, the error mapping and the audit rows
are all the real ones rather than stand-ins.

**No test in this file carries a fixed date.** Every instant comes from ``CLOCK`` or from a
weekday walked back off it, and every duration is an offset from one of those. The
``client`` fixture points at a fresh database per test, so nothing here can see another
test's rows, and every test that needs to move time moves it rather than waiting.

The audit-source rule gets its own section at the end: the contract requires that every
recorded source names a route the host actually mounted, and this file checks that against
the live registry rather than against a list somebody wrote twice.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from dsr.conversation_chase import rules, vocabulary as vocab
from dsr.conversation_chase.engine import ConversationChaseEngine
from dsr.conversation_chase.rules import ChaseRefusal
from dsr.db.audited import AuditedDatabase
from dsr.features import wf107_chase_unresponsive_buyers_and_reroute_ as feature
from dsr.store import RecordStore
from fastapi.routing import APIRoute

PREFIX = feature.router.prefix
TEN_MINUTES = 10 * 60
QUARTER_HOUR = 15 * 60


def _monday_at(hour: int, minute: int) -> datetime:
    today = datetime(2026, 3, 2, tzinfo=timezone.utc)
    monday = today - timedelta(days=today.weekday())
    return monday.replace(hour=hour, minute=minute)


CLOCK = _monday_at(9, 0)

#: A source that is legal to record from a test. Kept as a module constant so the tests
#: that assert on audit rows are asserting against the same string the routes use.
SOURCE = f"POST {PREFIX}/rooms/{{room_id}}/evaluate"
MESSAGE_SOURCE = f"POST {PREFIX}/rooms/{{room_id}}/conversations/{{conversation_id}}/messages"
CONVERSATION_SOURCE = f"POST {PREFIX}/rooms/{{room_id}}/conversations"


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture
def engine(store: RecordStore):
    """An engine over the test's own empty database, on the test's own clock."""

    return ConversationChaseEngine(store, now=lambda: CLOCK)


@pytest.fixture
def room_id(store: RecordStore) -> str:
    return store.create(
        "room", {"name": "Northwind Traders", "account": "Northwind"}, actor="dana"
    )["id"]


def at_engine(store: RecordStore, minutes: float) -> ConversationChaseEngine:
    """A second engine on the same store, with the clock moved."""

    frozen = CLOCK + timedelta(minutes=minutes)
    return ConversationChaseEngine(store, now=lambda: frozen)


# The researched step lists, built once and reused so the shape lives in one place.
CHASE_STEPS = [
    {"kind": vocab.STEP_MESSAGE, "body": "Just checking if you are still there?"},
    {
        "kind": vocab.STEP_WAIT,
        "duration_seconds": QUARTER_HOUR,
        "interruption_events": list(vocab.INTERRUPTION_EVENTS),
    },
    {"kind": vocab.STEP_CLOSE_MESSAGE, "body": "Closing this for now."},
    {"kind": vocab.STEP_CLOSE},
    {"kind": vocab.STEP_TAG, "tag": "no reply"},
]

REROUTE_STEPS = [
    {"kind": vocab.STEP_SHOW_EXPECTED_REPLY_TIME, "duration_seconds": QUARTER_HOUR},
    {"kind": vocab.STEP_MARK_PRIORITY},
    {"kind": vocab.STEP_TAG, "tag": vocab.DELAYED_RESPONSE_TAG},
    {"kind": vocab.STEP_ASSIGN, "inbox": "escalations"},
]


def open_conversation(engine, room_id: str, **payload: Any) -> dict[str, Any]:
    body = {"origin": vocab.ORIGIN_INBOX, "inbox": "sales"}
    body.update(payload)
    return engine.open_conversation(room_id, body, source=CONVERSATION_SOURCE, actor="dana")


def buyer_says(engine, conversation_id: str, minutes: float, body: str = "hello") -> dict[str, Any]:
    """A buyer message ``minutes`` **before** ``CLOCK``.

    Negative values are therefore after it, which is what the interruption tests need: a
    wait started at ``CLOCK + 20`` is only cancelled by a message the buyer sent later.
    """

    return engine.add_message(
        conversation_id,
        {
            "author_kind": vocab.AUTHOR_CUSTOMER,
            "at": rules.stamp(CLOCK - timedelta(minutes=minutes)),
            "body": body,
        },
        source=MESSAGE_SOURCE,
        actor="buyer@northwind.example",
    )


def make_trigger(engine, room_id: str, kind: str, **payload: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "kind": kind,
        "duration_seconds": TEN_MINUTES,
        "steps": CHASE_STEPS if kind == vocab.CUSTOMER_IDLE else REROUTE_STEPS,
    }
    body.update(payload)
    return engine.create_trigger(room_id, body, source=feature.TRIGGER_SOURCE, actor="dana")


def go_live(engine, trigger_id: str) -> dict[str, Any]:
    return engine.go_live(trigger_id, source=feature.GO_LIVE_SOURCE, actor="dana")


# --------------------------------------------------------------------------- #
# Conversations
# --------------------------------------------------------------------------- #


class TestConversations:
    def test_opening_one_records_its_origin_and_room(self, engine, room_id):
        view = open_conversation(engine, room_id, subject="Platform availability")
        assert view["state"] == vocab.STATE_OPEN
        assert view["origin"] == vocab.ORIGIN_INBOX
        assert view["inbox"] == "sales"
        assert view["room_id"] == room_id

    def test_an_api_created_conversation_is_recorded_as_such(self, engine, room_id):
        view = open_conversation(engine, room_id, origin=vocab.ORIGIN_API)
        assert view["origin"] == vocab.ORIGIN_API

    def test_the_create_without_contact_reply_default_is_false(self, engine, room_id):
        """The spec says it "Defaults to false if not provided", so omitting it is False."""

        view = open_conversation(engine, room_id)
        record = engine.store.get(view["id"])
        assert record is not None
        assert record["data"][vocab.CREATE_WITHOUT_CONTACT_REPLY] is False

    def test_the_flag_can_be_set_true_explicitly(self, engine, room_id):
        view = open_conversation(engine, room_id, **{vocab.CREATE_WITHOUT_CONTACT_REPLY: True})
        record = engine.store.get(view["id"])
        assert record is not None
        assert record["data"][vocab.CREATE_WITHOUT_CONTACT_REPLY] is True

    def test_the_string_false_is_not_read_as_true(self, engine, room_id):
        """A client sending "false" as a string must not get a truthy flag forever."""

        view = open_conversation(engine, room_id, **{vocab.CREATE_WITHOUT_CONTACT_REPLY: "false"})
        record = engine.store.get(view["id"])
        assert record is not None
        assert record["data"][vocab.CREATE_WITHOUT_CONTACT_REPLY] is False

    def test_nothing_claims_a_message_left_this_product(self, engine, room_id):
        view = open_conversation(engine, room_id)
        record = engine.store.get(view["id"])
        assert record is not None
        assert record["data"]["sent_by_this_product"] is False

    def test_tags_are_normalised_on_the_way_in(self, engine, room_id):
        view = open_conversation(engine, room_id, tags=["Delayed  Response"])
        assert view["tags"] == [vocab.DELAYED_RESPONSE_TAG]

    def test_a_customer_message_writes_a_part(self, engine, room_id):
        conversation = open_conversation(engine, room_id)
        buyer_says(engine, conversation["id"], 40, "Is the platform available?")
        view = engine.conversation_view(conversation["id"])
        assert len(view["parts"]) == 1
        assert view["parts"][0]["author_kind"] == vocab.AUTHOR_CUSTOMER
        assert view["parts"][0]["body"] == "Is the platform available?"

    def test_parts_are_ordered_oldest_first(self, engine, room_id):
        conversation = open_conversation(engine, room_id)
        for minutes in (40, 30, 20):
            buyer_says(engine, conversation["id"], minutes, f"message {minutes}")
        view = engine.conversation_view(conversation["id"])
        bodies = [part["body"] for part in view["parts"]]
        assert bodies == ["message 40", "message 30", "message 20"]

    def test_a_customer_message_records_the_first_and_the_last(self, engine, room_id):
        conversation = open_conversation(engine, room_id)
        buyer_says(engine, conversation["id"], 40, "first")
        buyer_says(engine, conversation["id"], 20, "third")
        view = engine.conversation_view(conversation["id"])
        assert view["customer_first_message_at"] == rules.stamp(CLOCK - timedelta(minutes=40))
        assert view["customer_last_message_at"] == rules.stamp(CLOCK - timedelta(minutes=20))

    def test_a_customer_message_unsnoozes_a_snoozed_conversation(self, engine, room_id):
        conversation = open_conversation(engine, room_id)
        buyer_says(engine, conversation["id"], 40)
        engine.snooze(conversation["id"], {}, source=feature.SNOOZE_SOURCE, actor="dana")
        assert engine.conversation_view(conversation["id"])["state"] == vocab.STATE_SNOOZED

        buyer_says(engine, conversation["id"], 35)
        assert engine.conversation_view(conversation["id"])["state"] == vocab.STATE_OPEN

    def test_a_closed_conversation_refuses_a_message(self, engine, room_id):
        conversation = open_conversation(engine, room_id)
        engine.close(conversation["id"], {}, source=feature.CLOSE_SOURCE, actor="dana")
        with pytest.raises(ChaseRefusal) as caught:
            engine.add_message(conversation["id"], {"body": "hello"}, source=MESSAGE_SOURCE)
        assert caught.value.code == "conversation_closed"
        assert caught.value.status == 409

    def test_a_closed_conversation_cannot_be_snoozed(self, engine, room_id):
        conversation = open_conversation(engine, room_id)
        engine.close(conversation["id"], {}, source=feature.CLOSE_SOURCE, actor="dana")
        with pytest.raises(ChaseRefusal) as caught:
            engine.snooze(conversation["id"], {}, source=feature.SNOOZE_SOURCE)
        assert caught.value.code == "conversation_closed"

    def test_closing_is_idempotent(self, engine, room_id):
        conversation = open_conversation(engine, room_id)
        first = engine.close(conversation["id"], {}, source=feature.CLOSE_SOURCE, actor="dana")
        second = engine.close(conversation["id"], {}, source=feature.CLOSE_SOURCE, actor="dana")
        assert first["state"] == second["state"] == vocab.STATE_CLOSED
        closes = [
            row
            for row in engine.activity(first["room_id"], conversation["id"])
            if row["code"] == "conversation_closed"
        ]
        assert len(closes) == 1

    def test_a_tag_on_a_closed_conversation_is_legal(self, engine, room_id):
        """Step 5 puts the Tag *after* the Close, so refusing would make it unreachable."""

        conversation = open_conversation(engine, room_id)
        engine.close(conversation["id"], {}, source=feature.CLOSE_SOURCE, actor="dana")
        tagged = engine.tag(conversation["id"], {"tag": "no reply"}, source=feature.TRIGGER_SOURCE)
        assert tagged["tags"] == ["no reply"]

    def test_tagging_twice_is_idempotent(self, engine, room_id):
        conversation = open_conversation(engine, room_id)
        engine.tag(conversation["id"], {"tag": "no reply"}, source=feature.TRIGGER_SOURCE)
        tagged = engine.tag(conversation["id"], {"tag": "no reply"}, source=feature.TRIGGER_SOURCE)
        assert tagged["tags"] == ["no reply"]

    def test_a_conversation_with_no_message_cannot_be_found_by_its_anchors(self, engine, room_id):
        conversation = open_conversation(engine, room_id)
        view = engine.conversation_view(conversation["id"])
        assert view["customer_first_message_at"] is None
        assert view["last_activity_at"] is None


class TestReroute:
    def test_rerouting_records_both_inboxes(self, engine, room_id):
        conversation = open_conversation(engine, room_id)
        moved = engine.reroute(
            conversation["id"],
            {"inbox": "escalations"},
            source=feature.REROUTE_SOURCE,
            actor="dana",
            known_inboxes=feature.KNOWN_INBOXES,
        )
        assert moved["inbox"] == "escalations"
        assert moved["previous_inbox"] == "sales"

    def test_rerouting_to_the_same_inbox_is_refused(self, engine, room_id):
        conversation = open_conversation(engine, room_id)
        with pytest.raises(ChaseRefusal) as caught:
            engine.reroute(
                conversation["id"],
                {"inbox": "sales"},
                source=feature.REROUTE_SOURCE,
                known_inboxes=feature.KNOWN_INBOXES,
            )
        assert caught.value.code == "reroute_to_same_inbox"
        assert caught.value.status == 409

    def test_an_unknown_inbox_is_refused(self, engine, room_id):
        conversation = open_conversation(engine, room_id)
        with pytest.raises(ChaseRefusal) as caught:
            engine.reroute(
                conversation["id"],
                {"inbox": "carrier_pigeon"},
                source=feature.REROUTE_SOURCE,
                known_inboxes=feature.KNOWN_INBOXES,
            )
        assert caught.value.code == "inbox_unknown"

    def test_rerouting_a_closed_conversation_is_refused(self, engine, room_id):
        conversation = open_conversation(engine, room_id)
        engine.close(conversation["id"], {}, source=feature.CLOSE_SOURCE, actor="dana")
        with pytest.raises(ChaseRefusal) as caught:
            engine.reroute(
                conversation["id"],
                {"inbox": "escalations"},
                source=feature.REROUTE_SOURCE,
                known_inboxes=feature.KNOWN_INBOXES,
            )
        assert caught.value.code == "conversation_closed"

    def test_marking_priority(self, engine, room_id):
        conversation = open_conversation(engine, room_id)
        marked = engine.mark_priority(conversation["id"], source=feature.REROUTE_SOURCE)
        assert marked["priority"] is True

    def test_the_reroute_activity_records_both_inboxes(self, engine, room_id):
        conversation = open_conversation(engine, room_id)
        engine.reroute(
            conversation["id"],
            {"inbox": "escalations"},
            source=feature.REROUTE_SOURCE,
            known_inboxes=feature.KNOWN_INBOXES,
        )
        events = [
            row for row in engine.activity(room_id, conversation["id"]) if row["code"] == "rerouted"
        ]
        assert len(events) == 1
        assert events[0]["detail"]["from"] == "sales"
        assert events[0]["detail"]["to"] == "escalations"
        assert events[0]["detail"]["sent_by_this_product"] is False


# --------------------------------------------------------------------------- #
# Triggers
# --------------------------------------------------------------------------- #


class TestTriggers:
    def test_a_new_trigger_is_a_draft(self, engine, room_id):
        trigger = make_trigger(engine, room_id, vocab.CUSTOMER_IDLE)
        assert trigger["live"] is False

    def test_a_draft_trigger_fires_nothing(self, engine, room_id):
        conversation = open_conversation(engine, room_id)
        buyer_says(engine, conversation["id"], 40)
        make_trigger(engine, room_id, vocab.CUSTOMER_IDLE)
        later = at_engine(engine.store, 20)
        sweep = later.evaluate(room_id, {}, source=SOURCE, actor="scheduler")
        assert sweep["fired_count"] == 0
        assert {row["reason"] for row in sweep["skipped"]} == {vocab.SKIP_TRIGGER_NOT_LIVE}

    def test_going_live_flips_the_switch(self, engine, room_id):
        trigger = make_trigger(engine, room_id, vocab.CUSTOMER_IDLE)
        assert go_live(engine, trigger["id"])["live"] is True

    def test_going_live_twice_is_refused(self, engine, room_id):
        trigger = make_trigger(engine, room_id, vocab.CUSTOMER_IDLE)
        go_live(engine, trigger["id"])
        with pytest.raises(ChaseRefusal) as caught:
            go_live(engine, trigger["id"])
        assert caught.value.code == "trigger_already_live"
        assert caught.value.status == 409

    def test_a_live_trigger_carries_its_anchor(self, engine, room_id):
        customer = make_trigger(engine, room_id, vocab.CUSTOMER_IDLE)
        teammate = make_trigger(engine, room_id, vocab.TEAMMATE_IDLE)
        assert customer["anchor"] == vocab.ANCHOR_LAST_ACTIVITY
        assert teammate["anchor"] == vocab.ANCHOR_FIRST_CUSTOMER_MESSAGE

    def test_the_anchor_label_is_the_researched_sentence(self, engine, room_id):
        teammate = make_trigger(engine, room_id, vocab.TEAMMATE_IDLE)
        assert teammate["anchor_label"] == "Customer's first message"

    def test_the_trigger_reports_who_owns_the_close(self, engine, room_id):
        with_wait = make_trigger(engine, room_id, vocab.CUSTOMER_IDLE)
        assert with_wait["close_authority"]["authority"] == "workflow"

    def test_a_trigger_with_no_wait_falls_back_to_the_global_setting(self, engine, room_id):
        plain = make_trigger(
            engine, room_id, vocab.CUSTOMER_IDLE, steps=[{"kind": vocab.STEP_MESSAGE}]
        )
        assert plain["close_authority"]["authority"] == "none"
        assert plain["close_authority"]["workflow_owns_close"] is False

    def test_the_precedence_quote_travels_with_the_authority(self, engine, room_id):
        trigger = make_trigger(engine, room_id, vocab.CUSTOMER_IDLE)
        assert trigger["close_authority"]["quote"] == vocab.WAIT_PRECEDENCE

    def test_an_out_of_range_duration_is_refused_on_create(self, engine, room_id):
        with pytest.raises(ChaseRefusal) as caught:
            make_trigger(engine, room_id, vocab.CUSTOMER_IDLE, duration_seconds=30)
        assert caught.value.code == "duration_out_of_range"

    def test_a_duration_out_of_range_on_edit_is_refused(self, engine, room_id):
        trigger = make_trigger(engine, room_id, vocab.CUSTOMER_IDLE)
        with pytest.raises(ChaseRefusal) as caught:
            engine.update_trigger(
                trigger["id"],
                {"duration_seconds": 14 * 24 * 60 * 60},
                source=feature.TRIGGER_PATCH_SOURCE,
            )
        assert caught.value.code == "duration_out_of_range"

    def test_a_legal_duration_on_edit_is_accepted(self, engine, room_id):
        trigger = make_trigger(engine, room_id, vocab.CUSTOMER_IDLE)
        edited = engine.update_trigger(
            trigger["id"], {"duration_seconds": 3600}, source=feature.TRIGGER_PATCH_SOURCE
        )
        assert edited["duration_seconds"] == 3600

    def test_editing_the_steps_revalidates_them(self, engine, room_id):
        trigger = make_trigger(engine, room_id, vocab.CUSTOMER_IDLE)
        with pytest.raises(ChaseRefusal) as caught:
            engine.update_trigger(
                trigger["id"],
                {"steps": [{"kind": vocab.STEP_WAIT, "duration_seconds": 5}]},
                source=feature.TRIGGER_PATCH_SOURCE,
            )
        assert caught.value.code == "duration_out_of_range"

    def test_editing_the_live_switch_goes_live(self, engine, room_id):
        trigger = make_trigger(engine, room_id, vocab.CUSTOMER_IDLE)
        edited = engine.update_trigger(
            trigger["id"], {"live": True}, source=feature.TRIGGER_PATCH_SOURCE
        )
        assert edited["live"] is True

    def test_channels_are_stored_in_vocabulary_order(self, engine, room_id):
        trigger = make_trigger(engine, room_id, vocab.CUSTOMER_IDLE, channels=["api", "messenger"])
        assert trigger["channels"] == ["messenger", "api"]

    def test_the_trigger_fields_are_stored(self, engine, room_id):
        trigger = make_trigger(
            engine,
            room_id,
            vocab.CUSTOMER_IDLE,
            audience="Buyers who asked a question",
            scheduling={"timezone": "UTC"},
            goal="Chase a buyer who has gone quiet",
        )
        assert trigger["audience"] == "Buyers who asked a question"
        assert trigger["scheduling"] == {"timezone": "UTC"}
        assert trigger["goal"] == "Chase a buyer who has gone quiet"

    def test_a_missing_duration_takes_the_researched_default(self, engine, room_id):
        trigger = make_trigger(engine, room_id, vocab.CUSTOMER_IDLE)
        assert trigger["duration_seconds"] == vocab.DEFAULT_TRIGGER_SECONDS

    def test_deleting_a_trigger_is_soft(self, engine, room_id, store: RecordStore):
        trigger = make_trigger(engine, room_id, vocab.CUSTOMER_IDLE)
        deleted = engine.delete_trigger(trigger["id"], source=feature.TRIGGER_DELETE_SOURCE)
        assert deleted["hard"] is False
        assert store.get(trigger["id"]) is None
        with pytest.raises(rules.TriggerNotFound):
            engine.trigger_view(trigger["id"])

    def test_a_deleted_trigger_stops_firing_but_its_runs_survive(self, engine, room_id):
        conversation = open_conversation(engine, room_id)
        buyer_says(engine, conversation["id"], 40)
        trigger = make_trigger(engine, room_id, vocab.CUSTOMER_IDLE)
        go_live(engine, trigger["id"])
        later = at_engine(engine.store, 20)
        assert later.evaluate(room_id, {}, source=SOURCE)["fired_count"] == 1

        engine.delete_trigger(trigger["id"], source=feature.TRIGGER_DELETE_SOURCE)
        again = at_engine(engine.store, 21)
        assert again.evaluate(room_id, {}, source=SOURCE)["fired_count"] == 0
        # The run that fired before the delete is still on the record.
        assert len(engine.runs(room_id)) == 1


# --------------------------------------------------------------------------- #
# The sweep
# --------------------------------------------------------------------------- #


class TestSweep:
    def test_a_quiet_conversation_fires_the_trigger(self, engine, room_id):
        conversation = open_conversation(engine, room_id)
        buyer_says(engine, conversation["id"], 40)
        trigger = make_trigger(engine, room_id, vocab.CUSTOMER_IDLE)
        go_live(engine, trigger["id"])

        later = at_engine(engine.store, 20)
        sweep = later.evaluate(room_id, {}, source=SOURCE, actor="scheduler")
        assert sweep["fired_count"] == 1
        assert sweep["fired"][0]["conversation_id"] == conversation["id"]

    def test_a_recent_conversation_is_not_due_and_says_how_long_is_left(self, engine, room_id):
        conversation = open_conversation(engine, room_id)
        buyer_says(engine, conversation["id"], 2)
        make_trigger(engine, room_id, vocab.CUSTOMER_IDLE)
        go_live(engine, engine.triggers(room_id)[0]["id"])

        later = at_engine(engine.store, 0)
        sweep = later.evaluate(room_id, {}, source=SOURCE)
        assert sweep["fired_count"] == 0
        skipped = sweep["skipped"][0]
        assert skipped["reason"] == vocab.SKIP_NOT_ELIGIBLE
        assert skipped["seconds_remaining"] > 0

    def test_a_conversation_with_no_customer_message_is_skipped(self, engine, room_id):
        open_conversation(engine, room_id)
        trigger = make_trigger(engine, room_id, vocab.CUSTOMER_IDLE)
        go_live(engine, trigger["id"])
        later = at_engine(engine.store, 20)
        sweep = later.evaluate(room_id, {}, source=SOURCE)
        assert {row["reason"] for row in sweep["skipped"]} == {vocab.SKIP_NO_ANCHOR_MESSAGE}

    def test_an_api_created_conversation_is_skipped_with_its_own_code(self, engine, room_id):
        conversation = open_conversation(engine, room_id, origin=vocab.ORIGIN_API)
        buyer_says(engine, conversation["id"], 40)
        trigger = make_trigger(engine, room_id, vocab.CUSTOMER_IDLE)
        go_live(engine, trigger["id"])

        later = at_engine(engine.store, 20)
        sweep = later.evaluate(room_id, {}, source=SOURCE)
        skipped = [row for row in sweep["skipped"] if row["conversation_id"] == conversation["id"]]
        assert skipped
        assert {row["reason"] for row in skipped} == {vocab.SKIP_API_CREATED}
        assert skipped[0]["is_specification_rule"] is True

    def test_a_snoozed_conversation_is_skipped_with_its_own_code(self, engine, room_id):
        conversation = open_conversation(engine, room_id)
        buyer_says(engine, conversation["id"], 40)
        engine.snooze(conversation["id"], {}, source=feature.SNOOZE_SOURCE)
        trigger = make_trigger(engine, room_id, vocab.CUSTOMER_IDLE)
        go_live(engine, trigger["id"])

        later = at_engine(engine.store, 20)
        sweep = later.evaluate(room_id, {}, source=SOURCE)
        skipped = [row for row in sweep["skipped"] if row["conversation_id"] == conversation["id"]]
        assert {row["reason"] for row in skipped} == {vocab.SKIP_SNOOZED}

    def test_a_room_with_no_trigger_reports_that(self, engine, room_id):
        conversation = open_conversation(engine, room_id)
        buyer_says(engine, conversation["id"], 1)
        sweep = engine.evaluate(room_id, {}, source=SOURCE)
        assert sweep["fired_count"] == 0
        assert {row["reason"] for row in sweep["skipped"]} == {vocab.SKIP_NO_TRIGGER}

    def test_a_kind_filter_narrows_the_sweep(self, engine, room_id):
        conversation = open_conversation(engine, room_id)
        buyer_says(engine, conversation["id"], 40)
        customer_trigger = make_trigger(engine, room_id, vocab.CUSTOMER_IDLE)
        go_live(engine, customer_trigger["id"])

        later = at_engine(engine.store, 20)
        sweep = later.evaluate(room_id, {"kind": vocab.TEAMMATE_IDLE}, source=SOURCE)
        assert sweep["fired_count"] == 0
        assert sweep["skipped"][0]["reason"] == vocab.SKIP_NO_TRIGGER

    def test_a_conversation_filter_narrows_the_sweep(self, engine, room_id):
        first = open_conversation(engine, room_id)
        second = open_conversation(engine, room_id)
        buyer_says(engine, first["id"], 40)
        buyer_says(engine, second["id"], 40)
        trigger = make_trigger(engine, room_id, vocab.CUSTOMER_IDLE)
        go_live(engine, trigger["id"])

        later = at_engine(engine.store, 20)
        sweep = later.evaluate(room_id, {"conversation_id": first["id"]}, source=SOURCE)
        assert sweep["conversations_considered"] == 1
        assert sweep["fired"][0]["conversation_id"] == first["id"]

    def test_a_trigger_filter_separates_two_triggers_of_one_kind(self, engine, room_id):
        """The once-per-message limit is per trigger, so both fire and the filter is how
        a caller tells them apart."""

        conversation = open_conversation(engine, room_id)
        buyer_says(engine, conversation["id"], 40)
        first = make_trigger(engine, room_id, vocab.CUSTOMER_IDLE)
        second = make_trigger(engine, room_id, vocab.CUSTOMER_IDLE, duration_seconds=1800)
        go_live(engine, first["id"])
        go_live(engine, second["id"])

        later = at_engine(engine.store, 20)
        # Scoped to one trigger, the sweep fires only that one. Asked first, because the
        # token is spent by whichever sweep fires it.
        scoped = at_engine(engine.store, 20).evaluate(
            room_id, {"trigger_id": first["id"]}, source=SOURCE
        )
        assert scoped["fired_count"] == 1
        assert scoped["fired"][0]["trigger_id"] == first["id"]

        # Unscoped, both fire: the limit is per trigger, so the second is unspent.
        unscoped = later.evaluate(room_id, {}, source=SOURCE)
        assert unscoped["fired_count"] == 1
        assert unscoped["fired"][0]["trigger_id"] == second["id"]
        assert {row["reason"] for row in unscoped["skipped"]} == {vocab.SKIP_ALREADY_FIRED}

        # And once both tokens are spent, neither fires again.
        again = at_engine(engine.store, 20).evaluate(room_id, {}, source=SOURCE)
        assert again["fired_count"] == 0

    def test_the_due_count_is_reported_for_the_next_call(self, engine, room_id):
        conversation = open_conversation(engine, room_id)
        buyer_says(engine, conversation["id"], 40)
        trigger = make_trigger(engine, room_id, vocab.CUSTOMER_IDLE)
        go_live(engine, trigger["id"])

        # The trigger is spent now, so this sweep fires and reports nothing due next.
        after = at_engine(engine.store, 20)
        first = after.evaluate(room_id, {}, source=SOURCE)
        assert first["fired_count"] == 1
        assert first["due_next"] == 0

    def test_the_due_count_counts_before_anything_fires(self, engine, room_id):
        """The honest consequence of the explicit-route decision: a GET can say a sweep is
        due without claiming anything fired."""

        conversation = open_conversation(engine, room_id)
        buyer_says(engine, conversation["id"], 40)
        trigger = make_trigger(engine, room_id, vocab.CUSTOMER_IDLE)
        go_live(engine, trigger["id"])
        assert at_engine(engine.store, 20).due_count(room_id) == 1

    def test_the_due_count_is_zero_without_a_live_trigger(self, engine, room_id):
        conversation = open_conversation(engine, room_id)
        buyer_says(engine, conversation["id"], 40)
        make_trigger(engine, room_id, vocab.CUSTOMER_IDLE)
        assert at_engine(engine.store, 20).due_count(room_id) == 0


class TestOncePerMessageThroughTheSweep:
    """The researched limit, through the real sweep rather than the pure rule."""

    def test_a_second_evaluation_does_not_fire_again(self, engine, room_id):
        conversation = open_conversation(engine, room_id)
        buyer_says(engine, conversation["id"], 40)
        trigger = make_trigger(engine, room_id, vocab.CUSTOMER_IDLE)
        go_live(engine, trigger["id"])

        first = at_engine(engine.store, 20).evaluate(room_id, {}, source=SOURCE)
        assert first["fired_count"] == 1

        second = at_engine(engine.store, 20).evaluate(room_id, {}, source=SOURCE)
        assert second["fired_count"] == 0
        assert {row["reason"] for row in second["skipped"]} == {vocab.SKIP_ALREADY_FIRED}
        assert second["skipped"][0]["is_specification_rule"] is True

    def test_a_second_customer_message_re_arms_exactly_once(self, engine, room_id):
        conversation = open_conversation(engine, room_id)
        buyer_says(engine, conversation["id"], 40)
        trigger = make_trigger(engine, room_id, vocab.CUSTOMER_IDLE)
        go_live(engine, trigger["id"])

        assert at_engine(engine.store, 20).evaluate(room_id, {}, source=SOURCE)["fired_count"] == 1
        assert at_engine(engine.store, 21).evaluate(room_id, {}, source=SOURCE)["fired_count"] == 0

        # A new customer message mints a new token.
        buyer_says(engine, conversation["id"], 10, "are you still there")

        assert at_engine(engine.store, 25).evaluate(room_id, {}, source=SOURCE)["fired_count"] == 1
        assert at_engine(engine.store, 26).evaluate(room_id, {}, source=SOURCE)["fired_count"] == 0

    def test_three_messages_in_a_row_re_arm_three_times_but_only_once_each(self, engine, room_id):
        """The researched burst, read through the sweep: the teammate trigger stays on the
        first message, and each customer message re-arms the customer trigger."""

        conversation = open_conversation(engine, room_id)
        for minutes in (40, 35, 30):
            buyer_says(engine, conversation["id"], minutes, f"message at {minutes}")

        teammate = make_trigger(engine, room_id, vocab.TEAMMATE_IDLE, duration_seconds=900)
        go_live(engine, teammate["id"])

        # At -15 the burst is fifteen minutes past the teammate window measured from -40.
        sweep = at_engine(engine.store, 25).evaluate(
            room_id, {"kind": vocab.TEAMMATE_IDLE}, source=SOURCE
        )
        assert sweep["fired_count"] == 1
        fired = sweep["fired"][0]
        assert fired["anchor_kind"] == vocab.ANCHOR_FIRST_CUSTOMER_MESSAGE
        # The first message, not the third: -40 minutes from CLOCK, at -15 that is 25 minutes.
        assert fired["anchor"] == rules.stamp(CLOCK - timedelta(minutes=40))

    def test_the_customer_trigger_uses_the_last_of_three(self, engine, room_id):
        conversation = open_conversation(engine, room_id)
        for minutes in (40, 35, 30):
            buyer_says(engine, conversation["id"], minutes, f"message at {minutes}")

        trigger = make_trigger(engine, room_id, vocab.CUSTOMER_IDLE)
        go_live(engine, trigger["id"])
        sweep = at_engine(engine.store, 20).evaluate(room_id, {}, source=SOURCE)
        assert sweep["fired_count"] == 1
        assert sweep["fired"][0]["anchor"] == rules.stamp(CLOCK - timedelta(minutes=30))
        assert sweep["fired"][0]["anchor_kind"] == vocab.ANCHOR_LAST_ACTIVITY


# --------------------------------------------------------------------------- #
# Runs: the researched step sequence
# --------------------------------------------------------------------------- #


class TestChaseRun:
    """Steps 3 to 5: message, wait, closing message, Close, Tag."""

    @pytest.fixture
    def fired(self, engine, room_id):
        conversation = open_conversation(engine, room_id, subject="Platform availability")
        buyer_says(engine, conversation["id"], 40, "Is the platform available?")
        trigger = make_trigger(engine, room_id, vocab.CUSTOMER_IDLE)
        go_live(engine, trigger["id"])
        sweep = at_engine(engine.store, 20).evaluate(room_id, {}, source=SOURCE)
        assert sweep["fired_count"] == 1
        return {"conversation": conversation, "run": sweep["fired"][0], "room": room_id}

    def test_a_firing_writes_a_run_in_the_running_state(self, fired):
        run = fired["run"]
        assert run["state"] == vocab.RUN_RUNNING
        assert run["cursor"] == 0
        assert run["next_step"]["kind"] == vocab.STEP_MESSAGE

    def test_a_firing_writes_the_trigger_activity(self, engine, fired):
        events = [
            row
            for row in engine.activity(fired["room"], fired["conversation"]["id"])
            if row["code"] == "trigger_fired"
        ]
        assert len(events) == 1
        assert events[0]["detail"]["once_per_message"] == vocab.ONCE_PER_MESSAGE

    def test_the_first_advance_writes_the_message(self, engine, fired):
        run = engine.advance(fired["run"]["id"], {}, source=feature.ADVANCE_SOURCE)
        assert run["cursor"] == 1
        parts = engine.conversation_view(fired["conversation"]["id"])["parts"]
        workflow_parts = [p for p in parts if p["author_kind"] == vocab.AUTHOR_SYSTEM]
        assert len(workflow_parts) == 1
        assert "still there" in workflow_parts[0]["body"]

    def test_the_wait_starts_rather_than_being_stepped_over(self, engine, fired):
        engine.advance(fired["run"]["id"], {}, source=feature.ADVANCE_SOURCE)
        run = engine.advance(fired["run"]["id"], {}, source=feature.ADVANCE_SOURCE)
        assert run["state"] == vocab.RUN_WAITING
        assert run["cursor"] == 1

    def test_the_wait_records_the_configured_duration_and_events(self, engine, fired):
        engine.advance(fired["run"]["id"], {}, source=feature.ADVANCE_SOURCE)
        run = engine.advance(fired["run"]["id"], {}, source=feature.ADVANCE_SOURCE)
        held = engine.store.get(run["id"])
        assert held is not None
        step = held["data"]["current_step"]
        assert step["duration_seconds"] == QUARTER_HOUR
        assert step["interruption_events"] == list(vocab.INTERRUPTION_EVENTS)

    def test_advancing_a_waiting_run_is_refused(self, engine, fired):
        engine.advance(fired["run"]["id"], {}, source=feature.ADVANCE_SOURCE)
        engine.advance(fired["run"]["id"], {}, source=feature.ADVANCE_SOURCE)
        with pytest.raises(ChaseRefusal) as caught:
            engine.advance(fired["run"]["id"], {}, source=feature.ADVANCE_SOURCE)
        assert caught.value.code == "run_not_advancing"

    def test_resolving_a_wait_with_time_left_is_refused(self, engine, fired):
        """The wait starts at the sweep instant, so CLOCK + 20 has barely started it."""

        engine.advance(fired["run"]["id"], {}, source=feature.ADVANCE_SOURCE)
        at_engine(engine.store, 20).advance(fired["run"]["id"], {}, source=feature.ADVANCE_SOURCE)
        with pytest.raises(ChaseRefusal) as caught:
            at_engine(engine.store, 25).resolve(
                fired["run"]["id"], {}, source=feature.RESOLVE_SOURCE
            )
        assert caught.value.code == "run_not_waiting"

    def test_a_wait_that_runs_out_resolves(self, engine, fired):
        started = at_engine(engine.store, 20)
        started.advance(fired["run"]["id"], {}, source=feature.ADVANCE_SOURCE)
        started.advance(fired["run"]["id"], {}, source=feature.ADVANCE_SOURCE)
        run = at_engine(engine.store, 20 + 16).resolve(
            fired["run"]["id"], {}, source=feature.RESOLVE_SOURCE
        )
        assert run["cursor"] == 2
        assert run["state"] == vocab.RUN_RUNNING

    def test_the_whole_sequence_closes_and_tags(self, engine, fired):
        started = at_engine(engine.store, 20)
        started.advance(fired["run"]["id"], {}, source=feature.ADVANCE_SOURCE)
        started.advance(fired["run"]["id"], {}, source=feature.ADVANCE_SOURCE)
        later = at_engine(engine.store, 20 + 16)
        later.resolve(fired["run"]["id"], {}, source=feature.RESOLVE_SOURCE)
        for _ in range(3):
            run = later.advance(fired["run"]["id"], {}, source=feature.ADVANCE_SOURCE)

        assert run["state"] == vocab.RUN_FINISHED
        view = engine.conversation_view(fired["conversation"]["id"])
        assert view["state"] == vocab.STATE_CLOSED
        assert view["tags"] == ["no reply"]

    def test_the_sequence_writes_the_events_in_the_researched_order(self, engine, fired):
        started = at_engine(engine.store, 20)
        started.advance(fired["run"]["id"], {}, source=feature.ADVANCE_SOURCE)
        started.advance(fired["run"]["id"], {}, source=feature.ADVANCE_SOURCE)
        later = at_engine(engine.store, 20 + 16)
        later.resolve(fired["run"]["id"], {}, source=feature.RESOLVE_SOURCE)
        for _ in range(3):
            later.advance(fired["run"]["id"], {}, source=feature.ADVANCE_SOURCE)

        codes = [
            row["code"]
            for row in engine.activity(fired["room"], fired["conversation"]["id"])
            if row["code"] != "message_sent"
        ]
        assert codes == [
            "trigger_fired",
            "wait_started",
            "conversation_closed",
            "tagged",
        ]

    def test_advancing_a_finished_run_is_refused(self, engine, fired):
        started = at_engine(engine.store, 20)
        started.advance(fired["run"]["id"], {}, source=feature.ADVANCE_SOURCE)
        started.advance(fired["run"]["id"], {}, source=feature.ADVANCE_SOURCE)
        later = at_engine(engine.store, 20 + 16)
        later.resolve(fired["run"]["id"], {}, source=feature.RESOLVE_SOURCE)
        # Three steps remain after the wait: the closing message, Close and Tag.
        for _ in range(3):
            later.advance(fired["run"]["id"], {}, source=feature.ADVANCE_SOURCE)
        with pytest.raises(ChaseRefusal) as caught:
            later.advance(fired["run"]["id"], {}, source=feature.ADVANCE_SOURCE)
        assert caught.value.code == "run_not_advancing"
        assert caught.value.status == 409

    def test_the_run_view_reports_the_remaining_steps(self, engine, fired):
        run = engine.run_view(fired["run"]["id"])
        assert [step["kind"] for step in run["remaining_steps"]] == [
            vocab.STEP_MESSAGE,
            vocab.STEP_WAIT,
            vocab.STEP_CLOSE_MESSAGE,
            vocab.STEP_CLOSE,
            vocab.STEP_TAG,
        ]
        assert run["sent_by_this_product"] is False


class TestInterruptedRun:
    """A wait cancelled by the buyer answering, which ends the run for good.

    The wait starts when the sweep fires, which is ``CLOCK + 20``. Every message below is
    written *after* that instant -- ``buyer_says`` takes minutes before ``CLOCK``, so a
    negative value is after it -- because a message from before the wait began does not
    interrupt it, and that rule has its own test.
    """

    #: The sweep fires here, and so does the wait begin.
    WAIT_STARTED_AT = 20
    #: A minute inside the quarter-hour wait.
    INSIDE = WAIT_STARTED_AT + 4

    @pytest.fixture
    def waiting(self, engine, room_id):
        conversation = open_conversation(engine, room_id, subject="Pricing for the pilot")
        buyer_says(engine, conversation["id"], 40, "What does a pilot cost?")
        trigger = make_trigger(engine, room_id, vocab.CUSTOMER_IDLE)
        go_live(engine, trigger["id"])
        started = at_engine(engine.store, self.WAIT_STARTED_AT)
        sweep = started.evaluate(room_id, {}, source=SOURCE)
        started.advance(sweep["fired"][0]["id"], {}, source=feature.ADVANCE_SOURCE)
        started.advance(sweep["fired"][0]["id"], {}, source=feature.ADVANCE_SOURCE)
        assert started.run_view(sweep["fired"][0]["id"])["state"] == vocab.RUN_WAITING
        return {
            "conversation": conversation,
            "run_id": sweep["fired"][0]["id"],
            "room": room_id,
        }

    def test_a_customer_message_during_the_wait_interrupts_it(self, engine, waiting):
        buyer_says(engine, waiting["conversation"]["id"], -self.INSIDE, "Sorry, what does it cost?")
        run = at_engine(engine.store, self.INSIDE + 1).resolve(
            waiting["run_id"], {}, source=feature.RESOLVE_SOURCE
        )
        assert run["state"] == vocab.RUN_INTERRUPTED
        assert run["interrupted_by"] == vocab.AUTHOR_CUSTOMER

    def test_an_interrupted_run_never_closes_the_conversation(self, engine, waiting):
        buyer_says(engine, waiting["conversation"]["id"], -self.INSIDE)
        at_engine(engine.store, self.INSIDE + 1).resolve(
            waiting["run_id"], {}, source=feature.RESOLVE_SOURCE
        )
        view = engine.conversation_view(waiting["conversation"]["id"])
        assert view["state"] == vocab.STATE_OPEN
        assert view["tags"] == []

    def test_an_interrupted_run_cannot_be_advanced(self, engine, waiting):
        buyer_says(engine, waiting["conversation"]["id"], -self.INSIDE)
        at_engine(engine.store, self.INSIDE + 1).resolve(
            waiting["run_id"], {}, source=feature.RESOLVE_SOURCE
        )
        with pytest.raises(ChaseRefusal) as caught:
            at_engine(engine.store, self.INSIDE + 2).advance(
                waiting["run_id"], {}, source=feature.ADVANCE_SOURCE
            )
        assert caught.value.code == "run_not_advancing"

    def test_an_interrupted_run_cannot_be_resolved_again(self, engine, waiting):
        buyer_says(engine, waiting["conversation"]["id"], -self.INSIDE)
        at_engine(engine.store, self.INSIDE + 1).resolve(
            waiting["run_id"], {}, source=feature.RESOLVE_SOURCE
        )
        with pytest.raises(ChaseRefusal) as caught:
            at_engine(engine.store, self.WAIT_STARTED_AT + 40).resolve(
                waiting["run_id"], {}, source=feature.RESOLVE_SOURCE
            )
        assert caught.value.code == "run_not_waiting"

    def test_the_interruption_is_written_as_an_event(self, engine, waiting):
        buyer_says(engine, waiting["conversation"]["id"], -self.INSIDE)
        at_engine(engine.store, self.INSIDE + 1).resolve(
            waiting["run_id"], {}, source=feature.RESOLVE_SOURCE
        )
        events = [
            row
            for row in engine.activity(waiting["room"], waiting["conversation"]["id"])
            if row["code"] == "wait_interrupted"
        ]
        assert len(events) == 1
        assert events[0]["detail"]["terminal"] is True

    def test_a_teammate_message_can_interrupt_too(self, engine, waiting):
        engine.add_message(
            waiting["conversation"]["id"],
            {
                "author_kind": vocab.AUTHOR_TEAMMATE,
                "at": rules.stamp(CLOCK + timedelta(minutes=self.INSIDE)),
            },
            source=MESSAGE_SOURCE,
        )
        run = at_engine(engine.store, self.INSIDE + 1).resolve(
            waiting["run_id"], {}, source=feature.RESOLVE_SOURCE
        )
        assert run["state"] == vocab.RUN_INTERRUPTED
        assert run["interrupted_by"] == vocab.AUTHOR_TEAMMATE

    def test_a_message_before_the_wait_started_does_not_interrupt(self, engine, waiting):
        """A buyer who wrote an hour ago has not interrupted a wait that began since."""

        run = at_engine(engine.store, self.WAIT_STARTED_AT + 16).resolve(
            waiting["run_id"], {}, source=feature.RESOLVE_SOURCE
        )
        assert run["state"] == vocab.RUN_RUNNING
        assert run["cursor"] == 2

    def test_the_workflow_own_message_does_not_interrupt(self, engine, waiting):
        """The reason a step 3 message does not stop the step 4 wait it precedes."""

        engine.add_message(
            waiting["conversation"]["id"],
            {
                "author_kind": vocab.AUTHOR_SYSTEM,
                "at": rules.stamp(CLOCK + timedelta(minutes=self.INSIDE)),
                "body": "chasing you",
            },
            source=MESSAGE_SOURCE,
        )
        # Still interrupted-or-not is decided by the customer message that opened the
        # fixture; with only the workflow's own part, the wait is untouched.
        with pytest.raises(ChaseRefusal) as caught:
            at_engine(engine.store, self.INSIDE + 1).resolve(
                waiting["run_id"], {}, source=feature.RESOLVE_SOURCE
            )
        assert caught.value.code == "run_not_waiting"
        assert "has not been interrupted" in caught.value.detail


class TestRerouteRun:
    """Step 6: expected reply time, priority, the tag, the assign."""

    @pytest.fixture
    def fired(self, engine, room_id):
        conversation = open_conversation(engine, room_id, subject="Security review")
        for index, minutes in enumerate((30, 25, 20)):
            buyer_says(engine, conversation["id"], minutes, f"Security question {index + 1}")
        trigger = make_trigger(engine, room_id, vocab.TEAMMATE_IDLE)
        go_live(engine, trigger["id"])
        sweep = at_engine(engine.store, 0).evaluate(room_id, {}, source=SOURCE)
        return {"conversation": conversation, "run": sweep["fired"][0], "room": room_id}

    def test_the_teammate_trigger_fires_on_the_first_of_three_messages(self, fired):
        assert fired["run"]["anchor_kind"] == vocab.ANCHOR_FIRST_CUSTOMER_MESSAGE
        assert fired["run"]["anchor"] == rules.stamp(CLOCK - timedelta(minutes=30))

    def test_the_first_step_shows_the_expected_reply_time(self, engine, fired):
        engine.advance(
            fired["run"]["id"],
            {},
            source=feature.ADVANCE_SOURCE,
            known_inboxes=feature.KNOWN_INBOXES,
        )
        events = [
            row
            for row in engine.activity(fired["room"], fired["conversation"]["id"])
            if row["code"] == "expected_reply_time"
        ]
        assert len(events) == 1
        assert events[0]["detail"]["duration_seconds"] == QUARTER_HOUR
        assert events[0]["detail"]["expected_reply_time"]

    def test_the_expected_reply_time_uses_the_rooms_office_hours(self, engine, fired, room_id):
        engine.save_office_hours(room_id, {}, source=feature.OFFICE_HOURS_SOURCE, actor="dana")
        computed = engine.expected_reply_time(room_id, CLOCK - timedelta(minutes=30), QUARTER_HOUR)
        assert computed["expected_reply_time"]
        assert computed["schedule"]["monday"] == {"open": 540, "close": 1080}

    def test_the_second_step_marks_priority(self, engine, fired):
        for _ in range(2):
            engine.advance(
                fired["run"]["id"],
                {},
                source=feature.ADVANCE_SOURCE,
                known_inboxes=feature.KNOWN_INBOXES,
            )
        assert engine.conversation_view(fired["conversation"]["id"])["priority"] is True

    def test_the_third_step_writes_the_delayed_response_tag(self, engine, fired):
        for _ in range(3):
            engine.advance(
                fired["run"]["id"],
                {},
                source=feature.ADVANCE_SOURCE,
                known_inboxes=feature.KNOWN_INBOXES,
            )
        assert engine.conversation_view(fired["conversation"]["id"])["tags"] == [
            vocab.DELAYED_RESPONSE_TAG
        ]

    def test_the_fourth_step_reroutes_to_the_named_inbox(self, engine, fired):
        for _ in range(4):
            engine.advance(
                fired["run"]["id"],
                {},
                source=feature.ADVANCE_SOURCE,
                known_inboxes=feature.KNOWN_INBOXES,
            )
        view = engine.conversation_view(fired["conversation"]["id"])
        assert view["inbox"] == "escalations"
        assert view["previous_inbox"] == "sales"

    def test_the_whole_reroute_sequence_finishes(self, engine, fired):
        for _ in range(4):
            run = engine.advance(
                fired["run"]["id"],
                {},
                source=feature.ADVANCE_SOURCE,
                known_inboxes=feature.KNOWN_INBOXES,
            )
        assert run["state"] == vocab.RUN_FINISHED

    def test_a_step_naming_an_unknown_inbox_is_refused_mid_run(self, engine, room_id):
        conversation = open_conversation(engine, room_id)
        buyer_says(engine, conversation["id"], 30)
        trigger = make_trigger(
            engine,
            room_id,
            vocab.TEAMMATE_IDLE,
            steps=[{"kind": vocab.STEP_ASSIGN, "inbox": "carrier_pigeon"}],
        )
        go_live(engine, trigger["id"])
        sweep = at_engine(engine.store, 0).evaluate(room_id, {}, source=SOURCE)
        with pytest.raises(ChaseRefusal) as caught:
            engine.advance(
                sweep["fired"][0]["id"],
                {},
                source=feature.ADVANCE_SOURCE,
                known_inboxes=feature.KNOWN_INBOXES,
            )
        assert caught.value.code == "inbox_unknown"


# --------------------------------------------------------------------------- #
# Office hours over HTTP-facing methods
# --------------------------------------------------------------------------- #


class TestOfficeHoursThroughTheEngine:
    def test_a_room_with_no_schedule_answers_with_the_derived_default(self, engine, room_id):
        hours = engine.office_hours(room_id)
        assert hours["stored"] is False
        assert hours["derived"] is True
        assert hours["schedule"]["monday"] == {"open": 540, "close": 1080}
        assert hours["derivation"] == vocab.OFFICE_HOURS_QUOTE

    def test_saving_a_schedule_marks_it_stored(self, engine, room_id):
        saved = engine.save_office_hours(
            room_id, {"schedule": rules.default_office_hours()}, source=feature.OFFICE_HOURS_SOURCE
        )
        assert saved["stored"] is True
        assert saved["derived"] is False
        assert engine.office_hours(room_id)["stored"] is True

    def test_saving_twice_updates_rather_than_duplicating(self, engine, room_id):
        engine.save_office_hours(room_id, {}, source=feature.OFFICE_HOURS_SOURCE)
        engine.save_office_hours(
            room_id,
            {"schedule": {day: {"open": "10:00", "close": "16:00"} for day in vocab.WEEKDAYS}},
            source=feature.OFFICE_HOURS_SOURCE,
        )
        hours = engine.office_hours(room_id)
        assert hours["schedule"]["monday"] == {"open": 600, "close": 960}

    def test_an_unusable_schedule_is_refused_before_the_write(self, engine, room_id):
        with pytest.raises(ChaseRefusal) as caught:
            engine.save_office_hours(
                room_id,
                {"schedule": {"monday": {"open": "18:00", "close": "09:00"}}},
                source=feature.OFFICE_HOURS_SOURCE,
            )
        assert caught.value.code == "invalid_office_hours"
        assert engine.office_hours(room_id)["stored"] is False

    def test_the_expected_reply_time_reports_the_office_minutes(self, engine, room_id):
        result = engine.expected_reply_time(room_id, _monday_at(17, 50), QUARTER_HOUR)
        assert result["expected_reply_time"].endswith("T09:05:00.000+00:00")

    def test_the_anchor_is_echoed_back(self, engine, room_id):
        anchor = _monday_at(9, 0)
        result = engine.expected_reply_time(room_id, anchor, QUARTER_HOUR)
        assert result["anchor"] == rules.stamp(anchor)


class TestSummary:
    def test_it_counts_by_state(self, engine, room_id):
        first = open_conversation(engine, room_id)
        second = open_conversation(engine, room_id)
        engine.close(first["id"], {}, source=feature.CLOSE_SOURCE)
        engine.snooze(second["id"], {}, source=feature.SNOOZE_SOURCE)
        summary = engine.summary(room_id)
        assert summary["by_state"][vocab.STATE_CLOSED] == 1
        assert summary["by_state"][vocab.STATE_SNOOZED] == 1
        assert summary["by_state"][vocab.STATE_OPEN] == 0

    def test_it_counts_api_created_conversations_separately(self, engine, room_id):
        open_conversation(engine, room_id, origin=vocab.ORIGIN_API)
        assert engine.summary(room_id)["api_created"] == 1

    def test_it_counts_live_and_draft_triggers(self, engine, room_id):
        first = make_trigger(engine, room_id, vocab.CUSTOMER_IDLE)
        make_trigger(engine, room_id, vocab.TEAMMATE_IDLE)
        go_live(engine, first["id"])
        summary = engine.summary(room_id)
        assert summary["triggers"] == 2
        assert summary["live_triggers"] == 1

    def test_it_counts_runs_by_state(self, engine, room_id):
        conversation = open_conversation(engine, room_id)
        buyer_says(engine, conversation["id"], 40)
        trigger = make_trigger(engine, room_id, vocab.CUSTOMER_IDLE)
        go_live(engine, trigger["id"])
        at_engine(engine.store, 20).evaluate(room_id, {}, source=SOURCE)
        summary = engine.summary(room_id)
        assert summary["runs"] == 1
        assert summary["runs_by_state"][vocab.RUN_RUNNING] == 1

    def test_an_empty_room_summarises_to_zeroes(self, engine, room_id):
        summary = engine.summary(room_id)
        assert summary["conversations"] == 0
        assert summary["triggers"] == 0
        assert summary["due"] == 0

    def test_it_carries_the_office_hours_alongside(self, engine, room_id):
        assert engine.summary(room_id)["office_hours"]["schedule"]["monday"] == {
            "open": 540,
            "close": 1080,
        }


# --------------------------------------------------------------------------- #
# The HTTP surface
# --------------------------------------------------------------------------- #


class TestFeatureRegistration:
    def test_the_feature_exports_its_ticket_and_name(self):
        assert feature.FEATURE["ticket"] == "WF-107"
        assert feature.FEATURE["name"]

    def test_the_prefix_is_ticket_derived_and_under_api(self):
        assert feature.router.prefix == "/api/wf-107"

    def test_every_route_is_room_scoped_except_the_three_published_ones(self):
        unscoped = {
            route.path
            for route in feature.router.routes
            if isinstance(route, APIRoute)
            and "{room_id}" not in route.path
            and route.path
            not in {
                f"{PREFIX}/vocabulary",
                f"{PREFIX}/inferences",
                f"{PREFIX}/decisions/{{decision_id}}",
            }
        }
        assert unscoped == set()

    def test_it_registers_a_handler_for_each_of_its_own_error_types(self):
        assert feature.EXCEPTION_HANDLERS[feature.ChaseRefusal] is feature._chase_refusal
        assert len(feature.EXCEPTION_HANDLERS) == 4

    def test_it_maps_no_shared_error_type(self):
        """A handler for ``ValueError`` would intercept that exception product-wide."""

        for mapped in feature.EXCEPTION_HANDLERS:
            assert mapped.__module__.startswith("dsr.conversation_chase")


class TestVocabularyRoute:
    def test_it_serves_the_published_vocabulary(self, client, db: AuditedDatabase):
        store = RecordStore(db)
        room_id = store.create("room", {"name": "R"}, actor="dana")["id"]
        body = client.get(f"{PREFIX}/vocabulary").json()
        assert body["trigger_kinds"] == list(vocab.TRIGGER_KINDS)
        assert body["bounds"]["min_duration_seconds"] == 30
        assert body["error_codes"]["duration_out_of_range"]["status"] == 422
        assert room_id  # the fixture is what put a store on the app

    def test_it_needs_no_room(self, client):
        assert client.get(f"{PREFIX}/vocabulary").status_code == 200


class TestInferencesRoute:
    def test_it_serves_every_recorded_decision(self, client):
        body = client.get(f"{PREFIX}/inferences").json()
        assert body["ticket"] == "WF-107"
        assert body["count"] >= 8
        for entry in body["decisions"]:
            assert entry["question"]
            assert entry["chosen"]
            assert entry["rejected"]
            assert entry["consequence"]

    def test_it_names_the_jev_audits_that_chose_the_mechanism(self, client):
        body = client.get(f"{PREFIX}/inferences").json()
        assert set(body["jev_audits"]) >= {
            "domain_package_placement",
            "job_driver",
            "api_created_conversation",
            "trigger_anchor",
        }
        for audit_id in body["jev_audits"].values():
            assert audit_id.startswith("jev-")

    def test_it_flags_the_office_hours_model_as_unsourced(self, client):
        body = client.get(f"{PREFIX}/inferences").json()
        assert "office-hours-model" in body["unsourced"]

    def test_one_decision_can_be_read_by_id(self, client):
        body = client.get(f"{PREFIX}/decisions/job-driver").json()
        assert body["id"] == "job-driver"
        assert "explicit POST" in body["chosen"] or "POST" in body["chosen"]

    def test_an_unknown_decision_id_is_a_404_shape(self, client):
        response = client.get(f"{PREFIX}/decisions/no-such-decision")
        assert response.status_code == 404
        assert response.json()["error"]


class TestTriggerRoutes:
    def test_creating_a_trigger(self, client, db: AuditedDatabase):
        store = RecordStore(db)
        room_id = store.create("room", {"name": "R"}, actor="dana")["id"]
        response = client.post(
            f"{PREFIX}/rooms/{room_id}/triggers",
            json={
                "kind": vocab.CUSTOMER_IDLE,
                "duration_seconds": TEN_MINUTES,
                "steps": CHASE_STEPS,
            },
        )
        assert response.status_code == 201
        assert response.json()["live"] is False

    def test_an_out_of_range_duration_is_422_with_the_published_code(self, client, db):
        store = RecordStore(db)
        room_id = store.create("room", {"name": "R"}, actor="dana")["id"]
        response = client.post(
            f"{PREFIX}/rooms/{room_id}/triggers",
            json={"kind": vocab.CUSTOMER_IDLE, "duration_seconds": 30},
        )
        assert response.status_code == 422
        body = response.json()
        assert body["error"] == "duration_out_of_range"
        assert body["status"] == 422
        assert body["errors"]

    def test_an_unknown_step_kind_is_422(self, client, db):
        store = RecordStore(db)
        room_id = store.create("room", {"name": "R"}, actor="dana")["id"]
        response = client.post(
            f"{PREFIX}/rooms/{room_id}/triggers",
            json={"kind": vocab.CUSTOMER_IDLE, "steps": [{"kind": "escalate_to_ceo"}]},
        )
        assert response.status_code == 422
        assert response.json()["error"] == "unknown_step_kind"

    def test_an_unknown_interruption_event_is_422(self, client, db):
        store = RecordStore(db)
        room_id = store.create("room", {"name": "R"}, actor="dana")["id"]
        response = client.post(
            f"{PREFIX}/rooms/{room_id}/triggers",
            json={
                "kind": vocab.CUSTOMER_IDLE,
                "steps": [
                    {
                        "kind": vocab.STEP_WAIT,
                        "duration_seconds": 900,
                        "interruption_events": ["earthquake"],
                    }
                ],
            },
        )
        assert response.status_code == 422
        assert response.json()["error"] == "unknown_interruption_event"

    def test_listing_and_reading_a_trigger(self, client, db):
        store = RecordStore(db)
        room_id = store.create("room", {"name": "R"}, actor="dana")["id"]
        created = client.post(
            f"{PREFIX}/rooms/{room_id}/triggers",
            json={"kind": vocab.TEAMMATE_IDLE, "steps": REROUTE_STEPS},
        ).json()
        listed = client.get(f"{PREFIX}/rooms/{room_id}/triggers").json()
        assert listed["count"] == 1
        assert listed["live"] == 0
        read = client.get(f"{PREFIX}/rooms/{room_id}/triggers/{created['id']}").json()
        assert read["id"] == created["id"]
        assert read["anchor"] == vocab.ANCHOR_FIRST_CUSTOMER_MESSAGE

    def test_going_live_then_going_live_again(self, client, db):
        store = RecordStore(db)
        room_id = store.create("room", {"name": "R"}, actor="dana")["id"]
        created = client.post(
            f"{PREFIX}/rooms/{room_id}/triggers",
            json={"kind": vocab.CUSTOMER_IDLE, "steps": CHASE_STEPS},
        ).json()
        first = client.post(f"{PREFIX}/rooms/{room_id}/triggers/{created['id']}/go-live")
        assert first.status_code == 200
        assert first.json()["live"] is True
        second = client.post(f"{PREFIX}/rooms/{room_id}/triggers/{created['id']}/go-live")
        assert second.status_code == 409
        assert second.json()["error"] == "trigger_already_live"

    def test_an_unknown_trigger_is_a_404_with_the_published_code(self, client, db):
        store = RecordStore(db)
        room_id = store.create("room", {"name": "R"}, actor="dana")["id"]
        response = client.get(f"{PREFIX}/rooms/{room_id}/triggers/wf107_trigger_absent")
        assert response.status_code == 404
        assert response.json()["error"] == "trigger_not_found"

    def test_patching_a_trigger(self, client, db):
        store = RecordStore(db)
        room_id = store.create("room", {"name": "R"}, actor="dana")["id"]
        created = client.post(
            f"{PREFIX}/rooms/{room_id}/triggers",
            json={"kind": vocab.CUSTOMER_IDLE, "duration_seconds": TEN_MINUTES},
        ).json()
        patched = client.patch(
            f"{PREFIX}/rooms/{room_id}/triggers/{created['id']}",
            json={"duration_seconds": 3600},
        )
        assert patched.status_code == 200
        assert patched.json()["duration_seconds"] == 3600

    def test_patching_an_out_of_range_duration_is_422(self, client, db):
        store = RecordStore(db)
        room_id = store.create("room", {"name": "R"}, actor="dana")["id"]
        created = client.post(
            f"{PREFIX}/rooms/{room_id}/triggers",
            json={"kind": vocab.CUSTOMER_IDLE, "duration_seconds": TEN_MINUTES},
        ).json()
        response = client.patch(
            f"{PREFIX}/rooms/{room_id}/triggers/{created['id']}",
            json={"duration_seconds": 14 * 24 * 60 * 60},
        )
        assert response.status_code == 422
        assert response.json()["error"] == "duration_out_of_range"

    def test_deleting_a_trigger(self, client, db):
        store = RecordStore(db)
        room_id = store.create("room", {"name": "R"}, actor="dana")["id"]
        created = client.post(
            f"{PREFIX}/rooms/{room_id}/triggers",
            json={"kind": vocab.CUSTOMER_IDLE, "duration_seconds": TEN_MINUTES},
        ).json()
        response = client.delete(f"{PREFIX}/rooms/{room_id}/triggers/{created['id']}")
        assert response.status_code == 200
        body = response.json()
        assert body["deleted"] is True
        assert body["hard"] is False
        assert client.get(f"{PREFIX}/rooms/{room_id}/triggers/{created['id']}").status_code == 404


class TestConversationRoutes:
    def test_opening_and_reading_one(self, client, db: AuditedDatabase):
        store = RecordStore(db)
        room_id = store.create("room", {"name": "R"}, actor="dana")["id"]
        created = client.post(
            f"{PREFIX}/rooms/{room_id}/conversations",
            json={"origin": vocab.ORIGIN_INBOX, "inbox": "sales"},
        )
        assert created.status_code == 201
        conversation_id = created.json()["id"]
        read = client.get(f"{PREFIX}/rooms/{room_id}/conversations/{conversation_id}")
        assert read.status_code == 200
        assert read.json()["inbox"] == "sales"

    def test_listing_with_a_state_filter(self, client, db):
        store = RecordStore(db)
        room_id = store.create("room", {"name": "R"}, actor="dana")["id"]
        first = client.post(
            f"{PREFIX}/rooms/{room_id}/conversations", json={"inbox": "sales"}
        ).json()
        client.post(f"{PREFIX}/rooms/{room_id}/conversations", json={"inbox": "sales"})
        client.post(f"{PREFIX}/rooms/{room_id}/conversations/{first['id']}/close", json={})

        everything = client.get(f"{PREFIX}/rooms/{room_id}/conversations").json()
        assert everything["count"] == 2
        closed = client.get(
            f"{PREFIX}/rooms/{room_id}/conversations?state={vocab.STATE_CLOSED}"
        ).json()
        assert closed["count"] == 1

    def test_an_unknown_state_filter_is_422(self, client, db):
        store = RecordStore(db)
        room_id = store.create("room", {"name": "R"}, actor="dana")["id"]
        response = client.get(f"{PREFIX}/rooms/{room_id}/conversations?state=pending")
        assert response.status_code == 422
        assert response.json()["error"] == "unknown_conversation_state"

    def test_the_listing_publishes_the_origins_and_inboxes(self, client, db):
        store = RecordStore(db)
        room_id = store.create("room", {"name": "R"}, actor="dana")["id"]
        body = client.get(f"{PREFIX}/rooms/{room_id}/conversations").json()
        assert body["origins"] == list(vocab.ORIGINS)
        assert "escalations" in body["inboxes"]

    def test_writing_a_message(self, client, db):
        store = RecordStore(db)
        room_id = store.create("room", {"name": "R"}, actor="dana")["id"]
        conversation_id = client.post(
            f"{PREFIX}/rooms/{room_id}/conversations", json={"inbox": "sales"}
        ).json()["id"]
        response = client.post(
            f"{PREFIX}/rooms/{room_id}/conversations/{conversation_id}/messages",
            json={"author_kind": vocab.AUTHOR_CUSTOMER, "body": "hello"},
        )
        assert response.status_code == 201
        assert response.json()["author_kind"] == vocab.AUTHOR_CUSTOMER

    def test_an_unknown_author_kind_is_422(self, client, db):
        store = RecordStore(db)
        room_id = store.create("room", {"name": "R"}, actor="dana")["id"]
        conversation_id = client.post(
            f"{PREFIX}/rooms/{room_id}/conversations", json={"inbox": "sales"}
        ).json()["id"]
        response = client.post(
            f"{PREFIX}/rooms/{room_id}/conversations/{conversation_id}/messages",
            json={"author_kind": "robot", "body": "beep"},
        )
        assert response.status_code == 422
        assert response.json()["error"] == "unknown_author_kind"

    def test_closing_and_snoozing(self, client, db):
        store = RecordStore(db)
        room_id = store.create("room", {"name": "R"}, actor="dana")["id"]
        first = client.post(
            f"{PREFIX}/rooms/{room_id}/conversations", json={"inbox": "sales"}
        ).json()
        second = client.post(
            f"{PREFIX}/rooms/{room_id}/conversations", json={"inbox": "sales"}
        ).json()
        assert (
            client.post(
                f"{PREFIX}/rooms/{room_id}/conversations/{first['id']}/close", json={}
            ).status_code
            == 200
        )
        assert (
            client.post(
                f"{PREFIX}/rooms/{room_id}/conversations/{second['id']}/snooze", json={}
            ).status_code
            == 200
        )

    def test_rerouting(self, client, db):
        store = RecordStore(db)
        room_id = store.create("room", {"name": "R"}, actor="dana")["id"]
        conversation_id = client.post(
            f"{PREFIX}/rooms/{room_id}/conversations", json={"inbox": "sales"}
        ).json()["id"]
        response = client.post(
            f"{PREFIX}/rooms/{room_id}/conversations/{conversation_id}/reroute",
            json={"inbox": "escalations"},
        )
        assert response.status_code == 200
        assert response.json()["inbox"] == "escalations"

    def test_rerouting_to_the_same_inbox_is_409(self, client, db):
        store = RecordStore(db)
        room_id = store.create("room", {"name": "R"}, actor="dana")["id"]
        conversation_id = client.post(
            f"{PREFIX}/rooms/{room_id}/conversations", json={"inbox": "sales"}
        ).json()["id"]
        response = client.post(
            f"{PREFIX}/rooms/{room_id}/conversations/{conversation_id}/reroute",
            json={"inbox": "sales"},
        )
        assert response.status_code == 409
        assert response.json()["error"] == "reroute_to_same_inbox"

    def test_rerouting_to_an_unknown_inbox_is_409(self, client, db):
        store = RecordStore(db)
        room_id = store.create("room", {"name": "R"}, actor="dana")["id"]
        conversation_id = client.post(
            f"{PREFIX}/rooms/{room_id}/conversations", json={"inbox": "sales"}
        ).json()["id"]
        response = client.post(
            f"{PREFIX}/rooms/{room_id}/conversations/{conversation_id}/reroute",
            json={"inbox": "carrier_pigeon"},
        )
        assert response.status_code == 409
        assert response.json()["error"] == "inbox_unknown"

    def test_an_unknown_conversation_is_a_404(self, client, db):
        store = RecordStore(db)
        room_id = store.create("room", {"name": "R"}, actor="dana")["id"]
        response = client.get(f"{PREFIX}/rooms/{room_id}/conversations/wf107_conversation_absent")
        assert response.status_code == 404
        assert response.json()["error"] == "conversation_not_found"

    def test_the_activity_log_reads_in_order(self, client, db):
        store = RecordStore(db)
        room_id = store.create("room", {"name": "R"}, actor="dana")["id"]
        conversation_id = client.post(
            f"{PREFIX}/rooms/{room_id}/conversations", json={"inbox": "sales"}
        ).json()["id"]
        client.post(
            f"{PREFIX}/rooms/{room_id}/conversations/{conversation_id}/messages",
            json={"author_kind": vocab.AUTHOR_CUSTOMER, "body": "hello"},
        )
        client.post(f"{PREFIX}/rooms/{room_id}/conversations/{conversation_id}/close", json={})
        body = client.get(
            f"{PREFIX}/rooms/{room_id}/conversations/{conversation_id}/activity"
        ).json()
        codes = [row["code"] for row in body["activity"]]
        assert codes == ["message_sent", "conversation_closed"]
        assert body["count"] == 2


class TestSweepRoute:
    def test_evaluating_an_empty_room(self, client, db: AuditedDatabase):
        store = RecordStore(db)
        room_id = store.create("room", {"name": "R"}, actor="dana")["id"]
        response = client.post(f"{PREFIX}/rooms/{room_id}/evaluate", json={})
        assert response.status_code == 200
        body = response.json()
        assert body["fired_count"] == 0
        assert body["skipped"][0]["reason"] == vocab.SKIP_NO_TRIGGER

    def test_evaluating_fires_and_reports_the_skip_codes(self, client, db):
        store = RecordStore(db)
        room_id = store.create("room", {"name": "R"}, actor="dana")["id"]
        api_conversation = client.post(
            f"{PREFIX}/rooms/{room_id}/conversations",
            json={"origin": vocab.ORIGIN_API, "inbox": "sales"},
        ).json()
        client.post(
            f"{PREFIX}/rooms/{room_id}/conversations/{api_conversation['id']}/messages",
            json={"author_kind": vocab.AUTHOR_CUSTOMER, "body": "hello"},
        )
        trigger = client.post(
            f"{PREFIX}/rooms/{room_id}/triggers",
            json={"kind": vocab.CUSTOMER_IDLE, "duration_seconds": TEN_MINUTES},
        ).json()
        client.post(f"{PREFIX}/rooms/{room_id}/triggers/{trigger['id']}/go-live")

        response = client.post(
            f"{PREFIX}/rooms/{room_id}/evaluate",
            json={"now": rules.stamp(CLOCK + timedelta(minutes=20))},
        )
        assert response.status_code == 200
        skipped = response.json()["skipped"]
        assert {row["reason"] for row in skipped} == {vocab.SKIP_API_CREATED}

    def test_the_query_filters_work(self, client, db):
        store = RecordStore(db)
        room_id = store.create("room", {"name": "R"}, actor="dana")["id"]
        response = client.post(f"{PREFIX}/rooms/{room_id}/evaluate?kind=teammate_idle", json={})
        assert response.status_code == 200
        assert response.json()["skipped"][0]["reason"] == vocab.SKIP_NO_TRIGGER

    def test_an_unknown_kind_is_422(self, client, db):
        store = RecordStore(db)
        room_id = store.create("room", {"name": "R"}, actor="dana")["id"]
        response = client.post(f"{PREFIX}/rooms/{room_id}/evaluate?kind=sales_idle", json={})
        assert response.status_code == 422
        assert response.json()["error"] == "unknown_trigger_kind"

    def test_the_summary_route(self, client, db):
        store = RecordStore(db)
        room_id = store.create("room", {"name": "R"}, actor="dana")["id"]
        body = client.get(f"{PREFIX}/rooms/{room_id}/summary").json()
        assert body["room_id"] == room_id
        assert body["conversations"] == 0
        assert body["office_hours"]["derived"] is True


class TestRunRoutes:
    def test_advancing_and_resolving_through_http(self, client, db):
        store = RecordStore(db)
        room_id = store.create("room", {"name": "R"}, actor="dana")["id"]
        conversation_id = client.post(
            f"{PREFIX}/rooms/{room_id}/conversations", json={"inbox": "sales"}
        ).json()["id"]
        # The instant is supplied, because the route's own clock is the wall clock and the
        # sweep below is asked to evaluate a time this test controls.
        client.post(
            f"{PREFIX}/rooms/{room_id}/conversations/{conversation_id}/messages",
            json={
                "author_kind": vocab.AUTHOR_CUSTOMER,
                "body": "hello",
                "at": rules.stamp(CLOCK - timedelta(minutes=40)),
            },
        )
        trigger = client.post(
            f"{PREFIX}/rooms/{room_id}/triggers",
            json={
                "kind": vocab.CUSTOMER_IDLE,
                "duration_seconds": TEN_MINUTES,
                "steps": CHASE_STEPS,
            },
        ).json()
        client.post(f"{PREFIX}/rooms/{room_id}/triggers/{trigger['id']}/go-live")

        sweep = client.post(
            f"{PREFIX}/rooms/{room_id}/evaluate",
            json={"now": rules.stamp(CLOCK + timedelta(minutes=20))},
        ).json()
        assert sweep["fired_count"] == 1
        run_id = sweep["fired"][0]["id"]

        first = client.post(f"{PREFIX}/rooms/{room_id}/runs/{run_id}/advance", json={})
        assert first.status_code == 200
        assert first.json()["cursor"] == 1

        second = client.post(f"{PREFIX}/rooms/{room_id}/runs/{run_id}/advance", json={})
        assert second.json()["state"] == vocab.RUN_WAITING

        resolving = client.post(f"{PREFIX}/rooms/{room_id}/runs/{run_id}/resolve", json={})
        assert resolving.status_code == 409
        assert resolving.json()["error"] == "run_not_waiting"

        listed = client.get(f"{PREFIX}/rooms/{room_id}/runs").json()
        assert listed["count"] == 1
        assert listed["by_state"][vocab.RUN_WAITING] == 1
        assert listed["state_labels"][vocab.RUN_WAITING] == "Waiting"

        read = client.get(f"{PREFIX}/rooms/{room_id}/runs/{run_id}").json()
        assert read["id"] == run_id
        assert read["sent_by_this_product"] is False

    def test_an_unknown_run_is_a_404(self, client, db):
        store = RecordStore(db)
        room_id = store.create("room", {"name": "R"}, actor="dana")["id"]
        response = client.get(f"{PREFIX}/rooms/{room_id}/runs/wf107_run_absent")
        assert response.status_code == 404
        assert response.json()["error"] == "run_not_found"

    def test_advancing_an_unknown_run_is_a_404(self, client, db):
        store = RecordStore(db)
        room_id = store.create("room", {"name": "R"}, actor="dana")["id"]
        response = client.post(f"{PREFIX}/rooms/{room_id}/runs/wf107_run_absent/advance", json={})
        assert response.status_code == 404


class TestOfficeHoursRoutes:
    def test_reading_the_derived_default(self, client, db):
        store = RecordStore(db)
        room_id = store.create("room", {"name": "R"}, actor="dana")["id"]
        body = client.get(f"{PREFIX}/rooms/{room_id}/office-hours").json()
        assert body["stored"] is False
        assert body["schedule"]["saturday"]["open"] is None

    def test_writing_a_schedule(self, client, db):
        store = RecordStore(db)
        room_id = store.create("room", {"name": "R"}, actor="dana")["id"]
        response = client.put(
            f"{PREFIX}/rooms/{room_id}/office-hours",
            json={"schedule": {"monday": {"open": "10:00", "close": "16:00"}}},
        )
        assert response.status_code == 200
        assert response.json()["schedule"]["monday"] == {"open": 600, "close": 960}

    def test_writing_an_unusable_schedule_is_422(self, client, db):
        store = RecordStore(db)
        room_id = store.create("room", {"name": "R"}, actor="dana")["id"]
        response = client.put(
            f"{PREFIX}/rooms/{room_id}/office-hours",
            json={"schedule": {"monday": {"open": "18:00", "close": "09:00"}}},
        )
        assert response.status_code == 422
        assert response.json()["error"] == "invalid_office_hours"


class TestHostIntegration:
    def test_the_feature_is_mounted_by_discovery(self, client):
        body = client.get("/api/features").json()
        found = [entry for entry in body["features"] if entry["id"] == feature.FEATURE["id"]]
        assert found, sorted(entry["id"] for entry in body["features"])
        assert body["failed_count"] == 0

    def test_it_reports_its_routes(self, client):
        body = client.get("/api/features").json()
        entry = next(e for e in body["features"] if e["id"] == feature.FEATURE["id"])
        paths = {route["path"] for route in entry["routes"]}
        assert f"{PREFIX}/vocabulary" in paths
        assert f"{PREFIX}/rooms/{{room_id}}/evaluate" in paths

    def test_no_route_of_this_feature_collides_with_another(self, client):
        body = client.get("/api/features").json()
        seen: dict[tuple[str, str], str] = {}
        for entry in body["features"]:
            for route in entry["routes"]:
                for method in route["methods"]:
                    key = (method, route["path"])
                    assert key not in seen, f"{key} claimed by {seen[key]} and {entry['id']}"
                    seen[key] = entry["id"]


# --------------------------------------------------------------------------- #
# The audit trail
# --------------------------------------------------------------------------- #


class TestAuditSources:
    """Every write names a route the host actually mounted, and every write is audited."""

    def _audit_sources(self, target: Any) -> list[str]:
        """The recorded sources, read from the database the writes actually went to.

        Takes either a ``RecordStore`` or an ``AuditedDatabase`` because the two test
        styles reach the audit log differently: the engine tests are built on the
        ``store`` fixture, whose own in-memory database is the one being written, while
        the ``client`` fixture points the application at the ``db`` fixture. Reading the
        wrong one reports an empty log and the assertion passes for the wrong reason,
        which is exactly how a broken audit source survives a green suite.
        """

        rows = (
            target.db.audit(limit=5000)
            if isinstance(target, RecordStore)
            else target.audit(limit=5000)
        )
        return [str(row.get("source")) for row in rows if row.get("source")]

    def test_every_write_records_a_source(self, engine, room_id, store: RecordStore):
        conversation = open_conversation(engine, room_id)
        buyer_says(engine, conversation["id"], 40)
        trigger = make_trigger(engine, room_id, vocab.CUSTOMER_IDLE)
        go_live(engine, trigger["id"])
        at_engine(engine.store, 20).evaluate(room_id, {}, source=SOURCE)
        assert self._audit_sources(store)

    def test_the_evaluate_source_is_recorded(self, engine, room_id, store: RecordStore):
        conversation = open_conversation(engine, room_id)
        buyer_says(engine, conversation["id"], 40)
        trigger = make_trigger(engine, room_id, vocab.CUSTOMER_IDLE)
        go_live(engine, trigger["id"])
        at_engine(engine.store, 20).evaluate(room_id, {}, source=SOURCE)
        assert SOURCE in self._audit_sources(store)

    def test_every_recorded_source_names_a_mounted_route(self, client, db: AuditedDatabase):
        """The contract's rule, checked against the live registry rather than a copy.

        ``{room_id}`` and the other placeholders become a regex segment, so this compares
        against the router's declared paths and not against a list somebody typed twice.
        """

        store = RecordStore(db)
        room_id = store.create("room", {"name": "R"}, actor="dana")["id"]
        conversation = store.create(
            vocab.CONVERSATIONS,
            {vocab.ROOM_REF: room_id, "state": "open", "origin": "inbox"},
            room_id=room_id,
            actor="dana",
            source=feature.CONVERSATION_SOURCE,
        )
        store.create(
            vocab.PARTS,
            {
                vocab.ROOM_REF: room_id,
                "conversation_id": conversation["id"],
                "author_kind": vocab.AUTHOR_CUSTOMER,
                "at": rules.stamp(CLOCK),
            },
            room_id=room_id,
            actor="buyer",
            source=MESSAGE_SOURCE,
        )

        mounted = [
            (method, route.path)
            for route in feature.router.routes
            if isinstance(route, APIRoute)
            for method in route.methods
        ]
        placeholder = re.compile(r"\{[a-z_]+\}")
        for source in self._audit_sources(db):
            method, _, path = source.partition(" ")
            wanted = placeholder.sub("[^/]+", path)
            assert any(m == method and re.fullmatch(wanted, p) for m, p in mounted), (
                f"{source} names no mounted route"
            )

    def test_every_seed_source_names_a_mounted_route(self):
        """Checked without a database, so it cannot pass because the seed did nothing."""

        mounted = [
            (method, route.path)
            for route in feature.router.routes
            if isinstance(route, APIRoute)
            for method in route.methods
        ]
        placeholder = re.compile(r"\{[a-z_]+\}")
        for source in feature.SEED_SOURCES:
            method, _, path = source.partition(" ")
            wanted = placeholder.sub("[^/]+", path)
            assert any(m == method and re.fullmatch(wanted, p) for m, p in mounted), (
                f"{source} names no mounted route"
            )

    def test_every_source_constant_is_built_from_the_router_prefix(self):
        for source in feature.SEED_SOURCES:
            assert PREFIX in source, source

    def test_an_http_write_names_its_own_route(self, client, db: AuditedDatabase):
        store = RecordStore(db)
        room_id = store.create("room", {"name": "R"}, actor="dana")["id"]
        client.post(
            f"{PREFIX}/rooms/{room_id}/triggers",
            json={"kind": vocab.CUSTOMER_IDLE, "duration_seconds": TEN_MINUTES},
        )
        assert feature.TRIGGER_SOURCE in self._audit_sources(db)

    def test_the_sweep_writes_name_the_evaluate_route(self, client, db: AuditedDatabase):
        store = RecordStore(db)
        room_id = store.create("room", {"name": "R"}, actor="dana")["id"]
        client.post(f"{PREFIX}/rooms/{room_id}/evaluate", json={})
        assert feature.EVALUATE_SOURCE not in self._audit_sources(db)  # nothing fired

        conversation_id = client.post(
            f"{PREFIX}/rooms/{room_id}/conversations", json={"inbox": "sales"}
        ).json()["id"]
        client.post(
            f"{PREFIX}/rooms/{room_id}/conversations/{conversation_id}/messages",
            json={
                "author_kind": vocab.AUTHOR_CUSTOMER,
                "body": "hello",
                "at": rules.stamp(CLOCK - timedelta(minutes=40)),
            },
        )
        trigger = client.post(
            f"{PREFIX}/rooms/{room_id}/triggers",
            json={"kind": vocab.CUSTOMER_IDLE, "duration_seconds": TEN_MINUTES},
        ).json()
        client.post(f"{PREFIX}/rooms/{room_id}/triggers/{trigger['id']}/go-live")
        sweep = client.post(
            f"{PREFIX}/rooms/{room_id}/evaluate",
            json={"now": rules.stamp(CLOCK + timedelta(minutes=20))},
        ).json()
        assert sweep["fired_count"] == 1
        assert feature.EVALUATE_SOURCE in self._audit_sources(db)

    def test_a_soft_delete_is_audited(self, engine, room_id, store: RecordStore):
        trigger = make_trigger(engine, room_id, vocab.CUSTOMER_IDLE)
        engine.delete_trigger(trigger["id"], source=feature.TRIGGER_DELETE_SOURCE)
        assert feature.TRIGGER_DELETE_SOURCE in self._audit_sources(store)


# --------------------------------------------------------------------------- #
# The seed
# --------------------------------------------------------------------------- #


class TestSeed:
    def test_it_returns_a_string(self, db: AuditedDatabase):
        store = RecordStore(db)
        room_id = store.create("room", {"name": "R"}, actor="dana")["id"]
        summary = feature.seed(db, {"room_ids": [(room_id, "R")], "now": CLOCK, "rng": None})
        assert isinstance(summary, str)
        assert summary

    def test_the_returned_string_is_cp1252_encodable(self, db: AuditedDatabase):
        """The seeder prints it on a Windows console and one arrow broke the whole seed."""

        store = RecordStore(db)
        room_id = store.create("room", {"name": "R"}, actor="dana")["id"]
        summary = feature.seed(db, {"room_ids": [(room_id, "R")], "now": CLOCK, "rng": None})
        summary.encode("cp1252")

    def test_it_names_the_states_it_created(self, db: AuditedDatabase):
        store = RecordStore(db)
        room_id = store.create("room", {"name": "R"}, actor="dana")["id"]
        summary = feature.seed(db, {"room_ids": [(room_id, "R")], "now": CLOCK, "rng": None})
        for phrase in (
            "closed",
            "snoozed",
            "REST API",
            "interrupted",
            "rerouted",
            "delayed response",
            "office-hours",
        ):
            assert phrase in summary, phrase

    def test_it_produces_rows_of_every_kind(self, db: AuditedDatabase):
        store = RecordStore(db)
        room_id = store.create("room", {"name": "R"}, actor="dana")["id"]
        feature.seed(db, {"room_ids": [(room_id, "R")], "now": CLOCK, "rng": None})
        for collection, minimum in (
            (vocab.CONVERSATIONS, 6),
            (vocab.PARTS, 6),
            (vocab.TRIGGERS, 3),
            (vocab.RUNS, 1),
            (vocab.ACTIVITY, 1),
            (vocab.OFFICE_HOURS, 1),
        ):
            assert len(store.find(collection, {vocab.ROOM_REF: room_id}, limit=100)) >= minimum, (
                collection
            )

    def test_it_seeds_the_states_that_are_not_successes(self, db: AuditedDatabase):
        """A demo of only green teaches a reviewer nothing."""

        store = RecordStore(db)
        room_id = store.create("room", {"name": "R"}, actor="dana")["id"]
        feature.seed(db, {"room_ids": [(room_id, "R")], "now": CLOCK, "rng": None})

        conversations = [
            dict(row["data"]) for row in store.find(vocab.CONVERSATIONS, {vocab.ROOM_REF: room_id})
        ]
        states = {row["state"] for row in conversations}
        assert vocab.STATE_CLOSED in states
        assert vocab.STATE_SNOOZED in states
        assert any(row["origin"] == vocab.ORIGIN_API for row in conversations)
        assert any(row.get("inbox") == "escalations" for row in conversations)

        runs = [dict(row["data"]) for row in store.find(vocab.RUNS, {vocab.ROOM_REF: room_id})]
        assert any(row["state"] == vocab.RUN_INTERRUPTED for row in runs)

    def test_it_runs_with_no_room_at_all(self, db: AuditedDatabase):
        summary = feature.seed(db, {"room_ids": [], "now": CLOCK, "rng": None})
        assert "room created" in summary

    def test_a_second_run_in_the_same_room_still_works(self, db: AuditedDatabase):
        store = RecordStore(db)
        room_id = store.create("room", {"name": "R"}, actor="dana")["id"]
        first = feature.seed(db, {"room_ids": [(room_id, "R")], "now": CLOCK, "rng": None})
        second = feature.seed(db, {"room_ids": [(room_id, "R")], "now": CLOCK, "rng": None})
        assert "12 conversation(s)" in second
        assert int(first.split()[0]) * 2 == 12

    def test_every_seeded_audit_source_names_a_mounted_route(self, db: AuditedDatabase):
        store = RecordStore(db)
        room_id = store.create("room", {"name": "R"}, actor="dana")["id"]
        feature.seed(db, {"room_ids": [(room_id, "R")], "now": CLOCK, "rng": None})

        mounted = [
            (method, route.path)
            for route in feature.router.routes
            if isinstance(route, APIRoute)
            for method in route.methods
        ]
        placeholder = re.compile(r"\{[a-z_]+\}")
        for row in db.audit(limit=5000):
            source = str(row.get("source") or "")
            if not source or not source.startswith(("GET ", "POST ", "PUT ", "PATCH ", "DELETE ")):
                continue
            method, _, path = source.partition(" ")
            wanted = placeholder.sub("[^/]+", path)
            assert any(m == method and re.fullmatch(wanted, p) for m, p in mounted), (
                f"{source} names no mounted route"
            )

    #: An ISO 8601 instant, matched so a payload key can be tested without knowing its format.


INSTANT = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")

#: Every payload key this workflow writes an instant into. Named rather than searched for
#: by pattern, so a new timestamped field has to be added here rather than silently
#: escaping the check below.
INSTANT_KEYS = (
    "at",
    "created_at",
    "started_at",
    "wait_started_at",
    "closed_at",
    "snoozed_at",
    "unsnoozed_at",
    "rerouted_at",
    "marked_priority_at",
    "went_live_at",
    "updated_at",
    "anchor",
)


class TestSeedInstants:
    """Every seeded instant derives from ``context["now"]``, never from a written-in date.

    The failure this guards against is real: a payload carrying ``2026-10-05T08:00:00Z``
    passed on the day it was written, stopped passing the next, and took a merged repair
    plus four blocked pull requests to undo. The check is not "no date-shaped string
    appears" -- the seed legitimately produces dates, that is the point -- but "every date
    it produces is within a day of the clock the seeder handed in", and "moving that clock
    moves every one of them".
    """

    def _instants(self, store: RecordStore, room_id: str) -> list[str]:
        found: list[str] = []
        for collection in (
            vocab.CONVERSATIONS,
            vocab.PARTS,
            vocab.TRIGGERS,
            vocab.RUNS,
            vocab.ACTIVITY,
            vocab.OFFICE_HOURS,
        ):
            for row in store.find(collection, {vocab.ROOM_REF: room_id}, limit=1000):
                data = dict(row["data"])
                for key in INSTANT_KEYS:
                    value = data.get(key)
                    if isinstance(value, str) and INSTANT.search(value):
                        found.append(value)
                for field in ("history", "detail", "steps"):
                    for value in _walk(data.get(field)):
                        if INSTANT.search(value):
                            found.append(INSTANT.search(value).group(0))
        return found

    def test_it_produces_instants_and_none_of_them_is_hardcoded(self, db: AuditedDatabase):
        store = RecordStore(db)
        room_id = store.create("room", {"name": "R"}, actor="dana")["id"]
        feature.seed(db, {"room_ids": [(room_id, "R")], "now": CLOCK, "rng": None})

        instants = self._instants(store, room_id)
        assert instants, "the seed wrote no instants at all, so this check proves nothing"
        # The seed writes instants both slightly before the clock (the buyer messages that
        # make a conversation quiet) and slightly after it (a wait resolved), plus the
        # office-hours walk, which legitimately lands on the next open morning. Three days
        # covers all three with room to spare; a hardcoded date would be years out.
        for value in instants:
            parsed = rules.coerce_instant(value)
            assert parsed is not None
            assert abs(parsed - CLOCK) < timedelta(days=3), value

    def test_moving_the_clock_moves_every_instant(self, db: AuditedDatabase):
        """The stronger claim, and the one that actually rules out a written-in date."""

        store = RecordStore(db)
        first_room = store.create("room", {"name": "A"}, actor="dana")["id"]
        second_room = store.create("room", {"name": "B"}, actor="dana")["id"]

        feature.seed(db, {"room_ids": [(first_room, "A")], "now": CLOCK, "rng": None})
        feature.seed(
            db,
            {"room_ids": [(second_room, "B")], "now": CLOCK + timedelta(days=400), "rng": None},
        )

        first = {rules.coerce_instant(value) for value in self._instants(store, first_room)}
        second = {rules.coerce_instant(value) for value in self._instants(store, second_room)}

        assert first and second
        assert not (first & second), "some instant ignored the clock it was given"
        # And every instant in the second room sits within a few days of the clock that
        # room was seeded with, which is the claim that a hardcoded date cannot make.
        shifted = CLOCK + timedelta(days=400)
        for value in second:
            assert abs(value - shifted) < timedelta(days=3), value

    def test_the_seed_takes_its_clock_from_the_context(self, db: AuditedDatabase):
        store = RecordStore(db)
        room_id = store.create("room", {"name": "R"}, actor="dana")["id"]
        shifted = CLOCK + timedelta(days=90)
        feature.seed(db, {"room_ids": [(room_id, "R")], "now": shifted, "rng": None})
        instants = [rules.coerce_instant(v) for v in self._instants(store, room_id)]
        assert instants
        assert min(instants) > shifted - timedelta(days=3)
        assert max(instants) < shifted + timedelta(days=3)


def _walk(node: Any) -> list[str]:
    """Every string inside a nested payload, so a timestamped history entry is found."""

    if isinstance(node, str):
        return [node]
    if isinstance(node, dict):
        out: list[str] = []
        for key, value in node.items():
            out.append(str(key))
            out.extend(_walk(value))
        return out
    if isinstance(node, (list, tuple)):
        out = []
        for entry in node:
            out.extend(_walk(entry))
        return out
    return []
