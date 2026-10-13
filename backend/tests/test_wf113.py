"""WF-113 domain tests: the pure rules, the engine, the architecture, the seed.

These run against a plain :class:`~dsr.store.RecordStore`, so every assertion is
about this workflow's own rules rather than about a router. The HTTP surface has
its own file.

Every clock is frozen and every timestamp is a literal, because a test that
depends on today's date is a test that fails on a particular day of the week and
looks like a flake.
"""

from __future__ import annotations

import ast
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from dsr.meeting_webhook_fanout import signing as wf066_signing, vocabulary as wf066_vocabulary
from dsr.store import RecordStore
from dsr.webhook_ingest import (
    VALUE_TYPE_JAVASCRIPT,
    VALUE_TYPE_LIQUID,
    AutomationNotStarted,
    BatchLimitExceeded,
    MalformedPayload,
    SignatureMismatch,
    SignatureMissing,
    UnknownAction,
    UnknownAutomation,
    UnknownEndpoint,
    UnknownTrigger,
    WebhookIngest,
    inferences,
    rules,
    signing,
    vocabulary,
)

FIXED = datetime(2026, 3, 4, 12, 0, tzinfo=timezone.utc)
PREFIX = "/api/wf-113"
FEATURE_MODULE = "wf113_ingest_third_party_data_by_webhook_and_fan_it"

ORDER = {
    "id": "ORD-20001",
    "event": "order.placed",
    "placed_at": "2026-03-04T11:42:00Z",
    "customer": {"email": "priya.raman@northwind.example"},
    "group": [{"id": "seat-1"}, {"id": "seat-2"}],
}


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def make_ingest(store, when=FIXED):
    clock = {"t": when}
    return WebhookIngest(store, now=lambda: clock["t"]), clock


def actions_send_event(**overrides):
    action = {
        "key": vocabulary.ACTION_SEND_EVENT,
        "name": "order:placed",
        "mapping": {"order_id": "{{trigger.identifiers.id}}"},
    }
    action.update(overrides)
    return action


def start(store, actions, *, secret=None, identifier_type="id", room="room_a", when=FIXED):
    ingest, clock = make_ingest(store, when)
    created = ingest.create_automation(
        room,
        {"name": "Demo", "identifier_type": identifier_type, "actions": actions, "secret": secret},
        actor="dana",
        source=f"POST {PREFIX}/automations",
    )
    ingest.start_automation(
        created["id"], actor="dana", source=f"POST {PREFIX}/automations/{{automation_id}}/start"
    )
    return ingest, clock, created


def raw(payload):
    return rules.canonical_json(payload).encode("utf-8")


# --------------------------------------------------------------------------- #
# The researched vocabulary
# --------------------------------------------------------------------------- #


def test_the_vocabulary_serves_the_four_actions_and_the_optional_slack_block():
    served = vocabulary.describe()
    keys = [row["key"] for row in served["actions"]]
    assert keys == [
        "send_event",
        "create_or_update_person",
        "batch_update",
        "send_data",
        "slack",
    ]
    slack = next(row for row in served["actions"] if row["key"] == "slack")
    assert slack["optional"] is True


def test_the_vocabulary_serves_both_value_types_with_the_quoted_examples():
    served = vocabulary.describe()
    examples = {row["id"]: row["example"] for row in served["value_types"]}
    assert examples[VALUE_TYPE_LIQUID] == "{{trigger.identifiers.id}}"
    assert examples[VALUE_TYPE_JAVASCRIPT] == "return triggers.identifiers.id;"


def test_the_batch_ceiling_and_the_rate_limits_are_the_quoted_numbers():
    served = vocabulary.describe()
    assert served["batch_limit"] == 1000
    limits = {row["surface"]: row for row in served["rate_limits"]}
    assert limits["pipelines"]["limit"] == 3000
    assert limits["pipelines"]["window_seconds"] == 3
    assert limits["pipelines"]["soft"] is True
    assert limits["app_api"]["limit"] == 10
    assert limits["app_api"]["window_seconds"] == 1
    assert served["rate_limits_enforced"] is False


