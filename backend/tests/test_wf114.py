"""WF-114's domain surface: the pure rules, the signing rule, and the engine.

What this file covers
---------------------
* **The architectural guards.** The domain package imports nothing but the store
  and itself, never ``dsr.api``, never a bare ``sqlite3`` connection, and never
  another workflow's package.
* **The vocabulary reuse.** Every name this workflow claims to reuse from WF-113
  - the signed input, the digest, the two header names, the freshness window, the
  endpoint-token shape and the three shared trigger states - is pinned to
  ``dsr.webhook_ingest`` here, so the two packages cannot drift. This is the
  mechanism that makes "reuse it rather than re-declare it" checkable, because the
  architectural guard forbids the domain package from importing the other package
  directly.
* **The pure rules**, one test per operator and per coercion, because the mapping
  step is where a payload becomes workflow state and a wrong comparison is
  invisible until a workflow starts with the wrong variable.
* **The engine**, including the states the issue asks for by name: started,
  duplicate, tested, unmapped and rejected.
* **The seed**, whose summary must name at least one state that is not a success
  and be encodable in cp1252.

Every test uses the shared ``store`` fixture from ``conftest.py``, so each one runs
against a fresh database and no test can see another's rows. That is what makes
the file pass on its own and under xdist.
"""

from __future__ import annotations

import ast
import importlib
import pathlib
from datetime import datetime, timedelta, timezone

import pytest
from dsr.features import wf114_start_a_workflow_from_an_external_systems_webhook as feature
from dsr.store import RecordStore
from dsr.webhook_workflow_start import (
    FAIL_CLOSED,
    ROUTER_PREFIX,
    TICKET,
    WebhookWorkflowError,
    WebhookWorkflowStart,
    errors,
    inferences,
    rules,
    signing,
    vocabulary,
)

NOW = datetime(2026, 3, 4, 12, 0, tzinfo=timezone.utc)
SECRET = "domain-test-signing-value"
SOURCE = feature.hook_source("POST")
FEATURE_MODULE = "dsr.features.wf114_start_a_workflow_from_an_external_systems_webhook"
DOMAIN_PACKAGE = "dsr/webhook_workflow_start"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def build(store: RecordStore, when: datetime = NOW):
    """A service over a store, with a frozen clock and a settable one."""
    clock = {"t": when}
    return WebhookWorkflowStart(store, now=lambda: clock["t"]), clock


def make_ready(store: RecordStore, *, mappings=None, actions=None, when: datetime = NOW):
    """A published workflow with a saved trigger. Returns ``(service, clock, trigger)``."""
    service, clock = build(store, when)
    workflow = service.create_workflow(
        "room_domain",
        {"name": "Payments", "actions": actions or ["Update the lead's status to 'Paid.'"]},
        actor="dana",
        source="test/workflows",
    )
    trigger = service.create_trigger(
        workflow["id"],
        {
            "name": "Payment result",
            "signing_value": SECRET,
            "mappings": mappings
            if mappings is not None
            else [{"variable": "customer_ref", "path": "customer_id"}],
        },
        actor="dana",
        source="test/triggers",
    )
    service.save_trigger(trigger["id"], actor="dana", source="test/save")
    service.publish_workflow(workflow["id"], actor="dana", source="test/publish")
    return service, clock, trigger


def deliver(service, trigger, payload, *, method="POST", mode=None, when=None, secret=SECRET):
    """A correctly signed delivery of ``payload``."""
    moment = when or service.clock()
    if method == "GET":
        query = {str(key): str(value) for key, value in (payload or {}).items()}
        return service.deliver(
            trigger["token"],
            method="GET",
            payload=query,
            raw_body=rules.canonical_json(query),
            query=query,
            signature=signing.sign(
                secret, signing.unix_seconds(moment), rules.canonical_json(query)
            ),
            timestamp=signing.unix_seconds(moment),
            mode=mode,
            actor="provider",
            source=feature.hook_source("GET"),
        )
    body = rules.canonical_json(payload)
    return service.deliver(
        trigger["token"],
        method=method,
        payload=payload,
        raw_body=body,
        body_present=True,
        signature=signing.sign(secret, signing.unix_seconds(moment), body),
        timestamp=signing.unix_seconds(moment),
        mode=mode,
        actor="provider",
        source=feature.hook_source(method),
    )


def domain_paths() -> list[pathlib.Path]:
    package = pathlib.Path(feature.__file__).resolve().parent.parent / "webhook_workflow_start"
    return sorted(package.glob("*.py"))


# --------------------------------------------------------------------------- #
# The architectural guards
# --------------------------------------------------------------------------- #


class TestArchitecture:
    def test_the_domain_package_imports_nothing_but_the_store(self):
        """The guard the brief names by name."""
        for path in domain_paths():
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                names: list[str] = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module]
                for name in names:
                    if not name.startswith("dsr"):
                        continue
                    assert (
                        name == "dsr.store"
                        or name == "dsr.webhook_workflow_start"
                        or name.startswith("dsr.webhook_workflow_start.")
                    ), f"{path.name} imports {name}"

    def test_the_domain_package_never_imports_the_app(self):
        for path in domain_paths():
            text = path.read_text(encoding="utf-8")
            assert "from dsr.api" not in text and "import dsr.api" not in text, path.name

    def test_the_domain_package_never_opens_sqlite(self):
        for path in domain_paths():
            text = path.read_text(encoding="utf-8")
            assert "import sqlite3" not in text, path.name
            assert "sqlite3.connect" not in text, path.name

    def test_the_domain_package_imports_no_other_workflows_package(self):
        for path in domain_paths():
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                names: list[str] = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module]
                for name in names:
                    assert not name.startswith("dsr.features"), f"{path.name} imports {name}"

    def test_the_feature_module_never_imports_the_app(self):
        assert "from dsr.api" not in pathlib.Path(feature.__file__).read_text(encoding="utf-8")
        assert "from dsr.deps import" in pathlib.Path(feature.__file__).read_text(encoding="utf-8")

    def test_the_feature_module_exposes_the_descriptor_the_host_needs(self):
        assert feature.FEATURE["id"].startswith("wf-114-")
        assert feature.FEATURE["ticket"] == TICKET
        assert feature.router.prefix == ROUTER_PREFIX == "/api/wf-114"
        assert feature.FEATURE["nav"]
        assert feature.EXCEPTION_HANDLERS == {WebhookWorkflowError: feature._workflow_error}

    def test_every_domain_error_is_in_the_feature_handled_hierarchy(self):
        for name in errors.__all__:
            error = getattr(errors, name)
            assert issubclass(error, WebhookWorkflowError)
            assert error.status >= 400
            assert error.code
        assert set(feature.EXCEPTION_HANDLERS) == {WebhookWorkflowError}


