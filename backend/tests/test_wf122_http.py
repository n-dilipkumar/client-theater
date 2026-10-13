"""WF-122 over HTTP: the routes the host actually mounted.

``test_wf122.py`` proves the rules. This file proves the wiring: that discovery
mounted the router, that each error type became the status code a client should
branch on, that a below-tier section is absent from the response rather than
hidden by a page, that the access link is exchanged for an HttpOnly cookie, and
that every audit row a write left behind names a concrete route the app serves.

That last one is the property the whole feature host exists to protect, so it is
asserted against the mounted route table rather than against a literal written in
the test. A literal would agree with a typo.

Each test file must pass on its own, because the suite runs under ``pytest-xdist``
and a test that only passes in one order fails intermittently. Nothing here
depends on another test's rows: every test builds its own room and its own rows.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from dsr.competitive_baseline import vocabulary as vocab
from dsr.competitive_baseline.errors import DisclosureRefused
from dsr.db.audited import RecordNotFound
from dsr.features import load_feature
from dsr.store import RecordStore
from fastapi.testclient import TestClient

MODULE = "wf122_tiered_disclosure"
PREFIX = "/api/wf-122"
FEATURE_ID = "wf-122-tiered-disclosure"


@pytest.fixture()
def feature():
    return load_feature(MODULE)


@pytest.fixture()
def live_store(db) -> RecordStore:
    """A store over the *same* database the ``client`` fixture serves.

    The shared ``store`` fixture is backed by ``memory_db``, while ``client``
    swaps ``app.state.store`` onto the file-backed ``db``. A fixture built from
    ``store`` and then asserted through ``client`` would read a database nothing
    wrote to. One database per test is the whole of the isolation this file needs.
    """
    return RecordStore(db)


@pytest.fixture()
def room_id(live_store: RecordStore) -> str:
    return live_store.create("room", {"name": "Northwind"}, actor="test", source="test")["id"]


def _section(client: TestClient, room_id: str, name: str, **extra: object) -> dict:
    response = client.post(f"{PREFIX}/rooms/{room_id}/sections", json={"name": name, **extra})
    assert response.status_code == 200, response.text
    return response.json()


def _recipient(client: TestClient, room_id: str, org: str = "Vantage", **extra: object) -> dict:
    response = client.post(f"{PREFIX}/rooms/{room_id}/recipients", json={"org": org, **extra})
    assert response.status_code == 200, response.text
    return response.json()


# --------------------------------------------------------------------------- #
# Discovery and mounting
# --------------------------------------------------------------------------- #


def test_discovery_mounted_this_feature_by_its_prefix(client: TestClient, feature):
    """The router was mounted by discovery alone. No shared file was edited."""
    listed = client.get("/api/features").json()
    found = [entry for entry in listed["features"] if entry["id"] == FEATURE_ID]
    assert found, "the feature registry did not list this feature"
    assert found[0]["prefix"] == PREFIX
    assert found[0]["loaded"] is True
    assert client.get(f"{PREFIX}/vocabulary").status_code == 200


def test_no_feature_failed_to_load_because_of_this_workflow(client: TestClient):
    """A broken feature is reported and skipped, so a failure here would be silent."""
    listed = client.get("/api/features").json()
    failures = [entry for entry in listed["failed"] if entry["module"].endswith(MODULE)]
    assert not failures, failures


def test_the_feature_maps_only_error_types_it_raises_itself(feature):
    """A handler for a builtin type would intercept that exception product-wide.

    All three types are declared by ``dsr.competitive_baseline.errors`` and
    raised by nothing else. None is a builtin, and the shared store's miss is not
    among them, so this feature's handlers cannot intercept another workflow's
    exception.
    """
    builtins_ = (ValueError, LookupError, PermissionError, KeyError, TypeError)
    for error_type in feature.EXCEPTION_HANDLERS:
        assert error_type.__module__ == "dsr.competitive_baseline.errors", error_type
        assert error_type not in builtins_, error_type
        assert error_type is not RecordNotFound, error_type
    assert len(feature.EXCEPTION_HANDLERS) == 3


def test_the_feature_reports_every_route_it_mounts(client: TestClient):
    """The registry is what ``tools/verify_all_routes.py`` walks.

    A route missing from it is a route nobody checks for a 5xx, so the set is
    asserted exactly: adding a route without listing it here, or listing one the
    feature does not serve, both fail.
    """
    shapes = client.get(f"/api/features/{FEATURE_ID}").json()["routes"]
    served = {f"{method} {shape['path']}" for shape in shapes for method in shape["methods"]}
    assert served == {
        f"GET {PREFIX}/summary",
        f"GET {PREFIX}/vocabulary",
        f"GET {PREFIX}/rooms/{{room_id}}/policy",
        f"PUT {PREFIX}/rooms/{{room_id}}/policy",
        f"GET {PREFIX}/rooms/{{room_id}}/sections",
        f"POST {PREFIX}/rooms/{{room_id}}/sections",
        f"GET {PREFIX}/sections/{{section_id}}",
        f"PATCH {PREFIX}/sections/{{section_id}}",
        f"DELETE {PREFIX}/sections/{{section_id}}",
        f"GET {PREFIX}/rooms/{{room_id}}/view",
        f"GET {PREFIX}/rooms/{{room_id}}/recipients",
        f"POST {PREFIX}/rooms/{{room_id}}/recipients",
        f"GET {PREFIX}/recipients/{{access_id}}",
        f"PATCH {PREFIX}/recipients/{{access_id}}",
        f"POST {PREFIX}/recipients/{{access_id}}/unlock",
        f"POST {PREFIX}/recipients/{{access_id}}/revoke",
        f"POST {PREFIX}/recipients/{{access_id}}/rotate",
        f"GET {PREFIX}/recipients/{{access_id}}/view",
        f"GET {PREFIX}/rooms/{{room_id}}/requests",
        f"GET {PREFIX}/rooms/{{room_id}}/events",
        f"POST {PREFIX}/sections/{{section_id}}/grant",
        f"POST {PREFIX}/sections/{{section_id}}/request",
        f"GET {PREFIX}/access/{{access_id}}",
        f"GET {PREFIX}/me",
    }


def test_the_module_never_imports_the_app_or_opens_sqlite(feature):
    """The enforced rule, restated so the reason is on the record for this workflow."""
    from pathlib import Path

    source = Path(feature.__file__).read_text(encoding="utf-8")
    assert "import sqlite3" not in source
    assert "from dsr.api" not in source
    assert "import dsr.api" not in source
    assert source.count("_source(") >= 12  # every write derives its audit source


# --------------------------------------------------------------------------- #
# The board
# --------------------------------------------------------------------------- #


def test_the_summary_route_reports_the_room_numbers(client: TestClient, room_id: str):
    _section(client, room_id, "Overview", min_tier=0)
    _recipient(client, room_id)
    body = client.get(f"{PREFIX}/summary", params={"room_id": room_id}).json()
    assert body["sections"] == 1
    assert body["recipients"] == 1
    assert body["active"] == 1


def test_the_vocabulary_route_publishes_the_issue_s_open_decisions(client: TestClient):
    """A reviewer can see each derivation without reading the source."""
    body = client.get(f"{PREFIX}/vocabulary").json()
    assert body["below_tier_states"] == list(vocab.BELOW_TIER_STATES)
    assert body["recipient_states"] == list(vocab.RECIPIENT_STATES)
    assert body["decisions"]["tier_model"].startswith("Tier numbers")
    assert any("CRM" in note for note in body["not_implemented"])


# --------------------------------------------------------------------------- #
# Errors become the status codes a client branches on
# --------------------------------------------------------------------------- #


def test_an_invalid_section_is_400_with_a_field_keyed_map(client: TestClient, room_id: str):
    response = client.post(f"{PREFIX}/rooms/{room_id}/sections", json={"min_tier": 1})
    assert response.status_code == 400, response.text
    body = response.json()
    assert body["error"] == "disclosure_invalid"
    assert "name" in body["errors"]


def test_an_unknown_section_is_404(client: TestClient):
    response = client.get(f"{PREFIX}/sections/wf122_section_absent")
    assert response.status_code == 404, response.text
    assert response.json()["error"] == "not_found"


def test_an_unknown_recipient_is_404(client: TestClient):
    response = client.get(f"{PREFIX}/recipients/wf122_recipient_absent")
    assert response.status_code == 404, response.text


def test_unlocking_a_revoked_link_is_409_with_a_reason(client: TestClient, room_id: str):
    """409, because nothing is malformed: the link's state refuses the change."""
    recipient = _recipient(client, room_id)
    assert client.post(f"{PREFIX}/recipients/{recipient['id']}/revoke").status_code == 200

    refused = client.post(f"{PREFIX}/recipients/{recipient['id']}/unlock", json={"tier": 2})
    assert refused.status_code == 409, refused.text
    body = refused.json()
    assert body["error"] == "disclosure_conflict"
    assert body["reason"] == DisclosureRefused.REASON_REVOKED


