"""The wire contract for WF-008: the external content library over HTTP.

The other WF-008 suite, ``test_wf008.py``, covers the service and the port
itself. This one covers the seam between them, which is where the port actually
had work to do: the branch registered its routes on the shared ``app`` from a
module that ``dsr/api.py`` had to import, and this feature is now a router the
host mounts by discovery. What has to stay true either way is the contract other
teams build against -- the researched request field names, the ``contentId`` to
persist, the error payload carrying a remedy and a correlation id, and the
promise that a schema-flexible ``metadata`` object needs no migration.

These live in their own file rather than alongside the service tests because
their fixtures build data through a different path onto a different store: a
``room`` here is a record in a running app, and a ``room`` there is a record in
a database the test opened. Sharing the name across one module would let one
silently answer for the other.
"""

from __future__ import annotations

import tempfile
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dsr.api import app
from dsr.features import load_feature
from dsr.external_library.sources import GOOGLE_DRIVE, SAMPLE_FILE_ID, STALE_FILE_ID

SOURCE = GOOGLE_DRIVE
FILE_ID = SAMPLE_FILE_ID
OTHER_FILE_ID = STALE_FILE_ID

#: The audit sources the routes must produce. They are built from
#: ``router.prefix``; spelling them out here is how a test notices if that stops
#: being true, and it is the defect the branch shipped.
ADD_SOURCE = "POST /api/library/external"
RESYNC_SOURCE = "POST /api/library/external/resync"


@pytest.fixture()
def client(monkeypatch):
    """A client on a throwaway database, the way ``test_features.py`` builds one.

    A fresh store per test is also what makes the rate limit fresh: the service is
    cached per store precisely because the documented limit is a property of the
    process, so a new store is a new allowance.
    """
    tmp = tempfile.TemporaryDirectory()
    monkeypatch.setenv("DSR_DB_PATH", str(Path(tmp.name) / "wf008.db"))
    monkeypatch.setenv("DSR_AUDIT_DIR", str(Path(tmp.name) / "audit"))
    monkeypatch.setattr("dsr.api.FRONTEND_DIST", Path(tmp.name) / "absent-frontend")
    with TestClient(app) as test_client:
        yield test_client
    tmp.cleanup()


@pytest.fixture()
def room(client):
    """A room created over HTTP, so it exists in the app's own store."""
    return client.post("/api/records/room", json={"name": "Northwind", "stage": "evaluation"}).json()


@pytest.fixture()
def connected(client):
    """A configured connection, created through the generic records API.

    Deliberately not a dedicated endpoint: a connection is an ordinary
    schema-flexible record, and this is what a team would actually do. It is also
    what the page does.
    """
    return client.post(
        "/api/records/external_connection",
        json={
            "source": SOURCE,
            "name": "Dana's Google Drive",
            "account": "dana@northwind.example",
            "status": "connected",
            "scopes": ["drive.readonly"],
        },
    ).json()


def add_over_http(client, room, actor="dana", **body):
    payload = {
        "externalSource": SOURCE,
        "externalContentId": FILE_ID,
        "roomId": room["id"],
        **body,
    }
    return client.post("/api/library/external", json=payload, params={"actor": actor})


# -- discovery -------------------------------------------------------------- #


def test_sources_route_reports_the_registry(client):
    body = client.get("/api/library/sources").json()

    # The research says only GoogleDrive is supported "at this time"; the route
    # reports the registry rather than hard-coding that sentence.
    assert body["count"] == 1
    assert body["sources"][0]["name"] == SOURCE
    assert body["sources"][0]["connection_required"] is True
    assert body["sources"][0]["connected"] is False


def test_sources_route_flips_to_connected_once_configured(client, connected):
    body = client.get("/api/library/sources").json()

    assert body["sources"][0]["connected"] is True
    assert body["sources"][0]["connections"] == 1


def test_connections_route_exposes_lineage(client, connected):
    body = client.get("/api/library/connections").json()

    assert body["count"] == 1
    entry = body["connections"][0]
    assert entry["external_connection_id"] == connected["id"]
    assert entry["external_system_connection_name"] == "Dana's Google Drive"
    assert entry["external_system_connection_mapping"]["source"] == SOURCE


def test_folder_route_offers_the_root_keyword(client, room):
    # The generic records route scopes with `room_id`; the library routes use
    # `roomId` to match the researched request vocabulary.
    client.post("/api/records/library_folder", json={"name": "Q2 Decks"}, params={"room_id": room["id"]})

    body = client.get("/api/library/folders", params={"roomId": room["id"]}).json()

    assert [f["id"] for f in body["folders"]][0] == "root"
    assert [f["name"] for f in body["folders"]] == ["root", "Q2 Decks"]


# -- the add operation ------------------------------------------------------ #


