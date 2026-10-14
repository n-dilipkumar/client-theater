"""WF-134: the deal-health rules, exercised as a domain module with no server.

The rules in ``dsr.competitive_baseline.deal_health`` are pure: they touch the
store and nothing else. So every rule below is reachable without a
``TestClient``, and these tests are what make each decision the issue left open
checkable at the instant it is stated about.

What is under test, and why each one matters
--------------------------------------------

* **The score is a weighted sum of three factors.** Engagement, unreviewed key
  material and stakeholder coverage. The issue gives the shape and no formula, so
  the derivation is pinned here rather than left to drift.
* **A score in the at-risk band raises an alert; a score that falls by the drop
  threshold raises a second one.** The research says a score drop raises a risk
  alert, and neither condition is quantified, so the threshold is pinned.
* **Each active risk reason raises one coaching cue.** Coaching is rule-based and
  needs no model: the research itself says coaching would need a model and a
  transcription pipeline this repository does not have.
* **An outcome closes a cue, and the outcomes are readiness data.** The
  research calls this the closed loop.
* **Every write is read back before it reports success.** A write that does not
  land is a conflict, matching the source CLI that verifies against the live site.
* **Records are plain JSON in ``data``.** A team adding a field needs no
  coordination, so this module adds no migration and no typed column.

Every test names the property it protects, because a test named ``test_flow``
tells a reviewer nothing about which rule they can stop worrying about.
"""

from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from dsr.competitive_baseline import deal_health as domain
from dsr.competitive_baseline.deal_health import (
    DealHealthEngine,
    DealHealthError,
    DealHealthNotFound,
    DealHealthRefused,
)
from dsr.db.audited import AuditedDatabase
from dsr.features import load_feature
from dsr.store import RecordStore

MODULE = "wf134_engagement_driven_deal_health_scoring_risk_detection_and_coaching"

NOW = datetime(2026, 5, 4, 9, 0, tzinfo=timezone.utc)


@pytest.fixture()
def engine(store: RecordStore) -> DealHealthEngine:
    """The engine, with the clock pinned so every decay boundary is exact."""
    return DealHealthEngine(store, now=lambda: NOW)


@pytest.fixture()
def room(store: RecordStore) -> str:
    return store.create("room", {"name": "Northwind"}, actor="test", source="test")["id"]


def _deal(engine: DealHealthEngine, room_id: str, name: str = "Northwind", **extra: object) -> dict:
    return engine.create_deal(room_id, {"name": name, **extra}, source="test", actor="test")


def _event(engine: DealHealthEngine, deal_id: str, **extra: object) -> dict:
    payload = {"kind": "meeting", "person": "Priya Raman", **extra}
    return engine.record_event(deal_id, payload, source="test", actor="test")


def _hours_ago(hours: float) -> str:
    return (NOW - timedelta(hours=hours)).isoformat()


def _days_ago(days: float) -> str:
    return (NOW - timedelta(days=days)).isoformat()


# --------------------------------------------------------------------------- #
# Coercion
# --------------------------------------------------------------------------- #


def test_a_whole_number_reads_from_a_number_or_a_numeric_string():
    assert domain.read_int(3) == 3
    assert domain.read_int("3") == 3
    assert domain.read_int(3.0) == 3
    assert domain.read_int("many", 7) == 7
    assert domain.read_int(None, 7) == 7


def test_a_bool_is_not_a_count():
    """``True`` is not a number of events, so it must not read as one."""
    assert domain.read_int(True, None) is None


def test_a_decimal_reads_from_a_number_or_text():
    assert domain.read_float(2) == 2.0
    assert domain.read_float("2.5") == 2.5
    assert domain.read_float("big", 1.5) == 1.5
    assert domain.read_float(True, None) is None


def test_a_flag_reads_from_a_bool_or_from_text():
    assert domain.read_bool(True, False) is True
    assert domain.read_bool("no", True) is False
    assert domain.read_bool("yes", False) is True
    assert domain.read_bool(None, True) is True
    assert domain.read_bool("maybe", True) is True


def test_an_iso_timestamp_reads_as_an_aware_datetime():
    moment = domain.as_datetime("2026-05-04T09:00:00")
    assert moment is not None and moment.tzinfo is not None
    assert domain.as_datetime(NOW) == NOW
    assert domain.as_datetime("2026-05-04T09:00:00Z").tzinfo is not None


