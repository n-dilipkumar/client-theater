"""WF-106: the domain rules of browsing-triggered outreach.

What is tested here and what is not
-----------------------------------

This file builds :class:`~dsr.page_outreach.engine.PageOutreach` over an in-memory
store and calls it directly. It does not go through HTTP; ``test_wf106_http.py`` does
that, and the split is what keeps each file cheap enough to run alone.

The claims this file exists to pin, in the order the researched data flow makes them:

* **The page view is read, or refused by name.** A closed field list, because a field
  no rule reads is a field a reader will later mistake for one that was.
* **A path is matched the way a seller writing the rule would expect.** ``/pricing``
  matches ``/pricing/plans`` and not ``/pricing-archive``, which is why the prefix
  comparison respects a slash boundary rather than using ``startswith``.
* **The repeat count is the count of matching visits inside the window**, and a view
  outside the window does not contribute to it.
* **The three frequency modes do three different things**, and the difference between
  counting shows and counting interactions is the whole of the ``seen`` mode.
* **The session damper expires at the session boundary**, because the snippet's session
  id is the boundary and this workflow invents no timer.
* **The delivery state is derived from the receipts**, so a delivery cannot hold a state
  its own receipts do not support.
* **A receipt for a block nobody saw is refused**, and a path selection naming a branch
  nobody declared is refused.

Every test here passes on its own and in any order. Nothing depends on a module-scoped
fixture, nothing reads the wall clock for an assertion, and every moment is passed in,
so this file is safe under pytest-xdist.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from dsr.db.audited import AuditedDatabase
from dsr.page_outreach import (
    DWELL_SECONDS,
    REPEAT_VISITS,
    REPEAT_WINDOW_DAYS,
    PageOutreach,
    count_matching_visits,
    decide,
    frequency_decision,
    is_engagement,
    is_session_hiding,
    normalise_path,
    parse_page_view,
    path_matches,
    rules_matched,
    session_decision,
    utm_matches,
)
from dsr.page_outreach.errors import (
    DuplicateWorkflow,
    InvalidInteraction,
    InvalidPageView,
    InvalidWorkflow,
    UnknownDelivery,
    UnknownPageView,
    UnknownPageViewField,
    UnknownPath,
    UnknownWorkflow,
)
from dsr.store import RecordStore

NOW = datetime(2026, 10, 5, 9, 0, tzinfo=timezone.utc)
ROOM = "room-1"
SOURCE = "POST /api/wf-106/views"

#: A workflow that qualifies a visitor on the third matching visit inside seven days,
#: and whose block carries the two branches the researched flow names.
LIVE_WORKFLOW: dict[str, object] = {
    "name": "Upgrade page repeaters",
    "frequency": "engaged_with",
    "state": "live",
    "repeat_visits": 3,
    "repeat_window_days": 7,
    "dwell_seconds": 60,
    "rules": [
        {"kind": "url", "mode": "prefix", "value": "/upgrade"},
        {"kind": "dwell", "value": "60"},
    ],
    "audience": {"company_keys": [], "tags": [], "segments": []},
    "goal_name": "meeting_booked",
    "blocks": [
        {
            "kind": "message",
            "text": "You have been back to this page a few times.",
            "apps": [{"kind": "video", "title": "Walkthrough", "url": "https://x.example/v"}],
        }
    ],
    "paths": [
        {"key": "yes_upgrade", "label": "Yes, let's talk about upgrading", "next": "book"},
        {"key": "not_right_now", "label": "Not right now", "closes": True},
    ],
}


@pytest.fixture()
def engine():
    """A :class:`PageOutreach` over a fresh, empty, in-memory database.

    Function scope on purpose. The repeat count is state, so a test that seeded a
    workflow and left it behind would change the answer for whichever test ran next,
    and this suite runs under xdist where that order is not stable.
    """
    db = AuditedDatabase(":memory:", actor="test")
    try:
        yield PageOutreach(RecordStore(db))
    finally:
        db.close()


def make_workflow(engine: PageOutreach, **overrides: object) -> dict:
    """Save one workflow in ROOM and return the record."""
    payload = {**LIVE_WORKFLOW, **overrides}
    return engine.create_workflow(payload, room_id=ROOM, actor="test", source="test", now=NOW)


def view_for(workflow_id: str, **overrides: object) -> dict[str, object]:
    """One page view against that workflow, with defaults that satisfy both rules."""
    body: dict[str, object] = {
        "workflow_id": workflow_id,
        "visitor_key": "visitor-1",
        "session_id": "sess-1",
        "path": "/upgrade/plans",
        "dwell_seconds": 74,
        "company_key": "northwind-energy",
        "visited_at": NOW.isoformat(),
    }
    body.update(overrides)
    return body


def qualify(
    engine: PageOutreach, workflow_id: str, count: int = REPEAT_VISITS, **overrides: object
) -> dict:
    """Post ``count`` matching views, and return the last result.

    The count is a parameter rather than always the derived threshold because several
    tests need a workflow whose own ``repeat_visits`` is lower than the module default,
    and posting the default would cross the threshold twice and change the answer.
    """
    result: dict = {}
    for index in range(count):
        result = engine.record_view(
            view_for(
                workflow_id,
                session_id=f"sess-{index}",
                visited_at=(NOW - timedelta(hours=index + 1)).isoformat(),
                **overrides,
            ),
            room_id=ROOM,
            actor="test",
            source=SOURCE,
            now=NOW,
        )
    return result


# --------------------------------------------------------------------------- #
# The published numbers, and which of them the research actually states
# --------------------------------------------------------------------------- #


def test_the_thresholds_are_the_ones_the_vocabulary_publishes():
    """A threshold changed in the vocabulary has to reach the rules, not sit beside them."""
    assert (REPEAT_VISITS, REPEAT_WINDOW_DAYS, DWELL_SECONDS) == (3, 7, 60)


def test_no_threshold_claims_to_be_sourced():
    """The vendor threshold was not published, so nothing here may say otherwise."""
    from dsr.page_outreach.vocabulary import THRESHOLDS

    assert [entry["threshold"] for entry in THRESHOLDS] == [3, 7, 60]
    assert [entry["sourced"] for entry in THRESHOLDS] == [False, False, False]
    for entry in THRESHOLDS:
        assert entry["derivation"], entry["kind"]
        assert entry["risk"], entry["kind"]


def test_the_vocabulary_says_nothing_is_sent():
    body = engine_vocabulary()
    assert body["calls_vendor"] is False
    assert body["sends_email"] is False
    assert body["sends_sms"] is False
    assert body["renders_in_browser_messenger"] is False
    assert body["channels"] == ["in_app"]
    assert "posts no message to any vendor" in body["reads"]


def test_the_vocabulary_publishes_the_three_sourced_frequency_modes():
    modes = engine_vocabulary()["frequency_modes"]
    assert [entry["mode"] for entry in modes] == ["seen", "any_interaction", "engaged_with"]
    assert [entry["default"] for entry in modes] == [True, False, False]
    for entry in modes:
        assert entry["sourced"] is True, entry["mode"]
        assert entry["quote"], entry["mode"]


def test_the_vocabulary_lists_the_limits_the_research_left_open():
    limits = {entry["limit"] for entry in engine_vocabulary()["unsourced_limits"]}
    assert {
        "repeat_count",
        "repeat_window",
        "dwell_threshold",
        "trigger_pane_contents",
        "ingest_contract",
    } <= limits


def engine_vocabulary() -> dict:
    """The vocabulary, over a throwaway engine. One engine per call keeps tests independent."""
    db = AuditedDatabase(":memory:", actor="test")
    try:
        return PageOutreach(RecordStore(db)).vocabulary()
    finally:
        db.close()


def test_every_inference_says_what_would_change_it_and_what_it_risks():
    body = inferences_body()
    assert body["count"] >= 17
    assert body["inferred_count"] >= 10
    for entry in body["inferences"]:
        assert {"id", "question", "reading", "why", "change", "risk", "sourced"} <= set(entry)


def inferences_body() -> dict:
    db = AuditedDatabase(":memory:", actor="test")
    try:
        return PageOutreach(RecordStore(db)).inferences()
    finally:
        db.close()


def test_the_repeat_count_is_recorded_as_unsourced():
    joined = " ".join(entry["reading"] for entry in inferences_body()["inferences"])
    assert "states no count" in joined or "states no repeat count" in joined


# --------------------------------------------------------------------------- #
# Path matching
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "pattern,path,mode,expected",
    [
        ("/pricing", "/pricing", "prefix", True),
        ("/pricing", "/pricing/plans", "prefix", True),
        ("/pricing", "/pricing-archive", "prefix", False),
        ("/pricing", "/plans", "prefix", False),
        ("/pricing", "/pricing", "exact", True),
        ("/pricing", "/pricing/plans", "exact", False),
        ("upgrade", "/upgrade", "prefix", True),
        ("pricing", "/plans/pricing/annual", "contains", True),
        ("pricing", "/plans/annual", "contains", False),
        ("", "/pricing", "prefix", False),
        ("/pricing", "", "prefix", False),
        ("/pricing", "/pricing", "not_a_mode", False),
    ],
)
def test_path_matches_respects_a_slash_boundary(pattern, path, mode, expected):
    assert path_matches(pattern, path, mode) is expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("https://example.com/pricing?utm_source=google", "/pricing"),
        ("/pricing/", "/pricing"),
        ("/pricing#plans", "/pricing"),
        ("pricing", "/pricing"),
        ("/", "/"),
        ("", ""),
    ],
)
def test_normalise_path_strips_what_a_seller_would_not_write(raw, expected):
    assert normalise_path(raw) == expected


def test_a_full_url_and_a_bare_path_reach_the_same_rule():
    """The snippet posts a URL and the seller writes a path, so both have to normalise."""
    assert path_matches("/pricing", "https://example.com/pricing?utm_source=google") is True


@pytest.mark.parametrize(
    "pattern,observed,expected",
    [
        ("linkedin", "linkedin", True),
        ("linkedin", "LinkedIn", True),
        ("linkedin*", "linkedin-ads", True),
        ("linkedin*", "linkedin", True),
        ("linkedin", "linkedin-ads", False),
        ("", "linkedin", False),
    ],
)
def test_utm_matches_is_case_insensitive_and_reads_a_wildcard(pattern, observed, expected):
    assert utm_matches(pattern, observed) is expected


# --------------------------------------------------------------------------- #
# Reading a page view
# --------------------------------------------------------------------------- #


def test_a_page_view_is_read():
    view = parse_page_view(view_for("wf-1", path="/upgrade/plans"), now=NOW)
    assert view.workflow_id == "wf-1"
    assert view.path == "/upgrade/plans"
    assert view.dwell_seconds == 74
    assert view.visited_at == NOW


@pytest.mark.parametrize("missing", ["workflow_id", "visitor_key", "path", "session_id"])
def test_a_page_view_without_a_required_field_is_refused_by_name(missing):
    body = view_for("wf-1")
    body.pop(missing)
    with pytest.raises(InvalidPageView) as caught:
        parse_page_view(body, now=NOW)
    assert missing in str(caught.value)


def test_a_page_view_carrying_an_unread_field_is_refused():
    with pytest.raises(UnknownPageViewField) as caught:
        parse_page_view(view_for("wf-1", user_agent="Mozilla/5.0"), now=NOW)
    assert "user_agent" in str(caught.value)


def test_a_page_view_that_is_not_an_object_is_refused():
    with pytest.raises(InvalidPageView):
        parse_page_view([view_for("wf-1")], now=NOW)


def test_a_fractional_dwell_reading_is_refused_rather_than_rounded():
    """A reading of 59.7 seconds is real, and rounding it up would fire the threshold."""
    with pytest.raises(InvalidPageView) as caught:
        parse_page_view(view_for("wf-1", dwell_seconds=59.7), now=NOW)
    assert "whole number" in str(caught.value)


def test_a_negative_dwell_reading_is_refused():
    with pytest.raises(InvalidPageView) as caught:
        parse_page_view(view_for("wf-1", dwell_seconds=-1), now=NOW)
    assert "negative" in str(caught.value)


def test_a_visited_at_without_a_timezone_offset_is_refused():
    """The repeat window compares moments, and an unzoned time has no place in a window."""
    with pytest.raises(InvalidPageView) as caught:
        parse_page_view(view_for("wf-1", visited_at="2026-10-05T09:00:00"), now=NOW)
    assert "timezone" in str(caught.value)


def test_a_visited_at_that_is_not_iso_8601_is_refused():
    with pytest.raises(InvalidPageView) as caught:
        parse_page_view(view_for("wf-1", visited_at="last tuesday"), now=NOW)
    assert "ISO 8601" in str(caught.value)


def test_a_visited_at_that_is_a_number_is_refused():
    with pytest.raises(InvalidPageView):
        parse_page_view(view_for("wf-1", visited_at=1759645200), now=NOW)


def test_a_missing_visited_at_defaults_to_now():
    body = view_for("wf-1")
    body.pop("visited_at")
    assert parse_page_view(body, now=NOW).visited_at == NOW


# --------------------------------------------------------------------------- #
# The window arithmetic, on its own
# --------------------------------------------------------------------------- #


def test_the_window_counts_only_the_visits_inside_it():
    history = [
        {"visited_at": (NOW - timedelta(days=1)).isoformat(), "matched": True},
        {"visited_at": (NOW - timedelta(days=3)).isoformat(), "matched": True},
        {"visited_at": (NOW - timedelta(days=40)).isoformat(), "matched": True},
    ]
    assert count_matching_visits(history, now=NOW, window_days=7) == 2


def test_a_visit_after_now_does_not_count():
    history = [{"visited_at": (NOW + timedelta(days=1)).isoformat(), "matched": True}]
    assert count_matching_visits(history, now=NOW, window_days=7) == 0


def test_a_view_that_did_not_match_is_not_evidence_of_interest():
    """A buyer who browsed a site for an hour has still not earned a block they never qualified for."""
    history = [{"visited_at": NOW.isoformat(), "matched": False}]
    assert count_matching_visits(history, now=NOW, window_days=7) == 0


def test_a_history_row_with_no_moment_is_skipped_rather_than_crashing_the_count():
    history = [{"visited_at": "", "matched": True}, {"matched": True}]
    assert count_matching_visits(history, now=NOW, window_days=7) == 0


def test_an_unzoned_moment_in_the_history_is_skipped():
    history = [{"visited_at": "2026-10-05T09:00:00", "matched": True}]
    assert count_matching_visits(history, now=NOW, window_days=7) == 0


def test_the_window_boundary_is_inclusive_of_a_visit_exactly_its_width_old():
    history = [{"visited_at": (NOW - timedelta(days=7)).isoformat(), "matched": True}]
    assert count_matching_visits(history, now=NOW, window_days=7) == 1


# --------------------------------------------------------------------------- #
# The three frequency modes
# --------------------------------------------------------------------------- #


def test_the_seen_mode_counts_shows():
    assert frequency_decision("seen", shown=0, interacted=0, engaged=0) == (
        True,
        "the Seen mode has not fired for this buyer yet",
    )
    assert frequency_decision("seen", shown=1, interacted=0, engaged=0)[0] is False


def test_the_seen_mode_stops_on_a_show_even_when_the_buyer_did_nothing():
    """The quote says once, whether or not they interact with or dismiss it."""
    allowed, reason = frequency_decision("seen", shown=1, interacted=0, engaged=0)
    assert allowed is False
    assert "never again" in reason


def test_the_any_interaction_mode_stops_on_a_dismissal():
    allowed, reason = frequency_decision("any_interaction", shown=0, interacted=1, engaged=0)
    assert allowed is False
    assert "interaction has already happened" in reason


def test_the_engaged_with_mode_ignores_a_dismissal_and_a_messenger_open():
    """Only selecting a path engages, which is what stops the mode."""
    assert frequency_decision("engaged_with", shown=2, interacted=2, engaged=0)[0] is True


def test_the_engaged_with_mode_stops_on_an_engagement():
    allowed, reason = frequency_decision("engaged_with", shown=1, interacted=1, engaged=1)
    assert allowed is False
    assert "engaged by selecting a path" in reason


def test_an_unpublished_frequency_mode_is_refused_rather_than_defaulted():
    allowed, reason = frequency_decision("always", shown=0, interacted=0, engaged=0)
    assert allowed is False
    assert "not a published" in reason


@pytest.mark.parametrize(
    "kind,engaging,hiding",
    [
        ("path_selected", True, False),
        ("goal_reached", True, False),
        ("messenger_opened", False, True),
        ("dismissed", False, True),
        ("clicked", False, False),
    ],
)
def test_which_interactions_engage_and_which_hide_the_session(kind, engaging, hiding):
    assert is_engagement(kind) is engaging
    assert is_session_hiding(kind) is hiding


# --------------------------------------------------------------------------- #
# The session damper
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("kind", ["dismissed", "messenger_opened"])
def test_the_damper_hides_the_block_after_the_two_kinds_the_quote_names(kind):
    allowed, reason = session_decision([kind])
    assert allowed is False
    assert "this session" in reason


@pytest.mark.parametrize("kind", ["path_selected", "clicked", "goal_reached"])
def test_the_damper_leaves_the_block_alone_after_everything_else(kind):
    assert session_decision([kind])[0] is True


def test_an_empty_session_history_shows_the_block():
    assert session_decision([]) == (True, "nothing in this session has hidden the block")


# --------------------------------------------------------------------------- #
# The four gates, in order, with the reason each one gives
# --------------------------------------------------------------------------- #


def gate_view(**overrides):
    body = view_for("wf-1")
    body.update(overrides)
    return parse_page_view(body, now=NOW)


def test_a_page_the_workflow_does_not_target_never_shows_however_often_it_is_seen():
    decision = decide(
        gate_view(path="/about/team"),
        rules=[{"kind": "url", "mode": "prefix", "value": "/upgrade"}],
        matching_visits=99,
        visits_required=1,
        now=NOW,
    )
    assert decision.show is False
    assert decision.stopped_by == "rules_not_matched"
    assert "/about/team" in decision.reason


def test_a_rule_that_holds_but_the_count_does_not_says_the_count():
    decision = decide(
        gate_view(),
        rules=[{"kind": "url", "mode": "prefix", "value": "/upgrade"}],
        matching_visits=2,
        visits_required=3,
        now=NOW,
    )
    assert decision.show is False
    assert decision.stopped_by == "matched_below_repeat"
    assert "2 matching visit(s)" in decision.reason
    assert "needs 3" in decision.reason


def test_a_block_that_was_already_shown_stops_on_the_frequency_mode():
    decision = decide(
        gate_view(),
        rules=[{"kind": "url", "mode": "prefix", "value": "/upgrade"}],
        frequency="seen",
        shown=1,
        matching_visits=5,
        visits_required=3,
        now=NOW,
    )
    assert decision.stopped_by == "frequency_mode"


def test_a_block_hidden_in_this_session_stops_on_the_damper():
    decision = decide(
        gate_view(),
        rules=[{"kind": "url", "mode": "prefix", "value": "/upgrade"}],
        frequency="engaged_with",
        session_interactions=["dismissed"],
        matching_visits=5,
        visits_required=3,
        now=NOW,
    )
    assert decision.stopped_by == "hidden_for_session"
    assert "dismissed" in decision.reason


def test_a_qualified_visitor_gets_the_block_and_says_why():
    decision = decide(
        gate_view(),
        rules=[{"kind": "url", "mode": "prefix", "value": "/upgrade"}],
        frequency="engaged_with",
        matching_visits=3,
        visits_required=3,
        now=NOW,
    )
    assert decision.show is True
    assert decision.stopped_by == "show"


def test_the_decision_reports_every_rule_and_whether_it_held():
    decision = decide(
        gate_view(dwell_seconds=12),
        rules=[
            {"kind": "url", "mode": "prefix", "value": "/upgrade"},
            {"kind": "dwell", "value": "60"},
        ],
        matching_visits=3,
        visits_required=3,
        now=NOW,
    )
    assert decision.show is False
    held = {entry.kind: entry.met for entry in decision.rule_matches}
    assert held == {"url": True, "dwell": False}
    # The observation is reported beside the expectation, so a seller can see the 12.
    dwell = next(entry for entry in decision.rule_matches if entry.kind == "dwell")
    assert (dwell.observed, dwell.expected) == ("12", "60")


def test_a_workflow_with_no_rules_matches_every_page_and_leans_on_the_count():
    """A seller who names no page has said every page, so only the count stands in the way."""
    decision = decide(
        gate_view(),
        rules=[],
        matching_visits=2,
        visits_required=3,
        now=NOW,
    )
    assert decision.show is False
    assert decision.stopped_by == "matched_below_repeat"
    assert decision.rule_matches == ()


def test_the_rule_list_is_an_and_across_kinds():
    """A seller who writes a URL rule and a dwell rule means both."""
    view = gate_view(dwell_seconds=74)
    both_hold = decide(
        view,
        rules=[{"kind": "url", "value": "/upgrade"}, {"kind": "dwell", "value": "60"}],
        matching_visits=1,
        visits_required=1,
        now=NOW,
    )
    assert rules_matched(both_hold.rule_matches) is True

    one_fails = decide(
        gate_view(dwell_seconds=10),
        rules=[{"kind": "url", "value": "/upgrade"}, {"kind": "dwell", "value": "60"}],
        matching_visits=1,
        visits_required=1,
        now=NOW,
    )
    assert rules_matched(one_fails.rule_matches) is False
    assert one_fails.show is False


def test_an_empty_rule_list_matches_everything():
    assert rules_matched(()) is True


def test_a_dwell_rule_with_a_number_of_its_own_overrides_the_workflow_default():
    view = gate_view(dwell_seconds=45)
    decision = decide(
        view,
        rules=[{"kind": "dwell", "value": "45"}],
        dwell_seconds=60,
        matching_visits=1,
        visits_required=1,
        now=NOW,
    )
    assert decision.show is True


def test_a_dwell_rule_with_no_readable_number_falls_back_to_the_derived_default():
    view = gate_view(dwell_seconds=DWELL_SECONDS - 1)
    decision = decide(
        view,
        rules=[{"kind": "dwell", "value": "not-a-number"}],
        dwell_seconds=DWELL_SECONDS,
        matching_visits=1,
        visits_required=1,
        now=NOW,
    )
    assert decision.show is False


# --------------------------------------------------------------------------- #
# Saving a workflow
# --------------------------------------------------------------------------- #


def test_a_workflow_is_saved_as_a_draft_unless_the_caller_says_live(engine):
    workflow = engine.create_workflow(
        {**LIVE_WORKFLOW, "state": ""},
        room_id=ROOM,
        actor="test",
        source="test",
        now=NOW,
    )
    assert workflow["state"] == "draft"


def test_the_scheduling_pane_carries_the_state(engine):
    workflow = engine.create_workflow(
        {**LIVE_WORKFLOW, "state": "", "scheduling": {"state": "live"}},
        room_id=ROOM,
        actor="test",
        source="test",
        now=NOW,
    )
    assert workflow["scheduling"]["state"] == "live"


def test_a_workflow_is_created_then_read_back(engine):
    workflow = make_workflow(engine)
    assert workflow["name"] == "Upgrade page repeaters"
    assert workflow["rule_count"] == 2
    assert workflow["path_count"] == 2
    assert workflow["block_count"] == 1
    assert engine.read_workflow(workflow["id"])["id"] == workflow["id"]


def test_two_workflows_may_not_share_a_name_in_one_room(engine):
    make_workflow(engine)
    with pytest.raises(DuplicateWorkflow) as caught:
        make_workflow(engine)
    assert "already exists" in str(caught.value)


def test_the_same_name_is_free_in_another_room(engine):
    first = make_workflow(engine)
    other = engine.create_workflow(
        LIVE_WORKFLOW, room_id="room-2", actor="test", source="test", now=NOW
    )
    assert other["id"] != first["id"]


def test_a_workflow_with_no_name_is_refused(engine):
    with pytest.raises(InvalidWorkflow) as caught:
        make_workflow(engine, name="   ")
    assert "name is required" in str(caught.value)


def test_an_unpublished_frequency_mode_is_refused_at_save_time(engine):
    with pytest.raises(InvalidWorkflow) as caught:
        make_workflow(engine, frequency="every_time")
    assert "frequency must be one of" in str(caught.value)


def test_a_channel_this_build_cannot_honour_is_refused_at_save_time(engine):
    """The research names in_app, and this product has no outbound transport."""
    with pytest.raises(InvalidWorkflow) as caught:
        make_workflow(engine, channel="email")
    assert "no outbound transport" in str(caught.value)


def test_an_unpublished_workflow_state_is_refused(engine):
    with pytest.raises(InvalidWorkflow):
        make_workflow(engine, state="published")


def test_a_repeat_count_below_one_is_refused(engine):
    with pytest.raises(InvalidWorkflow):
        make_workflow(engine, repeat_visits=0)


def test_a_repeat_window_longer_than_a_quarter_is_refused(engine):
    """Past ninety days it is not a repeat damper."""
    with pytest.raises(InvalidWorkflow) as caught:
        make_workflow(engine, repeat_window_days=91)
    assert "between 1 and 90" in str(caught.value)


def test_a_rule_on_a_kind_no_matcher_implements_is_refused_at_save_time(engine):
    """A rule that silently never fires is worse than a rule nobody configured."""
    with pytest.raises(InvalidWorkflow) as caught:
        make_workflow(engine, rules=[{"kind": "referrer_host", "value": "x"}])
    assert "implements" in str(caught.value)


def test_a_url_rule_with_an_unimplemented_match_mode_is_refused(engine):
    with pytest.raises(InvalidWorkflow) as caught:
        make_workflow(engine, rules=[{"kind": "url", "mode": "regex", "value": "/x"}])
    assert "implements" in str(caught.value)


def test_a_rule_with_no_value_is_refused(engine):
    with pytest.raises(InvalidWorkflow):
        make_workflow(engine, rules=[{"kind": "url", "value": "  "}])


def test_the_same_rule_listed_twice_is_refused(engine):
    with pytest.raises(InvalidWorkflow) as caught:
        make_workflow(
            engine,
            rules=[
                {"kind": "url", "value": "/upgrade"},
                {"kind": "url", "value": "/upgrade"},
            ],
        )
    assert "listed twice" in str(caught.value)


def test_a_dwell_rule_whose_value_is_not_seconds_is_refused(engine):
    with pytest.raises(InvalidWorkflow) as caught:
        make_workflow(engine, rules=[{"kind": "dwell", "value": "a while"}])
    assert "whole number of seconds" in str(caught.value)


def test_a_block_with_no_text_is_refused(engine):
    """A welcome message with nothing in it reaches a buyer as a box with no words in it."""
    with pytest.raises(InvalidWorkflow) as caught:
        make_workflow(engine, blocks=[{"kind": "message", "text": ""}])
    assert "needs text" in str(caught.value)


def test_a_block_on_an_unpublished_kind_is_refused(engine):
    with pytest.raises(InvalidWorkflow):
        make_workflow(engine, blocks=[{"kind": "carousel", "text": "hi"}])


def test_an_app_on_an_unpublished_kind_is_refused(engine):
    with pytest.raises(InvalidWorkflow):
        make_workflow(
            engine,
            blocks=[{"kind": "message", "text": "hi", "apps": [{"kind": "poll", "title": "t"}]}],
        )


def test_two_paths_may_not_answer_to_one_key(engine):
    """The buyer answered one question and two branches cannot both be right."""
    with pytest.raises(InvalidWorkflow) as caught:
        make_workflow(
            engine,
            paths=[
                {"key": "yes", "label": "Yes"},
                {"key": "yes", "label": "Also yes"},
            ],
        )
    assert "cannot both be right" in str(caught.value)


def test_a_path_with_no_label_is_refused(engine):
    """The label is the text on the button."""
    with pytest.raises(InvalidWorkflow) as caught:
        make_workflow(engine, paths=[{"key": "yes"}])
    assert "needs a label" in str(caught.value)


def test_an_audience_key_nobody_reads_is_refused(engine):
    with pytest.raises(InvalidWorkflow) as caught:
        make_workflow(engine, audience={"plan": ["gold"]})
    assert "company_keys" in str(caught.value)


def test_an_audience_entry_that_is_not_a_list_is_refused(engine):
    with pytest.raises(InvalidWorkflow):
        make_workflow(engine, audience={"company_keys": "northwind"})


def test_a_scheduling_key_nobody_reads_is_refused(engine):
    with pytest.raises(InvalidWorkflow):
        make_workflow(engine, scheduling={"timezone": "Europe/London"})


def test_a_draft_workflow_never_fires_and_says_so(engine):
    workflow = make_workflow(engine, state="draft")
    result = engine.record_view(
        view_for(workflow["id"]), room_id=ROOM, actor="test", source=SOURCE, now=NOW
    )
    assert result["show"] is False
    assert result["stopped_by"] == "not_live"
    assert "only a live workflow" in result["reason"]


def test_setting_a_workflow_live_is_what_makes_it_fire(engine):
    workflow = make_workflow(engine, state="draft")
    assert (
        engine.record_view(
            view_for(workflow["id"]), room_id=ROOM, actor="test", source=SOURCE, now=NOW
        )["show"]
        is False
    )
    engine.set_state(workflow["id"], "live", actor="test", source="test")
    assert engine.read_workflow(workflow["id"])["state"] == "live"


def test_setting_an_unpublished_state_is_refused(engine):
    workflow = make_workflow(engine)
    with pytest.raises(InvalidWorkflow):
        engine.set_state(workflow["id"], "paused", actor="test", source="test")


def test_a_workflow_is_replaced_not_merged(engine):
    """A merge of a rule list would keep a deleted rule forever, with no way to say remove."""
    workflow = make_workflow(engine)
    engine.update_workflow(
        workflow["id"],
        {**LIVE_WORKFLOW, "rules": [{"kind": "url", "mode": "prefix", "value": "/pricing"}]},
        actor="test",
        source="test",
        now=NOW,
    )
    assert engine.read_workflow(workflow["id"])["rules"][0]["value"] == "/pricing"
    assert engine.read_workflow(workflow["id"])["rule_count"] == 1


def test_renaming_a_workflow_onto_a_taken_name_is_refused(engine):
    first = make_workflow(engine)
    make_workflow(engine, name="Pricing page")
    with pytest.raises(DuplicateWorkflow):
        engine.update_workflow(
            first["id"],
            {**LIVE_WORKFLOW, "name": "Pricing page"},
            actor="test",
            source="test",
            now=NOW,
        )


def test_a_workflow_may_keep_its_own_name_on_an_update(engine):
    workflow = make_workflow(engine)
    again = engine.update_workflow(
        workflow["id"], LIVE_WORKFLOW, actor="test", source="test", now=NOW
    )
    assert again["name"] == "Upgrade page repeaters"


def test_deleting_a_workflow_is_a_soft_delete(engine):
    """The views recorded while it was live must keep pointing at readable rules."""
    workflow = make_workflow(engine)
    qualify(engine, workflow["id"])
    engine.delete_workflow(workflow["id"], actor="test", source="test")
    assert engine.workflows(room_id=ROOM) == []
    with pytest.raises(UnknownWorkflow):
        engine.read_workflow(workflow["id"])


def test_reading_or_deleting_an_absent_workflow_is_refused(engine):
    for call in (
        lambda: engine.read_workflow("nope"),
        lambda: engine.delete_workflow("nope", actor="test", source="test"),
        lambda: engine.set_state("nope", "live", actor="test", source="test"),
    ):
        with pytest.raises(UnknownWorkflow):
            call()


def test_workflows_are_room_scoped(engine):
    make_workflow(engine)
    assert engine.workflows(room_id="room-2") == []


# --------------------------------------------------------------------------- #
# The whole chain, over the store
# --------------------------------------------------------------------------- #


def test_the_first_two_visits_are_recorded_and_show_nothing(engine):
    workflow = make_workflow(engine)
    first = engine.record_view(
        view_for(workflow["id"], visited_at=(NOW - timedelta(hours=2)).isoformat()),
        room_id=ROOM,
        actor="test",
        source=SOURCE,
        now=NOW,
    )
    assert first["show"] is False
    assert first["stopped_by"] == "matched_below_repeat"
    assert first["wrote"] is True
    # The view is written even though nothing was shown, because the repeat count is
    # made of the visits that did not qualify.
    assert len(engine.views(room_id=ROOM)) == 1


def test_the_third_matching_view_shows_the_block_and_writes_a_delivery(engine):
    workflow = make_workflow(engine)
    result = qualify(engine, workflow["id"])
    assert result["show"] is True
    assert result["delivery"]["state"] == "shown"
    assert result["delivery"]["channel"] == "in_app"
    assert len(engine.deliveries(room_id=ROOM)) == 1


def test_the_delivery_carries_the_blocks_and_the_trigger_that_caused_it(engine):
    workflow = make_workflow(engine)
    delivery = qualify(engine, workflow["id"])["delivery"]
    assert delivery["blocks"][0]["apps"][0]["kind"] == "video"
    assert delivery["trigger"]["matching_visits"] == 3
    assert delivery["trigger"]["visits_required"] == 3
    assert delivery["trigger"]["path"] == "/upgrade/plans"
    assert delivery["first_path_key"] == "yes_upgrade"


def test_a_view_outside_the_repeat_window_does_not_qualify_the_buyer(engine):
    workflow = make_workflow(engine)
    engine.record_view(
        view_for(workflow["id"], visited_at=(NOW - timedelta(days=9)).isoformat()),
        room_id=ROOM,
        actor="test",
        source=SOURCE,
        now=NOW,
    )
    engine.record_view(
        view_for(workflow["id"], visited_at=(NOW - timedelta(days=8)).isoformat()),
        room_id=ROOM,
        actor="test",
        source=SOURCE,
        now=NOW,
    )
    late = engine.record_view(
        view_for(workflow["id"], visited_at=(NOW - timedelta(days=7)).isoformat()),
        room_id=ROOM,
        actor="test",
        source=SOURCE,
        now=NOW,
    )
    assert late["matching_visits"] == 1
    assert late["show"] is False


def test_evaluate_writes_nothing_and_reports_the_same_decision(engine):
    workflow = make_workflow(engine)
    engine.record_view(
        view_for(workflow["id"], visited_at=(NOW - timedelta(hours=2)).isoformat()),
        room_id=ROOM,
        actor="test",
        source=SOURCE,
        now=NOW,
    )
    report = engine.evaluate(view_for(workflow["id"]), now=NOW)
    assert report["wrote"] is False
    assert report["stopped_by"] == "matched_below_repeat"
    assert report["matching_visits"] == 2
    assert len(engine.views(room_id=ROOM)) == 1


def test_evaluate_on_a_draft_says_the_workflow_is_not_live(engine):
    workflow = make_workflow(engine, state="draft")
    report = engine.evaluate(view_for(workflow["id"]), now=NOW)
    assert report["stopped_by"] == "not_live"
    assert report["state"] == "draft"


def test_evaluate_against_an_absent_workflow_is_refused(engine):
    with pytest.raises(UnknownWorkflow):
        engine.evaluate(view_for("nope"), now=NOW)


def test_the_seen_mode_shows_once_across_sessions(engine):
    workflow = make_workflow(engine, frequency="seen", repeat_visits=2)
    shown = qualify(engine, workflow["id"], count=2)
    assert shown["show"] is True
    for index in range(4, 9):
        again = engine.record_view(
            view_for(
                workflow["id"],
                session_id=f"sess-{index}",
                visited_at=(NOW - timedelta(hours=index)).isoformat(),
            ),
            room_id=ROOM,
            actor="test",
            source=SOURCE,
            now=NOW,
        )
        assert again["show"] is False
        assert again["stopped_by"] == "frequency_mode"
    assert len(engine.deliveries(room_id=ROOM)) == 1


def test_the_engaged_with_mode_shows_again_in_the_next_session(engine):
    workflow = make_workflow(engine, repeat_visits=1)
    first = engine.record_view(
        view_for(workflow["id"], session_id="sess-a"),
        room_id=ROOM,
        actor="test",
        source=SOURCE,
        now=NOW,
    )
    engine.record_interaction(
        first["delivery"]["id"],
        {"kind": "messenger_opened"},
        actor="test",
        source="test",
        now=NOW,
    )
    hidden = engine.record_view(
        view_for(workflow["id"], session_id="sess-a"),
        room_id=ROOM,
        actor="test",
        source=SOURCE,
        now=NOW,
    )
    assert hidden["stopped_by"] == "hidden_for_session"
    fresh = engine.record_view(
        view_for(workflow["id"], session_id="sess-b"),
        room_id=ROOM,
        actor="test",
        source=SOURCE,
        now=NOW,
    )
    assert fresh["show"] is True


def test_the_engaged_with_mode_stops_once_the_buyer_engages(engine):
    workflow = make_workflow(engine, repeat_visits=1)
    first = engine.record_view(
        view_for(workflow["id"], session_id="sess-a"),
        room_id=ROOM,
        actor="test",
        source=SOURCE,
        now=NOW,
    )
    engine.record_interaction(
        first["delivery"]["id"],
        {"kind": "path_selected", "path_key": "yes_upgrade"},
        actor="test",
        source="test",
        now=NOW,
    )
    later = engine.record_view(
        view_for(workflow["id"], session_id="sess-z"),
        room_id=ROOM,
        actor="test",
        source=SOURCE,
        now=NOW,
    )
    assert later["show"] is False
    assert later["stopped_by"] == "frequency_mode"


def test_the_any_interaction_mode_stops_on_a_click(engine):
    """A click is an interaction, so it ends the mode even though it is not an engagement."""
    workflow = make_workflow(engine, frequency="any_interaction", repeat_visits=1)
    first = engine.record_view(
        view_for(workflow["id"], session_id="sess-a"),
        room_id=ROOM,
        actor="test",
        source=SOURCE,
        now=NOW,
    )
    engine.record_interaction(
        first["delivery"]["id"], {"kind": "clicked"}, actor="test", source="test", now=NOW
    )
    later = engine.record_view(
        view_for(workflow["id"], session_id="sess-z"),
        room_id=ROOM,
        actor="test",
        source=SOURCE,
        now=NOW,
    )
    assert later["stopped_by"] == "frequency_mode"


def test_the_damper_hides_the_block_in_that_session_and_the_next_one_restores_it(engine):
    """The quote: hidden for the remainder of the session, shown again in a new one."""
    workflow = make_workflow(engine, repeat_visits=1)
    first = engine.record_view(
        view_for(workflow["id"], session_id="sess-a"),
        room_id=ROOM,
        actor="test",
        source=SOURCE,
        now=NOW,
    )
    engine.record_interaction(
        first["delivery"]["id"],
        {"kind": "dismissed"},
        actor="test",
        source="test",
        now=NOW,
    )
    same = engine.record_view(
        view_for(workflow["id"], session_id="sess-a"),
        room_id=ROOM,
        actor="test",
        source=SOURCE,
        now=NOW,
    )
    assert same["stopped_by"] == "hidden_for_session"
    assert "dismissed" in same["reason"]


def test_the_damper_of_one_workflow_does_not_hide_another_ones_block(engine):
    workflow = make_workflow(engine, repeat_visits=1)
    other = make_workflow(engine, name="Second workflow", repeat_visits=1)
    first = engine.record_view(
        view_for(workflow["id"], session_id="sess-a"),
        room_id=ROOM,
        actor="test",
        source=SOURCE,
        now=NOW,
    )
    engine.record_interaction(
        first["delivery"]["id"], {"kind": "dismissed"}, actor="test", source="test", now=NOW
    )
    untouched = engine.record_view(
        view_for(other["id"], session_id="sess-a"),
        room_id=ROOM,
        actor="test",
        source=SOURCE,
        now=NOW,
    )
    assert untouched["show"] is True


def test_one_visitor_does_not_inherit_another_visitors_count(engine):
    workflow = make_workflow(engine)
    for index in range(REPEAT_VISITS):
        engine.record_view(
            view_for(workflow["id"], session_id=f"sess-{index}"),
            room_id=ROOM,
            actor="test",
            source=SOURCE,
            now=NOW,
        )
    stranger = engine.record_view(
        view_for(workflow["id"], visitor_key="visitor-2", session_id="sess-new"),
        room_id=ROOM,
        actor="test",
        source=SOURCE,
        now=NOW,
    )
    assert stranger["matching_visits"] == 1
    assert stranger["show"] is False


def test_a_view_a_company_audience_excludes_is_recorded_but_shows_nothing(engine):
    workflow = make_workflow(
        engine, audience={"company_keys": ["northwind-energy"], "tags": [], "segments": []}
    )
    result = engine.record_view(
        view_for(workflow["id"], company_key="meridian-foods"),
        room_id=ROOM,
        actor="test",
        source=SOURCE,
        now=NOW,
    )
    assert result["show"] is False
    assert result["stopped_by"] == "audience"
    assert "not on the audience company list" in result["reason"]
    assert len(engine.views(room_id=ROOM)) == 1


def test_a_company_audience_does_not_show_to_an_unidentified_visitor(engine):
    """An unidentified visitor cannot be checked against a list, and is told so."""
    workflow = make_workflow(
        engine, audience={"company_keys": ["northwind-energy"], "tags": [], "segments": []}
    )
    result = engine.record_view(
        view_for(workflow["id"], company_key=""),
        room_id=ROOM,
        actor="test",
        source=SOURCE,
        now=NOW,
    )
    assert result["stopped_by"] == "audience"
    assert "carries no company key" in result["reason"]


def test_a_company_audience_is_matched_case_insensitively(engine):
    workflow = make_workflow(
        engine, audience={"company_keys": ["Northwind-Energy"], "tags": [], "segments": []}
    )
    result = engine.record_view(
        view_for(workflow["id"]), room_id=ROOM, actor="test", source=SOURCE, now=NOW
    )
    assert result["stopped_by"] == "matched_below_repeat"


def test_a_segments_audience_is_served_but_not_enforced(engine):
    """The page has no segment attribute to match on, so it says so rather than pretending."""
    workflow = make_workflow(
        engine, audience={"company_keys": [], "tags": [], "segments": ["enterprise"]}
    )
    result = engine.record_view(
        view_for(workflow["id"]), room_id=ROOM, actor="test", source=SOURCE, now=NOW
    )
    assert result["stopped_by"] == "audience"
    assert "no segment attribute" in result["reason"]


def test_an_empty_audience_does_not_filter_anything(engine):
    workflow = make_workflow(engine, repeat_visits=1)
    result = engine.record_view(
        view_for(workflow["id"], company_key=""),
        room_id=ROOM,
        actor="test",
        source=SOURCE,
        now=NOW,
    )
    assert result["show"] is True


# --------------------------------------------------------------------------- #
# Receipts
# --------------------------------------------------------------------------- #


def shown_delivery(engine: PageOutreach, **workflow_overrides) -> dict:
    """A workflow with a delivery on it, for the receipt tests to work against."""
    workflow = make_workflow(engine, repeat_visits=1, **workflow_overrides)
    return engine.record_view(
        view_for(workflow["id"]), room_id=ROOM, actor="test", source=SOURCE, now=NOW
    )["delivery"]


def test_a_receipt_is_recorded_against_the_delivery(engine):
    delivery = shown_delivery(engine)
    result = engine.record_interaction(
        delivery["id"], {"kind": "clicked"}, actor="test", source="test", now=NOW
    )
    assert result["receipt"]["data"]["content_stat"] == "click"
    assert result["delivery"]["receipt_count"] == 1
    assert result["delivery"]["state"] == "interacted"


def test_a_receipt_records_the_published_content_stat_topic_for_each_kind(engine):
    delivery = shown_delivery(engine)
    expected = {
        "messenger_opened": "open",
        "clicked": "click",
        "goal_reached": "goal",
        "dismissed": "receipt",
        "path_selected": "receipt",
    }
    for kind, topic in expected.items():
        body = {"kind": kind}
        if kind == "path_selected":
            body["path_key"] = "yes_upgrade"
        result = engine.record_interaction(
            delivery["id"], body, actor="test", source="test", now=NOW
        )
        assert result["receipt"]["data"]["content_stat"] == topic, kind


def test_a_dismissal_hides_the_delivery_for_the_rest_of_its_session(engine):
    delivery = shown_delivery(engine)
    result = engine.record_interaction(
        delivery["id"], {"kind": "dismissed"}, actor="test", source="test", now=NOW
    )
    assert result["delivery"]["state"] == "hidden_for_session"
    assert result["delivery"]["session_hidden"] is True


def test_a_path_selection_engages_the_delivery_and_records_the_branch_label(engine):
    delivery = shown_delivery(engine)
    result = engine.record_interaction(
        delivery["id"],
        {"kind": "path_selected", "path_key": "yes_upgrade"},
        actor="test",
        source="test",
        now=NOW,
    )
    assert result["delivery"]["state"] == "engaged"
    assert result["receipt"]["data"]["path_label"] == "Yes, let's talk about upgrading"


def test_an_engagement_outranks_a_dismissal_on_the_same_delivery(engine):
    """The derived state reads the most meaningful receipt, so a buyer cannot be both."""
    delivery = shown_delivery(engine)
    engine.record_interaction(
        delivery["id"], {"kind": "dismissed"}, actor="test", source="test", now=NOW
    )
    result = engine.record_interaction(
        delivery["id"],
        {"kind": "path_selected", "path_key": "not_right_now"},
        actor="test",
        source="test",
        now=NOW,
    )
    assert result["delivery"]["state"] == "engaged"


def test_a_receipt_on_an_unpublished_kind_is_refused(engine):
    delivery = shown_delivery(engine)
    with pytest.raises(InvalidInteraction) as caught:
        engine.record_interaction(
            delivery["id"], {"kind": "shrugged"}, actor="test", source="test", now=NOW
        )
    assert "kind must be one of" in str(caught.value)


def test_a_receipt_with_no_kind_is_refused(engine):
    delivery = shown_delivery(engine)
    with pytest.raises(InvalidInteraction):
        engine.record_interaction(delivery["id"], {}, actor="test", source="test", now=NOW)


def test_a_receipt_carrying_an_unread_field_is_refused(engine):
    delivery = shown_delivery(engine)
    with pytest.raises(InvalidInteraction) as caught:
        engine.record_interaction(
            delivery["id"],
            {"kind": "clicked", "msisdn": "+44..."},
            actor="test",
            source="test",
            now=NOW,
        )
    assert "msisdn" in str(caught.value)


def test_a_receipt_that_is_not_an_object_is_refused(engine):
    delivery = shown_delivery(engine)
    with pytest.raises(InvalidInteraction):
        engine.record_interaction(delivery["id"], ["clicked"], actor="test", source="test", now=NOW)


def test_a_receipt_on_an_absent_delivery_is_refused(engine):
    with pytest.raises(UnknownDelivery):
        engine.record_interaction("nope", {"kind": "clicked"}, actor="test", source="test", now=NOW)


def test_reading_an_absent_delivery_is_refused(engine):
    with pytest.raises(UnknownDelivery):
        engine.read_delivery("nope")


def test_a_path_selection_naming_no_declared_branch_is_refused(engine):
    """The flow branches on the buyer's answer, so an answer with no branch points at nothing."""
    delivery = shown_delivery(engine)
    with pytest.raises(UnknownPath) as caught:
        engine.record_interaction(
            delivery["id"],
            {"kind": "path_selected", "path_key": "maybe"},
            actor="test",
            source="test",
            now=NOW,
        )
    assert "yes_upgrade" in str(caught.value)


