"""WF-120 domain tests: the rules and the engine, on a frozen clock.

Every test builds its own records and its own database, so the file passes alone
and under pytest-xdist. The clock is frozen to a fixed instant, so no test
depends on today's date or on a weekday.
"""

from __future__ import annotations

import importlib
import pathlib
from datetime import datetime, timedelta, timezone

import pytest
from dsr.schedule_enrollment import rules, vocabulary as v
from dsr.schedule_enrollment.engine import ScheduleEnrollmentEngine
from dsr.schedule_enrollment.errors import (
    EnrollConflict,
    EnrollNotFound,
    EnrollValidationError,
)
from dsr.seller_tasks import vocabulary as task_vocabulary
from dsr.store import RecordStore

NOW = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
TASK_COLLECTION = task_vocabulary.TASK_COLLECTION
SRC = "POST /api/wf-120/schedules/x/run"


def engine_for(store: RecordStore, *, task_engine=None) -> ScheduleEnrollmentEngine:
    return ScheduleEnrollmentEngine(store, now=lambda: NOW, task_engine=task_engine)


def at_ms(moment: datetime) -> int:
    return rules.parse_timestamp(moment, "t")


def make_contact(
    store: RecordStore,
    *,
    name: str = "Northwind - Ada",
    stage: str = "Qualification",
    lifecycle_stage: str = "Lead",
    risk: str = "low",
    owner: str = "dana",
    object_name: str = v.DEFAULT_OBJECT,
    room_id: str | None = None,
    **extra,
) -> dict:
    data = {
        "object": object_name,
        "name": name,
        "stage": stage,
        "lifecycle_stage": lifecycle_stage,
        "risk": risk,
        "owner": owner,
        **extra,
    }
    return store.create(v.CRM_RECORD_COLLECTION, data, room_id=room_id, actor="dana", source="seed")


def make_schedule(engine: ScheduleEnrollmentEngine, **overrides) -> dict:
    payload = {"name": "Scheduled enrollment", "object": v.DEFAULT_OBJECT, "published": False}
    payload.update(overrides)
    return engine.create_schedule(payload, source="POST /api/wf-120/schedules", actor="dana")


def publish(engine: ScheduleEnrollmentEngine, schedule_id: str, backfill: str) -> dict:
    return engine.set_published(
        schedule_id, True, backfill=backfill, source="POST publish", actor="dana"
    )


# --------------------------------------------------------------------------- #
# The closed vocabularies
# --------------------------------------------------------------------------- #


def test_the_trigger_types_are_exactly_the_researched_family():
    assert v.TRIGGER_TYPES == (
        "Filter-based",
        "Event-based",
        "Based on a schedule",
        "Based on webhooks",
    )
    assert v.DEFAULT_TRIGGER == "Based on a schedule"


def test_the_state_machine_and_the_two_controls_are_the_chosen_reading():
    assert v.STATUSES == ("queued", "completed", "unenrolled")
    assert v.ACTIVE_STATUSES == ("queued",)
    assert v.TERMINAL_STATUSES == ("completed", "unenrolled")
    assert v.REENROLL_MODES == ("single", "after_completion")
    assert v.DEFAULT_REENROLL_MODE == "single"
    assert v.BACKFILL_CHOICES == ("existing_and_future", "future_only")
    assert v.DEFAULT_BACKFILL == "existing_and_future"


def test_the_vocabulary_payload_publishes_the_rules_and_the_gaps():
    payload = ScheduleEnrollmentEngine.vocabulary()
    assert payload["ticket"] == "WF-120"
    assert payload["collections"]["schedule"] == "wf120_schedule"
    assert payload["collections"]["crm_record_read"] == v.CRM_RECORD_COLLECTION
    assert payload["implemented_actions"] == ["Create task", "Send internal notification"]
    assert payload["sourced_quotes"]["scheduled_deferral"].startswith("[sourced]")
    assert payload["sourced_gaps"]
    assert payload["reason_labels"]["re_enrolled"] == "Re-enrolled"
    assert payload["user_flow"][0]["step"] == "1"
    assert payload["retention"]["action_log_days"] == 90
    assert payload["webhook_event_limit"] == 100
    assert payload["webhook_content_type"] == "application/json"


