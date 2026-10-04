"""WF-124: the Mutual Action Plan rules, exercised as a domain module with no server.

The rules in ``dsr.mutual_action.plan`` are pure: they touch the
:class:`~dsr.store.RecordStore` and nothing else. So every rule below is
reachable without a ``TestClient``, and these tests are what make each of the
issue's open decisions checkable at the instant it is stated about.

What is under test, and why each one matters
--------------------------------------------

* **A dependency blocks a task.** The spec names the edge and never says what it
  does. The rule chosen is: a task is blocked while any task it depends on is not
  done. The alternative rejected was deriving blocking from the due dates.
* **Blocking follows the edge, not the clock.** A task whose predecessor is due
  *later* is still blocked, which is the property that separates the two rules.
* **A finished task is never re-blocked.** Otherwise a party that did the work
  would see it fall back to blocked because a predecessor was never ticked.
* **An internal-only task is invisible to the buyer**, in the list and on a direct
  read by id. The second is the one that matters: a task hidden in the UI and
  present in the response is not hidden.
* **The visibility flag is per task.** A plan that was half internal would lose
  the shared roadmap if the flag were per plan.
* **Overdue items escalate**, and a task due *today* is due soon rather than
  overdue, so a plan does not turn red at midnight.
* **A closed plan becomes an implementation plan and keeps its tasks.** This is
  the "survives closed-won" requirement, and copying rather than replacing is
  what preserves the record of what the parties agreed to.
* **Every record is plain JSON in ``data``.** A team adding a field must need no
  coordination with anyone, so this module adds no migration and no typed column.

Every test names the property it protects, because a test named
``test_plan_flow`` tells a reviewer nothing about which rule they can stop
worrying about.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from dsr.db.audited import AuditedDatabase
from dsr.features import load_feature
from dsr.mutual_action import plan as map_plan
from dsr.store import RecordStore

MODULE = "wf124_native_mutual_action_plan_execution"

NOW = datetime(2026, 5, 4, 9, 0, tzinfo=timezone.utc)


@pytest.fixture()
def engine(store: RecordStore) -> map_plan.PlanEngine:
    """The engine, with the clock pinned so every date boundary is exact."""
    return map_plan.PlanEngine(store, now=lambda: NOW)


@pytest.fixture()
def room(store: RecordStore) -> str:
    return store.create("room", {"name": "Northwind"}, actor="test", source="test")["id"]


def _task(title: str, owner: str = "Dana", **extra: object) -> dict:
    return {"title": title, "owner": owner, **extra}


def _make_plan(engine: map_plan.PlanEngine, room_id: str, tasks: list[dict], **extra: object):
    return engine.create_plan(
        room_id, {"name": "Northwind MAP", "tasks": tasks, **extra}, source="test", actor="test"
    )


def _by_title(plan: dict, title: str) -> dict:
    for task in plan["tasks"]:
        if task["title"] == title:
            return task
    raise AssertionError(f"no task named {title!r} in {[t['title'] for t in plan['tasks']]}")


# --------------------------------------------------------------------------- #
# The dependency rule
# --------------------------------------------------------------------------- #


def test_a_task_waits_on_a_predecessor_that_is_not_done(engine, room):
    """The edge, not the date, is what blocks a task.

    The predecessor here is due *later* than the task that waits on it. A rule that
    derived blocking from the dates would call this task ready; the rule chosen
    calls it blocked, because the edge is the ordering the parties agreed to.
    """
    plan = _make_plan(
        engine,
        room,
        [
            _task("Sign the order form", due_date=(NOW + timedelta(days=5)).isoformat()),
            _task(
                "Security review", owner="Buyer IT", due_date=(NOW + timedelta(days=30)).isoformat()
            ),
        ],
    )
    order_form = _by_title(plan, "Sign the order form")
    security = _by_title(plan, "Security review")

    engine.update_task(
        order_form["id"], {"depends_on": [security["id"]]}, source="test", actor="test"
    )
    reloaded = engine.read_plan(plan["id"])

    assert _by_title(reloaded, "Sign the order form")["status"] == map_plan.STATUS_BLOCKED
    assert _by_title(reloaded, "Security review")["status"] == map_plan.STATUS_TODO


def test_finishing_a_predecessor_unblocks_its_dependent(engine, room):
    """The block clears when the predecessor is done, and only then."""
    plan = _make_plan(engine, room, [_task("First"), _task("Second")])
    first = _by_title(plan, "First")
    second = _by_title(plan, "Second")

    engine.update_task(second["id"], {"depends_on": [first["id"]]}, source="test", actor="test")
    assert _by_title(engine.read_plan(plan["id"]), "Second")["status"] == map_plan.STATUS_BLOCKED

    engine.update_task(first["id"], {"status": "done"}, source="test", actor="test")
    assert _by_title(engine.read_plan(plan["id"]), "Second")["status"] == map_plan.STATUS_TODO


def test_a_finished_task_is_never_re_blocked(engine, room):
    """Work that is done stays done, whatever its predecessors say.

    Otherwise a party that finished the work would watch it fall back to blocked
    because a predecessor was never ticked, and would stop trusting the board.
    """
    plan = _make_plan(engine, room, [_task("Predecessor"), _task("Shipped")])
    predecessor = _by_title(plan, "Predecessor")
    shipped = _by_title(plan, "Shipped")

    engine.update_task(
        shipped["id"], {"depends_on": [predecessor["id"]]}, source="test", actor="test"
    )
    engine.update_task(shipped["id"], {"status": "done"}, source="test", actor="test")

    reloaded = engine.read_plan(plan["id"])
    assert _by_title(reloaded, "Shipped")["status"] == map_plan.STATUS_DONE
    assert _by_title(reloaded, "Predecessor")["status"] == map_plan.STATUS_TODO


def test_a_task_cannot_depend_on_itself(engine, room):
    """A self-edge would make a task block itself forever, so it is refused."""
    plan = _make_plan(engine, room, [_task("Self referential")])
    task = _by_title(plan, "Self referential")

    with pytest.raises(map_plan.PlanError) as caught:
        engine.update_task(task["id"], {"depends_on": [task["id"]]}, source="test", actor="test")
    assert "itself" in caught.value.errors["depends_on"]


def test_a_dependency_cannot_point_outside_the_plan(engine, room):
    """An edge to a task that is not on this plan is refused, not carried as dangling."""
    plan = _make_plan(engine, room, [_task("On the plan")])
    other = _make_plan(engine, room, [_task("On another plan")])
    task = _by_title(plan, "On the plan")
    stranger = _by_title(other, "On another plan")

    with pytest.raises(map_plan.PlanError) as caught:
        engine.update_task(
            task["id"], {"depends_on": [stranger["id"]]}, source="test", actor="test"
        )
    assert "another task on this plan" in caught.value.errors["depends_on"]


def test_a_template_edge_is_repointed_at_the_new_plan_s_own_tasks(engine, room):
    """Instantiating a template rewrites its edges onto this plan's task ids.

    A template is reusable, so its rows carry refs rather than ids. If the edges
    were copied verbatim the new plan would depend on ids that mean nothing in it,
    and every such task would read as unblocked.
    """
    template = engine.create_template(
        {
            "name": "Standard close",
            "tasks": [
                _task("Security review", owner="Buyer IT", **{map_plan.TEMPLATE_REF: "security"}),
                _task(
                    "Sign the order form",
                    depends_on=["security"],
                    **{map_plan.TEMPLATE_REF: "orderform"},
                ),
            ],
        },
        source="test",
        actor="test",
    )
    plan = engine.create_plan(
        room, {"name": "From template", "template_id": template["id"]}, source="test", actor="test"
    )

    security = _by_title(plan, "Security review")
    order_form = _by_title(plan, "Sign the order form")
    assert order_form["depends_on"] == [security["id"]]
    assert order_form["status"] == map_plan.STATUS_BLOCKED

    # A second instantiation gets its own task ids, so the two plans do not share
    # a graph and finishing a task on one does not unblock the other.
    second = engine.create_plan(
        room,
        {"name": "Also from template", "template_id": template["id"]},
        source="test",
        actor="test",
    )
    second_security = _by_title(second, "Security review")
    assert second_security["id"] != security["id"]
    engine.update_task(security["id"], {"status": "done"}, source="test", actor="test")
    assert _by_title(engine.read_plan(second["id"]), "Sign the order form")["status"] == (
        map_plan.STATUS_BLOCKED
    )


def test_a_template_edge_naming_an_unknown_ref_is_dropped_not_carried(engine, room):
    """An edge to a ref the template does not define does not survive.

    :func:`blocked_by` reads an edge to a missing task as satisfied, so carrying it
    would produce a task that looks ready when its predecessor was never written.
    """
    template = engine.create_template(
        {
            "name": "Broken edge",
            "tasks": [
                _task("Waits on nothing", depends_on=["no-such-ref"]),
            ],
        },
        source="test",
        actor="test",
    )
    plan = engine.create_plan(
        room, {"name": "Broken", "template_id": template["id"]}, source="test", actor="test"
    )
    task = _by_title(plan, "Waits on nothing")
    assert task["status"] == map_plan.STATUS_TODO
    assert not task.get("depends_on")


def test_deleting_a_task_an_open_task_waits_on_is_refused(engine, room):
    """The delete is refused and names the reason, because the graph would lie.

    Deleting the predecessor would leave the dependent on an edge to nothing, which
    the graph reads as satisfied. The board would then tell a buyer a task can start
    when its owner says it cannot.
    """
    plan = _make_plan(engine, room, [_task("Predecessor"), _task("Dependent")])
    predecessor = _by_title(plan, "Predecessor")
    dependent = _by_title(plan, "Dependent")
    engine.update_task(
        dependent["id"], {"depends_on": [predecessor["id"]]}, source="test", actor="test"
    )

    with pytest.raises(map_plan.PlanBlocked) as caught:
        engine.delete_task(predecessor["id"], source="test", actor="test")
    assert caught.value.reason == map_plan.PlanBlocked.REASON_DEPENDENT

    # Once the dependent is finished it no longer needs the predecessor.
    engine.update_task(dependent["id"], {"status": "done"}, source="test", actor="test")
    outcome = engine.delete_task(predecessor["id"], source="test", actor="test")
    assert outcome["removed"] is True


# --------------------------------------------------------------------------- #
# Visibility
# --------------------------------------------------------------------------- #


def test_the_buyer_never_sees_an_internal_only_task(engine, room):
    """The internal task is absent from the response, not hidden by the UI."""
    plan = _make_plan(
        engine,
        room,
        [
            _task("Shared task"),
            _task("Internal negotiation", visibility=map_plan.VISIBILITY_INTERNAL),
        ],
    )
    assert plan["internal_count"] == 1

    buyer_view = engine.visible_plan(plan["id"], audience=map_plan.SIDE_BUYER)
    titles = [task["title"] for task in buyer_view["tasks"]]
    assert titles == ["Shared task"], titles
    assert buyer_view["task_count"] == 1

    # The seller sees both, so the board is not hiding work from the party that
    # has to do it.
    seller_view = engine.visible_plan(plan["id"], audience=map_plan.SIDE_SELLER)
    assert seller_view["task_count"] == 2


def test_a_buyer_reading_an_internal_task_by_id_is_told_it_does_not_exist(engine, room):
    """A direct read must not leak it either, and must not confirm it exists.

    A 403 would tell the buyer that something is behind the id, so the buyer is
    given the same answer as for an id that was never written.
    """
    plan = _make_plan(engine, room, [_task("Internal only", visibility="internal")])
    task = _by_title(plan, "Internal only")

    with pytest.raises(map_plan.PlanNotFound):
        engine.read_task(task["id"], audience=map_plan.SIDE_BUYER)

    assert engine.read_task(task["id"], audience=map_plan.SIDE_SELLER)["title"] == "Internal only"


def test_the_visibility_flag_is_per_task_so_a_shared_roadmap_survives(engine, room):
    """One internal task must not remove the whole plan from the buyer.

    This is the property that distinguishes the flag chosen from the alternative
    rejected: a per-plan flag would make a half-internal plan entirely invisible,
    losing the "shared roadmap" the research calls for.
    """
    plan = _make_plan(
        engine,
        room,
        [
            _task("Security review", owner="Buyer IT"),
            _task("Internal margin check", visibility="internal"),
            _task("Kickoff date"),
        ],
    )
    buyer_tasks = engine.visible_plan(plan["id"], audience="buyer")["tasks"]
    assert [task["title"] for task in buyer_tasks] == ["Security review", "Kickoff date"]


def test_an_unrecognised_audience_is_treated_as_the_buyer(engine, room):
    """The default fails toward hiding internal work, not toward publishing it."""
    plan = _make_plan(engine, room, [_task("Shared"), _task("Internal", visibility="internal")])
    for audience in (None, "", "stranger", "client-side"):
        view = engine.visible_plan(plan["id"], audience=audience)
        assert [task["title"] for task in view["tasks"]] == ["Shared"], audience


def test_a_task_with_no_visibility_field_is_visible(engine, room):
    """An absent field is external, so a seller who forgot the flag shares it.

    The safe direction: a task meant to be hidden is marked ``internal``, and a
    field this workflow has never heard of cannot make a roadmap vanish.
    """
    stored = map_plan.validate_task({"title": "Plain", "owner": "Dana"})
    assert "visibility" not in stored or stored["visibility"] == map_plan.VISIBILITY_EXTERNAL
    assert map_plan.is_internal(stored) is False


# --------------------------------------------------------------------------- #
# Dates, escalation and reminders
# --------------------------------------------------------------------------- #


def test_a_task_past_its_due_date_is_overdue_and_raises_a_row(engine, room):
    """Overdue items escalate, and the escalation is a row of its own."""
    plan = _make_plan(
        engine, room, [_task("Late task", due_date=(NOW - timedelta(days=3)).isoformat())]
    )
    task = _by_title(plan, "Late task")

    state, days = map_plan.escalation_state(task, now=NOW)
    assert state == map_plan.ESCALATION_OVERDUE
    assert days == -3

    rows = engine.escalations_of(plan["id"])
    assert [row["task_id"] for row in rows] == [task["id"]]
    assert rows[0]["state"] == map_plan.ESCALATION_OVERDUE


def test_a_task_due_today_is_due_soon_not_overdue(engine):
    """The plan does not turn red at midnight on the day a task is meant to land."""
    state, days = map_plan.escalation_state(
        {"due_date": NOW.date().isoformat(), "status": "todo"}, now=NOW
    )
    assert state == map_plan.ESCALATION_DUE_SOON
    assert days == 0


def test_a_task_done_is_owed_nothing_even_when_its_date_has_passed(engine):
    """A finished task does not escalate, however late it finished."""
    state, _days = map_plan.escalation_state(
        {"due_date": (NOW - timedelta(days=9)).isoformat(), "status": "done"}, now=NOW
    )
    assert state is None


def test_a_task_with_no_readable_date_is_not_escalated(engine):
    """An unreadable date fails toward *not* escalating.

    Failing the other way would make a typo mark a task escalated forever, and an
    escalation nobody can explain is one a seller learns to ignore.
    """
    for due in (None, "", "not-a-date", "2026-13-45"):
        state, _days = map_plan.escalation_state({"due_date": due, "status": "todo"}, now=NOW)
        assert state is None, due


def test_a_task_further_out_than_the_lead_time_is_not_reminded(engine):
    """The reminder lead time is a constant, so it can be argued with in one place."""
    far = (NOW + timedelta(days=map_plan.REMINDER_LEAD_DAYS + 1)).isoformat()
    near = (NOW + timedelta(days=map_plan.REMINDER_LEAD_DAYS)).isoformat()

    assert map_plan.reminder_due({"due_date": far, "status": "todo"}, now=NOW) is False
    assert map_plan.reminder_due({"due_date": near, "status": "todo"}, now=NOW) is True


def test_one_escalation_row_per_task_per_state(engine, room):
    """A task that is due soon and then goes overdue has both rows.

    They are two different statements, and a seller escalating an overdue task
    should not have to guess whether the reminder already fired.
    """
    plan = _make_plan(engine, room, [_task("Slipping task")])
    task = _by_title(plan, "Slipping task")

    engine.update_task(
        task["id"], {"due_date": (NOW + timedelta(days=1)).isoformat()}, source="test", actor="test"
    )
    engine.update_task(
        task["id"], {"due_date": (NOW - timedelta(days=1)).isoformat()}, source="test", actor="test"
    )

    rows = engine.escalations_of(plan["id"])
    assert {row["state"] for row in rows} == {
        map_plan.ESCALATION_DUE_SOON,
        map_plan.ESCALATION_OVERDUE,
    }


def test_a_status_change_writes_an_event(engine, room):
    """The spec names status events, so a task's history is answerable."""
    plan = _make_plan(engine, room, [_task("Moving task")])
    task = _by_title(plan, "Moving task")

    engine.update_task(task["id"], {"status": "in_progress"}, source="test", actor="test")
    engine.update_task(task["id"], {"status": "done"}, source="test", actor="test")

    events = engine.events_of(plan["id"])
    assert [(event["from"], event["to"]) for event in events] == [
        (map_plan.STATUS_TODO, map_plan.STATUS_IN_PROGRESS),
        (map_plan.STATUS_IN_PROGRESS, map_plan.STATUS_DONE),
    ]


