"""WF-121 domain tests: the link, the view, the revisit, the alert, the write-back.

These drive :mod:`dsr.tracked_deal_link` directly, over a temporary database, with
no HTTP client. The HTTP surface has its own module, ``test_wf121_http.py``.

Every test names the rule it protects, quoting
``docs/research/digital-sales-room-workflows/wf/WF-121.md`` where the research is
the source. The clock is frozen at :data:`NOW`, so no test depends on today's
date or on a weekday that will pass - the brief calls that a time bomb.

The database is a fresh one per test, through the shared ``store`` fixture, so
this file passes alone and under ``pytest-xdist`` in any order.
"""

from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path

import dsr.tracked_deal_link as domain
import pytest
from dsr.crm_engagement import vocabulary as crm_vocabulary
from dsr.crm_integration import vocabulary as crm_integration_vocabulary
from dsr.pdf_analytics import AnalyticsBook, metrics as pdf_metrics, vocabulary as pdf_vocabulary
from dsr.store import RecordStore
from dsr.tracked_deal_link import (
    ALERT_COLLECTION,
    DEFAULT_LINK_LIFETIME_DAYS,
    DELIVERY_COLLECTION,
    ENGAGEMENT_EVENT_TYPE,
    LINK_COLLECTION,
    REVISIT_GAP_SECONDS,
    ROOM_REF,
    VIEW_COLLECTION,
    DistributionRefused,
    DwellRefused,
    LinkClosed,
    LinkNotFound,
    LinkValidationError,
    TrackedLinkBook,
    ViewerRequired,
    alert_decisions,
    engagement_record,
    group_revisits,
    normalise_link,
    normalise_view,
    page_dwell,
    refuse_document_bodies,
    revisit_index,
    timing_rows,
)

NOW = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)

DOMAIN_DIR = Path(domain.__file__).parent
FEATURE_MODULE = (
    Path(domain.__file__).parent.parent
    / "features"
    / "wf121_tracked_deal_link_distribution_with_per_page_dwell.py"
)


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture()
def room(store: RecordStore) -> dict:
    return store.create(
        "room", {"name": "Northwind", "account": "Northwind"}, actor="dana", source="test"
    )


@pytest.fixture()
def asset(store: RecordStore, room: dict) -> dict:
    return AnalyticsBook(store).register_asset(
        {
            "externalId": "deck-demo",
            "name": "Enterprise Security & Compliance Overview",
            "type": "pdf",
            "pageCount": 10,
        },
        room_id=room["id"],
        actor="dana",
        source="test",
    )


@pytest.fixture()
def book(store: RecordStore, room: dict, asset: dict) -> TrackedLinkBook:
    """The book with a frozen clock, so expiry is exact rather than approximate."""
    return TrackedLinkBook(store, now=lambda: NOW)


@pytest.fixture()
def link(book: TrackedLinkBook, room: dict) -> dict:
    """The link as the API projects it: a flat view, not a stored record."""
    return book.create_link(
        room["id"],
        {
            "asset_id": "deck-demo",
            "title": "Security overview",
            "recipients": ["a.buyer@northwind.example"],
            "crm_system": "salesforce",
            "crm_deal_id": "006-nw-5001",
        },
        actor="dana",
        source="POST /api/wf-121/links",
    )


@pytest.fixture()
def link_record(store: RecordStore, link: dict) -> dict:
    """The stored record behind the view, which the pure rules take."""
    record = store.get(link["id"])
    assert record is not None
    return record


def page_view_seconds(viewer: str, pages: int, *, started: datetime, seconds: float = 30.0) -> dict:
    """One reading, emitted page by page the way a viewer would."""
    elapsed = 0.0
    timings = []
    for page in range(1, pages + 1):
        timings.append(
            {
                "page": page,
                "seconds": seconds,
                "occurred_at": (started + timedelta(seconds=elapsed)).isoformat(),
            }
        )
        elapsed += seconds + 3
    return {"viewer": viewer, "timings": timings}


# --------------------------------------------------------------------------- #
# The vocabulary is reused, not re-declared
# --------------------------------------------------------------------------- #


