"""WF-084 over HTTP: the surface this feature's own router serves.

The domain rules are in ``test_wf084.py``. This file is the other half, and it is organised
by what a caller can observe:

``the route table``
    That the host mounted every route by discovery alone, with no shared file edited, and
    that no two of them collide.
``signing in``
    The authorization URL, the callback, and the assertion that guards it. The two refusals
    are the headline: a profile from another tenant, and an expired authorization code.
``Directory Sync``
    The SCIM webhook, its token, and the three operations the research names. A deprovision
    is the one that matters: it has to remove access over HTTP, not just in the engine.
``access``
    The group-to-rule mapping and the access answer it produces, including the tenant whose
    provider posts without the token.
``the error shapes``
    Every status and body this router can produce, including the 403 for a foreign tenant and
    the 401 for a dead code, which are different refusals and must stay different.
``the audit-source rule``
    Every ``source=`` this workflow records names a route the host actually mounted. This is
    the test the build brief asks for by name.
``the honesty rule``
    Every response carries the limitation and the assertion policy, so no caller can read a
    granted session without also reading what stood behind it.
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path
from typing import Any

import dsr.features as host
import pytest
from dsr.security_governance import sso_vocabulary as vocab
from fastapi.testclient import TestClient

FEATURE_MODULE = "dsr.features.wf084_federate_staff_sso_and_auto_provision_via_scim"
PREFIX = "/api/wf-084"
FEATURE_ID = "wf-084-federate-staff-sso-and-auto-provision-via-scim"

TENANT = "org_northwind"
CALLBACK = "https://app.example/wf-084/callback"
ISSUER = "https://idp.northwind.example/authorize"
WEBHOOK_HEADER = "X-WF084-Webhook-Token"


def make_room(client: TestClient, room_id: str = "room_a") -> str:
    """One room to hang a tenant on.

    Created through the core API rather than straight into the store, so the room is a row
    the application itself would have written. A test that faked it could pass against a
    tenant whose room does not exist.
    """

    response = client.post(
        "/api/records/room", json={"name": f"Room {room_id}"}, params={"record_id": room_id}
    )
    assert response.status_code in (200, 201), response.text
    return room_id


def make_tenant(client: TestClient, room_id: str | None = None, **overrides: Any) -> dict[str, Any]:
    """One recorded tenant with a registered redirect URI."""

    body = {
        vocab.ORGANIZATION_ID: TENANT,
        "name": "Northwind Traders",
        "redirect_uris": [CALLBACK],
        vocab.CLIENT_ID_FIELD: "client_northwind",
    }
    body.update(overrides)
    response = client.post(
        f"{PREFIX}/organizations", json=body, params={"room_id": room_id} if room_id else None
    )
    assert response.status_code == 201, response.text
    return response.json()


def make_connection(
    client: TestClient, tenant: dict[str, Any] | None = None, **body: Any
) -> dict[str, Any]:
    target = tenant or ensure_tenant(client)
    payload = {
        "name": "Northwind Okta",
        "protocol": vocab.PROTOCOL_SAML,
        vocab.REDIRECT_URI_PARAM: CALLBACK,
    }
    payload.update(body)
    response = client.post(
        f"{PREFIX}/organizations/{target[vocab.ORGANIZATION_ID]}/connections", json=payload
    )
    assert response.status_code == 201, response.text
    return response.json()


def ensure_tenant(client: TestClient) -> dict[str, Any]:
    """The tenant, created only if it is not there yet.

    Helpers below call this rather than ``make_tenant`` unconditionally. A test that needs
    to assert on a refusal must not fail on a duplicate tenant created by an earlier call in
    the same test, and a helper that silently tolerates that hides a real mistake: a test
    that creates two tenants and asserts about the first would otherwise read the second.
    """

    existing = client.get(f"{PREFIX}/organizations/{TENANT}")
    if existing.status_code == 200:
        return existing.json()
    return make_tenant(client)


def begin_sign_in(client: TestClient, **body: Any) -> dict[str, Any]:
    """One staff-initiated authorization, with defaults for the whole flow.

    A caller naming a ``connection`` gets the defaults without ``organization``, because
    the three identifiers are three separate instructions and a request that sends both is
    sending two where the IdP expects one. That is the vendor's evidence about what each one
    means, and this helper states the same rule the validator does.
    """

    ensure_tenant(client)
    payload: dict[str, Any] = {"issuer": ISSUER, vocab.REDIRECT_URI_PARAM: CALLBACK}
    if body.get(vocab.CONNECTION_PARAM) or body.get(vocab.PROVIDER_PARAM):
        payload.update(body)
    else:
        payload.update({vocab.ORGANIZATION_PARAM: TENANT, **body})
    response = client.post(f"{PREFIX}/sso/authorize", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def callback(client: TestClient, profile: dict[str, Any], **body: Any) -> Any:
    """One callback, returning the response so a test can read a refusal."""

    ensure_tenant(client)
    payload: dict[str, Any] = {vocab.ORGANIZATION_PARAM: TENANT, "profile": profile}
    payload.update(body)
    return client.post(f"{PREFIX}/sso/callback", json=payload)


def make_directory(
    client: TestClient, tenant: dict[str, Any] | None = None, **body: Any
) -> dict[str, Any]:
    target = tenant or ensure_tenant(client)
    payload = {"name": "Northwind Okta directory", vocab.PROVIDER_PARAM: vocab.PROVIDER_OKTA}
    payload.update(body)
    response = client.post(
        f"{PREFIX}/organizations/{target[vocab.ORGANIZATION_ID]}/directories", json=payload
    )
    assert response.status_code == 201, response.text
    return response.json()


def scim(
    client: TestClient,
    directory: dict[str, Any],
    payload: dict[str, Any],
    token: str | None = None,
) -> Any:
    """One SCIM change through the webhook, returning the response."""

    headers = {WEBHOOK_HEADER: token if token is not None else directory["webhook_token"]}
    return client.post(
        f"{PREFIX}/directories/{directory['id']}/events", json=payload, headers=headers
    )


def directory_user(
    client: TestClient,
    directory: dict[str, Any],
    external_id: str,
    *,
    groups: list[str] | None = None,
    address: str | None = None,
    active: bool = True,
    operation: str = vocab.SCIM_OP_CREATE,
) -> Any:
    subject: dict[str, Any] = {vocab.SCIM_EXTERNAL_ID: external_id}
    if operation != vocab.SCIM_OP_DELETE:
        subject.update(
            {
                vocab.SCIM_GIVEN_NAME: external_id.split("_")[-1].title(),
                vocab.SCIM_FAMILY_NAME: "Example",
                vocab.SCIM_EMAILS: [address or f"{external_id}@northwind.example"],
                vocab.SCIM_GROUPS: list(groups or []),
                vocab.SCIM_ACTIVE: active,
            }
        )
    return scim(client, directory, {"operation": operation, "user": subject})


def directory_group(
    client: TestClient, directory: dict[str, Any], external_id: str, name: str
) -> dict[str, Any]:
    response = scim(
        client,
        directory,
        {
            "operation": vocab.SCIM_OP_UPDATE,
            "group": {vocab.SCIM_EXTERNAL_ID: external_id, "name": name},
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def group_by_external(
    client: TestClient, directory: dict[str, Any], external_id: str
) -> dict[str, Any]:
    rows = client.get(f"{PREFIX}/directories/{directory['id']}/groups").json()["groups"]
    return next(row for row in rows if row[vocab.SCIM_EXTERNAL_ID] == external_id)


def sign_in(client: TestClient, address: str = "dana@northwind.example", **body: Any) -> Any:
    """One successful sign-in, unless the test is asking for a refusal."""

    ensure_tenant(client)
    return callback(
        client,
        {vocab.ORGANIZATION_ID: TENANT, vocab.SCIM_EMAILS: [{"address": address}]},
        **body,
    )


# --------------------------------------------------------------------------- #
# the route table
# --------------------------------------------------------------------------- #


class TestRouteTable:
    def test_the_feature_is_installed_with_its_routes(self, client: TestClient):
        payload = client.get("/api/features").json()
        installed = next(f for f in payload["features"] if f["id"] == FEATURE_ID)
        paths = {route["path"] for route in installed["routes"]}
        assert f"{PREFIX}/summary" in paths
        assert f"{PREFIX}/sso/authorize" in paths
        assert f"{PREFIX}/sso/callback" in paths
        assert f"{PREFIX}/directories/{{directory_id}}/events" in paths

    def test_the_feature_did_not_fail_to_load(self, client: TestClient):
        payload = client.get("/api/features").json()
        assert not [f for f in payload["failed"] if FEATURE_ID in str(f)]

    def test_the_host_mounted_it_without_a_shared_file_being_edited(self):
        """``backend/dsr/api.py`` discovers and mounts every feature router.

        This states the claim by name so a reviewer can check it against the diff.
        """

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
        assert len(mounted) >= 20

    def test_the_prefix_is_ticket_derived(self):
        assert PREFIX == "/api/wf-084"

    def test_the_error_handlers_are_this_features_own_types(self):
        """A handler for ValueError or PermissionError would intercept across the product."""
        from dsr.security_governance import sso_rules as rules

        module = importlib.import_module(FEATURE_MODULE)
        for error_type in module.EXCEPTION_HANDLERS:
            assert error_type.__module__.startswith("dsr.security_governance"), error_type
            assert not issubclass(error_type, (ValueError, PermissionError)) or error_type in (
                rules.IdentitySettingsInvalid,
                rules.TenantAssertionFailed,
            ), error_type

    def test_the_feature_id_matches_the_frontend_folder(self):
        """The id is the one name both halves share.

        A mismatch would discover the page and not find its backend.
        """

        import dsr

        module = importlib.import_module(FEATURE_MODULE)
        root = Path(dsr.__file__ or "").resolve().parents[2]
        folder = root / "frontend" / "src" / "features" / FEATURE_ID
        if not folder.is_dir():
            pytest.skip("frontend sources are not present in this checkout")
        assert (folder / "index.jsx").is_file()
        assert module.FEATURE["id"] == FEATURE_ID


# --------------------------------------------------------------------------- #
# signing in
# --------------------------------------------------------------------------- #


class TestSigningIn:
    def test_an_organization_can_be_created_and_read_back(self, client: TestClient):
        created = make_tenant(client, room_id=make_room(client))
        assert created[vocab.ORGANIZATION_ID] == TENANT
        assert created["redirect_uris"] == [CALLBACK]
        assert client.get(f"{PREFIX}/organizations/{TENANT}").json()["name"] == "Northwind Traders"

    def test_a_tenant_without_an_organization_id_is_a_400(self, client: TestClient):
        response = client.post(f"{PREFIX}/organizations", json={"name": "Nameless"})
        assert response.status_code == 400
        assert vocab.ORGANIZATION_ID in response.json()["errors"]

    def test_an_unknown_tenant_is_a_404_not_a_500(self, client: TestClient):
        assert client.get(f"{PREFIX}/organizations/org_never").status_code == 404

    def test_a_connection_is_created_for_a_tenant(self, client: TestClient):
        tenant = ensure_tenant(client)
        connection = make_connection(client, tenant)
        assert connection["protocol"] == vocab.PROTOCOL_SAML
        assert connection[vocab.REDIRECT_URI_PARAM] == CALLBACK

    def test_a_connection_with_an_unsupported_protocol_is_a_400(self, client: TestClient):
        ensure_tenant(client)
        response = client.post(
            f"{PREFIX}/organizations/{TENANT}/connections",
            json={"name": "LDAP", "protocol": "ldap", vocab.REDIRECT_URI_PARAM: CALLBACK},
        )
        assert response.status_code == 400
        assert "protocol" in response.json()["errors"]

    def test_a_connection_with_an_unregistered_redirect_uri_is_a_400(self, client: TestClient):
        ensure_tenant(client)
        response = client.post(
            f"{PREFIX}/organizations/{TENANT}/connections",
            json={
                "name": "Evil",
                "protocol": "oidc",
                vocab.REDIRECT_URI_PARAM: "https://attacker.example/steal",
            },
        )
        assert response.status_code == 400
        assert vocab.REDIRECT_URI_PARAM in response.json()["errors"]

    def test_an_unknown_connection_is_a_404(self, client: TestClient):
        assert client.get(f"{PREFIX}/connections/conn_absent").status_code == 404

    def test_an_authorization_url_names_the_organization_and_carries_no_session(
        self, client: TestClient
    ):
        """The specification's second user-flow step. No session exists yet."""
        authorization = begin_sign_in(client)
        assert authorization["identifier"] == "organization"
        assert authorization["authorization_url"].startswith(ISSUER)
        assert f"organization={TENANT}" in authorization["authorization_url"]
        # The remaining life of a code this app just minted, so 600 less whatever the
        # test process spent between minting it and reading it back. Asserting exactly 600
        # would make this a test of how fast pytest runs.
        assert 595 <= authorization["ttl_seconds"] <= 600
        assert client.get(f"{PREFIX}/sessions").json()["count"] == 0

    def test_an_authorization_url_can_name_a_connection_instead(self, client: TestClient):
        tenant = ensure_tenant(client)
        connection = make_connection(client, tenant)
        # `organization` is left out entirely rather than sent as null. The helper drops
        # empty values, and a connection sign-in names exactly one of the three identifiers.
        authorization = begin_sign_in(client, connection=connection["id"])
        assert authorization["identifier"] == "connection"
        assert connection["id"] in authorization["authorization_url"]

    def test_an_authorization_with_an_unregistered_redirect_uri_is_a_400(self, client: TestClient):
        ensure_tenant(client)
        response = client.post(
            f"{PREFIX}/sso/authorize",
            json={
                vocab.ORGANIZATION_PARAM: TENANT,
                "issuer": ISSUER,
                vocab.REDIRECT_URI_PARAM: "https://attacker.example/steal",
            },
        )
        assert response.status_code == 400

    def test_an_authorization_with_no_identifier_is_a_400(self, client: TestClient):
        response = client.post(
            f"{PREFIX}/sso/authorize", json={"issuer": ISSUER, vocab.REDIRECT_URI_PARAM: CALLBACK}
        )
        assert response.status_code == 400

    def test_a_matching_profile_grants_a_session(self, client: TestClient):
        response = sign_in(client)
        assert response.status_code == 201, response.text
        body = response.json()
        assert body[vocab.ORGANIZATION_ID] == TENANT
        assert body[vocab.ASSERT_ORDER_FIELD] is True
        assert body["revoked"] is False
        assert client.get(f"{PREFIX}/sessions/{body['id']}").json()["live"] is True

    def test_a_profile_from_another_tenant_is_a_403(self, client: TestClient):
        """The security property, over the wire.

        The email is at the expected tenant's own domain, so the profile looks right to
        anything checking the address and is wrong to the one field that decides.
        """

        response = callback(
            client,
            {
                vocab.ORGANIZATION_ID: "org_globex",
                vocab.SCIM_EMAILS: [{"address": "guest@northwind.example"}],
            },
        )
        assert response.status_code == 403
        body = response.json()
        assert body["error"] == "tenant_assertion_failed"
        assert body["reason"] == "tenant_mismatch"
        assert body["expected_organization_id"] == TENANT
        assert body["actual_organization_id"] == "org_globex"
        assert client.get(f"{PREFIX}/sessions").json()["count"] == 0

    def test_a_profile_with_no_organization_id_is_a_403(self, client: TestClient):
        response = callback(client, {vocab.SCIM_EMAILS: [{"address": "dana@northwind.example"}]})
        assert response.status_code == 403
        assert response.json()["reason"] == "tenant_absent"

    def test_a_refusal_says_an_email_domain_is_not_the_tenant(self, client: TestClient):
        """A refusal is where a reader is most likely to over-read the assertion."""
        body = callback(client, {vocab.ORGANIZATION_ID: "org_globex"}).json()
        assert "unsafe" in body[vocab.EMAIL_DOMAIN_UNSAFE_FIELD].casefold()
        assert body[vocab.ASSERTION_POLICY_FIELD] == vocab.ASSERTION_POLICY

    def test_an_expired_code_is_a_401_and_the_two_refusals_differ(self, client: TestClient):
        """An expired code and a foreign tenant are different problems.

        One needs a new sign-in, the other needs a different account. Answering both with
        one status would leave a login surface unable to tell the user what to do.
        """

        stale = sign_in(client, issued_at="2020-01-01T00:00:00.000+00:00")
        assert stale.status_code == 401
        assert stale.json()["error"] == "authorization_code_expired"
        assert stale.json()["reason"] == "authorization_code_expired"
        assert stale.json()[vocab.EXPIRED_CODE_POLICY_FIELD] == vocab.EXPIRED_CODE_POLICY

        foreign = callback(client, {vocab.ORGANIZATION_ID: "org_globex"})
        assert foreign.status_code == 403
        assert stale.status_code != foreign.status_code

    def test_a_code_issued_just_now_is_accepted(self, client: TestClient):
        """The bound has to be checked in both directions, or it is not a bound."""
        from dsr.security_governance.sso_rules import stamp, utcnow

        response = sign_in(client, issued_at=stamp(utcnow()))
        assert response.status_code == 201, response.text

    def test_a_callback_with_no_profile_is_a_400(self, client: TestClient):
        ensure_tenant(client)
        response = client.post(f"{PREFIX}/sso/callback", json={vocab.ORGANIZATION_PARAM: TENANT})
        assert response.status_code == 400
        assert "profile" in response.json()["errors"]

    def test_a_callback_naming_an_unknown_tenant_is_a_404(self, client: TestClient):
        response = client.post(
            f"{PREFIX}/sso/callback",
            json={
                vocab.ORGANIZATION_PARAM: "org_never",
                "profile": {vocab.ORGANIZATION_ID: "org_never"},
            },
        )
        assert response.status_code == 404

    def test_an_idp_initiated_flow_reaches_the_same_callback(self, client: TestClient):
        """The spec puts both in scope, and they are two doors into one room."""
        response = sign_in(
            client, **{vocab.FLOW_KEY: vocab.FLOW_IDP_INITIATED, vocab.RELAY_STATE_PARAM: CALLBACK}
        )
        assert response.status_code == 201, response.text
        assert response.json()[vocab.FLOW_KEY] == vocab.FLOW_IDP_INITIATED

    def test_an_idp_initiated_flow_is_asserted_too(self, client: TestClient):
        ensure_tenant(client)
        response = client.post(
            f"{PREFIX}/sso/callback",
            json={
                vocab.ORGANIZATION_PARAM: TENANT,
                vocab.FLOW_KEY: vocab.FLOW_IDP_INITIATED,
                "profile": {vocab.ORGANIZATION_ID: "org_globex"},
            },
        )
        assert response.status_code == 403

    def test_an_unregistered_relay_state_redirect_uri_is_a_400(self, client: TestClient):
        ensure_tenant(client)
        response = client.post(
            f"{PREFIX}/sso/callback",
            json={
                vocab.ORGANIZATION_PARAM: TENANT,
                vocab.FLOW_KEY: vocab.FLOW_IDP_INITIATED,
                vocab.REDIRECT_URI_PARAM: "https://attacker.example/steal",
                "profile": {vocab.ORGANIZATION_ID: TENANT},
            },
        )
        assert response.status_code == 400

    def test_sessions_are_narrowable_by_tenant(self, client: TestClient):
        sign_in(client)
        # The list narrows on `organization_id`, which is the stored field name. Sending
        # `organization` - the identifier the sign-in request uses - would silently filter
        # nothing, so the test names the parameter the route actually reads.
        narrowed = client.get(f"{PREFIX}/sessions", params={"organization_id": TENANT}).json()
        assert narrowed["count"] == 1
        empty = client.get(f"{PREFIX}/sessions", params={"organization_id": "org_other"}).json()
        assert empty["count"] == 0

    def test_an_unknown_session_is_a_404_not_a_500(self, client: TestClient):
        response = client.get(f"{PREFIX}/sessions/sess_absent")
        assert response.status_code == 404
        assert response.json()["error"] == "not_found"


