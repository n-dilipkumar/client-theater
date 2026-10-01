"""Tests for WF-040: surface partial failures, and reject invalid writes before commit.

The claims under test come from
``docs/research/digital-sales-room-workflows/wf/WF-040.md``, whose evidence is
three vendors' own error contracts and whose flow is five steps::

    1. A sync batch runs and some rows fail.
    2. Instead of failing the whole batch, the connector asks for per-record
       outcomes (multi-status / continue-on-error).
    3. The room's Sync log lists each failed row with a human-readable reason and
       the offending property.
    4. Admin clicks a failed row -> Field-level error detail (which property, what
       was sent, what was expected).
    5. Admin fixes the mapping or the data, and clicks Retry failed rows only -
       successes are not re-sent.

So the suite is organised around those five steps and around the transformation
the research describes - "vendor-specific error shapes collapse into one room error
model" - and it is explicit about which assertions are sourced and which are this
build's reading:

* **step two, per vendor** - HubSpot's ``207 Multi-Status`` correlated by
  ``objectWriteTraceId``, Dataverse's ``200 OK`` that is *not* a success with
  failures in the body and a ``HelpLink`` annotation, Salesforce's request-level
  ``400`` and ``403 REQUEST_LIMIT_EXCEEDED``;
* **the transformation** - the researched error model ``{retryable, field, code,
  message, docLink}``, and the four things the room refuses to get wrong: a 207 is
  not a failure, a Dataverse 200 is not a success, a row the response ignores is
  not a success, and an error with no correlation is not dropped;
* **the classification** - the two researched retryable classes, every entry's
  stated basis, and the conservative default for a code nobody has heard of;
* **the automation** - what drains, what is scheduled, what expires into
  ``needs_action``, and what no amount of draining touches;
* **steps three to five over the store and over HTTP** - the Sync log, the
  field-level detail, retry-failed-rows-only, the audit-source rule, and the
  guarantee that a pre-flight refusal writes nothing at all.

The inferences are not tested as if they were research. They are tested as the
*mechanism* each entry describes - a default that a routing rule reverses, a bound
that terminates, a fallback that a PATCH replaces - and ``INFERENCES`` is asserted
against what the code actually does, so the list cannot drift away from the
behaviour silently.
"""

from __future__ import annotations

import random
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dsr.api import app
from dsr.db.audited import AuditedDatabase
from dsr.features import load_feature
from dsr.partial_failures import (
    ANY,
    AUTOMATION_QUOTE,
    BACKOFF_LABEL,
    BASE_BACKOFF_SECONDS,
    CLASSIFICATION,
    CONNECTORS,
    CORRELATION,
    DEFAULT_BASIS,
    DEFAULT_PREFLIGHT_RULES,
    DISPOSITIONS,
    DISPOSITION_LABELS,
    DISPOSITION_MEANING,
    ERROR_MODEL_KEYS,
    EXTENSIBILITY_QUOTE,
    FUTURE_TOLERANCE_SECONDS,
    HELP_LINK_ANNOTATION,
    HISTORY_LIMIT,
    HUBSPOT_VALIDATION_ENFORCEMENT,
    INFERENCES,
    MAX_ATTEMPTS,
    MAX_BACKOFF_SECONDS,
    MULTI_STATUS,
    OVERRIDE_MATCH_KEYS,
    OVERRIDE_THEN_KEYS,
    PER_RECORD_REQUEST,
    PER_RECORD_STATUS,
    PREFLIGHT_KINDS,
    ROW_COLLECTION,
    ROW_STATUSES,
    RUN_COLLECTION,
    RULES_COLLECTION,
    RULES_RECORD_ID,
    SENT_PREVIEW_CHARS,
    SOURCED_AUTOMATION,
    SOURCED_EXTENSIBILITY,
    SOURCED_GAP,
    InvalidPayload,
    InvalidRule,
    PartialFailureError,
    SyncLog,
    UnknownConnector,
    UnknownRoom,
    UnknownRow,
    UnknownRun,
    backoff_seconds,
    can_retry,
    check_batch,
    check_row,
    classify,
    covers,
    dataverse_field_from_message,
    describe_inferences,
    disposition_for,
    effective_rules,
    expectation_text,
    expectations_for,
    help_link,
    is_retryable,
    merge_rules,
    normalise,
    preflight_defaults,
    retry_plan,
    validate_rule,
    validate_routing_rule,
    vocabulary,
)
from dsr.partial_failures import validation as validation_module
from dsr.partial_failures.normalise import DEFAULT_CLASSIFICATION as UNRECOGNISED
from dsr.store import RecordStore

#: The feature's own prefix. Written out here rather than imported, so a renamed
#: prefix fails a test instead of following silently.
PREFIX = "/api/wf-040"

FEATURE_ID = "wf-040-surface-partial-failures-and-reject-in"
MODULE = "wf040_surface_partial_failures_and_reject_in"

#: Every route this feature mounts. A change to the surface has to be made here
#: deliberately, which is the point of asserting a set.
ROUTES = {
    ("GET", "/vocabulary"),
    ("GET", "/connectors"),
    ("GET", "/inferences"),
    ("GET", "/rules"),
    ("PATCH", "/rules"),
    ("POST", "/validate"),
    ("GET", "/runs"),
    ("POST", "/runs"),
    ("GET", "/runs/{run_id}"),
    ("POST", "/runs/{run_id}/retry"),
    ("GET", "/queue"),
    ("POST", "/queue/drain"),
    ("GET", "/rooms/{room_id}/sync-log"),
    ("GET", "/rooms/{room_id}/rows/{row_id}"),
}

NOW = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)

ROOM = {
    "name": "Northwind Traders — Enterprise Evaluation",
    "account": "Northwind Traders",
    "owner": "dana",
    "stage": "evaluation",
}

#: The researched refusal, quoted in full, in the shape the research quotes it.
DATAVERSE_VALIDATION_MESSAGE = (
    "A validation error occurred. The length of the 'subject' attribute of the 'task' entity "
    "exceeded the maximum allowed length of '200'."
)

#: The researched multi-status response, in the shape the research quotes it:
#: a numErrors of 1 and a context carrying the objectWriteTraceId.
HUBSPOT_TRACE = "549b1c2a9351"

DOC_LINK = "https://learn.microsoft.com/power-apps/developer/data-platform/webapi"


# --------------------------------------------------------------------------- #
# Fixtures and helpers
# --------------------------------------------------------------------------- #


@pytest.fixture()
def store(tmp_path):
    db = AuditedDatabase(tmp_path / "wf040.db", mirror_dir=tmp_path / "mirror")
    yield RecordStore(db)
    db.close()


@pytest.fixture()
def room(store):
    return store.create("room", ROOM, actor="dana")


@pytest.fixture()
def log(store):
    return SyncLog(store)


@pytest.fixture()
def http(monkeypatch):
    """A client over a temporary database, on the shared app.

    The plugin host mounts every feature onto one app, so this exercises the real
    mounted route rather than a private test app - which is what makes the
    audit-source assertion below meaningful.
    """
    tmp = tempfile.TemporaryDirectory()
    monkeypatch.setenv("DSR_DB_PATH", str(Path(tmp.name) / "wf040.db"))
    monkeypatch.setenv("DSR_AUDIT_DIR", str(Path(tmp.name) / "audit"))
    monkeypatch.setattr("dsr.api.FRONTEND_DIST", Path(tmp.name) / "absent-frontend")
    with TestClient(app) as client:
        yield client
    tmp.cleanup()


@pytest.fixture()
def http_room(http):
    return http.post("/api/records/room", json=ROOM).json()


def rows(*specs):
    """Input rows, in the shape a connector hands the room."""
    return [
        {"row_key": key, "entity": entity, "trace_id": trace, "values": values}
        for key, entity, trace, values in specs
    ]


def hubspot_row(index, email="a.buyer@northwind.example"):
    return (f"hs-{index}", "contact", f"hs-t{index}", {"email": email, "lastname": "Buyer"})


def hubspot_success(trace, record_id=None):
    entry = {"status": "success", "context": {"objectWriteTraceId": [trace]}}
    if record_id:
        entry["id"] = record_id
    return entry


def hubspot_failure(trace, message, code="VALIDATION_ERROR", field=None, code_inner="INVALID_VALUE"):
    error = {"message": message, "code": code_inner}
    if field:
        error["in"] = field
    return {
        "status": "error",
        "category": code,
        "message": message,
        "context": {"objectWriteTraceId": [trace]},
        "errors": [error],
    }


def dataverse_ok(record_id="task-1"):
    return {"status": 204, "body": {"id": record_id}}


def dataverse_error(status, code, message, link=None):
    body = {"error": {"code": code, "message": message}}
    if link:
        body[HELP_LINK_ANNOTATION] = {
            "HelpLink": link,
            "Description": "Dataverse guidance for this error",
        }
    return {"status": status, "body": body}


def salesforce_result(success, error=None, record_id=None):
    entry = {"success": success}
    if record_id:
        entry["id"] = record_id
    if error:
        entry["errors"] = [error]
    return entry


def rate_limited():
    return salesforce_result(
        False, {"statusCode": "429", "errorCode": "REQUEST_LIMIT_EXCEEDED", "message": "no"}
    )


def run_payload(connector, input_rows, status, body, room_id=None, **extra):
    payload = {
        "connector": connector,
        "rows": input_rows,
        "vendor": {"status": status, "body": body},
        **extra,
    }
    if room_id:
        payload["room_id"] = room_id
    return payload


def record(log, room_id, payload, *, at=NOW, actor="dana", source="POST test"):
    return log.record_run(payload, room_id=room_id, actor=actor, source=source, now=at)


def post_run(http, room_id, connector, input_rows, status, body, **params):
    """POST one run for a room, the way the connector seam would.

    The room goes in the body rather than the query string so the test reads as the
    thing it is testing; the route accepts either, and a separate test covers the
    query winning over the body.
    """
    payload = run_payload(connector, input_rows, status, body, room_id=room_id)
    return http.post(f"{PREFIX}/runs", json=payload, params=params or None)


def retry_via(http, run_id, body, **params):
    return http.post(
        f"{PREFIX}/runs/{run_id}/retry", json={"vendor": body}, params=params or None
    )


def an_hour_hence():
    return (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()


# --------------------------------------------------------------------------- #
# Plugin registration
# --------------------------------------------------------------------------- #


def test_feature_is_discovered_and_mounted_without_editing_the_host(http):
    entry = next(f for f in http.get("/api/features").json()["features"] if f["id"] == FEATURE_ID)

    assert entry["prefix"] == PREFIX
    assert entry["ticket"] == "WF-040"
    assert entry["exception_handlers"] == ["PartialFailureError"]
    assert len(entry["routes"]) == len(ROUTES)


def test_the_mounted_route_set_is_exactly_the_one_this_suite_expects(http):
    entry = next(f for f in http.get("/api/features").json()["features"] if f["id"] == FEATURE_ID)
    served = {(method, route["path"]) for route in entry["routes"] for method in route["methods"]}
    assert served == {(method, f"{PREFIX}{path}") for method, path in ROUTES}


def test_no_core_route_and_no_other_feature_answers_under_the_prefix(http):
    served = {
        (method, route["path"])
        for feature in http.get("/api/features").json()["features"]
        for route in feature["routes"]
        for method in route["methods"]
    }
    mine = {key for key in served if key[1].startswith(PREFIX)}
    others = {key for key in served if not key[1].startswith(PREFIX)}

    assert len(mine) == len(ROUTES)
    assert not mine & others


def test_feature_module_does_not_import_the_shared_app():
    """A guard exists in test_features.py; this states the reason locally.

    Checked against the parsed import list rather than the raw text, so a docstring
    that *mentions* ``dsr.api`` - as this one does, to explain why the import is not
    there - does not read as an import of it.
    """
    import ast

    module = load_feature(MODULE)
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))

    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")

    assert not {name for name in imported if name == "dsr.api" or name.startswith("dsr.api.")}
    assert "dsr.deps" in imported


def test_frontend_descriptor_id_matches_the_backend_feature_id():
    descriptor = (
        Path(__file__).resolve().parents[2] / "frontend" / "src" / "features" / FEATURE_ID / "index.jsx"
    )
    text = descriptor.read_text(encoding="utf-8")
    module = load_feature(MODULE)

    assert f"id: {module.FEATURE['id']!r}" in text
    assert f"label: {module.FEATURE['nav'][0]['label']!r}" in text


def test_the_frontend_only_calls_its_own_prefix():
    """A page that reaches another feature's routes is a coupling the host cannot see."""
    folder = Path(__file__).resolve().parents[2] / "frontend" / "src" / "features" / FEATURE_ID
    others = sorted(
        path.name
        for path in folder.parent.iterdir()
        if path.is_dir() and path.name != FEATURE_ID
    )

    for name in ("api.js", "SyncLog.jsx", "index.jsx", "icons.jsx", "primitives.jsx"):
        text = (folder / name).read_text(encoding="utf-8")
        borrowed = [
            line.strip()
            for line in text.splitlines()
            if "apiRequest('/" in line and "/wf-040" not in line
        ]
        assert borrowed == [], f"{name} calls {borrowed} instead of its own prefix"
        for other in others:
            assert f"{other}/" not in text, f"{name} references {other}"


def test_the_room_scoped_routes_take_their_room_in_the_path():
    """The brief asks for room-scoped paths to stay room-scoped."""
    paths = {route.path for route in load_feature(MODULE).router.routes}

    assert f"{PREFIX}/rooms/{{room_id}}/sync-log" in paths
    assert f"{PREFIX}/rooms/{{room_id}}/rows/{{row_id}}" in paths
    # The only nesting is a room holding its own rows, and no path reaches past two
    # parameters - which is the shape that makes a shared prefix collide later.
    assert not [path for path in paths if path.count("{") > 2]
    allowed = {"{room_id}", "{row_id}", "{run_id}"}
    stray = [
        segment
        for path in paths
        for segment in path.split("/")
        if segment.startswith("{") and segment not in allowed
    ]
    assert stray == []


# --------------------------------------------------------------------------- #
# The sourced vocabulary
# --------------------------------------------------------------------------- #


def test_there_are_exactly_three_connectors():
    assert CONNECTORS == ("hubspot", "dataverse", "salesforce")


def test_every_connector_declares_the_request_that_yields_per_record_outcomes():
    for name in CONNECTORS:
        descriptor = PER_RECORD_REQUEST[name]
        assert descriptor["request_notes"], name
        assert descriptor["gap"], f"{name} must carry the gap the research states about it"


def test_the_hubspot_gap_is_stated_verbatim_and_the_descriptor_obeys_it():
    assert PER_RECORD_REQUEST["hubspot"]["endpoint_scope"] == "batch_create"
    gap = PER_RECORD_REQUEST["hubspot"]["gap"]
    assert "batch create" in gap
    assert "batch/upsert" in gap and "batch/update" in gap


def test_the_dataverse_request_carries_both_researched_headers():
    headers = PER_RECORD_REQUEST["dataverse"]["request_headers"]["Prefer"]
    assert "odata.continue-on-error" in headers
    assert 'odata.include-annotations="*"' in headers


def test_the_partial_statuses_are_the_ones_the_research_quotes():
    assert PER_RECORD_STATUS["hubspot"]["partial"] == MULTI_STATUS == 207
    assert PER_RECORD_STATUS["dataverse"]["partial"] == 200
    assert "200 OK whether or not" in PER_RECORD_STATUS["dataverse"]["note"]
    assert PER_RECORD_STATUS["salesforce"]["malformed"] == 400


def test_only_dataverse_is_documented_to_produce_a_doc_link():
    annotations = {entry["id"]: entry["doc_link"]["annotation"] for entry in vocabulary()["connectors"]}
    assert annotations["dataverse"] == HELP_LINK_ANNOTATION
    assert annotations["hubspot"] is None
    assert annotations["salesforce"] is None


def test_only_hubspots_correlation_is_marked_sourced():
    flags = {name: CORRELATION[name]["sourced"] for name in CONNECTORS}
    assert flags == {"hubspot": True, "dataverse": False, "salesforce": False}
    assert CORRELATION["hubspot"]["key"] == "objectWriteTraceId"


def test_the_hubspot_enforcement_fact_carries_its_researched_date():
    assert HUBSPOT_VALIDATION_ENFORCEMENT["api_version"] == "/2026-09/"
    assert HUBSPOT_VALIDATION_ENFORCEMENT["ga_date"] == "2026-09-08"
    assert "enforce admin-configured validation rules" in HUBSPOT_VALIDATION_ENFORCEMENT["quote"]


def test_there_are_exactly_two_row_statuses_and_three_dispositions():
    assert ROW_STATUSES == ("succeeded", "failed")
    assert DISPOSITIONS == ("queued", "needs_action", "resolved")


def test_every_disposition_is_labelled_and_explained():
    for value in DISPOSITIONS:
        assert DISPOSITION_LABELS[value]
        assert DISPOSITION_MEANING[value]


def test_the_two_researched_quotations_are_carried_verbatim():
    assert "Retry queue drains automatically for retryable classes (rate limit, locked)" in SOURCED_AUTOMATION
    assert "{retryable, field, code, message, docLink}" in SOURCED_EXTENSIBILITY
    assert "batch create" in SOURCED_GAP
    assert AUTOMATION_QUOTE == SOURCED_AUTOMATION
    assert EXTENSIBILITY_QUOTE == SOURCED_EXTENSIBILITY


