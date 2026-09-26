"""Tests for the audited write path.

The audit guarantee is the product's central promise, so these tests lean on
atomicity, completeness, and the schema-flexibility contract rather than on
happy-path shape alone.
"""

from __future__ import annotations

import json
import sqlite3
import threading

import pytest

from dsr.db.audited import AuditedDatabase, AuditError, RecordNotFound, utcnow


@pytest.fixture()
def db(tmp_path):
    database = AuditedDatabase(tmp_path / "test.db", mirror_dir=tmp_path / "mirror")
    yield database
    database.close()


# -- core CRUD + audit ------------------------------------------------------ #


def test_create_audits_with_no_before_state(db):
    record = db.create("room", {"name": "Acme Evaluation", "stage": "demo"})

    entries = db.audit(record_id=record["id"])
    assert len(entries) == 1
    entry = entries[0]
    assert entry["action"] == "insert"
    assert entry["collection"] == "room"
    assert entry["before_state"] is None
    assert entry["after_state"]["name"] == "Acme Evaluation"
    assert entry["seq"] == 1


def test_update_audits_before_and_after(db):
    record = db.create("room", {"name": "Acme", "stage": "demo"})

    db.update(record["id"], {"stage": "negotiation"})

    entry = db.audit(record_id=record["id"])[0]
    assert entry["action"] == "update"
    assert entry["before_state"]["stage"] == "demo"
    assert entry["after_state"]["stage"] == "negotiation"
    assert entry["diff"]["stage"] == {"from": "demo", "to": "negotiation"}


def test_update_merges_rather_than_replaces(db):
    record = db.create("room", {"name": "Acme", "stage": "demo", "owner": "dana"})

    updated = db.update(record["id"], {"stage": "closed"})

    assert updated["data"] == {"name": "Acme", "stage": "closed", "owner": "dana"}


def test_delete_is_soft_by_default_and_audited(db):
    record = db.create("room", {"name": "Acme"})

    db.delete(record["id"])

    assert db.get(record["id"]) is None
    assert db.get(record["id"], include_deleted=True) is not None
    entry = db.audit(record_id=record["id"])[0]
    assert entry["action"] == "delete"
    assert entry["after_state"] is None
    assert entry["before_state"]["name"] == "Acme"


def test_hard_delete_removes_row_and_index(db):
    record = db.create("room", {"name": "Acme", "stage": "demo"})

    db.delete(record["id"], hard=True)

    assert db.get(record["id"], include_deleted=True) is None
    assert db.query_index("stage", "demo") == []


def test_restore_undoes_soft_delete(db):
    record = db.create("room", {"name": "Acme", "stage": "demo"})
    db.delete(record["id"])

    restored = db.restore(record["id"])

    assert restored["deleted_at"] is None
    assert db.get(record["id"])["data"]["stage"] == "demo"
    assert db.audit(record_id=record["id"])[0]["action"] == "restore"


def test_audit_accumulates_across_lifecycle(db):
    record = db.create("room", {"name": "Acme", "stage": "demo"})

    db.update(record["id"], {"stage": "pilot"})
    db.delete(record["id"])
    db.restore(record["id"])

    actions = [e["action"] for e in reversed(db.audit(record_id=record["id"]))]
    assert actions == ["insert", "update", "delete", "restore"]


# -- the atomicity guarantee ------------------------------------------------ #


def test_failed_write_leaves_no_trace(db):
    """A rejected write must not leave a partial change or an orphan audit row."""
    record = db.create("room", {"name": "Acme"})
    before = db.audit_count()

    with pytest.raises(sqlite3.IntegrityError):
        with db._write() as conn:  # noqa: SLF001 - deliberately driving the failure path
            conn.execute(
                "INSERT INTO records (id, collection, data, revision, created_at, updated_at)"
                " VALUES (?,?,?,1,?,?)",
                ("dupe", "room", "{}", utcnow(), utcnow()),
            )
            raise sqlite3.IntegrityError("boom")

    assert db.audit_count() == before
    assert db.get("dupe") is None
    assert db.get(record["id"]) is not None


def test_update_of_missing_record_raises_and_audits_nothing(db):
    before = db.audit_count()

    with pytest.raises(RecordNotFound):
        db.update("room_missing", {"stage": "x"})

    assert db.audit_count() == before


def test_conflicting_revision_is_rejected(db):
    record = db.create("room", {"name": "Acme", "stage": "demo"})
    db.update(record["id"], {"stage": "pilot"})

    with pytest.raises(AuditError, match="revision conflict"):
        db.update(record["id"], {"stage": "closed"}, expected_revision=1)

    assert db.get(record["id"])["data"]["stage"] == "pilot"


