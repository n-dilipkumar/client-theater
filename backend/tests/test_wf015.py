"""Tests for the WF-015 plugin: registration, audit sources, and the port itself.

The behaviour of the gate is tested in ``test_access.py`` (the pure rules) and
``test_access_api.py`` (every endpoint, unchanged from the branch apart from the
prefix). This file covers the things that are *about the port* rather than about
identity, because those are the decisions a reviewer has to be able to check:

* the feature is mounted by discovery alone, under a prefix it owns, and maps its
  two domain errors through ``EXCEPTION_HANDLERS`` rather than through an edit to
  ``dsr/api.py``;
* the branch's core-vocabulary paths are not served anywhere, in either direction,
  so the rename is a fact rather than an intention;
* **every write's audit row names the path this router serves**, and the domain
  module contains no hard-coded route string at all. The branch guessed those
  strings inside ``dsr.access`` and the guesses went stale the moment the paths
  moved; these two tests are what stop that recurring;
* the verification link handed to a buyer is a URL this app actually serves, which
  is the one defect that would have made the whole feature 404 at the exact moment
  a buyer is watching;
* the frontend descriptor and the backend module agree on one name.
"""

from __future__ import annotations

import inspect
import random
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dsr.access import PolicyError
from dsr.api import app
from dsr.db.audited import AuditedDatabase
from dsr.features import load_feature

MODULE_NAME = "wf_015-identity-gate"
PREFIX = "/api/wf-015-identity-gate"
FRONTEND_ID = "wf-015-identity-gate"

ROOT = Path(__file__).resolve().parents[2]
DOMAIN_MODULE = ROOT / "backend" / "dsr" / "access.py"
DESCRIPTOR = ROOT / "frontend" / "src" / "features" / FRONTEND_ID / "index.jsx"


@pytest.fixture()
def client(monkeypatch):
    # A temporary database, exactly as test_features.py does it: the env var is
    # read at call time by dsr.deps, so every test gets its own store.
    tmp = tempfile.TemporaryDirectory()
    monkeypatch.setenv("DSR_DB_PATH", str(Path(tmp.name) / "wf015.db"))
    monkeypatch.setenv("DSR_AUDIT_DIR", str(Path(tmp.name) / "audit"))
    # The link a buyer follows must be stable for the round-trip test, so pin the
    # public base rather than inheriting the test client's host.
    monkeypatch.setenv("DSR_PUBLIC_URL", "http://salesroom.test")
    monkeypatch.setattr("dsr.api.FRONTEND_DIST", Path(tmp.name) / "absent-frontend")
    with TestClient(app) as test_client:
        yield test_client
    tmp.cleanup()


def make_room(client, name="Northwind", **extra):
    return client.post("/api/records/room", json={"name": name, **extra}).json()


def audit(client, **params):
    return client.get("/api/audit", params=params).json()["entries"]


# -- registration ------------------------------------------------------------- #


def test_the_feature_is_mounted_by_discovery(client):
    """No file in the host names this feature; discovery mounts it anyway."""
    body = client.get("/api/features").json()
    installed = {feature["id"]: feature for feature in body["features"]}

    assert FRONTEND_ID in installed
    record = installed[FRONTEND_ID]
    assert record["prefix"] == PREFIX
    assert record["ticket"] == "WF-015"
    # Both error mappings moved out of dsr/api.py and into the feature's export.
    assert record["exception_handlers"] == ["AccessDenied", "PolicyError"]


def test_no_file_in_the_host_names_this_feature():
    """Discovery alone, stated as an absence rather than as a passing route.

    The whole reason the plugin host exists is that the twelve original workflow
    branches each appended to ``dsr/api.py``, ``App.jsx`` and ``lib/api.js`` and so
    all twelve conflicted. This asserts this feature added nothing to the host.
    """
    for shared in ("backend/dsr/api.py", "frontend/src/lib/features.js"):
        assert FRONTEND_ID not in (ROOT / shared).read_text(encoding="utf-8")


def test_the_feature_did_not_collide_with_anything(client):
    body = client.get("/api/features").json()
    assert body["failed_count"] == 0, body["failed"]