def test_the_error_model_is_exactly_the_five_keys_the_research_names():
    assert list(ERROR_MODEL_KEYS) == ["retryable", "field", "code", "message", "doc_link"]


def test_vocabulary_endpoint_serves_every_published_name(http):
    body = http.get(f"{PREFIX}/vocabulary").json()

    assert body["connector_ids"] == list(CONNECTORS)
    assert body["row_statuses"] == list(ROW_STATUSES)
    assert body["dispositions"] == list(DISPOSITIONS)
    assert body["collections"] == {
        "run": RUN_COLLECTION,
        "row": ROW_COLLECTION,
        "rules": RULES_COLLECTION,
    }
    assert body["history_limit"] == HISTORY_LIMIT


def test_the_connectors_endpoint_serves_the_researched_request_per_vendor(http):
    body = http.get(f"{PREFIX}/connectors").json()

    assert body["ids"] == list(CONNECTORS)
    by_id = {entry["id"]: entry for entry in body["connectors"]}
    assert by_id["hubspot"]["status"]["partial"] == 207
    assert by_id["hubspot"]["per_record_request"]["endpoint_scope"] == "batch_create"
    headers = by_id["dataverse"]["per_record_request"]["request_headers"]["Prefer"]
    assert "odata.continue-on-error" in headers
    assert 'odata.include-annotations="*"' in headers
    notes = " ".join(by_id["salesforce"]["per_record_request"]["request_notes"])
    assert "allOrNone: false" in notes
    assert by_id["hubspot"]["per_record_request"]["gap"]
    assert body["error_model_keys"] == list(ERROR_MODEL_KEYS)


# --------------------------------------------------------------------------- #
# Step two: HubSpot's 207 Multi-Status, correlated by objectWriteTraceId
# --------------------------------------------------------------------------- #


def test_hubspot_207_is_a_success_status_not_a_failure():
    """The researched sentence: 207 means there are *different* statuses in the batch."""
    outcome = normalise(
        "hubspot",
        207,
        {"numErrors": 1, "results": [hubspot_failure("t1", "bad"), hubspot_success("t2")]},
        rows(("a", "contact", "t1", {}), ("b", "contact", "t2", {})),
    )

    assert outcome.http_status == 207
    assert outcome.per_record is True
    assert outcome.failed == 1
    assert outcome.succeeded == 1


def test_hubspot_keys_a_result_to_its_row_through_object_write_trace_id():
    outcome = normalise(
        "hubspot",
        207,
        {"numErrors": 1, "results": [hubspot_failure("t-b", "bad"), hubspot_success("t-a", "901")]},
        rows(("row-a", "contact", "t-a", {}), ("row-b", "contact", "t-b", {})),
    )

    by_key = {row.row_key: row for row in outcome.rows}
    assert by_key["row-a"].status == "succeeded"
    assert by_key["row-a"].vendor_record_id == "901"
    assert by_key["row-a"].correlation == "t-a"
    assert by_key["row-a"].correlation_basis == "correlation"
    assert by_key["row-b"].status == "failed"
    assert by_key["row-b"].error.code == "VALIDATION_ERROR"


def test_hubspot_takes_the_first_entry_of_the_object_write_trace_id_list():
    outcome = normalise(
        "hubspot",
        207,
        {"numErrors": 1, "results": [hubspot_failure(HUBSPOT_TRACE, "bad")]},
        rows(("row-a", "contact", HUBSPOT_TRACE, {})),
    )

    assert outcome.rows[0].correlation == HUBSPOT_TRACE
    assert outcome.rows[0].error.correlation == HUBSPOT_TRACE


def test_a_hubspot_result_whose_trace_id_names_no_row_is_kept_unattributed():
    outcome = normalise(
        "hubspot",
        207,
        {"numErrors": 1, "results": [hubspot_failure("t-nobody", "bad")]},
        rows(("row-a", "contact", "t-a", {})),
    )

    assert len(outcome.unattributed) == 1
    assert outcome.unattributed[0].correlation == "t-nobody"
    assert outcome.unattributed[0].code == "VALIDATION_ERROR"
    # The row itself is described by nothing either, so it is the silent-row case
    # rather than a guessed attribution.
    assert outcome.rows[0].status == "failed"
    assert outcome.rows[0].error.code == "NO_PER_RECORD_OUTCOME"


def test_a_hubspot_result_with_no_trace_id_is_not_guessed_into_a_row():
    """objectWriteTraceId is the documented key; a result without one names no row."""
    naked = {"status": "error", "category": "VALIDATION_ERROR", "message": "x"}
    outcome = normalise(
        "hubspot",
        207,
        {"numErrors": 1, "results": [naked]},
        rows(("row-a", "contact", "t-a", {}), ("row-b", "contact", "t-b", {})),
    )

    assert len(outcome.unattributed) == 1
    assert [row.status for row in outcome.rows] == ["failed", "failed"]
    assert {row.error.code for row in outcome.rows} == {"NO_PER_RECORD_OUTCOME"}


def test_a_hubspot_207_with_no_results_array_fails_every_row_rather_than_claiming_success():
    outcome = normalise("hubspot", 207, {"status": "error"}, rows(("a", "contact", "t", {})))

    assert outcome.per_record is False
    assert outcome.failed == 1
    assert outcome.rows[0].error.code == "MULTI_STATUS_WITHOUT_RESULTS"
    assert any("no results array" in note for note in outcome.notes)


def test_a_hubspot_200_with_no_results_means_every_row_was_accepted():
    outcome = normalise("hubspot", 200, {"status": "success"}, rows(("a", "contact", "t", {})))

    assert outcome.per_record is False
    assert outcome.succeeded == 1
    assert outcome.failed == 0


def test_a_hubspot_2xx_that_carries_results_is_read_as_per_record():
    outcome = normalise(
        "hubspot",
        200,
        {"results": [hubspot_success("t"), hubspot_failure("t2", "x")]},
        rows(("a", "contact", "t", {}), ("b", "contact", "t2", {})),
    )

    assert outcome.per_record is True
    assert outcome.failed == 1
    assert any("the status alone would not have shown the failures" in note for note in outcome.notes)


def test_the_hubspot_error_count_is_reconciled_against_what_was_described():
    agreeing = normalise(
        "hubspot",
        207,
        {"numErrors": 1, "results": [hubspot_failure("t", "x")]},
        rows(("a", "contact", "t", {})),
    )
    disagreeing = normalise(
        "hubspot",
        207,
        {"numErrors": 3, "results": [hubspot_failure("t", "x")]},
        rows(("a", "contact", "t", {})),
    )

    assert agreeing.reported_errors == 1
    assert agreeing.consistent is True
    assert disagreeing.reported_errors == 3
    assert disagreeing.consistent is False
    assert any("2 error(s) in this response are unaccounted for" in n for n in disagreeing.notes)


def test_an_absent_vendor_error_count_is_not_a_disagreement():
    outcome = normalise(
        "hubspot",
        207,
        {"results": [hubspot_failure("t", "x")]},
        rows(("a", "contact", "t", {})),
    )

    assert outcome.reported_errors is None
    assert outcome.consistent is None


def test_more_described_errors_than_the_vendor_counted_is_flagged_the_other_way():
    outcome = normalise(
        "hubspot",
        207,
        {"numErrors": 1, "results": [hubspot_failure("t1", "x"), hubspot_failure("t2", "y")]},
        rows(("a", "contact", "t1", {}), ("b", "contact", "t2", {})),
    )

    assert outcome.consistent is False
    assert any("were not in the vendor's count" in note for note in outcome.notes)


# --------------------------------------------------------------------------- #
# Step two, Dataverse: a 200 that is not a success
# --------------------------------------------------------------------------- #


def test_a_dataverse_200_is_not_read_as_a_clean_batch():
    """The researched sentence: the batch answers 200 OK and the errors are in the body."""
    outcome = normalise(
        "dataverse",
        200,
        [dataverse_ok(), dataverse_error(400, "0x80044331", DATAVERSE_VALIDATION_MESSAGE)],
        rows(("a", "task", "t-a", {}), ("b", "task", "t-b", {})),
    )

    assert outcome.http_status == 200
    assert outcome.failed == 1
    assert outcome.succeeded == 1
    assert any("a caller that read 200 as success" in note for note in outcome.notes)


def test_a_dataverse_validation_error_names_the_property_inside_its_own_message():
    outcome = normalise(
        "dataverse",
        200,
        [dataverse_error(400, "0x80044331", DATAVERSE_VALIDATION_MESSAGE)],
        rows(("a", "task", "t-a", {"subject": "x"})),
    )

    error = outcome.rows[0].error
    assert error.code == "0x80044331"
    assert error.field == "subject"
    assert error.retryable is False
    assert "0x80044331" in error.basis


def test_the_researched_message_yields_the_property_it_quotes():
    assert dataverse_field_from_message(DATAVERSE_VALIDATION_MESSAGE) == "subject"
    assert dataverse_field_from_message("The 'email' attribute of the 'contact' entity is required") == "email"
    assert dataverse_field_from_message("The 'subject' property is too long") == "subject"
    assert dataverse_field_from_message("nothing quoted here") is None
    assert dataverse_field_from_message("") is None


def test_a_dataverse_helplink_annotation_becomes_the_doc_link():
    outcome = normalise(
        "dataverse",
        200,
        [dataverse_error(400, "0x80044331", DATAVERSE_VALIDATION_MESSAGE, link=DOC_LINK)],
        rows(("a", "task", "t-a", {})),
    )

    assert outcome.rows[0].error.doc_link == DOC_LINK
    assert HELP_LINK_ANNOTATION in outcome.rows[0].error.raw["body"]


def test_the_helplink_annotation_is_read_in_every_shape_it_arrives_in():
    assert help_link("https://x.example") == "https://x.example"
    assert help_link({"HelpLink": "https://x.example"}) == "https://x.example"
    assert help_link({"Url": "https://x.example"}) == "https://x.example"
    assert help_link({"Description": "no url here"}) is None
    assert help_link(None) is None
    assert help_link("   ") is None


def test_dataverse_412_conditions_are_terminal_even_though_they_share_a_status():
    for code, rule in (
        ("ConcurrencyVersionMismatch", "dataverse-concurrency-mismatch"),
        ("DuplicateRecord", "dataverse-duplicate-record"),
    ):
        outcome = normalise(
            "dataverse",
            200,
            [dataverse_error(412, code, "refused")],
            rows(("a", "task", "t-a", {})),
        )
        error = outcome.rows[0].error
        assert error.http_status == 412
        assert error.retryable is False, code
        assert error.code == code
        assert error.matched_rule == rule


def test_a_dataverse_412_lock_is_the_one_condition_that_clears_on_its_own():
    outcome = normalise(
        "dataverse",
        200,
        [dataverse_error(412, "LockMismatch", "the row is locked")],
        rows(("a", "task", "t-a", {})),
    )

    error = outcome.rows[0].error
    assert error.retryable is True
    assert error.matched_rule == "lock-mismatch"
    assert "Inferred" in error.basis


def test_a_dataverse_batch_with_a_top_level_error_object_covers_every_row():
    outcome = normalise(
        "dataverse",
        400,
        {"error": {"code": "BadRequest", "message": "an argument is invalid"}},
        rows(("a", "task", "t-a", {}), ("b", "task", "t-b", {})),
    )

    assert outcome.per_record is False
    assert outcome.failed == 2
    assert outcome.request is not None
    assert outcome.request.scope == "request"
    assert outcome.rows[0].error is outcome.rows[1].error


def test_a_dataverse_200_whose_body_is_not_the_per_request_array_fails_every_row():
    """A 200 the contract does not describe cannot claim the requests succeeded."""
    outcome = normalise("dataverse", 200, {"unexpected": True}, rows(("a", "task", "t-a", {})))

    assert outcome.failed == 1
    assert outcome.rows[0].error.code == "BATCH_WITHOUT_OUTCOMES"
    assert any("neither per-request responses nor an error object" in note for note in outcome.notes)


def test_a_dataverse_200_with_an_empty_response_array_is_still_read_as_per_record():
    outcome = normalise("dataverse", 200, [], rows(("a", "task", "t-a", {})))

    assert outcome.per_record is True
    assert outcome.rows[0].status == "failed"
    assert outcome.rows[0].error.code == "NO_PER_RECORD_OUTCOME"


def test_a_dataverse_result_is_correlated_by_request_order_and_says_so():
    outcome = normalise(
        "dataverse",
        200,
        [dataverse_ok("x"), dataverse_error(400, "0x80044331", DATAVERSE_VALIDATION_MESSAGE)],
        rows(("a", "task", "t-a", {}), ("b", "task", "t-b", {})),
    )

    assert outcome.rows[0].status == "succeeded"
    assert outcome.rows[1].status == "failed"
    assert outcome.rows[1].correlation_basis == "request_order"
    assert outcome.rows[1].correlation is None


# --------------------------------------------------------------------------- #
# Step two, Salesforce
# --------------------------------------------------------------------------- #


def test_a_salesforce_403_request_limit_is_retryable_and_covers_the_whole_batch():
    outcome = normalise(
        "salesforce",
        403,
        {"errorCode": "REQUEST_LIMIT_EXCEEDED", "message": "TotalRequests Limit exceeded."},
        rows(("a", "contact", "t-a", {}), ("b", "contact", "t-b", {})),
    )

    assert outcome.per_record is False
    assert outcome.failed == 2
    assert outcome.rows[0].error.code == "REQUEST_LIMIT_EXCEEDED"
    assert outcome.rows[0].error.retryable is True
    assert outcome.rows[0].error.scope == "request"
    assert "403" in outcome.rows[0].error.basis


def test_a_salesforce_400_malformed_body_is_terminal():
    outcome = normalise(
        "salesforce",
        400,
        {"message": "The request could not be understood."},
        rows(("a", "contact", "t-a", {})),
    )

    assert outcome.rows[0].error.code == "HTTP_400"
    assert outcome.rows[0].error.retryable is False
    assert outcome.rows[0].error.matched_rule == "bad-request"
    assert "couldn" in outcome.rows[0].error.basis


def test_a_salesforce_request_error_is_read_from_both_body_shapes():
    as_object = normalise(
        "salesforce",
        403,
        {"errorCode": "REQUEST_LIMIT_EXCEEDED", "message": "no"},
        rows(("a", "contact", "t", {})),
    )
    as_array = normalise(
        "salesforce",
        400,
        [{"message": "malformed", "errorCode": "MALFORMED_QUERY"}],
        rows(("a", "contact", "t", {})),
    )

    assert as_object.rows[0].error.code == "REQUEST_LIMIT_EXCEEDED"
    assert as_array.rows[0].error.code == "MALFORMED_QUERY"
    assert as_array.rows[0].error.message == "malformed"


def test_a_salesforce_per_record_error_carries_error_code_message_and_fields():
    outcome = normalise(
        "salesforce",
        200,
        {
            "results": [
                salesforce_result(True, record_id="003x"),
                salesforce_result(
                    False,
                    {
                        "statusCode": "400001",
                        "errorCode": "INVALID_FIELD",
                        "message": "Required fields are missing: [Email]",
                        "fields": ["Email"],
                    },
                ),
            ]
        },
        rows(("a", "contact", "sf-1", {}), ("b", "contact", "sf-2", {})),
    )

    error = outcome.rows[1].error
    assert error.code == "INVALID_FIELD"
    assert error.field == "Email"
    assert error.message == "Required fields are missing: [Email]"
    assert error.retryable is False


def test_a_salesforce_result_record_id_beats_its_position():
    """The returned record id is a stronger key than the position it arrived in."""
    outcome = normalise(
        "salesforce",
        200,
        {
            "results": [
                salesforce_result(False, {"errorCode": "X", "message": "x"}, record_id="sf-2"),
                salesforce_result(True, record_id="sf-1"),
            ]
        },
        rows(("a", "contact", "sf-1", {}), ("b", "contact", "sf-2", {})),
    )

    by_key = {row.row_key: row for row in outcome.rows}
    assert by_key["a"].status == "succeeded"
    assert by_key["b"].status == "failed"
    assert by_key["a"].correlation_basis == "correlation"
    assert by_key["b"].correlation_basis == "correlation"
    assert by_key["a"].correlation == "sf-1"


def test_a_salesforce_result_whose_record_id_names_no_row_falls_back_to_its_position():
    outcome = normalise(
        "salesforce",
        200,
        {"results": [salesforce_result(True, record_id="003unknown")]},
        rows(("a", "contact", "sf-1", {})),
    )

    assert outcome.rows[0].row_key == "a"
    assert outcome.rows[0].status == "succeeded"
    assert outcome.rows[0].correlation_basis == "results_order"
    assert outcome.rows[0].correlation is None


def test_a_salesforce_success_with_no_result_array_accepts_every_row():
    outcome = normalise("salesforce", 200, {"done": True}, rows(("a", "contact", "t", {})))

    assert outcome.succeeded == 1
    assert outcome.per_record is False


# --------------------------------------------------------------------------- #
# The four things the room refuses to get wrong
# --------------------------------------------------------------------------- #