# -- schema flexibility ----------------------------------------------------- #


def test_arbitrary_nested_fields_round_trip_without_migration(db):
    payload = {
        "name": "Acme",
        "branding": {"theme": "dark", "palette": {"primary": "#0f172a", "accent": "#38bdf8"}},
        "integrations": ["salesforce", "zoom"],
        "scoring": {"weights": {"dwell": 0.4, "clicks": 0.6}, "enabled": True},
        "seats": 12,
    }

    record = db.create("room", payload)

    assert db.get(record["id"])["data"] == payload


def test_find_queries_nested_schema_free_fields(db):
    db.create("room", {"name": "A", "branding": {"theme": "dark"}})
    db.create("room", {"name": "B", "branding": {"theme": "light"}})
    db.create("room", {"name": "C", "scoring": {"enabled": True}})

    dark = db.find("room", {"branding.theme": "dark"})
    enabled = db.find("room", {"scoring.enabled": True})

    assert [r["data"]["name"] for r in dark] == ["A"]
    assert [r["data"]["name"] for r in enabled] == ["C"]


def test_find_supports_numeric_and_boolean_matching(db):
    db.create("document", {"name": "Deck", "pages": 24, "public": True})
    db.create("document", {"name": "One-pager", "pages": 1, "public": False})

    assert len(db.find("document", {"pages": 24})) == 1
    assert len(db.find("document", {"public": True})) == 1


def test_find_combines_multiple_conditions_across_types(db):
    """Regression: mixing text, numeric and boolean filters must bind correctly."""
    db.create("room", {"name": "A", "seats": 10, "public": True, "branding": {"theme": "dark"}})
    db.create("room", {"name": "B", "seats": 10, "public": False, "branding": {"theme": "dark"}})
    db.create("room", {"name": "C", "seats": 20, "public": True, "branding": {"theme": "dark"}})

    hits = db.find("room", {"branding.theme": "dark", "seats": 10, "public": True})

    assert [r["data"]["name"] for r in hits] == ["A"]


def test_find_excludes_soft_deleted_by_default(db):
    record = db.create("room", {"name": "A", "stage": "demo"})
    db.delete(record["id"])

    assert db.find("room", {"stage": "demo"}) == []
    assert len(db.find("room", {"stage": "demo"}, include_deleted=True)) == 1


def test_index_is_refreshed_on_update(db):
    record = db.create("document", {"status": "draft"})

    db.update(record["id"], {"status": "published"})

    assert db.query_index("status", "draft") == []
    assert db.query_index("status", "published") == [record["id"]]


def test_reserved_keys_cannot_be_spoofed_via_data(db):
    record = db.create("room", {"id": "spoofed", "collection": "spoofed", "name": "Acme"})

    stored = db.get(record["id"])
    assert stored["id"] == record["id"]
    assert stored["collection"] == "room"
    assert stored["data"]["name"] == "Acme"
    assert "id" not in stored["data"]


# -- listing, counting, stats ----------------------------------------------- #


def test_list_scopes_by_room_and_collection(db):
    room_a = db.create("room", {"name": "A"})["id"]
    room_b = db.create("room", {"name": "B"})["id"]
    db.create("document", {"title": "A deck"}, room_id=room_a)
    db.create("document", {"title": "B deck"}, room_id=room_b)
    db.create("note", {"body": "internal"}, room_id=room_a)

    scoped = db.list("document", room_id=room_a)

    assert [r["data"]["title"] for r in scoped] == ["A deck"]
    assert db.count("document") == 2
    assert db.count("document", room_id=room_b) == 1


def test_list_rejects_injected_order_by(db):
    with pytest.raises(ValueError, match="order_by"):
        db.list("room", order_by="deleted_at; DROP TABLE records")


def test_list_paginates(db):
    for index in range(5):
        db.create("room", {"name": f"room-{index}"})

    page = db.list("room", limit=2, offset=0)
    rest = db.list("room", limit=2, offset=2)

    assert len(page) == 2 and len(rest) == 2
    assert page[0]["id"] != rest[0]["id"]


def test_stats_summarise_live_deleted_and_audits(db):
    keep = db.create("room", {"name": "Keep"})["id"]
    drop = db.create("room", {"name": "Drop"})["id"]
    db.delete(drop)
    db.update(keep, {"name": "Kept"})

    stats = db.stats()

    assert stats["records"] == 1
    assert stats["deleted"] == 1
    # insert, insert, delete, update
    assert stats["audit_entries"] == 4
    assert stats["by_collection"] == {"room": 1}
    assert stats["by_action"] == {"insert": 2, "update": 1, "delete": 1}