def test_the_two_semantic_delete_events_are_published():
    assert vocabulary.SEMANTIC_EVENTS == ("Delete Person", "Suppress Person")


def test_the_customer_data_placeholder_is_the_empty_string_not_an_error():
    assert vocabulary.CUSTOMER_DATA_PLACEHOLDER == ""


# --------------------------------------------------------------------------- #
# The pure rules
# --------------------------------------------------------------------------- #


def test_fingerprint_is_stable_and_discriminating():
    assert rules.fingerprint({"a": 1, "b": 2}) == rules.fingerprint({"b": 2, "a": 1})
    assert rules.fingerprint({"a": 1}) != rules.fingerprint({"a": 2})


@pytest.mark.parametrize(
    ("payload", "shape"),
    [
        ({"a": 1}, "object"),
        ([1, 2, 3], "array"),
        ("hello", "scalar"),
        (7, "scalar"),
        (None, "empty"),
    ],
)
def test_a_body_of_any_shape_parses_and_keeps_its_body(payload, shape):
    trigger = rules.trigger_data(
        payload,
        endpoint_ref="ep",
        automation_ref="auto",
        received_at=FIXED.isoformat(),
    )
    assert trigger["shape"] == shape
    assert trigger["body"] == payload


def test_an_array_body_is_wrapped_rather_than_refused():
    trigger = rules.trigger_data(
        [1, 2], endpoint_ref="ep", automation_ref="auto", received_at=FIXED.isoformat()
    )
    assert rules.resolve_expression({"trigger": trigger}, "trigger.items.0") == "1"


def test_identifiers_are_read_from_the_incoming_payload():
    found = rules.resolve_identifiers({"email": "a@b.example", "id": "X-1"})
    assert found["id"] == "X-1"
    assert found["email"] == "a@b.example"


def test_a_reference_to_the_person_resolves_to_the_empty_string():
    trigger = rules.trigger_data(
        {"id": "X-1", "customer": {"email": "a@b.example"}},
        endpoint_ref="ep",
        automation_ref="auto",
        received_at=FIXED.isoformat(),
    )
    context = {"trigger": trigger}
    assert rules.resolve_expression(context, "person.email") == ""
    assert rules.resolve_expression(context, "customer.email") == ""


def test_the_trigger_root_resolves_and_the_plural_root_matches():
    trigger = rules.trigger_data(
        {"id": "X-1"}, endpoint_ref="ep", automation_ref="auto", received_at=FIXED.isoformat()
    )
    context = {"trigger": trigger}
    assert rules.resolve_expression(context, "trigger.identifiers.id") == "X-1"
    assert rules.resolve_expression(context, "triggers.identifiers.id") == "X-1"


def test_the_quoted_liquid_form_renders_the_identifier():
    trigger = rules.trigger_data(
        {"id": "X-1"}, endpoint_ref="ep", automation_ref="auto", received_at=FIXED.isoformat()
    )
    assert rules.render_liquid("{{trigger.identifiers.id}}", {"trigger": trigger}) == "X-1"


def test_the_quoted_javascript_form_renders_the_identifier():
    trigger = rules.trigger_data(
        {"id": "X-1"}, endpoint_ref="ep", automation_ref="auto", received_at=FIXED.isoformat()
    )
    assert rules.render_javascript("return triggers.identifiers.id;", {"trigger": trigger}) == "X-1"


def test_the_date_filter_the_research_names_renders_unix_seconds():
    trigger = rules.trigger_data(
        {"placed_at": "2026-03-04T11:42:00Z"},
        endpoint_ref="ep",
        automation_ref="auto",
        received_at=FIXED.isoformat(),
    )
    rendered = rules.render_liquid(
        "{{trigger.attributes.placed_at | date: %s}}", {"trigger": trigger}
    )
    assert rendered.isdigit()
    assert rendered != ""


