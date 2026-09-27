"""Tests for WF-036: provision the sales-room engagement object into the CRM.

The claims under test come from
``docs/research/digital-sales-room-workflows/wf/WF-036.md``, and each block below
names the sentence it is holding up:

* the flow's steps 2 and 3 - read the live schema, create only what is missing,
  never destructively rename or drop an existing field;
* step 4 - "Creation is idempotent: re-running the installer is a no-op for
  already-present properties";
* the data flow's last clause - record the room-object-id to CRM-object-id mapping
  for subsequent syncs;
* the HubSpot property create's required fields, as a body a test can read;
* Dataverse's ``EntityKeyMetadata`` + ``CreateEntityKey``, its four index
  statuses, ``AsyncJob``, and the "900 bytes per key and 16 columns per key"
  limit;
* the dry-run diff view, and ``ReactivateEntityKey`` as the repair extension point;
* the extensibility claim that the descriptor is data, not code;
* the research's own gap about Salesforce, which is a refusal rather than a silence.

There is no CRM account behind this build, so the vendor's schema is simulated in
this product's audited store. That is a judgement rather than a sourced fact, it
is recorded as the ``simulated-vendor-state`` inference, and it is the reason
idempotency can be asserted as behaviour here instead of as a comment: the second
run really does find the first run's properties and really does create nothing.
"""

from __future__ import annotations

import inspect
import json
import random
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from dsr.api import app
from dsr.crm_provisioning import ProvisioningEngine
from dsr.crm_provisioning.diff import key_metadata, object_request, property_request
from dsr.crm_provisioning.errors import (
    DuplicateManifestVersion,
    KeyConstraintError,
    ManifestError,
    PropertyConflict,
    ProvisioningError,
    UnsupportedVendor,
)
from dsr.crm_provisioning.gateway import DEFAULT_INDEX_POLLS, SimulatedCrm
from dsr.crm_provisioning.inferences import INFERENCES, by_id
from dsr.crm_provisioning.manifest import (
    advisory,
    blocking,
    check_key,
    findings_for,
    key_bytes,
    normalise_manifest,
    normalise_options,
    property_level,
    validate,
)
from dsr.crm_provisioning.vendors import (
    ADAPTERS,
    DATAVERSE,
    HUBSPOT,
    adapter_for,
    describe_adapter,
)
from dsr.crm_provisioning.vocabulary import (
    FINDING_SEVERITIES,
    KEY_ACTIONS,
    KEY_MAX_BYTES,
    KEY_MAX_COLUMNS,
    KEY_STATUSES,
    KEY_STATUS_PROGRESSION,
    OBJECT_ACTIONS,
    OUTCOMES,
    PROPERTY_ACTIONS,
    PROPERTY_TYPES,
    UNSUPPORTED_VENDORS,
    VENDORS,
)
from dsr.db.audited import AuditedDatabase, RecordNotFound
from dsr.features import load_feature
from dsr.store import RecordStore

#: The feature's own prefix. Duplicated here rather than imported so a change to
#: the prefix has to be made deliberately in the test as well, which is the point
#: of a test: a renamed route should fail, not follow silently.
PREFIX = "/api/wf-036"

#: What the pure-domain tests pass as ``source``. Deliberately the exact shape a
#: route passes, so a test asserting on an audit row is asserting on the real
#: thing rather than on a convenient placeholder.
SOURCE = f"POST {PREFIX}/install"

MODULE_NAME = "wf036_provision_the_sales_room_engagement_ob"

GROUP = "dsr-engagement"


# --------------------------------------------------------------------------- #
# Fixtures and builders
# --------------------------------------------------------------------------- #


def manifest_body(**overrides: Any) -> dict[str, Any]:
    """The shipped engagement descriptor, as an arbitrary JSON body."""
    body: dict[str, Any] = {
        "manifest_id": "dsr_engagement",
        "version": "1.0.0",
        "name": "Sales-room engagement",
        "description": "One row per buyer interaction with a room's content.",
        "room_object_id": "dsr.engagement",
        "object": {"name": "dsr_engagement", "label": "Sales-room engagement"},
        "properties": [
            {"name": "engagement_id", "label": "Engagement id", "type": "string",
             "group_name": GROUP, "length": 64, "required": True,
             "description": "The sales room's own row id. Carries the sync key."},
            {"name": "room_name", "label": "Room name", "type": "string",
             "group_name": GROUP, "length": 120},
            {"name": "action", "label": "Action", "type": "enumeration",
             "group_name": GROUP,
             "options": [{"label": "Viewed", "value": "viewed"},
                         {"label": "Downloaded", "value": "downloaded"}]},
            {"name": "seconds_on_page", "label": "Seconds on page", "type": "number",
             "group_name": GROUP},
        ],
        "sync_key": {"columns": [{"name": "engagement_id", "length": 64}]},
    }
    body.update(overrides)
    return body


def wide_key_manifest(**overrides: Any) -> dict[str, Any]:
    """A manifest whose key is over the researched 900 byte limit."""
    columns = [
        {"name": "engagement_id", "label": "Engagement id", "type": "string",
         "group_name": GROUP, "length": 200}
    ]
    for index in range(1, 5):
        columns.append(
            {"name": f"scoped_{index}", "label": f"Scoped {index}", "type": "string",
             "group_name": GROUP, "length": 200}
        )
    return manifest_body(
        manifest_id="dsr_engagement_scoped",
        version="0.1.0",
        object={"name": "dsr_engagement_scoped", "label": "Engagement (scoped)"},
        properties=columns,
        sync_key={"columns": [{"name": c["name"], "length": 200} for c in columns]},
        **overrides,
    )


@pytest.fixture()
def store(tmp_path):
    db = AuditedDatabase(tmp_path / "wf036.db", mirror_dir=tmp_path / "mirror")
    yield RecordStore(db)
    db.close()


@pytest.fixture()
def engine(store):
    return ProvisioningEngine(store, crm=SimulatedCrm(store, rng=random.Random("tests")))


@pytest.fixture()
def connection(engine):
    return engine.register_connection(
        {"name": "Northwind HubSpot", "vendor": "hubspot", "room_id": "room_1"},
        actor="dana",
        source=SOURCE,
    )


@pytest.fixture()
def dataverse_connection(engine):
    return engine.register_connection(
        {"name": "Contoso Dataverse", "vendor": "dataverse", "room_id": "room_2"},
        actor="dana",
        source=SOURCE,
    )


@pytest.fixture()
def manifest(engine):
    return engine.register_manifest(manifest_body(), actor="dana", source=SOURCE)


@pytest.fixture()
def installed(engine, connection, manifest):
    return engine.install(
        connection["id"], "dsr_engagement", "1.0.0", actor="dana", source=SOURCE
    )


@pytest.fixture()
def http(monkeypatch):
    """A client over a temporary database, exactly as test_features.py sets one up."""
    tmp = tempfile.TemporaryDirectory()
    monkeypatch.setenv("DSR_DB_PATH", str(Path(tmp.name) / "wf036.db"))
    monkeypatch.setenv("DSR_AUDIT_DIR", str(Path(tmp.name) / "audit"))
    monkeypatch.setattr("dsr.api.FRONTEND_DIST", Path(tmp.name) / "absent-frontend")
    with TestClient(app) as client:
        yield client
    tmp.cleanup()


@pytest.fixture()
def http_engine(http):
    """The engine the HTTP layer built, so a test can read what the routes wrote."""
    return ProvisioningEngine(http.app.state.store)


def seed_rooms(store: RecordStore) -> list[tuple[str, str]]:
    """Demo rooms in the shape ``backend/seed.py`` passes: ``[(room_id, account)]``."""
    first = store.create("room", {"name": "Northwind", "account": "Northwind Traders"}, actor="dana")
    second = store.create("room", {"name": "Contoso", "account": "Contoso Health"}, actor="dana")
    third = store.create("room", {"name": "Fabrikam", "account": "Fabrikam Logistics"}, actor="dana")
    return [
        (first["id"], "Northwind Traders"),
        (second["id"], "Contoso Health"),
        (third["id"], "Fabrikam Logistics"),
    ]


def register_demo(http, **overrides: Any) -> dict[str, Any]:
    """A connection, a manifest and an install, all through the HTTP routes."""
    connection = http.post(
        f"{PREFIX}/connections",
        json={"name": "Northwind HubSpot", "vendor": "hubspot", **overrides},
    ).json()
    http.post(f"{PREFIX}/manifests", json=manifest_body())
    http.post(
        f"{PREFIX}/install",
        json={"connection_id": connection["id"], "manifest_id": "dsr_engagement"},
    )
    return connection


def _matches_registered_route(source: str, served: list[dict[str, Any]]) -> bool:
    """Whether an audit source names a route the host actually mounted.

    The source is written as the route's own path template, so ``{room_id}`` and a
    real id have to compare equal. That is deliberate: a hardcoded literal id in an
    audit source would still be traceable, and a real id would not be stable across
    records.
    """
    method, _, path = source.partition(" ")
    if not path:
        return False
    segments = [segment for segment in path.split("/") if segment]
    for route in served:
        if method not in route["methods"]:
            continue
        template = [segment for segment in route["path"].split("/") if segment]
        if len(template) != len(segments):
            continue
        if all(
            expected.startswith("{") or expected == found
            for expected, found in zip(template, segments)
        ):
            return True
    return False


# --------------------------------------------------------------------------- #
# The plugin registration itself
# --------------------------------------------------------------------------- #


def test_feature_is_discovered_and_mounted_without_editing_the_host(http):
    """The route resolves even though no shared file names this feature."""
    module = load_feature(MODULE_NAME)
    entry = next(f for f in http.get("/api/features").json()["features"] if f["id"] == module.FEATURE["id"])

    assert entry["prefix"] == PREFIX
    assert entry["ticket"] == "WF-036"
    assert entry["exception_handlers"] == ["ProvisioningError"]
    assert len(entry["routes"]) == 22


def test_the_frontend_descriptor_id_matches_the_backend_feature_id():
    """The two halves of a feature are findable by one name, so they must agree."""
    module = load_feature(MODULE_NAME)
    descriptor = (
        Path(__file__).resolve().parents[2]
        / "frontend" / "src" / "features" / module.FEATURE["id"] / "index.jsx"
    )
    text = descriptor.read_text(encoding="utf-8")

    assert f'id: {module.FEATURE["id"]!r}' in text
    assert f'label:' in text
    assert "Component:" in text


def test_feature_module_does_not_import_the_shared_app():
    """A guard exists in test_features.py; this states the reason locally."""
    source = Path(load_feature(MODULE_NAME).__file__).read_text(encoding="utf-8")
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

    assert len(mine) == 22
    assert not mine & others


def test_the_registry_reports_no_failed_features(http):
    """A feature that raises on import is reported, so this one must not."""
    body = http.get("/api/features").json()
    assert body["failed_count"] == 0
    assert all(row["loaded"] for row in body["features"])


def test_the_domain_package_owns_its_own_path():
    """No two features may claim one domain package."""
    package = Path(__file__).resolve().parents[1] / "dsr" / "crm_provisioning"
    assert package.is_dir()
    names = {path.name for path in package.glob("*.py")}
    assert {
        "__init__.py",
        "diff.py",
        "engine.py",
        "errors.py",
        "gateway.py",
        "inferences.py",
        "manifest.py",
        "vendors.py",
        "vocabulary.py",
    } <= names


def test_no_migration_and_no_typed_column_was_added():
    """The schema is the envelope plus JSON. This is the guard for that rule."""
    schema = (Path(__file__).resolve().parents[1] / "dsr" / "db" / "schema.sql").read_text(
        encoding="utf-8"
    )
    for fragment in ("crm_object", "crm_manifest", "crm_property", "crm_sync_key", "crm_installation"):
        assert fragment not in schema


# --------------------------------------------------------------------------- #
# Published vocabulary, straight from the research
# --------------------------------------------------------------------------- #


def test_the_two_vendors_are_exactly_the_two_the_research_carries():
    assert VENDORS == ("hubspot", "dataverse")
    assert set(ADAPTERS) == set(VENDORS)


def test_salesforce_is_named_as_a_gap_rather_than_absent():
    assert "salesforce" in UNSUPPORTED_VENDORS
    assert "could not be sourced" in UNSUPPORTED_VENDORS["salesforce"]


def test_the_four_index_statuses_are_the_researched_four():
    # Verbatim, spaces and capitals included: a client switches on these.
    assert KEY_STATUSES == ("Pending", "In Progress", "Active", "Failed")


def test_the_key_limits_are_the_researched_limits():
    assert KEY_MAX_BYTES == 900
    assert KEY_MAX_COLUMNS == 16


def test_the_progression_runs_pending_to_active_and_stops_at_failed():
    assert KEY_STATUS_PROGRESSION["Pending"] == "In Progress"
    assert KEY_STATUS_PROGRESSION["In Progress"] == "Active"
    assert KEY_STATUS_PROGRESSION["Failed"] == "Pending"
    assert KEY_STATUS_PROGRESSION["Active"] == "Active"


def test_the_neutral_property_types_are_published_and_fixed():
    assert PROPERTY_TYPES == ("string", "number", "bool", "datetime", "enumeration")
    for vendor in VENDORS:
        assert set(ADAPTERS[vendor].type_map) == set(PROPERTY_TYPES)


def test_the_property_actions_are_a_closed_vocabulary():
    assert PROPERTY_ACTIONS == ("create", "unchanged", "conflict", "unmappable", "left_in_place")
    # There is no action that would change or drop a field, and the closure is what
    # a client renders its badges from.
    assert "update" not in PROPERTY_ACTIONS
    assert "delete" not in PROPERTY_ACTIONS


def test_the_outcomes_are_a_closed_vocabulary():
    assert OUTCOMES == ("created", "unchanged", "dry_run")


def test_the_object_and_key_verdicts_are_closed_vocabularies_too():
    """Two long and five long, and both are published, so a client can render them."""
    assert OBJECT_ACTIONS == ("create", "unchanged")
    assert KEY_ACTIONS == ("create", "unchanged", "absent", "unsupported", "failed")
    served = ProvisioningEngine(RecordStore(AuditedDatabase(":memory:"))).vocabulary()
    assert served["object_actions"] == list(OBJECT_ACTIONS)
    assert served["key_actions"] == list(KEY_ACTIONS)


def test_the_finding_severities_are_a_closed_vocabulary():
    assert FINDING_SEVERITIES == ("blocking", "property", "advisory")


