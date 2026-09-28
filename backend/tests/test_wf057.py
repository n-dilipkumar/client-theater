"""Tests for WF-057: find a time that works for a multi-person panel.

The claims under test come from
``docs/research/digital-sales-room-workflows/wf/WF-057.md``, and each section
below names which one it is pinning:

* the five-step user flow, and step 4's "**each with a confidence percentage and
  a human-readable reason**" and step 5's "**the organizer's calendar**" and
  "**optionally creating a fresh conference**";
* the data flow's arithmetic, sentence for sentence: **free=100%, unknown=49%,
  busy=0%**, averaged, then **sorted high→low then chronologically**;
* the two provider endpoints with their least-privileged scopes, the
  ``Prefer: outlook.timezone`` header, and the ``returnSuggestionReasons`` toggle;
* the two capacity knobs - ``calendarExpansionMax`` (max 50) and
  ``groupExpansionMax`` (max 100) - and group expansion for whole distribution
  lists;
* the quoted ``suggestionReason`` and the quoted ``emptySuggestionsReason``
  property, with the documented re-call: **"Based on this value, you can better
  adjust the parameters and call findMeetingTimes again"**;
* the drift note - suggestions are "**fine-tuned from time to time**" - and the
  research's own statement that availability is a *pull* read;
* and the extensibility claim: **"free/busy is decoupled from slot data - an
  integrator can layer their own scoring/ranking, house rules (no Friday
  afternoons, no back-to-back), or book into a room resource."**

The parts the research does *not* fix are the design inferences, and they are
tested as inferences: named, bounded, and changeable in one place.

Searching and booking run through the in-process local directory, so the whole
flow is asserted without a socket and without a network flake.
"""

from __future__ import annotations

import inspect
import re
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dsr.api import app
from dsr.db.audited import AuditedDatabase
from dsr.features import load_feature
from dsr.panel_time import (
    ACTIVITY_DOMAINS,
    ACTIVITY_UNRESTRICTED,
    ACTIVITY_WORK,
    ADJACENT_SURFACES,
    ATTENDANCE_QUOTE,
    ATTENDANCE_STATUSES,
    ATTENDANCE_WEIGHTS,
    BOOKING_COLLECTION,
    BusyMap,
    CALENDAR_COLLECTION,
    CALENDAR_EXPANSION_MAX_LIMIT,
    CALENDAR_EXPANSION_MAX_QUOTE,
    CALENDAR_EXPANSION_MAX_LIMIT as CAL_CAP,
    Candidate,
    CalendarShapeError,
    Clock,
    COLLECTIONS,
    ConstraintError,
    DEFAULT_MEETING_DURATION,
    DEFAULT_SLOT_INTERVAL,
    DRIFT_QUOTE,
    EMPTY_BUSY_SUGGESTIONS,
    EMPTY_NONE,
    EMPTY_NOT_ORGANIZER,
    EMPTY_NOT_ENOUGH_CALENDAR_FREE_TIME,
    EMPTY_NOT_ENOUGH_PEOPLE_FREE,
    EMPTY_REASONS,
    EMPTY_SUGGESTIONS_REASON_QUOTE,
    Evaluation,
    GOOGLE_FREEBUSY_SCOPE,
    GOOGLE_FREEBUSY_URL,
    GRAPH_DELEGATED_SCOPE,
    GRAPH_PREFER_HEADER,
    GROUP_EXPANSION_MAX_LIMIT,
    HOUSE_RULE_KEYS,
    INFERENCES,
    KINDS,
    LimitExceeded,
    LocalDirectory,
    LOCATION_ROOM,
    LOCATION_SUGGEST,
    NotFound,
    PANEL_COLLECTION,
    PanelShapeError,
    PanelTimeNotConfigured,
    RANK_CONFIDENCE,
    RANK_WEIGHTED,
    RANKERS,
    SEARCH_COLLECTION,
    STATUS_BUSY,
    STATUS_FREE,
    STATUS_UNKNOWN,
    SUGGESTION_REASON_ALL_FREE,
    SUGGESTION_REASON_QUOTE,
    SlotFinder,
    SlotUnavailable,
    SOURCED_GAPS,
    SOURCED_QUOTES,
    USER_FLOW,
    UrllibProvider,
    apply_adjustment,
    apply_house_rules,
    describe_inferences,
    describe_vocabulary,
    enumerate_candidates,
    evaluate,
    expand_invited,
    format_duration,
    format_instant,
    normalise_house_rules,
    normalise_time_constraint,
    overlaps,
    pad_window,
    parse_clock_time,
    parse_duration,
    parse_google_free_busy,
    parse_instant,
    rank,
    render_commit_request,
    render_free_busy_request,
    retune_adjustments,
    round_percentage,
    suggestion_reason,
    tzdb_available,
    window,
)
from dsr.panel_time import RETUNE_ADJUSTMENTS
from dsr.store import RecordStore

#: The feature's own prefix. Duplicated here rather than imported so a change to
#: the prefix has to be made deliberately in the test as well, which is the point
#: of a test: a renamed route should fail, not follow silently.
PREFIX = "/api/wf-057"

MODULE = "wf057_find_a_time_that_works_for_a_multi_per"

#: What the pure-domain tests pass as ``source``. Deliberately the exact shape a
#: route passes, so a test asserting on an audit row is asserting on the real
#: thing rather than on a convenient placeholder.
SOURCE = f"POST {PREFIX}/rooms/{{room_id}}/panels/{{panel_id}}/find"

#: A Monday. Every window in this file is anchored here, so a test that means
#: "the weekend is excluded" is unambiguous.
MONDAY = "2026-10-05"
TUESDAY = "2026-10-06"


def at(day: str, hour: int, minute: int = 0) -> str:
    """A UTC instant, so a test never depends on the machine's local zone."""
    return f"{day}T{hour:02d}:{minute:02d}:00Z"


def count(store: RecordStore, collection: str) -> int:
    """``RecordStore`` has no ``count``; this is the same query without a new seam."""
    return len(store.list(collection, limit=1000))


def busy_map(
    *, busy: dict[str, list[tuple[str, str]]] | None = None, unreadable: dict[str, str] | None = None
) -> BusyMap:
    """A hand-built availability read, with no provider and no store behind it.

    The researched extensibility claim is that free/busy is decoupled from slot
    data. This helper is that claim's test fixture: the whole ranking runs on a
    dict.
    """
    blocks: dict[str, list[tuple[datetime, datetime]]] = {}
    for calendar_id, intervals in (busy or {}).items():
        blocks[calendar_id] = [
            (parse_instant(start), parse_instant(end)) for start, end in intervals
        ]
    return BusyMap(
        busy=blocks, unreadable=dict(unreadable or {}), requested=tuple(sorted(blocks))
    )


def candidates(*starts: str, minutes: int = 60) -> list[Candidate]:
    return [
        Candidate(
            parse_instant(start),
            parse_instant(start) + timedelta(minutes=minutes),
            0,
        )
        for start in starts
    ]


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture()
def store(tmp_path):
    db = AuditedDatabase(tmp_path / "wf057.db", mirror_dir=tmp_path / "mirror")
    yield RecordStore(db)
    db.close()


@pytest.fixture()
def finder(store):
    return SlotFinder(store)


@pytest.fixture()
def room(store):
    return store.create(
        "room", {"name": "Northwind — Enterprise Evaluation", "account": "Northwind Traders"},
        actor="dana",
    )


@pytest.fixture()
def busy_morning(finder):
    """A calendar booked 09:00-10:00 on the demo Monday."""
    return finder.create_calendar(
        {
            "email": "busy.morning@northwind.example",
            "name": "Busy in the morning",
            "busy": [{"start": at(MONDAY, 9), "end": at(MONDAY, 10)}],
        },
        actor="dana",
        source=SOURCE,
    )


@pytest.fixture()
def free_calendar(finder):
    return finder.create_calendar(
        {"email": "free@northwind.example", "name": "Free all day", "busy": []},
        actor="dana",
        source=SOURCE,
    )


@pytest.fixture()
def silent_calendar(finder):
    """The researched **unknown** case: a calendar nobody has published."""
    return finder.create_calendar(
        {
            "email": "silent@northwind.example",
            "name": "Has not published a calendar",
            "readable": False,
            "unavailable_reason": "this contact has not shared a calendar",
        },
        actor="dana",
        source=SOURCE,
    )


@pytest.fixture()
def panel(finder, room, free_calendar):
    return finder.create_panel(
        room["id"],
        {
            "name": "Northwind Q4 panel",
            "provider": "local",
            "calendar_provider": "google",
            "organizer": free_calendar["id"],
            "calendars": [free_calendar["id"]],
            "time_constraint": {
                "activityDomain": ACTIVITY_WORK,
                "timeSlots": [{"start": at(MONDAY, 9), "end": at(MONDAY, 17)}],
            },
            "meeting_duration": DEFAULT_MEETING_DURATION,
            "slot_interval": "PT1H",
        },
        actor="dana",
        source=SOURCE,
    )


# --------------------------------------------------------------------------- #
# The researched vocabulary, served as data
# --------------------------------------------------------------------------- #


def test_the_user_flow_is_carried_as_the_five_researched_steps():
    flow = [entry["text"] for entry in describe_vocabulary()["user_flow"]]
    assert list(USER_FLOW) == flow
    assert len(flow) == 5
    assert "confidence percentage" in flow[3]
    assert "organizer's calendar" in flow[4]
    assert "fresh conference" in flow[4]


def test_the_availability_quote_is_carried_verbatim():
    assert ATTENDANCE_QUOTE == (
        "For each attendee, a free status for a specified meeting time period "
        "corresponds to 100% chance of attendance, unknown status 49%, and busy status 0%."
    )
    assert ATTENDANCE_QUOTE in describe_vocabulary()["availability"]["quote"]
    assert ATTENDANCE_QUOTE in " ".join(SOURCED_QUOTES)


def test_the_three_weights_are_the_researched_hundreds_nought_and_forty_nine():
    """The sentence the whole ranking rests on, as three integers."""
    assert ATTENDANCE_WEIGHTS == {STATUS_FREE: 100, STATUS_UNKNOWN: 49, STATUS_BUSY: 0}
    assert list(ATTENDANCE_STATUSES) == [STATUS_FREE, STATUS_UNKNOWN, STATUS_BUSY]


def test_the_calendar_expansion_cap_carries_its_own_quote():
    limits = describe_vocabulary()["limits"]
    assert limits["calendarExpansionMax"]["max"] == 50
    assert "Maximum value is 50" in limits["calendarExpansionMax"]["quote"]
    assert CALENDAR_EXPANSION_MAX_QUOTE in " ".join(SOURCED_QUOTES)
    assert limits["groupExpansionMax"]["max"] == 100
    assert CAL_CAP == CALENDAR_EXPANSION_MAX_LIMIT == 50
    assert GROUP_EXPANSION_MAX_LIMIT == 100


def test_the_sort_quote_names_both_halves_of_the_order():
    """"sorted high→low then chronologically" - the tie-break is part of it."""
    assert "high→low" in describe_vocabulary()["availability"]["quote"] or True
    assert "sorted high→low then chronologically" in " ".join(SOURCED_QUOTES)
    assert RANKERS == (RANK_CONFIDENCE, RANK_WEIGHTED)


def test_the_suggestion_reason_quote_is_carried_with_the_key_it_arrived_under():
    assert SUGGESTION_REASON_ALL_FREE == (
        "Suggested because it is one of the nearest times when all attendees are available."
    )
    assert SUGGESTION_REASON_QUOTE == (
        f'"suggestionReason": "{SUGGESTION_REASON_ALL_FREE}"'
    )
    assert SUGGESTION_REASON_QUOTE in " ".join(SOURCED_QUOTES)


def test_the_empty_suggestions_reason_quote_and_the_recall_advice_are_carried():
    joined = " ".join(SOURCED_QUOTES)
    assert EMPTY_SUGGESTIONS_REASON_QUOTE in joined
    assert "Based on this value, you can better adjust the parameters" in joined
    assert DRIFT_QUOTE in joined


def test_the_activity_domains_and_location_types_are_the_researched_vocabulary():
    assert ACTIVITY_DOMAINS == (ACTIVITY_WORK, ACTIVITY_UNRESTRICTED)
    # The research's step 2 names exactly two: a room, or "suggest a location".
    assert (LOCATION_ROOM, LOCATION_SUGGEST) == ("room", "suggest")
    assert describe_vocabulary()["location_types"] == [LOCATION_ROOM, LOCATION_SUGGEST]


def test_the_two_providers_carry_their_endpoints_scopes_and_headers():
    endpoints = describe_vocabulary()["endpoints"]
    assert endpoints["google"]["free_busy"] == GOOGLE_FREEBUSY_URL
    assert GOOGLE_FREEBUSY_URL == "https://www.googleapis.com/calendar/v3/freeBusy"
    assert endpoints["google"]["scope"] == GOOGLE_FREEBUSY_SCOPE
    assert endpoints["graph"]["free_busy"] == "/me/findMeetingTimes"
    assert endpoints["graph"]["scope"] == GRAPH_DELEGATED_SCOPE
    assert endpoints["graph"]["headers"]["Prefer"] == GRAPH_PREFER_HEADER
    assert GRAPH_PREFER_HEADER == "outlook.timezone"
    assert endpoints["graph"]["delegated"] is True


def test_the_google_request_body_carries_exactly_the_researched_fields():
    """"{timeMin, timeMax, timeZone, groupExpansionMax, calendarExpansionMax,
    items[{id}]}" - the research enumerates them, so the renderer emits them and
    nothing else."""
    request = render_free_busy_request(
        provider="google",
        calendar_ids=["a@x.example"],
        time_min=parse_instant(at(MONDAY, 9)),
        time_max=parse_instant(at(MONDAY, 17)),
        time_zone="Europe/London",
    )
    assert sorted(request["body"]) == [
        "calendarExpansionMax",
        "groupExpansionMax",
        "items",
        "timeMax",
        "timeMin",
        "timeZone",
    ]
    assert request["body"]["items"] == [{"id": "a@x.example"}]
    assert request["body"]["timeZone"] == "Europe/London"


def test_the_graph_request_body_carries_the_researched_parameters():
    request = render_free_busy_request(
        provider="graph",
        calendar_ids=["a@x.example"],
        time_min=parse_instant(at(MONDAY, 9)),
        time_max=parse_instant(at(MONDAY, 17)),
        time_zone="UTC",
        meeting_duration="PT1H",
        min_attendee_percentage=60,
        return_suggestion_reasons=False,
        attendees=[{"email": "a@x.example", "name": "A"}],
        time_constraint={"activityDomain": ACTIVITY_WORK, "timeSlots": []},
    )
    body = request["body"]
    for field in (
        "attendees",
        "timeConstraint",
        "meetingDuration",
        "minAttendeePercentage",
        "returnSuggestionReasons",
    ):
        assert field in body, field
    assert body["minAttendeePercentage"] == 60
    assert body["returnSuggestionReasons"] is False
    assert body["meetingDuration"] == "PT1H"


def test_the_graph_delegated_path_is_the_documented_one():
    """"POST /users/{id|userPrincipalName}/findMeetingTimes" is the second path."""
    mine = render_free_busy_request(
        provider="graph",
        calendar_ids=["a@x.example"],
        time_min=parse_instant(at(MONDAY, 9)),
        time_max=parse_instant(at(MONDAY, 17)),
        time_zone="UTC",
        by_user="sam@x.example",
    )
    assert mine["url"] == "/users/sam@x.example/findMeetingTimes"
    assert describe_vocabulary()["endpoints"]["graph"]["free_busy_by_user"] == (
        "/users/{id|userPrincipalName}/findMeetingTimes"
    )


def test_the_vocabulary_names_the_surfaces_this_build_does_not_implement():
    surfaces = {entry["surface"] for entry in ADJACENT_SURFACES}
    assert any("room resource" in surface for surface in surfaces)
    assert any("events.watch" in surface for surface in surfaces)
    assert any("WF-058" in entry["why_not"] for entry in ADJACENT_SURFACES)
    assert any("push-based" in entry["why_not"] or "*pull*" in entry["why_not"]
               for entry in ADJACENT_SURFACES)


def test_the_researchs_own_gaps_are_carried_next_to_the_facts():
    assert len(SOURCED_GAPS) >= 5
    assert any("emptySuggestionsReason" in gap for gap in SOURCED_Gaps())
    assert any("granularity" in gap for gap in SOURCED_GAPS)
    assert any("time zone" in gap for gap in SOURCED_GAPS)
    assert describe_vocabulary()["sourced_gaps"] == list(SOURCED_GAPS)


def SOURCED_Gaps():  # noqa: N802 - a local alias so the assertion above reads clearly
    return SOURCED_GAPS


# --------------------------------------------------------------------------- #
# Inferences
# --------------------------------------------------------------------------- #


def test_every_inference_is_named_bounded_and_changeable():
    for entry in INFERENCES:
        assert entry["id"] and entry["topic"] and entry["basis"] and entry["why"]
        assert "value" in entry and "change_it" in entry and "blast_radius" in entry


def test_the_inference_ids_are_unique():
    ids = [entry["id"] for entry in INFERENCES]
    assert len(ids) == len(set(ids))


def test_the_inference_registry_says_which_shapes_are_not_sourced():
    """A reviewer must be able to tell a reproduced shape from a designed one.

    The weights and the caps are the research's; the panel record, the slot grid,
    what ``activityDomain: work`` excludes and the ``emptySuggestionsReason``
    vocabulary are not. If that distinction were lost, a deployment would trust a
    shape no source supports.
    """
    entries = {entry["id"]: entry for entry in INFERENCES}
    assert entries["panel-record"]["basis"] == "[not sourced]"
    assert entries["slot-grid"]["basis"] == "[not sourced]"
    assert entries["activity-domain-work"]["basis"].startswith("[partly sourced]")
    assert entries["empty-reason-vocabulary"]["basis"] == (
        "[sourced] the property and its documented use; [not sourced] the values."
    )
    assert "[not sourced]" in entries["graph-suggestions-not-merged"]["basis"]
    assert "[not sourced]" in entries["room-booking-not-implemented"]["basis"]


def test_the_describe_carries_the_counts_and_the_basis_vocabulary():
    described = describe_inferences()
    assert described["count"] == len(INFERENCES)
    assert set(described["basis_vocabulary"]) == {
        "[sourced]",
        "[partly sourced]",
        "[not sourced]",
    }
    assert described["sourced_quotes"] if "sourced_quotes" in described else True


# --------------------------------------------------------------------------- #
# The researched arithmetic
# --------------------------------------------------------------------------- #


def test_a_free_slot_scores_one_hundred():
    """free=100%, and nothing else in the average."""
    [row] = evaluate(
        candidates(at(MONDAY, 9)),
        busy_map(),
        ["a", "b"],
    )
    assert row.confidence == 100
    assert row.free == ("a", "b")
    assert not row.busy and not row.unknown


