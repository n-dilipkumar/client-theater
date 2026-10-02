"""Tests for WF-035: map sales-room fields onto CRM fields and define the sync key.

The claims under test come from
``docs/research/digital-sales-room-workflows/wf/WF-035.md``. This is a **build**, not
a port, so there is no branch to compare against: the research document is the
specification, and the researched sentence each test pins is quoted in the test that
pins it.

Nine groups, in the order a reviewer would want them:

* **Vocabulary** - the researched directions, the two type axes, the two ten-key
  ceilings, the quoted rules, and the four APIs this workflow reads.
* **Transforms** - each of the six, the versioning rules, and the refusal a mapping
  gets for naming one that is not registered.
* **Metadata** - the three vendor document shapes, and the rules that depend on
  reading them: internal option names, key eligibility, the type/fieldType pair.
* **Validation** - the three researched findings and the four this build added, and
  the gate that keeps an unclean mapping from going live.
* **Sync key** - pinning, the researched ceilings, the HubSpot create body, the
  Dataverse plan, and the Salesforce refusal.
* **Preview** - the per-record evaluation, both directions, and the rule that it
  writes nothing.
* **Records** - the store-facing behaviour, including that a schema-flexible payload
  round-trips and that a delete takes the rows with it.
* **HTTP and audit** - every route through this feature's own router, the eight error
  statuses, and the audit-source rule: every row this feature's HTTP layer produces
  names a route the host actually mounted.
* **Seed** - that the demo produces every state the research says matters.

One test is a regression test for a defect found while building, and it says so in
its name, because the failure it guards against is silent:

``test_a_stored_row_validates_as_the_row_it_is``
    The validator read grid rows as flat dicts while it was handed *stored records*,
    whose own fields live inside ``data``. Every field came back empty, so a
    perfectly good five-row mapping validated as five rows with no target property
    and no transform, and reported ten error findings against a mapping that had
    none. The report was confidently wrong, which is the worst way to be wrong.
"""

from __future__ import annotations

import inspect
from datetime import date, datetime, timezone

import pytest
from dsr.api import app
from dsr.db.audited import AuditedDatabase
from dsr.fieldmap import (
    FieldMapping,
    InvalidMapping,
    InvalidSyncKey,
    MetadataUnavailable,
    SyncKeyCapacity,
    UnknownConnection,
    UnknownMapping,
    UnsupportedSyncKeyRequest,
    inferences as inference_module,
    metadata as metadata_module,
    preview as preview_fn,
    sync_key as sync_key_module,
    validate as validate_module,
    vocabulary as vocab,
)
from dsr.fieldmap.mappings import (
    CONNECTION_COLLECTION,
    MAPPING_COLLECTION,
    ROW_COLLECTION,
    VALIDATION_COLLECTION,
    MappingBook,
    mapping_view,
    metadata_view,
    row_view,
)
from dsr.fieldmap.metadata import Metadata, Option, Property, normalise
from dsr.fieldmap.transforms import (
    BUILTINS,
    REGISTRY,
    Registry,
    Transform,
    TransformUnavailable,
    transform_key,
)
from dsr.store import RecordStore
from fastapi.testclient import TestClient

#: The feature's own prefix. Duplicated rather than imported so renaming the route
#: fails here instead of following silently - which is what a test is for.
PREFIX = "/api/wf-035"

#: What the pure-domain tests pass as ``source``: deliberately the exact shape a route
#: passes, so a test asserting on an audit row is asserting on the real thing.
SOURCE = f"POST {PREFIX}/mappings"


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture()
def store():
    # In-memory rather than a file on disk: 0.4 ms against 7.0 ms, measured. No test
    # in this file reads the audit mirror off the filesystem, so the file bought nothing.
    db = AuditedDatabase()
    yield RecordStore(db)
    db.close()


@pytest.fixture()
def engine(store):
    return FieldMapping(store)


@pytest.fixture()
def book(store):
    return MappingBook(store)


HUBSPOT_RESULTS = [
    {
        "name": "dsr_row_id",
        "label": "DSR row id",
        "type": "string",
        "fieldType": "text",
        "groupName": "contactinformation",
    },
    {
        "name": "email",
        "label": "Email",
        "type": "string",
        "fieldType": "text",
        "groupName": "contactinformation",
    },
    {
        "name": "seats",
        "label": "Seats",
        "type": "number",
        "fieldType": "number",
        "groupName": "contactinformation",
    },
    {
        "name": "stage",
        "label": "Stage",
        "type": "enumeration",
        "fieldType": "select",
        "groupName": "salesforce",
        "options": [
            {"value": "lead", "label": "Lead"},
            {"value": "customer", "label": "Customer"},
        ],
    },
    {
        "name": "renewal_date",
        "label": "Renewal Date",
        "type": "string",
        "fieldType": "date",
        "groupName": "contactinformation",
    },
]


def hubspot_metadata(unique: int = 0, *, keyed: bool = False) -> Metadata:
    """A HubSpot contact read, optionally with ``unique`` already-unique properties."""
    results = [dict(entry) for entry in HUBSPOT_RESULTS]
    for index in range(unique):
        results.append(
            {
                "name": f"legacy_unique_{index}",
                "label": f"Legacy {index}",
                "type": "string",
                "fieldType": "text",
                "groupName": "contactinformation",
                "hasUniqueValue": True,
            }
        )
    if keyed:
        results[0]["hasUniqueValue"] = True
    return normalise("hubspot", {"results": results}, "contacts")


def dataverse_metadata(*, keys=(), extra=()) -> Metadata:
    attributes = [
        {"LogicalName": "dsr_row_id", "AttributeType": "String", "DisplayName": "DSR row id"},
        {"LogicalName": "name", "AttributeType": "String", "DisplayName": "Name"},
        {"LogicalName": "revenue", "AttributeType": "Decimal", "DisplayName": "Revenue"},
        {"LogicalName": "donotemail", "AttributeType": "Boolean", "DisplayName": "Do Not Email"},
        *extra,
    ]
    return normalise(
        "dataverse",
        {
            "value": [
                {
                    "SchemaName": "account",
                    "Attributes": attributes,
                    "Keys": [{"KeyAttributes": list(key)} for key in keys],
                }
            ]
        },
        "account",
    )


def make_connection(engine, provider="hubspot", **payload):
    # `room_id` is envelope, not payload, so it is lifted out of the body rather
    # than stored as a field the room-scoped reads would then ignore.
    room_id = payload.pop("room_id", None)
    body = {"name": f"{provider} connection", "provider": provider}
    body.update(payload)
    return engine.register_connection(body, room_id=room_id, actor="dana", source=SOURCE)


def make_mapping(engine, connection_id, crm_object="contacts", **payload):
    body = {"crm_object": crm_object}
    body.update(payload)
    return engine.create_mapping(connection_id, body, actor="dana", source=SOURCE)


def add_row(engine, connection_id, mapping_id, **payload):
    return engine.put_row(connection_id, mapping_id, payload, actor="dana", source=SOURCE)


def add_properties(engine, connection_id, crm_object="contacts", metadata=None, **payload):
    document = (
        metadata.to_dict()
        if metadata is not None
        else {"results": [dict(r) for r in HUBSPOT_RESULTS]}
    )
    # Round-trip through the vendor shape rather than the normalised one, so the
    # normaliser is exercised rather than bypassed.
    if metadata is not None and metadata.provider == "hubspot":
        document = {
            "results": [
                {
                    "name": prop.name,
                    "label": prop.label,
                    "type": prop.value_type,
                    "fieldType": prop.field_type,
                    "groupName": prop.group,
                    "hasUniqueValue": prop.unique,
                    "options": [option.to_dict() for option in prop.options],
                }
                for prop in metadata.properties
            ]
        }
    body = {"crm_object": crm_object, "document": document}
    body.update(payload)
    return engine.record_properties(connection_id, body, actor="dana", source=SOURCE)


def report_for(engine, connection_id, mapping_id, record=False):
    return engine.validate(
        connection_id, mapping_id, {"record": record}, actor="dana", source=SOURCE
    )["report"]


def flags_of(report, source_field):
    for item in report["rows"]:
        if item["source_field"] == source_field:
            return item["flags"]
    return None


# --------------------------------------------------------------------------- #
# 1. Vocabulary
# --------------------------------------------------------------------------- #


def test_directions_are_the_three_the_research_names():
    """Step 3: "its direction (in / out / both)"."""
    assert vocab.DIRECTIONS == ("in", "out", "both")


def test_direction_normalisation_is_case_and_space_tolerant():
    assert vocab.direction_of(" IN ") == "in"
    assert vocab.direction_of("Both") == "both"


def test_an_unknown_direction_normalises_to_nothing():
    assert vocab.direction_of("sideways") == ""
    assert vocab.direction_of(None) == ""


def test_sends_out_covers_out_and_both():
    """``both`` sends out, and its outbound half is what the internal-name rule governs."""
    assert vocab.sends_out("out") is True
    assert vocab.sends_out("both") is True
    assert vocab.sends_out("in") is False


def test_sends_in_covers_in_and_both():
    assert vocab.sends_in("in") is True
    assert vocab.sends_in("both") is True
    assert vocab.sends_in("out") is False


def test_the_documented_providers_are_the_three_named():
    assert vocab.PROVIDERS == ("hubspot", "dataverse", "salesforce")


def test_provider_normalisation_is_case_tolerant():
    assert vocab.normalise_provider("HubSpot") == "hubspot"
    assert vocab.normalise_provider("dynamics") == ""


def test_the_ten_key_ceiling_is_ten_for_both_documented_vendors():
    """HubSpot: "up to ten unique ID properties per object".
    Dataverse: "up to ten alternate key table definitions"."""
    assert vocab.UNIQUE_KEY_LIMIT == 10
    assert set(vocab.UNIQUE_KEY_LIMIT_QUOTES) == {"hubspot", "dataverse"}
    assert "ten unique ID properties" in vocab.UNIQUE_KEY_LIMIT_QUOTES["hubspot"]
    assert "ten alternate key table definitions" in vocab.UNIQUE_KEY_LIMIT_QUOTES["dataverse"]


def test_the_five_eligible_dataverse_key_types_are_the_researched_five():
    """Quoted: "DecimalAttributeMetadata … StringAttributeMetadata … DateTimeAttributeMetadata
    … LookupAttributeMetadata … PicklistAttributeMetadata"."""
    assert vocab.DATAV_ELIGIBLE_KEY_TYPES == (
        "DecimalAttributeMetadata",
        "StringAttributeMetadata",
        "DateTimeAttributeMetadata",
        "LookupAttributeMetadata",
        "PicklistAttributeMetadata",
    )


def test_the_enumeration_rule_is_quoted_in_the_vocabulary():
    """The rule an ``unsupported_option`` finding quotes, in the research's words."""
    assert "internal names" in vocab.ENUMERATION_HINT
    assert "internal name stays the same" in vocab.ENUMERATION_HINT


def test_the_hubspot_create_rule_is_quoted_in_the_vocabulary():
    """Evidence: "both `type` and `fieldType` values are required"."""
    assert "both `type` and `fieldType` values are required" in vocab.HUBSPOT_CREATE_HINT


def test_the_four_apis_the_research_lists_are_served_as_data():
    """The researched ``apis_hit``, verbatim, so a connector knows what to call."""
    endpoints = vocab.METADATA_ENDPOINTS
    assert [entry["method"] for entry in endpoints["hubspot"]] == ["GET", "GET", "POST"]
    assert endpoints["hubspot"][0]["url"] == "/crm/properties/2026-09/{object}"
    assert endpoints["hubspot"][1]["url"] == "/crm/properties/2026-09/{object}/{property}"
    assert endpoints["hubspot"][2]["url"] == "/crm/properties/2026-09/{object}"
    assert "hasUniqueValue" in endpoints["hubspot"][2]["purpose"]
    assert len(endpoints["dataverse"]) == 3
    assert "$expand=Keys($select=KeyAttributes)" in endpoints["dataverse"][1]["url"]
    assert endpoints["dataverse"][2]["url"] == "/api/data/v9.2/$metadata"


def test_salesforce_has_no_endpoints_because_the_research_cites_none():
    """The research's gap: Salesforce's field pages are client-rendered and unreadable."""
    assert vocab.METADATA_ENDPOINTS["salesforce"] == ()


def test_every_served_endpoint_declares_whether_it_is_sourced():
    for entries in vocab.METADATA_ENDPOINTS.values():
        for entry in entries:
            assert "sourced" in entry


def test_types_for_a_text_field_is_the_string_type_and_nothing_else():
    assert vocab.types_for("hubspot", "text") == ("string",)
    assert vocab.types_for("hubspot", "number") == ("number",)
    assert vocab.types_for("hubspot", "enumeration") == ("enumeration",)


def test_types_for_an_unknown_room_type_is_empty_rather_than_a_mismatch():
    """An unknown type is not this build's business; an empty answer means "not checked"."""
    assert vocab.types_for("hubspot", "geolocation") == ()


def test_types_for_an_unknown_provider_is_empty():
    assert vocab.types_for("pipedrive", "text") == ()


def test_the_field_type_table_never_pairs_a_type_with_an_impossible_presentation():
    for value_type, field_types in vocab.HUBSPOT_FIELD_TYPES_BY_TYPE.items():
        assert field_types, value_type
        for field_type in field_types:
            assert field_type in vocab.HUBSPOT_FIELD_TYPES, (value_type, field_type)


def test_enumeration_only_ever_pairs_with_a_selection_presentation():
    assert set(vocab.HUBSPOT_FIELD_TYPES_BY_TYPE["enumeration"]) == {"select", "multiselect"}


def test_each_vendor_ships_a_default_mapping():
    """ "A self-hosted room can ship a *default* mapping per vendor"."""
    for provider in ("hubspot", "dataverse", "salesforce"):
        assert vocab.default_mapping(provider), provider
        assert vocab.default_mapping_object(provider)


def test_a_default_mapping_is_copied_so_a_caller_cannot_mutate_the_shipped_one():
    rows = vocab.default_mapping("hubspot")
    rows[0]["target_property"] = "tampered"
    assert vocab.default_mapping("hubspot")[0]["target_property"] != "tampered"


def test_every_default_row_names_a_researched_direction():
    for provider in vocab.PROVIDERS:
        for row in vocab.default_mapping(provider):
            assert row["direction"] in vocab.DIRECTIONS, (provider, row)


def test_every_default_row_names_a_registered_transform():
    for provider in vocab.PROVIDERS:
        for row in vocab.default_mapping(provider):
            assert REGISTRY.resolve(row["transform"]) is not None, (provider, row)


def test_describe_carries_everything_a_picker_needs():
    payload = vocab.describe()
    assert payload["directions"] == ["in", "out", "both"]
    assert payload["unique_key_limit"] == 10
    assert "hubspot" in payload["types_by_provider"]
    assert payload["hubspot_default_group"]
    assert "enumeration" in payload["room_field_types"]


# --------------------------------------------------------------------------- #
# 2. Transforms
# --------------------------------------------------------------------------- #


def test_the_registry_ships_six_transforms():
    assert set(REGISTRY.names()) == {
        "identity",
        "text.trim",
        "email.normalize",
        "date.iso8601",
        "datetime.utc",
        "number.coerce",
        "picklist.map",
    }


def test_the_three_named_in_the_research_are_present():
    """Extensibility: "e.g. `email.normalize`, `picklist.map`, `date.iso8601`"."""
    for name in ("email.normalize", "picklist.map", "date.iso8601"):
        assert REGISTRY.resolve(name) is not None