def test_every_vendor_adapter_is_serialisable():
    for vendor in VENDORS:
        described = describe_adapter(ADAPTERS[vendor])
        json.dumps(described)  # a vocabulary endpoint cannot serve a frozenset
        assert described["vendor"] == vendor
        assert described["unmapped_types"] == []


def test_the_vocabulary_endpoint_serves_both_vendor_surfaces(engine):
    served = engine.vocabulary()
    assert served["key_limits"] == {"max_bytes": 900, "max_columns": 16}
    assert [row["vendor"] for row in served["vendors_detail"]] == list(VENDORS)
    hubspot = served["vendors_detail"][0]
    assert hubspot["property_create"] == "/crm/properties/2026-09/{object_type}"


# --------------------------------------------------------------------------- #
# The vendor adapters
# --------------------------------------------------------------------------- #


def test_hubspot_names_the_five_required_property_fields():
    # "HubSpot property creation required fields: groupName ... name ... label ...
    # type ... fieldType."
    assert HUBSPOT.required_property_fields == ("groupName", "name", "label", "type", "fieldType")


def test_dataverse_names_the_attribute_metadata_fields():
    assert DATAVERSE.required_property_fields == (
        "schemaName",
        "AttributeType",
        "AttributeDisplayName",
    )


def test_hubspot_reads_and_writes_the_researched_paths():
    assert HUBSPOT.schema_read == ("/", "/crm-object-schemas/2026-09/schemas", "/crm/v3/schemas/...")
    assert HUBSPOT.object_create == "/crm-object-schemas/2026-09/schemas"
    assert HUBSPOT.property_create == "/crm/properties/2026-09/{object_type}"


def test_dataverse_reads_and_writes_the_researched_paths():
    assert DATAVERSE.schema_read == ("/api/data/v9.2/$metadata", "/api/data/v9.2/EntityDefinitions")
    assert DATAVERSE.object_create == "/api/data/v9.2/EntityDefinitions"
    assert DATAVERSE.property_create == "/api/data/v9.2/EntityDefinitions({object_type})/Attributes"
    assert DATAVERSE.key_create == "/api/data/v9.2/CreateEntityKey"
    assert DATAVERSE.key_reactivate == "/api/data/v9.2/ReactivateEntityKey"


def test_hubspot_has_no_sourced_alternate_key_call():
    """CreateEntityKey is Dataverse's, and this workflow does not source HubSpot's."""
    assert HUBSPOT.key_create is None
    assert HUBSPOT.key_reactivate is None
    assert HUBSPOT.supports_alternate_key is False


def test_dataverse_supports_the_alternate_key():
    assert DATAVERSE.supports_alternate_key is True
    assert DATAVERSE.background_index is True


def test_object_id_style_differs_per_vendor_and_both_are_published():
    assert HUBSPOT.object_id_style == "numeric"
    assert DATAVERSE.object_id_style == "schema_name"


def test_the_property_path_is_built_from_the_object_type():
    assert HUBSPOT.property_path("2145902217") == "/crm/properties/2026-09/2145902217"
    assert DATAVERSE.property_path("dsr_engagement") == (
        "/api/data/v9.2/EntityDefinitions(dsr_engagement)/Attributes"
    )


def test_the_two_vendors_name_a_property_differently():
    assert HUBSPOT.name_field == "name"
    assert DATAVERSE.name_field == "schemaName"
    assert HUBSPOT.label_field == "label"
    assert DATAVERSE.label_field == "AttributeDisplayName"


def test_adapter_for_is_case_insensitive_and_trims():
    assert adapter_for("  HubSpot ").vendor == "hubspot"
    assert adapter_for("DATAVERSE").vendor == "dataverse"


def test_adapter_for_refuses_an_unresearched_vendor_with_the_gap():
    with pytest.raises(UnsupportedVendor) as excinfo:
        adapter_for("salesforce")
    assert excinfo.value.status == 422
    assert excinfo.value.code == "vendor_not_supported"
    assert "could not be sourced" in str(excinfo.value)


def test_adapter_for_refuses_an_unknown_vendor_naming_the_workflow():
    with pytest.raises(UnsupportedVendor) as excinfo:
        adapter_for("pipedrive")
    assert "WF-036" in str(excinfo.value)


def test_an_adapter_checks_its_own_vendor():
    HUBSPOT.check()
    with pytest.raises(UnsupportedVendor):
        adapter_for("salesforce").check()


# --------------------------------------------------------------------------- #
# The manifest is data
# --------------------------------------------------------------------------- #


def test_a_manifest_is_normalised_from_arbitrary_json():
    record = normalise_manifest(manifest_body())
    assert record["manifest_id"] == "dsr_engagement"
    assert record["version"] == "1.0.0"
    assert record["room_object_id"] == "dsr.engagement"
    assert record["object"]["name"] == "dsr_engagement"
    assert [prop["name"] for prop in record["properties"]] == [
        "engagement_id", "room_name", "action", "seconds_on_page",
    ]


def test_a_manifest_needs_an_id():
    with pytest.raises(ManifestError, match="manifest_id is required"):
        normalise_manifest({"object": {"name": "x"}, "properties": [{"name": "a"}]})


def test_a_manifest_needs_an_object_with_a_usable_name():
    with pytest.raises(ManifestError, match="object is required"):
        normalise_manifest({"manifest_id": "m", "properties": [{"name": "a"}]})


def test_an_object_name_must_be_a_crm_safe_identifier():
    with pytest.raises(ManifestError, match="object.name"):
        normalise_manifest({"manifest_id": "m", "object": {"name": "9 bad-name"},
                            "properties": [{"name": "a"}]})


def test_a_manifest_needs_at_least_one_property():
    with pytest.raises(ManifestError, match="at least one field"):
        normalise_manifest({"manifest_id": "m", "object": {"name": "m"}, "properties": []})


def test_a_property_needs_a_name():
    with pytest.raises(ManifestError, match="properties\\[1\\].name is required"):
        normalise_manifest({"manifest_id": "m", "object": {"name": "m"},
                            "properties": [{"name": "a"}, {"label": "b"}]})


def test_a_property_name_must_be_a_crm_safe_identifier():
    with pytest.raises(ManifestError, match="must start with a letter"):
        normalise_manifest({"manifest_id": "m", "object": {"name": "m"},
                            "properties": [{"name": "9lives"}]})


def test_a_version_must_be_a_short_opaque_token():
    with pytest.raises(ManifestError, match="version"):
        normalise_manifest(manifest_body(version="a version with spaces"))


def test_a_version_defaults_to_one():
    body = manifest_body()
    del body["version"]
    assert normalise_manifest(body)["version"] == "1"


def test_a_manifest_id_must_be_crm_safe():
    with pytest.raises(ManifestError, match="manifest_id"):
        normalise_manifest({"manifest_id": "1bad", "object": {"name": "m"},
                            "properties": [{"name": "a"}]})


def test_a_label_defaults_from_the_name():
    record = normalise_manifest(manifest_body())
    assert record["properties"][1]["label"] == "Room name"


def test_an_object_label_defaults_from_the_name():
    record = normalise_manifest({"manifest_id": "m", "object": {"name": "dsr_thing"},
                                 "properties": [{"name": "a"}]})
    assert record["object"]["label"] == "Dsr Thing"


def test_the_sync_key_is_accepted_under_either_spelling():
    with_field = normalise_manifest(manifest_body())
    with_other = manifest_body()
    with_other["key"] = with_other.pop("sync_key")
    assert normalise_manifest(with_other)["sync_key"] == with_field["sync_key"]


def test_a_sync_key_column_needs_a_name():
    with pytest.raises(ManifestError, match="columns\\[0\\].name is required"):
        normalise_manifest(manifest_body(sync_key={"columns": [{"length": 4}]}))


def test_a_sync_key_needs_at_least_one_column():
    with pytest.raises(ManifestError, match="non-empty list"):
        normalise_manifest(manifest_body(sync_key={"columns": []}))


def test_a_sync_key_column_length_must_be_a_positive_integer():
    with pytest.raises(ManifestError, match="positive integer length"):
        normalise_manifest(manifest_body(sync_key={"columns": [{"name": "engagement_id", "length": 0}]}))


def test_a_sync_key_column_may_be_named_by_its_field_alias():
    record = normalise_manifest(manifest_body(sync_key={"columns": [{"field": "engagement_id", "length": 8}]}))
    assert record["sync_key"]["columns"] == [{"name": "engagement_id", "length": 8}]


def test_an_option_set_accepts_labels_and_values_or_plain_strings():
    assert normalise_options(["a", "b"]) == [{"label": "a", "value": "a"}, {"label": "b", "value": "b"}]
    assert normalise_options([{"label": "A", "value": "a"}]) == [{"label": "A", "value": "a"}]


def test_an_option_set_must_be_a_list():
    with pytest.raises(ManifestError, match="options must be a list"):
        normalise_options("a,b")


def test_an_empty_option_is_refused():
    with pytest.raises(ManifestError, match="options\\[0\\] is empty"):
        normalise_options([""])


def test_the_room_object_id_defaults_to_the_manifest_id():
    body = manifest_body()
    del body["room_object_id"]
    assert normalise_manifest(body)["room_object_id"] == "dsr_engagement"


# -- findings --------------------------------------------------------------- #


def test_a_clean_manifest_has_no_property_or_blocking_findings_for_either_vendor():
    """Only the advisory about HubSpot's unsourced key call, which is not a defect."""
    record = normalise_manifest(manifest_body())
    for vendor in VENDORS:
        found = findings_for(record, ADAPTERS[vendor])
        assert blocking(found) == []
        assert property_level(found) == []


def test_a_duplicate_property_name_blocks_the_whole_manifest():
    body = manifest_body()
    body["properties"] = body["properties"] + [dict(body["properties"][0])]
    found = findings_for(normalise_manifest(body), HUBSPOT)
    assert blocking(found)[0]["code"] == "duplicate_property"
    assert blocking(found)[0]["property"] == "engagement_id"


def test_an_unknown_type_skips_one_property_and_names_it():
    body = manifest_body()
    body["properties"] = body["properties"] + [
        {"name": "weird", "label": "Weird", "type": "geo", "group_name": GROUP}
    ]
    record = normalise_manifest(body)
    found = findings_for(record, HUBSPOT)
    assert [f["property"] for f in property_level(found)] == ["weird"]
    assert property_level(found)[0]["code"] == "unknown_type"
    assert blocking(found) == []


def test_a_property_with_no_group_name_is_skipped_on_hubspot_only():
    """groupName is a required HubSpot field and the research names no default."""
    body = manifest_body()
    body["properties"] = [{"name": "loose", "label": "Loose", "type": "string", "length": 20}]
    record = normalise_manifest(body)

    hubspot = findings_for(record, HUBSPOT)
    assert [f["code"] for f in property_level(hubspot)] == ["missing_required_field"]
    assert "group_name" in property_level(hubspot)[0]["message"]

    assert property_level(findings_for(record, DATAVERSE)) == []


def test_a_repeated_option_is_advisory_not_blocking():
    body = manifest_body()
    body["properties"] = [
        {
            "name": "action", "label": "Action", "type": "enumeration", "group_name": GROUP,
            "options": [{"label": "Viewed", "value": "viewed"}, {"label": "Viewed", "value": "again"}],
        }
    ]
    found = findings_for(normalise_manifest(body), HUBSPOT)
    assert "duplicate_option" in [f["code"] for f in advisory(found)]


def test_an_enumeration_with_no_option_set_is_advisory():
    body = manifest_body()
    body["properties"] = [{"name": "action", "label": "Action", "type": "enumeration",
                           "group_name": GROUP}]
    found = findings_for(normalise_manifest(body), HUBSPOT)
    assert "no_options" in [f["code"] for f in advisory(found)]


def test_a_string_with_no_length_is_advisory():
    body = manifest_body()
    body["properties"] = [{"name": "room_name", "label": "Room", "type": "string",
                           "group_name": GROUP}]
    found = findings_for(normalise_manifest(body), HUBSPOT)
    assert "no_length" in [f["code"] for f in advisory(found)]


def test_a_sync_key_on_a_vendor_with_no_key_call_is_advisory():
    found = findings_for(normalise_manifest(manifest_body()), HUBSPOT)
    assert [f["code"] for f in advisory(found)] == ["key_not_supported"]


def test_findings_are_ordered_most_severe_first():
    body = manifest_body()
    body["properties"] = [
        dict(body["properties"][0]),
        {"name": "loose", "label": "Loose", "type": "string", "length": 4},
        {"name": "weird", "label": "Weird", "type": "geo", "group_name": GROUP},
    ]
    found = findings_for(normalise_manifest(body), HUBSPOT)
    severities = [f["severity"] for f in found]
    assert severities == sorted(severities, key=FINDING_SEVERITIES.index)


def test_validate_and_findings_for_agree():
    record = normalise_manifest(manifest_body())
    assert sorted(f["code"] for f in validate(record, HUBSPOT)) == sorted(
        f["code"] for f in findings_for(record, HUBSPOT)
    )


# -- the key limits --------------------------------------------------------- #


def test_a_clean_key_reports_its_width():
    report = check_key(normalise_manifest(manifest_body()))
    assert report["columns"] == ["engagement_id"]
    assert report["bytes"] == 64
    assert report["max_bytes"] == 900
    assert report["max_columns"] == 16


def test_a_manifest_with_no_key_has_no_key_report():
    body = manifest_body()
    del body["sync_key"]
    assert check_key(normalise_manifest(body)) == {}


def test_a_key_over_900_bytes_is_refused():
    with pytest.raises(KeyConstraintError) as excinfo:
        check_key(normalise_manifest(wide_key_manifest()))
    assert "900 bytes per key" in str(excinfo.value)
    assert "1000 bytes" in str(excinfo.value)


def test_a_key_over_16_columns_is_refused():
    properties = [
        {"name": f"col_{index}", "label": f"Col {index}", "type": "string",
         "group_name": GROUP, "length": 4}
        for index in range(17)
    ]
    body = manifest_body(
        properties=properties,
        sync_key={"columns": [{"name": f"col_{index}", "length": 4} for index in range(17)]},
    )
    with pytest.raises(KeyConstraintError) as excinfo:
        check_key(normalise_manifest(body))
    assert "16 columns per key" in str(excinfo.value)


