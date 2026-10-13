"""WF-112: the domain rules behind a Slack notification from a buyer signal.

What is under test
------------------

The package :mod:`dsr.signal_slack` holds the rules, and this file exercises them
without the framework. That is the point of keeping the package separate from the
feature module: a Liquid fallback, a rate-limit window and a refusal precedence can
each be read and checked on its own, and a test that goes through HTTP to check one
of them cannot tell a broken rule from a broken handler.

The groups are:

* **The sourced vocabulary.** The seven things the research fixes are pinned, so a
  change to a sourced number is a test failure rather than a silent drift.
* **The message surfaces.** Liquid tags and their documented fallback, Slack
  mentions, and Slack links.
* **The connection.** The two refusals the issue names at connect time, and the
  rules the other fields follow.
* **The rate limits.** Both sourced numbers, and the boundary that decides the
  slower broadcast path.
* **The decision.** Every refusal code in precedence order, and the recorded case.
* **The engine.** The writes, the signal read, and the preview that writes nothing.
* **The import surface.** The store-only rule, pinned line by line.

Every test uses the suite's shared ``store`` fixture, an in-memory ``RecordStore``
over a database created for that test alone, so this file passes on its own and
inside ``pytest-xdist`` without an ordering dependency.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from dsr.signal_slack import (
    INFERENCES,
    RATE_LIMITS,
    REFUSAL_CODES,
    REFUSAL_PRECEDENCE,
    RESEARCHED_REFUSALS,
    SOURCED,
    SlackNotifier,
    check_rate_limit,
    decide,
    describe,
    is_link,
    is_mention_id,
    normalise_automation,
    normalise_channel,
    normalise_connection,
    render_link,
    render_mention,
    render_message,
    render_tags,
    require_bot_scope,
    resolve_channel,
    scan_syntax,
    signal_summary,
)
from dsr.signal_slack.errors import (
    ChannelRequired,
    DuplicateAutomation,
    DuplicateConnection,
    InvalidAutomation,
    InvalidConnection,
    InvalidDispatch,
    MissingBotScope,
    UnknownAutomation,
    UnknownConnection,
    UnknownDispatch,
    UnknownSignal,
)
from dsr.signal_slack.vocabulary import (
    APP_API_QUOTE,
    BOT_SCOPE_QUOTE,
    BROADCAST_QUOTE,
    CHANNEL_REQUIRED_QUOTE,
    LIMIT_FOR_KIND,
    MENTION_QUOTE,
    NO_CONVERSION_QUOTE,
    SIGNAL_COLLECTION,
)

ROOM = "room-1"
NOW = datetime(2026, 10, 13, 9, 0, tzinfo=timezone.utc)
SOURCE = "test"


# --------------------------------------------------------------------------- #
# Provisioning
# --------------------------------------------------------------------------- #


@pytest.fixture
def notifier(store):
    """The engine over the test's own store, with a frozen clock."""
    return SlackNotifier(store, now=lambda: NOW)


def connection_payload(**overrides):
    payload = {
        "workspace": "acme",
        "channel": "#Sales-Signals",
        "channel_kind": "public",
        "scopes": ["bot", "chat:write"],
        "bot_user": "U0ACMEBOT",
    }
    payload.update(overrides)
    return payload


def automation_payload(**overrides):
    payload = {
        "name": "High-intent buyer signal",
        "trigger": "signal",
        "state": "live",
        "channel": "sales-signals",
        "message": "New signal for {{customer.username}}. See <https://example.co/room|the room>.",
    }
    payload.update(overrides)
    return payload


def connect(notifier_, room_id=ROOM, **overrides):
    return notifier_.connect(
        connection_payload(**overrides), room_id=room_id, actor="test", source=SOURCE
    )


def make_automation(notifier_, room_id=ROOM, **overrides):
    return notifier_.create_automation(
        automation_payload(**overrides), room_id=room_id, actor="test", source=SOURCE
    )


