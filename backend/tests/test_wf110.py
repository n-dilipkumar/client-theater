"""WF-110 domain tests: the rules, the engine, the payloads and the seeded states.

These drive :mod:`dsr.conversation_tickets` directly, with a store the test builds
and a clock the test injects. No request, no server, so a failure here names the
rule rather than the transport.

Every test builds its own rows, so none depends on another's, and the clock is
frozen rather than read from the wall: a test that asserts on "now" is a time bomb
the day it runs late.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import pytest
from dsr.conversation_tickets import (
    ConversationNotFound,
    TagNotInWorkspace,
    TeamUnknown,
    TicketConflict,
    TicketEngine,
    TicketNotFound,
    TicketRefused,
    inferences as ticket_inferences,
    rules,
    vocabulary as vocab,
)
from dsr.conversation_tickets.engine import TicketEngine as EngineClass
from dsr.db.audited import AuditedDatabase
from dsr.store import RecordStore

#: One instant, reused. Every instant a record carries is derived from it, so a
#: test never pins a date that will pass.
NOW = datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def engine(store: RecordStore) -> TicketEngine:
    return TicketEngine(store, clock=lambda: NOW)


def _conversation(store: RecordStore, subject: str = "Availability") -> str:
    record = store.create(
        vocab.CONVERSATION_COLLECTION,
        {
            "state": vocab.CONVERSATION_OPEN,
            "origin": vocab.CONVERSATION_ORIGIN_API,
            "tags": [],
            "subject": subject,
        },
        actor="dana",
        source="test",
    )
    return str(record["id"])


def _workspace(engine: TicketEngine, *, channel: str | None = "#escalations") -> dict[str, Any]:
    """A tag, an SLA policy and one live escalation trigger over every action."""

    engine.create_tag({"name": "priority customer"}, actor="dana", source="test")
    engine.create_sla_policy(
        {
            "name": "Escalation response",
            "metric": vocab.SLA_METRIC_TIME_TO_RESOLUTION,
            "target_hours": 8,
        },
        actor="dana",
        source="test",
    )
    trigger = engine.create_trigger(
        {
            "name": "Escalate a created escalation ticket",
            "kind": vocab.TRIGGER_TICKET_CREATED,
            "channel": channel,
            "conditions": {"ticket_types": [vocab.TYPE_ESCALATION]},
            "actions": [
                {"kind": vocab.ACTION_ASSIGN_TEAM, "team": vocab.TEAM_ESCALATIONS},
                {"kind": vocab.ACTION_NOTIFY_TEAM},
                {"kind": vocab.ACTION_APPLY_SLA, "policy": "Escalation response"},
                {"kind": vocab.ACTION_TAG_CONVERSATION, "tag": "priority customer"},
            ],
        },
        actor="dana",
        source="test",
    )
    engine.go_live(str(trigger["id"]), actor="dana", source="test")
    return trigger


# --------------------------------------------------------------------------- #
# Vocabulary and provenance
# --------------------------------------------------------------------------- #


def test_every_researched_term_carries_the_sentence_it_came_from():
    published = vocab.published_vocabulary()
    for key in (
        "trigger_kinds",
        "ticket_types",
        "ticket_states",
        "actions",
        "background_only",
        "conversion_gate",
        "tracker_exemption",
        "closed_payload_shape",
        "webhook_topics",
        "api_version_boundary",
    ):
        entry = published[key]
        assert entry.get("quote"), key
        assert entry.get("source", "").startswith("https://"), key


def test_the_vocabulary_is_ascii_so_the_seeder_can_print_it():
    """The seeder prints a feature's summary on a cp1252 console."""

    text = json.dumps(vocab.published_vocabulary(), ensure_ascii=False)
    assert text == text.encode("cp1252", "ignore").decode("cp1252")


def test_the_two_documented_limits_are_published_verbatim():
    published = vocab.published_vocabulary()
    assert published["background_only"]["value"] is True
    assert published["background_only"]["quote"] == vocab.BACKGROUND_ONLY
    assert published["tracker_exemption"]["quote"] == vocab.TRACKER_EXEMPTION


def test_resolved_and_closed_are_two_states_never_one():
    assert vocab.STATE_RESOLVED in vocab.TICKET_STATES
    assert vocab.STATE_CLOSED in vocab.TICKET_STATES
    assert vocab.STATE_RESOLVED != vocab.STATE_CLOSED


