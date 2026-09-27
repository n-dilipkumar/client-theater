"""Tests for WF-038: batch-upsert engagement rows keyed on the external ID.

The domain is :mod:`dsr.crm_upsert` and the HTTP surface is
``backend/dsr/features/wf038_batch_upsert_engagement_rows_keyed_on_.py``. The
split mirrors the split in the code: the researched rules are tested against the
domain, the wiring through the feature's own router.

What is pinned, and why each one matters
----------------------------------------
* **The caps are vendor limits, not preferences.** "The list can contain up to 200
  objects" and "Batch operations are limited to 100 records at a time". A
  connection asking for more is refused rather than clamped, because a silent
  clamp reports a plan the connector did not run.
* **The payload carries the external-ID field and no ``id`` field.** Asserted on
  the built body, including the feedback-loop case where the room's own
  ``crm_record_id`` write-back would otherwise be echoed into the next request.
* **Only external ids are supported. Don't use record ids.** Refused on save.
* **Results come back in request order**, so outcomes match by position and a
  wrong-length response fails the chunk rather than truncating it. The duplicate
  -key-in-one-batch case is the test that would fail under key matching.
* **``allOrNone`` is about the whole request.** One failed item means nothing in
  that chunk was written, so nothing in it is written back as synced.
* **A duplicate external id is an error, not a second write.**
* **HubSpot will not take a partial upsert by email**, so a row that cannot supply
  the complete property set is refused rather than sent.
* **Dataverse confirms nothing** ("204 NoContent"), so those rows are submitted,
  not synced, and they leave the queue so a re-run cannot loop.
* **The fallback is automatic**: a table with no bulk upsert gets one PATCH per
  row.
* **Two researched triggers**: nightly and opportunistic.
* **Nothing runs inside the CRM**: a run writes exactly two collections.
* **The audit trail names the route that served the write** - hard rule 4 of the
  build brief, and the defect the contract names by name.
"""

from __future__ import annotations

import inspect
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dsr import crm_upsert as cu
from dsr.crm_upsert import capabilities as cap
from dsr.crm_upsert import payloads as pay
from dsr.crm_upsert import runs as run_mod
from dsr.crm_upsert.errors import (
    BatchTooLarge,
    MixedObjectTypes,
    NoUpsertPath,
    UnknownConnection,
    UnknownRun,
    UnsupportedKey,
    UpsertError,
)
from dsr.db.audited import AuditedDatabase
from dsr.features import load_feature
from dsr.store import RecordStore

PREFIX = "/api/wf-038"
MODULE = "wf038_batch_upsert_engagement_rows_keyed_on_"
FEATURE_ID = "wf-038-batch-upsert-engagement-rows-keyed-on-"

NOW = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)

#: The researched caps, quoted once so a change has to be argued with here.
SALESFORCE_CAP = 200
HUBSPOT_CAP = 100
DATAVERSE_CAP = 1000


def feature_module():
    return load_feature(MODULE)


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture()
def store():
    """A store over a throwaway audited database, for the domain tests."""
    tmp = tempfile.TemporaryDirectory()
    db = AuditedDatabase(
        Path(tmp.name) / "wf038.db", mirror_dir=Path(tmp.name) / "audit", actor="test"
    )
    yield RecordStore(db)
    db.close()
    tmp.cleanup()


@pytest.fixture()
def client(monkeypatch):
    """A TestClient over a throwaway database, as test_features.py does it."""
    tmp = tempfile.TemporaryDirectory()
    monkeypatch.setenv("DSR_DB_PATH", str(Path(tmp.name) / "wf038.db"))
    monkeypatch.setenv("DSR_AUDIT_DIR", str(Path(tmp.name) / "audit"))
    monkeypatch.setattr("dsr.api.FRONTEND_DIST", Path(tmp.name) / "absent-frontend")
    with TestClient(_app()) as test_client:
        yield test_client
    tmp.cleanup()


def _app():
    from dsr.api import app

    return app


def make_room(client, name: str = "Northwind") -> dict:
    return client.post("/api/records/room", json={"name": name, "account": name}).json()


def make_connection(
    client,
    room_id: str,
    *,
    vendor: str = "salesforce",
    object_name: str = "Engagement__c",
    key_field: str = "External_Engagement_Id__c",
    **extra,
) -> dict:
    body = {"vendor": vendor, "object": object_name, "key_field": key_field, **extra}
    response = client.post(
        f"{PREFIX}/connections", json=body, params={"room_id": room_id}
    )
    assert response.status_code == 201, response.text
    return response.json()["connection"]


def make_engagement(client, room_id: str, key: str, **extra) -> dict:
    payload = {"engagement_id": key, "event_type": "viewed", "account": "Northwind", **extra}
    return client.post(
        f"{PREFIX}/rooms/{room_id}/engagement", json=payload
    ).json()["record"]


# -- domain helpers ---------------------------------------------------------- #


def row(store, room_id, key="eng-1", **extra):
    data = {"engagement_id": key, "event_type": "viewed", "account": "Acme"}
    data.update(extra)
    return store.create("engagement", data, room_id=room_id, actor="test", source="test")


def conn(store, config, room_id, **spec):
    # `object_name` is accepted as an alias so a test reads like the research
    # ("the object type indicated in the request URI") without shadowing the
    # `object` key the record actually stores.
    if "object_name" in spec:
        spec["object"] = spec.pop("object_name")
    base = {
        "vendor": "salesforce",
        "object": "Engagement__c",
        "key_field": "External_Engagement_Id__c",
        "key_source": "engagement_id",
        "room_id": room_id,
        "fields": {"event_type": "Event_Type__c", "account": "Account__c"},
    }
    base.update(spec)
    connection = cu.validate_connection(store, spec=base, config=config)
    record = store.create(
        "crm_upsert_connection",
        {k: v for k, v in connection.to_dict().items() if k not in ("id", "room_id")},
        room_id=room_id,
        actor="test",
        source="test",
    )
    return cu.Connection.from_record(record, config=config)


def ok_results(count, *, ids=None):
    return [
        {
            "id": (ids[index] if ids else f"a{index}"),
            "success": True,
            "created": index == 0,
            "errors": [],
        }
        for index in range(count)
    ]


def run_over(store, connection, config, room_id, transport, **kwargs):
    return cu.run_upsert(
        store,
        connection,
        config,
        room_id=room_id,
        transport=transport,
        source="test",
        now=NOW,
        **kwargs,
    )


# --------------------------------------------------------------------------- #
# Discovery and the feature contract
# --------------------------------------------------------------------------- #


def test_the_feature_is_mounted_by_discovery(client):
    body = client.get("/api/features").json()
    ids = {feature["id"] for feature in body["features"]}
    assert FEATURE_ID in ids
    assert body["failed_count"] == 0


def test_the_feature_reports_its_prefix_and_routes(client):
    feature = next(
        f for f in client.get("/api/features").json()["features"] if f["id"] == FEATURE_ID
    )
    assert feature["prefix"] == PREFIX
    assert feature["ticket"] == "WF-038"
    paths = {route["path"] for route in feature["routes"]}
    assert f"{PREFIX}/rooms/{{room_id}}/upsert" in paths


def test_the_prefix_is_ticket_shaped_and_collision_free():
    """A build's prefix is feature-shaped, so it cannot collide by construction."""
    assert feature_module().router.prefix == PREFIX


def test_the_feature_does_not_import_the_app():
    source = Path(feature_module().__file__).read_text(encoding="utf-8")
    assert "from dsr.api" not in source
    assert "import dsr.api" not in source


def test_the_feature_takes_its_dependencies_from_deps():
    source = Path(feature_module().__file__).read_text(encoding="utf-8")
    assert "from dsr.deps import" in source


def test_the_domain_has_no_web_framework_dependency():
    """The domain is testable on its own, which is why it has no FastAPI."""
    source = Path(inspect.getmodule(cu).__file__).read_text(encoding="utf-8")
    assert "fastapi" not in source
    assert "dsr.api" not in source


def test_the_domain_never_opens_the_database_itself():
    for module in (
        Path(inspect.getmodule(cu.capabilities).__file__),
        Path(inspect.getmodule(cu.payloads).__file__),
        Path(inspect.getmodule(cu.runs).__file__),
        Path(inspect.getmodule(cu.connections).__file__),
    ):
        text = module.read_text(encoding="utf-8")
        assert "sqlite3" not in text, module.name
        assert ".connect(" not in text, module.name


def test_the_domain_imports_no_other_feature():
    """A feature must not depend on another feature, including its domain."""
    for module in (
        Path(inspect.getmodule(cu.capabilities).__file__),
        Path(inspect.getmodule(cu.payloads).__file__),
        Path(inspect.getmodule(cu.runs).__file__),
        Path(inspect.getmodule(cu.connections).__file__),
    ):
        text = module.read_text(encoding="utf-8")
        assert "dsr.crm " not in text, module.name
        assert "from dsr.crm." not in text, module.name


def test_the_error_handlers_map_only_this_features_own_types():
    handlers = feature_module().EXCEPTION_HANDLERS
    for error_type in handlers:
        assert error_type.__module__.startswith("dsr.crm_upsert"), error_type
    assert UpsertError in handlers
    assert UnknownConnection in handlers
    assert UnknownRun in handlers


def test_every_refusal_subclass_is_covered_by_the_one_handler():
    """The concrete refusals are subclasses, so registering them again would
    collide with the host and be refused."""
    handlers = feature_module().EXCEPTION_HANDLERS
    for refusal in feature_module().UPSERT_REFUSALS:
        assert issubclass(refusal, UpsertError)
        assert refusal not in handlers
        assert handlers[UpsertError] is handlers[refusal.__mro__[1]]


def test_no_migration_or_typed_column_was_added():
    """The feature adds files only: no schema statement, no table creation."""
    for module in (
        Path(inspect.getmodule(cu.capabilities).__file__),
        Path(inspect.getmodule(cu.runs).__file__),
        Path(feature_module().__file__),
    ):
        text = module.read_text(encoding="utf-8")
        assert "CREATE TABLE" not in text.upper(), module.name
        assert "ALTER TABLE" not in text.upper(), module.name


def test_room_scoped_routes_keep_the_room_in_the_path():
    """Brief rule 3: a room-scoped path stays room-scoped."""
    paths = [route.path for route in feature_module().router.routes]
    for path in paths:
        if "room_id" in path:
            assert path.startswith(f"{PREFIX}/rooms/{{room_id}}"), path
        else:
            assert "room_id" not in path, path