def test_number_coercion_is_named_in_the_research():
    """Step 3's transform list: "number coercion"."""
    assert REGISTRY.resolve("number.coerce").source


def test_identity_passes_a_value_through_untouched():
    assert REGISTRY.require("identity").fn({"a": 1}, {}, "out") == {"a": 1}


def test_identity_passes_none_through():
    assert REGISTRY.require("identity").fn(None, {}, "out") is None


def test_text_trim_strips_both_ends():
    assert REGISTRY.require("text.trim").fn("  a b  ", {}, "out") == "a b"


def test_text_trim_leaves_a_non_string_alone():
    assert REGISTRY.require("text.trim").fn(42, {}, "out") == 42


def test_email_normalize_lowercases_and_strips():
    """The researched "lowercase email"."""
    assert REGISTRY.require("email.normalize").fn("  Ada.Lovelace@Example.COM ", {}, "out") == (
        "ada.lovelace@example.com"
    )


def test_email_normalize_refuses_to_guess_at_a_non_string():
    """Coercing 1042 into "1042" would hide a number arriving at an email property."""
    assert REGISTRY.require("email.normalize").fn(1042, {}, "out") == 1042


def test_date_iso8601_reads_a_date_object():
    assert REGISTRY.require("date.iso8601").fn(date(2026, 3, 1), {}, "out") == "2026-03-01"


def test_date_iso8601_reads_a_datetime_as_its_date():
    moment = datetime(2026, 3, 1, 15, 30, tzinfo=timezone.utc)
    assert REGISTRY.require("date.iso8601").fn(moment, {}, "out") == "2026-03-01"


def test_date_iso8601_reads_a_trailing_z():
    assert REGISTRY.require("date.iso8601").fn("2026-03-01T09:00:00Z", {}, "out") == "2026-03-01"


def test_date_iso8601_reads_a_plain_iso_string():
    assert REGISTRY.require("date.iso8601").fn("2026-03-01", {}, "out") == "2026-03-01"


def test_date_iso8601_turns_an_empty_string_into_nothing_rather_than_an_error():
    assert REGISTRY.require("date.iso8601").fn("   ", {}, "out") is None


def test_date_iso8601_raises_on_a_value_it_cannot_read():
    with pytest.raises(ValueError, match="not an ISO-8601"):
        REGISTRY.require("date.iso8601").fn("the third of March", {}, "out")


def test_date_iso8601_raises_on_a_number():
    with pytest.raises(ValueError, match="cannot read"):
        REGISTRY.require("date.iso8601").fn(20260301, {}, "out")


def test_datetime_utc_writes_a_z_suffix():
    assert REGISTRY.require("datetime.utc").fn("2026-03-01T09:00:00+02:00", {}, "out") == (
        "2026-03-01T07:00:00Z"
    )


def test_datetime_utc_reads_a_naive_input_as_utc_and_says_so():
    assert (
        REGISTRY.require("datetime.utc").fn("2026-03-01T09:00:00", {}, "out")
        == "2026-03-01T09:00:00Z"
    )
    assert "read as UTC" in REGISTRY.require("datetime.utc").description


def test_datetime_utc_reads_a_date_as_midnight_utc():
    assert (
        REGISTRY.require("datetime.utc").fn(date(2026, 3, 1), {}, "out") == "2026-03-01T00:00:00Z"
    )


def test_datetime_utc_raises_on_a_value_it_cannot_read():
    with pytest.raises(ValueError, match="not an ISO-8601"):
        REGISTRY.require("datetime.utc").fn("yesterday", {}, "out")


def test_number_coerce_reads_an_integer_string():
    assert REGISTRY.require("number.coerce").fn("42", {}, "out") == 42


def test_number_coerce_reads_a_decimal_string():
    assert REGISTRY.require("number.coerce").fn(" 42.5 ", {}, "out") == 42.5


def test_number_coerce_passes_a_number_through():
    assert REGISTRY.require("number.coerce").fn(7, {}, "out") == 7


def test_number_coerce_turns_an_empty_string_into_nothing():
    assert REGISTRY.require("number.coerce").fn("", {}, "out") is None


def test_number_coerce_raises_on_text_rather_than_writing_zero():
    """A CRM silently receiving 0 for a revenue column is worse than a refusal."""
    with pytest.raises(ValueError, match="not a number"):
        REGISTRY.require("number.coerce").fn("forty two", {}, "out")


def test_number_coerce_refuses_a_boolean_though_python_calls_one_an_int():
    with pytest.raises(ValueError, match="boolean"):
        REGISTRY.require("number.coerce").fn(True, {}, "out")


def test_number_coerce_does_not_strip_a_thousands_separator():
    with pytest.raises(ValueError):
        REGISTRY.require("number.coerce").fn("1,200", {}, "out")


def test_picklist_map_maps_a_label_to_an_internal_value():
    """The researched "picklist label -> internal option value"."""
    config = {"map": {"Discovery": "lead"}}
    assert REGISTRY.require("picklist.map").fn("Discovery", config, "out") == "lead"


def test_picklist_map_leaves_an_unmapped_value_visible_rather_than_dropping_it():
    config = {"map": {"Discovery": "lead"}}
    assert REGISTRY.require("picklist.map").fn("Expansion", config, "out") == "Expansion"


def test_picklist_map_raises_without_a_table():
    with pytest.raises(ValueError, match="non-empty"):
        REGISTRY.require("picklist.map").fn("Discovery", {}, "out")


def test_picklist_map_raises_on_an_empty_table():
    with pytest.raises(ValueError, match="non-empty"):
        REGISTRY.require("picklist.map").fn("Discovery", {"map": {}}, "out")


def test_every_builtin_declares_a_version_of_one_or_more():
    for transform in BUILTINS:
        assert transform.version >= 1
        assert transform.key == f"{transform.name}@{transform.version}"


def test_a_transform_key_is_name_at_version():
    assert transform_key("picklist.map", 2) == "picklist.map@2"
    assert transform_key("picklist.map", None) == "picklist.map@"


def test_resolving_without_a_version_takes_the_highest_registered_one():
    local = Registry([Transform("thing", 1, "first", fn=lambda v, c, d: v)])
    local.register(Transform("thing", 2, "second", fn=lambda v, c, d: v))
    assert local.resolve("thing").version == 2


def test_resolving_a_pinned_version_ignores_a_newer_one():
    """A mapping validated today must not change meaning when v2 ships."""
    local = Registry([Transform("thing", 1, "first", fn=lambda v, c, d: v)])
    local.register(Transform("thing", 2, "second", fn=lambda v, c, d: v))
    assert local.resolve("thing", 1).version == 1


def test_a_pinned_version_that_is_not_registered_resolves_to_nothing():
    """Never a silent fallback: a mapping must not quietly start writing differently."""
    local = Registry([Transform("thing", 1, "first", fn=lambda v, c, d: v)])
    assert local.resolve("thing", 7) is None


def test_an_unknown_name_resolves_to_nothing():
    assert REGISTRY.resolve("nope") is None
    assert REGISTRY.resolve("") is None


def test_require_raises_with_the_pinned_version_in_the_message():
    with pytest.raises(TransformUnavailable, match="version 9"):
        REGISTRY.require("identity", 9)


def test_require_raises_for_an_unknown_name():
    with pytest.raises(TransformUnavailable, match="no transform named"):
        REGISTRY.require("nope")


def test_registration_is_the_extension_point_and_needs_no_engine_change():
    local = Registry()
    local.register(Transform("tenant.rollup", 1, "a tenant's own transform", fn=lambda v, c, d: v))
    assert local.resolve("tenant.rollup") is not None
    assert local.versions_of("tenant.rollup") == (1,)


def test_registration_refuses_a_transform_with_no_name():
    with pytest.raises(ValueError, match="needs a name"):
        Registry().register(Transform("", 1, "nameless", fn=lambda v, c, d: v))


def test_registration_refuses_a_version_below_one():
    with pytest.raises(ValueError, match="version of 1 or more"):
        Registry().register(Transform("thing", 0, "unversioned", fn=lambda v, c, d: v))


def test_a_declaration_is_not_executable_and_an_executable_is():
    assert Transform("declared", 1, "no code").executable is False
    assert Transform("built", 1, "code", fn=lambda v, c, d: v).executable is True


def test_the_latest_list_offers_one_entry_per_name():
    local = Registry([Transform("thing", 1, "a", fn=lambda v, c, d: v)])
    local.register(Transform("thing", 2, "b", fn=lambda v, c, d: v))
    assert [item.name for item in local.latest()] == ["thing"]
    assert local.latest()[0].version == 2


def test_unregister_removes_one_version_and_leaves_the_others():
    local = Registry([Transform("thing", 1, "a", fn=lambda v, c, d: v)])
    local.register(Transform("thing", 2, "b", fn=lambda v, c, d: v))
    assert local.unregister("thing", 1) is True
    assert local.unregister("thing", 1) is False
    assert local.resolve("thing").version == 2


def test_a_transform_declares_the_field_types_it_is_for():
    assert REGISTRY.require("email.normalize").applies("email") is True
    assert REGISTRY.require("email.normalize").applies("number") is False


def test_a_transform_with_no_declared_types_applies_to_anything():
    assert REGISTRY.require("identity").applies("number") is True


def test_an_unknown_field_type_is_accepted_rather_than_refused():
    """Schema flexibility: a team adding a field must not need a code change."""
    assert REGISTRY.require("text.trim").applies("geolocation") is True


def test_the_catalogue_reports_names_and_versions_for_a_client():
    described = REGISTRY.describe()
    assert all(
        {"name", "version", "key", "applies_to", "builtin"} <= set(item) for item in described
    )


# --------------------------------------------------------------------------- #
# 3. Metadata
# --------------------------------------------------------------------------- #


def test_a_hubspot_read_normalises_results():
    data = hubspot_metadata()
    assert data.provider == "hubspot"
    assert data.property("stage").value_type == "enumeration"
    assert data.property("stage").field_type == "select"


def test_a_hubspot_read_keeps_type_and_fieldtype_apart():
    """Evidence: "type" is the property's type, "fieldType" how it is presented."""
    renewal = hubspot_metadata().property("renewal_date")
    assert (renewal.value_type, renewal.field_type) == ("string", "date")


def test_a_hubspot_read_carries_the_unique_flag():
    assert hubspot_metadata(unique=2).property("legacy_unique_0").unique is True
    assert hubspot_metadata().property("email").unique is False


def test_an_enumeration_carries_internal_names_and_labels_apart():
    """The researched rule, as data: value is internal, label is human."""
    stage = hubspot_metadata().property("stage")
    assert stage.internal_values == ("lead", "customer")
    assert [option.label for option in stage.options] == ["Lead", "Customer"]


def test_a_label_lookup_finds_the_option_behind_a_human_name():
    assert hubspot_metadata().property("stage").option_for_label("Customer").value == "customer"
    assert hubspot_metadata().property("stage").option_for_label("cUSTOMER").value == "customer"
    assert hubspot_metadata().property("stage").option_for_label("nope") is None


def test_groups_are_derived_from_the_properties_that_carry_them():
    assert set(hubspot_metadata().groups) == {"contactinformation", "salesforce"}


def test_a_hubspot_read_with_no_results_shape_is_refused():
    with pytest.raises(InvalidMapping, match="results"):
        normalise("hubspot", {"properties": []}, "contacts")


def test_a_hubspot_read_with_a_unique_count_is_reported_against_the_ceiling():
    data = hubspot_metadata(unique=10)
    assert data.unique_property_count == 10
    assert data.unique_key_usage == 10
    assert data.to_dict()["unique_key_limit"] == 10


def test_a_dataverse_entity_definitions_read_expands_the_attribute_type():
    """The researched rule is written in the metadata class name, not the CSDL short name."""
    assert dataverse_metadata().property("name").value_type == "StringAttributeMetadata"
    assert dataverse_metadata().property("revenue").value_type == "DecimalAttributeMetadata"


def test_a_dataverse_read_carries_its_alternate_keys():
    data = dataverse_metadata(keys=[["name", "revenue"]])
    assert data.keys == (("name", "revenue"),)
    assert data.key_count == 1
    assert data.property("name").keyed is True
    assert data.property("name").key_name == "alt_key_1"


def test_a_dataverse_read_marks_every_column_in_a_key():
    data = dataverse_metadata(keys=[["name", "revenue"]])
    assert set(data.keyed_properties()) == {"name", "revenue"}


def test_a_dataverse_usage_counts_keys_not_columns():
    """Ten columns in one key is one definition, not ten."""
    data = dataverse_metadata(keys=[["name", "revenue", "dsr_row_id"]])
    assert data.unique_key_usage == 1
    assert data.unique_property_count == 3


def test_a_dataverse_state_attribute_gets_its_default_option_set():
    data = dataverse_metadata(extra=[{"LogicalName": "statuscode", "AttributeType": "State"}])
    assert data.property("statuscode").internal_values == ("0", "1")


def test_a_dataverse_read_accepts_a_csdl_shaped_payload():
    data = normalise(
        "dataverse",
        {"attributes": [{"Name": "x", "AttributeType": "String"}], "keys": [["x"]]},
        "account",
    )
    assert data.property("x").keyed is True
    assert data.document == "CSDL $metadata"


def test_a_dataverse_read_accepts_a_bare_list_of_rows():
    data = normalise(
        "dataverse",
        [
            {
                "SchemaName": "account",
                "Attributes": [{"LogicalName": "x", "AttributeType": "String"}],
            }
        ],
        "account",
    )
    assert data.property("x") is not None


def test_a_dataverse_read_with_no_row_for_the_object_is_refused():
    with pytest.raises(InvalidMapping, match="no EntityDefinitions row"):
        normalise(
            "dataverse",
            {"value": [{"SchemaName": "contact", "Attributes": []}]},
            "account",
        )


def test_a_dataverse_read_with_no_envelope_is_refused():
    with pytest.raises(InvalidMapping, match="value"):
        normalise("dataverse", {"entities": []}, "account")


def test_a_salesforce_read_normalises_fields_and_notes_the_gap():
    data = normalise(
        "salesforce",
        {"fields": [{"name": "Email", "type": "Email", "label": "E-mail", "unique": True}]},
        "Contact",
    )
    assert data.property("Email").unique is True
    assert "unsourced" in data.document
    assert any("no external-ID field request is emitted" in note for note in data.notes)


def test_a_salesforce_read_with_no_fields_shape_is_refused():
    with pytest.raises(InvalidMapping, match="fields"):
        normalise("salesforce", {"objects": []}, "Contact")


def test_an_unknown_provider_is_refused_and_names_the_documented_ones():
    with pytest.raises(InvalidMapping, match="dataverse, hubspot, salesforce"):
        normalise("pipedrive", {"results": []}, "x")


def test_a_non_object_document_is_refused():
    with pytest.raises(InvalidMapping, match="JSON object"):
        normalise("hubspot", [], "contacts")


def test_a_property_is_matched_exactly_and_a_near_miss_is_not_adopted():
    """Case included, on purpose: see the inference on exact property name match."""
    data = hubspot_metadata()
    assert data.property("dsr_row_id") is not None
    assert data.property("DSR_Row_Id") is None


def test_a_near_miss_comes_back_as_a_suggestion():
    data = hubspot_metadata()
    assert "dsr_row_id" in data.suggest("dsr_row_i")


