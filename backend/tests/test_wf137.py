"""Tests for WF-137's domain rules and its book: versioning, brand, continuity, retention.

The rules are pure functions of their arguments and the book is a thin layer over
the audited store, so everything here runs without a server. The clock is frozen
on a fixed instant - never "now" - so no test depends on the day it runs.

Every test builds its own store through the shared fixtures in ``conftest.py``,
so the file passes alone and under ``pytest-xdist`` in any order.
"""

from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from dsr.competitive_baseline import governance as rules
from dsr.competitive_baseline.governance import (
    ARCHIVE_AFTER_DAYS,
    DELETE_AFTER_DAYS,
    LIFECYCLE_ARCHIVED,
    LIFECYCLE_ONBOARDED,
    LIFECYCLE_ONBOARDING,
    PHASE_ACTIVE,
    PHASE_COMPLETE,
    TRIGGER_BRANCH,
    TRIGGER_PUBLISH,
    TRIGGER_RESTORE,
    TRIGGER_SESSION,
    GovernanceBook,
    GovernanceError,
    GovernanceNotFound,
    GovernanceRefused,
    check_brand,
    diff_states,
    next_version,
    normalise_brand_set,
    phase_progress,
    retention_due,
)
from dsr.store import RecordStore

#: A fixed clock. Every date in this file is derived from it, so the result is
#: the same on a Monday and on a Sunday.
NOW = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def clock() -> dict[str, datetime]:
    return {"now": NOW}


@pytest.fixture
def book(store: RecordStore, clock: dict[str, datetime]) -> GovernanceBook:
    """The book over a fresh empty database, with the clock injectable per test."""
    return GovernanceBook(store, now=lambda: clock["now"])


def make_room(store: RecordStore, name: str = "Northwind", **extra) -> dict:
    return store.create("room", {"name": name, **extra}, source="test")


def publish(book: GovernanceBook, room_id: str, label: str, content: dict) -> dict:
    return book.publish(room_id, {"label": label, "content": content}, actor="dana", source="test")


# --------------------------------------------------------------------------- #
# The pure rules
# --------------------------------------------------------------------------- #


def test_next_version_is_one_for_an_empty_history():
    assert next_version([]) == 1


def test_next_version_is_the_highest_plus_one():
    rows = [{"data": {"version": 1}}, {"data": {"version": 2}}, {"data": {"version": 3}}]
    assert next_version(rows) == 4


def test_next_version_does_not_restart_after_a_gap():
    """A soft-deleted version must not hand a new snapshot a published number."""
    assert next_version([{"data": {"version": 7}}]) == 8


def test_check_brand_reports_an_off_brand_theme_and_accent():
    findings = check_brand({"theme": "dark", "accent": "#22c55e"}, normalise_brand_set(None))
    codes = {finding["code"] for finding in findings}
    assert "unapproved_theme" in codes
    assert "unapproved_value" in codes


def test_check_brand_reports_a_missing_required_token():
    findings = check_brand({"theme": "light"}, normalise_brand_set(None))
    tokens = {finding["token"] for finding in findings}
    assert "accent" in tokens


def test_a_room_on_the_brand_set_has_no_findings():
    approved = {
        name: rules.merge_branding(normalise_brand_set(None))[name] for name in rules.BRAND_TOKENS
    }
    assert check_brand(approved, normalise_brand_set(None)) == []


def test_normalise_brand_set_fills_an_unnamed_token_from_the_house_value():
    brand_set = normalise_brand_set({"accent": "#10506f"})
    assert brand_set["tokens"]["theme"] == rules.HOUSE_THEME
    assert brand_set["tokens"]["accent"] == "#10506f"


def test_normalise_brand_set_refuses_an_unknown_required_token():
    with pytest.raises(GovernanceError):
        normalise_brand_set({"required_tokens": ["theme", "not_a_token"]})


def test_diff_states_reports_only_a_field_that_differs():
    changes = diff_states(
        {"label": "same", "content": {"a": 1}}, {"label": "same", "content": {"a": 2}}
    )
    assert [row["field"] for row in changes] == ["content"]