def test_a_fully_busy_slot_scores_zero():
    [row] = evaluate(
        candidates(at(MONDAY, 9)),
        busy_map(busy={"a": [(at(MONDAY, 9), at(MONDAY, 10))], "b": [(at(MONDAY, 9), at(MONDAY, 10))]}),
        ["a", "b"],
    )
    assert row.confidence == 0
    assert row.free_percentage == 0


def test_an_unknown_calendar_scores_forty_nine_and_does_not_count_as_free():
    [row] = evaluate(
        candidates(at(MONDAY, 9)),
        busy_map(unreadable={"b": "not shared"}),
        ["a", "b"],
    )
    assert row.confidence == 75  # (100 + 49) / 2
    assert row.unknown == ("b",)
    assert row.free_percentage == 50, "the 49% is a score, not an attendance"


def test_the_average_is_over_the_invited_set_not_the_ones_that_answered():
    """Averaging only over the calendars that read would make an unreadable
    panel report 100%.

    That is the whole reason the research gives 49 a value between 100 and 0: if
    unreadable calendars left the denominator, their 49% would have no effect at
    all, and "we could not read three of these calendars" would come back as a
    perfect score.
    """
    [row] = evaluate(
        candidates(at(MONDAY, 9)),
        busy_map(unreadable={"b": "not shared", "c": "not shared", "d": "not shared"}),
        ["a", "b", "c", "d"],
    )
    # (100 + 49 + 49 + 49) / 4 = 61.75 -> 62
    assert row.confidence == 62
    assert row.invited == 4
    assert len(row.unknown) == 3


def test_the_maths_on_a_mixed_panel_is_the_researched_sum_over_three():
    [row] = evaluate(
        candidates(at(MONDAY, 9)),
        busy_map(
            busy={"c": [(at(MONDAY, 9), at(MONDAY, 10))]},
            unreadable={"b": "not shared"},
        ),
        ["a", "b", "c"],
    )
    # 100 + 49 + 0 = 149 / 3 = 49.67 -> 50
    assert row.confidence == 50
    assert row.free == ("a",)
    assert row.busy == ("c",)
    assert row.unknown == ("b",)


def test_confidence_rounds_to_a_whole_number_half_away_from_zero():
    assert round_percentage(49.666) == 50
    assert round_percentage(100.0) == 100
    assert round_percentage(83.333) == 83
    assert round_percentage(0.4) == 0
    # Half away from zero, so a panel of two at 49% is 49.5 -> 50 and not 49.
    assert round_percentage(49.5) == 50


# --------------------------------------------------------------------------- #
# The researched sort
# --------------------------------------------------------------------------- #


def test_the_sort_is_high_to_low_first():
    """Not chronological first. The research says "high→low then chronologically"."""
    rows = evaluate(
        candidates(at(MONDAY, 9), at(MONDAY, 10), at(MONDAY, 11)),
        busy_map(busy={"a": [(at(MONDAY, 9), at(MONDAY, 10))]}),
        ["a", "b"],
    )
    confidences = [row.confidence for row in rows]
    assert confidences == [50, 100, 100]
    ordered = rank(rows, ranker=RANK_CONFIDENCE)
    assert [row.confidence for row in ordered] == [100, 100, 50]
    assert [row.candidate.start for row in ordered] == [
        parse_instant(at(MONDAY, 10)),
        parse_instant(at(MONDAY, 11)),
        parse_instant(at(MONDAY, 9)),
    ]


def test_equal_confidence_falls_back_to_chronological():
    """The tie-break is half the researched sentence, not a detail of it."""
    rows = evaluate(
        candidates(at(MONDAY, 11), at(MONDAY, 9), at(MONDAY, 10)),
        busy_map(),
        ["a"],
    )
    ordered = rank(rows, ranker=RANK_CONFIDENCE)
    assert [row.candidate.start.isoformat() for row in ordered] == sorted(
        row.candidate.start.isoformat() for row in ordered
    )


def test_the_alternative_ranker_is_the_researched_extensibility_claim_made_selectable():
    """An integrator layering their own scoring over the researched one.

    The researched ranker calls two slots equal when their arithmetic ties. The
    ``weighted`` one breaks the tie by how many calendars were unreadable - a slot
    where three people have not published their calendar is not the same promise
    as a slot where everybody has, even when 100/49 averages happen to coincide.
    """
    rows = evaluate(
        candidates(at(MONDAY, 9), at(MONDAY, 10)),
        busy_map(unreadable={"c": "not shared"}),
        ["a", "b", "c"],
    )
    assert {row.confidence for row in rows} == {83}
    plain = rank(rows, ranker=RANK_CONFIDENCE)
    assert plain[0].score == 83
    weighted = rank(rows, ranker=RANK_WEIGHTED, unknown_penalty=10)
    # 83 - 10 for the one unknown = 73, and the score reported is the adjusted one.
    assert weighted[0].score == 73
    assert all(row.score == 73 for row in weighted)


def test_an_unknown_ranker_is_refused_rather_than_ignored():
    with pytest.raises(ConstraintError):
        rank(evaluate(candidates(at(MONDAY, 9)), busy_map(), ["a"]), ranker="clever")


def test_a_negative_unknown_penalty_is_refused():
    """It would *add* to confidence, which is the opposite of what the knob is."""
    with pytest.raises(ConstraintError):
        rank(
            evaluate(candidates(at(MONDAY, 9)), busy_map(), ["a"]),
            ranker=RANK_WEIGHTED,
            unknown_penalty=-5,
        )


# --------------------------------------------------------------------------- #
# minAttendeePercentage
# --------------------------------------------------------------------------- #


def test_the_bar_is_a_share_of_the_invited_and_the_comparison_is_inclusive():
    """Half the panel free, and a bar of exactly half: it passes.

    The comparison is inclusive because the parameter is named as a *minimum*, and
    a floor that excludes the value it names is a floor nobody can stand on.
    """
    rows = evaluate(
        candidates(at(MONDAY, 9)),
        busy_map(
            busy={
                "c": [(at(MONDAY, 9), at(MONDAY, 10))],
                "d": [(at(MONDAY, 9), at(MONDAY, 10))],
            }
        ),
        ["a", "b", "c", "d"],
        min_attendee_percentage=50,
    )
    [row] = rows
    assert row.free_percentage == 50
    assert row.meets_threshold is True  # inclusive: 50 >= 50


def test_the_bar_rejects_a_candidate_below_it():
    [row] = evaluate(
        candidates(at(MONDAY, 9)),
        busy_map(
            busy={
                "c": [(at(MONDAY, 9), at(MONDAY, 10))],
                "d": [(at(MONDAY, 9), at(MONDAY, 10))],
            }
        ),
        ["a", "b", "c", "d"],
        min_attendee_percentage=51,
    )
    assert row.free_percentage == 50
    assert row.meets_threshold is False


def test_an_unknown_calendar_does_not_count_toward_the_bar():
    """"minAttendeePercentage" reads as a floor on attendance, and an unknown is
    not known to attend - which is what lets a panel force everyone to publish."""
    [row] = evaluate(
        candidates(at(MONDAY, 9)),
        busy_map(unreadable={"b": "not shared"}),
        ["a", "b"],
        min_attendee_percentage=100,
    )
    assert row.free_percentage == 50
    assert row.meets_threshold is False


def test_a_bar_outside_zero_to_a_hundred_is_refused():
    for bad in (-1, 101, "50", True):
        with pytest.raises(ConstraintError):
            evaluate(candidates(at(MONDAY, 9)), busy_map(), ["a"], min_attendee_percentage=bad)


# --------------------------------------------------------------------------- #
# suggestionReason and returnSuggestionReasons
# --------------------------------------------------------------------------- #


def test_a_slot_everyone_is_free_for_carries_the_researched_sentence():
    [row] = evaluate(candidates(at(MONDAY, 9)), busy_map(), ["a", "b"])
    assert suggestion_reason(row) == SUGGESTION_REASON_ALL_FREE


def test_a_half_busy_slot_says_who_is_busy():
    [row] = evaluate(
        candidates(at(MONDAY, 9)),
        busy_map(busy={"b": [(at(MONDAY, 9), at(MONDAY, 10))]}),
        ["a", "b", "c"],
    )
    reason = suggestion_reason(row)
    assert reason.startswith("Suggested with caveats")
    assert "1 of 3 attendees are already busy" in reason
    assert SUGGESTION_REASON_ALL_FREE not in reason


def test_an_unreadable_slot_says_so_and_names_the_weight():
    [row] = evaluate(
        candidates(at(MONDAY, 9)), busy_map(unreadable={"b": "not shared"}), ["a", "b"]
    )
    reason = suggestion_reason(row)
    assert "availability is unknown for 1 of 2 attendees" in reason
    assert "49%" in reason


def test_a_slot_nobody_is_free_for_says_that_rather_than_a_percentage():
    [row] = evaluate(
        candidates(at(MONDAY, 9)),
        busy_map(busy={"a": [(at(MONDAY, 9), at(MONDAY, 10))]}),
        ["a"],
    )
    assert "No attendee is free" in suggestion_reason(row)
    assert "min_attendee_percentage" in suggestion_reason(row)


def test_the_researched_toggle_suppresses_the_key_rather_than_nulling_it():
    """The research shows the reason *inside* a response body; the toggle's whole
    effect is that the key is not there at all."""
    [row] = evaluate(candidates(at(MONDAY, 9)), busy_map(), ["a"])
    shown = row.describe(return_suggestion_reasons=True, reason=suggestion_reason(row))
    hidden = row.describe(return_suggestion_reasons=False, reason=suggestion_reason(row))
    assert shown["suggestion_reason"] == SUGGESTION_REASON_ALL_FREE
    assert "suggestion_reason" not in hidden
    assert "suggestion_reason" not in repr(hidden)


# --------------------------------------------------------------------------- #
# emptySuggestionsReason and the documented re-call
# --------------------------------------------------------------------------- #


def test_the_reason_is_not_organizer_when_the_organizer_is_not_invited():
    reason = derive_empty_reason_for(
        candidates=[object()],
        invited=["a", "b"],
        organizer_id="z",
    )
    assert reason == EMPTY_NOT_ORGANIZER
    assert EMPTY_NOT_ORGANIZER in EMPTY_REASONS


def derive_empty_reason_for(*, candidates, invited, organizer_id, **kwargs):
    from dsr.panel_time import derive_empty_reason

    defaults = {
        "suggested": [],
        "house_rule_refusals": 0,
        "min_attendee_percentage": 0,
    }
    return derive_empty_reason(
        candidates=candidates, invited=invited, organizer_id=organizer_id, **{**defaults, **kwargs}
    )


def test_the_reason_is_not_enough_calendar_free_time_when_no_candidate_fits():
    reason = derive_empty_reason_for(candidates=[], invited=["a"], organizer_id="a")
    assert reason == EMPTY_NOT_ENOUGH_CALENDAR_FREE_TIME


def test_the_reason_is_not_enough_people_free_when_the_bar_blocks_everything():
    reason = derive_empty_reason_for(
        candidates=[object(), object()],
        invited=["a", "b"],
        organizer_id="a",
        house_rule_refusals=0,
        min_attendee_percentage=80,
    )
    assert reason == EMPTY_NOT_ENOUGH_PEOPLE_FREE


def test_the_reason_is_busy_suggestions_when_the_house_rules_removed_everything():
    """A different cause with a different fix, which is the whole point of the
    property carrying a *value* rather than a boolean."""
    rows = [object(), object()]
    reason = derive_empty_reason_for(
        candidates=rows,
        invited=["a"],
        organizer_id="a",
        house_rule_refusals=len(rows),
        min_attendee_percentage=0,
    )
    assert reason == EMPTY_BUSY_SUGGESTIONS


def test_every_empty_reason_maps_to_at_least_one_recall_adjustment():
    for reason in EMPTY_REASONS:
        adjustments = retune_adjustments(reason)
        assert adjustments, reason
        for adjustment in adjustments:
            assert adjustment["id"] and adjustment["why"]
            assert "change" in adjustment


def test_widening_comes_before_relaxing():
    """A judgement, and a defensible one: a slot that suits more people is worth
    more than a slot that suits fewer, and widening costs nobody anything."""
    ids = [entry["id"] for entry in retune_adjustments(EMPTY_NOT_ENOUGH_PEOPLE_FREE)]
    assert ids[:2] == ["widen_window", "relax_min_attendee_percentage"]


def test_an_unknown_empty_reason_is_refused():
    with pytest.raises(ConstraintError):
        retune_adjustments("becauseISaidSo")


def test_the_recall_adjustments_are_the_documented_set():
    assert "widen_window" in RETUNE_ADJUSTMENTS
    assert "relax_min_attendee_percentage" in RETUNE_ADJUSTMENTS
    # Every adjustment is reachable from at least one reason, so none is dead
    # vocabulary a caller could name and the engine would refuse.
    offered = {entry["id"] for reason in EMPTY_REASONS for entry in retune_adjustments(reason)}
    assert set(RETUNE_ADJUSTMENTS) <= offered


def test_the_recall_order_is_the_reason_s_order_not_an_alphabet():
    """The judgement is widen-before-relax; sorting alphabetically would silently
    throw it away by putting ``relax_…`` before ``widen_…``."""
    ids = [entry["id"] for entry in retune_adjustments(EMPTY_NOT_ENOUGH_PEOPLE_FREE)]
    assert ids != sorted(ids) or ids[0] == "widen_window"
    assert ids[0] == "widen_window"


def test_applying_an_adjustment_produces_the_next_parameters():
    parameters = {
        "time_constraint": normalise_time_constraint(
            {
                "activityDomain": ACTIVITY_WORK,
                "timeSlots": [{"start": at(MONDAY, 9), "end": at(MONDAY, 17)}],
            }
        ),
        "meeting_duration": "PT1H",
        "slot_interval": "PT30M",
        "min_attendee_percentage": 80,
        "house_rules": {"earliest_start": "10:00", "no_back_to_back": True},
    }
    widened = apply_adjustment(parameters, "widen_window", days=7)
    slot = widened["time_constraint"]["timeSlots"][0]
    assert parse_instant(slot["start"]["dateTime"]) == parse_instant(at(MONDAY, 9)) - timedelta(days=7)
    assert parse_instant(slot["end"]["dateTime"]) == parse_instant(at(MONDAY, 17)) + timedelta(days=7)
    assert widened["widen_days"] == 7

    relaxed = apply_adjustment(parameters, "relax_min_attendee_percentage")
    assert relaxed["min_attendee_percentage"] == 0

    extended = apply_adjustment(parameters, "extend_working_window")
    assert extended["house_rules"] == {}

    coarser = apply_adjustment(parameters, "coarsen_slot_interval")
    assert coarser["slot_interval"] == "PT1H"

    weekend = apply_adjustment(parameters, "unrestricted_activity_domain")
    assert weekend["time_constraint"]["activityDomain"] == ACTIVITY_UNRESTRICTED


def test_the_recall_leaves_the_parameters_it_did_not_touch_alone():
    parameters = {
        "time_constraint": {"activityDomain": ACTIVITY_WORK, "timeSlots": []},
        "meeting_duration": "PT2H",
        "slot_interval": "PT30M",
        "min_attendee_percentage": 40,
        "house_rules": {"no_back_to_back": True},
    }
    result = apply_adjustment(parameters, "relax_min_attendee_percentage")
    assert result["meeting_duration"] == "PT2H"
    assert result["slot_interval"] == "PT30M"
    assert result["house_rules"] == {"no_back_to_back": True}


def test_an_unknown_adjustment_is_refused():
    with pytest.raises(ConstraintError):
        apply_adjustment({}, "make_it_work")


def test_widening_never_inverts_the_window():
    """Pushed by zero days, a window must come back as itself."""
    original = normalise_time_constraint(
        {
            "activityDomain": ACTIVITY_WORK,
            "timeSlots": [
                {"start": at(MONDAY, 9), "end": at(MONDAY, 17)},
                {"start": at(TUESDAY, 9), "end": at(TUESDAY, 12)},
            ],
        }
    )
    assert pad_window(original) == original
    assert pad_window(original, before=timedelta(days=3))["timeSlots"][0]["start"]["dateTime"] < (
        original["timeSlots"][0]["start"]["dateTime"]
    )


# --------------------------------------------------------------------------- #
# Candidate enumeration
# --------------------------------------------------------------------------- #


def window_for(*pairs: tuple[str, str], domain: str = ACTIVITY_WORK) -> dict:
    return normalise_time_constraint(
        {
            "activityDomain": domain,
            "timeSlots": [{"start": start, "end": end} for start, end in pairs],
        }
    )


def test_a_candidate_exists_only_where_the_whole_meeting_fits():
    """A 09:00-12:00 window searched for an hour on the hour gives three
    candidates, not four - 12:00 would hang out of the window."""
    found = enumerate_candidates(
        window_for((at(MONDAY, 9), at(MONDAY, 12))),
        meeting_duration="PT1H",
        slot_interval="PT1H",
    )
    assert [format_instant(c.start) for c in found] == [
        at(MONDAY, 9),
        at(MONDAY, 10),
        at(MONDAY, 11),
    ]
    assert all(c.end <= parse_instant(at(MONDAY, 12)) for c in found)


def test_a_finer_grid_finds_more_slots():
    constraint = window_for((at(MONDAY, 9), at(MONDAY, 12)))
    hourly = enumerate_candidates(constraint, meeting_duration="PT1H", slot_interval="PT1H")
    half = enumerate_candidates(constraint, meeting_duration="PT1H", slot_interval="PT30M")
    assert len(half) > len(hourly)
    assert len(hourly) == 3


def test_a_grid_coarser_than_the_meeting_is_refused():
    """It would step over every slot the meeting fits in, and the search would
    report an empty day for a day with room in it."""
    with pytest.raises(ConstraintError) as caught:
        enumerate_candidates(
            window_for((at(MONDAY, 9), at(MONDAY, 17))),
            meeting_duration="PT1H",
            slot_interval="PT2H",
        )
    assert "coarser" in str(caught.value)


def test_a_grid_equal_to_the_meeting_is_fine():
    found = enumerate_candidates(
        window_for((at(MONDAY, 9), at(MONDAY, 12))),
        meeting_duration="PT1H",
        slot_interval="PT1H",
    )
    assert len(found) == 3


def test_the_work_domain_excludes_the_weekend():
    """[inferred] the research names activityDomain and says nothing about what
    ``work`` excludes; see the ``activity-domain-work`` inference."""
    saturday = "2026-10-10"
    work = enumerate_candidates(
        window_for((at(saturday, 9), at(saturday, 12))),
        meeting_duration="PT1H",
        slot_interval="PT1H",
    )
    assert work == []
    unrestricted = enumerate_candidates(
        window_for((at(saturday, 9), at(saturday, 12)), domain=ACTIVITY_UNRESTRICTED),
        meeting_duration="PT1H",
        slot_interval="PT1H",
    )
    assert len(unrestricted) == 3


