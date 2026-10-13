"""WF-117 domain tests: the rules and the engine, on a frozen clock.

Every test builds its own opportunities and its own database, so the file passes
alone and under pytest-xdist. The clock is frozen to a fixed instant, so no test
depends on today's date or on a weekday.
"""

from __future__ import annotations

import pathlib
from datetime import datetime, timedelta, timezone

import pytest
from dsr.seller_tasks import vocabulary as task_vocabulary
from dsr.stale_opportunities import rules, vocabulary as v
from dsr.stale_opportunities.engine import StaleOpportunitySweepEngine
from dsr.stale_opportunities.errors import SweepConflict, SweepNotFound, SweepValidationError
from dsr.store import RecordStore

NOW = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
DAY_MS = 24 * 60 * 60 * 1000
TASK_COLLECTION = task_vocabulary.TASK_COLLECTION


def engine_for(store: RecordStore, *, task_engine=None) -> StaleOpportunitySweepEngine:
    return StaleOpportunitySweepEngine(store, now=lambda: NOW, task_engine=task_engine)


def ago(days: float) -> str:
    return (NOW - timedelta(days=days)).isoformat()


def make_opportunity(
    store: RecordStore,
    *,
    name: str = "Northwind - Q4",
    stage: str = "Qualification",
    stage_entered_days: float | None = 30,
    last_activity_days: float | None = 25,
    value: float | None = 120000,
    owner: str | None = "dana",
    object_name: str = v.OPPORTUNITY_OBJECT,
    room_id: str | None = None,
    **extra,
) -> dict:
    data: dict = {"object": object_name, "name": name, "stage": stage, "owner": owner, **extra}
    if value is not None:
        data["value"] = value
    if stage_entered_days is not None:
        data["stage_entered_at"] = ago(stage_entered_days)
    if last_activity_days is not None:
        data["last_activity_at"] = ago(last_activity_days)
    return store.create(
        v.OPPORTUNITY_COLLECTION, data, room_id=room_id, actor="dana", source="seed"
    )


def make_sweep(engine: StaleOpportunitySweepEngine, **overrides) -> dict:
    payload = {"name": "Stale sweep", "threshold_days": 7, "published": True}
    payload.update(overrides)
    return engine.create_sweep(payload, source="POST /api/wf-117/sweeps", actor="dana")


# --------------------------------------------------------------------------- #
# The closed vocabularies
# --------------------------------------------------------------------------- #


def test_the_triggers_are_exactly_the_researched_family():
    assert v.TRIGGERS == (
        "Stale Opportunities",
        "Opportunity Status Changed",
        "Opportunity Created",
        "Opportunity Changed",
        "Pipeline Stage Changed",
    )
    assert v.EMITTED_EVENTS == ("Pipeline Stage Changed", "Opportunity Changed")
    assert v.STATUSES == ("Open", "Won", "Lost")


def test_the_stale_rule_vocabulary_is_the_chosen_reading():
    assert v.STALE_REFERENCES == ("last_activity", "stage_entry", "latest_of_both")
    assert v.DEFAULT_STALE_REFERENCE == "latest_of_both"
    assert v.STEP_KINDS == ("nudge", "escalate")
    assert v.DEFAULT_CADENCE == (
        {"after_days": 7, "action": "nudge"},
        {"after_days": 21, "action": "escalate"},
    )
    assert v.DEFAULT_REFIRE_CONTROL == "marker_on_opportunity"
    assert v.TRIGGER_STALE in v.ACTIONS or v.ACTION_UPDATE_OPPORTUNITY in v.ACTIONS
    assert v.ESCALATION_ACTIONS == (
        "Create/Update Opportunity",
        "Add Task",
        "Send Internal Notification",
    )


def test_the_vocabulary_payload_publishes_the_rules_and_the_gaps():
    payload = StaleOpportunitySweepEngine.vocabulary()
    assert payload["ticket"] == "WF-117"
    assert payload["collections"]["sweep"] == "wf117_sweep"
    assert payload["collections"]["opportunity_read"] == v.OPPORTUNITY_COLLECTION
    assert payload["sourced_quotes"]["trigger"].startswith("[sourced]")
    assert payload["sourced_gaps"]
    assert payload["reason_labels"]["already_escalated"] == "Already escalated"
    assert payload["user_flow"][0]["step"] == "1"


