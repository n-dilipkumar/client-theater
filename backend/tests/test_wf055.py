"""WF-055: hand a lead off from an SDR scheduler to an AE. Domain tests.

These drive :mod:`dsr.handoff_scheduler` over a temporary database, with a clock
the test controls, so every assertion about a slot time is about a fixed instant
rather than about whatever time the suite happened to run.

The HTTP surface has its own file, ``test_wf055_http.py``. Both pass when run on
their own, which matters because the suite runs under ``pytest-xdist`` and a test
that only passes in one order fails intermittently on a busy runner.

What is asserted here, and why
------------------------------

* The researched sentences. Each behaviour is traced to the quote that fixes it,
  so a test failure says which research the build stopped honouring.
* The per-path intersection derivation. The research names neither the operation
  nor who it combines, so the derivation in ``availability`` is a decision that can
  be disagreed with, and the tests below pin what it decided.
* The ``Required`` toggle in both states. An evidence line says availability is
  not considered *unless* the button is toggled, and a test that only checked the
  untoggled state would pass against the behaviour the evidence forbids.
* Four bugs written to be caught, each of which shipped in some form during this
  build: the ``data`` envelope being read off a body that already was one, a stale
  gate set being trusted instead of re-checked at booking time, a shadowed
  ``crmExplicits`` key being silently dropped rather than reported, and the
  summary counting paths by iterating an empty list.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from dsr.handoff_scheduler import (
    MEETING_COLLECTION,
    ROUTER_COLLECTION,
    ROUTING_COLLECTION,
    WORKSPACE_COLLECTION,
    HandoffConflict,
    HandoffError,
    HandoffNotFound,
    HandoffSchedulerEngine,
    build_context,
    busy_at,
    calendar_connected,
    describe,
    exclusion_reason,
    explain_miss,
    explain_missing,
    find_slot,
    find_user,
    gating_user_ids,
    grid,
    has_role,
    ignored_user_ids,
    index_users,
    inferences as handoff_inferences,
    is_free,
    iso,
    match_report,
    matches,
    normalise_interval,
    overlaps,
    parse,
    path_id,
    path_window,
    published_vocabulary,
    read_request,
    required_of,
    roles_of,
    summarise,
    user_id,
    validate_path,
    validate_paths,
    validate_workspace,
    value_matches,
)
from dsr.handoff_scheduler.engine import DEFAULT_DURATION_MINUTES

#: A fixed instant. Every test that reads a slot time derives it from here, so the
#: assertions are about the arithmetic rather than about the wall clock.
NOW = datetime(2026, 3, 2, 8, 0, tzinfo=timezone.utc)

#: The interval every test uses: five working days from Monday 09:00, in 30 minute
#: steps.
INTERVAL = {
    "start": "2026-03-02T09:00:00+00:00",
    "end": "2026-03-06T17:00:00+00:00",
    "duration_minutes": 30,
    "min_notice_minutes": 0,
    "max_days": 14,
}

PACKAGE = Path(__file__).resolve().parents[1] / "dsr" / "handoff_scheduler"


def clock() -> datetime:
    """The engine's clock, fixed at :data:`NOW`."""
    return NOW


@pytest.fixture
def engine(store):
    """An engine over an empty database, with a fixed clock."""
    return HandoffSchedulerEngine(store, clock=clock)


def person(identifier: str, **extra):
    """One workspace user, holding both roles unless the test says otherwise."""
    return {
        "user_id": identifier,
        "name": identifier.upper(),
        "email": f"{identifier}@example.test",
        "roles": extra.pop("roles", ["booker", "assignee"]),
        "calendar_connected": extra.pop("calendar_connected", True),
        "busy": extra.pop("busy", []),
        **extra,
    }


def busy(identifier: str, start: str, end: str, **extra):
    """A user with one busy block."""
    return person(identifier, busy=[{"start": start, "end": end}], **extra)


def make_workspace(engine, *users, name="Demo pod", room_id=None):
    """Declare a workspace from user rows."""
    return engine.create_workspace(
        {"name": name, "users": list(users), "room_id": room_id}, actor="dana", source="test"
    )


def make_router(engine, workspace_id, *paths, name="Demo router", **extra):
    """Declare a router over a workspace."""
    payload = {"name": name, "workspace_ref": workspace_id, "paths": list(paths)}
    payload.update(extra)
    return engine.create_router(payload, actor="dana", source="test")


def make_path(identifier, assignee, **extra):
    """One declared routing path."""
    entry = {"path_id": identifier, "assignee_ref": assignee}
    entry.update(extra)
    return entry


def request_for(**extra):
    """A GuestEmailRequest an SDR would send, with the defaults filled in."""
    payload = {
        "type": "GuestEmailRequest",
        "guestEmail": "lead@example.test",
        "booker_ref": "sdr",
        "crmExplicits": {"region": "emea"},
        "interval": INTERVAL,
    }
    payload.update(extra)
    return payload


# --------------------------------------------------------------------------- #
# The researched sentences
# --------------------------------------------------------------------------- #


def test_the_link_type_is_handoff_and_not_the_two_that_sit_beside_it():
    """ "a rep is handing off a lead to another team member ... an SDR booking a
    discovery call with an AE"."""
    published = published_vocabulary()
    routed = [entry for entry in published["link_types"] if entry["routed_by_this_workflow"]]
    assert [entry["link_type"] for entry in routed] == ["Handoff"]
    assert published["link_type_names"] == ["Handoff", "RoundRobin", "Ownership"]


def test_the_two_request_shapes_are_exactly_the_two_the_researched_payload_names():
    """ "either {type:"GuestEmailRequest", guestEmail, interval} or
    {type:"CrmRequest", id, interval}"."""
    published = published_vocabulary()
    assert published["request_type_names"] == ["GuestEmailRequest", "CrmRequest"]
    for entry in published["request_types"]:
        assert entry["sourced_from"], entry
        assert entry["source"] == "chilipiper_programmatic"


def test_the_meeting_roles_are_the_two_the_research_names_in_one_sentence():
    """ "meeting created with SDR as Booker and AE as Assignee"."""
    published = published_vocabulary()
    assert published["meeting_role_names"] == ["booker", "assignee"]
    by_role = {entry["role"]: entry for entry in published["meeting_roles"]}
    assert "SDR" in by_role["booker"]["who"]
    assert "AE" in by_role["assignee"]["who"]


def test_every_published_term_is_sourced_or_flagged_as_an_inference():
    """No term may quietly acquire a quotation it does not have.

    The distinction the brief insists on is carried on the entries themselves, so
    this walks the whole published vocabulary rather than a hand-picked sample.
    """
    published = published_vocabulary()
    known = {entry["id"] for entry in describe()["inferences"]}
    for block in (
        "link_types",
        "request_types",
        "meeting_roles",
        "api_field_names",
        "evaluation_outcomes",
    ):
        for entry in published[block]:
            assert entry.get("sourced_from"), entry
            assert entry.get("source"), entry
    for block in ("path_availability", "required_toggle", "crm_explicits", "reassignment_handoff"):
        entry = published[block]
        assert entry.get("inference") or entry.get("sourced_from"), block
        if entry.get("inference"):
            assert entry["inference"] in known, block


def test_the_required_toggle_is_published_with_its_researched_default():
    """ "Chili Piper will not consider their availability ... unless you toggle the
    Required button"."""
    toggle = published_vocabulary()["required_toggle"]
    assert toggle["default"] is False
    assert toggle["field"] == "required"
    assert "unless you toggle" in toggle["sourced_from"]


def test_the_four_researched_schedule_fields_are_published_with_their_stored_names():
    """ "pass the corresponding routingId, routerId, pathId, and startTime"."""
    entries = {entry["api"]: entry for entry in published_vocabulary()["api_field_names"]}
    assert set(entries) >= {"routingId", "routerId", "pathId", "startTime"}
    assert entries["routerId"]["stored_as"] == "router_ref"
    assert entries["pathId"]["stored_as"] == "path_id"


def test_this_plugin_records_what_a_reassignment_needs_and_does_not_own_reassignment():
    """ "reassignment later respects your Handoff/ChiliCal User controls and the
    Distribution settings of the meeting booked"."""
    handoff = published_vocabulary()["reassignment_handoff"]
    assert handoff["owned_by_this_plugin"] is False
    for field in ("workspace_ref", "router_ref", "path_id", "booker_ref", "assignee_ref"):
        assert field in handoff["carries_on_the_meeting"], field


def test_a_pod_is_one_workspace():
    """ "workspaces partition SDR/AE pods so a third party can run one router per
    pod"."""
    pod = published_vocabulary()["pod_partition"]
    assert pod["one_workspace_is"] == "one SDR/AE pod"
    assert "one router per pod" in pod["sourced_from"]


# --------------------------------------------------------------------------- #
# timeutil
# --------------------------------------------------------------------------- #


def test_a_naive_timestamp_is_read_as_utc_not_as_the_hosts_local_zone():
    """The same workspace must offer the same slots on two machines."""
    assert parse("2026-03-02T09:00:00") == parse("2026-03-02T09:00:00Z")
    assert parse("2026-03-02T09:00:00").tzinfo is not None
    assert parse("2026-03-02T09:00:00+02:00") == datetime(2026, 3, 2, 7, 0, tzinfo=timezone.utc)


def test_a_timestamp_is_required():
    with pytest.raises(ValueError):
        parse("")


def test_overlaps_is_half_open_so_back_to_back_meetings_do_not_collide():
    """An SDR routinely books an AE back to back."""
    start = parse("2026-03-02T09:00:00+00:00")
    end = parse("2026-03-02T09:30:00+00:00")
    later = parse("2026-03-02T09:30:00+00:00")
    assert overlaps(start, end, start, end) is True
    assert overlaps(start, end, later, later + timedelta(minutes=30)) is False


