"""WF-014 through the HTTP surface.

The API is the integration surface other teams build against, so what is under
test here is the contract: the paths this feature owns, the statuses a refusal is
reported with, and the two rules a caller cannot guess - that ``live`` does not
mean open, and that ``Set Live`` does not clear a view cap.

Every write goes through the audited store, so the audit expectations below are
the product's guarantee rather than a side effect of this feature.

Note on the brief: it asked for ``test_access.py`` and ``test_access_api.py`` to
be brought across from a source branch. Those two files exist on ``main`` and
belong to WF-015 (``test_access.py`` imports ``dsr.access`` and its docstring says
"Unit tests for the WF-015 gate's pure logic"), and the source branch no longer
exists on this remote - ``origin`` carries only ``main``. Overwriting WF-015's
live, passing tests would have been the exact two-features-one-file collision this
plugin host exists to end, so WF-014's tests are named for WF-014 instead.
"""

from __future__ import annotations

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from dsr.api import app
from fastapi.testclient import TestClient

PREFIX = "/api/wf-014"
FEATURE_ID = "wf-014-access-controls"
COLLABORATOR = {"X-Role": "room_collaborator", "X-Actor": "dana"}
VIEWER = {"X-Role": "viewer", "X-Actor": "sam"}


@pytest.fixture()
def client(monkeypatch):
    import dsr.api as api_module

    tmp = tempfile.TemporaryDirectory()
    monkeypatch.setenv("DSR_DB_PATH", str(Path(tmp.name) / "wf014_api.db"))
    monkeypatch.setenv("DSR_AUDIT_DIR", str(Path(tmp.name) / "audit"))
    # Static mounts are import-time, so point the module at a missing directory
    # to keep these tests on the API rather than the built frontend.
    monkeypatch.setattr(api_module, "FRONTEND_DIST", Path(tmp.name) / "absent-frontend")
    with TestClient(app) as test_client:
        yield test_client
    tmp.cleanup()


def make_room(client, name="Northwind Evaluation"):
    return client.post("/api/records/room", json={"name": name, "status": "active"}).json()


def make_page(client, room_id, slug="overview", *, published=True, title="Overview"):
    """Create a page, publishing it the way ``dsr.pages`` records a publication.

    A page is published by carrying a ``published_revision_id``, so an unpublished
    one is simply left without it - which is exactly the "draft keeps the setting
    until you publish it" case.
    """
    page = client.post(
        "/api/records/page",
        json={"slug": slug, "title": title, "blocks": []},
        params={"room_id": room_id},
    ).json()
    if not published:
        return page
    revision = client.post(
        "/api/records/page_revision",
        json={"page_id": page["id"], "number": 1, "blocks": []},
        params={"room_id": room_id},
    ).json()
    # The core update route is keyed by collection *and* id.
    return client.patch(
        f"/api/records/page/{page['id']}",
        json={"published_revision_id": revision["id"]},
    ).json()


def set_expiry(client, page_id, **body):
    return client.put(f"{PREFIX}/pages/{page_id}/expiry", json=body, headers=COLLABORATOR)


def set_limit(client, page_id, **body):
    return client.put(f"{PREFIX}/pages/{page_id}/view-limit", json=body, headers=COLLABORATOR)


def record_view(client, page_id, viewer="alex@northwind.example"):
    return client.post(f"{PREFIX}/pages/{page_id}/views", json={"viewer": viewer})


def buyer(client, page_id, at=None):
    params = {"at": at} if at else None
    return client.get(f"{PREFIX}/pages/{page_id}/buyer", params=params).json()


def access(client, page_id):
    return client.get(f"{PREFIX}/pages/{page_id}/access").json()


# --------------------------------------------------------------------------- #
# Registration
# --------------------------------------------------------------------------- #


def test_the_feature_loads_with_no_failure(client):
    body = client.get("/api/features").json()
    assert body["failed_count"] == 0
    mine = [f for f in body["features"] if f["id"] == FEATURE_ID]
    assert len(mine) == 1
    assert mine[0]["prefix"] == PREFIX
    assert mine[0]["loaded"] is True


def test_every_route_this_feature_serves_is_under_its_own_prefix(client):
    body = client.get("/api/features").json()
    mine = next(f for f in body["features"] if f["id"] == FEATURE_ID)
    paths = {route["path"] for route in mine["routes"]}
    assert paths, "the feature mounts no routes"
    assert all(path.startswith(PREFIX) for path in paths), paths