def test_an_unreadable_timestamp_is_none_rather_than_an_exception():
    """One bad row must not stop a deal from scoring."""
    for value in ("not-a-date", "2026-13-45", "", None):
        assert domain.as_datetime(value) is None, value


def test_the_store_owned_keys_are_stripped_from_a_payload():
    stripped = domain.strip_reserved({"name": "Deal", "id": "forged", "revision": 9})
    assert stripped == {"name": "Deal"}


# --------------------------------------------------------------------------- #
# The three factors
# --------------------------------------------------------------------------- #


def test_an_event_weight_follows_the_buyers_time():
    assert domain.event_weight("meeting") > domain.event_weight("view")
    assert domain.event_weight("question") > domain.event_weight("dwell")


def test_an_unrecognised_event_kind_still_counts_least():
    assert domain.event_weight("telepathy") == domain.UNKNOWN_EVENT_WEIGHT


def test_no_events_scores_engagement_zero():
    row = domain.engagement_factor([], NOW)
    assert row["factor"] == 0.0
    assert row["events"] == 0
    assert row["days_since_last"] is None


def test_engagement_blends_recency_and_volume():
    events = [{"kind": "meeting", "at": _hours_ago(2)}, {"kind": "dwell", "at": _hours_ago(4)}]
    row = domain.engagement_factor(events, NOW)
    assert row["events"] == 2
    assert row["recency"] > 0.99
    # 2.0 + 1.0 of weight against a target of 6.
    assert row["volume"] == pytest.approx(0.5, abs=0.01)
    assert row["factor"] == pytest.approx(
        domain.RECENCY_SHARE * row["recency"] + domain.VOLUME_SHARE * row["volume"], abs=0.001
    )


def test_engagement_decays_towards_zero_as_the_room_goes_quiet():
    fresh = domain.engagement_factor([{"kind": "meeting", "at": _hours_ago(1)}], NOW)
    stale = domain.engagement_factor([{"kind": "meeting", "at": _days_ago(20)}], NOW)
    gone = domain.engagement_factor([{"kind": "meeting", "at": _days_ago(60)}], NOW)
    assert fresh["factor"] > stale["factor"] > 0.0
    assert gone["recency"] == 0.0
    assert gone["events_in_window"] == 0


def test_an_event_with_an_unreadable_timestamp_does_not_count():
    row = domain.engagement_factor([{"kind": "meeting", "at": "whenever"}], NOW)
    assert row["events"] == 0


def test_a_future_event_is_capped_at_full_recency():
    """A clock skew must not push recency above one."""
    row = domain.engagement_factor([{"kind": "view", "at": _hours_ago(-5)}], NOW)
    assert row["recency"] <= 1.0


def test_no_required_material_owes_nothing():
    row = domain.content_factor([{"name": "Optional", "required": False}])
    assert row["factor"] == 1.0
    assert row["required"] == 0


def test_content_factor_counts_only_required_material():
    row = domain.content_factor(
        [
            {"name": "Deck", "required": True, "reviewed_at": _days_ago(1)},
            {"name": "Plan", "required": True},
            {"name": "Blog", "required": False},
        ]
    )
    assert row["required"] == 2
    assert row["reviewed"] == 1
    assert row["factor"] == 0.5
    assert row["unreviewed"] == ["Plan"]


def test_a_buying_group_nobody_recorded_scores_zero_coverage():
    """The missing-stakeholders risk, not a pass."""
    row = domain.coverage_factor([])
    assert row["factor"] == 0.0
    assert row["total"] == 0


def test_coverage_reaches_full_at_the_target():
    people = [
        {"name": "A", "engaged_at": _days_ago(1)},
        {"name": "B", "engaged_at": _days_ago(2)},
        {"name": "C"},
        {"name": "D"},
        {"name": "E"},
    ]
    row = domain.coverage_factor(people)
    assert row["coverage"] == 0.4
    assert row["engaged"] == 2
    assert row["factor"] == pytest.approx(0.4 / domain.COVERAGE_TARGET, abs=0.001)
    assert row["not_engaged"] == ["C", "D", "E"]


def test_an_engaged_count_marks_a_stakeholder_engaged_without_a_timestamp():
    people = [{"name": "A", "engagement_count": 3}, {"name": "B"}]
    row = domain.coverage_factor(people)
    assert row["engaged"] == 1
    assert row["not_engaged"] == ["B"]