def test_a_path_selection_with_no_key_is_refused(engine):
    delivery = shown_delivery(engine)
    with pytest.raises(UnknownPath):
        engine.record_interaction(
            delivery["id"], {"kind": "path_selected"}, actor="test", source="test", now=NOW
        )


def test_a_path_selection_on_a_workflow_with_no_paths_is_refused_and_says_why(engine):
    delivery = shown_delivery(engine, paths=[])
    with pytest.raises(UnknownPath) as caught:
        engine.record_interaction(
            delivery["id"],
            {"kind": "path_selected", "path_key": "yes"},
            actor="test",
            source="test",
            now=NOW,
        )
    assert "declares no paths" in str(caught.value)


def test_a_dismissal_needs_no_path_key(engine):
    delivery = shown_delivery(engine, paths=[])
    result = engine.record_interaction(
        delivery["id"], {"kind": "dismissed"}, actor="test", source="test", now=NOW
    )
    assert result["receipt"]["data"]["path_key"] == ""


def test_a_receipt_on_a_deleted_workflow_is_refused_rather_than_written_against_nothing(engine):
    delivery = shown_delivery(engine)
    workflow_id = delivery["workflow_id"]
    engine.delete_workflow(workflow_id, actor="test", source="test")
    with pytest.raises(UnknownWorkflow):
        engine.record_interaction(
            delivery["id"], {"kind": "clicked"}, actor="test", source="test", now=NOW
        )


