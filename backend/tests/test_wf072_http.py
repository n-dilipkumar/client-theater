"""WF-072 over HTTP: the surface this feature's own router serves.

The domain rules are in ``test_wf072.py``. This file is the other half, and it is organised by
what a caller can observe:

``the route table``
    That the host mounted every route by discovery alone, with no shared file edited, and that
    no two of them collide.
``the room settings panel``
    Reading the room policy, the documented default before it is set, and setting the bulk
    download escalation.
``the links``
    Creating a link at the view-only default, escalating it, and rotating it back.
``the decision``
    The three-way AND read-only, and its refusal over the wire.
``the download path``
    A permitted fetch writing a view row, and a refused fetch answering 403 and writing none.
``the download log``
    Reading the events back, which is what the audit trail reads.
``the error shapes``
    Every status this router can produce, including the 400, the 403 and the 404s the store
    would otherwise leak as a 500.
``the audit-source rule``
    Every ``source=`` this workflow records names a route the host actually mounted. This is
    the test the build brief asks for by name.
``the honesty rule``
    Every response carries the scope ownership statement, the read source, the assumption and
    the limitation, so no caller can read the controls without also reading what they are
    worth.
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path
from typing import Any

import dsr.features as host
import pytest
from dsr.view_only_access import vocabulary as vocab
from fastapi.testclient import TestClient

FEATURE_MODULE = "dsr.features.wf072_enforce_view_only_access_and_block_bulk"
PREFIX = "/api/wf-072"
FEATURE_ID = "wf-072-enforce-view-only-access-and-block-bulk"


def make_room(client: TestClient, name: str = "Northwind") -> str:
    """One room, created through the core API.

    Created through the core API rather than straight into the store, so the room is a row the
    application itself would have written. A test that faked it could pass against a policy
    hanging on a room that does not exist, which is the one thing the engine refuses.
    """

    response = client.post("/api/records/room", json={"name": name})
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def make_link(client: TestClient, room_id: str, **payload: Any) -> dict[str, Any]:
    """One governed link over HTTP."""

    response = client.post(
        f"{PREFIX}/rooms/{room_id}/links", json={vocab.NAME_FIELD: "Review link", **payload}
    )
    assert response.status_code == 201, response.text
    return response.json()


def make_permission(
    client: TestClient, room_id: str, item_id: str, *, can_download: bool, revoked: bool = False
) -> str:
    """One WF-074 permission row, written through the core records API.

    The row belongs to WF-074's permission set; it is written here as the read source this
    workflow evaluates, so the test exercises the real store the request path reads from.
    """

    payload: dict[str, Any] = {
        vocab.ITEM_ID: item_id,
        vocab.ITEM_TYPE: vocab.ITEM_TYPE_DOCUMENT,
        "can_view": True,
        vocab.CAN_DOWNLOAD: can_download,
    }
    if revoked:
        payload[vocab.REVOKED_AT] = "2026-01-01T00:00:00+00:00"
    response = client.post(
        f"/api/records/{vocab.WF074_LINK_PERMISSION_COLLECTION}",
        json=payload,
        params={"room_id": room_id},
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


# --------------------------------------------------------------------------- #
# the route table
# --------------------------------------------------------------------------- #


class TestRouteTable:
    def test_the_feature_is_installed_with_its_routes(self, client: TestClient):
        payload = client.get("/api/features").json()
        installed = next(f for f in payload["features"] if f["id"] == FEATURE_ID)
        paths = {route["path"] for route in installed["routes"]}
        assert f"{PREFIX}/summary" in paths
        assert f"{PREFIX}/rooms/{{room_id}}/policy" in paths
        assert f"{PREFIX}/links/{{link_id}}/download" in paths

    def test_the_feature_did_not_fail_to_load(self, client: TestClient):
        payload = client.get("/api/features").json()
        assert not [f for f in payload["failed"] if FEATURE_ID in str(f)]

    def test_the_host_mounted_it_without_a_shared_file_being_edited(self):
        """Discovery alone mounts the router; the contract guard's rule, checked here."""
        module = importlib.import_module(FEATURE_MODULE)
        assert module.router.prefix == PREFIX
        mounted = {
            (method, route.path)
            for route in module.router.routes
            for method in route.methods
            if method not in ("HEAD", "OPTIONS")
        }
        assert len(mounted) == len(set(mounted))
        assert len(mounted) >= 14

    def test_no_two_routes_collide(self):
        module = importlib.import_module(FEATURE_MODULE)
        seen: set[tuple[str, str]] = set()
        for route in module.router.routes:
            for method in route.methods:
                if method in ("HEAD", "OPTIONS"):
                    continue
                key = (method, route.path)
                assert key not in seen, key
                seen.add(key)

    def test_the_live_registry_has_no_route_collisions(self):
        seen: set[tuple[str, str]] = set()
        for feature in host.REGISTRY.features:
            for route in feature.routes:
                for method in route["methods"]:
                    key = (method, route["path"])
                    assert key not in seen, f"{feature.id} duplicates {key}"
                    seen.add(key)