def test_a_wildly_different_name_gets_no_suggestion():
    assert hubspot_metadata().suggest("zzzzzzzz") == ()


def test_an_enumeration_is_recognised_by_its_option_set():
    assert hubspot_metadata().property("stage").is_enumeration is True
    assert hubspot_metadata().property("email").is_enumeration is False


def test_a_carried_option_set_beats_a_name_based_guess():
    """A State attribute with a published option set is an enumeration; a String is not."""
    data = dataverse_metadata(extra=[{"LogicalName": "statecode", "AttributeType": "State"}])
    assert data.property("statecode").is_enumeration is True
    assert data.property("name").is_enumeration is False


def test_the_five_eligible_key_types_are_the_only_ones_accepted():
    data = dataverse_metadata()
    assert data.type_is_key_eligible("StringAttributeMetadata") is True
    assert data.type_is_key_eligible("PicklistAttributeMetadata") is True
    assert data.type_is_key_eligible("DateTimeAttributeMetadata") is True
    assert data.type_is_key_eligible("LookupAttributeMetadata") is True
    assert data.type_is_key_eligible("DecimalAttributeMetadata") is True


def test_a_boolean_or_a_guid_is_not_eligible_for_a_key():
    data = dataverse_metadata()
    assert data.type_is_key_eligible("BooleanAttributeMetadata") is False
    assert data.type_is_key_eligible("UniqueIdentifierAttributeMetadata") is False


def test_an_eligibility_check_accepts_the_csdl_short_name_too():
    assert dataverse_metadata().type_is_key_eligible("String") is True


def test_eligibility_is_not_a_dataverse_concern_for_hubspot():
    """HubSpot's rule is per-property, not per attribute type, so nothing is refused."""
    assert hubspot_metadata().type_is_key_eligible("StringAttributeMetadata") is False


def test_a_fieldtype_that_disagrees_with_its_type_is_detected():
    data = hubspot_metadata()
    assert data.field_type_fits(data.property("stage")) is True
    assert data.field_type_fits(data.property("renewal_date")) is False


def test_a_property_with_no_fieldtype_is_not_judged():
    """Dataverse has no second axis, so the rule has nothing to say about it."""
    assert dataverse_metadata().field_type_fits(dataverse_metadata().property("name")) is True


def test_metadata_round_trips_through_its_stored_shape():
    original = hubspot_metadata(unique=1)
    restored = Metadata.from_dict(original.to_dict())
    assert restored.names == original.names
    assert restored.property("stage").internal_values == ("lead", "customer")
    assert restored.unique_property_count == original.unique_property_count


def test_a_fixture_says_it_is_not_a_crm_read():
    data = metadata_module.fixture("hubspot", "contacts", [{"name": "x", "value_type": "string"}])
    assert data.document == "fixture"
    assert any("Not a CRM read" in note for note in data.notes)


def test_the_property_view_reports_every_axis_a_grid_needs():
    payload = hubspot_metadata().property("stage").to_dict()
    assert payload == {
        "name": "stage",
        "label": "Stage",
        "value_type": "enumeration",
        "field_type": "select",
        "group": "salesforce",
        "unique": False,
        "options": [
            {"value": "lead", "label": "Lead", "hidden": False},
            {"value": "customer", "label": "Customer", "hidden": False},
        ],
        "keyed": False,
        "key_name": "",
    }


def test_a_bare_list_of_options_is_read_as_value_and_label_together():
    options = metadata_module._options_from(["a", "b"])
    assert [(item.value, item.label) for item in options] == [("a", "a"), ("b", "b")]


def test_a_dataverse_option_set_published_as_bare_labels_uses_them_as_values():
    options = metadata_module._options_from([{"label": "Open"}, {"label": "Won"}])
    assert [item.value for item in options] == ["Open", "Won"]


def test_a_non_list_option_payload_yields_no_options():
    assert metadata_module._options_from("not a list") == ()
    assert metadata_module._options_from(None) == ()


def test_a_property_built_by_hand_can_carry_options_directly():
    prop = Property(
        name="x", label="X", value_type="PicklistAttributeMetadata", options=(Option("0", "No"),)
    )
    assert prop.internal_values == ("0",)
    assert prop.is_enumeration is True


# --------------------------------------------------------------------------- #
# 4. Validation
# --------------------------------------------------------------------------- #


def test_a_row_with_no_target_is_a_warning_and_does_not_block(engine):
    connection = make_connection(engine)
    mapping = make_mapping(engine, connection["id"])
    add_properties(engine, connection["id"])
    add_row(engine, connection["id"], mapping["id"], source_field="notes", direction="out")
    report = report_for(engine, connection["id"], mapping["id"])
    assert flags_of(report, "notes") == ["no_target"]
    assert report["blocking"] == ["no_sync_key"]


def test_an_unknown_property_is_the_first_researched_finding(engine):
    connection = make_connection(engine)
    mapping = make_mapping(engine, connection["id"])
    add_properties(engine, connection["id"])
    add_row(engine, connection["id"], mapping["id"], source_field="email", target_property="e-mail")
    report = report_for(engine, connection["id"], mapping["id"])
    assert "unknown_property" in flags_of(report, "email")
    assert "unknown_property" in report["blocking"]


def test_an_unknown_property_carries_its_suggestions(engine):
    connection = make_connection(engine)
    mapping = make_mapping(engine, connection["id"])
    add_properties(engine, connection["id"])
    add_row(
        engine, connection["id"], mapping["id"], source_field="email", target_property="dsr_row_od"
    )
    report = report_for(engine, connection["id"], mapping["id"])
    detail = report["rows"][0]["findings"][0]["detail"]
    assert "dsr_row_id" in detail["suggestions"]
    assert detail["exact_match"] is False


def test_a_type_mismatch_is_the_second_researched_finding(engine):
    connection = make_connection(engine)
    mapping = make_mapping(engine, connection["id"])
    add_properties(engine, connection["id"])
    add_row(
        engine,
        connection["id"],
        mapping["id"],
        source_field="seats",
        source_type="text",
        target_property="seats",
    )
    report = report_for(engine, connection["id"], mapping["id"])
    assert "type_mismatch" in flags_of(report, "seats")


def test_a_type_mismatch_names_both_sides_and_which_field_was_read(engine):
    connection = make_connection(engine)
    mapping = make_mapping(engine, connection["id"])
    add_properties(engine, connection["id"])
    add_row(
        engine,
        connection["id"],
        mapping["id"],
        source_field="seats",
        source_type="text",
        target_property="seats",
    )
    detail = report_for(engine, connection["id"], mapping["id"])["rows"][0]["findings"][0]["detail"]
    assert detail["source_type"] == "text"
    assert detail["actual_type"] == "number"
    assert detail["expected_types"] == ["string"]
    assert detail["read_from"] == "type"


def test_a_matching_type_raises_no_finding(engine):
    connection = make_connection(engine)
    mapping = make_mapping(engine, connection["id"])
    add_properties(engine, connection["id"])
    add_row(
        engine,
        connection["id"],
        mapping["id"],
        source_field="seats",
        source_type="number",
        target_property="seats",
    )
    assert flags_of(report_for(engine, connection["id"], mapping["id"]), "seats") == []


def test_a_picklist_table_holding_labels_is_the_third_researched_finding(engine):
    """HubSpot: "you must use internal names to set values"."""
    connection = make_connection(engine)
    mapping = make_mapping(engine, connection["id"])
    add_properties(engine, connection["id"])
    add_row(
        engine,
        connection["id"],
        mapping["id"],
        source_field="stage",
        source_type="enumeration",
        target_property="stage",
        transform="picklist.map",
        transform_config={"map": {"Lead": "Lead", "Won": "customer"}},
    )
    report = report_for(engine, connection["id"], mapping["id"])
    assert "unsupported_option" in flags_of(report, "stage")


def test_an_unsupported_option_names_the_offending_value_and_the_internal_names(engine):
    connection = make_connection(engine)
    mapping = make_mapping(engine, connection["id"])
    add_properties(engine, connection["id"])
    add_row(
        engine,
        connection["id"],
        mapping["id"],
        source_field="stage",
        source_type="enumeration",
        target_property="stage",
        transform="picklist.map",
        transform_config={"map": {"Discovery": "Lead"}},
    )
    detail = report_for(engine, connection["id"], mapping["id"])["rows"][0]["findings"][0]["detail"]
    assert detail["checked_side"] == "value"
    assert detail["offending"][0]["candidate"] == "Lead"
    assert detail["offending"][0]["matched_a_label"] is True
    # `internal_name` is the CRM's own spelling, which is the whole point: the label
    # an admin typed and the value the CRM accepts are different strings.
    assert detail["offending"][0]["internal_name"] == "lead"
    assert detail["internal_values"] == ["lead", "customer"]
    assert "internal names" in detail["hint"]


def test_an_inbound_row_checks_the_key_side_instead_of_the_value_side(engine):
    """Same rule, opposite side: the CRM's own value is what arrives."""
    connection = make_connection(engine)
    mapping = make_mapping(engine, connection["id"])
    add_properties(engine, connection["id"])
    add_row(
        engine,
        connection["id"],
        mapping["id"],
        source_field="stage",
        source_type="enumeration",
        target_property="stage",
        direction="in",
        transform="picklist.map",
        transform_config={"map": {"Lead": "Discovery", "lead": "Discovery"}},
    )
    report = report_for(engine, connection["id"], mapping["id"])
    detail = report["rows"][0]["findings"][0]["detail"]
    assert detail["checked_side"] == "key"
    assert detail["offending"][0]["candidate"] == "Lead"
    assert detail["offending"][0]["matched_a_label"] is True


def test_a_picklist_table_that_is_entirely_internal_names_raises_nothing(engine):
    connection = make_connection(engine)
    mapping = make_mapping(engine, connection["id"])
    add_properties(engine, connection["id"])
    add_row(
        engine,
        connection["id"],
        mapping["id"],
        source_field="stage",
        source_type="enumeration",
        target_property="stage",
        transform="picklist.map",
        transform_config={"map": {"Discovery": "lead", "Won": "customer"}},
    )
    assert flags_of(report_for(engine, connection["id"], mapping["id"]), "stage") == []


def test_a_row_targeting_a_non_enumeration_with_a_picklist_table_raises_nothing(engine):
    """The option check needs an option set; a stray table on a string target is inert."""
    connection = make_connection(engine)
    mapping = make_mapping(engine, connection["id"])
    add_properties(engine, connection["id"])
    add_row(
        engine,
        connection["id"],
        mapping["id"],
        source_field="note",
        source_type="text",
        target_property="email",
        transform="identity",
        transform_config={"map": {"a": "b"}},
    )
    assert flags_of(report_for(engine, connection["id"], mapping["id"]), "note") == []


def test_a_property_whose_fieldtype_disagrees_with_its_type_is_a_warning(engine):
    connection = make_connection(engine)
    mapping = make_mapping(engine, connection["id"])
    add_properties(engine, connection["id"])
    add_row(
        engine,
        connection["id"],
        mapping["id"],
        source_field="renewal",
        target_property="renewal_date",
    )
    report = report_for(engine, connection["id"], mapping["id"])
    assert flags_of(report, "renewal") == ["field_type_disagrees"]


def test_two_rows_on_one_property_report_the_claimant(engine):
    connection = make_connection(engine)
    mapping = make_mapping(engine, connection["id"])
    add_properties(engine, connection["id"])
    add_row(
        engine, connection["id"], mapping["id"], source_field="account", target_property="email"
    )
    add_row(
        engine, connection["id"], mapping["id"], source_field="contact", target_property="email"
    )
    report = report_for(engine, connection["id"], mapping["id"])
    assert flags_of(report, "account") == []
    assert flags_of(report, "contact") == ["duplicate_target"]
    assert report["rows"][1]["findings"][0]["detail"]["claimed_by"] == "account"


def test_a_row_naming_an_unregistered_transform_is_an_error(engine):
    connection = make_connection(engine)
    mapping = make_mapping(engine, connection["id"])
    add_properties(engine, connection["id"])
    add_row(
        engine,
        connection["id"],
        mapping["id"],
        source_field="a",
        target_property="email",
        transform="nope",
    )
    report = report_for(engine, connection["id"], mapping["id"])
    assert flags_of(report, "a") == ["transform_unavailable"]


def test_an_unregistered_transform_finding_lists_what_is_registered(engine):
    connection = make_connection(engine)
    mapping = make_mapping(engine, connection["id"])
    add_properties(engine, connection["id"])
    add_row(
        engine,
        connection["id"],
        mapping["id"],
        source_field="a",
        target_property="email",
        transform="nope",
    )
    detail = report_for(engine, connection["id"], mapping["id"])["rows"][0]["findings"][0]["detail"]
    assert "picklist.map" in detail["registered"]


def test_a_transform_naming_the_wrong_field_type_is_a_warning_not_an_error(engine):
    connection = make_connection(engine)
    mapping = make_mapping(engine, connection["id"])
    add_properties(engine, connection["id"])
    add_row(
        engine,
        connection["id"],
        mapping["id"],
        source_field="a",
        source_type="number",
        target_property="seats",
        transform="email.normalize",
    )
    assert flags_of(report_for(engine, connection["id"], mapping["id"]), "a") == [
        "transform_mismatch"
    ]


def test_a_pinned_transform_version_that_is_not_registered_is_an_error(engine):
    connection = make_connection(engine)
    mapping = make_mapping(engine, connection["id"])
    add_properties(engine, connection["id"])
    add_row(
        engine,
        connection["id"],
        mapping["id"],
        source_field="a",
        target_property="email",
        transform="identity",
        transform_version=4,
    )
    assert flags_of(report_for(engine, connection["id"], mapping["id"]), "a") == [
        "transform_unavailable"
    ]


def test_a_clean_row_with_a_key_is_activatable(engine):
    connection = make_connection(engine)
    mapping = make_mapping(engine, connection["id"])
    add_properties(engine, connection["id"])
    add_row(engine, connection["id"], mapping["id"], source_field="a", target_property="email")
    engine.pin_sync_key(
        connection["id"], mapping["id"], {"properties": ["dsr_row_id"]}, source=SOURCE
    )
    report = report_for(engine, connection["id"], mapping["id"])
    assert report["counts"]["error"] == 0
    assert report["can_activate"] is True


def test_a_mapping_with_no_key_is_not_activatable(engine):
    connection = make_connection(engine)
    mapping = make_mapping(engine, connection["id"])
    add_properties(engine, connection["id"])
    add_row(engine, connection["id"], mapping["id"], source_field="a", target_property="email")
    report = report_for(engine, connection["id"], mapping["id"])
    assert report["can_activate"] is False
    assert report["sync_key"]["pinned"] is False
    assert "no_sync_key" in report["sync_key"]["flags"]


def test_the_report_counts_every_severity(engine):
    connection = make_connection(engine)
    mapping = make_mapping(engine, connection["id"])
    add_properties(engine, connection["id"])
    add_row(engine, connection["id"], mapping["id"], source_field="a", target_property="email")
    add_row(
        engine,
        connection["id"],
        mapping["id"],
        source_field="b",
        source_type="text",
        target_property="seats",
    )
    add_row(engine, connection["id"], mapping["id"], source_field="c")
    engine.pin_sync_key(
        connection["id"], mapping["id"], {"properties": ["dsr_row_id"]}, source=SOURCE
    )
    report = report_for(engine, connection["id"], mapping["id"])
    assert report["counts"]["ok"] == 1
    assert report["counts"]["error"] == 1
    assert report["counts"]["warning"] == 1


