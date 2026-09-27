"""Tests for WF-016: sync room events to the CRM via webhooks and automations.

The claims under test come from
``docs/research/digital-sales-room-workflows/wf/WF-016.md``: the published
event enum, the five page statuses sent to a CRM, subscribe / list / cancel, the
When / Do this automation shape, template scoping, the production-only
constraint, the metadata round-trip, and the Activity Log distinguishing success
from errored.

Delivery is driven through a fake transport, so the retry and signing behaviour
is asserted without a socket and without a network flake.

Ported from the branch's ``tests/test_crm.py`` and ``tests/test_crm_api.py``,
which are folded into this one file the way the earlier ports did it. What
changed, and why:

* every URL moved under the feature's own prefix, ``/api/wf-016``. The branch's
  HTTP tests asserted against ``/api/crm/*`` on the shared app, which is the
  surface the port deliberately no longer serves;
* the HTTP fixture points ``DSR_DB_PATH`` at a temporary file the way
  ``test_features.py`` does, and replaces the engine through
  ``app.dependency_overrides`` rather than through ``app.state``. The branch
  built a ``CRMSync`` in the lifespan and hung it on ``app.state.crm``, which
  needed an edit to the shared app and left the tests with no seam;
* every write now passes ``source=``, because the port made it required. The
  branch let hardcoded strings such as ``"subscribe"`` and ``"crm.event.<name>"``
  into the audit log, so an audit row could not be traced back to the request
  that caused it. Three tests were added for that, and one for the finding the
  port brief asks about: the retry loop's per-attempt state was in memory and
  only its status codes were persisted.

The pure-function half of the suite is unchanged from the branch: the published
vocabulary, the field-type lint, and the delivery policy are the researched part
of the workflow, and a port should not quietly change them.
"""

from __future__ import annotations

import json
import random
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dsr.api import app
from dsr.crm import CRMSync
from dsr.crm.automations import AutomationError, lint
from dsr.crm.delivery import (
    BODY_SAMPLE,
    DEFAULT_BACKOFF,
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_TIMEOUT,
    RETRYABLE_STATUS,
    DeliveryResult,
    deliver,
    sign,
)
from dsr.crm.inferences import INFERENCES
from dsr.crm.subscriptions import SubscriptionError
from dsr.crm.sync import FANOUT_NOTE, FieldRegistryError, fanout_source
from dsr.crm.vocabulary import (
    EVENTS,
    PAGE_STATUSES,
    VocabularyError,
    dig,
    lint_mapping,
    require_event,
    require_page_status,
    room_facts,
)
from dsr.db.audited import AuditedDatabase, RecordNotFound
from dsr.features import load_feature
from dsr.store import RecordStore

#: The feature's own prefix. Duplicated here rather than imported so a change to
#: the prefix has to be made deliberately in the test as well, which is the point
#: of a test: a renamed route should fail, not follow silently.
PREFIX = "/api/wf-016"

#: What the pure-domain tests pass as ``source``. Deliberately the exact shape a
#: route passes, so a test asserting on an audit row is asserting on the real
#: thing rather than on a convenient placeholder.
SOURCE = f"POST {PREFIX}/events"


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


def inferences_by_id(inference_id: str):
    return next((entry for entry in INFERENCES if entry["id"] == inference_id), None)


# A room payload with nothing enforced on it but the fields this workflow reads.
ROOM = {
    "name": "Northwind — Enterprise Evaluation",
    "account": "Northwind Traders",
    "owner": "dana",
    "stage": "evaluation",
    "template_id": "tpl_opportunity",
    "links": {
        "live": "https://rooms.example/northwind",
        "collaborator": "https://rooms.example/northwind?collab=1",
    },
    "value": 48000,
    "currency": "USD",
    "close_date": "2026-11-15",
    "view_count": 12,
    "payment": {"status": "paid", "reference": "pi_123", "amount": 48000},
    "metadata": {"opportunity_id": "006NW", "seats": 40},
}


class FakeTransport:
    """Records every call and replays a scripted list of results."""

    def __init__(self, *results: DeliveryResult) -> None:
        self.calls: list[dict] = []
        self.scripted = list(results)

    def post(self, url, body, headers, timeout) -> DeliveryResult:
        self.calls.append(
            {
                "url": url,
                "body": json.loads(body),
                "raw": body,
                "headers": dict(headers),
                "timeout": timeout,
            }
        )
        if self.scripted:
            return self.scripted.pop(0)
        return DeliveryResult(ok=True, status=200, body="ok")


def ok() -> DeliveryResult:
    return DeliveryResult(ok=True, status=200, body="ok")


def rate_limited() -> DeliveryResult:
    return DeliveryResult(ok=False, status=429, error="HTTP 429", retryable=True, retry_after=0)


def server_error() -> DeliveryResult:
    return DeliveryResult(ok=False, status=503, error="HTTP 503", retryable=True)


def not_found() -> DeliveryResult:
    return DeliveryResult(ok=False, status=404, error="HTTP 404", retryable=False)


def engine(store: RecordStore, transport: FakeTransport) -> CRMSync:
    """A :class:`CRMSync` that never sleeps and never opens a socket.

    Retry backoff is asserted through the recorded attempt count, not through
    wall-clock time, so a retry test costs the suite nothing.
    """
    return CRMSync(store, transport=transport, backoff=0, sleep=lambda _s: None)


@pytest.fixture()
def store(tmp_path):
    db = AuditedDatabase(tmp_path / "crm.db", mirror_dir=tmp_path / "mirror")
    yield RecordStore(db)
    db.close()


@pytest.fixture()
def transport():
    return FakeTransport()


@pytest.fixture()
def crm(store, transport):
    return engine(store, transport)


@pytest.fixture()
def room(store):
    return store.create("room", ROOM, actor="dana")


@pytest.fixture()
def http(monkeypatch, transport):
    """A client over a temporary database, with the engine faked at the transport.

    The branch hung its ``CRMSync`` on ``app.state`` in the lifespan, which meant
    editing the shared app and gave the tests nothing to override. The port builds
    one per request from a dependency, so ``dependency_overrides`` is the seam.
    """
    tmp = tempfile.TemporaryDirectory()
    monkeypatch.setenv("DSR_DB_PATH", str(Path(tmp.name) / "wf016.db"))
    monkeypatch.setenv("DSR_AUDIT_DIR", str(Path(tmp.name) / "audit"))
    monkeypatch.setattr("dsr.api.FRONTEND_DIST", Path(tmp.name) / "absent-frontend")
    with TestClient(app) as client:
        app.dependency_overrides[load_feature("wf016_crm_sync").get_crm] = (
            lambda: engine(client.app.state.store, transport)
        )
        try:
            yield client
        finally:
            app.dependency_overrides.clear()
    tmp.cleanup()


@pytest.fixture()
def http_room(http):
    return http.post("/api/records/room", json=ROOM).json()


def subscribe(http, **overrides):
    payload = {"event": "pageAccepted", "target_url": "https://crm.example/hook"}
    payload.update(overrides)
    return http.post(f"{PREFIX}/subscriptions", json=payload)


# --------------------------------------------------------------------------- #
# The plugin registration itself
# --------------------------------------------------------------------------- #


def test_feature_is_discovered_and_mounted_without_editing_the_host(http):
    """The route resolves even though no shared file names this feature."""
    entry = next(
        f for f in http.get("/api/features").json()["features"] if f["id"] == "wf-016-crm-sync"
    )

    assert entry["prefix"] == PREFIX
    assert entry["ticket"] == "WF-016"
    assert entry["exception_handlers"] == ["CrmError"]
    assert len(entry["routes"]) == 16


def test_frontend_descriptor_id_matches_the_backend_feature_id():
    """The two halves of a feature are findable by one name, so they must agree."""
    descriptor = (
        Path(__file__).resolve().parents[2]
        / "frontend"
        / "src"
        / "features"
        / "wf-016-crm-sync"
        / "index.jsx"
    )
    text = descriptor.read_text(encoding="utf-8")
    module = load_feature("wf016_crm_sync")

    assert module.FEATURE["id"] in text
    assert f'id: {module.FEATURE["id"]!r}' in text


def test_feature_module_does_not_import_the_shared_app():
    """A guard exists in test_features.py; this states the reason locally."""
    source = Path(load_feature("wf016_crm_sync").__file__).read_text(encoding="utf-8")
    assert "dsr.api" not in source
    assert "from dsr.deps import" in source


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

    assert len(mine) == 16
    assert not mine & others


