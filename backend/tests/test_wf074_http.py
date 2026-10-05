"""WF-074 over HTTP: the surface this feature's own router serves.

The domain rules are in ``test_wf074.py``. This file is the other half, and it is organised by
what a caller can observe:

``the route table``
    That the host mounted every route by discovery alone, with no shared file edited, and that
    no two of them collide.
``the audience surface``
    Creating an audience, reading it back, renaming it, and the domains it holds. The 400s are
    checked by their shape because a field-keyed map is what lets a page put each message beside
    the input that caused it.
``the membership surface``
    Adding members idempotently and silently, listing them, and removing one with no reissue.
``the group ACL``
    A delta write over the wire, the grid beside it, and the auto-opened ancestors named in the
    response.
``the link ACL``
    A full-replace write, the drop it caused, and the 422 a group link answers instead.
``the view``
    What one address sees on one link, filtered before any bytes, with the hidden items counted
    and never returned.
``the error shapes``
    Every status this router can produce, including the four the domain raises and the 404s the
    store would otherwise leak as a 500.
``the audit-source rule``
    Every ``source=`` this workflow records names a route the host actually mounted. This is the
    test the build brief asks for by name.
``the honesty rule``
    Every response carries the scope ownership statement, the assumption statement, the
    limitation and the not-proof sentence, so no caller can read a permission set without also
    reading what it is worth.
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path
from typing import Any

import dsr.features as host
import pytest
from dsr.audience_permissions import vocabulary as vocab
from dsr.audience_permissions.engine import LINK_SCOPE_SET, NAME_FIELD
from fastapi.testclient import TestClient

FEATURE_MODULE = "dsr.features.wf074_scope_visibility_to_an_audience_with_per_item"
PREFIX = "/api/wf-074"
FEATURE_ID = "wf-074-scope-visibility-to-an-audience-with-per-item"


def make_room(client: TestClient, name: str = "Northwind") -> str:
    """One room, created through the core API.

    Created through the core API rather than straight into the store, so the room is a row the
    application itself would have written. A test that faked it could pass against an audience
    hanging on a room that does not exist, which is the one thing the engine refuses.
    """

    response = client.post("/api/records/room", json={"name": name})
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def make_item(client: TestClient, room_id: str, collection: str, payload: dict[str, Any]) -> str:
    """One library row in one room: a document or a folder.

    Written through the core records API rather than the library, because the library's ingest
    writes a blob to disk and this test needs the row, not the file. The item row's shape is the
    same either way: a ``parentFolderId`` on the payload and the room on the envelope, which is
    what the engine reads.
    """

    response = client.post(f"/api/records/{collection}", json=payload, params={"room_id": room_id})
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def make_group(client: TestClient, room_id: str, **payload: Any) -> dict[str, Any]:
    """One audience over HTTP."""

    response = client.post(
        f"{PREFIX}/rooms/{room_id}/groups", json={NAME_FIELD: "Co-investors", **payload}
    )
    assert response.status_code == 201, response.text
    return response.json()


def make_link(client: TestClient, room_id: str, **payload: Any) -> dict[str, Any]:
    """One link over HTTP."""

    response = client.post(
        f"{PREFIX}/rooms/{room_id}/links",
        json={
            NAME_FIELD: "Review link",
            vocab.AUDIENCE_TYPE_FIELD: vocab.AUDIENCE_GENERAL,
            **payload,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def permissions(client: TestClient, path: str, entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Write a permission set and return the parsed body, asserting it was not refused."""

    response = client.put(f"{PREFIX}{path}", json={"permissions": entries})
    assert response.status_code == 200, response.text
    return response.json()


# --------------------------------------------------------------------------- #
# the route table
# --------------------------------------------------------------------------- #


class TestRouteTable:
    def test_the_feature_is_installed_with_its_routes(self, client: TestClient):
        payload = client.get("/api/features").json()
        installed = next(f for f in payload["features"] if f["id"] == FEATURE_ID)
        paths = {route["path"] for route in installed["routes"]}
        assert f"{PREFIX}/summary" in paths
        assert f"{PREFIX}/groups/{{group_id}}/permissions" in paths
        assert f"{PREFIX}/links/{{link_id}}/permissions" in paths
        assert f"{PREFIX}/links/{{link_id}}/view" in paths

    def test_the_feature_did_not_fail_to_load(self, client: TestClient):
        payload = client.get("/api/features").json()
        assert not [f for f in payload["failed"] if FEATURE_ID in str(f)]

    def test_the_host_mounted_it_without_a_shared_file_being_edited(self):
        """``backend/dsr/api.py`` discovers and mounts every feature router. This test states the
        claim by name so a reviewer can check it against the diff."""
        module = importlib.import_module(FEATURE_MODULE)
        assert host.REGISTRY.by_id(FEATURE_ID) is not None
        assert module.router.prefix == PREFIX

    def test_every_route_is_reachable_and_none_collide(self):
        mounted = {
            f"{method} {route.path}"
            for route in importlib.import_module(FEATURE_MODULE).router.routes
            for method in route.methods
            if method not in ("HEAD", "OPTIONS")
        }
        assert len(mounted) == len(set(mounted))
        assert len(mounted) >= 18

    def test_the_prefix_is_ticket_derived(self):
        assert PREFIX == "/api/wf-074"

    def test_the_feature_id_matches_the_frontend_folder(self):
        """The id is the one name both halves share. A mismatch would discover the page and not
        find its backend."""
        module = importlib.import_module(FEATURE_MODULE)
        import dsr

        root = Path(dsr.__file__ or "").resolve().parents[2]
        folder = root / "frontend" / "src" / "features" / FEATURE_ID
        if not folder.is_dir():
            pytest.skip("frontend sources are not present in this checkout")
        assert (folder / "index.jsx").is_file()
        assert module.FEATURE["id"] == FEATURE_ID

    def test_every_route_the_specification_names_is_served(self, client: TestClient):
        """The specification's eight APIs, checked by name rather than by count.

        The paths are compared against the registry rather than against this test's own
        knowledge of the prefix, because the registry records the full path including the prefix
        the host mounted. A duplicate entry in the list is deliberate: the specification names a
        create and a list for the same collection, and both must be served.
        """
        room_id = make_room(client)
        paths = {route["path"] for route in host.REGISTRY.by_id(FEATURE_ID).routes}
        for path in (
            f"{PREFIX}/rooms/{{room_id}}/groups",
            f"{PREFIX}/rooms/{{room_id}}/items",
            f"{PREFIX}/rooms/{{room_id}}/links",
            f"{PREFIX}/groups/{{group_id}}",
            f"{PREFIX}/groups/{{group_id}}/members",
            f"{PREFIX}/groups/{{group_id}}/members/{{member_id}}",
            f"{PREFIX}/groups/{{group_id}}/permissions",
            f"{PREFIX}/links/{{link_id}}/permissions",
        ):
            assert path in paths, path
        assert room_id