def test_a_row_the_response_never_described_is_failed_not_assumed_written():
    outcome = normalise(
        "hubspot",
        200,
        {"results": [hubspot_success("t-a")]},
        rows(("row-a", "contact", "t-a", {}), ("row-b", "contact", "t-b", {})),
    )

    silent = outcome.rows[1]
    assert silent.row_key == "row-b"
    assert silent.status == "failed"
    assert silent.error.code == "NO_PER_RECORD_OUTCOME"
    assert silent.error.retryable is False
    assert any("did not describe it" in note for note in outcome.notes)


def test_the_silent_row_message_says_exactly_what_was_observed():
    outcome = normalise(
        "dataverse",
        200,
        [dataverse_ok()],
        rows(("a", "task", "t-a", {}), ("b", "task", "t-b", {})),
    )

    error = outcome.rows[1].error
    assert error.message == (
        "The batch did not return an outcome for this row. It was sent, and nothing in the "
        "response says it was accepted, so the room does not record it as written."
    )


def test_an_error_that_cannot_be_keyed_is_kept_and_the_run_says_so():
    outcome = normalise(
        "hubspot",
        207,
        {"numErrors": 1, "results": [hubspot_failure("t-nobody", "bad")]},
        rows(("row-a", "contact", "t-a", {})),
    )

    assert len(outcome.unattributed) == 1
    assert any("could not be keyed to a row" in note for note in outcome.notes)


def test_outcomes_come_back_in_the_order_the_rows_were_sent():
    outcome = normalise(
        "hubspot",
        207,
        {"numErrors": 1, "results": [hubspot_failure("t-b", "x"), hubspot_success("t-a")]},
        rows(("row-a", "contact", "t-a", {}), ("row-b", "contact", "t-b", {})),
    )

    assert [row.row_key for row in outcome.rows] == ["row-a", "row-b"]
    assert [row.index for row in outcome.rows] == [0, 1]


def test_every_extra_error_object_on_a_result_is_kept_beyond_the_first():
    result = {
        "status": "error",
        "category": "VALIDATION_ERROR",
        "message": "three problems",
        "context": {"objectWriteTraceId": ["t"]},
        "errors": [
            {"message": "one", "code": "A", "in": "email"},
            {"message": "two", "code": "B", "in": "lastname"},
            {"message": "three", "code": "C", "in": "phone"},
        ],
    }
    outcome = normalise(
        "hubspot",
        207,
        {"numErrors": 1, "results": [result]},
        rows(("a", "contact", "t", {})),
    )

    error = outcome.rows[0].error
    assert error.field == "email"
    assert [item["field"] for item in error.related] == ["lastname", "phone"]


def test_a_hubspot_property_is_read_from_whichever_key_the_vendor_used():
    for key in ("field", "in", "name"):
        result = {
            "status": "error",
            "category": "VALIDATION_ERROR",
            "message": "x",
            "context": {"objectWriteTraceId": ["t"]},
            "errors": [{"message": "x", "code": "C", key: "email"}],
        }
        outcome = normalise(
            "hubspot", 207, {"results": [result]}, rows(("a", "contact", "t", {}))
        )
        assert outcome.rows[0].error.field == "email", key


def test_a_hubspot_result_naming_no_property_returns_none_rather_than_guessing():
    outcome = normalise(
        "hubspot",
        207,
        {"numErrors": 1, "results": [hubspot_failure("t", "no property named")]},
        rows(("a", "contact", "t", {})),
    )

    assert outcome.rows[0].error.field is None


# --------------------------------------------------------------------------- #
# The classification: retryable versus terminal
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("entry", CLASSIFICATION, ids=lambda e: e.id)
def test_every_classification_entry_states_why_it_classifies_that_way(entry):
    basis = entry.basis.strip()
    assert basis, f"{entry.id} has no basis"
    assert basis.startswith("Quoted") or basis.startswith("Inferred"), entry.id
    assert entry.id == entry.id.strip()


def test_the_two_researched_retryable_classes_are_retryable():
    limit, basis, rule = classify(connector="salesforce", code="REQUEST_LIMIT_EXCEEDED", http_status=403)
    assert limit is True
    assert rule == "salesforce-request-limit-exceeded"
    assert "Quoted" in basis

    lock, _basis, rule = classify(connector="dataverse", code="LockMismatch", http_status=412)
    assert lock is True
    assert rule == "lock-mismatch"


def test_the_researched_validation_class_waits_for_a_person():
    retryable, basis, rule = classify(connector="dataverse", code="0x80044331", http_status=400)

    assert retryable is False
    assert rule == "dataverse-validation"
    assert "Quoted" in basis


@pytest.mark.parametrize("status", [404, 405, 409, 418, 422, 451])
def test_an_unrecognised_status_waits_for_a_person(status):
    retryable, basis, matched = classify(connector="hubspot", code="SOMETHING_NEW", http_status=status)

    assert retryable is False
    assert matched == UNRECOGNISED
    assert basis == DEFAULT_BASIS
    assert "rate limit and locked" in basis


@pytest.mark.parametrize(
    "status,rule",
    [(400, "bad-request"), (401, "unauthenticated"), (403, "forbidden")],
)
def test_a_known_status_is_terminal_by_its_own_named_rule(status, rule):
    """Those three are in the table, so terminal with a reason rather than by default."""
    retryable, _basis, matched = classify(connector="hubspot", code="ANYTHING", http_status=status)

    assert retryable is False
    assert matched == rule


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504, 599])
def test_a_transport_level_transient_status_is_retryable(status):
    retryable, _basis, _matched = classify(connector="hubspot", code="WHATEVER", http_status=status)

    assert retryable is True


def test_a_403_that_is_not_the_request_limit_is_terminal():
    retryable, basis, matched = classify(
        connector="salesforce", code="INSUFFICIENT_ACCESS", http_status=403
    )

    assert retryable is False
    assert matched == "forbidden"
    assert "Inferred" in basis


def test_a_specific_code_wins_over_a_bare_status():
    """0x80044331 on a 500 is still a validation error, not a server fault."""
    retryable, _basis, matched = classify(connector="dataverse", code="0x80044331", http_status=500)

    assert retryable is False
    assert matched == "dataverse-validation"


def test_a_code_from_another_connector_does_not_leak_its_classification():
    """The HubSpot-shaped code name is only classified for HubSpot."""
    hubspot = classify(connector="hubspot", code="0x80044331", http_status=409)
    salesforce = classify(connector="salesforce", code="0x80044331", http_status=409)

    assert hubspot[2] == UNRECOGNISED
    assert salesforce[2] == UNRECOGNISED


def test_a_code_that_looks_like_another_connectors_is_still_classified_on_its_own_status():
    retryable, _basis, matched = classify(connector="hubspot", code="0x80044331", http_status=400)

    assert retryable is False
    assert matched == "bad-request"


# --------------------------------------------------------------------------- #
# The researched extension point: routing rules
# --------------------------------------------------------------------------- #


def test_a_routing_rule_reverses_a_built_in_classification():
    """The research's own example: route records missing email to a manual review."""
    rule = {
        "id": "missing-email-to-review",
        "when": {"field": "email"},
        "then": {"retryable": False},
        "basis": "the research's extensibility example, applied literally",
    }
    retryable, basis, matched = classify(
        connector="hubspot", code="RATE_LIMIT", http_status=429, field="email", overrides=[rule]
    )

    assert retryable is False
    assert matched == "missing-email-to-review"
    assert basis == rule["basis"]


def test_a_routing_rule_can_make_a_terminal_class_retryable():
    rule = {
        "id": "flaky-plugin",
        "when": {"connector": "dataverse", "code_prefix": "PLUGIN_"},
        "then": {"retryable": True},
        "basis": "a plug-in this deployment knows is flaky",
    }
    assert classify(connector="dataverse", code="PLUGIN_TIMEOUT", http_status=500, overrides=[rule])[0] is True
    assert classify(connector="dataverse", code="OTHER", http_status=500, overrides=[rule])[0] is True


def test_a_routing_rule_only_fires_when_every_when_key_matches():
    rule = {
        "id": "narrow",
        "when": {"connector": "hubspot", "code": "VALIDATION_ERROR"},
        "then": {"retryable": True},
        "basis": "only this exact pair",
    }
    assert classify(connector="hubspot", code="VALIDATION_ERROR", overrides=[rule])[0] is True
    assert classify(connector="salesforce", code="VALIDATION_ERROR", overrides=[rule])[0] is False
    assert classify(connector="hubspot", code="OTHER", overrides=[rule])[0] is False


def test_the_last_matching_routing_rule_wins():
    rules = [
        {"id": "broad", "when": {"connector": "hubspot"}, "then": {"retryable": False}, "basis": "broad"},
        {"id": "narrow", "when": {"code": "FORBIDDEN"}, "then": {"retryable": True}, "basis": "narrow"},
    ]
    _retryable, _basis, matched = classify(connector="hubspot", code="FORBIDDEN", overrides=rules)

    assert matched == "narrow"


def test_a_routing_rule_can_match_on_the_message():
    rule = {
        "id": "transient-text",
        "when": {"message_contains": "try again later"},
        "then": {"retryable": True},
        "basis": "the vendor's own wording for a transient condition",
    }
    hit = classify(connector="hubspot", code="X", message="Please try again later", overrides=[rule])
    miss = classify(connector="hubspot", code="X", message="Invalid", overrides=[rule])
    assert hit[0] is True
    assert miss[0] is False


def test_a_routing_rule_with_an_empty_when_is_refused_rather_than_matching_everything():
    with pytest.raises(InvalidRule):
        validate_routing_rule({"id": "x", "when": {}, "then": {"retryable": True}, "basis": "b"})


def test_a_routing_rule_without_a_basis_is_refused():
    with pytest.raises(InvalidRule):
        validate_routing_rule({"id": "x", "when": {"code": "A"}, "then": {"retryable": True}})


def test_a_routing_rule_with_an_unknown_when_key_is_refused():
    with pytest.raises(InvalidRule) as raised:
        validate_routing_rule(
            {"id": "x", "when": {"cde": "A"}, "then": {"retryable": True}, "basis": "b"}
        )
    assert "unknown when key" in str(raised.value)


def test_a_routing_rule_cannot_set_a_second_disposition_knob():
    with pytest.raises(InvalidRule) as raised:
        validate_routing_rule(
            {
                "id": "x",
                "when": {"code": "A"},
                "then": {"retryable": True, "disposition": "queued"},
                "basis": "b",
            }
        )
    assert "no second knob" in str(raised.value)


def test_a_routing_rule_then_retryable_must_be_a_boolean():
    with pytest.raises(InvalidRule):
        validate_routing_rule(
            {"id": "x", "when": {"code": "A"}, "then": {"retryable": "yes"}, "basis": "b"}
        )


def test_a_routing_rule_with_an_unknown_top_level_key_is_refused():
    with pytest.raises(InvalidRule) as raised:
        validate_routing_rule(
            {"id": "x", "when": {"code": "A"}, "then": {"retryable": True}, "basis": "b", "prio": 1}
        )
    assert "unknown routing rule key" in str(raised.value)


def test_the_routing_vocabulary_is_the_fixed_key_set():
    assert OVERRIDE_MATCH_KEYS == (
        "connector",
        "code",
        "code_prefix",
        "field",
        "category",
        "http_status",
        "message_contains",
    )
    assert OVERRIDE_THEN_KEYS == ("retryable",)


# --------------------------------------------------------------------------- #
# Before the commit: the pre-flight
# --------------------------------------------------------------------------- #


def test_the_shipped_preflight_rules_are_the_ones_the_research_grounds():
    ids = [rule["id"] for rule in DEFAULT_PREFLIGHT_RULES]

    assert ids == [
        "hubspot-contact-email-required",
        "dataverse-task-subject-length",
        "salesforce-contact-email-required",
    ]
    for rule in DEFAULT_PREFLIGHT_RULES:
        assert rule["basis"], rule["id"]


def test_the_dataverse_length_rule_is_the_quoted_sentence():
    rule = next(r for r in DEFAULT_PREFLIGHT_RULES if r["id"] == "dataverse-task-subject-length")

    assert (rule["entity"], rule["field"], rule["kind"], rule["value"]) == (
        "task",
        "subject",
        "max_length",
        200,
    )
    assert "'subject'" in rule["basis"]
    assert "'200'" in rule["basis"]


def test_a_missing_email_is_refused_before_the_batch_is_sent():
    batch = check_batch(
        [
            {"row_key": "a", "entity": "contact", "values": {"email": "x@y.example"}},
            {"row_key": "b", "entity": "contact", "values": {}},
        ],
        preflight_defaults(),
        connector="hubspot",
    )

    assert batch["accepted"] == 1
    assert batch["refused"] == 1
    assert batch["hold"] == ["b"]
    assert batch["send"] == ["a"]
    assert batch["ok"] is False


def test_the_refusal_carries_the_property_what_was_sent_and_what_was_expected():
    batch = check_batch(
        [{"row_key": "b", "entity": "contact", "values": {}}],
        preflight_defaults(),
        connector="hubspot",
    )
    violation = batch["verdicts"][0]["violations"][0]

    assert violation["field"] == "email"
    assert violation["kind"] == "required"
    assert violation["expected"] == "a value is required"
    assert violation["sent"] is None
    assert violation["sent_present"] is False


def test_an_over_long_subject_is_refused_and_the_message_cites_the_researched_wording():
    batch = check_batch(
        [{"row_key": "t", "entity": "task", "values": {"subject": "A" * 201}}],
        preflight_defaults(),
        connector="dataverse",
    )
    violation = batch["verdicts"][0]["violations"][0]

    assert violation["field"] == "subject"
    assert violation["limit"] == 200
    assert violation["sent_length"] == 201
    assert "maximum allowed length of '200'" in violation["message"]


def test_exactly_at_the_limit_is_accepted():
    batch = check_batch(
        [{"row_key": "t", "entity": "task", "values": {"subject": "A" * 200}}],
        preflight_defaults(),
        connector="dataverse",
    )

    assert batch["ok"] is True


def test_a_rule_only_applies_to_its_own_connector_and_entity():
    rules = preflight_defaults()
    for connector, entity, values in (
        ("hubspot", "deal", {}),
        ("dataverse", "contact", {}),
        ("salesforce", "task", {}),
    ):
        batch = check_batch(
            [{"row_key": "x", "entity": entity, "values": values}], rules, connector=connector
        )
        assert batch["ok"] is True, f"{connector}/{entity}"


def test_a_wildcard_rule_applies_to_every_connector_and_entity():
    rules = [
        validate_rule(
            {"id": "any-email", "connector": ANY, "entity": ANY, "field": "email", "kind": "required"}
        )
    ]
    batch = check_batch(
        [{"row_key": "x", "entity": "lead", "values": {}}], rules, connector="salesforce"
    )

    assert batch["refused"] == 1
    assert batch["rules_applied"] == ["any-email"]


def test_a_length_rule_never_fires_on_an_absent_value():
    found = check_row(
        {"row_key": "t", "entity": "task", "values": {}}, preflight_defaults(), connector="dataverse"
    )
    assert found == []


def test_a_length_rule_does_not_measure_a_number():
    """A numeric property has a length in digits, and no documented bound on it."""
    rules = [
        validate_rule(
            {"id": "max-zip", "connector": ANY, "entity": ANY, "field": "zip", "kind": "max_length", "value": 5}
        )
    ]
    found = check_row(
        {"row_key": "z", "entity": "contact", "values": {"zip": 1234567}}, rules, connector="hubspot"
    )
    assert found == []


def test_a_min_length_rule_fires_on_a_short_string():
    rules = [
        validate_rule(
            {"id": "min-subject", "connector": ANY, "entity": ANY, "field": "subject", "kind": "min_length", "value": 3}
        )
    ]
    violations = check_row(
        {"row_key": "s", "entity": "task", "values": {"subject": "ab"}}, rules, connector="dataverse"
    )

    assert [v["kind"] for v in violations] == ["min_length"]
    assert violations[0]["expected"] == "at least 3 characters"


def test_an_empty_string_counts_as_missing_for_a_required_rule():
    batch = check_batch(
        [{"row_key": "b", "entity": "contact", "values": {"email": "   "}}],
        preflight_defaults(),
        connector="hubspot",
    )

    assert batch["refused"] == 1


def test_a_rejected_value_is_echoed_but_bounded():
    long_value = "A" * (SENT_PREVIEW_CHARS + 50)
    batch = check_batch(
        [{"row_key": "t", "entity": "task", "values": {"subject": long_value}}],
        preflight_defaults(),
        connector="dataverse",
    )
    violation = batch["verdicts"][0]["violations"][0]

    assert violation["sent_truncated"] is True
    assert len(violation["sent"]) == SENT_PREVIEW_CHARS
    assert violation["sent_length"] == SENT_PREVIEW_CHARS + 50


def test_an_unknown_preflight_kind_is_refused_rather_than_stored_and_ignored():
    with pytest.raises(InvalidRule) as raised:
        validate_rule(
            {"id": "x", "connector": "hubspot", "entity": "contact", "field": "email", "kind": "maxLen", "value": 5}
        )

    assert "looks exactly like a rule that passes" in str(raised.value)


