"""Connect a CRM org to the sales room over OAuth 2.0 (WF-034).

Built from ``docs/research/digital-sales-room-workflows/wf/WF-034.md``, which is
the specification. The researched decisions are the product: the four-interface
connector shape, the HubSpot authorize URL and its three parameters, the bearer
header on every later call, the credential vault keyed by the org/account id and
sealed at rest, refresh driven by the ``expires_in`` the token came with, and
the sentence that shapes the whole token half - **"`Unauthorized (401)` requests
are not a valid indicator that a new access token must be retrieved."**

The package
-----------

``dsr.crm_oauth.vocabulary``
    The researched contract as data: the six steps, the quoted evidence with the
    research line it came from, the four interface names, the gaps the research
    could not close, and the surfaces this build deliberately does not implement.
``dsr.crm_oauth.connectors``
    The four interfaces, the three built-in connectors, and the registry that
    makes a fourth one a plug-in rather than a fork.
``dsr.crm_oauth.vault``
    The credential vault: sealed at rest, keyed by org, and with no read route
    anywhere in the feature.
``dsr.crm_oauth.transport``
    The outbound seam. The tests and the seeder replace it, so neither opens a
    socket.
``dsr.crm_oauth.engine``
    :class:`~dsr.crm_oauth.engine.CrmOAuthConnections` - the six steps, the TTL
    rule, the health sweep, readiness, and the token-event log.
``dsr.crm_oauth.inferences``
    Every judgement call this package makes, named, with what would change it,
    served next to the sourced facts.
``dsr.crm_oauth.errors``
    One error hierarchy, so the HTTP layer maps each answer to one status.

Nothing here imports ``dsr.api``; the HTTP surface lives in
``dsr.features.wf034_connect_a_crm_org_to_the_sales_room_oa``, and nothing here
imports another feature's package either. The two shared seams it *does* use -
:class:`~dsr.store.RecordStore` and :data:`dsr.deps.StoreDep` - are the seams the
feature contract names.
"""

from dsr.crm_oauth.connectors import (
    GRANT_TYPE_AUTHORIZATION_CODE,
    GRANT_TYPE_REFRESH_TOKEN,
    RESPONSE_TYPE_CODE,
    AuthorizeRequest,
    CrmConnector,
    DataverseConnector,
    ExecuteRequest,
    HubSpotConnector,
    SalesforceConnector,
    TokenRequest,
    TokenResponse,
    VendorInfo,
    assert_no_token_in_url,
    build_authorize_url,
    connector,
    describe_connector,
    encode_scopes,
    register,
    registered_vendors,
    unregister,
    vendor_info,
)
from dsr.crm_oauth.engine import (
    COLLECTIONS,
    CONNECTION_COLLECTION,
    CONNECTION_STATUSES,
    CREDENTIAL_COLLECTION,
    DEFAULT_HEALTH_INTERVAL_SECONDS,
    ERROR_RETRY_SECONDS,
    EVENT_COLLECTION,
    EVENT_TYPES,
    GRANT_COLLECTION,
    GRANT_STATES,
    GRANT_TTL_SECONDS,
    HEALTH_ERROR,
    HEALTH_OK,
    HEALTH_UNAUTHORIZED,
    HEALTH_UNKNOWN,
    HEALTH_VALUES,
    SKEW_SECONDS,
    STATUS_AUTHORIZED,
    STATUS_DISCONNECTED,
    STATUS_EXPIRED,
    STATUS_PENDING,
    UNAUTHORIZED_IS_NOT_A_REFRESH_TRIGGER,
    UNAUTHORIZED_STATUSES,
    CrmOAuthConnections,
    TokenUse,
    describe_statuses,
    iso,
    parse,
    utcnow,
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
    UnknownVendorError,
    VaultSealedError,
    VendorRequestError,
)
from dsr.crm_oauth.inferences import INFERENCES, by_id, describe as describe_inferences
from dsr.crm_oauth.transport import (
    DEFAULT_TIMEOUT_SECONDS,
    HttpResult,
    Transport,
    UrllibTransport,
    form_body,
    redact_headers,
)
from dsr.crm_oauth.vault import (
    APP_ORG_KEY,
    DEMO_KEY,
    KEY_ENV,
    CredentialVault,
    VaultKey,
    open_sealed,
    resolve_key,
    seal,
)
from dsr.crm_oauth.vocabulary import (
    CONNECTOR_INTERFACES,
    RESEARCH_GAPS,
    SOURCED_QUOTES,
    VENDOR_IDS,
    describe_adjacent_surfaces,
    describe_vocabulary,
)

