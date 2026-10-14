"""WF-071 domain errors.

Every refusal this workflow can make is a subclass of
:class:`ViewerWatermarkError` and carries the HTTP status it should become, so
the router stays a list of routes rather than a translation table and the whole
domain is testable without an HTTP client.

:mod:`dsr.features` refuses two features mapping the same error type, because
load order would otherwise decide the winner. ``ViewerWatermarkError`` is defined
here, in this workflow's own package, so no other feature can claim it and no
registration can collide. That is also why the plan refusal is a *local* type
with the researched code ``forbidden_plan_feature`` rather than
:class:`dsr.workspace_roles.errors.PlanFeatureError`: WF-077 already maps that
type, and mapping it here would fail this feature's registration outright.

The shape is the same one :mod:`dsr.pdf_analytics.errors` and
:mod:`dsr.tracked_deal_link.errors` use: ``status_code`` and ``code`` on the
class, a ``payload()`` for the response, and machine-readable context beside the
human message. A rep reads the message; a client branches on the code.
"""

from __future__ import annotations


class ViewerWatermarkError(RuntimeError):
    """Base of every refusal WF-071 makes.

    One registration in ``EXCEPTION_HANDLERS`` covers the whole hierarchy, which
    is safe for the same reason it is safe in :mod:`dsr.tracked_deal_link`: the
    type belongs to this workflow, so a handler for it can never intercept an
    unrelated error raised elsewhere in the product. The builtins
    (``ValueError``, ``PermissionError``) are deliberately not claimed.
    """

    status_code = 422
    code = "viewer_watermark_refused"

    def __init__(self, message: str, *, code: str | None = None, **context: object) -> None:
        super().__init__(message)
        if code:
            self.code = code
        #: Machine-readable context returned beside the message, never instead of
        #: it.
        self.context = dict(context)

    def payload(self) -> dict[str, object]:
        return {"error": self.code, "detail": str(self), "status": self.status_code, **self.context}


class WatermarkValidationError(ViewerWatermarkError):
    """The watermark config is not something this workflow will store. 422."""

    status_code = 422
    code = "invalid_watermark_config"


class WatermarkLinkNotFound(ViewerWatermarkError):
    """No WF-121 tracked link behind that reference. 404."""

    status_code = 404
    code = "watermark_link_not_found"


class WatermarkPlanError(ViewerWatermarkError):
    """The team's plan does not cover a watermark, on create or update. 403.

    The researched gate, quoted verbatim: "enabling a gated feature returns
    ``403 forbidden_plan_feature`` on create/update, while existing links keep
    working after a downgrade." So this is raised only on a request that flips the
    toggle from off to on, never when an already-watermarked link is viewed.
    """

    status_code = 403
    code = "forbidden_plan_feature"


__all__ = [
    "ViewerWatermarkError",
    "WatermarkLinkNotFound",
    "WatermarkPlanError",
    "WatermarkValidationError",
]
