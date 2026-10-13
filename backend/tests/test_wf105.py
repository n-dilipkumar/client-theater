"""WF-105 domain tests: the rules, the engine, and the seeded states.

These drive :mod:`dsr.automation_engagement.series` directly, with a store the
test builds and a clock the test injects. No request, no server, so a failure here
names the rule rather than the transport.

Every test builds its own rows, so none depends on another's, and the clock is
frozen rather than read from the wall: a test that asserts on "now" is a time
bomb the day it runs late.
"""

from __future__ import annotations

import pathlib
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from dsr.automation_engagement.series import (
    ENROLLED,
    ENROLLMENT_COLLECTION,
    EVALUATION_COLLECTION,
    EXITED,
    POLL_INTERVAL_MINUTES,
    SERIES_COLLECTION,
    TAG_APPLICATION_COLLECTION,
    SeriesConflict,
    SeriesEngine,
    SeriesNotFound,
    SeriesRefused,
    TagNotInWorkspace,
    inferences as series_inferences,
    rules,
    vocabulary as vocab,
)
from dsr.db.audited import AuditedDatabase
from dsr.store import RecordStore

#: One instant, reused. Every instant a record carries is derived from it, so a
#: test never pins a date that will pass.
NOW = datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def engine(store: RecordStore) -> SeriesEngine:
    return SeriesEngine(store, clock=lambda: NOW)


def _series_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": "Expansion nurture",
        "reenter": True,
        "start_filters": [{"field": "plan", "value": "trial"}],
        "exit_filters": [{"field": "tags", "value": "ExitUpgradeSeries"}],
        "goal": {
            "outcome_filters": [{"field": "plan", "value": "premium"}],
            "match_window_days": 30,
        },
        "path": [
            {"kind": "message", "title": "Welcome"},
            {"kind": "rule", "title": "Wait"},
            {"kind": "tag", "title": "Tag", "tag": "Upgraded after Series"},
        ],
    }
    payload.update(overrides)
    return payload


def _two_tags(engine: SeriesEngine) -> None:
    engine.create_tag({"name": "Upgraded after Series"}, actor="dana", source="test")
    engine.create_tag({"name": "ExitUpgradeSeries"}, actor="dana", source="test")


def _live_series(engine: SeriesEngine) -> dict[str, Any]:
    _two_tags(engine)
    series = engine.create_series(_series_payload(), actor="dana", source="test")
    engine.publish_series(str(series["id"]), actor="dana", source="test")
    return series


# --------------------------------------------------------------------------- #
# Vocabulary and provenance
# --------------------------------------------------------------------------- #


def test_every_researched_term_carries_the_sentence_it_came_from():
    published = vocab.published_vocabulary()
    for key in (
        "poll_interval_minutes",
        "reenter_series",
        "tag_block",
        "existing_tag_only",
        "sticky_exit_rule",
        "goal_match_window_days",
        "tags_are_shared",
    ):
        entry = published[key]
        assert entry.get("quote"), key
        assert entry.get("source", "").startswith("https://"), key


def test_the_poll_cadence_is_the_researched_thirty_minutes():
    assert vocab.POLL_INTERVAL_MINUTES == 30
    assert vocab.published_vocabulary()["poll_interval_minutes"]["value"] == 30


def test_the_vocabulary_publishes_only_ascii():
    """The seeder prints a feature's summary on a cp1252 console; the page is utf-8."""

    import json

    text = json.dumps(vocab.published_vocabulary(), ensure_ascii=False)
    assert text == text.encode("cp1252", "ignore").decode("cp1252")


def test_the_inferences_name_the_three_open_questions_the_issue_raises():
    describe = series_inferences.describe()
    assert describe["count"] == len(series_inferences.INFERENCES)
    ids = set(describe["ids"])
    assert series_inferences.DERIVED_POLL_IS_ON_DEMAND in ids
    assert series_inferences.DERIVED_GOAL_IS_A_SECOND_FILTER in ids
    for entry in describe["inferences"]:
        assert entry["decision"] and entry["why"] and entry["change_if"], entry["id"]