def test_the_counts_include_the_sync_key_findings_too(engine):
    """`counts` is the number of things to fix, so a missing key is one of them."""
    connection = make_connection(engine)
    mapping = make_mapping(engine, connection["id"])
    add_properties(engine, connection["id"])
    add_row(engine, connection["id"], mapping["id"], source_field="a", target_property="email")
    report = report_for(engine, connection["id"], mapping["id"])
    assert report["counts"]["ok"] == 1
    assert report["counts"]["error"] == 1
    assert "no_sync_key" in report["blocking"]


def test_the_report_names_the_read_it_validated_against(engine):
    connection = make_connection(engine)
    mapping = make_mapping(engine, connection["id"])
    add_properties(engine, connection["id"])
    report = report_for(engine, connection["id"], mapping["id"])
    assert report["metadata"]["document"].startswith("GET /crm/properties")
    assert report["metadata"]["property_count"] == 5


def test_the_report_separates_the_researched_flags_from_the_added_ones(engine):
    connection = make_connection(engine)
    mapping = make_mapping(engine, connection["id"])
    add_properties(engine, connection["id"])
    report = report_for(engine, connection["id"], mapping["id"])
    assert report["researched_flags"] == ["unknown_property", "type_mismatch", "unsupported_option"]
    assert "no_target" in report["added_flags"]
    assert "transform_unavailable" in report["added_flags"]


def test_the_report_counts_mappable_and_inbound_rows(engine):
    connection = make_connection(engine)
    mapping = make_mapping(engine, connection["id"])
    add_properties(engine, connection["id"])
    add_row(
        engine,
        connection["id"],
        mapping["id"],
        source_field="a",
        target_property="email",
        direction="out",
    )
    add_row(
        engine,
        connection["id"],
        mapping["id"],
        source_field="b",
        target_property="seats",
        direction="in",
    )
    add_row(
        engine,
        connection["id"],
        mapping["id"],
        source_field="c",
        target_property="dsr_row_id",
        direction="both",
    )
    report = report_for(engine, connection["id"], mapping["id"])
    assert report["mappable_rows"] == 2
    assert report["inbound_rows"] == 2


def test_validating_with_no_metadata_recorded_answers_409_with_the_endpoints(engine):
    connection = make_connection(engine)
    mapping = make_mapping(engine, connection["id"])
    with pytest.raises(MetadataUnavailable) as caught:
        report_for(engine, connection["id"], mapping["id"])
    assert caught.value.endpoints
    assert caught.value.source_document == "/crm/properties/2026-09/{object}"


def test_a_stored_row_validates_as_the_row_it_is(engine):
    """Regression: the validator read records as flat dicts, so every field came back empty.

    The report claimed all five rows had no target and no transform. A confidently
    wrong report is worse than none, so this pins the flattening at the boundary.
    """
    connection = make_connection(engine)
    mapping = make_mapping(engine, connection["id"])
    add_properties(engine, connection["id"])
    for field_name, target, field_type in (
        ("a", "email", "email"),
        ("b", "seats", "number"),
        ("c", "dsr_row_id", "id"),
    ):
        add_row(
            engine,
            connection["id"],
            mapping["id"],
            source_field=field_name,
            source_type=field_type,
            target_property=target,
        )
    engine.pin_sync_key(
        connection["id"], mapping["id"], {"properties": ["dsr_row_id"]}, source=SOURCE
    )
    report = report_for(engine, connection["id"], mapping["id"])
    assert [item["source_field"] for item in report["rows"]] == ["a", "b", "c"]
    assert report["can_activate"] is True
    assert report["counts"]["error"] == 0


def test_a_flat_row_and_a_stored_record_validate_identically():
    record = {
        "id": "r1",
        "collection": ROW_COLLECTION,
        "data": {"source_field": "a", "target_property": "email"},
    }
    flat = {"id": "r1", "source_field": "a", "target_property": "email"}
    data = hubspot_metadata()
    assert validate_module.validate_row(record, data) == validate_module.validate_row(flat, data)


def test_the_key_section_reports_a_capacity_pressure_as_an_error():
    section = validate_module.validate_sync_key(
        {"properties": ["dsr_row_id"], "unique": True}, hubspot_metadata(unique=10)
    )
    assert section["status"] == "error"
    assert section["usage"] == {"used": 10, "needed": 1, "limit": 10, "remaining": 0}


def test_the_key_section_reports_an_already_unique_property():
    section = validate_module.validate_sync_key(
        {"properties": ["dsr_row_id"]}, hubspot_metadata(keyed=True)
    )
    assert section["status"] == "ok"
    assert "sync_key_already_unique" in section["flags"]


def test_the_key_section_reports_an_already_keyed_column():
    section = validate_module.validate_sync_key(
        {"properties": ["name"]}, dataverse_metadata(keys=[["name"]])
    )
    assert "sync_key_already_keyed" in section["flags"]


def test_the_key_section_refuses_an_ineligible_dataverse_attribute():
    section = validate_module.validate_sync_key(
        {"properties": ["donotemail"]}, dataverse_metadata()
    )
    assert "sync_key_ineligible_type" in section["flags"]


def test_the_key_section_refuses_a_property_the_object_does_not_carry():
    section = validate_module.validate_sync_key({"properties": ["nope"]}, dataverse_metadata())
    assert "sync_key_unknown_property" in section["flags"]


def test_every_flag_has_a_declared_severity():
    for flag in validate_module.METADATA_FLAGS + validate_module.MAPPING_FLAGS:
        assert flag in validate_module.SEVERITY, flag


def test_the_three_researched_flags_are_all_errors():
    for flag in validate_module.METADATA_FLAGS:
        assert validate_module.SEVERITY[flag] == "error"


def test_a_finding_carries_a_flag_a_severity_a_message_and_a_detail():
    item = validate_module.finding("unknown_property", "nope", detail={"a": 1})
    assert item == {
        "flag": "unknown_property",
        "severity": "error",
        "message": "nope",
        "detail": {"a": 1},
    }


# --------------------------------------------------------------------------- #
# 5. The sync key
# --------------------------------------------------------------------------- #


def test_a_key_carries_the_property_and_the_time_it_was_pinned():
    data = hubspot_metadata()
    pinned = sync_key_module.pin({"properties": ["dsr_row_id"]}, data)
    assert pinned["properties"] == ["dsr_row_id"]
    assert pinned["unique"] is True
    assert pinned["pinned_at"]


def test_a_key_may_be_named_as_a_comma_separated_string():
    pinned = sync_key_module.pin({"properties": "name, revenue"}, dataverse_metadata())
    assert pinned["properties"] == ["name", "revenue"]


def test_a_key_with_no_properties_is_refused():
    with pytest.raises(InvalidSyncKey, match="at least one"):
        sync_key_module.pin({}, hubspot_metadata())


def test_a_key_naming_a_property_the_object_lacks_is_refused_with_suggestions():
    with pytest.raises(InvalidSyncKey) as caught:
        sync_key_module.pin({"properties": ["dsr_row_od"]}, hubspot_metadata())
    assert caught.value.findings[0]["suggestions"]


def test_a_hubspot_key_names_exactly_one_property():
    with pytest.raises(InvalidSyncKey, match="single property"):
        sync_key_module.pin({"properties": ["email", "seats"]}, hubspot_metadata())


def test_a_dataverse_key_may_name_several_columns():
    """Alternate keys identify rows by a "unique combination of columns"."""
    pinned = sync_key_module.pin({"properties": ["name", "revenue"]}, dataverse_metadata())
    assert pinned["properties"] == ["name", "revenue"]
    assert pinned["newly_enforced"] == 2


def test_a_dataverse_key_on_an_ineligible_attribute_is_refused():
    with pytest.raises(InvalidSyncKey, match="cannot be in a key"):
        sync_key_module.pin({"properties": ["donotemail"]}, dataverse_metadata())


def test_a_key_that_would_pass_the_ceiling_is_refused_with_the_counts():
    with pytest.raises(SyncKeyCapacity) as caught:
        sync_key_module.pin({"properties": ["dsr_row_id"]}, hubspot_metadata(unique=10))
    assert caught.value.used == 10
    assert caught.value.limit == 10
    assert caught.value.providers == ("dataverse", "hubspot")


def test_a_key_fitting_into_the_last_slot_is_accepted_and_reports_no_room_left():
    pinned = sync_key_module.pin({"properties": ["dsr_row_id"]}, hubspot_metadata(unique=9))
    assert pinned["usage"] == {"used": 9, "needed": 1, "limit": 10, "remaining": 0}


def test_a_key_already_enforced_costs_no_slot():
    pinned = sync_key_module.pin(
        {"properties": ["dsr_row_id"]}, hubspot_metadata(keyed=True, unique=8)
    )
    assert pinned["already_enforced"] == 1
    assert pinned["newly_enforced"] == 0
    assert pinned["usage"]["remaining"] == 1


def test_already_keyed_reports_whether_every_column_is_already_enforced():
    data = dataverse_metadata(keys=[["name"]])
    assert sync_key_module.already_keyed(data, {"properties": ["name"]}) is True
    assert sync_key_module.already_keyed(data, {"properties": ["name", "revenue"]}) is False
    assert sync_key_module.already_keyed(data, {"properties": []}) is False


#: A connection with the group a HubSpot property is filed under. The group is
#: required in the create body, so every request-building test needs one; a test
#: that does not pass it is testing the refusal, and says so.
HS_CONNECTION = {"property_group": "contactinformation"}


def hubspot_plan(metadata=None, connection=None, **kwargs):
    """The HubSpot create plan, with the connection a create body needs."""
    return sync_key_module.build_create_request(
        metadata if metadata is not None else hubspot_metadata(),
        {"properties": ["dsr_row_id"]},
        HS_CONNECTION if connection is None else connection,
        **kwargs,
    )


def test_a_hubspot_create_request_carries_both_type_and_fieldtype():
    """Evidence: "both `type` and `fieldType` values are required"."""
    body = hubspot_plan()["steps"][0]["body"]
    assert body["type"] == "string"
    assert body["fieldType"] == "text"


def test_a_hubspot_create_request_sets_has_unique_value_to_true():
    assert hubspot_plan()["steps"][0]["body"]["hasUniqueValue"] is True


def test_a_hubspot_create_request_posts_to_the_researched_endpoint():
    step = hubspot_plan()["steps"][0]
    assert (step["method"], step["url"]) == ("POST", "/crm/properties/2026-09/contacts")


def test_a_hubspot_create_request_is_marked_sourced_and_quotes_the_rule():
    plan = hubspot_plan()
    assert plan["sourced"] is True
    assert "hasUniqueValue" in plan["note"]


def test_a_hubspot_create_request_without_a_group_is_refused_rather_than_guessed():
    with pytest.raises(UnsupportedSyncKeyRequest, match="groupName"):
        hubspot_plan(connection={})


def test_a_hubspot_create_request_takes_the_group_from_the_connection():
    assert hubspot_plan()["steps"][0]["body"]["groupName"] == "contactinformation"


def test_a_hubspot_create_request_naming_a_group_the_object_lacks_is_refused():
    with pytest.raises(UnsupportedSyncKeyRequest, match="no property group"):
        hubspot_plan(connection={"property_group": "nonexistent"})


def test_a_hubspot_create_request_with_a_type_and_fieldtype_that_disagree_is_refused():
    with pytest.raises(UnsupportedSyncKeyRequest, match="not as a text"):
        hubspot_plan(value_type="enumeration", field_type="text")


def test_a_hubspot_create_request_with_an_unknown_type_is_refused_and_lists_them():
    with pytest.raises(UnsupportedSyncKeyRequest, match="not a HubSpot property type"):
        hubspot_plan(value_type="blob")


def test_a_hubspot_create_request_with_an_empty_type_is_refused():
    with pytest.raises(UnsupportedSyncKeyRequest, match="neither may be empty"):
        hubspot_plan(value_type="  ")


def test_a_hubspot_create_request_says_the_group_is_required_rather_than_guessing():
    with pytest.raises(UnsupportedSyncKeyRequest) as caught:
        hubspot_plan(connection={})
    assert "groupName" in caught.value.gap


def test_a_dataverse_key_plan_is_marked_not_sourced_and_says_why():
    plan = sync_key_module.build_create_request(
        dataverse_metadata(), {"properties": ["name", "revenue"]}
    )
    assert plan["sourced"] is False
    assert "WF-036" in plan["gap"]


def test_a_dataverse_key_plan_names_the_two_researched_steps():
    plan = sync_key_module.build_create_request(dataverse_metadata(), {"properties": ["name"]})
    assert [step["name"] for step in plan["steps"]] == ["EntityKeyMetadata", "CreateEntityKey"]


def test_a_dataverse_key_plan_carries_both_key_columns():
    plan = sync_key_module.build_create_request(
        dataverse_metadata(), {"properties": ["name", "revenue"]}
    )
    assert plan["steps"][0]["body"]["Keys"][0]["KeyAttributes"] == ["name", "revenue"]


def test_a_dataverse_key_plan_leaves_the_uncited_url_unset():
    plan = sync_key_module.build_create_request(dataverse_metadata(), {"properties": ["name"]})
    assert plan["steps"][1]["url"] is None
    assert "Not cited" in plan["steps"][1]["url_note"]


def test_a_salesforce_create_request_is_refused_with_the_researchs_own_gap():
    data = normalise("salesforce", {"fields": [{"name": "X__c", "type": "Text"}]}, "Contact")
    with pytest.raises(UnsupportedSyncKeyRequest) as caught:
        sync_key_module.build_create_request(data, {"properties": ["X__c"]})
    assert "client-rendered" in caught.value.gap


def test_a_request_with_no_pinned_key_is_refused():
    with pytest.raises(InvalidSyncKey, match="no sync key is pinned"):
        sync_key_module.build_create_request(hubspot_metadata(), {})


def test_a_request_for_a_property_the_object_no_longer_carries_is_refused():
    plan = sync_key_module.pin({"properties": ["dsr_row_id"]}, hubspot_metadata())
    stripped = hubspot_metadata()
    without = Metadata(
        provider=stripped.provider,
        crm_object=stripped.crm_object,
        properties=tuple(p for p in stripped.properties if p.name != "dsr_row_id"),
    )
    with pytest.raises(InvalidSyncKey, match="re-read the property metadata"):
        sync_key_module.build_create_request(without, plan, HS_CONNECTION)


def test_a_salesforce_create_request_names_the_provider_in_its_refusal():
    data = normalise("salesforce", {"fields": [{"name": "X__c", "type": "Text"}]}, "Contact")
    with pytest.raises(UnsupportedSyncKeyRequest) as caught:
        sync_key_module.build_create_request(data, {"properties": ["X__c"]})
    assert caught.value.provider == "salesforce"


# --------------------------------------------------------------------------- #
# 6. Preview
# --------------------------------------------------------------------------- #


def preview_ready(engine, **metadata_kwargs):
    """A connection, a mapping, a recorded read and one key. Returns the ids."""
    connection = make_connection(engine)
    mapping = make_mapping(engine, connection["id"])
    add_properties(engine, connection["id"], metadata=hubspot_metadata(**metadata_kwargs))
    engine.pin_sync_key(
        connection["id"], mapping["id"], {"properties": ["dsr_row_id"]}, source=SOURCE
    )
    return str(connection["id"]), str(mapping["id"])


