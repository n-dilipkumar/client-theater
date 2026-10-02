"""Tests for WF-020: extract DSR viewing sessions (dwell time + geography) for BI.

The researched rules this file pins, and why each is worth a test:

* **the row is a tab session, not a visit** - "Each row represents a single session
  in a DSR link from a single user in a single browser tab". So every rollup keeps
  ``sessions`` and ``visitors`` apart, and a test asserts they differ on data that
  looks like the happy path;
* **the pull merges, it does not append** - ``modifiedAt`` exists "so that it can
  pull the updates and merge them into existing data sets". A re-landed row must
  update the row already stored, a byte-identical re-land must not write at all,
  and a retracted-then-returned row must be restored rather than duplicated;
* **the window is the machine filter, not a business one** - it has no business
  meaning, it pages by the 24h refresh SLA, and two filter pairs are alternatives
  rather than something to combine;
* **rows are landed, not dropped** - a reversed timestamp, a duration that
  disagrees with the timestamps, a missing city and an unknown room are all flags,
  because the endpoints exist to "allow large portions of data" to be extracted
  and an ETL that discards rows loses engagement permanently;
* **a session count is not a visitor count** - the vocabulary exists to stop a
  dashboard reporting one as the other.

Plus the structural guarantees: the feature is mounted by discovery alone, its
error handlers are attached by the host, its domain module imports nothing from
``dsr.api``, and **every audit row it writes names a route the app actually
serves** - the defect the port brief names by name.
"""

from __future__ import annotations

import random
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from dsr.api import app
from dsr.db.audited import AuditedDatabase
from dsr.features import load_feature, wf020_reporting_domain as reporting
from dsr.store import RecordStore
from fastapi.testclient import TestClient

PREFIX = "/api/wf-020"
FEATURE_ID = "wf-020-extract-dsr-viewing-sessions-dwell-tim"
MODULE = "wf020_extract_dsr_viewing_sessions_dwell_tim"

NOW = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
# Fixtures and helpers
# --------------------------------------------------------------------------- #


@pytest.fixture()
def client(monkeypatch):
    # A temporary database, exactly as test_features.py does it: the env var is
    # read at call time by dsr.deps, so every test gets its own store.
    tmp = tempfile.TemporaryDirectory()
    monkeypatch.setenv("DSR_DB_PATH", str(Path(tmp.name) / "wf020.db"))
    monkeypatch.setenv("DSR_AUDIT_DIR", str(Path(tmp.name) / "audit"))
    monkeypatch.setattr("dsr.api.FRONTEND_DIST", Path(tmp.name) / "absent-frontend")
    with TestClient(app) as test_client:
        yield test_client
    tmp.cleanup()


@pytest.fixture()
def store():
    """A bare store for the domain rules, with no HTTP in the way."""
    db = AuditedDatabase(":memory:")
    try:
        yield RecordStore(db)
    finally:
        db.close()


def api_session(**overrides):
    """One row shaped exactly as the Reporting v2 endpoint returns it."""
    row = {
        "digitalSalesRoomId": "dsr-001",
        "roomDurationSeconds": 120,
        "engagementUserEmail": "buyer@example.com",
        "isEngagementUserInternal": False,
        "sessionStartedAt": "2026-09-26T09:00:00Z",
        "sessionEndedAt": "2026-09-26T09:02:00Z",
        "city": "Sydney",
        "state": "NSW",
        "country": "AU",
        "ipAddress": "203.0.113.9",
        "geolocationLatitude": -33.8688,
        "geoLocationLongitude": 151.2093,
        "userId": "u-1",
    }
    row.update(overrides)
    return {key: value for key, value in row.items() if value is not ...}


def api_room(**overrides):
    row = {
        "id": "dsr-001",
        "name": "Seismic DSR 001",
        "digitalSalesRoomTemplateId": "tmpl-1",
        "digitalSalesRoomTemplateVersionId": "tmpl-1-v2",
        "createdBy": "u-9",
        "createdByUsername": "dana",
        "createdAt": "2026-06-01T00:00:00Z",
        "modifiedAt": "2026-08-01T00:00:00Z",
        "userModifiedAt": "2026-08-02T00:00:00Z",
    }
    row.update(overrides)
    return {key: value for key, value in row.items() if value is not ...}


def make_room(client, name="Acme"):
    return client.post("/api/records/room", json={"name": name}).json()["id"]


def land(client, rows, **body):
    """POST /extract. ``run_id`` rides in the body: it describes this extraction."""
    return client.post(f"{PREFIX}/extract", json={**body, "rows": rows})


def land_rooms(client, rows, **body):
    return client.post(f"{PREFIX}/rooms", json={**body, "rows": rows})


def audit(client, **params):
    return client.get("/api/audit", params=params).json()["entries"]


def sessions_in(client, **params):
    return client.get(f"{PREFIX}/sessions", params=params).json()


# --------------------------------------------------------------------------- #
# Registration
# --------------------------------------------------------------------------- #


def test_the_feature_is_mounted_by_discovery(client):
    """No file in the host names this feature; discovery mounts it anyway."""
    body = client.get("/api/features").json()
    installed = {feature["id"]: feature for feature in body["features"]}

    assert FEATURE_ID in installed
    record = installed[FEATURE_ID]
    assert record["prefix"] == PREFIX
    assert record["ticket"] == "WF-020"
    # The error mapping moved out of dsr/api.py and into the feature's export.
    assert record["exception_handlers"] == [
        "PayloadError",
        "RoomUnknown",
        "SweepNotDue",
        "WindowError",
    ]


def test_the_feature_did_not_collide_with_anything(client):
    body = client.get("/api/features").json()
    assert body["failed_count"] == 0, body["failed"]


def test_room_scoped_paths_stay_room_scoped(client):
    """The brief's rule, as a test: no room path escapes the feature's prefix."""
    body = client.get("/api/features").json()
    record = {f["id"]: f for f in body["features"]}[FEATURE_ID]
    room_paths = {r["path"] for r in record["routes"] if "room" in r["path"]}

    assert room_paths == {
        f"{PREFIX}/rooms",
        f"{PREFIX}/rooms/{{room_id}}/dwell",
        f"{PREFIX}/rooms/{{room_id}}/sessions",
    }


def test_frontend_descriptor_id_matches_the_backend_feature_id():
    """The two halves of a feature are findable by one name, so they must agree."""
    descriptor = (
        Path(__file__).resolve().parents[2]
        / "frontend"
        / "src"
        / "features"
        / FEATURE_ID
        / "index.jsx"
    )
    module = load_feature(MODULE)

    assert descriptor.is_file(), f"{descriptor} is missing"
    text = descriptor.read_text(encoding="utf-8")
    assert f"id: {module.FEATURE['id']!r}" in text
    assert "Component:" in text


def test_no_feature_module_imports_the_shared_app():
    """The guard in test_features.py is the rule; this states why it matters here."""
    for name in (MODULE, "wf020_reporting_domain"):
        source = Path(load_feature(name).__file__).read_text(encoding="utf-8")
        assert "from dsr.api" not in source, f"{name} imports dsr.api"
        assert "import dsr.api" not in source, f"{name} imports dsr.api"
    feature_source = Path(load_feature(MODULE).__file__).read_text(encoding="utf-8")
    assert "from dsr.deps import" in feature_source


def test_the_domain_module_is_not_mounted_as_a_feature(client):
    """A helper module beside a feature is legitimate; it just has no router."""
    body = client.get("/api/features").json()
    ids = {feature["id"] for feature in body["features"]}
    assert "wf020_reporting_domain" not in ids
    assert FEATURE_ID in ids


# --------------------------------------------------------------------------- #
# The published contract quotes its sources
# --------------------------------------------------------------------------- #


def contract(client):
    return client.get(f"{PREFIX}/contract").json()


def test_the_contract_quotes_the_row_granularity_verbatim(client):
    assert contract(client)["granularity"]["quote"] == (
        "Each row represents a single session in a DSR link from a single user in "
        "a single browser tab."
    )


def test_the_contract_states_the_consequence_of_that_granularity(client):
    assert "not a visitor" in contract(client)["granularity"]["consequence"]


def test_the_contract_quotes_the_refresh_sla_verbatim(client):
    refresh = contract(client)["refresh"]
    assert refresh["quote"] == (
        "The data that is available through our reporting APIs is updated no less "
        "than every 24 hours."
    )
    assert refresh["sla_hours"] == 24
    assert refresh["emphasis"] == "no less than every 24 hours"


def test_the_contract_quotes_the_high_frequency_warning_verbatim(client):
    assert contract(client)["refresh"]["warning"] == (
        "are not designed to be used in high-frequency, interactive use cases."
    )


def test_the_contract_quotes_the_modified_at_semantics_verbatim(client):
    body = contract(client)
    assert body["evidence"]["modified_at_semantics"] == (
        "modifiedAtStartTime / EndTime (preferred for all use cases) - This is a data "
        "modifiedAt time and has no 'business' meaning. It is meant entirely for "
        "machines to know what rows may have changed so that it can pull the updates "
        "and merge them into existing data sets."
    )
    assert body["date_filter"]["preferred"] == "modified"


def test_the_contract_quotes_the_star_schema_requirement_verbatim(client):
    assert contract(client)["evidence"]["star_schema"] == (
        "Most modern business intelligence platforms require a form of star schema."
    )


def test_the_contract_quotes_the_etl_intent_verbatim(client):
    assert contract(client)["evidence"]["etl_intent"].startswith(
        "The reporting APIs are built with data extraction (ETL) in mind."
    )


def test_the_contract_reproduces_the_documented_response_example(client):
    """The example in the research, so a generated job can be diffed against it."""
    example = contract(client)["evidence"]["response_example"]
    assert '"roomDurationSeconds": 1' in example
    assert '"engagementUserEmail": "test engagementUserEmail"' in example
    assert '"isEngagementUserInternal": true' in example
    assert '"city": "test city"' in example
    assert '"ipAddress": "test ipAddress"' in example
    assert '"geolocationLatitude": 4' in example