# --------------------------------------------------------------------------- #
# The vocabulary reuse, pinned to WF-113
# --------------------------------------------------------------------------- #


class TestReusesWf113:
    def test_the_signed_input_matches_wf113(self):
        from dsr.webhook_ingest import signing as other

        assert signing.signing_input("1", "{}") == other.signing_input("1", "{}")

    def test_the_digest_matches_wf113(self):
        from dsr.webhook_ingest import signing as other

        assert signing.sign(SECRET, "1", "{}") == other.sign(SECRET, "1", "{}")

    def test_the_header_names_match_wf113(self):
        from dsr.webhook_ingest import signing as other

        assert signing.SIGNATURE_HEADER == other.SIGNATURE_HEADER == vocabulary.SIGNATURE_HEADER
        assert signing.TIMESTAMP_HEADER == other.TIMESTAMP_HEADER == vocabulary.TIMESTAMP_HEADER

    def test_the_freshness_window_matches_wf113(self):
        from dsr.webhook_ingest import signing as other

        assert signing.MAX_AGE_SECONDS == other.MAX_AGE_SECONDS == vocabulary.MAX_AGE_SECONDS == 300

    def test_the_three_shared_trigger_states_match_wf113(self):
        from dsr.webhook_ingest import vocabulary as other

        assert vocabulary.SHARED_TRIGGER_STATES == other.TRIGGER_STATES
        for state in vocabulary.SHARED_TRIGGER_STATES:
            assert state in vocabulary.STATES

    def test_the_token_is_a_row_that_resolves_to_one_trigger(self, store):
        service, _clock, trigger = make_ready(store)
        assert trigger["token"]
        assert len(service.triggers("room_domain")) == 1
        found = store.find(vocabulary.COLLECTION_TRIGGER, {"token": trigger["token"]}, limit=2)
        assert len(found) == 1
        assert found[0]["id"] == trigger["id"]

    def test_the_fingerprint_is_the_canonical_json_of_the_payload(self):
        first = rules.fingerprint({"b": 1, "a": 2})
        second = rules.fingerprint({"a": 2, "b": 1})
        assert first == second
        assert first != rules.fingerprint({"a": 2, "b": 3})

    def test_the_reused_names_are_published_for_a_reader(self):
        described = vocabulary.describe()
        assert described["signature_header"] == signing.SIGNATURE_HEADER
        assert described["max_age_seconds"] == 300
        assert len(vocabulary.REUSED_FROM_WF113) >= 4


# --------------------------------------------------------------------------- #
# The researched vocabulary
# --------------------------------------------------------------------------- #


class TestVocabulary:
    def test_the_url_is_unique_per_trigger(self):
        assert vocabulary.TICKET == "WF-114"
        assert vocabulary.ENDPOINT_PATH_TEMPLATE == "/api/wf-114/hooks/{token}"
        assert "{token}" in vocabulary.ENDPOINT_PATH_TEMPLATE

    def test_the_three_researched_methods_are_served(self):
        assert vocabulary.METHODS == ("POST", "GET", "PUT")

    def test_the_get_payload_source_is_the_query_string(self):
        by_method = {row["method"]: row for row in vocabulary.METHOD_PAYLOAD_SOURCES}
        assert by_method["POST"]["source"] == vocabulary.METHOD_PAYLOAD_SOURCE_BODY
        assert by_method["PUT"]["source"] == vocabulary.METHOD_PAYLOAD_SOURCE_BODY
        assert by_method["GET"]["source"] == vocabulary.METHOD_PAYLOAD_SOURCE_QUERY
        assert by_method["GET"]["accepts_body"] is False

    def test_an_unserved_method_has_no_payload_source(self):
        assert vocabulary.method_payload_source("delete") is None
        assert vocabulary.method_payload_source("post")["method"] == "POST"
        assert not vocabulary.is_served_method("HEAD")

    def test_the_operators_that_need_no_value_are_marked(self):
        assert vocabulary.operator_takes_value(vocabulary.OPERATOR_EXISTS) is False
        assert vocabulary.operator_takes_value(vocabulary.OPERATOR_MISSING) is False
        assert vocabulary.operator_takes_value(vocabulary.OPERATOR_GREATER_THAN) is True
        assert not vocabulary.is_known_operator("matches_regex")

    def test_the_variable_types_are_served(self):
        assert set(vocabulary.VARIABLE_TYPE_IDS) == {"text", "number", "boolean", "json"}
        assert vocabulary.is_known_variable_type("number")
        assert not vocabulary.is_known_variable_type("date")

    def test_the_premium_trigger_is_a_published_fact_and_not_a_gate(self):
        described = vocabulary.describe()
        assert described["premium_trigger"] is True
        assert "no delivery is refused on a tier" in described["premium_trigger_is_not_a_gate"]

    def test_the_documented_payload_example_is_served_verbatim(self):
        assert vocabulary.DOCUMENTED_PAYLOAD == {
            "customer_id": "CUST_001",
            "amount": 100,
            "currency": "USD",
            "status": "successful",
        }

    def test_the_state_set_extends_the_shared_three_with_unmapped(self):
        assert vocabulary.STATE_UNMAPPED in vocabulary.STATES
        assert set(vocabulary.SHARED_TRIGGER_STATES) < set(vocabulary.STATES)

    def test_the_served_vocabulary_document_carries_the_derivations(self):
        described = vocabulary.describe()
        assert described["trigger_label"] == "Inbound Webhook"
        assert described["test_mode_param"] == "test"
        assert described["methods"] == list(vocabulary.METHODS)
        assert described["documented_actions"]
        assert described["collections"] == list(vocabulary.ALL_COLLECTIONS)


# --------------------------------------------------------------------------- #
# The pure rules
# --------------------------------------------------------------------------- #