def test_coverage_is_capped_at_full_above_the_target():
    people = [{"name": name, "engaged_at": _days_ago(1)} for name in ("A", "B", "C")]
    assert domain.coverage_factor(people)["factor"] == 1.0


# --------------------------------------------------------------------------- #
# The score and its reasons
# --------------------------------------------------------------------------- #


def test_the_weights_are_a_partition():
    assert sum(domain.WEIGHTS.values()) == pytest.approx(1.0)
    assert domain.FACTOR_COUNT == len(domain.WEIGHTS)


def test_the_band_boundaries_are_exact():
    assert domain.band_for(domain.HEALTHY_FLOOR) == domain.BAND_HEALTHY
    assert domain.band_for(domain.HEALTHY_FLOOR - 1) == domain.BAND_WATCH
    assert domain.band_for(domain.WATCH_FLOOR) == domain.BAND_WATCH
    assert domain.band_for(domain.WATCH_FLOOR - 1) == domain.BAND_AT_RISK
    assert domain.band_for(100) == domain.BAND_HEALTHY


def test_an_unreadable_score_fails_toward_at_risk():
    """An unreadable score must not silence an alert."""
    assert domain.band_for("ninety") == domain.BAND_AT_RISK
    assert domain.band_for(None) == domain.BAND_AT_RISK


def test_a_deal_with_everything_going_for_it_scores_healthy():
    deal = {"name": "Northwind", "framework": "meddicc"}
    events = [{"kind": "meeting", "at": _hours_ago(1)}]
    people = [
        {"name": "A", "engaged_at": _days_ago(1)},
        {"name": "B", "engaged_at": _days_ago(1)},
    ]
    content = [{"name": "Deck", "required": True, "reviewed_at": _days_ago(1)}]
    score = domain.score_deal(deal, events, people, content, NOW)
    assert score["value"] >= domain.HEALTHY_FLOOR
    assert score["band"] == domain.BAND_HEALTHY
    assert score["reasons"] == []
    assert score["framework"] == "meddicc"


def test_a_quiet_deal_with_blind_buyers_and_unread_material_is_at_risk():
    deal = {"name": "Contoso"}
    people = [{"name": "A"}, {"name": "B"}]
    content = [{"name": "Plan", "required": True}]
    score = domain.score_deal(deal, [], people, content, NOW)
    assert score["band"] == domain.BAND_AT_RISK
    assert score["value"] == 0
    assert domain.REASON_NO_ENGAGEMENT in score["reasons"]
    assert domain.REASON_UNREVIEWED_KEY_MATERIAL in score["reasons"]
    assert domain.REASON_MISSING_STAKEHOLDERS in score["reasons"]
    assert domain.REASON_BELOW_TARGET in score["reasons"]
    assert score["reason_sentences"] == [
        domain.reason_sentence(reason) for reason in score["reasons"]
    ]


def test_engagement_decay_is_reported_when_the_factor_falls_below_the_floor():
    deal = {"name": "Fabrikam"}
    events = [{"kind": "view", "at": _days_ago(18)}]
    score = domain.score_deal(deal, events, [{"name": "A", "engaged_at": _days_ago(1)}], [], NOW)
    assert score["factors"]["engagement"]["factor"] < domain.ENGAGEMENT_FLOOR
    assert domain.REASON_ENGAGEMENT_DECAY in score["reasons"]


def test_a_drop_by_the_threshold_is_reported_and_a_small_fall_is_not():
    deal = {"name": "Northwind"}
    events = [{"kind": "meeting", "at": _hours_ago(1)}]
    people = [{"name": "A", "engaged_at": _days_ago(1)}, {"name": "B", "engaged_at": _days_ago(1)}]
    content = [{"name": "Deck", "required": True, "reviewed_at": _days_ago(1)}]

    steady = domain.score_deal(deal, events, people, content, NOW, previous=95)
    assert domain.REASON_SCORE_DROP not in steady["reasons"]

    # An empty deal after a healthy reading: the fall is well past the threshold.
    fallen = domain.score_deal(deal, [], [], [], NOW, previous=90)
    assert fallen["delta"] <= -domain.DROP_THRESHOLD
    assert domain.REASON_SCORE_DROP in fallen["reasons"]


