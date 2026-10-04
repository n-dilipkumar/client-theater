"""WF-042 HTTP tests: the mounted router, its errors, and its audit rows.

What is under test, and why
---------------------------

The domain rules live in ``tests/test042.py``. What is here is the half the
domain cannot see:

* the router is **mounted by discovery alone**, with no edit to ``dsr/api.py``
* every documented refusal reaches the client with the status and code the error
  carries, through **one** registered handler
* a room with **no CRM context is a 200**, because that is the state the research
  says the product must support
* every write's audit row **names a route this router actually serves**, checked
  against the live route table rather than against a list in this file
* the routes answer **no 5xx when called the way ``tools/verify_all_routes.py``
  calls them**, which is the tool CI runs

Isolation: the ``client`` fixture from ``conftest.py`` enters one ``TestClient``
per module and points ``app.state`` at a fresh, empty database per test, so this
file passes on its own and under ``pytest-xdist``.
"""

from __future__ import annotations

from typing import Any

import pytest
from dsr.features import wf042_pull_crm_deal_account_and_contact_data_into as feature
from fastapi.testclient import TestClient

PREFIX = "/api/wf-042"
ROOM = "room-1"
OTHER_ROOM = "room-2"


def served_routes(client: TestClient) -> list[dict[str, Any]]:
    """Every route the host reports, from the live registry."""
    return [
        route
        for entry in client.get("/api/features").json()["features"]
        for route in entry["routes"]
    ]


def names_a_served_route(source: str, routes: list[dict[str, Any]]) -> bool:
    """True when an audit ``source`` names a mounted route, segment by segment."""
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
            for expected, found in zip(template, actual, strict=False)
        ):
            return True
    return False


# --------------------------------------------------------------------------- #
# Fixtures and helpers
# --------------------------------------------------------------------------- #


@pytest.fixture
def salesforce(client: TestClient) -> TestClient:
    """A room with one Salesforce buyer, one vendor table, and one room option set."""
    client.post(
        f"{PREFIX}/option-sets",
        json={
            "system": "salesforce",
            "object": "deal",
            "field": "stage",
            "values": {"Negotiation": "In negotiation"},
        },
    )
    client.post(
        f"{PREFIX}/identities?room_id={ROOM}",
        json={
            "system": "salesforce",
            "buyer_email": "dana@northwind.example",
            "buyer_name": "Dana Okafor",
            "account_id": "001-1",
            "contact_id": "003-1",
            "deal_id": "006-1",
            "owner_id": "005-dana",
        },
    ).json()
    for object_name, payload in (
        (
            "deal",
            {
                "external_id": "006-1",
                "owner_id": "005-dana",
                "fields": {
                    "Name": "Northwind rollout",
                    "StageName": "Negotiation",
                    "Amount": 48000,
                    "CloseDate": "2026-11-30",
                },
                "modified": "2026-09-30T10:00:00+00:00",
            },
        ),
        (
            "contact",
            {
                "external_id": "003-1",
                "email": "dana@northwind.example",
                "fields": {
                    "Name": "Dana Okafor",
                    "Title": "VP Operations",
                    "Email": "dana@northwind.example",
                },
                "modified": "2026-09-30T10:00:00+00:00",
            },
        ),
        (
            "account",
            {
                "external_id": "001-1",
                "fields": {
                    "Name": "Northwind Traders",
                    "Industry": "Manufacturing",
                    "BillingCountry": "US",
                },
                "modified": "2026-09-30T10:00:00+00:00",
            },
        ),
    ):
        response = client.post(
            f"{PREFIX}/records?room_id={ROOM}",
            json={"system": "salesforce", "object": object_name, **payload},
        )
        assert response.status_code == 201, response.text
    return client


# --------------------------------------------------------------------------- #
# Discovery: the router is mounted because the host found the file
# --------------------------------------------------------------------------- #


def test_the_router_is_mounted_by_discovery_alone(client: TestClient):
    assert client.get(f"{PREFIX}/vocabulary").status_code == 200


def test_the_registry_reports_this_feature_with_its_prefix_and_routes(client: TestClient):
    body = client.get("/api/features").json()
    entry = next(row for row in body["features"] if row["id"] == feature.FEATURE["id"])
    assert entry["prefix"] == PREFIX
    assert entry["routes"]


def test_the_prefix_is_the_one_the_ticket_names():
    assert feature.router.prefix == "/api/wf-042"
    assert feature.FEATURE["ticket"] == "WF-042"


