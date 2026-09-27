"""HTTP tests for the library search workflow.

Observed from outside the process, the way a client sees it: the documented
status codes and messages, paging through the API, the attribution header, and
the one thing that must never break -- assembling content into a room is a
single audited write, and searching is not a write at all.

Converted from ``backend/tests/test_library_api.py`` on
``feature/WF-010-search-the-content-library-to-assemble-a-room``. The routes
moved from ``@app.<verb>`` in ``dsr/api.py`` to an ``APIRouter`` this feature
owns, so these tests now go through the discovery path a client does: if the
feature failed to register, or collided with another feature's route, every test
here would 404. The concrete paths are unchanged from the branch, because the
branch already used ``/api/library`` and that prefix is free.

The temp database is set up the way ``test_features.py`` does it. The token
expiry test is the one place the wiring is load-bearing: the branch minted its
cursor codec in the app's ``lifespan``, so rotating the signing key meant
restarting the app. Here the codec is built per request from the environment, so
the same rotation is what the test does, and it needs no restart to prove.
"""

from __future__ import annotations

import base64
import json
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dsr.api import app

#: This feature's own prefix. The tests go through the mounted app rather than a
#: throwaway router, so a collision with another feature fails here loudly.
PREFIX = "/api/library"

LIBRARY = [
    {
        "title": "Enterprise Overview Deck",
        "description": "Company overview for enterprise buyers",
        "body": "Covers the platform, the teams, and the security model in depth.",
        "kind": "pptx",
        "profile": "deck",
        "pages": 24,
        "publishDate": "2026-01-15",
        "properties": {"Region": "APAC"},
    },
    {
        "title": "Security and Compliance Pack",
        "description": "SOC 2, ISO 27001 and the penetration test summary",
        "body": "Every security question a review team asks, answered.",
        "kind": "pdf",
        "profile": "pack",
        "pages": 48,
        "publishDate": "2026-03-02",
        "properties": {"Region": "EMEA"},
    },
    {
        "title": "Pricing One-Pager",
        "description": "What it costs, with no asterisks",
        "body": "Per-seat pricing with an enterprise band.",
        "kind": "pdf",
        "profile": "onepager",
        "pages": 2,
        "publishDate": "2026-05-20",
        "properties": {"Region": "APAC"},
    },
    {
        "title": "API Integration Guide",
        "description": "REST and webhook reference",
        "body": "Authentication, rate limits, and every endpoint.",
        "kind": "pdf",
        "profile": "guide",
        "pages": 32,
        "publishDate": "2026-02-11",
        "properties": {"Region": "AMER"},
    },
]


@pytest.fixture()
def client(monkeypatch):
    import dsr.api as api_module

    tmp = tempfile.TemporaryDirectory()
    monkeypatch.setenv("DSR_DB_PATH", str(Path(tmp.name) / "api.db"))
    monkeypatch.setenv("DSR_AUDIT_DIR", str(Path(tmp.name) / "audit"))
    # Static mounts are import-time, so point the module at a missing directory.
    monkeypatch.setattr(api_module, "FRONTEND_DIST", Path(tmp.name) / "absent-frontend")
    with TestClient(app) as test_client:
        test_client.post("/api/records/document/bulk", json=LIBRARY)
        yield test_client
    tmp.cleanup()


@pytest.fixture()
def room(client):
    return client.post("/api/records/room", json={"name": "Northwind Evaluation"}).json()["id"]


def names(body):
    return [document["name"] for document in body["documents"]]


# -- registration ------------------------------------------------------------- #
#
# These are the assertions that make the port structural rather than cosmetic: the
# feature is mounted by discovery, it owns its prefix, and it claims no path that
# belongs to another workflow.


def test_the_feature_is_mounted_by_discovery(client):
    """No file in the host names this feature; discovery mounts it anyway."""
    body = client.get("/api/features").json()
    installed = {feature["id"]: feature for feature in body["features"]}

    assert "wf-010-library-search" in installed
    record = installed["wf-010-library-search"]
    assert record["prefix"] == PREFIX
    assert record["ticket"] == "WF-010"
    assert record["exception_handlers"] == ["SearchError"]


