"""One error hierarchy, so the HTTP layer maps each answer to exactly one status.

Every refusal in this package is a caller's to fix, an installation's to finish,
or an upstream vendor's to answer, and those three are different things to a
client. Keeping them as distinct types is what lets
:mod:`dsr.features.wf034_connect_a_crm_org_to_the_sales_room_oa` map each to a
status without pattern-matching on a message.

``RecordNotFound`` and ``AuditError`` are deliberately *not* claimed here: the
core app already maps them, and two handlers for one type is a collision the
plugin host refuses.
"""

from __future__ import annotations


class CrmOAuthError(Exception):
    """Base of every refusal in :mod:`dsr.crm_oauth`.

    Answered ``400``: well-formed JSON asking for something this layer will not
    do, and the caller's to fix. Subclasses exist where the answer differs.
    """


class UnknownVendorError(CrmOAuthError):
    """A vendor id no registered connector claims."""


class ConnectorConfigError(CrmOAuthError):
    """A connection is missing what its connector's authorize step needs.

    The researched authorize URL carries ``client_id``, ``scope`` and
    ``redirect_uri``, and the Dataverse resource URL is templated on the org, so
    a connection missing any of those cannot produce a URL the vendor will
    accept. Refusing here is what keeps a half-configured connection from
    reaching a consent screen.
    """


class AuthorizationError(CrmOAuthError):
    """A callback that does not match a pending authorization.

    Raised for an unknown, already-used, cancelled or expired ``state``, and for
    a callback that names a connection this installation does not have. The
    researched callback carries a ``code``; the ``state`` that names the
    connection is an inference, recorded as such in
    :mod:`dsr.crm_oauth.inferences`.
    """


class TokenExchangeError(CrmOAuthError):
    """The vendor refused a code, or refused a refresh token.

    The vendor's own status and body excerpt are carried on the exception, never
    the request: the request body holds the client secret.
    """

    def __init__(self, message: str, *, vendor_status: int = 0, vendor_body: str = "") -> None:
        super().__init__(message)
        self.vendor_status = vendor_status
        self.vendor_body = vendor_body


class VendorRequestError(CrmOAuthError):
    """The vendor's own endpoint failed in a way this layer cannot interpret.

    A 5xx, a timeout, a DNS failure. Distinct from
    :class:`TokenExchangeError` because the caller can retry it: nothing about
    the request was wrong.
    """


class NotConnectedError(CrmOAuthError):
    """The connection has no credential, so there is nothing to send.

    ``428``: the request is well formed and this installation is simply not set
    up to answer it yet. The frontend's ``apiRequest`` carries the status for
    exactly this distinction.
    """


class ConnectionDisabledError(NotConnectedError):
    """The connection exists and is switched off.

    Its own type, and its own handler, so a client can say "turn it on" rather
    than "finish setting it up" - different work for a person, and both of them
    ``428`` because in neither case is the request wrong.
    """


class VaultSealedError(CrmOAuthError):
    """A credential row is sealed with a key this process does not hold.

    Also ``428``. Not a corruption: the row is intact and the fix is to
    re-authorize, which re-seals it under the current key. Raised rather than
    swallowed, because returning a wrong token would fail later and further
    away.
    """


class ConnectionNotFoundError(CrmOAuthError):
    """No live connection record for this id. ``404``."""


class UnknownRoomError(CrmOAuthError):
    """No live room for this id. ``404``.

    The core app maps ``RecordNotFound`` to 404 as well, but a room that a
    *room-scoped route* was asked about is this layer's answer to give, and the
    two types are different so neither handler can shadow the other.
    """


__all__ = [
    "AuthorizationError",
    "ConnectionDisabledError",
    "ConnectionNotFoundError",
    "ConnectorConfigError",
    "CrmOAuthError",
    "NotConnectedError",
    "TokenExchangeError",
    "UnknownRoomError",
    "UnknownVendorError",
    "VaultSealedError",
    "VendorRequestError",
]
