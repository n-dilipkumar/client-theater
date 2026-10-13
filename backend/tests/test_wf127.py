"""WF-127 domain tests: the branching demo, the event stream, the heatmap, the
roll-up, the DQL score, and the stakeholder discovery.

These drive :mod:`dsr.demo_analytics` directly, over a temporary database, with
no HTTP client. The HTTP surface has its own module, ``test_wf127_http.py``.

Every test names the rule it protects, quoting
``docs/research/digital-sales-room-workflows/wf/WF-127.md`` where the research is
the source. The clock is frozen at :data:`NOW`, so no test depends on today's
date or on a weekday that will pass - the brief calls that a time bomb.

The database is a fresh one per test, through the shared ``store`` fixture, so
this file passes alone and under ``pytest-xdist`` in any order.
"""

from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path

import dsr.demo_analytics as domain
import pytest
from dsr.demo_analytics import (
    DEFAULT_CLICK_PATH,
    DEFAULT_DQL_THRESHOLD,
    DEMO_COLLECTION,
    DQL_MAX,
    DQL_WEIGHTS,
    EVENT_COLLECTION,
    HEATMAP_BUCKET_HOURS,
    LEVELS,
    METRIC_NAMES,
    SHARE_COLLECTION,
    STAKEHOLDER_COLLECTION,
    DemoAnalyticsBook,
    DemoArchived,
    DemoNotFound,
    DemoValidationError,
    EventRefused,
    ShareRefused,
    branch_options,
    buyer_group,
    dql_score,
    heatmap,
    metrics,
    normalise_demo,
    normalise_event,
    normalise_nodes,
    normalise_share,
    reach_counts,
    resolve_path,
    rollup,
    section_counts,
    stakeholder_role,
)
from dsr.store import RecordStore

NOW = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)

DOMAIN_DIR = Path(domain.__file__).parent
FEATURE_MODULE = (
    Path(domain.__file__).parent.parent
    / "features"
    / "wf127_interactive_demo_sandbox_analytics_with_stakeholder.py"
)


# --------------------------------------------------------------------------- #
# Fixtures and helpers
# --------------------------------------------------------------------------- #


@pytest.fixture()
def room(store: RecordStore) -> dict:
    return store.create(
        "room", {"name": "Northwind", "account": "Northwind"}, actor="dana", source="test"
    )


@pytest.fixture()
def book(store: RecordStore, room: dict) -> DemoAnalyticsBook:
    """The book with a frozen clock, so every timestamp is exact."""
    return DemoAnalyticsBook(store, now=lambda: NOW)


def demo_payload(**overrides) -> dict:
    payload = {
        "title": "Northwind onboarding sandbox",
        "demo_type": "sandbox",
        "team": "Enterprise",
        "owner": "dana",
    }
    payload.update(overrides)
    return payload


def make_demo(book: DemoAnalyticsBook, room_id: str, **overrides) -> dict:
    return book.create_demo(
        room_id, demo_payload(**overrides), actor="dana", source="POST /api/wf-127/demos"
    )


def a_view(viewer: str, **overrides) -> dict:
    payload = {"viewer": viewer, "event": "view", "occurred_at": NOW.isoformat()}
    payload.update(overrides)
    return payload


def domain_feature_seed(db, context):
    """Call the feature module's ``seed`` without importing the feature lazily."""
    import importlib

    module = importlib.import_module(
        "dsr.features.wf127_interactive_demo_sandbox_analytics_with_stakeholder"
    )
    return module.seed(db, context)


# --------------------------------------------------------------------------- #
# The vocabulary and the inferences
# --------------------------------------------------------------------------- #


class TestThePublishedContract:
    def test_the_metric_names_are_the_researched_vocabulary(self):
        assert METRIC_NAMES == (
            "demos_sent",
            "stakeholders_discovered",
            "discovery_rate",
            "views",
            "view_rate",
            "features_selected",
            "docs_downloaded",
        )

    def test_the_six_levels_are_in_the_researched_order(self):
        assert LEVELS == (
            "buyer_group",
            "top_level",
            "team",
            "individual",
            "recently_shared",
            "all_demos",
        )

    def test_the_vocabulary_marks_which_half_is_sourced(self):
        published = domain.describe_vocabulary()
        assert published["sourced"]["metric_names_source"]
        assert published["defaults"]["dql_threshold"] == DEFAULT_DQL_THRESHOLD
        assert published["collections"]["demos"] == DEMO_COLLECTION

    def test_the_inferences_register_names_the_open_decisions(self):
        published = domain.describe_inferences()
        ids = {entry["id"] for entry in published["inferences"]}
        assert {
            "stakeholder_discovery_attribution",
            "discovery_rate_denominator",
            "dql_formula",
            "heatmap_axes",
            "no_crm_write",
        } <= ids
        for entry in published["inferences"]:
            assert entry["decision"] and entry["why"] and entry["change_it_by"]


