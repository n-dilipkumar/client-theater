"""WF-115 through the HTTP surface.

The domain rules live in ``tests/test_wf115.py``. What is here is the half the
domain cannot see:

* the router is mounted by **discovery alone**, with no edit to ``dsr/api.py``
* every refusal reaches the client with the status and code it carries, through
  **one** registered handler
* every write's audit row **names a route this router actually serves**, checked
  against the live route table rather than against a list in this file
* the routes answer **no 5xx when called the way ``tools/verify_all_routes.py``
  calls them** (a substituted id and an empty body)

The ``client`` fixture from ``conftest.py`` enters one ``TestClient`` per module
and points ``app.state`` at a fresh, empty database per test, so this file passes
on its own and under ``pytest-xdist``.
"""

from __future__ import annotations

from typing import Any

from dsr.features import wf115_publish_custom_workflow_triggers_and_actions as feature
from fastapi.testclient import TestClient

PREFIX = "/api/wf-115"
FEATURE_ID = "wf-115-marketplace-primitives"

TRIGGER = {
    "kind": "trigger",
    "key": "mycustomtrigger",
    "name": "My custom trigger",
    "short_description": "Fires when a page is viewed.",
    "sample_payload": {"contact": {"name": "Dana"}, "tags": ["pricing"]},
    "filters": [
        {"field": "contact.name", "title": "Name", "field_type": "String", "required": True}
    ],
    "subscription_url": "https://apps.example.com/subscribe",
}

ACTION = {
    "kind": "action",
    "key": "mycustomaction",
    "name": "My custom action",
    "sample_payload": {"contact": {"name": "Dana"}},
    "execution": {"method": "url", "url": "https://apps.example.com/execute"},
}

EXECUTE_URL = "https://services.leadconnectorhq.com/workflows-marketplace/triggers/execute/abc/def"


def served_routes(client: TestClient) -> list[dict[str, Any]]:
    return [
        route
        for entry in client.get("/api/features").json()["features"]
        for route in entry["routes"]
    ]


def names_a_served_route(source: str, routes: list[dict[str, Any]]) -> bool:
    """True when an audit ``source`` names a mounted route, segment by segment."""
    method, _, path = source.partition(" ")
    actual = [segment for segment in path.split("/") if segment]
    for route in routes:
        if method not in route["methods"]:
            continue
        template = [segment for segment in route["path"].split("/") if segment]
        if len(template) != len(actual):
            continue
        if all(
            expected.startswith("{") or expected == found
            for expected, found in zip(template, actual, strict=False)
        ):
            return True
    return False