def test_a_key_naming_an_undeclared_property_is_refused():
    body = manifest_body(sync_key={"columns": [{"name": "nope", "length": 4}]})
    with pytest.raises(KeyConstraintError) as excinfo:
        check_key(normalise_manifest(body))
    assert "does not declare" in str(excinfo.value)


def test_a_string_key_column_with_no_declared_length_is_refused():
    body = manifest_body(
        properties=[{"name": "engagement_id", "label": "Engagement id", "type": "string",
                     "group_name": GROUP}],
        sync_key={"columns": [{"name": "engagement_id"}]},
    )
    with pytest.raises(KeyConstraintError) as excinfo:
        check_key(normalise_manifest(body))
    assert "must declare a length" in str(excinfo.value)
    assert "engagement_id" in str(excinfo.value)


def test_a_key_column_length_may_come_from_the_property():
    body = manifest_body(sync_key={"columns": [{"name": "engagement_id"}]})
    record = normalise_manifest(body)
    assert key_bytes(record) == 64
    assert check_key(record)["bytes"] == 64


def test_key_bytes_measures_a_numbered_key_as_eight_each():
    properties = [
        {"name": "a", "label": "A", "type": "number", "group_name": GROUP},
        {"name": "b", "label": "B", "type": "bool", "group_name": GROUP},
        {"name": "c", "label": "C", "type": "datetime", "group_name": GROUP},
        {"name": "d", "label": "D", "type": "enumeration", "group_name": GROUP,
         "options": ["x"]},
    ]
    body = manifest_body(properties=properties,
                         sync_key={"columns": [{"name": n} for n in ("a", "b", "c", "d")]})
    assert key_bytes(normalise_manifest(body)) == 8 + 1 + 8 + 4


def test_key_bytes_of_no_key_is_zero():
    body = manifest_body()
    del body["sync_key"]
    assert key_bytes(normalise_manifest(body)) == 0


# --------------------------------------------------------------------------- #
# The request bodies
# --------------------------------------------------------------------------- #


def test_a_hubspot_property_body_carries_the_five_required_fields():
    record = normalise_manifest(manifest_body())
    body = property_request(record["properties"][0], HUBSPOT)
    assert set(HUBSPOT.required_property_fields) <= set(body)
    assert body["name"] == "engagement_id"
    assert body["label"] == "Engagement id"
    assert body["groupName"] == GROUP
    assert body["type"] == "string"
    assert body["fieldType"] == "text"


def test_a_hubspot_property_body_carries_a_length_and_a_required_flag():
    record = normalise_manifest(manifest_body())
    body = property_request(record["properties"][0], HUBSPOT)
    assert body["MaxLength"] == 64
    assert body["isRequired"] is True
    assert body["description"]


def test_a_hubspot_enumeration_body_carries_its_option_set():
    record = normalise_manifest(manifest_body())
    body = property_request(record["properties"][2], HUBSPOT)
    assert body["type"] == "enumeration"
    assert body["fieldType"] == "select"
    assert body["options"] == [
        {"label": "Viewed", "value": "viewed"},
        {"label": "Downloaded", "value": "downloaded"},
    ]


def test_a_hubspot_number_body_is_a_number():
    record = normalise_manifest(manifest_body())
    body = property_request(record["properties"][3], HUBSPOT)
    assert body["type"] == "number"
    assert body["fieldType"] == "number"


def test_a_dataverse_property_body_uses_the_attribute_metadata_names():
    record = normalise_manifest(manifest_body())
    body = property_request(record["properties"][0], DATAVERSE)
    assert body["schemaName"] == "engagement_id"
    assert body["AttributeDisplayName"] == "Engagement id"
    assert body["AttributeType"] == "StringAttributeMetadata"
    assert body["AttributeFormat"] == "String"
    assert "MaxLength" not in body or body["MaxLength"] == 64


def test_a_dataverse_number_body_is_a_decimal_attribute():
    record = normalise_manifest(manifest_body())
    body = property_request(record["properties"][3], DATAVERSE)
    assert body["AttributeType"] == "DecimalAttributeMetadata"


def test_a_dataverse_enumeration_body_is_a_picklist_with_an_option_set():
    record = normalise_manifest(manifest_body())
    body = property_request(record["properties"][2], DATAVERSE)
    assert body["AttributeType"] == "PicklistAttributeMetadata"
    assert body["OptionSet"] == [
        {"label": "Viewed", "value": "viewed"},
        {"label": "Downloaded", "value": "downloaded"},
    ]


def test_a_dataverse_body_carries_the_group_under_its_own_name():
    record = normalise_manifest(manifest_body())
    body = property_request(record["properties"][0], DATAVERSE)
    assert body["AttributeGroupName"] == GROUP
    assert "groupName" not in body


def test_an_object_body_names_the_object_and_carries_no_vendor_only_key():
    record = normalise_manifest(manifest_body())
    body = object_request(record, HUBSPOT)
    assert body["name"] == "dsr_engagement"
    assert body["label"] == "Sales-room engagement"
    assert body["_object_id_style"] == "numeric"


def test_a_dataverse_object_body_has_no_label_to_put_it_in():
    record = normalise_manifest(manifest_body())
    body = object_request(record, DATAVERSE)
    assert "label" not in body
    assert body["name"] == "dsr_engagement"
    assert body["_object_id_style"] == "schema_name"


def test_the_key_body_is_an_entity_key_metadata_with_its_columns_set():
    record = normalise_manifest(manifest_body())
    body = key_metadata(record, DATAVERSE)
    assert body["EntityTypeName"] == "dsr_engagement"
    assert body["KeyAttributes"] == ["engagement_id"]
    assert body["AlternateKey"] == "EngagementId"


def test_a_multi_column_key_body_names_each_column():
    body = manifest_body(
        sync_key={"columns": [{"name": "engagement_id", "length": 8}, {"name": "room_name", "length": 8}]}
    )
    assert key_metadata(normalise_manifest(body), DATAVERSE)["KeyAttributes"] == [
        "engagement_id", "room_name",
    ]


# --------------------------------------------------------------------------- #
# Connections
# --------------------------------------------------------------------------- #


def test_a_connection_is_registered_with_its_vendor_and_environment(engine):
    row = engine.register_connection(
        {"name": "Northwind", "vendor": "hubspot", "environment": "sandbox"},
        actor="dana", source=SOURCE,
    )
    assert row["vendor"] == "hubspot"
    assert row["environment"] == "sandbox"
    assert row["supported"] is True
    assert row["unsupported_reason"] is None


def test_a_connection_needs_a_name(engine):
    with pytest.raises(ProvisioningError, match="name is required"):
        engine.register_connection({"vendor": "hubspot"}, source=SOURCE)


def test_a_connection_needs_a_vendor(engine):
    with pytest.raises(ProvisioningError, match="vendor is required"):
        engine.register_connection({"name": "x"}, source=SOURCE)


def test_an_unknown_environment_is_refused(engine):
    with pytest.raises(ProvisioningError, match="environment must be one of"):
        engine.register_connection(
            {"name": "x", "vendor": "hubspot", "environment": "staging"}, source=SOURCE
        )


def test_a_simulated_index_outcome_must_be_one_of_two(engine):
    with pytest.raises(ProvisioningError, match="key_index"):
        engine.register_connection(
            {"name": "x", "vendor": "dataverse", "simulate": {"key_index": "explode"}}, source=SOURCE
        )


def test_an_unresearched_vendor_is_registrable_and_reported(engine):
    """The account is recordable; the refusal comes from the install a human runs."""
    row = engine.register_connection(
        {"name": "Fabrikam", "vendor": "salesforce"}, actor="dana", source=SOURCE
    )
    assert row["supported"] is False
    assert "could not be sourced" in row["unsupported_reason"]


def test_a_connection_carries_its_room_binding_from_the_envelope(engine):
    row = engine.register_connection(
        {"name": "x", "vendor": "hubspot", "room_id": "room_9"}, source=SOURCE
    )
    assert row["room_id"] == "room_9"
    assert engine.connection(row["id"])["room_id"] == "room_9"


def test_connections_can_be_listed_by_room(engine):
    engine.register_connection({"name": "a", "vendor": "hubspot", "room_id": "r1"}, source=SOURCE)
    engine.register_connection({"name": "b", "vendor": "dataverse", "room_id": "r2"}, source=SOURCE)
    assert [c["name"] for c in engine.connections(room_id="r1")] == ["a"]


def test_connections_can_be_filtered_by_vendor(engine):
    engine.register_connection({"name": "a", "vendor": "hubspot"}, source=SOURCE)
    engine.register_connection({"name": "b", "vendor": "dataverse"}, source=SOURCE)
    assert [c["name"] for c in engine.connections(vendor="dataverse")] == ["b"]


def test_an_unknown_connection_is_a_core_not_found(engine):
    with pytest.raises(RecordNotFound):
        engine.connection("crm_connection_missing")


# --------------------------------------------------------------------------- #
# Registering a manifest version
# --------------------------------------------------------------------------- #


def test_registering_a_manifest_creates_a_record(engine):
    row = engine.register_manifest(manifest_body(), actor="dana", source=SOURCE)
    assert row["outcome"] == "created"
    assert row["manifest_id"] == "dsr_engagement"
    assert row["version"] == "1.0.0"


def test_registering_the_same_version_unchanged_is_accepted_not_refused(engine):
    engine.register_manifest(manifest_body(), source=SOURCE)
    again = engine.register_manifest(manifest_body(), source=SOURCE)
    assert again["outcome"] == "unchanged"


def test_registering_a_different_body_for_a_published_version_is_refused(engine):
    """An install must be able to say later which body it installed."""
    engine.register_manifest(manifest_body(), source=SOURCE)
    body = manifest_body()
    body["properties"] = body["properties"] + [
        {"name": "extra", "label": "Extra", "type": "string", "group_name": GROUP, "length": 8}
    ]
    with pytest.raises(DuplicateManifestVersion) as excinfo:
        engine.register_manifest(body, source=SOURCE)
    assert excinfo.value.status == 409
    assert "different body" in str(excinfo.value)


def test_a_new_version_of_the_same_manifest_is_a_new_record(engine):
    engine.register_manifest(manifest_body(), source=SOURCE)
    second = engine.register_manifest(manifest_body(version="1.1.0"), source=SOURCE)
    assert second["outcome"] == "created"
    assert len(engine.manifests(manifest_id="dsr_engagement")) == 2


def test_manifests_can_be_listed_by_id(engine):
    engine.register_manifest(manifest_body(), source=SOURCE)
    engine.register_manifest(wide_key_manifest(), source=SOURCE)
    assert [m["manifest_id"] for m in engine.manifests(manifest_id="dsr_engagement")] == [
        "dsr_engagement"
    ]


def test_reading_a_manifest_defaults_to_the_highest_declared_version(engine):
    """Numerically, not lexicographically: "1.9.0" must not beat "1.10.0"."""
    engine.register_manifest(manifest_body(version="1.9.0"), source=SOURCE)
    engine.register_manifest(manifest_body(version="1.10.0"), source=SOURCE)
    assert engine.manifest("dsr_engagement")["version"] == "1.10.0"


def test_a_date_versioned_manifest_still_installs_its_newest(engine):
    engine.register_manifest(manifest_body(version="2026-09-01"), source=SOURCE)
    engine.register_manifest(manifest_body(version="2026-09-27"), source=SOURCE)
    assert engine.manifest("dsr_engagement")["version"] == "2026-09-27"


def test_a_manifest_version_can_be_named_explicitly(engine):
    engine.register_manifest(manifest_body(version="1.0.0"), source=SOURCE)
    engine.register_manifest(manifest_body(version="2.0.0"), source=SOURCE)
    assert engine.manifest("dsr_engagement", "1.0.0")["version"] == "1.0.0"


def test_an_unknown_manifest_is_a_core_not_found(engine):
    with pytest.raises(RecordNotFound):
        engine.manifest("nope")


def test_an_unknown_manifest_version_is_a_core_not_found(engine, manifest):
    with pytest.raises(RecordNotFound):
        engine.manifest("dsr_engagement", "9.9.9")


# -- the manifest report ----------------------------------------------------- #


def test_the_report_covers_every_vendor_the_research_names_or_gaps(engine, manifest):
    report = engine.manifest_report("dsr_engagement")
    assert set(report["per_vendor"]) == {*VENDORS, *UNSUPPORTED_VENDORS}
    assert report["installable"] is True


def test_the_report_marks_an_unresearched_vendor_uninstallable_with_the_gap(engine, manifest):
    entry = engine.manifest_report("dsr_engagement")["per_vendor"]["salesforce"]
    assert entry["installable"] is False
    assert "could not be sourced" in entry["unsupported_reason"]


def test_the_report_lists_the_properties_a_vendor_would_skip(engine):
    body = manifest_body()
    body["properties"] = body["properties"] + [
        {"name": "loose", "label": "Loose", "type": "string", "length": 20}
    ]
    engine.register_manifest(body, source=SOURCE)
    report = engine.manifest_report("dsr_engagement")
    assert report["per_vendor"]["hubspot"]["skipped_properties"] == ["loose"]
    assert report["per_vendor"]["dataverse"]["skipped_properties"] == []


def test_the_report_fails_the_sync_key_check_without_raising(engine):
    engine.register_manifest(wide_key_manifest(), source=SOURCE)
    report = engine.manifest_report("dsr_engagement_scoped")
    assert report["sync_key"]["ok"] is False
    assert "900 bytes per key" in report["sync_key"]["error"]
    assert report["installable"] is False


def test_the_report_is_served_per_manifest_id_and_version(http, http_engine):
    http.post(f"{PREFIX}/manifests", json=manifest_body())
    body = http.get(f"{PREFIX}/manifests/dsr_engagement", params={"version": "1.0.0"}).json()
    assert body["manifest"]["version"] == "1.0.0"
    assert body["sync_key"]["ok"] is True


# --------------------------------------------------------------------------- #
# The diff: create only what is missing
# --------------------------------------------------------------------------- #


def test_a_fresh_connection_diffs_to_everything_missing(engine, connection, manifest):
    plan = engine.plan(connection["id"], "dsr_engagement", "1.0.0")
    assert plan["object"]["action"] == "create"
    assert plan["counts"]["create"] == 4
    assert plan["counts"]["unchanged"] == 0
    assert plan["counts"]["left_in_place"] == 0