# --------------------------------------------------------------------------- #
# Published vocabulary
# --------------------------------------------------------------------------- #


def test_event_enum_is_exactly_the_published_seven():
    assert EVENTS == (
        "pageViewed",
        "pageFirstViewed",
        "pageAccepted",
        "pagePartiallyAccepted",
        "pagePreviewAccepted",
        "pageSetLive",
        "pageRevivedLive",
    )


def test_page_statuses_are_the_published_fixed_set():
    # Reproduced verbatim, spaces included: this list goes over the wire.
    assert PAGE_STATUSES == ("draft", "live", "partially accepted", "accepted", "declined")


def test_unknown_event_is_rejected():
    with pytest.raises(Exception) as excinfo:
        require_event("pageDeleted")
    assert "pageAccepted" in str(excinfo.value)


def test_unknown_page_status_is_rejected():
    with pytest.raises(Exception) as excinfo:
        require_page_status("partially_accepted")
    assert "partially accepted" in str(excinfo.value)


def test_presets_cover_the_three_recommended_automations(crm):
    names = [preset["name"] for preset in crm.presets()]
    assert names == [
        "When a page is accepted by the client, sync page URLs",
        "When a page is accepted by the client, sync payment details",
        "When a page is viewed, sync and update view count",
    ]


# --------------------------------------------------------------------------- #
# Schema-flexible fact projection
# --------------------------------------------------------------------------- #


def test_dig_tolerates_missing_paths():
    assert dig({"a": {"b": 1}}, "a.b") == 1
    assert dig({"a": {}}, "a.b.c", "fallback") == "fallback"
    assert dig({}, "a", None) is None
    assert dig({"a": [{"b": 2}]}, "a.0.b") == 2


def test_room_facts_reads_a_team_s_arbitrary_payload(room):
    facts = room_facts(room["data"], room_id=room["id"])
    assert facts["account"] == "Northwind Traders"
    assert facts["live_url"] == "https://rooms.example/northwind"
    assert facts["collaborator_url"] == "https://rooms.example/northwind?collab=1"
    assert facts["value"] == 48000
    assert facts["metadata"]["opportunity_id"] == "006NW"


def test_room_facts_on_an_empty_payload_is_all_absent_not_an_error():
    facts = room_facts({}, room_id="room_x")
    assert facts["name"] is None
    assert facts["live_url"] is None
    assert facts["metadata"] == {}


def test_a_new_room_field_needs_no_migration_and_still_indexes(store, room):
    # A team adds a field the server has never seen. It stores, and it is
    # filterable through the dynamic index, with no code change.
    store.update(room["id"], {"procurement_contact": "buyer@northwind.example"})

    found = store.find("room", {"procurement_contact": "buyer@northwind.example"})
    assert [r["id"] for r in found] == [room["id"]]


# --------------------------------------------------------------------------- #
# The documented compatibility constraints
# --------------------------------------------------------------------------- #


def _codes(warnings):
    return {warning["code"] for warning in warnings}


def test_lint_flags_currency_code_into_a_currency_field():
    warnings = lint_mapping(
        {"Deal_Currency__c": "currency"},
        {"Deal_Currency__c": "currency"},
        {"currency": "USD"},
    )
    assert "currency_type" in _codes(warnings)


def test_lint_flags_a_url_into_a_text_field():
    warnings = lint_mapping(
        {"Live_Link__c": "live_url"},
        {"Live_Link__c": "text"},
        {"live_url": "https://rooms.example/northwind"},
    )
    assert "url_type" in _codes(warnings)


def test_lint_flags_the_derived_amount_field():
    warnings = lint_mapping(
        {"Amount": "currency"},
        {"Amount": "currency"},
        {"currency": "USD"},
    )
    assert "amount_derived" in _codes(warnings)


def test_lint_flags_a_field_the_integration_user_cannot_see():
    warnings = lint_mapping(
        {"Stage__c": "stage"},
        {"Stage__c": "text"},
        {"stage": "evaluation"},
        hidden_fields=frozenset({"Stage__c"}),
    )
    assert "field_not_visible" in _codes(warnings)


def test_lint_flags_a_fact_the_room_does_not_have():
    warnings = lint_mapping({"Payment_Ref__c": "payment.reference"}, {"Payment_Ref__c": "text"}, {})
    assert "unresolved_fact" in _codes(warnings)


def test_lint_reports_an_unregistered_field_as_info_not_a_failure():
    warnings = lint_mapping({"Anything": "stage"}, {}, {"stage": "evaluation"})
    assert [w["severity"] for w in warnings if w["code"] == "unknown_target_field"] == ["info"]


def test_lint_is_quiet_on_a_correct_mapping():
    warnings = lint_mapping(
        {"Live_Link__c": "live_url", "Deal_Value__c": "value"},
        {"Live_Link__c": "url", "Deal_Value__c": "currency"},
        {"live_url": "https://rooms.example/northwind", "value": 48000},
    )
    assert [w for w in warnings if w["severity"] == "warning"] == []


def test_no_lint_message_names_an_http_path():
    """A message that sends someone to a route the app does not serve is worse
    than no message. The branch's said "register it under /api/crm/fields"."""
    warnings = lint_mapping({"Anything": "stage"}, {}, {"stage": "evaluation"})

    for warning in warnings:
        assert "/api/" not in warning["message"]


def test_automation_lint_surfaces_the_traps_in_place(crm):
    """The warning a rep sees on the card comes from the registered field types."""
    crm.register_field({"name": "Live_Link__c", "type": "text"}, source=SOURCE)
    created = crm.create_automation(
        {
            "name": "Sync URLs",
            "trigger": {"event": "pageAccepted"},
            "actions": [{"kind": "update_fields", "fields": {"Live_Link__c": "live_url"}}],
        },
        source=SOURCE,
    )

    assert "url_type" in _codes(crm.get_automation(created["id"])["warnings"])


def test_automation_lint_warns_about_an_action_kind_with_no_handler():
    warnings = lint({"actions": [{"kind": "sync_line_items"}]}, {})
    assert "unknown_action_kind" in _codes(warnings)


# --------------------------------------------------------------------------- #
# Delivery
# --------------------------------------------------------------------------- #


def test_delivery_signs_exactly_the_bytes_it_sends():
    transport = FakeTransport()
    deliver(
        transport,
        "https://crm.example/hook",
        {"event": "pageAccepted", "n": 1},
        event="pageAccepted",
        delivery_id="d1",
        secret="s3cret",
    )
    sent = transport.calls[0]
    assert sent["headers"]["X-DSR-Signature"] == sign("s3cret", sent["raw"])


def test_delivery_omits_the_signature_without_a_secret():
    transport = FakeTransport()
    deliver(transport, "https://crm.example/hook", {}, event="pageAccepted", delivery_id="d1")
    assert "X-DSR-Signature" not in transport.calls[0]["headers"]


def test_delivery_carries_event_and_delivery_headers():
    transport = FakeTransport()
    deliver(transport, "https://crm.example/hook", {}, event="pageSetLive", delivery_id="d1")
    headers = transport.calls[0]["headers"]
    assert headers["X-DSR-Event"] == "pageSetLive"
    assert headers["X-DSR-Delivery"] == "d1"


def test_delivery_retries_a_rate_limited_endpoint():
    transport = FakeTransport(rate_limited(), rate_limited(), ok())
    report = deliver(transport, "https://crm.example/hook", {}, event="pageViewed", delivery_id="d1")
    assert report.ok is True
    assert report.attempts == 3


def test_delivery_gives_up_after_the_attempt_budget():
    transport = FakeTransport(*[server_error()] * 5)
    report = deliver(transport, "https://crm.example/hook", {}, event="pageViewed", delivery_id="d1")
    assert report.ok is False
    assert report.attempts == 3
    assert report.needs_manual_update is False  # still retryable, not a human problem


def test_delivery_does_not_retry_a_permanent_failure():
    transport = FakeTransport(not_found(), ok())
    report = deliver(transport, "https://crm.example/hook", {}, event="pageViewed", delivery_id="d1")
    assert report.attempts == 1
    assert report.ok is False
    # This is the case the research calls "will need manual updating".
    assert report.needs_manual_update is True