def test_every_inference_names_a_choice_and_a_basis():
    from dsr.stale_opportunities import inferences

    for entry in inferences.INFERENCES:
        assert entry["question"]
        assert entry["options"]
        assert entry["choice"] in entry["options"] or entry["basis"] == "sourced"
        assert entry["basis"] in {"sourced", "inferred"}
        assert entry["consequence"]
    assert inferences.by_id("stale_reference_is_latest_of_both")["choice"] == "latest_of_both"
    assert inferences.by_id("no_such_inference") is None


# --------------------------------------------------------------------------- #
# Timestamp rules
# --------------------------------------------------------------------------- #


def test_timestamps_read_numbers_iso_strings_and_dates():
    assert rules.parse_timestamp(1_780_000_000_000, "t") == 1_780_000_000_000
    assert rules.parse_timestamp("1780000000000", "t") == 1_780_000_000_000
    assert rules.parse_timestamp("2026-06-01T12:00:00Z", "t") == rules.parse_timestamp(NOW, "t")
    assert rules.to_iso(rules.parse_timestamp("2026-06-01", "t")) == "2026-06-01T00:00:00.000+00:00"
    assert rules.parse_timestamp(None, "t") is None
    assert rules.parse_timestamp("", "t") is None
    with pytest.raises(SweepValidationError):
        rules.parse_timestamp("not a date", "t")
    with pytest.raises(SweepValidationError):
        rules.parse_timestamp(True, "t")


# --------------------------------------------------------------------------- #
# Payload rules
# --------------------------------------------------------------------------- #


def test_a_sweep_takes_the_researched_defaults():
    fields = rules.normalise_sweep_payload({"name": "S"})
    assert fields["trigger"] == v.TRIGGER_STALE
    assert fields["inactivity_basis"] == v.REFERENCE_LATEST_OF_BOTH
    assert fields["threshold_days"] == 7
    assert fields["cadence"] == [dict(step) for step in v.DEFAULT_CADENCE]
    assert fields["refire_control"] == v.REFIRE_MARKER_ON_OPPORTUNITY
    assert fields["published"] is False
    assert fields["filters"]["stages"] == []


def test_a_bad_sweep_names_every_bad_field_at_once():
    with pytest.raises(SweepValidationError) as failure:
        rules.normalise_sweep_payload(
            {"threshold_days": 0, "inactivity_basis": "vibes", "refire_control": "wishful"}
        )
    assert set(failure.value.errors) >= {"threshold_days", "inactivity_basis", "refire_control"}


def test_a_cadence_must_cover_the_threshold():
    with pytest.raises(SweepValidationError) as failure:
        rules.normalise_sweep_payload(
            {"threshold_days": 7, "cadence": [{"after_days": 30, "action": "escalate"}]}
        )
    assert "cadence" in failure.value.errors


def test_a_cadence_refuses_unknown_actions_and_shared_days():
    with pytest.raises(SweepValidationError):
        rules.normalise_sweep_payload({"cadence": [{"after_days": 3, "action": "shout"}]})
    with pytest.raises(SweepValidationError) as failure:
        rules.normalise_sweep_payload(
            {
                "cadence": [
                    {"after_days": 3, "action": "nudge"},
                    {"after_days": 3, "action": "escalate"},
                ]
            }
        )
    assert "cadence" in failure.value.errors


def test_a_value_band_refuses_an_inverted_range():
    with pytest.raises(SweepValidationError) as failure:
        rules.normalise_sweep_payload({"filters": {"value_min": 100, "value_max": 10}})
    assert "filters.value_min" in failure.value.errors


# --------------------------------------------------------------------------- #
# The opportunity view and the reference instant
# --------------------------------------------------------------------------- #


def test_an_opportunity_reads_from_its_top_level_and_from_a_wf039_body():
    from_body = rules.opportunity_view(
        {
            "id": "o1",
            "data": {
                "object": "Opportunity",
                "body": {
                    "StageName": "Negotiation",
                    "LastActivityDate": ago(3),
                    "Amount": "50000",
                },
            },
        }
    )
    assert from_body["stage"] == "Negotiation"
    assert from_body["value"] == 50000.0
    assert from_body["last_activity_at_ms"] == rules.parse_timestamp(ago(3), "t")