# --------------------------------------------------------------------------- #
# Capabilities: the researched extensibility seam
# --------------------------------------------------------------------------- #


def test_salesforce_is_capped_at_the_researched_200():
    assert cap.SALESFORCE.max_batch_size == SALESFORCE_CAP


def test_hubspot_is_capped_at_the_researched_100():
    assert cap.HUBSPOT.max_batch_size == HUBSPOT_CAP


def test_salesforce_carries_both_researched_endpoints():
    assert cap.SALESFORCE.bulk_path == "/services/data/vXX.X/composite/sobjects/{object}/{key_field}"
    assert cap.SALESFORCE.single_path == "/services/data/vXX.X/sobjects/{object}/{key_field}/{key_value}"


def test_salesforce_supports_all_or_none_and_update_only():
    """"You can choose whether to roll back the entire request" and
    "use the `updateOnly` parameter" are both researched switches."""
    assert cap.SALESFORCE.supports_all_or_none is True
    assert cap.SALESFORCE.supports_update_only is True


def test_hubspot_uses_the_documented_batch_endpoint():
    assert cap.HUBSPOT.bulk_path == "/crm/v3/objects/{object}/batch/upsert"
    assert cap.HUBSPOT.payload_style == "id_property"


def test_hubspot_declares_no_all_or_none_or_update_only():
    """The research documents neither for HubSpot, so neither is claimed."""
    assert cap.HUBSPOT.supports_all_or_none is False
    assert cap.HUBSPOT.supports_update_only is False


def test_dataverse_uses_the_documented_upsertmultiple_action():
    assert "Microsoft.Dynamics.CRM.UpsertMultiple" in cap.DATAVERSE.bulk_path
    assert cap.DATAVERSE.payload_style == "odata"


def test_dataverse_returns_no_per_item_results():
    """"The `UpsertMultiple` action returns `204 NoContent`", so the capability
    must say the connector can never confirm a row it sent."""
    assert cap.DATAVERSE.returns_per_item_results is False


def test_dataverse_keys_on_an_alternate_key():
    assert cap.DATAVERSE.supported_key_types == ("alternate_key",)


def test_each_vendor_supports_only_its_own_key_type():
    assert cap.SALESFORCE.supported_key_types == ("external_id",)
    assert cap.HUBSPOT.supported_key_types == ("unique_property",)


def test_batch_size_defaults_to_the_vendor_cap():
    assert cap.resolve_batch_size(cap.SALESFORCE, None) == SALESFORCE_CAP


def test_a_smaller_batch_size_is_allowed():
    assert cap.resolve_batch_size(cap.SALESFORCE, 50) == 50


def test_the_exact_cap_is_allowed():
    assert cap.resolve_batch_size(cap.SALESFORCE, SALESFORCE_CAP) == SALESFORCE_CAP


def test_an_over_cap_batch_size_is_refused_not_clamped():
    """The cap is a documented vendor limit, so asking past it cannot be
    honoured by quietly sending fewer."""
    with pytest.raises(BatchTooLarge) as caught:
        cap.resolve_batch_size(cap.HUBSPOT, 500)
    assert "at most 100" in str(caught.value)
    assert "batch size 500" in str(caught.value)


def test_salesforce_over_cap_names_the_researched_number():
    with pytest.raises(BatchTooLarge) as caught:
        cap.resolve_batch_size(cap.SALESFORCE, 201)
    assert "at most 200" in str(caught.value)


def test_a_zero_batch_size_is_refused():
    with pytest.raises(BatchTooLarge):
        cap.resolve_batch_size(cap.SALESFORCE, 0)


def test_a_record_id_key_type_is_refused():
    """"Only external ids are supported. Don't use record ids." """
    with pytest.raises(UnsupportedKey) as caught:
        cap.resolve_key_type(cap.SALESFORCE, "record_id", "Something__c")
    assert "Only external ids are supported" in str(caught.value)


@pytest.mark.parametrize("field_name", ["Id", "id", "RecordId", "record_id", "crm_record_id", "CRMRecordId"])
def test_a_record_id_field_name_is_refused(field_name):
    with pytest.raises(UnsupportedKey):
        cap.resolve_key_type(cap.SALESFORCE, "external_id", field_name)


def test_an_external_id_field_name_is_accepted():
    assert cap.resolve_key_type(cap.SALESFORCE, "external_id", "External_Engagement_Id__c") == "external_id"


def test_an_unknown_key_type_is_refused():
    with pytest.raises(UnsupportedKey) as caught:
        cap.resolve_key_type(cap.SALESFORCE, "telepathy", "External__c")
    assert "is not one of" in str(caught.value)


def test_a_key_type_the_vendor_cannot_use_is_refused():
    with pytest.raises(UnsupportedKey) as caught:
        cap.resolve_key_type(cap.SALESFORCE, "alternate_key", "External__c")
    assert "cannot key an upsert on 'alternate_key'" in str(caught.value)


def test_a_key_type_is_inferred_when_a_vendor_allows_exactly_one():
    """Restating the only key type a vendor has would be friction, not safety."""
    assert cap.resolve_connection_key_type(cap.SALESFORCE, "", "Ext__c") == "external_id"
    assert cap.resolve_connection_key_type(cap.HUBSPOT, "", "email") == "unique_property"
    assert cap.resolve_connection_key_type(cap.DATAVERSE, "", "sample_keyattribute") == "alternate_key"


def test_an_explicit_key_type_beats_the_inferred_one():
    assert cap.resolve_connection_key_type(cap.SALESFORCE, "external_id", "Ext__c") == "external_id"


def test_inferring_a_key_type_never_slips_a_record_id_through():
    with pytest.raises(UnsupportedKey):
        cap.resolve_connection_key_type(cap.SALESFORCE, "", "Id")


def test_a_bulk_capable_table_resolves_to_bulk():
    assert cap.resolve_mode(cap.SALESFORCE) == "bulk"


def test_a_table_with_no_bulk_upsert_falls_back_to_single():
    """The researched fallback: "it auto-falls back from `UpsertMultiple` to
    per-row `PATCH` for tables that don't support bulk upsert"."""
    assert cap.LEGACY_TABLE.supports_bulk_upsert is False
    assert cap.resolve_mode(cap.LEGACY_TABLE) == "single"


def test_a_capability_with_neither_path_is_refused_rather_than_guessed():
    """The fallback rule is one direction only, so nowhere to go is an error."""
    broken = cap.Capability(
        vendor="nowhere",
        max_batch_size=10,
        payload_style="attributes",
        returns_per_item_results=True,
        supports_bulk_upsert=False,
        supports_single_upsert=False,
    )
    with pytest.raises(NoUpsertPath) as caught:
        cap.resolve_mode(broken)
    assert "nothing to fall back to" in str(caught.value)


def test_an_unknown_vendor_with_no_capability_is_refused():
    with pytest.raises(NoUpsertPath) as caught:
        cap.resolve_capability(None, "nobody")
    assert "register_bulk_capability" in str(caught.value)


def test_a_third_party_can_register_a_vendor_at_runtime():
    custom = cap.Capability(
        vendor="acme_crm",
        max_batch_size=25,
        payload_style="attributes",
        returns_per_item_results=True,
        single_path="/acme/{object}/{key_field}/{key_value}",
        sourced=False,
    )
    try:
        cap.register_bulk_capability(custom)
        resolved = cap.resolve_capability(None, "acme_crm")
        assert resolved.source == "registered"
        assert resolved.capability.max_batch_size == 25
        assert "acme_crm" in cap.supported_vendors()
    finally:
        cap.unregister_bulk_capability("acme_crm")
    with pytest.raises(NoUpsertPath):
        cap.resolve_capability(None, "acme_crm")


def test_a_built_in_cannot_be_unregistered():
    assert cap.unregister_bulk_capability("salesforce") is False
    assert cap.resolve_capability(None, "salesforce").capability is cap.SALESFORCE


def test_registering_a_capability_without_a_batch_size_is_refused():
    with pytest.raises(ValueError):
        cap.register_bulk_capability(
            cap.Capability(
                vendor="bad",
                max_batch_size=0,
                payload_style="attributes",
                returns_per_item_results=True,
            )
        )


def test_a_capability_from_dict_ignores_keys_it_has_never_heard_of():
    """Schema flexibility applied to a capability: a record written by a newer
    version of this feature must still load rather than take the feature off the
    registry."""
    parsed = cap.Capability.from_dict(
        {
            "vendor": "future",
            "max_batch_size": 7,
            "payload_style": "attributes",
            "returns_per_item_results": True,
            "invented_by_a_later_version": {"anything": 1},
        }
    )
    assert parsed.vendor == "future"
    assert parsed.max_batch_size == 7


def test_a_stored_capability_overrides_a_built_in(store):
    store.create(
        cap.COLLECTION_CAPABILITY,
        {
            "vendor": "hubspot",
            "max_batch_size": 60,
            "payload_style": "id_property",
            "returns_per_item_results": True,
        },
        record_id=f"{cap.COLLECTION_CAPABILITY}_hubspot",
        source="test",
    )
    resolved = cap.resolve_capability(store, "hubspot")
    assert resolved.source == "stored"
    assert resolved.capability.max_batch_size == 60


def test_the_catalogue_shows_a_stored_override_beside_the_built_in_it_replaces(store):
    store.create(
        cap.COLLECTION_CAPABILITY,
        {
            "vendor": "hubspot",
            "max_batch_size": 60,
            "payload_style": "id_property",
            "returns_per_item_results": True,
        },
        record_id=f"{cap.COLLECTION_CAPABILITY}_hubspot",
        source="test",
    )
    listed = {entry["vendor"]: entry for entry in cap.catalogue(store)}
    assert listed["hubspot"]["max_batch_size"] == 60
    assert listed["hubspot"]["origin"] == "stored"
    assert listed["salesforce"]["origin"] == "built_in"
    assert listed["salesforce"]["is_default"] is True


def test_a_capability_can_be_replaced_in_process():
    override = cap.Capability(
        vendor="salesforce",
        max_batch_size=25,
        payload_style="attributes",
        returns_per_item_results=True,
    )
    try:
        cap.register_bulk_capability(override)
        assert cap.resolve_capability(None, "salesforce").capability.max_batch_size == 25
    finally:
        cap.unregister_bulk_capability("salesforce")
    assert cap.resolve_capability(None, "salesforce").capability.max_batch_size == SALESFORCE_CAP


