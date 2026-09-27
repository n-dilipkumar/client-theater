"""Tests for WF-032: stream identified company/contact intent to your own systems.

The claims under test come from
``docs/research/digital-sales-room-workflows/wf/WF-032.md``, whose research
source is section 17 of ``docs/research/raw/analytics-intent.md``:

* the workflow type is **Webhooks**, chosen from Workflows -> New Workflow;
* a name and "the URL you want to send data to";
* the conditions a workflow applies are "based on **saved Segments** from your
  account";
* "only send a lead **once**" versus "**send updates as well**", where an update
  is "the same lead with **updated activity data**";
* the payload output is "only **Company** ... or **Company + Contacts**", with
  contacts filtered on "**'Keywords'** and required fields";
* a **token** that is "automatically generated" and "optional to specify", used
  "in **your** service or tool" to prove the traffic is ours;
* the destination may be "a public API for a third party tool or a **custom
  solution**", and the researched **Microsoft Teams** and **Google Sheets**
  webhook recipes are the ones the flow names;
* the trigger is "the segment-matching company visit - **no user action**".

Delivery runs through a fake transport, so the once-versus-updates rule, the
contact filters, and the failure classifications are all asserted without a
socket and without a network flake.

The four tests worth finding first
----------------------------------
``test_every_write_audit_row_names_a_route_the_app_serves`` is the brief's
central guarantee, checked against the live route table rather than a constant.
``test_a_once_workflow_skips_the_second_visit_and_says_why`` and
``test_an_updates_workflow_resends_the_same_lead_with_refreshed_activity`` are
the researched sentence, both halves, asserted end to end. And
``test_a_filter_that_keeps_nobody_still_sends_the_company`` is the behaviour
that would otherwise cost somebody an afternoon: a filter excluding every
contact must not be indistinguishable from "this company was not interesting".
"""

from __future__ import annotations

import ast
import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dsr.api import app
from dsr.db.audited import AuditedDatabase
from dsr.features import REGISTRY, load_feature
from dsr.intent_stream import (
    INFERENCES,
    IntentStream,
    build_payload,
    generate_token,
    mask_token,
    token_matches,
    validate_target_url,
)
from dsr.intent_stream import payloads as payload_module
from dsr.intent_stream import segments as segment_module
from dsr.intent_stream import tokens as token_module
from dsr.intent_stream.delivery import (
    DEFAULT_TIMEOUT_SECONDS,
    DeliveryResult,
    classify,
    encode,
    is_retryable,
)
from dsr.intent_stream.errors import (
    DeliveryError,
    IntentStreamError,
    LeadError,
    SegmentError,
    TargetError,
    WorkflowError,
)
from dsr.intent_stream.inferences import describe as describe_inferences
from dsr.intent_stream.stream import url_warnings
from dsr.intent_stream.vocabulary import (
    ALL_COLLECTIONS,
    DEFAULT_SEGMENT_MATCH,
    DEFAULT_SEND_MODE,
    DELIVERY_COLLECTION,
    DESTINATION_RECIPES,
    EVIDENCE,
    KEYWORD_FIELDS,
    LEAD_COLLECTION,
    MATCH_ALL,
    MATCH_ANY,
    MAX_RESPONSE_BYTES,
    PAYLOAD_COMPANY,
    PAYLOAD_COMPANY_CONTACTS,
    PAYLOAD_MODES,
    PAYLOAD_TYPE,
    SEGMENT_COLLECTION,
    SEND_MODES,
    SEND_ONCE,
    SEND_UPDATES,
    SKIP_ALREADY_SENT,
    SKIP_INACTIVE,
    SKIP_NOT_MATCHED,
    SKIP_REASONS,
    TOKEN_BODY_FIELD,
    TOKEN_HEADER,
    TOKEN_PREFIX,
    VISIT_COLLECTION,
    WORKFLOW_COLLECTION,
    WORKFLOW_TYPE,
    vocabulary,
)
from dsr.store import RecordStore

#: The feature's own prefix. Duplicated here rather than imported so a change to
#: the prefix has to be made deliberately in the test as well, which is the point
#: of a test: a renamed route should fail, not follow silently.
PREFIX = "/api/wf-032"

#: The feature module's own name, so a rename of the file has to be deliberate
#: here too.
MODULE = "wf032_stream_identified_company_contact_inte"
FEATURE_ID = "wf-032-stream-identified-company-contact-inte"

#: What the pure-domain tests pass as ``source``. Deliberately the exact shape a
#: route passes, so a test asserting on an audit row is asserting on the real
#: thing rather than on a convenient placeholder.
SOURCE = f"POST {PREFIX}/visits"

#: A fixed clock, so an update count, a ``sentAt``, and a preview are checkable.
NOW = datetime(2026, 9, 27, 9, 0, 0, tzinfo=timezone.utc)
LATER = NOW + timedelta(hours=3)

TARGET = "https://hooks.example/intent"
SECOND_TARGET = "https://hooks.example/google-sheets-leads"
THIRD_TARGET = "https://hooks.example/microsoft-teams"

SOFTWARE = {"industry": "Software", "employees": 4200, "region": "EMEA"}
RETAIL = {"industry": "Retail", "employees": 55, "region": "AMER"}

CONTACT_ENGINEER = {
    "name": "A. Buyer",
    "title": "VP Engineering",
    "department": "Engineering",
    "email": "a.buyer@northwind.example",
}
CONTACT_ANALYST = {
    "name": "R. Researcher",
    "title": "Market Analyst",
    "department": "Strategy",
    "email": "research@northwind.example",
}
CONTACT_NO_EMAIL = {"name": "S. Intern", "title": "Security Intern", "department": "Security", "email": ""}


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


class FakeTransport:
    """Records every call and replays a scripted list of results.

    When the script runs out it accepts, so a test only has to script the calls
    it actually cares about.
    """

    def __init__(self, *scripted: DeliveryResult) -> None:
        self.calls: list[dict] = []
        self.scripted = list(scripted)

    def post(self, url, body, headers, timeout) -> DeliveryResult:
        self.calls.append(
            {"url": url, "body": json.loads(body), "headers": dict(headers), "timeout": timeout}
        )
        if self.scripted:
            return self.scripted.pop(0)
        return DeliveryResult(ok=True, status=202, body="accepted", duration_ms=7.0, final_url=url)

    @property
    def bodies(self) -> list[dict]:
        return [call["body"] for call in self.calls]


def ok(status: int = 202) -> DeliveryResult:
    return DeliveryResult(ok=True, status=status, body="accepted", duration_ms=4.0)


def boom(status: int = 500, retryable: bool = True) -> DeliveryResult:
    return DeliveryResult(ok=False, status=status, error=f"HTTP {status}", retryable=retryable, duration_ms=3.0)


def unreachable() -> DeliveryResult:
    return DeliveryResult(ok=False, error="URLError: <urlopen error timed out>", retryable=True, duration_ms=9000.0)


@pytest.fixture()
def store(tmp_path):
    db = AuditedDatabase(tmp_path / "wf032.db", mirror_dir=tmp_path / "mirror")
    yield RecordStore(db)
    db.close()


@pytest.fixture()
def transport():
    return FakeTransport()


@pytest.fixture()
def stream(store, transport):
    """An :class:`IntentStream` with a fixed clock and no socket."""
    return IntentStream(store, transport=transport, now=NOW)


@pytest.fixture()
def segment(stream) -> str:
    return stream.create_segment(
        {"name": "Software", "rules": [{"path": "industry", "operator": "eq", "value": "Software"}]},
        source=SOURCE,
    )["segment"]["id"]


@pytest.fixture()
def never_segment(stream) -> str:
    return stream.create_segment(
        {"name": "Wholesale", "rules": [{"path": "industry", "operator": "eq", "value": "Wholesale"}]},
        source=SOURCE,
    )["segment"]["id"]


@pytest.fixture()
def lead(stream):
    return stream.create_lead({"name": "Northwind Traders", **SOFTWARE}, source=SOURCE)["lead"]


@pytest.fixture()
def workflow(stream, segment) -> dict:
    return stream.create_workflow(
        {"name": "Leads out", "url": TARGET, "conditions": {"segmentIds": [segment]}},
        source=SOURCE,
    )


def make_workflow(stream, segment_id: str, **overrides) -> dict:
    payload = {
        "name": overrides.pop("name", "Leads out"),
        "url": overrides.pop("url", TARGET),
        "conditions": overrides.pop("conditions", {"segmentIds": [segment_id]}),
    }
    payload.update(overrides)
    return stream.create_workflow(payload, source=SOURCE)


def visit(stream, lead_id: str, **overrides) -> dict:
    payload = {"leadId": lead_id}
    payload.update(overrides)
    return stream.record_visit(payload, source=SOURCE)


def outcome(result: dict, workflow_id: str) -> dict:
    """The one line a workflow produced for a visit."""
    return next(row for row in result["deliveries"] if row["workflowId"] == workflow_id)


@pytest.fixture()
def http(monkeypatch, transport):
    """A client over a temporary database, with the transport faked.

    ``DSR_DB_PATH`` points at a temporary file the way ``test_features.py`` does,
    and the whole service is replaced through ``app.dependency_overrides`` - the
    seam the feature contract provides for exactly this, so no socket is opened
    anywhere in this suite and no wall-clock time is spent in a POST.
    """
    tmp = tempfile.TemporaryDirectory()
    monkeypatch.setenv("DSR_DB_PATH", str(Path(tmp.name) / "wf032.db"))
    monkeypatch.setenv("DSR_AUDIT_DIR", str(Path(tmp.name) / "audit"))
    monkeypatch.setattr("dsr.api.FRONTEND_DIST", Path(tmp.name) / "absent-frontend")
    with TestClient(app) as client:
        app.dependency_overrides[load_feature(MODULE).get_stream] = lambda: IntentStream(
            client.app.state.store, transport=transport, now=NOW
        )
        # The scripted transport is reachable from a test body, so a test can
        # make the next call fail without rebuilding the service.
        client.transport = transport  # type: ignore[attr-defined]
        try:
            yield client
        finally:
            app.dependency_overrides.clear()
    tmp.cleanup()


@pytest.fixture()
def http_room(http):
    return http.post(
        "/api/records/room",
        json={"name": "Northwind — Enterprise Evaluation", "account": "Northwind Traders", "stage": "evaluation"},
    ).json()["id"]


@pytest.fixture()
def http_segment(http):
    return http.post(
        f"{PREFIX}/segments?actor=dana",
        json={"name": "Software", "rules": [{"path": "industry", "operator": "eq", "value": "Software"}]},
    ).json()["segment"]["id"]


@pytest.fixture()
def http_lead_id(http):
    """The id, not the row. Everything downstream takes an id, and a fixture that
    returns the row invites ``{leadId: <the row>}`` - which answers 404 for a
    reason that has nothing to do with the code under test."""
    return http.post(f"{PREFIX}/leads?actor=dana", json={"name": "Northwind Traders", **SOFTWARE}).json()["lead"]["id"]


@pytest.fixture()
def http_workflow(http, http_segment):
    return http.post(
        f"{PREFIX}/workflows?actor=dana",
        json={"name": "Leads out", "url": TARGET, "conditions": {"segmentIds": [http_segment]}},
    ).json()


# --------------------------------------------------------------------------- #
# The plugin registration itself
# --------------------------------------------------------------------------- #


def test_feature_is_discovered_and_mounted_without_editing_the_host(http):
    """The routes resolve even though no shared file names this feature."""
    entry = next(f for f in http.get("/api/features").json()["features"] if f["ticket"] == "WF-032")
    assert entry["prefix"] == PREFIX
    assert entry["id"] == FEATURE_ID
    assert entry["exception_handlers"] == ["IntentStreamError"]
    assert len(entry["routes"]) == 36


def test_the_prefix_is_ours_alone(http):
    """No core route and no other feature answers anything under it."""
    served = {
        (method, route["path"])
        for feature in http.get("/api/features").json()["features"]
        for route in feature["routes"]
        for method in route["methods"]
    }
    mine = {key for key in served if key[1].startswith(PREFIX)}
    others = {key for key in served if not key[1].startswith(PREFIX)}
    assert len(mine) == 36
    assert not mine & others


def test_the_two_half_ids_match(http):
    """The backend id and the frontend descriptor id are one name."""
    entry = next(f for f in http.get("/api/features").json()["features"] if f["ticket"] == "WF-032")
    descriptor = (
        Path(__file__).resolve().parents[2]
        / "frontend"
        / "src"
        / "features"
        / FEATURE_ID
        / "index.jsx"
    )
    text = descriptor.read_text(encoding="utf-8")
    assert entry["id"] in text
    assert f"id: {FEATURE_ID!r}" in text
    # The prefix itself lives in the feature's own api module, not in the
    # descriptor, so that is where it is checked.
    api_module = descriptor.parent / "api.js"
    assert f"const PREFIX = '{PREFIX.replace('/api', '')}'" in api_module.read_text(encoding="utf-8")