def test_no_core_route_is_shadowed_by_this_feature(client: TestClient):
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/records/room?limit=1").status_code == 200


# --------------------------------------------------------------------------- #
# Vocabulary, inferences, vendors
# --------------------------------------------------------------------------- #


def test_the_vocabulary_route_serves_the_three_systems_and_their_numbers(client: TestClient):
    body = client.get(f"{PREFIX}/vocabulary").json()
    assert body["systems"] == ["salesforce", "dataverse", "hubspot"]
    assert body["objects"] == ["deal", "contact", "account"]
    assert body["display_labels"] == {"salesforce": False, "dataverse": True, "hubspot": False}
    assert body["limits"]["salesforce"]["synchronous_records_per_request"] == 2000
    assert body["limits"]["hubspot"]["query_characters"] == 3000
    assert body["paging"]["modes"]["hubspot"] == "paging_cursor"


def test_the_inferences_route_names_the_identity_source_and_the_ttl(client: TestClient):
    body = client.get(f"{PREFIX}/inferences").json()
    decisions = {entry["decision"] for entry in body["inferred"]}
    assert "room_mapping" in decisions
    assert 300 in decisions
    assert "pull" in decisions
    assert body["sourced"]["field_scoped_read"]


def test_the_vendors_route_reports_capability_and_limits_per_vendor(client: TestClient):
    body = client.get(f"{PREFIX}/vendors").json()
    assert body["count"] == 3
    by_system = {row["system"]: row for row in body["vendors"]}
    assert by_system["dataverse"]["capability"]["display_label_annotations"] is True
    assert by_system["salesforce"]["capability"]["display_label_annotations"] is False


# --------------------------------------------------------------------------- #
# Identities
# --------------------------------------------------------------------------- #