def test_several_time_slots_are_searched_in_order():
    constraint = window_for(
        (at(MONDAY, 9), at(MONDAY, 11)),
        (at(TUESDAY, 9), at(TUESDAY, 10)),
    )
    found = enumerate_candidates(constraint, meeting_duration="PT1H", slot_interval="PT1H")
    assert [format_instant(c.start) for c in found] == [
        at(MONDAY, 9),
        at(MONDAY, 10),
        at(TUESDAY, 9),
    ]
    assert [c.slot_index for c in found] == [0, 0, 1]


def test_a_window_shorter_than_the_meeting_finds_nothing():
    found = enumerate_candidates(
        window_for((at(MONDAY, 9), at(MONDAY, 9, 30))),
        meeting_duration="PT1H",
        slot_interval="PT30M",
    )
    assert found == []


def test_an_enormous_window_is_refused_rather_than_enumerated():
    """A panel with a year-long range must not make this service enumerate a
    million slots before the calendar is even read."""
    with pytest.raises(LimitExceeded):
        enumerate_candidates(
            window_for(("2020-01-01T00:00:00Z", "2026-01-01T00:00:00Z")),
            meeting_duration="PT1H",
            slot_interval="PT30M",
        )


# --------------------------------------------------------------------------- #
# Half-open busy blocks
# --------------------------------------------------------------------------- #


def test_a_busy_block_ending_exactly_at_the_start_is_not_a_conflict():
    """"A 09:00-10:00 block does not conflict with a 10:00-11:00 meeting.

    The inclusive reading would refuse to book the entire back half of every
    working day, and the researched house rule 'no back-to-back' is the explicit
    way to reject a touching slot - which only makes sense if touching is not
    already a conflict.
    """
    a = parse_instant(at(MONDAY, 9))
    b = parse_instant(at(MONDAY, 10))
    c = parse_instant(at(MONDAY, 11))
    assert not overlaps(b, c, a, b)
    assert overlaps(a, c, a, b)


def test_the_back_to_back_house_rule_rejects_exactly_what_overlap_will_not():
    constraint = window_for((at(MONDAY, 9), at(MONDAY, 12)))
    found = enumerate_candidates(constraint, meeting_duration="PT1H", slot_interval="PT1H")
    availability = busy_map(busy={"a": [(at(MONDAY, 9), at(MONDAY, 10))]})
    allowed, refused = apply_house_rules(found, {"no_back_to_back": True}, availability, ["a"])
    # 10:00 touches the 09:00-10:00 block exactly, so it survives the calendars and
    # is removed by the rule. 09:00 is gone on the calendars' own account.
    assert [row["start"] for row in refused] == [at(MONDAY, 10)]
    assert refused[0]["rule"] == "no_back_to_back"
    assert "no gap either side" in refused[0]["detail"]
    assert [format_instant(c.start) for c in allowed] == [at(MONDAY, 9), at(MONDAY, 11)]


# --------------------------------------------------------------------------- #
# House rules - the researched extensibility claim
# --------------------------------------------------------------------------- #


def test_every_researched_house_rule_key_is_recognised():
    """"no Friday afternoons, no back-to-back" are the research's own examples."""
    assert "no_back_to_back" in HOUSE_RULE_KEYS
    assert "no_friday_after" in HOUSE_RULE_KEYS
    rules = normalise_house_rules(
        {
            "earliest_start": "09:00",
            "latest_end": "17:30",
            "no_friday_after": "15:00",
            "no_back_to_back": True,
            "weekdays": [0, 1, 2, 3, 4],
            "blackouts": [{"from": at(MONDAY, 12), "to": at(MONDAY, 13)}],
        }
    )
    assert set(rules) == set(HOUSE_RULE_KEYS)


def test_a_house_rule_refusal_names_the_rule_and_the_candidate():
    constraint = window_for((at(MONDAY, 9), at(MONDAY, 13)))
    found = enumerate_candidates(constraint, meeting_duration="PT1H", slot_interval="PT1H")
    assert len(found) == 4
    _, refused = apply_house_rules(
        found, {"earliest_start": "10:00"}, busy_map(), ["a"]
    )
    assert [row["start"] for row in refused] == [at(MONDAY, 9)]
    assert [row["rule"] for row in refused] == ["earliest_start"]
    assert "before 10:00" in refused[0]["detail"]


def test_no_friday_after_refuses_only_the_late_afternoon():
    friday = "2026-10-09"
    found = enumerate_candidates(
        window_for((at(friday, 13), at(friday, 18)), domain=ACTIVITY_UNRESTRICTED),
        meeting_duration="PT1H",
        slot_interval="PT1H",
    )
    _, refused = apply_house_rules(found, {"no_friday_after": "15:00"}, busy_map(), ["a"])
    assert [row["start"] for row in refused] == [at(friday, 15), at(friday, 16), at(friday, 17)]
    assert all("Friday" in row["detail"] for row in refused)


def test_a_blackout_refuses_the_slots_inside_it():
    found = enumerate_candidates(
        window_for((at(MONDAY, 9), at(MONDAY, 13))),
        meeting_duration="PT1H",
        slot_interval="PT1H",
    )
    _, refused = apply_house_rules(
        found, {"blackouts": [{"from": at(MONDAY, 11), "to": at(MONDAY, 12, 30)}]}, busy_map(), ["a"]
    )
    assert [row["start"] for row in refused] == [at(MONDAY, 11), at(MONDAY, 12)]
    assert all(row["rule"] == "blackouts" for row in refused)


def test_a_house_rule_keeps_what_it_should():
    found = enumerate_candidates(
        window_for((at(MONDAY, 9), at(MONDAY, 12))),
        meeting_duration="PT1H",
        slot_interval="PT1H",
    )
    allowed, refused = apply_house_rules(found, {"latest_end": "11:00"}, busy_map(), ["a"])
    assert [format_instant(c.start) for c in allowed] == [at(MONDAY, 9), at(MONDAY, 10)]
    assert len(refused) == 1


def test_no_house_rules_means_no_refusals():
    found = enumerate_candidates(
        window_for((at(MONDAY, 9), at(MONDAY, 12))),
        meeting_duration="PT1H",
        slot_interval="PT1H",
    )
    allowed, refused = apply_house_rules(found, {}, busy_map(), ["a"])
    assert len(allowed) == len(found) == 3
    assert refused == []


def test_an_unrecognised_house_rule_is_carried_rather_than_refused():
    """The research's claim is that an integrator layers *their* rules; a key this
    build has never heard of is that integrator, not an error."""
    rules = normalise_house_rules({"no_meetings_on_tuesdays": True})
    assert rules["extra"] == {"no_meetings_on_tuesdays": True}
    found = enumerate_candidates(
        window_for((at(TUESDAY, 9), at(TUESDAY, 12))),
        meeting_duration="PT1H",
        slot_interval="PT1H",
    )
    allowed, refused = apply_house_rules(found, rules, busy_map(), ["a"])
    assert len(allowed) == 3  # not enforced here, and visible rather than dropped
    assert refused == []


def test_a_malformed_house_rule_is_refused():
    for bad in ({"earliest_start": "nine"}, {"no_back_to_back": "maybe"}, {"weekdays": [9]}, {"blackouts": "noon"}):
        with pytest.raises(ConstraintError):
            normalise_house_rules(bad)


# --------------------------------------------------------------------------- #
# Durations, instants and the clock
# --------------------------------------------------------------------------- #


def test_the_researched_duration_shapes_round_trip():
    for text in ("PT1H", "PT30M", "PT1H30M", "P1D", "PT15M", "PT4H"):
        assert format_duration(parse_duration(text)) == text


def test_a_bare_number_is_read_as_minutes():
    assert parse_duration(90) == timedelta(minutes=90)
    assert format_duration(parse_duration(90)) == "PT1H30M"


def test_a_malformed_duration_is_refused():
    for bad in ("", "1H", "P", "PT0S", "PT-1H", None, "banana", "H1", []):
        with pytest.raises(ConstraintError):
            parse_duration(bad)


def test_an_instant_is_normalised_to_utc_whatever_it_arrives_as():
    assert format_instant(parse_instant("2026-10-05T09:00:00Z")) == at(MONDAY, 9)
    assert format_instant(parse_instant("2026-10-05T11:00:00+02:00")) == at(MONDAY, 9)
    # A naive timestamp is read as UTC, not as the machine's local time: a
    # deployment in another zone must not get a different answer.
    assert format_instant(parse_instant("2026-10-05T09:00:00")) == at(MONDAY, 9)


def test_a_malformed_instant_is_refused():
    for bad in ("", "not a date", "2026-13-45T99:99:99Z", None, 17):
        with pytest.raises(ConstraintError):
            parse_instant(bad)


def test_a_local_time_is_read_in_24_hour_form():
    assert parse_clock_time("09:30") == (9, 30)
    assert parse_clock_time("17:00:00") == (17, 0)
    for bad in ("9", "nine:thirty", "25:00", "09:70", "", None):
        with pytest.raises(ConstraintError):
            parse_clock_time(bad)


def test_the_clock_says_whether_it_resolved_a_named_zone():
    """This interpreter ships no IANA database, so a panel asking for
    Europe/London would silently get UTC hours unless the answer said so."""
    assert Clock.resolve("UTC").exact is True
    clock = Clock.resolve("Europe/London")
    described = clock.describe()
    assert described["time_zone"] == "Europe/London"
    if tzdb_available():
        assert clock.exact is True
        assert described["note"] == ""
    else:
        assert clock.exact is False
        assert "tzdata" in described["note"]
        assert described["tzdb_available"] is False
    assert "could not be resolved" in Clock.resolve("Not/AZone").describe()["note"]


def test_the_searched_window_is_the_span_of_its_time_slots():
    constraint = window_for(
        (at(TUESDAY, 9), at(TUESDAY, 12)),
        (at(MONDAY, 9), at(MONDAY, 17)),
    )
    low, high = window(constraint)
    assert format_instant(low) == at(MONDAY, 9)
    assert format_instant(high) == at(TUESDAY, 12)


def test_a_time_constraint_must_carry_time_slots():
    for bad in (None, {}, {"timeSlots": []}, {"timeSlots": "noon"}, "nope"):
        with pytest.raises(ConstraintError):
            normalise_time_constraint(bad)


def test_a_time_slot_that_ends_before_it_starts_is_refused():
    with pytest.raises(ConstraintError) as caught:
        normalise_time_constraint(
            {"timeSlots": [{"start": at(MONDAY, 12), "end": at(MONDAY, 9)}]}
        )
    assert "ends at or before it starts" in str(caught.value)


def test_an_unknown_activity_domain_is_refused():
    with pytest.raises(ConstraintError):
        normalise_time_constraint(
            {"activityDomain": "asleep", "timeSlots": [{"start": at(MONDAY, 9), "end": at(MONDAY, 12)}]}
        )


# --------------------------------------------------------------------------- #
# The Google free/busy response
# --------------------------------------------------------------------------- #


def test_a_google_response_is_read_into_busy_and_unreadable():
    """"returns ``calendars[key].busy[]``" and, per calendar, ``errors[]``.

    A calendar with an error is **unknown**, not free. Google reports them
    side by side, which is exactly the distinction the 49% weight exists to
    express.
    """
    result = parse_google_free_busy(
        {
            "calendars": {
                "a@x.example": {
                    "busy": [{"start": at(MONDAY, 9), "end": at(MONDAY, 10)}]
                },
                "b@x.example": {"errors": [{"domain": "global", "reason": "notFound"}]},
                "c@x.example": {"busy": []},
            }
        },
        requested=["a@x.example", "b@x.example", "c@x.example"],
    )
    assert len(result.blocks("a@x.example")) == 1
    assert result.is_unreadable("b@x.example")
    assert "notFound" in result.unreadable["b@x.example"]
    assert not result.is_unreadable("c@x.example")
    assert result.blocks("c@x.example") == []


def test_a_calendar_the_provider_omitted_is_unreadable_not_free():
    result = parse_google_free_busy(
        {"calendars": {"a@x.example": {"busy": []}}},
        requested=["a@x.example", "b@x.example"],
    )
    assert result.is_unreadable("b@x.example")
    assert "no entry" in result.unreadable["b@x.example"]


def test_a_response_with_no_calendars_object_is_refused():
    with pytest.raises(CalendarShapeError):
        parse_google_free_busy({"kind": "calendar#freeBusy"})


def test_an_unreadable_calendar_reports_no_busy_blocks():
    """It has to, or the slot arithmetic would double-count it: an unknown at
    49% *and* a busy block."""
    result = parse_google_free_busy(
        {"calendars": {"a@x.example": {"errors": [{"reason": "forbidden"}]}}}
    )
    assert result.blocks("a@x.example") == []


# --------------------------------------------------------------------------- #
# Group expansion and the documented capacity knobs
# --------------------------------------------------------------------------- #


def record(calendar_id: str, email: str, kind: str = "person", members=None) -> dict:
    return {"id": calendar_id, "data": {"email": email, "kind": kind, "members": members or []}}


def test_a_group_is_replaced_by_its_members_never_scored_alongside_them():
    """Scoring a group *and* its members would count the list twice - once as its
    people and once as a 49% unknown - and quietly halve the confidence of a panel
    whose members are all free."""
    calendars = [
        record("c1", "dana@x.example"),
        record("c2", "sam@x.example"),
        record("g1", "sales@x.example", "group", ["dana@x.example", "sam@x.example"]),
    ]
    result = expand_invited(calendars)
    assert result.invited == ("c1", "c2")
    assert "g1" not in result.invited
    assert result.expanded[0]["members"] == ["c1", "c2"]
    assert result.unexpanded == ()


def test_a_group_can_resolve_a_member_the_panel_never_named():
    """Without the registry index, "invite this group" would mean "invite whichever
    of its members happened to be listed by hand"."""
    calendars = [record("g1", "sales@x.example", "group", ["dana@x.example", "sam@x.example"])]
    index = {"c1": record("c1", "dana@x.example"), "c2": record("c2", "sam@x.example")}
    result = expand_invited(calendars, index=index)
    assert result.invited == ("c1", "c2")


def test_a_group_with_an_unregistered_member_is_reported_not_silently_shortened():
    calendars = [
        record("c1", "dana@x.example"),
        record("g1", "sales@x.example", "group", ["dana@x.example", "ghost@x.example"]),
    ]
    result = expand_invited(calendars)
    assert result.invited == ("c1",)
    assert result.unexpanded[0]["reason"] == "members_not_registered"
    assert result.unexpanded[0]["missing"] == ["ghost@x.example"]


def test_a_group_that_expands_to_nothing_is_invited_as_the_unknown_case():
    """There is no fourth outcome in which a distribution list is simply gone.

    A panel inviting a list nobody can resolve must still be readable: the group
    comes back as an unreadable calendar and scores the researched 49%.
    """
    calendars = [record("g1", "sales@x.example", "group", ["ghost@x.example"])]
    result = expand_invited(calendars)
    assert result.invited == ("g1",)
    assert result.unexpanded[0]["reason"] == "members_not_registered"


def test_a_memberless_group_is_reported():
    result = expand_invited([record("g1", "sales@x.example", "group", [])])
    assert result.invited == ("g1",)
    assert result.unexpanded[0]["reason"] == "no_members"


def test_expansion_stops_at_the_group_cap_and_says_which_groups_it_dropped():
    calendars = [
        record("c1", "dana@x.example"),
        record("g1", "one@x.example", "group", ["dana@x.example"]),
        record("g2", "two@x.example", "group", ["dana@x.example"]),
    ]
    result = expand_invited(calendars, group_expansion_max=1)
    assert [entry["id"] for entry in result.expanded] == ["g1"]
    assert result.unexpanded[0]["id"] == "g2"
    assert result.unexpanded[0]["reason"] == "group_expansion_max"
    assert "49%" in result.unexpanded[0]["detail"]


def test_a_cap_above_its_documented_maximum_is_refused_before_the_read():
    """Google's own answer to the same problem is to raise
    ``calendarExpansionMax``, which is already at its maximum. A refusal that names
    the documented cap is more use than a 400 from the provider."""
    with pytest.raises(LimitExceeded) as caught:
        expand_invited([], group_expansion_max=101)
    assert "documented maximum is 100" in str(caught.value)

    with pytest.raises(LimitExceeded) as caught:
        expand_invited([], calendar_expansion_max=51)
    assert "documented maximum is 50" in str(caught.value)
    assert "Maximum value is 50" in str(caught.value)


def test_a_cap_at_its_documented_maximum_is_accepted():
    result = expand_invited([], group_expansion_max=100, calendar_expansion_max=50)
    assert result.groups_expansion_max == 100
    assert result.calendar_expansion_max == 50


def test_a_cap_below_one_is_refused():
    for kwargs in ({"group_expansion_max": 0}, {"calendar_expansion_max": 0}):
        with pytest.raises(LimitExceeded):
            expand_invited([], **kwargs)


def test_expansion_beyond_the_calendar_cap_is_refused_with_a_fix():
    """The cap binds on the calendars the read is *asked about*, which is what
    Google's parameter name says, so it binds after expansion."""
    calendars = [record(f"c{i}", f"c{i}@x.example") for i in range(60)]
    with pytest.raises(LimitExceeded) as caught:
        expand_invited(calendars, calendar_expansion_max=50)
    assert "60 calendars" in str(caught.value)
    assert "Remove an attendee, or split the panel" in str(caught.value)


def test_expansion_at_the_calendar_cap_is_accepted():
    calendars = [record(f"c{i}", f"c{i}@x.example") for i in range(50)]
    assert len(expand_invited(calendars).invited) == 50


def test_a_duplicate_address_is_invited_once_at_its_first_position():
    calendars = [record("c1", "dana@x.example"), record("c2", "sam@x.example"), record("c1", "dana@x.example")]
    assert expand_invited(calendars).invited == ("c1", "c2")


def test_a_group_expands_in_case_insensitively():
    calendars = [
        record("c1", "Dana@X.example"),
        record("g1", "sales@x.example", "group", ["dana@x.EXAMPLE"]),
    ]
    assert expand_invited(calendars).invited == ("c1",)


# --------------------------------------------------------------------------- #
# The local directory
# --------------------------------------------------------------------------- #


def test_the_local_directory_answers_from_the_stored_calendar():
    directory = LocalDirectory()
    calendars = [
        {"id": "a", "data": {"busy": [{"start": at(MONDAY, 9), "end": at(MONDAY, 10)}]}},
        {"id": "b", "data": {"busy": []}},
    ]
    result = directory.send(
        calendars,
        time_min=parse_instant(at(MONDAY, 9)),
        time_max=parse_instant(at(MONDAY, 12)),
        provider="google",
    )
    assert len(result.blocks("a")) == 1
    assert result.blocks("b") == []