def test_a_plan_reads_the_live_schema_first(engine, connection, manifest):
    """Step two of the researched flow, as a request the run record shows."""
    plan = engine.plan(connection["id"], "dsr_engagement", "1.0.0")
    assert plan["service_document"]["vendor"] == "hubspot"
    assert plan["service_document"]["reachable"] is True


def test_an_installed_property_diffs_to_unchanged(engine, installed, connection):
    plan = engine.plan(connection["id"], "dsr_engagement", "1.0.0")
    assert plan["object"]["action"] == "unchanged"
    assert plan["counts"]["unchanged"] == 4
    assert plan["counts"]["create"] == 0


def test_an_unchanged_property_builds_no_request(engine, installed, connection):
    plan = engine.plan(connection["id"], "dsr_engagement", "1.0.0")
    assert all(row["request"] is None for row in plan["properties"])


def test_a_property_that_exists_and_differs_is_a_conflict_and_is_not_written(
    engine, installed, connection
):
    """The researched rule: never destructively rename or change an existing field."""
    engine.store.update(
        engine.crm.find_property(
            engine.connection(connection["id"]), "dsr_engagement", "action"
        )["id"],
        {"label": "Engagement action"},
        source=SOURCE,
    )
    result = engine.install(connection["id"], "dsr_engagement", "1.0.0", source=SOURCE)
    assert result["created"] == 0
    assert [row["property"] for row in result["run"]["conflicts"]] == ["action"]
    assert result["run"]["counts"]["conflicts"] == 1
    # And the label in the CRM is still the one a person typed.
    assert engine.crm.find_property(
        engine.connection(connection["id"]), "dsr_engagement", "action"
    )["label"] == "Engagement action"


def test_a_conflict_names_what_the_crm_actually_has(engine, installed, connection):
    engine.store.update(
        engine.crm.find_property(
            engine.connection(connection["id"]), "dsr_engagement", "room_name"
        )["id"],
        {"type": "number", "field_type": "number"},
        source=SOURCE,
    )
    plan = engine.plan(connection["id"], "dsr_engagement", "1.0.0")
    conflict = next(row for row in plan["properties"] if row["action"] == "conflict")
    assert conflict["name"] == "room_name"
    assert "number" in conflict["reason"]
    assert "never changes an existing property" in conflict["reason"]


def test_a_manifest_that_stops_declaring_a_field_leaves_it_in_place(
    engine, installed, connection
):
    """The dangerous direction of the same rule: a removed field is not dropped."""
    body = manifest_body(version="2.0.0")
    body["properties"] = [p for p in body["properties"] if p["name"] != "room_name"]
    engine.register_manifest(body, source=SOURCE)

    result = engine.install(connection["id"], "dsr_engagement", "2.0.0", source=SOURCE)
    assert result["created"] == 0
    assert [row["name"] for row in result["run"]["left_in_place"]] == ["room_name"]
    assert engine.crm.find_property(
        engine.connection(connection["id"]), "dsr_engagement", "room_name"
    ) is not None


def test_a_rename_creates_the_new_name_and_keeps_the_old_column(engine, installed, connection):
    body = manifest_body(version="2.0.0")
    body["properties"] = [
        {**p, "name": "room_label", "label": "Room label"} if p["name"] == "room_name" else p
        for p in body["properties"]
    ]
    engine.register_manifest(body, source=SOURCE)

    result = engine.install(connection["id"], "dsr_engagement", "2.0.0", source=SOURCE)
    assert result["created"] == 1
    assert [row["name"] for row in result["run"]["left_in_place"]] == ["room_name"]
    remote = engine.crm
    assert remote.find_property(engine.connection(connection["id"]), "dsr_engagement", "room_label")
    assert remote.find_property(engine.connection(connection["id"]), "dsr_engagement", "room_name")


def test_a_property_the_vendor_cannot_map_is_skipped_and_the_object_is_marked_incomplete(
    engine, connection, manifest
):
    body = manifest_body(version="1.0.1")
    body["properties"] = body["properties"] + [
        {"name": "loose", "label": "Loose", "type": "string", "length": 20}
    ]
    engine.register_manifest(body, source=SOURCE)
    result = engine.install(connection["id"], "dsr_engagement", "1.0.1", source=SOURCE)

    assert result["complete"] is False
    assert [row["property"] for row in result["run"]["skipped"]] == ["loose"]
    assert result["run"]["counts"]["skipped"] == 1
    assert result["object"]["complete"] is False
    assert result["object"]["skipped_properties"] == ["loose"]


def test_the_plan_reports_completeness(engine, connection, manifest):
    assert engine.plan(connection["id"], "dsr_engagement", "1.0.0")["complete"] is True


def test_a_plan_carries_no_blocking_findings_for_a_clean_manifest(engine, connection, manifest):
    assert engine.plan(connection["id"], "dsr_engagement", "1.0.0")["blocking_findings"] == []


def test_a_plan_can_be_taken_against_another_vendor(engine, connection, manifest):
    plan = engine.plan(connection["id"], "dsr_engagement", "1.0.0", vendor="dataverse")
    assert plan["vendor"] == "dataverse"
    assert plan["key"]["action"] == "create"


def test_a_plan_names_the_sync_key_columns(engine, dataverse_connection, manifest):
    plan = engine.plan(dataverse_connection["id"], "dsr_engagement", "1.0.0")
    assert plan["key"]["columns"] == ["engagement_id"]
    assert plan["key"]["request"]["KeyAttributes"] == ["engagement_id"]


def test_a_sync_key_is_unsupported_on_a_vendor_with_no_sourced_call(engine, connection, manifest):
    plan = engine.plan(connection["id"], "dsr_engagement", "1.0.0")
    assert plan["key"]["action"] == "unsupported"
    assert "reported, not sent" in plan["key"]["reason"]


def test_a_manifest_with_no_sync_key_plans_no_key(engine, connection, manifest):
    body = manifest_body(version="3.0.0")
    del body["sync_key"]
    engine.register_manifest(body, source=SOURCE)
    plan = engine.plan(connection["id"], "dsr_engagement", "3.0.0")
    assert plan["key"]["action"] == "absent"


def test_a_narrower_crm_column_is_advisory_rather_than_a_conflict(
    engine, installed, connection
):
    """No code path can widen a column either, so calling it a conflict would be noise."""
    engine.store.update(
        engine.crm.find_property(
            engine.connection(connection["id"]), "dsr_engagement", "room_name"
        )["id"],
        {"length": 10},
        source=SOURCE,
    )
    plan = engine.plan(connection["id"], "dsr_engagement", "1.0.0")
    assert plan["counts"]["conflict"] == 0
    assert [f["code"] for f in plan["advisories"]] == ["narrower_in_crm"]


def test_a_wider_crm_column_is_not_reported_at_all(engine, installed, connection):
    engine.store.update(
        engine.crm.find_property(
            engine.connection(connection["id"]), "dsr_engagement", "room_name"
        )["id"],
        {"length": 400},
        source=SOURCE,
    )
    plan = engine.plan(connection["id"], "dsr_engagement", "1.0.0")
    assert plan["advisories"] == []
    assert plan["counts"]["unchanged"] == 4


# --------------------------------------------------------------------------- #
# The installer
# --------------------------------------------------------------------------- #


def test_a_first_install_creates_the_object_and_every_missing_property(
    engine, connection, manifest
):
    result = engine.install(connection["id"], "dsr_engagement", "1.0.0", source=SOURCE)
    assert result["outcome"] == "created"
    assert result["created"] == 5  # one object and four properties
    assert result["complete"] is True
    assert [p["name"] for p in result["properties"]] == [
        "engagement_id", "room_name", "action", "seconds_on_page",
    ]


def test_an_install_records_the_mapping_the_data_flow_asks_for(engine, connection, manifest):
    result = engine.install(connection["id"], "dsr_engagement", "1.0.0", source=SOURCE)
    assert result["mapping"]["room_object_id"] == "dsr.engagement"
    assert result["mapping"]["crm_object_id"]
    assert result["mapping"]["crm_object_name"] == "dsr_engagement"
    assert result["mapping"]["vendor"] == "hubspot"


def test_the_mapping_is_served_as_an_object_record(engine, connection, manifest):
    result = engine.install(connection["id"], "dsr_engagement", "1.0.0", source=SOURCE)
    stored = engine.objects(connection_id=connection["id"])[0]
    assert stored["id"] == result["object"]["id"]
    assert stored["room_object_id"] == "dsr.engagement"
    assert str(stored["crm_object_id"]) == result["mapping"]["crm_object_id"]


def test_an_install_sends_the_researched_requests(engine, connection, manifest):
    run = engine.install(connection["id"], "dsr_engagement", "1.0.0", source=SOURCE)["run"]
    posts = [row for row in run["requests"] if row["method"] == "POST"]
    assert posts[0]["path"] == "/crm-object-schemas/2026-09/schemas"
    assert all(row["path"].startswith("/crm/properties/2026-09/") for row in posts[1:])


def test_an_install_records_the_schema_reads_it_made(engine, connection, manifest):
    run = engine.install(connection["id"], "dsr_engagement", "1.0.0", source=SOURCE)["run"]
    gets = [row["path"] for row in run["requests"] if row["method"] == "GET"]
    assert gets == ["/", "/crm-object-schemas/2026-09/schemas", "/crm/v3/schemas/..."]


def test_a_dataverse_install_sends_the_metadata_reads(engine, dataverse_connection, manifest):
    run = engine.install(
        dataverse_connection["id"], "dsr_engagement", "1.0.0", source=SOURCE
    )["run"]
    gets = [row["path"] for row in run["requests"] if row["method"] == "GET"]
    assert gets == ["/api/data/v9.2/$metadata", "/api/data/v9.2/EntityDefinitions"]


def test_a_dataverse_property_request_carries_the_object_name_in_its_path(
    engine, dataverse_connection, manifest
):
    run = engine.install(
        dataverse_connection["id"], "dsr_engagement", "1.0.0", source=SOURCE
    )["run"]
    posts = [row for row in run["requests"] if "Attributes" in str(row.get("path"))]
    assert posts
    assert all("dsr_engagement" in row["path"] for row in posts)


def test_a_dataverse_install_requests_the_key_with_the_researched_call(
    engine, dataverse_connection, manifest
):
    run = engine.install(
        dataverse_connection["id"], "dsr_engagement", "1.0.0", source=SOURCE
    )["run"]
    key_requests = [row for row in run["requests"] if row.get("path") == "/api/data/v9.2/CreateEntityKey"]
    assert len(key_requests) == 1
    assert key_requests[0]["body"]["KeyAttributes"] == ["engagement_id"]


def test_a_hubspot_install_sends_no_key_request(engine, connection, manifest):
    run = engine.install(connection["id"], "dsr_engagement", "1.0.0", source=SOURCE)["run"]
    assert run["key"]["action"] == "unsupported"
    assert all("EntityKey" not in str(row.get("path")) for row in run["requests"])


def test_an_install_is_idempotent_and_writes_nothing_the_second_time(
    store, engine, connection, manifest
):
    """Step four of the researched flow, as behaviour rather than as a comment."""
    engine.install(connection["id"], "dsr_engagement", "1.0.0", source=SOURCE)
    before = store.audit(collection="crm_remote_property")

    again = engine.install(connection["id"], "dsr_engagement", "1.0.0", source=SOURCE)
    after = store.audit(collection="crm_remote_property")

    assert again["outcome"] == "unchanged"
    assert again["created"] == 0
    assert len(after) == len(before)


def test_a_third_install_is_still_a_no_op(store, engine, connection, manifest):
    for _ in range(3):
        engine.install(connection["id"], "dsr_engagement", "1.0.0", source=SOURCE)
    assert len(store.find("crm_remote_object", {"connection_id": connection["id"]})) == 1
    assert len(store.find("crm_remote_property", {"connection_id": connection["id"]})) == 4


def test_an_idempotent_run_still_records_that_it_happened(store, engine, connection, manifest):
    engine.install(connection["id"], "dsr_engagement", "1.0.0", source=SOURCE)
    engine.install(connection["id"], "dsr_engagement", "1.0.0", source=SOURCE)
    runs = store.find("crm_installation", {"connection_id": connection["id"]})
    assert len(runs) == 2
    assert {run["data"]["outcome"] for run in runs} == {"created", "unchanged"}


def test_the_crm_object_id_is_stored_as_a_string_whatever_the_vendor_spells_it(
    engine, connection, dataverse_connection, manifest
):
    """HubSpot's object type is a number and Dataverse's is a schema name.

    The remote record keeps the vendor's own type, because that is what came back.
    Every record this workflow owns holds the string, so one comparison resolves a
    property to its object on either vendor - and a client is never handed a JSON
    value whose type depends on which vendor provisioned it.
    """
    engine.install(connection["id"], "dsr_engagement", "1.0.0", source=SOURCE)
    engine.install(dataverse_connection["id"], "dsr_engagement", "1.0.0", source=SOURCE)

    for vendor in ("hubspot", "dataverse"):
        connection_row = next(c for c in engine.connections() if c["vendor"] == vendor)
        obj = engine.objects(connection_id=connection_row["id"])[0]
        assert isinstance(obj["crm_object_id"], str)
        assert len(engine.properties(obj["id"])) == 4


def test_the_two_vendors_produce_differently_shaped_ids_and_both_work(
    engine, connection, dataverse_connection, manifest
):
    engine.install(connection["id"], "dsr_engagement", "1.0.0", source=SOURCE)
    engine.install(dataverse_connection["id"], "dsr_engagement", "1.0.0", source=SOURCE)
    hubspot_object = next(
        obj
        for obj in engine.objects()
        if engine.connection(obj["connection_id"])["vendor"] == "hubspot"
    )
    dataverse_object = next(
        obj
        for obj in engine.objects()
        if engine.connection(obj["connection_id"])["vendor"] == "dataverse"
    )
    assert hubspot_object["crm_object_id"].isdigit()
    assert dataverse_object["crm_object_id"] == "dsr_engagement"


def test_a_first_install_writes_the_object_record_once(engine, store, connection, manifest):
    """No tidy-up write: the id is normalised before the record is created."""
    engine.install(connection["id"], "dsr_engagement", "1.0.0", source=SOURCE)
    actions = [entry["action"] for entry in store.audit(collection="crm_object")]
    assert actions == ["insert"]


