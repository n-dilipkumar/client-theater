"""WF-126 over HTTP: the routes the host actually mounted.

``test_wf126.py`` proves the rules. This file proves the wiring: that discovery
mounted the router, that each error type became the status code a client should
branch on, that the seller's and the buyer's routes are kept apart, and that every
audit row a write left behind names a concrete route the app serves.

That last one is the property the whole feature host exists to protect, so it is
asserted against the mounted route table rather than against a literal written in
the test. A literal would agree with a typo.

Each test file must pass on its own, because the suite runs under ``pytest-xdist``
and a test that only passes in one order fails intermittently. Nothing here
depends on another test's rows: every test builds its own room and recording.
"""

from __future__ import annotations

import pytest
from dsr.db.audited import RecordNotFound
from dsr.features import load_feature
from dsr.personalised_video import vocabulary as v
from dsr.store import RecordStore
from fastapi.testclient import TestClient

MODULE = "wf126_asynchronous_personalised_video_inside_the_room"
PREFIX = "/api/wf-126"
FEATURE_ID = "wf-126-personalised-video"


@pytest.fixture()
def feature():
    return load_feature(MODULE)


@pytest.fixture()
def live_store(db) -> RecordStore:
    """A store over the *same* database the ``client`` fixture serves."""

    return RecordStore(db)


@pytest.fixture()
def room_id(live_store: RecordStore) -> str:
    room_id = live_store.create("room", {"name": "Northwind"}, actor="test", source="test")["id"]
    live_store.create(
        "page",
        {"title": "Welcome", "slug": "welcome", v.HERO_PAGE_FLAG: True},
        room_id=room_id,
        actor="test",
        source="test",
    )
    for email in ("a.buyer@northwind.example", "b.buyer@northwind.example"):
        live_store.create(
            "room_access",
            {"principal": email, "role": "viewer"},
            room_id=room_id,
            actor="test",
            source="test",
        )
    return room_id