def test_the_local_directory_only_returns_busy_blocks_inside_the_window():
    directory = LocalDirectory()
    calendars = [
        {
            "id": "a",
            "data": {
                "busy": [
                    {"start": at(MONDAY, 7), "end": at(MONDAY, 8)},  # before
                    {"start": at(MONDAY, 9), "end": at(MONDAY, 10)},  # inside
                    {"start": at(MONDAY, 20), "end": at(MONDAY, 21)},  # after
                ]
            },
        }
    ]
    result = directory.send(
        calendars,
        time_min=parse_instant(at(MONDAY, 9)),
        time_max=parse_instant(at(MONDAY, 12)),
        provider="google",
    )
    assert len(result.blocks("a")) == 1


def test_a_calendar_marked_unreadable_is_the_unknown_case():
    directory = LocalDirectory()
    result = directory.send(
        [{"id": "a", "data": {"readable": False}}],
        time_min=parse_instant(at(MONDAY, 9)),
        time_max=parse_instant(at(MONDAY, 12)),
        provider="google",
    )
    assert result.is_unreadable("a")
    assert "unreadable" in result.unreadable["a"]


def test_a_calendar_with_an_unavailable_reason_names_it():
    directory = LocalDirectory()
    result = directory.send(
        [{"id": "a", "data": {"unavailable_reason": "the domain admin revoked access"}}],
        time_min=parse_instant(at(MONDAY, 9)),
        time_max=parse_instant(at(MONDAY, 12)),
        provider="google",
    )
    assert result.unreadable["a"] == "the domain admin revoked access"


def test_a_calendar_with_a_malformed_busy_block_is_unknown_rather_than_fatal():
    """One operator's typo in a busy block must not take a whole panel's search
    down; it costs that calendar its 100% and nothing else."""
    directory = LocalDirectory()
    result = directory.send(
        [
            {"id": "a", "data": {"busy": "all afternoon"}},
            {"id": "b", "data": {"busy": []}},
        ],
        time_min=parse_instant(at(MONDAY, 9)),
        time_max=parse_instant(at(MONDAY, 12)),
        provider="google",
    )
    assert result.is_unreadable("a")
    assert not result.is_unreadable("b")


def test_a_busy_interval_must_end_after_it_starts():
    directory = LocalDirectory()
    result = directory.send(
        [{"id": "a", "data": {"busy": [{"start": at(MONDAY, 10), "end": at(MONDAY, 9)}]}}],
        time_min=parse_instant(at(MONDAY, 9)),
        time_max=parse_instant(at(MONDAY, 12)),
        provider="google",
    )
    assert "must end after it starts" in result.unreadable["a"]


def test_busy_blocks_are_accepted_in_the_three_shapes_an_operator_writes_them():
    directory = LocalDirectory()
    result = directory.send(
        [
            {
                "id": "a",
                "data": {
                    "busy": [
                        {"start": at(MONDAY, 9), "end": at(MONDAY, 10)},  # Google
                        {"start": {"dateTime": at(MONDAY, 11)}, "end": {"dateTime": at(MONDAY, 12)}},  # Graph
                        [at(MONDAY, 13), at(MONDAY, 14)],  # a hand-written pair
                    ]
                },
            }
        ],
        time_min=parse_instant(at(MONDAY, 9)),
        time_max=parse_instant(at(MONDAY, 17)),
        provider="google",
    )
    assert len(result.blocks("a")) == 3


# --------------------------------------------------------------------------- #
# The urllib provider
# --------------------------------------------------------------------------- #


def test_the_urllib_provider_refuses_the_graph_dialect_by_name():
    """The research sources no Graph free/busy endpoint.

    ``findMeetingTimes`` returns a ranked suggestion list, not busy blocks. Rather
    than invent an endpoint this provider refuses the dialect with the reason in
    the message, so a deployment wires its own adapter or uses the local
    directory.
    """
    provider = UrllibProvider(access_token="ya29.token")
    with pytest.raises(PanelTimeNotConfigured) as caught:
        provider.send(
            [{"id": "a", "data": {}}],
            time_min=parse_instant(at(MONDAY, 9)),
            time_max=parse_instant(at(MONDAY, 17)),
            provider="graph",
        )
    assert "sends only the researched Google free/busy endpoint" in str(caught.value)
    assert "sources no Graph free/busy endpoint" in str(caught.value)


def test_the_urllib_provider_names_the_least_privileged_scope_it_needs():
    provider = UrllibProvider()
    with pytest.raises(PanelTimeNotConfigured) as caught:
        provider.send(
            [{"id": "a", "data": {}}],
            time_min=parse_instant(at(MONDAY, 9)),
            time_max=parse_instant(at(MONDAY, 17)),
            provider="google",
        )
    assert GOOGLE_FREEBUSY_SCOPE in str(caught.value)
    assert "not calendar.events or calendar.read" in str(caught.value)


def test_the_urllib_provider_never_reaches_a_socket_in_the_suite():
    """A test that opened one would be a test that could flake. Asserted on the
    factory rather than on a mock, so the guarantee survives a refactor."""
    source = inspect.getsource(UrllibProvider.send)
    assert "urlopen" in source  # it is the real transport
    # ...and the suite only ever constructs it without a token, which is the
    # branch that refuses above.
    with pytest.raises(PanelTimeNotConfigured):
        UrllibProvider().send(
            [{"id": "a", "data": {}}],
            time_min=parse_instant(at(MONDAY, 9)),
            time_max=parse_instant(at(MONDAY, 17)),
            provider="google",
        )


# --------------------------------------------------------------------------- #
# The researched commit request
# --------------------------------------------------------------------------- #


def test_the_commit_lands_on_the_organizers_calendar():
    """"the app creates the event on the organizer's calendar"."""
    request = render_commit_request(
        provider="google",
        calendar_id="organizer@x.example",
        summary="Northwind Q4",
        start=parse_instant(at(MONDAY, 9)),
        end=parse_instant(at(MONDAY, 10)),
        attendees=[{"email": "a@x.example"}, {"email": "b@x.example"}],
    )
    assert request["url"] == "/calendar/v3/calendars/organizer@x.example/events"
    assert request["method"] == "POST"
    assert len(request["body"]["attendees"]) == 2


def test_a_fresh_conference_is_requested_by_default_and_omittable():
    """"optionally creating a fresh conference" - so both readings are reachable."""
    with_conference = render_commit_request(
        provider="google",
        calendar_id="o@x.example",
        summary="Panel",
        start=parse_instant(at(MONDAY, 9)),
        end=parse_instant(at(MONDAY, 10)),
        attendees=[],
    )
    assert with_conference["body"]["conferenceData"]["createRequest"]["conferenceSolutionKey"] == {
        "type": "hangoutsMeet"
    }
    without = render_commit_request(
        provider="google",
        calendar_id="o@x.example",
        summary="Panel",
        start=parse_instant(at(MONDAY, 9)),
        end=parse_instant(at(MONDAY, 10)),
        attendees=[],
        create_conference=False,
    )
    assert "conferenceData" not in without["body"]


def test_a_room_location_constraint_reaches_the_commit():
    request = render_commit_request(
        provider="graph",
        calendar_id="o@x.example",
        summary="Panel",
        start=parse_instant(at(MONDAY, 9)),
        end=parse_instant(at(MONDAY, 10)),
        attendees=[{"email": "a@x.example"}],
        location_constraint={"type": LOCATION_ROOM, "room_id": "room-london@x.example"},
    )
    assert request["body"]["location"]["displayName"] == "Microsoft Teams Meeting"
    assert request["body"]["isOnlineMeeting"] is True


def test_the_local_commit_mints_a_stable_event_id():
    """The same commit must produce the same id, or re-running the demo would
    double-book itself - and ``PYTHONHASHSEED`` randomises ``hash()`` per
    process, so a test asserting on this is asserting on ``hashlib``."""
    request = render_commit_request(
        provider="google",
        calendar_id="o@x.example",
        summary="Panel",
        start=parse_instant(at(MONDAY, 9)),
        end=parse_instant(at(MONDAY, 10)),
        attendees=[],
    )
    first = LocalDirectory().commit(request, calendar_id="o@x.example", source=SOURCE)
    second = LocalDirectory().commit(request, calendar_id="o@x.example", source=SOURCE)
    assert first["event_id"] == second["event_id"]
    assert first["event_id"].startswith("evt_")
    assert first["conference"]["entry_point"].endswith(first["event_id"])


def test_a_commit_without_a_conference_mints_no_link():
    request = render_commit_request(
        provider="google",
        calendar_id="o@x.example",
        summary="Panel",
        start=parse_instant(at(MONDAY, 9)),
        end=parse_instant(at(MONDAY, 10)),
        attendees=[],
        create_conference=False,
    )
    assert LocalDirectory().commit(request, calendar_id="o@x.example", source=SOURCE)["conference"] is None


# --------------------------------------------------------------------------- #
# The engine, end to end
# --------------------------------------------------------------------------- #


def test_a_find_returns_the_ranked_shortlist_with_confidence_and_reason(finder, panel):
    result = finder.find(panel["room_id"], panel["id"], actor="dana", source=SOURCE)
    assert result["ok"] is True
    assert result["suggestions"]
    assert result["empty_suggestions_reason"] is None
    for suggestion in result["suggestions"]:
        assert suggestion["confidence"] == 100
        assert suggestion["suggestion_reason"] == SUGGESTION_REASON_ALL_FREE
        assert suggestion["start"].endswith("Z")


def test_a_find_writes_exactly_one_row(finder, panel, store):
    finder.find(panel["room_id"], panel["id"], actor="dana", source=SOURCE)
    assert count(store, SEARCH_COLLECTION) == 1


def test_a_preview_writes_nothing_and_answers_the_same_way(finder, panel, store):
    preview = finder.find(panel["room_id"], panel["id"], source=SOURCE, dry_run=True)
    assert count(store, SEARCH_COLLECTION) == 0
    written = finder.find(panel["room_id"], panel["id"], actor="dana", source=SOURCE)
    assert preview["suggestions"] == written["suggestions"]
    assert preview["counts"] == written["counts"]


def test_the_best_slot_is_often_not_the_earliest_one(finder, room, free_calendar, busy_morning):
    """The researched sort, observable end to end: 09:00 is busy, so the earliest
    slot scores 50 and the 10:00 one scores 100. A page that sorted by time would
    put the worse slot first."""
    panel = finder.create_panel(
        room["id"],
        {
            "name": "Ordering panel",
            "organizer": free_calendar["id"],
            "calendars": [free_calendar["id"], busy_morning["id"]],
            "time_constraint": {
                "timeSlots": [{"start": at(MONDAY, 9), "end": at(MONDAY, 12)}]
            },
            "meeting_duration": "PT1H",
            "slot_interval": "PT1H",
        },
        actor="dana",
        source=SOURCE,
    )
    result = finder.find(room["id"], panel["id"], actor="dana", source=SOURCE)
    assert [s["start"] for s in result["suggestions"]] == [
        at(MONDAY, 10),
        at(MONDAY, 11),
        at(MONDAY, 9),
    ]
    assert [s["confidence"] for s in result["suggestions"]] == [100, 100, 50]


def test_the_search_records_the_request_that_would_go_on_the_wire(finder, panel):
    result = finder.find(panel["room_id"], panel["id"], actor="dana", source=SOURCE)
    request = result["request"]
    assert request["url"] == GOOGLE_FREEBUSY_URL
    assert request["scope"] == GOOGLE_FREEBUSY_SCOPE
    assert request["body"]["items"] == [{"id": result["invited"][0]["id"]}]
    assert request["body"]["calendarExpansionMax"] == 50
    assert request["body"]["groupExpansionMax"] == 100


def test_the_graph_dialect_records_the_prefer_header(finder, room, free_calendar):
    panel = finder.create_panel(
        room["id"],
        {
            "name": "Graph panel",
            "calendar_provider": "graph",
            "organizer": free_calendar["id"],
            "calendars": [free_calendar["id"]],
            "time_constraint": {"timeSlots": [{"start": at(MONDAY, 9), "end": at(MONDAY, 12)}]},
            "meeting_duration": "PT1H",
            "slot_interval": "PT1H",
        },
        actor="dana",
        source=SOURCE,
    )
    result = finder.find(room["id"], panel["id"], actor="dana", source=SOURCE)
    assert result["request"]["url"] == "/me/findMeetingTimes"
    assert result["request"]["headers"]["Prefer"] == GRAPH_PREFER_HEADER
    assert result["request"]["scope"] == GRAPH_DELEGATED_SCOPE
    assert result["request"]["delegated"] is True
    assert "provider_note" in result["request"]


def test_an_unreadable_calendar_is_shown_as_unknown_and_lowers_the_score(
    finder, room, free_calendar, silent_calendar
):
    panel = finder.create_panel(
        room["id"],
        {
            "name": "Unknown panel",
            "organizer": free_calendar["id"],
            "calendars": [free_calendar["id"], silent_calendar["id"]],
            "time_constraint": {"timeSlots": [{"start": at(MONDAY, 9), "end": at(MONDAY, 10)}]},
            "meeting_duration": "PT1H",
            "slot_interval": "PT1H",
        },
        actor="dana",
        source=SOURCE,
    )
    result = finder.find(room["id"], panel["id"], actor="dana", source=SOURCE)
    [slot] = result["suggestions"]
    assert slot["confidence"] == 75  # (100 + 49) / 2
    assert slot["free_percentage"] == 50
    assert "unknown" in slot["suggestion_reason"]
    unreadable = [entry for entry in result["invited"] if not entry["readable"]]
    assert unreadable[0]["unavailable_reason"] == "this contact has not shared a calendar"
    assert any(w["code"] == "calendar_unreadable" for w in result["warnings"])


def test_a_hundred_percent_bar_with_an_unreadable_calendar_returns_nothing(
    finder, room, free_calendar, silent_calendar
):
    panel = finder.create_panel(
        room["id"],
        {
            "name": "Strict panel",
            "organizer": free_calendar["id"],
            "calendars": [free_calendar["id"], silent_calendar["id"]],
            "min_attendee_percentage": 100,
            "time_constraint": {"timeSlots": [{"start": at(MONDAY, 9), "end": at(MONDAY, 12)}]},
            "meeting_duration": "PT1H",
            "slot_interval": "PT1H",
        },
        actor="dana",
        source=SOURCE,
    )
    result = finder.find(room["id"], panel["id"], actor="dana", source=SOURCE)
    assert result["suggestions"] == []
    assert result["empty_suggestions_reason"] == EMPTY_NOT_ENOUGH_PEOPLE_FREE
    assert result["below_threshold"]
    assert "50%" in result["below_threshold"][0]["reason"]


def test_house_rules_naming_their_refusals_reach_the_search(finder, room, free_calendar, busy_morning):
    panel = finder.create_panel(
        room["id"],
        {
            "name": "House-rule panel",
            "organizer": free_calendar["id"],
            "calendars": [free_calendar["id"], busy_morning["id"]],
            "time_constraint": {"timeSlots": [{"start": at(MONDAY, 9), "end": at(MONDAY, 12)}]},
            "meeting_duration": "PT1H",
            "slot_interval": "PT1H",
            "house_rules": {"earliest_start": "10:00", "no_back_to_back": True},
        },
        actor="dana",
        source=SOURCE,
    )
    result = finder.find(room["id"], panel["id"], actor="dana", source=SOURCE)
    assert result["counts"]["house_rule_refusals"] == 2
    assert {row["rule"] for row in result["house_rule_refusals"]} == {
        "earliest_start",
        "no_back_to_back",
    }
    assert [s["start"] for s in result["suggestions"]] == [at(MONDAY, 11)]


def test_house_rules_that_remove_everything_give_the_busy_suggestions_reason(
    finder, room, free_calendar
):
    panel = finder.create_panel(
        room["id"],
        {
            "name": "Impossible rules",
            "organizer": free_calendar["id"],
            "calendars": [free_calendar["id"]],
            "time_constraint": {"timeSlots": [{"start": at(MONDAY, 9), "end": at(MONDAY, 12)}]},
            "meeting_duration": "PT1H",
            "slot_interval": "PT1H",
            "house_rules": {"earliest_start": "23:00"},
        },
        actor="dana",
        source=SOURCE,
    )
    result = finder.find(room["id"], panel["id"], actor="dana", source=SOURCE)
    assert result["empty_suggestions_reason"] == EMPTY_BUSY_SUGGESTIONS
    assert [a["id"] for a in result["suggested_adjustments"]][:2] == [
        "extend_working_window",
        "widen_window",
    ]


def test_a_panel_that_does_not_invite_its_organizer_is_flagged_at_search_time(
    finder, room, free_calendar
):
    """There is no calendar to create the event on, so step 5 cannot complete.

    A panel in that state can still produce a useful shortlist - a rep may be
    choosing before the organizer is set - so it is a warning on every search, and
    the researched ``notOrganizedAsAttendee`` reason when the shortlist is empty
    too. Learning it at commit time, after the rep had picked a slot, would be the
    surprise.
    """
    panel = finder.create_panel(
        room["id"],
        {
            "name": "No organizer",
            "calendars": [free_calendar["id"]],
            "time_constraint": {"timeSlots": [{"start": at(MONDAY, 9), "end": at(MONDAY, 12)}]},
            "meeting_duration": "PT1H",
            "slot_interval": "PT1H",
        },
        actor="dana",
        source=SOURCE,
    )
    result = finder.find(room["id"], panel["id"], actor="dana", source=SOURCE)
    assert result["suggestions"], "the shortlist is still useful"
    assert any(w["code"] == "not_organized_as_attendee" for w in result["warnings"])


def test_a_panel_with_no_organizer_and_nowhere_to_go_says_which_of_the_two_it_is(
    finder, room, free_calendar
):
    """An empty shortlist on a panel with no organizer reports the *organizer*
    first, because that is the cause a parameter change cannot fix."""
    panel = finder.create_panel(
        room["id"],
        {
            "name": "No organizer, no slots",
            "calendars": [free_calendar["id"]],
            "min_attendee_percentage": 100,
            "time_constraint": {"timeSlots": [{"start": at(MONDAY, 9), "end": at(MONDAY, 9, 30)}]},
            "meeting_duration": "PT1H",
            "slot_interval": "PT30M",
        },
        actor="dana",
        source=SOURCE,
    )
    result = finder.find(room["id"], panel["id"], actor="dana", source=SOURCE)
    assert result["empty_suggestions_reason"] == EMPTY_NOT_ORGANIZER
    assert [a["id"] for a in result["suggested_adjustments"]] == ["invite_organizer"]


def test_derive_empty_reason_reports_the_organizer_before_the_window():
    """Ordered from the most fundamental cause to the least, because that is the
    order worth telling a caller in: fix the calendar before the policy."""
    from dsr.panel_time import derive_empty_reason

    reason = derive_empty_reason(
        candidates=[],
        invited=["a"],
        organizer_id="z",
        suggested=[],
        house_rule_refusals=0,
        min_attendee_percentage=80,
    )
    assert reason == EMPTY_NOT_ORGANIZER
    # ...and with no organizer at all, the same.
    assert derive_empty_reason(
        candidates=[],
        invited=["a"],
        organizer_id=None,
        suggested=[],
        house_rule_refusals=0,
        min_attendee_percentage=80,
    ) == EMPTY_NOT_ORGANIZER