def test_a_send_event_with_no_name_is_a_failed_delivery_not_an_exception():
    trigger = rules.trigger_data(
        {"id": "X-1"}, endpoint_ref="ep", automation_ref="auto", received_at=FIXED.isoformat()
    )
    plan = rules.plan_fanout([{"key": "send_event", "name": ""}], trigger)
    assert plan["events"] == []
    assert plan["deliveries"][0]["outcome"] == vocabulary.OUTCOME_FAILED
    assert plan["deliveries"][0]["reason"] == vocabulary.FAIL_EVENT_NAME_MISSING


def test_a_slack_block_with_no_channel_is_a_skipped_delivery():
    trigger = rules.trigger_data(
        {"id": "X-1"}, endpoint_ref="ep", automation_ref="auto", received_at=FIXED.isoformat()
    )
    plan = rules.plan_fanout([{"key": "slack"}], trigger)
    assert plan["deliveries"][0]["outcome"] == vocabulary.OUTCOME_SKIPPED
    assert plan["deliveries"][0]["reason"] == vocabulary.SKIP_NO_CHANNEL


def test_a_batch_over_the_stated_ceiling_raises_rather_than_truncating():
    trigger = rules.trigger_data(
        {"id": "X-1", "group": list(range(vocabulary.BATCH_LIMIT + 1))},
        endpoint_ref="ep",
        automation_ref="auto",
        received_at=FIXED.isoformat(),
    )
    with pytest.raises(BatchLimitExceeded) as excinfo:
        rules.plan_fanout([{"key": "batch_update"}], trigger)
    assert excinfo.value.extra["limit"] == vocabulary.BATCH_LIMIT


def test_a_batch_at_the_ceiling_is_allowed_and_produces_one_event_each():
    members = list(range(vocabulary.BATCH_LIMIT))
    trigger = rules.trigger_data(
        {"id": "X-1", "group": members},
        endpoint_ref="ep",
        automation_ref="auto",
        received_at=FIXED.isoformat(),
    )
    plan = rules.plan_fanout([{"key": "batch_update", "name": "group:updated"}], trigger)
    assert len(plan["events"]) == vocabulary.BATCH_LIMIT
    assert plan["deliveries"][0]["count"] == vocabulary.BATCH_LIMIT


def test_an_unknown_action_raises():
    trigger = rules.trigger_data(
        {"id": "X-1"}, endpoint_ref="ep", automation_ref="auto", received_at=FIXED.isoformat()
    )
    with pytest.raises(UnknownAction):
        rules.plan_fanout([{"key": "delete_person"}], trigger)


def test_a_delete_is_an_event_name_like_any_other():
    trigger = rules.trigger_data(
        {"id": "X-1"}, endpoint_ref="ep", automation_ref="auto", received_at=FIXED.isoformat()
    )
    plan = rules.plan_fanout([{"key": "send_event", "name": "Delete Person"}], trigger)
    assert plan["events"][0]["name"] == "Delete Person"


def test_check_rate_reports_the_published_bound():
    assert rules.check_rate("app_api", 10)["allowed"] is True
    assert rules.check_rate("app_api", 11)["allowed"] is False


# --------------------------------------------------------------------------- #
# The signing rule, pinned to the shipped one
# --------------------------------------------------------------------------- #


def test_the_signing_input_matches_the_wf066_rule():
    """The rule is WF-066's; this workflow does not re-invent it."""
    assert signing.signing_input("1700000000", '{"a":1}') == wf066_signing.signing_input(
        "1700000000", '{"a":1}'
    )


def test_the_signature_digest_matches_wf066():
    assert signing.sign("secret", "1700000000", '{"a":1}') == wf066_signing.sign(
        "secret", "1700000000", '{"a":1}'
    )


def test_the_shared_delivery_outcomes_are_the_wf066_ones():
    assert vocabulary.OUTCOME_DELIVERED == wf066_vocabulary.OUTCOME_DELIVERED
    assert vocabulary.OUTCOME_FAILED == wf066_vocabulary.OUTCOME_FAILED
    assert vocabulary.OUTCOME_SKIPPED == wf066_vocabulary.OUTCOME_SKIPPED