def test_a_connection_may_declare_its_own_capability(store):
    """A test, and a team with a private vendor, should not have to mutate a
    process-wide registry."""
    connection = cu.Connection(vendor="private", object="t", key_field="k")
    connection.capability = {
        "vendor": "private",
        "max_batch_size": 3,
        "payload_style": "attributes",
        "returns_per_item_results": True,
        "bulk_path": "/p/{object}/{key_field}",
    }
    resolved = connection.resolved_capability(store)
    assert resolved.source == "connection"
    assert connection.effective_batch_size(store) == 3


@pytest.mark.parametrize(
    "name,expected",
    [
        ("Id", True),
        ("id", True),
        ("RECORDID", True),
        ("record_id", True),
        ("crm_record_id", True),
        ("External_Engagement_Id__c", False),
        ("sample_keyattribute", False),
        ("email", False),
    ],
)
def test_record_id_field_detection(name, expected):
    assert cap.is_record_id_field(name) is expected


# --------------------------------------------------------------------------- #
# The researched request shapes
# --------------------------------------------------------------------------- #


def test_a_salesforce_item_carries_an_attributes_type():
    item = pay.salesforce_item({"A__c": 1}, object_name="Engagement__c", key_field="Ext__c", key_value_="k1")
    assert item["attributes"] == {"type": "Engagement__c"}
    assert item["Ext__c"] == "k1"


def test_a_salesforce_payload_puts_records_under_records():
    body = pay.build_payload(
        cap.SALESFORCE,
        object_name="Engagement__c",
        key_field="Ext__c",
        entries=[("k1", {"A__c": 1})],
    )
    assert list(body) == ["records"]
    assert len(body["records"]) == 1


def test_a_salesforce_payload_carries_no_id_field():
    """"**no `id` field**, external-ID field only" """
    body = pay.build_payload(
        cap.SALESFORCE,
        object_name="Engagement__c",
        key_field="Ext__c",
        entries=[("k1", {"A__c": 1})],
    )
    item = body["records"][0]
    assert "id" not in item
    assert "Id" not in item
    assert "id" not in item["attributes"]


def test_a_salesforce_item_keeps_the_request_order():
    """"Objects are created or updated in the order they're listed in the request
    body", so the builder must not sort or group."""
    body = pay.build_payload(
        cap.SALESFORCE,
        object_name="Engagement__c",
        key_field="Ext__c",
        entries=[("k3", {"n": 3}), ("k1", {"n": 1}), ("k2", {"n": 2})],
    )
    assert [item["Ext__c"] for item in body["records"]] == ["k3", "k1", "k2"]


def test_all_or_none_travels_in_the_query_string_not_the_body():
    request = pay.build_bulk_request(
        cap.SALESFORCE,
        object_name="Engagement__c",
        key_field="Ext__c",
        entries=[("k1", {"A__c": 1})],
        all_or_none=True,
    )
    assert request.query == {"allOrNone": "true"}
    assert "allOrNone" not in request.body


def test_all_or_none_false_is_sent_explicitly():
    request = pay.build_bulk_request(
        cap.SALESFORCE,
        object_name="Engagement__c",
        key_field="Ext__c",
        entries=[("k1", {"A__c": 1})],
        all_or_none=False,
    )
    assert request.query == {"allOrNone": "false"}


def test_a_vendor_without_all_or_none_is_never_sent_the_parameter():
    request = pay.build_bulk_request(
        cap.HUBSPOT,
        object_name="contacts",
        key_field="email",
        entries=[("a@b.example", {"lastname": "B"})],
        all_or_none=True,
    )
    assert request.query == {}


def test_the_salesforce_collections_request_is_a_patch():
    request = pay.build_bulk_request(
        cap.SALESFORCE,
        object_name="Engagement__c",
        key_field="Ext__c",
        entries=[("k1", {"A__c": 1})],
        all_or_none=False,
    )
    assert request.method == "PATCH"
    assert request.path == "/services/data/vXX.X/composite/sobjects/Engagement__c/Ext__c"


def test_the_hubspot_batch_request_is_a_post():
    """The research's HubSpot endpoint is a POST to a batch/upsert action."""
    request = pay.build_bulk_request(
        cap.HUBSPOT,
        object_name="contacts",
        key_field="email",
        entries=[("a@b.example", {"lastname": "B"})],
        all_or_none=False,
    )
    assert request.method == "POST"
    assert request.path == "/crm/v3/objects/contacts/batch/upsert"


def test_the_dataverse_bulk_request_is_a_post():
    """``POST [Organization URI]/api/data/v9.2/{entityset}/
    Microsoft.Dynamics.CRM.UpsertMultiple`` - an action, invoked with POST. Sending
    it as a PATCH would be a 405 from a real CRM."""
    request = pay.build_bulk_request(
        cap.DATAVERSE,
        object_name="engagements",
        key_field="sample_keyattribute",
        entries=[("k1", {"eventtype": "viewed"})],
        all_or_none=False,
    )
    assert request.method == "POST"
    assert request.path == "/api/data/v9.2/engagements/Microsoft.Dynamics.CRM.UpsertMultiple"


def test_the_dataverse_entity_set_placeholder_is_substituted():
    """An unsubstituted ``{entityset}`` left in a path is a 404 that looks like a
    missing table rather than a template bug."""
    request = pay.build_bulk_request(
        cap.DATAVERSE,
        object_name="contacts",
        key_field="sample_keyattribute",
        entries=[("k1", {"eventtype": "viewed"})],
        all_or_none=False,
    )
    assert "{" not in request.path
    assert "/api/data/v9.2/contacts/" in request.path


def test_the_dataverse_single_row_path_is_fully_substituted():
    request = pay.build_single_request(
        cap.DATAVERSE,
        object_name="contacts",
        key_field="sample_keyattribute",
        key_value_="k1",
        fields={"eventtype": "viewed"},
        update_only=False,
    )
    assert "{" not in request.path
    assert request.path == "/api/data/v9.2/contacts(sample_keyattribute=k1)"


def test_no_built_in_capability_template_leaves_a_placeholder_in_a_path():
    """Every built-in template must be fillable from the values a run supplies."""
    for capability in cap.BUILT_IN.values():
        for template in (capability.bulk_path, capability.single_path):
            if not template:
                continue
            filled = pay._fill(
                template,
                object="Engagement__c",
                entityset="engagements",
                key_field="Ext__c",
                key_value="k1",
                api_version="vXX.X",
            )
            assert "{" not in filled, (capability.vendor, template, filled)


def test_a_hubspot_item_names_its_id_property():
    """"include the `idProperty` parameter to identify the unique identifier
    property you're using" """
    item = pay.hubspot_item({"lastname": "B"}, key_field="email", key_value_="a@b.example")
    assert item["idProperty"] == "email"
    assert item["id"] == "a@b.example"
    assert item["properties"] == {"lastname": "B"}


def test_the_hubspot_envelope_id_is_the_id_property_value_not_a_record_id():
    """The generated ``id`` is the researched idProperty's value. Guarding the
    whole envelope for record ids would refuse every HubSpot request."""
    body = pay.build_payload(
        cap.HUBSPOT, object_name="contacts", key_field="email", entries=[("a@b.example", {"lastname": "B"})]
    )
    assert body["inputs"][0]["id"] == "a@b.example"


def test_a_dataverse_item_carries_odata_type_and_id():
    item = pay.dataverse_item(
        {"eventtype": "viewed"},
        entity_set="engagements",
        logical_name="engagement",
        key_field="sample_keyattribute",
        key_value_="k1",
    )
    assert item["@odata.type"] == "#Microsoft.Dynamics.CRM.engagement"
    assert item["@odata.id"] == "engagements(sample_keyattribute='k1')"


def test_every_dataverse_item_carries_its_own_odata_type():
    """"You must specify the `@odata.type` annotation with every item in the
    `Targets` parameter" - so it is per item, not once for the collection."""
    body = pay.build_payload(
        cap.DATAVERSE,
        object_name="engagements",
        key_field="sample_keyattribute",
        entries=[("k1", {"a": 1}), ("k2", {"a": 2})],
    )
    assert len(body["Targets"]) == 2
    for item in body["Targets"]:
        assert item["@odata.type"].startswith("#Microsoft.Dynamics.CRM.")


def test_a_dataverse_payload_uses_the_targets_collection():
    body = pay.build_payload(
        cap.DATAVERSE,
        object_name="engagements",
        key_field="sample_keyattribute",
        entries=[("k1", {"a": 1})],
    )
    assert list(body) == ["Targets"]


def test_a_dataverse_alternate_key_quotes_an_apostrophe():
    """An unescaped apostrophe would close the literal early and compare a
    different key than the one asked for."""
    item = pay.dataverse_item(
        {},
        entity_set="engagements",
        logical_name="engagement",
        key_field="sample_keyattribute",
        key_value_="o'brien",
    )
    assert item["@odata.id"] == "engagements(sample_keyattribute='o''brien')"


def test_a_dataverse_logical_name_is_singular_and_the_id_stays_plural():
    body = pay.build_payload(
        cap.DATAVERSE, object_name="contacts", key_field="sample_keyattribute", entries=[("k1", {})]
    )
    item = body["Targets"][0]
    assert item["@odata.type"] == "#Microsoft.Dynamics.CRM.contact"
    assert item["@odata.id"].startswith("contacts(")


def test_two_object_types_in_one_request_are_refused():
    """"The list can contain objects only of the type indicated in the request
    URI" """
    with pytest.raises(MixedObjectTypes) as caught:
        pay.salesforce_item  # keep the reference explicit for the reader
        pay._assert_single_type(
            [
                {"attributes": {"type": "A__c"}},
                {"attributes": {"type": "B__c"}},
            ],
            "A__c",
        )
    assert "single object type" in str(caught.value)


def test_one_object_type_in_a_request_is_fine():
    pay._assert_single_type([{"attributes": {"type": "A__c"}}] * 3, "A__c")


def test_a_mapped_field_cannot_be_a_record_id():
    """The loop the write-back would otherwise create: the room stores
    ``crm_record_id`` on every synced row, so mapping a field onto it echoes last
    run's record id into this run's request."""
    with pytest.raises(UpsertError) as caught:
        pay.build_payload(
            cap.SALESFORCE,
            object_name="Engagement__c",
            key_field="Ext__c",
            entries=[("k1", {"crm_record_id": "a01"})],
        )
    assert "which is a record id" in str(caught.value)
    assert "external-id field only" in str(caught.value)


def test_a_nested_record_id_field_is_also_refused():
    with pytest.raises(UpsertError):
        pay._assert_no_record_id([{"outer": {"Id": "a01"}}], "Ext__c")


