"""WF-119: the envelope, the resume rules, the hold window and the gate engine.

The rules in ``dsr.approval_gate.rules`` are pure, so most of this file tests them
without a database: whether an envelope carries the documented shape is a fact
about a dict, and whether a hold has lapsed is a fact about two timestamps. Every
moment is passed in, so no test here can pass or fail depending on when CI runs.

What is tested here, and why each matters:

* the documented envelope - ``extras`` present, three non-empty string fields -
  because that object is the correlation key and a partial key is not a key;
* the correlation key as dotted query paths and the all-three-fields match, so a
  resume cannot resolve a hold on one matching field;
* the branch is verified against the gate rather than trusted from the body, and
  a body outcome that disagrees with the URL is refused rather than one source
  silently winning;
* the four-case resume decision (ok, replay, conflict, expired), enumerated, so a
  caller cannot collapse an idempotent retry and a decision flip into one answer;
* the derived hold window at both boundaries, because a window with no ceiling is
  a trap and one with no floor is not a pause;
* a resumed hold never lapses, because expiry would overwrite a recorded decision
  with an inference;
* the engine against a real temporary database, because the audited wrapper and
  the stored envelope are where a pure rule test cannot see a defect;
* the seed, because its return string is printed on a Windows console and one
  non-encodable character once broke the whole seeder.
"""

from __future__ import annotations

import ast
import inspect
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from dsr.approval_gate import rules, vocabulary as vocab
from dsr.approval_gate.engine import ApprovalGate, seed
from dsr.approval_gate.errors import (
    REFUSAL_STATUS,
    ApprovalGateRefusal,
    BranchUnknown,
    EnvelopeInvalid,
    GateInvalid,
    GateNotFound,
    HoldConflict,
    HoldExpired,
    HoldNotFound,
    KeyTaken,
    OutcomeConflict,
    PauseDisabled,
    ResumeConflict,
)
from dsr.store import RecordStore

DOMAIN_PACKAGE = "dsr.approval_gate"

BASE = datetime(2026, 10, 14, 9, 0, tzinfo=timezone.utc)

EXTRAS = {"locationId": "loc_northwind", "contactId": "con_1", "workflowId": "wf-demo"}
BRANCHES = {vocab.OUTCOME_APPROVED: "branch_yes", vocab.OUTCOME_REFUSED: "branch_no"}


def envelope(**overrides) -> dict:
    body = {
        vocab.ENVELOPE_DATA: {"amount": 5, "owner": "dana"},
        vocab.ENVELOPE_EXTRAS: dict(EXTRAS),
        vocab.ENVELOPE_META: {vocab.META_KEY: "proposal-approval", vocab.META_VERSION: 1},
    }
    body.update(overrides)
    return body


def gate_data(**overrides) -> dict:
    body = {
        "key": "send_proposal",
        "workflow_id": "wf-demo",
        "action_key": "pause_for_approval",
        vocab.BRANCHES_KEY: dict(BRANCHES),
    }
    body.update(overrides)
    return body


def hold(state: str = vocab.STATE_HELD, **overrides) -> dict:
    body = {
        "state": state,
        "outcome": None,
        "branch": None,
        "expires_at": rules.to_iso(BASE + timedelta(hours=1)),
        vocab.ENVELOPE_EXTRAS: dict(EXTRAS),
    }
    body.update(overrides)
    return {"data": body}


@pytest.fixture
def engine(store: RecordStore) -> ApprovalGate:
    return ApprovalGate(store, now=lambda: BASE)


@pytest.fixture
def configured(engine: ApprovalGate) -> dict:
    return engine.create_gate(
        key="send_proposal",
        workflow_id="wf-demo",
        action_key="pause_for_approval",
        name="Approve before the proposal is sent",
        pause_execution=True,
        branches=dict(BRANCHES),
        actor="dana",
        source="test",
    )


# --------------------------------------------------------------------------- #
# Keys and the hold window
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("key", ["a", "send_proposal", "wf-119", "a1"])
def test_gate_keys_that_address_a_url_are_accepted(key):
    assert rules.key_problem(key) is None