def test_all_twelve_routes_are_registered(client):
    """The route table, so a dropped endpoint is a failing test and not a surprise."""
    record = next(f for f in client.get("/api/features").json()["features"] if f["id"] == FRONTEND_ID)
    served = {(method, route["path"]) for route in record["routes"] for method in route["methods"]}

    assert served == {
        ("GET", f"{PREFIX}/rooms/{{room_id}}/access"),
        ("PUT", f"{PREFIX}/rooms/{{room_id}}/access"),
        ("DELETE", f"{PREFIX}/rooms/{{room_id}}/access"),
        ("GET", f"{PREFIX}/templates/{{template_id}}/access"),
        ("PUT", f"{PREFIX}/templates/{{template_id}}/access"),
        ("DELETE", f"{PREFIX}/templates/{{template_id}}/access"),
        ("GET", f"{PREFIX}/rooms/{{room_id}}/access/requirements"),
        ("POST", f"{PREFIX}/rooms/{{room_id}}/access/sessions"),
        ("GET", f"{PREFIX}/rooms/{{room_id}}/access/verify"),
        ("GET", f"{PREFIX}/rooms/{{room_id}}/access/session"),
        ("GET", f"{PREFIX}/rooms/{{room_id}}/access/sessions"),
        ("GET", f"{PREFIX}/rooms/{{room_id}}/access/outbox"),
    }


def test_the_branch_paths_are_not_served_anywhere(client):
    """The port moved the surface under its own prefix; nothing else may claim it.

    The branch served ``/api/rooms/{id}/access`` and ``/api/templates/{id}/access``
    from the shared app. Those are core vocabulary other workflows want, so the
    port renamed them. This asserts the rename happened in both directions: the old
    path is not a feature, and the new one is.

    A ``PUT`` to a dead path is 405 rather than 404 when the built frontend is
    present, because ``dsr/api.py`` mounts a GET-only SPA catch-all at import time
    and Starlette matches it before the method is considered. Both are the same
    answer - nothing served it - and the property that matters is asserted
    directly: the write did not happen.
    """
    room = make_room(client)
    template = client.post("/api/records/room_template", json={"name": "T"}).json()
    before = client.get("/api/records/access_policy").json()["count"]

    assert client.get(f"/api/rooms/{room['id']}/access").status_code == 404
    assert client.get(f"/api/templates/{template['id']}/access").status_code == 404
    put_status = client.put(f"/api/rooms/{room['id']}/access", json={"mode": "open"}).status_code
    assert put_status in (404, 405), put_status

    # The substantive claim: the branch's path stored nothing, and the new one works.
    assert client.get("/api/records/access_policy").json()["count"] == before
    assert client.get(f"{PREFIX}/rooms/{room['id']}/access").status_code == 200
    client.put(f"{PREFIX}/rooms/{room['id']}/access", json={"mode": "open"})
    assert client.get(f"/api/rooms/{room['id']}/access").status_code == 404


def test_the_feature_module_does_not_import_the_shared_app():
    """A guard exists in test_features.py; this states the reason locally."""
    source = Path(load_feature(MODULE_NAME).__file__).read_text(encoding="utf-8")
    assert "dsr.api" not in source
    assert "from dsr.deps import" in source


def test_frontend_descriptor_id_matches_the_backend_feature_id():
    """The two halves of a feature are findable by one name, so they must agree."""
    module = load_feature(MODULE_NAME)
    text = DESCRIPTOR.read_text(encoding="utf-8")

    assert module.FEATURE["id"] == FRONTEND_ID
    assert f'id: {module.FEATURE["id"]!r}' in text


def test_the_frontend_intercepts_the_buyers_link_without_editing_app_jsx():
    """The emailed link lands on ``#/view/{room}``, which no core route matches.

    The branch added a ``BUYER_ROUTE`` branch to ``App.jsx`` so a buyer following a
    verification link got the gate and none of the seller's chrome. ``App.jsx`` is
    shared, so the descriptor renders the gate itself when the hash is a buyer
    route. This asserts the interception exists, because without it the redirect
    target resolves to the dashboard and the round trip silently dead-ends.
    """
    from_index = (DESCRIPTOR.parent / "index.jsx").read_text(encoding="utf-8")
    gate = (DESCRIPTOR.parent / "Gate.jsx").read_text(encoding="utf-8")

    assert "view/" in from_index
    assert "Gate" in from_index
    # ...and the gate is the one that refuses to render content before a grant.
    assert "check.status === 'granted'" in gate


# -- the two error mappings, through the host --------------------------------- #


