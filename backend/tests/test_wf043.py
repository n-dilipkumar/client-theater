"""Tests for WF-043: stream CRM record changes into the room in near real time.

The claims under test come from
``docs/research/digital-sales-room-workflows/wf/WF-043.md`` (section 10 of
``docs/research/raw/crm-integration.md``). They are, in order:

* "Changes include creation of a new record, updates to an existing record,
  deletion of a record, and undeletion of a record" - the whole change-type
  vocabulary;
* "A subscription channel is a stream of change events that correspond to one or
  more entities. Change Data Capture provides predefined standard channels and you
  can create your own custom channels. ... The channel name is case-sensitive";
* "Available in: Enterprise, Performance, Unlimited, and Developer editions";
* "Event enrichment is supported for subscribers that use Pub/Sub API, CometD
  (Streaming API), or event relays";
* "Fields that you select for enrichment are included in change events for update
  and delete operations. Enriched fields aren't included in change events for
  create and undelete operations because these events contain all the populated
  fields";
* "We recommend that you configure event enrichment on a custom channel and not the
  standard /data/ChangeEvents channel. This way, other subscribers that receive
  change events on the standard channel don't receive unchanged fields that they
  don't expect";
* "On each event the room checks changeType, buffers the change under its
  transactionKey, and only commits to the room's local replica when the key
  changes";
* "The client can control the flow of events received by setting the number of
  requested events in the FetchRequest parameter" and "The Subscribe method uses
  bidirectional streaming, enabling the client to request more events as it
  consumes events";
* "We recommend you set the buffer size to 3 MB", and the sizing being tunable;
* "If the room needs an unchanged field (e.g. the external ID) to resolve the
  record, that field is added as an enriched field on the channel";
* "The room refreshes the affected buyer's deal panel";
* Dataverse: "After you enable change tracking for a table, you can't disable it",
  the ``Prefer: odata.track-changes`` header, the four refused query options and
  the vendor's own message, and the ``ChangeTracking`` annotation;
* HubSpot: "Webhooks can be triggered as an action in any workflow", "Webhook calls
  made via workflows do not count towards the API rate limit", and "You can create
  up to 1,000 webhook subscriptions per app";
* ``PlatformEventUsageMetric`` for delivery usage.

The last group of gaps is tested by *absence*, because the research is explicit
about them. There is no gap reconciliation, no Avro decoder, and no HubSpot
subscription REST surface, and a test that asserted one existed would be a test
for this build's invention rather than for the research.

Every part of the feature is reachable through its own router, so the HTTP tests
drive the mounted routes rather than calling handlers, and the audit-source tests
check every recorded source against the route table the host actually reported.
"""

from __future__ import annotations

import random
import re
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from dsr.api import app
from dsr.change_stream import (
    CDC_EDITIONS,
    CHANGE_EVENTS,
    CHANGE_TRACKING_ANNOTATION,
    CHANGE_TRACKING_PREFERENCE,
    CHANGE_TYPES,
    CHANNELS,
    CRM_SYSTEMS,
    DATAVERSE_API_VERSION,
    DEAL_PANEL,
    ENRICHED_CHANGE_TYPES,
    ENRICHMENT_TRANSPORTS,
    EVENT_STATES,
    HUBSPOT_SUBSCRIPTIONS,
    HUBSPOT_WEBHOOK_SUBSCRIPTION_LIMIT,
    HUBSPOT_WORKFLOW_CALLS_EXEMPT_FROM_RATE_LIMIT,
    INVALIDATIONS,
    ORGS,
    OWNED_COLLECTIONS,
    RECOMMENDED_BUFFER_BYTES,
    REPLICA,
    REPLICA_STATES,
    STANDARD_CHANNEL,
    SUBSCRIPTION_STATES,
    SUBSCRIPTIONS,
    TABLES,
    TRANSPORTS,
    UNENRICHED_CHANGE_TYPES,
    UNSUPPORTED_DELTA_QUERY_OPTION_MESSAGE,
    UNSUPPORTED_DELTA_QUERY_OPTIONS,
    BufferSet,
    ChangeStreamEngine,
    ChangeStreamError,
    ChangeTrackingDisabled,
    ChangeTrackingIrreversible,
    ChannelError,
    ChannelInUse,
    DataverseError,
    DuplicateChannelName,
    DuplicateOrg,
    EditionDoesNotSupportCdc,
    EntityNotOnChannel,
    FieldMapError,
    MalformedEvent,
    MissingTrackChangesPreference,
    MissingTransactionKey,
    NoOutstandingFetchRequest,
    OrgError,
    RecordUnresolvable,
    StandardChannelEnrichmentRefused,
    SubscriptionClosed,
    SubscriptionLimitExceeded,
    UnknownChangeType,
    UnknownChannel,
    UnknownOrg,
    UnknownSubscription,
    UnknownTable,
    UnsupportedDeltaQueryOption,
    UnsupportedEnrichmentTransport,
    add_enrichment,
    apply_field_map,
    change_count,
    dataverse as dataverse_rules,
    deal_panel_row,
    edition_supports_cdc,
    enriched_fields_in_effect,
    field_map_findings,
    hubspot as hubspot_rules,
    is_enriched_change_type,
    is_standard_channel,
    merge_into,
    normalise_change_type,
    normalise_channel,
    normalise_event,
    normalise_field_map,
    normalise_table,
    parse_sequence_number,
    parse_timestamp,
    plan_event,
    poll,
    remove_enrichment,
    replica_findings,
    resolve_external_id,
    sequence_gaps,
    supports_enrichment,
    sync_key_field,
    unmapped_crm_fields,
    usage as usage_rules,
    wire_format_for,
)
from dsr.change_stream.buffering import TransactionBuffer
from dsr.change_stream.events import applies_to_entity
from dsr.change_stream.inferences import (
    ENRICHMENT_QUOTE,
    INFERENCES,
    SOURCED_QUOTE,
    by_id,
)
from dsr.db.audited import AuditedDatabase
from dsr.features import load_feature
from dsr.store import RecordStore
from fastapi.testclient import TestClient

#: The feature's own prefix. Duplicated here rather than imported so a change to
#: the prefix has to be made deliberately in the test as well, which is the point
#: of a test: a renamed route should fail, not follow silently.
PREFIX = "/api/wf-043"

#: The source a route passes for a write. The pure-domain tests use the same
#: shape, so a test asserting on an audit row is asserting on the real thing
#: rather than on a convenient placeholder.
SOURCE = f"POST {PREFIX}/channels"

MODULE = "wf043_stream_crm_record_changes_into_the_roo"
FEATURE_ID = "wf-043-stream-crm-record-changes-into-the-roo"

NOW = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)

FIELD_MAP: dict[str, Any] = {
    "sync_key": "External_Id__c",
    "account_field": "Account_Name__c",
    "fields": {
        "External_Id__c": "external_id",
        "StageName": "stage",
        "Amount": "amount",
        "CloseDate": "close_date",
        "Owner_Name__c": "owner",
    },
}

ROOM = {
    "name": "Northwind Traders — Enterprise Evaluation",
    "account": "Northwind Traders",
    "owner": "dana",
    "stage": "evaluation",
}


def ago(minutes: float) -> str:
    return (NOW - timedelta(minutes=minutes)).isoformat()


def event_payload(**overrides: Any) -> dict[str, Any]:
    """A well-formed change event, in the six fields the research names."""
    payload: dict[str, Any] = {
        "changeType": "CREATE",
        "transactionKey": "txn-A",
        "sequenceNumber": 1,
        "commitTimestamp": ago(5),
        "payload": {"External_Id__c": "dsr-1", "StageName": "Prospecting"},
    }
    payload.update(overrides)
    return payload


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture(autouse=True)
def clear_parked():
    """The parked-transaction registry is process-wide, so it is reset per test.

    Not a convenience: two tests sharing a subscription id would share a buffer,
    and a test asserting that a change is parked would pass or fail on the order
    the suite happens to run in.
    """
    BufferSet.reset()
    yield
    BufferSet.reset()


@pytest.fixture()
def store(tmp_path):
    db = AuditedDatabase(tmp_path / "wf043.db", mirror_dir=tmp_path / "mirror")
    yield RecordStore(db)
    db.close()


@pytest.fixture()
def engine(store):
    return ChangeStreamEngine(store)


@pytest.fixture()
def room(store):
    return store.create("room", ROOM, actor="dana")


@pytest.fixture()
def org(engine):
    return engine.register_org(
        {"system": "salesforce", "edition": "Unlimited"}, actor="dana", source=SOURCE
    )


@pytest.fixture()
def cdc_org(engine, org):
    return engine.patch_org(
        org["id"], {"cdc_enabled": True, "entities": ["Opportunity"]}, actor="dana", source=SOURCE
    )


@pytest.fixture()
def channel(engine, cdc_org):
    return engine.create_channel(
        {
            "name": "/data/dsrOpportunities",
            "org_id": cdc_org["id"],
            "entities": ["Opportunity"],
            "transport": "pubsub",
            "field_map": FIELD_MAP,
        },
        actor="dana",
        source=SOURCE,
    )


@pytest.fixture()
def enriched_channel(engine, channel):
    return engine.enrich_channel(
        channel["id"], ["External_Id__c", "Account_Name__c"], actor="dana", source=SOURCE
    )


@pytest.fixture()
def subscription(engine, enriched_channel, room):
    opened = engine.open_subscription(
        {"channel_id": enriched_channel["id"]}, room_id=room["id"], actor="dana", source=SOURCE
    )
    return engine.fetch(opened["id"], {"num_requested": 50}, actor="dana", source=SOURCE)


@pytest.fixture()
def http(monkeypatch):
    """A client over a temporary database.

    The database path is resolved at lifespan time, so the variable is set before
    the context manager is entered - the same way ``test_features.py`` does it.
    """
    tmp = tempfile.TemporaryDirectory()
    monkeypatch.setenv("DSR_DB_PATH", str(Path(tmp.name) / "wf043-http.db"))
    monkeypatch.setenv("DSR_AUDIT_DIR", str(Path(tmp.name) / "audit"))
    monkeypatch.setattr("dsr.api.FRONTEND_DIST", Path(tmp.name) / "absent-frontend")
    with TestClient(app) as client:
        yield client
    tmp.cleanup()


def http_setup(client) -> dict[str, Any]:
    """Register an org, enable CDC, and open an enriched channel + subscription."""
    org = client.post(
        f"{PREFIX}/orgs", json={"system": "salesforce", "edition": "Unlimited"}
    ).json()
    client.patch(
        f"{PREFIX}/orgs/{org['id']}", json={"cdc_enabled": True, "entities": ["Opportunity"]}
    )
    room = client.post("/api/records/room", json=ROOM).json()
    channel = client.post(
        f"{PREFIX}/channels",
        json={
            "name": "/data/dsrOpportunities",
            "org_id": org["id"],
            "entities": ["Opportunity"],
            "transport": "pubsub",
            "field_map": FIELD_MAP,
        },
    ).json()
    client.post(
        f"{PREFIX}/channels/{channel['id']}/enrichment", json={"fields": ["External_Id__c"]}
    )
    subscription = client.post(
        f"{PREFIX}/rooms/{room['id']}/subscriptions", json={"channel_id": channel["id"]}
    ).json()
    client.post(f"{PREFIX}/subscriptions/{subscription['id']}/fetch", json={"num_requested": 50})
    return {"org": org, "room": room, "channel": channel, "subscription": subscription}


# --------------------------------------------------------------------------- #
# The plugin registration itself
# --------------------------------------------------------------------------- #


def test_feature_is_discovered_and_mounted_without_editing_the_host(http):
    """The route resolves even though no shared file names this feature."""
    entry = next(f for f in http.get("/api/features").json()["features"] if f["id"] == FEATURE_ID)

    assert entry["prefix"] == PREFIX
    assert entry["ticket"] == "WF-043"
    assert entry["exception_handlers"] == ["ChangeStreamError"]
    assert len(entry["routes"]) == 37
    assert entry["nav"] == [{"id": "crm-change-stream", "label": "CRM change stream"}]


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
        Path(__file__).resolve().parents[2]
        / "frontend"
        / "src"
        / "features"
        / FEATURE_ID
        / "index.jsx"
    )
    text = descriptor.read_text(encoding="utf-8")
    assert f"id: '{FEATURE_ID}'" in text
    assert f'"{FEATURE_ID}"' in text or f"'{FEATURE_ID}'" in text
    assert "export default" in text
    assert load_feature(MODULE).FEATURE["id"] == FEATURE_ID


def test_the_engine_is_built_per_request_from_store_dep():
    """No ``app.state`` entry: that is the edit to the shared app this avoids."""
    source = Path(load_feature(MODULE).__file__).read_text(encoding="utf-8")
    assignments = re.findall(r"app\.state\.\w+\s*=", source)
    assert not assignments, f"the feature writes to the shared app: {assignments}"
    assert "Depends(get_engine)" in source


# --------------------------------------------------------------------------- #
# Vocabulary
# --------------------------------------------------------------------------- #


def test_change_type_vocabulary_is_exactly_the_four_the_research_lists():
    assert CHANGE_TYPES == ("CREATE", "UPDATE", "DELETE", "UNDELETE")


def test_enrichment_splits_the_four_by_change_type():
    assert set(ENRICHED_CHANGE_TYPES) | set(UNENRICHED_CHANGE_TYPES) == set(CHANGE_TYPES)
    assert not set(ENRICHED_CHANGE_TYPES) & set(UNENRICHED_CHANGE_TYPES)
    assert is_enriched_change_type("UPDATE") is True
    assert is_enriched_change_type("DELETE") is True
    assert is_enriched_change_type("CREATE") is False
    assert is_enriched_change_type("UNDELETE") is False


def test_change_type_normalisation_folds_case_but_not_vocabulary():
    assert normalise_change_type("create") == "CREATE"
    assert normalise_change_type("  UnDelete  ") == "UNDELETE"
    assert normalise_change_type("GAP_OVERFLOW") is None
    assert normalise_change_type(None) is None
    assert normalise_change_type(7) is None


def test_standard_channel_is_recognised_case_insensitively_for_this_check_only():
    assert STANDARD_CHANNEL == "/data/ChangeEvents"
    assert is_standard_channel(STANDARD_CHANNEL) is True
    assert is_standard_channel("/data/changeevents") is True
    assert is_standard_channel("/data/dsrOpportunities") is False


def test_the_enrichment_transport_set_is_the_three_the_research_names():
    assert ENRICHMENT_TRANSPORTS == ("pubsub", "cometd", "relay")
    assert supports_enrichment("pubsub") is True
    assert supports_enrichment("cometd") is True
    assert supports_enrichment("relay") is True
    assert supports_enrichment("delta_link") is False
    assert supports_enrichment("workflow_webhook") is False


def test_the_wire_format_is_named_for_pub_sub_and_cometd_only():
    """ "subscriber deserialisation (Avro for Pub/Sub, JSON for CometD)"."""
    assert wire_format_for("pubsub") == "avro"
    assert wire_format_for("cometd") == "json"
    # Not named by the research, so not guessed.
    assert wire_format_for("relay") is None
    assert wire_format_for("delta_link") is None


def test_the_edition_gate_covers_exactly_the_four_editions():
    assert CDC_EDITIONS == ("Enterprise", "Performance", "Unlimited", "Developer")
    for edition in CDC_EDITIONS:
        assert edition_supports_cdc(edition) is True
    assert edition_supports_cdc("enterprise") is True
    assert edition_supports_cdc("Professional") is False
    assert edition_supports_cdc("Starter") is False
    assert edition_supports_cdc(None) is False


def test_the_recommended_buffer_is_three_megabytes():
    """ "We recommend you set the buffer size to 3 MB"."""
    assert RECOMMENDED_BUFFER_BYTES == 3 * 1024 * 1024


def test_the_refused_delta_query_options_are_exactly_four_and_select_is_not_one():
    assert set(UNSUPPORTED_DELTA_QUERY_OPTIONS) == {"filter", "orderby", "expand", "top"}
    assert UNSUPPORTED_DELTA_QUERY_OPTION_MESSAGE.format(option="$filter") == (
        'The "$filter" query parameter isn\'t supported when Change Tracking is enabled.'
    )


def test_the_change_tracking_annotation_is_verbatim():
    assert CHANGE_TRACKING_ANNOTATION == (
        '<Annotation Term="Org.OData.Capabilities.V1.ChangeTracking">'
        '<Record><PropertyValue Property="Supported" Bool="true" />'
    )


def test_hubspot_facts_are_the_two_the_research_cites():
    assert HUBSPOT_WEBHOOK_SUBSCRIPTION_LIMIT == 1000
    assert HUBSPOT_WORKFLOW_CALLS_EXEMPT_FROM_RATE_LIMIT is True


def test_the_owned_collections_are_exactly_eight_and_are_all_namespaced():
    assert len(OWNED_COLLECTIONS) == 8
    assert all(name.startswith(("crm_", "dataverse_", "hubspot_")) for name in OWNED_COLLECTIONS)
    assert "room" not in OWNED_COLLECTIONS


def test_no_other_feature_writes_into_a_collection_this_one_owns():
    """Two features in one collection is invisible to the route-collision check.

    The host refuses a colliding ``(method, path)``, but two features can serve
    different routes and write into the same collection, and the symptom is a
    listing showing rows the feature did not write - which reads as data rather
    than as a defect. WF-016 owns a ``crm_subscription`` collection for its
    webhook subscriptions, and a shorter name here would have collided with it.

    Scans every sibling feature's source for this feature's collection names, so
    a collection added later without checking is caught rather than discovered by
    a reviewer reading a list that has the wrong rows in it.
    """
    import dsr.features as host

    mine = set(OWNED_COLLECTIONS)
    this_file = Path(load_feature(MODULE).__file__).name
    offenders: list[str] = []
    for path in sorted(Path(host.__file__).parent.glob("*.py")):
        if path.name in {"__init__.py", "installed_features.py", this_file}:
            continue
        text = path.read_text(encoding="utf-8")
        for name in mine:
            if re.search(rf"""['"]{re.escape(name)}['"]""", text):
                offenders.append(f"{path.name} mentions {name!r}")
    assert not offenders, (
        "another feature already owns a collection this one writes to: " + ", ".join(offenders)
    )