class TestReusedVocabulary:
    def test_the_revisit_rule_is_wf018s_own_constant(self):
        assert REVISIT_GAP_SECONDS == pdf_vocabulary.SESSION_GAP_SECONDS

    def test_every_engagement_field_is_a_wf037_canonical_field(self):
        for field in domain.ENGAGEMENT_FIELDS:
            assert field in crm_vocabulary.CANONICAL_FIELDS, field

    def test_the_engagement_event_type_is_this_workflows_own(self):
        assert ENGAGEMENT_EVENT_TYPE == "tracked_deal_link_view"

    def test_the_crm_object_is_wf042s_deal(self):
        assert domain.ENGAGEMENT_CRM_OBJECT in crm_integration_vocabulary.CRM_OBJECTS

    def test_the_published_vocabulary_names_the_workflow_each_value_came_from(self):
        published = domain.describe_vocabulary()
        assert published["reused"]["revisit_gap_seconds"] == pdf_vocabulary.SESSION_GAP_SECONDS
        assert "WF-018" in published["reused"]["dwell_rules_source"]
        assert "WF-037" in published["reused"]["engagement_fields_source"]
        assert "WF-069" in published["reused"]["expiry_rule_source"]


# --------------------------------------------------------------------------- #
# The link
# --------------------------------------------------------------------------- #


class TestTheLink:
    def test_a_link_needs_a_target_asset(self):
        with pytest.raises(LinkValidationError) as refused:
            normalise_link({"title": "No target"}, now=NOW)
        assert refused.value.context["field"] == "asset_id"

    def test_the_lifetime_is_the_inferred_default_when_no_expiry_is_given(self):
        data = normalise_link({"asset_id": "deck-demo"}, now=NOW)
        expected = NOW + timedelta(days=DEFAULT_LINK_LIFETIME_DAYS)
        assert data["expires_at"] == expected.isoformat()

    def test_an_explicit_expiry_wins_over_the_lifetime(self):
        data = normalise_link(
            {"asset_id": "deck-demo", "expires_at": "2026-06-01", "lifetime_days": 1}, now=NOW
        )
        # WF-069's rule: a bare date means the end of that day, not its start.
        assert data["expires_at"].startswith("2026-06-01T23:59:59")

    def test_recipients_are_normalised_and_deduplicated(self):
        data = normalise_link(
            {
                "asset_id": "deck-demo",
                "recipients": ["Buyer@Northwind.example", "buyer@northwind.example"],
            },
            now=NOW,
        )
        assert data["recipients"] == ["buyer@northwind.example"]

    def test_a_recipient_that_is_not_an_email_is_refused(self):
        with pytest.raises(LinkValidationError):
            normalise_link({"asset_id": "deck-demo", "recipients": ["not-an-address"]}, now=NOW)

    def test_an_unknown_crm_system_is_refused(self):
        with pytest.raises(LinkValidationError) as refused:
            normalise_link({"asset_id": "deck-demo", "crm_system": "sugarcrm"}, now=NOW)
        assert refused.value.context["field"] == "crm_system"

    def test_a_link_is_open_before_its_expiry_and_at_the_instant(self):
        record = {"id": "l1", "data": {"expires_at": (NOW + timedelta(days=1)).isoformat()}}
        assert domain.link_state(record, now=NOW)["state"] == "active"

    def test_a_link_is_expired_strictly_after_its_expiry(self):
        record = {"id": "l1", "data": {"expires_at": NOW.isoformat()}}
        # At the instant named the link is still open; a microsecond later it is not.
        assert domain.link_state(record, now=NOW)["state"] == "active"
        assert domain.link_state(record, now=NOW + timedelta(microseconds=1))["state"] == "expired"

    def test_a_revoked_link_reads_as_revoked(self):
        record = {"id": "l1", "data": {}, "deleted_at": NOW.isoformat()}
        status = domain.link_state(record, now=NOW)
        assert status["state"] == "revoked"
        assert status["closed"] is True

    def test_a_closed_link_refuses_a_view_with_its_own_error(self):
        record = {"id": "l1", "data": {"expires_at": (NOW - timedelta(days=1)).isoformat()}}
        with pytest.raises(LinkClosed):
            domain.require_open(record, now=NOW)


# --------------------------------------------------------------------------- #
# The privacy boundary
# --------------------------------------------------------------------------- #