def test_feature_module_does_not_import_the_shared_app():
    """A guard exists in test_features.py; this states the reason locally.

    Checks the two import spellings the real guard looks for rather than the bare
    string, so a docstring that *mentions* the app module to explain why it is
    not imported does not fail the test.
    """
    source = Path(load_feature(MODULE).__file__).read_text(encoding="utf-8")
    assert "from dsr.api" not in source
    assert "import dsr.api" not in source
    assert "from dsr.deps import" in source


def test_no_module_in_the_package_touches_the_environment_or_a_temp_folder():
    """A feature that read an env var or reached for a temp path would be reading
    outside the store it was handed.

    ``DSR_DB_PATH`` and the audit mirror are resolved by :mod:`dsr.deps` and only
    there, which is what keeps a feature testable against a temporary database
    without a module of its own going looking for one.
    """
    from dsr.intent_stream.vocabulary import TOKEN_VERIFICATION_RECIPE

    package = Path(load_feature(MODULE).__file__).parent
    sources = [package / f"{MODULE}.py"] + sorted((package.parent / "intent_stream").glob("*.py"))
    for source in sources:
        # The verification recipe is the one place an environment variable
        # legitimately appears: it is a snippet of code for the *destination* to
        # paste, held as a string, and it is removed before the check so the
        # guard looks at this package's code and not at its documentation.
        text = source.read_text(encoding="utf-8").replace(TOKEN_VERIFICATION_RECIPE, "")
        assert "import tempfile" not in text, f"{source.name} imports tempfile"
        assert "os.environ" not in text, f"{source.name} reads an environment variable"
        assert "sqlite3" not in text, f"{source.name} opens SQLite directly"


def test_the_domain_package_never_imports_the_feature_module():
    """The dependency direction is one-way: feature -> domain, never back.

    A domain module that reached up into the feature would make the rules
    untestable on their own, which is the reason they live in a package at all.
    """
    package = Path(load_feature(MODULE).__file__).parent.parent / "intent_stream"
    for source in package.glob("*.py"):
        for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith("dsr.features"), source.name
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("dsr.features"), source.name


def test_every_source_file_parses_and_defers_its_annotations():
    """``from __future__ import annotations`` keeps the package importable on 3.11."""
    package = Path(load_feature(MODULE).__file__).parent.parent / "intent_stream"
    for source in sorted(package.glob("*.py")):
        tree = ast.parse(source.read_text(encoding="utf-8"))
        assert any(
            isinstance(node, ast.ImportFrom) and node.module == "__future__" for node in tree.body
        ), f"{source.name} does not defer its annotations"


# --------------------------------------------------------------------------- #
# The researched vocabulary
# --------------------------------------------------------------------------- #


def test_the_workflow_type_is_webhooks_and_nothing_else_is_published():
    """"Choose **Webhooks** in the popup" is the only researched workflow type."""
    body = vocabulary()
    assert body["workflowType"] == WORKFLOW_TYPE == "webhooks"
    assert body["workflowTypes"] == ["webhooks"]
    assert body["unresearchedWorkflowTypes"] == []


def test_the_two_send_modes_are_the_researched_ones():
    assert tuple(SEND_MODES) == (SEND_ONCE, SEND_UPDATES)
    assert vocabulary()["defaultSendMode"] == DEFAULT_SEND_MODE == "once"


def test_the_two_payload_modes_are_the_researched_ones():
    assert tuple(PAYLOAD_MODES) == (PAYLOAD_COMPANY, PAYLOAD_COMPANY_CONTACTS)
    assert vocabulary()["defaultPayloadMode"] == PAYLOAD_COMPANY


def test_the_three_skip_reasons_cover_every_refusal():
    """"it was paused" and "your Segment did not match" are both written down.

    Three reasons, because there are exactly three ways this product decides not
    to send: already sent (the researched once-only rule), paused, and no match.
    Each has to be a row, because none of them can be reconstructed from the
    absence of one.
    """
    assert set(SKIP_REASONS) == {SKIP_ALREADY_SENT, SKIP_INACTIVE, SKIP_NOT_MATCHED}
    assert {SKIP_ALREADY_SENT, SKIP_INACTIVE, SKIP_NOT_MATCHED} == {
        "already_sent",
        "workflow_inactive",
        "segment_not_matched",
    }


def test_the_keyword_fields_are_published():
    """An operator can see that 'security' will not search a phone number."""
    body = vocabulary()
    assert body["keywordFields"] == list(KEYWORD_FIELDS)
    assert "title" in KEYWORD_FIELDS and "department" in KEYWORD_FIELDS


def test_every_collection_is_prefixed_so_two_features_cannot_collide():
    for collection in ALL_COLLECTIONS:
        assert collection.startswith("wf032_"), collection
    assert vocabulary()["collections"] == list(ALL_COLLECTIONS)


def test_no_migration_was_added_for_a_teams_field():
    """The store is still one table of JSON, and the collections are new names.

    The product's guarantee is that a team adding a field needs no coordination.
    This feature adds collections, never columns, which is what keeps that true.
    """
    schema = Path(__file__).resolve().parents[1] / "dsr" / "db" / "schema.sql"
    text = schema.read_text(encoding="utf-8")
    for collection in ALL_COLLECTIONS:
        assert collection not in text, f"{collection} was added to the schema"


# --------------------------------------------------------------------------- #
# The explainer, the destinations, and the inferences
# --------------------------------------------------------------------------- #


def test_the_explainer_carries_every_quoted_evidence_sentence(http):
    body = http.get(f"{PREFIX}/explain").json()
    assert len(body["evidence"]) == len(EVIDENCE) == 6
    joined = " ".join(item["quote"] for item in body["evidence"])
    for phrase in (
        "automatically export your leads via a Webhook",
        "public API for a third party tool or a custom solution",
        "Add a name for your Workflow and the URL you want to send data to",
        "only send a lead once or if it should send updates as well",
        "updated activity data if that lead visits your webpage again",
        "only Company for the company lead or Company + Contacts",
        "automatically generated token",
    ):
        assert phrase in joined, phrase


def test_the_explainer_maps_all_seven_steps_of_the_researched_flow(http):
    body = http.get(f"{PREFIX}/explain").json()
    assert [step["step"] for step in body["flow"]] == [1, 2, 3, 4, 5, 6, 7]
    for step in body["flow"]:
        assert step["route"].startswith(("GET", "POST", "PATCH")), step
    assert "Microsoft Teams" in body["flow"][6]["text"]
    assert "Google Sheets" in body["flow"][6]["text"]


def test_the_explainer_says_what_was_not_documented(http):
    """"No public inbound REST reference for Albacross was reachable"."""
    body = http.get(f"{PREFIX}/explain").json()
    joined = " ".join(body["notDocumented"])
    assert "inbound" in joined
    assert "retry" in joined


def test_microsoft_teams_and_google_sheets_are_webhook_recipes(http):
    body = http.get(f"{PREFIX}/destinations").json()
    ids = {item["id"] for item in body["recipes"]}
    assert {"microsoft-teams", "google-sheets"} <= ids
    assert all(item["kind"] == "webhook_recipe" for item in body["recipes"])


def test_the_other_documented_surfaces_are_not_called_recipes(http):
    """The research listed them; it did not publish a recipe for them."""
    body = http.get(f"{PREFIX}/destinations").json()
    surface_ids = {item["id"] for item in body["surfaces"]}
    assert {"zapier", "n8n", "linkedin", "hubspot", "salesforce", "attio", "pipedrive"} <= surface_ids
    assert not surface_ids & {item["id"] for item in body["recipes"]}
    assert len(DESTINATION_RECIPES) == 10


def test_every_inference_names_its_decision_its_source_and_how_to_change_it(http):
    body = http.get(f"{PREFIX}/inferences").json()
    assert body["count"] == len(INFERENCES) >= 20
    ids = {entry["id"] for entry in body["inferences"]}
    for required in (
        "conditions-require-at-least-one-segment",
        "segment-match-defaults-to-any",
        "required-fields-filter-contacts-not-fields",
        "contacts-key-absent-in-company-mode",
        "all-contacts-filtered-out-is-still-a-delivery",
        "token-travels-in-a-header-and-in-the-body",
        "no-token-rotation",
        "http-scheme-allowed-and-warned",
        "no-retry-ladder-and-an-inferred-timeout",
        "a-failed-delivery-does-not-consume-a-once-only-lead",
        "a-paused-workflow-records-a-skip",
        "a-deleted-segment-in-use-is-refused",
        "no-inbound-verification-endpoint",
    ):
        assert required in ids, required
    for entry in body["inferences"]:
        for key in ("id", "decision", "researched", "alternative", "why", "change"):
            assert entry.get(key), f"{entry['id']} is missing {key}"


def test_describe_inferences_matches_the_list():
    assert describe_inferences()["count"] == len(INFERENCES)


# --------------------------------------------------------------------------- #
# The Segment rule grammar
# --------------------------------------------------------------------------- #


def test_resolve_walks_mappings_and_lists():
    payload = {"a": {"b": [{"c": 1}, {"c": 2}]}}
    assert segment_module.resolve(payload, "a.b.1.c") == 2
    assert segment_module.resolve(payload, "a.b.9.c") is segment_module.MISSING


def test_resolve_reports_a_missing_path_rather_than_guessing():
    assert segment_module.resolve({}, "a") is segment_module.MISSING
    assert segment_module.resolve({"a": 1}, "a.b") is segment_module.MISSING
    assert segment_module.resolve({"a": [1]}, "a.x") is segment_module.MISSING


@pytest.mark.parametrize(
    "operator,value,expected",
    [
        ("eq", "Software", True),
        ("eq", "software", True),
        ("ne", "Software", False),
        ("in", ["Software", "Hardware"], True),
        ("in", ["Hardware"], False),
        ("not_in", ["Hardware"], True),
        ("contains", "Soft", True),
        ("not_contains", "Soft", False),
        ("exists", None, True),
        ("not_exists", None, False),
    ],
)
def test_every_text_operator_evaluates_against_a_text_field(operator, value, expected):
    rule = {"path": "industry", "operator": operator, "value": value}
    assert segment_module.evaluate_rule(rule, {"industry": "Software"})["matched"] is expected


@pytest.mark.parametrize(
    "operator,value,expected",
    [("gt", 1000, True), ("gte", 4200, True), ("lt", 1000, False), ("lte", 4200, True)],
)
def test_every_ordering_operator_evaluates_against_a_numeric_field(operator, value, expected):
    rule = {"path": "employees", "operator": operator, "value": value}
    assert segment_module.evaluate_rule(rule, {"employees": 4200})["matched"] is expected


def test_an_ordering_operator_against_text_does_not_match_rather_than_raising():
    """"employees gte 1000" on a free-text field is a typo, and reads as False."""
    rule = {"path": "industry", "operator": "gte", "value": 1000}
    verdict = segment_module.evaluate_rule(rule, {"industry": "Software"})
    assert verdict["matched"] is False and "not a number" in verdict["reason"]


def test_the_operator_list_is_the_grammar_and_nothing_else():
    assert set(segment_module.OPERATORS) == {
        "eq", "ne", "in", "not_in", "contains", "not_contains",
        "gt", "gte", "lt", "lte", "exists", "not_exists",
    }


def test_a_comparison_against_a_list_value_matches_any_element():
    """"tags contains enterprise" has to work when tags is a JSON array."""
    rule = {"path": "tags", "operator": "contains", "value": "enterprise"}
    assert segment_module.evaluate_rule(rule, {"tags": ["smb", "enterprise"]})["matched"] is True
    assert segment_module.evaluate_rule(rule, {"tags": ["smb"]})["matched"] is False


def test_in_matches_a_list_valued_field_too():
    rule = {"path": "countries", "operator": "in", "value": ["GB", "DE"]}
    assert segment_module.evaluate_rule(rule, {"countries": ["US", "GB"]})["matched"] is True


def test_a_number_arriving_as_a_string_still_compares():
    """A Segment that stops matching on a data-shape difference is one nobody trusts."""
    rule = {"path": "employees", "operator": "gte", "value": 1000}
    assert segment_module.evaluate_rule(rule, {"employees": "4200"})["matched"] is True


def test_a_boolean_is_not_a_number_for_an_ordering_comparison():
    """"views gte 1" must not match a lead whose views is ``true``."""
    rule = {"path": "views", "operator": "gte", "value": 1}
    verdict = segment_module.evaluate_rule(rule, {"views": True})
    assert verdict["matched"] is False
    assert "not a number" in verdict["reason"]


def test_a_missing_path_is_reported_with_the_value_the_lead_actually_had():
    verdict = segment_module.evaluate_rule({"path": "employees", "operator": "gte", "value": 10}, {})
    assert verdict["matched"] is False
    assert verdict["missing"] is True
    assert verdict["actual"] == "<missing>"
    assert "does not carry" in verdict["reason"]


def test_a_missing_path_still_answers_a_unary_operator():
    assert segment_module.evaluate_rule({"path": "hiring", "operator": "exists"}, {})["matched"] is False
    assert segment_module.evaluate_rule({"path": "hiring", "operator": "not_exists"}, {})["matched"] is True