# --------------------------------------------------------------------------- #
# Filters and matching
# --------------------------------------------------------------------------- #


def test_matches_reads_a_dotted_path_through_nested_data():
    contact = {"contact_id": "c1", "account": {"plan": "premium", "seats": 40}}
    assert rules.matches(contact, [{"field": "account.plan", "value": "premium"}])
    assert rules.matches(contact, [{"field": "account.seats", "value": 40}])
    assert not rules.matches(contact, [{"field": "account.plan", "value": "trial"}])


def test_a_missing_path_does_not_match_rather_than_raising():
    assert not rules.matches({"contact_id": "c1"}, [{"field": "plan", "value": "trial"}])


def test_booleans_and_numbers_compare_with_their_own_type():
    contact = {"spam": True, "seats": 3}
    assert rules.matches(contact, [{"field": "spam", "value": True}])
    assert not rules.matches(contact, [{"field": "spam", "value": False}])
    assert rules.matches(contact, [{"field": "seats", "value": 3}])
    assert not rules.matches(contact, [{"field": "seats", "value": 4}])


def test_normalise_filters_accepts_the_list_and_the_mapping_form():
    assert rules.normalise_filters([{"field": "plan", "value": "x"}], "f") == [
        {"field": "plan", "value": "x"}
    ]
    assert rules.normalise_filters({"plan": "x", "spam": True}, "f") == [
        {"field": "plan", "value": "x"},
        {"field": "spam", "value": True},
    ]


def test_normalise_filters_refuses_a_filter_with_no_field_or_no_value():
    with pytest.raises(SeriesRefused):
        rules.normalise_filters([{"value": "x"}], "f")
    with pytest.raises(SeriesRefused):
        rules.normalise_filters([{"field": "plan"}], "f")


def test_normalise_goal_defaults_the_window_to_the_researched_thirty_days():
    goal = rules.normalise_goal({"outcome_filters": [{"field": "plan", "value": "premium"}]})
    assert goal["match_window_days"] == 30


def test_normalise_goal_refuses_a_non_positive_window():
    with pytest.raises(SeriesRefused):
        rules.normalise_goal({"outcome_filters": [], "match_window_days": 0})


# --------------------------------------------------------------------------- #
# The workspace tag vocabulary and the tag block
# --------------------------------------------------------------------------- #


def test_select_tag_resolves_an_existing_tag_case_insensitively():
    tags = ["Upgraded after Series"]
    assert rules.select_tag({"tag": "upgraded after series"}, tags) == "Upgraded after Series"


def test_select_tag_refuses_a_tag_the_workspace_does_not_have():
    """ "You won't be able to create a new tag here"."""

    with pytest.raises(TagNotInWorkspace):
        rules.select_tag({"tag": "Newly invented"}, ["Upgraded after Series"])


def test_tag_block_with_no_tag_is_refused_by_field():
    with pytest.raises(TagNotInWorkspace) as excinfo:
        rules.select_tag({}, ["Upgraded after Series"])
    assert excinfo.value.errors["tag"] == "required"


# --------------------------------------------------------------------------- #
# The series definition
# --------------------------------------------------------------------------- #


def test_validate_series_requires_a_name_and_a_starting_rule_block():
    with pytest.raises(SeriesRefused):
        rules.validate_series(_series_payload(name=" "), [])
    with pytest.raises(SeriesRefused):
        rules.validate_series(_series_payload(start_filters=[]), ["Upgraded after Series"])


def test_validate_series_refuses_a_path_that_names_an_unknown_tag():
    with pytest.raises(TagNotInWorkspace):
        rules.validate_series(_series_payload(), ["ExitUpgradeSeries"])


def test_validate_series_refuses_an_unknown_block_kind():
    with pytest.raises(SeriesRefused):
        rules.validate_series(
            _series_payload(path=[{"kind": "webhook"}]), ["Upgraded after Series"]
        )


def test_validate_series_keeps_the_goal_separate_from_the_start_rule():
    series = rules.validate_series(_series_payload(), ["Upgraded after Series"])
    assert series["start_filters"] == [{"field": "plan", "value": "trial"}]
    assert series["goal"]["outcome_filters"] == [{"field": "plan", "value": "premium"}]
    assert series["state"] == "draft"