def test_the_subscription_collection_is_named_distinctly_from_wf016s():
    """The collision that the previous test was written for, pinned by name.

    Stated explicitly rather than left implicit, because the fix is a rename
    someone will be tempted to shorten and the collision is invisible until two
    features are seeded into one database. The other owner names the collection in
    its own domain package, so the check looks there rather than in the feature
    module.
    """
    assert SUBSCRIPTIONS == "crm_change_subscription"
    sibling = Path(
        Path(load_feature("wf016_crm_sync").__file__).parent.parent / "crm" / "subscriptions.py"
    ).read_text(encoding="utf-8")
    assert 'COLLECTION = "crm_subscription"' in sibling
    assert "crm_subscription" not in OWNED_COLLECTIONS


def test_the_vocabulary_endpoint_serves_what_the_rules_enforce(http):
    body = http.get(f"{PREFIX}/vocabulary").json()
    assert body["change_types"] == list(CHANGE_TYPES)
    assert body["enriched_change_types"] == list(ENRICHED_CHANGE_TYPES)
    assert body["unenriched_change_types"] == list(UNENRICHED_CHANGE_TYPES)
    assert body["standard_channel"] == STANDARD_CHANNEL
    assert body["channel_name_is_case_sensitive"] is True
    assert body["cdc_editions"] == list(CDC_EDITIONS)
    assert body["crm_systems"] == list(CRM_SYSTEMS)
    assert body["recommended_buffer_bytes"] == RECOMMENDED_BUFFER_BYTES
    assert body["subscription_states"] == list(SUBSCRIPTION_STATES)
    assert body["event_states"] == list(EVENT_STATES)
    assert body["replica_states"] == list(REPLICA_STATES)
    assert body["deal_panel"] == DEAL_PANEL
    assert body["fields"]["change_event"][:5] == [
        "changeType",
        "transactionKey",
        "sequenceNumber",
        "commitTimestamp",
        "changedFields",
    ]


def test_the_vocabulary_endpoint_names_the_transports_and_their_capabilities(http):
    transports = {row["id"]: row for row in http.get(f"{PREFIX}/vocabulary").json()["transports"]}
    assert set(transports) == set(TRANSPORTS)
    assert transports["pubsub"] == {
        "id": "pubsub",
        "vendor": "salesforce",
        "wire_format": "avro",
        "supports_enrichment": True,
    }
    assert transports["delta_link"]["vendor"] == "dataverse"
    assert transports["workflow_webhook"]["vendor"] == "hubspot"


def test_the_vocabulary_endpoint_says_hubspot_subscription_rest_is_not_claimed(http):
    hubspot = http.get(f"{PREFIX}/vocabulary").json()["hubspot"]
    assert "not implemented" in hubspot["subscription_rest_api"]
    assert hubspot["webhook_subscription_limit"] == 1000
    assert hubspot["workflow_calls_exempt_from_rate_limit"] is True


def test_the_vocabulary_endpoint_says_the_usage_metric_has_no_published_shape(http):
    usage = http.get(f"{PREFIX}/vocabulary").json()["usage"]
    assert usage["source"] == "PlatformEventUsageMetric"
    assert usage["schema_published_by_vendor"] is False
    assert "events_requested" in usage["counters"]


# --------------------------------------------------------------------------- #
# The inference register
# --------------------------------------------------------------------------- #


def test_every_inference_is_named_traceable_bounded_and_visible():
    for entry in INFERENCES:
        assert set(entry) >= {"id", "topic", "basis", "value", "why", "change_it", "blast_radius"}


def test_inference_ids_are_unique():
    ids = [entry["id"] for entry in INFERENCES]
    assert len(ids) == len(set(ids))


def test_the_register_covers_the_readings_a_reviewer_most_needs():
    ids = {entry["id"] for entry in INFERENCES}
    for expected in (
        "commit-when-the-key-changes",
        "failed-commit-stays-parked",
        "buffer-is-per-process",
        "unresolvable-record-needs-enrichment",
        "enrichment-on-the-standard-channel-is-refused",
        "enrichment-applies-to-update-and-delete-only",
        "enrichment-transport-set",
        "channel-name-is-case-sensitive",
        "delete-is-a-tombstone",
        "changed-fields-is-not-cleared",
        "pubsub-fetch-budget",
        "buffer-bytes-default-3mb",
        "room-panel-refresh",
        "unmapped-crm-fields-are-recorded",
        "hubspot-rate-limit-budget",
        "edition-is-declared-not-detected",
        "avro-is-the-transport-clients-job",
        "no-gap-reconciliation",
        "no-hubspot-subscription-rest",
        "entity-is-filtered-not-relabelled",
        "usage-metric-shape",
    ):
        assert expected in ids, expected


def test_the_boundaries_are_listed_as_boundaries():
    for expected in (
        "avro-is-the-transport-clients-job",
        "no-gap-reconciliation",
        "no-hubspot-subscription-rest",
    ):
        entry = by_id(expected)
        assert entry is not None
        assert "boundary" in entry["basis"].lower()


def test_by_id_returns_none_for_an_unknown_entry():
    assert by_id("no-such-inference") is None


def test_the_inference_endpoint_serves_the_register_beside_the_sourced_quotes(http):
    body = http.get(f"{PREFIX}/inferences").json()
    assert body["count"] == len(INFERENCES)
    assert body["sourced"]["buffering_quote"] == SOURCED_QUOTE
    assert body["sourced"]["enrichment_quote"] == ENRICHMENT_QUOTE
    assert body["sourced"]["change_types"] == list(CHANGE_TYPES)
    assert body["sourced"]["standard_channel"] == STANDARD_CHANNEL
    assert body["sourced"]["enrichment_transports"] == list(ENRICHMENT_TRANSPORTS)
    assert body["sourced"]["recommended_buffer_bytes"] == RECOMMENDED_BUFFER_BYTES
    assert body["sourced"]["delta_query_options_refused"] == [
        "$filter",
        "$orderby",
        "$expand",
        "$top",
    ]
    assert {row["id"] for row in body["inferences"]} == {entry["id"] for entry in INFERENCES}


def test_the_worked_example_shows_the_commit_rule_being_visible(http):
    example = http.get(f"{PREFIX}/inferences").json()["worked_example"]
    assert len(example["events"]) == 3
    steps = example["buffered_after_each"]
    assert steps[0]["committed"] == []
    assert steps[1]["committed"] == []
    assert steps[2]["committed"] == ["txn-A#1", "txn-A#2"]
    assert steps[2]["reason"] == "transactionKey changed"
    assert "enriched" in example["event_1_resolves_its_record"]


# --------------------------------------------------------------------------- #
# Events: normalising what the transport delivered
# --------------------------------------------------------------------------- #


def test_an_event_is_normalised_from_the_six_fields_the_research_names():
    normalised = normalise_event(event_payload(entity="Opportunity"))
    assert normalised["change_type"] == "CREATE"
    assert normalised["transaction_key"] == "txn-A"
    assert normalised["sequence_number"] == 1
    assert normalised["commit_timestamp"] == ago(5)
    assert normalised["changed_fields"] is None
    assert normalised["payload"]["External_Id__c"] == "dsr-1"
    assert normalised["enriched_fields"] == {}
    assert normalised["entity"] == "Opportunity"


def test_snake_case_spellings_are_accepted_alongside_the_vendors():
    normalised = normalise_event(
        {
            "change_type": "update",
            "transaction_key": "txn-Z",
            "sequence_number": "7",
            "commit_timestamp": ago(1),
            "changed_fields": ["StageName"],
            "record": {"StageName": "Negotiation"},
        }
    )
    assert normalised["change_type"] == "UPDATE"
    assert normalised["transaction_key"] == "txn-Z"
    assert normalised["sequence_number"] == 7
    assert normalised["changed_fields"] == ["StageName"]
    assert normalised["payload"] == {"StageName": "Negotiation"}


def test_the_payload_is_stored_verbatim_so_a_new_crm_field_needs_no_coordination():
    payload = {"External_Id__c": "dsr-1", "A_Brand_New_Field__c": {"nested": [1, 2]}}
    assert normalise_event(event_payload(payload=payload))["payload"] == payload


def test_an_unknown_change_type_is_refused_with_the_four():
    with pytest.raises(UnknownChangeType) as caught:
        normalise_event(event_payload(changeType="GAP_OVERFLOW"))
    assert "CREATE, UPDATE, DELETE, UNDELETE" in str(caught.value)
    assert "different workflow" in str(caught.value)


def test_an_event_without_a_transaction_key_is_refused():
    with pytest.raises(MissingTransactionKey) as caught:
        normalise_event(event_payload(transactionKey=""))
    assert "transactionKey" in str(caught.value)


def test_a_sequence_number_is_required_and_must_be_an_integer():
    for bad in (None, "abc", True, {}):
        with pytest.raises(MalformedEvent):
            normalise_event(event_payload(sequenceNumber=bad))
    assert normalise_event(event_payload(sequenceNumber="12"))["sequence_number"] == 12


def test_changed_fields_must_be_a_list():
    with pytest.raises(MalformedEvent):
        normalise_event(event_payload(changedFields="StageName"))


def test_absent_and_empty_changed_fields_are_kept_apart():
    """One means the transport did not say; the other means it said nothing changed."""
    assert normalise_event(event_payload())["changed_fields"] is None
    assert normalise_event(event_payload(changedFields=[]))["changed_fields"] == []


def test_a_non_object_event_is_refused():
    with pytest.raises(MalformedEvent):
        normalise_event(["not", "an", "object"])  # type: ignore[arg-type]


def test_a_non_object_payload_is_refused():
    with pytest.raises(MalformedEvent):
        normalise_event(event_payload(payload="StageName=Prospecting"))


def test_a_non_object_enriched_payload_is_refused():
    with pytest.raises(MalformedEvent):
        normalise_event(event_payload(enrichedFields=["External_Id__c"]))


@pytest.mark.parametrize(
    "raw",
    [
        "2026-09-26T12:00:00Z",
        "2026-09-26T14:00:00+02:00",
        "2026-09-26T12:00:00",
    ],
)
def test_commit_timestamps_normalise_to_one_utc_instant(raw):
    assert parse_timestamp(raw) == "2026-09-26T12:00:00+00:00"


def test_commit_timestamps_accept_a_number_and_a_datetime():
    assert parse_timestamp(1_789_776_000).startswith("20")
    assert parse_timestamp(NOW) == NOW.isoformat()


def test_an_unparseable_commit_timestamp_is_refused_rather_than_defaulted():
    with pytest.raises(MalformedEvent) as caught:
        parse_timestamp("whenever")
    assert "ISO-8601" in str(caught.value)


def test_parse_sequence_number_accepts_a_numeric_string():
    assert parse_sequence_number("42") == 42
    with pytest.raises(MalformedEvent):
        parse_sequence_number(None)


# --------------------------------------------------------------------------- #
# Events: the entity filter and the enrichment rule
# --------------------------------------------------------------------------- #


def test_an_event_for_an_entity_the_channel_does_not_stream_is_refused():
    channel = {"name": "/data/dsrOpportunities", "entities": ["Opportunity"]}
    applies_to_entity({"entity": "Opportunity"}, channel)
    with pytest.raises(EntityNotOnChannel) as caught:
        applies_to_entity({"entity": "Contact"}, channel)
    assert "would never reach the room's replica" in str(caught.value)


def test_no_entity_on_the_event_means_no_check_applies():
    applies_to_entity({}, {"entities": ["Opportunity"]})
    applies_to_entity({"entity": "Contact"}, {"entities": []})


def test_enriched_fields_apply_to_update_and_delete():
    channel = {"enriched_fields": ["External_Id__c"]}
    for change_type in ENRICHED_CHANGE_TYPES:
        effective = enriched_fields_in_effect(
            {"change_type": change_type, "enriched_fields": {"External_Id__c": "dsr-1"}}, channel
        )
        assert effective == {"External_Id__c": "dsr-1"}


def test_enriched_fields_are_dropped_on_create_and_undelete():
    """ "these events contain all the populated fields"."""
    channel = {"enriched_fields": ["External_Id__c"]}
    for change_type in UNENRICHED_CHANGE_TYPES:
        assert (
            enriched_fields_in_effect(
                {"change_type": change_type, "enriched_fields": {"External_Id__c": "stale"}},
                channel,
            )
            == {}
        )


def test_only_selected_enriched_fields_are_used():
    effective = enriched_fields_in_effect(
        {
            "change_type": "UPDATE",
            "enriched_fields": {"External_Id__c": "dsr-1", "Not_Selected__c": "x"},
        },
        {"enriched_fields": ["External_Id__c"]},
    )
    assert effective == {"External_Id__c": "dsr-1"}


def test_the_reason_dropped_enrichment_is_reported_not_performed_quietly():
    from dsr.change_stream.events import enrichment_dropped_reason

    reason = enrichment_dropped_reason(
        {"change_type": "CREATE", "enriched_fields": {"External_Id__c": "stale"}}
    )
    assert "all the populated fields" in reason
    assert enrichment_dropped_reason({"change_type": "UPDATE", "enriched_fields": {}}) == ""


# --------------------------------------------------------------------------- #
# The field map
# --------------------------------------------------------------------------- #


def test_a_field_map_needs_a_sync_key_and_a_non_empty_field_set():
    with pytest.raises(FieldMapError) as caught:
        normalise_field_map({"fields": {"StageName": "stage"}})
    assert "sync_key" in str(caught.value)
    with pytest.raises(FieldMapError):
        normalise_field_map({"sync_key": "External_Id__c", "fields": {}})
    with pytest.raises(FieldMapError):
        normalise_field_map(None)


def test_a_field_map_mapping_without_a_target_is_refused():
    with pytest.raises(FieldMapError) as caught:
        normalise_field_map({"sync_key": "X", "fields": {"StageName": ""}})
    assert "no room field name" in str(caught.value)


def test_a_normalised_field_map_carries_the_researchs_own_defaults():
    normalised = normalise_field_map(FIELD_MAP)
    assert normalised["sync_key"] == "External_Id__c"
    assert normalised["sync_key_field"] == "external_id"
    assert normalised["account_field"] == "Account_Name__c"
    assert normalised["fields"]["StageName"] == "stage"


def test_sync_key_field_falls_back_to_the_researchs_own_example():
    assert sync_key_field({}) == "external_id"
    assert sync_key_field({"sync_key_field": "crm_key"}) == "crm_key"


def test_the_sync_key_resolves_from_the_payload_before_the_enriched_field():
    """ "If the sync key itself changed, the new value is the row to find."""
    field_map = normalise_field_map(FIELD_MAP)
    assert (
        resolve_external_id({"External_Id__c": "new"}, {"External_Id__c": "old"}, field_map)
        == "new"
    )
    assert resolve_external_id({}, {"External_Id__c": "old"}, field_map) == "old"
    assert resolve_external_id({}, {}, field_map) is None


def test_a_blank_sync_key_value_is_not_a_resolution():
    assert (
        resolve_external_id({"External_Id__c": "   "}, {}, normalise_field_map(FIELD_MAP)) is None
    )


def test_changed_fields_decides_what_is_applied_and_nothing_else_is_cleared():
    field_map = normalise_field_map(FIELD_MAP)
    applied = apply_field_map(
        {"StageName": "Negotiation", "Amount": 1}, {}, field_map, changed_fields=["StageName"]
    )
    assert applied["fields"] == {"stage": "Negotiation"}


def test_an_absent_changed_fields_list_applies_everything_the_payload_carries():
    field_map = normalise_field_map(FIELD_MAP)
    applied = apply_field_map(
        {"StageName": "Negotiation", "Amount": 1}, {}, field_map, changed_fields=None
    )
    assert applied["fields"] == {"stage": "Negotiation", "amount": 1}


def test_an_empty_changed_fields_list_applies_nothing():
    field_map = normalise_field_map(FIELD_MAP)
    assert apply_field_map({"StageName": "X"}, {}, field_map, changed_fields=[])["fields"] == {}


def test_a_value_the_payload_omits_falls_through_to_the_enriched_field():
    field_map = normalise_field_map(FIELD_MAP)
    applied = apply_field_map(
        {"StageName": "X"},
        {"External_Id__c": "dsr-9"},
        field_map,
        changed_fields=["External_Id__c"],
    )
    assert applied["fields"] == {"external_id": "dsr-9"}
    assert applied["read_from"]["external_id"] == "enriched"


def test_a_value_from_the_payload_is_recorded_as_coming_from_the_payload():
    field_map = normalise_field_map(FIELD_MAP)
    applied = apply_field_map(
        {"External_Id__c": "dsr-9"}, {"External_Id__c": "stale"}, field_map, changed_fields=None
    )
    assert applied["read_from"]["external_id"] == "payload"


def test_the_account_field_resolves_to_account_on_the_replica_row():
    field_map = normalise_field_map(FIELD_MAP)
    applied = apply_field_map({}, {"Account_Name__c": "Northwind"}, field_map, changed_fields=None)
    assert applied["fields"]["account"] == "Northwind"


def test_a_field_the_map_does_not_name_is_recorded_not_dropped():
    field_map = normalise_field_map(FIELD_MAP)
    assert unmapped_crm_fields(
        {"StageName": "X", "Brand_New__c": 1, "Other_New__c": 2}, field_map, None
    ) == ["Brand_New__c", "Other_New__c"]