def test_the_slot_grid_is_aligned_to_the_interval_start_not_to_midnight():
    """Aligning to midnight would drop the first part of a 09:30 interval."""
    slots = grid(parse("2026-03-02T09:30:00+00:00"), parse("2026-03-02T10:30:00+00:00"), 30, 10)
    assert [iso(start) for start, _ in slots] == [
        "2026-03-02T09:30:00+00:00",
        "2026-03-02T10:00:00+00:00",
    ]


def test_the_slot_grid_emits_no_slot_that_does_not_fit_inside_the_interval():
    slots = grid(parse("2026-03-02T09:00:00+00:00"), parse("2026-03-02T10:10:00+00:00"), 30, 10)
    assert len(slots) == 2
    assert all(close <= parse("2026-03-02T10:10:00+00:00") for _, close in slots)


def test_the_slot_grid_is_bounded():
    start = parse("2026-03-02T09:00:00+00:00")
    assert len(grid(start, start + timedelta(days=30), 15, 5)) == 5


def test_a_non_positive_duration_is_refused():
    start = parse("2026-03-02T09:00:00+00:00")
    with pytest.raises(ValueError):
        grid(start, start + timedelta(hours=1), 0, 5)


def test_a_user_with_no_connected_calendar_is_never_free_at_a_slot():
    """Treating unreadable as free is how a lead lands on an AE who never accepted."""
    assert is_free([], parse("2026-03-02T09:00:00+00:00"), parse("2026-03-02T09:30:00+00:00"))
    assert not is_free(
        [(parse("2026-03-02T09:15:00+00:00"), parse("2026-03-02T10:00:00+00:00"))],
        parse("2026-03-02T09:00:00+00:00"),
        parse("2026-03-02T09:30:00+00:00"),
    )


# --------------------------------------------------------------------------- #
# The interval
# --------------------------------------------------------------------------- #


def test_the_interval_defaults_to_a_window_from_now_when_the_caller_names_none():
    normalised = normalise_interval({})
    assert normalised["duration_minutes"] == 30
    assert parse(normalised["end"]) > parse(normalised["start"])


def test_the_interval_clamps_a_max_days_beyond_the_bound():
    """The interval arrives in a request body and an unbounded one is a hazard."""
    with pytest.raises(HandoffError) as caught:
        normalise_interval({"max_days": 500})
    assert "at most" in str(caught.value)


def test_an_explicit_zero_is_refused_rather_than_read_as_absent():
    """`body.get("max_days") or 14` would answer with a fortnight nobody asked for."""
    with pytest.raises(HandoffError):
        normalise_interval({"duration_minutes": 0})
    with pytest.raises(HandoffError):
        normalise_interval({"max_days": 0})


def test_the_interval_refuses_one_that_does_not_move_forward():
    with pytest.raises(HandoffError) as caught:
        normalise_interval(
            {"start": "2026-03-06T17:00:00+00:00", "end": "2026-03-02T09:00:00+00:00"}
        )
    assert "must end after it starts" in str(caught.value)


def test_the_interval_refuses_a_negative_minimum_notice():
    with pytest.raises(HandoffError):
        normalise_interval({"min_notice_minutes": -5})


# --------------------------------------------------------------------------- #
# Workspaces, roles, and the calendar gate
# --------------------------------------------------------------------------- #


def test_a_workspace_needs_a_name_and_at_least_one_user():
    with pytest.raises(HandoffError) as caught:
        validate_workspace({"users": [person("a")]})
    assert "needs a name" in str(caught.value)
    with pytest.raises(HandoffError) as caught:
        validate_workspace({"name": "W", "users": []})
    assert "at least one user" in str(caught.value)


def test_a_user_with_no_id_is_refused():
    """No path, invitee or booking could name a user with no id."""
    with pytest.raises(HandoffError) as caught:
        validate_workspace({"name": "W", "users": [{"name": "Nameless"}]})
    assert "user_id" in str(caught.value)


def test_two_users_sharing_an_id_are_refused():
    """The router would route to whichever of the two happened to be read first."""
    with pytest.raises(HandoffError) as caught:
        validate_workspace({"name": "W", "users": [person("a"), person("a")]})
    assert "twice" in str(caught.value)


def test_a_user_that_is_not_an_object_is_refused():
    with pytest.raises(HandoffError):
        validate_workspace({"name": "W", "users": ["just a string"]})


def test_a_user_id_falls_back_to_its_email_and_then_to_its_envelope_id():
    """Two importers name the same field differently, and a router must resolve."""
    assert user_id({"email": "someone@example.test"}) == "someone@example.test"
    assert user_id({"id": "from-an-envelope"}) == "from-an-envelope"
    assert user_id({"name": "Nameless"}) == ""


def test_an_absent_roles_list_means_both_and_an_explicit_false_is_respected():
    """A workspace row from an older importer must not be unable to do anything."""
    assert validate_workspace({"name": "W", "users": [{"user_id": "a"}]})["users"][0]["roles"] == [
        "booker",
        "assignee",
    ]
    one = validate_workspace({"name": "W", "users": [person("a", roles=["booker"])]})
    assert one["users"][0]["roles"] == ["booker"]
    assert has_role(one["users"][0], "booker") is True
    assert has_role(one["users"][0], "assignee") is False


def test_an_explicit_empty_roles_list_is_respected():
    """A user nobody may book or assign is a thing an admin can mean."""
    nobody = validate_workspace({"name": "W", "users": [person("a", roles=[])]})
    assert roles_of(nobody["users"][0]) == []


def test_a_role_outside_the_two_researched_names_is_refused():
    with pytest.raises(HandoffError) as caught:
        validate_workspace({"name": "W", "users": [person("a", roles=["admin"])]})
    assert "booker, assignee" in str(caught.value)


def test_a_roles_value_that_is_not_a_list_is_refused():
    with pytest.raises(HandoffError) as caught:
        validate_workspace({"name": "W", "users": [person("a", roles="booker")]})
    assert "not a list" in str(caught.value)


def test_an_absent_calendar_flag_means_connected_and_an_explicit_false_is_respected():
    assert validate_workspace({"name": "W", "users": [{"user_id": "a"}]})["users"][0][
        "calendar_connected"
    ]
    off = validate_workspace({"name": "W", "users": [person("a", calendar_connected=False)]})
    assert calendar_connected(off["users"][0]) is False


def test_a_busy_block_that_cannot_be_read_is_refused_rather_than_dropped():
    """Dropping it would report an AE free for a period they are busy."""
    for value in (
        [{"start": "not-a-time", "end": "2026-03-02T10:00:00+00:00"}],
        [{"start": "2026-03-02T09:00:00+00:00"}],
        ["nope"],
        "not-a-list",
    ):
        with pytest.raises(HandoffError):
            validate_workspace({"name": "W", "users": [person("a", busy=value)]})


def test_the_calendar_and_role_exclusions_are_reported_separately():
    """The fix for one is a calendar connection and for the other a workspace edit."""
    assert exclusion_reason(person("a", calendar_connected=False)) == "calendar not connected"
    assert (
        exclusion_reason(person("b", roles=["booker"]), role="assignee")
        == "not an assignee on this workspace"
    )
    assert exclusion_reason(person("c")) is None


def test_a_refusal_about_a_role_reads_as_english_for_both_researched_names():
    """ "not a assignee" is the sort of thing a reader stops trusting a message over."""
    from dsr.handoff_scheduler import role_article

    assert role_article("assignee") == "an"
    assert role_article("booker") == "a"
    assert role_article("Assignee") == "an"


def test_the_workspace_summary_separates_bookers_from_assignees():
    """A pod with no booker cannot open a routing, and one with no assignee has
    nothing to route to. Those are different problems with the same user count."""
    summary = summarise(
        [
            person("s", roles=["booker"]),
            person("a", roles=["assignee"]),
            person("x", roles=["booker"]),
        ]
    )
    assert summary == {
        "users": 3,
        "bookers": 2,
        "assignees": 1,
        "calendar_not_connected": 0,
        "user_ids": ["s", "a", "x"],
    }


def test_a_user_is_found_by_id_then_by_email_then_by_name():
    workspace = {"id": "w1", "data": {"name": "W", "users": [person("ae-rui")]}}
    assert find_user(workspace, "ae-rui")["name"] == "AE-RUI"
    assert find_user(workspace, "ae-rui@example.test")["user_id"] == "ae-rui"
    assert find_user(workspace, "AE-RUI")["user_id"] == "ae-rui"
    assert find_user(workspace, "nobody") is None
    assert find_user(workspace, "") is None


def test_the_user_index_ignores_a_user_with_no_id():
    assert sorted(index_users({"data": {"users": [{"name": "Nameless"}, person("a")]}})) == ["a"]


def test_a_missing_user_is_refused_naming_the_users_the_workspace_does_have():
    """ "user not found" leaves an SDR looking at a router they can see."""
    from dsr.handoff_scheduler import require_user

    workspace = {"id": "w1", "data": {"name": "W", "users": [person("ae-rui")]}}
    with pytest.raises(HandoffError) as caught:
        require_user(workspace, "ae-ghost")
    assert "ae-rui" in str(caught.value)


def test_a_user_without_the_role_asked_of_them_is_refused_naming_their_roles():
    from dsr.handoff_scheduler import require_user

    workspace = {"id": "w1", "data": {"name": "W", "users": [person("a", roles=["booker"])]}}
    with pytest.raises(HandoffError) as caught:
        require_user(workspace, "a", role="assignee")
    assert "Their roles are booker" in str(caught.value)


# --------------------------------------------------------------------------- #
# Routing paths
# --------------------------------------------------------------------------- #


