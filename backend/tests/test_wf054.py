"""WF-054: distribute bookings across a team by round robin. Domain tests.

These drive :mod:`dsr.round_robin` over a temporary database, with a clock the
test controls, so every assertion about a slot time, a cursor or a cycle is
about a fixed instant rather than about whatever time the suite happened to run.

The HTTP surface has its own file, ``test_wf054_http.py``. Both pass when run on
their own, which matters because the suite runs under ``pytest-xdist`` and a test
that only passes in one order fails intermittently on a busy runner.

What is asserted here, and why
------------------------------

* The researched sentences. Each behaviour is traced to the quote that fixes it,
  so a test failure says which research the build stopped honouring.
* The union derivation. The research names union/intersection without saying
  which, so the derivation in ``availability`` is a decision that can be
  disagreed with, and the tests below pin what it decided.
* The license gate as exclusion, not warning. A test that only checked a warning
  would pass against the softer behaviour the research forbids.
* Four bugs written to be caught, each of which shipped in some form during this
  build: the ``data`` envelope being read off a body that already was one, an
  error class whose ``__init__`` returned a string, the license gate being
  skipped when the window happened to be empty, and the summary counting excluded
  members by iterating an empty list.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from dsr.round_robin import (
    NoEligibleMember,
    RoundRobinConflict,
    RoundRobinEngine,
    RoundRobinError,
    RoundRobinNotFound,
    advance,
    apply_consumption,
    apply_return,
    combined_window,
    describe,
    exclusion_reason,
    explain_missing,
    free_members_at,
    free_minutes,
    grid,
    iso,
    ledger,
    member_id,
    next_candidate,
    normalise_interval,
    overlaps,
    parse,
    published_vocabulary,
    select_member,
    shares,
    summarise,
    validate_team,
    weight_of,
)
from dsr.round_robin.credits import totals as ledger_totals
from dsr.round_robin.engine import (
    BOOKING_COLLECTION,
    CREDIT_MOVEMENT_COLLECTION,
    DISTRIBUTION_COLLECTION,
    NO_SHOW_COLLECTION,
    ROUTE_COLLECTION,
    TEAM_COLLECTION,
)

#: A fixed instant. Every test that reads a slot time derives it from here, so
#: the assertions are about the arithmetic rather than about the wall clock.
NOW = datetime(2026, 3, 2, 8, 0, tzinfo=timezone.utc)

#: The interval every test uses: five working days from Monday 09:00, in 30
#: minute steps.
INTERVAL = {
    "start": "2026-03-02T09:00:00+00:00",
    "end": "2026-03-06T17:00:00+00:00",
    "duration_minutes": 30,
    "min_notice_minutes": 0,
    "max_days": 14,
}


def clock() -> datetime:
    """The engine's clock, fixed at :data:`NOW`."""
    return NOW


@pytest.fixture
def engine(store):
    """An engine over an empty database, with a fixed clock."""
    return RoundRobinEngine(store, clock=clock)


def member(identifier: str, **extra):
    """One team member, licensed and connected unless the test says otherwise."""
    return {
        "member_id": identifier,
        "name": identifier.upper(),
        "email": f"{identifier}@example.test",
        "licensed": extra.pop("licensed", True),
        "calendar_connected": extra.pop("calendar_connected", True),
        "busy": extra.pop("busy", []),
        **extra,
    }


def busy(identifier: str, start: str, end: str, **extra):
    """A member with one busy block."""
    return member(identifier, busy=[{"start": start, "end": end}], **extra)


def make_team(engine, *members, name="Demo team", room_id=None):
    """Declare a team from member rows."""
    return engine.create_team(
        {"name": name, "members": list(members), "room_id": room_id},
        actor="dana",
        source="test",
    )


def make_distribution(engine, team_id, *, mode="strict", **extra):
    """Declare a distribution over a team."""
    payload = {
        "name": f"{mode} on {team_id}",
        "mode": mode,
        "team_ref": team_id,
        "interval": INTERVAL,
    }
    payload.update(extra)
    return engine.create_distribution(payload, actor="dana", source="test")


# --------------------------------------------------------------------------- #
# The researched sentences
# --------------------------------------------------------------------------- #


def test_the_modes_are_the_two_the_research_names_and_not_interchangeable():
    """ "either strict (equal turns) or flexible (weighted by availability)"."""
    published = published_vocabulary()
    names = published["mode_names"]
    assert names == ["strict", "flexible"]
    for entry in published["modes"]:
        assert entry["sourced_from"], entry
        assert entry["source"] == "chilipiper_concierge_flow"


def test_every_published_term_is_sourced_or_flagged_as_an_inference():
    """No term may quietly acquire a quotation it does not have.

    The distinction the brief insists on is carried on the entries themselves, so
    this walks the whole published vocabulary rather than a hand-picked sample.
    """
    published = published_vocabulary()
    sourced_blocks = [
        published["link_types"],
        published["modes"],
        published["credit_directions"],
        published["route_states"],
        published["booking_statuses"],
        published["evaluation_outcomes"],
        published["edge_api_calls"],
    ]
    for entries in sourced_blocks:
        for entry in entries:
            assert entry.get("sourced_from"), entry
            assert entry.get("source"), entry

    # The four constants that are this build's own carry an inference id instead.
    for block in ("calendar_combination", "license_gate", "credit_back_flag", "reuse_distribution"):
        entry = published[block]
        assert entry.get("inference") or entry.get("sourced_from"), block
        if entry.get("inference"):
            assert entry["inference"] in {i["id"] for i in describe()["inferences"]}, block


def test_the_license_gate_is_a_hard_gate_and_not_a_warning():
    """ "if any prospects match to an unlicensed user, they will not be able to book".

    The research gives a consequence, not a caution. Publishing it as
    ``is_a_warning: False`` is what stops a client rendering it as an amber note.
    """
    gate = published_vocabulary()["license_gate"]
    assert gate["is_a_warning"] is False
    assert "Not Scheduled" in gate["researched_effect"]
    assert gate["source"] == "chilipiper_concierge_flow"


def test_the_credit_ledger_has_exactly_two_directions_and_the_research_names_both():
    """ "credit consumed on the selected member (or credited back on no-show)"."""
    directions = {
        entry["direction"]: entry for entry in published_vocabulary()["credit_directions"]
    }
    assert set(directions) == {"consumed", "returned"}
    assert directions["consumed"]["amount"] == 1
    assert directions["returned"]["amount"] == 1


def test_the_no_show_credit_back_is_a_standing_rule_not_an_ad_hoc_correction():
    """ "the Meeting Type flag ... makes it a standing rule"."""
    flag = published_vocabulary()["credit_back_flag"]
    assert flag["standing_rule"] is True
    assert flag["default"] is False


def test_the_credit_ledger_is_the_distributions_state_and_not_the_bookings(store):
    """The issue forbids burying the weights and credits inside the booking.

    Asserted against the stored rows rather than against a written list, because
    the failure this guards against is a ledger that quietly moves onto the
    booking, and a booking that quietly copies one would look identical from the
    outside until the next reassignment.
    """
    engine = RoundRobinEngine(store, clock=clock)
    team = make_team(engine, member("a"), member("b"))
    distribution = make_distribution(engine, team["id"], credit_back_on_no_show=True)
    opened = engine.init_simple(distribution["id"], {"guestEmail": "p@example.test"}, source="test")
    taken = engine.book(
        str(opened["routing_id"]),
        {"startTime": opened["start_times"][0]},
        source="test",
    )

    booking = store.get(str(taken["booking_id"]))
    assert booking is not None
    assert "credits" not in booking["data"], "the ledger must not live on the booking"
    assert booking["data"]["distribution_ref"] == distribution["id"]

    distribution_after = store.get(distribution["id"])
    assert distribution_after is not None
    assert distribution_after["data"]["credits"][taken["member_id"]]["credits_consumed"] == 1


def test_the_distribution_is_a_reusable_asset_one_team_under_two_distributions(store):
    """ "Distributions are reusable assets independent of the router".

    One team, two distributions with different weights and different cursors. If
    the weights lived on the member row, the second distribution would inherit
    the first one's rotation.
    """
    engine = RoundRobinEngine(store, clock=clock)
    team = make_team(engine, member("a"), member("b"))
    strict = make_distribution(engine, team["id"], mode="strict")
    weighted = make_distribution(
        engine,
        team["id"],
        mode="flexible",
        members=[{"member_id": "a", "weight": 5.0}, {"member_id": "b", "weight": 1.0}],
    )

    assert strict["data"]["mode"] == "strict"
    assert weighted["data"]["mode"] == "flexible"
    assert weighted["data"]["members"] == [
        {"member_id": "a", "weight": 5.0},
        {"member_id": "b", "weight": 1.0},
    ]

    # Book against the strict one only, and the flexible one must be untouched.
    opened = engine.init_simple(strict["id"], {"guestEmail": "p@example.test"}, source="test")
    engine.book(str(opened["routing_id"]), {"startTime": opened["start_times"][0]}, source="test")
    assert store.get(strict["id"])["data"]["credits"]
    assert store.get(weighted["id"])["data"]["credits"] == {}