@pytest.mark.parametrize("key", ["", "Send", "-lead", "trail-", "has space", None, 7, "a" * 65])
def test_gate_keys_that_would_break_a_path_segment_are_refused(key):
    assert rules.key_problem(key) is not None


def test_the_hold_window_floor_is_a_pause_and_not_an_instant_expiry():
    assert rules.hold_seconds_problem(vocab.MIN_HOLD_SECONDS) is None
    assert rules.hold_seconds_problem(vocab.MIN_HOLD_SECONDS - 1) is not None


def test_the_hold_window_ceiling_is_a_pause_and_not_a_trap():
    assert rules.hold_seconds_problem(vocab.MAX_HOLD_SECONDS) is None
    assert rules.hold_seconds_problem(vocab.MAX_HOLD_SECONDS + 1) is not None


def test_an_absent_hold_window_takes_the_derived_default():
    assert rules.hold_seconds_problem(None) is None
    assert rules.hold_seconds({}) == vocab.DEFAULT_HOLD_SECONDS
    assert vocab.DEFAULT_HOLD_SECONDS == 259_200


def test_the_derived_default_is_three_days():
    """The derivation is a weekend plus one working day."""
    assert vocab.DEFAULT_HOLD_SECONDS == 3 * 24 * 60 * 60
    assert rules.hold_seconds({"hold_seconds": 60}) == vocab.DEFAULT_HOLD_SECONDS


def test_a_boolean_hold_window_is_not_a_number_of_seconds():
    assert rules.hold_seconds_problem(True) is not None


def test_a_custom_window_is_kept():
    assert rules.hold_seconds({"hold_seconds": 600}) == 600


# --------------------------------------------------------------------------- #
# The gate payload
# --------------------------------------------------------------------------- #


def test_a_complete_gate_payload_has_no_problems():
    assert rules.gate_errors(gate_data()) == []


@pytest.mark.parametrize(
    "payload",
    [
        gate_data(workflow_id=""),
        gate_data(action_key=None),
        gate_data(branches={"approved": "branch_yes"}),
        gate_data(branches={"approved": "  ", "refused": "x"}),
        gate_data(branches="branch_yes"),
        gate_data(hold_seconds=1),
        gate_data(pause_execution="yes"),
    ],
)
def test_each_incomplete_gate_payload_is_reported(payload):
    assert rules.gate_errors(payload) != []


def test_the_branch_map_drops_a_blank_entry_rather_than_keeping_it():
    assert rules.branch_map({vocab.BRANCHES_KEY: {"approved": "yes", "refused": " "}}) == {
        "approved": "yes"
    }
    assert rules.branch_map({vocab.BRANCHES_KEY: "yes"}) == {}


# --------------------------------------------------------------------------- #
# The documented envelope
# --------------------------------------------------------------------------- #


def test_a_full_envelope_has_no_problems():
    assert rules.envelope_problems(envelope()) == []


def test_an_envelope_may_omit_data_and_meta():
    body = {vocab.ENVELOPE_EXTRAS: dict(EXTRAS)}
    assert rules.envelope_problems(body) == []


@pytest.mark.parametrize("missing", vocab.EXTRAS_KEYS)
def test_every_extras_field_is_required(missing):
    extras = {k: v for k, v in EXTRAS.items() if k != missing}
    problems = rules.envelope_problems(envelope(**{vocab.ENVELOPE_EXTRAS: extras}))
    assert any(missing in problem for problem in problems)


def test_a_blank_extras_field_is_not_a_key():
    extras = {**EXTRAS, vocab.EXTRAS_CONTACT: "   "}
    problems = rules.envelope_problems(envelope(**{vocab.ENVELOPE_EXTRAS: extras}))
    assert problems


def test_extras_that_are_not_an_object_are_refused():
    assert rules.envelope_problems(envelope(**{vocab.ENVELOPE_EXTRAS: "con_1"})) != []


def test_data_that_is_not_an_object_is_refused():
    assert rules.envelope_problems(envelope(**{vocab.ENVELOPE_DATA: [1, 2]})) != []


def test_meta_is_optional_but_a_present_key_is_required():
    assert rules.envelope_problems(envelope(**{vocab.ENVELOPE_META: {}})) != []
    assert rules.envelope_problems(envelope(**{vocab.ENVELOPE_META: {"key": "k"}})) == []