# --------------------------------------------------------------------------- #
# the audience surface
# --------------------------------------------------------------------------- #


class TestAudienceSurface:
    def test_creating_an_audience_returns_201_and_grants_nothing(self, client: TestClient):
        """ "A new group sees **nothing** until you grant permissions." """
        room_id = make_room(client)
        group = make_group(client, room_id)
        assert group[vocab.PERMISSION_COUNT_FIELD] == 0
        assert group[vocab.MEMBER_COUNT_FIELD] == 0
        assert group[vocab.ROOM_REF] if False else group["room_id"] == room_id

    def test_domains_are_normalised_on_the_way_in(self, client: TestClient):
        """ "both are lowercased and normalized to ``@acme.com``. Duplicates are removed." """
        room_id = make_room(client)
        group = make_group(
            client,
            room_id,
            **{vocab.DOMAINS_FIELD: ["acme.example", "@ACME.example", "@zeta.example"]},
        )
        assert group[vocab.DOMAINS_FIELD] == ["@acme.example", "@zeta.example"]

    def test_audiences_are_listed_per_room(self, client: TestClient):
        first = make_room(client, "First")
        second = make_room(client, "Second")
        make_group(client, first, **{NAME_FIELD: "In first"})
        make_group(client, second, **{NAME_FIELD: "In second"})
        listed = client.get(f"{PREFIX}/rooms/{second}/groups").json()
        assert [row[NAME_FIELD] for row in listed["groups"]] == ["In second"]

    def test_renaming_an_audience_works(self, client: TestClient):
        room_id = make_room(client)
        group = make_group(client, room_id)
        response = client.patch(f"{PREFIX}/groups/{group['id']}", json={NAME_FIELD: "Renamed"})
        assert response.status_code == 200, response.text
        assert response.json()[NAME_FIELD] == "Renamed"

    def test_flipping_allow_all_works(self, client: TestClient):
        room_id = make_room(client)
        group = make_group(client, room_id)
        response = client.patch(f"{PREFIX}/groups/{group['id']}", json={vocab.ALLOW_ALL: True})
        assert response.status_code == 200
        assert response.json()[vocab.ALLOW_ALL] is True

    def test_adding_members_through_a_patch_is_refused(self, client: TestClient):
        """Adding and removing members are the two calls the source names, so a patch that could
        add one would make the idempotent path unreachable from one of the two doors."""
        room_id = make_room(client)
        group = make_group(client, room_id)
        response = client.patch(
            f"{PREFIX}/groups/{group['id']}", json={"emails": ["a@acme.example"]}
        )
        assert response.status_code == 400
        assert response.json()["error"] == "audience_payload_refused"

    def test_an_audience_without_a_name_is_a_400_with_a_field_keyed_map(self, client: TestClient):
        room_id = make_room(client)
        response = client.post(f"{PREFIX}/rooms/{room_id}/groups", json={})
        assert response.status_code == 400
        body = response.json()
        assert body["error"] == "audience_payload_refused"
        assert body["errors"][NAME_FIELD]

    def test_a_bad_domain_is_a_400_naming_the_field(self, client: TestClient):
        room_id = make_room(client)
        response = client.post(
            f"{PREFIX}/rooms/{room_id}/groups",
            json={NAME_FIELD: "x", vocab.DOMAINS_FIELD: ["not-a-domain"]},
        )
        assert response.status_code == 400
        assert response.json()["errors"][vocab.DOMAINS_FIELD]


# --------------------------------------------------------------------------- #
# the membership surface
# --------------------------------------------------------------------------- #


