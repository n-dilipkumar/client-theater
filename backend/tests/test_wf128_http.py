"""WF-128 over HTTP: the routes the host actually mounted.

``test_wf128.py`` proves the rules. This file proves the wiring: that discovery
mounted the router, that each error type became the status code a client should
branch on, that a follower and the presenter are kept apart, and that every audit
row a write left behind names a concrete route the app serves.

That last one is the property the whole feature host exists to protect, so it is
asserted against the mounted route table rather than against a literal written in
the test. A literal would agree with a typo.

Each test file must pass on its own, because the suite runs under
``pytest-xdist`` and a test that only passes in one order fails intermittently.
Nothing here depends on another test's rows: every test builds its own room.
"""

from __future__ import annotations

import pytest
from dsr.db.audited import RecordNotFound
from dsr.features import load_feature
from dsr.live_sessions import vocabulary as v
from dsr.store import RecordStore
from fastapi.testclient import TestClient

MODULE = "wf128_live_co_presented_walkthrough_inside_the_room"
PREFIX = "/api/wf-128"
FEATURE_ID = "wf-128-live-presentation"


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
    for index in range(3):
        live_store.create(
            "page",
            {"title": f"Page {index + 1}", "position": index},
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


def _created(client: TestClient, room_id: str, **extra: object) -> dict:
    response = client.post(
        f"{PREFIX}/sessions",
        json={"room_id": room_id, "presenter": "rep@northwind.example", **extra},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _started(client: TestClient, room_id: str) -> dict:
    session = _created(client, room_id)
    response = client.post(f"{PREFIX}/sessions/{session['id']}/start", json={})
    assert response.status_code == 200, response.text
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
        assert error_type.__module__ == "dsr.live_sessions.errors", error_type
        assert error_type not in builtins_, error_type
        assert error_type is not RecordNotFound, error_type
    assert len(feature.EXCEPTION_HANDLERS) == 3


def test_the_feature_reports_every_route_it_mounts(client: TestClient):
    paths = {route["path"] for route in client.get(f"/api/features/{FEATURE_ID}").json()["routes"]}
    assert paths == {
        f"{PREFIX}/vocabulary",
        f"{PREFIX}/summary",
        f"{PREFIX}/recipients",
        f"{PREFIX}/sessions",
        f"{PREFIX}/sessions/{{session_id}}",
        f"{PREFIX}/sessions/{{session_id}}/start",
        f"{PREFIX}/sessions/{{session_id}}/end",
        f"{PREFIX}/sessions/{{session_id}}/reveal",
        f"{PREFIX}/sessions/{{session_id}}/state",
        f"{PREFIX}/sessions/{{session_id}}/sync",
        f"{PREFIX}/sessions/{{session_id}}/invite",
        f"{PREFIX}/sessions/{{session_id}}/participants",
        f"{PREFIX}/sessions/{{session_id}}/events",
        f"{PREFIX}/sessions/{{session_id}}/questions",
        f"{PREFIX}/sessions/{{session_id}}/attendance",
        f"{PREFIX}/sessions/{{session_id}}/recordings",
        f"{PREFIX}/sessions/{{session_id}}/notifications",
        f"{PREFIX}/questions/{{question_id}}/answer",
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


def test_a_session_without_a_room_is_422_with_a_field_keyed_map(client: TestClient):
    response = client.post(f"{PREFIX}/sessions", json={"title": "Live"})
    assert response.status_code == 422, response.text
    body = response.json()
    assert body["error"] == "live_session_invalid"
    assert "room_id" in body["errors"]


def test_an_unknown_session_is_404(client: TestClient):
    response = client.get(f"{PREFIX}/sessions/wf128_sess_missing")
    assert response.status_code == 404, response.text
    assert response.json()["error"] == "live_session_not_found"


def test_a_forward_jump_over_http_is_409_not_400(client: TestClient, room_id):
    session = _started(client, room_id)
    response = client.post(f"{PREFIX}/sessions/{session['id']}/reveal", json={"position": 2})
    assert response.status_code == 409, response.text
    body = response.json()
    assert body["reason"] == v.REASON_SKIPPED_REVEAL
    assert body["error"] == "live_session_conflict"


def test_a_follower_driving_over_http_is_409(client: TestClient, room_id):
    session = _started(client, room_id)
    response = client.post(
        f"{PREFIX}/sessions/{session['id']}/reveal",
        json={"position": 1, "as": "a.buyer@northwind.example"},
    )
    assert response.status_code == 409, response.text
    assert response.json()["reason"] == v.REASON_NOT_PRESENTER


def test_a_second_presenter_over_http_is_409(client: TestClient, room_id):
    session = _created(client, room_id)
    response = client.post(
        f"{PREFIX}/sessions/{session['id']}/invite",
        json={"email": "other@northwind.example", "role": v.ROLE_PRESENTER},
    )
    assert response.status_code == 409, response.text
    assert response.json()["reason"] == v.REASON_SECOND_PRESENTER


# --------------------------------------------------------------------------- #
# The workflow over HTTP
# --------------------------------------------------------------------------- #


def test_create_start_reveal_is_the_presenter_path(client: TestClient, room_id):
    session = _created(client, room_id, invitees=["a.buyer@northwind.example"])
    assert session["step_count"] == 3
    assert {row["role"] for row in session["participants"]} == {v.ROLE_PRESENTER, v.ROLE_FOLLOWER}

    started = client.post(f"{PREFIX}/sessions/{session['id']}/start", json={}).json()
    assert started["state"] == v.STATE_LIVE
    assert started["recording"]["status"] == v.RECORDING_RECORDING

    moved = client.post(f"{PREFIX}/sessions/{session['id']}/reveal", json={"position": 1})
    assert moved.status_code == 200, moved.text
    assert moved.json()["position"] == 1
    assert moved.json()["current_step"]["label"] == "Page 2"


def test_a_follower_state_poll_and_sync_over_http(client: TestClient, room_id):
    session = _started(client, room_id)

    state = client.get(
        f"{PREFIX}/sessions/{session['id']}/state",
        params={"viewer": "a.buyer@northwind.example"},
    ).json()
    assert state["following"] == 0
    assert state["transport"] == v.TRANSPORT_STAND_IN
    assert state["streamed"] is False

    synced = client.post(
        f"{PREFIX}/sessions/{session['id']}/sync",
        json={"as": "a.buyer@northwind.example"},
    )
    assert synced.status_code == 200, synced.text
    assert synced.json()["participant"]["status"] == v.PARTICIPANT_JOINED


def test_a_question_and_its_answer_over_http(client: TestClient, room_id):
    session = _started(client, room_id)
    asked = client.post(
        f"{PREFIX}/sessions/{session['id']}/questions",
        json={"asker": "a.buyer@northwind.example", "text": "How does SSO work?"},
    )
    assert asked.status_code == 201, asked.text
    question = asked.json()
    assert question["status"] == v.QUESTION_OPEN
    assert question["deal_ref"] == room_id

    answer = client.post(f"{PREFIX}/questions/{question['id']}/answer", json={"answer": "By SCIM."})
    assert answer.status_code == 200, answer.text
    assert answer.json()["status"] == v.QUESTION_ANSWERED

    open_rows = client.get(
        f"{PREFIX}/sessions/{session['id']}/questions", params={"status": v.QUESTION_OPEN}
    ).json()
    assert open_rows["count"] == 0


def test_end_publishes_the_replay_and_writes_attendance(client: TestClient, room_id):
    session = _started(client, room_id)
    client.post(f"{PREFIX}/sessions/{session['id']}/sync", json={"as": "a.buyer@northwind.example"})

    ended = client.post(f"{PREFIX}/sessions/{session['id']}/end", json={})
    assert ended.status_code == 200, ended.text
    body = ended.json()
    assert body["state"] == v.STATE_ENDED
    assert body["recording"]["status"] == v.RECORDING_PUBLISHED

    recordings = client.get(f"{PREFIX}/sessions/{session['id']}/recordings").json()
    assert recordings["count"] == 1
    assert recordings["entries"][0]["hosted"] is False

    attendance = client.get(f"{PREFIX}/sessions/{session['id']}/attendance").json()
    states = {row["email"]: row["state"] for row in attendance["entries"]}
    assert states["a.buyer@northwind.example"] == v.ATTENDANCE_ATTENDED
    assert states["b.buyer@northwind.example"] == v.ATTENDANCE_INVITED_ONLY


def test_the_session_event_log_records_the_run(client: TestClient, room_id):
    session = _started(client, room_id)
    client.post(f"{PREFIX}/sessions/{session['id']}/reveal", json={"position": 1})
    client.post(f"{PREFIX}/sessions/{session['id']}/end", json={})
    events = client.get(f"{PREFIX}/sessions/{session['id']}/events").json()
    kinds = {row["kind"] for row in events["entries"]}
    assert {
        v.EVENT_SESSION_STARTED,
        v.EVENT_STEP_REVEALED,
        v.EVENT_SESSION_ENDED,
        v.EVENT_RECORDING_PUBLISHED,
    } <= kinds


def test_the_recipients_route_reuses_the_rooms_buyers(client: TestClient, room_id):
    body = client.get(f"{PREFIX}/recipients", params={"room_id": room_id}).json()
    emails = {row["email"] for row in body["entries"]}
    assert emails == {"a.buyer@northwind.example", "b.buyer@northwind.example"}


def test_the_sessions_route_lists_and_filters_by_state(client: TestClient, room_id):
    session = _created(client, room_id)
    listed = client.get(f"{PREFIX}/sessions", params={"room_id": room_id}).json()
    assert [row["id"] for row in listed["entries"]] == [session["id"]]

    scheduled = client.get(
        f"{PREFIX}/sessions", params={"room_id": room_id, "state": v.STATE_SCHEDULED}
    ).json()
    assert scheduled["count"] == 1

    live_only = client.get(
        f"{PREFIX}/sessions", params={"room_id": room_id, "state": v.STATE_LIVE}
    ).json()
    assert live_only["count"] == 0


def test_the_participants_route_lists_every_invitee(client: TestClient, room_id):
    session = _created(client, room_id, invitees=["a.buyer@northwind.example"])
    body = client.get(f"{PREFIX}/sessions/{session['id']}/participants").json()
    emails = {row["email"] for row in body["entries"]}
    assert emails == {
        "rep@northwind.example",
        "a.buyer@northwind.example",
    }
    assert all(row["join_url"] for row in body["entries"])


def test_start_notifies_every_participant_over_http(client: TestClient, room_id):
    session = _started(client, room_id)
    notifications = client.get(f"{PREFIX}/sessions/{session['id']}/notifications").json()
    recipients = {row["recipient"] for row in notifications["entries"]}
    assert {"rep@northwind.example", "a.buyer@northwind.example"} <= recipients


def test_the_summary_route_reports_the_counts(client: TestClient, room_id):
    _started(client, room_id)
    body = client.get(f"{PREFIX}/summary", params={"room_id": room_id}).json()
    assert body["sessions"] == 1
    assert body["live"] == 1
    assert body["transport"] == v.TRANSPORT_STAND_IN


def test_the_vocabulary_route_publishes_the_issue_s_open_decisions(client: TestClient):
    body = client.get(f"{PREFIX}/vocabulary").json()
    assert body["transport"] == v.TRANSPORT_STAND_IN
    assert body["stack_choice"]["sfu"]["licence"] == "Apache-2.0"
    assert body["stack_choice"]["deck"]["licence"] == "MIT"
    assert body["unimplemented_automations"] == list(v.UNIMPLEMENTED_AUTOMATIONS)
    assert any("no realtime transport" in note.lower() for note in body["sourced_gaps"])


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

    session = _created(client, room_id, invitees=["a.buyer@northwind.example"])
    client.post(f"{PREFIX}/sessions/{session['id']}/start", json={})
    client.post(f"{PREFIX}/sessions/{session['id']}/reveal", json={"position": 1})
    client.post(f"{PREFIX}/sessions/{session['id']}/sync", json={"as": "a.buyer@northwind.example"})
    question = client.post(
        f"{PREFIX}/sessions/{session['id']}/questions",
        json={"asker": "a.buyer@northwind.example", "text": "SSO?"},
    ).json()
    client.post(f"{PREFIX}/questions/{question['id']}/answer", json={"answer": "SCIM."})
    client.post(f"{PREFIX}/sessions/{session['id']}/end", json={})

    rows = [
        row for row in live_store.db.audit() if str(row.get("collection") or "").startswith("wf128")
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

    assert checked >= 6, f"only {checked} route-sourced audit rows were checked"
