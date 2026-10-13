"""WF-123: the domain rules behind in-room qualification capture.

What is under test
------------------

The package :mod:`dsr.qualification_capture` holds the vocabulary, the pure rules
and the engine, and this file exercises them without the framework. Keeping the
package separate from the feature module is what makes that possible: a band cut,
a weight and a routing map can each be read and checked on their own, and a test
that goes through HTTP to check a number cannot tell a broken rule from a broken
handler.

The groups are:

* **The vocabulary.** The four researched dimensions, the bands and the single
  visibility rule, each pinned because a change to one is a recorded decision.
* **The pure rules.** Validation, scoring and banding as functions of their
  arguments, with no store in sight.
* **The engine.** Forms, submissions, the routing decision and the seller-only
  summary, driven against a fresh in-memory store.
* **Reused vocabulary.** A room from :mod:`dsr.room_lifecycle` and a visitor
  session from :mod:`dsr.access` (WF-015) are read, never re-declared.
* **The refusals and the demo data.** Every distinct error, and the string the
  seeder prints on a Windows console, which has to stay cp1252-encodable.

The clock is frozen: every test builds the engine with ``now=lambda: NOW``, so
no assertion depends on the day it runs.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from dsr.qualification_capture import (
    BAND_DEVELOPING,
    BAND_QUALIFIED,
    BAND_UNQUALIFIED,
    DIMENSIONS,
    FEATURE_ID,
    FORM_COLLECTION,
    PREFIX,
    ROUTE_TARGET_BY_BAND,
    SELLER_ONLY_FIELDS,
    SUBMISSION_COLLECTION,
    TICKET,
    CaptureConflict,
    CaptureNotFound,
    FormValidationError,
    QualificationEngine,
    band_for,
    by_id,
    describe_inferences,
    form_dimensions,
    form_is_live,
    max_score,
    missing_required,
    normalise_answers,
    normalise_form_payload,
    parse_timestamp,
    qualify,
    required_dimensions,
    routing_target,
    score_answers,
    status_of,
    to_iso,
    vocabulary as v,
    weight_of,
)
from dsr.store import RecordStore

ROOM = "room-1"
NOW = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
NOW_MS = int(NOW.timestamp() * 1000)
SOURCE = "test"

#: The visitor collection and its two accepted states, which belong to WF-015's
#: identity gate. Written here as rows so the reuse is exercised rather than
#: assumed, and imported from the vocabulary so this file cannot drift from it.
VISITOR_SESSION = v.VISITOR_SESSION_COLLECTION


# --------------------------------------------------------------------------- #
# Fixtures and helpers
# --------------------------------------------------------------------------- #


def put_room(store, room_id=ROOM, **overrides):
    """A room record in the core collection, the way the seeder writes one."""
    data = {"name": "Northwind Traders"}
    data.update(overrides)
    return store.create("room", data, record_id=room_id, actor="dana", source=SOURCE)


def put_visitor(store, session_id="sess-1", status="identified", room_id=ROOM):
    return store.create(
        VISITOR_SESSION,
        {"status": status, "name": "Dana Okafor", "email": "dana@northwind.example"},
        record_id=session_id,
        room_id=room_id,
        actor="dana",
        source=SOURCE,
    )


def engine_for(store):
    return QualificationEngine(store, now=lambda: NOW)


def make_live_form(store, room_id=ROOM, **form_overrides):
    engine = engine_for(store)
    payload = {"name": "Northwind qualification", "deal_ref": "deal_northwind"}
    payload.update(form_overrides)
    form = engine.create_form(payload, room_id=room_id, source=SOURCE, actor="dana")
    engine.publish_form(form["id"], source=SOURCE, actor="dana")
    return engine, form


FULL_ANSWERS = {
    v.DIMENSION_BUDGET: "funded",
    v.DIMENSION_TIMELINE: "now",
    v.DIMENSION_AUTHORITY: "decision_maker",
    v.DIMENSION_PROCUREMENT_PATH: "known_process",
}


# --------------------------------------------------------------------------- #
# The vocabulary
# --------------------------------------------------------------------------- #


def test_the_four_researched_dimensions_are_the_only_ones():
    """The user flow names exactly four: budget, timeline, authority, path."""
    assert DIMENSIONS == ("budget", "timeline", "authority", "procurement_path")
    assert TICKET == "WF-123"
    assert FEATURE_ID == "wf-123-qualification-capture"
    assert PREFIX == "/api/wf-123"


def test_every_dimension_has_a_closed_option_set_and_a_weight_per_option():
    for dimension in DIMENSIONS:
        options = v.ANSWER_OPTIONS[dimension]
        assert options, dimension
        assert set(options) == set(v.ANSWER_WEIGHTS[dimension])
        assert set(options) == set(v.ANSWER_LABELS[dimension])
        assert max(v.ANSWER_WEIGHTS[dimension].values()) == v.WEIGHT_STRONG


def test_the_band_ratios_are_ordered_and_reachable():
    assert v.BAND_QUALIFIED_RATIO > v.BAND_DEVELOPING_RATIO > 0
    assert v.BAND_QUALIFIED_RATIO <= 1


def test_each_band_routes_to_a_named_target():
    assert set(ROUTE_TARGET_BY_BAND) == set(v.BANDS)
    # qualified -> the account executive; unqualified -> nurture.
    assert ROUTE_TARGET_BY_BAND[BAND_QUALIFIED] == "account_executive"
    assert ROUTE_TARGET_BY_BAND[BAND_UNQUALIFIED] == "nurture"


def test_the_seller_only_list_names_the_score_the_band_and_the_route():
    for field in ("score", "band", "routing", "route_target"):
        assert field in SELLER_ONLY_FIELDS


def test_the_published_vocabulary_carries_the_not_sourced_limit():
    payload = QualificationEngine.vocabulary()
    assert payload["ticket"] == "WF-123"
    assert payload["dimensions"] == list(DIMENSIONS)
    assert payload["room_vocabulary"]["statuses"] == list(v.ROOM_STATUSES)
    assert payload["visitor_vocabulary"]["capturable_states"] == list(v.CAPTURABLE_VISITOR_STATES)
    assert payload["sourced_gaps"], "an inferred scale must say it is inferred"


# --------------------------------------------------------------------------- #
# Timestamps
# --------------------------------------------------------------------------- #


def test_a_timestamp_is_read_from_every_shape_but_a_boolean():
    assert parse_timestamp(None) is None
    assert parse_timestamp("") is None
    assert parse_timestamp(1_700_000_000_000) == 1_700_000_000_000
    assert parse_timestamp("1700000000000") == 1_700_000_000_000
    assert parse_timestamp("2026-06-01T12:00:00Z") == NOW_MS
    # A naive ISO string is read as UTC rather than refused.
    assert parse_timestamp("2026-06-01T12:00:00") == NOW_MS
    with pytest.raises(FormValidationError):
        parse_timestamp(True)


def test_a_timestamp_renders_back_as_iso():
    assert to_iso(None) is None
    assert to_iso(NOW_MS).startswith("2026-06-01T12:00:00")


# --------------------------------------------------------------------------- #
# The pure rules: the form schema
# --------------------------------------------------------------------------- #


def test_a_form_defaults_to_all_four_dimensions_all_required():
    fields = normalise_form_payload({"name": "Qual"})
    assert fields["dimensions"] == list(DIMENSIONS)
    assert fields["required_dimensions"] == list(DIMENSIONS)
    assert fields["status"] == v.FORM_STATUS_DRAFT


def test_a_form_may_ask_about_fewer_dimensions():
    fields = normalise_form_payload(
        {"name": "Short", "dimensions": ["timeline", "budget"], "required_dimensions": ["timeline"]}
    )
    # Order follows the research, not the caller.
    assert fields["dimensions"] == ["budget", "timeline"]
    assert fields["required_dimensions"] == ["timeline"]


def test_a_form_needs_a_name():
    with pytest.raises(FormValidationError) as caught:
        normalise_form_payload({})
    assert "name" in caught.value.errors


def test_an_unknown_dimension_is_refused_by_name():
    with pytest.raises(FormValidationError) as caught:
        normalise_form_payload({"name": "Qual", "dimensions": ["budget", "moon"]})
    assert "moon" in caught.value.errors["dimensions"]


def test_a_required_dimension_outside_the_form_is_refused():
    with pytest.raises(FormValidationError) as caught:
        normalise_form_payload(
            {
                "name": "Qual",
                "dimensions": ["budget"],
                "required_dimensions": ["timeline"],
            }
        )
    assert "required_dimensions" in caught.value.errors


def test_an_unknown_band_override_is_refused():
    with pytest.raises(FormValidationError) as caught:
        normalise_form_payload({"name": "Qual", "routing": {"platinum": "vip"}})
    assert "routing" in caught.value.errors


def test_form_dimensions_and_liveness_read_a_record_or_a_payload():
    payload = {"dimensions": ["timeline"], "status": "live"}
    assert form_dimensions(payload) == ["timeline"]
    assert form_is_live(payload) is True
    assert form_dimensions({}) == list(DIMENSIONS)
    assert form_is_live({}) is False

    record = {"id": "f1", "data": payload}
    assert form_dimensions(record) == ["timeline"]
    assert required_dimensions(record) == ["timeline"]


def test_routing_target_reads_the_form_override_then_the_default():
    assert routing_target(BAND_QUALIFIED, {"routing": {"qualified": "enterprise_team"}}) == (
        "enterprise_team"
    )
    assert routing_target(BAND_QUALIFIED, {}) == "account_executive"
    assert routing_target(BAND_DEVELOPING, None) == "sales_development"


# --------------------------------------------------------------------------- #
# The pure rules: answers, scores and bands
# --------------------------------------------------------------------------- #


def test_an_answer_outside_the_closed_set_is_refused_by_name():
    form = {"dimensions": ["budget"]}
    with pytest.raises(FormValidationError) as caught:
        normalise_answers(form, {"budget": "infinite"})
    assert "answers.budget" in caught.value.errors


def test_an_answer_for_a_dimension_the_form_does_not_ask_is_refused():
    form = {"dimensions": ["budget"]}
    with pytest.raises(FormValidationError) as caught:
        normalise_answers(form, {"timeline": "now"})
    assert "answers.timeline" in caught.value.errors


def test_requiring_all_names_each_missing_dimension():
    form = {"dimensions": list(DIMENSIONS)}
    with pytest.raises(FormValidationError) as caught:
        normalise_answers(form, {"budget": "funded"}, require_all=True)
    assert set(caught.value.errors) == {
        "answers.timeline",
        "answers.authority",
        ("answers.procurement_path"),
    }


def test_missing_required_lists_the_unanswered_required_dimensions():
    form = {"dimensions": list(DIMENSIONS), "required_dimensions": ["budget", "timeline"]}
    assert missing_required(form, {"budget": "funded"}) == ["timeline"]
    assert missing_required(form, {"budget": "funded", "timeline": "now"}) == []


def test_weight_score_and_ceiling_are_consistent():
    assert weight_of("budget", "funded") == 3
    assert weight_of("budget", "no_budget") == 0
    assert weight_of("budget", "nonsense") == 0
    assert max_score({"dimensions": list(DIMENSIONS)}) == 12
    assert max_score({"dimensions": ["budget"]}) == 3
    assert score_answers({"dimensions": list(DIMENSIONS)}, FULL_ANSWERS) == 12


def test_the_band_is_a_share_of_the_reachable_maximum():
    assert band_for(12, 12) == BAND_QUALIFIED
    assert band_for(9, 12) == BAND_QUALIFIED  # exactly 0.75
    assert band_for(8, 12) == BAND_DEVELOPING
    assert band_for(5, 12) == BAND_DEVELOPING  # ~0.42
    assert band_for(4, 12) == BAND_UNQUALIFIED
    assert band_for(0, 0) == BAND_UNQUALIFIED


def test_a_two_dimension_form_can_still_qualify():
    """The reason the thresholds are ratios: a fixed cut would fail a perfect short form."""
    form = {"dimensions": ["budget", "timeline"]}
    result = qualify(form, {"budget": "funded", "timeline": "now"})
    assert result["max_score"] == 6
    assert result["band"] == BAND_QUALIFIED


def test_qualify_reports_the_score_band_and_route():
    result = qualify({"dimensions": list(DIMENSIONS)}, FULL_ANSWERS)
    assert result["score"] == 12
    assert result["band"] == BAND_QUALIFIED
    assert result["band_label"] == "Qualified"
    assert result["route_target"] == "account_executive"
    assert result["missing"] == []


# --------------------------------------------------------------------------- #
# The engine: rooms and forms
# --------------------------------------------------------------------------- #


def test_a_form_cannot_be_created_without_a_room(store):
    engine = engine_for(store)
    with pytest.raises(CaptureNotFound):
        engine.create_form({"name": "Qual"}, room_id="room_absent", source=SOURCE)


def test_a_form_is_created_against_a_real_room(store):
    put_room(store)
    engine = engine_for(store)
    form = engine.create_form({"name": "Qual"}, room_id=ROOM, source=SOURCE, actor="dana")
    assert form["collection"] == FORM_COLLECTION
    assert form["room_id"] == ROOM
    assert form["dimensions"] == list(DIMENSIONS)
    assert form["live"] is False
    assert form["respondents"] == 0
    assert form["created_at_ms"] == NOW_MS


def test_a_form_may_be_updated_without_losing_its_status(store):
    put_room(store)
    engine, form = make_live_form(store)
    updated = engine.update_form(form["id"], {"name": "Renamed"}, source=SOURCE, actor="dana")
    assert updated["name"] == "Renamed"
    assert updated["status"] == v.FORM_STATUS_LIVE
    assert updated["updated_at_ms"] == NOW_MS


def test_a_form_can_only_go_live_on_an_active_room(store):
    put_room(store, status=v.ARCHIVED)
    engine = engine_for(store)
    form = engine.create_form({"name": "Qual"}, room_id=ROOM, source=SOURCE)
    with pytest.raises(CaptureConflict) as caught:
        engine.publish_form(form["id"], source=SOURCE)
    assert caught.value.reason == "room_not_active"
    assert caught.value.as_dict()["room_state"] == v.ARCHIVED


def test_a_form_cannot_go_live_twice(store):
    put_room(store)
    engine, form = make_live_form(store)
    with pytest.raises(CaptureConflict) as caught:
        engine.publish_form(form["id"], source=SOURCE)
    assert caught.value.reason == "already_live"


def test_a_soft_deleted_form_disappears_then_restores(store):
    put_room(store)
    engine = engine_for(store)
    form = engine.create_form({"name": "Qual"}, room_id=ROOM, source=SOURCE)
    removed = engine.soft_delete_form(form["id"], source=SOURCE)
    assert removed["deleted"] is True
    with pytest.raises(CaptureNotFound):
        engine.get_form(form["id"])
    engine.restore_form(form["id"], source=SOURCE)
    assert engine.get_form(form["id"])["id"] == form["id"]


def test_status_of_reads_the_room_lifecycle_vocabulary(store):
    room = put_room(store)
    assert status_of(room) == v.ACTIVE
    archived = put_room(store, room_id="room-2", status=v.ARCHIVED)
    assert status_of(archived) == v.ARCHIVED


def test_the_buyer_form_carries_the_questions_and_no_seller_config(store):
    put_room(store)
    engine, form = make_live_form(store)
    view = engine.room_form(ROOM)
    assert view["id"] == form["id"]
    assert view["visibility"] == v.VISIBILITY_BUYER
    assert "score" not in view and "routing" not in view
    ids = [question["id"] for question in view["questions"]]
    assert ids == list(DIMENSIONS)
    assert all(question["options"] for question in view["questions"])


def test_a_room_with_no_live_form_is_not_found(store):
    put_room(store)
    engine = engine_for(store)
    engine.create_form({"name": "Draft only"}, room_id=ROOM, source=SOURCE)
    with pytest.raises(CaptureNotFound):
        engine.room_form(ROOM)


# --------------------------------------------------------------------------- #
# The engine: submissions, scoring and routing
# --------------------------------------------------------------------------- #


def test_submitting_scores_the_answer_attaches_it_and_routes_it(store):
    put_room(store)
    engine, form = make_live_form(store)
    result = engine.submit(
        ROOM,
        form["id"],
        {"answers": FULL_ANSWERS, "submission_token": "t1"},
        source=SOURCE,
        actor="dana",
    )
    assert result["status"] == v.SUBMISSION_STATUS_SUBMITTED
    assert result["score"] == 12
    assert result["band"] == BAND_QUALIFIED
    assert result["route_target"] == "account_executive"
    assert result["deal_ref"] == "deal_northwind"
    assert [event["event"] for event in result["events"]] == [
        v.EVENT_SUBMITTED,
        v.EVENT_QUALIFIED,
        v.EVENT_ROUTED,
    ]
    # The route and its event are written in the same transaction as the score.
    assert engine.routes(room_id=ROOM)[0]["target"] == "account_executive"


def test_a_submission_missing_a_required_answer_is_refused(store):
    put_room(store)
    engine, form = make_live_form(store)
    with pytest.raises(FormValidationError):
        engine.submit(ROOM, form["id"], {"answers": {"budget": "funded"}}, source=SOURCE)


def test_an_autosave_keeps_a_draft_unscored(store):
    put_room(store)
    engine, form = make_live_form(store)
    draft = engine.autosave(
        ROOM,
        form["id"],
        {"answers": {"budget": "needs_approval"}, "submission_token": "t1"},
        source=SOURCE,
    )
    assert draft["status"] == v.SUBMISSION_STATUS_DRAFT
    assert draft["score"] is None and draft["band"] is None
    assert [event["event"] for event in draft["events"]] == [v.EVENT_AUTOSAVED]


def test_submitting_after_an_autosave_reuses_the_same_row(store):
    put_room(store)
    engine, form = make_live_form(store)
    engine.autosave(
        ROOM,
        form["id"],
        {"answers": {"budget": "approved"}, "submission_token": "t1"},
        source=SOURCE,
    )
    engine.submit(
        ROOM,
        form["id"],
        {"answers": FULL_ANSWERS, "submission_token": "t1"},
        source=SOURCE,
    )
    assert len(store.list(SUBMISSION_COLLECTION)) == 1
    entry = engine.list_submissions(room_id=ROOM)[0]
    assert entry["status"] == v.SUBMISSION_STATUS_SUBMITTED
    assert entry["score"] == 12


def test_two_buyers_in_one_room_keep_separate_drafts(store):
    put_room(store)
    engine, form = make_live_form(store)
    for token in ("a", "b"):
        engine.autosave(
            ROOM,
            form["id"],
            {"answers": {"budget": "approved"}, "submission_token": token},
            source=SOURCE,
        )
    assert len(store.list(SUBMISSION_COLLECTION)) == 2


def test_a_form_cannot_be_submitted_to_unless_it_is_live(store):
    put_room(store)
    engine = engine_for(store)
    form = engine.create_form({"name": "Draft"}, room_id=ROOM, source=SOURCE)
    with pytest.raises(CaptureConflict) as caught:
        engine.submit(ROOM, form["id"], {"answers": FULL_ANSWERS}, source=SOURCE)
    assert caught.value.reason == "form_not_live"


def test_a_submission_for_a_different_room_is_not_found(store):
    put_room(store)
    engine, form = make_live_form(store)
    with pytest.raises(CaptureNotFound):
        engine.submit("room_absent", form["id"], {"answers": FULL_ANSWERS}, source=SOURCE)


def test_submissions_can_be_filtered_by_band(store):
    put_room(store)
    engine, form = make_live_form(store)
    engine.submit(
        ROOM, form["id"], {"answers": FULL_ANSWERS, "submission_token": "hot"}, source=SOURCE
    )
    engine.submit(
        ROOM,
        form["id"],
        {
            "answers": {
                v.DIMENSION_BUDGET: "no_budget",
                v.DIMENSION_TIMELINE: "later",
                v.DIMENSION_AUTHORITY: "unknown",
                v.DIMENSION_PROCUREMENT_PATH: "unknown",
            },
            "submission_token": "cold",
        },
        source=SOURCE,
    )
    qualified = engine.list_submissions(room_id=ROOM, band=BAND_QUALIFIED)
    assert len(qualified) == 1
    assert qualified[0]["score"] == 12


def test_a_draft_cannot_be_routed(store):
    put_room(store)
    engine, form = make_live_form(store)
    draft = engine.autosave(
        ROOM,
        form["id"],
        {"answers": {"budget": "approved"}, "submission_token": "t"},
        source=SOURCE,
    )
    with pytest.raises(CaptureConflict) as caught:
        engine.route_submission(draft["id"], source=SOURCE)
    assert caught.value.reason == "not_submitted"


def test_the_routing_rule_can_be_re_run_on_a_submission(store):
    put_room(store)
    engine, form = make_live_form(store, routing={"qualified": "enterprise_team"})
    submitted = engine.submit(
        ROOM, form["id"], {"answers": FULL_ANSWERS, "submission_token": "t"}, source=SOURCE
    )
    assert submitted["route_target"] == "enterprise_team"
    re_routed = engine.route_submission(submitted["id"], source=SOURCE)
    assert re_routed["route_target"] == "enterprise_team"
    assert re_routed["routed"] is True
    assert len(engine.routes(room_id=ROOM)) == 2


def test_the_seller_summary_counts_bands_and_routing(store):
    put_room(store)
    engine, form = make_live_form(store)
    engine.submit(
        ROOM, form["id"], {"answers": FULL_ANSWERS, "submission_token": "hot"}, source=SOURCE
    )
    engine.autosave(
        ROOM,
        form["id"],
        {"answers": {"budget": "approved"}, "submission_token": "d"},
        source=SOURCE,
    )
    summary = engine.qualification_summary(ROOM)
    assert summary["visibility"] == v.VISIBILITY_SELLER_ONLY
    assert summary["seller_only"] is True
    assert summary["submissions"] == 1
    assert summary["in_progress"] == 1
    assert summary["qualified"] == 1
    assert summary["by_band"][BAND_QUALIFIED] == 1
    assert summary["routing"] == {"account_executive": 1}


# --------------------------------------------------------------------------- #
# Visibility: the buyer view never carries a score or a band
# --------------------------------------------------------------------------- #


def test_the_buyer_view_strips_every_seller_only_field(store):
    put_room(store)
    engine, form = make_live_form(store)
    submitted = engine.submit(
        ROOM, form["id"], {"answers": FULL_ANSWERS, "submission_token": "t"}, source=SOURCE
    )
    buyer = engine.buyer_view(submitted)
    for hidden in SELLER_ONLY_FIELDS:
        assert hidden not in buyer, hidden
    assert buyer["visibility"] == v.VISIBILITY_BUYER
    assert buyer["answers"] == FULL_ANSWERS
    assert buyer["status"] == v.SUBMISSION_STATUS_SUBMITTED


def test_the_buyer_view_reads_a_raw_record_too(store):
    """The routes hand it a seller view; a caller may hand it a raw record."""
    put_room(store)
    engine, form = make_live_form(store)
    engine.submit(
        ROOM, form["id"], {"answers": FULL_ANSWERS, "submission_token": "t"}, source=SOURCE
    )
    record = store.list(SUBMISSION_COLLECTION)[0]
    buyer = engine.buyer_view(record)
    assert "score" not in buyer and "band" not in buyer
    assert buyer["answers"] == FULL_ANSWERS


# --------------------------------------------------------------------------- #
# Reused vocabulary: WF-015's visitor session and dsr.room_lifecycle
# --------------------------------------------------------------------------- #


def test_a_submission_may_name_a_visitor_session_from_wf015(store):
    put_room(store)
    put_visitor(store, "sess-1", status=v.STATUS_IDENTIFIED)
    engine, form = make_live_form(store)
    submitted = engine.submit(
        ROOM,
        form["id"],
        {"answers": FULL_ANSWERS, v.VISITOR_SESSION_REF: "sess-1"},
        source=SOURCE,
    )
    assert submitted["visitor_session_ref"] == "sess-1"


def test_a_visitor_session_in_another_room_is_a_conflict(store):
    put_room(store)
    put_room(store, room_id="room-2")
    put_visitor(store, "sess-2", room_id="room-2")
    engine, form = make_live_form(store)
    with pytest.raises(CaptureConflict) as caught:
        engine.submit(
            ROOM,
            form["id"],
            {"answers": FULL_ANSWERS, v.VISITOR_SESSION_REF: "sess-2"},
            source=SOURCE,
        )
    assert caught.value.reason == "visitor_session_room_mismatch"


def test_a_visitor_session_that_is_not_identified_is_a_conflict(store):
    put_room(store)
    put_visitor(store, "sess-3", status="pending_verification")
    engine, form = make_live_form(store)
    with pytest.raises(CaptureConflict) as caught:
        engine.submit(
            ROOM,
            form["id"],
            {"answers": FULL_ANSWERS, v.VISITOR_SESSION_REF: "sess-3"},
            source=SOURCE,
        )
    assert caught.value.reason == "visitor_not_identified"


def test_an_unknown_visitor_session_is_not_found(store):
    put_room(store)
    engine, form = make_live_form(store)
    with pytest.raises(CaptureNotFound) as caught:
        engine.submit(
            ROOM,
            form["id"],
            {"answers": FULL_ANSWERS, v.VISITOR_SESSION_REF: "sess_absent"},
            source=SOURCE,
        )
    assert caught.value.collection == "visitor_session"


def test_an_anonymous_submission_is_allowed(store):
    """The room is already behind WF-015's gate, so a buyer need not re-prove identity."""
    put_room(store)
    engine, form = make_live_form(store)
    submitted = engine.submit(ROOM, form["id"], {"answers": FULL_ANSWERS}, source=SOURCE)
    assert submitted["visitor_session_ref"] is None
    assert submitted["visitor_key"] == "anonymous"


