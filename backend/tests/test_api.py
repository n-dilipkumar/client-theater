"""Tests for the HTTP API.

The API is the integration surface other teams build against, so these tests
pin the envelope shape, the schema-flexibility promise, and the audit guarantee
as observed from outside the process.
"""

from __future__ import annotations

import os

import pytest
from dsr.api import app
from dsr.db.audited import AuditedDatabase
from dsr.store import RecordStore
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def _entered_client(tmp_path_factory):
    """Enter one TestClient for the whole module.

    Entering a TestClient runs the FastAPI lifespan, which opens the database.
    That is the expensive part, and a test needs a fresh *database*, not a fresh
    *application*: ``dsr.deps.get_store`` reads ``request.app.state.store`` on
    every request, so replacing that attribute is enough to isolate a test. The
    client is entered once here and the store is swapped per test below, which
    keeps the same isolation for a fraction of the cost.

    Module scope rules out ``monkeypatch``, so the environment is set by hand and
    put back on the way out.
    """
    import dsr.api as api_module

    tmp = tmp_path_factory.mktemp("client")
    saved = {name: os.environ.get(name) for name in ("DSR_DB_PATH", "DSR_AUDIT_DIR")}
    saved_dist = api_module.FRONTEND_DIST
    os.environ["DSR_DB_PATH"] = str(tmp / "api.db")
    os.environ["DSR_AUDIT_DIR"] = str(tmp / "audit")
    # Static mounts are import-time, so point the module at a missing directory
    # to keep these tests focused on the API rather than the built frontend.
    api_module.FRONTEND_DIST = tmp / "absent-frontend"
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        api_module.FRONTEND_DIST = saved_dist


@pytest.fixture()
def client(_entered_client):
    """Give each test its own empty database, in memory.

    The previous store is put back on the way out. `app` is a module-level
    singleton shared by every test file in the process, so leaving a closed
    database on `app.state` would be state this fixture leaked into whatever
    module runs next.
    """
    app_ = _entered_client.app
    previous_db, previous_store = app_.state.db, app_.state.store
    db = AuditedDatabase(":memory:", actor="api")
    app_.state.db = db
    app_.state.store = RecordStore(db)
    try:
        yield _entered_client
    finally:
        app_.state.db = previous_db
        app_.state.store = previous_store
        db.close()


# -- system ----------------------------------------------------------------- #