def test_every_inference_names_a_choice_and_a_basis():
    from dsr.schedule_enrollment import inferences

    for entry in inferences.INFERENCES:
        assert entry["question"]
        assert entry["options"]
        assert entry["choice"] in entry["options"] or entry["basis"] == "sourced"
        assert entry["basis"] in {"sourced", "inferred"}
        assert entry["consequence"]
    assert inferences.by_id("deferral_is_not_exclusion")["choice"] == "deferred_then_eligible"
    assert inferences.by_id("no_such_inference") is None


# --------------------------------------------------------------------------- #
# Timestamp and retention rules
# --------------------------------------------------------------------------- #


def test_timestamps_read_numbers_iso_strings_and_dates():
    assert rules.parse_timestamp(1_780_000_000_000, "t") == 1_780_000_000_000
    assert rules.parse_timestamp("1780000000000", "t") == 1_780_000_000_000
    assert rules.parse_timestamp("2026-06-01T12:00:00Z", "t") == at_ms(NOW)
    assert rules.to_iso(rules.parse_timestamp("2026-06-01", "t")) == "2026-06-01T00:00:00.000+00:00"
    assert rules.parse_timestamp(None, "t") is None
    with pytest.raises(EnrollValidationError):
        rules.parse_timestamp("not a date", "t")
    with pytest.raises(EnrollValidationError):
        rules.parse_timestamp(True, "t")


def test_the_next_run_and_the_retention_cutoffs_are_computed():
    assert rules.next_run_at_ms(None, 60) is None
    assert rules.next_run_at_ms(at_ms(NOW), 60) == at_ms(NOW) + 60 * rules.MINUTE_MS
    assert rules.retention_cutoff_ms(at_ms(NOW), "action_log") == at_ms(NOW) - 90 * rules.DAY_MS
    assert rules.retention_cutoff_ms(at_ms(NOW), "enrollments") == at_ms(NOW) - 730 * rules.DAY_MS


# --------------------------------------------------------------------------- #
# Payload rules
# --------------------------------------------------------------------------- #


def test_a_schedule_takes_the_researched_defaults():
    fields = rules.normalise_schedule_payload({"name": "S"})
    assert fields["trigger"] == v.TRIGGER_SCHEDULE
    assert fields["object"] == v.DEFAULT_OBJECT
    assert fields["filters"] == []
    assert fields["unenroll_filter"] == []
    assert fields["interval_minutes"] == v.DEFAULT_INTERVAL_MINUTES
    assert fields["delay_minutes"] == v.DEFAULT_DELAY_MINUTES
    assert fields["re_enroll"] == v.REENROLL_SINGLE
    assert fields["backfill"] == v.DEFAULT_BACKFILL
    assert fields["actions"] == list(v.DEFAULT_ACTIONS)
    assert fields["published"] is False


def test_a_bad_schedule_names_every_bad_field_at_once():
    with pytest.raises(EnrollValidationError) as failure:
        rules.normalise_schedule_payload(
            {
                "interval_minutes": 0,
                "re_enroll": "each_match",
                "backfill": "wishful",
                "trigger": "Filter-based",
                "actions": ["Field update"],
            }
        )
    assert set(failure.value.errors) >= {
        "interval_minutes",
        "re_enroll",
        "backfill",
        "trigger",
        "actions[0]",
    }


def test_a_filter_condition_is_read_in_both_forms_and_reports_the_bad_row():
    errors: dict[str, str] = {}
    long_form = rules.normalise_conditions(
        [{"field": "stage", "op": "in", "value": "Qualification, Discovery"}], errors, "filters"
    )
    assert long_form == [{"field": "stage", "op": "in", "value": ["Qualification", "Discovery"]}]

    shorthand = rules.normalise_conditions({"stage": "Qualification"}, {}, "filters")
    assert shorthand == [{"field": "stage", "op": "eq", "value": "Qualification"}]

    with pytest.raises(EnrollValidationError) as failure:
        rules.normalise_schedule_payload(
            {"filters": [{"field": "stage", "op": "wat", "value": 1}, {"field": ""}]}
        )
    assert "filters[0].op" in failure.value.errors
    assert "filters[1].field" in failure.value.errors


# --------------------------------------------------------------------------- #
# The filter matcher
# --------------------------------------------------------------------------- #