def test_the_latest_of_both_reference_picks_the_more_recent_field_view():
    view = {"last_activity_at_ms": 100, "stage_entered_at_ms": 300, "data": {}}
    reference, field = rules.reference_ms(view, v.REFERENCE_LATEST_OF_BOTH)
    assert reference == 300 and field == v.REFERENCE_STAGE_ENTRY
    view = {"last_activity_at_ms": 300, "stage_entered_at_ms": 100, "data": {}}
    reference, field = rules.reference_ms(view, v.REFERENCE_LATEST_OF_BOTH)
    assert reference == 300 and field == v.REFERENCE_LAST_ACTIVITY
    assert rules.reference_ms({"data": {}}, v.REFERENCE_LATEST_OF_BOTH) == (None, "latest_of_both")
    assert rules.reference_ms({"last_activity_at_ms": 5, "data": {}}, v.REFERENCE_STAGE_ENTRY) == (
        None,
        "stage_entry",
    )


# --------------------------------------------------------------------------- #
# Evaluation
# --------------------------------------------------------------------------- #


def test_an_opportunity_below_the_threshold_is_not_swept(store):
    engine = engine_for(store)
    sweep = make_sweep(engine)
    view = rules.opportunity_view(
        make_opportunity(store, last_activity_days=2, stage_entered_days=2)
    )
    decision = rules.evaluate_opportunity(view, sweep, rules.parse_timestamp(NOW, "t"))
    assert decision["matched"] is False
    assert decision["reason"] == v.REASON_BELOW_THRESHOLD


def test_an_opportunity_with_no_evidence_is_reported_not_swept(store):
    engine = engine_for(store)
    sweep = make_sweep(engine)
    view = rules.opportunity_view(
        make_opportunity(store, stage_entered_days=None, last_activity_days=None)
    )
    decision = rules.evaluate_opportunity(view, sweep, rules.parse_timestamp(NOW, "t"))
    assert decision["matched"] is False
    assert decision["reason"] == v.REASON_NO_EVIDENCE


def test_the_stage_and_value_filters_branch_before_escalation(store):
    engine = engine_for(store)
    sweep = make_sweep(
        engine,
        filters={"stages": ["Closed Won"], "stage_mode": "exclude"},
    )
    closed = rules.opportunity_view(make_opportunity(store, stage="Closed Won"))
    assert (
        rules.evaluate_opportunity(closed, sweep, rules.parse_timestamp(NOW, "t"))["reason"]
        == v.REASON_STAGE_FILTERED
    )

    banded = make_sweep(engine, filters={"value_min": 10000, "value_max": 500000})
    cheap = rules.opportunity_view(make_opportunity(store, value=3000))
    assert (
        rules.evaluate_opportunity(cheap, banded, rules.parse_timestamp(NOW, "t"))["reason"]
        == v.REASON_VALUE_FILTERED
    )

    unknown = rules.opportunity_view(make_opportunity(store, value=None))
    assert (
        rules.evaluate_opportunity(unknown, banded, rules.parse_timestamp(NOW, "t"))["reason"]
        == v.REASON_VALUE_FILTERED
    )


def test_the_cadence_step_is_the_furthest_wait_reached(store):
    engine = engine_for(store)
    sweep = make_sweep(engine)
    now_ms = rules.parse_timestamp(NOW, "t")
    nudge = rules.evaluate_opportunity(
        rules.opportunity_view(make_opportunity(store, last_activity_days=9, stage_entered_days=9)),
        sweep,
        now_ms,
    )
    assert nudge["matched"] is True and nudge["step"]["action"] == v.STEP_NUDGE
    escalate = rules.evaluate_opportunity(
        rules.opportunity_view(
            make_opportunity(store, last_activity_days=25, stage_entered_days=25)
        ),
        sweep,
        now_ms,
    )
    assert escalate["step"]["action"] == v.STEP_ESCALATE


def test_a_sweep_only_scans_opportunity_objects(store):
    engine = engine_for(store)
    make_opportunity(store, name="Real opportunity")
    make_opportunity(store, name="A contact", object_name="Contact")
    assert [entry["name"] for entry in engine.opportunities()] == ["Real opportunity"]


# --------------------------------------------------------------------------- #
# The escalation writes
# --------------------------------------------------------------------------- #


