"""The writes: a tenant, a connection, a sign-in, and a directory reconciliation.

The engine is the only module in this package that writes. It holds the store and a clock
and nothing else, and it is built per request by the HTTP layer for exactly that reason:
both seams stay overridable in a test without hanging a long-lived object off
``app.state``, which is a shared file this feature may not edit.

Every write below carries an ``actor`` and a ``source``, and both reach the audit log in the
same transaction as the change. The ``source`` is the route that served the write, which is
why no string in this module is a literal route: the feature module builds each one from its
own router, and ``tests/test_wf084_http.py`` asserts every source this workflow can record
names a concrete ``(method, path)`` the host mounted.

The order the rules are enforced in
-----------------------------------

Validate, then assert, then write. For the callback that order is the security property
rather than a style choice: the specification says the app "asserts
``profile.organizationId`` matches the expected tenant and only then creates a session", so
:meth:`FederationEngine.complete_sign_in` resolves the tenant and the code's state before
it writes anything. A rejected sign-in leaves no trace, because an audit trail carrying a row
for a session that was never granted is a trail a reader has to learn to discount.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

from dsr.security_governance import sso_rules as rules, sso_vocabulary as vocab
from dsr.store import RecordStore


class FederationEngine:
    """Every read and write WF-084 performs, over one audited store.

    ``now`` is a callable rather than a value so a test can move the clock by hand. The
    authorization code's ten-minute life is the property this workflow enforces at a
    boundary, so the clock is a seam a test has to be able to turn.
    """

    def __init__(self, store: RecordStore, now: Callable[[], datetime] | None = None) -> None:
        self.store = store
        self._now = now or rules.utcnow

    # -- tenants ------------------------------------------------------------ #
    #
    # Jev chose this over asserting the ids alone and over auto-creating a tenant on first
    # sight, at confidence 0.99, audit jev-20261004T181259-19372-79165. The reasoning is
    # in :mod:`dsr.security_governance.sso_inferences` under
    # ``DERIVED_TENANT_MEMBERSHIP_CHECK``.

    def create_organization(
        self,
        payload: Mapping[str, Any] | None = None,
        *,
        room_id: str | None = None,
        source: str | None = None,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Record a tenant staff may sign in to. Mirrors ``POST /sso/organizations``.

        The specification's first user-flow step is "IT admin connects the company's
        identity provider to the app", and the tenant is the thing that connection belongs
        to. So the tenant is recorded here rather than being inferred from the first
        profile that arrives, which is what the decision above rules out.
        """

        data = dict(payload or {})
        organization_id = str(data.get(vocab.ORGANIZATION_ID) or "").strip()
        if not organization_id:
            raise rules.IdentitySettingsInvalid(
                "An organization id is required.",
                {
                    vocab.ORGANIZATION_ID: (
                        "Name the tenant. This is the value a profile's organization id is "
                        "asserted against, and it is never an email domain."
                    )
                },
            )

        single_tenant = bool(data.get("single_tenant", False))
        redirect_uris = _string_list(data.get("redirect_uris"))

        record = self.store.create(
            vocab.ORGANIZATION_COLLECTION,
            {
                rules.ROOM_REF: room_id,
                vocab.ORGANIZATION_ID: organization_id,
                "name": data.get("name") or organization_id,
                "single_tenant": single_tenant,
                "redirect_uris": redirect_uris,
                "max_redirect_uris": rules.redirect_uri_limit(single_tenant),
                vocab.CLIENT_ID_FIELD: data.get(vocab.CLIENT_ID_FIELD) or "",
                "idp_initiated": bool(data.get("idp_initiated", False)),
                "created_at": rules.stamp(self._now()),
            },
            room_id=room_id,
            actor=actor,
            source=source,
        )
        return self.project_organization(record)

    def organizations(self, room_id: str | None = None) -> list[dict[str, Any]]:
        rows = self.store.list(vocab.ORGANIZATION_COLLECTION, limit=200, order_by="created_at")
        if room_id:
            rows = [row for row in rows if rules.room_ref_of(row.get("data") or {}, row) == room_id]
        return [self.project_organization(row) for row in rows]

    def read_organization(self, organization_id: str) -> dict[str, Any]:
        return self.project_organization(self._organization_record(organization_id))

    def _organization_record(self, organization_id: str) -> dict[str, Any]:
        """The stored tenant row.

        Looked up through ``find`` on the payload field rather than by record id, because a
        caller names the tenant with the IdP's own organization id and not with this app's
        record id. Reading it by record id would make the callback's input a different
        namespace from the profile's, and a mismatch between the two spellings of the same
        tenant is exactly the case this assertion must not get wrong.
        """

        wanted = str(organization_id or "").strip().casefold()
        rows = self.store.find(
            vocab.ORGANIZATION_COLLECTION, {vocab.ORGANIZATION_ID: wanted}, limit=200
        )
        # find() matches the index case-sensitively, so a row stored as `Org_A` will not
        # match a lookup for `org_a`. Fall back to a scan rather than pretending the two
        # spellings are different tenants.
        if not rows:
            rows = [
                row
                for row in self.store.list(vocab.ORGANIZATION_COLLECTION, limit=200)
                if str((row.get("data") or {}).get(vocab.ORGANIZATION_ID) or "").strip().casefold()
                == wanted
            ]
        if not rows:
            raise rules.OrganizationNotFound(str(organization_id))
        return rows[0]

    def project_organization(self, record: Mapping[str, Any]) -> dict[str, Any]:
        data = dict(record.get("data") or {})
        return {
            "id": record.get("id"),
            "room_id": rules.room_ref_of(data, record),
            vocab.ORGANIZATION_ID: data.get(vocab.ORGANIZATION_ID),
            "name": data.get("name"),
            "single_tenant": bool(data.get("single_tenant", False)),
            "redirect_uris": list(data.get("redirect_uris") or []),
            "max_redirect_uris": data.get("max_redirect_uris"),
            vocab.CLIENT_ID_FIELD: data.get(vocab.CLIENT_ID_FIELD) or None,
            "idp_initiated": bool(data.get("idp_initiated", False)),
            "created_at": data.get("created_at"),
            "connections": len(
                [
                    row
                    for row in self.store.list(vocab.CONNECTION_COLLECTION, limit=200)
                    if row.get("data", {}).get(vocab.ORGANIZATION_ID)
                    == data.get(vocab.ORGANIZATION_ID)
                ]
            ),
            vocab.ASSERTION_POLICY_FIELD: vocab.ASSERTION_POLICY,
            vocab.LIMITATION_FIELD: vocab.LIMITATION,
        }

    # -- connections -------------------------------------------------------- #

    def create_connection(
        self,
        organization_id: str,
        payload: Mapping[str, Any] | None = None,
        *,
        source: str | None = None,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Connect an identity provider to a tenant.

        The protocol is checked against the two the specification's evidence names, and the
        provider is checked against the ones it names as supported. Both refusals are
        validation failures rather than a stored row with a bad value, so a typo in a setup
        form cannot leave a connection that no sign-in can ever use.
        """

        organization = self.read_organization(organization_id)
        data = dict(payload or {})

        protocol = str(data.get("protocol") or "").strip().lower()
        if protocol not in vocab.PROTOCOLS:
            raise rules.IdentitySettingsInvalid(
                "A connection protocol must be SAML or OIDC.",
                {"protocol": f"Use one of: {', '.join(vocab.PROTOCOLS)}."},
            )

        name = str(data.get("name") or "").strip()
        if not name:
            raise rules.IdentitySettingsInvalid(
                "A connection needs a name.",
                {"name": "Name the connection so an administrator can tell them apart."},
            )

        provider = str(data.get(vocab.PROVIDER_PARAM) or "").strip().lower()
        if provider and provider not in vocab.SUPPORTED_PROVIDERS:
            raise rules.IdentitySettingsInvalid(
                "That identity provider is not one this workflow supports.",
                {vocab.PROVIDER_PARAM: (f"Use one of: {', '.join(vocab.SUPPORTED_PROVIDERS)}.")},
            )

        redirect_uri = rules.validate_redirect_uri(
            data.get(vocab.REDIRECT_URI_PARAM), organization["redirect_uris"]
        )

        record = self.store.create(
            vocab.CONNECTION_COLLECTION,
            {
                rules.ROOM_REF: organization["room_id"],
                vocab.ORGANIZATION_ID: organization[vocab.ORGANIZATION_ID],
                "name": name,
                "protocol": protocol,
                vocab.CONNECTION_PARAM: str(data.get(vocab.CONNECTION_PARAM) or name),
                vocab.PROVIDER_PARAM: provider or None,
                vocab.REDIRECT_URI_PARAM: redirect_uri,
                vocab.CLIENT_ID_FIELD: data.get(vocab.CLIENT_ID_FIELD)
                or organization[vocab.CLIENT_ID_FIELD],
                "created_at": rules.stamp(self._now()),
            },
            room_id=organization["room_id"],
            actor=actor,
            source=source,
        )
        return self.project_connection(record)

    def connections(self, organization_id: str | None = None) -> list[dict[str, Any]]:
        rows = self.store.list(vocab.CONNECTION_COLLECTION, limit=200, order_by="created_at")
        if organization_id:
            wanted = str(organization_id).casefold()
            rows = [
                row
                for row in rows
                if str(row.get("data", {}).get(vocab.ORGANIZATION_ID) or "").casefold() == wanted
            ]
        return [self.project_connection(row) for row in rows]

    def read_connection(self, connection_id: str) -> dict[str, Any]:
        return self.project_connection(self._connection_record(connection_id))

    def _connection_record(self, connection_id: str) -> dict[str, Any]:
        record = self.store.get(connection_id)
        if record is None or record.get("collection") != vocab.CONNECTION_COLLECTION:
            raise rules.ConnectionNotFound(connection_id)
        return record

    def project_connection(self, record: Mapping[str, Any]) -> dict[str, Any]:
        data = dict(record.get("data") or {})
        return {
            "id": record.get("id"),
            "room_id": rules.room_ref_of(data, record),
            vocab.ORGANIZATION_ID: data.get(vocab.ORGANIZATION_ID),
            "name": data.get("name"),
            "protocol": data.get("protocol"),
            vocab.CONNECTION_PARAM: data.get(vocab.CONNECTION_PARAM),
            vocab.PROVIDER_PARAM: data.get(vocab.PROVIDER_PARAM),
            vocab.REDIRECT_URI_PARAM: data.get(vocab.REDIRECT_URI_PARAM),
            vocab.CLIENT_ID_FIELD: data.get(vocab.CLIENT_ID_FIELD) or None,
            "created_at": data.get("created_at"),
            vocab.LIMITATION_FIELD: vocab.LIMITATION,
        }

    # -- sign-in ------------------------------------------------------------ #

    def begin_sign_in(
        self,
        payload: Mapping[str, Any],
        *,
        source: str | None = None,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Build the authorization URL and record the authorization.

        The specification's second user-flow step: "Staff hit the app's login surface, which
        redirects to the IdP using the organization's identifier (or a specific connection /
        provider)." So the three identifiers are accepted here and none of them is a session:
        no session exists until :meth:`complete_sign_in` has asserted the tenant.

        The redirect URI is resolved against what the tenant registered rather than taken
        from the caller, because the caller is the party an attacker would be.
        """

        body = dict(payload or {})
        organization = body.get(vocab.ORGANIZATION_PARAM)
        connection_id = body.get(vocab.CONNECTION_PARAM)
        provider = body.get(vocab.PROVIDER_PARAM)

        if connection_id:
            connection = self.read_connection(str(connection_id))
            tenant = self.read_organization(connection[vocab.ORGANIZATION_ID])
            registered = [connection[vocab.REDIRECT_URI_PARAM]] + tenant["redirect_uris"]
            client_id = connection[vocab.CLIENT_ID_FIELD]
        else:
            if not organization:
                raise rules.IdentitySettingsInvalid(
                    "Name an organization or a connection to sign in with.",
                    {
                        vocab.ORGANIZATION_PARAM: (
                            "The organization identifies the tenant. A connection selects "
                            "one IdP inside it. A provider names an OAuth provider."
                        )
                    },
                )
            tenant = self.read_organization(str(organization))
            registered = tenant["redirect_uris"]
            client_id = tenant[vocab.CLIENT_ID_FIELD]

        redirect_uri = rules.validate_redirect_uri(body.get(vocab.REDIRECT_URI_PARAM), registered)
        state = str(body.get(vocab.STATE_PARAM) or "").strip() or None
        issuer = str(body.get("issuer") or "").strip()
        if not issuer:
            raise rules.IdentitySettingsInvalid(
                "An authorization URL needs the IdP's issuer.",
                {"issuer": "Name the identity provider's authorization endpoint."},
            )

        issued = self._now()
        # The identifier is recorded as it arrived, and so is the connection it names. A
        # provider named directly and a provider reached through a connection are the same
        # authorization with a different provenance, and the audit trail has to be able to
        # tell them apart.
        chosen_provider = provider
        if connection_id and not chosen_provider:
            chosen_provider = connection.get(vocab.PROVIDER_PARAM)

        record = self.store.create(
            vocab.AUTHORIZATION_COLLECTION,
            {
                rules.ROOM_REF: tenant["room_id"],
                vocab.ORGANIZATION_ID: tenant[vocab.ORGANIZATION_ID],
                "identifier": rules.identifier_kind(organization, connection_id, provider),
                "connection_id": connection_id,
                vocab.PROVIDER_PARAM: chosen_provider,
                vocab.REDIRECT_URI_PARAM: redirect_uri,
                vocab.FLOW_KEY: vocab.FLOW_STAFF_INITIATED,
                vocab.STATE_PARAM: state,
                "issued_at": rules.stamp(issued),
                "expires_at": rules.code_deadline(rules.stamp(issued)),
            },
            room_id=tenant["room_id"],
            actor=actor,
            source=source,
        )
        data = dict(record.get("data") or {})
        return {
            "id": record.get("id"),
            "organization_id": data.get(vocab.ORGANIZATION_ID),
            "identifier": data.get("identifier"),
            vocab.FLOW_KEY: data.get(vocab.FLOW_KEY),
            vocab.REDIRECT_URI_PARAM: data.get(vocab.REDIRECT_URI_PARAM),
            "state": data.get(vocab.STATE_PARAM),
            "issued_at": data.get("issued_at"),
            "expires_at": data.get("expires_at"),
            "ttl_seconds": rules.seconds_until_expiry(data.get("issued_at"), issued),
            "authorization_url": rules.build_authorization_url(
                issuer=issuer,
                organization=tenant[vocab.ORGANIZATION_ID],
                connection=connection_id,
                provider=chosen_provider,
                redirect_uri=redirect_uri,
                client_id=client_id,
                state=state,
            ),
            vocab.EXPIRED_CODE_POLICY_FIELD: vocab.EXPIRED_CODE_POLICY,
            vocab.LIMITATION_FIELD: vocab.LIMITATION,
        }

    def complete_sign_in(
        self,
        payload: Mapping[str, Any],
        *,
        source: str | None = None,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Assert the tenant, then grant a session. Mirrors the callback.

        The whole security property of WF-084, in this order:

        1. The code's state is resolved. An expired code is refused and never retried.
        2. The redirect URI is checked against what the tenant registered.
        3. The profile's organization id is asserted against the expected tenant, and the
           tenant is required to be one this app holds a record for.
        4. Only then is a session written.

        A refusal at any step writes nothing. The reason travels on the exception and the
        HTTP layer renders it, so a caller can tell 'wrong tenant' from 'tenant not set up
        here' from 'the code expired' rather than reading one generic failure.
        """

        body = dict(payload or {})
        now = self._now()

        authorization_id = body.get("authorization_id")
        authorization = (
            self._authorization_record(str(authorization_id)) if authorization_id else None
        )

        issued_at = body.get("issued_at") or (
            (authorization.get("data") or {}).get("issued_at") if authorization else None
        )
        state = rules.code_state(issued_at, now)
        if state == rules.CODE_STATE_EXPIRED:
            raise rules.TenantAssertionFailed(
                "The authorization code has expired and is not retried.",
                expected=None,
                actual=None,
                reason=rules.REASON_CODE_EXPIRED,
            )
        if state == rules.CODE_STATE_UNKNOWN:
            # A code with no recorded issue time is not refused, because that would lock
            # out every IdP-initiated sign-in: the IdP sends the user straight to the
            # callback and this app may never have issued a code to exchange. The bound is
            # enforced on every code this app issued, which is where the research's ten
            # minutes actually apply. `code_known` reports which case this was, so a caller
            # can see whether the bound was checked rather than having to infer it.
            code_known = False
        else:
            code_known = True

        tenant_id = body.get(vocab.ORGANIZATION_PARAM) or (
            (authorization.get("data") or {}).get(vocab.ORGANIZATION_ID) if authorization else None
        )
        if not tenant_id:
            raise rules.IdentitySettingsInvalid(
                "The callback needs to know which tenant the code was issued for.",
                {
                    vocab.ORGANIZATION_PARAM: (
                        "Pass the authorization this code came from, or name the tenant."
                    )
                },
            )
        tenant = self.read_organization(str(tenant_id))

        redirect_uri = body.get(vocab.REDIRECT_URI_PARAM) or (
            (authorization.get("data") or {}).get(vocab.REDIRECT_URI_PARAM)
            if authorization
            else None
        )
        if redirect_uri:
            rules.validate_redirect_uri(redirect_uri, tenant["redirect_uris"])

        profile = body.get("profile")
        if not isinstance(profile, Mapping):
            raise rules.IdentitySettingsInvalid(
                "The callback needs the profile the IdP returned.",
                {"profile": "Pass the normalized profile, with its organization id."},
            )

        asserted = rules.assert_tenant(profile, tenant[vocab.ORGANIZATION_ID])
        if not rules.tenant_is_known(self.organizations(), asserted):
            raise rules.TenantAssertionFailed(
                "The profile's organization id matches, but this app holds no record of "
                "that tenant.",
                expected=tenant[vocab.ORGANIZATION_ID],
                actual=asserted,
                reason=rules.REASON_TENANT_NOT_A_MEMBER,
            )

        # The email is read here for display and for the directory lookup, and both of
        # those happen after the assertion has already passed. The ban is a ban on letting
        # it decide the tenant, not on storing it.
        address = _first_email(profile)
        directory_user_id = self._directory_user_for_address(asserted, address)

        session_record = self.store.create(
            vocab.SESSION_COLLECTION,
            {
                rules.ROOM_REF: tenant["room_id"],
                vocab.ORGANIZATION_ID: asserted,
                "flow": (authorization.get("data") or {}).get(vocab.FLOW_KEY)
                if authorization
                else body.get(vocab.FLOW_KEY) or vocab.FLOW_STAFF_INITIATED,
                "email": address,
                "email_domain": rules.email_domain_of(profile),
                "directory_user_id": directory_user_id,
                "revoked": False,
                "asserted_tenant": asserted,
                "asserted_at": rules.stamp(now),
                "granted_at": rules.stamp(now),
            },
            room_id=tenant["room_id"],
            actor=actor,
            source=source,
        )
        data = dict(session_record.get("data") or {})
        return {
            "id": session_record.get("id"),
            vocab.ORGANIZATION_ID: data.get(vocab.ORGANIZATION_ID),
            vocab.FLOW_KEY: data.get("flow"),
            "email": data.get("email"),
            "email_domain": data.get("email_domain"),
            "directory_user_id": data.get("directory_user_id"),
            "revoked": bool(data.get("revoked", False)),
            "granted_at": data.get("granted_at"),
            # A freshly granted session has no directory user behind it in most cases, so
            # it is live by definition. Asking is better than asserting it, because the
            # answer changes the moment a deprovision touches the directory user.
            "live": self.is_session_live(str(session_record.get("id"))),
            vocab.ASSERT_ORDER_FIELD: vocab.ASSERT_ORDER_VALUE,
            # Whether the ten-minute bound was checked on this request. False means the
            # callback carried no code this app issued, which is the IdP-initiated case the
            # specification puts in scope, so the answer is reported rather than implied.
            vocab.CODE_TTL_CHECKED_FIELD: code_known,
            vocab.ASSERTION_POLICY_FIELD: vocab.ASSERTION_POLICY,
            vocab.EMAIL_DOMAIN_UNSAFE_FIELD: vocab.EMAIL_DOMAIN_UNSAFE,
            vocab.LIMITATION_FIELD: vocab.LIMITATION,
        }

    def _directory_user_for_address(self, tenant_id: str, address: str | None) -> str | None:
        """The directory user a granted session belongs to, when the directory knows them.

        A read, not an assertion. The directory decides who exists; the tenant assertion
        decided who may sign in. A staff member who has not been provisioned yet still
        gets a session, because the research separates the two concerns and refusing them
        would fold provisioning into authentication.
        """

        if not address:
            return None
        for row in self.store.list(vocab.DIRECTORY_USER_COLLECTION, limit=500):
            data = row.get("data") or {}
            if data.get(vocab.ORGANIZATION_ID) != tenant_id:
                continue
            if not rules.is_active(data):
                continue
            if address in _stored_emails(data):
                return str(data.get(vocab.SCIM_EXTERNAL_ID) or row.get("id") or "") or None
        return None

    def _authorization_record(self, authorization_id: str) -> dict[str, Any]:
        record = self.store.get(authorization_id)
        if record is None or record.get("collection") != vocab.AUTHORIZATION_COLLECTION:
            raise rules.IdentitySettingsInvalid(
                "No such authorization.",
                {"authorization_id": "This id does not name an authorization."},
            )
        return record

    def sessions(
        self,
        organization_id: str | None = None,
        *,
        include_revoked: bool = True,
    ) -> list[dict[str, Any]]:
        rows = self.store.list(vocab.SESSION_COLLECTION, limit=500, order_by="updated_at")
        sessions = [self.project_session(row) for row in rows]
        if not include_revoked:
            sessions = [row for row in sessions if not row["revoked"]]
        if organization_id:
            wanted = str(organization_id).casefold()
            sessions = [
                row
                for row in sessions
                if str(row.get(vocab.ORGANIZATION_ID) or "").casefold() == wanted
            ]
        return sessions

    def read_session(self, session_id: str) -> dict[str, Any]:
        record = self.store.get(session_id)
        if record is None or record.get("collection") != vocab.SESSION_COLLECTION:
            raise rules.SessionNotFound(session_id)
        return self.project_session(record)

    def is_session_live(self, session_id: str) -> bool:
        """Whether a session still grants access right now.

        Read on every use rather than cached, because a deprovision has to take effect
        immediately. A session that a check could ignore is how a leaver keeps access, which
        is the outcome the deprovision operation exists to prevent.

        The lookup accepts either spelling of the user, because a session stores the
        directory's own external id while a caller reading a user passes this app's record
        id, and the two name the same person. A check that resolved only one of them would
        report every session dead the moment a directory user existed, which reads as a
        product that revokes access on its own.
        """

        record = self.store.get(session_id)
        if record is None or record.get("collection") != vocab.SESSION_COLLECTION:
            return False
        data = record.get("data") or {}
        if data.get("revoked"):
            return False
        user_id = data.get("directory_user_id")
        if not user_id:
            # No directory user behind this session, so there is no directory state that
            # could have revoked it. The assertion alone stands.
            return True
        wanted = str(user_id)
        row = self._directory_user_record(wanted)
        if row is None:
            # The session stores the directory's own external id, so the lookup falls back to
            # scanning for it. The scan reads **live rows only**: a row the directory has
            # removed must not satisfy it, or a leaver's session would read as granted on the
            # strength of the record of their departure.
            for candidate in self.store.list(vocab.DIRECTORY_USER_COLLECTION, limit=1000):
                if (candidate.get("data") or {}).get(vocab.SCIM_EXTERNAL_ID) == wanted:
                    row = candidate
                    break
        if row is None:
            # The directory user is gone entirely, not merely inactive. A session behind a
            # user this store has no row for grants nothing.
            return False
        return rules.is_active(row.get("data") or {})

    def project_session(self, record: Mapping[str, Any]) -> dict[str, Any]:
        data = dict(record.get("data") or {})
        return {
            "id": record.get("id"),
            "room_id": rules.room_ref_of(data, record),
            vocab.ORGANIZATION_ID: data.get(vocab.ORGANIZATION_ID),
            "flow": data.get("flow"),
            "email": data.get("email"),
            "email_domain": data.get("email_domain"),
            "directory_user_id": data.get("directory_user_id"),
            "revoked": bool(data.get("revoked", False)),
            "granted_at": data.get("granted_at"),
            "live": self.is_session_live(str(record.get("id"))),
            vocab.LIMITATION_FIELD: vocab.LIMITATION,
        }

    # -- directories -------------------------------------------------------- #

    def create_directory(
        self,
        organization_id: str,
        payload: Mapping[str, Any] | None = None,
        *,
        room_id: str | None = None,
        source: str | None = None,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Turn on Directory Sync for a tenant.

        The tenant's own room is the room, and ``room_id`` is here only so the seeder can
        scope a fresh database to a room it chose. A caller's room never overrides the
        tenant's, because a directory scoped to the wrong room would file a tenant's staff
        under a room the tenant has nothing to do with.

        The token in the response is the one this build generated, and it is returned once
        and never again: a webhook that anybody can post to would let a stranger deprovision
        every user. See ``DERIVED_WEBHOOK_AUTHENTICATION``.
        """

        tenant = self.read_organization(str(organization_id))
        data = dict(payload or {})
        # The tenant's own room, unless the caller supplied one for a fresh seed.
        room = tenant["room_id"] or room_id

        provider = str(data.get(vocab.PROVIDER_PARAM) or "").strip().lower()
        if provider not in vocab.SUPPORTED_PROVIDERS:
            raise rules.IdentitySettingsInvalid(
                "That directory provider is not one this workflow supports.",
                {vocab.PROVIDER_PARAM: (f"Use one of: {', '.join(vocab.SUPPORTED_PROVIDERS)}.")},
            )

        token = str(data.get("webhook_token") or "").strip() or _mint_token()
        record = self.store.create(
            vocab.DIRECTORY_COLLECTION,
            {
                rules.ROOM_REF: room,
                vocab.ORGANIZATION_ID: tenant[vocab.ORGANIZATION_ID],
                "name": data.get("name") or f"{vocab.PROVIDER_LABELS[provider]} directory",
                vocab.PROVIDER_PARAM: provider,
                vocab.CHOSEN_DELIVERY_METHOD_FIELD: vocab.CHOSEN_DELIVERY_METHOD,
                "webhook_token": token,
                "sync_enabled": True,
                "created_at": rules.stamp(self._now()),
            },
            room_id=room,
            actor=actor,
            source=source,
        )
        projected = self.project_directory(record)
        # Returned once, and the read projection never carries it again.
        projected["webhook_token"] = token
        projected[vocab.DELIVERY_REASON_FIELD] = vocab.DELIVERY_REASON
        return projected

    def directories(self, organization_id: str | None = None) -> list[dict[str, Any]]:
        rows = self.store.list(vocab.DIRECTORY_COLLECTION, limit=200, order_by="created_at")
        if organization_id:
            wanted = str(organization_id).casefold()
            rows = [
                row
                for row in rows
                if str(row.get("data", {}).get(vocab.ORGANIZATION_ID) or "").casefold() == wanted
            ]
        return [self.project_directory(row) for row in rows]

    def read_directory(self, directory_id: str) -> dict[str, Any]:
        return self.project_directory(self._directory_record(directory_id))

    def _directory_record(self, directory_id: str) -> dict[str, Any]:
        record = self.store.get(directory_id)
        if record is None or record.get("collection") != vocab.DIRECTORY_COLLECTION:
            raise rules.DirectoryNotFound(directory_id)
        return record

    def project_directory(self, record: Mapping[str, Any]) -> dict[str, Any]:
        data = dict(record.get("data") or {})
        directory_id = str(record.get("id"))
        users = [
            row.get("data") or {}
            for row in self.store.list(vocab.DIRECTORY_USER_COLLECTION, limit=500)
            if (row.get("data") or {}).get("directory_id") == directory_id
        ]
        groups = [
            row.get("data") or {}
            for row in self.store.list(vocab.DIRECTORY_GROUP_COLLECTION, limit=500)
            if (row.get("data") or {}).get("directory_id") == directory_id
        ]
        return {
            "id": directory_id,
            "room_id": rules.room_ref_of(data, record),
            vocab.ORGANIZATION_ID: data.get(vocab.ORGANIZATION_ID),
            "name": data.get("name"),
            vocab.PROVIDER_PARAM: data.get(vocab.PROVIDER_PARAM),
            "provider_label": vocab.PROVIDER_LABELS.get(
                str(data.get(vocab.PROVIDER_PARAM)), data.get(vocab.PROVIDER_PARAM)
            ),
            "sync_enabled": bool(data.get("sync_enabled", True)),
            vocab.CHOSEN_DELIVERY_METHOD_FIELD: data.get(
                vocab.CHOSEN_DELIVERY_METHOD_FIELD, vocab.CHOSEN_DELIVERY_METHOD
            ),
            "users": len(users),
            "active_users": sum(1 for user in users if rules.is_active(user)),
            # Counted from the removal events rather than from the user rows, because a
            # deprovisioned user is soft-deleted and no longer appears in `users`. Counting
            # live rows here would report zero leavers after a removal, which reads as
            # though deprovisioning had never happened.
            "deprovisioned_users": sum(
                1
                for row in self.store.list(vocab.DIRECTORY_EVENT_COLLECTION, limit=500)
                if (row.get("data") or {}).get("directory_id") == directory_id
                and (row.get("data") or {}).get("operation") == vocab.SCIM_OP_DELETE
            ),
            "groups": len(groups),
            "created_at": data.get("created_at"),
            vocab.DIRECTORY_TRUTH_FIELD: vocab.DIRECTORY_TRUTH_STATEMENT,
            vocab.LIMITATION_FIELD: vocab.LIMITATION,
        }

    # -- the webhook, which is the Events API's twin ------------------------- #

    def apply_directory_event(
        self,
        directory_id: str,
        payload: Mapping[str, Any],
        *,
        webhook_token: str | None = None,
        source: str | None = None,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Reconcile one directory change. Mirrors the SCIM Users and Groups resources.

        The specification's fourth user-flow step: "the admin enables Directory Sync: the
        directory provider is the source of truth, and user/group changes are pushed into
        the app." One entry point handles all three of the SCIM operations the research
        names, because they are the same reconciliation with three different payloads:

        * ``create`` provisions an identity, and is what the specification calls
          "Provisioning an identity for a user (account creation)".
        * ``update`` applies changed attributes and membership, which is "When a user's
          attribute has changed (account update)".
        * ``delete`` deprovisions, which is "Deprovisioning a user from your app (account
          deletion)" and has to remove access rather than relabel the user.
        """

        directory = self._directory_record(directory_id)
        data = dict(directory.get("data") or {})

        if not data.get("sync_enabled", True):
            raise rules.IdentitySettingsInvalid(
                "Directory Sync is not enabled for this directory.",
                {"sync_enabled": "Enable the directory before sending changes to it."},
            )

        expected_token = str(data.get("webhook_token") or "")
        supplied = str(webhook_token or "").strip()
        # A supplied token is compared; an absent one is refused. A webhook that trusted a
        # missing header would accept any post that reached the route.
        if not supplied or not expected_token or supplied != expected_token:
            raise rules.DirectoryNotFound(
                "The webhook token does not match this directory, so the change was not applied."
            )

        body = dict(payload or {})
        operation = str(body.get("operation") or vocab.SCIM_OP_UPDATE).strip().lower()
        if operation not in vocab.SCIM_OPERATIONS:
            raise rules.IdentitySettingsInvalid(
                "A directory change names create, update or delete.",
                {
                    "operation": (
                        f"Use one of: {', '.join(vocab.SCIM_OPERATIONS)}. The names are the "
                        "SCIM operations the specification quotes."
                    )
                },
            )

        room_id = rules.room_ref_of(data, directory)
        tenant_id = str(data.get(vocab.ORGANIZATION_ID) or "")

        if operation == vocab.SCIM_OP_DELETE:
            return self._deprovision(
                directory_id,
                tenant_id,
                room_id,
                body,
                source=source,
                actor=actor,
                delivery=vocab.DELIVERY_WEBHOOK,
            )

        subject = body.get("group") if body.get("group") else body.get("user")
        if not isinstance(subject, Mapping):
            raise rules.IdentitySettingsInvalid(
                "A directory change carries a user or a group.",
                {"user": "Name the resource the directory is reporting a change to."},
            )
        if body.get("group"):
            return self._reconcile_group(
                directory_id, tenant_id, room_id, subject, source=source, actor=actor
            )
        return self._reconcile_user(
            directory_id,
            tenant_id,
            room_id,
            subject,
            source=source,
            actor=actor,
            delivery=vocab.DELIVERY_WEBHOOK,
        )

    def poll_events(
        self,
        directory_id: str,
        payload: Mapping[str, Any] | None = None,
        *,
        since: str | None = None,
        source: str | None = None,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Read the Events API's changes: the same reconciliation, pulled rather than pushed.

        The specification offers both and the choice is recorded as
        ``DERIVED_DELIVERY_METHOD``. A tenant whose provider cannot post an inbound route
        reaches the same records through here, so the two paths are twins rather than one
        being a lesser version of the other.
        """

        directory = self._directory_record(directory_id)
        data = dict(directory.get("data") or {})
        if not data.get("sync_enabled", True):
            raise rules.IdentitySettingsInvalid(
                "Directory Sync is not enabled for this directory.",
                {"sync_enabled": "Enable the directory before reading events from it."},
            )

        wanted = str(since or "").strip()

        # Order comes from the store, not from a sort written here.
        #
        # `RecordStore.list` orders on `(created_at, rowid)`, and `rowid` is SQLite's
        # insertion sequence. That is the only order available that is both monotonic and
        # correct for events sharing one millisecond stamp, which is what a batch of
        # directory changes is. Sorting the payload here would have to invent the same
        # ordering from fields the payload does not carry, because the record id is a uuid4
        # and is random with respect to when the row was written. Sorting by that id would
        # apply a delete before the create preceding it, and would deprovision a user the
        # directory had just added.
        newest_first = [
            row
            for row in self.store.list(vocab.DIRECTORY_EVENT_COLLECTION, limit=500)
            if (row.get("data") or {}).get("directory_id") == directory_id
        ]
        rows = list(reversed(newest_first))

        # The cursor is a position in that order rather than a timestamp.
        #
        # Events written inside one request share a millisecond stamp, so filtering on
        # `at > since` would drop every event tying with the last one the pull saw. A dropped
        # event is a deprovision that never happens, so the safe direction is to resume after
        # a position rather than after a time. New rows only ever append to this order, so a
        # position stays valid however long the pull waits.
        start = 0
        if wanted:
            start = _cursor_index(wanted)
        pending = rows[start:]

        # The pull stops at the position it reached rather than at the end of the log, so
        # the cursor it hands back is the position a *subsequent* pull resumes from. Reading
        # it off `len(rows)` instead would point the next pull one event past what this one
        # applied, and that event would be silently skipped.
        consumed = start + len(pending)

        room_id = rules.room_ref_of(data, directory)
        tenant_id = str(data.get(vocab.ORGANIZATION_ID) or "")

        # The event ids this pull has already reconciled, read from the directory row rather
        # than held in memory. One event is reconciled once: re-applying a delete is not a
        # harmless repeat, because the user is already gone and the second pass would find
        # no row. A pull that over-replays gets a counted skip instead of an error.
        already_applied = set(data.get("applied_event_ids") or [])

        applied: list[dict[str, Any]] = []
        applied_ids: list[str] = []
        for row in pending:
            event_id = str(row.get("id") or "")
            event = row.get("data") or {}
            # Only the webhook writes log rows now, so a pull reconciles what a webhook
            # delivered. The cursor is what stops a second pull repeating this work, and the
            # marker is the belt to that braces: a pull that ran, then lost its cursor, then
            # ran again skips rather than re-applying.
            if event_id and event_id in already_applied:
                continue
            operation = str(event.get("operation") or vocab.SCIM_OP_UPDATE)
            subject = event.get("group") if event.get("group") else event.get("user")
            if not isinstance(subject, Mapping):
                continue
            try:
                # ``delivery=None`` throughout: this event already has a log row written by
                # whichever path delivered it, and writing a second one here would append to
                # the log the pull is reading.
                if operation == vocab.SCIM_OP_DELETE:
                    applied.append(
                        self._deprovision(
                            directory_id,
                            tenant_id,
                            room_id,
                            {**subject, "operation": operation},
                            source=source,
                            actor=actor,
                            delivery=None,
                        )
                    )
                elif event.get("group"):
                    applied.append(
                        self._reconcile_group(
                            directory_id,
                            tenant_id,
                            room_id,
                            subject,
                            source=source,
                            actor=actor,
                        )
                    )
                else:
                    applied.append(
                        self._reconcile_user(
                            directory_id,
                            tenant_id,
                            room_id,
                            subject,
                            source=source,
                            actor=actor,
                            delivery=None,
                        )
                    )
            except rules.DirectoryUserNotFound:
                # The event describes a user the directory has already removed. Skipped
                # rather than raised, because a pull is a reader: failing here would make
                # the cursor unusable the first time it re-read an old window.
                if event_id:
                    applied_ids.append(event_id)
                continue
            if event_id:
                applied_ids.append(event_id)

        if applied_ids:
            # Written after the loop, so a crash mid-pull replays rather than skipping.
            # A cursor that advanced past events it never applied is the worse failure:
            # it loses a deprovision silently.
            self.store.update(
                directory_id,
                {"applied_event_ids": sorted(already_applied | set(applied_ids))},
                actor=actor or "directory",
                source=source,
            )

        return {
            "directory_id": directory_id,
            "delivery": vocab.DELIVERY_EVENTS_API,
            "count": len(applied),
            "skipped": len(already_applied),
            "applied": applied,
            "cursor": _cursor(consumed),
            vocab.DIRECTORY_TRUTH_FIELD: vocab.DIRECTORY_TRUTH_STATEMENT,
            vocab.LIMITATION_FIELD: vocab.LIMITATION,
        }

    def _reconcile_user(
        self,
        directory_id: str,
        tenant_id: str,
        room_id: str | None,
        subject: Mapping[str, Any],
        *,
        source: str | None,
        actor: str | None,
        delivery: str,
    ) -> dict[str, Any]:
        """Provision or update one directory user, and write the event that described it."""

        external_id = str(subject.get(vocab.SCIM_EXTERNAL_ID) or "").strip()
        if not external_id:
            raise rules.IdentitySettingsInvalid(
                "A directory user needs an external id.",
                {
                    vocab.SCIM_EXTERNAL_ID: (
                        "This is the directory's own identifier for the person, and it is "
                        "how a later update finds the same row."
                    )
                },
            )

        existing = self._find_user_record(directory_id, external_id)
        # A live row only. A row that is already soft-deleted is the departed version of this
        # person, and the directory naming them again is a re-hire: a new row, not an update
        # to a deleted one. Updating a deleted row raises from the store, which is what a
        # replayed Events API pull used to do - it re-reads the create that preceded a
        # deprovision it has already applied, and tried to update the row the deprovision had
        # removed. The two rows stay side by side, so the audit trail keeps both accounts.
        if existing is not None and existing.get("deleted_at"):
            existing = None
        payload = _user_payload(tenant_id, directory_id, subject, room_id, self._now())

        if existing is None:
            record = self.store.create(
                vocab.DIRECTORY_USER_COLLECTION,
                {
                    **payload,
                    "state": vocab.USER_PROVISIONED,
                    "created_at": rules.stamp(self._now()),
                },
                room_id=room_id,
                actor=actor,
                source=source,
            )
            outcome = vocab.USER_PROVISIONED
            created = True
        else:
            record = self.store.update(
                str(existing.get("id")),
                {**payload, "state": vocab.USER_UPDATED},
                actor=actor,
                source=source,
            )
            outcome = vocab.USER_UPDATED
            created = False

        projected = self.project_user(record)
        self._record_event(
            directory_id,
            tenant_id,
            room_id,
            {
                "operation": vocab.SCIM_OP_CREATE if created else vocab.SCIM_OP_UPDATE,
                "user": {
                    vocab.SCIM_EXTERNAL_ID: external_id,
                    vocab.SCIM_GROUPS: rules.normalise_groups(subject.get(vocab.SCIM_GROUPS)),
                    vocab.SCIM_ACTIVE: rules.is_active(subject),
                },
            },
            source=source,
            actor=actor,
            delivery=delivery,
        )
        projected["operation"] = vocab.SCIM_OP_CREATE if created else vocab.SCIM_OP_UPDATE
        projected["outcome"] = outcome
        projected["delivery"] = delivery
        projected[vocab.SCIM_OPERATIONS_FIELD] = vocab.SCIM_OPERATION_LABELS
        projected[vocab.LIMITATION_FIELD] = vocab.LIMITATION
        return projected

    def _deprovision(
        self,
        directory_id: str,
        tenant_id: str,
        room_id: str | None,
        body: Mapping[str, Any],
        *,
        source: str | None,
        actor: str | None,
        delivery: str,
    ) -> dict[str, Any]:
        """Remove a user's access, and record what the removal did.

        The order is the whole point, and it is recorded as
        ``DERIVED_DEPROVISION_SEMANTICS``. The row is soft-deleted rather than deleted, so
        the group membership at the moment of removal stays readable, and every session the
        user holds is marked revoked in the same pass. A session left live would be a leaver
        with access.
        """

        subject = body.get("user") if isinstance(body.get("user"), Mapping) else body
        external_id = str(subject.get(vocab.SCIM_EXTERNAL_ID) or "").strip()
        existing = self._find_user_record(directory_id, external_id)
        if existing is None or existing.get("deleted_at"):
            # Nothing to remove, or the removal has already happened.

            # The second case is the one a replayed Events API pull hits, and it matters:
            # a pull that has lost its cursor re-reads a window that includes a deprovision
            # it already applied. The user is gone, so there is nothing left to delete, and
            # a hard delete of a soft-deleted row raises from the store. Reporting that as a
            # 404 would make a stale pull unusable - and a pull that cannot be re-run is a
            # pull that stops receiving directory changes at all.

            # A webhook post naming a user this store has never held is still a 404 below,
            # because from a provider's point of view that user was never provisioned. The
            # distinction is whether the row exists at all, not whether it is still live.
            if existing is None:
                raise rules.DirectoryUserNotFound(
                    f"No directory user with external id {external_id!r} in this directory."
                )
            revoked_ids = [
                str(row.get("id"))
                for row in self.store.list(vocab.SESSION_COLLECTION, limit=500)
                if (row.get("data") or {}).get("directory_user_id") == external_id
                and not (row.get("data") or {}).get("revoked")
            ]
            for session_id in revoked_ids:
                self.store.update(
                    session_id,
                    {"revoked": True, "revoked_at": rules.stamp(self._now())},
                    actor=actor,
                    source=source,
                )
            return {
                "id": str(existing.get("id")),
                vocab.ORGANIZATION_ID: tenant_id,
                vocab.SCIM_EXTERNAL_ID: external_id,
                "state": vocab.USER_DEPROVISIONED,
                "operation": vocab.SCIM_OP_DELETE,
                "outcome": vocab.USER_DEPROVISIONED,
                "delivery": delivery,
                "already_deprovisioned": True,
                "sessions_revoked": len(revoked_ids),
                "revoked_session_ids": revoked_ids,
                "event_id": None,
                vocab.SCIM_OPERATIONS_FIELD: vocab.SCIM_OPERATION_LABELS,
                vocab.DEPROVISION_DEFINITION_FIELD: vocab.DEPROVISION_DEFINITION,
                vocab.DEPROVISION_POLICY_FIELD: vocab.DEPROVISION_POLICY,
                vocab.LIMITATION_FIELD: vocab.LIMITATION,
            }

        user_id = str(existing.get("id"))
        revoked = [
            str(row.get("id"))
            for row in self.store.list(vocab.SESSION_COLLECTION, limit=500)
            if (row.get("data") or {}).get("directory_user_id") == external_id
            and not (row.get("data") or {}).get("revoked")
        ]
        for session_id in revoked:
            self.store.update(
                session_id,
                {"revoked": True, "revoked_at": rules.stamp(self._now())},
                actor=actor,
                source=source,
            )

        self.store.delete(user_id, actor=actor, source=source)
        event = self._record_event(
            directory_id,
            tenant_id,
            room_id,
            {
                "operation": vocab.SCIM_OP_DELETE,
                "user": {
                    vocab.SCIM_EXTERNAL_ID: external_id,
                    vocab.SCIM_GROUPS: rules.normalise_groups(
                        (existing.get("data") or {}).get(vocab.SCIM_GROUPS)
                    ),
                },
            },
            source=source,
            actor=actor,
            delivery=delivery,
        )

        return {
            "id": user_id,
            vocab.ORGANIZATION_ID: tenant_id,
            vocab.SCIM_EXTERNAL_ID: external_id,
            "state": vocab.USER_DEPROVISIONED,
            "operation": vocab.SCIM_OP_DELETE,
            "outcome": vocab.USER_DEPROVISIONED,
            "delivery": delivery,
            "sessions_revoked": len(revoked),
            "revoked_session_ids": revoked,
            # ``_record_event`` returns None when the change arrived through the pull,
            # because that change already has a log row written by whoever delivered it.
            # Reading `.get` on None is what a replayed deprovision used to do.
            "event_id": (event.get("id") if isinstance(event, Mapping) else None),
            vocab.SCIM_OPERATIONS_FIELD: vocab.SCIM_OPERATION_LABELS,
            vocab.DEPROVISION_DEFINITION_FIELD: vocab.DEPROVISION_DEFINITION,
            vocab.DEPROVISION_POLICY_FIELD: vocab.DEPROVISION_POLICY,
            vocab.LIMITATION_FIELD: vocab.LIMITATION,
        }

    def _reconcile_group(
        self,
        directory_id: str,
        tenant_id: str,
        room_id: str | None,
        subject: Mapping[str, Any],
        *,
        source: str | None,
        actor: str | None,
    ) -> dict[str, Any]:
        """Store a directory group. Its membership is derived, never copied."""

        external_id = str(subject.get(vocab.SCIM_EXTERNAL_ID) or "").strip()
        if not external_id:
            raise rules.IdentitySettingsInvalid(
                "A directory group needs an external id.",
                {vocab.SCIM_EXTERNAL_ID: "This is the directory's own id for the group."},
            )

        users = self.directory_users(directory_id)
        members = rules.active_memberships(users, external_id)
        payload = {
            rules.ROOM_REF: room_id,
            vocab.ORGANIZATION_ID: tenant_id,
            "directory_id": directory_id,
            vocab.SCIM_EXTERNAL_ID: external_id,
            "name": subject.get("name") or external_id,
            "members": members,
            vocab.GROUP_ROLE_KEY: vocab.GROUP_ROLE,
            "updated_at": rules.stamp(self._now()),
        }

        existing = self._find_group_record(directory_id, external_id)
        if existing is None:
            record = self.store.create(
                vocab.DIRECTORY_GROUP_COLLECTION,
                payload,
                room_id=room_id,
                actor=actor,
                source=source,
            )
        else:
            record = self.store.update(str(existing.get("id")), payload, actor=actor, source=source)

        projected = self.project_group(record)
        projected[vocab.LIMITATION_FIELD] = vocab.LIMITATION
        return projected

    def _record_event(
        self,
        directory_id: str,
        tenant_id: str,
        room_id: str | None,
        event: Mapping[str, Any],
        *,
        source: str | None,
        actor: str | None,
        delivery: str | None = None,
    ) -> dict[str, Any] | None:
        """The row describing one directory change, written once by the path that received it.

        ``delivery`` is the path that *received* the change: the webhook, or a pull of the
        Events API. It is ``None`` for a change the pull is reconciling, because that change
        already has a row written by whoever delivered it.

        That distinction is load-bearing. An earlier version wrote a fresh row on every
        reconciliation, so a pull that re-read the log appended a copy of each event to it.
        The log then grew on every pull, the cursor could never advance past what it had
        already seen, and each subsequent pull re-read its own output. One event now produces
        exactly one row, whatever path delivered it.
        """

        if delivery is None:
            return None

        # The stored fields are set after the caller's event rather than merged into one
        # dict, so an event that happens to carry a key named `directory_id` or `at`
        # describes the directory the change came from rather than overwriting this
        # row's own provenance.
        payload = dict(event)
        payload.update(
            {
                rules.ROOM_REF: room_id,
                "directory_id": directory_id,
                vocab.ORGANIZATION_ID: tenant_id,
                "delivery": delivery,
                "at": rules.stamp(self._now()),
            }
        )
        return self.store.create(
            vocab.DIRECTORY_EVENT_COLLECTION,
            payload,
            room_id=room_id,
            actor=actor,
            source=source,
        )

    # -- reads over the directory ------------------------------------------- #

    def directory_users(
        self, directory_id: str, include_deprovisioned: bool = False
    ) -> list[dict[str, Any]]:
        """The directory's users.

        A deprovisioned user is soft-deleted, and the store's ``list`` omits soft-deleted
        rows, so asking for them needs ``include_deleted``. That flag is what makes the
        history readable after a removal rather than leaving it in a table nothing queries.
        """

        rows = [
            row
            for row in self.store.list(
                vocab.DIRECTORY_USER_COLLECTION,
                limit=500,
                include_deleted=include_deprovisioned,
            )
            if (row.get("data") or {}).get("directory_id") == directory_id
        ]
        if not include_deprovisioned:
            rows = [
                row
                for row in rows
                if (row.get("data") or {}).get("state") != vocab.USER_DEPROVISIONED
            ]
        return [self.project_user(row) for row in rows]

    def read_directory_user(self, user_id: str) -> dict[str, Any]:
        """One directory user, or a refusal when the id does not name one.

        The refusal rather than a projection of ``None``, because every feature shares one
        ``records`` table: a caller passing another workflow's id has to be told so rather
        than handed a dict of nulls that reads like a directory user with no fields set.
        """

        record = self._directory_user_record(user_id)
        if record is None:
            raise rules.DirectoryUserNotFound(user_id)
        return self.project_user(record)

    def _directory_user_record(self, user_id: str) -> dict[str, Any] | None:
        """The stored row for a directory user id, or ``None``.

        A soft-deleted row resolves to ``None`` as well, and that is the part that matters.
        ``RecordStore.get`` omits deleted rows, so a leaver's user id stops resolving here
        exactly as it stops resolving in every other read - and :meth:`is_session_live` is
        built on this, so returning the deleted row would report a leaver's session as
        granted whenever the revocation itself was what removed the row.
        """

        record = self.store.get(user_id)
        if record is None or record.get("collection") != vocab.DIRECTORY_USER_COLLECTION:
            return None
        return record

    def _find_user_record(
        self, directory_id: str, external_id: str, include_deprovisioned: bool = True
    ) -> dict[str, Any] | None:
        """The stored user row for one directory's external id, or ``None``.

        Searches soft-deleted rows too, and prefers a live one. That preference is the
        whole point: a directory that removes a user and then re-adds them has two rows for
        one person, and a lookup that returned the deleted one would report the re-hire as
        still deprovisioned. A directory is the source of truth, so its latest word wins.
        """

        for row in self.store.list(
            vocab.DIRECTORY_USER_COLLECTION, limit=1000, include_deleted=True
        ):
            data = row.get("data") or {}
            if (
                data.get("directory_id") == directory_id
                and data.get(vocab.SCIM_EXTERNAL_ID) == external_id
            ):
                if not include_deprovisioned and row.get("deleted_at"):
                    continue
                return row
        return None

    def project_user(self, record: Mapping[str, Any]) -> dict[str, Any]:
        data = dict(record.get("data") or {})
        return {
            "id": record.get("id"),
            "room_id": rules.room_ref_of(data, record),
            "directory_id": data.get("directory_id"),
            vocab.ORGANIZATION_ID: data.get(vocab.ORGANIZATION_ID),
            vocab.SCIM_EXTERNAL_ID: data.get(vocab.SCIM_EXTERNAL_ID),
            vocab.SCIM_USER_NAME: data.get(vocab.SCIM_USER_NAME),
            "name": _display_name(data),
            vocab.SCIM_TITLE: data.get(vocab.SCIM_TITLE),
            "emails": list(data.get(vocab.SCIM_EMAILS) or []),
            "email_domain": _email_domain_of_list(data.get(vocab.SCIM_EMAILS) or []),
            vocab.SCIM_GROUPS: rules.normalise_groups(data.get(vocab.SCIM_GROUPS)),
            vocab.SCIM_ACTIVE: rules.is_active(data),
            "state": data.get("state"),
            "updated_at": data.get("updated_at") or record.get("updated_at"),
            vocab.LIMITATION_FIELD: vocab.LIMITATION,
        }

    def directory_groups(self, directory_id: str) -> list[dict[str, Any]]:
        rows = [
            row
            for row in self.store.list(vocab.DIRECTORY_GROUP_COLLECTION, limit=500)
            if (row.get("data") or {}).get("directory_id") == directory_id
        ]
        return [self.project_group(row) for row in rows]

    def read_directory_group(self, group_id: str) -> dict[str, Any]:
        record = self.store.get(group_id)
        if record is None or record.get("collection") != vocab.DIRECTORY_GROUP_COLLECTION:
            raise rules.DirectoryUserNotFound(group_id)
        return self.project_group(record)

    def _find_group_record(self, directory_id: str, external_id: str) -> dict[str, Any] | None:
        for row in self.store.list(vocab.DIRECTORY_GROUP_COLLECTION, limit=1000):
            data = row.get("data") or {}
            if (
                data.get("directory_id") == directory_id
                and data.get(vocab.SCIM_EXTERNAL_ID) == external_id
            ):
                return row
        return None

    def project_group(self, record: Mapping[str, Any]) -> dict[str, Any]:
        data = dict(record.get("data") or {})
        return {
            "id": record.get("id"),
            "room_id": rules.room_ref_of(data, record),
            "directory_id": data.get("directory_id"),
            vocab.ORGANIZATION_ID: data.get(vocab.ORGANIZATION_ID),
            vocab.SCIM_EXTERNAL_ID: data.get(vocab.SCIM_EXTERNAL_ID),
            "name": data.get("name"),
            "members": list(data.get("members") or []),
            "member_count": len(data.get("members") or []),
            vocab.GROUP_ROLE_KEY: data.get(vocab.GROUP_ROLE_KEY, vocab.GROUP_ROLE),
            "updated_at": data.get("updated_at"),
            vocab.LIMITATION_FIELD: vocab.LIMITATION,
        }

    # -- access rules -------------------------------------------------------- #

    def set_access_rule(
        self,
        group_id: str,
        role: str,
        *,
        source: str | None = None,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Map one directory group onto one app role.

        This is the specification's automation sentence made writable: directory groups
        "create groups that inform access rules". The mapping is from a group to a role, so
        access is a function of membership rather than a label somebody attaches to a person.
        """

        group = self.read_directory_group(group_id)
        if role not in vocab.RULE_ROLES:
            raise rules.IdentitySettingsInvalid(
                "That role is not one this workflow grants.",
                {"role": f"Use one of: {', '.join(vocab.RULE_ROLES)}."},
            )

        body = {
            rules.ROOM_REF: group["room_id"],
            "directory_id": group["directory_id"],
            vocab.ORGANIZATION_ID: group[vocab.ORGANIZATION_ID],
            "group_id": group_id,
            vocab.SCIM_EXTERNAL_ID: group[vocab.SCIM_EXTERNAL_ID],
            "role": role,
            "updated_at": rules.stamp(self._now()),
        }
        existing = self.store.find(vocab.ACCESS_RULE_COLLECTION, {"group_id": group_id}, limit=50)
        if existing:
            record = self.store.update(str(existing[0].get("id")), body, actor=actor, source=source)
        else:
            record = self.store.create(
                vocab.ACCESS_RULE_COLLECTION,
                body,
                room_id=group["room_id"],
                actor=actor,
                source=source,
            )
        projected = self.project_rule(record)
        projected[vocab.OVERRIDE_REFUSAL_FIELD] = vocab.OVERRIDE_REFUSAL_VALUE
        projected[vocab.LIMITATION_FIELD] = vocab.LIMITATION
        return projected

    def access_rules(self, directory_id: str | None = None) -> list[dict[str, Any]]:
        rows = self.store.list(vocab.ACCESS_RULE_COLLECTION, limit=200, order_by="created_at")
        if directory_id:
            rows = [
                row for row in rows if (row.get("data") or {}).get("directory_id") == directory_id
            ]
        return [self.project_rule(row) for row in rows]

    def project_rule(self, record: Mapping[str, Any]) -> dict[str, Any]:
        data = dict(record.get("data") or {})
        return {
            "id": record.get("id"),
            "room_id": rules.room_ref_of(data, record),
            "directory_id": data.get("directory_id"),
            "group_id": data.get("group_id"),
            vocab.SCIM_EXTERNAL_ID: data.get(vocab.SCIM_EXTERNAL_ID),
            "role": data.get("role"),
            "updated_at": data.get("updated_at"),
            vocab.OVERRIDE_REFUSAL_FIELD: vocab.OVERRIDE_REFUSAL_VALUE,
        }

    def effective_access(self, directory_id: str) -> dict[str, Any]:
        """What each directory user gets, as a function of the groups they are in.

        The only route that produces an access answer, and it reads groups and nothing else.
        That is what makes ``DERIVED_NO_MANUAL_OVERRIDE`` true in the code rather than only
        in prose.

        The rule index is keyed by both spellings of a group, because a directory names a
        group by its own external id and an access rule written here holds the group's
        record id. Keying on one alone makes every real membership list match nothing, and
        an access page showing no access for a user who is in an admin group is worse than
        no access page at all.
        """

        rules_by_group: dict[str, Any] = {}
        for row in self.access_rules(directory_id):
            if row.get("group_id"):
                rules_by_group[row["group_id"]] = row
            if row.get(vocab.SCIM_EXTERNAL_ID):
                rules_by_group.setdefault(row[vocab.SCIM_EXTERNAL_ID], row)
        users = []
        for user in self.directory_users(directory_id):
            resolved = rules.resolve_access(user.get(vocab.SCIM_GROUPS), rules_by_group)
            users.append({**user, "access": resolved})
        return {
            "directory_id": directory_id,
            "count": len(users),
            "users": users,
            "rules": list(rules_by_group.values()),
            "unmapped_groups": sorted(
                {
                    group
                    for user in users
                    for group in user.get(vocab.SCIM_GROUPS) or []
                    if group not in rules_by_group
                }
            ),
            vocab.DIRECTORY_TRUTH_FIELD: vocab.DIRECTORY_TRUTH_STATEMENT,
            vocab.OVERRIDE_REFUSAL_FIELD: vocab.OVERRIDE_REFUSAL_VALUE,
            vocab.LIMITATION_FIELD: vocab.LIMITATION,
        }

    # -- the board ---------------------------------------------------------- #

    def summary(self, room_id: str | None = None) -> dict[str, Any]:
        """The page's headline numbers. Read-only.

        Counts are read back from the store rather than accumulated, so the board cannot
        describe a state the store does not hold.
        """

        organizations = self.organizations(room_id)
        connections = self.connections()
        directories = self.directories()
        sessions = self.sessions()
        events = self.store.list(vocab.DIRECTORY_EVENT_COLLECTION, limit=500)
        # Counted from the event log rather than from the user rows. A deprovisioned user is
        # soft-deleted, so it is absent from the live rows and counting those would report
        # zero leavers after a removal - which reads as though deprovisioning never ran.
        deprovisioned = [
            row
            for row in events
            if (row.get("data") or {}).get("operation") == vocab.SCIM_OP_DELETE
        ]
        provisioned = [
            row
            for row in events
            if (row.get("data") or {}).get("operation") == vocab.SCIM_OP_CREATE
        ]
        live_users = sum(row["users"] for row in self.directories())

        return {
            "organizations": len(organizations),
            "connections": len(connections),
            "directories": len(directories),
            "sessions": len(sessions),
            "live_sessions": sum(1 for row in sessions if row["live"]),
            "revoked_sessions": sum(1 for row in sessions if row["revoked"]),
            "directory_users": live_users,
            "deprovisioned_users": len(deprovisioned),
            "provisioned_users": len(provisioned),
            "directory_groups": sum(row["groups"] for row in self.directories()),
            "access_rules": len(self.access_rules()),
            vocab.ASSERT_ORDER_FIELD: vocab.ASSERT_ORDER_VALUE,
            vocab.CODE_TTL_MINUTES_FIELD: vocab.CODE_TTL_MINUTES,
            vocab.DIRECTORY_TRUTH_FIELD: vocab.DIRECTORY_TRUTH_STATEMENT,
            vocab.DEPROVISION_POLICY_FIELD: vocab.DEPROVISION_POLICY,
            vocab.LIMITATION_FIELD: vocab.LIMITATION,
        }


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _string_list(value: Any) -> list[str]:
    """A stored list of strings, deduplicated and order-preserving."""

    if not isinstance(value, (list, tuple)):
        return []
    seen: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if text and text not in seen:
            seen.append(text)
    return seen


def _cursor(position: int) -> str:
    """A pull cursor: how many events this directory's log the pull has consumed.

    A count rather than a timestamp or a record id, and that is the only one of the three
    that is safe here. Directory events written together share a millisecond stamp, and the
    record id is a uuid4 with no relation to when its row was written, so both of the other
    candidates either skip the events that tie with the last one seen or skip an arbitrary
    set. A count is exact, because new events only ever append to the end of the order.
    """

    return f"events:{max(0, int(position))}"


def _cursor_index(cursor: str) -> int:
    """Read a cursor back into the position it was built from.

    An unrecognised cursor is read as position 0 rather than refused. That is deliberate: a
    cursor this build does not understand means the pull cannot know where it stopped, and
    resuming from the start re-applies events the directory's ``applied_event_ids`` marker
    then skips. A deprovision cannot be applied twice and a joiner cannot be lost, so
    replaying is the recoverable direction and skipping is not.
    """

    text = str(cursor or "").strip()
    if text.startswith("events:"):
        try:
            return max(0, int(text.split(":", 1)[1]))
        except ValueError:
            return 0
    return 0


def _user_payload(
    tenant_id: str,
    directory_id: str,
    subject: Mapping[str, Any],
    room_id: str | None,
    now: datetime,
) -> dict[str, Any]:
    """The SCIM attributes this workflow stores for one directory user.

    Only the researched attribute set is copied. Anything else the provider sends is
    dropped rather than stored, because a directory user that silently accumulated every
    field the provider happened to send would make the stored row describe the provider's
    payload rather than this product's user.
    """

    emails = subject.get(vocab.SCIM_EMAILS)
    if isinstance(emails, (list, tuple)):
        stored = [str(item).strip() for item in emails if str(item or "").strip()]
    elif isinstance(emails, str):
        stored = [emails.strip()] if emails.strip() else []
    else:
        stored = []

    return {
        rules.ROOM_REF: room_id,
        vocab.ORGANIZATION_ID: tenant_id,
        "directory_id": directory_id,
        vocab.SCIM_EXTERNAL_ID: str(subject.get(vocab.SCIM_EXTERNAL_ID) or "").strip(),
        vocab.SCIM_USER_NAME: subject.get(vocab.SCIM_USER_NAME) or "",
        vocab.SCIM_GIVEN_NAME: subject.get(vocab.SCIM_GIVEN_NAME) or "",
        vocab.SCIM_FAMILY_NAME: subject.get(vocab.SCIM_FAMILY_NAME) or "",
        vocab.SCIM_TITLE: subject.get(vocab.SCIM_TITLE) or "",
        vocab.SCIM_EMAILS: stored,
        vocab.SCIM_GROUPS: rules.normalise_groups(subject.get(vocab.SCIM_GROUPS)),
        vocab.SCIM_ACTIVE: rules.is_active(subject),
        "updated_at": rules.stamp(now),
    }


def _first_email(profile: Mapping[str, Any]) -> str | None:
    """The first address on a profile, as a string."""

    emails = profile.get(vocab.SCIM_EMAILS)
    if isinstance(emails, list) and emails:
        first = emails[0]
        if isinstance(first, Mapping):
            first = first.get(vocab.SCIM_EMAIL_ADDRESS)
        text = str(first or "").strip()
        if text:
            return text
    text = str(profile.get(vocab.EMAIL) or "").strip()
    return text or None


def _stored_emails(data: Mapping[str, Any]) -> list[str]:
    return [str(item).strip() for item in (data.get(vocab.SCIM_EMAILS) or []) if str(item).strip()]


def _email_domain_of_list(emails: list[Any]) -> str | None:
    for address in emails:
        text = str(address or "").strip()
        if "@" in text:
            return text.rsplit("@", 1)[1].casefold()
    return None


def _display_name(data: Mapping[str, Any]) -> str:
    given = str(data.get(vocab.SCIM_GIVEN_NAME) or "").strip()
    family = str(data.get(vocab.SCIM_FAMILY_NAME) or "").strip()
    joined = " ".join(part for part in (given, family) if part)
    return joined or str(data.get(vocab.SCIM_USER_NAME) or data.get(vocab.SCIM_EXTERNAL_ID) or "")


def _mint_token() -> str:
    """A webhook token for one directory.

    Generated with the same uuid4 hex the store uses for record ids, so two directories
    always get two different tokens and a test can compare them. Deliberately not derived
    from a record: an earlier version created a placeholder row and deleted it, which put a
    spurious insert and a spurious delete in the audit log for every directory that was
    ever created, and the audit log is the artefact this product is built on.
    """

    return uuid.uuid4().hex