def test_every_operator_matches_what_it_says():
    assert rules.match_condition("Qualification", {"op": "eq", "value": "Qualification"})
    assert rules.match_condition("50", {"op": "eq", "value": 50})
    assert rules.match_condition("Lead", {"op": "ne", "value": "Customer"})
    assert rules.match_condition("Lead", {"op": "in", "value": ["Lead", "Customer"]})
    assert rules.match_condition("Lead", {"op": "not_in", "value": ["Customer"]})
    assert rules.match_condition("enterprise", {"op": "contains", "value": "ENTER"})
    assert rules.match_condition("set", {"op": "exists", "value": None})
    assert not rules.match_condition("", {"op": "exists", "value": None})
    assert rules.match_condition("50", {"op": "gt", "value": 10})
    assert rules.match_condition("50", {"op": "gte", "value": 50})
    assert rules.match_condition("5", {"op": "lt", "value": 10})
    assert rules.match_condition("10", {"op": "lte", "value": 10})
    assert not rules.match_condition("5", {"op": "gt", "value": 10})


def test_a_record_reads_from_its_top_level_and_from_a_wf039_body():
    assert rules.read_field({"object": "Contact"}, "object") == "Contact"
    assert rules.read_field({"body": {"StageName": "Negotiation"}}, "StageName") == "Negotiation"
    assert rules.read_field({"body": {"stage": {"name": "Won"}}}, "body.stage.name") == "Won"
    assert rules.read_field({"stage": ""}, "stage") is None


def test_an_empty_filter_matches_every_record():
    assert rules.record_matches({"stage": "Discovery"}, [])
    assert rules.record_matches(
        {"stage": "Discovery"}, [{"field": "stage", "op": "eq", "value": "Discovery"}]
    )
    assert not rules.record_matches(
        {"stage": "Discovery"}, [{"field": "stage", "op": "eq", "value": "Qualification"}]
    )


def test_the_eligibility_rule_is_the_chosen_reading():
    assert rules.eligibility(None, "single") == (True, v.REASON_MATCHED, False)
    assert rules.eligibility(None, "after_completion") == (True, v.REASON_MATCHED, False)
    assert rules.eligibility({"status": "queued"}, "after_completion") == (
        False,
        v.REASON_ALREADY_ENROLLED,
        False,
    )
    assert rules.eligibility({"status": "completed"}, "single") == (
        False,
        v.REASON_SINGLE_ENROLLMENT,
        False,
    )
    assert rules.eligibility({"status": "completed"}, "after_completion") == (
        True,
        v.REASON_RE_ENROLLED,
        True,
    )
    assert rules.eligibility({"status": "unenrolled"}, "after_completion") == (
        True,
        v.REASON_RE_ENROLLED,
        True,
    )
    assert rules.eligibility({"status": "unenrolled"}, "single") == (
        False,
        v.REASON_SINGLE_ENROLLMENT,
        False,
    )


# --------------------------------------------------------------------------- #
# The publish-time backfill choice
# --------------------------------------------------------------------------- #


def test_publishing_with_backfill_enrols_the_records_that_already_match(store):
    engine = engine_for(store)
    make_contact(store, name="Matches", stage="Qualification")
    make_contact(store, name="Does not match", stage="Discovery")
    schedule = make_schedule(
        engine, filters=[{"field": "stage", "op": "eq", "value": "Qualification"}]
    )

    result = publish(engine, schedule["id"], v.BACKFILL_EXISTING)

    assert result["schedule"]["published"] is True
    run = result["run"]
    assert run["kind"] == v.RUN_BACKFILL
    assert run["scanned"] == 2
    assert run["enrolled"] == 1
    assert run["released"] == 1
    enrollments = engine.enrollments(schedule_id=schedule["id"])
    assert len(enrollments) == 1
    assert enrollments[0]["status"] == v.STATUS_COMPLETED


def test_publishing_future_only_defers_and_the_next_run_enrols(store):
    engine = engine_for(store)
    make_contact(store, name="Matches", stage="Qualification")
    schedule = make_schedule(
        engine,
        filters=[{"field": "stage", "op": "eq", "value": "Qualification"}],
        backfill=v.BACKFILL_FUTURE_ONLY,
    )

    result = publish(engine, schedule["id"], v.BACKFILL_FUTURE_ONLY)

    run = result["run"]
    assert run["kind"] == v.RUN_ACTIVATION
    assert run["deferred"] == 1
    assert run["enrolled"] == 0
    assert engine.enrollments(schedule_id=schedule["id"]) == []

    # Deferral is not exclusion: the next scheduled run enrols the record.
    second = engine.run(schedule["id"], source=SRC, actor="dana")
    assert second["kind"] == v.RUN_SCHEDULE
    assert second["enrolled"] == 1
    assert second["released"] == 1