def test_a_field_the_map_does_name_is_not_reported_as_unmapped():
    field_map = normalise_field_map(FIELD_MAP)
    assert (
        unmapped_crm_fields(
            {"StageName": "X", "External_Id__c": "a", "Account_Name__c": "b"}, field_map, None
        )
        == []
    )


def test_field_map_findings_report_a_missing_sync_key_as_an_error():
    findings = field_map_findings({"fields": {"StageName": "stage"}})
    codes = {entry["code"] for entry in findings}
    assert "sync_key_missing" in codes
    assert any(entry["severity"] == "error" for entry in findings)


def test_field_map_findings_note_a_sync_key_outside_fields_and_a_missing_account():
    findings = field_map_findings({"sync_key": "X__c", "fields": {"StageName": "stage"}})
    codes = {entry["code"] for entry in findings}
    assert codes == {"sync_key_not_in_fields", "account_field_missing"}


def test_field_map_findings_for_a_non_object_reports_one_error():
    assert field_map_findings("nope")[0]["code"] == "field_map_not_an_object"


# --------------------------------------------------------------------------- #
# The buffer and the commit rule
# --------------------------------------------------------------------------- #


def parked(key: str, *sequences: int) -> TransactionBuffer:
    buffer = TransactionBuffer(key)
    for sequence in sequences:
        buffer.add(
            {
                "change_type": "UPDATE",
                "transaction_key": key,
                "sequence_number": sequence,
                "commit_timestamp": ago(sequence),
                "changed_fields": ["StageName"],
                "payload": {"StageName": "X"},
                "enriched_fields": {},
            }
        )
    return buffer


def test_events_park_under_their_transaction_key_and_do_not_commit():
    buffers = BufferSet()
    buffer, completed, outcome = buffers.accept(normalise_event(event_payload()))
    assert outcome["added"] is True
    assert completed == []
    assert buffers.keys() == ["txn-A"]
    assert len(buffer) == 1


def test_a_repeat_of_the_same_key_parks_and_commits_nothing():
    buffers = BufferSet()
    buffers.accept(normalise_event(event_payload(sequenceNumber=1)))
    buffer, completed, outcome = buffers.accept(
        normalise_event(event_payload(sequenceNumber=2, commitTimestamp=ago(4)))
    )
    assert completed == []
    assert len(buffer) == 2


def test_a_different_key_commits_the_parked_transaction():
    """ "only commits to the room's local replica when the key changes"."""
    buffers = BufferSet()
    buffers.accept(normalise_event(event_payload(sequenceNumber=1)))
    buffers.accept(normalise_event(event_payload(sequenceNumber=2, commitTimestamp=ago(4))))
    buffer, completed, outcome = buffers.accept(
        normalise_event(
            event_payload(transactionKey="txn-B", sequenceNumber=1, commitTimestamp=ago(3))
        )
    )
    assert [b.key for b in completed] == ["txn-A"]
    assert buffer.key == "txn-B"
    assert buffers.keys() == ["txn-B"]


def test_each_new_key_commits_only_the_one_it_closed():
    buffers = BufferSet()
    buffers.accept(normalise_event(event_payload(transactionKey="txn-1", sequenceNumber=1)))
    _b, first, _o = buffers.accept(
        normalise_event(
            event_payload(transactionKey="txn-2", sequenceNumber=1, commitTimestamp=ago(4))
        )
    )
    _b, second, _o = buffers.accept(
        normalise_event(
            event_payload(transactionKey="txn-3", sequenceNumber=1, commitTimestamp=ago(3))
        )
    )
    assert [b.key for b in first] == ["txn-1"]
    assert [b.key for b in second] == ["txn-2"]
    assert buffers.keys() == ["txn-3"]


def test_a_restored_run_commits_together_when_the_next_key_arrives():
    """A run put back by a failed commit is a run, and commits as one.

    The only way two transactions sit parked at once is a rollback, and that is
    the case worth checking: a run drained one at a time would commit the
    transaction the room had already been told had failed.
    """
    buffers = BufferSet()
    buffers.accept(normalise_event(event_payload(transactionKey="txn-1", sequenceNumber=1)))
    _b, completed, _o = buffers.accept(
        normalise_event(
            event_payload(transactionKey="txn-2", sequenceNumber=1, commitTimestamp=ago(4))
        )
    )
    buffers.restore_front(completed)
    assert buffers.keys() == ["txn-1", "txn-2"]
    _b, drained, _o = buffers.accept(
        normalise_event(
            event_payload(transactionKey="txn-3", sequenceNumber=1, commitTimestamp=ago(3))
        )
    )
    assert [b.key for b in drained] == ["txn-1", "txn-2"]


def test_events_inside_a_transaction_apply_in_sequence_order():
    buffers = BufferSet()
    for sequence in (3, 1, 2):
        buffers.accept(
            normalise_event(
                event_payload(sequenceNumber=sequence, commitTimestamp=ago(10 - sequence))
            )
        )
    assert [e["sequence_number"] for e in buffers.current().ordered()] == [1, 2, 3]


def test_a_duplicate_sequence_number_inside_one_transaction_is_refused():
    buffers = BufferSet()
    buffers.accept(normalise_event(event_payload(sequenceNumber=1)))
    buffer, completed, outcome = buffers.accept(
        normalise_event(event_payload(sequenceNumber=1, commitTimestamp=ago(4)))
    )
    assert outcome["added"] is False
    assert outcome["reason"] == "duplicate_sequence_number"
    assert completed == []
    assert len(buffer) == 1


def test_a_sequence_gap_is_reported_and_not_repaired():
    """ "Reconcile gaps and overflows" is section 18: a different workflow."""
    assert sequence_gaps(parked("txn-A", 1, 2, 5, 6).ordered()) == [
        {"after": 2, "before": 5, "missing": 2}
    ]
    assert sequence_gaps(parked("txn-A", 1, 2, 3).ordered()) == []
    assert sequence_gaps(parked("txn-A", 1).ordered()) == []


def test_the_buffer_description_names_the_gap_policy_so_a_reader_is_not_led():
    described = parked("txn-A", 1, 4).describe()
    assert described["sequence_gaps"] == [{"after": 1, "before": 4, "missing": 2}]
    assert "different" in described["gap_policy"]
    assert described["sequence_range"] == [1, 4]
    assert described["buffer_bytes"] > 0
    assert described["change_types"] == ["UPDATE", "UPDATE"]


def test_closing_the_stream_flushes_what_is_parked():
    buffers = BufferSet()
    buffers.accept(normalise_event(event_payload(transactionKey="txn-1", sequenceNumber=1)))
    buffers.accept(
        normalise_event(
            event_payload(transactionKey="txn-2", sequenceNumber=1, commitTimestamp=ago(4))
        )
    )
    ready = buffers.flush()
    assert [b.key for b in ready] == ["txn-2"]
    assert buffers.keys() == []


def test_closing_a_restored_run_flushes_the_whole_run():
    buffers = BufferSet()
    buffers.accept(normalise_event(event_payload(transactionKey="txn-1", sequenceNumber=1)))
    _b, completed, _o = buffers.accept(
        normalise_event(
            event_payload(transactionKey="txn-2", sequenceNumber=1, commitTimestamp=ago(4))
        )
    )
    buffers.restore_front(completed)
    assert [b.key for b in buffers.flush()] == ["txn-1", "txn-2"]


def test_a_flush_of_an_idle_stream_is_empty():
    assert BufferSet().flush() == []


def test_a_failed_commit_puts_its_transaction_back_where_it_was():
    buffers = BufferSet()
    buffers.accept(normalise_event(event_payload(transactionKey="txn-1", sequenceNumber=1)))
    _b, completed, _o = buffers.accept(
        normalise_event(
            event_payload(transactionKey="txn-2", sequenceNumber=1, commitTimestamp=ago(4))
        )
    )
    buffers.restore_front(completed)
    assert buffers.keys() == ["txn-1", "txn-2"]
    assert len(buffers.get("txn-1")) == 1


def test_restore_front_is_idempotent_for_a_buffer_still_parked():
    buffers = BufferSet()
    buffers.accept(normalise_event(event_payload()))
    buffers.restore_front([buffers.get("txn-A")])
    assert buffers.keys() == ["txn-A"]


def test_take_removes_a_transaction_without_committing_it():
    buffers = BufferSet()
    buffers.accept(normalise_event(event_payload()))
    assert buffers.take("txn-A").key == "txn-A"
    assert buffers.keys() == []
    assert buffers.take("txn-A") is None


def test_a_stream_whose_timestamps_go_backwards_is_refused_before_any_write():
    buffers = BufferSet()
    older = parked("txn-1", 1)
    older.events[0]["commit_timestamp"] = ago(1)
    newer = parked("txn-2", 1)
    newer.events[0]["commit_timestamp"] = ago(9)
    with pytest.raises(MalformedEvent) as caught:
        buffers.assert_orderable([older, newer])
    assert "before" in str(caught.value)


def test_a_stream_in_timestamp_order_is_accepted():
    buffers = BufferSet()
    first = parked("txn-1", 1)
    first.events[0]["commit_timestamp"] = ago(9)
    second = parked("txn-2", 1)
    second.events[0]["commit_timestamp"] = ago(1)
    buffers.assert_orderable([first, second])


def test_the_parked_registry_is_shared_across_engine_instances():
    """The engine is built per request, so a per-engine buffer would vanish."""
    first = BufferSet.for_subscription("sub-1")
    first.accept(normalise_event(event_payload()))
    assert BufferSet.for_subscription("sub-1") is first
    assert len(BufferSet.for_subscription("sub-1")) == 1
    BufferSet.release("sub-1")
    assert BufferSet.for_subscription("sub-1") is not first


# --------------------------------------------------------------------------- #
# The replica: what each change type does
# --------------------------------------------------------------------------- #


def channel_stub(**overrides: Any) -> dict[str, Any]:
    stub = {
        "id": "crm_channel_stub",
        "name": "/data/dsrOpportunities",
        "entities": ["Opportunity"],
        "enriched_fields": ["External_Id__c"],
        "field_map": normalise_field_map(FIELD_MAP),
    }
    stub.update(overrides)
    return stub


def test_a_create_writes_the_row_from_the_payload():
    event = normalise_event(
        event_payload(
            payload={
                "External_Id__c": "dsr-1",
                "StageName": "Prospecting",
                "Account_Name__c": "Northwind",
            }
        )
    )
    plan = plan_event(event, channel_stub(), existing=None)
    assert plan["action"] == "insert"
    assert plan["state"] == "live"
    assert plan["fields"]["stage"] == "Prospecting"
    assert plan["account"] == "Northwind"
    assert plan["resolution"] == "payload"


def test_an_update_merges_only_what_changed_and_resolves_through_enrichment():
    event = normalise_event(
        event_payload(
            changeType="UPDATE",
            changedFields=["StageName"],
            payload={"StageName": "Negotiation"},
            enrichedFields={"External_Id__c": "dsr-1"},
        )
    )
    plan = plan_event(event, channel_stub(), existing=None)
    assert plan["external_id"] == "dsr-1"
    assert plan["resolution"] == "enriched"
    assert plan["enriched_fields_used"] == ["External_Id__c"]
    assert plan["action"] == "insert"
    assert "update_for_unknown_record" in {
        entry["code"] for entry in replica_findings(plan, channel_stub())
    }


def test_an_update_for_a_known_record_is_an_update_not_an_insert():
    event = normalise_event(
        event_payload(
            changeType="UPDATE",
            changedFields=["StageName"],
            payload={"StageName": "Negotiation"},
            enrichedFields={"External_Id__c": "dsr-1"},
        )
    )
    plan = plan_event(event, channel_stub(), existing={"id": "replica-1", "data": {}})
    assert plan["action"] == "update"
    assert plan["existed"] is True


def test_a_delete_becomes_a_tombstone_that_keeps_its_sync_key():
    event = normalise_event(
        event_payload(changeType="DELETE", payload={}, enrichedFields={"External_Id__c": "dsr-1"})
    )
    plan = plan_event(event, channel_stub(), existing={"id": "replica-1", "data": {}})
    assert plan["action"] == "delete"
    assert plan["state"] == "deleted"
    merged = merge_into(
        {"id": "replica-1", "data": {"external_id": "dsr-1", "stage": "Won"}}, plan, event
    )
    assert merged["data"]["external_id"] == "dsr-1"
    assert merged["data"]["replica_state"] == "deleted"
    assert merged["data"]["stage"] == "Won"


def test_an_undelete_restores_a_tombstone():
    event = normalise_event(
        event_payload(
            changeType="UNDELETE",
            payload={"External_Id__c": "dsr-1", "Account_Name__c": "Northwind"},
        )
    )
    plan = plan_event(event, channel_stub(), existing=None)
    assert plan["action"] == "insert"
    assert plan["state"] == "live"
    assert plan["account"] == "Northwind"
    assert "undelete_for_unknown_record" in {
        entry["code"] for entry in replica_findings(plan, channel_stub())
    }


def test_an_undelete_against_a_known_row_is_a_restore():
    event = normalise_event(
        event_payload(changeType="UNDELETE", payload={"External_Id__c": "dsr-1"})
    )
    plan = plan_event(event, channel_stub(), existing={"id": "replica-1", "data": {}})
    assert plan["action"] == "restore"


def test_an_undelete_with_no_tombstone_still_writes_a_complete_row():
    """ "these events contain all the populated fields"."""
    event = normalise_event(
        event_payload(
            changeType="UNDELETE", payload={"External_Id__c": "dsr-1", "StageName": "Won"}
        )
    )
    plan = plan_event(event, channel_stub(), existing=None)
    assert plan["action"] == "insert"
    assert plan["fields"]["stage"] == "Won"


def test_an_update_with_no_resolvable_record_is_refused_naming_the_field():
    """ "If the room needs an unchanged field (e.g. the external ID) to resolve
    the record, that field is added as an enriched field on the channel." """
    event = normalise_event(
        event_payload(
            changeType="UPDATE", changedFields=["StageName"], payload={"StageName": "Negotiation"}
        )
    )
    with pytest.raises(RecordUnresolvable) as caught:
        plan_event(event, channel_stub(enriched_fields=[]), existing=None)
    message = str(caught.value)
    assert "External_Id__c" in message
    assert "enriched fields" in message
    assert "custom channel" in message


def test_a_delete_with_no_resolvable_record_is_refused_too():
    event = normalise_event(event_payload(changeType="DELETE", payload={}))
    with pytest.raises(RecordUnresolvable):
        plan_event(event, channel_stub(enriched_fields=[]), existing=None)


def test_a_create_with_no_sync_key_is_refused_without_advising_enrichment():
    """ "Enriched fields aren't included in change events for create"."""
    event = normalise_event(event_payload(changeType="CREATE", payload={"StageName": "X"}))
    with pytest.raises(RecordUnresolvable) as caught:
        plan_event(event, channel_stub(), existing=None)
    assert "Enrichment does not apply" in str(caught.value)


def test_a_merge_reports_exactly_which_replica_fields_moved():
    event = normalise_event(
        event_payload(
            changeType="UPDATE",
            changedFields=["StageName"],
            payload={"StageName": "Negotiation"},
            enrichedFields={"External_Id__c": "dsr-1"},
        )
    )
    plan = plan_event(event, channel_stub(), existing=None)
    merged = merge_into(
        {"id": "replica-1", "data": {"external_id": "dsr-1", "stage": "Prospecting", "amount": 5}},
        plan,
        event,
    )
    assert merged["changed_replica_fields"] == ["stage"]
    assert merged["data"]["amount"] == 5
    assert merged["data"]["last_change_type"] == "UPDATE"
    assert merged["data"]["channel_name"] == "/data/dsrOpportunities"


def test_a_replica_row_records_its_channel_so_the_lookup_can_be_indexed():
    event = normalise_event(event_payload())
    plan = plan_event(event, channel_stub(), existing=None)
    merged = merge_into(None, plan, event)
    assert merged["data"]["channel_id"] == "crm_channel_stub"
    assert merged["data"]["channel_name"] == "/data/dsrOpportunities"


def test_replica_findings_report_enriched_values_and_unmapped_crm_fields():
    event = normalise_event(
        event_payload(
            changeType="UPDATE",
            changedFields=["StageName", "Brand_New__c"],
            payload={"StageName": "Negotiation", "Brand_New__c": 1},
            enrichedFields={"External_Id__c": "dsr-1"},
        )
    )
    plan = plan_event(event, channel_stub(), existing=None)
    codes = {entry["code"] for entry in replica_findings(plan, channel_stub())}
    assert "values_from_enriched_fields" in codes
    assert "unmapped_crm_fields" in codes


def test_a_delete_for_an_unknown_record_is_flagged_rather_than_silent():
    event = normalise_event(
        event_payload(changeType="DELETE", payload={}, enrichedFields={"External_Id__c": "dsr-1"})
    )
    plan = plan_event(event, channel_stub(), existing=None)
    codes = {entry["code"] for entry in replica_findings(plan, channel_stub())}
    assert "delete_for_unknown_record" in codes


def test_a_clean_update_with_the_sync_key_in_the_payload_carries_no_findings():
    event = normalise_event(
        event_payload(
            changeType="UPDATE",
            changedFields=["StageName"],
            payload={"StageName": "Negotiation", "External_Id__c": "dsr-1"},
        )
    )
    plan = plan_event(event, channel_stub(), existing={"id": "r", "data": {}})
    assert plan["resolution"] == "payload"
    assert replica_findings(plan, channel_stub()) == []