def test_an_unchanged_status_writes_no_event(engine, room):
    """Sending the status a task already has is not a change and not a fact."""
    plan = _make_plan(engine, room, [_task("Steady task")])
    task = _by_title(plan, "Steady task")

    engine.update_task(task["id"], {"status": "in_progress"}, source="test", actor="test")
    engine.update_task(task["id"], {"status": "in_progress"}, source="test", actor="test")

    assert len(engine.events_of(plan["id"])) == 1


# --------------------------------------------------------------------------- #
# Closed-won
# --------------------------------------------------------------------------- #


def test_a_closed_plan_becomes_an_implementation_plan_that_keeps_the_tasks(engine, room):
    """The plan survives closed-won, which is the whole requirement.

    The tasks are carried over rather than replaced, so the agreed work is still
    there after the deal closes. That is what "the plan survives closed-won and
    becomes the implementation plan" asks for.
    """
    plan = _make_plan(
        engine,
        room,
        [
            _task("Hand over the order form", due_date=NOW.date().isoformat()),
            _task("Internal margin check", visibility="internal"),
        ],
    )
    outcome = engine.close_as_won(plan["id"], source="test", actor="test")

    assert outcome["phase"] == map_plan.PHASE_CLOSED_WON
    assert outcome["carried_tasks"] == 2

    implementation = engine.read_plan(outcome["implementation_plan_id"])
    assert implementation["phase"] == map_plan.PHASE_IMPLEMENTATION
    assert sorted(task["title"] for task in implementation["tasks"]) == [
        "Hand over the order form",
        "Internal margin check",
    ]

    # The selling plan is still there as the record of what was agreed.
    original = engine.read_plan(plan["id"])
    assert original["task_count"] == 2
    assert original["tasks"] != implementation["tasks"]