class TestThePrivacyBoundary:
    @pytest.mark.parametrize(
        "key",
        ["body", "content", "file", "file_bytes", "form_values", "raw_form", "text"],
    )
    def test_a_document_body_or_raw_form_value_is_refused_by_name(self, key):
        with pytest.raises(LinkValidationError) as refused:
            refuse_document_bodies({key: "secret", "viewer": "a@b.example"})
        assert refused.value.context["field"] == key

    def test_an_alias_of_a_refused_key_is_refused_too(self):
        with pytest.raises(LinkValidationError):
            normalise_view(
                {"document_id": "x", "content": "the deck", "timings": [{"page": 1, "seconds": 5}]}
            )

    def test_page_numbers_and_durations_are_accepted(self):
        refuse_document_bodies({"viewer": "a@b.example", "timings": [{"page": 1, "seconds": 5}]})


# --------------------------------------------------------------------------- #
# The view
# --------------------------------------------------------------------------- #


class TestTheView:
    def test_a_view_needs_at_least_one_page(self):
        with pytest.raises(DwellRefused):
            normalise_view({"viewer": "a@b.example", "timings": []})

    def test_timings_must_be_a_list(self):
        with pytest.raises(DwellRefused):
            normalise_view({"viewer": "a@b.example", "timings": "page 1"})

    def test_a_page_number_below_one_is_refused(self):
        with pytest.raises(DwellRefused):
            normalise_view({"viewer": "a@b.example", "timings": [{"page": 0, "seconds": 5}]})

    def test_a_duration_of_zero_is_refused(self):
        with pytest.raises(DwellRefused):
            normalise_view({"viewer": "a@b.example", "timings": [{"page": 1, "seconds": 0}]})

    def test_the_email_alias_resolves_to_the_viewer(self):
        view = normalise_view(
            {"email": "Buyer@Northwind.example", "timings": [{"page": 1, "seconds": 5}]}
        )
        assert view["viewer"] == "buyer@northwind.example"

    def test_an_internal_view_is_marked_internal(self):
        view = normalise_view(
            {
                "viewer": "dana@northwind.example",
                "is_internal": True,
                "timings": [{"page": 1, "seconds": 5}],
            }
        )
        assert view["audience"] == "internal"


# --------------------------------------------------------------------------- #
# Revisits and the shared metrics
# --------------------------------------------------------------------------- #


class TestRevisits:
    def test_a_gap_longer_than_the_shared_rule_starts_a_new_revisit(self):
        rows = [
            {"page": 1, "seconds": 20.0, "occurredAt": NOW.isoformat(), "viewer": "a@b.example"},
            {
                "page": 2,
                "seconds": 20.0,
                "occurredAt": (NOW + timedelta(seconds=REVISIT_GAP_SECONDS + 1)).isoformat(),
                "viewer": "a@b.example",
            },
        ]
        assert len(group_revisits(rows)) == 2

    def test_a_gap_inside_the_shared_rule_stays_one_revisit(self):
        rows = [
            {"page": 1, "seconds": 20.0, "occurredAt": NOW.isoformat(), "viewer": "a@b.example"},
            {
                "page": 2,
                "seconds": 20.0,
                "occurredAt": (NOW + timedelta(seconds=REVISIT_GAP_SECONDS - 1)).isoformat(),
                "viewer": "a@b.example",
            },
        ]
        assert len(group_revisits(rows)) == 1

    def test_an_explicit_session_id_does_not_override_the_gap(self):
        """A revisit is defined by the gap, so a session id cannot collapse two readings."""
        rows = [
            {
                "page": 1,
                "seconds": 5.0,
                "occurredAt": NOW.isoformat(),
                "viewer": "a@b.example",
                "sessionId": "s1",
            },
            {
                "page": 2,
                "seconds": 5.0,
                "occurredAt": (NOW + timedelta(days=1)).isoformat(),
                "viewer": "a@b.example",
                "sessionId": "s2",
            },
        ]
        assert len(group_revisits(rows)) == 2

    def test_the_revisit_index_counts_forward(self):
        first = [
            {"page": 1, "seconds": 5.0, "occurredAt": NOW.isoformat(), "viewer": "a@b.example"}
        ]
        second = [
            {
                "page": 1,
                "seconds": 5.0,
                "occurredAt": (NOW + timedelta(days=1)).isoformat(),
                "viewer": "a@b.example",
            }
        ]
        assert revisit_index([], first) == 1
        assert revisit_index(first, second) == 2

    def test_revisits_and_dwell_are_wf018s_own_arithmetic(self):
        rows = [
            {"page": 1, "seconds": 10.0, "occurredAt": NOW.isoformat(), "viewer": "a@b.example"},
            {
                "page": 2,
                "seconds": 20.0,
                "occurredAt": (NOW + timedelta(seconds=30)).isoformat(),
                "viewer": "a@b.example",
            },
        ]
        assert page_dwell(rows, page_count=2) == pdf_metrics.dwell_per_page(rows, page_count=2)
        assert group_revisits(rows) == pdf_metrics.group_sessions(
            [{**row, "sessionId": None} for row in rows]
        )