def test_no_route_shadows_another_feature_or_a_core_route(client):
    """The host refuses a concrete-path collision at load time; this asserts the
    refusal did not happen and that the paths are the ones intended."""
    body = client.get("/api/features").json()
    assert body["failed_count"] == 0
    mine = next(f for f in body["features"] if f["id"] == FEATURE_ID)
    for route in mine["routes"]:
        assert route["path"].startswith(PREFIX)
        assert "/api/access" not in route["path"]


# --------------------------------------------------------------------------- #
# Vocabulary
# --------------------------------------------------------------------------- #


def test_the_vocabulary_states_the_rules_and_carries_their_sources(client):
    body = client.get(f"{PREFIX}/vocabulary").json()
    assert body["constraints"] == ["expiry", "view_limit"]
    assert body["independent"] is True
    assert body["expiry_warning_days"] == 7
    assert body["counted_by"] == "count_where"
    assert body["inferred"], "the gaps must be reported, not hidden"
    assert all(item.get("basis") for item in body["inferred"])


def test_the_vocabulary_reports_what_it_does_not_implement(client):
    """An unimplemented researched item is stated, not silently dropped."""
    body = client.get(f"{PREFIX}/vocabulary").json()
    items = {entry["item"] for entry in body["not_implemented"]}
    assert "password protection" in items
    assert all(entry["reason"] for entry in body["not_implemented"])


def test_the_vocabulary_needs_no_store_and_no_records(client):
    """Readable on a fresh database, so a reviewer can check the claim first."""
    body = client.get(f"{PREFIX}/vocabulary").json()
    assert body["access_statuses"]
    assert body["buyer_states"]


# --------------------------------------------------------------------------- #
# Configuring a window
# --------------------------------------------------------------------------- #


def test_a_page_with_no_configuration_is_a_draft_and_open_to_nobody(client):
    room = make_room(client)
    page = make_page(client, room["id"], published=False)
    body = access(client, page["id"])
    assert body["access_status"] == "draft"
    assert body["accessible"] is False


def test_putting_an_expiry_stamps_the_clock_and_reports_the_boundary(client):
    room = make_room(client)
    page = make_page(client, room["id"])

    body = set_expiry(client, page["id"], enabled=True, days=30).json()

    assert body["expiry"]["enabled"] is True
    assert body["expiry"]["days"] == 30
    assert body["expiry"]["starts_at"] is not None
    assert body["expiry"]["expires_at"].endswith("23:59:59.999999+00:00")
    assert body["access_status"] == "live"


def test_expiry_on_a_draft_leaves_the_clock_unstarted(client):
    """ "A draft page keeps the setting until you publish it." """
    room = make_room(client)
    page = make_page(client, room["id"], published=False)

    body = set_expiry(client, page["id"], enabled=True, days=14).json()

    assert body["expiry"]["enabled"] is True
    assert body["expiry"]["days"] == 14
    assert body["expiry"]["starts_at"] is None
    assert body["expiry"]["expires_at"] is None


def test_putting_a_cap_reports_the_count_it_will_enforce(client):
    room = make_room(client)
    page = make_page(client, room["id"])

    body = set_limit(client, page["id"], enabled=True, max_views=25).json()

    assert body["view_limit"]["max_views"] == 25
    assert body["view_limit"]["views"] == 0
    assert body["view_limit"]["views_remaining"] == 25
    assert body["view_limit"]["counted_by"] == "count_where"


@pytest.mark.parametrize("days", [0, -1, None, "abc"])
def test_an_unusable_day_count_is_a_400_with_a_readable_code(client, days):
    room = make_room(client)
    page = make_page(client, room["id"])
    response = set_expiry(client, page["id"], enabled=True, days=days)
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_access_window"


def test_an_unusable_cap_is_a_400(client):
    room = make_room(client)
    page = make_page(client, room["id"])
    response = set_limit(client, page["id"], enabled=True, max_views=0)
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_access_window"


def test_disabling_a_constraint_clears_its_numbers(client):
    room = make_room(client)
    page = make_page(client, room["id"])
    set_expiry(client, page["id"], enabled=True, days=30)
    set_limit(client, page["id"], enabled=True, max_views=5)

    body = client.delete(f"{PREFIX}/pages/{page['id']}/expiry", headers=COLLABORATOR).json()

    assert body["expiry"]["enabled"] is False
    assert body["expiry"]["days"] is None
    assert body["view_limit"]["max_views"] == 5