def put_signal(store, room_id=ROOM, **overrides):
    data = {
        "type": "pricing_page_view",
        "urgency": "high",
        "signal_name": "Buyer returned to pricing",
        "rendered": {"text": "Returned three times in one session."},
        "idempotency_key": "sig-" + str(len(store.list(SIGNAL_COLLECTION, room_id=room_id))),
    }
    data.update(overrides)
    return store.create(SIGNAL_COLLECTION, data, room_id=room_id, actor="test", source=SOURCE)


def wired(notifier_, store, room_id=ROOM):
    """A connection, a live block and one signal: the whole chain a live post needs."""
    connect(notifier_, room_id=room_id)
    block = make_automation(notifier_, room_id=room_id)
    signal = put_signal(store, room_id=room_id)
    return {"connection": notifier_.connection(room_id=room_id), "block": block, "signal": signal}


# --------------------------------------------------------------------------- #
# The sourced vocabulary
# --------------------------------------------------------------------------- #


class TestTheSourcedVocabulary:
    def test_the_bot_scope_is_the_only_required_scope(self):
        assert "bot" in BOT_SCOPE_QUOTE
        result = require_bot_scope(["bot"])
        assert result == ["bot"]

    def test_the_two_rate_limits_are_the_sourced_numbers(self):
        assert RATE_LIMITS["app_api"]["requests"] == 10
        assert RATE_LIMITS["app_api"]["per_seconds"] == 1
        assert RATE_LIMITS["broadcast"]["requests"] == 1
        assert RATE_LIMITS["broadcast"]["per_seconds"] == 10
        assert RATE_LIMITS["app_api"]["quote"] == APP_API_QUOTE
        assert RATE_LIMITS["broadcast"]["quote"] == BROADCAST_QUOTE

    def test_the_three_researched_refusals_are_all_present(self):
        assert set(RESEARCHED_REFUSALS) <= set(REFUSAL_CODES)
        assert set(RESEARCHED_REFUSALS) == {
            "missing_bot_scope",
            "channel_required",
            "bot_not_in_channel",
        }

    def test_the_mention_and_channel_and_conversion_rules_are_quoted(self):
        assert "<@U012AB3CD>" in MENTION_QUOTE
        assert "required at connect time" in CHANNEL_REQUIRED_QUOTE
        assert "don't attach conversions" in NO_CONVERSION_QUOTE

    def test_slack_is_named_as_the_channel_and_nothing_is_sent(self):
        body = describe()
        assert body["sends_slack"] is False
        assert body["delivery"] == "not_sent"
        assert body["attribution_excluded"] is True

    def test_both_limits_are_named_and_the_live_path_takes_the_slower_one(self):
        assert LIMIT_FOR_KIND["live"] == "broadcast"
        assert LIMIT_FOR_KIND["test"] == "app_api"

    def test_the_vocabulary_serves_the_malformed_source_entries(self):
        assert len(describe()["collections"]) == 4
        assert describe()["tag_fallback"]["customer.username"] == "the buyer"


# --------------------------------------------------------------------------- #
# Liquid tags
# --------------------------------------------------------------------------- #


class TestLiquidTags:
    def test_a_set_tag_renders_the_value(self):
        text, missing = render_tags("Hey {{customer.username}}", {"customer": {"username": "Dana"}})
        assert text == "Hey Dana"
        assert missing == []

    def test_the_dotted_path_is_read_from_a_nested_profile(self):
        text, missing = render_tags("{{customer.channel}}", {"customer": {"channel": "pricing"}})
        assert text == "pricing"
        assert missing == []

    def test_an_unset_tag_renders_the_documented_fallback_not_braces(self):
        text, missing = render_tags(
            "Hey {{customer.username}}, from {{customer.channel}}", {"customer": {}}
        )
        assert "{{" not in text
        assert text == "Hey the buyer, from the sales room"
        assert missing == ["customer.username", "customer.channel"]

    def test_an_unknown_path_renders_the_generic_fallback(self):
        text, missing = render_tags("Hi {{customer.email}}", {})
        assert text == "Hi unknown"
        assert missing == ["customer.email"]

    def test_a_non_string_value_counts_as_unset(self):
        text, missing = render_tags("Hi {{customer.username}}", {"customer": {"username": 42}})
        assert text == "Hi the buyer"
        assert missing == ["customer.username"]

    def test_whitespace_inside_the_braces_is_forgiven(self):
        text, _ = render_tags("Hi {{ customer.username }}", {"customer": {"username": "Dana"}})
        assert text == "Hi Dana"

    def test_a_stray_single_brace_is_left_alone(self):
        text, missing = render_tags("a { brace and a } brace", {})
        assert text == "a { brace and a } brace"
        assert missing == []