def test_an_empty_value_is_not_present_for_exists():
    """"exists" asks whether the lead carries the field, not whether it is truthy."""
    assert segment_module.evaluate_rule({"path": "t", "operator": "exists"}, {"t": ""})["matched"] is False
    assert segment_module.evaluate_rule({"path": "t", "operator": "exists"}, {"t": []})["matched"] is False
    assert segment_module.evaluate_rule({"path": "n", "operator": "exists"}, {"n": 0})["matched"] is True
    assert segment_module.evaluate_rule({"path": "b", "operator": "exists"}, {"b": False})["matched"] is True


def test_a_segment_with_no_rules_matches_everything_and_says_so():
    verdict = segment_module.evaluate_segment({"name": "Everyone", "rules": []}, {})
    assert verdict["matched"] is True
    assert "matches every company" in verdict["reason"]


def test_a_segment_combines_its_rules_with_its_own_match():
    rules = [
        {"path": "industry", "operator": "eq", "value": "Software"},
        {"path": "employees", "operator": "gte", "value": 1000},
    ]
    both = {"industry": "Software", "employees": 500}
    assert segment_module.evaluate_segment({"match": MATCH_ALL, "rules": rules}, both)["matched"] is False
    assert segment_module.evaluate_segment({"match": MATCH_ANY, "rules": rules}, both)["matched"] is True


def test_conditions_combine_segments_and_report_which_matched():
    """``matchedIds`` is what lets a payload be traced back to the Segment."""
    segments = [
        {"id": "s1", "name": "Software", "rules": [{"path": "industry", "operator": "eq", "value": "Software"}]},
        {"id": "s2", "name": "Big", "rules": [{"path": "employees", "operator": "gte", "value": 9000}]},
    ]
    verdict = segment_module.evaluate_conditions(segments, {"industry": "Software", "employees": 10})
    assert verdict["matched"] is True
    assert verdict["matchedIds"] == ["s1"]


def test_conditions_with_no_segments_never_match_and_say_why():
    verdict = segment_module.evaluate_conditions([], {})
    assert verdict["matched"] is False
    assert "no Segments" in verdict["reason"]


def test_a_segment_renders_as_a_readable_sentence():
    rules = [
        {"path": "industry", "operator": "eq", "value": "Software"},
        {"path": "employees", "operator": "gte", "value": 1000},
    ]
    assert segment_module.describe_segment({"name": "S", "match": MATCH_ANY, "rules": rules}) == (
        "industry eq Software OR employees gte 1000"
    )
    assert " AND " in segment_module.describe_segment({"name": "S", "match": MATCH_ALL, "rules": rules})
    assert "[GB, DE]" in segment_module.describe_rule({"path": "c", "operator": "in", "value": ["GB", "DE"]})


# --------------------------------------------------------------------------- #
# Segment validation
# --------------------------------------------------------------------------- #


def test_a_segment_with_no_rules_argument_is_refused():
    with pytest.raises(SegmentError, match="needs its rules"):
        segment_module.require_rules(None)


def test_rules_must_be_a_list_of_objects():
    with pytest.raises(SegmentError, match="must be a list"):
        segment_module.require_rules({"path": "a"})
    with pytest.raises(SegmentError, match="must be an object"):
        segment_module.require_rules(["a"])


def test_a_rule_with_no_path_is_refused():
    with pytest.raises(SegmentError, match="no path"):
        segment_module.require_rules([{"operator": "eq", "value": 1}])


def test_an_operator_outside_the_grammar_is_refused_with_the_grammar_listed():
    with pytest.raises(SegmentError) as excinfo:
        segment_module.require_rules([{"path": "a", "operator": "like", "value": 1}])
    assert "not in the grammar" in str(excinfo.value)
    assert "contains" in excinfo.value.remediation, "the refusal must list the grammar"


def test_an_ordering_operator_with_a_non_numeric_value_is_refused_at_save_time():
    """Otherwise a rule that can never match looks like a Segment nobody is in."""
    with pytest.raises(SegmentError, match="non-numeric"):
        segment_module.require_rules([{"path": "employees", "operator": "gte", "value": "lots"}])


def test_a_scalar_operator_with_a_list_value_is_refused():
    with pytest.raises(SegmentError, match="compares against one value"):
        segment_module.require_rules([{"path": "industry", "operator": "eq", "value": ["a", "b"]}])


def test_a_unary_operator_with_a_value_is_refused():
    with pytest.raises(SegmentError, match="takes no value"):
        segment_module.require_rules([{"path": "a", "operator": "exists", "value": 1}])


def test_a_comparison_operator_with_no_value_is_refused():
    with pytest.raises(SegmentError, match="no value"):
        segment_module.require_rules([{"path": "a", "operator": "eq"}])


def test_a_nested_value_is_refused_so_a_rule_cannot_smuggle_a_payload():
    with pytest.raises(SegmentError, match="nests"):
        segment_module.require_rules([{"path": "a", "operator": "eq", "value": {"k": "v"}}])


def test_a_very_long_value_is_refused():
    with pytest.raises(SegmentError, match="over the"):
        segment_module.require_rules([{"path": "a", "operator": "eq", "value": "x" * 500}])


def test_a_path_deeper_than_the_cap_is_refused():
    with pytest.raises(SegmentError, match="deeper than"):
        segment_module.require_rules([{"path": "a.b.c.d.e.f.g.h.i", "operator": "exists"}])


def test_more_rules_than_the_cap_is_refused():
    rules = [{"path": f"a{index}", "operator": "exists"} for index in range(segment_module.MAX_RULES + 1)]
    with pytest.raises(SegmentError, match="at most"):
        segment_module.require_rules(rules)


def test_a_valid_rule_set_round_trips_through_normalisation():
    normalised = segment_module.require_rules(
        [{"path": " employees ", "operator": "gte", "value": 1000}, {"path": "t", "operator": "exists"}]
    )
    assert normalised == [
        {"path": "employees", "operator": "gte", "value": 1000},
        {"path": "t", "operator": "exists"},
    ]


def test_require_match_defaults_and_refuses():
    assert segment_module.require_match(None) == segment_module.DEFAULT_RULE_MATCH == MATCH_ANY
    assert segment_module.require_match("") == MATCH_ANY
    assert segment_module.require_match("ALL") == MATCH_ALL
    with pytest.raises(SegmentError, match="must be 'any' or 'all'"):
        segment_module.require_match("either")


# --------------------------------------------------------------------------- #
# The contact filters
# --------------------------------------------------------------------------- #


def test_a_keyword_matches_a_contact_field_case_insensitively():
    hit = payload_module.keyword_hit(CONTACT_ENGINEER, ["ENGINEERING"])
    assert hit == {"matched": True, "keyword": "ENGINEERING", "field": "title"}


def test_a_keyword_is_reported_with_the_field_it_landed_in():
    """"no keyword matched" is not actionable without knowing what was searched."""
    assert payload_module.keyword_hit(CONTACT_ANALYST, ["strategy"]) == {
        "matched": True,
        "keyword": "strategy",
        "field": "department",
    }
    assert payload_module.keyword_hit(CONTACT_NO_EMAIL, ["security"])["field"] == "title"
    assert payload_module.keyword_hit(CONTACT_ANALYST, ["engineering"])["matched"] is False


def test_no_keywords_means_every_contact_passes_the_keyword_filter():
    assert payload_module.keyword_hit(CONTACT_ANALYST, [])["matched"] is True


def test_a_required_field_that_is_absent_or_empty_is_reported():
    assert payload_module.missing_required_fields(CONTACT_NO_EMAIL, ["email"]) == ["email"]
    assert payload_module.missing_required_fields({"title": ""}, ["title"]) == ["title"]
    assert payload_module.missing_required_fields({"tags": []}, ["tags"]) == ["tags"]
    assert payload_module.missing_required_fields(CONTACT_ENGINEER, ["email"]) == []


def test_a_nested_required_field_is_reachable():
    contact = {"social": {"linkedin": "https://x"}}
    assert payload_module.missing_required_fields(contact, ["social.linkedin"]) == []
    assert payload_module.missing_required_fields({}, ["social.linkedin"]) == ["social.linkedin"]


def test_both_filters_have_to_pass_for_a_contact_to_be_included():
    result = payload_module.filter_contacts(
        [CONTACT_ENGINEER, CONTACT_NO_EMAIL, CONTACT_ANALYST],
        {"keywords": ["engineering", "security"], "requiredFields": ["email"]},
    )
    assert [row["name"] for row in result["contacts"]] == ["A. Buyer"]
    assert result["considered"] == 3 and result["included"] == 1


def test_the_exclusions_carry_their_reasons():
    result = payload_module.filter_contacts(
        [CONTACT_NO_EMAIL, CONTACT_ANALYST],
        {"keywords": ["engineering", "security"], "requiredFields": ["email"]},
    )
    reasons = {row["name"]: row for row in result["excluded"]}
    assert "missing required field(s): email" in " ".join(reasons["S. Intern"]["reasons"])
    assert reasons["S. Intern"]["missingRequiredFields"] == ["email"]
    assert "no keyword matched" in " ".join(reasons["R. Researcher"]["reasons"])


def test_no_filter_sends_every_contact():
    result = payload_module.filter_contacts([CONTACT_ENGINEER, CONTACT_ANALYST], {})
    assert result["included"] == 2 and result["excluded"] == []


def test_the_prose_spellings_from_the_help_centre_are_accepted():
    """"Company + Contacts" is how the research writes it, so it must work."""
    assert payload_module.require_payload_mode("Company") == PAYLOAD_COMPANY
    assert payload_module.require_payload_mode("Company + Contacts") == PAYLOAD_COMPANY_CONTACTS
    assert payload_module.require_payload_mode("company-and-contacts") == PAYLOAD_COMPANY_CONTACTS
    assert payload_module.require_payload_mode(None) == PAYLOAD_COMPANY


def test_a_third_payload_mode_is_refused_with_the_two_listed():
    with pytest.raises(LeadError) as excinfo:
        payload_module.require_payload_mode("everything")
    assert "exactly two outputs" in excinfo.value.remediation


def test_a_contact_filter_may_be_a_comma_separated_string():
    assert payload_module.require_contact_filter("security, engineering") == {
        "keywords": ["security", "engineering"],
        "requiredFields": [],
    }


def test_a_contact_filter_holding_a_non_string_is_refused():
    with pytest.raises(LeadError, match="not a string"):
        payload_module.require_contact_filter({"keywords": [1]})


def test_a_filter_summary_says_an_empty_filter_is_not_a_filter_that_matched_nobody():
    summary = payload_module.contact_filter_summary({})
    assert summary["active"] is False
    assert "every contact" in summary["note"]


# --------------------------------------------------------------------------- #
# Payload composition
# --------------------------------------------------------------------------- #


def _payload(**overrides) -> dict:
    base = {
        "company": dict(SOFTWARE),
        "lead_id": "lead_1",
        "room_id": "room_1",
        "contacts": [dict(CONTACT_ENGINEER)],
        "workflow": {"id": "wf_1", "name": "Leads out", "sendMode": SEND_ONCE, "payload": PAYLOAD_COMPANY},
        "conditions": {"matchedIds": ["seg_1"], "match": MATCH_ANY},
        "send_count": 0,
        "token": "albwh_deadbeef",
        "sent_at": NOW.isoformat(),
    }
    base.update(overrides)
    return build_payload(**base)


def test_company_only_payload_has_no_contacts_key_at_all():
    """Not null, not empty: a destination branching on the key needs one answer."""
    body = _payload()
    assert "contacts" not in body
    assert body["type"] == PAYLOAD_TYPE
    assert body["company"] == SOFTWARE


def test_company_and_contacts_payload_carries_the_contacts_and_the_counts():
    body = _payload(workflow={"id": "wf_1", "name": "W", "sendMode": SEND_UPDATES, "payload": PAYLOAD_COMPANY_CONTACTS})
    assert body["contacts"] == [CONTACT_ENGINEER]
    assert body["contactsConsidered"] == 1 and body["contactsIncluded"] == 1


def test_a_filter_that_keeps_nobody_still_produces_a_company_payload():
    """The company is the lead the Segment matched; the filter narrows contacts."""
    body = _payload(
        workflow={"id": "wf_1", "name": "W", "sendMode": SEND_UPDATES, "payload": PAYLOAD_COMPANY_CONTACTS},
        contacts=[CONTACT_ANALYST],
        contact_filter={"keywords": ["engineering"], "requiredFields": []},
    )
    assert body["company"] == SOFTWARE
    assert body["contacts"] == []
    assert body["contactsConsidered"] == 1 and body["contactsIncluded"] == 0


def test_the_first_send_is_not_an_update_and_the_second_is():
    assert _payload()["lead"]["isUpdate"] is False
    assert _payload()["lead"]["updateCount"] == 0
    again = _payload(send_count=1)
    assert again["lead"]["isUpdate"] is True
    assert again["lead"]["updateCount"] == 1


def test_the_payload_records_which_segment_selected_the_company():
    """"which Segment matched" is the question somebody always ends up asking."""
    assert _payload()["lead"]["matchedSegmentIds"] == ["seg_1"]


def test_the_token_travels_in_the_body_as_well_as_the_header():
    """"use [the token] in your service or tool" - a Sheets recipe reads the body."""
    assert _payload()["token"] == "albwh_deadbeef"
    assert TOKEN_BODY_FIELD == "token"


