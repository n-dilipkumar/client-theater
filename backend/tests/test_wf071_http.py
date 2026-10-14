"""HTTP tests for WF-071: the mounted routes, the config lifecycle, the stamp.

These drive the mounted app through ``TestClient`` rather than calling the book,
so they exercise the same path a real client does. If the feature failed to
register, or collided with another feature's route, every test here would 404 -
which is the point: the router is mounted by discovery alone and
``backend/dsr/api.py`` is not edited.

Each test names the rule it protects. Two are worth reading twice: the audit test
asserts that every row this feature wrote carries a ``source`` that names a route
the host actually serves, and the plan test asserts the researched 403 on the
create path and *not* on a view.

No test depends on a row another test wrote, so the file passes alone and under
``pytest-xdist`` in any order. No test asserts a value measured against the wall
clock; the view timestamp is passed explicitly where one matters.
"""

from __future__ import annotations

import re

from fastapi.testclient import TestClient

PREFIX = "/api/wf-071"
FEATURE_ID = "wf-071-watermark"

#: This workflow's own collections, whose audit rows are the ones to check.
OWN_COLLECTIONS = {"wf071_watermark", "wf071_watermark_stamp"}

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
    client: TestClient, room_id: str, external_id: str = "deck-demo", pages: int = 8
) -> dict:
    """Register a PDF through WF-018's own route, which is the asset the link shares."""
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
    """A WF-121 tracked link, which is what a watermark is set on."""
    payload = {
        "asset_id": "deck-demo",
        "title": "Security overview",
        "recipients": ["a.buyer@northwind.example"],
        "share_url": "/api/wf-121/links/demo",
    }
    payload.update(overrides)
    response = client.post(
        "/api/wf-121/links", json=payload, params={"room_id": room_id, "actor": "dana"}
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
    """Is an audit ``source`` a route the app serves? ``POST /api/wf-071/preview`` -> yes."""
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
    assert "watermarked" in response.json()


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
    assert mine[0]["ticket"] == "WF-071"
    assert mine[0]["exception_handlers"] == ["ViewerWatermarkError"]
    prefixes = [f["prefix"] for f in body["features"] if f["prefix"]]
    assert prefixes.count(PREFIX) == 1


def test_the_feature_is_navigable_without_editing_app_jsx(client: TestClient):
    entry = next(f for f in client.get("/api/features").json()["features"] if f["prefix"] == PREFIX)
    assert entry["nav"] == [{"id": "viewer-watermark", "label": "Viewer watermark"}]


def test_the_vocabulary_serves_the_reused_values(client: TestClient):
    """The shared half is published, so a reviewer sees WF-121's and WF-077's values."""
    body = client.get(f"{PREFIX}/vocabulary").json()
    assert body["reused"]["link_collection"] == "wf121_tracked_link"
    assert "WF-121" in body["reused"]["link_collection_source"]
    assert body["plan"]["requires"] == "business"
    assert "forbidden_plan_feature" in body["plan"]["rule"]
    assert body["positions"][4] == "middle-center"
    assert body["rotations"] == [0, 30, 45, 90, 180]
    assert body["tokens"] == ["email", "date", "time", "link", "ipAddress"]
    assert body["required_fields"] == [
        "text",
        "is_tiled",
        "position",
        "rotation",
        "color",
        "font_size",
        "opacity",
    ]


def test_the_inferences_route_states_the_open_decisions(client: TestClient):
    body = client.get(f"{PREFIX}/inferences").json()
    ids = {entry["id"] for entry in body["inferences"]}
    assert {
        "storage_location",
        "email_token_without_a_gate",
        "ip_token_without_an_ip",
        "plan_tier",
        "tiled_default",
        "not_an_access_control",
    } <= ids
    for entry in body["inferences"]:
        assert entry["decision"] and entry["why"] and entry["change_it_by"]


# --------------------------------------------------------------------------- #
# The config lifecycle
# --------------------------------------------------------------------------- #


def test_read_always_reports_watermark_config_or_null(client: TestClient):
    room = make_room(client)
    make_asset(client, room["id"])
    link = make_link(client, room["id"])
    body = client.get(f"{PREFIX}/links/{link['id']}/watermark").json()
    assert "watermark_config" in body
    assert body["watermark_config"] is None
    assert body["enable_watermark"] is False
    assert body["link"]["id"] == link["id"]


def test_enable_then_read_returns_the_saved_config(client: TestClient):
    room = make_room(client)
    make_asset(client, room["id"])
    link = make_link(client, room["id"])
    enabled = client.post(
        f"{PREFIX}/links/{link['id']}/watermark",
        json={"text": "Confidential {{email}}", "is_tiled": True, "rotation": 90},
        params={"actor": "dana"},
    )
    assert enabled.status_code == 200, enabled.text
    body = enabled.json()
    assert body["enable_watermark"] is True
    assert body["watermark_config"]["text"] == "Confidential {{email}}"
    assert body["watermark_config"]["is_tiled"] is True
    assert body["watermark_config"]["rotation"] == 90

    read = client.get(f"{PREFIX}/links/{link['id']}/watermark").json()
    assert read["watermark_config"]["text"] == "Confidential {{email}}"


def test_enable_with_an_empty_body_fills_the_documented_defaults(client: TestClient):
    room = make_room(client)
    make_asset(client, room["id"])
    link = make_link(client, room["id"])
    body = client.post(f"{PREFIX}/links/{link['id']}/watermark", json={}).json()
    assert body["watermark_config"] == {
        "text": "Confidential",
        "is_tiled": False,
        "position": "middle-center",
        "rotation": 45,
        "color": "#000000",
        "font_size": 24,
        "opacity": 0.5,
    }


def test_patch_replaces_a_field_and_null_clears_the_config(client: TestClient):
    room = make_room(client)
    make_asset(client, room["id"])
    link = make_link(client, room["id"])
    client.post(
        f"{PREFIX}/links/{link['id']}/watermark",
        json={"text": "Confidential {{email}}"},
    )
    patched = client.patch(f"{PREFIX}/links/{link['id']}/watermark", json={"font_size": 40})
    assert patched.status_code == 200, patched.text
    assert patched.json()["watermark_config"]["font_size"] == 40
    assert patched.json()["watermark_config"]["text"] == "Confidential {{email}}"

    cleared = client.patch(
        f"{PREFIX}/links/{link['id']}/watermark", json={"watermark_config": None}
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["watermark_config"] is None
    assert cleared.json()["enable_watermark"] is True
    assert cleared.json()["cleared"] is True


def test_delete_disables_the_toggle_and_keeps_the_config(client: TestClient):
    room = make_room(client)
    make_asset(client, room["id"])
    link = make_link(client, room["id"])
    client.post(f"{PREFIX}/links/{link['id']}/watermark", json={"text": "Confidential {{email}}"})
    off = client.delete(f"{PREFIX}/links/{link['id']}/watermark")
    assert off.status_code == 200, off.text
    assert off.json()["enable_watermark"] is False
    assert off.json()["watermark_config"]["text"] == "Confidential {{email}}"


def test_the_list_route_joins_the_settings_onto_every_link(client: TestClient):
    room = make_room(client)
    make_asset(client, room["id"])
    link = make_link(client, room["id"])
    client.post(f"{PREFIX}/links/{link['id']}/watermark", json={"text": "Confidential"})
    listed = client.get(f"{PREFIX}/links", params={"room_id": room["id"]}).json()
    assert listed["count"] == 1
    assert listed["links"][0]["enable_watermark"] is True


# --------------------------------------------------------------------------- #
# The refusals
# --------------------------------------------------------------------------- #


def test_an_unknown_link_is_not_found(client: TestClient):
    assert client.get(f"{PREFIX}/links/ln_absent/watermark").status_code == 404
    assert client.post(f"{PREFIX}/links/ln_absent/watermark", json={}).status_code == 404


def test_a_payload_that_is_not_a_config_is_refused(client: TestClient):
    room = make_room(client)
    make_asset(client, room["id"])
    link = make_link(client, room["id"])
    response = client.post(
        f"{PREFIX}/links/{link['id']}/watermark",
        json={"rotation": 60, "font_size": 500},
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"] == "invalid_watermark_config"


def test_an_unknown_token_is_refused_with_its_name(client: TestClient):
    room = make_room(client)
    make_asset(client, room["id"])
    link = make_link(client, room["id"])
    response = client.post(
        f"{PREFIX}/links/{link['id']}/watermark", json={"text": "Confidential {{emial}}"}
    )
    assert response.status_code == 422, response.text
    assert response.json()["unknown_tokens"] == ["emial"]


def test_enabling_under_a_lower_plan_is_refused_403(client: TestClient):
    """The researched gate, quoted: 403 forbidden_plan_feature on create/update."""
    room = make_room(client)
    make_asset(client, room["id"])
    link = make_link(client, room["id"])
    response = client.post(
        f"{PREFIX}/links/{link['id']}/watermark",
        json={"text": "Confidential"},
        params={"plan": "starter"},
    )
    assert response.status_code == 403, response.text
    body = response.json()
    assert body["error"] == "forbidden_plan_feature"
    assert body["feature"] == "watermark"
    assert body["requires"] == "business"


def test_the_gate_does_not_fire_when_the_toggle_is_already_on(client: TestClient):
    """Researched: an existing link keeps working after a downgrade."""
    room = make_room(client)
    make_asset(client, room["id"])
    link = make_link(client, room["id"])
    client.post(
        f"{PREFIX}/links/{link['id']}/watermark",
        json={"text": "Confidential"},
        params={"plan": "business"},
    )
    again = client.post(
        f"{PREFIX}/links/{link['id']}/watermark",
        json={"text": "Confidential v2"},
        params={"plan": "starter"},
    )
    assert again.status_code == 200, again.text
    assert again.json()["watermark_config"]["text"] == "Confidential v2"


def test_a_patch_before_any_config_meets_the_plan_gate(client: TestClient):
    room = make_room(client)
    make_asset(client, room["id"])
    link = make_link(client, room["id"])
    response = client.patch(
        f"{PREFIX}/links/{link['id']}/watermark",
        json={"text": "Confidential"},
        params={"plan": "starter"},
    )
    assert response.status_code == 403, response.text


# --------------------------------------------------------------------------- #
# The per-view stamp
# --------------------------------------------------------------------------- #


def test_a_stamp_composites_the_text_onto_every_page(client: TestClient):
    room = make_room(client)
    make_asset(client, room["id"], pages=6)
    link = make_link(client, room["id"])
    client.post(
        f"{PREFIX}/links/{link['id']}/watermark",
        json={"text": "Confidential \u2014 {{email}} \u2014 {{date}}", "is_tiled": True},
    )
    stamped = client.post(
        f"{PREFIX}/links/{link['id']}/stamp",
        json={
            "viewer": "Buyer@Northwind.example",
            "ip_address": "203.0.113.9",
            "occurred_at": "2026-05-01T12:00:00+00:00",
        },
        params={"actor": "gate"},
    )
    assert stamped.status_code == 201, stamped.text
    body = stamped.json()
    assert body["stamped"] is True
    assert body["rendered_text"] == "Confidential \u2014 buyer@northwind.example \u2014 2026-05-01"
    assert body["page_count"] == 6
    assert [page["page"] for page in body["pages"]] == [1, 2, 3, 4, 5, 6]
    assert body["unresolved_tokens"] == []
    assert body["stamp_id"]


def test_a_link_with_no_watermark_stamps_nothing(client: TestClient):
    room = make_room(client)
    make_asset(client, room["id"])
    link = make_link(client, room["id"])
    response = client.post(
        f"{PREFIX}/links/{link['id']}/stamp", json={"viewer": "a.buyer@northwind.example"}
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["enable_watermark"] is False
    assert body["stamped"] is False
    assert body["pages"] == []


def test_a_stamp_without_a_viewer_is_refused_by_wf121s_own_rule(client: TestClient):
    room = make_room(client)
    make_asset(client, room["id"])
    link = make_link(client, room["id"])
    client.post(f"{PREFIX}/links/{link['id']}/watermark", json={"text": "{{email}}"})
    response = client.post(f"{PREFIX}/links/{link['id']}/stamp", json={"ip_address": "203.0.113.9"})
    assert response.status_code == 428, response.text
    assert response.json()["error"] == "viewer_required"


def test_an_anonymous_link_names_the_tokens_it_could_not_fill(client: TestClient):
    room = make_room(client)
    make_asset(client, room["id"])
    link = make_link(client, room["id"], require_viewer=False)
    client.post(
        f"{PREFIX}/links/{link['id']}/watermark",
        json={"text": "{{email}} {{ipAddress}}"},
    )
    body = client.post(f"{PREFIX}/links/{link['id']}/stamp", json={}).json()
    assert body["rendered_text"] == "anonymous unknown"
    assert body["unresolved_tokens"] == ["email", "ipAddress"]


def test_a_stamp_on_a_closed_link_is_refused(client: TestClient):
    room = make_room(client)
    make_asset(client, room["id"])
    link = make_link(client, room["id"], expires_at="2026-01-01")
    client.post(f"{PREFIX}/links/{link['id']}/watermark", json={"text": "Confidential"})
    response = client.post(
        f"{PREFIX}/links/{link['id']}/stamp", json={"viewer": "a.buyer@northwind.example"}
    )
    assert response.status_code == 410, response.text
    assert response.json()["error"] == "tracked_link_closed"


def test_the_stamps_route_reads_back_what_was_stamped(client: TestClient):
    room = make_room(client)
    make_asset(client, room["id"])
    link = make_link(client, room["id"])
    client.post(f"{PREFIX}/links/{link['id']}/watermark", json={"text": "Confidential {{email}}"})
    client.post(
        f"{PREFIX}/links/{link['id']}/stamp",
        json={"viewer": "a.buyer@northwind.example", "ip_address": "203.0.113.9"},
    )
    listed = client.get(f"{PREFIX}/links/{link['id']}/stamps").json()
    assert listed["count"] == 1
    assert listed["stamps"][0]["viewer"] == "a.buyer@northwind.example"
    assert listed["stamps"][0]["rendered_text"] == "Confidential a.buyer@northwind.example"


def test_preview_renders_a_config_with_no_write(client: TestClient):
    response = client.post(
        f"{PREFIX}/preview",
        json={"text": "Confidential \u2014 {{email}}", "viewer": "Buyer@Northwind.example"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["rendered_text"] == "Confidential \u2014 buyer@northwind.example"
    assert client.get(f"{PREFIX}/summary").json()["stamps"] == 0


def test_the_summary_counts_the_scope(client: TestClient):
    room = make_room(client)
    make_asset(client, room["id"])
    link = make_link(client, room["id"])
    client.post(
        f"{PREFIX}/links/{link['id']}/watermark", json={"text": "{{email}}", "is_tiled": True}
    )
    client.post(f"{PREFIX}/links/{link['id']}/stamp", json={"viewer": "a.buyer@northwind.example"})
    body = client.get(f"{PREFIX}/summary", params={"room_id": room["id"]}).json()
    assert body["links"] == 1
    assert body["watermarked"] == 1
    assert body["tiled"] == 1
    assert body["stamps"] == 1
    assert body["viewers_stamped"] == 1


# --------------------------------------------------------------------------- #
# The audit trail, and the rule that every source names a served route
# --------------------------------------------------------------------------- #


def test_every_write_this_feature_made_names_a_route_the_app_serves(client: TestClient):
    """The recurring defect the brief names: an audit row pointing at a dead path."""
    room = make_room(client)
    make_asset(client, room["id"])
    link = make_link(client, room["id"])
    client.post(f"{PREFIX}/links/{link['id']}/watermark", json={"text": "Confidential {{email}}"})
    client.patch(f"{PREFIX}/links/{link['id']}/watermark", json={"font_size": 30})
    client.post(
        f"{PREFIX}/links/{link['id']}/stamp",
        json={"viewer": "a.buyer@northwind.example", "ip_address": "203.0.113.9"},
    )

    entries = client.get("/api/audit", params={"limit": 1000}).json()["entries"]
    routes = mounted_routes(client)
    mine = [entry for entry in entries if PREFIX in str(entry.get("source") or "")]
    assert mine, "the feature wrote no audited rows, so this proves nothing"
    for entry in mine:
        assert source_matches_route(str(entry["source"]), routes), entry["source"]
    # And it really did write into its own collections.
    assert OWN_COLLECTIONS & {entry["collection"] for entry in mine}


def test_no_get_route_this_feature_advertises_returns_a_5xx(client: TestClient):
    """A light version of tools/verify_all_routes.py, scoped to this feature."""
    room = make_room(client)
    make_asset(client, room["id"])
    link = make_link(client, room["id"])
    get_routes = [
        route
        for route in mounted_routes(client)
        if route[0] == "GET" and route[1].startswith(PREFIX)
    ]
    assert get_routes, "the feature advertises no GET route, so this proves nothing"
    for _method, path in get_routes:
        concrete = path.replace("{link_id}", link["id"]).replace("{room_id}", room["id"])
        response = client.get(concrete)
        assert response.status_code < 500, f"{concrete} -> {response.status_code}"