def test_a_router_needs_at_least_one_path():
    """ "defining routing paths (e.g. region -> AE pod, product line -> AE)"."""
    workspace = {"id": "w1", "data": {"name": "W", "users": [person("a")]}}
    with pytest.raises(HandoffError) as caught:
        validate_paths({"paths": []}, workspace)
    assert "at least one routing path" in str(caught.value)


def test_a_path_needs_a_path_id_because_the_schedule_call_carries_it():
    workspace = {"id": "w1", "data": {"name": "W", "users": [person("a")]}}
    with pytest.raises(HandoffError) as caught:
        validate_path({"assignee_ref": "a"}, workspace)
    assert "path_id" in str(caught.value)


def test_a_path_reads_its_id_from_the_researched_spelling_too():
    assert path_id({"pathId": "emea-standard"}) == "emea-standard"
    assert path_id({"id": "from-an-envelope"}) == "from-an-envelope"


def test_a_path_needs_an_assignee_because_a_route_with_nobody_at_the_end_goes_nowhere():
    workspace = {"id": "w1", "data": {"name": "W", "users": [person("a")]}}
    with pytest.raises(HandoffError) as caught:
        validate_path({"path_id": "p"}, workspace)
    assert "is not on workspace" in str(caught.value)


def test_a_path_assignee_must_hold_the_assignee_role():
    workspace = {"id": "w1", "data": {"name": "W", "users": [person("a", roles=["booker"])]}}
    with pytest.raises(HandoffError) as caught:
        validate_path(make_path("p", "a"), workspace)
    assert "is not an assignee on this workspace" in str(caught.value)


def test_a_path_assignee_with_no_connected_calendar_is_refused_at_declaration():
    """The fix is a calendar connection, and it is cheaper to say so now."""
    workspace = {
        "id": "w1",
        "data": {"name": "W", "users": [person("a", calendar_connected=False)]},
    }
    with pytest.raises(HandoffError) as caught:
        validate_path(make_path("p", "a"), workspace)
    assert "calendar is not connected" in str(caught.value)


def test_a_path_invitee_must_be_on_the_workspace():
    workspace = {"id": "w1", "data": {"name": "W", "users": [person("a")]}}
    with pytest.raises(HandoffError) as caught:
        validate_path(make_path("p", "a", invitees=[{"user_ref": "ghost"}]), workspace)
    assert "ghost" in str(caught.value)


def test_the_assignee_is_refused_as_an_invitee_because_the_meeting_already_carries_them():
    workspace = {"id": "w1", "data": {"name": "W", "users": [person("a")]}}
    with pytest.raises(HandoffError) as caught:
        validate_path(make_path("p", "a", invitees=[{"user_ref": "a"}]), workspace)
    assert "already carries them as the Assignee" in str(caught.value)


def test_an_invitee_listed_twice_is_refused():
    workspace = {"id": "w1", "data": {"name": "W", "users": [person("a"), person("b")]}}
    with pytest.raises(HandoffError) as caught:
        validate_path(
            make_path("p", "a", invitees=[{"user_ref": "b"}, {"user_ref": "b"}]), workspace
        )
    assert "twice" in str(caught.value)


def test_two_paths_sharing_a_path_id_are_refused():
    """The researched schedule call would be ambiguous about which one answered."""
    workspace = {"id": "w1", "data": {"name": "W", "users": [person("a")]}}
    with pytest.raises(HandoffError) as caught:
        validate_paths({"paths": [make_path("p", "a"), make_path("p", "a")]}, workspace)
    assert "twice" in str(caught.value)


def test_an_absent_required_flag_means_not_required_which_is_the_researched_default():
    """ "will not consider their availability ... unless you toggle the Required
    button". The default is the restrictive one here, not the permissive one."""
    workspace = {"id": "w1", "data": {"name": "W", "users": [person("a"), person("b")]}}
    path = validate_path(make_path("p", "a", invitees=[{"user_ref": "b"}]), workspace)
    assert required_of(path["invitees"][0]) is False
    assert gating_user_ids(path) == ["a"]
    assert ignored_user_ids(path) == ["b"]


def test_a_required_invitee_joins_the_gate_set():
    workspace = {"id": "w1", "data": {"name": "W", "users": [person("a"), person("b")]}}
    path = validate_path(
        make_path("p", "a", invitees=[{"user_ref": "b", "required": True}]), workspace
    )
    assert gating_user_ids(path) == ["a", "b"]
    assert ignored_user_ids(path) == []


def test_a_match_block_that_is_not_an_object_is_refused():
    workspace = {"id": "w1", "data": {"name": "W", "users": [person("a")]}}
    with pytest.raises(HandoffError) as caught:
        validate_path(make_path("p", "a", match="region=emea"), workspace)
    assert "not an object" in str(caught.value)


def test_an_assignee_who_left_the_workspace_is_named_by_the_recheck():
    """A router is a reusable asset and a workspace is edited independently."""
    from dsr.handoff_scheduler import assignee_can_be_assigned

    path = {"path_id": "p", "assignee_ref": "ae-gone", "invitees": []}
    assert assignee_can_be_assigned(path, {"data": {"users": []}}) == (
        "ae-gone is no longer on this workspace"
    )
    assert (
        assignee_can_be_assigned(
            path, {"data": {"users": [person("ae-gone", calendar_connected=False)]}}
        )
        == "ae-gone has no connected calendar any more"
    )
    assert (
        assignee_can_be_assigned(path, {"data": {"users": [person("ae-gone", roles=["booker"])]}})
        == "ae-gone is not an assignee on this workspace any more"
    )
    assert assignee_can_be_assigned(path, {"data": {"users": [person("ae-gone")]}}) is None


# --------------------------------------------------------------------------- #
# The request context and the rule match
# --------------------------------------------------------------------------- #


def test_a_guest_email_request_carries_the_email_and_no_record_id():
    kind, email, record, explicits = read_request(
        {"type": "GuestEmailRequest", "guestEmail": "a@b.test"}
    )
    assert (kind, email, record) == ("GuestEmailRequest", "a@b.test", None)
    assert explicits == {}


def test_a_crm_request_carries_the_record_id_and_no_email():
    kind, email, record, _ = read_request({"type": "CrmRequest", "id": "00Q5"})
    assert (kind, email, record) == ("CrmRequest", None, "00Q5")


def test_a_request_that_names_neither_identity_is_refused():
    with pytest.raises(HandoffError) as caught:
        read_request({"type": "GuestEmailRequest"})
    assert "guestEmail is required" in str(caught.value)
    with pytest.raises(HandoffError) as caught:
        read_request({"type": "CrmRequest"})
    assert "id is required" in str(caught.value)


def test_a_request_carrying_both_identities_is_refused_rather_than_guessed():
    """The researched payload has one shape for each, so a body with both is ambiguous."""
    with pytest.raises(HandoffError) as caught:
        read_request({"type": "GuestEmailRequest", "guestEmail": "a@b.test", "id": "00Q5"})
    assert "one shape for each" in str(caught.value)


def test_a_type_outside_the_two_researched_shapes_is_refused():
    with pytest.raises(HandoffError) as caught:
        read_request({"type": "SmsRequest"})
    assert "GuestEmailRequest, CrmRequest" in str(caught.value)


def test_crm_explicits_must_be_an_object():
    with pytest.raises(HandoffError) as caught:
        read_request({"type": "CrmRequest", "id": "00Q5", "crmExplicits": ["region"]})
    assert "must be an object" in str(caught.value)


def test_the_context_carries_the_researched_fields_plus_every_explicit():
    """ "crmExplicits lets an integrator pass arbitrary CRM context into routing
    rules" -- arbitrary means arbitrary."""
    context, shadowed = build_context(
        "CrmRequest", crm_record_id="00Q5", explicits={"region": "emea", "tier": 3}
    )
    assert context == {
        "region": "emea",
        "tier": 3,
        "request_type": "CrmRequest",
        "crm_record_id": "00Q5",
    }
    assert shadowed == []


def test_an_explicit_cannot_shadow_a_researched_field_and_the_collision_is_reported():
    """Silently dropping the key would leave an integrator's lead failing for a
    reason that names neither their field nor the field that won."""
    context, shadowed = build_context(
        "GuestEmailRequest",
        guest_email="real@example.test",
        explicits={"guest_email": "wrong@example.test", "region": "emea"},
    )
    assert context["guest_email"] == "real@example.test"
    assert shadowed == ["guest_email"]


def test_a_shadowed_key_is_reported_even_when_no_researched_field_fills_the_gap():
    context, shadowed = build_context(
        "CrmRequest", crm_record_id="00Q5", explicits={"guest_email": "wrong@example.test"}
    )
    assert "guest_email" not in context
    assert shadowed == ["guest_email"]


def test_a_rule_comparison_ignores_case_and_surrounding_whitespace():
    """An SDR typing an email will not reproduce the CRM's capitalisation."""
    assert value_matches("  EMEA ", "emea") is True
    assert value_matches("emea", "EMEA") is True
    assert value_matches("emea", "apac") is False


def test_a_rule_may_declare_any_of_several_values():
    assert value_matches("apac", ["emea", "apac"]) is True
    assert value_matches("amer", ["emea", "apac"]) is False


def test_a_rule_may_ask_for_a_value_to_be_absent():
    assert value_matches(None, None) is True
    assert value_matches("platform", None) is False


def test_every_declared_field_must_be_satisfied_and_an_empty_block_matches_everything():
    context = {"region": "emea", "product_line": "platform"}
    assert matches({"region": "emea"}, context) is True
    assert matches({"region": "emea", "product_line": "platform"}, context) is True
    assert matches({"region": "emea", "product_line": "security"}, context) is False
    assert matches({"tier": 3}, context) is False, "a key the request lacks cannot be satisfied"
    assert matches({}, context) is True
    assert matches(None, context) is True