# --------------------------------------------------------------------------- #
# The branching demo
# --------------------------------------------------------------------------- #


class TestTheClickPath:
    def test_a_demo_with_no_tree_is_built_from_the_default_branching_template(self):
        data = normalise_demo({"title": "Built"}, now=NOW)
        assert [node["id"] for node in data["nodes"]] == [node["id"] for node in DEFAULT_CLICK_PATH]
        assert data["entry"] == "overview"
        # The template branches: the entry has two successors.
        assert len(branch_options(data, "overview")) == 2

    def test_a_demo_needs_a_title(self):
        with pytest.raises(DemoValidationError) as refused:
            normalise_demo({}, now=NOW)
        assert refused.value.context["field"] == "title"

    def test_an_unknown_demo_type_is_refused(self):
        with pytest.raises(DemoValidationError) as refused:
            normalise_demo({"title": "x", "demo_type": "hologram"}, now=NOW)
        assert refused.value.context["field"] == "demo_type"

    def test_an_unknown_state_is_refused(self):
        with pytest.raises(DemoValidationError) as refused:
            normalise_demo({"title": "x", "state": "live"}, now=NOW)
        assert refused.value.context["field"] == "state"

    def test_a_duplicate_node_id_is_refused(self):
        with pytest.raises(DemoValidationError):
            normalise_nodes([{"id": "a"}, {"id": "a"}])

    def test_a_branch_to_an_unknown_node_is_refused(self):
        with pytest.raises(DemoValidationError) as refused:
            normalise_nodes([{"id": "a", "next": ["ghost"]}])
        assert refused.value.context["ref"] == "ghost"

    def test_an_unknown_node_kind_is_refused(self):
        with pytest.raises(DemoValidationError):
            normalise_nodes([{"id": "a", "kind": "portal"}])

    def test_an_entry_that_is_not_a_node_is_refused(self):
        with pytest.raises(DemoValidationError) as refused:
            normalise_demo({"title": "x", "entry": "nowhere"}, now=NOW)
        assert refused.value.context["field"] == "entry"

    def test_a_click_path_may_arrive_as_lines(self):
        nodes = normalise_nodes("a\nb")
        assert [node["id"] for node in nodes] == ["a", "b"]

    def test_a_reachable_path_resolves(self):
        demo = normalise_demo({"title": "x"}, now=NOW)
        assert resolve_path(demo, ["dashboard", "export"]) == ["overview", "dashboard", "export"]

    def test_a_step_the_tree_lacks_is_refused(self):
        demo = normalise_demo({"title": "x"}, now=NOW)
        with pytest.raises(EventRefused) as refused:
            resolve_path(demo, ["export"])
        assert refused.value.context["at"] == "overview"

    def test_a_branch_option_names_its_label(self):
        demo = normalise_demo({"title": "x"}, now=NOW)
        labels = {option["id"]: option["label"] for option in branch_options(demo, "overview")}
        assert labels["dashboard"] == "Dashboard"

    def test_branch_options_for_an_unknown_node_are_refused(self):
        demo = normalise_demo({"title": "x"}, now=NOW)
        with pytest.raises(DemoValidationError):
            branch_options(demo, "ghost")


# --------------------------------------------------------------------------- #
# The viewer event stream
# --------------------------------------------------------------------------- #