def test_validate_series_carries_an_unknown_field_through_untouched():
    series = rules.validate_series(
        _series_payload(personalisation_tag="north-star"), ["Upgraded after Series"]
    )
    assert series["personalisation_tag"] == "north-star"


# --------------------------------------------------------------------------- #
# Entry: the starting rule block, the toggle and the sticky exit
# --------------------------------------------------------------------------- #


def _entry(series: dict[str, Any], contact: dict[str, Any], prior: dict | None = None) -> dict:
    return rules.entry_decision(series, contact, prior)


def test_a_live_series_enrols_a_matching_contact():
    series = rules.validate_series(_series_payload(), ["Upgraded after Series"])
    series["state"] = "live"
    decision = _entry(series, {"contact_id": "c1", "plan": "trial"})
    assert decision["outcome"] == "enrolled"


def test_a_draft_series_does_not_enrol():
    series = rules.validate_series(_series_payload(), ["Upgraded after Series"])
    assert _entry(series, {"contact_id": "c1", "plan": "trial"})["outcome"] == "skipped_not_live"


def test_a_contact_that_does_not_match_the_starting_rule_is_skipped():
    series = rules.validate_series(_series_payload(), ["Upgraded after Series"])
    series["state"] = "live"
    assert _entry(series, {"contact_id": "c1", "plan": "free"})["outcome"] == "skipped_no_match"


def test_a_completed_contact_reenters_only_when_the_toggle_is_on():
    series = rules.validate_series(_series_payload(reenter=False), ["Upgraded after Series"])
    series["state"] = "live"
    decision = _entry(series, {"contact_id": "c1", "plan": "trial"}, {"state": "completed"})
    assert decision["outcome"] == "skipped_reenter_disabled"

    series = rules.validate_series(_series_payload(reenter=True), ["Upgraded after Series"])
    series["state"] = "live"
    decision = _entry(series, {"contact_id": "c1", "plan": "trial"}, {"state": "completed"})
    assert decision["outcome"] == "reentered"


def test_an_active_contact_is_not_enrolled_twice():
    series = rules.validate_series(_series_payload(), ["Upgraded after Series"])
    series["state"] = "live"
    decision = _entry(series, {"contact_id": "c1", "plan": "trial"}, {"state": "enrolled"})
    assert decision["outcome"] == "skipped_already_active"


def test_an_exited_contact_is_refused_before_the_starting_rule_is_read():
    """ "they will exit the series completely, and cannot re-enter"."""

    series = rules.validate_series(_series_payload(), ["Upgraded after Series"])
    series["state"] = "live"
    matching = _entry(series, {"contact_id": "c1", "plan": "trial"}, {"state": "exited"})
    assert matching["outcome"] == "skipped_exited_sticky"
    non_matching = _entry(series, {"contact_id": "c1", "plan": "free"}, {"state": "exited"})
    assert non_matching["outcome"] == "skipped_exited_sticky"


# --------------------------------------------------------------------------- #
# Exit rules and the Goal tab
# --------------------------------------------------------------------------- #


def test_an_empty_exit_rule_list_never_exits_a_contact():
    series = rules.validate_series(_series_payload(exit_filters=[]), ["Upgraded after Series"])
    assert rules.exit_matches(series, {"contact_id": "c1", "spam": True}) is False


def test_any_single_exit_filter_is_enough_to_exit():
    series = rules.validate_series(
        _series_payload(
            exit_filters=[
                {"field": "tags", "value": "ExitUpgradeSeries"},
                {"field": "spam", "value": True},
            ]
        ),
        ["Upgraded after Series"],
    )
    assert rules.exit_matches(series, {"contact_id": "c1", "tags": "ExitUpgradeSeries"})
    assert rules.exit_matches(series, {"contact_id": "c1", "spam": True})
    assert not rules.exit_matches(series, {"contact_id": "c1", "plan": "premium"})