def test_the_contract_cites_the_five_researched_sources(client):
    assert contract(client)["sources"] == [
        "https://api.seismic.com/reporting/v2/digitalSalesRoomViewingSessions",
        "https://api.seismic.com/reporting/v2/digitalSalesRooms",
        "https://developer.seismic.com/seismicsoftware/reference/h1-reporting-api-overview",
        "https://developer.seismic.com/seismicsoftware/reference/reporting-digitalsalesroomsget",
        "https://developer.seismic.com/seismicsoftware/reference/"
        "reporting-digitalsalesroomviewingsessionsget",
    ]


def test_the_contract_publishes_the_auth_header_on_every_call(client):
    auth = contract(client)["authentication"]
    assert auth["header"] == "Authorization"
    assert auth["scheme"] == "Bearer"
    assert auth["value"] == "Bearer <JWT>"


def test_the_contract_publishes_both_accept_values(client):
    assert contract(client)["accept"] == ["application/json", "text/csv"]


def test_the_contract_publishes_every_documented_session_field(client):
    published = {f["name"] for f in contract(client)["fields"]["digitalSalesRoomViewingSessions"]}
    assert published == {
        "digitalSalesRoomId",
        "roomDurationSeconds",
        "engagementUserEmail",
        "isEngagementUserInternal",
        "sessionStartedAt",
        "sessionEndedAt",
        "city",
        "state",
        "country",
        "ipAddress",
        "geolocationLatitude",
        "geoLocationLongitude",
        "userId",
    }


def test_the_contract_publishes_every_documented_room_field(client):
    published = {f["name"] for f in contract(client)["fields"]["digitalSalesRooms"]}
    assert published == {
        "id",
        "name",
        "digitalSalesRoomTemplateId",
        "digitalSalesRoomTemplateVersionId",
        "createdBy",
        "createdByUsername",
        "createdAt",
        "modifiedAt",
        "userModifiedAt",
    }


def test_the_contract_keeps_the_apis_own_longitude_spelling(client):
    """`geoLocationLongitude` has a capital L in the API and in the research.

    A normaliser that "fixed" it would silently stop reading a documented field,
    so the dictionary publishes the odd spelling rather than the tidy one.
    """
    field = next(
        f
        for f in contract(client)["fields"]["digitalSalesRoomViewingSessions"]
        if f["name"] == "geoLocationLongitude"
    )
    assert field["field"] == "geo_location_longitude"
    assert "geolocationLongitude" in field["also_accepted"]


def test_the_contract_maps_every_api_name_to_a_stored_field(client):
    body = contract(client)
    for resource, documented in body["fields"].items():
        mapped = body["field_mapping"][resource]
        assert set(mapped) == {f["name"] for f in documented}
        for entry in documented:
            assert mapped[entry["name"]] == entry["field"]


def test_the_contract_publishes_the_documented_query_parameters(client):
    endpoints = {e["name"]: e for e in contract(client)["endpoints"]}
    sessions = endpoints["digitalSalesRoomViewingSessions"]
    rooms = endpoints["digitalSalesRooms"]

    assert set(sessions["query"]) == {
        "limit",
        "modifiedAtStartTime",
        "modifiedAtEndTime",
        "sessionStartedAtTime",
        "sessionEndedAtTime",
    }
    assert set(rooms["query"]) == {
        "limit",
        "modifiedAtStartTime",
        "modifiedAtEndTime",
        "createdAtStartTime",
        "createdAtEndTime",
    }
    assert sessions["query"]["modifiedAtStartTime"]["preferred"] is True
    assert sessions["query"]["sessionStartedAtTime"]["preferred"] is False


def test_the_contract_publishes_the_documented_join(client):
    join = contract(client)["join"]
    assert join["on"] == "digitalSalesRoomId"
    assert join["to"] == "/reporting/v2/digitalSalesRooms"


def test_the_contract_publishes_the_star_schema_fact_at_tab_grain(client):
    fact = contract(client)["star_schema"]["fact"]
    assert fact["table"] == reporting.SESSIONS
    assert "one browser tab" in fact["grain"]
    assert fact["key"] == ["digital_sales_room_id", "viewer_key", "session_started_at"]


def test_the_contract_publishes_the_four_computations_the_flow_names(client):
    assert contract(client)["compute_in_bi"] == [
        "dwell time",
        "geography",
        "internal-versus-external",
        "per-user engagement",
    ]


def test_the_contract_states_the_merge_limitation_it_could_not_source(client):
    """The derived session key is a decision, so the contract admits the risk."""
    merge = contract(client)["merge"]
    assert "no session identifier" in merge["limitation"]
    assert merge["behaviour"] == "a re-landed row updates the row already stored"


# --------------------------------------------------------------------------- #
# The window: half-open, one kind, paged by the SLA
# --------------------------------------------------------------------------- #


def test_a_window_with_no_parameters_defaults_to_the_preferred_kind():
    window = reporting.build_window({})
    assert window.kind == "modified"
    assert window.start is None and window.end is None
    assert window.bounded is False


def test_a_window_may_be_given_in_the_apis_own_parameter_names():
    window = reporting.build_window(
        {"modifiedAtStartTime": "2026-09-26T00:00:00Z", "modifiedAtEndTime": "2026-09-27T00:00:00Z"}
    )
    assert window.kind == "modified"
    assert window.as_query() == {
        "modifiedAtStartTime": "2026-09-26T00:00:00+00:00",
        "modifiedAtEndTime": "2026-09-27T00:00:00+00:00",
    }


def test_the_session_filter_pair_is_recognised():
    window = reporting.build_window(
        {
            "sessionStartedAtTime": "2026-09-26T00:00:00Z",
            "sessionEndedAtTime": "2026-09-26T12:00:00Z",
        }
    )
    assert window.kind == "session"
    assert set(window.as_query()) == {"sessionStartedAtTime", "sessionEndedAtTime"}


def test_a_session_filter_is_accepted_for_the_session_feed():
    """The rule above is about the wrong resource, not about the filter itself."""
    window = reporting.build_window(
        {"sessionStartedAtTime": "2026-09-26T00:00:00Z"}, resource="viewing_sessions"
    )
    assert window.kind == "session"


def test_two_filter_pairs_at_once_is_refused():
    """The documented filters are alternatives; the API publishes no combination."""
    with pytest.raises(reporting.WindowError) as caught:
        reporting.build_window(
            {
                "modifiedAtStartTime": "2026-09-26T00:00:00Z",
                "sessionStartedAtTime": "2026-09-26T00:00:00Z",
            }
        )
    assert "one extraction window only" in str(caught.value)
    assert "modifiedAtStartTime/modifiedAtEndTime" in str(caught.value)


def test_a_kind_contradicting_the_supplied_parameters_is_refused():
    with pytest.raises(reporting.WindowError, match="contradicts"):
        reporting.build_window(
            {
                "kind": "session",
                "modifiedAtStartTime": "2026-09-26T00:00:00Z",
            }
        )


def test_an_unknown_kind_names_the_documented_ones():
    with pytest.raises(reporting.WindowError) as caught:
        reporting.build_window({"kind": "business_date"})
    assert "modified" in str(caught.value) and "session" in str(caught.value)


def test_the_session_filter_is_not_offered_for_the_room_inventory():
    with pytest.raises(reporting.WindowError) as caught:
        reporting.build_window(
            {"sessionStartedAtTime": "2026-09-26T00:00:00Z"}, resource="digital_sales_rooms"
        )
    assert "belongs to viewing_sessions" in str(caught.value)


def test_the_created_filter_is_not_offered_for_the_session_feed():
    with pytest.raises(reporting.WindowError) as caught:
        reporting.build_window(
            {"createdAtStartTime": "2026-01-01T00:00:00Z"}, resource="viewing_sessions"
        )
    assert "belongs to digital_sales_rooms" in str(caught.value)


def test_the_created_filter_is_offered_for_the_room_inventory():
    window = reporting.build_window(
        {"createdAtStartTime": "2026-01-01T00:00:00Z"}, resource="digital_sales_rooms"
    )
    assert window.kind == "created"
    assert window.as_query() == {"createdAtStartTime": "2026-01-01T00:00:00+00:00"}


def test_a_reversed_window_is_refused():
    with pytest.raises(reporting.WindowError, match="must be earlier"):
        reporting.build_window(
            {"kind": "modified", "start": "2026-09-27T00:00:00Z", "end": "2026-09-26T00:00:00Z"}
        )


def test_an_empty_window_is_refused():
    """[start, end) with start == end selects nothing, which is never intended."""
    with pytest.raises(reporting.WindowError, match="must be earlier"):
        reporting.build_window(
            {"kind": "modified", "start": "2026-09-26T00:00:00Z", "end": "2026-09-26T00:00:00Z"}
        )


def test_a_window_bound_with_no_offset_is_refused():
    """A machine asking for a window must say which offset it means."""
    with pytest.raises(reporting.WindowError, match="must carry a UTC offset"):
        reporting.build_window({"kind": "modified", "start": "2026-09-26T00:00:00"})


def test_an_unparseable_window_bound_is_refused():
    with pytest.raises(reporting.WindowError, match="not an ISO-8601 timestamp"):
        reporting.build_window({"kind": "modified", "start": "last tuesday"})


def test_a_window_offset_is_normalised_to_utc():
    window = reporting.build_window(
        {
            "kind": "modified",
            "start": "2026-09-26T10:00:00+10:00",
            "end": "2026-09-26T11:00:00+10:00",
        }
    )
    assert window.as_query() == {
        "modifiedAtStartTime": "2026-09-26T00:00:00+00:00",
        "modifiedAtEndTime": "2026-09-26T01:00:00+00:00",
    }
    assert window.hours == 1.0


def test_the_window_is_half_open_at_the_start():
    window = reporting.Window(
        kind="modified",
        start=datetime(2026, 9, 26, tzinfo=timezone.utc),
        end=datetime(2026, 9, 27, tzinfo=timezone.utc),
    )
    assert window.classify(datetime(2026, 9, 26, tzinfo=timezone.utc)) == "in"