class TestParsing:
    def test_every_shape_parses(self):
        assert rules.shape_of({"a": 1}) == "object"
        assert rules.shape_of([1, 2]) == "array"
        assert rules.shape_of("x") == "scalar"
        assert rules.shape_of(None) == "empty"

    def test_a_dotted_path_reads_a_nested_value(self):
        payload = {"a": {"b": [{"c": 7}]}}
        assert rules.get_path(payload, "a.b.0.c") == 7

    def test_an_absent_path_is_missing_not_none(self):
        assert rules.is_missing(rules.get_path({"a": 1}, "b"))
        assert not rules.is_missing(rules.get_path({"a": None}, "a"))

    def test_flatten_names_every_scalar_by_its_path(self):
        flat = rules.flatten({"a": 1, "b": {"c": 2}, "d": [3]})
        assert flat == {"a": 1, "b.c": 2, "d.0": 3}

    def test_the_payload_facts_record_shape_and_paths(self):
        facts = rules.payload_facts({"a": 1, "b": {"c": 2}}, source="body")
        assert facts["shape"] == "object"
        assert facts["source"] == "body"
        assert facts["paths"] == ["a", "b.c"]
        assert facts["keys"] == ["a", "b"]

    def test_a_scalar_payload_has_no_paths(self):
        facts = rules.payload_facts(7, source="body")
        assert facts["shape"] == "scalar"
        assert facts["paths"] == []

    def test_the_test_flag_reads_the_documented_words(self):
        assert rules.test_mode_requested("1")
        assert rules.test_mode_requested("true")
        assert rules.test_mode_requested(True)
        assert not rules.test_mode_requested(None)
        assert not rules.test_mode_requested("0")


class TestComparison:
    def test_an_absent_condition_always_passes(self):
        assert rules.match_condition(None, {}) == (True, "")

    def test_equals_compares_text_and_number_across_types(self):
        assert rules.match_condition({"path": "a", "operator": "equals", "value": "x"}, {"a": "X"})
        assert rules.match_condition(
            {"path": "a", "operator": "equals", "value": 100}, {"a": "100"}
        )[0]
        assert not rules.match_condition(
            {"path": "a", "operator": "equals", "value": 100}, {"a": "10"}
        )[0]

    def test_a_boolean_is_not_compared_as_a_number(self):
        assert not rules.match_condition(
            {"path": "a", "operator": "equals", "value": 1}, {"a": True}
        )[0]

    def test_not_equals_is_the_complement(self):
        assert rules.match_condition(
            {"path": "a", "operator": "not_equals", "value": "x"}, {"a": "y"}
        )[0]

    def test_contains_reads_a_substring(self):
        assert rules.match_condition(
            {"path": "a", "operator": "contains", "value": "paid"}, {"a": "PAID in full"}
        )[0]
        assert not rules.match_condition(
            {"path": "a", "operator": "contains", "value": "paid"}, {"a": "pending"}
        )[0]

    def test_in_matches_one_of_a_list(self):
        assert rules.match_condition(
            {"path": "a", "operator": "in", "value": ["x", "y"]}, {"a": "Y"}
        )[0]
        assert not rules.match_condition(
            {"path": "a", "operator": "in", "value": ["x", "y"]}, {"a": "z"}
        )[0]

    def test_exists_and_missing_read_presence(self):
        assert rules.match_condition({"path": "a", "operator": "exists"}, {"a": 0})[0]
        assert not rules.match_condition({"path": "a", "operator": "exists"}, {})[0]
        assert rules.match_condition({"path": "a", "operator": "missing"}, {})[0]

    def test_the_ordering_operators_need_numbers(self):
        assert rules.match_condition(
            {"path": "a", "operator": "greater_than", "value": 50}, {"a": "100"}
        )[0]
        assert rules.match_condition(
            {"path": "a", "operator": "less_than", "value": 50}, {"a": 10}
        )[0]
        ok, reason = rules.match_condition(
            {"path": "a", "operator": "greater_than", "value": 50}, {"a": "lots"}
        )
        assert not ok and reason == "not_a_number"

    def test_an_unknown_operator_never_matches(self):
        ok, reason = rules.match_condition({"path": "a", "operator": "nope"}, {"a": 1})
        assert not ok and reason == "unknown_operator"


class TestCoercion:
    def test_text_passes_through_as_text(self):
        assert rules.coerce_value("CUST_001", "text") == (True, "CUST_001", "")

    def test_a_number_is_coerced_from_a_query_string(self):
        assert rules.coerce_value("100", "number") == (True, 100, "")
        assert rules.coerce_value("10.5", "number") == (True, 10.5, "")

    def test_a_non_number_for_a_number_variable_is_declined(self):
        ok, value, reason = rules.coerce_value("lots", "number")
        assert not ok and value is None and reason == "not_a_number"

    def test_a_boolean_is_coerced_from_the_words_a_query_carries(self):
        assert rules.coerce_value("true", "boolean") == (True, True, "")
        assert rules.coerce_value("no", "boolean") == (True, False, "")
        assert rules.coerce_value(True, "boolean") == (True, True, "")
        ok, _value, reason = rules.coerce_value("maybe", "boolean")
        assert not ok and reason == "not_a_boolean"

    def test_json_keeps_its_shape_and_a_mapping_becomes_json_text(self):
        assert rules.coerce_value({"a": 1}, "json") == (True, {"a": 1}, "")
        assert rules.coerce_value({"a": 1}, "text")[1] == '{"a":1}'


