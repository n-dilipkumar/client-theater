"""Tests for WF-018: read per-page dwell time and drop-off inside a PDF.

The claims under test come from
``docs/research/digital-sales-room-workflows/wf/WF-018.md``:

* the asset snapshot fields the ``asset.viewed`` / ``asset.downloaded`` payload
  embeds - ``name``, ``type``, ``shareUrl``, ``isInternal``, ``tags``,
  ``downloadEnabled``, ``trackingEnabled``;
* **PDF Analytics** - "For multi-page PDFs, we're able to show two additional
  metrics: **Time spent per page:** the average amount of time that's spent per
  page ... / **Drop off per page:** understand when someone stops looking at
  your content";
* **Video Analytics** - "For self-hosted videos, we're able to show the average
  watch time of the video";
* the external-only rule and its one exception - "Dock's analytics only show
  engagement from external users (i.e. buyers and customers). The one exception
  is 'Shares' which is an internal metric";
* the data flow - a trackable asset link, a viewer emitting per-page timing;
* and, just as load-bearing, the two places the research says there is *no*
  documented endpoint, which is why the ingest is ours and every judgement call
  is published at ``/api/wf-018/inferences``.

Layout follows the ports: the pure half first, so each researched rule is checked
against a literal example rather than through a database, then the book, then
the HTTP surface through this feature's own router, then the audit-source rule
the build brief names.
"""

from __future__ import annotations

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dsr.api import app
from dsr.db.audited import AuditedDatabase
from dsr.features import load_feature
from dsr.pdf_analytics import (
    ASSET_SNAPSHOT_FIELDS,
    ASSET_TYPES,
    AUDIENCES,
    GRAINS,
    INFERENCES,
    INTERNAL_ONLY_METRICS,
    MIN_PAGES_FOR_PDF_ANALYTICS,
    SESSION_GAP_SECONDS,
    TIMING_COLLECTION,
    WEBHOOK_EVENT_TYPES,
    AnalyticsBook,
    NotFound,
    PdfAnalyticsUnavailable,
    TrackingDisabled,
    UnknownEventType,
    ValidationError,
    VideoAnalyticsUnavailable,
    describe_inferences,
    metrics,
)
from dsr.pdf_analytics import vocabulary as vocab
from dsr.store import RecordStore

#: The feature's own prefix. Duplicated here rather than imported so a change to
#: the prefix has to be made deliberately in the test as well, which is the
#: point of a test: a renamed route should fail, not follow silently.
PREFIX = "/api/wf-018"

#: The feature module name, as the host discovers it.
MODULE = "wf018_read_per_page_dwell_time_and_drop_off_"

#: What the pure-domain tests pass as ``source``. Deliberately the exact shape a
#: route passes, so a test asserting on an audit row is asserting on the real
#: thing rather than on a convenient placeholder.
SOURCE = f"POST {PREFIX}/assets"

NOW = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)


def at(seconds: float = 0.0) -> str:
    return (NOW + timedelta(seconds=seconds)).isoformat()


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture()
def store(tmp_path):
    db = AuditedDatabase(tmp_path / "wf018.db", mirror_dir=tmp_path / "mirror")
    yield RecordStore(db)
    db.close()


@pytest.fixture()
def book(store):
    return AnalyticsBook(store)


@pytest.fixture()
def room(store):
    return store.create(
        "room",
        {"name": "Northwind — Enterprise Evaluation", "account": "Northwind Traders", "owner": "dana"},
        actor="dana",
    )


@pytest.fixture()
def other_room(store):
    return store.create("room", {"name": "Contoso — Pilot", "account": "Contoso"}, actor="dana")


def deck(book, **overrides):
    """A 12-page PDF, trackable, in the library."""
    payload = {
        "name": "Enterprise Security Overview",
        "type": "pdf",
        "pageCount": 12,
        "externalId": "asset-deck",
        "shareUrl": "https://share.example/assets/asset-deck",
        "tags": ["security"],
        "downloadEnabled": True,
        "trackingEnabled": True,
    }
    payload.update(overrides)
    return book.register_asset(payload, actor="dana", source=SOURCE)


def read(book, asset_id, viewer, pages, *, room_id=None, session="s1", internal=False, start=0.0):
    """One reader's per-page timing, through the real ingest."""
    elapsed = start
    timings = []
    for page in range(1, pages + 1):
        timings.append({"page": page, "seconds": 10.0 * page, "occurred_at": at(elapsed)})
        elapsed += 11.0
    return book.record_timings(
        asset_id,
        {
            "viewer": viewer,
            "session_id": session,
            "room_id": room_id,
            "isInternal": internal,
            "timings": timings,
        },
        source=f"POST {PREFIX}/assets/{asset_id}/timings",
    )


@pytest.fixture()
def http(monkeypatch):
    """A client over a temporary database, with the book built per request."""
    tmp = tempfile.TemporaryDirectory()
    monkeypatch.setenv("DSR_DB_PATH", str(Path(tmp.name) / "wf018-http.db"))
    monkeypatch.setenv("DSR_AUDIT_DIR", str(Path(tmp.name) / "audit"))
    monkeypatch.setattr("dsr.api.FRONTEND_DIST", Path(tmp.name) / "absent-frontend")
    with TestClient(app) as client:
        yield client
    tmp.cleanup()


@pytest.fixture()
def http_room(http):
    return http.post(
        "/api/records/room", json={"name": "Northwind", "account": "Northwind"}, params={"actor": "dana"}
    ).json()


# --------------------------------------------------------------------------- #
# The plugin registration itself
# --------------------------------------------------------------------------- #


def test_feature_is_discovered_and_mounted_without_editing_the_host(http):
    entry = next(
        f
        for f in http.get("/api/features").json()["features"]
        if f["id"] == "wf-018-read-per-page-dwell-time-and-drop-off-"
    )
    assert entry["prefix"] == PREFIX
    assert entry["ticket"] == "WF-018"
    assert entry["exception_handlers"] == ["AnalyticsError"]
    assert len(entry["routes"]) == 12


def test_the_whole_host_reports_no_failed_feature(http):
    """One broken feature must not take the product offline, and mine is not broken."""
    body = http.get("/api/features").json()
    assert body["failed_count"] == 0, body["failed"]


def test_the_feature_is_navigable_without_editing_app_jsx(http):
    entry = next(
        f for f in http.get("/api/features").json()["features"] if f["prefix"] == PREFIX
    )
    assert entry["nav"] == [{"id": "pdf-analytics", "label": "PDF analytics"}]


def test_frontend_descriptor_id_matches_the_backend_feature_id():
    descriptor = (
        Path(__file__).resolve().parents[2]
        / "frontend"
        / "src"
        / "features"
        / "wf-018-read-per-page-dwell-time-and-drop-off-"
        / "index.jsx"
    )
    text = descriptor.read_text(encoding="utf-8")
    module = load_feature(MODULE)
    assert module.FEATURE["id"] in text
    assert f"id: {module.FEATURE['id']!r}" in text


def test_feature_module_does_not_import_the_shared_app():
    """A guard exists in test_features.py; this states the reason locally."""
    source = Path(load_feature(MODULE).__file__).read_text(encoding="utf-8")
    assert "dsr.api" not in source
    assert "from dsr.deps import" in source


def test_the_prefix_is_ours_alone(http):
    served = {
        (method, route["path"])
        for feature in http.get("/api/features").json()["features"]
        for route in feature["routes"]
        for method in route["methods"]
    }
    mine = {key for key in served if key[1].startswith(PREFIX)}
    others = {key for key in served if not key[1].startswith(PREFIX)}
    assert len(mine) == 12
    assert not mine & others


def test_room_scoped_paths_are_room_scoped(http):
    """The brief's rule: a room-scoped path keeps the room in the path."""
    paths = {route.path for route in load_feature(MODULE).router.routes}
    room_paths = {path for path in paths if "room" in path}
    assert room_paths == {f"{PREFIX}/rooms/{{room_id}}/assets"}


def test_only_one_error_type_is_claimed(http):
    """Claiming a builtin would let this feature intercept errors product-wide."""
    claimed = list(load_feature(MODULE).EXCEPTION_HANDLERS)
    assert claimed == [load_feature(MODULE).AnalyticsError]
    assert not any(issubclass(error, Exception) and error.__module__ == "builtins" for error in claimed)


def test_the_error_type_is_this_workflows_own(http):
    """...because the host refuses two features mapping one type."""
    error = load_feature(MODULE).AnalyticsError
    assert error.__module__ == "dsr.pdf_analytics.errors"
    for other in (
        "dsr.rules.RuleError",
        "dsr.library.LibraryError",
        "dsr.search.errors.SearchError",
        "dsr.crm.errors.CrmError",
    ):
        module_name, _, class_name = other.rpartition(".")
        try:
            sibling = getattr(__import__(module_name, fromlist=[class_name]), class_name)
        except Exception:  # a sibling feature's module may not be importable here
            continue
        assert sibling is not error


# --------------------------------------------------------------------------- #
# The sourced vocabulary
# --------------------------------------------------------------------------- #


def test_asset_types_are_exactly_pdf_and_video():
    assert ASSET_TYPES == ("pdf", "video")


def test_webhook_event_types_are_the_three_named():
    assert WEBHOOK_EVENT_TYPES == ("asset.viewed", "asset.downloaded", "asset.shared")
    assert "asset.shared" not in (
        "asset.viewed",
        "asset.downloaded",
    ), "guarded so a rename of the inferred type is a deliberate edit here too"


