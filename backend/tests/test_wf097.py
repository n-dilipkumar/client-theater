"""Domain tests for WF-097: quote activity, the four counters, and follow-up.

Each test names the rule it protects in its docstring and quotes the sentence it
comes from, so a reviewer can check the assertion against
``docs/research/digital-sales-room-workflows/wf/WF-097.md`` rather than taking
the implementation's word for it.

These tests drive the engine through a real audited database rather than a mock.
That is deliberate: the product promise is that the audit row is written in the
same transaction as the change, and a mock store cannot demonstrate it. The
database is a temporary file per test, so the file passes on its own and under
pytest-xdist without depending on the order anything ran in.

Every test that measures an age passes a frozen ``now``. A test that depended on
today's date would pass on one day and fail on another, and the two outcomes
would both look like a defect in the workflow.
"""

from __future__ import annotations

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from dsr.db.audited import AuditedDatabase
from dsr.quote_engagement.engagement import (
    EXPIRING_WITHIN_DAYS,
    NO_ACTIVITY_AFTER_DAYS,
    QuoteEngagementService,
    vocabulary,
)
from dsr.quote_engagement.rules import (
    QuoteEngagementRuleError,
    chart_points,
    chart_tone,
    classify_activity,
    counts_as_view,
    event_side,
    iso,
    normalise_event,
    normalise_surface,
    parse_timestamp,
    pipeline_overview,
    read_expiration,
    read_owner,
    read_signing_status,
    recommend_follow_ups,
    require_threshold,
    require_window,
    summarise_events,
)
from dsr.quote_engagement.vocabulary import (
    ACTIVITY_COUNT,
    ACTIVITY_HIGH,
    ACTIVITY_LOW,
    ACTIVITY_SINCE_COMMUNICATION,
    ACTIVITY_UNCLASSIFIED,
    ALL_EVENTS,
    BUYER_EVENTS,
    CHAT_STARTED,
    EMAIL_CHANNEL_CONNECTED,
    HIGH_ACTIVITY_THRESHOLD,
    LAST_ACTIVITY_AT,
    LAST_COMMUNICATION_AT,
    MAX_THRESHOLD,
    MIN_THRESHOLD,
    QUOTE_ACCEPTED,
    QUOTE_VIEWED,
    REASON_EXPIRED_UNACCEPTED,
    REASON_EXPIRING_SOON,
    REASON_NO_ACTIVITY,
    REASON_NO_REPLY,
    SELLER_EVENTS,
    SINCE_COMMUNICATION_NO_BASELINE,
    SINCE_COMMUNICATION_UNAVAILABLE,
    SURFACE_EMAIL_PDF,
    SURFACE_QUOTE_LINK,
    VIEW_COUNT,
)
from dsr.store import RecordStore

SOURCE = "POST /api/wf-097/quotes/abc/activity"

#: A frozen moment. Every age in these tests is measured from here, so no test
#: depends on what day the suite runs, and no derived date is ever in the future
#: relative to the wall clock, which the future-event rule would refuse.
NOW = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture()
def service():
    """A service on its own temporary database.

    The database is closed before the directory is removed. On Windows an open
    handle blocks the delete, and a leak here would surface as a permission error
    in an unrelated test rather than as the leak it is.
    """
    tmp = tempfile.TemporaryDirectory()
    db = AuditedDatabase(str(Path(tmp.name) / "wf097.db"))
    try:
        yield QuoteEngagementService(RecordStore(db)), db
    finally:
        db.close()
        tmp.cleanup()


def make_quote(service, **overrides):
    """A published quote with an expiration date, unless a test says otherwise."""
    payload = {
        "title": "Northwind renewal",
        "status": "PUBLISHED",
        "quote_number": "Q-2026-021",
        "owner": "dana",
        "hs_quote_amount": 28500.0,
        "expires_at": (NOW + timedelta(days=21)).isoformat(timespec="seconds"),
    }
    payload.update(overrides)
    return service.store.create("quote", payload, room_id="room_1", actor="dana", source="seed")


def make_settings(service, **overrides):
    """Write the analysis settings the engine reads."""
    payload = {
        HIGH_ACTIVITY_THRESHOLD: 10,
        NO_ACTIVITY_AFTER_DAYS: 30,
        EXPIRING_WITHIN_DAYS: 14,
        EMAIL_CHANNEL_CONNECTED: False,
    }
    payload.update(overrides)
    return service.store.create(
        "quote_engagement_settings", payload, room_id="room_1", actor="dana", source="seed"
    )


def record(service, quote_id, event, **kwargs):
    """Record one event through the engine, at a frozen time."""
    kwargs.setdefault("occurred_at", NOW.isoformat(timespec="seconds"))
    return service.record_event(quote_id, event=event, source=SOURCE, **kwargs)


# --------------------------------------------------------------------------- #
# The event vocabulary
# --------------------------------------------------------------------------- #


def test_the_researched_buyer_events_are_the_ones_accepted():
    """Researched: "Buyer events include ``Quote sent``, ``Quote viewed``, ``Quote
    downloaded``, ``Quote printed``, ``Quote buyer signed``, ``Quote manually
    signed``, ``Quote accepted``."

    The vocabulary is the test surface, so the seven labels are asserted one by
    one rather than as a count: a list of the right length with one name wrong
    passes a length check and fails a seller reading the timeline.
    """
    assert BUYER_EVENTS == (
        "Quote sent",
        "Quote viewed",
        "Quote downloaded",
        "Quote printed",
        "Quote buyer signed",
        "Quote manually signed",
        "Quote accepted",
    )


def test_the_researched_seller_events_are_the_ones_accepted():
    """Researched: "The seller activity list includes ``Quote published``, ``Quote approval
    requested``, ``Quote approval rejected / requested changes``, ``Quote
    approved``, ``Quote recalled``, ``Quote countersigned``, ``Quote
    reassigned``, ``Signing attempt failed``, ``Chat started`` ... and ``Quote
    expired``."

    ``Chat started`` is asserted here because the research lists it in the seller
    list, and then asserted on the other side too: the research defines it as a
    buyer action, so its side is the definition's.
    """
    assert SELLER_EVENTS == (
        "Quote published",
        "Quote approval requested",
        "Quote approval rejected / requested changes",
        "Quote approved",
        "Quote recalled",
        "Quote countersigned",
        "Quote reassigned",
        "Signing attempt failed",
        "Chat started",
        "Quote expired",
    )
    assert event_side(CHAT_STARTED) == "buyer"


def test_every_researched_event_is_in_the_all_events_list():
    """A seller-facing list of events is only complete if it names all 17."""
    assert len(ALL_EVENTS) == 17
    assert set(ALL_EVENTS) == set(BUYER_EVENTS) | set(SELLER_EVENTS)