class TestTheEvent:
    def test_an_alias_resolves_to_the_stored_event_name(self):
        assert (
            normalise_event({"viewer": "a@b.example", "event": "open"}, now=NOW)["event"] == "view"
        )
        assert (
            normalise_event({"viewer": "a@b.example", "event": "download"}, now=NOW)["event"]
            == "doc_downloaded"
        )

    def test_an_unknown_event_is_refused(self):
        with pytest.raises(EventRefused) as refused:
            normalise_event({"viewer": "a@b.example", "event": "scroll"}, now=NOW)
        assert refused.value.context["field"] == "event"

    def test_a_viewer_must_be_an_email(self):
        with pytest.raises(EventRefused) as refused:
            normalise_event({"viewer": "not-an-address", "event": "view"}, now=NOW)
        assert refused.value.context["field"] == "viewer"

    def test_a_section_selection_needs_a_section(self):
        with pytest.raises(EventRefused) as refused:
            normalise_event({"viewer": "a@b.example", "event": "section"}, now=NOW)
        assert refused.value.context["field"] == "section"

    def test_the_email_alias_resolves_to_the_viewer(self):
        event = normalise_event({"email": "Buyer@Northwind.example", "event": "view"}, now=NOW)
        assert event["viewer"] == "buyer@northwind.example"

    def test_a_share_reference_naming_the_viewer_is_dropped(self):
        event = normalise_event(
            {"viewer": "a@b.example", "event": "view", "shared_from": "A@b.example"}, now=NOW
        )
        assert event["shared_from"] is None

    def test_an_internal_preview_is_marked(self):
        event = normalise_event(
            {"viewer": "dana@b.example", "event": "view", "is_internal": True}, now=NOW
        )
        assert event["is_internal"] is True

    @pytest.mark.parametrize(
        "key", ["body", "content", "form_values", "recording", "screen_capture"]
    )
    def test_a_document_body_or_recording_is_refused_by_name(self, key):
        with pytest.raises(EventRefused) as refused:
            normalise_event({"viewer": "a@b.example", key: "secret"}, now=NOW)
        assert refused.value.context["field"] == key

    def test_an_absent_timestamp_defaults_to_the_clock(self):
        event = normalise_event({"viewer": "a@b.example"}, now=NOW)
        assert event["occurred_at"] == NOW.isoformat()


class TestTheShare:
    def test_recipients_are_normalised_and_deduplicated(self):
        share = normalise_share(
            {"recipients": ["Buyer@Northwind.example", "buyer@northwind.example"]}
        )
        assert share["recipients"] == ["buyer@northwind.example"]

    def test_recipients_may_arrive_as_a_comma_string(self):
        share = normalise_share({"recipients": "a@b.example, c@d.example"})
        assert share["recipients"] == ["a@b.example", "c@d.example"]

    def test_a_share_with_no_recipient_is_refused(self):
        with pytest.raises(ShareRefused):
            normalise_share({"recipients": []})

    def test_a_forward_share_needs_a_sender(self):
        with pytest.raises(ShareRefused) as refused:
            normalise_share({"kind": "forward", "recipients": ["a@b.example"]})
        assert refused.value.context["field"] == "sender"

    def test_an_unknown_kind_is_refused(self):
        with pytest.raises(ShareRefused):
            normalise_share({"kind": "broadcast", "recipients": ["a@b.example"]})


# --------------------------------------------------------------------------- #
# Discovery, the metrics, the heatmap, the roll-up, the DQL score
# --------------------------------------------------------------------------- #


class TestTheDiscovery:
    def test_a_known_viewer_is_not_re_roled(self):
        assert stakeholder_role("a@b.example", known={"a@b.example"}, shared_from=None) == "known"

    def test_a_known_upstream_sender_makes_the_viewer_discovered(self):
        role = stakeholder_role("c@d.example", known={"a@b.example"}, shared_from="a@b.example")
        assert role == "discovered"

    def test_an_unknown_upstream_sender_is_only_a_direct_open(self):
        role = stakeholder_role("c@d.example", known={"a@b.example"}, shared_from="ghost@x.example")
        assert role == "direct"

    def test_no_upstream_sender_is_a_direct_open(self):
        assert stakeholder_role("c@d.example", known=set(), shared_from=None) == "direct"


class TestTheMetrics:
    def _events(self):
        return [
            {"viewer": "a@b.example", "event": "view", "is_internal": False},
            {
                "viewer": "a@b.example",
                "event": "section_selected",
                "section": "dashboard",
                "is_internal": False,
            },
            {
                "viewer": "a@b.example",
                "event": "doc_downloaded",
                "section": "reports",
                "is_internal": False,
            },
            {"viewer": "c@d.example", "event": "view", "is_internal": False},
            {"viewer": "dana@b.example", "event": "view", "is_internal": True},
        ]

    def test_every_researched_metric_is_present(self):
        block = metrics(self._events(), [], [])
        for name in METRIC_NAMES:
            assert name in block, name

    def test_demos_sent_counts_distinct_initial_recipients(self):
        shares = [
            {"kind": "initial", "recipient": "a@b.example"},
            {"kind": "initial", "recipient": "a@b.example"},
            {"kind": "forward", "recipient": "c@d.example"},
        ]
        assert metrics([], [], shares)["demos_sent"] == 1

    def test_the_rates_use_demos_sent_as_their_denominator(self):
        shares = [
            {"kind": "initial", "recipient": "a@b.example"},
            {"kind": "initial", "recipient": "c@d.example"},
            {"kind": "initial", "recipient": "never@b.example"},
        ]
        block = metrics(self._events(), [{"role": "discovered"}], shares)
        assert block["demos_sent"] == 3
        assert block["view_rate"] == 0.6667  # two viewers over three demos sent
        assert block["discovery_rate"] == 0.3333

    def test_a_rate_with_no_denominator_reads_as_zero(self):
        assert metrics(self._events(), [], [])["view_rate"] == 0.0

    def test_an_internal_preview_is_excluded_and_counted(self):
        block = metrics(self._events(), [], [])
        assert block["views"] == 2  # the internal view is not a buyer view
        assert block["internal_previews"] == 1

    def test_the_denominators_are_stated(self):
        block = metrics([], [], [])
        assert block["denominators"]["demos_sent"] == "distinct initial recipients"


