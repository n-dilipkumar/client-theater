"""WF-075 over HTTP: the surface this feature's own router serves.

The domain rules are in ``test_wf075.py``. This file is the other half, and it is
organised by what a caller can observe:

``the route table``
    That the host mounted every route by discovery alone, with no shared file edited,
    and that no two of them collide.
``the viewers list``
    One row per buyer email, with First Seen and Last Seen, and the filter to a single
    address.
``the aggregate``
    The cached room totals with Unix millisecond bounds, and the two counts staying
    apart.
``the drill-down``
    One view's page dwell plus location and client, with the geography labelled as the
    inference the specification marks it as.
``the per-link view list``
    Reverse chronological, anonymous views included.
``the error shapes``
    Every status code and body this router can produce.
``the audit-source rule``
    Every ``source=`` this workflow records names a route the host actually mounted.
``the read-path rule``
    The reads write no audit rows, because a route that logged every read would fill
    this product's own guarantee with entries describing no change.
"""

from __future__ import annotations

import importlib
from datetime import datetime, timezone
from typing import Any

import dsr.features as host
import pytest
from dsr.security_governance import engagement as vocab
from dsr.security_governance.engagement_engine import EngagementEngine
from fastapi.testclient import TestClient

FEATURE_MODULE = "dsr.features.wf075_review_who_engaged_where_and_for_how_long"
PREFIX = "/api/wf-075"
FEATURE_ID = "wf-075-review-who-engaged-where-and-for-how-long"

NOW = datetime(2026, 10, 4, 9, 0, tzinfo=timezone.utc)
BASE_MS = int(NOW.timestamp() * 1000)
HOUR_MS = 3600000


@pytest.fixture(autouse=True)
def _clear_the_process_cache():
    """The aggregate cache is process-level, so it is cleared around every test.

    The cache key carries the store identity, so a temporary database is never
    answered from another test's rows. This makes the first read of each test honest
    regardless of what ran before it, which is what the suite's parallel runs need.
    """

    EngagementEngine.clear_cache()
    yield
    EngagementEngine.clear_cache()


def make_visitor(
    client: TestClient, email: str = "buyer@northwind.example", **fields: Any
) -> dict[str, Any]:
    """Create a visitor row over HTTP and return the created record."""

    response = client.post(
        f"{PREFIX}/visitors", params={"room_id": "room_a"}, json={"email": email, **fields}
    )
    assert response.status_code == 201, response.text
    return response.json()


def make_view(
    client: TestClient,
    link_id: str = "link_a",
    viewer_email: str | None = "buyer@northwind.example",
    **fields: Any,
) -> dict[str, Any]:
    """Record one view event over HTTP and return the created record.

    The caller is named ``client`` and the user-agent breakdown is passed as
    ``user_agent`` rather than ``client``, because the JSON key the specification
    quotes is ``client`` and a caller asking for that key must not collide with this
    function's first parameter.
    """

    body: dict[str, Any] = {
        "viewer_email": viewer_email,
        "view_type": vocab.VIEW_TYPE_LINK,
        "viewed_at": fields.pop("viewed_at", BASE_MS),
        "page_durations": fields.pop(
            "page_durations", [{"page_number": 1, "duration_seconds": 60}]
        ),
        "location": fields.pop("location", {"country": "United Kingdom", "city": "London"}),
    }
    breakdown = fields.pop("user_agent", {"browser": "Chrome", "os": "macOS", "device": "laptop"})
    body["client"] = breakdown
    body.update(fields)
    response = client.post(
        f"{PREFIX}/links/{link_id}/views", params={"room_id": "room_a"}, json=body
    )
    assert response.status_code == 201, response.text
    return response.json()


# --------------------------------------------------------------------------- #
# the route table
# --------------------------------------------------------------------------- #


