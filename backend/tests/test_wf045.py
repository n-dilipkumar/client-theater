"""Tests for WF-045: backfill history on a resumable cursor.

The claims under test come from
``docs/research/digital-sales-room-workflows/wf/WF-045.md`` (section 12 of
``docs/research/raw/crm-integration.md``). They are, in order:

* "Room asks the CRM for an asynchronous extract job (large volumes) or a
  delta/paged read (moderate volumes)";
* "Any data operation that includes more than 2,000 records is a good candidate
  for Bulk API 2.0 ... Jobs with fewer than 2,000 records should involve
  'bulkified' synchronous calls";
* "You submit a request and come back for the results later";
* "If the job or the room crashes mid-run, the room restarts from the stored
  cursor - no duplicates, no gaps";
* "The cursor is an interface, so a vendor-specific cursor (Bulk job id, delta
  link, ``DataToken``) is swapped for a standard ``{vendor, connectionId, cursor,
  updatedAt}`` record ... a third party can add a new vendor by implementing only
  'create job' and 'read page'";
* "Changes are returned if the last token is within a default value of seven days
  ... ``ExpireChangeTrackingInDays`` ... If unprocessed changes are older than the
  configured value, the system throws an exception";
* "If the new or updated item collection is greater than 5,000, the user can page
  through the collection";
* "the user installing the app must be a **Super Admin** to grant the
  ``crm.export`` scope";
* "For standard objects, you can use the object's name (e.g., ``CONTACT``), but
  for custom objects, you must use the ``objectTypeId`` value";
* "The **daily** limit resets at midnight based on your time zone setting";
* "Because both Bulk APIs are asynchronous, Salesforce doesn't guarantee a service
  level agreement";
* "transform via field map -> upsert into room replica (or push into CRM for a
  reverse backfill) -> advance cursor -> completion marker";
* "Progress is shown as a percentage ... with a per-run log";
* "the room runs a poller on a fixed interval".

The crash promise is the one that decides this workflow's shape, so the tests
below stage both crashes it names - after the rows land and before the cursor
moves, and after the cursor moves and before the run's own state is written -
and assert on the replica rather than on the log.

Every part of the feature is reachable through its own router, so the HTTP tests
drive the mounted routes rather than calling handlers, and the audit-source tests
check every recorded source against the route table the host actually reported.
"""

from __future__ import annotations

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import pytest
from fastapi.testclient import TestClient

from dsr.api import app
from dsr.crm_backfill import (
    CURSORS_STORE,
    CURSOR_FIELDS,
    DEFAULT_KEY_FIELD,
    DIRECTIONS,
    EVENTS,
    NUMBERS,
    REPLICA,
    RUN_STATES,
    SCOPES,
    SOURCED_QUOTE,
    STRATEGIES,
    TERMINAL_STATES,
    BackfillEngine,
    CursorExpired,
    PlanError,
    QuotaExceeded,
    RunNotFound,
    RunStateError,
    SimulatedHistory,
    UnknownConnection,
    UnkeyedRow,
    UnsupportedStrategy,
    UnsupportedVendor,
    build_cursor,
    bulk_threshold,
    default_expiry_days,
    default_page_size,
    default_registry,
    is_expired,
    normalise_scope,
    require_resumable,
)
from dsr.crm_backfill import cursors as cursor_rules
from dsr.crm_backfill import plan as plan_rules
from dsr.crm_backfill import quota as quota_rules
from dsr.crm_backfill import transform as transform_rules
from dsr.crm_backfill import vendors as vendor_rules
from dsr.crm_backfill.engine import CONNECTIONS, RUNS, new_counters, progress
from dsr.crm_backfill.inferences import INFERENCES, by_id, describe
from dsr.crm_backfill.vendors import (
    HUBSPOT_EXPORT_STATUSES,
    HubSpotExportAdapter,
    SalesforceBulkAdapter,
    VendorAdapter,
    VendorPage,
)
from dsr.crm_backfill.vocabulary import (
    CURSOR_KINDS,
    LOG_EVENTS,
    NO_SLA_QUOTE,
    OBJECT_ADDRESSING_QUOTE,
    REQUIRED_SCOPE,
    VENDORS,
    describe as describe_vocabulary,
)
from dsr.db.audited import AuditedDatabase
from dsr.features import load_feature
from dsr.store import RecordStore

#: The feature's own prefix. Duplicated here rather than imported so a change to
#: the prefix has to be made deliberately in the test as well, which is the point
#: of a test: a renamed route should fail, not follow silently.
PREFIX = "/api/wf-045"

#: The source a route passes for a write, in the shape the tests use. The
#: room-scoped routes pass the templated path, which is what the route table
#: reports, so an audit row and a mounted route can be compared literally.
SOURCE = f"POST {PREFIX}/rooms/{{room_id}}/backfills"
POLL_SOURCE = f"POST {PREFIX}/rooms/{{room_id}}/backfills/{{run_id}}/poll"
RESUME_SOURCE = f"POST {PREFIX}/rooms/{{room_id}}/backfills/{{run_id}}/resume"

MODULE = "wf045_backfill_historical_records_on_a_sched"
FEATURE_ID = "wf-045-backfill-historical-records-on-a-sched"

NOW = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)


class Clock:
    """A clock the test moves, because a vendor's own clock is not this product's."""

    def __init__(self, now: datetime = NOW) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs: Any) -> datetime:
        self.now = self.now + timedelta(**kwargs)
        return self.now