def test_a_side_is_recorded_for_every_event():
    """The side is what the timeline groups by, so no event may lack one."""
    for event in ALL_EVENTS:
        assert event_side(event) in ("buyer", "seller")


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Quote viewed", "Quote viewed"),
        ("  quote   viewed ", "Quote viewed"),
        ("QUOTE VIEWED", "Quote viewed"),
        ("quote_viewed", "Quote viewed"),
    ],
)
def test_an_event_label_is_read_case_and_space_insensitively(raw, expected):
    """The researched labels are prose, and a client will type a slug."""
    assert normalise_event(raw) == expected


def test_an_event_outside_the_vocabulary_is_refused_with_the_allowed_set():
    """An activity log that accepts any string stops being the researched list.

    A row this workflow did not write is a row no rule has checked, so a quote
    whose timeline contains one cannot be classified on the strength of it.
    """
    with pytest.raises(QuoteEngagementRuleError, match="not a quote activity event"):
        normalise_event("Quote telepathed")
    with pytest.raises(QuoteEngagementRuleError, match="Quote accepted"):
        normalise_event("Quote telepathed")


def test_an_empty_event_is_refused():
    """A record with no label is not an event."""
    for blank in (None, "", "   "):
        with pytest.raises(QuoteEngagementRuleError, match="needs an event label"):
            normalise_event(blank)


# --------------------------------------------------------------------------- #
# The view rule
# --------------------------------------------------------------------------- #


def test_a_buyer_who_opened_the_quote_link_is_a_view():
    """Researched: "**Quote viewed:** the buyer has opened the quote using the quote link." """
    assert counts_as_view(QUOTE_VIEWED, SURFACE_QUOTE_LINK) is True
    assert counts_as_view(QUOTE_VIEWED, None) is True


def test_a_buyer_who_opened_the_pdf_in_the_email_is_not_a_view():
    """Researched: "If a buyer opens the PDF attached to the email, this doesn't count as a
    quote view."

    This is the one event that has a counter and does not move it. The event is
    still recorded and still on the timeline, which is what lets a seller see
    that the buyer opened something while the count stays honest about what that
    was.
    """
    assert counts_as_view(QUOTE_VIEWED, SURFACE_EMAIL_PDF) is False


def test_no_other_event_counts_as_a_view():
    """Only the viewed event moves the View Count.

    A quote that was downloaded was obviously opened first, but the research
    gives the counter one event and naming more would make the count a different
    figure from the one the platform shows a seller.
    """
    for event in ALL_EVENTS:
        if event == QUOTE_VIEWED:
            continue
        assert counts_as_view(event, SURFACE_QUOTE_LINK) is False


def test_an_unknown_surface_is_refused_rather_than_counted_as_a_view():
    """A surface the rules never heard of must not slip past the view rule."""
    with pytest.raises(QuoteEngagementRuleError, match="not a known activity surface"):
        normalise_surface("carrier_pigeon", QUOTE_VIEWED)


def test_the_view_count_counts_links_and_ignores_the_attached_pdf():
    """The distinction, measured on a whole log rather than on one event."""
    events = [
        {"event": QUOTE_VIEWED, "surface": SURFACE_QUOTE_LINK, "occurred_at": iso(NOW)},
        {"event": QUOTE_VIEWED, "surface": SURFACE_QUOTE_LINK, "occurred_at": iso(NOW)},
        {"event": QUOTE_VIEWED, "surface": SURFACE_EMAIL_PDF, "occurred_at": iso(NOW)},
        {"event": "Quote downloaded", "surface": "buyer_portal", "occurred_at": iso(NOW)},
    ]
    counters = summarise_events(events, now=NOW, threshold=10)
    assert counters[VIEW_COUNT] == 2
    assert counters[ACTIVITY_COUNT] == 4
    assert counters["pdf_opens_not_counted_as_views"] == 1


# --------------------------------------------------------------------------- #
# The threshold
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "count,threshold,expected",
    [
        (0, 10, ACTIVITY_LOW),
        (9, 10, ACTIVITY_LOW),
        (10, 10, ACTIVITY_HIGH),
        (11, 10, ACTIVITY_HIGH),
        (250, 10, ACTIVITY_HIGH),
    ],
)
def test_a_count_below_the_threshold_is_low_and_the_rest_is_high(count, threshold, expected):
    """Researched: "Published quotes with activity below this threshold are classified *Low
    activity*."

    A count exactly at the threshold is High. The research says "below", which in
    plain English excludes equality, and a super admin who types 10 into a
    threshold field has said that 10 activities is the engagement level. Jev
    chose this at confidence 0.92, audit ``jev-20261013T111832-11972-12671``.
    """
    assert classify_activity(count, threshold) == expected


def test_an_unconfigured_threshold_classifies_nothing():
    """Researched: "The activity threshold is configurable", and the research gives no number.

    A quote is not called Low activity by a workspace that has never chosen a
    threshold, because that states a finding nobody asked for. Jev chose to ship
    no default at all, audit ``jev-20261013T111655-4412-15602``.
    """
    assert classify_activity(99, None) == ACTIVITY_UNCLASSIFIED
    assert classify_activity(0, None) == ACTIVITY_UNCLASSIFIED
    assert classify_activity(99, "") == ACTIVITY_UNCLASSIFIED


@pytest.mark.parametrize(
    "raw,expected", [(None, None), ("", None), ("   ", None), (7, 7), ("12", 12)]
)
def test_an_absent_threshold_is_none_and_a_present_one_is_read(raw, expected):
    """An unset threshold is an answer, not a missing value."""
    assert require_threshold(raw) == expected


@pytest.mark.parametrize("raw", [0, -1, MAX_THRESHOLD + 1, 3.5, True, "many"])
def test_a_threshold_outside_its_bounds_is_refused(raw):
    """Zero would classify every quote high; the ceiling classifies every quote low.

    Both are a workspace that made a mistake rather than one that made a choice,
    so the rule refuses instead of accepting a number that makes the chart say
    nothing.
    """
    with pytest.raises(QuoteEngagementRuleError):
        require_threshold(raw)


def test_the_threshold_bounds_are_the_ones_the_rules_enforce():
    """The vocabulary serves the bounds so the page cannot offer another pair."""
    payload = vocabulary()
    assert payload["limits"]["threshold_min"] == MIN_THRESHOLD == 1
    assert payload["limits"]["threshold_max"] == MAX_THRESHOLD == 1000
    assert payload["limits"]["threshold_default"] is None


@pytest.mark.parametrize("raw", [0, -5, 3651, "soon", False])
def test_a_window_outside_its_bounds_is_refused(raw):
    """The two follow-up windows are held to the same kind of bound."""
    with pytest.raises(QuoteEngagementRuleError):
        require_window(raw, "no_activity_after_days")


def test_a_window_reads_a_whole_number_of_days():
    assert require_window(None, "x") is None
    assert require_window("30", "x") == 30