def test_suggest_exit_reason_refuses_a_contact_that_does_not_match():
    series = rules.validate_series(_series_payload(), ["Upgraded after Series"])
    with pytest.raises(SeriesConflict):
        rules.suggest_exit_reason(series, {"contact_id": "c1", "plan": "premium"})


def test_goal_met_reads_the_outcome_filter_and_the_window():
    series = rules.validate_series(_series_payload(), ["Upgraded after Series"])
    enrollment = {"entered_at": rules.to_iso(NOW - timedelta(days=5))}
    met = rules.goal_met(series, {"contact_id": "c1", "plan": "premium"}, enrollment, NOW)
    assert met["met"] is True

    stale = {"entered_at": rules.to_iso(NOW - timedelta(days=40))}
    missed = rules.goal_met(series, {"contact_id": "c1", "plan": "premium"}, stale, NOW)
    assert missed["met"] is False
    assert "window" in missed["reason"]


def test_goal_is_not_satisfied_by_the_starting_rule():
    """The goal is a second filter, so matching the start does not meet it."""

    series = rules.validate_series(_series_payload(), ["Upgraded after Series"])
    enrollment = {"entered_at": rules.to_iso(NOW)}
    missed = rules.goal_met(series, {"contact_id": "c1", "plan": "trial"}, enrollment, NOW)
    assert missed["met"] is False


def test_goal_absent_is_reported_rather_than_passing_silently():
    series = rules.validate_series(_series_payload(goal=None), ["Upgraded after Series"])
    result = rules.goal_met(series, {"contact_id": "c1"}, {"entered_at": rules.to_iso(NOW)}, NOW)
    assert result["met"] is False


def test_due_for_poll_uses_the_researched_interval():
    series = {"poll_interval_minutes": POLL_INTERVAL_MINUTES}
    assert rules.due_for_poll(series, None, NOW) is True
    assert rules.due_for_poll(series, rules.to_iso(NOW - timedelta(minutes=31)), NOW) is True
    assert rules.due_for_poll(series, rules.to_iso(NOW - timedelta(minutes=10)), NOW) is False


# --------------------------------------------------------------------------- #
# The engine: tags, series, publish
# --------------------------------------------------------------------------- #


def test_a_tag_name_cannot_be_registered_twice(engine: SeriesEngine):
    engine.create_tag({"name": "Upgraded after Series"}, actor="dana", source="test")
    with pytest.raises(SeriesConflict):
        engine.create_tag({"name": "upgraded after series"}, actor="dana", source="test")


def test_create_series_refuses_a_tag_the_workspace_does_not_have(engine: SeriesEngine):
    _two_tags(engine)
    payload = _series_payload()
    payload["path"][2]["tag"] = "Not a real tag"
    with pytest.raises(TagNotInWorkspace):
        engine.create_series(payload, actor="dana", source="test")


def test_publishing_a_series_rechecks_its_tag_blocks(engine: SeriesEngine):
    _two_tags(engine)
    series = engine.create_series(_series_payload(), actor="dana", source="test")
    published = engine.publish_series(str(series["id"]), actor="dana", source="test")
    assert published["data"]["state"] == "live"
    with pytest.raises(SeriesConflict):
        engine.publish_series(str(series["id"]), actor="dana", source="test")


def test_reading_a_series_that_does_not_exist_is_not_found(engine: SeriesEngine):
    with pytest.raises(SeriesNotFound):
        engine.require_series("nope")


def test_every_write_requires_a_source(engine: SeriesEngine):
    """A missing source must be a TypeError, not a row with a null route."""

    with pytest.raises(TypeError):
        engine.create_tag({"name": "x"}, actor="dana")  # type: ignore[call-arg]


# --------------------------------------------------------------------------- #
# The engine: entry, progression, exit
# --------------------------------------------------------------------------- #