def test_a_fresh_timestamp_verifies():
    body = '{"id":"X-1"}'
    timestamp = signing.unix_seconds(FIXED)
    digest = signing.sign("s3cret", timestamp, body)
    ok, reason = signing.verify("s3cret", body, signature=digest, timestamp=timestamp, now=FIXED)
    assert ok is True and reason == ""


def test_a_delivery_outside_the_window_is_refused_as_stale():
    body = '{"id":"X-1"}'
    old = signing.unix_seconds(FIXED - timedelta(hours=1))
    digest = signing.sign("s3cret", old, body)
    ok, reason = signing.verify("s3cret", body, signature=digest, timestamp=old, now=FIXED)
    assert ok is False
    assert reason == "signature_stale"


# --------------------------------------------------------------------------- #
# The engine
# --------------------------------------------------------------------------- #


def test_a_new_automation_lands_a_draft_with_a_generated_url(store):
    ingest, _clock = make_ingest(store)
    created = ingest.create_automation(
        "room_a",
        {"name": "Order events", "actions": [actions_send_event()]},
        actor="dana",
        source=f"POST {PREFIX}/automations",
    )
    assert created["status"] == vocabulary.STATUS_DRAFT
    assert created["token"]
    assert created["url"] == vocabulary.ENDPOINT_PATH_TEMPLATE.format(token=created["token"])


def test_the_secret_is_never_served_only_whether_one_exists(store):
    ingest, _clock = make_ingest(store)
    created = ingest.create_automation(
        "room_a",
        {"name": "Order events", "actions": [actions_send_event()], "secret": "top-secret"},
        actor="dana",
        source=f"POST {PREFIX}/automations",
    )
    assert created["has_secret"] is True
    assert "top-secret" not in str(created)


def test_an_ingest_into_a_draft_automation_is_refused(store):
    ingest, _clock = make_ingest(store)
    created = ingest.create_automation(
        "room_a", {"name": "Draft", "actions": [actions_send_event()]}, actor="dana", source="s"
    )
    with pytest.raises(AutomationNotStarted):
        ingest.ingest(created["token"], ORDER, raw_body=raw(ORDER), actor="provider", source="s")


def test_an_unknown_token_is_a_404_that_names_the_fix(store):
    ingest, _clock = make_ingest(store)
    with pytest.raises(UnknownEndpoint) as excinfo:
        ingest.ingest("nope", ORDER, raw_body=raw(ORDER), source="s")
    assert "generated" in str(excinfo.value)


def test_an_accepted_ingest_fans_out_events_and_deliveries(store):
    ingest, _clock, created = start(store, [actions_send_event(), {"key": "slack"}])
    report = ingest.ingest(
        created["token"], ORDER, raw_body=raw(ORDER), actor="provider", source="s"
    )
    assert report["state"] == vocabulary.TRIGGER_ACCEPTED
    assert report["events"][0]["data"]["order_id"] == "ORD-20001"
    assert {row["outcome"] for row in report["deliveries"]} == {
        vocabulary.OUTCOME_DELIVERED,
        vocabulary.OUTCOME_SKIPPED,
    }


def test_a_repeated_delivery_is_recorded_as_a_duplicate_and_fans_out_nothing(store):
    ingest, clock, created = start(store, [actions_send_event()])
    ingest.ingest(created["token"], ORDER, raw_body=raw(ORDER), actor="provider", source="s")
    clock["t"] = FIXED + timedelta(seconds=5)
    second = ingest.ingest(
        created["token"], ORDER, raw_body=raw(ORDER), actor="provider", source="s"
    )
    assert second["state"] == vocabulary.TRIGGER_DUPLICATE
    assert second["events"] == []
    assert second["trigger"]["duplicate_of"]
    listed = ingest.triggers("room_a", state=vocabulary.TRIGGER_DUPLICATE)
    assert listed["count"] == 1