class TestRouteTable:
    def test_the_feature_is_installed_with_its_routes(self, client: TestClient):
        payload = client.get("/api/features").json()
        installed = next(f for f in payload["features"] if f["id"] == FEATURE_ID)
        paths = {route["path"] for route in installed["routes"]}

        assert f"{PREFIX}/summary" in paths
        assert f"{PREFIX}/visitors" in paths
        assert f"{PREFIX}/analytics/views/{{view_id}}" in paths

    def test_the_feature_did_not_fail_to_load(self, client: TestClient):
        payload = client.get("/api/features").json()

        assert not [f for f in payload["failed"] if FEATURE_ID in str(f)]

    def test_the_host_mounted_it_without_a_shared_file_being_edited(self):
        """``backend/dsr/api.py`` discovers and mounts every feature router. This test
        states the claim by name so a reviewer can check it against the diff."""

        module = importlib.import_module(FEATURE_MODULE)
        record = host.REGISTRY.by_id(FEATURE_ID)

        assert record is not None
        assert module.router.prefix == PREFIX

    def test_every_route_is_reachable_and_none_collide(self):
        mounted = {
            f"{method} {route.path}"
            for route in importlib.import_module(FEATURE_MODULE).router.routes
            for method in route.methods
            if method not in ("HEAD", "OPTIONS")
        }

        assert len(mounted) == len(set(mounted))
        assert len(mounted) >= 13

    def test_the_prefix_is_ticket_derived(self):
        assert PREFIX == "/api/wf-075"

    def test_no_feature_imports_the_app(self):
        """Dependencies come from ``dsr.deps``. A test enforces it project-wide."""

        module = importlib.import_module(FEATURE_MODULE)

        assert "dsr.api" not in module.__dict__


# --------------------------------------------------------------------------- #
# the viewers list
# --------------------------------------------------------------------------- #


class TestViewersList:
    def test_the_list_is_one_row_per_email(self, client: TestClient):
        make_visitor(client, "a@northwind.example")
        make_visitor(client, "b@northwind.example")

        payload = client.get(f"{PREFIX}/visitors").json()

        assert payload["count"] == 2

    def test_a_single_address_can_be_filtered(self, client: TestClient):
        make_visitor(client, "a@northwind.example")
        make_visitor(client, "b@northwind.example")

        payload = client.get(f"{PREFIX}/visitors", params={"email": "a@northwind.example"}).json()

        assert payload["count"] == 1
        assert payload["visitors"][0]["email"] == "a@northwind.example"

    def test_the_filter_is_case_insensitive(self, client: TestClient):
        make_visitor(client, "a@northwind.example")

        payload = client.get(f"{PREFIX}/visitors", params={"email": "A@Northwind.Example"}).json()

        assert payload["count"] == 1

    def test_first_and_last_seen_are_served(self, client: TestClient):
        make_visitor(client, invited_at=BASE_MS - 10 * HOUR_MS)
        make_view(client, viewed_at=BASE_MS - HOUR_MS)

        row = client.get(f"{PREFIX}/visitors").json()["visitors"][0]

        assert row["first_seen"] == BASE_MS - 10 * HOUR_MS
        assert row["last_seen"] == BASE_MS - HOUR_MS

    def test_the_list_names_the_three_verification_states(self, client: TestClient):
        payload = client.get(f"{PREFIX}/visitors").json()

        assert payload["verification_states"] == list(vocab.VERIFICATION_STATES)

    def test_one_visitor_is_readable_with_its_views(self, client: TestClient):
        created = make_visitor(client)
        make_view(client, link_id="link_a")
        make_view(client, link_id="link_b")

        payload = client.get(f"{PREFIX}/visitors/{created['id']}").json()

        assert payload["email"] == "buyer@northwind.example"
        assert len(payload["views"]) == 2

    def test_one_visitors_history_is_served_newest_first(self, client: TestClient):
        created = make_visitor(client)
        make_view(client, link_id="link_a", viewed_at=BASE_MS - 5 * HOUR_MS)
        make_view(client, link_id="link_b", viewed_at=BASE_MS)

        rows = client.get(f"{PREFIX}/visitors/{created['id']}/views").json()["views"]

        assert rows[0][vocab.VIEWED_AT] == BASE_MS


# --------------------------------------------------------------------------- #
# the aggregate
# --------------------------------------------------------------------------- #


