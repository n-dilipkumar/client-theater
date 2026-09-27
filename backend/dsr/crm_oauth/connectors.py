"""The four researched interfaces, three connectors, and the registry they live in.

The research states the extension point precisely:

    "A third party adds a new vendor by implementing the same 4 interfaces —
    authorize-URL builder, code→token exchange, token refresh, and a
    bearer-authenticated request executor. Per-tenant credentials are already
    isolated in the integration record, so a connector is a plug-in, not a fork."

So a connector is any object with those four methods, and :func:`register`
accepts it. The suite registers a fourth vendor at test time and runs the whole
authorize → callback → refresh → probe flow through it, which is the only way to
keep the claim honest: "a connector is a plug-in" is a testable statement, not a
design note.

Two invariants hold for every connector registered here, and the suite checks
both for all of them:

* **The token never appears in a URL.** The research's claim is that every later
  call carries ``Authorization: Bearer token``, so the probe in particular has to
  put it in the header; a connector that put it in a path segment would write the
  credential into every log line the request touched.
* **A connector holds no per-tenant state.** Credentials live in the
  :mod:`dsr.crm_oauth.vault`, keyed by org, which is what "per-tenant credentials
  are already isolated in the integration record" has to mean in practice.

Only the HubSpot authorize URL is quoted in the research. Every other endpoint in
this file is an inference, and each one says so where it is defined.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, runtime_checkable
from urllib.parse import urlencode, urlsplit

from dsr.crm_oauth.errors import (
    ConnectorConfigError,
    TokenExchangeError,
    UnknownVendorError,
    VendorRequestError,
)
from dsr.crm_oauth.transport import (
    DEFAULT_TIMEOUT_SECONDS,
    HttpResult,
    Transport,
    form_body,
)
from dsr.crm_oauth.vocabulary import SOURCED_QUOTES

#: The grant type the authorization-code flow uses, and the ``response_type`` its
#: authorize step asks for. The *flow* is named by the research; these two values
#: are the OAuth 2.0 framework's, and the research quotes neither.
GRANT_TYPE_AUTHORIZATION_CODE = "authorization_code"
GRANT_TYPE_REFRESH_TOKEN = "refresh_token"
RESPONSE_TYPE_CODE = "code"

#: OAuth 2.0 encodes ``scope`` as space-separated values. The research quotes
#: ``scope=…`` for HubSpot and does not say how the values are separated; the
#: framework spec does. An inference, and the one line that would change it.
SCOPE_SEPARATOR = " "


# --------------------------------------------------------------------------- #
# The four requests
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class AuthorizeRequest:
    """Everything needed to build the URL the browser is sent to.

    ``org_hint`` is the admin's org/account choice, carried as a query parameter
    only by the vendors whose authorize screen is documented to accept one. It is
    never required: the research says the choice happens on the consent screen.
    """

    client_id: str
    redirect_uri: str
    scopes: tuple[str, ...]
    state: str
    org_hint: str = ""
    extra: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TokenRequest:
    """A code→token exchange, or a refresh.

    One shape for both, because the two requests differ in exactly one field and
    a separate class for each would be two places to keep in step.
    """

    client_id: str
    client_secret: str
    redirect_uri: str = ""
    code: str = ""
    refresh_token: str = ""
    extra: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TokenResponse:
    """What came back from a token endpoint, normalised.

    ``refresh_token`` is optional because a refresh response need not carry a new
    one. ``raw`` is the vendor's own body, kept so an operator can see exactly
    what the vendor said - and kept inside the sealed payload, never in a read.
    """

    access_token: str
    refresh_token: str = ""
    expires_in: int | None = None
    token_type: str = "Bearer"
    scope: str = ""
    org_id: str = ""
    raw: Mapping[str, Any] = field(default_factory=dict)

    def with_refresh(self, fallback: str) -> "TokenResponse":
        """A refresh response that omits a new refresh token keeps the old one."""
        return TokenResponse(
            access_token=self.access_token,
            refresh_token=self.refresh_token or fallback,
            expires_in=self.expires_in,
            token_type=self.token_type,
            scope=self.scope,
            org_id=self.org_id,
            raw=self.raw,
        )


@dataclass(frozen=True)
class ExecuteRequest:
    """One bearer-authenticated call.

    ``base_url`` and ``path`` are separate so a connector can compute the base
    from what the token exchange returned (Salesforce's ``instance_url``) without
    the caller having to know.
    """

    base_url: str
    path: str
    access_token: str
    method: str = "GET"
    query: Mapping[str, Any] = field(default_factory=dict)
    body: bytes | None = None
    headers: Mapping[str, str] = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# The interface
# --------------------------------------------------------------------------- #


@runtime_checkable
class CrmConnector(Protocol):
    """The researched four. A vendor is a plug-in, not a fork."""

    #: Stable id, used in URLs and in the ``vendor`` field of every record.
    vendor: str

    def authorize_url(self, request: AuthorizeRequest) -> str:
        """Build the URL the browser is sent to."""

    def exchange_code(self, request: TokenRequest, *, transport: Transport) -> TokenResponse:
        """Trade the redirect code for an access token and a refresh token."""

    def refresh(self, request: TokenRequest, *, transport: Transport) -> TokenResponse:
        """Renew the access token from the refresh token."""

    def execute(self, request: ExecuteRequest, *, transport: Transport) -> HttpResult:
        """Make one bearer-authenticated request."""


@dataclass(frozen=True)
class VendorInfo:
    """What the surface may say about a vendor, and what it needs to run.

    ``sources`` names the researched document each vendor's facts came from, so
    the page shows the provenance next to the endpoint rather than the reviewer
    having to ask.
    """

    vendor: str
    label: str
    authorize_endpoint: str
    token_endpoint: str
    api_base_url: str
    probe_path: str
    probe_query: Mapping[str, str] = field(default_factory=dict)
    #: Where the probe's API base URL may come from, in the order to try them.
    #: Data, not a branch in the engine, so a fourth vendor needs no code here.
    #: Sources: ``api_base_url`` (the connection), ``static`` (this record),
    #: ``instance_url`` (the token response), ``environment_url`` (the
    #: connection), ``org_host`` (built from the connection's org id).
    api_base_from: tuple[str, ...] = ("api_base_url", "static")
    #: Used only by the ``org_host`` source. ``{org}`` is the placeholder.
    org_host_template: str = ""
    grant_type: str = "client_credentials"  # informational only; see below
    default_scopes: tuple[str, ...] = ()
    suggested_scopes: tuple[str, ...] = ()
    org_id_keys: tuple[str, ...] = ()
    requires_org: bool = False
    grant_requirements: tuple[str, ...] = ()
    advisory: tuple[str, ...] = ()
    docs: str = ""
    researched: tuple[str, ...] = ()
    inferences: tuple[str, ...] = ()
    settings: tuple[dict[str, str], ...] = ()
    extra_authorize_params: Mapping[str, str] = field(default_factory=dict)
    api_version: str = ""
    environment_hosts: Mapping[str, str] = field(default_factory=dict)

    def describe(self) -> dict[str, Any]:
        return {
            "vendor": self.vendor,
            "label": self.label,
            "authorize_endpoint": self.authorize_endpoint,
            "token_endpoint": self.token_endpoint,
            "api_base_url": self.api_base_url,
            "api_base_from": list(self.api_base_from),
            "org_host_template": self.org_host_template,
            "probe_path": self.probe_path,
            "probe_query": dict(self.probe_query),
            "api_version": self.api_version,
            "scopes": {
                "required": True,
                "why": (
                    "The researched authorize URL carries a scope parameter and "
                    "the consent screen 'grant[s] scopes', so a connection with no "
                    "scopes has nothing to ask the vendor for."
                ),
                "suggested": list(self.suggested_scopes),
                "suggested_is_inference": True,
            },
            "org_id_keys": list(self.org_id_keys),
            "org_id_keys_are_inference": True,
            "requires_org": self.requires_org,
            "grant_requirements": list(self.grant_requirements),
            "advisory": list(self.advisory),
            "docs": self.docs,
            "researched": list(self.researched),
            "inferences": list(self.inferences),
            "settings": list(self.settings),
            "environment_hosts": dict(self.environment_hosts),
        }


# --------------------------------------------------------------------------- #
# Shared connector behaviour
# --------------------------------------------------------------------------- #


def encode_scopes(scopes: tuple[str, ...] | list[str]) -> str:
    """Space-separated, which is what the OAuth 2.0 framework specifies."""
    return SCOPE_SEPARATOR.join(scope for scope in scopes if scope)


def build_authorize_url(endpoint: str, params: Mapping[str, Any]) -> str:
    """Join a query onto an endpoint, dropping empty values.

    An empty parameter is not the same as an absent one to a consent screen: it
    renders as a blank scope picker. Empty values are dropped so a connection
    that has no org hint yet does not send ``org_hint=``.
    """
    pairs = [(key, str(value)) for key, value in params.items() if value not in (None, "")]
    if not pairs:
        return endpoint
    joiner = "&" if urlsplit(endpoint).query else "?"
    return f"{endpoint}{joiner}{urlencode(pairs)}"


def assert_no_token_in_url(url: str, token: str) -> None:
    """The invariant the research's bearer claim rests on, enforced at runtime.

    Not only the token itself: anything long enough to be one. A URL that
    carries a credential ends up in a proxy log, an access log and a support
    ticket, and none of those redact it.
    """
    if token and (token in url or re.search(r"(access_token|token)=[^&]+", url)):
        raise ConnectorConfigError(
            "a bearer token must travel in the Authorization header, not in a URL; "
            f"refusing to build {url!r}"
        )


def _token_response_from(result: HttpResult, vendor: str, org_id_keys: tuple[str, ...]) -> TokenResponse:
    """Turn a token endpoint's answer into a :class:`TokenResponse`, or refuse."""
    if result.status in (0,) and not result.ok:
        raise VendorRequestError(
            f"{vendor} token endpoint could not be reached: {result.error or 'no response'}"
        )
    if not result.ok:
        body = result.json()
        error_code = str(body.get("error") or "")
        detail = str(
            body.get("error_description")
            or body.get("description")
            or body.get("message")
            or result.sample
            or result.error
            or "no detail given"
        )
        message = f"{vendor} refused the request: {error_code}: {detail}" if error_code else (
            f"{vendor} refused the request: {detail}"
        )
        raise TokenExchangeError(message, vendor_status=result.status, vendor_body=result.sample)

    body = result.json()
    access_token = str(body.get("access_token") or "")
    if not access_token:
        raise TokenExchangeError(
            f"{vendor} answered {result.status} with no access_token",
            vendor_status=result.status,
            vendor_body=result.sample,
        )
    expires_raw = body.get("expires_in")
    expires_in: int | None
    try:
        expires_in = int(expires_raw) if expires_raw is not None else None
    except (TypeError, ValueError):
        expires_in = None
    org_id = ""
    for key in org_id_keys:
        value = body.get(key)
        if value:
            org_id = str(value)
            break
    return TokenResponse(
        access_token=access_token,
        refresh_token=str(body.get("refresh_token") or ""),
        expires_in=expires_in,
        token_type=str(body.get("token_type") or "Bearer"),
        scope=str(body.get("scope") or ""),
        org_id=org_id,
        raw=body,
    )


class _BaseConnector:
    """The parts of a connector that are the same for all three vendors.

    Subclasses declare their endpoints and their own ``resolve_org_id``; the two
    token requests and the bearer call are identical in shape, so they live here
    once and cannot drift apart.
    """

    info: VendorInfo

    @property
    def vendor(self) -> str:
        """The connector's id, from its own published metadata.

        A property rather than a second source of truth: the id appears once, in
        ``VendorInfo.vendor``, so the registry key and the description cannot
        disagree.
        """
        return self.info.vendor

    # -- interface -------------------------------------------------------- #

    def authorize_url(self, request: AuthorizeRequest) -> str:
        if not request.client_id:
            raise ConnectorConfigError(f"{self.info.vendor}: client_id is required")
        if not request.redirect_uri:
            raise ConnectorConfigError(f"{self.info.vendor}: redirect_uri is required")
        if not request.scopes:
            raise ConnectorConfigError(
                f"{self.info.vendor}: at least one scope is required; the consent "
                "screen grants scopes and an empty scope asks the vendor for nothing"
            )
        params: dict[str, Any] = {
            "client_id": request.client_id,
            "redirect_uri": request.redirect_uri,
            "response_type": RESPONSE_TYPE_CODE,
            "scope": encode_scopes(request.scopes),
            "state": request.state,
        }
        if request.org_hint:
            params["org_hint"] = request.org_hint
        params.update(self.info.extra_authorize_params)
        params.update({k: v for k, v in request.extra.items() if v not in (None, "")})
        return build_authorize_url(self.authorize_endpoint(request), params)

    def exchange_code(self, request: TokenRequest, *, transport: Transport) -> TokenResponse:
        if not request.code:
            raise ConnectorConfigError(f"{self.info.vendor}: code is required")
        return self._token(
            request,
            transport,
            fields={
                "grant_type": GRANT_TYPE_AUTHORIZATION_CODE,
                "code": request.code,
                "redirect_uri": request.redirect_uri,
            },
        )

    def refresh(self, request: TokenRequest, *, transport: Transport) -> TokenResponse:
        if not request.refresh_token:
            raise TokenExchangeError(
                f"{self.info.vendor} has no refresh token for this connection; the "
                "connection has to be authorized again"
            )
        return self._token(
            request,
            transport,
            fields={
                "grant_type": GRANT_TYPE_REFRESH_TOKEN,
                "refresh_token": request.refresh_token,
            },
        ).with_refresh(request.refresh_token)

    def execute(self, request: ExecuteRequest, *, transport: Transport) -> HttpResult:
        url = self._request_url(request)
        assert_no_token_in_url(url, request.access_token)
        headers = {
            "Authorization": f"Bearer {request.access_token}",
            "Accept": "application/json",
            **dict(request.headers),
        }
        return transport.request(
            request.method, url, body=request.body, headers=headers, timeout=DEFAULT_TIMEOUT_SECONDS
        )

    # -- hooks ------------------------------------------------------------ #

    def authorize_endpoint(self, request: AuthorizeRequest) -> str:
        """The authorize URL for one request. Overridden where it is computed."""
        return self.info.authorize_endpoint

    def token_endpoint(self, request: TokenRequest) -> str:
        """The token URL for one request. Overridden where it is computed."""
        return self.info.token_endpoint

    def resolve_org_id(self, response: TokenResponse) -> str:
        """The org/account id the vault is keyed by, as far as the vendor said."""
        return response.org_id

    # -- internals -------------------------------------------------------- #

    def _token(
        self, request: TokenRequest, transport: Transport, *, fields: Mapping[str, Any]
    ) -> TokenResponse:
        if not request.client_id or not request.client_secret:
            raise ConnectorConfigError(
                f"{self.info.vendor}: client_id and client_secret are required for a "
                "server-side token request"
            )
        body = {
            "client_id": request.client_id,
            "client_secret": request.client_secret,
            **fields,
            **{k: v for k, v in request.extra.items() if v not in (None, "")},
        }
        result = transport.request(
            "POST",
            self.token_endpoint(request),
            body=form_body(body),
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
            timeout=DEFAULT_TIMEOUT_SECONDS,
        )
        response = _token_response_from(result, self.info.vendor, self.info.org_id_keys)
        return TokenResponse(
            access_token=response.access_token,
            refresh_token=response.refresh_token,
            expires_in=response.expires_in,
            token_type=response.token_type,
            scope=response.scope,
            org_id=self.resolve_org_id(response) or response.org_id,
            raw=response.raw,
        )

    def _request_url(self, request: ExecuteRequest) -> str:
        base = str(request.base_url or "").rstrip("/")
        if not base:
            raise ConnectorConfigError(
                f"{self.info.vendor}: no API base URL; the token exchange did not "
                "return one and the connection does not carry one"
            )
        path = str(request.path or "")
        url = f"{base}/{path.lstrip('/')}" if path else base
        pairs = [(key, str(value)) for key, value in request.query.items() if value not in (None, "")]
        if pairs:
            url = f"{url}{'&' if urlsplit(url).query else '?'}{urlencode(pairs)}"
        return url


# --------------------------------------------------------------------------- #
# Salesforce
# --------------------------------------------------------------------------- #


SALESFORCE_POLICIES: tuple[dict[str, str], ...] = (
    {
        "policy": "external_client_app",
        "label": "External client app",
        "recommended": "true",
        "note": SOURCED_QUOTES["connected_apps_restricted_spring_26"]["quote"],
    },
    {
        "policy": "connected_app",
        "label": "Connected app (legacy)",
        "recommended": "false",
        "note": (
            "Still usable: 'You can continue to use existing connected apps during "
            "and after Spring '26.' Creation is restricted."
        ),
    },
)


class SalesforceConnector(_BaseConnector):
    """Salesforce, over an external client app or a legacy connected app.

    The two app kinds differ in the *policy screen* the research names
    ("Salesforce Setup → External Client Apps (or legacy Connected App) policy
    screen"), not in the request this room makes, so the authorize URL is the
    same for both and the choice is recorded on the connection where an operator
    can see which one they authorized against.
    """

    def __init__(self) -> None:
        self.info = VendorInfo(
            vendor="salesforce",
            label="Salesforce",
            authorize_endpoint="https://login.salesforce.com/services/oauth2/authorize",
            token_endpoint="https://login.salesforce.com/services/oauth2/token",
            api_base_url="https://<org>.my.salesforce.com",
            # instance_url first: the researched text never gives Salesforce's
            # host, and the token exchange's is the one that is right.
            api_base_from=("instance_url", "api_base_url", "static"),
            probe_path="/services/data/{api_version}/limits",
            api_version="v60.0",
            suggested_scopes=("api", "refresh_token", "offline_access"),
            org_id_keys=("instance_url",),
            grant_requirements=(
                "An external client app in Setup → External Client Apps, or an "
                "existing connected app.",
            ),
            advisory=(
                SOURCED_QUOTES["connected_apps_restricted_spring_26"]["quote"],
            ),
            docs="https://developer.salesforce.com/docs/platform/api-rest/guide/intro-oauth-and-connected-apps.html",
            researched=(
                "salesforce_needs_an_authorization",
                "connected_apps_restricted_spring_26",
                "oauth_grants_restricted_access",
                "bearer_header_on_every_call",
            ),
            inferences=(
                "The authorize and token endpoint URLs are inferences: the flow "
                "detail pages live in help.salesforce.com and the research could "
                "not read them.",
                "The API version v60.0 and the probe path are inferences; the "
                "research names no probe.",
                "`instance_url` as the org key and as the API base is an "
                "inference; the research names neither field.",
            ),
            settings=(
                {
                    "name": "api_version",
                    "label": "REST API version",
                    "default": "v60.0",
                    "why": "the probe path is versioned; a deployment on an older org overrides it",
                },
                {
                    "name": "policy",
                    "label": "App kind",
                    "options": "external_client_app | connected_app",
                    "default": "external_client_app",
                    "why": SOURCED_QUOTES["connected_apps_restricted_spring_26"]["quote"],
                },
                {
                    "name": "api_base_url",
                    "label": "API base URL",
                    "default": "from the token exchange's instance_url",
                    "why": "a sandbox or a custom domain does not use login.salesforce.com",
                },
            ),
            environment_hosts={
                "production": "https://login.salesforce.com",
                "sandbox": "https://test.salesforce.com",
            },
        )

    def authorize_endpoint(self, request: AuthorizeRequest) -> str:
        environment = str(request.extra.get("environment") or "production")
        return self.info.environment_hosts.get(environment, self.info.environment_hosts["production"]) + (
            "/services/oauth2/authorize"
        )

    def token_endpoint(self, request: TokenRequest) -> str:
        environment = str(request.extra.get("environment") or "production")
        return self.info.environment_hosts.get(environment, self.info.environment_hosts["production"]) + (
            "/services/oauth2/token"
        )

    def resolve_org_id(self, response: TokenResponse) -> str:
        """The ``instance_url`` host is the org, when the vendor sent one."""
        instance = str(response.raw.get("instance_url") or "")
        if instance:
            return urlsplit(instance).netloc or instance
        return ""


# --------------------------------------------------------------------------- #
# HubSpot
# --------------------------------------------------------------------------- #


class HubSpotConnector(_BaseConnector):
    """HubSpot, whose authorize URL is the one the research quotes.

    ``https://app.hubspot.com/oauth/authorize?client_id=…&scope=…&redirect_uri=…``
    is reproduced key for key by the base connector, and the suite asserts the
    three researched parameters are present and spelled the same way.
    """

    def __init__(self) -> None:
        self.info = VendorInfo(
            vendor="hubspot",
            label="HubSpot",
            authorize_endpoint="https://app.hubspot.com/oauth/authorize",
            token_endpoint="https://api.hubapi.com/oauth/v1/token",
            api_base_url="https://api.hubapi.com",
            # A cheap read of a small page, with the token in the header only.
            # `/oauth/v1/access-tokens/{token}` is the more obvious choice and
            # the wrong one: it puts the credential in the URL.
            probe_path="/crm/v3/objects/contacts",
            probe_query={"limit": "1"},
            suggested_scopes=("crm.objects.contacts.read", "offline_access"),
            org_id_keys=("hub_domain", "portalId"),
            grant_requirements=(
                "Users installing apps in their HubSpot account must either be a "
                "Super Admin or have HubSpot Marketplace Access permissions.",
            ),
            advisory=(
                SOURCED_QUOTES["hubspot_installer_permission"]["quote"],
                SOURCED_QUOTES["hubspot_oauth_is_mandatory"]["quote"],
            ),
            docs="https://developers.hubspot.com/docs/apps/developer-platform/build-apps/authentication/oauth/working-with-oauth",
            researched=(
                "hubspot_authorize_url",
                "callback_carries_a_code",
                "hubspot_installer_permission",
                "hubspot_oauth_is_mandatory",
                "ttl_is_the_apps_job",
            ),
            inferences=(
                "The token endpoint URL is an inference: the research names the "
                "token endpoint but not its URL.",
                "The probe path is an inference: the research asks for a low-cost "
                "authenticated endpoint without naming one.",
                "`hub_domain` / `portalId` as the org key is an inference.",
            ),
            settings=(
                {
                    "name": "scopes",
                    "label": "Scopes",
                    "default": "crm.objects.contacts.read offline_access",
                    "why": "HubSpot's consent screen grants what is asked for, and "
                    "a sales room that cannot read contacts cannot verify a token",
                },
            ),
        )


# --------------------------------------------------------------------------- #
# Dataverse
# --------------------------------------------------------------------------- #


class DataverseConnector(_BaseConnector):
    """Dynamics 365 / Dataverse, on the base URL the research quotes.

    ``https://<org>.api.crm.dynamics.com/api/data/v9.2/…`` is sourced, including
    the ``<org>`` placeholder, which is why a Dataverse connection must carry the
    environment host: the researched URL is templated, and a connection without
    the org cannot produce one.

    No Dataverse authentication page was read. The authorize endpoint, the token
    endpoint and the probe path below are all inferences, and the connector says
    so in its own ``inferences`` list rather than leaving a reader to assume the
    base URL's sourcing carries over to the rest.
    """

    #: The researched resource URL, as a template. ``{org}`` is literal in the
    #: research: "``https://<org>.api.crm.dynamics.com/api/data/v9.2/…``".
    RESOURCE_TEMPLATE = "https://<org>.api.crm.dynamics.com/api/data/v9.2/…"
    API_VERSION = "v9.2"

    def __init__(self) -> None:
        self.info = VendorInfo(
            vendor="dataverse",
            label="Dynamics (Dataverse)",
            authorize_endpoint="https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize",
            token_endpoint="https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
            api_base_url="https://<org>.api.crm.dynamics.com",
            api_base_from=("api_base_url", "environment_url", "org_host", "static"),
            org_host_template="https://{org}.api.crm.dynamics.com",
            probe_path=f"/api/data/{self.API_VERSION}/WhoAmI",
            api_version=self.API_VERSION,
            suggested_scopes=("https://contoso.crm.dynamics.com/.default",),
            org_id_keys=("resource", "instance_url"),
            requires_org=True,
            grant_requirements=(
                "An Azure AD app registration the admin consents to for the "
                "environment's org.",
            ),
            advisory=(
                "No Dataverse authentication page was read for this research, so "
                "the endpoints below are inferences rather than quotes.",
            ),
            docs="https://learn.microsoft.com/en-us/power-apps/developer/data-platform/webapi/execute-batch-operations-using-web-api?view=dataverse-latest",
            researched=("bearer_header_on_every_call", "code_to_token_is_the_transformation"),
            inferences=(
                "The authorize and token endpoints are inferences. The research "
                "states: 'no Dataverse-specific auth quote is claimed'.",
                "The probe path (WhoAmI) is an inference; only the base URL shape "
                "and the bearer header are sourced.",
                "`resource` as the org key is an inference.",
            ),
            settings=(
                {
                    "name": "environment_url",
                    "label": "Environment host",
                    "default": "https://<org>.api.crm.dynamics.com",
                    "why": "the researched resource URL is templated on the org",
                },
                {
                    "name": "tenant",
                    "label": "Azure AD tenant",
                    "default": "common",
                    "why": "the consent screen is an Azure AD one",
                },
            ),
        )

    def _tenant(self, extra: Mapping[str, Any]) -> str:
        return str(extra.get("tenant") or "common") or "common"

    def authorize_endpoint(self, request: AuthorizeRequest) -> str:
        return self.info.authorize_endpoint.format(tenant=self._tenant(request.extra))

    def token_endpoint(self, request: TokenRequest) -> str:
        return self.info.token_endpoint.format(tenant=self._tenant(request.extra))

    def resolve_org_id(self, response: TokenResponse) -> str:
        """``resource`` in a Dataverse token response is the environment URL."""
        resource = str(response.raw.get("resource") or "")
        if resource:
            return urlsplit(resource).netloc or resource
        return ""


# --------------------------------------------------------------------------- #
# The registry
# --------------------------------------------------------------------------- #

_REGISTRY: dict[str, CrmConnector] = {}
_INFOS: dict[str, VendorInfo] = {}


def register(connector: CrmConnector, *, replace: bool = False) -> CrmConnector:
    """Register a connector. The researched extension point.

    Accepts any object with the four methods, so a third party adds a vendor
    here without touching anything else in the package. A duplicate id is
    refused unless ``replace=True``, because two connectors claiming one vendor
    would make the winner load-order dependent - the same reason the plugin host
    refuses a duplicate route or error handler.
    """
    vendor = str(getattr(connector, "vendor", "") or "").strip()
    if not vendor:
        raise ValueError("a connector must carry a non-empty vendor id")
    for name in ("authorize_url", "exchange_code", "refresh", "execute"):
        if not callable(getattr(connector, name, None)):
            raise ValueError(f"connector {vendor!r} does not implement {name}()")
    if vendor in _REGISTRY and not replace:
        raise ValueError(f"a connector for {vendor!r} is already registered; pass replace=True to swap it")
    _REGISTRY[vendor] = connector
    info = getattr(connector, "info", None)
    if isinstance(info, VendorInfo):
        _INFOS[vendor] = info
    return connector


def unregister(vendor: str) -> None:
    """Remove a connector. Used by the suite to register a throwaway vendor."""
    _REGISTRY.pop(vendor, None)
    _INFOS.pop(vendor, None)


def connector(vendor: str) -> CrmConnector:
    """The connector for a vendor id, or a refusal naming the ones that exist."""
    try:
        return _REGISTRY[str(vendor or "")]
    except KeyError:
        known = ", ".join(registered_vendors()) or "none"
        raise UnknownVendorError(f"no connector is registered for {vendor!r}; known vendors: {known}") from None


def registered_vendors() -> tuple[str, ...]:
    return tuple(sorted(_REGISTRY))


def vendor_info(vendor: str) -> VendorInfo:
    """The published description of a vendor, refusing for an unknown one."""
    connector(str(vendor or ""))
    info = _INFOS.get(str(vendor))
    if info is None:
        # A connector registered without a VendorInfo is still a working
        # connector; the surface describes its four methods and says it carries
        # no published metadata rather than pretending it does.
        return VendorInfo(
            vendor=str(vendor),
            label=str(vendor),
            authorize_endpoint="",
            token_endpoint="",
            api_base_url="",
            probe_path="",
            inferences=("registered at runtime without a VendorInfo; nothing is claimed about its endpoints",),
        )
    return info


def describe_connector(vendor: str) -> dict[str, Any]:
    """What the surface publishes about a connector: its metadata and its four methods.

    ``holds_state`` is reported rather than asserted. The researched reason a
    connector can be a plug-in is that "per-tenant credentials are already
    isolated in the integration record", so a connector that kept a credential
    on itself would be the thing that breaks that; the suite asserts the three
    built-ins and the test-time fourth hold no instance attributes beyond their
    published metadata.
    """
    entry = connector(vendor)
    attributes = sorted(vars(entry)) if hasattr(entry, "__dict__") else []
    return {
        "vendor": vendor,
        "implements": [
            {"name": "authorize_url", "callable": callable(getattr(entry, "authorize_url", None))},
            {"name": "exchange_code", "callable": callable(getattr(entry, "exchange_code", None))},
            {"name": "refresh", "callable": callable(getattr(entry, "refresh", None))},
            {"name": "execute", "callable": callable(getattr(entry, "execute", None))},
        ],
        "instance_attributes": attributes,
        "holds_state": [name for name in attributes if name != "info"],
        "info": vendor_info(vendor).describe(),
    }


register(SalesforceConnector())
register(HubSpotConnector())
register(DataverseConnector())


__all__ = [
    "GRANT_TYPE_AUTHORIZATION_CODE",
    "GRANT_TYPE_REFRESH_TOKEN",
    "RESPONSE_TYPE_CODE",
    "SALESFORCE_POLICIES",
    "SCOPE_SEPARATOR",
    "AuthorizeRequest",
    "CrmConnector",
    "DataverseConnector",
    "ExecuteRequest",
    "HubSpotConnector",
    "SalesforceConnector",
    "TokenRequest",
    "TokenResponse",
    "VendorInfo",
    "assert_no_token_in_url",
    "build_authorize_url",
    "connector",
    "describe_connector",
    "encode_scopes",
    "register",
    "registered_vendors",
    "unregister",
    "vendor_info",
]