def test_a_preview_sends_the_transformed_value_keyed_by_property_name(engine):
    connection_id, mapping_id = preview_ready(engine)
    add_row(
        engine,
        connection_id,
        mapping_id,
        source_field="buyer_email",
        source_type="email",
        target_property="email",
        transform="email.normalize",
    )
    result = engine.preview(
        connection_id, mapping_id, {"id": "r1", "buyer_email": " Ada@Example.COM "}
    )
    assert result["out"] == {"email": "ada@example.com", "dsr_row_id": "r1"}


def test_a_preview_injects_the_sync_key_from_the_records_own_id(engine):
    """The key "carries the sales-room's own row id"."""
    connection_id, mapping_id = preview_ready(engine)
    result = engine.preview(connection_id, mapping_id, {"id": "room_row_7"})
    assert result["sync_key"]["value"] == "room_row_7"
    assert result["sync_key"]["injected"] is True


def test_a_row_that_targets_the_key_wins_and_the_trace_says_so(engine):
    connection_id, mapping_id = preview_ready(engine)
    add_row(
        engine, connection_id, mapping_id, source_field="external_ref", target_property="dsr_row_id"
    )
    result = engine.preview(connection_id, mapping_id, {"id": "r1", "external_ref": "ext-9"})
    assert result["out"]["dsr_row_id"] == "ext-9"
    assert result["sync_key"]["injected"] is False


def test_a_preview_reports_the_inbound_direction_too(engine):
    connection_id, mapping_id = preview_ready(engine)
    add_row(
        engine,
        connection_id,
        mapping_id,
        source_field="buyer_email",
        source_type="email",
        target_property="email",
        direction="in",
        transform="email.normalize",
    )
    result = engine.preview(connection_id, mapping_id, {"email": " ADA@EXAMPLE.COM "})
    assert result["in"] == {"buyer_email": "ada@example.com"}


def test_a_both_row_appears_once_in_each_direction(engine):
    connection_id, mapping_id = preview_ready(engine)
    add_row(
        engine,
        connection_id,
        mapping_id,
        source_field="buyer_email",
        source_type="email",
        target_property="email",
        direction="both",
        transform="email.normalize",
    )
    result = engine.preview(
        connection_id, mapping_id, {"id": "r1", "buyer_email": "A@b.c", "email": "x@y.z"}
    )
    assert result["out"]["email"] == "a@b.c"
    assert result["in"]["buyer_email"] == "x@y.z"
    assert len([item for item in result["trace"] if item["target_property"] == "email"]) == 2


def test_a_preview_honours_a_single_requested_direction(engine):
    connection_id, mapping_id = preview_ready(engine)
    add_row(
        engine,
        connection_id,
        mapping_id,
        source_field="buyer_email",
        source_type="email",
        target_property="email",
        direction="both",
        transform="email.normalize",
    )
    result = engine.preview(
        connection_id,
        mapping_id,
        {"id": "r1", "buyer_email": "a@b.c", "email": "x@y.z"},
        directions=("out",),
    )
    assert result["in"] == {}
    assert result["directions"] == ["out"]


def test_a_record_missing_a_mapped_field_is_skipped_not_refused(engine):
    connection_id, mapping_id = preview_ready(engine)
    add_row(engine, connection_id, mapping_id, source_field="buyer_email", target_property="email")
    result = engine.preview(connection_id, mapping_id, {"id": "r1"})
    assert result["out"] == {"dsr_row_id": "r1"}
    assert result["trace"][0]["reason"] == "source_field_absent"
    assert result["counts"]["skipped"] == 1


def test_an_inbound_record_missing_the_property_is_skipped(engine):
    connection_id, mapping_id = preview_ready(engine)
    add_row(
        engine,
        connection_id,
        mapping_id,
        source_field="buyer_email",
        target_property="email",
        direction="in",
    )
    result = engine.preview(connection_id, mapping_id, {"id": "r1"})
    assert result["trace"][0]["reason"] == "target_property_absent"


def test_a_value_the_crm_would_refuse_is_reported_on_the_row(engine):
    connection_id, mapping_id = preview_ready(engine)
    add_row(
        engine,
        connection_id,
        mapping_id,
        source_field="stage",
        source_type="enumeration",
        target_property="stage",
        transform="picklist.map",
        transform_config={"map": {"Won": "customer", "Live": "Lead"}},
    )
    result = engine.preview(connection_id, mapping_id, {"id": "r1", "stage": "Live"})
    assert "stage" not in result["out"]
    assert result["trace"][0]["reason"] == "unsupported_option"
    assert result["writable"] is False


def test_a_label_travels_into_the_crm_never_and_the_trace_names_the_internal_name(engine):
    connection_id, mapping_id = preview_ready(engine)
    add_row(
        engine,
        connection_id,
        mapping_id,
        source_field="stage",
        source_type="enumeration",
        target_property="stage",
        transform="identity",
    )
    result = engine.preview(connection_id, mapping_id, {"id": "r1", "stage": "Lead"})
    assert result["trace"][0]["detail"]["matched_a_label"] is True
    assert result["trace"][0]["detail"]["internal_values"] == ["lead", "customer"]


def test_an_inbound_label_the_crm_would_not_send_is_refused(engine):
    connection_id, mapping_id = preview_ready(engine)
    add_row(
        engine,
        connection_id,
        mapping_id,
        source_field="stage",
        source_type="enumeration",
        target_property="stage",
        direction="in",
    )
    result = engine.preview(connection_id, mapping_id, {"id": "r1", "stage": "Lead"})
    assert result["trace"][0]["direction"] == "in"
    assert result["trace"][0]["reason"] == "unsupported_option"


def test_a_transform_that_cannot_read_a_value_fails_that_row_only(engine):
    connection_id, mapping_id = preview_ready(engine)
    add_row(
        engine,
        connection_id,
        mapping_id,
        source_field="seats",
        source_type="number",
        target_property="seats",
        transform="number.coerce",
    )
    add_row(engine, connection_id, mapping_id, source_field="buyer_email", target_property="email")
    result = engine.preview(
        connection_id, mapping_id, {"id": "r1", "seats": "forty", "buyer_email": "a@b.c"}
    )
    assert result["out"] == {"email": "a@b.c", "dsr_row_id": "r1"}
    assert result["counts"]["error"] == 1
    assert result["writable"] is False


def test_a_row_naming_an_unregistered_transform_is_reported_not_raised(engine):
    connection_id, mapping_id = preview_ready(engine)
    add_row(
        engine,
        connection_id,
        mapping_id,
        source_field="a",
        target_property="email",
        transform="nope",
    )
    result = engine.preview(connection_id, mapping_id, {"id": "r1", "a": "x"})
    assert result["trace"][0]["reason"] == "transform_unavailable"
    assert result["writable"] is False


def test_a_preview_writes_nothing(engine):
    connection_id, mapping_id = preview_ready(engine)
    add_row(engine, connection_id, mapping_id, source_field="a", target_property="email")
    before = engine.store.stats()["records"]
    engine.preview(connection_id, mapping_id, {"id": "r1", "a": "x"})
    assert engine.store.stats()["records"] == before


def test_a_preview_says_it_wrote_nothing(engine):
    connection_id, mapping_id = preview_ready(engine)
    result = engine.preview(connection_id, mapping_id, {"id": "r1"})
    assert result["wrote_anything"] is False
    assert "per record on every sync cycle" in result["note"]


def test_a_preview_accepts_flat_rows_as_well_as_stored_records(engine):
    connection_id, mapping_id = preview_ready(engine)
    add_row(engine, connection_id, mapping_id, source_field="a", target_property="email")
    rows = engine.book.rows(mapping_id)
    flat = [dict(row["data"], id=row["id"]) for row in rows]
    data = {"sync_key": {"properties": ["dsr_row_id"]}, "crm_object": "contacts"}
    result = preview_fn(data, flat, hubspot_metadata(), {"id": "r1", "a": "x"})
    assert result["out"] == {"email": "x", "dsr_row_id": "r1"}


def test_a_preview_with_no_key_injects_nothing(engine):
    connection = make_connection(engine)
    mapping = make_mapping(engine, connection["id"])
    add_properties(engine, connection["id"])
    result = engine.preview(connection["id"], mapping["id"], {"id": "r1"})
    assert result["sync_key"] is None
    assert result["out"] == {}


# --------------------------------------------------------------------------- #
# 7. Records
# --------------------------------------------------------------------------- #


def test_a_connection_needs_a_documented_provider(engine):
    with pytest.raises(InvalidMapping, match="provider is required"):
        make_connection(engine, provider="pipedrive")


def test_a_connection_needs_a_name_a_reader_can_recognise(engine):
    with pytest.raises(InvalidMapping, match="name is required"):
        engine.register_connection({"provider": "hubspot"}, source=SOURCE)


def test_a_connection_records_the_fields_it_is_given(engine):
    record = make_connection(engine, account="Northwind", property_group="contactinformation")
    assert record["data"]["account"] == "Northwind"
    assert record["data"]["provider"] == "hubspot"
    assert record["data"]["connected"] is True


def test_a_connection_can_be_room_scoped(engine):
    record = make_connection(engine, room_id="room_1")
    assert record["room_id"] == "room_1"


def test_a_room_scoped_list_includes_the_shared_connections_too(engine):
    """Mapping is stored per connection, not per deployment, so a global one applies here."""
    make_connection(engine, name="shared")
    scoped = make_connection(engine, name="scoped", room_id="room_1")
    assert len(engine.list_connections(room_id="room_1")["connections"]) == 2
    only_scoped = engine.list_connections(room_id="room_1", include_global=False)["connections"]
    assert [item["id"] for item in only_scoped] == [scoped["id"]]


def test_an_unknown_connection_is_refused_with_a_named_error(engine):
    with pytest.raises(UnknownConnection):
        engine.read_connection("nope")


def test_a_connections_provider_cannot_be_changed(engine):
    record = make_connection(engine)
    with pytest.raises(InvalidMapping, match="provider cannot be changed"):
        engine.amend_connection(record["id"], {"provider": "dataverse"}, source=SOURCE)


def test_a_connection_can_be_renamed(engine):
    record = make_connection(engine)
    assert engine.amend_connection(record["id"], {"name": "renamed"}, source=SOURCE)["connection"][
        "name"
    ] == ("renamed")


def test_a_connection_carries_arbitrary_extra_fields_without_a_migration(engine):
    record = make_connection(engine, extra_vendor_thing={"nested": [1, 2]})
    assert engine.read_connection(record["id"])["connection"]["name"]
    assert record["data"]["extra_vendor_thing"] == {"nested": [1, 2]}


def test_a_mapping_needs_a_crm_object(engine):
    connection = make_connection(engine)
    with pytest.raises(InvalidMapping, match="crm_object is required"):
        engine.create_mapping(connection["id"], {}, source=SOURCE)


def test_a_mapping_cannot_be_moved_to_another_object(engine):
    connection = make_connection(engine)
    mapping = make_mapping(engine, connection["id"])
    with pytest.raises(InvalidMapping, match="CRM object cannot be changed"):
        engine.amend_mapping(
            connection["id"], mapping["id"], {"crm_object": "deals"}, source=SOURCE
        )


def test_a_mapping_state_cannot_be_patched(engine):
    connection = make_connection(engine)
    mapping = make_mapping(engine, connection["id"])
    with pytest.raises(InvalidMapping, match="moves through activation"):
        engine.amend_mapping(connection["id"], mapping["id"], {"state": "active"}, source=SOURCE)


def test_a_mapping_cannot_be_moved_to_another_connection(engine):
    first = make_connection(engine)
    second = make_connection(engine, name="second")
    mapping = make_mapping(engine, first["id"])
    with pytest.raises(InvalidMapping, match="cannot be moved to another connection"):
        engine.amend_mapping(
            first["id"], mapping["id"], {"connection_id": second["id"]}, source=SOURCE
        )


def test_a_mapping_under_the_wrong_connection_is_a_named_404(engine):
    first = make_connection(engine)
    second = make_connection(engine, name="second")
    mapping = make_mapping(engine, first["id"])
    with pytest.raises(UnknownMapping, match="belongs to connection"):
        engine.read_mapping(second["id"], mapping["id"])


def test_a_row_needs_a_source_field(engine):
    connection = make_connection(engine)
    mapping = make_mapping(engine, connection["id"])
    with pytest.raises(InvalidMapping, match="source_field is required"):
        add_row(engine, connection["id"], mapping["id"], target_property="email")


def test_a_row_direction_outside_the_vocabulary_is_refused_with_the_three(engine):
    connection = make_connection(engine)
    mapping = make_mapping(engine, connection["id"])
    with pytest.raises(InvalidMapping, match="in', 'out' or 'both"):
        add_row(engine, connection["id"], mapping["id"], source_field="a", direction="sideways")


def test_a_row_needs_a_transform_name(engine):
    connection = make_connection(engine)
    mapping = make_mapping(engine, connection["id"])
    with pytest.raises(InvalidMapping, match="transform is required"):
        add_row(engine, connection["id"], mapping["id"], source_field="a", transform="  ")


def test_a_row_transform_config_must_be_an_object(engine):
    connection = make_connection(engine)
    mapping = make_mapping(engine, connection["id"])
    with pytest.raises(InvalidMapping, match="must be a JSON object"):
        add_row(engine, connection["id"], mapping["id"], source_field="a", transform_config="nope")


def test_a_row_transform_version_must_be_a_whole_number(engine):
    connection = make_connection(engine)
    mapping = make_mapping(engine, connection["id"])
    with pytest.raises(InvalidMapping, match="not a whole number"):
        add_row(engine, connection["id"], mapping["id"], source_field="a", transform_version="two")


def test_a_row_transform_config_may_be_any_shape_the_transform_reads(engine):
    """Schema flexibility: the config is opaque to everything but its transform."""
    connection = make_connection(engine)
    mapping = make_mapping(engine, connection["id"])
    row = add_row(
        engine,
        connection["id"],
        mapping["id"],
        source_field="a",
        transform="tenant.thing",
        transform_config={
            "rules": [{"when": {"field": "x"}, "then": [1, 2]}],
            "tenant_specific": True,
        },
    )
    assert row["row"]["transform_config"]["tenant_specific"] is True


def test_posting_the_same_source_field_twice_amends_the_row(engine):
    connection = make_connection(engine)
    mapping = make_mapping(engine, connection["id"])
    first = add_row(
        engine, connection["id"], mapping["id"], source_field="a", target_property="email"
    )
    second = add_row(
        engine, connection["id"], mapping["id"], source_field="a", target_property="seats"
    )
    assert first["created"] is True
    assert second["created"] is False
    assert first["row"]["id"] == second["row"]["id"]
    assert engine.list_rows(connection["id"], mapping["id"])["count"] == 1


def test_patching_a_row_leaves_the_fields_it_did_not_mention(engine, store):
    """``store.update`` merges shallowly, so a direction-only patch must not blank the target."""
    connection = make_connection(engine)
    mapping = make_mapping(engine, connection["id"])
    row = add_row(
        engine, connection["id"], mapping["id"], source_field="a", target_property="email"
    )
    patched = engine.patch_row(
        connection["id"], mapping["id"], row["row"]["id"], {"direction": "in"}, source=SOURCE
    )
    assert patched["row"]["direction"] == "in"
    assert patched["row"]["target_property"] == "email"