# --------------------------------------------------------------------------- #
# The refusals
# --------------------------------------------------------------------------- #


def test_each_error_carries_a_stable_code_and_status():
    assert FormValidationError({"x": "bad"}).status_code == 422
    assert CaptureNotFound("x").status_code == 404
    assert CaptureConflict("why", "detail").status_code == 409
    assert FormValidationError({"x": "bad"}).code == "form_invalid"
    assert CaptureNotFound("x", collection="room").as_dict()["collection"] == "room"
    assert CaptureConflict("why", "detail").as_dict()["reason"] == "why"


# --------------------------------------------------------------------------- #
# Inferences
# --------------------------------------------------------------------------- #


def test_every_inference_names_its_basis_and_options():
    entries = describe_inferences()
    assert len(entries) >= 6
    for entry in entries:
        assert entry["basis"] in {"sourced", "inferred"}
        assert entry["options"] and entry["choice"] in entry["options"]
    assert by_id("band_thresholds")["choice"] == "ratios_of_the_reachable_maximum"
    assert by_id("does_not_exist") is None
    # describe returns copies, so a caller cannot mutate the record.
    entries[0]["choice"] = "tampered"
    assert by_id(entries[0]["id"])["choice"] != "tampered"


# --------------------------------------------------------------------------- #
# The demo data
# --------------------------------------------------------------------------- #


def test_the_seed_writes_a_form_three_submissions_and_a_draft(memory_db):
    from dsr.features import wf123_in_room_qualification_capture as feature

    store = RecordStore(memory_db)
    room = put_room(store)

    message = feature.seed(
        memory_db, {"room_ids": [(room["id"], "Northwind")], "now": NOW, "rng": None}
    )

    assert message.startswith("WF-123 seeded")
    # Every character is printable on a Windows console.
    assert message.encode("cp1252")

    engine = engine_for(store)
    forms = engine.list_forms(room_id=room["id"])
    assert len(forms) == 1 and forms[0]["live"] is True
    summary = engine.qualification_summary(room["id"])
    assert summary["submissions"] == 3
    assert summary["in_progress"] == 1
    assert summary["by_band"] == {
        BAND_QUALIFIED: 1,
        BAND_DEVELOPING: 1,
        BAND_UNQUALIFIED: 1,
    }


def test_the_seed_says_so_when_there_is_no_room(memory_db):
    from dsr.features import wf123_in_room_qualification_capture as feature

    message = feature.seed(memory_db, {"room_ids": [], "now": NOW, "rng": None})
    assert message == "WF-123: no room available, seeded nothing"
    assert message.encode("cp1252")