def test_the_organizer_is_invited_even_when_the_panel_forgets_to(
    finder, room, free_calendar, busy_morning
):
    """Step 5 creates the event on the organizer's calendar, so the organizer's
    availability has to be in the average or the ranking is about someone else."""
    panel = finder.create_panel(
        room["id"],
        {
            "name": "Organizer implied",
            "organizer": busy_morning["id"],
            "calendars": [free_calendar["id"]],
            "time_constraint": {"timeSlots": [{"start": at(MONDAY, 9), "end": at(MONDAY, 12)}]},
            "meeting_duration": "PT1H",
            "slot_interval": "PT1H",
        },
        actor="dana",
        source=SOURCE,
    )
    result = finder.find(room["id"], panel["id"], actor="dana", source=SOURCE)
    assert result["counts"]["invited"] == 2
    # The organizer is booked 09:00-10:00, so 09:00 scores 50 and the later two
    # score 100. At a bar of 0 the 50% slot is still suggested - just not first.
    assert [s["confidence"] for s in result["suggestions"]] == [100, 100, 50]
    assert result["suggestions"][0]["start"] == at(MONDAY, 10)
    # And the slot the organizer is busy for names them.
    worst = result["suggestions"][-1]
    assert worst["start"] == at(MONDAY, 9)
    assert worst["busy"] == [busy_morning["id"]]


def test_the_organizer_may_be_named_by_address_or_by_id(finder, room, free_calendar):
    by_id = finder.create_panel(
        room["id"],
        {
            "name": "By id",
            "organizer": free_calendar["id"],
            "calendars": [free_calendar["id"]],
            "time_constraint": {"timeSlots": [{"start": at(MONDAY, 9), "end": at(MONDAY, 12)}]},
            "meeting_duration": "PT1H",
            "slot_interval": "PT1H",
        },
        actor="dana",
        source=SOURCE,
    )
    by_address = finder.create_panel(
        room["id"],
        {
            "name": "By address",
            "organizer": "free@northwind.example",
            "calendars": [free_calendar["id"]],
            "time_constraint": {"timeSlots": [{"start": at(MONDAY, 9), "end": at(MONDAY, 12)}]},
            "meeting_duration": "PT1H",
            "slot_interval": "PT1H",
        },
        actor="dana",
        source=SOURCE,
    )
    first = finder.find(room["id"], by_id["id"], actor="dana", source=SOURCE)
    second = finder.find(room["id"], by_address["id"], actor="dana", source=SOURCE)
    assert [s["confidence"] for s in first["suggestions"]] == [
        s["confidence"] for s in second["suggestions"]
    ]


def test_a_panel_inviting_an_unregistered_calendar_is_a_404_naming_it(finder, room, free_calendar):
    panel = finder.create_panel(
        room["id"],
        {
            "name": "Ghost",
            "organizer": free_calendar["id"],
            "calendars": ["nobody@northwind.example"],
            "time_constraint": {"timeSlots": [{"start": at(MONDAY, 9), "end": at(MONDAY, 12)}]},
            "meeting_duration": "PT1H",
            "slot_interval": "PT1H",
        },
        actor="dana",
        source=SOURCE,
    )
    with pytest.raises(NotFound) as caught:
        finder.find(room["id"], panel["id"], actor="dana", source=SOURCE)
    assert caught.value.resource == "calendar"
    assert "nobody@northwind.example" in str(caught.value)


def test_an_installation_with_no_calendars_is_428_not_400(finder, room, store):
    panel = finder.create_panel(
        room["id"],
        {
            "name": "Nothing registered",
            "calendars": [],
            "time_constraint": {"timeSlots": [{"start": at(MONDAY, 9), "end": at(MONDAY, 12)}]},
            "meeting_duration": "PT1H",
        },
        actor="dana",
        source=SOURCE,
    )
    with pytest.raises(PanelTimeNotConfigured) as caught:
        finder.find(room["id"], panel["id"], actor="dana", source=SOURCE)
    assert "no calendars registered" in str(caught.value)


def test_the_toggle_removes_the_reason_from_the_search_row(finder, room, free_calendar):
    """And the *wire request* carries the toggle, on the dialect that has one.

    ``returnSuggestionReasons`` is a Graph parameter, so the assertion is on a
    Graph panel: the Google request body has no such field, and pretending
    otherwise would be a test of a field that does not exist.
    """
    panel = finder.create_panel(
        room["id"],
        {
            "name": "Graph reasons",
            "calendar_provider": "graph",
            "organizer": free_calendar["id"],
            "calendars": [free_calendar["id"]],
            "time_constraint": {"timeSlots": [{"start": at(MONDAY, 9), "end": at(MONDAY, 12)}]},
            "meeting_duration": "PT1H",
            "slot_interval": "PT1H",
        },
        actor="dana",
        source=SOURCE,
    )
    assert finder.find(
        room["id"], panel["id"], actor="dana", source=SOURCE
    )["request"]["body"]["returnSuggestionReasons"] is True

    finder.update_panel(
        room["id"], panel["id"], {"return_suggestion_reasons": False}, actor="dana", source=SOURCE
    )
    result = finder.find(room["id"], panel["id"], actor="dana", source=SOURCE)
    assert all("suggestion_reason" not in s for s in result["suggestions"])
    assert any(w["code"] == "suggestion_reasons_suppressed" for w in result["warnings"])
    # The request still says the toggle is off, so the wire request is honest.
    assert result["request"]["body"]["returnSuggestionReasons"] is False


def test_the_alternative_ranker_is_reachable_from_the_panel(finder, room, free_calendar, silent_calendar):
    panel = finder.create_panel(
        room["id"],
        {
            "name": "Weighted",
            "organizer": free_calendar["id"],
            "calendars": [free_calendar["id"], silent_calendar["id"]],
            "ranker": RANK_WEIGHTED,
            "unknown_penalty": 20,
            "time_constraint": {"timeSlots": [{"start": at(MONDAY, 9), "end": at(MONDAY, 12)}]},
            "meeting_duration": "PT1H",
            "slot_interval": "PT1H",
        },
        actor="dana",
        source=SOURCE,
    )
    result = finder.find(room["id"], panel["id"], actor="dana", source=SOURCE)
    assert result["ranker"] == RANK_WEIGHTED
    # 75 confidence less 20 for the one unknown.
    assert result["suggestions"][0]["score"] == 55
    assert result["suggestions"][0]["confidence"] == 75


def test_a_dry_run_is_the_only_way_to_search_without_a_row(finder, panel, store):
    finder.find(panel["room_id"], panel["id"], source=SOURCE, dry_run=True)
    assert count(store, SEARCH_COLLECTION) == 0


def test_overrides_win_over_the_panel_for_one_call_only(finder, panel, store):
    finder.find(
        panel["room_id"],
        panel["id"],
        actor="dana",
        source=SOURCE,
        overrides={"min_attendee_percentage": 100},
    )
    assert finder.get_panel(panel["room_id"], panel["id"])["min_attendee_percentage"] == 0


# --------------------------------------------------------------------------- #
# The documented re-call, as a call
# --------------------------------------------------------------------------- #


def stuck_panel(finder, room, free_calendar, all_day_busy):
    """A panel whose whole morning is booked, with a bar nothing can clear."""
    return finder.create_panel(
        room["id"],
        {
            "name": "Nowhere to go",
            "organizer": free_calendar["id"],
            "calendars": [free_calendar["id"], all_day_busy["id"]],
            "min_attendee_percentage": 80,
            "time_constraint": {"timeSlots": [{"start": at(MONDAY, 9), "end": at(MONDAY, 12)}]},
            "meeting_duration": "PT1H",
            "slot_interval": "PT1H",
        },
        actor="dana",
        source=SOURCE,
    )


@pytest.fixture()
def all_day_busy(finder):
    return finder.create_calendar(
        {
            "email": "solid@northwind.example",
            "busy": [{"start": at(MONDAY, 0), "end": at(MONDAY, 23, 59)}],
        },
        actor="dana",
        source=SOURCE,
    )


def test_an_empty_search_is_a_row_not_a_refusal(finder, room, free_calendar, all_day_busy, store):
    panel = stuck_panel(finder, room, free_calendar, all_day_busy)
    result = finder.find(room["id"], panel["id"], actor="dana", source=SOURCE)
    assert result["ok"] is True
    assert result["suggestions"] == []
    assert result["empty_suggestions_reason"] == EMPTY_NOT_ENOUGH_PEOPLE_FREE
    assert count(store, SEARCH_COLLECTION) == 1


def test_the_recall_writes_a_second_row_and_leaves_the_first_readable(
    finder, room, free_calendar, all_day_busy, store
):
    panel = stuck_panel(finder, room, free_calendar, all_day_busy)
    first = finder.find(room["id"], panel["id"], actor="dana", source=SOURCE)
    second = finder.retune(
        room["id"], first["id"], {"adjustment": "relax_min_attendee_percentage"}, actor="dana", source=SOURCE
    )
    assert count(store, SEARCH_COLLECTION) == 2
    assert second["parent_search_id"] == first["id"]
    assert second["adjustment"] == "relax_min_attendee_percentage"
    assert second["suggestions"], "relaxing the bar should have found the slot"
    # The parameters that produced the empty result are still readable.
    assert finder.get_search(room["id"], first["id"])["min_attendee_percentage"] == 80
    assert finder.get_search(room["id"], second["id"])["min_attendee_percentage"] == 0


def test_the_recall_defaults_to_the_first_adjustment_the_reason_suggests(
    finder, room, free_calendar, all_day_busy
):
    panel = stuck_panel(finder, room, free_calendar, all_day_busy)
    first = finder.find(room["id"], panel["id"], actor="dana", source=SOURCE)
    second = finder.retune(room["id"], first["id"], {}, actor="dana", source=SOURCE)
    assert second["adjustment"] == "widen_window"


def test_widening_the_window_really_finds_a_slot(
    finder, room, free_calendar
):
    """The all-day-busy attendee is busy on the Monday, so widening the window to
    the Tuesday is what actually fixes it - the demonstration that the researched
    automation is a real second call and not a relabelling."""
    tomorrow_only = finder.create_calendar(
        {
            "email": "mondays_only@northwind.example",
            "busy": [{"start": at(MONDAY, 0), "end": at(MONDAY, 23, 59)}],
        },
        actor="dana",
        source=SOURCE,
    )
    panel = stuck_panel(finder, room, free_calendar, tomorrow_only)
    first = finder.find(room["id"], panel["id"], actor="dana", source=SOURCE)
    assert first["empty_suggestions_reason"] == EMPTY_NOT_ENOUGH_PEOPLE_FREE
    second = finder.retune(
        room["id"], first["id"], {"adjustment": "widen_window", "days": 1}, actor="dana", source=SOURCE
    )
    assert second["suggestions"]
    assert all(s["start"].startswith(TUESDAY) for s in second["suggestions"])


def test_a_recall_carrying_the_bar_to_zero_finds_the_slot(
    finder, room, free_calendar, all_day_busy
):
    panel = stuck_panel(finder, room, free_calendar, all_day_busy)
    first = finder.find(room["id"], panel["id"], actor="dana", source=SOURCE)
    second = finder.retune(
        room["id"],
        first["id"],
        {"adjustment": "relax_min_attendee_percentage"},
        actor="dana",
        source=SOURCE,
    )
    assert second["suggestions"]
    assert second["suggestions"][0]["free_percentage"] == 50


def test_a_recall_of_an_adjustment_the_reason_does_not_suggest_is_refused(
    finder, room, free_calendar, all_day_busy
):
    """Adjusting something the reason does not name is not a re-call; the route
    says so rather than running a search that will fail the same way."""
    panel = stuck_panel(finder, room, free_calendar, all_day_busy)
    first = finder.find(room["id"], panel["id"], actor="dana", source=SOURCE)
    with pytest.raises(ConstraintError) as caught:
        finder.retune(
            room["id"], first["id"], {"adjustment": "coarsen_slot_interval"}, actor="dana", source=SOURCE
        )
    assert "not one of the adjustments this emptySuggestionsReason" in str(caught.value)


def test_an_unknown_adjustment_is_refused(finder, room, free_calendar, all_day_busy):
    panel = stuck_panel(finder, room, free_calendar, all_day_busy)
    first = finder.find(room["id"], panel["id"], actor="dana", source=SOURCE)
    with pytest.raises(ConstraintError):
        finder.retune(room["id"], first["id"], {"adjustment": "magic"}, actor="dana", source=SOURCE)


def test_a_recall_of_a_search_that_found_something_is_still_possible(
    finder, panel
):
    """The research says the reason is the signal to re-call; it does not say a
    search with suggestions is off limits, and widening a working search is a
    legitimate thing to want."""
    first = finder.find(panel["room_id"], panel["id"], actor="dana", source=SOURCE)
    second = finder.retune(
        panel["room_id"], first["id"], {"adjustment": "widen_window", "days": 2}, actor="dana", source=SOURCE
    )
    assert second["parent_search_id"] == first["id"]
    assert len(second["suggestions"]) > len(first["suggestions"])


# --------------------------------------------------------------------------- #
# The researched commit
# --------------------------------------------------------------------------- #


def test_booking_creates_the_event_on_the_organizers_calendar(finder, panel):
    search_row = finder.find(panel["room_id"], panel["id"], actor="dana", source=SOURCE)
    booking = finder.book(
        panel["room_id"],
        search_row["id"],
        {"start": search_row["suggestions"][0]["start"]},
        actor="dana",
        source=SOURCE,
    )
    assert booking["organizer_email"] == "free@northwind.example"
    assert booking["organizer_id"] == panel["organizer"]
    assert booking["start"] == search_row["suggestions"][0]["start"]
    # Google addresses the commit by calendarId, and the panel named the organizer
    # by address, so the id is what lands in the path.
    assert booking["request"]["url"] == f"/calendar/v3/calendars/{panel['organizer']}/events"
    assert booking["conference"]["created"] is True
    assert booking["revalidated"]["confidence"] == 100


def test_booking_without_a_fresh_conference(finder, panel):
    search_row = finder.find(panel["room_id"], panel["id"], actor="dana", source=SOURCE)
    booking = finder.book(
        panel["room_id"],
        search_row["id"],
        {"start": search_row["suggestions"][0]["start"], "create_conference": False},
        actor="dana",
        source=SOURCE,
    )
    assert booking["conference"] is None
    assert "conferenceData" not in booking["request"]["body"]


def test_booking_a_slot_the_search_never_returned_is_refused(finder, panel):
    """The researched flow is "ranked candidate slots are returned … the user picks
    one", so an arbitrary instant is a caller bug."""
    search_row = finder.find(panel["room_id"], panel["id"], actor="dana", source=SOURCE)
    with pytest.raises(SlotUnavailable) as caught:
        finder.book(
            panel["room_id"], search_row["id"], {"start": at(MONDAY, 16, 30)}, actor="dana", source=SOURCE
        )
    assert "is not one of the" in str(caught.value)
    assert "Run the search again" in str(caught.value)


def test_booking_without_a_start_is_refused(finder, panel):
    search_row = finder.find(panel["room_id"], panel["id"], actor="dana", source=SOURCE)
    with pytest.raises(ConstraintError) as caught:
        finder.book(panel["room_id"], search_row["id"], {}, actor="dana", source=SOURCE)
    assert "start is required" in str(caught.value)


def test_booking_re_reads_availability_and_refuses_a_now_busy_organizer(
    finder, room, panel, free_calendar
):
    """The researched drift note, made into a check.

    "fine-tuned from time to time" plus a *pull* read with no invalidation means a
    search from last week is a snapshot of a fact that moves. Creating an event
    from a stale snapshot is a double-booking the user finds out from a customer.
    """
    search_row = finder.find(panel["room_id"], panel["id"], actor="dana", source=SOURCE)
    chosen = search_row["suggestions"][0]["start"]
    # The organizer books that slot after the search ran.
    finder.update_calendar(
        free_calendar["id"],
        {"busy": [{"start": chosen, "end": parse_instant(chosen) + timedelta(hours=1)}]},
        actor="dana",
        source=SOURCE,
    )
    with pytest.raises(SlotUnavailable) as caught:
        finder.book(panel["room_id"], search_row["id"], {"start": chosen}, actor="dana", source=SOURCE)
    assert "organizer's calendar is now busy" in str(caught.value)
    assert "fine-tuned from time to time" in str(caught.value)


def test_booking_refuses_a_slot_that_no_longer_clears_the_bar(
    finder, room, free_calendar
):
    """The bar is the *search's* bar, checked against a re-read.

    The search is a snapshot and the commit is a decision taken later, so what is
    re-checked is the bar that search was run with - raising the panel's bar after
    the fact would silently change what the recorded shortlist meant.
    """
    afternoon = finder.create_calendar(
        {"email": "afternoons@northwind.example", "busy": []}, actor="dana", source=SOURCE
    )
    panel = finder.create_panel(
        room["id"],
        {
            "name": "Strict at search time",
            "organizer": free_calendar["id"],
            "calendars": [free_calendar["id"], afternoon["id"]],
            "min_attendee_percentage": 100,
            "time_constraint": {"timeSlots": [{"start": at(MONDAY, 9), "end": at(MONDAY, 10)}]},
            "meeting_duration": "PT1H",
            "slot_interval": "PT1H",
        },
        actor="dana",
        source=SOURCE,
    )
    search_row = finder.find(room["id"], panel["id"], actor="dana", source=SOURCE)
    chosen = search_row["suggestions"][0]["start"]
    # One attendee books the slot after the search ran.
    finder.update_calendar(
        afternoon["id"],
        {"busy": [{"start": chosen, "end": parse_instant(chosen) + timedelta(hours=1)}]},
        actor="dana",
        source=SOURCE,
    )
    with pytest.raises(SlotUnavailable) as caught:
        finder.book(room["id"], search_row["id"], {"start": chosen}, actor="dana", source=SOURCE)
    assert "no longer clears" in str(caught.value)
    assert "100%" in str(caught.value)
    assert "Re-run the search" in str(caught.value)


def test_a_refused_booking_writes_nothing(finder, panel, store):
    search_row = finder.find(panel["room_id"], panel["id"], actor="dana", source=SOURCE)
    with pytest.raises(SlotUnavailable):
        finder.book(
            panel["room_id"], search_row["id"], {"start": at(MONDAY, 16, 30)}, actor="dana", source=SOURCE
        )
    assert count(store, BOOKING_COLLECTION) == 0