def test_registering_an_identity_answers_201_with_the_identity_source(client: TestClient):
    response = client.post(
        f"{PREFIX}/identities?room_id={ROOM}",
        json={"system": "salesforce", "buyer_email": "dana@northwind.example"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["buyer_email"] == "dana@northwind.example"
    assert body["identity_source"] == "room_mapping"
    assert body["identity_source_derivation"] == "see /wf-042/inferences"


def test_an_identity_without_a_buyer_email_is_400_and_names_the_missing_field(
    client: TestClient,
):
    """400, not 404: the key is missing from the request, not missing from the table."""
    response = client.post(f"{PREFIX}/identities?room_id={ROOM}", json={"system": "salesforce"})
    assert response.status_code == 400
    body = response.json()
    assert body["error"] == "buyer_email_required"
    assert "buyer_email is required" in body["detail"]


def test_an_identity_on_a_system_outside_the_research_is_refused(client: TestClient):
    response = client.post(
        f"{PREFIX}/identities?room_id={ROOM}",
        json={"system": "sap", "buyer_email": "dana@northwind.example"},
    )
    assert response.status_code == 400
    assert response.json()["error"] == "unknown_crm_system"


def test_a_second_identity_for_one_buyer_is_409(client: TestClient):
    client.post(
        f"{PREFIX}/identities?room_id={ROOM}",
        json={"system": "salesforce", "buyer_email": "dana@northwind.example"},
    )
    response = client.post(
        f"{PREFIX}/identities?room_id={ROOM}",
        json={"system": "salesforce", "buyer_email": "dana@northwind.example", "deal_id": "006-2"},
    )
    assert response.status_code == 409
    assert response.json()["error"] == "crm_identity_already_registered"


def test_the_same_buyer_in_another_room_is_not_a_duplicate(client: TestClient):
    for room in (ROOM, OTHER_ROOM):
        assert (
            client.post(
                f"{PREFIX}/identities?room_id={room}",
                json={"system": "salesforce", "buyer_email": "dana@northwind.example"},
            ).status_code
            == 201
        )


def test_identities_can_be_listed_for_a_room_and_for_every_room(client: TestClient):
    client.post(
        f"{PREFIX}/identities?room_id={ROOM}",
        json={"system": "salesforce", "buyer_email": "a@northwind.example"},
    )
    client.post(
        f"{PREFIX}/identities?room_id={OTHER_ROOM}",
        json={"system": "dataverse", "buyer_email": "b@contoso.example"},
    )
    assert client.get(f"{PREFIX}/identities?room_id={ROOM}").json()["count"] == 1
    assert client.get(f"{PREFIX}/identities").json()["count"] == 2


def test_one_identity_can_be_read_back(salesforce: TestClient):
    listed = salesforce.get(f"{PREFIX}/identities?room_id={ROOM}").json()["identities"]
    identity_id = listed[0]["id"]
    body = salesforce.get(f"{PREFIX}/identities/{identity_id}?room_id={ROOM}").json()
    assert body["buyer_email"] == "dana@northwind.example"
    assert body["objects"] == ["deal", "contact", "account"]


def test_an_unknown_identity_is_404_with_the_feature_code(client: TestClient):
    response = client.get(f"{PREFIX}/identities/absent?room_id={ROOM}")
    assert response.status_code == 404
    assert response.json()["error"] == "crm_identity_not_found"


def test_an_identity_in_another_room_is_not_readable_through_this_room(salesforce: TestClient):
    listed = salesforce.get(f"{PREFIX}/identities?room_id={ROOM}").json()["identities"]
    identity_id = listed[0]["id"]
    assert (
        salesforce.get(f"{PREFIX}/identities/{identity_id}?room_id={OTHER_ROOM}").status_code == 404
    )


def test_an_identity_can_be_patched(salesforce: TestClient):
    listed = salesforce.get(f"{PREFIX}/identities?room_id={ROOM}").json()["identities"]
    identity_id = listed[0]["id"]
    response = salesforce.patch(
        f"{PREFIX}/identities/{identity_id}?room_id={ROOM}", json={"buyer_name": "Dana O."}
    )
    assert response.status_code == 200
    assert response.json()["buyer_name"] == "Dana O."
    assert response.json()["deal_id"] == "006-1"


def test_patching_a_field_to_something_invalid_is_refused(client: TestClient):
    created = client.post(
        f"{PREFIX}/identities?room_id={ROOM}",
        json={"system": "salesforce", "buyer_email": "a@northwind.example"},
    ).json()
    response = client.patch(
        f"{PREFIX}/identities/{created['id']}?room_id={ROOM}", json={"system": "sap"}
    )
    assert response.status_code == 400
    assert response.json()["error"] == "unknown_crm_system"


def test_deleting_an_identity_soft_deletes_it_and_reports_the_snapshot(salesforce: TestClient):
    listed = salesforce.get(f"{PREFIX}/identities?room_id={ROOM}").json()["identities"]
    identity_id = listed[0]["id"]
    salesforce.post(f"{PREFIX}/rooms/{ROOM}/panel/pull?identity_id={identity_id}")
    response = salesforce.delete(f"{PREFIX}/identities/{identity_id}?room_id={ROOM}")
    assert response.status_code == 200
    body = response.json()
    assert body["deleted"] is True
    assert body["soft_delete"] is True
    assert body["snapshot_removed"]


# --------------------------------------------------------------------------- #
# Option sets: the room's own display labels
# --------------------------------------------------------------------------- #


def test_registering_an_option_set_answers_201_with_its_values_as_strings(client: TestClient):
    response = client.post(
        f"{PREFIX}/option-sets",
        json={
            "system": "salesforce",
            "object": "deal",
            "field": "stage",
            "values": {2: "Proposal sent"},
        },
    )
    assert response.status_code == 201
    assert response.json()["values"] == {"2": "Proposal sent"}


def test_an_option_set_on_an_unlabellable_field_is_refused(client: TestClient):
    response = client.post(
        f"{PREFIX}/option-sets",
        json={"system": "salesforce", "object": "deal", "field": "deal_name", "values": {"a": "b"}},
    )
    assert response.status_code == 404
    assert response.json()["error"] == "unknown_option_set"


def test_a_second_option_set_at_the_same_scope_is_409(client: TestClient):
    payload = {"system": "salesforce", "object": "deal", "field": "stage", "values": {"A": "a"}}
    assert client.post(f"{PREFIX}/option-sets", json=payload).status_code == 201
    response = client.post(f"{PREFIX}/option-sets", json=payload)
    assert response.status_code == 409
    assert response.json()["error"] == "option_set_already_registered"


def test_option_sets_can_be_listed_and_filtered_by_system(client: TestClient):
    client.post(
        f"{PREFIX}/option-sets",
        json={"system": "salesforce", "object": "deal", "field": "stage", "values": {"A": "a"}},
    )
    client.post(
        f"{PREFIX}/option-sets",
        json={"system": "hubspot", "object": "deal", "field": "stage", "values": {"B": "b"}},
    )
    assert client.get(f"{PREFIX}/option-sets").json()["count"] == 2
    assert client.get(f"{PREFIX}/option-sets?system=hubspot").json()["count"] == 1


def test_one_option_set_can_be_read_and_deleted(client: TestClient):
    created = client.post(
        f"{PREFIX}/option-sets",
        json={"system": "salesforce", "object": "deal", "field": "stage", "values": {"A": "a"}},
    ).json()
    assert client.get(f"{PREFIX}/option-sets/{created['id']}").status_code == 200
    assert client.delete(f"{PREFIX}/option-sets/{created['id']}").status_code == 200
    assert client.get(f"{PREFIX}/option-sets/{created['id']}").status_code == 404


def test_an_option_set_from_another_room_is_not_readable_through_this_room(client: TestClient):
    created = client.post(
        f"{PREFIX}/option-sets?room_id={OTHER_ROOM}",
        json={"system": "salesforce", "object": "deal", "field": "stage", "values": {"A": "a"}},
    ).json()
    assert client.get(f"{PREFIX}/option-sets/{created['id']}?room_id={ROOM}").status_code == 404


# --------------------------------------------------------------------------- #
# The vendor's tables
# --------------------------------------------------------------------------- #


def test_the_tables_route_reports_all_nine_with_their_page_limits(client: TestClient):
    body = client.get(f"{PREFIX}/tables?room_id={ROOM}").json()
    assert body["count"] == 9
    assert body["read_only"] is True
    by_key = {(row["system"], row["object"]): row for row in body["tables"]}
    assert by_key[("salesforce", "deal")]["page_limit"] == 2000
    assert by_key[("dataverse", "deal")]["page_limit"] == 5000
    assert by_key[("hubspot", "deal")]["page_limit"] == 200


def test_declaring_a_row_answers_201_and_the_table_count_rises(salesforce: TestClient):
    body = salesforce.get(f"{PREFIX}/tables?room_id={ROOM}").json()
    by_key = {(row["system"], row["object"]): row for row in body["tables"]}
    assert by_key[("salesforce", "deal")]["rows"] == 1
    assert by_key[("hubspot", "deal")]["rows"] == 0


def test_a_row_with_an_unknown_system_or_object_is_refused(client: TestClient):
    assert (
        client.post(
            f"{PREFIX}/records?room_id={ROOM}", json={"system": "sap", "object": "deal"}
        ).status_code
        == 400
    )
    assert (
        client.post(
            f"{PREFIX}/records?room_id={ROOM}",
            json={"system": "salesforce", "object": "lead"},
        ).status_code
        == 404
    )


def test_the_records_route_can_be_narrowed_by_system_and_object(salesforce: TestClient):
    body = salesforce.get(f"{PREFIX}/records?system=salesforce&object=deal").json()
    assert body["count"] == 1
    assert body["tables"][0]["rows"] == 1
    assert salesforce.get(f"{PREFIX}/records?object=account").json()["count"] == 3


def test_the_records_route_refuses_an_object_the_panel_does_not_read(salesforce: TestClient):
    assert salesforce.get(f"{PREFIX}/records?object=lead").status_code == 404


# --------------------------------------------------------------------------- #
# The panel: user-flow steps two to five
# --------------------------------------------------------------------------- #


def test_the_panel_answers_200_and_carries_the_five_researched_fields(salesforce: TestClient):
    identity_id = salesforce.get(f"{PREFIX}/identities?room_id={ROOM}").json()["identities"][0][
        "id"
    ]
    body = salesforce.get(f"{PREFIX}/rooms/{ROOM}/panel?identity_id={identity_id}").json()
    assert body["crm_context"] is True
    panel = body["panel"]
    assert panel["deal"]["name"] == "Northwind rollout"
    assert panel["deal"]["amount"] == 48000
    assert panel["contact"]["name"] == "Dana Okafor"
    assert panel["account"]["name"] == "Northwind Traders"
    assert panel["account"]["industry"]["value"] == "Manufacturing"


def test_the_panel_resolves_a_buyer_by_email_as_well_as_by_id(salesforce: TestClient):
    body = salesforce.get(f"{PREFIX}/rooms/{ROOM}/panel?buyer_email=DANA@Northwind.EXAMPLE").json()
    assert body["crm_context"] is True
    assert body["identity"]["buyer_email"] == "dana@northwind.example"


def test_a_stage_is_labelled_from_the_room_own_option_set(salesforce: TestClient):
    identity_id = salesforce.get(f"{PREFIX}/identities?room_id={ROOM}").json()["identities"][0][
        "id"
    ]
    body = salesforce.get(f"{PREFIX}/rooms/{ROOM}/panel?identity_id={identity_id}").json()
    assert body["panel"]["deal"]["stage"] == {"value": "Negotiation", "label": "In negotiation"}
    assert body["label_source"] == "room option sets"
    assert body["fallback_fills"] == ["deal.stage"]


def test_the_panel_says_a_second_read_came_from_cache(salesforce: TestClient):
    identity_id = salesforce.get(f"{PREFIX}/identities?room_id={ROOM}").json()["identities"][0][
        "id"
    ]
    salesforce.get(f"{PREFIX}/rooms/{ROOM}/panel?identity_id={identity_id}")
    body = salesforce.get(f"{PREFIX}/rooms/{ROOM}/panel?identity_id={identity_id}").json()
    assert body["refreshed"] is False
    assert body["cache"]["state"] == "fresh"
    assert body["cache"]["served_from_cache"] is True
    assert body["cache"]["refresh_mode"] == "pull"


def test_a_refresh_forces_the_read_and_says_it_did(salesforce: TestClient):
    identity_id = salesforce.get(f"{PREFIX}/identities?room_id={ROOM}").json()["identities"][0][
        "id"
    ]
    salesforce.get(f"{PREFIX}/rooms/{ROOM}/panel?identity_id={identity_id}")
    body = salesforce.get(
        f"{PREFIX}/rooms/{ROOM}/panel?identity_id={identity_id}&refresh=true"
    ).json()
    assert body["refreshed"] is True
    assert body["refresh_was_forced"] is True


def test_a_room_with_no_crm_context_is_a_200_with_a_reason_not_a_404(client: TestClient):
    """The research's own summary of this workflow, as a status code."""
    response = client.get(f"{PREFIX}/rooms/{ROOM}/panel?buyer_email=nobody@example.test")
    assert response.status_code == 200
    body = response.json()
    assert body["crm_context"] is False
    assert body["outcome"] == "empty"
    assert body["cache"]["state"] == "absent"
    assert "without CRM context" in body["reason"]
    assert body["panel"]["deal"]["found"] is False


def test_the_panel_of_a_room_that_does_not_exist_is_still_a_200(client: TestClient):
    assert client.get(f"{PREFIX}/rooms/room-absent/panel").status_code == 200


def test_the_pull_route_issues_the_plans_and_logs_them(salesforce: TestClient):
    identity_id = salesforce.get(f"{PREFIX}/identities?room_id={ROOM}").json()["identities"][0][
        "id"
    ]
    response = salesforce.post(
        f"{PREFIX}/rooms/{ROOM}/panel/pull?identity_id={identity_id}", json={"limit": 50}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["refreshed"] is True
    assert {row["object"] for row in body["queries"]} == {"deal", "contact", "account"}
    assert all(row["read_only"] is True for row in body["queries"])
    deal_plan = next(row for row in body["queries"] if row["object"] == "deal")
    assert "LIMIT 50" in deal_plan["query"]["q"]
    assert deal_plan["vendor_name"] == "Opportunity"


def test_the_pull_route_takes_the_identity_from_the_body_too(salesforce: TestClient):
    identity_id = salesforce.get(f"{PREFIX}/identities?room_id={ROOM}").json()["identities"][0][
        "id"
    ]
    body = salesforce.post(
        f"{PREFIX}/rooms/{ROOM}/panel/pull", json={"identity_id": identity_id}
    ).json()
    assert body["crm_context"] is True


def test_a_pull_with_a_zero_limit_is_refused_and_names_the_vendor_rule(salesforce: TestClient):
    identity_id = salesforce.get(f"{PREFIX}/identities?room_id={ROOM}").json()["identities"][0][
        "id"
    ]
    response = salesforce.post(
        f"{PREFIX}/rooms/{ROOM}/panel/pull?identity_id={identity_id}", json={"limit": 0}
    )
    assert response.status_code == 400
    assert response.json()["error"] == "page_size_exceeds_vendor_limit"


def test_a_pull_naming_no_buyer_reads_the_rooms_only_identity(salesforce: TestClient):
    response = salesforce.post(f"{PREFIX}/rooms/{ROOM}/panel/pull", json={})
    assert response.status_code == 200
    body = response.json()
    assert body["crm_context"] is True
    assert body["identity"]["buyer_email"] == "dana@northwind.example"


def test_a_pull_naming_no_buyer_in_a_room_with_two_buyers_is_409(client: TestClient):
    for email in ("a@northwind.example", "b@northwind.example"):
        client.post(
            f"{PREFIX}/identities?room_id={ROOM}",
            json={"system": "salesforce", "buyer_email": email},
        )
    response = client.post(f"{PREFIX}/rooms/{ROOM}/panel/pull", json={})
    assert response.status_code == 409
    body = response.json()
    assert body["error"] == "ambiguous_crm_identity"
    assert "?identity_id=" in body["detail"]


def test_a_pull_for_an_unknown_identity_reports_no_context_rather_than_failing(
    salesforce: TestClient,
):
    response = salesforce.post(f"{PREFIX}/rooms/{ROOM}/panel/pull?identity_id=absent", json={})
    assert response.status_code == 200
    assert response.json()["crm_context"] is False


def test_the_deal_panel_holds_one_entry_per_identity(salesforce: TestClient):
    salesforce.post(
        f"{PREFIX}/identities?room_id={ROOM}",
        json={"system": "dataverse", "buyer_email": "raj@contoso.example", "deal_id": "op-1"},
    )
    body = salesforce.get(f"{PREFIX}/rooms/{ROOM}/deal-panel").json()
    assert body["identities"] == 2
    assert body["without_crm_context"] is False
    assert {entry["system"] for entry in body["panels"]} == {"salesforce", "dataverse"}


def test_the_deal_panel_of_a_room_with_no_identities_says_so(client: TestClient):
    body = client.get(f"{PREFIX}/rooms/{ROOM}/deal-panel").json()
    assert body["identities"] == 0
    assert body["without_crm_context"] is True
    assert body["panels"] == []


def test_the_query_log_reports_the_exact_plan_each_read_issued(salesforce: TestClient):
    salesforce.post(f"{PREFIX}/rooms/{ROOM}/panel/pull")
    body = salesforce.get(f"{PREFIX}/rooms/{ROOM}/queries").json()
    assert body["count"] == 3
    assert body["read_only"] is True
    assert body["paging_modes"]["salesforce"] == "query_locator"
    deal = next(row for row in body["queries"] if row["object"] == "deal")
    assert deal["endpoint"] == "query"
    assert deal["method"] == "GET"
    assert deal["path"] == "/services/data/v61.0/query"
    assert "SELECT Id, Name, StageName" in deal["query"]["q"]
    assert deal["done"] is True
    assert deal["headers"]["Sforce-Query-Options"] == "batchSize=200"


def test_the_query_log_can_be_filtered_by_object(salesforce: TestClient):
    salesforce.post(f"{PREFIX}/rooms/{ROOM}/panel/pull")
    assert salesforce.get(f"{PREFIX}/rooms/{ROOM}/queries?object=account").json()["count"] == 1


def test_the_query_log_of_a_room_that_read_nothing_is_empty(client: TestClient):
    body = client.get(f"{PREFIX}/rooms/{ROOM}/queries").json()
    assert body["count"] == 0


def test_a_dataverse_read_asks_for_the_formatted_value_annotation(client: TestClient):
    client.post(
        f"{PREFIX}/identities?room_id={ROOM}",
        json={
            "system": "dataverse",
            "buyer_email": "raj@contoso.example",
            "deal_id": "op-1",
        },
    )
    client.post(
        f"{PREFIX}/records?room_id={ROOM}",
        json={
            "system": "dataverse",
            "object": "deal",
            "external_id": "op-1",
            "fields": {"name": "Contoso pilot", "stepname": "1"},
            "labels": {"stepname": "Proposal sent"},
        },
    )
    body = client.get(f"{PREFIX}/rooms/{ROOM}/panel?buyer_email=raj@contoso.example").json()
    assert body["display_labels"] is True
    assert body["label_source"] == "vendor annotations"
    assert body["panel"]["deal"]["stage"] == {"value": "1", "label": "Proposal sent"}
    logged = client.get(f"{PREFIX}/rooms/{ROOM}/queries?object=deal").json()["queries"][0]
    assert logged["query"]["$select"] == "id,name,stepname,estimatedvalue,estimatedclosedate"
    assert logged["headers"]["Prefer"] == (
        'odata.include-annotations="OData.Community.Display.V1.FormattedValue"'
    )


def test_a_hubspot_read_by_record_id_is_a_batch_read(client: TestClient):
    client.post(
        f"{PREFIX}/identities?room_id={ROOM}",
        json={
            "system": "hubspot",
            "buyer_email": "mei@fabrikam.example",
            "deal_id": "dl-1",
            "contact_id": "ct-1",
        },
    )
    client.post(
        f"{PREFIX}/records?room_id={ROOM}",
        json={
            "system": "hubspot",
            "object": "deal",
            "external_id": "dl-1",
            "fields": {"dealname": "Fabrikam workspace", "dealstage": "contractsent"},
        },
    )
    body = client.get(f"{PREFIX}/rooms/{ROOM}/panel?buyer_email=mei@fabrikam.example").json()
    assert body["panel"]["deal"]["name"] == "Fabrikam workspace"
    logged = client.get(f"{PREFIX}/rooms/{ROOM}/queries?object=deal").json()["queries"][0]
    assert logged["endpoint"] == "batch_read"
    assert logged["method"] == "POST"
    assert logged["path"] == "/crm/v3/objects/deals/batch/read"
    assert logged["body"]["inputs"] == [{"id": "dl-1"}]


def test_a_hubspot_contact_resolved_by_email_uses_the_single_record_lookup(client: TestClient):
    client.post(
        f"{PREFIX}/identities?room_id={ROOM}",
        json={
            "system": "hubspot",
            "buyer_email": "mei@fabrikam.example",
            "deal_id": "dl-1",
            "contact_id": "",
        },
    )
    client.post(
        f"{PREFIX}/records?room_id={ROOM}",
        json={
            "system": "hubspot",
            "object": "contact",
            "external_id": "ct-1",
            "email": "mei@fabrikam.example",
            "fields": {"email": "mei@fabrikam.example", "jobtitle": "Director"},
        },
    )
    body = client.get(f"{PREFIX}/rooms/{ROOM}/panel?buyer_email=mei@fabrikam.example").json()
    assert body["panel"]["contact"]["found"] is True
    logged = client.get(f"{PREFIX}/rooms/{ROOM}/queries?object=contact").json()["queries"][0]
    assert logged["endpoint"] == "get_by_email"
    assert logged["query"]["idProperty"] == "email"


def test_a_field_the_deployment_adds_reaches_the_panel_over_http(client: TestClient):
    """The extensibility sentence, proved across the whole stack rather than in the domain."""
    field_map = {
        "deal": {"Name": "deal_name", "StageName": "stage", "Region__c": "deal_region"},
        "contact": {"Email": "contact_email"},
        "account": {"Industry": "account_industry"},
    }
    client.post(
        f"{PREFIX}/identities?room_id={ROOM}",
        json={
            "system": "salesforce",
            "buyer_email": "dana@northwind.example",
            "deal_id": "006-1",
            "field_map": field_map,
        },
    )
    client.post(
        f"{PREFIX}/records?room_id={ROOM}",
        json={
            "system": "salesforce",
            "object": "deal",
            "external_id": "006-1",
            "fields": {"Name": "Rollout", "StageName": "Proposal", "Region__c": "EMEA"},
        },
    )
    body = client.get(f"{PREFIX}/rooms/{ROOM}/panel?buyer_email=dana@northwind.example").json()
    assert "Region__c" in body["reads"]["deal"]["read_set"]
    assert {"object": "deal", "crm_field": "Region__c", "room_field": "deal_region"} in body[
        "panel"
    ]["read_set"]
    assert body["reads"]["deal"]["returned"] == 1


def test_a_field_map_whose_object_selects_nothing_is_refused_with_the_reason(client: TestClient):
    client.post(
        f"{PREFIX}/identities?room_id={ROOM}",
        json={
            "system": "salesforce",
            "buyer_email": "dana@northwind.example",
            "deal_id": "006-1",
            "field_map": {"deal": {"StageName": "stage"}, "contact": {}, "account": {}},
        },
    )
    response = client.get(f"{PREFIX}/rooms/{ROOM}/panel?buyer_email=dana@northwind.example")
    assert response.status_code == 400
    body = response.json()
    assert body["error"] == "empty_read_set"
    assert "indistinguishable from a room whose CRM has no deal" in body["detail"]


# --------------------------------------------------------------------------- #
# Summary and cache
# --------------------------------------------------------------------------- #


def test_the_summary_counts_the_room(salesforce: TestClient):
    salesforce.post(f"{PREFIX}/rooms/{ROOM}/panel/pull")
    body = salesforce.get(f"{PREFIX}/rooms/{ROOM}/summary").json()
    assert body["identities"] == 1
    assert body["identities_by_system"] == {"salesforce": 1}
    assert body["option_sets"] == 1
    assert body["vendor_rows"] == 3
    assert body["read_plans"] == 3
    assert "crm_read_identity" in body["collections"]


def test_the_cache_route_reports_every_state_and_the_bounds(salesforce: TestClient):
    salesforce.post(f"{PREFIX}/rooms/{ROOM}/panel/pull")
    body = salesforce.get(f"{PREFIX}/rooms/{ROOM}/cache").json()
    assert body["fresh"] == 1
    assert body["absent"] == 0
    assert body["ttl_bounds"] == [30, 3600]
    assert body["refresh_mode"] == "pull"


# --------------------------------------------------------------------------- #
# The hard rules
# --------------------------------------------------------------------------- #


def test_every_write_audit_row_names_a_route_the_app_serves(salesforce: TestClient):
    """The port brief's central guarantee, checked against the live route table."""
    identity_id = salesforce.get(f"{PREFIX}/identities?room_id={ROOM}").json()["identities"][0][
        "id"
    ]
    salesforce.post(f"{PREFIX}/rooms/{ROOM}/panel/pull?identity_id={identity_id}")
    salesforce.post(
        f"{PREFIX}/records?room_id={ROOM}",
        json={
            "system": "salesforce",
            "object": "deal",
            "external_id": "006-2",
            "fields": {"Name": "Second"},
        },
    )
    salesforce.patch(
        f"{PREFIX}/identities/{identity_id}?room_id={ROOM}", json={"buyer_name": "Dana"}
    )

    audit = salesforce.get("/api/audit?limit=500").json()
    rows = audit["entries"] if isinstance(audit, dict) and "entries" in audit else audit
    mine = [row for row in rows if PREFIX in str(row.get("source") or "")]
    assert mine, "no audit row from this feature was written"

    routes = served_routes(salesforce)
    for row in mine:
        source = str(row["source"])
        assert names_a_served_route(source, routes), (
            f"audit row names {source!r}, which is not a route this app serves"
        )


def test_the_sources_written_are_exactly_the_routes_that_served_them(salesforce: TestClient):
    salesforce.post(f"{PREFIX}/rooms/{ROOM}/panel/pull")
    audit = salesforce.get("/api/audit?limit=500").json()
    rows = audit["entries"] if isinstance(audit, dict) and "entries" in audit else audit
    mine = {
        row["source"]
        for row in rows
        if PREFIX in str(row.get("source") or "") and row.get("action") != "read"
    }
    assert mine == {
        f"POST {PREFIX}/identities",
        f"POST {PREFIX}/option-sets",
        f"POST {PREFIX}/records",
        f"POST {PREFIX}/rooms/{{room_id}}/panel/pull",
    }


def test_one_handler_covers_the_whole_error_hierarchy(client: TestClient):
    assert list(feature.EXCEPTION_HANDLERS) == [feature.CrmIntegrationError]


def test_the_feature_module_reaches_no_shared_file_and_opens_no_connection():
    from pathlib import Path

    text = Path(feature.__file__).read_text(encoding="utf-8")
    assert "from dsr.api" not in text
    assert "import dsr.api" not in text
    assert "import sqlite3" not in text
    assert "sqlite3.connect" not in text


# --------------------------------------------------------------------------- #
# The routes, called the way tools/verify_all_routes.py calls them
# --------------------------------------------------------------------------- #


def test_every_route_answers_without_a_5xx_when_called_with_an_empty_body(client: TestClient):
    """The tool CI runs substitutes ids and sends ``{}`` to every method it finds.

    A write route that 500s on an empty body fails the build, so this walks the
    same route table the tool walks and asserts the same rule.
    """
    faults: list[tuple[str, str, int]] = []
    for route in served_routes(client):
        if not route["path"].startswith(PREFIX):
            continue
        path = route["path"].replace("{room_id}", ROOM).replace("{identity_id}", "absent")
        path = path.replace("{option_set_id}", "absent")
        for method in route["methods"]:
            if method not in {"GET", "POST", "PATCH", "DELETE"}:
                continue
            body = {} if method in {"POST", "PATCH", "PUT"} else None
            response = client.request(method, path, json=body)
            if response.status_code == 0 or response.status_code >= 500:
                faults.append((method, path, response.status_code))
    assert faults == [], f"routes answered 5xx when called with an empty body: {faults}"


def test_every_get_route_answers_without_a_5xx_on_a_room_that_read_nothing(client: TestClient):
    faults: list[tuple[str, int]] = []
    for route in served_routes(client):
        if not route["path"].startswith(PREFIX) or "GET" not in route["methods"]:
            continue
        path = route["path"].replace("{room_id}", "room-absent")
        path = path.replace("{identity_id}", "absent").replace("{option_set_id}", "absent")
        response = client.get(path)
        if response.status_code >= 500:
            faults.append((path, response.status_code))
    assert faults == [], faults
