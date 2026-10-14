"""WF-126: the personalised video rules, exercised as a domain module with no server.

The rules in ``dsr.personalised_video`` are pure over the
:class:`~dsr.store.RecordStore` and touch nothing else. So every rule below is
reachable without a ``TestClient``, and these tests are what make each of the
issue's open decisions checkable at the instant it is stated about.

What is under test, and why each one matters
--------------------------------------------

* **A recording transcodes on save.** "Auto-transcode and publish on save" is
  modelled as a deterministic state on write, and the block says it is a stand-in
  so a reviewer cannot mistake it for a working pipeline.
* **The hero holds one video.** Placing a second supersedes the first rather than
  deleting it, so a buyer's playback history still resolves.
* **A recording may only be placed on its own room's hero.** The user flow is
  "from inside the room", so a cross-room placement is refused rather than
  silently allowed.
* **Personalising requires a known buyer, unless the room declares nobody.** The
  buyer identity comes from WF-004's grants; an unconfigured room accepts any
  buyer, the same shape WF-002 uses for page editing.
* **The roll-up folds the four researched events.** start, percent watched,
  completes and replays each move the roll-up, and the input is not mutated.
* **A bare start does not raise a low-completion alert.** The automation is
  "alert the rep on low completion", and completion is not yet possible at zero
  percent.
* **Completing clears an open alert.** Otherwise the rep keeps seeing a warning
  for a buyer who has since finished.
* **Every record is plain JSON in ``data``.** A team adding a field must need no
  coordination with anyone, so this module adds no migration and no typed column.

Every test names the property it protects, because a test named ``test_flow``
tells a reviewer nothing about which rule they can stop worrying about.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from dsr.db.audited import AuditedDatabase
from dsr.features import load_feature
from dsr.personalised_video import rules, vocabulary as v
from dsr.personalised_video.engine import VideoEngine
from dsr.personalised_video.errors import VideoConflict, VideoNotFound, VideoValidationError
from dsr.store import RecordStore

MODULE = "wf126_asynchronous_personalised_video_inside_the_room"

NOW = datetime(2026, 5, 4, 9, 0, tzinfo=timezone.utc)


@pytest.fixture()
def engine(store: RecordStore) -> VideoEngine:
    """The engine, with the clock pinned so every timestamp is exact."""

    return VideoEngine(store, now=lambda: NOW)


@pytest.fixture()
def room(store: RecordStore) -> str:
    return store.create("room", {"name": "Northwind"}, actor="test", source="test")["id"]


def _home_page(store: RecordStore, room_id: str) -> str:
    return store.create(
        "page",
        {"title": "Welcome", "slug": "welcome", v.HERO_PAGE_FLAG: True},
        room_id=room_id,
        actor="test",
        source="test",
    )["id"]


def _buyer(store: RecordStore, room_id: str, email: str, role: str = "viewer") -> str:
    return store.create(
        "room_access",
        {"principal": email, "role": role},
        room_id=room_id,
        actor="test",
        source="test",
    )["id"]


def _record(engine: VideoEngine, room_id: str, **extra: object) -> dict:
    return engine.create_recording(
        {"title": "Intro", "room_id": room_id, **extra}, source="test", actor="dana"
    )


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #


def test_a_recording_without_a_title_is_refused():
    with pytest.raises(VideoValidationError) as caught:
        rules.validate_recording({"kind": v.KIND_PERSONAL_INTRO})
    assert "title" in caught.value.errors


def test_an_unknown_kind_or_source_is_refused():
    with pytest.raises(VideoValidationError) as caught:
        rules.validate_recording({"title": "X", "kind": "podcast", "source": "webcam"})
    assert set(caught.value.errors) == {"kind", "source"}


def test_the_two_researched_kinds_and_three_sources_are_the_only_ones():
    assert v.KINDS == (v.KIND_PERSONAL_INTRO, v.KIND_WALKTHROUGH)
    assert set(v.CAPTURE_SOURCES) == {v.SOURCE_CAMERA, v.SOURCE_SCREEN, v.SOURCE_CAMERA_AND_SCREEN}
    stored = rules.validate_recording({"title": "X"})
    assert stored["kind"] == v.DEFAULT_KIND
    assert stored["source"] == v.DEFAULT_SOURCE


def test_a_negative_duration_is_refused():
    with pytest.raises(VideoValidationError) as caught:
        rules.validate_recording({"title": "X", "duration_seconds": -1})
    assert "duration_seconds" in caught.value.errors


def test_a_playback_event_must_name_one_of_the_four_researched_events():
    with pytest.raises(VideoValidationError) as caught:
        rules.validate_playback({"event": "clicked", "viewer": "a@b.example"})
    assert "event" in caught.value.errors


def test_a_progress_event_without_a_percentage_is_refused():
    with pytest.raises(VideoValidationError) as caught:
        rules.validate_playback({"event": v.EVENT_PROGRESS, "viewer": "a@b.example"})
    assert "watched_percent" in caught.value.errors


def test_a_completion_without_a_percentage_defaults_to_one_hundred():
    stored = rules.validate_playback({"event": v.EVENT_COMPLETE, "viewer": "a@b.example"})
    assert stored["watched_percent"] == 100


def test_a_percentage_outside_zero_to_one_hundred_is_refused():
    with pytest.raises(VideoValidationError) as caught:
        rules.validate_playback(
            {"event": v.EVENT_PROGRESS, "viewer": "a@b.example", "watched_percent": 140}
        )
    assert "watched_percent" in caught.value.errors


def test_a_viewer_is_stored_lower_cased_so_the_buyer_has_one_spelling():
    stored = rules.validate_playback(
        {"event": v.EVENT_START, "viewer": "A.Buyer@Northwind.Example"}
    )
    assert stored["viewer"] == "a.buyer@northwind.example"


# --------------------------------------------------------------------------- #
# The roll-up
# --------------------------------------------------------------------------- #


def test_the_four_researched_events_each_move_the_roll_up():
    base = rules.blank_engagement("rec", "a@b.example")
    started = rules.apply_event(base, {"event": v.EVENT_START}, 1000)
    assert started["starts"] == 1
    assert rules.engagement_state(started) == v.STATE_STARTED

    progressed = rules.apply_event(
        started, {"event": v.EVENT_PROGRESS, "watched_percent": 40}, 2000
    )
    assert progressed["max_percent"] == 40
    assert progressed["max_percent"] == max(progressed["max_percent"], 40)

    replayed = rules.apply_event(progressed, {"event": v.EVENT_REPLAY}, 3000)
    assert replayed["replays"] == 1

    completed = rules.apply_event(replayed, {"event": v.EVENT_COMPLETE}, 4000)
    assert completed["completes"] == 1
    assert completed["completed"] is True
    assert completed["max_percent"] >= v.COMPLETE_PERCENT


def test_folding_an_event_does_not_mutate_its_input():
    base = rules.blank_engagement("rec", "a@b.example")
    rules.apply_event(base, {"event": v.EVENT_START}, 1000)
    # The zero roll-up is still zero, so a caller can replay the same event twice.
    assert base["events"] == 0
    assert base["starts"] == 0


def test_the_percentage_never_goes_backwards():
    base = rules.blank_engagement("rec", "a@b.example")
    high = rules.apply_event(base, {"event": v.EVENT_PROGRESS, "watched_percent": 80}, 1000)
    low = rules.apply_event(high, {"event": v.EVENT_PROGRESS, "watched_percent": 10}, 2000)
    assert low["max_percent"] == 80


def test_a_percentage_at_the_threshold_completes_without_a_complete_event():
    base = rules.blank_engagement("rec", "a@b.example")
    done = rules.apply_event(
        base, {"event": v.EVENT_PROGRESS, "watched_percent": v.COMPLETE_PERCENT}, 1
    )
    assert done["completed"] is True
    assert rules.engagement_state(done) == v.STATE_COMPLETED


# --------------------------------------------------------------------------- #
# The low-completion rule
# --------------------------------------------------------------------------- #


def test_a_viewer_who_stopped_short_raises_an_alert():
    rollup = {"events": 1, "max_percent": 20, "completed": False}
    assert rules.should_alert(rollup) is True


def test_a_bare_start_does_not_raise_an_alert():
    """Completion is not yet possible at zero percent, so nothing is alerted."""

    assert rules.should_alert({"events": 1, "max_percent": 0, "completed": False}) is False


def test_a_completed_viewer_is_never_alerted():
    assert rules.should_alert({"events": 1, "max_percent": 100, "completed": True}) is False


def test_a_viewer_above_the_threshold_is_not_alerted():
    above = v.LOW_COMPLETION_PERCENT + 1
    assert rules.should_alert({"events": 1, "max_percent": above, "completed": False}) is False


def test_the_threshold_is_a_parameter_so_it_can_be_argued_with_in_one_place():
    assert rules.should_alert({"events": 1, "max_percent": 70, "completed": False}, 80) is True
    assert rules.should_alert({"events": 1, "max_percent": 70, "completed": False}, 50) is False


# --------------------------------------------------------------------------- #
# The recording
# --------------------------------------------------------------------------- #


def test_a_recording_transcodes_on_save_and_says_it_is_a_stand_in(engine, room):
    recording = _record(engine, room)
    assert recording["status"] == v.RECORDING_RECORDED
    assert recording["transcode"]["state"] == v.TRANSCODE_READY
    assert recording["transcode"]["stand_in"] is True
    # Hosting is not claimed: the media reference is opaque and the URL is fake.
    assert recording["hosted"] is False
    assert recording["media_ref"].startswith("media://wf126/")
    assert "example.invalid" in recording["stream_ref"]


def test_a_recording_for_an_unknown_room_is_refused(engine):
    with pytest.raises(VideoNotFound):
        _record(engine, "room_does_not_exist")


# --------------------------------------------------------------------------- #
# The hero placement
# --------------------------------------------------------------------------- #


def test_placing_a_recording_resolves_the_room_home_page_as_the_hero(engine, room, store):
    page_id = _home_page(store, room)
    _buyer(store, room, "a.buyer@northwind.example")
    recording = _record(engine, room)
    placement = engine.place_on_hero(
        room, recording["id"], {"note": "Before our call"}, source="test", actor="dana"
    )
    assert placement["surface"] == v.SURFACE_HERO
    assert placement["page_id"] == page_id
    assert placement["status"] == v.PLACEMENT_LIVE
    assert engine.get_recording(recording["id"])["status"] == v.RECORDING_PLACED


def test_a_second_placement_supersedes_the_first_rather_than_deleting_it(engine, room, store):
    _buyer(store, room, "a.buyer@northwind.example")
    first = _record(engine, room, title="First")
    second = _record(engine, room, title="Second")
    engine.place_on_hero(room, first["id"], {}, source="test", actor="dana")
    outcome = engine.place_on_hero(room, second["id"], {}, source="test", actor="dana")

    assert len(outcome["superseded"]) == 1
    placements = engine.placements(room_id=room)
    statuses = {row["id"]: row["status"] for row in placements}
    assert statuses[outcome["superseded"][0]] == v.PLACEMENT_SUPERSEDED
    assert statuses[outcome["id"]] == v.PLACEMENT_LIVE
    # Both rows still exist, so the buyer's earlier playback history resolves.
    assert len(placements) == 2


def test_placing_a_recording_on_another_room_is_refused(engine, room, store):
    other = store.create("room", {"name": "Contoso"}, actor="test", source="test")["id"]
    recording = _record(engine, room)
    with pytest.raises(VideoConflict) as caught:
        engine.place_on_hero(other, recording["id"], {}, source="test", actor="dana")
    assert caught.value.reason == "recording_belongs_to_another_room"


def test_personalising_for_a_stranger_is_refused_when_the_room_has_buyers(engine, room, store):
    _buyer(store, room, "a.buyer@northwind.example")
    recording = _record(engine, room)
    with pytest.raises(VideoValidationError) as caught:
        engine.place_on_hero(
            room,
            recording["id"],
            {"personalised_for": ["stranger@elsewhere.example"]},
            source="test",
            actor="dana",
        )
    assert "personalised_for" in caught.value.errors


def test_an_unconfigured_room_accepts_the_buyer_it_is_given(engine, room):
    recording = _record(engine, room)
    placement = engine.place_on_hero(
        room,
        recording["id"],
        {"personalised_for": ["anyone@elsewhere.example"]},
        source="test",
        actor="dana",
    )
    assert placement["personalised_for"] == ["anyone@elsewhere.example"]


def test_placing_notifies_every_personalised_buyer(engine, room, store):
    _buyer(store, room, "a.buyer@northwind.example")
    _buyer(store, room, "b.buyer@northwind.example")
    recording = _record(engine, room)
    placement = engine.place_on_hero(room, recording["id"], {}, source="test", actor="dana")
    notified = {row["recipient"] for row in placement["notified"]}
    assert notified == {"a.buyer@northwind.example", "b.buyer@northwind.example"}
    assert len(engine.notifications(room_id=room)) == 2


def test_withdrawing_a_live_placement_keeps_the_row(engine, room, store):
    _buyer(store, room, "a.buyer@northwind.example")
    recording = _record(engine, room)
    placement = engine.place_on_hero(room, recording["id"], {}, source="test", actor="dana")
    withdrawn = engine.withdraw_placement(placement["id"], source="test", actor="dana")
    assert withdrawn["status"] == v.PLACEMENT_WITHDRAWN
    assert engine.placements(room_id=room)[0]["id"] == placement["id"]


def test_withdrawing_a_placement_that_is_not_live_is_a_conflict(engine, room, store):
    _buyer(store, room, "a.buyer@northwind.example")
    recording = _record(engine, room)
    placement = engine.place_on_hero(room, recording["id"], {}, source="test", actor="dana")
    engine.withdraw_placement(placement["id"], source="test", actor="dana")
    with pytest.raises(VideoConflict):
        engine.withdraw_placement(placement["id"], source="test", actor="dana")


# --------------------------------------------------------------------------- #
# Recipients, reused from WF-004
# --------------------------------------------------------------------------- #


def test_recipients_come_from_grants_invitations_and_viewers(engine, room, store):
    _buyer(store, room, "grant@northwind.example", role="room_collaborator")
    store.create(
        v.INVITATIONS,
        {"email": "invited@northwind.example", "role": "viewer", "state": "pending"},
        room_id=room,
        actor="test",
        source="test",
    )
    store.update(
        room, {v.ROOM_VIEWERS_FIELD: ["listed@northwind.example"]}, actor="test", source="test"
    )

    by_email = {row["email"]: row for row in engine.recipients(room)}
    assert set(by_email) == {
        "grant@northwind.example",
        "invited@northwind.example",
        "listed@northwind.example",
    }
    assert by_email["grant@northwind.example"]["source"] == "grant"
    assert by_email["grant@northwind.example"]["role"] == "room_collaborator"


def test_a_grant_wins_over_an_invitation_for_the_same_buyer(engine, room, store):
    _buyer(store, room, "same@northwind.example", role="content_contributor")
    store.create(
        v.INVITATIONS,
        {"email": "same@northwind.example", "role": "viewer", "state": "pending"},
        room_id=room,
        actor="test",
        source="test",
    )
    rows = [row for row in engine.recipients(room) if row["email"] == "same@northwind.example"]
    assert len(rows) == 1
    assert rows[0]["source"] == "grant"


# --------------------------------------------------------------------------- #
# Playback over the log
# --------------------------------------------------------------------------- #


def test_a_playback_event_opens_then_clears_the_low_completion_alert(engine, room, store):
    _buyer(store, room, "a.buyer@northwind.example")
    recording = _record(engine, room)
    engine.place_on_hero(room, recording["id"], {}, source="test", actor="dana")

    engine.record_playback(
        recording["id"],
        {"event": v.EVENT_START, "viewer": "a.buyer@northwind.example"},
        source="test",
        actor="buyer",
    )
    low = engine.record_playback(
        recording["id"],
        {"event": v.EVENT_PROGRESS, "viewer": "a.buyer@northwind.example", "watched_percent": 20},
        source="test",
        actor="buyer",
    )
    assert low["alert"]["status"] == v.ALERT_OPEN
    assert len(engine.alerts(status=v.ALERT_OPEN)) == 1

    done = engine.record_playback(
        recording["id"],
        {"event": v.EVENT_COMPLETE, "viewer": "a.buyer@northwind.example"},
        source="test",
        actor="buyer",
    )
    assert done["alert"]["status"] == v.ALERT_CLEARED
    assert engine.alerts(status=v.ALERT_OPEN) == []
    # One alert row, updated rather than duplicated.
    assert len(engine.alerts()) == 1


def test_the_buyer_hero_is_personalised_and_carries_the_sellers_message(engine, room, store):
    _buyer(store, room, "a.buyer@northwind.example")
    recording = _record(engine, room)
    engine.place_on_hero(
        room, recording["id"], {"note": "Here is the pricing answer."}, source="test", actor="dana"
    )
    hero = engine.hero_for(room, "a.buyer@northwind.example")
    assert hero["personalised"] is True
    assert hero["message"] == "Here is the pricing answer."
    assert hero["recording"]["id"] == recording["id"]


def test_a_buyer_who_is_not_personalised_still_reads_the_video(engine, room, store):
    _buyer(store, room, "a.buyer@northwind.example")
    _buyer(store, room, "b.buyer@northwind.example")
    recording = _record(engine, room)
    engine.place_on_hero(
        room,
        recording["id"],
        {"personalised_for": ["a.buyer@northwind.example"]},
        source="test",
        actor="dana",
    )
    hero = engine.hero_for(room, "b.buyer@northwind.example")
    assert hero["personalised"] is False
    assert hero["recording"]["id"] == recording["id"]


def test_the_summary_counts_the_states_the_page_shows(engine, room, store):
    _buyer(store, room, "a.buyer@northwind.example")
    recording = _record(engine, room)
    engine.place_on_hero(room, recording["id"], {}, source="test", actor="dana")
    engine.record_playback(
        recording["id"],
        {"event": v.EVENT_START, "viewer": "a.buyer@northwind.example"},
        source="test",
        actor="buyer",
    )
    engine.record_playback(
        recording["id"],
        {"event": v.EVENT_PROGRESS, "viewer": "a.buyer@northwind.example", "watched_percent": 25},
        source="test",
        actor="buyer",
    )
    counted = engine.summary(room)
    assert counted["recordings"] == 1
    assert counted["live_placements"] == 1
    assert counted["viewers"] == 1
    assert counted["started"] == 1
    assert counted["low_completion_alerts"] == 1
    assert "generated_at" in counted


# --------------------------------------------------------------------------- #
# Schema flexibility
# --------------------------------------------------------------------------- #


def test_a_team_can_add_its_own_field_with_no_migration(store, room):
    record = store.create(
        v.RECORDING_COLLECTION,
        {
            "title": "With a local field",
            "kind": v.KIND_PERSONAL_INTRO,
            "procurement_ticket": "PR-4471",
            "our_field": {"reviewer": "sam", "weight": 3},
        },
        room_id=room,
        actor="test",
        source="test",
    )
    found = store.find(v.RECORDING_COLLECTION, {"procurement_ticket": "PR-4471"}, limit=5)
    assert [row["id"] for row in found] == [record["id"]]
    assert found[0]["data"]["our_field"] == {"reviewer": "sam", "weight": 3}


def test_every_record_this_workflow_writes_is_ordinary_json(store, engine, room):
    _buyer(store, room, "a.buyer@northwind.example")
    recording = _record(engine, room)
    engine.place_on_hero(room, recording["id"], {}, source="test", actor="dana")
    engine.record_playback(
        recording["id"],
        {"event": v.EVENT_START, "viewer": "a.buyer@northwind.example"},
        source="test",
        actor="buyer",
    )

    for collection in (
        v.RECORDING_COLLECTION,
        v.PLACEMENT_COLLECTION,
        v.PLAYBACK_COLLECTION,
        v.ENGAGEMENT_COLLECTION,
        v.NOTIFICATION_COLLECTION,
        v.ALERT_COLLECTION,
    ):
        for row in store.find(collection, {}, limit=50):
            for key, value in row["data"].items():
                assert isinstance(key, str), (collection, key)
                assert isinstance(value, (str, int, float, bool, list, dict, type(None))), (
                    collection,
                    key,
                    value,
                )


# --------------------------------------------------------------------------- #
# The domain module's own contract
# --------------------------------------------------------------------------- #


def test_the_domain_package_imports_no_framework_and_no_sqlite():
    """A domain module that imported FastAPI could not be exercised without a
    server, and one that opened SQLite itself could bypass the audit guarantee."""

    import ast
    from pathlib import Path

    import dsr.personalised_video as package

    folder = Path(package.__file__).parent
    for source_file in sorted(folder.glob("*.py")):
        tree = ast.parse(source_file.read_text(encoding="utf-8"))
        imported: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)
        assert "sqlite3" not in imported, source_file.name
        assert "fastapi" not in imported, source_file.name
        assert "dsr.api" not in imported, source_file.name
        assert "starlette" not in imported, source_file.name


def test_the_feature_declares_a_ticket_derived_prefix():
    feature = load_feature(MODULE)
    assert feature.router.prefix == "/api/wf-126"
    assert feature.FEATURE["ticket"] == "WF-126"
    assert feature.FEATURE["id"] == "wf-126-personalised-video"


def test_every_route_sits_under_this_features_own_prefix():
    feature = load_feature(MODULE)
    for route in feature.router.routes:
        assert route.path.startswith("/api/wf-126"), route.path


def test_the_vocabulary_publishes_the_decisions_the_issue_left_open():
    served = VideoEngine.vocabulary()
    assert served["events"] == list(v.EVENTS)
    assert served["kinds"] == list(v.KINDS)
    assert served["low_completion_percent"] == v.LOW_COMPLETION_PERCENT
    assert served["complete_percent"] == v.COMPLETE_PERCENT
    assert "low_completion_threshold" in served["inferences"]
    # The unimplemented automation is named, not quietly omitted.
    assert served["unimplemented_automations"] == list(v.UNIMPLEMENTED_AUTOMATIONS)
    # Hosting is named as unsourced, not pretended.
    assert any("hosting" in note.lower() for note in served["sourced_gaps"])


# --------------------------------------------------------------------------- #
# The seed
# --------------------------------------------------------------------------- #


def test_the_seed_string_is_encodable_by_cp1252(db: AuditedDatabase):
    """The seeder prints it on a Windows console, so every character must encode.

    A single RIGHTWARDS ARROW in one recovered feature broke the entire seeder.
    """

    feature = load_feature(MODULE)
    room_ids = [
        (db.create("room", {"name": name}, actor="seed", source="seed")["id"], name)
        for name in ("Northwind", "Contoso")
    ]
    reported = feature.seed(db, {"room_ids": room_ids, "now": NOW})
    assert reported
    reported.encode("cp1252")
    assert reported == reported.encode("cp1252").decode("cp1252")


def test_the_seed_creates_the_states_the_issue_asks_for(db: AuditedDatabase):
    feature = load_feature(MODULE)
    room_ids = []
    for name in ("Northwind", "Contoso"):
        room_id = db.create("room", {"name": name}, actor="seed", source="seed")["id"]
        db.create(
            "page",
            {"title": "Welcome", "slug": "welcome", v.HERO_PAGE_FLAG: True},
            room_id=room_id,
            actor="seed",
            source="seed",
        )
        for email in ("a.buyer@example.com", "b.buyer@example.com"):
            db.create(
                "room_access",
                {"principal": email, "role": "viewer"},
                room_id=room_id,
                actor="seed",
                source="seed",
            )
        room_ids.append((room_id, name))

    feature.seed(db, {"room_ids": room_ids, "now": NOW})

    store = RecordStore(db)
    engine = VideoEngine(store, now=lambda: NOW)
    counted = engine.summary()
    assert counted["recordings"] >= 2, counted
    assert counted["live_placements"] >= 2, counted
    assert counted["completed"] >= 1, counted
    assert counted["low_completion_alerts"] >= 1, counted
    assert counted["notifications"] >= 1, counted