def test_the_conversation_vocabulary_is_reused_from_wf107():
    """A second spelling of the conversation collection would unlink every ticket."""

    from dsr.conversation_chase import vocabulary as chase

    assert vocab.CONVERSATION_COLLECTION == chase.CONVERSATIONS
    assert vocab.CONVERSATION_CLOSED == chase.STATE_CLOSED
    assert vocab.CONVERSATION_ORIGIN_API == chase.ORIGIN_API


def test_the_inferences_name_the_four_decisions_the_issue_raises():
    describe = ticket_inferences.describe()
    assert describe["count"] == len(ticket_inferences.INFERENCES)
    ids = set(describe["ids"])
    for expected in (
        ticket_inferences.DERIVED_ACTIONS_ARE_BACKGROUND_ONLY,
        ticket_inferences.DERIVED_SECOND_WORKFLOW_IS_SHIPPED,
        ticket_inferences.DERIVED_CONVERSION_GATE_IS_A_PRECONDITION,
        ticket_inferences.DERIVED_TRACKER_EXCLUSION_IS_A_SKIP,
        ticket_inferences.DERIVED_RESOLVED_AND_CLOSED_ARE_TWO_EVENTS,
    ):
        assert expected in ids
    for entry in describe["inferences"]:
        assert entry["decision"] and entry["why"] and entry["change_if"], entry["id"]
    assert describe["jev"]["audit_id"].startswith("jev-")


# --------------------------------------------------------------------------- #
# The small validators
# --------------------------------------------------------------------------- #


def test_an_unknown_team_is_refused_by_name():
    with pytest.raises(TeamUnknown):
        rules.require_team("marketing")


def test_an_unknown_ticket_type_and_state_are_refused():
    with pytest.raises(TicketRefused):
        rules.require_ticket_type("nonsense")
    with pytest.raises(TicketRefused):
        rules.require_ticket_state("nonsense")


def test_normalise_tags_deduplicates_and_drops_blanks():
    assert rules.normalise_tags(["a", " a ", "", None, "b", "b"]) == ["a", "b"]
    assert rules.normalise_tags(None) == []
    assert rules.normalise_tags("solo") == ["solo"]


def test_validate_ticket_derives_created_via_from_the_conversation():
    data = rules.validate_ticket({"ticket_type": "escalation", "conversation_id": "c1"})
    assert data["created_via"] == vocab.VIA_CONVERSATION
    assert data["conversation_id"] == "c1"
    assert rules.converted(data) is True


def test_validate_ticket_refuses_a_conversion_with_no_conversation():
    with pytest.raises(TicketRefused) as caught:
        rules.validate_ticket({"ticket_type": "escalation", "created_via": vocab.VIA_CONVERSATION})
    assert caught.value.errors["conversation_id"]


def test_validate_ticket_refuses_a_tracker_ticket_with_a_conversation():
    with pytest.raises(TicketRefused) as caught:
        rules.validate_ticket(
            {"ticket_type": "escalation", "created_via": vocab.VIA_TRACKER, "conversation_id": "c1"}
        )
    assert caught.value.errors["conversation_id"]


def test_validate_sla_policy_refuses_a_non_positive_target():
    with pytest.raises(TicketRefused):
        rules.validate_sla_policy({"name": "x", "target_hours": 0})
    with pytest.raises(TicketRefused):
        rules.validate_sla_policy({"name": "x", "target_hours": "soon"})


def test_validate_trigger_refuses_an_unknown_action_kind():
    with pytest.raises(TicketRefused) as caught:
        rules.validate_trigger({"name": "x", "actions": [{"kind": "explode"}]})
    assert "actions[0].kind" in caught.value.errors


def test_validate_trigger_requires_a_name_and_at_least_one_action():
    with pytest.raises(TicketRefused):
        rules.validate_trigger({"actions": [{"kind": vocab.ACTION_NOTIFY_TEAM}]})
    with pytest.raises(TicketRefused):
        rules.validate_trigger({"name": "x", "actions": []})


# --------------------------------------------------------------------------- #
# Trigger matching and the tracker exclusion
# --------------------------------------------------------------------------- #


def _trigger(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "name": "t",
        "kind": vocab.TRIGGER_TICKET_CREATED,
        "state": "live",
        "conditions": {"ticket_types": [vocab.TYPE_ESCALATION], "teams": [], "to_states": []},
        "actions": [{"kind": vocab.ACTION_NOTIFY_TEAM}],
    }
    base.update(overrides)
    return base


def _ticket(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "ticket_type": vocab.TYPE_ESCALATION,
        "created_via": vocab.VIA_CONVERSATION,
    }
    base.update(overrides)
    return base