def test_a_new_version_creates_only_the_field_it_adds(engine, connection, manifest):
    engine.install(connection["id"], "dsr_engagement", "1.0.0", source=SOURCE)
    body = manifest_body(version="1.1.0")
    body["properties"] = body["properties"] + [
        {"name": "campaign_touch", "label": "Campaign touch", "type": "string",
         "group_name": GROUP, "length": 80},
    ]
    engine.register_manifest(body, source=SOURCE)

    result = engine.install(connection["id"], "dsr_engagement", "1.1.0", source=SOURCE)
    assert result["created"] == 1
    assert [p["name"] for p in result["properties"]] == ["campaign_touch"]
    # The object itself already exists, so this run creates only the new field.
    assert result["plan"]["object"]["action"] == "unchanged"
    # And the object record still names the version that created it, because that
    # is the version an audit has to be able to point at.
    assert result["object"]["manifest_version"] == "1.0.0"


def test_a_first_install_reports_one_object_and_four_properties(engine, connection, manifest):
    """The counters say what the plan would do and what the run did."""
    result = engine.install(connection["id"], "dsr_engagement", "1.0.0", source=SOURCE)
    assert result["created"] == result["applied"] == 5


def test_a_manifest_the_key_limit_rejects_is_refused_and_writes_nothing(
    store, engine, connection
):
    engine.register_manifest(wide_key_manifest(), source=SOURCE)
    before = len(store.audit(limit=1000))
    with pytest.raises(KeyConstraintError):
        engine.install(connection["id"], "dsr_engagement_scoped", "0.1.0", source=SOURCE)
    assert len(store.audit(limit=1000)) == before
    assert engine.objects(connection_id=connection["id"]) == []


def test_a_manifest_with_a_blocking_finding_is_refused_and_writes_nothing(
    store, engine, connection
):
    body = manifest_body()
    body["properties"] = body["properties"] + [dict(body["properties"][0])]
    engine.register_manifest(body, source=SOURCE)
    before = len(store.audit(limit=1000))
    with pytest.raises(ManifestError, match="cannot be installed as described"):
        engine.install(connection["id"], "dsr_engagement", "1.0.0", source=SOURCE)
    assert len(store.audit(limit=1000)) == before


def test_an_unresearched_vendor_is_refused_at_the_install(store, engine):
    connection = engine.register_connection(
        {"name": "Fabrikam", "vendor": "salesforce"}, source=SOURCE
    )
    engine.register_manifest(manifest_body(), source=SOURCE)
    before = len(store.audit(limit=1000))
    with pytest.raises(UnsupportedVendor):
        engine.install(connection["id"], "dsr_engagement", "1.0.0", source=SOURCE)
    assert len(store.audit(limit=1000)) == before


def test_an_install_of_an_unknown_connection_is_a_core_not_found(engine, manifest):
    with pytest.raises(RecordNotFound):
        engine.install("crm_connection_nope", "dsr_engagement", "1.0.0", source=SOURCE)


# -- the dry run ------------------------------------------------------------- #


def test_a_dry_run_creates_nothing(store, engine, connection, manifest):
    result = engine.install(
        connection["id"], "dsr_engagement", "1.0.0", dry_run=True, source=SOURCE
    )
    assert result["outcome"] == "dry_run"
    # It says both: what the plan would do, and what it did.
    assert result["created"] == 5
    assert result["applied"] == 0
    assert engine.objects(connection_id=connection["id"]) == []
    assert store.find("crm_remote_object", {"connection_id": connection["id"]}) == []
    assert store.find("crm_property", {}) == []


def test_a_dry_run_records_that_it_was_a_preview(engine, connection, manifest):
    result = engine.install(
        connection["id"], "dsr_engagement", "1.0.0", dry_run=True, source=SOURCE
    )
    assert result["run"]["dry_run"] is True
    assert result["run"]["counts"]["created"] == 5
    assert result["run"]["counts"]["applied"] == 0


def test_a_dry_run_still_reports_the_requests_it_would_have_sent(engine, connection, manifest):
    """A preview that reported only the object would be telling a third of the truth."""
    run = engine.install(
        connection["id"], "dsr_engagement", "1.0.0", dry_run=True, source=SOURCE
    )["run"]
    posts = [row for row in run["requests"] if row["method"] == "POST"]
    assert len(posts) == 5
    assert posts[0]["path"] == "/crm-object-schemas/2026-09/schemas"
    assert all(row["path"].startswith("/crm/properties/2026-09/") for row in posts[1:])


def test_a_dry_run_creates_no_key(engine, dataverse_connection, manifest):
    run = engine.install(
        dataverse_connection["id"], "dsr_engagement", "1.0.0", dry_run=True, source=SOURCE
    )["run"]
    assert any("CreateEntityKey" in str(row.get("path")) for row in run["requests"])
    assert engine.keys(connection_id=dataverse_connection["id"]) == []
    assert run["counts"]["applied"] == 0


def test_a_dry_run_does_not_touch_an_already_installed_object(engine, installed, connection):
    result = engine.install(
        connection["id"], "dsr_engagement", "1.0.0", dry_run=True, source=SOURCE
    )
    assert result["run"]["counts"]["unchanged"] == 4
    assert result["created"] == 0
    assert result["applied"] == 0


# --------------------------------------------------------------------------- #
# The sync key and its background index
# --------------------------------------------------------------------------- #


def test_a_requested_key_lands_pending_with_an_async_job(engine, dataverse_connection, manifest):
    result = engine.install(
        dataverse_connection["id"], "dsr_engagement", "1.0.0", source=SOURCE
    )
    key = result["key"]
    assert key["status"] == "Pending"
    assert key["async_job_id"]
    assert key["columns"] == ["engagement_id"]


def test_the_background_build_is_not_blocked_on_by_the_install(
    engine, dataverse_connection, manifest
):
    """The object is usable before the key is, which is the point of a background build."""
    result = engine.install(
        dataverse_connection["id"], "dsr_engagement", "1.0.0", source=SOURCE
    )
    assert result["object"] is not None
    assert result["key"]["status"] == "Pending"


def test_a_key_reaches_active_after_the_researched_progression(
    engine, dataverse_connection, manifest
):
    result = engine.install(
        dataverse_connection["id"], "dsr_engagement", "1.0.0", source=SOURCE
    )
    key_id = result["key"]["id"]
    seen = [engine.poll_key(key_id, source=SOURCE)["status"] for _ in range(DEFAULT_INDEX_POLLS)]
    assert seen == ["In Progress", "Active"]


def test_polling_an_active_key_leaves_it_active(engine, dataverse_connection, manifest):
    result = engine.install(
        dataverse_connection["id"], "dsr_engagement", "1.0.0", source=SOURCE
    )
    key_id = result["key"]["id"]
    for _ in range(DEFAULT_INDEX_POLLS):
        engine.poll_key(key_id, source=SOURCE)
    assert engine.poll_key(key_id, source=SOURCE)["status"] == "Active"


def test_an_active_key_records_when_it_was_activated(engine, dataverse_connection, manifest):
    result = engine.install(
        dataverse_connection["id"], "dsr_engagement", "1.0.0", source=SOURCE
    )
    key_id = result["key"]["id"]
    for _ in range(DEFAULT_INDEX_POLLS):
        row = engine.poll_key(key_id, source=SOURCE)
    assert row["activated_at"]


def test_a_build_that_will_not_complete_reports_failed(engine, connection, manifest):
    """The research names Failed, so the state has to be reachable."""
    failing = engine.register_connection(
        {"name": "Contoso", "vendor": "dataverse", "simulate": {"key_index": "failed"}},
        source=SOURCE,
    )
    result = engine.install(failing["id"], "dsr_engagement", "1.0.0", source=SOURCE)
    assert engine.poll_key(result["key"]["id"], source=SOURCE)["status"] == "Failed"


def test_a_failed_build_stays_failed_until_it_is_reactivated(engine, connection, manifest):
    failing = engine.register_connection(
        {"name": "Contoso", "vendor": "dataverse", "simulate": {"key_index": "failed"}},
        source=SOURCE,
    )
    result = engine.install(failing["id"], "dsr_engagement", "1.0.0", source=SOURCE)
    key_id = result["key"]["id"]
    engine.poll_key(key_id, source=SOURCE)
    assert engine.poll_key(key_id, source=SOURCE)["status"] == "Failed"


def test_reactivating_a_failed_key_rearms_it(engine, connection, manifest):
    failing = engine.register_connection(
        {"name": "Contoso", "vendor": "dataverse", "simulate": {"key_index": "failed"}},
        source=SOURCE,
    )
    result = engine.install(failing["id"], "dsr_engagement", "1.0.0", source=SOURCE)
    key_id = result["key"]["id"]
    engine.poll_key(key_id, source=SOURCE)

    repaired = engine.reactivate_key(key_id, source=SOURCE)
    assert repaired["status"] == "Pending"
    assert repaired["reactivated"] is True
    assert repaired["reactivated_count"] == 1
    assert repaired["polls"] == 0


def test_a_reactivated_key_then_reaches_active(engine, connection, manifest):
    """The repair is what makes the key usable again, so it has to actually work."""
    failing = engine.register_connection(
        {"name": "Contoso", "vendor": "dataverse", "simulate": {"key_index": "failed"}},
        source=SOURCE,
    )
    result = engine.install(failing["id"], "dsr_engagement", "1.0.0", source=SOURCE)
    key_id = result["key"]["id"]
    engine.poll_key(key_id, source=SOURCE)
    engine.reactivate_key(key_id, source=SOURCE)
    for _ in range(DEFAULT_INDEX_POLLS):
        row = engine.poll_key(key_id, source=SOURCE)
    assert row["status"] == "Active"


def test_reactivating_an_active_key_is_a_no_op(engine, dataverse_connection, manifest):
    result = engine.install(
        dataverse_connection["id"], "dsr_engagement", "1.0.0", source=SOURCE
    )
    key_id = result["key"]["id"]
    for _ in range(DEFAULT_INDEX_POLLS):
        engine.poll_key(key_id, source=SOURCE)
    repaired = engine.reactivate_key(key_id, source=SOURCE)
    assert repaired["reactivated"] is False
    assert repaired["status"] == "Active"


def test_reactivating_a_key_still_building_leaves_it_building(
    engine, dataverse_connection, manifest
):
    result = engine.install(
        dataverse_connection["id"], "dsr_engagement", "1.0.0", source=SOURCE
    )
    key_id = result["key"]["id"]
    engine.poll_key(key_id, source=SOURCE)
    assert engine.reactivate_key(key_id, source=SOURCE)["status"] == "In Progress"


def test_a_reinstall_does_not_request_a_second_key(engine, dataverse_connection, manifest):
    first = engine.install(dataverse_connection["id"], "dsr_engagement", "1.0.0", source=SOURCE)
    second = engine.install(dataverse_connection["id"], "dsr_engagement", "1.0.0", source=SOURCE)
    assert first["created"] > second["created"]
    assert len(engine.keys(connection_id=dataverse_connection["id"])) == 1


def test_a_key_whose_index_is_not_active_diffs_as_failed(engine, dataverse_connection, manifest):
    result = engine.install(
        dataverse_connection["id"], "dsr_engagement", "1.0.0", source=SOURCE
    )
    plan = engine.plan(dataverse_connection["id"], "dsr_engagement", "1.0.0")
    assert plan["key"]["action"] == "failed"
    assert result["key"]["status"] == "Pending"


def test_polling_an_unknown_key_is_a_core_not_found(engine):
    with pytest.raises(RecordNotFound):
        engine.poll_key("crm_sync_key_missing", source=SOURCE)


def test_reactivating_an_unknown_key_is_a_core_not_found(engine):
    with pytest.raises(RecordNotFound):
        engine.reactivate_key("crm_sync_key_missing", source=SOURCE)


# --------------------------------------------------------------------------- #
# Objects, properties, and a field added outside the installer
# --------------------------------------------------------------------------- #


def test_an_object_lists_its_properties(engine, installed, connection):
    obj = engine.objects(connection_id=connection["id"])[0]
    assert [p["name"] for p in engine.properties(obj["id"])] == [
        "action", "engagement_id", "room_name", "seconds_on_page",
    ]


def test_a_property_records_the_request_that_created_it(engine, connection, manifest):
    result = engine.install(connection["id"], "dsr_engagement", "1.0.0", source=SOURCE)
    created = next(p for p in result["properties"] if p["name"] == "engagement_id")
    assert created["origin"] == "installed"
    assert created["request"]["groupName"] == GROUP


def test_a_property_created_by_hand_does_not_claim_to_have_been_installed(
    engine, connection, manifest
):
    result = engine.install(connection["id"], "dsr_engagement", "1.0.0", source=SOURCE)
    added = engine.add_property(
        result["object"]["id"],
        {"name": "campaign_touch", "label": "Campaign touch", "type": "string",
         "group_name": GROUP, "length": 80},
        source=SOURCE,
    )
    assert added["origin"] == "manual"


def test_a_field_can_be_added_without_a_new_manifest_version(engine, connection, manifest):
    """The extensibility claim: adding a CRM field is a config change."""
    result = engine.install(connection["id"], "dsr_engagement", "1.0.0", source=SOURCE)
    added = engine.add_property(
        result["object"]["id"],
        {"name": "campaign_touch", "label": "Campaign touch", "type": "string",
         "group_name": GROUP, "length": 80},
        source=SOURCE,
    )
    assert added["name"] == "campaign_touch"
    assert len(engine.manifests(manifest_id="dsr_engagement")) == 1


def test_adding_a_property_that_already_exists_is_refused(engine, installed, connection):
    obj = engine.objects(connection_id=connection["id"])[0]
    with pytest.raises(PropertyConflict) as excinfo:
        engine.add_property(
            obj["id"], {"name": "room_name", "label": "Something else", "type": "string",
                        "group_name": GROUP},
            source=SOURCE,
        )
    assert excinfo.value.status == 409
    assert "never changes them" in str(excinfo.value)


def test_adding_a_property_does_not_change_an_existing_one(engine, installed, connection):
    obj = engine.objects(connection_id=connection["id"])[0]
    with pytest.raises(PropertyConflict):
        engine.add_property(
            obj["id"], {"name": "room_name", "label": "Changed", "type": "string",
                        "group_name": GROUP},
            source=SOURCE,
        )
    remote = engine.crm.find_property(engine.connection(connection["id"]), "dsr_engagement", "room_name")
    assert remote["label"] == "Room name"


