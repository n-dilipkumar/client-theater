"""WF-042 domain tests: the inbound CRM read, its limits, and its panel.

What is under test, and why each group exists
---------------------------------------------

``test_vocabulary_*``       the values the research fixes by name. A number that
                            changed here would change what the room asks a vendor
                            for, and nothing else would notice.
``test_fieldmap_*``         the extensibility claim. "The read set is derived
                            from the same field map as writes, so a deployment
                            that adds a field automatically gets it in the room's
                            deal panel with no extra API code" is a sentence a
                            test either proves or leaves as a docstring.
``test_query_*``            the three vendors' plans and the six researched
                            numbers: 2,000 Salesforce records, 5,000 and 500
                            Dataverse rows, 500 conditions, 100 batch ids,
                            200 per page, 3,000 characters, 10,000 results.
``test_sources_*``          the local copy of the vendor's tables, and the field
                            scope enforced on the way out.
``test_normalize_*``        three paging shapes into one view model.
``test_labels_*``           the researched capability check and its fallback.
``test_cache_*``            the four cache states and the TTL that separates them.
``test_identity_*``         the research's step two, and the room with no CRM.
``test_engine_*``           the whole flow, and the states that are not all green.
``test_the_domain_*``       the architectural guards the contract names.

Test isolation
--------------
Every test builds its own :class:`~dsr.crm_integration.engine.CrmReadEngine` over
the ``store`` fixture from ``conftest.py``, which is a fresh, empty, in-memory
database. Nothing here reaches for a module-level store or a shared file, so a
test passes when this file is run on its own and under ``pytest-xdist``.
"""

from __future__ import annotations

import inspect
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from dsr.crm_integration import (
    cache,
    fieldmap,
    identity,
    inferences,
    labels,
    normalize,
    query as query_rules,
    sources,
    vocabulary,
)
from dsr.crm_integration.engine import OWNED_COLLECTIONS, CrmReadEngine
from dsr.crm_integration.errors import (
    AmbiguousIdentity,
    BatchTooLarge,
    CrmIntegrationError,
    DuplicateIdentity,
    DuplicateOptionSet,
    EmptyReadSet,
    FieldMapError,
    FieldNotMapped,
    MissingBuyerEmail,
    NoCrmIdentity,
    PageTooLarge,
    QueryTooLong,
    TooManyConditions,
    UnknownIdentity,
    UnknownObject,
    UnknownOptionSet,
    UnknownSystem,
)
from dsr.features import wf042_pull_crm_deal_account_and_contact_data_into as feature
from dsr.store import RecordStore

