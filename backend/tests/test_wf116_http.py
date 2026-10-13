"""WF-116 HTTP tests: the mounted router, its errors and its audit trail.

The client is the conftest module-scoped one, repointed at a fresh database per
test, so each test sees only its own rows and the file passes alone. Times are
computed from the wall clock at test time, so no assertion depends on today's
date being a particular day.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

PREFIX = "/api/wf-116"
CONTACT_COLLECTION = "crm_atomic_target"


def now() -> datetime:
    return datetime.now(timezone.utc)


def make_contact(client, *, email="dana@northwind.example", room_id="room_1") -> str:
    response = client.post(
        f"/api/records/{CONTACT_COLLECTION}",
        json={"object": "contact", "reference_id": "seed-contact", "body": {"email": email}},
        params={"room_id": room_id},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def task_payload(**overrides) -> dict:
    payload = {
        "subject": "Email Dana the revised pricing",
        "due_at": (now() + timedelta(days=2)).isoformat(),
        "reminder_at": (now() + timedelta(days=1)).isoformat(),
    }
    payload.update(overrides)
    return payload


def create_task(client, **overrides):
    response = client.post(f"{PREFIX}/tasks", json=task_payload(**overrides))
    assert response.status_code == 201, response.text
    return response.json()


# --------------------------------------------------------------------------- #
# Discovery and reference data
# --------------------------------------------------------------------------- #


def test_the_router_is_mounted_by_discovery(client):
    response = client.get(f"{PREFIX}/summary")
    assert response.status_code == 200
    assert "tasks" in response.json()


def test_the_feature_appears_in_the_registry(client):
    body = client.get("/api/features").json()
    ids = {feature.get("id") for feature in body.get("features", [])}
    assert "wf-116-seller-followup-task" in ids
    assert body.get("failed_count", 0) == 0


def test_the_vocabulary_serves_the_closed_sets(client):
    payload = client.get(f"{PREFIX}/vocabulary").json()
    assert payload["ticket"] == "WF-116"
    assert payload["statuses"] == ["NOT_STARTED", "COMPLETED"]
    assert payload["priorities"] == ["LOW", "MEDIUM", "HIGH"]
    assert payload["task_types"] == ["EMAIL", "CALL", "TODO"]
    assert payload["triggers"] == ["Task Added", "Task Reminder", "Task Completed"]
    assert payload["reminder_unit"] == "unix_ms"
    assert payload["collections"]["task"] == "wf116_task"


def test_the_inferences_route_publishes_the_decisions(client):
    payload = client.get(f"{PREFIX}/inferences").json()
    assert payload["count"] >= 6
    ids = {entry["id"] for entry in payload["entries"]}
    assert "contact_less_is_required" in ids
    assert "recycling_bin_retention" in ids


# --------------------------------------------------------------------------- #
# Create
# --------------------------------------------------------------------------- #


def test_a_standalone_task_is_created_and_read_back(client):
    task = create_task(client, subject="Internal review")
    assert task["standalone"] is True
    assert task["status"] == "NOT_STARTED"
    assert task["due_at"] and task["due_at_ms"]

    read = client.get(f"{PREFIX}/tasks/{task['id']}")
    assert read.status_code == 200
    assert read.json()["subject"] == "Internal review"
    assert [event["trigger"] for event in read.json()["events"]] == ["Task Added"]


def test_a_contact_attached_task_links_to_a_wf039_contact(client):
    contact_id = make_contact(client)
    task = create_task(client, contact_id=contact_id)
    assert task["attached"] is True
    listed = client.get(f"{PREFIX}/contacts").json()
    assert listed["entries"][0]["id"] == contact_id


def test_an_unknown_contact_is_a_404(client):
    response = client.post(f"{PREFIX}/tasks", json=task_payload(contact_id="missing"))
    assert response.status_code == 404
    assert response.json()["error"] == "task_not_found"


def test_a_missing_due_time_is_a_422_with_the_field(client):
    payload = task_payload()
    payload.pop("due_at")
    response = client.post(f"{PREFIX}/tasks", json=payload)
    assert response.status_code == 422
    assert "due_at" in response.json()["errors"]


def test_a_reminder_after_the_due_time_is_a_422(client):
    due = (now() + timedelta(days=1)).isoformat()
    response = client.post(
        f"{PREFIX}/tasks",
        json={"due_at": due, "reminder_at": (now() + timedelta(days=2)).isoformat()},
    )
    assert response.status_code == 422
    assert "reminder_at" in response.json()["errors"]


def test_a_notification_needs_an_assignee(client):
    response = client.post(f"{PREFIX}/tasks", json=task_payload(notify=True))
    assert response.status_code == 422
    assert "assignee" in response.json()["errors"]


def test_the_audit_row_names_the_create_route(client):
    task = create_task(client)
    audit = client.get("/api/audit", params={"record_id": task["id"]}).json()
    sources = {entry["source"] for entry in audit["entries"]}
    assert f"POST {PREFIX}/tasks" in sources


# --------------------------------------------------------------------------- #
# Signals
# --------------------------------------------------------------------------- #


def test_a_signal_creates_a_task_through_its_own_route(client):
    response = client.post(
        f"{PREFIX}/signals/engagement_score_rule_met",
        json={"assignee": "dana"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["signal"] == "engagement_score_rule_met"
    assert body["task_type"] == "EMAIL"


def test_an_unknown_signal_is_a_422(client):
    response = client.post(f"{PREFIX}/signals/not_a_signal", json={})
    assert response.status_code == 422
    assert "signal" in response.json()["errors"]


# --------------------------------------------------------------------------- #
# Lifecycle
# --------------------------------------------------------------------------- #


def test_completing_fires_the_task_completed_trigger(client):
    task = create_task(client)
    response = client.post(f"{PREFIX}/tasks/{task['id']}/complete", json={})
    assert response.status_code == 200
    assert response.json()["status"] == "COMPLETED"
    events = client.get(f"{PREFIX}/tasks/{task['id']}/events").json()["entries"]
    assert [event["trigger"] for event in events] == ["Task Added", "Task Completed"]


def test_completing_twice_is_a_conflict(client):
    task = create_task(client)
    client.post(f"{PREFIX}/tasks/{task['id']}/complete", json={})
    response = client.post(f"{PREFIX}/tasks/{task['id']}/complete", json={})
    assert response.status_code == 409
    assert response.json()["reason"] == "task_already_completed"


def test_firing_a_reminder_writes_the_reminder_trigger(client):
    task = create_task(client)
    response = client.post(f"{PREFIX}/tasks/{task['id']}/reminder", json={})
    assert response.status_code == 200
    assert response.json()["reminder_fired_at_ms"] is not None
    again = client.post(f"{PREFIX}/tasks/{task['id']}/reminder", json={})
    assert again.status_code == 409
    assert again.json()["reason"] == "reminder_already_fired"


def test_a_task_with_no_reminder_cannot_fire_one(client):
    payload = task_payload()
    payload.pop("reminder_at")
    created = client.post(f"{PREFIX}/tasks", json=payload)
    assert created.status_code == 201
    response = client.post(f"{PREFIX}/tasks/{created.json()['id']}/reminder", json={})
    assert response.status_code == 409
    assert response.json()["reason"] == "reminder_not_set"


def test_the_due_reminders_route_lists_a_past_reminder(client):
    create_task(
        client,
        subject="Chase the order form",
        due_at=(now() - timedelta(hours=1)).isoformat(),
        reminder_at=(now() - timedelta(hours=2)).isoformat(),
    )
    due = client.get(f"{PREFIX}/reminders/due").json()
    assert due["count"] == 1
    assert due["entries"][0]["subject"] == "Chase the order form"


# --------------------------------------------------------------------------- #
# Update, attach, bin
# --------------------------------------------------------------------------- #


def test_a_patch_changes_the_subject_and_priority(client):
    task = create_task(client, hs_task_priority="LOW")
    response = client.patch(
        f"{PREFIX}/tasks/{task['id']}",
        json={"subject": "Renamed", "hs_task_priority": "HIGH"},
    )
    assert response.status_code == 200
    assert response.json()["subject"] == "Renamed"
    assert response.json()["priority"] == "HIGH"


def test_attach_and_detach_move_the_task_between_paths(client):
    contact_id = make_contact(client)
    task = create_task(client)
    attached = client.post(f"{PREFIX}/tasks/{task['id']}/attach", json={"contact_id": contact_id})
    assert attached.status_code == 200
    assert attached.json()["attached"] is True
    detached = client.post(f"{PREFIX}/tasks/{task['id']}/detach", json={})
    assert detached.json()["standalone"] is True


def test_attach_without_a_contact_is_a_422(client):
    task = create_task(client)
    response = client.post(f"{PREFIX}/tasks/{task['id']}/attach", json={})
    assert response.status_code == 422
    assert "contact_id" in response.json()["errors"]


def test_pin_records_the_engagement_id_on_the_task(client):
    task = create_task(client)
    response = client.post(f"{PREFIX}/tasks/{task['id']}/pin", json={"engagement_id": "eng_9"})
    assert response.status_code == 200
    assert response.json()["pin_engagement_id"] == "eng_9"


def test_a_delete_moves_the_task_to_the_bin_and_restore_brings_it_back(client):
    task = create_task(client)
    deleted = client.delete(f"{PREFIX}/tasks/{task['id']}")
    assert deleted.status_code == 200
    assert deleted.json()["bin"] is True

    binned = client.get(f"{PREFIX}/bin").json()
    assert binned["count"] == 1
    assert client.get(f"{PREFIX}/tasks/{task['id']}").status_code == 404

    restored = client.post(f"{PREFIX}/tasks/{task['id']}/restore", json={})
    assert restored.status_code == 200
    assert restored.json()["binned"] is False


def test_a_purge_with_no_expired_rows_purges_nothing(client):
    task = create_task(client)
    client.delete(f"{PREFIX}/tasks/{task['id']}")
    response = client.post(f"{PREFIX}/bin/purge", json={})
    assert response.status_code == 200
    assert response.json()["purged"] == 0
    assert response.json()["retention_days"] == 30


def test_the_summary_counts_are_served(client):
    contact_id = make_contact(client)
    create_task(client, contact_id=contact_id)
    done = create_task(client, subject="Done")
    client.post(f"{PREFIX}/tasks/{done['id']}/complete", json={})
    summary = client.get(f"{PREFIX}/summary").json()
    assert summary["tasks"] == 2
    assert summary["completed"] == 1
    assert summary["attached"] == 1


# --------------------------------------------------------------------------- #
# Errors and route health
# --------------------------------------------------------------------------- #


def test_an_unknown_task_is_a_404(client):
    assert client.get(f"{PREFIX}/tasks/nope").status_code == 404
    assert client.get(f"{PREFIX}/tasks/nope/events").status_code == 404
    assert client.post(f"{PREFIX}/tasks/nope/complete", json={}).status_code == 404
    assert client.post(f"{PREFIX}/tasks/nope/reminder", json={}).status_code == 404
    assert client.delete(f"{PREFIX}/tasks/nope").status_code == 404


def test_every_advertised_route_answers_without_a_5xx(client):
    get_paths = [
        "/vocabulary",
        "/inferences",
        "/contacts",
        "/summary",
        "/tasks",
        "/tasks/nope",
        "/tasks/nope/events",
        "/reminders/due",
        "/bin",
    ]
    for path in get_paths:
        response = client.get(f"{PREFIX}{path}")
        assert response.status_code < 500, f"GET {path} answered {response.status_code}"

    post_paths = [
        "/tasks",
        "/signals/inbound_webhook",
        "/tasks/nope/complete",
        "/tasks/nope/reminder",
        "/tasks/nope/attach",
        "/tasks/nope/detach",
        "/tasks/nope/pin",
        "/tasks/nope/restore",
        "/bin/purge",
    ]
    for path in post_paths:
        response = client.post(f"{PREFIX}{path}", json={})
        assert response.status_code < 500, f"POST {path} answered {response.status_code}"

    assert client.patch(f"{PREFIX}/tasks/nope", json={}).status_code < 500
    assert client.delete(f"{PREFIX}/tasks/nope").status_code < 500