# --------------------------------------------------------------------------- #
# The four counters
# --------------------------------------------------------------------------- #


def test_the_four_researched_counters_are_named_as_the_index_page_names_them():
    """Researched: "read the **Last Activity Date**, **Activity Count**, **Activity Count
    Since Last Communication** and **View Count** columns."

    Four counters, kept separate, with the researched label served beside each
    key so the page renders the researched name rather than an invented one.
    """
    labels = vocabulary()["counter_labels"]
    assert list(labels.values()) == [
        "Last Activity Date",
        "Activity Count",
        "Activity Count Since Last Communication",
        "View Count",
    ]
    assert len(labels) == 4


def test_the_counters_are_computed_from_the_whole_log():
    """Last activity is the newest event, not the last one written."""
    events = [
        {"event": "Quote published", "occurred_at": iso(NOW - timedelta(days=30))},
        {
            "event": "Quote viewed",
            "surface": SURFACE_QUOTE_LINK,
            "occurred_at": iso(NOW - timedelta(days=2)),
        },
        {"event": QUOTE_ACCEPTED, "occurred_at": iso(NOW - timedelta(days=1))},
    ]
    counters = summarise_events(events, now=NOW, threshold=10)
    assert counters[ACTIVITY_COUNT] == 3
    assert counters[VIEW_COUNT] == 1
    assert counters[LAST_ACTIVITY_AT] == iso(NOW - timedelta(days=1))
    assert counters["days_since_last_activity"] == 1
    assert counters["buyer_activity_count"] == 2
    assert counters["seller_activity_count"] == 1


def test_the_third_counter_reports_why_it_cannot_be_computed():
    """Researched: "connected email channel (for 'since last communication')" is the named
    source, and this product has no mail transport.

    The counter is ``None`` with the reason beside it rather than a count of
    outbound events, because an inbound buyer reply is a communication this
    workflow never records, and a number that overstates the silence produces a
    follow up that should not have been sent. Jev chose this over falling back to
    the last recorded send, audit ``jev-20261013T111835-5236-15033``.
    """
    events = [{"event": QUOTE_VIEWED, "surface": SURFACE_QUOTE_LINK, "occurred_at": iso(NOW)}]
    counters = summarise_events(events, now=NOW, threshold=10, email_channel_connected=False)
    assert counters[ACTIVITY_SINCE_COMMUNICATION] is None
    assert counters["since_last_communication_basis"] == SINCE_COMMUNICATION_UNAVAILABLE


def test_a_connected_channel_with_no_communication_still_has_no_baseline():
    """Researched: "Since" a communication that never happened has no answer either way."""
    events = [{"event": QUOTE_VIEWED, "surface": SURFACE_QUOTE_LINK, "occurred_at": iso(NOW)}]
    counters = summarise_events(events, now=NOW, threshold=10, email_channel_connected=True)
    assert counters[ACTIVITY_SINCE_COMMUNICATION] is None
    assert counters["since_last_communication_basis"] == SINCE_COMMUNICATION_NO_BASELINE


def test_a_connected_channel_with_a_baseline_counts_the_events_after_it():
    """With a channel and a baseline, the third counter is a number."""
    baseline = NOW - timedelta(days=5)
    events = [
        {"event": "Quote sent", "occurred_at": iso(NOW - timedelta(days=10))},
        {
            "event": QUOTE_VIEWED,
            "surface": SURFACE_QUOTE_LINK,
            "occurred_at": iso(NOW - timedelta(days=4)),
        },
        {"event": "Quote downloaded", "occurred_at": iso(NOW - timedelta(days=1))},
    ]
    counters = summarise_events(
        events,
        now=NOW,
        threshold=10,
        email_channel_connected=True,
        last_communication_at=iso(baseline),
    )
    assert counters[ACTIVITY_SINCE_COMMUNICATION] == 2
    assert counters["buyer_activity_since_last_communication"] == 2
    assert counters["since_last_communication_basis"] == "email_channel"


# --------------------------------------------------------------------------- #
# The engine, over a real audited database
# --------------------------------------------------------------------------- #


def test_recording_an_event_writes_one_row_and_updates_the_quote(service):
    """Researched: "Activity events are written server-side without user action."

    The counters live on the quote record and the event lives in this workflow's
    own collection, so the quote object a seller reads carries the researched
    columns and the timeline carries the history.
    """
    engine, db = service
    make_settings(engine)
    quote = make_quote(engine)

    result = record(engine, quote["id"], QUOTE_VIEWED, surface=SURFACE_QUOTE_LINK)

    assert result["engagement"][ACTIVITY_COUNT] == 1
    assert result["engagement"][VIEW_COUNT] == 1
    assert result["event"]["collection"] == "quote_engagement_event"
    stored = db.get(quote["id"])["data"]
    assert stored["engagement"][ACTIVITY_COUNT] == 1
    assert stored["engagement"]["activity_class"] == ACTIVITY_LOW


def test_the_counters_are_written_on_the_quote_and_not_on_a_typed_column(service):
    """No migration: the counters are ordinary JSON inside ``records.data``."""
    engine, db = service
    make_settings(engine)
    quote = make_quote(engine)
    record(engine, quote["id"], QUOTE_VIEWED)

    columns = {row["name"] for row in db._conn.execute("PRAGMA table_info(records)")}  # noqa: SLF001
    assert "engagement" not in columns
    assert "activity_count" not in columns
    assert isinstance(db.get(quote["id"])["data"]["engagement"], dict)


def test_the_activity_records_are_this_workflows_own_collection(service):
    """The ticket note: "This workflow can own its own activity records."

    Several pending workflows touch a quote and none claims the log, so the rows
    live in a collection of their own rather than in another feature's.
    """
    engine, db = service
    quote = make_quote(engine)
    record(engine, quote["id"], QUOTE_VIEWED)
    assert db.count_where("quote_engagement_event", {"quote_id": quote["id"]}) == 1


def test_the_counters_are_recomputed_not_incremented(service):
    """A counter that increments can disagree with the log it came from.

    Deleting the log and re-reading the counters is the only way to show which of
    the two this does, so the test does exactly that.
    """
    engine, db = service
    make_settings(engine)
    quote = make_quote(engine)
    record(engine, quote["id"], QUOTE_VIEWED)
    record(engine, quote["id"], "Quote downloaded")

    for row in db.list("quote_engagement_event", limit=50):
        db.delete(row["id"], actor="test", source=SOURCE)

    assert engine.engagement(quote["id"])["engagement"][ACTIVITY_COUNT] == 0


def test_the_written_audit_row_names_a_route_the_app_serves(service):
    """Every audit row names a path the app actually serves."""
    engine, db = service
    make_settings(engine)
    quote = make_quote(engine)
    record(engine, quote["id"], QUOTE_VIEWED)

    entries = db.audit(collection="quote_engagement_event", limit=10)
    assert entries
    for entry in entries:
        assert entry["source"].startswith(("POST /api/wf-097/", "PUT /api/wf-097/"))