def test_delivery_records_the_status_of_every_attempt():
    transport = FakeTransport(rate_limited(), ok())
    report = deliver(transport, "https://crm.example/hook", {}, event="pageViewed", delivery_id="d1")
    assert [r.status for r in report.history] == [429, 200]


def test_the_report_carries_every_attempt_in_full():
    """The finding this port had to act on.

    The retry loop's per-attempt state lived only in the returned object, and the
    branch persisted just the list of status codes. Two attempts into a rate
    limit, the reason for the retry was gone with the process. The report is now
    the record, so it carries the error, the retry decision and the endpoint's
    own words for each attempt.
    """
    transport = FakeTransport(rate_limited(), not_found())
    report = deliver(
        transport,
        "https://crm.example/hook",
        {},
        event="pageViewed",
        delivery_id="d1",
        secret="s3cret",
    )

    logged = report.to_dict()
    assert logged["attempt_statuses"] == [429, 404]
    assert [entry["attempt"] for entry in logged["attempt_log"]] == [1, 2]
    assert logged["attempt_log"][0]["error"] == "HTTP 429"
    assert logged["attempt_log"][0]["retryable"] is True
    assert logged["attempt_log"][0]["retry_after"] == 0
    assert logged["attempt_log"][1]["ok"] is False
    # The signature is an audit artefact; the secret never is.
    assert logged["request_headers"]["X-DSR-Signature"].startswith("sha256=")
    assert "s3cret" not in json.dumps(logged)


def test_a_chatty_endpoint_cannot_bloat_one_activity_row():
    transport = FakeTransport(DeliveryResult(ok=False, status=500, body="x" * 5000))
    report = deliver(transport, "https://crm.example/hook", {}, event="pageViewed", delivery_id="d1")

    assert len(report.to_dict()["attempt_log"][0]["body"]) == BODY_SAMPLE


# --------------------------------------------------------------------------- #
# Subscriptions
# --------------------------------------------------------------------------- #


def test_subscribe_returns_the_id_you_use_to_cancel(crm):
    subscription = crm.subscribe("pageAccepted", "https://crm.example/hook", source=SOURCE)
    assert subscription["id"].startswith("crm_subscription_")
    assert subscription["data"]["event"] == "pageAccepted"


def test_subscribe_rejects_an_event_outside_the_enum(crm):
    with pytest.raises(VocabularyError):
        crm.subscribe("pageDeleted", "https://crm.example/hook", source=SOURCE)


@pytest.mark.parametrize(
    "target", ["", "not-a-url", "ftp://crm.example/hook", "file:///etc/passwd", "/relative"]
)
def test_subscribe_rejects_a_target_the_server_could_not_safely_fetch(crm, target):
    with pytest.raises(SubscriptionError):
        crm.subscribe("pageAccepted", target, source=SOURCE)


def test_unsubscribe_is_a_soft_delete_so_history_survives(crm, store):
    subscription = crm.subscribe("pageAccepted", "https://crm.example/hook", source=SOURCE)
    crm.unsubscribe(subscription["id"], source=f"DELETE {PREFIX}/subscriptions/{{id}}")

    assert crm.list_subscriptions() == []
    assert store.get(subscription["id"]) is None
    assert store.db.get(subscription["id"], include_deleted=True) is not None


def test_unsubscribing_an_unknown_id_is_a_404_flavoured_error(crm):
    with pytest.raises(RecordNotFound):
        crm.unsubscribe("crm_subscription_missing", source=SOURCE)


def test_a_cancelled_subscription_receives_nothing_more(crm, transport, room):
    subscription = crm.subscribe("pageAccepted", "https://crm.example/hook", source=SOURCE)
    crm.unsubscribe(subscription["id"], source=SOURCE)

    result = crm.record_event("pageAccepted", room_id=room["id"], source=SOURCE)

    assert result["activity"] == []
    assert transport.calls == []


def test_only_the_subscribed_event_is_delivered(crm, transport, room):
    crm.subscribe("pageAccepted", "https://crm.example/hook", source=SOURCE)

    crm.record_event("pageViewed", room_id=room["id"], source=SOURCE)

    assert transport.calls == []


def test_a_room_scoped_subscription_skips_other_rooms(crm, transport, room, store):
    other = store.create("room", {"name": "Contoso"})
    crm.subscribe(
        "pageAccepted", "https://crm.example/hook", room_id=room["id"], source=SOURCE
    )

    crm.record_event("pageAccepted", room_id=other["id"], source=SOURCE)

    assert transport.calls == []


def test_an_unscoped_subscription_receives_every_room(crm, transport, room, store):
    other = store.create("room", {"name": "Contoso"})
    crm.subscribe("pageAccepted", "https://crm.example/hook", source=SOURCE)

    crm.record_event("pageAccepted", room_id=room["id"], source=SOURCE)
    crm.record_event("pageAccepted", room_id=other["id"], source=SOURCE)

    assert len(transport.calls) == 2


def test_the_delivered_payload_carries_the_room_metadata(crm, transport, room):
    crm.subscribe("pageAccepted", "https://crm.example/hook", source=SOURCE)

    crm.record_event("pageAccepted", room_id=room["id"], source=SOURCE)

    sent = transport.calls[0]["body"]
    assert sent["metadata"]["opportunity_id"] == "006NW"
    assert sent["status"] == "accepted"
    assert sent["event"] == "pageAccepted"
    assert sent["room"]["account"] == "Northwind Traders"


def test_event_metadata_is_merged_over_the_room_metadata(crm, transport, room):
    crm.subscribe("pageAccepted", "https://crm.example/hook", source=SOURCE)

    crm.record_event(
        "pageAccepted", room_id=room["id"], metadata={"viewed_by": "procurement"}, source=SOURCE
    )

    sent = transport.calls[0]["body"]
    assert sent["metadata"] == {
        "opportunity_id": "006NW",
        "seats": 40,
        "viewed_by": "procurement",
    }


def test_a_delivery_failure_is_recorded_and_counted(crm, store):
    crm.transport = FakeTransport(not_found())
    subscription = crm.subscribe("pageAccepted", "https://crm.example/hook", source=SOURCE)

    result = crm.record_event("pageAccepted", room_id="room_missing", source=SOURCE)

    entry = result["activity"][0]
    assert entry["data"]["status"] == "error"
    assert entry["data"]["needs_manual_update"] is True
    assert store.get(subscription["id"])["data"]["failures"] == 1


def test_delivery_counters_track_successes(crm, store, room):
    subscription = crm.subscribe("pageAccepted", "https://crm.example/hook", source=SOURCE)

    crm.record_event("pageAccepted", room_id=room["id"], source=SOURCE)
    crm.record_event("pageAccepted", room_id=room["id"], source=SOURCE)

    data = store.get(subscription["id"])["data"]
    assert data["deliveries"] == 2
    assert data["failures"] == 0
    assert data["last_status"] == "success"
    assert data["last_delivered_at"] is not None


# --------------------------------------------------------------------------- #
# Automations
# --------------------------------------------------------------------------- #


def test_create_automation_from_a_preset(crm):
    automation = crm.create_automation(crm.presets()[0] | {"name": "Sync URLs"}, source=SOURCE)

    assert automation["event"] == "pageAccepted"
    assert automation["actions"][0]["fields"]["page_live_url"] == "live_url"
    # A create reads back in the same shape as a get, warnings and all.
    assert "warnings" in automation


def test_automation_needs_a_name(crm):
    with pytest.raises(AutomationError):
        crm.create_automation(
            {"trigger": {"event": "pageAccepted"}, "actions": [{"kind": "update_fields", "fields": {}}]},
            source=SOURCE,
        )


def test_automation_needs_at_least_one_action(crm):
    with pytest.raises(AutomationError):
        crm.create_automation(
            {"name": "Empty", "trigger": {"event": "pageAccepted"}, "actions": []}, source=SOURCE
        )


def test_automation_rejects_an_event_outside_the_enum(crm):
    with pytest.raises(VocabularyError):
        crm.create_automation(
            {
                "name": "Bad trigger",
                "trigger": {"event": "pageDeleted"},
                "actions": [{"kind": "update_fields", "fields": {}}],
            },
            source=SOURCE,
        )