def test_an_unknown_preflight_key_is_refused():
    with pytest.raises(InvalidRule) as raised:
        validate_rule(
            {"id": "x", "connector": "hubspot", "entity": "contact", "field": "email", "kind": "required", "valeu": 5}
        )
    assert "unknown rule key" in str(raised.value)


def test_a_length_rule_without_a_numeric_limit_is_refused():
    with pytest.raises(InvalidRule):
        validate_rule(
            {"id": "x", "connector": ANY, "entity": ANY, "field": "s", "kind": "max_length", "value": "long"}
        )


def test_a_required_rule_with_a_value_is_refused():
    with pytest.raises(InvalidRule):
        validate_rule(
            {"id": "x", "connector": ANY, "entity": ANY, "field": "s", "kind": "required", "value": 5}
        )


def test_two_rules_sharing_an_id_are_refused():
    with pytest.raises(InvalidRule) as raised:
        validation_module.validate_rules(
            [
                {"id": "same", "connector": ANY, "entity": ANY, "field": "a", "kind": "required"},
                {"id": "same", "connector": ANY, "entity": ANY, "field": "b", "kind": "required"},
            ]
        )
    assert "share the id" in str(raised.value)


def test_the_expectation_text_names_the_bound_in_words():
    assert expectation_text("required", None) == "a value is required"
    assert expectation_text("max_length", 200) == "at most 200 characters"
    assert expectation_text("min_length", 3) == "at least 3 characters"


def test_expectations_for_a_property_list_the_rule_behind_it():
    found = expectations_for(
        preflight_defaults(), connector="dataverse", entity="task", field="subject"
    )

    assert found == [
        {
            "field": "subject",
            "kind": "max_length",
            "rule_id": "dataverse-task-subject-length",
            "limit": 200,
            "expected": "at most 200 characters",
        }
    ]


def test_covers_reports_a_configuration_gap_rather_than_guessing():
    rules = preflight_defaults()
    assert covers(rules, connector="dataverse", entity="task", field="subject") is True
    assert covers(rules, connector="dataverse", entity="task", field="description") is False


# --------------------------------------------------------------------------- #
# The rules record
# --------------------------------------------------------------------------- #


def test_the_rules_start_at_the_shipped_defaults():
    from dsr.partial_failures import DEFAULT_RULES

    rules, origin = effective_rules(None)

    assert origin == "defaults"
    assert rules["max_attempts"] == MAX_ATTEMPTS
    assert rules["routing"] == []
    assert rules["preflight"] == DEFAULT_RULES["preflight"]


def test_a_patch_adds_a_preflight_rule_without_removing_the_shipped_ones():
    from dsr.partial_failures import DEFAULT_RULES

    merged = merge_rules(
        DEFAULT_RULES,
        {"preflight": [{"id": "extra", "connector": "hubspot", "entity": "deal", "field": "amount", "kind": "required"}]},
    )

    ids = {rule["id"] for rule in merged["preflight"]}
    assert "extra" in ids
    assert "dataverse-task-subject-length" in ids
    assert len(ids) == len(DEFAULT_PREFLIGHT_RULES) + 1


def test_a_patch_replaces_a_shipped_rule_rather_than_shadowing_it():
    from dsr.partial_failures import DEFAULT_RULES

    merged = merge_rules(
        DEFAULT_RULES,
        {"preflight": [{"id": "dataverse-task-subject-length", "connector": "dataverse", "entity": "task", "field": "subject", "kind": "max_length", "value": 120}]},
    )

    matches = [rule for rule in merged["preflight"] if rule["id"] == "dataverse-task-subject-length"]
    assert len(matches) == 1
    assert matches[0]["value"] == 120


def test_a_retuned_limit_actually_moves_the_boundary():
    from dsr.partial_failures import DEFAULT_RULES

    merged = merge_rules(
        DEFAULT_RULES,
        {"preflight": [{"id": "dataverse-task-subject-length", "connector": "dataverse", "entity": "task", "field": "subject", "kind": "max_length", "value": 120}]},
    )
    batch = check_batch(
        [{"row_key": "t", "entity": "task", "values": {"subject": "A" * 150}}],
        merged["preflight"],
        connector="dataverse",
    )

    assert batch["refused"] == 1


def test_a_patch_replaces_max_attempts_outright():
    from dsr.partial_failures import DEFAULT_RULES

    merged = merge_rules(DEFAULT_RULES, {"max_attempts": 2})

    assert merged["max_attempts"] == 2
    assert len(merged["preflight"]) == len(DEFAULT_PREFLIGHT_RULES)


def test_a_max_attempts_of_zero_is_refused_because_it_would_stop_the_drain():
    from dsr.partial_failures import DEFAULT_RULES

    with pytest.raises(InvalidRule) as raised:
        merge_rules(DEFAULT_RULES, {"max_attempts": 0})
    assert "at least 1" in str(raised.value)


def test_an_unknown_top_level_rule_is_refused_rather_than_ignored():
    from dsr.partial_failures import DEFAULT_RULES

    with pytest.raises(InvalidRule) as raised:
        merge_rules(DEFAULT_RULES, {"max_attempt": 3})
    assert "unknown rule(s)" in str(raised.value)


def test_a_stored_override_that_became_invalid_falls_back_and_says_where_it_came_from():
    rules, origin = effective_rules({"max_attempts": "five", "routing": [], "preflight": []})

    assert origin == "defaults"
    assert rules["max_attempts"] == MAX_ATTEMPTS


def test_a_stored_override_with_a_key_this_version_does_not_know_about_is_ignored():
    rules, origin = effective_rules(
        {"max_attempts": 3, "routing": [], "preflight": [], "v2_thing": True}
    )

    assert origin == "override"
    assert rules["max_attempts"] == 3


def test_the_rules_view_serves_both_the_effective_rules_and_the_stored_copy(log):
    view = log.rules_view()

    assert view["source"] == "defaults"
    assert view["stored"] is None
    assert view["collection"] == RULES_COLLECTION
    assert view["record_id"] == RULES_RECORD_ID
    assert view["rule_kinds"] == list(PREFLIGHT_KINDS)
    assert view["when_keys"] == list(OVERRIDE_MATCH_KEYS)
    assert view["effective"]["preflight"] == len(DEFAULT_PREFLIGHT_RULES)


def test_saving_rules_creates_one_row_and_audits_it(log, store):
    log.save_rules({"max_attempts": 2}, actor="dana", source=f"PATCH {PREFIX}/rules")

    assert store.get(RULES_RECORD_ID)["data"]["max_attempts"] == 2
    audit = store.audit(collection=RULES_COLLECTION)
    assert [entry["action"] for entry in audit] == ["insert"]
    assert audit[0]["source"] == f"PATCH {PREFIX}/rules"


def test_saving_rules_twice_updates_the_same_row(log, store):
    log.save_rules({"max_attempts": 2}, actor="dana", source=f"PATCH {PREFIX}/rules")
    log.save_rules({"max_attempts": 3}, actor="dana", source=f"PATCH {PREFIX}/rules")

    assert len(store.list(RULES_COLLECTION)) == 1
    assert store.get(RULES_RECORD_ID)["data"]["max_attempts"] == 3
    # The audit log is newest first, so the update is the row a reader meets.
    actions = [entry["action"] for entry in store.audit(collection=RULES_COLLECTION)]
    assert actions == ["update", "insert"]


def test_an_invalid_rules_patch_writes_nothing(log, store):
    with pytest.raises(InvalidRule):
        log.save_rules({"max_attempts": 0}, actor="dana", source=f"PATCH {PREFIX}/rules")

    assert store.list(RULES_COLLECTION) == []
    assert store.audit(collection=RULES_COLLECTION) == []


def test_a_routing_rule_reaches_the_classifier_through_the_stored_record(log, store, room):
    log.save_rules(
        {
            "routing": [
                {
                    "id": "throttle-is-not-ours",
                    "when": {"code": "RATE_LIMIT"},
                    "then": {"retryable": False},
                    "basis": "a deployment that would rather a human decided",
                }
            ]
        },
        actor="dana",
        source=f"PATCH {PREFIX}/rules",
    )
    result = record(
        log,
        room["id"],
        run_payload(
            "hubspot",
            rows(hubspot_row(1)),
            429,
            {"numErrors": 1, "results": [hubspot_failure("hs-t1", "slow down", code="RATE_LIMIT")]},
        ),
    )

    assert result["rows"][0]["retryable"] is False
    assert result["rows"][0]["disposition"] == "needs_action"
    assert store.get(RULES_RECORD_ID) is not None


# --------------------------------------------------------------------------- #
# The retry queue: the researched automation
# --------------------------------------------------------------------------- #


def test_a_retryable_error_is_queued_and_a_terminal_one_waits_for_a_person():
    assert disposition_for(True) == "queued"
    assert disposition_for(False) == "needs_action"


def test_the_backoff_doubles_from_the_shipped_base_and_is_capped():
    assert backoff_seconds(1) == BASE_BACKOFF_SECONDS
    assert backoff_seconds(2) == BASE_BACKOFF_SECONDS * 2
    assert backoff_seconds(3) == BASE_BACKOFF_SECONDS * 4
    assert backoff_seconds(20) == MAX_BACKOFF_SECONDS
    assert "exponential" in BACKOFF_LABEL


def test_a_lost_attempt_count_still_gets_a_real_wait_rather_than_zero():
    assert backoff_seconds(0) == BASE_BACKOFF_SECONDS
    assert backoff_seconds(-3) == BASE_BACKOFF_SECONDS


def test_a_plan_splits_failed_rows_into_four_lists_and_leaves_successes_alone():
    planned = retry_plan(
        [
            {"id": "r1", "row_key": "retryable", "status": "failed", "attempts": 1,
             "error": {"retryable": True, "code": "X"}, "next_retry_at": None},
            {"id": "r2", "row_key": "terminal", "status": "failed", "attempts": 1,
             "error": {"retryable": False, "code": "Y"}},
            {"id": "r3", "row_key": "done", "status": "succeeded", "attempts": 1},
        ],
        max_attempts=5,
        now=NOW,
    )

    assert [entry["row_key"] for entry in planned["drain"]] == ["retryable"]
    assert [entry["row_key"] for entry in planned["waiting"]] == ["terminal"]
    assert planned["counts"]["succeeded"] == 1
    assert planned["scheduled"] == []


def test_a_row_still_inside_its_backoff_is_scheduled_rather_than_drained():
    """Sending one early is the immediate retry that produces a second refusal."""
    planned = retry_plan(
        [
            {"id": "r1", "row_key": "later", "status": "failed", "attempts": 1,
             "error": {"retryable": True},
             "next_retry_at": (NOW + timedelta(seconds=30)).isoformat()},
        ],
        max_attempts=5,
        now=NOW,
    )

    assert planned["drain"] == []
    assert [entry["row_key"] for entry in planned["scheduled"]] == ["later"]
    assert planned["scheduled"][0]["due_in_seconds"] == 30
    assert "backs off" in planned["scheduled"][0]["scheduled_because"]


def test_a_row_past_the_bound_is_expired_rather_than_queued():
    planned = retry_plan(
        [{"id": "r1", "row_key": "tired", "status": "failed", "attempts": 5, "error": {"retryable": True}}],
        max_attempts=5,
        now=NOW,
    )

    assert planned["drain"] == []
    assert [entry["row_key"] for entry in planned["expired"]] == ["tired"]
    assert "is not a throttle" in planned["expired"][0]["expires_because"]


def test_a_row_one_attempt_short_of_the_bound_still_drains():
    planned = retry_plan(
        [{"id": "r1", "row_key": "k", "status": "failed", "attempts": 4, "error": {"retryable": True}}],
        max_attempts=5,
        now=NOW,
    )
    assert planned["counts"]["drain"] == 1

    planned = retry_plan(
        [{"id": "r1", "row_key": "k", "status": "failed", "attempts": 5, "error": {"retryable": True}}],
        max_attempts=5,
        now=NOW,
    )
    assert planned["counts"]["expired"] == 1


def test_can_retry_answers_the_queues_question_not_the_admins():
    row = {"status": "failed", "attempts": 1, "error": {"retryable": True}, "next_retry_at": None}

    assert can_retry(row, now=NOW) is True
    assert can_retry({**row, "attempts": 99}, now=NOW) is False
    assert can_retry({**row, "error": {"retryable": False}}, now=NOW) is False
    later = {**row, "next_retry_at": (NOW + timedelta(seconds=60)).isoformat()}
    assert can_retry(later, now=NOW) is False


def test_is_retryable_reads_the_stored_error():
    assert is_retryable({"retryable": True}) is True
    assert is_retryable({"retryable": False}) is False
    assert is_retryable(None) is False
    assert is_retryable({}) is False


# --------------------------------------------------------------------------- #
# Step three: the Sync log over the store
# --------------------------------------------------------------------------- #


def test_recording_a_run_creates_one_audited_row_per_input_row(log, store, room):
    result = record(
        log,
        room["id"],
        run_payload(
            "hubspot",
            rows(hubspot_row(1), hubspot_row(2)),
            207,
            {"numErrors": 1, "results": [hubspot_success("hs-t1", "901"), hubspot_failure("hs-t2", "bad")]},
        ),
    )

    assert len(store.list(ROW_COLLECTION, room_id=room["id"])) == 2
    assert len(store.list(RUN_COLLECTION, room_id=room["id"])) == 1
    assert len(result["rows"]) == 2
    audit = store.audit(collection=ROW_COLLECTION)
    assert len(audit) == 2
    assert all(entry["source"] == "POST test" for entry in audit)
    assert all(entry["room_id"] == room["id"] for entry in audit)


def test_a_successful_row_carries_no_waiting_state(log, room):
    """`resolved` is a claim that something was outstanding; a clean write has none."""
    result = record(
        log, room["id"], run_payload("hubspot", rows(hubspot_row(1)), 200, {"status": "success"})
    )

    assert result["rows"][0]["status"] == "succeeded"
    assert result["rows"][0]["disposition"] is None


def test_a_row_that_was_failed_and_then_accepted_becomes_resolved(log, room):
    first = record(
        log,
        room["id"],
        run_payload(
            "salesforce",
            rows(("a", "contact", "sf-1", {"email": "a@x.example"})),
            403,
            {"errorCode": "REQUEST_LIMIT_EXCEEDED", "message": "no"},
        ),
    )

    log.retry_failed(
        first["run"]["id"],
        {"vendor": {"status": 200, "body": {"results": [salesforce_result(True, record_id="003x")]}}},
        actor="dana",
        source="POST test",
        now=NOW + timedelta(minutes=5),
    )
    row = log.sync_log(room["id"])["rows"][0]

    assert row["status"] == "succeeded"
    assert row["disposition"] == "resolved"
    assert row["attempts"] == 2


def test_the_stored_row_records_what_was_sent_and_the_correlation_basis(log, store, room):
    record(
        log,
        room["id"],
        run_payload("hubspot", rows(hubspot_row(1)), 207, {"results": [hubspot_failure("hs-t1", "bad")]}),
    )
    stored = store.list(ROW_COLLECTION, room_id=room["id"])[0]["data"]

    assert stored["sent"] == {"email": "a.buyer@northwind.example", "lastname": "Buyer"}
    assert stored["correlation_basis"] == "correlation"
    assert stored["history"][0]["attempt"] == 1
    assert stored["attempts"] == 1


def test_a_property_field_the_room_does_not_own_is_stored_verbatim(log, store, room):
    payload = run_payload(
        "hubspot",
        [
            {
                "row_key": "a",
                "entity": "contact",
                "values": {"email": "a@x.example"},
                "billing": {"seat": 12},
                "deal_stage": "evaluation",
            }
        ],
        200,
        {"status": "success"},
    )
    record(log, room["id"], payload)
    stored = store.list(ROW_COLLECTION, room_id=room["id"])[0]["data"]

    assert stored["billing"] == {"seat": 12}
    assert stored["deal_stage"] == "evaluation"


def test_a_stored_extra_field_is_queryable_through_the_dynamic_index(log, store, room):
    record(
        log,
        room["id"],
        run_payload(
            "hubspot",
            [
                {
                    "row_key": "a",
                    "entity": "contact",
                    "values": {"email": "a@x.example"},
                    "billing": {"seat": 12},
                }
            ],
            200,
            {"status": "success"},
        ),
    )

    assert store.find(ROW_COLLECTION, {"billing.seat": 12})


def test_a_stored_field_cannot_override_a_key_the_room_owns(log, store, room):
    record(
        log,
        room["id"],
        run_payload(
            "hubspot",
            [
                {
                    "row_key": "a",
                    "entity": "contact",
                    "values": {"email": "a@x.example"},
                    "status": "succeeded",
                    "attempts": 99,
                    "disposition": "resolved",
                }
            ],
            429,
            {"numErrors": 1, "results": [hubspot_failure("t", "slow", code="RATE_LIMIT")]},
        ),
    )
    stored = store.list(ROW_COLLECTION, room_id=room["id"])[0]["data"]

    assert stored["status"] == "failed"
    assert stored["attempts"] == 1
    assert stored["disposition"] == "queued"