def test_asset_snapshot_fields_are_exactly_the_researched_seven():
    assert ASSET_SNAPSHOT_FIELDS == (
        "name",
        "type",
        "shareUrl",
        "isInternal",
        "tags",
        "downloadEnabled",
        "trackingEnabled",
    )


def test_shares_is_the_only_internal_metric():
    """Sourced: "The one exception is 'Shares' which is an internal metric"."""
    assert INTERNAL_ONLY_METRICS == ("shares",)


def test_audiences_are_the_two_the_research_names():
    assert AUDIENCES == ("external", "internal")


def test_multi_page_is_the_threshold_for_pdf_analytics():
    assert MIN_PAGES_FOR_PDF_ANALYTICS == 2


def test_a_reading_session_gap_is_declared():
    assert SESSION_GAP_SECONDS > 0


def test_grains_are_declared_for_the_core_analytics_axis():
    assert GRAINS == ("day", "week", "month")


# -- snapshot normalisation -------------------------------------------------- #


def test_a_minimal_snapshot_is_accepted():
    """The researched list is what the payload *carries*, not what it requires."""
    clean = vocab.normalise_snapshot({"name": "Deck", "type": "pdf"})
    assert clean["name"] == "Deck"
    assert clean["type"] == "pdf"
    assert clean["isInternal"] is False
    assert clean["downloadEnabled"] is True
    assert clean["trackingEnabled"] is True


def test_a_snapshot_needs_a_name():
    with pytest.raises(ValidationError) as caught:
        vocab.normalise_snapshot({"type": "pdf"})
    assert caught.value.context["field"] == "name"


def test_a_blank_name_is_not_a_name():
    with pytest.raises(ValidationError):
        vocab.normalise_snapshot({"name": "   ", "type": "pdf"})


def test_a_snapshot_needs_a_researched_type():
    with pytest.raises(ValidationError) as caught:
        vocab.normalise_snapshot({"name": "Deck", "type": "spreadsheet"})
    assert caught.value.context["allowed"] == list(ASSET_TYPES)


def test_type_is_case_insensitive_and_trimmed():
    assert vocab.normalise_snapshot({"name": "D", "type": " PDF "})["type"] == "pdf"


def test_a_video_defaults_to_self_hosted():
    """Otherwise defaulting the other way would hide the analytics silently."""
    assert vocab.normalise_snapshot({"name": "V", "type": "video"})["selfHosted"] is True


def test_a_pdf_gets_no_self_hosted_flag():
    assert "selfHosted" not in vocab.normalise_snapshot({"name": "D", "type": "pdf", "pageCount": 4})


def test_researched_camel_case_survives_normalisation():
    clean = vocab.normalise_snapshot(
        {
            "name": "D",
            "type": "pdf",
            "pageCount": 3,
            "shareUrl": "https://s.example/a",
            "isInternal": True,
            "downloadEnabled": False,
            "trackingEnabled": False,
        }
    )
    assert clean["shareUrl"] == "https://s.example/a"
    assert clean["isInternal"] is True
    assert clean["downloadEnabled"] is False
    assert clean["trackingEnabled"] is False


def test_snake_case_input_is_normalised_to_the_researched_spelling():
    clean = vocab.normalise_snapshot(
        {"name": "D", "type": "pdf", "page_count": 8, "share_url": "https://s.example/a", "tracking_enabled": False}
    )
    assert clean["pageCount"] == 8
    assert clean["shareUrl"] == "https://s.example/a"
    assert clean["trackingEnabled"] is False


def test_the_string_false_is_read_not_truth_tested():
    """``bool("false")`` is True, which would silently untrack an asset."""
    assert vocab.normalise_snapshot({"name": "D", "type": "pdf", "trackingEnabled": "false"})[
        "trackingEnabled"
    ] is False
    assert vocab.normalise_snapshot({"name": "D", "type": "pdf", "trackingEnabled": "no"})[
        "trackingEnabled"
    ] is False
    assert vocab.normalise_snapshot({"name": "D", "type": "pdf", "trackingEnabled": "true"})[
        "trackingEnabled"
    ] is True


def test_numeric_booleans_are_accepted():
    assert vocab.normalise_snapshot({"name": "D", "type": "pdf", "isInternal": 1})["isInternal"] is True
    assert vocab.normalise_snapshot({"name": "D", "type": "pdf", "isInternal": 0})["isInternal"] is False


def test_a_boolean_that_is_neither_is_refused():
    with pytest.raises(ValidationError) as caught:
        vocab.normalise_snapshot({"name": "D", "type": "pdf", "trackingEnabled": "sometimes"})
    assert caught.value.context["field"] == "trackingEnabled"


def test_tags_may_be_a_list():
    assert vocab.normalise_snapshot({"name": "D", "type": "pdf", "tags": ["a", " b "]})["tags"] == ["a", "b"]


def test_tags_may_be_a_comma_separated_string():
    """A query string has no arrays, and a tag filter is common enough to want."""
    assert vocab.normalise_snapshot({"name": "D", "type": "pdf", "tags": "a, b ,c"})["tags"] == ["a", "b", "c"]


def test_tags_may_be_absent():
    assert "tags" not in vocab.normalise_snapshot({"name": "D", "type": "pdf"})


def test_tags_must_be_a_list_or_a_string():
    with pytest.raises(ValidationError) as caught:
        vocab.normalise_snapshot({"name": "D", "type": "pdf", "tags": {"a": 1}})
    assert caught.value.context["field"] == "tags"


def test_a_page_count_must_be_a_positive_whole_number():
    with pytest.raises(ValidationError):
        vocab.normalise_snapshot({"name": "D", "type": "pdf", "pageCount": 0})
    with pytest.raises(ValidationError):
        vocab.normalise_snapshot({"name": "D", "type": "pdf", "pageCount": "many"})
    with pytest.raises(ValidationError):
        vocab.normalise_snapshot({"name": "D", "type": "pdf", "pageCount": True})


def test_an_empty_external_id_is_dropped_rather_than_stored_blank():
    assert "externalId" not in vocab.normalise_snapshot({"name": "D", "type": "pdf", "externalId": "  "})


def test_an_undeclared_field_is_kept_verbatim():
    """No migration, no typed column: a team's own field just works."""
    clean = vocab.normalise_snapshot(
        {"name": "D", "type": "pdf", "pricingTier": "gold", "review": {"owner": "dana", "cycle": 42}}
    )
    assert clean["pricingTier"] == "gold"
    assert clean["review"] == {"owner": "dana", "cycle": 42}


def test_a_teams_own_field_survives_the_store_and_is_indexable(store, book):
    """The dynamic index has to see it, or the flexibility is only cosmetic."""
    book.register_asset(
        {"name": "D", "type": "pdf", "pageCount": 3, "pricingTier": "gold"}, source=SOURCE
    )
    found = store.find("contentAsset", {"pricingTier": "gold"}, limit=10)
    assert [r["data"]["name"] for r in found] == ["D"]


# -- audience --------------------------------------------------------------- #


def test_an_interaction_without_isinternal_counts_as_a_buyer():
    assert vocab.audience_of({}) == "external"
    assert vocab.audience_of({"isInternal": False}) == "external"


def test_an_explicit_internal_flag_is_internal():
    assert vocab.audience_of({"isInternal": True}) == "internal"


def test_audience_of_tolerates_a_missing_body():
    assert vocab.audience_of(None) == "external"


# -- validators ------------------------------------------------------------- #


@pytest.mark.parametrize("value", ["asset.viewed", "asset.downloaded", "asset.shared"])
def test_the_named_event_types_validate(value):
    assert vocab.require_event_type(value) == value


def test_an_unknown_event_type_is_refused_with_the_allowed_set():
    with pytest.raises(UnknownEventType) as caught:
        vocab.require_event_type("asset.deleted")
    assert caught.value.context["allowed"] == list(WEBHOOK_EVENT_TYPES)
    assert caught.value.status_code == 400


def test_a_missing_event_type_is_refused():
    with pytest.raises(UnknownEventType):
        vocab.require_event_type(None)


def test_unknown_event_type_is_a_validation_error_too():
    assert issubclass(UnknownEventType, ValidationError)


def test_grain_validates():
    assert vocab.require_grain("WEEK") == "week"
    with pytest.raises(ValidationError):
        vocab.require_grain("fortnight")


def test_seconds_must_be_positive_and_finite():
    assert vocab.as_seconds(41.5) == 41.5
    assert vocab.as_seconds("30") == 30.0
    for bad in (0, -1, None, True, "long", float("inf"), float("nan")):
        with pytest.raises(ValidationError):
            vocab.as_seconds(bad)


def test_pages_must_be_whole_and_at_least_one():
    assert vocab.as_int(4, "page", minimum=1) == 4
    for bad in (0, -3, None, True, "two"):
        with pytest.raises(ValidationError):
            vocab.as_int(bad, "page", minimum=1)


def test_every_refusal_carries_a_status_and_a_code():
    for error in (
        ValidationError("x"),
        NotFound("x"),
        PdfAnalyticsUnavailable("x"),
        VideoAnalyticsUnavailable("x"),
        TrackingDisabled("x"),
    ):
        assert 400 <= error.status_code < 500
        assert error.payload()["error"] == error.code
        assert error.payload()["detail"] == "x"


# --------------------------------------------------------------------------- #
# Metrics: the researched arithmetic, as pure functions
# --------------------------------------------------------------------------- #


def rows(*entries):
    return [dict(entry) for entry in entries]


# -- time spent per page ---------------------------------------------------- #