def test_a_single_row_request_puts_the_key_in_the_path_not_the_body():
    """``PATCH .../sobjects/{sObject}/{fieldName}/{fieldValue}`` - the key value
    is the address."""
    request = pay.build_single_request(
        cap.SALESFORCE,
        object_name="Engagement__c",
        key_field="Ext__c",
        key_value_="k1",
        fields={"A__c": 1},
        update_only=False,
    )
    assert request.path == "/services/data/vXX.X/sobjects/Engagement__c/Ext__c/k1"
    assert "Ext__c" not in request.body
    assert request.body["attributes"] == {"type": "Engagement__c"}


def test_update_only_is_sent_when_the_vendor_supports_it():
    """"To prevent a new record from being created, use the `updateOnly`
    parameter" """
    request = pay.build_single_request(
        cap.SALESFORCE,
        object_name="Engagement__c",
        key_field="Ext__c",
        key_value_="k1",
        fields={"A__c": 1},
        update_only=True,
    )
    assert request.query == {"updateOnly": "true"}


def test_update_only_is_omitted_when_off():
    request = pay.build_single_request(
        cap.SALESFORCE,
        object_name="Engagement__c",
        key_field="Ext__c",
        key_value_="k1",
        fields={"A__c": 1},
        update_only=False,
    )
    assert request.query == {}


def test_update_only_is_not_sent_to_a_vendor_that_does_not_support_it():
    request = pay.build_single_request(
        cap.HUBSPOT,
        object_name="contacts",
        key_field="email",
        key_value_="a@b.example",
        fields={"lastname": "B"},
        update_only=True,
    )
    assert request.query == {}


def test_a_key_value_with_a_slash_is_percent_encoded_into_the_path():
    """Otherwise the key becomes two path segments and addresses a different
    record, or a 404 that looks like a missing key."""
    request = pay.build_single_request(
        cap.SALESFORCE,
        object_name="Engagement__c",
        key_field="Ext__c",
        key_value_="a/b c",
        fields={},
        update_only=False,
    )
    assert request.path.endswith("/a%2Fb%20c")


def test_a_dataverse_single_row_addresses_by_alternate_key():
    request = pay.build_single_request(
        cap.DATAVERSE,
        object_name="engagements",
        key_field="sample_keyattribute",
        key_value_="k1",
        fields={"a": 1},
        update_only=False,
    )
    assert "sample_keyattribute=k1" in request.path
    assert "@odata.id" not in request.body


def test_an_unknown_payload_style_is_refused():
    broken = cap.Capability(
        vendor="weird",
        max_batch_size=5,
        payload_style="smoke signals",
        returns_per_item_results=True,
        bulk_path="/w/{object}/{key_field}",
    )
    with pytest.raises(UpsertError) as caught:
        pay.build_payload(broken, object_name="T", key_field="K", entries=[("k", {})])
    assert "unknown payload style" in str(caught.value)


# --------------------------------------------------------------------------- #
# Preflight: the researched refusals
# --------------------------------------------------------------------------- #


FIELDS = {"event_type": "Event_Type__c"}


def test_a_row_with_a_key_value_passes():
    value = pay.preflight_row(
        {"engagement_id": "k1", "event_type": "viewed"},
        key_source="engagement_id",
        field_map=FIELDS,
        key_field="Ext__c",
        vendor="salesforce",
    )
    assert value == "k1"


def test_a_row_with_no_key_value_is_refused():
    with pytest.raises(pay.RowRejected) as caught:
        pay.preflight_row(
            {"engagement_id": "", "event_type": "viewed"},
            key_source="engagement_id",
            field_map=FIELDS,
            key_field="Ext__c",
            vendor="salesforce",
        )
    assert caught.value.reason == "no_key_value"
    # The stored error text carries the rule as well as the specifics, so a log
    # line says why the connector refused rather than only what it saw.
    assert "engagement_id" in str(caught.value)
    assert "external id" in str(caught.value)


def test_a_row_with_no_mapped_field_is_refused():
    with pytest.raises(pay.RowRejected) as caught:
        pay.preflight_row(
            {"engagement_id": "k1", "unrelated": 1},
            key_source="engagement_id",
            field_map=FIELDS,
            key_field="Ext__c",
            vendor="salesforce",
        )
    assert caught.value.reason == "no_mapped_fields"


def test_a_record_id_key_field_is_refused_by_preflight():
    with pytest.raises(pay.RowRejected) as caught:
        pay.preflight_row(
            {"engagement_id": "k1", "event_type": "v"},
            key_source="engagement_id",
            field_map=FIELDS,
            key_field="Id",
            vendor="salesforce",
        )
    assert caught.value.reason == "record_id_key"


def test_a_hubspot_email_row_missing_a_required_property_is_refused():
    """"Partial upserts are not supported when using `email` as the `idProperty`
    for contacts" """
    with pytest.raises(pay.RowRejected) as caught:
        pay.preflight_row(
            {"email": "a@b.example", "event_type": "viewed"},
            key_source="email",
            field_map={"email": "email", "event_type": "event_type"},
            key_field="email",
            vendor="hubspot",
            partial_upserts_supported=False,
            required_properties=["lastname"],
        )
    assert caught.value.reason == "missing_required_property"
    assert "lastname" in str(caught.value)


def test_a_hubspot_email_row_with_the_required_property_passes():
    value = pay.preflight_row(
        {"email": "a@b.example", "event_type": "viewed", "lastname": "B"},
        key_source="email",
        field_map={"email": "email", "event_type": "event_type", "lastname": "lastname"},
        key_field="email",
        vendor="hubspot",
        partial_upserts_supported=False,
        required_properties=["lastname"],
    )
    assert value == "a@b.example"


def test_a_vendor_that_does_accept_partial_upserts_is_not_held_to_required_properties():
    value = pay.preflight_row(
        {"email": "a@b.example", "event_type": "viewed"},
        key_source="email",
        field_map={"email": "email", "event_type": "event_type"},
        key_field="email",
        vendor="hubspot",
        partial_upserts_supported=True,
        required_properties=["lastname"],
    )
    assert value == "a@b.example"


def test_every_rejection_reason_is_published():
    for reason in pay.REJECTION_REASONS:
        assert reason.islower()
        assert pay.REJECTION_REASONS[reason].strip()


def test_an_unknown_rejection_reason_cannot_be_constructed():
    with pytest.raises(KeyError):
        pay.RowRejected("because_i_said_so")


def test_mapped_fields_carry_only_fields_the_room_holds():
    fields = pay.mapped_fields({"event_type": "viewed", "other": 1}, {"event_type": "E__c", "missing": "M__c"})
    assert fields == {"E__c": "viewed"}


def test_mapped_fields_skip_a_null_value():
    assert pay.mapped_fields({"event_type": None}, {"event_type": "E__c"}) == {}


def test_mapped_fields_drop_a_record_id_target():
    assert pay.mapped_fields({"a": 1}, {"a": "crm_record_id"}) == {}


def test_a_key_value_that_is_an_object_is_not_a_key():
    assert pay.key_value({"engagement_id": {"nested": 1}}, "engagement_id") == ""


def test_a_key_value_is_stripped():
    assert pay.key_value({"engagement_id": "  k1  "}, "engagement_id") == "k1"


# --------------------------------------------------------------------------- #
# Chunking and the researched run
# --------------------------------------------------------------------------- #


def test_chunking_is_consecutive_and_never_exceeds_the_size():
    groups = run_mod.chunk(list(range(401)), 200)
    assert [len(group) for group in groups] == [200, 200, 1]


def test_chunking_an_exact_multiple_has_no_empty_tail():
    assert [len(group) for group in run_mod.chunk(list(range(400)), 200)] == [200, 200]


def test_chunking_nothing_is_no_chunks():
    assert run_mod.chunk([], 200) == []


def test_chunking_refuses_a_zero_size():
    with pytest.raises(UpsertError):
        run_mod.chunk([1], 0)


def test_chunking_keeps_each_item_s_position():
    groups = run_mod.chunk_indexed(list("abcde"), 2)
    assert [position for position, _item in groups[0]] == [0, 1]
    assert [position for position, _item in groups[1]] == [2, 3]
    assert [position for position, _item in groups[2]] == [4]


def test_a_row_created_and_a_row_updated_are_distinguished(store):
    config = cu.load_config(store)
    room = store.create("room", {"name": "R"}, source="test")
    connection = conn(store, config, room["id"])
    row(store, room["id"], "k1")
    row(store, room["id"], "k2")

    result = run_over(
        store,
        connection,
        config,
        room["id"],
        cu.ScriptedTransport(cu.salesforce_upsert_results(ok_results(2, ids=["a01", "a02"]))),
    )
    by_key = {entry.key: entry for entry in result.outcomes}
    assert by_key["k1"].outcome == "created"
    assert by_key["k2"].outcome == "updated"
    assert by_key["k1"].crm_record_id == "a01"
    assert by_key["k2"].crm_record_id == "a02"


def test_results_are_matched_by_position_not_by_key(store):
    """The same external key twice in one batch is exactly what re-running a queue
    produces, and key matching would attach both outcomes to the first row."""
    config = cu.load_config(store)
    room = store.create("room", {"name": "R"}, source="test")
    connection = conn(store, config, room["id"])
    first = row(store, room["id"], "same", event_type="viewed")
    second = row(store, room["id"], "same", event_type="downloaded")

    result = run_over(
        store,
        connection,
        config,
        room["id"],
        cu.ScriptedTransport(
            cu.salesforce_upsert_results(
                [
                    {"id": "a01", "success": True, "created": True},
                    {"id": "a02", "success": True, "created": False},
                ]
            )
        ),
    )
    by_record = {entry.record_id: entry for entry in result.outcomes}
    assert by_record[first["id"]].crm_record_id == "a01"
    assert by_record[first["id"]].outcome == "created"
    assert by_record[second["id"]].crm_record_id == "a02"
    assert by_record[second["id"]].outcome == "updated"


def test_too_few_results_fails_the_whole_chunk(store):
    """Silently truncating would leave the tail pending while the run reported
    success, and the queue would never drain."""
    config = cu.load_config(store)
    room = store.create("room", {"name": "R"}, source="test")
    connection = conn(store, config, room["id"])
    for index in range(3):
        row(store, room["id"], f"k{index}")

    result = run_over(
        store,
        connection,
        config,
        room["id"],
        cu.ScriptedTransport(cu.salesforce_upsert_results(ok_results(1))),
    )
    assert [entry.outcome for entry in result.outcomes] == ["failed"] * 3
    assert all("1 results for 3 rows" in entry.errors[0] for entry in result.outcomes)