def test_the_sync_log_summary_is_computed_over_the_rows_returned(log, room):
    record(
        log,
        room["id"],
        run_payload(
            "hubspot",
            rows(hubspot_row(1), hubspot_row(2), hubspot_row(3)),
            207,
            {
                "numErrors": 2,
                "results": [
                    hubspot_success("hs-t1"),
                    hubspot_failure("hs-t2", "bad", field="lastname"),
                    hubspot_failure("hs-t3", "bad"),
                ],
            },
        ),
    )

    body = log.sync_log(room["id"])

    assert body["total"] == 3
    assert body["summary"]["succeeded"] == 1
    assert body["summary"]["failed"] == 2
    assert body["summary"]["needs_action"] == 2
    assert body["summary"]["with_property"] == 1

    filtered = log.sync_log(room["id"], status="succeeded")
    assert filtered["summary"]["rows"] == 1
    assert filtered["summary"]["succeeded"] == 1
    assert filtered["summary"]["failed"] == 0


def test_the_sync_log_filters_by_every_value_it_stores(log, room):
    record(
        log,
        room["id"],
        run_payload(
            "hubspot",
            rows(hubspot_row(1), hubspot_row(2)),
            207,
            {"numErrors": 1, "results": [hubspot_success("hs-t1"), hubspot_failure("hs-t2", "bad", field="lastname")]},
        ),
    )
    record(
        log,
        room["id"],
        run_payload("salesforce", rows(("s", "contact", "sf-1", {})), 403,
                    {"errorCode": "REQUEST_LIMIT_EXCEEDED", "message": "no"}),
    )

    assert log.sync_log(room["id"], connector="hubspot")["total"] == 2
    assert log.sync_log(room["id"], connector="salesforce")["total"] == 1
    assert log.sync_log(room["id"], disposition="queued")["total"] == 1
    assert log.sync_log(room["id"], disposition="needs_action")["total"] == 1
    assert log.sync_log(room["id"], field="lastname")["total"] == 1
    assert log.sync_log(room["id"], field="nothing")["total"] == 0


def test_the_sync_log_is_scoped_to_its_room(log, store, room):
    other = store.create("room", {"name": "Contoso", "account": "Contoso"})
    record(log, room["id"], run_payload("hubspot", rows(hubspot_row(1)), 200, {"status": "success"}))
    record(log, other["id"], run_payload("hubspot", rows(hubspot_row(1)), 200, {"status": "success"}))

    assert log.sync_log(room["id"])["total"] == 1
    assert log.sync_log(other["id"])["total"] == 1


def test_an_unknown_status_filter_is_refused_with_the_values_it_serves(log, room):
    with pytest.raises(InvalidPayload) as raised:
        log.sync_log(room["id"], status="maybe")

    assert "succeeded, failed" in str(raised.value)


def test_an_unknown_disposition_filter_is_refused_with_the_values_it_serves(log, room):
    with pytest.raises(InvalidPayload):
        log.sync_log(room["id"], disposition="furious")


def test_listing_runs_reports_a_per_connector_breakdown_over_the_whole_set(log, room):
    record(log, room["id"], run_payload("hubspot", rows(hubspot_row(1)), 200, {"status": "success"}))
    record(log, room["id"], run_payload("hubspot", rows(hubspot_row(2)), 200, {"status": "success"}))
    record(log, room["id"], run_payload("salesforce", rows(("s", "contact", "t", {})), 200,
                                        {"results": [salesforce_result(True)]}))

    body = log.list_runs(room_id=room["id"])

    assert body["total"] == 3
    assert body["by_connector"]["hubspot"] == {"runs": 2, "rows": 2, "succeeded": 2, "failed": 0}
    assert body["by_connector"]["salesforce"]["runs"] == 1
    assert log.list_runs(room_id=room["id"], connector="hubspot")["total"] == 2


def test_a_run_is_ordered_newest_first(log, room):
    first = record(log, room["id"], run_payload("hubspot", rows(hubspot_row(1)), 200, {"status": "success"}))
    second = record(
        log, room["id"], run_payload("hubspot", rows(hubspot_row(2)), 200, {"status": "success"}),
        at=NOW + timedelta(minutes=10),
    )

    runs = log.list_runs(room_id=room["id"])["runs"]

    assert [entry["id"] for entry in runs] == [second["run"]["id"], first["run"]["id"]]


# --------------------------------------------------------------------------- #
# Step four: field-level error detail
# --------------------------------------------------------------------------- #


def test_the_detail_view_answers_which_property_what_was_sent_what_was_expected(log, room):
    result = record(
        log,
        room["id"],
        run_payload(
            "dataverse",
            rows(("t-1", "task", "dv-1", {"subject": "A" * 260})),
            200,
            [dataverse_error(400, "0x80044331", DATAVERSE_VALIDATION_MESSAGE)],
        ),
    )
    detail = log.row_detail(room["id"], result["rows"][0]["id"])

    assert detail["detail"]["property"] == "subject"
    assert detail["detail"]["property_source"] == "vendor"
    assert detail["detail"]["sent_length"] == 260
    assert detail["detail"]["expected"] == [
        {
            "field": "subject",
            "kind": "max_length",
            "rule_id": "dataverse-task-subject-length",
            "limit": 200,
            "expected": "at most 200 characters",
        }
    ]
    assert detail["detail"]["reason"] == DATAVERSE_VALIDATION_MESSAGE
    assert detail["detail"]["code"] == "0x80044331"


def test_the_detail_view_carries_the_doc_link_and_the_raw_vendor_error(log, room):
    result = record(
        log,
        room["id"],
        run_payload(
            "dataverse",
            rows(("t", "task", "dv-1", {})),
            200,
            [dataverse_error(400, "0x80044331", DATAVERSE_VALIDATION_MESSAGE, link=DOC_LINK)],
        ),
    )
    detail = log.row_detail(room["id"], result["rows"][0]["id"])

    assert detail["detail"]["doc_link"] == DOC_LINK
    assert detail["detail"]["raw"]["status"] == 400
    assert detail["detail"]["matched_rule"] == "dataverse-validation"
    assert "Quoted" in detail["detail"]["basis"]


def test_the_property_falls_back_to_the_rooms_own_rule_and_says_so(log, room):
    """The vendor named no property, so the room supplies one from its own metadata."""
    result = record(
        log,
        room["id"],
        run_payload(
            "hubspot",
            rows(("a", "contact", "t", {})),
            207,
            {
                "numErrors": 1,
                "results": [
                    {
                        "status": "error",
                        "category": "VALIDATION_ERROR",
                        "message": "no email",
                        "context": {"objectWriteTraceId": ["t"]},
                    }
                ],
            },
        ),
    )
    detail = log.row_detail(room["id"], result["rows"][0]["id"])

    assert detail["detail"]["property"] == "email"
    assert detail["detail"]["property_source"] == "preflight"
    assert detail["detail"]["expected"][0]["kind"] == "required"


def test_a_row_the_room_has_no_rule_for_says_so_rather_than_inventing_a_property(log, room):
    result = record(
        log,
        room["id"],
        run_payload(
            "hubspot",
            rows(("a", "contact", "t", {"email": "a@x.example"})),
            207,
            {
                "numErrors": 1,
                "results": [
                    {
                        "status": "error",
                        "category": "FORBIDDEN",
                        "message": "no permission",
                        "context": {"objectWriteTraceId": ["t"]},
                    }
                ],
            },
        ),
    )
    detail = log.row_detail(room["id"], result["rows"][0]["id"])

    assert detail["detail"]["property"] is None
    assert detail["detail"]["property_source"] is None
    assert detail["detail"]["passed_preflight"] is True
    assert "the CRM refused it" in detail["detail"]["hold_message"]


def test_a_vendor_property_wins_over_the_rooms_own(log, room):
    result = record(
        log,
        room["id"],
        run_payload(
            "hubspot",
            rows(("a", "contact", "t", {})),
            207,
            {"numErrors": 1, "results": [hubspot_failure("t", "bad", field="lastname")]},
        ),
    )
    detail = log.row_detail(room["id"], result["rows"][0]["id"])

    assert detail["detail"]["property"] == "lastname"
    assert detail["detail"]["property_source"] == "vendor"


def test_the_detail_view_explains_the_rooms_own_half_of_a_failure(log, room):
    """A row the pre-flight would have refused is a rule the configuration is missing."""
    result = record(
        log,
        room["id"],
        run_payload(
            "hubspot",
            rows(("a", "contact", "t", {"lastname": "Nobody"})),
            207,
            {"numErrors": 1, "results": [hubspot_failure("t", "bad", field="lastname")]},
        ),
    )
    detail = log.row_detail(room["id"], result["rows"][0]["id"])

    assert detail["detail"]["passed_preflight"] is False
    assert [v["field"] for v in detail["detail"]["preflight_violations"]] == ["email"]
    assert "the room holds no rule for this row's property" in detail["detail"]["hold_message"]


def test_the_detail_view_reports_the_retry_question_for_its_row(log, room):
    result = record(
        log,
        room["id"],
        run_payload("salesforce", rows(("a", "contact", "t", {})), 429, {"message": "slow down"}),
    )
    detail = log.row_detail(room["id"], result["rows"][0]["id"])

    assert detail["retry"]["attempts"] == 1
    assert detail["retry"]["max_attempts"] == MAX_ATTEMPTS
    assert detail["retry"]["disposition"] == "queued"
    assert detail["retry"]["next_retry_at"] is not None
    assert "a manual retry is not capped" in detail["retry"]["bound_is"]


def test_the_detail_view_carries_the_run_and_its_notes(log, room):
    result = record(
        log,
        room["id"],
        run_payload(
            "dataverse",
            rows(("a", "task", "t", {})),
            200,
            [dataverse_error(400, "0x80044331", DATAVERSE_VALIDATION_MESSAGE)],
        ),
    )
    detail = log.row_detail(room["id"], result["rows"][0]["id"])

    assert detail["run"]["id"] == result["run"]["id"]
    assert detail["run"]["http_status"] == 200
    assert any("a caller that read 200 as success" in note for note in detail["run"]["notes"])


def test_the_detail_view_for_a_row_on_another_room_is_refused(log, store, room):
    other = store.create("room", {"name": "Contoso"})
    result = record(
        log, room["id"], run_payload("hubspot", rows(hubspot_row(1)), 200, {"status": "success"})
    )

    with pytest.raises(UnknownRow):
        log.row_detail(other["id"], result["rows"][0]["id"])


def test_an_unknown_row_is_refused(log, room):
    with pytest.raises(UnknownRow):
        log.row_detail(room["id"], "crm_sync_row_nope")


# --------------------------------------------------------------------------- #
# Step five: retry failed rows only
# --------------------------------------------------------------------------- #


def test_retry_sends_the_failed_rows_and_leaves_the_successes_out(log, room):
    result = record(
        log,
        room["id"],
        run_payload(
            "hubspot",
            rows(hubspot_row(1), hubspot_row(2), hubspot_row(3)),
            207,
            {"numErrors": 1, "results": [hubspot_success("hs-t1"), hubspot_success("hs-t2"), hubspot_failure("hs-t3", "bad")]},
        ),
    )
    before = log.sync_log(room["id"])["summary"]["succeeded"]

    retry = log.retry_failed(result["run"]["id"], actor="dana", source="POST test", now=NOW)

    assert [item["row_key"] for item in retry["sent"]] == ["hs-3"]
    assert retry["untouched"] == ["hs-1", "hs-2"]
    assert retry["awaiting_vendor_response"] is True
    assert "will not be re-sent" in retry["detail"]
    assert log.sync_log(room["id"])["summary"]["succeeded"] == before


def test_retry_never_re_sends_a_succeeded_row_even_when_the_vendor_names_it(log, room):
    """The succeeded row is not in the normaliser's input, so it cannot be re-sent."""
    result = record(
        log,
        room["id"],
        run_payload(
            "hubspot",
            rows(hubspot_row(1), hubspot_row(2)),
            207,
            {"numErrors": 1, "results": [hubspot_success("hs-t1"), hubspot_failure("hs-t2", "bad")]},
        ),
    )
    ok_row = next(r for r in log.sync_log(room["id"])["rows"] if r["row_key"] == "hs-1")

    log.retry_failed(
        result["run"]["id"],
        {"vendor": {"status": 207, "body": {"results": [hubspot_success("hs-t1"), hubspot_success("hs-t2", "902")]}}},
        actor="dana",
        source="POST test",
        now=NOW + timedelta(minutes=1),
    )

    after = next(r for r in log.sync_log(room["id"])["rows"] if r["id"] == ok_row["id"])
    assert after["attempts"] == 1
    assert after["last_attempt_at"] == ok_row["last_attempt_at"]


def test_retry_applies_the_vendor_response_and_resolves_the_fixed_row(log, room):
    result = record(
        log,
        room["id"],
        run_payload(
            "salesforce",
            rows(("a", "contact", "sf-1", {"email": "a@x.example"})),
            403,
            {"errorCode": "REQUEST_LIMIT_EXCEEDED", "message": "no"},
        ),
    )

    retry = log.retry_failed(
        result["run"]["id"],
        {"vendor": {"status": 200, "body": {"results": [salesforce_result(True, record_id="003x")]}}},
        actor="dana",
        source="POST test",
        now=NOW + timedelta(minutes=5),
    )

    assert retry["retried"] == 1
    assert retry["succeeded_now"] == 1
    assert retry["still_failed"] == 0
    assert retry["applied"][0]["attempts"] == 2


def test_a_row_that_fails_again_keeps_its_attempt_count(log, room):
    result = record(
        log,
        room["id"],
        run_payload("salesforce", rows(("a", "contact", "sf-1", {})), 429, {"message": "slow"}),
    )

    retry = log.retry_failed(
        result["run"]["id"],
        {"vendor": {"status": 429, "body": {"results": [rate_limited()]}}},
        actor="dana",
        source="POST test",
        now=NOW + timedelta(minutes=5),
    )

    row = log.sync_log(room["id"])["rows"][0]
    assert retry["still_failed"] == 1
    assert row["attempts"] == 2
    assert row["status"] == "failed"
    assert row["disposition"] == "queued"
    assert len(log.row_detail(room["id"], row["id"])["history"]) == 2


def test_a_retry_on_a_clean_run_says_there_is_nothing_to_do(log, room):
    result = record(
        log,
        room["id"],
        run_payload(
            "hubspot",
            rows(hubspot_row(1), hubspot_row(2)),
            200,
            {"results": [hubspot_success("hs-t1"), hubspot_success("hs-t2")]},
        ),
    )

    retry = log.retry_failed(result["run"]["id"], actor="dana", source="POST test", now=NOW)

    assert retry["nothing_to_retry"] is True
    assert retry["retried"] == 0
    assert retry["untouched"] == ["hs-1", "hs-2"]
    assert "were not touched" in retry["detail"]


def test_a_manual_retry_is_not_capped_by_the_attempt_bound(log, room):
    result = record(
        log,
        room["id"],
        run_payload("salesforce", rows(("a", "contact", "t", {})), 429, {"message": "slow"}),
    )
    run_id = result["run"]["id"]
    for index in range(1, MAX_ATTEMPTS + 2):
        log.retry_failed(
            run_id,
            {"vendor": {"status": 429, "body": {"results": [rate_limited()]}}},
            actor="dana",
            source="POST test",
            now=NOW + timedelta(hours=index),
        )

    row = log.sync_log(room["id"])["rows"][0]
    assert row["attempts"] == MAX_ATTEMPTS + 2


def test_retrying_an_unknown_run_is_refused(log):
    with pytest.raises(UnknownRun):
        log.retry_failed("crm_sync_run_nope", actor="dana", source="POST test")


# --------------------------------------------------------------------------- #
# The drain
# --------------------------------------------------------------------------- #


def test_the_queue_separates_due_scheduled_expired_and_waiting(log, room):
    record(log, room["id"], run_payload("salesforce", rows(("a", "contact", "t1", {})), 429, {"message": "slow"}))
    record(log, room["id"], run_payload("hubspot", rows(("b", "contact", "t2", {})), 207,
                                        {"numErrors": 1, "results": [hubspot_failure("t2", "bad")]}))

    early = log.queue(room_id=room["id"], as_of=NOW.isoformat())
    later = log.queue(room_id=room["id"], as_of=(NOW + timedelta(hours=1)).isoformat())

    assert early["counts"]["scheduled"] == 1
    assert early["counts"]["drain"] == 0
    assert later["counts"]["drain"] == 1
    assert later["counts"]["waiting"] == 1
    assert [entry["row_key"] for entry in later["drain"]] == ["a"]
    assert [entry["row_key"] for entry in later["waiting"]] == ["b"]
    assert later["max_attempts"] == MAX_ATTEMPTS
    assert later["backoff"] == BACKOFF_LABEL


def test_a_drain_with_no_body_reports_the_batch_and_transports_nothing(log, store, room):
    record(log, room["id"], run_payload("salesforce", rows(("a", "contact", "t1", {"email": "a@x.example"})),
                                        429, {"message": "slow"}))

    body = log.drain(actor="dana", source="POST test", now=NOW + timedelta(hours=1))

    assert [item["row_key"] for item in body["sent"]] == ["a"]
    assert body["sent"][0]["values"] == {"email": "a@x.example"}
    assert body["sent"][0]["trace_id"] == "t1"
    assert body["applied"] == []
    assert "opens no socket" in body["transported_by"]
    # Nothing was written: a dry read of the queue is not an event.
    assert store.audit(action="update", collection=ROW_COLLECTION) == []