def test_policy_error_becomes_a_400_with_a_field_keyed_map_through_the_host(client):
    """FastAPI only accepts exception handlers on the app, so the host attaches it.

    ``PolicyError`` propagates out of ``dsr.access``; nothing in the feature
    converts it by hand. If this stops being a 400 the handler stopped being
    registered, and the seller gets one combined string instead of a message per
    field.
    """
    room = make_room(client)

    response = client.put(
        f"{PREFIX}/rooms/{room['id']}/access",
        json={"mode": "identify", "collect_email": True, "domain_security": True,
              "allowed_domains": "northwind.example"},
    )

    assert response.status_code == 400
    assert response.json()["error"] == "policy_invalid"
    assert "domain_security" in response.json()["errors"]


def test_access_denied_becomes_a_403_naming_the_reason_and_the_session(client):
    """A refusal states why, and names the attempt without naming the buyer."""
    room = make_room(client)
    client.put(
        f"{PREFIX}/rooms/{room['id']}/access",
        json={"mode": "verify_email", "collect_email": True, "domain_security": True,
              "allowed_domains": "northwind.example"},
    )

    response = client.post(
        f"{PREFIX}/rooms/{room['id']}/access/sessions",
        json={"email": "someone@contoso.example"},
    )

    assert response.status_code == 403
    body = response.json()
    assert body["error"] == "domain_not_allowed"
    assert body["session_id"]
    # The attempt is on the record; the response does not attribute it.
    assert "someone@contoso.example" not in str(body)