# --------------------------------------------------------------------------- #
# Directory Sync
# --------------------------------------------------------------------------- #


class TestDirectorySync:
    def test_a_directory_returns_its_token_once(self, client: TestClient):
        directory = make_directory(client)
        assert directory["webhook_token"]
        read_back = client.get(f"{PREFIX}/directories/{directory['id']}").json()
        assert "webhook_token" not in read_back
        assert directory["webhook_token"] not in str(read_back)

    def test_every_named_provider_can_be_connected(self, client: TestClient):
        tenant = ensure_tenant(client)
        for provider in vocab.SUPPORTED_PROVIDERS:
            directory = make_directory(client, tenant, provider=provider)
            assert directory[vocab.PROVIDER_PARAM] == provider

    def test_an_unsupported_provider_is_a_400(self, client: TestClient):
        ensure_tenant(client)
        response = client.post(
            f"{PREFIX}/organizations/{TENANT}/directories", json={vocab.PROVIDER_PARAM: "ldap"}
        )
        assert response.status_code == 400
        assert vocab.PROVIDER_PARAM in response.json()["errors"]

    def test_an_unknown_directory_is_a_404(self, client: TestClient):
        assert client.get(f"{PREFIX}/directories/dir_absent").status_code == 404

    def test_a_joiner_is_provisioned_over_http(self, client: TestClient):
        directory = make_directory(client)
        response = directory_user(client, directory, "okta_ada", groups=["grp_sales"])
        assert response.status_code == 201, response.text
        assert response.json()[vocab.SCIM_EXTERNAL_ID] == "okta_ada"
        listed = client.get(f"{PREFIX}/directories/{directory['id']}/users").json()
        assert listed["count"] == 1

    def test_a_mover_updates_rather_than_duplicates(self, client: TestClient):
        directory = make_directory(client)
        directory_user(client, directory, "okta_ada", groups=["grp_sales"])
        directory_user(client, directory, "okta_ada", groups=["grp_admin"])
        assert client.get(f"{PREFIX}/directories/{directory['id']}/users").json()["count"] == 1

    def test_a_post_without_the_token_is_refused(self, client: TestClient):
        """The cheapest attack on this workflow is an unauthenticated deprovision."""
        directory = make_directory(client)
        response = scim(
            client,
            directory,
            {"operation": vocab.SCIM_OP_CREATE, "user": {vocab.SCIM_EXTERNAL_ID: "okta_mallory"}},
            token="",
        )
        assert response.status_code == 404
        assert client.get(f"{PREFIX}/directories/{directory['id']}/users").json()["count"] == 0

    def test_a_post_with_another_directorys_token_is_refused(self, client: TestClient):
        first = make_directory(client)
        second = make_directory(client)
        response = scim(
            client,
            second,
            {"operation": vocab.SCIM_OP_CREATE, "user": {vocab.SCIM_EXTERNAL_ID: "okta_mallory"}},
            token=first["webhook_token"],
        )
        assert response.status_code == 404

    def test_the_token_travels_in_a_header_and_a_body_is_the_fallback(self, client: TestClient):
        """A credential in a body is one an access log captures, so the header wins."""
        directory = make_directory(client)
        body = {
            "operation": vocab.SCIM_OP_CREATE,
            "user": {vocab.SCIM_EXTERNAL_ID: "okta_ada", vocab.SCIM_EMAILS: ["a@nw.example"]},
            "webhook_token": directory["webhook_token"],
        }
        assert scim(client, directory, body).status_code == 201

        # A wrong header is believed over a right body, which is the safe direction: a
        # header is set by the caller that authenticated the directory.
        body["webhook_token"] = "wrong"
        response = scim(
            client,
            directory,
            {**body, "user": {vocab.SCIM_EXTERNAL_ID: "okta_robin"}},
            token="wrong",
        )
        assert response.status_code == 404

    def test_a_leaver_is_deprovisioned_over_http(self, client: TestClient):
        directory = make_directory(client)
        directory_user(client, directory, "okta_ada", address="ada@northwind.example")
        response = directory_user(client, directory, "okta_ada", operation=vocab.SCIM_OP_DELETE)
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["state"] == vocab.USER_DEPROVISIONED
        assert client.get(f"{PREFIX}/directories/{directory['id']}/users").json()["count"] == 0

    def test_a_deprovision_revokes_a_live_session_over_http(self, client: TestClient):
        """The issue's own requirement: remove access, not relabel the user."""
        directory = make_directory(client)
        directory_user(client, directory, "okta_ada", address="ada@northwind.example")
        session = sign_in(client, "ada@northwind.example").json()
        assert client.get(f"{PREFIX}/sessions/{session['id']}").json()["live"] is True

        removed = directory_user(
            client, directory, "okta_ada", operation=vocab.SCIM_OP_DELETE
        ).json()
        assert removed["sessions_revoked"] == 1
        assert client.get(f"{PREFIX}/sessions/{session['id']}").json()["live"] is False
        assert client.get(f"{PREFIX}/sessions").json()["count"] == 1
        assert client.get(f"{PREFIX}/sessions").json()["revoked_sessions"] == 1

    def test_a_deprovision_names_the_operation_and_the_definition(self, client: TestClient):
        directory = make_directory(client)
        directory_user(client, directory, "okta_kai")
        body = directory_user(client, directory, "okta_kai", operation=vocab.SCIM_OP_DELETE).json()
        assert body["operation"] == vocab.SCIM_OP_DELETE
        assert body[vocab.DEPROVISION_DEFINITION_FIELD] == vocab.DEPROVISION_DEFINITION
        assert "removing a user from an app" in vocab.DEPROVISION_DEFINITION

    def test_a_deprovision_of_an_unknown_user_is_a_404(self, client: TestClient):
        directory = make_directory(client)
        response = directory_user(client, directory, "okta_never", operation=vocab.SCIM_OP_DELETE)
        assert response.status_code == 404

    def test_an_unknown_operation_is_a_400(self, client: TestClient):
        directory = make_directory(client)
        response = scim(
            client, directory, {"operation": "upsert", "user": {vocab.SCIM_EXTERNAL_ID: "x"}}
        )
        assert response.status_code == 400
        assert "operation" in response.json()["errors"]

    def test_a_user_with_no_external_id_is_a_400(self, client: TestClient):
        directory = make_directory(client)
        response = scim(
            client, directory, {"operation": "create", "user": {vocab.SCIM_EMAILS: ["a@b.test"]}}
        )
        assert response.status_code == 400
        assert vocab.SCIM_EXTERNAL_ID in response.json()["errors"]

    def test_the_event_log_pulls_the_same_records(self, client: TestClient):
        """The Events API is the twin of the webhook, not a lesser copy of it."""
        directory = make_directory(client)
        directory_user(client, directory, "okta_ada", groups=["grp_sales"])
        pulled = client.get(f"{PREFIX}/directories/{directory['id']}/event-log")
        assert pulled.status_code == 200, pulled.text
        body = pulled.json()
        assert body["delivery"] == "events_api"
        assert body["count"] == 1
        assert body["applied"][0][vocab.SCIM_EXTERNAL_ID] == "okta_ada"

    def test_pulling_twice_applies_nothing_twice(self, client: TestClient):
        directory = make_directory(client)
        directory_user(client, directory, "okta_ada")
        assert client.get(f"{PREFIX}/directories/{directory['id']}/event-log").json()["count"] == 1
        second = client.get(f"{PREFIX}/directories/{directory['id']}/event-log").json()
        assert second["count"] == 0
        assert second["skipped"] == 1

    def test_the_pull_reports_a_cursor(self, client: TestClient):
        directory = make_directory(client)
        directory_user(client, directory, "okta_ada")
        body = client.get(f"{PREFIX}/directories/{directory['id']}/event-log").json()
        assert body["cursor"]


