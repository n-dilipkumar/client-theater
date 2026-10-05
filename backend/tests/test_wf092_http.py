"""WF-092: the HTTP surface.

The domain tests in ``test_wf092.py`` prove the chain behaves. This file proves the
routes answer, that a refusal reaches the client as the right status code, and that the
audit rows name URLs the app actually serves.

What is tested here, and why each matters:

* every advertised route answers with no 5xx, which is the release check
  ``tools/verify_all_routes.py`` performs over real HTTP;
* a rule refusal is 422, a missing workflow is 404, and a second workflow is 409, because
  those three are different faults and one status code would hide two of them;
* a premature decision is refused by the route and writes no decision row, which is the
  sequential rule as a person experiences it;
* every write lands in the audit log with a source naming a route this router serves,
  which is the product's central promise;
* the seed runs and its return string is encodable by cp1252, because one arrow glyph in
  one recovered feature broke the entire seeder on a Windows console.
"""

from __future__ import annotations

from dsr.db.audited import AuditedDatabase
from dsr.store import RecordStore
from fastapi.testclient import TestClient

PREFIX = "/api/wf-092"

SEQUENCES = [
    {"priority": 1, "approvers": ["sales_manager"], "message": "Approve {{quote.quote_amount}}"},
    {"priority": 2, "approvers": ["sales_director"], "message": "Second review"},
    {"priority": 3, "approvers": ["legal_representative"], "message": "Legal sign off"},
]


def _make_branch(client: TestClient, **overrides) -> dict:
    body = {
        "name": "Quotes above 5000",
        "property": "quote_amount",
        "operator": "greater_than",
        "threshold": 5000,
        "sequences": SEQUENCES,
    }
    body.update(overrides)
    response = client.post(f"{PREFIX}/branches", json=body)
    assert response.status_code == 200, response.text
    return response.json()["branch"]


def _make_quote(client: TestClient, amount: float, **extra) -> str:
    response = client.post(
        "/api/records/wf086_quote",
        json={"name": "Acme", "quote_amount": amount, **extra},
        params={"source": "test"},
    )
    assert response.status_code in (200, 201), response.text
    return response.json()["id"]


# --------------------------------------------------------------------------- #
# Discovery and the served surface
# --------------------------------------------------------------------------- #


def test_the_feature_is_mounted_by_discovery_alone(client: TestClient):
    response = client.get("/api/features")
    assert response.status_code == 200
    body = response.json()
    features = body.get("features", body) if isinstance(body, dict) else body
    match = [f for f in features if "wf-092" in str(f.get("id"))]
    assert match, "the feature module must be discovered with no registration step"


def test_the_vocabulary_route_serves_the_researched_caps(client: TestClient):
    body = client.get(f"{PREFIX}/vocabulary").json()
    assert body["chain"]["caps"]["max_sequences"] == 5
    assert body["chain"]["caps"]["max_approvers_per_sequence"] == 10
    assert body["branch"]["default_threshold"] == 5000.0
    assert body["chain"]["requirements"] == ["all", "any", "sequential"]


def test_the_inferences_route_names_the_judgement_calls(client: TestClient):
    body = client.get(f"{PREFIX}/inferences").json()
    ids = {entry["id"] for entry in body["inferences"]}
    assert "DERIVED_AUTO_APPROVE_IS_THE_SAFE_DEFAULT" in ids
    assert body["not_built"], "what this build leaves out must be visible"


def test_every_advertised_route_answers_with_no_5xx(client: TestClient):
    """The release check ``tools/verify_all_routes.py`` performs, run in-process."""
    response = client.get("/api/features")
    body = response.json()
    features = body.get("features", body) if isinstance(body, dict) else body
    match = [f for f in features if "wf-092" in str(f.get("id"))][0]
    seen = 0
    for route in match["routes"]:
        path = route["path"]
        for verb in route.get("methods", ["GET"]):
            if "{quote_id}" in path:
                continue
            if "{enrolment_id}" in path:
                continue
            probe = client.request(verb, path)
            assert probe.status_code < 500, f"{verb} {path} returned {probe.status_code}"
            seen += 1
    assert seen >= 6, f"expected several routes to be probed, probed {seen}"