def test_adding_a_property_without_a_name_is_refused(engine, installed, connection):
    obj = engine.objects(connection_id=connection["id"])[0]
    with pytest.raises(ManifestError, match="name is required"):
        engine.add_property(obj["id"], {"type": "string"}, source=SOURCE)


def test_adding_a_property_the_vendor_cannot_map_is_refused(engine, installed, connection):
    obj = engine.objects(connection_id=connection["id"])[0]
    with pytest.raises(ManifestError, match="will be skipped"):
        engine.add_property(obj["id"], {"name": "loose", "type": "string"}, source=SOURCE)


def test_adding_a_dataverse_property_needs_no_group_name(engine, dataverse_connection, manifest):
    result = engine.install(dataverse_connection["id"], "dsr_engagement", "1.0.0", source=SOURCE)
    added = engine.add_property(
        result["object"]["id"],
        {"name": "campaign_touch", "label": "Campaign touch", "type": "string", "length": 80},
        source=SOURCE,
    )
    assert added["name"] == "campaign_touch"
    assert added["request"]["schemaName"] == "campaign_touch"


def test_adding_a_property_to_an_unknown_object_is_a_core_not_found(engine):
    with pytest.raises(RecordNotFound):
        engine.add_property("crm_object_missing", {"name": "a"}, source=SOURCE)


def test_reading_properties_of_an_unknown_object_is_a_core_not_found(engine):
    with pytest.raises(RecordNotFound):
        engine.properties("crm_object_missing")


def test_reading_an_unknown_object_is_a_core_not_found(engine):
    with pytest.raises(RecordNotFound):
        engine.object("crm_object_missing")


def test_reading_an_unknown_key_is_a_core_not_found(engine):
    with pytest.raises(RecordNotFound):
        engine.key("crm_sync_key_missing")


def test_reading_an_unknown_run_is_a_core_not_found(engine):
    with pytest.raises(RecordNotFound):
        engine.run("crm_installation_missing")


def test_keys_can_be_listed_by_object(engine, dataverse_connection, manifest):
    result = engine.install(dataverse_connection["id"], "dsr_engagement", "1.0.0", source=SOURCE)
    assert len(engine.keys(object_id=result["object"]["id"])) == 1


def test_listing_keys_by_an_unknown_object_is_a_core_not_found(engine):
    with pytest.raises(RecordNotFound):
        engine.keys(object_id="crm_object_missing")


def test_runs_can_be_filtered_by_outcome(engine, connection, manifest):
    engine.install(connection["id"], "dsr_engagement", "1.0.0", source=SOURCE)
    engine.install(connection["id"], "dsr_engagement", "1.0.0", source=SOURCE)
    engine.install(connection["id"], "dsr_engagement", "1.0.0", dry_run=True, source=SOURCE)
    assert len(engine.runs(outcome="created")) == 1
    assert len(engine.runs(outcome="unchanged")) == 1
    assert len(engine.runs(outcome="dry_run")) == 1


def test_runs_can_be_filtered_by_connection(engine, connection, dataverse_connection, manifest):
    engine.install(connection["id"], "dsr_engagement", "1.0.0", source=SOURCE)
    engine.install(dataverse_connection["id"], "dsr_engagement", "1.0.0", source=SOURCE)
    assert len(engine.runs(connection_id=dataverse_connection["id"])) == 1


# --------------------------------------------------------------------------- #
# Room-scoped reads
# --------------------------------------------------------------------------- #


def test_a_room_sees_the_objects_its_connections_provisioned(engine, connection, manifest):
    engine.install(connection["id"], "dsr_engagement", "1.0.0", source=SOURCE)
    payload = engine.room_objects("room_1")
    assert payload["count"] == 1
    assert [c["name"] for c in payload["connections"]] == ["Northwind HubSpot"]
    assert len(payload["properties"]) == 1


def test_a_room_with_nothing_installed_answers_with_zeroes(engine, connection):
    payload = engine.room_objects("room_1")
    assert payload["count"] == 0
    assert payload["objects"] == []


def test_a_room_summary_counts_only_that_room(engine, connection, dataverse_connection, manifest):
    engine.install(connection["id"], "dsr_engagement", "1.0.0", source=SOURCE)
    engine.install(dataverse_connection["id"], "dsr_engagement", "1.0.0", source=SOURCE)
    first = engine.room_summary("room_1")
    second = engine.room_summary("room_2")
    assert first["objects"] == 1 and second["objects"] == 1
    assert first["properties"] == 4
    assert first["keys"] == 0 and second["keys"] == 1


def test_a_room_summary_names_its_unsupported_connections(engine):
    engine.register_connection(
        {"name": "Fabrikam", "vendor": "salesforce", "room_id": "room_3"}, source=SOURCE
    )
    assert engine.room_summary("room_3")["unsupported_connections"] == ["Fabrikam"]


def test_a_room_summary_counts_keys_that_need_a_person(engine, connection, manifest):
    failing = engine.register_connection(
        {"name": "Contoso", "vendor": "dataverse", "room_id": "room_2",
         "simulate": {"key_index": "failed"}},
        source=SOURCE,
    )
    result = engine.install(failing["id"], "dsr_engagement", "1.0.0", source=SOURCE)
    assert engine.room_summary("room_2")["needs_repair"] == 1
    engine.poll_key(result["key"]["id"], source=SOURCE)
    assert engine.room_summary("room_2")["keys_active"] == 0
    assert engine.room_summary("room_2")["needs_repair"] == 1


def test_a_room_summary_names_its_incomplete_objects(engine, connection, manifest):
    body = manifest_body(version="1.0.1")
    body["properties"] = body["properties"] + [
        {"name": "loose", "label": "Loose", "type": "string", "length": 20}
    ]
    engine.register_manifest(body, source=SOURCE)
    engine.install(connection["id"], "dsr_engagement", "1.0.1", source=SOURCE)
    assert engine.room_summary("room_1")["incomplete_objects"] == 1


def test_a_room_summary_names_its_last_installation(engine, connection, manifest):
    engine.install(connection["id"], "dsr_engagement", "1.0.0", source=SOURCE)
    summary = engine.room_summary("room_1")
    assert summary["installations"] == 1
    assert summary["last_installation"]["outcome"] == "created"


def test_an_empty_room_summary_survives(engine):
    summary = engine.room_summary("room_none")
    assert summary["connections"] == 0
    assert summary["objects"] == 0
    assert summary["last_installation"] is None


# --------------------------------------------------------------------------- #
# The vendor seam
# --------------------------------------------------------------------------- #


def test_the_gateway_answers_the_service_document_from_the_connection(engine, connection):
    document = engine.crm.service_document(connection)
    assert document["vendor"] == "hubspot"
    assert document["environment"] == "production"


def test_the_gateway_reads_only_its_own_connections_state(
    engine, connection, dataverse_connection, manifest
):
    engine.install(connection["id"], "dsr_engagement", "1.0.0", source=SOURCE)
    assert len(engine.crm.object_definitions(connection)) == 1
    assert engine.crm.object_definitions(dataverse_connection) == []


def test_the_gateway_refuses_to_create_a_second_object_of_one_name(engine, connection):
    created = engine.crm.create_object(
        connection, {"name": "twice", "label": "Twice", "_object_id_style": "numeric"},
        source=SOURCE,
    )
    again = engine.crm.create_object(
        connection, {"name": "twice", "label": "Twice", "_object_id_style": "numeric"},
        source=SOURCE,
    )
    assert created["created"] is True
    assert again["created"] is False
    assert again["crm_object_id"] == created["crm_object_id"]


def test_the_gateway_keeps_the_vendors_own_id_type(engine, connection):
    numeric = engine.crm.create_object(
        connection, {"name": "h", "_object_id_style": "numeric"}, source=SOURCE
    )
    named = engine.crm.create_object(
        connection, {"name": "d", "_object_id_style": "schema_name"}, source=SOURCE
    )
    assert isinstance(numeric["crm_object_id"], int)
    assert named["crm_object_id"] == "d"


def test_the_gateway_refuses_to_create_a_second_property_of_one_name(engine, connection):
    request = {"name": "twice", "label": "Twice", "type": "string", "fieldType": "text"}
    assert engine.crm.create_property(connection, "obj", request, source=SOURCE)["created"] is True
    assert engine.crm.create_property(connection, "obj", request, source=SOURCE)["created"] is False


def test_the_gateway_advancing_an_unknown_key_is_a_core_not_found(engine, connection):
    with pytest.raises(RecordNotFound):
        engine.crm.advance_key(connection, "key-nope", source=SOURCE)


def test_the_gateway_reads_a_key_definition_by_its_key_id(engine, dataverse_connection, manifest):
    result = engine.install(dataverse_connection["id"], "dsr_engagement", "1.0.0", source=SOURCE)
    key_id = result["key"]["key_id"]
    assert engine.crm.find_key(engine.connection(dataverse_connection["id"]), key_id) is not None
    assert engine.crm.find_key(engine.connection(dataverse_connection["id"]), "nope") is None


# --------------------------------------------------------------------------- #
# The audit trail
# --------------------------------------------------------------------------- #


def test_registering_a_connection_is_audited_to_the_route_that_served_it(store, engine):
    engine.register_connection({"name": "x", "vendor": "hubspot"}, actor="dana", source=SOURCE)
    assert store.audit(collection="crm_connection")[0]["source"] == SOURCE
    assert store.audit(collection="crm_connection")[0]["actor"] == "dana"


def test_registering_a_manifest_is_audited_to_the_route_that_served_it(store, engine):
    engine.register_manifest(manifest_body(), actor="dana", source=SOURCE)
    assert store.audit(collection="crm_manifest")[0]["source"] == SOURCE


def test_installing_is_audited_to_the_install_route(store, engine, connection, manifest):
    engine.install(connection["id"], "dsr_engagement", "1.0.0", actor="dana", source=SOURCE)
    for collection in ("crm_object", "crm_property", "crm_installation", "crm_remote_object"):
        entries = store.audit(collection=collection)
        assert entries, collection
        assert all(entry["source"] == SOURCE for entry in entries), collection


def test_poll_and_reactivate_are_audited_to_their_own_routes(store, engine, dataverse_connection, manifest):
    result = engine.install(dataverse_connection["id"], "dsr_engagement", "1.0.0", source=SOURCE)
    key_id = result["key"]["id"]
    engine.poll_key(key_id, source=f"POST {PREFIX}/keys/{{key_id}}/poll")
    engine.reactivate_key(key_id, source=f"POST {PREFIX}/keys/{{key_id}}/reactivate")
    sources = {entry["source"] for entry in store.audit(collection="crm_sync_key")}
    assert sources == {
        SOURCE,
        f"POST {PREFIX}/keys/{{key_id}}/poll",
        f"POST {PREFIX}/keys/{{key_id}}/reactivate",
    }


def test_adding_a_property_is_audited_to_the_property_route(store, engine, connection, manifest):
    result = engine.install(connection["id"], "dsr_engagement", "1.0.0", source=SOURCE)
    engine.add_property(
        result["object"]["id"],
        {"name": "campaign_touch", "label": "Campaign touch", "type": "string",
         "group_name": GROUP, "length": 80},
        source=f"POST {PREFIX}/objects/{{object_id}}/properties",
    )
    assert store.audit(collection="crm_property")[0]["source"] == (
        f"POST {PREFIX}/objects/{{object_id}}/properties"
    )


def test_the_vendor_state_is_audited_too(store, engine, connection, manifest):
    engine.install(connection["id"], "dsr_engagement", "1.0.0", source=SOURCE)
    for collection in ("crm_remote_object", "crm_remote_property"):
        assert all(entry["source"] == SOURCE for entry in store.audit(collection=collection))


def test_an_audit_row_carries_the_room_the_installation_belongs_to(store, engine, connection, manifest):
    engine.install(connection["id"], "dsr_engagement", "1.0.0", source=SOURCE)
    assert store.audit(collection="crm_object")[0]["room_id"] == "room_1"


def test_a_refused_install_writes_nothing_at_all(store, engine, connection):
    engine.register_manifest(wide_key_manifest(), source=SOURCE)
    before = len(store.audit(limit=1000))
    with pytest.raises(KeyConstraintError):
        engine.install(connection["id"], "dsr_engagement_scoped", "0.1.0", source=SOURCE)
    assert len(store.audit(limit=1000)) == before


def test_every_write_method_demands_a_source():
    """A hardcoded source is a defect; this is the guard against one coming back."""
    engine = ProvisioningEngine(RecordStore(AuditedDatabase(":memory:")))
    for name in (
        "register_connection",
        "register_manifest",
        "install",
        "add_property",
        "poll_key",
        "reactivate_key",
    ):
        parameters = inspect.signature(getattr(engine, name)).parameters
        assert "source" in parameters, name
        assert parameters["source"].default is inspect.Parameter.empty, name


# --------------------------------------------------------------------------- #
# The HTTP surface, through this feature's own router
# --------------------------------------------------------------------------- #


def test_the_vocabulary_route_serves_the_vocabulary(http):
    response = http.get(f"{PREFIX}/vocabulary")
    assert response.status_code == 200
    assert response.json()["vendors"] == ["hubspot", "dataverse"]


def test_the_inferences_route_serves_the_register(http):
    response = http.get(f"{PREFIX}/inferences")
    assert response.status_code == 200
    assert response.json()["count"] == len(INFERENCES)
    assert response.json()["sourced_quotes"]["gap"]


def test_the_connections_route_lists_connections(http):
    http.post(f"{PREFIX}/connections", json={"name": "Northwind", "vendor": "hubspot"})
    body = http.get(f"{PREFIX}/connections").json()
    assert body["count"] == 1
    assert body["unsupported"] == 0


def test_the_connections_route_reports_an_unsupported_connection_rather_than_hiding_it(http):
    http.post(f"{PREFIX}/connections", json={"name": "Fabrikam", "vendor": "salesforce"})
    body = http.get(f"{PREFIX}/connections").json()
    assert body["unsupported"] == 1
    assert "could not be sourced" in body["connections"][0]["unsupported_reason"]


def test_creating_a_connection_is_a_201(http):
    response = http.post(f"{PREFIX}/connections", json={"name": "x", "vendor": "hubspot"})
    assert response.status_code == 201
    assert response.json()["id"].startswith("crm_connection_")


def test_creating_a_connection_without_a_vendor_is_a_422(http):
    """A missing required field is the caller's typo, so 400 like every other one."""
    response = http.post(f"{PREFIX}/connections", json={"name": "x"})
    assert response.status_code == 400
    assert response.json()["error"] == "provisioning_error"