def test_the_carried_tasks_are_copies_not_the_same_rows(engine, room):
    """Closing a plan must not make editing the implementation plan rewrite history."""
    plan = _make_plan(engine, room, [_task("Agreed task")])
    outcome = engine.close_as_won(plan["id"], source="test", actor="test")

    carried = engine.read_plan(outcome["implementation_plan_id"])["tasks"][0]
    original = _by_title(engine.read_plan(plan["id"]), "Agreed task")
    assert carried["id"] != original["id"]

    engine.update_task(carried["id"], {"status": "done"}, source="test", actor="test")
    assert _by_title(engine.read_plan(plan["id"]), "Agreed task")["status"] == (
        map_plan.STATUS_TODO
    )


def test_conversion_is_pure_so_the_rule_is_testable_without_a_database():
    """The derivation is a function of the plan and its tasks, not of a clock side effect."""
    plan = {"id": "p1", "name": "Northwind MAP"}
    tasks = [
        {"id": "t1", "title": "One", "owner": "Dana", "owner_side": "seller", "status": "done"},
        {"id": "t2", "title": "Two", "owner": "Buyer", "owner_side": "buyer", "status": "todo"},
    ]
    outcome = map_plan.convert_to_implementation(plan, tasks, now=NOW)

    assert outcome["plan_patch"]["phase"] == map_plan.PHASE_IMPLEMENTATION
    assert outcome["plan_patch"]["converted_from"] == "p1"
    assert [task["title"] for task in outcome["tasks"]] == ["One", "Two"]
    assert all(task["phase"] == map_plan.PHASE_IMPLEMENTATION for task in outcome["tasks"])
    assert all(task["carried_from"] == "p1" for task in outcome["tasks"])
    # The inputs are not mutated, so a caller can convert twice and get the same answer.
    assert [task["status"] for task in tasks] == ["done", "todo"]


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #


def test_a_task_without_a_named_owner_is_refused():
    """The plan tracks people, so an unnamed owner is not a task."""
    with pytest.raises(map_plan.PlanError) as caught:
        map_plan.validate_task({"title": "Anonymous"})
    assert "owner" in caught.value.errors


def test_a_task_without_a_title_is_refused():
    with pytest.raises(map_plan.PlanError) as caught:
        map_plan.validate_task({"owner": "Dana"})
    assert "title" in caught.value.errors


def test_an_unknown_side_status_or_visibility_is_refused():
    with pytest.raises(map_plan.PlanError) as caught:
        map_plan.validate_task(
            {
                "title": "Odd",
                "owner": "Dana",
                "owner_side": "legal",
                "status": "frozen",
                "visibility": "secret",
            }
        )
    errors = caught.value.errors
    assert set(errors) == {"owner_side", "status", "visibility"}


def test_a_plan_needs_a_template_or_at_least_one_task():
    with pytest.raises(map_plan.PlanError) as caught:
        map_plan.validate_plan({"name": "Empty"})
    assert "tasks" in caught.value.errors


def test_a_plan_may_not_be_given_both_a_template_and_task_rows():
    """Otherwise the two sources of tasks are ambiguous about which one won."""
    with pytest.raises(map_plan.PlanError) as caught:
        map_plan.validate_plan(
            {"name": "Both", "template_id": "t1", "tasks": [{"title": "A", "owner": "Dana"}]}
        )
    assert "not both" in caught.value.errors["tasks"]