def test_a_missed_rule_names_the_field_and_both_values():
    message = explain_miss({"region": "emea", "product_line": "security"}, {"region": "emea"})
    assert "no product_line" in message
    message = explain_miss({"region": "apac"}, {"region": "emea"})
    assert "region=apac" in message and "region=emea" in message


def test_a_matched_rule_explains_nothing_because_it_matched():
    assert explain_miss({"region": "emea"}, {"region": "emea"}) == (
        "this path has no match rule, so it matches every request"
    )


def test_the_match_report_names_the_losers_rather_than_dropping_them():
    """ "one or more routing paths" plus an SDR who typed the wrong region."""
    paths = [
        {"path_id": "emea", "name": "EMEA", "match": {"region": "emea"}},
        {"path_id": "any", "name": "Catch-all", "match": {}},
    ]
    report = match_report(paths, {"region": "apac"})
    assert [entry["matched"] for entry in report] == [False, True]
    assert "region=emea" in report[0]["reason"]
    assert report[1]["reason"] == ""


# --------------------------------------------------------------------------- #
# Per-path availability: the derivation
# --------------------------------------------------------------------------- #


def test_a_path_offers_a_slot_only_when_its_assignee_is_free():
    """The derivation Jev ratified: audit jev-20261004T065905-27100-45006."""
    workspace = {"data": {"users": [person("ae-rui")]}}
    path = {"path_id": "p", "assignee_ref": "ae-rui", "invitees": []}
    window = path_window(
        path,
        workspace,
        start=parse(INTERVAL["start"]),
        end=parse(INTERVAL["end"]),
        duration_minutes=30,
        now=NOW,
    )
    assert window["operation"] == "intersection_of_required_calendars"
    assert window["derivation"] == "inference_path_availability_is_intersection"
    assert window["gating_user_ids"] == ["ae-rui"]
    assert window["slots"][0]["start_at"] == "2026-03-02T09:00:00+00:00"


def test_a_busy_assignee_removes_their_instant_from_the_path_rather_than_another_paths():
    """A path names one AE. A union here would offer an instant the AE is busy for."""
    workspace = {
        "data": {
            "users": [
                busy("ae-rui", "2026-03-02T09:00:00+00:00", "2026-03-02T12:00:00+00:00"),
                person("ae-priya"),
            ]
        }
    }
    busy_path = {"path_id": "busy", "assignee_ref": "ae-rui", "invitees": []}
    free_path = {"path_id": "free", "assignee_ref": "ae-priya", "invitees": []}
    common = {"start": parse(INTERVAL["start"]), "end": parse(INTERVAL["end"]), "now": NOW}
    busy_window = path_window(busy_path, workspace, duration_minutes=30, **common)
    free_window = path_window(free_path, workspace, duration_minutes=30, **common)
    assert busy_window["slots"][0]["start_at"] == "2026-03-02T12:00:00+00:00"
    assert free_window["slots"][0]["start_at"] == "2026-03-02T09:00:00+00:00"


def test_a_not_required_invitee_does_not_narrow_the_path_at_all():
    """The quoted sentence about the Required button, asserted in the untoggled state."""
    window = _window_for(
        [
            person("ae-rui"),
            busy("se-sam", "2026-03-02T09:00:00+00:00", "2026-03-06T17:00:00+00:00"),
        ],
        [{"user_ref": "se-sam", "required": False}],
    )
    assert window["gating_user_ids"] == ["ae-rui"]
    assert window["ignored_user_ids"] == ["se-sam"]
    assert window["slots"][0]["start_at"] == "2026-03-02T09:00:00+00:00"


def test_a_required_invitee_does_narrow_the_path():
    """The same person, toggled on, and the path loses every instant they are busy."""
    window = _window_for(
        [
            person("ae-rui"),
            busy("se-sam", "2026-03-02T09:00:00+00:00", "2026-03-06T17:00:00+00:00"),
        ],
        [{"user_ref": "se-sam", "required": True}],
    )
    assert window["gating_user_ids"] == ["ae-rui", "se-sam"]
    assert window["ignored_user_ids"] == []
    assert window["slot_count"] == 0
    assert window["busy_user_ids"] == ["se-sam"]


def test_a_required_invitee_with_no_connected_calendar_empties_the_path_and_says_so():
    """Unreadable is not free. The fix is a calendar connection, not another AE."""
    window = _window_for(
        [person("ae-rui"), person("mgr-lena", calendar_connected=False)],
        [{"user_ref": "mgr-lena", "required": True}],
    )
    assert window["slot_count"] == 0
    assert window["busy_user_ids"] == ["mgr-lena"]
    assert "mgr-lena" in explain_missing(window, "2026-03-02T09:00:00+00:00")


def test_a_not_required_invitee_with_no_connected_calendar_does_not_empty_the_path():
    window = _window_for(
        [person("ae-rui"), person("mgr-lena", calendar_connected=False)],
        [{"user_ref": "mgr-lena", "required": False}],
    )
    assert window["slot_count"] > 0


def test_a_gate_user_the_workspace_no_longer_carries_is_reported_rather_than_failing_the_call():
    """A stale SE should cost one note, not every other path on the router."""
    workspace = {"data": {"users": [person("ae-rui")]}}
    path = {
        "path_id": "p",
        "assignee_ref": "ae-rui",
        "invitees": [{"user_ref": "se-gone", "required": True, "name": "Gone"}],
    }
    window = path_window(
        path,
        workspace,
        start=parse(INTERVAL["start"]),
        end=parse(INTERVAL["end"]),
        duration_minutes=30,
        now=NOW,
    )
    assert window["unresolved_user_ids"] == ["se-gone"]
    assert window["slot_count"] > 0
    assert "no longer on the workspace" in explain_missing(
        {"slots": [], "gating_user_ids": [], "unresolved_user_ids": ["se-gone"]},
        "2026-03-02T09:00:00+00:00",
    )


def test_the_window_honours_the_minimum_notice():
    window = _window_for([person("ae-rui")], [], min_notice_minutes=120)
    assert window["slots"][0]["start_at"] == "2026-03-02T11:00:00+00:00"


def test_the_window_refuses_a_range_that_does_not_move_forward():
    with pytest.raises(HandoffError):
        path_window(
            {"path_id": "p", "assignee_ref": "ae-rui", "invitees": []},
            {"data": {"users": [person("ae-rui")]}},
            start=parse(INTERVAL["end"]),
            end=parse(INTERVAL["start"]),
            duration_minutes=30,
            now=NOW,
        )


def test_busy_at_counts_an_unconnected_calendar_as_busy_rather_than_free():
    users = [person("a"), person("b", calendar_connected=False)]
    assert busy_at(
        users, parse(INTERVAL["start"]), parse(INTERVAL["start"]) + timedelta(minutes=30)
    ) == ["b"]


def test_a_time_the_path_never_offered_says_so_rather_than_explaining():
    assert (
        explain_missing(
            {"slots": [{"start_at": "2026-03-02T09:00:00+00:00"}]}, "2026-03-02T09:00:00+00:00"
        )
        == "the requested time is available"
    )


def test_a_time_before_the_next_open_slot_names_the_people_who_gate_the_path():
    message = explain_missing(
        {
            "slots": [{"start_at": "2026-03-02T10:00:00+00:00"}],
            "gating_user_ids": ["ae-rui", "se-sam"],
        },
        "2026-03-02T09:15:00+00:00",
    )
    assert "ae-rui, se-sam" in message
    assert "2026-03-02T10:00:00+00:00" in message


def test_a_time_after_every_slot_names_the_interval_and_the_gate_set():
    message = explain_missing(
        {
            "slots": [],
            "start": INTERVAL["start"],
            "end": INTERVAL["end"],
            "gating_user_ids": ["ae-rui"],
        },
        "2026-03-06T16:00:00+00:00",
    )
    assert "no slot on this path is open" in message
    assert INTERVAL["start"] in message and INTERVAL["end"] in message


def test_a_time_outside_the_interval_with_no_gate_set_is_told_the_range():
    message = explain_missing(
        {"slots": [], "start": INTERVAL["start"], "end": INTERVAL["end"], "gating_user_ids": []},
        "2026-03-06T16:00:00+00:00",
    )
    assert "outside the interval" in message


def test_a_slot_is_found_by_its_exact_instant_not_by_containment():
    """Booking 09:15 against a 09:00-09:30 slot is refused, not quietly rounded."""
    window = {
        "slots": [{"start_at": "2026-03-02T09:00:00+00:00", "end_at": "2026-03-02T09:30:00+00:00"}]
    }
    assert find_slot(window, "2026-03-02T09:00:00+00:00")["end_at"] == "2026-03-02T09:30:00+00:00"
    assert find_slot(window, "2026-03-02T09:15:00+00:00") is None


def _window_for(users, invitees, *, min_notice_minutes=0):
    """One path's window over a hand-built workspace and invitee list."""
    return path_window(
        {"path_id": "p", "assignee_ref": users[0]["user_id"], "invitees": invitees},
        {"data": {"users": users}},
        start=parse(INTERVAL["start"]),
        end=parse(INTERVAL["end"]),
        duration_minutes=30,
        min_notice_minutes=min_notice_minutes,
        now=NOW,
    )


# --------------------------------------------------------------------------- #
# The engine, end to end
# --------------------------------------------------------------------------- #