def test_an_over_limit_batch_is_recorded_as_rejected_and_refused(store):
    ingest, _clock, created = start(store, [{"key": "batch_update", "name": "group:updated"}])
    oversized = {"id": "ORD-9", "group": list(range(vocabulary.BATCH_LIMIT + 1))}
    with pytest.raises(BatchLimitExceeded):
        ingest.ingest(
            created["token"], oversized, raw_body=raw(oversized), actor="provider", source="s"
        )
    rejected = ingest.triggers("room_a", state=vocabulary.TRIGGER_REJECTED)
    assert rejected["count"] == 1
    assert rejected["triggers"][0]["reason"] == "batch_limit_exceeded"


def test_a_secret_bearing_automation_requires_a_signature(store):
    ingest, _clock, created = start(store, [actions_send_event()], secret="s3cret")
    with pytest.raises(SignatureMissing):
        ingest.ingest(created["token"], ORDER, raw_body=raw(ORDER), actor="provider", source="s")


def test_a_mismatched_signature_is_refused(store):
    ingest, _clock, created = start(store, [actions_send_event()], secret="s3cret")
    with pytest.raises(SignatureMismatch):
        ingest.ingest(
            created["token"],
            ORDER,
            raw_body=raw(ORDER),
            signature="0" * 64,
            timestamp=signing.unix_seconds(FIXED),
            actor="provider",
            source="s",
        )


def test_a_correctly_signed_delivery_is_accepted(store):
    ingest, _clock, created = start(store, [actions_send_event()], secret="s3cret")
    body = raw(ORDER)
    timestamp = signing.unix_seconds(FIXED)
    report = ingest.ingest(
        created["token"],
        ORDER,
        raw_body=body,
        signature=signing.sign("s3cret", timestamp, body.decode()),
        timestamp=timestamp,
        actor="provider",
        source="s",
    )
    assert report["state"] == vocabulary.TRIGGER_ACCEPTED


def test_a_batch_ingest_fans_out_one_event_per_member(store):
    ingest, _clock, created = start(store, [{"key": "batch_update", "name": "group:updated"}])
    report = ingest.ingest(
        created["token"], ORDER, raw_body=raw(ORDER), actor="provider", source="s"
    )
    assert len(report["events"]) == len(ORDER["group"])
    assert report["deliveries"][0]["count"] == len(ORDER["group"])


def test_the_patch_revalidates_the_whole_automation(store):
    ingest, _clock, created = start(store, [actions_send_event()])
    from dsr.webhook_ingest import InvalidAutomation

    with pytest.raises(InvalidAutomation):
        ingest.patch_automation(
            created["id"], {"actions": [{"key": "not_a_real_action"}]}, source="s"
        )


def test_an_unknown_automation_and_trigger_are_404s(store):
    ingest, _clock = make_ingest(store)
    with pytest.raises(UnknownAutomation):
        ingest.automation("auto_nope")
    with pytest.raises(UnknownTrigger):
        ingest.trigger("trigger_nope")


def test_summary_counts_by_state_and_by_outcome(store):
    ingest, clock, created = start(store, [actions_send_event(), {"key": "slack"}])
    ingest.ingest(created["token"], ORDER, raw_body=raw(ORDER), actor="provider", source="s")
    clock["t"] = FIXED + timedelta(seconds=5)
    ingest.ingest(created["token"], ORDER, raw_body=raw(ORDER), actor="provider", source="s")
    summary = ingest.summary("room_a")
    assert summary["accepted"] == 1
    assert summary["duplicates"] == 1
    assert summary["by_outcome"]["skipped"] == 1


def test_the_vocabulary_and_inferences_are_served_as_data(store):
    ingest, _clock = make_ingest(store)
    served = ingest.vocabulary()
    assert served["signing"]["signature_header"] == signing.SIGNATURE_HEADER
    assert served["vocabulary"]["batch_limit"] == 1000
    assert ingest.inferences()["count"] == inferences.count()
    assert ingest.inference("customer-data-resolves-to-the-empty-string") is not None


# --------------------------------------------------------------------------- #
# The seed
# --------------------------------------------------------------------------- #