def test_a_write_that_does_not_read_back_is_409_not_a_success(
    client: TestClient, room_id: str, monkeypatch
):
    """The seed for the release-bar claim that every clear change is verified.

    The store's update is replaced with a no-op, so the write appears to succeed
    and the record does not change. The route must refuse to report success.
    """
    recipient = _recipient(client, room_id)
    monkeypatch.setattr(client.app.state.store, "update", lambda *args, **kwargs: None)

    response = client.post(f"{PREFIX}/recipients/{recipient['id']}/unlock", json={"tier": 2})
    assert response.status_code == 409, response.text
    assert response.json()["reason"] == DisclosureRefused.REASON_WRITE_NOT_VISIBLE


# --------------------------------------------------------------------------- #
# Tiered visibility
# --------------------------------------------------------------------------- #


def test_a_below_tier_section_is_a_teaser_not_a_greyed_row(client: TestClient, room_id: str):
    """The teaser text is returned and the contents are not, on the wire."""
    _section(
        client,
        room_id,
        "Financial statements",
        min_tier=1,
        below_tier=vocab.TEASER,
        teaser="Three years of statements.",
        summary="The real numbers.",
    )
    recipient = _recipient(client, room_id)

    view = client.get(f"{PREFIX}/recipients/{recipient['id']}/view").json()
    rows = {row["name"]: row for row in view["sections"]}
    assert rows["Financial statements"]["disclosure"] == vocab.TEASER
    assert rows["Financial statements"]["teaser"] == "Three years of statements."
    assert "summary" not in rows["Financial statements"]