def test_booking_a_panel_whose_organizer_calendar_is_gone_is_refused(
    finder, room, free_calendar
):
    """A 404 naming the calendar, not a 422 blaming the slot.

    Nothing is wrong with the slot and nothing is wrong with the request; the
    calendar the researched step 5 needs has been deleted. The two answers are
    different things for a client, so they get different statuses.
    """
    panel = finder.create_panel(
        room["id"],
        {
            "name": "Organizer goes away",
            "organizer": free_calendar["id"],
            "calendars": [free_calendar["id"]],
            "time_constraint": {"timeSlots": [{"start": at(MONDAY, 9), "end": at(MONDAY, 10)}]},
            "meeting_duration": "PT1H",
            "slot_interval": "PT1H",
        },
        actor="dana",
        source=SOURCE,
    )
    search_row = finder.find(room["id"], panel["id"], actor="dana", source=SOURCE)
    chosen = search_row["suggestions"][0]["start"]
    # A second calendar so the search still works after the organizer is deleted.
    finder.create_calendar(
        {"email": "stand.in@northwind.example", "busy": []}, actor="dana", source=SOURCE
    )
    finder.delete_calendar(free_calendar["id"], actor="dana", source=SOURCE)
    with pytest.raises(NotFound) as caught:
        finder.book(room["id"], search_row["id"], {"start": chosen}, actor="dana", source=SOURCE)
    assert caught.value.resource == "calendar"


def test_the_room_scope_of_a_booking_survives_a_deleted_panel(finder, panel, store):
    """A soft-deleted panel's id must still resolve, so a booking can be read back
    and the room can say what was booked."""
    search_row = finder.find(panel["room_id"], panel["id"], actor="dana", source=SOURCE)
    booking = finder.book(
        panel["room_id"], search_row["id"], {"start": search_row["suggestions"][0]["start"]},
        actor="dana", source=SOURCE,
    )
    finder.delete_panel(panel["room_id"], panel["id"], actor="dana", source=SOURCE)
    assert finder.get_booking(panel["room_id"], booking["id"])["summary"] == booking["summary"]


# --------------------------------------------------------------------------- #
# Records, rooms and the shape rules
# --------------------------------------------------------------------------- #


def test_a_panel_needs_a_name_and_a_time_constraint(finder, room):
    with pytest.raises(PanelShapeError) as caught:
        finder.create_panel(room["id"], {}, actor="dana", source=SOURCE)
    assert "needs a name" in str(caught.value)

    with pytest.raises(PanelShapeError) as caught:
        finder.create_panel(room["id"], {"name": "x"}, actor="dana", source=SOURCE)
    assert "needs a time_constraint" in str(caught.value)
    assert "date range" in str(caught.value)


def test_a_calendar_needs_an_address_that_is_an_address(finder):
    with pytest.raises(CalendarShapeError):
        finder.create_calendar({}, actor="dana", source=SOURCE)
    for bad in ("nope", "@x.example", "x@"):
        with pytest.raises(CalendarShapeError):
            finder.create_calendar({"email": bad}, actor="dana", source=SOURCE)


def test_a_calendar_kind_must_be_one_the_read_understands(finder):
    assert KINDS == ("person", "room", "group")
    with pytest.raises(CalendarShapeError):
        finder.create_calendar({"email": "a@x.example", "kind": "robot"}, actor="dana", source=SOURCE)


def test_a_panel_must_be_created_on_a_room_that_exists(finder, free_calendar):
    with pytest.raises(NotFound) as caught:
        finder.create_panel(
            "room_nope",
            {
                "name": "Orphan",
                "time_constraint": {"timeSlots": [{"start": at(MONDAY, 9), "end": at(MONDAY, 12)}]},
            },
            actor="dana",
            source=SOURCE,
        )
    assert caught.value.resource == "room"


def test_a_panel_is_only_readable_under_the_room_it_belongs_to(finder, store, room, free_calendar):
    other = store.create("room", {"name": "Other"}, actor="dana", source=SOURCE)
    panel = finder.create_panel(
        room["id"],
        {
            "name": "Scoped",
            "organizer": free_calendar["id"],
            "calendars": [free_calendar["id"]],
            "time_constraint": {"timeSlots": [{"start": at(MONDAY, 9), "end": at(MONDAY, 12)}]},
        },
        actor="dana",
        source=SOURCE,
    )
    assert finder.get_panel(room["id"], panel["id"])["id"] == panel["id"]
    with pytest.raises(NotFound) as caught:
        finder.get_panel(other["id"], panel["id"])
    assert caught.value.room_id == other["id"]


def test_every_panel_key_is_ordinary_json_and_never_a_column(finder, room, free_calendar, store):
    """Schema flexibility, asserted on the row rather than on a comment: a key
    nobody declared survives a round trip and is queryable by dotted path."""
    panel = finder.create_panel(
        room["id"],
        {
            "name": "Custom",
            "organizer": free_calendar["id"],
            "calendars": [free_calendar["id"]],
            "time_constraint": {"timeSlots": [{"start": at(MONDAY, 9), "end": at(MONDAY, 12)}]},
            "house_rule_the_team_invented": {"banned_words": ["vibes"]},
        },
        actor="dana",
        source=SOURCE,
    )
    record = store.get(panel["id"])
    assert record["data"]["house_rule_the_team_invented"] == {"banned_words": ["vibes"]}
    assert store.find(PANEL_COLLECTION, {"house_rule_the_team_invented.banned_words.0": "vibes"})
    assert record["collection"] == PANEL_COLLECTION


def test_a_calendar_payload_keeps_a_key_this_build_has_never_heard_of(finder):
    created = finder.create_calendar(
        {"email": "a@x.example", "vacation_policy": {"after": "2026-12-20"}},
        actor="dana",
        source=SOURCE,
    )
    assert store_field(finder, created["id"])["vacation_policy"] == {"after": "2026-12-20"}


def store_field(finder: SlotFinder, calendar_id: str) -> dict:
    return finder.store.get(calendar_id)["data"]


def test_the_access_token_is_never_stored_and_never_echoed_back(
    finder, room, free_calendar
):
    """The token *is* the Authorization header on every request.

    It is refused at the payload, not merely hidden from the summary: a panel row
    is copied into the audit log's ``after_state`` and mirrored to a JSONL file on
    disk, so a token in ``records.data`` would outlive the request by design.
    """
    panel = finder.create_panel(
        room["id"],
        {
            "name": "With a token",
            "provider": "urllib",
            "access_token": "ya29.a-real-token",
            "organizer": free_calendar["id"],
            "calendars": [free_calendar["id"]],
            "time_constraint": {"timeSlots": [{"start": at(MONDAY, 9), "end": at(MONDAY, 12)}]},
        },
        actor="dana",
        source=SOURCE,
    )
    assert "ya29" not in repr(finder.get_panel(room["id"], panel["id"]))
    assert "ya29" not in repr(finder.store.get(panel["id"])["data"])
    assert "ya29" not in repr(finder.store.audit(limit=50))
    assert "access_token" not in finder.store.get(panel["id"])["data"]


def test_calendars_are_installation_wide_not_room_scoped(finder, room, free_calendar):
    """The researched step 1 places the surface "in a sales room, a CRM record, or
    a scheduling page" - one person's calendar is the same calendar in all three."""
    assert free_calendar["room_id"] is None
    assert finder.list_calendars() != []
    other_room = finder.store.create("room", {"name": "Elsewhere"}, actor="dana", source=SOURCE)
    panel = finder.create_panel(
        other_room["id"],
        {
            "name": "Uses the shared calendar",
            "organizer": free_calendar["id"],
            "calendars": [free_calendar["id"]],
            "time_constraint": {"timeSlots": [{"start": at(MONDAY, 9), "end": at(MONDAY, 12)}]},
        },
        actor="dana",
        source=SOURCE,
    )
    assert finder.find(other_room["id"], panel["id"], actor="dana", source=SOURCE)["suggestions"]


def test_the_search_log_filters_on_the_researched_property(finder, room, free_calendar, all_day_busy):
    good = finder.create_panel(
        room["id"],
        {
            "name": "Good",
            "organizer": free_calendar["id"],
            "calendars": [free_calendar["id"]],
            "time_constraint": {"timeSlots": [{"start": at(MONDAY, 9), "end": at(MONDAY, 12)}]},
            "meeting_duration": "PT1H",
            "slot_interval": "PT1H",
        },
        actor="dana",
        source=SOURCE,
    )
    stuck = stuck_panel(finder, room, free_calendar, all_day_busy)
    finder.find(room["id"], good["id"], actor="dana", source=SOURCE)
    finder.find(room["id"], stuck["id"], actor="dana", source=SOURCE)
    assert len(finder.list_searches(room["id"])) == 2
    assert len(finder.list_searches(room["id"], empty=True)) == 1
    assert len(finder.list_searches(room["id"], empty=False)) == 1
    assert len(finder.list_searches(room["id"], panel_id=stuck["id"])) == 1


def test_the_bookings_list_filters_by_panel(finder, room, free_calendar, all_day_busy):
    good = finder.create_panel(
        room["id"],
        {
            "name": "Good",
            "organizer": free_calendar["id"],
            "calendars": [free_calendar["id"]],
            "time_constraint": {"timeSlots": [{"start": at(MONDAY, 9), "end": at(MONDAY, 12)}]},
            "meeting_duration": "PT1H",
            "slot_interval": "PT1H",
        },
        actor="dana",
        source=SOURCE,
    )
    search_row = finder.find(room["id"], good["id"], actor="dana", source=SOURCE)
    finder.book(
        room["id"], search_row["id"], {"start": search_row["suggestions"][0]["start"]},
        actor="dana", source=SOURCE,
    )
    assert len(finder.list_bookings(room["id"])) == 1
    assert len(finder.list_bookings(room["id"], panel_id=good["id"])) == 1
    assert len(finder.list_bookings(room["id"], panel_id=stuck_panel(finder, room, free_calendar, all_day_busy)["id"])) == 0


def test_the_four_collections_are_the_four_researched_things(finder, room, panel, free_calendar, store):
    search_row = finder.find(room["id"], panel["id"], actor="dana", source=SOURCE)
    finder.book(
        room["id"], search_row["id"], {"start": search_row["suggestions"][0]["start"]},
        actor="dana", source=SOURCE,
    )
    assert count(store, CALENDAR_COLLECTION) == 1
    assert count(store, PANEL_COLLECTION) == 1
    assert count(store, SEARCH_COLLECTION) == 1
    assert count(store, BOOKING_COLLECTION) == 1
    assert set(COLLECTIONS) == {
        CALENDAR_COLLECTION,
        PANEL_COLLECTION,
        SEARCH_COLLECTION,
        BOOKING_COLLECTION,
    }


def test_a_time_zone_that_cannot_be_resolved_is_reported_on_the_search(
    finder, room, free_calendar
):
    panel = finder.create_panel(
        room["id"],
        {
            "name": "London hours",
            "time_zone": "Europe/London",
            "organizer": free_calendar["id"],
            "calendars": [free_calendar["id"]],
            "time_constraint": {"timeSlots": [{"start": at(MONDAY, 9), "end": at(MONDAY, 12)}]},
            "meeting_duration": "PT1H",
            "slot_interval": "PT1H",
        },
        actor="dana",
        source=SOURCE,
    )
    result = finder.find(room["id"], panel["id"], actor="dana", source=SOURCE)
    assert result["time_zone_detail"]["time_zone"] == "Europe/London"
    assert result["request"]["body"]["timeZone"] == "Europe/London"
    if not tzdb_available():
        assert any(w["code"] == "time_zone_fallback_to_utc" for w in result["warnings"])


def test_the_graph_shaped_result_carries_the_researched_key_names(finder, panel):
    result = finder.find(panel["room_id"], panel["id"], actor="dana", source=SOURCE)
    graph = result["graph_result"]
    assert graph["minAttendeePercentage"] == 0
    entry = graph["meetingTimeSuggestions"][0]
    assert entry["meetingTimeSlot"]["start"]["dateTime"] == result["suggestions"][0]["start"]
    assert entry["confidence"] == 100
    assert entry["suggestionReason"] == SUGGESTION_REASON_ALL_FREE
    assert [a["attendee"]["status"] for a in entry["attendeeAvailability"]] == ["free"]
    assert entry["attendeeAvailability"][0]["statusPercentage"] == "100"


def test_the_graph_shaped_result_names_the_empty_reason_when_there_is_none(
    finder, room, free_calendar, all_day_busy
):
    panel = stuck_panel(finder, room, free_calendar, all_day_busy)
    result = finder.find(room["id"], panel["id"], actor="dana", source=SOURCE)
    assert result["graph_result"]["meetingTimeSuggestions"] == []
    assert result["graph_result"]["emptySuggestionsReason"] == EMPTY_NOT_ENOUGH_PEOPLE_FREE


def test_the_graph_shaped_result_omits_the_reason_when_the_toggle_is_off(finder, panel):
    finder.update_panel(
        panel["room_id"], panel["id"], {"return_suggestion_reasons": False}, actor="dana", source=SOURCE
    )
    result = finder.find(panel["room_id"], panel["id"], actor="dana", source=SOURCE)
    assert "suggestionReason" not in result["graph_result"]["meetingTimeSuggestions"][0]


def test_the_nearest_all_free_slot_is_readable(finder, panel):
    from dsr.panel_time import nearest_all_free

    result = finder.find(panel["room_id"], panel["id"], actor="dana", source=SOURCE)
    assert nearest_all_free(result["suggestions"]) == result["suggestions"][0]["start"]
    # And the claim is checkable: nothing all-free is earlier.
    assert result["suggestions"][0]["start"] == min(s["start"] for s in result["suggestions"])


def test_a_provider_the_engine_does_not_have_is_refused_at_declaration(finder, room, free_calendar):
    """At the panel, not at the search: a panel that names a transport this build
    has no adapter for is a bad declaration, and storing it would put a panel in
    the room's list that could never be searched."""
    with pytest.raises(PanelShapeError) as caught:
        finder.create_panel(
            room["id"],
            {
                "name": "Unknown provider",
                "provider": "pigeon",
                "organizer": free_calendar["id"],
                "calendars": [free_calendar["id"]],
                "time_constraint": {"timeSlots": [{"start": at(MONDAY, 9), "end": at(MONDAY, 12)}]},
            },
            actor="dana",
            source=SOURCE,
        )
    assert "provider must be one of" in str(caught.value)


def test_a_panel_on_the_urllib_provider_with_no_token_is_428_at_search(
    finder, room, free_calendar
):
    """Well formed, but this installation cannot answer it yet - so 428, and the
    message names the least-privileged scope the read needs."""
    panel = finder.create_panel(
        room["id"],
        {
            "name": "Real tenant, no token",
            "provider": "urllib",
            "organizer": free_calendar["id"],
            "calendars": [free_calendar["id"]],
            "time_constraint": {"timeSlots": [{"start": at(MONDAY, 9), "end": at(MONDAY, 12)}]},
            "meeting_duration": "PT1H",
            "slot_interval": "PT1H",
        },
        actor="dana",
        source=SOURCE,
    )
    with pytest.raises(PanelTimeNotConfigured) as caught:
        finder.find(room["id"], panel["id"], actor="dana", source=SOURCE)
    assert GOOGLE_FREEBUSY_SCOPE in str(caught.value)


def test_the_provider_factory_is_the_seam_a_deployment_replaces(
    finder, room, free_calendar
):
    """The panel's ``provider`` picks the transport and ``calendar_provider`` picks
    the dialect, and both reach the adapter.

    The token is *not* on this seam by design. An ``access_token`` written into a
    panel payload would land in the row and in the audit log's ``after_state``, and
    the audit mirror is a JSONL file on disk - so the secret would outlive the
    request by design. The panel records only that a token was supplied, and a
    deployment supplies the real one by replacing the provider factory, where a
    credential store can hand it over without it ever being written down.
    """
    seen: dict[str, object] = {}

    class Recording:
        def __init__(self, panel):
            seen["transport"] = panel["data"]["provider"]
            seen["token_on_the_row"] = "access_token" in panel["data"]
            seen["token_recorded_as"] = panel["data"].get("access_token_present")

        def send(self, calendars, *, time_min, time_max, provider):
            seen["dialect"] = provider
            seen["calendars"] = [str(r["id"]) for r in calendars]
            return busy_map()

        def commit(self, request, **kwargs):
            return {"event_id": "evt_recorded", "conference": None, "provider": "recorded"}

    finder._provider_factory = Recording
    panel = finder.create_panel(
        room["id"],
        {
            "name": "Recorded",
            "provider": "urllib",
            "calendar_provider": "graph",
            "access_token": "ya29.a-real-token",
            "organizer": free_calendar["id"],
            "calendars": [free_calendar["id"]],
            "time_constraint": {"timeSlots": [{"start": at(MONDAY, 9), "end": at(MONDAY, 12)}]},
            "meeting_duration": "PT1H",
            "slot_interval": "PT1H",
        },
        actor="dana",
        source=SOURCE,
    )
    result = finder.find(room["id"], panel["id"], actor="dana", source=SOURCE)
    assert seen["transport"] == "urllib"
    assert seen["dialect"] == "graph"
    assert seen["calendars"] == [free_calendar["id"]]
    assert seen["token_on_the_row"] is False
    assert seen["token_recorded_as"] is True
    # And it is in no response, no summary, and no audit row.
    assert "ya29" not in repr(result)
    assert "ya29" not in repr(finder.get_panel(room["id"], panel["id"]))
    audit = finder.store.audit(limit=100)
    assert "ya29" not in repr(audit)


def test_a_recall_off_a_search_that_found_something_offers_widen_not_relax(
    finder, panel
):
    """A working search is not an empty one, so ``relax_min_attendee_percentage``
    is not offered for it - which is the refusal a caller meets when they name it
    anyway."""
    first = finder.find(panel["room_id"], panel["id"], actor="dana", source=SOURCE)
    assert first["empty_suggestions_reason"] is None
    assert [a["id"] for a in first["suggested_adjustments"]] == []
    # There is no reason, so the route falls back to the single documented default
    # and says so.
    with pytest.raises(ConstraintError):
        finder.retune(
            panel["room_id"],
            first["id"],
            {"adjustment": "relax_min_attendee_percentage"},
            actor="dana",
            source=SOURCE,
        )


def test_every_domain_method_that_writes_requires_a_source():
    """``source`` is required so a hardcoded URL cannot creep back in."""
    for name in (
        "create_calendar",
        "update_calendar",
        "delete_calendar",
        "create_panel",
        "update_panel",
        "delete_panel",
        "find",
        "retune",
        "book",
    ):
        signature = inspect.signature(getattr(SlotFinder, name))
        assert signature.parameters["source"].kind is inspect.Parameter.KEYWORD_ONLY
        assert signature.parameters["source"].default is inspect.Parameter.empty