def test_too_many_results_also_fails_the_chunk(store):
    config = cu.load_config(store)
    room = store.create("room", {"name": "R"}, source="test")
    connection = conn(store, config, room["id"])
    row(store, room["id"], "k1")

    result = run_over(
        store,
        connection,
        config,
        room["id"],
        cu.ScriptedTransport(cu.salesforce_upsert_results(ok_results(4))),
    )
    assert result.outcomes[0].outcome == "failed"
    assert "4 results for 1 rows" in result.outcomes[0].errors[0]


def test_a_duplicate_external_id_is_an_error_not_a_second_write(store):
    """"If the external ID matches multiple existing records, then a 300 error is
    returned, and no records are created or updated." """
    config = cu.load_config(store)
    room = store.create("room", {"name": "R"}, source="test")
    connection = conn(store, config, room["id"], all_or_none=False)
    row(store, room["id"], "k1")
    row(store, room["id"], "k2")

    result = run_over(
        store,
        connection,
        config,
        room["id"],
        cu.ScriptedTransport(
            cu.salesforce_upsert_results(
                [
                    {"id": "a01", "success": True, "created": True},
                    {
                        "id": None,
                        "success": False,
                        "created": False,
                        "errors": [
                            {
                                "statusCode": "300",
                                "message": "The external ID field is not unique.",
                                "fields": ["External_Engagement_Id__c"],
                            }
                        ],
                    },
                ]
            )
        ),
    )
    by_key = {entry.key: entry for entry in result.outcomes}
    assert by_key["k1"].outcome == "created"
    failed = by_key["k2"]
    assert failed.outcome == "failed"
    assert failed.crm_record_id == ""
    assert "not unique" in failed.errors[0]


def test_the_error_text_is_the_crm_s_own_words(store):
    config = cu.load_config(store)
    room = store.create("room", {"name": "R"}, source="test")
    connection = conn(store, config, room["id"])
    row(store, room["id"], "k1")

    result = run_over(
        store,
        connection,
        config,
        room["id"],
        cu.ScriptedTransport(
            cu.salesforce_upsert_results(
                [
                    {
                        "id": None,
                        "success": False,
                        "errors": [{"statusCode": "REQUIRED_FIELD_MISSING", "message": "Required fields are missing: [Name]"}],
                    }
                ]
            )
        ),
    )
    assert result.outcomes[0].errors == [
        "Required fields are missing: [Name] [REQUIRED_FIELD_MISSING]"
    ]


def test_a_result_carrying_only_a_status_code_still_produces_text(store):
    config = cu.load_config(store)
    room = store.create("room", {"name": "R"}, source="test")
    connection = conn(store, config, room["id"])
    row(store, room["id"], "k1")
    result = run_over(
        store,
        connection,
        config,
        room["id"],
        cu.ScriptedTransport(
            cu.salesforce_upsert_results(
                [{"id": None, "success": False, "errors": [{"statusCode": "300"}]}]
            )
        ),
    )
    assert result.outcomes[0].errors == ["300"]


def test_all_or_none_rolls_the_whole_chunk_back(store):
    """"You can choose whether to roll back the entire request when an error
    occurs" - so the rows that "succeeded" were not written either."""
    config = cu.load_config(store)
    room = store.create("room", {"name": "R"}, source="test")
    connection = conn(store, config, room["id"], all_or_none=True)
    row(store, room["id"], "k1")
    row(store, room["id"], "k2")

    result = run_over(
        store,
        connection,
        config,
        room["id"],
        cu.ScriptedTransport(
            cu.salesforce_upsert_results(
                [
                    {"id": "a01", "success": True, "created": True},
                    {"id": None, "success": False, "errors": [{"statusCode": "300", "message": "dup"}]},
                ]
            )
        ),
    )
    by_key = {entry.key: entry for entry in result.outcomes}
    assert by_key["k1"].outcome == "rolled_back"
    assert by_key["k1"].crm_record_id == ""
    assert by_key["k2"].outcome == "failed"
    assert "rolled back the whole request" in by_key["k1"].errors[0]


def test_a_rolled_back_row_is_not_written_back_as_synced(store):
    config = cu.load_config(store)
    room = store.create("room", {"name": "R"}, source="test")
    connection = conn(store, config, room["id"], all_or_none=True)
    kept = row(store, room["id"], "k1")
    row(store, room["id"], "k2")

    run_over(
        store,
        connection,
        config,
        room["id"],
        cu.ScriptedTransport(
            cu.salesforce_upsert_results(
                [
                    {"id": "a01", "success": True, "created": True},
                    {"id": None, "success": False, "errors": [{"message": "dup"}]},
                ]
            )
        ),
    )
    data = store.get(kept["id"])["data"]
    assert data["sync_status"] == "pending"
    assert data.get("synced_at") is None
    assert "crm_record_id" not in data or not data["crm_record_id"]


def test_all_or_none_off_lets_the_good_rows_land(store):
    config = cu.load_config(store)
    room = store.create("room", {"name": "R"}, source="test")
    connection = conn(store, config, room["id"], all_or_none=False)
    good = row(store, room["id"], "k1")
    row(store, room["id"], "k2")

    run_over(
        store,
        connection,
        config,
        room["id"],
        cu.ScriptedTransport(
            cu.salesforce_upsert_results(
                [
                    {"id": "a01", "success": True, "created": True},
                    {"id": None, "success": False, "errors": [{"message": "dup"}]},
                ]
            )
        ),
    )
    data = store.get(good["id"])["data"]
    assert data["sync_status"] == "synced"
    assert data["crm_record_id"] == "a01"
    assert data["synced_at"]


def test_a_confirmed_row_records_synced_at_and_the_record_id(store):
    config = cu.load_config(store)
    room = store.create("room", {"name": "R"}, source="test")
    connection = conn(store, config, room["id"])
    queued = row(store, room["id"], "k1")

    run_over(
        store,
        connection,
        config,
        room["id"],
        cu.ScriptedTransport(cu.salesforce_upsert_results([{"id": "a01", "success": True, "created": True}])),
    )
    data = store.get(queued["id"])["data"]
    assert data["sync_status"] == "synced"
    assert data["sync_outcome"] == "created"
    assert data["crm_record_id"] == "a01"
    assert data["synced_at"]


def test_a_failed_row_stays_pending_and_records_why(store):
    config = cu.load_config(store)
    room = store.create("room", {"name": "R"}, source="test")
    connection = conn(store, config, room["id"])
    queued = row(store, room["id"], "k1")

    run_over(
        store,
        connection,
        config,
        room["id"],
        cu.ScriptedTransport(
            cu.salesforce_upsert_results([{"id": None, "success": False, "errors": [{"message": "nope"}]}])
        ),
    )
    data = store.get(queued["id"])["data"]
    # A row the CRM refused is `failed`, not `pending` - the distinction tells a
    # rep which rows have already been tried - but it is still unsent, so the
    # queue picks it up again.
    assert data["sync_status"] == "failed"
    assert data["sync_outcome"] == "failed"
    assert data["sync_errors"] == ["nope"]


def test_a_row_the_crm_refused_is_queued_again(store):
    """The research names no retry and no give-up rule, so the queue is the
    mechanism - and a row dropped after one failure would never sync at all."""
    config = cu.load_config(store)
    room = store.create("room", {"name": "R"}, source="test")
    connection = conn(store, config, room["id"])
    queued = row(store, room["id"], "k1")

    run_over(
        store,
        connection,
        config,
        room["id"],
        cu.ScriptedTransport(
            cu.salesforce_upsert_results([{"id": None, "success": False, "errors": [{"message": "nope"}]}])
        ),
    )
    assert cu.row_status(store.get(queued["id"]), connection.id) == "failed"
    assert [r["id"] for r in cu.pending_rows(store, connection, config, room_id=room["id"])] == [queued["id"]]


def test_a_failed_row_leaves_the_queue_once_it_lands(store):
    config = cu.load_config(store)
    room = store.create("room", {"name": "R"}, source="test")
    connection = conn(store, config, room["id"])
    row(store, room["id"], "k1")

    run_over(
        store,
        connection,
        config,
        room["id"],
        cu.ScriptedTransport(
            cu.salesforce_upsert_results([{"id": None, "success": False, "errors": [{"message": "nope"}]}])
        ),
    )
    run_over(
        store,
        connection,
        config,
        room["id"],
        cu.ScriptedTransport(cu.salesforce_upsert_results([{"id": "a01", "success": True, "created": True}])),
    )
    assert cu.pending_rows(store, connection, config, room_id=room["id"]) == []


def test_the_queue_reports_failed_rows_apart_from_untried_ones(store):
    config = cu.load_config(store)
    room = store.create("room", {"name": "R"}, source="test")
    connection = conn(store, config, room["id"])
    row(store, room["id"], "k1")
    row(store, room["id"], "k2")

    run_over(
        store,
        connection,
        config,
        room["id"],
        cu.ScriptedTransport(
            lambda request: cu.salesforce_upsert_results(
                [
                    {"id": None, "success": False, "errors": [{"message": "nope"}]},
                    {"id": "a02", "success": True, "created": True},
                ]
            )
        ),
    )
    view = cu.queue_view(store, connection, config, room_id=room["id"])
    assert view["counts"]["failed"] == 1
    assert view["counts"]["synced"] == 1
    assert view["unsent"] == 1


def test_a_refused_row_is_queued_again_as_pending(store):
    config = cu.load_config(store)
    room = store.create("room", {"name": "R"}, source="test")
    connection = conn(store, config, room["id"])
    queued = row(store, room["id"], "")

    run_over(store, connection, config, room["id"], cu.ScriptedTransport(cu.salesforce_upsert_results([])))
    assert cu.row_status(store.get(queued["id"]), connection.id) == "pending"
    assert [r["id"] for r in cu.pending_rows(store, connection, config, room_id=room["id"])] == [queued["id"]]