def test_a_first_reading_has_no_delta_and_no_drop_reason():
    score = domain.score_deal({"name": "Northwind"}, [], [], [], NOW, previous=None)
    assert score["previous"] is None
    assert score["delta"] is None
    assert domain.REASON_SCORE_DROP not in score["reasons"]


def test_the_score_carries_its_working_rather_than_asserting_a_number():
    score = domain.score_deal({"name": "Northwind"}, [], [], [], NOW)
    assert set(score["factors"]) == {"engagement", "content", "stakeholder"}
    assert score["weights"] == domain.WEIGHTS
    assert 0 <= score["value"] <= 100
    assert score["measured_at"] == NOW.isoformat()


# --------------------------------------------------------------------------- #
# Alerts
# --------------------------------------------------------------------------- #


def test_an_at_risk_band_raises_a_band_alert():
    score = domain.score_deal({"name": "Contoso"}, [], [], [], NOW)
    candidates = domain.alert_candidates(score)
    assert [row["kind"] for row in candidates] == [domain.ALERT_BAND]
    assert candidates[0]["severity"] == "high"


def test_a_score_drop_raises_a_second_alert_beside_the_band_one():
    score = domain.score_deal({"name": "Contoso"}, [], [], [], NOW, previous=90)
    kinds = [row["kind"] for row in domain.alert_candidates(score)]
    assert kinds == [domain.ALERT_BAND, domain.ALERT_DROP]


def test_a_healthy_deal_raises_no_alert():
    events = [{"kind": "meeting", "at": _hours_ago(1)}]
    people = [{"name": "A", "engaged_at": _days_ago(1)}, {"name": "B", "engaged_at": _days_ago(1)}]
    content = [{"name": "Deck", "required": True, "reviewed_at": _days_ago(1)}]
    assert domain.alert_candidates(domain.score_deal({}, events, people, content, NOW)) == []


# --------------------------------------------------------------------------- #
# Coaching and readiness
# --------------------------------------------------------------------------- #


def test_every_risk_reason_has_one_coaching_action():
    for reason in domain.RISK_REASONS:
        assert reason in domain.COACHING_ACTIONS, reason


def test_one_cue_per_active_reason_in_the_scores_own_order():
    score = domain.score_deal({"name": "Contoso"}, [], [], [], NOW)
    cues = domain.coaching_cues(score)
    assert [cue["reason"] for cue in cues] == score["reasons"]
    assert all(cue["action"] for cue in cues)


def test_readiness_is_none_when_no_cue_was_ever_closed():
    """No coachable cue is not the same as a rep who ignored every cue."""
    assert domain.readiness([])["readiness"] is None


def test_readiness_counts_the_actioned_share_and_participation():
    rows = [
        {"state": domain.OUTCOME_ACTIONED},
        {"state": domain.OUTCOME_ACKNOWLEDGED},
        {"state": domain.OUTCOME_DISMISSED},
        {"state": domain.OUTCOME_DISMISSED},
    ]
    index = domain.readiness(rows)
    assert index["outcomes"] == 4
    assert index["readiness"] == 0.25
    assert index["participation"] == 0.5


def test_a_reason_with_no_sentence_still_explains_itself():
    assert domain.reason_sentence("mystery") == "This deal needs attention."


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #


def test_a_deal_needs_a_name():
    with pytest.raises(DealHealthError) as caught:
        domain.validate_deal({"account": "Northwind"})
    assert "name" in caught.value.errors


def test_an_unknown_framework_is_refused_and_a_known_one_is_kept():
    with pytest.raises(DealHealthError) as caught:
        domain.validate_deal({"name": "Deal", "framework": "guesswork"})
    assert "framework" in caught.value.errors
    assert domain.validate_deal({"name": "Deal", "framework": "BANT"})["framework"] == "bant"


def test_a_deal_takes_the_default_framework_when_the_payload_is_silent():
    assert domain.validate_deal({"name": "Deal"})["framework"] == domain.DEFAULT_FRAMEWORK


def test_an_unreadable_amount_is_refused():
    with pytest.raises(DealHealthError) as caught:
        domain.validate_deal({"name": "Deal", "amount": "a lot"})
    assert "amount" in caught.value.errors


def test_a_deal_keeps_a_field_this_workflow_has_never_heard_of():
    """Schema flexibility: a team adds a field without a migration."""
    normalised = domain.validate_deal({"name": "Deal", "procurement_ticket": "PR-1"})
    assert normalised["procurement_ticket"] == "PR-1"