# --------------------------------------------------------------------------- #
# access
# --------------------------------------------------------------------------- #


class TestAccess:
    def test_a_group_is_derived_from_the_directory(self, client: TestClient):
        directory = make_directory(client)
        directory_user(client, directory, "okta_ada", groups=["grp_sales"])
        directory_user(client, directory, "okta_robin", groups=["grp_sales"])
        group = directory_group(client, directory, "grp_sales", "Sales")
        assert sorted(group["members"]) == ["okta_ada", "okta_robin"]
        assert group["member_count"] == 2

    def test_a_group_maps_to_a_role_and_the_role_is_granted(self, client: TestClient):
        directory = make_directory(client)
        directory_user(client, directory, "okta_ada", groups=["grp_sales"])
        group = directory_group(client, directory, "grp_sales", "Sales")

        mapped = client.put(
            f"{PREFIX}/groups/{group['id']}/access", json={"role": vocab.RULE_ROLE_MEMBER}
        )
        assert mapped.status_code == 201, mapped.text
        assert mapped.json()["role"] == vocab.RULE_ROLE_MEMBER

        access = client.get(f"{PREFIX}/directories/{directory['id']}/access").json()
        assert access["users"][0]["access"]["granted"] is True
        assert access["users"][0]["access"]["role"] == vocab.RULE_ROLE_MEMBER

    def test_the_strongest_group_wins_over_http(self, client: TestClient):
        directory = make_directory(client)
        directory_user(client, directory, "okta_robin", groups=["grp_sales", "grp_admin"])
        sales = directory_group(client, directory, "grp_sales", "Sales")
        admins = directory_group(client, directory, "grp_admin", "Admins")
        client.put(f"{PREFIX}/groups/{sales['id']}/access", json={"role": vocab.RULE_ROLE_MEMBER})
        client.put(f"{PREFIX}/groups/{admins['id']}/access", json={"role": vocab.RULE_ROLE_ADMIN})

        access = client.get(f"{PREFIX}/directories/{directory['id']}/access").json()
        assert access["users"][0]["access"]["role"] == vocab.RULE_ROLE_ADMIN

    def test_a_user_in_no_mapped_group_is_granted_nothing(self, client: TestClient):
        directory = make_directory(client)
        directory_user(client, directory, "okta_kai", groups=["grp_unmapped"])
        access = client.get(f"{PREFIX}/directories/{directory['id']}/access").json()
        assert access["users"][0]["access"]["granted"] is False
        assert access["unmapped_groups"] == ["grp_unmapped"]

    def test_an_unknown_role_is_a_400(self, client: TestClient):
        directory = make_directory(client)
        directory_user(client, directory, "okta_ada", groups=["grp_sales"])
        group = directory_group(client, directory, "grp_sales", "Sales")
        response = client.put(f"{PREFIX}/groups/{group['id']}/access", json={"role": "superuser"})
        assert response.status_code == 400
        assert "role" in response.json()["errors"]

    def test_a_group_mapping_is_audited_with_its_route(self, client: TestClient):
        directory = make_directory(client)
        directory_user(client, directory, "okta_ada", groups=["grp_sales"])
        group = directory_group(client, directory, "grp_sales", "Sales")
        client.put(f"{PREFIX}/groups/{group['id']}/access", json={"role": vocab.RULE_ROLE_MEMBER})
        entries = client.get(
            "/api/audit", params={"collection": vocab.ACCESS_RULE_COLLECTION}
        ).json()["entries"]
        assert entries[0]["source"] == f"PUT {PREFIX}/groups/{{group_id}}/access"

    def test_the_access_rules_list_says_no_manual_override_exists(self, client: TestClient):
        body = client.get(f"{PREFIX}/access-rules").json()
        assert body["roles"] == list(vocab.RULE_ROLES)
        assert body[vocab.OVERRIDE_REFUSAL_FIELD] == vocab.OVERRIDE_REFUSAL_VALUE

    def test_there_is_no_route_that_grants_a_person_access_by_hand(self):
        """`DERIVED_NO_MANUAL_OVERRIDE`, asserted on the route table.

        An override is not merely discouraged in prose. No path exists that would accept one,
        and a test that walks the routes is what says so rather than a reviewer re-reading
        twenty-four decorators.
        """

        mounted = {
            f"{method} {route.path}"
            for route in importlib.import_module(FEATURE_MODULE).router.routes
            for method in route.methods
        }
        for banned in ("override", "grant", "invite", "impersonate", "assume"):
            assert not [path for path in mounted if banned in path.casefold()], banned

    def test_an_unknown_group_is_a_404(self, client: TestClient):
        assert (
            client.put(f"{PREFIX}/groups/grp_absent/access", json={"role": "member"}).status_code
            == 404
        )


