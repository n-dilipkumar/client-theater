"""Tests for WF-007: ingest a document or deck into the content library.

The researched contract is specific, so these tests pin the specific parts:
the required metadata, the ``root`` folder keyword, name de-collision, the
``rollbackOnError`` promise, add-a-version rather than replace-the-binary, the
asynchronous thumbnail, and the schema-flexible passthrough.

They run over HTTP against the real app, because the multipart contract *is*
the contract. The failure paths HTTP cannot reach -- a filesystem that refuses a
write -- are pinned against :class:`~dsr.library.ContentLibrary` directly.

Three tests are about the port rather than the workflow, and are the ones worth
not deleting:

* the feature is mounted by discovery alone, under a prefix it owns;
* every audited write names the route that actually served it, which is the
  defect the branch shipped with its source strings baked into the domain;
* the demo seed goes through the same ingest path the API uses.

A note on the environment: serving the multipart routes needs the
``python-multipart`` package, which is not declared in ``backend/pyproject.toml``
(this port is not permitted to add it). Collection itself raises a clear error
if it is absent, so a missing dependency is one obvious message rather than a
wall of collection errors.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import tempfile
from pathlib import Path

import pytest
from dsr.api import app
from dsr.db.audited import AuditedDatabase
from dsr.library import (
    BlobStorageError,
    ContentLibrary,
    ValidationError,
    collision_name,
    derive_format,
    join_path,
    parse_version,
    split_name,
)
from dsr.store import RecordStore
from fastapi.testclient import TestClient

FEATURE_ID = "wf-007-content-library"
PREFIX = "/api/library"

# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

PAYLOAD = b"%PDF-1.7 fake deck bytes \x00\x01\x02 for testing"
PAYLOAD_V2 = b"%PDF-1.7 fake deck bytes, second revision, longer than the first"


@pytest.fixture()
def client(monkeypatch):
    import dsr.api as api_module

    tmp = tempfile.TemporaryDirectory()
    monkeypatch.setenv("DSR_DB_PATH", str(Path(tmp.name) / "api.db"))
    monkeypatch.setenv("DSR_AUDIT_DIR", str(Path(tmp.name) / "audit"))
    # Binary payloads live on the filesystem, not in SQLite, so the test needs
    # its own content root or every test would share one blob store. The feature
    # reads this at call time, which is what makes the swap safe.
    monkeypatch.setenv("DSR_CONTENT_DIR", str(Path(tmp.name) / "content"))
    monkeypatch.setenv("DSR_MAX_UPLOAD_BYTES", "4096")
    # Static mounts are import-time, so point the module at a missing directory
    # to keep these tests focused on the API rather than the built frontend.
    monkeypatch.setattr(api_module, "FRONTEND_DIST", Path(tmp.name) / "absent-frontend")
    with TestClient(app) as test_client:
        yield test_client
    tmp.cleanup()


@pytest.fixture()
def content_root() -> Path:
    """The blob root the client fixture pointed the feature at."""
    return Path(os.environ["DSR_CONTENT_DIR"])


@pytest.fixture()
def room(client) -> dict:
    """A room to ingest into. The room is the teamsite equivalent."""
    return client.post("/api/records/room", json={"name": "Acme renewal", "stage": "demo"}).json()


def post_document(
    client,
    room_id,
    *,
    filename="Q2 Sales Deck.pptx",
    content: bytes = PAYLOAD,
    metadata=None,
    files=None,
    data=None,
):
    """POST the multipart ingest: a metadata JSON part and a binary part."""
    payload = {
        "name": Path(filename).name,
        "format": derive_format(filename),
        "parentFolderId": "root",
    }
    if metadata is not None:
        payload = metadata
    form = {"metadata": json.dumps(payload)}
    if data:
        form.update(data)
    upload = {
        "content": (filename, io.BytesIO(content), "application/octet-stream")
        if files is None
        else files
    }
    return client.post(
        f"{PREFIX}/rooms/{room_id}/documents",
        data=form,
        files=upload,
    )


# --------------------------------------------------------------------------- #
# The port: this feature is a plugin, not an edit to the host
# --------------------------------------------------------------------------- #


def test_feature_is_mounted_by_discovery_under_a_prefix_it_owns(client):
    """Nothing in api.py names this feature, yet /api/library resolves."""
    body = client.get("/api/features").json()
    feature = next(f for f in body["features"] if f["id"] == FEATURE_ID)
    assert feature["ticket"] == "WF-007"
    assert feature["prefix"] == PREFIX
    assert feature["exception_handlers"] == ["LibraryError"]
    # A mounted router that reported no routes would be a silent no-op.
    assert len(feature["routes"]) == 12


def test_no_feature_failed_to_load(client):
    """A missing python-multipart shows up here, not as a 500 somewhere."""
    assert client.get("/api/features").json()["failed"] == []


def test_prefix_is_shared_with_wf010_without_colliding():
    """Two features may share /api/library; the host refuses only on (method, path).

    This is the check that held WF-007 back on the belief that it collided with
    WF-010 on the prefix. Sharing a prefix is legitimate; the collision rule is
    on the concrete path.
    """
    from dsr.features import REGISTRY

    owned = [
        (method, route["path"], feature.id)
        for feature in REGISTRY.features
        for route in feature.routes
        for method in route["methods"]
    ]
    seen: set[tuple[str, str]] = set()
    for method, path, feature_id in owned:
        key = (method, path)
        assert key not in seen, f"{feature_id} duplicates {key}"
        seen.add(key)


def test_vocabulary_is_served(client):
    body = client.get(f"{PREFIX}/vocabulary").json()
    assert body["folder_root_keyword"] == "root"
    assert body["initial_version"] == "0.1"
    assert body["max_upload_bytes"] == 4096  # the fixture's ceiling, not the 2 GB default


# --------------------------------------------------------------------------- #
# The port: an audit row names the route that served it
# --------------------------------------------------------------------------- #


def audit_rows(client, **params) -> list[dict]:
    return client.get("/api/audit", params=params).json()["entries"]


def test_every_write_names_the_route_that_served_it(client, room):
    """The branch baked literal paths into the domain, so the log could name a
    route the app had stopped serving.

    Scoped to this feature's own collections: the room in the fixture is created
    through the core ``/api/records/room`` route, and that row is correctly
    attributed to the core route rather than to this one.
    """
    document_id = post_document(client, room["id"]).json()["id"]
    client.post(f"{PREFIX}/rooms/{room['id']}/folders", json={"name": "Q2 Decks"})
    client.post(f"{PREFIX}/documents/{document_id}/thumbnail")
    client.put(
        f"{PREFIX}/documents/{document_id}",
        files={"content": ("Deck.pptx", io.BytesIO(PAYLOAD_V2), "application/octet-stream")},
    )
    client.delete(f"{PREFIX}/documents/{document_id}")

    seen = set()
    for collection in ("document", "documentFolder"):
        for row in audit_rows(client, collection=collection):
            verb, _, path = row["source"].partition(" ")
            seen.add((verb, path))

    # Nothing fell back to the domain's own labels ("library ingest",
    # "library delete"), and nothing recorded an unexpanded route template.
    assert seen == {
        ("POST", f"{PREFIX}/rooms/{room['id']}/documents"),
        ("POST", f"{PREFIX}/rooms/{room['id']}/folders"),
        ("POST", f"{PREFIX}/documents/{document_id}/thumbnail"),
        ("PUT", f"{PREFIX}/documents/{document_id}"),
        ("DELETE", f"{PREFIX}/documents/{document_id}"),
    }


def test_deriving_a_thumbnail_records_the_render_route(client, room):
    created = post_document(client, room["id"]).json()
    client.post(f"{PREFIX}/documents/{created['id']}/thumbnail")

    row = audit_rows(client, collection="document", action="update")[0]
    assert row["source"] == f"POST {PREFIX}/documents/{created['id']}/thumbnail"


def test_stored_thumbnail_url_points_at_a_route_this_feature_serves(client, room):
    """The domain used to hard-code the URL, so a renamed prefix left every
    stored document pointing at nothing."""
    created = post_document(client, room["id"]).json()
    client.post(f"{PREFIX}/documents/{created['id']}/thumbnail")

    url = client.get(f"{PREFIX}/documents/{created['id']}").json()["data"]["thumbnailUrl"]
    assert url == f"{PREFIX}/documents/{created['id']}/thumbnail"
    assert client.get(url).status_code == 200


# --------------------------------------------------------------------------- #
# The port: demo data, through the real ingest path
# --------------------------------------------------------------------------- #


def test_seed_ingests_through_the_library_and_is_idempotent(tmp_path, monkeypatch):
    from dsr.features import load_feature

    feature = load_feature("wf007_library")
    monkeypatch.setenv("DSR_CONTENT_DIR", str(tmp_path / "content"))
    monkeypatch.setenv("DSR_MAX_UPLOAD_BYTES", str(1024 * 1024))

    db = AuditedDatabase(tmp_path / "seed.db")
    try:
        store = RecordStore(db)
        room_ids = [(store.create("room", {"name": f"Room {i}"})["id"], "acme") for i in range(2)]
        context = {"room_ids": room_ids, "now": None, "rng": None}

        summary = feature.seed(db, context)
        assert "3 documents ingested" in summary
        assert "2 thumbnail(s) rendered" in summary

        library = ContentLibrary(store, tmp_path / "content")
        first = library.list_documents(room_ids[0][0])
        second = library.list_documents(room_ids[1][0])
        assert len(first) == 2
        assert len(second) == 1
        # Real bytes, on disk, addressed by the same key the API serves.
        assert all(doc["data"]["ingestState"] == "complete" for doc in first)
        # Both of room 0's documents went into the "Sales Enablement" folder.
        assert {doc["data"]["libraryMaterializedPath"].count("/") for doc in first} == {2}
        # The third document is left unrendered on purpose, so the demo shows
        # the documented thumbnail lag rather than implying it.
        assert {doc["data"]["thumbnailStatus"] for doc in first} == {"ready"}
        assert [doc["data"]["thumbnailStatus"] for doc in second] == ["pending"]
        path, _, size = library.read_version(first[0]["id"])
        assert path.is_file() and size > 0

        # Running the seeder twice must not duplicate the demo page.
        assert "already holds documents" in feature.seed(db, context)
        assert len(library.list_documents(room_ids[0][0])) == 2
    finally:
        db.close()


def test_seed_with_no_rooms_says_so(tmp_path):
    from dsr.features import load_feature

    feature = load_feature("wf007_library")
    db = AuditedDatabase(tmp_path / "empty.db")
    try:
        assert "no rooms" in feature.seed(db, {"room_ids": [], "now": None, "rng": None})
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# Pure helpers
# --------------------------------------------------------------------------- #


def test_derive_format_from_filename():
    assert derive_format("Q2 Sales Deck.pptx") == "pptx"
    assert derive_format("notes.PDF") == "pdf"
    assert derive_format("no-extension") is None
    assert derive_format("") is None


def test_split_name_keeps_the_extension():
    assert split_name("Q2 Sales Deck.pptx") == ("Q2 Sales Deck", ".pptx")
    assert split_name("Q2 Sales Deck") == ("Q2 Sales Deck", "")


def test_collision_name_appends_an_index():
    assert collision_name("Deck.pptx", 1) == "Deck (1).pptx"
    # A name that already carries an index walks 1, 2, 3 rather than nesting
    # parentheses, so the folder never fills up with "Deck (1) (1)".
    assert collision_name("Deck (1).pptx", 2) == "Deck (2).pptx"
    assert collision_name("Deck", 3) == "Deck (3)"


def test_join_path_builds_a_materialized_path():
    assert join_path("", "Deck.pptx") == "/Deck.pptx"
    assert join_path("/Sales Enablement/Q2 Decks", "Q2 Sales Deck.pptx") == (
        "/Sales Enablement/Q2 Decks/Q2 Sales Deck.pptx"
    )


def test_parse_version_defaults_when_unparsable():
    assert parse_version("0.2") == (0, 2)
    assert parse_version("1") == (1, 0)
    assert parse_version("") == (0, 1)


# --------------------------------------------------------------------------- #
# Ingest
# --------------------------------------------------------------------------- #


def test_ingest_creates_a_draft_at_version_zero_point_one(client, room):
    response = post_document(client, room["id"])

    assert response.status_code == 201
    body = response.json()
    assert body["collection"] == "document"
    assert body["room_id"] == room["id"]
    data = body["data"]
    assert data["name"] == "Q2 Sales Deck.pptx"
    assert data["format"] == "pptx"
    assert data["status"] == "Draft"
    assert data["version"] == "0.1"
    assert data["type"] == "file"
    assert data["repository"] == "library"
    # Thumbnail rendering lags the upload, so the URL is empty at first.
    assert data["thumbnailUrl"] == ""
    assert data["thumbnailStatus"] == "pending"
    assert data["assignedToProfiles"] == []
    assert data["versionId"]


def test_ingest_persists_the_version_id_and_the_binary_facts(client, room):
    body = post_document(client, room["id"]).json()

    data = body["data"]
    assert data["ingestState"] == "complete"
    assert data["size"] == len(PAYLOAD)
    assert data["storage"]["checksum"] == hashlib.sha256(PAYLOAD).hexdigest()
    assert data["storage"]["algorithm"] == "sha256"
    assert [v["versionId"] for v in data["versions"]] == [data["versionId"]]


def test_ingest_derives_the_format_when_metadata_omits_it(client, room):
    response = post_document(
        client, room["id"], metadata={"name": "Q2 Deck.pptx", "parentFolderId": "root"}
    )

    assert response.status_code == 201
    data = response.json()["data"]
    assert data["format"] == "pptx"
    assert data["formatSource"] == "filename"


def test_ingest_uses_an_explicit_format_over_the_extension(client, room):
    data = post_document(
        client,
        room["id"],
        metadata={"name": "Q2 Deck", "format": "pptx", "parentFolderId": "root"},
    ).json()["data"]

    assert data["format"] == "pptx"
    assert data["formatSource"] == "metadata"


def test_ingest_stores_the_binary_outside_the_database(client, room, content_root):
    post_document(client, room["id"])

    blobs = [p for p in content_root.rglob("*") if p.is_file()]
    assert len(blobs) == 1
    assert blobs[0].read_bytes() == PAYLOAD
    # The bytes are nowhere in the database: the record carries a key, a size
    # and a checksum instead. Jev chose this layout deliberately.
    row = client.app.state.db.get(
        client.get("/api/records/document", params={"limit": 1}).json()["records"][0]["id"]
    )
    assert "PAYLOAD" not in json.dumps(row["data"])


def test_download_returns_the_stored_bytes(client, room):
    created = post_document(client, room["id"]).json()

    response = client.get(f"{PREFIX}/documents/{created['id']}/content")

    assert response.status_code == 200
    assert response.content == PAYLOAD


# --------------------------------------------------------------------------- #
# Required metadata
# --------------------------------------------------------------------------- #


def test_name_is_required(client, room):
    response = post_document(client, room["id"], metadata={"parentFolderId": "root"})

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_metadata"
    assert "name" in response.json()["detail"]


def test_format_is_required_when_it_cannot_be_derived(client, room):
    # Neither the metadata nor the upload filename carries an extension, so
    # there is nothing to derive and the ingest must be refused.
    response = post_document(
        client,
        room["id"],
        filename="untitled",
        metadata={"name": "Untitled", "parentFolderId": "root"},
    )

    assert response.status_code == 400
    assert "format" in response.json()["detail"]


def test_content_part_is_required(client, room):
    response = client.post(
        f"{PREFIX}/rooms/{room['id']}/documents",
        data={
            "metadata": json.dumps({"name": "A.pptx", "format": "pptx", "parentFolderId": "root"})
        },
    )

    assert response.status_code == 422


def test_malformed_metadata_part_is_rejected(client, room):
    response = client.post(
        f"{PREFIX}/rooms/{room['id']}/documents",
        data={"metadata": "{not json"},
        files={"content": ("A.pptx", io.BytesIO(PAYLOAD), "application/octet-stream")},
    )

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_metadata"


def test_unknown_parent_folder_is_rejected(client, room):
    response = post_document(
        client,
        room["id"],
        metadata={"name": "Deck.pptx", "format": "pptx", "parentFolderId": "folder_does_not_exist"},
    )

    assert response.status_code == 400
    assert "library folder" in response.json()["detail"]


def test_folder_from_another_room_is_rejected(client, room):
    other = client.post("/api/records/room", json={"name": "Other"}).json()
    folder = client.post(f"{PREFIX}/rooms/{other['id']}/folders", json={"name": "Q2 Decks"}).json()

    response = post_document(
        client,
        room["id"],
        metadata={"name": "Deck.pptx", "format": "pptx", "parentFolderId": folder["id"]},
    )

    assert response.status_code == 400
    assert "different room" in response.json()["detail"]


def test_ingest_into_an_unknown_room_is_404(client):
    assert post_document(client, "room_missing").status_code == 404


def test_oversized_upload_is_rejected_and_leaves_nothing_behind(client, room, content_root):
    # The fixture caps uploads at 4096 bytes.
    response = post_document(client, room["id"], content=b"x" * 5000)

    assert response.status_code == 413
    assert client.get(f"{PREFIX}/rooms/{room['id']}/documents").json()["count"] == 0
    assert not [p for p in content_root.rglob("*") if p.is_file()]


def test_empty_upload_is_rejected(client, room):
    response = post_document(client, room["id"], content=b"")

    assert response.status_code == 400
    assert client.get(f"{PREFIX}/rooms/{room['id']}/documents").json()["count"] == 0


# --------------------------------------------------------------------------- #
# Folders and materialized paths
# --------------------------------------------------------------------------- #


def test_root_keyword_addresses_the_room_root(client, room):
    data = post_document(client, room["id"]).json()["data"]

    assert data["parentFolderId"] == "root"
    assert data["libraryMaterializedPath"] == "/Q2 Sales Deck.pptx"


def test_folders_build_a_materialized_path(client, room):
    parent = client.post(
        f"{PREFIX}/rooms/{room['id']}/folders", json={"name": "Sales Enablement"}
    ).json()
    child = client.post(
        f"{PREFIX}/rooms/{room['id']}/folders",
        json={"name": "Q2 Decks", "parentFolderId": parent["id"]},
    ).json()
    assert child["data"]["path"] == "/Sales Enablement/Q2 Decks"

    document = post_document(
        client,
        room["id"],
        metadata={
            "name": "Q2 Sales Deck.pptx",
            "format": "pptx",
            "parentFolderId": child["id"],
        },
    ).json()["data"]

    assert document["libraryMaterializedPath"] == "/Sales Enablement/Q2 Decks/Q2 Sales Deck.pptx"


def test_folder_listing_is_room_scoped(client, room):
    other = client.post("/api/records/room", json={"name": "Other"}).json()
    client.post(f"{PREFIX}/rooms/{room['id']}/folders", json={"name": "Q2 Decks"})
    client.post(f"{PREFIX}/rooms/{other['id']}/folders", json={"name": "Elsewhere"})

    listed = client.get(f"{PREFIX}/rooms/{room['id']}/folders").json()

    assert [f["data"]["name"] for f in listed["folders"]] == ["Q2 Decks"]


def test_documents_can_be_listed_per_folder(client, room):
    folder = client.post(f"{PREFIX}/rooms/{room['id']}/folders", json={"name": "Q2 Decks"}).json()
    in_folder = post_document(
        client,
        room["id"],
        metadata={"name": "A.pptx", "format": "pptx", "parentFolderId": folder["id"]},
    ).json()
    at_root = post_document(client, room["id"]).json()

    listed = client.get(
        f"{PREFIX}/rooms/{room['id']}/documents", params={"parentFolderId": folder["id"]}
    ).json()

    assert [d["id"] for d in listed["documents"]] == [in_folder["id"]]
    assert at_root["id"] not in [d["id"] for d in listed["documents"]]


# --------------------------------------------------------------------------- #
# resolveNameCollision
# --------------------------------------------------------------------------- #


def test_name_collision_appends_an_index(client, room):
    first = post_document(client, room["id"]).json()
    second = post_document(client, room["id"]).json()
    third = post_document(client, room["id"]).json()

    assert first["data"]["name"] == "Q2 Sales Deck.pptx"
    assert second["data"]["name"] == "Q2 Sales Deck (1).pptx"
    assert second["data"]["requestedName"] == "Q2 Sales Deck.pptx"
    assert third["data"]["name"] == "Q2 Sales Deck (2).pptx"


def test_collision_resolution_can_be_turned_off(client, room):
    post_document(client, room["id"])

    response = post_document(client, room["id"], data={"resolveNameCollision": "false"})

    assert response.status_code == 409
    assert response.json()["error"] == "conflict"
    assert client.get(f"{PREFIX}/rooms/{room['id']}/documents").json()["count"] == 1


def test_collision_is_scoped_to_the_target_folder(client, room):
    folder = client.post(f"{PREFIX}/rooms/{room['id']}/folders", json={"name": "Q2 Decks"}).json()
    at_root = post_document(client, room["id"]).json()
    in_folder = post_document(
        client,
        room["id"],
        metadata={"name": "Q2 Sales Deck.pptx", "format": "pptx", "parentFolderId": folder["id"]},
    ).json()

    # Same name, different folder: no de-collision, because nothing conflicts.
    assert at_root["data"]["name"] == "Q2 Sales Deck.pptx"
    assert in_folder["data"]["name"] == "Q2 Sales Deck.pptx"
    assert in_folder["data"].get("requestedName") is None


def test_collision_is_scoped_to_the_room(client, room):
    other = client.post("/api/records/room", json={"name": "Other"}).json()
    post_document(client, room["id"])
    in_other = post_document(client, other["id"])

    assert in_other.json()["data"]["name"] == "Q2 Sales Deck.pptx"


# --------------------------------------------------------------------------- #
# rollbackOnError
# --------------------------------------------------------------------------- #


@pytest.fixture()
def library(tmp_path):
    """A domain-level library with a private database and content root."""
    db = AuditedDatabase(tmp_path / "library.db")
    store = RecordStore(db)
    lib = ContentLibrary(store, tmp_path / "content", max_upload_bytes=4096, chunk_bytes=16)
    lib._test_room_id = store.create("room", {"name": "Acme"})["id"]  # noqa: SLF001 - fixture wiring
    yield lib
    db.close()


def test_a_failed_binary_leaves_no_orphaned_draft(library, monkeypatch):
    """rollbackOnError=true: the folder shows no entry for a failed upload."""

    def refuse(key, content):
        raise OSError("disk is full")

    monkeypatch.setattr(library, "_write_blob", refuse)

    with pytest.raises(Exception) as caught:
        library.ingest(
            library._test_room_id,  # noqa: SLF001
            io.BytesIO(PAYLOAD),
            "Deck.pptx",
            metadata={"name": "Deck.pptx", "format": "pptx", "parentFolderId": "root"},
        )

    assert "rolled back" in str(caught.value)
    assert library.list_documents(library._test_room_id) == []  # noqa: SLF001
    # The removal is audited like any other delete, so the trail shows both the
    # draft and its rollback rather than a gap. Newest first.
    actions = [e["action"] for e in library.store.audit(collection="document")]
    assert actions == ["delete", "insert"]


def test_a_failed_binary_keeps_a_retryable_draft_when_rollback_is_off(library, monkeypatch):
    """rollbackOnError=false: the draft survives, marked failed, for a human."""
    monkeypatch.setattr(
        library, "_write_blob", lambda key, content: (_ for _ in ()).throw(OSError("no space"))
    )

    with pytest.raises(Exception) as caught:
        library.ingest(
            library._test_room_id,  # noqa: SLF001
            io.BytesIO(PAYLOAD),
            "Deck.pptx",
            metadata={"name": "Deck.pptx", "format": "pptx", "parentFolderId": "root"},
            rollback_on_error=False,
        )

    assert "retained" in str(caught.value)
    documents = library.list_documents(library._test_room_id)  # noqa: SLF001
    assert len(documents) == 1
    assert documents[0]["data"]["ingestState"] == "failed"
    assert "no space" in documents[0]["data"]["ingestError"]


def test_a_partial_blob_is_never_left_on_disk(library):
    """Even the failed path removes the bytes it managed to write."""
    good = library._write_blob  # noqa: SLF001
    calls = {"n": 0}

    def fail_midway(key, content):
        calls["n"] += 1
        if calls["n"] == 1:
            library.content_root.mkdir(parents=True, exist_ok=True)
            (library.content_root / key).parent.mkdir(parents=True, exist_ok=True)
            (library.content_root / key).write_bytes(b"partial")
            raise OSError("interrupted")
        return good(key, content)

    library._write_blob = fail_midway  # noqa: SLF001
    # BlobStorageError, not OSError: the ingest path settles a failed write and
    # re-raises with the draft's fate in the message, chaining the OSError.
    with pytest.raises(BlobStorageError):
        library.ingest(
            library._test_room_id,  # noqa: SLF001
            io.BytesIO(PAYLOAD),
            "Deck.pptx",
            metadata={"name": "Deck.pptx", "format": "pptx", "parentFolderId": "root"},
        )

    assert list(library.content_root.rglob("*.pptx")) == []


def test_version_cannot_be_added_to_an_incomplete_document(library, monkeypatch):
    monkeypatch.setattr(
        library, "_write_blob", lambda key, content: (_ for _ in ()).throw(OSError("no space"))
    )
    with pytest.raises(BlobStorageError):
        library.ingest(
            library._test_room_id,  # noqa: SLF001
            io.BytesIO(PAYLOAD),
            "Deck.pptx",
            metadata={"name": "Deck.pptx", "format": "pptx", "parentFolderId": "root"},
            rollback_on_error=False,
        )
    document = library.list_documents(library._test_room_id)[0]  # noqa: SLF001

    with pytest.raises(Exception) as caught:
        library.add_version(document["id"], io.BytesIO(PAYLOAD_V2), "Deck.pptx")

    assert "not complete" in str(caught.value)


# --------------------------------------------------------------------------- #
# New versions
# --------------------------------------------------------------------------- #


def test_put_adds_a_version_and_keeps_the_old_bytes(client, room):
    created = post_document(client, room["id"]).json()
    first_version_id = created["data"]["versionId"]

    response = client.put(
        f"{PREFIX}/documents/{created['id']}",
        files={
            "content": ("Q2 Sales Deck.pptx", io.BytesIO(PAYLOAD_V2), "application/octet-stream")
        },
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["version"] == "0.2"
    assert data["minorVersion"] == 2
    assert data["versionId"] != first_version_id
    assert len(data["versions"]) == 2
    # The current bytes are the new ones...
    assert client.get(f"{PREFIX}/documents/{created['id']}/content").content == PAYLOAD_V2
    # ...and the previous version is still retrievable, which is the whole point
    # of an add-version operation rather than a replacement.
    assert (
        client.get(
            f"{PREFIX}/documents/{created['id']}/content", params={"versionId": first_version_id}
        ).content
        == PAYLOAD
    )


def test_adding_a_version_is_audited_as_a_put(client, room):
    created = post_document(client, room["id"]).json()
    client.put(
        f"{PREFIX}/documents/{created['id']}",
        files={
            "content": ("Q2 Sales Deck.pptx", io.BytesIO(PAYLOAD_V2), "application/octet-stream")
        },
    )

    row = audit_rows(client, collection="document", action="update")[0]
    assert row["source"] == f"PUT {PREFIX}/documents/{created['id']}"


def test_a_new_version_invalidates_the_thumbnail(client, room):
    created = post_document(client, room["id"]).json()
    client.post(f"{PREFIX}/documents/{created['id']}/thumbnail")

    assert (
        client.get(f"{PREFIX}/documents/{created['id']}").json()["data"]["thumbnailStatus"]
        == "ready"
    )

    client.put(
        f"{PREFIX}/documents/{created['id']}",
        files={
            "content": ("Q2 Sales Deck.pptx", io.BytesIO(PAYLOAD_V2), "application/octet-stream")
        },
    )

    data = client.get(f"{PREFIX}/documents/{created['id']}").json()["data"]
    assert data["thumbnailStatus"] == "pending"
    assert data["thumbnailUrl"] == ""


def test_version_on_an_unknown_document_is_404(client):
    response = client.put(
        f"{PREFIX}/documents/document_missing",
        files={"content": ("A.pptx", io.BytesIO(PAYLOAD), "application/octet-stream")},
    )
    assert response.status_code == 404


# --------------------------------------------------------------------------- #
# Thumbnails
# --------------------------------------------------------------------------- #


def test_thumbnail_is_pending_until_derived(client, room):
    created = post_document(client, room["id"]).json()

    # Polling a pending thumbnail is a 404, which is the documented lag. It is
    # also a pure read: no audit row, no state change.
    assert client.get(f"{PREFIX}/documents/{created['id']}/thumbnail").status_code == 404
    assert len(audit_rows(client, collection="document")) == 2

    client.post(f"{PREFIX}/documents/{created['id']}/thumbnail")

    assert client.get(f"{PREFIX}/documents/{created['id']}/thumbnail").status_code == 200
    derived = client.get(f"{PREFIX}/documents/{created['id']}").json()["data"]
    assert derived["thumbnailStatus"] == "ready"
    assert derived["thumbnailUrl"] == f"{PREFIX}/documents/{created['id']}/thumbnail"
    assert derived["thumbnailGeneratedAt"]


def test_polling_a_pending_thumbnail_does_not_audit(client, room):
    """A read that wrote would be a mutation, and the log must not hide that."""
    created = post_document(client, room["id"]).json()
    before = len(audit_rows(client, collection="document"))

    for _ in range(3):
        client.get(f"{PREFIX}/documents/{created['id']}/thumbnail")

    assert len(audit_rows(client, collection="document")) == before


def test_thumbnail_markup_escapes_the_document_name(client, room):
    created = post_document(
        client,
        room["id"],
        metadata={
            "name": "<script>alert(1)</script>.pptx",
            "format": "pptx",
            "parentFolderId": "root",
        },
    ).json()
    client.post(f"{PREFIX}/documents/{created['id']}/thumbnail")

    markup = client.get(f"{PREFIX}/documents/{created['id']}/thumbnail").text

    assert "<script>" not in markup
    assert "&lt;script&gt;" in markup


def test_thumbnail_of_an_unknown_document_is_404(client):
    assert client.get(f"{PREFIX}/documents/document_missing/thumbnail").status_code == 404


# --------------------------------------------------------------------------- #
# Schema flexibility
# --------------------------------------------------------------------------- #


def test_arbitrary_metadata_is_stored_and_indexed(client, room):
    created = post_document(
        client,
        room["id"],
        metadata={
            "name": "Q2 Deck.pptx",
            "format": "pptx",
            "parentFolderId": "root",
            "campaignTag": "FY26-q2",
            "reviewBoard": {"chair": "dana", "quorum": 4},
        },
    ).json()

    assert created["data"]["campaignTag"] == "FY26-q2"
    assert created["data"]["reviewBoard"] == {"chair": "dana", "quorum": 4}
    # A filter on a field this code has never heard of resolves through the
    # dynamic index, dotted path and all.
    listed = client.get(
        f"{PREFIX}/rooms/{room['id']}/documents",
        params={"where": json.dumps({"reviewBoard.chair": "dana"})},
    ).json()
    assert [d["id"] for d in listed["documents"]] == [created["id"]]


def test_properties_accept_the_documented_array_form(client, room):
    created = post_document(
        client,
        room["id"],
        metadata={
            "name": "Q2 Deck.pptx",
            "format": "pptx",
            "parentFolderId": "root",
            "properties": [
                {"id": "audience", "value": "enterprise"},
                {"id": "region", "value": "emea"},
            ],
        },
    ).json()

    assert created["data"]["properties"] == {"audience": "enterprise", "region": "emea"}
    listed = client.get(
        f"{PREFIX}/rooms/{room['id']}/documents",
        params={"where": json.dumps({"properties.audience": "enterprise"})},
    ).json()
    assert listed["count"] == 1


def test_experts_are_validated_and_stored(client, room):
    created = post_document(
        client,
        room["id"],
        metadata={
            "name": "Q2 Deck.pptx",
            "format": "pptx",
            "parentFolderId": "root",
            "experts": [{"type": "group", "id": "solutions-engineering"}],
        },
    ).json()
    assert created["data"]["experts"] == [{"type": "group", "id": "solutions-engineering"}]

    bad = post_document(
        client,
        room["id"],
        metadata={
            "name": "Other.pptx",
            "format": "pptx",
            "parentFolderId": "root",
            "experts": [{"type": "robot", "id": "r2"}],
        },
    )
    assert bad.status_code == 400
    assert "expert type" in bad.json()["detail"]


def test_optional_correlation_fields_are_stored(client, room):
    created = post_document(
        client,
        room["id"],
        metadata={
            "name": "Q2 Deck.pptx",
            "format": "pptx",
            "parentFolderId": "root",
            "externalId": "ext-12345",
            "expiresAt": "2026-12-31",
            "ownerId": "user-42",
            "description": "The deck we walk the buyer through.",
            "language": "en-GB",
        },
    ).json()

    data = created["data"]
    assert data["externalId"] == "ext-12345"
    assert data["expiresAt"] == "2026-12-31"
    assert data["ownerId"] == "user-42"
    assert data["language"] == "en-GB"
    listed = client.get(
        f"{PREFIX}/rooms/{room['id']}/documents", params={"where": "externalId=ext-12345"}
    ).json()
    assert listed["count"] == 1


def test_metadata_cannot_forge_the_librarys_own_bookkeeping(client, room):
    """Open payloads must not let a caller rewrite what the library tracks.

    Schema flexibility means a team can add a field. It does not mean a caller
    gets to mark its own upload Published, or point ``storage.key`` at bytes that
    are not there, by putting those keys in the metadata part.
    """
    created = post_document(
        client,
        room["id"],
        metadata={
            "name": "Q2 Deck.pptx",
            "format": "pptx",
            "parentFolderId": "root",
            "status": "Published",
            "version": "9.9",
            "ingestState": "complete",
            "storage": {"key": "../../etc/passwd", "bytes": 1, "checksum": "forged"},
            "thumbnailStatus": "ready",
        },
    ).json()

    data = created["data"]
    assert data["status"] == "Draft"
    assert data["version"] == "0.1"
    assert data["thumbnailStatus"] == "pending"
    assert data["storage"]["key"] != "../../etc/passwd"
    # The real bytes are still the ones that get served.
    assert client.get(f"{PREFIX}/documents/{created['id']}/content").content == PAYLOAD


def test_a_field_this_code_never_declared_needs_no_migration(client, room):
    """The requirement, stated as a test: a team adds a field and it just works."""
    created = post_document(
        client,
        room["id"],
        metadata={
            "name": "Q2 Deck.pptx",
            "format": "pptx",
            "parentFolderId": "root",
            "wf011EscalationPolicy": {"reviewer": "sam", "slaHours": 48},
        },
    ).json()

    assert created["data"]["wf011EscalationPolicy"] == {"reviewer": "sam", "slaHours": 48}
    assert client.get("/api/collections").json()["collections"]  # no migration added a table


# --------------------------------------------------------------------------- #
# Audit and removal
# --------------------------------------------------------------------------- #


def test_ingest_is_audited_as_two_mutations(client, room):
    post_document(client, room["id"])

    entries = audit_rows(client, collection="document")

    # Create the draft, then complete it. Two mutations, two rows: the log
    # describes what happened rather than how many round trips it took.
    assert [e["action"] for e in entries] == ["update", "insert"]
    assert entries[-1]["after_state"]["name"] == "Q2 Sales Deck.pptx"
    assert entries[0]["diff"]["ingestState"] == {"from": "pending", "to": "complete"}


def test_ingest_records_the_actor_and_source(client, room):
    post_document(client, room["id"])

    # The route that caused each row is recorded, which is what makes the trail
    # navigable when a reviewer asks where a document came from.
    entries = audit_rows(client, collection="document")
    assert [e["action"] for e in entries] == ["update", "insert"]
    assert all(e["source"] == f"POST {PREFIX}/rooms/{room['id']}/documents" for e in entries)
    assert all(e["actor"] == "api" for e in entries)


def test_delete_hides_the_document_and_keeps_the_bytes(client, room, content_root):
    created = post_document(client, room["id"]).json()

    response = client.delete(f"{PREFIX}/documents/{created['id']}")

    assert response.status_code == 200
    assert client.get(f"{PREFIX}/documents/{created['id']}").status_code == 404
    assert client.get(f"{PREFIX}/rooms/{room['id']}/documents").json()["count"] == 0
    # Bytes are retained: the audit log still references this document.
    assert len([p for p in content_root.rglob("*") if p.is_file()]) == 1


def test_delete_of_a_rendered_document_revokes_the_preview_under_the_delete_route(client, room):
    """Revoking the preview is a second audited write, and it must not be
    recorded as though the domain had invented its own label."""
    created = post_document(client, room["id"]).json()
    client.post(f"{PREFIX}/documents/{created['id']}/thumbnail")

    client.delete(f"{PREFIX}/documents/{created['id']}")

    sources = [row["source"] for row in audit_rows(client, collection="document")]
    assert sources[0] == f"DELETE {PREFIX}/documents/{created['id']}"
    assert sources[1] == f"DELETE {PREFIX}/documents/{created['id']}"


def test_usage_reports_the_library_totals(client, room):
    post_document(client, room["id"])
    client.post(f"{PREFIX}/rooms/{room['id']}/folders", json={"name": "Q2 Decks"})

    body = client.get(f"{PREFIX}/rooms/{room['id']}/usage").json()

    assert body["documents"] == 1
    assert body["folders"] == 1
    assert body["bytes"] == len(PAYLOAD)
    assert body["pending_thumbnails"] == 1
    assert body["failed"] == 0
    assert body["max_upload_bytes"] == 4096


# --------------------------------------------------------------------------- #
# Domain-level validation without HTTP
# --------------------------------------------------------------------------- #


def test_parse_metadata_part_rejects_non_objects():
    from dsr.library import parse_metadata_part

    with pytest.raises(ValidationError):
        parse_metadata_part("[1, 2, 3]")
    with pytest.raises(ValidationError):
        parse_metadata_part("   ")


def test_a_bad_where_clause_is_a_400_not_a_500(client, room):
    """A filter the query language cannot parse is the caller's mistake, and the
    LibraryError handler is what turns it into a 400 rather than a traceback."""
    response = client.get(
        f"{PREFIX}/rooms/{room['id']}/documents", params={"where": '{"unclosed": '}
    )
    assert response.status_code == 400
    assert response.json()["error"] == "library_error"