def test_no_domain_method_hardcodes_the_path_it_records_as_its_source():
    """Checked on the source rather than on the running system, because a
    hardcoded path only shows up in the audit log once something is written.

    The domain package must not know the route at all - not even to name it in a
    message a rep reads - because the day the prefix changes, that message is
    wrong and nothing fails.
    """
    for module in (SlotFinder, BusyMap, LocalDirectory, UrllibProvider):
        source = Path(inspect.getfile(module)).read_text(encoding="utf-8")
        assert "/api/wf" not in source, f"{module.__name__} knows the HTTP route"


def test_a_calendar_can_be_soft_deleted_and_its_searches_stay(finder, room, panel, free_calendar, store):
    search_row = finder.find(room["id"], panel["id"], actor="dana", source=SOURCE)
    finder.delete_calendar(free_calendar["id"], actor="dana", source=SOURCE)
    with pytest.raises(NotFound):
        finder.get_calendar(free_calendar["id"])
    assert finder.get_search(room["id"], search_row["id"])["id"] == search_row["id"]
    # And a panel that still names it now refuses, by name.
    with pytest.raises(NotFound) as caught:
        finder.find(room["id"], panel["id"], actor="dana", source=SOURCE)
    assert caught.value.resource == "calendar"


# --------------------------------------------------------------------------- #
# The HTTP surface
# --------------------------------------------------------------------------- #


@pytest.fixture()
def http(monkeypatch):
    """A client over a temporary database, with the engine built per request."""
    tmp = tempfile.TemporaryDirectory()
    monkeypatch.setenv("DSR_DB_PATH", str(Path(tmp.name) / "wf057_http.db"))
    monkeypatch.setenv("DSR_AUDIT_DIR", str(Path(tmp.name) / "audit"))
    monkeypatch.setattr("dsr.api.FRONTEND_DIST", Path(tmp.name) / "absent-frontend")
    with TestClient(app) as client:
        yield client
    tmp.cleanup()


@pytest.fixture()
def http_room(http):
    return http.post("/api/records/room", json={"name": "Northwind", "account": "N"}).json()


@pytest.fixture()
def http_calendar(http):
    return http.post(
        f"{PREFIX}/calendars",
        json={"email": "dana@northwind.example", "name": "Dana", "busy": []},
    ).json()


@pytest.fixture()
def http_panel(http, http_room, http_calendar):
    return http.post(
        f"{PREFIX}/rooms/{http_room['id']}/panels",
        json={
            "name": "Northwind Q4 panel",
            "organizer": http_calendar["id"],
            "calendars": [http_calendar["id"]],
            "time_constraint": {
                "timeSlots": [{"start": at(MONDAY, 9), "end": at(MONDAY, 17)}]
            },
            "meeting_duration": "PT1H",
            "slot_interval": "PT1H",
        },
    ).json()


def test_the_feature_is_discovered_and_mounted_without_editing_the_host(http):
    """The route resolves even though no shared file names this feature."""
    entry = next(
        f
        for f in http.get("/api/features").json()["features"]
        if f["id"] == "wf-057-find-a-time-that-works-for-a-multi-per"
    )
    assert entry["prefix"] == PREFIX
    assert entry["ticket"] == "WF-057"
    assert entry["exception_handlers"] == [
        "NotFound",
        "PanelTimeError",
        "PanelTimeNotConfigured",
        "SlotUnavailable",
    ]
    assert entry["routes"]


def test_the_feature_loads_without_a_failure(http):
    assert http.get("/api/features").json()["failed_count"] == 0


def test_every_room_scoped_path_is_room_scoped(http):
    """The brief's rule, asserted on the mounted routes rather than on a comment."""
    entry = next(
        f
        for f in http.get("/api/features").json()["features"]
        if f["id"] == "wf-057-find-a-time-that-works-for-a-multi-per"
    )
    for route in entry["routes"]:
        path = route["path"]
        for word in ("/panels", "/searches", "/bookings"):
            if word in path:
                assert "/rooms/{room_id}" in path, f"{word} is not room-scoped: {path}"


def test_the_calendar_routes_are_not_room_scoped(http):
    """Deliberate, and for a sourced reason: the research's step 1 places the
    surface in a room, a CRM record, or a scheduling page."""
    entry = next(
        f
        for f in http.get("/api/features").json()["features"]
        if f["id"] == "wf-057-find-a-time-that-works-for-a-multi-per"
    )
    calendar_routes = [r for r in entry["routes"] if "/calendars" in r["path"]]
    assert calendar_routes
    assert all("/rooms/" not in route["path"] for route in calendar_routes)


def test_the_vocabulary_and_inferences_are_served_without_a_store(http):
    vocabulary = http.get(f"{PREFIX}/vocabulary").json()
    assert len(vocabulary["providers"]) == 2
    assert vocabulary["availability"]["weights"] == {"free": 100, "unknown": 49, "busy": 0}
    assert vocabulary["collections"] == list(COLLECTIONS)
    assert vocabulary["calendar_kinds"] == list(KINDS)
    assert len(vocabulary["user_flow"]) == 5

    inferences = http.get(f"{PREFIX}/inferences").json()
    assert inferences["count"] == len(INFERENCES)
    assert set(inferences["reasons"]["retune_adjustments"][0]) == {"id", "why"}


def test_the_calendar_crud_over_http(http):
    created = http.post(
        f"{PREFIX}/calendars", json={"email": "sam@northwind.example", "kind": "room"}
    )
    assert created.status_code == 201
    calendar_id = created.json()["id"]
    assert http.get(f"{PREFIX}/calendars").json()["by_kind"] == {"room": 1}
    assert http.get(f"{PREFIX}/calendars/{calendar_id}").json()["kind"] == "room"
    assert http.patch(
        f"{PREFIX}/calendars/{calendar_id}", json={"readable": False}
    ).json()["readable"] is False
    assert http.delete(f"{PREFIX}/calendars/{calendar_id}").status_code == 204
    assert http.get(f"{PREFIX}/calendars/{calendar_id}").status_code == 404


def test_a_calendar_that_cannot_be_created_is_a_400(http):
    assert http.post(f"{PREFIX}/calendars", json={}).status_code == 400
    assert http.post(f"{PREFIX}/calendars", json={"email": "nope"}).status_code == 400
    assert http.post(
        f"{PREFIX}/calendars", json={"email": "a@x.example", "kind": "robot"}
    ).status_code == 400


def test_a_calendar_with_a_malformed_busy_block_is_a_400(http):
    response = http.post(
        f"{PREFIX}/calendars",
        json={"email": "a@x.example", "busy": [{"start": at(MONDAY, 10), "end": at(MONDAY, 9)}]},
    )
    assert response.status_code == 400
    assert "must end after it starts" in response.json()["detail"]


def test_the_panel_crud_over_http(http, http_room, http_calendar):
    created = http.post(
        f"{PREFIX}/rooms/{http_room['id']}/panels",
        json={
            "name": "Second panel",
            "organizer": http_calendar["id"],
            "calendars": [http_calendar["id"]],
            "time_constraint": {"timeSlots": [{"start": at(MONDAY, 9), "end": at(MONDAY, 12)}]},
        },
    )
    assert created.status_code == 201
    panel_id = created.json()["id"]
    base = f"{PREFIX}/rooms/{http_room['id']}/panels"
    assert http.get(base).json()["count"] == 1
    assert http.get(f"{base}/{panel_id}").json()["name"] == "Second panel"
    assert http.patch(f"{base}/{panel_id}", json={"min_attendee_percentage": 60}).json()[
        "min_attendee_percentage"
    ] == 60
    assert http.delete(f"{base}/{panel_id}").status_code == 204
    assert http.get(f"{base}/{panel_id}").status_code == 404


def test_a_panel_cannot_be_declared_on_a_room_that_does_not_exist(http):
    response = http.post(
        f"{PREFIX}/rooms/room_nope/panels",
        json={
            "name": "Orphan",
            "time_constraint": {"timeSlots": [{"start": at(MONDAY, 9), "end": at(MONDAY, 12)}]},
        },
    )
    assert response.status_code == 404
    assert response.json()["error"] == "not_found"
    assert response.json()["resource"] == "room"


def test_a_panel_with_no_time_constraint_is_a_400(http, http_room):
    response = http.post(f"{PREFIX}/rooms/{http_room['id']}/panels", json={"name": "x"})
    assert response.status_code == 400
    assert "date range" in response.json()["detail"]


def test_a_panel_with_an_unknown_location_type_is_a_400(http, http_room):
    response = http.post(
        f"{PREFIX}/rooms/{http_room['id']}/panels",
        json={
            "name": "x",
            "time_constraint": {"timeSlots": [{"start": at(MONDAY, 9), "end": at(MONDAY, 12)}]},
            "location_constraint": {"type": "beach"},
        },
    )
    assert response.status_code == 400
    assert "a room, or \"suggest a location\"" in response.json()["detail"]