class TestMappingPlan:
    def test_a_row_binds_its_path_to_its_variable(self):
        plan = rules.plan_bindings(
            [{"variable": "ref", "path": "customer_id"}], {"customer_id": "CUST_1"}
        )
        assert plan["matched"] == 1
        assert plan["bindings"][0]["variable"] == "ref"
        assert plan["bindings"][0]["value"] == "CUST_1"
        assert plan["required_unresolved"] == []

    def test_an_absent_required_path_is_unresolved(self):
        plan = rules.plan_bindings([{"variable": "ref", "path": "nope"}], {"a": 1})
        assert plan["matched"] == 0
        assert plan["required_unresolved"][0]["reason"] == "path_absent"

    def test_an_optional_row_that_is_absent_is_only_declined(self):
        plan = rules.plan_bindings(
            [{"variable": "ref", "path": "nope", "required": False}], {"a": 1}
        )
        assert plan["declined"][0]["reason"] == "path_absent"
        assert plan["required_unresolved"] == []

    def test_a_condition_that_does_not_match_is_a_decline_not_a_refusal(self):
        plan = rules.plan_bindings(
            [
                {
                    "variable": "ref",
                    "path": "customer_id",
                    "when": {"path": "status", "operator": "equals", "value": "refunded"},
                }
            ],
            {"customer_id": "CUST_1", "status": "successful"},
        )
        assert plan["matched"] == 0
        assert plan["required_unresolved"] == []
        assert plan["declined"][0]["reason"].startswith("condition_not_met")

    def test_a_payload_that_binds_nothing_reports_zero_of_zero(self):
        plan = rules.plan_bindings(
            [{"variable": "a", "path": "x", "when": {"path": "y", "operator": "exists"}}],
            {"x": 1},
        )
        assert plan["matched"] == 0
        assert plan["planned"] == 1

    def test_a_mapping_needs_a_variable_and_a_path(self):
        with pytest.raises(errors.InvalidTrigger):
            rules.normalise_mappings([{"path": "a"}])
        with pytest.raises(errors.InvalidTrigger):
            rules.normalise_mappings([{"variable": "a"}])
        with pytest.raises(errors.InvalidTrigger):
            rules.normalise_mappings("not a list")

    def test_a_condition_is_validated_when_the_trigger_is_written(self):
        with pytest.raises(errors.InvalidTrigger):
            rules.normalise_mappings(
                [{"variable": "a", "path": "x", "when": {"path": "y", "operator": "nope"}}]
            )
        with pytest.raises(errors.InvalidTrigger):
            rules.normalise_mappings(
                [{"variable": "a", "path": "x", "when": {"operator": "equals", "value": 1}}]
            )
        with pytest.raises(errors.InvalidTrigger):
            rules.normalise_mappings(
                [{"variable": "a", "path": "x", "when": {"path": "y", "operator": "equals"}}]
            )

    def test_a_condition_that_needs_no_value_is_accepted_without_one(self):
        rows = rules.normalise_mappings(
            [{"variable": "a", "path": "x", "when": {"path": "y", "operator": "exists"}}]
        )
        assert rows[0]["when"]["operator"] == "exists"

    def test_a_variable_type_is_validated_and_carried(self):
        rows = rules.normalise_mappings([{"variable": "a", "path": "x", "type": "number"}])
        assert rows[0]["type"] == "number"
        with pytest.raises(errors.InvalidTrigger):
            rules.normalise_mappings([{"variable": "a", "path": "x", "type": "date"}])

    def test_no_mappings_planned_binds_nothing_and_plans_nothing(self):
        plan = rules.plan_bindings([], {"a": 1})
        assert plan["planned"] == 0
        assert plan["matched"] == 0


class TestSigning:
    def test_a_correct_signature_verifies(self):
        body = '{"a":1}'
        stamp = signing.unix_seconds(NOW)
        ok, reason = signing.verify(
            SECRET, body, signature=signing.sign(SECRET, stamp, body), timestamp=stamp, now=NOW
        )
        assert ok and reason == ""

    def test_a_missing_signature_is_refused_and_named(self):
        ok, reason = signing.verify(SECRET, "{}", signature=None, timestamp="1", now=NOW)
        assert not ok and reason == "signature_missing"

    def test_a_missing_timestamp_is_refused_and_named(self):
        ok, reason = signing.verify(SECRET, "{}", signature="abc", timestamp=None, now=NOW)
        assert not ok and reason == "timestamp_missing"

    def test_a_wrong_signature_is_refused_and_named(self):
        stamp = signing.unix_seconds(NOW)
        ok, reason = signing.verify(
            SECRET, "{}", signature=signing.sign("other", stamp, "{}"), timestamp=stamp, now=NOW
        )
        assert not ok and reason == "signature_mismatch"

    def test_a_stale_delivery_is_refused_and_named(self):
        stale = signing.unix_seconds(NOW - timedelta(hours=1))
        ok, reason = signing.verify(
            SECRET,
            "{}",
            signature=signing.sign(SECRET, stale, "{}"),
            timestamp=stale,
            now=NOW,
        )
        assert not ok and reason == "signature_stale"

    def test_milliseconds_are_still_a_timestamp(self):
        stamp = str(int(NOW.timestamp() * 1000))
        ok, reason = signing.verify(
            SECRET, "{}", signature=signing.sign(SECRET, stamp, "{}"), timestamp=stamp, now=NOW
        )
        assert ok and reason == ""

    def test_the_window_is_a_parameter(self):
        stale = signing.unix_seconds(NOW - timedelta(hours=1))
        ok, _reason = signing.verify(
            SECRET,
            "{}",
            signature=signing.sign(SECRET, stale, "{}"),
            timestamp=stale,
            window_seconds=None,
            now=NOW,
        )
        assert ok

    def test_the_headers_carry_the_rule_and_a_content_type(self):
        headers = signing.headers_for(SECRET, "1", "{}")
        assert headers[signing.SIGNATURE_HEADER]
        assert headers[signing.TIMESTAMP_HEADER] == "1"
        assert headers["Content-Type"] == "application/json"

    def test_the_build_fails_closed(self):
        assert signing.HEADERS_REQUIRED_ALWAYS is True
        assert "published write endpoint" in FAIL_CLOSED


# --------------------------------------------------------------------------- #
# The engine: workflows, variables and triggers
# --------------------------------------------------------------------------- #