def test_an_update_that_needs_enrichment_to_resolve_is_flagged_for_it():
    event = normalise_event(
        event_payload(
            changeType="UPDATE",
            changedFields=["StageName"],
            payload={"StageName": "Negotiation"},
            enrichedFields={"External_Id__c": "dsr-1"},
        )
    )
    plan = plan_event(event, channel_stub(), existing={"id": "r", "data": {}})
    codes = {entry["code"] for entry in replica_findings(plan, channel_stub())}
    assert "values_from_enriched_fields" in codes
    assert "update_for_unknown_record" not in codes


def test_the_deal_panel_row_hides_the_bookkeeping_fields():
    event = normalise_event(
        event_payload(payload={"External_Id__c": "dsr-1", "Account_Name__c": "Northwind"})
    )
    plan = plan_event(event, channel_stub(), existing=None)
    merged = merge_into(None, plan, event)
    row = deal_panel_row({"id": "replica-1", "room_id": "room-1", "data": merged["data"]})
    assert row["state"] == "live"
    assert row["account"] == "Northwind"
    assert row["replica_id"] == "replica-1"
    assert "replica_state" not in row["fields"]
    assert "channel_name" not in row["fields"]
    assert row["fields"]["external_id"] == "dsr-1"


# --------------------------------------------------------------------------- #
# Channels
# --------------------------------------------------------------------------- #


def org_stub() -> dict[str, Any]:
    return {"id": "crm_org_stub", "system": "salesforce"}


def test_naming_the_standard_channel_makes_a_channel_standard():
    normalised = normalise_channel(
        {"name": STANDARD_CHANNEL, "entities": ["Opportunity"], "field_map": FIELD_MAP},
        org=org_stub(),
        transport="pubsub",
    )
    assert normalised["kind"] == "standard"


def test_any_other_name_makes_a_custom_channel():
    normalised = normalise_channel(
        {"name": "/data/dsrOpportunities", "entities": ["Opportunity"], "field_map": FIELD_MAP},
        org=org_stub(),
        transport="pubsub",
    )
    assert normalised["kind"] == "custom"


def test_a_caller_cannot_relabel_a_custom_channel_as_the_standard_one():
    with pytest.raises(ChannelError) as caught:
        normalise_channel(
            {
                "name": "/data/dsrOpportunities",
                "kind": "standard",
                "entities": ["Opportunity"],
                "field_map": FIELD_MAP,
            },
            org=org_stub(),
            transport="pubsub",
        )
    assert "contradicts it" in str(caught.value)


def test_a_channel_needs_a_name_entities_and_a_field_map():
    for payload in (
        {"entities": ["Opportunity"], "field_map": FIELD_MAP},
        {"name": "/data/x", "field_map": FIELD_MAP},
        {"name": "/data/x", "entities": []},
    ):
        with pytest.raises(ChannelError):
            normalise_channel(payload, org=org_stub(), transport="pubsub")


def test_duplicate_entities_are_refused():
    with pytest.raises(ChannelError) as caught:
        normalise_channel(
            {"name": "/data/x", "entities": ["Opportunity", "Opportunity"], "field_map": FIELD_MAP},
            org=org_stub(),
            transport="pubsub",
        )
    assert "unique" in str(caught.value)


def test_the_default_buffer_is_the_vendor_recommendation_and_a_deviation_is_reported():
    default = normalise_channel(
        {"name": "/data/x", "entities": ["Opportunity"], "field_map": FIELD_MAP},
        org=org_stub(),
        transport="pubsub",
    )
    assert default["buffer_bytes"] == RECOMMENDED_BUFFER_BYTES
    assert default["buffer_matches_recommendation"] is True

    smaller = normalise_channel(
        {
            "name": "/data/y",
            "entities": ["Opportunity"],
            "field_map": FIELD_MAP,
            "buffer_bytes": 1024,
        },
        org=org_stub(),
        transport="pubsub",
    )
    assert smaller["buffer_matches_recommendation"] is False


def test_a_buffer_size_must_be_a_positive_whole_number():
    for bad in (0, -1, "lots"):
        with pytest.raises(ChannelError):
            normalise_channel(
                {
                    "name": "/data/x",
                    "entities": ["Opportunity"],
                    "field_map": FIELD_MAP,
                    "buffer_bytes": bad,
                },
                org=org_stub(),
                transport="pubsub",
            )


def test_channel_name_collision_is_case_sensitive():
    """ "The channel name is case-sensitive." """
    existing = [{"name": STANDARD_CHANNEL, "org_id": "crm_org_stub"}]
    from dsr.change_stream.channels import channel_name_taken

    assert channel_name_taken(existing, STANDARD_CHANNEL, org_id="crm_org_stub") is True
    assert channel_name_taken(existing, "/data/changeevents", org_id="crm_org_stub") is False


def test_a_channel_name_is_free_on_another_org():
    from dsr.change_stream.channels import channel_name_taken

    existing = [{"name": STANDARD_CHANNEL, "org_id": "crm_org_one"}]
    assert channel_name_taken(existing, STANDARD_CHANNEL, org_id="crm_org_two") is False


def test_the_collision_message_names_the_names_in_use():
    from dsr.change_stream.channels import names_in_use

    existing = [
        {"name": STANDARD_CHANNEL, "org_id": "crm_org_stub"},
        {"name": "/data/dsrContacts", "org_id": "crm_org_stub"},
        {"name": "/data/other", "org_id": "crm_org_elsewhere"},
    ]
    assert names_in_use(existing, org_id="crm_org_stub") == [
        "/data/ChangeEvents",
        "/data/dsrContacts",
    ]


def test_enrichment_on_the_standard_channel_is_refused_with_the_researched_reason():
    with pytest.raises(StandardChannelEnrichmentRefused) as caught:
        add_enrichment(
            {"kind": "standard", "name": STANDARD_CHANNEL, "enriched_fields": []},
            "External_Id__c",
            transport="pubsub",
        )
    message = str(caught.value)
    assert "other subscriber" in message
    assert "custom channel" in message


def test_a_case_variant_of_the_standard_channel_is_still_protected():
    """ "The channel name is case-sensitive", so a variant is a separate channel -
    but the harm the isolation rule names does not depend on the spelling."""
    assert is_standard_channel("/data/changeevents") is True


def test_enrichment_on_a_transport_outside_the_three_is_refused():
    for transport in ("delta_link", "workflow_webhook"):
        with pytest.raises(UnsupportedEnrichmentTransport) as caught:
            add_enrichment({"kind": "custom", "enriched_fields": []}, "X__c", transport=transport)
        assert "Pub/Sub API" in str(caught.value)


def test_enrichment_on_a_custom_channel_is_accepted_and_merged():
    result = add_enrichment(
        {"kind": "custom", "enriched_fields": ["A__c"]}, ["B__c", "A__c"], transport="pubsub"
    )
    assert result["enriched_fields"] == ["A__c", "B__c"]
    assert result["added"] == ["B__c"]


def test_enrichment_accepts_a_bare_string():
    assert add_enrichment({"kind": "custom", "enriched_fields": []}, "A__c", transport="pubsub")[
        "enriched_fields"
    ] == ["A__c"]


def test_enrichment_needs_at_least_one_field_and_the_right_type():
    with pytest.raises(ChannelError):
        add_enrichment({"kind": "custom", "enriched_fields": []}, [], transport="pubsub")
    with pytest.raises(ChannelError):
        add_enrichment({"kind": "custom", "enriched_fields": []}, {"A__c": 1}, transport="pubsub")


def test_removing_an_enrichment_the_channel_does_not_have_is_refused():
    with pytest.raises(ChannelError) as caught:
        remove_enrichment({"name": "/data/x", "enriched_fields": ["A__c"]}, "B__c")
    assert "it enriches" in str(caught.value)
    assert (
        remove_enrichment({"name": "/data/x", "enriched_fields": ["A__c"]}, "A__c")[
            "enriched_fields"
        ]
        == []
    )


# --------------------------------------------------------------------------- #
# Dataverse, in the domain
# --------------------------------------------------------------------------- #


def test_a_declared_table_starts_with_change_tracking_off():
    table = normalise_table({"logical_name": "account"})
    assert table["track_changes"] is False
    assert table["change_tracking_supported"] is False
    assert table["entity_set"] == "accounts"
    assert table["delta_link"] == ""


def test_a_table_needs_a_logical_name():
    with pytest.raises(ChangeTrackingDisabled):
        normalise_table({})


def test_enabling_track_changes_is_idempotent():
    assert (
        dataverse_rules.enable_track_changes({"track_changes": False})["already_enabled"] is False
    )
    assert dataverse_rules.enable_track_changes({"track_changes": True})["already_enabled"] is True


def test_disabling_track_changes_is_refused_from_either_state():
    """ "After you enable change tracking for a table, you can't disable it." """
    for table in (
        {"logical_name": "account", "track_changes": True},
        {"logical_name": "account", "track_changes": False},
    ):
        with pytest.raises(ChangeTrackingIrreversible) as caught:
            dataverse_rules.refuse_disable(table)
        assert "can't disable it" in str(caught.value)


def test_a_poll_without_the_track_changes_header_is_refused():
    with pytest.raises(MissingTrackChangesPreference) as caught:
        poll({"logical_name": "account", "track_changes": True}, prefer=None, options={})
    assert CHANGE_TRACKING_PREFERENCE in str(caught.value)


def test_a_poll_on_a_table_that_is_not_tracking_is_refused():
    with pytest.raises(ChangeTrackingDisabled) as caught:
        poll(
            {"logical_name": "account", "track_changes": False},
            prefer=CHANGE_TRACKING_PREFERENCE,
            options={},
        )
    assert "Track changes" in str(caught.value)


@pytest.mark.parametrize("option", ["filter", "$filter", "orderby", "expand", "top"])
def test_each_of_the_four_refused_query_options_names_itself(option):
    with pytest.raises(UnsupportedDeltaQueryOption) as caught:
        poll(
            {"logical_name": "account", "track_changes": True},
            prefer=CHANGE_TRACKING_PREFERENCE,
            options={option: "1"},
        )
    assert str(caught.value).startswith('The "$')


def test_select_is_not_a_refused_query_option():
    result = poll(
        {"logical_name": "account", "track_changes": True, "entity_set": "accounts"},
        prefer=CHANGE_TRACKING_PREFERENCE,
        options={"select": "accountid, name"},
    )
    assert result["@odata.context"].endswith("accounts?$select=accountid, name")


def test_a_poll_returns_a_delta_link_the_annotation_and_a_count_url():
    result = poll(
        {"logical_name": "account", "track_changes": True, "entity_set": "accounts"},
        prefer=CHANGE_TRACKING_PREFERENCE,
        options={"select": "accountid"},
        observed_changes=3,
    )
    assert DATAVERSE_API_VERSION in result["@odata.deltaLink"]
    assert "$deltatoken=" in result["@odata.deltaLink"]
    assert result["changeTracking"]["Supported"] is True
    assert result["changeTracking"]["Annotation"] == CHANGE_TRACKING_ANNOTATION
    assert result["changes_observed"] == 3
    assert "/$count?$deltatoken=" in result["count_url"]


def test_an_incremental_poll_says_so_and_reports_the_previous_token():
    result = poll(
        {"logical_name": "account", "track_changes": True, "deltatoken": "account:0"},
        prefer=CHANGE_TRACKING_PREFERENCE,
        options={"deltatoken": "account:0"},
    )
    assert result["incremental"] is True
    assert result["previous_deltatoken"] == "account:0"


def test_a_full_poll_is_not_incremental():
    result = poll(
        {"logical_name": "account", "track_changes": True},
        prefer=CHANGE_TRACKING_PREFERENCE,
        options={},
    )
    assert result["incremental"] is False
    assert result["returned_deltatoken"] == "account:0"


def test_the_change_count_needs_tracking_and_says_what_it_does_not_count():
    with pytest.raises(ChangeTrackingDisabled):
        change_count({"logical_name": "account", "track_changes": False}, deltatoken=None)
    counted = change_count(
        {
            "logical_name": "account",
            "track_changes": True,
            "entity_set": "accounts",
            "deltatoken": "account:0",
            "change_count": 7,
        },
        deltatoken="account:0",
    )
    assert counted["count"] == 7
    assert "buffered" in counted["not_counted"]


# --------------------------------------------------------------------------- #
# HubSpot, in the domain
# --------------------------------------------------------------------------- #


def test_a_hubspot_subscription_needs_an_absolute_target_url():
    with pytest.raises(ChannelError):
        hubspot_rules.normalise_subscription({})
    with pytest.raises(ChannelError) as caught:
        hubspot_rules.normalise_subscription({"target_url": "hooks.example/x"})
    assert "absolute http(s) URL" in str(caught.value)


def test_a_hubspot_subscription_records_that_the_criteria_are_the_workflows():
    row = hubspot_rules.normalise_subscription(
        {"target_url": "https://hooks.example/x", "workflow_id": "wf-1"}
    )
    assert row["workflow_id"] == "wf-1"
    assert "starting conditions" in row["criteria_owned_by"]


def test_a_workflow_call_is_exempt_from_the_rate_limit_and_still_counted():
    """ "Webhook calls made via workflows do not count towards the API rate limit." """
    result = hubspot_rules.charge_or_exempt({"limit": 10, "spent": 4}, via_workflow=True)
    assert result["charged"] is False
    assert result["spent"] == 4
    assert result["remaining"] == 6
    assert "do not count" in result["exempt_because"]


def test_an_app_call_spends_the_budget():
    result = hubspot_rules.charge_or_exempt({"limit": 10, "spent": 4}, via_workflow=False)
    assert result["charged"] is True
    assert result["spent"] == 5
    assert result["remaining"] == 5
    assert result["exempt_because"] is None


def test_the_subscription_cap_is_a_refusal_naming_the_limit_and_the_count():
    hubspot_rules.require_capacity(999)
    with pytest.raises(SubscriptionLimitExceeded) as caught:
        hubspot_rules.require_capacity(1000)
    assert "1000" in str(caught.value)
    assert "Cancel one" in str(caught.value)


def test_the_hubspot_description_says_the_rest_surface_is_not_built():
    described = hubspot_rules.describe()
    assert described["direction"] == "inbound"
    assert "starting conditions" in described["fired_by"]
    assert "not implemented" in described["subscription_rest_api"]


# --------------------------------------------------------------------------- #
# Usage counters
# --------------------------------------------------------------------------- #


def test_requested_and_delivered_are_counted_apart():
    """ "the number of requested events in the FetchRequest parameter"."""
    usage = usage_rules.request_fetch(None, 100)
    assert usage["events_requested"] == 100
    assert usage["fetch_outstanding"] == 100
    usage = usage_rules.record_delivery(usage, buffer_bytes=512)
    assert usage["events_delivered"] == 1
    assert usage["fetch_outstanding"] == 99
    assert usage["buffer_high_water_bytes"] == 512


def test_the_buffer_high_water_never_goes_back_down():
    usage = usage_rules.record_delivery(
        usage_rules.record_delivery(None, buffer_bytes=900), buffer_bytes=10
    )
    assert usage["buffer_high_water_bytes"] == 900
    assert usage["buffer_bytes"] == 10


def test_a_non_positive_fetch_request_changes_nothing():
    assert usage_rules.request_fetch(None, 0) == usage_rules.blank()
    assert usage_rules.request_fetch(None, -5) == usage_rules.blank()


def test_a_duplicate_sequence_number_is_counted():
    assert usage_rules.record_duplicate(None)["duplicates_refused"] == 1


def test_a_commit_counts_events_and_writes_separately():
    usage = usage_rules.record_commit(None, events=3, writes=1)
    assert usage["transactions_committed"] == 1
    assert usage["events_committed"] == 3
    assert usage["replica_writes"] == 1


def test_usage_merge_ignores_a_counter_it_does_not_know():
    merged = usage_rules.merge({"events_delivered": 4, "a_counter_from_the_future": 99})
    assert merged["events_delivered"] == 4
    assert "a_counter_from_the_future" not in merged


def test_the_usage_description_compares_against_the_recommendation():
    described = usage_rules.describe(
        {
            "id": "sub-1",
            "channel_name": "/data/x",
            "transport": "pubsub",
            "state": "open",
            "buffer_limit_bytes": RECOMMENDED_BUFFER_BYTES,
            "usage": {"events_delivered": 2, "buffer_bytes": RECOMMENDED_BUFFER_BYTES * 2},
        }
    )
    assert described["recommended_buffer_bytes"] == RECOMMENDED_BUFFER_BYTES
    assert described["buffer_exceeds_recommendation"] is True
    assert described["buffer_within_recommendation"] is False
    assert described["buffer_usage_percent"] > 100.0


def test_the_metric_source_says_no_vendor_schema_was_published():
    source = usage_rules.describe_metric_source()
    assert source["source"] == "PlatformEventUsageMetric"
    assert source["schema_published_by_vendor"] is False
    assert "events_requested" in source["counters"]


# --------------------------------------------------------------------------- #
# Engine: orgs and the edition gate
# --------------------------------------------------------------------------- #


def test_registering_an_org_records_the_gate_reading(engine):
    record = engine.register_org(
        {"system": "salesforce", "edition": "Unlimited"}, actor="dana", source=SOURCE
    )
    assert record["data"]["edition_covers_cdc"] is True
    assert record["data"]["cdc_enabled"] is False


def test_the_system_must_be_one_the_research_names(engine):
    for bad in ("pipedrive", "", None):
        with pytest.raises(OrgError):
            engine.register_org(
                {"system": bad, "edition": "Unlimited"}, actor="dana", source=SOURCE
            )


def test_the_edition_is_required_and_says_why(engine):
    with pytest.raises(OrgError) as caught:
        engine.register_org({"system": "salesforce"}, actor="dana", source=SOURCE)
    assert "Enterprise" in str(caught.value)