def test_an_unknown_page_is_a_404_not_a_500(client):
    response = client.get(f"{PREFIX}/pages/no-such-page/access")
    assert response.status_code == 404
    assert response.json()["error"] == "page_not_found"


def test_a_viewer_cannot_change_a_window(client):
    room = make_room(client)
    page = make_page(client, room["id"])
    response = client.put(
        f"{PREFIX}/pages/{page['id']}/expiry", json={"enabled": True, "days": 30}, headers=VIEWER
    )
    assert response.status_code == 403
    assert response.json()["error"] == "access_window_forbidden"


def test_the_page_is_told_which_actions_the_caller_has(client):
    """So the UI cannot offer a button the route would refuse."""
    room = make_room(client)
    page = make_page(client, room["id"])

    owner = client.get(f"{PREFIX}/pages/{page['id']}/access", headers=COLLABORATOR).json()
    viewer = client.get(f"{PREFIX}/pages/{page['id']}/access", headers=VIEWER).json()

    assert owner["available_actions"]["set_expiry"] is True
    assert viewer["available_actions"]["set_expiry"] is False
    assert all(value is False for value in viewer["available_actions"].values())


# --------------------------------------------------------------------------- #
# The cap, counted
# --------------------------------------------------------------------------- #


def test_a_cap_closes_the_link_on_the_view_that_meets_it(client):
    room = make_room(client)
    page = make_page(client, room["id"])
    set_limit(client, page["id"], enabled=True, max_views=2)

    assert record_view(client, page["id"]).json()["view_limit"]["capped"] is False
    assert buyer(client, page["id"])["show_content"] is True

    body = record_view(client, page["id"]).json()
    assert body["view_limit"]["capped"] is True
    assert body["accessible"] is False
    assert buyer(client, page["id"])["show_content"] is False


def test_a_capped_page_is_badged_view_limit_and_still_stored_as_published(client):
    """The documented rule, end to end.

    "The Page will retain a Live status, although it's been disabled by the view
    limit."
    """
    room = make_room(client)
    page = make_page(client, room["id"])
    set_limit(client, page["id"], enabled=True, max_views=1)
    record_view(client, page["id"])

    body = access(client, page["id"])

    assert body["access_status"] == "view_limit"
    assert body["stored_status"] == "published"
    assert body["accessible"] is False


def test_a_view_on_a_closed_link_is_a_409_and_is_not_counted(client):
    room = make_room(client)
    page = make_page(client, room["id"])
    set_limit(client, page["id"], enabled=True, max_views=1)
    record_view(client, page["id"])

    response = record_view(client, page["id"])
    assert response.status_code == 409
    assert response.json()["error"] == "access_window_conflict"
    assert access(client, page["id"])["view_limit"]["views"] == 1


def test_raising_a_cap_reopens_the_link_without_clearing_the_count(client):
    room = make_room(client)
    page = make_page(client, room["id"])
    set_limit(client, page["id"], enabled=True, max_views=1)
    record_view(client, page["id"])
    assert access(client, page["id"])["accessible"] is False

    set_limit(client, page["id"], enabled=True, max_views=5)

    body = access(client, page["id"])
    assert body["accessible"] is True
    assert body["view_limit"]["views"] == 1


# --------------------------------------------------------------------------- #
# Expiry, resolved at an instant
# --------------------------------------------------------------------------- #


def at_now(**delta):
    return (datetime.now(timezone.utc) + timedelta(**delta)).isoformat()


def test_the_link_is_open_before_the_boundary_and_closed_after_it(client):
    room = make_room(client)
    page = make_page(client, room["id"])
    set_expiry(client, page["id"], enabled=True, days=2)
    expires_at = access(client, page["id"])["expiry"]["expires_at"]

    assert buyer(client, page["id"], at=expires_at)["show_content"] is True
    after = (datetime.fromisoformat(expires_at) + timedelta(microseconds=1)).isoformat()
    assert buyer(client, page["id"], at=after)["show_content"] is False