def _record(client: TestClient, room_id: str, **extra: object) -> dict:
    response = client.post(
        f"{PREFIX}/recordings",
        json={"title": "Intro", "room_id": room_id, **extra},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _placed(client: TestClient, room_id: str) -> dict:
    recording = _record(client, room_id)
    response = client.post(
        f"{PREFIX}/rooms/{room_id}/recordings/{recording['id']}/place",
        json={"note": "Before our call"},
    )
    assert response.status_code == 201, response.text
    return response.json()


# --------------------------------------------------------------------------- #
# Discovery and mounting
# --------------------------------------------------------------------------- #


def test_discovery_mounted_this_feature_by_its_prefix(client: TestClient, feature):
    listed = client.get("/api/features").json()
    found = [entry for entry in listed["features"] if entry["id"] == FEATURE_ID]
    assert found, "the feature registry did not list this feature"
    assert found[0]["prefix"] == PREFIX
    assert found[0]["loaded"] is True
    assert client.get(f"{PREFIX}/vocabulary").status_code == 200


def test_no_feature_failed_to_load_because_of_this_workflow(client: TestClient):
    listed = client.get("/api/features").json()
    failures = [entry for entry in listed["failed"] if entry["module"].endswith(MODULE)]
    assert not failures, failures


def test_the_feature_maps_only_error_types_it_raises_itself(feature):
    builtins_ = (ValueError, LookupError, PermissionError, KeyError, TypeError)
    for error_type in feature.EXCEPTION_HANDLERS:
        assert error_type.__module__ == "dsr.personalised_video.errors", error_type
        assert error_type not in builtins_, error_type
        assert error_type is not RecordNotFound, error_type
    assert len(feature.EXCEPTION_HANDLERS) == 3


def test_the_feature_reports_every_route_it_mounts(client: TestClient):
    paths = {route["path"] for route in client.get(f"/api/features/{FEATURE_ID}").json()["routes"]}
    assert paths == {
        f"{PREFIX}/vocabulary",
        f"{PREFIX}/summary",
        f"{PREFIX}/recipients",
        f"{PREFIX}/recordings",
        f"{PREFIX}/recordings/{{recording_id}}",
        f"{PREFIX}/recordings/{{recording_id}}/playback",
        f"{PREFIX}/recordings/{{recording_id}}/events",
        f"{PREFIX}/recordings/{{recording_id}}/engagement",
        f"{PREFIX}/rooms/{{room_id}}/hero",
        f"{PREFIX}/rooms/{{room_id}}/recordings/{{recording_id}}/place",
        f"{PREFIX}/rooms/{{room_id}}/placements",
        f"{PREFIX}/placements/{{placement_id}}/withdraw",
        f"{PREFIX}/rooms/{{room_id}}/events",
        f"{PREFIX}/rooms/{{room_id}}/engagement",
        f"{PREFIX}/alerts",
        f"{PREFIX}/rooms/{{room_id}}/alerts",
        f"{PREFIX}/notifications",
        f"{PREFIX}/rooms/{{room_id}}/notifications",
    }


def test_the_module_never_imports_the_app_or_opens_sqlite(feature):
    import ast
    from pathlib import Path

    source = Path(feature.__file__).read_text(encoding="utf-8")
    assert "import sqlite3" not in source
    assert "from dsr.api" not in source
    assert "import dsr.api" not in source
    ast.parse(source)  # the file parses, so the grep above read real code


# --------------------------------------------------------------------------- #
# Errors become the status codes a client branches on
# --------------------------------------------------------------------------- #


def test_a_recording_without_a_title_is_422_with_a_field_keyed_map(client: TestClient):
    response = client.post(f"{PREFIX}/recordings", json={"kind": v.KIND_PERSONAL_INTRO})
    assert response.status_code == 422, response.text
    body = response.json()
    assert body["error"] == "video_invalid"
    assert "title" in body["errors"]


def test_an_unknown_recording_is_404(client: TestClient):
    response = client.get(f"{PREFIX}/recordings/wf126_rec_does_not_exist")
    assert response.status_code == 404, response.text
    assert response.json()["error"] == "video_not_found"


def test_placing_a_recording_on_another_room_is_409_not_400(
    client: TestClient, live_store, room_id
):
    other = live_store.create("room", {"name": "Contoso"}, actor="test", source="test")["id"]
    recording = _record(client, room_id)
    response = client.post(f"{PREFIX}/rooms/{other}/recordings/{recording['id']}/place", json={})
    assert response.status_code == 409, response.text
    assert response.json()["reason"] == "recording_belongs_to_another_room"


def test_personalising_for_a_stranger_is_422(client: TestClient, room_id):
    recording = _record(client, room_id)
    response = client.post(
        f"{PREFIX}/rooms/{room_id}/recordings/{recording['id']}/place",
        json={"personalised_for": ["stranger@elsewhere.example"]},
    )
    assert response.status_code == 422, response.text
    assert "personalised_for" in response.json()["errors"]


# --------------------------------------------------------------------------- #
# The workflow over HTTP
# --------------------------------------------------------------------------- #


def test_recording_and_placing_puts_a_video_on_the_hero(client: TestClient, room_id):
    recording = _record(client, room_id, kind=v.KIND_WALKTHROUGH)
    assert recording["kind"] == v.KIND_WALKTHROUGH
    assert recording["transcode"]["state"] == v.TRANSCODE_READY
    assert recording["hosted"] is False

    response = client.post(
        f"{PREFIX}/rooms/{room_id}/recordings/{recording['id']}/place",
        json={"note": "Security review"},
    )
    assert response.status_code == 201, response.text
    placement = response.json()
    assert placement["surface"] == v.SURFACE_HERO
    assert placement["status"] == v.PLACEMENT_LIVE
    assert {row["recipient"] for row in placement["notified"]} == {
        "a.buyer@northwind.example",
        "b.buyer@northwind.example",
    }


def test_the_hero_route_serves_the_buyer_a_personalised_video(client: TestClient, room_id):
    _placed(client, room_id)
    response = client.get(
        f"{PREFIX}/rooms/{room_id}/hero", params={"viewer": "a.buyer@northwind.example"}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["personalised"] is True
    assert body["message"] == "Before our call"
    assert body["recording"]["title"] == "Intro"


def test_playback_over_http_opens_then_clears_the_alert(client: TestClient, room_id):
    placement = _placed(client, room_id)
    recording_id = placement["recording_id"]
    viewer = "a.buyer@northwind.example"

    client.post(
        f"{PREFIX}/recordings/{recording_id}/playback",
        json={"event": v.EVENT_START, "viewer": viewer},
    )
    low = client.post(
        f"{PREFIX}/recordings/{recording_id}/playback",
        json={"event": v.EVENT_PROGRESS, "viewer": viewer, "watched_percent": 20},
    )
    assert low.status_code == 201, low.text
    assert low.json()["alert"]["status"] == v.ALERT_OPEN

    opened = client.get(f"{PREFIX}/alerts", params={"status": v.ALERT_OPEN}).json()
    assert opened["count"] == 1
    assert opened["entries"][0]["viewer"] == viewer

    done = client.post(
        f"{PREFIX}/recordings/{recording_id}/playback",
        json={"event": v.EVENT_COMPLETE, "viewer": viewer},
    )
    assert done.json()["alert"]["status"] == v.ALERT_CLEARED
    assert client.get(f"{PREFIX}/alerts", params={"status": v.ALERT_OPEN}).json()["count"] == 0


def test_a_second_placement_supersedes_the_first_over_http(client: TestClient, room_id):
    first = _placed(client, room_id)
    second = _placed(client, room_id)
    assert second["superseded"] == [first["id"]]

    live = client.get(
        f"{PREFIX}/rooms/{room_id}/placements", params={"status": v.PLACEMENT_LIVE}
    ).json()
    assert [row["id"] for row in live["entries"]] == [second["id"]]


def test_withdrawing_a_live_placement_over_http(client: TestClient, room_id):
    placement = _placed(client, room_id)
    response = client.post(f"{PREFIX}/placements/{placement['id']}/withdraw", json={})
    assert response.status_code == 200, response.text
    assert response.json()["status"] == v.PLACEMENT_WITHDRAWN


def test_the_recipients_route_reuses_the_rooms_buyers(client: TestClient, room_id):
    body = client.get(f"{PREFIX}/recipients", params={"room_id": room_id}).json()
    emails = {row["email"] for row in body["entries"]}
    assert emails == {"a.buyer@northwind.example", "b.buyer@northwind.example"}


def test_the_summary_route_reports_the_counts(client: TestClient, room_id):
    _placed(client, room_id)
    body = client.get(f"{PREFIX}/summary", params={"room_id": room_id}).json()
    assert body["recordings"] == 1
    assert body["live_placements"] == 1
    assert body["notifications"] == 2


def test_the_vocabulary_route_publishes_the_issue_s_open_decisions(client: TestClient):
    body = client.get(f"{PREFIX}/vocabulary").json()
    assert body["events"] == list(v.EVENTS)
    assert body["low_completion_percent"] == v.LOW_COMPLETION_PERCENT
    assert body["unimplemented_automations"] == list(v.UNIMPLEMENTED_AUTOMATIONS)
    assert any("hosting" in note.lower() for note in body["sourced_gaps"])


# --------------------------------------------------------------------------- #
# Every write's audit row names a route the app actually serves
# --------------------------------------------------------------------------- #


def test_every_audit_row_this_feature_wrote_names_a_route_the_app_serves(
    client: TestClient, live_store: RecordStore, room_id: str
):
    served = {
        (method, shape["path"])
        for shape in client.get(f"/api/features/{FEATURE_ID}").json()["routes"]
        for method in shape["methods"]
    }
    assert served, "the registry reports no route for this feature"

    recording = _record(client, room_id)
    placement = client.post(
        f"{PREFIX}/rooms/{room_id}/recordings/{recording['id']}/place", json={}
    ).json()
    client.post(
        f"{PREFIX}/recordings/{recording['id']}/playback",
        json={"event": v.EVENT_START, "viewer": "a.buyer@northwind.example"},
    )
    client.post(f"{PREFIX}/placements/{placement['id']}/withdraw", json={})

    rows = [
        row for row in live_store.db.audit() if str(row.get("collection") or "").startswith("wf126")
    ]
    assert rows, "no audit rows were written for this feature's collections"

    checked = 0
    for row in rows:
        source = str(row.get("source") or "")
        if not source.startswith(("POST ", "PATCH ", "DELETE ")):
            continue
        method, _, path = source.partition(" ")
        assert (method, path) in served, f"audit row names a route the app does not serve: {source}"
        checked += 1

    assert checked >= 4, f"only {checked} route-sourced audit rows were checked"
