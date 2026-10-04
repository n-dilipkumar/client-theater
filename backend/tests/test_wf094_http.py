"""HTTP tests for WF-094: publish and share a quote.

These drive the mounted app through ``TestClient`` rather than calling the
service, so they exercise the same path a real client does. If the feature failed
to register, or collided with another feature's route, every test here would 404,
which is the point: the router is mounted by discovery alone and
``backend/dsr/api.py`` is not edited.

Each test names the rule it protects, quoting
``docs/research/digital-sales-room-workflows/wf/WF-094.md``.

The database is a temporary file per test, set through ``DSR_DB_PATH``, which
:mod:`dsr.deps` reads at call time. Nothing here depends on a row another test
wrote, so the file passes alone and under pytest-xdist in any order.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from dsr.api import app
from fastapi.testclient import TestClient

PREFIX = "/api/WF-094"


@pytest.fixture()
def client(monkeypatch):
    """A client over the mounted app, on its own temporary database."""
    tmp = tempfile.TemporaryDirectory()
    monkeypatch.setenv("DSR_DB_PATH", str(Path(tmp.name) / "wf094_http.db"))
    monkeypatch.setenv("DSR_AUDIT_DIR", str(Path(tmp.name) / "audit"))
    monkeypatch.setattr("dsr.api.FRONTEND_DIST", Path(tmp.name) / "absent-frontend")
    with TestClient(app) as test_client:
        yield test_client
    tmp.cleanup()


def make_quote(client, **overrides):
    """A draft quote in room_1, created through the generic records endpoint."""
    payload = {
        "title": "Northwind renewal",
        "status": "DRAFT",
        "quote_number": "Q-2026-014",
        "line_items": [{"quantity": 2, "price": 1200}, {"quantity": 1, "price": 450}],
    }
    payload.update(overrides)
    response = client.post("/api/records/quote", json=payload, params={"room_id": "room_1"})
    assert response.status_code == 201, response.text
    return response.json()


def publish(client, quote_id, **body):
    return client.post(f"{PREFIX}/quotes/{quote_id}/publish", json=body)


# --------------------------------------------------------------------------- #
# Discovery and vocabulary
# --------------------------------------------------------------------------- #


def test_the_router_is_mounted_by_discovery(client):
    """The feature registers by adding a file. api.py is not edited.

    A 404 here would mean the host did not mount this feature, which is the one
    failure mode a plugin host has that an edit to a shared file cannot have.
    """
    response = client.get(f"{PREFIX}/summary")
    assert response.status_code == 200
    assert "quotes" in response.json()


def test_the_feature_appears_in_the_registry(client):
    """The feature is listed as loaded, not as failed."""
    response = client.get("/api/features")
    assert response.status_code == 200
    ids = [f.get("id") for f in response.json().get("features", [])]
    assert "wf-094-publish-quote" in ids


def test_the_vocabulary_serves_the_caps_the_rules_enforce(client):
    """The panel renders the caps from here so it cannot disagree with the rules.

    The Cc cap and the 20 MB attachment cap are both here, because the page has
    to show a seller the same numbers the rules refuse against.
    """
    payload = client.get(f"{PREFIX}/vocabulary").json()

    assert payload["limits"]["cc"] == 9
    assert payload["limits"]["email_attachment_cap_bytes"] == 20 * 1024 * 1024
    assert set(payload["unlock_targets"]) == {"DRAFT", "PENDING_APPROVAL", "REJECTED"}
    assert "Quote published" in payload["activities"]
    assert "Quote sent" in payload["activities"]


# --------------------------------------------------------------------------- #
# Publishing
# --------------------------------------------------------------------------- #


def test_publishing_returns_the_computed_link_and_the_frozen_total(client):
    """ "on publish the platform computes read-only properties hs_quote_link
    (public URL), hs_domain, hs_slug, hs_locked=true ..."."""
    quote = make_quote(client)

    response = publish(client, quote["id"])
    assert response.status_code == 200
    data = response.json()["data"]

    assert data["status"] == "PUBLISHED"
    assert data["hs_locked"] is True
    assert data["hs_quote_amount"] == 2850.0
    assert data["hs_quote_link"].endswith("/q-2026-014")
    assert data["hs_slug"] == "q-2026-014"