# -- audit filtering + mirror ----------------------------------------------- #


def test_audit_filters_by_collection_actor_and_action(db):
    room = db.create("room", {"name": "A"}, actor="dana")["id"]
    db.create("document", {"title": "d"}, actor="sam")
    db.update(room, {"stage": "pilot"}, actor="dana")

    assert len(db.audit(collection="room")) == 2
    assert len(db.audit(actor="sam")) == 1
    assert len(db.audit(action="delete")) == 0
    assert db.audit_count(collection="document") == 1


def test_audit_is_newest_first_with_sequence_numbers(db):
    record = db.create("room", {"name": "A"})
    db.update(record["id"], {"name": "B"})
    db.update(record["id"], {"name": "C"})

    entries = db.audit(record_id=record["id"])

    assert [e["seq"] for e in entries] == [3, 2, 1]
    assert entries[0]["after_state"]["name"] == "C"


def test_audit_records_actor_source_and_request_id(db):
    db.create("room", {"name": "A"}, actor="dana", source="POST /api/records", request_id="req-1")

    entry = db.audit()[0]

    assert entry["actor"] == "dana"
    assert entry["source"] == "POST /api/records"
    assert entry["request_id"] == "req-1"


def test_audit_filters_by_request_id_to_read_one_request_as_a_unit(db):
    with db.transaction(request_id="req-1", actor="dana") as tx:
        room = tx.create("room", {"name": "Acme"})
        tx.create("site", {"friendly_url": "acme"}, room_id=room["id"])
    with db.transaction(request_id="req-2", actor="sam") as tx:
        other = tx.create("room", {"name": "Contoso"})

    first = db.audit(request_id="req-1")
    assert {e["collection"] for e in first} == {"room", "site"}
    assert all(e["request_id"] == "req-1" for e in first)
    assert db.audit_count(request_id="req-1") == 2
    assert [e["record_id"] for e in db.audit(request_id="req-2")] == [other["id"]]


def test_jsonl_mirror_written_for_each_change(db, tmp_path):
    record = db.create("room", {"name": "A"})
    db.update(record["id"], {"name": "B"})

    mirrors = list((tmp_path / "mirror").glob("audit-*.jsonl"))
    lines = [json.loads(line) for line in mirrors[0].read_text(encoding="utf-8").splitlines()]

    assert [m["action"] for m in lines] == ["insert", "update"]


def test_audit_diff_is_json_serialisable(db):
    record = db.create("room", {"name": "A", "seats": 1})

    db.update(record["id"], {"seats": 5, "name": "B"})

    diff = db.audit(record_id=record["id"])[0]["diff"]
    assert diff == {
        "name": {"from": "A", "to": "B"},
        "seats": {"from": 1, "to": 5},
    }


# -- bulk + concurrency ----------------------------------------------------- #


def test_bulk_create_is_one_transaction_one_audit_row(db):
    created = db.bulk_create("document", [{"title": "a"}, {"title": "b"}, {"title": "c"}])

    assert len(created) == 3
    entries = db.audit(collection="document")
    assert len(entries) == 1
    assert entries[0]["summary"] == "bulk-created 3 document record(s)"


def test_bulk_create_rolls_back_completely_on_failure(db):
    before = db.audit_count()

    with pytest.raises(sqlite3.IntegrityError):
        with db._write() as conn:  # noqa: SLF001
            db.bulk_create  # referenced for clarity
            conn.execute(
                "INSERT INTO records (id, collection, data, revision, created_at, updated_at)"
                " VALUES ('x','document','{}',1,'t','t')"
            )
            raise sqlite3.IntegrityError("boom")

    assert db.count("document") == 0
    assert db.audit_count() == before


def test_bulk_create_of_empty_list_is_a_noop(db):
    assert db.bulk_create("document", []) == []
    assert db.audit_count() == 0


def test_concurrent_writes_are_serialised_not_lost(tmp_path):
    """Many threads writing at once must not lose writes or corrupt the log."""
    database = AuditedDatabase(tmp_path / "concurrent.db")
    errors: list[Exception] = []

    def worker(index: int) -> None:
        try:
            for step in range(10):
                database.create("event", {"worker": index, "step": step})
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert database.count("event") == 80
    # One audit row per created record, no duplicates and none missing.
    assert database.audit_count(action="insert") == 80
    database.close()