def test_a_hidden_section_is_absent_from_the_response_entirely(client: TestClient, room_id: str):
    """Absent, not hidden by the page: a row the UI greys out still ships its name."""
    _section(client, room_id, "Security and compliance", min_tier=3, below_tier=vocab.HIDDEN)
    recipient = _recipient(client, room_id)

    response = client.get(f"{PREFIX}/recipients/{recipient['id']}/view")
    assert "Security and compliance" not in response.text
    assert response.json()["visible_count"] == 0


def test_unlocking_over_http_reveals_the_section(client: TestClient, room_id: str):
    _section(
        client,
        room_id,
        "Financial statements",
        min_tier=1,
        below_tier=vocab.TEASER,
        teaser="Later.",
    )
    recipient = _recipient(client, room_id)

    assert (
        client.post(f"{PREFIX}/recipients/{recipient['id']}/unlock", json={"tier": 1}).status_code
        == 200
    )
    rows = client.get(f"{PREFIX}/recipients/{recipient['id']}/view").json()["sections"]
    assert [row["disclosure"] for row in rows] == [vocab.FULL]


def test_a_revoked_link_gets_an_empty_room_over_http(client: TestClient, room_id: str):
    _section(client, room_id, "Overview", min_tier=0, below_tier=vocab.TEASER, teaser="Hi.")
    recipient = _recipient(client, room_id)
    client.post(f"{PREFIX}/recipients/{recipient['id']}/revoke")

    view = client.get(f"{PREFIX}/recipients/{recipient['id']}/view").json()
    assert view["sections"] == []
    assert view["visible_count"] == 0
    assert view["state"] == vocab.REVOKED_STATE


def test_a_seller_can_preview_any_tier(client: TestClient, room_id: str):
    """The preview proves the rule without a recipient, and omits hidden sections."""
    _section(client, room_id, "Overview", min_tier=0, order=1)
    _section(client, room_id, "Security", min_tier=3, below_tier=vocab.HIDDEN, order=2)

    at_zero = client.get(f"{PREFIX}/rooms/{room_id}/view", params={"tier": 0}).json()
    assert [row["name"] for row in at_zero["sections"]] == ["Overview"]
    assert at_zero["hidden_count"] == 1

    at_three = client.get(f"{PREFIX}/rooms/{room_id}/view", params={"tier": 3}).json()
    assert at_three["visible_count"] == 2