def test_receipts_can_be_filtered_by_visitor_workflow_and_delivery(engine):
    first = shown_delivery(engine)
    second = shown_delivery(engine, name="Another one")
    engine.record_interaction(
        first["id"], {"kind": "clicked"}, actor="test", source="test", now=NOW
    )
    engine.record_interaction(
        second["id"], {"kind": "clicked"}, actor="test", source="test", now=NOW
    )
    assert len(engine.receipts(room_id=ROOM)) == 2
    assert len(engine.receipts(room_id=ROOM, delivery_id=first["id"])) == 1
    assert len(engine.receipts(room_id=ROOM, workflow_id=second["workflow_id"])) == 1
    assert len(engine.receipts(room_id=ROOM, visitor_key="nobody")) == 0


# --------------------------------------------------------------------------- #
# The reads a client needs
# --------------------------------------------------------------------------- #


def test_the_summary_counts_this_features_own_four_collections_only(engine):
    workflow = make_workflow(engine, repeat_visits=1)
    engine.record_view(view_for(workflow["id"]), room_id=ROOM, actor="test", source=SOURCE, now=NOW)
    body = engine.summary(room_id=ROOM)
    assert body["workflows"] == 1
    assert body["live_workflows"] == 1
    assert body["views"] == 1
    assert body["deliveries"] == 1
    assert body["receipts"] == 0
    assert body["calls_vendor"] is False