def test_a_caller_supplied_link_is_refused_rather_than_ignored(client):
    """ "hs_quote_link - The quote's publicly accessible URL. This is a read-only
    property and cannot be set through the API after publishing."

    A body that carries a link is refused, not quietly dropped. A caller who sets
    a link and watches the publish succeed with a different one has been told the
    wrong thing.
    """
    quote = make_quote(client)

    response = publish(client, quote["id"], hs_quote_link="https://evil.example/steal")
    assert response.status_code == 422
    assert "computed on publish" in response.json()["detail"]


@pytest.mark.parametrize("key", ["hs_slug", "hs_domain", "public_link"])
def test_every_computed_property_is_refused_from_a_body(client, key):
    """The computed set is the researched one, and none of it is settable."""
    quote = make_quote(client)

    response = publish(client, quote["id"], **{key: "attacker-supplied"})
    assert response.status_code == 422
    assert key in response.json()["detail"]


def test_sharing_without_publishing_freezes_nothing(client):
    """ "when you click Share, the quote moves to a Shared status, even if it
    hasn't been sent to the buyer."

    Shared is not published, so nothing about the total is locked.
    """
    quote = make_quote(client)

    data = publish(client, quote["id"], shared_only=True).json()["data"]
    assert data["status"] == "SHARED"
    assert data["hs_locked"] is False
    assert "hs_quote_amount" not in data


def test_a_shared_quote_can_then_be_published(client):
    """Sharing is a first step. Jev chose this edge (audit
    jev-20261004T215726-29100-46936, pass, 0.92) over a terminal SHARED."""
    quote = make_quote(client)
    publish(client, quote["id"], shared_only=True)

    response = publish(client, quote["id"])
    assert response.status_code == 200
    assert response.json()["data"]["hs_locked"] is True


def test_publishing_a_published_quote_is_a_conflict_not_a_repeat(client):
    """A repeated publish would re-freeze a total and report work that did not happen."""
    quote = make_quote(client)
    publish(client, quote["id"])

    response = publish(client, quote["id"])
    assert response.status_code == 422
    assert "cannot be published" in response.json()["detail"]


def test_publishing_an_unknown_quote_is_a_refusal_naming_the_id(client):
    """A 404-shaped answer would hide a caller's own bug behind a missing page."""
    response = publish(client, "quote_does_not_exist")
    assert response.status_code == 422
    assert "not found" in response.json()["detail"]


def test_the_link_survives_a_round_trip_through_the_get_route(client):
    """The link a seller copied is the link a later read returns.

    If these disagreed, a seller would hand a buyer a URL that resolves to
    something else.
    """
    quote = make_quote(client)
    published = publish(client, quote["id"]).json()["data"]

    fetched = client.get(f"{PREFIX}/quotes/{quote['id']}").json()["data"]
    assert fetched["hs_quote_link"] == published["hs_quote_link"]
    assert fetched["hs_quote_amount"] == published["hs_quote_amount"]


# --------------------------------------------------------------------------- #
# Unlocking
# --------------------------------------------------------------------------- #


def test_unlocking_with_a_named_target_releases_the_lock(client):
    """ "To modify any properties after you've published a quote, you must first
    update the hs_status of the quote back to DRAFT..."."""
    quote = make_quote(client)
    publish(client, quote["id"])

    response = client.post(f"{PREFIX}/quotes/{quote['id']}/unlock", json={"target": "DRAFT"})
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "DRAFT"
    assert data["hs_locked"] is False