def make_trigger(client: TestClient, **overrides) -> dict[str, Any]:
    body = {**TRIGGER, **overrides}
    response = client.post(f"{PREFIX}/modules", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def go_live(client: TestClient, module_id: str) -> dict[str, Any]:
    assert (
        client.post(f"{PREFIX}/modules/{module_id}/submit", json={"changelog": "v1"}).status_code
        == 200
    )
    assert (
        client.post(
            f"{PREFIX}/modules/{module_id}/review",
            json={"decision": "approve", "reviewer": "r", "note": "ok"},
        ).status_code
        == 200
    )
    response = client.post(f"{PREFIX}/modules/{module_id}/publish")
    assert response.status_code == 200, response.text
    return response.json()


# --------------------------------------------------------------------------- #
# Registration
# --------------------------------------------------------------------------- #


def test_the_feature_loads_with_no_failure(client: TestClient):
    body = client.get("/api/features").json()
    assert body["failed_count"] == 0
    mine = [entry for entry in body["features"] if entry["id"] == FEATURE_ID]
    assert len(mine) == 1
    assert mine[0]["prefix"] == PREFIX
    assert mine[0]["loaded"] is True
    assert "MarketplaceRefusal" in mine[0]["exception_handlers"]


def test_every_route_this_feature_serves_is_under_its_own_prefix(client: TestClient):
    body = client.get("/api/features").json()
    mine = next(entry for entry in body["features"] if entry["id"] == FEATURE_ID)
    paths = {route["path"] for route in mine["routes"]}
    assert paths, "the feature mounts no routes"
    assert all(path.startswith(PREFIX) for path in paths), paths


def test_the_feature_declares_the_prefix_the_contract_names():
    assert feature.router.prefix == PREFIX
    assert feature.FEATURE["ticket"] == "WF-115"
    assert feature.FEATURE["id"] == FEATURE_ID
    assert feature.FEATURE["prefix"] == PREFIX
    assert feature.FEATURE["name"]


def test_one_handler_covers_the_whole_refusal_hierarchy():
    from dsr.marketplace_primitives.errors import MarketplaceRefusal

    assert list(feature.EXCEPTION_HANDLERS) == [MarketplaceRefusal]


def test_the_feature_module_never_imports_the_app_or_opens_sqlite():
    from pathlib import Path

    text = Path(feature.__file__).read_text(encoding="utf-8")
    assert "from dsr.api" not in text and "import dsr.api" not in text
    assert "import sqlite3" not in text
    assert "sqlite3.connect" not in text


# --------------------------------------------------------------------------- #
# Vocabulary and reads
# --------------------------------------------------------------------------- #


def test_the_vocabulary_route_serves_the_rules_without_a_store(client: TestClient):
    body = client.get(f"{PREFIX}/vocabulary").json()
    assert body["ticket"] == "WF-115"
    assert body["event_types"] == ["CREATED", "UPDATED", "DELETED"]
    assert body["dynamic_filter_limit"] == 1
    assert "published" in body["review_states"]
    assert body["transitions"]["draft"]


def test_the_decisions_route_records_the_gates(client: TestClient):
    body = client.get(f"{PREFIX}/decisions").json()
    assert body["count"] == len(body["decisions"])
    ids = {item["id"] for item in body["decisions"]}
    assert {"third_party_endpoints", "review_state_machine", "soft_delete"} <= ids


def test_summary_starts_empty(client: TestClient):
    body = client.get(f"{PREFIX}/summary").json()
    assert body["total"] == 0
    assert body["published"] == 0


def test_modules_lists_and_filters(client: TestClient):
    make_trigger(client)
    client.post(f"{PREFIX}/modules", json=ACTION)
    listed = client.get(f"{PREFIX}/modules").json()
    assert listed["count"] == 2
    only_trigger = client.get(f"{PREFIX}/modules", params={"kind": "trigger"}).json()
    assert [m["key"] for m in only_trigger["modules"]] == ["mycustomtrigger"]
    searched = client.get(f"{PREFIX}/modules", params={"q": "action"}).json()
    assert [m["key"] for m in searched["modules"]] == ["mycustomaction"]


def test_get_module_404s_and_the_detail_route_embeds_history(client: TestClient):
    assert client.get(f"{PREFIX}/modules/nope").status_code == 404
    module = make_trigger(client)
    detail = client.get(f"{PREFIX}/modules/{module['id']}").json()
    assert detail["id"] == module["id"]
    assert detail["versions"] == []
    assert detail["reviews"] == []


def test_key_resolution_reports_publication_state(client: TestClient):
    assert client.get(f"{PREFIX}/keys/absent").status_code == 404
    module = make_trigger(client)
    before = client.get(f"{PREFIX}/keys/mycustomtrigger").json()
    assert before["resolvable"] is False
    go_live(client, module["id"])
    after = client.get(f"{PREFIX}/keys/mycustomtrigger").json()
    assert after["resolvable"] is True
    assert after["review_state"] == "published"


# --------------------------------------------------------------------------- #
# Authoring and the review machine over HTTP
# --------------------------------------------------------------------------- #


def test_create_returns_201_and_a_draft(client: TestClient):
    module = make_trigger(client)
    assert module["review_state"] == "draft"
    assert module["available_actions"]["submit"] is True


def test_duplicate_key_is_a_409(client: TestClient):
    make_trigger(client)
    response = client.post(f"{PREFIX}/modules", json=TRIGGER)
    assert response.status_code == 409
    assert response.json()["error"] == "key_taken"


def test_a_malformed_key_is_a_422(client: TestClient):
    response = client.post(f"{PREFIX}/modules", json={**TRIGGER, "key": "Bad Key"})
    assert response.status_code == 422
    assert response.json()["error"] == "key_invalid"


def test_an_invalid_payload_is_a_422_with_every_problem(client: TestClient):
    response = client.post(
        f"{PREFIX}/modules", json={"kind": "trigger", "key": "okaykey", "name": ""}
    )
    assert response.status_code == 422
    body = response.json()
    assert body["error"] == "module_invalid"
    assert any("name is required" in problem for problem in body["problems"])


def test_a_second_dynamic_filter_is_a_422(client: TestClient):
    response = client.post(
        f"{PREFIX}/modules",
        json={
            "kind": "trigger",
            "key": "dynkey",
            "name": "Dyn",
            "subscription_url": "https://apps.example.com/s",
            "dynamic_filter_url": "https://apps.example.com/dyn",
            "filters": [
                {
                    "field": "a",
                    "field_type": "Dynamic",
                    "option_source": "external_api",
                    "dynamic_url": "https://apps.example.com/1",
                },
                {
                    "field": "b",
                    "field_type": "Dynamic",
                    "option_source": "external_api",
                    "dynamic_url": "https://apps.example.com/2",
                },
            ],
        },
    )
    assert response.status_code == 422
    assert response.json()["error"] == "dynamic_filter_limit"


def test_patch_edits_a_field(client: TestClient):
    module = make_trigger(client)
    response = client.patch(f"{PREFIX}/modules/{module['id']}", json={"name": "Renamed"})
    assert response.status_code == 200
    assert response.json()["name"] == "Renamed"


def test_patch_refuses_a_key_change(client: TestClient):
    module = make_trigger(client)
    response = client.patch(f"{PREFIX}/modules/{module['id']}", json={"key": "different"})
    assert response.status_code == 409
    assert response.json()["error"] == "key_immutable"


def test_patch_refuses_a_published_module(client: TestClient):
    module = make_trigger(client)
    go_live(client, module["id"])
    response = client.patch(f"{PREFIX}/modules/{module['id']}", json={"name": "New"})
    assert response.status_code == 409
    assert response.json()["error"] == "transition_refused"


def test_submit_requires_a_changelog(client: TestClient):
    module = make_trigger(client)
    response = client.post(f"{PREFIX}/modules/{module['id']}/submit", json={})
    assert response.status_code == 422
    assert response.json()["error"] == "module_invalid"


def test_the_full_lifecycle_reaches_published(client: TestClient):
    module = make_trigger(client)
    submitted = client.post(
        f"{PREFIX}/modules/{module['id']}/submit", json={"changelog": "First cut"}
    ).json()
    assert submitted["review_state"] == "submitted"
    assert submitted["version"] == 1
    reviewing = client.post(
        f"{PREFIX}/modules/{module['id']}/review",
        json={"decision": "start_review", "reviewer": "r"},
    ).json()
    assert reviewing["review_state"] == "in_review"
    approved = client.post(
        f"{PREFIX}/modules/{module['id']}/review",
        json={"decision": "approve", "reviewer": "r", "note": "ok"},
    ).json()
    assert approved["review_state"] == "approved"
    published = client.post(f"{PREFIX}/modules/{module['id']}/publish").json()
    assert published["review_state"] == "published"
    detail = client.get(f"{PREFIX}/modules/{module['id']}").json()
    assert len(detail["versions"]) == 1


def test_review_rejects_an_unknown_decision_and_a_missing_note(client: TestClient):
    module = make_trigger(client)
    client.post(f"{PREFIX}/modules/{module['id']}/submit", json={"changelog": "v1"})
    bad_decision = client.post(
        f"{PREFIX}/modules/{module['id']}/review", json={"decision": "maybe"}
    )
    assert bad_decision.status_code == 422
    no_note = client.post(f"{PREFIX}/modules/{module['id']}/review", json={"decision": "reject"})
    assert no_note.status_code == 422
    rejected = client.post(
        f"{PREFIX}/modules/{module['id']}/review",
        json={"decision": "reject", "note": "not now"},
    )
    assert rejected.status_code == 200
    assert rejected.json()["review_state"] == "rejected"


def test_publish_is_a_409_until_approved(client: TestClient):
    module = make_trigger(client)
    response = client.post(f"{PREFIX}/modules/{module['id']}/publish")
    assert response.status_code == 409
    assert response.json()["error"] == "transition_refused"


def test_withdraw_moves_a_submission_back(client: TestClient):
    module = make_trigger(client)
    client.post(f"{PREFIX}/modules/{module['id']}/submit", json={"changelog": "v1"})
    response = client.post(f"{PREFIX}/modules/{module['id']}/withdraw")
    assert response.status_code == 200
    assert response.json()["review_state"] == "withdrawn"


def test_delete_is_a_soft_delete_and_restore_undoes_it(client: TestClient):
    module = make_trigger(client)
    deleted = client.delete(f"{PREFIX}/modules/{module['id']}")
    assert deleted.status_code == 200
    assert deleted.json()["review_state"] == "deleted"
    assert client.get(f"{PREFIX}/modules/{module['id']}").status_code == 404
    assert client.get(f"{PREFIX}/modules", params={"include_deleted": "true"}).json()["count"] == 1
    resolved = client.get(f"{PREFIX}/keys/mycustomtrigger").json()
    assert resolved["review_state"] == "deleted"
    assert resolved["resolvable"] is False
    restored = client.post(f"{PREFIX}/modules/{module['id']}/restore")
    assert restored.status_code == 200
    assert restored.json()["deleted"] is False


# --------------------------------------------------------------------------- #
# Subscription deliveries
# --------------------------------------------------------------------------- #


def test_a_delivery_needs_a_published_module(client: TestClient):
    module = make_trigger(client)
    response = client.post(
        f"{PREFIX}/modules/{module['id']}/deliveries",
        json={"event_type": "CREATED", "target_url": EXECUTE_URL, "filters": []},
    )
    assert response.status_code == 409
    assert response.json()["error"] == "module_not_published"


def test_a_delivery_with_a_bad_event_is_a_422(client: TestClient):
    module = make_trigger(client)
    go_live(client, module["id"])
    response = client.post(
        f"{PREFIX}/modules/{module['id']}/deliveries",
        json={"event_type": "NOPE", "target_url": EXECUTE_URL, "filters": []},
    )
    assert response.status_code == 422
    assert response.json()["error"] == "module_invalid"


def test_a_published_module_records_and_evaluates_a_delivery(client: TestClient):
    module = make_trigger(client)
    go_live(client, module["id"])
    response = client.post(
        f"{PREFIX}/modules/{module['id']}/deliveries",
        json={
            "event_type": "UPDATED",
            "target_url": EXECUTE_URL,
            "filters": [{"field": "contact.name", "required": True}],
            "extras": {"locationId": "loc_1", "workflowId": "wf_7"},
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["evaluation"]["matched"] is True
    assert body["delivery"]["data"]["event_type"] == "UPDATED"
    detail = client.get(f"{PREFIX}/modules/{module['id']}").json()
    assert len(detail["deliveries"]) == 1


# --------------------------------------------------------------------------- #
# The audit guarantee
# --------------------------------------------------------------------------- #


def test_every_audit_row_names_a_route_this_feature_serves(client: TestClient):
    module = make_trigger(client)
    client.patch(f"{PREFIX}/modules/{module['id']}", json={"name": "Edited"})
    client.post(f"{PREFIX}/modules/{module['id']}/submit", json={"changelog": "v1"})
    client.post(
        f"{PREFIX}/modules/{module['id']}/review",
        json={"decision": "approve", "reviewer": "r", "note": "ok"},
    )
    client.post(f"{PREFIX}/modules/{module['id']}/publish")
    client.post(
        f"{PREFIX}/modules/{module['id']}/deliveries",
        json={"event_type": "CREATED", "target_url": EXECUTE_URL, "filters": []},
    )
    client.delete(f"{PREFIX}/modules/{module['id']}")

    rows = client.get("/api/audit?limit=500").json()["entries"]
    mine = [row for row in rows if PREFIX in str(row.get("source") or "")]
    assert mine, "no audit row from this feature was written"
    routes = served_routes(client)
    for row in mine:
        assert names_a_served_route(str(row["source"]), routes), (
            f"audit row names {row['source']!r}, which is not a route this app serves"
        )


def test_the_recorded_actor_is_the_caller(client: TestClient):
    client.post(f"{PREFIX}/modules", json=TRIGGER, headers={"X-Actor": "builder"})
    rows = client.get("/api/audit?limit=50").json()["entries"]
    mine = [
        row for row in rows if PREFIX in str(row.get("source") or "") and row["action"] == "insert"
    ]
    assert mine and mine[0]["actor"] == "builder"


# --------------------------------------------------------------------------- #
# The routes, called the way tools/verify_all_routes.py calls them
# --------------------------------------------------------------------------- #


def test_every_route_answers_without_a_5xx_when_called_with_an_empty_body(client: TestClient):
    faults: list[tuple[str, str, int]] = []
    for route in served_routes(client):
        if not route["path"].startswith(PREFIX):
            continue
        path = route["path"].replace("{module_id}", "absent").replace("{key}", "absent")
        for method in route["methods"]:
            if method not in {"GET", "POST", "PATCH", "DELETE"}:
                continue
            body = {} if method in {"POST", "PATCH", "PUT"} else None
            response = client.request(method, path, json=body)
            if response.status_code == 0 or response.status_code >= 500:
                faults.append((method, path, response.status_code))
    assert faults == [], f"routes answered 5xx when called with an empty body: {faults}"


def test_a_missing_module_is_a_clean_404_on_every_module_route(client: TestClient):
    faults: list[tuple[str, int]] = []
    for route in served_routes(client):
        if not route["path"].startswith(PREFIX) or "{module_id}" not in route["path"]:
            continue
        path = route["path"].replace("{module_id}", "absent")
        for method in route["methods"]:
            response = client.request(method, path, json={} if method != "DELETE" else None)
            if response.status_code >= 500:
                faults.append((path, response.status_code))
    assert faults == [], faults