def test_the_window_is_half_open_at_the_end():
    """Back-to-back nightly pages: both ends closed double-counts one instant."""
    window = reporting.Window(
        kind="modified",
        start=datetime(2026, 9, 26, tzinfo=timezone.utc),
        end=datetime(2026, 9, 27, tzinfo=timezone.utc),
    )
    assert window.classify(datetime(2026, 9, 27, tzinfo=timezone.utc)) == "out"
    assert window.to_dict()["half_open"] is True


def test_classify_reports_unknown_rather_than_guessing():
    window = reporting.Window(
        kind="modified",
        start=datetime(2026, 9, 26, tzinfo=timezone.utc),
        end=datetime(2026, 9, 27, tzinfo=timezone.utc),
    )
    assert window.classify(None) == "unknown"


def test_an_unbounded_window_has_no_span():
    window = reporting.Window(kind="modified", start=None, end=None)
    assert window.hours is None
    assert window.within_sla is None


def test_a_window_narrower_than_the_sla_is_annotated_not_refused():
    window = reporting.Window(
        kind="modified",
        start=datetime(2026, 9, 26, tzinfo=timezone.utc),
        end=datetime(2026, 9, 26, 6, tzinfo=timezone.utc),
    )
    assert window.hours == 6.0
    assert window.within_sla is False


def test_a_window_of_one_sla_is_within_it():
    window = reporting.Window(
        kind="modified",
        start=datetime(2026, 9, 26, tzinfo=timezone.utc),
        end=datetime(2026, 9, 27, tzinfo=timezone.utc),
    )
    assert window.hours == 24.0
    assert window.within_sla is True


def test_a_page_size_of_zero_hours_is_refused(store):
    with pytest.raises(reporting.WindowError, match="greater than zero"):
        reporting.next_window(store, now=NOW, hours=0)


# --------------------------------------------------------------------------- #
# Row normalisation
# --------------------------------------------------------------------------- #


def test_the_documented_camel_case_names_land_as_snake_case():
    payload = reporting.normalise_session(api_session())
    assert payload["digital_sales_room_id"] == "dsr-001"
    assert payload["room_duration_seconds"] == 120
    assert payload["engagement_user_email"] == "buyer@example.com"
    assert payload["is_engagement_user_internal"] is False
    assert payload["user_id"] == "u-1"
    assert payload["geolocation_latitude"] == -33.8688


def test_the_odd_longitude_spelling_is_read():
    payload = reporting.normalise_session(api_session())
    assert payload["geo_location_longitude"] == 151.2093


def test_the_tidy_longitude_spelling_is_also_accepted():
    """A fixed spelling must not be the only way in, or a corrected payload dies."""
    row = api_session()
    del row["geoLocationLongitude"]
    row["geolocationLongitude"] = 12.5
    assert reporting.normalise_session(row)["geo_location_longitude"] == 12.5


def test_both_the_reported_and_the_elapsed_dwell_are_kept():
    payload = reporting.normalise_session(
        api_session(roomDurationSeconds=120, sessionEndedAt="2026-09-26T09:02:00Z")
    )
    assert payload["room_duration_seconds"] == 120
    assert payload["elapsed_duration_seconds"] == 120
    assert payload["duration_delta_seconds"] == 0
    assert payload["quality_flags"] == []


def test_a_duration_beyond_the_tolerance_is_flagged_not_corrected():
    """The ETL must not decide which of the two numbers the vendor meant."""
    payload = reporting.normalise_session(
        api_session(roomDurationSeconds=40, sessionEndedAt="2026-09-26T09:08:00Z")
    )
    assert "duration_mismatch" in payload["quality_flags"]
    assert payload["room_duration_seconds"] == 40
    assert payload["elapsed_duration_seconds"] == 480


def test_a_duration_within_the_tolerance_is_not_flagged():
    payload = reporting.normalise_session(
        api_session(roomDurationSeconds=119, sessionEndedAt="2026-09-26T09:02:00Z")
    )
    assert "duration_mismatch" not in payload["quality_flags"]


def test_a_missing_duration_is_flagged_and_stored_as_null():
    payload = reporting.normalise_session(api_session(roomDurationSeconds=...))
    assert payload["room_duration_seconds"] is None
    assert "duration_missing" in payload["quality_flags"]


def test_a_non_numeric_duration_is_flagged_rather_than_crashing():
    payload = reporting.normalise_session(api_session(roomDurationSeconds="about two minutes"))
    assert payload["room_duration_seconds"] is None
    assert "duration_missing" in payload["quality_flags"]


def test_a_negative_duration_is_flagged_and_kept():
    payload = reporting.normalise_session(api_session(roomDurationSeconds=-5))
    assert payload["room_duration_seconds"] == -5
    assert "duration_negative" in payload["quality_flags"]


def test_a_session_that_ends_before_it_starts_is_landed_and_flagged():
    payload = reporting.normalise_session(
        api_session(sessionStartedAt="2026-09-26T09:02:00Z", sessionEndedAt="2026-09-26T09:00:00Z")
    )
    assert "session_end_before_start" in payload["quality_flags"]
    assert payload["elapsed_duration_seconds"] == -120


def test_a_naive_row_timestamp_is_read_as_utc_and_flagged():
    payload = reporting.normalise_session(
        api_session(sessionStartedAt="2026-09-26T09:00:00", sessionEndedAt="2026-09-26T09:02:00")
    )
    assert payload["session_started_at"] == "2026-09-26T09:00:00+00:00"
    assert "timestamp_assumed_utc" in payload["quality_flags"]


def test_an_offset_row_timestamp_is_not_flagged():
    payload = reporting.normalise_session(api_session())
    assert "timestamp_assumed_utc" not in payload["quality_flags"]


def test_the_viewer_is_the_user_id_when_there_is_one():
    assert reporting.viewer_of(api_session()) == ("user:u-1", "user_id")


def test_the_viewer_falls_back_to_the_email():
    row = api_session()
    del row["userId"]
    assert reporting.viewer_of(row) == ("email:buyer@example.com", "email")


def test_email_casing_cannot_fork_one_buyer_into_two_viewers():
    lower = reporting.viewer_of(api_session(userId=..., engagementUserEmail="Buyer@Example.com"))
    upper = reporting.viewer_of(api_session(userId=..., engagementUserEmail="buyer@example.COM"))
    assert lower == upper == ("email:buyer@example.com", "email")


def test_a_row_with_no_identity_is_still_a_viewer():
    row = api_session(userId=..., engagementUserEmail=...)
    assert reporting.viewer_of(row) == ("anonymous", "anonymous")
    assert "unattributed" in reporting.normalise_session(row)["quality_flags"]


def test_an_explicit_session_id_wins_over_the_derived_key():
    """The documented field list has no session id; the moment one arrives it wins."""
    row = api_session(viewingSessionId="vs-777")
    assert reporting.normalise_session(row)["session_key"] == "session:vs-777"


def test_a_row_with_no_usable_start_falls_back_to_a_digest_of_itself():
    row = api_session(sessionStartedAt=..., sessionEndedAt=...)
    first = reporting.normalise_session(row)["session_key"]
    second = reporting.normalise_session(row)["session_key"]
    assert first == second
    assert "unparsed:" in first


def test_two_different_corrupt_rows_get_different_keys():
    one = reporting.normalise_session(api_session(sessionStartedAt=..., sessionEndedAt=...))
    two = reporting.normalise_session(
        api_session(sessionStartedAt=..., sessionEndedAt=..., roomDurationSeconds=999)
    )
    assert one["session_key"] != two["session_key"]


def test_the_merge_key_is_room_viewer_and_start_instant():
    payload = reporting.normalise_session(api_session())
    assert payload["session_key"] == "dsr-001|user:u-1|2026-09-26T09:00:00+00:00"


def test_undocumented_fields_are_kept_rather_than_dropped():
    """The dictionary lists fields the response "include"s, not all of them."""
    payload = reporting.normalise_session(api_session(documentId="doc-42"))
    assert payload["extra"] == {"documentId": "doc-42"}
    assert "undocumented_fields" in payload["quality_flags"]


def test_documented_fields_do_not_leak_into_extra():
    payload = reporting.normalise_session(api_session())
    assert "extra" not in payload
    assert "undocumented_fields" not in payload["quality_flags"]


def test_undocumented_fields_are_still_queryable(store):
    """Schema flexibility means a field nobody declared is still findable."""
    reporting.land_sessions(store, None, [api_session(documentId="doc-42")], source="test")
    found = store.find(reporting.SESSIONS, {"extra.documentId": "doc-42"})
    assert len(found) == 1


def test_fully_missing_geography_is_flagged():
    payload = reporting.normalise_session(
        api_session(
            city=..., state=..., country=..., geolocationLatitude=..., geoLocationLongitude=...
        )
    )
    assert "geography_missing" in payload["quality_flags"]


def test_partial_geography_is_flagged():
    payload = reporting.normalise_session(api_session(state=...))
    assert "geography_partial" in payload["quality_flags"]


def test_a_row_with_no_room_id_is_flagged():
    payload = reporting.normalise_session(api_session(digitalSalesRoomId=...))
    assert payload["digital_sales_room_id"] is None
    assert "room_missing" in payload["quality_flags"]


def test_a_non_object_row_is_refused():
    with pytest.raises(reporting.PayloadError, match="must be an object"):
        reporting.normalise_session("a session")


def test_a_boolean_internal_flag_survives_both_spellings():
    assert (
        reporting.normalise_session(api_session(isEngagementUserInternal="true"))[
            "is_engagement_user_internal"
        ]
        is True
    )
    assert (
        reporting.normalise_session(
            api_session(isEngagementUserInternal=..., is_engagement_user_internal=False)
        )["is_engagement_user_internal"]
        is False
    )


def test_an_absent_internal_flag_stays_unknown_rather_than_becoming_false():
    payload = reporting.normalise_session(api_session(isEngagementUserInternal=...))
    assert payload["is_engagement_user_internal"] is None