def test_a_row_that_once_synced_has_synced_at_cleared_when_it_fails(store):
    """A list view that only reads ``synced_at`` would otherwise report a row that
    just failed as synced."""
    config = cu.load_config(store)
    room = store.create("room", {"name": "R"}, source="test")
    connection = conn(store, config, room["id"], all_or_none=False)
    queued = row(store, room["id"], "k1")

    run_over(
        store,
        connection,
        config,
        room["id"],
        cu.ScriptedTransport(cu.salesforce_upsert_results([{"id": "a01", "success": True, "created": True}])),
    )
    assert store.get(queued["id"])["data"]["synced_at"]

    # An operator re-queues the row to try again. This clears the per-connection
    # state, which is what the queue reads - setting only the row-level
    # `sync_status` would leave the row synced as far as the run is concerned.
    store.update(queued["id"], {"sync": {}, "sync_status": "pending"}, source="test")

    run_over(
        store,
        connection,
        config,
        room["id"],
        cu.ScriptedTransport(
            cu.salesforce_upsert_results([{"id": None, "success": False, "errors": [{"message": "nope"}]}])
        ),
    )
    data = store.get(queued["id"])["data"]
    assert data["synced_at"] is None
    assert data["crm_record_id"] == "a01"  # the earlier id is not invented away


def test_a_refused_row_never_reaches_the_wire(store):
    config = cu.load_config(store)
    room = store.create("room", {"name": "R"}, source="test")
    connection = conn(store, config, room["id"])
    queued = row(store, room["id"], "")
    transport = cu.ScriptedTransport(cu.salesforce_upsert_results([]))

    result = run_over(store, connection, config, room["id"], transport)
    assert transport.count == 0
    assert result.outcomes[0].outcome == "rejected"
    assert result.outcomes[0].record_id == queued["id"]
    assert "external id" in result.outcomes[0].errors[0]


def test_one_bad_row_does_not_stop_the_others(store):
    config = cu.load_config(store)
    room = store.create("room", {"name": "R"}, source="test")
    connection = conn(store, config, room["id"])
    row(store, room["id"], "")
    row(store, room["id"], "k2")

    transport = cu.ScriptedTransport(cu.salesforce_upsert_results(ok_results(1)))
    result = run_over(store, connection, config, room["id"], transport)
    assert transport.count == 1
    outcomes = {entry.outcome for entry in result.outcomes}
    assert outcomes == {"rejected", "created"}


# --------------------------------------------------------------------------- #
# Dataverse: the vendor that confirms nothing
# --------------------------------------------------------------------------- #


def test_dataverse_rows_are_submitted_not_synced(store):
    """"The `UpsertMultiple` action returns `204 NoContent`", so there is no
    per-item success flag to read."""
    config = cu.load_config(store)
    room = store.create("room", {"name": "R"}, source="test")
    connection = conn(
        store,
        config,
        room["id"],
        vendor="dataverse",
        object_name="engagements",
        key_field="sample_keyattribute",
        fields={"event_type": "eventtype"},
    )
    queued = row(store, room["id"], "k1")

    result = run_over(
        store, connection, config, room["id"], cu.ScriptedTransport(cu.dataverse_upsert_multiple())
    )
    assert result.outcomes[0].outcome == "submitted"
    assert result.outcomes[0].crm_record_id == ""
    data = store.get(queued["id"])["data"]
    assert data["sync_status"] == "unconfirmed"
    assert data["sent_at"]
    assert not data.get("synced_at")
    assert not data.get("crm_record_id")


def test_a_submitted_row_leaves_the_queue(store):
    """Otherwise a re-run would send it forever, because a Dataverse upsert that
    worked looks exactly the same next time."""
    config = cu.load_config(store)
    room = store.create("room", {"name": "R"}, source="test")
    connection = conn(
        store,
        config,
        room["id"],
        vendor="dataverse",
        object_name="engagements",
        key_field="sample_keyattribute",
        fields={"event_type": "eventtype"},
    )
    row(store, room["id"], "k1")

    run_over(store, connection, config, room["id"], cu.ScriptedTransport(cu.dataverse_upsert_multiple()))
    assert cu.pending_rows(store, connection, config, room_id=room["id"]) == []


def test_unconfirmed_is_counted_apart_from_synced(store):
    config = cu.load_config(store)
    room = store.create("room", {"name": "R"}, source="test")
    connection = conn(
        store,
        config,
        room["id"],
        vendor="dataverse",
        object_name="engagements",
        key_field="sample_keyattribute",
        fields={"event_type": "eventtype"},
    )
    row(store, room["id"], "k1")
    run_over(store, connection, config, room["id"], cu.ScriptedTransport(cu.dataverse_upsert_multiple()))

    view = cu.queue_view(store, connection, config, room_id=room["id"])
    assert view["counts"]["unconfirmed"] == 1
    assert view["counts"]["synced"] == 0
    assert view["counts"]["pending"] == 0


# --------------------------------------------------------------------------- #
# The researched fallback
# --------------------------------------------------------------------------- #


def test_a_table_with_no_bulk_upsert_sends_one_request_per_row(store):
    """"it auto-falls back from `UpsertMultiple` to per-row `PATCH` for tables
    that don't support bulk upsert" """
    config = cu.load_config(store)
    room = store.create("room", {"name": "R"}, source="test")
    connection = conn(
        store,
        config,
        room["id"],
        vendor="legacy_table",
        object_name="engagements",
        key_field="eng_key",
        fields={"event_type": "event_type"},
    )
    for index in range(3):
        row(store, room["id"], f"k{index}")

    transport = cu.ScriptedTransport(cu.salesforce_single(201, "L-1", created=True))
    result = run_over(store, connection, config, room["id"], transport)

    assert result.record["data"]["mode"] == "single"
    assert transport.count == 3
    assert all(path.startswith("/api/legacy/") for path in transport.paths())
    assert len(result.outcomes) == 3


def test_the_fallback_puts_the_key_in_the_path(store):
    config = cu.load_config(store)
    room = store.create("room", {"name": "R"}, source="test")
    connection = conn(
        store,
        config,
        room["id"],
        vendor="legacy_table",
        object_name="engagements",
        key_field="eng_key",
        fields={"event_type": "event_type"},
    )
    row(store, room["id"], "k1")
    transport = cu.ScriptedTransport(cu.salesforce_single(201, "L-1", created=True))
    run_over(store, connection, config, room["id"], transport)
    assert transport.paths() == ["/api/legacy/engagements/eng_key/k1"]


def test_a_single_row_201_created_and_204_updated(store):
    """"``201`` - 'Created' success code, for POST requests and some PATCH
    requests" versus "``204`` - 'No Content' success code"."""
    config = cu.load_config(store)
    room = store.create("room", {"name": "R"}, source="test")
    connection = conn(
        store,
        config,
        room["id"],
        vendor="legacy_table",
        object_name="engagements",
        key_field="eng_key",
        fields={"event_type": "event_type"},
    )
    row(store, room["id"], "k1")
    row(store, room["id"], "k2")

    answers = iter([cu.salesforce_single(201, "L-1", created=True), cu.salesforce_single(204)])
    result = run_over(
        store, connection, config, room["id"], cu.ScriptedTransport(lambda _request: next(answers))
    )
    by_key = {entry.key: entry for entry in result.outcomes}
    assert by_key["k1"].outcome == "created"
    assert by_key["k2"].outcome == "updated"
    assert by_key["k1"].crm_record_id == "L-1"
    assert by_key["k2"].crm_record_id == ""


def test_update_only_reaches_the_fallback_request(store):
    config = cu.load_config(store)
    room = store.create("room", {"name": "R"}, source="test")
    connection = conn(
        store,
        config,
        room["id"],
        vendor="legacy_table",
        object_name="engagements",
        key_field="eng_key",
        update_only=True,
        fields={"event_type": "event_type"},
    )
    row(store, room["id"], "k1")
    transport = cu.ScriptedTransport(cu.salesforce_single(204))
    run_over(store, connection, config, room["id"], transport)
    assert transport.queries() == [{"updateOnly": "true"}]


# --------------------------------------------------------------------------- #
# Chunking a real queue, and the per-connection queue
# --------------------------------------------------------------------------- #


def test_a_queue_is_chunked_at_the_connections_batch_size(store):
    config = cu.load_config(store)
    room = store.create("room", {"name": "R"}, source="test")
    connection = conn(store, config, room["id"], batch_size=2)
    for index in range(5):
        row(store, room["id"], f"k{index}")

    transport = cu.ScriptedTransport(lambda request: cu.salesforce_upsert_results(ok_results(len(request.body["records"]))))
    result = run_over(store, connection, config, room["id"], transport)

    assert transport.count == 3
    assert [len(request.body["records"]) for request in transport.requests] == [2, 2, 1]
    assert result.record["data"]["batch_size"] == 2
    assert len(result.record["data"]["chunks"]) == 3


def test_two_connections_keep_two_independent_queues(store):
    """One flat flag would make them fight: each would re-send the other's rows."""
    config = cu.load_config(store)
    room = store.create("room", {"name": "R"}, source="test")
    salesforce = conn(store, config, room["id"], batch_size=50)
    dataverse = conn(
        store,
        config,
        room["id"],
        vendor="dataverse",
        object_name="engagements",
        key_field="sample_keyattribute",
        fields={"event_type": "eventtype"},
    )
    row(store, room["id"], "k1")

    run_over(
        store,
        salesforce,
        config,
        room["id"],
        cu.ScriptedTransport(cu.salesforce_upsert_results([{"id": "a01", "success": True, "created": True}])),
    )
    assert cu.pending_rows(store, salesforce, config, room_id=room["id"]) == []
    assert len(cu.pending_rows(store, dataverse, config, room_id=room["id"])) == 1


def test_a_write_back_keeps_a_sibling_connections_state(store):
    """``store.update`` merges shallowly, so writing one connection's state alone
    would silently drop the other's."""
    config = cu.load_config(store)
    room = store.create("room", {"name": "R"}, source="test")
    first = conn(store, config, room["id"], batch_size=50)
    second = conn(store, config, room["id"], vendor="dataverse", object_name="engagements",
                  key_field="sample_keyattribute", fields={"event_type": "eventtype"})
    queued = row(store, room["id"], "k1")

    run_over(
        store,
        first,
        config,
        room["id"],
        cu.ScriptedTransport(cu.salesforce_upsert_results([{"id": "a01", "success": True, "created": True}])),
    )
    run_over(store, second, config, room["id"], cu.ScriptedTransport(cu.dataverse_upsert_multiple()))

    sync = store.get(queued["id"])["data"]["sync"]
    assert sync[first.id]["status"] == "synced"
    assert sync[second.id]["status"] == "unconfirmed"
    assert sync[first.id]["run_id"] and sync[second.id]["run_id"]