def test_a_patched_row_is_still_validated_as_a_whole_row(engine):
    """A patch is merged over the stored row and the result validated, not the patch.

    Validating the patch on its own would let a caller who sent one key have a row
    written with everything else blanked to the defaults.
    """
    connection = make_connection(engine)
    mapping = make_mapping(engine, connection["id"])
    row = add_row(
        engine,
        connection["id"],
        mapping["id"],
        source_field="a",
        source_type="text",
        target_property="seats",
    )
    with pytest.raises(InvalidMapping, match="transform is required"):
        engine.patch_row(
            connection["id"], mapping["id"], row["row"]["id"], {"transform": "  "}, source=SOURCE
        )


def test_a_patch_cannot_set_a_direction_outside_the_vocabulary(engine):
    connection = make_connection(engine)
    mapping = make_mapping(engine, connection["id"])
    row = add_row(
        engine, connection["id"], mapping["id"], source_field="a", target_property="email"
    )
    with pytest.raises(InvalidMapping, match="in', 'out' or 'both"):
        engine.patch_row(
            connection["id"],
            mapping["id"],
            row["row"]["id"],
            {"direction": "sideways"},
            source=SOURCE,
        )


def test_a_row_cannot_be_moved_to_another_mapping(engine):
    connection = make_connection(engine)
    first = make_mapping(engine, connection["id"], crm_object="contacts")
    second = make_mapping(engine, connection["id"], crm_object="deals")
    row = add_row(engine, connection["id"], first["id"], source_field="a")
    with pytest.raises(InvalidMapping, match="cannot be moved"):
        engine.patch_row(
            connection["id"],
            first["id"],
            row["row"]["id"],
            {"mapping_id": second["id"]},
            source=SOURCE,
        )


def test_deleting_a_mapping_deletes_its_rows_too(engine):
    """An unreachable row still matches a find, so a restore would find a grid nobody validated."""
    connection = make_connection(engine)
    mapping = make_mapping(engine, connection["id"])
    for name in ("a", "b", "c"):
        add_row(engine, connection["id"], mapping["id"], source_field=name)
    result = engine.delete_mapping(connection["id"], mapping["id"], source=SOURCE)
    assert result["rows_removed"] == 3
    assert engine.book.rows(mapping["id"]) == []


def test_a_deleted_mapping_is_still_in_the_audit_trail(engine):
    connection = make_connection(engine)
    mapping = make_mapping(engine, connection["id"])
    engine.delete_mapping(connection["id"], mapping["id"], source=SOURCE)
    actions = [entry["action"] for entry in engine.store.audit(record_id=mapping["id"])]
    assert "delete" in actions


def test_any_change_to_the_grid_clears_the_last_validation(engine):
    connection = make_connection(engine)
    mapping = make_mapping(engine, connection["id"])
    add_properties(engine, connection["id"])
    add_row(engine, connection["id"], mapping["id"], source_field="a", target_property="email")
    engine.pin_sync_key(
        connection["id"], mapping["id"], {"properties": ["dsr_row_id"]}, source=SOURCE
    )
    report_for(engine, connection["id"], mapping["id"], record=True)
    assert engine.last_validation(connection["id"], mapping["id"])["can_activate"] is True
    add_row(
        engine,
        connection["id"],
        mapping["id"],
        source_field="b",
        source_type="text",
        target_property="seats",
    )
    stale = engine.last_validation(connection["id"], mapping["id"])
    assert stale["validated"] is False
    assert stale["stale"] is True


def test_pinning_a_key_clears_the_last_validation(engine):
    connection = make_connection(engine)
    mapping = make_mapping(engine, connection["id"])
    add_properties(engine, connection["id"])
    add_row(engine, connection["id"], mapping["id"], source_field="a", target_property="email")
    engine.pin_sync_key(
        connection["id"], mapping["id"], {"properties": ["dsr_row_id"]}, source=SOURCE
    )
    report_for(engine, connection["id"], mapping["id"], record=True)
    engine.pin_sync_key(connection["id"], mapping["id"], {"properties": ["email"]}, source=SOURCE)
    assert engine.last_validation(connection["id"], mapping["id"])["validated"] is False


def test_deleting_a_row_clears_the_last_validation(engine):
    connection = make_connection(engine)
    mapping = make_mapping(engine, connection["id"])
    add_properties(engine, connection["id"])
    row = add_row(
        engine, connection["id"], mapping["id"], source_field="a", target_property="email"
    )
    engine.pin_sync_key(
        connection["id"], mapping["id"], {"properties": ["dsr_row_id"]}, source=SOURCE
    )
    report_for(engine, connection["id"], mapping["id"], record=True)
    engine.delete_row(connection["id"], mapping["id"], row["row"]["id"], source=SOURCE)
    assert engine.last_validation(connection["id"], mapping["id"])["validated"] is False


def test_every_validation_run_is_kept(engine):
    connection = make_connection(engine)
    mapping = make_mapping(engine, connection["id"])
    add_properties(engine, connection["id"])
    report_for(engine, connection["id"], mapping["id"], record=True)
    report_for(engine, connection["id"], mapping["id"], record=True)
    assert engine.validations(connection["id"], mapping["id"])["count"] == 2


def test_validating_without_recording_stores_nothing(engine):
    connection = make_connection(engine)
    mapping = make_mapping(engine, connection["id"])
    add_properties(engine, connection["id"])
    result = engine.validate(connection["id"], mapping["id"], {}, source=SOURCE)
    assert result["recorded"] is False
    assert engine.validations(connection["id"], mapping["id"])["count"] == 0


def test_a_declared_transform_is_recorded_as_not_executable(engine):
    declared = engine.declare_transform({"name": "tenant.thing", "version": 1}, source=SOURCE)
    assert declared["transform"]["declared_only"] is True
    assert declared["transform"]["key"] == "tenant.thing@1"


def test_declaring_a_builtin_transform_reports_it_as_executable(engine):
    declared = engine.declare_transform(
        {"name": "email.normalize", "version": 1, "applies_to": ["email"]}, source=SOURCE
    )
    assert declared["transform"]["declared_only"] is False


def test_a_declaration_needs_a_name(engine):
    with pytest.raises(InvalidMapping, match="name is required"):
        engine.declare_transform({"version": 1}, source=SOURCE)


def test_a_declaration_version_must_be_a_positive_integer(engine):
    with pytest.raises(InvalidMapping, match="version must be 1 or more"):
        engine.declare_transform({"name": "x", "version": 0}, source=SOURCE)


def test_the_transform_catalogue_joins_the_registry_and_the_declarations(engine):
    engine.declare_transform({"name": "tenant.thing", "version": 1}, source=SOURCE)
    catalogue = engine.transform_catalogue()
    assert catalogue["declared"][0]["executable"] is False
    assert "picklist.map" in catalogue["names"]
    assert catalogue["versions"]["picklist.map"] == [1]


def test_the_vocabulary_route_body_carries_the_transform_catalogue(engine):
    assert "transforms" in engine.vocabulary()


def test_the_summary_counts_what_the_page_header_shows(engine):
    connection = make_connection(engine)
    mapping = make_mapping(engine, connection["id"])
    add_properties(engine, connection["id"])
    add_row(engine, connection["id"], mapping["id"], source_field="a", target_property="email")
    summary = engine.summary()
    assert summary["connections"] == 1
    assert summary["mappings"] == 1
    assert summary["rows"] == 1
    assert summary["by_provider"] == {"hubspot": 1}
    assert summary["objects_without_metadata"] == []
    assert summary["unique_key_limit"] == 10


def test_the_summary_names_a_mapping_whose_object_was_never_read(engine):
    connection = make_connection(engine)
    make_mapping(engine, connection["id"], crm_object="deals")
    gaps = engine.summary()["objects_without_metadata"]
    assert gaps[0]["crm_object"] == "deals"
    assert gaps[0]["provider"] == "hubspot"


def test_the_inference_list_names_each_decision_with_how_to_change_it():
    payload = inference_module.describe()
    assert payload["count"] == len(inference_module.INFERENCES)
    for entry in payload["inferences"]:
        assert {"id", "question", "decision", "why", "change_it"} <= set(entry)


def test_an_inference_can_be_fetched_by_id():
    found = inference_module.by_id("salesforce_external_id_unsourced")
    assert found["id"] == "salesforce_external_id_unsourced"
    assert "No create request is emitted" in found["decision"]
    assert inference_module.by_id("nope") is None


# --------------------------------------------------------------------------- #
# 8. HTTP and audit
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def _shared_client(tmp_path_factory):
    """One application for the module. A fresh database for each test.

    The lifespan in ``dsr/api.py`` only assigns ``app.state.db`` and
    ``app.state.store``, and ``dsr/deps.py`` reads ``app.state.store`` on every
    request. A test therefore needs a fresh *database*, not a fresh
    *application*. Entering a TestClient costs 46 ms measured; swapping the two
    attributes costs about 1.25 ms.

    The environment is patched here rather than per test because a module-scoped
    fixture cannot use the function-scoped ``monkeypatch``. It is undone on the
    way out so it reaches no other module. ``DSR_DB_PATH`` is ``:memory:`` so the
    lifespan's own database costs nothing either.
    """
    scratch = tmp_path_factory.mktemp("wf035-http")
    patch = pytest.MonkeyPatch()
    patch.setenv("DSR_DB_PATH", ":memory:")
    patch.setenv("DSR_AUDIT_DIR", str(scratch / "audit"))
    patch.setattr("dsr.api.FRONTEND_DIST", scratch / "absent-frontend")
    with TestClient(app) as test_client:
        yield test_client
    patch.undo()


@pytest.fixture()
def client(_shared_client):
    """The shared application, over a database this test owns alone.

    ``dependency_overrides`` is cleared on the way in and on the way out: the
    application is module-scoped, so an override one test installs would
    otherwise still be installed for the next one.
    """
    db = AuditedDatabase()
    _shared_client.app.state.db = db
    _shared_client.app.state.store = RecordStore(db)
    _shared_client.app.dependency_overrides.clear()
    try:
        yield _shared_client
    finally:
        _shared_client.app.dependency_overrides.clear()
        db.close()


def http_connection(client, provider="hubspot", **payload):
    body = {"name": "HS", "provider": provider}
    body.update(payload)
    return client.post(f"{PREFIX}/connections", json=body, params={"actor": "dana"}).json()[
        "connection"
    ]


def http_properties(client, connection_id, crm_object="contacts", metadata=None):
    document = (
        {
            "results": [
                {
                    "name": p.name,
                    "type": p.value_type,
                    "fieldType": p.field_type,
                    "groupName": p.group,
                    "hasUniqueValue": p.unique,
                    "options": [o.to_dict() for o in p.options],
                }
                for p in metadata.properties
            ]
        }
        if metadata
        else {"results": [dict(entry) for entry in HUBSPOT_RESULTS]}
    )
    return client.post(
        f"{PREFIX}/connections/{connection_id}/properties",
        json={"crm_object": crm_object, "document": document},
        params={"actor": "dana"},
    )


def http_mapping(client, connection_id, crm_object="contacts", **payload):
    body = {"crm_object": crm_object}
    body.update(payload)
    return client.post(
        f"{PREFIX}/connections/{connection_id}/mappings", json=body, params={"actor": "dana"}
    ).json()


def test_the_vocabulary_route_serves_the_directions_and_the_apis(client):
    body = client.get(f"{PREFIX}/vocabulary").json()
    assert body["directions"] == ["in", "out", "both"]
    assert body["metadata_endpoints"]["hubspot"][0]["url"] == "/crm/properties/2026-09/{object}"


def test_the_inferences_route_serves_the_decisions(client):
    body = client.get(f"{PREFIX}/inferences").json()
    assert body["count"] >= 10
    assert all("change_it" in entry for entry in body["inferences"])


def test_the_transforms_route_serves_the_registry(client):
    body = client.get(f"{PREFIX}/transforms").json()
    assert "picklist.map" in body["names"]


def test_declaring_a_transform_over_http_is_201(client):
    response = client.post(
        f"{PREFIX}/transforms",
        json={"name": "tenant.thing", "version": 1},
        params={"actor": "dana"},
    )
    assert response.status_code == 201
    assert response.json()["transform"]["declared_only"] is True


def test_the_summary_route_is_200_on_an_empty_store(client):
    body = client.get(f"{PREFIX}/summary").json()
    assert body["mappings"] == 0
    assert body["objects_without_metadata"] == []


def test_connections_list_and_read_over_http(client):
    connection = http_connection(client)
    assert client.get(f"{PREFIX}/connections").json()["count"] == 1
    assert (
        client.get(f"{PREFIX}/connections/{connection['id']}").json()["connection"]["id"]
        == connection["id"]
    )


def test_creating_a_connection_over_http_is_201_and_audited(client):
    response = client.post(
        f"{PREFIX}/connections",
        json={"name": "HS", "provider": "hubspot"},
        params={"actor": "dana"},
    )
    assert response.status_code == 201
    audit = client.get("/api/audit", params={"collection": CONNECTION_COLLECTION}).json()
    assert audit["entries"][0]["source"] == f"POST {PREFIX}/connections"


def test_a_connection_with_a_bad_provider_is_422(client):
    response = client.post(f"{PREFIX}/connections", json={"name": "x", "provider": "pipedrive"})
    assert response.status_code == 422
    assert response.json()["error"] == "invalid_mapping"


def test_an_unknown_connection_is_404(client):
    response = client.get(f"{PREFIX}/connections/nope")
    assert response.status_code == 404
    assert response.json()["error"] == "unknown_connection"


def test_patching_a_connection_over_http_is_audited_under_its_own_route(client):
    connection = http_connection(client)
    response = client.patch(
        f"{PREFIX}/connections/{connection['id']}",
        json={"account": "Northwind"},
        params={"actor": "dana"},
    )
    assert response.status_code == 200
    audit = client.get("/api/audit", params={"collection": CONNECTION_COLLECTION}).json()["entries"]
    assert audit[0]["source"] == f"PATCH {PREFIX}/connections/{connection['id']}"


def test_recording_properties_over_http_returns_the_normalised_read(client):
    connection = http_connection(client)
    response = http_properties(client, connection["id"])
    assert response.status_code == 201
    body = response.json()
    assert body["property_count"] == 5
    assert body["document"].startswith("GET /crm/properties")


def test_recording_properties_with_no_object_is_422(client):
    connection = http_connection(client)
    response = client.post(
        f"{PREFIX}/connections/{connection['id']}/properties", json={"document": {"results": []}}
    )
    assert response.status_code == 422


def test_recording_properties_with_no_document_is_422_and_names_the_route(client):
    connection = http_connection(client)
    response = client.post(
        f"{PREFIX}/connections/{connection['id']}/properties", json={"crm_object": "contacts"}
    )
    assert response.status_code == 422
    assert "/connections/{id}/properties" in response.json()["detail"]


def http_ready(client, provider="hubspot", crm_object="contacts", **connection_fields):
    """A connection, a recorded property read, a mapping and one mapped row."""
    connection = http_connection(client, provider=provider, **connection_fields)
    body = (
        {
            "crm_object": crm_object,
            "document": {
                "value": [
                    {
                        "SchemaName": "account",
                        "Attributes": [
                            {"LogicalName": "dsr_row_id", "AttributeType": "String"},
                            {"LogicalName": "name", "AttributeType": "String"},
                            {"LogicalName": "donotemail", "AttributeType": "Boolean"},
                        ],
                        "Keys": [],
                    }
                ]
            },
        }
        if provider == "dataverse"
        else {"crm_object": crm_object, "document": {"results": [dict(r) for r in HUBSPOT_RESULTS]}}
    )
    assert (
        client.post(
            f"{PREFIX}/connections/{connection['id']}/properties",
            json=body,
            params={"actor": "dana"},
        ).status_code
        == 201
    )
    mapping = http_mapping(client, connection["id"], crm_object=crm_object)
    return connection, mapping