def test_retention_due_orders_archive_before_delete():
    policy = {
        "state": rules.RETENTION_LIVE,
        "lifecycle_ended_at": (NOW - timedelta(days=ARCHIVE_AFTER_DAYS + 1)).isoformat(),
    }
    due = retention_due(policy, None, now=NOW)
    assert due["archive_due"] is True
    assert due["delete_due"] is False


def test_phase_progress_counts_phases_and_milestones():
    phases = [
        {
            "data": {
                "state": PHASE_COMPLETE,
                "milestones": [
                    {"state": rules.MILESTONE_COMPLETE},
                    {"state": rules.MILESTONE_COMPLETE},
                ],
            }
        },
        {"data": {"state": PHASE_ACTIVE, "milestones": [{"state": rules.MILESTONE_PENDING}]}},
    ]
    progress = phase_progress(phases)
    assert progress["phases"] == 2
    assert progress["phases_complete"] == 1
    assert progress["milestones_complete"] == 2


def test_the_vocabulary_names_the_triggers_the_rules_record():
    body = rules.vocabulary()
    assert body["ticket"] == "WF-137"
    assert set(body["snapshot_triggers"]) == {
        TRIGGER_PUBLISH,
        TRIGGER_SESSION,
        TRIGGER_RESTORE,
        TRIGGER_BRANCH,
    }


def test_the_inferences_route_states_each_decision_with_how_to_change_it():
    body = rules.inferences()
    assert body["count"] >= 5
    for entry in body["inferences"]:
        assert entry["decision"] and entry["why"] and entry["change_it_by"]


# --------------------------------------------------------------------------- #
# Brand governance, through the book
# --------------------------------------------------------------------------- #


def test_brand_state_reads_the_cores_branding_before_an_apply(book, store):
    room = make_room(store, branding={"theme": "dark", "accent": "#22c55e"})
    state = book.brand_state(room["id"])
    assert state["branding_source"] == "room"
    assert state["compliant"] is False


def test_apply_brand_writes_the_approved_tokens(book, store):
    room = make_room(store, branding={"theme": "dark", "accent": "#22c55e"})
    state = book.apply_brand(room["id"], actor="enablement", source="test")
    assert state["compliant"] is True
    assert state["branding"]["theme"] == rules.HOUSE_THEME
    assert state["branding"]["accent"] == rules.HOUSE_ACCENT


def test_a_second_apply_of_an_unchanged_set_writes_nothing(book, store):
    room = make_room(store, branding={"theme": "dark"})
    book.apply_brand(room["id"], actor="e", source="test")
    before = len(store.audit(collection=rules.ROOM_STATE_COLLECTION, limit=1000))
    book.apply_brand(room["id"], actor="e", source="test")
    assert len(store.audit(collection=rules.ROOM_STATE_COLLECTION, limit=1000)) == before


def test_a_room_applied_against_an_old_set_reports_as_stale(book, store):
    room = make_room(store)
    book.write_brand_set({"accent": "#10506f"}, actor="e", source="test")
    book.apply_brand(room["id"], actor="e", source="test")
    assert book.brand_state(room["id"])["stale"] is False
    # The brand set moves on. The room still carries version one and is stale.
    book.write_brand_set({"accent": "#10506f", "logo": "second.svg"}, actor="e", source="test")
    assert book.brand_state(room["id"])["stale"] is True


def test_the_brand_board_reports_each_governed_room(book, store):
    room = make_room(store, branding={"theme": "dark"})
    book.apply_brand(room["id"], actor="e", source="test")
    board = book.brand_board()
    assert board["count"] == 1
    assert board["rooms"][0]["room_id"] == room["id"]


# --------------------------------------------------------------------------- #
# Versioning
# --------------------------------------------------------------------------- #


def test_a_publish_is_version_one_and_the_head(book, store):
    room = make_room(store)
    snapshot = publish(book, room["id"], "first", {"a": 1})
    assert snapshot["version"] == 1
    assert snapshot["trigger"] == TRIGGER_PUBLISH
    assert book.history(room["id"])["head_version"] == 1


def test_an_editing_session_is_its_own_trigger(book, store):
    room = make_room(store)
    publish(book, room["id"], "first", {"a": 1})
    checkpoint = book.checkpoint(room["id"], {"label": "session"}, actor="dana", source="test")
    assert checkpoint["version"] == 2
    assert checkpoint["trigger"] == TRIGGER_SESSION


