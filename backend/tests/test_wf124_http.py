"""WF-124 over HTTP: the routes the host actually mounted.

``test_wf124.py`` proves the rules. This file proves the wiring: that discovery
mounted the router, that each error type became the status code a client should
branch on, that the seller's and the buyer's routes are kept apart, and that every
audit row a write left behind names a concrete route the app serves.

That last one is the property the whole feature host exists to protect, so it is
asserted against the mounted route table rather than against a literal written in
the test. A literal would agree with a typo.

Each test file must pass on its own, because the suite runs under ``pytest-xdist``
and a test that only passes in one order fails intermittently. Nothing here
depends on another test's rows: every test builds its own room, template and plan.
"""

from __future__ import annotations

import pytest
from dsr.db.audited import RecordNotFound
from dsr.features import load_feature
from dsr.mutual_action import plan as mutual
from dsr.store import RecordStore
from fastapi.testclient import TestClient

MODULE = "wf124_native_mutual_action_plan_execution"
PREFIX = "/api/wf-124"
FEATURE_ID = "wf-124-native-mutual-action-plan-execution"


@pytest.fixture()
def feature():
    return load_feature(MODULE)


@pytest.fixture()
def live_store(db) -> RecordStore:
    """A store over the *same* database the ``client`` fixture serves.

    The shared ``store`` fixture is backed by ``memory_db``, while ``client`` swaps
    ``app.state.store`` onto the file-backed ``db``. Building fixtures from ``store``
    and then asserting through ``client`` therefore reads a database nothing wrote
    to. One database per test is the whole of the isolation this file needs.
    """
    return RecordStore(db)


@pytest.fixture()
def room_id(live_store: RecordStore) -> str:
    return live_store.create("room", {"name": "Northwind"}, actor="test", source="test")["id"]


def _task(title: str, owner: str = "Dana", **extra: object) -> dict:
    return {"title": title, "owner": owner, **extra}


def _tasks(*titles: str) -> list[dict]:
    return [_task(title) for title in titles]


@pytest.fixture()
def plan_id(client: TestClient, room_id: str) -> str:
    """A real plan with two tasks, made through the real route.

    Built over HTTP so every later test in this file starts from rows the routes
    themselves produced.
    """
    response = client.post(
        f"{PREFIX}/rooms/{room_id}/plans",
        json={"name": "Northwind MAP", "tasks": _tasks("First task", "Second task")},
    )
    assert response.status_code == 200, response.text
    return response.json()["id"]


# --------------------------------------------------------------------------- #
# Discovery and mounting
# --------------------------------------------------------------------------- #


def test_discovery_mounted_this_feature_by_its_prefix(client: TestClient, feature):
    """The router was mounted by discovery alone. No shared file was edited."""
    listed = client.get("/api/features").json()
    found = [entry for entry in listed["features"] if entry["id"] == FEATURE_ID]
    assert found, "the feature registry did not list this feature"
    assert found[0]["prefix"] == PREFIX
    assert found[0]["loaded"] is True

    # And a route really answers, which is what "mounted" has to mean.
    assert client.get(f"{PREFIX}/vocabulary").status_code == 200


def test_no_feature_failed_to_load_because_of_this_workflow(client: TestClient):
    """A broken feature is reported and skipped, so a failure here would be silent."""
    listed = client.get("/api/features").json()
    failures = [entry for entry in listed["failed"] if entry["module"].endswith(MODULE)]
    assert not failures, failures


def test_the_feature_maps_only_error_types_it_raises_itself(feature):
    """A handler for a builtin type would intercept that exception product-wide.

    Subclassing a builtin is fine and is what the other features do. Starlette
    matches a handler on the exception's own type first and only then walks its
    MRO, so a handler for ``PlanError`` catches ``PlanError`` and not every
    ``ValueError`` in the product. What must never happen is registering the
    builtin *itself*, which would catch it everywhere.
    """
    builtins_ = (ValueError, LookupError, PermissionError, KeyError, TypeError)
    for error_type in feature.EXCEPTION_HANDLERS:
        assert error_type.__module__ == "dsr.mutual_action.plan", error_type
        assert error_type not in builtins_, error_type
        # Distinct from the shared store's miss, so this feature's handler cannot
        # intercept another workflow's exception.
        assert error_type is not RecordNotFound, error_type

    # Three types, and each is mapped exactly once.
    assert len(feature.EXCEPTION_HANDLERS) == 3