def test_automations_cannot_be_set_up_in_a_sandbox(crm):
    # Documented as a hard product constraint, not a preference.
    with pytest.raises(AutomationError) as excinfo:
        crm.create_automation(
            {
                "name": "Sandbox rule",
                "environment": "sandbox",
                "trigger": {"event": "pageAccepted"},
                "actions": [{"kind": "update_fields", "fields": {}}],
            },
            source=SOURCE,
        )
    assert "sandbox" in str(excinfo.value)


def test_a_patch_cannot_smuggle_in_a_sandbox_environment(crm):
    automation = crm.create_automation(crm.presets()[0], source=SOURCE)
    with pytest.raises(AutomationError):
        crm.update_automation(automation["id"], {"environment": "sandbox"}, source=SOURCE)


def test_toggling_an_automation_off_stops_it_running(crm, store, room):
    automation = crm.create_automation(crm.presets()[0], source=SOURCE)

    crm.update_automation(automation["id"], {"enabled": False}, source=SOURCE)
    result = crm.record_event("pageAccepted", room_id=room["id"], source=SOURCE)

    assert result["activity"] == []
    assert store.get(automation["id"])["data"]["runs"] == 0


def test_an_automation_scoped_to_templates_only_runs_for_those_templates(crm, store, room):
    other = store.create("room", {"name": "Contoso", "template_id": "tpl_renewal"})
    automation = crm.create_automation(crm.presets()[0], source=SOURCE)
    crm.update_automation(
        automation["id"],
        {"trigger": {"event": "pageAccepted", "template_ids": ["tpl_opportunity"]}},
        source=SOURCE,
    )

    skipped = crm.record_event("pageAccepted", room_id=other["id"], source=SOURCE)
    fired = crm.record_event("pageAccepted", room_id=room["id"], source=SOURCE)

    assert skipped["activity"] == []
    assert len(fired["activity"]) == 1


def test_an_automation_with_no_templates_applies_to_every_room(crm, store, room):
    other = store.create("room", {"name": "Contoso"})
    crm.create_automation(crm.presets()[0], source=SOURCE)

    assert len(crm.record_event("pageAccepted", room_id=other["id"], source=SOURCE)["activity"]) == 1


def test_a_run_resolves_the_field_map_against_the_room(crm, room):
    crm.create_automation(crm.presets()[0], source=SOURCE)

    entry = crm.record_event("pageAccepted", room_id=room["id"], source=SOURCE)["activity"][0]

    assert entry["data"]["status"] == "success"
    assert entry["data"]["resolved"] == {
        "page_live_url": "https://rooms.example/northwind",
        "page_collaborator_url": "https://rooms.example/northwind?collab=1",
    }


def test_a_run_that_cannot_resolve_is_an_error_needing_manual_update(crm, store):
    room = store.create("room", {"name": "Unpaid"})
    crm.create_automation(crm.presets()[1], source=SOURCE)  # syncs payment details

    entry = crm.record_event("pageAccepted", room_id=room["id"], source=SOURCE)["activity"][0]

    assert entry["data"]["status"] == "error"
    assert entry["data"]["needs_manual_update"] is True
    assert "payment.status" in entry["data"]["error"]


def test_an_action_kind_with_no_handler_is_skipped_not_silently_dropped(crm, room):
    crm.create_automation(
        {
            "name": "Sync line items",
            "trigger": {"event": "pageAccepted"},
            "actions": [{"kind": "sync_line_items", "fields": {"Qty__c": "view_count"}}],
        },
        source=SOURCE,
    )

    entry = crm.record_event("pageAccepted", room_id=room["id"], source=SOURCE)["activity"][0]

    assert entry["data"]["skipped"] == [
        {"kind": "sync_line_items", "reason": "no handler registered for this action kind"}
    ]
    assert entry["data"]["resolved"] == {}


def test_automation_run_counters_advance(crm, store, room):
    automation = crm.create_automation(crm.presets()[0], source=SOURCE)

    crm.record_event("pageAccepted", room_id=room["id"], source=SOURCE)

    data = store.get(automation["id"])["data"]
    assert data["runs"] == 1
    assert data["errors"] == 0
    assert data["last_run_at"] is not None


# --------------------------------------------------------------------------- #
# The field registry
# --------------------------------------------------------------------------- #


def test_registering_a_field_needs_a_name(crm):
    with pytest.raises(FieldRegistryError):
        crm.register_field({"type": "url"}, source=SOURCE)


def test_registering_a_field_rejects_an_undeclared_type(crm):
    with pytest.raises(FieldRegistryError):
        crm.register_field({"name": "Deal__c", "type": "money"}, source=SOURCE)


def test_a_registered_field_makes_the_lint_specific(crm):
    crm.register_field({"name": "page_live_url", "type": "text"}, source=SOURCE)

    automation = crm.create_automation(
        {
            "name": "Sync the live link",
            "trigger": {"event": "pageAccepted"},
            "actions": [{"kind": "update_fields", "fields": {"page_live_url": "live_url"}}],
        },
        source=SOURCE,
    )

    assert "url_type" in _codes(automation["warnings"])


# --------------------------------------------------------------------------- #
# The fan-out as a whole
# --------------------------------------------------------------------------- #


def test_one_event_reaches_both_paths(crm, transport, room):
    crm.subscribe("pageAccepted", "https://crm.example/hook", source=SOURCE)
    crm.create_automation(crm.presets()[0], source=SOURCE)

    result = crm.record_event("pageAccepted", room_id=room["id"], source=SOURCE)

    assert [entry["data"]["channel"] for entry in result["activity"]] == ["webhook", "automation"]
    assert result["counts"] == {"total": 2, "success": 2, "error": 0}


def test_the_event_is_stored_even_when_every_target_fails(crm, store):
    crm.transport = FakeTransport(not_found())
    crm.subscribe("pageAccepted", "https://crm.example/hook", source=SOURCE)

    crm.record_event("pageAccepted", metadata={"k": "v"}, source=SOURCE)

    stored = store.list("crm_event")
    assert len(stored) == 1
    assert stored[0]["data"]["metadata"] == {"k": "v"}


def test_recording_an_event_is_audited(crm, store, room):
    crm.record_event("pageAccepted", room_id=room["id"], actor="system", source=SOURCE)

    entries = store.audit(collection="crm_event")
    assert len(entries) == 1
    assert entries[0]["action"] == "insert"
    assert entries[0]["actor"] == "system"
    assert entries[0]["room_id"] == room["id"]


def test_every_activity_row_is_audited_too(crm, store, room):
    crm.subscribe("pageAccepted", "https://crm.example/hook", source=SOURCE)
    crm.create_automation(crm.presets()[0], source=SOURCE)

    crm.record_event("pageAccepted", room_id=room["id"], source=SOURCE)

    assert len(store.audit(collection="crm_activity")) == 2


def test_an_event_outside_the_enum_is_refused_before_anything_is_written(crm, store):
    with pytest.raises(Exception):
        crm.record_event("pageDeleted", source=SOURCE)

    assert store.stats()["records"] == 0
    assert store.stats()["audit_entries"] == 0


def test_the_activity_log_filters_by_channel_and_status(crm, store, room):
    crm.subscribe("pageAccepted", "https://crm.example/hook", source=SOURCE)
    crm.create_automation(crm.presets()[0], source=SOURCE)
    crm.record_event("pageAccepted", room_id=room["id"], source=SOURCE)

    assert len(crm.activity(channel="webhook")) == 1
    assert len(crm.activity(channel="automation")) == 1
    assert len(crm.activity(status="success")) == 2
    assert len(crm.activity(status="error")) == 0


def test_the_activity_log_filters_by_room(crm, store, room):
    other = store.create("room", {"name": "Contoso"})
    crm.subscribe("pageViewed", "https://crm.example/hook", source=SOURCE)
    crm.record_event("pageViewed", room_id=room["id"], source=SOURCE)
    crm.record_event("pageViewed", room_id=other["id"], source=SOURCE)

    assert len(crm.activity(room_id=room["id"])) == 1


# --------------------------------------------------------------------------- #
# Vocabulary discovery, over HTTP
# --------------------------------------------------------------------------- #


def test_vocabulary_publishes_the_documented_enums(http):
    body = http.get(f"{PREFIX}/vocabulary").json()

    assert body["events"] == list(EVENTS)
    assert body["page_statuses"] == list(PAGE_STATUSES)
    assert "salesforce" in body["crms"]
    assert body["environments"] == ["production"]


