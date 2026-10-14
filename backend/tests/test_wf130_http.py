"""HTTP tests for WF-130: the mounted routes, the review flow, the room publish
and the audit trail.

These drive the mounted app through ``TestClient`` rather than calling the book,
so they exercise the same path a real client does. If the feature failed to
register, or collided with another feature's route, every test here would 404 -
which is the point: the router is mounted by discovery alone and
``backend/dsr/api.py`` is not edited.

One test is worth reading twice: the audit test asserts that every row this
feature wrote carries a ``source`` that names a route the host actually serves,
including the page and revision the publishing seam wrote on this workflow's
behalf.

The database is a temporary file per test, set through ``DSR_DB_PATH``, which
:mod:`dsr.deps` reads at call time. Nothing here depends on a row another test
wrote, so the file passes alone and under ``pytest-xdist`` in any order.
"""

from __future__ import annotations

import re

from fastapi.testclient import TestClient

PREFIX = "/api/wf-130"
FEATURE_ID = "wf-130-collateral-generation"

#: This workflow's own collections, whose audit rows are the ones to check.
OWN_COLLECTIONS = {"wf130_collateral", "wf130_source_asset", "wf130_collateral_event"}

DEAL = {"account": "Northwind", "contact": "Ava Chen", "product": "DSR", "stage": "discovery"}

_PLACEHOLDER = re.compile(r"\{[^}]+\}")

#: The publishing seam appends a note to the source of the page revision and the
#: page pointer ("write revision 1", "point page at revision 1"). It still names
#: this workflow's route, so the suffix is stripped before matching.
_SUFFIX = re.compile(r" \((?:write revision|point page at revision|withdraw page)[^)]*\)$")


# --------------------------------------------------------------------------- #
# Fixtures and helpers
# --------------------------------------------------------------------------- #


def make_room(client: TestClient, name: str = "Northwind") -> dict:
    response = client.post(
        "/api/records/room", json={"name": name, "account": name}, params={"actor": "dana"}
    )
    assert response.status_code == 201, response.text
    return response.json()


def make_asset(client: TestClient, **overrides) -> dict:
    payload = {
        "key": "northwind-case-study",
        "kind": "case_study",
        "body": "Proof for {{account}}.",
    }
    payload.update(overrides)
    response = client.post(f"{PREFIX}/assets", json=payload, params={"actor": "dana"})
    assert response.status_code == 201, response.text
    return response.json()


def make_collateral(client: TestClient, room_id: str, **overrides) -> dict:
    payload = {"deal": dict(DEAL), "template_key": "proposal_document"}
    payload.update(overrides)
    response = client.post(
        f"{PREFIX}/collateral", json=payload, params={"room_id": room_id, "actor": "dana"}
    )
    assert response.status_code == 201, response.text
    return response.json()


def mounted_routes(client: TestClient) -> set[tuple[str, str]]:
    body = client.get("/api/features").json()
    return {
        (method, route["path"])
        for feature in body["features"]
        for route in feature["routes"]
        for method in route["methods"]
    }


def source_matches_route(source: str, routes: set[tuple[str, str]]) -> bool:
    """Is an audit ``source`` a route the app serves? ``POST /api/wf-130/assets`` -> yes."""
    text = _SUFFIX.sub("", source)
    verb, _, path = text.partition(" ")
    pattern = _PLACEHOLDER.sub("[^/]+", path)
    return any(method == verb and re.fullmatch(pattern, path_) for method, path_ in routes)


# --------------------------------------------------------------------------- #
# Discovery and the published contract
# --------------------------------------------------------------------------- #


def test_the_router_is_mounted_by_discovery(client: TestClient):
    """The feature registers by adding a file. api.py is not edited."""
    response = client.get(f"{PREFIX}/summary")
    assert response.status_code == 200
    assert "collaterals" in response.json()


def test_the_feature_appears_in_the_registry(client: TestClient):
    body = client.get("/api/features").json()
    ids = [f.get("id") for f in body.get("features", [])]
    assert FEATURE_ID in ids
    assert body["failed_count"] == 0, body["failed"]


def test_the_prefix_is_this_workflows_own(client: TestClient):
    body = client.get("/api/features").json()
    mine = [f for f in body["features"] if f["id"] == FEATURE_ID]
    assert len(mine) == 1
    assert mine[0]["prefix"] == PREFIX
    assert mine[0]["ticket"] == "WF-130"
    assert mine[0]["exception_handlers"] == ["CollateralError"]
    prefixes = [f["prefix"] for f in body["features"] if f["prefix"]]
    assert prefixes.count(PREFIX) == 1


def test_the_feature_is_navigable_without_editing_app_jsx(client: TestClient):
    entry = next(f for f in client.get("/api/features").json()["features"] if f["prefix"] == PREFIX)
    assert entry["nav"] == [{"id": "collateral-generation", "label": "Collateral generation"}]


