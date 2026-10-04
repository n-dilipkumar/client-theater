"""WF-052 HTTP tests: the mounted router, its refusals, and its audit rows.

What is under test, and why
---------------------------

The domain rules live in ``tests/test_wf052.py``. What is here is the half the
domain cannot see:

* the router is **mounted by discovery alone**, with no edit to ``dsr/api.py``
* every documented refusal reaches the client with the status and code the error
  carries, through **one** registered handler
* ``POST /qualify`` **writes nothing over HTTP either**, so the researched
  guarantee survives the layer above it
* every write's audit row **names a route this router actually serves**, checked
  against the live route table rather than against a list in this file
* the routes answer **no 5xx when called the way ``tools/verify_all_routes.py``
  calls them**, which is the tool CI runs

Isolation: the ``client`` fixture from ``conftest.py`` enters one ``TestClient``
per module and points ``app.state`` at a fresh, empty database per test, so this
file passes on its own and under ``pytest-xdist``.
"""

from __future__ import annotations

from typing import Any

import pytest
from dsr.features import wf052_qualify_a_lead_without_offering_any_calen as feature
from fastapi.testclient import TestClient

PREFIX = "/api/wf-052"
ROOM = "room-1"
OTHER_ROOM = "room-2"

NADIA = "005-nadia"
DESK = "005-desk"
PRIYA = "005-priya"

ENTERPRISE_RULE: dict[str, Any] = {
    "kind": "data_field",
    "name": "Enterprise seat count",
    "conditions": [
        {"kind": "data_field", "source": "form", "field": "seats", "operator": "gte", "value": 200}
    ],
    "assign_user_id": NADIA,
}

HOT_RULE: dict[str, Any] = {
    "kind": "crm_field",
    "name": "Hot contact is handled without a scheduler",
    "conditions": [
        {
            "kind": "crm_field",
            "source": "lead",
            "field": "rating",
            "operator": "equals",
            "value": "hot",
        }
    ],
    "assign_user_id": PRIYA,
    "scheduling_allowed": False,
    "crm_writeback": {"rating": "hot"},
}

CATCH_ALL_RULE: dict[str, Any] = {
    "kind": "catch_all",
    "name": "Deal desk takes the rest",
    "assign_user_id": DESK,
}

CHAIN = [ENTERPRISE_RULE, HOT_RULE, CATCH_ALL_RULE]


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
# Fixtures and helpers
# --------------------------------------------------------------------------- #


@pytest.fixture
def room(client: TestClient) -> str:
    """One real room, so the room-scoped write path is exercised against real data."""
    return client.post("/api/records/room", json={"name": "Northwind"}).json()["id"]


@pytest.fixture
def workspace(client: TestClient) -> TestClient:
    """Two assignees and one valid router: the smallest useful world over HTTP."""
    for user_id, name in ((NADIA, "Nadia A. Farouk"), (DESK, "SoluSpring Deal Desk")):
        assert (
            client.post(f"{PREFIX}/assignees", json={"user_id": user_id, "name": name}).status_code
            == 201
        )
    response = client.post(
        f"{PREFIX}/routers",
        json={
            "router_slug": "demo-request",
            "name": "Demo requests",
            "tenant": "soluspring.chilipiper.example",
            "rules": CHAIN,
        },
    )
    assert response.status_code == 201, response.text
    return client


def qualify(
    client: TestClient, form: dict[str, Any], crm: dict[str, Any] | None = None, **params: Any
):
    """POST the researched call with a form payload and optional CRM values."""
    body: dict[str, Any] = {"form": form}
    if crm is not None:
        body["crm"] = crm
    query = "&".join(f"{key}={value}" for key, value in params.items())
    return client.post(f"{PREFIX}/qualify?{query}" if query else f"{PREFIX}/qualify", json=body)


# --------------------------------------------------------------------------- #
# Discovery: the router is mounted because the host found the file
# --------------------------------------------------------------------------- #


def test_the_router_is_mounted_by_discovery_alone(client: TestClient):
    assert client.get(f"{PREFIX}/vocabulary").status_code == 200


def test_the_registry_reports_this_feature_with_its_prefix_and_routes(client: TestClient):
    body = client.get("/api/features").json()
    entry = next(row for row in body["features"] if row["id"] == feature.FEATURE["id"])
    assert entry["prefix"] == PREFIX
    assert entry["ticket"] == "WF-052"
    assert entry["routes"]