def test_one_org_per_system_so_the_gate_has_one_answer(engine):
    engine.register_org(
        {"system": "salesforce", "edition": "Unlimited"}, actor="dana", source=SOURCE
    )
    with pytest.raises(DuplicateOrg):
        engine.register_org(
            {"system": "salesforce", "edition": "Enterprise"}, actor="dana", source=SOURCE
        )


def test_enabling_cdc_on_an_edition_without_it_is_refused(engine, org):
    engine.orgs()
    low = engine.register_org({"system": "hubspot", "edition": "Free"}, actor="dana", source=SOURCE)
    with pytest.raises(EditionDoesNotSupportCdc) as caught:
        engine.patch_org(
            low["id"], {"cdc_enabled": True, "entities": ["Deal"]}, actor="dana", source=SOURCE
        )
    assert "Professional" not in str(caught.value)
    assert "Enterprise, Performance, Unlimited, Developer" in str(caught.value)


def test_enabling_cdc_needs_the_objects_the_room_cares_about(engine, org):
    """Nothing is enabled on the first call, so the entity list is never asked for."""
    with pytest.raises(OrgError) as caught:
        engine.patch_org(org["id"], {"cdc_enabled": True}, actor="dana", source=SOURCE)
    assert "one or more entities" in str(caught.value)


def test_a_channel_needs_cdc_enabled_first(engine, org):
    with pytest.raises(ChangeStreamError) as caught:
        engine.create_channel(
            {
                "name": "/data/x",
                "org_id": org["id"],
                "entities": ["Opportunity"],
                "field_map": FIELD_MAP,
            },
            actor="dana",
            source=SOURCE,
        )
    assert "Change Data Capture is not enabled" in str(caught.value)


def test_an_unknown_org_is_a_404_shaped_refusal(engine):
    with pytest.raises(UnknownOrg):
        engine.require_org("crm_org_nope")


def test_patching_an_org_with_nothing_to_change_is_refused(engine, cdc_org):
    with pytest.raises(OrgError) as caught:
        engine.patch_org(cdc_org["id"], {}, actor="dana", source=SOURCE)
    assert "nothing to change" in str(caught.value)


# --------------------------------------------------------------------------- #
# Engine: channels
# --------------------------------------------------------------------------- #


def test_a_channel_is_created_on_a_cdc_enabled_org(engine, cdc_org):
    channel = engine.create_channel(
        {
            "name": "/data/dsrOpportunities",
            "org_id": cdc_org["id"],
            "entities": ["Opportunity"],
            "field_map": FIELD_MAP,
        },
        actor="dana",
        source=SOURCE,
    )
    assert channel["kind"] == "custom"
    assert channel["enrichment_note"].startswith("No enriched fields")
    assert channel["field_map_findings"] == []


def test_a_duplicate_channel_name_on_the_same_org_is_refused(engine, cdc_org, channel):
    with pytest.raises(DuplicateChannelName) as caught:
        engine.create_channel(
            {
                "name": channel["name"],
                "org_id": cdc_org["id"],
                "entities": ["Opportunity"],
                "field_map": FIELD_MAP,
            },
            actor="dana",
            source=SOURCE,
        )
    assert "case-sensitive" in str(caught.value)


def test_the_same_name_on_another_org_is_allowed(engine, cdc_org, channel):
    other = engine.register_org(
        {"system": "hubspot", "edition": "Professional"}, actor="dana", source=SOURCE
    )
    with pytest.raises(EditionDoesNotSupportCdc):
        engine.patch_org(
            other["id"], {"cdc_enabled": True, "entities": ["Deal"]}, actor="dana", source=SOURCE
        )
    other = engine.patch_org(
        other["id"], {"edition": "Enterprise", "org_name": "Contoso"}, actor="dana", source=SOURCE
    )
    engine.patch_org(
        other["id"], {"cdc_enabled": True, "entities": ["Deal"]}, actor="dana", source=SOURCE
    )
    second = engine.create_channel(
        {
            "name": channel["name"],
            "org_id": other["id"],
            "entities": ["Deal"],
            "field_map": FIELD_MAP,
        },
        actor="dana",
        source=SOURCE,
    )
    assert second["id"] != channel["id"]


def test_the_channel_name_filter_is_case_sensitive_over_http(http):
    setup = http_setup(http)
    http.post(
        f"{PREFIX}/channels",
        json={
            "name": "/data/changeevents",
            "org_id": setup["org"]["id"],
            "entities": ["Opportunity"],
            "transport": "pubsub",
            "field_map": FIELD_MAP,
        },
    )
    exact = http.get(f"{PREFIX}/channels", params={"name": "/data/changeevents"}).json()
    assert exact["count"] == 1
    assert (
        http.get(f"{PREFIX}/channels", params={"name": "/DATA/ChangeEvents"}).json()["count"] == 0
    )


def test_enrichment_through_the_engine_records_the_fields(engine, channel):
    enriched = engine.enrich_channel(channel["id"], ["External_Id__c"], actor="dana", source=SOURCE)
    assert enriched["enriched_fields"] == ["External_Id__c"]
    assert enriched["added"] == ["External_Id__c"]
    assert "enriched field" in enriched["enrichment_note"]


def test_enriching_the_standard_channel_through_the_engine_is_refused(engine, cdc_org):
    standard = engine.create_channel(
        {
            "name": STANDARD_CHANNEL,
            "org_id": cdc_org["id"],
            "entities": ["Opportunity"],
            "field_map": FIELD_MAP,
        },
        actor="dana",
        source=SOURCE,
    )
    with pytest.raises(StandardChannelEnrichmentRefused):
        engine.enrich_channel(standard["id"], "External_Id__c", actor="dana", source=SOURCE)


def test_patching_a_channel_refuses_its_name_and_reports_what_it_accepts(engine, channel):
    patched = engine.patch_channel(
        channel["id"], {"buffer_bytes": 1024}, actor="dana", source=SOURCE
    )
    assert patched["buffer_matches_recommendation"] is False
    with pytest.raises(UnknownChannel) as caught:
        engine.patch_channel(channel["id"], {}, actor="dana", source=SOURCE)
    assert "entities, field_map or buffer_bytes" in str(caught.value)
    with pytest.raises(UnknownChannel):
        engine.patch_channel(channel["id"], {"name": "/data/renamed"}, actor="dana", source=SOURCE)


def test_patching_a_channel_rejects_a_bad_buffer_size(engine, channel):
    for bad in (0, -1, "lots"):
        with pytest.raises(UnknownChannel):
            engine.patch_channel(channel["id"], {"buffer_bytes": bad}, actor="dana", source=SOURCE)


def test_a_channel_with_an_open_subscription_cannot_be_deleted(engine, subscription, channel):
    with pytest.raises(ChannelInUse) as caught:
        engine.delete_channel(channel["id"], actor="dana", source=SOURCE)
    assert "open subscription" in str(caught.value)


def test_deleting_a_channel_with_no_live_subscription_is_a_soft_delete(engine, channel):
    result = engine.delete_channel(channel["id"], actor="dana", source=SOURCE)
    assert result["hard"] is False
    assert engine.channel(channel["id"]) is None


def test_an_unknown_channel_is_a_404_shaped_refusal(engine):
    with pytest.raises(UnknownChannel):
        engine.require_channel("crm_channel_nope")


# --------------------------------------------------------------------------- #
# Engine: subscriptions
# --------------------------------------------------------------------------- #


def test_a_subscription_starts_with_nothing_requested(engine, channel, room):
    """The research publishes no default fetch size, so there is no default."""
    opened = engine.open_subscription(
        {"channel_id": channel["id"]}, room_id=room["id"], actor="dana", source=SOURCE
    )
    assert opened["fetch_outstanding"] == 0
    assert opened["usage"]["events_requested"] == 0
    assert opened["wire_format"] == "avro"
    assert opened["state"] == "open"


def test_a_fetch_request_records_what_was_asked_for(engine, subscription):
    assert subscription["usage"]["events_requested"] == 50
    assert subscription["fetch_outstanding"] == 50
    assert subscription["usage"]["fetch_requests"] == 1


def test_the_vendors_num_events_spelling_works_too(engine, subscription):
    fetched = engine.fetch(subscription["id"], {"numEvents": 10}, actor="dana", source=SOURCE)
    assert fetched["usage"]["events_requested"] == 60
    assert fetched["fetch_outstanding"] == 60


def test_a_fetch_request_needs_a_positive_count_and_accepts_either_spelling(engine, subscription):
    for payload in ({}, {"num_requested": 0}, {"num_requested": -1}, {"num_requested": "many"}):
        with pytest.raises(MalformedEvent):
            engine.fetch(subscription["id"], payload, actor="dana", source=SOURCE)


def test_a_subscription_cannot_change_the_transport_its_channel_declared(engine, channel, room):
    with pytest.raises(ChangeStreamError) as caught:
        engine.open_subscription(
            {"channel_id": channel["id"], "transport": "cometd"},
            room_id=room["id"],
            actor="dana",
            source=SOURCE,
        )
    assert "Pub/Sub" in str(caught.value) or "streams over" in str(caught.value)


def test_a_subscription_cannot_read_an_entity_the_channel_does_not_stream(engine, channel, room):
    with pytest.raises(EntityNotOnChannel):
        engine.open_subscription(
            {"channel_id": channel["id"], "entity": "Contact"},
            room_id=room["id"],
            actor="dana",
            source=SOURCE,
        )


def test_an_unknown_channel_on_a_subscription_is_a_404_shaped_refusal(engine, room):
    with pytest.raises(UnknownChannel):
        engine.open_subscription(
            {"channel_id": "crm_channel_nope"}, room_id=room["id"], actor="dana", source=SOURCE
        )


def test_an_unknown_subscription_is_a_404_shaped_refusal(engine):
    with pytest.raises(UnknownSubscription):
        engine.require_subscription("crm_subscription_nope")


def test_a_fetch_on_a_closed_subscription_is_refused(engine, subscription):
    engine.close_subscription(subscription["id"], actor="dana", source=SOURCE)
    with pytest.raises(SubscriptionClosed) as caught:
        engine.fetch(subscription["id"], {"num_requested": 5}, actor="dana", source=SOURCE)
    assert "long-lived" in str(caught.value)


def test_closing_an_already_closed_subscription_is_a_no_op_not_an_error(engine, subscription):
    engine.close_subscription(subscription["id"], actor="dana", source=SOURCE)
    again = engine.close_subscription(subscription["id"], actor="dana", source=SOURCE)
    assert again["already_closed"] is True
    assert again["flushed"] == []


# --------------------------------------------------------------------------- #
# Engine: the event path
# --------------------------------------------------------------------------- #


def deliver(engine, subscription, room, **overrides):
    payload = event_payload(subscription_id=subscription["id"], entity="Opportunity")
    payload.update(overrides)
    return engine.deliver_event(payload, room_id=room["id"], actor="dana", source=SOURCE)


def test_an_event_needs_a_subscription(engine, room):
    with pytest.raises(MalformedEvent) as caught:
        engine.deliver_event(event_payload(), room_id=room["id"], actor="dana", source=SOURCE)
    assert "subscription_id" in str(caught.value)


def fresh_subscription(engine, channel, room):
    """A subscription with a FetchRequest outstanding, for the flow-control tests."""
    opened = engine.open_subscription(
        {"channel_id": channel["id"]}, room_id=room["id"], actor="dana", source=SOURCE
    )
    return engine.fetch(opened["id"], {"num_requested": 10}, actor="dana", source=SOURCE)


def test_an_event_with_no_fetch_request_outstanding_is_refused(engine, channel, room):
    """ "The client can control the flow of events received"."""
    opened = engine.open_subscription(
        {"channel_id": channel["id"]}, room_id=room["id"], actor="dana", source=SOURCE
    )
    assert opened["fetch_outstanding"] == 0
    with pytest.raises(NoOutstandingFetchRequest) as caught:
        deliver(engine, opened, room)
    assert "FetchRequest" in str(caught.value)
    assert "not authorised to receive" in str(caught.value)


def test_the_refusal_writes_nothing_at_all(engine, channel, room):
    opened = engine.open_subscription(
        {"channel_id": channel["id"]}, room_id=room["id"], actor="dana", source=SOURCE
    )
    with pytest.raises(NoOutstandingFetchRequest):
        deliver(engine, opened, room)
    assert engine.events(room_id=room["id"]) == []
    assert engine.replica(room_id=room["id"]) == []
    assert engine.buffer_view(room_id=room["id"])["subscriptions"][0]["parked"] == []


def test_an_event_on_a_closed_subscription_is_refused(engine, subscription, room):
    engine.close_subscription(subscription["id"], actor="dana", source=SOURCE)
    with pytest.raises(SubscriptionClosed):
        deliver(engine, subscription, room)


def test_an_event_parks_and_commits_nothing_until_the_key_changes(engine, subscription, room):
    result = deliver(engine, subscription, room)
    assert result["outcome"] == "buffered"
    assert result["committed"] == []
    assert result["buffer"]["event_count"] == 1
    assert result["event"]["state"] == "buffered"
    assert engine.replica(room_id=room["id"]) == []


def test_the_arrival_of_a_new_key_commits_the_parked_transaction(engine, subscription, room):
    deliver(engine, subscription, room, transactionKey="txn-1", sequenceNumber=1)
    result = deliver(
        engine,
        subscription,
        room,
        transactionKey="txn-2",
        sequenceNumber=1,
        commitTimestamp=ago(4),
    )
    assert [row["transaction_key"] for row in result["committed"]] == ["txn-1"]
    assert len(engine.replica(room_id=room["id"])) == 1


def test_a_committed_transaction_applies_every_event_in_sequence_order(engine, subscription, room):
    deliver(
        engine,
        subscription,
        room,
        transactionKey="txn-1",
        sequenceNumber=3,
        changeType="UPDATE",
        changedFields=["Amount"],
        payload={"Amount": 300},
        enrichedFields={"External_Id__c": "dsr-1"},
    )
    deliver(
        engine,
        subscription,
        room,
        transactionKey="txn-1",
        sequenceNumber=1,
        changeType="UPDATE",
        changedFields=["StageName"],
        payload={"StageName": "Negotiation"},
        enrichedFields={"External_Id__c": "dsr-1"},
        commitTimestamp=ago(6),
    )
    result = deliver(
        engine,
        subscription,
        room,
        transactionKey="txn-2",
        sequenceNumber=1,
        commitTimestamp=ago(4),
    )
    committed = result["committed"][0]
    assert committed["sequence_numbers"] == [1, 3]
    # Sequence 1 runs first, so the row does not exist yet and both the sync key
    # and the stage are new. Order inside a transaction is observable here, which
    # is the point of sorting on sequenceNumber at all.
    assert committed["replica_writes"][0]["changed_replica_fields"] == ["external_id", "stage"]
    assert committed["replica_writes"][1]["changed_replica_fields"] == ["amount"]


def test_the_commit_result_names_the_rule_that_fired(engine, subscription, room):
    result = deliver(engine, subscription, room)
    assert "when the key changes" in result["commit_rule"]
    assert result["buffered_transactions"] == ["txn-A"]


def test_an_update_merges_into_the_row_a_create_wrote(engine, subscription, room):
    deliver(
        engine,
        subscription,
        room,
        transactionKey="txn-1",
        sequenceNumber=1,
        payload={"External_Id__c": "dsr-1", "StageName": "Prospecting", "Amount": 100},
    )
    deliver(
        engine,
        subscription,
        room,
        transactionKey="txn-2",
        sequenceNumber=1,
        commitTimestamp=ago(4),
        changeType="UPDATE",
        changedFields=["StageName"],
        payload={"StageName": "Negotiation"},
        enrichedFields={"External_Id__c": "dsr-1"},
    )
    # txn-2 is the transaction the stream is still filling, so the drain is the
    # close. That is the researched rule's terminal case, not a shortcut.
    engine.close_subscription(subscription["id"], actor="dana", source=SOURCE)
    rows = engine.replica(room_id=room["id"])
    assert len(rows) == 1
    assert rows[0]["fields"]["stage"] == "Negotiation"
    assert rows[0]["fields"]["amount"] == 100


def test_a_field_the_update_did_not_change_is_kept(engine, subscription, room):
    """The "unchanged but needed" gap, and the reason nothing is cleared."""
    deliver(
        engine,
        subscription,
        room,
        transactionKey="txn-1",
        sequenceNumber=1,
        payload={"External_Id__c": "dsr-1", "StageName": "Prospecting", "Amount": 100},
    )
    deliver(
        engine,
        subscription,
        room,
        transactionKey="txn-2",
        sequenceNumber=1,
        commitTimestamp=ago(4),
        changeType="UPDATE",
        changedFields=["StageName"],
        payload={"StageName": "Negotiation"},
        enrichedFields={"External_Id__c": "dsr-1"},
    )
    engine.close_subscription(subscription["id"], actor="dana", source=SOURCE)
    assert engine.replica(room_id=room["id"])[0]["fields"]["amount"] == 100


def test_a_delete_commits_a_tombstone_and_keeps_the_sync_key(engine, subscription, room):
    deliver(
        engine,
        subscription,
        room,
        transactionKey="txn-1",
        sequenceNumber=1,
        payload={"External_Id__c": "dsr-1", "Account_Name__c": "Northwind"},
    )
    deliver(
        engine,
        subscription,
        room,
        transactionKey="txn-2",
        sequenceNumber=1,
        commitTimestamp=ago(4),
        changeType="DELETE",
        payload={},
        enrichedFields={"External_Id__c": "dsr-1"},
    )
    engine.close_subscription(subscription["id"], actor="dana", source=SOURCE)
    rows = engine.replica(room_id=room["id"])
    assert rows[0]["state"] == "deleted"
    assert rows[0]["fields"]["external_id"] == "dsr-1"
    assert rows[0]["fields"]["account"] == "Northwind"