# --------------------------------------------------------------------------- #
# Mentions and links
# --------------------------------------------------------------------------- #


class TestMentionsAndLinks:
    @pytest.mark.parametrize("user_id", ["U012AB3CD", "W123", "U1"])
    def test_a_well_formed_id_renders_as_a_mention(self, user_id):
        assert render_mention(user_id) == f"<@{user_id}>"
        assert is_mention_id(user_id) is True

    @pytest.mark.parametrize("bad", ["dana", "@dana", "X123", "", None, 7])
    def test_an_id_with_no_slack_prefix_is_refused(self, bad):
        with pytest.raises(InvalidAutomation):
            render_mention(bad)
        assert is_mention_id(bad) is False

    def test_a_bare_link_is_embedded(self):
        assert render_link("https://example.co/room") == "<https://example.co/room>"

    def test_a_labelled_link_is_embedded_with_its_label(self):
        assert render_link("https://example.co/room", "the room") == (
            "<https://example.co/room|the room>"
        )

    @pytest.mark.parametrize("bad", ["example.co", "ftp://example.co", "", None])
    def test_a_non_absolute_link_is_refused(self, bad):
        with pytest.raises(InvalidAutomation):
            render_link(bad)
        assert is_link(bad) is False

    def test_scan_finds_both_surfaces(self):
        found = scan_syntax("Hey <@U012AB3CD>, see <https://example.co/x|X>")
        assert found["mentions"] == ["<@U012AB3CD>"]
        assert found["links"] == ["<https://example.co/x|X>"]
        assert found["invalid"] == []

    @pytest.mark.parametrize("bad", ["<@dana>", "<not a link>", "<https://x|>"])
    def test_a_token_that_is_neither_is_reported_invalid(self, bad):
        assert scan_syntax(bad)["invalid"] == [bad]

    def test_a_message_renders_tags_and_keeps_the_syntax(self):
        rendered = render_message(
            "Hey {{customer.username}}, see <https://example.co/x|X>",
            {"customer": {"username": "Dana"}},
        )
        assert rendered["text"] == "Hey Dana, see <https://example.co/x|X>"
        assert rendered["links"] == ["<https://example.co/x|X>"]
        assert rendered["missing_tags"] == []

    def test_a_blank_message_is_refused(self):
        with pytest.raises(InvalidAutomation):
            render_message("   ", {})

    def test_a_malformed_token_in_a_message_is_refused(self):
        with pytest.raises(InvalidAutomation):
            render_message("Hey <@dana>", {})


# --------------------------------------------------------------------------- #
# The connection
# --------------------------------------------------------------------------- #