def test_a_missing_meta_version_takes_the_documented_default():
    assert rules.normalise_meta(None)[vocab.META_VERSION] == vocab.DEFAULT_META_VERSION
    assert rules.normalise_meta({vocab.META_KEY: "k"})[vocab.META_VERSION] == 1
    assert (
        rules.normalise_meta({vocab.META_KEY: "k", vocab.META_VERSION: 4})[vocab.META_VERSION] == 4
    )


def test_the_correlation_key_reads_as_dotted_paths_for_the_dynamic_index():
    assert rules.extras_where(EXTRAS) == {
        "extras.locationId": "loc_northwind",
        "extras.contactId": "con_1",
        "extras.workflowId": "wf-demo",
    }


def test_the_correlation_key_is_trimmed_before_it_is_compared():
    assert rules.normalise_extras({k: f" {v} " for k, v in EXTRAS.items()}) == dict(EXTRAS)


def test_a_hold_matches_the_key_only_when_all_three_fields_match():
    subject = hold(**{vocab.ENVELOPE_EXTRAS: dict(EXTRAS)})
    assert rules.extras_match(subject, EXTRAS) is True
    assert rules.extras_match(subject, {**EXTRAS, vocab.EXTRAS_CONTACT: "con_2"}) is False
    assert rules.extras_match({"data": {}}, EXTRAS) is False


# --------------------------------------------------------------------------- #
# Time
# --------------------------------------------------------------------------- #


def test_a_trailing_z_is_read_as_utc():
    assert rules.parse_time("2026-10-14T09:00:00Z") == BASE


def test_a_naive_timestamp_is_read_as_utc_not_as_local_time():
    parsed = rules.parse_time("2026-10-14T09:00:00")
    assert parsed is not None
    assert parsed.utcoffset() == timedelta(0)


def test_an_unparseable_timestamp_is_none_not_an_exception():
    assert rules.parse_time("last tuesday") is None
    assert rules.parse_time(None) is None


def test_a_hold_expires_exactly_at_its_deadline():
    subject = hold(expires_at=rules.to_iso(BASE))
    assert rules.is_expired(subject, BASE) is True
    assert rules.is_expired(subject, BASE - timedelta(seconds=1)) is False


def test_a_resumed_hold_never_lapses():
    subject = hold(state=vocab.STATE_RESUMED, expires_at=rules.to_iso(BASE - timedelta(days=9)))
    assert rules.is_expired(subject, BASE) is False


def test_a_hold_without_a_deadline_is_not_treated_as_expired():
    assert rules.is_expired({"data": {"state": vocab.STATE_HELD}}, BASE) is False


# --------------------------------------------------------------------------- #
# The two resume rules
# --------------------------------------------------------------------------- #


def test_the_url_names_the_outcome_when_the_body_is_silent():
    assert rules.resolve_outcome("approved", None) == ("approved", None)


def test_a_body_that_agrees_with_the_url_is_accepted():
    assert rules.resolve_outcome("refused", "refused") == ("refused", None)


def test_a_body_that_disagrees_with_the_url_is_refused_not_preferred():
    outcome, problem = rules.resolve_outcome("approved", "refused")
    assert outcome is None
    assert problem


def test_an_unknown_url_outcome_is_refused():
    outcome, problem = rules.resolve_outcome("maybe", None)
    assert outcome is None
    assert problem


def test_the_branch_falls_back_to_the_gates_own_branch():
    assert rules.resolve_branch(gate_data(), "approved", None) == ("branch_yes", None)


def test_the_branch_in_the_body_must_equal_the_gates_branch():
    assert rules.resolve_branch(gate_data(), "approved", "branch_yes") == ("branch_yes", None)
    branch, problem = rules.resolve_branch(gate_data(), "approved", "branch_other")
    assert branch is None
    assert problem


def test_a_gate_with_no_branch_for_an_outcome_refuses_a_resume():
    branch, problem = rules.resolve_branch(gate_data(branches={"refused": "no"}), "approved", None)
    assert branch is None
    assert problem