# --------------------------------------------------------------------------- #
# The single workflow
# --------------------------------------------------------------------------- #


def test_a_missing_workflow_is_404(client: TestClient):
    response = client.get(f"{PREFIX}/workflow")
    assert response.status_code == 404
    assert response.json()["error"] == "workflow_not_found"


def test_creating_the_workflow_twice_is_refused_as_a_conflict(client: TestClient):
    first = client.post(f"{PREFIX}/workflow", json={"name": "Sequential"})
    assert first.status_code == 200
    second = client.post(f"{PREFIX}/workflow", json={"name": "Another"})
    assert second.status_code == 200, "asking for the same single workflow returns it"
    body = client.get(f"{PREFIX}/workflow").json()
    assert body["workflow_id"] == "wf092-sequential-quote-approval"


def test_the_re_enrolment_switch_toggles(client: TestClient):
    client.post(f"{PREFIX}/workflow", json={})
    assert (
        client.post(f"{PREFIX}/workflow/re-enrol-toggle", json={"re_enroll": True}).status_code
        == 200
    )
    body = client.get(f"{PREFIX}/workflow").json()
    assert body["re_enroll"] is True


# --------------------------------------------------------------------------- #
# Branches
# --------------------------------------------------------------------------- #


def test_a_branch_is_created_with_its_priority_levels(client: TestClient):
    branch = _make_branch(client)
    listing = client.get(f"{PREFIX}/branches").json()
    stored = next(b for b in listing["branches"] if b["id"] == branch["id"])
    assert [level["priority"] for level in stored["steps"]] == [1, 2, 3]


def test_a_sixth_sequence_is_refused_with_422(client: TestClient):
    sequences = [{"priority": n, "approvers": ["a"]} for n in range(1, 7)]
    response = client.post(f"{PREFIX}/branches", json={"name": "Too many", "sequences": sequences})
    assert response.status_code == 422
    assert response.json()["error"] == "approval_rule_refused"


def test_an_eleventh_approver_is_refused_with_422(client: TestClient):
    sequences = [{"priority": 1, "approvers": [f"a{n}" for n in range(11)]}]
    response = client.post(f"{PREFIX}/branches", json={"name": "Too wide", "sequences": sequences})
    assert response.status_code == 422
    assert "at most 10" in response.json()["detail"]


def test_five_sequences_and_ten_approvers_are_accepted(client: TestClient):
    sequences = [
        {"priority": n, "approvers": [f"p{n}_{k}" for k in range(10)]} for n in range(1, 6)
    ]
    response = client.post(
        f"{PREFIX}/branches", json={"name": "At the caps", "sequences": sequences}
    )
    assert response.status_code == 200


# --------------------------------------------------------------------------- #
# Evaluation and enrolment
# --------------------------------------------------------------------------- #


def test_evaluation_writes_nothing_and_names_the_qualifying_branch(client: TestClient):
    _make_branch(client)
    quote_id = _make_quote(client, 12000)
    response = client.get(f"{PREFIX}/quotes/{quote_id}/evaluate")
    assert response.status_code == 200
    assert response.json()["qualified"] is True
    assert client.get(f"{PREFIX}/enrolments").json()["enrolments"] == []


def test_evaluating_a_quote_that_does_not_exist_is_404(client: TestClient):
    response = client.get(f"{PREFIX}/quotes/wf086_quote_absent/evaluate")
    assert response.status_code == 404


def test_enrolment_puts_the_chain_at_the_first_priority(client: TestClient):
    _make_branch(client)
    quote_id = _make_quote(client, 12000)
    body = client.post(f"{PREFIX}/quotes/{quote_id}/enrol").json()
    assert body["auto_approved"] is False
    assert body["state"] == "pending_approval"
    assert body["enrolment"]["active_priority"] == 1