def test_an_event_needs_a_known_kind_and_a_named_person():
    with pytest.raises(DealHealthError) as caught:
        domain.validate_event({"kind": "telepathy", "person": ""})
    assert set(caught.value.errors) == {"kind", "person"}


def test_an_event_takes_the_engines_clock_when_it_has_no_time():
    normalised = domain.validate_event({"kind": "view", "person": "A"}, at_default=NOW)
    assert normalised["at"] == NOW.isoformat()


def test_an_event_with_an_unreadable_time_is_refused_at_validation_time():
    with pytest.raises(DealHealthError) as caught:
        domain.validate_event({"kind": "view", "person": "A", "at": "soon"})
    assert "at" in caught.value.errors


def test_a_negative_engagement_duration_is_refused():
    with pytest.raises(DealHealthError) as caught:
        domain.validate_event({"kind": "view", "person": "A", "seconds": -5})
    assert "seconds" in caught.value.errors


def test_a_stakeholder_needs_a_name():
    with pytest.raises(DealHealthError) as caught:
        domain.validate_stakeholder({"role": "champion"})
    assert "name" in caught.value.errors


def test_a_negative_stakeholder_count_is_refused():
    with pytest.raises(DealHealthError) as caught:
        domain.validate_stakeholder({"name": "Ada", "engagement_count": -1})
    assert "engagement_count" in caught.value.errors


def test_key_material_needs_a_name():
    with pytest.raises(DealHealthError) as caught:
        domain.validate_content({"kind": "deck"})
    assert "name" in caught.value.errors


def test_key_material_is_required_by_default():
    assert domain.validate_content({"name": "Deck"})["required"] is True


# --------------------------------------------------------------------------- #
# The engine, against stored state
# --------------------------------------------------------------------------- #


def test_a_deal_is_created_and_read_back(engine, room):
    deal = _deal(engine, room)
    assert deal["room_id"] == room
    assert engine.read_deal(deal["id"])["name"] == "Northwind"


def test_an_unknown_deal_is_not_found(engine):
    with pytest.raises(DealHealthNotFound):
        engine.read_deal("wf134_deal_absent")


def test_an_event_scores_the_deal_in_the_same_call(engine, room):
    """Auto-score on every event is the automation the research names."""
    deal = _deal(engine, room)
    result = _event(engine, deal["id"])
    assert result["event"]["kind"] == "meeting"
    assert result["score"]["value"] > 0
    assert engine.history(deal["id"])


def test_a_live_reading_does_not_write_a_score(engine, room):
    deal = _deal(engine, room)
    card = engine.score_card(deal["id"])
    assert card["recorded"] is False
    assert engine.history(deal["id"]) == []


def test_a_recorded_score_is_served_back(engine, room):
    deal = _deal(engine, room)
    engine.score(deal["id"], source="test", actor="test")
    card = engine.score_card(deal["id"])
    assert card["recorded"] is True
    assert card["last_recorded"]["band"] == domain.BAND_AT_RISK
    assert card["score"]["band"] == domain.BAND_AT_RISK


def test_a_staying_risk_raises_one_alert_not_one_per_reading(engine, room):
    deal = _deal(engine, room)
    first = engine.score(deal["id"], source="test", actor="test")
    engine.score(deal["id"], source="test", actor="test")
    assert [row["kind"] for row in first["alerts"]] == [domain.ALERT_BAND]
    assert len(engine.list_alerts(deal["id"])) == 1


def test_resolving_an_alert_lets_the_same_risk_raise_a_new_one(engine, room):
    deal = _deal(engine, room)
    score = engine.score(deal["id"], source="test", actor="test")
    engine.resolve_alert(score["alerts"][0]["id"], source="test", actor="test")
    assert engine.list_alerts(deal["id"])[0]["open"] is False

    again = engine.score(deal["id"], source="test", actor="test")
    assert [row["kind"] for row in again["alerts"]] == [domain.ALERT_BAND]


def test_a_staying_risk_raises_one_cue_not_one_per_reading(engine, room):
    deal = _deal(engine, room)
    first = engine.score(deal["id"], source="test", actor="test")
    engine.score(deal["id"], source="test", actor="test")
    reasons = [row["reason"] for row in first["coaching"]]
    assert reasons == domain.score_deal({}, [], [], [], NOW)["reasons"]
    assert len(engine.list_coaching(deal["id"])) == len(reasons)