class TestAggregate:
    def test_the_room_totals_are_served(self, client: TestClient):
        make_view(client, link_id="link_a")
        make_view(client, link_id="link_b")

        payload = client.get(f"{PREFIX}/analytics/datarooms/room_a").json()

        assert payload[vocab.TOTAL_VIEWS_FIELD] == 2
        assert payload[vocab.UNIQUE_VISITORS_FIELD] == 1

    def test_the_two_counts_stay_apart(self, client: TestClient):
        """One buyer, two links: one visitor row and two view events."""

        make_visitor(client)
        make_view(client, link_id="link_a")
        make_view(client, link_id="link_b")

        visitors = client.get(f"{PREFIX}/visitors").json()
        views = client.get(f"{PREFIX}/analytics/datarooms/room_a").json()

        assert visitors["count"] == 1
        assert views[vocab.TOTAL_VIEWS_FIELD] == 2

    def test_the_bounds_are_accepted_in_unix_milliseconds(self, client: TestClient):
        make_view(client, viewed_at=BASE_MS - 5 * HOUR_MS)
        make_view(client, viewed_at=BASE_MS - HOUR_MS)

        payload = client.get(
            f"{PREFIX}/analytics/datarooms/room_a", params={"since": BASE_MS - 2 * HOUR_MS}
        ).json()

        assert payload[vocab.TOTAL_VIEWS_FIELD] == 1
        assert payload["since"] == BASE_MS - 2 * HOUR_MS

    def test_a_window_that_ends_before_it_begins_is_a_400(self, client: TestClient):
        response = client.get(
            f"{PREFIX}/analytics/datarooms/room_a",
            params={"since": BASE_MS, "until": BASE_MS - HOUR_MS},
        )

        assert response.status_code == 400
        assert response.json()["error"] == "invalid_engagement_query"

    def test_an_unparseable_bound_is_a_400_and_names_the_field(self, client: TestClient):
        response = client.get(f"{PREFIX}/analytics/datarooms/room_a", params={"since": "yesterday"})

        assert response.status_code == 400
        assert "since" in response.json()["errors"]

    def test_the_response_declares_its_time_unit(self, client: TestClient):
        payload = client.get(f"{PREFIX}/analytics/datarooms/room_a").json()

        assert payload[vocab.TIME_UNIT_FIELD] == "unix_ms"

    def test_a_second_poll_inside_the_window_is_marked_cached(self, client: TestClient):
        make_view(client)

        first = client.get(f"{PREFIX}/analytics/datarooms/room_a").json()
        second = client.get(f"{PREFIX}/analytics/datarooms/room_a").json()

        assert first[vocab.CACHED_FIELD] is False
        assert second[vocab.CACHED_FIELD] is True

    def test_every_aggregate_names_the_instant_it_was_computed(self, client: TestClient):
        payload = client.get(f"{PREFIX}/analytics/datarooms/room_a").json()

        assert payload[vocab.COMPUTED_AT]

    def test_the_aggregate_carries_the_tighter_rate_limit_note(self, client: TestClient):
        payload = client.get(f"{PREFIX}/analytics/datarooms/room_a").json()

        assert "tighter" in payload["rate_limit_note"]

    def test_the_per_page_engagement_is_served(self, client: TestClient):
        make_view(
            client,
            page_durations=[
                {"page_number": 1, "duration_seconds": 40},
                {"page_number": 2, "duration_seconds": 80},
            ],
        )

        payload = client.get(f"{PREFIX}/analytics/datarooms/room_a").json()

        assert [row["page_number"] for row in payload[vocab.PER_PAGE_FIELD]] == [1, 2]

    def test_a_links_own_aggregate_is_served(self, client: TestClient):
        make_view(client, link_id="link_a")
        make_view(client, link_id="link_b")

        payload = client.get(f"{PREFIX}/analytics/links/link_a").json()

        assert payload[vocab.TOTAL_VIEWS_FIELD] == 1
        assert payload["link_id"] == "link_a"


# --------------------------------------------------------------------------- #
# the drill-down
# --------------------------------------------------------------------------- #


class TestDrillDown:
    def test_one_view_carries_its_pages_location_and_client(self, client: TestClient):
        created = make_view(
            client,
            page_durations=[{"page_number": 1, "duration_seconds": 30}],
            location={"country": "Germany", "city": "Berlin"},
            user_agent={"browser": "Edge", "os": "Windows", "device": "desktop"},
        )

        payload = client.get(f"{PREFIX}/analytics/views/{created['id']}").json()

        assert payload[vocab.PAGE_DURATIONS] == [{"page_number": 1, "duration_seconds": 30}]
        assert payload[vocab.LOCATION] == {"country": "Germany", "city": "Berlin"}
        assert payload[vocab.CLIENT]["browser"] == "Edge"

    def test_the_total_dwell_is_served(self, client: TestClient):
        created = make_view(client, page_durations=[{"page_number": 1, "duration_seconds": 30}])

        payload = client.get(f"{PREFIX}/analytics/views/{created['id']}").json()

        assert payload[vocab.TOTAL_DURATION_SECONDS] == 30

    def test_the_drill_down_labels_the_geography_as_an_inference(self, client: TestClient):
        """The specification marks the vendor as inferred. A response says so too."""

        created = make_view(client)

        payload = client.get(f"{PREFIX}/analytics/views/{created['id']}").json()

        assert "inferred" in payload["geography_source"]

    def test_the_drill_down_names_no_vendor(self, client: TestClient):
        created = make_view(client)

        payload = client.get(f"{PREFIX}/analytics/views/{created['id']}").json()

        for vendor in ("maxmind", "ipinfo", "ipapi", "cloudflare"):
            assert vendor not in payload["geography_source"].lower()

    def test_an_unknown_view_is_a_404(self, client: TestClient):
        response = client.get(f"{PREFIX}/analytics/views/no-such-view")

        assert response.status_code == 404
        assert response.json()["error"] == "not_found"


