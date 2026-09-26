"""Tests for WF-001: create a Digital Sales Room from an account and a template.

The researched flow is a three-step wizard (account -> template -> name and
optional friendly URL) that produces a room bound to exactly one site. These
tests pin the parts a coding agent could plausibly get wrong: the bindings the
workflow is responsible for, the one-site-per-room invariant, friendly-URL
behaviour, the audit trail, and the promise that an unknown field needs no
migration.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dsr.api import app
from dsr.db.audited import AuditedDatabase
from dsr.rooms import RoomCreationError, create_room, list_rooms, slugify
from dsr.store import RecordStore


@pytest.fixture()
def client(monkeypatch):
    import dsr.api as api_module

    tmp = tempfile.TemporaryDirectory()
    monkeypatch.setenv("DSR_DB_PATH", str(Path(tmp.name) / "api.db"))
    monkeypatch.setenv("DSR_AUDIT_DIR", str(Path(tmp.name) / "audit"))
    monkeypatch.setattr(api_module, "FRONTEND_DIST", Path(tmp.name) / "absent-frontend")
    with TestClient(app) as test_client:
        yield test_client
    tmp.cleanup()


@pytest.fixture()
def store(tmp_path):
    database = AuditedDatabase(tmp_path / "wf001.db", mirror_dir=tmp_path / "mirror")
    yield RecordStore(database)
    database.close()


def make_account(store: RecordStore, name: str = "Northwind Traders", **extra):
    return store.create("account", {"name": name, **extra}, actor="dana")


def create(store: RecordStore, **overrides):
    """Create a room with sensible defaults for the fields under test."""
    account = overrides.pop("account", None) or make_account(store)
    payload = {
        "name": "Acme Evaluation",
        "account_id": account["id"],
        "template_id": "tpl_standard",
    }
    payload.update(overrides)
    return create_room(store, **payload)


# -- the bindings the workflow is responsible for ---------------------------- #


def test_created_room_binds_account_template_and_site(store):
    account = make_account(store, "Northwind Traders")

    room = create(store, account=account, name="Acme Evaluation")

    assert room["collection"] == "room"
    assert room["data"]["account_id"] == account["id"]
    assert room["data"]["account_name"] == "Northwind Traders"
    assert room["data"]["template_id"] == "tpl_standard"
    assert room["data"]["status"] == "active"
    assert room["data"]["name"] == "Acme Evaluation"


def test_room_pins_the_template_version_it_was_created_from(store):
    room = create(store)

    assert room["data"]["template_version_id"] == "tpl_standard_v1"


def test_every_room_gets_exactly_one_site_and_the_room_carries_its_id(store):
    room = create(store)

    site_id = room["data"]["site_id"]
    assert site_id
    assert room["site"]["id"] == site_id

    sites = store.db.list("site")
    assert [s["id"] for s in sites] == [site_id]
    # The site is room-scoped, which is what makes "one site per room" queryable.
    assert sites[0]["room_id"] == room["id"]


def test_site_record_carries_the_friendly_url_and_template(store):
    room = create(store, friendly_url="acme-evaluation")

    assert room["site"]["data"]["friendly_url"] == "acme-evaluation"
    assert room["site"]["data"]["template_id"] == "tpl_standard"
    assert room["site"]["data"]["status"] == "active"


def test_created_by_falls_back_to_the_actor(store):
    room = create_room(
        store,
        name="Acme",
        account_id=make_account(store)["id"],
        template_id="tpl_standard",
        actor="dana",
    )

    assert room["data"]["created_by"] == "dana"


def test_created_by_username_is_stored_when_given(store):
    room = create(store, created_by="dana", created_by_username="Dana Okafor")

    assert room["data"]["created_by_username"] == "Dana Okafor"


# -- validation -------------------------------------------------------------- #


def test_name_is_required(store):
    with pytest.raises(RoomCreationError) as caught:
        create(store, name="   ")

    assert caught.value.code == "invalid_name"
    assert store.db.count("room") == 0


def test_name_length_is_bounded(store):
    with pytest.raises(RoomCreationError) as caught:
        create(store, name="x" * 201)

    assert caught.value.code == "invalid_name"


def test_unknown_account_is_refused_and_nothing_is_written(store):
    with pytest.raises(RoomCreationError) as caught:
        create(store, account_id="account_missing")

    assert caught.value.code == "account_not_found"
    assert caught.value.status == 404
    assert store.db.count("room") == 0
    assert store.db.count("site") == 0


def test_a_record_from_another_collection_is_not_an_account(store):
    document = store.create("document", {"title": "Deck"})

    with pytest.raises(RoomCreationError) as caught:
        create(store, account_id=document["id"])

    assert caught.value.code == "account_not_found"


def test_soft_deleted_account_cannot_be_bound(store):
    account = make_account(store)
    store.delete(account["id"])

    with pytest.raises(RoomCreationError) as caught:
        create(store, account_id=account["id"])

    assert caught.value.code == "account_not_found"


def test_unknown_template_is_refused(store):
    with pytest.raises(RoomCreationError) as caught:
        create(store, template_id="tpl_does_not_exist")

    assert caught.value.code == "template_not_found"
    assert store.db.count("room") == 0


# -- friendly URL ------------------------------------------------------------ #


def test_friendly_url_is_derived_from_the_name_when_omitted(store):
    room = create(store, name="Acme Enterprise Evaluation")

    assert room["data"]["friendly_url"] == "acme-enterprise-evaluation"
    assert room["data"]["friendly_url_source"] == "derived"


def test_derived_friendly_url_is_suffixed_until_free(store):
    create(store, name="Acme Evaluation")

    second = create(store, name="Acme Evaluation")

    assert second["data"]["friendly_url"] == "acme-evaluation-2"
    assert second["data"]["friendly_url_source"] == "derived"


def test_operator_supplied_friendly_url_is_normalised(store):
    room = create(store, friendly_url="  /Acme  Evaluation/  ")

    assert room["data"]["friendly_url"] == "acme-evaluation"
    assert room["data"]["friendly_url_source"] == "operator"


def test_duplicate_operator_friendly_url_is_a_conflict_not_a_silent_rewrite(store):
    create(store, friendly_url="acme")

    with pytest.raises(RoomCreationError) as caught:
        create(store, friendly_url="acme")

    assert caught.value.code == "friendly_url_taken"
    assert caught.value.status == 409
    assert store.db.count("room") == 1


def test_friendly_url_that_normalises_to_nothing_is_refused(store):
    with pytest.raises(RoomCreationError) as caught:
        create(store, friendly_url="///")

    assert caught.value.code == "invalid_friendly_url"


def test_name_with_no_url_safe_characters_is_refused(store):
    with pytest.raises(RoomCreationError) as caught:
        create(store, name="???")

    assert caught.value.code == "invalid_friendly_url"


def test_slugify_folds_accents_and_collapses_separators():
    assert slugify("  Ácme   Evaluación — Phase 2 ") == "acme-evaluacion-phase-2"


# -- atomicity --------------------------------------------------------------- #


def test_room_and_site_are_written_in_one_transaction(store):
    account = make_account(store)
    before = store.db.audit_count()

    create(store, account=account)

    # Exactly two new audit rows: the room and its site, no summary row between.
    assert store.db.audit_count() - before == 2
    inserts = store.db.audit(action="insert")
    assert {entry["collection"] for entry in inserts[:2]} == {"room", "site"}


def test_a_failure_after_the_room_is_written_leaves_no_room(store, monkeypatch):
    """The reason the site is written in the same transaction as the room."""
    from dsr.db.audited import AuditedWriter

    account = make_account(store)
    original = AuditedWriter.create
    calls = {"n": 0}

    def flaky(self, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("site allocation failed")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(AuditedWriter, "create", flaky)

    with pytest.raises(RuntimeError, match="site allocation failed"):
        create(store, account=account)

    # The room insert had already succeeded when the site failed, and neither
    # record nor either audit row survives.
    assert calls["n"] == 2
    assert store.db.count("room") == 0
    assert store.db.count("site") == 0
    assert store.db.audit_count() == 1  # only the account insert


# -- schema flexibility ------------------------------------------------------ #


def test_unknown_fields_are_stored_and_indexed_without_a_migration(store):
    account = make_account(store)

    room = create_room(
        store,
        name="Acme",
        account_id=account["id"],
        template_id="tpl_standard",
        extra={
            "branding": {"theme": "dark", "accent": "#22c55e"},
            "seats": 40,
            "requires_nda": True,
            "team_routing": {"queue": "enterprise"},
        },
    )

    data = room["data"]
    assert data["seats"] == 40
    assert data["requires_nda"] is True
    assert data["branding"]["theme"] == "dark"
    # Filterable through the dynamic index, dotted path and all.
    assert [r["id"] for r in store.db.find("room", {"team_routing.queue": "enterprise"})] == [room["id"]]
    assert [r["id"] for r in store.db.find("room", {"requires_nda": True})] == [room["id"]]


def test_extra_fields_cannot_overwrite_the_workflows_own_bindings(store):
    account = make_account(store)

    room = create_room(
        store,
        name="Acme",
        account_id=account["id"],
        template_id="tpl_standard",
        extra={"status": "archived", "account_id": "someone_elses_account", "template_id": "tpl_hijack"},
    )

    assert room["data"]["account_id"] == account["id"]
    assert room["data"]["template_id"] == "tpl_standard"
    assert room["data"]["status"] == "active"


def test_reserved_envelope_keys_cannot_be_smuggled_into_the_payload(store):
    account = make_account(store)

    room = create_room(
        store,
        name="Acme",
        account_id=account["id"],
        template_id="tpl_standard",
        extra={"id": "spoofed", "room_id": "spoofed", "revision": 99},
    )

    assert room["id"] != "spoofed"
    assert room["revision"] == 1
    assert "id" not in room["data"] and "revision" not in room["data"]


# -- template catalogue ------------------------------------------------------ #


def test_shipped_templates_are_offered_on_a_fresh_database(store):
    from dsr.rooms import list_templates

    ids = [template["template_id"] for template in list_templates(store)]

    assert "tpl_standard" in ids
    assert "tpl_guided_evaluation" in ids


def test_a_store_template_appears_alongside_the_shipped_ones(store):
    from dsr.rooms import list_templates

    store.create(
        "template",
        {
            "template_id": "tpl_brand",
            "template_version_id": "tpl_brand_v4",
            "name": "Brand Standard",
            "sections": ["overview"],
        },
    )

    ids = [template["template_id"] for template in list_templates(store)]

    assert "tpl_brand" in ids
    assert "tpl_standard" in ids


def test_a_store_template_can_override_a_shipped_one_without_a_redeploy(store):
    from dsr.rooms import find_template

    store.create(
        "template",
        {
            "template_id": "tpl_standard",
            "template_version_id": "tpl_standard_v9",
            "name": "Standard (retuned)",
        },
    )

    resolved = find_template(store, "tpl_standard")

    assert resolved["template_version_id"] == "tpl_standard_v9"
    assert resolved["source"] == "store"


def test_a_store_template_only_needs_a_template_id(store):
    from dsr.rooms import find_template

    store.create("template", {"template_id": "tpl_bare"})

    assert find_template(store, "tpl_bare")["template_version_id"] == "tpl_bare"


def test_a_room_can_be_created_from_a_store_supplied_template(store):
    store.create("template", {"template_id": "tpl_brand", "template_version_id": "v1"})

    room = create(store, template_id="tpl_brand")

    assert room["data"]["template_id"] == "tpl_brand"
    assert room["data"]["template_version_id"] == "v1"
    assert room["data"]["template_source"] == "store"


# -- the Rooms list ---------------------------------------------------------- #


def test_list_defaults_to_active_rooms_only(store):
    create(store, name="Live room")
    archived = create(store, name="Old room")
    store.update(archived["id"], {"status": "archived"})

    listed = list_rooms(store)

    assert [r["data"]["name"] for r in listed["rooms"]] == ["Live room"]
    assert listed["status"] == "active"


def test_list_can_show_archived_rooms(store):
    archived = create(store, name="Old room")
    store.update(archived["id"], {"status": "archived"})

    listed = list_rooms(store, status="archived")

    assert [r["id"] for r in listed["rooms"]] == [archived["id"]]


def test_list_can_show_every_status_at_once(store):
    create(store, name="Live room")
    archived = create(store, name="Old room")
    store.update(archived["id"], {"status": "archived"})

    assert list_rooms(store, status="all")["total"] == 2


def test_unknown_status_is_refused(store):
    with pytest.raises(RoomCreationError) as caught:
        list_rooms(store, status="paused")

    assert caught.value.code == "invalid_status"


def test_list_search_matches_name_friendly_url_and_account(store):
    account = make_account(store, "Contoso Health")
    create(store, name="Acme Evaluation", account=account, friendly_url="acme-eval")

    assert list_rooms(store, query="contoso")["total"] == 1
    assert list_rooms(store, query="acme-eval")["total"] == 1
    assert list_rooms(store, query="nothing here")["total"] == 0


def test_list_filters_by_account_and_template(store):
    northwind = make_account(store, "Northwind Traders")
    contoso = make_account(store, "Contoso Health")
    create(store, name="A", account=northwind)
    create(store, name="B", account=contoso, template_id="tpl_technical_review")

    assert [r["data"]["name"] for r in list_rooms(store, account_id=northwind["id"])["rooms"]] == ["A"]
    assert [
        r["data"]["name"] for r in list_rooms(store, template_id="tpl_technical_review")["rooms"]
    ] == ["B"]


def test_list_paginates(store):
    for index in range(3):
        create(store, name=f"Room {index}")

    first = list_rooms(store, limit=2, offset=0)
    second = list_rooms(store, limit=2, offset=2)

    assert first["count"] == 2 and first["total"] == 3
    assert second["count"] == 1
    assert {r["id"] for r in first["rooms"]}.isdisjoint({r["id"] for r in second["rooms"]})


# -- HTTP surface ------------------------------------------------------------ #


def test_post_rooms_creates_a_room_and_returns_it_with_its_site(client):
    account = client.post("/api/records/account", json={"name": "Northwind Traders"}).json()

    response = client.post(
        "/api/rooms",
        json={"name": "Acme Evaluation", "account_id": account["id"], "template_id": "tpl_standard"},
        params={"actor": "dana"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["collection"] == "room"
    assert body["data"]["account_id"] == account["id"]
    assert body["data"]["friendly_url"] == "acme-evaluation"
    assert body["site"]["id"] == body["data"]["site_id"]


def test_post_rooms_appears_in_the_rooms_list(client):
    account = client.post("/api/records/account", json={"name": "Northwind Traders"}).json()
    client.post(
        "/api/rooms",
        json={"name": "Acme Evaluation", "account_id": account["id"], "template_id": "tpl_standard"},
    )

    listed = client.get("/api/rooms").json()

    assert listed["count"] == 1
    assert listed["rooms"][0]["data"]["name"] == "Acme Evaluation"


def test_post_rooms_is_audited_with_the_actor(client):
    account = client.post("/api/records/account", json={"name": "Northwind Traders"}).json()
    room = client.post(
        "/api/rooms",
        json={"name": "Acme Evaluation", "account_id": account["id"], "template_id": "tpl_standard"},
        params={"actor": "dana"},
    ).json()

    entries = client.get("/api/audit", params={"record_id": room["id"]}).json()["entries"]

    assert len(entries) == 1
    assert entries[0]["action"] == "insert"
    assert entries[0]["actor"] == "dana"
    assert entries[0]["source"] == "POST /api/rooms"


def test_post_rooms_shares_one_request_id_across_both_audit_rows(client):
    account = client.post("/api/records/account", json={"name": "Northwind Traders"}).json()
    room = client.post(
        "/api/rooms",
        json={"name": "Acme Evaluation", "account_id": account["id"], "template_id": "tpl_standard"},
        params={"request_id": "req-42"},
    ).json()

    # The request id is how one submission's writes are read back as a unit.
    pair = client.get("/api/audit", params={"request_id": "req-42"}).json()["entries"]

    assert {e["collection"] for e in pair} == {"room", "site"}
    assert room["data"]["site_id"] in {e["record_id"] for e in pair}
    assert all(e["request_id"] == "req-42" for e in pair)


def test_post_rooms_stores_unknown_fields_without_a_migration(client):
    account = client.post("/api/records/account", json={"name": "Northwind Traders"}).json()

    room = client.post(
        "/api/rooms",
        json={
            "name": "Acme Evaluation",
            "account_id": account["id"],
            "template_id": "tpl_standard",
            "seats": 40,
            "branding": {"theme": "dark"},
        },
    ).json()

    assert room["data"]["seats"] == 40
    assert room["data"]["branding"] == {"theme": "dark"}
    # Filtered through the generic record route, which is the documented way to
    # query arbitrary JSON paths: /api/rooms only speaks the wizard's own filters.
    found = client.get("/api/records/room", params={"where": '{"branding.theme":"dark"}'}).json()
    assert [r["id"] for r in found["records"]] == [room["id"]]
    assert client.get("/api/records/room", params={"where": '{"branding.theme":"light"}'}).json()["count"] == 0


def test_post_rooms_with_unknown_account_is_404(client):
    response = client.post(
        "/api/rooms",
        json={"name": "Acme", "account_id": "account_missing", "template_id": "tpl_standard"},
    )

    assert response.status_code == 404
    assert response.json()["error"] == "account_not_found"


def test_post_rooms_with_unknown_template_is_400(client):
    account = client.post("/api/records/account", json={"name": "Northwind Traders"}).json()

    response = client.post(
        "/api/rooms",
        json={"name": "Acme", "account_id": account["id"], "template_id": "nope"},
    )

    assert response.status_code == 400
    assert response.json()["error"] == "template_not_found"


def test_post_rooms_with_missing_name_is_400(client):
    account = client.post("/api/records/account", json={"name": "Northwind Traders"}).json()

    response = client.post(
        "/api/rooms", json={"account_id": account["id"], "template_id": "tpl_standard"}
    )

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_name"


def test_post_rooms_with_taken_friendly_url_is_409(client):
    account = client.post("/api/records/account", json={"name": "Northwind Traders"}).json()
    body = {"name": "Acme", "account_id": account["id"], "template_id": "tpl_standard", "friendly_url": "acme"}
    client.post("/api/rooms", json=body)

    response = client.post("/api/rooms", json=body)

    assert response.status_code == 409
    assert response.json()["error"] == "friendly_url_taken"


def test_a_refused_creation_writes_nothing_at_all(client):
    account = client.post("/api/records/account", json={"name": "Northwind Traders"}).json()
    before = client.get("/api/stats").json()

    client.post(
        "/api/rooms",
        json={"name": "Acme", "account_id": "account_missing", "template_id": "tpl_standard"},
    )

    after = client.get("/api/stats").json()
    assert after["records"] == before["records"]
    assert after["audit_entries"] == before["audit_entries"]


def test_get_rooms_filters_by_status_and_query(client):
    account = client.post("/api/records/account", json={"name": "Northwind Traders"}).json()
    client.post(
        "/api/rooms",
        json={"name": "Acme Evaluation", "account_id": account["id"], "template_id": "tpl_standard"},
    )
    archived = client.post(
        "/api/rooms",
        json={"name": "Contoso Renewal", "account_id": account["id"], "template_id": "tpl_standard"},
    ).json()
    client.patch(f"/api/records/room/{archived['id']}", json={"status": "archived"})

    assert client.get("/api/rooms", params={"q": "contoso"}).json()["count"] == 0
    assert client.get("/api/rooms", params={"status": "archived"}).json()["count"] == 1
    assert client.get("/api/rooms", params={"status": "all"}).json()["count"] == 2


def test_get_rooms_rejects_an_unknown_status(client):
    response = client.get("/api/rooms", params={"status": "paused"})

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_status"


def test_room_templates_endpoint_lists_the_shipped_set(client):
    body = client.get("/api/room-templates").json()

    assert body["count"] >= 1
    assert any(t["template_id"] == "tpl_standard" for t in body["templates"])


def test_room_templates_endpoint_includes_store_templates(client):
    client.post(
        "/api/records/template",
        json={"template_id": "tpl_brand", "template_version_id": "v1", "name": "Brand Standard"},
    )

    body = client.get("/api/room-templates").json()

    assert "tpl_brand" in [t["template_id"] for t in body["templates"]]


def test_accounts_endpoint_returns_records_untouched(client):
    client.post("/api/records/account", json={"name": "Northwind", "tier": "enterprise", "seats": 500})

    body = client.get("/api/accounts").json()

    assert body["count"] == 1
    # Pass-through, not a projection: a team's own fields survive.
    assert body["accounts"][0]["data"] == {"name": "Northwind", "tier": "enterprise", "seats": 500}


def test_accounts_endpoint_searches_by_name_and_domain(client):
    client.post("/api/records/account", json={"name": "Northwind Traders"})
    client.post("/api/records/account", json={"name": "Contoso Health", "domain": "contoso.example"})

    assert client.get("/api/accounts", params={"q": "north"}).json()["count"] == 1
    assert client.get("/api/accounts", params={"q": "contoso.example"}).json()["count"] == 1


def test_wizard_step_one_requires_an_account_to_exist(client):
    """A fresh deployment has nothing to select, and says so rather than erroring."""
    body = client.get("/api/accounts").json()

    assert body == {"accounts": [], "count": 0, "total": 0}