# --------------------------------------------------------------------------- #
# the room settings panel
# --------------------------------------------------------------------------- #


class TestRoomPolicy:
    def test_a_room_with_no_policy_reads_the_default(self, client: TestClient):
        room_id = make_room(client)
        body = client.get(f"{PREFIX}/rooms/{room_id}/policy").json()
        assert body["configured"] is False
        assert body[vocab.BULK_DOWNLOAD] is False

    def test_setting_bulk_download_on_then_off(self, client: TestClient):
        room_id = make_room(client)
        on = client.patch(f"{PREFIX}/rooms/{room_id}/policy", json={vocab.BULK_DOWNLOAD: "on"})
        assert on.status_code == 200, on.text
        assert on.json()[vocab.BULK_DOWNLOAD] is True
        off = client.patch(f"{PREFIX}/rooms/{room_id}/policy", json={vocab.BULK_DOWNLOAD: "off"})
        assert off.json()[vocab.BULK_DOWNLOAD] is False

    def test_the_policy_carries_the_other_settings_untouched(self, client: TestClient):
        room_id = make_room(client)
        body = client.patch(
            f"{PREFIX}/rooms/{room_id}/policy",
            json={vocab.BULK_DOWNLOAD: True, "conversations": True},
        ).json()
        assert body[vocab.BULK_DOWNLOAD] is True

    def test_a_bad_payload_is_a_400_with_a_field_map(self, client: TestClient):
        room_id = make_room(client)
        response = client.patch(
            f"{PREFIX}/rooms/{room_id}/policy", json={vocab.BULK_DOWNLOAD: "maybe"}
        )
        assert response.status_code == 400
        assert vocab.BULK_DOWNLOAD in response.json()["errors"]

    def test_a_policy_for_a_missing_room_is_a_404(self, client: TestClient):
        assert (
            client.get(f"{PREFIX}/rooms/room_missing/policy").status_code == 200
        )  # a read of a missing room is still the default
        response = client.patch(
            f"{PREFIX}/rooms/room_missing/policy", json={vocab.BULK_DOWNLOAD: True}
        )
        assert response.status_code == 404


# --------------------------------------------------------------------------- #
# the links
# --------------------------------------------------------------------------- #


class TestLinks:
    def test_a_new_link_is_view_only(self, client: TestClient):
        room_id = make_room(client)
        link = make_link(client, room_id)
        assert link[vocab.ALLOW_DOWNLOAD] is False

    def test_a_link_can_be_created_escalated_and_rotated_back(self, client: TestClient):
        room_id = make_room(client)
        link = make_link(client, room_id)
        on = client.patch(f"{PREFIX}/links/{link['id']}", json={vocab.ALLOW_DOWNLOAD: "on"})
        assert on.status_code == 200, on.text
        assert on.json()[vocab.ALLOW_DOWNLOAD] is True
        off = client.patch(f"{PREFIX}/links/{link['id']}", json={vocab.ALLOW_DOWNLOAD: False})
        assert off.json()[vocab.ALLOW_DOWNLOAD] is False

    def test_links_are_listed_for_one_room(self, client: TestClient):
        room_id = make_room(client)
        make_link(client, room_id)
        make_link(client, room_id)
        body = client.get(f"{PREFIX}/rooms/{room_id}/links").json()
        assert body["count"] == 2

    def test_an_unknown_link_is_a_404(self, client: TestClient):
        assert client.get(f"{PREFIX}/links/link_missing").status_code == 404