def test_a_draft_trigger_fires_nothing():
    result = rules.trigger_matches(
        _trigger(state="draft"), _ticket(), event_kind=vocab.TRIGGER_TICKET_CREATED
    )
    assert result == {"matched": False, "reason": vocab.SKIP_TRIGGER_NOT_LIVE}


def test_a_tracker_ticket_is_skipped_before_the_conditions():
    """The exclusion wins even when the ticket matches the type condition."""

    result = rules.trigger_matches(
        _trigger(), _ticket(created_via=vocab.VIA_TRACKER), event_kind=vocab.TRIGGER_TICKET_CREATED
    )
    assert result == {"matched": False, "reason": vocab.SKIP_TRACKER_CREATED}


def test_the_tracker_exclusion_applies_only_to_the_created_trigger():
    """The researched sentence names the 'ticket is created' trigger alone."""

    result = rules.trigger_matches(
        _trigger(kind=vocab.TRIGGER_STATE_CHANGED),
        _ticket(created_via=vocab.VIA_TRACKER, state=vocab.STATE_RESOLVED),
        event_kind=vocab.TRIGGER_STATE_CHANGED,
        to_state=vocab.STATE_RESOLVED,
    )
    assert result == {"matched": True, "reason": None}


def test_a_ticket_outside_the_type_condition_is_skipped():
    result = rules.trigger_matches(
        _trigger(), _ticket(ticket_type=vocab.TYPE_BILLING), event_kind=vocab.TRIGGER_TICKET_CREATED
    )
    assert result["reason"] == vocab.SKIP_TYPE_NOT_MATCHED


def test_a_state_condition_filters_the_transition():
    trigger = _trigger(
        kind=vocab.TRIGGER_STATE_CHANGED, conditions={"to_states": [vocab.STATE_RESOLVED]}
    )
    assert rules.trigger_matches(
        trigger, _ticket(), event_kind=vocab.TRIGGER_STATE_CHANGED, to_state=vocab.STATE_RESOLVED
    )["matched"]
    assert (
        rules.trigger_matches(
            trigger,
            _ticket(),
            event_kind=vocab.TRIGGER_STATE_CHANGED,
            to_state=vocab.STATE_IN_PROGRESS,
        )["reason"]
        == vocab.SKIP_STATE_NOT_MATCHED
    )


def test_an_empty_condition_matches_every_ticket():
    trigger = _trigger(conditions={"ticket_types": []})
    assert rules.trigger_matches(
        trigger, _ticket(ticket_type=vocab.TYPE_BILLING), event_kind=vocab.TRIGGER_TICKET_CREATED
    )["matched"]


# --------------------------------------------------------------------------- #
# The converted-conversation gate
# --------------------------------------------------------------------------- #


def test_the_gate_names_only_tag_conversation_and_apply_sla():
    assert rules.action_requires_conversion(vocab.ACTION_TAG_CONVERSATION)
    assert rules.action_requires_conversion(vocab.ACTION_APPLY_SLA)
    assert not rules.action_requires_conversion(vocab.ACTION_NOTIFY_TEAM)
    assert not rules.action_requires_conversion(vocab.ACTION_ASSIGN_TEAM)


def test_a_gated_action_on_an_unconverted_ticket_is_skipped_not_refused():
    action = {"kind": vocab.ACTION_APPLY_SLA, "policy": "Escalation response"}
    ticket = _ticket(conversation_id=None)
    assert rules.action_skip(action, ticket) == vocab.ACTION_SKIPPED_NOT_CONVERTED


def test_a_gated_action_on_a_converted_ticket_may_run():
    action = {"kind": vocab.ACTION_TAG_CONVERSATION, "tag": "priority customer"}
    tags = [{"data": {"name": "priority customer"}}]
    ticket = _ticket(conversation_id="c1")
    assert rules.action_skip(action, ticket, known_tags=tags) is None


def test_notify_without_an_owning_team_is_skipped():
    ticket = _ticket(team=None)
    assert (
        rules.action_skip({"kind": vocab.ACTION_NOTIFY_TEAM}, ticket)
        == vocab.ACTION_SKIPPED_UNKNOWN_TEAM
    )