def test_enroll_then_walk_the_path_writes_the_tag_and_completes(engine: SeriesEngine):
    series = _live_series(engine)
    result = engine.evaluate_entry(
        str(series["id"]), {"contact_id": "c1", "plan": "trial"}, actor="dana", source="test"
    )
    assert result["outcome"] == "enrolled"

    steps = []
    for _ in range(3):
        moved = engine.advance(
            str(series["id"]),
            "c1",
            contact={"contact_id": "c1", "plan": "premium"},
            actor="dana",
            source="test",
        )
        steps.append(moved["outcome"])
    assert steps == ["advanced", "advanced", "completed"]

    applications = engine.tag_applications(contact_id="c1")
    assert len(applications) == 1
    assert applications[0]["data"]["tag"] == "Upgraded after Series"
    assert applications[0]["data"]["topic"] == "contact.user.tag.created"

    enrollment = engine.enrollments(contact_id="c1")[0]
    assert enrollment["data"]["state"] == "completed"
    assert enrollment["data"]["goal_met"] is True


def test_a_completed_contact_reenters_and_the_counter_moves(engine: SeriesEngine):
    series = _live_series(engine)
    engine.evaluate_entry(
        str(series["id"]), {"contact_id": "c1", "plan": "trial"}, actor="dana", source="test"
    )
    for _ in range(3):
        engine.advance(str(series["id"]), "c1", actor="dana", source="test")
    again = engine.evaluate_entry(
        str(series["id"]), {"contact_id": "c1", "plan": "trial"}, actor="dana", source="test"
    )
    assert again["outcome"] == "reentered"
    enrollment = engine.enrollments(contact_id="c1")[0]
    assert enrollment["data"]["reentry_count"] == 1
    assert enrollment["data"]["state"] == "enrolled"


def test_a_sticky_exit_survives_a_later_qualifying_event(engine: SeriesEngine):
    series = _live_series(engine)
    engine.evaluate_entry(
        str(series["id"]), {"contact_id": "c1", "plan": "trial"}, actor="dana", source="test"
    )
    exit_result = engine.record_exit(
        str(series["id"]),
        "c1",
        {"contact_id": "c1", "tags": "ExitUpgradeSeries"},
        actor="dana",
        source="test",
    )
    assert exit_result["exit_reason"] == "exit_rule"
    again = engine.evaluate_entry(
        str(series["id"]), {"contact_id": "c1", "plan": "trial"}, actor="dana", source="test"
    )
    assert again["outcome"] == "skipped_exited_sticky"
    assert engine.enrollments(contact_id="c1")[0]["data"]["state"] == EXITED


def test_advancing_an_enrollment_that_does_not_exist_is_not_found(engine: SeriesEngine):
    series = _live_series(engine)
    with pytest.raises(SeriesNotFound):
        engine.advance(str(series["id"]), "ghost", actor="dana", source="test")


def test_advancing_a_completed_enrollment_is_a_conflict(engine: SeriesEngine):
    series = _live_series(engine)
    engine.evaluate_entry(
        str(series["id"]), {"contact_id": "c1", "plan": "trial"}, actor="dana", source="test"
    )
    for _ in range(3):
        engine.advance(str(series["id"]), "c1", actor="dana", source="test")
    with pytest.raises(SeriesConflict):
        engine.advance(str(series["id"]), "c1", actor="dana", source="test")


def test_an_exit_rule_that_does_not_match_is_refused(engine: SeriesEngine):
    series = _live_series(engine)
    engine.evaluate_entry(
        str(series["id"]), {"contact_id": "c1", "plan": "trial"}, actor="dana", source="test"
    )
    with pytest.raises(SeriesConflict):
        engine.record_exit(
            str(series["id"]), "c1", {"contact_id": "c1"}, actor="dana", source="test"
        )


def test_a_contact_with_no_contact_id_is_refused_by_field(engine: SeriesEngine):
    series = _live_series(engine)
    with pytest.raises(SeriesRefused):
        engine.evaluate_entry(str(series["id"]), {"plan": "trial"}, actor="dana", source="test")


def test_a_draft_series_reports_a_skip_rather_than_enrolling(engine: SeriesEngine):
    _two_tags(engine)
    series = engine.create_series(_series_payload(), actor="dana", source="test")
    result = engine.evaluate_entry(
        str(series["id"]), {"contact_id": "c1", "plan": "trial"}, actor="dana", source="test"
    )
    assert result["outcome"] == "skipped_not_live"
    assert engine.enrollments() == []