def test_unlocking_to_a_frozen_status_is_refused(client):
    """An unlock aimed at PUBLISHED does not unlock, so it is refused."""
    quote = make_quote(client)
    publish(client, quote["id"])

    response = client.post(f"{PREFIX}/quotes/{quote['id']}/unlock", json={"target": "PUBLISHED"})
    assert response.status_code == 422
    assert "an unlock target must be one of" in response.json()["detail"]


def test_unlocking_without_a_target_names_the_three_that_work(client):
    """An omitted target is a refusal that says what is allowed."""
    quote = make_quote(client)
    publish(client, quote["id"])

    response = client.post(f"{PREFIX}/quotes/{quote['id']}/unlock", json={})
    assert response.status_code == 422
    detail = response.json()["detail"]
    for target in ("DRAFT", "PENDING_APPROVAL", "REJECTED"):
        assert target in detail


# --------------------------------------------------------------------------- #
# Sharing
# --------------------------------------------------------------------------- #


def test_copying_a_link_records_it_and_returns_it(client):
    """ "in the Copy link, download PDF tab click Copy link"."""
    quote = make_quote(client)
    published = publish(client, quote["id"]).json()["data"]

    response = client.post(f"{PREFIX}/quotes/{quote['id']}/link", json={"actor": "dana"})
    assert response.status_code == 200
    assert response.json()["hs_quote_link"] == published["hs_quote_link"]


def test_copying_a_link_on_an_unpublished_quote_is_refused(client):
    """The one case that would hand a seller a URL that does not resolve."""
    quote = make_quote(client)

    response = client.post(f"{PREFIX}/quotes/{quote['id']}/link", json={})
    assert response.status_code == 422
    assert "no public link" in response.json()["detail"]