def test_a_drain_applies_the_connector_response_through_the_same_normaliser(log, room):
    record(log, room["id"], run_payload("salesforce", rows(("a", "contact", "sf-1", {})), 429, {"message": "slow"}))
    record(log, room["id"], run_payload("salesforce", rows(("b", "contact", "sf-2", {})), 429, {"message": "slow"}))

    body = log.drain(
        {
            "connector": "salesforce",
            "vendor": {
                "status": 200,
                "body": {
                    "results": [
                        salesforce_result(True, record_id="sf-1"),
                        salesforce_result(
                            False,
                            {"statusCode": "403", "errorCode": "REQUEST_LIMIT_EXCEEDED", "message": "still no"},
                            record_id="sf-2",
                        ),
                    ]
                },
            },
        },
        actor="dana",
        source="POST test",
        now=NOW + timedelta(hours=1),
    )

    assert body["counts"]["drain"] == 2
    assert len(body["applied"]) == 2
    by_key = {item["row_key"]: item for item in log.sync_log(room["id"])["rows"]}
    assert by_key["a"]["status"] == "succeeded"
    assert by_key["a"]["disposition"] == "resolved"
    assert by_key["b"]["status"] == "failed"
    assert by_key["b"]["attempts"] == 2
    assert by_key["b"]["disposition"] == "queued"


def test_a_row_past_the_bound_is_moved_out_of_the_automatic_queue(log, store, room):
    result = record(
        log,
        room["id"],
        run_payload("salesforce", rows(("a", "contact", "t1", {})), 429, {"message": "slow"}),
    )
    for index in range(1, MAX_ATTEMPTS):
        log.retry_failed(
            result["run"]["id"],
            {"vendor": {"status": 429, "body": {"results": [rate_limited()]}}},
            actor="dana",
            source="POST test",
            now=NOW + timedelta(hours=index),
        )
    assert log.sync_log(room["id"])["rows"][0]["attempts"] == MAX_ATTEMPTS

    body = log.drain(actor="dana", source="POST test", now=NOW + timedelta(hours=MAX_ATTEMPTS + 1))

    assert body["counts"]["expired"] == 1
    assert body["counts"]["drain"] == 0
    assert len(body["expired_now"]) == 1
    row = log.sync_log(room["id"])["rows"][0]
    detail = log.row_detail(room["id"], row["id"])
    assert row["disposition"] == "needs_action"
    assert row["next_retry_at"] is None
    assert "is not a throttle" in detail["queue_note"]
    assert store.audit(action="update", collection=ROW_COLLECTION)


def test_a_drain_with_nothing_to_expire_writes_nothing(log, store, room):
    record(log, room["id"], run_payload("salesforce", rows(("a", "contact", "t1", {})), 429, {"message": "slow"}))
    log.drain(actor="dana", source="POST test", now=NOW + timedelta(hours=1))

    before = len(store.audit(action="update", collection=ROW_COLLECTION))
    log.drain(actor="dana", source="POST test", now=NOW + timedelta(hours=2))

    assert len(store.audit(action="update", collection=ROW_COLLECTION)) == before


def test_a_drain_spanning_two_connectors_is_refused_rather_than_guessed_at(log, room):
    record(log, room["id"], run_payload("salesforce", rows(("a", "contact", "t1", {})), 429, {"message": "s"}))
    record(log, room["id"], run_payload("hubspot", rows(("b", "contact", "t2", {})), 429, {"message": "s"}))

    with pytest.raises(InvalidPayload) as raised:
        log.drain(
            {"vendor": {"status": 200, "body": {}}},
            actor="dana",
            source="POST test",
            now=NOW + timedelta(hours=1),
        )

    assert "more than one connector" in str(raised.value)


def test_a_drain_can_be_narrowed_to_one_connector(log, room):
    record(log, room["id"], run_payload("salesforce", rows(("a", "contact", "t1", {})), 429, {"message": "s"}))
    record(log, room["id"], run_payload("hubspot", rows(("b", "contact", "t2", {})), 429, {"message": "s"}))

    body = log.drain(
        {"vendor": {"status": 200, "body": {}}},
        connector="salesforce",
        actor="dana",
        source="POST test",
        now=NOW + timedelta(hours=1),
    )

    assert [item["row_key"] for item in body["sent"]] == ["a"]


def test_the_drain_counts_the_successes_it_did_not_resent(log, room):
    record(log, room["id"], run_payload("hubspot", rows(("a", "contact", "t1", {})), 200, {"status": "success"}))
    record(log, room["id"], run_payload("salesforce", rows(("b", "contact", "t2", {})), 429, {"message": "s"}))

    body = log.drain(actor="dana", source="POST test", now=NOW + timedelta(hours=1))

    assert body["succeeded_not_resent"] == 1


def test_a_drain_narrows_to_one_room_when_asked(log, store, room):
    other = store.create("room", {"name": "Contoso"})
    record(log, room["id"], run_payload("salesforce", rows(("a", "contact", "t1", {})), 429, {"message": "s"}))
    record(log, other["id"], run_payload("salesforce", rows(("b", "contact", "t2", {})), 429, {"message": "s"}))

    body = log.drain(room_id=room["id"], actor="dana", source="POST test", now=NOW + timedelta(hours=1))

    assert [item["row_key"] for item in body["sent"]] == ["a"]


# --------------------------------------------------------------------------- #
# Before the commit, over the store and the wire
# --------------------------------------------------------------------------- #


def test_the_preflight_writes_nothing_at_all(log, store):
    before = store.stats()["records"]
    audit_before = store.stats()["audit_entries"]

    verdict = log.preflight(
        {"connector": "hubspot", "rows": [{"row_key": "a", "entity": "contact", "values": {}}]}
    )

    assert verdict["refused"] == 1
    assert store.stats()["records"] == before
    assert store.stats()["audit_entries"] == audit_before


def test_a_refused_row_never_reaches_the_sync_log(log, room):
    """A row in the log means a vendor answered, and no request was sent."""
    payload = run_payload(
        "hubspot",
        [{"row_key": "a", "entity": "contact", "values": {}}],
        200,
        {"status": "success"},
    )
    result = record(log, room["id"], payload)

    assert result["rows"][0]["status"] == "succeeded"
    assert result["run"]["preflight"]["would_refuse"] == 1
    assert result["run"]["preflight"]["refused_rows"] == ["a"]
    assert any("would have refused" in note for note in result["run"]["notes"])


def test_a_refused_row_is_not_something_the_retry_action_will_resend(log, room):
    record(log, room["id"], run_payload("hubspot", rows(hubspot_row(1)), 200, {"status": "success"}))
    before = log.queue(room_id=room["id"], as_of=(NOW + timedelta(hours=1)).isoformat())["counts"]
    log.preflight(
        {"connector": "hubspot", "rows": [{"row_key": "a", "entity": "contact", "values": {}}]}
    )

    after = log.queue(room_id=room["id"], as_of=(NOW + timedelta(hours=1)).isoformat())["counts"]
    assert after == before


def test_a_run_with_no_rows_is_refused(log, store, room):
    with pytest.raises(InvalidPayload) as raised:
        record(log, room["id"], run_payload("hubspot", [], 200, {}))

    assert "a batch with no rows records nothing" in str(raised.value)
    assert store.list(RUN_COLLECTION) == []


def test_a_run_with_no_vendor_response_is_refused(log, room):
    with pytest.raises(InvalidPayload) as raised:
        log.record_run(
            {"connector": "hubspot", "rows": rows(hubspot_row(1))},
            room_id=room["id"],
            actor="dana",
            source="POST test",
            now=NOW,
        )

    assert "vendor is required" in str(raised.value)


def test_a_vendor_response_with_no_status_is_refused(log, room):
    with pytest.raises(InvalidPayload) as raised:
        record(
            log,
            room["id"],
            {"connector": "hubspot", "rows": rows(hubspot_row(1)), "vendor": {"body": {}}},
        )

    assert "vendor.status is required" in str(raised.value)


def test_a_row_with_no_key_is_refused_because_a_result_cannot_be_keyed_to_it(log, room):
    with pytest.raises(InvalidPayload) as raised:
        record(
            log,
            room["id"],
            run_payload("hubspot", [{"entity": "contact", "values": {}}], 200, {"status": "success"}),
        )

    assert "row_key" in str(raised.value)


def test_two_rows_sharing_a_key_are_refused(log, room):
    payload = run_payload(
        "hubspot",
        [{"row_key": "same", "values": {}}, {"row_key": "same", "values": {}}],
        200,
        {"status": "success"},
    )
    with pytest.raises(InvalidPayload) as raised:
        record(log, room["id"], payload)

    assert "would be ambiguous" in str(raised.value)


def test_a_row_whose_values_are_not_an_object_is_refused(log, room):
    with pytest.raises(InvalidPayload) as raised:
        record(log, room["id"], run_payload("hubspot", [{"row_key": "a", "values": ["x"]}], 200, {}))

    assert "values must be a JSON object" in str(raised.value)


def test_a_run_that_finished_before_it_started_is_refused(log, room):
    payload = run_payload(
        "hubspot",
        rows(hubspot_row(1)),
        200,
        {"status": "success"},
        started_at=NOW.isoformat(),
        finished_at=(NOW - timedelta(minutes=1)).isoformat(),
    )
    with pytest.raises(InvalidPayload) as raised:
        log.record_run(payload, room_id=room["id"], actor="dana", source="POST test", now=NOW)

    assert "before it started" in str(raised.value)


def test_an_unreadable_run_instant_is_refused(log, room):
    payload = run_payload(
        "hubspot", rows(hubspot_row(1)), 200, {"status": "success"}, started_at="the other day"
    )
    with pytest.raises(InvalidPayload) as raised:
        log.record_run(payload, room_id=room["id"], actor="dana", source="POST test", now=NOW)

    assert "not an instant this workflow can read" in str(raised.value)


def test_a_run_dated_far_in_the_future_is_refused(log, room):
    far = (NOW + timedelta(days=FUTURE_TOLERANCE_SECONDS + 10)).isoformat()
    payload = run_payload("hubspot", rows(hubspot_row(1)), 200, {"status": "success"}, started_at=far)
    with pytest.raises(InvalidPayload) as raised:
        log.record_run(payload, room_id=room["id"], actor="dana", source="POST test", now=NOW)

    assert "beyond the" in str(raised.value)


def test_a_run_with_no_room_is_refused(log):
    payload = run_payload("hubspot", rows(hubspot_row(1)), 200, {"status": "success"})
    with pytest.raises(UnknownRoom):
        log.record_run(payload, room_id="room_nope", actor="dana", source="POST test", now=NOW)

    with pytest.raises(UnknownRoom):
        log.record_run(payload, actor="dana", source="POST test", now=NOW)


def test_a_record_that_is_not_a_room_is_not_a_room(log, store):
    other = store.create(ROW_COLLECTION, {"status": "failed"}, actor="dana")

    with pytest.raises(UnknownRoom):
        log.require_room(other["id"])


def test_an_unknown_connector_is_refused_by_name_with_the_three_it_serves():
    from dsr.partial_failures.vocabulary import require_connector

    with pytest.raises(UnknownConnector) as raised:
        require_connector("pipedrive")

    assert "hubspot, dataverse, salesforce" in str(raised.value)


def test_a_connector_name_is_read_in_any_case():
    from dsr.partial_failures.vocabulary import require_connector

    assert require_connector(" HubSpot ") == "hubspot"
    assert require_connector("DATAVERSE") == "dataverse"


# --------------------------------------------------------------------------- #
# The history
# --------------------------------------------------------------------------- #


def test_the_attempt_history_grows_with_every_attempt_and_is_bounded(log, room):
    result = record(
        log,
        room["id"],
        run_payload("salesforce", rows(("a", "contact", "t", {})), 429, {"message": "s"}),
    )
    run_id = result["run"]["id"]

    for index in range(1, HISTORY_LIMIT + 3):
        log.retry_failed(
            run_id,
            {"vendor": {"status": 429, "body": {"results": [rate_limited()]}}},
            actor="dana",
            source="POST test",
            now=NOW + timedelta(minutes=index),
        )

    row = log.sync_log(room["id"])["rows"][0]
    detail = log.row_detail(room["id"], row["id"])

    assert row["attempts"] == HISTORY_LIMIT + 3
    assert len(detail["history"]) == HISTORY_LIMIT
    assert detail["history_truncated"] is True


def test_a_first_attempt_is_not_truncated_history(log, room):
    result = record(
        log, room["id"], run_payload("hubspot", rows(hubspot_row(1)), 200, {"status": "success"})
    )
    detail = log.row_detail(room["id"], result["rows"][0]["id"])

    assert len(detail["history"]) == 1
    assert detail["history_truncated"] is False


def test_the_first_attempt_instant_is_kept_across_retries(log, room):
    result = record(
        log, room["id"], run_payload("salesforce", rows(("a", "contact", "t", {})), 429, {"message": "s"})
    )
    first_seen = log.sync_log(room["id"])["rows"][0]["last_attempt_at"]

    log.retry_failed(
        result["run"]["id"],
        {"vendor": {"status": 429, "body": {"results": [rate_limited()]}}},
        actor="dana",
        source="POST test",
        now=NOW + timedelta(minutes=5),
    )

    detail = log.row_detail(room["id"], log.sync_log(room["id"])["rows"][0]["id"])
    assert detail["row"]["last_attempt_at"] != first_seen
    assert detail["history"][0]["at"] == first_seen


# --------------------------------------------------------------------------- #
# The inferences
# --------------------------------------------------------------------------- #


def test_the_inference_registry_names_the_sourced_half_it_sits_next_to():
    described = describe_inferences()

    assert described["sourced_automation"] == SOURCED_AUTOMATION
    assert described["sourced_extensibility"] == SOURCED_EXTENSIBILITY
    assert described["sourced_gap"] == SOURCED_GAP
    assert described["count"] == len(INFERENCES)


@pytest.mark.parametrize("entry", INFERENCES, ids=lambda e: e["id"])
def test_every_inference_is_named_bounded_and_changeable(entry):
    assert entry["id"]
    assert entry["topic"]
    assert entry["why"].strip()
    assert entry["value"] is not None
    assert entry["change_it"].strip()


@pytest.mark.parametrize("entry", INFERENCES, ids=lambda e: e["id"])
def test_every_inference_says_what_the_research_does_and_does_not_say(entry):
    basis = entry["basis"]
    assert "Sourced" in basis or "Not sourced" in basis, entry["id"]


def test_inference_ids_are_unique():
    ids = [entry["id"] for entry in INFERENCES]
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize(
    "inference_id",
    [
        "unknown-codes-are-terminal",
        "rate-limit-is-http-429",
        "lock-versus-precondition",
        "server-faults-are-retryable",
        "dataverse-correlation-is-positional",
        "the-property-falls-back-to-the-rooms-own-rules",
        "validation-rules-ship-with-three",
        "an-unenforced-rule-is-refused-not-ignored",
        "the-automatic-drain-is-bounded",
        "a-row-with-no-outcome-is-failed",
        "preflight-refuses-and-writes-nothing",
        "the-drain-is-the-connector-not-the-server",
        "numErrors-is-hubspots-to-reconcile",
    ],
)
def test_the_judgement_calls_the_research_left_open_are_all_published(inference_id):
    assert any(entry["id"] == inference_id for entry in INFERENCES)


def test_the_published_default_is_the_one_the_code_actually_applies():
    entry = next(e for e in INFERENCES if e["id"] == "unknown-codes-are-terminal")
    retryable, basis, matched = classify(connector="hubspot", code="NEVER_SEEN_BEFORE", http_status=418)

    assert entry["value"]["default"] == "terminal"
    assert entry["value"]["matched_rule"] == matched == UNRECOGNISED
    assert entry["value"]["reason_in_row"] is True
    assert basis == DEFAULT_BASIS
    assert retryable is False


def test_the_published_retry_bound_is_the_one_the_code_uses():
    entry = next(e for e in INFERENCES if e["id"] == "the-automatic-drain-is-bounded")

    assert entry["value"]["max_attempts"] == MAX_ATTEMPTS
    assert entry["value"]["backoff"] == BACKOFF_LABEL
    assert entry["value"]["on_expiry"] == "needs_action"


def test_the_published_preflight_kinds_are_the_ones_the_code_enforces():
    entry = next(e for e in INFERENCES if e["id"] == "validation-rules-ship-with-three")

    assert entry["value"]["kinds"] == list(PREFLIGHT_KINDS)
    assert entry["value"]["rules"] == [rule["id"] for rule in DEFAULT_PREFLIGHT_RULES]


def test_the_published_vendor_holding_is_also_carried_by_the_drain_docstring():
    entry = next(e for e in INFERENCES if e["id"] == "the-drain-is-the-connector-not-the-server")

    assert entry["value"]["room_transports_nothing"] is True
    body = " ".join((SyncLog.drain.__doc__ or "").split())
    assert "does not send them" in body
    assert "vendor credentials" in body