def test_presets_are_listed(http):
    body = http.get(f"{PREFIX}/presets").json()

    assert body["count"] == 3
    assert {preset["id"] for preset in body["presets"]} == {
        "sync-page-urls",
        "sync-payment-details",
        "sync-view-count",
    }


# --------------------------------------------------------------------------- #
# The inferences, declared
# --------------------------------------------------------------------------- #
#
# The Jev gate for this port returned `uncertain` twice and, asked what the
# `fix` option pointed at, named unverified_inferences: behaviour resting on an
# unchecked judgement call, declared only in prose comments. These tests are the
# answer to that. They cannot make an inference sourced, but they make each one
# named, bounded, and overridable, and they fail if one is quietly deleted or
# quietly changed.


def test_every_inference_is_named_and_traceable():
    for entry in INFERENCES:
        assert entry["id"], "an inference with no id cannot be argued with by name"
        assert entry["basis"].strip(), f"{entry['id']} does not say what the research does or does not say"
        assert entry["why"].strip(), f"{entry['id']} does not say why this value was chosen"
        assert entry["change_it"].strip(), f"{entry['id']} does not say how to change it"
        assert entry["blast_radius"].strip(), f"{entry['id']} does not say what it affects"
        assert "value" in entry, f"{entry['id']} does not say what this build chose"
        # No entry may smuggle a route path back in, for the reason the two
        # user-facing messages were rewritten during the port.
        assert "/api/" not in json.dumps(entry)


def test_inference_ids_are_unique():
    ids = [entry["id"] for entry in INFERENCES]
    assert len(ids) == len(set(ids))


def test_the_largest_inference_is_flagged_as_such():
    """The one the research explicitly declines to support, called out by name."""
    entry = inferences_by_id("automation-run-is-resolve-only")

    assert entry is not None
    assert "no claims" in entry["basis"]


def test_inferences_endpoint_serves_the_registry_next_to_the_sourced_half(http):
    body = http.get(f"{PREFIX}/inferences").json()

    assert body["count"] == len(INFERENCES)
    assert {entry["id"] for entry in body["inferences"]} == {e["id"] for e in INFERENCES}
    # The point of the endpoint is the line between the two halves, so the
    # sourced vocabulary ships in the same payload.
    assert body["sourced"]["events"] == list(EVENTS)
    assert body["sourced"]["page_statuses"] == list(PAGE_STATUSES)
    assert "no claims" in body["sourced_quote"]


def test_inferences_endpoint_writes_nothing(http):
    http.get(f"{PREFIX}/inferences")

    assert http.get("/api/audit", params={"limit": 50}).json()["count"] == 0


def test_the_declared_retry_policy_is_the_one_that_runs(crm, store, room):
    """The registry must not drift from the code. If someone changes the retry
    constants, this fails rather than leaving the endpoint quietly lying."""
    declared = inferences_by_id("retry-policy")["value"]

    assert declared["max_attempts"] == DEFAULT_MAX_ATTEMPTS
    assert declared["backoff_seconds"] == DEFAULT_BACKOFF
    assert declared["timeout_seconds"] == DEFAULT_TIMEOUT
    assert declared["retryable_statuses"] == sorted(RETRYABLE_STATUS)
    assert declared["honours_retry_after"] is True

    # And the declared budget is what a real run actually spends.
    crm.transport = FakeTransport(*[server_error()] * 5)
    crm.subscribe("pageAccepted", "https://crm.example/hook", source=SOURCE)
    entry = crm.record_event("pageAccepted", room_id=room["id"], source=SOURCE)["activity"][0]

    assert entry["data"]["attempts"] == DEFAULT_MAX_ATTEMPTS
    assert entry["data"]["attempt_statuses"] == [503] * DEFAULT_MAX_ATTEMPTS


def test_the_declared_signature_matches_what_is_sent(crm, transport, room):
    declared = inferences_by_id("hmac-signature")["value"]
    crm.subscribe(
        "pageAccepted", "https://crm.example/hook", secret="s3cret", source=SOURCE
    )

    crm.record_event("pageAccepted", room_id=room["id"], source=SOURCE)

    sent = transport.calls[0]
    assert sent["headers"][declared["header"]] == sign("s3cret", sent["raw"])
    assert sent["headers"][declared["header"]].startswith("sha256=")
    assert declared["algorithm"] == "HMAC-SHA256"


def test_the_declared_headers_match_what_is_sent(crm, transport, room):
    declared = inferences_by_id("delivery-headers")["value"]
    crm.subscribe("pageAccepted", "https://crm.example/hook", source=SOURCE)

    crm.record_event("pageAccepted", room_id=room["id"], source=SOURCE)

    headers = transport.calls[0]["headers"]
    assert headers["X-DSR-Event"] == "pageAccepted"
    assert headers["X-DSR-Delivery"].count(":") == 1
    assert set(declared) == {"X-DSR-Event", "X-DSR-Delivery"}


def test_the_declared_envelope_matches_what_is_sent(crm, transport, room):
    declared = inferences_by_id("delivery-envelope")["value"]
    crm.subscribe("pageAccepted", "https://crm.example/hook", source=SOURCE)

    crm.record_event("pageAccepted", room_id=room["id"], source=SOURCE)

    assert set(transport.calls[0]["body"]) == set(declared)


def test_the_debatable_status_inference_is_overridable_per_event(crm, transport, room):
    """``pagePreviewAccepted`` -> 'partially accepted' is a judgement call, so it
    has to be changeable without editing the vocabulary."""
    declared = inferences_by_id("page-preview-accepted-status")["value"]
    crm.subscribe("pagePreviewAccepted", "https://crm.example/hook", source=SOURCE)

    crm.record_event("pagePreviewAccepted", room_id=room["id"], source=SOURCE)
    assert transport.calls[0]["body"]["status"] == declared["pagePreviewAccepted"]

    transport.calls.clear()
    crm.record_event(
        "pagePreviewAccepted", room_id=room["id"], status="accepted", source=SOURCE
    )
    assert transport.calls[0]["body"]["status"] == "accepted"


def test_the_room_scope_inference_is_the_documented_one(crm, transport, room, store):
    """Unscoped receives everything, scoped receives only its room. The registry
    claims that, so the two cases are asserted together."""
    declared = inferences_by_id("room-scope-is-a-filter-not-an-only")["value"]
    other = store.create("room", {"name": "Contoso"})

    assert declared["room_id_null"].startswith("receives every")
    crm.subscribe("pageViewed", "https://crm.example/hook", source=SOURCE)
    scoped = crm.subscribe(
        "pageViewed", "https://crm.example/hook", room_id=room["id"], source=SOURCE
    )

    crm.record_event("pageViewed", room_id=room["id"], source=SOURCE)
    crm.record_event("pageViewed", room_id=other["id"], source=SOURCE)

    # Three deliveries: the unscoped one twice, the scoped one only for its room.
    assert len(transport.calls) == 3
    delivered_to_scoped = [
        call for call in transport.calls if call["url"] and scoped["id"] in call["headers"]["X-DSR-Delivery"]
    ]
    assert len(delivered_to_scoped) == 1


def test_the_unresolved_fact_inference_is_the_rule_that_runs(crm, store):
    """A run that resolved nothing is an error, not a success with empty fields."""
    declared = inferences_by_id("unresolved-fact-is-an-error")["value"]
    assert "unresolved_fact" in declared["rule"]

    room = store.create("room", {"name": "Unpaid"})
    crm.create_automation(crm.presets()[1], source=SOURCE)

    entry = crm.record_event("pageAccepted", room_id=room["id"], source=SOURCE)["activity"][0]

    assert entry["data"]["status"] == "error"
    assert entry["data"]["needs_manual_update"] is True