def test_the_two_mapped_error_types_cannot_intercept_the_rest_of_the_product():
    """Why mapping them here is safe even though they subclass builtins.

    ``PolicyError`` is a ``ValueError`` and ``AccessDenied`` is a
    ``PermissionError``, so a handler registered for the *builtin* would intercept
    every such exception in the product. Starlette resolves a handler by walking the
    exception's own MRO, so a handler keyed on the specific type matches that type
    first and leaves everything above it alone. This asserts that behaviourally,
    because the reasoning is the guarantee.
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient as Client

    module = load_feature(MODULE_NAME)
    probe = FastAPI()
    probe.include_router(module.router)
    for error_type, handler in module.EXCEPTION_HANDLERS.items():
        probe.add_exception_handler(error_type, handler)

    @probe.get("/api/wf-015-identity-gate/probe/value-error")
    def raises_value_error():
        raise ValueError("some other feature's bug")

    @probe.get("/api/wf-015-identity-gate/probe/policy-error")
    def raises_policy_error():
        raise PolicyError({"mode": "nope"})

    client = Client(probe, raise_server_exceptions=False)
    # The specific type is still handled...
    assert client.get("/api/wf-015-identity-gate/probe/policy-error").status_code == 400
    # ...and an unrelated ValueError is not silently converted into a 400.
    assert client.get("/api/wf-015-identity-gate/probe/value-error").status_code == 500


# -- hard rule 4: the audit row names the route that served the write ---------- #


def test_every_write_audits_the_path_this_router_serves(client):
    """The port brief's hard rule 4, as a test.

    The branch hard-coded ``source="POST /access/sessions"`` and friends inside
    ``dsr.access``. Those strings name no route anybody can call, and the feature
    then moved its paths, so every row in the log pointed at a dead endpoint. If the
    prefix and the recorded source ever drift apart again, this fails.

    ``GET`` appears as a verb because two of these routes are reads that write:
    following a verification link and opening a granted room both change state.
    That is forced by the research - the emailed link has to be a link - and it is
    reported in the port report rather than quietly changed.
    """
    room = make_room(client)
    template = client.post("/api/records/room_template", json={"name": "T"}).json()
    rid = room["id"]

    client.put(f"{PREFIX}/rooms/{rid}/access", json={"mode": "verify_email", "collect_email": True})
    client.put(f"{PREFIX}/templates/{template['id']}/access", json={"mode": "identify", "collect_name": True})
    token = client.post(
        f"{PREFIX}/rooms/{rid}/access/sessions", json={"email": "alex@northwind.example"}
    ).json()["token"]
    client.get(f"{PREFIX}/rooms/{rid}/access/verify", params={"token": token})
    client.get(f"{PREFIX}/rooms/{rid}/access/session", params={"token": token})
    client.delete(f"{PREFIX}/rooms/{rid}/access")
    client.delete(f"{PREFIX}/templates/{template['id']}/access")

    # The room and the template were created through the core route, so look only
    # at the writes this feature served.
    sources = {entry["source"] for entry in audit(client) if PREFIX in (entry["source"] or "")}
    assert sources == {
        f"PUT {PREFIX}/rooms/{rid}/access",
        f"PUT {PREFIX}/templates/{template['id']}/access",
        f"POST {PREFIX}/rooms/{rid}/access/sessions",
        f"GET {PREFIX}/rooms/{rid}/access/verify",
        f"GET {PREFIX}/rooms/{rid}/access/session",
        f"DELETE {PREFIX}/rooms/{rid}/access",
        f"DELETE {PREFIX}/templates/{template['id']}/access",
    }


def test_the_domain_module_contains_no_hard_coded_route():
    """The defect itself, asserted at the source.

    ``source`` and the verification link are required keyword arguments on every
    ``AccessGate`` method that writes, so the domain cannot name a path. A literal
    route string creeping back into ``access.py`` is the regression.
    """
    text = DOMAIN_MODULE.read_text(encoding="utf-8")
    assert not re.search(r'source\s*=\s*"(?:GET|POST|PUT|DELETE|PATCH)\s+/', text)
    assert not re.search(r'"/api/', text), "a path literal in the domain module can only go stale"


def test_the_domain_module_refuses_to_guess_an_audit_source():
    """``source`` is required, not defaulted, on every writing method.

    A default would be a guess wearing a parameter's clothes: the first caller to
    omit it would put a plausible but wrong route in the audit log, silently.
    """
    from dsr.access import AccessGate

    for name in ("set_policy", "clear_policy", "open_session", "verify", "check"):
        parameter = inspect.signature(getattr(AccessGate, name)).parameters["source"]
        assert parameter.default is inspect.Parameter.empty, f"{name} defaults its source"
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY


# -- the delivery seam --------------------------------------------------------- #


def test_the_verification_link_is_a_url_this_app_serves(client):
    """The one defect that would have broken the feature end to end.

    The branch built the emailed link as ``/api/rooms/{id}/access/verify``. Left
    alone through the port that link 404s, so the buyer clicks it and nothing
    happens - at the one moment a person is watching. The link is now built from
    the router prefix, and this follows it verbatim rather than reassembling it.
    """
    room = make_room(client)
    client.put(
        f"{PREFIX}/rooms/{room['id']}/access",
        json={"mode": "verify_email", "collect_email": True},
    )
    client.post(
        f"{PREFIX}/rooms/{room['id']}/access/sessions",
        json={"email": "alex@northwind.example"},
    )

    message = client.get(f"{PREFIX}/rooms/{room['id']}/access/outbox").json()["messages"][0]
    link = message["data"]["link"]
    assert link.startswith(f"http://salesroom.test{PREFIX}/rooms/{room['id']}/access/verify")

    # Follow the seller's copy, exactly as a buyer would.
    response = client.get(link.removeprefix("http://salesroom.test"))
    assert response.status_code == 200
    assert response.json()["status"] == "verified"


def test_the_emailed_link_redirects_into_the_room_the_app_serves(client):
    """``open_link`` is what a human clicks, so it has to land somewhere real.

    It lands on ``/#/view/{room}``, a hash route no core route matches. The feature
    descriptor intercepts it; this pins the target so a change to it is deliberate.
    """
    room = make_room(client)
    rid = room["id"]
    client.put(f"{PREFIX}/rooms/{rid}/access", json={"mode": "verify_email", "collect_email": True})
    token = client.post(
        f"{PREFIX}/rooms/{rid}/access/sessions", json={"email": "alex@northwind.example"}
    ).json()["token"]
    open_link = client.get(f"{PREFIX}/rooms/{rid}/access/outbox").json()["messages"][0]["data"]["open_link"]

    response = client.get(open_link.removeprefix("http://salesroom.test"), follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == f"/#/view/{rid}?token={token}"


# -- schema flexibility -------------------------------------------------------- #


def test_a_team_can_add_its_own_field_to_a_policy_through_this_features_route(client):
    """Schema flexibility, checked at the feature's own door rather than the core one."""
    room = make_room(client)

    client.put(
        f"{PREFIX}/rooms/{room['id']}/access",
        json={"mode": "open", "legal_footer": "Confidential", "scoring": {"weight": 0.4}},
    )

    policy = client.get(f"{PREFIX}/rooms/{room['id']}/access").json()["policy"]
    assert policy["legal_footer"] == "Confidential"
    assert policy["scoring"] == {"weight": 0.4}
    # ...and it is a record, so the generic surface and the dynamic index see it.
    found = client.get(
        "/api/records/access_policy", params={"where": "subject_kind=room"}
    ).json()
    assert found["count"] == 1
    assert found["records"][0]["data"]["scoring"] == {"weight": 0.4}