def test_dwell_is_the_average_seconds_per_page():
    result = metrics.dwell_per_page(
        rows({"page": 1, "seconds": 10}, {"page": 1, "seconds": 30}, {"page": 2, "seconds": 5})
    )
    assert [entry["average_seconds"] for entry in result] == [20.0, 5.0]
    assert [entry["reads"] for entry in result] == [2, 1]
    assert [entry["total_seconds"] for entry in result] == [40.0, 5.0]


def test_dwell_lists_every_page_including_the_ones_nobody_read():
    """Otherwise a table that stops at the last read reads as "the deck ends here"."""
    result = metrics.dwell_per_page(rows({"page": 1, "seconds": 10}), page_count=6)
    assert [entry["page"] for entry in result] == [1, 2, 3, 4, 5, 6]
    assert [entry["reads"] for entry in result] == [1, 0, 0, 0, 0, 0]
    assert result[5]["average_seconds"] is None


def test_dwell_rounds_to_one_decimal():
    result = metrics.dwell_per_page(rows({"page": 1, "seconds": 10}, {"page": 1, "seconds": 11}))
    assert result[0]["average_seconds"] == 10.5


def test_dwell_ignores_a_row_with_no_page():
    assert metrics.dwell_per_page(rows({"page": 0, "seconds": 99}), page_count=1)[0]["reads"] == 0


def test_dwell_grows_past_a_shrunken_page_count():
    """A re-uploaded document leaves stale rows; the table must not lose them."""
    result = metrics.dwell_per_page(rows({"page": 9, "seconds": 3}), page_count=4)
    assert result[-1]["page"] == 9


def test_dwell_of_no_rows_is_a_single_unread_page():
    result = metrics.dwell_per_page([])
    assert result == [{"page": 1, "reads": 0, "total_seconds": 0.0, "average_seconds": None}]


# -- drop off per page ------------------------------------------------------ #


def curve(page_count=4, depths=(4, 4, 2, 2)):
    """Four readers: two finished, two stopped after page 2."""
    sessions = [{"deepest_page": depth} for depth in depths]
    return metrics.drop_off_per_page(sessions, page_count=page_count)


def test_page_one_is_every_reader_and_loses_nobody():
    """Every reader opens page 1, so a reach figure there is a counting check."""
    assert curve()["curve"][0] == {
        "page": 1,
        "reached": 4,
        "retained_rate": 1.0,
        "dropped": 0,
        "drop_off_rate": 0.0,
        "is_last_page": False,
    }


def test_reach_is_monotone_because_a_reader_who_reached_a_page_read_the_earlier_ones():
    reached = [entry["reached"] for entry in curve()["curve"]]
    assert reached == [4, 4, 2, 2]
    assert reached == sorted(reached, reverse=True)


def test_a_skipped_page_does_not_read_as_a_collapse():
    """One reader scrolled 1 -> 5. The curve must not show 100% off at 2, 3, 4."""
    sessions = [{"deepest_page": 1}, {"deepest_page": 5}]
    result = metrics.drop_off_per_page(sessions, page_count=5)
    assert [entry["drop_off_rate"] for entry in result["curve"]] == [0.5, 0.0, 0.0, 0.0, 0.0]
    assert [entry["reached"] for entry in result["curve"]] == [2, 1, 1, 1, 1]


def test_the_loss_is_attributed_to_the_page_they_were_reading_not_the_one_they_missed():
    """The reader who stopped on the cover stopped *on the cover*.

    Attributing the loss to page 2 would tell a seller deciding what to cut that
    page 2 is the problem, when page 1 is.
    """
    sessions = [{"deepest_page": 1}, {"deepest_page": 5}]
    result = metrics.drop_off_per_page(sessions, page_count=5)
    assert result["curve"][0]["dropped"] == 1
    assert result["curve"][1]["dropped"] == 0


def test_drop_off_rate_is_the_share_of_those_who_reached_a_page_who_stopped_there():
    assert [entry["drop_off_rate"] for entry in curve()["curve"]] == [0.0, 0.5, 0.0, 0.0]


def test_the_last_page_reports_no_drop_because_there_is_nothing_after_it():
    assert curve()["curve"][-1]["dropped"] == 0


def test_the_drops_account_for_every_reader_who_did_not_finish():
    result = curve()
    assert sum(entry["dropped"] for entry in result["curve"]) == result["sessions"] - result["completed_sessions"]


def test_retained_rate_is_measured_against_the_opening_page():
    assert [entry["retained_rate"] for entry in curve()["curve"]] == [1.0, 1.0, 0.5, 0.5]


def test_the_last_page_is_flagged_so_a_client_does_not_read_it_as_a_next_step():
    assert [entry["is_last_page"] for entry in curve()["curve"]] == [False, False, False, True]


def test_drop_off_reports_the_sessions_and_the_finishers():
    result = curve()
    assert result["sessions"] == 4
    assert result["opening_page_readers"] == 4
    assert result["completed_sessions"] == 2


def test_a_session_past_the_page_count_is_clamped_and_counted():
    """A shortened re-upload must not make the tail look better than it is."""
    result = metrics.drop_off_per_page([{"deepest_page": 9}], page_count=4)
    assert result["sessions_past_page_count"] == 1
    assert [entry["reached"] for entry in result["curve"]] == [1, 1, 1, 1]


def test_drop_off_of_no_sessions_is_all_zero_not_an_error():
    result = metrics.drop_off_per_page([], page_count=3)
    assert result["sessions"] == 0
    assert [entry["retained_rate"] for entry in result["curve"]] == [0.0, 0.0, 0.0]
    assert [entry["drop_off_rate"] for entry in result["curve"]] == [0.0, 0.0, 0.0]


# -- reading sessions ------------------------------------------------------- #


def test_rows_with_one_session_id_are_one_reading():
    sessions = metrics.group_sessions(
        rows({"sessionId": "s1", "page": 1, "seconds": 5, "occurredAt": at(0)})
    )
    assert len(sessions) == 1
    assert sessions[0]["deepest_page"] == 1


def test_one_viewer_without_a_session_id_is_one_reading_while_continuous():
    sessions = metrics.group_sessions(
        [
            {"viewer": "a@x", "page": 1, "seconds": 5, "occurredAt": at(0)},
            {"viewer": "a@x", "page": 2, "seconds": 5, "occurredAt": at(20)},
        ]
    )
    assert len(sessions) == 1
    assert sessions[0]["deepest_page"] == 2


def test_a_long_pause_starts_a_second_reading():
    """Counting a reader who closed the tab for lunch as reading throughout flatters the tail."""
    sessions = metrics.group_sessions(
        [
            {"viewer": "a@x", "page": 1, "seconds": 5, "occurredAt": at(0)},
            {"viewer": "a@x", "page": 2, "seconds": 5, "occurredAt": at(SESSION_GAP_SECONDS + 60)},
        ]
    )
    assert len(sessions) == 2


def test_a_pause_just_under_the_gap_stays_one_reading():
    sessions = metrics.group_sessions(
        [
            {"viewer": "a@x", "page": 1, "seconds": 5, "occurredAt": at(0)},
            {"viewer": "a@x", "page": 2, "seconds": 5, "occurredAt": at(SESSION_GAP_SECONDS - 1)},
        ]
    )
    assert len(sessions) == 1


def test_two_viewers_are_two_readings():
    sessions = metrics.group_sessions(
        [
            {"viewer": "a@x", "page": 1, "seconds": 5, "occurredAt": at(0)},
            {"viewer": "b@x", "page": 1, "seconds": 5, "occurredAt": at(0)},
        ]
    )
    assert len(sessions) == 2


def test_an_anonymous_trackable_link_reader_is_still_counted():
    """The documented entry point is a buyer who has not signed in."""
    sessions = metrics.group_sessions([{"page": 3, "seconds": 9, "occurredAt": at(0)}])
    assert len(sessions) == 1
    assert sessions[0]["deepest_page"] == 3


def test_a_session_reports_its_span_and_its_pages():
    sessions = metrics.group_sessions(
        [
            {"sessionId": "s1", "viewer": "a@x", "page": 3, "seconds": 9, "occurredAt": at(60)},
            {"sessionId": "s1", "viewer": "a@x", "page": 1, "seconds": 4, "occurredAt": at(0)},
            {"sessionId": "s1", "viewer": "a@x", "page": 2, "seconds": 6, "occurredAt": at(30)},
        ]
    )
    assert sessions[0]["pages_visited"] == [1, 2, 3]
    assert sessions[0]["deepest_page"] == 3
    assert sessions[0]["rows"] == 3
    assert sessions[0]["total_seconds"] == 19.0
    assert sessions[0]["startedAt"] == at(0)
    assert sessions[0]["endedAt"] == at(60)


def test_a_row_with_an_unreadable_timestamp_still_groups():
    assert metrics.group_sessions([{"viewer": "a@x", "page": 1, "seconds": 5}])[0]["deepest_page"] == 1


def test_timestamps_parse_from_every_spelling_the_ingest_accepts():
    assert metrics.parse_ts("2026-09-27T12:00:00Z") == NOW
    assert metrics.parse_ts("2026-09-27T12:00:00") == NOW, "a naive stamp is UTC, not the server's zone"
    assert metrics.parse_ts("2026-09-27T14:00:00+02:00") == NOW
    assert metrics.parse_ts(NOW) == NOW
    assert metrics.parse_ts("not a date") is None
    assert metrics.parse_ts("") is None
    assert metrics.parse_ts(None) is None


# -- video ------------------------------------------------------------------ #


def test_average_watch_time_over_no_watches_is_null_not_zero():
    """Zero would read as "nobody watched", which is a different statement."""
    result = metrics.average_watch_time([])
    assert result["watches"] == 0
    assert result["average_seconds"] is None