def test_the_branch_key_is_the_one_the_specification_names():
    assert vocab.BRANCH_KEY == "branchId"


def test_an_undecided_hold_inside_its_window_can_be_resumed():
    assert rules.hold_decision(hold(), "approved", "branch_yes", BASE) == rules.DECISION_OK


def test_a_hold_past_its_window_lapsed():
    subject = hold(expires_at=rules.to_iso(BASE - timedelta(seconds=1)))
    assert rules.hold_decision(subject, "approved", "branch_yes", BASE) == rules.DECISION_EXPIRED


def test_the_same_decision_twice_is_an_idempotent_replay():
    subject = hold(state=vocab.STATE_RESUMED, outcome="approved", branch="branch_yes")
    assert rules.hold_decision(subject, "approved", "branch_yes", BASE) == rules.DECISION_REPLAY


def test_a_second_different_decision_is_a_conflict():
    subject = hold(state=vocab.STATE_RESUMED, outcome="approved", branch="branch_yes")
    assert rules.hold_decision(subject, "refused", "branch_no", BASE) == rules.DECISION_CONFLICT


# --------------------------------------------------------------------------- #
# The gate engine
# --------------------------------------------------------------------------- #


def test_the_pause_toggle_defaults_off_like_the_builders_own_toggle(engine):
    view = engine.create_gate(
        key="send_proposal",
        workflow_id="wf-demo",
        action_key="pause_for_approval",
        branches=dict(BRANCHES),
        actor="dana",
        source="test",
    )
    assert view["pause_execution"] is False


def test_a_created_gate_publishes_the_two_resume_urls_and_the_action_url(engine):
    view = engine.create_gate(
        key="send_proposal",
        workflow_id="wf-demo",
        action_key="pause_for_approval",
        pause_execution=True,
        branches=dict(BRANCHES),
        actor="dana",
        source="test",
    )
    assert view["action_url"] == "/api/wf-119/gates/send_proposal/pause"
    for outcome in vocab.OUTCOMES:
        assert view["resume_urls"][outcome].startswith("/api/wf-119/hooks/")
        assert view["resume_urls"][outcome].endswith(f"/{outcome}")
    assert view["resume_urls"]["approved"] != view["resume_urls"]["refused"]


def test_the_hold_window_on_the_gate_is_the_derived_default(configured):
    assert configured["hold_seconds"] == vocab.DEFAULT_HOLD_SECONDS


def test_a_second_gate_with_the_same_key_is_refused(engine, configured):
    with pytest.raises(KeyTaken):
        engine.create_gate(
            key="send_proposal",
            workflow_id="wf-demo",
            action_key="pause_for_approval",
            branches=dict(BRANCHES),
            actor="dana",
            source="test",
        )


def test_a_gate_that_misses_a_branch_is_refused_before_anything_is_written(engine):
    with pytest.raises(GateInvalid):
        engine.create_gate(
            key="send_proposal",
            workflow_id="wf-demo",
            action_key="pause_for_approval",
            branches={"approved": "branch_yes"},
            actor="dana",
            source="test",
        )


def test_an_unknown_gate_is_not_found(engine):
    with pytest.raises(GateNotFound):
        engine.require_gate("send_proposal")


def test_the_pause_toggle_turns_on_and_the_key_is_immutable(engine, configured):
    revised = engine.revise_gate(
        "send_proposal", {vocab.PAUSE_EXECUTION_KEY: True}, actor="dana", source="test"
    )
    assert revised["pause_execution"] is True
    with pytest.raises(GateInvalid):
        engine.revise_gate("send_proposal", {"key": "other"}, actor="dana", source="test")


def test_a_gate_with_pause_off_holds_nobody(engine):
    engine.create_gate(
        key="send_proposal",
        workflow_id="wf-demo",
        action_key="pause_for_approval",
        branches=dict(BRANCHES),
        actor="dana",
        source="test",
    )
    with pytest.raises(PauseDisabled):
        engine.pause("send_proposal", envelope(), actor="dana", source="test")