class TestTheHeatmap:
    def test_a_bucket_is_a_pure_function_of_the_event_timestamp(self):
        events = [
            {"viewer": "a@b.example", "event": "view", "occurred_at": NOW.isoformat()},
        ]
        block = heatmap(events)
        cell = block["cells"][0]
        # NOW is a Friday at 12:00 UTC, so the slot is index 3, starting at 12:00.
        assert cell["weekday"] == "Friday"
        assert cell["weekday_index"] == 4
        assert cell["start_hour"] == 12
        assert cell["bucket"] == 12 // HEATMAP_BUCKET_HOURS

    def test_two_events_in_one_slot_land_in_one_cell(self):
        events = [
            {"viewer": "a@b.example", "event": "view", "occurred_at": NOW.isoformat()},
            {
                "viewer": "c@d.example",
                "event": "view",
                "occurred_at": (NOW + timedelta(minutes=30)).isoformat(),
            },
        ]
        block = heatmap(events)
        assert len(block["cells"]) == 1
        assert block["cells"][0]["count"] == 2
        assert block["cells"][0]["viewers"] == 2

    def test_an_internal_preview_is_not_on_the_buyer_heatmap(self):
        events = [
            {
                "viewer": "dana@b.example",
                "event": "view",
                "is_internal": True,
                "occurred_at": NOW.isoformat(),
            }
        ]
        assert heatmap(events)["total"] == 0

    def test_the_slots_cover_the_day(self):
        block = heatmap([])
        assert len(block["buckets"]) == 24 // HEATMAP_BUCKET_HOURS

    def test_a_bucket_width_that_does_not_divide_the_day_is_refused(self):
        with pytest.raises(DemoValidationError):
            heatmap([], bucket_hours=5)


class TestTheDqlScore:
    def test_a_single_view_does_not_qualify_a_lead(self):
        block = dql_score([{"viewer": "a@b.example", "event": "view"}])
        assert block["score"] == DQL_WEIGHTS["viewed"]
        assert block["qualified"] is False

    def test_a_deep_engaged_lead_qualifies(self):
        block = dql_score(
            [
                {"viewer": "a@b.example", "event": "view"},
                {"viewer": "a@b.example", "event": "view"},
                {"viewer": "a@b.example", "event": "section_selected", "section": "dashboard"},
                {"viewer": "a@b.example", "event": "section_selected", "section": "reports"},
                {"viewer": "a@b.example", "event": "doc_downloaded", "section": "reports"},
            ]
        )
        assert block["score"] == 30 + 16 + 18 + 20
        assert block["qualified"] is True

    def test_the_section_contribution_is_capped(self):
        events = [
            {"viewer": "a@b.example", "event": "section_selected", "section": f"s{index}"}
            for index in range(20)
        ]
        assert dql_score(events)["components"]["sections"] == DQL_WEIGHTS["section_cap"]

    def test_the_score_never_exceeds_the_maximum(self):
        events = [
            {"viewer": "a@b.example", "event": "view"},
            {"viewer": "a@b.example", "event": "view"},
            {"viewer": "a@b.example", "event": "doc_downloaded"},
        ] + [
            {"viewer": "a@b.example", "event": "section_selected", "section": f"s{index}"}
            for index in range(10)
        ]
        assert dql_score(events)["score"] <= DQL_MAX