def test_average_watch_time_is_over_watches():
    result = metrics.average_watch_time(
        rows({"seconds": 100, "viewer": "a@x"}, {"seconds": 200, "viewer": "b@x"}, {"seconds": 300, "viewer": "a@x"})
    )
    assert result["watches"] == 3
    assert result["unique_viewers"] == 2
    assert result["average_seconds"] == 200.0
    assert result["shortest_seconds"] == 100.0
    assert result["longest_seconds"] == 300.0


# -- core analytics --------------------------------------------------------- #


def test_views_and_downloads_count_buyers_only():
    counts = metrics.core_counts(
        [
            {"event": "asset.viewed", "audience": "external", "viewer": "a@x"},
            {"event": "asset.viewed", "audience": "internal", "viewer": "dana"},
            {"event": "asset.downloaded", "audience": "internal", "viewer": "dana"},
        ]
    )
    assert counts["views"] == 1
    assert counts["downloads"] == 0


def test_shares_are_the_one_internal_metric():
    """Sourced, and the whole reason the audience rule is per-metric."""
    counts = metrics.core_counts(
        [
            {"event": "asset.shared", "audience": "internal", "viewer": "dana"},
            {"event": "asset.shared", "audience": "external", "viewer": "a@x"},
        ]
    )
    assert counts["shares"] == 2


def test_unique_viewers_counts_buyers_not_the_team():
    counts = metrics.core_counts(
        [
            {"event": "asset.viewed", "audience": "external", "viewer": "a@x"},
            {"event": "asset.viewed", "audience": "external", "viewer": "a@x"},
            {"event": "asset.viewed", "audience": "internal", "viewer": "dana"},
        ]
    )
    assert counts["unique_viewers"] == 1


def test_an_event_outside_the_vocabulary_moves_no_counter():
    counts = metrics.core_counts([{"event": "asset.deleted", "audience": "external"}])
    assert counts == {"views": 0, "downloads": 0, "shares": 0, "unique_viewers": 0}


def test_the_series_fills_an_empty_bucket_rather_than_skipping_it():
    series = metrics.bucket_series(
        [
            {"event": "asset.viewed", "audience": "external", "occurredAt": "2026-09-01T10:00:00Z"},
            {"event": "asset.viewed", "audience": "external", "occurredAt": "2026-09-04T10:00:00Z"},
        ]
    )
    assert [bucket["bucket"] for bucket in series] == [
        "2026-09-01",
        "2026-09-02",
        "2026-09-03",
        "2026-09-04",
    ]
    assert [bucket["views"] for bucket in series] == [1, 0, 0, 1]


def test_a_weekly_bucket_starts_on_a_monday():
    friday = metrics.parse_ts("2026-09-04T10:00:00Z")
    assert metrics.floor(friday, "week") == "2026-08-31"
    series = metrics.bucket_series(
        [{"event": "asset.viewed", "audience": "external", "occurredAt": "2026-09-04T10:00:00Z"}],
        grain="week",
    )
    assert series[0]["bucket"] == "2026-08-31"


def test_a_monthly_bucket_starts_on_the_first_and_advances_by_calendar():
    series = metrics.bucket_series(
        [
            {"event": "asset.viewed", "audience": "external", "occurredAt": "2026-01-31T10:00:00Z"},
            {"event": "asset.viewed", "audience": "external", "occurredAt": "2026-02-01T10:00:00Z"},
            {"event": "asset.viewed", "audience": "external", "occurredAt": "2026-03-15T10:00:00Z"},
        ],
        grain="month",
    )
    assert [bucket["bucket"] for bucket in series] == ["2026-01-01", "2026-02-01", "2026-03-01"]


def test_a_thirty_day_month_does_not_lose_a_bucket():
    """A fixed 28-day step would silently merge two months into one bar."""
    series = metrics.bucket_series(
        [
            {"event": "asset.viewed", "audience": "external", "occurredAt": "2026-01-01T10:00:00Z"},
            {"event": "asset.viewed", "audience": "external", "occurredAt": "2026-02-28T10:00:00Z"},
            {"event": "asset.viewed", "audience": "external", "occurredAt": "2026-03-01T10:00:00Z"},
        ],
        grain="month",
    )
    assert [bucket["views"] for bucket in series] == [1, 1, 1]


def test_an_event_with_no_parsable_timestamp_leaves_the_series_empty():
    assert metrics.bucket_series([{"event": "asset.viewed", "audience": "external"}]) == []


def test_the_series_refuses_an_unlisted_grain():
    with pytest.raises(ValueError):
        metrics.bucket_series([], grain="fortnight")


# --------------------------------------------------------------------------- #
# The inferences registry
# --------------------------------------------------------------------------- #


def test_there_are_inferences_and_they_are_all_described():
    assert INFERENCES
    for entry in INFERENCES:
        for field in ("id", "topic", "basis", "value", "why", "change_it", "blast_radius"):
            assert entry.get(field), f"{entry.get('id')} is missing {field}"


def test_inference_ids_are_unique():
    ids = [entry["id"] for entry in INFERENCES]
    assert len(ids) == len(set(ids))


def test_the_registry_says_which_half_is_sourced():
    body = describe_inferences()
    assert body["count"] == len(INFERENCES)
    assert body["sourced"]["asset_types"] == list(ASSET_TYPES)
    assert body["sourced"]["webhook_event_types"] == list(WEBHOOK_EVENT_TYPES)
    assert body["sourced"]["min_pages_for_pdf_analytics"] == MIN_PAGES_FOR_PDF_ANALYTICS


def test_the_researched_external_only_rule_is_quoted_verbatim():
    quotes = " ".join(describe_inferences()["sourced_quotes"])
    assert "only show engagement from external users" in quotes
    assert "The one exception is 'Shares'" in quotes


def test_the_absent_endpoint_is_quoted_so_the_ingest_is_honest_about_being_ours():
    entry = next(e for e in INFERENCES if e["id"] == "per-page-ingest-is-ours")
    assert "No documented per-page analytics endpoint" in entry["basis"]
    assert "POST /api/wf-018/assets/{asset_id}/timings" in str(entry["value"]["endpoint"])


def test_no_inference_asks_for_a_migration_or_a_typed_column():
    for entry in INFERENCES:
        blob = str(entry).lower()
        assert "migration" not in blob or "no migration" in blob
        assert "typed column" not in blob or "no migration" in blob


def test_the_session_gap_inference_names_the_constant_that_changes_it():
    entry = next(e for e in INFERENCES if e["id"] == "reading-session-gap")
    assert "SESSION_GAP_SECONDS" in entry["change_it"]
    assert entry["value"]["gap_seconds"] == SESSION_GAP_SECONDS


def test_the_dwell_inference_records_a_decision_not_to_build():
    """A cap would quietly change the researched metric into a different one."""
    entry = next(e for e in INFERENCES if e["id"] == "dwell-has-no-ceiling")
    assert entry["value"]["applies"] == "no cap on seconds"


def test_every_inference_names_a_file_it_can_be_changed_in():
    for entry in INFERENCES:
        assert "backend/dsr/pdf_analytics/" in entry["change_it"]


# --------------------------------------------------------------------------- #
# The book: writes
# --------------------------------------------------------------------------- #


def test_registering_an_asset_stores_the_researched_snapshot(book, store):
    record = deck(book, room_id=None)
    assert record["collection"] == "contentAsset"
    for field in ASSET_SNAPSHOT_FIELDS:
        assert field in record["data"]
    assert store.get(record["id"])["data"]["name"] == "Enterprise Security Overview"


def test_registering_the_same_external_id_updates_rather_than_duplicates(book):
    """Two rows would split one asset's telemetry in half."""
    first = deck(book)
    second = deck(book, name="Enterprise Security Overview v2")
    assert first["id"] == second["id"]
    assert second["data"]["name"] == "Enterprise Security Overview v2"
    assert second["revision"] == 2
    assert len(book.list_assets()) == 1


def test_a_registration_is_audited_with_the_source_the_route_passed(book, store):
    deck(book)
    entry = store.audit(collection="contentAsset", limit=1)[0]
    assert entry["action"] == "insert"
    assert entry["source"] == SOURCE


def test_an_asset_is_scoped_to_a_room(book, room):
    record = book.register_asset({"name": "D", "type": "pdf", "pageCount": 3}, room_id=room["id"], source=SOURCE)
    assert record["room_id"] == room["id"]
    assert [r["id"] for r in book.list_assets(room_id=room["id"])] == [record["id"]]
    assert book.list_assets(room_id="room_missing") == []


def test_registering_into_a_room_that_does_not_exist_is_refused(book):
    with pytest.raises(NotFound):
        book.register_asset({"name": "D", "type": "pdf", "pageCount": 3}, room_id="room_nope", source=SOURCE)


def test_the_availability_flags_are_derived_at_registration(book):
    multi = book.register_asset({"name": "A", "type": "pdf", "pageCount": 4}, source=SOURCE)
    single = book.register_asset({"name": "B", "type": "pdf", "pageCount": 1}, source=SOURCE)
    video = book.register_asset({"name": "C", "type": "video"}, source=SOURCE)
    assert multi["data"]["pdfAnalyticsAvailable"] is True
    assert multi["data"]["videoAnalyticsAvailable"] is False
    assert single["data"]["pdfAnalyticsAvailable"] is False
    assert video["data"]["videoAnalyticsAvailable"] is True