class TestWorkflowsAndTriggers:
    def test_a_new_workflow_lands_a_draft(self, store):
        service, _clock = build(store)
        workflow = service.create_workflow("room_domain", {"name": "Payments"}, actor="dana")
        assert workflow["status"] == vocabulary.STATUS_DRAFT
        assert workflow["room_ref"] == "room_domain"

    def test_a_workflow_needs_a_name(self, store):
        service, _clock = build(store)
        with pytest.raises(errors.InvalidWorkflow):
            service.create_workflow("room_domain", {}, actor="dana")

    def test_an_unknown_workflow_is_a_404(self, store):
        service, _clock = build(store)
        with pytest.raises(errors.UnknownWorkflow):
            service.workflow("nope")

    def test_a_workflow_can_be_renamed_and_given_actions(self, store):
        service, _clock = build(store)
        workflow = service.create_workflow("room_domain", {"name": "Payments"}, actor="dana")
        patched = service.patch_workflow(
            workflow["id"], {"name": "Payments v2", "actions": ["Email the customer"]}
        )
        assert patched["name"] == "Payments v2"
        assert patched["action_count"] == 1
        assert patched["previous_status"] == vocabulary.STATUS_DRAFT

    def test_an_unknown_status_is_refused(self, store):
        service, _clock = build(store)
        workflow = service.create_workflow("room_domain", {"name": "Payments"}, actor="dana")
        with pytest.raises(errors.InvalidWorkflow):
            service.patch_workflow(workflow["id"], {"status": "live"})

    def test_publishing_records_when(self, store):
        service, _clock = build(store)
        workflow = service.create_workflow("room_domain", {"name": "Payments"}, actor="dana")
        published = service.publish_workflow(workflow["id"], actor="dana")
        assert published["status"] == vocabulary.STATUS_PUBLISHED
        assert published["published_at"]

    def test_a_variable_is_declared_with_a_type(self, store):
        service, _clock = build(store)
        workflow = service.create_workflow("room_domain", {"name": "Payments"}, actor="dana")
        variable = service.declare_variable(
            workflow["id"], {"name": "amount", "type": "number"}, actor="dana"
        )
        assert variable["type"] == "number"
        assert service.variables(workflow["id"])[0]["name"] == "amount"
        assert service.variable(variable["id"])["name"] == "amount"

    def test_a_variable_needs_a_name_and_a_known_type(self, store):
        service, _clock = build(store)
        workflow = service.create_workflow("room_domain", {"name": "Payments"}, actor="dana")
        with pytest.raises(errors.InvalidVariable):
            service.declare_variable(workflow["id"], {"type": "number"})
        with pytest.raises(errors.InvalidVariable):
            service.declare_variable(workflow["id"], {"name": "when", "type": "date"})

    def test_an_unknown_variable_is_a_404(self, store):
        service, _clock = build(store)
        with pytest.raises(errors.UnknownVariable):
            service.variable("nope")

    def test_the_trigger_is_created_with_a_generated_url(self, store):
        service, _clock = build(store)
        workflow = service.create_workflow("room_domain", {"name": "Payments"}, actor="dana")
        trigger = service.create_trigger(
            workflow["id"], {"name": "Payment result"}, actor="dana", source="test/triggers"
        )
        assert trigger["url"] == vocabulary.ENDPOINT_PATH_TEMPLATE.format(token=trigger["token"])
        assert trigger["saved"] is False
        assert trigger["methods"] == list(vocabulary.METHODS)
        assert trigger["premium_trigger"] is True
        assert trigger["kind"] == vocabulary.TRIGGER_KIND

    def test_the_signing_value_is_served_once_and_a_read_never_carries_it(self, store):
        service, _clock, trigger = make_ready(store)
        # make_ready set the value explicitly; the read must not return it.
        read_back = service.trigger(trigger["id"])
        assert "signing_value" not in read_back
        assert read_back["has_signing_value"] is True
        fresh = service.create_trigger(trigger["workflow_ref"], {"name": "Second"}, actor="dana")
        assert fresh["signing_value"]
        assert fresh["signing_value_shown_once"] is True
        assert "signing_value" not in service.trigger(fresh["id"])

    def test_a_generated_signing_value_is_long_and_unique(self, store):
        service, _clock = build(store)
        workflow = service.create_workflow("room_domain", {"name": "Payments"}, actor="dana")
        first = service.create_trigger(workflow["id"], {"name": "a"})
        second = service.create_trigger(workflow["id"], {"name": "b"})
        assert first["signing_value"] != second["signing_value"]
        assert first["token"] != second["token"]
        assert len(first["signing_value"]) >= 32

    def test_a_trigger_can_serve_one_method(self, store):
        service, _clock = build(store)
        workflow = service.create_workflow("room_domain", {"name": "Payments"}, actor="dana")
        trigger = service.create_trigger(workflow["id"], {"name": "a", "methods": ["POST"]})
        assert trigger["methods"] == ["POST"]

    def test_an_unserved_method_is_refused_at_creation(self, store):
        service, _clock = build(store)
        workflow = service.create_workflow("room_domain", {"name": "Payments"}, actor="dana")
        with pytest.raises(errors.InvalidTrigger):
            service.create_trigger(workflow["id"], {"name": "a", "methods": ["DELETE"]})
        with pytest.raises(errors.InvalidTrigger):
            service.create_trigger(workflow["id"], {"name": "a", "methods": []})

    def test_mappings_are_validated_at_creation(self, store):
        service, _clock = build(store)
        workflow = service.create_workflow("room_domain", {"name": "Payments"}, actor="dana")
        with pytest.raises(errors.InvalidTrigger):
            service.create_trigger(workflow["id"], {"mappings": [{"path": "a"}]})

    def test_a_mapping_can_be_added_through_patch(self, store):
        service, _clock, trigger = make_ready(store)
        patched = service.patch_trigger(
            trigger["id"],
            {"mappings": [{"variable": "amount", "path": "amount", "type": "number"}]},
            source="test/patch",
        )
        assert patched["mapping_count"] == 1
        assert patched["previous_saved"] is True

    def test_an_unknown_trigger_is_a_404(self, store):
        service, _clock = build(store)
        with pytest.raises(errors.UnknownTrigger):
            service.trigger("nope")

    def test_saving_a_trigger_is_a_deliberate_second_act(self, store):
        service, _clock = build(store)
        workflow = service.create_workflow("room_domain", {"name": "Payments"}, actor="dana")
        trigger = service.create_trigger(workflow["id"], {"name": "a"}, source="test/triggers")
        saved = service.save_trigger(trigger["id"], actor="dana", source="test/save")
        assert saved["saved"] is True
        assert saved["previous_saved"] is False
        again = service.save_trigger(trigger["id"], actor="dana", source="test/save")
        assert again["previous_saved"] is True


# --------------------------------------------------------------------------- #
# The engine: deliveries
# --------------------------------------------------------------------------- #