def test_the_vocabulary_serves_the_researched_values(client: TestClient):
    body = client.get(f"{PREFIX}/vocabulary").json()
    assert body["kinds"] == ["document", "deck"]
    assert body["deal_stages"][0] == "discovery"
    assert body["default_template"] == "proposal_document"


def test_the_decisions_route_states_the_open_questions(client: TestClient):
    body = client.get(f"{PREFIX}/decisions").json()
    ids = set(body["decisions"])
    assert {"assembly_engine", "llm_provider", "artifacts", "readiness", "publish_seam"} <= ids
    assert body["not_implemented"]


# --------------------------------------------------------------------------- #
# The content library
# --------------------------------------------------------------------------- #


def test_a_source_asset_is_registered_and_listed(client: TestClient):
    asset = make_asset(client)
    assert asset["version"] == 1
    listing = client.get(f"{PREFIX}/assets").json()
    assert listing["count"] == 1
    assert listing["assets"][0]["key"] == "northwind-case-study"


def test_a_duplicate_asset_key_is_refused(client: TestClient):
    make_asset(client)
    response = client.post(f"{PREFIX}/assets", json={"key": "northwind-case-study"})
    assert response.status_code == 409, response.text
    assert response.json()["error"] == "collateral_refused"


# --------------------------------------------------------------------------- #
# Generation and review
# --------------------------------------------------------------------------- #


def test_a_collateral_is_generated_personalised_and_listed(client: TestClient):
    room = make_room(client)
    make_asset(client)
    collateral = make_collateral(client, room["id"])
    assert collateral["state"] == "draft"
    assert collateral["engine"] == "rules"
    assert collateral["section_count"] == 5
    overview = next(s for s in collateral["sections"] if s["key"] == "overview")
    assert "Northwind" in overview["body"]

    listed = client.get(f"{PREFIX}/collateral", params={"room_id": room["id"]}).json()
    assert listed["count"] == 1
    assert listed["collateral"][0]["id"] == collateral["id"]


def test_a_collateral_for_an_unknown_room_is_not_found(client: TestClient):
    response = client.post(
        f"{PREFIX}/collateral", json={"deal": DEAL}, params={"room_id": "room_absent"}
    )
    assert response.status_code == 404, response.text
    assert response.json()["error"] == "collateral_not_found"