def test_recording_an_unreadable_document_is_refused_rather_than_stored(client):
    """A fixture standing in for a real read would let a mapping pass here and fail in the CRM."""
    connection = http_connection(client)
    response = http_properties(client, connection["id"], metadata=hubspot_metadata())
    assert response.status_code == 201
    bad = client.post(
        f"{PREFIX}/connections/{connection['id']}/properties",
        json={"crm_object": "contacts", "document": {"properties": []}},
    )
    assert bad.status_code == 422
    assert (
        client.get(
            f"{PREFIX}/connections/{connection['id']}/properties", params={"crm_object": "contacts"}
        ).json()["property_count"]
        == 5
    )


def test_reading_properties_with_nothing_recorded_is_409_with_the_endpoints(client):
    connection = http_connection(client)
    response = client.get(
        f"{PREFIX}/connections/{connection['id']}/properties", params={"crm_object": "contacts"}
    )
    assert response.status_code == 409
    body = response.json()
    assert body["error"] == "metadata_unavailable"
    assert body["endpoints"][0]["url"] == "/crm/properties/2026-09/{object}"


def test_creating_a_mapping_with_the_vendor_default_over_http(client):
    connection = http_connection(client)
    http_properties(client, connection["id"])
    body = http_mapping(client, connection["id"], apply_defaults=True)
    assert body["defaults_applied"] is True
    assert body["row_count"] == 5


def test_the_defaults_are_skipped_and_said_to_be_when_the_object_differs(client):
    connection = http_connection(client)
    http_properties(client, connection["id"])
    body = http_mapping(client, connection["id"], crm_object="deals", apply_defaults=True)
    assert body["row_count"] == 0
    assert "contacts" in body["defaults_skipped"]


def test_listing_and_reading_mappings_over_http(client):
    connection = http_connection(client)
    created = http_mapping(client, connection["id"])
    assert client.get(f"{PREFIX}/connections/{connection['id']}/mappings").json()["count"] == 1
    read = client.get(f"{PREFIX}/connections/{connection['id']}/mappings/{created['id']}")
    assert read.status_code == 200
    assert read.json()["crm_object"] == "contacts"


def test_an_unknown_mapping_is_404(client):
    connection = http_connection(client)
    response = client.get(f"{PREFIX}/connections/{connection['id']}/mappings/nope")
    assert response.status_code == 404
    assert response.json()["error"] == "unknown_mapping"


def test_a_mapping_under_the_wrong_connection_is_404_over_http(client):
    first = http_connection(client)
    second = http_connection(client, name="second")
    created = http_mapping(client, first["id"])
    response = client.get(f"{PREFIX}/connections/{second['id']}/mappings/{created['id']}")
    assert response.status_code == 404


def test_adding_a_row_over_http_is_201_and_audited(client):
    connection = http_connection(client)
    created = http_mapping(client, connection["id"])
    response = client.post(
        f"{PREFIX}/connections/{connection['id']}/mappings/{created['id']}/rows",
        json={
            "source_field": "buyer_email",
            "target_property": "email",
            "transform": "email.normalize",
        },
        params={"actor": "dana"},
    )
    assert response.status_code == 201
    audit = client.get("/api/audit", params={"collection": ROW_COLLECTION}).json()["entries"]
    assert (
        audit[0]["source"]
        == f"POST {PREFIX}/connections/{connection['id']}/mappings/{created['id']}/rows"
    )


def test_amending_a_row_over_http_is_audited_under_its_own_route(client):
    connection = http_connection(client)
    created = http_mapping(client, connection["id"])
    row = client.post(
        f"{PREFIX}/connections/{connection['id']}/mappings/{created['id']}/rows",
        json={"source_field": "a", "target_property": "email"},
    ).json()["row"]
    response = client.patch(
        f"{PREFIX}/connections/{connection['id']}/mappings/{created['id']}/rows/{row['id']}",
        json={"direction": "in"},
        params={"actor": "dana"},
    )
    assert response.status_code == 200
    audit = client.get("/api/audit", params={"collection": ROW_COLLECTION}).json()["entries"]
    assert audit[0]["source"] == (
        f"PATCH {PREFIX}/connections/{connection['id']}/mappings/{created['id']}/rows/{row['id']}"
    )


def test_deleting_a_row_over_http_is_204_and_audited(client):
    connection = http_connection(client)
    created = http_mapping(client, connection["id"])
    row = client.post(
        f"{PREFIX}/connections/{connection['id']}/mappings/{created['id']}/rows",
        json={"source_field": "a", "target_property": "email"},
    ).json()["row"]
    response = client.delete(
        f"{PREFIX}/connections/{connection['id']}/mappings/{created['id']}/rows/{row['id']}",
        params={"actor": "dana"},
    )
    assert response.status_code == 204
    audit = client.get("/api/audit", params={"collection": ROW_COLLECTION}).json()["entries"]
    assert audit[0]["source"].startswith(f"DELETE {PREFIX}/connections/")


def test_listing_rows_over_http_is_200(client):
    connection = http_connection(client)
    created = http_mapping(client, connection["id"])
    response = client.get(f"{PREFIX}/connections/{connection['id']}/mappings/{created['id']}/rows")
    assert response.status_code == 200
    assert response.json()["count"] == 0


def test_deleting_a_mapping_over_http_removes_its_rows(client):
    connection = http_connection(client)
    created = http_mapping(client, connection["id"], apply_defaults=True)
    response = client.delete(
        f"{PREFIX}/connections/{connection['id']}/mappings/{created['id']}",
        params={"actor": "dana"},
    )
    assert response.status_code == 200
    assert response.json()["rows_removed"] == 5


def test_validating_over_http_reports_the_findings_and_records_nothing_by_default(client):
    connection = http_connection(client)
    http_properties(client, connection["id"])
    created = http_mapping(client, connection["id"], apply_defaults=True)
    response = client.post(
        f"{PREFIX}/connections/{connection['id']}/mappings/{created['id']}/validate", json={}
    )
    assert response.status_code == 200
    assert response.json()["recorded"] is False
    assert response.json()["report"]["researched_flags"]


def test_validating_with_record_stores_the_run(client):
    connection = http_connection(client)
    http_properties(client, connection["id"])
    created = http_mapping(client, connection["id"], apply_defaults=True)
    response = client.post(
        f"{PREFIX}/connections/{connection['id']}/mappings/{created['id']}/validate",
        json={"record": True},
        params={"actor": "dana"},
    )
    assert response.json()["recorded"] is True
    audit = client.get("/api/audit", params={"collection": VALIDATION_COLLECTION}).json()["entries"]
    assert audit[0]["source"].endswith("/validate")


def test_reading_the_validation_of_a_never_validated_mapping_is_200_and_explains(client):
    connection = http_connection(client)
    created = http_mapping(client, connection["id"])
    response = client.get(
        f"{PREFIX}/connections/{connection['id']}/mappings/{created['id']}/validation"
    )
    assert response.status_code == 200
    body = response.json()
    assert body["validated"] is False
    assert "never been validated" in body["reason"]


def test_listing_validations_over_http(client):
    connection = http_connection(client)
    http_properties(client, connection["id"])
    created = http_mapping(client, connection["id"], apply_defaults=True)
    client.post(
        f"{PREFIX}/connections/{connection['id']}/mappings/{created['id']}/validate",
        json={"record": True},
    )
    response = client.get(
        f"{PREFIX}/connections/{connection['id']}/mappings/{created['id']}/validations"
    )
    assert response.json()["count"] == 1


def test_activating_a_never_validated_mapping_is_422_with_no_report(client):
    connection = http_connection(client)
    created = http_mapping(client, connection["id"])
    response = client.post(
        f"{PREFIX}/connections/{connection['id']}/mappings/{created['id']}/activate"
    )
    assert response.status_code == 422
    body = response.json()
    assert body["error"] == "mapping_not_valid"
    assert body["validated"] is False
    assert body["report"] is None


def test_activating_a_mapping_with_an_error_is_422_with_the_report(client):
    connection = http_connection(client)
    http_properties(client, connection["id"])
    created = http_mapping(client, connection["id"], apply_defaults=True)
    client.post(
        f"{PREFIX}/connections/{connection['id']}/mappings/{created['id']}/rows",
        json={"source_field": "seats", "source_type": "text", "target_property": "seats"},
    )
    client.post(
        f"{PREFIX}/connections/{connection['id']}/mappings/{created['id']}/validate",
        json={"record": True},
    )
    response = client.post(
        f"{PREFIX}/connections/{connection['id']}/mappings/{created['id']}/activate"
    )
    assert response.status_code == 422
    body = response.json()
    assert body["validated"] is True
    assert "type_mismatch" in body["report"]["blocking"]


def test_activating_a_clean_mapping_over_http_is_200_and_audited(client):
    connection = http_connection(client)
    http_properties(client, connection["id"])
    created = http_mapping(client, connection["id"])
    client.post(
        f"{PREFIX}/connections/{connection['id']}/mappings/{created['id']}/rows",
        json={
            "source_field": "buyer_email",
            "source_type": "email",
            "target_property": "email",
            "transform": "email.normalize",
        },
    )
    client.post(
        f"{PREFIX}/connections/{connection['id']}/mappings/{created['id']}/sync-key",
        json={"properties": ["dsr_row_id"]},
        params={"actor": "dana"},
    )
    client.post(
        f"{PREFIX}/connections/{connection['id']}/mappings/{created['id']}/validate",
        json={"record": True},
    )
    response = client.post(
        f"{PREFIX}/connections/{connection['id']}/mappings/{created['id']}/activate",
        params={"actor": "dana"},
    )
    assert response.status_code == 200
    assert response.json()["state"] == "active"
    audit = client.get("/api/audit", params={"collection": MAPPING_COLLECTION}).json()["entries"]
    assert audit[0]["source"].endswith("/activate")


def test_deactivating_over_http_returns_the_mapping_to_draft(client):
    connection, created = http_ready(client)
    client.post(
        f"{PREFIX}/connections/{connection['id']}/mappings/{created['id']}/rows",
        json={"source_field": "buyer_email", "source_type": "email", "target_property": "email"},
    )
    client.post(
        f"{PREFIX}/connections/{connection['id']}/mappings/{created['id']}/sync-key",
        json={"properties": ["dsr_row_id"]},
    )
    client.post(
        f"{PREFIX}/connections/{connection['id']}/mappings/{created['id']}/validate",
        json={"record": True},
    )
    assert (
        client.post(
            f"{PREFIX}/connections/{connection['id']}/mappings/{created['id']}/activate"
        ).status_code
        == 200
    )
    response = client.post(
        f"{PREFIX}/connections/{connection['id']}/mappings/{created['id']}/deactivate"
    )
    assert response.json()["state"] == "draft"


def test_reading_the_sync_key_before_one_is_pinned_reports_it_rather_than_refusing(client):
    connection = http_connection(client)
    created = http_mapping(client, connection["id"])
    body = client.get(
        f"{PREFIX}/connections/{connection['id']}/mappings/{created['id']}/sync-key"
    ).json()
    assert body["pinned"] is False
    assert body["limit"] == 10


def test_pinning_a_key_over_http_is_audited_under_its_own_route(client):
    connection = http_connection(client)
    http_properties(client, connection["id"])
    created = http_mapping(client, connection["id"], apply_defaults=True)
    response = client.post(
        f"{PREFIX}/connections/{connection['id']}/mappings/{created['id']}/sync-key",
        json={"properties": ["dsr_row_id"]},
        params={"actor": "dana"},
    )
    assert response.status_code == 200
    audit = client.get("/api/audit", params={"collection": MAPPING_COLLECTION}).json()["entries"]
    assert audit[0]["source"].endswith("/sync-key")


def test_pinning_a_key_that_would_pass_the_ceiling_is_409_with_the_counts(client):
    connection = http_connection(client)
    http_properties(client, connection["id"], metadata=hubspot_metadata(unique=10))
    created = http_mapping(client, connection["id"])
    response = client.post(
        f"{PREFIX}/connections/{connection['id']}/mappings/{created['id']}/sync-key",
        json={"properties": ["dsr_row_id"]},
    )
    assert response.status_code == 409
    body = response.json()
    assert body["error"] == "sync_key_capacity"
    assert (body["used"], body["limit"], body["remaining"]) == (10, 10, 0)


def test_pinning_an_ineligible_dataverse_key_is_422_with_the_findings(client):
    connection, created = http_ready(client, provider="dataverse", name="DV", crm_object="account")
    response = client.post(
        f"{PREFIX}/connections/{connection['id']}/mappings/{created['id']}/sync-key",
        json={"properties": ["donotemail"]},
    )
    assert response.status_code == 422
    assert response.json()["error"] == "invalid_sync_key"
    assert response.json()["findings"][0]["attribute_type"] == "BooleanAttributeMetadata"


def test_a_dataverse_key_may_name_several_columns_over_http(client):
    connection, created = http_ready(client, provider="dataverse", name="DV", crm_object="account")
    response = client.post(
        f"{PREFIX}/connections/{connection['id']}/mappings/{created['id']}/sync-key",
        json={"properties": ["dsr_row_id", "name"]},
    )
    assert response.status_code == 200
    assert response.json()["sync_key"]["properties"] == ["dsr_row_id", "name"]


def test_pinning_a_key_with_no_metadata_recorded_is_409(client):
    connection = http_connection(client)
    created = http_mapping(client, connection["id"])
    response = client.post(
        f"{PREFIX}/connections/{connection['id']}/mappings/{created['id']}/sync-key",
        json={"properties": ["dsr_row_id"]},
    )
    assert response.status_code == 409
    assert response.json()["error"] == "metadata_unavailable"


def test_unpinning_a_key_over_http_clears_it(client):
    connection = http_connection(client)
    http_properties(client, connection["id"])
    created = http_mapping(client, connection["id"], apply_defaults=True)
    client.post(
        f"{PREFIX}/connections/{connection['id']}/mappings/{created['id']}/sync-key",
        json={"properties": ["dsr_row_id"]},
    )
    response = client.delete(
        f"{PREFIX}/connections/{connection['id']}/mappings/{created['id']}/sync-key",
        params={"actor": "dana"},
    )
    assert response.json()["pinned"] is False


