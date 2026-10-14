"""WF-130 domain errors.

Every refusal this workflow can make is a subclass of :class:`CollateralError`
and carries the HTTP status it should become, so the router stays a list of
routes rather than a translation table and the whole domain is testable without
an HTTP client.

:mod:`dsr.features` refuses two features that map the same error type, because
load order would otherwise decide the winner. ``CollateralError`` is defined in
this workflow's own package, so no other feature can claim it and no
registration can collide. The builtins the type subclasses are deliberately not
claimed themselves: a handler registered for ``ValueError`` directly would
intercept that exception across the whole product.
"""

from __future__ import annotations


class CollateralError(RuntimeError):
    """Base of every refusal WF-130 makes.

    One registration in ``EXCEPTION_HANDLERS`` covers the whole hierarchy. The
    shape matches the sibling workflows: ``status_code`` and ``code`` on the
    class, a ``payload()`` for the response, and machine-readable context beside
    the human message. A seller reads the message; a client branches on the
    code.
    """

    status_code = 422
    code = "collateral_refused"

    def __init__(self, message: str, *, code: str | None = None, **context: object) -> None:
        super().__init__(message)
        if code:
            self.code = code
        #: Machine-readable context returned beside the message, never instead
        #: of it.
        self.context = dict(context)

    def payload(self) -> dict[str, object]:
        return {"error": self.code, "detail": str(self), "status": self.status_code, **self.context}


class CollateralValidationError(CollateralError):
    """The deal, template, section or edit is not usable. 422."""

    status_code = 422
    code = "invalid_collateral"


class CollateralNotFound(CollateralError):
    """No such collateral, room, or source asset. 404."""

    status_code = 404
    code = "collateral_not_found"


class CollateralRefused(CollateralError):
    """A change the artefact's current state will not accept. 409.

    Raised when a withdrawn artefact is edited or published, when an
    always-visible section is empty at publish time, and when the room's
    publishing seam refuses the page. The artefact exists; its state is what
    refuses the request, so this is a 409 rather than a 404 or a 422.
    """

    status_code = 409
    code = "collateral_refused"


__all__ = [
    "CollateralError",
    "CollateralNotFound",
    "CollateralRefused",
    "CollateralValidationError",
]
