"""WF-015 behaviour through the HTTP surface.

The API is the integration surface other teams build against, so the contract
under test is the endpoints: the tier a seller configures, the shape of the
answer a buyer gets, and the audit rows a click leaves behind.
"""

from __future__ import annotations

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from dsr.api import app

BUYER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Chrome/120.0 Safari/537.36"
SCANNER_AGENT = "Microsoft Outlook preview scanner"


@pytest.fixture()
def client(monkeypatch):
    import dsr.api as api_module

    tmp = tempfile.TemporaryDirectory()
    monkeypatch.setenv("DSR_DB_PATH", str(Path(tmp.name) / "api.db"))
    monkeypatch.setenv("DSR_AUDIT_DIR", str(Path(tmp.name) / "audit"))
    # The link a buyer follows must be stable for the round-trip test, so pin
    # the public base rather than inheriting the test client's host.
    monkeypatch.setenv("DSR_PUBLIC_URL", "http://salesroom.test")
    # Static mounts are import-time, so point the module at a missing directory
    # to keep these tests focused on the API rather than the built frontend.
    monkeypatch.setattr(api_module, "FRONTEND_DIST", Path(tmp.name) / "absent-frontend")
    with TestClient(app) as test_client:
        yield test_client
    tmp.cleanup()


# -- helpers ----------------------------------------------------------------- #


def make_room(client, name="Northwind Evaluation", **extra):
    payload = {"name": name, "account": "Northwind Traders", **extra}
    return client.post("/api/records/room", json=payload).json()


def set_policy(client, room_id, **policy):
    return client.put(f"/api/rooms/{room_id}/access", json=policy)


def submit(client, room_id, email="alex@northwind.example", name="Alex Buyer", **extra):
    body = {"name": name, "email": email, **extra}
    return client.post(f"/api/rooms/{room_id}/access/sessions", json=body)


def token_from(link: str) -> str:
    """Read the token out of a verification link.

    The round-trip tests deliberately use this rather than a hand-built token,
    so the delivery seam cannot drift away from the flow it is supposed to serve.
    """
    return parse_qs(urlparse(link).query)["token"][0]


def sessions(client, room_id, **params):
    return client.get(f"/api/rooms/{room_id}/access/sessions", params=params).json()


def outbox(client, room_id):
    return client.get(f"/api/rooms/{room_id}/access/outbox").json()["messages"]


def count(client, collection):
    return client.get(f"/api/records/{collection}").json()["count"]


def past_iso(**delta):
    return (datetime.now(timezone.utc) - timedelta(**delta)).isoformat(timespec="milliseconds")


# -- configuration ----------------------------------------------------------- #


def test_policy_defaults_to_open_and_reports_the_level(client):
    room = make_room(client)

    body = client.get(f"/api/rooms/{room['id']}/access").json()

    assert body["policy"]["mode"] == "open"
    assert body["level"] == "default"
    assert body["fields"] == []


def test_put_normalises_the_allowlist_and_audits_the_change(client):
    room = make_room(client)

    response = set_policy(
        client,
        room["id"],
        mode="verify_email",
        collect_name=True,
        collect_email=True,
        domain_security=True,
        allowed_domains="Northwind.example, @contoso.example",
    )

    assert response.status_code == 200
    assert response.json()["policy"]["allowed_domains"] == ["northwind.example", "contoso.example"]
    stored = client.get(f"/api/rooms/{room['id']}/access").json()
    assert stored["policy"]["mode"] == "verify_email"
    assert stored["level"] == "room"
    assert client.get("/api/audit", params={"collection": "access_policy"}).json()["count"] == 1


def test_repeated_put_updates_one_record_rather_than_accumulating(client):
    room = make_room(client)
    set_policy(client, room["id"], mode="identify", collect_email=True)
    second = set_policy(client, room["id"], mode="identify", collect_name=True)

    assert second.json()["revision"] == 2
    assert count(client, "access_policy") == 1


def test_invalid_policy_is_400_with_a_field_keyed_error_map(client):
    room = make_room(client)

    response = set_policy(
        client, room["id"], mode="identify", collect_email=True, domain_security=True,
        allowed_domains="northwind.example",
    )

    assert response.status_code == 400
    body = response.json()
    assert body["error"] == "policy_invalid"
    assert "domain_security" in body["errors"]