# --------------------------------------------------------------------------- #
# The engagement record and the alert
# --------------------------------------------------------------------------- #


def _engagement(link: dict, view: dict, summary: dict, revisit: int = 1) -> dict:
    return engagement_record(link, view, summary, revisit=revisit)


class TestTheEngagementRecord:
    def test_it_carries_the_wf037_canonical_names(self, link_record: dict):
        view = {
            "viewer": "a@b.example",
            "occurred_at": NOW,
            "crm_deal_id": "006-1",
            "crm_system": "salesforce",
        }
        summary = {"total_seconds": 120.0, "pages_viewed": [1, 2], "deepest_page": 2, "rows": []}
        record = _engagement(link_record, view, summary)
        for field in ("type", "occurred_at", "asset", "dwell_seconds", "buyer_email"):
            assert field in record
        assert record["type"] == ENGAGEMENT_EVENT_TYPE
        assert record["dwell_seconds"] == 120.0

    def test_it_names_the_crm_object_and_the_opportunity(self, link_record: dict):
        view = {
            "viewer": "a@b.example",
            "occurred_at": NOW,
            "crm_deal_id": "006-1",
            "crm_system": "salesforce",
        }
        record = _engagement(
            link_record,
            view,
            {"total_seconds": 1.0, "pages_viewed": [1], "deepest_page": 1, "rows": []},
        )
        assert record["crm_object"] == "deal"
        assert record["deal_id"] == "006-1"
        assert record["crm_system"] == "salesforce"

    def test_it_records_which_revisit_and_the_per_page_dwell(self, link_record: dict):
        summary = {
            "total_seconds": 30.0,
            "pages_viewed": [1, 2],
            "deepest_page": 2,
            "rows": [
                {"page": 2, "seconds": 20.0, "occurredAt": NOW.isoformat()},
                {"page": 1, "seconds": 10.0, "occurredAt": NOW.isoformat()},
            ],
        }
        record = _engagement(
            link_record, {"viewer": "a@b.example", "occurred_at": NOW}, summary, revisit=3
        )
        assert record["revisit"] == 3
        assert record["page_dwell"][0]["page"] == 1
        assert record["page_dwell"][1]["page"] == 2

    def test_a_crm_matched_viewer_carries_the_buyer_id(self, link_record: dict):
        """The other half of the researched identity source: a CRM-matched user."""
        view = normalise_view(
            {
                "viewer": "a@b.example",
                "crm_contact_id": "003-nw-1001",
                "timings": [{"page": 1, "seconds": 5}],
            }
        )
        assert view["buyer_crm_id"] == "003-nw-1001"
        record = _engagement(
            link_record,
            view,
            {"total_seconds": 5.0, "pages_viewed": [1], "deepest_page": 1, "rows": []},
            revisit=1,
        )
        assert record["buyer_crm_id"] == "003-nw-1001"


