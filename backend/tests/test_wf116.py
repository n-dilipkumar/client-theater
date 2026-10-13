"""WF-116 domain tests: the rules and the engine, on a frozen clock.

Every test builds its own task and its own database, so the file passes alone and
under pytest-xdist. The clock is frozen to a fixed instant, so no test depends on
today's date or on a weekday.
"""

from __future__ import annotations

import pathlib
from datetime import datetime, timedelta, timezone

import pytest
from dsr.db.audited import AuditedDatabase
from dsr.seller_tasks import rules, vocabulary as v
from dsr.seller_tasks.engine import SellerTaskEngine
from dsr.seller_tasks.errors import TaskConflict, TaskNotFound, TaskValidationError
from dsr.store import RecordStore

NOW = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
DAY_MS = 24 * 60 * 60 * 1000


def engine_for(store: RecordStore) -> SellerTaskEngine:
    return SellerTaskEngine(store, now=lambda: NOW)


def make_contact(db: AuditedDatabase, *, email: str = "dana@northwind.example") -> str:
    record = db.create(
        rules.CONTACT_COLLECTION,
        {"object": "contact", "reference_id": "seed-contact", "body": {"email": email}},
        actor="dana",
        source="seed",
    )
    return record["id"]


def due(**overrides) -> dict:
    payload = {
        "subject": "Follow up with Northwind",
        "due_at": (NOW + timedelta(days=2)).isoformat(),
        "reminder_at": (NOW + timedelta(days=1)).isoformat(),
    }
    payload.update(overrides)
    return payload


# --------------------------------------------------------------------------- #
# The closed vocabularies
# --------------------------------------------------------------------------- #


def test_the_four_closed_vocabularies_are_exactly_the_researched_sets():
    assert v.STATUSES == ("NOT_STARTED", "COMPLETED")
    assert v.PRIORITIES == ("LOW", "MEDIUM", "HIGH")
    assert v.TASK_TYPES == ("EMAIL", "CALL", "TODO")
    assert v.TRIGGERS == ("Task Added", "Task Reminder", "Task Completed")
    assert v.REMINDER_UNIT == "unix_ms"
    assert v.REQUIRED_FIELDS == ("due_at",)


def test_the_vocabulary_payload_publishes_the_rules_and_the_gaps():
    payload = SellerTaskEngine.vocabulary()
    assert payload["ticket"] == "WF-116"
    assert payload["collections"]["task"] == "wf116_task"
    assert payload["recycling_bin_retention_days"] == 30
    assert payload["sourced_quotes"]["reminder_trigger"].startswith("[sourced]")
    assert payload["sourced_gaps"]
    assert set(payload["signal_rules"]) == set(v.SIGNALS)


def test_every_inference_names_a_choice_and_a_basis():
    from dsr.seller_tasks import inferences

    for entry in inferences.INFERENCES:
        assert entry["question"]
        assert entry["options"]
        assert entry["choice"] in entry["options"] or entry["basis"] == "sourced"
        assert entry["basis"] in {"sourced", "inferred"}
    assert inferences.by_id("recycling_bin_retention")["choice"] == "30_days"
    assert inferences.by_id("no_such_inference") is None


# --------------------------------------------------------------------------- #
# Timestamp rules
# --------------------------------------------------------------------------- #


def test_timestamps_read_numbers_iso_strings_and_dates():
    assert rules.parse_timestamp(1_780_000_000_000, "t") == 1_780_000_000_000
    assert rules.parse_timestamp("1780000000000", "t") == 1_780_000_000_000
    as_ms = rules.parse_timestamp("2026-06-01T12:00:00Z", "t")
    assert as_ms == rules.parse_timestamp(NOW, "t")
    # A date-only string means midnight UTC.
    midnight = rules.parse_timestamp("2026-06-01", "t")
    assert rules.to_iso(midnight) == "2026-06-01T00:00:00.000+00:00"
    assert rules.parse_timestamp(None, "t") is None
    assert rules.parse_timestamp("", "t") is None
    with pytest.raises(TaskValidationError):
        rules.parse_timestamp("not a date", "t")
    with pytest.raises(TaskValidationError):
        rules.parse_timestamp(True, "t")


