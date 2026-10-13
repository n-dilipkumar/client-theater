"""WF-120 HTTP tests: the mounted router, its errors and its audit trail.

The client is the conftest module-scoped one, repointed at a fresh database per
test, so each test sees only its own rows and the file passes alone. Every
timestamp is computed relative to the wall clock at test time, so no assertion
depends on today's date being a particular day.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

PREFIX = "/api/wf-120"
CRM_COLLECTION = "crm_atomic_target"
ROOM_ID = "room_1"


def now() -> datetime:
    return datetime.now(timezone.utc)


def make_contact(
    client,
    *,
    name="Northwind - Ada",
    stage="Qualification",
    lifecycle_stage="Lead",
    risk="low",
    owner="dana",
    object_name="Contact",
):
    payload = {
        "object": object_name,
        "name": name,
        "stage": stage,
        "lifecycle_stage": lifecycle_stage,
        "risk": risk,
        "owner": owner,
    }
    response = client.post(
        f"/api/records/{CRM_COLLECTION}", json=payload, params={"room_id": ROOM_ID}
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def create_schedule(client, **overrides):
    payload = {"name": "Scheduled enrollment", "room_id": ROOM_ID, "object": "Contact"}
    payload.update(overrides)
    response = client.post(f"{PREFIX}/schedules", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def publish(client, schedule_id, backfill="existing_and_future"):
    response = client.post(
        f"{PREFIX}/schedules/{schedule_id}/publish", json={"published": True, "backfill": backfill}
    )
    assert response.status_code == 200, response.text
    return response.json()


# --------------------------------------------------------------------------- #
# Discovery and reference data
# --------------------------------------------------------------------------- #


def test_the_router_is_mounted_by_discovery(client):
    response = client.get(f"{PREFIX}/summary")
    assert response.status_code == 200
    assert "schedules" in response.json()


def test_the_feature_appears_in_the_registry(client):
    body = client.get("/api/features").json()
    ids = {feature.get("id") for feature in body.get("features", [])}
    assert "wf-120-schedule-enrollment" in ids
    assert body.get("failed_count", 0) == 0


def test_the_vocabulary_serves_the_closed_sets(client):
    payload = client.get(f"{PREFIX}/vocabulary").json()
    assert payload["ticket"] == "WF-120"
    assert payload["trigger_types"][2] == "Based on a schedule"
    assert payload["statuses"] == ["queued", "completed", "unenrolled"]
    assert payload["reenroll_modes"] == ["single", "after_completion"]
    assert payload["backfill_choices"] == ["existing_and_future", "future_only"]
    assert payload["collections"]["schedule"] == "wf120_schedule"


def test_the_inferences_route_publishes_the_decisions(client):
    payload = client.get(f"{PREFIX}/inferences").json()
    assert payload["count"] >= 6
    ids = {entry["id"] for entry in payload["entries"]}
    assert "deferral_is_not_exclusion" in ids
    assert "reenrollment_is_after_completion_only" in ids


def test_the_records_route_reads_the_wf039_collection(client):
    make_contact(client, name="Northwind - Ada")
    listed = client.get(f"{PREFIX}/records", params={"room_id": ROOM_ID}).json()
    assert listed["count"] == 1
    assert listed["entries"][0]["name"] == "Northwind - Ada"


# --------------------------------------------------------------------------- #
# Schedules
# --------------------------------------------------------------------------- #


def test_a_schedule_is_created_read_back_and_patched(client):
    schedule = create_schedule(client)
    assert schedule["id"].startswith("wf120_schedule_")
    assert schedule["object"] == "Contact"
    assert client.get(f"{PREFIX}/schedules/{schedule['id']}").json()["id"] == schedule["id"]

    patched = client.patch(f"{PREFIX}/schedules/{schedule['id']}", json={"interval_minutes": 30})
    assert patched.status_code == 200
    assert patched.json()["interval_minutes"] == 30


def test_a_missing_schedule_is_a_404(client):
    assert client.get(f"{PREFIX}/schedules/nope").status_code == 404
    assert client.post(f"{PREFIX}/schedules/nope/run", json={}).status_code == 404
    assert client.patch(f"{PREFIX}/schedules/nope", json={}).status_code == 404
    assert client.get(f"{PREFIX}/enrollments/nope").status_code == 404
    assert client.get(f"{PREFIX}/runs/nope").status_code == 404


def test_a_bad_field_is_a_422_with_the_field(client):
    response = client.post(
        f"{PREFIX}/schedules",
        json={"interval_minutes": 0, "re_enroll": "each_match", "trigger": "Filter-based"},
    )
    assert response.status_code == 422
    errors = response.json()["errors"]
    assert "interval_minutes" in errors
    assert "re_enroll" in errors
    assert "trigger" in errors


def test_publish_toggles_the_schedule(client):
    schedule = create_schedule(client)
    assert client.get(f"{PREFIX}/schedules/{schedule['id']}").json()["published"] is False
    published = client.post(
        f"{PREFIX}/schedules/{schedule['id']}/publish", json={"published": True}
    )
    assert published.status_code == 200
    assert published.json()["schedule"]["published"] is True
    unpublished = client.post(
        f"{PREFIX}/schedules/{schedule['id']}/publish", json={"published": False}
    )
    assert unpublished.json()["schedule"]["published"] is False


# --------------------------------------------------------------------------- #
# Evaluate and run
# --------------------------------------------------------------------------- #


def test_evaluate_previews_without_writing(client):
    make_contact(client)
    schedule = create_schedule(
        client, filters=[{"field": "stage", "op": "eq", "value": "Qualification"}]
    )
    evaluation = client.post(f"{PREFIX}/schedules/{schedule['id']}/evaluate", json={}).json()
    assert evaluation["scanned"] == 1
    assert evaluation["matched"] == 1
    assert evaluation["decisions"][0]["reason"] == "matched"
    assert client.get(f"{PREFIX}/runs").json()["count"] == 0


def test_a_backfill_run_enrolls_and_records_the_actions(client):
    make_contact(client)
    schedule = create_schedule(client)
    result = publish(client, schedule["id"])
    body = result["run"]
    assert body["kind"] == "backfill"
    assert body["scanned"] == 1
    assert body["released"] == 1
    released = [row for row in body["results"] if row["outcome"] == "released"]
    assert len(released) == 1
    assert released[0]["task_id"].startswith("wf116_task_")

    # The task landed in WF-116's store.
    assert client.get("/api/wf-116/tasks").json()["count"] == 1

    enrollments = client.get(f"{PREFIX}/enrollments").json()
    assert enrollments["count"] == 1
    assert enrollments["entries"][0]["status"] == "completed"

    notifications = client.get(f"{PREFIX}/notifications").json()
    assert notifications["count"] == 1


def test_a_backfill_publish_enrols_the_records_that_already_match(client):
    make_contact(client, name="Matches")
    make_contact(client, name="Does not", stage="Discovery")
    schedule = create_schedule(
        client, filters=[{"field": "stage", "op": "eq", "value": "Qualification"}]
    )
    result = publish(client, schedule["id"])
    assert result["run"]["kind"] == "backfill"
    assert result["run"]["enrolled"] == 1


def test_a_future_only_publish_defers_and_the_next_run_enrols(client):
    make_contact(client, name="Deferred")
    schedule = create_schedule(client, backfill="future_only")
    result = publish(client, schedule["id"], backfill="future_only")
    assert result["run"]["kind"] == "activation"
    assert result["run"]["deferred"] == 1
    assert client.get(f"{PREFIX}/enrollments").json()["count"] == 0
    run = client.post(f"{PREFIX}/schedules/{schedule['id']}/run", json={}).json()
    assert run["enrolled"] == 1


def test_a_second_run_does_not_re_fire_it(client):
    make_contact(client)
    schedule = create_schedule(client)
    publish(client, schedule["id"])
    second = client.post(f"{PREFIX}/schedules/{schedule['id']}/run", json={}).json()
    assert second["enrolled"] == 0
    assert second["by_reason"]["single_enrollment"] == 1
    assert client.get(f"{PREFIX}/enrollments").json()["count"] == 1


def test_an_unpublished_schedule_is_a_conflict(client):
    make_contact(client)
    schedule = create_schedule(client, published=False)
    response = client.post(f"{PREFIX}/schedules/{schedule['id']}/run", json={})
    assert response.status_code == 409
    assert response.json()["reason"] == "schedule_not_published"


def test_the_audit_row_names_the_run_route(client):
    record_id = make_contact(client)
    schedule = create_schedule(client, backfill="future_only")
    publish(client, schedule["id"], backfill="future_only")
    # The marker write happens on the run route, because publish deferred.
    client.post(f"{PREFIX}/schedules/{schedule['id']}/run", json={})
    audit = client.get("/api/audit", params={"record_id": record_id}).json()
    sources = {entry["source"] for entry in audit["entries"]}
    assert f"POST {PREFIX}/schedules/{schedule['id']}/run" in sources


def test_the_workflow_log_and_the_run_log_read_back(client):
    make_contact(client)
    schedule = create_schedule(client)
    publish(client, schedule["id"])
    run = client.post(f"{PREFIX}/schedules/{schedule['id']}/run", json={}).json()

    listed = client.get(f"{PREFIX}/runs", params={"schedule_id": schedule["id"]}).json()
    assert listed["count"] >= 1
    assert client.get(f"{PREFIX}/runs/{run['id']}").json()["kind"] == "schedule"

    events = client.get(f"{PREFIX}/events", params={"schedule_id": schedule["id"]}).json()
    kinds = {entry["kind"] for entry in events["entries"]}
    assert "enrolled" in kinds and "completed" in kinds


def test_the_summary_counts_are_served(client):
    make_contact(client)
    schedule = create_schedule(client)
    publish(client, schedule["id"])
    summary = client.get(f"{PREFIX}/summary").json()
    assert summary["schedules"] == 1
    assert summary["published"] == 1
    assert summary["records"] == 1
    assert summary["enrolled_total"] == 1
    assert summary["released_total"] == 1


# --------------------------------------------------------------------------- #
# Route health
# --------------------------------------------------------------------------- #


def test_every_advertised_route_answers_without_a_5xx(client):
    get_paths = [
        "/vocabulary",
        "/inferences",
        "/records",
        "/schedules",
        "/schedules/nope",
        "/enrollments",
        "/enrollments/nope",
        "/runs",
        "/runs/nope",
        "/events",
        "/notifications",
        "/summary",
    ]
    for path in get_paths:
        response = client.get(f"{PREFIX}{path}")
        assert response.status_code < 500, f"GET {path} answered {response.status_code}"

    post_paths = [
        "/schedules",
        "/schedules/nope/publish",
        "/schedules/nope/evaluate",
        "/schedules/nope/run",
    ]
    for path in post_paths:
        response = client.post(f"{PREFIX}{path}", json={})
        assert response.status_code < 500, f"POST {path} answered {response.status_code}"

    assert client.patch(f"{PREFIX}/schedules/nope", json={}).status_code < 500
    assert client.delete(f"{PREFIX}/schedules/nope").status_code < 500


def test_a_delayed_enrollment_is_visible_through_the_run_route(client):
    make_contact(client)
    schedule = create_schedule(client, delay_minutes=60)
    publish(client, schedule["id"])
    enrollment = client.get(f"{PREFIX}/enrollments").json()["entries"][0]
    assert enrollment["status"] == "queued"
    later = (now() + timedelta(hours=2)).isoformat()
    run = client.post(f"{PREFIX}/schedules/{schedule['id']}/run", json={"at": later}).json()
    assert run["released"] == 1
    assert client.get(f"{PREFIX}/enrollments/{enrollment['id']}").json()["status"] == "completed"