class TestDelivery:
    def test_the_documented_payload_starts_the_workflow(self, store):
        service, _clock, trigger = make_ready(
            store,
            mappings=[
                {"variable": "customer_ref", "path": "customer_id"},
                {"variable": "amount", "path": "amount", "type": "number"},
                {
                    "variable": "lead_status",
                    "path": "status",
                    "when": {"path": "status", "operator": "equals", "value": "successful"},
                },
            ],
            actions=["Update the lead's status to 'Paid.'"],
        )
        report = deliver(service, trigger, vocabulary.DOCUMENTED_PAYLOAD)
        assert report["state"] == vocabulary.STATE_ACCEPTED
        assert report["started"] is True
        assert report["matched"] == 3
        assert {row["variable"] for row in report["bindings"]} == {
            "customer_ref",
            "amount",
            "lead_status",
        }
        assert report["run"]["status"] == vocabulary.RUN_STARTED
        assert report["run"]["planned_actions"] == ["Update the lead's status to 'Paid.'"]

    def test_a_replay_is_a_duplicate_and_starts_nothing(self, store):
        service, _clock, trigger = make_ready(store)
        first = deliver(service, trigger, {"customer_id": "CUST_1"})
        second = deliver(service, trigger, {"customer_id": "CUST_1"})
        assert first["state"] == vocabulary.STATE_ACCEPTED
        assert second["state"] == vocabulary.STATE_DUPLICATE
        assert second["started"] is False
        assert len(service.runs("room_domain")["runs"]) == 1

    def test_a_different_payload_is_not_a_duplicate(self, store):
        service, _clock, trigger = make_ready(store)
        deliver(service, trigger, {"customer_id": "CUST_1"})
        second = deliver(service, trigger, {"customer_id": "CUST_2"})
        assert second["state"] == vocabulary.STATE_ACCEPTED

    def test_a_test_delivery_validates_and_starts_nothing(self, store):
        service, _clock, trigger = make_ready(store)
        report = deliver(service, trigger, {"customer_id": "CUST_9"}, mode=vocabulary.MODE_TEST)
        assert report["state"] == vocabulary.STATE_ACCEPTED
        assert report["mode"] == vocabulary.MODE_TEST
        assert report["started"] is False
        assert report["run"] is None
        assert report["bindings"][0]["value"] == "CUST_9"
        assert service.runs("room_domain")["count"] == 0

    def test_a_test_request_is_accepted_before_the_trigger_is_saved(self, store):
        service, _clock = build(store)
        workflow = service.create_workflow("room_domain", {"name": "Payments"}, actor="dana")
        trigger = service.create_trigger(
            workflow["id"],
            {"mappings": [{"variable": "ref", "path": "customer_id"}], "signing_value": SECRET},
            source="test/triggers",
        )
        report = deliver(service, trigger, {"customer_id": "CUST_1"}, mode=vocabulary.MODE_TEST)
        assert report["state"] == vocabulary.STATE_ACCEPTED

    def test_a_live_delivery_before_save_is_a_409(self, store):
        service, _clock = build(store)
        workflow = service.create_workflow("room_domain", {"name": "Payments"}, actor="dana")
        trigger = service.create_trigger(
            workflow["id"],
            {"mappings": [{"variable": "ref", "path": "customer_id"}], "signing_value": SECRET},
        )
        service.publish_workflow(workflow["id"], actor="dana")
        with pytest.raises(errors.TriggerNotSaved):
            deliver(service, trigger, {"customer_id": "CUST_1"})
        rejected = service.invocations("room_domain", state=vocabulary.STATE_REJECTED)
        assert rejected["count"] == 1
        assert rejected["invocations"][0]["reason"] == vocabulary.REASON_TRIGGER_NOT_SAVED

    def test_a_live_delivery_into_a_draft_workflow_is_a_409(self, store):
        service, _clock = build(store)
        workflow = service.create_workflow("room_domain", {"name": "Payments"}, actor="dana")
        trigger = service.create_trigger(
            workflow["id"],
            {"mappings": [{"variable": "ref", "path": "customer_id"}], "signing_value": SECRET},
        )
        service.save_trigger(trigger["id"], actor="dana")
        with pytest.raises(errors.WorkflowNotPublished):
            deliver(service, trigger, {"customer_id": "CUST_1"})
        rejected = service.invocations("room_domain", state=vocabulary.STATE_REJECTED)
        assert rejected["invocations"][0]["reason"] == vocabulary.REASON_WORKFLOW_NOT_PUBLISHED

    def test_a_payload_that_matches_no_mapping_is_recorded_as_unmapped(self, store):
        service, _clock, trigger = make_ready(
            store,
            mappings=[
                {
                    "variable": "lead_status",
                    "path": "status",
                    "when": {"path": "status", "operator": "equals", "value": "refunded"},
                }
            ],
        )
        report = deliver(service, trigger, {"status": "pending"})
        assert report["state"] == vocabulary.STATE_UNMAPPED
        assert report["started"] is False
        assert report["bindings"] == []
        assert "did not start" in report["message"]
        assert service.summary("room_domain")["unmapped"] == 1
        assert service.runs("room_domain")["count"] == 0

    def test_a_missing_required_key_is_refused_and_recorded(self, store):
        service, _clock, trigger = make_ready(store)
        with pytest.raises(errors.RequiredMappingUnresolved) as caught:
            deliver(service, trigger, {"amount": 100})
        assert "customer_ref" in str(caught.value)
        rejected = service.invocations("room_domain", state=vocabulary.STATE_REJECTED)
        assert rejected["invocations"][0]["reason"] == (
            vocabulary.REASON_REQUIRED_MAPPING_UNRESOLVED
        )

    def test_an_optional_mapping_does_not_refuse_a_payload(self, store):
        service, _clock, trigger = make_ready(
            store,
            mappings=[
                {"variable": "ref", "path": "customer_id"},
                {"variable": "note", "path": "note", "required": False},
            ],
        )
        report = deliver(service, trigger, {"customer_id": "CUST_1"})
        assert report["state"] == vocabulary.STATE_ACCEPTED
        assert report["matched"] == 1
        assert report["declined"][0]["variable"] == "note"

    def test_an_unsigned_delivery_is_a_401_and_recorded(self, store):
        service, _clock, trigger = make_ready(store)
        with pytest.raises(errors.SignatureMissing):
            service.deliver(
                trigger["token"],
                method="POST",
                payload={"customer_id": "CUST_1"},
                raw_body='{"customer_id":"CUST_1"}',
                body_present=True,
                actor="provider",
                source=SOURCE,
            )
        rejected = service.invocations("room_domain", state=vocabulary.STATE_REJECTED)
        assert rejected["invocations"][0]["reason"] == vocabulary.REASON_SIGNATURE_MISSING

    def test_a_wrong_signature_is_a_401_and_recorded(self, store):
        service, _clock, trigger = make_ready(store)
        with pytest.raises(errors.SignatureMismatch):
            deliver(service, trigger, {"customer_id": "CUST_1"}, secret="another-value")
        rejected = service.invocations("room_domain", state=vocabulary.STATE_REJECTED)
        assert rejected["invocations"][0]["reason"] == vocabulary.REASON_SIGNATURE_MISMATCH

    def test_a_stale_signed_delivery_is_a_401(self, store):
        service, _clock, trigger = make_ready(store)
        stale = NOW - timedelta(hours=1)
        with pytest.raises(errors.SignatureMismatch) as caught:
            deliver(service, trigger, {"customer_id": "CUST_1"}, when=stale)
        assert caught.value.extra["reason"] == vocabulary.REASON_SIGNATURE_STALE

    def test_an_unserved_method_is_a_405_and_recorded(self, store):
        service, _clock = build(store)
        workflow = service.create_workflow("room_domain", {"name": "Payments"}, actor="dana")
        trigger = service.create_trigger(
            workflow["id"],
            {"name": "POST only", "methods": ["POST"], "signing_value": SECRET},
            source="test/triggers",
        )
        service.save_trigger(trigger["id"], actor="dana", source="test/save")
        service.publish_workflow(workflow["id"], actor="dana", source="test/publish")
        with pytest.raises(errors.MethodNotAllowed):
            deliver(service, trigger, {"customer_id": "CUST_1"}, method="PUT")
        rejected = service.invocations("room_domain", state=vocabulary.STATE_REJECTED)
        assert rejected["invocations"][0]["reason"] == "method_not_served"

    def test_a_get_reads_its_payload_from_the_query_string(self, store):
        service, _clock, trigger = make_ready(
            store,
            mappings=[
                {"variable": "ref", "path": "customer_id"},
                {"variable": "amount", "path": "amount", "type": "number"},
            ],
        )
        report = deliver(service, trigger, {"customer_id": "CUST_1", "amount": "100"}, method="GET")
        assert report["state"] == vocabulary.STATE_ACCEPTED
        assert report["payload"]["source"] == vocabulary.METHOD_PAYLOAD_SOURCE_QUERY
        by_variable = {row["variable"]: row["value"] for row in report["bindings"]}
        assert by_variable["amount"] == 100, "a query string carries text, so it must be coerced"

    def test_a_get_that_carries_a_body_is_refused(self, store):
        service, _clock, trigger = make_ready(store)
        body = rules.canonical_json({"customer_id": "CUST_1"})
        with pytest.raises(errors.BodyNotAllowedOnGet):
            service.deliver(
                trigger["token"],
                method="GET",
                payload={"customer_id": "CUST_1"},
                raw_body=body,
                body_present=True,
                signature=signing.sign(SECRET, signing.unix_seconds(NOW), body),
                timestamp=signing.unix_seconds(NOW),
                actor="provider",
                source=feature.hook_source("GET"),
            )
        rejected = service.invocations("room_domain", state=vocabulary.STATE_REJECTED)
        assert rejected["invocations"][0]["reason"] == vocabulary.REASON_BODY_NOT_ALLOWED_ON_GET

    def test_the_test_flag_is_read_from_the_query(self, store):
        service, _clock, trigger = make_ready(store)
        query = {"customer_id": "CUST_1", vocabulary.TEST_MODE_PARAM: "1"}
        body = rules.canonical_json(query)
        report = service.deliver(
            trigger["token"],
            method="GET",
            payload=query,
            raw_body=body,
            query=query,
            signature=signing.sign(SECRET, signing.unix_seconds(NOW), body),
            timestamp=signing.unix_seconds(NOW),
            actor="provider",
            source=feature.hook_source("GET"),
        )
        assert report["mode"] == vocabulary.MODE_TEST
        assert report["started"] is False

    def test_an_unknown_token_is_a_404_that_names_the_fix(self, store):
        service, _clock = build(store)
        with pytest.raises(errors.UnknownEndpoint) as caught:
            deliver(service, {"token": "not-a-token"}, {"a": 1})
        assert "generated" in str(caught.value)

    def test_an_empty_payload_of_any_shape_parses(self, store):
        service, _clock, trigger = make_ready(
            store, mappings=[{"variable": "whole", "path": "0", "required": False}]
        )
        report = deliver(service, trigger, [])
        assert report["payload"]["shape"] == "array"

    def test_many_mappings_all_bind(self, store):
        service, _clock, trigger = make_ready(
            store,
            mappings=[
                {"variable": "ref", "path": "customer_id"},
                {"variable": "amount", "path": "amount", "type": "number"},
                {"variable": "currency", "path": "currency"},
                {"variable": "paid", "path": "status", "type": "boolean"},
            ],
        )
        report = deliver(
            service,
            trigger,
            {"customer_id": "CUST_1", "amount": 100, "currency": "USD", "status": "true"},
        )
        assert report["matched"] == 4
        assert {row["variable"] for row in report["bindings"]} == {
            "ref",
            "amount",
            "currency",
            "paid",
        }

    def test_the_invocation_records_the_actor_and_the_payload_shape(self, store):
        service, _clock, trigger = make_ready(store)
        deliver(service, trigger, {"customer_id": "CUST_1"})
        rows = service.invocations("room_domain")["invocations"]
        assert rows[0]["payload_shape"] == "object"
        assert rows[0]["fingerprint"]
        assert rows[0]["received_at"]

    def test_an_unknown_invocation_is_a_404(self, store):
        service, _clock = build(store)
        with pytest.raises(errors.UnknownInvocation):
            service.invocation("nope")

    def test_an_unknown_run_is_a_404(self, store):
        service, _clock = build(store)
        with pytest.raises(errors.UnknownRun):
            service.run("nope")

    def test_a_run_can_be_read_by_id(self, store):
        service, _clock, trigger = make_ready(store)
        report = deliver(service, trigger, {"customer_id": "CUST_1"})
        assert service.run(report["run"]["id"])["status"] == vocabulary.RUN_STARTED

    def test_the_reads_are_scoped_to_their_room(self, store):
        service, _clock, trigger = make_ready(store)
        deliver(service, trigger, {"customer_id": "CUST_1"})
        assert service.invocations("another_room")["count"] == 0
        assert service.invocations("room_domain")["count"] == 1

    def test_the_invocation_log_filters_by_state_and_mode(self, store):
        service, _clock, trigger = make_ready(store)
        deliver(service, trigger, {"customer_id": "CUST_1"})
        deliver(service, trigger, {"customer_id": "CUST_1"})
        deliver(service, trigger, {"customer_id": "CUST_2"}, mode=vocabulary.MODE_TEST)
        assert service.invocations("room_domain", state=vocabulary.STATE_DUPLICATE)["count"] == 1
        assert service.invocations("room_domain", mode=vocabulary.MODE_TEST)["count"] == 1

    def test_the_summary_reports_every_state(self, store):
        service, _clock, trigger = make_ready(
            store,
            mappings=[
                {
                    "variable": "ref",
                    "path": "customer_id",
                    "when": {"path": "status", "operator": "equals", "value": "ok"},
                }
            ],
        )
        deliver(service, trigger, {"customer_id": "CUST_1", "status": "ok"})
        deliver(service, trigger, {"customer_id": "CUST_1", "status": "ok"})
        deliver(service, trigger, {"customer_id": "CUST_2", "status": "no"})
        summary = service.summary("room_domain")
        assert summary["accepted"] == 1
        assert summary["duplicates"] == 1
        assert summary["unmapped"] == 1
        assert summary["runs"] == 1
        assert summary["saved_triggers"] == 1
        assert summary["published"] == 1
        assert summary["by_state"][vocabulary.STATE_UNMAPPED] == 1
        assert summary["notes"]
        assert summary["premium_trigger_is_a_gate"] is False

    def test_the_summary_over_every_room_is_a_workspace_view(self, store):
        service, _clock, trigger = make_ready(store)
        deliver(service, trigger, {"customer_id": "CUST_1"})
        assert service.summary()["invocations"] == 1