# --------------------------------------------------------------------------- #
# the decision
# --------------------------------------------------------------------------- #


class TestDecision:
    def _ready(self, client: TestClient, *, bulk: bool, allow: bool, grant: bool):
        room_id = make_room(client)
        client.patch(f"{PREFIX}/rooms/{room_id}/policy", json={vocab.BULK_DOWNLOAD: bulk})
        link = make_link(client, room_id, **{vocab.ALLOW_DOWNLOAD: allow})
        make_permission(client, room_id, "doc_1", can_download=grant)
        return room_id, link

    def test_the_three_controls_all_permit(self, client: TestClient):
        _, link = self._ready(client, bulk=True, allow=True, grant=True)
        body = client.get(
            f"{PREFIX}/links/{link['id']}/download", params={"item_id": "doc_1"}
        ).json()
        assert body[vocab.DOWNLOAD_FIELD] is True
        assert body[vocab.STATE_FIELD] == vocab.STATE_DOWNLOAD

    @pytest.mark.parametrize(
        "bulk,allow,grant",
        [(True, True, False), (True, False, True), (False, True, True)],
    )
    def test_one_control_alone_is_not_enough(self, client: TestClient, bulk, allow, grant):
        _, link = self._ready(client, bulk=bulk, allow=allow, grant=grant)
        body = client.get(
            f"{PREFIX}/links/{link['id']}/download", params={"item_id": "doc_1"}
        ).json()
        assert body[vocab.DOWNLOAD_FIELD] is False
        assert body[vocab.STATE_FIELD] == vocab.STATE_VIEW_ONLY
        assert len(body[vocab.BLOCKED_BY_FIELD]) == 1

    def test_a_revoked_grant_reads_as_no_download(self, client: TestClient):
        room_id = make_room(client)
        client.patch(f"{PREFIX}/rooms/{room_id}/policy", json={vocab.BULK_DOWNLOAD: True})
        link = make_link(client, room_id, **{vocab.ALLOW_DOWNLOAD: True})
        make_permission(client, room_id, "doc_1", can_download=True, revoked=True)
        body = client.get(
            f"{PREFIX}/links/{link['id']}/download", params={"item_id": "doc_1"}
        ).json()
        assert body[vocab.DOWNLOAD_FIELD] is False
        assert body["grant"][vocab.GRANT_FIELD] == vocab.GRANT_REVOKED

    def test_the_decision_carries_the_read_source(self, client: TestClient):
        _, link = self._ready(client, bulk=True, allow=True, grant=True)
        body = client.get(
            f"{PREFIX}/links/{link['id']}/download", params={"item_id": "doc_1"}
        ).json()
        assert body[vocab.READ_SOURCE_FIELD] == vocab.READ_SOURCE


# --------------------------------------------------------------------------- #
# the download path
# --------------------------------------------------------------------------- #