def test_the_feature_did_not_collide_with_anything(client):
    """The host refuses a concrete-route clash rather than letting one shadow."""
    body = client.get("/api/features").json()

    assert body["failed_count"] == 0, body["failed"]


def test_this_feature_claims_exactly_its_own_paths(client):
    body = client.get("/api/features").json()
    record = next(f for f in body["features"] if f["id"] == "wf-010-library-search")

    claimed = {
        (method, route["path"]) for route in record["routes"] for method in route["methods"]
    }
    expected = {
        ("GET", f"{PREFIX}/contract"),
        ("GET", f"{PREFIX}/fields"),
        ("POST", f"{PREFIX}/search"),
        ("POST", f"{PREFIX}/assemble"),
        ("GET", f"{PREFIX}/searches"),
        ("POST", f"{PREFIX}/searches"),
        ("GET", f"{PREFIX}/searches/{{record_id}}"),
        ("DELETE", f"{PREFIX}/searches/{{record_id}}"),
    }

    assert claimed == expected


def test_this_feature_does_not_claim_wf007s_paths(client):
    """WF-007 owns the document paths; the shared prefix is safe only if disjoint.

    WF-010 was originally held back believing it collided with WF-007 on
    ``/api/library``. It does not, and this is the assertion that keeps that true:
    two features may share a prefix, but neither may reach into the other's
    concrete paths.
    """
    body = client.get("/api/features").json()
    record = next(f for f in body["features"] if f["id"] == "wf-010-library-search")

    claimed = {route["path"] for route in record["routes"]}

    assert not any("/rooms/" in path for path in claimed)
    assert not any(path.endswith("/documents") for path in claimed)
    assert not any(path.startswith(f"{PREFIX}/documents") for path in claimed)


# -- discovery --------------------------------------------------------------- #


def test_contract_reports_the_documented_limits(client):
    limits = client.get(f"{PREFIX}/contract").json()["limits"]

    assert limits["maxTermLength"] == 150
    assert limits["minPageSize"] == 0
    assert limits["maxPageSize"] == 100
    assert limits["defaultPageSize"] == 40
    assert limits["maxFilterDepth"] == 2


def test_fields_endpoint_reports_what_the_library_actually_contains(client):
    body = client.get(f"{PREFIX}/fields").json()

    paths = {entry["path"] for entry in body["fields"]}
    assert "title" in paths
    assert "properties.Region" in paths
    assert body["collection"] == "document"


def test_fields_endpoint_is_empty_before_anything_is_stored(client, monkeypatch):
    """A fresh database reports no fields rather than inventing some."""
    import dsr.api as api_module

    with tempfile.TemporaryDirectory() as tmp:
        monkeypatch.setenv("DSR_DB_PATH", str(Path(tmp) / "empty.db"))
        monkeypatch.setenv("DSR_AUDIT_DIR", str(Path(tmp) / "audit"))
        monkeypatch.setattr(api_module, "FRONTEND_DIST", Path(tmp) / "absent")
        with TestClient(app) as fresh:
            assert fresh.get(f"{PREFIX}/fields").json() == {
                "count": 0,
                "fields": [],
                "collection": "document",
            }


# -- search ------------------------------------------------------------------ #


def test_empty_body_queries_all_content(client):
    body = client.post(f"{PREFIX}/search", json={}).json()

    assert body["totalCount"] == 4
    assert body["continuationToken"] is None


def test_no_body_at_all_is_the_same_as_an_empty_one(client):
    assert client.post(f"{PREFIX}/search").json()["totalCount"] == 4


def test_term_search(client):
    body = client.post(f"{PREFIX}/search", json={"term": "security"}).json()

    assert "Security and Compliance Pack" in names(body)
    assert body["repository"] == "library"
    assert body["searchedCollection"] == "document"


def test_search_fields_narrow_where_the_term_is_looked_for(client):
    body = client.post(
        f"{PREFIX}/search",
        json={"term": "rate", "options": {"searchFields": ["body"]}},
    ).json()

    assert names(body) == ["API Integration Guide"]