def test_an_expired_link_is_badged_declined_with_nothing_declined_by_hand(client):
    room = make_room(client)
    page = make_page(client, room["id"])
    set_expiry(client, page["id"], enabled=True, days=1)

    body = client.get(f"{PREFIX}/pages/{page['id']}/access", params={"at": at_now(days=5)}).json()

    assert body["access_status"] == "declined"
    assert body["manual_status"] is None
    assert body["closed_by"] == ["expired"]


def test_a_link_inside_the_warning_horizon_warns_and_still_shows_content(client):
    room = make_room(client)
    page = make_page(client, room["id"])
    set_expiry(client, page["id"], enabled=True, days=30)

    view = client.get(f"{PREFIX}/pages/{page['id']}/buyer", params={"at": at_now(days=26)}).json()

    assert view["state"] == "expiring_soon"
    assert view["show_content"] is True
    assert view["warning"] is True
    assert view["message_placement"] == "bottom-left"
    assert view["message"]


def test_an_expired_link_replaces_the_content_with_an_error(client):
    room = make_room(client)
    page = make_page(client, room["id"])
    set_expiry(client, page["id"], enabled=True, days=1)

    view = client.get(f"{PREFIX}/pages/{page['id']}/buyer", params={"at": at_now(days=5)}).json()

    assert view["state"] == "expired"
    assert view["show_content"] is False
    assert view["error"] is True
    assert view["message_placement"] == "replaces-content"
    assert view["message"]


def test_a_closed_link_is_a_200_with_a_message_not_a_4xx(client):
    """A buyer who follows a dead link gets a page with a message on it."""
    room = make_room(client)
    page = make_page(client, room["id"])
    set_expiry(client, page["id"], enabled=True, days=1)

    response = client.get(f"{PREFIX}/pages/{page['id']}/buyer", params={"at": at_now(days=5)})
    assert response.status_code == 200


def test_an_unparseable_instant_is_a_400(client):
    room = make_room(client)
    page = make_page(client, room["id"])
    response = client.get(f"{PREFIX}/pages/{page['id']}/buyer", params={"at": "yesterday"})
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_access_window"


def test_expired_and_capped_both_reported_with_declined_the_badge(client):
    room = make_room(client)
    page = make_page(client, room["id"])
    set_expiry(client, page["id"], enabled=True, days=1)
    set_limit(client, page["id"], enabled=True, max_views=1)
    record_view(client, page["id"])

    body = client.get(f"{PREFIX}/pages/{page['id']}/access", params={"at": at_now(days=5)}).json()

    assert body["access_status"] == "declined"
    assert set(body["closed_by"]) == {"expired", "view_limit"}


# --------------------------------------------------------------------------- #
# Listing and filtering
# --------------------------------------------------------------------------- #


def test_pages_can_be_filtered_by_the_derived_badge(client):
    room = make_room(client)
    live = make_page(client, room["id"], slug="live")
    limited = make_page(client, room["id"], slug="limited")
    set_limit(client, limited["id"], enabled=True, max_views=1)
    record_view(client, limited["id"])

    body = client.get(f"{PREFIX}/pages", params={"status": "view_limit"}).json()

    assert [w["page_id"] for w in body["windows"]] == [limited["id"]]
    assert live["id"] not in [w["page_id"] for w in body["windows"]]


def test_pages_can_be_filtered_by_room(client):
    first = make_room(client, "Northwind")
    second = make_room(client, "Contoso")
    mine = make_page(client, first["id"], slug="mine")
    theirs = make_page(client, second["id"], slug="theirs")

    body = client.get(f"{PREFIX}/pages", params={"room_id": first["id"]}).json()

    assert [w["page_id"] for w in body["windows"]] == [mine["id"]]
    assert theirs["id"] not in [w["page_id"] for w in body["windows"]]


def test_a_room_can_be_read_as_a_whole_with_a_closed_count(client):
    room = make_room(client)
    make_page(client, room["id"], slug="open")
    limited = make_page(client, room["id"], slug="limited")
    set_limit(client, limited["id"], enabled=True, max_views=1)
    record_view(client, limited["id"])

    body = client.get(f"{PREFIX}/rooms/{room['id']}/access").json()

    assert body["count"] == 2
    assert body["closed"] == 1


def test_an_unknown_status_filter_is_a_400(client):
    response = client.get(f"{PREFIX}/pages", params={"status": "nonsense"})
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_access_window"


def test_an_unknown_room_is_a_404(client):
    response = client.get(f"{PREFIX}/rooms/no-such-room/access")
    assert response.status_code == 404