def test_pausing_holds_the_contact_and_stamps_the_window(engine, configured):
    result = engine.pause("send_proposal", envelope(), actor="dana", source="test")
    assert result["held"] is True
    assert result["hold"]["state"] == vocab.STATE_HELD
    assert result["hold"]["paused_at"] == rules.to_iso(BASE)
    assert result["hold"]["expires_at"] == rules.to_iso(
        BASE + timedelta(seconds=vocab.DEFAULT_HOLD_SECONDS)
    )
    assert result["resume_mode"] == "async"
    assert result["hold"]["can_resume"] is True


def test_pausing_with_a_broken_envelope_writes_nothing(engine, configured):
    with pytest.raises(EnvelopeInvalid):
        engine.pause(
            "send_proposal",
            envelope(**{vocab.ENVELOPE_EXTRAS: {"locationId": "loc"}}),
            actor="dana",
            source="test",
        )
    assert engine.list_holds() == []


def test_a_second_pause_for_the_same_contact_is_refused_while_it_is_held(engine, configured):
    engine.pause("send_proposal", envelope(), actor="dana", source="test")
    with pytest.raises(HoldConflict):
        engine.pause("send_proposal", envelope(), actor="dana", source="test")


def test_a_different_contact_can_be_held_at_the_same_gate(engine, configured):
    engine.pause("send_proposal", envelope(), actor="dana", source="test")
    other = {**EXTRAS, vocab.EXTRAS_CONTACT: "con_2"}
    result = engine.pause(
        "send_proposal",
        envelope(**{vocab.ENVELOPE_EXTRAS: other}),
        actor="dana",
        source="test",
    )
    assert result["held"] is True
    assert len(engine.list_holds()) == 2


def test_a_lapsed_hold_releases_the_contact_for_a_new_pause(engine, configured):
    engine.pause(
        "send_proposal",
        envelope(),
        actor="dana",
        source="test",
        at=BASE - timedelta(days=5),
    )
    result = engine.pause("send_proposal", envelope(), actor="dana", source="test")
    assert result["held"] is True
    states = sorted(view["state"] for view in engine.list_holds())
    assert states == [vocab.STATE_EXPIRED, vocab.STATE_HELD]


def test_a_hold_raised_in_the_past_reads_as_already_expired(engine, configured):
    result = engine.pause(
        "send_proposal", envelope(), actor="dana", source="test", at=BASE - timedelta(days=4)
    )
    assert result["hold"]["expired"] is True
    assert result["hold"]["can_resume"] is False


def test_a_pause_carries_the_mapped_data_payload(engine, configured):
    result = engine.pause("send_proposal", envelope(), actor="dana", source="test")
    assert result["hold"][vocab.ENVELOPE_DATA] == {"amount": 5, "owner": "dana"}
    assert result["hold"][vocab.ENVELOPE_EXTRAS] == EXTRAS


# --------------------------------------------------------------------------- #
# The resume
# --------------------------------------------------------------------------- #


def _pause(engine, extras=None, **overrides):
    body = {"actor": "dana", "source": "test"}
    body.update(overrides)
    envelope_body = envelope(**({vocab.ENVELOPE_EXTRAS: extras} if extras else {}))
    return engine.pause("send_proposal", envelope_body, **body)


def _resume(engine, extras=None, **overrides):
    """The resume the webhook would make, with the correlation key verified."""
    body = {
        "url_outcome": vocab.OUTCOME_APPROVED,
        "body_branch": "branch_yes",
        "approver": "dana",
        "source_url": "/api/wf-119/hooks/t/approved",
        "actor": "dana",
        "source": "test",
    }
    body.update(overrides)
    return engine.resume_by_extras(extras or EXTRAS, **body)


def _hold_and_resume(engine, **overrides):
    hold_result = _pause(engine)
    return hold_result, _resume(engine, **overrides)


def test_an_approval_resumes_the_contact_on_the_approved_branch(engine, configured):
    _, result = _hold_and_resume(engine)
    assert result["outcome"] == "approved"
    assert result["branch"] == "branch_yes"
    assert result["replay"] is False
    assert result["hold"]["state"] == vocab.STATE_RESUMED
    assert result["hold"]["resumed_at"] == rules.to_iso(BASE)
    assert result["resume"]["approver"] == "dana"