def test_recording_an_event_on_a_quote_that_does_not_exist_is_refused(service):
    """The engine refuses rather than writing an event against nothing."""
    engine, _ = service
    with pytest.raises(QuoteEngagementRuleError, match="not found"):
        engine.record_event("quote_absent", event=QUOTE_VIEWED, source=SOURCE)


def test_an_event_in_the_future_is_refused(service):
    """A future event would make "days since last activity" negative.

    The page never renders a negative age, so storing one would be a page
    disagreeing with itself the moment it read the row back. The timestamp is
    measured against the real clock here rather than against the frozen moment,
    because that is the comparison the rule makes.
    """
    engine, _ = service
    quote = make_quote(engine)
    tomorrow = datetime.now(timezone.utc) + timedelta(days=1)
    with pytest.raises(QuoteEngagementRuleError, match="in the future"):
        record(engine, quote["id"], QUOTE_VIEWED, occurred_at=iso(tomorrow))


def test_an_unreadable_occurred_at_is_refused(service):
    """A timestamp this workflow cannot compare is refused, not stored raw."""
    engine, _ = service
    quote = make_quote(engine)
    with pytest.raises(QuoteEngagementRuleError, match="ISO timestamp"):
        record(engine, quote["id"], QUOTE_VIEWED, occurred_at="last Tuesday")


def test_two_rooms_with_two_thresholds_classify_independently(service):
    """A threshold is a workspace setting, so it is scoped to the room."""
    engine, _ = service
    high = engine.store.create(
        "quote", {"title": "A", "status": "PUBLISHED"}, room_id="room_1", source="seed"
    )
    low = engine.store.create(
        "quote", {"title": "B", "status": "PUBLISHED"}, room_id="room_2", source="seed"
    )
    engine.store.create(
        "quote_engagement_settings",
        {HIGH_ACTIVITY_THRESHOLD: 3},
        room_id="room_1",
        source="seed",
    )
    engine.store.create(
        "quote_engagement_settings",
        {HIGH_ACTIVITY_THRESHOLD: 50},
        room_id="room_2",
        source="seed",
    )
    for _ in range(5):
        record(engine, high["id"], QUOTE_VIEWED)
        record(engine, low["id"], QUOTE_VIEWED)

    assert engine.engagement(high["id"])["engagement"]["activity_class"] == ACTIVITY_HIGH
    assert engine.engagement(low["id"])["engagement"]["activity_class"] == ACTIVITY_LOW


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #


def test_a_workspace_with_no_settings_gets_no_threshold_and_no_windows(service):
    """Nothing is defaulted, so a fresh workspace says what it has not set."""
    engine, _ = service
    settings = engine.settings()
    assert settings[HIGH_ACTIVITY_THRESHOLD] is None
    assert settings[NO_ACTIVITY_AFTER_DAYS] is None
    assert settings[EXPIRING_WITHIN_DAYS] is None
    assert sorted(settings["unconfigured_keys"]) == sorted(
        [
            HIGH_ACTIVITY_THRESHOLD,
            EMAIL_CHANNEL_CONNECTED,
            LAST_COMMUNICATION_AT,
            NO_ACTIVITY_AFTER_DAYS,
            EXPIRING_WITHIN_DAYS,
        ]
    )
    assert settings["configured_keys"] == []


def test_a_settings_patch_merges_and_leaves_the_other_keys_alone(service):
    """An admin who sets the threshold does not lose their channel flag."""
    engine, _ = service
    make_settings(engine, **{EMAIL_CHANNEL_CONNECTED: True})

    engine.save_settings({HIGH_ACTIVITY_THRESHOLD: 5}, source="PUT /api/wf-097/settings")

    settings = engine.settings()
    assert settings[HIGH_ACTIVITY_THRESHOLD] == 5
    assert settings[EMAIL_CHANNEL_CONNECTED] is True
    assert settings[NO_ACTIVITY_AFTER_DAYS] == 30


def test_a_refused_settings_value_leaves_the_row_exactly_as_it_was(service):
    """Validation runs before the write, so there is no half-applied setting."""
    engine, _ = service
    make_settings(engine)

    with pytest.raises(QuoteEngagementRuleError):
        engine.save_settings(
            {HIGH_ACTIVITY_THRESHOLD: 0, NO_ACTIVITY_AFTER_DAYS: 45},
            source="PUT /api/wf-097/settings",
        )

    settings = engine.settings()
    assert settings[HIGH_ACTIVITY_THRESHOLD] == 10
    assert settings[NO_ACTIVITY_AFTER_DAYS] == 30


def test_an_unknown_setting_key_is_refused_by_name(service):
    """A typo in a settings key must not be stored as a field nobody reads."""
    engine, _ = service
    with pytest.raises(QuoteEngagementRuleError, match="unknown analysis setting"):
        engine.save_settings({"high_activity_treshold": 10}, source="PUT /api/wf-097/settings")


def test_a_settings_row_written_directly_is_held_to_the_same_bounds(service):
    """The generic records endpoint cannot route around the rules."""
    engine, _ = service
    engine.store.create(
        "quote_engagement_settings",
        {HIGH_ACTIVITY_THRESHOLD: 0},
        room_id="room_1",
        source="seed",
    )
    with pytest.raises(QuoteEngagementRuleError):
        engine.settings()


# --------------------------------------------------------------------------- #
# Reading quote fields a workspace named itself
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "field", ["expires_at", "expiration_date", "hs_expiration_date", "expiry_date"]
)
def test_the_expiration_date_is_read_from_whichever_field_the_quote_carries(field):
    """Record payloads are arbitrary JSON, so a reader tries each name in turn."""
    assert read_expiration({field: iso(NOW + timedelta(days=3))}) == NOW + timedelta(days=3)


def test_a_quote_with_no_expiration_date_reads_as_absent_rather_than_guessed():
    assert read_expiration({}) is None
    assert read_expiration(None) is None
    assert read_expiration({"expires_at": "sometime soon"}) is None


@pytest.mark.parametrize("field", ["owner", "rep", "assigned_to", "hs_owner_id"])
def test_the_owner_is_read_from_whichever_field_the_quote_carries(field):
    assert read_owner({field: "dana"}) == "dana"


@pytest.mark.parametrize("field", ["signing_status", "hs_signing_status", "signature_status"])
def test_the_signing_status_is_read_but_never_written(field):
    """Researched: "filter by ... **Signing status**" is a filter, not an ownership claim.

    The signing workflow owns writing this field. This one reads it so the index
    filter works, and there is no route here that sets it.
    """
    assert read_signing_status({field: "countersigned"}) == "countersigned"
    assert read_signing_status({}) is None


# --------------------------------------------------------------------------- #
# Follow-up recommendations
# --------------------------------------------------------------------------- #