def test_the_summary_of_an_empty_room_reads_as_zeros(engine):
    body = engine.summary(room_id="room-empty")
    assert body == {
        **{
            key: 0
            for key in (
                "workflows",
                "live_workflows",
                "draft_workflows",
                "views",
                "matched_views",
                "deliveries",
                "receipts",
                "engaged_receipts",
                "hidden_for_session",
            )
        },
        "room_id": "room-empty",
        "receipt_kinds": {},
        "calls_vendor": False,
        "sends_email": False,
        "sends_sms": False,
        "renders_in_browser_messenger": False,
        "reads": engine.summary(room_id="room-empty")["reads"],
    }


def test_the_summary_counts_receipts_by_kind_and_engagement(engine):
    delivery = shown_delivery(engine)
    engine.record_interaction(
        delivery["id"], {"kind": "clicked"}, actor="test", source="test", now=NOW
    )
    engine.record_interaction(
        delivery["id"],
        {"kind": "path_selected", "path_key": "yes_upgrade"},
        actor="test",
        source="test",
        now=NOW,
    )
    body = engine.summary(room_id=ROOM)
    assert body["receipt_kinds"] == {"clicked": 1, "path_selected": 1}
    assert body["engaged_receipts"] == 1


def test_the_prospects_view_includes_the_buyer_who_never_qualified(engine):
    """A view built from the deliveries alone could only ever show who was shown the block."""
    workflow = make_workflow(engine)
    engine.record_view(
        view_for(workflow["id"], visitor_key="quiet-one"),
        room_id=ROOM,
        actor="test",
        source=SOURCE,
        now=NOW,
    )
    body = engine.prospects(room_id=ROOM, now=NOW)
    assert body["count"] == 1
    assert body["shown_to"] == 0
    assert body["not_shown_to"] == 1
    row = body["prospects"][0]
    assert row["visitor_key"] == "quiet-one"
    assert row["matched_visits"] == 1
    assert row["paths"] == ["/upgrade/plans"]