def test_declaring_a_workspace_writes_one_row_and_audits_it(store):
    engine = HandoffSchedulerEngine(store, clock=clock)
    workspace = make_workspace(engine, person("sdr", roles=["booker"]), person("ae"))
    assert workspace["collection"] == WORKSPACE_COLLECTION
    assert len(store.list(WORKSPACE_COLLECTION)) == 1
    assert store.db.audit_count(collection=WORKSPACE_COLLECTION) == 1


def test_the_workspace_view_annotates_each_user_with_what_they_may_do(store):
    engine = HandoffSchedulerEngine(store, clock=clock)
    workspace = make_workspace(
        engine,
        person("sdr", roles=["booker"]),
        person("ae"),
        person("ghost", roles=["assignee"], calendar_connected=False),
    )
    view = engine.workspace_view(workspace)
    rows = {row["user_id"]: row for row in view["users"]}
    assert rows["sdr"]["can_book"] is True and rows["sdr"]["assignable"] is False
    assert rows["ae"]["can_book"] is True and rows["ae"]["assignable"] is True
    assert rows["ghost"]["assignable"] is False
    assert rows["ghost"]["assignable_reason"] == "calendar not connected"


def test_a_router_must_name_a_workspace_that_exists(engine):
    with pytest.raises(HandoffNotFound):
        make_router(engine, "no-such-workspace", make_path("p", "ae"))


def test_a_router_requires_a_name_and_a_workspace_reference(engine):
    workspace = make_workspace(engine, person("ae"))
    with pytest.raises(HandoffError) as caught:
        engine.create_router(
            {"workspace_ref": workspace["id"], "paths": [make_path("p", "ae")]}, source="test"
        )
    assert "needs a name" in str(caught.value)
    with pytest.raises(HandoffError) as caught:
        engine.create_router({"name": "R", "paths": [make_path("p", "ae")]}, source="test")
    assert "workspace_ref is required" in str(caught.value)


def test_a_router_records_its_paths_and_its_default_interval(engine):
    workspace = make_workspace(engine, person("sdr"), person("ae"))
    router = make_router(engine, workspace["id"], make_path("p", "ae", match={"region": "emea"}))
    assert router["data"]["path_count"] == 1
    assert router["data"]["paths"][0]["assignee_name"] == "AE"
    assert router["data"]["interval"]["duration_minutes"] == DEFAULT_DURATION_MINUTES


def test_the_router_view_reports_the_gate_set_and_the_ignored_invitees(store):
    engine = HandoffSchedulerEngine(store, clock=clock)
    workspace = make_workspace(engine, person("sdr"), person("ae"), person("se"), person("mgr"))
    router = make_router(
        engine,
        workspace["id"],
        make_path(
            "p",
            "ae",
            invitees=[{"user_ref": "se", "required": True}, {"user_ref": "mgr", "required": False}],
        ),
    )
    view = engine.router_view(router)
    assert view["paths"][0]["gating_user_ids"] == ["ae", "se"]
    assert view["paths"][0]["ignored_user_ids"] == ["mgr"]


def test_the_preview_writes_nothing_at_all(store):
    """The read-only half of init-simple, for an SDR who asks before committing."""
    engine = HandoffSchedulerEngine(store, clock=clock)
    workspace = make_workspace(engine, person("sdr"), person("ae"))
    make_router(engine, workspace["id"], make_path("emea", "ae", match={"region": "emea"}))
    preview = engine.check(workspace["id"], request_for())
    assert preview["outcome"] == "paths_offered"
    assert preview["path_count"] == 1
    assert store.list(ROUTING_COLLECTION) == []
    assert store.list(MEETING_COLLECTION) == []


def test_the_preview_and_the_initialisation_agree_on_the_paths(store):
    """They call the same evaluation, so only the consequences differ."""
    engine = HandoffSchedulerEngine(store, clock=clock)
    workspace = make_workspace(engine, person("sdr"), person("ae"))
    make_router(
        engine,
        workspace["id"],
        make_path("emea", "ae", match={"region": "emea"}),
        make_path("any", "ae"),
    )
    preview = engine.check(workspace["id"], request_for())
    opened = engine.init_simple(workspace["id"], request_for(), source="test")
    assert [p["path_id"] for p in preview["paths"]] == [p["path_id"] for p in opened["paths"]]


def test_init_simple_answers_with_one_or_more_paths_each_with_its_own_start_times(store):
    """ "returns one or more routing paths, each with its own pathId and startTimes"."""
    engine = HandoffSchedulerEngine(store, clock=clock)
    workspace = make_workspace(
        engine,
        person("sdr"),
        busy("ae-rui", "2026-03-02T09:00:00+00:00", "2026-03-02T10:00:00+00:00"),
        person("ae-priya"),
    )
    make_router(
        engine,
        workspace["id"],
        make_path("emea-standard", "ae-rui", match={"region": "emea"}),
        make_path(
            "emea-platform", "ae-priya", match={"region": "emea", "product_line": "platform"}
        ),
        make_path("any-lead", "ae-priya"),
    )
    opened = engine.init_simple(
        workspace["id"],
        request_for(crmExplicits={"region": "emea", "product_line": "platform"}),
        source="test",
    )
    assert opened["outcome"] == "paths_offered"
    assert [path["path_id"] for path in opened["paths"]] == [
        "emea-standard",
        "emea-platform",
        "any-lead",
    ]
    times = {path["path_id"]: path["start_times"] for path in opened["paths"]}
    assert times["emea-standard"][0] == "2026-03-02T10:00:00+00:00"
    assert times["emea-platform"][0] == "2026-03-02T09:00:00+00:00"
    assert times["any-lead"][0] == "2026-03-02T09:00:00+00:00"


def test_the_paths_come_back_in_declaration_order(store):
    engine = HandoffSchedulerEngine(store, clock=clock)
    workspace = make_workspace(engine, person("sdr"), person("ae"))
    make_router(
        engine,
        workspace["id"],
        make_path("third", "ae", match={"region": "emea"}),
        make_path("first", "ae", match={"region": "emea"}),
        make_path("second", "ae", match={"region": "emea"}),
    )
    opened = engine.init_simple(workspace["id"], request_for(), source="test")
    assert [path["path_id"] for path in opened["paths"]] == ["third", "first", "second"]


def test_a_matched_path_with_no_free_time_is_still_returned_with_an_empty_start_time_list(store):
    """The SDR's next move is to pick a different path, which needs the empty ones."""
    engine = HandoffSchedulerEngine(store, clock=clock)
    workspace = make_workspace(
        engine,
        person("sdr"),
        busy("ae-busy", INTERVAL["start"], INTERVAL["end"]),
        person("ae-free"),
    )
    make_router(
        engine,
        workspace["id"],
        make_path("blocked", "ae-busy", match={"region": "emea"}),
        make_path("open", "ae-free", match={"region": "emea"}),
    )
    opened = engine.init_simple(workspace["id"], request_for(), source="test")
    blocked = next(p for p in opened["paths"] if p["path_id"] == "blocked")
    free = next(p for p in opened["paths"] if p["path_id"] == "open")
    assert blocked["start_times"] == []
    assert blocked["window"]["busy_user_ids"] == ["ae-busy"]
    assert free["slot_count"] > 0
    assert opened["outcome"] == "paths_offered"


def test_every_matched_path_being_empty_is_a_success_with_the_outcome_no_availability(store):
    """A busy week is a legitimate answer rather than an error."""
    engine = HandoffSchedulerEngine(store, clock=clock)
    workspace = make_workspace(
        engine, person("sdr"), busy("ae-busy", INTERVAL["start"], INTERVAL["end"])
    )
    make_router(engine, workspace["id"], make_path("blocked", "ae-busy", match={"region": "emea"}))
    opened = engine.init_simple(workspace["id"], request_for(), source="test")
    assert opened["outcome"] == "no_availability"
    assert opened["paths_with_availability"] == 0
    assert opened["routing_id"], "the routing is still written so the answer is reproducible"


def test_a_router_that_matched_no_path_is_the_one_refusal_and_names_every_path(store):
    """An empty path list is indistinguishable from a router that was never set up."""
    engine = HandoffSchedulerEngine(store, clock=clock)
    workspace = make_workspace(engine, person("sdr"), person("ae-rui"), person("ae-ade"))
    make_router(engine, workspace["id"], make_path("emea", "ae-rui", match={"region": "emea"}))
    with pytest.raises(HandoffError) as caught:
        engine.init_simple(
            workspace["id"], request_for(crmExplicits={"region": "antarctica"}), source="test"
        )
    message = str(caught.value)
    assert "no routing path matched" in message
    assert "path emea" in message and "region=emea" in message
    assert "antarctica" in message
    assert store.list(ROUTING_COLLECTION) == [], "a refusal must not leave a routing"


def test_a_catch_all_path_is_why_an_unmatched_request_can_still_be_routed(store):
    """The empty match block is the only spelling of a catch-all."""
    engine = HandoffSchedulerEngine(store, clock=clock)
    workspace = make_workspace(engine, person("sdr"), person("ae"))
    make_router(
        engine,
        workspace["id"],
        make_path("emea", "ae", match={"region": "emea"}),
        make_path("any-lead", "ae"),
    )
    opened = engine.init_simple(
        workspace["id"], request_for(crmExplicits={"region": "antarctica"}), source="test"
    )
    assert [path["path_id"] for path in opened["paths"]] == ["any-lead"]


def test_a_workspace_with_no_router_is_refused_naming_what_to_build(engine):
    workspace = make_workspace(engine, person("sdr"), person("ae"))
    with pytest.raises(HandoffError) as caught:
        engine.init_simple(workspace["id"], request_for(), source="test")
    assert "no Handoff Router" in str(caught.value)