def _row(quote_id, *, data=None, counters=None):
    return {"id": quote_id, "data": data or {}, "engagement": counters or {}}


def test_a_quote_with_no_activity_is_recommended_even_with_no_clock_start():
    """Researched: "quotes with no activity in a long time".

    A quote with no activity at all has no first day to count from, and treating
    that as "not long enough" would leave the least interesting quote off the
    list.
    """
    rows = [_row("q1", data={"status": "PUBLISHED"}, counters={LAST_ACTIVITY_AT: None})]
    out = recommend_follow_ups(rows, now=NOW, no_activity_after_days=30)
    assert [row["quote_id"] for row in out] == ["q1"]
    assert REASON_NO_ACTIVITY in out[0]["reason_codes"]
    assert out[0]["reasons"][0]["days_idle"] is None


def test_a_quiet_quote_is_recommended_once_it_passes_the_window():
    """A quote idle for exactly the configured window is already recommended."""
    counters = {LAST_ACTIVITY_AT: iso(NOW - timedelta(days=30))}
    rows = [_row("q1", data={"status": "PUBLISHED"}, counters=counters)]
    out = recommend_follow_ups(rows, now=NOW, no_activity_after_days=30)
    assert out[0]["reasons"][0]["days_idle"] == 30

    fresh = {LAST_ACTIVITY_AT: iso(NOW - timedelta(days=29))}
    assert (
        recommend_follow_ups(
            [_row("q1", data={"status": "PUBLISHED"}, counters=fresh)],
            now=NOW,
            no_activity_after_days=30,
        )
        == []
    )


def test_an_expiring_quote_is_recommended_inside_the_window():
    """Researched: "quotes that'll expire soon"."""
    rows = [
        _row(
            "q1",
            data={"status": "PUBLISHED", "expires_at": iso(NOW + timedelta(days=5))},
            counters={LAST_ACTIVITY_AT: iso(NOW)},
        )
    ]
    out = recommend_follow_ups(rows, now=NOW, expiring_within_days=14)
    assert REASON_EXPIRING_SOON in out[0]["reason_codes"]
    assert out[0]["reasons"][0]["days_remaining"] == 5


def test_a_quote_past_its_date_and_unaccepted_is_recommended():
    """The researched list says "e.g.", so the examples are the shape of it."""
    rows = [
        _row(
            "q1",
            data={"status": "PUBLISHED", "expires_at": iso(NOW - timedelta(days=3))},
            counters={LAST_ACTIVITY_AT: iso(NOW - timedelta(days=40))},
        )
    ]
    out = recommend_follow_ups(rows, now=NOW, no_activity_after_days=30, expiring_within_days=14)
    assert sorted(out[0]["reason_codes"]) == [REASON_EXPIRED_UNACCEPTED, REASON_NO_ACTIVITY]


def test_an_accepted_quote_is_never_recommended():
    """A quote the buyer accepted is not chased."""
    rows = [
        _row(
            "q1",
            data={"status": "ACCEPTED", "expires_at": iso(NOW - timedelta(days=30))},
            counters={LAST_ACTIVITY_AT: iso(NOW - timedelta(days=90))},
        )
    ]
    assert (
        recommend_follow_ups(rows, now=NOW, no_activity_after_days=1, expiring_within_days=1) == []
    )


def test_a_quote_with_no_status_is_not_recommended():
    """No status means no pipeline, so there is no send to renew and no date to miss."""
    rows = [_row("q1", data={}, counters={LAST_ACTIVITY_AT: iso(NOW - timedelta(days=90))})]
    assert recommend_follow_ups(rows, now=NOW, no_activity_after_days=1) == []


@pytest.mark.parametrize("status", ["DRAFT", "REJECTED", "CHANGES_REQUESTED"])
def test_a_quote_nothing_has_reached_is_not_a_follow_up(status):
    """The panel is "Analyze your published quotes" and the action is a follow up.

    A draft has never been published, shared or emailed, so emailing it is a first
    send rather than a follow up. A card that said "no activity in a long time"
    about a draft nobody has seen would be a card about nothing.
    """
    rows = [
        _row(
            "q1",
            data={"status": status, "expires_at": iso(NOW + timedelta(days=2))},
            counters={LAST_ACTIVITY_AT: iso(NOW - timedelta(days=90))},
        )
    ]
    assert (
        recommend_follow_ups(rows, now=NOW, no_activity_after_days=1, expiring_within_days=30) == []
    )


def test_a_recommendation_carries_the_quote_number_beside_the_title():
    """Two workspaces can carry two quotes with the same title.

    A follow-up list that shows only the title collapses them into one row, so the
    number is carried on every recommendation.
    """
    rows = [
        _row(
            "q1",
            data={
                "title": "Northwind",
                "quote_number": "Q-2026-021",
                "status": "PUBLISHED",
            },
            counters={LAST_ACTIVITY_AT: iso(NOW - timedelta(days=90))},
        )
    ]
    out = recommend_follow_ups(rows, now=NOW, no_activity_after_days=30)
    assert out[0]["quote_number"] == "Q-2026-021"


def test_an_unconfigured_window_recommends_nothing_rather_than_defaulting():
    """No invented window: a quiet panel says why it is quiet."""
    rows = [_row("q1", data={"status": "PUBLISHED"}, counters={LAST_ACTIVITY_AT: None})]
    assert recommend_follow_ups(rows, now=NOW) == []


def test_a_quote_can_carry_two_reasons_rather_than_being_collapsed_to_one():
    """A quote that is both quiet and about to expire is one row and two facts."""
    rows = [
        _row(
            "q1",
            data={"status": "PUBLISHED", "expires_at": iso(NOW + timedelta(days=2))},
            counters={LAST_ACTIVITY_AT: iso(NOW - timedelta(days=60))},
        )
    ]
    out = recommend_follow_ups(rows, now=NOW, no_activity_after_days=30, expiring_within_days=14)
    assert sorted(out[0]["reason_codes"]) == [REASON_EXPIRING_SOON, REASON_NO_ACTIVITY]


def test_no_reply_is_recommended_only_with_a_connected_channel_and_a_baseline(service):
    """The reason that measures buyer silence needs a communication to measure from."""
    engine, _ = service
    quote = make_quote(engine)
    baseline = iso(NOW - timedelta(days=20))
    make_settings(
        engine,
        **{EMAIL_CHANNEL_CONNECTED: True, LAST_COMMUNICATION_AT: baseline},
    )
    record(engine, quote["id"], "Quote sent", occurred_at=iso(NOW - timedelta(days=30)))
    # A seller action after the baseline, and no buyer event after it.
    record(engine, quote["id"], "Quote reassigned", occurred_at=iso(NOW - timedelta(days=5)))

    out = engine.follow_ups(now=NOW)
    assert out["entries"][0]["reason_codes"] == [REASON_NO_REPLY]