# --------------------------------------------------------------------------- #
# Re-enrollment control
# --------------------------------------------------------------------------- #


def test_single_enrollment_refuses_a_second_run(store):
    engine = engine_for(store)
    make_contact(store, name="Only once")
    schedule = make_schedule(engine)
    publish(engine, schedule["id"], v.BACKFILL_EXISTING)

    again = engine.run(schedule["id"], source=SRC, actor="dana")

    assert again["enrolled"] == 0
    assert again["by_reason"] == {v.REASON_SINGLE_ENROLLMENT: 1}
    assert len(engine.enrollments(schedule_id=schedule["id"])) == 1


def test_reenroll_after_completion_enrols_a_second_time(store):
    engine = engine_for(store)
    contact = make_contact(store, name="Completes")
    schedule = make_schedule(engine, re_enroll=v.REENROLL_AFTER_COMPLETION)
    publish(engine, schedule["id"], v.BACKFILL_EXISTING)

    again = engine.run(schedule["id"], source=SRC, actor="dana")

    assert again["re_enrolled"] == 1
    assert again["enrolled"] == 0
    assert again["released"] == 1
    enrollments = engine.enrollments(schedule_id=schedule["id"])
    assert len(enrollments) == 2
    assert all(row["record_id"] == contact["id"] for row in enrollments)


def test_a_currently_enrolled_record_is_refused_even_with_reenroll_on(store):
    engine = engine_for(store)
    make_contact(store, name="Queued")
    schedule = make_schedule(engine, re_enroll=v.REENROLL_AFTER_COMPLETION, delay_minutes=60)
    publish(engine, schedule["id"], v.BACKFILL_EXISTING)

    again = engine.run(schedule["id"], source=SRC, actor="dana")

    assert again["by_reason"] == {v.REASON_ALREADY_ENROLLED: 1}
    assert again["enrolled"] == 0


def test_an_unenrolled_record_may_re_enroll(store):
    engine = engine_for(store)
    make_contact(store, name="Leaves", stage="Qualification")
    make_contact(store, name="Stays", stage="Discovery")
    schedule = make_schedule(
        engine,
        re_enroll=v.REENROLL_AFTER_COMPLETION,
        delay_minutes=60,
        unenroll_filter=[{"field": "stage", "op": "eq", "value": "Qualification"}],
    )
    publish(engine, schedule["id"], v.BACKFILL_EXISTING)

    later = at_ms(NOW + timedelta(hours=2))
    release_run = engine.run(schedule["id"], source=SRC, actor="dana", at_ms=later)

    assert release_run["unenrolled"] == 1
    assert release_run["released"] == 1
    states = {
        row["record_id"]: row["status"] for row in engine.enrollments(schedule_id=schedule["id"])
    }
    assert v.STATUS_UNENROLLED in states.values()

    third = engine.run(
        schedule["id"], source=SRC, actor="dana", at_ms=at_ms(NOW + timedelta(hours=3))
    )
    assert third["re_enrolled"] == 2


# --------------------------------------------------------------------------- #
# The queue delay and the actions
# --------------------------------------------------------------------------- #


def test_the_queue_delay_holds_the_enrollment_until_a_later_run(store):
    engine = engine_for(store)
    contact = make_contact(store, name="Held")
    schedule = make_schedule(engine, delay_minutes=60)

    result = publish(engine, schedule["id"], v.BACKFILL_EXISTING)

    assert result["run"]["enrolled"] == 1
    assert result["run"]["released"] == 0
    enrollment = engine.enrollments(schedule_id=schedule["id"])[0]
    assert enrollment["status"] == v.STATUS_QUEUED
    assert enrollment["release_at_ms"] == at_ms(NOW) + 60 * rules.MINUTE_MS
    assert store.find(TASK_COLLECTION, {}) == []

    later = engine.run(
        schedule["id"], source=SRC, actor="dana", at_ms=at_ms(NOW + timedelta(hours=2))
    )
    assert later["released"] == 1
    refreshed = engine.get_enrollment(enrollment["id"])
    assert refreshed["status"] == v.STATUS_COMPLETED
    assert refreshed["task_id"].startswith("wf116_task_")
    tasks = store.find(TASK_COLLECTION, {})
    assert len(tasks) == 1
    assert tasks[0]["data"]["assignee"] == "dana"
    # The marker on the CRM record says the record completed.
    assert store.get(contact["id"])["data"][v.MARKER_STATE] == v.STATUS_COMPLETED