def test_an_oversize_pdf_email_sends_without_the_attachment(client):
    """ "HubSpot doesn't attach the generated quote PDF if it's larger than 20 MB."

    The email still sends. The refusal is not the email.
    """
    quote = make_quote(client)
    publish(client, quote["id"])

    response = client.post(
        f"{PREFIX}/quotes/{quote['id']}/emails",
        json={"to": ["buyer@example.com"], "pdf_size_bytes": 21 * 1024 * 1024},
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["pdf_attached"] is False
    assert data["attachment_note"]


def test_a_tenth_cc_address_is_refused_over_http(client):
    """**Cc** up to nine addresses. The refusal happens before any record is written."""
    quote = make_quote(client)
    publish(client, quote["id"])

    response = client.post(
        f"{PREFIX}/quotes/{quote['id']}/emails",
        json={"to": ["buyer@example.com"], "cc": [f"c{i}@example.com" for i in range(10)]},
    )
    assert response.status_code == 422
    assert "at most 9 Cc addresses" in response.json()["detail"]


def test_a_refused_email_writes_no_activity(client):
    """A refused send must not leave a sent activity behind.

    An activity row for an email that was never sent is exactly the kind of drift
    between the log and the work that this product's audit guarantee forbids.
    """
    quote = make_quote(client)
    publish(client, quote["id"])

    client.post(
        f"{PREFIX}/quotes/{quote['id']}/emails",
        json={"to": ["buyer@example.com"], "cc": [f"c{i}@example.com" for i in range(10)]},
    )
    activity = client.get(f"{PREFIX}/quotes/{quote['id']}/activity").json()
    assert all(entry["data"]["activity"] != "Quote sent" for entry in activity["entries"])


def test_a_sent_email_appears_once_in_the_activity_stream(client):
    """The email is the Quote sent activity, recorded once."""
    quote = make_quote(client)
    publish(client, quote["id"])
    client.post(f"{PREFIX}/quotes/{quote['id']}/emails", json={"to": ["buyer@example.com"]})

    activity = client.get(f"{PREFIX}/quotes/{quote['id']}/activity").json()
    labels = [entry["data"]["activity"] for entry in activity["entries"]]
    assert labels.count("Quote sent") == 1
    assert labels.count("Quote published") == 1


def test_a_pdf_request_names_the_documented_location(client):
    """ "the generated PDF file is always saved to the default location:
    ``<record_name>_<record_id>``"."""
    quote = make_quote(client)

    response = client.post(f"{PREFIX}/quotes/{quote['id']}/pdf", json={})
    assert response.status_code == 200
    assert response.json()["location"] == "northwind-renewal_" + quote["id"]


# --------------------------------------------------------------------------- #
# Listing and settings
# --------------------------------------------------------------------------- #


def test_quotes_can_be_listed_and_filtered_by_status(client):
    """The index page lists every quote and filters on the researched status."""
    draft = make_quote(client, quote_number="Q-2026-100")
    published = make_quote(client, quote_number="Q-2026-101")
    publish(client, published["id"])

    everything = client.get(f"{PREFIX}/quotes", params={"room_id": "room_1"}).json()
    assert everything["count"] == 2

    only_drafts = client.get(
        f"{PREFIX}/quotes", params={"room_id": "room_1", "status": "DRAFT"}
    ).json()
    assert [entry["id"] for entry in only_drafts["entries"]] == [draft["id"]]


def test_the_summary_counts_the_states_a_seller_needs(client):
    """A seller opening the page needs the published and sent counts at a glance."""
    published = make_quote(client, quote_number="Q-2026-201")
    publish(client, published["id"])
    client.post(f"{PREFIX}/quotes/{published['id']}/emails", json={"to": ["buyer@example.com"]})
    make_quote(client, quote_number="Q-2026-202")

    summary = client.get(f"{PREFIX}/summary", params={"room_id": "room_1"}).json()
    assert summary["quotes"] == 2
    assert summary["published"] == 1
    assert summary["sent"] == 1
    assert summary["by_status"]["DRAFT"] == 1


def test_the_settings_report_whether_the_domain_was_a_fallback(client):
    """ "By default, quotes are hosted on the landing page primary domain connected
    to your account." A workspace with none connected must say so.

    A link served from the fallback looks identical to a real one to a reader,
    so the flag is what tells them which happened.
    """
    settings = client.get(f"{PREFIX}/settings", params={"room_id": "room_1"}).json()
    assert settings["domain_configured"] is False
    assert settings["domain_fallback"] is True


def test_a_configured_domain_is_reported_as_configured(client):
    """The flag is false only when the workspace connected a real domain."""
    client.post(
        "/api/records/quote_settings",
        json={"domain": "billing.northwind.example"},
        params={"room_id": "room_1"},
    )

    settings = client.get(f"{PREFIX}/settings", params={"room_id": "room_1"}).json()
    assert settings["domain_configured"] is True
    assert settings["domain"] == "billing.northwind.example"


# --------------------------------------------------------------------------- #
# The audit guarantee, over HTTP
# --------------------------------------------------------------------------- #


def test_the_audit_row_names_the_route_that_served_the_write(client):
    """The audit row must name a URL the app actually serves.

    This is what the port brief calls out: a fixed label written into the write
    path can drift away from the route table, and then the log describes a
    request nobody can make.
    """
    quote = make_quote(client)
    publish(client, quote["id"], shared_only=True)

    audit = client.get("/api/audit", params={"record_id": quote["id"]}).json()
    sources = [entry["source"] for entry in audit["entries"]]
    assert f"POST {PREFIX}/quotes/{quote['id']}/publish" in sources


def test_every_advertised_route_answers_without_a_5xx(client):
    """No route this feature mounts may answer 5xx on the ordinary paths.

    ``tools/verify_all_routes.py`` calls each route with no arguments. A path
    parameter with no id behind it must still answer 4xx, because a 500 there
    means the route is mounted and broken.
    """
    paths = [
        "/summary",
        "/vocabulary",
        "/settings",
        "/quotes",
        "/quotes/quote_absent",
        "/quotes/quote_absent/activity",
    ]
    for path in paths:
        response = client.get(f"{PREFIX}{path}")
        assert response.status_code < 500, f"{path} answered {response.status_code}"