def test_policy_is_reachable_through_the_generic_records_api(client):
    """It is a record, so the generic surface must see it too."""
    room = make_room(client)
    set_policy(client, room["id"], mode="identify", collect_email=True)

    listed = client.get("/api/records/access_policy", params={"where": "subject_kind=room"}).json()
    assert listed["count"] == 1
    assert listed["records"][0]["data"]["mode"] == "identify"


def test_unknown_field_survives_the_api_round_trip(client):
    """Schema flexibility: a team adds its own field with no migration."""
    room = make_room(client)
    set_policy(client, room["id"], mode="open", legal_footer="Confidential", scoring={"w": 0.4})

    policy = client.get(f"/api/rooms/{room['id']}/access").json()["policy"]
    assert policy["legal_footer"] == "Confidential"
    assert policy["scoring"] == {"w": 0.4}


def test_policy_on_a_missing_room_is_404(client):
    assert client.get("/api/rooms/room_nope/access").status_code == 404
    assert set_policy(client, "room_nope", mode="open").status_code == 404


# -- requirements: what the buyer is told ------------------------------------ #


def test_requirements_reflect_the_tier(client):
    room = make_room(client)
    set_policy(
        client, room["id"], mode="verify_email", collect_name=True, collect_email=True,
        domain_security=True, allowed_domains="northwind.example",
    )

    body = client.get(f"/api/rooms/{room['id']}/access/requirements").json()

    assert body["fields"] == ["name", "email"]
    assert body["requires_verification"] is True
    assert body["allows_account_login"] is True
    assert body["domain_security"] is True


def test_requirements_never_expose_the_allowlist(client):
    """A form that says which domains pass is a free allowlist oracle."""
    room = make_room(client)
    set_policy(
        client, room["id"], mode="verify_email", collect_email=True, domain_security=True,
        allowed_domains="northwind.example",
    )

    body = client.get(f"/api/rooms/{room['id']}/access/requirements").json()

    assert "northwind.example" not in str(body)
    assert "allowed_domains" not in body


# -- the open tier ----------------------------------------------------------- #


def test_open_room_grants_and_records_nothing(client):
    room = make_room(client)

    response = submit(client, room["id"])

    assert response.status_code == 201
    assert response.json()["status"] == "granted"
    assert response.json()["token"] is None
    assert count(client, "access_session") == 0
    assert count(client, "verification_outbox") == 0


# -- the identify tier ------------------------------------------------------- #


def test_identify_records_the_identity_without_sending_mail(client):
    room = make_room(client)
    set_policy(client, room["id"], mode="identify", collect_name=True, collect_email=True)

    response = submit(client, room["id"])

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "granted"
    assert body["identity"]["email_domain"] == "northwind.example"
    assert count(client, "verification_outbox") == 0

    listing = sessions(client, room["id"])
    assert listing["by_status"] == {"identified": 1}


def test_identify_grant_emits_an_unverified_activity_row(client):
    room = make_room(client)
    set_policy(client, room["id"], mode="identify", collect_email=True)

    granted = submit(client, room["id"]).json()
    client.get(f"/api/rooms/{room['id']}/access/session", params={"token": granted["token"]})

    activity = client.get("/api/records/activity").json()["records"]
    assert len(activity) == 1
    assert activity[0]["data"]["identity_verified"] is False
    assert activity[0]["data"]["person"] == "alex@northwind.example"


def test_account_login_is_recorded_as_its_own_method(client):
    """Sourced: a buyer with an existing account can use it to populate name and
    email, so it is an alternative to typing rather than a different privilege."""
    room = make_room(client)
    set_policy(client, room["id"], mode="identify", collect_email=True)

    response = submit(client, room["id"], method="account_login")

    assert response.status_code == 201
    session = sessions(client, room["id"])["sessions"][0]
    assert session["data"]["method"] == "account_login"


def test_two_visits_from_one_address_create_two_sessions(client):
    """Repeat visits must not be silently merged, or analytics undercount."""
    room = make_room(client)
    set_policy(client, room["id"], mode="identify", collect_email=True)

    first = submit(client, room["id"]).json()
    second = submit(client, room["id"]).json()

    assert first["token"] != second["token"]
    assert sessions(client, room["id"])["count"] == 2


# -- the verification round trip --------------------------------------------- #


def test_verify_tier_issues_a_pending_session_and_queues_one_message(client):
    room = make_room(client)
    set_policy(client, room["id"], mode="verify_email", collect_name=True, collect_email=True)

    body = submit(client, room["id"]).json()

    assert body["status"] == "pending_verification"
    assert body["delivered_via"] == "outbox"
    assert count(client, "verification_outbox") == 1
    # The room is still closed.
    check = client.get(f"/api/rooms/{room['id']}/access/session", params={"token": body["token"]}).json()
    assert check["status"] == "pending"
    assert check["owed"] == ["verification"]