def test_add_returns_201_with_a_content_id(client, room, connected):
    response = add_over_http(client, room)

    assert response.status_code == 201
    body = response.json()
    assert body["contentId"].startswith("document_")
    assert body["created"] is True
    assert body["record"]["room_id"] == room["id"]


def test_add_stores_the_researched_fields(client, room, connected):
    source = add_over_http(client, room, autoSync=True).json()["record"]["data"]["source"]

    assert source["external_source"] == SOURCE
    assert source["external_content_id"] == FILE_ID
    assert source["parent_folder_id"] == "root"
    assert source["auto_sync"] is True
    assert source["linkage"] == "linked"


def test_omitted_auto_sync_creates_a_snapshot(client, room, connected):
    body = add_over_http(client, room).json()

    assert body["status"]["linkage"] == "snapshot"
    assert body["record"]["data"]["source"]["auto_sync"] is False


def test_the_content_id_resolves_through_the_generic_api(client, room, connected):
    content_id = add_over_http(client, room).json()["contentId"]

    fetched = client.get(f"/api/records/document/{content_id}")

    assert fetched.status_code == 200
    assert fetched.json()["data"]["source"]["content_id"] == content_id


def test_relinking_returns_200_and_creates_nothing(client, room, connected):
    first = add_over_http(client, room).json()

    # Straight back to back, with no pause: a re-link never reaches the source,
    # so it is not throttled and a double-click stays harmless.
    second = add_over_http(client, room)

    assert second.status_code == 200
    assert second.json()["created"] is False
    assert second.json()["contentId"] == first["contentId"]
    assert client.get("/api/records/document").json()["count"] == 1


def test_a_folder_in_the_room_is_accepted(client, room, connected):
    folder = client.post(
        "/api/records/library_folder", json={"name": "Decks"}, params={"room_id": room["id"]}
    ).json()

    body = add_over_http(client, room, parentFolderId=folder["id"]).json()

    assert body["record"]["data"]["source"]["parent_folder_id"] == folder["id"]


def test_metadata_is_stored_and_queryable(client, room, connected):
    add_over_http(client, room, metadata={"review_owner": "sam", "campaign": {"tier": "enterprise"}})

    found = client.get(
        "/api/records/document", params={"where": '{"campaign.tier":"enterprise"}'}
    ).json()

    assert found["count"] == 1
    assert found["records"][0]["data"]["review_owner"] == "sam"


def test_add_is_audited_with_the_route_that_served_it(client, room, connected):
    content_id = add_over_http(client, room).json()["contentId"]

    entries = client.get("/api/audit", params={"record_id": content_id}).json()["entries"]

    assert len(entries) == 1
    assert entries[0]["action"] == "insert"
    assert entries[0]["actor"] == "dana"
    assert entries[0]["source"] == ADD_SOURCE


# -- failures carry a remedy and a correlation id --------------------------- #


def test_missing_connection_is_404_with_a_remedy(client, room):
    response = add_over_http(client, room)

    assert response.status_code == 404
    body = response.json()
    assert body["error"] == "ExternalConnectionNotFound"
    assert "Connect" in body["remediation"]
    assert body["correlation_id"].startswith("corr_")


def test_the_message_a_client_reads_carries_the_remedy_and_the_correlation_id(client, room):
    """The shared ``apiRequest`` keeps only ``detail``; nothing may be lost in it.

    This is the reason the error handler composes its sentence rather than
    leaving ``detail`` as a bare message: the remedy and the correlation id are
    the only parts of a failure that tell the user what to do next, and a client
    that can only read ``detail`` would otherwise drop both.
    """
    body = add_over_http(client, room).json()

    assert body["remediation"] in body["detail"]
    assert body["correlation_id"] in body["detail"]


def test_unknown_file_is_404(client, room, connected):
    body = add_over_http(client, room, externalContentId="1Nope").json()

    assert body["error"] == "ExternalContentNotFound"
    assert body["correlation_id"]


def test_unknown_folder_is_404(client, room, connected):
    body = add_over_http(client, room, parentFolderId="library_folder_nope").json()

    assert body["error"] == "FolderNotFound"
    assert body["parent_folder_id"] == "library_folder_nope"


def test_unknown_room_is_404(client, connected):
    body = client.post(
        "/api/library/external",
        json={"externalSource": SOURCE, "externalContentId": FILE_ID, "roomId": "room_nope"},
    ).json()

    assert body["error"] == "RoomNotFound"


def test_unsupported_source_is_400_and_lists_the_supported_ones(client, room, connected):
    body = add_over_http(client, room, externalSource="Dropbox").json()

    assert body["error"] == "InvalidParameter"
    assert body["supported_sources"] == [SOURCE]


