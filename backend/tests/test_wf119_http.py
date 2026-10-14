"""WF-119: the HTTP surface.

The domain tests in ``test_wf119.py`` prove the envelope, the resume rules and
the engine. This file proves the routes answer, that each refusal reaches the
client as the right status code, and that every audit row names a URL the app
actually serves.

What is tested here, and why each matters:

* every advertised route answers with no 5xx, which is the release check
  ``tools/verify_all_routes.py`` performs over real HTTP;
* the pause, the two resume webhooks and the in-app resume all write an audit row
  whose ``source`` is a served route, which is the product's central promise;
* the documented envelope is refused before a contact is held, so a malformed
  body cannot create a hold nobody can resume;
* the correlation key is verified against the stored hold, so a resume with the
  wrong ``extras`` is a 404 rather than a decision on someone else's contact;
* a holder already held is a 409, a branch the gate never declared is a 422, and a
  body outcome that disagrees with the URL is a 422, because those are three
  different faults and one status would hide two of them;
* a retried resume is an idempotent replay and a decision flip is a 409;
* the seed runs and its return string is encodable by cp1252, because one arrow
  glyph in one recovered feature broke the entire seeder on a Windows console.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from dsr.approval_gate import vocabulary as vocab
from dsr.approval_gate.engine import ApprovalGate, seed
from dsr.store import RecordStore
from fastapi.testclient import TestClient

PREFIX = "/api/wf-119"
FEATURE_ID = "wf-119-approval-gate"

GATE_BODY = {
    "key": "send_proposal",
    "workflow_id": "wf-demo",
    "action_key": "pause_for_approval",
    "name": "Approve before the proposal is sent",
    "pause_execution": True,
    "branches": {vocab.OUTCOME_APPROVED: "branch_yes", vocab.OUTCOME_REFUSED: "branch_no"},
}

EXTRAS = {"locationId": "loc_northwind", "contactId": "con_1", "workflowId": "wf-demo"}


def _envelope(contact: str = "con_1") -> dict:
    return {
        vocab.ENVELOPE_DATA: {"amount": 24000, "owner": "dana"},
        vocab.ENVELOPE_EXTRAS: {**EXTRAS, vocab.EXTRAS_CONTACT: contact},
        vocab.ENVELOPE_META: {vocab.META_KEY: "proposal-approval", vocab.META_VERSION: 1},
    }


def _create_gate(client: TestClient, **overrides) -> dict:
    body = dict(GATE_BODY)
    body.update(overrides)
    response = client.post(f"{PREFIX}/gates", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def _pause(client: TestClient, contact: str = "con_1") -> dict:
    response = client.post(f"{PREFIX}/gates/send_proposal/pause", json=_envelope(contact))
    assert response.status_code == 200, response.text
    return response.json()


def _feature(client: TestClient) -> dict:
    body = client.get("/api/features").json()
    match = [f for f in body["features"] if f.get("id") == FEATURE_ID]
    assert match, "the feature module must be discovered with no registration step"
    return match[0]


# --------------------------------------------------------------------------- #
# Discovery and the served surface
# --------------------------------------------------------------------------- #


def test_the_feature_is_mounted_by_discovery_alone(client: TestClient):
    feature = _feature(client)
    assert feature["prefix"] == PREFIX
    assert feature["routes"], "the registry must advertise the routes"


def test_every_advertised_route_answers_with_no_5xx(client: TestClient):
    """The release check ``tools/verify_all_routes.py`` performs, run in-process."""
    feature = _feature(client)
    substitutions = {
        "{key}": "absent",
        "{hold_id}": "wf119_hold_absent",
        "{token}": "absent",
        "{outcome}": vocab.OUTCOME_APPROVED,
    }
    seen = 0
    for route in feature["routes"]:
        path = route["path"]
        for placeholder, value in substitutions.items():
            path = path.replace(placeholder, value)
        for verb in route.get("methods", ["GET"]):
            body = {"json": {}} if verb in ("POST", "PATCH", "PUT") else {}
            probe = client.request(verb, path, **body)
            assert probe.status_code < 500, f"{verb} {path} returned {probe.status_code}"
            seen += 1
    assert seen >= 10, f"expected the whole surface to be probed, probed {seen}"


def test_the_vocabulary_route_serves_the_researched_rules(client: TestClient):
    body = client.get(f"{PREFIX}/vocabulary").json()
    assert body["collections"]["gates"] == "wf119_approval_gate"
    assert body["envelope"]["extras_keys"] == ["locationId", "contactId", "workflowId"]
    assert body["envelope"]["branch_key"] == "branchId"
    assert body["hold_window"]["default_seconds"] == 259_200
    assert body["resume"]["mode"] == "async"
    assert body["resume"]["success_url_outcome"] == "approved"
    assert body["resume"]["failed_url_outcome"] == "refused"
    assert body["pause_execution_key"] == "pause_execution"


def test_the_decisions_route_names_the_judgement_calls(client: TestClient):
    body = client.get(f"{PREFIX}/decisions").json()
    assert body["resume_mode"]["audit_id"] == "jev-20261014T105812-18180-92928"
    assert body["resume_mode"]["rejected"] == "sync"
    ids = {entry["id"] for entry in body["decisions"]}
    assert "RESUME_MODE_IS_ASYNC" in ids
    assert body["not_built"], "what this build leaves out must be visible"


def test_the_summary_route_answers_before_anything_exists(client: TestClient):
    body = client.get(f"{PREFIX}/summary").json()
    assert body["gates"] == 0
    assert body["holds"] == 0
    assert body["resume_mode"] == "async"


# --------------------------------------------------------------------------- #
# Gates
# --------------------------------------------------------------------------- #


def test_a_gate_is_created_and_read_back_with_its_two_resume_urls(client: TestClient):
    created = _create_gate(client)
    assert created["key"] == "send_proposal"
    assert created["action_url"] == f"{PREFIX}/gates/send_proposal/pause"
    assert created["resume_urls"]["approved"].endswith("/approved")
    assert created["resume_urls"]["refused"].endswith("/refused")
    assert created["hold_seconds"] == 259_200
    fetched = client.get(f"{PREFIX}/gates/send_proposal").json()
    assert fetched["resume_urls"] == created["resume_urls"]
    listing = client.get(f"{PREFIX}/gates").json()
    assert listing["count"] == 1


def test_a_second_gate_with_the_same_key_is_409(client: TestClient):
    _create_gate(client)
    response = client.post(f"{PREFIX}/gates", json=GATE_BODY)
    assert response.status_code == 409
    assert response.json()["error"] == "gate_key_taken"


def test_a_gate_that_misses_a_branch_is_422_with_every_problem(client: TestClient):
    body = {**GATE_BODY, "branches": {vocab.OUTCOME_APPROVED: "branch_yes"}}
    response = client.post(f"{PREFIX}/gates", json=body)
    assert response.status_code == 422
    payload = response.json()
    assert payload["error"] == "gate_invalid"
    assert any("refused" in problem for problem in payload["problems"])


def test_an_unknown_gate_is_404(client: TestClient):
    response = client.get(f"{PREFIX}/gates/send_proposal")
    assert response.status_code == 404
    assert response.json()["error"] == "gate_not_found"


def test_the_pause_toggle_can_be_turned_on_over_http(client: TestClient):
    _create_gate(client, pause_execution=False, key="send_invoice")
    response = client.patch(f"{PREFIX}/gates/send_invoice", json={"pause_execution": True})
    assert response.status_code == 200
    assert response.json()["pause_execution"] is True


# --------------------------------------------------------------------------- #
# The pause
# --------------------------------------------------------------------------- #


def test_the_action_holds_the_contact_and_reports_the_resume_urls(client: TestClient):
    _create_gate(client)
    held = _pause(client)
    assert held["held"] is True
    assert held["hold"]["state"] == vocab.STATE_HELD
    assert held["hold"][vocab.ENVELOPE_EXTRAS] == EXTRAS
    assert held["resume_mode"] == "async"
    assert held["resume_urls"]["approved"].startswith(f"{PREFIX}/hooks/")


def test_a_gate_with_pause_off_holds_nobody(client: TestClient):
    _create_gate(client, pause_execution=False, key="send_invoice")
    response = client.post(f"{PREFIX}/gates/send_invoice/pause", json=_envelope())
    assert response.status_code == 409
    assert response.json()["error"] == "pause_disabled"


def test_a_broken_envelope_is_422_and_creates_no_hold(client: TestClient):
    _create_gate(client)
    body = _envelope()
    del body[vocab.ENVELOPE_EXTRAS][vocab.EXTRAS_CONTACT]
    response = client.post(f"{PREFIX}/gates/send_proposal/pause", json=body)
    assert response.status_code == 422
    payload = response.json()
    assert payload["error"] == "envelope_invalid"
    assert any("contactId" in problem for problem in payload["problems"])
    assert client.get(f"{PREFIX}/holds").json()["count"] == 0


def test_a_second_pause_for_a_held_contact_is_409(client: TestClient):
    _create_gate(client)
    _pause(client)
    response = client.post(f"{PREFIX}/gates/send_proposal/pause", json=_envelope())
    assert response.status_code == 409
    assert response.json()["error"] == "already_held"


# --------------------------------------------------------------------------- #
# The resume webhooks
# --------------------------------------------------------------------------- #


def test_the_success_webhook_resumes_on_the_approved_branch(client: TestClient):
    gate = _create_gate(client)
    _pause(client)
    response = client.post(
        gate["resume_urls"]["approved"],
        json={"extras": dict(EXTRAS), vocab.BRANCH_KEY: "branch_yes"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["outcome"] == "approved"
    assert body["branch"] == "branch_yes"
    assert body["replay"] is False
    assert body["hold"]["state"] == vocab.STATE_RESUMED


def test_the_failed_webhook_resumes_on_the_refused_branch(client: TestClient):
    gate = _create_gate(client)
    _pause(client)
    response = client.post(
        gate["resume_urls"]["refused"],
        json={"extras": dict(EXTRAS), vocab.BRANCH_KEY: "branch_no"},
    )
    assert response.status_code == 200
    assert response.json()["outcome"] == "refused"
    assert response.json()["branch"] == "branch_no"


def test_an_unknown_resume_token_is_404(client: TestClient):
    response = client.post(f"{PREFIX}/hooks/nope/approved", json={"extras": dict(EXTRAS)})
    assert response.status_code == 404
    assert response.json()["error"] == "gate_not_found"


def test_an_unknown_correlation_key_is_404(client: TestClient):
    gate = _create_gate(client)
    _pause(client)
    response = client.post(
        gate["resume_urls"]["approved"],
        json={"extras": {**EXTRAS, vocab.EXTRAS_CONTACT: "con_nobody"}},
    )
    assert response.status_code == 404
    assert response.json()["error"] == "hold_not_found"
    assert client.get(f"{PREFIX}/holds").json()["holds"][0]["state"] == vocab.STATE_HELD


def test_a_branch_the_gate_never_declared_is_422(client: TestClient):
    gate = _create_gate(client)
    _pause(client)
    response = client.post(
        gate["resume_urls"]["approved"],
        json={"extras": dict(EXTRAS), vocab.BRANCH_KEY: "branch_smuggled"},
    )
    assert response.status_code == 422
    assert response.json()["error"] == "branch_unknown"


def test_a_body_outcome_that_disagrees_with_the_url_is_422(client: TestClient):
    gate = _create_gate(client)
    _pause(client)
    response = client.post(
        gate["resume_urls"]["approved"],
        json={"extras": dict(EXTRAS), "outcome": "refused", vocab.BRANCH_KEY: "branch_yes"},
    )
    assert response.status_code == 422
    assert response.json()["error"] == "outcome_conflict"


def test_a_retried_webhook_is_an_idempotent_replay(client: TestClient):
    gate = _create_gate(client)
    _pause(client)
    first = client.post(
        gate["resume_urls"]["approved"],
        json={"extras": dict(EXTRAS), vocab.BRANCH_KEY: "branch_yes"},
    ).json()
    second = client.post(
        gate["resume_urls"]["approved"],
        json={"extras": dict(EXTRAS), vocab.BRANCH_KEY: "branch_yes"},
    )
    assert second.status_code == 200
    body = second.json()
    assert body["replay"] is True
    assert body["resume"]["id"] == first["resume"]["id"]
    assert body["hold"]["resumed_at"] == first["hold"]["resumed_at"]


def test_a_webhook_that_changes_a_recorded_decision_is_409(client: TestClient):
    gate = _create_gate(client)
    _pause(client)
    client.post(
        gate["resume_urls"]["approved"],
        json={"extras": dict(EXTRAS), vocab.BRANCH_KEY: "branch_yes"},
    )
    response = client.post(
        gate["resume_urls"]["refused"],
        json={"extras": dict(EXTRAS), vocab.BRANCH_KEY: "branch_no"},
    )
    assert response.status_code == 409
    assert response.json()["error"] == "resume_conflict"


def test_an_in_app_resume_decides_the_hold(client: TestClient):
    _create_gate(client)
    held = _pause(client)
    response = client.post(
        f"{PREFIX}/holds/{held['hold']['id']}/resume",
        json={"outcome": "refused", vocab.BRANCH_KEY: "branch_no", "approver": "ravi"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["outcome"] == "refused"
    assert body["resume"]["approver"] == "ravi"


def test_an_in_app_resume_of_an_unknown_hold_is_404(client: TestClient):
    response = client.post(f"{PREFIX}/holds/wf119_hold_absent/resume", json={"outcome": "approved"})
    assert response.status_code == 404
    assert response.json()["error"] == "hold_not_found"


# --------------------------------------------------------------------------- #
# Expiry
# --------------------------------------------------------------------------- #


def _a_month_later(_self) -> datetime:
    return datetime.now(timezone.utc) + timedelta(days=30)


def test_a_resume_after_the_window_lapsed_is_409_and_marks_the_hold(
    client: TestClient, monkeypatch
):
    gate = _create_gate(client)
    _pause(client)
    monkeypatch.setattr(ApprovalGate, "moment", _a_month_later)
    response = client.post(
        gate["resume_urls"]["approved"],
        json={"extras": dict(EXTRAS), vocab.BRANCH_KEY: "branch_yes"},
    )
    assert response.status_code == 409
    assert response.json()["error"] == "hold_expired"
    hold = client.get(f"{PREFIX}/holds").json()["holds"][0]
    assert hold["state"] == vocab.STATE_EXPIRED
    assert hold["expiry_reason"] == "resume_after_lapse"


def test_a_lapsed_hold_reads_as_expired_before_any_sweep(client: TestClient, monkeypatch):
    _create_gate(client)
    _pause(client)
    monkeypatch.setattr(ApprovalGate, "moment", _a_month_later)
    hold = client.get(f"{PREFIX}/holds").json()["holds"][0]
    assert hold["expired"] is True
    assert hold["can_resume"] is False
    assert client.get(f"{PREFIX}/holds/{hold['id']}").json()["expired"] is True


def test_the_sweep_expires_a_lapsed_hold(client: TestClient, monkeypatch):
    _create_gate(client)
    _pause(client)
    monkeypatch.setattr(ApprovalGate, "moment", _a_month_later)
    response = client.post(f"{PREFIX}/sweep")
    assert response.status_code == 200
    assert response.json()["expired"] == 1
    assert client.post(f"{PREFIX}/sweep").json()["expired"] == 0


# --------------------------------------------------------------------------- #
# Reads
# --------------------------------------------------------------------------- #


def test_holds_can_be_filtered_by_gate_and_state(client: TestClient):
    _create_gate(client)
    _pause(client)
    assert client.get(f"{PREFIX}/holds", params={"gate_key": "send_proposal"}).json()["count"] == 1
    assert client.get(f"{PREFIX}/holds", params={"gate_key": "elsewhere"}).json()["count"] == 0
    assert client.get(f"{PREFIX}/holds", params={"state": "held"}).json()["count"] == 1
    assert client.get(f"{PREFIX}/holds", params={"state": "resumed"}).json()["count"] == 0


def test_the_events_route_reads_the_sequence_for_one_hold(client: TestClient):
    _create_gate(client)
    held = _pause(client)
    body = client.get(f"{PREFIX}/holds/{held['hold']['id']}/events").json()
    assert body["count"] == 1
    assert body["events"][0]["event"] == "paused"
    assert body["events"][0]["hold_id"] == held["hold"]["id"]


def test_the_events_route_is_404_for_an_unknown_hold(client: TestClient):
    response = client.get(f"{PREFIX}/holds/wf119_hold_absent/events")
    assert response.status_code == 404


# --------------------------------------------------------------------------- #
# The audit trail
# --------------------------------------------------------------------------- #


def _template_matches(template: str, concrete: str) -> bool:
    """Does a concrete request path fit an advertised route template?"""
    wanted = template.strip("/").split("/")
    got = concrete.strip("/").split("/")
    if len(wanted) != len(got):
        return False
    return all(
        part.startswith("{") or part == actual for part, actual in zip(wanted, got, strict=True)
    )


def test_every_write_names_a_route_the_app_actually_serves(client: TestClient, db):
    """The product's central promise, checked against the route registry."""
    advertised = [route["path"] for route in _feature(client)["routes"]]
    gate = _create_gate(client)
    _pause(client)
    client.post(
        gate["resume_urls"]["approved"],
        json={"extras": dict(EXTRAS), vocab.BRANCH_KEY: "branch_yes"},
    )
    client.post(f"{PREFIX}/gates/send_proposal/pause", json=_envelope("con_2"))
    client.post(f"{PREFIX}/sweep")
    client.patch(f"{PREFIX}/gates/send_proposal", json={"name": "Renamed"})

    rows = [
        row
        for row in db.audit(limit=500)
        if str(row.get("collection") or "").startswith("wf119") and row.get("source") != "seed"
    ]
    assert len(rows) >= 8, f"expected several audited writes, saw {len(rows)}"
    for row in rows:
        source = str(row["source"])
        method, _, path = source.partition(" ")
        assert method in {"POST", "PATCH", "PUT", "DELETE", "GET"}, source
        assert any(_template_matches(template, path) for template in advertised), source


def test_a_refusal_writes_no_decision_row(client: TestClient, db):
    gate = _create_gate(client)
    _pause(client)
    client.post(
        gate["resume_urls"]["approved"],
        json={"extras": dict(EXTRAS), vocab.BRANCH_KEY: "branch_smuggled"},
    )
    assert db.count_where("wf119_resume", {}) == 0
    assert db.count_where("wf119_hold", {"state": "resumed"}) == 0


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #


def test_the_seed_runs_and_prints_a_cp1252_encodable_state(db):
    summary = seed(db, {"now": datetime(2026, 10, 14, 9, 0, tzinfo=timezone.utc)})
    assert isinstance(summary, str) and summary
    summary.encode("cp1252")
    assert seed(db, {"now": datetime(2026, 10, 14, 9, 0, tzinfo=timezone.utc)}) == "already seeded"
    engine = ApprovalGate(RecordStore(db))
    states = sorted(view["state"] for view in engine.list_holds())
    assert states == [vocab.STATE_EXPIRED, vocab.STATE_HELD, vocab.STATE_RESUMED]
    assert "expired with no decision" in summary