def test_an_unknown_policy_and_an_unknown_tag_are_skipped():
    ticket = _ticket(conversation_id="c1")
    assert (
        rules.action_skip({"kind": vocab.ACTION_APPLY_SLA, "policy": "ghost"}, ticket)
        == vocab.ACTION_SKIPPED_UNKNOWN_POLICY
    )
    assert (
        rules.action_skip(
            {"kind": vocab.ACTION_TAG_CONVERSATION, "tag": "ghost"}, ticket, known_tags=[]
        )
        == vocab.ACTION_SKIPPED_UNKNOWN_TAG
    )


def test_resolve_tag_is_case_insensitive_and_returns_the_stored_spelling():
    workspace = [{"data": {"name": "Priority Customer"}}]
    assert rules.resolve_tag("priority customer", workspace) == "Priority Customer"
    with pytest.raises(TagNotInWorkspace):
        rules.resolve_tag("ghost", workspace)


# --------------------------------------------------------------------------- #
# The two events and the two payload shapes
# --------------------------------------------------------------------------- #


def test_a_transition_to_resolved_emits_state_updated_and_resolved():
    assert rules.state_topics(vocab.STATE_IN_PROGRESS, vocab.STATE_RESOLVED) == [
        vocab.TOPIC_STATE_UPDATED,
        vocab.TOPIC_RESOLVED,
    ]


def test_a_plain_transition_emits_only_state_updated():
    assert rules.state_topics(vocab.STATE_SUBMITTED, vocab.STATE_IN_PROGRESS) == [
        vocab.TOPIC_STATE_UPDATED
    ]


def test_resolved_and_closed_are_two_topics():
    assert vocab.TOPIC_RESOLVED != vocab.TOPIC_CLOSED
    assert vocab.TOPIC_CLOSED not in {vocab.TOPIC_TICKET_CREATED, vocab.TOPIC_RESOLVED}


def test_ticket_closed_nests_the_ticket_under_a_ticket_field():
    payload = rules.webhook_payload(vocab.TOPIC_CLOSED, {"id": "t1", "ticket_type": "escalation"})
    assert payload["data"]["item"]["ticket"]["id"] == "t1"
    assert set(payload["data"]["item"]) == {"ticket"}


def test_every_other_topic_places_the_fields_directly_on_data_item():
    payload = rules.webhook_payload(vocab.TOPIC_STATE_UPDATED, {"id": "t1", "state": "resolved"})
    assert payload["data"]["item"]["id"] == "t1"
    assert "ticket" not in payload["data"]["item"]


def test_one_reader_reads_both_shapes():
    nested = rules.webhook_payload(vocab.TOPIC_CLOSED, {"id": "t1", "state": "closed"})
    flat = rules.webhook_payload(vocab.TOPIC_RESOLVED, {"id": "t1", "state": "resolved"})
    assert rules.read_webhook_ticket(nested)["id"] == "t1"
    assert rules.read_webhook_ticket(flat)["id"] == "t1"


def test_the_version_boundary_gates_the_nested_shape():
    assert rules.uses_nested_shape(vocab.TOPIC_CLOSED, "2.15") is True
    assert rules.uses_nested_shape(vocab.TOPIC_CLOSED, "2.14") is False
    assert rules.uses_nested_shape(vocab.TOPIC_STATE_UPDATED, "2.15") is False
    assert rules.parse_api_version("2.15+") == (2, 15)


# --------------------------------------------------------------------------- #
# The engine end to end
# --------------------------------------------------------------------------- #


def test_a_converted_ticket_runs_every_action(engine: TicketEngine, store: RecordStore):
    _workspace(engine)
    conversation_id = _conversation(store)
    outcome = engine.create_ticket(
        {
            "subject": "Escalate",
            "ticket_type": vocab.TYPE_ESCALATION,
            "conversation_id": conversation_id,
        },
        actor="dana",
        source="test",
    )
    assert outcome["ticket"]["converted"] is True
    assert outcome["ticket"]["owning_team"] == vocab.TEAM_ESCALATIONS
    actions = outcome["evaluations"][0]["data"]["actions"]
    assert [action["outcome"] for action in actions] == [vocab.ACTION_RAN] * 4
    assert outcome["ticket"]["sla"]["policy"] == "Escalation response"
    assert len(outcome["notifications"]) == 1
    # The linked conversation is tagged, which is the researched action.
    conversation = store.get(conversation_id)
    assert "priority customer" in conversation["data"]["tags"]


def test_a_tracker_ticket_is_created_and_the_trigger_is_skipped(
    engine: TicketEngine,
):
    _workspace(engine)
    outcome = engine.create_ticket(
        {
            "subject": "Tracker",
            "ticket_type": vocab.TYPE_ESCALATION,
            "created_via": vocab.VIA_TRACKER,
        },
        actor="dana",
        source="test",
    )
    assert outcome["evaluations"][0]["data"]["reason"] == vocab.SKIP_TRACKER_CREATED
    assert outcome["ticket"]["created_via"] == vocab.VIA_TRACKER
    assert outcome["notifications"] == []