# --------------------------------------------------------------------------- #
# timeutil
# --------------------------------------------------------------------------- #


def test_a_naive_timestamp_is_read_as_utc_not_as_the_hosts_local_zone():
    """The same distribution must offer the same slots on two machines."""
    assert parse("2026-03-02T09:00:00") == parse("2026-03-02T09:00:00Z")
    assert parse("2026-03-02T09:00:00").tzinfo is not None
    assert parse("2026-03-02T09:00:00+02:00") == datetime(2026, 3, 2, 7, 0, tzinfo=timezone.utc)


def test_a_timestamp_is_required():
    with pytest.raises(ValueError):
        parse("")


def test_overlaps_is_half_open_so_back_to_back_meetings_do_not_collide():
    """ "a 09:00-09:30 and a 09:30-10:00 meeting do not overlap"."""
    start = parse("2026-03-02T09:00:00+00:00")
    end = parse("2026-03-02T09:30:00+00:00")
    later = parse("2026-03-02T09:30:00+00:00")
    assert overlaps(start, end, start, end) is True
    assert overlaps(start, end, later, later + timedelta(minutes=30)) is False


def test_free_minutes_subtracts_overlapping_busy_blocks_once():
    """A rep who booked two overlapping meetings has one busy period, not two."""
    start = parse("2026-03-02T09:00:00+00:00")
    end = parse("2026-03-02T10:00:00+00:00")
    blocks = [
        (parse("2026-03-02T09:00:00+00:00"), parse("2026-03-02T09:45:00+00:00")),
        (parse("2026-03-02T09:15:00+00:00"), parse("2026-03-02T09:30:00+00:00")),
    ]
    assert free_minutes(blocks, start, end) == 15


def test_free_minutes_clips_a_block_that_runs_past_the_interval():
    start = parse("2026-03-02T09:00:00+00:00")
    end = parse("2026-03-02T10:00:00+00:00")
    blocks = [(parse("2026-03-02T08:00:00+00:00"), parse("2026-03-02T11:00:00+00:00"))]
    assert free_minutes(blocks, start, end) == 0


def test_free_minutes_of_an_empty_window_is_zero():
    start = parse("2026-03-02T09:00:00+00:00")
    assert free_minutes([], start, start) == 0


def test_the_slot_grid_is_aligned_to_the_interval_start_not_to_midnight():
    """Aligning to midnight would drop the first part of a 09:30 interval."""
    start = parse("2026-03-02T09:30:00+00:00")
    end = parse("2026-03-02T10:30:00+00:00")
    slots = grid(start, end, 30, 10)
    assert [iso(s) for s, _ in slots] == [
        "2026-03-02T09:30:00+00:00",
        "2026-03-02T10:00:00+00:00",
    ]


def test_the_slot_grid_emits_no_slot_that_does_not_fit_inside_the_interval():
    start = parse("2026-03-02T09:00:00+00:00")
    end = parse("2026-03-02T10:10:00+00:00")
    slots = grid(start, end, 30, 10)
    assert all(close <= end for _, close in slots)
    assert len(slots) == 2


def test_the_slot_grid_is_bounded():
    start = parse("2026-03-02T09:00:00+00:00")
    end = start + timedelta(days=30)
    assert len(grid(start, end, 15, 5)) == 5


def test_a_non_positive_duration_is_refused():
    start = parse("2026-03-02T09:00:00+00:00")
    with pytest.raises(ValueError):
        grid(start, start + timedelta(hours=1), 0, 5)


# --------------------------------------------------------------------------- #
# The interval
# --------------------------------------------------------------------------- #


def test_the_interval_defaults_to_a_window_from_now_when_the_caller_names_none():
    normalised = normalise_interval({})
    assert normalised["duration_minutes"] == 30
    assert parse(normalised["end"]) > parse(normalised["start"])


def test_the_interval_clamps_a_max_days_beyond_the_bound():
    """The interval arrives in a request body and an unbounded one is a hazard."""
    with pytest.raises(RoundRobinError) as caught:
        normalise_interval({"max_days": 500})
    assert "at most" in str(caught.value)


def test_the_interval_refuses_a_non_positive_duration_or_max_days():
    with pytest.raises(RoundRobinError):
        normalise_interval({"duration_minutes": 0})
    with pytest.raises(RoundRobinError):
        normalise_interval({"max_days": 0})


def test_the_interval_refuses_one_that_does_not_move_forward():
    with pytest.raises(RoundRobinError) as caught:
        normalise_interval(
            {"start": "2026-03-06T17:00:00+00:00", "end": "2026-03-02T09:00:00+00:00"}
        )
    assert "must end after it starts" in str(caught.value)


def test_the_interval_refuses_a_negative_minimum_notice():
    with pytest.raises(RoundRobinError):
        normalise_interval({"min_notice_minutes": -5})


# --------------------------------------------------------------------------- #
# Teams and the license gate
# --------------------------------------------------------------------------- #


def test_a_team_needs_a_name_and_at_least_one_member():
    with pytest.raises(RoundRobinError) as caught:
        validate_team({"members": [member("a")]})
    assert "needs a name" in str(caught.value)
    with pytest.raises(RoundRobinError) as caught:
        validate_team({"name": "T", "members": []})
    assert "at least one member" in str(caught.value)


def test_a_member_with_no_id_is_refused():
    """No weight, credit or decision could name a member with no id."""
    with pytest.raises(RoundRobinError) as caught:
        validate_team({"name": "T", "members": [{"name": "Nameless"}]})
    assert "member_id" in str(caught.value)


def test_two_members_sharing_an_id_are_refused():
    """The ledger would credit one rep for two bookings and skip a real person."""
    with pytest.raises(RoundRobinError) as caught:
        validate_team({"name": "T", "members": [member("a"), member("a")]})
    assert "twice" in str(caught.value)


def test_a_member_that_is_not_an_object_is_refused():
    with pytest.raises(RoundRobinError):
        validate_team({"name": "T", "members": ["just a string"]})


def test_an_absent_license_flag_means_licensed_but_an_explicit_false_is_respected():
    """An older importer's rows must not look like a licensing failure."""
    assert validate_team({"name": "T", "members": [{"member_id": "a"}]})["members"][0]["licensed"]
    team = validate_team({"name": "T", "members": [{"member_id": "a", "licensed": False}]})
    assert team["members"][0]["licensed"] is False


def test_an_absent_calendar_flag_means_connected_and_an_explicit_false_is_respected():
    assert validate_team({"name": "T", "members": [{"member_id": "a"}]})["members"][0][
        "calendar_connected"
    ]
    team = validate_team(
        {"name": "T", "members": [{"member_id": "a", "calendar_connected": False}]}
    )
    assert team["members"][0]["calendar_connected"] is False


def test_a_busy_block_that_cannot_be_read_is_refused_rather_than_dropped():
    """Dropping it would report a member free for a period they are busy."""
    for busy_value in (
        [{"start": "not-a-time", "end": "2026-03-02T10:00:00+00:00"}],
        [{"start": "2026-03-02T09:00:00+00:00"}],
        ["nope"],
        "not-a-list",
    ):
        with pytest.raises(RoundRobinError):
            validate_team({"name": "T", "members": [member("a", busy=busy_value)]})


def test_the_licensing_and_calendar_exclusions_are_reported_separately():
    """The fix for one is a licence purchase and for the other a connection."""
    assert exclusion_reason(member("a", licensed=False)) == "no Concierge license"
    assert exclusion_reason(member("b", calendar_connected=False)) == "calendar not connected"
    assert exclusion_reason(member("c")) is None


def test_the_eligibility_summary_counts_each_excluded_member():
    """Counts, not reasons, because a reason can repeat and a member cannot."""
    assert summarise([member("a"), member("b", licensed=False)]) == {
        "members": 2,
        "eligible": 1,
        "excluded": 1,
        "excluded_reasons": ["no Concierge license"],
    }


def test_a_member_with_no_id_yields_an_empty_key_so_lookups_never_match():
    """A nameless member is refused at validation; this guards the helper alone."""
    assert member_id({"name": "Nameless"}) == ""


def test_a_member_id_falls_back_to_its_email_when_it_has_no_member_id():
    """Two importers name the same field differently, and a weight must resolve."""
    assert member_id({"email": "someone@example.test"}) == "someone@example.test"
    assert member_id({"id": "from-an-envelope"}) == "from-an-envelope"