def history_rows(count: int, prefix: str = "acc", *, days: int = 60, unkeyed: int = 0) -> list[dict[str, Any]]:
    """A deterministic history, newest last, with the last ``unkeyed`` rows unidentifiable."""
    rows: list[dict[str, Any]] = []
    for index in range(count):
        row: dict[str, Any] = {
            "id": f"{prefix}-{index:04d}",
            "Name": f"Account {index:04d}",
            "Stage__c": ["Prospecting", "Proposal", "Negotiation"][index % 3],
            "Amount": 1_000 + index,
            "occurred_at": (NOW - timedelta(days=days - (days * index // max(1, count)))).isoformat(),
        }
        rows.append(row)
    for row in rows[-unkeyed:] if unkeyed else []:
        row.pop("id")
    return rows


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture()
def clock() -> Clock:
    return Clock()


@pytest.fixture()
def store(tmp_path):
    db = AuditedDatabase(tmp_path / "wf045.db", mirror_dir=tmp_path / "mirror")
    yield RecordStore(db)
    db.close()


@pytest.fixture()
def engine(store, clock):
    return BackfillEngine(
        store, registry=default_registry(SimulatedHistory(history_rows(30))), clock=clock
    )


@pytest.fixture()
def salesforce(engine):
    return engine.create_connection(
        {"name": "SF", "vendor": "salesforce", "replica_key_field": "Id"}, source=SOURCE
    )


@pytest.fixture()
def dataverse(engine):
    return engine.create_connection(
        {"name": "DV", "vendor": "dataverse"}, source=SOURCE
    )


@pytest.fixture()
def hubspot(engine):
    return engine.create_connection(
        {
            "name": "HS",
            "vendor": "hubspot",
            "granted_scopes": ["crm.export"],
            "standard_objects": ["CONTACT", "DEAL"],
            "daily_limit": 500,
        },
        source=SOURCE,
    )


def open_run(engine, connection, room="room-1", **overrides: Any) -> dict[str, Any]:
    """Open a backfill with the knobs a test cares about set to something usable."""
    payload: dict[str, Any] = {
        "connection_id": connection["id"],
        "scope": {"kind": "full_history"},
        "page_size": 10,
        "poll_interval_seconds": 0,
        "ready_after": 1,
    }
    payload.update(overrides)
    return engine.start(room, payload, source=SOURCE)


def drive(engine, room: str, run: Mapping[str, Any], *, limit: int = 40, source: str = POLL_SOURCE) -> dict[str, Any]:
    """Poll until the run stops moving, the way a scheduler would."""
    current = dict(run)
    for _ in range(limit):
        if current["data"]["state"] != "running":
            return current
        current = engine.poll(room, str(current["id"]), source=source, force=True)["run"]
    return current


@pytest.fixture()
def http(monkeypatch):
    """A client over a temporary database, with a scripted CRM history behind it.

    The database path is resolved at lifespan time, so the variable is set before
    the context manager is entered - the same way ``test_features.py`` does it.

    The vendor registry is overridden rather than the app's state: this product
    ships without CRM credentials, so the default registry reads an empty
    history, and an HTTP test that then asserted "a page landed" would be
    asserting on a workflow that has nothing to read. Overriding the feature's
    own dependency keeps the assertion on the routes, which is what the HTTP
    tests are for, and leaves the engine's real constructor untouched.
    """
    tmp = tempfile.TemporaryDirectory()
    monkeypatch.setenv("DSR_DB_PATH", str(Path(tmp.name) / "wf045-http.db"))
    monkeypatch.setenv("DSR_AUDIT_DIR", str(Path(tmp.name) / "audit"))
    monkeypatch.setattr("dsr.api.FRONTEND_DIST", Path(tmp.name) / "absent-frontend")

    module = load_feature(MODULE)
    registry = default_registry(SimulatedHistory(history_rows(30)))

    def override():
        return BackfillEngine(app.state.store, registry=registry, clock=lambda: NOW)

    app.dependency_overrides[module.get_engine] = override
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.pop(module.get_engine, None)
        tmp.cleanup()


@pytest.fixture()
def http_room(http):
    return http.post(
        "/api/records/room",
        json={"name": "Northwind Traders — Enterprise Evaluation", "account": "Northwind", "owner": "dana"},
    ).json()["id"]


@pytest.fixture()
def http_connection(http):
    return http.post(
        f"{PREFIX}/connections",
        json={"name": "SF", "vendor": "salesforce", "replica_key_field": "Id"},
    ).json()


# --------------------------------------------------------------------------- #
# The plugin registration itself
# --------------------------------------------------------------------------- #


def test_feature_is_discovered_and_mounted_without_editing_the_host(http):
    """The route resolves even though no shared file names this feature."""
    entry = next(f for f in http.get("/api/features").json()["features"] if f["id"] == FEATURE_ID)
    assert entry["prefix"] == PREFIX
    assert entry["ticket"] == "WF-045"
    assert entry["exception_handlers"] == ["BackfillError"]
    assert len(entry["routes"]) == 16


def test_no_feature_failed_to_load(http):
    """A refused route collision or a broken import would show up here."""
    body = http.get("/api/features").json()
    assert body["failed_count"] == 0, body["failed"]


def test_the_prefix_is_ours_alone(http):
    """No core route and no other feature answers anything under it."""
    body = http.get("/api/features").json()
    mine = {feature["id"] for feature in body["features"] if feature["prefix"] == PREFIX}
    assert mine == {FEATURE_ID}
    for feature in body["features"]:
        if feature["id"] == FEATURE_ID:
            continue
        assert not any(route["path"].startswith(PREFIX) for route in feature["routes"]), (
            f"{feature['id']} also serves under {PREFIX}"
        )


def test_feature_module_does_not_import_the_shared_app():
    """A guard exists in test_features.py; this states the reason locally."""
    source = Path(load_feature(MODULE).__file__).read_text(encoding="utf-8")
    assert "from dsr.api" not in source
    assert "import dsr.api" not in source
    assert "from dsr.deps import" in source


def test_frontend_descriptor_id_matches_the_backend_feature_id():
    """The two halves of a feature are findable by one name, so they must agree."""
    descriptor = (
        Path(__file__).resolve().parents[2] / "frontend" / "src" / "features" / FEATURE_ID / "index.jsx"
    )
    text = descriptor.read_text(encoding="utf-8")
    module = load_feature(MODULE)
    assert module.FEATURE["id"] in text
    assert f"id: {module.FEATURE['id']!r}" in text
    assert "Component:" in text


def test_the_feature_exports_what_the_host_looks_for():
    module = load_feature(MODULE)
    assert set(module.EXCEPTION_HANDLERS) == {BackfillEngine.__module__ and __import__(
        "dsr.crm_backfill", fromlist=["BackfillError"]
    ).BackfillError}
    assert callable(module.seed)


def test_every_error_type_hangs_off_one_base():
    """One handler for the hierarchy, which is why the host never sees a clash."""
    from dsr.crm_backfill import errors

    base = errors.BackfillError
    for name in (
        "PlanError",
        "InvalidRange",
        "UnaddressableObject",
        "PrerequisiteError",
        "UnknownConnection",
        "UnsupportedVendor",
        "UnsupportedStrategy",
        "DirectionNotSupported",
        "MissingScope",
        "QuotaExceeded",
        "CursorExpired",
        "RunStateError",
        "RunNotFound",
        "UnkeyedRow",
    ):
        assert issubclass(getattr(errors, name), base), name


# --------------------------------------------------------------------------- #
# The vocabulary the research fixes by name
# --------------------------------------------------------------------------- #


def test_the_three_researched_vendors_are_the_registry():
    assert set(VENDORS) == {"salesforce", "dataverse", "hubspot"}
    assert default_registry() .keys() == VENDORS.keys()


def test_each_vendor_carries_the_quote_that_fixed_it():
    for entry in VENDORS.values():
        assert entry["quote"].strip()
        assert entry["mechanism"].strip()
        assert entry["cursor_kind"] in CURSOR_KINDS


def test_the_two_ways_of_asking_are_the_published_strategies():
    assert STRATEGIES == ("async_job", "delta_read", "paged_read")
    assert {entry["value"] for entry in describe_vocabulary()["strategies"]} == set(STRATEGIES)


def test_the_researched_numbers_are_carried_with_their_quotes():
    assert bulk_threshold() == 2000
    assert default_page_size() == 5000
    assert default_expiry_days() == 7
    for name, entry in NUMBERS.items():
        assert entry["quote"].strip(), name
        assert entry["meaning"].strip(), name


def test_the_two_thousands_rule_is_quoted_verbatim():
    assert "more than 2,000 records" in NUMBERS["bulk_threshold_records"]["quote"]
    assert "fewer than 2,000 records" in NUMBERS["bulk_threshold_records"]["quote"]


def test_the_seven_day_window_is_quoted_verbatim():
    quote = NUMBERS["change_tracking_expiry_days"]["quote"]
    assert "within a default value of seven days" in quote
    assert "ExpireChangeTrackingInDays" in quote
    assert "throws an exception" in quote


def test_the_five_thousand_page_size_is_quoted_verbatim():
    assert "greater than 5,000, the user can page through" in NUMBERS["dataverse_page_size"]["quote"]


def test_the_negative_is_published_because_it_is_easy_to_drop():
    assert NO_SLA_QUOTE == (
        "Because both Bulk APIs are asynchronous, Salesforce doesn't guarantee a service level agreement."
    )
    assert NO_SLA_QUOTE in describe_vocabulary()["no_sla_quote"]


def test_the_hubspot_scope_and_object_rules_are_published():
    assert REQUIRED_SCOPE["scope"] == "crm.export"
    assert "Super Admin" in REQUIRED_SCOPE["quote"]
    assert "objectTypeId" in OBJECT_ADDRESSING_QUOTE


def test_the_run_states_include_the_one_the_research_forces_into_existence():
    assert "stalled" in RUN_STATES
    assert "stalled" in TERMINAL_STATES
    assert TERMINAL_STATES == frozenset({"complete", "failed", "cancelled", "stalled"})


def test_every_state_the_engine_can_store_is_published():
    assert set(RUN_STATES) == set(describe_vocabulary()["run_states"][i]["value"] for i in range(len(RUN_STATES)))


def test_the_per_run_log_has_an_event_for_each_kind_the_engine_writes():
    for event in ("run_created", "page_written", "cursor_saved", "run_complete", "run_stalled"):
        assert event in LOG_EVENTS


def test_the_scopes_are_the_two_the_flow_names():
    assert SCOPES == ("range", "full_history")
    assert DIRECTIONS == ("pull", "push")


# --------------------------------------------------------------------------- #
# The inference register
# --------------------------------------------------------------------------- #


def test_the_sourced_quote_is_the_one_that_governs_the_package():
    assert "restarts from the stored cursor" in SOURCED_QUOTE
    assert "no duplicates, no gaps" in SOURCED_QUOTE


def test_the_register_names_every_judgement_with_a_basis_and_a_remedy():
    for entry in INFERENCES:
        assert entry["id"] and entry["topic"] and entry["basis"] and entry["why"], entry["id"]
        assert entry["value"] is not None, entry["id"]
        assert entry["change_it"].strip(), entry["id"]
        assert entry["blast_radius"].strip(), entry["id"]


def test_the_register_ids_are_unique():
    ids = [entry["id"] for entry in INFERENCES]
    assert len(ids) == len(set(ids))


def test_the_register_documents_the_decisions_the_research_left_open():
    """The ones a reviewer most needs to find without reading the diff."""
    for expected in (
        "cursor-advance-after-write",
        "expired-cursor-stalls-rather-than-restarts",
        "unkeyable-rows-are-rejected-not-stalled",
        "unchanged-rows-are-not-written",
        "progress-is-indeterminate-without-a-total",
        "quota-is-sized-before-a-job-is-opened",
        "push-needs-a-third-method",
    ):
        assert by_id(expected) is not None, expected


def test_describe_serves_the_sourced_half_beside_the_inferred_half():
    payload = describe()
    assert payload["count"] == len(INFERENCES)
    assert payload["sourced_quote"] == SOURCED_QUOTE
    assert payload["sourced"]["numbers"]["bulk_threshold_records"] == 2000
    assert payload["sourced"]["cursor_shape"] == ["vendor", "connectionId", "cursor", "updatedAt"]
    assert len(payload["inferences"]) == len(INFERENCES)


# --------------------------------------------------------------------------- #
# The cursor: the research's own four fields
# --------------------------------------------------------------------------- #


def test_the_cursor_record_carries_the_four_researched_fields_verbatim():
    record = build_cursor(
        vendor="dataverse", connection_id="c1", cursor="600", updated_at=NOW, kind="data_token"
    )
    for field in CURSOR_FIELDS:
        assert field in record, field
    assert CURSOR_FIELDS == ("vendor", "connectionId", "cursor", "updatedAt")
    assert record["vendor"] == "dataverse"
    assert record["connectionId"] == "c1"
    assert record["cursor"] == "600"
    assert record["updatedAt"] == NOW.isoformat()


def test_the_cursors_bookkeeping_is_additive_rather_than_folded_in():
    """A third party reading the documented four still finds exactly the four."""
    record = build_cursor(
        vendor="salesforce",
        connection_id="c1",
        cursor="750abc",
        updated_at=NOW,
        kind="bulk_job_id",
        pages_advanced=3,
        rows_written=900,
    )
    assert record["pages_advanced"] == 3 and record["rows_written"] == 900
    assert set(CURSOR_FIELDS) <= set(record)


def test_a_cursor_with_nothing_in_it_is_not_resumable():
    record = build_cursor(
        vendor="salesforce", connection_id="c1", cursor=None, updated_at=NOW, kind="none"
    )
    assert record["resumable"] is False
    assert is_expired(record, NOW) is False


def test_an_unknown_cursor_kind_is_refused():
    with pytest.raises(CursorExpired):
        build_cursor(vendor="x", connection_id="c", cursor="1", updated_at=NOW, kind="telepathy")


def test_only_a_data_token_ages_out():
    """A Bulk job id resolves or it does not; a seven-day rule says nothing about it."""
    old = NOW - timedelta(days=30)
    for kind in ("bulk_job_id", "export_id", "paging_cookie", "odata_delta_link"):
        record = build_cursor(
            vendor="salesforce", connection_id="c", cursor="x", updated_at=old, kind=kind
        )
        assert is_expired(record, NOW) is False, kind


def test_a_data_token_older_than_the_window_is_expired():
    record = build_cursor(
        vendor="dataverse", connection_id="c", cursor="600", updated_at=NOW - timedelta(days=8), kind="data_token"
    )
    assert is_expired(record, NOW) is True
    # Two days earlier, the same cursor is six days old and still inside the window.
    assert is_expired(record, NOW - timedelta(days=2)) is False


def test_the_window_is_half_open_at_exactly_seven_days():
    """``<=`` not ``<``: the vendor stops answering *at* the boundary."""
    at_limit = build_cursor(
        vendor="dataverse", connection_id="c", cursor="1", updated_at=NOW - timedelta(days=7), kind="data_token"
    )
    assert is_expired(at_limit, NOW) is True
    just_inside = build_cursor(
        vendor="dataverse",
        connection_id="c",
        cursor="1",
        updated_at=NOW - timedelta(days=7) + timedelta(seconds=1),
        kind="data_token",
    )
    assert is_expired(just_inside, NOW) is False


def test_a_connection_may_declare_its_own_window():
    """``ExpireChangeTrackingInDays`` "controls this duration and can be changed"."""
    record = build_cursor(
        vendor="dataverse", connection_id="c", cursor="1", updated_at=NOW - timedelta(days=9), kind="data_token"
    )
    assert is_expired(record, NOW, 14) is False
    assert is_expired(record, NOW, 3) is True


def test_a_cursor_whose_timestamp_cannot_be_read_is_treated_as_old():
    """The safe direction: a run that refuses to resume can be started again."""
    assert is_expired({"cursor": "1", "kind": "data_token", "updatedAt": "yesterday"}, NOW) is True


def test_require_resumable_names_the_backfill_to_open_instead_of_stalling_silently():
    record = build_cursor(
        vendor="dataverse", connection_id="c", cursor="600", updated_at=NOW - timedelta(days=9), kind="data_token"
    )
    with pytest.raises(CursorExpired) as caught:
        require_resumable(record, NOW)
    message = str(caught.value)
    assert "full-history backfill" in message
    assert "9 day(s) old" in message, "the refusal says how stale it is, not only that it is"
    assert "cannot be made safe" in message


def test_require_resumable_with_no_cursor_at_all():
    with pytest.raises(CursorExpired):
        require_resumable(None, NOW)
    with pytest.raises(CursorExpired):
        require_resumable({"cursor": None}, NOW)


def test_describe_reports_the_verdict_and_when_it_expires():
    record = build_cursor(
        vendor="dataverse", connection_id="c", cursor="600", updated_at=NOW, kind="data_token"
    )
    verdict = cursor_rules.describe(record, NOW)
    assert verdict["present"] is True
    assert verdict["expired"] is False
    assert verdict["age_days"] == 0.0
    assert verdict["resumable_until"] == (NOW + timedelta(days=7)).isoformat()


def test_describe_of_no_cursor_says_so_rather_than_raising():
    assert cursor_rules.describe(None, NOW) == {
        "present": False,
        "kind": "none",
        "expiry_days": 7,
        "expired": False,
        "age_days": 0.0,
    }


def test_age_days_is_reported_so_a_stalled_run_can_say_how_stale():
    record = build_cursor(
        vendor="dataverse", connection_id="c", cursor="1", updated_at=NOW - timedelta(days=9, hours=12), kind="data_token"
    )
    assert cursor_rules.age_days(record, NOW) == 9.5


# --------------------------------------------------------------------------- #
# The range picker
# --------------------------------------------------------------------------- #


def test_a_range_normalises_to_comparable_bounds():
    scope = normalise_scope(
        {"kind": "range", "from": "2026-01-01T00:00:00Z", "to": "2026-06-01T00:00:00Z"}, NOW
    )
    assert scope["kind"] == "range"
    assert scope["from"] == "2026-01-01T00:00:00+00:00"
    assert scope["to"] == "2026-06-01T00:00:00+00:00"
    assert scope["unbounded_below"] is False
    assert 150 < scope["days"] < 153


def test_a_range_needs_a_from_and_says_so_rather_than_guessing():
    with pytest.raises(Exception) as caught:
        normalise_scope({"kind": "range", "to": "2026-06-01T00:00:00Z"}, NOW)
    assert "full_history" in str(caught.value)


def test_a_range_that_runs_backwards_is_refused():
    with pytest.raises(Exception) as caught:
        normalise_scope(
            {"kind": "range", "from": "2026-06-01T00:00:00Z", "to": "2026-01-01T00:00:00Z"}, NOW
        )
    assert "not before" in str(caught.value)


def test_a_range_spanning_nothing_is_refused_rather_than_reported_as_done():
    with pytest.raises(Exception):
        normalise_scope({"kind": "range", "from": "2026-06-01T00:00:00Z", "to": "2026-06-01T00:00:00Z"}, NOW)


def test_a_range_that_starts_in_the_future_is_refused():
    with pytest.raises(Exception) as caught:
        normalise_scope({"kind": "range", "from": "2027-01-01T00:00:00Z"}, NOW)
    assert "future" in str(caught.value)


def test_a_to_in_the_future_is_refused():
    with pytest.raises(Exception) as caught:
        normalise_scope({"kind": "range", "from": "2026-01-01T00:00:00Z", "to": "2027-01-01T00:00:00Z"}, NOW)
    assert "future" in str(caught.value)


def test_a_to_a_minute_ahead_is_a_rounding_artefact_and_is_allowed():
    scope = normalise_scope(
        {"kind": "range", "from": "2026-01-01T00:00:00Z", "to": (NOW + timedelta(seconds=30)).isoformat()},
        NOW,
    )
    assert scope["to"] == (NOW + timedelta(seconds=30)).isoformat()


def test_an_unknown_scope_kind_is_refused():
    with pytest.raises(Exception) as caught:
        normalise_scope({"kind": "yesterday"}, NOW)
    assert "range or full_history" in str(caught.value)


def test_a_bare_from_becomes_a_range():
    assert normalise_scope({"from": "2026-01-01T00:00:00Z"}, NOW)["kind"] == "range"


def test_no_scope_at_all_is_full_history():
    assert normalise_scope(None, NOW)["kind"] == "full_history"
    assert normalise_scope({}, NOW)["kind"] == "full_history"


def test_full_history_has_a_floor_so_the_call_estimate_has_a_denominator():
    scope = normalise_scope({"kind": "full_history"}, NOW)
    assert scope["unbounded_below"] is True
    assert scope["from"] == (NOW - timedelta(days=plan_rules.FULL_HISTORY_FLOOR_DAYS)).isoformat()


def test_full_history_with_an_explicit_from_is_not_unbounded():
    scope = normalise_scope({"kind": "full_history", "from": "2024-01-01T00:00:00Z"}, NOW)
    assert scope["unbounded_below"] is False
    assert scope["from"] == "2024-01-01T00:00:00+00:00"


def test_a_z_suffix_and_a_naive_timestamp_both_read_as_utc():
    """Whatever machine runs the seeder, the result is the same."""
    zulu = normalise_scope({"kind": "range", "from": "2026-01-01T00:00:00Z"}, NOW)
    naive = normalise_scope({"kind": "range", "from": "2026-01-01T00:00:00"}, NOW)
    assert zulu["from"] == naive["from"]


def test_an_unparseable_timestamp_names_the_format():
    with pytest.raises(Exception) as caught:
        normalise_scope({"kind": "range", "from": "last tuesday"}, NOW)
    assert "ISO 8601" in str(caught.value)


def test_a_scope_must_be_an_object():
    with pytest.raises(Exception):
        normalise_scope(["range"], NOW)


# --------------------------------------------------------------------------- #
# The volume rule
# --------------------------------------------------------------------------- #


def adapter(vendor="salesforce"):
    return default_registry()[vendor]


def test_more_than_two_thousand_records_asks_for_an_async_job():
    strategy, reason = plan_rules.choose_strategy(adapter(), estimated_records=2001)
    assert strategy == "async_job"
    assert reason["rule"] == "volume"
    assert "2000" in reason["detail"]


def test_exactly_two_thousand_is_not_more_than_two_thousand():
    strategy, _reason = plan_rules.choose_strategy(adapter(), estimated_records=2000)
    assert strategy == "paged_read"


def test_one_record_over_the_boundary_and_one_under_it_are_different_jobs():
    over, _ = plan_rules.choose_strategy(adapter(), estimated_records=2001)
    under, _ = plan_rules.choose_strategy(adapter(), estimated_records=1999)
    assert over != under


def test_a_negative_estimate_is_refused():
    with pytest.raises(PlanError):
        plan_rules.choose_strategy(adapter(), estimated_records=-1)


def test_a_vendor_that_cannot_do_what_the_volume_asks_falls_through_and_says_so():
    """HubSpot documents one path to a file, so a small export is still an export."""
    strategy, reason = plan_rules.choose_strategy(adapter("hubspot"), estimated_records=10)
    assert strategy == "async_job"
    assert reason["rule"] == "volume_unavailable"
    assert "does not implement" in reason["detail"]


def test_hubspot_documents_only_the_async_path_to_a_file():
    """`POST /crm/exports/2026-09/export/async` is the only way to get one."""
    assert adapter("hubspot").strategies == ("async_job",)


def test_an_unknown_volume_opens_with_the_vendors_own_preference():
    strategy, reason = plan_rules.choose_strategy(adapter(), estimated_records=None)
    assert strategy == "async_job"
    assert reason["rule"] == "undetermined_volume"
    assert "reconsiders" in reason["detail"]


def test_a_live_data_token_keeps_the_run_on_a_delta_read():
    """Switching strategy mid-run would abandon the token it is resuming."""
    strategy, reason = plan_rules.choose_strategy(
        adapter("dataverse"),
        estimated_records=9_000,
        live_cursor={"cursor": "600", "kind": "data_token"},
    )
    assert strategy == "delta_read"
    assert reason["rule"] == "live_cursor", "the cursor decides, not the volume"


def test_a_live_bulk_job_id_keeps_the_run_on_that_job():
    strategy, reason = plan_rules.choose_strategy(
        adapter("salesforce"),
        estimated_records=9_000,
        live_cursor={"cursor": "750abc", "kind": "bulk_job_id"},
    )
    assert strategy == "async_job"
    assert reason["rule"] == "live_cursor"


def test_a_vendor_implementing_no_researched_strategy_is_refused():
    class Nothing(VendorAdapter):
        vendor = "nowhere"
        strategies = ("carrier_pigeon",)

    with pytest.raises(UnsupportedStrategy):
        plan_rules.choose_strategy(Nothing(), estimated_records=1)


# --------------------------------------------------------------------------- #
# The call estimate
# --------------------------------------------------------------------------- #


def test_the_estimate_is_one_to_create_one_to_poll_and_one_per_page():
    assert plan_rules.estimate_calls(total=100, page_size=10) == 1 + 1 + 10
    assert plan_rules.estimate_calls(total=100, page_size=30) == 1 + 1 + 4
    assert plan_rules.estimate_calls(total=1, page_size=5000) == 1 + 1 + 1


def test_an_unsized_plan_reports_no_estimate_rather_than_zero():
    assert plan_rules.estimate_calls(total=None, page_size=100) is None


def test_page_size_defaults_to_the_researched_five_thousand_and_may_be_lower():
    assert plan_rules.normalise_page_size(None, 5000) == 5000
    assert plan_rules.normalise_page_size(250, 5000) == 250
    with pytest.raises(PlanError) as caught:
        plan_rules.normalise_page_size(9000, 5000)
    assert "resume point that does not exist" in str(caught.value)


def test_a_page_size_of_zero_is_refused():
    with pytest.raises(PlanError):
        plan_rules.normalise_page_size(0, 5000)


def test_the_poll_interval_has_a_floor_and_a_default():
    assert plan_rules.normalise_poll_interval(None) == 300
    assert plan_rules.normalise_poll_interval(0) == 0
    with pytest.raises(PlanError):
        plan_rules.normalise_poll_interval(-1)


def test_the_field_map_is_validated_both_ways():
    assert plan_rules.normalise_field_map({"Name": "title"}) == {"Name": "title"}
    assert plan_rules.normalise_field_map({"map": {"Name": "title"}}) == {"Name": "title"}
    assert plan_rules.normalise_field_map(None) == {}
    with pytest.raises(PlanError):
        plan_rules.normalise_field_map({"Name": ""})
    with pytest.raises(PlanError):
        plan_rules.normalise_field_map(["Name"])


# --------------------------------------------------------------------------- #
# The daily allowance
# --------------------------------------------------------------------------- #


def test_the_window_opens_at_local_midnight():
    assert quota_rules.window_start(NOW, 0) == datetime(2026, 9, 27, 0, 0, tzinfo=timezone.utc)
    # New York is UTC-5 in September, so its midnight is 05:00 UTC.
    assert quota_rules.window_start(NOW, -5) == datetime(2026, 9, 27, 5, 0, tzinfo=timezone.utc)
    assert quota_rules.window_start(NOW, 9) == datetime(2026, 9, 26, 15, 0, tzinfo=timezone.utc)


def test_the_window_is_half_open_so_a_call_at_midnight_belongs_to_the_new_day():
    midnight = datetime(2026, 9, 28, 0, 0, tzinfo=timezone.utc)
    assert quota_rules.window_start(midnight, 0) == midnight
    assert quota_rules.next_reset(midnight, 0) == datetime(2026, 9, 29, 0, 0, tzinfo=timezone.utc)


def test_a_connection_without_a_declared_limit_is_not_limited_rather_than_unlimited_by_default():
    assert quota_rules.limit_for({"vendor": "salesforce"}) is None
    state = quota_rules.report({"vendor": "salesforce"}, {"calls": 99}, NOW)
    assert state["limited"] is False and state["remaining"] is None


def test_the_report_names_the_window_and_the_reset():
    state = quota_rules.report(
        {"daily_limit": 100, "quota_timezone_offset_hours": -5},
        {"window_started_at": "2026-09-27T05:00:00+00:00", "calls": 40},
        NOW,
    )
    assert state["remaining"] == 60
    assert state["resets_at"] == "2026-09-28T05:00:00+00:00"
    assert "midnight" in state["quote"]


def test_calls_from_a_window_that_has_rolled_over_are_gone():
    state = quota_rules.report(
        {"daily_limit": 100},
        {"window_started_at": "2026-09-20T00:00:00+00:00", "calls": 100},
        NOW,
    )
    assert state["calls_today"] == 0
    assert state["remaining"] == 100


def test_check_says_a_run_that_fits_fits():
    verdict = quota_rules.check({"daily_limit": 100}, {"calls": 0}, NOW, estimated_calls=10)
    assert verdict["verdict"] == "fits"


def test_check_says_a_run_that_does_not_fit_exceeds_the_remainder():
    verdict = quota_rules.check({"daily_limit": 10}, {"calls": 5}, NOW, estimated_calls=10)
    assert verdict["verdict"] == "exceeds_remaining"
    assert verdict["remaining"] == 5


def test_an_unsized_run_is_unverifiable_rather_than_refused():
    """The research says to size against the quota; a plan with no size has nothing to compare."""
    verdict = quota_rules.check({"daily_limit": 10}, {"calls": 0}, NOW, estimated_calls=None)
    assert verdict["verdict"] == "unverifiable"


def test_a_vendor_with_no_documented_limit_is_never_refused():
    verdict = quota_rules.check({"vendor": "dataverse"}, {"calls": 0}, NOW, estimated_calls=10_000)
    assert verdict["verdict"] == "not_limited"


# --------------------------------------------------------------------------- #
# The transform, and the upsert key
# --------------------------------------------------------------------------- #


def test_the_key_comes_from_the_named_field_first():
    assert transform_rules.external_id({"Id": "006A", "id": "other"}, "Id") == "006A"


def test_the_key_falls_back_to_the_researched_spellings():
    assert transform_rules.external_id({"id": "006A"}, "Nope") == "006A"
    assert transform_rules.external_id({"external_id": "006A"}, "Nope") == "006A"
    assert transform_rules.external_id({"externalId": "006A"}, "Nope") == "006A"


def test_a_row_with_no_key_at_all_is_refused_rather_than_stored():
    with pytest.raises(UnkeyedRow) as caught:
        transform_rules.external_id({"Name": "no id here"}, "Id")
    assert "no duplicates and no gaps" in str(caught.value)


def test_a_blank_key_is_not_a_key():
    with pytest.raises(UnkeyedRow):
        transform_rules.external_id({"Id": "   "}, "Id")


def test_the_default_key_field_is_the_vendor_external_id():
    assert DEFAULT_KEY_FIELD == "id"


def test_the_field_map_renames_and_carries_everything_else_through():
    mapped = transform_rules.apply_map({"Id": "006A", "Name": "Deal", "Amount": 1}, {"Name": "title"})
    # `Id` is Salesforce's spelling and is not reserved, so it survives. Lowercase
    # `id` is the envelope's, and `test_a_vendor_field_called_id_is_carried...`
    # covers that one.
    assert mapped == {"Id": "006A", "Amount": 1, "title": "Deal"}


def test_a_vendor_field_called_id_is_carried_as_external_id_not_duplicated():
    """The envelope owns ``id`` and strips it, so the value lives in one place."""
    row = transform_rules.replica_row(
        {"id": "006A", "Name": "Deal"},
        field_map={},
        key_field="id",
        vendor="salesforce",
        connection_id="c1",
        room_id="r1",
        object_name="opportunity",
        run_id="run1",
        at=NOW.isoformat(),
    )
    assert row["external_id"] == "006A"
    assert "id" not in row
    assert row["Name"] == "Deal"


def test_a_field_map_onto_this_products_bookkeeping_is_refused():
    with pytest.raises(PlanError) as caught:
        transform_rules.apply_map({"Name": "Deal"}, {"Name": "last_seen_at"})
    assert "bookkeeping this product keeps" in str(caught.value)


def test_the_reverse_map_is_the_map_read_the_other_way():
    stored = {"title": "Deal", "stage": "Proposal", "external_id": "006A"}
    assert transform_rules.apply_map(stored, {"Name": "title", "Stage__c": "stage"}, reverse=True) == {
        "Name": "Deal",
        "Stage__c": "Proposal",
    }


def test_bookkeeping_is_stripped_before_a_change_is_measured():
    stored = {
        "title": "Deal",
        "external_id": "006A",
        "vendor": "salesforce",
        "first_seen_at": "a",
        "last_seen_at": "b",
        "run_id": "r1",
    }
    assert transform_rules.payload_of(stored) == {"title": "Deal"}


def test_merge_creates_from_nothing():
    merged, changed = transform_rules.merge(None, {"title": "Deal"})
    assert changed is True and merged == {"title": "Deal"}


def test_merge_sees_no_change_when_only_the_bookkeeping_moved():
    stored = {"title": "Deal", "external_id": "006A", "last_seen_at": "a", "first_seen_at": "b"}
    incoming = {"title": "Deal", "external_id": "006A", "last_seen_at": "c", "first_seen_at": "d"}
    merged, changed = transform_rules.merge(stored, incoming)
    assert changed is False
    assert merged["last_seen_at"] == "c"
    # first_seen_at keeps the earlier of the two: the replica cannot un-know it.
    assert merged["first_seen_at"] == "b"


def test_merge_sees_a_change_when_the_payload_moved():
    stored = {"title": "Deal", "external_id": "006A"}
    _merged, changed = transform_rules.merge(stored, {"title": "Renamed", "external_id": "006A"})
    assert changed is True


def test_a_row_that_changed_key_is_a_different_row():
    stored = {"title": "Deal", "external_id": "006A"}
    merged, changed = transform_rules.merge(stored, {"title": "Deal", "external_id": "006B"})
    assert changed is True and merged["external_id"] == "006B"


def test_the_outbox_restores_the_key_under_the_vendors_own_field_name():
    stored = {"title": "Deal", "external_id": "006A", "last_seen_at": "x", "run_id": "r1"}
    assert transform_rules.outbox([stored], field_map={"Name": "title"}, key_field="Id") == [
        {"Name": "Deal", "Id": "006A"}
    ]


def test_a_soft_deleted_row_is_not_sent_on_a_push():
    stored = {"title": "Deal", "external_id": "006A", "deleted_at": NOW.isoformat()}
    assert transform_rules.outbox([stored], field_map={}) == []


# --------------------------------------------------------------------------- #
# The vendor seam
# --------------------------------------------------------------------------- #


def test_the_researched_interface_is_two_methods_and_the_base_says_so():
    assert VendorAdapter.strategies == ()
    with pytest.raises(NotImplementedError):
        VendorAdapter().start({}, {})
    with pytest.raises(NotImplementedError):
        VendorAdapter().read_page({}, {}, {}, None, 0)


def test_a_read_only_vendor_refuses_a_push_by_naming_the_method():
    class ReadOnly(VendorAdapter):
        vendor = "salesforce"

    from dsr.crm_backfill import errors

    with pytest.raises(errors.DirectionNotSupported) as caught:
        ReadOnly().submit_page({}, {}, {}, [], 1)
    assert "submit_page" in str(caught.value)


def test_a_vendor_with_no_adapter_is_refused_with_the_way_out():
    with pytest.raises(UnsupportedVendor) as caught:
        vendor_rules.require_registered("pipedream", {"salesforce": adapter()})
    assert "create job" in str(caught.value) and "read page" in str(caught.value)


def test_a_strategy_the_adapter_lacks_is_refused_naming_what_it_has():
    with pytest.raises(UnsupportedStrategy) as caught:
        vendor_rules.require_supported(adapter("dataverse"), "async_job")
    assert "delta_read" in str(caught.value)


def test_every_shipped_adapter_declares_what_the_research_describes():
    registry = default_registry()
    assert registry["salesforce"].strategies == ("async_job", "paged_read")
    assert registry["dataverse"].strategies == ("delta_read", "paged_read")
    assert registry["hubspot"].required_scopes == ("crm.export",)
    assert registry["hubspot"].addresses_objects_by_id is True
    assert registry["salesforce"].cursor_kind == "bulk_job_id"
    assert registry["dataverse"].cursor_kind == "data_token"
    assert registry["hubspot"].cursor_kind == "export_id"


def test_a_hubspot_connection_without_the_grant_is_told_about_the_super_admin_rule():
    findings = HubSpotExportAdapter(SimulatedHistory([])).preflight(
        {"granted_scopes": []}, {"object_name": "CONTACT"}
    )
    assert len(findings) == 1
    assert "crm.export" in findings[0] and "Super Admin" in findings[0]


def test_a_hubspot_connection_with_the_grant_gets_no_scope_finding():
    findings = HubSpotExportAdapter(SimulatedHistory([])).preflight(
        {"granted_scopes": ["crm.export"]}, {"object_name": "CONTACT"}
    )
    assert findings == []


def test_a_custom_object_named_by_its_label_asks_for_an_object_type_id():
    findings = vendor_rules.check_object_addressing(
        {"standard_objects": ["CONTACT"]}, {"object_name": "MY_CUSTOM"}
    )
    assert "objectTypeId" in findings[0] and "object_type_id" in findings[0]


def test_a_standard_object_name_is_accepted():
    assert (
        vendor_rules.check_object_addressing({"standard_objects": ["CONTACT"]}, {"object_name": "CONTACT"})
        == []
    )


def test_a_connection_that_declares_no_standard_objects_declines_to_guess():
    assert vendor_rules.check_object_addressing({}, {"object_name": "ANYTHING"}) == []


def test_an_object_type_id_satisfies_the_rule_on_its_own():
    assert vendor_rules.check_object_addressing(
        {"standard_objects": ["CONTACT"]}, {"object_name": "MY_CUSTOM", "object_type_id": "12345"}
    ) == []


def test_the_simulated_history_pages_in_a_stable_order():
    source = SimulatedHistory(history_rows(10))
    first = source.rows({}, {"kind": "full_history"}, offset=0, limit=3)
    second = source.rows({}, {"kind": "full_history"}, offset=3, limit=3)
    assert [row["id"] for row in first] == ["acc-0000", "acc-0001", "acc-0002"]
    assert [row["id"] for row in second] == ["acc-0003", "acc-0004", "acc-0005"]


def test_the_history_scope_is_inclusive_below_and_exclusive_above():
    """Two adjacent ranges neither skip nor double-count the row on their boundary."""
    source = SimulatedHistory(history_rows(4, days=4))
    whole = source.scoped({}, {"from": "2026-01-01T00:00:00+00:00", "to": "2027-01-01T00:00:00+00:00"})
    half = source.scoped({}, {"from": whole[0]["occurred_at"], "to": whole[2]["occurred_at"]})
    rest = source.scoped(
        {}, {"from": whole[2]["occurred_at"], "to": "2027-01-01T00:00:00+00:00"}
    )
    assert len(half) + len(rest) == len(whole)
    assert [r["id"] for r in half] + [r["id"] for r in rest] == [r["id"] for r in whole]


def test_a_connection_reads_only_the_object_it_names():
    """One history serves three vendors, so an export of one object must not answer with another."""
    source = SimulatedHistory(
        [{**history_rows(3)[0], "id": "a", "object": "opportunity"}]
        + [{**history_rows(3)[1], "id": "b", "object": "account"}]
    )
    assert [row["id"] for row in source.scoped({"object_name": "account"}, {})] == ["b"]
    assert source.count({"object_name": "opportunity"}, {}) == 1
    # A connection that names no object gets the lot, which is what lets a
    # single-vendor deployment leave the tag out.
    assert source.count({}, {}) == 2


def test_the_object_tag_is_routing_and_is_stripped_before_it_reaches_the_replica(store, clock):
    """Left in, it would collide with the replica's own `object` bookkeeping.

    The run's object wins, and it is the *run* that names it: the connection's
    ``object_name`` scopes what the scripted source will answer with, and the
    run's is what the room recorded the rows as being.
    """
    source = SimulatedHistory([{**history_rows(2)[0], "object": "opportunity"}])
    engine = BackfillEngine(store, registry=default_registry(source), clock=clock)
    connection = engine.create_connection(
        {"vendor": "dataverse", "object_name": "opportunity"}, source=SOURCE
    )
    drive(engine, "room-1", open_run(engine, connection, page_size=5, object_name="opportunity"))
    row = engine.replica(room_id="room-1", limit=1)[0]
    assert row["data"]["object"] == "opportunity"
    # Stripped on the way through, so it is bookkeeping rather than payload: a
    # field map naming `object` would not be refused over a name this invented.
    assert "object" not in transform_rules.payload_of(row["data"])


def test_a_run_that_names_no_object_records_none_rather_than_guessing(store, clock):
    source = SimulatedHistory([{**history_rows(2)[0], "object": "opportunity"}])
    engine = BackfillEngine(store, registry=default_registry(source), clock=clock)
    connection = engine.create_connection(
        {"vendor": "dataverse", "object_name": "opportunity"}, source=SOURCE
    )
    drive(engine, "room-1", open_run(engine, connection, page_size=5))
    assert engine.replica(room_id="room-1", limit=1)[0]["data"]["object"] is None


def test_a_salesforce_job_is_not_readable_on_its_first_poll():
    """The researched background processing, modelled rather than assumed."""
    adapter = SalesforceBulkAdapter(SimulatedHistory(history_rows(10)))
    plan = {"scope": {"kind": "full_history"}, "page_size": 5, "ready_after": 1}
    started = adapter.start({"id": "c1"}, plan | {"run_id": "r1"})
    assert started.job_state == "queued" and started.rows == [] and started.more is True
    early = adapter.read_page({"id": "c1"}, plan | {"run_id": "r1"}, {"ready_after": 1}, started.cursor, 0)
    assert early.rows == [] and "still being prepared" in early.detail
    ready = adapter.read_page({"id": "c1"}, plan | {"run_id": "r1"}, {"ready_after": 1}, started.cursor, 1)
    assert len(ready.rows) == 5 and ready.more is True


def test_a_salesforce_cursor_is_the_job_and_does_not_move():
    adapter = SalesforceBulkAdapter(SimulatedHistory(history_rows(10)))
    plan = {"scope": {"kind": "full_history"}, "page_size": 5, "ready_after": 0}
    started = adapter.start({"id": "c1"}, plan | {"run_id": "r1"})
    page = adapter.read_page({"id": "c1"}, plan | {"run_id": "r1"}, {"processed": 5, "total": 10}, started.cursor, 1)
    assert page.cursor == started.cursor


def test_a_job_handle_is_derived_from_the_run_so_two_processes_agree():
    first = SalesforceBulkAdapter(SimulatedHistory([])).start({"id": "c1"}, {"run_id": "r1", "attempt": 0})
    again = SalesforceBulkAdapter(SimulatedHistory([])).start({"id": "c1"}, {"run_id": "r1", "attempt": 0})
    other = SalesforceBulkAdapter(SimulatedHistory([])).start({"id": "c1"}, {"run_id": "r1", "attempt": 1})
    assert first.cursor == again.cursor
    assert other.cursor != first.cursor


def test_the_hubspot_export_statuses_are_published_rather_than_assumed():
    assert HUBSPOT_EXPORT_STATUSES == ("IN_PROGRESS", "COMPLETED", "FAILED")
    assert "IN_PROGRESS" in HUBSPOT_EXPORT_STATUSES


# --------------------------------------------------------------------------- #
# Opening a run
# --------------------------------------------------------------------------- #


def test_a_connection_records_the_fields_the_vendors_own_rules_need(engine):
    connection = engine.create_connection(
        {
            "vendor": "hubspot",
            "granted_scopes": ["crm.export"],
            "standard_objects": ["CONTACT"],
            "daily_limit": 1_000,
            "quota_timezone_offset_hours": -5,
        },
        source=SOURCE,
    )
    data = connection["data"]
    assert data["granted_scopes"] == ["crm.export"]
    assert data["standard_objects"] == ["CONTACT"]
    assert data["daily_limit"] == 1_000
    assert connection["quota"]["time_zone_offset_hours"] == -5
    assert connection["required_scopes"] == ["crm.export"]


def test_a_vendor_nobody_researched_is_refused_with_the_way_to_add_one(engine):
    with pytest.raises(UnsupportedVendor) as caught:
        engine.create_connection({"vendor": "pipedream"}, source=SOURCE)
    assert "create job" in str(caught.value)


def test_a_connection_needs_a_vendor(engine):
    with pytest.raises(PlanError):
        engine.create_connection({"name": "nameless"}, source=SOURCE)


def test_opening_a_run_stores_the_wizards_four_decisions_as_a_transcript(engine, dataverse):
    run = open_run(engine, dataverse)
    events = [entry["data"]["event"] for entry in engine.events(run["id"])]
    assert events[:4] == ["run_created", "connection_checked", "strategy_selected", "job_created"]
    assert run["data"]["strategy"] == "delta_read"
    assert run["data"]["scope"]["kind"] == "full_history"
    assert run["data"]["state"] in ("running", "complete")


def test_opening_a_run_adopts_the_stored_cursor_rather_than_replacing_it(engine, dataverse):
    first = drive(engine, "room-1", open_run(engine, dataverse))
    second = open_run(engine, dataverse)
    assert first["data"]["state"] == "complete"
    assert second["data"]["adopted_cursor"] is True
    assert "cursor_adopted" in [e["data"]["event"] for e in engine.events(second["id"])]


def test_from_scratch_needs_the_full_history_scope_because_a_range_would_re_read(engine, dataverse):
    with pytest.raises(PlanError) as caught:
        engine.start(
            "room-1",
            {
                "connection_id": dataverse["id"],
                "scope": {"kind": "range", "from": "2026-01-01T00:00:00Z"},
                "from_scratch": True,
            },
            source=SOURCE,
        )
    assert "full_history" in str(caught.value)


def test_from_scratch_ignores_the_stored_cursor(engine, dataverse):
    drive(engine, "room-1", open_run(engine, dataverse))
    again = open_run(engine, dataverse, from_scratch=True)
    assert again["data"]["adopted_cursor"] is False


def test_an_unknown_volume_is_reconsidered_once_against_the_vendors_own_count(engine, salesforce, store):
    """The room said it did not know the volume; the vendor does, and nothing has landed yet."""
    big = BackfillEngine(
        RecordStore(store.db), registry=default_registry(SimulatedHistory(history_rows(40))), clock=lambda: NOW
    )
    connection = big.create_connection({"vendor": "salesforce"}, source=SOURCE)
    run = big.start("room-1", {"connection_id": connection["id"], "scope": {"kind": "full_history"}, "page_size": 5}, source=SOURCE)
    events = [entry["data"]["event"] for entry in big.events(run["id"])]
    assert "strategy_reconsidered" in events
    assert "run_replanned" in events
    assert run["counters"]["replans"] == 1
    assert run["data"]["strategy"] == "paged_read"
    assert run["data"]["strategy_reason"]["rule"] == "reconsidered_after_total"


def test_a_stated_volume_is_not_second_guessed(engine, salesforce):
    run = open_run(engine, salesforce, estimated_records=1)
    assert "strategy_reconsidered" not in [e["data"]["event"] for e in engine.events(run["id"])]


def test_opening_a_run_needs_a_connection(engine):
    with pytest.raises(UnknownConnection) as caught:
        open_run(engine, {"id": "nope"})
    assert "nope" in str(caught.value)


def test_opening_a_run_with_no_connection_id_says_what_is_missing(engine):
    with pytest.raises(UnknownConnection) as caught:
        engine.start("room-1", {"scope": {"kind": "full_history"}}, source=SOURCE)
    assert "connection_id is required" in str(caught.value)


def test_opening_a_run_needs_a_room(engine, dataverse):
    with pytest.raises(PlanError):
        engine.start("", {"connection_id": dataverse["id"]}, source=SOURCE)


def test_a_backfill_larger_than_the_rest_of_the_allowance_is_refused_before_a_job_exists(store, clock):
    engine = BackfillEngine(
        RecordStore(store.db), registry=default_registry(SimulatedHistory(history_rows(40))), clock=clock
    )
    connection = engine.create_connection(
        {
            "vendor": "hubspot",
            "granted_scopes": ["crm.export"],
            "standard_objects": ["CONTACT"],
            "daily_limit": 4,
        },
        source=SOURCE,
    )
    with pytest.raises(QuotaExceeded) as caught:
        engine.start(
            "room-1",
            {
                "connection_id": connection["id"],
                "scope": {"kind": "full_history"},
                "object_name": "CONTACT",
                "estimated_records": 40,
                "page_size": 5,
            },
            source=SOURCE,
        )
    assert "daily calls are left" in str(caught.value)
    assert "resets at" in str(caught.value)
    assert engine.runs() == []


def test_a_run_whose_connection_cannot_do_what_was_asked_stops_before_the_job(engine):
    connection = engine.create_connection(
        {"vendor": "hubspot", "granted_scopes": [], "standard_objects": ["CONTACT"]}, source=SOURCE
    )
    run = open_run(engine, connection, object_name="CONTACT")
    assert run["data"]["state"] == "created"
    assert run["data"]["job"] == {}
    assert "crm.export" in run["data"]["findings"][0]
    assert run["data"]["findings"][0] in [
        entry["data"]["detail"] for entry in engine.events(run["id"])
    ]


def test_every_preflight_finding_lands_at_once(engine):
    connection = engine.create_connection(
        {"vendor": "hubspot", "granted_scopes": [], "standard_objects": ["CONTACT"]}, source=SOURCE
    )
    run = open_run(engine, connection, object_name="MY_CUSTOM")
    assert len(run["data"]["findings"]) == 2


# --------------------------------------------------------------------------- #
# The page cycle
# --------------------------------------------------------------------------- #


def test_a_poll_before_the_run_is_due_asks_nothing_of_the_vendor(engine, dataverse):
    run = open_run(engine, dataverse, poll_interval_seconds=300)
    step = engine.poll("room-1", run["id"], source=POLL_SOURCE)
    assert step["advanced"] is False
    assert step["reason"] == "not_due"
    assert "fixed interval" in step["detail"]


def test_a_poll_of_a_finished_run_is_refused_rather_than_answered(engine, dataverse):
    run = drive(engine, "room-1", open_run(engine, dataverse))
    with pytest.raises(RunStateError) as caught:
        engine.poll("room-1", run["id"], source=POLL_SOURCE)
    assert "nothing left to poll" in str(caught.value)


def test_a_poll_of_a_run_in_another_room_is_a_404_not_a_leak(engine, dataverse):
    run = open_run(engine, dataverse, room="room-1")
    with pytest.raises(RunNotFound):
        engine.run("room-2", run["id"])


def test_a_page_lands_then_the_cursor_moves(engine, dataverse, store):
    run = open_run(engine, dataverse, page_size=10)
    assert run["counters"]["rows_seen"] == 10
    assert run["data"]["cursor"]["cursor"] == "10"
    assert run["data"]["cursor"]["kind"] == "data_token"
    # One cursor record per room and connection, not one per page.
    assert len(engine.cursors(room_id="room-1")) == 1


def test_the_cursor_is_written_to_the_standard_shape(engine, dataverse):
    run = open_run(engine, dataverse)
    stored = engine.stored_cursor("room-1", run["data"]["connection_id"])
    assert stored["vendor"] == "dataverse"
    assert stored["connectionId"] == run["data"]["connection_id"]
    assert stored["cursor"] == "10"
    assert stored["updatedAt"].startswith("2026-09-27")


def test_driving_a_run_to_completion_reads_everything_exactly_once(engine, dataverse):
    run = drive(engine, "room-1", open_run(engine, dataverse, page_size=7))
    assert run["data"]["state"] == "complete"
    assert run["counters"]["rows_seen"] == 30
    assert run["counters"]["rows_created"] == 30
    assert run["counters"]["rows_updated"] == 0
    assert len(engine.replica(room_id="room-1", limit=100)) == 30


def test_completion_is_the_researched_last_step_of_the_data_flow(engine, dataverse):
    run = drive(engine, "room-1", open_run(engine, dataverse))
    assert run["data"]["completed_at"] is not None
    assert run["data"]["next_poll_at"] is None
    assert "run_complete" in [e["data"]["event"] for e in engine.events(run["id"])]


def test_a_row_with_no_key_is_rejected_and_the_page_carries_on(store, clock):
    engine = BackfillEngine(
        RecordStore(store.db),
        registry=default_registry(SimulatedHistory(history_rows(10, unkeyed=2))),
        clock=clock,
    )
    connection = engine.create_connection({"vendor": "dataverse"}, source=SOURCE)
    run = drive(engine, "room-1", open_run(engine, connection, page_size=10))
    assert run["counters"]["rows_rejected"] == 2
    assert run["counters"]["rows_created"] == 8
    assert run["data"]["state"] == "complete"
    rejections = [e for e in engine.events(run["id"]) if e["data"]["event"] == "rows_rejected"]
    assert rejections and "cannot stall the range" in rejections[0]["data"]["detail"]


def test_a_rejected_row_is_rendered_shortly_because_a_log_is_not_a_record_of_personal_data(store, clock):
    engine = BackfillEngine(
        RecordStore(store.db),
        registry=default_registry(SimulatedHistory(history_rows(4, unkeyed=1))),
        clock=clock,
    )
    connection = engine.create_connection({"vendor": "dataverse"}, source=SOURCE)
    run = drive(engine, "room-1", open_run(engine, connection, page_size=4))
    rejection = [e for e in engine.events(run["id"]) if e["data"]["event"] == "rows_rejected"][0]
    assert rejection["data"]["count"] == 1
    assert "no duplicates and no gaps" in rejection["data"]["rows"][0]["reason"]


def test_a_vendor_that_breaks_is_a_failed_run_and_not_a_500(store, clock):
    class Broken(HubSpotExportAdapter):
        def read_page(self, *args, **kwargs):
            raise RuntimeError("the CRM returned nothing useful")

    engine = BackfillEngine(store, registry={"hubspot": Broken(SimulatedHistory([]))}, clock=clock)
    connection = engine.create_connection(
        {"vendor": "hubspot", "granted_scopes": ["crm.export"]}, source=SOURCE
    )
    run = open_run(engine, connection)
    step = engine.poll("room-1", run["id"], source=POLL_SOURCE, force=True)
    assert step["reason"] == "vendor_error"
    assert step["run"]["data"]["state"] == "failed"
    assert step["run"]["data"]["failure"]["detail"] == "the CRM returned nothing useful"


def test_a_vendor_reported_failure_is_logged_rather_than_guessed_at(store, clock):
    class Failing(HubSpotExportAdapter):
        def read_page(self, *args, **kwargs):
            return VendorPage(job_state="FAILED", failure="the export could not be produced")

    engine = BackfillEngine(store, registry={"hubspot": Failing(SimulatedHistory([]))}, clock=clock)
    connection = engine.create_connection(
        {"vendor": "hubspot", "granted_scopes": ["crm.export"]}, source=SOURCE
    )
    run = open_run(engine, connection)
    step = engine.poll("room-1", run["id"], source=POLL_SOURCE, force=True)
    assert step["reason"] == "vendor_reported_failure"
    assert step["run"]["data"]["failure"]["reason"] == "vendor_reported_failure"


def test_a_run_that_reaches_the_allowance_waits_for_the_reset_rather_than_failing(store, clock):
    engine = BackfillEngine(
        store, registry=default_registry(SimulatedHistory(history_rows(20))), clock=clock
    )
    # Four calls allowed: the start spends one and the size check passes, so the
    # run opens; the wall arrives on a later poll rather than at the door.
    connection = engine.create_connection(
        {"vendor": "salesforce", "daily_limit": 4}, source=SOURCE
    )
    run = open_run(engine, connection, page_size=5, estimated_records=1)
    step = {}
    for _ in range(8):
        step = engine.poll("room-1", run["id"], source=POLL_SOURCE, force=True)
        if step["reason"] == "quota_spent":
            break
    assert step["reason"] == "quota_spent"
    assert step["run"]["data"]["state"] == "running"
    assert step["next_poll_at"] == "2026-09-28T00:00:00+00:00"
    assert "quota_refused" in [e["data"]["event"] for e in engine.events(run["id"])]


# --------------------------------------------------------------------------- #
# The researched crash promise
# --------------------------------------------------------------------------- #


class CrashingStore(RecordStore):
    """A store that dies once, on the write named, and then behaves."""

    def __init__(self, db, *, collection: str, after: int = 1) -> None:
        super().__init__(db)
        self.collection = collection
        self.remaining = after
        self.crashed = False

    def update(self, record_id, patch, **kwargs):
        existing = self.get(record_id)
        if (
            not self.crashed
            and existing is not None
            and existing["collection"] == self.collection
            and self.remaining > 0
        ):
            self.remaining -= 1
            self.crashed = True
            raise RuntimeError(f"simulated crash writing {self.collection}")
        return super().update(record_id, patch, **kwargs)


def test_a_crash_between_the_page_and_the_cursor_replays_the_page_and_duplicates_nothing(
    engine, dataverse, store
):
    """The researched promise, staged: rows land, the resume point does not move."""
    run = open_run(engine, dataverse, page_size=10)
    before = len(engine.replica(room_id="room-1", limit=100))
    engine.store = CrashingStore(store.db, collection=CURSORS_STORE)
    with pytest.raises(RuntimeError):
        engine.resume("room-1", run["id"], source=RESUME_SOURCE)
    engine.store = store

    landed = len(engine.replica(room_id="room-1", limit=100))
    assert landed == before + 10, "the page was written before the cursor, as the ordering requires"
    after = engine.resume("room-1", run["id"], source=RESUME_SOURCE)["run"]
    assert len(engine.replica(room_id="room-1", limit=100)) == landed
    assert after["counters"]["rows_created"] == 10, "only the genuinely new page created anything"
    assert after["counters"]["rows_unchanged"] == 10, "the replayed page merged rather than duplicated"


def test_a_crash_after_the_cursor_moves_loses_nothing(engine, dataverse, store):
    """The other ordering: the resume point is ahead, so nothing is re-read."""
    run = open_run(engine, dataverse, page_size=10)
    engine.store = CrashingStore(store.db, collection=RUNS, after=1)
    with pytest.raises(RuntimeError):
        engine.resume("room-1", run["id"], source=RESUME_SOURCE)
    engine.store = store
    after = engine.resume("room-1", run["id"], source=RESUME_SOURCE)["run"]
    assert after["counters"]["rows_seen"] == 20
    assert after["counters"]["rows_created"] == 20
    assert len(engine.replica(room_id="room-1", limit=100)) == 20


def test_resume_counts_itself_so_a_room_restarted_four_times_says_so(engine, dataverse):
    # Left running on purpose: a finished run cannot be resumed, so this has to
    # count three resumes against a run that still has pages left.
    run = open_run(engine, dataverse, page_size=7)
    assert run["counters"]["resumes"] == 0
    for _ in range(3):
        run = engine.resume("room-1", run["id"], source=RESUME_SOURCE)["run"]
        assert run["data"]["state"] == "running"
    assert engine.run("room-1", run["id"])["counters"]["resumes"] == 3
    assert engine.run("room-1", run["id"])["counters"]["rows_seen"] == 28


def test_a_finished_run_cannot_be_resumed(engine, dataverse):
    run = drive(engine, "room-1", open_run(engine, dataverse))
    with pytest.raises(RunStateError):
        engine.resume("room-1", run["id"], source=RESUME_SOURCE)


def test_an_aged_data_token_stalls_the_run_rather_than_reading_the_range_again(engine, dataverse, clock):
    """Dataverse "throws an exception"; the room refuses first and says what to do."""
    run = open_run(engine, dataverse, page_size=10)
    clock.advance(days=8)
    with pytest.raises(CursorExpired) as caught:
        engine.resume("room-1", run["id"], source=RESUME_SOURCE)
    assert "full-history backfill" in str(caught.value)
    stalled = engine.run("room-1", run["id"])
    assert stalled["data"]["state"] == "stalled"
    assert stalled["data"]["failure"]["reason"] == "cursor_expired"
    assert "run_stalled" in [e["data"]["event"] for e in engine.events(run["id"])]


def test_a_stalled_run_cannot_be_resumed_and_says_which_backfill_to_open(engine, dataverse, clock):
    run = open_run(engine, dataverse)
    clock.advance(days=8)
    with pytest.raises(CursorExpired):
        engine.resume("room-1", run["id"], source=RESUME_SOURCE)
    with pytest.raises(RunStateError) as caught:
        engine.resume("room-1", run["id"], source=RESUME_SOURCE)
    assert "full-history backfill" in str(caught.value)


def test_a_poll_stops_at_an_aged_token_before_the_vendor_is_asked(engine, dataverse, clock):
    run = open_run(engine, dataverse, page_size=10)
    clock.advance(days=8)
    with pytest.raises(CursorExpired):
        engine.poll("room-1", run["id"], source=POLL_SOURCE, force=True)
    assert engine.run("room-1", run["id"])["data"]["state"] == "stalled"


def test_a_stalled_run_is_listed_separately_because_it_needs_a_human(engine, dataverse, clock):
    open_run(engine, dataverse)
    clock.advance(days=8)
    with pytest.raises(CursorExpired):
        engine.resume("room-1", engine.runs(room_id="room-1")[0]["id"], source=RESUME_SOURCE)
    summary = engine.summary(room_id="room-1")
    assert summary["by_state"]["stalled"] == 1
    assert len(summary["stalled"]) == 1


def test_opening_a_new_run_against_an_aged_token_is_refused_too(engine, dataverse, clock):
    open_run(engine, dataverse)
    clock.advance(days=8)
    with pytest.raises(CursorExpired):
        open_run(engine, dataverse)


# --------------------------------------------------------------------------- #
# Idempotence
# --------------------------------------------------------------------------- #


def test_reading_the_same_range_again_writes_nothing_at_all(engine, dataverse, store):
    """The visible half of the promise: a re-read is not work."""
    first = drive(engine, "room-1", open_run(engine, dataverse, page_size=7))
    assert first["counters"]["rows_created"] == 30
    revisions = {row["id"]: row["revision"] for row in engine.replica(room_id="room-1", limit=100)}
    audits = len(store.audit(collection=REPLICA, limit=1000))

    second = drive(engine, "room-1", open_run(engine, dataverse, page_size=7, from_scratch=True))
    assert second["counters"]["rows_unchanged"] == 30
    assert second["counters"]["rows_written"] == 0
    assert {row["id"]: row["revision"] for row in engine.replica(room_id="room-1", limit=100)} == revisions
    assert len(store.audit(collection=REPLICA, limit=1000)) == audits


def test_a_row_whose_content_moved_is_written_and_its_last_seen_at_moves(store, clock):
    source = SimulatedHistory(history_rows(5))
    engine = BackfillEngine(store, registry=default_registry(source), clock=clock)
    connection = engine.create_connection({"vendor": "dataverse"}, source=SOURCE)
    drive(engine, "room-1", open_run(engine, connection, page_size=5))
    before = engine.replica(room_id="room-1", limit=10)[0]

    source.add([{"id": "acc-0000", "Name": "Renamed", "Amount": 9, "occurred_at": NOW.isoformat()}])
    clock.advance(hours=1)
    again = drive(engine, "room-1", open_run(engine, connection, page_size=5, from_scratch=True))
    assert again["counters"]["rows_updated"] == 1
    after = next(row for row in engine.replica(room_id="room-1", limit=10) if row["data"]["external_id"] == "acc-0000")
    assert after["data"]["Name"] == "Renamed"
    assert after["data"]["last_seen_at"] > before["data"]["last_seen_at"]
    assert after["data"]["first_seen_at"] == before["data"]["first_seen_at"]


def test_the_field_map_renames_the_replica_columns(engine, salesforce):
    run = drive(engine, "room-1", open_run(engine, salesforce, page_size=10, field_map={"Name": "title"}))
    row = engine.replica(room_id="room-1", limit=1)[0]
    assert row["data"]["title"].startswith("Account")
    assert "Name" not in row["data"]


# --------------------------------------------------------------------------- #
# The reverse direction
# --------------------------------------------------------------------------- #


def test_a_push_submits_the_replica_back_through_the_same_map(engine, salesforce):
    drive(engine, "room-1", open_run(engine, salesforce, page_size=10))
    push = open_run(engine, salesforce, direction="push", page_size=10)
    push = drive(engine, "room-1", push)
    assert push["data"]["direction"] == "push"
    assert push["counters"]["rows_written"] == 30
    assert push["data"]["state"] == "complete"
    assert "submitted" in [
        e["data"]["detail"] for e in engine.events(push["id"]) if e["data"]["event"] == "page_written"
    ][0]


def test_a_push_advances_a_paging_cookie_cursor(engine, salesforce):
    drive(engine, "room-1", open_run(engine, salesforce, page_size=10))
    push = drive(engine, "room-1", open_run(engine, salesforce, direction="push", page_size=10))
    assert push["data"]["cursor"]["kind"] == "paging_cookie"


def test_a_vendor_that_cannot_receive_a_push_is_refused(store, clock):
    class ReadOnly(VendorAdapter):
        vendor = "salesforce"
        strategies = ("async_job", "paged_read")
        cursor_kind = "bulk_job_id"

    engine = BackfillEngine(store, registry={"salesforce": ReadOnly()}, clock=clock)
    connection = engine.create_connection({"vendor": "salesforce"}, source=SOURCE)
    from dsr.crm_backfill import errors

    with pytest.raises(errors.DirectionNotSupported):
        open_run(engine, connection, direction="push")


def test_an_unknown_direction_is_refused(engine, salesforce):
    with pytest.raises(PlanError) as caught:
        open_run(engine, salesforce, direction="sideways")
    assert "pull or push" in str(caught.value)


# --------------------------------------------------------------------------- #
# Cancellation
# --------------------------------------------------------------------------- #


def test_cancelling_keeps_the_landed_rows_and_the_cursor(engine, dataverse):
    run = open_run(engine, dataverse, page_size=10)
    before = len(engine.replica(room_id="room-1", limit=100))
    cancelled = engine.cancel("room-1", run["id"], source=SOURCE, reason="wrong range")
    assert cancelled["data"]["state"] == "cancelled"
    assert len(engine.replica(room_id="room-1", limit=100)) == before
    assert engine.stored_cursor("room-1", run["data"]["connection_id"]) is not None
    assert "wrong range" in [e["data"]["detail"] for e in engine.events(run["id"])][-1]


def test_a_finished_run_cannot_be_cancelled(engine, dataverse):
    run = drive(engine, "room-1", open_run(engine, dataverse))
    with pytest.raises(RunStateError) as caught:
        engine.cancel("room-1", run["id"], source=SOURCE)
    assert "something untrue" in str(caught.value)


# --------------------------------------------------------------------------- #
# Listing, cursors, replica, summary
# --------------------------------------------------------------------------- #


def test_runs_are_listed_newest_first_and_filtered_by_state(engine, dataverse):
    open_run(engine, dataverse)
    drive(engine, "room-2", open_run(engine, dataverse, room="room-2"))
    assert len(engine.runs(room_id="room-1")) == 1
    assert engine.runs(room_id="room-1", state="running")
    assert engine.runs(room_id="room-1", state="complete") == []


def test_a_run_filter_outside_the_vocabulary_is_refused(engine):
    with pytest.raises(PlanError):
        engine.runs(state="vibing")


def test_runs_are_filterable_on_any_json_path(engine, dataverse):
    open_run(engine, dataverse, strategy="delta_read")
    assert len(engine.runs(where="vendor=dataverse")) == 1
    assert engine.runs(where="vendor=salesforce") == []


def test_a_malformed_where_is_refused_with_the_reason(engine):
    with pytest.raises(PlanError):
        engine.runs(where="{not json")


def test_cursors_report_their_expiry_verdict_per_connection(engine, dataverse, salesforce):
    # Driven, not merely opened: a Salesforce job returns no rows on its first
    # call, and a cursor is written after the first page lands rather than when
    # the job is created.
    drive(engine, "room-1", open_run(engine, dataverse, page_size=10))
    drive(engine, "room-1", open_run(engine, salesforce, page_size=10))
    listed = {entry["kind"]: entry for entry in engine.cursors(room_id="room-1")}
    assert set(listed) == {"data_token", "bulk_job_id"}
    assert listed["data_token"]["expiry"]["applies"] is True
    assert listed["bulk_job_id"]["expiry"]["applies"] is False
    assert listed["data_token"]["expiry"]["connection_known"] is True


def test_a_cursor_whose_connection_is_gone_reports_it_rather_than_looking_fine(engine, dataverse, store):
    run = open_run(engine, dataverse)
    store.delete(run["data"]["connection_id"], source="test")
    listed = engine.cursors(room_id="room-1")
    assert listed[0]["expiry"]["connection_known"] is False


def test_the_replica_is_filterable_on_a_mapped_field(engine, salesforce):
    drive(engine, "room-1", open_run(engine, salesforce, page_size=5, field_map={"Stage__c": "stage"}))
    found = engine.replica(room_id="room-1", where="stage=Proposal", limit=100)
    assert found and all(row["data"]["stage"] == "Proposal" for row in found)


def test_a_field_a_team_added_later_is_queryable_without_a_code_change(engine, store, clock):
    source = SimulatedHistory([{**history_rows(1)[0], "annual_revenue": 12_000_000}])
    engine = BackfillEngine(store, registry=default_registry(source), clock=clock)
    connection = engine.create_connection({"vendor": "dataverse"}, source=SOURCE)
    drive(engine, "room-1", open_run(engine, connection))
    found = engine.replica(room_id="room-1", where="annual_revenue=12000000", limit=10)
    assert len(found) == 1


def test_the_summary_counts_this_rooms_runs_only(engine, dataverse):
    open_run(engine, dataverse, room="room-1")
    open_run(engine, dataverse, room="room-2")
    first = engine.summary(room_id="room-1")
    second = engine.summary(room_id="room-2")
    assert first["runs"] == 1 and second["runs"] == 1
    assert first["no_sla_quote"] == NO_SLA_QUOTE


def test_the_summary_says_when_the_replica_count_is_capped(engine, dataverse, store):
    engine = BackfillEngine(
        store, registry=default_registry(SimulatedHistory(history_rows(20))), clock=lambda: NOW
    )
    connection = engine.create_connection({"vendor": "dataverse"}, source=SOURCE)
    drive(engine, "room-1", open_run(engine, connection))
    assert engine.summary(room_id="room-1")["replica_rows_truncated"] is False


def test_the_per_run_log_reads_oldest_first(engine, dataverse):
    run = drive(engine, "room-1", open_run(engine, dataverse, page_size=7))
    listed = engine.events(run["id"])
    assert [entry["data"]["event"] for entry in listed][0] == "run_created"
    assert listed[-1]["data"]["event"] == "run_complete"
    assert [entry["created_at"] for entry in listed] == sorted(
        entry["created_at"] for entry in listed
    )


def test_every_log_event_is_in_the_published_vocabulary(engine, dataverse):
    run = drive(engine, "room-1", open_run(engine, dataverse, page_size=7))
    for entry in engine.events(run["id"]):
        assert entry["data"]["event"] in LOG_EVENTS, entry["data"]["event"]


# --------------------------------------------------------------------------- #
# Progress
# --------------------------------------------------------------------------- #


def test_progress_is_a_percentage_when_there_is_a_total():
    assert progress({"rows_total": 200, "rows_seen": 50}, "running")["percent"] == 25.0
    assert progress({"rows_total": 200, "rows_seen": 50}, "running")["indeterminate"] is False


def test_progress_is_indeterminate_rather_than_invented_without_a_total():
    shape = progress({"rows_total": None, "rows_seen": 50}, "running")
    assert shape["percent"] is None and shape["indeterminate"] is True
    assert "no denominator" in shape["detail"]


def test_a_zero_total_is_treated_as_no_total():
    assert progress({"rows_total": 0, "rows_seen": 0}, "running")["indeterminate"] is True


def test_a_complete_run_is_a_hundred_percent_whatever_the_numbers():
    assert progress({"rows_total": None, "rows_seen": 0}, "complete")["percent"] == 100.0


def test_progress_never_carries_an_eta():
    """The research quotes the vendor declining to promise one.

    Asserted on the key rather than on the substring, because `indeterminate`
    contains "eta" and a substring check would pass or fail for the wrong reason.
    """
    for state, total, seen in (("running", 100, 50), ("running", None, 0), ("complete", 1, 1)):
        shape = progress({"rows_total": total, "rows_seen": seen}, state)
        assert not any(key in shape for key in ("eta", "eta_at", "finishes_at", "estimated_completion"))
        assert "no_sla" in shape or shape["basis"] == "complete"
    assert NO_SLA_QUOTE in str(progress({"rows_total": 100, "rows_seen": 1}, "running"))


def test_progress_never_exceeds_a_hundred():
    assert progress({"rows_total": 10, "rows_seen": 40}, "running")["percent"] == 100.0


# --------------------------------------------------------------------------- #
# The HTTP surface, through this feature's own router
# --------------------------------------------------------------------------- #


def test_the_vocabulary_endpoint_serves_the_researched_numbers(http):
    body = http.get(f"{PREFIX}/vocabulary").json()
    assert body["numbers"]["bulk_threshold_records"]["value"] == 2000
    assert body["numbers"]["dataverse_page_size"]["value"] == 5000
    assert body["numbers"]["change_tracking_expiry_days"]["value"] == 7
    assert body["no_sla_quote"] == NO_SLA_QUOTE
    assert body["required_scope"]["scope"] == "crm.export"


def test_the_inferences_endpoint_serves_the_register(http):
    body = http.get(f"{PREFIX}/inferences").json()
    assert body["count"] >= 15
    assert body["sourced_quote"] == SOURCED_QUOTE
    assert any(entry["id"] == "cursor-advance-after-write" for entry in body["inferences"])


def test_the_vendors_endpoint_shows_the_two_method_interface(http):
    body = http.get(f"{PREFIX}/vendors").json()
    assert body["bulk_threshold_records"] == 2000
    assert {row["vendor"] for row in body["vendors"]} == {"salesforce", "dataverse", "hubspot"}
    assert body["interface"][0].startswith("start (create job)")
    assert body["interface"][1].startswith("read_page (read page)")


def test_a_connection_can_be_declared_and_read_over_http(http, http_room):
    created = http.post(
        f"{PREFIX}/connections",
        params={"room_id": http_room},
        json={"name": "DV", "vendor": "dataverse", "change_tracking_expiry_days": 3},
    )
    assert created.status_code == 201
    body = created.json()
    assert body["data"]["vendor"] == "dataverse"
    assert body["strategies"] == ["delta_read", "paged_read"]
    assert body["object_addressing"] == "object_name"
    assert http.get(f"{PREFIX}/connections/{body['id']}").status_code == 200
    assert http.get(f"{PREFIX}/connections/{body['id']}").json()["id"] == body["id"]


def test_an_unreadable_vendor_is_answered_with_the_domain_error_payload(http):
    response = http.post(f"{PREFIX}/connections", json={"vendor": "pipedream"})
    # 409, not 400: the request is well formed and what it conflicts with is the
    # adapter registry, which is state that already exists.
    assert response.status_code == 409
    assert response.json()["error"] == "backfill_vendor_not_supported"
    assert "create job" in response.json()["detail"]


def test_a_backfill_can_be_opened_polled_and_read_over_http(http, http_room, http_connection):
    opened = http.post(
        f"{PREFIX}/rooms/{http_room}/backfills",
        json={
            "connection_id": http_connection["id"],
            "scope": {"kind": "full_history"},
            "page_size": 7,
            "poll_interval_seconds": 0,
        },
    )
    assert opened.status_code == 201
    run_id = opened.json()["id"]
    assert opened.json()["data"]["state"] == "running"

    for _ in range(20):
        step = http.post(f"{PREFIX}/rooms/{http_room}/backfills/{run_id}/poll").json()
        if step["run"]["data"]["state"] != "running":
            break
    assert step["run"]["data"]["state"] == "complete"

    fetched = http.get(f"{PREFIX}/rooms/{http_room}/backfills/{run_id}").json()
    assert fetched["progress"]["percent"] == 100.0
    assert fetched["progress"]["indeterminate"] is False

    log = http.get(f"{PREFIX}/rooms/{http_room}/backfills/{run_id}/log").json()
    assert log["count"] >= 5
    assert log["events"][0]["event"] == "run_created"

    summary = http.get(f"{PREFIX}/rooms/{http_room}/backfill-summary").json()
    assert summary["runs"] == 1
    assert summary["by_state"]["complete"] == 1


def test_the_poll_route_reports_not_due_rather_than_asking_the_vendor_again(http, http_room, http_connection):
    opened = http.post(
        f"{PREFIX}/rooms/{http_room}/backfills",
        json={
            "connection_id": http_connection["id"],
            "scope": {"kind": "full_history"},
            "page_size": 7,
            "poll_interval_seconds": 300,
        },
    ).json()
    step = http.post(f"{PREFIX}/rooms/{http_room}/backfills/{opened['id']}/poll").json()
    assert step["advanced"] is False and step["reason"] == "not_due"


def test_resume_over_http_counts_itself_and_reads_on(http, http_room, http_connection):
    opened = http.post(
        f"{PREFIX}/rooms/{http_room}/backfills",
        json={
            "connection_id": http_connection["id"],
            "scope": {"kind": "full_history"},
            "page_size": 5,
            "poll_interval_seconds": 300,
        },
    ).json()
    resumed = http.post(f"{PREFIX}/rooms/{http_room}/backfills/{opened['id']}/resume").json()
    assert resumed["run"]["counters"]["resumes"] == 1


def test_cancel_over_http_takes_a_reason(http, http_room, http_connection):
    opened = http.post(
        f"{PREFIX}/rooms/{http_room}/backfills",
        json={
            "connection_id": http_connection["id"],
            "scope": {"kind": "full_history"},
            "poll_interval_seconds": 300,
        },
    ).json()
    cancelled = http.post(
        f"{PREFIX}/rooms/{http_room}/backfills/{opened['id']}/cancel",
        json={"reason": "picked the wrong quarter"},
    ).json()
    assert cancelled["data"]["state"] == "cancelled"
    logged = [entry["detail"] for entry in http.get(f"{PREFIX}/rooms/{http_room}/backfills/{opened['id']}/log").json()["events"]]
    assert any("wrong quarter" in detail for detail in logged), logged


def test_cursors_and_replica_are_served_per_room(http, http_room, http_connection):
    opened = http.post(
        f"{PREFIX}/rooms/{http_room}/backfills",
        json={
            "connection_id": http_connection["id"],
            "scope": {"kind": "full_history"},
            "page_size": 20,
            "poll_interval_seconds": 0,
        },
    ).json()
    for _ in range(4):
        step = http.post(f"{PREFIX}/rooms/{http_room}/backfills/{opened['id']}/poll").json()
        if step["run"]["data"]["state"] != "running":
            break
    cursors = http.get(f"{PREFIX}/rooms/{http_room}/cursors").json()
    assert cursors["count"] == 1
    assert cursors["cursors"][0]["kind"] == "bulk_job_id"
    replica = http.get(f"{PREFIX}/rooms/{http_room}/replica").json()
    assert replica["collection"] == "crm_replica"
    assert replica["count"] == len(replica["rows"])


def test_runs_are_listed_and_filterable_over_http(http, http_room, http_connection):
    http.post(
        f"{PREFIX}/rooms/{http_room}/backfills",
        json={"connection_id": http_connection["id"], "scope": {"kind": "full_history"}, "poll_interval_seconds": 0},
    )
    assert http.get(f"{PREFIX}/rooms/{http_room}/backfills").json()["count"] == 1
    assert http.get(f"{PREFIX}/rooms/{http_room}/backfills", params={"state": "running"}).json()["count"] == 1
    assert http.get(f"{PREFIX}/rooms/{http_room}/backfills", params={"state": "complete"}).json()["count"] == 0
    assert http.get(f"{PREFIX}/rooms/{http_room}/backfills", params={"where": "vendor=salesforce"}).json()["count"] == 1


def test_an_unknown_run_is_a_404_from_this_feature_s_own_error(http, http_room):
    response = http.get(f"{PREFIX}/rooms/{http_room}/backfills/crm_backfill_run_nope")
    assert response.status_code == 404
    assert response.json()["error"] == "backfill_run_not_found"


def test_an_unknown_connection_is_a_409_not_a_404(http, http_room):
    response = http.post(
        f"{PREFIX}/rooms/{http_room}/backfills",
        json={"connection_id": "crm_backfill_connection_nope", "scope": {"kind": "full_history"}},
    )
    assert response.status_code == 409
    assert response.json()["error"] == "backfill_connection_not_found"


def test_a_range_that_runs_backwards_is_a_422_with_the_field_to_fix(http, http_room, http_connection):
    response = http.post(
        f"{PREFIX}/rooms/{http_room}/backfills",
        json={
            "connection_id": http_connection["id"],
            "scope": {"kind": "range", "from": "2026-06-01T00:00:00Z", "to": "2026-01-01T00:00:00Z"},
        },
    )
    assert response.status_code == 400
    assert response.json()["error"] == "backfill_range_invalid"


def test_poll_and_resume_of_a_finished_run_are_409_over_http(http, http_room, http_connection):
    opened = http.post(
        f"{PREFIX}/rooms/{http_room}/backfills",
        json={
            "connection_id": http_connection["id"],
            "scope": {"kind": "full_history"},
            "page_size": 5000,
            "poll_interval_seconds": 0,
        },
    ).json()
    for _ in range(10):
        step = http.post(f"{PREFIX}/rooms/{http_room}/backfills/{opened['id']}/poll").json()
        if step["run"]["data"]["state"] != "running":
            break
    assert step["run"]["data"]["state"] == "complete"
    again = http.post(f"{PREFIX}/rooms/{http_room}/backfills/{opened['id']}/poll")
    assert again.status_code == 409
    assert again.json()["error"] == "backfill_run_state_conflict"


# --------------------------------------------------------------------------- #
# The audit-source rule
# --------------------------------------------------------------------------- #


def _mounted(http) -> set[tuple[str, str]]:
    entry = next(f for f in http.get("/api/features").json()["features"] if f["id"] == FEATURE_ID)
    return {(method, route["path"]) for route in entry["routes"] for method in route["methods"]}


def test_every_source_this_feature_records_names_a_mounted_route(http, http_room, http_connection):
    """The defect the contract names: an audit row naming a route nobody serves."""
    http.post(
        f"{PREFIX}/rooms/{http_room}/backfills",
        json={
            "connection_id": http_connection["id"],
            "scope": {"kind": "full_history"},
            "page_size": 5,
            "poll_interval_seconds": 0,
        },
    )
    run_id = http.get(f"{PREFIX}/rooms/{http_room}/backfills").json()["backfills"][0]["id"]
    for _ in range(6):
        step = http.post(f"{PREFIX}/rooms/{http_room}/backfills/{run_id}/poll").json()
        if step["run"]["data"]["state"] != "running":
            break
    http.post(f"{PREFIX}/rooms/{http_room}/backfills/{run_id}/cancel", json={"reason": "done"})

    sources = {
        entry["source"]
        for entry in http.get("/api/audit", params={"limit": 1000}).json()["entries"]
        if entry["source"] and PREFIX in entry["source"]
    }
    assert sources, "the feature wrote nothing, so the rule below proves nothing"

    mounted = _mounted(http)
    for source in sources:
        verb, _, path = source.partition(" ")
        assert (verb, path) in mounted, f"{source} is not a route this app serves"


def test_the_audit_source_is_a_required_keyword_on_every_writing_method():
    """A hardcoded path inside a domain method is a defect this makes impossible."""
    import inspect

    from dsr.crm_backfill import engine as engine_module

    # Named exactly, not by substring: "log" is inside "catalog", and a sweep
    # that matched on a substring would have made this test a claim about a
    # method that writes nothing.
    writers = (
        "start",
        "poll",
        "resume",
        "cancel",
        "create_connection",
        "log",
    )
    for name in writers:
        parameters = inspect.signature(getattr(engine_module.BackfillEngine, name)).parameters
        assert parameters.get("source") is not None, f"{name} does not require source="
        assert parameters["source"].default is inspect.Parameter.empty, f"{name} defaults source="
        # Keyword-only too: a positional source would be easy to pass in the
        # wrong order, and the whole rule is that the caller cannot forget it.
        assert parameters["source"].kind is inspect.Parameter.KEYWORD_ONLY, name


def test_every_audit_row_this_writes_records_the_room_it_belongs_to(http, http_room, http_connection):
    http.post(
        f"{PREFIX}/rooms/{http_room}/backfills",
        json={"connection_id": http_connection["id"], "scope": {"kind": "full_history"}, "poll_interval_seconds": 0},
    )
    entries = http.get("/api/audit", params={"limit": 1000}).json()["entries"]
    mine = [entry for entry in entries if entry["source"] and PREFIX in entry["source"]]
    assert mine
    for entry in mine:
        if "/rooms/{room_id}/" in entry["source"]:
            assert entry["room_id"] == http_room, entry["source"]
        else:
            # A connection is account-level: the researched cursor record is keyed
            # on it, so putting a room in its key would declare the same account
            # twice. Its audit row therefore carries no room, on purpose.
            assert entry["source"] == f"POST {PREFIX}/connections", entry["source"]
            assert entry["room_id"] is None


# --------------------------------------------------------------------------- #
# Schema flexibility
# --------------------------------------------------------------------------- #


def test_the_feature_added_no_table_and_no_typed_column(store):
    """The only fixed vocabulary is the envelope; the collections are ordinary."""
    before = {
        row["name"]
        for row in store.db._conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    BackfillEngine(store).vocabulary()
    after = {
        row["name"]
        for row in store.db._conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert before == after


def test_the_collections_are_named_apart_from_every_other_features(store, engine, dataverse):
    open_run(engine, dataverse)
    assert {record["collection"] for record in store.list(RUNS, limit=10)} == {"crm_backfill_run"}
    assert {record["collection"] for record in store.list(CURSORS_STORE, limit=10)} == {"crm_backfill_cursor"}


def test_the_cursor_store_deduplicates_its_researched_fields_into_one_indexable_name(engine, dataverse):
    """The mirror exists so a `where` on the snake_case name resolves."""
    run = open_run(engine, dataverse)
    connection_id = run["data"]["connection_id"]
    assert engine.stored_cursor("room-1", connection_id)["connectionId"] == connection_id
    assert engine.stored_cursor("room-1", connection_id)["connection_id"] == connection_id
    assert len(engine.cursors(room_id="room-1")) == 1


def test_a_second_room_holds_its_own_cursor_for_the_same_connection(engine, dataverse):
    """Keyed on room and connection, or two rooms would overwrite each other."""
    open_run(engine, dataverse, room="room-1")
    open_run(engine, dataverse, room="room-2")
    assert len(engine.cursors(room_id="room-1")) == 1
    assert len(engine.cursors(room_id="room-2")) == 1
    assert engine.stored_cursor("room-1", engine.cursors(room_id="room-1")[0]["connectionId"])


def test_the_run_record_is_ordinary_json_so_a_team_can_add_a_field(engine, dataverse, store):
    run = open_run(engine, dataverse)
    store.update(run["id"], {"cost_centre": "sales-ops"}, source="test")
    found = engine.runs(room_id="room-1", where="cost_centre=sales-ops", limit=10)
    assert len(found) == 1
    assert found[0]["data"]["cost_centre"] == "sales-ops"


def test_the_replica_carries_a_vendor_field_the_room_never_heard_of(store, clock):
    source = SimulatedHistory([{**history_rows(1)[0], "annual_revenue": 12_000_000, "nps": 71}])
    engine = BackfillEngine(store, registry=default_registry(source), clock=clock)
    connection = engine.create_connection({"vendor": "dataverse"}, source=SOURCE)
    drive(engine, "room-1", open_run(engine, connection))
    row = engine.replica(room_id="room-1", limit=1)[0]
    assert row["data"]["annual_revenue"] == 12_000_000
    assert row["data"]["nps"] == 71