def test_the_published_silent_row_code_is_the_one_the_code_emits():
    entry = next(e for e in INFERENCES if e["id"] == "a-row-with-no-outcome-is-failed")
    outcome = normalise(
        "hubspot",
        200,
        {"results": [hubspot_success("t")]},
        rows(("a", "contact", "t", {}), ("b", "contact", "u", {})),
    )

    assert entry["value"]["code"] == "NO_PER_RECORD_OUTCOME"
    assert outcome.rows[1].error.code == entry["value"]["code"]


def test_the_published_hubspot_only_reconciliation_is_the_one_the_code_does():
    entry = next(e for e in INFERENCES if e["id"] == "numErrors-is-hubspots-to-reconcile")

    assert entry["value"]["reconciled_for"] == ["hubspot"]
    hubspot = normalise(
        "hubspot", 207, {"numErrors": 1, "results": [hubspot_failure("t", "x")]},
        rows(("a", "contact", "t", {})),
    )
    dataverse = normalise("dataverse", 200, [dataverse_ok()], rows(("a", "task", "t", {})))
    salesforce = normalise(
        "salesforce", 200, {"results": [salesforce_result(True)]}, rows(("a", "contact", "t", {}))
    )

    assert hubspot.consistent is True
    assert dataverse.reported_errors is None
    assert salesforce.reported_errors is None


def test_the_inferences_endpoint_serves_the_registry_next_to_the_sourced_half(http):
    body = http.get(f"{PREFIX}/inferences").json()

    assert body["count"] == len(INFERENCES)
    assert body["ids"]
    assert "rate limit" in body["sourced_automation"]


# --------------------------------------------------------------------------- #
# The HTTP surface
# --------------------------------------------------------------------------- #


def test_vocabulary_is_a_read_with_no_store(http):
    assert http.get(f"{PREFIX}/vocabulary").status_code == 200


def test_connectors_is_a_read_with_no_store(http):
    assert http.get(f"{PREFIX}/connectors").status_code == 200


def test_inferences_is_a_read_with_no_store(http):
    assert http.get(f"{PREFIX}/inferences").status_code == 200


def test_rules_can_be_read_and_patched_over_http(http):
    assert http.get(f"{PREFIX}/rules").json()["source"] == "defaults"

    patched = http.patch(
        f"{PREFIX}/rules",
        json={"preflight": [{"id": "extra", "connector": "hubspot", "entity": "deal", "field": "amount", "kind": "required"}]},
        params={"actor": "dana"},
    )

    assert patched.status_code == 200
    assert patched.json()["source"] == "override"
    assert patched.json()["effective"]["preflight"] == len(DEFAULT_PREFLIGHT_RULES) + 1
    assert patched.json()["stored"]["max_attempts"] == MAX_ATTEMPTS


def test_an_invalid_rules_patch_over_http_is_400_with_the_reason(http):
    response = http.patch(f"{PREFIX}/rules", json={"max_attempts": 0})

    assert response.status_code == 400
    assert "at least 1" in response.json()["detail"]
    assert response.json()["error"] == "InvalidRule"


def test_an_unknown_rule_key_over_http_is_400(http):
    response = http.patch(f"{PREFIX}/rules", json={"max_attempt": 3})

    assert response.status_code == 400
    assert "unknown rule(s)" in response.json()["detail"]


def test_validate_over_http_reports_the_hold_without_writing(http, http_room):
    before = http.get("/api/audit", params={"limit": 1000}).json()["count"]

    response = http.post(
        f"{PREFIX}/validate",
        json={"connector": "hubspot", "rows": [{"row_key": "a", "entity": "contact", "values": {}}]},
    )

    assert response.status_code == 200
    assert response.json()["hold"] == ["a"]
    assert response.json()["refused"] == 1
    assert http.get("/api/audit", params={"limit": 1000}).json()["count"] == before


def test_validate_with_an_unknown_connector_over_http_is_400(http):
    response = http.post(
        f"{PREFIX}/validate", json={"connector": "pipedrive", "rows": [{"row_key": "a", "values": {}}]}
    )

    assert response.status_code == 400
    assert response.json()["error"] == "UnknownConnector"


def test_recording_a_run_over_http_creates_one_audited_row_per_input_row(http, http_room):
    response = post_run(
        http,
        http_room["id"],
        "hubspot",
        rows(hubspot_row(1), hubspot_row(2)),
        207,
        {"numErrors": 1, "results": [hubspot_success("hs-t1"), hubspot_failure("hs-t2", "bad")]},
        actor="dana",
    )

    assert response.status_code == 201
    body = response.json()
    assert body["recorded"] is True
    assert body["run"]["succeeded"] == 1
    assert body["run"]["failed"] == 1

    entries = http.get("/api/audit", params={"collection": ROW_COLLECTION}).json()["entries"]
    assert len(entries) == 2
    assert {entry["source"] for entry in entries} == {f"POST {PREFIX}/runs"}
    assert {entry["actor"] for entry in entries} == {"dana"}


def test_a_run_for_an_unknown_room_over_http_is_404(http):
    response = http.post(
        f"{PREFIX}/runs",
        json=run_payload("hubspot", rows(hubspot_row(1)), 200, {"status": "success"}),
        params={"room_id": "room_nope"},
    )

    assert response.status_code == 404
    assert response.json()["error"] == "UnknownRoom"


def test_the_query_room_wins_over_the_body(http, http_room):
    other = http.post("/api/records/room", json={**ROOM, "name": "Contoso"}).json()

    response = http.post(
        f"{PREFIX}/runs",
        json=run_payload("hubspot", rows(hubspot_row(1)), 200, {"status": "success"}, room_id=http_room["id"]),
        params={"room_id": other["id"]},
    )

    assert response.status_code == 201
    assert response.json()["run"]["room_id"] == other["id"]
    assert http.get(f"{PREFIX}/rooms/{http_room['id']}/sync-log").json()["total"] == 0


def test_a_run_with_no_rows_over_http_is_400(http, http_room):
    response = post_run(http, http_room["id"], "hubspot", [], 200, {})

    assert response.status_code == 400
    assert response.json()["error"] == "InvalidPayload"


def test_reading_an_unknown_run_over_http_is_404(http):
    response = http.get(f"{PREFIX}/runs/crm_sync_run_nope")

    assert response.status_code == 404
    assert response.json()["error"] == "UnknownRun"


def test_the_run_route_returns_the_outcomes_and_the_notes(http, http_room):
    created = post_run(
        http,
        http_room["id"],
        "dataverse",
        rows(("a", "task", "t", {})),
        200,
        [dataverse_error(400, "0x80044331", DATAVERSE_VALIDATION_MESSAGE)],
    ).json()

    body = http.get(f"{PREFIX}/runs/{created['run']['id']}").json()

    assert body["http_status"] == 200
    assert body["outcomes"][0]["code"] == "0x80044331"
    assert any("a caller that read 200 as success" in note for note in body["notes"])


def test_the_sync_log_route_returns_the_summary_and_the_filters(http, http_room):
    post_run(
        http,
        http_room["id"],
        "hubspot",
        rows(hubspot_row(1), hubspot_row(2)),
        207,
        {"numErrors": 1, "results": [hubspot_success("hs-t1"), hubspot_failure("hs-t2", "bad")]},
    )

    body = http.get(f"{PREFIX}/rooms/{http_room['id']}/sync-log").json()
    assert body["summary"]["succeeded"] == 1
    assert body["summary"]["failed"] == 1

    failed_only = http.get(f"{PREFIX}/rooms/{http_room['id']}/sync-log", params={"status": "failed"})
    assert failed_only.json()["total"] == 1

    bad = http.get(f"{PREFIX}/rooms/{http_room['id']}/sync-log", params={"status": "nope"})
    assert bad.status_code == 400


def test_the_sync_log_for_an_unknown_room_over_http_is_404(http):
    assert http.get(f"{PREFIX}/rooms/room_nope/sync-log").status_code == 404


def test_the_row_detail_route_answers_which_property_sent_and_expected(http, http_room):
    created = post_run(
        http,
        http_room["id"],
        "dataverse",
        rows(("a", "task", "t", {"subject": "A" * 260})),
        200,
        [dataverse_error(400, "0x80044331", DATAVERSE_VALIDATION_MESSAGE)],
    ).json()

    body = http.get(f"{PREFIX}/rooms/{http_room['id']}/rows/{created['rows'][0]['id']}").json()

    assert body["detail"]["property"] == "subject"
    assert body["detail"]["sent_length"] == 260
    assert body["detail"]["expected"][0]["limit"] == 200
    assert body["retry"]["can_retry_now"] is False


def test_the_row_detail_route_for_an_unknown_row_is_404(http, http_room):
    assert http.get(f"{PREFIX}/rooms/{http_room['id']}/rows/nope").status_code == 404


def test_retry_over_http_leaves_the_successes_alone(http, http_room):
    created = post_run(
        http,
        http_room["id"],
        "hubspot",
        rows(hubspot_row(1), hubspot_row(2)),
        207,
        {"numErrors": 1, "results": [hubspot_success("hs-t1"), hubspot_failure("hs-t2", "bad")]},
    ).json()
    run_id = created["run"]["id"]

    first = http.post(f"{PREFIX}/runs/{run_id}/retry", json={}).json()
    assert [item["row_key"] for item in first["sent"]] == ["hs-2"]
    assert first["untouched"] == ["hs-1"]

    applied = retry_via(
        http, run_id, {"status": 207, "body": {"results": [hubspot_success("hs-t2", "902")]}}
    ).json()
    assert applied["retried"] == 1
    assert applied["succeeded_now"] == 1


def test_retrying_an_unknown_run_over_http_is_404(http):
    assert http.post(f"{PREFIX}/runs/crm_sync_run_nope/retry", json={}).status_code == 404


def test_the_queue_route_lists_the_two_researched_waiting_states(http, http_room):
    post_run(http, http_room["id"], "salesforce", rows(("a", "contact", "t1", {})), 429, {"message": "slow"})
    post_run(
        http,
        http_room["id"],
        "hubspot",
        rows(("b", "contact", "t2", {})),
        207,
        {"numErrors": 1, "results": [hubspot_failure("t2", "bad")]},
    )

    body = http.get(f"{PREFIX}/queue", params={"room_id": http_room["id"], "as_of": an_hour_hence()}).json()

    assert body["counts"]["drain"] == 1
    assert body["counts"]["waiting"] == 1
    assert [entry["row_key"] for entry in body["waiting"]] == ["b"]


def test_a_row_inside_its_backoff_is_scheduled_and_not_drained(http, http_room):
    post_run(http, http_room["id"], "salesforce", rows(("a", "contact", "t1", {})), 429, {"message": "slow"})

    body = http.post(f"{PREFIX}/queue/drain", json={}).json()

    assert body["sent"] == []
    assert body["counts"]["scheduled"] == 1
    assert "backs off" in body["scheduled"][0]["scheduled_because"]


def test_the_queue_route_can_be_read_as_of_a_later_instant(http, http_room):
    post_run(http, http_room["id"], "salesforce", rows(("a", "contact", "t1", {})), 429, {"message": "slow"})
    later = an_hour_hence()

    assert http.get(f"{PREFIX}/queue").json()["counts"]["scheduled"] == 1
    assert http.get(f"{PREFIX}/queue", params={"as_of": later}).json()["counts"]["drain"] == 1


def test_the_drain_route_with_no_body_transports_nothing_and_writes_nothing(http, http_room):
    post_run(http, http_room["id"], "salesforce", rows(("a", "contact", "t1", {})), 429, {"message": "slow"})
    before = http.get("/api/audit", params={"limit": 1000}).json()["count"]

    body = http.post(f"{PREFIX}/queue/drain", json={}, params={"as_of": an_hour_hence()}).json()

    assert [item["row_key"] for item in body["sent"]] == ["a"]
    assert http.get("/api/audit", params={"limit": 1000}).json()["count"] == before


def test_the_drain_route_applies_a_connector_response(http, http_room):
    post_run(http, http_room["id"], "salesforce", rows(("a", "contact", "t1", {})), 429, {"message": "slow"})

    body = http.post(
        f"{PREFIX}/queue/drain",
        json={"connector": "salesforce",
              "vendor": {"status": 200, "body": {"results": [salesforce_result(True, record_id="003x")]}}},
        params={"actor": "dana", "as_of": an_hour_hence()},
    ).json()

    assert len(body["applied"]) == 1
    log_rows = http.get(f"{PREFIX}/rooms/{http_room['id']}/sync-log").json()
    assert log_rows["rows"][0]["status"] == "succeeded"
    assert log_rows["rows"][0]["disposition"] == "resolved"


def test_a_drain_with_a_body_but_no_vendor_reports_the_batch_only(http, http_room):
    post_run(http, http_room["id"], "salesforce", rows(("a", "contact", "t1", {})), 429, {"message": "slow"})

    body = http.post(
        f"{PREFIX}/queue/drain", json={"connector": "salesforce"}, params={"as_of": an_hour_hence()}
    ).json()

    assert len(body["sent"]) == 1
    assert body["applied"] == []


def test_every_route_is_documented_in_openapi(http):
    documented = set(http.get("/openapi.json").json()["paths"])

    for path in ("/vocabulary", "/connectors", "/inferences", "/rules", "/validate", "/runs", "/queue", "/queue/drain"):
        assert f"{PREFIX}{path}" in documented
    assert f"{PREFIX}/runs/{{run_id}}" in documented
    assert f"{PREFIX}/runs/{{run_id}}/retry" in documented
    assert f"{PREFIX}/rooms/{{room_id}}/sync-log" in documented
    assert f"{PREFIX}/rooms/{{room_id}}/rows/{{row_id}}" in documented


def test_the_feature_is_discoverable_with_its_routes_over_http(http):
    body = http.get("/api/features").json()
    entry = next(f for f in body["features"] if f["id"] == FEATURE_ID)

    assert entry["nav"] == [{"id": "sync-log", "label": "Sync log"}]
    assert FEATURE_ID not in {failure["id"] for failure in body["failed"]}


def test_the_collections_this_workflow_owns_appear_in_schema_discovery(http, http_room):
    post_run(
        http,
        http_room["id"],
        "dataverse",
        rows(("a", "task", "t", {})),
        200,
        [dataverse_error(400, "0x80044331", DATAVERSE_VALIDATION_MESSAGE)],
    )

    body = http.get("/api/collections").json()
    names = {entry["collection"] for entry in body["collections"]}
    assert {RUN_COLLECTION, ROW_COLLECTION} <= names

    entry = next(e for e in body["collections"] if e["collection"] == ROW_COLLECTION)
    paths = {field["path"] for field in entry["fields"]}
    assert {"row_key", "status", "disposition", "field", "error.code", "error.basis"} <= paths


# --------------------------------------------------------------------------- #
# The audit guarantee
# --------------------------------------------------------------------------- #


def _matches_registered_route(source, routes):
    """Does ``"POST /api/wf-040/runs"`` name a route the host actually mounted?

    Compared segment by segment, with a ``{parameter}`` segment matching any one
    segment, so the assertion is about the shape of the path rather than about a
    hardcoded list that could itself go stale.
    """
    method, _, path = source.partition(" ")
    actual = [segment for segment in path.split("/") if segment]
    for route in routes:
        if method not in route["methods"]:
            continue
        template = [segment for segment in route["path"].split("/") if segment]
        if len(template) != len(actual):
            continue
        if all(expected.startswith("{") or expected == found for expected, found in zip(template, actual)):
            return True
    return False


def _write_everything(http, room_id):
    """Exercise every write route this feature owns, on one room."""
    post_run(http, room_id, "hubspot", rows(hubspot_row(1)), 200, {"status": "success"})
    http.patch(f"{PREFIX}/rules", json={"max_attempts": 3})
    http.post(f"{PREFIX}/queue/drain", json={}, params={"as_of": an_hour_hence()})
    # Last, so the rate-limited run is the newest and the retry below has a failed
    # row to act on. The route is named in the audit row whatever run it is.
    created = post_run(
        http,
        room_id,
        "salesforce",
        rows(("a", "contact", "t1", {"email": "a@x.example"})),
        429,
        {"message": "slow"},
    ).json()
    retry_via(http, created["run"]["id"], {"status": 429, "body": {"results": [rate_limited()]}})


def test_every_write_audit_row_names_a_route_the_app_serves(http, http_room):
    """The defect the feature contract names by name, checked against the live table."""
    _write_everything(http, http_room["id"])

    served = [
        route
        for feature in http.get("/api/features").json()["features"]
        for route in feature["routes"]
    ]
    entries = http.get("/api/audit", params={"limit": 1000}).json()["entries"]

    written = [
        entry
        for entry in entries
        if entry["collection"] in (ROW_COLLECTION, RUN_COLLECTION, RULES_COLLECTION)
    ]
    assert written, "the writes recorded no audit rows at all"
    for entry in written:
        assert _matches_registered_route(entry["source"], served), entry["source"]


def test_every_write_names_a_route_of_this_feature(http, http_room):
    """Every audit row this feature writes names a route of this feature."""
    _write_everything(http, http_room["id"])

    entries = http.get("/api/audit", params={"limit": 1000}).json()["entries"]
    written = [
        entry
        for entry in entries
        if entry["collection"] in (ROW_COLLECTION, RUN_COLLECTION, RULES_COLLECTION)
    ]
    assert written
    for entry in written:
        method, _, path = entry["source"].partition(" ")
        assert method in ("POST", "PATCH"), entry["source"]
        assert path.startswith(f"{PREFIX}/"), entry["source"]