def test_a_template_needs_at_least_one_task_row():
    """A template with no rows would instantiate an empty plan, which is not a plan."""
    with pytest.raises(map_plan.PlanError) as caught:
        map_plan.validate_template({"name": "Empty", "tasks": []})
    assert "tasks" in caught.value.errors


def test_a_bad_task_inside_a_plan_reports_its_position():
    """The message must name which row is wrong, or a form cannot show it."""
    with pytest.raises(map_plan.PlanError) as caught:
        map_plan.validate_plan(
            {"name": "Mixed", "tasks": [{"title": "Fine", "owner": "Dana"}, {"title": "Bad"}]}
        )
    assert "tasks[1].owner" in caught.value.errors


# --------------------------------------------------------------------------- #
# Schema flexibility
# --------------------------------------------------------------------------- #


def test_a_team_can_add_its_own_field_with_no_migration(store, room):
    """The store stays schema-flexible: an extra field round-trips as plain JSON.

    This is the property that lets a team add a field without coordinating with
    anyone. If it needed a migration or a typed column, adding one would become a
    cross-team change.
    """
    record = store.create(
        map_plan.TASK_COLLECTION,
        {
            "title": "Task with a local field",
            "owner": "Dana",
            "owner_side": map_plan.SIDE_SELLER,
            "procurement_ticket": "PR-4471",
            "our_field": {"reviewer": "sam", "weight": 3},
        },
        room_id=room,
        actor="test",
        source="test",
    )
    found = store.find(map_plan.TASK_COLLECTION, {"procurement_ticket": "PR-4471"}, limit=5)
    assert [row["id"] for row in found] == [record["id"]]
    assert found[0]["data"]["our_field"] == {"reviewer": "sam", "weight": 3}