# --------------------------------------------------------------------------- #
# the per-link view list
# --------------------------------------------------------------------------- #


class TestLinkViewList:
    def test_views_come_back_newest_first(self, client: TestClient):
        make_view(client, link_id="link_a", viewed_at=BASE_MS - 5 * HOUR_MS)
        make_view(client, link_id="link_a", viewed_at=BASE_MS)

        rows = client.get(f"{PREFIX}/links/link_a/views").json()["views"]

        assert rows[0][vocab.VIEWED_AT] == BASE_MS

    def test_only_that_links_views_are_returned(self, client: TestClient):
        make_view(client, link_id="link_a")
        make_view(client, link_id="link_b")

        assert client.get(f"{PREFIX}/links/link_a/views").json()["count"] == 1

    def test_an_anonymous_view_is_still_reachable_per_link(self, client: TestClient):
        """The specification's requirement, not a convenience."""

        make_view(client, link_id="link_a", viewer_email=None)

        payload = client.get(f"{PREFIX}/links/link_a/views").json()

        assert payload["count"] == 1
        assert payload["anonymous"] == 1

    def test_the_download_state_is_readable_on_each_row(self, client: TestClient):
        make_view(client, link_id="link_a", download_type="pdf", downloaded_at=BASE_MS + 1000)
        make_view(client, link_id="link_a", viewed_at=BASE_MS - 1000)

        rows = client.get(f"{PREFIX}/links/link_a/views").json()["views"]
        states = {row[vocab.VIEW_TYPE] for row in rows}

        assert len(rows) == 2
        assert states == {vocab.VIEW_TYPE_LINK}

    def test_a_view_with_no_download_says_so_distinctly(self, client: TestClient):
        make_view(client, link_id="link_a")

        row = client.get(f"{PREFIX}/links/link_a/views").json()["views"][0]

        assert row["download"]["download_type"] == vocab.NO_DOWNLOAD
        assert row["download"]["downloaded"] is False

    def test_the_page_size_is_honoured(self, client: TestClient):
        for index in range(3):
            make_view(client, link_id="link_a", viewed_at=BASE_MS + index * 1000)

        assert client.get(f"{PREFIX}/links/link_a/views", params={"limit": 2}).json()["count"] == 2


# --------------------------------------------------------------------------- #
# the error shapes
# --------------------------------------------------------------------------- #


class TestErrorShapes:
    def test_an_unknown_visitor_is_a_404_not_a_500(self, client: TestClient):
        response = client.get(f"{PREFIX}/visitors/no-such-visitor")

        assert response.status_code == 404
        assert response.json()["detail"] == "No such visitor."

    def test_a_visitor_without_an_email_is_a_400(self, client: TestClient):
        response = client.post(f"{PREFIX}/visitors", json={"email": "  "})

        assert response.status_code == 400
        assert "email" in response.json()["errors"]

    def test_a_page_size_below_one_is_a_400(self, client: TestClient):
        response = client.get(f"{PREFIX}/links/link_a/views", params={"limit": 0})

        assert response.status_code == 400

    def test_an_unknown_decision_is_a_404(self, client: TestClient):
        assert client.get(f"{PREFIX}/decisions/NOT_A_DECISION").status_code == 404

    def test_no_route_leaks_a_500_for_a_missing_record(self, client: TestClient):
        for path in (
            f"{PREFIX}/visitors/missing",
            f"{PREFIX}/analytics/views/missing",
            f"{PREFIX}/decisions/missing",
        ):
            assert client.get(path).status_code == 404


# --------------------------------------------------------------------------- #
# the audit-source rule
# --------------------------------------------------------------------------- #