def test_the_routes_that_write_are_exactly_the_ones_that_audit_a_write(http, http_room):
    _write_everything(http, http_room["id"])

    entries = http.get("/api/audit", params={"limit": 1000}).json()["entries"]
    written = [
        entry
        for entry in entries
        if entry["collection"] in (ROW_COLLECTION, RUN_COLLECTION, RULES_COLLECTION)
    ]
    paths = {entry["source"].partition(" ")[2] for entry in written}

    expected = {f"{PREFIX}/runs", f"{PREFIX}/rules", f"{PREFIX}/queue/drain"}
    for path in paths:
        if path in expected:
            continue
        # The only other shape is the retry route, whose audit row carries the run id.
        head, _, tail = path.partition(f"{PREFIX}/runs/")
        assert head == "" and tail.endswith("/retry") and "/" not in tail[:-len("/retry")], path
    assert any(path.startswith(f"{PREFIX}/runs/") for path in paths)


def test_the_domain_layer_refuses_to_write_without_a_source():
    """``source`` is a required keyword, so a hardcoded string cannot creep back in."""
    import inspect

    for method in (SyncLog.record_run, SyncLog.retry_failed, SyncLog.drain, SyncLog.save_rules):
        parameter = inspect.signature(method).parameters["source"]
        assert parameter.default is inspect.Parameter.empty
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY


def test_no_domain_module_hardcodes_a_literal_string_as_an_audit_source():
    """The other half of the same rule: no literal string passed as ``source=``."""
    import ast

    package = Path(SyncLog.__module__.replace(".", "/") + ".py").parent
    for module in sorted(package.glob("*.py")):
        tree = ast.parse(module.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for keyword in node.keywords:
                if keyword.arg == "source":
                    assert not isinstance(
                        keyword.value, ast.Constant
                    ), f"{module.name} passes a literal string as source="


def test_the_domain_layer_imports_no_transport():
    """The researched automation is driven by the connector; the room opens no socket."""
    import ast

    forbidden = {"urllib", "urllib.request", "http", "http.client", "socket", "requests", "httpx"}
    package = Path(SyncLog.__module__.replace(".", "/") + ".py").parent
    for module in sorted(package.glob("*.py")):
        tree = ast.parse(module.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            for name in names:
                assert name not in forbidden, f"{module.name} imports {name}"


def test_reads_leave_no_audit_rows(http, http_room):
    post_run(http, http_room["id"], "hubspot", rows(hubspot_row(1)), 200, {"status": "success"})
    before = http.get("/api/audit", params={"limit": 1000}).json()["count"]

    http.get(f"{PREFIX}/vocabulary")
    http.get(f"{PREFIX}/connectors")
    http.get(f"{PREFIX}/inferences")
    http.get(f"{PREFIX}/rules")
    http.get(f"{PREFIX}/runs")
    http.get(f"{PREFIX}/queue")
    http.get(f"{PREFIX}/rooms/{http_room['id']}/sync-log")
    http.post(f"{PREFIX}/validate", json={"connector": "hubspot", "rows": rows(hubspot_row(1))})

    assert http.get("/api/audit", params={"limit": 1000}).json()["count"] == before


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #


def _seed_once(tmp_path, room_ids, name="seed"):
    """Run the feature's ``seed`` and read everything back while the db is open.

    The demo rooms are created here because the seeder is handed ids, not records:
    the real seeder has already written the rooms by the time a feature's ``seed``
    is called, and a feature that invented its own rooms would be demo data no
    other workflow could see.

    Everything is read inside the ``with``, because the audited database closes its
    connection on the way out and reading it afterwards is a test bug masquerading
    as a seed bug.
    """
    module = load_feature(MODULE)
    with AuditedDatabase(tmp_path / f"{name}.db", mirror_dir=tmp_path / f"{name}-audit") as db:
        for room_id, account in room_ids:
            db.create(
                "room", {**ROOM, "name": account, "account": account},
                record_id=room_id, actor="dana", source="seed",
            )
        summary = module.seed(db, {"room_ids": room_ids, "now": NOW, "rng": random.Random("wf040")})
        sync = SyncLog(RecordStore(db))
        return {
            "summary": summary,
            "runs": db.list(RUN_COLLECTION),
            "rows": db.list(ROW_COLLECTION),
            "audit": db.audit(limit=500),
            "queue": sync.queue(as_of=NOW.isoformat()),
            "queue_later": sync.queue(as_of=(NOW + timedelta(hours=1)).isoformat()),
            "logs": {room_id: sync.sync_log(room_id) for room_id, _account in room_ids},
        }


DEMO_ROOMS = [(f"room_{name}", name) for name in ("northwind", "contoso", "fabrikam", "adventure")]


def _demo_row(seeded, row_key):
    return next(r for r in seeded["rows"] if r["data"]["row_key"] == row_key)["data"]


def test_the_demo_seeds_a_partial_failure_with_the_property_named(tmp_path):
    seeded = _seed_once(tmp_path, DEMO_ROOMS)
    row = _demo_row(seeded, "northwind-2")

    assert row["status"] == "failed"
    assert row["field"] == "lastname"
    assert row["field_basis"] == "vendor"
    assert row["error"]["code"] == "VALIDATION_ERROR"
    assert row["disposition"] == "needs_action"


def test_the_demo_seeds_an_error_that_could_not_be_keyed_to_a_row(tmp_path):
    """A dropped error is a row nobody will ever fix, so the demo shows one."""
    seeded = _seed_once(tmp_path, DEMO_ROOMS)
    run = next(r for r in seeded["runs"] if r["data"]["connector"] == "hubspot" and r["data"]["unattributed"])

    assert len(run["data"]["unattributed"]) == 1
    assert run["data"]["unattributed"][0]["correlation"] == "hs-nw-unattributed"
    assert any("could not be keyed to a row" in note for note in run["data"]["notes"])


def test_the_demo_seeds_a_dataverse_200_that_is_not_a_success(tmp_path):
    seeded = _seed_once(tmp_path, DEMO_ROOMS)
    run = next(r for r in seeded["runs"] if r["data"]["connector"] == "dataverse")

    assert run["data"]["http_status"] == 200
    assert run["data"]["per_record"] is True
    assert run["data"]["succeeded"] == 1
    assert run["data"]["failed"] == 2
    assert any("a caller that read 200 as success" in note for note in run["data"]["notes"])


def test_the_demo_seeds_the_researched_validation_error_with_its_doc_link(tmp_path):
    row = _demo_row(_seed_once(tmp_path, DEMO_ROOMS), "contoso-2")

    assert row["error"]["code"] == "0x80044331"
    assert row["field"] == "subject"
    assert row["error"]["doc_link"].startswith("https://learn.microsoft.com/")
    assert row["expected"][0]["limit"] == 200


def test_the_demo_seeds_the_run_the_rooms_own_rules_would_have_refused(tmp_path):
    """The pre-flight half, visible on a run that went out anyway."""
    seeded = _seed_once(tmp_path, DEMO_ROOMS)
    run = next(r for r in seeded["runs"] if r["data"]["connector"] == "dataverse")

    assert run["data"]["preflight"]["would_refuse"] == 1
    assert run["data"]["preflight"]["refused_rows"] == ["contoso-2"]
    assert any("would have refused" in note for note in run["data"]["notes"])


def test_the_demo_seeds_a_run_where_everything_succeeded(tmp_path):
    """A demo of only failures would not show what a clean sync looks like."""
    log = _seed_once(tmp_path, DEMO_ROOMS)["logs"]["room_adventure"]

    assert log["summary"]["succeeded"] == 2
    assert log["summary"]["failed"] == 1


def test_the_demo_seeds_a_permission_failure_that_names_no_property(tmp_path):
    row = _demo_row(_seed_once(tmp_path, DEMO_ROOMS), "adventure-3")

    assert row["field"] is None
    assert row["field_basis"] is None
    assert row["error"]["code"] == "FORBIDDEN"
    assert row["passed_preflight"] is True


def test_the_demo_actually_runs_the_retry_queue(tmp_path):
    """The researched automation, doing both of its jobs, on real rows."""
    seeded = _seed_once(tmp_path, DEMO_ROOMS)
    accepted = _demo_row(seeded, "fabrikam-1")
    throttled = _demo_row(seeded, "fabrikam-2")

    # Read at the moment the seed ran, and an hour later: the second row cleared the
    # queue but is still inside its backoff at the first instant, which is the state
    # a rep most needs to see on the page.
    assert seeded["queue"]["counts"]["drain"] == 0
    assert seeded["queue"]["counts"]["scheduled"] == 1
    assert seeded["queue"]["counts"]["waiting"] == 5
    assert accepted["status"] == "succeeded"
    assert accepted["disposition"] == "resolved"
    assert throttled["status"] == "failed"
    assert throttled["attempts"] == 2
    assert throttled["disposition"] == "queued"
    assert seeded["queue_later"]["counts"]["drain"] == 1


def test_the_demo_leaves_both_researched_waiting_states_on_screen(tmp_path):
    seeded = _seed_once(tmp_path, DEMO_ROOMS)
    dispositions = {row["data"].get("disposition") for row in seeded["rows"]}

    assert "queued" in dispositions
    assert "needs_action" in dispositions
    assert "resolved" in dispositions


def test_every_seeded_run_and_row_is_audited_to_the_seeder(tmp_path):
    seeded = _seed_once(tmp_path, DEMO_ROOMS)

    assert seeded["runs"]
    assert seeded["rows"]
    mine = [entry for entry in seeded["audit"] if entry["collection"] in (RUN_COLLECTION, ROW_COLLECTION)]
    # One insert per run and per row, plus the drain's own updates. The point of the
    # assertion is the source, not the arithmetic.
    assert len(mine) >= len(seeded["runs"]) + len(seeded["rows"])
    assert {entry["source"] for entry in mine} == {"seed"}


def test_the_demo_covers_all_four_demo_rooms(tmp_path):
    seeded = _seed_once(tmp_path, DEMO_ROOMS)

    assert {row["room_id"] for row in seeded["rows"]} == {room_id for room_id, _ in DEMO_ROOMS}


def test_the_demo_says_what_it_seeded_and_why(tmp_path):
    summary = _seed_once(tmp_path, DEMO_ROOMS)["summary"]

    assert "5 sync runs" in summary
    assert "unattributed" in summary
    assert "0x80044331" in summary
    assert "queued" in summary
    assert "one real drain" in summary
    assert ROW_COLLECTION in summary


def test_the_demo_survives_a_database_with_no_rooms(tmp_path):
    """The seeder skips a feature loudly rather than aborting."""
    seeded = _seed_once(tmp_path, [])

    assert seeded["runs"] == []
    assert seeded["rows"] == []
    assert "no rooms to scope them to" in seeded["summary"]


def test_the_demo_is_deterministic(tmp_path):
    """Two seeds of the same plan produce the same rows.

    The comparison is on the *set* of row keys rather than on the order they come
    back in, and that is the whole fix. The rows are read with ``db.list``,
    ordered by ``updated_at`` and then by insertion ``rowid``. The demo plan is
    fixed and the seeder is deterministic, so which rows exist is not in
    question - but the two rows the drain rewrites (``fabrikam-1`` and
    ``fabrikam-2``) are the only ones whose ``updated_at`` moves during the
    seed, and the two writes land in the same millisecond whenever the machine
    is slow enough for the clock to repeat.

    A tie then falls to ``rowid``, which is ``fabrikam-1`` before
    ``fabrikam-2`` - the reverse of what the untied millisecond order gives,
    because the drain retries them in reverse. On a fast laptop the clock
    always advances between the two writes, so the test passed every time; on the
    CI runner it tied, and the two seeds disagreed on one element:

        At index 0 diff: 'fabrikam-2' != 'fabrikam-1'

    Asserting on order here was asserting on how quickly the host can write,
    which is not a property of the demo. Asserting on the keys asserts what the
    test is named for, and holds on a fast machine and a slow one alike.
    """
    first = _seed_once(tmp_path, DEMO_ROOMS, name="a")
    second = _seed_once(tmp_path, DEMO_ROOMS, name="b")

    assert first["summary"] == second["summary"]
    assert {row["data"]["row_key"] for row in first["rows"]} == {
        row["data"]["row_key"] for row in second["rows"]
    }
    # Same keys *and* same per-row outcome: a set alone would miss a seed that
    # produced the same rows with different statuses.
    assert {row["data"]["row_key"]: row["data"].get("status") for row in first["rows"]} == {
        row["data"]["row_key"]: row["data"].get("status") for row in second["rows"]
    }


def test_the_demo_survives_a_core_dataset_with_fewer_rooms(tmp_path):
    """The seeder hands over whatever rooms exist; a plan entry past them is skipped."""
    seeded = _seed_once(tmp_path, DEMO_ROOMS[:2])

    assert len(seeded["runs"]) == 2
    assert {row["room_id"] for row in seeded["rows"]} == {"room_northwind", "room_contoso"}


# --------------------------------------------------------------------------- #
# Boundary properties, asserted once rather than by eye
# --------------------------------------------------------------------------- #


def test_no_vendor_response_ever_produces_a_row_the_room_cannot_explain():
    """Every row outcome carries a code, a basis and a reason - or it is a success."""
    rng = random.Random(20260927)
    for _ in range(300):
        connector = rng.choice(list(CONNECTORS))
        count = rng.randint(1, 4)
        rows_in = [
            {"row_key": f"r{index}", "entity": "contact", "trace_id": f"t{index}", "values": {}}
            for index in range(count)
        ]
        status = rng.choice([200, 207, 400, 403, 412, 429, 500])
        body = {
            "status": "error",
            "numErrors": rng.randint(0, 3),
            "results": [
                {
                    "status": rng.choice(["success", "error"]),
                    "category": rng.choice(["VALIDATION_ERROR", "FORBIDDEN", "RATE_LIMIT"]),
                    "message": "m",
                    "context": {"objectWriteTraceId": [f"t{rng.randint(0, count)}"]},
                }
                for _ in range(rng.randint(0, count + 1))
            ],
        }
        outcome = normalise(connector, status, body, rows_in)
        assert len(outcome.rows) == count
        for row in outcome.rows:
            if row.status == "failed":
                assert row.error is not None
                assert row.error.code
                assert row.error.basis
                assert row.error.vendor == connector
            else:
                assert row.error is None


def test_a_queued_row_is_never_one_a_terminal_classification_produced(log, room):
    """The disposition follows from the classification, on the real write path."""
    record(
        log,
        room["id"],
        run_payload(
            "dataverse",
            rows(("a", "task", "t1", {}), ("b", "task", "t2", {})),
            200,
            [
                dataverse_error(400, "0x80044331", DATAVERSE_VALIDATION_MESSAGE),
                dataverse_error(429, "TooManyRequests", "throttled"),
            ],
        ),
    )

    by_key = {row["row_key"]: row for row in log.sync_log(room["id"])["rows"]}
    assert by_key["a"]["retryable"] is False
    assert by_key["a"]["disposition"] == "needs_action"
    assert by_key["b"]["retryable"] is True
    assert by_key["b"]["disposition"] == "queued"


def test_adding_a_routing_rule_never_changes_which_rows_are_recorded(log, room):
    """A rule changes the classification, never the set of rows the log holds."""
    payload = run_payload(
        "hubspot",
        rows(hubspot_row(1), hubspot_row(2)),
        207,
        {"numErrors": 1, "results": [hubspot_success("hs-t1"), hubspot_failure("hs-t2", "bad")]},
    )
    before = {row["row_key"] for row in log.sync_log(room["id"])["rows"]}

    log.save_rules(
        {"routing": [{"id": "everything-retryable", "when": {"connector": "hubspot"},
                      "then": {"retryable": True}, "basis": "a deliberately absurd rule, for the test"}]},
        actor="dana",
        source="PATCH test",
    )
    result = record(log, room["id"], payload, at=NOW + timedelta(hours=1))

    after = {row["row_key"] for row in log.sync_log(room["id"])["rows"]}
    assert before <= after
    retried = [
        row
        for row in log.sync_log(room["id"], run_id=result["run"]["id"])["rows"]
        if row["status"] == "failed"
    ]
    assert retried and all(row["disposition"] == "queued" for row in retried)


def test_a_refused_row_is_never_a_silent_one_and_a_silent_one_is_never_refused(log):
    """The two "you cannot tell what happened" cases stay distinguishable.

    A row the pre-flight refused was never sent. A row the response ignored *was*
    sent and got nothing back. They share a classification - both terminal, both
    waiting for a person - and they must not share a reason.
    """
    refused = log.preflight(
        {"connector": "hubspot", "rows": [{"row_key": "a", "entity": "contact", "values": {}}]}
    )
    silent = normalise(
        "hubspot",
        200,
        {"results": [hubspot_success("t")]},
        rows(("a", "contact", "t", {}), ("b", "contact", "u", {})),
    )
    error = silent.rows[1].error

    assert refused["refused"] == 1
    assert refused["hold"] == ["a"]
    assert error.code == "NO_PER_RECORD_OUTCOME"
    assert "It was sent" in error.message
    assert error.retryable is False
    assert error.scope == "row"