def test_the_feature_reports_every_route_it_mounts(client: TestClient):
    """The registry is what ``tools/verify_all_routes.py`` walks.

    A route missing from it is a route nobody checks for a 5xx, so the set is
    asserted exactly: adding a route without listing it here, or listing one the
    feature does not serve, both fail.
    """
    paths = {route["path"] for route in client.get(f"/api/features/{FEATURE_ID}").json()["routes"]}
    assert paths == {
        f"{PREFIX}/summary",
        f"{PREFIX}/vocabulary",
        f"{PREFIX}/templates",
        f"{PREFIX}/templates/{{template_id}}",
        f"{PREFIX}/rooms/{{room_id}}/plans",
        f"{PREFIX}/rooms/{{room_id}}/plans",
        f"{PREFIX}/plans",
        f"{PREFIX}/plans/{{plan_id}}",
        f"{PREFIX}/plans/{{plan_id}}",
        f"{PREFIX}/plans/{{plan_id}}/close-won",
        f"{PREFIX}/plans/{{plan_id}}/shared",
        f"{PREFIX}/plans/{{plan_id}}/tasks",
        f"{PREFIX}/plans/{{plan_id}}/tasks",
        f"{PREFIX}/tasks/{{task_id}}",
        f"{PREFIX}/tasks/{{task_id}}",
        f"{PREFIX}/tasks/{{task_id}}",
        f"{PREFIX}/plans/{{plan_id}}/events",
        f"{PREFIX}/plans/{{plan_id}}/escalations",
    }


def test_the_module_never_imports_the_app_or_opens_sqlite(feature):
    """The enforced rule, restated so the reason is on the record for this workflow."""
    import ast
    from pathlib import Path

    source = Path(feature.__file__).read_text(encoding="utf-8")
    assert "import sqlite3" not in source
    assert "from dsr.api" not in source
    assert "import dsr.api" not in source
    ast.parse(source)  # the file parses, so the grep above read real code


# --------------------------------------------------------------------------- #
# The board
# --------------------------------------------------------------------------- #


def test_the_summary_route_reports_the_board_numbers(client: TestClient, room_id: str):
    client.post(
        f"{PREFIX}/rooms/{room_id}/plans",
        json={"name": "Counted", "tasks": [_task("A"), _task("B", visibility="internal")]},
    )
    body = client.get(f"{PREFIX}/summary", params={"room_id": room_id}).json()
    assert body["plans"] == 1
    assert body["tasks"] == 2
    assert body["internal_tasks"] == 1


def test_the_vocabulary_route_publishes_the_issue_s_open_decisions(client: TestClient):
    """A reviewer can see each derivation without reading the source."""
    body = client.get(f"{PREFIX}/vocabulary").json()
    assert body["decisions"]["visibility_scope"] == "The flag is per task, not per plan."
    assert body["reminder_lead_days"] == mutual.REMINDER_LEAD_DAYS
    assert body["statuses"] == list(mutual.STATUSES)
    assert any("e-signature" in note for note in body["not_implemented"])


# --------------------------------------------------------------------------- #
# Errors become the status codes a client branches on
# --------------------------------------------------------------------------- #


def test_an_invalid_payload_is_400_with_a_field_keyed_map(client: TestClient, room_id: str):
    """The map lets a form put each message beside the input that caused it."""
    response = client.post(
        f"{PREFIX}/rooms/{room_id}/plans", json={"name": "No tasks", "tasks": []}
    )
    assert response.status_code == 400, response.text
    body = response.json()
    assert body["error"] == "plan_invalid"
    assert "tasks" in body["errors"]


def test_an_unknown_plan_is_404(client: TestClient):
    response = client.get(f"{PREFIX}/plans/wf124_plan_does_not_exist")
    assert response.status_code == 404, response.text
    assert response.json()["error"] == "not_found"