def test_a_refusal_resumes_on_the_refused_branch(engine, configured):
    _, result = _hold_and_resume(engine, url_outcome=vocab.OUTCOME_REFUSED, body_branch="branch_no")
    assert result["outcome"] == "refused"
    assert result["branch"] == "branch_no"


def test_the_gates_branch_is_used_when_the_body_omits_it(engine, configured):
    _, result = _hold_and_resume(engine, body_branch=None)
    assert result["branch"] == "branch_yes"


def test_a_resume_without_an_approver_records_none(engine, configured):
    """The spec gives no approver field, so an absent one is not a refusal."""
    _pause(engine)
    result = _resume(engine, approver=None)
    assert result["resume"]["approver"] is None


def test_a_branch_the_gate_never_declared_is_refused(engine, configured):
    _pause(engine)
    with pytest.raises(BranchUnknown):
        _resume(engine, body_branch="branch_smuggled")
    assert engine.list_holds()[0]["state"] == vocab.STATE_HELD, "a refusal writes no decision"


def test_a_body_outcome_that_disagrees_with_the_url_is_refused(engine, configured):
    _pause(engine)
    with pytest.raises(OutcomeConflict):
        _resume(engine, body_outcome="refused")


def test_the_correlation_key_must_name_a_real_hold(engine, configured):
    with pytest.raises(HoldNotFound):
        _resume(engine, extras={**EXTRAS, vocab.EXTRAS_CONTACT: "con_nobody"})


def test_a_partial_correlation_key_is_refused(engine, configured):
    _pause(engine)
    with pytest.raises(EnvelopeInvalid):
        _resume(engine, extras={"locationId": "loc_northwind"})


def test_a_retried_resume_with_the_same_decision_is_a_replay_with_no_second_row(engine, configured):
    _, first = _hold_and_resume(engine)
    second = _resume(engine)
    assert second["replay"] is True
    assert second["resume"]["id"] == first["resume"]["id"]
    assert second["hold"]["resumed_at"] == first["hold"]["resumed_at"]
    assert engine.store.count_where(vocab.RESUMES, {}) == 1


def test_a_retried_resume_is_recorded_as_a_replay_event(engine, configured):
    _, first = _hold_and_resume(engine)
    _resume(engine)
    events = engine.events_for(first["hold"]["id"])
    assert [event["event"] for event in reversed(events)] == [
        vocab.EVENT_PAUSED,
        vocab.EVENT_RESUMED,
        vocab.EVENT_REPLAY,
    ]


def test_a_second_resume_that_changes_the_decision_is_refused(engine, configured):
    _, first = _hold_and_resume(engine)
    with pytest.raises(ResumeConflict):
        _resume(engine, url_outcome=vocab.OUTCOME_REFUSED, body_branch="branch_no")
    stored = engine.store.get(first["hold"]["id"])
    assert stored["data"]["outcome"] == "approved"


def test_a_resume_after_the_window_lapsed_is_refused_and_the_hold_is_marked(engine, configured):
    engine.pause(
        "send_proposal", envelope(), actor="dana", source="test", at=BASE - timedelta(days=4)
    )
    with pytest.raises(HoldExpired):
        engine.resume_by_extras(
            EXTRAS,
            url_outcome="approved",
            body_branch="branch_yes",
            source_url="hook",
            actor="dana",
            source="test",
        )
    view = engine.list_holds()[0]
    assert view["state"] == vocab.STATE_EXPIRED
    assert view["expiry_reason"] == "resume_after_lapse"


def test_the_webhook_resolves_its_gate_from_the_token(engine, configured):
    engine.pause("send_proposal", envelope(), actor="dana", source="test")
    token = configured["resume_urls"]["approved"].split("/")[4]
    result = engine.resume_by_token(
        token, "approved", {"extras": dict(EXTRAS)}, actor="dana", source="test"
    )
    assert result["outcome"] == "approved"


def test_an_unknown_resume_token_is_not_found(engine):
    with pytest.raises(GateNotFound):
        engine.resume_by_token("nope", "approved", {"extras": dict(EXTRAS)}, actor="d", source="t")


