"""WF-122: the error types this workflow raises and the host maps.

Three types are declared here, and nothing else in the product raises them. The
feature host refuses a second feature that registers a handler for the same
type, so a collision is a load failure rather than a silent shadow.

None of the three is a Python builtin. A handler registered for ``ValueError``
or ``PermissionError`` directly would intercept those exceptions across the
whole product, so each type subclasses a builtin and is registered by its own
name instead.
"""

from __future__ import annotations

from collections.abc import Mapping


class DisclosureError(ValueError):
    """A section, a recipient or a tier this workflow will not accept.

    Carries a field-keyed map, because a seller filling in a form needs the
    message beside the input that caused it. The HTTP layer renders the map as
    ``errors``.
    """

    def __init__(self, message: str, errors: Mapping[str, str] | None = None) -> None:
        super().__init__(message)
        self.errors: dict[str, str] = dict(errors or {})


class DisclosureRefused(PermissionError):
    """A change the room's current state will not accept.

    Raised when a tier change is asked of a revoked or expired link, and when a
    write does not read back as written. ``reason`` is a stable machine token, so
    the page can branch on it instead of parsing an English sentence.
    """

    #: The link was revoked, so it sees nothing.
    REASON_REVOKED = "link_revoked"

    #: The link passed its expiry, so it sees nothing.
    REASON_EXPIRED = "link_expired"

    #: A write was made but did not read back. The room must not report success.
    REASON_WRITE_NOT_VISIBLE = "write_not_visible"

    _MESSAGES = {
        REASON_REVOKED: "This link was revoked. Nothing is disclosed to it.",
        REASON_EXPIRED: "This link expired. Nothing is disclosed to it.",
        REASON_WRITE_NOT_VISIBLE: "The change was written but did not read back.",
    }

    def __init__(self, reason: str, detail: str | None = None) -> None:
        super().__init__(detail or self._MESSAGES.get(reason, "That change was refused."))
        self.reason = reason


class DisclosureNotFound(LookupError):
    """No such section, recipient, policy or access link.

    Its own type rather than the store's ``RecordNotFound``, because a feature
    may map only the error types it raises itself. Registering a handler for the
    shared type would intercept that exception across the whole product.
    """