class TestTheSectionsAndReach:
    def _demo(self):
        return normalise_demo({"title": "x"}, now=NOW)

    def test_a_section_selection_is_counted_on_its_node(self):
        events = [
            {"viewer": "a@b.example", "event": "section_selected", "section": "dashboard"},
            {"viewer": "c@d.example", "event": "section_selected", "section": "dashboard"},
        ]
        block = section_counts(self._demo(), events)
        assert block["sections"][1]["section"] == "dashboard"
        assert block["sections"][1]["count"] == 2
        assert block["most_selected"]["section"] == "dashboard"

    def test_the_entry_node_is_reached_by_every_view(self):
        events = [{"viewer": "a@b.example", "event": "view"}]
        block = reach_counts(self._demo(), events)
        assert block[0]["section"] == "overview"
        assert block[0]["reached"] == 1

    def test_a_page_nobody_reached_reads_as_zero(self):
        block = reach_counts(self._demo(), [])
        assert all(row["reached"] == 0 for row in block)


class TestTheRollup:
    def _demos(self):
        return [
            {"id": "d1", "title": "One", "team": "Enterprise", "owner": "dana"},
            {"id": "d2", "title": "Two", "team": "SMB", "owner": "sam"},
        ]

    def _events(self):
        return [
            {
                "demo_id": "d1",
                "viewer": "a@northwind.example",
                "event": "view",
                "is_internal": False,
            },
            {
                "demo_id": "d2",
                "viewer": "c@fabrikam.example",
                "event": "view",
                "is_internal": False,
            },
        ]

    def test_top_level_is_one_group_over_everything(self):
        block = rollup("top_level", self._demos(), self._events(), [], [])
        assert block["count"] == 1
        assert block["groups"][0]["metrics"]["views"] == 2

    def test_all_demos_is_one_group_per_demo(self):
        block = rollup("all_demos", self._demos(), self._events(), [], [])
        assert block["count"] == 2

    def test_team_and_individual_group_by_their_field(self):
        teams = rollup("team", self._demos(), self._events(), [], [])
        assert {group["key"] for group in teams["groups"]} == {"Enterprise", "SMB"}
        people = rollup("individual", self._demos(), self._events(), [], [])
        assert {group["key"] for group in people["groups"]} == {"dana", "sam"}

    def test_recently_shared_picks_the_latest_share(self):
        shares = [
            {"demo_id": "d1", "shared_at": "2026-05-01T09:00:00+00:00"},
            {"demo_id": "d2", "shared_at": "2026-05-02T09:00:00+00:00"},
        ]
        block = rollup("recently_shared", self._demos(), self._events(), [], shares)
        assert block["count"] == 1
        assert block["groups"][0]["key"] == "d2"

    def test_buyer_group_groups_on_the_viewer_domain(self):
        block = rollup("buyer_group", self._demos(), self._events(), [], [])
        assert {group["key"] for group in block["groups"]} == {
            "northwind.example",
            "fabrikam.example",
        }

    def test_an_explicit_buyer_group_wins_over_the_domain(self):
        assert buyer_group({"viewer": "a@b.example", "buyer_group": "Acme"}) == "Acme"

    def test_an_unknown_level_is_refused(self):
        with pytest.raises(DemoValidationError):
            rollup("galaxy", self._demos(), [], [], [])


# --------------------------------------------------------------------------- #
# The book: writes through the audited store
# --------------------------------------------------------------------------- #


