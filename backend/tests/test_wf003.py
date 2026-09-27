"""HTTP tests for WF-003, the room document library.

Converted from ``backend/tests/test_documents_api.py`` on
``feature/WF-003-populate-and-govern-the-room-s-document-library``. Two things
changed and one thing did not:

* the routes moved from ``/api/rooms/...`` and ``/api/document-workflow`` to this
  feature's own ``/api/wf-003/...`` prefix, because the host mounts a router a
  feature owns rather than letting a workflow append to the shared ``app``;
* the fixture builds a client from the mounted app, so these tests exercise the
  same discovery path a client does. If this feature failed to register, or
  collided with another feature's route, every test here would 404.

What did not change is the intent. The status codes a client branches on are
pinned, and every write is checked to produce exactly one audit row naming the
route that served it.

The domain rules themselves are covered separately, in ``test_documents.py`` and
``test_permissions.py``, which need no HTTP layer at all.
"""

from __future__ import annotations

import random
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dsr.api import app
from dsr.db.audited import AuditedDatabase
from dsr.features import load_feature

PREFIX = "/api/wf-003"

CONTRIBUTOR = {"actor": "sam", "role": "content_contributor"}
COLLABORATOR = {"actor": "dana", "role": "room_collaborator"}
ADMIN = {"actor": "root", "role": "instance_admin"}
VIEWER = {"actor": "buyer", "role": "viewer"}


@pytest.fixture()
def client(monkeypatch):
    # A temporary database, exactly as test_features.py does it: the env var is
    # read at call time by dsr.deps, so every test gets its own store.
    tmp = tempfile.TemporaryDirectory()
    monkeypatch.setenv("DSR_DB_PATH", str(Path(tmp.name) / "wf003.db"))
    monkeypatch.setenv("DSR_AUDIT_DIR", str(Path(tmp.name) / "audit"))
    monkeypatch.setattr("dsr.api.FRONTEND_DIST", Path(tmp.name) / "absent-frontend")
    with TestClient(app) as test_client:
        yield test_client
    tmp.cleanup()


@pytest.fixture()
def room(client):
    return client.post("/api/records/room", json={"name": "Northwind", "status": "active"}).json()


def add(client, room_id, name="Proposal.pdf", who=CONTRIBUTOR, **payload):
    return client.post(
        f"{PREFIX}/rooms/{room_id}/documents", json={"name": name, **payload}, params=who
    )


def archive(client, room):
    client.patch(f"/api/records/room/{room['id']}", json={"status": "archived"})


# -- registration -------------------------------------------------------------- #


def test_the_feature_is_mounted_by_discovery(client):
    """No file in the host names this feature; discovery mounts it anyway."""
    body = client.get("/api/features").json()
    installed = {feature["id"]: feature for feature in body["features"]}

    assert "wf-003-document-library" in installed
    record = installed["wf-003-document-library"]
    assert record["prefix"] == PREFIX
    assert record["ticket"] == "WF-003"
    assert record["exception_handlers"] == [
        "DocumentNotFound",
        "InvalidDocument",
        "InvalidTransition",
        "PermissionDenied",
        "RoomNotFound",
    ]


def test_the_feature_did_not_collide_with_anything(client):
    body = client.get("/api/features").json()
    assert body["failed_count"] == 0, body["failed"]


# -- the empty view ------------------------------------------------------------ #


def test_new_room_has_an_empty_document_library(client, room):
    response = client.get(f"{PREFIX}/rooms/{room['id']}/documents", params=CONTRIBUTOR)

    body = response.json()
    assert response.status_code == 200
    assert body["documents"] == []
    assert body["total"] == 0
    assert body["room_id"] == room["id"]


def test_unknown_room_is_404(client):
    response = client.get(f"{PREFIX}/rooms/room_missing/documents")
    assert response.status_code == 404
    assert response.json()["error"] == "room_not_found"


# -- the New button ------------------------------------------------------------ #


def test_contributor_can_add_a_document(client, room):
    response = add(client, room["id"])

    assert response.status_code == 201
    body = response.json()
    assert body["library"]["name"] == "Proposal.pdf"
    assert body["library"]["status"] == "draft"
    assert body["library"]["uploaded_by"] == "sam"
    assert body["room_id"] == room["id"]


