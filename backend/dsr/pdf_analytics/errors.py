"""WF-018 domain errors.

Every refusal this workflow can make is a subclass of :class:`AnalyticsError`
and carries the HTTP status it should become, so the router is a list of routes
rather than a translation table, and the domain can be tested without an HTTP
client at all.

:mod:`dsr.features` refuses two features mapping the same error type, because
load order would otherwise decide the winner. ``AnalyticsError`` is defined
here, in this workflow's own package, so no other feature can claim it and no
registration can collide.
"""

from __future__ import annotations


class AnalyticsError(RuntimeError):
    """Base of every refusal WF-018 makes.

    One registration in ``EXCEPTION_HANDLERS`` covers the whole hierarchy,
    reading ``status_code`` and ``code`` off the exception. That is safe here
    for the same reason it is safe in :mod:`dsr.crm`: the type is this
    workflow's own, so a global handler for it cannot intercept an unrelated
    error anywhere else in the product.
    """

    status_code = 400
    code = "analytics_error"

    def __init__(self, message: str, *, code: str | None = None, **context: object) -> None:
        super().__init__(message)
        if code:
            self.code = code
        #: Machine-readable context, returned to the client alongside the
        #: message. Never the message's only carrier: a rep reads the message, a
        #: client reads the code.
        self.context = dict(context)

    def payload(self) -> dict[str, object]:
        return {"error": self.code, "detail": str(self), **self.context}


class NotFound(AnalyticsError):
    """A room or an asset reference did not resolve. 404."""

    status_code = 404
    code = "not_found"


class ValidationError(AnalyticsError):
    """The request body is not something this workflow will store. 400."""

    status_code = 400
    code = "invalid_request"


class UnknownEventType(ValidationError):
    """The ``event`` is outside the researched webhook vocabulary. 400."""

    code = "unknown_event_type"


class PdfAnalyticsUnavailable(AnalyticsError):
    """PDF Analytics does not exist for this asset. 422.

    The research is explicit that the two PDF metrics exist "for multi-page
    PDFs". A single-page PDF, or a video, has no per-page curve, and answering
    with an empty one would read as "nobody read it" rather than "there is
    nothing to show".
    """

    status_code = 422
    code = "pdf_analytics_unavailable"


class VideoAnalyticsUnavailable(AnalyticsError):
    """Video Analytics does not exist for this asset. 422.

    Sourced the same way: the research scopes average watch time to
    "self-hosted videos", so an externally hosted one has no watch time to
    average.
    """

    status_code = 422
    code = "video_analytics_unavailable"


class TrackingDisabled(AnalyticsError):
    """The asset is not trackable, so its timings are refused. 422.

    Refused rather than stored-and-ignored. A silently dropped timing row
    leaves a gap in the drop-off curve with no way for a rep to tell it from
    "readers skipped this page", which is the one thing this workflow exists to
    let them tell apart.
    """

    status_code = 422
    code = "tracking_disabled"
