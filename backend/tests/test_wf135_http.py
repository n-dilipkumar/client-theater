"""WF-135 over HTTP: the routes the host actually mounted.

``test_wf135.py`` proves the rules. This file proves the wiring: that discovery
mounted the router, that each error type became the status code a client should
branch on, that one POST fans out to all three destinations, that a failed
destination makes the capture partial, and that every audit row a write left
behind names a concrete route the app serves.

That last one is the property the whole feature host exists to protect, so it is
asserted against the mounted route table rather than against a literal written in
the test. A literal would agree with a typo.

Each test file must pass on its own, because the suite runs under ``pytest-xdist``
and a test that only passes in one order fails intermittently. Nothing here
depends on another test's rows: every test builds its own room and its own
captures.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from dsr.competitive_baseline import conversation_intelligence as ci
from dsr.competitive_baseline.conversation_intelligence import CaptureRefused
from dsr.db.audited import RecordNotFound
from dsr.features import load_feature
from dsr.store import RecordStore
from fastapi.testclient import TestClient

MODULE = "wf135_conversation_intelligence"
PREFIX = "/api/wf-135"
FEATURE_ID = "wf-135-conversation-intelligence"


@pytest.fixture()
def feature():
    return load_feature(MODULE)


@pytest.fixture()
def live_store(db) -> RecordStore:
    """A store over the *same* database the ``client`` fixture serves."""
    return RecordStore(db)


@pytest.fixture()
def room_id(live_store: RecordStore) -> str:
    return live_store.create("room", {"name": "Northwind"}, actor="test", source="test")["id"]


def _capture(client: TestClient, room_id: str, **extra: object) -> dict:
    payload = {
        "room_id": room_id,
        "summary": "A discovery call.",
        "next_steps": ["Send the SOC 2 report"],
        "requirements": ["SOC 2 Type II"],
        "meddicc": {"champion": "Priya", "metrics": "time"},
        **extra,
    }
    response = client.post(f"{PREFIX}/captures", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def _destination(capture: dict, kind: str) -> dict:
    return next(row for row in capture["destinations"] if row["kind"] == kind)


# --------------------------------------------------------------------------- #
# Discovery and mounting
# --------------------------------------------------------------------------- #


def test_discovery_mounted_this_feature_by_its_prefix(client: TestClient, feature):
    """The router was mounted by discovery alone. No shared file was edited."""
    listed = client.get("/api/features").json()
    found = [entry for entry in listed["features"] if entry["id"] == FEATURE_ID]
    assert found, "the feature registry did not list this feature"
    assert found[0]["prefix"] == PREFIX
    assert found[0]["loaded"] is True
    assert client.get(f"{PREFIX}/vocabulary").status_code == 200


def test_no_feature_failed_to_load_because_of_this_workflow(client: TestClient):
    """A broken feature is reported and skipped, so a failure here would be silent."""
    listed = client.get("/api/features").json()
    failures = [entry for entry in listed["failed"] if entry["module"].endswith(MODULE)]
    assert not failures, failures


def test_the_feature_maps_only_error_types_it_raises_itself(feature):
    """A handler for a builtin type would intercept that exception product-wide."""
    builtins_ = (ValueError, LookupError, PermissionError, KeyError, TypeError)
    for error_type in feature.EXCEPTION_HANDLERS:
        assert error_type.__module__ == "dsr.competitive_baseline.conversation_intelligence"
        assert error_type not in builtins_, error_type
        assert error_type is not RecordNotFound, error_type
    assert len(feature.EXCEPTION_HANDLERS) == 3


def test_the_feature_reports_every_route_it_mounts(client: TestClient):
    """The registry is what ``tools/verify_all_routes.py`` walks."""
    shapes = client.get(f"/api/features/{FEATURE_ID}").json()["routes"]
    served = {f"{method} {shape['path']}" for shape in shapes for method in shape["methods"]}
    assert served == {
        f"GET {PREFIX}/summary",
        f"GET {PREFIX}/vocabulary",
        f"GET {PREFIX}/coverage",
        f"GET {PREFIX}/captures",
        f"POST {PREFIX}/captures",
        f"GET {PREFIX}/rooms/{{room_id}}/captures",
        f"GET {PREFIX}/captures/{{interaction_id}}",
        f"GET {PREFIX}/captures/{{interaction_id}}/destinations",
        f"GET {PREFIX}/destinations/{{row_id}}",
        f"POST {PREFIX}/destinations/{{row_id}}/report",
    }


def test_the_module_never_imports_the_app_or_opens_sqlite(feature):
    """The enforced rule, restated so the reason is on the record for this workflow."""
    source = Path(feature.__file__).read_text(encoding="utf-8")
    assert "import sqlite3" not in source
    assert "from dsr.api" not in source
    assert "import dsr.api" not in source
    assert source.count("_source(") >= 2  # every write derives its audit source


# --------------------------------------------------------------------------- #
# The board
# --------------------------------------------------------------------------- #


def test_the_summary_route_reports_the_numbers(client: TestClient, room_id: str):
    _capture(client, room_id)
    body = client.get(f"{PREFIX}/summary", params={"room_id": room_id}).json()
    assert body["captures"] == 1
    assert body["destinations"] == len(ci.DESTINATION_KINDS)
    assert body["agreed"] == 1


def test_the_vocabulary_route_publishes_the_issues_open_decisions(client: TestClient):
    body = client.get(f"{PREFIX}/vocabulary").json()
    assert body["destination_kinds"] == list(ci.DESTINATION_KINDS)
    assert body["crm_outcomes"] == list(ci.CRM_OUTCOMES)
    assert [row["letter"] for row in body["meddicc"]] == list("MEDDICC")
    assert any("Bi-directional" in note for note in body["not_implemented"])


def test_the_coverage_route_reports_covered_and_missing_letters(client: TestClient, room_id: str):
    _capture(client, room_id, meddicc={"champion": "Priya"})
    body = client.get(f"{PREFIX}/coverage", params={"room_id": room_id}).json()
    assert body["covered_letters"] == ["champion"]
    assert "metrics" in body["missing_letters"]
    assert not any("score" in key for key in body)


# --------------------------------------------------------------------------- #
# One entry, three destinations
# --------------------------------------------------------------------------- #


def test_creating_a_capture_fans_out_to_three_destinations(client: TestClient, room_id: str):
    capture = _capture(client, room_id)
    assert sorted(row["kind"] for row in capture["destinations"]) == sorted(ci.DESTINATION_KINDS)
    assert capture["agreement"]["agreed"] is True
    assert capture["meddicc_coverage"]["letters_total"] == len(ci.MEDDICC_KEYS)


def test_the_capture_can_be_read_back_with_its_destinations(client: TestClient, room_id: str):
    capture = _capture(client, room_id)
    read = client.get(f"{PREFIX}/captures/{capture['id']}").json()
    assert read["id"] == capture["id"]
    assert read["extraction"]["summary"] == "A discovery call."

    listed = client.get(f"{PREFIX}/captures/{capture['id']}/destinations").json()
    assert len(listed["destinations"]) == len(ci.DESTINATION_KINDS)
    assert listed["agreement"]["agreed"] is True


def test_captures_can_be_listed_for_one_room(client: TestClient, room_id: str):
    _capture(client, room_id)
    body = client.get(f"{PREFIX}/rooms/{room_id}/captures").json()
    assert body["count"] == 1
    assert body["captures"][0]["room_id"] == room_id

    every = client.get(f"{PREFIX}/captures").json()
    assert every["count"] >= 1


def test_an_unknown_capture_is_404(client: TestClient):
    response = client.get(f"{PREFIX}/captures/wf135_capture_absent")
    assert response.status_code == 404, response.text
    assert response.json()["error"] == "not_found"


def test_an_unknown_destination_is_404(client: TestClient):
    response = client.get(f"{PREFIX}/destinations/wf135_row_absent")
    assert response.status_code == 404, response.text


# --------------------------------------------------------------------------- #
# Errors become the status codes a client branches on
# --------------------------------------------------------------------------- #


def test_an_invalid_capture_is_400_with_a_field_keyed_map(client: TestClient, room_id: str):
    response = client.post(f"{PREFIX}/captures", json={"room_id": room_id})
    assert response.status_code == 400, response.text
    body = response.json()
    assert body["error"] == "capture_invalid"
    assert "summary" in body["errors"]


def test_a_report_from_the_wrong_vocabulary_is_400(client: TestClient, room_id: str):
    capture = _capture(client, room_id)
    row = _destination(capture, ci.ROOM_NOTE)
    response = client.post(f"{PREFIX}/destinations/{row['id']}/report", json={"outcome": "updated"})
    assert response.status_code == 400, response.text
    assert "outcome" in response.json()["errors"]


def test_a_fan_out_that_reaches_one_destination_is_409(
    client: TestClient, live_store: RecordStore, room_id: str, monkeypatch
):
    """The researched requirement, over HTTP: one destination is not the workflow."""
    real_create = live_store.create

    def drop_the_crm_row(collection, data, **kwargs):
        if collection == ci.DESTINATION_COLLECTION and data.get("kind") == ci.CRM_FIELD:
            return {"id": "dropped"}
        return real_create(collection, data, **kwargs)

    monkeypatch.setattr(client.app.state.store, "create", drop_the_crm_row)
    response = client.post(
        f"{PREFIX}/captures",
        json={"room_id": room_id, "summary": "A call.", "requirements": ["SSO"]},
    )
    assert response.status_code == 409, response.text
    body = response.json()
    assert body["error"] == "capture_conflict"
    assert body["reason"] == CaptureRefused.REASON_FANOUT_INCOMPLETE


def test_a_write_that_does_not_read_back_is_409(
    client: TestClient, live_store: RecordStore, room_id: str, monkeypatch
):
    """A report that appears to succeed and does not change the row must be refused."""
    capture = _capture(client, room_id)
    row = _destination(capture, ci.CRM_FIELD)
    monkeypatch.setattr(client.app.state.store, "update", lambda *args, **kwargs: None)

    response = client.post(f"{PREFIX}/destinations/{row['id']}/report", json={"outcome": "failed"})
    assert response.status_code == 409, response.text
    assert response.json()["reason"] == CaptureRefused.REASON_WRITE_NOT_VISIBLE


# --------------------------------------------------------------------------- #
# Reporting a destination flips agreement
# --------------------------------------------------------------------------- #


def test_reporting_a_failed_crm_outcome_makes_the_capture_partial(client: TestClient, room_id: str):
    capture = _capture(client, room_id)
    row = _destination(capture, ci.CRM_FIELD)

    reported = client.post(f"{PREFIX}/destinations/{row['id']}/report", json={"outcome": "failed"})
    assert reported.status_code == 200, reported.text
    assert reported.json()["state"] == ci.FAILED
    assert reported.json()["capture_agreement"]["agreed"] is False

    after = client.get(f"{PREFIX}/captures/{capture['id']}").json()
    assert after["agreement"]["agreed"] is False
    assert after["agreement"]["failed"] == [ci.CRM_FIELD]


def test_reporting_a_successful_crm_outcome_keeps_agreement(client: TestClient, room_id: str):
    capture = _capture(client, room_id)
    row = _destination(capture, ci.CRM_FIELD)
    reported = client.post(f"{PREFIX}/destinations/{row['id']}/report", json={"outcome": "updated"})
    assert reported.status_code == 200, reported.text
    assert reported.json()["crm_state"] == "updated"
    assert reported.json()["capture_agreement"]["agreed"] is True


# --------------------------------------------------------------------------- #
# Every write's audit row names a route the app actually serves
# --------------------------------------------------------------------------- #


def test_every_audit_row_this_feature_wrote_names_a_route_the_app_serves(
    client: TestClient, live_store: RecordStore, room_id: str
):
    """The property the feature host exists to protect."""
    served = {
        (method, shape["path"])
        for shape in client.get(f"/api/features/{FEATURE_ID}").json()["routes"]
        for method in shape["methods"]
    }
    assert served, "the registry reports no route for this feature"

    capture = _capture(client, room_id)
    row = _destination(capture, ci.CRM_FIELD)
    client.post(f"{PREFIX}/destinations/{row['id']}/report", json={"outcome": "failed"})

    rows = [
        audit
        for audit in live_store.db.audit()
        if str(audit.get("collection") or "").startswith("wf135")
    ]
    assert rows, "no audit rows were written for this feature's collections"

    checked = 0
    for audit in rows:
        source = str(audit.get("source") or "")
        if not source.startswith(("POST ", "PATCH ", "PUT ", "DELETE ")):
            continue
        method, _, path = source.partition(" ")
        assert (method, path) in served, f"audit row names a route the app does not serve: {source}"
        checked += 1

    assert checked >= 2, f"only {checked} route-sourced audit rows were checked"