def test_the_prefix_is_the_one_the_ticket_names():
    assert feature.router.prefix == "/api/wf-052"
    assert feature.FEATURE["ticket"] == "WF-052"


def test_no_core_route_is_shadowed_by_this_feature(client: TestClient):
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/records/room?limit=1").status_code == 200
    assert client.get("/api/features").json()["failed_count"] == 0


# --------------------------------------------------------------------------- #
# Vocabulary, inferences, summary
# --------------------------------------------------------------------------- #


def test_the_vocabulary_route_serves_the_researched_terms(client: TestClient):
    body = client.get(f"{PREFIX}/vocabulary").json()
    assert body["verdicts"] == ["qualified", "not_scheduled", "unroutable"]
    assert body["rule_kinds"] == ["data_field", "crm_field", "catch_all"]
    assert body["assignment_types"] == ["user", "unassigned"]
    assert body["interval_field"] == "interval"
    assert body["edge_call"].endswith("/rest")
    assert body["access_patterns"]["return_a_booking_url"]["consumes_a_session"] is False


def test_the_vocabulary_route_publishes_the_guarantees(client: TestClient):
    guarantees = client.get(f"{PREFIX}/vocabulary").json()["guarantees"]
    assert guarantees["consumes_no_session"] is True
    assert guarantees["queries_no_availability"] is True
    assert guarantees["computes_no_slots"] is True


def test_the_inferences_route_names_every_derivation_and_the_sentence_behind_it(client: TestClient):
    body = client.get(f"{PREFIX}/inferences").json()
    decisions = {entry["decision"] for entry in body["inferred"]}
    assert "derived_route_id" in decisions
    assert "interval_refused" in decisions
    assert body["sourced"]["no_session_consumed"] is True
    for entry in body["inferred"]:
        assert entry["researched"], entry["decision"]


def test_the_summary_route_reports_the_three_collections_and_the_counters(client: TestClient):
    body = client.get(f"{PREFIX}/summary").json()
    assert body["collections"] == [
        "lead_qualification_router",
        "lead_qualification_assignee",
        "lead_qualification_verdict",
    ]
    assert body["side_effects"]["routing_sessions_consumed"] == 0
    assert body["verdicts"] == 0


def test_the_summary_route_is_room_scoped_when_a_room_is_named(client: TestClient):
    body = client.get(f"{PREFIX}/summary?room_id={ROOM}").json()
    assert body["room_id"] == ROOM
    assert body["verdicts"] == 0


# --------------------------------------------------------------------------- #
# Routers
# --------------------------------------------------------------------------- #