def test_health(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_stats_starts_empty(client):
    body = client.get("/api/stats").json()
    assert body["records"] == 0
    assert body["audit_entries"] == 0


# -- create / read ---------------------------------------------------------- #


def test_create_record_returns_envelope(client):
    response = client.post("/api/records/room", json={"name": "Acme", "stage": "demo"})

    assert response.status_code == 201
    body = response.json()
    assert body["collection"] == "room"
    assert body["data"] == {"name": "Acme", "stage": "demo"}
    assert body["revision"] == 1
    assert body["id"].startswith("room_")


def test_get_record_by_id(client):
    created = client.post("/api/records/room", json={"name": "Acme"}).json()

    fetched = client.get(f"/api/records/room/{created['id']}")

    assert fetched.status_code == 200
    assert fetched.json()["data"]["name"] == "Acme"


def test_get_unknown_record_is_404(client):
    assert client.get("/api/records/room/room_missing").status_code == 404


def test_get_record_from_wrong_collection_is_404(client):
    created = client.post("/api/records/room", json={"name": "Acme"}).json()

    response = client.get(f"/api/records/document/{created['id']}")

    assert response.status_code == 404


# -- schema flexibility ----------------------------------------------------- #


def test_arbitrary_payload_accepted_without_schema_change(client):
    payload = {
        "name": "Acme",
        "branding": {"theme": "dark", "palette": {"primary": "#0f172a"}},
        "integrations": ["salesforce", "zoom"],
        "seats": 12,
        "public": True,
    }

    created = client.post("/api/records/room", json=payload).json()

    assert created["data"] == payload


def test_where_filters_on_nested_json_paths(client):
    client.post("/api/records/room", json={"name": "A", "branding": {"theme": "dark"}})
    client.post("/api/records/room", json={"name": "B", "branding": {"theme": "light"}})

    response = client.get("/api/records/room", params={"where": '{"branding.theme":"dark"}'})

    assert [r["data"]["name"] for r in response.json()["records"]] == ["A"]


def test_where_accepts_compact_form(client):
    client.post("/api/records/room", json={"name": "A", "stage": "demo", "seats": 5})
    client.post("/api/records/room", json={"name": "B", "stage": "pilot", "seats": 5})

    response = client.get("/api/records/room", params={"where": "stage=demo,seats=5"})

    assert [r["data"]["name"] for r in response.json()["records"]] == ["A"]


def test_where_rejects_malformed_json(client):
    response = client.get("/api/records/room", params={"where": "{not json"})
    assert response.status_code == 400


def test_collections_endpoint_reports_fields_in_use(client):
    client.post("/api/records/room", json={"name": "A", "stage": "demo"})
    client.post("/api/records/document", json={"title": "D"})

    body = client.get("/api/collections").json()

    assert body["count"] == 2
    rooms = next(c for c in body["collections"] if c["collection"] == "room")
    paths = {f["path"] for f in rooms["fields"]}
    assert {"name", "stage"} <= paths


# -- update / delete -------------------------------------------------------- #


def test_patch_merges_and_bumps_revision(client):
    created = client.post("/api/records/room", json={"name": "Acme", "stage": "demo"}).json()

    updated = client.patch(f"/api/records/room/{created['id']}", json={"stage": "pilot"})

    assert updated.status_code == 200
    assert updated.json()["data"] == {"name": "Acme", "stage": "pilot"}
    assert updated.json()["revision"] == 2


def test_patch_with_stale_revision_is_409(client):
    created = client.post("/api/records/room", json={"stage": "demo"}).json()
    client.patch(f"/api/records/room/{created['id']}", json={"stage": "pilot"})

    response = client.patch(
        f"/api/records/room/{created['id']}",
        json={"stage": "closed"},
        params={"expected_revision": 1},
    )

    assert response.status_code == 409


def test_delete_is_soft_and_hides_from_reads(client):
    created = client.post("/api/records/room", json={"name": "Acme"}).json()

    response = client.delete(f"/api/records/room/{created['id']}")

    assert response.status_code == 200
    assert response.json()["hard"] is False
    assert client.get(f"/api/records/room/{created['id']}").status_code == 404
    assert client.get("/api/records/room").json()["count"] == 0


def test_hard_delete_removes_entirely(client):
    created = client.post("/api/records/room", json={"name": "Acme"}).json()

    client.delete(f"/api/records/room/{created['id']}", params={"hard": True})

    listed = client.get("/api/records/room", params={"include_deleted": True}).json()
    assert listed["count"] == 0


def test_restore_brings_record_back(client):
    created = client.post("/api/records/room", json={"name": "Acme"}).json()
    client.delete(f"/api/records/room/{created['id']}")

    restored = client.post(f"/api/records/room/{created['id']}/restore")

    assert restored.status_code == 200
    assert client.get(f"/api/records/room/{created['id']}").status_code == 200


def test_delete_unknown_is_404(client):
    assert client.delete("/api/records/room/room_missing").status_code == 404


# -- bulk ------------------------------------------------------------------- #


def test_bulk_create_is_one_audit_entry(client):
    items = [{"title": "a"}, {"title": "b"}, {"title": "c"}]

    response = client.post("/api/records/document/bulk", json=items)

    assert response.status_code == 201
    assert response.json()["count"] == 3
    audit = client.get("/api/audit", params={"collection": "document"}).json()
    assert audit["count"] == 1


# -- audit ------------------------------------------------------------------ #


def test_every_mutation_produces_exactly_one_audit_entry(client):
    room = client.post("/api/records/room", json={"name": "Acme", "stage": "demo"}).json()
    client.patch(f"/api/records/room/{room['id']}", json={"stage": "pilot"})
    client.delete(f"/api/records/room/{room['id']}")
    client.post(f"/api/records/room/{room['id']}/restore")

    entries = client.get("/api/audit", params={"record_id": room["id"]}).json()["entries"]

    assert [e["action"] for e in entries] == ["restore", "delete", "update", "insert"]


def test_audit_entry_exposes_before_after_and_diff(client):
    room = client.post("/api/records/room", json={"stage": "demo"}).json()
    client.patch(f"/api/records/room/{room['id']}", json={"stage": "pilot"})

    entry = client.get("/api/audit", params={"record_id": room["id"], "action": "update"}).json()[
        "entries"
    ][0]

    assert entry["before_state"]["stage"] == "demo"
    assert entry["after_state"]["stage"] == "pilot"
    assert entry["diff"]["stage"] == {"from": "demo", "to": "pilot"}


def test_audit_filters_by_collection_and_actor(client):
    client.post("/api/records/room", json={"name": "A"}, params={"actor": "dana"})
    client.post("/api/records/document", json={"title": "D"}, params={"actor": "sam"})

    assert client.get("/api/audit", params={"collection": "room"}).json()["count"] == 1
    assert client.get("/api/audit", params={"actor": "sam"}).json()["count"] == 1


def test_audit_entry_is_fetchable_by_sequence(client):
    client.post("/api/records/room", json={"name": "A"})

    listed = client.get("/api/audit").json()["entries"]
    seq = listed[0]["seq"]

    assert client.get(f"/api/audit/{seq}").json()["seq"] == seq
    assert client.get("/api/audit/999999").status_code == 404


def test_reads_do_not_appear_in_the_audit_log(client):
    room = client.post("/api/records/room", json={"name": "A"}).json()

    client.get(f"/api/records/room/{room['id']}")
    client.get("/api/records/room")
    client.get("/api/stats")

    assert client.get("/api/audit").json()["count"] == 1


def test_stats_reflects_activity(client):
    room = client.post("/api/records/room", json={"name": "A"}).json()
    client.patch(f"/api/records/room/{room['id']}", json={"stage": "x"})

    body = client.get("/api/stats").json()

    assert body["records"] == 1
    assert body["audit_entries"] == 2
    assert body["by_action"] == {"insert": 1, "update": 1}