def test_full_round_trip_from_the_outbox_link(client):
    room = make_room(client)
    set_policy(
        client, room["id"], mode="verify_email", collect_name=True, collect_email=True,
        domain_security=True, allowed_domains="northwind.example",
    )

    pending = submit(client, room["id"]).json()
    message = outbox(client, room["id"])[0]
    token = token_from(message["data"]["link"])

    verified = client.get(f"/api/rooms/{room['id']}/access/verify", params={"token": token})

    assert verified.status_code == 200
    assert verified.json()["status"] == "verified"
    assert verified.json()["identity"]["email"] == "alex@northwind.example"
    assert outbox(client, room["id"])[0]["data"]["status"] == "sent"

    granted = client.get(f"/api/rooms/{room['id']}/access/session", params={"token": token}).json()
    assert granted["status"] == "granted"
    assert granted["verified"] is True
    assert granted["viewed_at"]


def test_first_grant_emits_one_activity_row_and_refreshes_do_not(client):
    room = make_room(client)
    set_policy(client, room["id"], mode="verify_email", collect_email=True)
    token = submit(client, room["id"]).json()["token"]
    client.get(f"/api/rooms/{room['id']}/access/verify", params={"token": token})

    for _ in range(4):
        client.get(f"/api/rooms/{room['id']}/access/session", params={"token": token})

    activity = client.get("/api/records/activity").json()["records"]
    assert len(activity) == 1
    assert activity[0]["data"]["identity_verified"] is True
    assert activity[0]["data"]["account"] == "Northwind Traders"


def test_verification_is_idempotent(client):
    room = make_room(client)
    set_policy(client, room["id"], mode="verify_email", collect_email=True)
    token = submit(client, room["id"]).json()["token"]

    first = client.get(f"/api/rooms/{room['id']}/access/verify", params={"token": token}).json()
    second = client.get(f"/api/rooms/{room['id']}/access/verify", params={"token": token}).json()

    assert first["already_verified"] is False
    assert second["already_verified"] is True
    assert second["identity"] == first["identity"]


def test_eleventh_presentation_of_a_token_retires_it(client):
    room = make_room(client)
    set_policy(client, room["id"], mode="verify_email", collect_email=True)
    token = submit(client, room["id"]).json()["token"]

    for _ in range(10):
        assert client.get(f"/api/rooms/{room['id']}/access/verify", params={"token": token}).status_code == 200

    response = client.get(f"/api/rooms/{room['id']}/access/verify", params={"token": token})

    assert response.status_code == 403
    assert response.json()["error"] == "too_many_attempts"
    session = client.get(f"/api/records/access_session/{token}").json()
    assert session["data"]["attempts"] == 11
    assert session["data"]["status"] == "refused"


def test_expired_link_is_refused(client):
    room = make_room(client)
    set_policy(client, room["id"], mode="verify_email", collect_email=True)
    token = submit(client, room["id"]).json()["token"]
    client.patch(f"/api/records/access_session/{token}", json={"expires_at": past_iso(hours=1)})

    response = client.get(f"/api/rooms/{room['id']}/access/verify", params={"token": token})

    assert response.status_code == 403
    assert response.json()["error"] == "session_expired"
    assert client.get(f"/api/records/access_session/{token}").json()["data"]["status"] == "refused"


def test_expired_grant_stops_granting(client):
    room = make_room(client)
    set_policy(client, room["id"], mode="verify_email", collect_email=True)
    token = submit(client, room["id"]).json()["token"]
    client.get(f"/api/rooms/{room['id']}/access/verify", params={"token": token})
    client.patch(
        f"/api/records/access_session/{token}",
        json={"expires_at": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()},
    )

    body = client.get(f"/api/rooms/{room['id']}/access/session", params={"token": token}).json()

    assert body["status"] == "expired"


def test_unknown_and_foreign_tokens_are_403(client):
    room = make_room(client)
    other = make_room(client, name="Contoso", account="Contoso")
    set_policy(client, room["id"], mode="verify_email", collect_email=True)
    token = submit(client, room["id"]).json()["token"]

    assert client.get(f"/api/rooms/{room['id']}/access/verify", params={"token": "access_session_x"}).status_code == 403
    assert client.get(f"/api/rooms/{other['id']}/access/verify", params={"token": token}).status_code == 403


