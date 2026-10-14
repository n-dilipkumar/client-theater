"""WF-127 domain errors.

Every refusal this workflow can make is a subclass of :class:`DemoAnalyticsError`
and carries the HTTP status it should become, so the router stays a list of
routes rather than a translation table and the whole domain is testable without
an HTTP client.

:mod:`dsr.features` refuses two features that map the same error type, because
load order would otherwise decide the winner. ``DemoAnalyticsError`` is defined
in this workflow's own package, so no other feature can claim it and no
registration can collide.

The shape matches :mod:`dsr.tracked_deal_link.errors`: ``status_code`` and
``code`` on the class, a ``payload()`` for the response, and machine-readable
context beside the human message. A seller reads the message; a client branches
on the code.
"""

from __future__ import annotations


class DemoAnalyticsError(RuntimeError):
    """Base of every refusal WF-127 makes.

    One registration in ``EXCEPTION_HANDLERS`` covers the whole hierarchy. That
    is safe for the same reason it is safe in the sibling workflows: the type
    belongs to this workflow, so a handler for it can never intercept an
    unrelated error raised elsewhere in the product. The builtins
    (``ValueError``, ``PermissionError``) are deliberately not claimed.
    """

    status_code = 422
    code = "demo_analytics_refused"

    def __init__(self, message: str, *, code: str | None = None, **context: object) -> None:
        super().__init__(message)
        if code:
            self.code = code
        #: Machine-readable context returned beside the message, never instead
        #: of it.
        self.context = dict(context)

    def payload(self) -> dict[str, object]:
        return {"error": self.code, "detail": str(self), "status": self.status_code, **self.context}


class DemoValidationError(DemoAnalyticsError):
    """The demo, its click path, or a viewer event is not usable. 422."""

    status_code = 422
    code = "invalid_demo"


class DemoNotFound(DemoAnalyticsError):
    """No such demo, room, or stakeholder reference. 404."""

    status_code = 404
    code = "demo_not_found"


class DemoArchived(DemoAnalyticsError):
    """The demo is archived, so it accepts no new share or event. 410.

    The researched automation "auto-route by demo type" means a demo has a
    lifecycle. An archived demo is withdrawn from distribution but keeps all of
    its recorded activity, so the refusal is a 410 rather than a 404: the demo
    exists, it is simply closed to new input.
    """

    status_code = 410
    code = "demo_archived"


class EventRefused(DemoAnalyticsError):
    """A viewer event is malformed or names a step the click path lacks. 422."""

    status_code = 422
    code = "event_refused"


class ShareRefused(DemoAnalyticsError):
    """A distribution cannot be recorded as asked. 422."""

    status_code = 422
    code = "share_refused"


__all__ = [
    "DemoAnalyticsError",
    "DemoArchived",
    "DemoNotFound",
    "DemoValidationError",
    "EventRefused",
    "ShareRefused",
]