def test_an_asset_resolves_by_record_id_and_by_the_sources_own_id(book):
    record = deck(book)
    assert book.resolve_asset(record["id"])["id"] == record["id"]
    assert book.resolve_asset("asset-deck")["id"] == record["id"]
    assert book.resolve_asset("nope") is None
    assert book.resolve_asset("") is None


def test_a_record_that_is_not_an_asset_does_not_resolve_as_one(book, store):
    room = store.create("room", {"name": "R"}, actor="dana")
    assert book.resolve_asset(room["id"]) is None


# -- events ----------------------------------------------------------------- #


def test_a_viewed_event_is_recorded(book, store):
    asset = deck(book)
    event = book.record_event(
        asset["id"],
        {"event": "asset.viewed", "viewer": "a@x", "occurred_at": at()},
        source=f"POST {PREFIX}/assets/{asset['id']}/events",
    )
    assert event["data"]["event"] == "asset.viewed"
    assert event["data"]["audience"] == "external"
    assert event["data"]["assetId"] == asset["id"]


def test_an_event_outside_the_researched_vocabulary_is_refused(book):
    asset = deck(book)
    with pytest.raises(UnknownEventType):
        book.record_event(asset["id"], {"event": "asset.exploded"}, source="seed")


def test_an_event_registers_an_unknown_asset_from_its_embedded_snapshot(book):
    """The payload embeds the snapshot so the receiver need not make a second call."""
    event = book.record_event(
        "asset_from_webhook",
        {
            "event": "asset.viewed",
            "viewer": "a@x",
            "asset": {
                "name": "Webhook Deck",
                "type": "pdf",
                "pageCount": 9,
                "externalId": "asset_from_webhook",
                "shareUrl": "https://s.example/a",
                "tags": ["webhook"],
                "downloadEnabled": True,
                "trackingEnabled": True,
            },
        },
        source="seed",
    )
    registered = book.require_asset("asset_from_webhook")
    assert registered["data"]["name"] == "Webhook Deck"
    assert event["data"]["assetId"] == registered["id"]


def test_an_event_leaves_a_known_asset_alone(book):
    """A webhook must not be able to rewrite an asset's name."""
    asset = deck(book)
    book.record_event(
        asset["id"],
        {"event": "asset.viewed", "viewer": "a@x", "asset": {"name": "Hijacked", "type": "pdf"}},
        source="seed",
    )
    assert book.require_asset(asset["id"])["data"]["name"] == "Enterprise Security Overview"


def test_the_embedded_snapshot_is_stored_verbatim_for_the_join(book):
    """Sourced extensibility: a third party joins its telemetry to these rows."""
    asset = deck(book)
    snapshot = {"name": "Deck", "type": "pdf", "someFutureField": {"a": 1}}
    event = book.record_event(
        asset["id"], {"event": "asset.viewed", "asset": snapshot}, source="seed"
    )
    assert event["data"]["assetSnapshot"] == snapshot


def test_an_event_for_an_unknown_asset_with_no_snapshot_is_a_404(book):
    with pytest.raises(NotFound):
        book.record_event("asset_nope", {"event": "asset.viewed"}, source="seed")


def test_an_internal_event_is_marked_internal(book):
    asset = deck(book)
    event = book.record_event(
        asset["id"], {"event": "asset.shared", "viewer": "dana", "isInternal": True}, source="seed"
    )
    assert event["data"]["audience"] == "internal"


# -- timings ---------------------------------------------------------------- #


def test_a_batch_of_page_timings_is_stored(book, store):
    asset = deck(book)
    result = read(book, asset["id"], "a@x", 3)
    assert result["count"] == 3
    rows = store.find(TIMING_COLLECTION, {"assetId": asset["id"]}, limit=10)
    assert sorted(row["data"]["page"] for row in rows) == [1, 2, 3]


def test_a_batch_is_one_transaction_and_one_audit_row(book, store):
    """Half a session is not a session, so it is not a separately auditable thing."""
    asset = deck(book)
    read(book, asset["id"], "a@x", 8)
    entries = store.audit(collection=TIMING_COLLECTION, limit=10)
    assert len(entries) == 1
    assert entries[0]["action"] == "insert"
    assert entries[0]["source"] == f"POST {PREFIX}/assets/{asset['id']}/timings"


def test_the_envelope_is_per_request_and_the_rows_are_pages(book):
    asset = deck(book)
    result = read(book, asset["id"], "a@x", 2, session="sess-9", room_id=None)
    assert result["viewer"] == "a@x"
    assert result["session_id"] == "sess-9"
    assert result["audience"] == "external"
    assert [entry["page"] for entry in result["timings"]] == [1, 2]


def test_timings_for_a_video_are_refused(book):
    video = book.register_asset({"name": "V", "type": "video"}, source="seed")
    with pytest.raises(ValidationError) as caught:
        book.record_timings(video["id"], {"timings": [{"page": 1, "seconds": 5}]}, source="seed")
    assert caught.value.context["asset_type"] == "video"


def test_timings_for_an_untracked_asset_are_refused_not_dropped(book, store):
    """A dropped row is a hole in the curve indistinguishable from a skipped page."""
    asset = deck(book, trackingEnabled=False)
    with pytest.raises(TrackingDisabled) as caught:
        read(book, asset["id"], "a@x", 3)
    assert caught.value.status_code == 422
    assert store.find(TIMING_COLLECTION, {"assetId": asset["id"]}, limit=10) == []


def test_an_empty_batch_is_refused(book):
    asset = deck(book)
    for body in ({}, {"timings": []}, {"timings": "page 1"}, {"timings": 5}):
        with pytest.raises(ValidationError):
            book.record_timings(asset["id"], body, source="seed")


def test_a_row_must_be_an_object(book):
    asset = deck(book)
    with pytest.raises(ValidationError) as caught:
        book.record_timings(asset["id"], {"timings": ["page 1"]}, source="seed")
    assert caught.value.context["field"] == "timings[0]"


def test_a_row_page_must_be_a_positive_whole_number(book):
    asset = deck(book)
    for bad in (0, -1, "two", None):
        with pytest.raises(ValidationError):
            book.record_timings(asset["id"], {"timings": [{"page": bad, "seconds": 5}]}, source="seed")


def test_a_row_must_spend_a_positive_number_of_seconds(book):
    asset = deck(book)
    for bad in (0, -5, None, "ages"):
        with pytest.raises(ValidationError):
            book.record_timings(asset["id"], {"timings": [{"page": 1, "seconds": bad}]}, source="seed")


def test_a_refused_batch_writes_nothing_at_all(book, store):
    asset = deck(book)
    before = store.stats()["records"]
    with pytest.raises(ValidationError):
        book.record_timings(
            asset["id"], {"timings": [{"page": 1, "seconds": 5}, {"page": 0, "seconds": 5}]}, source="seed"
        )
    assert store.stats()["records"] == before


def test_timings_are_scoped_to_one_room(book, room, other_room):
    asset = book.register_asset(
        {"name": "D", "type": "pdf", "pageCount": 4}, room_id=room["id"], source=SOURCE
    )
    read(book, asset["id"], "a@x", 4, room_id=room["id"], session="in-room")
    read(book, asset["id"], "b@x", 2, room_id=other_room["id"], session="other-room")
    in_room = book.pdf_analytics(asset["id"], room_id=room["id"])
    assert [entry["reached"] for entry in in_room["drop_off_per_page"]] == [1, 1, 1, 1]
    other = book.pdf_analytics(asset["id"], room_id=other_room["id"])
    assert [entry["reached"] for entry in other["drop_off_per_page"]] == [1, 1, 0, 0]


def test_timings_for_an_unknown_asset_are_a_404(book):
    with pytest.raises(NotFound):
        book.record_timings("nope", {"timings": [{"page": 1, "seconds": 5}]}, source="seed")


# -- watches ---------------------------------------------------------------- #


def test_a_watch_is_recorded_and_averaged(book):
    video = book.register_asset({"name": "V", "type": "video"}, source="seed")
    for seconds in (100, 200):
        book.record_watch(video["id"], {"viewer": "a@x", "seconds": seconds}, source="seed")
    result = book.video_analytics(video["id"])
    assert result["watches"] == 2
    assert result["average_seconds"] == 150.0


def test_a_watch_for_a_pdf_is_refused(book):
    asset = deck(book)
    with pytest.raises(ValidationError):
        book.record_watch(asset["id"], {"seconds": 30}, source="seed")


def test_a_watch_for_an_externally_hosted_video_is_refused(book):
    """No such viewer exists, so a number here would be one none of ours produced."""
    video = book.register_asset({"name": "V", "type": "video", "selfHosted": False}, source="seed")
    with pytest.raises(VideoAnalyticsUnavailable) as caught:
        book.record_watch(video["id"], {"seconds": 30}, source="seed")
    assert caught.value.context["reason"] == "not_self_hosted"


def test_a_watch_must_spend_a_positive_number_of_seconds(book):
    video = book.register_asset({"name": "V", "type": "video"}, source="seed")
    with pytest.raises(ValidationError):
        book.record_watch(video["id"], {"seconds": 0}, source="seed")


# --------------------------------------------------------------------------- #
# The book: the three researched reads
# --------------------------------------------------------------------------- #


def test_pdf_analytics_answers_the_two_named_metrics(book):
    asset = deck(book)
    read(book, asset["id"], "a@x", 12)
    read(book, asset["id"], "b@x", 4)
    result = book.pdf_analytics(asset["id"])
    assert result["page_count"] == 12
    assert len(result["time_spent_per_page"]) == 12
    assert len(result["drop_off_per_page"]) == 12
    assert result["audience"] == "external"
    assert result["readings"] == 16