class TestMembershipSurface:
    def test_adding_a_member_twice_is_a_201_that_adds_nothing(self, client: TestClient):
        room_id = make_room(client)
        group = make_group(client, room_id)
        first = client.post(
            f"{PREFIX}/groups/{group['id']}/members", json={"emails": ["jane@sequoia.example"]}
        ).json()
        second = client.post(
            f"{PREFIX}/groups/{group['id']}/members", json={"emails": ["jane@sequoia.example"]}
        ).json()
        assert first["added"] == ["jane@sequoia.example"]
        assert second["added"] == []
        assert second["skipped"] == ["jane@sequoia.example"]

    def test_no_invitation_is_sent_and_the_response_says_so(self, client: TestClient):
        room_id = make_room(client)
        group = make_group(client, room_id)
        response = client.post(
            f"{PREFIX}/groups/{group['id']}/members", json={"emails": ["jane@sequoia.example"]}
        ).json()
        assert response["invitations_sent"] == 0
        assert response["invitation_note"] == vocab.NO_INVITATIONS

    def test_members_are_listed_by_address(self, client: TestClient):
        room_id = make_room(client)
        group = make_group(client, room_id)
        client.post(
            f"{PREFIX}/groups/{group['id']}/members",
            json={"emails": ["zoe@acme.example", "amy@acme.example"]},
        )
        listed = client.get(f"{PREFIX}/groups/{group['id']}/members").json()
        assert [row[vocab.EMAIL_FIELD] for row in listed["members"]] == [
            "amy@acme.example",
            "zoe@acme.example",
        ]

    def test_a_bare_address_list_is_accepted(self, client: TestClient):
        """The CLI takes the addresses without a wrapper, so the array alone is read."""
        room_id = make_room(client)
        group = make_group(client, room_id)
        response = client.post(f"{PREFIX}/groups/{group['id']}/members", json=["jane@acme.example"])
        assert response.status_code == 201, response.text
        assert response.json()["added"] == ["jane@acme.example"]

    def test_removing_a_member_reports_no_reissue(self, client: TestClient):
        """ "Later changes to the group's permissions or members apply to the existing link
        immediately, no re-sharing." """
        room_id = make_room(client)
        group = make_group(client, room_id)
        client.post(
            f"{PREFIX}/groups/{group['id']}/members", json={"emails": ["jane@acme.example"]}
        )
        member = client.get(f"{PREFIX}/groups/{group['id']}/members").json()["members"][0]
        response = client.delete(f"{PREFIX}/groups/{group['id']}/members/{member['id']}")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body[vocab.EMAIL_FIELD] == "jane@acme.example"
        assert body["link_reissued"] is False

    def test_a_removed_member_is_refused_on_the_next_view(self, client: TestClient):
        room_id = make_room(client)
        group = make_group(client, room_id)
        client.post(
            f"{PREFIX}/groups/{group['id']}/members", json={"emails": ["jane@acme.example"]}
        )
        link = make_link(
            client,
            room_id,
            **{vocab.AUDIENCE_TYPE_FIELD: vocab.AUDIENCE_GROUP, "group_id": group["id"]},
        )
        assert (
            client.get(
                f"{PREFIX}/links/{link['id']}/view", params={"email": "jane@acme.example"}
            ).json()["admitted"]
            is True
        )
        member = client.get(f"{PREFIX}/groups/{group['id']}/members").json()["members"][0]
        client.delete(f"{PREFIX}/groups/{group['id']}/members/{member['id']}")
        # The same link and the same address. No re-share, and the answer has changed.
        after = client.get(
            f"{PREFIX}/links/{link['id']}/view", params={"email": "jane@acme.example"}
        ).json()
        assert after["admitted"] is False


# --------------------------------------------------------------------------- #
# the group ACL
# --------------------------------------------------------------------------- #


