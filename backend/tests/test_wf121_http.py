"""HTTP tests for WF-121: the mounted routes, the view ingest, and the audit trail.

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

No test asserts an age measured against the wall clock. The clock is frozen where
a value depends on it, and elsewhere the test asserts a field is present and
typed rather than its exact value.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

PREFIX = "/api/wf-121"
FEATURE_ID = "wf-121-tracked-deal-link"

#: This workflow's own collections, whose audit rows are the ones to check.
OWN_COLLECTIONS = {
    "wf121_tracked_link",
    "wf121_link_page_view",
    "wf121_link_delivery",
    "wf121_link_alert",
}

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


def make_asset(
    client: TestClient, room_id: str, external_id: str = "deck-demo", pages: int = 10
) -> dict:
    """Register a PDF through WF-018's own route, which is the asset this link shares."""
    response = client.post(
        "/api/wf-018/assets",
        json={
            "name": "Enterprise Security & Compliance Overview",
            "type": "pdf",
            "externalId": external_id,
            "pageCount": pages,
        },
        params={"room_id": room_id, "actor": "dana"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def make_link(client: TestClient, room_id: str, **overrides) -> dict:
    payload = {
        "asset_id": "deck-demo",
        "title": "Security overview",
        "recipients": ["a.buyer@northwind.example"],
        "crm_system": "salesforce",
        "crm_deal_id": "006-nw-5001",
    }
    payload.update(overrides)
    response = client.post(
        f"{PREFIX}/links", json=payload, params={"room_id": room_id, "actor": "dana"}
    )
    assert response.status_code == 201, response.text
    return response.json()


def readings(viewer: str, pages: int, *, started: datetime, seconds: float = 30.0) -> dict:
    elapsed = 0.0
    timings = []
    for page in range(1, pages + 1):
        timings.append(
            {
                "page": page,
                "seconds": seconds,
                "occurred_at": (started + timedelta(seconds=elapsed)).isoformat(),
            }
        )
        elapsed += seconds + 3
    return {"viewer": viewer, "timings": timings}


def mounted_routes(client: TestClient) -> set[tuple[str, str]]:
    body = client.get("/api/features").json()
    return {
        (method, route["path"])
        for feature in body["features"]
        for route in feature["routes"]
        for method in route["methods"]
    }


def source_matches_route(source: str, routes: set[tuple[str, str]]) -> bool:
    """Is an audit ``source`` a route the app serves? ``POST /api/wf-121/links`` -> yes."""
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
    assert "links" in response.json()


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
    assert mine[0]["ticket"] == "WF-121"
    assert mine[0]["exception_handlers"] == ["TrackedLinkError"]
    prefixes = [f["prefix"] for f in body["features"] if f["prefix"]]
    assert prefixes.count(PREFIX) == 1


def test_the_feature_is_navigable_without_editing_app_jsx(client: TestClient):
    entry = next(f for f in client.get("/api/features").json()["features"] if f["prefix"] == PREFIX)
    assert entry["nav"] == [{"id": "tracked-deal-link", "label": "Tracked deal link"}]


def test_the_vocabulary_serves_the_reused_values(client: TestClient):
    """The shared half is published, so a reviewer sees WF-018's and WF-037's values."""
    body = client.get(f"{PREFIX}/vocabulary").json()
    assert body["reused"]["revisit_gap_seconds"] == 1800
    assert "WF-018" in body["reused"]["revisit_gap_source"]
    assert body["engagement_event_type"] == "tracked_deal_link_view"
    assert body["engagement_crm_object"] == "deal"
    assert "buyer_email" in body["engagement_fields"]


def test_the_inferences_route_states_the_open_decisions(client: TestClient):
    body = client.get(f"{PREFIX}/inferences").json()
    ids = {entry["id"] for entry in body["inferences"]}
    # The two the issue names as open, plus the reuse decisions.
    assert {"link_lifetime", "alert_page_threshold", "do_not_rebuild_the_gate"} <= ids
    for entry in body["inferences"]:
        assert entry["decision"] and entry["why"] and entry["change_it_by"]


# --------------------------------------------------------------------------- #
# The links
# --------------------------------------------------------------------------- #


def test_a_link_is_created_over_a_real_library_asset(client: TestClient):
    room = make_room(client)
    make_asset(client, room["id"])
    link = make_link(client, room["id"])
    assert link["asset_name"] == "Enterprise Security & Compliance Overview"
    assert link["page_count"] == 10
    assert link["state"] == "active"
    # The share URL is built from this router's own prefix.
    assert link["share_url"] == f"{PREFIX}/links/{link['id']}"


def test_a_link_for_an_unknown_room_is_not_found(client: TestClient):
    response = client.post(
        f"{PREFIX}/links", json={"asset_id": "x"}, params={"room_id": "room_absent"}
    )
    assert response.status_code == 404, response.text


def test_a_link_for_an_unknown_asset_is_refused(client: TestClient):
    room = make_room(client)
    response = client.post(
        f"{PREFIX}/links", json={"asset_id": "no-such-asset"}, params={"room_id": room["id"]}
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"] == "invalid_tracked_link"


def test_a_link_is_listed_read_and_patched(client: TestClient):
    room = make_room(client)
    make_asset(client, room["id"])
    link = make_link(client, room["id"])

    listed = client.get(f"{PREFIX}/links", params={"room_id": room["id"]}).json()
    assert listed["count"] == 1
    assert listed["links"][0]["id"] == link["id"]

    read = client.get(f"{PREFIX}/links/{link['id']}")
    assert read.status_code == 200

    patched = client.patch(
        f"{PREFIX}/links/{link['id']}", json={"alert_on_page": 7}, params={"actor": "dana"}
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["alert_on_page"] == 7


def test_an_unknown_link_is_not_found(client: TestClient):
    assert client.get(f"{PREFIX}/links/ln_absent").status_code == 404
    assert client.get(f"{PREFIX}/links/ln_absent/analytics").status_code == 404


def test_a_distribution_names_the_recipients_of_the_one_link(client: TestClient):
    room = make_room(client)
    make_asset(client, room["id"])
    link = make_link(client, room["id"])
    response = client.post(
        f"{PREFIX}/links/{link['id']}/deliveries",
        json={"recipients": ["a@northwind.example", "b@northwind.example"]},
        params={"actor": "dana"},
    )
    assert response.status_code == 201, response.text
    assert response.json()["count"] == 2
    listed = client.get(f"{PREFIX}/links/{link['id']}/deliveries").json()
    assert listed["count"] == 2


def test_a_revoked_link_is_closed_to_new_views(client: TestClient):
    room = make_room(client)
    make_asset(client, room["id"])
    link = make_link(client, room["id"])
    revoked = client.delete(f"{PREFIX}/links/{link['id']}", params={"actor": "dana"})
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["state"] == "revoked"

    refused = client.post(
        f"{PREFIX}/links/{link['id']}/views",
        json=readings("a.buyer@northwind.example", 2, started=datetime.now(timezone.utc)),
    )
    assert refused.status_code == 410, refused.text
    assert refused.json()["error"] == "tracked_link_closed"


# --------------------------------------------------------------------------- #
# The view ingest
# --------------------------------------------------------------------------- #


def test_a_view_records_pages_alerts_the_rep_and_queues_the_crm_write(client: TestClient):
    room = make_room(client)
    make_asset(client, room["id"])
    link = make_link(client, room["id"])
    started = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)

    first = client.post(
        f"{PREFIX}/links/{link['id']}/views",
        json=readings("a.buyer@northwind.example", 3, started=started),
        params={"actor": "system"},
    )
    assert first.status_code == 201, first.text
    body = first.json()
    assert body["revisit"] == 1
    assert body["deepest_page"] == 3
    assert {a["trigger"] for a in body["alerts"]} == {"first_view", "page_threshold"}
    # The engagement record is written locally and queued; nothing was sent.
    assert body["engagement"]["event_type"] == "tracked_deal_link_view"
    assert body["engagement"]["queue_state"] == "pending"
    assert body["engagement"]["fired"] is False

    second = client.post(
        f"{PREFIX}/links/{link['id']}/views",
        json=readings("a.buyer@northwind.example", 4, started=started + timedelta(days=1)),
        params={"actor": "system"},
    )
    assert second.json()["revisit"] == 2
    assert "return_visit" in {a["trigger"] for a in second.json()["alerts"]}


def test_a_view_without_a_viewer_is_refused_when_the_link_is_attributed(client: TestClient):
    room = make_room(client)
    make_asset(client, room["id"])
    link = make_link(client, room["id"])
    response = client.post(
        f"{PREFIX}/links/{link['id']}/views",
        json={"timings": [{"page": 1, "seconds": 5}]},
    )
    assert response.status_code == 428, response.text
    assert response.json()["error"] == "viewer_required"


def test_a_view_carrying_a_document_body_is_refused_by_name(client: TestClient):
    """The researched privacy boundary: no document bodies, no raw form values."""
    room = make_room(client)
    make_asset(client, room["id"])
    link = make_link(client, room["id"])
    response = client.post(
        f"{PREFIX}/links/{link['id']}/views",
        json={
            "viewer": "a.buyer@northwind.example",
            "content": "the whole deck",
            "timings": [{"page": 1, "seconds": 5}],
        },
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"] == "invalid_tracked_link"
    assert response.json()["field"] == "content"


def test_a_view_with_no_pages_is_refused(client: TestClient):
    room = make_room(client)
    make_asset(client, room["id"])
    link = make_link(client, room["id"])
    response = client.post(
        f"{PREFIX}/links/{link['id']}/views",
        json={"viewer": "a.buyer@northwind.example", "timings": []},
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"] == "dwell_refused"


def test_the_analytics_block_reads_back_the_shared_metrics(client: TestClient):
    room = make_room(client)
    make_asset(client, room["id"], pages=10)
    link = make_link(client, room["id"])
    started = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
    client.post(
        f"{PREFIX}/links/{link['id']}/views",
        json=readings("a.buyer@northwind.example", 4, started=started),
        params={"actor": "system"},
    )
    # An internal preview is recorded and does not count as a buyer reading.
    client.post(
        f"{PREFIX}/links/{link['id']}/views",
        json={
            "viewer": "dana@northwind.example",
            "is_internal": True,
            "timings": [{"page": 1, "seconds": 5}],
        },
        params={"actor": "system"},
    )

    body = client.get(f"{PREFIX}/links/{link['id']}/analytics").json()
    assert body["audience"] == "external"
    assert body["readings"] == 4
    assert body["page_count"] == 10
    assert len(body["time_spent_per_page"]) == 10
    assert len(body["drop_off_per_page"]) == 10
    assert body["session_gap_seconds"] == 1800
    assert [v["viewer"] for v in body["viewers"]] == ["a.buyer@northwind.example"]
    assert body["pages_never_read"] and 5 in body["pages_never_read"]


def test_the_viewers_and_one_viewers_pages_are_readable(client: TestClient):
    room = make_room(client)
    make_asset(client, room["id"])
    link = make_link(client, room["id"])
    started = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
    client.post(
        f"{PREFIX}/links/{link['id']}/views",
        json=readings("a.buyer@northwind.example", 3, started=started),
        params={"actor": "system"},
    )
    viewers = client.get(f"{PREFIX}/links/{link['id']}/viewers").json()
    assert viewers["count"] == 1
    assert viewers["viewers"][0]["revisits"] == 1

    detail = client.get(f"{PREFIX}/links/{link['id']}/viewers/a.buyer@northwind.example/views")
    assert detail.status_code == 200, detail.text
    assert detail.json()["readings"] == 3
    assert len(detail.json()["revisits"]) == 1


def test_the_alerts_route_lists_what_the_views_raised(client: TestClient):
    room = make_room(client)
    make_asset(client, room["id"])
    link = make_link(client, room["id"])
    client.post(
        f"{PREFIX}/links/{link['id']}/views",
        json=readings("a.buyer@northwind.example", 3, started=datetime.now(timezone.utc)),
        params={"actor": "system"},
    )
    body = client.get(f"{PREFIX}/alerts", params={"room_id": room["id"]}).json()
    assert body["count"] >= 1
    assert body["alerts"][0]["trigger"]


def test_the_summary_counts_the_scope(client: TestClient):
    room = make_room(client)
    make_asset(client, room["id"])
    link = make_link(client, room["id"])
    client.post(
        f"{PREFIX}/links/{link['id']}/views",
        json=readings("a.buyer@northwind.example", 2, started=datetime.now(timezone.utc)),
        params={"actor": "system"},
    )
    body = client.get(f"{PREFIX}/summary", params={"room_id": room["id"]}).json()
    assert body["links"] == 1 and body["open"] == 1
    assert body["views"] == 2
    assert body["alerts"] >= 1


def test_the_push_route_drains_the_queue_without_a_5xx(client: TestClient):
    """No connector is registered, so every row is blocked by name, not sent."""
    room = make_room(client)
    make_asset(client, room["id"])
    link = make_link(client, room["id"])
    client.post(
        f"{PREFIX}/links/{link['id']}/views",
        json=readings("a.buyer@northwind.example", 2, started=datetime.now(timezone.utc)),
        params={"actor": "system"},
    )
    response = client.post(f"{PREFIX}/rooms/{room['id']}/push", params={"actor": "dana"})
    assert response.status_code == 200, response.text
    counts = response.json()["counts"]
    assert counts["considered"] >= 1
    assert counts["blocked"] >= 1


# --------------------------------------------------------------------------- #
# The audit trail, and the rule that every source names a served route
# --------------------------------------------------------------------------- #


def test_every_write_this_feature_made_names_a_route_the_app_serves(client: TestClient):
    """The recurring defect the brief names: an audit row pointing at a dead path."""
    room = make_room(client)
    make_asset(client, room["id"])
    link = make_link(client, room["id"])
    client.post(
        f"{PREFIX}/links/{link['id']}/deliveries",
        json={"recipients": ["a@northwind.example"]},
        params={"actor": "dana"},
    )
    client.post(
        f"{PREFIX}/links/{link['id']}/views",
        json=readings("a.buyer@northwind.example", 2, started=datetime.now(timezone.utc)),
        params={"actor": "system"},
    )

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
    make_asset(client, room["id"])
    link = make_link(client, room["id"])
    get_routes = [
        route
        for route in mounted_routes(client)
        if route[0] == "GET" and route[1].startswith(PREFIX)
    ]
    for _method, path in get_routes:
        concrete = path.replace("{link_id}", link["id"]).replace("{room_id}", room["id"])
        concrete = concrete.replace("{viewer}", "a.buyer@northwind.example")
        response = client.get(concrete)
        assert response.status_code < 500, f"{concrete} -> {response.status_code}"