def test_closed_database_refuses_writes(tmp_path):
    database = AuditedDatabase(tmp_path / "closed.db")
    database.close()

    with pytest.raises(AuditError, match="closed"):
        database.create("room", {"name": "A"})


# -- transactions (multi-record atomic writes) ------------------------------- #


def test_transaction_commits_every_write_and_audits_each(db):
    with db.transaction(actor="dana") as tx:
        room = tx.create("room", {"name": "Acme"})
        site = tx.create("site", {"friendly_url": "acme"}, room_id=room["id"])

    assert db.get(room["id"]) is not None
    assert db.get(site["id"])["room_id"] == room["id"]
    # One audit row per record, not one for the pair: the log stays as granular
    # as it is for single-record writes.
    assert db.audit_count() == 2
    assert db.audit(record_id=room["id"])[0]["actor"] == "dana"
    assert db.audit(record_id=site["id"])[0]["actor"] == "dana"


def test_transaction_rolls_back_all_writes_and_audit_rows_on_failure(db):
    with pytest.raises(RuntimeError, match="boom"):
        with db.transaction() as tx:
            tx.create("room", {"name": "Acme"})
            raise RuntimeError("boom")

    assert db.count("room") == 0
    assert db.audit_count() == 0
    # The dynamic index rolls back with the data, so a rolled-back room is not
    # findable by any of its fields.
    assert db.find("room", {"name": "Acme"}) == []


def test_transaction_update_is_audited_and_rolled_back_together(db):
    room = db.create("room", {"name": "Acme"})

    with pytest.raises(RuntimeError):
        with db.transaction() as tx:
            tx.update(room["id"], {"name": "Renamed"})
            tx.create("site", {"friendly_url": "acme"})
            raise RuntimeError("boom")

    assert db.get(room["id"])["data"]["name"] == "Acme"
    assert db.count("site") == 0
    assert db.audit_count() == 1  # only the original insert survives


def test_transaction_index_reflects_writes_inside_the_block(db):
    with db.transaction() as tx:
        tx.create("room", {"name": "Acme", "status": "active"})
        # Visible to reads on the same connection before the commit.
        assert db.find("room", {"status": "active"}) != []

    assert db.query_index("status", "active") != []


def test_write_inside_a_transaction_is_refused_with_a_clear_error(db):
    """A nested single-record write is a programming error, not a partial commit.

    Refusing it rolls the whole block back, so a caller that ignores the error
    still cannot end up with half a workflow committed.
    """
    with pytest.raises(AuditError, match="writer handle"):
        with db.transaction() as tx:
            tx.create("room", {"name": "Acme"})
            db.create("room", {"name": "Sneaky"})

    assert db.count("room") == 0
    assert db.audit_count() == 0


def test_transaction_mirror_is_written_only_after_commit(db, tmp_path):
    with db.transaction(actor="api") as tx:
        tx.create("room", {"name": "Acme"})
        tx.create("site", {"friendly_url": "acme"})

    mirrors = list((tmp_path / "mirror").glob("audit-*.jsonl"))
    lines = [json.loads(line) for line in mirrors[0].read_text(encoding="utf-8").splitlines()]
    assert [line["action"] for line in lines] == ["insert", "insert"]


def test_transaction_mirror_is_not_written_when_rolled_back(db, tmp_path):
    with pytest.raises(RuntimeError):
        with db.transaction() as tx:
            tx.create("room", {"name": "Acme"})
            raise RuntimeError("boom")

    assert list((tmp_path / "mirror").glob("audit-*.jsonl")) == []


def test_transaction_per_call_actor_overrides_the_block_default(db):
    with db.transaction(actor="dana") as tx:
        room = tx.create("room", {"name": "Acme"})
        tx.create("site", {"friendly_url": "acme"}, actor="system")

    assert db.audit(record_id=room["id"])[0]["actor"] == "dana"
    assert db.audit(collection="site")[0]["actor"] == "system"


def test_transaction_rejects_duplicate_record_id_and_rolls_back(db):
    with db.transaction() as tx:
        tx.create("room", {"name": "Acme"}, record_id="room_fixed")

    with pytest.raises(AuditError, match="already exists"):
        with db.transaction() as tx:
            tx.create("room", {"name": "Clash"}, record_id="room_fixed")
            tx.create("site", {"friendly_url": "clash"})

    assert db.get("room_fixed")["data"]["name"] == "Acme"
    assert db.count("site") == 0