def test_reading_one_connection_reports_what_was_installed(http):
    connection = register_demo(http)
    body = http.get(f"{PREFIX}/connections/{connection['id']}").json()
    assert body["connection"]["id"] == connection["id"]
    assert len(body["objects"]) == 1
    assert body["installations"] == 1


def test_reading_an_unknown_connection_is_a_404(http):
    assert http.get(f"{PREFIX}/connections/crm_connection_missing").status_code == 404


def test_the_manifests_route_lists_versions_per_manifest_id(http):
    http.post(f"{PREFIX}/manifests", json=manifest_body())
    http.post(f"{PREFIX}/manifests", json=manifest_body(version="1.1.0"))
    body = http.get(f"{PREFIX}/manifests").json()
    assert body["count"] == 2
    assert body["versions"] == {"dsr_engagement": ["1.0.0", "1.1.0"]}


def test_creating_a_manifest_is_a_201_and_returns_its_report(http):
    response = http.post(f"{PREFIX}/manifests", json=manifest_body())
    assert response.status_code == 201
    assert response.json()["report"]["installable"] is True


def test_re_registering_an_unchanged_manifest_is_a_200(http):
    http.post(f"{PREFIX}/manifests", json=manifest_body())
    response = http.post(f"{PREFIX}/manifests", json=manifest_body())
    assert response.status_code == 200
    assert response.json()["outcome"] == "unchanged"


def test_re_registering_a_published_version_with_a_different_body_is_a_409(http):
    http.post(f"{PREFIX}/manifests", json=manifest_body())
    body = manifest_body()
    body["properties"] = body["properties"] + [
        {"name": "extra", "label": "Extra", "type": "string", "group_name": GROUP, "length": 8}
    ]
    response = http.post(f"{PREFIX}/manifests", json=body)
    assert response.status_code == 409
    assert response.json()["error"] == "manifest_version_already_registered"


def test_creating_an_unreadable_manifest_is_a_422(http):
    """Also 400: the manifest is the caller's, and unreadable is a typo not a conflict."""
    response = http.post(f"{PREFIX}/manifests", json={"object": {"name": "x"}, "properties": []})
    assert response.status_code == 400
    assert response.json()["error"] == "manifest_invalid"


def test_reading_an_unknown_manifest_is_a_404(http):
    assert http.get(f"{PREFIX}/manifests/nope").status_code == 404


def test_the_diff_route_answers_a_200_and_writes_nothing(http, http_engine):
    connection = register_demo(http)
    before = len(http_engine.store.audit(limit=1000))
    response = http.get(
        f"{PREFIX}/diff",
        params={"connection_id": connection["id"], "manifest_id": "dsr_engagement"},
    )
    assert response.status_code == 200
    assert response.json()["object"]["action"] == "unchanged"
    assert len(http_engine.store.audit(limit=1000)) == before


def test_the_diff_route_for_an_unresearched_vendor_is_a_422_quoting_the_gap(http):
    connection = http.post(
        f"{PREFIX}/connections", json={"name": "Fabrikam", "vendor": "salesforce"}
    ).json()
    http.post(f"{PREFIX}/manifests", json=manifest_body())
    response = http.get(
        f"{PREFIX}/diff",
        params={"connection_id": connection["id"], "manifest_id": "dsr_engagement"},
    )
    assert response.status_code == 422
    assert response.json()["error"] == "vendor_not_supported"
    assert "could not be sourced" in response.json()["detail"]


def test_the_diff_route_requires_a_connection_and_a_manifest(http):
    assert http.get(f"{PREFIX}/diff").status_code == 422


def test_a_first_install_is_a_201(http):
    http.post(f"{PREFIX}/manifests", json=manifest_body())
    connection = http.post(
        f"{PREFIX}/connections", json={"name": "Northwind", "vendor": "hubspot"}
    ).json()
    response = http.post(
        f"{PREFIX}/install",
        json={"connection_id": connection["id"], "manifest_id": "dsr_engagement"},
    )
    assert response.status_code == 201
    assert response.json()["outcome"] == "created"


def test_a_re_install_is_a_200_not_a_201(http):
    register_demo(http)
    connection = http.get(f"{PREFIX}/connections").json()["connections"][0]
    response = http.post(
        f"{PREFIX}/install",
        json={"connection_id": connection["id"], "manifest_id": "dsr_engagement"},
    )
    assert response.status_code == 200
    assert response.json()["outcome"] == "unchanged"
    assert response.json()["created"] == 0


def test_a_dry_run_over_http_is_a_200_and_creates_nothing(http, http_engine):
    connection = register_demo(http)
    response = http.post(
        f"{PREFIX}/install",
        json={"connection_id": connection["id"], "manifest_id": "dsr_engagement",
              "version": "1.0.0", "dry_run": True},
    )
    assert response.status_code == 200
    assert response.json()["outcome"] == "dry_run"
    assert http_engine.objects(connection_id=connection["id"])[0]["declared_properties"] == 4


def test_the_install_route_requires_a_connection_and_a_manifest(http):
    response = http.post(f"{PREFIX}/install", json={"connection_id": "x"})
    assert response.status_code == 400
    assert "manifest_id" in response.json()["detail"]


def test_the_install_route_passes_the_actor_through_to_the_audit_row(http):
    connection = register_demo(http)
    http.post(
        f"{PREFIX}/install",
        json={"connection_id": connection["id"], "manifest_id": "dsr_engagement"},
        params={"actor": "dana"},
    )
    entries = http.get("/api/audit", params={"collection": "crm_installation"}).json()["entries"]
    # The audit log is newest first, so the run this request made is entries[0].
    assert entries[0]["actor"] == "dana"


def test_the_objects_route_serves_the_mapping(http):
    register_demo(http)
    body = http.get(f"{PREFIX}/objects").json()
    assert body["count"] == 1
    assert body["objects"][0]["room_object_id"] == "dsr.engagement"
    assert body["objects"][0]["crm_object_id"]


def test_the_objects_route_can_filter_by_room(http):
    register_demo(http, room_id="room_a")
    assert http.get(f"{PREFIX}/objects", params={"room_id": "room_a"}).json()["count"] == 1
    assert http.get(f"{PREFIX}/objects", params={"room_id": "room_b"}).json()["count"] == 0


def test_reading_one_object_serves_its_properties_and_keys(http):
    connection = register_demo(http)
    object_id = http.get(f"{PREFIX}/objects").json()["objects"][0]["id"]
    body = http.get(f"{PREFIX}/objects/{object_id}").json()
    assert body["connection"]["id"] == connection["id"]
    assert len(body["properties"]) == 4
    assert body["keys"] == []


def test_reading_an_unknown_object_is_a_404(http):
    assert http.get(f"{PREFIX}/objects/crm_object_missing").status_code == 404


def test_listing_an_objects_properties(http):
    register_demo(http)
    object_id = http.get(f"{PREFIX}/objects").json()["objects"][0]["id"]
    body = http.get(f"{PREFIX}/objects/{object_id}/properties").json()
    assert body["count"] == 4
    assert all(row["origin"] == "installed" for row in body["properties"])


def test_adding_a_property_over_http_is_a_201(http):
    register_demo(http)
    object_id = http.get(f"{PREFIX}/objects").json()["objects"][0]["id"]
    response = http.post(
        f"{PREFIX}/objects/{object_id}/properties",
        json={"name": "campaign_touch", "label": "Campaign touch", "type": "string",
              "group_name": GROUP, "length": 80},
    )
    assert response.status_code == 201
    assert response.json()["origin"] == "manual"


def test_adding_a_property_that_exists_is_a_409(http):
    register_demo(http)
    object_id = http.get(f"{PREFIX}/objects").json()["objects"][0]["id"]
    response = http.post(
        f"{PREFIX}/objects/{object_id}/properties",
        json={"name": "room_name", "label": "Changed", "type": "string", "group_name": GROUP},
    )
    assert response.status_code == 409
    assert response.json()["error"] == "property_already_exists"


def test_the_keys_route_counts_the_four_statuses(http):
    http.post(f"{PREFIX}/manifests", json=manifest_body())
    connection = http.post(
        f"{PREFIX}/connections", json={"name": "Contoso", "vendor": "dataverse"}
    ).json()
    http.post(
        f"{PREFIX}/install",
        json={"connection_id": connection["id"], "manifest_id": "dsr_engagement"},
    )
    body = http.get(f"{PREFIX}/keys").json()
    assert body["summary"] == {"Pending": 1, "In Progress": 0, "Active": 0, "Failed": 0}


def test_poll_and_reactivate_over_http_drive_the_status(http):
    http.post(f"{PREFIX}/manifests", json=manifest_body())
    connection = http.post(
        f"{PREFIX}/connections",
        json={"name": "Contoso", "vendor": "dataverse", "simulate": {"key_index": "failed"}},
    ).json()
    http.post(
        f"{PREFIX}/install",
        json={"connection_id": connection["id"], "manifest_id": "dsr_engagement"},
    )
    key_id = http.get(f"{PREFIX}/keys").json()["keys"][0]["id"]

    assert http.post(f"{PREFIX}/keys/{key_id}/poll").json()["status"] == "Failed"
    repaired = http.post(f"{PREFIX}/keys/{key_id}/reactivate").json()
    assert repaired["status"] == "Pending"
    assert repaired["reactivated"] is True
    assert http.post(f"{PREFIX}/keys/{key_id}/poll").json()["status"] == "In Progress"
    assert http.post(f"{PREFIX}/keys/{key_id}/poll").json()["status"] == "Active"


def test_reactivating_an_active_key_over_http_reports_no_reactivation(http):
    http.post(f"{PREFIX}/manifests", json=manifest_body())
    connection = http.post(
        f"{PREFIX}/connections", json={"name": "Contoso", "vendor": "dataverse"}
    ).json()
    http.post(
        f"{PREFIX}/install",
        json={"connection_id": connection["id"], "manifest_id": "dsr_engagement"},
    )
    key_id = http.get(f"{PREFIX}/keys").json()["keys"][0]["id"]
    for _ in range(2):
        http.post(f"{PREFIX}/keys/{key_id}/poll")
    assert http.post(f"{PREFIX}/keys/{key_id}/reactivate").json()["reactivated"] is False


def test_polling_an_unknown_key_over_http_is_a_404(http):
    assert http.post(f"{PREFIX}/keys/crm_sync_key_missing/poll").status_code == 404


def test_reading_one_key_over_http(http):
    http.post(f"{PREFIX}/manifests", json=manifest_body())
    connection = http.post(
        f"{PREFIX}/connections", json={"name": "Contoso", "vendor": "dataverse"}
    ).json()
    http.post(
        f"{PREFIX}/install",
        json={"connection_id": connection["id"], "manifest_id": "dsr_engagement"},
    )
    key_id = http.get(f"{PREFIX}/keys").json()["keys"][0]["id"]
    body = http.get(f"{PREFIX}/keys/{key_id}").json()
    assert body["key"]["status"] == "Pending"
    assert body["key"]["columns"] == ["engagement_id"]


def test_the_installations_route_counts_over_the_rows_it_returns(http):
    register_demo(http)
    connection = http.get(f"{PREFIX}/connections").json()["connections"][0]
    http.post(
        f"{PREFIX}/install",
        json={"connection_id": connection["id"], "manifest_id": "dsr_engagement",
              "version": "1.0.0", "dry_run": True},
    )
    body = http.get(f"{PREFIX}/installations").json()
    assert body["count"] == 2
    assert body["totals"]["dry_runs"] == 1


def test_the_installations_route_can_filter_by_outcome(http):
    register_demo(http)
    connection = http.get(f"{PREFIX}/connections").json()["connections"][0]
    http.post(
        f"{PREFIX}/install",
        json={"connection_id": connection["id"], "manifest_id": "dsr_engagement",
              "version": "1.0.0", "dry_run": True},
    )
    assert http.get(f"{PREFIX}/installations", params={"outcome": "dry_run"}).json()["count"] == 1


def test_reading_one_installation_serves_its_requests(http):
    register_demo(http)
    run_id = http.get(f"{PREFIX}/installations").json()["installations"][0]["id"]
    body = http.get(f"{PREFIX}/installations/{run_id}").json()
    assert body["installation"]["outcome"] == "created"
    assert body["installation"]["requests"]


def test_reading_an_unknown_installation_is_a_404(http):
    assert http.get(f"{PREFIX}/installations/crm_installation_missing").status_code == 404


def test_the_room_objects_route_serves_the_room(http):
    connection = register_demo(http, room_id="room_a")
    body = http.get(f"{PREFIX}/rooms/room_a/objects").json()
    assert body["count"] == 1
    assert body["unsupported"] == []
    assert body["connections"][0]["id"] == connection["id"]


def test_the_room_objects_route_names_its_unsupported_connections(http):
    http.post(f"{PREFIX}/connections", json={"name": "Fabrikam", "vendor": "salesforce",
                                             "room_id": "room_a"})
    body = http.get(f"{PREFIX}/rooms/room_a/objects").json()
    assert body["unsupported"][0]["name"] == "Fabrikam"
    assert "could not be sourced" in body["unsupported"][0]["reason"]


def test_the_room_summary_route_serves_the_room(http):
    register_demo(http, room_id="room_a")
    body = http.get(f"{PREFIX}/rooms/room_a/summary").json()
    assert body["room_id"] == "room_a"
    assert body["objects"] == 1
    assert body["properties"] == 4


def test_the_room_summary_route_survives_an_empty_room(http):
    assert http.get(f"{PREFIX}/rooms/room_none/summary").json()["objects"] == 0


# --------------------------------------------------------------------------- #
# The audit-source rule, over HTTP
# --------------------------------------------------------------------------- #