def test_missing_required_field_is_400(client):
    room = make_room(client)
    set_policy(client, room["id"], mode="identify", collect_name=True)

    response = client.post(f"/api/rooms/{room['id']}/access/sessions", json={"name": ""})

    assert response.status_code == 400
    assert "name" in response.json()["errors"]


def test_malformed_address_is_400(client):
    room = make_room(client)
    set_policy(client, room["id"], mode="identify", collect_email=True)

    response = submit(client, room["id"], email="alex@northwind.example extra")

    assert response.status_code == 400
    assert "email" in response.json()["errors"]


# -- domain security --------------------------------------------------------- #


def test_disallowed_domain_is_refused_and_no_mail_is_sent(client):
    room = make_room(client)
    set_policy(
        client, room["id"], mode="verify_email", collect_email=True, domain_security=True,
        allowed_domains="northwind.example",
    )

    response = submit(client, room["id"], email="someone@contoso.example")

    assert response.status_code == 403
    assert response.json()["error"] == "domain_not_allowed"
    assert count(client, "verification_outbox") == 0


def test_refused_attempt_is_recorded_without_verifying_anybody(client):
    """The seller needs to see that the room was probed; the prober gets no
    credit for the attempt."""
    room = make_room(client)
    set_policy(
        client, room["id"], mode="verify_email", collect_email=True, domain_security=True,
        allowed_domains="northwind.example",
    )
    refused = submit(client, room["id"], email="someone@contoso.example").json()

    listing = sessions(client, room["id"], include_refused=True)

    assert listing["count"] == 1
    assert listing["verified"] == 0
    assert listing["sessions"][0]["data"]["refusal_reason"] == "domain_not_allowed"
    assert refused["session_id"] == listing["sessions"][0]["id"]
    # ...and it is hidden by default.
    assert sessions(client, room["id"])["count"] == 0


def test_a_second_listed_domain_is_accepted(client):
    room = make_room(client)
    set_policy(
        client, room["id"], mode="verify_email", collect_email=True, domain_security=True,
        allowed_domains="northwind.example, contoso.example",
    )

    assert submit(client, room["id"], email="someone@contoso.example").status_code == 201


def test_subdomain_is_not_implicitly_allowed(client):
    """Exact match only. A subdomain is a different domain, and silently
    accepting one is how an allowlist quietly grows."""
    room = make_room(client)
    set_policy(
        client, room["id"], mode="verify_email", collect_email=True, domain_security=True,
        allowed_domains="northwind.example",
    )

    assert submit(client, room["id"], email="someone@mail.northwind.example").status_code == 403


def test_allowlist_tightened_mid_flight_locks_out_the_pending_link(client):
    """A verification email can sit in an inbox for a day; a link that was
    legitimate when it was sent must not outlive the policy."""
    room = make_room(client)
    set_policy(
        client, room["id"], mode="verify_email", collect_email=True, domain_security=True,
        allowed_domains="northwind.example",
    )
    token = submit(client, room["id"]).json()["token"]
    set_policy(
        client, room["id"], mode="verify_email", collect_email=True, domain_security=True,
        allowed_domains="contoso.example",
    )

    response = client.get(f"/api/rooms/{room['id']}/access/verify", params={"token": token})

    assert response.status_code == 403
    assert response.json()["error"] == "domain_not_allowed"


# -- bots -------------------------------------------------------------------- #


def test_scanner_request_is_refused_flagged_and_excluded(client):
    """Sourced caveat: scanners "pollute analytics". A flag that does not change
    what the seller sees is a note in a log file."""
    room = make_room(client)
    set_policy(client, room["id"], mode="identify", collect_email=True)

    response = client.post(
        f"/api/rooms/{room['id']}/access/sessions",
        json={"name": "Preview", "email": "preview@contoso.example"},
        headers={"User-Agent": SCANNER_AGENT},
    )

    assert response.status_code == 403
    assert response.json()["error"] == "likely_bot"
    listing = sessions(client, room["id"], include_bots=True, include_refused=True)
    assert listing["sessions"][0]["data"]["likely_bot"] is True
    assert listing["sessions"][0]["data"]["refusal_reason"] == "likely_bot"
    # Excluded by default, and the exclusion is reported rather than silent.
    hidden = sessions(client, room["id"])
    assert hidden["count"] == 0
    assert hidden["excluded_bots"] == 1


