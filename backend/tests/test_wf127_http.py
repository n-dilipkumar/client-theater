"""HTTP tests for WF-127: the mounted routes, the event ingest, and the audit trail.

These drive the mounted app through ``TestClient`` rather than calling the book,
so they exercise the same path a real client does. If the feature failed to
register, or collided with another feature's route, every test here would 404 -
which is the point: the router is mounted by discovery alone and
``backend/dsr/api.py`` is not edited.

Each test names the rule it protects. One test is worth reading twice: the audit
test asserts that every row this feature wrote carries a ``source`` that names a
route the host actually serves, which is the recurring defect the build brief
calls out.

The database is a temporary file per test, set through ``DSR_DB_PATH``, which
:mod:`dsr.deps` reads at call time. Nothing here depends on a row another test
wrote, so the file passes alone and under ``pytest-xdist`` in any order.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from fastapi.testclient import TestClient

PREFIX = "/api/wf-127"
FEATURE_ID = "wf-127-demo-analytics"

#: This workflow's own collections, whose audit rows are the ones to check.
OWN_COLLECTIONS = {
    "wf127_demo",
    "wf127_demo_event",
    "wf127_demo_share",
    "wf127_demo_stakeholder",
}

NOW = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)

_PLACEHOLDER = re.compile(r"\{[^}]+\}")


# --------------------------------------------------------------------------- #
# Fixtures and helpers
# --------------------------------------------------------------------------- #


def make_room(client: TestClient, name: str = "Northwind") -> dict:
    response = client.post(
        "/api/records/room", json={"name": name, "account": name}, params={"actor": "dana"}
    )
    assert response.status_code == 201, response.text
    return response.json()


def make_demo(client: TestClient, room_id: str, **overrides) -> dict:
    payload = {
        "title": "Northwind onboarding sandbox",
        "demo_type": "sandbox",
        "team": "Enterprise",
        "owner": "dana",
    }
    payload.update(overrides)
    response = client.post(
        f"{PREFIX}/demos", json=payload, params={"room_id": room_id, "actor": "dana"}
    )
    assert response.status_code == 201, response.text
    return response.json()


def publish(client: TestClient, demo_id: str) -> dict:
    response = client.post(f"{PREFIX}/demos/{demo_id}/publish", params={"actor": "dana"})
    assert response.status_code == 200, response.text
    return response.json()


def share(client: TestClient, demo_id: str, **payload) -> dict:
    response = client.post(
        f"{PREFIX}/demos/{demo_id}/shares", json=payload, params={"actor": "dana"}
    )
    assert response.status_code == 201, response.text
    return response.json()


def record(client: TestClient, demo_id: str, **payload) -> dict:
    response = client.post(
        f"{PREFIX}/demos/{demo_id}/events", json=payload, params={"actor": "system"}
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
    """Is an audit ``source`` a route the app serves? ``POST /api/wf-127/demos`` -> yes."""
    verb, _, path = source.partition(" ")
    pattern = _PLACEHOLDER.sub("[^/]+", path)
    return any(method == verb and re.fullmatch(pattern, path_) for method, path_ in routes)


# --------------------------------------------------------------------------- #
# Discovery and the published contract
# --------------------------------------------------------------------------- #


def test_the_router_is_mounted_by_discovery(client: TestClient):
    """The feature registers by adding a file. api.py is not edited."""
    response = client.get(f"{PREFIX}/summary")
    assert response.status_code == 200
    assert "demos" in response.json()


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
    assert mine[0]["ticket"] == "WF-127"
    assert mine[0]["exception_handlers"] == ["DemoAnalyticsError"]
    prefixes = [f["prefix"] for f in body["features"] if f["prefix"]]
    assert prefixes.count(PREFIX) == 1


def test_the_feature_is_navigable_without_editing_app_jsx(client: TestClient):
    entry = next(f for f in client.get("/api/features").json()["features"] if f["prefix"] == PREFIX)
    assert entry["nav"] == [{"id": "demo-analytics", "label": "Interactive demos"}]


def test_the_vocabulary_serves_the_researched_metric_names(client: TestClient):
    body = client.get(f"{PREFIX}/vocabulary").json()
    assert body["metric_names"][0] == "demos_sent"
    assert "docs_downloaded" in body["metric_names"]
    assert body["levels"][0] == "buyer_group"
    assert body["defaults"]["dql_threshold"] == 60


def test_the_inferences_route_states_the_open_decisions(client: TestClient):
    body = client.get(f"{PREFIX}/inferences").json()
    ids = {entry["id"] for entry in body["inferences"]}
    assert {"stakeholder_discovery_attribution", "dql_formula", "no_crm_write"} <= ids
    for entry in body["inferences"]:
        assert entry["decision"] and entry["why"] and entry["change_it_by"]


# --------------------------------------------------------------------------- #
# The demos
# --------------------------------------------------------------------------- #


def test_a_demo_is_built_from_a_recording_with_a_branching_tree(client: TestClient):
    room = make_room(client)
    demo = make_demo(client, room["id"])
    assert demo["state"] == "draft"
    assert demo["node_count"] == 5
    assert demo["branch_count"] >= 3  # the click path branches
    assert demo["entry"] == "overview"


def test_a_demo_for_an_unknown_room_is_not_found(client: TestClient):
    response = client.post(
        f"{PREFIX}/demos", json={"title": "x"}, params={"room_id": "room_absent"}
    )
    assert response.status_code == 404, response.text


def test_a_demo_with_no_title_is_refused(client: TestClient):
    room = make_room(client)
    response = client.post(f"{PREFIX}/demos", json={}, params={"room_id": room["id"]})
    assert response.status_code == 422, response.text
    assert response.json()["error"] == "invalid_demo"


def test_a_demo_is_listed_read_and_patched(client: TestClient):
    room = make_room(client)
    demo = make_demo(client, room["id"])

    listed = client.get(f"{PREFIX}/demos", params={"room_id": room["id"]}).json()
    assert listed["count"] == 1
    assert listed["demos"][0]["id"] == demo["id"]

    read = client.get(f"{PREFIX}/demos/{demo['id']}")
    assert read.status_code == 200

    patched = client.patch(
        f"{PREFIX}/demos/{demo['id']}", json={"title": "Renamed"}, params={"actor": "dana"}
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["title"] == "Renamed"


def test_an_unknown_demo_is_not_found(client: TestClient):
    assert client.get(f"{PREFIX}/demos/wf127_demo_absent").status_code == 404
    assert client.get(f"{PREFIX}/demos/wf127_demo_absent/analytics").status_code == 404
    assert client.get(f"{PREFIX}/demos/wf127_demo_absent/heatmap").status_code == 404
    assert client.get(f"{PREFIX}/demos/wf127_demo_absent/sections").status_code == 404
    assert client.get(f"{PREFIX}/demos/wf127_demo_absent/stakeholders").status_code == 404
    assert client.get(f"{PREFIX}/demos/wf127_demo_absent/rollup").status_code == 404


def test_a_demo_publishes_and_an_archived_demo_refuses_an_event(client: TestClient):
    room = make_room(client)
    demo = make_demo(client, room["id"])
    assert publish(client, demo["id"])["state"] == "published"
    archived = client.post(f"{PREFIX}/demos/{demo['id']}/archive", params={"actor": "dana"})
    assert archived.status_code == 200, archived.text
    assert archived.json()["state"] == "archived"

    refused = client.post(
        f"{PREFIX}/demos/{demo['id']}/events",
        json={"viewer": "a@b.example", "event": "view"},
    )
    assert refused.status_code == 410, refused.text
    assert refused.json()["error"] == "demo_archived"


# --------------------------------------------------------------------------- #
# Distribution and the event stream
# --------------------------------------------------------------------------- #


def test_an_initial_share_names_the_recipients(client: TestClient):
    room = make_room(client)
    demo = make_demo(client, room["id"])
    result = share(client, demo["id"], kind="initial", recipients=["a@b.example", "c@d.example"])
    assert result["count"] == 2
    assert result["stakeholders_discovered"] == 0


def test_a_forward_share_discovers_a_stakeholder(client: TestClient):
    room = make_room(client)
    demo = make_demo(client, room["id"])
    share(client, demo["id"], kind="initial", recipients=["a@b.example"])
    result = share(
        client, demo["id"], kind="forward", sender="a@b.example", recipients=["cfo@b.example"]
    )
    assert result["stakeholders_discovered"] == 1
    people = client.get(f"{PREFIX}/demos/{demo['id']}/stakeholders").json()["stakeholders"]
    roles = {row["viewer"]: row["role"] for row in people}
    assert roles["cfo@b.example"] == "discovered"


def test_a_forward_share_without_a_sender_is_refused(client: TestClient):
    room = make_room(client)
    demo = make_demo(client, room["id"])
    response = client.post(
        f"{PREFIX}/demos/{demo['id']}/shares",
        json={"kind": "forward", "recipients": ["a@b.example"]},
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"] == "share_refused"


def test_a_view_records_the_event_and_computes_the_dql_score(client: TestClient):
    room = make_room(client)
    demo = make_demo(client, room["id"])
    body = record(client, demo["id"], viewer="a@b.example", event="view")
    assert body["event"] == "view"
    assert body["stakeholder"]["role"] == "direct"
    assert body["dql"]["score"] == 30
    assert body["dql"]["qualified"] is False


def test_a_view_from_a_forwarded_viewer_is_discovered(client: TestClient):
    room = make_room(client)
    demo = make_demo(client, room["id"])
    share(client, demo["id"], kind="initial", recipients=["a@b.example"])
    body = record(
        client, demo["id"], viewer="cfo@b.example", event="view", shared_from="a@b.example"
    )
    assert body["discovered"] is True
    assert body["stakeholder"]["role"] == "discovered"
    assert body["stakeholder"]["invited_by"] == "a@b.example"


def test_a_section_the_demo_lacks_is_refused(client: TestClient):
    room = make_room(client)
    demo = make_demo(client, room["id"])
    response = client.post(
        f"{PREFIX}/demos/{demo['id']}/events",
        json={"viewer": "a@b.example", "event": "section_selected", "section": "ghost"},
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"] == "invalid_demo"


def test_an_event_carrying_a_recording_is_refused_by_name(client: TestClient):
    """The researched privacy boundary: no recording or document body."""
    room = make_room(client)
    demo = make_demo(client, room["id"])
    response = client.post(
        f"{PREFIX}/demos/{demo['id']}/events",
        json={"viewer": "a@b.example", "event": "view", "recording": "the whole capture"},
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"] == "event_refused"
    assert response.json()["field"] == "recording"


def test_a_view_without_a_viewer_is_refused(client: TestClient):
    room = make_room(client)
    demo = make_demo(client, room["id"])
    response = client.post(f"{PREFIX}/demos/{demo['id']}/events", json={"event": "view"})
    assert response.status_code == 422, response.text
    assert response.json()["error"] == "event_refused"


# --------------------------------------------------------------------------- #
# The analytics
# --------------------------------------------------------------------------- #


def _seed_reads(client: TestClient, room_id: str) -> dict:
    demo = make_demo(client, room_id)
    publish(client, demo["id"])
    share(client, demo["id"], kind="initial", recipients=["a@b.example", "never@b.example"])
    record(client, demo["id"], viewer="a@b.example", event="view", occurred_at=NOW.isoformat())
    record(
        client,
        demo["id"],
        viewer="a@b.example",
        event="section_selected",
        section="dashboard",
        occurred_at=NOW.isoformat(),
    )
    record(
        client,
        demo["id"],
        viewer="a@b.example",
        event="doc_downloaded",
        section="dashboard",
        occurred_at=NOW.isoformat(),
    )
    record(
        client,
        demo["id"],
        viewer="dana@b.example",
        event="view",
        is_internal=True,
        occurred_at=NOW.isoformat(),
    )
    return demo


def test_the_analytics_block_reads_back_every_researched_metric(client: TestClient):
    room = make_room(client)
    demo = _seed_reads(client, room["id"])
    body = client.get(f"{PREFIX}/demos/{demo['id']}/analytics").json()
    block = body["metrics"]
    assert block["demos_sent"] == 2
    assert block["views"] == 1
    assert block["view_rate"] == 0.5
    assert block["features_selected"] == 1
    assert block["docs_downloaded"] == 1
    assert block["internal_previews"] == 1
    assert len(body["heatmap"]["cells"]) == 1
    assert body["sections"]["sections"][1]["count"] == 1
    viewers = [row["viewer"] for row in body["stakeholders"]]
    # The internal preview is excluded; the recipient who never opened is not.
    assert viewers == ["a@b.example", "never@b.example"]


def test_the_heatmap_route_buckets_by_weekday_and_hour(client: TestClient):
    room = make_room(client)
    demo = _seed_reads(client, room["id"])
    body = client.get(f"{PREFIX}/demos/{demo['id']}/heatmap").json()
    assert body["bucket_hours"] == 4
    assert body["cells"][0]["weekday"] == "Friday"
    assert body["total"] == 3  # the internal preview is not on the buyer heatmap


def test_the_sections_route_counts_each_node(client: TestClient):
    room = make_room(client)
    demo = _seed_reads(client, room["id"])
    body = client.get(f"{PREFIX}/demos/{demo['id']}/sections").json()
    assert body["total"] == 1
    assert body["most_selected"]["section"] == "dashboard"


def test_the_rollup_route_serves_every_level(client: TestClient):
    room = make_room(client)
    demo = _seed_reads(client, room["id"])
    for level in ("buyer_group", "top_level", "team", "individual", "recently_shared", "all_demos"):
        body = client.get(f"{PREFIX}/demos/{demo['id']}/rollup", params={"level": level}).json()
        assert body["level"] == level
        assert body["groups"], level


def test_an_unknown_rollup_level_is_refused(client: TestClient):
    room = make_room(client)
    demo = make_demo(client, room["id"])
    response = client.get(f"{PREFIX}/demos/{demo['id']}/rollup", params={"level": "galaxy"})
    assert response.status_code == 422, response.text
    assert response.json()["error"] == "invalid_demo"


def test_the_room_stakeholders_route_lists_the_discovered(client: TestClient):
    room = make_room(client)
    demo = make_demo(client, room["id"])
    share(client, demo["id"], kind="initial", recipients=["a@b.example"])
    share(client, demo["id"], kind="forward", sender="a@b.example", recipients=["cfo@b.example"])
    body = client.get(f"{PREFIX}/stakeholders", params={"room_id": room["id"]}).json()
    assert body["count"] == 2
    assert {row["role"] for row in body["stakeholders"]} == {"initial_recipient", "discovered"}


def test_the_summary_counts_the_scope(client: TestClient):
    room = make_room(client)
    demo = _seed_reads(client, room["id"])
    body = client.get(f"{PREFIX}/summary", params={"room_id": room["id"]}).json()
    assert body["demos"] == 1
    assert body["published"] == 1
    assert body["demos_sent"] == 2
    assert body["views"] == 1
    assert body["internal_previews"] == 1
    assert demo["id"]


# --------------------------------------------------------------------------- #
# The audit trail, and the rule that every source names a served route
# --------------------------------------------------------------------------- #


def test_every_write_this_feature_made_names_a_route_the_app_serves(client: TestClient):
    """The recurring defect the brief names: an audit row pointing at a dead path."""
    room = make_room(client)
    demo = make_demo(client, room["id"])
    publish(client, demo["id"])
    share(client, demo["id"], kind="initial", recipients=["a@b.example"])
    record(client, demo["id"], viewer="a@b.example", event="view")

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
    demo = _seed_reads(client, room["id"])
    get_routes = [
        route
        for route in mounted_routes(client)
        if route[0] == "GET" and route[1].startswith(PREFIX)
    ]
    for _method, path in get_routes:
        concrete = path.replace("{demo_id}", demo["id"])
        response = client.get(concrete)
        assert response.status_code < 500, f"{concrete} -> {response.status_code}"