# --------------------------------------------------------------------------- #
# Payload rules
# --------------------------------------------------------------------------- #


def test_a_due_time_is_the_only_required_field():
    with pytest.raises(TaskValidationError) as failure:
        rules.normalise_task_payload({"subject": "No due time"})
    assert "due_at" in failure.value.errors
    fields = rules.normalise_task_payload({"due_at": "2026-06-02"})
    assert fields["due_at_ms"] is not None
    assert fields["subject"] == rules.DEFAULT_SUBJECT
    assert fields["priority"] == v.PRIORITY_MEDIUM
    assert fields["task_type"] == v.TYPE_TODO
    assert fields["status"] == v.STATUS_NOT_STARTED
    assert fields["attachment"] == v.ATTACHMENT_STANDALONE


def test_a_reminder_after_the_due_time_is_refused():
    with pytest.raises(TaskValidationError) as failure:
        rules.normalise_task_payload(
            {
                "due_at": "2026-06-01T10:00:00Z",
                "reminder_at": "2026-06-01T11:00:00Z",
            }
        )
    assert "reminder_at" in failure.value.errors


def test_an_unknown_value_in_a_closed_vocabulary_is_refused():
    with pytest.raises(TaskValidationError) as failure:
        rules.normalise_task_payload(due(hs_task_status="PAUSED", hs_task_priority="URGENT"))
    assert "hs_task_status" in failure.value.errors
    assert "hs_task_priority" in failure.value.errors


def test_a_notification_needs_an_assignee():
    with pytest.raises(TaskValidationError) as failure:
        rules.normalise_task_payload(due(notify=True))
    assert "assignee" in failure.value.errors


def test_the_assignee_role_comes_from_wf077():
    fields = rules.normalise_task_payload(due(assignee_role="Manager"))
    assert fields["assignee_role"] == "Manager"
    assert rules.is_permitted_role("Owner")
    assert not rules.is_permitted_role("Wizard")
    with pytest.raises(TaskValidationError) as failure:
        rules.normalise_task_payload(due(assignee_role="Wizard"))
    assert "assignee_role" in failure.value.errors


def test_a_signal_maps_to_one_deterministic_task():
    for signal in v.SIGNALS:
        defaults = rules.signal_defaults(signal, rules.parse_timestamp(NOW, "t"))
        assert defaults["subject"]
        assert defaults["reminder_at_ms"] <= defaults["due_at_ms"]
    with pytest.raises(TaskValidationError):
        rules.signal_defaults("no_such_signal", 0)


# --------------------------------------------------------------------------- #
# The engine
# --------------------------------------------------------------------------- #


def test_a_standalone_task_is_created_with_its_added_event(store):
    engine = engine_for(store)
    task = engine.create(
        due(subject="Internal review"),
        source="POST /api/wf-116/tasks",
        actor="dana",
    )
    assert task["id"].startswith("wf116_task_")
    assert task["standalone"] is True
    assert task["status"] == v.STATUS_NOT_STARTED
    assert task["reminder_at_ms"] is not None
    assert [event["trigger"] for event in task["events"]] == ["Task Added"]


def test_a_contact_attached_task_reads_the_contact_from_wf039(db):
    engine = engine_for(RecordStore(db))
    contact_id = make_contact(db)
    task = engine.create(due(contact_id=contact_id), source="POST /api/wf-116/tasks")
    assert task["attached"] is True
    assert task["contact_id"] == contact_id
    assert engine.list_contacts()[0]["label"] == "dana@northwind.example"


def test_an_unknown_contact_is_a_not_found(db):
    engine = engine_for(RecordStore(db))
    with pytest.raises(TaskNotFound) as failure:
        engine.create(due(contact_id="crm_atomic_target_missing"), source="src")
    assert failure.value.collection == "contact"


def test_a_notification_is_written_for_the_assignee(store):
    engine = engine_for(store)
    task = engine.create(
        due(assignee="dana", notify=True),
        source="POST /api/wf-116/tasks",
        actor="dana",
    )
    assert task["notifications"][0]["assignee"] == "dana"
    assert engine.notifications(task["id"])[0]["message"].startswith("Task assigned")