# --------------------------------------------------------------------------- #
# Section-level access and requests
# --------------------------------------------------------------------------- #


def test_a_section_grant_reveals_a_hidden_section_over_http(client: TestClient, room_id: str):
    section = _section(client, room_id, "Contracts and IP", min_tier=2, below_tier=vocab.ON_REQUEST)
    recipient = _recipient(client, room_id)

    granted = client.post(
        f"{PREFIX}/sections/{section['id']}/grant", json={"recipient_id": recipient["id"]}
    )
    assert granted.status_code == 200, granted.text

    rows = client.get(f"{PREFIX}/recipients/{recipient['id']}/view").json()["sections"]
    assert rows[0]["name"] == "Contracts and IP"
    assert rows[0]["disclosure"] == vocab.FULL


def test_a_request_is_recorded_and_listed_for_the_seller(client: TestClient, room_id: str):
    section = _section(client, room_id, "Legal", min_tier=2, below_tier=vocab.ON_REQUEST)
    recipient = _recipient(client, room_id)

    made = client.post(
        f"{PREFIX}/sections/{section['id']}/request",
        json={"recipient_id": recipient["id"], "note": "Requested at kickoff."},
    )
    assert made.status_code == 200, made.text
    assert made.json()["state"] == "pending"

    listed = client.get(f"{PREFIX}/rooms/{room_id}/requests").json()["requests"]
    assert [row["id"] for row in listed] == [made.json()["id"]]


# --------------------------------------------------------------------------- #
# The access link: one per organisation, exchanged for a cookie
# --------------------------------------------------------------------------- #


def test_the_link_is_issued_once_and_not_stored_in_readable_form(client: TestClient, room_id: str):
    created = _recipient(client, room_id)
    assert created["token"]
    assert created["access_path"].endswith(created["token"])

    stored = client.get(f"{PREFIX}/recipients/{created['id']}").json()
    assert "token" not in stored
    assert "token_hash" not in stored
    assert stored["token_hint"] == created["token"][-4:]


def test_redeeming_a_token_sets_an_httponly_cookie_and_redirects(client: TestClient, room_id: str):
    """The token never lives in the page URL, so it never reaches history."""
    created = _recipient(client, room_id)
    response = client.get(f"{PREFIX}/access/{created['token']}", follow_redirects=False)
    assert response.status_code == 303, response.text
    assert response.headers["location"] == "/"
    cookie = response.headers["set-cookie"].lower()
    assert f"{vocab.TOKEN_COOKIE}=" in cookie
    assert "httponly" in cookie
    assert "samesite=lax" in cookie


def test_an_unknown_token_is_refused_rather_than_redirected(client: TestClient):
    response = client.get(f"{PREFIX}/access/not-a-real-token", follow_redirects=False)
    assert response.status_code == 404, response.text


def test_me_reads_the_cookie_and_reports_what_that_link_may_see(client: TestClient, room_id: str):
    _section(client, room_id, "Overview", min_tier=0)
    created = _recipient(client, room_id)

    anonymous = client.get(f"{PREFIX}/me").json()
    assert anonymous["authenticated"] is False

    client.cookies.set(vocab.TOKEN_COOKIE, created["token"])
    try:
        identified = client.get(f"{PREFIX}/me").json()
    finally:
        client.cookies.delete(vocab.TOKEN_COOKIE)
    assert identified["authenticated"] is True
    assert identified["recipient"]["org"] == "Vantage"
    assert identified["viewer_tier"] == 0
    assert [row["name"] for row in identified["sections"]] == ["Overview"]


def test_rotating_over_http_invalidates_the_old_token(client: TestClient, room_id: str):
    created = _recipient(client, room_id)
    old = created["token"]
    rotated = client.post(f"{PREFIX}/recipients/{created['id']}/rotate").json()
    assert rotated["token"] != old
    assert client.get(f"{PREFIX}/access/{old}", follow_redirects=False).status_code == 404
    assert (
        client.get(f"{PREFIX}/access/{rotated['token']}", follow_redirects=False).status_code == 303
    )


def test_an_expired_link_is_reported_and_shows_nothing(client: TestClient, room_id: str):
    _section(client, room_id, "Overview", min_tier=0)
    # The route uses the real clock, so set an expiry that is already past it.
    past = datetime.now(timezone.utc) - timedelta(days=1)
    created = _recipient(client, room_id, expires_at=past.isoformat())
    view = client.get(f"{PREFIX}/recipients/{created['id']}/view").json()
    assert view["state"] == vocab.EXPIRED_STATE
    assert view["sections"] == []