def test_the_seed_reports_states_that_are_not_all_successes(db):
    from dsr.features import wf113_ingest_third_party_data_by_webhook_and_fan_it as module

    summary = module.seed(db, {"room_ids": [], "now": FIXED, "rng": None})
    assert summary
    assert "duplicate" in summary
    assert "batch_limit_exceeded" in summary
    assert "signature_missing" in summary
    assert "skipped" in summary


def test_every_character_of_the_seed_return_string_encodes_as_cp1252(db):
    from dsr.features import wf113_ingest_third_party_data_by_webhook_and_fan_it as module

    summary = module.seed(db, {"room_ids": [], "now": FIXED, "rng": None})
    summary.encode("cp1252")


def test_the_seed_writes_through_the_audited_store(db):
    from dsr.features import wf113_ingest_third_party_data_by_webhook_and_fan_it as module

    module.seed(db, {"room_ids": [], "now": FIXED, "rng": None})
    store = RecordStore(db)
    for collection in vocabulary.ALL_COLLECTIONS:
        assert store.find(collection, {}, limit=500), f"{collection} was not seeded"
    assert db.audit_count() > 0


def test_the_seed_is_safe_to_run_twice(db):
    from dsr.features import wf113_ingest_third_party_data_by_webhook_and_fan_it as module

    context = {"room_ids": [], "now": FIXED, "rng": None}
    module.seed(db, context)
    second = module.seed(db, context)
    assert second
    second.encode("cp1252")


# --------------------------------------------------------------------------- #
# The architectural guards
# --------------------------------------------------------------------------- #

DOMAIN_PACKAGE = "dsr.webhook_ingest"


def domain_paths():
    root = Path(inferences.__file__).parent
    return sorted(path for path in root.glob("*.py"))


def test_the_domain_package_imports_nothing_but_the_store_and_its_own_modules():
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
                    or name == DOMAIN_PACKAGE
                    or name.startswith(f"{DOMAIN_PACKAGE}.")
                ), f"{path.name} imports {name}"


def test_the_domain_package_never_imports_the_app_or_a_framework():
    for path in domain_paths():
        text = path.read_text(encoding="utf-8")
        assert "from dsr.api" not in text and "import dsr.api" not in text, path.name
        assert "import sqlite3" not in text and "sqlite3.connect" not in text, path.name
        assert "from fastapi" not in text and "import fastapi" not in text, path.name


def test_the_feature_module_never_imports_the_app():
    module_path = Path(__file__).resolve().parents[1] / "dsr" / "features" / f"{FEATURE_MODULE}.py"
    text = module_path.read_text(encoding="utf-8")
    assert "from dsr.api" not in text and "import dsr.api" not in text


def test_the_prefix_is_the_ticket_slug_the_issue_names():
    from dsr.features import wf113_ingest_third_party_data_by_webhook_and_fan_it as module

    assert module.router.prefix == PREFIX
    assert module.FEATURE["ticket"] == "WF-113"
    assert module.FEATURE["id"] == "wf-113-webhook-fanout"


def test_the_feature_owns_no_shared_file():
    shared = {
        "backend/dsr/api.py",
        "backend/dsr/deps.py",
        "backend/dsr/store.py",
        "backend/dsr/db/audited.py",
        "backend/seed.py",
        "frontend/src/App.jsx",
        "frontend/src/main.jsx",
        "frontend/src/lib/api.js",
        "frontend/src/lib/features.js",
        "frontend/src/components/ui.jsx",
        "frontend/vite.config.js",
    }
    root = Path(__file__).resolve().parents[2]
    try:
        changed = subprocess.run(
            ["git", "diff", "--name-only", "origin/main...HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        ).stdout.split()
    except (OSError, subprocess.SubprocessError):
        pytest.skip("git is not available to measure the diff")
    if not changed:
        pytest.skip("no committed diff against origin/main to measure")
    assert not (set(changed) & shared), sorted(set(changed) & shared)


def test_a_malformed_payload_error_exists_for_the_http_layer():
    assert issubclass(MalformedPayload, Exception)
