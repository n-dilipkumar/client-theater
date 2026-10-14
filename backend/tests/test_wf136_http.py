"""WF-136 over HTTP: the routes the host actually mounted.

``test_wf136.py`` proves the rules. This file proves the wiring: that discovery
mounted the router by itself, that each refusal became the status code a client
branches on, that the demo seeder returns a string a Windows console can print,
and that every audit row this feature's routes wrote names a route the app really
serves.

That last one is the property the whole feature host exists to protect, so it is
asserted against the mounted route table rather than against a literal written in
the test. A literal would agree with a typo.

Every test builds its own room, so nothing depends on another test's rows and the
file passes on its own under ``pytest-xdist``. The demo room is seeded from the
feature's own ``seed`` function, because that is what the reviewer will run.
"""

from __future__ import annotations

import random
from datetime import datetime, timezone

import pytest
from dsr.db.audited import RecordNotFound
from dsr.features import load_feature
from dsr.inroom_booking.errors import InRoomBookingError
from dsr.store import RecordStore
from fastapi.testclient import TestClient

MODULE = "wf136_inroom_booking"
PREFIX = "/api/wf-136"
FEATURE_ID = "wf-136-inroom-booking"
ACCEPT = f"{PREFIX}/proposals/{{proposal_id}}/accept"


@pytest.fixture()
def feature():
    return load_feature(MODULE)


@pytest.fixture()
def live_store(db) -> RecordStore:
    """A store over the *same* database the ``client`` fixture serves."""
    return RecordStore(db)


@pytest.fixture()
def seeded(db, live_store: RecordStore, feature):
    """A room the feature's own seeder made bookable, plus its id.

    The seeder is the code a reviewer runs, so the demo path is exercised here
    rather than mirrored with a second hand-built fixture that could drift from
    it.
    """
    room = live_store.create("room", {"name": "Northwind"}, actor="test", source="test")
    summary = feature.seed(
        db,
        {
            "room_ids": [(room["id"], "Northwind")],
            "now": datetime.now(timezone.utc),
            "rng": random.Random(7),
        },
    )
    return {"room_id": room["id"], "summary": summary}


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
    failures = [
        entry
        for entry in client.get("/api/features").json()["failed"]
        if entry["module"].endswith(MODULE)
    ]
    assert not failures, failures


def test_the_feature_maps_only_error_types_it_raises_itself(feature):
    """A handler for a builtin, or for another workflow's error, is refused.

    WF-058, WF-059 and WF-124 each already map their own hierarchies, so mapping
    one of theirs here would be an exception-handler collision the host refuses.
    The engine translates those errors at the boundary instead, and this asserts
    the handler's own type is this workflow's and nothing else.
    """
    builtins_ = (ValueError, LookupError, PermissionError, KeyError, TypeError)
    assert set(feature.EXCEPTION_HANDLERS) == {InRoomBookingError}
    for error_type in feature.EXCEPTION_HANDLERS:
        assert error_type.__module__ == "dsr.inroom_booking.errors", error_type
        assert error_type not in builtins_, error_type
        assert error_type is not RecordNotFound, error_type


def test_the_feature_reports_every_route_it_mounts(client: TestClient):
    """The registry is what ``tools/verify_all_routes.py`` walks, so the set is
    asserted exactly: a route missing from it is a route nobody checks for a 5xx."""
    paths = {route["path"] for route in client.get(f"/api/features/{FEATURE_ID}").json()["routes"]}
    assert paths == {
        f"{PREFIX}/vocabulary",
        f"{PREFIX}/inferences",
        f"{PREFIX}/summary",
        f"{PREFIX}/rooms/{{room_id}}/proposals",
        f"{PREFIX}/rooms/{{room_id}}/auto-propose",
        f"{PREFIX}/rooms/{{room_id}}/expire-lapsed",
        f"{PREFIX}/proposals/{{proposal_id}}",
        f"{PREFIX}/proposals/{{proposal_id}}/slots",
        f"{PREFIX}/proposals/{{proposal_id}}/accept",
        f"{PREFIX}/proposals/{{proposal_id}}/decline",
        f"{PREFIX}/rooms/{{room_id}}/meetings",
        f"{PREFIX}/meetings/{{meeting_id}}",
        f"{PREFIX}/meetings/{{meeting_id}}/recording",
        f"{PREFIX}/rooms/{{room_id}}/next-steps",
    }


def test_the_module_never_imports_the_app_or_opens_sqlite(feature):
    import ast
    from pathlib import Path

    source = Path(feature.__file__).read_text(encoding="utf-8")
    assert "import sqlite3" not in source
    assert "from dsr.api" not in source
    assert "import dsr.api" not in source
    ast.parse(source)  # the file parses, so the greps above read real code