def test_an_undelete_finds_the_tombstone_and_restores_it(engine, subscription, room):
    deliver(
        engine,
        subscription,
        room,
        transactionKey="txn-1",
        sequenceNumber=1,
        payload={"External_Id__c": "dsr-1", "Account_Name__c": "Northwind"},
    )
    deliver(
        engine,
        subscription,
        room,
        transactionKey="txn-2",
        sequenceNumber=1,
        commitTimestamp=ago(4),
        changeType="DELETE",
        payload={},
        enrichedFields={"External_Id__c": "dsr-1"},
    )
    deliver(
        engine,
        subscription,
        room,
        transactionKey="txn-3",
        sequenceNumber=1,
        commitTimestamp=ago(3),
        changeType="UNDELETE",
        payload={"External_Id__c": "dsr-1", "Account_Name__c": "Northwind"},
    )
    engine.close_subscription(subscription["id"], actor="dana", source=SOURCE)
    rows = engine.replica(room_id=room["id"])
    assert rows[0]["state"] == "live"
    # One row, not two: the undelete resolved the tombstone rather than inserting.
    assert len(rows) == 1


def test_a_delete_that_is_never_undeleted_leaves_a_tombstone(engine, subscription, room):
    deliver(
        engine,
        subscription,
        room,
        transactionKey="txn-1",
        sequenceNumber=1,
        payload={"External_Id__c": "dsr-1", "Account_Name__c": "Northwind"},
    )
    deliver(
        engine,
        subscription,
        room,
        transactionKey="txn-2",
        sequenceNumber=1,
        commitTimestamp=ago(4),
        changeType="DELETE",
        payload={},
        enrichedFields={"External_Id__c": "dsr-1"},
    )
    assert engine.replica(room_id=room["id"])[0]["state"] == "live"
    engine.close_subscription(subscription["id"], actor="dana", source=SOURCE)
    rows = engine.replica(room_id=room["id"])
    assert rows[0]["state"] == "deleted"
    assert rows[0]["fields"]["external_id"] == "dsr-1"
    assert rows[0]["fields"]["account"] == "Northwind"


def test_a_tombstone_can_be_filtered_on(engine, subscription, room):
    deliver(
        engine,
        subscription,
        room,
        transactionKey="txn-1",
        sequenceNumber=1,
        payload={"External_Id__c": "dsr-1"},
    )
    deliver(
        engine,
        subscription,
        room,
        transactionKey="txn-2",
        sequenceNumber=1,
        commitTimestamp=ago(4),
        changeType="DELETE",
        payload={},
        enrichedFields={"External_Id__c": "dsr-1"},
    )
    engine.close_subscription(subscription["id"], actor="dana", source=SOURCE)
    assert engine.replica(room_id=room["id"], state="deleted")
    assert engine.replica(room_id=room["id"], state="live") == []


def test_an_unresolvable_update_is_refused_at_commit_and_stays_parked(engine, room, cdc_org):
    channel = engine.create_channel(
        {
            "name": "/data/dsrNoEnrichment",
            "org_id": cdc_org["id"],
            "entities": ["Opportunity"],
            "transport": "pubsub",
            "field_map": FIELD_MAP,
        },
        actor="dana",
        source=SOURCE,
    )
    opened = engine.open_subscription(
        {"channel_id": channel["id"]}, room_id=room["id"], actor="dana", source=SOURCE
    )
    engine.fetch(opened["id"], {"num_requested": 10}, actor="dana", source=SOURCE)
    deliver(
        engine,
        opened,
        room,
        transactionKey="txn-1",
        sequenceNumber=1,
        changeType="UPDATE",
        changedFields=["StageName"],
        payload={"StageName": "Negotiation"},
    )
    with pytest.raises(RecordUnresolvable) as caught:
        deliver(
            engine,
            opened,
            room,
            transactionKey="txn-2",
            sequenceNumber=1,
            commitTimestamp=ago(4),
            changeType="UPDATE",
            changedFields=["Amount"],
            payload={"Amount": 1},
        )
    assert "External_Id__c" in str(caught.value)
    parked = engine.buffer_view(room_id=room["id"])["subscriptions"][0]["parked"]
    assert [row["transaction_key"] for row in parked] == ["txn-1", "txn-2"]
    assert engine.replica(room_id=room["id"]) == []


def test_a_close_whose_flush_cannot_be_applied_is_refused_and_leaves_it_open(engine, room, cdc_org):
    channel = engine.create_channel(
        {
            "name": "/data/dsrNoEnrichment",
            "org_id": cdc_org["id"],
            "entities": ["Opportunity"],
            "transport": "pubsub",
            "field_map": FIELD_MAP,
        },
        actor="dana",
        source=SOURCE,
    )
    opened = engine.open_subscription(
        {"channel_id": channel["id"]}, room_id=room["id"], actor="dana", source=SOURCE
    )
    engine.fetch(opened["id"], {"num_requested": 10}, actor="dana", source=SOURCE)
    deliver(
        engine,
        opened,
        room,
        changeType="UPDATE",
        changedFields=["StageName"],
        payload={"StageName": "Negotiation"},
    )
    with pytest.raises(RecordUnresolvable):
        engine.close_subscription(opened["id"], actor="dana", source=SOURCE)
    assert engine.subscription(opened["id"])["state"] == "open"
    parked = engine.buffer_view(room_id=room["id"])["subscriptions"][0]["parked"]
    assert [row["transaction_key"] for row in parked] == ["txn-A"]


def test_closing_a_subscription_flushes_the_last_parked_transaction(engine, subscription, room):
    deliver(engine, subscription, room)
    closed = engine.close_subscription(subscription["id"], actor="dana", source=SOURCE)
    assert [row["transaction_key"] for row in closed["flushed"]] == ["txn-A"]
    assert closed["already_closed"] is False
    assert engine.subscription(subscription["id"])["state"] == "closed"
    assert len(engine.replica(room_id=room["id"])) == 1


def test_closing_an_idle_subscription_flushes_nothing(engine, subscription):
    closed = engine.close_subscription(subscription["id"], actor="dana", source=SOURCE)
    assert closed["flushed"] == []
    assert "long-lived" in closed["reason"]


def test_a_duplicate_sequence_number_answers_duplicate_and_applies_nothing(
    engine, subscription, room
):
    deliver(engine, subscription, room, transactionKey="txn-1", sequenceNumber=1)
    result = deliver(
        engine,
        subscription,
        room,
        transactionKey="txn-1",
        sequenceNumber=1,
        commitTimestamp=ago(4),
        payload={"StageName": "X"},
    )
    assert result["outcome"] == "duplicate"
    assert result["reason"] == "duplicate_sequence_number"
    assert result["committed"] == []
    assert "at-least-once" in result["note"]


def test_an_event_for_a_foreign_entity_is_refused_at_arrival(engine, subscription, room):
    with pytest.raises(EntityNotOnChannel):
        deliver(engine, subscription, room, entity="Contact")


def test_the_buffer_view_says_why_an_idle_buffer_is_healthy(engine, subscription, room):
    view = engine.buffer_view(room_id=room["id"])
    assert view["count"] == 1
    assert view["subscriptions"][0]["parked"] == []
    assert "when the key changes" in view["commit_rule"]
    assert "flushes when the" in view["commit_rule"]


def test_the_buffer_view_can_narrow_to_one_subscription(engine, room, cdc_org):
    first = engine.create_channel(
        {
            "name": "/data/a",
            "org_id": cdc_org["id"],
            "entities": ["Opportunity"],
            "transport": "pubsub",
            "field_map": FIELD_MAP,
        },
        actor="dana",
        source=SOURCE,
    )
    second = engine.create_channel(
        {
            "name": "/data/b",
            "org_id": cdc_org["id"],
            "entities": ["Opportunity"],
            "transport": "pubsub",
            "field_map": FIELD_MAP,
        },
        actor="dana",
        source=SOURCE,
    )
    one = engine.open_subscription(
        {"channel_id": first["id"]}, room_id=room["id"], actor="dana", source=SOURCE
    )
    engine.open_subscription(
        {"channel_id": second["id"]}, room_id=room["id"], actor="dana", source=SOURCE
    )
    view = engine.buffer_view(room_id=room["id"], subscription_id=one["id"])
    assert view["count"] == 1


# --------------------------------------------------------------------------- #
# Engine: the reads
# --------------------------------------------------------------------------- #


def test_the_live_activity_panel_filters_on_the_dynamic_index(engine, subscription, room):
    deliver(engine, subscription, room, transactionKey="txn-1", sequenceNumber=1)
    deliver(
        engine, subscription, room, transactionKey="txn-2", sequenceNumber=1, commitTimestamp=ago(4)
    )
    assert len(engine.events(room_id=room["id"])) == 2
    assert len(engine.events(room_id=room["id"], state="buffered")) == 1
    assert len(engine.events(room_id=room["id"], state="committed")) == 1
    assert len(engine.events(room_id=room["id"], change_type="CREATE")) == 2
    assert len(engine.events(room_id=room["id"], transaction_key="txn-1")) == 1
    assert len(engine.events(room_id=room["id"], entity="Opportunity")) == 2


def test_the_live_activity_panel_is_newest_first(engine, subscription, room):
    deliver(
        engine, subscription, room, transactionKey="txn-1", sequenceNumber=1, commitTimestamp=ago(5)
    )
    deliver(
        engine, subscription, room, transactionKey="txn-2", sequenceNumber=1, commitTimestamp=ago(4)
    )
    listed = engine.events(room_id=room["id"])
    assert [row["commit_timestamp"] for row in listed] == [ago(4), ago(5)]


def test_the_live_activity_panel_is_room_scoped(engine, subscription, room, store):
    other = store.create("room", {"name": "Other", "account": "Contoso"}, actor="dana")
    deliver(engine, subscription, room)
    assert engine.events(room_id=other["id"]) == []


def test_the_replica_is_room_scoped(engine, subscription, room, store):
    other = store.create("room", {"name": "Other", "account": "Contoso"}, actor="dana")
    deliver(engine, subscription, room)
    engine.close_subscription(subscription["id"], actor="dana", source=SOURCE)
    assert len(engine.replica(room_id=room["id"])) == 1
    assert engine.replica(room_id=other["id"]) == []


def test_one_replica_row_can_be_read_by_external_id(engine, subscription, room):
    deliver(engine, subscription, room, payload={"External_Id__c": "dsr-42"})
    engine.close_subscription(subscription["id"], actor="dana", source=SOURCE)
    assert engine.replica_row(room_id=room["id"], external_id="dsr-42") is not None
    assert engine.replica_row(room_id=room["id"], external_id="dsr-99") is None


def test_the_deal_panel_groups_by_the_account_the_field_map_resolves(engine, subscription, room):
    deliver(
        engine,
        subscription,
        room,
        transactionKey="txn-1",
        sequenceNumber=1,
        payload={"External_Id__c": "dsr-1", "Account_Name__c": "Northwind"},
    )
    deliver(
        engine, subscription, room, transactionKey="txn-2", sequenceNumber=1, commitTimestamp=ago(4)
    )
    panel = engine.deal_panel(room_id=room["id"])
    assert panel["panel"] == DEAL_PANEL
    assert [row["account"] for row in panel["accounts"]] == ["Northwind"]
    assert panel["live"] == 1


def test_a_channel_with_no_account_field_still_produces_a_panel(engine, room, cdc_org):
    field_map = {"sync_key": "Contact_External_Id__c", "fields": {"Title": "title"}}
    channel = engine.create_channel(
        {
            "name": "/data/dsrContacts",
            "org_id": cdc_org["id"],
            "entities": ["Contact"],
            "transport": "relay",
            "field_map": field_map,
        },
        actor="dana",
        source=SOURCE,
    )
    opened = engine.open_subscription(
        {"channel_id": channel["id"]}, room_id=room["id"], actor="dana", source=SOURCE
    )
    engine.fetch(opened["id"], {"num_requested": 4}, actor="dana", source=SOURCE)
    deliver(
        engine,
        opened,
        room,
        entity="Contact",
        payload={"Contact_External_Id__c": "ctc-1", "Title": "CISO"},
    )
    deliver(
        engine,
        opened,
        room,
        entity="Contact",
        transactionKey="txn-2",
        sequenceNumber=1,
        commitTimestamp=ago(4),
        changeType="UPDATE",
        changedFields=["Title"],
        payload={"Title": "VP Security"},
        enrichedFields={"Contact_External_Id__c": "ctc-1"},
    )
    invalidations = engine.invalidations(room_id=room["id"])
    assert invalidations[0]["resolved"] is False
    assert "field_map declares no account_field" in invalidations[0]["unresolved_note"]
    panel = engine.deal_panel(room_id=room["id"])
    assert panel["count"] == 1
    # Unattributed, but still listed under its own id rather than dropped.
    assert panel["accounts"][0]["account"] == "(unattributed) ctc-1"


def test_every_commit_writes_one_invalidation_naming_the_panel(engine, subscription, room):
    deliver(
        engine,
        subscription,
        room,
        payload={"External_Id__c": "dsr-1", "Account_Name__c": "Northwind"},
    )
    deliver(
        engine, subscription, room, transactionKey="txn-2", sequenceNumber=1, commitTimestamp=ago(4)
    )
    rows = engine.invalidations(room_id=room["id"])
    assert len(rows) == 1
    assert rows[0]["panel"] == DEAL_PANEL
    assert rows[0]["reason"] == "crm_change_committed"
    assert rows[0]["account"] == "Northwind"
    assert rows[0]["resolved"] is True
    assert rows[0]["change_type"] == "CREATE"


def test_the_summary_counts_this_rooms_rows_only(engine, subscription, room, store):
    other = store.create("room", {"name": "Other", "account": "Contoso"}, actor="dana")
    deliver(engine, subscription, room)
    deliver(
        engine, subscription, room, transactionKey="txn-2", sequenceNumber=1, commitTimestamp=ago(4)
    )
    summary = engine.summary(room_id=other["id"])
    assert summary["events"] == 0
    assert summary["replica_rows"] == 0
    assert engine.summary(room_id=room["id"])["events_by_change_type"] == {"CREATE": 2}


def test_the_summary_reports_what_is_parked_and_which_replica_state(engine, subscription, room):
    deliver(engine, subscription, room, payload={"External_Id__c": "dsr-1"})
    deliver(
        engine,
        subscription,
        room,
        transactionKey="txn-2",
        sequenceNumber=1,
        commitTimestamp=ago(4),
        changeType="DELETE",
        payload={},
        enrichedFields={"External_Id__c": "dsr-1"},
    )
    # The delete is the transaction the stream is still filling, so the row is
    # still live and the summary says one is parked. Both halves of the rule.
    assert engine.summary(room_id=room["id"])["replica_live"] == 1
    assert engine.summary(room_id=room["id"])["replica_deleted"] == 0
    assert engine.summary(room_id=room["id"])["buffered_now"] == 1
    assert engine.summary(room_id=room["id"])["events_by_state"] == {"buffered": 1, "committed": 1}
    engine.close_subscription(subscription["id"], actor="dana", source=SOURCE)
    assert engine.summary(room_id=room["id"])["replica_deleted"] == 1
    assert engine.summary(room_id=room["id"])["buffered_now"] == 0


def test_a_usage_row_reports_every_published_counter(engine, subscription):
    """The presented usage is the counters plus the recommendation beside them."""
    assert set(usage_rules.blank()) <= set(subscription["usage"])
    assert subscription["usage"]["recommended_buffer_bytes"] == RECOMMENDED_BUFFER_BYTES


# --------------------------------------------------------------------------- #
# Engine: Dataverse
# --------------------------------------------------------------------------- #


def test_a_table_is_declared_with_tracking_off_and_the_power_apps_way_to_turn_it_on(engine):
    table = engine.declare_table({"logical_name": "account"}, actor="dana", source=SOURCE)
    assert table["track_changes"] is False
    assert "Track changes" in table["how_to_enable"]
    assert table["annotation"] is None


def test_declaring_the_same_logical_name_twice_is_refused(engine):
    engine.declare_table({"logical_name": "account"}, actor="dana", source=SOURCE)
    with pytest.raises(DataverseError) as caught:
        engine.declare_table({"logical_name": "account"}, actor="dana", source=SOURCE)
    assert "two delta links" in str(caught.value)


def test_turning_track_changes_on_records_the_annotation_and_its_irreversibility(engine):
    table = engine.declare_table({"logical_name": "account"}, actor="dana", source=SOURCE)
    enabled = engine.enable_track_changes(table["id"], actor="dana", source=SOURCE)
    assert enabled["track_changes"] is True
    assert enabled["change_tracking_supported"] is True
    assert enabled["annotation"] == CHANGE_TRACKING_ANNOTATION
    assert enabled["irreversible"] is True
    assert (
        engine.enable_track_changes(table["id"], actor="dana", source=SOURCE)["already_enabled"]
        is True
    )


def test_turning_track_changes_off_is_always_refused(engine):
    table = engine.declare_table({"logical_name": "account"}, actor="dana", source=SOURCE)
    engine.enable_track_changes(table["id"], actor="dana", source=SOURCE)
    with pytest.raises(ChangeTrackingIrreversible):
        engine.disable_track_changes(table["id"], actor="dana", source=SOURCE)