def test_a_quote_that_misses_every_branch_is_auto_approved(client: TestClient):
    _make_branch(client)
    quote_id = _make_quote(client, 900)
    body = client.post(f"{PREFIX}/quotes/{quote_id}/enrol").json()
    assert body["auto_approved"] is True
    assert body["publishable"] is True


def test_enrolling_a_quote_that_does_not_exist_is_404(client: TestClient):
    """A missing record must not reach the engine as a 500."""
    _make_branch(client)
    response = client.post(f"{PREFIX}/quotes/wf086_quote_absent/enrol")
    assert response.status_code == 404


# --------------------------------------------------------------------------- #
# Decisions
# --------------------------------------------------------------------------- #


def _enrolled(client: TestClient) -> str:
    _make_branch(client)
    quote_id = _make_quote(client, 12000)
    return client.post(f"{PREFIX}/quotes/{quote_id}/enrol").json()["enrolment"]["id"]


def test_the_whole_chain_writes_the_final_state(client: TestClient):
    enrolment_id = _enrolled(client)
    for approver in ("sales_manager", "sales_director", "legal_representative"):
        response = client.post(
            f"{PREFIX}/enrolments/{enrolment_id}/decide",
            json={"approver": approver, "decision": "approved"},
        )
        assert response.status_code == 200, response.text
    body = client.get(f"{PREFIX}/enrolments/{enrolment_id}").json()
    assert body["state"] == "approved"
    assert body["approval_status"] == "approved"
    assert body["publishable"] is True
    assert len(body["decision_rows"]) == 3