def test_declaring_a_router_answers_201_with_its_stored_chain(client: TestClient):
    response = client.post(
        f"{PREFIX}/routers",
        json={"router_slug": "solo", "name": "Solo", "rules": [CATCH_ALL_RULE]},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["data"]["router_slug"] == "solo"
    assert body["data"]["enabled"] is True
    assert len(body["data"]["rules"]) == 1


def test_a_chain_with_no_catch_all_is_refused_with_422_and_the_researched_sentence(
    client: TestClient,
):
    response = client.post(
        f"{PREFIX}/routers",
        json={"router_slug": "solo", "name": "Solo", "rules": [ENTERPRISE_RULE]},
    )
    assert response.status_code == 422
    body = response.json()
    assert body["error"] == "router_refused"
    assert "Catch All" in body["detail"]


def test_a_second_router_at_the_same_slug_is_409(workspace: TestClient):
    response = workspace.post(
        f"{PREFIX}/routers",
        json={"router_slug": "demo-request", "name": "Again", "rules": [CATCH_ALL_RULE]},
    )
    assert response.status_code == 409
    assert response.json()["error"] == "router_already_exists"


def test_routers_can_be_listed_and_filtered_on_enabled(workspace: TestClient):
    workspace.post(
        f"{PREFIX}/routers",
        json={"router_slug": "draft", "name": "Draft", "rules": [CATCH_ALL_RULE]},
    )
    workspace.patch(f"{PREFIX}/routers/draft", json={"enabled": False})
    assert workspace.get(f"{PREFIX}/routers").json()["count"] == 2
    assert workspace.get(f"{PREFIX}/routers?enabled=false").json()["count"] == 1
    assert workspace.get(f"{PREFIX}/routers?enabled=true").json()["count"] == 1


def test_one_router_can_be_read_back_and_the_response_names_the_collection(workspace: TestClient):
    body = workspace.get(f"{PREFIX}/routers/demo-request").json()
    assert body["data"]["name"] == "Demo requests"
    assert len(body["data"]["rules"]) == 3
    assert workspace.get(f"{PREFIX}/routers").json()["collection"] == "lead_qualification_router"


def test_an_unknown_router_is_a_404_with_the_feature_code(client: TestClient):
    response = client.get(f"{PREFIX}/routers/absent")
    assert response.status_code == 404
    assert response.json()["error"] == "router_not_found"


def test_a_router_is_patchable_and_the_patch_cannot_move_the_slug(workspace: TestClient):
    response = workspace.patch(
        f"{PREFIX}/routers/demo-request", json={"notes": "Reviewed", "router_slug": "stolen"}
    )
    assert response.status_code == 200
    assert response.json()["data"]["notes"] == "Reviewed"
    assert response.json()["data"]["router_slug"] == "demo-request"


def test_a_patch_that_breaks_the_chain_is_refused_and_the_router_is_untouched(
    workspace: TestClient,
):
    response = workspace.patch(f"{PREFIX}/routers/demo-request", json={"rules": [ENTERPRISE_RULE]})
    assert response.status_code == 422
    assert len(workspace.get(f"{PREFIX}/routers/demo-request").json()["data"]["rules"]) == 3


def test_patching_an_unknown_router_is_a_404(client: TestClient):
    assert client.patch(f"{PREFIX}/routers/absent", json={"notes": "x"}).status_code == 404


def test_deleting_a_router_is_204_and_the_record_is_gone_afterwards(workspace: TestClient):
    assert workspace.delete(f"{PREFIX}/routers/demo-request").status_code == 204
    assert workspace.get(f"{PREFIX}/routers/demo-request").status_code == 404


def test_deleting_an_unknown_router_is_a_404(client: TestClient):
    assert client.delete(f"{PREFIX}/routers/absent").status_code == 404


def test_the_preview_route_reports_a_bad_draft_and_saves_nothing(client: TestClient):
    response = client.post(
        f"{PREFIX}/routers/preview",
        json={"router_slug": "draft", "name": "Draft", "rules": [ENTERPRISE_RULE]},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is False
    assert body["saved"] is False
    assert any("Catch All" in problem for problem in body["problems"])
    assert client.get(f"{PREFIX}/routers").json()["count"] == 0


def test_the_preview_route_reports_which_rule_a_sample_lead_would_hit(workspace: TestClient):
    response = workspace.post(
        f"{PREFIX}/routers/preview",
        json={
            "router_slug": "draft",
            "name": "Draft",
            "rules": CHAIN,
            "sample": {"form": {"email": "a@x.example", "seats": 400}},
        },
    )
    body = response.json()
    assert body["valid"] is True
    assert body["rules"] == 3
    assert body["sample_verdict"] == "qualified"
    assert body["sample_rule"] == ENTERPRISE_RULE["name"]


def test_the_preview_route_says_nothing_about_a_sample_it_was_not_given(client: TestClient):
    body = client.post(
        f"{PREFIX}/routers/preview",
        json={"router_slug": "draft", "name": "Draft", "rules": [CATCH_ALL_RULE]},
    ).json()
    assert body["sample_verdict"] is None
    assert body["sample_conditions"] == []


def test_the_preview_route_is_not_shadowed_by_the_slug_template(client: TestClient):
    """`/routers/preview` must reach the preview route, not read a router called preview."""
    response = client.post(f"{PREFIX}/routers/preview", json={})
    assert response.status_code == 200
    assert "problems" in response.json()
    assert client.get(f"{PREFIX}/routers/preview").status_code == 404


# --------------------------------------------------------------------------- #
# Assignees
# --------------------------------------------------------------------------- #


def test_declaring_an_assignee_answers_201_and_the_list_names_the_collection(client: TestClient):
    response = client.post(f"{PREFIX}/assignees", json={"user_id": NADIA, "name": "Nadia"})
    assert response.status_code == 201
    assert response.json()["data"]["user_id"] == NADIA
    listed = client.get(f"{PREFIX}/assignees").json()
    assert listed["count"] == 1
    assert listed["collection"] == "lead_qualification_assignee"


def test_an_assignee_without_a_user_id_is_422(client: TestClient):
    response = client.post(f"{PREFIX}/assignees", json={"name": "Nobody"})
    assert response.status_code == 422
    assert response.json()["error"] == "assignee_refused"


def test_an_assignee_without_a_name_is_422(client: TestClient):
    assert client.post(f"{PREFIX}/assignees", json={"user_id": NADIA}).status_code == 422


def test_the_same_assignee_twice_is_refused(workspace: TestClient):
    response = workspace.post(f"{PREFIX}/assignees", json={"user_id": NADIA, "name": "Again"})
    assert response.status_code == 422
    assert "already declared" in response.json()["detail"]


def test_deleting_an_assignee_is_204_and_its_leads_then_answer_unroutable(workspace: TestClient):
    assert workspace.delete(f"{PREFIX}/assignees/{DESK}").status_code == 204
    body = qualify(
        workspace, {"email": "a@x.example", "seats": 5}, router_slug="demo-request"
    ).json()
    assert body["verdict"] == "unroutable"
    assert DESK in body["reason"]


def test_deleting_an_unknown_assignee_is_a_404(client: TestClient):
    assert client.delete(f"{PREFIX}/assignees/absent").status_code == 404


# --------------------------------------------------------------------------- #
# The researched call
# --------------------------------------------------------------------------- #


def test_a_qualified_lead_answers_with_the_researched_fields(workspace: TestClient):
    response = qualify(
        workspace, {"email": "a@northwind.example", "seats": 400}, router_slug="demo-request"
    )
    assert response.status_code == 200
    body = response.json()
    assert body["verdict"] == "qualified"
    assert body["scheduling_allowed"] is True
    assert body["schedulingAllowed"] is True
    assert body["assignment"] == {"userId": NADIA, "type": "user"}
    assert body["route_id"] == body["routeId"]
    assert body["routing_link"] == body["routingLink"]
    assert body["routing_link"].endswith(
        f"/concierge-router/demo-request/routing/{body['routeId']}"
    )


def test_the_answer_says_in_its_own_counters_that_nothing_happened(workspace: TestClient):
    body = qualify(
        workspace, {"email": "a@x.example", "seats": 400}, router_slug="demo-request"
    ).json()
    assert body["side_effects"] == {
        "records_written": 0,
        "routing_sessions_consumed": 0,
        "availability_queries": 0,
        "calendar_reads": 0,
        "slots_computed": 0,
        "automations_fired": 0,
    }
    assert body["interval_supplied"] is False


def test_the_qualify_route_writes_no_record_and_no_audit_row(workspace: TestClient):
    """The researched guarantee, counted over HTTP rather than asserted."""
    before_records = workspace.get("/api/stats").json()["records"]
    before_audit = workspace.get("/api/audit?limit=1000").json()["count"]

    for _ in range(3):
        assert (
            qualify(
                workspace, {"email": "a@x.example", "seats": 400}, router_slug="demo-request"
            ).status_code
            == 200
        )

    assert workspace.get("/api/stats").json()["records"] == before_records
    assert workspace.get("/api/audit?limit=1000").json()["count"] == before_audit


def test_the_same_lead_answers_with_the_same_derived_route_id(workspace: TestClient):
    first = qualify(
        workspace, {"email": "a@x.example", "seats": 400}, router_slug="demo-request"
    ).json()
    second = qualify(
        workspace, {"email": "a@x.example", "seats": 400}, router_slug="demo-request"
    ).json()
    assert first["route_id"] == second["route_id"]


def test_a_lead_falls_through_to_the_catch_all(workspace: TestClient):
    body = qualify(
        workspace, {"email": "a@x.example", "seats": 5}, router_slug="demo-request"
    ).json()
    assert body["verdict"] == "qualified"
    assert body["assignment"]["userId"] == DESK
    assert body["matched_rule"]["name"] == CATCH_ALL_RULE["name"]


def test_a_crm_value_is_read_from_the_body_the_caller_supplies(workspace: TestClient):
    workspace.post(f"{PREFIX}/assignees", json={"user_id": PRIYA, "name": "Priya"})
    body = qualify(
        workspace,
        {"email": "a@x.example", "seats": 5},
        {"lead": {"rating": "hot"}},
        router_slug="demo-request",
    ).json()
    assert body["verdict"] == "not_scheduled"
    assert body["assignment"] == {"userId": PRIYA, "type": "user"}
    assert body["crm_writeback"]["applied"] is False


def test_a_lead_that_reaches_a_catch_all_naming_an_absent_rep_is_unroutable(client: TestClient):
    client.post(f"{PREFIX}/assignees", json={"user_id": NADIA, "name": "Nadia"})
    client.post(
        f"{PREFIX}/routers",
        json={
            "router_slug": "broken",
            "name": "Broken",
            "rules": [{**CATCH_ALL_RULE, "assign_user_id": "005-ghost"}],
        },
    )
    body = qualify(client, {"email": "a@x.example"}, router_slug="broken").json()
    assert body["verdict"] == "unroutable"
    assert body["scheduling_allowed"] is False
    assert body["assignment"] == {"userId": "", "type": "unassigned"}


def test_a_body_carrying_an_interval_is_refused_with_the_researched_sentence(workspace: TestClient):
    response = workspace.post(
        f"{PREFIX}/qualify?router_slug=demo-request",
        json={"form": {"email": "a@x.example"}, "interval": {"start": "2026-10-05T09:00:00Z"}},
    )
    assert response.status_code == 400
    body = response.json()
    assert body["error"] == "interval_supplied"
    assert "difference is whether you pass an interval" in body["detail"]


def test_a_body_with_no_form_is_refused(workspace: TestClient):
    response = workspace.post(
        f"{PREFIX}/qualify?router_slug=demo-request", json={"crm": {"lead": {"rating": "hot"}}}
    )
    assert response.status_code == 400
    assert response.json()["error"] == "form_payload_required"


def test_qualifying_through_an_unknown_router_is_a_404(client: TestClient):
    response = qualify(client, {"email": "a@x.example"}, router_slug="absent")
    assert response.status_code == 404
    assert response.json()["error"] == "router_not_found"


def test_qualifying_without_naming_a_router_is_a_400(client: TestClient):
    response = client.post(f"{PREFIX}/qualify", json={"form": {"email": "a@x.example"}})
    assert response.status_code == 400
    assert "router_slug is required" in response.json()["detail"]


def test_the_router_slug_may_come_from_the_body_instead_of_the_query(workspace: TestClient):
    response = workspace.post(
        f"{PREFIX}/qualify", json={"router_slug": "demo-request", "form": {"email": "a@x.example"}}
    )
    assert response.status_code == 200
    assert response.json()["router_slug"] == "demo-request"


def test_an_unpublished_router_refuses_rather_than_qualifying(workspace: TestClient):
    workspace.patch(f"{PREFIX}/routers/demo-request", json={"enabled": False})
    response = qualify(workspace, {"email": "a@x.example"}, router_slug="demo-request")
    assert response.status_code == 409
    assert response.json()["error"] == "router_disabled"


# --------------------------------------------------------------------------- #
# The recorded path: caller path (b)
# --------------------------------------------------------------------------- #


def test_recording_a_verdict_answers_201_with_the_row_and_the_answer(
    workspace: TestClient, room: str
):
    response = workspace.post(
        f"{PREFIX}/rooms/{room}/verdicts?router_slug=demo-request",
        json={"form": {"email": "a@x.example", "seats": 400}},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["collection"] == "lead_qualification_verdict"
    assert body["room_id"] == room
    assert body["data"]["verdict"] == "qualified"
    assert body["qualification"]["route_id"] == body["data"]["route_id"]
    assert body["data"]["side_effects"]["records_written"] == 1


def test_a_recorded_verdict_stages_the_crm_writeback_and_applies_nothing(
    workspace: TestClient, room: str
):
    workspace.post(f"{PREFIX}/assignees", json={"user_id": PRIYA, "name": "Priya"})
    body = workspace.post(
        f"{PREFIX}/rooms/{room}/verdicts?router_slug=demo-request",
        json={"form": {"email": "a@x.example", "seats": 5}, "crm": {"lead": {"rating": "hot"}}},
    ).json()
    assert body["data"]["crm_writeback"]["applied"] is False
    assert body["data"]["crm_writeback"]["fields"]["qualification_verdict"] == "not_scheduled"


def test_recording_into_a_room_that_does_not_exist_is_a_404(workspace: TestClient):
    response = workspace.post(
        f"{PREFIX}/rooms/room-absent/verdicts?router_slug=demo-request",
        json={"form": {"email": "a@x.example"}},
    )
    assert response.status_code == 404
    assert response.json()["error"] == "room_not_found"


def test_recording_without_naming_a_router_is_a_400(workspace: TestClient, room: str):
    response = workspace.post(
        f"{PREFIX}/rooms/{room}/verdicts", json={"form": {"email": "a@x.example"}}
    )
    assert response.status_code == 400
    assert "router_slug is required" in response.json()["detail"]


def test_a_refused_recording_writes_no_row(workspace: TestClient, room: str):
    before = workspace.get(f"{PREFIX}/verdicts").json()["count"]
    response = workspace.post(
        f"{PREFIX}/rooms/{room}/verdicts?router_slug=demo-request",
        json={"form": {}, "interval": {"start": "x"}},
    )
    assert response.status_code == 400
    assert workspace.get(f"{PREFIX}/verdicts").json()["count"] == before


def test_recorded_verdicts_can_be_listed_filtered_and_read(workspace: TestClient, room: str):
    other = workspace.post("/api/records/room", json={"name": "Contoso"}).json()["id"]
    workspace.post(f"{PREFIX}/assignees", json={"user_id": PRIYA, "name": "Priya"})
    workspace.post(
        f"{PREFIX}/rooms/{room}/verdicts?router_slug=demo-request",
        json={"form": {"email": "a@x.example", "seats": 400}},
    )
    workspace.post(
        f"{PREFIX}/rooms/{other}/verdicts?router_slug=demo-request",
        json={"form": {"email": "b@x.example", "seats": 5}, "crm": {"lead": {"rating": "hot"}}},
    )

    assert workspace.get(f"{PREFIX}/verdicts").json()["count"] == 2
    assert workspace.get(f"{PREFIX}/verdicts?room_id={room}").json()["count"] == 1
    assert workspace.get(f"{PREFIX}/verdicts?verdict=not_scheduled").json()["count"] == 1
    assert workspace.get(f"{PREFIX}/verdicts?scheduling_allowed=false").json()["count"] == 1
    assert workspace.get(f"{PREFIX}/verdicts?scheduling_allowed=true").json()["count"] == 1
    assert workspace.get(f"{PREFIX}/verdicts?router_slug=demo-request").json()["count"] == 2
    assert workspace.get(f"{PREFIX}/verdicts?router_slug=absent").json()["count"] == 0
    assert workspace.get(f"{PREFIX}/verdicts").json()["collection"] == "lead_qualification_verdict"

    listed = workspace.get(f"{PREFIX}/verdicts").json()["verdicts"]
    read = workspace.get(f"{PREFIX}/verdicts/{listed[0]['id']}")
    assert read.status_code == 200
    assert read.json()["id"] == listed[0]["id"]


def test_the_summary_route_counts_the_recorded_verdicts(workspace: TestClient, room: str):
    workspace.post(
        f"{PREFIX}/rooms/{room}/verdicts?router_slug=demo-request",
        json={"form": {"email": "a@x.example", "seats": 400}},
    )
    body = workspace.get(f"{PREFIX}/summary").json()
    assert body["routers"] == 1
    assert body["assignees"] == 2
    assert body["verdicts"] == 1
    assert body["by_verdict"] == {"qualified": 1}
    assert workspace.get(f"{PREFIX}/summary?room_id=room-absent").json()["verdicts"] == 0


def test_an_unknown_verdict_is_a_404_with_the_feature_code(client: TestClient):
    response = client.get(f"{PREFIX}/verdicts/absent")
    assert response.status_code == 404
    assert response.json()["error"] == "verdict_not_found"


# --------------------------------------------------------------------------- #
# The hard rules
# --------------------------------------------------------------------------- #


def test_every_write_audit_row_names_a_route_the_app_serves(workspace: TestClient, room: str):
    """The contract's central guarantee, checked against the live route table."""
    workspace.patch(f"{PREFIX}/routers/demo-request", json={"notes": "Reviewed"})
    workspace.post(
        f"{PREFIX}/rooms/{room}/verdicts?router_slug=demo-request",
        json={"form": {"email": "a@x.example", "seats": 400}},
    )
    workspace.delete(f"{PREFIX}/assignees/{DESK}")

    entries = workspace.get("/api/audit?limit=500").json()["entries"]
    mine = [row for row in entries if PREFIX in str(row.get("source") or "")]
    assert mine, "no audit row from this feature was written"

    routes = served_routes(workspace)
    for row in mine:
        source = str(row["source"])
        assert names_a_served_route(source, routes), (
            f"audit row names {source!r}, which is not a route this app serves"
        )


def test_the_sources_written_are_exactly_the_routes_that_served_them(
    workspace: TestClient, room: str
):
    workspace.post(
        f"{PREFIX}/rooms/{room}/verdicts?router_slug=demo-request",
        json={"form": {"email": "a@x.example", "seats": 400}},
    )
    workspace.delete(f"{PREFIX}/routers/demo-request")
    entries = workspace.get("/api/audit?limit=500").json()["entries"]
    sources = {
        row["source"]
        for row in entries
        if PREFIX in str(row.get("source") or "") and row.get("action") != "read"
    }
    assert sources == {
        f"POST {PREFIX}/assignees",
        f"POST {PREFIX}/routers",
        f"POST {PREFIX}/rooms/{{room_id}}/verdicts",
        f"DELETE {PREFIX}/routers/{{router_slug}}",
    }


def test_one_handler_covers_the_whole_error_hierarchy():
    from dsr.lead_qualification import QualificationError

    assert list(feature.EXCEPTION_HANDLERS) == [QualificationError]


def test_the_feature_module_reaches_no_shared_file_and_opens_no_connection():
    from pathlib import Path

    text = Path(feature.__file__).read_text(encoding="utf-8")
    assert "from dsr.api" not in text
    assert "import dsr.api" not in text
    assert "import sqlite3" not in text
    assert "sqlite3.connect" not in text
    assert "from dsr.deps import" in text, "dependencies come from dsr.deps"


def test_the_seeder_returns_a_string_the_console_can_print(db):
    from datetime import datetime, timezone

    store = __import__("dsr.store", fromlist=["RecordStore"]).RecordStore(db)
    room_id = store.create("room", {"name": "R"}, actor="dana", source="test")["id"]
    summary = feature.seed(
        db, {"room_ids": [(room_id, "Room")], "now": datetime.now(timezone.utc), "rng": None}
    )
    assert summary.encode("cp1252")


# --------------------------------------------------------------------------- #
# The routes, called the way tools/verify_all_routes.py calls them
# --------------------------------------------------------------------------- #


def test_every_route_answers_without_a_5xx_when_called_with_an_empty_body(
    client: TestClient, room: str
):
    """The tool CI runs substitutes ids and sends ``{}`` to every method it finds.

    A write route that 500s on an empty body fails the build, so this walks the
    same route table the tool walks and asserts the same rule.
    """
    faults: list[tuple[str, str, int]] = []
    for route in served_routes(client):
        if not route["path"].startswith(PREFIX):
            continue
        path = route["path"].replace("{room_id}", room)
        path = path.replace("{router_slug}", "absent").replace("{user_id}", "absent")
        path = path.replace("{verdict_id}", "absent")
        for method in route["methods"]:
            if method not in {"GET", "POST", "PATCH", "DELETE"}:
                continue
            body = {} if method in {"POST", "PATCH", "PUT"} else None
            response = client.request(method, path, json=body)
            if response.status_code == 0 or response.status_code >= 500:
                faults.append((method, path, response.status_code))
    assert faults == [], f"routes answered 5xx when called with an empty body: {faults}"


def test_every_get_route_answers_without_a_5xx_on_an_empty_workspace(client: TestClient):
    faults: list[tuple[str, int]] = []
    for route in served_routes(client):
        if not route["path"].startswith(PREFIX) or "GET" not in route["methods"]:
            continue
        path = route["path"].replace("{room_id}", "room-absent")
        path = path.replace("{router_slug}", "absent").replace("{user_id}", "absent")
        path = path.replace("{verdict_id}", "absent")
        response = client.get(path)
        if response.status_code >= 500:
            faults.append((path, response.status_code))
    assert faults == [], faults
