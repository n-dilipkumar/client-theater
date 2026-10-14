"""WF-136's rules, without a request in sight.

What this file pins is the workflow's own behaviour: the states a next step moves
through, what a refusal leaves behind, and the two automations the research names
(idle auto-propose, record-and-attach). The slot grid, the booking, the join link
and the MAP task belong to WF-058, WF-059 and WF-124, so those are exercised here
only as far as this workflow *delegates* to them - and the delegation is asserted,
because a re-implemented slot rule is the failure this build is written to avoid.

The clock is frozen everywhere. A test that depended on a real weekday, or on a
date that will eventually pass, is a time bomb, so every clock here is the same
fixed instant and every expiry is expressed relative to it.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from dsr.conference_links.engine import ProvisioningEngine
from dsr.conference_links.errors import ConferenceLinkError
from dsr.inroom_booking import (
    IDLE_MINUTES,
    AttendeeRequired,
    InRoomBookingEngine,
    InRoomBookingError,
    MeetingNotFound,
    NextStepTaskNotFound,
    ProposalAnswered,
    ProposalExpired,
    ProposalInvalid,
    ProposalNotFound,
    RecordingNotAttached,
    RoomNotBookable,
    SlotNotOffered,
    inferences as booking_inferences,
    vocabulary as booking_vocabulary,
)
from dsr.inroom_scheduling import SchedulingEngine
from dsr.mutual_action import plan as mutual
from dsr.store import RecordStore

NOW = datetime(2026, 10, 14, 9, 0, tzinfo=timezone.utc)
EVENT_TYPE = "evt_wf136_rule_test"
SOURCE = "POST /api/wf-136/rooms/{room_id}/proposals"
ATTENDEE = {"name": "Ines Duarte", "email": "ines@northwind.example"}


def frozen(now: datetime = NOW):
    return lambda: now


def build_engine(store: RecordStore, now: datetime = NOW, **overrides) -> InRoomBookingEngine:
    borrowed: dict[str, object] = {
        "scheduling": SchedulingEngine(store, clock=frozen(now)),
        "conferencing": ProvisioningEngine(store, now=frozen(now)),
        "plans": mutual.PlanEngine(store, now=frozen(now)),
    }
    borrowed.update(overrides)
    return InRoomBookingEngine(store, now=frozen(now), **borrowed)


def add_bookable_room(store: RecordStore, now: datetime = NOW) -> str:
    """A room WF-058 would let a prospect book, built by running its real engine.

    No fixture pokes rows in directly: the client, the grant, the event type and
    the embed all come through WF-058's own rules, so a change to those rules
    breaks this setup loudly rather than leaving a stale hand-built row behind.
    """
    scheduling = SchedulingEngine(store, clock=frozen(now))
    room = store.create("room", {"name": "Northwind"}, actor="test", source="test")
    client = scheduling.create_client(
        {
            "name": "Rule test",
            "client_id": "dsr-wf136-rules",
            "redirect_uri": "https://rooms.example/wf-136/rules",
            "scopes": ["BOOKING", "EVENT_TYPE"],
        },
        actor="test",
        source="test",
    )
    scheduling.grant(client["id"], {"subject": "priya"}, actor="test", source="test")
    scheduling.create_event_type(
        {
            "eventTypeId": EVENT_TYPE,
            "slug": "rules",
            "kind": "personal",
            "host": "priya",
            "length_minutes": 30,
            "slot_interval_minutes": 30,
            "minimum_notice_minutes": 0,
            "time_zone": "UTC",
            "days": [1, 2, 3, 4, 5, 6, 7],
            "working_hours_start": "08:00",
            "working_hours_end": "18:00",
            "location": "google_meet",
        },
        actor="test",
        source="test",
    )
    scheduling.save_embed(
        room["id"],
        {
            "clientId": client["id"],
            "eventTypeId": EVENT_TYPE,
            "time_zone": "UTC",
            "horizon_days": 14,
            "require_hold": False,
        },
        actor="test",
        source="test",
    )
    if ProvisioningEngine(store, now=frozen(now)).default_location() is None:
        ProvisioningEngine(store, now=frozen(now)).create_location(
            {
                "kind": "conference-details",
                "name": "Meeting room",
                "conference_details": "https://meet.example/rule-test",
                "is_default": True,
            },
            actor="test",
            source="test",
        )
    return room["id"]


def add_plan(store: RecordStore, room_id: str, titles=("Deep dive", "Security review")) -> dict:
    plans = mutual.PlanEngine(store, now=frozen())
    return plans.create_plan(
        room_id,
        {
            "name": "Rule test plan",
            "tasks": [
                {"title": title, "owner": "Dana", "owner_side": "seller"} for title in titles
            ],
        },
        actor="test",
        source="test",
    )


def first_slot(store: RecordStore, room_id: str) -> str:
    grid = SchedulingEngine(store, clock=frozen()).slots(room_id, {"eventTypeId": EVENT_TYPE})
    return next(row["start"] for row in grid["slots"] if row["available"])


def count(store: RecordStore, collection: str, room_id: str | None = None) -> int:
    return len(store.list(collection, room_id=room_id, limit=500))


# --------------------------------------------------------------------------- #
# The published vocabulary and inferences
# --------------------------------------------------------------------------- #


def test_the_vocabulary_publishes_the_five_proposal_states():
    published = booking_vocabulary.published_vocabulary()
    assert published["proposal_states"] == list(booking_vocabulary.PROPOSAL_STATES)
    assert published["answerable_proposal_states"] == ["proposed"]
    assert published["completed_proposal_states"] == ["booked"]
    assert published["meeting_states"] == list(booking_vocabulary.MEETING_STATES)


def test_an_unknown_proposal_state_is_refused_by_name():
    with pytest.raises(InRoomBookingError):
        booking_vocabulary.require_proposal_state("teleported")


def test_the_unresolved_inference_is_published_rather_than_hidden():
    described = booking_inferences.describe()
    assert described["unresolved"] == ["idle_minutes"]
    assert described["unresolved_count"] == 1
    # The cleared one carries the audit id a reviewer can look up.
    cleared = {entry["name"]: entry for entry in described["inferences"]}
    assert cleared["accept_is_a_commit"]["status"] == "cleared"
    assert cleared["accept_is_a_commit"]["audit_id"].startswith("jev-")


# --------------------------------------------------------------------------- #
# Creating a proposal
# --------------------------------------------------------------------------- #


def test_a_room_with_no_embed_cannot_be_proposed_in(store: RecordStore):
    room = store.create("room", {"name": "Bare"}, actor="test", source="test")
    engine = build_engine(store)
    with pytest.raises(RoomNotBookable) as refused:
        engine.create_proposal(room["id"], {"summary": "Talk"}, source=SOURCE)
    assert refused.value.reason == "room_has_no_embed"


def test_a_proposal_without_a_summary_is_refused_with_a_field_key(store: RecordStore):
    room_id = add_bookable_room(store)
    engine = build_engine(store)
    with pytest.raises(ProposalInvalid) as refused:
        engine.create_proposal(room_id, {"details": "no summary"}, source=SOURCE)
    assert "summary" in refused.value.errors
    assert count(store, "inroom_booking_proposal", room_id) == 0


def test_a_proposal_for_a_task_the_plan_does_not_hold_is_refused(store: RecordStore):
    room_id = add_bookable_room(store)
    engine = build_engine(store)
    with pytest.raises(NextStepTaskNotFound):
        engine.create_proposal(
            room_id, {"summary": "Talk", "task_id": "wf124_task_missing"}, source=SOURCE
        )
    assert count(store, "inroom_booking_proposal", room_id) == 0


def test_a_created_proposal_is_answerable_and_carries_an_expiry(store: RecordStore):
    room_id = add_bookable_room(store)
    engine = build_engine(store)
    proposal = engine.create_proposal(room_id, {"summary": "Talk"}, actor="dana", source=SOURCE)
    assert proposal["state"] == "proposed"
    assert proposal["effective_state"] == "proposed"
    assert proposal["answerable"] is True
    expires = datetime.fromisoformat(proposal["expires_at"])
    assert expires > NOW


def test_a_proposal_lapses_on_its_own_clock_not_an_elapsed_age(store: RecordStore):
    """The expiry is authority, and it is exercised with a frozen clock.

    Reading at the same instant sees ``proposed``; reading one second past the
    stored expiry sees ``expired``. Nothing here depends on a real date.
    """
    room_id = add_bookable_room(store)
    engine = build_engine(store)
    proposal = engine.create_proposal(
        room_id,
        {"summary": "Talk", "expires_at": (NOW + timedelta(hours=1)).isoformat()},
        source=SOURCE,
    )
    assert engine.read_proposal(proposal["id"])["effective_state"] == "proposed"
    later = build_engine(store, now=NOW + timedelta(hours=2))
    assert later.read_proposal(proposal["id"])["effective_state"] == "expired"


def test_expire_lapsed_persists_the_transition(store: RecordStore):
    room_id = add_bookable_room(store)
    engine = build_engine(store)
    proposal = engine.create_proposal(
        room_id,
        {"summary": "Talk", "expires_at": (NOW - timedelta(minutes=1)).isoformat()},
        source=SOURCE,
    )
    outcome = engine.expire_lapsed(room_id, source="POST /api/wf-136/rooms/{room_id}/expire-lapsed")
    assert outcome["expired"] == [proposal["id"]]
    assert engine.read_proposal(proposal["id"])["state"] == "expired"


# --------------------------------------------------------------------------- #
# Reading the availability
# --------------------------------------------------------------------------- #


def test_the_slot_grid_is_delegated_and_saves_nothing(store: RecordStore):
    room_id = add_bookable_room(store)
    engine = build_engine(store)
    proposal = engine.create_proposal(room_id, {"summary": "Talk"}, source=SOURCE)
    before = count(store, "scheduling_booking", room_id)
    grid = engine.slots(proposal["id"])
    assert grid["proposal_id"] == proposal["id"]
    assert grid["available_count"] >= 1
    assert count(store, "scheduling_booking", room_id) == before


# --------------------------------------------------------------------------- #
# Accepting: the whole integration, end to end
# --------------------------------------------------------------------------- #


def test_accept_books_the_slot_and_integrates_the_room(store: RecordStore):
    room_id = add_bookable_room(store)
    plan = add_plan(store, room_id)
    task_id = plan["tasks"][0]["id"]
    engine = build_engine(store)
    proposal = engine.create_proposal(
        room_id, {"summary": "Book the deep dive", "task_id": task_id}, actor="dana", source=SOURCE
    )
    outcome = engine.accept(
        proposal["id"],
        {"start": first_slot(store, room_id), "attendee": ATTENDEE},
        actor="ines",
        source="POST /api/wf-136/proposals/{proposal_id}/accept",
    )

    assert outcome["proposal"]["state"] == "booked"
    assert outcome["meeting"]["state"] == "linked"
    assert outcome["meeting"]["conference_url"] == "https://meet.example/rule-test"
    # The MAP task flipped, through WF-124's own engine.
    assert outcome["meeting"]["map_task_state"] == "done"
    tasks = mutual.PlanEngine(store, now=frozen()).tasks_of(plan["id"])
    assert {t["id"]: t["status"] for t in tasks}[task_id] == "done"
    # A timeline row the buyer can see, and the room's convenience copy.
    timeline = store.list("timeline", room_id=room_id, limit=20)
    assert any("Next step booked" in (row["data"] or {}).get("summary", "") for row in timeline)
    room = store.get(room_id)
    assert room["data"]["inroom_booking"]["next_step"]["proposal_id"] == proposal["id"]


def test_a_second_accept_is_refused_rather_than_booking_twice(store: RecordStore):
    room_id = add_bookable_room(store)
    engine = build_engine(store)
    proposal = engine.create_proposal(room_id, {"summary": "Talk"}, source=SOURCE)
    start = first_slot(store, room_id)
    engine.accept(
        proposal["id"],
        {"start": start, "attendee": ATTENDEE},
        source="POST /api/wf-136/proposals/{proposal_id}/accept",
    )
    with pytest.raises(ProposalAnswered):
        engine.accept(
            proposal["id"],
            {"start": start, "attendee": ATTENDEE},
            source="POST /api/wf-136/proposals/{proposal_id}/accept",
        )
    assert count(store, "inroom_booking_meeting", room_id) == 1


def test_accepting_an_expired_proposal_writes_nothing(store: RecordStore):
    room_id = add_bookable_room(store)
    engine = build_engine(store)
    proposal = engine.create_proposal(room_id, {"summary": "Talk"}, source=SOURCE)
    later = build_engine(store, now=NOW + timedelta(days=8))
    with pytest.raises(ProposalExpired):
        later.accept(
            proposal["id"],
            {"start": first_slot(store, room_id), "attendee": ATTENDEE},
            source="POST /api/wf-136/proposals/{proposal_id}/accept",
        )
    assert count(store, "inroom_booking_meeting", room_id) == 0


def test_a_slot_that_is_not_offered_is_refused_rather_than_substituted(store: RecordStore):
    room_id = add_bookable_room(store)
    engine = build_engine(store)
    proposal = engine.create_proposal(room_id, {"summary": "Talk"}, source=SOURCE)
    with pytest.raises(SlotNotOffered):
        engine.accept(
            proposal["id"],
            {"start": "2027-01-01T03:00:00+00:00", "attendee": ATTENDEE},
            source="POST /api/wf-136/proposals/{proposal_id}/accept",
        )
    assert count(store, "inroom_booking_meeting", room_id) == 0


def test_accept_without_an_attendee_is_refused_and_books_nothing(store: RecordStore):
    room_id = add_bookable_room(store)
    engine = build_engine(store)
    proposal = engine.create_proposal(room_id, {"summary": "Talk"}, source=SOURCE)
    with pytest.raises(AttendeeRequired):
        engine.accept(
            proposal["id"],
            {"start": first_slot(store, room_id)},
            source="POST /api/wf-136/proposals/{proposal_id}/accept",
        )
    assert count(store, "inroom_booking_meeting", room_id) == 0


def test_a_failed_conference_link_does_not_fail_the_meeting(store: RecordStore):
    """WF-059's rule, adopted: a booking with no conference is still a booking."""

    class RefusingConferencing:
        def book(self, *args, **kwargs):
            raise ConferenceLinkError("the provider is down")

    room_id = add_bookable_room(store)
    engine = build_engine(store, conferencing=RefusingConferencing())
    proposal = engine.create_proposal(room_id, {"summary": "Talk"}, source=SOURCE)
    outcome = engine.accept(
        proposal["id"],
        {"start": first_slot(store, room_id), "attendee": ATTENDEE},
        source="POST /api/wf-136/proposals/{proposal_id}/accept",
    )
    assert outcome["meeting"]["state"] == "unlinked"
    assert outcome["meeting"]["conference_url"] is None
    assert outcome["proposal"]["state"] == "booked"


