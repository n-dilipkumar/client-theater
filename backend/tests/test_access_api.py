"""HTTP tests for WF-004.

These pin the parts of the workflow another team integrates against: the
status codes, the response envelope, and the fact that every refusal is
enforced on the server rather than merely hidden in the UI.
"""

from __future__ import annotations

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dsr.api import app
from dsr.access import ACCESS, INVITATIONS

FUTURE = (datetime.now(timezone.utc) + timedelta(days=60)).strftime("%Y-%m-%d")


@pytest.fixture()
def client(monkeypatch):
    import dsr.api as api_module

    tmp = tempfile.TemporaryDirectory()
    monkeypatch.setenv("DSR_DB_PATH", str(Path(tmp.name) / "access.db"))
    monkeypatch.setenv("DSR_AUDIT_DIR", str(Path(tmp.name) / "audit"))
    monkeypatch.setattr(api_module, "FRONTEND_DIST", Path(tmp.name) / "absent-frontend")
    with TestClient(app) as test_client:
        yield test_client
    tmp.cleanup()


@pytest.fixture()
def room(client):
    return client.post(
        "/api/records/room", json={"name": "Northwind Evaluation", "owner": "dana"}
    ).json()


def _grant(client, room_id, principal, role="viewer", access_valid_until=None):
    return client.post(
        "/api/records/room_access",
        params={"room_id": room_id},
        json={
            "principal": principal,
            "role": role,
            "access_valid_until": access_valid_until,
            "source": "manual",
        },
    ).json()


# -- the role vocabulary ------------------------------------------------------ #


def test_roles_endpoint_lists_the_three_documented_roles(client):
    body = client.get("/api/access/roles", params={"actor_role": "room_owner"}).json()

    assert [r["id"] for r in body["roles"]] == [
        "room_collaborator",
        "content_contributor",
        "viewer",
    ]
    assert body["default_role"] == "viewer"
    assert body["invitation_ttl_hours"] == 48
    assert body["expiring_soon_days"] == 7


def test_roles_endpoint_marks_what_a_viewer_may_assign(client):
    body = client.get("/api/access/roles", params={"actor_role": "viewer"}).json()
    assert all(r["assignable"] is False for r in body["roles"])


def test_roles_endpoint_marks_everything_the_owner_may_assign(client):
    body = client.get("/api/access/roles", params={"actor_role": "room_owner"}).json()
    assert all(r["assignable"] is True for r in body["roles"])


# -- reading Who Has Access --------------------------------------------------- #


def test_access_snapshot_of_an_empty_room(client, room):
    body = client.get(f"/api/rooms/{room['id']}/access", params={"actor": "dana"}).json()

    assert body["members"] == []
    assert body["pending_invitations"] == []
    assert body["banner"] is None
    assert body["actor"]["can_share"] is True
    assert body["actor"]["is_owner"] is True


def test_access_snapshot_lists_members_with_derived_expiry_facts(client, room):
    _grant(client, room["id"], "a@example.com", access_valid_until=FUTURE)

    member = client.get(
        f"/api/rooms/{room['id']}/access", params={"actor": "dana"}
    ).json()["members"][0]

    assert member["principal"] == "a@example.com"
    assert member["role_label"] == "Viewer"
    assert member["access_valid_until"] == FUTURE
    assert member["expires_at_utc"].endswith("Z")
    assert member["expiring_soon"] is False
    assert member["removable"] is True


def test_access_snapshot_reports_no_expiration_for_an_open_grant(client, room):
    _grant(client, room["id"], "a@example.com")

    member = client.get(
        f"/api/rooms/{room['id']}/access", params={"actor": "dana"}
    ).json()["members"][0]

    assert member["access_valid_until"] is None
    assert member["expires_at_utc"] is None


def test_access_snapshot_carries_the_banner(client, room):
    soon = (datetime.now(timezone.utc) + timedelta(days=2)).strftime("%Y-%m-%d")
    _grant(client, room["id"], "one@example.com", access_valid_until=soon)
    _grant(client, room["id"], "two@example.com", access_valid_until=soon)

    body = client.get(f"/api/rooms/{room['id']}/access", params={"actor": "dana"}).json()

    assert body["expiring_soon_count"] == 2
    assert body["banner"] == "2 users have access expiring within 7 days."