def test_an_unconverted_ticket_skips_only_the_gated_actions(engine: TicketEngine):
    _workspace(engine)
    outcome = engine.create_ticket(
        {
            "subject": "Inbound escalation",
            "ticket_type": vocab.TYPE_ESCALATION,
            "created_via": vocab.VIA_INBOUND,
            "team": vocab.TEAM_SALES,
        },
        actor="dana",
        source="test",
    )
    actions = outcome["evaluations"][0]["data"]["actions"]
    outcomes = {action["kind"]: action["outcome"] for action in actions}
    assert outcomes[vocab.ACTION_ASSIGN_TEAM] == vocab.ACTION_RAN
    assert outcomes[vocab.ACTION_NOTIFY_TEAM] == vocab.ACTION_RAN
    assert outcomes[vocab.ACTION_APPLY_SLA] == vocab.ACTION_SKIPPED_NOT_CONVERTED
    assert outcomes[vocab.ACTION_TAG_CONVERSATION] == vocab.ACTION_SKIPPED_NOT_CONVERTED


def test_a_state_change_emits_resolved_and_fires_the_second_trigger(
    engine: TicketEngine, store: RecordStore
):
    _workspace(engine)
    engine.create_trigger(
        {
            "name": "Tell the owner",
            "kind": vocab.TRIGGER_STATE_CHANGED,
            "channel": "#escalations",
            "conditions": {"to_states": [vocab.STATE_RESOLVED]},
            "actions": [{"kind": vocab.ACTION_NOTIFY_TEAM}],
        },
        actor="dana",
        source="test",
    )
    for trigger in engine.triggers(kind=vocab.TRIGGER_STATE_CHANGED):
        engine.go_live(str(trigger["id"]), actor="dana", source="test")
    ticket = engine.create_ticket(
        {
            "subject": "Escalate",
            "ticket_type": vocab.TYPE_ESCALATION,
            "conversation_id": _conversation(store),
        },
        actor="dana",
        source="test",
    )
    outcome = engine.change_state(
        str(ticket["ticket"]["id"]), {"state": vocab.STATE_RESOLVED}, actor="dana", source="test"
    )
    topics = [webhook["data"]["topic"] for webhook in outcome["webhooks"]]
    assert topics == [vocab.TOPIC_STATE_UPDATED, vocab.TOPIC_RESOLVED]
    assert outcome["notifications"], "the state-changed trigger notified the owning team"
    assert outcome["ticket"]["state"] == vocab.STATE_RESOLVED


def test_closing_the_conversation_emits_the_nested_closed_payload(
    engine: TicketEngine, store: RecordStore
):
    _workspace(engine)
    conversation_id = _conversation(store)
    ticket = engine.create_ticket(
        {"subject": "x", "ticket_type": vocab.TYPE_ESCALATION, "conversation_id": conversation_id},
        actor="dana",
        source="test",
    )
    outcome = engine.close_conversation(
        str(ticket["ticket"]["id"]), {}, actor="dana", source="test"
    )
    assert outcome["webhooks"][0]["data"]["topic"] == vocab.TOPIC_CLOSED
    assert outcome["webhooks"][0]["data"]["shape"] == vocab.PAYLOAD_NESTED
    assert outcome["conversation"]["state"] == vocab.CONVERSATION_CLOSED
    # The ticket's state is untouched: closed is not a state transition.
    assert outcome["ticket"]["state"] == vocab.STATE_SUBMITTED
    assert outcome["ticket"]["conversation_closed"] is True


def test_closing_the_conversation_of_an_unconverted_ticket_is_refused(engine: TicketEngine):
    ticket = engine.create_ticket(
        {"subject": "x", "ticket_type": vocab.TYPE_ESCALATION, "created_via": vocab.VIA_API},
        actor="dana",
        source="test",
    )
    with pytest.raises(TicketRefused):
        engine.close_conversation(str(ticket["ticket"]["id"]), {}, actor="dana", source="test")