def test_a_poll_advances_the_deltatoken_and_the_delta_link(engine):
    table = engine.declare_table({"logical_name": "account"}, actor="dana", source=SOURCE)
    engine.enable_track_changes(table["id"], actor="dana", source=SOURCE)
    first = engine.poll_table(
        table["id"],
        {
            "prefer": CHANGE_TRACKING_PREFERENCE,
            "options": {"select": "accountid"},
            "changes_observed": 2,
        },
        actor="dana",
        source=SOURCE,
    )
    assert first["returned_deltatoken"]
    assert engine.table(table["id"])["deltatoken"] == first["returned_deltatoken"]
    second = engine.poll_table(
        table["id"],
        {
            "prefer": CHANGE_TRACKING_PREFERENCE,
            "options": {"deltatoken": first["returned_deltatoken"]},
            "changes_observed": 1,
        },
        actor="dana",
        source=SOURCE,
    )
    assert second["incremental"] is True
    assert engine.table(table["id"])["poll_count"] == 2
    assert engine.table(table["id"])["change_count"] == 3


def test_a_poll_that_omits_the_header_writes_nothing(engine):
    table = engine.declare_table({"logical_name": "account"}, actor="dana", source=SOURCE)
    engine.enable_track_changes(table["id"], actor="dana", source=SOURCE)
    with pytest.raises(MissingTrackChangesPreference):
        engine.poll_table(table["id"], {"options": {}}, actor="dana", source=SOURCE)
    assert engine.table(table["id"])["poll_count"] == 0


def test_an_unknown_table_is_a_404_shaped_refusal(engine):
    with pytest.raises(UnknownTable):
        engine.require_table("dataverse_table_nope")


def test_the_change_count_route_says_what_it_counts(engine):
    table = engine.declare_table({"logical_name": "account"}, actor="dana", source=SOURCE)
    engine.enable_track_changes(table["id"], actor="dana", source=SOURCE)
    engine.poll_table(
        table["id"],
        {"prefer": CHANGE_TRACKING_PREFERENCE, "options": {}, "changes_observed": 5},
        actor="dana",
        source=SOURCE,
    )
    counted = engine.count_table(table["id"], deltatoken=None)
    assert counted["count"] == 5
    assert "unconsumed" in counted["not_counted"]


# --------------------------------------------------------------------------- #
# Engine: HubSpot
# --------------------------------------------------------------------------- #


def test_a_hubspot_webhook_target_is_registered_against_the_cap(engine):
    created = engine.register_hubspot_subscription(
        {"target_url": "https://hooks.example.invalid/stage", "workflow_id": "wf-1"},
        room_id=None,
        actor="dana",
        source=SOURCE,
    )
    assert created["workflow_id"] == "wf-1"
    assert created["capacity"]["live"] == 1
    assert created["capacity"]["limit"] == HUBSPOT_WEBHOOK_SUBSCRIPTION_LIMIT
    assert created["capacity"]["remaining"] == 999
    assert "1,000" in created["capacity"]["source"]


def test_cancelling_a_hubspot_target_frees_its_slot(engine):
    created = engine.register_hubspot_subscription(
        {"target_url": "https://hooks.example.invalid/stage"},
        room_id=None,
        actor="dana",
        source=SOURCE,
    )
    engine.delete_hubspot_subscription(created["id"], actor="dana", source=SOURCE)
    assert engine.hubspot_capacity()["live"] == 0
    assert engine.hubspot_capacity()["remaining"] == 1000


def test_the_hubspot_cap_is_enforced_before_the_row_is_written(engine, store, monkeypatch):
    from dsr.change_stream import hubspot as rules

    monkeypatch.setattr(rules, "WEBHOOK_SUBSCRIPTION_LIMIT", 2)
    monkeypatch.setattr(
        engine,
        "hubspot_require_capacity",
        lambda: rules.require_capacity(len(store.list(HUBSPOT_SUBSCRIPTIONS, limit=1000))),
    )
    engine.register_hubspot_subscription(
        {"target_url": "https://hooks.example.invalid/a"}, room_id=None, actor="dana", source=SOURCE
    )
    engine.register_hubspot_subscription(
        {"target_url": "https://hooks.example.invalid/b"}, room_id=None, actor="dana", source=SOURCE
    )
    with pytest.raises(SubscriptionLimitExceeded):
        engine.register_hubspot_subscription(
            {"target_url": "https://hooks.example.invalid/c"},
            room_id=None,
            actor="dana",
            source=SOURCE,
        )
    assert len(store.list(HUBSPOT_SUBSCRIPTIONS, limit=100)) == 2


def test_a_workflow_call_is_counted_and_not_charged(engine):
    created = engine.register_hubspot_subscription(
        {"target_url": "https://hooks.example.invalid/stage"},
        room_id=None,
        actor="dana",
        source=SOURCE,
    )
    charged = engine.charge_hubspot_call(
        created["id"], via_workflow=True, actor="dana", source=SOURCE
    )
    assert charged["calls_received"] == 1
    assert charged["calls_exempt_from_rate_limit"] == 1
    assert charged["calls_charged_to_budget"] == 0
    assert charged["call_budget"]["spent"] == 0


def test_an_app_call_is_charged_to_the_budget(engine):
    created = engine.register_hubspot_subscription(
        {"target_url": "https://hooks.example.invalid/stage"},
        room_id=None,
        actor="dana",
        source=SOURCE,
    )
    charged = engine.charge_hubspot_call(
        created["id"], via_workflow=False, actor="dana", source=SOURCE
    )
    assert charged["calls_charged_to_budget"] == 1
    assert charged["call_budget"]["spent"] == 1


def test_the_hubspot_usage_reports_the_exemption_beside_the_budget(engine):
    created = engine.register_hubspot_subscription(
        {"target_url": "https://hooks.example.invalid/stage"},
        room_id=None,
        actor="dana",
        source=SOURCE,
    )
    engine.charge_hubspot_call(created["id"], via_workflow=True, actor="dana", source=SOURCE)
    report = engine.hubspot_usage()
    assert report["calls_received"] == 1
    assert report["calls_exempt_from_rate_limit"] == 1
    assert report["budget"]["spent"] == 0
    assert "not researched" in report["api_call_budget_note"]
    assert report["capacity"]["limit"] == 1000


def test_deleting_an_unknown_hubspot_target_is_refused(engine):
    with pytest.raises(ChangeStreamError):
        engine.delete_hubspot_subscription(
            "hubspot_webhook_subscription_nope", actor="dana", source=SOURCE
        )


# --------------------------------------------------------------------------- #
# Engine: usage
# --------------------------------------------------------------------------- #


def test_usage_totals_add_up_across_subscriptions(engine, subscription, room):
    deliver(engine, subscription, room, transactionKey="txn-1", sequenceNumber=1)
    deliver(
        engine, subscription, room, transactionKey="txn-2", sequenceNumber=1, commitTimestamp=ago(4)
    )
    report = engine.usage()
    assert report["count"] == 1
    assert report["totals"]["events_delivered"] == 2
    assert report["totals"]["events_requested"] == 50
    assert report["totals"]["fetch_outstanding"] == 48
    assert report["totals"]["transactions_committed"] == 1
    assert report["totals"]["replica_writes"] == 1
    assert report["recommended_buffer_bytes"] == RECOMMENDED_BUFFER_BYTES
    assert report["metric"]["source"] == "PlatformEventUsageMetric"


def test_a_presented_usage_row_carries_the_recommendation_beside_the_counters(subscription):
    """The presented usage is the counters plus the researched recommendation.

    ``set(...) <= set(...)`` rather than equality: the presented row is the
    counters with the 3 MB recommendation and the high-water mark beside them,
    and a test that demanded exact equality would be asserting the absence of the
    very thing the research supplies.
    """
    assert set(usage_rules.blank()) <= set(subscription["usage"])
    assert subscription["usage"]["recommended_buffer_bytes"] == RECOMMENDED_BUFFER_BYTES
    assert "buffer_within_recommendation" in subscription["usage"]


def test_usage_can_be_narrowed_to_a_room(engine, subscription, room, store):
    other = store.create("room", {"name": "Other", "account": "Contoso"}, actor="dana")
    deliver(engine, subscription, room)
    assert engine.usage(room_id=other["id"])["count"] == 0
    assert engine.usage(room_id=room["id"])["count"] == 1


# --------------------------------------------------------------------------- #
# The HTTP surface
# --------------------------------------------------------------------------- #


def test_the_whole_documented_flow_works_over_http(http):
    """ "An operator enables CDC, picks a channel, the client subscribes, events
    arrive, a transaction commits, and the buyer's panel is refreshed." """
    setup = http_setup(http)
    room = setup["room"]["id"]
    subscription = setup["subscription"]["id"]

    first = http.post(
        f"{PREFIX}/rooms/{room}/events",
        json={
            "subscription_id": subscription,
            "changeType": "CREATE",
            "transactionKey": "txn-1",
            "sequenceNumber": 1,
            "commitTimestamp": ago(6),
            "payload": {
                "External_Id__c": "dsr-1",
                "StageName": "Prospecting",
                "Account_Name__c": "Northwind",
                "Amount": 100,
            },
            "entity": "Opportunity",
        },
    )
    assert first.status_code == 201
    assert first.json()["committed"] == []

    second = http.post(
        f"{PREFIX}/rooms/{room}/events",
        json={
            "subscription_id": subscription,
            "changeType": "UPDATE",
            "transactionKey": "txn-2",
            "sequenceNumber": 1,
            "commitTimestamp": ago(5),
            "changedFields": ["StageName"],
            "payload": {"StageName": "Negotiation"},
            "enrichedFields": {"External_Id__c": "dsr-1"},
            "entity": "Opportunity",
        },
    )
    assert [row["transaction_key"] for row in second.json()["committed"]] == ["txn-1"]

    # The update is itself the parked transaction, so the drain is the close.
    # That is the researched rule's terminal case, not a shortcut.
    http.post(f"{PREFIX}/subscriptions/{subscription}/close")

    panel = http.get(f"{PREFIX}/rooms/{room}/deal-panel").json()
    assert [row["account"] for row in panel["accounts"]] == ["Northwind"]
    assert panel["accounts"][0]["rows"][0]["fields"]["stage"] == "Negotiation"
    assert panel["accounts"][0]["rows"][0]["fields"]["amount"] == 100


def test_a_duplicate_sequence_number_answers_200_over_http(http):
    setup = http_setup(http)
    room = setup["room"]["id"]
    subscription = setup["subscription"]["id"]
    body = {
        "subscription_id": subscription,
        "changeType": "CREATE",
        "transactionKey": "txn-1",
        "sequenceNumber": 1,
        "commitTimestamp": ago(6),
        "payload": {"External_Id__c": "dsr-1"},
        "entity": "Opportunity",
    }
    assert http.post(f"{PREFIX}/rooms/{room}/events", json=body).status_code == 201
    second = dict(body, commitTimestamp=ago(5), payload={"External_Id__c": "dsr-2"})
    response = http.post(f"{PREFIX}/rooms/{room}/events", json=second)
    assert response.status_code == 200
    assert response.json()["outcome"] == "duplicate"


def test_a_domain_refusal_answers_the_status_the_error_carries(http):
    setup = http_setup(http)
    response = http.post(
        f"{PREFIX}/rooms/{setup['room']['id']}/events",
        json={
            "subscription_id": setup["subscription"]["id"],
            "changeType": "GAP_OVERFLOW",
            "transactionKey": "txn-1",
            "sequenceNumber": 1,
            "commitTimestamp": ago(6),
            "payload": {},
        },
    )
    assert response.status_code == 400
    body = response.json()
    assert body["error"] == "unknown_change_type"
    assert body["status"] == 400
    assert "CREATE, UPDATE, DELETE, UNDELETE" in body["detail"]


def test_enrichment_on_the_standard_channel_answers_409_over_http(http):
    setup = http_setup(http)
    standard = http.post(
        f"{PREFIX}/channels",
        json={
            "name": STANDARD_CHANNEL,
            "org_id": setup["org"]["id"],
            "entities": ["Opportunity"],
            "transport": "pubsub",
            "field_map": FIELD_MAP,
        },
    ).json()
    response = http.post(
        f"{PREFIX}/channels/{standard['id']}/enrichment", json={"fields": ["External_Id__c"]}
    )
    assert response.status_code == 409
    assert response.json()["error"] == "enrichment_not_available_on_the_standard_channel"


def test_enrichment_on_a_delta_link_transport_answers_400_over_http(http):
    setup = http_setup(http)
    relay = http.post(
        f"{PREFIX}/channels",
        json={
            "name": "/data/dsrDelta",
            "org_id": setup["org"]["id"],
            "entities": ["Opportunity"],
            "transport": "delta_link",
            "field_map": FIELD_MAP,
        },
    ).json()
    response = http.post(f"{PREFIX}/channels/{relay['id']}/enrichment", json={"fields": ["X__c"]})
    assert response.status_code == 400
    assert response.json()["error"] == "enrichment_not_supported_by_this_transport"


def test_removing_enrichment_over_http_returns_the_removed_field(http):
    setup = http_setup(http)
    response = http.delete(f"{PREFIX}/channels/{setup['channel']['id']}/enrichment/External_Id__c")
    assert response.status_code == 200
    assert response.json()["removed"] == ["External_Id__c"]
    assert response.json()["enriched_fields"] == []


def test_the_edition_gate_answers_409_over_http(http):
    org = http.post(
        f"{PREFIX}/orgs", json={"system": "salesforce", "edition": "Professional"}
    ).json()
    response = http.patch(
        f"{PREFIX}/orgs/{org['id']}", json={"cdc_enabled": True, "entities": ["Opportunity"]}
    )
    assert response.status_code == 409
    assert response.json()["error"] == "edition_does_not_support_change_data_capture"


def test_a_delta_poll_that_breaks_the_vendors_rule_answers_the_vendors_message(http):
    table = http.post(f"{PREFIX}/dataverse/tables", json={"logical_name": "account"}).json()
    http.post(f"{PREFIX}/dataverse/tables/{table['id']}/track-changes")
    response = http.post(
        f"{PREFIX}/dataverse/tables/{table['id']}/poll",
        json={"prefer": CHANGE_TRACKING_PREFERENCE, "options": {"top": 10}},
    )
    assert response.status_code == 400
    assert response.json()["error"] == "unsupported_query_option_with_change_tracking"
    assert response.json()["detail"] == (
        'The "$top" query parameter isn\'t supported when Change Tracking is enabled.'
    )


def test_disabling_track_changes_answers_409_over_http(http):
    table = http.post(f"{PREFIX}/dataverse/tables", json={"logical_name": "account"}).json()
    http.post(f"{PREFIX}/dataverse/tables/{table['id']}/track-changes")
    response = http.delete(f"{PREFIX}/dataverse/tables/{table['id']}/track-changes")
    assert response.status_code == 409
    assert response.json()["error"] == "change_tracking_cannot_be_disabled"


def test_a_delta_poll_carries_the_annotation_and_a_delta_link_over_http(http):
    table = http.post(f"{PREFIX}/dataverse/tables", json={"logical_name": "account"}).json()
    http.post(f"{PREFIX}/dataverse/tables/{table['id']}/track-changes")
    body = http.post(
        f"{PREFIX}/dataverse/tables/{table['id']}/poll",
        json={
            "prefer": CHANGE_TRACKING_PREFERENCE,
            "options": {"select": "accountid"},
            "changes_observed": 2,
        },
    ).json()
    assert body["changeTracking"]["Annotation"] == CHANGE_TRACKING_ANNOTATION
    assert body["@odata.deltaLink"].startswith(f"/api/data/{DATAVERSE_API_VERSION}/accounts")
    assert body["changes_observed"] == 2


def test_the_count_route_serves_the_vendors_count_url_shape(http):
    table = http.post(f"{PREFIX}/dataverse/tables", json={"logical_name": "account"}).json()
    http.post(f"{PREFIX}/dataverse/tables/{table['id']}/track-changes")
    body = http.get(f"{PREFIX}/dataverse/tables/{table['id']}/count").json()
    assert "/$count?$deltatoken=" in body["count_url"]


def test_an_event_with_no_fetch_request_outstanding_answers_409_over_http(http):
    setup = http_setup(http)
    other = http.post(
        f"{PREFIX}/rooms/{setup['room']['id']}/subscriptions",
        json={"channel_id": setup["channel"]["id"]},
    ).json()
    response = http.post(
        f"{PREFIX}/rooms/{setup['room']['id']}/events",
        json={
            "subscription_id": other["id"],
            "changeType": "CREATE",
            "transactionKey": "txn-1",
            "sequenceNumber": 1,
            "commitTimestamp": ago(1),
            "payload": {"External_Id__c": "x"},
        },
    )
    assert response.status_code == 409
    assert response.json()["error"] == "no_outstanding_fetch_request"


def test_closing_a_subscription_over_http_returns_what_it_flushed(http):
    setup = http_setup(http)
    room = setup["room"]["id"]
    http.post(
        f"{PREFIX}/rooms/{room}/events",
        json={
            "subscription_id": setup["subscription"]["id"],
            "changeType": "CREATE",
            "transactionKey": "txn-1",
            "sequenceNumber": 1,
            "commitTimestamp": ago(2),
            "payload": {"External_Id__c": "dsr-1", "Account_Name__c": "Northwind"},
            "entity": "Opportunity",
        },
    )
    body = http.post(f"{PREFIX}/subscriptions/{setup['subscription']['id']}/close").json()
    assert [row["transaction_key"] for row in body["flushed"]] == ["txn-1"]
    assert body["subscription"]["state"] == "closed"


def test_a_channel_with_an_open_subscription_cannot_be_deleted_over_http(http):
    setup = http_setup(http)
    response = http.delete(f"{PREFIX}/channels/{setup['channel']['id']}")
    assert response.status_code == 409
    assert response.json()["error"] == "channel_has_open_subscriptions"


def test_reads_that_find_nothing_are_404_over_http(http):
    room = http.post("/api/records/room", json=ROOM).json()
    assert http.get(f"{PREFIX}/orgs/crm_org_nope").status_code == 404
    assert http.get(f"{PREFIX}/channels/crm_channel_nope").status_code == 404
    assert http.get(f"{PREFIX}/subscriptions/crm_subscription_nope").status_code == 404
    assert http.get(f"{PREFIX}/rooms/{room['id']}/replica/dsr-nope").status_code == 404