def test_the_summary_inference_is_the_rule_that_runs(http, http_room):
    """The summary covers the rows the filter returned, not the whole log."""
    declared = inferences_by_id("activity-summary-is-scoped")["value"]
    assert "filters returned" in declared["over"]

    subscribe(http, event="pageViewed")
    http.post(f"{PREFIX}/automations", json={"preset_id": "sync-page-urls"})
    # One event the webhook is subscribed to, on a room with links; one the
    # automation fires on, on a room with none, so its run cannot resolve and is
    # the row that needs a human. Two rows in total, across both channels.
    http.post(f"{PREFIX}/events", json={"event": "pageViewed"}, params={"room_id": http_room["id"]})
    bare = http.post("/api/records/room", json={"name": "Unpaid"}).json()
    http.post(
        f"{PREFIX}/events", json={"event": "pageAccepted"}, params={"room_id": bare["id"]}
    )

    everything = http.get(f"{PREFIX}/activity").json()
    only_webhooks = http.get(f"{PREFIX}/activity", params={"channel": "webhook"}).json()

    assert everything["count"] == 2
    assert everything["summary"]["success"] + everything["summary"]["error"] == 2
    assert only_webhooks["count"] == 1
    assert only_webhooks["summary"]["success"] + only_webhooks["summary"]["error"] == 1
    # The automation's run errored on a room with no links, so it is the one row
    # that needs a human, and it is not in the webhook view.
    assert everything["summary"]["needs_manual_update"] == 1
    assert only_webhooks["summary"]["needs_manual_update"] == 0


# --------------------------------------------------------------------------- #
# Subscriptions, over HTTP
# --------------------------------------------------------------------------- #


def test_subscribe_returns_201_and_the_id_to_cancel_with(http):
    response = subscribe(http)

    assert response.status_code == 201
    body = response.json()
    assert body["id"].startswith("crm_subscription_")
    assert body["data"]["event"] == "pageAccepted"


def test_subscribe_accepts_the_researched_target_url_spelling(http):
    assert subscribe(http, target_url=None, targetUrl="https://crm.example/hook").status_code == 201


def test_subscribe_without_a_target_is_400(http):
    response = http.post(f"{PREFIX}/subscriptions", json={"event": "pageAccepted"})

    assert response.status_code == 400
    assert "target_url" in response.json()["detail"]


def test_subscribe_with_an_event_outside_the_enum_is_400(http):
    response = subscribe(http, event="pageDeleted")

    assert response.status_code == 400
    assert response.json()["error"] == "crm_error"


def test_subscribe_with_a_non_http_target_is_400(http):
    assert subscribe(http, target_url="file:///etc/passwd").status_code == 400


def test_listing_subscriptions_never_leaks_the_shared_secret(http):
    subscribe(http, secret="s3cret")

    body = http.get(f"{PREFIX}/subscriptions").json()

    assert body["count"] == 1
    assert body["subscriptions"][0]["signed"] is True
    assert "s3cret" not in body["subscriptions"][0].keys()


def test_cancel_returns_204_and_removes_it_from_the_list(http):
    subscription = subscribe(http).json()

    assert http.delete(f"{PREFIX}/subscriptions/{subscription['id']}").status_code == 204
    assert http.get(f"{PREFIX}/subscriptions").json()["count"] == 0


def test_cancelling_twice_is_404(http):
    subscription = subscribe(http).json()
    http.delete(f"{PREFIX}/subscriptions/{subscription['id']}")

    assert http.delete(f"{PREFIX}/subscriptions/{subscription['id']}").status_code == 404


def test_cancelling_a_never_subscribed_id_is_404(http):
    assert http.delete(f"{PREFIX}/subscriptions/crm_subscription_nope").status_code == 404


# --------------------------------------------------------------------------- #
# Field registry, over HTTP
# --------------------------------------------------------------------------- #


def test_registering_a_crm_field_is_201(http):
    response = http.post(f"{PREFIX}/fields", json={"name": "Live_Link__c", "type": "url"})

    assert response.status_code == 201
    assert response.json()["data"]["type"] == "url"


def test_registering_a_field_with_an_undeclared_type_is_400(http):
    response = http.post(f"{PREFIX}/fields", json={"name": "Deal__c", "type": "money"})

    assert response.status_code == 400


def test_a_registered_field_turns_the_editor_lint_on(http):
    http.post(f"{PREFIX}/fields", json={"name": "page_live_url", "type": "text"})

    automation = http.post(f"{PREFIX}/automations", json={"preset_id": "sync-page-urls"}).json()

    assert "url_type" in {warning["code"] for warning in automation["warnings"]}


def test_listing_fields(http):
    http.post(f"{PREFIX}/fields", json={"name": "Deal_Value__c", "type": "currency"})

    body = http.get(f"{PREFIX}/fields").json()

    assert body["count"] == 1
    assert body["fields"][0]["name"] == "Deal_Value__c"


# --------------------------------------------------------------------------- #
# Automations, over HTTP
# --------------------------------------------------------------------------- #


def test_creating_an_automation_from_a_preset_is_201(http):
    response = http.post(f"{PREFIX}/automations", json={"preset_id": "sync-page-urls"})

    assert response.status_code == 201
    assert response.json()["event"] == "pageAccepted"


def test_creating_an_automation_from_an_explicit_spec(http):
    response = http.post(
        f"{PREFIX}/automations",
        json={
            "name": "Sync view count",
            "trigger": {"event": "pageViewed"},
            "actions": [{"kind": "update_fields", "fields": {"room_view_count": "view_count"}}],
        },
    )

    assert response.status_code == 201
    assert response.json()["actions"][0]["fields"] == {"room_view_count": "view_count"}


def test_a_preset_can_be_renamed_on_creation(http):
    """``{"preset_id": ..., "name": ...}`` renames the preset, not replaces it."""
    body = http.post(
        f"{PREFIX}/automations", json={"preset_id": "sync-page-urls", "name": "Our URL sync"}
    ).json()

    assert body["name"] == "Our URL sync"
    assert body["actions"][0]["fields"] == {
        "page_live_url": "live_url",
        "page_collaborator_url": "collaborator_url",
    }


def test_an_unknown_preset_is_400(http):
    response = http.post(f"{PREFIX}/automations", json={"preset_id": "no-such-preset"})

    assert response.status_code == 400
    # The message must not send anyone to a path the app does not serve.
    assert "/api/" not in response.json()["detail"]


def test_an_automation_in_a_sandbox_is_400(http):
    response = http.post(
        f"{PREFIX}/automations",
        json={
            "name": "Sandbox rule",
            "environment": "sandbox",
            "trigger": {"event": "pageAccepted"},
            "actions": [{"kind": "update_fields", "fields": {}}],
        },
    )

    assert response.status_code == 400
    assert "sandbox" in response.json()["detail"]


def test_an_automation_with_an_unknown_event_is_400(http):
    response = http.post(
        f"{PREFIX}/automations",
        json={
            "name": "Bad trigger",
            "trigger": {"event": "pageDeleted"},
            "actions": [{"kind": "update_fields", "fields": {}}],
        },
    )

    assert response.status_code == 400


def test_toggling_an_automation_off(http):
    automation = http.post(f"{PREFIX}/automations", json={"preset_id": "sync-page-urls"}).json()

    response = http.patch(f"{PREFIX}/automations/{automation['id']}", json={"enabled": False})

    assert response.status_code == 200
    assert response.json()["enabled"] is False


def test_patching_an_unknown_automation_is_404(http):
    response = http.patch(f"{PREFIX}/automations/crm_automation_nope", json={"enabled": False})

    assert response.status_code == 404


def test_getting_an_unknown_automation_is_404(http):
    assert http.get(f"{PREFIX}/automations/crm_automation_nope").status_code == 404


def test_deleting_an_unknown_automation_is_404(http):
    assert http.delete(f"{PREFIX}/automations/crm_automation_nope").status_code == 404


def test_deleting_an_automation_is_a_soft_delete(http):
    automation = http.post(f"{PREFIX}/automations", json={"preset_id": "sync-page-urls"}).json()

    assert http.delete(f"{PREFIX}/automations/{automation['id']}").status_code == 200
    assert http.get(f"{PREFIX}/automations").json()["count"] == 0
    # The rule is gone from the library but its history is still auditable.
    assert http.get("/api/audit", params={"record_id": automation["id"]}).json()["count"] == 2


def test_listing_automations_reports_the_library_state(http):
    http.post(f"{PREFIX}/automations", json={"preset_id": "sync-view-count"})

    body = http.get(f"{PREFIX}/automations").json()

    assert body["count"] == 1
    assert body["automations"][0]["name"] == "When a page is viewed, sync and update view count"
    assert body["automations"][0]["enabled"] is True


# --------------------------------------------------------------------------- #
# Events, over HTTP
# --------------------------------------------------------------------------- #


def test_recording_an_event_needs_an_event(http):
    assert http.post(f"{PREFIX}/events", json={}).status_code == 400