def test_access_snapshot_reports_a_viewer_cannot_share(client, room):
    _grant(client, room["id"], "watcher@example.com", role="viewer")

    body = client.get(
        f"/api/rooms/{room['id']}/access", params={"actor": "watcher@example.com"}
    ).json()

    assert body["actor"]["role"] == "viewer"
    assert body["actor"]["can_share"] is False
    assert body["actor"]["assignable_roles"] == []


def test_access_snapshot_of_an_unknown_room_is_404(client):
    assert client.get("/api/rooms/room_missing/access").status_code == 404


# -- sending invitations ------------------------------------------------------ #


def test_invite_returns_201_and_a_confirmation(client, room):
    response = client.post(
        f"/api/rooms/{room['id']}/invitations",
        params={"actor": "dana"},
        json={"emails": ["a@example.com", "b@example.com"], "role": "content_contributor"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["invites"] == 2
    assert body["pending"] == ["a@example.com", "b@example.com"]
    assert "2 invitations sent as Content Contributor" in body["message"]


def test_invite_lands_in_the_room_invitation_collection(client, room):
    client.post(
        f"/api/rooms/{room['id']}/invitations",
        params={"actor": "dana"},
        json={"emails": ["a@example.com"]},
    )

    listed = client.get(
        "/api/records/room_invitation", params={"room_id": room["id"]}
    ).json()
    assert listed["count"] == 1
    assert listed["records"][0]["data"]["state"] == "pending"


def test_invite_is_audited_with_the_actor_who_pressed_invite(client, room):
    _grant(client, room["id"], "lead@example.com", role="content_contributor")

    client.post(
        f"/api/rooms/{room['id']}/invitations",
        params={"actor": "lead@example.com"},
        json={"emails": ["a@example.com"]},
    )

    entries = client.get("/api/audit", params={"collection": INVITATIONS}).json()["entries"]
    assert entries[0]["actor"] == "lead@example.com"


def test_invite_accepts_a_single_address_as_a_bare_string(client, room):
    response = client.post(
        f"/api/rooms/{room['id']}/invitations",
        params={"actor": "dana"},
        json={"email": "a@example.com"},
    )
    assert response.status_code == 201
    assert response.json()["invites"] == 1


def test_invite_with_no_addresses_is_400(client, room):
    response = client.post(
        f"/api/rooms/{room['id']}/invitations", params={"actor": "dana"}, json={}
    )
    assert response.status_code == 400


def test_invite_with_an_invalid_address_is_400(client, room):
    response = client.post(
        f"/api/rooms/{room['id']}/invitations",
        params={"actor": "dana"},
        json={"emails": ["not-an-email"]},
    )

    assert response.status_code == 400
    assert "not a valid email address" in response.json()["detail"]


def test_invite_with_an_unknown_role_is_400(client, room):
    response = client.post(
        f"/api/rooms/{room['id']}/invitations",
        params={"actor": "dana"},
        json={"emails": ["a@example.com"], "role": "superuser"},
    )
    assert response.status_code == 400


def test_a_viewer_inviting_is_403(client, room):
    _grant(client, room["id"], "watcher@example.com", role="viewer")

    response = client.post(
        f"/api/rooms/{room['id']}/invitations",
        params={"actor": "watcher@example.com"},
        json={"emails": ["a@example.com"]},
    )

    assert response.status_code == 403
    assert "cannot share" in response.json()["detail"]


def test_an_anonymous_invite_is_403(client, room):
    response = client.post(
        f"/api/rooms/{room['id']}/invitations", json={"emails": ["a@example.com"]}
    )
    assert response.status_code == 403


def test_a_contributor_cannot_assign_room_collaborator(client, room):
    _grant(client, room["id"], "lead@example.com", role="content_contributor")

    response = client.post(
        f"/api/rooms/{room['id']}/invitations",
        params={"actor": "lead@example.com"},
        json={"emails": ["a@example.com"], "role": "room_collaborator"},
    )

    assert response.status_code == 403
    assert "only the room owner" in response.json()["detail"]


def test_inviting_to_an_unknown_room_is_404(client):
    response = client.post(
        "/api/rooms/room_missing/invitations",
        params={"actor": "dana"},
        json={"emails": ["a@example.com"]},
    )
    assert response.status_code == 404


# -- accepting ---------------------------------------------------------------- #


def test_accepting_turns_the_invitation_into_a_grant(client, room):
    client.post(
        f"/api/rooms/{room['id']}/invitations",
        params={"actor": "dana"},
        json={"emails": ["a@example.com"], "role": "content_contributor"},
    )
    invitation = client.get(
        "/api/records/room_invitation", params={"room_id": room["id"]}
    ).json()["records"][0]

    response = client.post(
        f"/api/invitations/{invitation['id']}/accept", params={"actor": "a@example.com"}
    )

    assert response.status_code == 200
    assert response.json()["access"]["data"]["role"] == "content_contributor"
    assert response.json()["invitation"]["state"] == "accepted"


def test_an_accepted_invitation_joins_who_has_access(client, room):
    client.post(
        f"/api/rooms/{room['id']}/invitations",
        params={"actor": "dana"},
        json={"emails": ["a@example.com"]},
    )
    invitation = client.get(
        "/api/records/room_invitation", params={"room_id": room["id"]}
    ).json()["records"][0]
    client.post(f"/api/invitations/{invitation['id']}/accept")

    body = client.get(f"/api/rooms/{room['id']}/access", params={"actor": "dana"}).json()
    assert [m["principal"] for m in body["members"]] == ["a@example.com"]
    assert body["pending_invitations"] == []


def test_accepting_twice_is_400(client, room):
    client.post(
        f"/api/rooms/{room['id']}/invitations",
        params={"actor": "dana"},
        json={"emails": ["a@example.com"]},
    )
    invitation = client.get(
        "/api/records/room_invitation", params={"room_id": room["id"]}
    ).json()["records"][0]
    client.post(f"/api/invitations/{invitation['id']}/accept")

    assert client.post(f"/api/invitations/{invitation['id']}/accept").status_code == 400


def test_accepting_an_unknown_invitation_is_404(client):
    assert client.post("/api/invitations/room_invitation_missing/accept").status_code == 404


def test_an_already_known_invitee_joins_immediately(client, room):
    _grant(client, room["id"], "a@example.com", role="viewer")

    response = client.post(
        f"/api/rooms/{room['id']}/invitations",
        params={"actor": "dana"},
        json={"emails": ["a@example.com"], "role": "content_contributor"},
    )

    body = response.json()
    assert body["joined_immediately"] == ["a@example.com"]
    assert body["pending"] == []
    members = client.get(
        f"/api/rooms/{room['id']}/access", params={"actor": "dana"}
    ).json()["members"]
    assert [m["role"] for m in members] == ["content_contributor"]


# -- editing access ----------------------------------------------------------- #


def test_changing_a_role_with_confirmation(client, room):
    grant = _grant(client, room["id"], "a@example.com")

    response = client.patch(
        f"/api/access/{grant['id']}",
        params={"actor": "dana", "confirm": True},
        json={"role": "content_contributor"},
    )

    assert response.status_code == 200
    assert response.json()["access"]["role"] == "content_contributor"


def test_changing_a_role_without_confirmation_is_428(client, room):
    grant = _grant(client, room["id"], "a@example.com")

    response = client.patch(
        f"/api/access/{grant['id']}", params={"actor": "dana"}, json={"role": "content_contributor"}
    )

    assert response.status_code == 428
    assert "needs confirmation" in response.json()["detail"]


def test_changing_the_expiry_needs_no_confirmation(client, room):
    grant = _grant(client, room["id"], "a@example.com")

    response = client.patch(
        f"/api/access/{grant['id']}", params={"actor": "dana"}, json={"set_expiry": True, "access_valid_until": FUTURE}
    )

    assert response.status_code == 200
    assert response.json()["access"]["access_valid_until"] == FUTURE


def test_clearing_the_expiry_returns_to_no_expiration(client, room):
    grant = _grant(client, room["id"], "a@example.com", access_valid_until=FUTURE)

    response = client.patch(
        f"/api/access/{grant['id']}",
        params={"actor": "dana"},
        json={"set_expiry": True, "access_valid_until": None},
    )

    assert response.json()["access"]["access_valid_until"] is None


def test_editing_an_unknown_grant_is_404(client):
    response = client.patch(
        "/api/access/room_access_missing", params={"actor": "dana"}, json={"role": "viewer"}
    )
    assert response.status_code == 404


def test_a_viewer_editing_someone_is_403(client, room):
    watcher = _grant(client, room["id"], "watcher@example.com", role="viewer")
    target = _grant(client, room["id"], "a@example.com")

    response = client.patch(
        f"/api/access/{target['id']}",
        params={"actor": "watcher@example.com", "confirm": True},
        json={"role": "content_contributor"},
    )

    assert response.status_code == 403
    assert watcher["id"] != target["id"]


# -- removing access ---------------------------------------------------------- #


def test_removing_with_confirmation(client, room):
    grant = _grant(client, room["id"], "a@example.com")

    response = client.delete(
        f"/api/access/{grant['id']}", params={"actor": "dana", "confirm": True}
    )

    assert response.status_code == 200
    assert response.json()["principal"] == "a@example.com"
    assert client.get(f"/api/records/room_access/{grant['id']}").status_code == 404


def test_removing_without_confirmation_is_428(client, room):
    grant = _grant(client, room["id"], "a@example.com")

    response = client.delete(f"/api/access/{grant['id']}", params={"actor": "dana"})

    assert response.status_code == 428
    assert "needs confirmation" in response.json()["detail"]


def test_removal_is_audited(client, room):
    grant = _grant(client, room["id"], "a@example.com")
    client.delete(f"/api/access/{grant['id']}", params={"actor": "dana", "confirm": True})

    entry = client.get(
        "/api/audit", params={"record_id": grant["id"], "action": "delete"}
    ).json()["entries"][0]
    assert entry["before_state"]["principal"] == "a@example.com"


def test_a_removed_person_disappears_from_who_has_access(client, room):
    grant = _grant(client, room["id"], "a@example.com", role="content_contributor")
    client.delete(f"/api/access/{grant['id']}", params={"actor": "dana", "confirm": True})

    body = client.get(f"/api/rooms/{room['id']}/access", params={"actor": "dana"}).json()
    assert body["members"] == []
    # And the removal really took: they can no longer share.
    assert client.get(
        f"/api/rooms/{room['id']}/access", params={"actor": "a@example.com"}
    ).json()["actor"]["can_share"] is False


def test_the_owners_grant_cannot_be_removed(client, room):
    grant = _grant(client, room["id"], "dana")

    response = client.delete(
        f"/api/access/{grant['id']}", params={"actor": "dana", "confirm": True}
    )

    assert response.status_code == 400
    assert "owner cannot be changed or removed" in response.json()["detail"]


# -- the whole flow, end to end ----------------------------------------------- #


def test_the_full_share_flow(client, room):
    """Type addresses, pick a role, set an expiry, invite, then accept."""
    invited = client.post(
        f"/api/rooms/{room['id']}/invitations",
        params={"actor": "dana"},
        json={
            "emails": ["buyer@northwind.example", "procurement@northwind.example"],
            "role": "content_contributor",
            "access_valid_until": FUTURE,
        },
    )
    assert invited.status_code == 201

    pending = client.get(
        f"/api/rooms/{room['id']}/access", params={"actor": "dana"}
    ).json()["pending_invitations"]
    assert len(pending) == 2
    assert all(item["hours_until_expiry"] == 48.0 for item in pending)
    assert all(item["role"] == "content_contributor" for item in pending)
    assert {item["email"] for item in pending} == {
        "buyer@northwind.example",
        "procurement@northwind.example",
    }

    buyer = next(item for item in pending if item["email"] == "buyer@northwind.example")
    client.post(f"/api/invitations/{buyer['id']}/accept")

    members = client.get(
        f"/api/rooms/{room['id']}/access", params={"actor": "dana"}
    ).json()["members"]
    assert [m["principal"] for m in members] == ["buyer@northwind.example"]
    assert members[0]["role_label"] == "Content Contributor"
    assert members[0]["access_valid_until"] == FUTURE

    # The other invitation is still waiting to be accepted.
    still_pending = client.get(
        f"/api/rooms/{room['id']}/access", params={"actor": "dana"}
    ).json()["pending_invitations"]
    assert [item["email"] for item in still_pending] == ["procurement@northwind.example"]

    # The whole flow is in the audit trail, attributed as it happened.
    collections = {
        e["collection"] for e in client.get("/api/audit", params={"limit": 50}).json()["entries"]
    }
    assert {INVITATIONS, ACCESS} <= collections


def test_the_access_collections_are_ordinary_schema_flexible_records(client, room):
    """A team adds a field to a grant with no migration and no code change."""
    grant = client.post(
        "/api/records/room_access",
        params={"room_id": room["id"]},
        json={"principal": "a@example.com", "role": "viewer", "cost_centre": "CC-4417"},
    ).json()

    assert grant["data"]["cost_centre"] == "CC-4417"

    hits = client.get(
        "/api/records/room_access", params={"where": '{"cost_centre":"CC-4417"}'}
    ).json()
    assert [r["id"] for r in hits["records"]] == [grant["id"]]