def test_reviewing_key_material_clears_the_reason_and_scores_again(engine, room):
    deal = _deal(engine, room)
    asset = engine.add_content(
        deal["id"], {"name": "Plan", "required": True}, source="test", actor="test"
    )
    engine.add_stakeholder(
        deal["id"], {"name": "Ada", "engaged_at": _days_ago(1)}, source="test", actor="test"
    )
    engine.record_event(
        deal["id"],
        {"kind": "meeting", "person": "Ada", "at": _hours_ago(1)},
        source="test",
        actor="test",
    )
    assert (
        domain.REASON_UNREVIEWED_KEY_MATERIAL in engine.score_card(deal["id"])["score"]["reasons"]
    )

    engine.review_content(deal["id"], asset["id"], source="test", actor="test")
    assert engine.list_content(deal["id"])[0]["reviewed"] is True
    assert (
        domain.REASON_UNREVIEWED_KEY_MATERIAL
        not in engine.score_card(deal["id"])["score"]["reasons"]
    )

    engine.review_content(deal["id"], asset["id"], reviewed=False, source="test", actor="test")
    assert engine.list_content(deal["id"])[0]["reviewed"] is False


def test_optional_material_never_owes_a_review(engine, room):
    deal = _deal(engine, room)
    engine.add_content(deal["id"], {"name": "Blog", "required": False}, source="test", actor="test")
    reasons = engine.score_card(deal["id"])["score"]["reasons"]
    assert domain.REASON_UNREVIEWED_KEY_MATERIAL not in reasons


def test_the_coverage_map_names_who_engaged_and_who_did_not(engine, room):
    deal = _deal(engine, room)
    engine.add_stakeholder(
        deal["id"], {"name": "Ada", "engaged_at": _days_ago(1)}, source="test", actor="test"
    )
    engine.add_stakeholder(deal["id"], {"name": "Ben"}, source="test", actor="test")

    coverage = engine.coverage_map(deal["id"])
    assert [row["name"] for row in coverage["engaged"]] == ["Ada"]
    assert [row["name"] for row in coverage["not_engaged"]] == ["Ben"]
    assert coverage["total"] == 2
    assert coverage["met"] is False


def test_a_stakeholder_can_be_edited_to_have_engaged(engine, room):
    deal = _deal(engine, room)
    person = engine.add_stakeholder(deal["id"], {"name": "Ben"}, source="test", actor="test")
    engine.update_stakeholder(
        deal["id"], person["id"], {"engagement_count": 2}, source="test", actor="test"
    )
    assert engine.coverage_map(deal["id"])["engaged_count"] == 1


def test_a_stakeholder_from_another_deal_is_not_found(engine, room):
    first = _deal(engine, room, "Northwind")
    second = _deal(engine, room, "Fabrikam")
    person = engine.add_stakeholder(first["id"], {"name": "Ben"}, source="test", actor="test")
    with pytest.raises(DealHealthNotFound):
        engine.update_stakeholder(
            second["id"], person["id"], {"role": "x"}, source="test", actor="test"
        )


def test_an_outcome_closes_a_cue_and_writes_readiness_data(engine, room):
    deal = _deal(engine, room)
    cue = engine.score(deal["id"], source="test", actor="test")["coaching"][0]
    closed = engine.record_outcome(
        cue["id"],
        {"state": domain.OUTCOME_ACTIONED, "note": "Booked it."},
        source="test",
        actor="test",
    )
    assert closed["cue"]["open"] is False
    assert closed["outcome"]["state"] == domain.OUTCOME_ACTIONED

    index = engine.readiness()
    assert index["outcomes"] == 1
    assert index["readiness"] == 1.0


def test_a_cue_that_already_has_an_outcome_is_refused(engine, room):
    deal = _deal(engine, room)
    cue = engine.score(deal["id"], source="test", actor="test")["coaching"][0]
    engine.record_outcome(
        cue["id"], {"state": domain.OUTCOME_DISMISSED}, source="test", actor="test"
    )
    with pytest.raises(DealHealthRefused) as caught:
        engine.record_outcome(
            cue["id"], {"state": domain.OUTCOME_ACTIONED}, source="test", actor="test"
        )
    assert caught.value.reason == DealHealthRefused.REASON_ALREADY_CLOSED