def test_a_published_sweep_escalates_with_a_task_a_notification_and_a_marker(store):
    engine = engine_for(store)
    opportunity = make_opportunity(store)
    sweep = make_sweep(engine)

    run = engine.sweep(sweep["id"], source="POST /api/wf-117/sweeps/x/run", actor="dana")

    assert run["scanned"] == 1
    assert run["escalated"] == 1
    assert run["failed"] == 0
    assert run["by_reason"] == {v.REASON_MATCHED: 1}

    refreshed = store.get(opportunity["id"])["data"]
    assert refreshed[v.MARKER_FLAG] is True
    assert refreshed[v.MARKER_SWEEP_ID] == sweep["id"]
    assert refreshed[v.MARKER_AT_MS] == rules.parse_timestamp(NOW, "t")

    tasks = store.find(TASK_COLLECTION, {"assignee": "dana"})
    assert len(tasks) == 1
    assert tasks[0]["data"]["priority"] == task_vocabulary.PRIORITY_HIGH

    escalations = engine.list_escalations(sweep_id=sweep["id"])
    assert len(escalations) == 1
    assert escalations[0]["task_id"] == tasks[0]["id"]

    notifications = engine.list_notifications(sweep_id=sweep["id"])
    assert notifications[0]["team"] == "sales"
    assert "Escalate" not in notifications[0]["message"]
    assert notifications[0]["action"] == v.STEP_ESCALATE


def test_the_marker_stops_a_second_run_from_re_firing(store):
    engine = engine_for(store)
    make_opportunity(store)
    sweep = make_sweep(engine)

    engine.sweep(sweep["id"], source="src", actor="dana")
    second = engine.sweep(sweep["id"], source="src", actor="dana")

    assert second["escalated"] == 0
    assert second["by_reason"] == {v.REASON_ALREADY_ESCALATED: 1}
    assert len(store.find(TASK_COLLECTION, {})) == 1


def test_new_activity_after_the_marker_re_qualifies_the_opportunity(store):
    engine = engine_for(store)
    opportunity = make_opportunity(store)
    sweep = make_sweep(engine)
    engine.sweep(sweep["id"], source="src", actor="dana")

    # Activity five days after the escalation, then thirty-five more idle days.
    later_activity = NOW + timedelta(days=5)
    store.update(opportunity["id"], {"last_activity_at": later_activity.isoformat()}, source="src")

    at_ms = rules.parse_timestamp(NOW + timedelta(days=40), "t")
    again = engine.sweep(sweep["id"], source="src", at_ms=at_ms)
    assert again["escalated"] == 1


def test_an_unpublished_sweep_refuses_to_run(store):
    engine = engine_for(store)
    make_opportunity(store)
    sweep = make_sweep(engine, published=False)
    with pytest.raises(SweepConflict) as failure:
        engine.sweep(sweep["id"], source="src")
    assert failure.value.reason == "sweep_not_published"


def test_moving_the_stage_rewrites_the_stage_entry(store):
    engine = engine_for(store)
    make_opportunity(store)
    sweep = make_sweep(engine, escalate_to_stage="Escalated")
    engine.sweep(sweep["id"], source="src")
    view = engine.opportunities()[0]
    assert view["stage"] == "Escalated"
    assert view["stage_entered_at_ms"] == rules.parse_timestamp(NOW, "t")


def test_a_value_bump_updates_the_opportunity_value(store):
    engine = engine_for(store)
    opportunity = make_opportunity(store, value=50000)
    sweep = make_sweep(engine, value_bump=1000)
    engine.sweep(sweep["id"], source="src")
    assert store.get(opportunity["id"])["data"]["value"] == 51000


def test_a_failed_task_leaves_the_opportunity_eligible_and_is_recorded(store):
    class BoomTaskEngine:
        def create(self, *args, **kwargs):
            raise RuntimeError("the task store refused")

    engine = engine_for(store, task_engine=BoomTaskEngine())
    opportunity = make_opportunity(store)
    sweep = make_sweep(engine)

    run = engine.sweep(sweep["id"], source="src")

    assert run["failed"] == 1
    assert run["escalated"] == 0
    assert run["results"][0]["error"]["code"] == "RuntimeError"
    # The marker is written last, so a failed task does not take the opportunity
    # out of the sweep: the next run retries it.
    assert store.get(opportunity["id"])["data"].get(v.MARKER_FLAG) is None
    assert engine.evaluate(sweep["id"])["matched"] == 1


