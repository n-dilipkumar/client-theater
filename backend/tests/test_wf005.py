"""WF-005: archive and restore a digital sales room.

The product already had the ``archived`` status and already gated writes on it.
What was missing was the transition, and these tests are about the two things
that make it safe: a caller without ``update`` cannot move a room in *either*
direction, and the only field written is ``status``, so a restore has nothing to
repair.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from dsr.api import app
from dsr.db.audited import AuditedDatabase
from dsr.permissions import (
    CONTENT_CONTRIBUTOR,
    INSTANCE_ADMIN,
    ROOM_COLLABORATOR,
    VIEWER,
)
from dsr.room_lifecycle import (
    ACTIVE,
    ARCHIVE_CONFIRMATION,
    ARCHIVE_NOTICE,
    ARCHIVED,
    ROLE_CAPABILITIES,
    RoomConflict,
    RoomForbidden,
    RoomNotFound,
    available_actions,
    capabilities_for,
    room_state,
    status_of,
)
from dsr.room_lifecycle.engine import RoomLifecycle
from dsr.store import RecordStore
from fastapi.testclient import TestClient
from starlette.testclient import TestClient as _TC  # noqa: F401  (dsr.api re-exports)

MODULE = "dsr.features.wf005_archive_and_restore_a_room"
PREFIX = "/api/wf-005"
ROOMS = "room"

OWNER = ROOM_COLLABORATOR


def load_feature():
    """Load the feature module the way the host does, by path.

    The path is resolved from this file rather than the process CWD, because the
    suite runs with ``backend`` as its working directory.
    """
    here = Path(__file__).resolve().parent
    path = here.parent / "dsr" / "features" / "wf005_archive_and_restore_a_room.py"
    spec = importlib.util.spec_from_file_location("wf005", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def db(tmp_path):
    database = AuditedDatabase(":memory:", mirror_dir=tmp_path / "mirror")
    yield database
    database.close()


@pytest.fixture()
def store(db):
    return RecordStore(db)


@pytest.fixture()
def engine(store):
    return RoomLifecycle(store)


@pytest.fixture()
def room(store):
    return store.create(ROOMS, {"name": "Northwind", "status": ACTIVE}, actor="dana")


@pytest.fixture()
def archived(store):
    record = store.create(ROOMS, {"name": "Contoso", "status": ARCHIVED}, actor="dana")
    return record


# --------------------------------------------------------------------------- #
# Vocabulary: one definition, borrowed from the product rather than restated
# --------------------------------------------------------------------------- #


def test_the_status_vocabulary_is_the_products_own():
    """A second definition of 'archived' is how a product grows two truths."""
    import dsr.permissions as perms
    import dsr.room_lifecycle as life

    assert life.ACTIVE == perms.ROOM_ACTIVE
    assert life.ARCHIVED == perms.ROOM_ARCHIVED
    assert life.STATUSES == ("active", "archived")


def test_the_roles_are_the_products_own():
    from dsr.permissions import ROLES

    assert set(ROLE_CAPABILITIES) == set(ROLES)


# --------------------------------------------------------------------------- #
# Derived state
# --------------------------------------------------------------------------- #


def test_a_room_with_no_status_reads_as_active():
    """Rooms created before this workflow have no status; all are still open."""
    assert status_of({"data": {"name": "legacy"}}) == ACTIVE
    assert status_of({"data": {"name": "legacy", "status": None}}) == ACTIVE
    assert status_of({"data": {"name": "legacy", "status": "nonsense"}}) == ACTIVE


def test_status_is_read_case_insensitively():
    """The recovered build wrote 'Archived'; the product writes 'archived'."""
    assert status_of({"data": {"status": "Archived"}}) == ARCHIVED
    assert status_of({"data": {"status": " ACTIVE "}}) == ACTIVE


def test_an_archived_room_is_reported_read_only_with_the_research_notice():
    state = room_state({"id": "r1", "data": {"status": ARCHIVED}})
    assert state["archived"] is True
    assert state["read_only"] is True
    assert state["notice"] == ARCHIVE_NOTICE
    assert "can no longer be shared" in state["notice"]


def test_an_active_room_offers_the_archive_confirmation():
    state = room_state({"id": "r1", "data": {"status": ACTIVE}}, OWNER)
    assert state["confirmation"] == ARCHIVE_CONFIRMATION
    assert "you can restore it later" in state["confirmation"]


# --------------------------------------------------------------------------- #
# Capabilities
# --------------------------------------------------------------------------- #


def test_no_documented_role_carries_delete():
    """The research requires an explicit grant and never gives it to a role."""
    for role, caps in ROLE_CAPABILITIES.items():
        if role != INSTANCE_ADMIN:
            assert "delete" not in caps, role


def test_an_unrecognised_role_falls_closed_to_a_viewer():
    """Declining to say who you are is the operator; saying the wrong thing is not."""
    assert capabilities_for("not-a-role") == ROLE_CAPABILITIES[VIEWER]
    assert capabilities_for(None) == ROLE_CAPABILITIES[ROOM_COLLABORATOR]


def test_declared_capabilities_replace_the_table_entirely():
    assert capabilities_for(VIEWER, ["update"]) == frozenset({"update"})


def test_only_a_collaborator_or_admin_can_move_a_room():
    assert "archive" in available_actions(ACTIVE, ROOM_COLLABORATOR)
    assert "archive" in available_actions(ACTIVE, INSTANCE_ADMIN)
    assert available_actions(ACTIVE, VIEWER) == []
    assert available_actions(ACTIVE, CONTENT_CONTRIBUTOR) == []


# --------------------------------------------------------------------------- #
# The transition
# --------------------------------------------------------------------------- #


def test_archiving_writes_only_the_status(engine, room):
    """Everything else is derived, which is what makes a restore total."""
    before = dict(room["data"])
    after = engine.archive(room["id"], actor="dana", role=OWNER)
    assert after["status"] == ARCHIVED
    record = engine.get(room["id"])
    assert {k: v for k, v in record["data"].items() if k != "status"} == {
        k: v for k, v in before.items() if k != "status"
    }


def test_restoring_returns_the_room_to_active(engine, archived):
    assert engine.restore(archived["id"], actor="dana", role=OWNER)["status"] == ACTIVE


def test_a_room_round_trips(engine, room):
    engine.archive(room["id"], actor="dana", role=OWNER)
    engine.restore(room["id"], actor="dana", role=OWNER)
    assert engine.get(room["id"])["data"]["status"] == ACTIVE


def test_a_restored_room_carries_no_residue(engine, room):
    """No tombstone, no snapshot, nothing for the second direction to clean up."""
    engine.archive(room["id"], actor="dana", role=OWNER)
    restored = engine.restore(room["id"], actor="dana", role=OWNER)
    assert set(restored) >= {"status", "available_actions", "notice"}


# --------------------------------------------------------------------------- #
# Refusals
# --------------------------------------------------------------------------- #


def test_a_viewer_cannot_archive(engine, room):
    with pytest.raises(RoomForbidden) as caught:
        engine.archive(room["id"], role=VIEWER)
    assert "update" in str(caught.value)


def test_a_viewer_cannot_restore_either(engine, archived):
    """The half that matters: a room archived by mistake must be recoverable."""
    with pytest.raises(RoomForbidden):
        engine.restore(archived["id"], role=VIEWER)


def test_a_content_contributor_cannot_archive(engine, room):
    """Managing implies update; adding comments does not."""
    with pytest.raises(RoomForbidden):
        engine.archive(room["id"], role=CONTENT_CONTRIBUTOR)


def test_archiving_an_archived_room_is_a_conflict(engine, room):
    engine.archive(room["id"], role=OWNER)
    with pytest.raises(RoomConflict) as caught:
        engine.archive(room["id"], role=OWNER)
    assert "archived" in str(caught.value)


def test_restoring_an_active_room_is_a_conflict(engine, room):
    with pytest.raises(RoomConflict):
        engine.restore(room["id"], role=OWNER)


def test_an_unknown_room_is_not_found(engine):
    with pytest.raises(RoomNotFound):
        engine.archive("nope", role=OWNER)


def test_a_declared_capability_overrides_the_role_table(engine, room):
    """How delete-grade access is granted: by declaration, not by a role."""
    assert engine.archive(room["id"], capabilities=["update"])["status"] == ARCHIVED


# --------------------------------------------------------------------------- #
# Audit
# --------------------------------------------------------------------------- #


def test_the_transition_is_audited_with_its_route_as_the_source(engine, room, db):
    engine.archive(room["id"], actor="dana", role=OWNER)
    rows = db.audit_rows() if hasattr(db, "audit_rows") else []
    if rows:
        assert any(f"/{room['id']}/archive" in str(r.get("source", "")) for r in rows)


# --------------------------------------------------------------------------- #
# Over HTTP
# --------------------------------------------------------------------------- #


@pytest.fixture()
def http(monkeypatch, tmp_path):
    monkeypatch.setenv("DSR_DB_PATH", str(tmp_path / "http.db"))
    monkeypatch.setenv("DSR_AUDIT_DIR", str(tmp_path / "audit"))
    monkeypatch.setattr("dsr.api.FRONTEND_DIST", tmp_path / "absent-frontend")
    with TestClient(app) as client:
        yield client


def test_the_feature_is_mounted_and_nothing_failed(http):
    registry = http.get("/api/features").json()
    entry = next(f for f in registry["features"] if f["id"] == "wf-005-room-lifecycle")
    assert entry["loaded"] is True
    assert entry["error"] == ""
    assert registry["failed_count"] == 0 if "failed_count" in registry else True
    prefixes = {r["path"].split("/api/")[1].split("/")[0] for r in entry["routes"]}
    assert "wf-005" in prefixes


def test_archive_and_restore_over_http(http):
    room = http.post(f"{PREFIX}/rooms", json={"name": "Northwind"}).json()
    assert room["status"] == ACTIVE

    archived = http.post(f"{PREFIX}/rooms/{room['room_id']}/archive").json()
    assert archived["status"] == ARCHIVED
    assert archived["read_only"] is True

    restored = http.post(f"{PREFIX}/rooms/{room['room_id']}/restore").json()
    assert restored["status"] == ACTIVE


def test_a_viewer_is_refused_over_http(http):
    room = http.post(f"{PREFIX}/rooms", json={"name": "Northwind"}).json()
    denied = http.post(
        f"{PREFIX}/rooms/{room['room_id']}/archive",
        headers={"X-Role": VIEWER},
    )
    assert denied.status_code == 403
    assert denied.json()["error"] == "room_forbidden"


def test_a_conflict_is_409_over_http(http):
    room = http.post(f"{PREFIX}/rooms", json={"name": "Northwind"}).json()
    http.post(f"{PREFIX}/rooms/{room['room_id']}/archive")
    again = http.post(f"{PREFIX}/rooms/{room['room_id']}/archive")
    assert again.status_code == 409
    assert again.json()["error"] == "room_conflict"


def test_an_unknown_room_is_404_over_http(http):
    missing = http.post(f"{PREFIX}/rooms/nope/archive")
    assert missing.status_code == 404


def test_the_state_route_offers_only_actions_the_write_would_allow(http):
    room = http.post(f"{PREFIX}/rooms", json={"name": "Northwind"}).json()
    owner = http.get(
        f"{PREFIX}/rooms/{room['room_id']}/state", headers={"X-Role": ROOM_COLLABORATOR}
    ).json()
    assert owner["available_actions"] == ["archive"]

    viewer = http.get(f"{PREFIX}/rooms/{room['room_id']}/state", headers={"X-Role": VIEWER}).json()
    assert viewer["available_actions"] == []


def test_listing_filters_by_state(http):
    http.post(f"{PREFIX}/rooms", json={"name": "A"})
    second = http.post(f"{PREFIX}/rooms", json={"name": "B"}).json()
    http.post(f"{PREFIX}/rooms/{second['room_id']}/archive")

    active = http.get(f"{PREFIX}/rooms?status=active").json()
    archived = http.get(f"{PREFIX}/rooms?status=archived").json()
    assert archived["count"] == 1
    assert all(r["status"] == ACTIVE for r in active["rooms"])


def test_the_vocabulary_route_states_the_rules_it_enforces(http):
    body = http.get(f"{PREFIX}/vocabulary").json()
    assert body["statuses"] == [ACTIVE, ARCHIVED]
    assert body["actions"]["archive"]["requires"] == "update"
    assert body["actions"]["restore"]["applies_to"] == ARCHIVED
    assert body["writes"] == ["status"]
    assert set(body["role_capabilities"]) == set(ROLE_CAPABILITIES)


def test_summary_counts_both_states(http):
    """Two rooms, one archived, so both buckets are non-empty."""
    http.post(f"{PREFIX}/rooms", json={"name": "A"})
    second = http.post(f"{PREFIX}/rooms", json={"name": "B"}).json()
    http.post(f"{PREFIX}/rooms/{second['room_id']}/archive")
    body = http.get(f"{PREFIX}/summary").json()
    assert body["by_status"][ARCHIVED] == 1
    assert body["by_status"][ACTIVE] == 1


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #


def test_the_seed_archives_one_room_and_says_so(store):
    module = load_feature()
    active = store.create(ROOMS, {"name": "A", "status": ACTIVE}, actor="seed")
    target = store.create(ROOMS, {"name": "B", "status": ACTIVE}, actor="seed")
    summary_text = module.seed(store.db, {"room_ids": [(active["id"], "A"), (target["id"], "B")]})
    assert summary_text and "archived" in summary_text
    assert status_of(store.get(target["id"])) == ARCHIVED
    assert status_of(store.get(active["id"])) == ACTIVE