def test_an_outcome_state_must_be_one_of_the_three(engine, room):
    deal = _deal(engine, room)
    cue = engine.score(deal["id"], source="test", actor="test")["coaching"][0]
    with pytest.raises(DealHealthError) as caught:
        engine.record_outcome(cue["id"], {"state": "ignored"}, source="test", actor="test")
    assert "state" in caught.value.errors


def test_readiness_can_be_scoped_to_one_rep(engine, room):
    deal = _deal(engine, room, "Northwind", rep="dana")
    cue = engine.score(deal["id"], source="test", actor="test")["coaching"][0]
    engine.record_outcome(
        cue["id"], {"state": domain.OUTCOME_ACTIONED}, source="test", actor="test"
    )
    assert engine.readiness("dana")["outcomes"] == 1
    assert engine.readiness("sam")["outcomes"] == 0


def test_the_summary_counts_each_band_and_the_open_work(engine, room):
    healthy = _deal(engine, room, "Northwind")
    engine.add_stakeholder(
        healthy["id"], {"name": "A", "engaged_at": _days_ago(1)}, source="test", actor="test"
    )
    engine.record_event(
        healthy["id"],
        {"kind": "meeting", "person": "A", "at": _hours_ago(1)},
        source="test",
        actor="test",
    )
    _deal(engine, room, "Contoso")

    counted = engine.summary(room)
    assert counted["deals"] == 2
    assert counted["scored"] == 1
    assert counted["healthy"] == 1
    assert counted["weight_percent"] == {"engagement": 50, "content": 30, "stakeholder": 20}
    assert "generated_at" in counted


def test_a_write_that_does_not_read_back_is_refused(engine, room, monkeypatch):
    deal = _deal(engine, room)
    monkeypatch.setattr(engine.store, "update", lambda *args, **kwargs: None)
    with pytest.raises(DealHealthRefused) as caught:
        engine.update_deal(deal["id"], {"name": "Renamed"}, source="test", actor="test")
    assert caught.value.reason == DealHealthRefused.REASON_WRITE_NOT_VISIBLE


def test_editing_a_deal_changes_its_framework(engine, room):
    deal = _deal(engine, room)
    updated = engine.update_deal(deal["id"], {"framework": "bant"}, source="test", actor="test")
    assert updated["framework"] == "bant"


def test_a_team_can_add_its_own_field_with_no_migration(store, room):
    deal = store.create(
        domain.DEAL_COLLECTION,
        {"name": "Northwind", "procurement_ticket": "PR-4471", domain.ROOM_REF: room},
        room_id=room,
        actor="test",
        source="test",
    )
    found = store.find(domain.DEAL_COLLECTION, {"procurement_ticket": "PR-4471"}, limit=5)
    assert [row["id"] for row in found] == [deal["id"]]


def test_every_record_this_workflow_writes_is_ordinary_json(engine, room):
    deal = _deal(engine, room)
    engine.add_stakeholder(
        deal["id"], {"name": "Ada", "engaged_at": _days_ago(1)}, source="test", actor="test"
    )
    asset = engine.add_content(
        deal["id"], {"name": "Deck", "required": True}, source="test", actor="test"
    )
    recorded = engine.record_event(
        deal["id"],
        {"kind": "meeting", "person": "Ada", "at": _hours_ago(1)},
        source="test",
        actor="test",
    )
    cue = recorded["coaching"][0]
    engine.review_content(deal["id"], asset["id"], source="test", actor="test")
    for alert in engine.list_alerts(deal["id"]):
        engine.resolve_alert(alert["id"], source="test", actor="test")
    engine.record_outcome(
        cue["id"], {"state": domain.OUTCOME_ACTIONED}, source="test", actor="test"
    )

    for collection in (
        domain.DEAL_COLLECTION,
        domain.EVENT_COLLECTION,
        domain.STAKEHOLDER_COLLECTION,
        domain.CONTENT_COLLECTION,
        domain.SCORE_COLLECTION,
        domain.ALERT_COLLECTION,
        domain.COACHING_COLLECTION,
        domain.OUTCOME_COLLECTION,
    ):
        for row in engine.store.find(collection, {}, limit=50):
            for key, value in row["data"].items():
                assert isinstance(key, str), (collection, key)
                assert isinstance(value, (str, int, float, bool, list, dict, type(None))), (
                    collection,
                    key,
                    value,
                )


# --------------------------------------------------------------------------- #
# The domain module's import surface
# --------------------------------------------------------------------------- #