def test_every_write_audit_row_names_a_route_the_app_serves(http):
    """The central guarantee, checked against the route table the host reports.

    The same class of bug has shipped in this codebase before: a feature's audit
    log kept naming a path the app had stopped serving.
    """
    connection = register_demo(http, room_id="room_a")
    object_id = http.get(f"{PREFIX}/objects").json()["objects"][0]["id"]
    http.post(
        f"{PREFIX}/objects/{object_id}/properties",
        json={"name": "campaign_touch", "label": "Campaign touch", "type": "string",
              "group_name": GROUP, "length": 80},
    )
    dataverse = http.post(
        f"{PREFIX}/connections", json={"name": "Contoso", "vendor": "dataverse"}
    ).json()
    http.post(
        f"{PREFIX}/install",
        json={"connection_id": dataverse["id"], "manifest_id": "dsr_engagement"},
    )
    key_id = http.get(f"{PREFIX}/keys").json()["keys"][0]["id"]
    http.post(f"{PREFIX}/keys/{key_id}/poll")
    http.post(f"{PREFIX}/keys/{key_id}/reactivate")
    assert connection["id"]

    served = [
        route
        for feature in http.get("/api/features").json()["features"]
        for route in feature["routes"]
    ]
    entries = http.get("/api/audit", params={"limit": 500}).json()["entries"]
    sources = {entry["source"] for entry in entries if entry["source"]}

    # The core records API and the seeder write with their own sources; only the
    # rows this feature's HTTP layer produced are in scope here.
    ours = {source for source in sources if source.split(" ", 1)[1].startswith(PREFIX)}
    assert len(ours) >= 6, f"only {len(ours)} wf-036 writes were audited; saw {sorted(ours)}"
    for source in sorted(ours):
        assert _matches_registered_route(source, served), (
            f"audit row names {source!r}, which is not a route this app serves"
        )


def test_a_property_added_by_hand_audits_against_its_own_route(http):
    register_demo(http)
    object_id = http.get(f"{PREFIX}/objects").json()["objects"][0]["id"]
    http.post(
        f"{PREFIX}/objects/{object_id}/properties",
        json={"name": "campaign_touch", "label": "Campaign touch", "type": "string",
              "group_name": GROUP, "length": 80},
    )
    entries = http.get("/api/audit", params={"collection": "crm_property"}).json()["entries"]
    assert entries[0]["source"] == f"POST {PREFIX}/objects/{{object_id}}/properties"


def test_a_polled_key_audits_against_the_poll_route_not_the_install_route(http):
    http.post(f"{PREFIX}/manifests", json=manifest_body())
    connection = http.post(
        f"{PREFIX}/connections", json={"name": "Contoso", "vendor": "dataverse"}
    ).json()
    http.post(
        f"{PREFIX}/install",
        json={"connection_id": connection["id"], "manifest_id": "dsr_engagement"},
    )
    key_id = http.get(f"{PREFIX}/keys").json()["keys"][0]["id"]
    http.post(f"{PREFIX}/keys/{key_id}/poll")
    entries = http.get("/api/audit", params={"collection": "crm_sync_key"}).json()["entries"]
    assert entries[0]["source"] == f"POST {PREFIX}/keys/{{key_id}}/poll"
    assert entries[1]["source"] == f"POST {PREFIX}/install"


def test_writes_do_not_record_a_path_this_app_does_not_serve(http):
    """Explicitly: no hardcoded URL, and no vendor hostname in the audit log.

    The second half matters because the researched endpoints are HubSpot's and
    Dataverse's. This product is not a proxy for either vendor, and an audit row
    naming a vendor's absolute URL would tell a reviewer a request went somewhere
    this app never sends one.
    """
    register_demo(http)
    entries = http.get("/api/audit", params={"limit": 500}).json()["entries"]
    for entry in entries:
        source = entry.get("source") or ""
        assert "http" not in source.lower()
        assert "hubspot" not in source.lower()
        assert "dataverse" not in source.lower()
        assert "salesforce" not in source.lower()
        if source:
            assert source.split(" ")[0] in {"POST", "PATCH", "DELETE", "PUT"}


def test_the_vendor_paths_appear_only_in_the_run_record_not_in_an_audit_source(http):
    register_demo(http)
    run = http.get("/api/audit", params={"collection": "crm_installation"}).json()["entries"][0]
    paths = {row["path"] for row in run["after_state"]["requests"]}
    assert "/crm-object-schemas/2026-09/schemas" in paths
    assert run["source"] == f"POST {PREFIX}/install"


def test_the_audit_row_carries_the_room_the_object_belongs_to(http):
    register_demo(http, room_id="room_a")
    entries = http.get("/api/audit", params={"collection": "crm_object"}).json()["entries"]
    assert entries[0]["room_id"] == "room_a"


# --------------------------------------------------------------------------- #
# The inference register
# --------------------------------------------------------------------------- #


def inference(inference_id: str) -> dict[str, Any]:
    found = by_id(inference_id)
    assert found is not None, inference_id
    return found


def test_every_inference_is_named_and_justified():
    for entry in INFERENCES:
        assert entry["id"]
        assert entry["topic"]
        assert entry["basis"]
        assert entry["why"]
        assert entry["change_it"]
        assert entry["blast_radius"]
        assert entry["value"]


def test_the_inference_ids_are_unique():
    assert len({entry["id"] for entry in INFERENCES}) == len(INFERENCES)


def test_an_unknown_inference_is_none():
    assert by_id("no-such-inference") is None


def test_the_simulated_vendor_inference_says_the_effect_is_stored():
    entry = inference("simulated-vendor-state")
    assert entry["value"]["the_effect_is_stored"] is True
    assert entry["value"]["opens_no_socket"] is True
    assert "no-op" in entry["why"]
    assert "CRM account" in entry["basis"]


def test_the_simulated_vendor_inference_lists_both_sets_of_collections():
    entry = inference("simulated-vendor-state")
    assert entry["value"]["remote_collections"] == [
        "crm_remote_object", "crm_remote_property", "crm_remote_key",
    ]
    assert "crm_object" in entry["value"]["own_collections"]


def test_the_unsupported_vendor_inference_says_a_connection_is_still_registrable():
    entry = inference("unsupported-vendor-is-a-refusal")
    assert entry["value"]["status"] == 422
    assert entry["value"]["connection_is_still_registrable"] is True
    assert "salesforce" in entry["value"]["unsupported"]


def test_the_findings_inference_states_that_a_skip_is_never_silent():
    entry = inference("blocking-vs-property-findings")
    never_silent = entry["value"]["skipped_is_never_silent"]
    assert "counted on the run record" in never_silent
    assert any("complete=false" in line for line in never_silent)


def test_the_never_change_inference_says_there_is_no_update_path():
    entry = inference("existing-property-is-never-changed")
    assert entry["value"]["no_update_path_exists"] is True
    assert "never destructively renaming" in entry["basis"]


def test_the_left_in_place_inference_says_nothing_is_dropped():
    entry = inference("a-removed-field-is-left-in-place")
    assert entry["value"]["dropped"] is False
    assert entry["value"]["action"] == "left_in_place"


def test_the_key_length_inference_pins_the_sourced_numbers_and_the_judgement():
    entry = inference("key-length-must-be-declared")
    assert entry["value"]["max_bytes"] == KEY_MAX_BYTES
    assert entry["value"]["max_columns"] == KEY_MAX_COLUMNS
    assert entry["value"]["undeclared_string_length"].startswith("refused")


def test_the_index_inference_names_the_four_statuses_and_the_unsourced_cadence():
    entry = inference("key-index-advances-on-poll")
    assert entry["value"]["statuses"] == list(KEY_STATUSES)
    assert entry["value"]["polls_to_active"] == DEFAULT_INDEX_POLLS
    assert entry["value"]["reactivate_on_active"].startswith("a no-op")


def test_the_key_inference_says_hubspot_gets_no_key_request():
    entry = inference("key-requested-only-where-sourced")
    assert entry["value"]["hubspot"] == "unsupported; the request is reported, not sent"
    assert "does not say how HubSpot's uniqueness is set" in entry["basis"]
    assert "hasUniqueValue" in entry["why"]


def test_the_connection_inference_says_it_is_an_addition_here():
    entry = inference("connection-registry-is-added-here")
    assert entry["value"]["record"] == "crm_connection"
    assert "assumed to exist" in entry["basis"]


def test_the_scope_inference_says_provisioning_is_per_connection():
    entry = inference("provisioning-is-scoped-to-the-connection")
    assert entry["value"]["installed_against"] == "a connection"
    assert entry["value"]["room_scoped"] == "the reads under /rooms/{room_id}/"


def test_the_manifest_immutability_inference_pins_the_three_outcomes():
    entry = inference("manifest-versions-are-immutable")
    assert "409" in entry["value"]["same_id_same_version_different_body"]


def test_the_dry_run_inference_says_a_preview_stores_one_row_and_nothing_else():
    entry = inference("dry-run-stores-a-run-record")
    assert entry["value"]["outcome"] == "dry_run"
    assert "crm_remote_*" in entry["value"]["never_writes"]
    assert "crm_installation" in entry["value"]["writes"]


def test_the_dry_run_inference_says_a_preview_reports_the_requests(engine, connection, manifest):
    """The requests are recorded for a preview too, which is the point of recording them."""
    run = engine.install(
        connection["id"], "dsr_engagement", "1.0.0", dry_run=True, source=SOURCE
    )["run"]
    assert any(row["method"] == "POST" for row in run["requests"])


def test_the_register_says_which_half_is_sourced(http):
    body = http.get(f"{PREFIX}/inferences").json()
    assert body["sourced"]["vendors"] == list(VENDORS)
    assert body["sourced"]["key_limits"] == {"max_bytes": 900, "max_columns": 16}
    assert body["sourced_quotes"]["idempotency"].startswith("Creation is idempotent")


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #


@pytest.fixture()
def seeded(store):
    module = load_feature(MODULE_NAME)
    rooms = seed_rooms(store)
    summary = module.seed(
        store.db,
        {"room_ids": rooms, "now": datetime.now(timezone.utc), "rng": random.Random("wf036")},
    )
    return ProvisioningEngine(store), summary, rooms


def test_the_seed_returns_a_description_of_what_it_added(seeded):
    _, summary, _ = seeded
    assert "connections" in summary
    assert "idempotent" in summary
    assert "installer runs" in summary


def test_the_seed_registers_a_connection_per_vendor_plus_the_gap(seeded):
    engine, _, _ = seeded
    vendors = {row["vendor"] for row in engine.connections()}
    assert vendors == {"hubspot", "dataverse", "salesforce"}


def test_the_seed_marks_the_salesforce_connection_unsupported(seeded):
    engine, _, _ = seeded
    row = next(c for c in engine.connections() if c["vendor"] == "salesforce")
    assert row["supported"] is False
    assert "could not be sourced" in row["unsupported_reason"]


def test_the_seed_registers_three_versions_of_the_engagement_manifest(seeded):
    engine, _, _ = seeded
    assert [m["version"] for m in engine.manifests(manifest_id="dsr_engagement")] == [
        "1.0.0", "1.1.0", "1.2.0",
    ]


def test_the_seed_installs_the_hubspot_object_and_its_properties(seeded):
    engine, _, _ = seeded
    hubspot = next(c for c in engine.connections() if c["vendor"] == "hubspot")
    objects = engine.objects(connection_id=hubspot["id"])
    assert len(objects) == 1
    assert len(engine.properties(objects[0]["id"])) == 5


def test_the_seed_proves_idempotency_with_a_second_run(seeded):
    engine, _, _ = seeded
    hubspot = next(c for c in engine.connections() if c["vendor"] == "hubspot")
    runs = engine.runs(connection_id=hubspot["id"])
    assert [run["outcome"] for run in runs] == ["dry_run", "unchanged", "created"]


def test_the_seed_leaves_a_relabelled_field_untouched(seeded):
    engine, _, _ = seeded
    dataverse = next(c for c in engine.connections() if c["vendor"] == "dataverse")
    conflicts = [row for run in engine.runs(connection_id=dataverse["id"]) for row in run["conflicts"]]
    assert [row["property"] for row in conflicts] == ["action"]
    remote = engine.crm.find_property(
        engine.connection(dataverse["id"]), "dsr_engagement", "action"
    )
    assert remote["label"] == "Action"


def test_the_seed_leaves_a_renamed_column_in_place(seeded):
    engine, _, _ = seeded
    dataverse = next(c for c in engine.connections() if c["vendor"] == "dataverse")
    remote = engine.crm
    assert remote.find_property(engine.connection(dataverse["id"]), "dsr_engagement", "room_label")
    assert remote.find_property(engine.connection(dataverse["id"]), "dsr_engagement", "room_name")


def test_the_seed_ends_with_a_repaired_active_key(seeded):
    engine, _, _ = seeded
    keys = engine.keys()
    assert len(keys) == 1
    assert keys[0]["status"] == "Active"
    assert keys[0]["reactivated_count"] == 1


def test_the_seed_leaves_a_manifest_the_key_limit_refuses(seeded):
    engine, _, _ = seeded
    report = engine.manifest_report("dsr_engagement_scoped", "0.1.0")
    assert report["sync_key"]["ok"] is False
    assert "900 bytes per key" in report["sync_key"]["error"]


def test_the_seed_binds_each_connection_to_its_demo_room(seeded):
    engine, _, rooms = seeded
    demo_room_ids = {room_id for room_id, _ in rooms}
    bound = {row["room_id"] for row in engine.connections()}
    assert bound == demo_room_ids


def test_the_seed_survives_having_no_rooms(store):
    module = load_feature(MODULE_NAME)
    summary = module.seed(
        store.db,
        {"room_ids": [], "now": datetime.now(timezone.utc), "rng": random.Random("wf036")},
    )
    assert "connections" in summary
    engine = ProvisioningEngine(store)
    assert len(engine.connections()) == 3


def test_the_seed_is_deterministic(store):
    """Two runs of the seeder must produce the same demo, or a reviewer is reading noise."""
    module = load_feature(MODULE_NAME)
    first = AuditedDatabase(store.db.path)
    second = AuditedDatabase(store.db.path)
    try:
        rooms = seed_rooms(store)
        module.seed(first, {"room_ids": rooms, "now": datetime.now(timezone.utc),
                            "rng": random.Random("wf036")})
        module.seed(second, {"room_ids": rooms, "now": datetime.now(timezone.utc),
                             "rng": random.Random("wf036")})
    finally:
        first.close()
        second.close()


def test_the_seeds_demo_survives_the_full_suite(store):
    """The demo must be runnable, not merely importable."""
    module = load_feature(MODULE_NAME)
    rooms = seed_rooms(store)
    summary = module.seed(
        store.db,
        {"room_ids": rooms, "now": datetime.now(timezone.utc), "rng": random.Random("wf036")},
    )
    assert "0 created by the re-install (idempotent)" in summary
    assert "1 relabelled field left untouched" in summary
    assert "Failed -> Pending -> Active" in summary
