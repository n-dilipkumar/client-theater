"""Pin the ordering guarantees, because both were broken and neither was visible.

Two defects, found by a test that passed locally and failed on CI with the same
data on the same commit:

  1. `AuditedDatabase.list()` and `.find()` did `ORDER BY updated_at DESC` with
     no tie-break. `updated_at` is second-granularity, so rows written in the
     same second TIE, and the order among tied rows is whatever SQLite's query
     plan produces. Locally the plan returned insertion order; on the runner it
     did not. Same commit, same data, different answer.

  2. `crm_workflows.engine.activity()` sorted by calling `.reverse()` on
     `store.list()`, which orders by `updated_at` - when the row was written, not
     when the event happened. Reversing that yields write order and its docstring
     calls it event order. The two differ whenever events are recorded out of
     order, which is the normal case for a backfill or a replay.

The first is a property of the store, so it is asserted here against the store.
The second is a property of a feature, so it is asserted against records
deliberately recorded in reverse chronological order - the case a
`.reverse()`-based implementation gets wrong even when the tie-break is fixed.

Both assertions are about ORDER and DETERMINISM, which is why they failed only
on CI. A test that passes on one machine proves nothing about ordering, so each
one here also asserts the order is unchanged by a second identical read: a
one-off correct answer is still luck if the next read can differ.
"""
from __future__ import annotations

import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dsr.db.audited import AuditedDatabase  # noqa: E402


def test_list_order_is_total_when_timestamps_tie(tmp_path):
    """Rows sharing an updated_at must come back in a defined order, every time.

    Written in one tight loop so the writes land in the same second and tie.
    That is the routine case, not an edge case: any burst of writes does it.
    """
    db = AuditedDatabase(tmp_path / "t.db")
    try:
        for i in range(12):
            db.create("widget", {"n": i})
        rows = db.list("widget", order_by="updated_at", descending=True)
        ids = [r["id"] for r in rows]

        # Repeating the read must give the same answer. A single correct read
        # proves nothing when the ordering is a plan artefact.
        for _ in range(5):
            again = [r["id"] for r in db.list("widget", order_by="updated_at", descending=True)]
            assert again == ids, "list() returned a different order on a repeat read"

        # And the order must actually be a total order, not merely stable within
        # one process: no duplicates, and ascending id within each tie group.
        assert len(set(ids)) == len(ids), "list() returned a row twice"
        groups: dict[str, list[str]] = {}
        for r in rows:
            groups.setdefault(r["updated_at"], []).append(r["id"])
        for stamp, group in groups.items():
            assert group == sorted(group, reverse=True), (
                f"rows tied at {stamp} came back in an arbitrary order: {group}"
            )
    finally:
        db.close()


def test_find_order_is_total_when_timestamps_tie(tmp_path):
    """find() had the identical flaw and the identical reason."""
    db = AuditedDatabase(tmp_path / "t.db")
    try:
        for i in range(12):
            db.create("gadget", {"family": "shared", "n": i})
        rows = db.find("gadget", {"family": "shared"})
        ids = [r["id"] for r in rows]
        for _ in range(5):
            again = [r["id"] for r in db.find("gadget", {"family": "shared"})]
            assert again == ids, "find() returned a different order on a repeat read"
        assert len(set(ids)) == len(ids)
    finally:
        db.close()


def test_activity_is_ordered_by_when_it_happened_not_when_it_was_written(tmp_path):
    """Events recorded out of order must still be listed in the order they happened.

    The implementation this replaces reversed `store.list()`, which orders by
    `updated_at` - the write time. Writing the newest event FIRST is the case
    that separates the two, and it is what a backfill does.
    """
    from datetime import datetime, timedelta, timezone

    from dsr.crm_workflows.engine import WorkflowEngine
    from dsr.store import RecordStore

    db = AuditedDatabase(tmp_path / "t.db")
    try:
        engine = WorkflowEngine(RecordStore(db))
        now = datetime.now(timezone.utc)
        room_id = db.create("room", {"name": "Acme"})["id"]

        # Newest first - the reverse of the order they should be listed in.
        for minutes in (10, 30, 20):
            stamp = (now - timedelta(minutes=minutes)).isoformat()
            engine.record_activity(
                room_id,
                {
                    "action": "viewed",
                    "occurred_at": stamp,
                    # The domain refuses an event with no contact: every
                    # filterable activity is tied to the contact record, so one
                    # without could not enrol anybody. Supplying it is not
                    # ceremony - the validator is right and the test was wrong.
                    "contact": "buyer@example.com",
                },
                actor="dana",
                source="test",
            )

        rows = engine.activity(room_id=room_id)
        stamps = [r["occurred_at"] for r in rows]
        assert stamps == sorted(stamps), (
            f"activity is in write order, not event order: {stamps}"
        )
        assert len(stamps) == 3
    finally:
        db.close()