# --------------------------------------------------------------------------- #
# Declining
# --------------------------------------------------------------------------- #


def test_decline_closes_the_proposal_and_a_second_decline_is_refused(store: RecordStore):
    room_id = add_bookable_room(store)
    engine = build_engine(store)
    proposal = engine.create_proposal(room_id, {"summary": "Talk"}, source=SOURCE)
    declined = engine.decline(
        proposal["id"],
        {"reason": "not now"},
        source="POST /api/wf-136/proposals/{proposal_id}/decline",
    )
    assert declined["state"] == "declined"
    assert declined["decline_reason"] == "not now"
    with pytest.raises(ProposalAnswered):
        engine.decline(proposal["id"], source="POST /api/wf-136/proposals/{proposal_id}/decline")


# --------------------------------------------------------------------------- #
# The idle automation
# --------------------------------------------------------------------------- #


def test_the_automation_proposes_when_the_room_has_gone_quiet(store: RecordStore):
    room_id = add_bookable_room(store)
    # The last thing that happened in the room was long enough ago to count as
    # idle. An explicit old row rather than a fresh room, so the test does not
    # depend on how a room's creation time compares with the frozen clock.
    store.create(
        "activity",
        {
            "action": "viewed",
            "occurred_at": (NOW - timedelta(minutes=IDLE_MINUTES + 5)).isoformat(),
        },
        room_id=room_id,
        actor="system",
        source="test",
    )
    engine = build_engine(store)
    outcome = engine.auto_propose(room_id, source="POST /api/wf-136/rooms/{room_id}/auto-propose")
    assert outcome["proposed"] is True
    assert outcome["reason"] == "room_idle"
    assert outcome["idle_minutes"] == IDLE_MINUTES