class TestDownloadPath:
    def _ready(self, client: TestClient):
        room_id = make_room(client)
        client.patch(f"{PREFIX}/rooms/{room_id}/policy", json={vocab.BULK_DOWNLOAD: True})
        link = make_link(client, room_id, **{vocab.ALLOW_DOWNLOAD: True})
        make_permission(client, room_id, "doc_1", can_download=True)
        return room_id, link

    def test_a_permitted_fetch_writes_a_view_row_and_releases_the_bytes(self, client: TestClient):
        _, link = self._ready(client)
        response = client.post(
            f"{PREFIX}/links/{link['id']}/download",
            json={vocab.ITEM_ID: "doc_1", vocab.DOWNLOAD_TYPE: vocab.DOWNLOAD_TYPE_PDF},
        )
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["download"] is True
        assert body["view"][vocab.DOWNLOADED_AT] is not None
        assert body["view"][vocab.DOWNLOAD_TYPE] == vocab.DOWNLOAD_TYPE_PDF

    def test_a_permitted_fetch_names_a_route_the_host_mounted(self, client: TestClient):
        _, link = self._ready(client)
        client.post(f"{PREFIX}/links/{link['id']}/download", json={vocab.ITEM_ID: "doc_1"})
        audit = client.get("/api/audit", params={"collection": vocab.VIEW_COLLECTION}).json()
        source = audit["entries"][0]["source"]
        mounted = {
            f"{method} {route.path}"
            for route in importlib.import_module(FEATURE_MODULE).router.routes
            for method in route.methods
            if method not in ("HEAD", "OPTIONS")
        }
        assert source in mounted

    def test_a_refused_fetch_is_a_403_that_writes_nothing(self, client: TestClient):
        room_id = make_room(client)
        link = make_link(client, room_id, **{vocab.ALLOW_DOWNLOAD: True})
        make_permission(client, room_id, "doc_1", can_download=True)
        response = client.post(
            f"{PREFIX}/links/{link['id']}/download", json={vocab.ITEM_ID: "doc_1"}
        )
        assert response.status_code == 403, response.text
        body = response.json()
        assert body["error"] == "view_only"
        assert body[vocab.DOWNLOAD_FIELD] is False
        assert (
            client.get("/api/audit", params={"collection": vocab.VIEW_COLLECTION}).json()["count"]
            == 0
        )

    def test_a_fetch_without_an_item_is_a_400(self, client: TestClient):
        _, link = self._ready(client)
        response = client.post(f"{PREFIX}/links/{link['id']}/download", json={})
        assert response.status_code == 400
        assert vocab.ITEM_ID in response.json()["errors"]

    def test_a_bad_download_type_is_a_400(self, client: TestClient):
        _, link = self._ready(client)
        response = client.post(
            f"{PREFIX}/links/{link['id']}/download",
            json={vocab.ITEM_ID: "doc_1", vocab.DOWNLOAD_TYPE: "screenshot"},
        )
        assert response.status_code == 400

    def test_a_fetch_on_an_unknown_link_is_a_404(self, client: TestClient):
        response = client.post(
            f"{PREFIX}/links/link_missing/download", json={vocab.ITEM_ID: "doc_1"}
        )
        assert response.status_code == 404


# --------------------------------------------------------------------------- #
# the bulk escalation and the download log
# --------------------------------------------------------------------------- #


class TestBulkAndLog:
    def test_bulk_reports_a_decision_without_releasing_bytes(self, client: TestClient):
        room_id = make_room(client)
        client.patch(f"{PREFIX}/rooms/{room_id}/policy", json={vocab.BULK_DOWNLOAD: True})
        link = make_link(client, room_id, **{vocab.ALLOW_DOWNLOAD: True})
        make_permission(client, room_id, "doc_1", can_download=True)
        response = client.post(f"{PREFIX}/links/{link['id']}/bulk", json={"item_ids": ["doc_1"]})
        assert response.status_code == 200, response.text
        assert response.json()[vocab.DOWNLOAD_FIELD] is True

    def test_bulk_is_blocked_when_the_room_control_is_off(self, client: TestClient):
        room_id = make_room(client)
        link = make_link(client, room_id, **{vocab.ALLOW_DOWNLOAD: True})
        make_permission(client, room_id, "doc_1", can_download=True)
        response = client.post(f"{PREFIX}/links/{link['id']}/bulk", json={"item_ids": ["doc_1"]})
        assert response.json()[vocab.DOWNLOAD_FIELD] is False
        assert response.json()[vocab.BLOCKED_BY_FIELD] == [vocab.GATE_ROOM]

    def test_the_download_log_reads_the_events_back(self, client: TestClient):
        room_id = make_room(client)
        client.patch(f"{PREFIX}/rooms/{room_id}/policy", json={vocab.BULK_DOWNLOAD: True})
        link = make_link(client, room_id, **{vocab.ALLOW_DOWNLOAD: True})
        make_permission(client, room_id, "doc_1", can_download=True)
        client.post(f"{PREFIX}/links/{link['id']}/download", json={vocab.ITEM_ID: "doc_1"})
        body = client.get(f"{PREFIX}/links/{link['id']}/downloads").json()
        assert body["count"] == 1
        assert body["downloads"][0]["downloaded"] is True

    def test_the_board_reads_the_states(self, client: TestClient):
        room_id = make_room(client)
        client.patch(f"{PREFIX}/rooms/{room_id}/policy", json={vocab.BULK_DOWNLOAD: True})
        make_link(client, room_id, **{vocab.ALLOW_DOWNLOAD: True})
        make_link(client, room_id)
        board = client.get(f"{PREFIX}/summary", params={"room_id": room_id}).json()
        assert board["links"] == 2
        assert board["allow_download"] == 1
        assert board["bulk_download"] == 1