class TestTheAlert:
    def _summary(self, deepest: int, seconds: float) -> dict:
        return {
            "deepest_page": deepest,
            "total_seconds": seconds,
            "pages_viewed": list(range(1, deepest + 1)),
        }

    def test_a_first_view_alerts_the_rep(self, link_record: dict):
        decisions = alert_decisions(
            link_record, {"audience": "external"}, self._summary(1, 10.0), prior_views=0, revisit=1
        )
        assert [d["trigger"] for d in decisions] == ["first_view"]

    def test_a_return_visit_alerts_and_deep_reads_alert_again(self, link_record: dict):
        decisions = alert_decisions(
            link_record, {"audience": "external"}, self._summary(9, 300.0), prior_views=3, revisit=2
        )
        triggers = [d["trigger"] for d in decisions]
        assert triggers[0] == "return_visit"
        assert "page_threshold" in triggers
        assert "dwell_threshold" in triggers

    def test_an_internal_view_raises_no_alert(self, link_record: dict):
        decisions = alert_decisions(
            link_record, {"audience": "internal"}, self._summary(9, 300.0), prior_views=0, revisit=1
        )
        assert decisions == []

    def test_the_page_threshold_is_the_links_own(self, link_record: dict):
        record = {**link_record, "data": {**link_record["data"], "alert_on_page": 8}}
        decisions = alert_decisions(
            record, {"audience": "external"}, self._summary(4, 30.0), prior_views=1, revisit=2
        )
        assert "page_threshold" not in [d["trigger"] for d in decisions]


# --------------------------------------------------------------------------- #
# The book: writes through the audited store
# --------------------------------------------------------------------------- #


def _fake_sync(seen: list[dict]):
    class _Fake:
        def record_event(self, room_id, payload, *, actor=None, source, fire_queue=True):
            seen.append({"room_id": room_id, "payload": dict(payload), "source": source})
            return {
                "engagement": {"id": "eng_1", "data": dict(payload)},
                "queue": {"id": "q_1", "data": {"state": "pending"}},
                "fired": bool(fire_queue),
                "result": None,
            }

        def drain(self, room_id, *, actor=None, source, limit=100):
            return {
                "room_id": room_id,
                "counts": {"considered": 0, "synced": 0, "failed": 0, "blocked": 0, "skipped": 0},
                "results": [],
            }

    return _Fake()