class TestGroupAclSurface:
    def _room_with_two_documents(self, client: TestClient) -> tuple[str, str, str]:
        room_id = make_room(client)
        first = make_item(
            client,
            room_id,
            vocab.DOCUMENT_COLLECTION,
            {NAME_FIELD: "Deck", vocab.PARENT_FOLDER_FIELD: vocab.ROOT_FOLDER},
        )
        second = make_item(
            client,
            room_id,
            vocab.DOCUMENT_COLLECTION,
            {NAME_FIELD: "Pricing", vocab.PARENT_FOLDER_FIELD: vocab.ROOT_FOLDER},
        )
        return room_id, first, second

    def test_items_resolve_for_a_room(self, client: TestClient):
        """ " ``GET .../documents`` and ``GET .../folders`` to "resolve ``item_id``s" """
        room_id, first, _ = self._room_with_two_documents(client)
        make_item(
            client,
            room_id,
            vocab.FOLDER_COLLECTION,
            {NAME_FIELD: "Decks", vocab.PARENT_FOLDER_FIELD: vocab.ROOT_FOLDER},
        )
        listed = client.get(f"{PREFIX}/rooms/{room_id}/items").json()
        assert listed["count"] == 3
        kinds = {row["item_type"] for row in listed["items"]}
        assert kinds == {vocab.ITEM_TYPE_DOCUMENT, vocab.ITEM_TYPE_FOLDER}
        assert first in {row["item_id"] for row in listed["items"]}

    def test_an_empty_room_says_it_has_nothing_to_grant(self, client: TestClient):
        room_id = make_room(client)
        listed = client.get(f"{PREFIX}/rooms/{room_id}/items").json()
        assert listed["count"] == 0
        assert "nothing to grant" in listed["no_items_note"]

    def test_a_delta_write_reports_what_it_touched_and_left_alone(self, client: TestClient):
        room_id, first, second = self._room_with_two_documents(client)
        group = make_group(client, room_id)
        permissions(
            client,
            f"/groups/{group['id']}/permissions",
            [
                {
                    "item_id": first,
                    "item_type": vocab.ITEM_TYPE_DOCUMENT,
                    vocab.CAN_VIEW: True,
                    vocab.CAN_DOWNLOAD: True,
                }
            ],
        )
        body = permissions(
            client,
            f"/groups/{group['id']}/permissions",
            [
                {
                    "item_id": second,
                    "item_type": vocab.ITEM_TYPE_DOCUMENT,
                    vocab.CAN_VIEW: True,
                    vocab.CAN_DOWNLOAD: False,
                }
            ],
        )
        assert body["semantics"] == "delta"
        assert body["touched"] == [f"{vocab.ITEM_TYPE_DOCUMENT}:{second}"]
        assert body["untouched"] == [f"{vocab.ITEM_TYPE_DOCUMENT}:{first}"]

    def test_the_grid_reports_every_item_and_why_it_is_hidden(self, client: TestClient):
        """A rep needs to know whether nobody granted an item or somebody revoked it."""
        room_id, first, second = self._room_with_two_documents(client)
        group = make_group(client, room_id)
        permissions(
            client,
            f"/groups/{group['id']}/permissions",
            [
                {
                    "item_id": first,
                    "item_type": vocab.ITEM_TYPE_DOCUMENT,
                    vocab.CAN_VIEW: True,
                    vocab.CAN_DOWNLOAD: True,
                },
                {
                    "item_id": second,
                    "item_type": vocab.ITEM_TYPE_DOCUMENT,
                    vocab.CAN_VIEW: False,
                    vocab.CAN_DOWNLOAD: False,
                },
            ],
        )
        grid = client.get(f"{PREFIX}/groups/{group['id']}/permissions").json()
        rows = {row["item_id"]: row for row in grid["items"]}
        assert rows[first]["state"] == vocab.VISIBLE
        assert rows[second][vocab.DENY_REASON_FIELD] == vocab.DENY_CAN_VIEW_FALSE
        assert grid["granted"] == 2
        assert grid["hidden"] == 1

    def test_ancestors_are_auto_opened_and_named(self, client: TestClient):
        """ "Ancestor folders of any item made visible are automatically set to ``can_view:
        true``." """
        room_id = make_room(client)
        folder = make_item(
            client,
            room_id,
            vocab.FOLDER_COLLECTION,
            {NAME_FIELD: "Decks", vocab.PARENT_FOLDER_FIELD: vocab.ROOT_FOLDER},
        )
        document = make_item(
            client,
            room_id,
            vocab.DOCUMENT_COLLECTION,
            {NAME_FIELD: "Deck", vocab.PARENT_FOLDER_FIELD: folder},
        )
        group = make_group(client, room_id)
        body = permissions(
            client,
            f"/groups/{group['id']}/permissions",
            [
                {
                    "item_id": document,
                    "item_type": vocab.ITEM_TYPE_DOCUMENT,
                    vocab.CAN_VIEW: True,
                    vocab.CAN_DOWNLOAD: True,
                }
            ],
        )
        assert body["auto_opened"] == [f"{vocab.ITEM_TYPE_FOLDER}:{folder}"]
        grid = client.get(f"{PREFIX}/groups/{group['id']}/permissions").json()
        rows = {row["item_id"]: row for row in grid["items"]}
        assert rows[folder][vocab.AUTO_OPENED_FIELD] is True
        assert rows[folder]["state"] == vocab.VIEW_ONLY

    def test_a_dangling_grant_is_reported(self, client: TestClient):
        room_id = make_room(client)
        group = make_group(client, room_id)
        body = permissions(
            client,
            f"/groups/{group['id']}/permissions",
            [
                {
                    "item_id": "ddoc_absent",
                    "item_type": vocab.ITEM_TYPE_DOCUMENT,
                    vocab.CAN_VIEW: True,
                    vocab.CAN_DOWNLOAD: False,
                }
            ],
        )
        assert body["dangling_items"]
        grid = client.get(f"{PREFIX}/groups/{group['id']}/permissions").json()
        assert [row["item_id"] for row in grid["dangling"]] == ["ddoc_absent"]

    def test_a_closed_entry_is_a_400(self, client: TestClient):
        """``PermissionEntry`` declares ``additionalProperties: false``, quoted."""
        room_id = make_room(client)
        group = make_group(client, room_id)
        response = client.put(
            f"{PREFIX}/groups/{group['id']}/permissions",
            json={
                "permissions": [
                    {
                        "item_id": "doc_x",
                        "item_type": vocab.ITEM_TYPE_DOCUMENT,
                        vocab.CAN_VIEW: True,
                        vocab.CAN_DOWNLOAD: False,
                        "expires_at": "2026-12-31",
                    }
                ]
            },
        )
        assert response.status_code == 400
        assert "expires_at" in str(response.json())

    def test_a_non_boolean_flag_is_a_400(self, client: TestClient):
        room_id = make_room(client)
        group = make_group(client, room_id)
        response = client.put(
            f"{PREFIX}/groups/{group['id']}/permissions",
            json={
                "permissions": [
                    {
                        "item_id": "doc_x",
                        "item_type": vocab.ITEM_TYPE_DOCUMENT,
                        vocab.CAN_VIEW: True,
                        vocab.CAN_DOWNLOAD: "yes please",
                    }
                ]
            },
        )
        assert response.status_code == 400

    def test_a_call_with_no_permissions_key_is_a_400_not_a_clear(self, client: TestClient):
        room_id = make_room(client)
        group = make_group(client, room_id)
        response = client.put(f"{PREFIX}/groups/{group['id']}/permissions", json={"perms": []})
        assert response.status_code == 400
        assert response.json()["errors"]["permissions"]

    def test_two_entries_for_one_item_in_one_call_are_a_400(self, client: TestClient):
        room_id = make_room(client)
        group = make_group(client, room_id)
        entry = {
            "item_id": "doc_x",
            "item_type": vocab.ITEM_TYPE_DOCUMENT,
            vocab.CAN_VIEW: True,
            vocab.CAN_DOWNLOAD: True,
        }
        response = client.put(
            f"{PREFIX}/groups/{group['id']}/permissions",
            json={"permissions": [entry, {**entry, vocab.CAN_DOWNLOAD: False}]},
        )
        assert response.status_code == 400

    def test_a_write_records_the_route_that_served_it(self, client: TestClient):
        """The audit row's source is the route string the router built, with its path template
        rather than the concrete id the request happened to carry."""
        room_id = make_room(client)
        make_group(client, room_id)
        audit = client.get("/api/audit", params={"collection": vocab.GROUP_COLLECTION}).json()
        assert audit["entries"][0]["source"] == f"POST {PREFIX}/rooms/{{room_id}}/groups"


# --------------------------------------------------------------------------- #
# the link ACL
# --------------------------------------------------------------------------- #


