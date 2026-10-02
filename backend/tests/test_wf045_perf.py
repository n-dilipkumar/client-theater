"""The lookup pattern in ``_land``, held to a bound.

This file exists because of a measured regression, not because a style rule said
so. WF-045's first seed implementation looked up each row of a page with
``store.find(REPLICA, {"external_id": ...})``, and the profile put 188 of its 190
seconds into 2,410 such calls - about 77ms each, because ``AuditedDatabase.find``
resolves a condition as a correlated ``EXISTS`` over ``record_index`` and there
is no index on ``(path, value_text)``. The consequence was not this feature's
slow seed but another feature's test: ``test_wf019.py`` shells out to the whole
seeder with a 300 second timeout, and the seeder crossed it.

The fix is in ``engine._replica_index``: one plain ordered scan per thousand rows
per page, instead of one correlated-index scan per row. This file pins that, so
the next person who finds the code "could be simplified" finds a test saying what
it cost first.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Mapping

import pytest
from dsr.crm_backfill import (
    CURSORS_STORE,
    REPLICA,
    RUNS,
    BackfillEngine,
    SimulatedHistory,
    default_registry,
)
from dsr.crm_backfill.engine import CONNECTIONS
from dsr.db.audited import AuditedDatabase
from dsr.store import RecordStore

SOURCE = "POST /api/wf-045/rooms/{room_id}/backfills"


def _rows(count: int, prefix: str = "acc") -> list[dict[str, Any]]:
    return [
        {
            "id": f"{prefix}-{index:05d}",
            "Name": f"Account {index:05d}",
            "Stage__c": "Proposal",
            "Amount": index,
            "occurred_at": "2026-06-01T00:00:00+00:00",
        }
        for index in range(count)
    ]


@pytest.fixture()
def store(tmp_path):
    db = AuditedDatabase(tmp_path / "perf.db", mirror_dir=tmp_path / "mirror")
    yield RecordStore(db)
    db.close()


def test_landing_a_page_asks_the_store_often_enough_to_be_a_page(store):
    """The count, not the clock: a wall-clock bound is a flaky test on a loaded host.

    One page of 1,000 rows must not cost 1,000 lookups. The bound is generous -
    the fix leaves a couple of plain scans per page - and the point is that
    somebody changing the loop back to a per-row `find` would exceed it by two
    orders of magnitude, so the test fails for the right reason and not for the
    machine being busy.

    Counted on ``RecordStore.list`` rather than on the SQLite connection,
    because ``sqlite3.Connection.execute`` is read-only and cannot be wrapped.
    ``list`` is the access path this fix moved onto, and ``find`` is covered by
    its own test below, so nothing escapes the count.
    """
    lists: list[Mapping[str, Any]] = []
    finds: list[Mapping[str, Any]] = []
    original_list = store.list
    original_find = store.find

    def watched_list(collection, **kwargs):
        lists.append({"collection": collection, **kwargs})
        return original_list(collection, **kwargs)

    def watched_find(collection, where, **kwargs):
        finds.append({"collection": collection, "where": where})
        return original_find(collection, where, **kwargs)

    store.list = watched_list  # type: ignore[method-assign]
    store.find = watched_find  # type: ignore[method-assign]
    try:
        engine = BackfillEngine(store, registry=default_registry(SimulatedHistory(_rows(1_000))))
        connection = engine.create_connection({"vendor": "dataverse"}, source=SOURCE)
        lists.clear()
        engine.start(
            "room-1",
            {
                "connection_id": connection["id"],
                "scope": {"kind": "full_history"},
                "page_size": 1_000,
            },
            source=SOURCE,
        )
        page_lists = list(lists)
        page_finds = list(finds)
    finally:
        store.list = original_list  # type: ignore[method-assign]
        store.find = original_find  # type: ignore[method-assign]

    assert len(engine.replica(room_id="room-1", limit=1_000)) == 1_000
    replica_lists = [call for call in page_lists if call["collection"] == REPLICA]
    assert len(replica_lists) <= 4, (
        f"a 1,000-row page scanned the replica {len(replica_lists)} times"
    )
    assert [call for call in page_finds if call["collection"] == REPLICA] == []


def test_landing_a_page_of_a_thousand_rows_is_not_pathological_in_wall_clock(store):
    """A wall-clock bound, as a smoke test against a regression rather than a limit.

    Loose enough not to fail on a loaded host, tight enough that the 77ms-per-row
    lookup this file was written about (about 77 seconds for a thousand rows)
    cannot come back unnoticed.
    """
    engine = BackfillEngine(store, registry=default_registry(SimulatedHistory(_rows(1_000))))
    connection = engine.create_connection({"vendor": "dataverse"}, source=SOURCE)

    started = time.perf_counter()
    engine.start(
        "room-1",
        {"connection_id": connection["id"], "scope": {"kind": "full_history"}, "page_size": 1_000},
        source=SOURCE,
    )
    elapsed = time.perf_counter() - started
    assert elapsed < 30.0, f"1,000 rows took {elapsed:.1f}s"


def test_the_replica_index_resolves_every_key_of_a_page_even_when_the_room_is_large(store):
    """The lookup walks the room in batches, so the batch boundary is a real case.

    A room holding more than one batch of rows, and a page whose keys sit at the
    far end of that walk, is exactly where "resolved in one pass" would quietly
    stop resolving. The keys are chosen to sit past the first batch.
    """
    engine = BackfillEngine(
        store, registry=default_registry(SimulatedHistory(_rows(1_500, "first")))
    )
    connection = engine.create_connection({"vendor": "dataverse"}, source=SOURCE)
    first = engine.start(
        "room-1",
        {"connection_id": connection["id"], "scope": {"kind": "full_history"}, "page_size": 1_500},
        source=SOURCE,
    )
    assert first["counters"]["rows_created"] == 1_500

    # A second source holding the same keys under different content, landing as
    # an update rather than as 1,500 fresh rows, proves the walk found them.
    engine.registry = default_registry(
        SimulatedHistory([{**row, "Name": "Renamed"} for row in _rows(1_500, "first")])
    )
    again = engine.start(
        "room-1",
        {
            "connection_id": connection["id"],
            "scope": {"kind": "full_history"},
            "page_size": 1_500,
            "from_scratch": True,
        },
        source=SOURCE,
    )
    assert again["counters"]["rows_created"] == 0
    assert again["counters"]["rows_updated"] == 1_500

    # Read back through the store directly rather than through `engine.replica`,
    # because that listing is capped at 1,000 and asserting on 1,500 from one
    # call would be asserting on a cap rather than on the data.
    first_batch = store.list(REPLICA, room_id="room-1", limit=1_000, offset=0)
    second_batch = store.list(REPLICA, room_id="room-1", limit=1_000, offset=1_000)
    assert len(first_batch) + len(second_batch) == 1_500
    assert all(row["data"]["Name"] == "Renamed" for row in first_batch + second_batch), (
        "the second read renamed every row, which only happens if the walk found them"
    )


def test_the_replica_index_does_not_leak_rows_from_another_connection(store):
    """Two connections on one room, same vendor, different external ids.

    Keyed on external id alone would let one connection's row stand in for
    another's when the ids collide, which is a silent wrong merge rather than a
    failure.
    """
    engine = BackfillEngine(store, registry=default_registry(SimulatedHistory(_rows(5))))
    first = engine.create_connection({"vendor": "dataverse"}, source=SOURCE)
    second = engine.create_connection({"vendor": "dataverse"}, source=SOURCE)
    engine.start(
        "room-1",
        {"connection_id": first["id"], "scope": {"kind": "full_history"}},
        source=SOURCE,
    )
    engine.start(
        "room-1",
        {"connection_id": second["id"], "scope": {"kind": "full_history"}},
        source=SOURCE,
    )
    assert len(engine.replica(room_id="room-1", connection_id=first["id"], limit=100)) == 5
    assert len(engine.replica(room_id="room-1", connection_id=second["id"], limit=100)) == 5


def test_the_lookup_never_touches_the_replica_index_through_find(store):
    """`find` is the slow path this file is about, so the loop must not use it.

    Asserted rather than assumed: a future edit that reaches for the convenient
    API would pass every behavioural test above and cost 77 milliseconds a row.
    """
    engine = BackfillEngine(store, registry=default_registry(SimulatedHistory(_rows(50))))
    connection = engine.create_connection({"vendor": "dataverse"}, source=SOURCE)

    calls: list[Mapping[str, Any]] = []
    original = store.find

    def watched(collection, where, **kwargs):
        calls.append({"collection": collection, "where": where})
        return original(collection, where, **kwargs)

    store.find = watched  # type: ignore[method-assign]
    try:
        engine.start(
            "room-1",
            {"connection_id": connection["id"], "scope": {"kind": "full_history"}},
            source=SOURCE,
        )
    finally:
        store.find = original  # type: ignore[method-assign]

    replica_lookups = [call for call in calls if call["collection"] == REPLICA]
    assert replica_lookups == [], f"the page cycle still uses find for {replica_lookups}"


def test_the_module_under_test_is_where_the_file_says_it_is():
    """A path assertion, because this file names lines in a file that can move."""
    engine_source = (
        Path(__file__).resolve().parents[1] / "dsr" / "crm_backfill" / "engine.py"
    ).read_text(encoding="utf-8")
    assert "def _replica_index(" in engine_source
    assert "def _find_replica(" not in engine_source, (
        "the per-row lookup this file was written about is back"
    )
    # The collections this feature owns, named so a rename is a test failure.
    assert {CONNECTIONS, RUNS, CURSORS_STORE, REPLICA} == {
        "crm_backfill_connection",
        "crm_backfill_run",
        "crm_backfill_cursor",
        "crm_replica",
    }