def test_the_automation_stays_silent_while_the_room_is_active(store: RecordStore):
    room_id = add_bookable_room(store)
    store.create(
        "activity",
        {"action": "viewed", "occurred_at": (NOW - timedelta(minutes=2)).isoformat()},
        room_id=room_id,
        actor="system",
        source="test",
    )
    engine = build_engine(store)
    outcome = engine.auto_propose(room_id, source="POST /api/wf-136/rooms/{room_id}/auto-propose")
    assert outcome["proposed"] is False
    assert outcome["reason"] == "room_active"


def test_the_automation_will_not_propose_a_second_next_step(store: RecordStore):
    room_id = add_bookable_room(store)
    engine = build_engine(store)
    engine.create_proposal(room_id, {"summary": "Already waiting"}, source=SOURCE)
    outcome = engine.auto_propose(room_id, source="POST /api/wf-136/rooms/{room_id}/auto-propose")
    assert outcome["proposed"] is False
    assert outcome["reason"] == "already_proposed"


# --------------------------------------------------------------------------- #
# The next-steps panel
# --------------------------------------------------------------------------- #


def test_the_panel_merges_live_proposals_with_open_map_tasks(store: RecordStore):
    room_id = add_bookable_room(store)
    plan = add_plan(store, room_id, titles=("Deep dive", "Security review"))
    engine = build_engine(store)
    engine.create_proposal(room_id, {"summary": "Book the deep dive"}, source=SOURCE)

    # Completing one task through WF-124's own route must remove it from the panel.
    mutual.PlanEngine(store, now=frozen()).update_task(
        plan["tasks"][0]["id"], {"status": "done"}, source="test"
    )
    panel = engine.next_steps(room_id)
    assert [p["summary"] for p in panel["proposals"]] == ["Book the deep dive"]
    assert [t["title"] for t in panel["map_tasks"]] == ["Security review"]