def test_prefetch_header_is_treated_as_a_bot(client):
    room = make_room(client)
    set_policy(client, room["id"], mode="identify", collect_email=True)

    response = client.post(
        f"/api/rooms/{room['id']}/access/sessions",
        json={"name": "Link preview", "email": "preview@contoso.example"},
        headers={"User-Agent": BUYER_AGENT, "Sec-Purpose": "prefetch"},
    )

    assert response.status_code == 403


def test_a_real_browser_is_not_flagged(client):
    room = make_room(client)
    set_policy(client, room["id"], mode="identify", collect_email=True)

    response = client.post(
        f"/api/rooms/{room['id']}/access/sessions",
        json={"name": "Alex Buyer", "email": "alex@northwind.example"},
        headers={"User-Agent": BUYER_AGENT},
    )

    assert response.status_code == 201


# -- template inheritance ---------------------------------------------------- #


def make_template(client, name="Standard template"):
    return client.post("/api/records/room_template", json={"name": name}).json()


def test_room_without_a_policy_resolves_to_the_template(client):
    template = make_template(client)
    room = make_room(client, template_id=template["id"])
    client.put(
        f"/api/templates/{template['id']}/access",
        json={"mode": "verify_email", "collect_email": True},
    )

    body = client.get(f"/api/rooms/{room['id']}/access").json()

    assert body["level"] == "template"
    assert body["policy"]["mode"] == "verify_email"


def test_room_policy_overrides_the_template(client):
    template = make_template(client)
    room = make_room(client, template_id=template["id"])
    client.put(f"/api/templates/{template['id']}/access", json={"mode": "verify_email", "collect_email": True})
    set_policy(client, room["id"], mode="identify", collect_name=True)

    body = client.get(f"/api/rooms/{room['id']}/access").json()

    assert body["level"] == "room"
    assert body["policy"]["mode"] == "identify"


def test_template_change_reaches_an_inheriting_room_with_no_write_to_the_room(client):
    template = make_template(client)
    room = make_room(client, template_id=template["id"])
    assert client.get(f"/api/rooms/{room['id']}/access").json()["policy"]["mode"] == "open"

    client.put(f"/api/templates/{template['id']}/access", json={"mode": "verify_email", "collect_email": True})

    assert client.get(f"/api/rooms/{room['id']}/access").json()["policy"]["mode"] == "verify_email"
    # Nothing was written to the room itself.
    assert count(client, "access_policy") == 1


def test_clearing_a_room_policy_falls_back_to_the_template_and_is_audited(client):
    template = make_template(client)
    room = make_room(client, template_id=template["id"])
    client.put(f"/api/templates/{template['id']}/access", json={"mode": "verify_email", "collect_email": True})
    set_policy(client, room["id"], mode="open")

    cleared = client.delete(f"/api/rooms/{room['id']}/access")

    assert cleared.json()["cleared"] is True
    assert client.get(f"/api/rooms/{room['id']}/access").json()["level"] == "template"
    assert client.get("/api/audit", params={"collection": "access_policy", "action": "delete"}).json()["count"] == 1


def test_clearing_a_policy_that_does_not_exist_says_so(client):
    room = make_room(client)

    response = client.delete(f"/api/rooms/{room['id']}/access")

    assert response.status_code == 200
    assert response.json() == {"cleared": False, "reason": "no policy to clear"}


def test_inherit_on_a_room_policy_defers_to_its_template(client):
    template = make_template(client)
    room = make_room(client, template_id=template["id"])
    client.put(f"/api/templates/{template['id']}/access", json={"mode": "identify", "collect_name": True})
    set_policy(client, room["id"], mode="open", inherit=True, template_id=template["id"])

    assert client.get(f"/api/rooms/{room['id']}/access").json()["policy"]["mode"] == "identify"


def test_a_stored_policy_that_no_longer_validates_is_reported_not_hidden(client):
    """A rule tightened into an impossible state must not lock the seller out of
    their own room, and must not disappear either."""
    room = make_room(client)
    set_policy(
        client, room["id"], mode="verify_email", collect_email=True, domain_security=True,
        allowed_domains="northwind.example",
    )
    # Reach past validation the way a future rule change would break old data.
    record = client.get("/api/records/access_policy").json()["records"][0]
    client.patch(f"/api/records/access_policy/{record['id']}", json={"allowed_domains": []})

    body = client.get(f"/api/rooms/{room['id']}/access").json()

    assert body["policy"]["mode"] == "open"
    assert "allowed_domains" in body["errors"]


# -- the audit guarantee ----------------------------------------------------- #


