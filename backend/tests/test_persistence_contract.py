"""The persistence contract: a room survives its database being closed and reopened.

The local dev loop exists so that work done in the browser is still there after
the servers are stopped and relaunched. Every other test in this suite creates a
room and reads it back through the SAME open connection, which proves the write
path but not the promise: nothing there would fail if the data lived only in
memory and were lost on exit.

These tests close ``AuditedDatabase`` and open a second one against the same
file. That is exactly what a launcher restart does, so it is the smallest honest
test of the claim the loop makes to its user.

Nothing here touches ``data/dsr.db``. Every test uses ``tmp_path``, so running
the suite can never write into the database a person is working in.
"""

from __future__ import annotations

from dsr.db.audited import AuditedDatabase
from dsr.room_templates import create_room
from dsr.store import RecordStore

CREATE_SOURCE = "/api/wf-001/rooms"


def _open(path, mirror):
    return RecordStore(AuditedDatabase(path, mirror_dir=mirror))


def _make_room(store: RecordStore, name: str = "Acme Evaluation", **extra) -> dict:
    account = store.create("account", {"name": "Northwind Traders"}, actor="dana")
    return create_room(
        store,
        name=name,
        account_id=account["id"],
        template_id="tpl_standard",
        source=CREATE_SOURCE,
        extra=extra or None,
    )


def test_a_room_survives_its_database_being_reopened(tmp_path):
    """The room, its bound site, and the audit count are all still there."""
    db_path = tmp_path / "loop.db"
    mirror = tmp_path / "mirror"

    first = _open(db_path, mirror)
    try:
        room = _make_room(first)
        room_id, site_id = room["id"], room["site"]["id"]
        audit_before = first.db.audit_count()
    finally:
        first.db.close()

    # Nothing holds the connection now. This is the restart boundary.
    second = _open(db_path, mirror)
    try:
        stored_room = second.get(room_id)
        assert stored_room is not None, "the room did not survive the reopen"
        assert stored_room["collection"] == "room"
        assert stored_room["data"]["name"] == "Acme Evaluation"

        stored_site = second.get(site_id)
        assert stored_site is not None, "the site did not survive the reopen"
        # room_id is an ENVELOPE column, not part of the free-form payload, so
        # the binding survives independently of anything written into data.
        assert stored_site["room_id"] == room_id, "the site lost its binding"

        # Both writes were audited, and the audit count outlived the connection.
        assert second.db.audit_count() == audit_before
    finally:
        second.db.close()


def test_a_reopened_room_still_lists_and_filters(tmp_path):
    """A reopened room is reachable the way the app queries it, not just by id."""
    db_path = tmp_path / "loop.db"
    mirror = tmp_path / "mirror"

    first = _open(db_path, mirror)
    try:
        room = _make_room(first, name="Reopened Co")
        account_id = room["data"]["account_id"]
        room_id = room["id"]
    finally:
        first.db.close()

    second = _open(db_path, mirror)
    try:
        listed = second.list("room")
        assert [r["id"] for r in listed] == [room_id]

        # The dynamic index resolves payload fields by their bare name, so
        # filtering works after the reopen exactly as it did before it. There is
        # no "data." prefix - that is the JSON column, not the key.
        by_name = second.find("room", {"name": "Reopened Co"})
        assert [r["id"] for r in by_name] == [room_id]

        by_account = second.find("room", {"account_id": account_id})
        assert [r["id"] for r in by_account] == [room_id]
    finally:
        second.db.close()


def test_a_room_survives_a_reopen_with_an_arbitrary_payload_field(tmp_path):
    """Schema flexibility holds across a restart, not just within one session.

    A team adding a field must not need a migration, and that promise has to
    hold for data written in an earlier process. If it only held in-session, a
    restart would be exactly where a team's field silently disappeared.
    """
    db_path = tmp_path / "loop.db"
    mirror = tmp_path / "mirror"

    first = _open(db_path, mirror)
    try:
        room = _make_room(
            first,
            name="Flexible Co",
            # A field no column exists for, and no migration was written.
            procurement_contact="dana@northwind.example",
            renewal_notice_days=45,
        )
        room_id = room["id"]
    finally:
        first.db.close()

    second = _open(db_path, mirror)
    try:
        stored = second.get(room_id)
        assert stored["data"]["procurement_contact"] == "dana@northwind.example"
        assert stored["data"]["renewal_notice_days"] == 45
        found = second.find("room", {"procurement_contact": "dana@northwind.example"})
        assert [r["id"] for r in found] == [room_id]
    finally:
        second.db.close()


def test_reopening_preserves_the_audit_trail_for_that_record(tmp_path):
    """The audit log is the project's core guarantee, so it is asserted on reopen.

    The JSONL mirror is flushed only after a successful commit, so a crash
    between commit and flush is the failure this guards: the records survive but
    the evidence of them does not.
    """
    db_path = tmp_path / "loop.db"
    mirror = tmp_path / "mirror"

    first = _open(db_path, mirror)
    try:
        room = _make_room(first)
        room_id, site_id = room["id"], room["site"]["id"]
    finally:
        first.db.close()

    second = _open(db_path, mirror)
    try:
        entries = second.db.audit()
        audited = {e["record_id"] for e in entries}
        assert room_id in audited
        assert site_id in audited

        inserts = [e for e in entries if e["record_id"] == room_id]
        assert inserts, "the room has no audit row"
        for entry in inserts:
            assert entry["action"] == "insert"
            assert entry.get("actor"), "an audit row with no actor records nothing"
    finally:
        second.db.close()


def test_the_audit_mirror_written_before_the_reopen_is_still_there(tmp_path):
    """The on-disk JSONL mirror survives, not just the SQLite audit table."""
    db_path = tmp_path / "loop.db"
    mirror = tmp_path / "mirror"

    first = _open(db_path, mirror)
    try:
        room = _make_room(first)
        room_id = room["id"]
    finally:
        first.db.close()

    mirrored = "\n".join(p.read_text(encoding="utf-8") for p in mirror.glob("*.jsonl"))
    assert room_id in mirrored, "the audit mirror lost the room's write"


def test_the_launcher_never_forces_the_database_path():
    """``DSR_DB_PATH`` is respected when set, and defaulted only when it is not.

    The launcher passes the default explicitly so a launch is predictable, but
    it must never overwrite a value the person set on purpose - that is the only
    way to point a run at a scratch database. The shape of the assignment is
    what matters here: read first, fill only when absent.
    """
    import pathlib

    launcher = pathlib.Path(__file__).resolve().parents[2] / "start-dev.ps1"
    source = launcher.read_text(encoding="utf-8")

    assert "if (-not $env:DSR_DB_PATH)" in source
    assert "$env:DSR_DB_PATH = Join-Path $DataDir 'dsr.db'" in source
    # A bare assignment with no guard would silently discard a deliberate choice.
    guarded = source.index("if (-not $env:DSR_DB_PATH)")
    assigned = source.index("$env:DSR_DB_PATH = Join-Path $DataDir 'dsr.db'")
    assert guarded < assigned, "the guard must come before the assignment"