def test_an_evaluation_needs_a_booker_holding_the_booker_role(engine):
    workspace = make_workspace(engine, person("ae"), person("only-ae", roles=["assignee"]))
    make_router(engine, workspace["id"], make_path("p", "ae", match={"region": "emea"}))
    with pytest.raises(HandoffError) as caught:
        engine.init_simple(workspace["id"], request_for(booker_ref=""), source="test")
    assert "booker_ref is required" in str(caught.value)
    with pytest.raises(HandoffError) as caught:
        engine.init_simple(workspace["id"], request_for(booker_ref="only-ae"), source="test")
    assert "not a booker" in str(caught.value)


def test_a_router_id_may_be_named_and_it_must_belong_to_the_workspace(engine):
    first = make_workspace(engine, person("sdr"), person("ae-a"), name="Pod A")
    second = make_workspace(engine, person("sdr"), person("ae-b"), name="Pod B")
    router_a = make_router(engine, first["id"], make_path("a", "ae-a"), name="A")
    router_b = make_router(engine, second["id"], make_path("b", "ae-b"), name="B")
    opened = engine.init_simple(first["id"], request_for(router_ref=router_a["id"]), source="test")
    assert [path["path_id"] for path in opened["paths"]] == ["a"]
    with pytest.raises(HandoffError) as caught:
        engine.init_simple(first["id"], request_for(router_ref=router_b["id"]), source="test")
    assert "not to" in str(caught.value)


def test_the_routers_own_interval_is_the_default_and_a_callers_overrides_it(store):
    """The researched init payload sends its own interval next to the request."""
    engine = HandoffSchedulerEngine(store, clock=clock)
    workspace = make_workspace(engine, person("sdr"), person("ae"))
    make_router(
        engine,
        workspace["id"],
        make_path("p", "ae", match={"region": "emea"}),
        interval={"start": "2026-04-01T09:00:00+00:00", "duration_minutes": 60},
    )
    default = engine.init_simple(workspace["id"], request_for(interval=None), source="test")
    assert default["paths"][0]["interval"]["start"] == "2026-04-01T09:00:00+00:00"
    assert default["paths"][0]["interval"]["duration_minutes"] == 60
    assert default["paths"][0]["start_times"][0] == "2026-04-01T09:00:00+00:00"

    override = engine.init_simple(
        workspace["id"],
        request_for(interval={"start": "2026-05-04T09:00:00+00:00", "duration_minutes": 30}),
        source="test",
    )
    assert override["paths"][0]["interval"]["start"] == "2026-05-04T09:00:00+00:00"
    assert override["paths"][0]["start_times"][0] == "2026-05-04T09:00:00+00:00"


def test_a_slot_offered_under_the_routers_interval_can_still_be_booked(store):
    """Booking re-reads the interval the path was offered under.

    Reading the routing's own ``interval`` instead would refuse a start time the SDR
    was legitimately shown, because that field is the interval the *caller* asked
    for and a router may carry a different default.
    """
    engine = HandoffSchedulerEngine(store, clock=clock)
    workspace = make_workspace(engine, person("sdr"), person("ae"))
    make_router(
        engine,
        workspace["id"],
        make_path("p", "ae", match={"region": "emea"}),
        interval={"start": "2026-04-01T09:00:00+00:00", "duration_minutes": 60},
    )
    opened = engine.init_simple(workspace["id"], request_for(interval=None), source="test")
    taken = engine.schedule_simple(
        opened["routing_id"],
        str(opened["paths"][0]["router_ref"]),
        "p",
        "sdr",
        {"startTime": opened["paths"][0]["start_times"][0]},
        source="test",
    )
    assert taken["start_at"] == "2026-04-01T09:00:00+00:00"
    assert taken["end_at"] == "2026-04-01T10:00:00+00:00"


def test_a_routing_keeps_the_explicits_verbatim_and_reports_the_shadowed_keys(store):
    engine = HandoffSchedulerEngine(store, clock=clock)
    workspace = make_workspace(engine, person("sdr"), person("ae"))
    make_router(engine, workspace["id"], make_path("p", "ae", match={"region": "emea"}))
    opened = engine.init_simple(
        workspace["id"],
        request_for(crmExplicits={"region": "emea", "guest_email": "wrong@example.test"}),
        source="test",
    )
    body = opened["routing"]["data"]
    assert body["crm_explicits"]["guest_email"] == "wrong@example.test"
    assert body["shadowed_explicit_keys"] == ["guest_email"]
    assert body["paths"][0]["matched_fields"] == {"region": "emea"}


def test_an_evaluation_of_a_workspace_that_does_not_exist_is_404(engine):
    with pytest.raises(HandoffNotFound):
        engine.init_simple("no-such-workspace", request_for(), source="test")


def test_an_evaluation_writes_one_routing_holding_the_paths_and_the_booker(store):
    engine = HandoffSchedulerEngine(store, clock=clock)
    workspace = make_workspace(engine, person("sdr"), person("ae"))
    make_router(engine, workspace["id"], make_path("emea", "ae", match={"region": "emea"}))
    opened = engine.init_simple(workspace["id"], request_for(), source="test")
    rows = store.list(ROUTING_COLLECTION)
    assert len(rows) == 1
    assert rows[0]["id"] == opened["routing_id"]
    assert rows[0]["data"]["state"] == "open"
    assert rows[0]["data"]["booker_ref"] == "sdr"
    assert rows[0]["data"]["request_type"] == "GuestEmailRequest"
    assert rows[0]["data"]["paths"][0]["start_times"]


def test_the_routing_view_reports_the_gate_set_for_each_stored_path(store):
    engine = HandoffSchedulerEngine(store, clock=clock)
    workspace = make_workspace(engine, person("sdr"), person("ae"), person("se"))
    make_router(
        engine,
        workspace["id"],
        make_path(
            "p",
            "ae",
            match={"region": "emea"},
            invitees=[{"user_ref": "se", "required": True, "name": "SE"}],
        ),
    )
    opened = engine.init_simple(workspace["id"], request_for(), source="test")
    view = engine.routing_view(opened["routing"])
    assert view["paths"][0]["gating_user_ids"] == ["ae", "se"]
    assert view["paths"][0]["ignored_user_ids"] == []


# --------------------------------------------------------------------------- #
# Step 4: booking
# --------------------------------------------------------------------------- #


def test_booking_records_the_sdr_as_booker_and_the_ae_as_assignee(store):
    """ "meeting created with SDR as Booker and AE as Assignee"."""
    engine = HandoffSchedulerEngine(store, clock=clock)
    workspace = make_workspace(engine, person("sdr"), person("ae"))
    make_router(engine, workspace["id"], make_path("emea", "ae", match={"region": "emea"}))
    opened = engine.init_simple(workspace["id"], request_for(), source="test")
    taken = engine.schedule_simple(
        opened["routing_id"],
        str(opened["paths"][0]["router_ref"]),
        "emea",
        "sdr",
        {"startTime": opened["paths"][0]["start_times"][0]},
        source="test",
    )
    meeting = store.get(taken["meeting_id"])
    assert meeting["data"]["booker_ref"] == "sdr"
    assert meeting["data"]["booker_role"] == "booker"
    assert meeting["data"]["assignee_ref"] == "ae"
    assert meeting["data"]["assignee_role"] == "assignee"
    assert meeting["data"]["state"] == "confirmed"
    assert meeting["data"]["router_ref"] == opened["paths"][0]["router_ref"]
    assert meeting["data"]["path_id"] == "emea"


def test_booking_carries_the_paths_additional_invitees_onto_the_meeting(store):
    engine = HandoffSchedulerEngine(store, clock=clock)
    workspace = make_workspace(engine, person("sdr"), person("ae"), person("se"), person("mgr"))
    make_router(
        engine,
        workspace["id"],
        make_path(
            "emea",
            "ae",
            match={"region": "emea"},
            invitees=[
                {"user_ref": "se", "required": True},
                {"user_ref": "mgr", "required": False},
            ],
        ),
    )
    opened = engine.init_simple(workspace["id"], request_for(), source="test")
    taken = engine.schedule_simple(
        opened["routing_id"],
        str(opened["paths"][0]["router_ref"]),
        "emea",
        "sdr",
        {"startTime": opened["paths"][0]["start_times"][0]},
        source="test",
    )
    invitees = {row["user_ref"]: row for row in taken["invitees"]}
    assert set(invitees) == {"se", "mgr"}
    assert invitees["se"]["gated_the_window"] is True
    assert invitees["mgr"]["gated_the_window"] is False


def test_booking_closes_the_routing_and_the_two_writes_land_together(store):
    engine = HandoffSchedulerEngine(store, clock=clock)
    workspace = make_workspace(engine, person("sdr"), person("ae"))
    make_router(engine, workspace["id"], make_path("emea", "ae", match={"region": "emea"}))
    opened = engine.init_simple(workspace["id"], request_for(), source="test")
    engine.schedule_simple(
        opened["routing_id"],
        str(opened["paths"][0]["router_ref"]),
        "emea",
        "sdr",
        {"startTime": opened["paths"][0]["start_times"][0]},
        source="test",
    )
    routing = store.get(opened["routing_id"])
    assert routing["data"]["state"] == "booked"
    assert routing["data"]["booked_path_id"] == "emea"
    assert routing["data"]["meeting_ref"]
    assert store.db.audit_count(collection=MEETING_COLLECTION) == 1


def test_a_routing_cannot_be_booked_twice(store):
    engine = HandoffSchedulerEngine(store, clock=clock)
    workspace = make_workspace(engine, person("sdr"), person("ae"))
    make_router(engine, workspace["id"], make_path("emea", "ae", match={"region": "emea"}))
    opened = engine.init_simple(workspace["id"], request_for(), source="test")
    router_ref = str(opened["paths"][0]["router_ref"])
    start = opened["paths"][0]["start_times"][0]
    engine.schedule_simple(
        opened["routing_id"], router_ref, "emea", "sdr", {"startTime": start}, source="test"
    )
    with pytest.raises(HandoffConflict) as caught:
        engine.schedule_simple(
            opened["routing_id"], router_ref, "emea", "sdr", {"startTime": start}, source="test"
        )
    assert "cannot be booked again" in str(caught.value)
    assert len(store.list(MEETING_COLLECTION)) == 1