def test_return_fields_control_the_payload(client):
    body = client.post(
        f"{PREFIX}/search",
        json={"term": "security", "options": {"returnFields": ["id", "name", "pages"]}},
    ).json()

    assert set(body["documents"][0]) == {"id", "name", "pages", "_matchedFields"}


def test_filter_by_equality(client):
    body = client.post(
        f"{PREFIX}/search", json={"filter": {"field": "profile", "value": "guide"}}
    ).json()

    assert names(body) == ["API Integration Guide"]


def test_filter_by_custom_property(client):
    """Custom properties are filterable by name, as the research describes."""
    body = client.post(
        f"{PREFIX}/search", json={"filter": {"field": "custom.Region", "value": "APAC"}}
    ).json()

    assert sorted(names(body)) == ["Enterprise Overview Deck", "Pricing One-Pager"]


def test_filter_by_range(client):
    body = client.post(
        f"{PREFIX}/search",
        json={"filter": {"field": "pages", "operator": "greaterThan", "value": 30}},
    ).json()

    assert sorted(names(body)) == ["API Integration Guide", "Security and Compliance Pack"]


def test_and_or_group_at_the_documented_depth(client):
    body = client.post(
        f"{PREFIX}/search",
        json={
            "filter": {
                "and": [
                    {"field": "custom.Region", "value": "APAC"},
                    {
                        "or": [
                            {"field": "profile", "value": "deck"},
                            {"field": "profile", "value": "onepager"},
                        ]
                    },
                ]
            }
        },
    ).json()

    assert sorted(names(body)) == ["Enterprise Overview Deck", "Pricing One-Pager"]


def test_sort_by_an_unmapped_field(client):
    body = client.post(
        f"{PREFIX}/search", json={"sort": [{"field": "pages", "direction": "asc"}]}
    ).json()

    assert body["documents"][0]["name"] == "Pricing One-Pager"


def test_a_field_added_today_is_searchable_today_without_a_migration(client):
    client.post("/api/records/document", json={"title": "Regional Playbook", "seats": 12})

    assert client.post(f"{PREFIX}/search", json={"term": "regional"}).json()["totalCount"] == 1
    assert (
        client.post(
            f"{PREFIX}/search", json={"filter": {"field": "seats", "value": 12}}
        ).json()["totalCount"]
        == 1
    )


# -- documented 400s --------------------------------------------------------- #


def test_an_over_long_term_is_400_with_the_documented_message(client):
    response = client.post(f"{PREFIX}/search", json={"term": "a" * 151})

    assert response.status_code == 400
    assert response.json()["detail"] == "Search term should be less than 150 characters"


def test_a_term_at_the_limit_is_accepted(client):
    assert client.post(f"{PREFIX}/search", json={"term": "a" * 150}).status_code == 200


def test_an_out_of_range_page_size_is_400_with_the_documented_message(client):
    response = client.post(f"{PREFIX}/search", json={"options": {"pageSize": 150}})

    assert response.status_code == 400
    assert response.json()["detail"] == "PageSize 150 is incorrect. Please set a value between 0-100"