def test_linking_a_conversation_converts_the_ticket(engine: TicketEngine, store: RecordStore):
    ticket = engine.create_ticket(
        {"subject": "x", "ticket_type": vocab.TYPE_ESCALATION, "created_via": vocab.VIA_API},
        actor="dana",
        source="test",
    )
    assert ticket["ticket"]["converted"] is False
    linked = engine.link_conversation(
        str(ticket["ticket"]["id"]),
        {"conversation_id": _conversation(store)},
        actor="dana",
        source="test",
    )
    assert linked["ticket"]["converted"] is True
    unlinked = engine.unlink_conversation(str(ticket["ticket"]["id"]), actor="dana", source="test")
    assert unlinked["ticket"]["converted"] is False


def test_linking_an_absent_conversation_is_not_found(engine: TicketEngine):
    ticket = engine.create_ticket(
        {"subject": "x", "ticket_type": vocab.TYPE_ESCALATION, "created_via": vocab.VIA_API},
        actor="dana",
        source="test",
    )
    with pytest.raises(ConversationNotFound):
        engine.link_conversation(
            str(ticket["ticket"]["id"]), {"conversation_id": "ghost"}, actor="dana", source="test"
        )


def test_unlinking_a_ticket_with_no_conversation_is_a_conflict(engine: TicketEngine):
    ticket = engine.create_ticket(
        {"subject": "x", "ticket_type": vocab.TYPE_ESCALATION, "created_via": vocab.VIA_API},
        actor="dana",
        source="test",
    )
    with pytest.raises(TicketConflict):
        engine.unlink_conversation(str(ticket["ticket"]["id"]), actor="dana", source="test")


def test_a_duplicate_tag_name_is_a_conflict(engine: TicketEngine):
    engine.create_tag({"name": "priority customer"}, actor="dana", source="test")
    with pytest.raises(TicketConflict):
        engine.create_tag({"name": "PRIORITY customer"}, actor="dana", source="test")


def test_a_missing_trigger_is_not_found(engine: TicketEngine):
    with pytest.raises(TicketNotFound):
        engine.require_trigger("ghost")
    with pytest.raises(TicketNotFound):
        engine.require_ticket("ghost")


def test_going_live_twice_is_a_conflict(engine: TicketEngine):
    trigger = _workspace(engine)
    with pytest.raises(TicketConflict):
        engine.go_live(str(trigger["id"]), actor="dana", source="test")


def test_editing_a_trigger_revalidates_and_keeps_it_live(engine: TicketEngine):
    trigger = _workspace(engine)
    updated = engine.update_trigger(
        str(trigger["id"]),
        {"conditions": {"ticket_types": [vocab.TYPE_SUPPORT]}},
        actor="dana",
        source="test",
    )
    assert updated["data"]["state"] == "live"
    assert updated["data"]["conditions"]["ticket_types"] == [vocab.TYPE_SUPPORT]


def test_a_reply_records_an_admin_and_emits_the_assigned_webhook(engine: TicketEngine):
    ticket = engine.create_ticket(
        {"subject": "x", "ticket_type": vocab.TYPE_ESCALATION, "created_via": vocab.VIA_API},
        actor="dana",
        source="test",
    )
    outcome = engine.reply(
        str(ticket["ticket"]["id"]), {"body": "On it", "admin": "dana"}, actor="dana", source="test"
    )
    assert outcome["ticket"]["admin"] == "dana"
    assert outcome["webhooks"][0]["data"]["topic"] == vocab.TOPIC_ADMIN_ASSIGNED


def test_a_reply_without_a_body_is_refused(engine: TicketEngine):
    ticket = engine.create_ticket(
        {"subject": "x", "ticket_type": vocab.TYPE_ESCALATION, "created_via": vocab.VIA_API},
        actor="dana",
        source="test",
    )
    with pytest.raises(TicketRefused):
        engine.reply(str(ticket["ticket"]["id"]), {}, actor="dana", source="test")


def test_tags_are_added_and_removed_on_a_ticket(engine: TicketEngine):
    engine.create_tag({"name": "priority customer"}, actor="dana", source="test")
    ticket = engine.create_ticket(
        {"subject": "x", "ticket_type": vocab.TYPE_ESCALATION, "created_via": vocab.VIA_API},
        actor="dana",
        source="test",
    )
    ticket_id = str(ticket["ticket"]["id"])
    tagged = engine.add_tag(ticket_id, {"tag": "priority customer"}, actor="dana", source="test")
    assert tagged["data"]["tags"] == ["priority customer"]
    untagged = engine.remove_tag(ticket_id, "priority customer", actor="dana", source="test")
    assert untagged["data"]["tags"] == []
    with pytest.raises(TicketConflict):
        engine.remove_tag(ticket_id, "priority customer", actor="dana", source="test")