def test_deleting_a_task_an_open_task_waits_on_is_409_not_400(client: TestClient, plan_id: str):
    """409, because nothing is malformed: the plan's shape refuses the change.

    A 400 would tell a client its request was wrong and invite a retry of the same
    call, which would fail the same way.
    """
    tasks = client.get(f"{PREFIX}/plans/{plan_id}/tasks").json()["tasks"]
    first, second = tasks[0]["id"], tasks[1]["id"]

    linked = client.patch(f"{PREFIX}/tasks/{second}", json={"depends_on": [first]})
    assert linked.status_code == 200, linked.text

    refused = client.delete(f"{PREFIX}/tasks/{first}")
    assert refused.status_code == 409, refused.text
    body = refused.json()
    assert body["error"] == "plan_conflict"
    assert body["reason"] == mutual.PlanBlocked.REASON_DEPENDENT


# --------------------------------------------------------------------------- #
# The buyer's view is a different route from the seller's
# --------------------------------------------------------------------------- #


def test_the_buyer_route_omits_an_internal_task_entirely(client: TestClient, room_id: str):
    """Absent from the response, not hidden by the UI.

    A task the UI merely greys out is still in the network response, so the buyer
    could read it. The test asserts on the response body, not on what the page drew.
    """
    created = client.post(
        f"{PREFIX}/rooms/{room_id}/plans",
        json={
            "name": "Shared roadmap",
            "tasks": _tasks("Shared task") + [_task("Internal task", visibility="internal")],
        },
    ).json()

    buyer = client.get(f"{PREFIX}/plans/{created['id']}/shared", params={"audience": "buyer"})
    assert buyer.status_code == 200, buyer.text
    titles = [task["title"] for task in buyer.json()["tasks"]]
    assert titles == ["Shared task"], titles
    assert "Internal task" not in buyer.text

    seller = client.get(f"{PREFIX}/plans/{created['id']}/shared", params={"audience": "seller"})
    assert seller.json()["task_count"] == 2


def test_a_buyer_reading_an_internal_task_by_id_gets_404(client: TestClient, room_id: str):
    """404 rather than 403, because a 403 would confirm something is there."""
    created = client.post(
        f"{PREFIX}/rooms/{room_id}/plans",
        json={"name": "Half internal", "tasks": [_task("Internal task", visibility="internal")]},
    ).json()
    task_id = created["tasks"][0]["id"]

    assert client.get(f"{PREFIX}/tasks/{task_id}", params={"audience": "buyer"}).status_code == 404
    assert client.get(f"{PREFIX}/tasks/{task_id}", params={"audience": "seller"}).status_code == 200


def test_the_buyer_task_list_route_also_hides_internal_work(client: TestClient, room_id: str):
    """The rule holds on every route that returns a task list, not just one."""
    created = client.post(
        f"{PREFIX}/rooms/{room_id}/plans",
        json={
            "name": "Two faces",
            "tasks": _tasks("Visible") + [_task("Hidden", visibility="internal")],
        },
    ).json()

    buyer = client.get(f"{PREFIX}/plans/{created['id']}/tasks", params={"audience": "buyer"})
    assert [task["title"] for task in buyer.json()["tasks"]] == ["Visible"]

    seller = client.get(f"{PREFIX}/plans/{created['id']}/tasks", params={"audience": "seller"})
    assert len(seller.json()["tasks"]) == 2


def test_the_seller_board_route_still_shows_every_task(client: TestClient, plan_id: str):
    """Hiding a task from the buyer must not hide it from the party doing the work."""
    body = client.get(f"{PREFIX}/plans/{plan_id}").json()
    assert body["task_count"] == 2
    assert "audience" not in body


# --------------------------------------------------------------------------- #
# The graph over HTTP
# --------------------------------------------------------------------------- #


def test_a_dependency_set_over_http_blocks_the_dependent(client: TestClient, plan_id: str):
    """The same rule the domain test proves, reached through a real route."""
    tasks = client.get(f"{PREFIX}/plans/{plan_id}/tasks").json()["tasks"]
    first, second = tasks[0]["id"], tasks[1]["id"]

    assert client.patch(f"{PREFIX}/tasks/{second}", json={"depends_on": [first]}).status_code == 200

    reloaded = {
        task["title"]: task for task in client.get(f"{PREFIX}/plans/{plan_id}").json()["tasks"]
    }
    assert reloaded["Second task"]["status"] == mutual.STATUS_BLOCKED

    assert client.patch(f"{PREFIX}/tasks/{first}", json={"status": "done"}).status_code == 200
    reloaded = {
        task["title"]: task for task in client.get(f"{PREFIX}/plans/{plan_id}").json()["tasks"]
    }
    assert reloaded["Second task"]["status"] == mutual.STATUS_TODO