class TestTheBook:
    def test_a_link_belongs_to_a_room_that_exists(self, book: TrackedLinkBook):
        with pytest.raises(LinkNotFound):
            book.create_link(
                "room_absent", {"asset_id": "deck-demo"}, source="POST /api/wf-121/links"
            )

    def test_a_link_needs_an_asset_in_the_library(self, book: TrackedLinkBook, room: dict):
        with pytest.raises(LinkValidationError) as refused:
            book.create_link(
                room["id"], {"asset_id": "no-such-asset"}, source="POST /api/wf-121/links"
            )
        assert refused.value.context["field"] == "asset_id"

    def test_a_link_copies_the_asset_name_and_page_count(self, link: dict):
        assert link["asset_name"] == "Enterprise Security & Compliance Overview"
        assert link["page_count"] == 10

    def test_a_link_resolves_by_its_share_url(self, book: TrackedLinkBook, link: dict):
        book.attach_share_url(
            link["id"], "https://share.example/one", source="POST /api/wf-121/links"
        )
        resolved = book.resolve_link("https://share.example/one")
        assert resolved["id"] == link["id"]

    def test_an_unknown_reference_is_not_found(self, book: TrackedLinkBook):
        with pytest.raises(LinkNotFound):
            book.resolve_link("nope")

    def test_a_view_is_written_through_the_audited_store(
        self, store: RecordStore, book: TrackedLinkBook, link: dict
    ):
        book.record_view(
            link["id"],
            page_view_seconds("a.buyer@northwind.example", 3, started=NOW),
            source="POST /api/wf-121/links/{link_id}/views",
        )
        rows = store.find(VIEW_COLLECTION, {"linkId": link["id"]})
        assert len(rows) == 3
        for row in rows:
            assert row["data"]["audience"] == "external"
            assert row["data"]["linkId"] == link["id"]

    def test_a_second_day_reading_becomes_the_next_revisit(self, book: TrackedLinkBook, link: dict):
        viewer = "a.buyer@northwind.example"
        first = book.record_view(
            link["id"], page_view_seconds(viewer, 4, started=NOW), source="POST views"
        )
        second = book.record_view(
            link["id"],
            page_view_seconds(viewer, 6, started=NOW + timedelta(days=1)),
            source="POST views",
        )
        assert first["revisit"] == 1
        assert second["revisit"] == 2

    def test_a_first_view_raises_a_rep_alert(
        self, store: RecordStore, book: TrackedLinkBook, link: dict
    ):
        book.record_view(
            link["id"],
            page_view_seconds("a.buyer@northwind.example", 3, started=NOW),
            source="POST views",
        )
        alerts = store.find(ALERT_COLLECTION, {"link_id": link["id"]})
        assert {row["data"]["trigger"] for row in alerts} == {"first_view", "page_threshold"}

    def test_a_view_that_needs_a_viewer_and_has_none_is_refused(
        self, book: TrackedLinkBook, link: dict
    ):
        with pytest.raises(ViewerRequired):
            book.record_view(
                link["id"], {"timings": [{"page": 1, "seconds": 5}]}, source="POST views"
            )

    def test_an_anonymous_view_is_allowed_when_the_link_asks_for_none(
        self, book: TrackedLinkBook, room: dict
    ):
        anonymous = book.create_link(
            room["id"],
            {"asset_id": "deck-demo", "require_viewer": False},
            source="POST /api/wf-121/links",
        )
        result = book.record_view(
            anonymous["id"], {"timings": [{"page": 1, "seconds": 5}]}, source="POST views"
        )
        assert result["viewer"] is None

    def test_each_viewers_revisits_and_alerts_are_their_own(
        self, book: TrackedLinkBook, link: dict
    ):
        """One viewer's reading must not become another's second revisit.

        The regression this guards is a prior-rows lookup keyed on a viewer that
        is ``None`` for an anonymous reader: a truthiness test then reads every
        viewer's rows, and an anonymous reader inherits a stranger's alerts.
        """
        first = book.record_view(
            link["id"],
            page_view_seconds("a.buyer@northwind.example", 2, started=NOW),
            source="POST views",
        )
        second = book.record_view(
            link["id"],
            page_view_seconds("procurement@northwind.example", 2, started=NOW),
            source="POST views",
        )
        assert first["revisit"] == 1
        assert second["revisit"] == 1
        assert [alert["trigger"] for alert in second["alerts"]] == ["first_view"]

    def test_an_untracked_link_refuses_its_views(self, book: TrackedLinkBook, room: dict):
        untracked = book.create_link(
            room["id"],
            {"asset_id": "deck-demo", "tracking_enabled": False},
            source="POST /api/wf-121/links",
        )
        with pytest.raises(DwellRefused):
            book.record_view(
                untracked["id"],
                {"viewer": "a@b.example", "timings": [{"page": 1, "seconds": 5}]},
                source="POST views",
            )

    def test_the_engagement_record_is_handed_to_the_wf037_write_back(
        self, store: RecordStore, room: dict, asset: dict
    ):
        seen: list[dict] = []
        book = TrackedLinkBook(store, now=lambda: NOW, engagement=lambda: _fake_sync(seen))
        link = book.create_link(
            room["id"], {"asset_id": "deck-demo"}, source="POST /api/wf-121/links"
        )
        book.record_view(
            link["id"],
            page_view_seconds("a.buyer@northwind.example", 2, started=NOW),
            source="POST views",
        )
        assert len(seen) == 1
        assert seen[0]["payload"]["type"] == ENGAGEMENT_EVENT_TYPE
        assert seen[0]["payload"]["buyer_email"] == "a.buyer@northwind.example"
        assert seen[0]["room_id"] == room["id"]

    def test_recording_a_view_writes_a_real_engagement_and_queue_row(
        self, store: RecordStore, book: TrackedLinkBook, link: dict
    ):
        """No fake here: the real WF-037 write-back records the row and enqueues."""
        result = book.record_view(
            link["id"],
            page_view_seconds("a.buyer@northwind.example", 2, started=NOW),
            source="POST views",
        )
        engagement_id = result["engagement"]["engagement_id"]
        assert engagement_id
        row = store.get(engagement_id)
        assert row is not None and row["collection"] == "crm_engagement"
        assert row["data"]["sync_state"] == "pending"
        assert result["engagement"]["queue_state"] == "pending"

    def test_an_internal_view_is_recorded_and_writes_no_engagement(
        self, store: RecordStore, book: TrackedLinkBook, link: dict
    ):
        result = book.record_view(
            link["id"],
            {
                "viewer": "dana@northwind.example",
                "is_internal": True,
                "timings": [{"page": 1, "seconds": 5}],
            },
            source="POST views",
        )
        assert result["engagement"] is None
        assert result["alerts"] == []

    def test_the_analytics_count_buyers_only(self, book: TrackedLinkBook, link: dict):
        book.record_view(
            link["id"],
            page_view_seconds("a.buyer@northwind.example", 4, started=NOW),
            source="POST views",
        )
        book.record_view(
            link["id"],
            {
                "viewer": "dana@northwind.example",
                "is_internal": True,
                "timings": [{"page": 1, "seconds": 5}],
            },
            source="POST views",
        )
        analytics = book.link_analytics(link["id"])
        assert analytics["readings"] == 4
        assert [v["viewer"] for v in analytics["viewers"]] == ["a.buyer@northwind.example"]
        assert analytics["audience"] == "external"

    def test_the_analytics_block_reports_dwell_drop_off_and_unread_pages(
        self, book: TrackedLinkBook, link: dict
    ):
        book.record_view(
            link["id"],
            page_view_seconds("a.buyer@northwind.example", 4, started=NOW),
            source="POST views",
        )
        analytics = book.link_analytics(link["id"])
        assert analytics["page_count"] == 10
        assert len(analytics["time_spent_per_page"]) == 10
        assert len(analytics["drop_off_per_page"]) == 10
        assert 5 in analytics["pages_never_read"]

    def test_a_distribution_records_one_row_per_recipient(
        self, store: RecordStore, book: TrackedLinkBook, link: dict
    ):
        result = book.distribute(
            link["id"],
            {"recipients": ["a@northwind.example", "b@northwind.example", "a@northwind.example"]},
            source="POST /api/wf-121/links/{link_id}/deliveries",
        )
        assert result["count"] == 2
        assert len(store.find(DELIVERY_COLLECTION, {"link_id": link["id"]})) == 2

    def test_a_distribution_with_no_recipient_is_refused(self, book: TrackedLinkBook, room: dict):
        bare = book.create_link(
            room["id"], {"asset_id": "deck-demo"}, source="POST /api/wf-121/links"
        )
        with pytest.raises(DistributionRefused):
            book.distribute(bare["id"], {"recipients": []}, source="POST deliveries")

    def test_revoking_a_link_closes_it_and_keeps_its_history(
        self, store: RecordStore, book: TrackedLinkBook, link: dict
    ):
        book.record_view(
            link["id"],
            page_view_seconds("a.buyer@northwind.example", 2, started=NOW),
            source="POST views",
        )
        revoked = book.revoke_link(link["id"], source="DELETE /api/wf-121/links/{link_id}")
        assert revoked["state"] == "revoked"
        with pytest.raises(LinkClosed):
            book.record_view(
                link["id"],
                {"viewer": "a@b.example", "timings": [{"page": 1, "seconds": 5}]},
                source="POST views",
            )
        # The rows survive the revocation: a revoke that erased history would be
        # a revoke nobody can audit.
        assert store.find(VIEW_COLLECTION, {"linkId": link["id"]})

    def test_one_viewer_page_record_shows_the_revisits(self, book: TrackedLinkBook, link: dict):
        viewer = "a.buyer@northwind.example"
        book.record_view(link["id"], page_view_seconds(viewer, 3, started=NOW), source="POST views")
        book.record_view(
            link["id"],
            page_view_seconds(viewer, 3, started=NOW + timedelta(days=2)),
            source="POST views",
        )
        detail = book.list_records_for_viewer(link["id"], viewer)
        assert detail["readings"] == 6
        assert len(detail["revisits"]) == 2

    def test_the_summary_counts_the_states_a_reviewer_checks(
        self, store: RecordStore, book: TrackedLinkBook, link: dict
    ):
        book.distribute(
            link["id"], {"recipients": ["a@northwind.example"]}, source="POST deliveries"
        )
        book.record_view(
            link["id"],
            page_view_seconds("a.buyer@northwind.example", 2, started=NOW),
            source="POST views",
        )
        summary = book.summary(link["room_id"])
        assert summary["links"] == 1
        assert summary["open"] == 1
        assert summary["views"] == 2
        assert summary["deliveries"] == 1
        assert summary["alerts"] == 1