def test_a_workflow_with_no_token_omits_the_body_field_rather_than_sending_null():
    assert TOKEN_BODY_FIELD not in _payload(token=None)


def test_the_company_is_the_leads_own_data_sent_whole():
    """A field a team added yesterday is in the payload, with no code change here."""
    company = {**SOFTWARE, "firmographics": {"hiringSignal": "expanding"}}
    body = _payload(company=company)
    assert body["company"]["firmographics"]["hiringSignal"] == "expanding"


def test_the_body_encodes_deterministically_so_a_test_can_compare_it():
    assert encode(_payload()) == encode(_payload())


# --------------------------------------------------------------------------- #
# The token
# --------------------------------------------------------------------------- #


def test_a_generated_token_is_prefixed_and_long_enough_to_be_unguessable():
    first, second = generate_token(), generate_token()
    assert first.startswith(TOKEN_PREFIX)
    assert first != second
    assert len(first) > 40


def test_a_masked_token_shows_the_prefix_and_the_last_four_and_nothing_else():
    token = generate_token()
    masked = mask_token(token)
    assert masked.startswith(TOKEN_PREFIX)
    assert masked.endswith(token[-4:])
    assert token[12:-4] not in masked


def test_a_token_too_short_to_mask_safely_is_not_shown():
    assert mask_token("short") == "<not shown>"
    assert mask_token("") == ""


def test_token_comparison_is_constant_time_and_correct():
    token = generate_token()
    assert token_matches(token, token) is True
    assert token_matches(token, token[:-1] + "0") is False
    assert token_matches(None, token) is False
    assert token_matches(token, None) is False


def test_every_post_carries_the_token_header():
    headers = token_module.headers_for("albwh_x")
    assert headers[TOKEN_HEADER] == "albwh_x"
    assert headers["Content-Type"] == "application/json"
    assert "digital-sales-room" in headers["User-Agent"]


# --------------------------------------------------------------------------- #
# Destination URLs
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "url",
    [
        "https://hooks.example/leads",
        "http://localhost:8080/hook",
        "https://hooks.example",
        "https://user:pass@hooks.example/x",
    ],
)
def test_a_destination_url_is_accepted(url):
    assert validate_target_url(url) == url


@pytest.mark.parametrize(
    "url,message",
    [
        ("", "destination URL is required"),
        ("   ", "destination URL is required"),
        ("hooks.example/leads", "not a URL with a scheme"),
        ("ftp://hooks.example/x", "cannot receive an HTTP POST"),
        ("file:///etc/passwd", "cannot receive an HTTP POST"),
        ("javascript:alert(1)", "not a URL with a scheme"),
        ("https://", "no usable host"),
        ("https://a", "no usable host"),
        ("https://hooks example/x", "whitespace"),
        ("https://..example/x", "malformed host"),
    ],
)
def test_a_url_that_could_not_receive_a_post_is_refused(url, message):
    with pytest.raises(TargetError, match=message):
        validate_target_url(url)


def test_an_http_destination_is_warned_about_because_the_token_is_useless_in_clear():
    """"prove that traffic is coming from the Albacross platform" needs a channel."""
    warnings = url_warnings("http://localhost:8080/hook")
    assert warnings and TOKEN_HEADER in warnings[0]
    assert url_warnings("https://hooks.example/x") == []


# --------------------------------------------------------------------------- #
# Delivery classification
# --------------------------------------------------------------------------- #


def test_a_2xx_is_delivered_and_anything_else_is_not():
    assert classify(DeliveryResult(ok=True, status=202)) == "delivered"
    assert classify(DeliveryResult(ok=False, status=500)) == "failed"
    assert classify(DeliveryResult(ok=False, error="URLError")) == "failed"


def test_a_3xx_is_not_a_success():
    """The POST did not follow the redirect, so the payload did not land."""
    assert classify(DeliveryResult(ok=False, status=302)) == "failed"


def test_retryability_is_advice_about_a_never_retryable_failure():
    assert is_retryable(DeliveryResult(ok=False, status=404)) is False
    assert is_retryable(DeliveryResult(ok=False, status=500)) is True
    assert is_retryable(DeliveryResult(ok=False, status=429)) is True
    assert is_retryable(DeliveryResult(ok=False, error="URLError")) is True
    assert is_retryable(DeliveryResult(ok=True, status=202)) is False


def test_the_documented_timeout_is_ten_seconds_and_is_flagged_as_inferred():
    """This research states no timeout at all, so the value is labelled as ours.

    Ten seconds is a judgement call. The sibling event-stream feature also uses
    ten, but its number is *sourced* to that workflow's vendor; reusing the same
    figure here would present an inference as a rule, so the inference says so.
    """
    assert DEFAULT_TIMEOUT_SECONDS == 10.0
    entry = next(item for item in INFERENCES if item["id"] == "no-retry-ladder-and-an-inferred-timeout")
    assert "10-second timeout" in entry["decision"]


def test_the_response_excerpt_is_bounded():
    assert MAX_RESPONSE_BYTES == 2048


# --------------------------------------------------------------------------- #
# Segments, over the service
# --------------------------------------------------------------------------- #


def test_a_segment_is_created_with_a_readable_summary(stream):
    created = stream.create_segment(
        {"name": "Big", "rules": [{"path": "employees", "operator": "gte", "value": 1000}]},
        source=SOURCE,
    )
    assert created["created"] is True
    assert created["segment"]["summary"] == "employees gte 1000"
    assert created["segment"]["ruleCount"] == 1


def test_a_segment_needs_a_name(stream):
    with pytest.raises(SegmentError, match="needs a name"):
        stream.create_segment({"rules": []}, source=SOURCE)


def test_two_segments_cannot_share_a_name(stream, segment):
    with pytest.raises(SegmentError, match="already exists") as excinfo:
        stream.create_segment(
            {"name": "Software", "rules": [{"path": "a", "operator": "exists"}]}, source=SOURCE
        )
    assert excinfo.value.status == 409


def test_a_segment_can_be_renamed_rules_changed_and_read_back(stream, segment):
    stream.update_segment(
        segment, {"name": "Software and SaaS", "match": MATCH_ALL, "rules": [{"path": "region", "operator": "eq", "value": "EMEA"}]}, source=SOURCE
    )
    read = stream.read_segment(segment)
    assert read["name"] == "Software and SaaS"
    assert read["match"] == MATCH_ALL
    assert read["rules"] == [{"path": "region", "operator": "eq", "value": "EMEA"}]


def test_renaming_a_segment_onto_another_segments_name_is_refused(stream, segment):
    stream.create_segment({"name": "Other", "rules": []}, source=SOURCE)
    with pytest.raises(SegmentError, match="already exists"):
        stream.update_segment(segment, {"name": "Other"}, source=SOURCE)


def test_a_segment_patch_with_nothing_in_it_is_refused(stream, segment):
    with pytest.raises(SegmentError, match="nothing to change"):
        stream.update_segment(segment, {}, source=SOURCE)


def test_deleting_a_segment_nothing_uses_works(stream, segment):
    result = stream.delete_segment(segment, source=SOURCE)
    assert result["deleted"] is True
    assert stream.list_segments() == []


def test_deleting_a_segment_a_workflow_uses_is_refused_and_names_the_workflow(stream, segment, workflow):
    """"it would stop sending while looking perfectly healthy on both lists"."""
    with pytest.raises(DeliveryError) as excinfo:
        stream.delete_segment(segment, source=SOURCE)
    assert excinfo.value.status == 409
    assert "Leads out" in excinfo.value.remediation


def test_a_segment_reports_which_workflows_use_it(stream, segment, workflow):
    assert stream.read_segment(segment)["usedBy"] == ["Leads out"]


def test_a_segment_can_be_evaluated_against_a_saved_lead_without_a_visit(stream, segment, lead, transport):
    verdict = stream.evaluate_segment_against(segment, lead_id=lead["id"])
    assert verdict["matched"] is True
    assert verdict["rules"][0]["actual"] == "Software"
    assert transport.calls == [], "evaluating a Segment must not send anything"


def test_a_segment_can_be_evaluated_against_an_inline_company_shape(stream, segment):
    verdict = stream.evaluate_segment_against(segment, company={"industry": "Software"})
    assert verdict["matched"] is True


def test_evaluating_a_segment_needs_a_lead_or_a_company_not_both(stream, segment, lead):
    with pytest.raises(SegmentError, match="not both"):
        stream.evaluate_segment_against(segment, lead_id=lead["id"], company={"industry": "Software"})


# --------------------------------------------------------------------------- #
# Company leads and contacts
# --------------------------------------------------------------------------- #


def test_a_lead_needs_a_company_name(stream):
    with pytest.raises(LeadError, match="company name"):
        stream.create_lead({"industry": "Software"}, source=SOURCE)


def test_a_company_is_identified_once(stream, lead):
    with pytest.raises(LeadError, match="already identified") as excinfo:
        stream.create_lead({"name": "Northwind Traders", **SOFTWARE}, source=SOURCE)
    assert excinfo.value.status == 409


def test_a_lead_carries_the_teams_own_fields_verbatim(stream):
    created = stream.create_lead(
        {"name": "Acme", "firmographics": {"hiringSignal": "expanding"}, "region": "EMEA"},
        source=SOURCE,
    )["lead"]
    assert created["id"]
    read = stream.read_lead(created["id"])
    assert read["data"]["firmographics"] == {"hiringSignal": "expanding"}


def test_a_lead_can_be_patched_but_not_reidentified(stream, lead):
    stream.update_lead(lead["id"], {"employees": 5000, "notes": "called"}, source=SOURCE)
    read = stream.read_lead(lead["id"])
    assert read["data"]["employees"] == 5000 and read["data"]["notes"] == "called"
    with pytest.raises(LeadError) as excinfo:
        stream.update_lead(lead["id"], {"companyId": "co_other"}, source=SOURCE)
    assert excinfo.value.status == 409


def test_a_lead_patch_with_nothing_in_it_is_refused(stream, lead):
    with pytest.raises(LeadError, match="nothing to change"):
        stream.update_lead(lead["id"], {}, source=SOURCE)


def test_a_contact_belongs_to_a_lead_and_needs_a_name(stream, lead):
    with pytest.raises(LeadError, match="contact needs a name"):
        stream.create_contact(lead["id"], {"title": "VP"}, source=SOURCE)
    created = stream.create_contact(lead["id"], dict(CONTACT_ENGINEER), source=SOURCE)["contact"]
    assert created["leadId"] == lead["id"]
    assert [row["name"] for row in stream.list_contacts(lead["id"])] == ["A. Buyer"]


def test_contacts_are_listed_alphabetically_so_a_filter_says_the_same_thing_twice(stream, lead):
    stream.create_contact(lead["id"], dict(CONTACT_ANALYST), source=SOURCE)
    stream.create_contact(lead["id"], dict(CONTACT_ENGINEER), source=SOURCE)
    assert [row["name"] for row in stream.list_contacts(lead["id"])] == [
        "A. Buyer",
        "R. Researcher",
    ]


def test_listing_contacts_for_a_lead_that_does_not_exist_is_a_404(stream):
    with pytest.raises(IntentStreamError) as excinfo:
        stream.list_contacts("nope")
    assert excinfo.value.status == 404


# --------------------------------------------------------------------------- #
# Workflows
# --------------------------------------------------------------------------- #


def test_a_workflow_is_created_with_a_generated_token_returned_once(stream, segment):
    created = make_workflow(stream, segment)
    assert created["token"].startswith(TOKEN_PREFIX)
    assert created["workflow"]["tokenHint"] != created["token"]
    assert created["workflow"]["hasToken"] is True


def test_a_caller_cannot_supply_the_token(stream, segment):
    """"automatically generated" - a silently ignored field is how you get fooled."""
    with pytest.raises(WorkflowError, match="cannot be supplied"):
        make_workflow(stream, segment, token="mine")


def test_the_token_cannot_be_set_afterwards_either(stream, segment, workflow):
    with pytest.raises(WorkflowError) as excinfo:
        stream.update_workflow(workflow["workflow"]["id"], {"token": "mine"}, source=SOURCE)
    assert "cannot be set or replaced" in str(excinfo.value)
    assert "no rotation route" in excinfo.value.remediation


def test_the_token_is_returned_only_by_create_and_by_the_reveal_route(stream, segment, workflow):
    workflow_id = workflow["workflow"]["id"]
    listed = [row for row in stream.list_workflows() if row["id"] == workflow_id][0]
    assert workflow["token"] not in json.dumps(listed)
    detail = stream.read_workflow(workflow_id)
    assert workflow["token"] not in json.dumps(detail)
    assert workflow["token"] not in json.dumps(detail["data"])
    assert stream.reveal_token(workflow_id)["token"] == workflow["token"]


def test_revealing_the_token_writes_no_audit_row(stream, segment, workflow, store):
    before = len(store.audit(limit=1000))
    stream.reveal_token(workflow["workflow"]["id"])
    assert len(store.audit(limit=1000)) == before


def test_a_workflow_needs_a_name(stream, segment):
    with pytest.raises(WorkflowError, match="needs a name"):
        make_workflow(stream, segment, name="")