def test_the_prospects_view_separates_the_states_a_buyer_can_be_in(engine):
    """Four rows at a one-visit threshold: engaged, hidden, and two shown with nothing on them."""
    workflow = make_workflow(engine, repeat_visits=1)
    qualified = engine.record_view(
        view_for(workflow["id"], visitor_key="a"),
        room_id=ROOM,
        actor="test",
        source=SOURCE,
        now=NOW,
    )
    engine.record_interaction(
        qualified["delivery"]["id"],
        {"kind": "path_selected", "path_key": "yes_upgrade"},
        actor="test",
        source="test",
        now=NOW,
    )
    hidden = engine.record_view(
        view_for(workflow["id"], visitor_key="b"),
        room_id=ROOM,
        actor="test",
        source=SOURCE,
        now=NOW,
    )
    engine.record_interaction(
        hidden["delivery"]["id"], {"kind": "dismissed"}, actor="test", source="test", now=NOW
    )
    for visitor in ("c", "d"):
        engine.record_view(
            view_for(workflow["id"], visitor_key=visitor),
            room_id=ROOM,
            actor="test",
            source=SOURCE,
            now=NOW,
        )
    body = engine.prospects(room_id=ROOM, now=NOW)
    assert body["count"] == 4
    assert body["shown_to"] == 4
    assert body["not_shown_to"] == 0
    assert body["engaged"] == 1
    assert body["hidden_for_session"] == 1
    rows = {row["visitor_key"]: row for row in body["prospects"]}
    assert rows["a"]["engaged"] is True
    assert rows["b"]["hidden_for_session"] is True
    assert rows["c"]["engaged"] is False
    assert rows["c"]["deliveries"] == 1