def test_every_record_this_workflow_writes_is_ordinary_json(store, engine, room):
    """No plan, task, event or escalation row carries anything but JSON values."""
    plan = _make_plan(engine, room, [_task("Round trip", due_date=NOW.date().isoformat())])
    task = _by_title(plan, "Round trip")
    engine.update_task(task["id"], {"status": "done"}, source="test", actor="test")

    for collection in (
        map_plan.PLAN_COLLECTION,
        map_plan.TASK_COLLECTION,
        map_plan.EVENT_COLLECTION,
        map_plan.ESCALATION_COLLECTION,
    ):
        for row in store.find(collection, {}, limit=50):
            for key, value in row["data"].items():
                assert isinstance(key, str), (collection, key)
                assert isinstance(value, (str, int, float, bool, list, dict, type(None))), (
                    collection,
                    key,
                    value,
                )


def test_the_domain_module_imports_no_framework_and_no_sqlite():
    """An enforced property, restated here so the reason survives.

    A domain module that imported FastAPI could not be exercised without a server,
    and one that opened SQLite itself could bypass the audit guarantee. The suite
    asserts this over every feature module; this test names it for this workflow.
    """
    import ast
    from pathlib import Path

    import dsr.mutual_action.plan as module

    source = Path(module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)

    assert "sqlite3" not in imported
    assert "fastapi" not in imported
    assert "dsr.api" not in imported
    assert "starlette" not in imported