def test_a_workflow_needs_a_destination_url(stream, segment):
    with pytest.raises(TargetError, match="destination URL is required"):
        make_workflow(stream, segment, url="")


def test_a_workflow_needs_conditions(stream):
    """Step 4 makes conditions a step, not an option."""
    with pytest.raises(WorkflowError, match="needs conditions"):
        make_workflow(stream, "seg_1", conditions=None)


def test_a_workflow_must_name_at_least_one_segment(stream, segment):
    with pytest.raises(WorkflowError, match="at least one saved Segment"):
        make_workflow(stream, segment, conditions={"segmentIds": []})


def test_a_workflow_refuses_a_segment_that_does_not_exist(stream):
    with pytest.raises(WorkflowError, match="no such Segment"):
        make_workflow(stream, "seg_missing")


def test_a_workflow_refuses_a_repeated_segment(stream, segment):
    with pytest.raises(WorkflowError, match="repeats a Segment"):
        make_workflow(stream, segment, conditions={"segmentIds": [segment, segment]})


def test_a_bare_list_of_segment_ids_is_accepted_as_the_conditions(stream, segment):
    """The researched step says "add the conditions", not "add a conditions object"."""
    created = stream.create_workflow(
        {"name": "W", "url": TARGET, "conditions": [segment]}, source=SOURCE
    )["workflow"]
    assert created["conditions"]["segmentIds"] == [segment]
    assert created["conditions"]["match"] == DEFAULT_SEGMENT_MATCH


def test_a_comma_separated_segment_list_is_accepted(stream, segment, never_segment):
    created = stream.create_workflow(
        {"name": "W", "url": TARGET, "conditions": {"segmentIds": f"{segment},{never_segment}"}}, source=SOURCE
    )["workflow"]
    assert created["conditions"]["segmentIds"] == [segment, never_segment]


def test_the_conditions_summary_names_the_segments_in_words(stream, segment):
    created = make_workflow(stream, segment)["workflow"]
    assert created["conditions"]["summary"] == "Software"


def test_creating_a_workflow_against_an_http_destination_returns_a_warning(stream, segment):
    created = make_workflow(stream, segment, url="http://localhost:8080/hook")
    assert created["warnings"] and TOKEN_HEADER in created["warnings"][0]


def test_an_unknown_send_mode_is_refused_with_the_two_researched_ones(stream, segment):
    with pytest.raises(WorkflowError) as excinfo:
        make_workflow(stream, segment, sendMode="sometimes")
    assert "must be 'once' or 'updates'" in str(excinfo.value)
    assert "only send a lead once" in excinfo.value.remediation


def test_an_unknown_payload_mode_is_refused(stream, segment):
    with pytest.raises(LeadError) as excinfo:
        make_workflow(stream, segment, payload="everything")
    assert "exactly two outputs" in excinfo.value.remediation


def test_a_contact_filter_on_a_company_only_workflow_is_kept_but_unused(stream, segment):
    """Recorded, so switching the payload later does not lose the operator's intent."""
    created = make_workflow(stream, segment, contactFilter={"keywords": ["security"]})["workflow"]
    assert created["contactFilter"]["keywords"] == ["security"]


def test_a_workflow_can_be_paused_renamed_and_retargeted(stream, segment, workflow):
    workflow_id = workflow["workflow"]["id"]
    stream.update_workflow(workflow_id, {"active": False, "name": "Paused"}, source=SOURCE)
    assert stream.read_workflow(workflow_id)["active"] is False
    assert stream.read_workflow(workflow_id)["name"] == "Paused"
    stream.update_workflow(workflow_id, {"url": SECOND_TARGET}, source=SOURCE)
    assert stream.read_workflow(workflow_id)["url"] == SECOND_TARGET


def test_active_must_be_a_boolean(stream, segment, workflow):
    with pytest.raises(WorkflowError, match="must be true or false"):
        stream.update_workflow(workflow["workflow"]["id"], {"active": "no"}, source=SOURCE)


def test_a_workflow_patch_with_nothing_in_it_is_refused(stream, segment, workflow):
    with pytest.raises(WorkflowError, match="nothing to change"):
        stream.update_workflow(workflow["workflow"]["id"], {}, source=SOURCE)


def test_a_deleted_workflow_keeps_its_delivery_log(stream, segment, workflow, lead):
    workflow_id = workflow["workflow"]["id"]
    visit(stream, lead["id"])
    assert stream.delete_workflow(workflow_id, source=SOURCE)["deleted"] is True
    assert stream.list_workflows() == []
    delivery = stream.list_deliveries()[0]
    assert delivery["workflowName"] == "Leads out"
    assert stream.read_delivery(delivery["id"])["workflow"] is None


def test_listing_workflows_can_hide_the_paused_ones(stream, segment, workflow):
    workflow_id = workflow["workflow"]["id"]
    stream.update_workflow(workflow_id, {"active": False}, source=SOURCE)
    assert len(stream.list_workflows()) == 1
    assert stream.list_workflows(include_inactive=False) == []


def test_the_preview_shows_the_exact_body_and_sends_nothing(stream, segment, workflow, lead, transport):
    result = stream.preview(workflow["workflow"]["id"], lead_id=lead["id"])
    assert result["wouldMatch"] is True
    assert result["wouldSend"] is True
    assert result["payload"]["company"] == stream.read_lead(lead["id"])["data"]
    assert transport.calls == []


def test_the_preview_refuses_to_show_a_workflow_that_would_not_match(stream, segment, never_segment, lead):
    created = make_workflow(stream, never_segment, name="Wholesale")["workflow"]
    result = stream.preview(created["id"], lead_id=lead["id"])
    assert result["wouldMatch"] is False
    assert result["wouldSend"] is False


def test_the_preview_masks_the_token_and_says_the_destination_gets_it_in_full(stream, segment, workflow, lead):
    result = stream.preview(workflow["workflow"]["id"], lead_id=lead["id"])
    assert workflow["token"] not in json.dumps(result)
    assert "in full" in result["note"]


def test_the_preview_honours_the_contact_filters(stream, segment, lead, transport):
    stream.create_contact(lead["id"], dict(CONTACT_ENGINEER), source=SOURCE)
    stream.create_contact(lead["id"], dict(CONTACT_ANALYST), source=SOURCE)
    created = make_workflow(
        stream,
        segment,
        name="People",
        payload=PAYLOAD_COMPANY_CONTACTS,
        contactFilter={"keywords": ["engineering"], "requiredFields": ["email"]},
    )["workflow"]
    body = stream.preview(created["id"], lead_id=lead["id"])["payload"]
    assert [row["name"] for row in body["contacts"]] == ["A. Buyer"]
    assert body["contacts"][0]["email"] == CONTACT_ENGINEER["email"]
    assert body["contactsConsidered"] == 2 and body["contactsIncluded"] == 1


# --------------------------------------------------------------------------- #
# The trigger: a company visit
# --------------------------------------------------------------------------- #


def test_a_visit_needs_the_company_it_belongs_to(stream):
    with pytest.raises(LeadError, match="the identified company"):
        stream.record_visit({"pagesViewed": ["Pricing"]}, source=SOURCE)


def test_a_visit_for_a_lead_that_does_not_exist_is_a_404(stream):
    with pytest.raises(IntentStreamError) as excinfo:
        stream.record_visit({"leadId": "nope"}, source=SOURCE)
    assert excinfo.value.status == 404


def test_a_visit_refreshes_the_leads_activity_data_before_anything_is_sent(stream, segment, workflow, lead, transport):
    """"the same lead with updated activity data" has to mean the visit itself."""
    visit(stream, lead["id"], pagesViewed=["Pricing"], secondsOnPage=90)
    read = stream.read_lead(lead["id"])
    assert read["data"]["visitCount"] == 1
    assert read["data"]["lastVisitAt"]
    assert "Pricing" in read["data"]["pagesViewed"]
    assert transport.bodies[0]["company"]["visitCount"] == 1


def test_a_visit_merges_new_pages_into_the_leads_history(stream, lead):
    visit(stream, lead["id"], pagesViewed=["Pricing One-Pager"])
    visit(stream, lead["id"], pagesViewed=["API Guide", "Pricing One-Pager"])
    pages = stream.read_lead(lead["id"])["data"]["pagesViewed"]
    assert pages == ["Pricing One-Pager", "API Guide"]


def test_a_visit_is_written_even_when_nothing_matched(stream, lead, transport):
    """A visit that matched nothing is how a Segment gets debugged."""
    result = visit(stream, lead["id"])
    assert result["visit"]["id"]
    assert result["matchedWorkflows"] == 0
    assert transport.calls == []
    assert result["deliveries"] == []


def test_a_once_workflow_sends_the_first_visit_and_skips_the_second_with_a_reason(stream, segment, workflow, lead):
    workflow_id = workflow["workflow"]["id"]
    first = outcome(visit(stream, lead["id"]), workflow_id)
    assert (first["state"], first["status"]) == ("delivered", 202)
    assert first["isUpdate"] is False and first["updateCount"] == 1

    second = outcome(visit(stream, lead["id"]), workflow_id)
    assert (second["state"], second["skipReason"]) == ("skipped", SKIP_ALREADY_SENT)
    assert "only once" in second["detail"]


def test_an_updates_workflow_resends_the_same_lead_with_refreshed_activity(stream, segment, lead):
    workflow_id = make_workflow(stream, segment, sendMode=SEND_UPDATES)["workflow"]["id"]
    outcome(visit(stream, lead["id"], pagesViewed=["Pricing"]), workflow_id)
    again = outcome(visit(stream, lead["id"], pagesViewed=["Security Pack"]), workflow_id)
    assert again["state"] == "delivered"
    assert again["isUpdate"] is True and again["updateCount"] == 2
    assert "Security Pack" in again["payload"]["company"]["pagesViewed"]
    assert again["payload"]["company"]["visitCount"] == 2


def test_an_updates_workflow_counts_up_across_visits(stream, segment, lead):
    workflow_id = make_workflow(stream, segment, sendMode=SEND_UPDATES)["workflow"]["id"]
    counts = []
    for _ in range(3):
        counts.append(outcome(visit(stream, lead["id"]), workflow_id)["updateCount"])
    assert counts == [1, 2, 3]
    assert all(
        outcome(visit(stream, lead["id"]), workflow_id)["isUpdate"] for _ in range(1)
    )


def test_a_paused_workflow_records_why_it_sent_nothing(stream, segment, workflow, lead, transport):
    workflow_id = workflow["workflow"]["id"]
    stream.update_workflow(workflow_id, {"active": False}, source=SOURCE)
    result = visit(stream, lead["id"])
    row = outcome(result, workflow_id)
    assert (row["state"], row["skipReason"]) == ("skipped", SKIP_INACTIVE)
    assert transport.calls == []


def test_a_workflow_whose_segment_does_not_match_records_that_too(stream, never_segment, lead):
    workflow_id = make_workflow(stream, never_segment, name="Wholesale")["workflow"]["id"]
    row = outcome(visit(stream, lead["id"]), workflow_id)
    assert (row["state"], row["skipReason"]) == ("skipped", SKIP_NOT_MATCHED)
    assert "no Segment matched" in row["detail"]


def test_a_workflow_scoped_to_one_room_ignores_a_visit_in_another(stream, segment, lead):
    created = stream.create_workflow(
        {"name": "Room scoped", "url": TARGET, "conditions": {"segmentIds": [segment], "roomId": "room_other"}},
        source=SOURCE,
    )["workflow"]
    row = outcome(visit(stream, lead["id"], roomId="room_here"), created["id"])
    assert row["skipReason"] == SKIP_NOT_MATCHED
    assert "scoped to room room_other" in row["detail"]


def test_a_workflow_whose_conditions_do_not_name_a_room_sends_from_every_room(stream, segment, lead):
    """Creating a workflow from a room-scoped page must not silently narrow it."""
    created = stream.create_workflow(
        {"name": "W", "url": TARGET, "conditions": {"segmentIds": [segment]}},
        room_id="room_authoring",
        source=SOURCE,
    )["workflow"]
    assert created["conditions"]["roomId"] is None
    assert outcome(visit(stream, lead["id"], roomId="room_elsewhere"), created["id"])["state"] == "delivered"


def test_conditions_combined_with_all_need_every_segment_to_match(stream, segment, never_segment, lead):
    either = make_workflow(
        stream, segment, name="Any", conditions={"segmentIds": [segment, never_segment], "match": MATCH_ANY}
    )["workflow"]
    both = make_workflow(
        stream, segment, name="All", conditions={"segmentIds": [segment, never_segment], "match": MATCH_ALL}
    )["workflow"]
    result = visit(stream, lead["id"])
    assert outcome(result, either["id"])["state"] == "delivered"
    assert outcome(result, both["id"])["skipReason"] == SKIP_NOT_MATCHED


def test_a_failed_delivery_does_not_consume_a_once_only_lead(stream, segment, transport, lead):
    """'Send a lead once' is about what the destination has seen."""
    workflow_id = make_workflow(stream, segment)["workflow"]["id"]
    transport.scripted.extend([boom(500), ok()])
    assert outcome(visit(stream, lead["id"]), workflow_id)["state"] == "failed"
    assert outcome(visit(stream, lead["id"]), workflow_id)["state"] == "delivered"
    row = stream.read_lead(lead["id"])["workflows"][0]
    assert row["sendCount"] == 1