def test_no_reply_is_not_reported_with_the_channel_disconnected(service):
    """The same quote with no channel reports no silence at all."""
    engine, _ = service
    quote = make_quote(engine)
    make_settings(
        engine,
        **{
            EMAIL_CHANNEL_CONNECTED: False,
            LAST_COMMUNICATION_AT: iso(NOW - timedelta(days=20)),
            NO_ACTIVITY_AFTER_DAYS: 30,
        },
    )
    record(engine, quote["id"], "Quote sent", occurred_at=iso(NOW - timedelta(days=30)))

    out = engine.follow_ups(now=NOW)
    assert out["email_channel_connected"] is False
    assert all(REASON_NO_REPLY not in row["reason_codes"] for row in out["entries"])


# --------------------------------------------------------------------------- #
# The follow-up send
# --------------------------------------------------------------------------- #


def test_a_follow_up_records_a_new_quote_sent(service):
    """Researched: "generating a new `Quote sent` activity"."""
    engine, _ = service
    make_settings(engine)
    quote = make_quote(engine)
    record(engine, quote["id"], "Quote sent")

    result = engine.send_follow_up(
        quote["id"], actor="dana", source="POST /api/wf-097/follow-ups/q/send"
    )

    assert result["event"]["data"]["event"] == "Quote sent"
    assert result["engagement"][ACTIVITY_COUNT] == 2
    assert result["renewed"] is False
    assert result["opened"] == "send_quote_dialog"


def test_a_follow_up_after_an_expiry_is_reported_as_a_new_send(service):
    """(if previously expired/recalled) a new send"."""
    engine, _ = service
    make_settings(engine)
    quote = make_quote(engine)
    record(engine, quote["id"], "Quote expired")

    result = engine.send_follow_up(quote["id"], source="POST /api/wf-097/follow-ups/q/send")

    assert result["renewed"] is True
    assert "new send" in result["message"]


def test_a_follow_up_on_an_expired_status_quote_is_reported_as_a_new_send(service):
    """The status counts as well as the event, because either one is the evidence."""
    engine, _ = service
    make_settings(engine)
    quote = make_quote(engine, status="EXPIRED")

    result = engine.send_follow_up(quote["id"], source="POST /api/wf-097/follow-ups/q/send")

    assert result["renewed"] is True
    assert result["renewed_from"] == "EXPIRED"


def test_a_follow_up_does_not_restate_the_quote(service):
    """Whether an expired quote becomes live is the expiry workflow's decision.

    This workflow records the send and says whether it renewed; it does not move
    the quote's status, because that would be claiming work another workflow
    owns.
    """
    engine, db = service
    make_settings(engine)
    quote = make_quote(engine, status="EXPIRED")
    engine.send_follow_up(quote["id"], source="POST /api/wf-097/follow-ups/q/send")

    assert db.get(quote["id"])["data"]["status"] == "EXPIRED"


# --------------------------------------------------------------------------- #
# Pipeline overview and the chart
# --------------------------------------------------------------------------- #


def test_the_overview_reports_the_three_figures_the_panel_reads():
    """Researched: "read the value of high-activity quotes, quotes at risk of expiring, and
    the quote acceptance rate"."""
    rows = [
        _row(
            "high",
            data={
                "status": "PUBLISHED",
                "hs_quote_amount": 1000,
                "expires_at": iso(NOW + timedelta(days=5)),
            },
            counters={ACTIVITY_COUNT: 12, "activity_class": ACTIVITY_HIGH},
        ),
        _row(
            "low",
            data={
                "status": "PUBLISHED",
                "hs_quote_amount": 500,
                "expires_at": iso(NOW + timedelta(days=60)),
            },
            counters={ACTIVITY_COUNT: 1, "activity_class": ACTIVITY_LOW},
        ),
        _row(
            "won",
            data={
                "status": "ACCEPTED",
                "hs_quote_amount": 250,
                "expires_at": iso(NOW + timedelta(days=60)),
            },
            counters={ACTIVITY_COUNT: 4, "activity_class": ACTIVITY_LOW},
        ),
    ]
    out = pipeline_overview(rows, now=NOW, threshold=10, expiring_within_days=14)
    assert out["high_activity_quotes"] == 1
    assert out["high_activity_value"] == 1000.0
    assert out["quotes_at_risk"] == 1
    assert out["acceptance_rate"] == round(1 / 3, 4)
    assert out["threshold"] == 10


def test_the_overview_says_which_figures_it_could_not_compute():
    """A zero from a figure that could not be computed is not a measurement."""
    rows = [_row("q1", data={"status": "PUBLISHED"}, counters={ACTIVITY_COUNT: 3})]
    out = pipeline_overview(rows, now=NOW)
    assert out["threshold"] is None
    assert out["high_activity_quotes"] == 0
    assert "No threshold configured" in out["basis"]["high_activity"]
    assert "No expiry window configured" in out["basis"]["at_risk"]


def test_the_acceptance_rate_is_absent_on_an_empty_room_rather_than_zero():
    """Zero out of zero is not an acceptance rate of zero."""
    assert pipeline_overview([], now=NOW, threshold=10)["acceptance_rate"] is None


def test_the_chart_hides_expired_quotes_until_the_box_is_ticked():
    """Researched: "tick **Show older expired quotes**"."""
    rows = [
        _row(
            "live",
            data={"status": "PUBLISHED", "expires_at": iso(NOW + timedelta(days=10))},
            counters={ACTIVITY_COUNT: 5, "activity_class": ACTIVITY_LOW},
        ),
        _row(
            "gone",
            data={"status": "PUBLISHED", "expires_at": iso(NOW - timedelta(days=10))},
            counters={ACTIVITY_COUNT: 5, "activity_class": ACTIVITY_LOW},
        ),
    ]
    hidden = chart_points(rows, now=NOW, threshold=10, include_expired=False)
    assert [point["quote_id"] for point in hidden["points"]] == ["live"]
    assert hidden["expired_hidden"] == 1

    shown = chart_points(rows, now=NOW, threshold=10, include_expired=True)
    assert len(shown["points"]) == 2
    assert shown["expired_hidden"] == 0


def test_every_chart_point_carries_the_three_hover_facts():
    """; hover for name, expiration date and activity count"."""
    rows = [
        _row(
            "q1",
            data={"title": "Northwind", "expires_at": iso(NOW + timedelta(days=4))},
            counters={ACTIVITY_COUNT: 7, VIEW_COUNT: 3, "activity_class": ACTIVITY_LOW},
        )
    ]
    point = chart_points(rows, now=NOW, threshold=10)["points"][0]
    assert point["name"] == "Northwind"
    assert point["expires_at"] == iso(NOW + timedelta(days=4))
    assert point["activity_count"] == 7