def test_an_unreadable_weight_is_refused_rather_than_treated_as_a_default():
    """Silently falling back to 1.0 would unweight a distribution an admin set."""
    with pytest.raises(RoundRobinError) as caught:
        weight_of(member("a"), {"a": {"weight": "heavy"}})
    assert "not a number" in str(caught.value)


def test_a_negative_weight_is_refused_wherever_it_is_declared():
    with pytest.raises(RoundRobinError) as caught:
        weight_of(member("a"), {"a": {"weight": -2.0}})
    assert "negative weight" in str(caught.value)
    with pytest.raises(RoundRobinError):
        weight_of({"member_id": "a", "weight": -1.0}, {})


def test_a_missing_weight_defaults_to_one_rather_than_excluding_the_member():
    """Defaulting to 0 would silently exclude members, which reads as licensing."""
    assert weight_of(member("a"), {}) == 1.0


def test_the_member_list_index_ignores_a_member_with_no_id():
    from dsr.round_robin.teams import validate_members

    assert list(validate_members([{"name": "Nameless"}, member("a")])) == ["a"]


def test_eligible_members_keeps_the_team_order_and_drops_the_excluded():
    """Order is load-bearing: it is the final tie-break in both modes."""
    from dsr.round_robin.teams import eligible_members

    rows = [member("a"), member("ghost", licensed=False), member("b")]
    assert [member_id(row) for row in eligible_members(rows)] == ["a", "b"]
    assert len(eligible_members(rows, ignore_availability=True)) == 2


# --------------------------------------------------------------------------- #
# The combined window: the derivation
# --------------------------------------------------------------------------- #


def test_the_window_is_a_union_so_a_time_one_member_is_free_is_on_offer():
    """The derivation Jev ratified: audit jev-20261004T045227-22564-47815."""
    window = combined_window(
        [
            busy("a", "2026-03-02T09:00:00+00:00", "2026-03-02T12:00:00+00:00"),
            member("b"),
        ],
        start=parse(INTERVAL["start"]),
        end=parse(INTERVAL["end"]),
        duration_minutes=30,
        now=NOW,
    )
    assert window["operation"] == "union"
    assert window["derivation"] == "inference_calendar_combination"
    assert window["slot_count"] > 0
    # The 09:00 slot is booked for a but free for b, so it is on offer, annotated
    # with b only.
    first = window["slots"][0]
    assert first["start_at"] == "2026-03-02T09:00:00+00:00"
    assert first["free_member_ids"] == ["b"]


def test_the_window_is_empty_when_every_licensed_member_is_busy():
    start = parse(INTERVAL["start"])
    end = parse(INTERVAL["end"])
    window = combined_window(
        [busy("a", start.isoformat(), end.isoformat())],
        start=start,
        end=end,
        duration_minutes=30,
        now=NOW,
    )
    assert window["slot_count"] == 0


def test_an_unlicensed_members_calendar_does_not_narrow_the_window():
    """Narrowing by someone who cannot take the meeting would be unsellable.

    The license gate excludes a member from assignment, so counting their busy
    time would offer a prospect a slot no eligible rep can hold.
    """
    start = parse(INTERVAL["start"])
    end = parse(INTERVAL["end"])
    window = combined_window(
        [
            busy("ghost", start.isoformat(), end.isoformat(), licensed=False),
            member("real"),
        ],
        start=start,
        end=end,
        duration_minutes=30,
        now=NOW,
    )
    assert window["slot_count"] > 0
    assert window["excluded_members"] == [{"member_id": "ghost", "reason": "no Concierge license"}]