class TestTheBook:
    def test_a_demo_belongs_to_a_room_that_exists(self, book: DemoAnalyticsBook):
        with pytest.raises(DemoNotFound):
            book.create_demo("room_absent", demo_payload(), source="POST /api/wf-127/demos")

    def test_a_demo_is_created_published_and_read(self, book: DemoAnalyticsBook, room: dict):
        demo = make_demo(book, room["id"])
        assert demo["state"] == "draft"
        assert demo["node_count"] == len(DEFAULT_CLICK_PATH)
        published = book.publish_demo(demo["id"], source="POST /api/wf-127/demos/{demo_id}/publish")
        assert published["state"] == "published"
        assert published["published_at"]
        assert book.read_demo(demo["id"])["state"] == "published"

    def test_an_unknown_demo_is_not_found(self, book: DemoAnalyticsBook):
        with pytest.raises(DemoNotFound):
            book.read_demo("wf127_demo_absent")

    def test_the_heatmap_and_the_stakeholders_refuse_an_unknown_demo(self, book: DemoAnalyticsBook):
        with pytest.raises(DemoNotFound):
            book.heatmap("wf127_demo_absent")
        with pytest.raises(DemoNotFound):
            book.stakeholder_views("wf127_demo_absent")

    def test_a_demo_can_be_updated_and_is_revalidated(self, book: DemoAnalyticsBook, room: dict):
        demo = make_demo(book, room["id"])
        updated = book.update_demo(
            demo["id"], {"title": "Renamed"}, source="PATCH /api/wf-127/demos/{demo_id}"
        )
        assert updated["title"] == "Renamed"
        with pytest.raises(DemoValidationError):
            book.update_demo(
                demo["id"], {"demo_type": "hologram"}, source="PATCH /api/wf-127/demos/{demo_id}"
            )

    def test_an_archived_demo_refuses_an_event(self, book: DemoAnalyticsBook, room: dict):
        demo = make_demo(book, room["id"])
        book.publish_demo(demo["id"], source="seed")
        book.archive_demo(demo["id"], source="POST /api/wf-127/demos/{demo_id}/archive")
        with pytest.raises(DemoArchived):
            book.record_event(demo["id"], a_view("a@b.example"), source="POST events")

    def test_an_archived_demo_cannot_be_edited(self, book: DemoAnalyticsBook, room: dict):
        demo = make_demo(book, room["id"])
        book.archive_demo(demo["id"], source="seed")
        with pytest.raises(DemoArchived):
            book.update_demo(demo["id"], {"title": "x"}, source="PATCH")

    def test_an_initial_share_mints_initial_recipients(
        self, store: RecordStore, book: DemoAnalyticsBook, room: dict
    ):
        demo = make_demo(book, room["id"])
        result = book.record_share(
            demo["id"],
            {"kind": "initial", "recipients": ["a@b.example", "c@d.example", "a@b.example"]},
            source="POST /api/wf-127/demos/{demo_id}/shares",
        )
        assert result["count"] == 2
        roles = {
            row["data"]["role"]
            for row in store.find(STAKEHOLDER_COLLECTION, {"demo_id": demo["id"]})
        }
        assert roles == {"initial_recipient"}

    def test_a_forward_share_discovers_the_recipient(
        self, store: RecordStore, book: DemoAnalyticsBook, room: dict
    ):
        demo = make_demo(book, room["id"])
        book.record_share(
            demo["id"], {"kind": "initial", "recipients": ["a@b.example"]}, source="seed"
        )
        result = book.record_share(
            demo["id"],
            {"kind": "forward", "sender": "a@b.example", "recipients": ["c@d.example"]},
            source="POST /api/wf-127/demos/{demo_id}/shares",
        )
        assert result["stakeholders_discovered"] == 1
        row = store.find(STAKEHOLDER_COLLECTION, {"demo_id": demo["id"], "viewer": "c@d.example"})
        assert row[0]["data"]["role"] == "discovered"
        assert row[0]["data"]["invited_by"] == "a@b.example"
        assert row[0]["data"]["discovered_via"] == "intra_viral"

    def test_a_forward_share_from_an_unknown_sender_is_only_a_direct_open(
        self, store: RecordStore, book: DemoAnalyticsBook, room: dict
    ):
        demo = make_demo(book, room["id"])
        book.record_share(
            demo["id"],
            {"kind": "forward", "sender": "ghost@x.example", "recipients": ["c@d.example"]},
            source="seed",
        )
        row = store.find(STAKEHOLDER_COLLECTION, {"demo_id": demo["id"], "viewer": "c@d.example"})
        assert row[0]["data"]["role"] == "direct"

    def test_a_share_writes_one_row_per_recipient(
        self, store: RecordStore, book: DemoAnalyticsBook, room: dict
    ):
        demo = make_demo(book, room["id"])
        book.record_share(
            demo["id"],
            {"kind": "initial", "recipients": ["a@b.example", "c@d.example"]},
            source="seed",
        )
        rows = store.find(SHARE_COLLECTION, {"demo_id": demo["id"]})
        assert len(rows) == 2

    def test_a_view_is_written_through_the_audited_store(
        self, store: RecordStore, book: DemoAnalyticsBook, room: dict
    ):
        demo = make_demo(book, room["id"])
        book.record_event(
            demo["id"], a_view("a@b.example"), source="POST /api/wf-127/demos/{demo_id}/events"
        )
        rows = store.find(EVENT_COLLECTION, {"demo_id": demo["id"]})
        assert len(rows) == 1
        assert rows[0]["data"]["viewer"] == "a@b.example"

    def test_a_view_from_a_forwarded_viewer_discovers_a_stakeholder(
        self, store: RecordStore, book: DemoAnalyticsBook, room: dict
    ):
        demo = make_demo(book, room["id"])
        book.record_share(
            demo["id"], {"kind": "initial", "recipients": ["a@b.example"]}, source="seed"
        )
        result = book.record_event(
            demo["id"],
            a_view("c@d.example", shared_from="a@b.example"),
            source="POST events",
        )
        assert result["discovered"] is True
        assert result["stakeholder"]["role"] == "discovered"

    def test_a_second_event_from_the_same_viewer_is_not_a_new_discovery(
        self, store: RecordStore, book: DemoAnalyticsBook, room: dict
    ):
        demo = make_demo(book, room["id"])
        book.record_share(
            demo["id"], {"kind": "initial", "recipients": ["a@b.example"]}, source="seed"
        )
        first = book.record_event(
            demo["id"], a_view("c@d.example", shared_from="a@b.example"), source="POST events"
        )
        second = book.record_event(
            demo["id"], a_view("c@d.example", shared_from="a@b.example"), source="POST events"
        )
        assert first["discovered"] is True
        assert second["discovered"] is False
        assert (
            len(
                store.find(STAKEHOLDER_COLLECTION, {"demo_id": demo["id"], "viewer": "c@d.example"})
            )
            == 1
        )

    def test_a_section_the_demo_does_not_have_is_refused(self, book: DemoAnalyticsBook, room: dict):
        demo = make_demo(book, room["id"])
        with pytest.raises(DemoValidationError) as refused:
            book.record_event(
                demo["id"],
                {"viewer": "a@b.example", "event": "section_selected", "section": "ghost"},
                source="POST events",
            )
        assert refused.value.context["field"] == "section"

    def test_an_internal_preview_is_recorded_and_excluded_from_the_metrics(
        self, book: DemoAnalyticsBook, room: dict
    ):
        demo = make_demo(book, room["id"])
        book.record_share(
            demo["id"], {"kind": "initial", "recipients": ["a@b.example"]}, source="seed"
        )
        book.record_event(
            demo["id"], a_view("dana@b.example", is_internal=True), source="POST events"
        )
        block = book.analytics(demo["id"])["metrics"]
        assert block["views"] == 0
        assert block["internal_previews"] == 1
        # The internal preview is not a stakeholder; the initial recipient who
        # never opened is, because a demo sent is a stakeholder.
        viewers = [row["viewer"] for row in book.analytics(demo["id"])["stakeholders"]]
        assert viewers == ["a@b.example"]

    def test_the_analytics_block_reports_every_researched_surface(
        self, book: DemoAnalyticsBook, room: dict
    ):
        demo = make_demo(book, room["id"])
        book.record_share(
            demo["id"],
            {"kind": "initial", "recipients": ["a@b.example", "never@b.example"]},
            source="seed",
        )
        book.record_event(demo["id"], a_view("a@b.example"), source="POST events")
        book.record_event(
            demo["id"],
            {"viewer": "a@b.example", "event": "section_selected", "section": "dashboard"},
            source="POST events",
        )
        block = book.analytics(demo["id"])
        assert block["metrics"]["demos_sent"] == 2
        assert block["metrics"]["view_rate"] == 0.5
        assert block["heatmap"]["total"] == 2
        assert block["sections"]["sections"][1]["count"] == 1
        assert block["stakeholders"][0]["viewer"] == "a@b.example"
        assert block["levels"] == list(LEVELS)

    def test_the_stakeholder_engagement_is_implied_by_the_events(
        self, book: DemoAnalyticsBook, room: dict
    ):
        demo = make_demo(book, room["id"])
        book.record_event(demo["id"], a_view("a@b.example"), source="POST events")
        book.record_event(
            demo["id"],
            {"viewer": "a@b.example", "event": "section_selected", "section": "dashboard"},
            source="POST events",
        )
        book.record_event(
            demo["id"],
            {"viewer": "a@b.example", "event": "doc_downloaded", "section": "dashboard"},
            source="POST events",
        )
        row = book.stakeholder_views(demo["id"])[0]
        assert row["views"] == 1
        assert row["sections"] == ["dashboard"]
        assert row["downloads"] == 1
        assert row["dql"]["score"] == 30 + 8 + 18

    def test_the_rollup_over_a_room_sees_every_demo(self, book: DemoAnalyticsBook, room: dict):
        first = make_demo(book, room["id"])
        make_demo(book, room["id"], title="Second", team="SMB", owner="sam")
        book.record_event(first["id"], a_view("a@b.example"), source="POST events")
        block = book.rollup("all_demos", room_id=room["id"])
        assert block["count"] == 2

    def test_the_summary_counts_the_states_a_reviewer_checks(
        self, book: DemoAnalyticsBook, room: dict
    ):
        demo = make_demo(book, room["id"])
        book.publish_demo(demo["id"], source="seed")
        book.record_share(
            demo["id"], {"kind": "initial", "recipients": ["a@b.example"]}, source="seed"
        )
        book.record_event(demo["id"], a_view("a@b.example"), source="POST events")
        block = book.summary(room["id"])
        assert block["demos"] == 1
        assert block["published"] == 1
        assert block["demos_sent"] == 1
        assert block["views"] == 1
        assert block["stakeholders"] == 1