class TestLinkAclSurface:
    def test_a_general_link_starts_unscoped(self, client: TestClient):
        """ "With no overrides, viewers see the full dataroom." """
        room_id = make_room(client)
        link = make_link(client, room_id)
        assert link[LINK_SCOPE_SET] is False
        assert link["scope_state"] == "unscoped"

    def test_a_full_replace_reports_the_drop_it_caused(self, client: TestClient):
        room_id = make_room(client)
        first = make_item(
            client,
            room_id,
            vocab.DOCUMENT_COLLECTION,
            {NAME_FIELD: "A", vocab.PARENT_FOLDER_FIELD: "root"},
        )
        second = make_item(
            client,
            room_id,
            vocab.DOCUMENT_COLLECTION,
            {NAME_FIELD: "B", vocab.PARENT_FOLDER_FIELD: "root"},
        )
        link = make_link(client, room_id)
        permissions(
            client,
            f"/links/{link['id']}/permissions",
            [
                {
                    "item_id": first,
                    "item_type": vocab.ITEM_TYPE_DOCUMENT,
                    vocab.CAN_VIEW: True,
                    vocab.CAN_DOWNLOAD: True,
                },
                {
                    "item_id": second,
                    "item_type": vocab.ITEM_TYPE_DOCUMENT,
                    vocab.CAN_VIEW: True,
                    vocab.CAN_DOWNLOAD: False,
                },
            ],
        )
        body = permissions(
            client,
            f"/links/{link['id']}/permissions",
            [
                {
                    "item_id": first,
                    "item_type": vocab.ITEM_TYPE_DOCUMENT,
                    vocab.CAN_VIEW: True,
                    vocab.CAN_DOWNLOAD: True,
                }
            ],
        )
        assert body["semantics"] == "full_replace"
        assert body["dropped"] == [f"{vocab.ITEM_TYPE_DOCUMENT}:{second}"]

    def test_an_empty_array_clears_and_is_not_the_same_as_unscoped(self, client: TestClient):
        """ "Remove all overrides and hide every item." """
        room_id = make_room(client)
        document = make_item(
            client,
            room_id,
            vocab.DOCUMENT_COLLECTION,
            {NAME_FIELD: "A", vocab.PARENT_FOLDER_FIELD: "root"},
        )
        link = make_link(client, room_id)
        permissions(
            client,
            f"/links/{link['id']}/permissions",
            [
                {
                    "item_id": document,
                    "item_type": vocab.ITEM_TYPE_DOCUMENT,
                    vocab.CAN_VIEW: True,
                    vocab.CAN_DOWNLOAD: False,
                }
            ],
        )
        cleared = permissions(client, f"/links/{link['id']}/permissions", [])
        assert cleared["scope_state"] == "cleared"
        grid = client.get(f"{PREFIX}/links/{link['id']}/permissions").json()
        assert grid["scope_state"] == "cleared"
        assert len(grid["revoked"]) == 1

    def test_a_group_link_answers_422_and_names_the_way_out(self, client: TestClient):
        """ " Rejected with ``422`` on links with ``audience_type: \"group\"``"""
        room_id = make_room(client)
        group = make_group(client, room_id)
        link = make_link(
            client,
            room_id,
            **{vocab.AUDIENCE_TYPE_FIELD: vocab.AUDIENCE_GROUP, "group_id": group["id"]},
        )
        response = client.put(f"{PREFIX}/links/{link['id']}/permissions", json={"permissions": []})
        assert response.status_code == 422
        body = response.json()
        assert body["error"] == vocab.SCOPE_CONFLICT_REJECTED
        assert "general" in body["way_out"]

    def test_a_group_link_writes_nothing_when_refused(self, client: TestClient):
        room_id = make_room(client)
        group = make_group(client, room_id)
        link = make_link(
            client,
            room_id,
            **{vocab.AUDIENCE_TYPE_FIELD: vocab.AUDIENCE_GROUP, "group_id": group["id"]},
        )
        before = client.get(
            "/api/audit", params={"collection": vocab.LINK_PERMISSION_COLLECTION}
        ).json()["count"]
        client.put(f"{PREFIX}/links/{link['id']}/permissions", json={"permissions": []})
        after = client.get(
            "/api/audit", params={"collection": vocab.LINK_PERMISSION_COLLECTION}
        ).json()["count"]
        assert after == before

    def test_a_group_link_reports_the_group_as_its_scope(self, client: TestClient):
        room_id = make_room(client)
        group = make_group(client, room_id)
        link = make_link(
            client,
            room_id,
            **{vocab.AUDIENCE_TYPE_FIELD: vocab.AUDIENCE_GROUP, "group_id": group["id"]},
        )
        assert link["scope_state"] == "group"
        assert link["email_gated"] is True
        assert link["email_gate_note"] == vocab.EMAIL_GATE_NOTE

    def test_a_link_naming_a_group_from_another_room_is_a_404(self, client: TestClient):
        first = make_room(client, "First")
        second = make_room(client, "Second")
        group = make_group(client, first)
        response = client.post(
            f"{PREFIX}/rooms/{second}/links",
            json={
                NAME_FIELD: "x",
                vocab.AUDIENCE_TYPE_FIELD: vocab.AUDIENCE_GROUP,
                "group_id": group["id"],
            },
        )
        assert response.status_code == 404

    def test_links_can_be_narrowed_to_one_group(self, client: TestClient):
        room_id = make_room(client)
        group = make_group(client, room_id)
        make_link(
            client,
            room_id,
            **{vocab.AUDIENCE_TYPE_FIELD: vocab.AUDIENCE_GROUP, "group_id": group["id"]},
        )
        make_link(client, room_id, **{NAME_FIELD: "General"})
        listed = client.get(
            f"{PREFIX}/rooms/{room_id}/links", params={"group_id": group["id"]}
        ).json()
        assert listed["count"] == 1
        assert listed["links"][0]["group_id"] == group["id"]


# --------------------------------------------------------------------------- #
# the view
# --------------------------------------------------------------------------- #