# --------------------------------------------------------------------------- #
# The decision register
# --------------------------------------------------------------------------- #


class TestInferences:
    def test_every_decision_the_issue_left_open_is_recorded(self):
        ids = inferences.known_ids()
        assert len(ids) >= 12
        for required in (
            "the-url-is-unique-per-trigger-not-per-workflow",
            "post-and-put-carry-the-body-get-carries-the-query-string",
            "the-test-request-validates-and-starts-nothing",
            "the-payload-shape-is-unvalidated-except-for-a-required-mapping",
            "every-generated-url-is-signed",
            "the-premium-trigger-is-a-published-fact-not-a-gate",
            "the-services-host-is-not-called",
            "the-domain-package-is-new-not-crm-workflows",
        ):
            assert required in ids, required

    def test_every_decision_names_its_options_and_why_the_others_lost(self):
        for decision in inferences.describe():
            assert decision["chosen"] in decision["options"]
            assert len(decision["options"]) >= 2
            assert decision["rejected_because"]
            assert decision["evidence"]
            assert decision["change_it"]

    def test_a_decision_can_be_read_by_id(self, store):
        service, _clock = build(store)
        assert service.inference("every-generated-url-is-signed")["chosen"]
        assert service.inference("nope") is None
        assert service.known_inference_ids() == inferences.known_ids()

    def test_the_served_vocabulary_reports_the_rule_and_the_register(self, store):
        service, _clock = build(store)
        served = service.vocabulary()
        assert served["ticket"] == "WF-114"
        assert served["signing"]["required"] == "always"
        assert served["signing"]["signature_header"] == signing.SIGNATURE_HEADER
        assert served["inferences"] == inferences.count()
        listed = service.inferences()
        assert listed["count"] == inferences.count()
        assert len(listed["decisions"]) == inferences.count()

    def test_the_crm_workflows_finding_is_recorded_rather_than_skipped(self):
        decision = inferences.by_id("the-domain-package-is-new-not-crm-workflows")
        assert "WorkflowEngine" in decision["evidence"]
        assert "dsr/crm_workflows" in decision["rejected_because"]