def test_a_too_deep_filter_is_400_with_the_documented_message(client):
    response = client.post(
        f"{PREFIX}/search",
        json={
            "filter": {
                "and": [
                    {"field": "profile", "value": "deck"},
                    {
                        "or": [
                            {"field": "pages", "value": 1},
                            {"and": [{"field": "kind", "value": "pdf"}]},
                        ]
                    },
                ]
            }
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Filter is too complex. Currently the max filter depth is 2."


def test_a_filter_at_the_documented_depth_is_accepted(client):
    response = client.post(
        f"{PREFIX}/search",
        json={
            "filter": {
                "and": [
                    {"field": "profile", "value": "deck"},
                    {"or": [{"field": "kind", "value": "pptx"}, {"field": "kind", "value": "pdf"}]},
                ]
            }
        },
    )

    assert response.status_code == 200


# -- paging ------------------------------------------------------------------ #


def test_paging_through_the_api_visits_every_result_once(client):
    seen: list[str] = []
    token = None

    while True:
        params = {"continuationToken": token} if token else {}
        body = client.post(f"{PREFIX}/search", json={"options": {"pageSize": 3}}, params=params).json()
        seen.extend(document["id"] for document in body["documents"])
        token = body["continuationToken"]
        if not token:
            break

    assert len(seen) == 4
    assert len(set(seen)) == 4


def test_an_expired_token_is_400_and_says_to_start_again(client, monkeypatch):
    first = client.post(f"{PREFIX}/search", json={"options": {"pageSize": 2}}).json()
    assert first["continuationToken"]

    # The token's lifetime is read at startup, so a second app with a zero TTL
    # stands in for "the token has aged out".
    import dsr.api as api_module

    monkeypatch.setenv("DSR_SEARCH_TOKEN_SECRET", "rotated-secret")
    with tempfile.TemporaryDirectory() as tmp:
        monkeypatch.setenv("DSR_DB_PATH", str(Path(tmp) / "api.db"))
        monkeypatch.setenv("DSR_AUDIT_DIR", str(Path(tmp) / "audit"))
        monkeypatch.setattr(api_module, "FRONTEND_DIST", Path(tmp) / "absent")
        with TestClient(app) as rotated:
            response = rotated.post(
                f"{PREFIX}/search",
                json={"options": {"pageSize": 2}},
                params={"continuationToken": first["continuationToken"]},
            )

    assert response.status_code == 400
    assert response.json()["detail"] == (
        "continuationToken is invalid or expired. Please regenerate it."
    )


def test_a_garbage_token_is_400(client):
    response = client.post(
        f"{PREFIX}/search", json={}, params={"continuationToken": "not-a-token"}
    )

    assert response.status_code == 400
    assert "invalid or expired" in response.json()["detail"]


def test_a_token_cannot_be_reused_with_a_different_term(client):
    token = client.post(
        f"{PREFIX}/search", json={"options": {"pageSize": 2}}
    ).json()["continuationToken"]

    response = client.post(
        f"{PREFIX}/search",
        json={"term": "security", "options": {"pageSize": 2}},
        params={"continuationToken": token},
    )

    assert response.status_code == 400


# -- zero-hit broadening ----------------------------------------------------- #


def test_broadening_is_off_by_default(client):
    body = client.post(f"{PREFIX}/search", json={"term": "securty"}).json()

    assert body["totalCount"] == 0
    assert body["actualSearchTerm"] is None


def test_broadening_reports_the_term_that_actually_matched(client):
    body = client.post(
        f"{PREFIX}/search",
        json={"term": "securty", "options": {"enableSuggestedQueryResults": True}},
    ).json()

    assert body["actualSearchTerm"] == "security"
    assert "Security and Compliance Pack" in names(body)


# -- attribution ------------------------------------------------------------- #


def encode_client_details(payload):
    return base64.b64encode(json.dumps(payload).encode()).decode()


def test_client_details_are_decoded_and_echoed(client):
    response = client.post(
        f"{PREFIX}/search",
        json={"term": "security"},
        headers={"X-Client-Details": encode_client_details({"application": "sales-room-ui"})},
    )

    assert response.status_code == 200
    assert response.json()["clientDetails"] == {"application": "sales-room-ui"}


def test_a_malformed_client_details_header_is_400(client):
    """Silently dropping attribution would make the analytics quietly wrong."""
    response = client.post(
        f"{PREFIX}/search", json={}, headers={"X-Client-Details": "!!!not base64!!!"}
    )

    assert response.status_code == 400
    assert "base64" in response.json()["detail"]


def test_client_details_that_decode_to_a_non_object_are_400(client):
    response = client.post(
        f"{PREFIX}/search", json={}, headers={"X-Client-Details": encode_client_details(["a"])}
    )

    assert response.status_code == 400


def test_no_header_means_no_attribution_rather_than_an_error(client):
    assert client.post(f"{PREFIX}/search", json={}).json()["clientDetails"] is None


# -- assembly ---------------------------------------------------------------- #


def test_assemble_attaches_the_chosen_documents(client, room):
    found = client.post(f"{PREFIX}/search", json={"term": "security"}).json()["documents"]
    items = [{"id": document["id"], "name": document["name"]} for document in found]

    response = client.post(
        f"{PREFIX}/assemble", json={"room_id": room, "items": items}, params={"actor": "dana"}
    )

    assert response.status_code == 201
    assert response.json()["added_count"] == 2
    attached = client.get("/api/records/room_content", params={"room_id": room}).json()
    assert attached["count"] == 2
    assert all(record["room_id"] == room for record in attached["records"])


def test_assembly_is_one_audit_row_however_many_documents(client, room):
    before = client.get("/api/audit").json()["count"]
    found = client.post(f"{PREFIX}/search", json={}).json()["documents"]

    client.post(
        f"{PREFIX}/assemble",
        json={"room_id": room, "items": [{"id": d["id"]} for d in found]},
    )

    entries = client.get("/api/audit", params={"collection": "room_content"}).json()
    assert entries["count"] == 1
    assert client.get("/api/audit").json()["count"] == before + 1


def test_assembly_appears_in_the_audit_trail_with_its_actor(client, room):
    found = client.post(f"{PREFIX}/search", json={"term": "pricing"}).json()["documents"]

    client.post(
        f"{PREFIX}/assemble",
        json={"room_id": room, "items": [{"id": found[0]["id"]}], "search_id": "library_search_1"},
        params={"actor": "dana"},
    )

    entry = client.get("/api/audit", params={"collection": "room_content"}).json()["entries"][0]
    assert entry["actor"] == "dana"
    assert entry["room_id"] == room
    assert entry["after_state"]["count"] == 1
    assert entry["after_state"]["ids"] == [
        record["id"]
        for record in client.get(
            "/api/records/room_content", params={"room_id": room}
        ).json()["records"]
    ]


def test_the_audit_row_names_the_route_that_served_the_assembly(client, room):
    """The port's hard rule 4: the source comes from the router, not a literal.

    The branch defaulted ``source`` to the string "POST /api/library/assemble"
    inside the domain function. The route now builds it from ``router.prefix``, so
    the audit row cannot drift away from the path that is actually mounted.
    """
    found = client.post(f"{PREFIX}/search", json={"term": "pricing"}).json()["documents"]

    client.post(
        f"{PREFIX}/assemble",
        json={"room_id": room, "items": [{"id": found[0]["id"]}]},
        params={"actor": "dana"},
    )

    entry = client.get("/api/audit", params={"collection": "room_content"}).json()["entries"][0]
    assert entry["source"] == "POST /api/library/assemble"


def test_assembly_provenance_is_on_the_content_records(client, room):
    """Provenance is recorded per record, which is where a reader looks for it.

    The branch also pushed ``search_id`` and ``client_application`` into the audit
    row via a ``context=`` keyword it added to ``bulk_create`` in the shared
    ``dsr/db/audited.py``. That file cannot be edited by a feature, so the port
    records provenance on the records instead - the same facts, a reachable place.
    """
    found = client.post(f"{PREFIX}/search", json={"term": "pricing"}).json()["documents"]

    client.post(
        f"{PREFIX}/assemble",
        json={"room_id": room, "items": [{"id": found[0]["id"]}], "search_id": "library_search_1"},
        params={"actor": "dana"},
        headers={"X-Client-Details": encode_client_details({"application": "sales-room-ui"})},
    )

    data = client.get(
        "/api/records/room_content", params={"room_id": room}
    ).json()["records"][0]["data"]
    assert data["content_id"] == found[0]["id"]
    assert data["search_id"] == "library_search_1"
    assert data["client_application"] == "sales-room-ui"


def test_assembling_the_same_document_twice_reports_it_as_skipped(client, room):
    found = client.post(f"{PREFIX}/search", json={"term": "pricing"}).json()["documents"]
    payload = {"room_id": room, "items": [{"id": found[0]["id"]}]}

    client.post(f"{PREFIX}/assemble", json=payload)
    second = client.post(f"{PREFIX}/assemble", json=payload).json()

    assert second["added_count"] == 0
    assert second["skipped"] == [found[0]["id"]]


def test_assembling_into_a_missing_room_is_404(client):
    response = client.post(
        f"{PREFIX}/assemble", json={"room_id": "room_missing", "items": [{"id": "x"}]}
    )

    assert response.status_code == 404


def test_assemble_needs_a_room_and_items(client):
    assert client.post(f"{PREFIX}/assemble", json={"items": [{"id": "x"}]}).status_code == 400
    assert client.post(f"{PREFIX}/assemble", json={"room_id": "r", "items": []}).status_code == 400


def test_assembly_records_the_calling_application(client, room):
    found = client.post(f"{PREFIX}/search", json={"term": "pricing"}).json()["documents"]

    client.post(
        f"{PREFIX}/assemble",
        json={"room_id": room, "items": [{"id": found[0]["id"]}]},
        headers={"X-Client-Details": encode_client_details({"application": "sales-room-ui"})},
    )

    data = client.get("/api/records/room_content", params={"room_id": room}).json()["records"][0]["data"]
    assert data["client_application"] == "sales-room-ui"


# -- searching is a read ----------------------------------------------------- #


def test_searching_never_writes_to_the_audit_log(client):
    before = client.get("/api/audit").json()["count"]

    client.post(f"{PREFIX}/search", json={"term": "security"})
    client.get(f"{PREFIX}/contract")
    client.get(f"{PREFIX}/fields")

    assert client.get("/api/audit").json()["count"] == before


# -- saved searches ---------------------------------------------------------- #


def test_a_saved_search_round_trips_and_is_audited(client):
    created = client.post(
        f"{PREFIX}/searches",
        json={
            "name": "Security packs",
            "query": {"term": "security", "filter": {"field": "profile", "value": "pack"}},
        },
        params={"actor": "dana"},
    )

    assert created.status_code == 201
    record_id = created.json()["id"]
    assert created.json()["data"]["query"]["term"] == "security"

    listed = client.get(f"{PREFIX}/searches").json()
    assert listed["count"] == 1
    assert client.get(f"{PREFIX}/searches/{record_id}").json()["id"] == record_id
    assert client.get("/api/audit", params={"collection": "library_search"}).json()["count"] == 1


def test_saved_search_writes_audit_the_route_that_served_them(client):
    """Both the create and the delete name the mounted path, not a baked-in one."""
    record_id = client.post(f"{PREFIX}/searches", json={"name": "Owned", "query": {}}).json()["id"]

    client.delete(f"{PREFIX}/searches/{record_id}")

    sources = [entry["source"] for entry in client.get(
        "/api/audit", params={"collection": "library_search"}
    ).json()["entries"]]
    assert sources == [
        f"DELETE {PREFIX}/searches/{record_id}",
        f"POST {PREFIX}/searches",
    ]


def test_a_saved_search_is_validated_on_the_way_in(client):
    response = client.post(
        f"{PREFIX}/searches", json={"name": "Broken", "query": {"term": "a" * 200}}
    )

    assert response.status_code == 400
    assert client.get("/api/audit", params={"collection": "library_search"}).json()["count"] == 0


def test_a_saved_search_needs_a_name(client):
    assert client.post(f"{PREFIX}/searches", json={"query": {}}).status_code == 400


def test_deleting_a_saved_search_is_audited(client):
    record_id = client.post(f"{PREFIX}/searches", json={"name": "Temp", "query": {}}).json()["id"]

    assert client.delete(f"{PREFIX}/searches/{record_id}").status_code == 200
    assert client.get(f"{PREFIX}/searches").json()["count"] == 0
    assert client.get("/api/audit", params={"collection": "library_search"}).json()["count"] == 2
    assert client.get(f"{PREFIX}/searches/{record_id}").status_code == 404
    assert client.delete(f"{PREFIX}/searches/{record_id}").status_code == 404


def test_saved_searches_carry_arbitrary_extra_fields(client):
    created = client.post(
        f"{PREFIX}/searches", json={"name": "Owned", "query": {"term": "deck", "ticket": "JIRA-42"}}
    ).json()

    assert created["data"]["ticket"] == "JIRA-42"