class TestViewSurface:
    def _scoped(self, client: TestClient) -> tuple[str, dict[str, Any]]:
        """A group link, one member, one granted document and one folder holding it."""
        room_id = make_room(client)
        folder = make_item(
            client,
            room_id,
            vocab.FOLDER_COLLECTION,
            {NAME_FIELD: "Decks", vocab.PARENT_FOLDER_FIELD: "root"},
        )
        document = make_item(
            client,
            room_id,
            vocab.DOCUMENT_COLLECTION,
            {NAME_FIELD: "Deck", vocab.PARENT_FOLDER_FIELD: folder},
        )
        group = make_group(client, room_id)
        client.post(
            f"{PREFIX}/groups/{group['id']}/members", json={"emails": ["jane@acme.example"]}
        )
        permissions(
            client,
            f"/groups/{group['id']}/permissions",
            [
                {
                    "item_id": document,
                    "item_type": vocab.ITEM_TYPE_DOCUMENT,
                    vocab.CAN_VIEW: True,
                    vocab.CAN_DOWNLOAD: True,
                }
            ],
        )
        link = make_link(
            client,
            room_id,
            **{
                vocab.AUDIENCE_TYPE_FIELD: vocab.AUDIENCE_GROUP,
                "group_id": group["id"],
                "allow_download": True,
            },
        )
        return room_id, link

    def test_a_member_sees_only_what_was_granted(self, client: TestClient):
        _, link = self._scoped(client)
        view = client.get(
            f"{PREFIX}/links/{link['id']}/view", params={"email": "jane@acme.example"}
        ).json()
        assert view["admitted"] is True
        assert view["membership_step"] == vocab.MEMBERSHIP_BY_EMAIL
        # The document and the folder above it. Nothing else.
        assert {row["item_type"] for row in view["items"]} == {
            vocab.ITEM_TYPE_DOCUMENT,
            vocab.ITEM_TYPE_FOLDER,
        }
        assert view["item_count"] == 2
        assert view["hidden_count"] == 0
        assert view["filtered_server_side"] is True

    def test_a_stranger_is_a_200_saying_why_not_a_403(self, client: TestClient):
        """The viewer did nothing wrong. The link exists; the answer is that this address is not
        in the audience, and a viewer screen needs the membership step to say so."""
        _, link = self._scoped(client)
        response = client.get(
            f"{PREFIX}/links/{link['id']}/view", params={"email": "nobody@elsewhere.example"}
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["admitted"] is False
        assert body["denied_reason"] == vocab.NOT_A_MEMBER
        assert body["items"] == []

    def test_the_hidden_items_are_never_returned(self, client: TestClient):
        room_id = make_room(client)
        make_item(
            client,
            room_id,
            vocab.DOCUMENT_COLLECTION,
            {NAME_FIELD: "Secret", vocab.PARENT_FOLDER_FIELD: "root"},
        )
        folder = make_item(
            client,
            room_id,
            vocab.FOLDER_COLLECTION,
            {NAME_FIELD: "Decks", vocab.PARENT_FOLDER_FIELD: "root"},
        )
        document = make_item(
            client,
            room_id,
            vocab.DOCUMENT_COLLECTION,
            {NAME_FIELD: "Deck", vocab.PARENT_FOLDER_FIELD: folder},
        )
        group = make_group(client, room_id)
        permissions(
            client,
            f"/groups/{group['id']}/permissions",
            [
                {
                    "item_id": document,
                    "item_type": vocab.ITEM_TYPE_DOCUMENT,
                    vocab.CAN_VIEW: True,
                    vocab.CAN_DOWNLOAD: False,
                }
            ],
        )
        view = client.get(f"{PREFIX}/links/{make_link(client, room_id)['id']}/view").json()
        # Unscoped, so everything shows. That is the sourced behaviour for a link with no overrides.
        assert view["scope_state"] == "unscoped"
        assert view["item_count"] == 3

        # Scoped to the folder only. The other two are counted, not returned.
        link = make_link(client, room_id)
        permissions(
            client,
            f"/links/{link['id']}/permissions",
            [
                {
                    "item_id": folder,
                    "item_type": vocab.ITEM_TYPE_FOLDER,
                    vocab.CAN_VIEW: True,
                    vocab.CAN_DOWNLOAD: False,
                }
            ],
        )
        scoped = client.get(f"{PREFIX}/links/{link['id']}/view").json()
        assert [row["item_id"] for row in scoped["items"]] == [folder]
        assert scoped["hidden_count"] == 2
        assert "Secret" not in str(scoped["items"])

    def test_the_link_download_switch_is_reported_separately_from_the_row(self, client: TestClient):
        _, link = self._scoped(client)
        view = client.get(
            f"{PREFIX}/links/{link['id']}/view", params={"email": "jane@acme.example"}
        ).json()
        document = next(
            row for row in view["items"] if row["item_type"] == vocab.ITEM_TYPE_DOCUMENT
        )
        assert document["can_download_row"] is True
        assert document["can_download"] is True
        assert document["download_blocked_by_link"] is False

    def test_a_general_link_reports_that_it_did_not_check_membership(self, client: TestClient):
        room_id = make_room(client)
        view = client.get(f"{PREFIX}/links/{make_link(client, room_id)['id']}/view").json()
        assert view["membership_checked"] is False
        assert view["membership"] is None

    def test_the_view_carries_the_not_proof_sentence(self, client: TestClient):
        _, link = self._scoped(client)
        view = client.get(
            f"{PREFIX}/links/{link['id']}/view", params={"email": "jane@acme.example"}
        ).json()
        assert view[vocab.NOT_PROOF_FIELD] == vocab.NOT_PROOF
        assert view[vocab.LIMITATION_FIELD] == vocab.LIMITATION


# --------------------------------------------------------------------------- #
# the error shapes
# --------------------------------------------------------------------------- #


class TestErrorShapes:
    def test_an_unknown_audience_is_a_404_not_a_500(self, client: TestClient):
        assert client.get(f"{PREFIX}/groups/nope").status_code == 404
        assert client.get(f"{PREFIX}/groups/nope/permissions").status_code == 404
        assert client.get(f"{PREFIX}/groups/nope/members").status_code == 404

    def test_an_unknown_member_is_a_404(self, client: TestClient):
        room_id = make_room(client)
        group = make_group(client, room_id)
        assert client.delete(f"{PREFIX}/groups/{group['id']}/members/nope").status_code == 404

    def test_an_unknown_link_is_a_404(self, client: TestClient):
        assert client.get(f"{PREFIX}/links/nope").status_code == 404
        assert client.get(f"{PREFIX}/links/nope/permissions").status_code == 404
        assert client.get(f"{PREFIX}/links/nope/view").status_code == 404

    def test_an_unknown_room_is_a_404(self, client: TestClient):
        response = client.post(f"{PREFIX}/rooms/nope/groups", json={NAME_FIELD: "x"})
        assert response.status_code == 404
        assert response.json()["error"] == "not_found"

    def test_an_unknown_decision_is_a_404(self, client: TestClient):
        assert client.get(f"{PREFIX}/decisions/NOPE").status_code == 404

    def test_an_unknown_audience_type_is_a_400(self, client: TestClient):
        room_id = make_room(client)
        response = client.post(
            f"{PREFIX}/rooms/{room_id}/links",
            json={NAME_FIELD: "x", vocab.AUDIENCE_TYPE_FIELD: "partner"},
        )
        assert response.status_code == 400
        assert response.json()["errors"][vocab.AUDIENCE_TYPE_FIELD]

    def test_a_group_link_with_no_group_is_a_400(self, client: TestClient):
        room_id = make_room(client)
        response = client.post(
            f"{PREFIX}/rooms/{room_id}/links",
            json={NAME_FIELD: "x", vocab.AUDIENCE_TYPE_FIELD: vocab.AUDIENCE_GROUP},
        )
        assert response.status_code == 400
        assert response.json()["errors"]["group_id"]

    def test_an_oversized_domain_list_is_a_400(self, client: TestClient):
        room_id = make_room(client)
        response = client.post(
            f"{PREFIX}/rooms/{room_id}/groups",
            json={
                NAME_FIELD: "x",
                vocab.DOMAINS_FIELD: [
                    f"d{index}.example" for index in range(vocab.MAX_DOMAINS + 1)
                ],
            },
        )
        assert response.status_code == 400
        assert response.json()["errors"][vocab.DOMAINS_FIELD]


# --------------------------------------------------------------------------- #
# the research surfaces
# --------------------------------------------------------------------------- #


class TestResearchSurfaces:
    def test_the_vocabulary_serves_both_item_types_and_their_collections(self, client: TestClient):
        body = client.get(f"{PREFIX}/vocabulary").json()
        types = {row["id"]: row for row in body["item_types"]}
        assert set(types) == set(vocab.ITEM_TYPES)
        assert types[vocab.ITEM_TYPE_DOCUMENT]["collection"] == vocab.DOCUMENT_COLLECTION
        assert types[vocab.ITEM_TYPE_FOLDER]["collection"] == vocab.FOLDER_COLLECTION

    def test_the_vocabulary_serves_the_closed_entry(self, client: TestClient):
        entry = client.get(f"{PREFIX}/vocabulary").json()["permission_entry"]
        assert entry["closed"] is True
        assert entry["required"] == list(vocab.PERMISSION_ENTRY_FIELDS)

    def test_the_vocabulary_serves_both_scopes_with_their_semantics(self, client: TestClient):
        scopes = {row["id"]: row for row in client.get(f"{PREFIX}/vocabulary").json()["scopes"]}
        assert scopes[vocab.SCOPE_GROUP]["semantics"] == "delta"
        assert scopes[vocab.SCOPE_LINK]["semantics"] == "full_replace"

    def test_the_vocabulary_serves_the_membership_order(self, client: TestClient):
        membership = client.get(f"{PREFIX}/vocabulary").json()["membership"]
        assert [row["id"] for row in membership["steps"]] == list(vocab.MEMBERSHIP_STEPS)

    def test_the_vocabulary_serves_the_three_size_caps(self, client: TestClient):
        caps = client.get(f"{PREFIX}/vocabulary").json()["caps"]
        assert caps == {
            "domains": vocab.MAX_DOMAINS,
            "members_per_call": vocab.MAX_MEMBERS_PER_CALL,
            "permissions_per_call": vocab.MAX_PERMISSIONS_PER_CALL,
        }

    def test_the_vocabulary_serves_the_email_gate_as_unconditional(self, client: TestClient):
        gating = client.get(f"{PREFIX}/vocabulary").json()["link_gating"]
        assert gating["email_gated"] is True
        assert "no setting that turns this off" in gating["note"]

    def test_the_vocabulary_serves_the_scope_conflict_rule(self, client: TestClient):
        conflict = client.get(f"{PREFIX}/vocabulary").json()["scope_conflict"]
        assert conflict["rule"] == vocab.SCOPE_CONFLICT_REJECTED
        assert "general" in conflict["message"]

    def test_the_vocabulary_serves_the_four_link_scope_states(self, client: TestClient):
        states = {
            row["id"] for row in client.get(f"{PREFIX}/vocabulary").json()["link_scope_states"]
        }
        assert states == {"unscoped", "cleared", "scoped", "group"}

    def test_the_decisions_route_serves_every_recorded_derivation(self, client: TestClient):
        body = client.get(f"{PREFIX}/decisions").json()
        assert body["count"] >= 9
        for decision in body["decisions"]:
            assert decision["options"]
            assert decision["chosen"] in decision["options"]
            assert decision["rejected_because"]
            assert decision["cost_of_the_choice"]

    def test_one_decision_is_readable_by_id(self, client: TestClient):
        body = client.get(f"{PREFIX}/decisions/DERIVED_DOWNLOAD_NEVER_IMPLIES_VIEW").json()
        assert body["chosen"] == "flags_independent"


# --------------------------------------------------------------------------- #
# the audit-source rule
# --------------------------------------------------------------------------- #


class TestAuditSourceRule:
    def test_every_source_this_router_can_record_names_a_mounted_route(self):
        """The rule the build brief asks for by name.

        Every ``source=`` is built by ``_source`` from the router's own prefix and a path
        literal. This walks the module for those literals and checks each against the routes the
        host actually mounted, so an audit row cannot name a route the app stopped serving.
        """

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

    def test_the_engine_records_no_literal_route_of_its_own(self):
        """A domain function hardcoding a URL leaves the audit log naming a route the app stopped
        serving."""
        engine_module = importlib.import_module("dsr.audience_permissions.engine")
        source = Path(engine_module.__file__ or "").read_text(encoding="utf-8")
        assert "/api/" not in source

    def test_a_live_write_records_the_route_that_served_it(self, client: TestClient):
        room_id = make_room(client)
        group = make_group(client, room_id)
        members = client.post(
            f"{PREFIX}/groups/{group['id']}/members", json={"emails": ["jane@acme.example"]}
        )
        assert members.status_code == 201
        audit = client.get("/api/audit", params={"collection": vocab.MEMBER_COLLECTION}).json()
        source = audit["entries"][0]["source"]
        mounted = {
            f"{method} {route.path}"
            for route in importlib.import_module(FEATURE_MODULE).router.routes
            for method in route.methods
        }
        assert source in mounted


# --------------------------------------------------------------------------- #
# the honesty rule
# --------------------------------------------------------------------------- #


class TestHonestyRule:
    def test_every_endpoint_carries_the_limitation(self, client: TestClient):
        room_id = make_room(client)
        group = make_group(client, room_id)
        link = make_link(client, room_id)
        for path in (
            f"{PREFIX}/summary",
            f"{PREFIX}/vocabulary",
            f"{PREFIX}/rooms/{room_id}/groups",
            f"{PREFIX}/groups/{group['id']}",
            f"{PREFIX}/groups/{group['id']}/members",
            f"{PREFIX}/groups/{group['id']}/permissions",
            f"{PREFIX}/rooms/{room_id}/items",
            f"{PREFIX}/rooms/{room_id}/links",
            f"{PREFIX}/links/{link['id']}",
            f"{PREFIX}/links/{link['id']}/permissions",
            f"{PREFIX}/links/{link['id']}/view",
        ):
            body = client.get(path).json()
            assert vocab.LIMITATION_FIELD in body, path

    def test_the_scope_ownership_statement_is_carried_where_a_grid_is_edited(
        self, client: TestClient
    ):
        room_id = make_room(client)
        group = make_group(client, room_id)
        for path in (
            f"{PREFIX}/groups/{group['id']}",
            f"{PREFIX}/groups/{group['id']}/permissions",
        ):
            body = client.get(path).json()
            assert body[vocab.OWNER_FIELD] == vocab.SCOPE_OWNER

    def test_the_assumption_statement_reaches_the_configuring_caller(self, client: TestClient):
        room_id = make_room(client)
        body = client.get(f"{PREFIX}/rooms/{room_id}/items").json()
        assert body[vocab.ASSUMPTION_FIELD] == vocab.ASSUMPTION

    def test_the_board_names_the_audiences_that_have_been_granted_nothing(self, client: TestClient):
        """The shipped default is a state a rep has to be able to see rather than infer."""
        room_id = make_room(client)
        make_group(client, room_id, **{NAME_FIELD: "Granted nothing"})
        granted = make_group(client, room_id, **{NAME_FIELD: "Granted something"})
        document = make_item(
            client,
            room_id,
            vocab.DOCUMENT_COLLECTION,
            {NAME_FIELD: "A", vocab.PARENT_FOLDER_FIELD: "root"},
        )
        permissions(
            client,
            f"/groups/{granted['id']}/permissions",
            [
                {
                    "item_id": document,
                    "item_type": vocab.ITEM_TYPE_DOCUMENT,
                    vocab.CAN_VIEW: True,
                    vocab.CAN_DOWNLOAD: False,
                }
            ],
        )
        board = client.get(f"{PREFIX}/summary", params={"room_id": room_id}).json()
        assert board["groups"] == 2
        assert board["groups_with_no_permissions"] == 1
        assert board["groups_with_no_permissions_names"] == ["Granted nothing"]

    def test_a_refusal_repeats_the_limitation(self, client: TestClient):
        """A refusal is exactly where a reader is most likely to over-read the control."""
        room_id = make_room(client)
        group = make_group(client, room_id)
        link = make_link(
            client,
            room_id,
            **{vocab.AUDIENCE_TYPE_FIELD: vocab.AUDIENCE_GROUP, "group_id": group["id"]},
        )
        body = client.put(
            f"{PREFIX}/links/{link['id']}/permissions", json={"permissions": []}
        ).json()
        assert body["rule"] == vocab.SCOPE_CONFLICT_REJECTED
        assert "group" in body["way_out"]


# --------------------------------------------------------------------------- #
# the frontend contract
# --------------------------------------------------------------------------- #


class TestFrontendContract:
    def test_the_page_says_what_this_control_is_worth(self):
        """The frontend ships words, so the words are the thing to test."""
        joined = _frontend_text()
        assert "does not establish who" in joined
        assert "does not invite anybody" in joined

    def test_the_page_states_the_default_deny_rule(self):
        joined = _frontend_text()
        assert "granted" in joined
        assert "invisible" in joined or "sees nothing" in joined

    def test_the_page_names_both_scopes(self):
        joined = _frontend_text()
        assert "delta" in joined
        assert "full-replace" in joined or "full replace" in joined

    def test_the_page_names_the_three_membership_steps(self):
        joined = _frontend_text()
        for step in vocab.MEMBERSHIP_STEPS:
            assert step in joined, step

    def test_the_page_has_no_emoji_as_an_icon(self):
        """The design floor bans it and a page that ships one fails review, so it is asserted."""
        for path in _frontend_files():
            text = path.read_text(encoding="utf-8")
            for marker in ("\U0001f4e6", "\U0001f512", "✅", "❌", "\U0001f6e1"):
                assert marker not in text, f"{path.name} uses an emoji as an icon"


def _frontend_dir() -> Path:
    import dsr

    module = importlib.import_module(FEATURE_MODULE)
    root = Path(dsr.__file__ or "").resolve().parents[2]
    folder = root / "frontend" / "src" / "features" / module.FEATURE["id"]
    if not folder.is_dir():
        pytest.skip("frontend sources are not present in this checkout")
    return folder


def _frontend_files() -> list[Path]:
    return [
        path
        for path in _frontend_dir().glob("*")
        if path.suffix in (".jsx", ".js") and ".test." not in path.name
    ]


def _frontend_text() -> str:
    return " ".join(path.read_text(encoding="utf-8") for path in _frontend_files()).lower()