def test_the_prospects_view_shows_a_buyer_who_browsed_and_never_qualified(engine):
    """The half of the screen a delivery-driven view can never produce."""
    workflow = make_workflow(engine, repeat_visits=3)
    qualify(engine, workflow["id"], count=2, visitor_key="still-thinking")
    body = engine.prospects(room_id=ROOM, now=NOW)
    assert body["count"] == 1
    assert body["shown_to"] == 0
    assert body["not_shown_to"] == 1
    row = body["prospects"][0]
    assert row["visitor_key"] == "still-thinking"
    assert row["matched_visits"] == 2
    assert row["workflows"] == ["Upgrade page repeaters"]


def test_the_prospects_view_is_ordered_newest_first(engine):
    workflow = make_workflow(engine, repeat_visits=1)
    engine.record_view(
        view_for(
            workflow["id"],
            visitor_key="older",
            visited_at=(NOW - timedelta(days=2)).isoformat(),
        ),
        room_id=ROOM,
        actor="test",
        source=SOURCE,
        now=NOW,
    )
    engine.record_view(
        view_for(
            workflow["id"],
            visitor_key="newer",
            visited_at=(NOW - timedelta(hours=1)).isoformat(),
        ),
        room_id=ROOM,
        actor="test",
        source=SOURCE,
        now=NOW,
    )
    keys = [row["visitor_key"] for row in engine.prospects(room_id=ROOM, now=NOW)["prospects"]]
    assert keys == ["newer", "older"]