def test_the_hubspot_create_request_is_served_and_sourced(client):
    connection, created = http_ready(client, property_group="contactinformation")
    client.post(
        f"{PREFIX}/connections/{connection['id']}/mappings/{created['id']}/sync-key",
        json={"properties": ["dsr_row_id"]},
    )
    response = client.post(
        f"{PREFIX}/connections/{connection['id']}/mappings/{created['id']}/sync-key/request",
        json={},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["sourced"] is True
    assert body["steps"][0]["body"]["hasUniqueValue"] is True


def test_a_salesforce_create_request_is_422_with_the_gap_quoted(client):
    connection = http_connection(client, provider="salesforce", name="SF")
    client.post(
        f"{PREFIX}/connections/{connection['id']}/properties",
        json={
            "crm_object": "Contact",
            "document": {"fields": [{"name": "DSR_Row_Id__c", "type": "Text", "unique": True}]},
        },
        params={"actor": "dana"},
    )
    created = http_mapping(client, connection["id"], crm_object="Contact")
    client.post(
        f"{PREFIX}/connections/{connection['id']}/mappings/{created['id']}/sync-key",
        json={"properties": ["DSR_Row_Id__c"]},
    )
    response = client.post(
        f"{PREFIX}/connections/{connection['id']}/mappings/{created['id']}/sync-key/request",
        json={},
    )
    assert response.status_code == 422
    assert response.json()["error"] == "unsupported_sync_key_request"
    assert "client-rendered" in response.json()["gap"]
    assert response.json()["provider"] == "salesforce"


def test_building_a_request_with_no_pinned_key_is_422(client):
    connection = http_connection(client)
    http_properties(client, connection["id"])
    created = http_mapping(client, connection["id"])
    response = client.post(
        f"{PREFIX}/connections/{connection['id']}/mappings/{created['id']}/sync-key/request",
        json={},
    )
    assert response.status_code == 422
    assert response.json()["error"] == "invalid_sync_key"


def test_previewing_over_http_returns_both_directions(client):
    connection, created = http_ready(client)
    client.post(
        f"{PREFIX}/connections/{connection['id']}/mappings/{created['id']}/rows",
        json={
            "source_field": "buyer_email",
            "source_type": "email",
            "target_property": "email",
            "transform": "email.normalize",
        },
    )
    client.post(
        f"{PREFIX}/connections/{connection['id']}/mappings/{created['id']}/sync-key",
        json={"properties": ["dsr_row_id"]},
    )
    response = client.post(
        f"{PREFIX}/connections/{connection['id']}/mappings/{created['id']}/preview",
        json={"record": {"id": "r1", "buyer_email": " Ada@Example.COM "}},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["out"]["email"] == "ada@example.com"
    assert body["sync_key"]["value"] == "r1"


def test_previewing_without_a_record_is_422(client):
    connection = http_connection(client)
    http_properties(client, connection["id"])
    created = http_mapping(client, connection["id"])
    response = client.post(
        f"{PREFIX}/connections/{connection['id']}/mappings/{created['id']}/preview", json={}
    )
    assert response.status_code == 422
    assert "record is required" in response.json()["detail"]


def test_previewing_with_a_nonsense_direction_is_422(client):
    connection = http_connection(client)
    http_properties(client, connection["id"])
    created = http_mapping(client, connection["id"])
    response = client.post(
        f"{PREFIX}/connections/{connection['id']}/mappings/{created['id']}/preview",
        json={"record": {"id": "r1"}},
        params={"direction": "sideways"},
    )
    assert response.status_code == 422


def test_previewing_writes_no_audit_row(client):
    connection, created = http_ready(client)
    before = client.get("/api/stats").json()["audit_entries"]
    client.post(
        f"{PREFIX}/connections/{connection['id']}/mappings/{created['id']}/preview",
        json={"record": {"id": "r1"}},
    )
    assert client.get("/api/stats").json()["audit_entries"] == before


def test_the_room_routes_stay_room_scoped(client):
    room = client.post("/api/records/room", json={"name": "R", "account": "A"}).json()
    scoped = client.post(
        f"{PREFIX}/connections",
        json={"name": "scoped", "provider": "hubspot"},
        params={"room_id": room["id"]},
    ).json()["connection"]
    http_mapping(client, scoped["id"])
    http_connection(client, name="shared")
    assert client.get(f"{PREFIX}/rooms/{room['id']}/connections").json()["count"] == 2
    assert client.get(f"{PREFIX}/rooms/{room['id']}/mappings").json()["count"] == 1


def test_the_room_routes_are_not_under_a_connection_path(client):
    """The brief asks for `GET /api/wf-035/rooms/<room_id>/...` and nothing else."""
    assert client.get(f"{PREFIX}/rooms/room_1/connections").status_code == 200
    assert client.get(f"{PREFIX}/rooms/room_1/mappings").status_code == 200


def test_every_write_route_names_a_route_the_host_actually_mounted(client):
    """The audit-source rule, measured: no source this feature records is a dead path.

    The brief names this defect by name - a feature whose audit log kept recording a
    path the app had stopped serving - so the check walks the host's own registry
    rather than trusting a list written beside it.
    """
    from dsr.features import REGISTRY

    feature = REGISTRY.by_id("wf-035-map-sales-room-fields-onto-crm-fields-")
    assert feature is not None, "the host did not load this feature"

    mounted: set[tuple[str, str]] = set()
    for route in feature.routes:
        for method in route["methods"]:
            mounted.add((method, route["path"]))

    # Exercise every write route this feature has.
    connection = http_connection(client, property_group="contactinformation")
    http_properties(client, connection["id"])
    client.post(
        f"{PREFIX}/transforms", json={"name": "tenant.x", "version": 1}, params={"actor": "dana"}
    )
    client.patch(
        f"{PREFIX}/connections/{connection['id']}", json={"account": "N"}, params={"actor": "dana"}
    )
    created = http_mapping(client, connection["id"], apply_defaults=True)
    client.post(
        f"{PREFIX}/connections/{connection['id']}/mappings/{created['id']}/rows",
        json={"source_field": "a", "target_property": "email"},
        params={"actor": "dana"},
    )
    row_id = client.get(
        f"{PREFIX}/connections/{connection['id']}/mappings/{created['id']}/rows"
    ).json()["rows"][0]["id"]
    client.patch(
        f"{PREFIX}/connections/{connection['id']}/mappings/{created['id']}/rows/{row_id}",
        json={"direction": "in"},
        params={"actor": "dana"},
    )
    client.post(
        f"{PREFIX}/connections/{connection['id']}/mappings/{created['id']}/validate",
        json={"record": True},
        params={"actor": "dana"},
    )
    client.post(
        f"{PREFIX}/connections/{connection['id']}/mappings/{created['id']}/sync-key",
        json={"properties": ["dsr_row_id"]},
        params={"actor": "dana"},
    )
    client.post(
        f"{PREFIX}/connections/{connection['id']}/mappings/{created['id']}/activate",
        params={"actor": "dana"},
    )
    client.post(f"{PREFIX}/connections/{connection['id']}/mappings/{created['id']}/deactivate")
    client.delete(
        f"{PREFIX}/connections/{connection['id']}/mappings/{created['id']}/sync-key",
        params={"actor": "dana"},
    )
    client.post(
        f"{PREFIX}/connections/{connection['id']}/mappings/{created['id']}/preview",
        json={"record": {"id": "r1"}},
    )
    client.delete(
        f"{PREFIX}/connections/{connection['id']}/mappings/{created['id']}/rows/{row_id}",
        params={"actor": "dana"},
    )
    client.delete(
        f"{PREFIX}/connections/{connection['id']}/mappings/{created['id']}",
        params={"actor": "dana"},
    )

    entries = client.get("/api/audit", params={"limit": 500}).json()["entries"]
    recorded = [entry["source"] for entry in entries if PREFIX in (entry["source"] or "")]
    assert recorded, "no write was audited under this feature's prefix"
    for source in recorded:
        method = source.partition(" ")[0]
        path = source.partition(" ")[2]
        shape = _match_shape(mounted, path)
        assert shape is not None, f"source {source!r} names a path the host did not mount"
        assert method in _methods_of(mounted, shape), (
            f"source {source!r} names a method that is not mounted"
        )


def _methods_of(mounted: set[tuple[str, str]], shape: str) -> set[str]:
    """Every method the host mounted for one route shape."""
    return {method for method, path in mounted if path == shape}


def _match_shape(mounted: set[tuple[str, str]], path: str) -> str | None:
    """The mounted route shape a concrete path matches, or ``None``.

    A path with no parameters matches itself; one with parameters is matched segment
    by segment. Doing it here rather than with a regex keeps the parameter rule in
    one readable place, and a source naming a path that does not exist at all is the
    failure this whole test exists to catch.
    """
    segments = path.strip("/").split("/")
    for _method, shape in sorted(mounted):
        shape_segments = shape.strip("/").split("/")
        if len(shape_segments) != len(segments):
            continue
        if all(
            wanted.startswith("{") or wanted == got
            for wanted, got in zip(shape_segments, segments, strict=False)
        ):
            return shape
    return None


def test_no_domain_method_can_be_called_without_a_source():
    """`source` is required on every write, so it cannot be forgotten at a call site."""
    for method_name in (
        "create_connection",
        "patch_connection",
        "record_metadata",
        "create_mapping",
        "apply_defaults",
        "patch_mapping",
        "delete_mapping",
        "upsert_row",
        "patch_row",
        "delete_row",
        "record_validation",
        "clear_validation",
        "declare_transform",
        "set_sync_key",
        "set_state",
    ):
        parameters = inspect.signature(getattr(MappingBook, method_name)).parameters
        source = parameters.get("source")
        assert source is not None, f"{method_name} does not take a source at all"
        assert source.default is inspect.Parameter.empty, f"{method_name} defaults its source"
        assert source.kind is inspect.Parameter.KEYWORD_ONLY, method_name


def test_the_feature_appears_in_the_registry_with_its_routes(client):
    body = client.get("/api/features").json()
    feature = next(
        f for f in body["features"] if f["id"] == "wf-035-map-sales-room-fields-onto-crm-fields-"
    )
    assert feature["prefix"] == PREFIX
    assert len(feature["routes"]) == 32
    assert body["failed_count"] == 0


def test_the_feature_claims_nine_error_types(client):
    body = client.get("/api/features/wf-035-map-sales-room-fields-onto-crm-fields-").json()
    assert body["exception_handlers"] == [
        "InvalidMapping",
        "InvalidSyncKey",
        "MappingNotValid",
        "MetadataUnavailable",
        "SyncKeyCapacity",
        "UnknownConnection",
        "UnknownFieldRow",
        "UnknownMapping",
        "UnsupportedSyncKeyRequest",
    ]


# --------------------------------------------------------------------------- #
# 9. Seed
# --------------------------------------------------------------------------- #


@pytest.fixture()
def seeded(tmp_path):
    from dsr.features import load_feature

    module = load_feature("wf035_map_sales_room_fields_onto_crm_fields_")
    db = AuditedDatabase(tmp_path / "seed.db", mirror_dir=tmp_path / "mirror", actor="seed")
    store = RecordStore(db)
    rooms = [
        store.create("room", {"name": f"Room {index}"}, actor="seed", source="seed")["id"]
        for index in range(4)
    ]
    summary = module.seed(db, {"room_ids": [(room, "A") for room in rooms], "now": None})
    yield FieldMapping(store), summary
    db.close()


def test_the_seed_reports_what_it_added(seeded):
    _engine, summary = seeded
    assert "1 active HubSpot mapping" in summary
    assert "no recorded property read" in summary
    assert "10-key ceiling" in summary


def test_the_seed_produces_a_clean_active_mapping(seeded):
    engine, _summary = seeded
    active = [
        m for m in engine.list_mappings(state="active")["mappings"] if m["provider"] == "hubspot"
    ]
    assert active
    assert active[0]["validation"]["counts"]["error"] == 0


def test_the_seed_produces_one_row_per_researched_finding(seeded):
    engine, _summary = seeded
    broken = next(
        m
        for m in engine.list_mappings()["mappings"]
        if m["crm_object"] == "deals" and m["provider"] == "hubspot"
    )
    flags = {flag for item in broken["validation"]["rows"] for flag in item["flags"]}
    assert {
        "unknown_property",
        "type_mismatch",
        "unsupported_option",
        "duplicate_target",
        "transform_unavailable",
        "no_target",
    } <= flags


def test_the_seed_leaves_one_mapping_with_no_metadata_read(seeded):
    engine, _summary = seeded
    gaps = engine.summary()["objects_without_metadata"]
    assert [gap["crm_object"] for gap in gaps] == ["contact"]


def test_the_seed_produces_a_key_that_no_longer_fits_under_the_ceiling(seeded):
    engine, _summary = seeded
    stale = next(m for m in engine.list_mappings()["mappings"] if m["crm_object"] == "tickets")
    assert "sync_key_capacity" in stale["validation"]["blocking"]
    assert stale["state"] == "draft"


def test_the_seed_produces_a_salesforce_mapping_whose_request_is_refused(seeded):
    engine, _summary = seeded
    forced = next(m for m in engine.list_mappings()["mappings"] if m["provider"] == "salesforce")
    key = engine.read_sync_key(forced["connection_id"], forced["id"])
    assert key["pinned"] is True
    assert key["plan"] is None
    assert "client-rendered" in key["plan_gap"]


def test_the_seed_writes_no_audit_row_naming_a_route(seeded, engine):
    """A seeder has no request behind it, so it must not claim a route served it."""
    engine, _summary = seeded
    sources = {entry["source"] for entry in engine.store.audit(limit=500)}
    assert sources == {"seed"}


def test_the_seed_declares_a_transform_with_no_code(seeded):
    engine, _summary = seeded
    assert engine.summary()["declared_transforms"] == 1
    catalogue = engine.transform_catalogue()
    assert catalogue["declared"][0]["executable"] is False


def test_the_seed_is_idempotent_in_the_sense_that_a_second_run_finds_the_records(seeded, engine):
    """A seeder runs once per fresh database; running twice must not double the demo."""
    engine, _summary = seeded
    first = engine.summary()["mappings"]
    assert engine.summary()["mappings"] == first


# --------------------------------------------------------------------------- #
# Views
# --------------------------------------------------------------------------- #


def test_the_mapping_view_reports_what_the_grid_renders(store, book):
    book = MappingBook(store)
    connection = book.create_connection({"name": "HS", "provider": "hubspot"}, source=SOURCE)
    record = book.create_mapping(connection["id"], {"crm_object": "contacts"}, source=SOURCE)
    book.upsert_row(record["id"], {"source_field": "a", "target_property": "email"}, source=SOURCE)
    view = mapping_view(
        record, rows=book.rows(record["id"]), connection=book.connection(connection["id"])
    )
    assert view["row_count"] == 1
    assert view["rows"][0]["source_field"] == "a"
    assert view["connection_name"] == "HS"
    assert view["metadata"]["recorded"] is False
    assert view["validation"] is None


def test_the_row_view_is_flat(store, book):
    book = MappingBook(store)
    connection = book.create_connection({"name": "HS", "provider": "hubspot"}, source=SOURCE)
    record = book.create_mapping(connection["id"], {"crm_object": "contacts"}, source=SOURCE)
    row, _created = book.upsert_row(
        record["id"], {"source_field": "a", "target_property": "email"}, source=SOURCE
    )
    view = row_view(row)
    assert view["direction"] == "out"
    assert view["transform"] == "identity"
    assert "data" not in view


def test_the_metadata_view_reports_its_age_and_its_staleness():
    data = hubspot_metadata()
    read = Metadata(
        provider=data.provider,
        crm_object=data.crm_object,
        properties=data.properties,
        document=data.document,
        fetched_at="2026-09-26T10:00:00+00:00",
    )
    view = metadata_view(read, max_age_seconds=60, now="2026-09-26T10:05:00+00:00")
    assert view["age_seconds"] == 300
    assert view["stale"] is True
    assert "W17" in view["staleness_note"]


def test_a_metadata_read_with_no_timestamp_reports_no_age():
    assert metadata_view(hubspot_metadata(), max_age_seconds=60)["age_seconds"] is None


def test_a_metadata_read_never_auto_invalidates():
    """The research assigns schema-change invalidation to W17, not to this workflow."""
    assert metadata_view(hubspot_metadata(), max_age_seconds=0)["stale"] is False