def test_evaluate_writes_nothing(store):
    engine = engine_for(store)
    make_opportunity(store)
    sweep = make_sweep(engine)
    before = store.db.count(v.RUN_COLLECTION)
    evaluation = engine.evaluate(sweep["id"])
    assert evaluation["matched"] == 1
    assert store.db.count(v.RUN_COLLECTION) == before
    assert store.find(TASK_COLLECTION, {}) == []


# --------------------------------------------------------------------------- #
# Sweeps, runs and the summary
# --------------------------------------------------------------------------- #


def test_update_and_delete_a_sweep(store):
    engine = engine_for(store)
    sweep = make_sweep(engine)
    updated = engine.update_sweep(
        sweep["id"], {"threshold_days": 14, "name": "Renamed"}, source="PATCH /api/wf-117/sweeps/x"
    )
    assert updated["threshold_days"] == 14
    assert updated["name"] == "Renamed"
    # cadence 7/21 still covers the 14-day threshold, so the update is accepted
    assert engine.set_published(sweep["id"], False, source="src")["published"] is False
    engine.delete_sweep(sweep["id"], source="DELETE /api/wf-117/sweeps/x")
    with pytest.raises(SweepNotFound):
        engine.get_sweep(sweep["id"])


def test_a_run_records_its_results_and_the_summary_counts_them(store):
    engine = engine_for(store)
    make_opportunity(store, name="Stale one")
    make_opportunity(store, name="Fresh one", last_activity_days=1, stage_entered_days=1)
    sweep = make_sweep(engine)
    run = engine.sweep(sweep["id"], source="src")

    assert {result["outcome"] for result in run["results"]} == {"escalated", "skipped"}
    assert engine.get_run(run["id"])["escalated"] == 1
    listed = engine.list_runs(sweep_id=sweep["id"])
    assert [entry["id"] for entry in listed] == [run["id"]]

    counts = engine.summary()
    assert counts["sweeps"] == 1
    assert counts["published"] == 1
    assert counts["opportunities"] == 2
    assert counts["runs"] == 1
    assert counts["escalated_total"] == 1
    assert counts["notifications"] == 1


def test_an_unknown_run_is_a_not_found(store):
    engine = engine_for(store)
    with pytest.raises(SweepNotFound):
        engine.get_run("wf117_sweep_run_missing")


def test_records_stay_schema_flexible_with_no_migration(store):
    record = store.create(
        v.SWEEP_COLLECTION,
        {"name": "Extra field", "threshold_days": 7, "risk_band": "amber"},
        actor="dana",
        source="src",
    )
    assert store.find(v.SWEEP_COLLECTION, {"risk_band": "amber"})[0]["id"] == record["id"]


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
    import importlib

    feature = importlib.import_module(
        "dsr.features.wf117_sweep_stale_opportunities_and_escalate_the_pipeline"
    )
    text = pathlib.Path(feature.__file__).read_text(encoding="utf-8")
    assert "from dsr.api" not in text and "import dsr.api" not in text
    assert "import sqlite3" not in text
    assert feature.router.prefix == "/api/wf-117"
    assert feature.FEATURE["ticket"] == "WF-117"
    assert feature.FEATURE["id"] == "wf-117-stale-opportunities"
    assert set(feature.EXCEPTION_HANDLERS) == {SweepValidationError, SweepNotFound, SweepConflict}


def test_the_seed_returns_a_cp1252_safe_string_that_names_its_states(db):
    import importlib

    feature = importlib.import_module(
        "dsr.features.wf117_sweep_stale_opportunities_and_escalate_the_pipeline"
    )
    room = db.create("room", {"name": "Northwind"}, actor="seed", source="seed")
    message = feature.seed(db, {"room_ids": [(room["id"], "Northwind")], "now": NOW, "rng": None})
    assert message.encode("cp1252")
    assert "WF-117" in message
    store = RecordStore(db)
    opportunities = store.find(v.OPPORTUNITY_COLLECTION, {"object": v.OPPORTUNITY_OBJECT})
    assert len(opportunities) == 6
    assert len(store.find(v.ESCALATION_COLLECTION, {})) == 2
    # The second seeded run escalated nothing.
    runs = store.list(v.RUN_COLLECTION, limit=10)
    assert len(runs) == 2
    assert runs[0]["data"]["escalated"] == 0
