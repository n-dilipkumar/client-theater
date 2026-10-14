"""HTTP tests for WF-137: the mounted routes, the governance writes, and the audit trail.

These drive the mounted app through ``TestClient`` rather than calling the book,
so they exercise the same path a real client does. If the feature failed to
register, or collided with another feature's route, every test here would 404 -
which is the point: the router is mounted by discovery alone and
``backend/dsr/api.py`` is not edited.

Each test names the rule it protects. One test is worth reading twice: the audit
test asserts that every row this feature wrote carries a ``source`` that names a
route the host actually serves, which is the recurring defect the build brief
calls out.

The database is a temporary file per test, through the shared ``client`` fixture,
so nothing here depends on a row another test wrote.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from fastapi.testclient import TestClient

PREFIX = "/api/wf-137"
FEATURE_ID = "wf-137-governance"

#: This workflow's own collections, whose audit rows are the ones to check.
OWN_COLLECTIONS = {
    "wf137_room",
    "wf137_snapshot",
    "wf137_plan",
    "wf137_phase",
    "wf137_retention",
}

NOW = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)

_PLACEHOLDER = re.compile(r"\{[^}]+\}")


# --------------------------------------------------------------------------- #
# Fixtures and helpers
# --------------------------------------------------------------------------- #


def make_room(client: TestClient, name: str = "Northwind", **extra) -> dict:
    response = client.post(
        "/api/records/room", json={"name": name, **extra}, params={"actor": "dana"}
    )
    assert response.status_code == 201, response.text
    return response.json()


def publish(client: TestClient, room_id: str, label: str = "first", content=None) -> dict:
    response = client.post(
        f"{PREFIX}/rooms/{room_id}/snapshots",
        json={"label": label, "content": content or {"a": 1}},
        params={"actor": "dana"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def repurpose(client: TestClient, room_id: str, **payload) -> dict:
    response = client.post(
        f"{PREFIX}/rooms/{room_id}/repurpose", json=payload, params={"actor": "dana"}
    )
    assert response.status_code == 200, response.text
    return response.json()


def mounted_routes(client: TestClient) -> set[tuple[str, str]]:
    body = client.get("/api/features").json()
    return {
        (method, route["path"])
        for feature in body["features"]
        for route in feature["routes"]
        for method in route["methods"]
    }


def source_matches_route(source: str, routes: set[tuple[str, str]]) -> bool:
    """Is an audit ``source`` a route the app serves? ``POST /api/wf-137/brand`` -> yes."""
    verb, _, path = source.partition(" ")
    pattern = _PLACEHOLDER.sub("[^/]+", path)
    return any(method == verb and re.fullmatch(pattern, path_) for method, path_ in routes)


# --------------------------------------------------------------------------- #
# Discovery and the published contract
# --------------------------------------------------------------------------- #


def test_the_router_is_mounted_by_discovery(client: TestClient):
    response = client.get(f"{PREFIX}/summary")
    assert response.status_code == 200
    assert "rooms_tracked" in response.json()


def test_the_feature_appears_in_the_registry(client: TestClient):
    body = client.get("/api/features").json()
    ids = [f.get("id") for f in body.get("features", [])]
    assert FEATURE_ID in ids
    assert body["failed_count"] == 0, body["failed"]


def test_the_prefix_and_handlers_are_this_workflows_own(client: TestClient):
    body = client.get("/api/features").json()
    mine = [f for f in body["features"] if f["id"] == FEATURE_ID]
    assert len(mine) == 1
    assert mine[0]["prefix"] == PREFIX
    assert mine[0]["ticket"] == "WF-137"
    assert mine[0]["exception_handlers"] == ["GovernanceError"]
    assert [f["prefix"] for f in body["features"]].count(PREFIX) == 1


def test_the_feature_is_navigable_without_editing_app_jsx(client: TestClient):
    entry = next(f for f in client.get("/api/features").json()["features"] if f["prefix"] == PREFIX)
    assert entry["nav"] == [{"id": "governance", "label": "Governance"}]


def test_the_vocabulary_serves_the_recorded_triggers_and_lifecycle(client: TestClient):
    body = client.get(f"{PREFIX}/vocabulary").json()
    assert body["ticket"] == "WF-137"
    assert body["snapshot_triggers"] == ["publish", "session", "restore", "branch"]
    assert body["lifecycle_states"][0] == "sales"
    assert body["defaults"]["archive_after_days"] == 180


def test_the_inferences_route_states_the_open_decisions(client: TestClient):
    body = client.get(f"{PREFIX}/inferences").json()
    ids = {entry["id"] for entry in body["inferences"]}
    assert {"retention_windows", "snapshot_triggers", "synced_sections"} <= ids
    for entry in body["inferences"]:
        assert entry["decision"] and entry["why"] and entry["change_it_by"]


# --------------------------------------------------------------------------- #
# Brand governance
# --------------------------------------------------------------------------- #


def test_a_room_starts_off_brand_and_apply_clears_the_findings(client: TestClient):
    room = make_room(client, branding={"theme": "dark", "accent": "#22c55e"})
    before = client.get(f"{PREFIX}/rooms/{room['id']}/brand").json()
    assert before["compliant"] is False
    assert before["findings"]

    applied = client.post(
        f"{PREFIX}/rooms/{room['id']}/brand/apply", params={"actor": "enablement"}
    )
    assert applied.status_code == 200, applied.text
    assert applied.json()["compliant"] is True
    assert applied.json()["branding"]["theme"] == "light"


def test_the_brand_set_can_be_written_and_read_back(client: TestClient):
    written = client.post(
        f"{PREFIX}/brand", json={"accent": "#10506f"}, params={"actor": "enablement"}
    )
    assert written.status_code == 200, written.text
    board = client.get(f"{PREFIX}/brand").json()
    assert board["brand_set"]["tokens"]["accent"] == "#10506f"


def test_an_unknown_room_brand_is_not_found(client: TestClient):
    assert client.get(f"{PREFIX}/rooms/room_absent/brand").status_code == 404


# --------------------------------------------------------------------------- #
# Versioning
# --------------------------------------------------------------------------- #


def test_a_publish_is_snapshotted_and_read_back(client: TestClient):
    room = make_room(client)
    snapshot = publish(client, room["id"], "first", {"a": 1})
    assert snapshot["version"] == 1
    assert snapshot["trigger"] == "publish"
    history = client.get(f"{PREFIX}/rooms/{room['id']}/history").json()
    assert history["count"] == 1
    assert history["head_version"] == 1


def test_an_editing_session_is_a_distinct_trigger(client: TestClient):
    room = make_room(client)
    publish(client, room["id"])
    response = client.post(
        f"{PREFIX}/rooms/{room['id']}/snapshots",
        json={"label": "session"},
        params={"trigger": "session", "actor": "dana"},
    )
    assert response.status_code == 201, response.text
    assert response.json()["trigger"] == "session"


def test_a_preview_reports_the_change_and_a_restore_writes_a_new_version(client: TestClient):
    room = make_room(client)
    publish(client, room["id"], "first", {"a": 1})
    publish(client, room["id"], "second", {"a": 2})
    preview = client.get(f"{PREFIX}/rooms/{room['id']}/snapshots/1").json()
    assert preview["matches_head"] is False
    assert "content" in [row["field"] for row in preview["changes"]]

    restored = client.post(
        f"{PREFIX}/rooms/{room['id']}/snapshots/1/restore", params={"actor": "dana"}
    )
    assert restored.status_code == 200, restored.text
    assert restored.json()["version"] == 3
    assert restored.json()["restored_from"] == 1


def test_a_branch_seeds_a_new_room(client: TestClient):
    room = make_room(client)
    publish(client, room["id"], "first", {"a": 1})
    branch = client.post(
        f"{PREFIX}/rooms/{room['id']}/snapshots/1/branch",
        json={"name": "Northwind branch"},
        params={"actor": "dana"},
    )
    assert branch.status_code == 201, branch.text
    new_id = branch.json()["room_id"]
    assert new_id != room["id"]
    packed = client.get("/api/records/room", params={"limit": 100}).json()
    assert new_id in {record["id"] for record in packed["records"]}


def test_an_unknown_version_is_not_found(client: TestClient):
    room = make_room(client)
    publish(client, room["id"])
    assert client.get(f"{PREFIX}/rooms/{room['id']}/snapshots/99").status_code == 404


def test_a_snapshot_with_unreadable_content_is_refused(client: TestClient):
    room = make_room(client)
    response = client.post(f"{PREFIX}/rooms/{room['id']}/snapshots", json={"content": 5})
    assert response.status_code == 422, response.text
    assert response.json()["error"] == "invalid_governance"


# --------------------------------------------------------------------------- #
# Closed-won continuity
# --------------------------------------------------------------------------- #


def test_repurpose_instantiates_the_phases_on_the_same_room(client: TestClient):
    room = make_room(client)
    state = repurpose(client, room["id"], closed_at=NOW.isoformat())
    assert state["lifecycle_state"] == "onboarding"
    assert state["room_id"] == room["id"]
    assert len(state["phases"]) == 4
    assert state["phases"][0]["state"] == "active"


def test_a_room_is_repurposed_once(client: TestClient):
    room = make_room(client)
    repurpose(client, room["id"])
    again = client.post(f"{PREFIX}/rooms/{room['id']}/repurpose", json={})
    assert again.status_code == 409, again.text
    assert again.json()["reason"] == "already_repurposed"


def test_advancing_with_an_open_milestone_is_refused(client: TestClient):
    room = make_room(client)
    repurpose(client, room["id"])
    refused = client.post(f"{PREFIX}/rooms/{room['id']}/onboarding/advance")
    assert refused.status_code == 409, refused.text
    assert refused.json()["reason"] == "milestones_open"


def test_advancing_a_sales_room_is_refused(client: TestClient):
    room = make_room(client)
    refused = client.post(f"{PREFIX}/rooms/{room['id']}/onboarding/advance")
    assert refused.status_code == 409, refused.text
    assert refused.json()["reason"] == "not_repurposed"


def test_closing_the_milestones_advances_the_phase(client: TestClient):
    room = make_room(client)
    repurpose(client, room["id"])
    for milestone in ("introductions", "success_criteria"):
        done = client.post(
            f"{PREFIX}/rooms/{room['id']}/onboarding/milestones/complete",
            json={"phase_key": "kickoff", "milestone_key": milestone},
            params={"actor": "cs"},
        )
        assert done.status_code == 200, done.text
    advanced = client.post(
        f"{PREFIX}/rooms/{room['id']}/onboarding/advance", params={"actor": "cs"}
    )
    assert advanced.status_code == 200, advanced.text
    assert advanced.json()["active_phase"]["phase_key"] == "implementation"


def test_an_unknown_milestone_is_not_found(client: TestClient):
    room = make_room(client)
    repurpose(client, room["id"])
    response = client.post(
        f"{PREFIX}/rooms/{room['id']}/onboarding/milestones/complete",
        json={"phase_key": "kickoff", "milestone_key": "ghost"},
    )
    assert response.status_code == 404, response.text


def test_a_named_plan_can_be_written_and_used(client: TestClient):
    room = make_room(client)
    plan = {
        "key": "pilot",
        "name": "Pilot plan",
        "phases": [{"key": "p1", "name": "Phase one", "milestones": [{"key": "m1", "name": "M1"}]}],
    }
    written = client.post(f"{PREFIX}/plans", json=plan, params={"actor": "enablement"})
    assert written.status_code == 201, written.text
    state = repurpose(client, room["id"], plan_key="pilot")
    assert state["plan_key"] == "pilot"
    assert [phase["phase_key"] for phase in state["phases"]] == ["p1"]


# --------------------------------------------------------------------------- #
# Retention
# --------------------------------------------------------------------------- #


def test_a_retention_window_is_set_and_read(client: TestClient):
    room = make_room(client)
    set_response = client.post(
        f"{PREFIX}/rooms/{room['id']}/retention",
        json={
            "archive_after_days": 30,
            "delete_after_days": 365,
            "lifecycle_ended_at": NOW.isoformat(),
        },
        params={"actor": "cs"},
    )
    assert set_response.status_code == 200, set_response.text
    state = client.get(f"{PREFIX}/rooms/{room['id']}/retention").json()
    assert state["has_policy"] is True
    assert state["archive_after_days"] == 30


def test_an_unreadable_retention_window_is_refused(client: TestClient):
    room = make_room(client)
    response = client.post(f"{PREFIX}/rooms/{room['id']}/retention", json={"archive_after_days": 0})
    assert response.status_code == 422, response.text
    assert response.json()["error"] == "invalid_governance"


def test_applying_a_not_due_retention_is_refused(client: TestClient):
    room = make_room(client)
    client.post(
        f"{PREFIX}/rooms/{room['id']}/retention",
        json={"archive_after_days": 365, "lifecycle_ended_at": NOW.isoformat()},
    )
    response = client.post(f"{PREFIX}/rooms/{room['id']}/retention/apply")
    assert response.status_code == 409, response.text
    assert response.json()["reason"] == "retention_state"


def test_applying_a_due_retention_archives_the_room(client: TestClient):
    room = make_room(client)
    client.post(
        f"{PREFIX}/rooms/{room['id']}/retention",
        json={
            "archive_after_days": 1,
            "lifecycle_ended_at": "2020-01-01T00:00:00+00:00",
        },
    )
    response = client.post(f"{PREFIX}/rooms/{room['id']}/retention/apply", params={"actor": "cs"})
    assert response.status_code == 200, response.text
    assert response.json()["applied"] == "archived"
    board = client.get(f"{PREFIX}/retention").json()
    assert board["count"] == 1


def test_an_archived_room_refuses_a_new_snapshot(client: TestClient):
    room = make_room(client)
    client.post(
        f"{PREFIX}/rooms/{room['id']}/retention",
        json={"archive_after_days": 1, "lifecycle_ended_at": "2020-01-01T00:00:00+00:00"},
    )
    client.post(f"{PREFIX}/rooms/{room['id']}/retention/apply")
    refused = client.post(f"{PREFIX}/rooms/{room['id']}/snapshots", json={"label": "late"})
    assert refused.status_code == 409, refused.text
    assert refused.json()["reason"] == "lifecycle_closed"


# --------------------------------------------------------------------------- #
# The audit trail, and the rule that every source names a served route
# --------------------------------------------------------------------------- #


def test_every_write_this_feature_made_names_a_route_the_app_serves(client: TestClient):
    """The recurring defect the brief names: an audit row pointing at a dead path."""
    room = make_room(client)
    publish(client, room["id"])
    client.post(f"{PREFIX}/brand", json={"accent": "#10506f"})
    client.post(f"{PREFIX}/rooms/{room['id']}/brand/apply")
    repurpose(client, room["id"])
    client.post(
        f"{PREFIX}/rooms/{room['id']}/retention",
        json={"archive_after_days": 30, "lifecycle_ended_at": NOW.isoformat()},
    )

    entries = client.get("/api/audit", params={"limit": 1000}).json()["entries"]
    routes = mounted_routes(client)
    mine = [entry for entry in entries if PREFIX in str(entry.get("source") or "")]
    assert mine, "the feature wrote no audited rows, so this proves nothing"
    for entry in mine:
        assert source_matches_route(str(entry["source"]), routes), entry["source"]
    assert OWN_COLLECTIONS & {entry["collection"] for entry in mine}


def test_no_get_route_this_feature_advertises_returns_a_5xx(client: TestClient):
    """A light version of tools/verify_all_routes.py, scoped to this feature."""
    room = make_room(client)
    publish(client, room["id"])
    repurpose(client, room["id"])
    client.post(
        f"{PREFIX}/rooms/{room['id']}/retention",
        json={"archive_after_days": 30, "lifecycle_ended_at": NOW.isoformat()},
    )
    get_routes = [
        route
        for route in mounted_routes(client)
        if route[0] == "GET" and route[1].startswith(PREFIX)
    ]
    for _method, path in get_routes:
        concrete = path.replace("{room_id}", room["id"]).replace("{version}", "1")
        response = client.get(concrete)
        assert response.status_code < 500, f"{concrete} -> {response.status_code}"