def test_pdf_analytics_on_a_single_page_pdf_is_refused_with_a_reason(book):
    single = book.register_asset({"name": "One", "type": "pdf", "pageCount": 1}, source=SOURCE)
    with pytest.raises(PdfAnalyticsUnavailable) as caught:
        book.pdf_analytics(single["id"])
    assert caught.value.status_code == 422
    assert caught.value.context["reason"] == "single_page"


def test_pdf_analytics_on_a_video_is_refused_with_a_reason(book):
    video = book.register_asset({"name": "V", "type": "video"}, source=SOURCE)
    with pytest.raises(PdfAnalyticsUnavailable) as caught:
        book.pdf_analytics(video["id"])
    assert caught.value.context["reason"] == "unknown_asset_type"


def test_video_analytics_on_a_pdf_is_refused_with_a_reason(book):
    asset = deck(book)
    with pytest.raises(VideoAnalyticsUnavailable) as caught:
        book.video_analytics(asset["id"])
    assert caught.value.context["reason"] == "unknown_asset_type"


def test_video_analytics_on_an_externally_hosted_video_is_refused(book):
    video = book.register_asset({"name": "V", "type": "video", "selfHosted": False}, source=SOURCE)
    with pytest.raises(VideoAnalyticsUnavailable) as caught:
        book.video_analytics(video["id"])
    assert caught.value.context["reason"] == "not_self_hosted"


def test_the_internal_team_reading_its_own_deck_does_not_move_the_curve(book):
    asset = deck(book)
    read(book, asset["id"], "a@x", 6, session="buyer")
    before = book.pdf_analytics(asset["id"])
    read(book, asset["id"], "dana", 12, session="team", internal=True)
    after = book.pdf_analytics(asset["id"])
    assert after["drop_off"]["sessions"] == before["drop_off"]["sessions"]
    assert after["readings"] == before["readings"]


def test_the_curve_starts_at_every_reader(book):
    asset = deck(book)
    for index in range(5):
        read(book, asset["id"], f"r{index}@x", 3, session=f"s{index}")
    result = book.pdf_analytics(asset["id"])
    assert result["drop_off_per_page"][0]["retained_rate"] == 1.0
    assert result["drop_off"]["opening_page_readers"] == 5


def test_pdf_analytics_names_the_pages_nobody_read(book):
    asset = deck(book, pageCount=10)
    read(book, asset["id"], "a@x", 3)
    result = book.pdf_analytics(asset["id"])
    assert result["pages_never_read"] == [4, 5, 6, 7, 8, 9, 10]


def test_pdf_analytics_on_an_asset_with_no_readings_is_empty_not_an_error(book):
    asset = deck(book)
    result = book.pdf_analytics(asset["id"])
    assert result["readings"] == 0
    assert result["drop_off"]["sessions"] == 0
    assert result["time_spent_per_page"][0]["average_seconds"] is None


def test_pdf_analytics_reports_the_gap_it_used_to_group_readers(book):
    """A reader who has to guess why two sittings became one curve cannot argue with it."""
    asset = deck(book)
    assert book.pdf_analytics(asset["id"])["session_gap_seconds"] == SESSION_GAP_SECONDS


def test_core_analytics_keeps_shares_and_drops_internal_views(book):
    asset = deck(book)
    for event, viewer, internal in (
        ("asset.viewed", "a@x", False),
        ("asset.viewed", "dana", True),
        ("asset.downloaded", "a@x", False),
        ("asset.shared", "dana", True),
    ):
        book.record_event(
            asset["id"],
            {"event": event, "viewer": viewer, "isInternal": internal, "occurred_at": at()},
            source="seed",
        )
    counts = book.core_analytics(asset["id"])["counts"]
    assert counts == {"views": 1, "downloads": 1, "shares": 1, "unique_viewers": 1}


def test_core_analytics_honours_the_grain(book):
    asset = deck(book)
    book.record_event(
        asset["id"], {"event": "asset.viewed", "occurred_at": "2026-01-31T23:00:00Z"}, source="seed"
    )
    book.record_event(
        asset["id"], {"event": "asset.viewed", "occurred_at": "2026-02-01T00:30:00Z"}, source="seed"
    )
    daily = book.core_analytics(asset["id"], grain="day")
    monthly = book.core_analytics(asset["id"], grain="month")
    assert [bucket["views"] for bucket in daily["series"]] == [1, 1]
    assert [bucket["views"] for bucket in monthly["series"]] == [1, 1]
    assert [bucket["bucket"] for bucket in monthly["series"]] == ["2026-01-01", "2026-02-01"]


def test_core_analytics_refuses_an_unlisted_grain(book):
    asset = deck(book)
    with pytest.raises(ValidationError):
        book.core_analytics(asset["id"], grain="fortnight")


def test_core_analytics_for_an_unknown_asset_is_a_404(book):
    with pytest.raises(NotFound):
        book.core_analytics("nope")


# -- the asset detail page -------------------------------------------------- #


def test_the_detail_page_reports_an_inapplicable_block_instead_of_raising(book):
    """On the detail page "not applicable" is information, not a wrong question."""
    single = book.register_asset({"name": "One", "type": "pdf", "pageCount": 1}, source=SOURCE)
    detail = book.asset_detail(single["id"])
    assert detail["advanced_analytics"]["pdf"] == {
        "available": False,
        "reason": "single_page",
        "block": "PDF Analytics",
    }
    assert detail["advanced_analytics"]["video"]["reason"] == "unknown_asset_type"
    assert detail["advanced_analytics"]["core"]["counts"]["views"] == 0


def test_the_detail_page_carries_the_whole_advanced_analytics_block(book):
    asset = deck(book)
    read(book, asset["id"], "a@x", 12)
    detail = book.asset_detail(asset["id"])
    panels = detail["advanced_analytics"]
    assert panels["pdf"]["available"] is True
    assert panels["pdf"]["page_count"] == 12
    assert panels["video"]["available"] is False, "a PDF has no watch time"
    assert panels["core"]["grain"] == "day"
    assert detail["asset"]["name"] == "Enterprise Security Overview"
    assert detail["id"] == asset["id"]


def test_the_detail_page_of_a_video_carries_the_watch_time(book):
    video = book.register_asset({"name": "V", "type": "video"}, source=SOURCE)
    book.record_watch(video["id"], {"viewer": "a@x", "seconds": 90}, source="seed")
    panels = book.asset_detail(video["id"])["advanced_analytics"]
    assert panels["video"]["available"] is True
    assert panels["video"]["average_seconds"] == 90.0


# -- the room view ---------------------------------------------------------- #


def test_a_room_lists_its_own_assets_with_their_headline_numbers(book, room, other_room):
    mine = book.register_asset({"name": "Mine", "type": "pdf", "pageCount": 4}, room_id=room["id"], source=SOURCE)
    book.register_asset({"name": "Theirs", "type": "pdf", "pageCount": 4}, room_id=other_room["id"], source=SOURCE)
    read(book, mine["id"], "a@x", 4, room_id=room["id"])
    book.record_event(
        mine["id"], {"event": "asset.viewed", "viewer": "a@x", "room_id": room["id"]}, source="seed"
    )
    result = book.room_assets(room["id"])
    assert result["room_id"] == room["id"]
    assert result["count"] == 1
    entry = result["assets"][0]
    assert entry["name"] == "Mine"
    assert entry["counts"]["views"] == 1
    assert entry["pdf"]["readings"] == 4
    assert entry["pdf"]["completed_sessions"] == 1
    assert entry["pdf"]["last_page_reached"] == 4
    assert entry["pdf"]["longest_average_page"] == 4
    assert entry["pdf"]["longest_average_seconds"] == 40.0


def test_a_room_view_counts_only_that_rooms_readers(book, room, other_room):
    asset = book.register_asset({"name": "Shared", "type": "pdf", "pageCount": 4}, room_id=room["id"], source=SOURCE)
    read(book, asset["id"], "a@x", 4, room_id=room["id"], session="mine")
    read(book, asset["id"], "b@x", 4, room_id=other_room["id"], session="theirs")
    result = book.room_assets(room["id"])
    assert result["assets"][0]["pdf"]["sessions"] == 1


def test_a_room_view_summarises_a_video_too(book, room):
    video = book.register_asset({"name": "V", "type": "video"}, room_id=room["id"], source=SOURCE)
    book.record_watch(video["id"], {"viewer": "a@x", "seconds": 60, "room_id": room["id"]}, source="seed")
    entry = book.room_assets(room["id"])["assets"][0]
    assert entry["video"]["average_seconds"] == 60.0
    assert entry["videoAnalyticsAvailable"] is True


def test_an_unknown_room_is_a_404(book):
    with pytest.raises(NotFound):
        book.room_assets("room_nope")


# --------------------------------------------------------------------------- #
# The HTTP surface
# --------------------------------------------------------------------------- #


def register(http, **overrides):
    payload = {
        "name": "Enterprise Security Overview",
        "type": "pdf",
        "pageCount": 12,
        "externalId": "asset-deck",
    }
    payload.update(overrides)
    return http.post(f"{PREFIX}/assets", json=payload).json()


def test_vocabulary_is_served_over_http(http):
    body = http.get(f"{PREFIX}/vocabulary").json()
    assert body["asset_types"] == ["pdf", "video"]
    assert body["webhook_event_types"] == list(WEBHOOK_EVENT_TYPES)
    assert body["internal_only_metrics"] == ["shares"]
    assert body["min_pages_for_pdf_analytics"] == 2


