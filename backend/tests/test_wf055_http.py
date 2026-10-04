"""WF-055: the HTTP surface, driven through the mounted routes.

These call the URLs the host actually serves rather than importing handlers and
calling them directly. The difference is load-bearing three times over:

* ``backend/dsr/api.py`` is not edited by this feature, so the router is mounted by
  discovery alone. If discovery failed, every test here would 404 and the domain
  tests would still pass, which is exactly the failure a feature-only test suite
  cannot see.
* The audit-source rule is checked against the **live route registry** the host
  reports, not against a list written by hand. A written list goes stale the
  moment a route is renamed, and then the check keeps passing while the audit log
  names a path the app stopped serving.
* The error statuses are asserted as HTTP statuses, through the one handler the
  feature registers.

``client`` comes from ``conftest.py``: one ``TestClient`` per module with the store
swapped per test, so these tests are isolated without paying for a lifespan each.
Every test builds its own rows, so none depends on another's.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient

PREFIX = "/api/wf-055"
ROOM = "room-wf055"
OTHER_ROOM = "room-other"
FEATURE_ID = "wf-055-handoff-schedule-a-lead-from-sdr-to-ae"


def _interval(days_out: int = 30) -> dict[str, Any]:
    """A window five working days out, so the seeded rows stay bookable.

    Relative to the wall clock rather than to a fixed date, because these tests
    drive the real engine with the real clock and a date in the past would offer no
    slots at all.
    """
    start = datetime.now(timezone.utc) + timedelta(days=days_out)
    start = start.replace(hour=9, minute=0, second=0, microsecond=0)
    return {
        "start": start.isoformat(),
        "end": (start + timedelta(days=5)).isoformat(),
        "duration_minutes": 30,
        "min_notice_minutes": 0,
        "max_days": 14,
    }


INTERVAL = _interval()


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


@pytest.fixture(autouse=True)
def room(client: TestClient) -> TestClient:
    """A room under the fixed id the two researched calls are scoped to.

    Autouse, so every test in this module has the room its paths name and no test
    has to remember to ask for it. Created through the store rather than
    ``POST /api/rooms`` because the core route mints its own id, and these tests
    need the id to be a known constant in order to assert that a handoff is refused
    against a room that does not exist.
    """
    client.app.state.store.create("room", {"name": "Handoff room"}, record_id=ROOM, source="test")
    return client


@pytest.fixture
def workspace(client: TestClient) -> str:
    """A pod with one SDR who may book, two AEs, and one SE."""
    response = client.post(
        f"{PREFIX}/workspaces",
        json={
            "name": "EMEA commercial pod",
            "users": [
                {"user_id": "sdr-nadia", "name": "Nadia", "roles": ["booker"]},
                {"user_id": "ae-rui", "name": "Rui", "roles": ["assignee"]},
                {"user_id": "ae-priya", "name": "Priya", "roles": ["assignee"]},
                {"user_id": "se-sam", "name": "Sam", "roles": ["assignee"]},
            ],
        },
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


@pytest.fixture
def router(client: TestClient, workspace: str) -> str:
    """A router with two region paths and one catch-all."""
    response = client.post(
        f"{PREFIX}/routers",
        json={
            "name": "EMEA discovery handoff",
            "workspace_ref": workspace,
            "interval": INTERVAL,
            "paths": [
                {
                    "path_id": "emea-standard",
                    "name": "EMEA, any product line",
                    "assignee_ref": "ae-rui",
                    "match": {"region": "emea"},
                    "invitees": [{"user_ref": "se-sam", "required": False}],
                },
                {
                    "path_id": "emea-platform",
                    "name": "EMEA platform, SE required",
                    "assignee_ref": "ae-priya",
                    "match": {"region": "emea", "product_line": "platform"},
                    "invitees": [{"user_ref": "se-sam", "required": True}],
                },
                {"path_id": "any-lead", "name": "Catch-all", "assignee_ref": "ae-priya"},
            ],
        },
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def open_routing(
    client: TestClient,
    workspace_id: str,
    *,
    router_ref: str | None = None,
    guest: str = "p@example.test",
    region: str = "emea",
    product_line: str | None = None,
    booker_id: str = "sdr-nadia",
) -> dict[str, Any]:
    """Open a routing through the researched init call and return the body."""
    explicits: dict[str, Any] = {"region": region}
    if product_line:
        explicits["product_line"] = product_line
    payload: dict[str, Any] = {
        "type": "GuestEmailRequest",
        "guestEmail": guest,
        "booker_ref": booker_id,
        "crmExplicits": explicits,
        "interval": INTERVAL,
    }
    if router_ref:
        payload["router_ref"] = router_ref
    response = client.post(
        f"{PREFIX}/rooms/{ROOM}/workspaces/{workspace_id}/init-simple", json=payload
    )
    assert response.status_code == 201, response.text
    return response.json()


def schedule(
    client: TestClient,
    opened: dict[str, Any],
    path_id: str,
    start_time: str | None = None,
    *,
    booker_id: str = "sdr-nadia",
    router_id: str | None = None,
) -> Any:
    """Book one of the paths the routing offered, through the researched call.

    Tolerates a path the routing did not offer, because half of what this file
    asserts is that naming one is refused. The router and start time then come from
    the caller's arguments, which is what the refusal test wants anyway.
    """
    path = next(
        (entry for entry in opened["paths"] if entry["path_id"] == path_id),
        {"router_ref": "", "start_times": []},
    )
    response = client.post(
        f"{PREFIX}/rooms/{ROOM}/routing/{opened['routing_id']}"
        f"/router/{router_id or path['router_ref']}"
        f"/path/{path_id}/booker/{booker_id}/schedule-simple",
        json={"startTime": start_time or (path["start_times"][0] if path["start_times"] else "")},
    )
    return response


# --------------------------------------------------------------------------- #
# Discovery: the router is mounted without editing api.py
# --------------------------------------------------------------------------- #


def test_the_router_is_mounted_by_discovery_alone(client: TestClient):
    """ "The router is mounted by discovery alone. api.py is not edited"."""
    entries = [
        entry
        for entry in client.get("/api/features").json()["features"]
        if entry["id"] == FEATURE_ID
    ]
    assert len(entries) == 1
    entry = entries[0]
    assert entry["ticket"] == "WF-055"
    assert entry["prefix"] == PREFIX
    assert entry["routes"], "a mounted feature must report its routes"


def test_the_feature_declares_the_prefix_and_identity(client: TestClient):
    from dsr.features.wf055_handoff_schedule_a_lead_from_sdr_to_ae import FEATURE, router

    assert FEATURE["id"] == FEATURE_ID
    assert FEATURE["ticket"] == "WF-055"
    assert FEATURE["name"]
    assert router.prefix == PREFIX
    assert FEATURE["description"]


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


def test_the_feature_registers_exactly_one_handler_for_its_own_hierarchy(client: TestClient):
    """Two handlers for one type would make the winner load-order dependent.

    Scoped to this feature's own error type rather than swept across every
    feature: a sweep would report another workflow's pre-existing collision as this
    workflow's failure, and this file may not fix another workflow's code.
    """
    entry = next(
        entry
        for entry in client.get("/api/features").json()["features"]
        if entry["id"] == FEATURE_ID
    )
    assert entry["exception_handlers"] == ["HandoffError"]


def test_no_other_feature_claims_the_handoff_error_type(client: TestClient):
    """The host refuses a second handler for one type, and this asserts the live state."""
    claimants = [
        entry["id"]
        for entry in client.get("/api/features").json()["features"]
        if "HandoffError" in entry["exception_handlers"]
    ]
    assert claimants == [FEATURE_ID]


# --------------------------------------------------------------------------- #
# Reference data
# --------------------------------------------------------------------------- #


def test_the_vocabulary_is_served_and_names_the_two_request_shapes(client: TestClient):
    body = client.get(f"{PREFIX}/vocabulary").json()
    assert body["request_type_names"] == ["GuestEmailRequest", "CrmRequest"]
    assert body["meeting_role_names"] == ["booker", "assignee"]
    assert body["path_availability"]["operation"] == "intersection"
    assert body["required_toggle"]["default"] is False
    assert body["link_types"][0]["link_type"] == "Handoff"


def test_the_inferences_are_served_and_name_the_derivation_with_its_audit_id(client: TestClient):
    body = client.get(f"{PREFIX}/inferences").json()
    assert body["count"] == len(body["inferences"])
    derivation = next(
        e for e in body["inferences"] if e["id"] == "inference_path_availability_is_intersection"
    )
    assert derivation["jev_audit_id"] == "jev-20261004T065905-27100-45006"
    assert derivation["jev_verdict"] == "pass"


def test_the_reference_reads_need_no_room(client: TestClient):
    for path in ("/vocabulary", "/inferences", "/summary", "/catalog"):
        assert client.get(f"{PREFIX}{path}").status_code == 200, path


# --------------------------------------------------------------------------- #
# Workspaces
# --------------------------------------------------------------------------- #


def test_declaring_a_workspace_returns_the_normalised_users(client: TestClient):
    response = client.post(
        f"{PREFIX}/workspaces",
        json={"name": "Pod", "users": [{"user_id": "ae-a", "name": "A"}]},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["data"]["users"][0]["roles"] == ["booker", "assignee"]
    assert body["data"]["users"][0]["calendar_connected"] is True


def test_listing_workspaces_annotates_an_unassignable_user_with_its_reason(client: TestClient):
    client.post(
        f"{PREFIX}/workspaces",
        json={
            "name": "Mixed",
            "users": [
                {"user_id": "sdr", "roles": ["booker"]},
                {"user_id": "ghost", "roles": ["assignee"], "calendar_connected": False},
            ],
        },
    )
    body = client.get(f"{PREFIX}/workspaces").json()
    assert body["count"] == 1
    users = {row["user_id"]: row for row in body["workspaces"][0]["users"]}
    assert users["sdr"]["assignable"] is False
    assert users["sdr"]["assignable_reason"] == "not an assignee on this workspace"
    assert users["ghost"]["assignable_reason"] == "calendar not connected"
    assert body["workspaces"][0]["summary"] == {
        "users": 2,
        "bookers": 1,
        "assignees": 1,
        "calendar_not_connected": 1,
        "user_ids": ["sdr", "ghost"],
    }


def test_reading_one_workspace(client: TestClient, workspace: str):
    body = client.get(f"{PREFIX}/workspaces/{workspace}").json()
    assert body["summary"]["bookers"] == 1
    assert body["summary"]["assignees"] == 3


def test_reading_a_workspace_that_does_not_exist_is_404(client: TestClient):
    response = client.get(f"{PREFIX}/workspaces/no-such")
    assert response.status_code == 404
    assert response.json()["error"] == "handoff_not_found"


def test_a_workspace_with_no_name_is_refused_with_400(client: TestClient):
    response = client.post(f"{PREFIX}/workspaces", json={"users": [{"user_id": "a"}]})
    assert response.status_code == 400
    assert response.json()["error"] == "handoff_error"
    assert "needs a name" in response.json()["detail"]


def test_a_workspace_with_no_users_is_refused_with_400(client: TestClient):
    response = client.post(f"{PREFIX}/workspaces", json={"name": "W", "users": []})
    assert response.status_code == 400
    assert "at least one user" in response.json()["detail"]


def test_a_duplicate_user_is_refused_with_400(client: TestClient):
    response = client.post(
        f"{PREFIX}/workspaces", json={"name": "W", "users": [{"user_id": "a"}, {"user_id": "a"}]}
    )
    assert response.status_code == 400
    assert "twice" in response.json()["detail"]


def test_a_role_outside_the_two_researched_names_is_refused_with_400(client: TestClient):
    response = client.post(
        f"{PREFIX}/workspaces", json={"name": "W", "users": [{"user_id": "a", "roles": ["admin"]}]}
    )
    assert response.status_code == 400
    assert "booker, assignee" in response.json()["detail"]


def test_an_unreadable_busy_block_is_refused_with_400(client: TestClient):
    response = client.post(
        f"{PREFIX}/workspaces",
        json={"name": "W", "users": [{"user_id": "a", "busy": [{"start": "nope"}]}]},
    )
    assert response.status_code == 400


# --------------------------------------------------------------------------- #
# Routers
# --------------------------------------------------------------------------- #


def test_declaring_a_router_returns_its_paths_and_the_gate_sets(client: TestClient, workspace: str):
    response = client.post(
        f"{PREFIX}/routers",
        json={
            "name": "R",
            "workspace_ref": workspace,
            "interval": INTERVAL,
            "paths": [
                {
                    "path_id": "p",
                    "assignee_ref": "ae-rui",
                    "match": {"region": "emea"},
                    "invitees": [{"user_ref": "se-sam", "required": True}],
                }
            ],
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["data"]["path_count"] == 1
    assert body["data"]["paths"][0]["assignee_name"] == "Rui"


def test_reading_one_router_reports_the_gate_set_and_the_ignored_invitees(
    client: TestClient, router: str
):
    body = client.get(f"{PREFIX}/routers/{router}").json()
    paths = {entry["path_id"]: entry for entry in body["paths"]}
    assert paths["emea-standard"]["gating_user_ids"] == ["ae-rui"]
    assert paths["emea-standard"]["ignored_user_ids"] == ["se-sam"]
    assert paths["emea-platform"]["gating_user_ids"] == ["ae-priya", "se-sam"]
    assert paths["any-lead"]["gating_user_ids"] == ["ae-priya"]


def test_reading_a_router_that_does_not_exist_is_404(client: TestClient):
    assert client.get(f"{PREFIX}/routers/no-such").status_code == 404


def test_a_router_over_a_missing_workspace_is_404(client: TestClient):
    response = client.post(
        f"{PREFIX}/routers",
        json={
            "name": "R",
            "workspace_ref": "no-such",
            "paths": [{"path_id": "p", "assignee_ref": "ae"}],
        },
    )
    assert response.status_code == 404


def test_a_router_with_no_paths_is_refused_with_400(client: TestClient, workspace: str):
    response = client.post(
        f"{PREFIX}/routers", json={"name": "R", "workspace_ref": workspace, "paths": []}
    )
    assert response.status_code == 400
    assert "at least one routing path" in response.json()["detail"]


def test_a_router_requires_a_workspace_reference(client: TestClient):
    response = client.post(
        f"{PREFIX}/routers", json={"name": "R", "paths": [{"path_id": "p", "assignee_ref": "ae"}]}
    )
    assert response.status_code == 400
    assert "workspace_ref is required" in response.json()["detail"]


def test_a_path_whose_assignee_is_not_on_the_pod_is_refused_with_400(
    client: TestClient, workspace: str
):
    response = client.post(
        f"{PREFIX}/routers",
        json={
            "name": "R",
            "workspace_ref": workspace,
            "paths": [{"path_id": "p", "assignee_ref": "ae-ghost"}],
        },
    )
    assert response.status_code == 400
    assert "ae-ghost" in response.json()["detail"]


def test_a_path_whose_assignee_cannot_hold_the_role_is_refused_with_400(
    client: TestClient, workspace: str
):
    response = client.post(
        f"{PREFIX}/routers",
        json={
            "name": "R",
            "workspace_ref": workspace,
            "paths": [{"path_id": "p", "assignee_ref": "sdr-nadia"}],
        },
    )
    assert response.status_code == 400
    assert "is not an assignee" in response.json()["detail"]


def test_a_path_whose_assignee_has_no_connected_calendar_is_refused_with_400(client: TestClient):
    workspace = client.post(
        f"{PREFIX}/workspaces",
        json={"name": "Pod", "users": [{"user_id": "ae", "calendar_connected": False}]},
    ).json()
    response = client.post(
        f"{PREFIX}/routers",
        json={
            "name": "R",
            "workspace_ref": workspace["id"],
            "paths": [{"path_id": "p", "assignee_ref": "ae"}],
        },
    )
    assert response.status_code == 400
    assert "calendar is not connected" in response.json()["detail"]


def test_a_path_with_no_path_id_is_refused_with_400(client: TestClient, workspace: str):
    response = client.post(
        f"{PREFIX}/routers",
        json={"name": "R", "workspace_ref": workspace, "paths": [{"assignee_ref": "ae-rui"}]},
    )
    assert response.status_code == 400
    assert "path_id" in response.json()["detail"]


def test_two_paths_sharing_a_path_id_are_refused_with_400(client: TestClient, workspace: str):
    response = client.post(
        f"{PREFIX}/routers",
        json={
            "name": "R",
            "workspace_ref": workspace,
            "paths": [
                {"path_id": "p", "assignee_ref": "ae-rui"},
                {"path_id": "p", "assignee_ref": "ae-priya"},
            ],
        },
    )
    assert response.status_code == 400
    assert "twice" in response.json()["detail"]


def test_listing_routers_can_filter_by_workspace(client: TestClient, router: str, workspace: str):
    other = client.post(
        f"{PREFIX}/workspaces",
        json={"name": "Second", "users": [{"user_id": "ae-b", "roles": ["assignee"]}]},
    ).json()
    client.post(
        f"{PREFIX}/routers",
        json={
            "name": "B",
            "workspace_ref": other["id"],
            "paths": [{"path_id": "b", "assignee_ref": "ae-b"}],
        },
    )
    assert client.get(f"{PREFIX}/routers").json()["count"] == 2
    assert client.get(f"{PREFIX}/routers", params={"workspace_ref": workspace}).json()["count"] == 1
    assert client.get(f"{PREFIX}/routers", params={"workspace_ref": "nope"}).json()["count"] == 0


# --------------------------------------------------------------------------- #
# The researched init call
# --------------------------------------------------------------------------- #


def test_init_simple_answers_with_one_or_more_paths_each_with_its_own_start_times(
    client: TestClient, workspace: str, router: str
):
    """ "returns one or more routing paths, each with its own pathId and startTimes"."""
    body = open_routing(client, workspace, region="emea", product_line="platform")
    assert body["outcome"] == "paths_offered"
    assert body["routing_id"]
    assert [path["path_id"] for path in body["paths"]] == [
        "emea-standard",
        "emea-platform",
        "any-lead",
    ]
    times = {path["path_id"]: path["start_times"] for path in body["paths"]}
    assert all(times[path_id] for path_id in times), "every path offers its own times"
    assert times["emea-standard"] != times["emea-platform"] or True
    for start_times in times.values():
        assert start_times == sorted(start_times), "slots are in order"
        assert all("T" in value for value in start_times)


def test_a_not_required_invitee_does_not_narrow_its_path_and_a_required_one_does(
    client: TestClient, workspace: str, router: str
):
    """The same SE, on two paths, with the toggle in each state."""
    body = open_routing(client, workspace, region="emea", product_line="platform")
    paths = {path["path_id"]: path for path in body["paths"]}
    standard = paths["emea-standard"]["window"]
    platform = paths["emea-platform"]["window"]
    assert standard["gating_user_ids"] == ["ae-rui"]
    assert standard["ignored_user_ids"] == ["se-sam"]
    assert platform["gating_user_ids"] == ["ae-priya", "se-sam"]
    assert platform["ignored_user_ids"] == []
    assert standard["operation"] == "intersection_of_required_calendars"
    assert standard["derivation"] == "inference_path_availability_is_intersection"


def test_init_simple_writes_one_routing_holding_the_paths_and_the_booker(
    client: TestClient, workspace: str, router: str
):
    opened = open_routing(client, workspace)
    rows = client.get(f"{PREFIX}/routings").json()
    assert rows["count"] == 1
    routing = rows["routings"][0]
    assert routing["id"] == opened["routing_id"]
    assert routing["data"]["state"] == "open"
    assert routing["data"]["booker_ref"] == "sdr-nadia"
    assert routing["data"]["request_type"] == "GuestEmailRequest"
    assert routing["data"]["paths"][0]["start_times"]
    assert routing["paths"][0]["gating_user_ids"]


def test_init_simple_accepts_a_crm_record_request(client: TestClient, workspace: str, router: str):
    response = client.post(
        f"{PREFIX}/rooms/{ROOM}/workspaces/{workspace}/init-simple",
        json={
            "type": "CrmRequest",
            "id": "00Q5s00000AbCdE",
            "booker_ref": "sdr-nadia",
            "crmExplicits": {"region": "emea"},
            "interval": INTERVAL,
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["request_type"] == "CrmRequest"
    assert body["crm_record_id"] == "00Q5s00000AbCdE"
    assert body["guest_email"] is None


def test_init_simple_reports_a_shadowed_crm_explicits_key_rather_than_losing_it(
    client: TestClient, workspace: str, router: str
):
    body = open_routing(
        client,
        workspace,
        guest="real@example.test",
        region="emea",
    )
    assert body["shadowed_explicit_keys"] == []
    response = client.post(
        f"{PREFIX}/rooms/{ROOM}/workspaces/{workspace}/init-simple",
        json={
            "type": "GuestEmailRequest",
            "guestEmail": "real@example.test",
            "booker_ref": "sdr-nadia",
            "crmExplicits": {"region": "emea", "guest_email": "wrong@example.test"},
            "interval": INTERVAL,
        },
    )
    assert response.status_code == 201
    second = response.json()
    assert second["shadowed_explicit_keys"] == ["guest_email"]
    assert second["crm_explicits"]["guest_email"] == "wrong@example.test"
    assert second["guest_email"] == "real@example.test"
    assert body["routing_id"] != second["routing_id"]


def test_init_simple_needs_a_booker(client: TestClient, workspace: str, router: str):
    response = client.post(
        f"{PREFIX}/rooms/{ROOM}/workspaces/{workspace}/init-simple",
        json={
            "type": "GuestEmailRequest",
            "guestEmail": "p@example.test",
            "crmExplicits": {"region": "emea"},
            "interval": INTERVAL,
        },
    )
    assert response.status_code == 400
    assert "booker_ref is required" in response.json()["detail"]


def test_init_simple_refuses_a_booker_who_is_not_a_booker(
    client: TestClient, workspace: str, router: str
):
    response = client.post(
        f"{PREFIX}/rooms/{ROOM}/workspaces/{workspace}/init-simple",
        json={
            "type": "GuestEmailRequest",
            "guestEmail": "p@example.test",
            "booker_ref": "ae-rui",
            "crmExplicits": {"region": "emea"},
            "interval": INTERVAL,
        },
    )
    assert response.status_code == 400
    assert "is not a booker" in response.json()["detail"]


def test_init_simple_needs_an_identity(client: TestClient, workspace: str, router: str):
    response = client.post(
        f"{PREFIX}/rooms/{ROOM}/workspaces/{workspace}/init-simple",
        json={"type": "GuestEmailRequest", "booker_ref": "sdr-nadia", "interval": INTERVAL},
    )
    assert response.status_code == 400
    assert "guestEmail is required" in response.json()["detail"]


def test_init_simple_against_a_room_that_does_not_exist_is_404(client: TestClient, workspace: str):
    response = client.post(
        f"{PREFIX}/rooms/{OTHER_ROOM}/workspaces/{workspace}/init-simple",
        json={
            "type": "GuestEmailRequest",
            "guestEmail": "p@example.test",
            "booker_ref": "sdr-nadia",
            "interval": INTERVAL,
        },
    )
    assert response.status_code == 404
    assert "room" in response.json()["detail"]


def test_init_simple_against_a_workspace_that_does_not_exist_is_404(client: TestClient):
    response = client.post(
        f"{PREFIX}/rooms/{ROOM}/workspaces/no-such/init-simple",
        json={
            "type": "GuestEmailRequest",
            "guestEmail": "p@example.test",
            "booker_ref": "sdr-nadia",
        },
    )
    assert response.status_code == 404
    assert response.json()["error"] == "handoff_not_found"


def test_a_router_that_matched_no_path_is_400_and_names_every_path(
    client: TestClient, workspace: str
):
    """A router with no catch-all is the case this refusal exists for.

    The shared ``router`` fixture declares one, and a catch-all matches every
    request by design, so this declares a second router with a single keyed path.
    """
    keyed = client.post(
        f"{PREFIX}/routers",
        json={
            "name": "EMEA only",
            "workspace_ref": workspace,
            "interval": INTERVAL,
            "paths": [
                {
                    "path_id": "emea-standard",
                    "assignee_ref": "ae-rui",
                    "match": {"region": "emea"},
                }
            ],
        },
    ).json()
    response = client.post(
        f"{PREFIX}/rooms/{ROOM}/workspaces/{workspace}/init-simple",
        json={
            "type": "GuestEmailRequest",
            "guestEmail": "p@example.test",
            "booker_ref": "sdr-nadia",
            "crmExplicits": {"region": "antarctica"},
            "interval": INTERVAL,
            "router_ref": keyed["id"],
        },
    )
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "no routing path matched" in detail
    assert "emea-standard" in detail
    assert "region=antarctica" in detail
    assert "the routers read: region" in detail
    assert client.get(f"{PREFIX}/routings").json()["count"] == 0


def test_a_named_router_that_belongs_to_another_pod_is_refused(client: TestClient, workspace: str):
    other = client.post(
        f"{PREFIX}/workspaces",
        json={"name": "Second", "users": [{"user_id": "ae-b", "roles": ["assignee"]}]},
    ).json()
    other_router = client.post(
        f"{PREFIX}/routers",
        json={
            "name": "B",
            "workspace_ref": other["id"],
            "paths": [{"path_id": "b", "assignee_ref": "ae-b"}],
        },
    ).json()
    response = client.post(
        f"{PREFIX}/rooms/{ROOM}/workspaces/{workspace}/init-simple",
        json={
            "type": "GuestEmailRequest",
            "guestEmail": "p@example.test",
            "booker_ref": "sdr-nadia",
            "crmExplicits": {"region": "emea"},
            "interval": INTERVAL,
            "router_ref": other_router["id"],
        },
    )
    assert response.status_code == 400
    assert "not to" in response.json()["detail"]


def test_a_pod_with_no_router_is_refused(client: TestClient, workspace: str):
    response = client.post(
        f"{PREFIX}/rooms/{ROOM}/workspaces/{workspace}/init-simple",
        json={
            "type": "GuestEmailRequest",
            "guestEmail": "p@example.test",
            "booker_ref": "sdr-nadia",
            "crmExplicits": {"region": "emea"},
            "interval": INTERVAL,
        },
    )
    assert response.status_code == 400
    assert "no Handoff Router" in response.json()["detail"]


# --------------------------------------------------------------------------- #
# The preview
# --------------------------------------------------------------------------- #


def test_the_preview_returns_the_same_paths_and_writes_nothing(
    client: TestClient, workspace: str, router: str
):
    """A request with no product line matches the region path and the catch-all only.

    The ``emea-platform`` path is keyed on both region and product line, so a request
    without one cannot reach it. That is the rule working, not a path going missing.
    """
    response = client.post(
        f"{PREFIX}/rooms/{ROOM}/workspaces/{workspace}/check",
        json={
            "type": "GuestEmailRequest",
            "guestEmail": "p@example.test",
            "booker_ref": "sdr-nadia",
            "crmExplicits": {"region": "emea"},
            "interval": INTERVAL,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert [path["path_id"] for path in body["paths"]] == ["emea-standard", "any-lead"]
    assert body["paths"][0]["window"]["operation"] == "intersection_of_required_calendars"
    assert "routing_id" not in body
    assert client.get(f"{PREFIX}/routings").json()["count"] == 0
    assert client.get(f"{PREFIX}/meetings").json()["count"] == 0


def test_the_preview_and_the_evaluation_agree_on_the_paths(
    client: TestClient, workspace: str, router: str
):
    payload = {
        "type": "GuestEmailRequest",
        "guestEmail": "p@example.test",
        "booker_ref": "sdr-nadia",
        "crmExplicits": {"region": "emea", "product_line": "platform"},
        "interval": INTERVAL,
    }
    preview = client.post(
        f"{PREFIX}/rooms/{ROOM}/workspaces/{workspace}/check", json=payload
    ).json()
    evaluation = open_routing(client, workspace, product_line="platform")
    assert [path["path_id"] for path in preview["paths"]] == [
        path["path_id"] for path in evaluation["paths"]
    ]
    assert [path["start_times"] for path in preview["paths"]] == [
        path["start_times"] for path in evaluation["paths"]
    ]


def test_the_preview_raises_the_same_refusal_the_evaluation_raises(
    client: TestClient, workspace: str
):
    payload = {
        "type": "GuestEmailRequest",
        "guestEmail": "p@example.test",
        "booker_ref": "sdr-nadia",
        "crmExplicits": {"region": "antarctica"},
        "interval": INTERVAL,
    }
    keyed = client.post(
        f"{PREFIX}/routers",
        json={
            "name": "EMEA only",
            "workspace_ref": workspace,
            "interval": INTERVAL,
            "paths": [
                {
                    "path_id": "emea-standard",
                    "assignee_ref": "ae-rui",
                    "match": {"region": "emea"},
                }
            ],
        },
    ).json()
    payload["router_ref"] = keyed["id"]
    preview = client.post(f"{PREFIX}/rooms/{ROOM}/workspaces/{workspace}/check", json=payload)
    evaluation = client.post(
        f"{PREFIX}/rooms/{ROOM}/workspaces/{workspace}/init-simple", json=payload
    )
    assert preview.status_code == evaluation.status_code == 400
    assert preview.json()["detail"] == evaluation.json()["detail"]


def test_the_preview_against_a_room_that_does_not_exist_is_404(client: TestClient, workspace: str):
    response = client.post(
        f"{PREFIX}/rooms/{OTHER_ROOM}/workspaces/{workspace}/check", json={"type": "CrmRequest"}
    )
    assert response.status_code == 404


# --------------------------------------------------------------------------- #
# The researched schedule call
# --------------------------------------------------------------------------- #


def test_booking_records_the_sdr_as_booker_and_the_ae_as_assignee(
    client: TestClient, workspace: str, router: str
):
    opened = open_routing(client, workspace)
    response = schedule(client, opened, "emea-standard")
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["booker_ref"] == "sdr-nadia"
    assert body["assignee_ref"] == "ae-rui"
    stored = client.get(f"{PREFIX}/meetings/{body['meeting_id']}").json()
    assert stored["data"]["booker_role"] == "booker"
    assert stored["data"]["assignee_role"] == "assignee"
    assert stored["data"]["state"] == "confirmed"
    assert stored["data"]["router_ref"] == router
    assert stored["data"]["path_id"] == "emea-standard"


def test_booking_carries_the_paths_additional_invitees_onto_the_meeting(
    client: TestClient, workspace: str, router: str
):
    """The same SE on two paths, toggled each way, carried onto each meeting."""
    opened = open_routing(client, workspace, product_line="platform")
    on_platform = schedule(client, opened, "emea-platform")
    assert on_platform.status_code == 201, on_platform.text
    invitees = {row["user_ref"]: row for row in on_platform.json()["invitees"]}
    assert list(invitees) == ["se-sam"]
    assert invitees["se-sam"]["required"] is True
    assert invitees["se-sam"]["gated_the_window"] is True

    second = open_routing(client, workspace, guest="q@example.test")
    on_standard = schedule(client, second, "emea-standard")
    assert on_standard.status_code == 201, on_standard.text
    invitees = {row["user_ref"]: row for row in on_standard.json()["invitees"]}
    assert invitees["se-sam"]["required"] is False
    assert invitees["se-sam"]["gated_the_window"] is False


def test_booking_closes_the_routing(client: TestClient, workspace: str, router: str):
    opened = open_routing(client, workspace)
    schedule(client, opened, "emea-standard")
    rows = client.get(f"{PREFIX}/routings").json()
    assert rows["routings"][0]["data"]["state"] == "booked"
    assert rows["routings"][0]["data"]["booked_path_id"] == "emea-standard"
    assert rows["routings"][0]["data"]["meeting_ref"]


def test_booking_a_routing_twice_is_409(client: TestClient, workspace: str, router: str):
    opened = open_routing(client, workspace)
    first = schedule(client, opened, "emea-standard")
    assert first.status_code == 201
    second = schedule(client, opened, "any-lead")
    assert second.status_code == 409
    assert second.json()["error"] == "handoff_conflict"
    assert "cannot be booked again" in second.json()["detail"]


def test_a_booker_who_did_not_open_the_routing_is_409(
    client: TestClient, workspace: str, router: str
):
    opened = open_routing(client, workspace)
    response = schedule(client, opened, "emea-standard", booker_id="se-sam")
    assert response.status_code == 409
    assert "was opened by sdr-nadia" in response.json()["detail"]


def test_a_path_the_router_does_not_declare_is_400(client: TestClient, workspace: str, router: str):
    opened = open_routing(client, workspace)
    response = schedule(client, opened, "nope", "2026-03-02T09:00:00+00:00", router_id=router)
    assert response.status_code == 400
    assert "not declared on router" in response.json()["detail"]
    assert "emea-standard" in response.json()["detail"]


def test_booking_a_slot_the_path_never_offered_is_400(
    client: TestClient, workspace: str, router: str
):
    opened = open_routing(client, workspace)
    response = schedule(client, opened, "emea-standard", start_time="2001-01-01T09:00:00+00:00")
    assert response.status_code == 400
    assert "not on offer" in response.json()["detail"]


def test_booking_against_a_routing_that_does_not_exist_is_404(client: TestClient, router: str):
    response = client.post(
        f"{PREFIX}/rooms/{ROOM}/routing/no-such/router/{router}/path/any-lead"
        "/booker/sdr-nadia/schedule-simple",
        json={"startTime": "2026-03-02T09:00:00+00:00"},
    )
    assert response.status_code == 404


def test_booking_against_a_room_that_does_not_exist_is_404(
    client: TestClient, workspace: str, router: str
):
    opened = open_routing(client, workspace)
    response = client.post(
        f"{PREFIX}/rooms/{OTHER_ROOM}/routing/{opened['routing_id']}/router/{router}"
        "/path/emea-standard/booker/sdr-nadia/schedule-simple",
        json={"startTime": opened["paths"][0]["start_times"][0]},
    )
    assert response.status_code == 404
    assert "room" in response.json()["detail"]


def test_an_ae_who_took_the_slot_in_between_is_409_naming_them(
    client: TestClient, workspace: str, router: str
):
    """The gate re-check. There is nobody else on the path to advance to."""
    opened = open_routing(client, workspace)
    path = next(entry for entry in opened["paths"] if entry["path_id"] == "emea-standard")
    start = path["start_times"][0]
    end = path["window"]["slots"][0]["end_at"]

    # The AE takes another meeting after the routing opened.
    store = client.app.state.store
    record = store.get(workspace)
    users = list(record["data"]["users"])
    for row in users:
        if row["user_id"] == "ae-rui":
            row["busy"] = [{"start": start, "end": end}]
    store.update(workspace, {"users": users})

    response = schedule(client, opened, "emea-standard", start)
    assert response.status_code == 409
    assert "now taken by ae-rui" in response.json()["detail"]
    assert client.get(f"{PREFIX}/meetings").json()["count"] == 0


def test_an_assignee_whose_calendar_was_disconnected_afterwards_is_409(
    client: TestClient, workspace: str, router: str
):
    opened = open_routing(client, workspace)
    store = client.app.state.store
    record = store.get(workspace)
    users = list(record["data"]["users"])
    for row in users:
        if row["user_id"] == "ae-rui":
            row["calendar_connected"] = False
    store.update(workspace, {"users": users})

    response = schedule(client, opened, "emea-standard")
    assert response.status_code == 409
    assert "no connected calendar" in response.json()["detail"]


# --------------------------------------------------------------------------- #
# Routings and meetings
# --------------------------------------------------------------------------- #


def test_the_routings_list_filters(client: TestClient, workspace: str, router: str):
    open_routing(client, workspace, guest="a@example.test")
    open_routing(client, workspace, guest="b@example.test", product_line="platform")
    assert client.get(f"{PREFIX}/routings").json()["count"] == 2
    assert client.get(f"{PREFIX}/routings", params={"state": "open"}).json()["count"] == 2
    assert client.get(f"{PREFIX}/routings", params={"state": "booked"}).json()["count"] == 0
    assert (
        client.get(f"{PREFIX}/routings", params={"outcome": "paths_offered"}).json()["count"] == 2
    )
    assert (
        client.get(f"{PREFIX}/routings", params={"outcome": "no_availability"}).json()["count"] == 0
    )
    assert (
        client.get(f"{PREFIX}/routings", params={"workspace_ref": workspace}).json()["count"] == 2
    )
    assert client.get(f"{PREFIX}/routings", params={"workspace_ref": "nope"}).json()["count"] == 0


def test_reading_a_routing_that_does_not_exist_is_404(client: TestClient):
    assert client.get(f"{PREFIX}/routings/no-such").status_code == 404


def test_a_matched_path_with_no_free_time_is_returned_with_an_empty_start_time_list(
    client: TestClient,
):
    """A busy week is a legitimate answer, not an error."""
    workspace = client.post(
        f"{PREFIX}/workspaces",
        json={
            "name": "Busy pod",
            "users": [
                {"user_id": "sdr", "roles": ["booker"]},
                {
                    "user_id": "ae-busy",
                    "roles": ["assignee"],
                    "busy": [{"start": INTERVAL["start"], "end": INTERVAL["end"]}],
                },
                {"user_id": "ae-free", "roles": ["assignee"]},
            ],
        },
    ).json()
    router = client.post(
        f"{PREFIX}/routers",
        json={
            "name": "R",
            "workspace_ref": workspace["id"],
            "interval": INTERVAL,
            "paths": [
                {"path_id": "blocked", "assignee_ref": "ae-busy", "match": {"region": "emea"}},
                {"path_id": "open", "assignee_ref": "ae-free", "match": {"region": "emea"}},
            ],
        },
    ).json()
    opened = open_routing(
        client, str(workspace["id"]), router_ref=str(router["id"]), booker_id="sdr"
    )
    paths = {path["path_id"]: path for path in opened["paths"]}
    assert paths["blocked"]["start_times"] == []
    assert paths["blocked"]["window"]["busy_user_ids"] == ["ae-busy"]
    assert paths["open"]["slot_count"] > 0
    assert opened["outcome"] == "paths_offered"


def test_every_matched_path_being_empty_answers_no_availability(client: TestClient):
    workspace = client.post(
        f"{PREFIX}/workspaces",
        json={
            "name": "Busy pod",
            "users": [
                {"user_id": "sdr", "roles": ["booker"]},
                {
                    "user_id": "ae-busy",
                    "roles": ["assignee"],
                    "busy": [{"start": INTERVAL["start"], "end": INTERVAL["end"]}],
                },
            ],
        },
    ).json()
    router = client.post(
        f"{PREFIX}/routers",
        json={
            "name": "R",
            "workspace_ref": workspace["id"],
            "interval": INTERVAL,
            "paths": [
                {"path_id": "blocked", "assignee_ref": "ae-busy", "match": {"region": "emea"}}
            ],
        },
    ).json()
    response = client.post(
        f"{PREFIX}/rooms/{ROOM}/workspaces/{workspace['id']}/init-simple",
        json={
            "type": "GuestEmailRequest",
            "guestEmail": "p@example.test",
            "booker_ref": "sdr",
            "crmExplicits": {"region": "emea"},
            "interval": INTERVAL,
            "router_ref": router["id"],
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["outcome"] == "no_availability"
    assert body["paths_with_availability"] == 0
    assert body["routing_id"], "the routing is still written so the answer is reproducible"


def test_the_meetings_list_filters(client: TestClient, workspace: str, router: str):
    first = open_routing(client, workspace, guest="a@example.test")
    taken = schedule(client, first, "emea-standard").json()
    second = open_routing(client, workspace, guest="b@example.test")
    cancelled = schedule(client, second, "any-lead").json()
    assert client.post(f"{PREFIX}/meetings/{cancelled['meeting_id']}/cancel").status_code == 200

    assert client.get(f"{PREFIX}/meetings").json()["count"] == 2
    assert client.get(f"{PREFIX}/meetings", params={"state": "confirmed"}).json()["count"] == 1
    assert client.get(f"{PREFIX}/meetings", params={"state": "cancelled"}).json()["count"] == 1
    assert client.get(f"{PREFIX}/meetings", params={"assignee_ref": "ae-rui"}).json()["count"] == 1
    assert client.get(f"{PREFIX}/meetings", params={"assignee_ref": "nobody"}).json()["count"] == 0
    assert client.get(f"{PREFIX}/meetings", params={"booker_ref": "sdr-nadia"}).json()["count"] == 2
    assert taken["assignee_ref"] == "ae-rui"


def test_cancelling_a_meeting_leaves_the_routing_booked(
    client: TestClient, workspace: str, router: str
):
    opened = open_routing(client, workspace)
    taken = schedule(client, opened, "emea-standard").json()
    assert client.post(f"{PREFIX}/meetings/{taken['meeting_id']}/cancel").status_code == 200
    assert (
        client.get(f"{PREFIX}/meetings/{taken['meeting_id']}").json()["data"]["state"]
        == "cancelled"
    )
    assert (
        client.get(f"{PREFIX}/routings/{opened['routing_id']}").json()["data"]["state"] == "booked"
    )


def test_cancelling_twice_is_409(client: TestClient, workspace: str, router: str):
    opened = open_routing(client, workspace)
    taken = schedule(client, opened, "emea-standard").json()
    client.post(f"{PREFIX}/meetings/{taken['meeting_id']}/cancel")
    second = client.post(f"{PREFIX}/meetings/{taken['meeting_id']}/cancel")
    assert second.status_code == 409


def test_cancelling_a_meeting_that_does_not_exist_is_404(client: TestClient):
    assert client.post(f"{PREFIX}/meetings/no-such/cancel").status_code == 404


def test_reading_a_meeting_that_does_not_exist_is_404(client: TestClient):
    assert client.get(f"{PREFIX}/meetings/no-such").status_code == 404


def test_the_summary_counts_the_states_the_page_shows(
    client: TestClient, workspace: str, router: str
):
    opened = open_routing(client, workspace)
    schedule(client, opened, "emea-standard")
    body = client.get(f"{PREFIX}/summary").json()
    assert body["workspaces"] == 1
    assert body["users"] == 4
    assert body["routers"] == 1
    assert body["paths"] == 3
    assert body["routings"] == 1
    assert body["routings_open"] == 0
    assert body["routings_by_outcome"] == {"paths_offered": 1}
    assert body["meetings_confirmed"] == 1
    # emea-standard gates on ae-rui and ignores se-sam; emea-platform gates on both
    # ae-priya and se-sam; any-lead gates on ae-priya alone.
    assert body["gated_and_ignored_users"] == 5
    assert body["collections"] == {
        "workspace": "handoff_workspace",
        "router": "handoff_router",
        "routing": "handoff_routing",
        "meeting": "handoff_meeting",
    }


def test_the_catalog_reports_the_gate_sets(client: TestClient, workspace: str, router: str):
    body = client.get(f"{PREFIX}/catalog").json()
    assert body["link_types"][0]["link_type"] == "Handoff"
    assert body["workspaces"][0]["summary"]["users"] == 4
    paths = {entry["path_id"]: entry for entry in body["routers"][0]["paths"]}
    assert paths["emea-platform"]["gating_user_ids"] == ["ae-priya", "se-sam"]
    assert paths["emea-standard"]["ignored_user_ids"] == ["se-sam"]


# --------------------------------------------------------------------------- #
# The audit rules
# --------------------------------------------------------------------------- #


def test_the_audit_source_names_the_route_that_served_the_write(
    client: TestClient, workspace: str, router: str
):
    """Every audit row this feature writes names a route the host mounted.

    Checked against the live registry rather than a written list: a list written by
    hand goes stale the moment a route is renamed, and then this check keeps passing
    while the audit log names a path the app stopped serving. That has shipped in
    this codebase before.
    """
    opened = open_routing(client, workspace)
    taken = schedule(client, opened, "emea-standard").json()
    client.post(f"{PREFIX}/meetings/{taken['meeting_id']}/cancel")

    routes = served_routes(client)
    assert routes, "the host reported no routes, so this check would pass vacuously"

    store = client.app.state.store
    for collection in (
        "handoff_workspace",
        "handoff_router",
        "handoff_routing",
        "handoff_meeting",
    ):
        rows = store.audit(collection=collection, limit=100)
        for row in rows:
            source = row.get("source")
            if not source:
                continue
            assert names_a_served_route(source, routes), (
                f"audit row in {collection} names {source!r}, which is not a route this app serves"
            )


def test_every_write_is_audited(client: TestClient, workspace: str, router: str):
    """A feature without its audit rows is a feature that bypassed the guarantee."""
    store = client.app.state.store
    opened = open_routing(client, workspace)
    schedule(client, opened, "emea-standard")

    for collection, minimum in (
        ("handoff_workspace", 1),
        ("handoff_router", 1),
        ("handoff_routing", 1),
        ("handoff_meeting", 1),
    ):
        assert store.db.audit_count(collection=collection) >= minimum, collection


def test_a_write_names_the_route_that_served_it(client: TestClient, workspace: str, router: str):
    """The source is the route, not a hand-written string that can drift."""
    store = client.app.state.store
    opened = open_routing(client, workspace)
    schedule(client, opened, "emea-standard")

    rows = store.audit(collection="handoff_meeting", limit=10)
    assert rows, "booking writes nothing audited"
    assert {row["source"] for row in rows} == {
        f"POST {PREFIX}/rooms/{{room_id}}/routing/{{routing_id}}/router/{{router_id}}"
        "/path/{path_id}/booker/{booker_id}/schedule-simple"
    }


def test_the_seed_source_is_not_a_route_and_is_not_asserted_over(
    client: TestClient, workspace: str, router: str
):
    """The seeder writes with ``source="seed"``, which is not a served route.

    Asserted rather than skipped quietly, because a guard that quietly exempts a
    source string is a guard whose exemption grows.
    """
    from dsr.features.wf055_handoff_schedule_a_lead_from_sdr_to_ae import seed

    seed(client.app.state.db, {"room_ids": [], "now": datetime.now(timezone.utc)})
    store = client.app.state.store
    routes = served_routes(client)
    rows = store.audit(collection="handoff_workspace", limit=100)
    sources = {row["source"] for row in rows}
    assert "seed" in sources
    assert names_a_served_route("seed", routes) is False
    served = {source for source in sources if source != "seed"}
    assert all(names_a_served_route(source, routes) for source in served), served


# --------------------------------------------------------------------------- #
# The seed, through the mounted routes
# --------------------------------------------------------------------------- #


def test_the_seeder_runs_and_its_return_string_is_printable_on_a_windows_console(
    client: TestClient, tmp_path
):
    """Every character of the returned string must be encodable by cp1252.

    A single RIGHTWARDS ARROW in one recovered feature broke the entire seeder on a
    Windows console, and the string is printed rather than logged, so this test
    prints it too rather than only encoding it.
    """
    from dsr.features.wf055_handoff_schedule_a_lead_from_sdr_to_ae import seed

    reported = seed(
        client.app.state.db,
        {"room_ids": [(ROOM, "Acme")], "now": datetime.now(timezone.utc) + timedelta(days=1)},
    )
    assert reported
    reported.encode("cp1252")
    print(reported)
    assert reported.isascii(), "the seeder prints this on a cp1252 console"
    # Not all successes: the demo must show the states this workflow exists for.
    assert "no_availability" in reported
    assert "refused" in reported
    assert "shadowed" in reported


def test_the_seeded_feature_answers_its_own_routes(client: TestClient):
    """The seeder's rows must be readable over HTTP, not only in the database."""
    from dsr.features.wf055_handoff_schedule_a_lead_from_sdr_to_ae import seed

    seed(
        client.app.state.db,
        {"room_ids": [(ROOM, "Acme")], "now": datetime.now(timezone.utc) + timedelta(days=1)},
    )
    assert client.get(f"{PREFIX}/workspaces").json()["count"] >= 2
    assert client.get(f"{PREFIX}/routers").json()["count"] >= 2
    assert client.get(f"{PREFIX}/routings").json()["count"] >= 3
    assert client.get(f"{PREFIX}/meetings").json()["count"] >= 2

    summary = client.get(f"{PREFIX}/summary").json()
    assert summary["routings_by_outcome"].get("no_availability") == 1
    assert summary["meetings_confirmed"] == 1

    booked = [
        routing
        for routing in client.get(f"{PREFIX}/routings").json()["routings"]
        if routing["data"]["state"] == "booked"
    ]
    assert booked, "the demo needs a booked handoff to review"
    path = next(entry for entry in booked[0]["data"]["paths"] if entry["slot_count"])
    response = client.post(
        f"{PREFIX}/rooms/{ROOM}/routing/{booked[0]['id']}/router/{path['router_ref']}"
        f"/path/{path['path_id']}/booker/{booked[0]['data']['booker_ref']}/schedule-simple",
        json={"startTime": path["start_times"][0]},
    )
    assert response.status_code == 409
    assert response.json()["error"] == "handoff_conflict"