def test_an_unclassified_point_carries_no_chart_colour():
    """A grey point beside two coloured ones would read as a third finding."""
    assert chart_tone(ACTIVITY_HIGH) == "success"
    assert chart_tone(ACTIVITY_LOW) == "warning"
    assert chart_tone(ACTIVITY_UNCLASSIFIED) is None


# --------------------------------------------------------------------------- #
# Timestamps
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "raw",
    [
        iso(NOW),
        "2026-06-01T12:00:00Z",
        "2026-06-01T12:00:00",
        "2026-06-01 12:00:00",
    ],
)
def test_a_timestamp_is_read_in_every_shape_the_store_and_a_team_write(raw):
    """The store writes an offset and a team writes a date. Both must parse."""
    assert parse_timestamp(raw) == NOW


def test_an_unreadable_timestamp_is_absent_rather_than_an_exception():
    """A bad date in a hand-written field must not break the quote's counters."""
    assert parse_timestamp("whenever") is None
    assert parse_timestamp("") is None
    assert parse_timestamp(None) is None
    assert parse_timestamp(True) is None


# --------------------------------------------------------------------------- #
# The edges the rules have to survive
# --------------------------------------------------------------------------- #


def test_a_datetime_is_read_as_a_moment_and_a_naive_one_as_utc():
    """The rules take datetimes as well as strings, because callers hold both.

    A naive moment is read as UTC rather than refused. Every timestamp the store
    writes carries an offset, so this only matters for a caller that built the
    value by hand, and refusing it there buys nothing and loses a comparison
    later.
    """
    assert parse_timestamp(NOW) == NOW
    assert parse_timestamp(NOW.replace(tzinfo=None)) == NOW
    assert iso(NOW.replace(tzinfo=None)) == iso(NOW)
    assert iso(None) is None


def test_an_unusable_event_counts_as_nothing_rather_than_raising():
    """``counts_as_view`` answers a question about a row, so it must total.

    It is called on every row of a log, including a row a teammate wrote through
    the generic records endpoint with a label this workflow never checked. A row
    like that is not a view, and asking the question about it must not raise.
    """
    assert counts_as_view("Quote telepathed") is False
    assert counts_as_view(None) is False


def test_a_count_that_is_not_a_number_classifies_as_zero():
    """A counter read from a hand-written field is not always a number.

    A malformed Activity Count is treated as no activity, which is the reading
    that cannot overstate how engaged a buyer is.
    """
    assert classify_activity("many", 10) == ACTIVITY_LOW
    assert classify_activity(None, 10) == ACTIVITY_LOW


def test_a_naive_now_is_read_as_utc_by_every_rule_that_takes_one():
    """Four rules accept a ``now``, and all four must agree on a naive one.

    Otherwise the same quote measures differently depending on which panel asked,
    and the pipeline overview and the follow-up list would disagree.
    """
    naive = NOW.replace(tzinfo=None)
    events = [{"event": QUOTE_VIEWED, "surface": SURFACE_QUOTE_LINK, "occurred_at": iso(NOW)}]
    assert summarise_events(events, now=naive, threshold=10)["days_since_last_activity"] == 0
    rows = [_row("q1", data={"status": "PUBLISHED"}, counters={LAST_ACTIVITY_AT: iso(NOW)})]
    assert recommend_follow_ups(rows, now=naive, no_activity_after_days=1) == []
    assert pipeline_overview(rows, now=naive, threshold=10)["quotes"] == 1
    assert chart_points(rows, now=naive, threshold=10)["count"] == 1


def test_an_expired_unaccepted_quote_is_counted_as_at_risk_in_the_overview():
    """The overview's at-risk figure covers the past as well as the next window.

    A quote that already expired and was never accepted is the seller problem the
    researched figure is describing, so it counts even though the "expires soon"
    window is a forward-looking number.
    """
    rows = [
        _row(
            "gone",
            data={"status": "PUBLISHED", "expires_at": iso(NOW - timedelta(days=5))},
            counters={ACTIVITY_COUNT: 3},
        ),
        _row(
            "won",
            data={"status": "ACCEPTED", "expires_at": iso(NOW - timedelta(days=5))},
            counters={ACTIVITY_COUNT: 3},
        ),
    ]
    out = pipeline_overview(rows, now=NOW, threshold=10, expiring_within_days=14)
    assert out["quotes_expired"] == 2
    assert out["quotes_at_risk"] == 1


def test_an_amount_that_is_not_a_number_contributes_nothing():
    """A value read from a hand-written field is not always a number either.

    A malformed amount is skipped rather than added as zero, so the high-activity
    value is the sum of the amounts it could read.
    """
    rows = [
        _row(
            "q1",
            data={"status": "PUBLISHED", "hs_quote_amount": "a lot"},
            counters={ACTIVITY_COUNT: 12, "activity_class": ACTIVITY_HIGH},
        )
    ]
    out = pipeline_overview(rows, now=NOW, threshold=10)
    assert out["quotes_with_a_value"] == 0
    assert out["total_value"] == 0.0


def test_advanced_filters_are_scoped_to_the_room_they_were_asked_about(service):
    """The generic filter query is room-scoped like every other read here.

    Without this, a filter typed in one room would match a quote in another and
    the page would show a row the workspace cannot see.
    """
    engine, _ = service
    mine = make_quote(engine, my_own_field="renewal")
    engine.store.create(
        "quote",
        {"title": "Other room", "status": "PUBLISHED", "my_own_field": "renewal"},
        room_id="room_2",
        source="seed",
    )

    found = engine.advanced({"my_own_field": "renewal"}, room_id="room_1")
    assert [row["id"] for row in found] == [mine["id"]]


def test_advanced_filters_are_not_narrowed_by_a_room_nobody_asked_about(service):
    """The room filter is a narrowing, so omitting it must not narrow anything.

    A caller that filters across every room is answering a different question
    from the one the index page asks, and it is a question the dashboard does
    ask. Scoping every read to one room would make that answer impossible.
    """
    engine, _ = service
    mine = make_quote(engine, my_own_field="renewal")
    theirs = engine.store.create(
        "quote",
        {"title": "Other room", "status": "PUBLISHED", "my_own_field": "renewal"},
        room_id="room_2",
        source="seed",
    )

    found = engine.advanced({"my_own_field": "renewal"})
    assert sorted(row["id"] for row in found) == sorted([mine["id"], theirs["id"]])


def test_advanced_filters_keep_a_page_full_in_a_room_that_holds_few_rows(service):
    """The room filter is applied after the index answers, so it cannot truncate.

    The store's JSON-path query cannot filter on the room column, so the page is
    filled from the index first and narrowed second. If it were the other way
    round, a workspace with two rooms and a small page would show fewer rows
    than the page asks for, and the missing rows would look like missing data.
    """
    engine, _ = service
    for index in range(4):
        make_quote(engine, title=f"Room one {index}", my_own_field="renewal")
    for index in range(9):
        engine.store.create(
            "quote",
            {"title": f"Room two {index}", "status": "PUBLISHED", "my_own_field": "renewal"},
            room_id="room_2",
            source="seed",
        )

    found = engine.advanced({"my_own_field": "renewal"}, room_id="room_1", limit=4)
    assert len(found) == 4
    assert {row["room_id"] for row in found} == {"room_1"}