def test_a_signal_creates_a_task_through_the_workflow_entry_point(store):
    engine = engine_for(store)
    task = engine.create_from_signal(
        v.SIGNAL_ENGAGEMENT_SCORE_RULE_MET,
        {"contact_id": None, "assignee": "dana"},
        source="POST /api/wf-116/signals/engagement_score_rule_met",
    )
    assert task["signal"] == v.SIGNAL_ENGAGEMENT_SCORE_RULE_MET
    assert task["task_type"] == v.TYPE_EMAIL
    with pytest.raises(TaskValidationError):
        engine.create_from_signal("nope", {}, source="src")


def test_completing_fires_the_task_completed_trigger(store):
    engine = engine_for(store)
    task = engine.create(due(), source="src")
    done = engine.complete(task["id"], source="POST /api/wf-116/tasks/x/complete")
    assert done["status"] == v.STATUS_COMPLETED
    assert done["completed_at_ms"] == rules.parse_timestamp(NOW, "t")
    assert [event["trigger"] for event in done["events"]] == ["Task Added", "Task Completed"]
    with pytest.raises(TaskConflict) as failure:
        engine.complete(task["id"], source="src")
    assert failure.value.reason == "task_already_completed"


def test_a_reminder_fires_once_and_only_with_a_reminder(store):
    engine = engine_for(store)
    task = engine.create(due(), source="src")
    fired = engine.fire_reminder(task["id"], source="POST /api/wf-116/tasks/x/reminder")
    assert fired["reminder_fired_at_ms"] is not None
    assert [event["trigger"] for event in fired["events"]] == ["Task Added", "Task Reminder"]
    with pytest.raises(TaskConflict) as failure:
        engine.fire_reminder(task["id"], source="src")
    assert failure.value.reason == "reminder_already_fired"

    no_reminder = engine.create({"due_at": "2026-06-02"}, source="src")
    with pytest.raises(TaskConflict) as failure:
        engine.fire_reminder(no_reminder["id"], source="src")
    assert failure.value.reason == "reminder_not_set"


def test_a_completed_task_has_no_reminder_to_fire(store):
    engine = engine_for(store)
    task = engine.create(due(), source="src")
    engine.complete(task["id"], source="src")
    with pytest.raises(TaskConflict) as failure:
        engine.fire_reminder(task["id"], source="src")
    assert failure.value.reason == "task_completed"


def test_due_reminders_only_returns_open_unfired_past_reminders(store):
    engine = engine_for(store)
    past = engine.create(
        {"due_at": NOW.isoformat(), "reminder_at": (NOW - timedelta(hours=2)).isoformat()},
        source="src",
    )
    future = engine.create(due(), source="src")
    completed = engine.create(due(), source="src")
    engine.complete(completed["id"], source="src")

    before = rules.parse_timestamp(NOW - timedelta(hours=3), "t")
    assert engine.due_reminders(before) == []

    now_ms = rules.parse_timestamp(NOW, "t")
    ids = {task["id"] for task in engine.due_reminders(now_ms)}
    assert ids == {past["id"]}

    engine.fire_reminder(past["id"], source="src")
    assert engine.due_reminders(now_ms) == []
    assert future["id"] not in {t["id"] for t in engine.due_reminders(now_ms)}


def test_update_changes_fields_and_keeps_the_status(store):
    engine = engine_for(store)
    task = engine.create(due(priority=v.PRIORITY_LOW), source="src")
    updated = engine.update(
        task["id"],
        {"subject": "Renamed", "hs_task_priority": "HIGH", "reminder_at": None},
        source="PATCH /api/wf-116/tasks/x",
    )
    assert updated["subject"] == "Renamed"
    assert updated["priority"] == v.PRIORITY_HIGH
    assert updated["reminder_at_ms"] is None
    assert updated["status"] == v.STATUS_NOT_STARTED

    with pytest.raises(TaskValidationError):
        engine.update(
            task["id"],
            {"due_at": "2026-06-01T10:00:00Z", "reminder_at": "2026-06-01T11:00:00Z"},
            source="src",
        )


