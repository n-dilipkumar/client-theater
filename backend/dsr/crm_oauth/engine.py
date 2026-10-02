"""The workflow: authorize, exchange, seal, refresh on the TTL, probe, report.

Six researched steps, and this module is all of them:

1. an admin picks a vendor and registers the app credentials the vendor's own
   screen gave them;
2. the room builds the vendor's authorization URL with ``client_id``, ``scope``
   and ``redirect_uri`` and hands it to the browser;
3. the vendor's consent screen asks the admin to choose the org/account and
   grant scopes - which happens on the vendor's side, so this module's job for
   step 3 is to *record* the answer and be able to say what it was;
4. the vendor redirects back with a ``code``;
5. the room exchanges the code server-side and seals the refresh token in the
   credential vault keyed by the org/account id;
6. "Test connection" calls a low-cost authenticated endpoint with the bearer.

The automations the research names are here too: refresh *before* expiry, driven
by the ``expires_in`` the token came with, and connection-health polling that the
room drives itself.

The sentence this module is built around
----------------------------------------

    "`Unauthorized (401)` requests are not a valid indicator that a new access
    token must be retrieved."

So a 401 changes :func:`test_connection`'s *health* and writes a token event, and
touches nothing else. It does not clear the stored token, does not move
``expires_at``, does not set any flag the refresh path reads, and does not bring
the next health check forward. There is exactly one writer of a refresh trigger
in this file - :meth:`CrmOAuthConnections._usable_token`, and it reads the stored
TTL and nothing else.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Mapping

from dsr.crm_oauth.connectors import (
    AuthorizeRequest,
    CrmConnector,
    ExecuteRequest,
    TokenRequest,
    TokenResponse,
    VendorInfo,
    connector as connector_for,
    registered_vendors,
    vendor_info,
)
from dsr.crm_oauth.errors import (
    AuthorizationError,
    ConnectionDisabledError,
    ConnectionNotFoundError,
    ConnectorConfigError,
    CrmOAuthError,
    NotConnectedError,
    TokenExchangeError,
    UnknownRoomError,
)
from dsr.crm_oauth.transport import DEFAULT_TIMEOUT_SECONDS, HttpResult, Transport, UrllibTransport
from dsr.crm_oauth.vault import APP_ORG_KEY, CredentialVault
from dsr.store import RecordStore

# --------------------------------------------------------------------------- #
# Collections
# --------------------------------------------------------------------------- #

CONNECTION_COLLECTION = "crm_connection"
GRANT_COLLECTION = "crm_grant"
EVENT_COLLECTION = "crm_token_event"

#: The credential vault's own collection. Listed here so the seeder and the
#: vocabulary can account for every row this feature writes - and deliberately
#: *not* served by any route.
CREDENTIAL_COLLECTION = "crm_credential"

COLLECTIONS: tuple[str, ...] = (
    CONNECTION_COLLECTION,
    GRANT_COLLECTION,
    CREDENTIAL_COLLECTION,
    EVENT_COLLECTION,
)

# --------------------------------------------------------------------------- #
# Vocabulary of states
# --------------------------------------------------------------------------- #

#: The credential lifecycle. Computed from the sealed credential at read time
#: rather than stored, so a stored status cannot go stale and claim a connection
#: is authorized when its token expired an hour ago.
STATUS_PENDING = "pending_authorization"
STATUS_AUTHORIZED = "authorized"
STATUS_EXPIRED = "expired"
STATUS_DISCONNECTED = "disconnected"
CONNECTION_STATUSES: tuple[str, ...] = (
    STATUS_PENDING,
    STATUS_AUTHORIZED,
    STATUS_EXPIRED,
    STATUS_DISCONNECTED,
)

#: The last probe's outcome. Separate from ``status`` on purpose - see the module
#: docstring and ``status_and_health_are_separate`` in :mod:`dsr.crm_oauth.inferences`.
HEALTH_UNKNOWN = "unknown"
HEALTH_OK = "ok"
HEALTH_UNAUTHORIZED = "unauthorized"
HEALTH_ERROR = "error"
HEALTH_VALUES: tuple[str, ...] = (HEALTH_UNKNOWN, HEALTH_OK, HEALTH_UNAUTHORIZED, HEALTH_ERROR)

GRANT_PENDING = "pending"
GRANT_EXCHANGED = "exchanged"
GRANT_FAILED = "failed"
GRANT_CANCELLED = "cancelled"
GRANT_STATES: tuple[str, ...] = (GRANT_PENDING, GRANT_EXCHANGED, GRANT_FAILED, GRANT_CANCELLED)

EVENT_AUTHORIZE = "authorize"
EVENT_ISSUED = "issued"
EVENT_REFRESHED = "refreshed"
EVENT_TESTED = "tested"
EVENT_REFRESH_REFUSED = "refresh_refused"
EVENT_CANCELLED = "cancelled"
EVENT_DISCONNECTED = "disconnected"
EVENT_TYPES: tuple[str, ...] = (
    EVENT_AUTHORIZE,
    EVENT_ISSUED,
    EVENT_REFRESHED,
    EVENT_TESTED,
    EVENT_REFRESH_REFUSED,
    EVENT_CANCELLED,
    EVENT_DISCONNECTED,
)

#: What a 401 from the probe means, and what it does not.
#: [sourced] "`Unauthorized (401)` requests are not a valid indicator that a new
#: access token must be retrieved."
UNAUTHORIZED_IS_NOT_A_REFRESH_TRIGGER = True

#: Seconds before expiry at which a token is refreshed. See
#: ``refresh_skew_seconds`` in :mod:`dsr.crm_oauth.inferences`.
SKEW_SECONDS = 60

#: How long a pending authorization stays usable. See ``grant_expiry_and_single_use``.
GRANT_TTL_SECONDS = 600

#: The health sweep's cadence. See ``default_health_interval``.
DEFAULT_HEALTH_INTERVAL_SECONDS = 3600
ERROR_RETRY_SECONDS = 300

#: The status codes that count as "the vendor rejected this credential".
#: 401 is the researched one; 403 is the same answer from some vendors and
#: treating it as an unrelated error would be the kind of thing a reviewer finds
#: in production.
UNAUTHORIZED_STATUSES: frozenset[int] = frozenset({401, 403})


# --------------------------------------------------------------------------- #
# Small time helpers
# --------------------------------------------------------------------------- #


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="milliseconds")


def parse(value: Any) -> datetime | None:
    """Parse a stored ISO timestamp, or ``None``.

    A stored timestamp that does not parse is treated as absent rather than
    raising: a malformed ``expires_at`` must not take a sweep down, and absent
    is the safe reading (it means "unknown", which is the loud one).
    """
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def seconds_until(value: Any, now: datetime) -> int | None:
    moment = parse(value)
    if moment is None:
        return None
    return int((moment - now).total_seconds())


# --------------------------------------------------------------------------- #
# The token in hand
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class TokenUse:
    """An access token plus why this call has it."""

    access_token: str
    credential: Mapping[str, Any]
    refreshed: bool
    trigger: str
    expires_at: str | None
    ttl_known: bool

    @property
    def scope(self) -> str:
        return str(self.credential.get("scope") or "")

    def describe(self) -> dict[str, Any]:
        return {
            "refreshed": self.refreshed,
            "trigger": self.trigger,
            "expires_at": self.expires_at,
            "ttl_known": self.ttl_known,
            "has_refresh_token": bool(self.credential.get("refresh_token")),
            "scope": self.scope,
        }


# --------------------------------------------------------------------------- #
# The engine
# --------------------------------------------------------------------------- #


class CrmOAuthConnections:
    """Every read and write this workflow makes, on top of :class:`RecordStore`.

    Built per request from :data:`dsr.deps.StoreDep` rather than stored on
    ``app.state``, which is also what leaves the transport as an overridable
    dependency: the suite and the seeder both replace it and never open a socket.
    """

    def __init__(
        self,
        store: RecordStore,
        *,
        transport: Transport | None = None,
        vault: CredentialVault | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.store = store
        self.transport: Transport = transport or UrllibTransport()
        self.vault = vault or CredentialVault(store)
        self._now = now or utcnow

    # -- clock ------------------------------------------------------------ #

    def now(self) -> datetime:
        return self._now()

    # -- lookups ---------------------------------------------------------- #

    def require_connection(self, connection_id: str) -> dict[str, Any]:
        record = self.store.get(str(connection_id or ""))
        if record is None or record.get("collection") != CONNECTION_COLLECTION:
            raise ConnectionNotFoundError(f"CRM connection {connection_id} not found")
        return record

    def require_room(self, room_id: str) -> dict[str, Any]:
        record = self.store.get(str(room_id or ""))
        if record is None or record.get("collection") != "room":
            raise UnknownRoomError(str(room_id or ""))
        return record

    def require_enabled(self, record: Mapping[str, Any]) -> dict[str, Any]:
        if not record["data"].get("enabled", True):
            raise ConnectionDisabledError(
                f"CRM connection {record['id']} is switched off; turn it on before "
                "authorizing, testing or polling it"
            )
        return dict(record["data"])

    def connector(self, vendor: str) -> CrmConnector:
        return connector_for(vendor)

    def tenants(self) -> list[str]:
        """Every tenant that has at least one live connection."""
        seen: set[str] = set()
        for record in self.store.list(CONNECTION_COLLECTION, limit=1000):
            seen.add(str(record["data"].get("tenant") or ""))
        return sorted(seen - {""})

    def list_connections(
        self,
        *,
        room_id: str | None = None,
        vendor: str | None = None,
        status: str | None = None,
        tenant: str | None = None,
        scope: str = "all",
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """Connections, as summaries.

        ``room_id`` means "the connections that serve this room", which is the
        connections attached to it *plus* the tenant-wide ones - a tenant-scoped
        credential serves every room in the tenant, and hiding it from a room
        that uses it would be the kind of quiet wrongness this feature exists to
        avoid.

        The tenant-wide half is filtered in Python rather than through ``find``:
        a ``None`` in ``data`` is indexed as an empty string, and ``find`` has no
        way to say "this path is absent". The envelope's own ``room_id`` column
        does the room-attached half, which is what it is for.
        """
        where: dict[str, Any] = {}
        if vendor:
            where["vendor"] = vendor
        if tenant:
            where["tenant"] = tenant
        records = (
            self.store.find(CONNECTION_COLLECTION, where, limit=1000)
            if where
            else self.store.list(CONNECTION_COLLECTION, limit=1000)
        )
        summaries = [self.summarise(record) for record in records]
        if scope in ("room", "tenant"):
            wanted = "room" if scope == "room" else "tenant"
            summaries = [entry for entry in summaries if entry["scope"] == wanted]
        if room_id:
            summaries = [entry for entry in summaries if entry["room_id"] in (room_id, None)]
        if status:
            summaries = [entry for entry in summaries if entry["status"] == status]
        summaries.sort(key=lambda entry: (entry["vendor"], entry["label"] or entry["id"]))
        return summaries[: max(1, min(int(limit), 1000))]

    def connection(self, connection_id: str) -> dict[str, Any]:
        return self.summarise(self.require_connection(connection_id))

    def tenant_wide(self) -> list[dict[str, Any]]:
        return [entry for entry in self.list_connections() if entry["scope"] == "tenant"]

    # -- step 1: register the app ------------------------------------------ #

    def create_connection(
        self, payload: Mapping[str, Any], *, actor: str | None = None, source: str
    ) -> dict[str, Any]:
        """Register a connection. The researched step 1, recorded.

        The ``client_id`` is public by design and stays in the settings row; the
        ``client_secret`` is a credential of the same kind as the token, so it is
        sealed in the vault rather than kept here. Nothing else is enforced yet -
        a half-configured connection is a real state, and readiness reports it
        rather than refusing the create.
        """
        vendor = str(payload.get("vendor") or "").strip()
        connector_for(vendor)  # refuses an unknown vendor before anything is written
        info = vendor_info(vendor)

        scopes = _as_scopes(payload.get("scopes"))
        room_id = payload.get("room_id")
        if room_id is not None:
            self.require_room(str(room_id))

        body: dict[str, Any] = {
            "vendor": vendor,
            "label": str(payload.get("label") or info.label),
            "tenant": str(payload.get("tenant") or "default"),
            "room_id": str(room_id) if room_id is not None else None,
            "client_id": str(payload.get("client_id") or ""),
            "redirect_uri": str(payload.get("redirect_uri") or ""),
            "scopes": scopes,
            "org_id": str(payload.get("org_id") or ""),
            "environment": str(payload.get("environment") or "production"),
            "policy": str(payload.get("policy") or ""),
            "api_version": str(payload.get("api_version") or ""),
            "api_base_url": str(payload.get("api_base_url") or ""),
            "environment_url": str(payload.get("environment_url") or ""),
            "tenant_id": str(payload.get("tenant_id") or ""),
            "enabled": bool(payload.get("enabled", True)),
            "health_interval_seconds": _as_interval(payload.get("health_interval_seconds")),
            "status": STATUS_PENDING,
            "health": HEALTH_UNKNOWN,
            "health_detail": "",
            "last_error": "",
            "last_checked_at": None,
            "last_unauthorized_at": None,
            "last_authorized_at": None,
            "next_check_at": None,
            "credential_org_key": None,
            "has_client_secret": bool(payload.get("client_secret")),
            "notes": str(payload.get("notes") or ""),
        }
        record = self.store.create(
            CONNECTION_COLLECTION, body, room_id=body["room_id"], actor=actor, source=source
        )

        if body["has_client_secret"]:
            self.vault.put(
                record["id"],
                APP_ORG_KEY,
                {"client_secret": str(payload["client_secret"])},
                kind="app",
                actor=actor,
                source=source,
            )
        return self.summarise(self.store.get(record["id"]) or record)

    def update_connection(
        self,
        connection_id: str,
        payload: Mapping[str, Any],
        *,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Patch a connection, re-sealing the client secret when one is given."""
        record = self.require_connection(connection_id)
        record["data"]
        patch: dict[str, Any] = {}

        if "vendor" in payload:
            vendor = str(payload["vendor"] or "").strip()
            connector_for(vendor)
            patch["vendor"] = vendor
        if "label" in payload:
            patch["label"] = str(payload["label"] or "")
        if "tenant" in payload:
            patch["tenant"] = str(payload["tenant"] or "")
        if "room_id" in payload:
            room_id = payload["room_id"]
            if room_id not in (None, ""):
                self.require_room(str(room_id))
            patch["room_id"] = str(room_id) if room_id not in (None, "") else None
        for key in (
            "client_id",
            "redirect_uri",
            "org_id",
            "environment",
            "policy",
            "api_version",
            "api_base_url",
            "environment_url",
            "tenant_id",
            "notes",
        ):
            if key in payload:
                patch[key] = str(payload[key] or "")
        if "scopes" in payload:
            patch["scopes"] = _as_scopes(payload["scopes"])
        if "enabled" in payload:
            patch["enabled"] = bool(payload["enabled"])
        if "health_interval_seconds" in payload:
            patch["health_interval_seconds"] = _as_interval(payload["health_interval_seconds"])
        if patch.get("scopes") is not None and not patch["scopes"] and "scopes" in payload:
            raise ConnectorConfigError(
                "scopes cannot be emptied: the consent screen grants scopes and an "
                "empty scope asks the vendor for nothing"
            )

        if "client_secret" in payload:
            secret = str(payload["client_secret"] or "")
            patch["has_client_secret"] = bool(secret)
            if secret:
                self.vault.put(
                    record["id"],
                    APP_ORG_KEY,
                    {"client_secret": secret},
                    kind="app",
                    actor=actor,
                    source=source,
                )

        if patch:
            self.store.update(record["id"], patch, actor=actor, source=source)
        return self.summarise(self.store.get(record["id"]) or record)

    def disconnect(
        self, connection_id: str, *, actor: str | None = None, source: str
    ) -> dict[str, Any]:
        """Soft-delete a connection and cancel its pending authorizations.

        The token events and the audit trail outlive it: a connection that was
        authorized and then removed is the row an operator needs when a room
        stops syncing, and the audit log is the guarantee the product is built on.
        The sealed row goes with it, so a disconnected connection leaves no
        credential behind.
        """
        record = self.require_connection(connection_id)
        data = record["data"]
        cancelled = 0
        for grant in self._grants_for(record["id"]):
            if grant["data"].get("state_name") == GRANT_PENDING:
                cancelled += 1
                self.store.update(
                    grant["id"],
                    {"state_name": GRANT_CANCELLED, "closed_at": iso(self.now())},
                    actor=actor,
                    source=source,
                )
        org_key = data.get("credential_org_key")
        if org_key:
            self.vault.delete(record["id"], str(org_key), actor=actor, source=source)
        self.vault.delete(record["id"], APP_ORG_KEY, actor=actor, source=source)
        self._event(
            record["id"],
            EVENT_DISCONNECTED,
            {
                "detail": f"connection {data.get('label') or record['id']} disconnected",
                "cancelled_grants": cancelled,
            },
            actor=actor,
            source=source,
            room_id=record.get("room_id"),
        )
        self.store.delete(record["id"], actor=actor, source=source)
        return {
            "id": record["id"],
            "vendor": data.get("vendor"),
            "disconnected": True,
            "credential_removed": bool(org_key),
            "cancelled_grants": cancelled,
        }

    # -- step 2: the authorize URL ----------------------------------------- #

    def begin_authorization(
        self,
        connection_id: str,
        *,
        scopes: Iterable[str] | None = None,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """Build the vendor's authorization URL. The researched step 2.

        Refused for a connection that cannot produce a URL the vendor will
        accept - no client id, no redirect URI, no scopes, no org for a vendor
        whose resource URL is templated on one. The refusal lists every blocker
        rather than the first, so an admin fixing the form sees the whole list.
        """
        record = self.require_connection(connection_id)
        self.require_enabled(record)
        data = record["data"]
        connector = self.connector(str(data.get("vendor")))
        effective = list(scopes) if scopes else list(data.get("scopes") or [])
        blockers = self._authorize_blockers(data)
        if blockers:
            raise ConnectorConfigError(
                f"connection {record['id']} cannot be authorized yet: "
                + "; ".join(f"{item['code']} - {item['why']}" for item in blockers)
            )

        state = secrets.token_urlsafe(24)
        now = self.now()
        url = connector.authorize_url(
            AuthorizeRequest(
                client_id=str(data.get("client_id") or ""),
                redirect_uri=str(data.get("redirect_uri") or ""),
                scopes=tuple(effective),
                state=state,
                org_hint=str(data.get("org_id") or ""),
                extra={
                    "environment": data.get("environment"),
                    "tenant": data.get("tenant_id"),
                },
            )
        )
        grant = self.store.create(
            GRANT_COLLECTION,
            {
                "connection_id": record["id"],
                "room_id": record.get("room_id"),
                "vendor": data.get("vendor"),
                "state_nonce": state,
                "scopes": effective,
                "redirect_uri": data.get("redirect_uri"),
                "org_hint": data.get("org_id"),
                "authorize_url": url,
                "state_name": GRANT_PENDING,
                "created_at": iso(now),
                "expires_at": iso(now + timedelta(seconds=GRANT_TTL_SECONDS)),
                "closed_at": None,
                "error": "",
            },
            room_id=record.get("room_id"),
            actor=actor,
            source=source,
        )
        self._event(
            record["id"],
            EVENT_AUTHORIZE,
            {
                "detail": f"sent the admin to {data.get('vendor')} for authorization",
                "grant_id": grant["id"],
                "scopes": effective,
                "expires_at": iso(now + timedelta(seconds=GRANT_TTL_SECONDS)),
            },
            actor=actor,
            source=source,
            room_id=record.get("room_id"),
        )
        return {
            "connection_id": record["id"],
            "vendor": data.get("vendor"),
            "grant_id": grant["id"],
            "state": state,
            "authorize_url": url,
            "scopes": effective,
            "redirect_uri": data.get("redirect_uri"),
            "expires_at": iso(now + timedelta(seconds=GRANT_TTL_SECONDS)),
            "callback": f"{data.get('redirect_uri')}?code=…&state={state}",
        }

    def cancel_grant(
        self, grant_id: str, *, actor: str | None = None, source: str
    ) -> dict[str, Any]:
        """Abandon a pending authorization.

        The researched flow has no "cancel" step, but a tab the admin closed
        leaves one lying around, and a grant that is still live can be completed
        long after the admin stopped caring. Cancelling is a state change, so it
        is audited like one.
        """
        grant = self.store.get(str(grant_id or ""))
        if grant is None or grant.get("collection") != GRANT_COLLECTION:
            raise AuthorizationError(f"pending authorization {grant_id} not found")
        if grant["data"].get("state_name") != GRANT_PENDING:
            raise AuthorizationError(
                f"authorization {grant_id} is {grant['data'].get('state_name')}, not pending"
            )
        updated = self.store.update(
            grant["id"],
            {"state_name": GRANT_CANCELLED, "closed_at": iso(self.now())},
            actor=actor,
            source=source,
        )
        self._event(
            str(grant["data"].get("connection_id") or ""),
            EVENT_CANCELLED,
            {"detail": "a pending authorization was abandoned", "grant_id": grant["id"]},
            actor=actor,
            source=source,
            room_id=grant.get("room_id"),
        )
        return self.grant_summary(updated)

    def list_grants(
        self, *, connection_id: str | None = None, state_name: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        """Pending and closed authorizations, newest first."""
        where: dict[str, Any] = {}
        if connection_id:
            where["connection_id"] = connection_id
        if state_name:
            where["state_name"] = state_name
        records = (
            self.store.find(GRANT_COLLECTION, where, limit=1000)
            if where
            else self.store.list(GRANT_COLLECTION, limit=1000)
        )
        self.now()
        summaries = [self.grant_summary(record) for record in records]
        for entry in summaries:
            if entry["state"] == GRANT_PENDING and entry["seconds_remaining"] is not None:
                entry["seconds_remaining"] = max(0, entry["seconds_remaining"])
        summaries.sort(key=lambda entry: entry["created_at"] or "", reverse=True)
        return summaries[: max(1, min(int(limit), 1000))]

    def pending_grants(self, connection_id: str) -> list[dict[str, Any]]:
        return [
            entry
            for entry in self.list_grants(connection_id=connection_id)
            if entry["state"] == GRANT_PENDING
        ]

    # -- steps 4 and 5: the callback and the exchange ---------------------- #

    def exchange_callback(
        self,
        connection_id: str,
        *,
        code: str,
        state: str,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """The researched callback: ``?code=…`` in, a sealed credential out.

        [sourced] "Sales room exchanges the code server-side for an access token
        (+ refresh token) and stores the refresh token in the integration's
        credential vault, keyed by the org/account id."

        The ``state`` is what binds this callback to the authorization that
        started it. The research names only the ``code``; see
        ``state_parameter`` in :mod:`dsr.crm_oauth.inferences` for why the binding
        is here and what it costs.
        """
        record = self.require_connection(connection_id)
        self.require_enabled(record)
        data = record["data"]
        if not code:
            raise AuthorizationError(
                "the callback carried no code; the vendor's redirect_uri is wrong"
            )
        grant = self._require_pending_grant(str(state or ""), record["id"])

        connector = self.connector(str(data.get("vendor")))
        secret = self._client_secret(record["id"])
        now = self.now()
        try:
            response = connector.exchange_code(
                TokenRequest(
                    client_id=str(data.get("client_id") or ""),
                    client_secret=secret,
                    redirect_uri=str(
                        grant["data"].get("redirect_uri") or data.get("redirect_uri") or ""
                    ),
                    code=code,
                    extra={
                        "environment": data.get("environment"),
                        "tenant": data.get("tenant_id"),
                    },
                ),
                transport=self.transport,
            )
        except TokenExchangeError as exc:
            self.store.update(
                grant["id"],
                {
                    "state_name": GRANT_FAILED,
                    "closed_at": iso(now),
                    "error": str(exc),
                    "vendor_status": exc.vendor_status,
                },
                actor=actor,
                source=source,
            )
            raise

        org_key = self._resolve_org_key(data, response)
        if not org_key:
            message = (
                "the token response named no org/account and this connection has "
                "none configured: the credential vault is keyed by the org/account "
                "id, so there is nowhere to file this token. Set org_id on the "
                "connection, or re-authorize against an org the vendor names."
            )
            self.store.update(
                grant["id"],
                {"state_name": GRANT_FAILED, "closed_at": iso(now), "error": message},
                actor=actor,
                source=source,
            )
            raise ConnectorConfigError(message)

        expires_at = self._expiry(now, response.expires_in, connector)
        credential = {
            "access_token": response.access_token,
            "refresh_token": response.refresh_token,
            "token_type": response.token_type,
            "scope": response.scope or " ".join(grant["data"].get("scopes") or []),
            "issued_at": iso(now),
            "expires_in": response.expires_in,
            "expires_at": expires_at,
            "org_id": org_key,
            "org_id_source": "vendor" if response.org_id else "configured",
            "raw": dict(response.raw),
        }
        self.vault.put(record["id"], org_key, credential, kind="org", actor=actor, source=source)
        self.store.update(
            grant["id"],
            {
                "state_name": GRANT_EXCHANGED,
                "closed_at": iso(now),
                "exchanged_at": iso(now),
                "org_key": org_key,
                "error": "",
            },
            actor=actor,
            source=source,
        )
        self.store.update(
            record["id"],
            {
                "status": STATUS_AUTHORIZED,
                "credential_org_key": org_key,
                "last_authorized_at": iso(now),
                "last_error": "",
                "health": HEALTH_UNKNOWN,
                "health_detail": "",
            },
            actor=actor,
            source=source,
        )
        self._event(
            record["id"],
            EVENT_ISSUED,
            {
                "detail": f"exchanged the authorization code for a token, sealed under org {org_key}",
                "grant_id": grant["id"],
                "org_key": org_key,
                "org_id_source": credential["org_id_source"],
                "expires_in": response.expires_in,
                "expires_at": expires_at,
                "ttl_known": bool(expires_at),
                "has_refresh_token": bool(response.refresh_token),
                "scope": credential["scope"],
            },
            actor=actor,
            source=source,
            room_id=record.get("room_id"),
        )
        return {
            "connection_id": record["id"],
            "grant_id": grant["id"],
            "vendor": data.get("vendor"),
            "org_key": org_key,
            "org_id_source": credential["org_id_source"],
            "sealed": True,
            "has_refresh_token": bool(response.refresh_token),
            "expires_at": expires_at,
            "ttl_known": bool(expires_at),
            "scope": credential["scope"],
            "connection": self.summarise(self.store.get(record["id"]) or record),
        }

    # -- the automation: refresh on the TTL --------------------------------- #

    def refresh_now(
        self, connection_id: str, *, actor: str | None = None, source: str
    ) -> dict[str, Any]:
        """Refresh on demand, which is the same code path the TTL takes.

        Exposed because "refresh before expiry" is a rule and an operator
        watching a connection about to expire should not have to wait for the
        rule to fire. It is deliberately the *only* way to force one: there is no
        flag a 401 could set.
        """
        record = self.require_connection(connection_id)
        self.require_enabled(record)
        use = self._usable_token(record, force=True, actor=actor, source=source)
        return {
            "connection_id": record["id"],
            "refreshed": use.refreshed,
            "trigger": use.trigger,
            "expires_at": use.expires_at,
            "ttl_known": use.ttl_known,
            "connection": self.summarise(self.store.get(record["id"]) or record),
        }

    def _usable_token(
        self,
        record: Mapping[str, Any],
        *,
        force: bool = False,
        actor: str | None = None,
        source: str,
    ) -> TokenUse:
        """The one place in this package that decides to refresh.

        It reads the stored ``expires_at`` and the clock, and nothing else. In
        particular it does not read any health field, because
        :data:`UNAUTHORIZED_IS_NOT_A_REFRESH_TRIGGER` is not a convention here,
        it is the rule the research states.
        """
        data = record["data"]
        org_key = str(data.get("credential_org_key") or data.get("org_id") or "")
        credential = self.vault.read(record["id"], org_key) if org_key else None
        if credential is None:
            raise NotConnectedError(
                f"connection {record['id']} has no sealed credential for org "
                f"{org_key or '(none)'}; authorize it first"
            )

        now = self.now()
        expires_at = parse(credential.get("expires_at"))
        ttl_known = expires_at is not None
        if force:
            trigger = "forced"
        elif not ttl_known:
            trigger = "ttl_unknown"
        elif now >= expires_at - timedelta(seconds=SKEW_SECONDS):
            trigger = "ttl_due"
        else:
            return TokenUse(
                access_token=str(credential.get("access_token") or ""),
                credential=credential,
                refreshed=False,
                trigger="cached",
                expires_at=iso(expires_at) if expires_at else None,
                ttl_known=True,
            )

        connector = self.connector(str(data.get("vendor")))
        try:
            response = connector.refresh(
                TokenRequest(
                    client_id=str(data.get("client_id") or ""),
                    client_secret=self._client_secret(record["id"]),
                    refresh_token=str(credential.get("refresh_token") or ""),
                    extra={
                        "environment": data.get("environment"),
                        "tenant": data.get("tenant_id"),
                    },
                ),
                transport=self.transport,
            )
        except TokenExchangeError as exc:
            # A refresh the vendor refuses will not start working. The stored TTL
            # still says the token is dead, but the *remedy* is the consent
            # screen rather than another refresh, so the refusal is recorded and
            # ``needs_action`` reads it.
            self._event(
                record["id"],
                EVENT_REFRESH_REFUSED,
                {
                    "detail": f"the vendor refused the refresh: {exc}",
                    "trigger": trigger,
                    "vendor_status": exc.vendor_status,
                    "vendor_body": exc.vendor_body,
                },
                actor=actor,
                source=source,
                room_id=record.get("room_id"),
                expires_at=credential.get("expires_at"),
            )
            self.store.update(
                record["id"],
                {
                    "refresh_refused_at": iso(now),
                    "last_refresh_error": str(exc),
                    "last_error": str(exc),
                },
                actor=actor,
                source=source,
            )
            raise

        refreshed_expires = self._expiry(now, response.expires_in, connector)
        merged = {
            **credential,
            "access_token": response.access_token,
            "refresh_token": response.refresh_token or credential.get("refresh_token", ""),
            "token_type": response.token_type,
            "issued_at": iso(now),
            "expires_in": response.expires_in
            if response.expires_in is not None
            else credential.get("expires_in"),
            "expires_at": refreshed_expires,
            "scope": response.scope or credential.get("scope", ""),
            "raw": dict(response.raw),
        }
        self.vault.put(record["id"], org_key, merged, kind="org", actor=actor, source=source)
        self.store.update(
            record["id"],
            {"refresh_refused_at": None, "last_refresh_error": ""},
            actor=actor,
            source=source,
        )
        self._event(
            record["id"],
            EVENT_REFRESHED,
            {
                "detail": f"refreshed on the stored TTL ({trigger})",
                "trigger": trigger,
                "expires_in": response.expires_in,
                "expires_at": refreshed_expires,
                "ttl_known": bool(refreshed_expires),
                "returned_a_new_refresh_token": bool(response.refresh_token),
            },
            actor=actor,
            source=source,
            room_id=record.get("room_id"),
        )
        return TokenUse(
            access_token=str(merged.get("access_token") or ""),
            credential=merged,
            refreshed=True,
            trigger=trigger,
            expires_at=refreshed_expires,
            ttl_known=bool(refreshed_expires),
        )

    # -- step 6: test connection ------------------------------------------- #

    def test_connection(
        self, connection_id: str, *, actor: str | None = None, source: str
    ) -> dict[str, Any]:
        """The researched step 6: a low-cost authenticated call, and what it said.

        Answers ``200`` even when the vendor rejects the token, with ``ok: false``
        and ``outcome: "unauthorized"``: the call succeeded, and the answer was
        no. See ``test_connection_reports_200`` in
        :mod:`dsr.crm_oauth.inferences`.

        **What a 401 does not do.** It does not clear the sealed token, does not
        move ``expires_at``, does not set anything the refresh path reads, and
        does not bring the next health check forward.
        """
        record = self.require_connection(connection_id)
        self.require_enabled(record)
        data = record["data"]
        now = self.now()
        use = self._usable_token(record, force=False, actor=actor, source=source)
        connector = self.connector(str(data.get("vendor")))
        info = vendor_info(str(data.get("vendor")))

        base_url = self._probe_base(record, use)
        probe_path = self._probe_path(info, data)
        result = connector.execute(
            ExecuteRequest(
                base_url=base_url,
                path=probe_path,
                access_token=use.access_token,
                method="GET",
                query=dict(info.probe_query),
            ),
            transport=self.transport,
        )
        outcome = _classify(result)
        url = f"{base_url.rstrip('/')}/{probe_path.lstrip('/')}"

        detail = _outcome_detail(result, outcome)
        if outcome == HEALTH_UNAUTHORIZED:
            # [sourced] "Unauthorized (401) requests are not a valid indicator
            # that a new access token must be retrieved."  Nothing below writes
            # a refresh trigger; `last_unauthorized_at` is a health fact and the
            # status is left where the TTL put it.
            patch: dict[str, Any] = {
                "health": HEALTH_UNAUTHORIZED,
                "health_detail": detail,
                "last_checked_at": iso(now),
                "last_unauthorized_at": iso(now),
                "last_error": detail,
            }
        elif outcome == HEALTH_OK:
            patch = {
                "health": HEALTH_OK,
                "health_detail": detail,
                "last_checked_at": iso(now),
                "last_error": "",
                "last_unauthorized_at": None,
            }
        else:
            patch = {
                "health": HEALTH_ERROR,
                "health_detail": detail,
                "last_checked_at": iso(now),
                "last_error": detail,
            }
        patch["next_check_at"] = iso(now + timedelta(seconds=_next_interval(data, outcome)))
        self.store.update(record["id"], patch, actor=actor, source=source)

        self._event(
            record["id"],
            EVENT_TESTED,
            {
                "detail": detail,
                "outcome": outcome,
                "vendor_status": result.status,
                "duration_ms": result.duration_ms,
                "request_url": url,
                "method": "GET",
                "authorization_header": "<redacted>",
                "refreshed_before_probe": use.refreshed,
                "refresh_trigger": use.trigger,
                "unauthorized_is_not_a_refresh_trigger": UNAUTHORIZED_IS_NOT_A_REFRESH_TRIGGER,
                "expires_at": use.expires_at,
                "ttl_known": use.ttl_known,
            },
            actor=actor,
            source=source,
            room_id=record.get("room_id"),
        )

        refreshed = self.store.get(record["id"]) or record
        return {
            "connection_id": record["id"],
            "vendor": data.get("vendor"),
            "ok": outcome == HEALTH_OK,
            "outcome": outcome,
            "detail": detail,
            "vendor_status": result.status,
            "duration_ms": result.duration_ms,
            "request_url": url,
            "refreshed_before_probe": use.refreshed,
            "refresh_trigger": use.trigger,
            "unauthorized_is_not_a_refresh_trigger": UNAUTHORIZED_IS_NOT_A_REFRESH_TRIGGER,
            "expires_at": use.expires_at,
            "ttl_known": use.ttl_known,
            "needs_action": self.needs_action(self.summarise(refreshed)),
            "connection": self.summarise(refreshed),
        }

    def health_check(
        self,
        room_id: str,
        *,
        force: bool = False,
        actor: str | None = None,
        source: str,
    ) -> dict[str, Any]:
        """The sweep the room's own scheduler calls. Nothing pushes at us.

        [sourced] "Connection-health polling is driven by the sales room's own
        scheduler (not vendor-side)."

        Covers the connections attached to the room *and* the tenant-wide ones,
        because a tenant-scoped credential serves every room in the tenant. A
        connection that is not due is skipped and says so, rather than being
        polled anyway - that is what makes the interval mean something.
        """
        self.require_room(room_id)
        now = self.now()
        results: list[dict[str, Any]] = []
        for entry in self.list_connections(room_id=room_id, limit=500):
            record = self.store.get(entry["id"]) or {}
            if not record.get("data", {}).get("enabled", True):
                results.append(
                    {"connection_id": entry["id"], "skipped": "disabled", "outcome": "unknown"}
                )
                continue
            due_at = parse(entry.get("next_check_at"))
            if not force and due_at is not None and due_at > now:
                results.append(
                    {
                        "connection_id": entry["id"],
                        "skipped": "not_due",
                        "next_check_at": entry.get("next_check_at"),
                        "outcome": entry.get("health"),
                    }
                )
                continue
            try:
                probed = self.test_connection(entry["id"], actor=actor, source=source)
                results.append(
                    {
                        "connection_id": entry["id"],
                        "vendor": entry["vendor"],
                        "outcome": probed["outcome"],
                        "detail": probed["detail"],
                        "refreshed_before_probe": probed["refreshed_before_probe"],
                    }
                )
            except CrmOAuthError as exc:
                # One connection that cannot be probed must not take the sweep
                # down. The research says polling is the room's own scheduler's
                # job, and a scheduler that stops because one tenant is
                # misconfigured stops polling every other tenant too.
                results.append(
                    {
                        "connection_id": entry["id"],
                        "vendor": entry["vendor"],
                        "skipped": "not_connected",
                        "detail": str(exc),
                        "outcome": "unknown",
                    }
                )
        counts = {"checked": 0, "ok": 0, "unauthorized": 0, "error": 0, "skipped": 0}
        for item in results:
            if item.get("skipped"):
                counts["skipped"] += 1
            else:
                counts["checked"] += 1
                counts[item.get("outcome", "unknown")] = (
                    counts.get(item.get("outcome", "unknown"), 0) + 1
                )
        return {
            "room_id": room_id,
            "forced": force,
            "counts": counts,
            "results": results,
            "source": "the sales room's own scheduler; nothing polls a CRM for us",
        }

    # -- token events ------------------------------------------------------ #

    def token_events(
        self, *, connection_id: str | None = None, kind: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        """The token lifecycle, newest first, as a record rather than as memory.

        Every attempt is here rather than in the fields of one call: which
        trigger refreshed a token, what the vendor answered, and - the one this
        workflow is measured on - that a 401 changed nothing but the health.
        """
        where: dict[str, Any] = {}
        if connection_id:
            where["connection_id"] = connection_id
        if kind:
            where["kind"] = kind
        records = (
            self.store.find(EVENT_COLLECTION, where, limit=1000)
            if where
            else self.store.list(EVENT_COLLECTION, limit=1000)
        )
        return [self._event_summary(record) for record in records][: max(1, min(int(limit), 1000))]

    # -- readiness --------------------------------------------------------- #

    def readiness(self, room_id: str) -> dict[str, Any]:
        """What is missing before a room's connections can do anything.

        The researched flow has six steps and each one has a precondition, so a
        connection that cannot finish is a state this reports precisely. A rule
        that does not fall through is a bug someone hits in production, and the
        fall-through here is "you cannot authorize, and here is the field to fix".
        """
        self.require_room(room_id)
        entries: list[dict[str, Any]] = []
        for summary in self.list_connections(room_id=room_id, limit=500):
            info = vendor_info(summary["vendor"])
            entries.append(
                {
                    "connection_id": summary["id"],
                    "label": summary["label"],
                    "vendor": summary["vendor"],
                    "scope": summary["scope"],
                    "status": summary["status"],
                    "health": summary["health"],
                    "ready_to_authorize": not summary["blockers"],
                    "ready_to_use": summary["status"] == STATUS_AUTHORIZED
                    and summary["credential"].get("readable_here", False),
                    "blockers": summary["blockers"],
                    "advisory": list(info.grant_requirements) + list(info.advisory),
                    "needs_action": summary["needs_action"],
                }
            )
        pending = [entry for entry in entries if entry["blockers"]]
        return {
            "room_id": room_id,
            "ready": bool(entries) and not pending,
            "count": len(entries),
            "blocked": len(pending),
            "vault_key_origin": self.vault.key_origin,
            "vault_key_warning": self.vault.key_warning(),
            "connections": entries,
        }

    # -- summaries --------------------------------------------------------- #

    def summarise(self, record: Mapping[str, Any]) -> dict[str, Any]:
        """A connection, with no credential in it.

        The sealed payload is never returned, only whether this process can open
        it and which field names are inside. ``has_client_secret`` is a boolean
        for the same reason.
        """
        data = dict(record.get("data") or {})
        now = self.now()
        vendor = str(data.get("vendor") or "")
        info = vendor_info(vendor) if vendor else None
        org_key = str(data.get("credential_org_key") or data.get("org_id") or "")
        credential_summary = self._credential_summary(record, org_key)
        expires_at = credential_summary.get("expires_at")
        expires_in = seconds_until(expires_at, now)
        ttl_known = expires_at is not None
        refresh_due_at = (
            iso(parse(expires_at) - timedelta(seconds=SKEW_SECONDS)) if parse(expires_at) else None
        )
        status = self._status(data, credential_summary)
        health = str(data.get("health") or HEALTH_UNKNOWN)
        summary: dict[str, Any] = {
            "id": record.get("id"),
            "vendor": vendor,
            "vendor_label": info.label if info else vendor,
            "label": data.get("label"),
            "tenant": data.get("tenant"),
            "room_id": record.get("room_id"),
            "scope": "room" if record.get("room_id") else "tenant",
            "client_id": data.get("client_id"),
            "has_client_secret": bool(data.get("has_client_secret")),
            "redirect_uri": data.get("redirect_uri"),
            "scopes": list(data.get("scopes") or []),
            "org_id": data.get("org_id"),
            "credential_org_key": data.get("credential_org_key"),
            "environment": data.get("environment"),
            "policy": data.get("policy"),
            "api_version": data.get("api_version"),
            "api_base_url": data.get("api_base_url"),
            "environment_url": data.get("environment_url"),
            "enabled": bool(data.get("enabled", True)),
            "status": status,
            "health": health,
            "health_detail": data.get("health_detail") or "",
            "last_error": data.get("last_error") or "",
            "expires_at": expires_at,
            "expires_in": expires_in,
            "refresh_due_at": refresh_due_at,
            "refresh_in": seconds_until(refresh_due_at, now),
            "ttl_known": ttl_known,
            "health_interval_seconds": data.get("health_interval_seconds")
            or DEFAULT_HEALTH_INTERVAL_SECONDS,
            "last_checked_at": data.get("last_checked_at"),
            "last_unauthorized_at": data.get("last_unauthorized_at"),
            "last_authorized_at": data.get("last_authorized_at"),
            "refresh_refused_at": data.get("refresh_refused_at"),
            "last_refresh_error": data.get("last_refresh_error") or "",
            "next_check_at": data.get("next_check_at"),
            "next_check_in": seconds_until(data.get("next_check_at"), now),
            "notes": data.get("notes") or "",
            "credential": credential_summary,
            "created_at": record.get("created_at"),
            "updated_at": record.get("updated_at"),
            "revision": record.get("revision"),
        }
        summary["blockers"] = self.blockers(summary)
        summary["needs_action"] = self.needs_action(summary)
        return summary

    def grant_summary(self, record: Mapping[str, Any]) -> dict[str, Any]:
        data = dict(record.get("data") or {})
        now = self.now()
        state = str(data.get("state_name") or GRANT_PENDING)
        return {
            "id": record.get("id"),
            "connection_id": data.get("connection_id"),
            "room_id": record.get("room_id"),
            "vendor": data.get("vendor"),
            "state": state,
            # The one-shot CSRF nonce, published deliberately. It is already in
            # ``authorize_url`` above, it stops being useful the moment the code
            # is exchanged, and a deployment whose redirect lands on another host
            # needs it to hand the callback back. Not a credential - which is why
            # the stored field is named ``state_nonce`` and the lifecycle field
            # ``state_name``: two different things called "state" is how a
            # callback ends up matched to the wrong authorization.
            "state_nonce": data.get("state_nonce"),
            "scopes": list(data.get("scopes") or []),
            "redirect_uri": data.get("redirect_uri"),
            "org_hint": data.get("org_hint"),
            "org_key": data.get("org_key"),
            "authorize_url": data.get("authorize_url"),
            "created_at": data.get("created_at"),
            "expires_at": data.get("expires_at"),
            "closed_at": data.get("closed_at"),
            "error": data.get("error") or "",
            "vendor_status": data.get("vendor_status"),
            "seconds_remaining": seconds_until(data.get("expires_at"), now)
            if state == GRANT_PENDING
            else None,
            "expired": bool(
                state == GRANT_PENDING and (seconds_until(data.get("expires_at"), now) or 0) <= 0
            ),
        }

    def _event_summary(self, record: Mapping[str, Any]) -> dict[str, Any]:
        data = dict(record.get("data") or {})
        return {
            "id": record.get("id"),
            "connection_id": data.get("connection_id"),
            "room_id": record.get("room_id"),
            "vendor": data.get("vendor"),
            "kind": data.get("kind"),
            "at": data.get("at"),
            **{key: value for key, value in data.items() if key not in ("id", "data")},
        }

    def overview(self) -> dict[str, Any]:
        """Counts across every connection, for the page header."""
        connections = self.list_connections(limit=1000)
        statuses: dict[str, int] = {name: 0 for name in CONNECTION_STATUSES}
        for entry in connections:
            statuses[entry["status"]] = statuses.get(entry["status"], 0) + 1
        healths: dict[str, int] = {name: 0 for name in HEALTH_VALUES}
        for entry in connections:
            healths[entry["health"]] = healths.get(entry["health"], 0) + 1
        events = self.token_events(limit=1000)
        return {
            "count": len(connections),
            "statuses": statuses,
            "health": healths,
            "needs_action": sum(1 for entry in connections if entry["needs_action"]),
            "blocked": sum(1 for entry in connections if entry["blockers"]),
            "pending_authorizations": len(self.list_grants(state_name=GRANT_PENDING, limit=1000)),
            "refreshed": sum(1 for event in events if event.get("kind") == EVENT_REFRESHED),
            "unauthorized_probes": sum(
                1
                for event in events
                if event.get("kind") == EVENT_TESTED and event.get("outcome") == HEALTH_UNAUTHORIZED
            ),
            "vault_key_origin": self.vault.key_origin,
            "vault_key_warning": self.vault.key_warning(),
            "registered_vendors": list(registered_vendors()),
            "unauthorized_is_not_a_refresh_trigger": UNAUTHORIZED_IS_NOT_A_REFRESH_TRIGGER,
        }

    # -- rules ------------------------------------------------------------- #

    def blockers(self, summary: Mapping[str, Any]) -> list[dict[str, str]]:
        """Every reason this connection cannot be authorized or used.

        All of them, not the first: an admin fixing a form should see the whole
        list, and a rule that stops at the first failure is a rule that will be
        re-run four times.
        """
        found: list[dict[str, str]] = []
        if not summary.get("client_id"):
            found.append(
                {
                    "code": "client_id_missing",
                    "why": "the researched authorize URL carries client_id; it comes from the vendor's app screen",
                }
            )
        if not summary.get("has_client_secret"):
            found.append(
                {
                    "code": "client_secret_missing",
                    "why": "the server-side exchange carries a client secret; the research names HubSpot's app Auth page as where it comes from",
                }
            )
        if not summary.get("redirect_uri"):
            found.append(
                {
                    "code": "redirect_uri_missing",
                    "why": "the researched authorize URL carries redirect_uri and the vendor redirects back to it",
                }
            )
        if not summary.get("scopes"):
            found.append(
                {
                    "code": "scopes_missing",
                    "why": "the consent screen grants scopes; an empty scope asks the vendor for nothing",
                }
            )
        info = vendor_info(str(summary.get("vendor") or "")) if summary.get("vendor") else None
        if (
            info
            and info.requires_org
            and not (summary.get("environment_url") or summary.get("org_id"))
        ):
            found.append(
                {
                    "code": "org_missing",
                    "why": f"{info.label}'s resource URL is templated on the org: {info.api_base_url}",
                }
            )
        if info and summary.get("vendor") == "salesforce":
            policy = str(summary.get("policy") or "")
            if policy and policy not in {"external_client_app", "connected_app"}:
                found.append(
                    {
                        "code": "policy_unsupported",
                        "why": "policy must be external_client_app or connected_app",
                    }
                )
        credential = summary.get("credential") or {}
        if credential.get("sealed") and not credential.get("readable_here"):
            found.append(
                {
                    "code": "vault_sealed_elsewhere",
                    "why": "the sealed credential was written under a different vault key; re-authorize to re-seal it",
                }
            )
        return found

    def _authorize_blockers(self, data: Mapping[str, Any]) -> list[dict[str, str]]:
        """The subset that stops the *authorize URL* being built at all."""
        codes = {
            "client_id_missing",
            "client_secret_missing",
            "redirect_uri_missing",
            "scopes_missing",
            "org_missing",
        }
        return [
            item
            for item in self.blockers(self.summarise({"id": "", "data": data}))
            if item["code"] in codes
        ]

    def needs_action(self, summary: Mapping[str, Any]) -> dict[str, str] | None:
        """The single next thing a person has to do, or ``None``.

        Ordered by the researched rules rather than by how bad the state looks:
        the TTL decides whether a refresh is the remedy, and only then does the
        last probe's answer get a say.
        """
        credential = summary.get("credential") or {}
        if credential.get("sealed") and not credential.get("readable_here"):
            return {
                "action": "re-authorize",
                "why": "the sealed credential was written under a different vault key, so this process cannot read it",
            }
        status = str(summary.get("status"))
        if status == STATUS_DISCONNECTED:
            return {"action": "none", "why": "the connection is disconnected"}
        if status == STATUS_PENDING:
            return {
                "action": "authorize",
                "why": "no credential yet: the researched flow starts at the vendor's consent screen",
            }
        if status == STATUS_EXPIRED:
            if summary.get("refresh_refused_at"):
                return {
                    "action": "re-authorize",
                    "why": "the vendor refused the refresh, and a refresh it refuses will not start working",
                }
            if credential.get("has_refresh_token"):
                return {
                    "action": "refresh",
                    "why": "the stored TTL has passed, and the TTL is what the research says drives refresh",
                }
            return {
                "action": "authorize",
                "why": "the stored TTL has passed and there is no refresh token, so the flow has to start again",
            }
        if summary.get("health") == HEALTH_UNAUTHORIZED:
            return {
                "action": "re-authorize",
                "why": "the vendor answered 401. A 401 is not a refresh trigger, so the remedy is the consent screen, not a refresh",
            }
        if summary.get("health") == HEALTH_ERROR:
            return {
                "action": "retry_test",
                "why": "the last probe failed for a reason a retry can fix",
            }
        if not summary.get("enabled", True):
            return {"action": "enable", "why": "the connection is switched off"}
        return None

    # -- internals --------------------------------------------------------- #

    def _status(self, data: Mapping[str, Any], credential: Mapping[str, Any]) -> str:
        """The credential lifecycle, computed rather than stored.

        ``disconnected`` is the state of a soft-deleted connection and is
        therefore reached through the audit log rather than through a live read;
        it is in the vocabulary because the audit row and the status name are
        the same word.
        """
        if not credential.get("sealed"):
            return STATUS_PENDING
        if not credential.get("readable_here"):
            # Unreadable is not expired and not authorized; report the one thing
            # that is true and let ``needs_action`` name the remedy.
            return STATUS_AUTHORIZED if data.get("last_authorized_at") else STATUS_PENDING
        expires_at = parse(credential.get("expires_at"))
        if expires_at is None:
            return STATUS_AUTHORIZED
        if self.now() >= expires_at:
            return STATUS_EXPIRED
        return STATUS_AUTHORIZED

    def _credential_summary(self, record: Mapping[str, Any], org_key: str) -> dict[str, Any]:
        """What may be said about the sealed credential without opening it.

        When this process *can* open it, the expiry is reported from inside,
        because a connection that cannot say when its token expires cannot tell
        an operator whether to wait.
        """
        empty = {
            "sealed": False,
            "readable_here": False,
            "org_key": org_key or None,
            "expires_at": None,
            "has_refresh_token": False,
            "fields": [],
            "sealed_key_id": None,
        }
        if not org_key or not record.get("id"):
            return empty
        row = self.vault.find(record["id"], org_key)
        if row is None:
            return empty
        base = self.vault.summarise(row)
        try:
            opened = self.vault.read(record["id"], org_key) or {}
        except Exception:  # noqa: BLE001 - a sealed row must not break a read
            return {**base, "expires_at": None, "has_refresh_token": False, "unreadable": True}
        return {
            **base,
            "expires_at": opened.get("expires_at"),
            "has_refresh_token": bool(opened.get("refresh_token")),
            "scope": opened.get("scope") or "",
            "org_id": opened.get("org_id") or org_key,
            "org_id_source": opened.get("org_id_source") or "",
            "issued_at": opened.get("issued_at"),
        }

    def _client_secret(self, connection_id: str) -> str:
        opened = self.vault.read(connection_id, APP_ORG_KEY) or {}
        return str(opened.get("client_secret") or "")

    def _resolve_org_key(self, data: Mapping[str, Any], response: TokenResponse) -> str:
        """The org/account the vault is keyed by.

        The vendor's own answer wins over the admin's choice, because a token
        for one org filed under another's key is exactly the failure the researched
        "keyed by the org/account id" rule exists to prevent.
        """
        return str(response.org_id or data.get("org_id") or "")

    def _expiry(self, now: datetime, expires_in: int | None, connector: CrmConnector) -> str | None:
        """When the token expires, or ``None`` when nobody said.

        ``None`` is a real state, not a shrug: it makes the next use refresh, and
        the summary reports ``ttl_known: false``. See
        ``unknown_ttl_refreshes_every_use`` in :mod:`dsr.crm_oauth.inferences`.
        """
        seconds = expires_in
        if seconds is None:
            return None
        try:
            seconds = int(seconds)
        except (TypeError, ValueError):
            return None
        if seconds <= 0:
            return None
        return iso(now + timedelta(seconds=seconds))

    def _grants_for(self, connection_id: str) -> list[dict[str, Any]]:
        return self.store.find(GRANT_COLLECTION, {"connection_id": connection_id}, limit=100)

    def _require_pending_grant(self, state: str, connection_id: str) -> dict[str, Any]:
        if not state:
            raise AuthorizationError(
                "the callback carried no state; this build binds a callback to the "
                "authorization that started it, because the research names only the "
                "code and two authorizations in flight would otherwise cross over"
            )
        for grant in self._grants_for(connection_id):
            data = grant["data"]
            if not secrets.compare_digest(str(data.get("state_nonce") or ""), state):
                continue
            if data.get("state_name") != GRANT_PENDING:
                raise AuthorizationError(
                    f"authorization {grant['id']} is {data.get('state_name')}, not pending; "
                    "a code can only be exchanged once"
                )
            remaining = seconds_until(data.get("expires_at"), self.now())
            if remaining is not None and remaining <= 0:
                raise AuthorizationError(
                    f"authorization {grant['id']} expired {abs(remaining or 0)}s ago; "
                    "start it again from the connection"
                )
            return grant
        raise AuthorizationError("no pending authorization matches that state for this connection")

    def _probe_base(self, record: Mapping[str, Any], use: TokenUse) -> str:
        """The API base URL for the probe, resolved from data rather than branches.

        Each connector declares the order its sources are tried in
        (``api_base_from``), so adding a vendor does not mean adding a branch
        here. A value that still contains the researched ``<org>`` placeholder is
        refused rather than requested: a URL with a literal ``<org>`` in it is a
        request to nothing.
        """
        data = record["data"]
        info = vendor_info(str(data.get("vendor") or ""))
        credential = use.credential
        for source in info.api_base_from:
            value = ""
            if source == "api_base_url":
                value = str(data.get("api_base_url") or "")
            elif source == "static":
                value = info.api_base_url
            elif source == "instance_url":
                value = str((credential.get("raw") or {}).get("instance_url") or "")
            elif source == "environment_url":
                value = str(data.get("environment_url") or "")
            elif source == "org_host" and data.get("org_id"):
                value = info.org_host_template.format(org=str(data.get("org_id")))
            if value and "<" not in value and "{" not in value:
                return value
        raise ConnectorConfigError(
            f"{info.label}: no API base URL for the probe. The token exchange did not "
            "return one and the connection does not carry one; set api_base_url on "
            "the connection."
        )

    def _probe_path(self, info: VendorInfo, data: Mapping[str, Any]) -> str:
        version = str(data.get("api_version") or info.api_version or "").lstrip("/")
        return info.probe_path.format(api_version=version)

    def _event(
        self,
        connection_id: str,
        kind: str,
        detail: Mapping[str, Any],
        *,
        actor: str | None = None,
        source: str,
        room_id: str | None = None,
        expires_at: str | None = None,
    ) -> dict[str, Any]:
        """One row in the token lifecycle log.

        A record rather than memory: which trigger refreshed a token, what the
        vendor answered, and that a 401 changed nothing, all have to outlive the
        request that learned them.
        """
        data: dict[str, Any] = dict(detail)
        data.update(
            {
                "connection_id": connection_id,
                "kind": kind,
                "at": iso(self.now()),
                "expires_at": expires_at,
                "vendor": "",
            }
        )
        if connection_id:
            record = self.store.get(connection_id)
            if record is not None:
                data["vendor"] = str(record["data"].get("vendor") or "")
        return self.store.create(
            EVENT_COLLECTION, data, room_id=room_id, actor=actor, source=source
        )


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _as_scopes(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        parts = [part for part in value.replace(",", " ").split() if part]
    elif isinstance(value, (list, tuple)):
        parts = [str(part).strip() for part in value if str(part).strip()]
    else:
        parts = [str(value).strip()]
    seen: set[str] = set()
    ordered: list[str] = []
    for part in parts:
        if part not in seen:
            seen.add(part)
            ordered.append(part)
    return ordered


def _as_interval(value: Any) -> int:
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        return DEFAULT_HEALTH_INTERVAL_SECONDS
    if seconds < 60:
        return 60
    return min(seconds, 7 * 24 * 3600)


def _classify(result: HttpResult) -> str:
    """One HTTP answer, one of three health outcomes.

    A 401 is ``unauthorized`` and nothing else, and in particular it is not
    ``error`` - the difference is the difference between "the vendor stopped
    accepting this" and "the request could not be delivered".
    """
    if result.status == 0:
        return HEALTH_ERROR
    if result.status in UNAUTHORIZED_STATUSES:
        return HEALTH_UNAUTHORIZED
    if 200 <= result.status < 300:
        return HEALTH_OK
    return HEALTH_ERROR


def _outcome_detail(result: HttpResult, outcome: str) -> str:
    if outcome == HEALTH_OK:
        return f"the vendor answered {result.status}"
    if outcome == HEALTH_UNAUTHORIZED:
        return (
            f"the vendor answered {result.status}: the token was rejected. A 401 is "
            "not a refresh trigger, so the stored token and its expiry are unchanged"
        )
    if result.status == 0:
        return f"the request could not be delivered: {result.error or 'no response'}"
    return f"the vendor answered {result.status}: {result.sample or result.error or 'no detail'}"


def _next_interval(data: Mapping[str, Any], outcome: str) -> int:
    """When to check again.

    A 5xx is usually transient and is retried soon. A 401 is not, and is **not**
    retried early: the researched rule is that a 401 is not a signal to fetch a
    new token, and shortening the interval would be treating it as one.
    """
    base = _as_interval(data.get("health_interval_seconds"))
    return ERROR_RETRY_SECONDS if outcome == HEALTH_ERROR else base


def describe_statuses() -> dict[str, Any]:
    """The state vocabulary, served so a client renders from one source."""
    return {
        "statuses": list(CONNECTION_STATUSES),
        "health": list(HEALTH_VALUES),
        "grant_states": list(GRANT_STATES),
        "event_types": list(EVENT_TYPES),
        "unauthorized_statuses": sorted(UNAUTHORIZED_STATUSES),
        "skew_seconds": SKEW_SECONDS,
        "grant_ttl_seconds": GRANT_TTL_SECONDS,
        "default_health_interval_seconds": DEFAULT_HEALTH_INTERVAL_SECONDS,
        "error_retry_seconds": ERROR_RETRY_SECONDS,
        "timeout_seconds": DEFAULT_TIMEOUT_SECONDS,
        "unauthorized_is_not_a_refresh_trigger": UNAUTHORIZED_IS_NOT_A_REFRESH_TRIGGER,
        "collections": list(COLLECTIONS),
        "notes": {
            "status": "the credential lifecycle, computed from the sealed credential at read time",
            "health": "the last probe's outcome; separate from status so a 401 and an expiry stay different facts",
            "credential": "the vault has no read route: no endpoint returns a sealed row",
        },
    }


__all__ = [
    "COLLECTIONS",
    "CONNECTION_COLLECTION",
    "CONNECTION_STATUSES",
    "CREDENTIAL_COLLECTION",
    "DEFAULT_HEALTH_INTERVAL_SECONDS",
    "ERROR_RETRY_SECONDS",
    "EVENT_COLLECTION",
    "EVENT_TYPES",
    "GRANT_COLLECTION",
    "GRANT_STATES",
    "GRANT_TTL_SECONDS",
    "HEALTH_ERROR",
    "HEALTH_OK",
    "HEALTH_UNAUTHORIZED",
    "HEALTH_UNKNOWN",
    "HEALTH_VALUES",
    "SKEW_SECONDS",
    "STATUS_AUTHORIZED",
    "STATUS_DISCONNECTED",
    "STATUS_EXPIRED",
    "STATUS_PENDING",
    "UNAUTHORIZED_IS_NOT_A_REFRESH_TRIGGER",
    "UNAUTHORIZED_STATUSES",
    "ConnectionDisabledError",
    "CrmOAuthConnections",
    "TokenUse",
    "describe_statuses",
    "iso",
    "parse",
    "utcnow",
]