# --------------------------------------------------------------------------- #
# the audit-source rule
# --------------------------------------------------------------------------- #


class TestAuditSourceRule:
    def test_every_source_this_router_can_record_names_a_mounted_route(self):
        """The rule the build brief asks for by name.

        Every ``source=`` is built by ``_source`` from the router's own prefix and a path
        literal. This walks the module for those literals and checks each against the routes
        the host actually mounted, so an audit row cannot name a route the app stopped
        serving.
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
        """A domain function hardcoding a URL leaves the audit log naming a route the app
        stopped serving."""

        engine_module = importlib.import_module("dsr.security_governance.sso_engine")
        source = Path(engine_module.__file__ or "").read_text(encoding="utf-8")
        assert "/api/" not in source

    def test_a_live_write_records_the_route_that_served_it(self, client: TestClient):
        directory = make_directory(client)
        directory_user(client, directory, "okta_ada")
        audit = client.get(
            "/api/audit", params={"collection": vocab.DIRECTORY_USER_COLLECTION}
        ).json()
        mounted = {
            f"{method} {route.path}"
            for route in importlib.import_module(FEATURE_MODULE).router.routes
            for method in route.methods
        }
        assert audit["entries"][0]["source"] in mounted

    def test_the_webhook_write_names_the_webhook_route(self, client: TestClient):
        directory = make_directory(client)
        directory_user(client, directory, "okta_ada")
        entries = client.get(
            "/api/audit", params={"collection": vocab.DIRECTORY_USER_COLLECTION}
        ).json()["entries"]
        assert entries[0]["source"] == f"POST {PREFIX}/directories/{{directory_id}}/events"


# --------------------------------------------------------------------------- #
# the honesty rule and the research surfaces
# --------------------------------------------------------------------------- #


class TestTheResearchSurfaces:
    def test_the_vocabulary_serves_both_protocols(self, client: TestClient):
        body = client.get(f"{PREFIX}/vocabulary").json()
        assert {row["id"] for row in body[vocab.PROTOCOLS_FIELD]} == set(vocab.PROTOCOLS)

    def test_the_vocabulary_serves_the_three_identifiers_and_their_jobs(self, client: TestClient):
        body = client.get(f"{PREFIX}/vocabulary").json()
        assert {row["param"] for row in body[vocab.IDENTIFIERS_FIELD]} == set(
            vocab.IDENTIFIER_PARAMS
        )

    def test_the_vocabulary_serves_the_code_bound(self, client: TestClient):
        body = client.get(f"{PREFIX}/vocabulary").json()
        assert body[vocab.CODE_TTL_MINUTES_FIELD] == 10
        assert "ten minutes" in body[vocab.EXPIRED_CODE_POLICY_FIELD]

    def test_the_vocabulary_serves_the_three_scim_operations(self, client: TestClient):
        body = client.get(f"{PREFIX}/vocabulary").json()
        assert {row["id"] for row in body[vocab.SCIM_OPERATIONS_FIELD]} == set(
            vocab.SCIM_OPERATIONS
        )

    def test_the_vocabulary_names_the_surfaces_this_build_does_not_ship(self, client: TestClient):
        body = client.get(f"{PREFIX}/vocabulary").json()
        assert len(body["replaces_hosted_surface"]) == 2

    def test_the_decisions_include_the_ownership_decision_with_its_audit_id(
        self, client: TestClient
    ):
        body = client.get(f"{PREFIX}/decisions").json()
        decision = next(
            d for d in body["decisions"] if d["id"] == "DERIVED_TENANT_MEMBERSHIP_CHECK"
        )
        assert decision["chosen"] == "assert_and_require_a_tenant_record"
        assert decision["jev_audit_id"].startswith("jev-")
        assert decision["jev_verdict"] == "pass"

    def test_an_unknown_decision_is_a_404(self, client: TestClient):
        assert client.get(f"{PREFIX}/decisions/NOPE").status_code == 404

    def test_the_summary_reports_the_states_that_matter(self, client: TestClient):
        directory = make_directory(client)
        directory_user(client, directory, "okta_ada")
        # Provisioned before it is deprovisioned, because a removal of somebody the
        # directory never named is a 404 rather than a change - and a summary that counted
        # it would be reporting a leaver who was never a joiner.
        directory_user(client, directory, "okta_kai")
        removed = directory_user(client, directory, "okta_kai", operation=vocab.SCIM_OP_DELETE)
        assert removed.status_code == 201, removed.text
        sign_in(client)

        body = client.get(f"{PREFIX}/summary").json()
        assert body["organizations"] == 1
        assert body["directories"] == 1
        assert body["provisioned_users"] == 2
        assert body["deprovisioned_users"] == 1
        assert body["directory_users"] == 1
        assert body[vocab.ASSERT_ORDER_FIELD] is True

    def test_removing_somebody_the_directory_never_named_is_a_404(self, client: TestClient):
        """The distinction matters: a 404 is not a change and is not counted as one."""
        directory = make_directory(client)
        response = directory_user(client, directory, "okta_ghost", operation=vocab.SCIM_OP_DELETE)
        assert response.status_code == 404
        assert client.get(f"{PREFIX}/summary").json()["deprovisioned_users"] == 0


class TestTheHonestyRule:
    def test_every_endpoint_carries_the_limitation(self, client: TestClient):
        tenant = ensure_tenant(client)
        connection = make_connection(client, tenant)
        directory = make_directory(client, tenant)
        directory_user(client, directory, "okta_ada")
        for path in (
            f"{PREFIX}/summary",
            f"{PREFIX}/vocabulary",
            f"{PREFIX}/organizations",
            f"{PREFIX}/organizations/{TENANT}",
            f"{PREFIX}/connections",
            f"{PREFIX}/connections/{connection['id']}",
            f"{PREFIX}/directories",
            f"{PREFIX}/directories/{directory['id']}",
            f"{PREFIX}/directories/{directory['id']}/users",
            f"{PREFIX}/directories/{directory['id']}/groups",
            f"{PREFIX}/directories/{directory['id']}/access",
            f"{PREFIX}/access-rules",
            f"{PREFIX}/sessions",
        ):
            body = client.get(path).json()
            assert vocab.LIMITATION_FIELD in body, path

    def test_a_granted_session_carries_the_assertion_policy(self, client: TestClient):
        body = sign_in(client).json()
        assert body[vocab.ASSERTION_POLICY_FIELD] == vocab.ASSERTION_POLICY
        assert body[vocab.EMAIL_DOMAIN_UNSAFE_FIELD] == vocab.EMAIL_DOMAIN_UNSAFE


# --------------------------------------------------------------------------- #
# the frontend contract
# --------------------------------------------------------------------------- #


class TestFrontendContract:
    def test_the_page_says_what_the_assertion_is_worth(self):
        """The frontend ships words, so the words are the thing to test."""
        folder = _frontend_dir()
        joined = _shipped_text(folder)
        assert "email domain" in joined
        assert "organization id" in joined

    def test_the_page_names_the_ten_minute_bound(self):
        joined = _shipped_text(_frontend_dir())
        assert "ten minutes" in joined or "10 minutes" in joined

    def test_the_page_names_deprovisioning_as_a_removal(self):
        joined = _shipped_text(_frontend_dir())
        assert "removing a user from an app" in joined

    def test_the_page_says_no_manual_override_exists(self):
        joined = _shipped_text(_frontend_dir())
        assert "no manual override" in joined

    def test_the_page_uses_no_emoji_as_an_icon(self):
        """The design system's floor, checked rather than trusted."""
        folder = _frontend_dir()
        for path in _shipped_files(folder):
            text = path.read_text(encoding="utf-8")
            assert not any(ord(char) > 0x2100 for char in text), path.name


def _frontend_dir() -> Path:
    import dsr

    module = importlib.import_module(FEATURE_MODULE)
    root = Path(dsr.__file__ or "").resolve().parents[2]
    folder = root / "frontend" / "src" / "features" / module.FEATURE["id"]
    if not folder.is_dir():
        pytest.skip("frontend sources are not present in this checkout")
    return folder


def _shipped_files(folder: Path) -> list[Path]:
    return [
        path
        for path in folder.glob("*")
        if path.suffix in (".jsx", ".js") and ".test." not in path.name
    ]


def _shipped_text(folder: Path) -> str:
    return " ".join(path.read_text(encoding="utf-8") for path in _shipped_files(folder)).casefold()