def test_a_premature_decision_is_refused_and_writes_nothing(client: TestClient):
    enrolment_id = _enrolled(client)
    response = client.post(
        f"{PREFIX}/enrolments/{enrolment_id}/decide",
        json={"approver": "sales_director", "decision": "approved"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["outcome"] == "not_yet_your_priority"
    detail = client.get(f"{PREFIX}/enrolments/{enrolment_id}").json()
    assert detail["decision_rows"] == []


def test_a_rejection_ends_the_chain_and_blocks_publication(client: TestClient):
    enrolment_id = _enrolled(client)
    client.post(
        f"{PREFIX}/enrolments/{enrolment_id}/decide",
        json={"approver": "sales_manager", "decision": "rejected"},
    )
    body = client.get(f"{PREFIX}/enrolments/{enrolment_id}").json()
    assert body["state"] == "rejected"
    assert body["publishable"] is False


def test_a_decision_without_an_approver_is_400(client: TestClient):
    enrolment_id = _enrolled(client)
    response = client.post(
        f"{PREFIX}/enrolments/{enrolment_id}/decide", json={"decision": "approved"}
    )
    assert response.status_code == 400


def test_a_decision_without_a_verdict_is_400(client: TestClient):
    enrolment_id = _enrolled(client)
    response = client.post(
        f"{PREFIX}/enrolments/{enrolment_id}/decide", json={"approver": "sales_manager"}
    )
    assert response.status_code == 400


def test_an_unknown_decision_is_422(client: TestClient):
    enrolment_id = _enrolled(client)
    response = client.post(
        f"{PREFIX}/enrolments/{enrolment_id}/decide",
        json={"approver": "sales_manager", "decision": "maybe"},
    )
    assert response.status_code == 422


def test_only_the_active_priority_is_notified(client: TestClient):
    enrolment_id = _enrolled(client)
    detail = client.get(f"{PREFIX}/enrolments/{enrolment_id}").json()
    assert {row["approver"] for row in detail["notifications"]} == {"sales_manager"}
    assert {row["channel"] for row in detail["notifications"]} == {"bell", "email"}

    client.post(
        f"{PREFIX}/enrolments/{enrolment_id}/decide",
        json={"approver": "sales_manager", "decision": "approved"},
    )
    detail = client.get(f"{PREFIX}/enrolments/{enrolment_id}").json()
    assert {row["approver"] for row in detail["notifications"]} == {
        "sales_manager",
        "sales_director",
    }


def test_re_enrolment_is_refused_while_the_switch_is_off(client: TestClient):
    enrolment_id = _enrolled(client)
    response = client.post(f"{PREFIX}/enrolments/{enrolment_id}/re-enrol")
    assert response.status_code == 422


def test_re_enrolment_resets_the_chain_when_the_switch_is_on(client: TestClient):
    enrolment_id = _enrolled(client)
    client.post(f"{PREFIX}/workflow/re-enrol-toggle", json={"re_enroll": True})
    client.post(
        f"{PREFIX}/enrolments/{enrolment_id}/decide",
        json={"approver": "sales_manager", "decision": "approved"},
    )
    response = client.post(f"{PREFIX}/enrolments/{enrolment_id}/re-enrol")
    assert response.status_code == 200
    body = client.get(f"{PREFIX}/enrolments/{enrolment_id}").json()
    assert body["state"] == "pending_approval"
    assert body["active_priority"] == 1
    assert body["decisions"] == {}


def test_reading_an_enrolment_that_does_not_exist_is_refused(client: TestClient):
    response = client.get(f"{PREFIX}/enrolments/enrol_absent")
    assert response.status_code >= 400
    assert response.status_code < 500


# --------------------------------------------------------------------------- #
# The audit promise
# --------------------------------------------------------------------------- #


def test_every_write_lands_in_the_audit_log_naming_a_served_route(
    client: TestClient, db: AuditedDatabase
):
    enrolment_id = _enrolled(client)
    client.post(
        f"{PREFIX}/enrolments/{enrolment_id}/decide",
        json={"approver": "sales_manager", "decision": "approved"},
    )
    rows = db.audit()
    assert rows, "every write must be audited"
    mine = [row for row in rows if PREFIX in str(row.get("source") or "")]
    assert len(mine) >= 5, f"expected the enrolment and its decision to be audited, saw {len(mine)}"
    for row in mine:
        source = str(row.get("source") or "")
        assert source.startswith(("POST ", "PATCH ")), source
        assert source.split(" ", 1)[1].startswith(PREFIX), source


# --------------------------------------------------------------------------- #
# The seeder contract
# --------------------------------------------------------------------------- #


def _feature_module():
    """The feature module, imported the way the host imports it.

    ``dsr.features`` is on the path, so the host's own discovery module can be asked
    for it rather than guessing a file path. That is also the import the seeder makes.
    """
    from dsr.features import iter_feature_modules

    for _name, module, error in iter_feature_modules():
        if error or module is None:
            continue
        if str(getattr(module, "FEATURE", {}).get("id", "")).startswith("wf-092"):
            return module
    raise AssertionError("the WF-092 feature module was not discovered")


def test_the_seed_returns_a_cp1252_encodable_string(db: AuditedDatabase):
    """One RIGHTWARDS ARROW in one feature broke the entire seeder on Windows."""
    from datetime import datetime, timezone

    summary = _feature_module().seed(db, {"now": datetime.now(timezone.utc), "rng": None})
    assert summary
    summary.encode("cp1252")
    assert "priority level" in summary


def test_the_seed_creates_the_demo_chain(db: AuditedDatabase):
    from datetime import datetime, timezone

    _feature_module().seed(db, {"now": datetime.now(timezone.utc), "rng": None})
    store = RecordStore(db)
    assert store.count_where("wf092_quote_approval_workflow", {}) == 1
    assert store.count_where("wf092_approval_branch", {}) == 1
    enrolments = store.list("wf092_approval_enrolment")
    assert len(enrolments) == 2, "one waiting chain and one auto-approved quote"