def test_advanced_filters_with_no_conditions_return_only_the_room(service):
    """An empty filter means every quote, and the room still narrows it.

    A route that passes ``where`` through with nothing in it is the normal case
    for a page that has not been filtered yet, so the room must still be the only
    limit on what comes back.
    """
    engine, _ = service
    mine = make_quote(engine)
    engine.store.create(
        "quote", {"title": "Other room", "status": "PUBLISHED"}, room_id="room_2", source="seed"
    )

    found = engine.advanced({}, room_id="room_1")
    assert [row["id"] for row in found] == [mine["id"]]


def test_the_seed_counter_pass_tolerates_a_quote_that_is_gone(service):
    """One id in, one counter block out; a missing quote is skipped, not fatal.

    The helper is given the ids the seed created, so a missing one means a quote
    was deleted between the two passes. Raising there would take the whole
    seeder down for a row this workflow no longer needs.
    """
    engine, db = service
    from dsr.features import (
        wf097_track_buyer_engagement_on_a_shared_quote as plugin,  # noqa: PLC0415
    )

    quote = make_quote(engine, title="Written by another workflow")

    message = plugin._seed_counter_block(db, "room_1", [quote["id"], "quote_absent"])

    assert message == "counters recomputed on 1 seeded quotes"


# --------------------------------------------------------------------------- #
# The seed
# --------------------------------------------------------------------------- #


def test_the_seed_names_the_states_it_created_and_prints_on_cp1252(service):
    """Every character of the seeder's return value must encode on a Windows console.

    One RIGHTWARDS ARROW in one recovered feature broke the whole seeder, so the
    check is an actual ``encode`` call against the actual encoding rather than an
    assertion that the string has no non-ASCII characters.
    """
    engine, db = service
    from dsr.features.wf097_track_buyer_engagement_on_a_shared_quote import seed  # noqa: PLC0415

    summary = seed(db, {"room_ids": [("room_1", "Northwind")], "now": NOW})
    assert isinstance(summary, str)
    summary.encode("cp1252")
    assert "threshold of 10 activities" in summary
    assert "opened the PDF" in summary


def test_the_seed_produces_a_view_count_that_reflects_the_pdf_rule(service):
    """The seeded demo has to show the researched distinction, or it shows nothing.

    Without this the feature's one sourced exception is invisible on the page a
    reviewer opens, and a reviewer cannot tell it from a workflow that simply
    forgot to count PDF opens.
    """
    engine, db = service
    from dsr.features.wf097_track_buyer_engagement_on_a_shared_quote import seed  # noqa: PLC0415

    seed(db, {"room_ids": [("room_1", "Northwind")], "now": NOW})
    quotes = engine.list_quotes(room_id="room_1", limit=50)
    by_title = {row["data"]["title"]: row["engagement"] for row in quotes}

    engaged = by_title["Northwind enterprise renewal"]
    assert engaged[ACTIVITY_COUNT] > 10
    assert engaged[VIEW_COUNT] == 12
    assert engaged["activity_class"] == ACTIVITY_HIGH

    pdf_only = by_title["Contoso security review"]
    assert pdf_only[ACTIVITY_COUNT] == 5
    assert pdf_only[VIEW_COUNT] == 0
    assert pdf_only["pdf_opens_not_counted_as_views"] == 3


def test_the_seed_configures_a_threshold_so_the_demo_classifies_something(service):
    """No default ships, so a demo with no threshold would classify nothing."""
    engine, db = service
    from dsr.features.wf097_track_buyer_engagement_on_a_shared_quote import seed  # noqa: PLC0415

    seed(db, {"room_ids": [("room_1", "Northwind")], "now": NOW})
    settings = engine.settings(room_id="room_1")
    assert settings[HIGH_ACTIVITY_THRESHOLD] == 10
    assert settings[NO_ACTIVITY_AFTER_DAYS] == 30
    assert settings[EXPIRING_WITHIN_DAYS] == 14


def test_the_seed_returns_nothing_without_a_room_to_seed_into(service):
    """A seeder with no room returns ``None`` rather than raising."""
    engine, db = service
    from dsr.features.wf097_track_buyer_engagement_on_a_shared_quote import seed  # noqa: PLC0415

    assert seed(db, {"room_ids": [], "now": NOW}) is None


def test_the_seed_leaves_a_quote_another_feature_created_alone(service):
    """The counter pass touches only the ids this call created."""
    engine, db = service
    other = make_quote(engine, title="Written by another workflow")
    from dsr.features.wf097_track_buyer_engagement_on_a_shared_quote import seed  # noqa: PLC0415

    seed(db, {"room_ids": [("room_1", "Northwind")], "now": NOW})
    assert db.get(other["id"])["data"].get("engagement") is None


# --------------------------------------------------------------------------- #
# The architecture this workflow promised
# --------------------------------------------------------------------------- #


def test_the_domain_package_imports_nothing_but_its_own_modules_and_the_store():
    """The rules must be provably free of storage, so a reviewer can read them.

    ``rules`` and ``vocabulary`` import neither the store nor anything else in
    the package, and ``engagement`` imports the store and nothing else outside
    this package and the audited clock. Nothing here opens SQLite and nothing
    imports the app.
    """
    import ast

    package = Path(__file__).resolve().parents[1] / "dsr" / "quote_engagement"
    own = "dsr.quote_engagement"
    outside = {f"{own}.{module.stem}" for module in package.glob("*.py")} | {
        "dsr.store",
        "dsr.db.audited",
    }
    allowed_roots = {"__future__", "datetime", "typing"}

    for module in sorted(package.glob("*.py")):
        tree = ast.parse(module.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                names = [node.module]
            for name in names:
                if name.startswith("dsr."):
                    assert name in outside, f"{module.name} imports {name}"
                else:
                    assert name.split(".")[0] in allowed_roots, f"{module.name} imports {name}"

    engine_text = (package / "engagement.py").read_text(encoding="utf-8")
    assert "sqlite3" not in engine_text
    assert "dsr.api" not in engine_text


def test_the_rules_module_touches_no_storage():
    """The whole point of the split: each rule is testable without a database.

    The check reads the import statements rather than the whole file, because the
    module docstring explains what the rules deliberately do not touch and saying
    the word "store" there is the documentation working, not a violation.
    """
    import ast

    module = Path(__file__).resolve().parents[1] / "dsr" / "quote_engagement" / "rules.py"
    tree = ast.parse(module.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert imported == {"__future__", "datetime", "typing", "dsr.quote_engagement.vocabulary"}