def test_an_unsaved_connection_is_refused_rather_than_sharing_a_queue(store):
    """The queue is keyed on the connection id, so an unsaved one would fall back
    to the row-level state and share a queue with every other connection."""
    config = cu.load_config(store)
    room = store.create("room", {"name": "R"}, source="test")
    row(store, room["id"], "k1")
    unsaved = cu.Connection(
        vendor="salesforce", object="Engagement__c", key_field="Ext__c", key_source="engagement_id"
    )
    with pytest.raises(UpsertError) as caught:
        run_over(store, unsaved, config, room["id"], cu.ScriptedTransport())
    assert "has not been saved" in str(caught.value)


def test_an_unknown_driver_is_refused(store):
    config = cu.load_config(store)
    room = store.create("room", {"name": "R"}, source="test")
    connection = conn(store, config, room["id"])
    with pytest.raises(UpsertError) as caught:
        run_over(store, connection, config, room["id"], cu.ScriptedTransport(), driver="crm_side_trigger")
    assert "driver must be one of" in str(caught.value)


def test_a_run_records_its_transport_so_a_simulation_is_visible(store):
    config = cu.load_config(store)
    room = store.create("room", {"name": "R"}, source="test")
    connection = conn(store, config, room["id"])
    row(store, room["id"], "k1")
    result = run_over(store, connection, config, room["id"], cu.SimulatedTransport())
    assert result.record["data"]["transport"] == "simulated"


def test_a_run_writes_only_the_two_collections_the_research_names(store):
    """"Nothing runs inside the CRM in this workflow" - a run writes the room's
    rows and its own log, and registers nothing on the CRM side."""
    config = cu.load_config(store)
    room = store.create("room", {"name": "R"}, source="test")
    connection = conn(store, config, room["id"])
    row(store, room["id"], "k1")

    before = {entry["collection"] for entry in store.collections()}
    run_over(
        store,
        connection,
        config,
        room["id"],
        cu.ScriptedTransport(cu.salesforce_upsert_results([{"id": "a01", "success": True, "created": True}])),
    )
    after = {entry["collection"] for entry in store.collections()}
    assert after - before <= set(run_mod.WRITTEN_COLLECTIONS)


def test_the_written_collections_constant_is_exactly_the_two():
    assert run_mod.WRITTEN_COLLECTIONS == frozenset({"engagement", "crm_upsert_run"})


def test_progress_percent_ignores_rows_that_were_never_sent(store):
    """A bar stuck below 100% because of rows it deliberately refused would be
    lying about a finished run."""
    config = cu.load_config(store)
    room = store.create("room", {"name": "R"}, source="test")
    connection = conn(store, config, room["id"])
    row(store, room["id"], "")
    row(store, room["id"], "k1")

    result = run_over(
        store,
        connection,
        config,
        room["id"],
        cu.ScriptedTransport(cu.salesforce_upsert_results(ok_results(1))),
    )
    assert result.progress["rows_total"] == 2
    assert result.progress["rows_rejected"] == 1
    assert result.progress["rows_sent"] == 1
    assert result.progress["percent"] == 100.0


def test_a_run_with_nothing_pending_is_a_zero_row_success(store):
    """A scheduler firing on a quiet room should see that it had nothing to do."""
    config = cu.load_config(store)
    room = store.create("room", {"name": "R"}, source="test")
    connection = conn(store, config, room["id"])
    transport = cu.ScriptedTransport(cu.salesforce_upsert_results([]))
    result = run_over(store, connection, config, room["id"], transport)
    assert transport.count == 0
    assert result.outcomes == []
    assert result.record["data"]["totals"]["rows"] == 0
    assert result.progress["percent"] == 100.0


def test_a_limit_takes_only_that_many_rows(store):
    config = cu.load_config(store)
    room = store.create("room", {"name": "R"}, source="test")
    connection = conn(store, config, room["id"], batch_size=10)
    for index in range(5):
        row(store, room["id"], f"k{index}")
    transport = cu.ScriptedTransport(
        lambda request: cu.salesforce_upsert_results(ok_results(len(request.body["records"])))
    )
    result = run_over(store, connection, config, room["id"], transport, limit=2)
    assert len(result.outcomes) == 2
    assert len(cu.pending_rows(store, connection, config, room_id=room["id"])) == 3


def test_the_run_record_stores_the_full_request_per_chunk(store):
    """A log saying 'sent 5 rows' could not show that a chunk carried a record id."""
    config = cu.load_config(store)
    room = store.create("room", {"name": "R"}, source="test")
    connection = conn(store, config, room["id"], batch_size=2, all_or_none=True)
    for index in range(2):
        row(store, room["id"], f"k{index}")
    result = run_over(
        store,
        connection,
        config,
        room["id"],
        cu.ScriptedTransport(cu.salesforce_upsert_results(ok_results(2))),
    )
    chunk = result.record["data"]["chunks"][0]
    assert chunk["method"] == "PATCH"
    assert chunk["query"] == {"allOrNone": "true"}
    assert len(chunk["body"]["records"]) == 2
    assert chunk["body"]["records"][0]["attributes"]["type"] == "Engagement__c"
    assert chunk["status"] == 200
    assert chunk["progress"]["rows_done"] == 2


def test_a_response_with_no_per_item_detail_fails_the_chunk(store):
    config = cu.load_config(store)
    room = store.create("room", {"name": "R"}, source="test")
    connection = conn(store, config, room["id"])
    row(store, room["id"], "k1")
    result = run_over(
        store,
        connection,
        config,
        room["id"],
        cu.ScriptedTransport(cu.OutboundResponse(status=400, body=[{"errorCode": "INVALID", "message": "bad batch"}])),
    )
    assert result.outcomes[0].outcome == "failed"
    assert "bad batch" in result.outcomes[0].errors[0]


# --------------------------------------------------------------------------- #
# Connections, config, the queue, and the backlog triggers
# --------------------------------------------------------------------------- #


def test_the_default_config_publishes_the_researched_queue_target():
    assert cu.DEFAULT_CONFIG["queue"]["target"] == 200


def test_the_default_config_names_the_key_source_field():
    assert cu.DEFAULT_CONFIG["mapping"]["key_source"] == "engagement_id"


def test_config_saving_is_a_deep_merge(store):
    cu.save_config(store, {"thresholds": {"a": 1}}, source="test")
    cu.save_config(store, {"thresholds": {"b": 2}}, source="test")
    merged = cu.load_config(store)
    assert merged["thresholds"] == {"a": 1, "b": 2}
    assert merged["queue"]["target"] == 200


def test_config_saving_requires_a_source(store):
    """An audit row that cannot be traced to a request is not an audit trail."""
    with pytest.raises(TypeError):
        cu.save_config(store, {})  # type: ignore[call-arg]


def test_a_connection_is_validated_on_save(store):
    config = cu.load_config(store)
    with pytest.raises(UnsupportedKey):
        cu.validate_connection(store, {"vendor": "salesforce", "object": "E", "key_field": "Id"}, config=config)


def test_a_connection_must_name_a_vendor(store):
    config = cu.load_config(store)
    with pytest.raises(UpsertError) as caught:
        cu.validate_connection(store, {"object": "E", "key_field": "K"}, config=config)
    assert "vendor is required" in str(caught.value)


def test_a_connection_must_name_an_object(store):
    config = cu.load_config(store)
    with pytest.raises(UpsertError) as caught:
        cu.validate_connection(store, {"vendor": "salesforce", "key_field": "K"}, config=config)
    assert "object is required" in str(caught.value)


def test_a_connection_must_name_a_key_field(store):
    config = cu.load_config(store)
    with pytest.raises(UpsertError) as caught:
        cu.validate_connection(store, {"vendor": "salesforce", "object": "E"}, config=config)
    assert "key_field is required" in str(caught.value)


def test_a_connection_cannot_map_onto_a_record_id(store):
    config = cu.load_config(store)
    with pytest.raises(UpsertError) as caught:
        cu.validate_connection(
            store,
            {
                "vendor": "salesforce",
                "object": "E",
                "key_field": "Ext__c",
                "fields": {"a": "Id"},
            },
            config=config,
        )
    assert "which is a CRM record id" in str(caught.value)


def test_a_connection_cannot_ask_for_an_over_cap_batch(store):
    config = cu.load_config(store)
    with pytest.raises(BatchTooLarge):
        cu.validate_connection(
            store, {"vendor": "hubspot", "object": "contacts", "key_field": "email", "batch_size": 250}, config=config
        )


def test_a_connection_with_no_batch_size_keeps_the_vendor_cap(store):
    config = cu.load_config(store)
    connection = cu.validate_connection(
        store, {"vendor": "hubspot", "object": "contacts", "key_field": "email"}, config=config
    )
    assert connection.batch_size == HUBSPOT_CAP


def test_hubspot_on_email_does_not_support_partial_upserts(store):
    config = cu.load_config(store)
    connection = cu.validate_connection(
        store, {"vendor": "hubspot", "object": "contacts", "key_field": "email"}, config=config
    )
    assert connection.partial_upserts_supported(cap.HUBSPOT) is False


def test_hubspot_on_another_key_does_support_partial_upserts(store):
    """The refusal is scoped to the researched sentence, not to the vendor."""
    config = cu.load_config(store)
    connection = cu.validate_connection(
        store, {"vendor": "hubspot", "object": "contacts", "key_field": "dsr_key"}, config=config
    )
    assert connection.partial_upserts_supported(cap.HUBSPOT) is True


def test_salesforce_does_support_partial_upserts(store):
    config = cu.load_config(store)
    connection = cu.validate_connection(
        store, {"vendor": "salesforce", "object": "E", "key_field": "Ext__c"}, config=config
    )
    assert connection.partial_upserts_supported(cap.SALESFORCE) is True


def test_an_unknown_connection_is_a_lookup_error(store):
    with pytest.raises(UnknownConnection):
        cu.load_connection(store, "crm_upsert_connection_nope", cu.load_config(store))


def test_a_record_of_another_collection_is_not_a_connection(store):
    room = store.create("room", {"name": "R"}, source="test")
    with pytest.raises(UnknownConnection):
        cu.load_connection(store, room["id"], cu.load_config(store))


def test_a_row_with_no_sync_status_is_pending(store):
    assert cu.row_status({"data": {}}) == "pending"


def test_row_status_reads_the_row_level_mirror_without_a_connection(store):
    assert cu.row_status({"data": {"sync_status": "synced"}}) == "synced"


def test_row_status_prefers_the_connections_own_state(store):
    record = {"data": {"sync_status": "synced", "sync": {"conn_1": {"status": "pending"}}}}
    assert cu.row_status(record, "conn_1") == "pending"
    assert cu.row_status(record) == "synced"