# --------------------------------------------------------------------------- #
# Attaching a recording
# --------------------------------------------------------------------------- #


def test_a_recording_is_attached_by_url_and_lands_on_the_timeline(store: RecordStore):
    room_id = add_bookable_room(store)
    engine = build_engine(store)
    proposal = engine.create_proposal(room_id, {"summary": "Talk"}, source=SOURCE)
    outcome = engine.accept(
        proposal["id"],
        {"start": first_slot(store, room_id), "attendee": ATTENDEE},
        source="POST /api/wf-136/proposals/{proposal_id}/accept",
    )
    meeting_id = outcome["meeting"]["id"]
    updated = engine.attach_recording(
        meeting_id,
        {"url": "https://record.example/one"},
        source="POST /api/wf-136/meetings/{meeting_id}/recording",
    )
    assert updated["recording_url"] == "https://record.example/one"
    timeline = store.list("timeline", room_id=room_id, limit=20)
    assert any((row["data"] or {}).get("kind") == "recording_attached" for row in timeline)


def test_a_recording_that_is_not_a_url_is_refused(store: RecordStore):
    room_id = add_bookable_room(store)
    engine = build_engine(store)
    proposal = engine.create_proposal(room_id, {"summary": "Talk"}, source=SOURCE)
    outcome = engine.accept(
        proposal["id"],
        {"start": first_slot(store, room_id), "attendee": ATTENDEE},
        source="POST /api/wf-136/proposals/{proposal_id}/accept",
    )
    with pytest.raises(RecordingNotAttached):
        engine.attach_recording(
            outcome["meeting"]["id"],
            {"url": "not-a-url"},
            source="POST /api/wf-136/meetings/{meeting_id}/recording",
        )


# --------------------------------------------------------------------------- #
# Reads that miss
# --------------------------------------------------------------------------- #


def test_reading_a_proposal_and_a_meeting_that_do_not_exist_is_not_found(store: RecordStore):
    engine = build_engine(store)
    with pytest.raises(ProposalNotFound):
        engine.read_proposal("inroom_booking_proposal_missing")
    with pytest.raises(MeetingNotFound):
        engine.read_meeting("inroom_booking_meeting_missing")