# --------------------------------------------------------------------------- #
# The seed, over a plain database
# --------------------------------------------------------------------------- #


class TestSeed:
    def test_the_seed_reports_states_that_are_not_all_successes(self, store):
        summary = feature.seed(store.db, {"room_ids": [], "now": NOW, "rng": None})
        assert summary
        summary.encode("cp1252")
        for state in ("duplicate", "unmapped", "signature_missing", "required_mapping_unresolved"):
            assert state in summary, state

    def test_the_seed_writes_every_ticket_collection(self, db):
        summary = feature.seed(db, {"room_ids": [], "now": NOW, "rng": None})
        assert summary
        for collection in vocabulary.ALL_COLLECTIONS:
            assert db.list(collection, limit=500), f"{collection} was not seeded"

    def test_the_seed_uses_the_documented_payload_example(self, db):
        feature.seed(db, {"room_ids": [], "now": NOW, "rng": None})
        store = RecordStore(db)
        accepts = [
            row
            for row in store.list(vocabulary.COLLECTION_RUN, limit=10)
            if row["data"]["bindings"]
        ]
        assert accepts, "no run bound a variable"
        assert {row["variable"] for row in accepts[0]["data"]["bindings"]} == {
            "customer_ref",
            "amount",
            "currency",
            "lead_status",
        }

    def test_the_seed_attaches_to_a_given_room(self, store):
        store.create("room", {"name": "Given"}, record_id="room_given", actor="seed")
        summary = feature.seed(store.db, {"room_ids": ["room_given"], "now": NOW, "rng": None})
        assert summary
        assert "room(s) created" not in summary
        assert store.find(vocabulary.COLLECTION_WORKFLOW, {}, limit=5)

    def test_the_seed_is_deterministic_for_a_fixed_clock(self, db):
        first = feature.seed(db, {"room_ids": [], "now": NOW, "rng": None})
        second = feature.seed(db, {"room_ids": [], "now": NOW, "rng": None})
        assert first == second


# --------------------------------------------------------------------------- #
# The module is importable by name, as the host finds it
# --------------------------------------------------------------------------- #


def test_the_feature_module_is_discovered_by_name():
    module = importlib.import_module(FEATURE_MODULE)
    assert module.FEATURE["ticket"] == TICKET
    assert module.router.prefix == ROUTER_PREFIX