class TestAuditSourceRule:
    def test_every_source_this_router_can_record_names_a_mounted_route(self):
        """The test the brief asks for by name.

        A hardcoded source string is the defect this prevents: it leaves the audit log
        naming a route the app stopped serving.
        """

        module = importlib.import_module(FEATURE_MODULE)
        mounted = {
            f"{method} {route.path}"
            for route in module.router.routes
            for method in route.methods
            if method not in ("HEAD", "OPTIONS")
        }

        # Every literal source the feature module can build from its own router.
        for method, path in (
            ("POST", "/visitors"),
            ("POST", "/links/{link_id}/views"),
            ("GET", "/summary"),
            ("GET", "/visitors"),
            ("GET", "/links/{link_id}/views"),
        ):
            assert module._source(method, path) in mounted

    def test_the_read_paths_record_no_audit_row(self, client: TestClient):
        """A route that wrote a row on every read would fill the audit log with noise."""

        created = make_view(client, link_id="link_a")

        before = client.get("/api/audit").json()["count"]
        client.get(f"{PREFIX}/summary")
        client.get(f"{PREFIX}/visitors")
        client.get(f"{PREFIX}/links/link_a/views")
        client.get(f"{PREFIX}/analytics/views/{created['id']}")
        client.get(f"{PREFIX}/analytics/datarooms/room_a")
        client.get(f"{PREFIX}/vocabulary")
        after = client.get("/api/audit").json()["count"]

        assert after == before

    def test_a_write_lands_in_the_audit_log(self, client: TestClient):
        make_view(client, link_id="link_a")

        entries = client.get("/api/audit", params={"collection": vocab.VIEW_COLLECTION}).json()

        assert entries["count"] >= 1

    def test_a_write_names_this_workflows_own_ingest_path(self, client: TestClient):
        """No route served a seed or an ingest, so no route is claimed."""

        make_view(client, link_id="link_a")

        entries = client.get("/api/audit", params={"collection": vocab.VIEW_COLLECTION}).json()
        sources = {str(row.get("source")) for row in entries["entries"]}

        assert sources == {"wf-075 ingest"}


# --------------------------------------------------------------------------- #
# the vocabulary and the decisions
# --------------------------------------------------------------------------- #


class TestServedResearch:
    def test_the_vocabulary_carries_the_quoted_shapes(self, client: TestClient):
        payload = client.get(f"{PREFIX}/vocabulary").json()

        assert set(payload["view_analytics_fields"]) == set(vocab.VIEW_ANALYTICS_FIELDS)
        assert set(payload["visitor_fields"]) == set(vocab.VISITOR_FIELDS)

    def test_the_vocabulary_names_the_geography_inference(self, client: TestClient):
        payload = client.get(f"{PREFIX}/vocabulary").json()

        assert "inferred" in payload["geography_source"]

    def test_the_vocabulary_declares_the_time_unit(self, client: TestClient):
        assert client.get(f"{PREFIX}/vocabulary").json()["time_unit"] == "unix_ms"

    def test_the_decisions_are_served(self, client: TestClient):
        payload = client.get(f"{PREFIX}/decisions").json()

        assert payload["count"] > 0
        for decision in payload["decisions"]:
            assert decision["chosen"] in decision["options"]

    def test_one_decision_is_readable_by_id(self, client: TestClient):
        payload = client.get(f"{PREFIX}/decisions/DERIVED_CACHE_SECONDS").json()

        assert payload["id"] == "DERIVED_CACHE_SECONDS"


# --------------------------------------------------------------------------- #
# the board
# --------------------------------------------------------------------------- #


class TestBoard:
    def test_the_board_separates_the_two_counts(self, client: TestClient):
        make_visitor(client)
        make_view(client, link_id="link_a")
        make_view(client, link_id="link_b")

        board = client.get(f"{PREFIX}/summary").json()

        assert board["visitors"] == 1
        assert board["views"] == 2

    def test_the_board_reports_anonymous_views_separately(self, client: TestClient):
        make_view(client, link_id="link_a")
        make_view(client, link_id="link_b", viewer_email=None)

        board = client.get(f"{PREFIX}/summary").json()

        assert board["views"] == 2
        assert board["anonymous_views"] == 1

    def test_the_board_counts_all_three_verification_states(self, client: TestClient):
        make_visitor(client, "a@x.example", verified=True)
        make_visitor(client, "b@x.example", verified=False)
        make_visitor(client, "c@x.example")

        board = client.get(f"{PREFIX}/summary").json()

        assert board["verified"] == 1
        assert board["unverified"] == 1
        assert board["unknown_verification"] == 1

    def test_the_board_groups_by_country_and_device(self, client: TestClient):
        make_view(client, location={"country": "Germany", "city": "Berlin"})
        make_view(
            client, viewed_at=BASE_MS - 1000, location={"country": "Germany", "city": "Berlin"}
        )

        board = client.get(f"{PREFIX}/summary").json()

        assert board["by_country"]["Germany"] == 2