def test_connection_state_of_a_row_with_no_sync_map_is_empty(store):
    assert cu.connection_state({}, "conn_1") == {}
    assert cu.connection_state({"sync": "not a map"}, "conn_1") == {}
    assert cu.connection_state({"sync": {"conn_1": "not a map"}}, "conn_1") == {}


def test_the_queue_target_is_published_and_never_truncates_a_run(store):
    config = cu.load_config(store)
    room = store.create("room", {"name": "R"}, source="test")
    connection = conn(store, config, room["id"], batch_size=200)
    for index in range(3):
        row(store, room["id"], f"k{index}")
    view = cu.queue_view(store, connection, config, room_id=room["id"])
    assert view["target"] == 200
    assert view["at_target"] is False
    assert len(view["rows"]) == 3


def test_the_queue_counts_at_target_when_full(store):
    config = cu.load_config(store)
    store.create(
        "crm_upsert_config", {"key": "settings", "queue": {"target": 2}}, source="test"
    )
    config = cu.load_config(store)
    room = store.create("room", {"name": "R"}, source="test")
    connection = conn(store, config, room["id"])
    for index in range(2):
        row(store, room["id"], f"k{index}")
    view = cu.queue_view(store, connection, config, room_id=room["id"])
    assert view["target"] == 2
    assert view["at_target"] is True


def test_the_queue_is_scoped_to_one_room(store):
    config = cu.load_config(store)
    first = store.create("room", {"name": "A"}, source="test")
    second = store.create("room", {"name": "B"}, source="test")
    connection = conn(store, config, first["id"])
    row(store, first["id"], "k1")
    row(store, second["id"], "k2")
    view = cu.queue_view(store, connection, config, room_id=first["id"])
    assert view["total"] == 1


# -- the researched triggers ------------------------------------------------- #


def test_an_empty_queue_is_not_due(store):
    config = cu.load_config(store)
    room = store.create("room", {"name": "R"}, source="test")
    connection = conn(store, config, room["id"])
    state = cu.backlog(store, connection, config, room_id=room["id"])
    assert state["due"] is False
    assert "the queue is empty" in state["reasons"]


def test_a_never_synced_connection_with_rows_is_due(store):
    config = cu.load_config(store)
    room = store.create("room", {"name": "R"}, source="test")
    connection = conn(store, config, room["id"])
    row(store, room["id"], "k1")
    state = cu.backlog(store, connection, config, room_id=room["id"])
    assert state["due"] is True
    assert any("never been synced" in reason for reason in state["reasons"])


def test_a_queue_over_the_threshold_is_due(store):
    """The researched opportunistic trigger: "when the queue exceeds N rows"."""
    store.create(
        "crm_upsert_config",
        {"key": "settings", "queue": {"opportunistic_threshold": 2}},
        source="test",
    )
    config = cu.load_config(store)
    room = store.create("room", {"name": "R"}, source="test")
    connection = conn(store, config, room["id"])
    for index in range(3):
        row(store, room["id"], f"k{index}")
    state = cu.backlog(store, connection, config, room_id=room["id"], now=NOW)
    assert state["threshold"] == 2
    assert state["over_threshold"] is True
    assert state["due"] is True
    assert any("opportunistic threshold" in reason for reason in state["reasons"])


def test_a_queue_under_the_threshold_is_not_opportunistically_due(store):
    store.create(
        "crm_upsert_config",
        {"key": "settings", "queue": {"opportunistic_threshold": 10}},
        source="test",
    )
    config = cu.load_config(store)
    room = store.create("room", {"name": "R"}, source="test")
    connection = conn(store, config, room["id"])
    row(store, room["id"], "k1")
    run_over(
        store,
        connection,
        config,
        room["id"],
        cu.ScriptedTransport(cu.salesforce_upsert_results([{"id": "a01", "success": True, "created": True}])),
    )
    row(store, room["id"], "k2")
    state = cu.backlog(store, connection, config, room_id=room["id"], now=NOW)
    assert state["over_threshold"] is False
    assert state["due"] is False


def test_the_nightly_interval_is_reported_and_honoured(store):
    config = cu.load_config(store)
    room = store.create("room", {"name": "R"}, source="test")
    connection = conn(store, config, room["id"])
    row(store, room["id"], "k1")
    run_over(
        store,
        connection,
        config,
        room["id"],
        cu.ScriptedTransport(cu.salesforce_upsert_results([{"id": "a01", "success": True, "created": True}])),
    )
    room_id = room["id"]
    store.create("engagement", {"engagement_id": "k2", "event_type": "viewed"}, room_id=room_id, source="test")

    just_after = cu.backlog(store, connection, config, room_id=room_id, now=NOW + timedelta(minutes=5))
    assert just_after["due"] is False

    a_later = cu.backlog(store, connection, config, room_id=room_id, now=NOW + timedelta(hours=25))
    assert a_later["due"] is True
    assert any("since the last run" in reason for reason in a_later["reasons"])


def test_the_backlog_reports_its_next_due_time(store):
    config = cu.load_config(store)
    room = store.create("room", {"name": "R"}, source="test")
    connection = conn(store, config, room["id"])
    row(store, room["id"], "k1")
    run_over(
        store,
        connection,
        config,
        room["id"],
        cu.ScriptedTransport(cu.salesforce_upsert_results([{"id": "a01", "success": True, "created": True}])),
    )
    state = cu.backlog(store, connection, config, room_id=room["id"], now=NOW + timedelta(hours=1))
    assert state["next_due_at"]
    assert state["last_run_id"]


def test_the_backlog_counts_unconfirmed_rows(store):
    config = cu.load_config(store)
    room = store.create("room", {"name": "R"}, source="test")
    connection = conn(
        store,
        config,
        room["id"],
        vendor="dataverse",
        object_name="engagements",
        key_field="sample_keyattribute",
        fields={"event_type": "eventtype"},
    )
    row(store, room["id"], "k1")
    run_over(store, connection, config, room["id"], cu.ScriptedTransport(cu.dataverse_upsert_multiple()))
    state = cu.backlog(store, connection, config, room_id=room["id"], now=NOW)
    assert state["unconfirmed"] == 1
    assert state["pending"] == 0
    assert state["due"] is False


# -- the researched gap, reported as a gap ---------------------------------- #


def test_the_lint_reports_the_salesforce_email_gap_without_recommending_a_fix(store):
    """The research documents the 404 but says its workarounds "were not
    cross-verified against a second source", so the lint must not recommend one."""
    config = cu.load_config(store)
    connection = cu.validate_connection(
        store, {"vendor": "salesforce", "object": "E", "key_field": "email"}, config=config
    )
    advisories = {entry["id"]: entry for entry in cu.lint(connection, config, store)}
    gap = advisories["salesforce-email-external-id"]
    assert gap["kind"] == "documented_gap"
    assert "example@email.inc" in gap["message"]
    assert "did not cross-verify" in gap["message"]


def test_the_gap_advisory_suggests_no_workaround(store):
    config = cu.load_config(store)
    connection = cu.validate_connection(
        store, {"vendor": "salesforce", "object": "E", "key_field": "email"}, config=config
    )
    for entry in cu.lint(connection, config, store):
        assert "change_it" not in entry or "email" not in str(entry.get("change_it", "")).lower()


def test_an_external_id_field_raises_no_email_gap(store):
    config = cu.load_config(store)
    connection = cu.validate_connection(
        store, {"vendor": "salesforce", "object": "E", "key_field": "Ext__c"}, config=config
    )
    assert "salesforce-email-external-id" not in {entry["id"] for entry in cu.lint(connection, config, store)}


def test_the_lint_reports_the_hubspot_partial_upsert_rule(store):
    config = cu.load_config(store)
    connection = cu.validate_connection(
        store,
        {"vendor": "hubspot", "object": "contacts", "key_field": "email", "required_properties": ["lastname"]},
        config=config,
    )
    advisories = {entry["id"]: entry for entry in cu.lint(connection, config, store)}
    assert advisories["hubspot-email-no-partial-upsert"]["kind"] == "sourced_rule"
    assert "lastname" in advisories["hubspot-email-no-partial-upsert"]["change_it"]


def test_the_lint_reports_a_vendor_that_cannot_confirm_a_row(store):
    config = cu.load_config(store)
    connection = cu.validate_connection(
        store,
        {"vendor": "dataverse", "object": "engagements", "key_field": "sample_keyattribute"},
        config=config,
    )
    advisories = {entry["id"]: entry for entry in cu.lint(connection, config, store)}
    assert advisories["no-per-item-results"]["kind"] == "sourced_rule"
    assert "submitted" in advisories["no-per-item-results"]["message"]


def test_the_lint_marks_a_local_capability_as_an_inference(store):
    config = cu.load_config(store)
    connection = cu.validate_connection(
        store, {"vendor": "legacy_table", "object": "engagements", "key_field": "eng_key"}, config=config
    )
    advisories = {entry["id"]: entry for entry in cu.lint(connection, config, store)}
    assert advisories["local-capability"]["kind"] == "inference"


def test_the_lint_reports_an_unresolvable_vendor_as_an_error(store):
    config = cu.load_config(store)
    connection = cu.Connection(vendor="nobody", object="E", key_field="K", id="c1")
    advisories = cu.lint(connection, config, store)
    assert advisories[0]["kind"] == "error"
    assert advisories[0]["id"] == "no-capability"


# --------------------------------------------------------------------------- #
# Inferences
# --------------------------------------------------------------------------- #


def test_every_inference_is_named_and_traceable():
    assert cu.INFERENCES
    for entry in cu.INFERENCES:
        assert entry["id"]
        assert entry["topic"]
        assert entry["basis"]
        assert entry["why"]
        assert entry["change_it"]


def test_inference_ids_are_unique():
    ids = [entry["id"] for entry in cu.INFERENCES]
    assert len(ids) == len(set(ids))


def test_the_dataverse_inference_quotes_the_204():
    entry = next(e for e in cu.INFERENCES if e["id"] == "unconfirmed-is-a-state")
    assert "204 NoContent" in entry["basis"]


def test_the_no_transport_inference_is_recorded_rather_than_glossed():
    entry = next(e for e in cu.INFERENCES if e["id"] == "no-network-transport-ships")
    assert "does_not_ship" in entry["value"]
    assert "Transport.send" in entry["change_it"]


def test_describe_reports_a_count():
    assert cu.describe_inferences()["count"] == len(cu.INFERENCES)