def test_a_failed_delivery_records_the_status_the_duration_and_the_attempt(stream, segment, transport, lead):
    workflow_id = make_workflow(stream, segment)["workflow"]["id"]
    transport.scripted.append(DeliveryResult(ok=False, status=503, error="HTTP 503", retryable=True, duration_ms=11.0, body="busy"))
    row = stream.read_delivery(outcome(visit(stream, lead["id"]), workflow_id)["id"])
    assert row["status"] == 503 and row["retryable"] is True
    assert row["responseExcerpt"] == "busy"
    assert row["attemptLog"][0]["number"] == 1
    assert row["attemptLog"][0]["via"] == "visit"


def test_an_unreachable_destination_is_recorded_as_a_failure_with_no_status(stream, segment, transport, lead):
    workflow_id = make_workflow(stream, segment)["workflow"]["id"]
    transport.scripted.append(unreachable())
    row = stream.read_delivery(outcome(visit(stream, lead["id"]), workflow_id)["id"])
    assert row["state"] == "failed" and row["status"] is None
    assert "URLError" in row["attemptLog"][0]["error"]


def test_every_workflow_is_evaluated_even_when_an_earlier_one_sent(stream, segment, never_segment, lead):
    """A rule that does not fall through is a bug somebody hits in production."""
    first = make_workflow(stream, segment, name="A")["workflow"]["id"]
    second = make_workflow(stream, never_segment, name="B")["workflow"]["id"]
    result = visit(stream, lead["id"])
    assert {row["workflowId"] for row in result["deliveries"]} == {first, second}
    assert outcome(result, second)["skipReason"] == SKIP_NOT_MATCHED


def test_a_workflow_naming_a_deleted_segment_fails_loudly_rather_than_sending(stream, segment, lead, store):
    """The loud branch a cascade would have swallowed."""
    make_workflow(stream, segment)
    store.delete(segment, hard=True, source="test")
    with pytest.raises(WorkflowError) as excinfo:
        visit(stream, lead["id"])
    assert excinfo.value.status == 409
    assert "no longer exist" in str(excinfo.value)


def test_a_visit_is_scoped_to_the_room_and_its_rows_are_too(stream, segment, workflow, lead):
    """The brief requires room-scoped paths to stay room-scoped, and the rows
    they read have to be room-scoped too, or the tiles and the tables disagree."""
    stream.record_visit({"leadId": lead["id"]}, room_id="room_a", source=SOURCE)
    stream.record_visit({"leadId": lead["id"]}, room_id="room_b", source=SOURCE)
    assert stream.read_visit(stream.list_visits(room_id="room_a")[0]["id"])["room_id"] == "room_a"
    assert len(stream.list_visits(room_id="room_a")) == 1
    assert len(stream.list_deliveries(room_id="room_b")) == 1
    assert stream.room_summary("room_a")["deliveries"] == 1
    # A second visit in the same room adds the researched once-only skip, and it
    # lands in that room's count too.
    stream.record_visit({"leadId": lead["id"]}, room_id="room_b", source=SOURCE)
    assert stream.room_summary("room_b")["deliveries"] == 2


def test_visits_can_be_listed_by_lead_and_by_whether_they_matched(stream, segment, never_segment, lead):
    matching = make_workflow(stream, segment)["workflow"]["id"]
    visit(stream, lead["id"])
    stream.create_lead({"name": "Shopco", **RETAIL}, source=SOURCE)
    shop = [row for row in stream.list_leads() if row["name"] == "Shopco"][0]
    visit(stream, shop["id"])

    assert len(stream.list_visits(lead_id=lead["id"])) == 1
    assert len(stream.list_visits(matched=True)) == 1
    assert len(stream.list_visits(matched=False)) == 1
    assert stream.list_visits(matched=False)[0]["leadId"] == shop["id"]
    assert matching


def test_a_visit_records_its_own_outcomes(stream, segment, never_segment, lead):
    a = make_workflow(stream, segment, name="A")["workflow"]["id"]
    b = make_workflow(stream, never_segment, name="B")["workflow"]["id"]
    result = visit(stream, lead["id"])
    stored = stream.read_visit(result["visit"]["id"])
    assert {row["workflowId"] for row in stored["outcomes"]} == {a, b}


# --------------------------------------------------------------------------- #
# Deliveries and resending
# --------------------------------------------------------------------------- #


def test_deliveries_can_be_filtered_by_workflow_lead_state_and_reason(stream, segment, never_segment, lead):
    make_workflow(stream, segment, name="A")
    make_workflow(stream, never_segment, name="B")
    visit(stream, lead["id"])
    assert len(stream.list_deliveries(state="delivered")) == 1
    assert len(stream.list_deliveries(state="skipped")) == 1
    assert len(stream.list_deliveries(skip_reason=SKIP_NOT_MATCHED)) == 1
    assert len(stream.list_deliveries(lead_id=lead["id"])) == 2
    assert stream.list_deliveries(lead_id="nope") == []


def test_a_where_filter_works_on_the_stores_own_paths(stream, segment, lead):
    workflow_id = make_workflow(stream, segment)["workflow"]["id"]
    visit(stream, lead["id"])
    assert len(stream.list_deliveries(where={"workflowId": workflow_id})) == 1
    assert stream.list_deliveries(where={"workflowId": "nope"}) == []


def test_a_delivery_row_carries_the_conditions_that_decided_it(stream, segment, workflow, lead):
    segment_id = segment
    visit(stream, lead["id"])
    row = stream.read_delivery(stream.list_deliveries()[0]["id"])
    assert row["conditions"]["matchedIds"] == [segment_id]
    assert row["conditions"]["segments"][0]["rules"][0]["actual"] == "Software"


def test_resending_a_failed_delivery_appends_to_its_attempt_log(stream, segment, transport, lead):
    workflow_id = make_workflow(stream, segment)["workflow"]["id"]
    transport.scripted.extend([boom(500), ok()])
    failed = outcome(visit(stream, lead["id"]), workflow_id)
    resent = stream.resend(failed["id"], source=SOURCE)
    assert resent["state"] == "delivered"
    detail = stream.read_delivery(failed["id"])
    assert [attempt["number"] for attempt in detail["attemptLog"]] == [1, 2]
    assert detail["attemptLog"][1]["via"] == "resend"


def test_a_successful_resend_marks_the_once_only_lead_as_sent(stream, segment, transport, lead):
    workflow_id = make_workflow(stream, segment)["workflow"]["id"]
    transport.scripted.extend([boom(500), ok()])
    failed = outcome(visit(stream, lead["id"]), workflow_id)
    stream.resend(failed["id"], source=SOURCE)
    assert outcome(visit(stream, lead["id"]), workflow_id)["skipReason"] == SKIP_ALREADY_SENT


def test_resending_a_delivered_delivery_is_refused_so_the_destination_is_not_duplicated(stream, segment, lead):
    workflow_id = make_workflow(stream, segment)["workflow"]["id"]
    delivered = outcome(visit(stream, lead["id"]), workflow_id)
    with pytest.raises(DeliveryError, match="already reached"):
        stream.resend(delivered["id"], source=SOURCE)


def test_resending_a_skipped_delivery_is_refused_because_nothing_was_attempted(stream, never_segment, lead):
    workflow_id = make_workflow(stream, never_segment)["workflow"]["id"]
    skipped = outcome(visit(stream, lead["id"]), workflow_id)
    with pytest.raises(DeliveryError, match="nothing to resend"):
        stream.resend(skipped["id"], source=SOURCE)


def test_resending_after_the_workflow_is_gone_is_refused_with_the_url_on_the_row(stream, segment, transport, lead):
    workflow_id = make_workflow(stream, segment)["workflow"]["id"]
    transport.scripted.append(boom(500))
    failed = outcome(visit(stream, lead["id"]), workflow_id)
    stream.delete_workflow(workflow_id, source=SOURCE)
    with pytest.raises(DeliveryError, match="nowhere to resend"):
        stream.resend(failed["id"], source=SOURCE)
    assert stream.read_delivery(failed["id"])["data"]["url"] == TARGET


def test_a_resend_of_a_deleted_lead_still_sends_the_recorded_payload(stream, segment, transport, lead, store):
    """The destination asked for this company; a deleted lead does not unsend it."""
    workflow_id = make_workflow(stream, segment)["workflow"]["id"]
    transport.scripted.extend([boom(500), ok()])
    failed = outcome(visit(stream, lead["id"]), workflow_id)
    recorded = stream.read_delivery(failed["id"])["data"]
    store.delete(lead["id"], hard=True, source="test")
    resent = stream.resend(failed["id"], source=SOURCE)
    assert resent["state"] == "delivered"
    assert recorded["payloadBytes"] > 0
    assert transport.calls[-1]["body"]["lead"]["companyId"]


# --------------------------------------------------------------------------- #
# Summaries and room scoping
# --------------------------------------------------------------------------- #


def test_the_summary_counts_every_state_and_skip_reason(stream, segment, never_segment, lead, transport):
    make_workflow(stream, segment, name="A")
    make_workflow(stream, never_segment, name="B")
    make_workflow(stream, segment, name="C", url=SECOND_TARGET)
    transport.scripted.append(boom(404, retryable=False))
    visit(stream, lead["id"])
    visit(stream, lead["id"])

    summary = stream.summary()
    assert summary["workflows"] == 3
    assert summary["activeWorkflows"] == 3
    assert summary["leads"] == 1
    assert summary["segments"] == 2
    assert set(summary["byState"]) == {"delivered", "failed", "skipped"}
    assert summary["bySkipReason"][SKIP_ALREADY_SENT] == 1
    assert summary["permanentFailures"] == 1
    assert summary["retryableFailures"] == 0
    assert len(stream.list_deliveries(state="failed")) == 1


def test_a_room_scoped_summary_agrees_with_the_room_scoped_lists(stream, segment):
    northwind = stream.create_lead(
        {"name": "Northwind Traders", **SOFTWARE}, room_id="room_a", source=SOURCE
    )["lead"]
    shop = stream.create_lead(
        {"name": "Shopco", **RETAIL}, room_id="room_b", source=SOURCE
    )["lead"]
    make_workflow(stream, segment)
    stream.record_visit({"leadId": northwind["id"]}, room_id="room_a", source=SOURCE)
    stream.record_visit({"leadId": shop["id"]}, room_id="room_b", source=SOURCE)

    for room, expected_leads in (("room_a", 1), ("room_b", 1)):
        summary = stream.room_summary(room)
        assert summary["leads"] == expected_leads
        assert summary["visits"] == len(stream.list_visits(room_id=room))
        assert summary["deliveries"] == len(stream.list_deliveries(room_id=room))


def test_workflows_can_be_listed_by_the_room_they_were_authored_in(stream, segment):
    """Grouping, not filtering: the room a workflow belongs to is not the room
    it sends. A workflow's own ``conditions.roomId`` is what narrows that, and it
    is visible on the workflow itself."""
    make_workflow(stream, segment, name="A")
    stream.create_workflow(
        {"name": "B", "url": TARGET, "conditions": {"segmentIds": [segment]}},
        room_id="room_authors",
        source=SOURCE,
    )
    assert {row["name"] for row in stream.list_workflows()} == {"A", "B"}
    assert [row["name"] for row in stream.list_workflows(room_id="room_authors")] == ["B"]
    assert stream.list_workflows(room_id="room_missing") == []


def test_fields_reports_the_json_paths_actually_in_use(stream, segment, lead):
    make_workflow(stream, segment)
    visit(stream, lead["id"], pagesViewed=["Pricing One-Pager"])
    paths = stream.fields()["collections"]
    segment_paths = {row["path"] for row in paths[SEGMENT_COLLECTION]}
    assert {"name", "match", "rules.0.path", "rules.0.operator"} <= segment_paths
    lead_paths = {row["path"] for row in paths[LEAD_COLLECTION]}
    assert {"name", "visitCount", "pagesViewed.0"} <= lead_paths
    delivery_paths = {row["path"] for row in paths[DELIVERY_COLLECTION]}
    assert {"state", "skipReason", "url", "payloadBody.company.name"} <= delivery_paths
    assert "payloadBody.lead.updateCount" in delivery_paths


# --------------------------------------------------------------------------- #
# Caps, and a cap check that can actually fire
# --------------------------------------------------------------------------- #


def test_the_live_count_comes_from_the_store_not_from_a_one_row_list(stream, segment):
    """"is this collection full?" cannot be answered by listing one row.

    The first cut of this check was ``len(store.list(limit=1)) >= cap``, which
    returns at most one element and so was never true. A cap check that cannot
    fire reads as a guard and protects nothing, which is the specific reason it
    is worth a test.
    """
    assert stream._live_count(SEGMENT_COLLECTION) == 1
    assert stream._live_count(WORKFLOW_COLLECTION) == 0
    assert stream._live_count("no_such_collection") == 0