# -- demo data ----------------------------------------------------------------- #


def test_seed_leaves_every_tier_and_every_session_state_visible():
    """The feature's own ``seed(db, context)``, which replaced its seed.py edit.

    A feature whose page is empty in the demo is a feature nobody can review, so the
    hook has to produce something the page can actually show: all three tiers, an
    inherited policy, the domain allowlist, a session in each of the four states the
    seller list renders, one bot-flagged probe, and a queued message to hand over.
    """
    module = load_feature(MODULE_NAME)
    now = datetime(2026, 9, 27, 9, 0, tzinfo=timezone.utc)

    tmp = tempfile.TemporaryDirectory()
    try:
        db = AuditedDatabase(str(Path(tmp.name) / "seeded.db"), actor="seed")
        try:
            rooms = [
                db.create("room", {"name": name, "account": name}, actor="dana", source="seed")
                for name in ("Northwind", "Contoso", "Fabrikam", "Adventure Works")
            ]
            room_ids = [(room["id"], room["data"]["account"]) for room in rooms]

            summary = module.seed(db, {"room_ids": room_ids, "now": now, "rng": random.Random("wf015")})

            assert "2 room policies" in summary
            assert "1 queued message" in summary

            policies = db.list("access_policy", limit=50)
            room_policies = [r for r in policies if r["data"]["subject_kind"] == "room"]
            assert {r["data"]["mode"] for r in room_policies} == {"identify", "verify_email"}

            # The open tier is the absence of a policy, and the page renders that
            # case differently - "no policy set" rather than "own policy" - so it
            # has to exist in the demo as a room with nothing on it.
            policied = {r["data"]["subject_id"] for r in room_policies}
            assert any(room["id"] not in policied for room in db.list("room", limit=50))

            # The domain allowlist is seeded normalised, with the @ stripped, which
            # is the form the research tells the operator they may type.
            restricted = [
                record
                for record in db.list("access_policy", limit=50)
                if record["data"].get("domain_security")
            ]
            assert len(restricted) == 1
            assert restricted[0]["data"]["allowed_domains"] == ["northwind.example", "contoso.example"]

            # A room inherits the template with nothing written to it. This is the
            # researched behaviour and the reason `level` exists at all.
            inheriting = [
                record
                for record in db.list("room", limit=50)
                if (record["data"] or {}).get("template_id")
            ]
            assert len(inheriting) == 1
            template_id = inheriting[0]["data"]["template_id"]
            room_policies = {
                record["data"]["subject_id"]
                for record in db.list("access_policy", limit=50)
                if record["data"]["subject_kind"] == "room"
            }
            assert inheriting[0]["id"] not in room_policies
            assert template_id in {
                record["data"]["subject_id"]
                for record in db.list("access_policy", limit=50)
                if record["data"]["subject_kind"] == "template"
            }

            # Every state the seller list renders, including the probe that makes
            # `excluded_bots` a real number rather than a hypothetical.
            statuses = {record["data"]["status"] for record in db.list("access_session", limit=50)}
            assert statuses == {"verified", "identified", "pending_verification", "refused"}
            assert any(record["data"].get("likely_bot") for record in db.list("access_session", limit=50))
            assert any(
                record["data"].get("refusal_reason") == "domain_not_allowed"
                for record in db.list("access_session", limit=50)
            )

            # The outbox row links to the path this router serves, so the demo's
            # round trip is finishable rather than decorative.
            queued = db.list("verification_outbox", limit=10)
            assert len(queued) == 1
            assert f"{PREFIX}/rooms/" in queued[0]["data"]["link"]
            assert queued[0]["data"]["open_link"].endswith("&redirect=1")

            # The research's claim that a verified identity shows up in analytics is
            # otherwise invisible in a demo, so it is seeded.
            verified_views = [
                record
                for record in db.list("activity", limit=500)
                if record["data"].get("identity_verified")
            ]
            assert len(verified_views) == 1
        finally:
            db.close()
    finally:
        tmp.cleanup()


def test_seed_reports_when_there_is_nothing_to_attach_to():
    module = load_feature(MODULE_NAME)
    tmp = tempfile.TemporaryDirectory()
    try:
        db = AuditedDatabase(str(Path(tmp.name) / "empty.db"), actor="seed")
        try:
            assert module.seed(db, {"room_ids": [], "now": datetime.now(timezone.utc), "rng": random.Random("x")})
        finally:
            db.close()
    finally:
        tmp.cleanup()