def test_inferences_are_served_over_http(http):
    body = http.get(f"{PREFIX}/inferences").json()
    assert body["count"] == len(INFERENCES)
    assert any(entry["id"] == "per-page-ingest-is-ours" for entry in body["inferences"])


def test_the_library_list_is_served_over_http(http):
    register(http)
    body = http.get(f"{PREFIX}/assets").json()
    assert body["count"] == 1
    assert body["assets"][0]["pdfAnalyticsAvailable"] is True


def test_the_library_list_filters_by_type(http):
    register(http)
    http.post(f"{PREFIX}/assets", json={"name": "V", "type": "video"})
    assert http.get(f"{PREFIX}/assets", params={"type": "video"}).json()["count"] == 1
    assert http.get(f"{PREFIX}/assets", params={"type": "pdf"}).json()["count"] == 1


def test_the_library_list_refuses_an_unknown_type(http):
    assert http.get(f"{PREFIX}/assets", params={"type": "spreadsheet"}).status_code == 400


def test_the_library_list_searches_by_name(http):
    register(http)
    http.post(f"{PREFIX}/assets", json={"name": "Analyst Day Keynote", "type": "video"})
    assert http.get(f"{PREFIX}/assets", params={"q": "analyst"}).json()["count"] == 1
    assert http.get(f"{PREFIX}/assets", params={"q": "zzz"}).json()["count"] == 0


def test_the_library_list_rejects_a_limit_outside_its_bounds(http):
    assert http.get(f"{PREFIX}/assets", params={"limit": 0}).status_code == 422
    assert http.get(f"{PREFIX}/assets", params={"limit": 1001}).status_code == 422


def test_registering_an_asset_returns_201_and_is_audited(http):
    response = http.post(f"{PREFIX}/assets", json={"name": "D", "type": "pdf", "pageCount": 4})
    assert response.status_code == 201
    entry = http.get("/api/audit", params={"collection": "contentAsset", "limit": 1}).json()["entries"][0]
    assert entry["source"] == f"POST {PREFIX}/assets"


def test_registering_a_snapshot_without_a_name_is_a_400(http):
    response = http.post(f"{PREFIX}/assets", json={"type": "pdf"})
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_request"
    assert response.json()["field" if "field" in response.json() else "detail"] == "name"


def test_recording_a_viewed_event_over_http(http):
    asset = register(http)
    response = http.post(
        f"{PREFIX}/assets/{asset['id']}/events",
        json={"event": "asset.viewed", "viewer": "a@x", "occurred_at": at()},
    )
    assert response.status_code == 201
    assert response.json()["data"]["event"] == "asset.viewed"
    assert http.get(f"{PREFIX}/assets/{asset['id']}/core-analytics").json()["counts"]["views"] == 1


def test_recording_an_unknown_event_type_over_http_is_a_400(http):
    asset = register(http)
    response = http.post(f"{PREFIX}/assets/{asset['id']}/events", json={"event": "asset.exploded"})
    assert response.status_code == 400
    assert response.json()["error"] == "unknown_event_type"
    assert response.json()["allowed"] == list(WEBHOOK_EVENT_TYPES)


def test_recording_timings_over_http_then_reading_the_curve(http):
    asset = register(http)
    timings = [{"page": page, "seconds": 10.0 * page} for page in range(1, 13)]
    posted = http.post(
        f"{PREFIX}/assets/{asset['id']}/timings",
        json={"viewer": "a@x", "session_id": "s1", "timings": timings},
    )
    assert posted.status_code == 201
    assert posted.json()["count"] == 12

    pdf = http.get(f"{PREFIX}/assets/{asset['id']}/pdf-analytics").json()
    assert pdf["page_count"] == 12
    assert pdf["drop_off_per_page"][0]["retained_rate"] == 1.0
    assert pdf["drop_off_per_page"][11]["reached"] == 1


def test_recording_timings_for_an_untracked_asset_over_http_is_a_422(http):
    asset = register(http, trackingEnabled=False)
    response = http.post(
        f"{PREFIX}/assets/{asset['id']}/timings", json={"timings": [{"page": 1, "seconds": 5}]}
    )
    assert response.status_code == 422
    assert response.json()["error"] == "tracking_disabled"


def test_recording_timings_for_a_single_page_pdf_still_works(http):
    """A one-pager is read; it simply has no per-page curve to show."""
    asset = register(http, name="Terms", pageCount=1)
    response = http.post(
        f"{PREFIX}/assets/{asset['id']}/timings", json={"timings": [{"page": 1, "seconds": 20}]}
    )
    assert response.status_code == 201
    assert http.get(f"{PREFIX}/assets/{asset['id']}/pdf-analytics").status_code == 422


def test_asking_for_pdf_analytics_on_a_single_page_pdf_is_a_422_with_a_reason(http):
    asset = register(http, name="Terms", pageCount=1)
    response = http.get(f"{PREFIX}/assets/{asset['id']}/pdf-analytics")
    assert response.status_code == 422
    body = response.json()
    assert body["error"] == "pdf_analytics_unavailable"
    assert body["reason"] == "single_page"


def test_asking_for_video_analytics_on_an_externally_hosted_video_is_a_422(http):
    asset = http.post(f"{PREFIX}/assets", json={"name": "Keynote", "type": "video", "selfHosted": False}).json()
    response = http.get(f"{PREFIX}/assets/{asset['id']}/video-analytics")
    assert response.status_code == 422
    assert response.json()["reason"] == "not_self_hosted"


def test_recording_a_watch_over_http_then_reading_the_average(http):
    asset = http.post(f"{PREFIX}/assets", json={"name": "Walkthrough", "type": "video"}).json()
    assert http.post(f"{PREFIX}/assets/{asset['id']}/watch", json={"seconds": 60}).status_code == 201
    assert http.post(f"{PREFIX}/assets/{asset['id']}/watch", json={"seconds": 120}).status_code == 201
    body = http.get(f"{PREFIX}/assets/{asset['id']}/video-analytics").json()
    assert body["average_seconds"] == 90.0
    assert body["watches"] == 2


def test_the_detail_route_answers_the_whole_page(http):
    asset = register(http)
    http.post(
        f"{PREFIX}/assets/{asset['id']}/timings",
        json={"timings": [{"page": 1, "seconds": 30}, {"page": 2, "seconds": 20}]},
    )
    body = http.get(f"{PREFIX}/assets/{asset['id']}").json()
    assert body["asset"]["name"] == "Enterprise Security Overview"
    assert body["advanced_analytics"]["pdf"]["available"] is True
    assert body["advanced_analytics"]["core"]["counts"] == {
        "views": 0,
        "downloads": 0,
        "shares": 0,
        "unique_viewers": 0,
    }


def test_the_room_route_needs_a_real_room(http, http_room):
    asset = http.post(
        f"{PREFIX}/assets", params={"room_id": http_room["id"]}, json={"name": "D", "type": "pdf", "pageCount": 3}
    ).json()
    http.post(
        f"{PREFIX}/assets/{asset['id']}/timings",
        params={"room_id": http_room["id"]},
        json={"timings": [{"page": 1, "seconds": 12}]},
    )
    body = http.get(f"{PREFIX}/rooms/{http_room['id']}/assets").json()
    assert body["count"] == 1
    assert body["assets"][0]["pdf"]["readings"] == 1


def test_the_room_route_for_an_unknown_room_is_a_404(http):
    response = http.get(f"{PREFIX}/rooms/room_nope/assets")
    assert response.status_code == 404
    assert response.json()["error"] == "not_found"


def test_every_asset_route_for_an_unknown_asset_is_a_404(http):
    for path in ("", "/pdf-analytics", "/video-analytics", "/core-analytics"):
        assert http.get(f"{PREFIX}/assets/asset_nope{path}").status_code == 404
    assert http.post(
        f"{PREFIX}/assets/asset_nope/timings", json={"timings": [{"page": 1, "seconds": 5}]}
    ).status_code == 404
    assert http.post(f"{PREFIX}/assets/asset_nope/watch", json={"seconds": 5}).status_code == 404
    assert http.post(
        f"{PREFIX}/assets/asset_nope/events", json={"event": "asset.viewed"}
    ).status_code == 404


def test_a_malformed_body_is_refused_before_the_reference_is_resolved(http):
    """Cheap validation first: a caller with two mistakes is told about both, not
    sent on a 404 for the one that happened to be checked second."""
    response = http.post(f"{PREFIX}/assets/asset_nope/events", json={})
    assert response.status_code == 400
    assert response.json()["error"] == "unknown_event_type"


def test_the_analytics_routes_reject_an_unlisted_grain(http):
    asset = register(http)
    for path in ("", "/core-analytics"):
        response = http.get(f"{PREFIX}/assets/{asset['id']}{path}", params={"grain": "fortnight"})
        assert response.status_code == 400
        assert response.json()["error"] == "invalid_request"


def test_the_room_route_checks_the_room_before_the_grain(http):
    """A 404 for a room that does not exist is more useful than a 400 about a filter."""
    response = http.get(f"{PREFIX}/rooms/room_x/assets", params={"grain": "fortnight"})
    assert response.status_code == 404