# --------------------------------------------------------------------------- #
# The feature module's own contract
# --------------------------------------------------------------------------- #


def test_the_feature_declares_a_ticket_derived_prefix():
    """The contract requires a prefix of /api/<ticket>, unique across features."""
    feature = load_feature(MODULE)
    assert feature.router.prefix == "/api/wf-124"
    assert feature.FEATURE["ticket"] == "WF-124"
    assert feature.FEATURE["id"] == "wf-124-native-mutual-action-plan-execution"


def test_every_route_sits_under_this_features_own_prefix():
    """A route outside the prefix would land on a path this workflow does not own."""
    feature = load_feature(MODULE)
    for route in feature.router.routes:
        assert route.path.startswith("/api/wf-124"), route.path


def test_the_vocabulary_publishes_the_decisions_the_issue_left_open():
    """A reviewer must be able to see each derivation without reading the source."""
    served = map_plan.vocabulary()
    assert served["decisions"]["visibility_scope"] == "The flag is per task, not per plan."
    assert served["reminder_lead_days"] == map_plan.REMINDER_LEAD_DAYS
    assert served["statuses"] == list(map_plan.STATUSES)
    assert served["visibilities"] == list(map_plan.VISIBILITIES)
    # The e-signature surface is named as unsourced, not quietly built.
    assert any("e-signature" in note for note in served["not_implemented"])


def test_the_seed_string_is_encodable_by_cp1252(db: AuditedDatabase):
    """The seeder prints it on a Windows console, so every character must encode.

    A single RIGHTWARDS ARROW in one recovered feature broke the entire seeder.
    """
    feature = load_feature(MODULE)
    room_ids = [
        (db.create("room", {"name": name}, actor="seed", source="seed")["id"], name)
        for name in ("Northwind", "Vantage")
    ]
    reported = feature.seed(db, {"room_ids": room_ids, "now": NOW})
    assert reported
    reported.encode("cp1252")
    assert reported == reported.encode("cp1252").decode("cp1252")


def test_the_seed_creates_the_states_the_issue_asks_for(db: AuditedDatabase):
    """The demo must show a blocked task, an overdue one, an internal one and a
    closed plan, or a reviewer cannot see the workflow doing its work."""
    feature = load_feature(MODULE)
    room_ids = [
        (db.create("room", {"name": name}, actor="seed", source="seed")["id"], name)
        for name in ("Northwind", "Vantage")
    ]
    feature.seed(db, {"room_ids": room_ids, "now": NOW})

    store = RecordStore(db)
    engine = map_plan.PlanEngine(store, now=lambda: NOW)
    counted = engine.summary()

    assert counted["plans"] >= 3, counted
    assert counted["blocked"] >= 1, counted
    assert counted["internal_tasks"] >= 1, counted
    assert counted["overdue"] >= 1, counted
    assert counted["implementation_plans"] >= 1, counted
    assert counted["done"] >= 1, counted


def test_the_summary_reports_the_board_numbers_without_a_write(engine, room):
    _make_plan(engine, room, [_task("A"), _task("B", visibility="internal")])
    counted = engine.summary(room)
    assert counted["plans"] == 1
    assert counted["tasks"] == 2
    assert counted["internal_tasks"] == 1
    assert "generated_at" in counted


def test_plans_are_scoped_to_their_room(engine, store):
    first = store.create("room", {"name": "One"}, actor="test", source="test")["id"]
    second = store.create("room", {"name": "Two"}, actor="test", source="test")["id"]
    _make_plan(engine, first, [_task("In room one")])
    _make_plan(engine, second, [_task("In room two")])

    assert len(engine.list_plans(first)) == 1
    assert engine.list_plans(first)[0]["name"] == "Northwind MAP"
    assert len(engine.list_plans()) == 2