def test_the_booker_cannot_change_between_init_and_schedule(store):
    """The meeting is booked with the SDR who opened the Handoff scheduler."""
    engine = HandoffSchedulerEngine(store, clock=clock)
    workspace = make_workspace(engine, person("sdr-a"), person("sdr-b"), person("ae"))
    make_router(engine, workspace["id"], make_path("emea", "ae", match={"region": "emea"}))
    opened = engine.init_simple(workspace["id"], request_for(booker_ref="sdr-a"), source="test")
    with pytest.raises(HandoffConflict) as caught:
        engine.schedule_simple(
            opened["routing_id"],
            str(opened["paths"][0]["router_ref"]),
            "emea",
            "sdr-b",
            {"startTime": opened["paths"][0]["start_times"][0]},
            source="test",
        )
    assert "was opened by sdr-a" in str(caught.value)


def test_a_router_the_routing_never_consulted_is_refused(store):
    engine = HandoffSchedulerEngine(store, clock=clock)
    workspace = make_workspace(engine, person("sdr"), person("ae"))
    make_router(
        engine, workspace["id"], make_path("emea", "ae", match={"region": "emea"}), name="R"
    )
    other = make_router(
        engine, workspace["id"], make_path("emea", "ae", match={"region": "emea"}), name="S"
    )
    opened = engine.init_simple(workspace["id"], request_for(router_ref=None), source="test")
    with pytest.raises(HandoffConflict):
        engine.schedule_simple(
            opened["routing_id"],
            "no-such-router",
            "emea",
            "sdr",
            {"startTime": opened["paths"][0]["start_times"][0]},
            source="test",
        )
    assert other["data"]["name"] == "S"


def test_a_path_the_router_does_not_declare_is_refused_naming_the_ones_it_does(store):
    engine = HandoffSchedulerEngine(store, clock=clock)
    workspace = make_workspace(engine, person("sdr"), person("ae"))
    make_router(engine, workspace["id"], make_path("emea", "ae", match={"region": "emea"}))
    opened = engine.init_simple(workspace["id"], request_for(), source="test")
    with pytest.raises(HandoffError) as caught:
        engine.schedule_simple(
            opened["routing_id"],
            str(opened["paths"][0]["router_ref"]),
            "nope",
            "sdr",
            {"startTime": opened["paths"][0]["start_times"][0]},
            source="test",
        )
    assert "not declared on router" in str(caught.value)
    assert "emea" in str(caught.value)


def test_a_booking_must_name_a_slot_the_path_offered(store):
    engine = HandoffSchedulerEngine(store, clock=clock)
    workspace = make_workspace(engine, person("sdr"), person("ae"))
    make_router(engine, workspace["id"], make_path("emea", "ae", match={"region": "emea"}))
    opened = engine.init_simple(workspace["id"], request_for(), source="test")
    with pytest.raises(HandoffError) as caught:
        engine.schedule_simple(
            opened["routing_id"],
            str(opened["paths"][0]["router_ref"]),
            "emea",
            "sdr",
            {"startTime": "2026-03-02T09:15:00+00:00"},
            source="test",
        )
    assert "not on offer" in str(caught.value)


def test_a_booking_must_name_a_start_time(store):
    engine = HandoffSchedulerEngine(store, clock=clock)
    workspace = make_workspace(engine, person("sdr"), person("ae"))
    make_router(engine, workspace["id"], make_path("emea", "ae", match={"region": "emea"}))
    opened = engine.init_simple(workspace["id"], request_for(), source="test")
    with pytest.raises(HandoffError) as caught:
        engine.schedule_simple(
            opened["routing_id"],
            str(opened["paths"][0]["router_ref"]),
            "emea",
            "sdr",
            {},
            source="test",
        )
    assert "startTime is required" in str(caught.value)


def test_a_booking_refuses_a_guest_the_routing_was_not_opened_for(store):
    engine = HandoffSchedulerEngine(store, clock=clock)
    workspace = make_workspace(engine, person("sdr"), person("ae"))
    make_router(engine, workspace["id"], make_path("emea", "ae", match={"region": "emea"}))
    opened = engine.init_simple(workspace["id"], request_for(), source="test")
    with pytest.raises(HandoffError) as caught:
        engine.schedule_simple(
            opened["routing_id"],
            str(opened["paths"][0]["router_ref"]),
            "emea",
            "sdr",
            {"startTime": opened["paths"][0]["start_times"][0], "guestEmail": "other@example.test"},
            source="test",
        )
    assert "opened for" in str(caught.value)


def test_an_ae_who_took_the_slot_in_between_is_a_conflict_naming_them(store):
    """The gate re-check. There is nobody else on the path to advance to."""
    engine = HandoffSchedulerEngine(store, clock=clock)
    workspace = make_workspace(engine, person("sdr"), person("ae"))
    make_router(engine, workspace["id"], make_path("emea", "ae", match={"region": "emea"}))
    opened = engine.init_simple(workspace["id"], request_for(), source="test")
    start = opened["paths"][0]["start_times"][0]
    end = opened["paths"][0]["window"]["slots"][0]["end_at"]

    # The AE takes another meeting after the routing opened.
    store.update(workspace["id"], {"users": [person("sdr"), busy("ae", start, end)]})
    with pytest.raises(HandoffConflict) as caught:
        engine.schedule_simple(
            opened["routing_id"],
            str(opened["paths"][0]["router_ref"]),
            "emea",
            "sdr",
            {"startTime": start},
            source="test",
        )
    assert "now taken by ae" in str(caught.value)
    assert store.list(MEETING_COLLECTION) == []


def test_a_required_invitee_who_took_the_slot_in_between_is_also_a_conflict(store):
    engine = HandoffSchedulerEngine(store, clock=clock)
    workspace = make_workspace(engine, person("sdr"), person("ae"), person("se"))
    make_router(
        engine,
        workspace["id"],
        make_path(
            "emea", "ae", match={"region": "emea"}, invitees=[{"user_ref": "se", "required": True}]
        ),
    )
    opened = engine.init_simple(workspace["id"], request_for(), source="test")
    start = opened["paths"][0]["start_times"][0]
    end = opened["paths"][0]["window"]["slots"][0]["end_at"]
    store.update(workspace["id"], {"users": [person("sdr"), person("ae"), busy("se", start, end)]})
    with pytest.raises(HandoffConflict) as caught:
        engine.schedule_simple(
            opened["routing_id"],
            str(opened["paths"][0]["router_ref"]),
            "emea",
            "sdr",
            {"startTime": start},
            source="test",
        )
    assert "now taken by se" in str(caught.value)


def test_an_assignee_whose_calendar_was_disconnected_after_the_routing_opened_is_refused(store):
    """A workspace is edited after the router that reads it, and booking is the gate."""
    engine = HandoffSchedulerEngine(store, clock=clock)
    workspace = make_workspace(engine, person("sdr"), person("ae"))
    make_router(engine, workspace["id"], make_path("emea", "ae", match={"region": "emea"}))
    opened = engine.init_simple(workspace["id"], request_for(), source="test")
    store.update(
        workspace["id"], {"users": [person("sdr"), person("ae", calendar_connected=False)]}
    )
    with pytest.raises(HandoffConflict) as caught:
        engine.schedule_simple(
            opened["routing_id"],
            str(opened["paths"][0]["router_ref"]),
            "emea",
            "sdr",
            {"startTime": opened["paths"][0]["start_times"][0]},
            source="test",
        )
    assert "no connected calendar" in str(caught.value)


def test_booking_against_a_routing_that_does_not_exist_is_404(engine):
    with pytest.raises(HandoffNotFound):
        engine.schedule_simple(
            "no-such-routing", "r", "p", "sdr", {"startTime": "x"}, source="test"
        )


def test_two_sdrs_can_book_two_slots_from_one_routing_for_two_paths(store):
    """The researched call is per path, so one routing can carry two handoffs."""
    engine = HandoffSchedulerEngine(store, clock=clock)
    workspace = make_workspace(engine, person("sdr"), person("ae-a"), person("ae-b"))
    make_router(
        engine,
        workspace["id"],
        make_path("first", "ae-a", match={"region": "emea"}),
        make_path("second", "ae-b", match={"region": "emea"}),
    )
    opened = engine.init_simple(workspace["id"], request_for(), source="test")
    router_ref = str(opened["paths"][0]["router_ref"])
    engine.schedule_simple(
        opened["routing_id"],
        router_ref,
        "first",
        "sdr",
        {"startTime": opened["paths"][0]["start_times"][0]},
        source="test",
    )
    with pytest.raises(HandoffConflict) as caught:
        engine.schedule_simple(
            opened["routing_id"],
            router_ref,
            "second",
            "sdr",
            {"startTime": opened["paths"][1]["start_times"][0]},
            source="test",
        )
    assert "cannot be booked again" in str(caught.value)


# --------------------------------------------------------------------------- #
# Meetings
# --------------------------------------------------------------------------- #