class TestTheConnection:
    def test_a_credential_without_the_bot_scope_is_refused_by_name(self):
        with pytest.raises(MissingBotScope) as caught:
            normalise_connection(connection_payload(scopes=["chat:write"]))
        assert caught.value.code == "missing_bot_scope"
        assert caught.value.status == 422

    def test_a_credential_with_no_scopes_at_all_is_refused(self):
        with pytest.raises(MissingBotScope):
            normalise_connection({"workspace": "acme", "channel": "sales"})

    def test_a_credential_with_an_unknown_scope_is_refused(self):
        with pytest.raises(InvalidConnection) as caught:
            normalise_connection(connection_payload(scopes=["bot", "telepathy"]))
        assert "telepathy" in str(caught.value)

    def test_a_connect_without_a_channel_is_refused_by_name(self):
        for channel in ("", "   ", "#", None):
            with pytest.raises(ChannelRequired) as caught:
                normalise_connection(connection_payload(channel=channel))
            assert caught.value.code == "channel_required"

    def test_a_channel_is_folded_to_one_spelling(self):
        assert normalise_channel("#Sales-Signals") == "sales-signals"
        assert normalise_channel("  sales-signals ") == "sales-signals"
        assert normalise_channel("Sales") == "sales"

    def test_a_channel_with_a_space_is_refused(self):
        with pytest.raises(InvalidConnection):
            normalise_channel("sales signals")

    def test_a_channel_longer_than_slack_allows_is_refused(self):
        with pytest.raises(InvalidConnection):
            normalise_channel("x" * 81)

    def test_a_channel_kind_outside_the_two_is_refused(self):
        with pytest.raises(InvalidConnection):
            normalise_connection(connection_payload(channel_kind="secret"))

    def test_a_private_connection_defaults_to_the_bot_not_being_in_the_channel(self):
        data = normalise_connection(connection_payload(channel_kind="private"))
        assert data["bot_in_channel"] is False

    def test_a_public_connection_defaults_to_the_bot_being_in_the_channel(self):
        data = normalise_connection(connection_payload(channel_kind="public"))
        assert data["bot_in_channel"] is True

    def test_a_missing_workspace_is_refused(self):
        with pytest.raises(InvalidConnection):
            normalise_connection(connection_payload(workspace=""))

    def test_a_non_object_connection_is_refused(self):
        with pytest.raises(InvalidConnection):
            normalise_connection(["not", "an", "object"])


# --------------------------------------------------------------------------- #
# The automation
# --------------------------------------------------------------------------- #


class TestTheAutomation:
    def test_a_block_needs_a_name(self):
        with pytest.raises(InvalidAutomation):
            normalise_automation(automation_payload(name="  "))

    def test_a_block_kind_outside_the_one_this_build_ships_is_refused(self):
        with pytest.raises(InvalidAutomation):
            normalise_automation(automation_payload(block_kind="email"))

    def test_a_trigger_outside_the_two_researched_ones_is_refused(self):
        with pytest.raises(InvalidAutomation):
            normalise_automation(automation_payload(trigger="cron"))

    def test_the_webhook_trigger_is_accepted(self):
        assert normalise_automation(automation_payload(trigger="webhook"))["trigger"] == "webhook"

    def test_a_block_needs_a_message(self):
        with pytest.raises(InvalidAutomation):
            normalise_automation(automation_payload(message=""))

    def test_a_message_over_the_ceiling_is_refused(self):
        with pytest.raises(InvalidAutomation):
            normalise_automation(automation_payload(message="x" * 4001))

    def test_a_malformed_mention_is_refused_at_save_time(self):
        with pytest.raises(InvalidAutomation):
            normalise_automation(automation_payload(message="Hey <@dana>"))

    def test_a_state_outside_draft_and_live_is_refused(self):
        with pytest.raises(InvalidAutomation):
            normalise_automation(automation_payload(state="paused"))

    def test_a_block_defaults_to_draft(self):
        assert normalise_automation({"name": "x", "message": "hi"})["state"] == "draft"

    def test_a_block_channel_is_normalised(self):
        assert normalise_automation(automation_payload(channel="#Sales"))["channel"] == "sales"

    def test_a_missing_dispatch_kind_defaults_to_live(self):
        from dsr.signal_slack import normalise_dispatch_kind

        assert normalise_dispatch_kind(None) == "live"
        with pytest.raises(InvalidDispatch):
            normalise_dispatch_kind("digest")


# --------------------------------------------------------------------------- #
# The rate limits
# --------------------------------------------------------------------------- #