# --------------------------------------------------------------------------- #
# The poll
# --------------------------------------------------------------------------- #


def test_the_poll_records_one_evaluation_and_updates_last_polled_at(engine: SeriesEngine):
    series = _live_series(engine)
    result = engine.poll_series(
        str(series["id"]),
        [
            {"contact": {"contact_id": "c1", "plan": "trial"}, "event_type": "purchase.made"},
            {"contact": {"contact_id": "c2", "plan": "free"}, "event_type": "purchase.made"},
        ],
        actor="dana",
        source="test",
    )
    assert result["enrolled"] == 1
    assert result["outcomes"]["skipped_no_match"] == 1
    assert len(engine.evaluations()) == 1
    refreshed = engine.require_series(str(series["id"]))
    assert refreshed["data"]["last_polled_at"]


def test_the_poll_refuses_a_series_that_is_not_live(engine: SeriesEngine):
    _two_tags(engine)
    series = engine.create_series(_series_payload(), actor="dana", source="test")
    with pytest.raises(SeriesConflict):
        engine.poll_series(
            str(series["id"]),
            [{"contact": {"contact_id": "c1", "plan": "trial"}}],
            actor="dana",
            source="test",
        )


def test_the_poll_can_advance_an_enrolling_contact(engine: SeriesEngine):
    series = _live_series(engine)
    result = engine.poll_series(
        str(series["id"]),
        [
            {
                "contact": {"contact_id": "c1", "plan": "trial"},
                "event_type": "purchase.made",
                "advance": True,
            }
        ],
        actor="dana",
        source="test",
    )
    assert result["advanced"] == 1
    assert engine.enrollments(contact_id="c1")[0]["data"]["position"] == 1


def test_a_series_is_due_until_it_is_polled(engine: SeriesEngine):
    series = _live_series(engine)
    before = engine.series_due(str(series["id"]))
    assert before["due"] is True
    assert before["next_due_at"] is None
    assert before["poll_interval_minutes"] == 30

    engine.poll_series(str(series["id"]), [], actor="dana", source="test")

    after = engine.series_due(str(series["id"]))
    assert after["due"] is False
    assert after["next_due_at"] == rules.to_iso(NOW + timedelta(minutes=30))


def test_due_series_names_only_live_series(engine: SeriesEngine):
    _two_tags(engine)
    draft = engine.create_series(_series_payload(), actor="dana", source="test")
    live = engine.create_series(_series_payload(name="Live one"), actor="dana", source="test")
    engine.publish_series(str(live["id"]), actor="dana", source="test")

    due = engine.due_series()
    ids = {entry["series_id"] for entry in due}
    assert str(live["id"]) in ids
    assert str(draft["id"]) not in ids


# --------------------------------------------------------------------------- #
# Reads
# --------------------------------------------------------------------------- #


def test_the_summary_counts_each_enrollment_state(engine: SeriesEngine):
    series = _live_series(engine)
    engine.evaluate_entry(
        str(series["id"]), {"contact_id": "c1", "plan": "trial"}, actor="dana", source="test"
    )
    engine.record_exit(
        str(series["id"]),
        "c1",
        {"contact_id": "c1", "tags": "ExitUpgradeSeries"},
        actor="dana",
        source="test",
    )
    engine.evaluate_entry(
        str(series["id"]), {"contact_id": "c2", "plan": "trial"}, actor="dana", source="test"
    )
    summary = engine.summary()
    assert summary["series"] == 1
    assert summary["live"] == 1
    assert summary["exited"] == 1
    assert summary["active"] == 1
    assert summary["poll_interval_minutes"] == 30


def test_the_catalog_reports_the_tag_blocks_a_series_names(engine: SeriesEngine):
    _live_series(engine)
    catalog = engine.catalog()
    assert len(catalog["series"]) == 1
    assert catalog["series"][0]["tag_blocks"][0]["tag"] == "Upgraded after Series"
    assert catalog["series"][0]["block_count"] == 3


