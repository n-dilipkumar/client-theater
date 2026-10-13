"""WF-121 domain errors.

Every refusal this workflow can make is a subclass of :class:`TrackedLinkError`
and carries the HTTP status it should become, so the router stays a list of
routes rather than a translation table and the whole domain is testable without
an HTTP client.

:mod:`dsr.features` refuses two features mapping the same error type, because
load order would otherwise decide the winner. ``TrackedLinkError`` is defined
here, in this workflow's own package, so no other feature can claim it and no
registration can collide.

The shape is deliberately the same one :mod:`dsr.pdf_analytics.errors` uses:
``status_code`` and ``code`` on the class, a ``payload()`` for the response, and
machine-readable context beside the human message. A rep reads the message; a
client branches on the code.
"""

from __future__ import annotations


class TrackedLinkError(RuntimeError):
    """Base of every refusal WF-121 makes.

    One registration in ``EXCEPTION_HANDLERS`` covers the whole hierarchy, which
    is safe for the same reason it is safe in :mod:`dsr.pdf_analytics`: the type
    belongs to this workflow, so a handler for it can never intercept an
    unrelated error raised elsewhere in the product. The builtins
    (``ValueError``, ``PermissionError``) are deliberately not claimed.
    """

    status_code = 422
    code = "tracked_link_refused"

    def __init__(self, message: str, *, code: str | None = None, **context: object) -> None:
        super().__init__(message)
        if code:
            self.code = code
        #: Machine-readable context returned beside the message, never instead of
        #: it.
        self.context = dict(context)

    def payload(self) -> dict[str, object]:
        return {"error": self.code, "detail": str(self), "status": self.status_code, **self.context}


class LinkValidationError(TrackedLinkError):
    """The link or the view is not something this workflow will store. 422."""

    status_code = 422
    code = "invalid_tracked_link"


class LinkNotFound(TrackedLinkError):
    """No such link, or the reference never resolved. 404."""

    status_code = 404
    code = "tracked_link_not_found"


class LinkClosed(TrackedLinkError):
    """The link has expired or been revoked, so a view is refused. 410.

    The researched ``automations`` line names link-expiry without a rule. Either
    way a link stops working, the viewer gets the friendly closed page rather
    than a 404, which is the distinction WF-069's gate also keeps.
    """

    status_code = 410
    code = "tracked_link_closed"


class ViewerRequired(TrackedLinkError):
    """The link is attributed, and this view carries no viewer identity. 428.

    428 rather than 422: the request is well-formed, and what is missing is a
    precondition the caller can satisfy by identifying the viewer. The research
    attaches identity to the link - "email gate or CRM-matched user" - and a
    view this workflow cannot attribute is the one thing it must not record
    silently.
    """

    status_code = 428
    code = "viewer_required"


class DwellRefused(TrackedLinkError):
    """The per-page timing is not usable. 422."""

    status_code = 422
    code = "dwell_refused"


class DistributionRefused(TrackedLinkError):
    """The link cannot be distributed as asked. 422."""

    status_code = 422
    code = "distribution_refused"


__all__ = [
    "DistributionRefused",
    "DwellRefused",
    "LinkClosed",
    "LinkNotFound",
    "LinkValidationError",
    "TrackedLinkError",
    "ViewerRequired",
]