def test_a_status_change_over_http_writes_an_event(client: TestClient, plan_id: str):
    """The spec names status events, and the route is where they are produced."""
    task_id = client.get(f"{PREFIX}/plans/{plan_id}/tasks").json()["tasks"][0]["id"]
    client.patch(f"{PREFIX}/tasks/{task_id}", json={"status": "in_progress"})

    events = client.get(f"{PREFIX}/plans/{plan_id}/events").json()["events"]
    assert len(events) == 1
    assert (events[0]["from"], events[0]["to"]) == ("todo", "in_progress")


def test_an_overdue_task_over_http_raises_an_escalation(client: TestClient, room_id: str):
    """Overdue items escalate, and the escalation is a row the board can list."""
    created = client.post(
        f"{PREFIX}/rooms/{room_id}/plans",
        json={
            "name": "Behind",
            "tasks": [{"title": "Late task", "owner": "Dana", "due_date": "2020-01-01"}],
        },
    ).json()

    rows = client.get(f"{PREFIX}/plans/{created['id']}/escalations").json()["escalations"]
    assert len(rows) == 1, rows
    assert rows[0]["state"] == mutual.ESCALATION_OVERDUE
    assert rows[0]["task_id"] == created["tasks"][0]["id"]


def test_tasks_are_returned_in_the_order_the_parties_agreed(client: TestClient, room_id: str):
    """The order is a fact about the plan, so two reads must not disagree."""
    titles = ["Alpha", "Bravo", "Charlie", "Delta", "Echo"]
    created = client.post(
        f"{PREFIX}/rooms/{room_id}/plans", json={"name": "Ordered", "tasks": _tasks(*titles)}
    ).json()

    for _ in range(3):
        rows = client.get(f"{PREFIX}/plans/{created['id']}/tasks").json()["tasks"]
        assert [row["title"] for row in rows] == titles


# --------------------------------------------------------------------------- #
# Templates
# --------------------------------------------------------------------------- #


def test_a_template_edge_survives_instantiation_over_http(client: TestClient, room_id: str):
    """Instantiation rewrites the template's edges onto this plan's own tasks."""
    template = client.post(
        f"{PREFIX}/templates",
        json={
            "name": "Standard close",
            "tasks": [
                {"title": "Security review", "owner": "Buyer IT", mutual.TEMPLATE_REF: "security"},
                {
                    "title": "Sign the order form",
                    "owner": "Dana",
                    "depends_on": ["security"],
                    mutual.TEMPLATE_REF: "orderform",
                },
            ],
        },
    ).json()

    plan = client.post(
        f"{PREFIX}/rooms/{room_id}/plans",
        json={"name": "From template", "template_id": template["id"]},
    ).json()

    rows = {task["title"]: task for task in plan["tasks"]}
    assert rows["Sign the order form"]["depends_on"] == [rows["Security review"]["id"]]
    assert rows["Sign the order form"]["status"] == mutual.STATUS_BLOCKED


def test_an_empty_template_is_refused(client: TestClient):
    """A template with no rows would instantiate an empty plan, which is not a plan."""
    response = client.post(f"{PREFIX}/templates", json={"name": "Empty", "tasks": []})
    assert response.status_code == 400, response.text
    assert "tasks" in response.json()["errors"]


def test_the_template_routes_answer(client: TestClient, room_id: str):
    created = client.post(
        f"{PREFIX}/templates", json={"name": "Listed", "tasks": _tasks("Only task")}
    ).json()

    assert client.get(f"{PREFIX}/templates").json()["templates"]
    read = client.get(f"{PREFIX}/templates/{created['id']}")
    assert read.status_code == 200
    assert read.json()["task_count"] == 1


# --------------------------------------------------------------------------- #
# Closed-won
# --------------------------------------------------------------------------- #