def test_the_window_verdict_is_recorded_on_the_row():
    window = reporting.Window(
        kind="session",
        start=datetime(2026, 9, 26, tzinfo=timezone.utc),
        end=datetime(2026, 9, 27, tzinfo=timezone.utc),
    )
    inside = reporting.normalise_session(api_session(), window=window)
    outside = reporting.normalise_session(
        api_session(sessionStartedAt="2026-09-28T09:00:00Z", sessionEndedAt="2026-09-28T09:02:00Z"),
        window=window,
    )
    assert inside["window_verdict"] == "in"
    assert "outside_window" not in inside["quality_flags"]
    assert outside["window_verdict"] == "out"
    assert "outside_window" in outside["quality_flags"]


def test_a_row_that_cannot_be_checked_is_not_called_a_defect():
    """No documented session field is a modifiedAt, so this is the normal case."""
    window = reporting.Window(
        kind="modified",
        start=datetime(2026, 9, 26, tzinfo=timezone.utc),
        end=datetime(2026, 9, 27, tzinfo=timezone.utc),
    )
    payload = reporting.normalise_session(api_session(), window=window)
    assert payload["window_verdict"] == "unknown"
    assert payload["quality_flags"] == []


def test_the_ip_is_masked_when_redaction_is_asked_for():
    payload = reporting.normalise_session(api_session(ipAddress="203.0.113.42"), redact_ip=True)
    assert payload["ip_address"] == "203.0.0.0"
    assert "ip_redacted" in payload["quality_flags"]


def test_an_ipv6_address_is_masked_by_the_network_half():
    assert reporting.mask_ip("2001:db8:85a3:0:0:8a2e:370:7334").startswith("2001:db8:85a3:0:")


# --------------------------------------------------------------------------- #
# Landing and the merge
# --------------------------------------------------------------------------- #


def live(store, collection, limit=100):
    """Live records in a collection. ``RecordStore`` has no ``count``."""
    return store.list(collection, limit=limit)


def test_landing_creates_one_record_per_row(store):
    counters = reporting.land_sessions(
        store, None, [api_session(), api_session(userId="u-2")], source="test"
    )
    assert counters["created"] == 2
    assert counters["landed"] == 2
    assert len(live(store, reporting.SESSIONS)) == 2


def test_a_re_landed_row_updates_rather_than_duplicating(store):
    """The documented reason the incremental filter exists at all."""
    reporting.land_sessions(store, None, [api_session()], source="test")
    counters = reporting.land_sessions(
        store, None, [api_session(roomDurationSeconds=300)], source="test"
    )
    assert counters["updated"] == 1
    assert counters["created"] == 0
    assert len(live(store, reporting.SESSIONS)) == 1
    assert live(store, reporting.SESSIONS)[0]["data"]["room_duration_seconds"] == 300


def test_a_byte_identical_re_land_writes_nothing(store):
    """modifiedAt is a may-have-changed signal; an unchanged row should be quiet."""
    reporting.land_sessions(store, None, [api_session()], source="test")
    before = len(store.audit(limit=1000))
    counters = reporting.land_sessions(store, None, [api_session()], source="test")
    assert counters["unchanged"] == 1
    assert counters["updated"] == 0
    assert len(store.audit(limit=1000)) == before


def test_an_unchanged_re_land_does_not_bump_the_revision(store):
    reporting.land_sessions(store, None, [api_session()], source="test")
    first = store.list(reporting.SESSIONS)[0]["revision"]
    reporting.land_sessions(store, None, [api_session()], source="test")
    assert store.list(reporting.SESSIONS)[0]["revision"] == first


def test_a_vendor_clearing_a_field_clears_it_here_too(store):
    """A shallow merge must not leave a stale city frozen in the row."""
    reporting.land_sessions(store, None, [api_session()], source="test")
    reporting.land_sessions(store, None, [api_session(city=...)], source="test")
    assert store.list(reporting.SESSIONS)[0]["data"]["city"] is None


def test_a_retracted_row_that_came_back_is_restored_not_duplicated(store):
    reporting.land_sessions(store, None, [api_session()], source="test")
    record = live(store, reporting.SESSIONS)[0]
    store.delete(record["id"], source="test")
    counters = reporting.land_sessions(
        store, None, [api_session(roomDurationSeconds=999)], source="test"
    )
    assert counters["restored"] == 1
    assert len(live(store, reporting.SESSIONS)) == 1
    assert live(store, reporting.SESSIONS)[0]["data"]["room_duration_seconds"] == 999


def test_the_same_key_twice_in_one_batch_is_one_session(store):
    counters = reporting.land_sessions(
        store, None, [api_session(), api_session(roomDurationSeconds=7)], source="test"
    )
    assert counters["duplicate_keys"] == 1
    assert counters["created"] == 1
    assert counters["updated"] == 1
    assert len(live(store, reporting.SESSIONS)) == 1


def test_a_row_that_is_not_an_object_is_rejected_and_the_rest_still_land(store):
    counters = reporting.land_sessions(
        store, None, [api_session(), "nonsense", api_session(userId="u-2")], source="test"
    )
    assert counters["received"] == 3
    assert counters["rejected"] == 1
    assert counters["landed"] == 2
    assert counters["rejections"][0]["index"] == 1
    assert len(live(store, reporting.SESSIONS)) == 2


def test_an_empty_batch_is_a_request_that_cannot_be_attempted(store):
    with pytest.raises(reporting.PayloadError, match="rows is empty"):
        reporting.land_sessions(store, None, [], source="test")


def test_a_batch_that_is_not_a_list_is_refused(store):
    with pytest.raises(reporting.PayloadError, match="must be a list"):
        reporting.land_sessions(store, None, {"digitalSalesRoomId": "x"}, source="test")


def test_rejection_detail_is_capped(store):
    rows = ["nonsense"] * (reporting.REJECTION_DETAIL_LIMIT + 5)
    counters = reporting.land_sessions(store, None, rows, source="test")
    assert counters["rejected"] == reporting.REJECTION_DETAIL_LIMIT + 5
    assert len(counters["rejections"]) == reporting.REJECTION_DETAIL_LIMIT
    assert counters["rejections_truncated"] == 5


def test_the_counters_add_up_to_what_arrived(store):
    rows = [api_session(userId=f"u-{n}") for n in range(6)] + ["bad"]
    counters = reporting.land_sessions(store, None, rows, source="test")
    assert counters["received"] == 7
    assert counters["landed"] + counters["rejected"] + counters["unchanged"] == 7


def test_a_session_for_an_unknown_room_is_landed_and_flagged(store):
    counters = reporting.land_sessions(store, None, [api_session()], source="test")
    assert counters["unresolved_rooms"] == 1
    stored = store.list(reporting.SESSIONS)[0]["data"]
    assert stored["room_unresolved"] is True
    assert "room_unresolved" in stored["quality_flags"]


def test_a_session_whose_room_is_in_the_inventory_is_not_flagged(store):
    reporting.land_rooms(store, None, [api_room()], source="test")
    counters = reporting.land_sessions(store, None, [api_session()], source="test")
    assert counters["unresolved_rooms"] == 0
    assert "room_unresolved" not in store.list(reporting.SESSIONS)[0]["data"]["quality_flags"]


def test_later_landing_the_inventory_resolves_the_flag_on_read(store):
    """The join is resolved on read, so a late inventory pull is not a re-extraction."""
    reporting.land_sessions(store, None, [api_session()], source="test")
    assert reporting.sessions_for(store)[0]["room_resolved"] is False
    reporting.land_rooms(store, None, [api_room()], source="test")
    row = reporting.sessions_for(store)[0]
    assert row["room_resolved"] is True
    assert row["room_name"] == "Seismic DSR 001"


def test_a_renamed_room_is_reflected_without_re_extracting_the_sessions(store):
    reporting.land_rooms(store, None, [api_room()], source="test")
    reporting.land_sessions(store, None, [api_session()], source="test")
    reporting.land_rooms(store, None, [api_room(name="Renamed")], source="test")
    assert reporting.sessions_for(store)[0]["room_name"] == "Renamed"


def test_the_dwell_total_counts_reported_seconds_only(store):
    reporting.land_sessions(
        store,
        None,
        [api_session(userId="u-1"), api_session(userId="u-2", roomDurationSeconds=30)],
        source="test",
    )
    rows = reporting.sessions_for(store)
    assert reporting.summarise(rows)["dwell_seconds"] == 150


def test_rooms_merge_on_the_room_key_too(store):
    reporting.land_rooms(store, None, [api_room()], source="test")
    counters = reporting.land_rooms(store, None, [api_room(name="Renamed")], source="test")
    assert counters["updated"] == 1
    assert len(live(store, reporting.ROOMS)) == 1


def test_an_inventory_row_with_no_id_is_rejected_rather_than_raised(store):
    """Batch semantics: one unusable row must not abort the inventory landing."""
    counters = reporting.land_rooms(store, None, [{"name": "no id"}], source="test")
    assert counters["rejected"] == 1
    assert "needs an id" in counters["rejections"][0]["error"]
    assert len(live(store, reporting.ROOMS)) == 0


def test_a_user_edit_on_a_room_is_recorded_distinctly_from_a_data_change(store):
    reporting.land_rooms(store, None, [api_room()], source="test")
    stored = live(store, reporting.ROOMS)[0]["data"]
    assert stored["modified_at"] != stored["user_modified_at"]
    assert "user_modified" in stored["quality_flags"]


def test_an_inventory_row_can_be_bound_to_a_core_room_as_it_lands(store):
    reporting.land_rooms(store, None, [api_room(bound_room_id="room_1")], source="test")
    assert live(store, reporting.ROOMS)[0]["data"]["bound_room_id"] == "room_1"


# --------------------------------------------------------------------------- #
# Rollups: sessions are not visitors
# --------------------------------------------------------------------------- #


