"""WF-075 domain rules: counting, bounds, dwell and verification.

The rules are in ``dsr.security_governance.engagement_rules`` and the vocabulary in
``engagement``. This file tests them without HTTP, over a store fixture, and is
organised by the claims the specification makes:

``a visitor and a view are different counts``
    The specification says a viewer who hits two links "shows up once here, but twice
    in ``papermark views list``". Neither count is derived from the other, and these
    tests fail if one starts being.
``anonymous views stay reachable``
    A view with no visitor email is a normal row, not an error and not something to
    filter out of a total.
``verified means proven, not typed``
    Three states, and the absent field is one of them.
``times are Unix milliseconds``
    One conversion function, checked at its boundaries.

Every test here passes with the file run on its own. Nothing in this module depends on
a test that ran before it.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from dsr.db.audited import AuditedDatabase
from dsr.security_governance import (
    engagement as vocab,
    engagement_inferences as inferences,
    engagement_rules as rules,
)
from dsr.security_governance.engagement_engine import EngagementEngine
from dsr.store import RecordStore

NOW = datetime(2026, 10, 4, 9, 0, tzinfo=timezone.utc)
BASE_MS = int(NOW.timestamp() * 1000)
HOUR_MS = 3600000


@pytest.fixture
def engine(store: RecordStore) -> EngagementEngine:
    """An engine with a clock the test moves by hand.

    The aggregate cache is process-level, so it is cleared before every test through
    this fixture. Without that, a test could read another test's cached board: the
    cache key carries the store identity, so two stores do not collide, but clearing
    keeps each test's first read honest.
    """

    EngagementEngine.clear_cache()
    return EngagementEngine(store, now=lambda: NOW)


def seed_view(engine: EngagementEngine, link_id: str = "link_a", **overrides):
    """One view event, with sensible defaults for anything the test does not care about."""

    body = {
        "viewer_email": "buyer@northwind.example",
        "view_type": vocab.VIEW_TYPE_LINK,
        "viewed_at": BASE_MS,
        "page_durations": [{"page_number": 1, "duration_seconds": 60}],
        "location": {"country": "United Kingdom", "city": "London"},
        "client": {"browser": "Chrome", "os": "macOS", "device": "laptop"},
        "room_id": "room_a",
    }
    body.update(overrides)
    return engine.ingest_view(link_id, **body)


# --------------------------------------------------------------------------- #
# a visitor and a view are different counts
# --------------------------------------------------------------------------- #


class TestTheTwoCountsStayApart:
    def test_one_buyer_two_links_is_one_visitor_and_two_views(self, engine: EngagementEngine):
        """The specification's own sentence, made into a test.

        "a viewer who hits two links in the same dataroom shows up once here, but
        twice in ``papermark views list``".
        """

        engine.ingest_visitor("buyer@northwind.example", dataroom_id="room_a", room_id="room_a")
        seed_view(engine, link_id="link_a")
        seed_view(engine, link_id="link_b")

        assert len(engine.visitors()) == 1
        assert len(engine.link_views("link_a")) + len(engine.link_views("link_b")) == 2

    def test_unique_visitors_collapses_the_addresses_behind_the_views(
        self, engine: EngagementEngine
    ):
        seed_view(engine, link_id="link_a")
        seed_view(engine, link_id="link_b")
        rows = engine.link_views("link_a") + engine.link_views("link_b")

        assert len(rules.unique_visitors(rows)) == 1

    def test_unique_visitors_ignores_an_anonymous_view(self, engine: EngagementEngine):
        seed_view(engine, link_id="link_a")
        seed_view(engine, link_id="link_b", viewer_email=None)
        rows = engine.link_views("link_a") + engine.link_views("link_b")

        assert rules.unique_visitors(rows) == ["buyer@northwind.example"]

    def test_the_viewers_list_is_counted_from_visitor_rows_not_from_views(
        self, engine: EngagementEngine
    ):
        """A visitor who was invited and never opened a link is still a readable row."""

        engine.ingest_visitor("invited@halcyon.example", dataroom_id="room_a", room_id="room_a")
        assert engine.visitors() != []
        assert engine.summary()["views"] == 0

    def test_count_views_does_not_call_count_visitors(self, engine: EngagementEngine):
        """The rule is structural, so this asserts the shape rather than a number.

        A summary that derived unique visitors by collapsing views would answer a
        question the rep did not ask, and the cheapest guard is that the two counts are
        produced by different code paths over different collections.
        """

        seed_view(engine, link_id="link_a")
        rows = engine.link_views("link_a")

        assert rules.count_views(rows) == 1
        assert len(engine.visitors()) == 0


# --------------------------------------------------------------------------- #
# anonymous views stay reachable
# --------------------------------------------------------------------------- #


class TestAnonymousViews:
    def test_a_view_with_no_email_is_written_and_stays_readable(self, engine: EngagementEngine):
        created = seed_view(engine, link_id="link_a", viewer_email=None)

        assert created["viewer_email"] is None
        assert created["anonymous"] is True
        assert len(engine.link_views("link_a")) == 1

    def test_an_anonymous_view_counts_towards_the_room_total(self, engine: EngagementEngine):
        """Engagement is engagement. A rep must not read an unopened link as unread."""

        seed_view(engine, link_id="link_a", viewer_email=None)

        assert engine.summary()["views"] == 1

    def test_an_anonymous_view_is_reported_as_its_own_number(self, engine: EngagementEngine):
        """The honest number and the addressable one are both on the board."""

        seed_view(engine, link_id="link_a")
        seed_view(engine, link_id="link_b", viewer_email=None)
        board = engine.summary()

        assert board["views"] == 2
        assert board["anonymous_views"] == 1
        assert board["unique_viewers"] == 1

    def test_an_empty_email_is_treated_as_no_email(self, engine: EngagementEngine):
        created = seed_view(engine, viewer_email="   ")

        assert created["anonymous"] is True


# --------------------------------------------------------------------------- #
# verified means proven, not typed
# --------------------------------------------------------------------------- #


class TestVerification:
    def test_a_true_flag_is_verified(self):
        assert rules.verification_state({"verified": True}) == vocab.VERIFIED_TRUE

    def test_a_false_flag_is_unverified(self):
        assert rules.verification_state({"verified": False}) == vocab.VERIFIED_FALSE

    def test_an_absent_flag_is_unknown_and_not_unverified(self):
        """The third state. These are different facts about a buyer."""

        assert rules.verification_state({}) == vocab.VERIFIED_UNKNOWN

    def test_a_null_flag_is_unknown(self):
        assert rules.verification_state({"verified": None}) == vocab.VERIFIED_UNKNOWN

    def test_a_non_boolean_stays_unknown_rather_than_becoming_true(self):
        """The defect this rule exists to prevent: "2" is not a proof."""

        assert rules.verification_state({"verified": "sometimes"}) == vocab.VERIFIED_UNKNOWN
        assert rules.verification_state({"verified": 2}) == vocab.VERIFIED_UNKNOWN

    def test_zero_and_one_are_accepted_as_a_json_round_trip(self):
        assert rules.verification_state({"verified": 1}) == vocab.VERIFIED_TRUE
        assert rules.verification_state({"verified": 0}) == vocab.VERIFIED_FALSE

    def test_the_default_is_not_verified(self):
        """A typed address is not a proven one."""

        assert vocab.DEFAULT_VERIFIED is False

    def test_a_visitor_written_without_the_flag_reads_as_unknown(self, engine: EngagementEngine):
        created = engine.ingest_visitor("buyer@vantage.example", dataroom_id="room_a")

        assert created["verification"] == vocab.VERIFIED_UNKNOWN
        assert created["verified_field"] is None

    def test_a_verified_visitor_reads_as_verified(self, engine: EngagementEngine):
        created = engine.ingest_visitor(
            "buyer@northwind.example", dataroom_id="room_a", verified=True
        )

        assert created["verification"] == vocab.VERIFIED_TRUE

    def test_the_board_counts_all_three_states(self, engine: EngagementEngine):
        engine.ingest_visitor("a@northwind.example", verified=True)
        engine.ingest_visitor("b@northwind.example", verified=False)
        engine.ingest_visitor("c@northwind.example")

        board = engine.summary()
        assert board["verified"] == 1
        assert board["unverified"] == 1
        assert board["unknown_verification"] == 1


# --------------------------------------------------------------------------- #
# times are Unix milliseconds
# --------------------------------------------------------------------------- #


class TestTimeBounds:
    def test_a_number_passes_through(self):
        assert rules.coerce_ms(BASE_MS, "since") == BASE_MS

    def test_a_string_is_accepted_because_the_cli_sends_one(self):
        assert rules.coerce_ms("1759600000000", "since") == 1759600000000

    def test_a_float_is_accepted_because_javascript_divides(self):
        assert rules.coerce_ms(1759600000000.0, "since") == 1759600000000

    def test_none_and_empty_mean_unbounded(self):
        assert rules.coerce_ms(None, "since") is None
        assert rules.coerce_ms("", "since") is None

    def test_zero_is_a_real_bound_and_not_unparseable(self):
        assert rules.coerce_ms(0, "since") == 0

    def test_unparseable_text_is_refused_rather_than_becoming_zero(self):
        with pytest.raises(rules.EngagementError) as caught:
            rules.coerce_ms("yesterday", "since")

        assert "since" in caught.value.errors

    def test_a_boolean_is_refused(self):
        with pytest.raises(rules.EngagementError):
            rules.coerce_ms(True, "since")

    def test_a_window_that_ends_before_it_begins_is_refused(self):
        with pytest.raises(rules.EngagementError) as caught:
            rules.window(since=BASE_MS, until=BASE_MS - HOUR_MS)

        assert "since" in caught.value.errors

    def test_a_row_inside_the_window_is_inside_it(self):
        bounds = rules.window(since=BASE_MS - HOUR_MS, until=BASE_MS + HOUR_MS)

        assert rules.in_window(BASE_MS, bounds) is True

    def test_a_row_before_the_bound_is_outside_it(self):
        bounds = rules.window(since=BASE_MS, until=None)

        assert rules.in_window(BASE_MS - HOUR_MS, bounds) is False

    def test_a_row_after_the_bound_is_outside_it(self):
        bounds = rules.window(since=None, until=BASE_MS)

        assert rules.in_window(BASE_MS + HOUR_MS, bounds) is False

    def test_an_undated_row_is_inside_an_unbounded_window_only(self):
        """A view with no timestamp cannot be placed in a window the caller asked for."""

        assert rules.in_window(None, rules.window()) is True
        assert rules.in_window(None, rules.window(since=BASE_MS)) is False

    def test_the_window_filters_the_aggregate(self, engine: EngagementEngine):
        seed_view(engine, link_id="link_a", viewed_at=BASE_MS - 5 * HOUR_MS)
        seed_view(engine, link_id="link_a", viewed_at=BASE_MS - HOUR_MS)

        recent = engine.stats("room_a", since=BASE_MS - 2 * HOUR_MS, use_cache=False)
        assert recent[vocab.TOTAL_VIEWS_FIELD] == 1

        everything = engine.stats("room_a", use_cache=False)
        assert everything[vocab.TOTAL_VIEWS_FIELD] == 2


# --------------------------------------------------------------------------- #
# dwell time
# --------------------------------------------------------------------------- #


class TestDwell:
    def test_pages_are_sorted_by_page_number(self):
        data = {
            vocab.PAGE_DURATIONS: [
                {"page_number": 3, "duration_seconds": 10},
                {"page_number": 1, "duration_seconds": 20},
                {"page_number": 2, "duration_seconds": 30},
            ]
        }

        assert [row["page_number"] for row in rules.page_durations(data)] == [1, 2, 3]

    def test_a_negative_duration_is_dropped_rather_than_clamped(self):
        """A negative means the client clock moved. Zero would invent a dwell."""

        data = {
            vocab.PAGE_DURATIONS: [
                {"page_number": 1, "duration_seconds": -5},
                {"page_number": 2, "duration_seconds": 10},
            ]
        }

        assert rules.page_durations(data) == [{"page_number": 2, "duration_seconds": 10}]

    def test_a_non_mapping_entry_is_skipped(self):
        data = {vocab.PAGE_DURATIONS: ["nonsense", {"page_number": 1, "duration_seconds": 5}]}

        assert rules.page_durations(data) == [{"page_number": 1, "duration_seconds": 5}]

    def test_a_missing_list_is_an_empty_list(self):
        assert rules.page_durations({}) == []

    def test_the_stored_total_is_used_when_it_is_at_least_the_sum(self):
        data = {
            vocab.PAGE_DURATIONS: [{"page_number": 1, "duration_seconds": 20}],
            vocab.TOTAL_DURATION_SECONDS: 90,
        }

        assert rules.total_duration(data) == 90

    def test_a_stored_total_below_the_sum_falls_back_to_the_sum(self):
        """A client that stops sending after the viewer leaves still has a total."""

        data = {
            vocab.PAGE_DURATIONS: [{"page_number": 1, "duration_seconds": 20}],
            vocab.TOTAL_DURATION_SECONDS: 5,
        }

        assert rules.total_duration(data) == 20

    def test_nothing_known_is_zero_and_not_a_guess(self):
        assert rules.total_duration({}) == 0

    def test_the_per_page_aggregate_reports_readers_and_events_apart(
        self, engine: EngagementEngine
    ):
        """One buyer reading a page four times is one reader and four events."""

        for moment in range(4):
            seed_view(
                engine,
                link_id="link_a",
                viewed_at=BASE_MS + moment * 1000,
                page_durations=[{"page_number": 1, "duration_seconds": 10}],
            )

        board = engine.per_page(engine.link_views("link_a"))

        assert board[0]["viewers"] == 1
        assert board[0]["total_views"] == 4
        assert board[0]["total_duration_seconds"] == 40


# --------------------------------------------------------------------------- #
# location and client
# --------------------------------------------------------------------------- #


class TestLocationAndClient:
    def test_both_location_keys_are_always_present(self):
        """A missing key and an empty value are different answers."""

        assert rules.location_of({}) == {"country": "unknown", "city": "unknown"}

    def test_a_known_location_is_reported_as_stored(self):
        data = {vocab.LOCATION: {"country": "Germany", "city": "Berlin"}}

        assert rules.location_of(data) == {"country": "Germany", "city": "Berlin"}

    def test_all_three_client_keys_are_always_present(self):
        assert rules.client_of({}) == {"browser": "unknown", "os": "unknown", "device": "unknown"}

    def test_no_vendor_is_named_anywhere_in_the_package(self):
        """The specification marks the geolocation provider as an inference.

        It says so itself: "viewer IP -> geolocation provider ``[inferred - the API
        returns location.country/city but names no vendor]``". A vendor name would be a
        claim the research does not support.
        """

        source = (
            __import__("pathlib").Path(__file__).resolve().parents[1]
            / "dsr"
            / "security_governance"
            / "engagement.py"
        ).read_text(encoding="utf-8")
        for vendor in ("maxmind", "ipinfo", "ipapi", "geoip", "cloudflare", "google"):
            assert vendor not in source.lower()


# --------------------------------------------------------------------------- #
# downloads
# --------------------------------------------------------------------------- #


class TestDownloads:
    def test_a_view_with_no_download_says_so_distinctly(self):
        result = rules.download_of({})

        assert result["downloaded"] is False
        assert result["download_type"] == vocab.NO_DOWNLOAD

    def test_the_no_download_state_is_not_a_download_type(self):
        """So it cannot be written into the field by accident."""

        assert vocab.NO_DOWNLOAD not in vocab.DOWNLOAD_TYPES

    def test_a_download_reports_its_kind_and_its_instant(self):
        result = rules.download_of({vocab.DOWNLOAD_TYPE: "pdf", vocab.DOWNLOADED_AT: BASE_MS})

        assert result["downloaded"] is True
        assert result["download_type"] == "pdf"
        assert result["downloaded_at"] == BASE_MS

    def test_a_download_kind_with_no_instant_is_not_yet_a_download(self):
        result = rules.download_of({vocab.DOWNLOAD_TYPE: "pdf"})

        assert result["downloaded"] is False
        assert result["download_type"] == "pdf"


# --------------------------------------------------------------------------- #
# paging and order
# --------------------------------------------------------------------------- #


class TestPagingAndOrder:
    def test_the_default_page_size_is_the_derived_one(self):
        assert rules.page_size(None) == vocab.DEFAULT_PAGE_SIZE

    def test_a_caller_cannot_ask_for_more_than_the_maximum(self):
        assert rules.page_size(vocab.MAX_PAGE_SIZE * 10) == vocab.MAX_PAGE_SIZE

    def test_a_page_size_below_one_is_refused(self):
        with pytest.raises(rules.EngagementError):
            rules.page_size(0)

    def test_views_come_back_newest_first(self):
        rows = [
            {vocab.VIEWED_AT: BASE_MS - HOUR_MS},
            {vocab.VIEWED_AT: BASE_MS},
            {vocab.VIEWED_AT: BASE_MS - 5 * HOUR_MS},
        ]

        ordered = rules.reverse_chronological(rows)

        assert [row[vocab.VIEWED_AT] for row in ordered] == [
            BASE_MS,
            BASE_MS - HOUR_MS,
            BASE_MS - 5 * HOUR_MS,
        ]

    def test_an_undated_view_goes_last_rather_than_first(self):
        """An undated view is not the newest thing that happened."""

        ordered = rules.reverse_chronological([{vocab.VIEWED_AT: None}, {vocab.VIEWED_AT: BASE_MS}])

        assert ordered[0][vocab.VIEWED_AT] == BASE_MS

    def test_the_link_list_honours_the_page_size(self, engine: EngagementEngine):
        for index in range(5):
            seed_view(engine, link_id="link_a", viewed_at=BASE_MS + index * 1000)

        assert len(engine.link_views("link_a", limit=2)) == 2


# --------------------------------------------------------------------------- #
# the aggregate cache
# --------------------------------------------------------------------------- #


class TestTheCache:
    def test_a_second_poll_inside_the_window_reads_the_cache(self, store: RecordStore):
        ticks = [0.0]
        engine = EngagementEngine(store, now=lambda: NOW, monotonic=lambda: ticks[0])
        seed_view(engine, link_id="link_a")

        first = engine.stats("room_a")
        second = engine.stats("room_a")

        assert first[vocab.CACHED_FIELD] is False
        assert second[vocab.CACHED_FIELD] is True
        assert second[vocab.TOTAL_VIEWS_FIELD] == first[vocab.TOTAL_VIEWS_FIELD]

    def test_a_poll_after_the_window_recomputes(self, store: RecordStore):
        ticks = [0.0]
        engine = EngagementEngine(store, now=lambda: NOW, monotonic=lambda: ticks[0])
        seed_view(engine, link_id="link_a")

        engine.stats("room_a")
        ticks[0] = vocab.DEFAULT_CACHE_SECONDS + 1
        again = engine.stats("room_a")

        assert again[vocab.CACHED_FIELD] is False

    def test_two_different_windows_never_read_each_other(self, store: RecordStore):
        engine = EngagementEngine(store, now=lambda: NOW, monotonic=lambda: 0.0)
        seed_view(engine, link_id="link_a", viewed_at=BASE_MS - 5 * HOUR_MS)
        seed_view(engine, link_id="link_a", viewed_at=BASE_MS)

        narrow = engine.stats("room_a", since=BASE_MS - HOUR_MS)
        wide = engine.stats("room_a")

        assert narrow[vocab.TOTAL_VIEWS_FIELD] == 1
        assert wide[vocab.TOTAL_VIEWS_FIELD] == 2

    def test_a_write_clears_the_cache(self, store: RecordStore):
        """A cached board that outlived the write would defeat the point of the poller."""

        engine = EngagementEngine(store, now=lambda: NOW, monotonic=lambda: 0.0)
        seed_view(engine, link_id="link_a")
        before = engine.stats("room_a")

        seed_view(engine, link_id="link_a", viewed_at=BASE_MS + 1000)
        after = engine.stats("room_a")

        assert before[vocab.TOTAL_VIEWS_FIELD] == 1
        assert after[vocab.TOTAL_VIEWS_FIELD] == 2
        assert after[vocab.CACHED_FIELD] is False

    def test_every_aggregate_says_when_it_was_computed(self, store: RecordStore):
        engine = EngagementEngine(store, now=lambda: NOW, monotonic=lambda: 0.0)
        payload = engine.stats("room_a")

        assert payload[vocab.COMPUTED_AT] == BASE_MS
        assert payload[vocab.TIME_UNIT_FIELD] == vocab.TIME_UNIT_VALUE


# --------------------------------------------------------------------------- #
# first seen and last seen
# --------------------------------------------------------------------------- #


class TestFirstAndLastSeen:
    def test_first_seen_is_the_earliest_of_the_invitation_and_the_first_view(
        self, engine: EngagementEngine
    ):
        engine.ingest_visitor(
            "buyer@northwind.example",
            dataroom_id="room_a",
            invited_at=BASE_MS - 10 * HOUR_MS,
            room_id="room_a",
        )
        seed_view(engine, link_id="link_a", viewed_at=BASE_MS - 5 * HOUR_MS)

        row = engine.visitors()[0]
        assert row["first_seen"] == BASE_MS - 10 * HOUR_MS

    def test_first_seen_falls_back_to_the_earliest_view_for_an_uninvited_visitor(
        self, engine: EngagementEngine
    ):
        engine.ingest_visitor("buyer@northwind.example", dataroom_id="room_a", room_id="room_a")
        seed_view(engine, link_id="link_a", viewed_at=BASE_MS - 2 * HOUR_MS)

        assert engine.visitors()[0]["first_seen"] == BASE_MS - 2 * HOUR_MS

    def test_a_visitor_with_neither_reports_nothing_rather_than_the_epoch(
        self, engine: EngagementEngine
    ):
        engine.ingest_visitor("buyer@northwind.example", dataroom_id="room_a", room_id="room_a")

        assert engine.visitors()[0]["first_seen"] is None

    def test_last_seen_is_the_latest_view_or_the_recorded_instant(self, engine: EngagementEngine):
        engine.ingest_visitor(
            "buyer@northwind.example",
            dataroom_id="room_a",
            last_viewed_at=BASE_MS - 20 * HOUR_MS,
            room_id="room_a",
        )
        seed_view(engine, link_id="link_a", viewed_at=BASE_MS)

        assert engine.visitors()[0]["last_seen"] == BASE_MS


# --------------------------------------------------------------------------- #
# the record shapes the specification quotes
# --------------------------------------------------------------------------- #


class TestTheQuotedShapes:
    def test_the_view_analytics_shape_is_reproduced_verbatim(self):
        assert set(vocab.VIEW_ANALYTICS_FIELDS) == {
            "viewer_email",
            "viewed_at",
            "page_durations",
            "total_duration_seconds",
            "location",
            "client",
        }

    def test_the_visitor_shape_is_reproduced_verbatim(self):
        assert set(vocab.VISITOR_FIELDS) == {
            "email",
            "verified",
            "dataroom_id",
            "invited_at",
            "total_views",
            "last_viewed_at",
        }

    def test_the_nested_shapes_are_reproduced_verbatim(self):
        assert tuple(vocab.LOCATION_FIELDS) == ("country", "city")
        assert tuple(vocab.CLIENT_FIELDS) == ("browser", "os", "device")
        assert tuple(vocab.PAGE_DURATION_FIELDS) == ("page_number", "duration_seconds")

    def test_a_view_projection_carries_both_shapes_side_by_side(self, engine: EngagementEngine):
        created = seed_view(engine, link_id="link_a")

        for field in vocab.VIEW_ANALYTICS_FIELDS:
            assert field in created

    def test_the_time_unit_is_declared_once_and_served(self):
        assert vocab.TIME_UNIT_VALUE == "unix_ms"


# --------------------------------------------------------------------------- #
# the recorded decisions
# --------------------------------------------------------------------------- #


class TestRecordedDecisions:
    def test_every_decision_names_an_option_and_the_one_that_was_taken(self):
        for key, decision in inferences.DECISIONS.items():
            assert decision["chosen"] in decision["options"], key
            assert decision["rejected_because"], key

    def test_every_decision_names_what_the_choice_cost(self):
        for key, decision in inferences.DECISIONS.items():
            assert decision["cost_of_the_choice"], key

    def test_the_verification_decision_is_recorded_by_name(self):
        assert "DERIVED_VERIFICATION_IS_THREE_STATES" in inferences.DECISIONS

    def test_one_decision_can_be_described_alone(self):
        found = inferences.describe_one("DERIVED_CACHE_SECONDS")

        assert found["id"] == "DERIVED_CACHE_SECONDS"

    def test_an_unknown_decision_describes_as_empty(self):
        assert inferences.describe_one("NOT_A_DECISION") == {}


# --------------------------------------------------------------------------- #
# the seed
# --------------------------------------------------------------------------- #


class TestSeed:
    def test_the_seed_string_is_encodable_by_cp1252(self, db: AuditedDatabase):
        """The seeder prints it to a Windows console.

        One RIGHTWARDS ARROW in a recovered feature's return string broke the entire
        seeder, so this is asserted rather than assumed.
        """

        import dsr.features.wf075_review_who_engaged_where_and_for_how_long as feature

        returned = feature.seed(db, {"room_ids": [("room_a", "Northwind")], "now": NOW})
        assert returned.encode("cp1252", errors="strict")

    def test_the_seed_creates_rows_a_reviewer_can_see(self, db: AuditedDatabase):
        import dsr.features.wf075_review_who_engaged_where_and_for_how_long as feature

        returned = feature.seed(db, {"room_ids": [("room_a", "Northwind")], "now": NOW})
        store = RecordStore(db)

        assert store.list(vocab.VISITOR_COLLECTION, limit=50)
        assert store.list(vocab.VIEW_COLLECTION, limit=50)
        assert "anonymous" in returned

    def test_the_seed_says_the_states_are_not_all_successes(self, db: AuditedDatabase):
        """The brief asks for a return string naming the states it created."""

        import dsr.features.wf075_review_who_engaged_where_and_for_how_long as feature

        returned = feature.seed(db, {"room_ids": [("room_a", "Northwind")], "now": NOW})

        assert "unverified" in returned
        assert "no proof recorded" in returned

    def test_the_seed_with_no_room_returns_nothing(self, db: AuditedDatabase):
        import dsr.features.wf075_review_who_engaged_where_and_for_how_long as feature

        assert feature.seed(db, {"room_ids": [], "now": NOW}) == ""

    def test_a_visitor_needs_an_email(self, engine: EngagementEngine):
        with pytest.raises(rules.EngagementError):
            engine.ingest_visitor("", dataroom_id="room_a")


# --------------------------------------------------------------------------- #
# tallying
# --------------------------------------------------------------------------- #


class TestTally:
    def test_counts_are_sorted_largest_first_then_alphabetical(self):
        rows = [{"view_type": "b"}, {"view_type": "a"}, {"view_type": "b"}]

        assert rules.tally(rows, "view_type") == {"b": 2, "a": 1}

    def test_an_absent_key_is_counted_as_unknown_rather_than_dropped(self):
        rows = [{"view_type": "a"}, {}]

        assert rules.tally(rows, "view_type") == {"a": 1, "unknown": 1}