def test_every_error_carries_a_distinct_correlation_id(client, room):
    first = add_over_http(client, room).json()["correlation_id"]
    second = add_over_http(client, room).json()["correlation_id"]

    assert first != second


def test_a_failed_add_writes_nothing(client, room):
    before = client.get("/api/stats").json()["audit_entries"]

    add_over_http(client, room)  # fails: no connection

    assert client.get("/api/stats").json()["audit_entries"] == before


# -- request validation ----------------------------------------------------- #


def test_missing_required_field_is_400(client, room, connected):
    response = client.post(
        "/api/library/external", json={"externalSource": SOURCE, "roomId": room["id"]}
    )

    assert response.status_code == 400
    assert "externalContentId" in response.json()["detail"]


def test_unknown_top_level_field_is_refused_with_advice(client, room, connected):
    response = add_over_http(client, room, autoSyn=True)

    assert response.status_code == 400
    # The message points at metadata rather than silently storing a typo.
    assert "metadata" in response.json()["detail"]


def test_non_boolean_auto_sync_is_400(client, room, connected):
    assert add_over_http(client, room, autoSync="yes").status_code == 400


def test_non_object_metadata_is_400(client, room, connected):
    assert add_over_http(client, room, metadata="nope").status_code == 400


# -- the rate limit --------------------------------------------------------- #


def test_a_rapid_second_add_is_429_with_a_retry_hint(client, room, connected):
    assert add_over_http(client, room).status_code == 201

    response = add_over_http(client, room, externalContentId=OTHER_FILE_ID)

    assert response.status_code == 429
    body = response.json()
    assert body["error"] == "RateLimitExceeded"
    assert 0 < body["retry_after_seconds"] <= 1
    assert "per token" in body["remediation"]


def test_a_local_failure_does_not_spend_the_allowance(client, room):
    # No connection, so both calls fail on the local prerequisite. The
    # allowance is for the call to the source, and neither call reached it.
    assert add_over_http(client, room).json()["error"] == "ExternalConnectionNotFound"
    assert add_over_http(client, room).json()["error"] == "ExternalConnectionNotFound"


def test_the_limit_applies_per_token(client, room, connected):
    assert add_over_http(client, room, actor="dana").status_code == 201

    other = add_over_http(client, room, actor="sam", externalContentId=OTHER_FILE_ID)

    assert other.status_code == 201


def test_the_limit_expires(client, room, connected):
    assert add_over_http(client, room, actor="dana").status_code == 201
    time.sleep(1.05)

    assert add_over_http(client, room, actor="dana", externalContentId=OTHER_FILE_ID).status_code == 201


# -- read models ------------------------------------------------------------ #


def test_list_route_returns_items_and_statuses(client, room, connected):
    content_id = add_over_http(client, room, autoSync=True).json()["contentId"]

    body = client.get("/api/library/external", params={"roomId": room["id"]}).json()

    assert body["count"] == 1
    assert body["items"][0]["id"] == content_id
    assert body["statuses"][0]["state"] == "in_sync"


def test_list_route_excludes_documents_that_are_not_external(client, room, connected):
    client.post("/api/records/document", json={"title": "Uploaded deck"}, params={"room_id": room["id"]})
    add_over_http(client, room)

    body = client.get("/api/library/external").json()

    assert body["count"] == 1


def test_list_route_filters_on_any_json_path(client, room, connected):
    add_over_http(client, room, externalContentId=FILE_ID, autoSync=True, actor="dana")
    add_over_http(client, room, externalContentId=OTHER_FILE_ID, autoSync=False, actor="sam")

    live = client.get("/api/library/external", params={"where": '{"source.auto_sync":true}'}).json()

    assert live["count"] == 1
    assert live["items"][0]["data"]["source"]["auto_sync"] is True


def test_list_route_rejects_malformed_where(client):
    assert client.get("/api/library/external", params={"where": "{not json"}).status_code == 400


def test_list_route_refuses_to_filter_away_externals(client):
    response = client.get("/api/library/external", params={"where": '{"source.kind":"local"}'})

    assert response.status_code == 400


def test_get_one_item(client, room, connected):
    content_id = add_over_http(client, room).json()["contentId"]

    body = client.get(f"/api/library/external/{content_id}").json()

    assert body["record"]["id"] == content_id
    assert body["status"]["linkage"] == "snapshot"


def test_get_one_item_that_does_not_exist_is_404(client):
    response = client.get("/api/library/external/document_nope")

    assert response.status_code == 404
    assert response.json()["error"] == "ExternalContentNotFound"


def test_sync_status_route(client, room, connected):
    content_id = add_over_http(client, room, autoSync=True).json()["contentId"]

    body = client.get(f"/api/library/external/{content_id}/sync-status").json()

    assert body["content_id"] == content_id
    assert body["external_source"] == SOURCE
    assert body["external_content_id"] == FILE_ID
    assert body["state"] == "in_sync"
    assert body["auto_sync"] is True


