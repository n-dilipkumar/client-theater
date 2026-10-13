"""WF-117 HTTP tests: the mounted router, its errors and its audit trail.

The client is the conftest module-scoped one, repointed at a fresh database per
test, so each test sees only its own rows and the file passes alone. Opportunity
timestamps are computed relative to the wall clock at test time, so no assertion
depends on today's date being a particular day.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

PREFIX = "/api/wf-117"
OPPORTUNITY_COLLECTION = "crm_atomic_target"
ROOM_ID = "room_1"


def now() -> datetime:
    return datetime.now(timezone.utc)


def ago(days: float) -> str:
    return (now() - timedelta(days=days)).isoformat()


def make_opportunity(
    client,
    *,
    name="Northwind - Q4",
    stage="Qualification",
    stage_entered_days=30,
    last_activity_days=25,
    value=120000,
    owner="dana",
):
    payload = {
        "object": "Opportunity",
        "name": name,
        "stage": stage,
        "value": value,
        "owner": owner,
    }
    if stage_entered_days is not None:
        payload["stage_entered_at"] = ago(stage_entered_days)
    if last_activity_days is not None:
        payload["last_activity_at"] = ago(last_activity_days)
    response = client.post(
        f"/api/records/{OPPORTUNITY_COLLECTION}", json=payload, params={"room_id": ROOM_ID}
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def create_sweep(client, **overrides):
    payload = {"name": "Stale sweep", "room_id": ROOM_ID, "threshold_days": 7, "published": True}
    payload.update(overrides)
    response = client.post(f"{PREFIX}/sweeps", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


# --------------------------------------------------------------------------- #
# Discovery and reference data
# --------------------------------------------------------------------------- #


def test_the_router_is_mounted_by_discovery(client):
    response = client.get(f"{PREFIX}/summary")
    assert response.status_code == 200
    assert "opportunities" in response.json()


def test_the_feature_appears_in_the_registry(client):
    body = client.get("/api/features").json()
    ids = {feature.get("id") for feature in body.get("features", [])}
    assert "wf-117-stale-opportunities" in ids
    assert body.get("failed_count", 0) == 0


def test_the_vocabulary_serves_the_closed_sets(client):
    payload = client.get(f"{PREFIX}/vocabulary").json()
    assert payload["ticket"] == "WF-117"
    assert payload["triggers"][0] == "Stale Opportunities"
    assert payload["stale_references"] == ["last_activity", "stage_entry", "latest_of_both"]
    assert payload["default_stale_reference"] == "latest_of_both"
    assert payload["default_cadence"] == [
        {"after_days": 7, "action": "nudge"},
        {"after_days": 21, "action": "escalate"},
    ]
    assert payload["collections"]["sweep"] == "wf117_sweep"


def test_the_inferences_route_publishes_the_decisions(client):
    payload = client.get(f"{PREFIX}/inferences").json()
    assert payload["count"] >= 5
    ids = {entry["id"] for entry in payload["entries"]}
    assert "stale_reference_is_latest_of_both" in ids
    assert "refire_control_is_a_marker_on_the_opportunity" in ids


def test_the_opportunities_route_reads_the_wf039_collection(client):
    make_opportunity(client, name="Northwind - Q4")
    listed = client.get(f"{PREFIX}/opportunities", params={"room_id": ROOM_ID}).json()
    assert listed["count"] == 1
    assert listed["entries"][0]["name"] == "Northwind - Q4"


# --------------------------------------------------------------------------- #
# Sweeps
# --------------------------------------------------------------------------- #


def test_a_sweep_is_created_read_back_and_patched(client):
    sweep = create_sweep(client)
    assert sweep["id"].startswith("wf117_sweep_")
    assert sweep["threshold_days"] == 7
    assert client.get(f"{PREFIX}/sweeps/{sweep['id']}").json()["id"] == sweep["id"]

    patched = client.patch(f"{PREFIX}/sweeps/{sweep['id']}", json={"threshold_days": 14})
    assert patched.status_code == 200
    assert patched.json()["threshold_days"] == 14


def test_a_missing_sweep_is_a_404(client):
    assert client.get(f"{PREFIX}/sweeps/nope").status_code == 404
    assert client.post(f"{PREFIX}/sweeps/nope/run", json={}).status_code == 404
    assert client.patch(f"{PREFIX}/sweeps/nope", json={}).status_code == 404


def test_a_bad_threshold_is_a_422_with_the_field(client):
    response = client.post(f"{PREFIX}/sweeps", json={"threshold_days": 0})
    assert response.status_code == 422
    assert "threshold_days" in response.json()["errors"]


def test_publish_toggles_the_sweep(client):
    sweep = create_sweep(client, published=False)
    assert client.get(f"{PREFIX}/sweeps/{sweep['id']}").json()["published"] is False
    published = client.post(f"{PREFIX}/sweeps/{sweep['id']}/publish", json={"published": True})
    assert published.status_code == 200
    assert published.json()["published"] is True


# --------------------------------------------------------------------------- #
# Evaluate and run
# --------------------------------------------------------------------------- #


def test_evaluate_previews_without_writing(client):
    make_opportunity(client)
    sweep = create_sweep(client)
    evaluation = client.post(f"{PREFIX}/sweeps/{sweep['id']}/evaluate", json={}).json()
    assert evaluation["scanned"] == 1
    assert evaluation["matched"] == 1
    assert evaluation["decisions"][0]["reason"] == "matched"
    # No run was recorded.
    assert client.get(f"{PREFIX}/runs").json()["count"] == 0


def test_a_run_escalates_and_records_the_three_actions(client):
    make_opportunity(client)
    sweep = create_sweep(client)
    run = client.post(f"{PREFIX}/sweeps/{sweep['id']}/run", json={})
    assert run.status_code == 201, run.text
    body = run.json()
    assert body["scanned"] == 1
    assert body["escalated"] == 1
    assert body["results"][0]["step"] == "escalate"
    assert body["results"][0]["task_id"].startswith("wf116_task_")

    # The owner task landed in WF-116's store.
    tasks = client.get("/api/wf-116/tasks").json()
    assert tasks["count"] == 1

    escalations = client.get(f"{PREFIX}/escalations").json()
    assert escalations["count"] == 1
    assert escalations["entries"][0]["opportunity_id"] == body["results"][0]["opportunity_id"]

    notifications = client.get(f"{PREFIX}/notifications").json()
    assert notifications["count"] == 1
    assert notifications["entries"][0]["team"] == "sales"


def test_a_second_run_does_not_re_fire(client):
    make_opportunity(client)
    sweep = create_sweep(client)
    client.post(f"{PREFIX}/sweeps/{sweep['id']}/run", json={})
    second = client.post(f"{PREFIX}/sweeps/{sweep['id']}/run", json={}).json()
    assert second["escalated"] == 0
    assert second["by_reason"]["already_escalated"] == 1
    assert client.get("/api/wf-116/tasks").json()["count"] == 1


def test_an_unpublished_sweep_is_a_conflict(client):
    make_opportunity(client)
    sweep = create_sweep(client, published=False)
    response = client.post(f"{PREFIX}/sweeps/{sweep['id']}/run", json={})
    assert response.status_code == 409
    assert response.json()["reason"] == "sweep_not_published"


def test_the_audit_row_names_the_run_route(client):
    opportunity_id = make_opportunity(client)
    sweep = create_sweep(client)
    client.post(f"{PREFIX}/sweeps/{sweep['id']}/run", json={})
    audit = client.get("/api/audit", params={"record_id": opportunity_id}).json()
    sources = {entry["source"] for entry in audit["entries"]}
    assert f"POST {PREFIX}/sweeps/{sweep['id']}/run" in sources


def test_the_run_log_lists_and_reads_a_run(client):
    make_opportunity(client)
    sweep = create_sweep(client)
    run = client.post(f"{PREFIX}/sweeps/{sweep['id']}/run", json={}).json()
    listed = client.get(f"{PREFIX}/runs", params={"sweep_id": sweep["id"]}).json()
    assert listed["count"] == 1
    assert client.get(f"{PREFIX}/runs/{run['id']}").json()["escalated"] == 1
    assert client.get(f"{PREFIX}/runs/nope").status_code == 404


def test_the_summary_counts_are_served(client):
    make_opportunity(client)
    sweep = create_sweep(client)
    client.post(f"{PREFIX}/sweeps/{sweep['id']}/run", json={})
    summary = client.get(f"{PREFIX}/summary").json()
    assert summary["sweeps"] == 1
    assert summary["published"] == 1
    assert summary["opportunities"] == 1
    assert summary["escalated_total"] == 1


# --------------------------------------------------------------------------- #
# Route health
# --------------------------------------------------------------------------- #


def test_every_advertised_route_answers_without_a_5xx(client):
    get_paths = [
        "/vocabulary",
        "/inferences",
        "/opportunities",
        "/sweeps",
        "/sweeps/nope",
        "/runs",
        "/runs/nope",
        "/escalations",
        "/notifications",
        "/summary",
    ]
    for path in get_paths:
        response = client.get(f"{PREFIX}{path}")
        assert response.status_code < 500, f"GET {path} answered {response.status_code}"

    post_paths = [
        "/sweeps",
        "/sweeps/nope/publish",
        "/sweeps/nope/evaluate",
        "/sweeps/nope/run",
    ]
    for path in post_paths:
        response = client.post(f"{PREFIX}{path}", json={})
        assert response.status_code < 500, f"POST {path} answered {response.status_code}"

    assert client.patch(f"{PREFIX}/sweeps/nope", json={}).status_code < 500
    assert client.delete(f"{PREFIX}/sweeps/nope").status_code < 500