def test_an_in_app_resume_is_addressed_by_hold_id(engine, configured):
    held = engine.pause("send_proposal", envelope(), actor="dana", source="test")
    result = engine.resume_hold(
        held["hold"]["id"],
        url_outcome="refused",
        body_branch="branch_no",
        approver="ravi",
        source_url="/api/wf-119/holds/x/resume",
        actor="ravi",
        source="test",
    )
    assert result["outcome"] == "refused"
    assert result["resume"]["approver"] == "ravi"


def test_an_unknown_hold_id_is_not_found(engine):
    with pytest.raises(HoldNotFound):
        engine.resume_hold(
            "wf119_hold_absent", url_outcome="approved", source_url="x", actor="d", source="t"
        )


# --------------------------------------------------------------------------- #
# Expiry and the summary
# --------------------------------------------------------------------------- #


def test_the_sweep_lapses_every_hold_that_has_passed_its_window(engine, configured):
    engine.pause(
        "send_proposal",
        envelope(**{vocab.ENVELOPE_EXTRAS: {**EXTRAS, vocab.EXTRAS_CONTACT: "con_old"}}),
        actor="dana",
        source="test",
        at=BASE - timedelta(days=5),
    )
    engine.pause("send_proposal", envelope(), actor="dana", source="test")
    swept = engine.sweep(actor="dana", source="test")
    assert swept["expired"] == 1
    views = {
        view[vocab.ENVELOPE_EXTRAS][vocab.EXTRAS_CONTACT]: view for view in engine.list_holds()
    }
    assert views["con_old"]["state"] == vocab.STATE_EXPIRED
    assert views["con_1"]["state"] == vocab.STATE_HELD


def test_the_sweep_is_idempotent(engine, configured):
    engine.pause(
        "send_proposal", envelope(), actor="dana", source="test", at=BASE - timedelta(days=5)
    )
    assert engine.sweep(actor="dana", source="test")["expired"] == 1
    assert engine.sweep(actor="dana", source="test")["expired"] == 0


def test_the_summary_counts_a_lapsed_hold_as_expired_before_any_sweep(engine, configured):
    engine.pause(
        "send_proposal", envelope(), actor="dana", source="test", at=BASE - timedelta(days=5)
    )
    summary = engine.summary()
    assert summary["holds"] == 1
    assert summary["by_state"][vocab.STATE_EXPIRED] == 1
    assert summary["expired_lazily"] == 1
    assert summary["resume_mode"] == "async"


def test_the_summary_counts_a_resumed_hold(engine, configured):
    _hold_and_resume(engine)
    summary = engine.summary()
    assert summary["by_state"][vocab.STATE_RESUMED] == 1
    assert summary["resumes"] == 1
    assert summary["gates"] == 1
    assert summary["pause_enabled"] == 1


def test_listing_holds_can_filter_by_gate_and_state(engine, configured):
    engine.pause("send_proposal", envelope(), actor="dana", source="test")
    assert len(engine.list_holds(gate_key="send_proposal")) == 1
    assert engine.list_holds(gate_key="elsewhere") == []
    assert len(engine.list_holds(state=vocab.STATE_HELD)) == 1
    assert engine.list_holds(state=vocab.STATE_RESUMED) == []


def test_the_events_for_a_hold_read_as_a_sequence(engine, configured):
    held = engine.pause("send_proposal", envelope(), actor="dana", source="test")
    events = engine.events_for(held["hold"]["id"])
    assert len(events) == 1
    assert events[0]["event"] == vocab.EVENT_PAUSED
    assert events[0]["hold_id"] == held["hold"]["id"]


# --------------------------------------------------------------------------- #
# The dependency direction
# --------------------------------------------------------------------------- #


def domain_paths() -> list[Path]:
    return sorted(Path(inspect.getfile(ApprovalGate)).parent.glob("*.py"))


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
    assert all(value.startswith("wf119_") for value in vocab.vocabulary()["collections"].values())
    assert vocab.vocabulary()["collections"]["gates"] == vocab.GATES


