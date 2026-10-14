"""WF-128: the live-session rules, exercised as a domain module with no server.

The rules in ``dsr.live_sessions`` are pure over the
:class:`~dsr.store.RecordStore` and touch nothing else, so every rule below is
reachable without a ``TestClient``. These tests are what make each of the
issue's open decisions checkable at the instant it is stated about.

What is under test, and why each one matters
--------------------------------------------

* **A session's agenda is the room's own pages.** The user flow is "the deck or
  the actual product", so the steps a presenter reveals are the pages the room
  already has. A room with no pages gets one screen step, so start cannot fail
  on an empty agenda.
* **The reveal order is respected.** The presenter moves one step forward and
  may step back, but a forward jump is refused, because revealing a step before
  the one before it is exactly what the workflow exists to prevent.
* **Only the presenter drives.** A follower cannot move the cursor. The refusal
  is a stable token, not a prose sentence a page has to parse.
* **One presenter per session.** A second is refused so "the presenter" never
  becomes ambiguous.
* **A follower polls.** No realtime transport exists, so sync is recorded state
  and a participant joining is a write that proves they were following.
* **Auto-record and auto-publish.** Starting creates a recording; ending
  publishes it with a stand-in reference and URL, and the vocabulary says the
  artefact is not hosted.
* **Attendance is join-based.** A participant who joined counts as attended and
  the row records the seconds present.
* **Questions are logged against the deal.** A question carries the room it was
  asked in, so "logged against the deal" is the room record.
* **Every record is plain JSON in ``data``.** This module adds no migration and
  no typed column.

Every test names the property it protects, because a test named ``test_flow``
tells a reviewer nothing about which rule they can stop worrying about.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from dsr.features import load_feature
from dsr.live_sessions import rules, vocabulary as v
from dsr.live_sessions.engine import LiveSessionBook
from dsr.live_sessions.errors import SessionConflict, SessionNotFound, SessionValidationError
from dsr.store import RecordStore

MODULE = "wf128_live_co_presented_walkthrough_inside_the_room"

NOW = datetime(2026, 5, 4, 9, 0, tzinfo=timezone.utc)
LATER = datetime(2026, 5, 4, 9, 30, tzinfo=timezone.utc)


@pytest.fixture()
def book(store: RecordStore) -> LiveSessionBook:
    """The book, with the clock pinned so every timestamp is exact."""

    return LiveSessionBook(store, now=lambda: NOW)


@pytest.fixture()
def room(store: RecordStore) -> str:
    return store.create("room", {"name": "Northwind"}, actor="t", source="t")["id"]


def _page(store: RecordStore, room_id: str, title: str, position: int = 0) -> str:
    return store.create(
        "page",
        {"title": title, "position": position},
        room_id=room_id,
        actor="t",
        source="t",
    )["id"]


def _buyer(store: RecordStore, room_id: str, email: str, role: str = "viewer") -> str:
    return store.create(
        "room_access",
        {"principal": email, "role": role},
        room_id=room_id,
        actor="t",
        source="t",
    )["id"]


def _room_with_pages(store: RecordStore, count: int = 3) -> str:
    room_id = store.create("room", {"name": "Northwind"}, actor="t", source="t")["id"]
    for index in range(count):
        _page(store, room_id, f"Page {index + 1}", index)
    return room_id


def _session(book: LiveSessionBook, room_id: str, **extra: object) -> dict:
    return book.create_session(
        {"room_id": room_id, "presenter": "rep@northwind.example", **extra},
        source="test",
        actor="dana",
    )


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #


def test_a_session_without_a_room_is_refused():
    with pytest.raises(SessionValidationError) as caught:
        rules.validate_session({"title": "Live"})
    assert "room_id" in caught.value.errors


def test_a_participant_without_an_email_is_refused():
    with pytest.raises(SessionValidationError) as caught:
        rules.validate_invite({"role": v.ROLE_FOLLOWER})
    assert "email" in caught.value.errors


def test_an_unknown_role_is_refused():
    with pytest.raises(SessionValidationError) as caught:
        rules.validate_invite({"email": "a@x.example", "role": "chair"})
    assert "role" in caught.value.errors


def test_a_question_without_text_is_refused():
    with pytest.raises(SessionValidationError) as caught:
        rules.validate_question({"asker": "a@x.example"})
    assert "text" in caught.value.errors


def test_a_move_without_a_position_is_refused():
    with pytest.raises(SessionValidationError) as caught:
        rules.validate_move({})
    assert "position" in caught.value.errors


def test_the_two_roles_are_the_only_ones():
    assert v.PARTICIPANT_ROLES == (v.ROLE_PRESENTER, v.ROLE_FOLLOWER)


# --------------------------------------------------------------------------- #
# The agenda
# --------------------------------------------------------------------------- #


def test_the_agenda_is_the_rooms_pages_in_order(store: RecordStore, book: LiveSessionBook):
    room_id = _room_with_pages(store, count=3)
    session = _session(book, room_id)
    assert session["step_count"] == 3
    assert [step["label"] for step in session["agenda"]] == ["Page 1", "Page 2", "Page 3"]
    assert all(step["kind"] == v.STEP_PAGE for step in session["agenda"])


def test_a_room_with_no_pages_gets_one_screen_step(store: RecordStore, book: LiveSessionBook):
    room_id = _room_with_pages(store, count=0)
    session = _session(book, room_id)
    assert session["step_count"] == 1
    assert session["agenda"][0]["kind"] == v.STEP_SCREEN


def test_plan_agenda_is_pure_and_reads_pages_only():
    pages = [{"id": "p1", "data": {"title": "First"}}, {"id": "p2", "data": {}}]
    steps = rules.plan_agenda(pages)
    assert [step["ref"] for step in steps] == ["p1", "p2"]
    assert steps[1]["label"] == "Room page"


# --------------------------------------------------------------------------- #
# The reveal order: the pure decision
# --------------------------------------------------------------------------- #


def test_the_presenter_moves_forward_by_one_step():
    ok, reason = rules.reveal_decision(role=v.ROLE_PRESENTER, current=0, target=1, last=2)
    assert ok is True
    assert reason is None


def test_a_forward_jump_is_refused_as_out_of_order():
    ok, reason = rules.reveal_decision(role=v.ROLE_PRESENTER, current=0, target=2, last=2)
    assert ok is False
    assert reason == v.REASON_SKIPPED_REVEAL


def test_the_presenter_may_step_back_to_any_earlier_step():
    ok, reason = rules.reveal_decision(role=v.ROLE_PRESENTER, current=2, target=0, last=2)
    assert ok is True
    assert reason is None


def test_a_position_off_the_agenda_is_out_of_range():
    ok, reason = rules.reveal_decision(role=v.ROLE_PRESENTER, current=0, target=9, last=2)
    assert ok is False
    assert reason == v.REASON_OUT_OF_RANGE


def test_a_follower_can_never_move_the_cursor():
    ok, reason = rules.reveal_decision(role=v.ROLE_FOLLOWER, current=0, target=1, last=2)
    assert ok is False
    assert reason == v.REASON_NOT_PRESENTER
    assert rules.follower_can_control() == (False, v.REASON_FOLLOWER_CANNOT_CONTROL)


# --------------------------------------------------------------------------- #
# The reveal order: over the engine
# --------------------------------------------------------------------------- #


def test_reveal_over_the_engine_respects_the_order(store: RecordStore, book: LiveSessionBook):
    room_id = _room_with_pages(store, count=3)
    session = _session(book, room_id)
    book.start_session(session["id"], source="test")

    with pytest.raises(SessionConflict) as jumped:
        # Skip step 2 and reveal step 3: the reveal order is broken.
        book.reveal(session["id"], {"position": 2}, source="test")
    assert jumped.value.reason == v.REASON_SKIPPED_REVEAL

    first = book.reveal(session["id"], {"position": 1}, source="test")
    assert first["position"] == 1
    assert first["direction"] == "forward"

    with pytest.raises(SessionConflict) as ranged:
        book.reveal(session["id"], {"position": 3}, source="test")
    assert ranged.value.reason == v.REASON_OUT_OF_RANGE

    back = book.reveal(session["id"], {"position": 0}, source="test")
    assert back["direction"] == "back"
    assert back["revealed"] == 1  # the high-water mark never goes back


def test_a_follower_driving_the_session_is_a_conflict(store: RecordStore, book: LiveSessionBook):
    room_id = _room_with_pages(store, count=3)
    session = _session(book, room_id, invitees=["a.buyer@northwind.example"])
    book.start_session(session["id"], source="test")

    with pytest.raises(SessionConflict) as caught:
        book.reveal(
            session["id"], {"position": 1, "as": "a.buyer@northwind.example"}, source="test"
        )
    assert caught.value.reason == v.REASON_NOT_PRESENTER


def test_a_move_before_the_session_is_live_is_refused(store: RecordStore, book: LiveSessionBook):
    room_id = _room_with_pages(store, count=3)
    session = _session(book, room_id)
    with pytest.raises(SessionConflict) as caught:
        book.reveal(session["id"], {"position": 1}, source="test")
    assert caught.value.reason == v.REASON_SESSION_NOT_LIVE


def test_an_unknown_actor_is_not_found(store: RecordStore, book: LiveSessionBook):
    room_id = _room_with_pages(store, count=3)
    session = _session(book, room_id)
    book.start_session(session["id"], source="test")
    with pytest.raises(SessionNotFound):
        book.reveal(session["id"], {"position": 1, "as": "stranger@x.example"}, source="test")


# --------------------------------------------------------------------------- #
# Participants and the per-participant link
# --------------------------------------------------------------------------- #


def test_each_participant_gets_their_own_link(store: RecordStore, book: LiveSessionBook):
    room_id = _room_with_pages(store, count=2)
    session = _session(book, room_id, invitees=["a@x.example", "b@x.example"])
    tokens = {row["join_token"] for row in book.participants(session_id=session["id"])}
    assert len(tokens) == 3  # presenter plus two followers
    assert None not in tokens
    assert all(row["join_url"].startswith(v.JOIN_PATH) for row in session["participants"])


def test_a_second_presenter_is_refused(store: RecordStore, book: LiveSessionBook):
    room_id = _room_with_pages(store, count=2)
    session = _session(book, room_id)
    with pytest.raises(SessionConflict) as caught:
        book.invite(
            session["id"], {"email": "other@x.example", "role": v.ROLE_PRESENTER}, source="test"
        )
    assert caught.value.reason == v.REASON_SECOND_PRESENTER


def test_invitees_default_to_the_rooms_recipients(store: RecordStore, book: LiveSessionBook):
    room_id = _room_with_pages(store, count=2)
    _buyer(store, room_id, "a.buyer@x.example")
    _buyer(store, room_id, "b.buyer@x.example")
    session = _session(book, room_id)
    emails = {row["email"] for row in book.participants(session_id=session["id"])}
    assert {"a.buyer@x.example", "b.buyer@x.example"} <= emails


def test_an_unconfigured_room_invites_only_the_presenter(store: RecordStore, book: LiveSessionBook):
    room_id = _room_with_pages(store, count=2)
    session = _session(book, room_id)
    assert len(session["participants"]) == 1
    assert session["participants"][0]["role"] == v.ROLE_PRESENTER


# --------------------------------------------------------------------------- #
# Start, sync and end
# --------------------------------------------------------------------------- #


def test_starting_makes_the_session_live_and_begins_recording(
    store: RecordStore, book: LiveSessionBook
):
    room_id = _room_with_pages(store, count=2)
    session = _session(book, room_id, invitees=["a@x.example"])
    started = book.start_session(session["id"], source="test")
    assert started["state"] == v.STATE_LIVE
    assert started["recording"]["status"] == v.RECORDING_RECORDING
    assert started["streamed"] is False
    assert started["transport"] == v.TRANSPORT_STAND_IN


def test_starting_twice_is_refused(store: RecordStore, book: LiveSessionBook):
    room_id = _room_with_pages(store, count=2)
    session = _session(book, room_id)
    book.start_session(session["id"], source="test")
    with pytest.raises(SessionConflict) as caught:
        book.start_session(session["id"], source="test")
    assert caught.value.reason == v.REASON_ALREADY_STARTED


def test_starting_notifies_every_invitee(store: RecordStore, book: LiveSessionBook):
    room_id = _room_with_pages(store, count=2)
    session = _session(book, room_id, invitees=["a@x.example", "b@x.example"])
    started = book.start_session(session["id"], source="test")
    recipients = {row["recipient"] for row in started["notified"]}
    assert recipients == {"rep@northwind.example", "a@x.example", "b@x.example"}


def test_a_follower_syncs_and_becomes_attendee(store: RecordStore, book: LiveSessionBook):
    room_id = _room_with_pages(store, count=3)
    session = _session(book, room_id, invitees=["a@x.example"])
    book.start_session(session["id"], source="test")
    book.reveal(session["id"], {"position": 1}, source="test")

    synced = book.sync(session["id"], {"as": "a@x.example"}, source="test")
    assert synced["participant"]["status"] == v.PARTICIPANT_JOINED
    assert synced["following"] == 1

    attendance = book.attendance(session_id=session["id"])
    follower = [row for row in attendance if row["email"] == "a@x.example"][0]
    assert follower["attended"] is True
    assert follower["written_to"] == v.DESTINATION_ROOM


def test_sync_from_an_unknown_participant_is_not_found(store: RecordStore, book: LiveSessionBook):
    room_id = _room_with_pages(store, count=2)
    session = _session(book, room_id)
    book.start_session(session["id"], source="test")
    with pytest.raises(SessionNotFound):
        book.sync(session["id"], {"as": "ghost@x.example"}, source="test")


def test_ending_publishes_a_replay_and_closes_attendance(store: RecordStore, book: LiveSessionBook):
    room_id = _room_with_pages(store, count=3)
    session = _session(book, room_id, invitees=["a@x.example", "b@x.example"])
    book.start_session(session["id"], source="test")
    book.sync(session["id"], {"as": "a@x.example"}, source="test")

    ended = book.end_session(session["id"], source="test")
    assert ended["state"] == v.STATE_ENDED
    assert ended["recording"]["status"] == v.RECORDING_PUBLISHED
    assert ended["recording"]["hosted"] is False
    assert ended["recording"]["playback_url"].startswith(v.REPLAY_HOST)

    rows = {row["email"]: row for row in book.attendance(session_id=session["id"])}
    assert rows["a@x.example"]["state"] == v.ATTENDANCE_ATTENDED
    assert rows["b@x.example"]["state"] == v.ATTENDANCE_INVITED_ONLY
    assert all(row["left_at_ms"] == rules.parse_timestamp(NOW, "now") for row in rows.values())


def test_ending_a_session_that_is_not_live_is_refused(store: RecordStore, book: LiveSessionBook):
    room_id = _room_with_pages(store, count=2)
    session = _session(book, room_id)
    with pytest.raises(SessionConflict) as caught:
        book.end_session(session["id"], source="test")
    assert caught.value.reason == v.REASON_SESSION_NOT_LIVE


def test_an_ended_session_cannot_be_started(store: RecordStore, book: LiveSessionBook):
    room_id = _room_with_pages(store, count=2)
    session = _session(book, room_id)
    book.start_session(session["id"], source="test")
    book.end_session(session["id"], source="test")
    with pytest.raises(SessionConflict) as caught:
        book.start_session(session["id"], source="test")
    assert caught.value.reason == v.REASON_ALREADY_ENDED


# --------------------------------------------------------------------------- #
# Questions
# --------------------------------------------------------------------------- #


def test_a_question_is_logged_against_the_deal(store: RecordStore, book: LiveSessionBook):
    room_id = _room_with_pages(store, count=2)
    session = _session(book, room_id)
    question = book.ask(session["id"], {"asker": "a@x.example", "text": "SSO?"}, source="test")
    assert question["status"] == v.QUESTION_OPEN
    assert question["deal_ref"] == room_id
    assert question["room_id"] == room_id


def test_answering_a_question_twice_is_refused(store: RecordStore, book: LiveSessionBook):
    room_id = _room_with_pages(store, count=2)
    session = _session(book, room_id)
    question = book.ask(session["id"], {"asker": "a@x.example", "text": "SSO?"}, source="test")
    answered = book.answer(question["id"], {"answer": "Yes, by SCIM."}, source="test")
    assert answered["status"] == v.QUESTION_ANSWERED
    with pytest.raises(SessionConflict) as caught:
        book.answer(question["id"], {"answer": "Again"}, source="test")
    assert caught.value.reason == v.REASON_QUESTION_ALREADY_ANSWERED


def test_an_unknown_question_is_not_found(book: LiveSessionBook):
    with pytest.raises(SessionNotFound):
        book.answer("wf128_question_missing", {"answer": "x"}, source="test")


def test_the_open_question_filter_narrows_to_unanswered(store: RecordStore, book: LiveSessionBook):
    room_id = _room_with_pages(store, count=2)
    session = _session(book, room_id)
    first = book.ask(session["id"], {"asker": "a@x.example", "text": "One"}, source="test")
    book.ask(session["id"], {"asker": "b@x.example", "text": "Two"}, source="test")
    book.answer(first["id"], {"answer": "Sure"}, source="test")
    open_rows = book.questions(session_id=session["id"], status=v.QUESTION_OPEN)
    assert [row["text"] for row in open_rows] == ["Two"]


# --------------------------------------------------------------------------- #
# The pure helpers
# --------------------------------------------------------------------------- #


def test_attendance_is_join_based():
    assert rules.attendance_state(None) == v.ATTENDANCE_INVITED_ONLY
    assert rules.attendance_state(1000) == v.ATTENDANCE_ATTENDED


def test_parse_timestamp_reads_every_supported_shape():
    assert rules.parse_timestamp(None, "at") is None
    assert rules.parse_timestamp("", "at") is None

    expected = rules.parse_timestamp(NOW, "at")
    assert expected is not None
    # An ISO string, with and without a Z, a naive datetime, a date, a number
    # and a numeric string all resolve to the same instant.
    assert rules.parse_timestamp(NOW.isoformat(), "at") == expected
    assert rules.parse_timestamp(NOW.isoformat().replace("+00:00", "Z"), "at") == expected
    assert rules.parse_timestamp(NOW.replace(tzinfo=None), "at") == expected
    assert rules.parse_timestamp(NOW.date(), "at") == rules.parse_timestamp(
        datetime(2026, 5, 4, tzinfo=timezone.utc), "at"
    )
    assert rules.parse_timestamp(expected, "at") == expected
    assert rules.parse_timestamp(str(expected), "at") == expected
    assert rules.parse_timestamp(float(expected), "at") == expected
    assert rules.parse_timestamp(1000, "at") == 1000


def test_parse_timestamp_refuses_a_boolean_a_bad_string_and_a_bad_type():
    with pytest.raises(SessionValidationError) as boolean:
        rules.parse_timestamp(True, "at")
    assert "at" in boolean.value.errors
    with pytest.raises(SessionValidationError):
        rules.parse_timestamp("not-a-date", "at")
    with pytest.raises(SessionValidationError):
        rules.parse_timestamp(object(), "at")


def test_to_iso_is_the_inverse_of_a_millisecond_value():
    assert rules.to_iso(None) is None
    assert rules.to_iso(0) == "1970-01-01T00:00:00.000+00:00"


def test_a_session_with_an_unreadable_schedule_is_refused():
    with pytest.raises(SessionValidationError) as caught:
        rules.validate_session({"room_id": "room_1", "scheduled_for": "whenever"})
    assert "scheduled_for" in caught.value.errors


def test_a_move_with_a_negative_or_boolean_position_is_refused():
    with pytest.raises(SessionValidationError) as negative:
        rules.validate_move({"position": -1})
    assert "position" in negative.value.errors
    with pytest.raises(SessionValidationError) as boolean:
        rules.validate_move({"position": True})
    assert "position" in boolean.value.errors


def test_an_answer_without_text_is_refused():
    with pytest.raises(SessionValidationError) as caught:
        rules.validate_answer({})
    assert "answer" in caught.value.errors


def test_a_choice_falls_back_to_its_default_or_records_the_field():
    errors: dict[str, str] = {}
    assert rules.coerce_choice(None, ["a", "b"], "field", errors, default="a") == "a"
    assert errors == {}
    assert rules.coerce_choice("b", ["a", "b"], "field", errors) == "b"
    assert rules.coerce_choice("z", ["a", "b"], "field", errors) is None
    assert "field" in errors


def test_step_at_returns_none_off_the_end_of_the_agenda():
    agenda = rules.plan_agenda(None)
    assert rules.step_at(agenda, 0)["kind"] == v.STEP_SCREEN
    assert rules.step_at(agenda, 5) is None
    assert rules.step_at([], 0) is None


def test_attended_seconds_uses_leave_or_now():
    start = rules.parse_timestamp(NOW, "now")
    later = rules.parse_timestamp(LATER, "now")
    assert rules.attended_seconds(start, later, later) == 1800
    assert rules.attended_seconds(start, None, later) == 1800
    assert rules.attended_seconds(None, later, later) == 0


def test_the_join_and_replay_urls_are_stand_ins():
    assert rules.join_url("tok") == f"{v.JOIN_PATH}/tok"
    assert rules.replay_url("sess") == f"{v.REPLAY_HOST}/sess.m3u8"
    assert v.REPLAY_HOST.startswith("https://")


# --------------------------------------------------------------------------- #
# The vocabulary: the issue's open decisions
# --------------------------------------------------------------------------- #


def test_the_vocabulary_states_that_live_transport_is_a_stand_in():
    published = v.published_vocabulary()
    assert published["transport"] == v.TRANSPORT_STAND_IN
    assert "no realtime transport" in published["transport_note"].lower()
    assert any("no realtime transport" in gap.lower() for gap in published["sourced_gaps"])


def test_the_vocabulary_records_the_stack_choice_with_licences():
    published = v.published_vocabulary()
    assert published["stack_choice"]["sfu"]["name"] == "LiveKit"
    assert published["stack_choice"]["sfu"]["licence"] == "Apache-2.0"
    assert published["stack_choice"]["deck"]["name"] == "reveal.js"
    assert published["stack_choice"]["deck"]["licence"] == "MIT"
    assert all(entry["reason"] for entry in published["stack_choice"].values())


def test_the_vocabulary_names_what_was_not_built():
    published = v.published_vocabulary()
    assert v.AUTOMATION_TRANSLATE_CAPTIONS in published["unimplemented_automations"]
    assert v.AUTOMATION_RECORD_AND_PUBLISH in published["implemented_automations"]
    assert v.AUTOMATION_ATTENDANCE_TO_CRM in published["unimplemented_automations"]


def test_the_researched_user_flow_is_published_in_order():
    published = v.published_vocabulary()
    steps = published["user_flow"]
    assert [row["step"] for row in steps] == [str(i) for i in range(1, len(v.USER_FLOW) + 1)]
    assert "replay" in steps[2]["text"]


# --------------------------------------------------------------------------- #
# The stored shape and the audit guarantee
# --------------------------------------------------------------------------- #


def test_every_record_is_plain_json_in_data(store: RecordStore, book: LiveSessionBook):
    room_id = _room_with_pages(store, count=2)
    session = _session(book, room_id)
    raw = store.get(session["id"])
    assert isinstance(raw["data"], dict)
    assert "state" in raw["data"]
    # The envelope is the only fixed vocabulary; the payload is arbitrary JSON.
    assert set(raw) >= {"id", "collection", "room_id", "revision", "created_at", "updated_at"}


def test_each_write_leaves_an_audit_row_with_a_source(store: RecordStore, book: LiveSessionBook):
    room_id = _room_with_pages(store, count=2)
    session = _session(book, room_id)
    book.start_session(session["id"], source="POST /api/wf-128/sessions/{session_id}/start")
    rows = [row for row in store.audit() if str(row.get("collection") or "").startswith("wf128")]
    assert rows
    assert all(row.get("source") for row in rows)


def test_the_domain_package_never_imports_the_app_or_opens_sqlite():
    import ast
    from pathlib import Path

    package = Path(__file__).resolve().parents[1] / "dsr" / "live_sessions"
    feature = Path(__file__).resolve().parents[1] / "dsr" / "features" / f"{MODULE}.py"
    for path in sorted(package.glob("*.py")) + [feature]:
        source = path.read_text(encoding="utf-8")
        assert "import sqlite3" not in source, path.name
        assert "from dsr.api" not in source and "import dsr.api" not in source, path.name
        ast.parse(source)  # the file parses, so the grep above read real code


def test_the_feature_module_exports_the_seed_hook_and_a_cp1252_summary():
    module = load_feature(MODULE)
    assert callable(module.seed)
    summary = module.seed.__doc__
    assert summary and "cp1252" in summary.lower()


def test_the_feature_module_declares_the_ticket_prefix():
    module = load_feature(MODULE)
    assert module.FEATURE["id"] == v.FEATURE_ID
    assert module.router.prefix == "/api/wf-128"
    assert module.FEATURE["ticket"] == v.TICKET


def test_the_seed_hook_creates_the_researched_states(db):
    """The demo states, in process, and the summary stays cp1252-encodable."""

    module = load_feature(MODULE)
    first = db.create("room", {"name": "Northwind", "owner": "dana"}, actor="t", source="t")["id"]
    second = db.create("room", {"name": "Contoso", "owner": "sam"}, actor="t", source="t")["id"]

    summary = module.seed(db, {"room_ids": [(first, "Northwind"), (second, "Contoso")], "now": NOW})

    summary.encode("cp1252")  # a non-encodable character would break the seeder
    assert summary.startswith("WF-128 seeded")
    assert "invited only" in summary  # not every state is a success
    assert "questions open" in summary
    assert db.count(v.SESSION_COLLECTION) == 2
    assert db.count(v.RECORDING_COLLECTION) == 1


def test_the_seed_hook_declines_a_context_with_no_rooms(db):
    module = load_feature(MODULE)
    assert module.seed(db, {"room_ids": [], "now": NOW}) == "no rooms to attach to"