def test_attach_detach_and_pin_rewrite_the_task_row(store):
    engine = engine_for(store)
    contact_id = make_contact(store.db)
    task = engine.create(due(), source="src")
    attached = engine.attach(task["id"], contact_id, source="POST /api/wf-116/tasks/x/attach")
    assert attached["attached"] is True
    detached = engine.detach(task["id"], source="POST /api/wf-116/tasks/x/detach")
    assert detached["standalone"] is True
    pinned = engine.pin(task["id"], "eng_1", source="POST /api/wf-116/tasks/x/pin")
    assert pinned["pin_engagement_id"] == "eng_1"


def test_a_delete_is_soft_and_the_bin_can_restore(store):
    engine = engine_for(store)
    task = engine.create(due(), source="src")
    result = engine.soft_delete(task["id"], source="DELETE /api/wf-116/tasks/x")
    assert result["bin"] is True
    assert result["view"]["binned"] is True
    assert [t["id"] for t in engine.bin()] == [task["id"]]
    # A plain get hides the deleted row; the bin is the only way back.
    with pytest.raises(TaskNotFound):
        engine.get(task["id"])
    restored = engine.restore(task["id"], source="POST /api/wf-116/tasks/x/restore")
    assert restored["binned"] is False
    assert engine.bin() == []


def test_a_purge_respects_the_retention_window(store):
    engine = engine_for(store)
    task = engine.create(due(), source="src")
    engine.soft_delete(task["id"], source="src")
    binned = engine.bin()[0]
    deleted_ms = rules.parse_timestamp(binned["deleted_at"], "deleted_at")

    too_soon = engine.purge_bin(source="src", now_ms=deleted_ms + 10 * DAY_MS)
    assert too_soon["purged"] == 0
    assert engine.bin()

    late = engine.purge_bin(source="src", now_ms=deleted_ms + 31 * DAY_MS)
    assert late["purged"] == 1
    assert late["retention_days"] == 30
    assert engine.bin() == []


def test_summary_counts_every_state(store):
    engine = engine_for(store)
    contact_id = make_contact(store.db)
    engine.create(due(contact_id=contact_id), source="src")
    engine.create(due(subject="Standalone"), source="src")
    done = engine.create(due(), source="src")
    engine.complete(done["id"], source="src")
    binned = engine.create(due(), source="src")
    engine.soft_delete(binned["id"], source="src")

    counts = engine.summary()
    assert counts["tasks"] == 3
    assert counts["completed"] == 1
    assert counts["open"] == 2
    assert counts["attached"] == 1
    assert counts["standalone"] == 2
    assert counts["binned"] == 1
    assert counts["events"] == 5


def test_records_stay_schema_flexible_with_no_migration(store):
    """A team adds a field with no migration, and the index finds it."""

    record = store.create(
        v.TASK_COLLECTION,
        {"subject": "Extra field", "due_at": "2026-06-02", "urgency_band": "amber"},
        actor="dana",
        source="src",
    )
    assert store.find(v.TASK_COLLECTION, {"urgency_band": "amber"})[0]["id"] == record["id"]
    assert store.find(v.TASK_COLLECTION, {"data.urgency_band": "amber"}) == []


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


def test_the_feature_module_owns_no_connection_and_declares_its_prefix():
    import importlib

    feature = importlib.import_module(
        "dsr.features.wf116_create_an_automated_seller_follow_up_task_with_a"
    )
    text = pathlib.Path(feature.__file__).read_text(encoding="utf-8")
    assert "from dsr.api" not in text and "import dsr.api" not in text
    assert "import sqlite3" not in text
    assert feature.router.prefix == "/api/wf-116"
    assert feature.FEATURE["ticket"] == "WF-116"
    assert feature.FEATURE["id"] == "wf-116-seller-followup-task"


def test_the_seed_returns_a_cp1252_safe_string(db):
    import importlib

    feature = importlib.import_module(
        "dsr.features.wf116_create_an_automated_seller_follow_up_task_with_a"
    )
    room = db.create("room", {"name": "Northwind"}, actor="seed", source="seed")
    message = feature.seed(db, {"room_ids": [(room["id"], "Northwind")], "now": NOW, "rng": None})
    assert message.encode("cp1252")
    assert "WF-116" in message
    assert RecordStore(db).find(v.TASK_COLLECTION, {"status": v.STATUS_COMPLETED})