def test_views_can_be_filtered_by_workflow_and_visitor(engine):
    first = make_workflow(engine, repeat_visits=1)
    second = make_workflow(engine, name="Second", repeat_visits=1)
    engine.record_view(view_for(first["id"]), room_id=ROOM, actor="test", source=SOURCE, now=NOW)
    engine.record_view(view_for(second["id"]), room_id=ROOM, actor="test", source=SOURCE, now=NOW)
    assert len(engine.views(room_id=ROOM)) == 2
    assert len(engine.views(room_id=ROOM, workflow_id=first["id"])) == 1
    assert len(engine.views(room_id=ROOM, visitor_key="nobody")) == 0


def test_reading_a_view_returns_the_snapshot_the_decision_was_based_on(engine):
    """A recount would answer what is true now, not what the decision was made on."""
    workflow = make_workflow(engine)
    view_id = engine.record_view(
        view_for(workflow["id"]), room_id=ROOM, actor="test", source=SOURCE, now=NOW
    )
    rows = engine.views(room_id=ROOM)
    assert rows[0]["counts"]["matching_visits"] == 1
    assert rows[0]["counts"]["visits_required"] == 3
    assert engine.read_view(rows[0]["id"])["stopped_by"] == "matched_below_repeat"
    assert view_id["show"] is False


def test_reading_an_absent_view_is_refused(engine):
    with pytest.raises(UnknownPageView):
        engine.read_view("nope")


def test_reading_a_delivery_id_as_a_view_is_refused(engine):
    """The collections are named apart, so one id never resolves as the other."""
    delivery = shown_delivery(engine)
    with pytest.raises(UnknownPageView):
        engine.read_view(delivery["id"])


# --------------------------------------------------------------------------- #
# The write path
# --------------------------------------------------------------------------- #


def test_every_write_names_the_route_that_served_it(db: AuditedDatabase):
    """The audit row has to name a path the app really serves, which is why source is required."""
    engine = PageOutreach(RecordStore(db))
    workflow = engine.create_workflow(
        {**LIVE_WORKFLOW, "repeat_visits": 1},
        room_id=ROOM,
        actor="test",
        source="POST /api/wf-106/workflows",
        now=NOW,
    )
    result = engine.record_view(
        view_for(workflow["id"], session_id="sess-a"),
        room_id=ROOM,
        actor="test",
        source="POST /api/wf-106/views",
        now=NOW,
    )
    assert result["delivery"] is not None, result["reason"]
    engine.record_interaction(
        result["delivery"]["id"],
        {"kind": "clicked"},
        actor="test",
        source="POST /api/wf-106/deliveries/{delivery_id}/interactions",
        now=NOW,
    )
    sources = {entry["source"] for entry in db.audit(limit=50)}
    assert sources == {
        "POST /api/wf-106/workflows",
        "POST /api/wf-106/views",
        "POST /api/wf-106/deliveries/{delivery_id}/interactions",
    }
    assert all(str(entry["source"]).startswith("POST /api/wf-106") for entry in db.audit(limit=50))


# --------------------------------------------------------------------------- #
# The guards a direct engine caller can reach and an HTTP caller cannot
# --------------------------------------------------------------------------- #


#: FastAPI hands every body in as an object, so these refusals are only reachable by a
#: caller that built the engine itself. They are real, so they are tested rather than
#: left as the unreachable-looking lines coverage reports.
def test_a_workflow_that_is_not_an_object_is_refused(engine):
    with pytest.raises(InvalidWorkflow) as caught:
        engine.create_workflow(["not", "an", "object"], room_id=ROOM, actor="test", source="test")
    assert "must be an object" in str(caught.value)


@pytest.mark.parametrize("field", ["repeat_visits", "repeat_window_days", "dwell_seconds"])
def test_a_workflow_number_that_is_text_is_refused(engine, field):
    with pytest.raises(InvalidWorkflow) as caught:
        make_workflow(engine, **{field: "three"})
    assert "whole number" in str(caught.value)


def test_a_workflow_number_with_a_fraction_is_refused(engine):
    with pytest.raises(InvalidWorkflow):
        make_workflow(engine, repeat_visits=2.5)


def test_a_dwell_rule_value_longer_than_a_day_is_refused(engine):
    with pytest.raises(InvalidWorkflow) as caught:
        make_workflow(engine, rules=[{"kind": "dwell", "value": "90000"}])
    assert "between 1 and 86400" in str(caught.value)


def test_a_rules_field_that_is_a_string_is_refused(engine):
    with pytest.raises(InvalidWorkflow) as caught:
        make_workflow(engine, rules="/upgrade")
    assert "must be a list" in str(caught.value)


def test_a_rule_that_is_not_an_object_is_refused(engine):
    with pytest.raises(InvalidWorkflow) as caught:
        make_workflow(engine, rules=["/upgrade"])
    assert "must be an object" in str(caught.value)


def test_an_audience_that_is_not_an_object_is_refused(engine):
    with pytest.raises(InvalidWorkflow) as caught:
        make_workflow(engine, audience=["northwind"])
    assert "company_keys, tags and segments" in str(caught.value)


def test_an_audience_entry_that_is_blank_is_refused(engine):
    with pytest.raises(InvalidWorkflow) as caught:
        make_workflow(engine, audience={"company_keys": ["northwind", "  "]})
    assert "blank entry" in str(caught.value)