def test_the_error_hierarchy_maps_every_type_to_a_status_and_a_code():
    for error_type, (status, code) in REFUSAL_STATUS.items():
        assert issubclass(error_type, ApprovalGateRefusal)
        assert 400 <= status < 500
        assert code == error_type.code
    for error_type in (
        BranchUnknown,
        EnvelopeInvalid,
        GateInvalid,
        GateNotFound,
        HoldConflict,
        HoldExpired,
        HoldNotFound,
        KeyTaken,
        OutcomeConflict,
        PauseDisabled,
        ResumeConflict,
    ):
        assert error_type in REFUSAL_STATUS, error_type.__name__


# --------------------------------------------------------------------------- #
# The vocabulary and the recorded decisions
# --------------------------------------------------------------------------- #


def test_the_vocabulary_records_the_resume_mode_decision_and_its_audit_id():
    resume = vocab.vocabulary()["resume"]
    assert resume["mode"] == "async"
    assert resume["rejected_mode"] == "sync"
    assert resume["audit_id"] == "jev-20261014T105812-18180-92928"
    assert resume["success_url_outcome"] == "approved"
    assert resume["failed_url_outcome"] == "refused"


def test_the_vocabulary_records_every_open_decision_the_issue_names():
    ids = {entry["id"] for entry in vocab.decisions()}
    assert {
        "RESUME_MODE_IS_ASYNC",
        "HOLD_WINDOW_IS_DERIVED",
        "EXTRAS_ARE_THE_CORRELATION_KEY_AND_ARE_VERIFIED",
        "THE_GATE_DOES_NOT_REQUIRE_AN_APPROVER_IDENTITY",
        "A_RESUME_IS_IDEMPOTENT_FOR_THE_SAME_OUTCOME",
        "THE_URL_NAMES_THE_OUTCOME_AND_THE_BODY_NAMES_THE_BRANCH",
    } <= ids


def test_every_decision_records_what_was_rejected():
    for entry in vocab.decisions():
        assert entry["choice"]
        assert entry["rejected"], entry["id"]
        assert entry["question"], entry["id"]


def test_what_the_build_leaves_out_is_visible():
    not_built = vocab.not_built()
    assert len(not_built) >= 3
    joined = " ".join(entry["item"] for entry in not_built)
    assert "Test Code" in joined
    assert "targetUrl" in joined


def test_the_vocabulary_serves_the_hold_window_and_the_correlation_key():
    payload = vocab.vocabulary()
    assert payload["hold_window"]["default_seconds"] == vocab.DEFAULT_HOLD_SECONDS
    assert payload["envelope"]["extras_keys"] == list(vocab.EXTRAS_KEYS)
    assert payload["envelope"]["branch_key"] == vocab.BRANCH_KEY
    assert payload["resume"]["mode"] == vocab.RESUME_MODE
    assert payload["sources"]


def test_the_resume_mode_record_carries_the_quotes_it_was_chosen_from():
    record = vocab.resume_mode_record()
    assert record["mode"] == "async"
    assert set(record["quotes"]) == {"sync", "async", "hold"}
    assert "branchId" in record["quotes"]["sync"]


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
    assert "gates" in summary


def test_seed_creates_the_states_the_page_has_to_show(store: RecordStore):
    summary = seed(store.db, {"now": BASE})
    engine = ApprovalGate(store, now=lambda: BASE)
    assert len(engine.list_gates()) == 2
    states = sorted(view["state"] for view in engine.list_holds())
    assert states == [vocab.STATE_EXPIRED, vocab.STATE_HELD, vocab.STATE_RESUMED]
    resumed = [view for view in engine.list_holds() if view["state"] == vocab.STATE_RESUMED][0]
    assert resumed["branch"] == "branch_proposal_approved"
    # The issue requires at least one state that is not a success, and the
    # summary has to name it.
    assert "expired with no decision" in summary
    assert "waiting on a human" in summary


def test_seed_turns_the_pause_toggle_on_for_the_gate_that_holds(store: RecordStore):
    seed(store.db, {"now": BASE})
    engine = ApprovalGate(store, now=lambda: BASE)
    gates = {gate["key"]: gate for gate in engine.list_gates()}
    assert gates["send_proposal"]["pause_execution"] is True
    assert gates["send_invoice"]["pause_execution"] is False