def test_an_unconnected_members_free_time_is_not_counted_in_the_weighting():
    start = parse(INTERVAL["start"])
    end = parse(INTERVAL["end"])
    window = combined_window(
        [member("a"), member("b", calendar_connected=False)],
        start=start,
        end=end,
        duration_minutes=30,
        now=NOW,
    )
    assert window["free_minutes_by_member"] == {"a": int((end - start).total_seconds() // 60)}


def test_the_window_honours_the_minimum_notice():
    window = combined_window(
        [member("a")],
        start=parse(INTERVAL["start"]),
        end=parse(INTERVAL["end"]),
        duration_minutes=30,
        min_notice_minutes=120,
        now=NOW,
    )
    assert window["slots"][0]["start_at"] == "2026-03-02T11:00:00+00:00"


def test_the_window_refuses_a_range_that_does_not_move_forward():
    with pytest.raises(RoundRobinError):
        combined_window(
            [member("a")],
            start=parse(INTERVAL["end"]),
            end=parse(INTERVAL["start"]),
            duration_minutes=30,
            now=NOW,
        )


def test_a_requested_time_that_is_on_offer_says_so_rather_than_explaining():
    """The happy path through the explainer, so a caller can probe without booking."""
    assert explain_missing(
        {"slots": [{"start_at": "2026-03-02T09:00:00+00:00"}]}, "2026-03-02T09:00:00+00:00"
    ) == ("the requested time is available")


def test_a_time_past_the_end_of_the_interval_names_the_excluded_members():
    """ "nobody is free" is useless without saying who was excluded and why."""
    message = explain_missing(
        {
            "slots": [],
            "start": INTERVAL["start"],
            "end": INTERVAL["end"],
            "excluded_members": [{"member_id": "ghost", "reason": "no Concierge license"}],
        },
        "2026-03-06T16:00:00+00:00",
    )
    assert "excluded from assignment" in message
    assert "ghost" in message


def test_a_time_past_the_end_of_a_fully_eligible_team_names_the_interval():
    message = explain_missing(
        {"slots": [], "start": INTERVAL["start"], "end": INTERVAL["end"]},
        "2026-03-06T16:00:00+00:00",
    )
    assert "outside the interval" in message
    assert INTERVAL["start"] in message


def test_a_time_before_the_next_open_slot_points_at_that_slot():
    """Someone who typed 09:15 needs to be told where the nearest opening is."""
    message = explain_missing(
        {
            "slots": [
                {"start_at": "2026-03-02T09:30:00+00:00"},
                {"start_at": "2026-03-02T10:00:00+00:00"},
            ]
        },
        "2026-03-02T09:15:00+00:00",
    )
    assert "no licensed member is free" in message
    assert "2026-03-02T09:30:00+00:00" in message


def test_a_time_after_every_slot_is_told_the_interval_rather_than_a_dead_end():
    """Nothing follows it, so the message names the range rather than a next slot."""
    message = explain_missing(
        {
            "slots": [{"start_at": "2026-03-02T09:00:00+00:00"}],
            "start": INTERVAL["start"],
            "end": INTERVAL["end"],
        },
        "2026-03-02T09:15:00+00:00",
    )
    assert "falls outside the interval" in message
    assert INTERVAL["start"] in message and INTERVAL["end"] in message


def test_a_slot_is_annotated_with_the_members_free_at_that_instant():
    """That annotation is what the booking-time re-check reads."""
    members = [busy("a", "2026-03-02T09:00:00+00:00", "2026-03-02T10:00:00+00:00"), member("b")]
    window = combined_window(
        members,
        start=parse(INTERVAL["start"]),
        end=parse(INTERVAL["end"]),
        duration_minutes=30,
        now=NOW,
    )
    free = free_members_at(
        members, parse("2026-03-02T09:00:00+00:00"), parse("2026-03-02T09:30:00+00:00")
    )
    assert free == ["b"]
    assert window["slots"][0]["free_member_ids"] == free


def test_a_member_with_no_connected_calendar_is_never_free_at_a_slot():
    """Treating unreadable as free is how a prospect lands on a rep who declined."""
    members = [member("a", calendar_connected=False)]
    assert (
        free_members_at(
            members, parse("2026-03-02T09:00:00+00:00"), parse("2026-03-02T09:30:00+00:00")
        )
        == []
    )


# --------------------------------------------------------------------------- #
# Selection: the two modes
# --------------------------------------------------------------------------- #


def test_strict_rotates_by_equal_turns_ignoring_the_weights():
    """ "strict (equal turns)" -- a weight is a statement about capacity."""
    members = [member("a"), member("b")]
    distribution = {
        "mode": "strict",
        "credits": {},
        "members": [{"member_id": "a", "weight": 99.0}, {"member_id": "b", "weight": 0.01}],
    }
    window = {"free_minutes_by_member": {"a": 600, "b": 600}, "total_free_minutes": 1200}
    chosen = select_member(distribution, members, window)
    assert chosen["member_id"] == "a", "strict takes the first, not the heaviest"
    assert chosen["rule"] == "fewest credits consumed this cycle"


def test_strict_visits_every_member_before_revisiting_any(store):
    """The whole point of equal turns, asserted over a real rotation."""
    engine = RoundRobinEngine(store, clock=clock)
    team = make_team(engine, member("a"), member("b"), member("c"))
    distribution = make_distribution(engine, team["id"], mode="strict")
    taken = []
    for index in range(6):
        opened = engine.init_simple(
            distribution["id"], {"guestEmail": f"p{index}@example.test"}, source="test"
        )
        result = engine.book(
            str(opened["routing_id"]), {"startTime": opened["start_times"][0]}, source="test"
        )
        taken.append(result["member_id"])
    assert taken == ["a", "b", "c", "a", "b", "c"]


def test_flexible_weights_by_availability_so_the_freer_member_wins():
    """ "flexible (weighted by availability)" -- the weight is a volume."""
    members = [member("a"), member("b")]
    distribution = {"mode": "flexible", "credits": {}, "members": []}
    window = {"free_minutes_by_member": {"a": 900, "b": 100}, "total_free_minutes": 1000}
    chosen = select_member(distribution, members, window)
    assert chosen["member_id"] == "a"
    assert chosen["rule"] == "largest weight times free share"


def test_flexible_respects_the_declared_weight_over_raw_availability():
    """A weighted distribution is the admin's stated preference."""
    members = [member("a"), member("b")]
    distribution = {
        "mode": "flexible",
        "credits": {},
        "members": [{"member_id": "a", "weight": 1.0}, {"member_id": "b", "weight": 50.0}],
    }
    window = {"free_minutes_by_member": {"a": 900, "b": 100}, "total_free_minutes": 1000}
    assert select_member(distribution, members, window)["member_id"] == "b"


def test_flexible_never_chooses_a_member_with_no_free_time():
    """A share of zero is a share of zero, whatever the weight."""
    members = [member("a"), member("b")]
    distribution = {
        "mode": "flexible",
        "credits": {},
        "members": [{"member_id": "b", "weight": 1000.0}],
    }
    window = {"free_minutes_by_member": {"a": 600, "b": 0}, "total_free_minutes": 600}
    assert select_member(distribution, members, window)["member_id"] == "a"


def test_a_mode_outside_the_published_vocabulary_is_refused():
    with pytest.raises(RoundRobinError) as caught:
        select_member({"mode": "weighted"}, [member("a")], {"free_minutes_by_member": {}})
    assert "mode must be one of" in str(caught.value)


def test_the_tie_break_is_team_order_so_the_rotation_completes():
    """Without a final key, two members could be chosen repeatedly while two
    others were skipped, and "equal turns" would not hold."""
    members = [member("a"), member("b"), member("c")]
    distribution = {"mode": "strict", "credits": {}, "members": []}
    window = {"free_minutes_by_member": {}, "total_free_minutes": 0}
    assert select_member(distribution, members, window)["ranking"] == ["a", "b", "c"]

    # Equal credits and equal turns: order decides, not the row order of the dict.
    tied = {
        "mode": "strict",
        "credits": {"b": {"credits_consumed": 1, "turns_taken": 1}},
        "members": [],
    }
    assert select_member(tied, members, window)["member_id"] == "a"


def test_the_selection_reports_the_arithmetic_that_chose_the_member():
    """ "why did this prospect reach me" deserves the numbers that answered it."""
    members = [member("a"), member("b")]
    distribution = {"mode": "flexible", "credits": {}, "members": []}
    window = {"free_minutes_by_member": {"a": 750, "b": 250}, "total_free_minutes": 1000}
    scores = {
        score["member_id"]: score
        for score in select_member(distribution, members, window)["scores"]
    }
    assert scores["a"]["free_share"] == 0.75
    assert scores["b"]["free_share"] == 0.25
    assert scores["a"]["score"] == 0.75


def test_the_selection_skips_a_member_who_is_not_free_at_the_chosen_slot():
    """The union's re-check, applied at selection."""
    members = [member("a"), member("b")]
    distribution = {"mode": "strict", "credits": {}, "members": []}
    window = {"free_minutes_by_member": {"a": 600, "b": 600}, "total_free_minutes": 1200}
    assert select_member(distribution, members, window, free_member_ids=["b"])["member_id"] == "b"


def test_the_selection_refuses_when_nobody_is_free_at_the_chosen_slot():
    members = [member("a"), member("b")]
    distribution = {"mode": "strict", "credits": {}, "members": []}
    window = {"free_minutes_by_member": {"a": 600, "b": 600}, "total_free_minutes": 1200}
    with pytest.raises(NoEligibleMember) as caught:
        select_member(distribution, members, window, free_member_ids=["nobody"])
    assert "no licensed member is free" in str(caught.value)


def test_the_selection_refuses_a_team_with_nobody_assignable_and_names_each_reason():
    members = [member("a", licensed=False), member("b", calendar_connected=False)]
    distribution = {"mode": "strict", "credits": {}, "members": []}
    window = {"free_minutes_by_member": {}, "total_free_minutes": 0}
    with pytest.raises(NoEligibleMember) as caught:
        select_member(distribution, members, window)
    assert "no Concierge license" in str(caught.value)
    assert "calendar not connected" in str(caught.value)


def test_the_selection_refuses_an_empty_team():
    with pytest.raises(NoEligibleMember):
        select_member({"mode": "strict", "credits": {}, "members": []}, [], {})


def test_the_shares_are_zero_when_the_window_has_no_free_time():
    assert shares({"free_minutes_by_member": {"a": 0}, "total_free_minutes": 0}) == {"a": 0.0}


def test_the_next_candidate_keeps_the_distributions_own_rule():
    """Advancing must not fall back to whoever happens to be next in the list."""
    members = [member("a"), member("b"), member("c")]
    distribution = {
        "mode": "strict",
        "credits": {"c": {"credits_consumed": 5}},
        "members": [],
    }
    window = {"free_minutes_by_member": {"a": 100, "b": 100, "c": 100}, "total_free_minutes": 300}
    following = next_candidate(distribution, members, window, free_member_ids=["b", "c"], after="a")
    assert following["member_id"] == "b", (
        "c has five credits, so b takes it even though c is in the list"
    )


def test_the_next_candidate_refuses_when_the_chosen_member_is_the_only_one():
    distribution = {"mode": "strict", "credits": {}, "members": []}
    with pytest.raises(NoEligibleMember) as caught:
        next_candidate(distribution, [member("a")], {}, free_member_ids=[], after="a")
    assert "only member" in str(caught.value)


# --------------------------------------------------------------------------- #
# The cursor
# --------------------------------------------------------------------------- #


def test_the_cursor_advances_on_every_booking_and_wraps_into_the_next_cycle():
    """ "distribution state advances on each booking".

    Three eligible members, four bookings: the cursor goes 0, 1, 2, back to 0,
    and the cycle increments on the wrap.
    """
    members = [member("a"), member("b"), member("c")]
    state = {"cursor": 0, "cycle": 0}
    seen = []
    for _ in range(4):
        state = advance(state, members)
        seen.append((state["cursor"], state["cycle"]))
    assert seen == [(1, 0), (2, 0), (0, 1), (1, 1)]


def test_the_cursor_counts_only_eligible_members():
    """An unlicensed member must not consume a turn nobody should wait for."""
    members = [member("a"), member("b", licensed=False), member("c")]
    state = advance({"cursor": 0, "cycle": 0}, members)
    assert state == {"cursor": 1, "cycle": 0}
    wrapped = advance({"cursor": 1, "cycle": 0}, members)
    assert wrapped == {"cursor": 0, "cycle": 1}, "two eligible members, so one step wraps"


def test_the_cursor_leaves_itself_alone_when_nobody_is_eligible():
    members = [member("a", licensed=False)]
    assert advance({"cursor": 3, "cycle": 7}, members) == {"cursor": 3, "cycle": 7}


def test_the_cursor_advances_over_a_booking_that_reached_another_member(store):
    """Tying the advance to the cursor would stall the rotation.

    Under a union the booking often lands on a member other than the one the
    cursor pointed at, so an advance conditioned on that match would move the
    cursor only rarely.
    """
    engine = RoundRobinEngine(store, clock=clock)
    team = make_team(engine, member("a"), member("b"), member("c"))
    distribution = make_distribution(engine, team["id"], mode="strict")
    before = store.get(distribution["id"])["data"]["cursor"]
    opened = engine.init_simple(distribution["id"], {"guestEmail": "p@example.test"}, source="test")
    engine.book(str(opened["routing_id"]), {"startTime": opened["start_times"][0]}, source="test")
    assert store.get(distribution["id"])["data"]["cursor"] == (before + 1) % 3


# --------------------------------------------------------------------------- #
# The credit ledger
# --------------------------------------------------------------------------- #


def test_a_booking_consumes_exactly_one_credit_and_takes_the_turn():
    ledger_after = apply_consumption({}, "a", booking_id="b1")
    assert ledger_after["a"]["credits_consumed"] == 1
    assert ledger_after["a"]["turns_taken"] == 1
    assert ledger_after["a"]["bookings"] == 1
    assert ledger_after["a"]["last_booking_id"] == "b1"


def test_a_credit_cannot_be_consumed_without_a_member():
    with pytest.raises(RoundRobinError):
        apply_consumption({}, "", booking_id="b1")


def test_a_ledger_entry_that_is_not_an_object_is_refused():
    with pytest.raises(RoundRobinError):
        apply_consumption({"a": 7}, "a", booking_id="b1")


def test_a_no_show_returns_the_credit_when_the_distribution_allows_it():
    """The flag is off on an empty distribution, so the return is refused."""
    with pytest.raises(RoundRobinError) as caught:
        apply_return({"a": {"credits_consumed": 1}}, "a", booking_id="b1", distribution={})
    assert "credit_back_on_no_show" in str(caught.value)


def test_a_no_show_returns_the_credit_when_the_flag_is_set():
    consumed = apply_consumption({}, "a", booking_id="b1")
    returned = apply_return(
        consumed, "a", booking_id="b1", distribution={"credit_back_on_no_show": True}
    )
    assert returned["a"]["credits_consumed"] == 0
    assert returned["a"]["credits_returned"] == 1
    assert returned["a"]["returned_booking_id"] == "b1"


def test_a_no_show_returns_nothing_when_the_member_has_no_consumed_credit():
    with pytest.raises(RoundRobinError) as caught:
        apply_return({}, "a", booking_id="b1", distribution={"credit_back_on_no_show": True})
    assert "no consumed credit" in str(caught.value)


def test_a_returned_credit_does_not_move_the_turn():
    """A no-show corrects one booking's effect rather than taking a new turn."""
    consumed = apply_consumption({}, "a", booking_id="b1")
    returned = apply_return(
        consumed, "a", booking_id="b1", distribution={"credit_back_on_no_show": True}
    )
    assert returned["a"]["turns_taken"] == 1
    assert returned["a"]["bookings"] == 1


def test_the_ledger_totals_separate_consumed_from_returned():
    ledger_after = apply_return(
        apply_consumption({"b": {"credits_consumed": 2}}, "a", booking_id="b1"),
        "a",
        booking_id="b1",
        distribution={"credit_back_on_no_show": True},
    )
    totals = ledger_totals(ledger_after)
    assert totals["consumed"] == 2
    assert totals["returned"] == 1
    assert totals["bookings"] == 1


def test_the_ledger_reads_a_member_the_distribution_has_never_seen_as_zero():
    """A member who joins mid-cycle starts on an even footing."""
    assert ledger({"mode": "strict", "credits": {}}, [member("new")]) == [
        {
            "member_id": "new",
            "credits_consumed": 0,
            "turns_taken": 0,
            "bookings": 0,
            "credits_returned": 0,
        }
    ]


def test_a_ledger_entry_that_is_not_an_object_is_refused_when_read():
    with pytest.raises(RoundRobinError):
        ledger({"credits": {"a": "nope"}}, [member("a")])


# --------------------------------------------------------------------------- #
# The engine, end to end
# --------------------------------------------------------------------------- #


def test_declaring_a_team_writes_one_row_and_audits_it(store):
    engine = RoundRobinEngine(store, clock=clock)
    team = make_team(engine, member("a"), member("b"))
    assert team["collection"] == TEAM_COLLECTION
    assert len(store.list(TEAM_COLLECTION)) == 1
    assert store.db.audit_count(collection=TEAM_COLLECTION) == 1


def test_a_distribution_must_name_a_team_that_exists(engine):
    with pytest.raises(RoundRobinNotFound):
        make_distribution(engine, "no-such-team")


def test_a_distribution_must_name_a_mode(engine):
    team = make_team(engine, member("a"))
    with pytest.raises(RoundRobinError) as caught:
        engine.create_distribution(
            {"name": "x", "team_ref": team["id"], "interval": INTERVAL}, source="test"
        )
    assert "mode must be one of" in str(caught.value)


def test_a_distribution_refuses_a_link_type_this_workflow_does_not_route(engine):
    """Declaring an Ownership link here would make the link's own type a lie."""
    team = make_team(engine, member("a"))
    with pytest.raises(RoundRobinError) as caught:
        engine.create_distribution(
            {
                "name": "x",
                "mode": "strict",
                "team_ref": team["id"],
                "link_type": "Ownership",
                "interval": INTERVAL,
            },
            source="test",
        )
    assert "RoundRobin" in str(caught.value)


def test_a_distribution_requires_a_team_reference(engine):
    with pytest.raises(RoundRobinError) as caught:
        engine.create_distribution({"name": "x", "mode": "strict"}, source="test")
    assert "team_ref is required" in str(caught.value)


def test_a_distribution_refuses_a_weight_for_a_member_the_team_does_not_list(engine):
    team = make_team(engine, member("a"))
    with pytest.raises(RoundRobinError) as caught:
        make_distribution(engine, team["id"], members=[{"member_id": "stranger", "weight": 2.0}])
    assert "stranger" in str(caught.value)


def test_a_distribution_refuses_a_negative_or_unreadable_weight(engine):
    team = make_team(engine, member("a"))
    with pytest.raises(RoundRobinError):
        make_distribution(engine, team["id"], members=[{"member_id": "a", "weight": -1.0}])
    with pytest.raises(RoundRobinError):
        make_distribution(engine, team["id"], members=[{"member_id": "a", "weight": "heavy"}])
    with pytest.raises(RoundRobinError):
        make_distribution(engine, team["id"], members=["not an object"])


def test_a_distribution_refuses_a_member_entry_with_no_id(engine):
    team = make_team(engine, member("a"))
    with pytest.raises(RoundRobinError) as caught:
        make_distribution(engine, team["id"], members=[{"weight": 2.0}])
    assert "member_id" in str(caught.value)


def test_a_distribution_refuses_a_members_list_that_is_not_a_list(engine):
    team = make_team(engine, member("a"))
    with pytest.raises(RoundRobinError):
        make_distribution(engine, team["id"], members="a,b")


def test_a_distribution_starts_with_an_empty_ledger_and_a_zeroed_cursor(engine):
    team = make_team(engine, member("a"))
    distribution = make_distribution(engine, team["id"])
    assert distribution["data"]["credits"] == {}
    assert distribution["data"]["cursor"] == 0
    assert distribution["data"]["cycle"] == 0
    assert distribution["data"]["credit_back_on_no_show"] is False


def test_the_preview_writes_nothing_at_all(store):
    """The read-only half of init-simple, for a form that asks before committing."""
    engine = RoundRobinEngine(store, clock=clock)
    team = make_team(engine, member("a"), member("b"))
    distribution = make_distribution(engine, team["id"])
    preview = engine.check(distribution["id"], {})

    assert preview["outcome"] == "allocation"
    assert preview["chosen"]["member_id"] in {"a", "b"}
    assert store.list(ROUTE_COLLECTION) == []
    assert store.list(BOOKING_COLLECTION) == []
    assert store.get(distribution["id"])["data"]["credits"] == {}


def test_the_preview_and_the_initialisation_agree_on_the_chosen_member(store):
    """They call the same selection, so only the consequences differ."""
    engine = RoundRobinEngine(store, clock=clock)
    team = make_team(engine, member("a"), member("b"), member("c"))
    distribution = make_distribution(engine, team["id"], mode="strict")
    assert (
        engine.check(distribution["id"], {})["chosen"]["member_id"]
        == engine.init_simple(distribution["id"], {"guestEmail": "p@example.test"}, source="test")[
            "chosen"
        ]["member_id"]
    )


def test_an_evaluation_needs_a_guest_email(engine):
    team = make_team(engine, member("a"))
    distribution = make_distribution(engine, team["id"])
    with pytest.raises(RoundRobinError) as caught:
        engine.init_simple(distribution["id"], {}, source="test")
    assert "guestEmail is required" in str(caught.value)


def test_an_evaluation_reports_eligible_with_no_slots_when_the_team_is_fully_booked(engine):
    """A busy week is a legitimate answer, not an error."""
    team = make_team(
        engine,
        busy("a", INTERVAL["start"], INTERVAL["end"]),
        busy("b", INTERVAL["start"], INTERVAL["end"]),
    )
    distribution = make_distribution(engine, team["id"])
    opened = engine.init_simple(distribution["id"], {"guestEmail": "p@example.test"}, source="test")
    assert opened["outcome"] == "eligible"
    assert opened["start_times"] == []
    assert opened["routing_id"] is None


def test_an_evaluation_refuses_a_team_with_nobody_assignable(store):
    """The researched Not Scheduled path with nothing left on it.

    This is the bug written to be caught during the build: a team of unlicensed
    members produces an empty window, and an empty window alone reads as "nobody
    is free" rather than "nobody may be assigned".
    """
    engine = RoundRobinEngine(store, clock=clock)
    team = make_team(engine, member("ghost", licensed=False))
    distribution = make_distribution(engine, team["id"])
    with pytest.raises(NoEligibleMember) as caught:
        engine.init_simple(distribution["id"], {"guestEmail": "p@example.test"}, source="test")
    assert "no Concierge license" in str(caught.value)
    assert store.list(ROUTE_COLLECTION) == [], "a refusal must not leave a routing session"


def test_the_preview_refuses_the_same_team_the_evaluation_refuses(store):
    engine = RoundRobinEngine(store, clock=clock)
    team = make_team(engine, member("ghost", licensed=False))
    distribution = make_distribution(engine, team["id"])
    with pytest.raises(NoEligibleMember):
        engine.check(distribution["id"], {})


def test_an_evaluation_refuses_a_distribution_that_does_not_exist(engine):
    with pytest.raises(RoundRobinNotFound):
        engine.check("no-such-distribution", {})


def test_an_evaluation_writes_one_route_holding_the_slots_and_the_chosen_member(store):
    engine = RoundRobinEngine(store, clock=clock)
    team = make_team(engine, member("a"), member("b"))
    distribution = make_distribution(engine, team["id"])
    opened = engine.init_simple(distribution["id"], {"guestEmail": "p@example.test"}, source="test")

    routes = store.list(ROUTE_COLLECTION)
    assert len(routes) == 1
    route = routes[0]
    assert route["data"]["state"] == "open"
    assert route["data"]["guest_email"] == "p@example.test"
    assert route["data"]["chosen_member_id"] == opened["chosen"]["member_id"]
    assert route["data"]["slot_count"] == len(opened["slots"])
    assert route["data"]["offered_slots"][0]["free_member_ids"]


def test_booking_consumes_a_credit_advances_the_cursor_and_closes_the_route(store):
    engine = RoundRobinEngine(store, clock=clock)
    team = make_team(engine, member("a"), member("b"))
    distribution = make_distribution(engine, team["id"])
    opened = engine.init_simple(distribution["id"], {"guestEmail": "p@example.test"}, source="test")
    taken = engine.book(
        str(opened["routing_id"]), {"startTime": opened["start_times"][0]}, source="test"
    )

    assert taken["booking"]["data"]["status"] == "confirmed"
    assert taken["cursor"] == 1
    after = store.get(distribution["id"])["data"]
    assert after["credits"][taken["member_id"]]["credits_consumed"] == 1
    assert store.get(str(opened["routing_id"]))["data"]["state"] == "booked"
    assert len(store.list(CREDIT_MOVEMENT_COLLECTION)) == 1


def test_a_route_cannot_be_booked_twice(store):
    engine = RoundRobinEngine(store, clock=clock)
    team = make_team(engine, member("a"), member("b"))
    distribution = make_distribution(engine, team["id"])
    opened = engine.init_simple(distribution["id"], {"guestEmail": "p@example.test"}, source="test")
    engine.book(str(opened["routing_id"]), {"startTime": opened["start_times"][0]}, source="test")
    with pytest.raises(RoundRobinConflict) as caught:
        engine.book(
            str(opened["routing_id"]), {"startTime": opened["start_times"][0]}, source="test"
        )
    assert "cannot be booked again" in str(caught.value)


def test_a_booking_must_name_a_slot_the_route_offered(store):
    """Booking 09:15 against a 09:00-09:30 slot is refused, not quietly rounded."""
    engine = RoundRobinEngine(store, clock=clock)
    team = make_team(engine, member("a"))
    distribution = make_distribution(engine, team["id"])
    opened = engine.init_simple(distribution["id"], {"guestEmail": "p@example.test"}, source="test")
    with pytest.raises(RoundRobinError) as caught:
        engine.book(
            str(opened["routing_id"]), {"startTime": "2026-03-02T09:15:00+00:00"}, source="test"
        )
    assert "not on offer" in str(caught.value)


def test_a_booking_must_name_a_start_time(store):
    engine = RoundRobinEngine(store, clock=clock)
    team = make_team(engine, member("a"))
    distribution = make_distribution(engine, team["id"])
    opened = engine.init_simple(distribution["id"], {"guestEmail": "p@example.test"}, source="test")
    with pytest.raises(RoundRobinError) as caught:
        engine.book(str(opened["routing_id"]), {}, source="test")
    assert "startTime is required" in str(caught.value)


def test_a_booking_refuses_a_guest_the_route_was_not_opened_for(store):
    engine = RoundRobinEngine(store, clock=clock)
    team = make_team(engine, member("a"))
    distribution = make_distribution(engine, team["id"])
    opened = engine.init_simple(distribution["id"], {"guestEmail": "p@example.test"}, source="test")
    with pytest.raises(RoundRobinError) as caught:
        engine.book(
            str(opened["routing_id"]),
            {"startTime": opened["start_times"][0], "guestEmail": "someone.else@example.test"},
            source="test",
        )
    assert "opened for" in str(caught.value)


def test_booking_against_a_route_that_does_not_exist_is_404(engine):
    with pytest.raises(RoundRobinNotFound):
        engine.book("no-such-route", {"startTime": "2026-03-02T09:00:00+00:00"}, source="test")


def test_a_member_who_took_another_booking_is_replaced_rather_than_refused(store):
    """The union's re-check, made actionable.

    A busy rep is not the prospect's error, so the booking advances to the next
    eligible member rather than refusing.
    """
    engine = RoundRobinEngine(store, clock=clock)
    team = make_team(engine, member("a"), member("b"))
    distribution = make_distribution(engine, team["id"], mode="strict")
    opened = engine.init_simple(distribution["id"], {"guestEmail": "p@example.test"}, source="test")
    first_choice = opened["chosen"]["member_id"]

    # The chosen member takes another booking after the route opened, so they are
    # no longer free at the slot the prospect picked.
    store.update(
        team["id"],
        {
            "members": [
                member("a", busy=[{"start": INTERVAL["start"], "end": INTERVAL["end"]}]),
                member("b"),
            ]
        },
    )
    taken = engine.book(
        str(opened["routing_id"]), {"startTime": opened["start_times"][0]}, source="test"
    )

    assert taken["rechecked_at_booking"] is True
    assert taken["member_id"] != first_choice
    assert taken["member_id"] == "b"


def test_booking_advances_the_distribution_to_the_next_eligible_member_when_none_is_free(store):
    engine = RoundRobinEngine(store, clock=clock)
    team = make_team(engine, member("a"))
    distribution = make_distribution(engine, team["id"])
    opened = engine.init_simple(distribution["id"], {"guestEmail": "p@example.test"}, source="test")
    # The only member takes another booking after the route opened.
    store.update(
        team["id"],
        {"members": [member("a", busy=[{"start": INTERVAL["start"], "end": INTERVAL["end"]}])]},
    )
    with pytest.raises(NoEligibleMember):
        engine.book(
            str(opened["routing_id"]), {"startTime": opened["start_times"][0]}, source="test"
        )


def test_the_booking_the_credit_and_the_advance_commit_together(store):
    """A booking beside an open route is the state that lets one slot be twice."""
    engine = RoundRobinEngine(store, clock=clock)
    team = make_team(engine, member("a"), member("b"))
    distribution = make_distribution(engine, team["id"])
    opened = engine.init_simple(distribution["id"], {"guestEmail": "p@example.test"}, source="test")

    before_movements = len(store.list(CREDIT_MOVEMENT_COLLECTION))
    engine.book(str(opened["routing_id"]), {"startTime": opened["start_times"][0]}, source="test")
    assert len(store.list(CREDIT_MOVEMENT_COLLECTION)) == before_movements + 1
    assert len(store.list(BOOKING_COLLECTION)) == 1


# --------------------------------------------------------------------------- #
# Step 5: the no-show
# --------------------------------------------------------------------------- #


def test_a_no_show_credits_the_member_back_when_the_flag_is_set(store):
    engine = RoundRobinEngine(store, clock=clock)
    team = make_team(engine, member("a"), member("b"))
    distribution = make_distribution(engine, team["id"], credit_back_on_no_show=True)
    opened = engine.init_simple(distribution["id"], {"guestEmail": "p@example.test"}, source="test")
    taken = engine.book(
        str(opened["routing_id"]), {"startTime": opened["start_times"][0]}, source="test"
    )

    result = engine.mark_no_show(str(taken["booking_id"]), {"note": "did not join"}, source="test")
    assert result["credited_back"] == 1
    after = store.get(distribution["id"])["data"]["credits"][taken["member_id"]]
    assert after["credits_consumed"] == 0
    assert after["credits_returned"] == 1
    assert store.get(str(taken["booking_id"]))["data"]["status"] == "no_show"
    assert len(store.list(NO_SHOW_COLLECTION)) == 1


def test_a_no_show_refuses_when_the_distribution_does_not_credit_back(store):
    """An admin who pressed the button expects a credit back or an explanation."""
    engine = RoundRobinEngine(store, clock=clock)
    team = make_team(engine, member("a"), member("b"))
    distribution = make_distribution(engine, team["id"], credit_back_on_no_show=False)
    opened = engine.init_simple(distribution["id"], {"guestEmail": "p@example.test"}, source="test")
    taken = engine.book(
        str(opened["routing_id"]), {"startTime": opened["start_times"][0]}, source="test"
    )

    with pytest.raises(RoundRobinError) as caught:
        engine.mark_no_show(str(taken["booking_id"]), {}, source="test")
    assert "credit_back_on_no_show" in str(caught.value)
    assert store.list(NO_SHOW_COLLECTION) == [], "a refusal must not leave a no-show row"
    assert store.get(str(taken["booking_id"]))["data"]["status"] == "confirmed"


def test_a_booking_cannot_be_marked_no_show_twice(store):
    engine = RoundRobinEngine(store, clock=clock)
    team = make_team(engine, member("a"), member("b"))
    distribution = make_distribution(engine, team["id"], credit_back_on_no_show=True)
    opened = engine.init_simple(distribution["id"], {"guestEmail": "p@example.test"}, source="test")
    taken = engine.book(
        str(opened["routing_id"]), {"startTime": opened["start_times"][0]}, source="test"
    )
    engine.mark_no_show(str(taken["booking_id"]), {}, source="test")
    with pytest.raises(RoundRobinConflict) as caught:
        engine.mark_no_show(str(taken["booking_id"]), {}, source="test")
    assert "already been marked No-Show" in str(caught.value)


def test_a_no_show_on_a_booking_that_does_not_exist_is_404(engine):
    with pytest.raises(RoundRobinNotFound):
        engine.mark_no_show("no-such-booking", {}, source="test")


def test_a_no_show_on_a_cancelled_booking_is_refused(store):
    engine = RoundRobinEngine(store, clock=clock)
    team = make_team(engine, member("a"), member("b"))
    distribution = make_distribution(engine, team["id"], credit_back_on_no_show=True)
    opened = engine.init_simple(distribution["id"], {"guestEmail": "p@example.test"}, source="test")
    taken = engine.book(
        str(opened["routing_id"]), {"startTime": opened["start_times"][0]}, source="test"
    )
    engine.cancel_booking(str(taken["booking_id"]), source="test")
    with pytest.raises(RoundRobinConflict):
        engine.mark_no_show(str(taken["booking_id"]), {}, source="test")


def test_a_cancellation_returns_no_credit(store):
    """The research describes a credit-back for a no-show, not a cancellation."""
    engine = RoundRobinEngine(store, clock=clock)
    team = make_team(engine, member("a"), member("b"))
    distribution = make_distribution(engine, team["id"], credit_back_on_no_show=True)
    opened = engine.init_simple(distribution["id"], {"guestEmail": "p@example.test"}, source="test")
    taken = engine.book(
        str(opened["routing_id"]), {"startTime": opened["start_times"][0]}, source="test"
    )
    engine.cancel_booking(str(taken["booking_id"]), source="test")

    after = store.get(distribution["id"])["data"]["credits"][taken["member_id"]]
    assert after["credits_consumed"] == 1, "a cancellation consumes nothing and returns nothing"
    assert after["credits_returned"] == 0


def test_a_cancelled_booking_cannot_be_cancelled_again(store):
    engine = RoundRobinEngine(store, clock=clock)
    team = make_team(engine, member("a"), member("b"))
    distribution = make_distribution(engine, team["id"])
    opened = engine.init_simple(distribution["id"], {"guestEmail": "p@example.test"}, source="test")
    taken = engine.book(
        str(opened["routing_id"]), {"startTime": opened["start_times"][0]}, source="test"
    )
    engine.cancel_booking(str(taken["booking_id"]), source="test")
    with pytest.raises(RoundRobinConflict):
        engine.cancel_booking(str(taken["booking_id"]), source="test")


def test_cancelling_a_booking_that_does_not_exist_is_404(engine):
    with pytest.raises(RoundRobinNotFound):
        engine.cancel_booking("no-such-booking", source="test")


# --------------------------------------------------------------------------- #
# The reads the page is built from
# --------------------------------------------------------------------------- #


def test_the_team_view_lists_an_excluded_member_with_its_reason(store):
    """ "this prospect cannot reach Sam" is the thing an admin needs to see."""
    engine = RoundRobinEngine(store, clock=clock)
    team = make_team(engine, member("a"), member("ghost", licensed=False))
    view = engine.team_view(team)
    excluded = [row for row in view["members"] if not row["eligible"]]
    assert excluded[0]["member_id"] == "ghost"
    assert excluded[0]["excluded_reason"] == "no Concierge license"
    assert view["eligibility"] == {
        "members": 2,
        "eligible": 1,
        "excluded": 1,
        "excluded_reasons": ["no Concierge license"],
    }


def test_reading_a_team_that_does_not_exist_is_404(engine):
    with pytest.raises(RoundRobinNotFound):
        engine.require_team("no-such-team")


def test_reading_a_distribution_that_does_not_exist_is_404(engine):
    with pytest.raises(RoundRobinNotFound):
        engine.require_distribution("no-such-distribution")


def test_the_distribution_view_renders_the_ledger_against_the_teams_members(store):
    """A ledger entry for a member who left the team must not become invisible."""
    engine = RoundRobinEngine(store, clock=clock)
    team = make_team(engine, member("a"), member("b"))
    distribution = make_distribution(engine, team["id"], credit_back_on_no_show=True)
    opened = engine.init_simple(distribution["id"], {"guestEmail": "p@example.test"}, source="test")
    taken = engine.book(
        str(opened["routing_id"]), {"startTime": opened["start_times"][0]}, source="test"
    )

    view = engine.distribution_view(store.get(distribution["id"]))
    assert [row["member_id"] for row in view["ledger"]] == ["a", "b"]
    credited = next(row for row in view["ledger"] if row["member_id"] == taken["member_id"])
    assert credited["credits_consumed"] == 1
    assert view["credit_totals"]["consumed"] == 1
    assert view["eligibility"]["eligible"] == 2


def test_the_distribution_view_survives_a_team_that_has_been_removed(store):
    engine = RoundRobinEngine(store, clock=clock)
    team = make_team(engine, member("a"))
    distribution = make_distribution(engine, team["id"])
    store.delete(team["id"])
    view = engine.distribution_view(store.get(distribution["id"]))
    assert view["ledger"] == []
    assert view["credit_totals"]["consumed"] == 0


def test_the_catalog_reports_the_link_types_and_who_is_supported(engine):
    catalog = engine.catalog()
    assert catalog["supported_link_types"] == ["RoundRobin"]
    assert len(catalog["link_types"]) == 5


def test_the_catalog_lists_teams_with_their_eligibility(store):
    engine = RoundRobinEngine(store, clock=clock)
    make_team(engine, member("a"), member("ghost", licensed=False))
    catalog = engine.catalog()
    assert len(catalog["teams"]) == 1
    assert catalog["teams"][0]["eligibility"]["excluded"] == 1


def test_the_summary_counts_the_rows_the_filters_return(store):
    engine = RoundRobinEngine(store, clock=clock)
    team = make_team(engine, member("a"), member("b"))
    distribution = make_distribution(engine, team["id"], credit_back_on_no_show=True)
    opened = engine.init_simple(distribution["id"], {"guestEmail": "p@example.test"}, source="test")
    engine.book(str(opened["routing_id"]), {"startTime": opened["start_times"][0]}, source="test")

    counts = engine.summary()
    assert counts["teams"] == 1
    assert counts["distributions"] == 1
    assert counts["distributions_by_mode"] == {"strict": 1}
    assert counts["routes"] == 1
    assert counts["routes_open"] == 0
    assert counts["bookings"] == 1
    assert counts["bookings_confirmed"] == 1
    assert counts["no_shows"] == 0


def test_the_summary_counts_the_excluded_members_it_actually_found(store):
    """This is the fourth bug written to be caught: the count was read off an
    empty list, so a team with an unlicensed member reported zero exclusions
    however many of them it had."""
    engine = RoundRobinEngine(store, clock=clock)
    make_team(
        engine,
        member("a"),
        member("ghost", licensed=False),
        member("ghost2", licensed=False),
        member("offline", calendar_connected=False),
    )
    counts = engine.summary()
    assert counts["excluded_members"] == 3


def test_the_summary_is_room_scoped_over_the_same_rows_the_lists_return(store):
    engine = RoundRobinEngine(store, clock=clock)
    store.create("room", {"name": "R"}, record_id="room-1", source="test")
    team = make_team(engine, member("a"), room_id="room-1")
    distribution = make_distribution(engine, team["id"], room_id="room-1")
    opened = engine.init_simple(
        distribution["id"], {"guestEmail": "p@example.test"}, room_id="room-1", source="test"
    )
    engine.book(
        str(opened["routing_id"]),
        {"startTime": opened["start_times"][0]},
        room_id="room-1",
        source="test",
    )
    assert engine.summary(room_id="room-1")["bookings"] == 1
    assert engine.summary()["bookings"] == 1, "unscoped is the whole product, not the room"


def test_the_route_and_booking_lists_filter_by_state_status_and_member(store):
    engine = RoundRobinEngine(store, clock=clock)
    team = make_team(engine, member("a"), member("b"))
    distribution = make_distribution(engine, team["id"])
    first = engine.init_simple(distribution["id"], {"guestEmail": "p@example.test"}, source="test")
    engine.book(str(first["routing_id"]), {"startTime": first["start_times"][0]}, source="test")
    engine.init_simple(distribution["id"], {"guestEmail": "q@example.test"}, source="test")

    assert len(engine.routes(state="booked")) == 1
    assert len(engine.routes(state="open")) == 1
    assert len(engine.routes(distribution_id=distribution["id"])) == 2
    assert engine.routes(member_id_filter="nobody") == []
    assert len(engine.bookings(status="confirmed")) == 1
    assert engine.bookings(member_id_filter="nobody") == []


def test_the_credit_movement_list_can_be_scoped_to_one_distribution(store):
    engine = RoundRobinEngine(store, clock=clock)
    team = make_team(engine, member("a"))
    distribution = make_distribution(engine, team["id"])
    opened = engine.init_simple(distribution["id"], {"guestEmail": "p@example.test"}, source="test")
    engine.book(str(opened["routing_id"]), {"startTime": opened["start_times"][0]}, source="test")

    assert len(engine.credit_movements(distribution_id=distribution["id"])) == 1
    assert engine.credit_movements(distribution_id="other") == []


# --------------------------------------------------------------------------- #
# The inferences registry
# --------------------------------------------------------------------------- #


def test_every_inference_is_addressable_by_its_own_id():
    registry = describe()
    for entry in registry["inferences"]:
        assert entry["id"] and entry["question"] and entry["decision"]
        assert entry["why"] and entry["change_if"], entry["id"]


def test_the_calendar_derivation_records_both_jev_asks():
    """The first ask was uncertain at 0.44; the narrowed one passed at 1.00.

    Recorded because a quietly overridden uncertain verdict is a defect, and the
    only way to prove it was not is to leave the record of both in the code.
    """
    registry = describe()
    derivation = next(
        e for e in registry["inferences"] if e["id"] == "inference_calendar_combination"
    )
    assert derivation["jev_audit_id"] == "jev-20261004T045227-22564-47815"
    assert derivation["jev_verdict"] == "pass"
    assert derivation["jev_confidence"] == 1.0
    assert "uncertain" in derivation["jev_first_ask"]


def test_the_published_vocabulary_carries_the_jev_ratified_derivation():
    combination = published_vocabulary()["calendar_combination"]
    assert combination["operation"] == "union"
    assert combination["recheck_at_booking"] is True
    assert combination["inference"] == "inference_calendar_combination"


def test_every_vocabulary_validator_accepts_its_own_value_and_refuses_another():
    """The validators are the only thing standing between a bad value and a 500.

    A bare ``ValueError`` here would escape the one registered handler and answer
    500 for what every other out-of-vocabulary value answers 400 for.
    """
    from dsr.round_robin import vocabulary as vocab

    cases = [
        (vocab.require_mode, "strict"),
        (vocab.require_mode, "nonsense"),
        (vocab.require_link_type, "RoundRobin"),
        (vocab.require_link_type, "Telepathy"),
        (vocab.require_route_state, "open"),
        (vocab.require_route_state, "halfway"),
        (vocab.require_booking_status, "confirmed"),
        (vocab.require_booking_status, "postponed"),
        (vocab.require_credit_direction, "consumed"),
        (vocab.require_credit_direction, "borrowed"),
        (vocab.require_outcome, "allocation"),
        (vocab.require_outcome, "maybe"),
    ]
    for require, value in cases:
        if value in {
            "strict",
            "RoundRobin",
            "open",
            "confirmed",
            "consumed",
            "allocation",
        }:
            assert require(value) == value
        else:
            with pytest.raises(RoundRobinError) as caught:
                require(value)
            assert "must be one of" in str(caught.value)


def test_a_vocabulary_validator_names_the_field_it_refused():
    """ "mode must be one of" is useful; "must be one of" on its own is not."""
    from dsr.round_robin import vocabulary as vocab

    with pytest.raises(RoundRobinError) as caught:
        vocab.require_mode("nonsense")
    assert str(caught.value).startswith("mode must be one of")


def test_the_inference_registry_can_be_read_one_entry_at_a_time():
    from dsr.round_robin.inferences import by_id

    assert by_id("inference_calendar_combination")["id"] == "inference_calendar_combination"
    assert by_id("no-such-inference") is None


def test_an_error_may_carry_its_own_code():
    """The override is what lets a subclass reuse the base without lying about it."""
    error = RoundRobinError("something specific", code="bespoke")
    assert error.code == "bespoke"
    assert str(error) == "something specific"


def test_an_error_without_an_override_keeps_the_class_code():
    assert RoundRobinError("plain").code == "round_robin_error"


def test_every_error_subclass_carries_its_own_status_and_code():
    """Two features may not map one error type, so each class must be distinct."""
    for error_type in (RoundRobinNotFound, RoundRobinConflict, NoEligibleMember):
        assert issubclass(error_type, RoundRobinError)
        assert isinstance(error_type.status, int)
        assert error_type.code
    codes = {e.code for e in (RoundRobinNotFound, RoundRobinConflict, NoEligibleMember)}
    assert len(codes) == 3, "two refusals sharing a code are indistinguishable to a client"


# --------------------------------------------------------------------------- #
# The feature module
# --------------------------------------------------------------------------- #


def test_the_feature_module_declares_the_prefix_the_issue_names():
    from dsr.features import wf054_round_robin_booking as feature

    assert feature.router.prefix == "/api/wf054"
    assert feature.FEATURE["ticket"] == "WF-054"
    assert feature.FEATURE["id"] == "wf-054-round-robin-booking"
    assert feature.FEATURE["name"]


def test_the_feature_registers_one_handler_for_the_whole_error_hierarchy():
    from dsr.features import wf054_round_robin_booking as feature

    assert feature.EXCEPTION_HANDLERS == {RoundRobinError: feature._round_robin_error}


def test_the_domain_package_never_opens_the_database_or_imports_the_app():
    """The enforced rules, checked here so a regression is caught at the source."""
    from pathlib import Path

    import dsr.round_robin as package

    root = Path(package.__file__).parent
    for module in root.glob("*.py"):
        text = module.read_text(encoding="utf-8")
        assert "from dsr.api" not in text and "import dsr.api" not in text, module.name
        assert "import sqlite3" not in text, module.name
        assert "AuditedDatabase(" not in text, module.name


def test_the_seeder_runs_and_its_return_string_is_printable_on_a_windows_console(tmp_path):
    """A single RIGHTWARDS ARROW in one recovered feature broke the whole seeder."""
    from dsr.db.audited import AuditedDatabase
    from dsr.features import wf054_round_robin_booking as feature

    db = AuditedDatabase(tmp_path / "seed.db", actor="seed")
    try:
        reported = feature.seed(db, {"room_ids": [], "now": NOW})
    finally:
        db.close()

    assert isinstance(reported, str) and reported
    reported.encode("cp1252")
    print(reported)
    assert "no-show" in reported
    assert "no_eligible_member" in reported, "the demo must show the honest refusal too"


def test_the_seeder_creates_the_states_it_claims(tmp_path):
    from dsr.db.audited import AuditedDatabase
    from dsr.features import wf054_round_robin_booking as feature
    from dsr.store import RecordStore

    db = AuditedDatabase(tmp_path / "seed.db", actor="seed")
    try:
        feature.seed(db, {"room_ids": [], "now": NOW})
        store = RecordStore(db)
        assert store.list(TEAM_COLLECTION)
        assert store.list(DISTRIBUTION_COLLECTION)
        assert store.list(ROUTE_COLLECTION)
        assert store.list(BOOKING_COLLECTION)
        assert store.list(NO_SHOW_COLLECTION)
        # An unlicensed-only team exists, which is the refusal the demo exists for.
        unlicensed = [
            team
            for team in store.list(TEAM_COLLECTION)
            if all(not row.get("licensed", True) for row in team["data"]["members"])
        ]
        assert unlicensed, "the demo must include a team nobody can be assigned from"
    finally:
        db.close()