def sample_rows():
    """Four tab sessions: three viewers across two rooms, one of them internal."""
    return [
        {
            **reporting.normalise_session(
                api_session(userId="u-1", roomDurationSeconds=100, digitalSalesRoomId="dsr-001")
            ),
            "room_name": "Room A",
        },
        {
            **reporting.normalise_session(
                api_session(userId="u-1", roomDurationSeconds=50, digitalSalesRoomId="dsr-001")
            ),
            "room_name": "Room A",
        },
        {
            **reporting.normalise_session(
                api_session(
                    userId="u-2",
                    roomDurationSeconds=25,
                    digitalSalesRoomId="dsr-002",
                    isEngagementUserInternal=False,
                )
            ),
            "room_name": "Room B",
        },
        {
            **reporting.normalise_session(
                api_session(
                    userId="u-3",
                    roomDurationSeconds=10,
                    digitalSalesRoomId="dsr-002",
                    isEngagementUserInternal=True,
                )
            ),
            "room_name": "Room B",
        },
    ]


def test_a_row_count_is_a_session_count_and_not_a_visitor_count():
    summary = reporting.summarise(sample_rows())
    assert summary["sessions"] == 4
    assert summary["visitors"] == 3


def test_the_summary_says_so_in_words():
    assert "not one visit" in reporting.summarise(sample_rows())["granularity"]


def test_the_internal_and_external_split_is_reported():
    summary = reporting.summarise(sample_rows())
    assert summary["external"]["sessions"] == 3
    assert summary["internal"]["sessions"] == 1
    assert summary["internal"]["dwell_seconds"] == 10
    assert summary["external"]["dwell_seconds"] == 175


def test_dwell_by_room_groups_and_ranks():
    rollup = reporting.dwell_rollup(sample_rows(), group_by="room")
    assert [entry["group"] for entry in rollup] == ["dsr-001", "dsr-002"]
    assert rollup[0]["sessions"] == 2
    assert rollup[0]["dwell_seconds"] == 150
    assert rollup[0]["label"] == "Room A"
    assert rollup[1]["dwell_seconds"] == 35


def test_dwell_by_viewer_keeps_the_tabs_together():
    rollup = reporting.dwell_rollup(sample_rows(), group_by="viewer")
    assert [entry["group"] for entry in rollup] == ["user:u-1", "user:u-2", "user:u-3"]
    assert rollup[0]["sessions"] == 2


def test_the_geography_rollup_nests_country_state_city():
    rollup = reporting.geography_rollup(sample_rows())
    assert [entry["country"] for entry in rollup] == ["AU"]
    assert rollup[0]["sessions"] == 4
    assert rollup[0]["states"][0]["state"] == "NSW"
    assert rollup[0]["states"][0]["cities"][0]["city"] == "Sydney"


def test_the_centroid_is_the_mean_and_says_how_much_it_covers():
    rollup = reporting.geography_rollup(sample_rows())
    assert rollup[0]["rows_with_coordinates"] == 4
    assert rollup[0]["centroid"] == {"latitude": -33.8688, "longitude": 151.2093}


def test_a_rollup_with_no_coordinates_reports_no_centroid():
    rows = [
        dict(
            reporting.normalise_session(
                api_session(
                    city=...,
                    state=...,
                    country=...,
                    geolocationLatitude=...,
                    geoLocationLongitude=...,
                )
            )
        )
    ]
    assert reporting.geography_rollup(rows)[0]["centroid"] is None


def test_the_viewer_rollup_carries_both_internal_flags():
    rollup = {entry["viewer_key"]: entry for entry in reporting.viewer_rollup(sample_rows())}
    assert rollup["user:u-3"]["is_internal"] is True
    assert rollup["user:u-3"]["is_external"] is False
    assert rollup["user:u-1"]["is_external"] is True


def test_the_viewer_rollup_reports_first_and_last_seen():
    rollup = {entry["viewer_key"]: entry for entry in reporting.viewer_rollup(sample_rows())}
    assert rollup["user:u-1"]["sessions"] == 2
    assert rollup["user:u-1"]["dwell_seconds"] == 150


def test_the_summary_counts_quality_flags_by_name():
    rows = [
        reporting.normalise_session(
            api_session(
                city=..., state=..., country=..., geolocationLatitude=..., geoLocationLongitude=...
            )
        )
    ]
    assert reporting.summarise(rows)["quality_flags"] == {"geography_missing": 1}


def test_the_summary_counts_a_partial_geography_as_partial():
    rows = [
        reporting.normalise_session(api_session(geolocationLatitude=..., geoLocationLongitude=...))
    ]
    assert reporting.summarise(rows)["quality_flags"] == {"geography_partial": 1}


def test_sensitive_fields_are_withheld_unless_asked_for(store):
    reporting.land_sessions(store, None, [api_session()], source="test")

    withheld = reporting.sessions_for(store, include_sensitive=False)[0]
    assert "ip_address" not in withheld
    assert "engagement_user_email" not in withheld
    assert "geolocation_latitude" not in withheld
    assert "geo_location_longitude" not in withheld

    revealed = reporting.sessions_for(store, include_sensitive=True)[0]
    assert revealed["ip_address"] == "203.0.113.9"
    assert revealed["engagement_user_email"] == "buyer@example.com"


def test_a_withheld_row_still_carries_its_dwell_and_its_room(store):
    """Withholding is about identity, not about the numbers the workflow is for."""
    reporting.land_rooms(store, None, [api_room()], source="test")
    reporting.land_sessions(store, None, [api_session()], source="test")
    row = reporting.sessions_for(store, include_sensitive=False)[0]
    assert row["room_duration_seconds"] == 120
    assert row["room_name"] == "Seismic DSR 001"
    assert row["country"] == "AU"


# --------------------------------------------------------------------------- #
# Runs, the watermark, and the sweep
# --------------------------------------------------------------------------- #


def test_there_is_no_watermark_before_anything_lands(store):
    assert reporting.watermark(store) is None


def test_a_run_opens_as_requested(store):
    window = reporting.build_window({"kind": "modified", "end": "2026-09-27T00:00:00Z"})
    run = reporting.open_run(
        store,
        window=window,
        resource="viewing_sessions",
        run_kind="sweep",
        fmt=reporting.ACCEPT_JSON,
        limit=None,
        started=NOW,
        source="test",
    )
    assert run["data"]["state"] == "requested"
    assert run["data"]["landed_at"] is None


def test_closing_a_run_records_the_counters_and_the_advanced_watermark(store):
    window = reporting.build_window({"kind": "modified", "end": "2026-09-27T00:00:00Z"})
    run = reporting.open_run(
        store,
        window=window,
        resource="viewing_sessions",
        run_kind="sweep",
        fmt=reporting.ACCEPT_JSON,
        limit=None,
        started=NOW,
        source="test",
    )
    closed = reporting.close_run(store, run["id"], {"landed": 3}, finished=NOW, source="test")
    assert closed["data"]["state"] == "landed"
    assert closed["data"]["counters"] == {"landed": 3}
    assert closed["data"]["watermark_after"] == "2026-09-27T00:00:00+00:00"


def test_a_run_cannot_be_landed_twice(store):
    window = reporting.build_window({"kind": "modified", "end": "2026-09-27T00:00:00Z"})
    run = reporting.open_run(
        store,
        window=window,
        resource="viewing_sessions",
        run_kind="sweep",
        fmt=reporting.ACCEPT_JSON,
        limit=None,
        started=NOW,
        source="test",
    )
    reporting.close_run(store, run["id"], {}, finished=NOW, source="test")
    with pytest.raises(reporting.PayloadError, match="already landed"):
        reporting.close_run(store, run["id"], {}, finished=NOW, source="test")


def test_landing_a_run_that_does_not_exist_is_refused(store):
    with pytest.raises(reporting.PayloadError, match="no extraction run"):
        reporting.close_run(store, "nope", {}, finished=NOW, source="test")


def test_an_unsupported_format_is_refused_when_a_run_opens(store):
    window = reporting.build_window({"kind": "modified", "end": "2026-09-27T00:00:00Z"})
    with pytest.raises(reporting.PayloadError, match="unsupported Accept"):
        reporting.open_run(
            store,
            window=window,
            resource="viewing_sessions",
            run_kind="sweep",
            fmt="application/xml",
            limit=None,
            started=NOW,
            source="test",
        )


def land_a_run(store, end="2026-09-27T00:00:00Z", kind="modified", resource="viewing_sessions"):
    """Open and close one run over a 24h window ending at ``end``."""
    stop = reporting.parse_instant(end, field="end")
    window = reporting.build_window(
        {"kind": kind, "start": (stop - timedelta(hours=24)).isoformat(), "end": end},
        resource=resource,
    )
    run = reporting.open_run(
        store,
        window=window,
        resource=resource,
        run_kind="extract",
        fmt=reporting.ACCEPT_JSON,
        limit=None,
        started=NOW,
        source="test",
    )
    return reporting.close_run(store, run["id"], {}, finished=NOW, source="test")


def test_the_watermark_comes_from_the_run_that_landed(store):
    land_a_run(store)
    assert reporting.watermark(store) == "2026-09-27T00:00:00+00:00"


def test_the_watermark_is_derived_not_stored_so_it_cannot_drift(store):
    """There is no singleton to fall out of step with the runs."""
    land_a_run(store)
    assert store.find("watermark", {}, limit=1) == []


def test_a_session_filtered_run_does_not_move_the_watermark(store):
    """A window that says nothing about what changed must not advance the mark."""
    land_a_run(store, kind="session", resource="viewing_sessions")
    assert reporting.watermark(store) is None


def test_a_requested_run_does_not_move_the_watermark(store):
    window = reporting.build_window({"kind": "modified", "end": "2026-09-28T00:00:00Z"})
    reporting.open_run(
        store,
        window=window,
        resource="viewing_sessions",
        run_kind="sweep",
        fmt=reporting.ACCEPT_JSON,
        limit=None,
        started=NOW,
        source="test",
    )
    assert reporting.watermark(store) is None