# --------------------------------------------------------------------------- #
# Architecture: the dependency direction the contract fixes
# --------------------------------------------------------------------------- #

#: The only ``dsr`` packages a module in this domain package may import. This
#: workflow is standalone, so the sibling domain packages are absent: it restates
#: no other workflow's capability, and the research names no dependency.
ALLOWED_DSR_PREFIXES = ("dsr.store", "dsr.demo_analytics")


def _domain_modules() -> list[Path]:
    return sorted(DOMAIN_DIR.glob("*.py"))


class TestArchitecture:
    def test_the_domain_package_never_imports_the_app_or_a_feature(self):
        for path in _domain_modules():
            # The import statements are what matter, so the check is on the AST
            # rather than on the raw text: a docstring that names ``dsr.api`` to
            # explain why it is not imported is not an import of it.
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                names: list[str] = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module]
                for name in names:
                    assert name != "dsr.api" and not name.startswith("dsr.api."), path.name
                    assert name != "dsr.features" and not name.startswith("dsr.features."), (
                        path.name
                    )
                    assert not name.startswith("fastapi"), path.name
            text = path.read_text(encoding="utf-8")
            assert "import sqlite3" not in text, path.name
            assert "sqlite3.connect" not in text, path.name

    def test_the_domain_package_imports_only_allowed_dsr_packages(self):
        for path in _domain_modules():
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                names: list[str] = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module]
                for name in names:
                    if not name.startswith("dsr"):
                        continue
                    assert name.startswith(ALLOWED_DSR_PREFIXES), f"{path.name} imports {name}"

    def test_the_feature_module_takes_its_dependencies_from_deps(self):
        text = FEATURE_MODULE.read_text(encoding="utf-8")
        assert "from dsr.deps import" in text
        assert "from dsr.api" not in text and "import dsr.api" not in text
        assert "import sqlite3" not in text
        assert "sqlite3.connect" not in text

    def test_the_feature_module_declares_its_ticket_prefix_and_handler(self):
        import importlib

        module = importlib.import_module(
            "dsr.features.wf127_interactive_demo_sandbox_analytics_with_stakeholder"
        )
        assert module.FEATURE["id"] == "wf-127-demo-analytics"
        assert module.FEATURE["ticket"] == "WF-127"
        assert module.router.prefix == "/api/wf-127"
        assert list(module.EXCEPTION_HANDLERS) == [domain.DemoAnalyticsError]

    def test_the_error_type_is_this_workflows_own(self):
        assert domain.DemoAnalyticsError.__module__ == "dsr.demo_analytics.errors"


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #


class TestSeed:
    def test_the_demo_seeds_two_demos_and_a_discovered_stakeholder(self, db, store: RecordStore):
        scoped = RecordStore(db)
        room = scoped.create("room", {"name": "Northwind"}, actor="dana", source="test")
        summary = domain_feature_seed(
            db, {"room_ids": [(room["id"], "Northwind")], "now": NOW, "rng": None}
        )
        assert isinstance(summary, str) and summary
        # A Windows console prints this string; a non-cp1252 character once broke
        # the whole seeder, so the encoding is asserted rather than assumed.
        summary.encode("cp1252")
        demos = scoped.find(DEMO_COLLECTION, {})
        assert len(demos) == 2
        assert {row["data"]["state"] for row in demos} == {"published", "archived"}
        stakeholders = scoped.find(STAKEHOLDER_COLLECTION, {})
        assert any(row["data"]["role"] == "discovered" for row in stakeholders)
        events = scoped.find(EVENT_COLLECTION, {})
        assert len(events) >= 6

    def test_the_demo_seed_names_the_non_success_states(self, db):
        scoped = RecordStore(db)
        room = scoped.create("room", {"name": "Northwind"}, actor="dana", source="test")
        summary = domain_feature_seed(
            db, {"room_ids": [(room["id"], "Northwind")], "now": NOW, "rng": None}
        )
        assert "never opened" in summary
        assert "internal preview excluded" in summary
        assert "archived demo refused an event" in summary

    def test_the_demo_reports_nothing_when_there_is_no_room(self, db):
        summary = domain_feature_seed(db, {"room_ids": [], "now": NOW, "rng": None})
        assert "0 interactive demos" in summary
        summary.encode("cp1252")