def test_cancelling_a_meeting_marks_it_cancelled_and_leaves_the_routing_booked(store):
    """The slot was taken, so re-opening the routing would let it be taken twice."""
    engine = HandoffSchedulerEngine(store, clock=clock)
    workspace = make_workspace(engine, person("sdr"), person("ae"))
    make_router(engine, workspace["id"], make_path("emea", "ae", match={"region": "emea"}))
    opened = engine.init_simple(workspace["id"], request_for(), source="test")
    taken = engine.schedule_simple(
        opened["routing_id"],
        str(opened["paths"][0]["router_ref"]),
        "emea",
        "sdr",
        {"startTime": opened["paths"][0]["start_times"][0]},
        source="test",
    )
    engine.cancel_meeting(taken["meeting_id"], source="test")
    assert store.get(taken["meeting_id"])["data"]["state"] == "cancelled"
    assert store.get(opened["routing_id"])["data"]["state"] == "booked"


def test_cancelling_twice_is_a_conflict(store):
    engine = HandoffSchedulerEngine(store, clock=clock)
    workspace = make_workspace(engine, person("sdr"), person("ae"))
    make_router(engine, workspace["id"], make_path("emea", "ae", match={"region": "emea"}))
    opened = engine.init_simple(workspace["id"], request_for(), source="test")
    taken = engine.schedule_simple(
        opened["routing_id"],
        str(opened["paths"][0]["router_ref"]),
        "emea",
        "sdr",
        {"startTime": opened["paths"][0]["start_times"][0]},
        source="test",
    )
    engine.cancel_meeting(taken["meeting_id"], source="test")
    with pytest.raises(HandoffConflict):
        engine.cancel_meeting(taken["meeting_id"], source="test")


def test_cancelling_a_meeting_that_does_not_exist_is_404(engine):
    with pytest.raises(HandoffNotFound):
        engine.cancel_meeting("no-such-meeting", source="test")


def test_the_meetings_list_filters_by_assignee_booker_and_state(store):
    engine = HandoffSchedulerEngine(store, clock=clock)
    workspace = make_workspace(engine, person("sdr"), person("ae-a"), person("ae-b"))
    make_router(
        engine,
        workspace["id"],
        make_path("first", "ae-a", match={"region": "emea"}),
        make_path("second", "ae-b", match={"region": "emea"}),
    )
    opened = engine.init_simple(workspace["id"], request_for(), source="test")
    router_ref = str(opened["paths"][0]["router_ref"])
    first = engine.schedule_simple(
        opened["routing_id"],
        router_ref,
        "first",
        "sdr",
        {"startTime": opened["paths"][0]["start_times"][0]},
        source="test",
    )
    second = engine.init_simple(
        workspace["id"], request_for(guestEmail="b@example.test"), source="test"
    )
    taken = engine.schedule_simple(
        second["routing_id"],
        router_ref,
        "second",
        "sdr",
        {"startTime": second["paths"][0]["start_times"][0]},
        source="test",
    )
    engine.cancel_meeting(taken["meeting_id"], source="test")

    assert len(engine.meetings()) == 2
    assert len(engine.meetings(assignee_ref="ae-a")) == 1
    assert engine.meetings(assignee_ref=str(first["meeting_id"])) == []
    assert len(engine.meetings(booker_ref="sdr")) == 2
    assert engine.meetings(booker_ref="nobody") == []
    assert len(engine.meetings(state="confirmed")) == 1
    assert len(engine.meetings(state="cancelled")) == 1


# --------------------------------------------------------------------------- #
# The reads for the page
# --------------------------------------------------------------------------- #


def test_the_catalog_lists_the_supported_link_type_and_the_gate_sets(store):
    engine = HandoffSchedulerEngine(store, clock=clock)
    workspace = make_workspace(engine, person("sdr"), person("ae"), person("se"))
    make_router(
        engine,
        workspace["id"],
        make_path(
            "p", "ae", match={"region": "emea"}, invitees=[{"user_ref": "se", "required": False}]
        ),
    )
    catalog = engine.catalog()
    assert [entry["link_type"] for entry in catalog["link_types"]][0] == "Handoff"
    assert catalog["workspaces"][0]["summary"]["users"] == 3
    assert catalog["routers"][0]["paths"][0]["ignored_user_ids"] == ["se"]


def test_the_summary_counts_the_states_the_page_shows(store):
    engine = HandoffSchedulerEngine(store, clock=clock)
    workspace = make_workspace(
        engine, person("sdr"), person("ae"), busy("busy", INTERVAL["start"], INTERVAL["end"])
    )
    make_router(
        engine,
        workspace["id"],
        make_path("open", "ae", match={"region": "emea"}),
        make_path("blocked", "busy", match={"region": "emea"}),
    )
    opened = engine.init_simple(workspace["id"], request_for(), source="test")
    engine.schedule_simple(
        opened["routing_id"],
        str(opened["paths"][0]["router_ref"]),
        "open",
        "sdr",
        {"startTime": opened["paths"][0]["start_times"][0]},
        source="test",
    )
    summary = engine.summary()
    assert summary["workspaces"] == 1
    assert summary["users"] == 3
    assert summary["routers"] == 1
    assert summary["paths"] == 2
    assert summary["routings"] == 1
    assert summary["routings_open"] == 0
    assert summary["routings_by_outcome"] == {"paths_offered": 1}
    assert summary["meetings_confirmed"] == 1
    assert summary["collections"] == {
        "workspace": WORKSPACE_COLLECTION,
        "router": ROUTER_COLLECTION,
        "routing": ROUTING_COLLECTION,
        "meeting": MEETING_COLLECTION,
    }


def test_the_summary_of_an_empty_database_is_all_zeroes_rather_than_an_error(store):
    """Iterating an empty list must not make the counts wrong; it was a real bug."""
    summary = HandoffSchedulerEngine(store, clock=clock).summary()
    assert summary["workspaces"] == 0
    assert summary["paths"] == 0
    assert summary["routings_by_outcome"] == {}
    assert summary["gated_and_ignored_users"] == 0


def test_the_summary_counts_the_ignored_invitees_as_well_as_the_gate_set(store):
    """A reader asking why a path was empty needs to see whose calendar was not read."""
    engine = HandoffSchedulerEngine(store, clock=clock)
    workspace = make_workspace(engine, person("sdr"), person("ae"), person("se"), person("mgr"))
    make_router(
        engine,
        workspace["id"],
        make_path(
            "p",
            "ae",
            match={"region": "emea"},
            invitees=[{"user_ref": "se", "required": True}, {"user_ref": "mgr", "required": False}],
        ),
    )
    assert engine.summary()["gated_and_ignored_users"] == 3


def test_the_routings_list_filters_by_workspace_state_and_outcome(store):
    engine = HandoffSchedulerEngine(store, clock=clock)
    first = make_workspace(engine, person("sdr"), person("ae"), name="A")
    second = make_workspace(
        engine, person("sdr"), busy("busy", INTERVAL["start"], INTERVAL["end"]), name="B"
    )
    make_router(engine, first["id"], make_path("p", "ae", match={"region": "emea"}), name="RA")
    make_router(engine, second["id"], make_path("p", "busy", match={"region": "emea"}), name="RB")
    engine.init_simple(first["id"], request_for(), source="test")
    engine.init_simple(second["id"], request_for(), source="test")
    assert len(engine.routings()) == 2
    assert len(engine.routings(workspace_ref=first["id"])) == 1
    assert len(engine.routings(state="open")) == 2
    assert engine.routings(state="booked") == []
    assert len(engine.routings(outcome="no_availability")) == 1


# --------------------------------------------------------------------------- #
# The inferences registry
# --------------------------------------------------------------------------- #


def test_the_derivation_is_recorded_with_its_jev_audit_id_and_verdict():
    entry = next(
        e
        for e in describe()["inferences"]
        if e["id"] == "inference_path_availability_is_intersection"
    )
    assert entry["jev_audit_id"] == "jev-20261004T065905-27100-45006"
    assert entry["jev_verdict"] == "pass"
    assert entry["jev_selected"] == "intersection_with_gate_recheck"
    assert "intersection" in entry["decision"]
    assert entry["change_if"]


def test_every_inference_names_the_four_research_open_questions_the_issue_lists():
    """The issue quotes four decisions the research left open. Each has an entry."""
    ids = set(describe()["ids"])
    for required in (
        "inference_required_invitee_narrows_the_path",
        "inference_crm_explicits_cannot_shadow_the_researched_fields",
        "inference_this_plugin_does_not_own_reassignment",
        "inference_one_workspace_is_one_pod",
    ):
        assert required in ids, required


def test_every_inference_carries_a_decision_a_reason_and_a_change():
    for entry in describe()["inferences"]:
        assert entry["decision"], entry["id"]
        assert entry["why"], entry["id"]
        assert entry["change_if"], entry["id"]


def test_the_registry_ids_are_unique():
    ids = [entry["id"] for entry in describe()["inferences"]]
    assert len(ids) == len(set(ids))


def test_by_id_returns_one_entry_or_none():
    from dsr.handoff_scheduler import inferences as module

    assert module.by_id("inference_one_workspace_is_one_pod")["topic"] == "pod partition"
    assert module.by_id("no-such-inference") is None


def test_the_domain_package_never_opens_the_database_or_imports_the_app():
    """The domain module imports nothing but the store.

    An enforced grep exists for the feature modules; this asserts the same rule for
    the package itself, because ``AuditedDatabase`` reaching in here would mean a
    write path outside the audited transaction the product guarantee rests on.
    """
    for module in sorted(PACKAGE.glob("*.py")):
        text = module.read_text(encoding="utf-8")
        assert "from dsr.api" not in text, module.name
        assert "import dsr.api" not in text, module.name
        assert "import sqlite3" not in text, module.name
        assert "sqlite3.connect" not in text, module.name
        assert "AuditedDatabase(" not in text, module.name


def test_the_handoff_inferences_module_is_the_one_the_registry_serves():
    assert handoff_inferences.describe()["count"] == len(describe()["inferences"])