def test_each_publish_is_a_new_immutable_version(book, store):
    room = make_room(store)
    publish(book, room["id"], "first", {"a": 1})
    publish(book, room["id"], "second", {"a": 2})
    history = book.history(room["id"])
    assert [row["version"] for row in history["versions"]] == [1, 2]
    assert history["versions"][0]["content"] == {"a": 1}


def test_history_reads_oldest_version_first(book, store):
    room = make_room(store)
    publish(book, room["id"], "two", {"a": 2})
    publish(book, room["id"], "three", {"a": 3})
    versions = [row["version"] for row in book.history(room["id"])["versions"]]
    assert versions == [1, 2]


def test_a_preview_reports_what_a_restore_would_change(book, store):
    room = make_room(store)
    publish(book, room["id"], "first", {"a": 1})
    publish(book, room["id"], "second", {"a": 2})
    preview = book.preview(room["id"], 1)
    fields = [row["field"] for row in preview["changes"]]
    assert "content" in fields
    assert preview["matches_head"] is False


def test_a_preview_of_the_head_reports_no_change(book, store):
    room = make_room(store)
    publish(book, room["id"], "first", {"a": 1})
    assert book.preview(room["id"], 1)["matches_head"] is True


def test_a_restore_writes_the_next_version_and_never_edits_one(book, store):
    room = make_room(store)
    publish(book, room["id"], "first", {"a": 1})
    publish(book, room["id"], "second", {"a": 2})
    restored = book.restore(room["id"], 1, actor="dana", source="test")
    assert restored["version"] == 3
    assert restored["trigger"] == TRIGGER_RESTORE
    assert restored["restored_from"] == 1
    history = book.history(room["id"])
    assert [row["version"] for row in history["versions"]] == [1, 2, 3]
    # The old rows are untouched: version one still carries its own content.
    assert history["versions"][0]["content"] == {"a": 1}


def test_a_branch_starts_a_new_room_from_an_earlier_version(book, store):
    room = make_room(store)
    publish(book, room["id"], "first", {"a": 1})
    publish(book, room["id"], "second", {"a": 2})
    branch = book.branch(room["id"], 1, actor="dana", source="test")
    assert branch["room_id"] != room["id"]
    assert branch["snapshot"]["trigger"] == TRIGGER_BRANCH
    assert branch["snapshot"]["branched_from_room"] == room["id"]
    assert book.history(branch["room_id"])["count"] == 1
    assert store.get(branch["room_id"])["collection"] == "room"


def test_an_unknown_version_is_not_found(book, store):
    room = make_room(store)
    publish(book, room["id"], "first", {"a": 1})
    with pytest.raises(GovernanceNotFound):
        book.preview(room["id"], 99)


def test_a_snapshot_of_an_unknown_room_is_not_found(book):
    with pytest.raises(GovernanceNotFound):
        book.publish("room_absent", {"label": "x"}, actor="d", source="test")


# --------------------------------------------------------------------------- #
# Closed-won continuity
# --------------------------------------------------------------------------- #


def test_repurpose_keeps_the_room_and_instantiates_the_phases(book, store):
    room = make_room(store)
    state = book.repurpose(room["id"], {"closed_at": NOW.isoformat()}, actor="dana", source="test")
    assert state["lifecycle_state"] == LIFECYCLE_ONBOARDING
    assert state["room_id"] == room["id"]
    assert len(state["phases"]) == len(rules.DEFAULT_PLAN["phases"])
    assert state["phases"][0]["state"] == PHASE_ACTIVE


def test_a_room_is_repurposed_once(book, store):
    room = make_room(store)
    book.repurpose(room["id"], actor="dana", source="test")
    with pytest.raises(GovernanceRefused) as excinfo:
        book.repurpose(room["id"], actor="dana", source="test")
    assert excinfo.value.reason == GovernanceRefused.REASON_ALREADY_REPURPOSED


