"""WF-105: the HTTP surface, driven through the mounted routes.

These call the URLs the host actually serves rather than importing handlers and
calling them directly. The difference is load-bearing three times over:

* ``backend/dsr/api.py`` is not edited by this feature, so the router is mounted
  by discovery alone. If discovery failed, every test here would 404 and the
  domain tests would still pass, which is exactly the failure a feature-only
  test suite cannot see.
* The audit-source rule is checked against the **live route registry** the host
  reports, not against a list written by hand. A written list goes stale the
  moment a route is renamed, and then the check keeps passing while the audit log
  names a path the app stopped serving.
* The error statuses are asserted as HTTP statuses, through the one handler the
  feature registers.

``client`` comes from ``conftest.py``: one ``TestClient`` per module with the
store swapped per test, so these tests are isolated without paying for a lifespan
each. Every test builds its own rows, so none depends on another's.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

PREFIX = "/api/wf105"

WF105_COLLECTIONS = {
    "wf105_series",
    "wf105_tag",
    "wf105_enrollment",
    "wf105_tag_application",
    "wf105_series_evaluation",
}


def served_routes(client: TestClient) -> list[dict[str, Any]]:
    """Every route the host reports, from the live registry."""

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


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


def make_series(client: TestClient, *, reenter: bool = True) -> str:
    """Register the two workspace tags, declare a series, and publish it."""

    for name in ("Upgraded after Series", "ExitUpgradeSeries"):
        response = client.post(f"{PREFIX}/tags", json={"name": name})
        assert response.status_code == 201, response.text
    response = client.post(
        f"{PREFIX}/series",
        json={
            "name": "Expansion nurture",
            "reenter": reenter,
            "start_filters": [{"field": "plan", "value": "trial"}],
            "exit_filters": [{"field": "tags", "value": "ExitUpgradeSeries"}],
            "goal": {
                "outcome_filters": [{"field": "plan", "value": "premium"}],
                "match_window_days": 30,
            },
            "path": [
                {"kind": "message", "title": "Welcome"},
                {"kind": "rule", "title": "Wait"},
                {"kind": "tag", "title": "Tag", "tag": "Upgraded after Series"},
            ],
        },
    )
    assert response.status_code == 201, response.text
    series_id = str(response.json()["id"])
    published = client.post(f"{PREFIX}/series/{series_id}/publish")
    assert published.status_code == 200, published.text
    return series_id


# --------------------------------------------------------------------------- #
# Discovery: the router is mounted without editing api.py
# --------------------------------------------------------------------------- #


def test_the_router_is_mounted_by_discovery_alone(client: TestClient):
    entries = [
        entry
        for entry in client.get("/api/features").json()["features"]
        if entry["id"] == "wf-105-reenter-series-and-tag"
    ]
    assert len(entries) == 1
    entry = entries[0]
    assert entry["ticket"] == "WF-105"
    assert entry["prefix"] == PREFIX
    assert entry["routes"], "a mounted feature must report its routes"


def test_the_feature_reports_no_load_failure(client: TestClient):
    body = client.get("/api/features").json()
    assert not body.get("failed"), body.get("failed")


def test_no_two_features_claim_the_same_route(client: TestClient):
    seen: set[tuple[str, str]] = set()
    for entry in client.get("/api/features").json()["features"]:
        for route in entry["routes"]:
            for method in route["methods"]:
                key = (method, route["path"])
                assert key not in seen, f"{entry['id']} duplicates {key}"
                seen.add(key)


def test_every_advertised_route_answers_without_a_server_error(client: TestClient):
    """A route with no working handler behind it is the fault this guards."""

    for method, path in sorted(
        (method, route["path"])
        for route in served_routes(client)
        if route["path"].startswith(PREFIX)
        for method in route["methods"]
    ):
        url = path.replace("{series_id}", "absent").replace("{contact_id}", "absent")
        response = client.request(method, url, json={})
        assert response.status_code < 500, f"{method} {path} -> {response.status_code}"


# --------------------------------------------------------------------------- #
# Reference data
# --------------------------------------------------------------------------- #


def test_the_vocabulary_route_serves_the_researched_terms(client: TestClient):
    body = client.get(f"{PREFIX}/vocabulary").json()
    assert body["poll_interval_minutes"]["value"] == 30
    assert body["existing_tag_only"]["value"] is True
    assert body["sticky_exit_rule"]["value"] == "exit_rule"


def test_the_inferences_route_serves_every_judgement_call(client: TestClient):
    body = client.get(f"{PREFIX}/inferences").json()
    assert body["count"] == len(body["inferences"])
    assert any(e["id"] == "inference_poll_is_on_demand" for e in body["inferences"])


def test_the_summary_and_catalog_read_without_writes(client: TestClient):
    assert client.get(f"{PREFIX}/summary").status_code == 200
    assert client.get(f"{PREFIX}/catalog").status_code == 200
    assert client.get(f"{PREFIX}/tags").status_code == 200
    assert client.get(f"{PREFIX}/series").status_code == 200
    assert client.get(f"{PREFIX}/enrollments").status_code == 200
    assert client.get(f"{PREFIX}/tag-applications").status_code == 200
    assert client.get(f"{PREFIX}/evaluations").status_code == 200


# --------------------------------------------------------------------------- #
# Tags
# --------------------------------------------------------------------------- #


def test_a_tag_can_be_registered_and_listed(client: TestClient):
    created = client.post(f"{PREFIX}/tags", json={"name": "Upgraded after Series"})
    assert created.status_code == 201
    listed = client.get(f"{PREFIX}/tags").json()
    assert listed["count"] == 1
    assert listed["tags"][0]["data"]["name"] == "Upgraded after Series"


def test_a_duplicate_tag_name_is_refused(client: TestClient):
    client.post(f"{PREFIX}/tags", json={"name": "Upgraded after Series"})
    again = client.post(f"{PREFIX}/tags", json={"name": "upgraded after series"})
    assert again.status_code == 409
    assert again.json()["error"] == "tag_name_already_used"


def test_a_tag_with_no_name_is_refused_with_the_field(client: TestClient):
    response = client.post(f"{PREFIX}/tags", json={})
    assert response.status_code == 400
    assert response.json()["errors"]["name"] == "required"


# --------------------------------------------------------------------------- #
# Series and publish
# --------------------------------------------------------------------------- #


def test_a_series_can_be_declared_and_read_back(client: TestClient):
    series_id = make_series(client)
    body = client.get(f"{PREFIX}/series/{series_id}").json()
    assert body["data"]["state"] == "live"
    assert body["tag_blocks"][0]["tag"] == "Upgraded after Series"
    assert body["block_count"] == 3


def test_a_series_with_no_name_is_refused(client: TestClient):
    response = client.post(f"{PREFIX}/series", json={"name": " "})
    assert response.status_code == 400
    assert response.json()["errors"]["name"] == "required"


def test_a_series_that_names_an_unknown_tag_is_refused(client: TestClient):
    response = client.post(
        f"{PREFIX}/series",
        json={
            "name": "x",
            "start_filters": [{"field": "plan", "value": "trial"}],
            "path": [{"kind": "tag", "tag": "Never declared"}],
        },
    )
    assert response.status_code == 400
    assert response.json()["error"] == "tag_not_in_workspace"


def test_publishing_twice_is_a_conflict(client: TestClient):
    series_id = make_series(client)
    again = client.post(f"{PREFIX}/series/{series_id}/publish")
    assert again.status_code == 409
    assert again.json()["error"] == "series_already_live"


def test_publishing_a_series_that_does_not_exist_is_not_found(client: TestClient):
    response = client.post(f"{PREFIX}/series/absent/publish")
    assert response.status_code == 404


# --------------------------------------------------------------------------- #
# The poll
# --------------------------------------------------------------------------- #


def test_a_poll_enrols_a_match_and_records_a_skip(client: TestClient):
    series_id = make_series(client)
    response = client.post(
        f"{PREFIX}/series/{series_id}/poll",
        json={
            "events": [
                {"contact": {"contact_id": "c1", "plan": "trial"}, "event_type": "purchase.made"},
                {"contact": {"contact_id": "c2", "plan": "free"}, "event_type": "purchase.made"},
            ]
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["enrolled"] == 1
    assert body["outcomes"]["skipped_no_match"] == 1
    assert client.get(f"{PREFIX}/evaluations").json()["count"] == 1


def test_a_poll_without_events_is_refused(client: TestClient):
    series_id = make_series(client)
    response = client.post(f"{PREFIX}/series/{series_id}/poll", json={})
    assert response.status_code == 400
    assert response.json()["errors"]["events"] == "required"


def test_polling_a_draft_series_is_a_conflict(client: TestClient):
    client.post(f"{PREFIX}/tags", json={"name": "Upgraded after Series"})
    created = client.post(
        f"{PREFIX}/series",
        json={
            "name": "Draft only",
            "start_filters": [{"field": "plan", "value": "trial"}],
            "path": [{"kind": "tag", "tag": "Upgraded after Series"}],
        },
    )
    series_id = created.json()["id"]
    response = client.post(
        f"{PREFIX}/series/{series_id}/poll",
        json={"events": [{"contact": {"contact_id": "c1", "plan": "trial"}}]},
    )
    assert response.status_code == 409
    assert response.json()["error"] == "series_not_live"


def test_the_due_routes_report_the_cadence(client: TestClient):
    series_id = make_series(client)
    listed = client.get(f"{PREFIX}/due").json()
    assert listed["count"] == 1
    assert listed["due"][0]["series_id"] == series_id
    assert listed["due"][0]["due"] is True

    one = client.get(f"{PREFIX}/series/{series_id}/due").json()
    assert one["due"] is True
    assert one["poll_interval_minutes"] == 30

    client.post(
        f"{PREFIX}/series/{series_id}/poll",
        json={"events": [{"contact": {"contact_id": "c2", "plan": "free"}}]},
    )
    after = client.get(f"{PREFIX}/series/{series_id}/due").json()
    assert after["due"] is False
    assert after["next_due_at"]


# --------------------------------------------------------------------------- #
# Progression, exit, and the tag application
# --------------------------------------------------------------------------- #


def test_walking_the_path_writes_the_tag_onto_the_contact(client: TestClient):
    series_id = make_series(client)
    client.post(
        f"{PREFIX}/series/{series_id}/poll",
        json={"events": [{"contact": {"contact_id": "c1", "plan": "trial"}}]},
    )
    for _ in range(3):
        step = client.post(
            f"{PREFIX}/series/{series_id}/contacts/c1/advance",
            json={"contact": {"contact_id": "c1", "plan": "premium"}},
        )
        assert step.status_code == 201, step.text
    assert step.json()["outcome"] == "completed"
    assert step.json()["goal"]["met"] is True
    applications = client.get(f"{PREFIX}/tag-applications?contact_id=c1").json()
    assert applications["count"] == 1
    assert applications["tag_applications"][0]["data"]["topic"] == "contact.user.tag.created"


def test_advancing_a_contact_that_is_not_enrolled_is_not_found(client: TestClient):
    series_id = make_series(client)
    response = client.post(f"{PREFIX}/series/{series_id}/contacts/ghost/advance", json={})
    assert response.status_code == 404


def test_an_exit_matching_the_rule_is_recorded_and_blocks_reentry(client: TestClient):
    series_id = make_series(client)
    client.post(
        f"{PREFIX}/series/{series_id}/poll",
        json={"events": [{"contact": {"contact_id": "c1", "plan": "trial"}}]},
    )
    exited = client.post(
        f"{PREFIX}/series/{series_id}/contacts/c1/exit",
        json={"contact": {"contact_id": "c1", "tags": "ExitUpgradeSeries"}},
    )
    assert exited.status_code == 200
    assert exited.json()["exit_reason"] == "exit_rule"
    again = client.post(
        f"{PREFIX}/series/{series_id}/poll",
        json={"events": [{"contact": {"contact_id": "c1", "plan": "trial"}}]},
    )
    assert again.json()["outcomes"]["skipped_exited_sticky"] == 1


def test_an_exit_that_does_not_match_the_rule_is_a_conflict(client: TestClient):
    series_id = make_series(client)
    client.post(
        f"{PREFIX}/series/{series_id}/poll",
        json={"events": [{"contact": {"contact_id": "c1", "plan": "trial"}}]},
    )
    response = client.post(
        f"{PREFIX}/series/{series_id}/contacts/c1/exit",
        json={"contact": {"contact_id": "c1", "plan": "premium"}},
    )
    assert response.status_code == 409
    assert response.json()["error"] == "exit_rule_not_matched"


def test_an_exit_without_the_contact_is_refused(client: TestClient):
    series_id = make_series(client)
    response = client.post(f"{PREFIX}/series/{series_id}/contacts/c1/exit", json={})
    assert response.status_code == 400


# --------------------------------------------------------------------------- #
# The audit-source rule, checked against the live route registry
# --------------------------------------------------------------------------- #


def test_every_audit_source_names_a_route_the_host_serves(client: TestClient):
    series_id = make_series(client)
    client.post(
        f"{PREFIX}/series/{series_id}/poll",
        json={
            "events": [
                {"contact": {"contact_id": "c1", "plan": "trial"}, "advance": True},
                {"contact": {"contact_id": "c2", "plan": "trial"}},
            ]
        },
    )
    for _ in range(2):
        client.post(
            f"{PREFIX}/series/{series_id}/contacts/c1/advance",
            json={"contact": {"contact_id": "c1", "plan": "premium"}},
        )
    client.post(
        f"{PREFIX}/series/{series_id}/contacts/c2/exit",
        json={"contact": {"contact_id": "c2", "tags": "ExitUpgradeSeries"}},
    )

    routes = served_routes(client)
    audit = client.app.state.db.audit(limit=1000)
    sources = {
        str(row["source"])
        for row in audit
        if row["collection"] in WF105_COLLECTIONS and row["source"]
    }
    assert sources, "the flow wrote no audited rows"
    for source in sorted(sources):
        assert names_a_served_route(source, routes), source