# --------------------------------------------------------------------------- #
# Policy and edits
# --------------------------------------------------------------------------- #


def test_the_policy_route_writes_and_reads_back_a_ladder(client: TestClient, room_id: str):
    written = client.put(
        f"{PREFIX}/rooms/{room_id}/policy",
        json={"tier_labels": {"1": "Financials"}, "default_below_tier": vocab.ON_REQUEST},
    )
    assert written.status_code == 200, written.text
    assert written.json()["tier_labels"] == {"1": "Financials"}
    assert (
        client.get(f"{PREFIX}/rooms/{room_id}/policy").json()["default_below_tier"]
        == vocab.ON_REQUEST
    )


def test_a_generic_patch_cannot_revive_a_link(client: TestClient, room_id: str):
    created = _recipient(client, room_id)
    client.post(f"{PREFIX}/recipients/{created['id']}/revoke")
    client.patch(
        f"{PREFIX}/recipients/{created['id']}",
        json={"org": "Vantage", "state": vocab.ACTIVE_STATE},
    )
    assert client.get(f"{PREFIX}/recipients/{created['id']}").json()["state"] == vocab.REVOKED_STATE


def test_deleting_a_section_removes_it_from_the_tree(client: TestClient, room_id: str):
    section = _section(client, room_id, "Overview", min_tier=0)
    assert client.delete(f"{PREFIX}/sections/{section['id']}").status_code == 200
    assert client.get(f"{PREFIX}/sections/{section['id']}").status_code == 404
    assert client.get(f"{PREFIX}/rooms/{room_id}/sections").json()["sections"] == []


# --------------------------------------------------------------------------- #
# Every write's audit row names a route the app actually serves
# --------------------------------------------------------------------------- #


def test_every_audit_row_this_feature_wrote_names_a_route_the_app_serves(
    client: TestClient, live_store: RecordStore, room_id: str
):
    """The property the feature host exists to protect.

    Asserted against the mounted route table rather than a literal, because a
    literal in the test would agree with a typo in the module. Served paths come
    from the feature registry, which is also what ``tools/verify_all_routes.py``
    walks.
    """
    served = {
        (method, shape["path"])
        for shape in client.get(f"/api/features/{FEATURE_ID}").json()["routes"]
        for method in shape["methods"]
    }
    assert served, "the registry reports no route for this feature"

    # Drive one write of every kind this feature makes.
    client.put(f"{PREFIX}/rooms/{room_id}/policy", json={"tier_labels": {"1": "Financials"}})
    section = _section(client, room_id, "Overview", min_tier=0)
    hidden = _section(client, room_id, "Security", min_tier=3, below_tier=vocab.HIDDEN)
    recipient = _recipient(client, room_id)
    client.patch(f"{PREFIX}/recipients/{recipient['id']}", json={"tier": 0})
    client.patch(f"{PREFIX}/sections/{section['id']}", json={"name": "Overview renamed"})
    client.post(f"{PREFIX}/recipients/{recipient['id']}/unlock", json={"tier": 1})
    client.post(f"{PREFIX}/sections/{hidden['id']}/grant", json={"recipient_id": recipient["id"]})
    client.post(f"{PREFIX}/sections/{hidden['id']}/request", json={"recipient_id": recipient["id"]})
    client.post(f"{PREFIX}/recipients/{recipient['id']}/rotate")
    client.post(f"{PREFIX}/recipients/{recipient['id']}/revoke")
    client.delete(f"{PREFIX}/sections/{hidden['id']}")

    rows = [
        row for row in live_store.db.audit() if str(row.get("collection") or "").startswith("wf122")
    ]
    assert rows, "no audit rows were written for this feature's collections"

    checked = 0
    for row in rows:
        source = str(row.get("source") or "")
        if not source.startswith(("POST ", "PATCH ", "PUT ", "DELETE ")):
            # A write made by the seeder or by a test fixture. Not a route, and
            # correctly so: nothing served it.
            continue
        method, _, path = source.partition(" ")
        assert (method, path) in served, f"audit row names a route the app does not serve: {source}"
        checked += 1

    assert checked >= 10, f"only {checked} route-sourced audit rows were checked"