# --------------------------------------------------------------------------- #
# The seeder
# --------------------------------------------------------------------------- #


def test_the_seed_returns_a_string_a_windows_console_can_print(seeded):
    """cp1252, because the seeder runs on a Windows console.

    A RIGHTWARDS ARROW in one recovered feature broke the whole seeder once, so
    the string is encoded here rather than assumed printable.
    """
    print(seeded["summary"])
    assert isinstance(seeded["summary"], str)
    assert seeded["summary"].encode("cp1252")
    assert "WF-136" in seeded["summary"]


def test_the_seeded_room_has_an_awaiting_next_step_and_a_booked_meeting(client: TestClient, seeded):
    room_id = seeded["room_id"]
    proposals = client.get(f"{PREFIX}/rooms/{room_id}/proposals").json()["proposals"]
    states = sorted(proposal["effective_state"] for proposal in proposals)
    assert states == ["booked", "proposed"]
    meetings = client.get(f"{PREFIX}/rooms/{room_id}/meetings").json()["meetings"]
    assert len(meetings) == 1
    assert meetings[0]["state"] == "linked"
    assert meetings[0]["recording_url"]


# --------------------------------------------------------------------------- #
# Vocabulary, inferences and counts
# --------------------------------------------------------------------------- #


def test_the_vocabulary_route_publishes_the_states_and_the_open_numbers(client: TestClient):
    body = client.get(f"{PREFIX}/vocabulary").json()
    assert body["proposal_states"] == ["proposed", "accepted", "booked", "declined", "expired"]
    assert body["answerable_proposal_states"] == ["proposed"]
    assert body["meeting_states"] == ["linked", "unlinked"]
    assert isinstance(body["idle_minutes"], int)


def test_the_inferences_route_names_the_unresolved_decision(client: TestClient):
    body = client.get(f"{PREFIX}/inferences").json()
    assert body["unresolved"] == ["idle_minutes"]
    assert body["unresolved_count"] == 1


def test_the_summary_route_reports_the_seeded_board(client: TestClient, seeded):
    body = client.get(f"{PREFIX}/summary", params={"room_id": seeded["room_id"]}).json()
    assert body["proposals"] == 2
    assert body["booked"] == 1
    assert body["meetings"] == 1
    assert body["meetings_linked"] == 1


def test_the_next_steps_panel_merges_proposals_and_map_tasks(client: TestClient, seeded):
    body = client.get(f"{PREFIX}/rooms/{seeded['room_id']}/next-steps").json()
    assert body["proposals"], "the awaiting next step is missing from the panel"
    assert body["map_tasks"], "the open MAP task is missing from the panel"
    # The booked proposal's task flipped to done, so it is not listed as open.
    titles = [task["title"] for task in body["map_tasks"]]
    assert "Complete the security review" not in titles


# --------------------------------------------------------------------------- #
# The workflow over HTTP
# --------------------------------------------------------------------------- #


def _first_available_slot(client: TestClient, proposal_id: str) -> str:
    grid = client.get(f"{PREFIX}/proposals/{proposal_id}/slots").json()
    return next(row["start"] for row in grid["slots"] if row["available"])


def test_a_buyer_can_pick_a_slot_and_book_it_over_http(client: TestClient, seeded):
    room_id = seeded["room_id"]
    created = client.post(
        f"{PREFIX}/rooms/{room_id}/proposals",
        json={"summary": "Book the deep dive"},
    )
    assert created.status_code == 201, created.text
    proposal_id = created.json()["id"]

    start = _first_available_slot(client, proposal_id)
    accepted = client.post(
        f"{PREFIX}/proposals/{proposal_id}/accept",
        json={"start": start, "attendee": {"name": "Ines", "email": "ines@example.com"}},
    )
    assert accepted.status_code == 200, accepted.text
    body = accepted.json()
    assert body["proposal"]["state"] == "booked"
    assert body["meeting"]["state"] == "linked"
    assert body["meeting"]["conference_url"]


def test_a_second_accept_is_409(client: TestClient, seeded):
    room_id = seeded["room_id"]
    created = client.post(f"{PREFIX}/rooms/{room_id}/proposals", json={"summary": "Book it"}).json()
    payload = {
        "start": _first_available_slot(client, created["id"]),
        "attendee": {"name": "Ines", "email": "i@example.com"},
    }
    assert (
        client.post(f"{PREFIX}/proposals/{created['id']}/accept", json=payload).status_code == 200
    )
    again = client.post(f"{PREFIX}/proposals/{created['id']}/accept", json=payload)
    assert again.status_code == 409
    assert again.json()["reason"] == "proposal_answered"