def test_sync_status_of_a_snapshot_explains_itself(client, room, connected):
    content_id = add_over_http(client, room, autoSync=False).json()["contentId"]

    body = client.get(f"/api/library/external/{content_id}/sync-status").json()

    assert "one-time snapshot" in body["reason"]


def test_sync_status_of_an_unknown_content_id_is_404(client):
    response = client.get("/api/library/external/document_nope/sync-status")

    assert response.status_code == 404
    assert response.json()["error"] == "ExternalContentNotFound"


# -- the automation --------------------------------------------------------- #


def test_resync_route_reports_what_it_did(client, room, connected):
    add_over_http(client, room, autoSync=True)
    # The source has not moved, so the pass has nothing to apply.
    body = client.post("/api/library/external/resync", json={}, params={"actor": "sync-worker"}).json()

    assert body["checked"] == 1
    assert body["updated"] == []
    assert body["counts"] == {"updated": 0, "in_sync": 1, "skipped": 0, "failed": 0}


def test_resync_route_applies_drift_and_audits_it_under_its_own_route(client, room, connected):
    content_id = add_over_http(client, room, autoSync=True).json()["contentId"]
    # The source has not moved, so nothing is written and nothing is audited.
    client.post("/api/library/external/resync", json={})
    before = client.get("/api/stats").json()["audit_entries"]

    # Simulate the upstream edit the next real add would see. A first add
    # records the version the source reports now, so to have drift the item must
    # hold an older one: roll it back through the audited store, which is what
    # the seeder does and what an edit between two passes really looks like.
    record = client.get(f"/api/records/document/{content_id}").json()
    client.patch(
        f"/api/records/document/{content_id}",
        json={"source": {**record["data"]["source"], "source_version": "rev-1"}},
    )

    body = client.post("/api/library/external/resync", json={}, params={"actor": "sync-worker"}).json()

    assert body["counts"]["updated"] == 1
    entries = client.get("/api/audit", params={"record_id": content_id}).json()["entries"]
    assert entries[0]["source"] == RESYNC_SOURCE
    assert entries[0]["actor"] == "sync-worker"
    # The unchanged pass wrote nothing, so the counts line up: one PATCH and one
    # re-sync update since `before`, and the untouched pass added none.
    assert client.get("/api/stats").json()["audit_entries"] == before + 2


def test_resync_route_skips_snapshots(client, room, connected):
    content_id = add_over_http(client, room, autoSync=False).json()["contentId"]

    body = client.post("/api/library/external/resync", json={}).json()

    assert body["skipped"][0]["content_id"] == content_id
    assert "snapshot" in body["skipped"][0]["reason"]


def test_resync_route_scopes_to_a_room(client, room, connected):
    other = client.post("/api/records/room", json={"name": "Contoso"}).json()
    mine = add_over_http(client, room, actor="dana", autoSync=True).json()["contentId"]
    theirs = add_over_http(
        client,
        room,
        actor="sam",
        externalContentId=OTHER_FILE_ID,
        roomId=other["id"],
        autoSync=True,
    ).json()["contentId"]

    body = client.post("/api/library/external/resync", json={"roomId": other["id"]}).json()

    assert body["checked"] == 1
    assert [entry["content_id"] for entry in body["in_sync"]] == [theirs]
    assert mine not in [entry["content_id"] for entry in body["in_sync"]]


def test_the_resync_route_does_not_shadow_the_item_route(client, room, connected):
    """``/external/resync`` and ``/external/{content_id}`` share a prefix.

    They differ by method here, so a GET cannot reach the pass, but the
    declaration order still has to put the literal path first in case a future
    verb makes them overlap.
    """
    paths = [route.path for route in load_feature("wf008_external_sync").router.routes]

    assert paths.index("/api/library/external/resync") < paths.index(
        "/api/library/external/{content_id}"
    )


def test_an_unchanged_resync_produces_no_audit_rows(client, room, connected):
    add_over_http(client, room, autoSync=True)
    before = client.get("/api/stats").json()["audit_entries"]

    client.post("/api/library/external/resync", json={})

    assert client.get("/api/stats").json()["audit_entries"] == before


def test_reading_the_library_writes_nothing(client, room, connected):
    content_id = add_over_http(client, room, autoSync=True).json()["contentId"]

    client.get("/api/library/external")
    client.get(f"/api/library/external/{content_id}")
    client.get(f"/api/library/external/{content_id}/sync-status")
    client.get("/api/library/sources")
    client.get("/api/library/connections")
    client.get("/api/library/folders", params={"roomId": room["id"]})

    assert client.get("/api/audit", params={"collection": "document"}).json()["count"] == 1