def test_the_summary_reports_closed_alongside_the_badges(client):
    """A dashboard of badges alone would read "one live, all fine" while that one
    page is closed to every buyer."""
    room = make_room(client)
    page = make_page(client, room["id"])
    set_limit(client, page["id"], enabled=True, max_views=1)
    record_view(client, page["id"])

    body = client.get(f"{PREFIX}/summary").json()

    assert body["total"] == 1
    assert body["by_status"]["view_limit"] == 1
    assert body["closed"] == 1
    assert body["open"] == 0


def test_the_summary_of_an_empty_database_is_zeroes_not_an_error(client):
    body = client.get(f"{PREFIX}/summary").json()
    assert body["total"] == 0
    assert body["closed"] == 0
    assert sum(body["by_status"].values()) == 0


# --------------------------------------------------------------------------- #
# Decline and Set Live
# --------------------------------------------------------------------------- #


def test_declining_by_hand_closes_the_link(client):
    room = make_room(client)
    page = make_page(client, room["id"])

    body = client.post(f"{PREFIX}/pages/{page['id']}/decline", headers=COLLABORATOR).json()

    assert body["access_status"] == "declined"
    assert body["manual_status"] == "declined"
    assert buyer(client, page["id"])["show_content"] is False


def test_set_live_reopens_a_hand_declined_link(client):
    room = make_room(client)
    page = make_page(client, room["id"])
    client.post(f"{PREFIX}/pages/{page['id']}/decline", headers=COLLABORATOR)

    client.post(f"{PREFIX}/pages/{page['id']}/set-live", headers=COLLABORATOR)

    assert access(client, page["id"])["accessible"] is True


def test_set_live_does_not_clear_the_view_limit(client):
    """The documented trap, over HTTP.

    "If you do that, the view limit setting will still in place, so you'll want to
    use the steps above to remove it."
    """
    room = make_room(client)
    page = make_page(client, room["id"])
    set_limit(client, page["id"], enabled=True, max_views=1)
    record_view(client, page["id"])
    client.post(f"{PREFIX}/pages/{page['id']}/decline", headers=COLLABORATOR)

    body = client.post(f"{PREFIX}/pages/{page['id']}/set-live", headers=COLLABORATOR).json()

    assert body["manual_status"] == "live"
    assert body["view_limit"]["enabled"] is True
    assert body["view_limit"]["max_views"] == 1
    assert body["accessible"] is False
    assert buyer(client, page["id"])["show_content"] is False


def test_removing_the_cap_is_what_reopens_a_view_limited_link(client):
    room = make_room(client)
    page = make_page(client, room["id"])
    set_limit(client, page["id"], enabled=True, max_views=1)
    record_view(client, page["id"])

    client.delete(f"{PREFIX}/pages/{page['id']}/view-limit", headers=COLLABORATOR)

    assert access(client, page["id"])["accessible"] is True


def test_set_live_restarts_the_expiry_clock(client):
    room = make_room(client)
    page = make_page(client, room["id"])
    set_expiry(client, page["id"], enabled=True, days=2)
    later = at_now(days=5)
    assert buyer(client, page["id"], at=later)["show_content"] is False

    client.post(f"{PREFIX}/pages/{page['id']}/set-live", headers=COLLABORATOR, params={"at": later})

    assert buyer(client, page["id"], at=later)["show_content"] is True


def test_a_viewer_cannot_revive_a_link(client):
    room = make_room(client)
    page = make_page(client, room["id"])
    response = client.post(f"{PREFIX}/pages/{page['id']}/set-live", headers=VIEWER)
    assert response.status_code == 403


# --------------------------------------------------------------------------- #
# Audit
# --------------------------------------------------------------------------- #


def test_configuring_a_window_is_audited(client):
    room = make_room(client)
    page = make_page(client, room["id"])
    set_expiry(client, page["id"], enabled=True, days=30)

    body = client.get("/api/audit", params={"record_id": page["id"]}).json()
    assert body["count"] >= 1
    assert any(entry["action"] == "update" for entry in body["entries"])


def test_a_recorded_view_is_audited(client):
    room = make_room(client)
    page = make_page(client, room["id"])
    record_view(client, page["id"], viewer="alex@northwind.example")

    rows = client.get("/api/records/page_view").json()
    assert rows["count"] == 1
    assert rows["records"][0]["data"]["viewer"] == "alex@northwind.example"