def test_an_unknown_proposal_is_404(client: TestClient):
    response = client.get(f"{PREFIX}/proposals/inroom_booking_proposal_missing")
    assert response.status_code == 404
    assert response.json()["reason"] == "proposal_not_found"


def test_a_proposal_with_no_summary_is_400_with_a_field_key(client: TestClient, seeded):
    response = client.post(
        f"{PREFIX}/rooms/{seeded['room_id']}/proposals", json={"details": "nothing here"}
    )
    assert response.status_code == 400, response.text
    body = response.json()
    assert body["reason"] == "proposal_invalid"
    assert "summary" in body["errors"]


def test_a_room_with_no_embed_cannot_be_proposed_in_is_409(client: TestClient, live_store):
    bare = live_store.create("room", {"name": "Bare"}, actor="test", source="test")
    response = client.post(f"{PREFIX}/rooms/{bare['id']}/proposals", json={"summary": "Talk"})
    assert response.status_code == 409, response.text
    assert response.json()["reason"] == "room_has_no_embed"


def test_declining_and_the_idle_automation_answer_over_http(client: TestClient, seeded, live_store):
    room_id = seeded["room_id"]
    created = client.post(
        f"{PREFIX}/rooms/{room_id}/proposals", json={"summary": "Decline me"}
    ).json()
    declined = client.post(
        f"{PREFIX}/proposals/{created['id']}/decline", json={"reason": "not now"}
    )
    assert declined.status_code == 200
    assert declined.json()["state"] == "declined"

    # A live proposal already exists (the seeded awaiting one), so the
    # automation declines to propose a second.
    auto = client.post(f"{PREFIX}/rooms/{room_id}/auto-propose")
    assert auto.status_code == 200
    assert auto.json()["reason"] in {"already_proposed", "room_active"}

    sweep = client.post(f"{PREFIX}/rooms/{room_id}/expire-lapsed")
    assert sweep.status_code == 200
    assert "count" in sweep.json()


def test_a_recording_is_attached_over_http(client: TestClient, seeded):
    meetings = client.get(f"{PREFIX}/rooms/{seeded['room_id']}/meetings").json()["meetings"]
    meeting_id = meetings[0]["id"]
    attached = client.post(
        f"{PREFIX}/meetings/{meeting_id}/recording", json={"url": "https://record.example/x"}
    )
    assert attached.status_code == 200, attached.text
    assert attached.json()["recording_url"] == "https://record.example/x"

    refused = client.post(f"{PREFIX}/meetings/{meeting_id}/recording", json={"url": "nope"})
    assert refused.status_code == 400
    assert refused.json()["reason"] == "recording_not_attached"


# --------------------------------------------------------------------------- #
# Every write's audit row names a route the app actually serves
# --------------------------------------------------------------------------- #


def test_every_audit_row_this_feature_wrote_names_a_route_the_app_serves(
    client: TestClient, live_store: RecordStore, seeded
):
    served = {
        (method, shape["path"])
        for shape in client.get(f"/api/features/{FEATURE_ID}").json()["routes"]
        for method in shape["methods"]
    }
    assert served, "the registry reports no route for this feature"

    room_id = seeded["room_id"]
    created = client.post(
        f"{PREFIX}/rooms/{room_id}/proposals", json={"summary": "Audited next step"}
    ).json()
    start = _first_available_slot(client, created["id"])
    client.post(
        f"{PREFIX}/proposals/{created['id']}/accept",
        json={"start": start, "attendee": {"name": "Ines", "email": "i@example.com"}},
    )
    other = client.post(f"{PREFIX}/rooms/{room_id}/proposals", json={"summary": "Declined"}).json()
    client.post(f"{PREFIX}/proposals/{other['id']}/decline", json={"reason": "no"})
    meetings = client.get(f"{PREFIX}/rooms/{room_id}/meetings").json()["meetings"]
    client.post(
        f"{PREFIX}/meetings/{meetings[0]['id']}/recording", json={"url": "https://r.example/x"}
    )
    client.post(f"{PREFIX}/rooms/{room_id}/expire-lapsed")

    rows = [row for row in live_store.db.audit() if "/api/wf-136" in str(row.get("source") or "")]
    assert rows, "no audit rows named a route of this feature"

    checked = 0
    for row in rows:
        source = str(row.get("source") or "")
        method, _, path = source.partition(" ")
        assert (method, path) in served, f"audit row names a route the app does not serve: {source}"
        checked += 1
    assert checked >= 5, f"only {checked} route-sourced audit rows were checked"