def test_the_room_inventory_keeps_its_own_watermark(store):
    land_a_run(store, resource="digital_sales_rooms", end="2026-09-20T00:00:00Z")
    assert reporting.watermark(store, resource="digital_sales_rooms") == "2026-09-20T00:00:00+00:00"
    assert reporting.watermark(store, resource="viewing_sessions") is None


def test_the_first_window_backfills_below_and_closes_above(store):
    plan = reporting.next_window(store, now=NOW)
    assert plan["window"]["start"] is None
    assert plan["window"]["end"] == NOW.isoformat()
    assert plan["sweep"]["reason"] == "never_run"
    assert any("backfills" in note for note in plan["notes"])


def test_the_next_window_starts_at_the_watermark_and_pages_by_the_sla(store):
    land_a_run(store, end="2026-09-27T00:00:00Z")
    plan = reporting.next_window(store, now=NOW)
    assert plan["window"]["query"] == {
        "modifiedAtStartTime": "2026-09-27T00:00:00+00:00",
        "modifiedAtEndTime": "2026-09-28T00:00:00+00:00",
    }
    assert plan["window"]["hours"] == 24.0
    assert plan["window"]["within_sla"] is True


def test_the_documented_limit_is_passed_through_to_the_job(store):
    plan = reporting.next_window(store, now=NOW, limit=500)
    assert plan["query"]["limit"] == "500"


def test_the_limit_is_left_to_the_server_when_it_is_not_given(store):
    assert "limit" not in reporting.next_window(store, now=NOW)["query"]


def test_a_narrower_page_is_annotated_as_not_fresher(store):
    land_a_run(store, end="2026-09-27T00:00:00Z")
    plan = reporting.next_window(store, now=NOW, hours=6)
    assert plan["window"]["within_sla"] is False
    assert any("cannot return anything fresher" in note for note in plan["notes"])


def test_pages_behind_counts_the_outstanding_sla_pages(store):
    """Watermark 2026-09-20, now 2026-09-27T12:00: seven 24h pages are outstanding."""
    land_a_run(store, end="2026-09-20T00:00:00Z")
    plan = reporting.next_window(store, now=NOW)
    assert plan["pages_behind"] == 7
    assert any("outstanding" in note for note in plan["notes"])


def test_pages_behind_is_one_when_the_job_is_current(store):
    land_a_run(store, end="2026-09-27T06:00:00Z")
    assert reporting.next_window(store, now=NOW)["pages_behind"] == 1


def test_a_sweep_is_due_when_nothing_has_ever_landed(store):
    state = reporting.due_state(store, now=NOW)
    assert state["due"] is True
    assert state["reason"] == "never_run"


def test_a_sweep_is_not_due_inside_the_sla(store):
    run = land_a_run(store)
    store.update(run["id"], {"landed_at": (NOW - timedelta(hours=3)).isoformat()}, source="test")
    state = reporting.due_state(store, now=NOW)
    assert state["due"] is False
    assert state["reason"] == "within_sla"
    assert state["due_in_hours"] == 21.0


def test_a_sweep_becomes_due_once_the_sla_has_elapsed(store):
    run = land_a_run(store)
    store.update(run["id"], {"landed_at": (NOW - timedelta(hours=25)).isoformat()}, source="test")
    state = reporting.due_state(store, now=NOW)
    assert state["due"] is True
    assert state["reason"] == "sla_elapsed"


def test_a_run_that_never_landed_blocks_a_second_window(store):
    """Two overlapping pages of the same incremental filter double-count the day."""
    window = reporting.build_window({"kind": "modified", "end": "2026-09-28T00:00:00Z"})
    reporting.open_run(
        store,
        window=window,
        resource="viewing_sessions",
        run_kind="sweep",
        fmt=reporting.ACCEPT_JSON,
        limit=None,
        started=NOW,
        source="test",
    )
    state = reporting.due_state(store, now=NOW)
    assert state["due"] is False
    assert state["reason"] == "in_flight"
    assert state["pending_run_id"]


def test_a_window_round_trips_through_the_run_it_is_stored_on(store):
    window = reporting.build_window(
        {"kind": "modified", "start": "2026-09-26T00:00:00Z", "end": "2026-09-27T00:00:00Z"}
    )
    run = {"data": {"resource": "viewing_sessions", "window": window.to_dict()}}
    assert reporting.window_from_run(run).as_query() == window.as_query()


# --------------------------------------------------------------------------- #
# HTTP: the contract and the window
# --------------------------------------------------------------------------- #


def test_the_contract_is_served(client):
    response = client.get(f"{PREFIX}/contract")
    assert response.status_code == 200
    assert response.json()["ticket"] == "WF-020"


def test_the_window_is_served_with_the_documented_query(client):
    body = client.get(f"{PREFIX}/window").json()
    assert set(body["query"]) <= {"modifiedAtStartTime", "modifiedAtEndTime", "limit"}
    assert body["sweep"]["sla_hours"] == 24
    assert body["format"] == "application/json"


def test_the_window_serves_the_room_inventory_too(client):
    body = client.get(f"{PREFIX}/window", params={"resource": "digital_sales_rooms"}).json()
    assert body["resource"] == "digital_sales_rooms"


def test_an_unknown_resource_is_refused(client):
    response = client.get(f"{PREFIX}/window", params={"resource": "widgets"})
    assert response.status_code == 422
    assert response.json()["error"] == "invalid_payload"


def test_an_accept_this_extraction_cannot_produce_is_refused(client):
    """The contract publishes two Accept values, and only two."""
    response = client.get(f"{PREFIX}/window", params={"fmt": "application/xml"})
    assert response.status_code == 422
    assert response.json()["error"] == "invalid_payload"


def test_the_window_serves_the_csv_format_for_a_flat_file_load(client):
    body = client.get(f"{PREFIX}/window", params={"fmt": "csv"}).json()
    assert body["format"] == "text/csv"


def test_the_page_size_must_be_positive(client):
    assert client.get(f"{PREFIX}/window", params={"hours": 0}).status_code == 422


# --------------------------------------------------------------------------- #
# HTTP: the sweep
# --------------------------------------------------------------------------- #


def test_a_sweep_opens_the_next_run_when_none_has_landed(client):
    response = client.post(f"{PREFIX}/sweep")
    assert response.status_code == 200
    body = response.json()
    assert body["due"] is True
    assert body["run"]["data"]["state"] == "requested"
    assert body["run"]["data"]["run_kind"] == "sweep"


def test_a_sweep_is_refused_while_a_run_is_still_in_flight(client):
    client.post(f"{PREFIX}/sweep")
    response = client.post(f"{PREFIX}/sweep")
    assert response.status_code == 409
    assert response.json()["error"] == "sweep_not_due"
    assert "in_flight" in response.json()["detail"]


def test_a_sweep_is_refused_inside_the_sla(client):
    land(client, [api_session()])
    response = client.post(f"{PREFIX}/sweep")
    assert response.status_code == 409
    assert "within_sla" in response.json()["detail"]


# --------------------------------------------------------------------------- #
# HTTP: landing
# --------------------------------------------------------------------------- #


def test_landing_sessions_is_a_201_and_reports_its_counters(client):
    response = land(client, [api_session(), api_session(userId="u-2")])
    assert response.status_code == 201
    counters = response.json()["counters"]
    assert counters["created"] == 2
    assert counters["landed"] == 2
    assert response.json()["window"]["kind"] == "modified"


def test_landing_accepts_the_apis_own_window_parameter_names(client):
    response = land(
        client,
        [api_session()],
        modifiedAtStartTime="2026-09-26T00:00:00Z",
        modifiedAtEndTime="2026-09-27T00:00:00Z",
    )
    assert response.status_code == 201
    assert response.json()["window"]["query"]["modifiedAtStartTime"] == "2026-09-26T00:00:00+00:00"


def test_landing_with_a_mixed_window_is_a_422_naming_the_rule(client):
    response = land(
        client,
        [api_session()],
        modifiedAtStartTime="2026-09-26T00:00:00Z",
        sessionStartedAtTime="2026-09-26T00:00:00Z",
    )
    assert response.status_code == 422
    assert response.json()["error"] == "invalid_window"


def test_landing_with_a_reversed_window_is_a_422(client):
    response = land(
        client, [api_session()], start="2026-09-27T00:00:00Z", end="2026-09-26T00:00:00Z"
    )
    assert response.status_code == 422
    assert response.json()["error"] == "invalid_window"


def test_landing_an_empty_batch_is_a_422(client):
    response = land(client, [])
    assert response.status_code == 422
    assert response.json()["error"] == "invalid_payload"


def test_landing_a_body_with_no_rows_at_all_is_a_422(client):
    response = client.post(f"{PREFIX}/extract", json={})
    assert response.status_code == 422
    assert response.json()["error"] == "invalid_payload"


def test_landing_an_unsupported_format_is_a_422(client):
    response = land(client, [api_session()], format="parquet")
    assert response.status_code == 422
    assert response.json()["error"] == "invalid_payload"


def test_landing_reports_the_watermark_and_the_refresh_caveat(client):
    body = land(client, [api_session()], end="2026-09-27T00:00:00Z").json()
    assert body["watermark"] == "2026-09-27T00:00:00+00:00"
    assert body["high_frequency"] is False
    assert body["sla_hours"] == 24


def test_a_second_extraction_inside_the_sla_is_flagged_as_high_frequency(client):
    land(client, [api_session()], end="2026-09-27T00:00:00Z")
    body = land(client, [api_session(userId="u-2")]).json()
    assert body["high_frequency"] is True


def test_landing_into_an_open_run_closes_it(client):
    opened = client.post(f"{PREFIX}/sweep").json()
    run_id = opened["run"]["id"]
    body = land(
        client, [api_session()], run_id=run_id, **opened["run"]["data"]["window"]["query"]
    ).json()
    assert body["run"]["data"]["state"] == "landed"
    assert body["run"]["data"]["counters"]["landed"] == 1


def test_a_run_opened_by_a_sweep_carries_the_window_the_job_was_told_to_pull(client):
    opened = client.post(f"{PREFIX}/sweep").json()
    assert opened["run"]["data"]["state"] == "requested"
    assert opened["run"]["data"]["window"]["query"] == opened["query"]