class TestTheRateLimits:
    def test_the_broadcast_path_is_limited_to_one_per_ten_seconds(self):
        result = check_rate_limit([NOW.isoformat()], kind="live", now=NOW)
        assert result["limited"] is True
        assert result["per_seconds"] == 10
        assert result["retry_after_seconds"] == 10.0

    def test_the_same_broadcast_is_not_limited_after_the_window(self):
        result = check_rate_limit([(NOW - timedelta(seconds=11)).isoformat()], kind="live", now=NOW)
        assert result["limited"] is False

    def test_the_broadcast_boundary_is_the_window_itself(self):
        result = check_rate_limit([(NOW - timedelta(seconds=10)).isoformat()], kind="live", now=NOW)
        assert result["limited"] is False, "a moment exactly one window old has left the window"

    def test_the_app_api_path_allows_ten_per_second(self):
        nine = [(NOW - timedelta(milliseconds=100 * index)).isoformat() for index in range(9)]
        assert check_rate_limit(nine, kind="test", now=NOW)["limited"] is False
        ten = [(NOW - timedelta(milliseconds=50 * index)).isoformat() for index in range(10)]
        assert check_rate_limit(ten, kind="test", now=NOW)["limited"] is True

    def test_a_stamp_that_is_not_a_moment_is_ignored(self):
        assert check_rate_limit(["not a date"], kind="live", now=NOW)["limited"] is False

    def test_an_unknown_kind_falls_back_to_the_slower_limit(self):
        result = check_rate_limit([NOW.isoformat()], kind="mystery", now=NOW)
        assert result["per_seconds"] == 10


# --------------------------------------------------------------------------- #
# The decision
# --------------------------------------------------------------------------- #


def decision(**overrides):
    base = {
        "connection": {
            "workspace": "acme",
            "channel": "sales-signals",
            "channel_kind": "public",
            "state": "connected",
            "bot_in_channel": True,
        },
        "automation": {"state": "live", "message": "Hi {{customer.username}}", "channel": ""},
        "signal": None,
        "customer": {"customer": {"username": "Dana"}},
        "kind": "live",
        "now": NOW,
        "priors": (),
    }
    base.update(overrides)
    return decide(**base)


class TestTheDecision:
    def test_a_clean_chain_is_recorded_and_renders_the_message(self):
        result = decision()
        assert result["state"] == "recorded"
        assert result["refusal"] == ""
        assert result["channel"] == "sales-signals"
        assert result["message"]["text"] == "Hi Dana"
        assert result["deliverable"] is True

    def test_nothing_can_be_sent_so_delivery_is_a_stand_in(self):
        result = decision()
        assert result["delivery"] == "not_sent"
        assert "no Slack transport" in result["delivery_note"]

    def test_no_conversion_is_attached(self):
        result = decision()
        assert result["attribution_excluded"] is True
        assert not any("conversion" in key for key in result)

    def test_no_connection_is_refused_first(self):
        result = decision(connection=None)
        assert result["refusal"] == "no_connection"
        assert result["deliverable"] is False

    def test_a_revoked_connection_is_refused(self):
        result = decision(
            connection={"state": "revoked", "channel": "sales", "channel_kind": "public"}
        )
        assert result["refusal"] == "connection_revoked"

    def test_a_draft_block_does_not_fire_on_a_signal(self):
        result = decision(automation={"state": "draft", "message": "hi"})
        assert result["refusal"] == "automation_not_live"

    def test_a_test_send_runs_on_a_draft_block(self):
        result = decision(
            kind="test", automation={"state": "draft", "message": "hi", "channel": "sales"}
        )
        assert result["state"] == "recorded"

    def test_a_channel_that_is_nowhere_is_refused_by_name(self):
        result = decision(
            connection={"state": "connected", "channel": "", "channel_kind": "public"}
        )
        assert result["refusal"] == "channel_required"

    def test_a_bot_not_in_a_private_channel_is_refused_with_the_invite(self):
        result = decision(
            connection={
                "state": "connected",
                "channel": "deal-desk",
                "channel_kind": "private",
                "bot_in_channel": False,
            },
            automation={"state": "live", "message": "hi"},
        )
        assert result["refusal"] == "bot_not_in_channel"
        assert "/invite" in result["reason"]

    def test_a_rate_limited_post_is_refused_with_the_retry_window(self):
        result = decision(priors=[NOW.isoformat()])
        assert result["refusal"] == "rate_limited"
        assert result["retry_after_seconds"] == 10.0

    def test_the_per_message_channel_wins_over_the_block_default(self):
        result = decision(
            automation={"state": "live", "message": "hi", "channel": "block-default"},
            channel="per-message",
        )
        assert result["channel"] == "per-message"

    def test_the_block_default_wins_over_the_connection_channel(self):
        result = decision(automation={"state": "live", "message": "hi", "channel": "block-default"})
        assert result["channel"] == "block-default"

    def test_every_dispatch_refusal_code_is_reachable(self):
        """``missing_bot_scope`` is a connect-time refusal and cannot be reached here;
        the other six are decision-time and each is produced by one arrangement."""
        reached = {
            decision(connection=None)["refusal"],
            decision(connection={"state": "revoked", "channel": "x"})["refusal"],
            decision(automation={"state": "draft", "message": "hi"})["refusal"],
            decision(connection={"state": "connected", "channel": ""})["refusal"],
            decision(
                connection={
                    "state": "connected",
                    "channel": "x",
                    "channel_kind": "private",
                    "bot_in_channel": False,
                }
            )["refusal"],
            decision(priors=[NOW.isoformat()])["refusal"],
        }
        assert reached == set(REFUSAL_CODES) - {"missing_bot_scope"}

    def test_the_precedence_is_stated_as_a_tuple(self):
        assert REFUSAL_PRECEDENCE[0] == "no_connection"
        assert REFUSAL_PRECEDENCE[-1] == "rate_limited"

    def test_resolve_channel_refuses_when_every_source_is_empty(self):
        with pytest.raises(ChannelRequired):
            resolve_channel({"channel": ""}, {"channel": ""}, None)