def test_an_enrollment_writes_the_marker_and_the_workflow_log(store):
    engine = engine_for(store)
    contact = make_contact(store, name="Logged")
    schedule = make_schedule(engine)
    publish(engine, schedule["id"], v.BACKFILL_EXISTING)

    marker = store.get(contact["id"])["data"]
    assert marker[v.MARKER_SCHEDULE] == schedule["id"]
    assert marker[v.MARKER_STATE] == v.STATUS_COMPLETED
    assert marker[v.MARKER_AT_MS] == at_ms(NOW)

    events = engine.events(schedule_id=schedule["id"])
    kinds = {event["kind"] for event in events}
    assert {v.EVENT_ENROLLED, v.EVENT_RELEASED, v.EVENT_COMPLETED, v.EVENT_RUN} <= kinds
    notifications = engine.notifications(schedule_id=schedule["id"])
    assert len(notifications) == 1
    assert notifications[0]["message"].startswith("[Scheduled enrollment]")


def test_a_delayed_enrollment_is_re_enrolled_as_a_second_row(store):
    engine = engine_for(store)
    make_contact(store, name="Delayed once")
    schedule = make_schedule(engine, re_enroll=v.REENROLL_AFTER_COMPLETION, delay_minutes=30)
    publish(engine, schedule["id"], v.BACKFILL_EXISTING)
    engine.run(schedule["id"], source=SRC, actor="dana", at_ms=at_ms(NOW + timedelta(hours=1)))
    second = engine.run(
        schedule["id"], source=SRC, actor="dana", at_ms=at_ms(NOW + timedelta(hours=2))
    )
    assert second["re_enrolled"] == 1
    assert second["by_reason"][v.REASON_RE_ENROLLED] == 1


def test_a_failed_action_leaves_the_enrollment_queued_and_is_recorded(store):
    class BoomTaskEngine:
        def create(self, *args, **kwargs):
            raise RuntimeError("the task store refused")

    engine = engine_for(store, task_engine=BoomTaskEngine())
    make_contact(store, name="Fails")
    schedule = make_schedule(engine)
    publish(engine, schedule["id"], v.BACKFILL_EXISTING)

    assert engine.enrollments(schedule_id=schedule["id"])[0]["status"] == v.STATUS_QUEUED
    run = engine.run(schedule["id"], source=SRC, actor="dana")
    assert run["failed"] == 1
    assert run["released"] == 0
    assert run["results"][-1]["error"]["code"] == "RuntimeError"
    # The enrollment stays queued, so the next run retries it.
    assert engine.enrollments(schedule_id=schedule["id"])[0]["status"] == v.STATUS_QUEUED


# --------------------------------------------------------------------------- #
# Preview, conflicts and the summary
# --------------------------------------------------------------------------- #


def test_evaluate_writes_nothing(store):
    engine = engine_for(store)
    make_contact(store, name="Preview only")
    schedule = make_schedule(engine)
    before = store.db.count(v.RUN_COLLECTION)
    evaluation = engine.evaluate(schedule["id"])
    assert evaluation["scanned"] == 1
    assert evaluation["matched"] == 1
    assert evaluation["decisions"][0]["reason"] == v.REASON_MATCHED
    assert store.db.count(v.RUN_COLLECTION) == before
    assert engine.enrollments(schedule_id=schedule["id"]) == []
    assert store.find(TASK_COLLECTION, {}) == []


def test_an_unpublished_schedule_refuses_to_run(store):
    engine = engine_for(store)
    make_contact(store)
    schedule = make_schedule(engine, published=False)
    with pytest.raises(EnrollConflict) as failure:
        engine.run(schedule["id"], source=SRC)
    assert failure.value.reason == "schedule_not_published"


def test_a_schedule_only_scans_the_object_it_names(store):
    engine = engine_for(store)
    make_contact(store, name="Real contact")
    make_contact(store, name="A deal", object_name="Deal")
    assert [entry["name"] for entry in engine.records(object=v.DEFAULT_OBJECT)] == ["Real contact"]