def test_closing_the_plan_over_http_carries_the_tasks_into_an_implementation_plan(
    client: TestClient, room_id: str
):
    """The route the automation runs on, and the property that matters.

    The agreed tasks are carried into the implementation plan, and the selling plan
    is still there afterwards, because the plan has to survive closed-won.
    """
    created = client.post(
        f"{PREFIX}/rooms/{room_id}/plans",
        json={"name": "Closing", "tasks": _tasks("Agreed one", "Agreed two")},
    ).json()

    response = client.post(f"{PREFIX}/plans/{created['id']}/close-won")
    assert response.status_code == 200, response.text
    outcome = response.json()
    assert outcome["phase"] == mutual.PHASE_CLOSED_WON
    assert outcome["carried_tasks"] == 2

    implementation = client.get(f"{PREFIX}/plans/{outcome['implementation_plan_id']}").json()
    assert implementation["phase"] == mutual.PHASE_IMPLEMENTATION
    assert implementation["task_count"] == 2

    assert client.get(f"{PREFIX}/plans/{created['id']}").json()["task_count"] == 2


# --------------------------------------------------------------------------- #
# Every write's audit row names a route the app actually serves
# --------------------------------------------------------------------------- #


def test_every_audit_row_this_feature_wrote_names_a_route_the_app_serves(
    client: TestClient, live_store: RecordStore, room_id: str
):
    """The property the feature host exists to protect.

    Asserted against the mounted route table rather than a literal, because a
    literal in the test would agree with a typo in the module. Served paths come from the feature registry rather than from ``app.routes``.
    The host mounts a discovered feature's router on a sub-application, so a
    feature's routes answer over HTTP without appearing in the top-level table.
    The registry is also what ``tools/verify_all_routes.py`` walks, so a route
    absent from it is a route nobody checks for a 5xx.
    """
    served = {
        (method, shape["path"])
        for shape in client.get(f"/api/features/{FEATURE_ID}").json()["routes"]
        for method in shape["methods"]
    }
    assert served, "the registry reports no route for this feature"

    # Drive one write of every kind this feature makes.
    client.post(f"{PREFIX}/templates", json={"name": "Audited", "tasks": _tasks("Task one")})
    created = client.post(
        f"{PREFIX}/rooms/{room_id}/plans",
        json={"name": "Audited", "tasks": _tasks("Task one", "Task two")},
    ).json()
    task_id = created["tasks"][0]["id"]
    other_id = created["tasks"][1]["id"]
    client.patch(f"{PREFIX}/tasks/{other_id}", json={"depends_on": [task_id]})
    client.patch(f"{PREFIX}/tasks/{task_id}", json={"status": "done"})
    client.patch(f"{PREFIX}/plans/{created['id']}", json={"name": "Renamed"})
    client.post(f"{PREFIX}/plans/{created['id']}/close-won")
    client.delete(f"{PREFIX}/tasks/{other_id}")

    rows = [
        row for row in live_store.db.audit() if str(row.get("collection") or "").startswith("wf124")
    ]
    assert rows, "no audit rows were written for this feature's collections"

    checked = 0
    for row in rows:
        source = str(row.get("source") or "")
        if not source.startswith(("POST ", "PATCH ", "DELETE ")):
            # A write made by the seeder, or by a test fixture. Not a route, and
            # correctly so: nothing served it.
            continue
        method, _, path = source.partition(" ")
        assert (method, path) in served, f"audit row names a route the app does not serve: {source}"
        checked += 1

    assert checked >= 8, f"only {checked} route-sourced audit rows were checked"


def test_no_write_this_feature_makes_is_reachable_only_through_a_test_hook(
    client: TestClient, feature
):
    """The domain takes ``source`` from the route, never from a literal it invented."""
    import inspect

    signature = inspect.signature(mutual.PlanEngine.create_plan)
    assert "source" in signature.parameters
    # Every engine write requires a source rather than defaulting one, so a caller
    # cannot reach a write without naming the route that served it.
    for name in (
        "create_template",
        "create_plan",
        "update_plan",
        "add_task",
        "update_task",
        "delete_task",
        "close_as_won",
    ):
        assert "source" in inspect.signature(getattr(mutual.PlanEngine, name)).parameters, name
        assert (
            inspect.signature(getattr(mutual.PlanEngine, name)).parameters["source"].default
            is inspect.Parameter.empty
        ), name
    assert feature.router.prefix == PREFIX