SOURCE = "test"
ROOM = "room-1"
OTHER_ROOM = "room-2"
NOW = datetime(2026, 10, 4, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def engine(store: RecordStore) -> CrmReadEngine:
    """A fresh engine over a fresh, empty database."""
    return CrmReadEngine(store)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def add_identity(
    engine: CrmReadEngine,
    *,
    system: str = "salesforce",
    email: str = "dana@northwind.example",
    room_id: str = ROOM,
    deal: str = "006-1",
    contact: str = "003-1",
    account: str = "001-1",
    owner: str = "",
    field_map: dict | None = None,
    **extra,
) -> dict:
    """Register one identity, with the record ids a panel needs."""
    payload = {
        "system": system,
        "buyer_email": email,
        "buyer_name": extra.pop("buyer_name", "Dana Okafor"),
        "account_id": account,
        "contact_id": contact,
        "deal_id": deal,
        "owner_id": owner,
        **extra,
    }
    if field_map is not None:
        payload["field_map"] = field_map
    return engine.register_identity(
        payload, room_id=room_id, actor="test", source="POST /api/wf-042/identities"
    )


def add_rows(
    engine: CrmReadEngine,
    system: str,
    table: dict[str, dict],
    *,
    room_id: str = ROOM,
    owner: str = "",
) -> None:
    """Put one deal, contact and account into a vendor's tables."""
    for object_name, payload in table.items():
        engine.declare_record(
            {"system": system, "object": object_name, "owner_id": owner, **payload},
            room_id=room_id,
            actor="test",
            source="POST /api/wf-042/records",
        )


SALESFORCE_TABLE = {
    "deal": {
        "external_id": "006-1",
        "fields": {
            "Name": "Northwind rollout",
            "StageName": "Negotiation",
            "Amount": 48000,
            "CloseDate": "2026-11-30",
            "Probability": 70,
        },
        "modified": "2026-09-30T10:00:00+00:00",
    },
    "contact": {
        "external_id": "003-1",
        "email": "dana@northwind.example",
        "fields": {
            "Name": "Dana Okafor",
            "Title": "VP Operations",
            "Email": "dana@northwind.example",
        },
        "modified": "2026-09-30T10:00:00+00:00",
    },
    "account": {
        "external_id": "001-1",
        "fields": {
            "Name": "Northwind Traders",
            "Industry": "Manufacturing",
            "BillingCountry": "US",
        },
        "modified": "2026-09-30T10:00:00+00:00",
    },
}

DATAVERSE_TABLE = {
    "deal": {
        "external_id": "op-1",
        "fields": {"name": "Contoso pilot", "stepname": "1", "estimatedvalue": 27500},
        "labels": {"stepname": "Proposal sent"},
        "modified": "2026-09-30T10:00:00+00:00",
    },
    "contact": {
        "external_id": "ct-1",
        "email": "raj@contoso.example",
        "fields": {"fullname": "Raj Patel", "emailaddress1": "raj@contoso.example"},
        "modified": "2026-09-30T10:00:00+00:00",
    },
    "account": {
        "external_id": "ac-1",
        "fields": {"name": "Contoso Health", "industrycode": "Healthcare"},
        "modified": "2026-09-30T10:00:00+00:00",
    },
}

HUBSPOT_TABLE = {
    "deal": {
        "external_id": "dl-1",
        "fields": {"dealname": "Fabrikam workspace", "dealstage": "contractsent", "amount": 12400},
        "modified": "2026-09-30T10:00:00+00:00",
    },
    "contact": {
        "external_id": "ct-1",
        "email": "mei@fabrikam.example",
        "fields": {"email": "mei@fabrikam.example", "jobtitle": "Director"},
        "modified": "2026-09-30T10:00:00+00:00",
    },
    "account": {
        "external_id": "co-1",
        "fields": {"name": "Fabrikam Logistics", "industry": "Software"},
        "modified": "2026-09-30T10:00:00+00:00",
    },
}


def stage_option_set(engine: CrmReadEngine, **overrides) -> dict:
    """The room's own label for one stage option, registered globally."""
    payload = {
        "system": "salesforce",
        "object": "deal",
        "field": "stage",
        "values": {"Negotiation": "In negotiation"},
        **overrides,
    }
    return engine.register_option_set(
        payload, room_id=overrides.pop("room_id", None), actor="test", source="test"
    )


# --------------------------------------------------------------------------- #
# Vocabulary: the values the research fixes by name
# --------------------------------------------------------------------------- #


def test_the_three_researched_systems_and_objects_are_published():
    assert vocabulary.CRM_SYSTEMS == ("salesforce", "dataverse", "hubspot")
    assert vocabulary.CRM_OBJECTS == ("deal", "contact", "account")


def test_each_vendor_spells_each_object_in_its_own_words():
    assert vocabulary.object_name("salesforce", "deal") == "Opportunity"
    assert vocabulary.object_name("dataverse", "deal") == "opportunities"
    assert vocabulary.object_name("hubspot", "account") == "companies"
    assert vocabulary.object_name("hubspot", "deal") == "deals"


def test_require_system_folds_case_because_the_vendor_is_one_thing():
    assert vocabulary.require_system("  Salesforce ") == "salesforce"
    assert vocabulary.require_system("HUBSPOT") == "hubspot"


def test_require_system_refuses_a_system_outside_the_researched_three():
    with pytest.raises(UnknownSystem) as caught:
        vocabulary.require_system("sap")
    assert caught.value.status == 400
    assert "salesforce, dataverse, hubspot" in str(caught.value)


def test_require_object_refuses_an_object_the_panel_does_not_read():
    assert vocabulary.require_object("DEAL") == "deal"
    with pytest.raises(UnknownObject) as caught:
        vocabulary.require_object("lead")
    assert caught.value.status == 404


def test_the_paging_shape_per_vendor_is_the_one_the_data_flow_names():
    assert vocabulary.paging_mode("salesforce") == "query_locator"
    assert vocabulary.paging_mode("dataverse") == "odata_next_link"
    assert vocabulary.paging_mode("hubspot") == "paging_cursor"


def test_the_paging_fields_are_the_response_fields_the_research_quotes():
    assert vocabulary.PAGING_FIELDS["query_locator"] == {
        "total": "totalSize",
        "done": "done",
        "next": "nextRecordsUrl",
    }
    assert vocabulary.PAGING_FIELDS["odata_next_link"]["next"] == "@odata.nextLink"
    assert vocabulary.PAGING_FIELDS["paging_cursor"]["next"] == "paging.next.after"


def test_the_six_researched_vendor_numbers_are_published_exactly():
    assert vocabulary.SALESFORCE_SYNCHRONOUS_RECORD_LIMIT == 2000
    assert vocabulary.DATAVERSE_STANDARD_ROW_LIMIT == 5000
    assert vocabulary.DATAVERSE_ELASTIC_ROW_LIMIT == 500
    assert vocabulary.DATAVERSE_MAX_CONDITIONS == 500
    assert vocabulary.HUBSPOT_BATCH_READ_LIMIT == 100
    assert vocabulary.HUBSPOT_PAGE_LIMIT == 200
    assert vocabulary.HUBSPOT_QUERY_CHARACTER_LIMIT == 3000
    assert vocabulary.HUBSPOT_SEARCH_RESULT_LIMIT == 10000
    assert vocabulary.HUBSPOT_SEARCH_REQUESTS_PER_SECOND == 5


def test_only_dataverse_publishes_a_condition_ceiling():
    assert vocabulary.condition_limit("dataverse") == 500
    assert vocabulary.condition_limit("salesforce") is None
    assert vocabulary.condition_limit("hubspot") is None


def test_only_dataverse_can_annotate_a_display_label():
    assert vocabulary.supports_display_labels("dataverse") is True
    assert vocabulary.supports_display_labels("salesforce") is False
    assert vocabulary.supports_display_labels("hubspot") is False
    assert vocabulary.display_annotation_suffix("dataverse").endswith("FormattedValue")
    assert vocabulary.display_annotation_suffix("salesforce") == ""


def test_the_dataverse_prefer_header_is_the_researched_wording():
    assert vocabulary.DATAVERSE_DISPLAY_PREFERENCE == (
        'odata.include-annotations="OData.Community.Display.V1.FormattedValue"'
    )


def test_the_default_page_is_inside_every_ceiling_and_below_the_largest():
    for system in vocabulary.CRM_SYSTEMS:
        assert vocabulary.default_page(system) <= vocabulary.page_limit(system)
    assert vocabulary.DEFAULT_PAGE == 200


def test_a_short_ttl_is_clamped_rather_than_refused_and_the_clamp_is_bounded():
    assert vocabulary.clamp_ttl(None) == vocabulary.CACHE_TTL_SECONDS
    assert vocabulary.clamp_ttl(60) == 60
    assert vocabulary.clamp_ttl(5) == vocabulary.CACHE_TTL_MIN_SECONDS
    assert vocabulary.clamp_ttl(99999) == vocabulary.CACHE_TTL_MAX_SECONDS
    assert vocabulary.clamp_ttl("not a number") == vocabulary.CACHE_TTL_SECONDS


def test_the_refresh_mode_is_pull_and_the_alternative_is_recorded_not_taken():
    assert vocabulary.REFRESH_MODE == "pull"
    assert vocabulary.REFRESH_MODE_ALTERNATIVE == "change_tracking"


def test_the_identity_source_is_named_and_the_signed_token_claims_are_declared():
    assert vocabulary.IDENTITY_SOURCE == "room_mapping"
    assert vocabulary.IDENTITY_SOURCES == ("room_mapping", "signed_token")
    assert "iss" in vocabulary.SIGNED_TOKEN_CLAIMS
    assert "account_id" in vocabulary.SIGNED_TOKEN_CLAIMS


# --------------------------------------------------------------------------- #
# The error hierarchy
# --------------------------------------------------------------------------- #


def test_every_refusal_hangs_off_one_base_and_carries_its_own_status():
    refusals = [
        CrmIntegrationError,
        UnknownIdentity,
        UnknownObject,
        EmptyReadSet,
        FieldNotMapped,
        FieldMapError,
        DuplicateIdentity,
        DuplicateOptionSet,
        UnknownOptionSet,
        NoCrmIdentity,
        PageTooLarge,
        BatchTooLarge,
        TooManyConditions,
        QueryTooLong,
    ]
    for refusal in refusals:
        assert issubclass(refusal, CrmIntegrationError)
        assert isinstance(refusal.code, str) and refusal.code
        assert isinstance(refusal.status, int)
        assert 400 <= refusal.status < 600


def test_a_missing_row_is_404_and_a_conflict_is_409():
    assert UnknownIdentity.status == 404
    assert DuplicateIdentity.status == 409
    assert NoCrmIdentity.status == 404


# --------------------------------------------------------------------------- #
# The field map: the extensibility claim
# --------------------------------------------------------------------------- #


def test_the_default_map_covers_the_five_fields_the_user_flow_names():
    mapped = fieldmap.default_field_map("salesforce")
    assert "Name" in mapped["deal"]
    assert mapped["deal"]["StageName"] == "stage"
    assert mapped["deal"]["Amount"] == "amount"
    assert mapped["contact"]["Email"] == "contact_email"
    assert mapped["account"]["Industry"] == "account_industry"


def test_the_default_map_is_a_copy_so_a_caller_may_edit_it():
    first = fieldmap.default_field_map("salesforce")
    first["deal"]["Injected"] = "x"
    assert "Injected" not in fieldmap.default_field_map("salesforce")["deal"]


def test_the_read_set_is_derived_from_the_map_and_not_compiled_anywhere():
    mapped = {"deal": {"Name": "deal_name", "Amount": "amount"}}
    assert fieldmap.read_set(mapped, "salesforce", "deal") == ["Id", "Name", "Amount"]


def test_a_field_the_deployment_adds_reaches_the_read_set_with_no_api_change():
    """The extensibility sentence, as a test rather than a claim."""
    mapped = fieldmap.default_field_map("salesforce")
    mapped["deal"]["Region__c"] = "deal_region"
    assert "Region__c" in fieldmap.read_set(mapped, "salesforce", "deal")
    assert fieldmap.require_mapped(mapped, "deal", "deal_region") == "Region__c"
    assert {"object": "deal", "crm_field": "Region__c", "room_field": "deal_region"} in (
        fieldmap.panel_fields(mapped)
    )


def test_the_read_set_puts_the_vendor_id_column_first_and_dedupes_it():
    assert fieldmap.read_set({"deal": {"Id": "x", "Name": "n"}}, "salesforce", "deal") == [
        "Id",
        "Name",
    ]
    assert fieldmap.read_set({"deal": {"id": "x", "name": "n"}}, "dataverse", "deal") == [
        "id",
        "name",
    ]


def test_an_id_column_in_another_vendors_spelling_is_a_different_column():
    """`Id` on Dataverse is not `id`. Folding them would silently drop a field."""
    assert fieldmap.read_set({"deal": {"Id": "x", "Name": "n"}}, "dataverse", "deal") == [
        "id",
        "Id",
        "Name",
    ]


def test_a_map_that_selects_nothing_is_refused_rather_than_returning_nothing():
    with pytest.raises(EmptyReadSet) as caught:
        fieldmap.read_set({}, "salesforce", "deal")
    assert "indistinguishable from a room whose CRM has no deal" in str(caught.value)


def test_a_field_the_map_lacks_is_refused_with_the_object_and_the_field_named():
    with pytest.raises(FieldNotMapped) as caught:
        fieldmap.require_mapped(fieldmap.default_field_map("hubspot"), "deal", "deal_region")
    message = str(caught.value)
    assert "deal_region" in message and "deal" in message


def test_a_partial_map_is_accepted_because_three_objects_are_not_a_requirement():
    mapped = fieldmap.normalise_field_map({"deal": {"Name": "deal_name"}})
    assert mapped == {"deal": {"Name": "deal_name"}}


def test_a_field_map_with_a_bad_object_or_a_bad_entry_is_refused():
    with pytest.raises(UnknownObject):
        fieldmap.normalise_field_map({"lead": {"X": "y"}})
    with pytest.raises(FieldMapError):
        fieldmap.normalise_field_map({"deal": "not an object"})
    with pytest.raises(FieldMapError):
        fieldmap.normalise_field_map({"deal": {"Name": ""}})
    with pytest.raises(FieldMapError):
        fieldmap.normalise_field_map(["not", "an", "object"])


def test_an_empty_object_mapping_is_dropped_rather_than_stored_as_a_gap():
    assert fieldmap.normalise_field_map({"deal": {}}) == {}


def test_naming_a_vendor_validates_it_and_the_map_stays_vendor_independent():
    with pytest.raises(UnknownSystem):
        fieldmap.normalise_field_map({"deal": {"Name": "n"}}, system="sap")
    assert fieldmap.normalise_field_map({"deal": {"Name": "n"}}, system="dataverse") == {
        "deal": {"Name": "n"}
    }


def test_columns_a_vendor_returned_that_the_map_lacks_are_named_not_hidden():
    mapped = {"deal": {"Name": "deal_name"}}
    assert fieldmap.unmapped_columns(mapped, "deal", ["Name", "Secret", "Amount"]) == [
        "Amount",
        "Secret",
    ]


def test_room_fields_are_the_room_side_names_in_map_order_without_repeats():
    mapped = {"deal": {"Name": "deal_name", "StageName": "stage", "Amount": "deal_name"}}
    assert fieldmap.room_fields(mapped, "deal") == ["deal_name", "stage"]


def test_findings_name_the_objects_a_map_leaves_empty():
    notes = fieldmap.findings({"deal": {"Name": "deal_name"}}, "salesforce")
    joined = " ".join(notes)
    assert "contact" in joined and "account" in joined
    assert "deal" not in joined


def test_a_full_map_has_no_findings_to_report():
    assert fieldmap.findings(fieldmap.default_field_map("dataverse"), "dataverse") == []


# --------------------------------------------------------------------------- #
# The read query, per vendor
# --------------------------------------------------------------------------- #


def test_the_salesforce_plan_is_soql_over_the_mapped_columns_only():
    plan = query_rules.build_query(
        "salesforce",
        "deal",
        field_map=fieldmap.default_field_map("salesforce"),
        target_id="006-1",
        owner_id="005-dana",
    )
    assert plan.endpoint == "query"
    assert plan.method == "GET"
    assert plan.path == "/services/data/v61.0/query"
    soql = plan.query["q"]
    assert soql.startswith(
        "SELECT Id, Name, StageName, Amount, CloseDate, Probability FROM Opportunity"
    )
    assert "WHERE Id = '006-1' AND OwnerId = '005-dana'" in soql
    assert soql.endswith("LIMIT 200")


def test_the_salesforce_batch_size_goes_in_the_researched_query_options_header():
    plan = query_rules.build_query(
        "salesforce", "deal", field_map={"deal": {"Name": "n"}}, target_id="1", limit=50
    )
    assert plan.headers[query_rules.SALESFORCE_QUERY_OPTIONS_HEADER] == "batchSize=50"


def test_a_salesforce_query_never_asks_for_a_column_the_map_does_not_carry():
    plan = query_rules.build_query(
        "salesforce", "deal", field_map={"deal": {"Name": "deal_name"}}, target_id="1"
    )
    assert "StageName" not in plan.query["q"]
    assert plan.select == ("Id", "Name")


def test_a_query_locator_continues_the_read_instead_of_rebuilding_the_query():
    first = query_rules.build_query(
        "salesforce", "deal", field_map={"deal": {"Name": "n"}}, target_id="1"
    )
    response = sources._salesforce_response(first, [], 5, [])
    locator = response["nextRecordsUrl"].rsplit("/", 1)[-1]
    second = query_rules.build_query(
        "salesforce",
        "deal",
        field_map={"deal": {"Name": "n"}},
        target_id="1",
        continuation=locator,
    )
    assert second.endpoint == "query_more"
    assert second.path.endswith(f"/query-all/{locator}")
    assert second.query == {}


def test_the_dataverse_plan_uses_the_four_researched_query_options():
    plan = query_rules.build_query(
        "dataverse",
        "account",
        field_map=fieldmap.default_field_map("dataverse"),
        target_id="ac-1",
        owner_id="o-1",
    )
    assert plan.path == "/api/data/v9.2/accounts"
    assert plan.query["$select"] == "id,name,industrycode,address1_country"
    assert plan.query["$filter"] == "id eq 'ac-1' and ownerid eq 'o-1'"
    assert plan.query["$orderby"] == "name"
    assert plan.query["$top"] == "200"


def test_the_dataverse_plan_asks_for_formatted_values_only_when_it_can():
    asking = query_rules.build_query(
        "dataverse", "deal", field_map={"deal": {"name": "n"}}, target_id="1"
    )
    assert asking.headers["Prefer"] == vocabulary.DATAVERSE_DISPLAY_PREFERENCE
    declining = query_rules.build_query(
        "dataverse",
        "deal",
        field_map={"deal": {"name": "n"}},
        target_id="1",
        display_labels=False,
    )
    assert "Prefer" not in declining.headers


def test_a_dataverse_next_link_replaces_the_filter_rather_than_adding_to_it():
    plan = query_rules.build_query(
        "dataverse",
        "deal",
        field_map={"deal": {"name": "n"}},
        target_id="1",
        continuation="/api/data/v9.2/opportunities?$skiptoken=abc",
    )
    assert plan.endpoint == "entity_set"
    assert plan.path == "/api/data/v9.2/opportunities?$skiptoken=abc"
    assert "$filter" not in plan.query


def test_a_hubspot_read_by_record_id_is_a_batch_read_with_no_id_property():
    plan = query_rules.build_query(
        "hubspot", "contact", field_map={"contact": {"email": "contact_email"}}, target_id="ct-1"
    )
    assert plan.endpoint == "batch_read"
    assert plan.method == "POST"
    assert plan.path == "/crm/v3/objects/contacts/batch/read"
    assert plan.body == {"properties": ["email"], "inputs": [{"id": "ct-1"}]}


def test_a_hubspot_read_by_email_is_the_single_record_lookup_with_id_property():
    plan = query_rules.build_query(
        "hubspot",
        "contact",
        field_map={"contact": {"email": "contact_email"}},
        target_id="mei@example.test",
        id_property="email",
    )
    assert plan.endpoint == "get_by_email"
    assert plan.method == "GET"
    assert plan.path == "/crm/v3/objects/contacts/mei@example.test"
    assert plan.query["idProperty"] == "email"


def test_a_hubspot_read_with_nothing_resolved_is_a_search_with_a_filter_group():
    plan = query_rules.build_query(
        "hubspot", "deal", field_map={"deal": {"dealname": "deal_name"}}, owner_id="991"
    )
    assert plan.endpoint == "search"
    assert plan.path == "/crm/v3/objects/deals/search"
    assert plan.body["filterGroups"][0]["filters"] == [
        {"propertyName": "hubspot_owner_id", "operator": "EQ", "value": "991"}
    ]
    assert plan.body["limit"] == 200


def test_a_hubspot_condition_in_the_other_grammars_is_dropped_and_reported():
    plan = query_rules.build_query(
        "hubspot",
        "deal",
        field_map={"deal": {"dealname": "deal_name"}},
        conditions=["dealstage EQ contractsent", "StageName 'Negotiation'"],
    )
    # "not the right shape" is three tokens and would have parsed as a
    # filter. The rule is about a condition that is not in
    # `property operator value` shape at all.
    assert query_rules.dropped_conditions(plan) == ["StageName 'Negotiation'"]
    carried = plan.body["filterGroups"][0]["filters"]
    assert [row["propertyName"] for row in carried] == ["dealstage"]
    assert plan.body["filterGroups"][0]["filters"][0]["propertyName"] == "dealstage"


def test_no_other_vendor_reports_a_dropped_condition_because_it_uses_the_string():
    plan = query_rules.build_query(
        "salesforce", "deal", field_map={"deal": {"Name": "n"}}, conditions=["Name = 'x'"]
    )
    assert query_rules.dropped_conditions(plan) == []


def test_the_read_is_read_only_on_every_vendor():
    mapped = {"deal": {"Name": "n"}}
    for system, vendor_map in (
        ("salesforce", mapped),
        ("dataverse", {"deal": {"name": "n"}}),
        ("hubspot", {"deal": {"dealname": "n"}}),
    ):
        plan = query_rules.build_query(system, "deal", field_map=vendor_map, target_id="1")
        assert plan.as_dict()["read_only"] is True
        assert plan.endpoint in query_rules.READ_ONLY_ENDPOINTS
        assert "write" not in plan.path.lower()


def test_a_page_over_the_vendor_ceiling_is_clamped_not_refused():
    plan = query_rules.build_query(
        "salesforce",
        "deal",
        field_map={"deal": {"Name": "n"}},
        target_id="1",
        limit=99999,
    )
    assert plan.limit == vocabulary.SALESFORCE_SYNCHRONOUS_RECORD_LIMIT


def test_an_elastic_dataverse_table_gets_the_elastic_ceiling_not_the_standard_one():
    standard = query_rules.build_query(
        "dataverse",
        "deal",
        field_map={"deal": {"name": "n"}},
        target_id="1",
        limit=99999,
    )
    elastic = query_rules.build_query(
        "dataverse",
        "deal",
        field_map={"deal": {"name": "n"}},
        target_id="1",
        limit=99999,
        elastic=True,
    )
    assert standard.limit == vocabulary.DATAVERSE_STANDARD_ROW_LIMIT
    assert elastic.limit == vocabulary.DATAVERSE_ELASTIC_ROW_LIMIT
    default = query_rules.build_query(
        "dataverse", "deal", field_map={"deal": {"name": "n"}}, target_id="1", elastic=True
    )
    assert default.limit == vocabulary.DEFAULT_PAGE


def test_a_limit_of_zero_is_refused_because_a_caller_has_not_decided_yet():
    with pytest.raises(PageTooLarge) as caught:
        query_rules.build_query(
            "salesforce", "deal", field_map={"deal": {"Name": "n"}}, target_id="1", limit=0
        )
    assert "has not decided yet" in str(caught.value)


def test_more_than_500_dataverse_conditions_is_refused_in_the_vendor_wording():
    conditions = [f"name eq '{index}'" for index in range(501)]
    with pytest.raises(TooManyConditions) as caught:
        query_rules.build_query(
            "dataverse",
            "account",
            field_map={"account": {"name": "n"}},
            target_id="1",
            conditions=conditions,
        )
    assert str(vocabulary.DATAVERSE_MAX_CONDITIONS) in str(caught.value)
    assert "Number of conditions in query exceeded maximum limit." in str(caught.value)


def test_exactly_499_caller_conditions_plus_the_id_clause_is_the_vendor_ceiling():
    conditions = [f"name eq '{index}'" for index in range(499)]
    plan = query_rules.build_query(
        "dataverse",
        "account",
        field_map={"account": {"name": "n"}},
        target_id="1",
        conditions=conditions,
    )
    assert len(plan.conditions) == vocabulary.DATAVERSE_MAX_CONDITIONS


def test_a_caller_sending_500_conditions_has_asked_the_vendor_for_501():
    """The count is checked against the clauses the plan sends, not the ones it was given."""
    conditions = [f"name eq '{index}'" for index in range(500)]
    with pytest.raises(TooManyConditions) as caught:
        query_rules.build_query(
            "dataverse",
            "account",
            field_map={"account": {"name": "n"}},
            target_id="1",
            conditions=conditions,
        )
    assert "501 conditions" in str(caught.value)


def test_a_batch_larger_than_100_ids_is_refused_though_only_one_is_ever_built():
    """The guard exists for a caller that batches; this build reads one id at a time."""
    assert vocabulary.HUBSPOT_BATCH_READ_LIMIT == 100
    with pytest.raises(BatchTooLarge) as caught:
        query_rules._check_batch_size([{"id": str(index)} for index in range(101)])
    assert "100" in str(caught.value)
    query_rules._check_batch_size([{"id": str(index)} for index in range(100)])


def test_a_query_over_3000_characters_is_refused_with_the_vendor_number():
    body = {"properties": ["column_" + "x" * 40 for _ in range(80)]}
    assert len(json.dumps(body, separators=(",", ":"), sort_keys=True)) > 3000
    with pytest.raises(QueryTooLong) as caught:
        query_rules._check_query_length(body)
    assert str(vocabulary.HUBSPOT_QUERY_CHARACTER_LIMIT) in str(caught.value)


def test_a_query_under_the_limit_passes():
    query_rules._check_query_length({"properties": ["dealname"]})


def test_describe_reports_every_vendor_number_and_the_annotation():
    described = query_rules.describe()
    assert described["limits"]["salesforce"]["synchronous_records_per_request"] == 2000
    assert described["limits"]["dataverse"]["max_conditions"] == 500
    assert described["limits"]["hubspot"]["batch_read_ids"] == 100
    assert described["display_label_annotation"] == vocabulary.DATAVERSE_DISPLAY_ANNOTATION


# --------------------------------------------------------------------------- #
# The room's own copy of the vendor's tables
# --------------------------------------------------------------------------- #


def test_a_record_is_stored_with_its_system_object_and_fields():
    data = sources.normalise_record(
        {"system": "Salesforce", "object": "DEAL", "external_id": "006-1", "fields": {"Name": "x"}}
    )
    assert data == {
        "system": "salesforce",
        "object": "deal",
        "external_id": "006-1",
        "owner_id": "",
        "fields": {"Name": "x"},
        "labels": {},
        "modified": "",
        "email": "",
    }


def test_a_hubspot_owner_lifts_out_of_its_property_into_the_shared_column():
    data = sources.normalise_record(
        {"system": "hubspot", "object": "deal", "fields": {"hubspot_owner_id": "991"}}
    )
    assert data["owner_id"] == "991"


def test_a_record_derives_its_email_from_whichever_column_carries_it():
    assert (
        sources.normalise_record(
            {"system": "salesforce", "object": "contact", "fields": {"Email": "A@B.example"}}
        )["email"]
        == "a@b.example"
    )
    assert (
        sources.normalise_record(
            {"system": "salesforce", "object": "contact", "email": " C@D.example "}
        )["email"]
        == "c@d.example"
    )


def test_a_record_with_a_non_object_field_map_is_still_storable():
    data = sources.normalise_record({"system": "hubspot", "object": "deal", "fields": "nope"})
    assert data["fields"] == {}
    assert data["labels"] == {}


def test_a_record_with_an_unknown_system_or_object_is_refused():
    with pytest.raises(UnknownSystem):
        sources.normalise_record({"system": "sap", "object": "deal"})
    with pytest.raises(UnknownObject):
        sources.normalise_record({"system": "salesforce", "object": "lead"})


def test_a_vendor_table_is_readable_and_countable_per_system_and_object(engine: CrmReadEngine):
    add_rows(engine, "salesforce", SALESFORCE_TABLE)
    assert sources.table_size(engine.store, "salesforce", "deal") == 1
    assert sources.table_size(engine.store, "salesforce", "account") == 1
    assert sources.table_size(engine.store, "dataverse", "deal") == 0


def test_a_vendor_table_is_room_scoped_so_two_rooms_hold_their_own_rows(engine: CrmReadEngine):
    add_rows(engine, "salesforce", SALESFORCE_TABLE, room_id=ROOM)
    assert sources.table_size(engine.store, "salesforce", "deal", room_id=ROOM) == 1
    assert sources.table_size(engine.store, "salesforce", "deal", room_id=OTHER_ROOM) == 0
    assert sources.table_size(engine.store, "salesforce", "deal") == 1


def test_a_response_carries_only_the_columns_the_plan_selected(engine: CrmReadEngine):
    add_rows(engine, "salesforce", SALESFORCE_TABLE)
    plan = query_rules.build_query(
        "salesforce",
        "deal",
        field_map={"deal": {"Name": "deal_name"}},
        target_id="006-1",
    )
    response = sources.run_query(engine.store, plan, room_id=ROOM)
    row = response["records"][0]
    assert set(row) == {"attributes", "Id", "Name"}


def test_the_salesforce_response_uses_total_size_done_and_next_records_url(engine: CrmReadEngine):
    add_rows(engine, "salesforce", SALESFORCE_TABLE)
    mapped = {"deal": {"Name": "deal_name"}}
    first = query_rules.build_query(
        "salesforce", "deal", field_map=mapped, target_id="006-1", limit=1
    )
    response = sources.run_query(engine.store, first, room_id=ROOM)
    assert response["totalSize"] == 1
    assert response["done"] is True
    assert "nextRecordsUrl" not in response


def test_a_table_larger_than_the_page_answers_with_a_locator_and_done_false(engine: CrmReadEngine):
    for index in range(3):
        engine.declare_record(
            {
                "system": "salesforce",
                "object": "deal",
                "external_id": f"006-{index}",
                "owner_id": "005-dana",
                "fields": {"Name": f"Deal {index}"},
                "modified": f"2026-09-0{index + 1}T00:00:00+00:00",
            },
            room_id=ROOM,
            actor="test",
            source="test",
        )
    plan = query_rules.build_query(
        "salesforce", "deal", field_map={"deal": {"Name": "n"}}, owner_id="005-dana", limit=2
    )
    response = sources.run_query(engine.store, plan, room_id=ROOM)
    assert response["totalSize"] == 3
    assert response["done"] is False
    assert len(response["records"]) == 2
    locator = response["nextRecordsUrl"].rsplit("/", 1)[-1]
    assert locator.startswith("sa-deal-")
    assert locator.endswith("-3")
    # Deterministic: the same plan answers with the same locator on every run,
    # because a locator derived from a clock would make a plan irreproducible.
    again = sources.run_query(engine.store, plan, room_id=ROOM)
    assert again["nextRecordsUrl"] == response["nextRecordsUrl"]


def test_the_dataverse_response_adds_the_formatted_value_annotation_beside_the_value(
    engine: CrmReadEngine,
):
    add_rows(engine, "dataverse", DATAVERSE_TABLE)
    plan = query_rules.build_query(
        "dataverse", "deal", field_map={"deal": {"stepname": "stage"}}, target_id="op-1"
    )
    response = sources.run_query(engine.store, plan, room_id=ROOM)
    row = response["value"][0]
    assert row["stepname"] == "1"
    assert row[f"stepname@{vocabulary.DATAVERSE_DISPLAY_ANNOTATION}"] == "Proposal sent"
    assert response["@odata.count"] == 1
    assert "@odata.nextLink" not in response


def test_the_dataverse_response_omits_the_annotation_when_the_plan_did_not_ask(
    engine: CrmReadEngine,
):
    add_rows(engine, "dataverse", DATAVERSE_TABLE)
    plan = query_rules.build_query(
        "dataverse",
        "deal",
        field_map={"deal": {"stepname": "stage"}},
        target_id="op-1",
        display_labels=False,
    )
    response = sources.run_query(engine.store, plan, room_id=ROOM)
    assert f"stepname@{vocabulary.DATAVERSE_DISPLAY_ANNOTATION}" not in response["value"][0]


def test_a_dataverse_table_larger_than_the_page_answers_with_a_next_link(engine: CrmReadEngine):
    for index in range(3):
        engine.declare_record(
            {
                "system": "dataverse",
                "object": "deal",
                "owner_id": "o-1",
                "external_id": f"op-{index}",
                "fields": {"name": f"Deal {index}"},
                "modified": f"2026-09-0{index + 1}T00:00:00+00:00",
            },
            room_id=ROOM,
            actor="test",
            source="test",
        )
    plan = query_rules.build_query(
        "dataverse", "deal", field_map={"deal": {"name": "n"}}, owner_id="o-1", limit=2
    )
    response = sources.run_query(engine.store, plan, room_id=ROOM)
    assert "@odata.nextLink" in response
    assert "$skiptoken=" in response["@odata.nextLink"]


def test_a_hubspot_batch_read_lifts_properties_and_reports_no_next_page(engine: CrmReadEngine):
    add_rows(engine, "hubspot", HUBSPOT_TABLE)
    plan = query_rules.build_query(
        "hubspot", "deal", field_map={"deal": {"dealname": "deal_name"}}, target_id="dl-1"
    )
    response = sources.run_query(engine.store, plan, room_id=ROOM)
    assert response["status"] == "COMPLETE"
    assert response["results"][0]["properties"] == {"dealname": "Fabrikam workspace"}
    assert "paging" not in response


def test_a_hubspot_single_lookup_that_matches_nothing_says_not_found(engine: CrmReadEngine):
    add_rows(engine, "hubspot", HUBSPOT_TABLE)
    plan = query_rules.build_query(
        "hubspot",
        "contact",
        field_map={"contact": {"email": "contact_email"}},
        target_id="absent@example.test",
        id_property="email",
    )
    response = sources.run_query(engine.store, plan, room_id=ROOM)
    assert response["status"] == "NOT_FOUND"


def test_a_hubspot_search_reports_a_paging_cursor_when_the_page_is_short(engine: CrmReadEngine):
    for index in range(3):
        engine.declare_record(
            {
                "system": "hubspot",
                "object": "deal",
                "owner_id": "991",
                "external_id": f"dl-{index}",
                "fields": {"dealname": f"Deal {index}", "hubspot_owner_id": "991"},
                "modified": f"2026-09-0{index + 1}T00:00:00+00:00",
            },
            room_id=ROOM,
            actor="test",
            source="test",
        )
    plan = query_rules.build_query(
        "hubspot", "deal", field_map={"deal": {"dealname": "n"}}, owner_id="991", limit=2
    )
    response = sources.run_query(engine.store, plan, room_id=ROOM)
    assert response["total"] == 3
    assert response["paging"]["next"]["after"].startswith("hu-deal-")


def test_a_condition_the_room_wrote_in_another_grammar_is_reported_not_guessed(
    engine: CrmReadEngine,
):
    add_rows(engine, "salesforce", SALESFORCE_TABLE)
    plan = query_rules.build_query(
        "salesforce",
        "deal",
        field_map={"deal": {"Name": "n"}},
        target_id="006-1",
        conditions=["StageName = 'Proposal'"],
    )
    response = sources.run_query(engine.store, plan, room_id=ROOM)
    assert response["unappliedConditions"] == ["StageName = 'Proposal'"]


def test_the_clause_the_plan_built_itself_is_never_reported_as_unapplied(engine: CrmReadEngine):
    add_rows(engine, "salesforce", SALESFORCE_TABLE, owner="005-dana")
    plan = query_rules.build_query(
        "salesforce",
        "deal",
        field_map={"deal": {"Name": "n"}},
        owner_id="005-dana",
        target_id="006-1",
    )
    response = sources.run_query(engine.store, plan, room_id=ROOM)
    assert response.get("unappliedConditions", []) == []
    assert response["totalSize"] == 1


# --------------------------------------------------------------------------- #
# Normalising: three paging shapes into one view model
# --------------------------------------------------------------------------- #


def test_a_salesforce_page_becomes_a_view_model_with_the_locator_as_the_cursor():
    response = {
        "totalSize": 40,
        "done": False,
        "nextRecordsUrl": "/services/data/v61.0/query-all/01g5k00000abc",
        "records": [{"Id": "006-1", "Name": "Rollout"}],
    }
    view = normalize.normalise_response(
        "salesforce", "deal", response, {"deal": {"Name": "deal_name"}}, requested_limit=1
    )
    assert view["total"] == 40
    assert view["returned"] == 1
    assert view["done"] is False
    assert view["next"] == {"mode": "query_locator", "cursor": "01g5k00000abc"}
    assert view["records"][0]["fields"] == {"deal_name": "Rollout"}
    assert view["records"][0]["external_id"] == "006-1"


def test_the_cursor_is_the_locator_not_the_whole_url():
    """The locator, because a snapshot cached under a URL would send the next page to it."""
    response = {
        "totalSize": 40,
        "done": False,
        "nextRecordsUrl": "https://x.example/q/abc",
        "records": [],
    }
    view = normalize.normalise_response("salesforce", "deal", response, {}, requested_limit=1)
    assert view["next"]["cursor"] == "abc"


def test_a_dataverse_page_carries_its_formatted_value_into_the_labels():
    suffix = f"@{vocabulary.DATAVERSE_DISPLAY_ANNOTATION}"
    response = {
        "@odata.count": 1,
        "value": [{"id": "op-1", "stepname": "1", f"stepname{suffix}": "Proposal sent"}],
    }
    view = normalize.normalise_response(
        "dataverse", "deal", response, {"deal": {"stepname": "stage"}}
    )
    record = view["records"][0]
    assert record["fields"] == {"stage": "1"}
    assert record["labels"] == {"stage": "Proposal sent"}


def test_done_is_derived_from_the_page_length_when_the_vendor_publishes_none():
    short = normalize.normalise_response(
        "dataverse", "deal", {"@odata.count": 9, "value": [{"id": "1"}]}, {}, requested_limit=5
    )
    assert short["done"] is True
    long = normalize.normalise_response(
        "dataverse",
        "deal",
        {"@odata.count": 9, "value": [{"id": "1"}, {"id": "2"}]},
        {},
        requested_limit=2,
    )
    assert long["done"] is False
    assert len(long["records"]) == 2


def test_a_next_link_makes_a_dataverse_page_unfinished_whatever_its_length():
    view = normalize.normalise_response(
        "dataverse",
        "deal",
        {"@odata.count": 1, "value": [{"id": "1"}], "@odata.nextLink": "https://x.example/n"},
        {},
        requested_limit=5,
    )
    assert view["done"] is False
    assert view["next"]["cursor"] == "https://x.example/n"


def test_a_hubspot_search_page_uses_paging_next_after():
    view = normalize.normalise_response(
        "hubspot",
        "deal",
        {
            "total": 9,
            "results": [{"id": "dl-1", "properties": {"dealname": "W"}}],
            "paging": {"next": {"after": "42"}},
        },
        {"deal": {"dealname": "deal_name"}},
        requested_limit=1,
    )
    assert view["paging_mode"] == "paging_cursor"
    assert view["next"] == {"mode": "paging_cursor", "cursor": "42"}
    assert view["records"][0]["fields"] == {"deal_name": "W"}


def test_a_hubspot_single_record_response_is_read_as_one_record_not_zero():
    view = normalize.normalise_response(
        "hubspot",
        "contact",
        {"id": "ct-1", "properties": {"email": "a@b.example"}, "status": "COMPLETE"},
        {"contact": {"email": "contact_email"}},
    )
    assert view["returned"] == 1
    assert view["records"][0]["fields"] == {"contact_email": "a@b.example"}
    assert view["records"][0]["external_id"] == "ct-1"


def test_a_vendor_response_larger_than_the_vendor_result_ceiling_is_marked_truncated():
    response = {"totalSize": 9000, "done": True, "records": []}
    view = normalize.normalise_response("salesforce", "deal", response, {"deal": {"Name": "n"}})
    assert view["total"] == vocabulary.SALESFORCE_SYNCHRONOUS_RECORD_LIMIT
    assert view["total_reported"] == 9000
    assert view["truncated_at_vendor_limit"] is True


def test_the_vendor_id_and_metadata_are_not_reported_as_unmapped_columns():
    response = {
        "totalSize": 1,
        "done": True,
        "records": [{"attributes": {"type": "Opportunity"}, "Id": "1", "Name": "x"}],
    }
    view = normalize.normalise_response("salesforce", "deal", response, {"deal": {"Name": "n"}})
    assert view["unmapped_returned"] == []


def test_a_column_the_map_lacks_is_reported_on_the_view_model():
    response = {
        "totalSize": 1,
        "done": True,
        "records": [{"Id": "1", "Name": "x", "Region__c": "EMEA"}],
    }
    view = normalize.normalise_response(
        "salesforce", "deal", response, {"deal": {"Name": "deal_name"}}
    )
    assert view["unmapped_returned"] == ["Region__c"]
    assert view["records"][0]["unmapped"] == ["Region__c"]


def test_pages_merge_into_one_view_model_without_repeating_a_row():
    first = {
        "total": 3,
        "total_reported": 3,
        "done": False,
        "next": {"mode": "query_locator", "cursor": "loc"},
        "records": [{"external_id": "1", "fields": {}, "labels": {}, "unmapped": []}],
        "pages": 1,
        "truncated_at_vendor_limit": False,
        "unapplied_conditions": ["x"],
    }
    second = {
        **first,
        "done": True,
        "next": None,
        "records": [
            {"external_id": "1", "fields": {}, "labels": {}, "unmapped": []},
            {"external_id": "2", "fields": {}, "labels": {}, "unmapped": []},
        ],
    }
    merged = normalize.merge_pages([first, second])
    assert len(merged["records"]) == 2
    assert merged["pages"] == 2
    assert merged["done"] is True
    assert merged["next"] is None
    assert merged["total"] == 3


def test_merging_no_pages_is_an_empty_complete_read():
    merged = normalize.merge_pages([])
    assert merged == {
        "total": 0,
        "returned": 0,
        "done": True,
        "next": None,
        "stalled": False,
        "records": [],
        "pages": 0,
        "truncated_at_vendor_limit": False,
        "unapplied_conditions": [],
    }


def test_the_panel_reports_a_missing_part_rather_than_omitting_it():
    panel = normalize.deal_panel("salesforce", {"buyer_email": "a@b.example"}, {}, {})
    assert panel["deal"]["found"] is False
    assert panel["contact"]["found"] is False
    assert panel["account"]["found"] is False
    assert panel["objects_read"] == []


def test_the_panel_reads_the_five_researched_fields_by_their_room_names():
    per_object = {
        "deal": {
            "records": [
                {
                    "external_id": "006-1",
                    "fields": {"deal_name": "Rollout", "stage": "2", "amount": 48000},
                    "labels": {"stage": "Proposal sent"},
                    "unmapped": [],
                }
            ]
        },
        "contact": {
            "records": [
                {
                    "external_id": "003-1",
                    "fields": {"contact_name": "Dana", "contact_title": "VP"},
                    "labels": {},
                    "unmapped": [],
                }
            ]
        },
        "account": {
            "records": [
                {
                    "external_id": "001-1",
                    "fields": {"account_name": "Northwind", "account_industry": "Manufacturing"},
                    "labels": {},
                    "unmapped": [],
                }
            ]
        },
    }
    panel = normalize.deal_panel(
        "salesforce", {"buyer_email": "a@b.example"}, per_object, {"deal": {"Name": "deal_name"}}
    )
    assert panel["deal"]["name"] == "Rollout"
    assert panel["deal"]["stage"] == {"value": "2", "label": "Proposal sent"}
    assert panel["deal"]["amount"] == 48000
    assert panel["contact"]["name"] == "Dana"
    assert panel["account"]["industry"]["value"] == "Manufacturing"
    assert panel["deal"]["found"] is True


# --------------------------------------------------------------------------- #
# Display labels: the capability check and its fallback
# --------------------------------------------------------------------------- #


def test_an_option_set_is_stored_with_its_values_as_strings():
    data = labels.normalise(
        {"system": "salesforce", "object": "deal", "field": "stage", "values": {2: "Proposal sent"}}
    )
    assert data["values"] == {"2": "Proposal sent"}


def test_an_option_set_on_an_unlabellable_field_or_with_no_values_is_refused():
    with pytest.raises(UnknownOptionSet) as caught:
        labels.normalise(
            {"system": "salesforce", "object": "deal", "field": "deal_name", "values": {"a": "b"}}
        )
    assert "stage" in str(caught.value)
    with pytest.raises(UnknownOptionSet):
        labels.normalise({"system": "salesforce", "object": "deal", "field": "stage", "values": {}})
    with pytest.raises(UnknownOptionSet):
        labels.normalise(
            {"system": "salesforce", "object": "deal", "field": "stage", "values": "no"}
        )


def test_a_global_option_set_reaches_a_room_and_a_room_may_override_it(engine: CrmReadEngine):
    global_set = engine.register_option_set(
        {"system": "salesforce", "object": "deal", "field": "stage", "values": {"A": "global A"}},
        room_id=None,
        actor="test",
        source="test",
    )
    engine.register_option_set(
        {"system": "salesforce", "object": "deal", "field": "stage", "values": {"A": "room A"}},
        room_id=ROOM,
        actor="test",
        source="test",
    )
    collected = labels.fallback_labels(engine.store, "salesforce", "deal", ROOM)
    assert collected["stage"]["A"] == "room A"
    assert global_set["id"]


def test_a_second_option_set_at_the_same_scope_is_refused(engine: CrmReadEngine):
    stage_option_set(engine)
    with pytest.raises(DuplicateOptionSet) as caught:
        stage_option_set(engine)
    assert "already" in str(caught.value)


def test_one_option_set_can_be_read_back_and_a_missing_one_refuses(engine: CrmReadEngine):
    created = stage_option_set(engine)
    found = labels.option_set(engine.store, "salesforce", "deal", "stage")
    assert found["id"] == created["id"]
    with pytest.raises(UnknownOptionSet) as caught:
        labels.option_set(engine.store, "salesforce", "deal", "status")
    assert "no option set is registered" in str(caught.value)


def test_a_label_is_found_for_an_integer_value_because_vendors_disagree_on_the_type():
    assert labels.label_for({"2": "Proposal sent"}, 2) == "Proposal sent"
    assert labels.label_for({"2": "Proposal sent"}, "2") == "Proposal sent"
    assert labels.label_for({"2": "Proposal sent"}, None) == ""
    assert labels.label_for({"2": "Proposal sent"}, 9) == ""


def test_the_fallback_fills_only_what_the_vendor_did_not_label():
    records = [
        {"fields": {"stage": "2"}, "labels": {"stage": "vendor label"}},
        {"fields": {"stage": "1"}, "labels": {}},
    ]
    filled, fills = labels.apply_fallback(records, {"stage": {"1": "Proposal sent"}})
    assert filled[0]["labels"]["stage"] == "vendor label"
    assert filled[1]["labels"]["stage"] == "Proposal sent"
    assert fills == ["stage"]


def test_the_fallback_leaves_a_field_the_record_does_not_carry_alone():
    records = [{"fields": {"amount": 1}, "labels": {}}]
    _filled, fills = labels.apply_fallback(records, {"stage": {"1": "x"}})
    assert fills == []


def test_option_fields_still_showing_a_raw_value_are_reported_with_their_row_count():
    records = [
        {"fields": {"stage": "1", "account_industry": "M"}, "labels": {}},
        {"fields": {"stage": "2"}, "labels": {}},
        {"fields": {"deal_name": "x"}, "labels": {}},
    ]
    assert labels.unlabelled(records) == [
        {"field": "account_industry", "rows": 1},
        {"field": "stage", "rows": 2},
    ]


def test_no_option_set_leaves_an_empty_map_rather_than_an_error(engine: CrmReadEngine):
    assert labels.fallback_labels(engine.store, "hubspot", "deal", ROOM) == {}


def test_the_capability_report_says_which_source_each_vendor_uses():
    dataverse = labels.capability("dataverse")
    assert dataverse["display_label_annotations"] is True
    assert dataverse["preference_header"] == {"Prefer": vocabulary.DATAVERSE_DISPLAY_PREFERENCE}
    salesforce = labels.capability("salesforce")
    assert salesforce["display_label_annotations"] is False
    assert salesforce["preference_header"] == {}
    assert "option-set map" in salesforce["why"]
    assert salesforce["labellable_fields"] == list(labels.LABELLABLE_FIELDS)


# --------------------------------------------------------------------------- #
# The cache: four states and the TTL between them
# --------------------------------------------------------------------------- #


def test_an_unreadable_timestamp_is_reported_as_unknown_age_not_as_zero():
    assert cache.parse_iso("not a timestamp") is None
    assert cache.parse_iso(None) is None
    assert cache.age_seconds({"read_at": "not a timestamp"}, NOW) is None
    assert cache.age_seconds({"read_at": NOW.isoformat()}, NOW) == 0.0


def test_a_naive_timestamp_is_read_as_utc_rather_than_refused():
    assert cache.parse_iso("2026-10-04T12:00:00") == NOW


def test_the_four_cache_states_are_reachable_and_each_means_something():
    assert cache.state(None, NOW) == "absent"
    fresh = {"data": {"read_at": NOW.isoformat(), "ttl_seconds": 300, "outcome": "complete"}}
    assert cache.state(fresh, NOW) == "fresh"
    old = {
        "data": {
            "read_at": (NOW - timedelta(seconds=600)).isoformat(),
            "ttl_seconds": 300,
            "outcome": "complete",
        }
    }
    assert cache.state(old, NOW) == "expired"
    partial = {"data": {"read_at": NOW.isoformat(), "ttl_seconds": 300, "outcome": "paged"}}
    assert cache.state(partial, NOW) == "stale"
    unreadable = {
        "data": {"read_at": "rubbish", "ttl_seconds": 300, "outcome": "complete"},
    }
    assert cache.state(unreadable, NOW) == "expired"
    assert cache.state({"data": {"read_at": NOW.isoformat()}}, NOW) == "stale"


def test_a_stale_read_is_never_fresh_even_when_it_is_brand_new():
    record = {"data": {"read_at": NOW.isoformat(), "ttl_seconds": 300, "outcome": "paged"}}
    assert cache.is_fresh(record, NOW) is False


def test_expiry_is_the_read_plus_the_ttl():
    data = {"read_at": NOW.isoformat(), "ttl_seconds": 120}
    assert cache.expires_at(data, NOW) == (NOW + timedelta(seconds=120)).isoformat()
    assert cache.expires_at({"ttl_seconds": 120}, NOW) is None


def test_the_ttl_a_snapshot_was_written_with_is_bounded_to_the_short_range():
    assert cache.ttl_of({"ttl_seconds": 99999}) == vocabulary.CACHE_TTL_MAX_SECONDS
    assert cache.ttl_of({}) == vocabulary.CACHE_TTL_SECONDS


def test_a_snapshot_records_how_it_was_built_beside_the_panel():
    snapshot = cache.build_snapshot(
        {
            "id": "id-1",
            "data": {"system": "salesforce", "buyer_email": "a@b.example", "ttl_seconds": 60},
        },
        {"deal": {}},
        {"deal": {"returned": 1}},
        outcome="capability_fallback",
        now=NOW,
        fills=["deal.stage"],
        unlabelled=[{"object": "deal", "field": "stage", "rows": 1}],
    )
    assert snapshot["identity_id"] == "id-1"
    assert snapshot["read_at"] == NOW.isoformat()
    assert snapshot["ttl_seconds"] == 60
    assert snapshot["refresh_mode"] == "pull"
    assert snapshot["fallback_fills"] == ["deal.stage"]
    assert snapshot["unlabelled"]


def test_describing_an_absent_cache_says_so_and_still_reports_the_bounds():
    described = cache.describe_cache(None, NOW)
    assert described["state"] == "absent"
    assert described["served_from_cache"] is False
    assert described["ttl_bounds"] == [30, 3600]
    assert described["refresh_alternative"] == "change_tracking"


def test_describing_a_present_cache_reports_the_age_and_the_state():
    record = {"data": {"read_at": NOW.isoformat(), "ttl_seconds": 300, "outcome": "complete"}}
    described = cache.describe_cache(record, NOW)
    assert described["state"] == "fresh"
    assert described["served_from_cache"] is True
    assert described["age_seconds"] == 0.0


def test_the_cache_summary_counts_every_state_even_the_empty_ones(engine: CrmReadEngine):
    summary = cache.cache_summary(engine.store, ROOM, NOW)
    assert summary["absent"] == 0
    assert set(summary) >= set(vocabulary.CACHE_STATES)


# --------------------------------------------------------------------------- #
# Identity: the research's step two
# --------------------------------------------------------------------------- #


def test_an_identity_is_normalised_into_the_read_scope_it_uses():
    data = identity.normalise(
        {"system": "Salesforce", "email": " A@B.example ", "deal_id": "006-1"}
    )
    assert data["system"] == "salesforce"
    assert data["buyer_email"] == "A@B.example"
    assert data["deal_id"] == "006-1"
    assert data["source"] == "room_mapping"


def test_an_identity_without_a_buyer_email_is_refused_because_that_is_the_key():
    with pytest.raises(MissingBuyerEmail) as caught:
        identity.normalise({"system": "salesforce"})
    assert "buyer_email is required" in str(caught.value)
    assert caught.value.status == 400


def test_an_identity_with_a_system_outside_the_research_is_refused():
    with pytest.raises(UnknownSystem):
        identity.normalise({"system": "sap", "buyer_email": "a@b.example"})


def test_the_display_label_flag_defaults_to_the_vendor_and_can_be_pinned():
    assert (
        identity.normalise({"system": "dataverse", "buyer_email": "a@b"})["display_labels"] is True
    )
    assert (
        identity.normalise({"system": "hubspot", "buyer_email": "a@b"})["display_labels"] is False
    )
    pinned = identity.normalise(
        {"system": "dataverse", "buyer_email": "a@b", "display_labels": False}
    )
    assert pinned["display_labels"] is False


def test_a_field_this_build_does_not_know_survives_the_identity():
    data = identity.normalise(
        {"system": "salesforce", "buyer_email": "a@b.example", "crm_region__c": "EMEA"}
    )
    assert data["crm_region__c"] == "EMEA"


def test_a_second_identity_for_one_buyer_on_one_vendor_is_refused(engine: CrmReadEngine):
    add_identity(engine)
    with pytest.raises(DuplicateIdentity) as caught:
        add_identity(engine, deal="006-2")
    assert "Patch it instead" in str(caught.value)


def test_the_same_buyer_on_another_vendor_is_not_a_duplicate(engine: CrmReadEngine):
    add_identity(engine)
    add_identity(engine, system="dataverse")
    assert len(engine.identities(ROOM)) == 2


def test_an_identity_in_another_room_is_not_a_duplicate(engine: CrmReadEngine):
    add_identity(engine, room_id=ROOM)
    add_identity(engine, room_id=OTHER_ROOM)
    assert len(engine.identities(ROOM)) == 1
    assert len(engine.identities(OTHER_ROOM)) == 1


def test_resolution_by_email_is_case_folded_because_a_buyer_types_their_own_address(
    engine: CrmReadEngine,
):
    add_identity(engine, email="Dana@Northwind.example")
    found = identity.resolve(engine.store, ROOM, buyer_email="dana@northwind.EXAMPLE")
    assert found is not None


def test_resolution_returns_none_rather_than_raising_for_a_room_without_crm_context(
    engine: CrmReadEngine,
):
    assert identity.resolve(engine.store, ROOM, buyer_email="nobody@example.test") is None
    assert identity.resolve(engine.store, ROOM) is None
    assert identity.resolve(engine.store, ROOM, identity_id="absent") is None


def test_resolution_refuses_an_identity_belonging_to_another_room(engine: CrmReadEngine):
    created = add_identity(engine, room_id=OTHER_ROOM)
    assert identity.resolve(engine.store, ROOM, identity_id=created["id"]) is None


def test_requiring_an_identity_refuses_when_there_is_none(engine: CrmReadEngine):
    with pytest.raises(NoCrmIdentity) as caught:
        identity.require(engine.store, ROOM, buyer_email="nobody@example.test")
    assert "without CRM context" in str(caught.value)
    with pytest.raises(UnknownIdentity):
        identity.require(engine.store, ROOM, identity_id="absent")


def test_a_read_naming_no_buyer_takes_the_rooms_only_identity(engine: CrmReadEngine):
    created = add_identity(engine)
    assert identity.resolve(engine.store, ROOM) is None
    assert identity.resolve_or_refuse(engine.store, ROOM)["id"] == created["id"]


def test_a_read_naming_no_buyer_in_a_room_with_two_is_refused_not_guessed(
    engine: CrmReadEngine,
):
    add_identity(engine, email="a@northwind.example")
    add_identity(engine, email="b@northwind.example")
    with pytest.raises(AmbiguousIdentity) as caught:
        identity.resolve_or_refuse(engine.store, ROOM)
    assert "2 CRM identities" in str(caught.value)
    assert "?identity_id=" in str(caught.value)


def test_a_room_with_no_identity_at_all_resolves_to_none_rather_than_refusing(
    engine: CrmReadEngine,
):
    assert identity.resolve_or_refuse(engine.store, ROOM) is None


def test_an_explicit_buyer_beats_the_only_identity_rule(engine: CrmReadEngine):
    """Naming a buyer is never ambiguous, even when the room has one identity."""
    add_identity(engine)
    assert identity.resolve_or_refuse(engine.store, ROOM, buyer_email="absent@example.test") is None


def test_an_identity_can_be_read_back_with_its_capability_flags(engine: CrmReadEngine):
    created = add_identity(engine)
    view = identity.identity_view(engine.store.get(created["id"]))
    assert view["identity_source"] == "room_mapping"
    assert view["display_label_capability"]["dataverse"] is True
    assert view["objects"] == ["deal", "contact", "account"]


# --------------------------------------------------------------------------- #
# Inferences: every judgement call, named
# --------------------------------------------------------------------------- #


def test_every_inference_states_a_decision_a_reason_and_its_source():
    for entry in inferences.INFERENCES:
        assert entry["decision"]
        assert entry["question"]
        assert entry["because"]
        assert entry["source"].startswith("docs/research/")


def test_the_four_edges_the_research_left_open_are_all_served():
    decisions = {entry["decision"] for entry in inferences.INFERENCES}
    assert "room_mapping" in decisions
    assert vocabulary.CACHE_TTL_SECONDS in decisions
    assert "pull" in decisions
    assert "derive_done_from_page_length" in decisions
    assert "local_vendor_tables" in decisions
    assert vocabulary.DEFAULT_PAGE in decisions
    assert "not_built" in decisions


def test_the_signed_token_source_is_named_as_rejected_with_its_claims():
    entry = next(item for item in inferences.INFERENCES if item["decision"] == "room_mapping")
    assert entry["rejected"] == "signed_token"
    assert "sub" in entry["claims_a_signed_token_would_need"]


def test_the_researchs_own_gap_is_carried_rather_than_rediscovered():
    entry = next(item for item in inferences.INFERENCES if item["decision"] == "not_built")
    assert "$skip" in entry["quoted"]
    assert "this is the room's read path" in entry["because"]
    assert "$skip is not used because" in entry["because"]


def test_describe_splits_the_sourced_half_from_the_inferred_half():
    described = inferences.describe()
    assert described["count"] == len(inferences.INFERENCES)
    assert "field-scoped" in described["sourced"]["field_scoped_read"]
    assert "Proposal sent" in described["sourced"]["display_labels"]


# --------------------------------------------------------------------------- #
# The engine: the whole flow
# --------------------------------------------------------------------------- #


def test_the_vocabulary_route_serves_everything_a_client_renders_from(engine: CrmReadEngine):
    served = engine.vocabulary()
    assert served["systems"] == list(vocabulary.CRM_SYSTEMS)
    assert served["objects"] == list(vocabulary.CRM_OBJECTS)
    assert served["display_labels"] == vocabulary.DISPLAY_LABEL_CAPABILITY
    assert served["paging"]["modes"] == vocabulary.PAGING_MODES
    assert served["collections"] == list(OWNED_COLLECTIONS)
    assert served["cache_states"] == list(vocabulary.CACHE_STATES)
    assert served["limits"]["hubspot"]["query_characters"] == 3000


def test_the_vendor_route_reports_capability_and_limits_per_vendor(engine: CrmReadEngine):
    served = engine.vendors()
    assert served["count"] == 3
    by_system = {row["system"]: row for row in served["vendors"]}
    assert by_system["dataverse"]["capability"]["display_label_annotations"] is True
    assert by_system["salesforce"]["paging_mode"] == "query_locator"
    assert by_system["hubspot"]["limits"]["search_results"] == 10000


def test_a_full_salesforce_pull_produces_the_five_researched_panel_fields(engine: CrmReadEngine):
    created = add_identity(engine)
    add_rows(engine, "salesforce", SALESFORCE_TABLE)
    result = engine.pull(ROOM, identity_id=created["id"], now=NOW, actor="t", source="test")
    panel = result["panel"]
    assert panel["deal"]["name"] == "Northwind rollout"
    assert panel["deal"]["amount"] == 48000
    assert panel["contact"]["name"] == "Dana Okafor"
    assert panel["account"]["name"] == "Northwind Traders"
    assert panel["account"]["industry"]["value"] == "Manufacturing"
    assert result["crm_context"] is True
    assert result["refreshed"] is True
    assert result["snapshot_id"]


def test_a_stage_option_is_labelled_from_the_room_own_map_when_the_vendor_cannot(
    engine: CrmReadEngine,
):
    stage_option_set(engine)
    created = add_identity(engine)
    add_rows(engine, "salesforce", SALESFORCE_TABLE)
    result = engine.pull(ROOM, identity_id=created["id"], now=NOW, actor="t", source="test")
    assert result["panel"]["deal"]["stage"] == {"value": "Negotiation", "label": "In negotiation"}
    assert result["label_source"] == "room option sets"
    assert result["fallback_fills"] == ["deal.stage"]
    assert result["outcome"] == "capability_fallback"


def test_a_dataverse_stage_is_labelled_from_the_vendor_own_annotation(engine: CrmReadEngine):
    created = add_identity(
        engine,
        system="dataverse",
        email="raj@contoso.example",
        deal="op-1",
        contact="ct-1",
        account="ac-1",
    )
    add_rows(engine, "dataverse", DATAVERSE_TABLE)
    result = engine.pull(ROOM, identity_id=created["id"], now=NOW, actor="t", source="test")
    assert result["display_labels"] is True
    assert result["label_source"] == "vendor annotations"
    assert result["panel"]["deal"]["stage"] == {"value": "1", "label": "Proposal sent"}
    assert result["fallback_fills"] == []
    assert result["outcome"] == "complete"


def test_a_hubspot_stage_is_labelled_from_the_room_own_map(engine: CrmReadEngine):
    engine.register_option_set(
        {
            "system": "hubspot",
            "object": "deal",
            "field": "stage",
            "values": {"contractsent": "Contract sent"},
        },
        room_id=None,
        actor="t",
        source="test",
    )
    created = add_identity(
        engine,
        system="hubspot",
        email="mei@fabrikam.example",
        deal="dl-1",
        contact="ct-1",
        account="co-1",
    )
    add_rows(engine, "hubspot", HUBSPOT_TABLE)
    result = engine.pull(ROOM, identity_id=created["id"], now=NOW, actor="t", source="test")
    assert result["panel"]["deal"]["name"] == "Fabrikam workspace"
    assert result["panel"]["deal"]["stage"]["label"] == "Contract sent"


def test_a_contact_resolved_by_email_uses_the_single_record_lookup(engine: CrmReadEngine):
    created = add_identity(
        engine,
        system="hubspot",
        email="mei@fabrikam.example",
        deal="dl-1",
        contact="",
        account="co-1",
    )
    add_rows(engine, "hubspot", HUBSPOT_TABLE)
    result = engine.pull(ROOM, identity_id=created["id"], now=NOW, actor="t", source="test")
    contact_plan = next(q for q in result["queries"] if q["object"] == "contact")
    assert contact_plan["endpoint"] == "get_by_email"
    assert contact_plan["query"]["idProperty"] == "email"
    assert result["panel"]["contact"]["found"] is True


def test_an_owner_with_no_record_id_reads_by_owner_rather_than_the_whole_table(
    engine: CrmReadEngine,
):
    add_rows(engine, "salesforce", SALESFORCE_TABLE, owner="005-dana")
    add_rows(engine, "salesforce", SALESFORCE_TABLE, room_id=OTHER_ROOM)
    created = add_identity(engine, deal="", contact="", account="", owner="005-dana")
    result = engine.pull(ROOM, identity_id=created["id"], now=NOW, actor="t", source="test")
    deal_plan = next(q for q in result["queries"] if q["object"] == "deal")
    assert "OwnerId = '005-dana'" in deal_plan["query"]["q"]
    assert result["panel"]["deal"]["found"] is True


def test_a_contact_with_no_record_id_is_read_by_the_buyers_email(engine: CrmReadEngine):
    add_rows(engine, "salesforce", SALESFORCE_TABLE)
    created = add_identity(engine, deal="006-1", contact="", account="", owner="")
    result = engine.pull(ROOM, identity_id=created["id"], now=NOW, actor="t", source="test")
    contact_plan = next(q for q in result["queries"] if q["object"] == "contact")
    assert "Email = 'dana@northwind.example'" in contact_plan["query"]["q"]
    assert result["panel"]["contact"]["found"] is True


def test_an_object_with_no_id_and_no_owner_is_reported_unfound_not_filled_with_the_table(
    engine: CrmReadEngine,
):
    add_rows(engine, "salesforce", SALESFORCE_TABLE)
    created = add_identity(engine, deal="006-1", contact="003-1", account="", owner="")
    result = engine.pull(ROOM, identity_id=created["id"], now=NOW, actor="t", source="test")
    assert result["panel"]["account"]["found"] is False
    assert "account" not in result["reads"]
    assert "account" not in [q["object"] for q in result["queries"]]


def test_a_column_added_to_the_identity_map_reaches_the_panel_with_no_api_change(
    engine: CrmReadEngine,
):
    mapped = fieldmap.default_field_map("salesforce")
    mapped["deal"]["Region__c"] = "deal_region"
    created = add_identity(engine, field_map=mapped)
    add_rows(engine, "salesforce", SALESFORCE_TABLE)
    engine.declare_record(
        {
            "system": "salesforce",
            "object": "deal",
            "external_id": "006-1",
            "fields": {"Name": "Northwind rollout", "Region__c": "EMEA"},
        },
        room_id=ROOM,
        actor="t",
        source="test",
    )
    result = engine.pull(ROOM, identity_id=created["id"], now=NOW, actor="t", source="test")
    assert "Region__c" in result["reads"]["deal"]["read_set"]
    assert {"object": "deal", "crm_field": "Region__c", "room_field": "deal_region"} in (
        result["panel"]["read_set"]
    )


def test_a_pull_writes_one_query_log_row_per_object_it_read(engine: CrmReadEngine):
    created = add_identity(engine)
    add_rows(engine, "salesforce", SALESFORCE_TABLE)
    engine.pull(ROOM, identity_id=created["id"], now=NOW, actor="t", source="test")
    logged = engine.queries(ROOM)
    assert {row["object"] for row in logged} == {"deal", "contact", "account"}
    assert all(row["read_only"] is True for row in logged)
    assert all(row["identity_id"] == created["id"] for row in logged)
    assert all(row["vendor_name"] for row in logged)


def test_the_query_log_can_be_filtered_by_object_through_the_dynamic_index(
    engine: CrmReadEngine,
):
    created = add_identity(engine)
    add_rows(engine, "salesforce", SALESFORCE_TABLE)
    engine.pull(ROOM, identity_id=created["id"], now=NOW, actor="t", source="test")
    assert [row["object"] for row in engine.queries(ROOM, "deal")] == ["deal"]
    # An object the panel does not read is refused by name rather than silently
    # returning nothing, because "nothing" would look like "nothing was read".
    with pytest.raises(UnknownObject):
        engine.queries(ROOM, "lead")


def test_the_query_log_is_room_scoped(engine: CrmReadEngine):
    created = add_identity(engine, room_id=OTHER_ROOM)
    add_rows(engine, "salesforce", SALESFORCE_TABLE, room_id=OTHER_ROOM)
    engine.pull(OTHER_ROOM, identity_id=created["id"], now=NOW, actor="t", source="test")
    assert engine.queries(ROOM) == []
    assert len(engine.queries(OTHER_ROOM)) == 3


def test_a_second_read_inside_the_ttl_serves_from_cache_without_reading_again(
    engine: CrmReadEngine,
):
    created = add_identity(engine)
    add_rows(engine, "salesforce", SALESFORCE_TABLE)
    engine.pull(ROOM, identity_id=created["id"], now=NOW, actor="t", source="test")
    served = engine.read_panel(ROOM, identity_id=created["id"], now=NOW, actor="t", source="test")
    assert served["refreshed"] is False
    assert served["cache"]["state"] == "fresh"
    assert served["cache"]["served_from_cache"] is True
    assert len(engine.queries(ROOM)) == 3


def test_a_refresh_forces_the_read_even_inside_the_ttl(engine: CrmReadEngine):
    created = add_identity(engine)
    add_rows(engine, "salesforce", SALESFORCE_TABLE)
    engine.pull(ROOM, identity_id=created["id"], now=NOW, actor="t", source="test")
    forced = engine.read_panel(
        ROOM, identity_id=created["id"], refresh=True, now=NOW, actor="t", source="test"
    )
    assert forced["refreshed"] is True
    assert forced["refresh_was_forced"] is True
    assert len(engine.queries(ROOM)) == 6


def test_a_snapshot_past_its_ttl_is_read_again(engine: CrmReadEngine):
    created = add_identity(engine)
    add_rows(engine, "salesforce", SALESFORCE_TABLE)
    engine.pull(ROOM, identity_id=created["id"], now=NOW, actor="t", source="test")
    later = NOW + timedelta(seconds=vocabulary.CACHE_TTL_SECONDS + 1)
    served = engine.read_panel(ROOM, identity_id=created["id"], now=later, actor="t", source="test")
    assert served["refreshed"] is True
    assert served["cache"]["state"] == "fresh"
    assert served["cache"]["age_seconds"] == 0.0


def test_a_room_with_no_crm_context_is_a_panel_not_a_failure(engine: CrmReadEngine):
    served = engine.read_panel(
        ROOM, buyer_email="nobody@example.test", now=NOW, actor="t", source="test"
    )
    assert served["crm_context"] is False
    assert served["outcome"] == "empty"
    assert served["refreshed"] is False
    assert served["cache"]["state"] == "absent"
    assert "without CRM context" in served["reason"]
    assert served["panel"]["deal"]["found"] is False
    assert served["asked_for"] == {"identity_id": None, "buyer_email": "nobody@example.test"}


def test_pulling_for_a_buyer_with_no_identity_is_also_a_panel(engine: CrmReadEngine):
    pulled = engine.pull(ROOM, buyer_email="nobody@example.test", now=NOW, actor="t", source="test")
    assert pulled["crm_context"] is False
    assert pulled["snapshot_id"] is None


def test_the_deal_panel_holds_one_entry_per_identity_and_counts_its_reads(
    engine: CrmReadEngine,
):
    first = add_identity(engine, email="a@northwind.example", deal="006-1")
    add_identity(engine, email="b@northwind.example", deal="006-2")
    add_rows(engine, "salesforce", SALESFORCE_TABLE)
    engine.pull(ROOM, identity_id=first["id"], now=NOW, actor="t", source="test")
    panel = engine.deal_panel(ROOM)
    assert panel["identities"] == 2
    assert panel["served_from_cache"] == 1
    assert panel["reads_issued"] == 1
    assert panel["without_crm_context"] is False


def test_the_deal_panel_of_a_room_with_no_identities_says_so(engine: CrmReadEngine):
    panel = engine.deal_panel(ROOM)
    assert panel["identities"] == 0
    assert panel["without_crm_context"] is True
    assert panel["panels"] == []


def test_an_identity_can_be_patched_and_a_snapshot_survives_the_patch(engine: CrmReadEngine):
    created = add_identity(engine)
    add_rows(engine, "salesforce", SALESFORCE_TABLE)
    engine.pull(ROOM, identity_id=created["id"], now=NOW, actor="t", source="test")
    patched = engine.patch_identity(
        created["id"], {"buyer_name": "Dana O."}, room_id=ROOM, actor="t", source="test"
    )
    assert patched["buyer_name"] == "Dana O."
    assert patched["deal_id"] == "006-1"


def test_patching_to_a_duplicate_buyer_is_refused(engine: CrmReadEngine):
    first = add_identity(engine, email="a@northwind.example")
    second = add_identity(engine, email="b@northwind.example")
    with pytest.raises(DuplicateIdentity):
        engine.patch_identity(
            second["id"],
            {"buyer_email": "a@northwind.example"},
            room_id=ROOM,
            actor="t",
            source="test",
        )
    assert engine.read_identity(first["id"], ROOM)["buyer_email"] == "a@northwind.example"


def test_deleting_an_identity_takes_its_cached_panel_with_it(engine: CrmReadEngine):
    created = add_identity(engine)
    add_rows(engine, "salesforce", SALESFORCE_TABLE)
    pulled = engine.pull(ROOM, identity_id=created["id"], now=NOW, actor="t", source="test")
    removed = engine.delete_identity(created["id"], room_id=ROOM, actor="t", source="test")
    assert removed["deleted"] is True
    assert removed["soft_delete"] is True
    assert removed["snapshot_removed"] == [pulled["snapshot_id"]]
    assert cache.find(engine.store, created["id"], ROOM) is None


def test_deleting_an_identity_removes_no_snapshot_when_there_was_none(engine: CrmReadEngine):
    created = add_identity(engine)
    removed = engine.delete_identity(created["id"], room_id=ROOM, actor="t", source="test")
    assert removed["snapshot_removed"] == []


def test_an_option_set_can_be_read_and_deleted_through_the_engine(engine: CrmReadEngine):
    created = stage_option_set(engine)
    assert engine.read_option_set(created["id"])["field"] == "stage"
    assert engine.delete_option_set(created["id"], room_id=None, actor="t", source="test") == {
        "id": created["id"],
        "deleted": True,
    }
    with pytest.raises(UnknownIdentity):
        engine.read_option_set(created["id"])


def test_an_option_set_from_another_room_cannot_be_read_through_this_room(
    engine: CrmReadEngine,
):
    created = stage_option_set(engine, room_id=OTHER_ROOM)
    with pytest.raises(UnknownIdentity):
        engine.read_option_set(created["id"], room_id=ROOM)


def test_deleting_a_missing_option_set_refuses_rather_than_reporting_success(
    engine: CrmReadEngine,
):
    with pytest.raises(UnknownIdentity):
        engine.delete_option_set("absent", room_id=None, actor="t", source="test")


def test_the_summary_counts_the_room_and_names_the_collections_it_added(
    engine: CrmReadEngine,
):
    created = add_identity(engine)
    stage_option_set(engine)
    add_rows(engine, "salesforce", SALESFORCE_TABLE)
    engine.pull(ROOM, identity_id=created["id"], now=NOW, actor="t", source="test")
    served = engine.summary(ROOM)
    assert served["identities"] == 1
    assert served["identities_by_system"] == {"salesforce": 1}
    assert served["option_sets"] == 1
    assert served["vendor_rows"] == 3
    assert served["read_plans"] == 3
    assert served["collections"] == list(OWNED_COLLECTIONS)


def test_the_tables_route_reports_every_system_and_object_with_its_page_limit(
    engine: CrmReadEngine,
):
    listed = engine.tables(ROOM)
    assert len(listed) == 9
    by_key = {(row["system"], row["object"]): row for row in listed}
    assert by_key[("salesforce", "deal")]["page_limit"] == 2000
    assert by_key[("hubspot", "account")]["vendor_name"] == "companies"
    assert by_key[("dataverse", "contact")]["rows"] == 0


def test_a_pull_with_an_owner_filtered_page_longer_than_the_table_is_complete(
    engine: CrmReadEngine,
):
    add_rows(engine, "salesforce", SALESFORCE_TABLE, owner="005-dana")
    created = add_identity(engine, owner="005-dana")
    result = engine.pull(ROOM, identity_id=created["id"], now=NOW, actor="t", source="test")
    assert result["reads"]["deal"]["done"] is True
    assert result["outcome"] != "paged"


def test_a_resolved_record_id_is_the_whole_filter_not_the_owner_on_top_of_it(
    engine: CrmReadEngine,
):
    """The owner's clause is the *wider* read, so it never narrows a resolved id.

    A contact or account row usually carries no owner, so applying both clauses
    would drop a contact who exists and report them as absent.
    """
    add_rows(engine, "salesforce", SALESFORCE_TABLE, owner="")
    created = add_identity(engine, owner="005-dana")
    result = engine.pull(ROOM, identity_id=created["id"], now=NOW, actor="t", source="test")
    assert result["panel"]["contact"]["found"] is True
    assert result["panel"]["account"]["found"] is True
    contact_plan = next(q for q in result["queries"] if q["object"] == "contact")
    assert "OwnerId" not in contact_plan["query"]["q"]
    assert contact_plan["owner_id"] == ""


def test_a_pull_naming_no_buyer_reads_the_rooms_only_identity(engine: CrmReadEngine):
    add_rows(engine, "salesforce", SALESFORCE_TABLE)
    created = add_identity(engine)
    result = engine.pull(ROOM, now=NOW, actor="t", source="test")
    assert result["identity"]["id"] == created["id"]
    assert result["crm_context"] is True


def test_a_pull_naming_no_buyer_in_a_room_with_two_is_refused(engine: CrmReadEngine):
    add_identity(engine, email="a@northwind.example")
    add_identity(engine, email="b@northwind.example")
    with pytest.raises(AmbiguousIdentity):
        engine.pull(ROOM, now=NOW, actor="t", source="test")


def test_a_pull_whose_table_is_longer_than_its_page_reports_the_continuation(
    engine: CrmReadEngine,
):
    for index in range(3):
        engine.declare_record(
            {
                "system": "salesforce",
                "object": "deal",
                "external_id": f"006-{index}",
                "owner_id": "005-dana",
                "fields": {"Name": f"Deal {index}"},
                "modified": f"2026-09-0{index + 1}T00:00:00+00:00",
            },
            room_id=ROOM,
            actor="t",
            source="test",
        )
    created = add_identity(engine, deal="", contact="", account="", owner="005-dana")
    result = engine.pull(
        ROOM, identity_id=created["id"], limit=2, now=NOW, actor="t", source="test"
    )
    assert result["reads"]["deal"]["done"] is False
    assert result["reads"]["deal"]["next"]["mode"] == "query_locator"
    assert result["outcome"] == "paged"


def test_every_write_names_the_route_that_served_it_and_the_row_is_audited(
    engine: CrmReadEngine,
    store: RecordStore,
):
    created = add_identity(engine)
    add_rows(engine, "salesforce", SALESFORCE_TABLE)
    engine.pull(ROOM, identity_id=created["id"], now=NOW, actor="t", source="test")
    sources_written = {entry["source"] for entry in store.audit(limit=200) if entry.get("source")}
    assert sources_written == {
        "POST /api/wf-042/identities",
        "POST /api/wf-042/records",
        "test",
    }


def test_a_write_without_a_source_is_a_type_error_not_an_unaudited_row(engine: CrmReadEngine):
    with pytest.raises(TypeError):
        engine.register_identity(
            {"system": "salesforce", "buyer_email": "a@b.example"}, room_id=ROOM, actor="t"
        )


def test_records_are_ordinary_json_so_a_team_can_add_a_field_without_a_migration(
    engine: CrmReadEngine,
):
    engine.declare_record(
        {
            "system": "salesforce",
            "object": "deal",
            "external_id": "006-1",
            "fields": {"Name": "x"},
            "crm_tier__c": "EMEA",
            "crm_owner": {"team": "field-sales"},
        },
        room_id=ROOM,
        actor="t",
        source="test",
    )
    # Queryable through the dynamic index, with no migration and no typed
    # column. The dotted path is the one the store resolves inside JSON.
    assert len(engine.store.find(sources.RECORDS, {"crm_tier__c": "EMEA"})) == 1
    assert len(engine.store.find(sources.RECORDS, {"crm_owner.team": "field-sales"})) == 1


# --------------------------------------------------------------------------- #
# The architectural guards the contract names
# --------------------------------------------------------------------------- #


def test_the_feature_module_owns_no_shared_file_and_opens_no_connection():
    text = Path(feature.__file__).read_text(encoding="utf-8")
    assert "from dsr.api" not in text and "import dsr.api" not in text
    assert "import sqlite3" not in text
    assert "sqlite3.connect" not in text


def test_the_domain_module_imports_nothing_but_the_store():
    """The contract's rule, read off the package on disk rather than off a claim."""
    package = Path(inspect.getfile(CrmReadEngine)).parent
    for module in sorted(package.glob("*.py")):
        text = module.read_text(encoding="utf-8")
        assert "import sqlite3" not in text, module.name
        assert "sqlite3.connect" not in text, module.name
        assert "from dsr.api" not in text, module.name
        assert "dsr.deps" not in text, module.name
        assert "fastapi" not in text, module.name
        assert "AuditedDatabase(" not in text, module.name


def test_the_feature_declares_the_prefix_the_contract_names():
    assert feature.router.prefix == "/api/wf-042"
    assert feature.FEATURE["ticket"] == "WF-042"
    assert feature.FEATURE["id"] == "wf-042-pull-crm-deal-account-and-contact-data-into"
    assert feature.FEATURE["name"]


def test_the_feature_registers_one_handler_for_the_whole_error_hierarchy():
    assert feature.EXCEPTION_HANDLERS == {CrmIntegrationError: feature._crm_integration_error}


def test_the_seeder_runs_and_its_return_string_is_printable_on_a_windows_console(tmp_path):
    """A single RIGHTWARDS ARROW in one recovered feature broke the whole seeder."""
    from dsr.db.audited import AuditedDatabase

    db = AuditedDatabase(tmp_path / "seed.db", actor="seed")
    try:
        store = RecordStore(db)
        store.create("room", {"name": "Demo"}, room_id="room-seed", actor="seed", source="seed")
        reported = feature.seed(
            db, {"room_ids": [("room-seed", "northwind"), ("room-seed-2", "contoso")], "now": NOW}
        )
    finally:
        db.close()
    assert isinstance(reported, str) and reported
    reported.encode("cp1252")
    print(reported)
    assert "no CRM identity" in reported
    assert "identities" in reported


def test_the_seeder_with_no_rooms_says_so_rather_than_claiming_rows_it_did_not_create(tmp_path):
    from dsr.db.audited import AuditedDatabase

    db = AuditedDatabase(tmp_path / "seed-empty.db", actor="seed")
    try:
        reported = feature.seed(db, {"room_ids": []})
    finally:
        db.close()
    reported.encode("cp1252")
    assert "no rooms to scope them to" in reported