# --------------------------------------------------------------------------- #
# Architecture: the dependency direction the contract fixes
# --------------------------------------------------------------------------- #

#: The only ``dsr`` packages a module in this domain package may import. The
#: brief's reuse instruction is why the sibling domain packages are here, and
#: ``dsr.api`` / ``dsr.features`` are absent because a domain module must never
#: reach for the app or for another workflow's feature module.
ALLOWED_DSR_PREFIXES = (
    "dsr.store",
    "dsr.db.audited",
    "dsr.tracked_deal_link",
    "dsr.pdf_analytics",
    "dsr.crm_engagement",
    "dsr.crm_integration",
    "dsr.link_gating",
)


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
            "dsr.features.wf121_tracked_deal_link_distribution_with_per_page_dwell"
        )
        assert module.FEATURE["id"] == "wf-121-tracked-deal-link"
        assert module.FEATURE["ticket"] == "WF-121"
        assert module.router.prefix == "/api/wf-121"
        assert list(module.EXCEPTION_HANDLERS) == [domain.TrackedLinkError]

    def test_the_error_type_is_this_workflows_own(self):
        assert domain.TrackedLinkError.__module__ == "dsr.tracked_deal_link.errors"


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #


class TestSeed:
    def test_the_demo_seeds_a_link_with_readings_and_a_returning_buyer(
        self, db, store: RecordStore
    ):
        # The seed is handed ``db``, so the room must be in that same database.
        # ``store`` is in-memory; build a second store over the file so the
        # seeding and the reading agree on one database.
        scoped = RecordStore(db)
        room = scoped.create("room", {"name": "Northwind"}, actor="dana", source="test")
        summary = domain_feature_seed(
            db, {"room_ids": [(room["id"], "Northwind")], "now": NOW, "rng": None}
        )
        assert isinstance(summary, str) and summary
        # A Windows console prints this string; a non-cp1252 character once broke
        # the whole seeder, so the encoding is asserted rather than assumed.
        summary.encode("cp1252")
        links = scoped.find(LINK_COLLECTION, {ROOM_REF: room["id"]})
        assert len(links) == 1
        views = scoped.find(VIEW_COLLECTION, {"linkId": links[0]["id"]})
        assert len(views) > 20
        alerts = scoped.find(ALERT_COLLECTION, {"link_id": links[0]["id"]})
        triggers = {row["data"]["trigger"] for row in alerts}
        assert "return_visit" in triggers
        assert "first_view" in triggers

    def test_the_demo_raises_no_alert_for_the_internal_preview(self, db):
        scoped = RecordStore(db)
        room = scoped.create("room", {"name": "Northwind"}, actor="dana", source="test")
        domain_feature_seed(db, {"room_ids": [(room["id"], "Northwind")], "now": NOW, "rng": None})
        alerts = scoped.list(ALERT_COLLECTION, limit=100)
        assert alerts
        assert "dana@northwind.example" not in {row["data"].get("viewer") for row in alerts}

    def test_the_demo_reports_nothing_when_there_is_no_room(self, db):
        summary = domain_feature_seed(db, {"room_ids": [], "now": NOW, "rng": None})
        assert "0 tracked links" in summary
        summary.encode("cp1252")


def domain_feature_seed(db, context):
    """Call the feature module's ``seed`` without importing the feature lazily."""
    import importlib

    module = importlib.import_module(
        "dsr.features.wf121_tracked_deal_link_distribution_with_per_page_dwell"
    )
    return module.seed(db, context)


# --------------------------------------------------------------------------- #
# The pure helpers WF-018 owns, referenced so a rename cannot detach this file
# --------------------------------------------------------------------------- #


def test_timing_rows_use_wf018s_own_field_names(link: dict):
    view = normalise_view(
        {
            "viewer": "a@b.example",
            "session_id": "s1",
            "timings": [{"page": 1, "seconds": 5, "occurred_at": NOW.isoformat()}],
        }
    )
    rows = timing_rows({"id": "l1", "data": {"asset_id": "deck-demo"}}, view)
    assert set(rows[0]) >= {"page", "seconds", "occurredAt", "viewer", "sessionId", "audience"}
    # And the shared metric takes them unchanged.
    assert page_dwell(rows, page_count=1)[0]["reads"] == 1