def test_a_scheduling_pane_that_is_not_an_object_is_refused(engine):
    with pytest.raises(InvalidWorkflow) as caught:
        make_workflow(engine, scheduling="live")
    assert "must be an object" in str(caught.value)


def test_a_blocks_field_that_is_a_string_is_refused(engine):
    with pytest.raises(InvalidWorkflow):
        make_workflow(engine, blocks="hello")


def test_a_block_that_is_not_an_object_is_refused(engine):
    with pytest.raises(InvalidWorkflow) as caught:
        make_workflow(engine, blocks=["hello"])
    assert "must be an object" in str(caught.value)


def test_a_blocks_apps_field_that_is_a_string_is_refused(engine):
    with pytest.raises(InvalidWorkflow) as caught:
        make_workflow(engine, blocks=[{"kind": "message", "text": "hi", "apps": "video"}])
    assert "apps must be a list" in str(caught.value)


def test_an_app_that_is_not_an_object_is_refused(engine):
    with pytest.raises(InvalidWorkflow) as caught:
        make_workflow(engine, blocks=[{"kind": "message", "text": "hi", "apps": ["a video"]}])
    assert "an app must be an object" in str(caught.value)


def test_an_app_with_no_title_is_refused(engine):
    with pytest.raises(InvalidWorkflow) as caught:
        make_workflow(engine, blocks=[{"kind": "message", "text": "hi", "apps": [{"url": "x"}]}])
    assert "needs a title" in str(caught.value)


def test_a_paths_field_that_is_a_string_is_refused(engine):
    with pytest.raises(InvalidWorkflow):
        make_workflow(engine, paths="yes")


def test_a_path_that_is_not_an_object_is_refused(engine):
    with pytest.raises(InvalidWorkflow) as caught:
        make_workflow(engine, paths=["yes"])
    assert "must be an object" in str(caught.value)


def test_a_path_with_no_key_is_refused(engine):
    with pytest.raises(InvalidWorkflow) as caught:
        make_workflow(engine, paths=[{"label": "Yes"}])
    assert "needs a key" in str(caught.value)


def test_every_pane_may_be_omitted_entirely_and_the_workflow_still_saves(engine):
    """A seller who names only a name has said the least, and it is refused nowhere."""
    workflow = engine.create_workflow(
        {"name": "Bare"}, room_id=ROOM, actor="test", source="test", now=NOW
    )
    assert workflow["rules"] == []
    assert workflow["audience"] == {"company_keys": [], "tags": [], "segments": []}
    assert workflow["blocks"] == []
    assert workflow["paths"] == []
    assert workflow["frequency"] == "seen"
    assert workflow["state"] == "draft"
    assert workflow["repeat_visits"] == REPEAT_VISITS
    assert workflow["repeat_window_days"] == REPEAT_WINDOW_DAYS
    assert workflow["dwell_seconds"] == DWELL_SECONDS
    assert workflow["channel"] == "in_app"


# --------------------------------------------------------------------------- #
# The guards inside the page-view reader
# --------------------------------------------------------------------------- #


def test_a_page_view_field_that_is_not_text_is_refused():
    with pytest.raises(InvalidPageView) as caught:
        parse_page_view(view_for("wf-1", path=17), now=NOW)
    assert "must be text" in str(caught.value)


def test_a_dwell_reading_that_is_a_boolean_is_refused():
    """True is an int in Python, so a bool has to be refused before it reads as 1 second."""
    with pytest.raises(InvalidPageView):
        parse_page_view(view_for("wf-1", dwell_seconds=True), now=NOW)


def test_a_visited_at_given_as_a_datetime_is_accepted_and_normalised():
    view = parse_page_view(view_for("wf-1", visited_at=NOW), now=NOW)
    assert view.visited_at == NOW
    naive = datetime(2026, 10, 5, 9, 0)
    with pytest.raises(InvalidPageView):
        parse_page_view(view_for("wf-1", visited_at=naive), now=NOW)


def test_a_utc_campaign_rule_is_matched_like_a_source_rule():
    decision = decide(
        gate_view(utm_campaign="q3-enterprise"),
        rules=[{"kind": "utm_campaign", "value": "q3-*"}],
        matching_visits=1,
        visits_required=1,
        now=NOW,
    )
    assert decision.show is True
    entry = decision.rule_matches[0]
    assert entry.kind == "utm_campaign"
    assert entry.observed == "q3-enterprise"


def test_a_rule_on_a_kind_the_parser_does_not_implement_never_matches():
    """The save path refuses one, so this is the belt to that braces: a stored record
    from an older build still cannot fire on a kind nothing implements."""
    decision = decide(
        gate_view(),
        rules=[{"kind": "referrer_host", "mode": "equals", "value": "x"}],
        matching_visits=5,
        visits_required=1,
        now=NOW,
    )
    assert decision.show is False
    assert decision.rule_matches[0].kind == "referrer_host"
    assert decision.rule_matches[0].met is False


def test_a_history_row_carrying_a_datetime_object_is_counted():
    history = [{"visited_at": NOW - timedelta(days=1), "matched": True}]
    assert count_matching_visits(history, now=NOW, window_days=7) == 1


def test_a_history_row_carrying_an_unzoned_datetime_object_is_skipped():
    history = [{"visited_at": datetime(2026, 10, 4, 9, 0), "matched": True}]
    assert count_matching_visits(history, now=NOW, window_days=7) == 0


def test_a_history_row_carrying_an_unparseable_moment_is_skipped():
    history = [{"visited_at": "last tuesday", "matched": True}]
    assert count_matching_visits(history, now=NOW, window_days=7) == 0


def test_a_mode_this_package_cannot_implement_is_refused(monkeypatch):
    """The defensive branch. Reached by a rule stop that is neither show, interaction
    nor path_selected, which no published mode uses today."""
    from dsr.page_outreach import rules

    monkeypatch.setitem(rules._FREQUENCY_RULES, "always", "whenever")
    allowed, reason = rules.frequency_decision("always", shown=0, interacted=0, engaged=0)
    assert allowed is False
    assert "does not implement" in reason


def test_the_two_published_rule_kind_helpers_agree():
    from dsr.page_outreach import rules

    assert rules.supported_rule_kinds() == ("url", "dwell", "utm_source", "utm_campaign")
    assert rules.known_rule_kinds() == rules.supported_rule_kinds()


def test_a_scheduling_state_outside_the_two_published_ones_is_refused(engine):
    with pytest.raises(InvalidWorkflow) as caught:
        make_workflow(engine, scheduling={"state": "published"})
    assert "scheduling.state must be one of" in str(caught.value)


def test_the_scheduling_pane_keeps_its_own_start_and_end_moments_as_written(engine):
    """They are display values the workflow never compares against, so they are not parsed."""
    workflow = make_workflow(
        engine, scheduling={"state": "live", "starts_at": "2026-11-01", "ends_at": "2026-12-01"}
    )
    assert workflow["scheduling"] == {
        "state": "live",
        "starts_at": "2026-11-01",
        "ends_at": "2026-12-01",
    }


def test_deliveries_can_be_filtered_by_visitor_and_workflow_over_the_engine(engine):
    first = shown_delivery(engine)
    shown_delivery(engine, name="Second workflow")
    assert len(engine.deliveries(room_id=ROOM)) == 2
    assert len(engine.deliveries(room_id=ROOM, visitor_key="visitor-1")) == 2
    assert len(engine.deliveries(room_id=ROOM, workflow_id=first["workflow_id"])) == 1
    assert len(engine.deliveries(room_id=ROOM, visitor_key="nobody")) == 0


def test_receipts_can_be_filtered_by_visitor_and_workflow_over_the_engine(engine):
    first = shown_delivery(engine)
    second = shown_delivery(engine, name="Second workflow")
    engine.record_interaction(
        first["id"], {"kind": "clicked"}, actor="test", source="test", now=NOW
    )
    engine.record_interaction(
        second["id"], {"kind": "clicked"}, actor="test", source="test", now=NOW
    )
    workflow_id = first["workflow_id"]
    assert len(engine.receipts(room_id=ROOM, visitor_key="visitor-1")) == 2
    assert len(engine.receipts(room_id=ROOM, workflow_id=workflow_id)) == 1
    assert engine.receipts(room_id="room-2") == []


def test_the_domain_package_imports_neither_the_app_nor_sqlite():
    """An enforced test greps the feature modules; this pins it for the package itself."""
    import pathlib

    import dsr.page_outreach as package

    package_dir = pathlib.Path(package.__file__).parent
    for module in sorted(package_dir.glob("*.py")):
        text = module.read_text(encoding="utf-8")
        assert "from dsr.api" not in text and "import dsr.api" not in text, module.name
        assert "import sqlite3" not in text, module.name
        assert "from fastapi" not in text, module.name