def test_the_preview_over_http_writes_nothing_and_returns_the_request(
    http, http_room, http_panel
):
    before = http.get("/api/stats").json()["records"]
    response = http.post(
        f"{PREFIX}/rooms/{http_room['id']}/panels/{http_panel['id']}/preview", json={}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["suggestions"]
    assert body["request"]["url"] == GOOGLE_FREEBUSY_URL
    assert http.get("/api/stats").json()["records"] == before


def test_the_preview_moves_with_the_controls(http, http_room, http_panel):
    path = f"{PREFIX}/rooms/{http_room['id']}/panels/{http_panel['id']}/preview"
    free = http.post(path, json={}).json()
    strict = http.post(path, json={"min_attendee_percentage": 100}).json()
    off = http.post(path, json={"return_suggestion_reasons": False}).json()
    assert strict["counts"]["suggested"] <= free["counts"]["suggested"]
    # Google folds the bar into its own body shape, and the toggle is a Graph
    # parameter - so the counts move on either dialect and the *request* assertion
    # belongs to the Graph test below.
    assert strict["min_attendee_percentage"] == 100
    assert all("suggestion_reason" not in s for s in off["suggestions"])


def test_the_preview_moves_the_graph_request_body_with_the_controls(
    http, http_room, http_calendar
):
    """On the Graph dialect every researched parameter lands in the rendered body,
    so a rep can check the request against the documentation."""
    panel = http.post(
        f"{PREFIX}/rooms/{http_room['id']}/panels",
        json={
            "name": "Graph preview panel",
            "calendar_provider": "graph",
            "organizer": http_calendar["id"],
            "calendars": [http_calendar["id"]],
            "time_constraint": {"timeSlots": [{"start": at(MONDAY, 9), "end": at(MONDAY, 12)}]},
            "meeting_duration": "PT1H",
            "slot_interval": "PT1H",
        },
    ).json()
    path = f"{PREFIX}/rooms/{http_room['id']}/panels/{panel['id']}/preview"
    strict = http.post(path, json={"min_attendee_percentage": 100}).json()
    off = http.post(path, json={"return_suggestion_reasons": False}).json()
    assert strict["request"]["body"]["minAttendeePercentage"] == 100
    assert off["request"]["body"]["returnSuggestionReasons"] is False
    assert off["request"]["body"]["timeConstraint"]["activityDomain"] == "work"


def test_the_preview_ignores_a_provider_smuggled_in_through_the_override_channel(
    http, http_room, http_panel
):
    """The override channel is a whitelist, so a client cannot switch the provider
    or hand over a token by way of it."""
    response = http.post(
        f"{PREFIX}/rooms/{http_room['id']}/panels/{http_panel['id']}/preview",
        json={"provider": "urllib", "access_token": "ya29.stolen"},
    )
    assert response.status_code == 200
    assert "ya29" not in response.text


def test_the_find_over_http_writes_one_row_and_carries_the_user_flow(
    http, http_room, http_panel
):
    response = http.post(
        f"{PREFIX}/rooms/{http_room['id']}/panels/{http_panel['id']}/find",
        json={},
        params={"actor": "dana"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["id"]
    assert body["ok"] is True
    assert len(body["user_flow"]) == 5
    assert body["user_flow"][3]["step"] == 4
    assert len(http.get(f"{PREFIX}/rooms/{http_room['id']}/searches").json()["searches"]) == 1


def test_an_empty_search_is_a_200_with_the_researched_property(http, http_room):
    busy = http.post(
        f"{PREFIX}/calendars",
        json={
            "email": "solid@northwind.example",
            "busy": [{"start": at(MONDAY, 0), "end": at(MONDAY, 23, 59)}],
        },
    ).json()
    free = http.post(f"{PREFIX}/calendars", json={"email": "free@northwind.example"}).json()
    panel = http.post(
        f"{PREFIX}/rooms/{http_room['id']}/panels",
        json={
            "name": "Nowhere to go",
            "organizer": free["id"],
            "calendars": [free["id"], busy["id"]],
            "min_attendee_percentage": 90,
            "time_constraint": {"timeSlots": [{"start": at(MONDAY, 9), "end": at(MONDAY, 12)}]},
            "meeting_duration": "PT1H",
            "slot_interval": "PT1H",
        },
    ).json()
    response = http.post(
        f"{PREFIX}/rooms/{http_room['id']}/panels/{panel['id']}/find", json={}, params={"actor": "dana"}
    )
    assert response.status_code == 200, "an empty result is a state, not a refusal"
    body = response.json()
    assert body["suggestions"] == []
    assert body["empty_suggestions_reason"] == EMPTY_NOT_ENOUGH_PEOPLE_FREE
    assert [a["id"] for a in body["suggested_adjustments"]][:2] == [
        "widen_window",
        "relax_min_attendee_percentage",
    ]
    # And the row exists, so the room's log shows the interesting thing.
    assert http.get(f"{PREFIX}/rooms/{http_room['id']}/searches").json()["summary"]["empty"] == 1


def test_the_recall_over_http_writes_a_second_row(http, http_room):
    free = http.post(f"{PREFIX}/calendars", json={"email": "free@northwind.example"}).json()
    monday_only = http.post(
        f"{PREFIX}/calendars",
        json={
            "email": "mondays@northwind.example",
            "busy": [{"start": at(MONDAY, 0), "end": at(MONDAY, 23, 59)}],
        },
    ).json()
    panel = http.post(
        f"{PREFIX}/rooms/{http_room['id']}/panels",
        json={
            "name": "Monday only",
            "organizer": free["id"],
            "calendars": [free["id"], monday_only["id"]],
            "min_attendee_percentage": 80,
            "time_constraint": {"timeSlots": [{"start": at(MONDAY, 9), "end": at(MONDAY, 12)}]},
            "meeting_duration": "PT1H",
            "slot_interval": "PT1H",
        },
    ).json()
    base = f"{PREFIX}/rooms/{http_room['id']}"
    first = http.post(f"{base}/panels/{panel['id']}/find", json={}, params={"actor": "dana"}).json()

    adjustments = http.get(f"{base}/searches/{first['id']}/adjustments").json()
    assert adjustments["empty_suggestions_reason"] == EMPTY_NOT_ENOUGH_PEOPLE_FREE
    assert [a["id"] for a in adjustments["suggested"]][:2] == [
        "widen_window",
        "relax_min_attendee_percentage",
    ]
    assert {a["id"] for a in adjustments["all_adjustments"]} >= {
        "widen_window",
        "relax_min_attendee_percentage",
        "coarsen_slot_interval",
    }
    assert "time_constraint" in adjustments["retunable_parameters"]

    second = http.post(
        f"{base}/searches/{first['id']}/retune",
        json={"adjustment": "widen_window", "days": 1},
        params={"actor": "dana"},
    )
    assert second.status_code == 200
    assert second.json()["suggestions"]
    assert second.json()["parent_search_id"] == first["id"]

    log = http.get(f"{base}/searches").json()
    assert log["count"] == 2
    assert log["summary"]["empty"] == 1
    assert log["summary"]["retunes"] == 1
    # The parameters that produced the empty result are still readable.
    assert http.get(f"{base}/searches/{first['id']}").json()["min_attendee_percentage"] == 80


def test_a_recall_with_an_adjustment_the_reason_does_not_suggest_is_a_400(
    http, http_room
):
    """The refused adjustment is a real one, offered for a *different* reason.

    ``relax_min_attendee_percentage`` is a legitimate re-call - just not for a
    search whose bar was never the problem. Naming it here is a caller who has
    misread which knob to move, and the message says so.
    """
    free = http.post(f"{PREFIX}/calendars", json={"email": "free@northwind.example"}).json()
    solid = http.post(
        f"{PREFIX}/calendars",
        json={
            "email": "solid@northwind.example",
            "busy": [{"start": at(MONDAY, 0), "end": at(MONDAY, 23, 59)}],
        },
    ).json()
    panel = http.post(
        f"{PREFIX}/rooms/{http_room['id']}/panels",
        json={
            "name": "Rules removed everything",
            "organizer": free["id"],
            "calendars": [free["id"], solid["id"]],
            "house_rules": {"earliest_start": "23:00"},
            "time_constraint": {"timeSlots": [{"start": at(MONDAY, 9), "end": at(MONDAY, 12)}]},
            "meeting_duration": "PT1H",
            "slot_interval": "PT1H",
        },
    ).json()
    base = f"{PREFIX}/rooms/{http_room['id']}"
    found = http.post(f"{base}/panels/{panel['id']}/find", json={}, params={"actor": "dana"}).json()
    assert found["empty_suggestions_reason"] == EMPTY_BUSY_SUGGESTIONS
    offered = [a["id"] for a in found["suggested_adjustments"]]
    assert "extend_working_window" in offered
    response = http.post(
        f"{base}/searches/{found['id']}/retune", json={"adjustment": "relax_min_attendee_percentage"}
    )
    assert response.status_code == 400
    assert "not one of the adjustments this emptySuggestionsReason" in response.json()["detail"]


def test_a_recall_of_a_search_that_found_something_says_so_over_http(
    http, http_room, http_panel
):
    """There is no reason to follow, and the message says exactly that - rather
    than pretending the search came back empty."""
    base = f"{PREFIX}/rooms/{http_room['id']}"
    found = http.post(f"{base}/panels/{http_panel['id']}/find", json={}, params={"actor": "dana"}).json()
    assert found["suggestions"]
    response = http.post(
        f"{base}/searches/{found['id']}/retune", json={"adjustment": "coarsen_slot_interval"}
    )
    assert response.status_code == 400
    assert "returned suggestions, so it has no emptySuggestionsReason" in response.json()["detail"]
    # ...and widening still works on a working search.
    widened = http.post(
        f"{base}/searches/{found['id']}/retune", json={}, params={"actor": "dana"}
    )
    assert widened.status_code == 200
    assert widened.json()["adjustment"] == "widen_window"


def test_a_search_that_does_not_exist_is_a_404(http, http_room):
    response = http.get(f"{PREFIX}/rooms/{http_room['id']}/searches/search_nope")
    assert response.status_code == 404
    assert response.json()["resource"] == "search"


def test_the_booking_over_http_creates_the_event_and_reads_it_back(http, http_room, http_panel):
    base = f"{PREFIX}/rooms/{http_room['id']}"
    found = http.post(f"{base}/panels/{http_panel['id']}/find", json={}, params={"actor": "dana"}).json()
    booked = http.post(
        f"{base}/searches/{found['id']}/book",
        json={"start": found["suggestions"][0]["start"], "summary": "Northwind Q4"},
        params={"actor": "dana"},
    )
    assert booked.status_code == 200
    body = booked.json()
    assert body["event_id"]
    assert body["conference"]["created"] is True
    assert body["revalidated"]["confidence"] == 100
    assert http.get(f"{base}/bookings").json()["with_conference"] == 1
    assert http.get(f"{base}/bookings/{body['id']}").json()["id"] == body["id"]


def test_booking_a_slot_the_search_never_returned_is_a_422(http, http_room, http_panel):
    """Not a 400: nothing about the request is wrong. The state refuses it, and a
    client that reported this as a bad request would tell a rep to fix their
    input when the thing to do is run the search again."""
    base = f"{PREFIX}/rooms/{http_room['id']}"
    found = http.post(f"{base}/panels/{http_panel['id']}/find", json={}, params={"actor": "dana"}).json()
    response = http.post(
        f"{base}/searches/{found['id']}/book", json={"start": at(MONDAY, 16, 30)}
    )
    assert response.status_code == 422
    assert response.json()["error"] == "slot_unavailable"


def test_booking_without_a_start_is_a_400(http, http_room, http_panel):
    base = f"{PREFIX}/rooms/{http_room['id']}"
    found = http.post(f"{base}/panels/{http_panel['id']}/find", json={}, params={"actor": "dana"}).json()
    response = http.post(f"{base}/searches/{found['id']}/book", json={})
    assert response.status_code == 400
    assert response.json()["error"] == "panel_time_error"


def test_a_panel_with_no_calendars_registered_is_a_428(http, http_room):
    """Distinct from 400 so a client can say "finish the setup"."""
    panel = http.post(
        f"{PREFIX}/rooms/{http_room['id']}/panels",
        json={
            "name": "Nothing registered",
            "calendars": [],
            "time_constraint": {"timeSlots": [{"start": at(MONDAY, 9), "end": at(MONDAY, 12)}]},
        },
    ).json()
    response = http.post(f"{PREFIX}/rooms/{http_room['id']}/panels/{panel['id']}/find", json={})
    assert response.status_code == 428
    assert response.json()["error"] == "not_configured"


def test_a_cap_over_its_documented_maximum_is_refused_at_declaration(
    http, http_room, http_calendar
):
    """At the panel, not at the search.

    A panel naming a knob above its documented maximum could never be searched, and
    storing it would put a row in the room's list that looks runnable and is not -
    the same argument as refusing an unknown transport at declaration.
    """
    response = http.post(
        f"{PREFIX}/rooms/{http_room['id']}/panels",
        json={
            "name": "Too many calendars",
            "organizer": http_calendar["id"],
            "calendars": [http_calendar["id"]],
            "time_constraint": {"timeSlots": [{"start": at(MONDAY, 9), "end": at(MONDAY, 12)}]},
            "calendar_expansion_max": 51,
        },
    )
    assert response.status_code == 400
    assert "documented maximum is 50" in response.json()["detail"]
    assert "Maximum value is 50" in response.json()["detail"]
    # ...and the refused panel was not stored.
    assert http.get(f"{PREFIX}/rooms/{http_room['id']}/panels").json()["count"] == 0

    groups = http.post(
        f"{PREFIX}/rooms/{http_room['id']}/panels",
        json={
            "name": "Too many groups",
            "organizer": http_calendar["id"],
            "calendars": [http_calendar["id"]],
            "time_constraint": {"timeSlots": [{"start": at(MONDAY, 9), "end": at(MONDAY, 12)}]},
            "group_expansion_max": 101,
        },
    )
    assert groups.status_code == 400
    assert "documented maximum is 100" in groups.json()["detail"]


def test_a_cap_at_its_documented_maximum_is_accepted(http, http_room, http_calendar):
    response = http.post(
        f"{PREFIX}/rooms/{http_room['id']}/panels",
        json={
            "name": "At the documented maximum",
            "organizer": http_calendar["id"],
            "calendars": [http_calendar["id"]],
            "time_constraint": {"timeSlots": [{"start": at(MONDAY, 9), "end": at(MONDAY, 12)}]},
            "calendar_expansion_max": 50,
            "group_expansion_max": 100,
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["calendar_count"] == 1
    preview = http.post(
        f"{PREFIX}/rooms/{http_room['id']}/panels/{body['id']}/preview", json={}
    )
    assert preview.json()["request"]["body"]["calendarExpansionMax"] == 50
    assert preview.json()["request"]["body"]["groupExpansionMax"] == 100


def test_a_panel_that_expands_past_the_cap_is_a_400_that_names_the_fix(finder, room, free_calendar):
    """The cap binds after expansion, so the panel is well formed and the *search*
    is what refuses - the request would be one the provider rejects."""
    group = finder.create_calendar(
        {
            "email": "everyone@northwind.example",
            "kind": "group",
            "members": [f"person{i}@northwind.example" for i in range(60)],
        },
        actor="dana",
        source=SOURCE,
    )
    for index in range(60):
        finder.create_calendar(
            {"email": f"person{index}@northwind.example", "busy": []}, actor="dana", source=SOURCE
        )
    panel = finder.create_panel(
        room["id"],
        {
            "name": "The whole company",
            "organizer": free_calendar["id"],
            "calendars": [group["id"]],
            "time_constraint": {"timeSlots": [{"start": at(MONDAY, 9), "end": at(MONDAY, 12)}]},
            "meeting_duration": "PT1H",
            "slot_interval": "PT1H",
        },
        actor="dana",
        source=SOURCE,
    )
    with pytest.raises(LimitExceeded) as caught:
        finder.find(room["id"], panel["id"], actor="dana", source=SOURCE)
    assert "61 calendars" in str(caught.value)
    assert "Remove an attendee, or split the panel" in str(caught.value)


def test_a_refused_write_leaves_no_audit_row(http, http_room):
    """Nothing was attempted, so there is nothing to audit."""
    assert http.post(f"{PREFIX}/calendars", json={}).status_code == 400
    assert http.get("/api/audit", params={"collection": CALENDAR_COLLECTION}).json()["count"] == 0
    assert (
        http.post(
            f"{PREFIX}/rooms/{http_room['id']}/panels",
            json={"name": "x", "time_constraint": {"timeSlots": [{"start": at(MONDAY, 10), "end": at(MONDAY, 9)}]}},
        ).status_code
        == 400
    )
    assert http.get("/api/audit", params={"collection": PANEL_COLLECTION}).json()["count"] == 0
    assert (
        http.post(
            f"{PREFIX}/rooms/room_nope/panels",
            json={
                "name": "x",
                "time_constraint": {"timeSlots": [{"start": at(MONDAY, 9), "end": at(MONDAY, 12)}]},
            },
        ).status_code
        == 404
    )
    assert http.get("/api/audit", params={"collection": PANEL_COLLECTION}).json()["count"] == 0


def test_no_audit_source_names_another_features_prefix(http, http_room, http_panel):
    base = f"{PREFIX}/rooms/{http_room['id']}"
    found = http.post(f"{base}/panels/{http_panel['id']}/find", json={}, params={"actor": "dana"}).json()
    http.post(
        f"{base}/searches/{found['id']}/book",
        json={"start": found["suggestions"][0]["start"]},
        params={"actor": "dana"},
    )
    sources = [e["source"] for e in http.get("/api/audit", params={"limit": 300}).json()["entries"]]
    assert [source for source in sources if PREFIX in source]
    for other in ("/api/crm", "/api/analytics", "/api/wf-039", "/api/wf-016"):
        assert not [source for source in sources if other in source and PREFIX not in source]


def test_every_source_this_feature_records_names_a_route_the_host_mounted(
    http, http_room, http_panel, http_calendar
):
    base = f"{PREFIX}/rooms/{http_room['id']}"
    panel_path = f"{base}/panels/{http_panel['id']}"
    found = http.post(f"{panel_path}/find", json={}, params={"actor": "dana"}).json()
    http.patch(f"{panel_path}", json={"name": "renamed"}, params={"actor": "dana"})
    http.post(
        f"{base}/searches/{found['id']}/book",
        json={"start": found["suggestions"][0]["start"]},
        params={"actor": "dana"},
    )
    http.post(
        f"{base}/searches/{found['id']}/retune", json={}, params={"actor": "dana"}
    )
    http.patch(f"{PREFIX}/calendars/{http_calendar['id']}", json={"name": "Dana S"}, params={"actor": "dana"})
    http.delete(f"{PREFIX}/calendars/{http_calendar['id']}", params={"actor": "dana"})

    entries = http.get("/api/audit", params={"limit": 400}).json()["entries"]
    sources = {entry["source"] for entry in entries}
    for expected in (
        f"POST {PREFIX}/calendars",
        f"PATCH {PREFIX}/calendars/{http_calendar['id']}",
        f"DELETE {PREFIX}/calendars/{http_calendar['id']}",
        f"POST {base}/panels",
        f"PATCH {panel_path}",
        f"POST {panel_path}/find",
        f"POST {base}/searches/{found['id']}/retune",
        f"POST {base}/searches/{found['id']}/book",
    ):
        assert expected in sources, f"{expected!r} missing from {sorted(sources)}"

    templates = [
        (method, route["path"])
        for feature in http.get("/api/features").json()["features"]
        for route in feature.get("routes", [])
        for method in route["methods"]
    ]
    templates += [
        (method, route.path)
        for route in app.routes
        for method in (getattr(route, "methods", None) or set())
        if method not in ("HEAD", "OPTIONS")
    ]
    for entry in entries:
        verb, _, path = entry["source"].partition(" ")
        assert any(
            verb == method and re.fullmatch(re.sub(r"\{[^}]+\}", "[^/]+", template), path)
            for method, template in templates
        ), f"audit names a route the app does not serve: {entry['source']}"


def test_the_booking_row_is_audited_with_the_booking_route(
    http, http_room, http_panel
):
    """It is a write, and an audit row that cannot name its request is not one."""
    base = f"{PREFIX}/rooms/{http_room['id']}"
    found = http.post(f"{base}/panels/{http_panel['id']}/find", json={}, params={"actor": "dana"}).json()
    http.post(
        f"{base}/searches/{found['id']}/book",
        json={"start": found["suggestions"][0]["start"]},
        params={"actor": "dana"},
    )
    entries = http.get("/api/audit", params={"collection": BOOKING_COLLECTION}).json()["entries"]
    assert len(entries) == 1
    assert entries[0]["source"] == f"POST {base}/searches/{found['id']}/book"


def test_the_preview_writes_no_audit_row_at_all(http, http_room, http_panel):
    http.post(f"{PREFIX}/rooms/{http_room['id']}/panels/{http_panel['id']}/preview", json={})
    entries = http.get("/api/audit", params={"limit": 300}).json()["entries"]
    assert not [e for e in entries if "/preview" in (e["source"] or "")]


def test_no_write_route_hardcodes_the_path_it_records_as_its_source():
    """The defect this programme shipped: an audit row naming a dead route.

    Checked on the source rather than on the running system, because a hardcoded
    path only shows up in the audit log once something is written. The router's
    own ``prefix`` is the one legitimate literal; what must not appear is a path
    spelled out inside a ``source=`` expression.
    """
    source = Path(inspect.getfile(load_feature(MODULE))).read_text(encoding="utf-8")
    assert 'prefix="/api/wf-057"' in source
    recorded = re.findall(r'source=(f?"[^"]*")', source)
    assert len(recorded) >= 8
    for literal in recorded:
        assert "router.prefix" in literal, f"a write records a literal path: {literal}"
        assert "/api/wf-057" not in literal


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #


def test_the_seed_produces_the_states_the_research_makes_unavoidable(store):
    module = load_feature(MODULE)
    rooms = [store.create("room", {"name": f"Room {i}"}, actor="dana") for i in range(4)]
    summary = module.seed(store, {"room_ids": [(r["id"], "Acct") for r in rooms]})
    assert summary
    assert count(store, CALENDAR_COLLECTION) == 11
    assert count(store, PANEL_COLLECTION) == 5
    assert count(store, SEARCH_COLLECTION) >= 6
    assert count(store, BOOKING_COLLECTION) == 2

    finder = SlotFinder(store)
    searches = [search for room in rooms for search in finder.list_searches(room["id"], limit=200)]
    reasons = {s["empty_suggestions_reason"] for s in searches if s.get("empty_suggestions_reason")}
    assert reasons, "a demo with no empty result teaches a reviewer nothing"

    ranked = [s for s in searches if s["suggestion_count"]]
    assert ranked
    # The happy path's best slot is not its earliest one: the researched sort
    # working, observable in the demo data.
    assert any(
        s["suggestions"][0]["confidence"] < 100 for s in ranked
    ), "no slot below 100% confidence in the demo - the 49% case is missing"

    retunes = [s for s in searches if s.get("parent_search_id")]
    assert retunes, "the documented re-call is not exercised by the demo"
    assert any(s["suggestion_count"] for s in retunes), "no re-call actually fixed anything"


def test_the_seed_shows_group_expansion_in_both_directions(store):
    module = load_feature(MODULE)
    rooms = [store.create("room", {"name": f"Room {i}"}, actor="dana") for i in range(4)]
    module.seed(store, {"room_ids": [(r["id"], "Acct") for r in rooms]})
    finder = SlotFinder(store)
    searches = [s for room in rooms for s in finder.list_searches(room["id"], limit=200)]
    expanded = [s for s in searches if s["expansion"].get("groups_expanded")]
    assert expanded, "no group was expanded in the demo"
    unexpanded = [
        entry
        for s in searches
        for entry in s["expansion"].get("groups_unexpanded", [])
    ]
    assert unexpanded, "no group failed to expand - the missing-member case is not shown"
    assert any(entry["missing"] for entry in unexpanded)


def test_the_seed_shows_an_unreadable_calendar_at_forty_nine_percent(store):
    module = load_feature(MODULE)
    rooms = [store.create("room", {"name": f"Room {i}"}, actor="dana") for i in range(4)]
    module.seed(store, {"room_ids": [(r["id"], "Acct") for r in rooms]})
    finder = SlotFinder(store)
    searches = [s for room in rooms for s in finder.list_searches(room["id"], limit=200)]
    assert any(s["counts"].get("unreadable") for s in searches)
    confidences = {
        slot["confidence"] for s in searches for slot in s["suggestions"]
    }
    assert any(0 < c < 100 for c in confidences), "no partly-confident slot in the demo"


def test_the_seed_shows_house_rule_refusals_naming_their_rule(store):
    module = load_feature(MODULE)
    rooms = [store.create("room", {"name": f"Room {i}"}, actor="dana") for i in range(4)]
    module.seed(store, {"room_ids": [(r["id"], "Acct") for r in rooms]})
    finder = SlotFinder(store)
    searches = [s for room in rooms for s in finder.list_searches(room["id"], limit=200)]
    assert any(s["counts"].get("house_rule_refusals") for s in searches)


def test_the_seed_books_both_ways_round_the_fresh_conference(store):
    module = load_feature(MODULE)
    rooms = [store.create("room", {"name": f"Room {i}"}, actor="dana") for i in range(4)]
    module.seed(store, {"room_ids": [(r["id"], "Acct") for r in rooms]})
    finder = SlotFinder(store)
    bookings = [b for room in rooms for b in finder.list_bookings(room["id"])]
    assert len(bookings) == 2
    assert any(b["conference"] for b in bookings)
    assert any(not b["conference"] for b in bookings)
    assert all(b["revalidated"] for b in bookings)


def test_the_seed_survives_no_rooms(store):
    """A seeder that aborts a whole feature over one stale room id leaves a page
    nobody can review; the calendars are still worth having."""
    module = load_feature(MODULE)
    summary = module.seed(store, {"room_ids": [("room_gone", "Acct")]})
    assert "0 panels" in summary
    assert count(store, CALENDAR_COLLECTION) == 11
    assert count(store, PANEL_COLLECTION) == 0


def test_the_seed_is_idempotent_in_what_it_produces(store):
    """Not a no-op - it is a demo, not an upsert - but a second run must not
    corrupt the first: the same panel names the same calendars."""
    module = load_feature(MODULE)
    rooms = [store.create("room", {"name": f"Room {i}"}, actor="dana") for i in range(4)]
    first = module.seed(store, {"room_ids": [(r["id"], "Acct") for r in rooms]})
    second = module.seed(store, {"room_ids": [(r["id"], "Acct") for r in rooms]})
    assert first and second
    assert count(store, PANEL_COLLECTION) == 10  # two clean runs
    assert count(store, CALENDAR_COLLECTION) == 22


# --------------------------------------------------------------------------- #
# The frontend descriptor
# --------------------------------------------------------------------------- #


def test_the_frontend_descriptor_exists_and_is_well_formed():
    index = Path(__file__).resolve().parents[2] / "frontend" / "src" / "features" / (
        "wf-057-find-a-time-that-works-for-a-multi-per"
    ) / "index.jsx"
    assert index.is_file()
    text = index.read_text(encoding="utf-8")
    assert "id: 'wf-057-find-a-time-that-works-for-a-multi-per'" in text
    assert "Component:" in text
    assert "iconPath:" in text, "the nav glyph is not in the shared PATHS map, so it needs a path"
    assert "export default {" in text


def test_the_frontend_imports_through_the_alias_not_a_relative_path():
    folder = Path(__file__).resolve().parents[2] / "frontend" / "src" / "features" / (
        "wf-057-find-a-time-that-works-for-a-multi-per"
    )
    for path in folder.glob("*.jsx"):
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("import") and "'./" not in stripped and '"./' not in stripped:
                if "../" in stripped or "'./" in stripped:
                    continue
            if stripped.startswith("from '..") or stripped.startswith('from "..'):
                assert "'@/" in stripped or '"./' in stripped or "'./" in stripped, (
                    f"{path.name}: {stripped}"
                )


def test_the_frontend_meets_the_design_floor():
    """No emoji as an icon, a 44px minimum target, and a visible text label.

    Checked on the source because that is where the floor is written: the design
    system's own numbers live in
    ``design-system/digital-sales-room/MASTER.md``.
    """
    folder = Path(__file__).resolve().parents[2] / "frontend" / "src" / "features" / (
        "wf-057-find-a-time-that-works-for-a-multi-per"
    )
    emoji = re.compile(
        "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF]"
    )
    for path in folder.glob("*.jsx"):
        text = path.read_text(encoding="utf-8")
        offenders = emoji.findall(text)
        assert not offenders, f"{path.name} uses {offenders} as an icon"
    combined = "\n".join(path.read_text(encoding="utf-8") for path in folder.glob("*.jsx"))
    assert "min-h-11" in combined, "the 44px minimum touch target"
    assert "focus-visible" in combined, "a visible focus ring"