def test_a_blank_deal_is_refused(client: TestClient):
    room = make_room(client)
    response = client.post(
        f"{PREFIX}/collateral",
        json={"deal": {"stage": "discovery"}},
        params={"room_id": room["id"]},
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"] == "invalid_collateral"


def test_an_unknown_collateral_is_not_found(client: TestClient):
    assert client.get(f"{PREFIX}/collateral/wf130_collateral_absent").status_code == 404
    assert client.get(f"{PREFIX}/collateral/wf130_collateral_absent/events").status_code == 404


def test_advancing_the_stage_reveals_a_hidden_section(client: TestClient):
    room = make_room(client)
    make_asset(client)
    collateral = make_collateral(client, room["id"])
    assert collateral["hidden_count"] == 1

    moved = client.patch(
        f"{PREFIX}/collateral/{collateral['id']}",
        json={"deal": {"stage": "procurement"}},
        params={"actor": "dana"},
    )
    assert moved.status_code == 200, moved.text
    body = moved.json()
    assert body["visible_count"] == 5
    assert "pricing" in body["visible_sections"]


def test_an_edit_to_an_unknown_section_is_refused(client: TestClient):
    room = make_room(client)
    make_asset(client)
    collateral = make_collateral(client, room["id"])
    response = client.patch(
        f"{PREFIX}/collateral/{collateral['id']}",
        json={"sections": [{"key": "ghost", "body": "x"}]},
    )
    assert response.status_code == 422, response.text


# --------------------------------------------------------------------------- #
# Publish, the room seam, and withdrawal
# --------------------------------------------------------------------------- #


def test_publishing_writes_a_room_page_through_the_seam(client: TestClient):
    room = make_room(client)
    make_asset(client)
    collateral = make_collateral(client, room["id"])
    response = client.post(
        f"{PREFIX}/collateral/{collateral['id']}/publish", params={"actor": "dana"}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["state"] == "published"
    assert body["room_page_id"]

    page = client.get(f"/api/records/page/{body['room_page_id']}")
    assert page.status_code == 200, page.text
    assert page.json()["data"]["status"] == "published"
    assert page.json()["data"]["published_revision_id"]


def test_publishing_an_empty_always_visible_section_is_refused(client: TestClient):
    room = make_room(client)
    make_asset(client)
    collateral = make_collateral(client, room["id"])
    client.patch(
        f"{PREFIX}/collateral/{collateral['id']}",
        json={"sections": [{"key": "overview", "body": ""}]},
        params={"actor": "dana"},
    )
    response = client.post(
        f"{PREFIX}/collateral/{collateral['id']}/publish", params={"actor": "dana"}
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"] == "collateral_refused"


def test_a_withdrawn_collateral_refuses_an_edit_and_a_publish(client: TestClient):
    room = make_room(client)
    make_asset(client)
    collateral = make_collateral(client, room["id"])
    client.post(f"{PREFIX}/collateral/{collateral['id']}/publish", params={"actor": "dana"})
    withdrawn = client.post(
        f"{PREFIX}/collateral/{collateral['id']}/withdraw", params={"actor": "dana"}
    )
    assert withdrawn.status_code == 200, withdrawn.text
    assert withdrawn.json()["state"] == "withdrawn"

    edit = client.patch(f"{PREFIX}/collateral/{collateral['id']}", json={"title": "Nope"})
    assert edit.status_code == 409, edit.text
    republish = client.post(f"{PREFIX}/collateral/{collateral['id']}/publish")
    assert republish.status_code == 409, republish.text


def test_the_events_route_serves_the_artefact_trail(client: TestClient):
    room = make_room(client)
    make_asset(client)
    collateral = make_collateral(client, room["id"])
    client.post(f"{PREFIX}/collateral/{collateral['id']}/publish", params={"actor": "dana"})
    events = client.get(f"{PREFIX}/collateral/{collateral['id']}/events").json()
    kinds = [event["kind"] for event in events["events"]]
    assert "generated" in kinds
    assert "published" in kinds


# --------------------------------------------------------------------------- #
# The synced refresh
# --------------------------------------------------------------------------- #


def test_a_changed_asset_refreshes_the_live_collateral(client: TestClient):
    room = make_room(client)
    asset = make_asset(client)
    collateral = make_collateral(client, room["id"])
    client.post(f"{PREFIX}/collateral/{collateral['id']}/publish", params={"actor": "dana"})

    client.patch(
        f"{PREFIX}/assets/{asset['id']}", json={"body": "Revised proof."}, params={"actor": "dana"}
    )
    response = client.post(f"{PREFIX}/assets/{asset['id']}/refresh", params={"actor": "dana"})
    assert response.status_code == 200, response.text
    assert response.json()["refreshed"] == 1

    refreshed = client.get(f"{PREFIX}/collateral/{collateral['id']}").json()
    section = next(s for s in refreshed["sections"] if s["key"] == "case_study")
    assert section["asset_version"] == 2
    assert section["body"].endswith("Revised proof.")


def test_the_summary_counts_the_scope(client: TestClient):
    room = make_room(client)
    make_asset(client)
    collateral = make_collateral(client, room["id"])
    client.post(f"{PREFIX}/collateral/{collateral['id']}/publish", params={"actor": "dana"})
    body = client.get(f"{PREFIX}/summary", params={"room_id": room["id"]}).json()
    assert body["collaterals"] == 1
    assert body["published"] == 1
    assert body["hidden_sections"] == 1
    assert body["assets"] == 1


# --------------------------------------------------------------------------- #
# The audit trail, and the rule that every source names a served route
# --------------------------------------------------------------------------- #


def test_every_write_this_feature_made_names_a_route_the_app_serves(client: TestClient):
    """The recurring defect the brief names: an audit row pointing at a dead path."""
    room = make_room(client)
    asset = make_asset(client)
    collateral = make_collateral(client, room["id"])
    client.post(f"{PREFIX}/collateral/{collateral['id']}/publish", params={"actor": "dana"})
    client.patch(f"{PREFIX}/assets/{asset['id']}", json={"body": "x"}, params={"actor": "dana"})
    client.post(f"{PREFIX}/assets/{asset['id']}/refresh", params={"actor": "dana"})

    entries = client.get("/api/audit", params={"limit": 1000}).json()["entries"]
    routes = mounted_routes(client)
    mine = [entry for entry in entries if PREFIX in str(entry.get("source") or "")]
    assert mine, "the feature wrote no audited rows, so this proves nothing"
    for entry in mine:
        assert source_matches_route(str(entry["source"]), routes), entry["source"]
    # And it really did write into its own collections.
    assert OWN_COLLECTIONS & {entry["collection"] for entry in mine}


def test_no_route_this_feature_advertises_returns_a_5xx(client: TestClient):
    """A light version of tools/verify_all_routes.py, scoped to this feature."""
    room = make_room(client)
    make_asset(client)
    collateral = make_collateral(client, room["id"])
    get_routes = [
        route
        for route in mounted_routes(client)
        if route[0] == "GET" and route[1].startswith(PREFIX)
    ]
    for _method, path in get_routes:
        concrete = path.replace("{collateral_id}", collateral["id"]).replace(
            "{asset_id}", "wf130_asset_x"
        )
        response = client.get(concrete)
        assert response.status_code < 500, f"{concrete} -> {response.status_code}"