def test_recording_an_event_outside_the_enum_is_400(http, http_room):
    response = http.post(
        f"{PREFIX}/events", json={"event": "pageDeleted"}, params={"room_id": http_room["id"]}
    )

    assert response.status_code == 400
    assert http.get(f"{PREFIX}/events").json()["count"] == 0


def test_recording_an_event_returns_201_and_the_outcome(http, http_room):
    response = http.post(
        f"{PREFIX}/events", json={"event": "pageAccepted"}, params={"room_id": http_room["id"]}
    )

    assert response.status_code == 201
    assert response.json()["counts"] == {"total": 0, "success": 0, "error": 0}


def test_recording_an_event_delivers_to_a_subscriber(http, http_room, transport):
    subscribe(http)

    body = http.post(
        f"{PREFIX}/events", json={"event": "pageAccepted"}, params={"room_id": http_room["id"]}
    ).json()

    assert transport.calls[0]["body"]["metadata"] == {"opportunity_id": "006NW", "seats": 40}
    assert body["activity"][0]["data"]["status"] == "success"


def test_recording_an_event_runs_the_automation(http, http_room):
    http.post(f"{PREFIX}/automations", json={"preset_id": "sync-page-urls"})

    body = http.post(
        f"{PREFIX}/events", json={"event": "pageAccepted"}, params={"room_id": http_room["id"]}
    ).json()

    assert body["activity"][0]["data"]["resolved"] == {
        "page_live_url": "https://rooms.example/northwind",
        "page_collaborator_url": "https://rooms.example/northwind?collab=1",
    }
    assert body["counts"] == {"total": 1, "success": 1, "error": 0}


def test_a_run_against_a_room_missing_the_fact_is_error_needing_manual_update(http):
    """The researched outcome a rep acts on: a run that resolved nothing it was
    asked to resolve is an error, not a success with empty fields."""
    bare = http.post("/api/records/room", json={"name": "Unpaid"}).json()
    http.post(f"{PREFIX}/automations", json={"preset_id": "sync-payment-details"})

    body = http.post(
        f"{PREFIX}/events", json={"event": "pageAccepted"}, params={"room_id": bare["id"]}
    ).json()

    entry = body["activity"][0]["data"]
    assert entry["status"] == "error"
    assert entry["needs_manual_update"] is True
    assert "payment.status" in entry["error"]
    assert body["counts"] == {"total": 1, "success": 0, "error": 1}


def test_the_status_defaults_to_the_documented_value_for_the_event(http, http_room, transport):
    subscribe(http)

    http.post(
        f"{PREFIX}/events", json={"event": "pageAccepted"}, params={"room_id": http_room["id"]}
    )

    assert transport.calls[0]["body"]["status"] == "accepted"


def test_the_status_can_be_overridden_within_the_published_set(http, http_room, transport):
    subscribe(http, event="pageViewed")

    http.post(
        f"{PREFIX}/events",
        json={"event": "pageViewed", "status": "draft"},
        params={"room_id": http_room["id"]},
    )

    assert transport.calls[0]["body"]["status"] == "draft"


def test_a_status_outside_the_published_set_is_400(http, http_room):
    response = http.post(
        f"{PREFIX}/events",
        json={"event": "pageViewed", "status": "signed"},
        params={"room_id": http_room["id"]},
    )

    assert response.status_code == 400


def test_events_can_be_listed_per_room(http, http_room):
    http.post(f"{PREFIX}/events", json={"event": "pageViewed"}, params={"room_id": http_room["id"]})
    http.post(f"{PREFIX}/events", json={"event": "pageViewed"})

    assert http.get(f"{PREFIX}/events", params={"room_id": http_room["id"]}).json()["count"] == 1
    assert http.get(f"{PREFIX}/events").json()["count"] == 2


# --------------------------------------------------------------------------- #
# Activity log, over HTTP
# --------------------------------------------------------------------------- #


def test_the_activity_log_summarises_success_and_error(http, http_room, transport):
    subscribe(http)
    transport.scripted = [not_found()]
    http.post(
        f"{PREFIX}/events", json={"event": "pageAccepted"}, params={"room_id": http_room["id"]}
    )

    body = http.get(f"{PREFIX}/activity").json()

    assert body["summary"] == {"success": 0, "error": 1, "needs_manual_update": 1}
    assert body["entries"][0]["data"]["http_status"] == 404


def test_the_activity_log_summary_covers_only_the_rows_returned(http, http_room):
    subscribe(http)
    http.post(f"{PREFIX}/automations", json={"preset_id": "sync-page-urls"})
    http.post(
        f"{PREFIX}/events", json={"event": "pageAccepted"}, params={"room_id": http_room["id"]}
    )

    webhooks = http.get(f"{PREFIX}/activity", params={"channel": "webhook"}).json()

    assert webhooks["count"] == 1
    assert webhooks["summary"]["success"] == 1
    assert webhooks["summary"]["needs_manual_update"] == 0


def test_the_activity_log_filters_by_channel(http, http_room):
    subscribe(http)
    http.post(f"{PREFIX}/automations", json={"preset_id": "sync-page-urls"})
    http.post(
        f"{PREFIX}/events", json={"event": "pageAccepted"}, params={"room_id": http_room["id"]}
    )

    assert http.get(f"{PREFIX}/activity", params={"channel": "webhook"}).json()["count"] == 1
    assert http.get(f"{PREFIX}/activity", params={"channel": "automation"}).json()["count"] == 1
    assert http.get(f"{PREFIX}/activity", params={"room_id": http_room["id"]}).json()["count"] == 2


def test_an_activity_row_carries_the_request_that_was_sent(http, http_room):
    subscribe(http)
    http.post(
        f"{PREFIX}/events", json={"event": "pageAccepted"}, params={"room_id": http_room["id"]}
    )

    entry = http.get(f"{PREFIX}/activity").json()["entries"][0]

    assert entry["data"]["request"]["room"]["account"] == "Northwind Traders"
    assert entry["data"]["target_url"] == "https://crm.example/hook"


def test_an_activity_row_carries_every_delivery_attempt(http, http_room, transport):
    """End to end: the retry history is in the record, not in the process."""
    subscribe(http)
    transport.scripted = [rate_limited(), ok()]
    http.post(
        f"{PREFIX}/events", json={"event": "pageAccepted"}, params={"room_id": http_room["id"]}
    )

    entry = http.get(f"{PREFIX}/activity").json()["entries"][0]

    assert entry["data"]["attempts"] == 2
    assert entry["data"]["attempt_statuses"] == [429, 200]
    assert [a["error"] for a in entry["data"]["attempt_log"]] == ["HTTP 429", None]
    assert entry["data"]["status"] == "success"


# --------------------------------------------------------------------------- #
# The audit guarantee
# --------------------------------------------------------------------------- #


def test_subscribing_cancelling_and_running_are_all_audited(http, http_room):
    subscription = subscribe(http).json()
    http.post(
        f"{PREFIX}/events", json={"event": "pageAccepted"}, params={"room_id": http_room["id"]}
    )
    http.delete(f"{PREFIX}/subscriptions/{subscription['id']}")

    actions = {
        entry["action"]
        for entry in http.get(
            "/api/audit", params={"collection": "crm_subscription"}
        ).json()["entries"]
    }
    assert actions == {"insert", "update", "delete"}
    assert http.get("/api/audit", params={"collection": "crm_event"}).json()["count"] == 1
    assert http.get("/api/audit", params={"collection": "crm_activity"}).json()["count"] == 1


def test_crm_collections_appear_in_the_schema_discovery_endpoint(http, http_room):
    subscribe(http, event="pageViewed")
    http.post(
        f"{PREFIX}/events", json={"event": "pageViewed"}, params={"room_id": http_room["id"]}
    )

    body = http.get("/api/collections").json()
    names = {entry["collection"] for entry in body["collections"]}

    assert "crm_event" in names
    assert "crm_activity" in names
    # The Activity Log's own fields are discoverable too, which is the point of
    # keeping every collection schema-flexible.
    activity = next(entry for entry in body["collections"] if entry["collection"] == "crm_activity")
    paths = {field["path"] for field in activity["fields"]}
    assert {"channel", "status", "needs_manual_update"} <= paths