# --------------------------------------------------------------------------- #
# The signal summary
# --------------------------------------------------------------------------- #


class TestTheSignalSummary:
    def test_a_signal_is_flattened_to_the_fields_the_record_shows(self):
        summary = signal_summary(
            {
                "id": "sig_1",
                "data": {
                    "type": "pricing_page_view",
                    "urgency": "high",
                    "signal_name": "Returned to pricing",
                    "rendered": {"text": "Three times."},
                },
            }
        )
        assert summary["type"] == "pricing_page_view"
        assert summary["urgency"] == "high"
        assert summary["name"] == "Returned to pricing"
        assert summary["description"] == "Three times."

    def test_a_test_send_has_an_empty_signal_summary(self):
        assert signal_summary(None)["id"] == ""


# --------------------------------------------------------------------------- #
# The engine
# --------------------------------------------------------------------------- #


class TestTheEngine:
    def test_a_connection_is_stored_and_read_back(self, notifier):
        created = connect(notifier)
        assert created["workspace"] == "acme"
        assert notifier.connection(room_id=ROOM)["channel"] == "sales-signals"

    def test_a_second_connection_in_one_room_is_refused(self, notifier):
        connect(notifier)
        with pytest.raises(DuplicateConnection):
            connect(notifier)

    def test_a_connection_can_be_revoked_and_is_then_not_the_live_one(self, notifier):
        created = connect(notifier)
        notifier.revoke(created["id"], actor="test", source=SOURCE)
        assert notifier.connection(room_id=ROOM) is None
        assert notifier.read_connection(created["id"])["state"] == "revoked"

    def test_a_connection_in_another_room_is_a_different_room(self, notifier):
        connect(notifier, room_id="room-1")
        assert notifier.connection(room_id="room-2") is None

    def test_reading_or_revoking_an_absent_connection_is_404(self, notifier):
        with pytest.raises(UnknownConnection):
            notifier.read_connection("conn_absent")
        with pytest.raises(UnknownConnection):
            notifier.revoke("conn_absent", actor="test", source=SOURCE)

    def test_a_block_is_created_updated_and_deleted(self, notifier):
        created = make_automation(notifier)
        assert created["state"] == "live"
        updated = notifier.update_automation(
            created["id"], {"state": "draft"}, actor="test", source=SOURCE
        )
        assert updated["state"] == "draft"
        notifier.delete_automation(created["id"], actor="test", source=SOURCE)
        assert notifier.automations(room_id=ROOM) == []

    def test_two_blocks_may_not_share_a_name(self, notifier):
        make_automation(notifier)
        with pytest.raises(DuplicateAutomation):
            make_automation(notifier)

    def test_an_update_that_collides_with_another_blocks_name_is_refused(self, notifier):
        make_automation(notifier, name="First")
        second = make_automation(notifier, name="Second")
        with pytest.raises(DuplicateAutomation):
            notifier.update_automation(second["id"], {"name": "First"}, actor="test", source=SOURCE)

    def test_an_update_with_a_bad_message_is_refused(self, notifier):
        created = make_automation(notifier)
        with pytest.raises(InvalidAutomation):
            notifier.update_automation(
                created["id"], {"message": "Hey <@dana>"}, actor="test", source=SOURCE
            )

    def test_reading_an_absent_block_is_404(self, notifier):
        with pytest.raises(UnknownAutomation):
            notifier.read_automation("block_absent")

    def test_a_signal_is_read_from_the_intent_signal_collection(self, notifier, store):
        signal = put_signal(store)
        assert notifier.read_signal(signal["id"])["type"] == "pricing_page_view"

    def test_a_signal_id_that_is_not_a_signal_is_404(self, notifier, store):
        other = store.create("room", {"name": "x"}, source=SOURCE)
        with pytest.raises(UnknownSignal):
            notifier.read_signal(other["id"])

    def test_a_live_dispatch_records_the_message_and_the_channel(self, notifier, store):
        data = wired(notifier, store)
        result = notifier.record_dispatch(
            {
                "kind": "live",
                "signal_id": data["signal"]["id"],
                "automation_id": data["block"]["id"],
                "customer": {"customer": {"username": "Dana"}},
            },
            room_id=ROOM,
            actor="dana",
            source=SOURCE,
        )
        assert result["state"] == "recorded"
        assert result["channel"] == "sales-signals"
        assert "Dana" in result["message"]["text"]
        assert len(notifier.dispatches(room_id=ROOM)) == 1

    def test_a_live_dispatch_without_a_signal_is_refused(self, notifier, store):
        connect(notifier)
        with pytest.raises(InvalidDispatch):
            notifier.record_dispatch(
                {"kind": "live", "customer": {}}, room_id=ROOM, actor="test", source=SOURCE
            )

    def test_an_unknown_signal_is_404(self, notifier, store):
        connect(notifier)
        with pytest.raises(UnknownSignal):
            notifier.record_dispatch(
                {"kind": "live", "signal_id": "sig_absent"}, room_id=ROOM, actor="t", source=SOURCE
            )

    def test_a_test_send_records_a_decision_with_no_signal(self, notifier, store):
        connect(notifier)
        block = make_automation(notifier)
        result = notifier.record_dispatch(
            {
                "kind": "test",
                "automation_id": block["id"],
                "customer": {"customer": {"username": "Dana"}},
            },
            room_id=ROOM,
            actor="dana",
            source=SOURCE,
        )
        assert result["kind"] == "test"
        assert result["state"] == "recorded"

    def test_the_second_live_post_is_rate_limited_and_still_written(self, notifier, store):
        data = wired(notifier, store)
        body = {
            "kind": "live",
            "signal_id": data["signal"]["id"],
            "automation_id": data["block"]["id"],
            "customer": {"customer": {"username": "Dana"}},
        }
        first = notifier.record_dispatch(body, room_id=ROOM, actor="test", source=SOURCE)
        second = notifier.record_dispatch(body, room_id=ROOM, actor="test", source=SOURCE)
        assert first["state"] == "recorded"
        assert second["state"] == "refused"
        assert second["refusal"] == "rate_limited"
        assert len(notifier.dispatches(room_id=ROOM)) == 2

    def test_a_private_channel_without_the_bot_is_refused_and_still_written(self, notifier, store):
        connect(notifier, channel="deal-desk", channel_kind="private")
        block = make_automation(notifier, channel="deal-desk")
        signal = put_signal(store)
        result = notifier.record_dispatch(
            {"kind": "live", "signal_id": signal["id"], "automation_id": block["id"]},
            room_id=ROOM,
            actor="test",
            source=SOURCE,
        )
        assert result["refusal"] == "bot_not_in_channel"
        assert len(notifier.dispatches(room_id=ROOM)) == 1

    def test_the_preview_reports_a_decision_and_writes_nothing(self, notifier, store):
        data = wired(notifier, store)
        result = notifier.decide(
            {
                "kind": "live",
                "signal_id": data["signal"]["id"],
                "automation_id": data["block"]["id"],
            },
            room_id=ROOM,
        )
        assert result["state"] == "recorded"
        assert notifier.dispatches(room_id=ROOM) == []

    def test_dispatches_are_filterable_by_kind_and_state(self, notifier, store):
        data = wired(notifier, store)
        notifier.record_dispatch(
            {
                "kind": "live",
                "signal_id": data["signal"]["id"],
                "automation_id": data["block"]["id"],
            },
            room_id=ROOM,
            actor="test",
            source=SOURCE,
        )
        notifier.record_dispatch(
            {"kind": "test", "automation_id": data["block"]["id"]},
            room_id=ROOM,
            actor="test",
            source=SOURCE,
        )
        assert len(notifier.dispatches(room_id=ROOM, kind="live")) == 1
        assert len(notifier.dispatches(room_id=ROOM, kind="test")) == 1
        assert notifier.dispatches(room_id=ROOM, state="refused") == []

    def test_reading_an_absent_dispatch_is_404(self, notifier):
        with pytest.raises(UnknownDispatch):
            notifier.read_dispatch("disp_absent")

    def test_the_summary_counts_this_features_own_collections(self, notifier, store):
        data = wired(notifier, store)
        notifier.record_dispatch(
            {
                "kind": "live",
                "signal_id": data["signal"]["id"],
                "automation_id": data["block"]["id"],
            },
            room_id=ROOM,
            actor="test",
            source=SOURCE,
        )
        summary = notifier.summary(room_id=ROOM)
        assert summary["connected"] is True
        assert summary["automations"] == 1
        assert summary["dispatch_states"] == {"recorded": 1}
        assert summary["sends_slack"] is False

    def test_every_collection_is_room_scoped(self, notifier, store):
        wired(notifier, store, room_id="room-1")
        assert notifier.connection(room_id="room-2") is None
        assert notifier.automations(room_id="room-2") == []
        assert notifier.dispatches(room_id="room-2") == []