def test_this_domain_module_imports_nothing_but_the_store():
    """An enforced property: no framework, no ``dsr.api``, no bare SQLite.

    The suite asserts this over every feature module; this test names it for this
    workflow's new file, so the reason survives a refactor of the package.
    """
    source_file = Path(domain.__file__)
    tree = ast.parse(source_file.read_text(encoding="utf-8"))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    assert imported, "the module imports nothing at all, which cannot be right"
    for name in imported:
        assert name != "sqlite3", name
        assert name != "fastapi", name
        assert not name.startswith("starlette"), name
        assert name != "dsr.api", name
        if name == "dsr" or name.startswith("dsr."):
            assert name == "dsr.store", name


# --------------------------------------------------------------------------- #
# The feature module's own contract
# --------------------------------------------------------------------------- #


def test_the_feature_declares_a_ticket_derived_prefix():
    feature = load_feature(MODULE)
    assert feature.router.prefix == "/api/wf-134"
    assert feature.FEATURE["ticket"] == "WF-134"
    assert feature.FEATURE["id"] == "wf-134-deal-health"


def test_every_route_sits_under_this_features_own_prefix():
    feature = load_feature(MODULE)
    for route in feature.router.routes:
        assert route.path.startswith("/api/wf-134"), route.path


def test_every_engine_write_requires_a_source_and_has_no_default():
    """A write cannot be reached without naming the route that served it."""
    import inspect

    for name in (
        "create_deal",
        "update_deal",
        "record_event",
        "add_stakeholder",
        "update_stakeholder",
        "add_content",
        "review_content",
        "score",
        "resolve_alert",
        "record_outcome",
    ):
        signature = inspect.signature(getattr(DealHealthEngine, name))
        assert "source" in signature.parameters, name
        assert signature.parameters["source"].default is inspect.Parameter.empty, name


def test_the_vocabulary_publishes_the_decisions_the_issue_left_open():
    served = domain.vocabulary()
    assert served["frameworks"] == list(domain.FRAMEWORKS)
    assert served["bands"] == list(domain.BANDS)
    assert served["event_kinds"] == list(domain.EVENT_KINDS)
    assert served["outcome_states"] == list(domain.OUTCOME_STATES)
    for key in ("framework", "score_formula", "decay_window", "coaching_scope"):
        assert key in served["decisions"], key
    # The three surfaces the spec names and this build does not carry.
    assert any("Live coaching" in note for note in served["not_implemented"])
    assert any("CRM" in note for note in served["not_implemented"])
    assert any("guarantees" in note for note in served["not_implemented"])


def test_the_seed_string_is_encodable_by_cp1252(db: AuditedDatabase):
    """The seeder prints it on a Windows console, so every character must encode.

    A single RIGHTWARDS ARROW in one recovered feature broke the entire seeder.
    """
    feature = load_feature(MODULE)
    room_ids = [
        (db.create("room", {"name": name}, actor="seed", source="seed")["id"], name)
        for name in ("Northwind", "Vantage")
    ]
    reported = feature.seed(db, {"room_ids": room_ids, "now": NOW})
    assert reported
    reported.encode("cp1252")
    assert reported == reported.encode("cp1252").decode("cp1252")


def test_the_seed_creates_the_states_the_issue_asks_for(db: AuditedDatabase):
    """The demo must show all three bands, an alert and a closed cue, or a
    reviewer cannot see the workflow doing its work."""
    feature = load_feature(MODULE)
    room_ids = [
        (db.create("room", {"name": name}, actor="seed", source="seed")["id"], name)
        for name in ("Northwind", "Vantage")
    ]
    feature.seed(db, {"room_ids": room_ids, "now": NOW})

    engine = DealHealthEngine(RecordStore(db), now=lambda: NOW)
    counted = engine.summary(room_ids[0][0])
    assert counted["deals"] == 3
    assert counted["healthy"] >= 1
    assert counted["watch"] >= 1
    assert counted["at_risk"] >= 1
    assert counted["open_alerts"] >= 1
    assert counted["open_coaching_cues"] >= 1
    assert counted["coaching_outcomes"] >= 2
    assert 0 < counted["readiness"] < 1


def test_the_seed_reports_nothing_for_a_context_with_no_rooms(db: AuditedDatabase):
    feature = load_feature(MODULE)
    assert feature.seed(db, {"room_ids": [], "now": NOW}) == ""