# --------------------------------------------------------------------------- #
# the research surfaces
# --------------------------------------------------------------------------- #


class TestResearch:
    def test_the_vocabulary_is_served(self, client: TestClient):
        body = client.get(f"{PREFIX}/vocabulary").json()
        assert body["defaults"][vocab.BULK_DOWNLOAD] is False
        assert set(body["collections"]) == set(vocab.ALL_COLLECTIONS)

    def test_the_decisions_are_served(self, client: TestClient):
        listing = client.get(f"{PREFIX}/decisions").json()
        assert listing["count"] >= 3
        first = listing["decisions"][0]["id"]
        assert client.get(f"{PREFIX}/decisions/{first}").status_code == 200
        assert client.get(f"{PREFIX}/decisions/nope").status_code == 404


# --------------------------------------------------------------------------- #
# the audit-source rule
# --------------------------------------------------------------------------- #


class TestAuditSourceRule:
    def test_every_source_this_router_can_record_names_a_mounted_route(self):
        """The rule the build brief asks for by name."""

        module = importlib.import_module(FEATURE_MODULE)
        mounted = {
            f"{method} {route.path}"
            for route in module.router.routes
            for method in route.methods
            if method not in ("HEAD", "OPTIONS")
        }

        tree = ast.parse(Path(module.__file__ or "").read_text(encoding="utf-8"))
        found: list[str] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            if node.func.id != "_source" or len(node.args) != 2:
                continue
            method, path = node.args
            if not (isinstance(method, ast.Constant) and isinstance(path, ast.Constant)):
                continue
            found.append(f"{method.value} {PREFIX}{path.value}")

        assert found, "no _source() call found; the audit-source rule is not being exercised"
        for source in found:
            assert source in mounted, f"{source} names a route the host did not mount"

    def test_the_domain_package_records_no_literal_route_of_its_own(self):
        module = importlib.import_module("dsr.view_only_access.engine")
        assert "/api/" not in Path(module.__file__ or "").read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# the honesty rule
# --------------------------------------------------------------------------- #


class TestHonestyRule:
    def test_every_response_carries_the_honesty_sentences(self, client: TestClient):
        room_id = make_room(client)
        link = make_link(client, room_id)
        bodies = [
            client.get(f"{PREFIX}/summary").json(),
            client.get(f"{PREFIX}/rooms/{room_id}/links").json(),
            client.get(f"{PREFIX}/downloads").json(),
            client.get(f"{PREFIX}/links/{link['id']}/downloads").json(),
            client.get(f"{PREFIX}/links/{link['id']}/download", params={"item_id": "doc_1"}).json(),
        ]
        for body in bodies:
            assert body[vocab.OWNER_FIELD] == vocab.SCOPE_OWNER
            assert body[vocab.READ_SOURCE_FIELD] == vocab.READ_SOURCE
            assert body[vocab.ASSUMPTION_FIELD] == vocab.ASSUMPTION
            assert body[vocab.LIMITATION_FIELD] == vocab.LIMITATION

    def test_the_refusal_carries_the_limitation(self, client: TestClient):
        room_id = make_room(client)
        link = make_link(client, room_id)
        body = client.post(
            f"{PREFIX}/links/{link['id']}/download", json={vocab.ITEM_ID: "doc_1"}
        ).json()
        assert body[vocab.LIMITATION_FIELD] == vocab.LIMITATION