def test_a_room_scope_on_the_analytics_routes_narrows_the_numbers(http, http_room):
    http.post("/api/records/room", json={"name": "Contoso"}, params={"actor": "dana"})
    other = http.get("/api/records/room", params={"limit": 50}).json()["records"]
    other_id = next(record["id"] for record in other if record["data"]["name"] == "Contoso")
    asset = register(http)

    http.post(
        f"{PREFIX}/assets/{asset['id']}/timings",
        params={"room_id": http_room["id"]},
        json={"timings": [{"page": 1, "seconds": 20}, {"page": 2, "seconds": 10}]},
    )
    http.post(
        f"{PREFIX}/assets/{asset['id']}/timings",
        params={"room_id": other_id},
        json={"timings": [{"page": 1, "seconds": 20}]},
    )
    assert http.get(f"{PREFIX}/assets/{asset['id']}/pdf-analytics").json()["readings"] == 3
    scoped = http.get(f"{PREFIX}/assets/{asset['id']}/pdf-analytics", params={"room_id": other_id}).json()
    assert scoped["readings"] == 1
    assert scoped["room_id"] == other_id


# --------------------------------------------------------------------------- #
# The audit-source rule
# --------------------------------------------------------------------------- #


def _matches_registered_route(path, served):
    actual = [segment for segment in path.split("/") if segment]
    for route in served:
        template = [segment for segment in route["path"].split("/") if segment]
        if len(template) != len(actual):
            continue
        if all(
            expected.startswith("{") or expected == found
            for expected, found in zip(template, actual)
        ):
            return True
    return False


def test_every_write_audit_row_names_a_route_the_app_serves(http):
    """The port's central guarantee, checked against the live route table.

    The same class of bug - an audit row naming a path the app had stopped
    serving - has shipped in this codebase before, so it is checked rather than
    assumed.
    """
    asset = register(http)
    http.post(f"{PREFIX}/assets/{asset['id']}/events", json={"event": "asset.viewed", "viewer": "a@x"})
    http.post(
        f"{PREFIX}/assets/{asset['id']}/timings",
        json={"timings": [{"page": 1, "seconds": 30}]},
    )
    video = http.post(f"{PREFIX}/assets", json={"name": "V", "type": "video"}).json()
    http.post(f"{PREFIX}/assets/{video['id']}/watch", json={"seconds": 30})
    register(http, name="Second Deck", externalId="asset-second")

    served = [
        route
        for feature in http.get("/api/features").json()["features"]
        for route in feature["routes"]
    ]
    entries = http.get("/api/audit", params={"limit": 200}).json()["entries"]
    sources = {entry["source"] for entry in entries if entry["source"]}

    # The core records API and the seeder write with their own sources; only the
    # rows this feature's HTTP layer produced are in scope here.
    ours = {source for source in sources if source.split(" ")[1].startswith(PREFIX)}
    assert ours, f"no wf-018 write was audited at all; saw {sorted(sources)}"
    for source in sorted(ours):
        assert _matches_registered_route(source.split(" ", 1)[1], served), (
            f"audit row names {source!r}, which is not a route this app serves"
        )


def test_a_write_source_names_the_concrete_request_not_a_placeholder(http):
    """``/assets/{asset_id}/timings`` would be a template, not a route we served."""
    asset = register(http)
    http.post(f"{PREFIX}/assets/{asset['id']}/timings", json={"timings": [{"page": 1, "seconds": 5}]})
    entries = http.get("/api/audit", params={"collection": "pageTiming"}).json()["entries"]
    assert entries[0]["source"] == f"POST {PREFIX}/assets/{asset['id']}/timings"


def test_no_write_source_names_a_path_outside_the_prefix(http):
    asset = register(http)
    http.post(f"{PREFIX}/assets/{asset['id']}/events", json={"event": "asset.viewed"})
    entries = http.get("/api/audit", params={"limit": 200}).json()["entries"]
    for entry in entries:
        source = entry["source"] or ""
        if source == "seed":
            continue
        assert source.split(" ")[1].startswith("/api/") or source.startswith("POST /api/records"), source


def test_reading_the_analytics_writes_nothing(http):
    """Sourced: analytics are computed continuously, the action on them is manual.

    There is no rollup row to keep in step, so a read that wrote would be a bug
    the audit log would surface on every page load.
    """
    asset = register(http)
    http.post(
        f"{PREFIX}/assets/{asset['id']}/timings",
        json={"timings": [{"page": 1, "seconds": 30}]},
    )
    before = http.get("/api/stats").json()["audit_entries"]
    http.get(f"{PREFIX}/assets")
    http.get(f"{PREFIX}/assets/{asset['id']}")
    http.get(f"{PREFIX}/assets/{asset['id']}/pdf-analytics")
    http.get(f"{PREFIX}/assets/{asset['id']}/core-analytics")
    http.get(f"{PREFIX}/vocabulary")
    http.get(f"{PREFIX}/inferences")
    assert http.get("/api/stats").json()["audit_entries"] == before


def test_a_refused_write_leaves_no_audit_row_behind(http):
    asset = register(http, trackingEnabled=False)
    before = http.get("/api/stats").json()["audit_entries"]
    response = http.post(
        f"{PREFIX}/assets/{asset['id']}/timings", json={"timings": [{"page": 1, "seconds": 5}]}
    )
    assert response.status_code == 422
    assert http.get("/api/stats").json()["audit_entries"] == before


def test_the_audit_row_and_the_change_land_in_one_transaction(book, store):
    """The product guarantee, asserted on this feature's own write."""
    asset = deck(book)
    before = store.stats()["records"]
    read(book, asset["id"], "a@x", 4)
    entries = store.audit(collection=TIMING_COLLECTION, limit=10)
    assert len(entries) == 1
    assert entries[0]["after_state"] is not None
    assert store.stats()["records"] == before + 4


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #


@pytest.fixture()
def seeded(store):
    from dsr.features import load_feature as _load

    module = _load(MODULE)
    rooms = [
        store.create("room", {"name": "Northwind"}, actor="dana"),
        store.create("room", {"name": "Contoso"}, actor="dana"),
    ]
    summary = module.seed(
        store.db, {"room_ids": [(r["id"], r["data"]["name"]) for r in rooms], "now": NOW, "rng": None}
    )
    return module, summary, [r["id"] for r in rooms]


def test_the_seed_reports_what_it_added(seeded):
    _, summary, _ = seeded
    assert "content assets" in summary
    assert "page timings" in summary
    assert "watches" in summary
    assert "asset events" in summary


def test_the_seed_produces_a_readable_curve_not_random_noise(seeded, store):
    module, _, _ = seeded
    book = AnalyticsBook(store)
    deck_id = book.resolve_asset("asset_demo_02")["id"]  # Platform Roadmap FY27
    result = book.pdf_analytics(deck_id)
    # 5 readers, one of whom stops at page 1 and two of whom stop at page 4.
    assert result["drop_off"]["sessions"] == 5
    assert result["drop_off_per_page"][4]["drop_off_rate"] == 0.5, "the cliff is visible"


def test_the_seed_makes_the_readers_own_previews_invisible(seeded, store):
    book = AnalyticsBook(store)
    deck_id = book.resolve_asset("asset_demo_02")["id"]
    internal = store.find("pageTiming", {"assetId": deck_id, "audience": "internal"}, limit=10)
    assert internal == []


def test_the_seed_shows_the_session_gap_actually_splitting_a_reading(seeded, store):
    """One human who stopped for lunch is two readings, and the demo proves it."""
    book = AnalyticsBook(store)
    deck_id = book.resolve_asset("asset_demo_01")["id"]  # the 24-page security deck
    assert book.pdf_analytics(deck_id)["drop_off"]["sessions"] == 6


def test_the_seed_contains_every_state_a_reviewer_needs_to_see(seeded, store):
    module, _, _ = seeded
    book = AnalyticsBook(store)
    assets = {record["data"]["name"]: record["data"] for record in book.list_assets()}

    assert assets["Commercial Terms One-Pager"]["pageCount"] == 1, "the single-page refusal has a home"
    assert assets["M&A Diligence Data Room Index"]["trackingEnabled"] is False, "the refusal has a home"
    assert assets["Analyst Day Keynote (external player)"]["selfHosted"] is False, "the refusal has a home"
    assert assets["Partner Referral Terms (internal)"]["isInternal"] is True

    internal_id = book.resolve_asset("asset_demo_05")["id"]
    counts = book.core_analytics(internal_id)["counts"]
    assert counts["views"] == 0, "the team previewed it and buyers still did not"
    assert counts["shares"] == 4, "the researched exception is visible"


def test_the_seed_spreads_its_assets_across_both_demo_rooms(seeded, store):
    _, _, rooms = seeded
    book = AnalyticsBook(store)
    assert book.room_assets(rooms[0])["count"] > 0
    assert book.room_assets(rooms[1])["count"] > 0


def test_the_seed_is_deterministic(store):
    """A demo whose asset ids change per run cannot be reviewed against."""
    from dsr.features import load_feature as _load

    module = _load(MODULE)
    rooms = [store.create("room", {"name": f"R{index}"}, actor="dana") for index in range(2)]
    context = {
        "room_ids": [(room["id"], room["data"]["name"]) for room in rooms],
        "now": NOW,
        "rng": None,
    }
    first = module.seed(store.db, context)
    ids_first = sorted(record["data"]["externalId"] for record in AnalyticsBook(store).list_assets())
    second = module.seed(store.db, context)
    ids_second = sorted(record["data"]["externalId"] for record in AnalyticsBook(store).list_assets())
    assert ids_first == ids_second
    assert first.split(",")[1:] == second.split(",")[1:], "re-seeding adds no new telemetry"


def test_the_seed_survives_having_no_rooms(store):
    """The seeder prints what was skipped rather than aborting the whole seed."""
    from dsr.features import load_feature as _load

    summary = _load(MODULE).seed(store.db, {"room_ids": [], "now": NOW, "rng": None})
    assert "no rooms" in summary
    assert AnalyticsBook(store).list_assets() == []