def test_advancing_a_sales_room_is_refused(book, store):
    room = make_room(store)
    with pytest.raises(GovernanceRefused) as excinfo:
        book.advance_phase(room["id"], actor="dana", source="test")
    assert excinfo.value.reason == GovernanceRefused.REASON_NOT_REPURPOSED


def test_advancing_with_an_open_milestone_is_refused(book, store):
    room = make_room(store)
    book.repurpose(room["id"], actor="dana", source="test")
    with pytest.raises(GovernanceRefused) as excinfo:
        book.advance_phase(room["id"], actor="dana", source="test")
    assert excinfo.value.reason == GovernanceRefused.REASON_MILESTONES_OPEN


def test_closing_the_milestones_advances_the_phase(book, store):
    room = make_room(store)
    book.repurpose(room["id"], actor="dana", source="test")
    book.complete_milestone(room["id"], "kickoff", "introductions", actor="dana", source="test")
    book.complete_milestone(room["id"], "kickoff", "success_criteria", actor="dana", source="test")
    state = book.advance_phase(room["id"], actor="dana", source="test")
    assert state["active_phase"]["phase_key"] == "implementation"
    assert state["progress"]["phases_complete"] == 1


def test_the_last_phase_completes_the_onboarding(book, store):
    room = make_room(store)
    plan = {
        "key": "tiny",
        "name": "Tiny plan",
        "phases": [{"key": "only", "name": "Only", "milestones": [{"key": "m", "name": "M"}]}],
    }
    book.repurpose(room["id"], plan, actor="dana", source="test")
    book.complete_milestone(room["id"], "only", "m", actor="dana", source="test")
    state = book.advance_phase(room["id"], actor="dana", source="test")
    assert state["lifecycle_state"] == LIFECYCLE_ONBOARDED


def test_an_unknown_milestone_is_not_found(book, store):
    room = make_room(store)
    book.repurpose(room["id"], actor="dana", source="test")
    with pytest.raises(GovernanceNotFound):
        book.complete_milestone(room["id"], "kickoff", "ghost", actor="dana", source="test")


# --------------------------------------------------------------------------- #
# Retention
# --------------------------------------------------------------------------- #


def test_a_retention_window_makes_a_room_archive_due(book, store, clock):
    room = make_room(store)
    book.write_retention(
        room["id"],
        {"archive_after_days": 30, "lifecycle_ended_at": (NOW - timedelta(days=60)).isoformat()},
        actor="cs",
        source="test",
    )
    assert book.retention_state(room["id"])["archive_due"] is True


def test_apply_retention_archives_a_due_room(book, store):
    room = make_room(store)
    book.write_retention(
        room["id"],
        {"archive_after_days": 30, "lifecycle_ended_at": (NOW - timedelta(days=60)).isoformat()},
        actor="cs",
        source="test",
    )
    result = book.apply_retention(room["id"], actor="cs", source="test")
    assert result["applied"] == "archived"
    assert book.room_state(room["id"])["lifecycle_state"] == LIFECYCLE_ARCHIVED


def test_a_not_due_retention_is_refused(book, store):
    room = make_room(store)
    book.write_retention(
        room["id"],
        {"archive_after_days": 365, "lifecycle_ended_at": NOW.isoformat()},
        actor="cs",
        source="test",
    )
    with pytest.raises(GovernanceRefused) as excinfo:
        book.apply_retention(room["id"], actor="cs", source="test")
    assert excinfo.value.reason == GovernanceRefused.REASON_RETENTION_STATE


def test_a_delete_soft_deletes_the_governance_rows(book, store, clock):
    room = make_room(store)
    publish(book, room["id"], "first", {"a": 1})
    book.write_retention(
        room["id"],
        {
            "archive_after_days": 1,
            "delete_after_days": 1,
            "lifecycle_ended_at": NOW.isoformat(),
        },
        actor="cs",
        source="test",
    )
    clock["now"] = NOW + timedelta(days=2)
    book.apply_retention(room["id"], actor="cs", source="test")
    clock["now"] = NOW + timedelta(days=5)
    result = book.apply_retention(room["id"], actor="cs", source="test")
    assert result["applied"] == "deleted"
    assert result["deleted_rows"] >= 1
    # The snapshot is gone from the live reads but still auditable.
    assert book.history(room["id"])["count"] == 0
    assert len(store.audit(collection=rules.SNAPSHOT_COLLECTION, limit=1000)) >= 1