# --------------------------------------------------------------------------- #
# Inferences and the import surface
# --------------------------------------------------------------------------- #


class TestTheDecisionRecord:
    def test_every_inference_is_named_and_reversible(self):
        assert len(INFERENCES) >= 15
        for entry in INFERENCES:
            assert {"id", "question", "reading", "why", "change", "risk"} <= set(entry), entry
        ids = [entry["id"] for entry in INFERENCES]
        assert len(ids) == len(set(ids))

    def test_the_sourced_half_is_served_beside_the_inferred_half(self):
        assert len(SOURCED) >= 6
        assert describe()["sends_slack"] is False


def test_the_domain_package_imports_nothing_but_the_store():
    """The enforced rule in ``test_features.py`` greps for the app import; this pins
    the whole import surface of the package so a new framework import is caught here."""
    package = Path(__file__).resolve().parents[1] / "dsr" / "signal_slack"
    allowed_ds = {"dsr.store", "dsr.signal_slack"}
    for module in sorted(package.glob("*.py")):
        text = module.read_text(encoding="utf-8")
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped.startswith(("import ", "from ")):
                continue
            assert "dsr.api" not in stripped, f"{module.name}: {stripped}"
            assert "sqlite3" not in stripped, f"{module.name}: {stripped}"
            for token in stripped.split():
                if token.startswith("dsr."):
                    root = ".".join(token.split(".")[:2])
                    assert root in allowed_ds, f"{module.name} imports {token}"