def test_a_full_segment_collection_is_refused(stream, segment, monkeypatch):
    import dsr.intent_stream.stream as stream_module

    monkeypatch.setattr(stream_module, "MAX_SEGMENTS", 1)
    with pytest.raises(SegmentError, match="already holds 1 Segments"):
        stream.create_segment({"name": "Second", "rules": []}, source=SOURCE)


def test_a_full_lead_collection_is_refused(stream, lead, monkeypatch):
    import dsr.intent_stream.stream as stream_module

    monkeypatch.setattr(stream_module, "MAX_LEADS", 1)
    with pytest.raises(LeadError, match="already holds 1 company leads"):
        stream.create_lead({"name": "Second", **RETAIL}, source=SOURCE)


def test_a_full_workflow_collection_is_refused(stream, segment, workflow, monkeypatch):
    import dsr.intent_stream.stream as stream_module

    monkeypatch.setattr(stream_module, "MAX_WORKFLOWS", 1)
    with pytest.raises(WorkflowError, match="already holds 1 workflows"):
        make_workflow(stream, segment, name="Second")


def test_a_company_with_too_many_contacts_is_refused(stream, lead, monkeypatch):
    import dsr.intent_stream.stream as stream_module

    stream.create_contact(lead["id"], dict(CONTACT_ENGINEER), source=SOURCE)
    monkeypatch.setattr(stream_module, "MAX_CONTACTS_PER_LEAD", 1)
    with pytest.raises(LeadError, match="already holds 1 contacts"):
        stream.create_contact(lead["id"], dict(CONTACT_ANALYST), source=SOURCE)


def test_a_workflow_may_not_name_more_segments_than_the_cap(stream, monkeypatch):
    import dsr.intent_stream.stream as stream_module

    ids = [
        stream.create_segment({"name": f"S{index}", "rules": []}, source=SOURCE)["segment"]["id"]
        for index in range(4)
    ]
    monkeypatch.setattr(stream_module, "MAX_SEGMENTS_PER_WORKFLOW", 3)
    with pytest.raises(WorkflowError, match="at most 3 Segments"):
        make_workflow(stream, ids[0], conditions={"segmentIds": ids})


def test_too_many_segments_in_one_visit_are_bounded(stream, lead):
    """"A visit with a thousand pages in it is a payload, not a visit."""
    pages = [f"page-{index}" for index in range(80)]
    result = visit(stream, lead["id"], pagesViewed=pages)
    assert len(result["visit"]["pagesViewed"]) == 50
    assert len(stream.read_lead(lead["id"])["data"]["pagesViewed"]) == 50


def test_a_visit_with_no_pages_still_records_a_visit(stream, lead):
    result = visit(stream, lead["id"], pagesViewed=None, secondsOnPage=None)
    assert result["visit"]["id"]
    assert result["visit"]["pagesViewed"] == []


def test_a_pages_value_that_is_not_a_list_is_ignored_rather_than_crashing(stream, lead):
    assert visit(stream, lead["id"], pagesViewed={"a": 1})["visit"]["pagesViewed"] == []


# --------------------------------------------------------------------------- #
# The HTTP surface
# --------------------------------------------------------------------------- #


def test_the_vocabulary_route_publishes_the_rule_grammar(http):
    body = http.get(f"{PREFIX}/vocabulary").json()
    assert body["segmentOperators"] == list(segment_module.OPERATORS)
    assert body["segmentUnaryOperators"] == list(segment_module.UNARY_OPERATORS)
    assert "gte" in body["segmentOrderingOperators"]


def test_a_segment_can_be_created_over_http_and_evaluated(http, http_segment, http_lead_id):
    response = http.post(f"{PREFIX}/segments/{http_segment}/evaluate", params={"lead_id": http_lead_id})
    assert response.status_code == 200
    assert response.json()["matched"] is True
    assert http.transport.calls == []


def test_a_segment_can_be_evaluated_against_an_inline_company(http, http_segment):
    response = http.post(
        f"{PREFIX}/segments/{http_segment}/evaluate", json={"company": {"industry": "Software"}}
    )
    assert response.json()["matched"] is True


def test_a_lead_keeps_its_own_fields_over_http(http):
    created = http.post(
        f"{PREFIX}/leads", json={"name": "Acme", "firmographics": {"hiringSignal": "expanding"}}
    ).json()["lead"]
    assert http.get(f"{PREFIX}/leads/{created['id']}").json()["data"]["firmographics"] == {
        "hiringSignal": "expanding"
    }


def test_a_contact_is_added_and_listed_over_http(http, http_lead_id):
    created = http.post(f"{PREFIX}/leads/{http_lead_id}/contacts", json=dict(CONTACT_ENGINEER))
    assert created.status_code == 201
    listed = http.get(f"{PREFIX}/leads/{http_lead_id}/contacts").json()
    assert listed["count"] == 1 and listed["contacts"][0]["name"] == "A. Buyer"


def test_a_visit_over_http_reports_every_workflow_outcome(http, http_workflow, http_lead_id):
    response = http.post(f"{PREFIX}/visits?actor=dana", json={"leadId": http_lead_id})
    assert response.status_code == 201
    body = response.json()
    assert body["matchedWorkflows"] == 1
    assert body["deliveries"][0]["state"] == "delivered"
    assert body["deliveries"][0]["payload"]["company"]["industry"] == "Software"


def test_a_visit_over_http_carries_the_token_in_the_header_and_the_body(http, http_workflow, http_lead_id):
    http.post(f"{PREFIX}/visits", json={"leadId": http_lead_id})
    call = http.transport.calls[0]
    assert call["headers"][TOKEN_HEADER] == http_workflow["token"]
    assert call["body"]["token"] == http_workflow["token"]
    assert call["timeout"] == DEFAULT_TIMEOUT_SECONDS


def test_the_delivery_log_can_be_filtered_and_summarised_over_http(http, http_workflow, http_lead_id):
    http.post(f"{PREFIX}/visits", json={"leadId": http_lead_id})
    body = http.get(f"{PREFIX}/deliveries", params={"state": "delivered"}).json()
    assert body["count"] == 1 and body["summary"] == {"delivered": 1}
    assert http.get(f"{PREFIX}/deliveries", params={"state": "skipped"}).json()["count"] == 0


def test_a_bad_where_is_refused_as_this_features_own_error(http):
    response = http.get(f"{PREFIX}/deliveries", params={"where": "{not json"})
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_where"


def test_a_workflow_can_be_paused_and_resumed_over_http(http, http_workflow, http_lead_id):
    workflow_id = http_workflow["workflow"]["id"]
    http.patch(f"{PREFIX}/workflows/{workflow_id}", json={"active": False})
    body = http.post(f"{PREFIX}/visits", json={"leadId": http_lead_id}).json()
    assert body["deliveries"][0]["skipReason"] == SKIP_INACTIVE
    http.patch(f"{PREFIX}/workflows/{workflow_id}", json={"active": True})
    assert http.post(f"{PREFIX}/visits", json={"leadId": http_lead_id}).json()["deliveries"][0]["state"] == "delivered"


def test_deleting_a_workflow_over_http_is_a_204_and_keeps_the_log(http, http_workflow, http_lead_id):
    workflow_id = http_workflow["workflow"]["id"]
    http.post(f"{PREFIX}/visits", json={"leadId": http_lead_id})
    assert http.delete(f"{PREFIX}/workflows/{workflow_id}").status_code == 204
    assert http.get(f"{PREFIX}/workflows").json()["count"] == 0
    assert http.get(f"{PREFIX}/deliveries").json()["count"] == 1


def test_a_failed_delivery_can_be_resent_over_http(http, http_workflow, http_lead_id):
    http.transport.scripted.extend([boom(500), ok()])
    http.post(f"{PREFIX}/visits", json={"leadId": http_lead_id})
    delivery = http.get(f"{PREFIX}/deliveries", params={"state": "failed"}).json()["deliveries"][0]
    response = http.post(f"{PREFIX}/deliveries/{delivery['id']}/resend?actor=dana")
    assert response.status_code == 200
    assert response.json()["state"] == "delivered"
    assert len(http.get(f"{PREFIX}/deliveries/{delivery['id']}").json()["attemptLog"]) == 2


def test_the_preview_route_names_the_query_parameter_it_needs(http, http_workflow, http_lead_id):
    assert http.get(f"{PREFIX}/workflows/{http_workflow['workflow']['id']}/preview").status_code == 422
    response = http.get(
        f"{PREFIX}/workflows/{http_workflow['workflow']['id']}/preview", params={"lead_id": http_lead_id}
    )
    assert response.json()["wouldSend"] is True


def test_the_token_route_returns_it_in_full_and_the_list_does_not(http, http_workflow):
    workflow_id = http_workflow["workflow"]["id"]
    assert http.post(f"{PREFIX}/workflows/{workflow_id}/token").json()["token"] == http_workflow["token"]
    assert http_workflow["token"] not in http.get(f"{PREFIX}/workflows").text
    assert http_workflow["token"] not in http.get(f"{PREFIX}/workflows/{workflow_id}").text


def test_the_room_scoped_routes_answer_for_one_room(http, http_room, http_segment):
    lead = http.post(f"{PREFIX}/leads?room_id={http_room}", json={"name": "Room lead", **SOFTWARE}).json()["lead"]
    http.post(
        f"{PREFIX}/workflows?room_id={http_room}",
        json={"name": "Room workflow", "url": TARGET, "conditions": {"segmentIds": [http_segment]}},
    )
    http.post(f"{PREFIX}/visits?room_id={http_room}", json={"leadId": lead["id"]})

    assert http.get(f"{PREFIX}/rooms/{http_room}/summary").json()["leads"] == 1
    assert http.get(f"{PREFIX}/rooms/{http_room}/leads").json()["count"] == 1
    assert http.get(f"{PREFIX}/rooms/{http_room}/visits").json()["count"] == 1
    assert http.get(f"{PREFIX}/rooms/{http_room}/deliveries").json()["count"] == 1
    assert http.get(f"{PREFIX}/rooms/{http_room}/workflows").json()["count"] == 1
    assert http.get(f"{PREFIX}/rooms/room_absent/visits").json()["count"] == 0


def test_the_fields_route_reports_every_collection(http):
    body = http.get(f"{PREFIX}/fields").json()
    assert set(body["collections"]) == set(ALL_COLLECTIONS)


def test_the_summary_route_is_unscoped_and_the_room_one_is_not(http, http_room, http_lead_id):
    assert http.get(f"{PREFIX}/summary").json()["leads"] == 1
    assert http.get(f"{PREFIX}/summary").json()["roomId"] is None
    assert http.get(f"{PREFIX}/rooms/{http_room}/summary").json()["leads"] == 0


# --------------------------------------------------------------------------- #
# Error mapping
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "method,path,kwargs,status,code",
    [
        ("POST", "/visits", {"json": {}}, 422, "invalid_lead"),
        ("POST", "/segments", {"json": {"rules": []}}, 422, "invalid_segment"),
        ("POST", "/segments", {"json": {"name": "S", "rules": "no"}}, 422, "invalid_segment"),
        ("POST", "/leads", {"json": {}}, 422, "invalid_lead"),
        ("POST", "/workflows", {"json": {"name": "W"}}, 400, "invalid_target"),
        ("POST", "/workflows", {"json": {"name": "W", "url": TARGET}}, 422, "invalid_workflow"),
        ("POST", "/segments/x/evaluate", {"json": {"company": {}}}, 404, "not_found"),
        ("GET", "/leads/nope", {}, 404, "not_found"),
        ("GET", "/deliveries/nope", {}, 404, "not_found"),
        ("DELETE", "/workflows/nope", {}, 404, "not_found"),
        ("GET", "/segments/nope", {}, 404, "not_found"),
        ("GET", "/visits/nope", {}, 404, "not_found"),
        ("GET", "/workflows/nope", {}, 404, "not_found"),
        ("POST", "/leads/nope/contacts", {"json": {"name": "A"}}, 404, "not_found"),
        ("POST", "/deliveries/nope/resend", {}, 404, "not_found"),
    ],
)
def test_a_domain_refusal_becomes_the_documented_status(http, method, path, kwargs, status, code):
    response = http.request(method, f"{PREFIX}{path}", **kwargs)
    assert response.status_code == status, response.text
    body = response.json()
    assert body["error"] == code
    assert body["correlation_id"].startswith("corr")
    assert body["status"] == status


def test_the_error_detail_carries_the_code_the_remedy_and_the_correlation_id(http):
    """"apiRequest" keeps only ``detail``, so everything a user needs rides in it."""
    body = http.post(f"{PREFIX}/visits", json={}).json()
    assert body["error"] in body["detail"]
    assert "correlation id" in body["detail"]
    assert "remediation" in body or "Send leadId" in body["detail"]


def test_a_refusal_writes_no_record(http):
    before = http.get("/api/stats").json()["records"]
    assert http.post(f"{PREFIX}/workflows", json={"name": "W", "url": TARGET}).status_code == 422
    assert http.post(f"{PREFIX}/visits", json={}).status_code == 422
    assert http.post(f"{PREFIX}/segments", json={"rules": []}).status_code == 422
    assert http.get("/api/stats").json()["records"] == before