__all__ = [
    "APP_ORG_KEY",
    "COLLECTIONS",
    "CONNECTOR_INTERFACES",
    "CONNECTION_COLLECTION",
    "CONNECTION_STATUSES",
    "CREDENTIAL_COLLECTION",
    "DEFAULT_HEALTH_INTERVAL_SECONDS",
    "DEFAULT_TIMEOUT_SECONDS",
    "DEMO_KEY",
    "ERROR_RETRY_SECONDS",
    "ERROR_TYPES",
    "EVENT_COLLECTION",
    "EVENT_TYPES",
    "GRANT_COLLECTION",
    "GRANT_STATES",
    "GRANT_TTL_SECONDS",
    "GRANT_TYPE_AUTHORIZATION_CODE",
    "GRANT_TYPE_REFRESH_TOKEN",
    "HEALTH_ERROR",
    "HEALTH_OK",
    "HEALTH_UNAUTHORIZED",
    "HEALTH_UNKNOWN",
    "HEALTH_VALUES",
    "INFERENCES",
    "KEY_ENV",
    "RESPONSE_TYPE_CODE",
    "RESEARCH_GAPS",
    "SKEW_SECONDS",
    "SOURCED_QUOTES",
    "STATUS_AUTHORIZED",
    "STATUS_DISCONNECTED",
    "STATUS_EXPIRED",
    "STATUS_PENDING",
    "UNAUTHORIZED_IS_NOT_A_REFRESH_TRIGGER",
    "UNAUTHORIZED_STATUSES",
    "VENDOR_IDS",
    "AuthorizationError",
    "AuthorizeRequest",
    "ConnectionDisabledError",
    "ConnectionNotFoundError",
    "ConnectorConfigError",
    "CredentialVault",
    "CrmConnector",
    "CrmOAuthConnections",
    "CrmOAuthError",
    "DataverseConnector",
    "ExecuteRequest",
    "HttpResult",
    "HubSpotConnector",
    "NotConnectedError",
    "SalesforceConnector",
    "TokenExchangeError",
    "TokenRequest",
    "TokenResponse",
    "TokenUse",
    "Transport",
    "UnknownRoomError",
    "UnknownVendorError",
    "UrllibTransport",
    "VaultKey",
    "VaultSealedError",
    "VendorInfo",
    "VendorRequestError",
    "assert_no_token_in_url",
    "build_authorize_url",
    "by_id",
    "connector",
    "describe_adjacent_surfaces",
    "describe_connector",
    "describe_inferences",
    "describe_statuses",
    "describe_vocabulary",
    "encode_scopes",
    "form_body",
    "iso",
    "open_sealed",
    "parse",
    "redact_headers",
    "register",
    "registered_vendors",
    "resolve_key",
    "seal",
    "unregister",
    "utcnow",
    "vendor_info",
]

#: Every error type this feature's HTTP layer maps. Named here so the router and
#: the suite cannot drift: a refusal with no handler would be a 500, which is
#: the one answer a caller cannot act on.
ERROR_TYPES: tuple[type[Exception], ...] = (
    CrmOAuthError,
    AuthorizationError,
    TokenExchangeError,
    VendorRequestError,
    NotConnectedError,
    ConnectionDisabledError,
    VaultSealedError,
    ConnectionNotFoundError,
    UnknownRoomError,
    UnknownVendorError,
    ConnectorConfigError,
)