def test_change_type_and_search(engine: TicketEngine):
    ticket = engine.create_ticket(
        {
            "subject": "Renewal question",
            "ticket_type": vocab.TYPE_ESCALATION,
            "created_via": vocab.VIA_API,
        },
        actor="dana",
        source="test",
    )
    changed = engine.change_type(
        str(ticket["ticket"]["id"]),
        {"ticket_type": vocab.TYPE_BILLING},
        actor="dana",
        source="test",
    )
    assert changed["data"]["ticket_type"] == vocab.TYPE_BILLING
    assert engine.search({"query": "renewal"})[0]["id"] == ticket["ticket"]["id"]
    assert engine.search({"ticket_type": vocab.TYPE_SUPPORT}) == []


def test_the_summary_counts_the_un_notified_ticket(engine: TicketEngine):
    """The non-success state: a ticket created with no owning-team notification."""

    _workspace(engine)
    engine.create_ticket(
        {
            "subject": "Tracker",
            "ticket_type": vocab.TYPE_ESCALATION,
            "created_via": vocab.VIA_TRACKER,
        },
        actor="dana",
        source="test",
    )
    summary = engine.summary()
    assert summary["tickets"] == 1
    assert summary["notified_tickets"] == 0
    assert summary["tickets_without_a_notification"] == 1


def test_the_activity_log_records_the_creation_and_the_webhook(engine: TicketEngine):
    ticket = engine.create_ticket(
        {"subject": "x", "ticket_type": vocab.TYPE_ESCALATION, "created_via": vocab.VIA_API},
        actor="dana",
        source="test",
    )
    activity = engine.activity(str(ticket["ticket"]["id"]))
    kinds = {record["data"]["kind"] for record in activity["activity"]}
    assert vocab.ACTIVITY_TICKET_CREATED in kinds
    assert vocab.ACTIVITY_WEBHOOK in kinds


def test_an_activity_read_for_a_missing_ticket_is_not_found(engine: TicketEngine):
    with pytest.raises(TicketNotFound):
        engine.activity("ghost")


def test_an_unknown_action_kind_raises_rather_than_succeeding(engine: TicketEngine):
    """A drift between validate_trigger and action_skip must not read as success."""

    ticket = engine.create_ticket(
        {"subject": "x", "ticket_type": vocab.TYPE_ESCALATION, "created_via": vocab.VIA_API},
        actor="dana",
        source="test",
    )
    with pytest.raises(TicketRefused):
        engine._run_action(
            {"id": "trig", "data": {"channel": None, "goal": ""}},
            {"kind": "teleport"},
            engine.require_ticket(str(ticket["ticket"]["id"])),
            source="test",
            actor="dana",
        )


# --------------------------------------------------------------------------- #
# The seed
# --------------------------------------------------------------------------- #


def test_the_seed_returns_a_cp1252_string_and_names_a_non_success_state():
    db = AuditedDatabase(":memory:", actor="test")
    try:
        from dsr.features.wf110_escalate_a_converted_conversation_into_an_owned_ticket import seed

        text = seed(db, {"now": NOW, "room_ids": []})
        assert isinstance(text, str) and text
        assert text == text.encode("cp1252", "ignore").decode("cp1252")
        # The issue requires at least one state that is not a success.
        assert "no owning-team notification" in text
        assert "0 with no owning-team notification" not in text
        print(text)
    finally:
        db.close()


def test_the_seed_reuses_a_room_when_the_seeder_hands_one_in():
    db = AuditedDatabase(":memory:", actor="test")
    try:
        from dsr.features.wf110_escalate_a_converted_conversation_into_an_owned_ticket import seed

        room = db.create("room", {"name": "Existing"}, actor="dana", source="test")
        text = seed(db, {"now": NOW, "room_ids": [(room["id"], "Existing")]})
        assert "1 room(s) from the seeder" in text
    finally:
        db.close()


def test_the_engine_module_never_imports_the_app_or_sqlite_directly():
    """The domain imports the store and nothing that opens SQLite on its own."""

    import pathlib

    package = pathlib.Path(EngineClass.__module__.replace(".", "/") + ".py")
    root = pathlib.Path(__file__).resolve().parents[1]
    text = (root / package).read_text(encoding="utf-8")
    assert "dsr.api" not in text
    assert "import sqlite3" not in text


# --------------------------------------------------------------------------- #
# The errors, and the branches the flow tests do not reach
# --------------------------------------------------------------------------- #