def test_viewer_is_refused_with_403(client, room):
    # The UI hides the New button; the API refuses it as well.
    response = add(client, room["id"], who=VIEWER)
    assert response.status_code == 403
    assert response.json()["error"] == "forbidden"


def test_a_request_with_no_role_is_refused(client, room):
    # Failing closed: forgetting to send a role must not grant upload.
    assert add(client, room["id"], who={}).status_code == 403


def test_document_without_a_name_is_400(client, room):
    response = client.post(
        f"{PREFIX}/rooms/{room['id']}/documents", json={"description": "no name"}, params=CONTRIBUTOR
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_document"


def test_expired_date_on_create_is_400(client, room):
    response = add(client, room["id"], expires_at="2000-01-01")
    assert response.status_code == 400
    assert "future" in response.json()["detail"]


def test_archived_room_refuses_contributor_uploads(client, room):
    archive(client, room)
    assert add(client, room["id"]).status_code == 403
    assert add(client, room["id"], who=ADMIN).status_code == 201


# -- reading the view ---------------------------------------------------------- #


def test_row_carries_the_documented_metadata(client, room):
    document = add(client, room["id"], title="Q3 Proposal").json()

    row = client.get(f"{PREFIX}/rooms/{room['id']}/documents/{document['id']}").json()

    assert row["library"]["title"] == "Q3 Proposal"
    assert row["library"]["last_modified_by"] == "sam"
    assert row["library"]["status"] == "draft"
    assert row["library"]["thumbnail"]["state"] == "pending"


def test_documents_are_scoped_to_their_room(client, room):
    other = client.post("/api/records/room", json={"name": "Contoso"}).json()
    add(client, room["id"], "A.pdf")
    add(client, other["id"], "B.pdf")

    listed = client.get(f"{PREFIX}/rooms/{room['id']}/documents").json()
    assert [d["library"]["name"] for d in listed["documents"]] == ["A.pdf"]


def test_a_document_cannot_be_read_through_another_room(client, room):
    other = client.post("/api/records/room", json={"name": "Contoso"}).json()
    document = add(client, other["id"], "B.pdf").json()

    response = client.get(f"{PREFIX}/rooms/{room['id']}/documents/{document['id']}")
    assert response.status_code == 404
    assert response.json()["error"] == "document_not_found"


def test_search_and_status_filters_compose(client, room):
    first = add(client, room["id"], "Draft-Proposal.pdf").json()
    client.put(
        f"{PREFIX}/rooms/{room['id']}/documents/{first['id']}/status",
        json={"status": "published"},
        params=CONTRIBUTOR,
    )
    add(client, room["id"], "Draft-Roadmap.pdf")

    response = client.get(
        f"{PREFIX}/rooms/{room['id']}/documents",
        params={**CONTRIBUTOR, "search": "proposal", "status": "published"},
    )

    assert [d["id"] for d in response.json()["documents"]] == [first["id"]]


def test_a_teams_custom_field_is_filterable_over_http(client, room):
    # Schema flexibility end to end: no migration, queryable immediately.
    add(client, room["id"], "Contract.pdf", legal={"reviewer": "priya"})

    response = client.get(
        "/api/records/document", params={"where": '{"legal.reviewer":"priya"}'}
    )

    assert response.json()["count"] == 1


# -- workflow status ----------------------------------------------------------- #


def test_status_change_updates_the_row(client, room):
    document = add(client, room["id"]).json()

    response = client.put(
        f"{PREFIX}/rooms/{room['id']}/documents/{document['id']}/status",
        json={"status": "published"},
        params=CONTRIBUTOR,
    )

    assert response.status_code == 200
    assert response.json()["library"]["status"] == "published"


def test_disallowed_transition_is_409(client, room):
    document = add(client, room["id"]).json()
    client.put(
        f"{PREFIX}/rooms/{room['id']}/documents/{document['id']}/status",
        json={"status": "published"},
        params=CONTRIBUTOR,
    )

    response = client.put(
        f"{PREFIX}/rooms/{room['id']}/documents/{document['id']}/status",
        json={"status": "in_review"},
        params=CONTRIBUTOR,
    )

    assert response.status_code == 409
    assert response.json()["error"] == "invalid_transition"


def test_viewer_cannot_change_status(client, room):
    document = add(client, room["id"]).json()
    response = client.put(
        f"{PREFIX}/rooms/{room['id']}/documents/{document['id']}/status",
        json={"status": "published"},
        params=VIEWER,
    )
    assert response.status_code == 403


def test_an_unknown_status_is_accepted(client, room):
    response = add(client, room["id"], status="legal_review")
    assert response.status_code == 201
    assert response.json()["library"]["status"] == "legal_review"
    assert response.json()["library"]["status_known"] is False


# -- patching ------------------------------------------------------------------ #


def test_patch_merges_arbitrary_fields(client, room):
    document = add(client, room["id"]).json()

    response = client.patch(
        f"{PREFIX}/rooms/{room['id']}/documents/{document['id']}",
        json={"notes": "reviewed by legal", "vendor": {"name": "Globex"}},
        params=CONTRIBUTOR,
    )

    assert response.status_code == 200
    assert response.json()["data"]["vendor"] == {"name": "Globex"}


def test_patch_records_the_last_modifier(client, room):
    document = add(client, room["id"], who=COLLABORATOR).json()

    response = client.patch(
        f"{PREFIX}/rooms/{room['id']}/documents/{document['id']}",
        json={"title": "Revised"},
        params=CONTRIBUTOR,
    )

    assert response.json()["library"]["last_modified_by"] == "sam"


# -- deleting ------------------------------------------------------------------ #


def test_uploader_can_delete_their_own_document(client, room):
    document = add(client, room["id"], who=CONTRIBUTOR).json()

    response = client.delete(
        f"{PREFIX}/rooms/{room['id']}/documents/{document['id']}", params=CONTRIBUTOR
    )

    assert response.status_code == 200
    assert response.json()["deleted"] is True
    assert client.get(f"{PREFIX}/rooms/{room['id']}/documents").json()["total"] == 0


def test_collaborator_cannot_delete_someone_elses_document(client, room):
    document = add(client, room["id"], who=COLLABORATOR).json()

    response = client.delete(
        f"{PREFIX}/rooms/{room['id']}/documents/{document['id']}", params=CONTRIBUTOR
    )

    assert response.status_code == 403


def test_instance_admin_can_delete_anyones_document(client, room):
    document = add(client, room["id"], who=COLLABORATOR).json()
    response = client.delete(
        f"{PREFIX}/rooms/{room['id']}/documents/{document['id']}", params=ADMIN
    )
    assert response.status_code == 200


def test_delete_of_an_unknown_document_is_404(client, room):
    response = client.delete(f"{PREFIX}/rooms/{room['id']}/documents/document_missing", params=ADMIN)
    assert response.status_code == 404


# -- gallery block ------------------------------------------------------------- #


def test_gallery_block_is_created_and_listed(client, room):
    first = add(client, room["id"], "A.pdf").json()
    second = add(client, room["id"], "B.pdf").json()

    created = client.post(
        f"{PREFIX}/rooms/{room['id']}/document-gallery",
        json={"label": "Overview", "documents": [first["id"], second["id"]]},
        params=CONTRIBUTOR,
    )
    listed = client.get(f"{PREFIX}/rooms/{room['id']}/document-gallery").json()

    assert created.status_code == 201
    assert listed["slots"] == 4
    assert [d["id"] for d in listed["blocks"][0]["documents"]] == [first["id"], second["id"]]
    assert listed["blocks"][0]["open_in_new_tab"] is True


def test_gallery_rejects_a_fifth_document(client, room):
    documents = [add(client, room["id"], f"D{i}.pdf").json() for i in range(5)]

    response = client.post(
        f"{PREFIX}/rooms/{room['id']}/document-gallery",
        json={"documents": [d["id"] for d in documents]},
        params=CONTRIBUTOR,
    )

    assert response.status_code == 400
    assert "another block" in response.json()["detail"]


def test_gallery_block_can_be_replaced(client, room):
    first = add(client, room["id"], "A.pdf").json()
    second = add(client, room["id"], "B.pdf").json()
    block = client.post(
        f"{PREFIX}/rooms/{room['id']}/document-gallery",
        json={"documents": [first["id"], second["id"]]},
        params=CONTRIBUTOR,
    ).json()

    updated = client.put(
        f"{PREFIX}/rooms/{room['id']}/document-gallery/{block['id']}",
        json={"documents": [second["id"]]},
        params=CONTRIBUTOR,
    )

    assert updated.status_code == 200
    assert [d["id"] for d in updated.json()["documents"]] == [second["id"]]


def test_viewer_cannot_edit_a_gallery_block(client, room):
    response = client.post(
        f"{PREFIX}/rooms/{room['id']}/document-gallery", json={"documents": []}, params=VIEWER
    )
    assert response.status_code == 403


# -- discovery ----------------------------------------------------------------- #


def test_workflow_endpoint_publishes_the_vocabulary(client):
    body = client.get(f"{PREFIX}/document-workflow").json()

    assert body["default"] == "draft"
    assert body["gallery_slots"] == 4
    assert body["folder"] == "documents"
    assert [r["id"] for r in body["roles"]] == [
        "viewer",
        "content_contributor",
        "room_collaborator",
        "instance_admin",
    ]


# -- the audit guarantee ------------------------------------------------------- #


def test_every_library_write_is_audited_exactly_once(client, room):
    document = add(client, room["id"]).json()
    client.patch(
        f"{PREFIX}/rooms/{room['id']}/documents/{document['id']}",
        json={"title": "Revised"},
        params=CONTRIBUTOR,
    )
    client.put(
        f"{PREFIX}/rooms/{room['id']}/documents/{document['id']}/status",
        json={"status": "published"},
        params=CONTRIBUTOR,
    )
    client.delete(f"{PREFIX}/rooms/{room['id']}/documents/{document['id']}", params=CONTRIBUTOR)

    entries = client.get("/api/audit", params={"record_id": document["id"]}).json()["entries"]
    assert [e["action"] for e in entries] == ["delete", "update", "update", "insert"]


def test_audit_rows_name_the_route_that_served_the_write(client, room):
    """The defect the port brief calls out, pinned as a test.

    The branch hard-coded ``source="POST /api/rooms/{room_id}/documents"`` inside
    the domain function, so its audit log kept naming a path the app had stopped
    serving. The source is now built from the mounted prefix, and this asserts
    the recorded value is a path a client can actually call.
    """
    document = add(client, room["id"]).json()
    client.patch(
        f"{PREFIX}/rooms/{room['id']}/documents/{document['id']}",
        json={"title": "Revised"},
        params=CONTRIBUTOR,
    )
    client.put(
        f"{PREFIX}/rooms/{room['id']}/documents/{document['id']}/status",
        json={"status": "published"},
        params=CONTRIBUTOR,
    )
    client.delete(f"{PREFIX}/rooms/{room['id']}/documents/{document['id']}", params=CONTRIBUTOR)

    entries = client.get("/api/audit", params={"record_id": document["id"]}).json()["entries"]
    sources = [e["source"] for e in entries]

    assert sources == [
        f"DELETE {PREFIX}/rooms/{room['id']}/documents/{document['id']}",
        f"PUT {PREFIX}/rooms/{room['id']}/documents/{document['id']}/status",
        f"PATCH {PREFIX}/rooms/{room['id']}/documents/{document['id']}",
        f"POST {PREFIX}/rooms/{room['id']}/documents",
    ]

    # The stronger property: every path recorded in the audit log is one the
    # host actually mounted. The registry reports route *templates*, so a
    # recorded concrete path is matched against them rather than compared
    # literally. Comparing against the registry rather than re-issuing the
    # request, because a recorded DELETE no longer succeeds once it has run -
    # the row is gone, which is the point of the audit trail.
    features = client.get("/api/features").json()["features"]
    templates = [
        (method, route["path"])
        for feature in features
        for route in feature.get("routes", [])
        for method in route["methods"]
    ]
    for entry in entries:
        verb, _, path = entry["source"].partition(" ")
        assert any(
            verb == method and re.fullmatch(re.sub(r"\{[^}]+\}", "[^/]+", template), path)
            for method, template in templates
        ), f"audit names a route the app does not serve: {entry['source']}"


def test_a_refused_write_leaves_no_audit_row(client, room):
    add(client, room["id"], who=VIEWER)
    assert client.get("/api/audit", params={"collection": "document"}).json()["count"] == 0


def test_reading_the_library_writes_nothing(client, room):
    add(client, room["id"])
    before = client.get("/api/audit").json()["count"]

    client.get(f"{PREFIX}/rooms/{room['id']}/documents")
    client.get(f"{PREFIX}/rooms/{room['id']}/document-gallery")
    client.get(f"{PREFIX}/document-workflow")

    assert client.get("/api/audit").json()["count"] == before


# -- demo data ----------------------------------------------------------------- #


def test_seed_leaves_a_readable_library():
    """The feature's own ``seed(db, context)``, which replaced its seed.py edit.

    A feature whose page is empty in the demo is a feature nobody can review, so
    the hook is exercised against a core-shaped dataset: rooms, documents that
    already exist, and a closed room. This is the test that would have caught the
    ``room_id`` argument ``update()`` does not accept.
    """
    module = load_feature("wf003_library")
    now = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)

    tmp = tempfile.TemporaryDirectory()
    try:
        db = AuditedDatabase(str(Path(tmp.name) / "seeded.db"), actor="seed")
        try:
            # A miniature of what backend/seed.py builds: rooms, then documents
            # that already exist and therefore must be filled in, not duplicated.
            rooms = [
                db.create("room", {"name": name, "stage": stage}, actor="dana", source="seed")
                for name, stage in (("Northwind", "evaluation"), ("Adventure Works", "closed"))
            ]
            for index, title in enumerate(("Overview Deck", "Security Pack")):
                db.create(
                    "document",
                    {"title": title, "kind": "deck" if index == 0 else "pdf", "status": "published"},
                    room_id=rooms[0]["id"],
                    actor="dana",
                    source="seed",
                )

            summary = module.seed(
                db,
                {
                    "room_ids": [(room["id"], room["data"]["name"]) for room in rooms],
                    "now": now,
                    "rng": random.Random("wf003_library"),
                },
            )

            assert "2 documents" in summary
            # The closed room is archived so the read-only gate is demonstrable.
            assert db.get(rooms[1]["id"])["data"]["status"] == "archived"
            # The other room is left alone: only `stage == closed` is touched.
            assert "status" not in db.get(rooms[0]["id"])["data"]
            # Two documents in, two documents out: filled in, not duplicated.
            documents = db.list("document", limit=10, order_by="created_at", descending=False)
            assert len(documents) == 2
            assert [d["data"]["folder"] for d in documents] == ["documents", "documents"]
            assert {d["data"]["status"] for d in documents} == {"published", "draft"}
            assert all(d["data"]["uploaded_by"] for d in documents)
            assert db.count("document_gallery") == 1
        finally:
            db.close()
    finally:
        tmp.cleanup()


def test_seed_is_idempotent_on_gallery_blocks():
    """Re-running the seeder must not stack duplicate gallery blocks."""
    module = load_feature("wf003_library")

    tmp = tempfile.TemporaryDirectory()
    try:
        db = AuditedDatabase(str(Path(tmp.name) / "twice.db"), actor="seed")
        try:
            room = db.create("room", {"name": "Northwind"}, actor="dana", source="seed")
            db.create(
                "document",
                {"title": "Deck", "kind": "deck"},
                room_id=room["id"],
                actor="dana",
                source="seed",
            )
            context = {
                "room_ids": [(room["id"], "Northwind")],
                "now": datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc),
                "rng": random.Random("wf003_library"),
            }
            module.seed(db, context)
            module.seed(db, context)
            assert db.count("document_gallery") == 1
        finally:
            db.close()
    finally:
        tmp.cleanup()