def test_landing_into_a_run_with_a_different_window_is_refused(client):
    """The lineage must not claim one window and the rows say another."""
    opened = client.post(f"{PREFIX}/sweep").json()
    response = land(
        client,
        [api_session()],
        run_id=opened["run"]["id"],
        start="2026-01-01T00:00:00Z",
        end="2026-01-02T00:00:00Z",
    )
    assert response.status_code == 422
    assert "land it into its own run" in response.json()["detail"]


def test_landing_into_a_run_that_does_not_exist_is_a_404(client):
    response = land(client, [api_session()], run_id="run_missing")
    assert response.status_code == 404


def test_landing_without_a_run_id_opens_and_closes_one(client):
    """Otherwise the watermark never advances and the next sweep repeats the day."""
    body = land(client, [api_session()], end="2026-09-27T00:00:00Z").json()
    assert body["run"]["data"]["run_kind"] == "extract"
    assert body["run"]["data"]["state"] == "landed"
    assert body["watermark"] == "2026-09-27T00:00:00+00:00"
    assert client.get(f"{PREFIX}/runs").json()["count"] == 1


def test_landing_the_room_inventory_is_its_own_route(client):
    response = land_rooms(client, [api_room(), api_room(id="dsr-002", name="Second")])
    assert response.status_code == 201
    assert response.json()["counters"]["created"] == 2
    assert client.get(f"{PREFIX}/rooms").json()["count"] == 2


def test_an_inventory_row_may_bind_itself_to_a_core_room(client):
    room_id = make_room(client)
    land_rooms(client, [api_room(bound_room_id=room_id)])
    body = client.get(f"{PREFIX}/rooms").json()
    assert body["bound"] == 1
    assert body["rooms"][0]["bound_room_id"] == room_id


def test_the_inventory_refuses_the_session_only_filter(client):
    response = land_rooms(client, [api_room()], sessionStartedAtTime="2026-09-26T00:00:00Z")
    assert response.status_code == 422
    assert response.json()["error"] == "invalid_window"


# --------------------------------------------------------------------------- #
# HTTP: the BI read surface
# --------------------------------------------------------------------------- #


def test_the_summary_keeps_sessions_apart_from_visitors(client):
    """One buyer, two tabs: the exact shape that makes a row count a visit count."""
    land(
        client,
        [
            api_session(userId="u-1", sessionEndedAt="2026-09-26T09:02:00Z"),
            api_session(
                userId="u-1",
                sessionStartedAt="2026-09-26T09:05:00Z",
                sessionEndedAt="2026-09-26T09:11:00Z",
            ),
        ],
    )
    summary = client.get(f"{PREFIX}/summary").json()["summary"]
    assert summary["sessions"] == 2
    assert summary["visitors"] == 1
    assert summary["dwell_seconds"] == 240


def test_the_sessions_list_resolves_the_room_join(client):
    land_rooms(client, [api_room()])
    land(client, [api_session()])
    row = sessions_in(client)["sessions"][0]
    assert row["room_name"] == "Seismic DSR 001"
    assert row["room_resolved"] is True


def test_the_sessions_list_withholds_sensitive_fields_by_default(client):
    land(client, [api_session()])
    row = sessions_in(client)["sessions"][0]
    assert "ip_address" not in row
    assert "engagement_user_email" not in row
    assert "geolocation_latitude" not in row
    assert sessions_in(client)["sensitive_fields_included"] is False


def test_the_sessions_list_hands_over_sensitive_fields_when_asked(client):
    land(client, [api_session()])
    row = sessions_in(client, include_sensitive=True)["sessions"][0]
    assert row["ip_address"] == "203.0.113.9"
    assert row["engagement_user_email"] == "buyer@example.com"


def test_the_sessions_list_scopes_to_a_core_room(client):
    room_id = make_room(client)
    other = make_room(client, name="Other")
    land_rooms(
        client, [api_room(bound_room_id=room_id), api_room(id="dsr-002", bound_room_id=other)]
    )
    land(client, [api_session(), api_session(digitalSalesRoomId="dsr-002", userId="u-2")])

    scoped = client.get(f"{PREFIX}/rooms/{room_id}/sessions").json()
    assert scoped["bound"] is True
    assert scoped["bound_dsr_rooms"] == ["dsr-001"]
    assert [row["digital_sales_room_id"] for row in scoped["sessions"]] == ["dsr-001"]


def test_a_room_with_nothing_bound_to_it_is_empty_not_an_error(client):
    room_id = make_room(client)
    body = client.get(f"{PREFIX}/rooms/{room_id}/sessions").json()
    assert body["bound"] is False
    assert body["count"] == 0


def test_an_unknown_core_room_is_a_404(client):
    response = client.get(f"{PREFIX}/rooms/room_missing/sessions")
    assert response.status_code == 404
    assert response.json()["error"] == "not_found"


def test_binding_a_room_later_makes_its_earlier_sessions_visible(client):
    room_id = make_room(client)
    land(client, [api_session()])
    assert client.get(f"{PREFIX}/rooms/{room_id}/sessions").json()["count"] == 0
    land_rooms(client, [api_room(bound_room_id=room_id)])
    assert client.get(f"{PREFIX}/rooms/{room_id}/sessions").json()["count"] == 1


def test_the_room_scoped_dwell_route_answers(client):
    room_id = make_room(client)
    land_rooms(client, [api_room(bound_room_id=room_id)])
    land(client, [api_session()])
    body = client.get(f"{PREFIX}/rooms/{room_id}/dwell").json()
    assert body["unit"] == "seconds"
    assert body["summary"]["dwell_seconds"] == 120
    assert body["by_viewer"][0]["group"] == "user:u-1"


def test_dwell_groups_by_room_or_by_viewer(client):
    land(client, [api_session()])
    by_room = client.get(f"{PREFIX}/dwell").json()
    by_viewer = client.get(f"{PREFIX}/dwell", params={"group_by": "viewer"}).json()
    assert by_room["group_by"] == "room"
    assert by_viewer["dwell"][0]["group"] == "user:u-1"


def test_dwell_says_a_total_is_tab_seconds(client):
    body = client.get(f"{PREFIX}/dwell").json()
    assert body["unit"] == "seconds"
    assert "tab-seconds" in body["grain"]


def test_an_unknown_dwell_grouping_is_refused(client):
    assert client.get(f"{PREFIX}/dwell", params={"group_by": "team"}).status_code == 422


def test_the_geography_rollup_reports_where_the_sessions_came_from(client):
    land(
        client,
        [
            api_session(),
            api_session(
                userId="u-2",
                country="US",
                state="WA",
                city="Seattle",
                geolocationLatitude=47.6062,
                geoLocationLongitude=-122.3321,
            ),
        ],
    )
    body = client.get(f"{PREFIX}/geography").json()
    assert body["countries"] == 2
    assert {entry["country"] for entry in body["geography"]} == {"AU", "US"}
    assert body["withheld"] == list(reporting.SENSITIVE_SESSION_FIELDS)


def test_the_per_user_engagement_table_carries_the_email(client):
    land(client, [api_session()])
    body = client.get(f"{PREFIX}/viewers").json()
    assert body["viewers"][0]["engagement_user_email"] == "buyer@example.com"
    assert body["viewers"][0]["sessions"] == 1


def test_per_user_engagement_can_be_filtered_to_buyers_or_sellers(client):
    land(client, [api_session(), api_session(userId="u-9", isEngagementUserInternal=True)])
    buyers = client.get(f"{PREFIX}/viewers", params={"internal": "false"}).json()
    sellers = client.get(f"{PREFIX}/viewers", params={"internal": "true"}).json()
    assert [row["viewer_key"] for row in buyers["viewers"]] == ["user:u-1"]
    assert [row["viewer_key"] for row in sellers["viewers"]] == ["user:u-9"]


def test_the_runs_are_listed_newest_first_with_their_lineage(client):
    land(client, [api_session()], end="2026-09-27T00:00:00Z")
    runs = client.get(f"{PREFIX}/runs").json()["runs"]
    assert len(runs) == 1
    assert runs[0]["data"]["state"] == "landed"
    assert runs[0]["data"]["window"]["kind"] == "modified"
    assert runs[0]["data"]["counters"]["created"] == 1


def test_one_run_can_be_fetched_and_an_unknown_one_is_a_404(client):
    land(client, [api_session()])
    run_id = client.get(f"{PREFIX}/runs").json()["runs"][0]["id"]
    assert client.get(f"{PREFIX}/runs/{run_id}").status_code == 200
    assert client.get(f"{PREFIX}/runs/run_missing").status_code == 404


# --------------------------------------------------------------------------- #
# HTTP: the two documented output formats
# --------------------------------------------------------------------------- #