def test_an_archived_room_refuses_a_new_snapshot(book, store):
    room = make_room(store)
    publish(book, room["id"], "first", {"a": 1})
    book.write_retention(
        room["id"],
        {"archive_after_days": 1, "lifecycle_ended_at": (NOW - timedelta(days=60)).isoformat()},
        actor="cs",
        source="test",
    )
    book.apply_retention(room["id"], actor="cs", source="test")
    with pytest.raises(GovernanceRefused) as excinfo:
        publish(book, room["id"], "after archive", {"a": 2})
    assert excinfo.value.reason == GovernanceRefused.REASON_LIFECYCLE_CLOSED


def test_the_default_windows_are_the_recorded_house_rules():
    assert ARCHIVE_AFTER_DAYS == 180
    assert DELETE_AFTER_DAYS == 365


# --------------------------------------------------------------------------- #
# The summary and the audit rule
# --------------------------------------------------------------------------- #


def test_the_summary_counts_the_governance_surface(book, store):
    room = make_room(store)
    publish(book, room["id"], "first", {"a": 1})
    book.repurpose(room["id"], actor="dana", source="test")
    summary = book.summary()
    assert summary["rooms_tracked"] == 1
    assert summary["snapshots"] == 1
    assert summary["lifecycle"][LIFECYCLE_ONBOARDING] == 1


def test_every_write_names_the_source_it_was_given(book, store):
    room = make_room(store)
    book.publish(
        room["id"],
        {"label": "x"},
        actor="dana",
        source="POST /api/wf-137/rooms/{room_id}/snapshots",
    )
    entries = store.audit(collection=rules.SNAPSHOT_COLLECTION)
    assert entries
    assert all(entry["source"] == "POST /api/wf-137/rooms/{room_id}/snapshots" for entry in entries)


# --------------------------------------------------------------------------- #
# The architectural guards the contract names
# --------------------------------------------------------------------------- #


def test_the_domain_module_imports_nothing_but_the_store():
    """The contract's rule, read off the package on disk rather than off a claim."""
    package = Path(inspect.getfile(GovernanceBook)).parent
    for module in sorted(package.glob("*.py")):
        text = module.read_text(encoding="utf-8")
        assert "import sqlite3" not in text, module.name
        assert "sqlite3.connect" not in text, module.name
        assert "from dsr.api" not in text, module.name
        assert "dsr.deps" not in text, module.name
        assert "fastapi" not in text, module.name
        assert "AuditedDatabase(" not in text, module.name


def test_the_domain_module_imports_only_the_store_from_this_project():
    text = Path(inspect.getfile(GovernanceBook)).read_text(encoding="utf-8")
    project_imports = {
        line.strip()
        for line in text.splitlines()
        if line.strip().startswith(("from dsr", "import dsr"))
    }
    assert project_imports == {"from dsr.store import RecordStore"}


def test_the_feature_module_imports_no_app_and_opens_no_connection():
    from dsr.features import wf137_governance_versioning_brand_control_and_the_closed_won as feature

    text = Path(feature.__file__).read_text(encoding="utf-8")
    assert "from dsr.api" not in text and "import dsr.api" not in text
    assert "import sqlite3" not in text
    assert "sqlite3.connect" not in text


def test_the_feature_declares_the_prefix_the_brief_names():
    from dsr.features import wf137_governance_versioning_brand_control_and_the_closed_won as feature

    assert feature.router.prefix == "/api/wf-137"
    assert feature.FEATURE["ticket"] == "WF-137"


def test_the_seed_returns_a_cp1252_encodable_summary(store):
    """A RIGHTWARDS ARROW in one seed string once broke the whole seeder."""
    from dsr.features import wf137_governance_versioning_brand_control_and_the_closed_won as feature

    rooms = [(make_room(store, f"Room {index}")["id"], f"Room {index}") for index in range(4)]
    summary = feature.seed(store.db, {"room_ids": rooms, "now": NOW, "rng": None})
    assert isinstance(summary, str) and summary
    summary.encode("cp1252")  # raises if any character is not encodable
    assert "refused" in summary