def test_a_full_round_trip_leaves_exactly_the_expected_audit_rows(client):
    room = make_room(client)
    client.put(f"/api/rooms/{room['id']}/access", json={"mode": "verify_email", "collect_email": True})
    token = submit(client, room["id"]).json()["token"]
    client.get(f"/api/rooms/{room['id']}/access/verify", params={"token": token})
    client.get(f"/api/rooms/{room['id']}/access/session", params={"token": token})

    by_collection = {}
    for entry in client.get("/api/audit", params={"limit": 200}).json()["entries"]:
        by_collection.setdefault(entry["collection"], []).append(entry["action"])

    assert by_collection["access_policy"] == ["insert"]
    # The outbox row is created queued and updated to sent when the link is
    # followed, so a round trip leaves an insert and an update.
    assert by_collection["verification_outbox"] == ["update", "insert"]
    assert by_collection["activity"] == ["insert"]
    # The audit endpoint is newest-first, so the insert is last. The session is
    # touched once per presentation plus once per state change.
    assert by_collection["access_session"][-1] == "insert"
    assert set(by_collection["access_session"]) == {"insert", "update"}


def test_a_refusal_is_audited_too(client):
    """Whoever was refused, the attempt is on the record."""
    room = make_room(client)
    client.put(
        f"/api/rooms/{room['id']}/access",
        json={"mode": "verify_email", "collect_email": True, "domain_security": True,
              "allowed_domains": "northwind.example"},
    )
    submit(client, room["id"], email="someone@contoso.example")

    entries = client.get("/api/audit", params={"collection": "access_session"}).json()["entries"]
    assert len(entries) == 1
    assert entries[0]["after_state"]["refusal_reason"] == "domain_not_allowed"
    assert entries[0]["actor"] == "gate"


def test_the_verification_state_change_is_audited_with_a_diff(client):
    room = make_room(client)
    client.put(f"/api/rooms/{room['id']}/access", json={"mode": "verify_email", "collect_email": True})
    token = submit(client, room["id"]).json()["token"]
    client.get(f"/api/rooms/{room['id']}/access/verify", params={"token": token})

    update = client.get(
        "/api/audit", params={"record_id": token, "action": "update"}
    ).json()["entries"][0]

    assert update["diff"]["status"] == {"from": "pending_verification", "to": "verified"}


# -- the delivery seam ------------------------------------------------------- #


def test_emailed_link_redirects_a_human_into_the_app(client):
    """A buyer who clicks a link in an email and gets a JSON body has been sent
    the wrong link, so the emailed form redirects into the room instead."""
    room = make_room(client)
    set_policy(client, room["id"], mode="verify_email", collect_email=True)
    token = submit(client, room["id"]).json()["token"]
    open_link = outbox(client, room["id"])[0]["data"]["open_link"]

    response = client.get(open_link.replace("http://salesroom.test", ""), follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == f"/#/view/{room['id']}?token={token}"
    # ...and the session really is verified by following it.
    assert client.get(f"/api/rooms/{room['id']}/access/session", params={"token": token}).json()["status"] == "granted"


def test_a_refused_link_still_returns_403_rather_than_redirecting(client):
    """A dead link must tell the buyer why, not bounce them to an error page."""
    room = make_room(client)
    set_policy(
        client, room["id"], mode="verify_email", collect_email=True, domain_security=True,
        allowed_domains="northwind.example",
    )
    token = submit(client, room["id"]).json()["token"]
    set_policy(
        client, room["id"], mode="verify_email", collect_email=True, domain_security=True,
        allowed_domains="contoso.example",
    )
    link = outbox(client, room["id"])[0]["data"]["open_link"].replace("http://salesroom.test", "")

    response = client.get(link, follow_redirects=False)

    assert response.status_code == 403


def test_delivery_can_be_suppressed_for_a_headless_integration(client, monkeypatch):
    """DSR_ACCESS_DELIVER=0 lets a service issue its own link by another route,
    which is what a real SMTP transport would do."""
    room = make_room(client)
    set_policy(client, room["id"], mode="verify_email", collect_email=True)
    monkeypatch.setenv("DSR_ACCESS_DELIVER", "0")

    body = submit(client, room["id"]).json()

    assert body["status"] == "pending_verification"
    assert body["delivered_via"] == "suppressed"
    assert body["link"] is None
    assert count(client, "verification_outbox") == 0
    # The session still exists, so the buyer is not locked out by a mail failure.
    assert body["token"] is not None