def test_every_error_type_carries_its_status_and_a_serialisable_body():
    from dsr.conversation_tickets import errors as error_module

    refused = TicketRefused("bad value", {"field": "required"})
    assert refused.status == 400
    assert refused.to_dict()["errors"] == {"field": "required"}
    conflict = TicketConflict("already live", code="already_live")
    assert conflict.status == 409
    assert conflict.to_dict()["error"] == "already_live"
    missing = TicketNotFound("no such row", kind="trigger")
    assert missing.status == 404
    assert missing.to_dict() == {"error": "trigger_not_found", "detail": "no such row"}
    conversation = ConversationNotFound("no such conversation")
    assert conversation.to_dict()["error"] == "conversation_not_found"
    assert TeamUnknown("x").to_dict()["error"] == "team_unknown"
    assert TagNotInWorkspace("x").to_dict()["error"] == "tag_not_in_workspace"
    assert error_module.error_codes()["ticket_not_found"]["status"] == 404


def test_parsing_an_instant_tolerates_a_naive_value_and_a_bad_one():
    assert rules.parse_instant(None) is None
    assert rules.parse_instant("not-a-date") is None
    naive = rules.parse_instant("2026-10-13T12:00:00")
    assert naive is not None and naive.tzinfo is not None


def test_require_created_via_refuses_an_unknown_origin():
    with pytest.raises(TicketRefused):
        rules.require_created_via("carrier-pigeon")


def test_normalise_tags_refuses_a_value_that_is_not_iterable():
    with pytest.raises(TicketRefused):
        rules.normalise_tags(42)


def test_validate_sla_policy_refuses_an_unknown_metric():
    with pytest.raises(TicketRefused):
        rules.validate_sla_policy({"name": "x", "metric": "vibes"})


def test_validate_trigger_refuses_unknown_conditions():
    with pytest.raises(TicketRefused) as caught:
        rules.validate_trigger(
            {
                "name": "x",
                "actions": [{"kind": vocab.ACTION_NOTIFY_TEAM}],
                "conditions": {"teams": ["marketing"], "to_states": ["nonsense"]},
            }
        )
    assert "teams" in caught.value.errors
    assert "to_states" in caught.value.errors


def test_an_duplicate_sla_policy_is_a_conflict(engine: TicketEngine):
    engine.create_sla_policy(
        {"name": "Escalation response", "target_hours": 8}, actor="dana", source="test"
    )
    with pytest.raises(TicketConflict):
        engine.create_sla_policy(
            {"name": "escalation RESPONSE", "target_hours": 4}, actor="dana", source="test"
        )


def test_a_required_tag_and_policy_report_not_found(engine: TicketEngine):
    with pytest.raises(TagNotInWorkspace):
        engine.require_tag("ghost")
    with pytest.raises(TicketNotFound):
        engine.require_sla_policy("ghost")


def test_the_reads_filter_by_ticket_and_topic(engine: TicketEngine):
    ticket = engine.create_ticket(
        {"subject": "x", "ticket_type": vocab.TYPE_ESCALATION, "created_via": vocab.VIA_API},
        actor="dana",
        source="test",
    )
    ticket_id = str(ticket["ticket"]["id"])
    assert engine.webhooks(ticket_id=ticket_id)["count"] >= 1
    assert engine.webhooks(topic=vocab.TOPIC_TICKET_CREATED)["count"] == 1
    assert engine.evaluations(ticket_id=ticket_id)["count"] == 0
    assert engine.notifications(ticket_id=ticket_id)["count"] == 0
    assert engine.catalog()["tickets"][0]["id"] == ticket_id


def test_a_tag_action_whose_conversation_vanished_is_skipped(
    engine: TicketEngine, store: RecordStore
):
    """The gate passed, but the conversation row is gone: a skip, not a crash."""

    _workspace(engine)
    conversation_id = _conversation(store)
    ticket = engine.create_ticket(
        {"subject": "x", "ticket_type": vocab.TYPE_ESCALATION, "conversation_id": conversation_id},
        actor="dana",
        source="test",
    )
    store.delete(conversation_id, actor="dana", source="test", hard=True)
    result = engine._run_action(
        {"id": "trig", "data": {"channel": "#x", "goal": ""}},
        {"kind": vocab.ACTION_TAG_CONVERSATION, "tag": "priority customer"},
        engine.require_ticket(str(ticket["ticket"]["id"])),
        source="test",
        actor="dana",
    )
    assert result["result"]["outcome"] == vocab.ACTION_SKIPPED_NOT_CONVERTED