def test_tag_applications_filter_by_tag(engine: SeriesEngine):
    series = _live_series(engine)
    engine.evaluate_entry(
        str(series["id"]), {"contact_id": "c1", "plan": "trial"}, actor="dana", source="test"
    )
    for _ in range(3):
        engine.advance(str(series["id"]), "c1", actor="dana", source="test")
    assert len(engine.tag_applications(tag="Upgraded after Series")) == 1
    assert engine.tag_applications(tag="Other") == []


# --------------------------------------------------------------------------- #
# Audit and the storage contract
# --------------------------------------------------------------------------- #


def test_every_write_lands_with_an_audit_row_naming_the_collection(db: AuditedDatabase):
    engine = SeriesEngine(RecordStore(db), clock=lambda: NOW)
    _two_tags(engine)
    series = engine.create_series(_series_payload(), actor="dana", source="POST /api/wf105/series")
    engine.publish_series(
        str(series["id"]), actor="dana", source="POST /api/wf105/series/{series_id}/publish"
    )
    assert db.audit_count(collection=SERIES_COLLECTION) == 2
    assert db.audit_count(collection=vocab.TAG_COLLECTION) == 2
    entry = db.audit(collection=SERIES_COLLECTION)[0]
    assert entry["source"] == "POST /api/wf105/series/{series_id}/publish"


def test_enrollments_tags_and_evaluations_are_ordinary_json_rows(db: AuditedDatabase):
    engine = SeriesEngine(RecordStore(db), clock=lambda: NOW)
    series = _live_series(engine)
    engine.evaluate_entry(
        str(series["id"]), {"contact_id": "c1", "plan": "trial"}, actor="dana", source="test"
    )
    assert db.count(ENROLLMENT_COLLECTION) == 1
    for _ in range(3):
        engine.advance(str(series["id"]), "c1", actor="dana", source="test")
    assert db.count(TAG_APPLICATION_COLLECTION) == 1
    assert db.count(EVALUATION_COLLECTION) == 1
    row = db.list(ENROLLMENT_COLLECTION)[0]
    assert row["data"]["state"] == ENROLLED or row["data"]["state"] == "completed"


# --------------------------------------------------------------------------- #
# The seed and the import surface
# --------------------------------------------------------------------------- #

SERIES_PACKAGE = (
    pathlib.Path(__file__).resolve().parents[1] / "dsr" / "automation_engagement" / "series"
)


def test_the_seed_creates_the_states_the_research_names(db: AuditedDatabase):
    from dsr.features.wf105_reenter_series_and_tag import seed

    summary = seed(db, {"room_ids": [("room-1", "Acct")], "now": NOW, "rng": None})
    assert isinstance(summary, str) and summary
    engine = SeriesEngine(RecordStore(db))
    states = {r["data"]["state"] for r in engine.enrollments()}
    assert {"completed", "enrolled", "exited"} <= states
    assert engine.tag_applications()
    assert engine.evaluations()


def test_the_seed_summary_is_encodable_in_cp1252(db: AuditedDatabase):
    """A RIGHTWARDS ARROW in one recovered feature broke the whole seeder."""

    from dsr.features.wf105_reenter_series_and_tag import seed

    summary = seed(db, {"room_ids": [("room-1", "Acct")], "now": NOW, "rng": None})
    assert summary.encode("cp1252").decode("cp1252") == summary


def test_the_domain_modules_import_nothing_but_the_store():
    for module in SERIES_PACKAGE.glob("*.py"):
        text = module.read_text(encoding="utf-8")
        assert "from dsr.api" not in text, module.name
        assert "import dsr.api" not in text, module.name
        assert "import sqlite3" not in text, module.name
        assert "sqlite3.connect" not in text, module.name


def test_the_engine_is_the_only_module_that_reaches_the_store():
    engine_text = (SERIES_PACKAGE / "engine.py").read_text(encoding="utf-8")
    assert "from dsr.store import RecordStore" in engine_text
    for module in SERIES_PACKAGE.glob("*.py"):
        if module.name == "engine.py":
            continue
        text = module.read_text(encoding="utf-8")
        assert "from dsr.store" not in text, module.name
