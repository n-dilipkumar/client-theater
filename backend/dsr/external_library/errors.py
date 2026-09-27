"""Error vocabulary for the external content library (WF-008).

Every failure the researched operation can produce carries two things beyond a
message: what the caller should do about it, and an id that ties the failure to
server-side logs. The research states plainly that *every error carries
``Remediation`` and ``CorrelationId``*, so those are part of the type here
rather than something each raise site has to remember.

Codes and their status codes come from the documented responses of the add
external content operation: 400 ``InvalidParameterApiException``, 401/403
``NoPermissionApiException``, 404 ``ExternalConnectionNotFoundApiException`` /
``FolderNotFoundApiException`` / ``ExternalContentNotFoundApiException``, 500
``UnknownError``. Two codes are additions this project needs and the research
does not document, and both are called out where they are raised:

``RoomNotFound``
    Teamsite scoping. The operation places the item "under the specified
    teamsite"; in this project the teamsite is a sales room, which is a concept
    of ours rather than the vendor's.
``RateLimitExceeded``
    The research documents a "1 request per second per token" limit and that
    every error carries a remediation, but not what a breach looks like. 429
    with a retry hint is the standard HTTP shape.
"""

from __future__ import annotations

from dsr.db.audited import new_id

#: code -> (http status, remediation shown to the caller when none is supplied)
ERROR_CATALOGUE: dict[str, tuple[int, str]] = {
    "InvalidParameter": (400, "Correct the request and try again."),
    "NoPermission": (403, "Ask a room administrator for library access."),
    "ExternalConnectionNotFound": (
        404,
        "Connect a cloud account in your profile settings before retrying.",
    ),
    "FolderNotFound": (404, "Use a folder id from this room's library, or the keyword 'root'."),
    "ExternalContentNotFound": (404, "Check the file id and that the connection can see the file."),
    "RoomNotFound": (404, "The room does not exist; create it before adding library content."),
    "RateLimitExceeded": (
        429,
        "This operation allows one request per second. Wait a moment and retry.",
    ),
    "UnknownError": (500, "Retry. Quote the correlation id if the problem persists."),
}


class ExternalSyncError(RuntimeError):
    """A failure of the external content operation, with a remedy attached."""

    def __init__(
        self,
        code: str,
        detail: str,
        *,
        remediation: str | None = None,
        correlation_id: str | None = None,
        status: int | None = None,
        extra: dict | None = None,
    ) -> None:
        if code not in ERROR_CATALOGUE:  # a new code must be declared, not improvised
            raise KeyError(f"undeclared external sync error code {code!r}")
        default_status, default_remediation = ERROR_CATALOGUE[code]
        self.code = code
        self.detail = detail
        self.remediation = remediation or default_remediation
        self.correlation_id = correlation_id or new_id("corr")
        self.status = int(status or default_status)
        self.extra = dict(extra or {})
        super().__init__(f"{code}: {detail}")

    def to_payload(self) -> dict:
        """The wire shape: everything a caller needs to act on the failure."""
        return {
            "error": self.code,
            "detail": self.detail,
            "remediation": self.remediation,
            "correlation_id": self.correlation_id,
            **self.extra,
        }