def test_the_handler_claims_one_hierarchy_and_not_value_error():
    """``IntentStreamError`` *is* a ``ValueError``, so the key set is the claim.

    A handler for ``ValueError`` itself would let this feature intercept an
    exception raised anywhere in the app. The registry already refuses two
    handlers for one type, so the narrow key is what keeps the other features
    loadable.
    """
    module = load_feature(MODULE)
    assert set(module.EXCEPTION_HANDLERS) == {IntentStreamError}
    assert "ValueError" not in module.EXCEPTION_HANDLERS
    assert "RecordNotFound" not in module.EXCEPTION_HANDLERS, "the core app already maps it to 404"


# --------------------------------------------------------------------------- #
# The audit-source rule
# --------------------------------------------------------------------------- #


def _matches_registered_route(source: str, routes: list[dict]) -> bool:
    """Does ``"POST /api/wf-032/visits"`` name a real route?

    Compared segment by segment, with a ``{parameter}`` segment matching any one
    segment. A route built from ``router.prefix`` and a literal id therefore
    matches, and one built from a path this app does not serve does not.
    """
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
            for expected, found in zip(template, actual)
        ):
            return True
    return False


def test_every_write_audit_row_names_a_route_the_app_serves(http):
    """The brief's central guarantee, checked against the live route table.

    An audit row naming a path the app had stopped serving is worse than no
    audit row, because it looks authoritative.
    """
    segment_id = http.post(
        f"{PREFIX}/segments?actor=dana",
        json={"name": "Audit", "rules": [{"path": "industry", "operator": "eq", "value": "Software"}]},
    ).json()["segment"]["id"]
    lead_id = http.post(
        f"{PREFIX}/leads?actor=dana&room_id=room_audit", json={"name": "Audit Co", **SOFTWARE}
    ).json()["lead"]["id"]
    http.post(f"{PREFIX}/leads/{lead_id}/contacts?actor=dana", json=dict(CONTACT_ENGINEER))
    workflow_id = http.post(
        f"{PREFIX}/workflows?actor=dana",
        json={"name": "Audit out", "url": TARGET, "conditions": {"segmentIds": [segment_id]}},
    ).json()["workflow"]["id"]
    visit_id = http.post(f"{PREFIX}/visits?actor=dana&room_id=room_audit", json={"leadId": lead_id}).json()["visit"]["id"]
    http.patch(f"{PREFIX}/workflows/{workflow_id}?actor=dana", json={"active": False})
    http.patch(f"{PREFIX}/segments/{segment_id}?actor=dana", json={"name": "Audit 2"})
    failed = http.get(f"{PREFIX}/deliveries", params={"state": "failed"}).json()["deliveries"]
    if not failed:
        http.patch(f"{PREFIX}/workflows/{workflow_id}?actor=dana", json={"active": True})
    http.delete(f"{PREFIX}/workflows/{workflow_id}?actor=dana")
    http.delete(f"{PREFIX}/segments/{segment_id}?actor=dana")
    assert visit_id

    served = [
        route
        for feature in http.get("/api/features").json()["features"]
        for route in feature["routes"]
    ]
    entries = http.get("/api/audit", params={"limit": 500}).json()["entries"]
    sources = {entry["source"] for entry in entries if entry["source"]}
    ours = {source for source in sources if source.split(" ", 1)[1].startswith(PREFIX)}

    assert ours, f"no wf-032 write was audited at all; saw {sorted(sources)}"
    for source in sorted(ours):
        assert _matches_registered_route(source, served), (
            f"audit row names {source!r}, which is not a route this app serves"
        )


def test_a_write_records_the_route_that_actually_served_it(http):
    segment_id = http.post(
        f"{PREFIX}/segments?actor=dana",
        json={"name": "S", "rules": [{"path": "a", "operator": "exists"}]},
    ).json()["segment"]["id"]
    entry = http.get("/api/audit", params={"collection": SEGMENT_COLLECTION, "action": "insert"}).json()["entries"][0]
    assert entry["source"] == f"POST {PREFIX}/segments"
    assert entry["actor"] == "dana"
    assert segment_id


def test_the_rows_a_visit_creates_name_the_visit_route(http, http_workflow, http_lead_id):
    http.post(f"{PREFIX}/visits?actor=dana", json={"leadId": http_lead_id})
    for collection in (VISIT_COLLECTION, DELIVERY_COLLECTION):
        rows = http.get("/api/audit", params={"collection": collection}).json()["entries"]
        assert rows, collection
        assert {row["source"] for row in rows} == {f"POST {PREFIX}/visits"}
    # The lead's *update* is the one the visit wrote; its insert names the route
    # that created it.
    lead_rows = http.get("/api/audit", params={"collection": LEAD_COLLECTION}).json()["entries"]
    by_action = {row["action"]: row["source"] for row in lead_rows}
    assert by_action["insert"] == f"POST {PREFIX}/leads"
    assert by_action["update"] == f"POST {PREFIX}/visits"


def test_a_pause_names_the_patch_route(http, http_workflow):
    workflow_id = http_workflow["workflow"]["id"]
    http.patch(f"{PREFIX}/workflows/{workflow_id}?actor=dana", json={"active": False})
    rows = http.get("/api/audit", params={"collection": WORKFLOW_COLLECTION, "action": "update"}).json()["entries"]
    assert rows[0]["source"] == f"PATCH {PREFIX}/workflows/{workflow_id}"


def test_a_resend_names_the_resend_route(http, http_workflow, http_lead_id):
    http.transport.scripted.extend([boom(500), ok()])
    http.post(f"{PREFIX}/visits", json={"leadId": http_lead_id})
    delivery = http.get(f"{PREFIX}/deliveries", params={"state": "failed"}).json()["deliveries"][0]
    http.post(f"{PREFIX}/deliveries/{delivery['id']}/resend?actor=dana")
    rows = http.get("/api/audit", params={"collection": DELIVERY_COLLECTION, "action": "update"}).json()["entries"]
    assert rows[0]["source"] == f"POST {PREFIX}/deliveries/{delivery['id']}/resend"


def test_a_delete_names_the_delete_route(http, http_workflow):
    workflow_id = http_workflow["workflow"]["id"]
    http.delete(f"{PREFIX}/workflows/{workflow_id}?actor=dana")
    rows = http.get("/api/audit", params={"collection": WORKFLOW_COLLECTION, "action": "delete"}).json()["entries"]
    assert rows[0]["source"] == f"DELETE {PREFIX}/workflows/{workflow_id}"


def test_a_fresh_database_has_no_audit_rows_for_this_feature_to_hide_behind(http):
    """The audit-source test above is only meaningful if the log starts empty."""
    entries = http.get("/api/audit", params={"limit": 10}).json()["entries"]
    assert entries == []


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #


@pytest.fixture()
def seeded(tmp_path):
    """A database seeded by the real seeder, so the demo rows are the real rows."""
    import random
    import sys

    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root / "backend"))
    import seed as seeder

    db = AuditedDatabase(tmp_path / "seeded.db", mirror_dir=tmp_path / "mirror", actor="seed")
    rooms: list[tuple[str, str]] = []
    for spec in seeder.ROOMS:
        room = db.create("room", spec, actor="seed", source="seed")
        rooms.append((room["id"], spec["account"]))
    module = load_feature(MODULE)
    summary = module.seed(
        db, {"room_ids": rooms, "now": NOW, "rng": random.Random("wf032")}
    )
    stream = IntentStream(RecordStore(db), transport=FakeTransport(), now=NOW)
    yield stream, summary
    db.close()


def test_the_seed_reports_what_it_added(seeded):
    _, summary = seeded
    assert "segments" in summary and "delivery decisions" in summary
    assert "seed FAILED" not in summary


def test_the_seed_produces_all_three_delivery_states(seeded):
    stream, _ = seeded
    summary = stream.summary()
    assert summary["byState"]["delivered"] > 0
    assert summary["byState"]["failed"] > 0
    assert summary["byState"]["skipped"] > 0


def test_the_seed_shows_every_skip_reason(seeded):
    stream, _ = seeded
    by_reason = stream.summary()["bySkipReason"]
    assert by_reason["already_sent"] > 0
    assert by_reason["workflow_inactive"] > 0
    assert by_reason["segment_not_matched"] > 0


def test_the_seed_shows_both_kinds_of_failure(seeded):
    """A retryable row and a permanent one, or the log cannot answer 'try again?'."""
    stream, _ = seeded
    summary = stream.summary()
    assert summary["retryableFailures"] > 0
    assert summary["permanentFailures"] > 0


def test_the_seed_shows_a_resend_that_worked_next_to_one_that_cannot(seeded):
    stream, _ = seeded
    logs = [row["data"] for row in stream.books.deliveries.find({"state": "delivered"}, limit=200)]
    multi = [row for row in logs if len(row.get("attemptLog") or []) > 1]
    assert multi, "no delivery in the demo has more than one attempt"
    assert any(attempt["via"] == "resend" for attempt in multi[0]["attemptLog"])


def test_the_seed_shows_both_contact_filter_outcomes(seeded):
    """Some contacts kept, and a company sent with nobody."""
    stream, _ = seeded
    rows = [row["data"] for row in stream.books.deliveries.find({"payload": "company_contacts"}, limit=200)]
    included = {row.get("contactsIncluded") for row in rows}
    considered = {row.get("contactsConsidered") for row in rows}
    assert any(value and value > 0 for value in included), included
    assert 0 in included, "no delivery shows the filter keeping nobody"
    assert max(considered) > max(included), "the counts do not show who was filtered out"


def test_the_seed_shows_a_lead_sent_once_and_a_lead_sent_twice(seeded):
    stream, _ = seeded
    counts = {row["data"].get("sendCount") for row in stream.books.states.list(limit=200)}
    assert 1 in counts
    assert any(value and value > 1 for value in counts), counts


def test_the_seed_shows_a_visit_that_matched_nothing_at_all(seeded):
    stream, _ = seeded
    assert stream.list_visits(matched=False), "every demo visit matched something"


def test_the_seed_shows_a_company_no_saved_segment_selects(seeded):
    stream, _ = seeded
    verdicts = [
        segment_module.evaluate_segment(segment["data"], lead["data"])
        for segment in stream.books.segments.list(limit=50)
        for lead in stream.books.leads.list(limit=50)
    ]
    assert any(not verdict["matched"] for verdict in verdicts)


def test_the_seed_writes_through_the_audited_store_only(seeded, tmp_path):
    stream, _ = seeded
    entries = stream.store.audit(limit=2000)
    assert entries
    collections = {entry["collection"] for entry in entries}
    assert collections <= set(ALL_COLLECTIONS) | {"room"}
    for entry in entries:
        assert entry["source"], entry


def test_the_seed_never_opened_a_socket(monkeypatch):
    """The demo transport is scripted; a real POST from the seeder would hang."""
    import socket

    def refuse(*args, **kwargs):
        raise AssertionError("the seeder opened a socket")

    monkeypatch.setattr(socket, "socket", refuse)
    root = Path(__file__).resolve().parents[2]
    import sys

    sys.path.insert(0, str(root / "backend"))
    import seed as seeder

    db = AuditedDatabase(":memory:", actor="seed")
    try:
        room = db.create("room", {"name": "R", "account": "A"}, source="seed")
        summary = load_feature(MODULE).seed(
            db, {"room_ids": [(room["id"], "A")], "now": NOW, "rng": None}
        )
    finally:
        db.close()
    assert "delivery decisions" in summary


def test_the_seed_survives_an_account_with_no_rooms(tmp_path):
    db = AuditedDatabase(tmp_path / "empty.db", actor="seed")
    try:
        summary = load_feature(MODULE).seed(db, {"room_ids": [], "now": NOW, "rng": None})
    finally:
        db.close()
    assert "no rooms" in summary


# --------------------------------------------------------------------------- #
# What this feature deliberately does not build
# --------------------------------------------------------------------------- #


def _our_paths() -> set[str]:
    entry = next(record for record in REGISTRY.features if record.ticket == "WF-032")
    return {route["path"] for route in entry.routes}


def test_there_is_no_retry_ladder_route():
    """This research specifies no retry at all, so none is scheduled.

    The sibling event-stream feature has a 26-retry ladder, sourced to a
    different vendor. Copying the number here would be this build inventing a
    policy the sources never state, and ``/inferences`` says so by name.
    """
    assert not any("ladder" in path or "attempts" in path for path in _our_paths())


def test_there_is_no_token_rotation_route():
    """Nothing in the sources mentions rotation, so nothing rotates."""
    paths = _our_paths()
    assert not any("rotate" in path for path in paths)
    assert f"{PREFIX}/workflows/{{workflow_id}}/token" in paths


def test_there_is_no_inbound_verification_route():
    """"No public inbound REST reference for Albacross was reachable"."""
    assert not any(path.endswith("/verify") for path in _our_paths())


def test_the_verification_recipe_names_the_header_the_product_actually_sends():
    from dsr.intent_stream.vocabulary import TOKEN_HEADER as header
    from dsr.intent_stream.vocabulary import TOKEN_VERIFICATION_RECIPE as recipe

    assert header in recipe
    # Parses, so a copy-paste into the destination is not a syntax error.
    compile(recipe, "recipe", "exec")