def test_update_delete_and_read_back_a_schedule(store):
    engine = engine_for(store)
    schedule = make_schedule(engine)
    updated = engine.update_schedule(
        schedule["id"], {"interval_minutes": 30, "name": "Renamed"}, source="PATCH"
    )
    assert updated["interval_minutes"] == 30
    assert updated["name"] == "Renamed"
    assert updated["next_run_at_ms"] is None
    assert (
        engine.set_published(schedule["id"], False, source="POST publish")["schedule"]["published"]
        is False
    )
    engine.delete_schedule(schedule["id"], source="DELETE")
    with pytest.raises(EnrollNotFound):
        engine.get_schedule(schedule["id"])


def test_a_run_records_its_results_and_the_summary_counts_them(store):
    engine = engine_for(store)
    make_contact(store, name="Matches")
    make_contact(store, name="Does not", stage="Discovery")
    schedule = make_schedule(
        engine, filters=[{"field": "stage", "op": "eq", "value": "Qualification"}]
    )
    publish(engine, schedule["id"], v.BACKFILL_EXISTING)

    with pytest.raises(EnrollNotFound):
        engine.get_run("wf120_run_missing")

    counts = engine.summary()
    assert counts["schedules"] == 1
    assert counts["published"] == 1
    assert counts["records"] == 2
    assert counts["enrollments"] == 1
    assert counts["completed"] == 1
    assert counts["enrolled_total"] == 1
    assert counts["released_total"] == 1
    assert counts["notifications"] == 1


def test_records_stay_schema_flexible_with_no_migration(store):
    record = store.create(
        v.SCHEDULE_COLLECTION,
        {"name": "Extra field", "object": "Contact", "risk_band": "amber"},
        actor="dana",
        source="src",
    )
    assert store.find(v.SCHEDULE_COLLECTION, {"risk_band": "amber"})[0]["id"] == record["id"]


# --------------------------------------------------------------------------- #
# The contract guards
# --------------------------------------------------------------------------- #


def test_the_domain_package_owns_no_shared_file_and_opens_no_connection():
    package = pathlib.Path(rules.__file__).parent
    for module in sorted(package.glob("*.py")):
        text = module.read_text(encoding="utf-8")
        assert "from dsr.api" not in text and "import dsr.api" not in text, module.name
        assert "import sqlite3" not in text, module.name
        assert "sqlite3.connect" not in text, module.name
        assert "fastapi" not in text, module.name
        assert "dsr.deps" not in text, module.name
        assert "AuditedDatabase(" not in text, module.name


def test_the_feature_module_declares_its_prefix_and_owns_no_connection():
    feature = importlib.import_module(
        "dsr.features.wf120_enroll_records_on_a_schedule_with_re_enrollment_control"
    )
    text = pathlib.Path(feature.__file__).read_text(encoding="utf-8")
    assert "from dsr.api" not in text and "import dsr.api" not in text
    assert "import sqlite3" not in text
    assert feature.router.prefix == "/api/wf-120"
    assert feature.FEATURE["ticket"] == "WF-120"
    assert feature.FEATURE["id"] == "wf-120-schedule-enrollment"
    assert set(feature.EXCEPTION_HANDLERS) == {
        EnrollValidationError,
        EnrollNotFound,
        EnrollConflict,
    }


def test_the_seed_returns_a_cp1252_safe_string_that_names_its_states(db):
    feature = importlib.import_module(
        "dsr.features.wf120_enroll_records_on_a_schedule_with_re_enrollment_control"
    )
    room = db.create("room", {"name": "Northwind"}, actor="seed", source="seed")
    message = feature.seed(db, {"room_ids": [(room["id"], "Northwind")], "now": NOW, "rng": None})
    assert message.encode("cp1252")
    assert "WF-120" in message
    store = RecordStore(db)
    assert len(store.find(v.CRM_RECORD_COLLECTION, {"object": v.DEFAULT_OBJECT})) == 6
    assert len(store.find(v.SCHEDULE_COLLECTION, {})) == 4
    assert len(store.find(v.ENROLLMENT_COLLECTION, {})) == 9
    statuses = [row["data"]["status"] for row in store.find(v.ENROLLMENT_COLLECTION, {})]
    assert statuses.count(v.STATUS_QUEUED) == 1
    assert statuses.count(v.STATUS_UNENROLLED) == 1
    assert len(store.find(TASK_COLLECTION, {})) == 7