def test_the_csv_accept_header_returns_a_flat_file(client):
    land(client, [api_session()])
    response = client.get(f"{PREFIX}/export", headers={"Accept": "text/csv"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment" in response.headers["content-disposition"]


def test_the_csv_header_is_the_documented_dictionary_in_order(client):
    land(client, [api_session()])
    response = client.get(f"{PREFIX}/export", headers={"Accept": "text/csv"})
    assert response.text.splitlines()[0] == ",".join(reporting.SESSION_FIELD_NAMES)


def test_a_csv_row_holds_the_landed_values(client):
    land(client, [api_session()])
    response = client.get(f"{PREFIX}/export", headers={"Accept": "text/csv"})
    row = response.text.splitlines()[1].split(",")
    assert row[reporting.SESSION_FIELD_NAMES.index("room_duration_seconds")] == "120"
    assert row[reporting.SESSION_FIELD_NAMES.index("is_engagement_user_internal")] == "false"


def test_the_format_query_parameter_is_the_browser_fallback(client):
    land(client, [api_session()])
    response = client.get(f"{PREFIX}/export", params={"format": "csv"})
    assert response.headers["content-type"].startswith("text/csv")


def test_the_json_accept_header_returns_the_envelope(client):
    land(client, [api_session()])
    response = client.get(f"{PREFIX}/export", headers={"Accept": "application/json"})
    assert response.json()["format"] == "application/json"
    assert response.json()["count"] == 1


def test_the_export_defaults_to_json_for_a_browser(client):
    land(client, [api_session()])
    assert client.get(f"{PREFIX}/export").json()["format"] == "application/json"


def test_an_accept_this_workflow_cannot_produce_is_refused(client):
    response = client.get(f"{PREFIX}/export", headers={"Accept": "application/xml"})
    assert response.status_code == 422
    assert response.json()["error"] == "invalid_payload"


def test_an_unknown_format_parameter_is_refused(client):
    response = client.get(f"{PREFIX}/export", params={"format": "parquet"})
    assert response.status_code == 422


# --------------------------------------------------------------------------- #
# The audit source rule
# --------------------------------------------------------------------------- #


def _served_routes(client):
    return [
        route
        for feature in client.get("/api/features").json()["features"]
        for route in feature["routes"]
    ]


def _names_a_served_route(source, routes):
    """True when a source names a mounted route, comparing segment by segment."""
    method, _, path = source.partition(" ")
    actual = [segment for segment in path.split("/") if segment]
    for route in routes:
        if method not in route["methods"]:
            continue
        template = [segment for segment in route["path"].split("/") if segment]
        if len(template) != len(actual):
            continue
        if all(
            expected.startswith("{") or expected == found
            for expected, found in zip(template, actual, strict=False)
        ):
            return True
    return False


def test_every_write_audit_row_names_a_route_the_app_serves(client):
    """The port brief's central guarantee, checked against the live route table.

    A hard-coded path in a domain function is the defect this names: the audit row
    would name a route the app had stopped serving, and nothing else would notice.
    """
    room_id = make_room(client)
    opened = client.post(f"{PREFIX}/sweep").json()
    land_rooms(client, [api_room(bound_room_id=room_id)])
    land(client, [api_session()], run_id=opened["run"]["id"])
    land(client, [api_session(userId="u-2", roomDurationSeconds=600)])

    sources = {entry["source"] for entry in audit(client, limit=200) if entry["source"]}
    ours = {source for source in sources if source.split(" ")[1].startswith(PREFIX)}
    assert ours, f"no wf-020 write was audited at all; saw {sorted(sources)}"
    routes = _served_routes(client)
    for source in sorted(ours):
        assert _names_a_served_route(source, routes), (
            f"audit row names {source!r}, which is not a route this app serves"
        )


def test_the_sources_are_exactly_the_routes_that_served_them(client):
    room_id = make_room(client)
    land_rooms(client, [api_room(bound_room_id=room_id)])
    land(client, [api_session()])

    ours = {
        entry["source"]
        for entry in audit(client, limit=200)
        if entry["source"] and entry["source"].split(" ")[1].startswith(PREFIX)
    }
    assert ours == {f"POST {PREFIX}/rooms", f"POST {PREFIX}/extract"}


def test_a_sweep_names_the_sweep_route(client):
    client.post(f"{PREFIX}/sweep")
    sources = [entry["source"] for entry in audit(client, collection=reporting.RUNS, limit=50)]
    assert f"POST {PREFIX}/sweep" in sources


def test_landing_into_a_run_names_the_landing_route_not_the_sweep(client):
    opened = client.post(f"{PREFIX}/sweep").json()
    land(
        client,
        [api_session()],
        run_id=opened["run"]["id"],
        **opened["run"]["data"]["window"]["query"],
    )
    sources = {entry["source"] for entry in audit(client, collection=reporting.RUNS, limit=50)}
    assert sources == {f"POST {PREFIX}/sweep", f"POST {PREFIX}/extract"}


def test_a_second_landing_of_the_same_row_names_the_same_route(client):
    land(client, [api_session()])
    land(client, [api_session(roomDurationSeconds=300)])
    by_action = {entry["action"]: entry["source"] for entry in audit(client, limit=50)}
    assert by_action["insert"] == f"POST {PREFIX}/extract"
    assert by_action["update"] == f"POST {PREFIX}/extract"


def test_a_retracted_row_that_came_back_names_the_landing_route(client):
    land(client, [api_session()])
    record = client.get(f"{PREFIX}/sessions").json()["sessions"][0]["id"]
    client.delete(f"/api/records/{reporting.SESSIONS}/{record}")
    land(client, [api_session(roomDurationSeconds=999)])

    actions = {entry["action"] for entry in audit(client, limit=50)}
    assert "restore" in actions
    restored = [entry for entry in audit(client, limit=50) if entry["action"] == "restore"][0]
    assert restored["source"] == f"POST {PREFIX}/extract"


def test_no_write_escapes_the_audited_store(client):
    """Every landed row is a record, so the audit log covers all of it."""
    land(client, [api_session(), api_session(userId="u-2")])
    entries = audit(client, collection=reporting.SESSIONS, limit=50)
    assert len(entries) == 2
    assert {entry["action"] for entry in entries} == {"insert"}
    assert all(entry["source"] == f"POST {PREFIX}/extract" for entry in entries)


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #


@pytest.fixture()
def seeded(tmp_path):
    """Run the feature's own seed and hand back what it wrote."""
    module = load_feature(MODULE)
    room_ids = []
    with AuditedDatabase(tmp_path / "seed.db", mirror_dir=tmp_path / "audit") as db:
        for name in ("Northwind", "Contoso", "Fabrikam", "Adventure"):
            room_ids.append((db.create("room", {"name": name}, source="seed")["id"], name))
        summary = module.seed(
            db, {"room_ids": room_ids, "now": NOW, "rng": random.Random("wf020-test")}
        )
        return {
            "summary": summary,
            "rooms": db.list(reporting.ROOMS, limit=100),
            "sessions": db.list(reporting.SESSIONS, limit=100),
            "runs": db.list(reporting.RUNS, limit=100),
            "audit": db.audit(limit=500),
            "room_ids": room_ids,
        }


def test_the_seed_reports_what_it_added(seeded):
    assert "viewing sessions" in seeded["summary"]
    assert "inventory rooms" in seeded["summary"]


def test_the_seed_lands_sessions_in_several_tabs_per_buyer(seeded):
    """The demo is tab-shaped, so a session count is visibly not a visit count."""
    summary = reporting.summarise([record["data"] for record in seeded["sessions"]])
    assert summary["sessions"] > summary["visitors"]


def test_the_seed_contains_the_api_documented_one_second_session(seeded):
    assert 1 in [record["data"]["room_duration_seconds"] for record in seeded["sessions"]]


def test_the_seed_contains_a_long_session_a_tab_left_open(seeded):
    assert max(record["data"]["room_duration_seconds"] for record in seeded["sessions"]) > 3600


def test_the_seed_seeds_an_internal_viewer_so_the_split_is_not_vacuous(seeded):
    internal = [
        record["data"]
        for record in seeded["sessions"]
        if record["data"]["is_engagement_user_internal"] is True
    ]
    assert internal


def test_the_seed_seeds_an_unattributed_session(seeded):
    flagged = [
        r["data"] for r in seeded["sessions"] if "unattributed" in r["data"]["quality_flags"]
    ]
    assert flagged
    assert flagged[0]["viewer_key"] == "anonymous"


def test_the_seed_seeds_a_session_whose_room_is_not_in_the_inventory(seeded):
    unresolved = [r for r in seeded["sessions"] if r["data"]["room_unresolved"]]
    assert unresolved


def test_the_seed_seeds_a_room_with_no_sessions_at_all(seeded):
    seen = {record["data"]["digital_sales_room_id"] for record in seeded["sessions"]}
    quiet = [
        record for record in seeded["rooms"] if record["data"]["digital_sales_room_id"] not in seen
    ]
    assert quiet


def test_the_seed_leaves_some_rooms_unbound_so_the_binding_is_visible(seeded):
    assert any("bound_room_id" not in record["data"] for record in seeded["rooms"])
    assert any("bound_room_id" in record["data"] for record in seeded["rooms"])


def test_the_seed_seeds_a_run_that_never_landed(seeded):
    requested = [r for r in seeded["runs"] if r["data"]["state"] == "requested"]
    assert len(requested) == 1


def test_the_seed_shows_the_merge_doing_both_of_its_jobs(seeded):
    landed = [r for r in seeded["runs"] if r["data"]["run_kind"] == "extract"]
    assert any(r["data"]["counters"].get("created", 0) for r in landed)
    assert any(r["data"]["counters"].get("updated", 0) for r in landed)
    assert any(r["data"]["counters"].get("unchanged", 0) for r in landed)


def test_the_seed_seeds_an_undocumented_field_which_is_kept(seeded):
    extras = [record["data"]["extra"] for record in seeded["sessions"] if "extra" in record["data"]]
    assert extras
    assert "documentId" in extras[0]


def test_the_seed_seeds_a_room_whose_user_modified_differs_from_modified(seeded):
    assert any("user_modified" in record["data"]["quality_flags"] for record in seeded["rooms"])


def test_every_seeded_write_is_audited_under_the_seeder(seeded):
    assert seeded["audit"]
    assert {entry["source"] for entry in seeded["audit"]} == {"seed"}


def test_the_seed_is_reproducible(tmp_path):
    module = load_feature(MODULE)
    outputs = []
    for name in ("a.db", "b.db"):
        with AuditedDatabase(tmp_path / name, mirror_dir=tmp_path / "audit") as db:
            room_ids = [(db.create("room", {"name": "R"}, source="seed")["id"], "R")]
            outputs.append(
                module.seed(
                    db, {"room_ids": room_ids, "now": NOW, "rng": random.Random("wf020-test")}
                )
            )
    assert outputs[0] == outputs[1]


def test_the_seed_copes_with_no_demo_rooms():
    """A feature the seeder cannot attach rows to must say so, not raise."""
    module = load_feature(MODULE)
    with AuditedDatabase(":memory:") as db:
        assert module.seed(db, {"room_ids": [], "now": NOW, "rng": random.Random("x")}) is None
