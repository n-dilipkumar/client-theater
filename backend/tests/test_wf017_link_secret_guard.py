"""Probe: the generic record route must not be a back door for the room secret.

The research for WF-017 states that the share-link secret is a "unique,
non-removable identifier ... for security purposes". ``PATCH
/api/records/room/{id}`` merged an arbitrary JSON body into the record with no
filter, so a caller who could reach the generic route could clear the secret
while still being served an ordinary rename.

These tests are written to assert the requirement, not today's behaviour. Each
one fails on an unfixed host and passes once the guard is wired into
``dsr.api.update_record``. They are kept as plain tests, with no xfail marker,
so the fix cannot regress silently.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest
from dsr.api import app
from dsr.features import load_feature
from fastapi.testclient import TestClient

feature = load_feature("wf_017_white_label")
PREFIX = feature.router.prefix

CNAME_TARGET = "cname.dsr.test"
BASE_URL = "http://127.0.0.1:8000"
FIXTURES = {"proposals.acme.com": [CNAME_TARGET]}


@pytest.fixture()
def client(monkeypatch):
    tmp = tempfile.TemporaryDirectory()
    monkeypatch.setenv("DSR_DB_PATH", str(Path(tmp.name) / "api.db"))
    monkeypatch.setenv("DSR_AUDIT_DIR", str(Path(tmp.name) / "audit"))
    monkeypatch.setenv("DSR_CNAME_TARGET", CNAME_TARGET)
    monkeypatch.setenv("DSR_PUBLIC_BASE_URL", BASE_URL)
    monkeypatch.setenv("DSR_CNAME_FIXTURES", json.dumps(FIXTURES))
    monkeypatch.setattr("dsr.api.FRONTEND_DIST", Path(tmp.name) / "absent-frontend")
    with TestClient(app) as test_client:
        yield test_client
    tmp.cleanup()


@pytest.fixture()
def room(client):
    return client.post("/api/records/room", json={"name": "Proposal Name"}).json()


def _secret(client, room_id: str) -> str:
    client.post(f"{PREFIX}/rooms/{room_id}/white-label/link-secret")
    slug = client.get(f"{PREFIX}/rooms/{room_id}/white-label").json()["slug"]
    return slug.rsplit("-", 1)[1]


def test_a_room_patch_cannot_clear_the_link_secret(room, client):
    """The secret survives a patch that sets it to null."""
    secret = _secret(client, room["id"])

    client.patch(f"/api/records/room/{room['id']}", json={"link_secret": None})

    stored = client.get(f"/api/records/room/{room['id']}").json()["data"]
    assert stored["link_secret"] == secret


def test_a_room_patch_cannot_overwrite_the_link_secret(room, client):
    """The secret survives a patch that sets it to a value the caller chose."""
    secret = _secret(client, room["id"])

    client.patch(f"/api/records/room/{room['id']}", json={"link_secret": "attackerChosen9z"})

    stored = client.get(f"/api/records/room/{room['id']}").json()["data"]
    assert stored["link_secret"] == secret


def test_a_room_patch_cannot_write_the_collaborator_token(room, client):
    """The reserved collaborator token is the product's own vocabulary."""
    client.patch(f"/api/records/room/{room['id']}", json={"collaborator_token": "attackerChosen9z"})

    stored = client.get(f"/api/records/room/{room['id']}").json()["data"]
    assert "collaborator_token" not in stored


def test_a_room_patch_cannot_write_the_domain(room, client):
    """The domain is verified through its own route only."""
    client.patch(f"/api/records/room/{room['id']}", json={"domain": "evil.acme.com"})

    stored = client.get(f"/api/records/room/{room['id']}").json()["data"]
    assert "domain" not in stored


def test_a_room_patch_still_honours_the_fields_it_is_allowed_to_change(room, client):
    """The guard drops reserved keys only. Everything else in the body lands."""
    secret = _secret(client, room["id"])

    client.patch(
        f"/api/records/room/{room['id']}",
        json={"link_secret": None, "name": "Renamed", "account": "Acme"},
    )

    stored = client.get(f"/api/records/room/{room['id']}").json()["data"]
    assert stored["link_secret"] == secret
    assert stored["name"] == "Renamed"
    assert stored["account"] == "Acme"


def test_a_patch_on_another_collection_keeps_its_own_fields(client):
    """The guard is a room-scoped field list. Other collections keep their data.

    ``link_secret`` is not the name of any core product field, so a feature
    that needs it on a non-room record must not be broken by this fix.
    """
    created = client.post("/api/records/document", json={"title": "Deck"}).json()

    client.patch(f"/api/records/document/{created['id']}", json={"title": "Deck v2"})

    stored = client.get(f"/api/records/document/{created['id']}").json()["data"]
    assert stored["title"] == "Deck v2"