def _matches_registered_route(source: str, routes: list[dict]) -> bool:
    """Does ``"POST /api/wf-016/automations/abc"`` name a real route?

    Compared segment by segment, with a ``{parameter}`` segment matching any one
    segment. A fan-out row's source is the route that caused it plus a note, so
    the note is split off first: the route is still the first thing in the string.
    """
    method, _, path = source.partition(" ")
    path = path.split(FANOUT_NOTE)[0]
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


def test_every_write_audit_row_names_a_route_the_app_serves(http, http_room):
    """The port's central guarantee, checked against the live route table.

    The branch let a hardcoded ``"subscribe"`` and ``"crm.event.pageAccepted"``
    into the audit log, and the same class of bug - an audit row naming a path
    the app had stopped serving - has shipped in this codebase before.
    """
    subscription = subscribe(http).json()
    http.post(f"{PREFIX}/fields", json={"name": "Deal_Value__c", "type": "currency"})
    automation = http.post(
        f"{PREFIX}/automations", json={"preset_id": "sync-page-urls"}
    ).json()
    http.patch(f"{PREFIX}/automations/{automation['id']}", json={"description": "Renamed"})
    http.post(
        f"{PREFIX}/events", json={"event": "pageAccepted"}, params={"room_id": http_room["id"]}
    )
    http.delete(f"{PREFIX}/automations/{automation['id']}")
    http.delete(f"{PREFIX}/subscriptions/{subscription['id']}")

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
    assert ours, f"no wf-016 write was audited at all; saw {sorted(sources)}"
    for source in sorted(ours):
        assert _matches_registered_route(source, served), (
            f"audit row names {source!r}, which is not a route this app serves"
        )


def test_a_fan_out_row_names_the_route_that_caused_it(http, http_room):
    """Not just *a* route: the event's. The counters a delivery advances are
    written while serving that request, so that is what has to be on the row."""
    subscribe(http)

    http.post(
        f"{PREFIX}/events", json={"event": "pageAccepted"}, params={"room_id": http_room["id"]}
    )

    entries = http.get("/api/audit", params={"collection": "crm_activity"}).json()["entries"]
    assert entries[0]["source"] == fanout_source(f"POST {PREFIX}/events", "webhook delivery")

    # The subscription's own delivery counter, advanced in the same request, and
    # distinguishable from the row that created the subscription.
    counters = http.get("/api/audit", params={"collection": "crm_subscription"}).json()["entries"]
    by_action = {entry["action"]: entry["source"] for entry in counters}
    assert by_action["insert"] == f"POST {PREFIX}/subscriptions"
    assert by_action["update"] == fanout_source(f"POST {PREFIX}/events", "webhook delivery")


def test_writes_do_not_record_the_pre_port_urls(http, http_room):
    """Explicitly: nothing may still log the branch's ``/api/crm/*`` or its
    internal verbs."""
    subscription = subscribe(http).json()
    http.post(f"{PREFIX}/automations", json={"preset_id": "sync-view-count"})
    http.post(
        f"{PREFIX}/events", json={"event": "pageAccepted"}, params={"room_id": http_room["id"]}
    )
    http.delete(f"{PREFIX}/subscriptions/{subscription['id']}")

    entries = http.get("/api/audit", params={"limit": 200}).json()["entries"]
    sources = {entry["source"] for entry in entries if entry["source"]}
    internal = {"subscribe", "unsubscribe", "delivery", "crm.activity", "automation.create"}

    assert not any("/api/crm/" in source for source in sources)
    assert not sources & internal
    assert all(source.split(" ")[0] in {"POST", "PATCH", "DELETE", "PUT", "GET"} for source in sources)


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #


def _seed_once(tmp_path, room_ids):
    """Run the feature's ``seed`` and read the result back while the db is open.

    Everything is collected inside the ``with``, because the audited database
    closes its connection on the way out and reading it afterwards would be a
    test bug masquerading as a seed bug.
    """
    module = load_feature("wf016_crm_sync")
    now = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)
    with AuditedDatabase(tmp_path / "seed.db", mirror_dir=tmp_path / "audit") as db:
        summary = module.seed(db, {"room_ids": room_ids, "now": now, "rng": random.Random("wf016")})
        return {
            "summary": summary,
            "fields": db.list("crm_field"),
            "automations": db.list("crm_automation"),
            "subscriptions": db.list("crm_subscription"),
            "events": db.list("crm_event"),
            "activity": db.list("crm_activity"),
            "audit": db.audit(limit=200),
        }


def test_seed_fills_the_page_with_both_activity_outcomes(tmp_path):
    """A feature nobody can see in the demo is a feature nobody can review.

    The demo is mixed on purpose: the research says the Activity Log exists so a
    rep can tell "successful" from "errored" and see which rows "will need
    manual updating". A demo of only green would not exercise any of that.
    """
    rooms = [("room_northwind", "Northwind Traders"), ("room_contoso", "Contoso Health")]
    seeded = _seed_once(tmp_path, rooms)
    activity = seeded["activity"]

    assert len(seeded["fields"]) == 5
    assert len(seeded["automations"]) == 2
    assert len(seeded["subscriptions"]) == 3
    assert len(seeded["events"]) == 2
    assert len(activity) == 5

    assert {row["data"]["status"] for row in activity} == {"success", "error"}
    assert {row["data"]["channel"] for row in activity} == {"webhook", "automation"}

    # One delivery was rate limited and accepted on the retry, and both attempts
    # are in the record. That is the finding the port had to act on: the branch
    # kept that history in the request and stored only the status codes.
    retried = [row for row in activity if row["data"].get("attempts", 1) > 1]
    assert len(retried) == 1
    assert retried[0]["data"]["attempt_statuses"] == [429, 200]
    assert retried[0]["data"]["status"] == "success"

    # Two rows need a human, one on each channel, for the two different reasons:
    # a webhook failure no retry can fix, and an automation that could not
    # resolve a fact the room does not have.
    manual = [row for row in activity if row["data"].get("needs_manual_update")]
    assert {row["data"]["channel"] for row in manual} == {"webhook", "automation"}
    failed_webhook = [row for row in manual if row["data"]["channel"] == "webhook"]
    assert [row["data"]["http_status"] for row in failed_webhook] == [404]
    assert [row["data"]["attempts"] for row in failed_webhook] == [1]

    # One subscription is scoped to a room, the other two are not: the research's
    # scope rule is the difference between those rows, so the demo shows it.
    assert sorted(str(row["room_id"]) for row in seeded["subscriptions"]) == sorted(
        [str(None), str(None), rooms[1][0]]
    )

    # Every seeded write is audited, like any other. The fan-out rows name the
    # seed and which channel wrote them, because that is what caused them.
    assert seeded["audit"]
    assert all(entry["source"].startswith("seed") for entry in seeded["audit"])
    assert {entry["source"] for entry in seeded["audit"]} == {
        "seed",
        fanout_source("seed", "webhook delivery"),
        fanout_source("seed", "automation run"),
    }

    assert "5 CRM fields" in seeded["summary"]
    assert "3 subscriptions" in seeded["summary"]
    assert "1 delivery retried" in seeded["summary"]
    assert "1 delivery failed" in seeded["summary"]


def test_seed_opens_no_socket():
    """The seeder must never try to POST to crm.example."""
    module = load_feature("wf016_crm_sync")
    transport = module.DemoTransport()
    headers = {"Content-Type": "application/json"}

    healthy = transport.post(module.DEMO_HEALTHY_TARGET, b"{}", headers, 1.0)
    first_try = transport.post(module.DEMO_RETRYING_TARGET, b"{}", headers, 1.0)
    retry = transport.post(module.DEMO_RETRYING_TARGET, b"{}", headers, 1.0)
    retired = transport.post(module.DEMO_RETIRED_TARGET, b"{}", headers, 1.0)

    assert healthy.ok is True
    assert first_try.retryable is True and retry.ok is True
    # A 404 is the permanent failure: no retry count would ever fix it, which is
    # exactly the "needs manual updating" row the demo is built to contain.
    assert retired.ok is False and retired.retryable is False


def test_seed_survives_a_database_with_no_rooms(tmp_path):
    """The seeder skips a feature loudly rather than aborting, so a seed that
    raises here would print a failure for every fresh database."""
    seeded = _seed_once(tmp_path, [])

    assert len(seeded["fields"]) == 5
    assert len(seeded["automations"]) == 2
    assert seeded["subscriptions"] == []
    assert "no rooms to scope them to" in seeded["summary"]