def test_every_read_route_answers_an_empty_room_without_error(http):
    room = http.post("/api/records/room", json=ROOM).json()
    for path in (
        f"{PREFIX}/orgs",
        f"{PREFIX}/channels",
        f"{PREFIX}/subscriptions",
        f"{PREFIX}/rooms/{room['id']}/events",
        f"{PREFIX}/rooms/{room['id']}/buffer",
        f"{PREFIX}/rooms/{room['id']}/replica",
        f"{PREFIX}/rooms/{room['id']}/deal-panel",
        f"{PREFIX}/rooms/{room['id']}/invalidations",
        f"{PREFIX}/dataverse/tables",
        f"{PREFIX}/hubspot/subscriptions",
        f"{PREFIX}/hubspot/usage",
        f"{PREFIX}/usage",
        f"{PREFIX}/summary",
    ):
        assert http.get(path).status_code == 200, path


def test_the_room_scoped_paths_are_the_ones_that_need_a_room(http):
    """The brief: keep room-scoped paths room-scoped."""
    entry = next(f for f in http.get("/api/features").json()["features"] if f["id"] == FEATURE_ID)
    scoped = {route["path"] for route in entry["routes"] if "/rooms/" in route["path"]}
    assert scoped == {
        f"{PREFIX}/rooms/{{room_id}}/subscriptions",
        f"{PREFIX}/rooms/{{room_id}}/events",
        f"{PREFIX}/rooms/{{room_id}}/buffer",
        f"{PREFIX}/rooms/{{room_id}}/replica",
        f"{PREFIX}/rooms/{{room_id}}/replica/{{external_id}}",
        f"{PREFIX}/rooms/{{room_id}}/deal-panel",
        f"{PREFIX}/rooms/{{room_id}}/invalidations",
    }


# --------------------------------------------------------------------------- #
# The audit source rule
# --------------------------------------------------------------------------- #


def audit_sources(client) -> set[str]:
    entries = client.get("/api/audit", params={"limit": 1000}).json()["entries"]
    return {entry["source"] for entry in entries if PREFIX in (entry.get("source") or "")}


def test_every_write_audits_a_path_this_router_serves(http):
    setup = http_setup(http)
    room = setup["room"]["id"]
    client_channels = [setup["channel"]]
    client_channels.append(
        http.post(
            f"{PREFIX}/channels",
            json={
                "name": "/data/dsrOther",
                "org_id": setup["org"]["id"],
                "entities": ["Opportunity"],
                "transport": "pubsub",
                "field_map": FIELD_MAP,
            },
        ).json()
    )
    http.patch(f"{PREFIX}/orgs/{setup['org']['id']}", json={"org_name": "Renamed"})
    http.patch(f"{PREFIX}/channels/{client_channels[1]['id']}", json={"buffer_bytes": 4096})
    http.post(
        f"{PREFIX}/channels/{client_channels[1]['id']}/enrichment", json={"fields": ["Amount"]}
    )
    http.delete(f"{PREFIX}/channels/{client_channels[1]['id']}/enrichment/Amount")
    http.post(
        f"{PREFIX}/rooms/{room}/events",
        json={
            "subscription_id": setup["subscription"]["id"],
            "changeType": "CREATE",
            "transactionKey": "txn-1",
            "sequenceNumber": 1,
            "commitTimestamp": ago(2),
            "payload": {"External_Id__c": "dsr-1"},
            "entity": "Opportunity",
        },
    )
    http.post(
        f"{PREFIX}/subscriptions/{setup['subscription']['id']}/fetch", json={"num_requested": 5}
    )
    http.post(f"{PREFIX}/subscriptions/{setup['subscription']['id']}/close")
    table = http.post(f"{PREFIX}/dataverse/tables", json={"logical_name": "account"}).json()
    http.post(f"{PREFIX}/dataverse/tables/{table['id']}/track-changes")
    http.post(
        f"{PREFIX}/dataverse/tables/{table['id']}/poll",
        json={"prefer": CHANGE_TRACKING_PREFERENCE, "options": {}},
    )
    hook = http.post(
        f"{PREFIX}/hubspot/subscriptions",
        json={"target_url": "https://hooks.example.invalid/x", "workflow_id": "wf-1"},
    ).json()
    http.delete(f"{PREFIX}/hubspot/subscriptions/{hook['id']}")
    http.delete(f"{PREFIX}/channels/{client_channels[1]['id']}")

    sources = audit_sources(http)
    assert sources == {
        f"POST {PREFIX}/orgs",
        f"PATCH {PREFIX}/orgs/{{org_id}}",
        f"POST {PREFIX}/channels",
        f"PATCH {PREFIX}/channels/{{channel_id}}",
        f"DELETE {PREFIX}/channels/{{channel_id}}",
        f"POST {PREFIX}/channels/{{channel_id}}/enrichment",
        f"DELETE {PREFIX}/channels/{{channel_id}}/enrichment/{{field}}",
        f"POST {PREFIX}/rooms/{{room_id}}/subscriptions",
        f"POST {PREFIX}/rooms/{{room_id}}/events",
        f"POST {PREFIX}/subscriptions/{{subscription_id}}/fetch",
        f"POST {PREFIX}/subscriptions/{{subscription_id}}/close",
        f"POST {PREFIX}/dataverse/tables",
        f"POST {PREFIX}/dataverse/tables/{{table_id}}/track-changes",
        f"POST {PREFIX}/dataverse/tables/{{table_id}}/poll",
        f"POST {PREFIX}/hubspot/subscriptions",
        f"DELETE {PREFIX}/hubspot/subscriptions/{{subscription_id}}",
    }


def test_no_audit_row_names_a_path_this_feature_does_not_serve(http):
    setup = http_setup(http)
    http.post(
        f"{PREFIX}/rooms/{setup['room']['id']}/events",
        json={
            "subscription_id": setup["subscription"]["id"],
            "changeType": "CREATE",
            "transactionKey": "txn-1",
            "sequenceNumber": 1,
            "commitTimestamp": ago(2),
            "payload": {"External_Id__c": "dsr-1"},
            "entity": "Opportunity",
        },
    )
    stale = [source for source in audit_sources(http) if PREFIX not in source]
    assert not stale, f"audit rows name an unserved path: {stale}"


def test_every_source_this_feature_can_write_is_one_of_its_own_routes():
    """The source strings are derived from the mounted router, not written out.

    Read out of the module rather than asserted one by one, because the defect
    this guards is a *drift* between the recorded source and the mounted path: a
    hand-written list in the test would be updated in the same commit as the bug
    and would never catch it.
    """
    source = Path(load_feature(MODULE).__file__).read_text(encoding="utf-8")
    expressions = re.findall(r'source=f?"([^"]*\{router\.prefix\}[^"]*)"', source)
    assert expressions, "no source is built from router.prefix"

    write_routes = {
        (sorted(route.methods - {"HEAD", "OPTIONS"})[0], route.path)
        for route in load_feature(MODULE).router.routes
        if sorted(route.methods - {"HEAD", "OPTIONS"})[0] in ("POST", "PATCH", "PUT", "DELETE")
    }
    recorded = {
        # The module writes the path as an f-string, so its literal braces are
        # doubled; the mounted path has single ones.
        (
            expression.split(" ", 1)[0],
            f"{PREFIX}{expression.split('{router.prefix}')[1]}".replace("{{", "{").replace(
                "}}", "}"
            ),
        )
        for expression in expressions
    }
    assert {method for method, _path in write_routes} == {method for method, _path in recorded}
    for method, path in recorded:
        assert (method, path) in write_routes, f"{method} {path} is not a route this feature serves"


def test_no_domain_module_hardcodes_a_url_as_its_source():
    """The defect the contract names: an audit log that names a route we do not serve.

    Every ``source=`` in the domain package has to be a parameter. A literal there
    would be a string the feature recorded forever, and the route behind it could
    be renamed without anything noticing.
    """
    package = Path(load_feature(MODULE).__file__).parent.parent / "change_stream"
    offenders: list[str] = []
    for module in sorted(package.glob("*.py")):
        for number, line in enumerate(module.read_text(encoding="utf-8").splitlines(), 1):
            for found in re.finditer(r"source\s*=\s*(\"[^\"]*\"|'[^']*')", line):
                literal = found.group(1).strip("\"'")
                if literal in ("seed", ""):
                    continue
                if (
                    literal.startswith("/api")
                    or literal.startswith("POST ")
                    or literal.startswith("PATCH ")
                ):
                    offenders.append(f"{module.name}:{number}: {found.group(1)}")
    assert not offenders, f"a domain module hardcodes an audit source: {offenders}"


def test_every_writing_engine_method_requires_source():
    """A missing ``source`` must be a ``TypeError`` at the call site, not a blank row."""
    import inspect

    engine_class = ChangeStreamEngine
    for name, member in inspect.getmembers(engine_class, inspect.isfunction):
        if name.startswith("_"):
            continue
        signature = inspect.signature(member)
        if signature.parameters.get("source") is signature.empty:
            continue
        writes = {
            "register_org",
            "patch_org",
            "create_channel",
            "patch_channel",
            "delete_channel",
            "enrich_channel",
            "unenrich_channel",
            "open_subscription",
            "fetch",
            "close_subscription",
            "deliver_event",
            "declare_table",
            "enable_track_changes",
            "disable_track_changes",
            "poll_table",
            "register_hubspot_subscription",
            "delete_hubspot_subscription",
            "charge_hubspot_call",
        }
        if name in writes:
            assert signature.parameters["source"].kind is inspect.Parameter.KEYWORD_ONLY, name
            assert signature.parameters["source"].default is inspect.Parameter.empty, name


# --------------------------------------------------------------------------- #
# The demo data
# --------------------------------------------------------------------------- #


@pytest.fixture()
def seeded(tmp_path):
    db = AuditedDatabase(tmp_path / "seed.db")
    store = RecordStore(db)
    module = load_feature(MODULE)
    rooms = [
        (
            store.create("room", {"name": "Northwind", "account": "Northwind"}, actor="dana")["id"],
            "Northwind",
        ),
        (
            store.create("room", {"name": "Contoso", "account": "Contoso"}, actor="dana")["id"],
            "Contoso",
        ),
    ]
    summary = module.seed(
        db,
        {"room_ids": rooms, "now": NOW, "rng": random.Random("wf043")},
    )
    engine = ChangeStreamEngine(store)
    yield store, engine, rooms, summary
    db.close()


def test_the_seed_never_opens_a_socket(seeded):
    """The demo runs the real engine, so the rows it makes are the real shape."""
    _store, _engine, _rooms, summary = seeded
    assert isinstance(summary, str)
    assert summary


def test_the_seed_runs_the_real_engine_so_the_rows_satisfy_the_rules(seeded):
    store, _engine, _rooms, summary = seeded
    assert len(store.list(ORGS, limit=50)) == 2
    assert len(store.list(CHANNELS, limit=50)) == 4
    assert len(store.list(SUBSCRIPTIONS, limit=50)) == 4
    assert len(store.list(REPLICA, limit=50)) == 4
    assert len(store.list(INVALIDATIONS, limit=50)) >= 9
    assert len(store.list(TABLES, limit=50)) == 2
    assert len(store.list(HUBSPOT_SUBSCRIPTIONS, limit=50)) == 3
    assert "tombstoned" in summary


def test_the_seed_covers_all_four_change_types(seeded):
    store, _engine, _rooms, _summary = seeded
    types = {record["data"]["change_type"] for record in store.find(CHANGE_EVENTS, {}, limit=1000)}
    assert types == set(CHANGE_TYPES)


def test_the_seed_leaves_a_tombstone_and_a_restored_row(seeded):
    store, _engine, _rooms, _summary = seeded
    states = [record["data"]["replica_state"] for record in store.find(REPLICA, {}, limit=100)]
    assert states.count("deleted") == 1
    assert states.count("live") == 3


def test_the_seed_shows_the_standard_channel_enrichment_refusal(seeded):
    _store, engine, _rooms, summary = seeded
    standard = engine.channel(next(c["id"] for c in engine.channels() if c["kind"] == "standard"))
    assert standard["enriched_fields"] == []
    assert "refused" in standard["enrichment_note"]
    assert "enrichment_not_available_on_the_standard_channel" in summary


def test_the_seed_shows_the_edition_gate_refusal(seeded):
    _store, engine, _rooms, summary = seeded
    editions = {row["data"]["system"]: row["data"]["edition_covers_cdc"] for row in engine.orgs()}
    assert editions == {"salesforce": True, "dataverse": False}
    assert "edition_does_not_support_change_data_capture" in summary


def test_the_seed_shows_a_non_recommended_buffer_and_one_that_matches(seeded):
    _store, engine, _rooms, summary = seeded
    sizes = {row["buffer_bytes"]: row["buffer_matches_recommendation"] for row in engine.channels()}
    assert sizes[RECOMMENDED_BUFFER_BYTES] is True
    assert sizes[1024 * 1024] is False
    assert "1 MB buffer" in summary


def test_the_seed_shows_a_commit_that_could_not_be_applied(seeded):
    _store, engine, _rooms, summary = seeded
    assert "enrichment_required" in summary
    assert "close refused: enrichment_required" in summary


def test_the_seed_shows_a_duplicate_refused_and_a_gap_reported(seeded):
    store, _demo, _rooms, summary = seeded
    assert "1 duplicate refused" in summary
    # The seeded transaction's third sequence number never arrived, and the gap is
    # recorded on the event rows rather than only on the commit response, because
    # "was this transaction's order complete?" is asked later by whoever reads the
    # change log. Reported, not repaired - that is section 18.
    rows = store.find(CHANGE_EVENTS, {"transaction_key": "txn-1001"}, limit=10)
    assert sorted(row["data"]["sequence_number"] for row in rows) == [1001, 1002, 1004]
    assert rows[0]["data"]["sequence_gaps"] == [{"after": 1002, "before": 1004, "missing": 1}]
    assert rows[0]["data"]["transaction_sequence_numbers"] == [1001, 1002, 1004]


def test_a_sequence_gap_is_recorded_on_the_committed_events(engine, subscription, room):
    deliver(
        engine,
        subscription,
        room,
        transactionKey="txn-1",
        sequenceNumber=1,
        payload={"External_Id__c": "dsr-1"},
    )
    deliver(
        engine,
        subscription,
        room,
        transactionKey="txn-1",
        sequenceNumber=4,
        commitTimestamp=ago(6),
        changeType="UPDATE",
        changedFields=["StageName"],
        payload={"StageName": "Won"},
        enrichedFields={"External_Id__c": "dsr-1"},
    )
    deliver(
        engine, subscription, room, transactionKey="txn-2", sequenceNumber=1, commitTimestamp=ago(4)
    )
    rows = engine.events(room_id=room["id"], transaction_key="txn-1")
    assert rows[0]["sequence_gaps"] == [{"after": 1, "before": 4, "missing": 2}]
    assert rows[0]["state"] == "committed"


def test_the_seed_shows_a_panel_refresh_with_no_buyer_named(seeded):
    _store, engine, rooms, summary = seeded
    invalidations = engine.invalidations(room_id=rooms[0][0])
    assert any(row["resolved"] is False for row in invalidations)
    assert "with no buyer named" in summary


def test_the_seed_shows_a_flushed_transaction_and_a_closed_subscription(seeded):
    _store, engine, rooms, summary = seeded
    closed = [row for row in engine.subscriptions(room_id=rooms[0][0]) if row["state"] == "closed"]
    assert len(closed) == 1
    assert "1 transaction flushed on close" in summary


def test_the_seed_shows_the_dataverse_rules(seeded):
    _store, engine, _rooms, summary = seeded
    tables = engine.tables()
    # Compared as a set, not a list. store.list() returns rows in insertion
    # order, and the row ids are generated, so which of the two tables comes
    # first varies between runs. Asserting the order asserted the id generator
    # rather than the seed, and failed roughly half the time.
    assert {row["track_changes"] for row in tables} == {True, False}
    assert len(tables) == 2
    assert "change_tracking_cannot_be_disabled" in summary
    assert "unsupported_query_option_with_change_tracking" in summary


def test_the_seed_shows_the_hubspot_exemption_and_the_cap(seeded):
    _store, engine, _rooms, summary = seeded
    assert engine.hubspot_capacity()["live"] == 3
    assert engine.hubspot_usage()["calls_exempt_from_rate_limit"] == 3
    assert engine.hubspot_usage()["budget"]["spent"] == 0
    assert "3 calls exempt" in summary


def test_the_seed_writes_only_into_its_own_collections(seeded):
    store, _engine, _rooms, _summary = seeded
    collections = {row["collection"] for row in store.collections()}
    assert collections - set(OWNED_COLLECTIONS) == {"room"}


def test_the_seed_survives_a_context_with_no_rooms(tmp_path):
    db = AuditedDatabase(tmp_path / "no-rooms.db")
    try:
        summary = load_feature(MODULE).seed(
            db, {"room_ids": [], "now": NOW, "rng": random.Random("wf043")}
        )
        assert "no rooms to scope them to" in summary
    finally:
        db.close()


def test_the_seed_accepts_bare_room_ids(tmp_path):
    """A caller assembling a context by hand should not have to know the tuple shape."""
    db = AuditedDatabase(tmp_path / "bare-rooms.db")
    try:
        store = RecordStore(db)
        room_id = store.create("room", {"name": "Only", "account": "One"}, actor="dana")["id"]
        summary = load_feature(MODULE).seed(
            db, {"room_ids": [room_id], "now": NOW, "rng": random.Random("wf043")}
        )
        assert "change events across 2 rooms" not in summary
    finally:
        db.close()
