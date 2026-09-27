"""One error type for the whole intent-stream package.

Every refusal this package makes is a *caller* mistake, in one of four shapes:

* ``400`` - the request cannot be honoured as written. Fix the request.
* ``404`` - the thing named does not exist. Handled by the core app's
  ``RecordNotFound``, which this package deliberately does **not** claim: two
  handlers for one type is a collision the host refuses.
* ``409`` - the request is well formed but conflicts with current state
  (deleting a Segment that a Webhook workflow still uses, resending a delivery
  that never reached the destination).
* ``422`` - the request parses, but a rule in the research says it is not a
  thing this product will do (an unknown payload mode, a Segment rule that
  cannot be evaluated).

Why one base class rather than several handlers
----------------------------------------------
It also means this package never claims a type another feature or the core app
already handles. ``ValueError`` in particular is *not* claimed, because a
handler for it would let this feature intercept exceptions raised anywhere in
the app. One handler for the whole hierarchy also means the family keeps one
shape on the wire, with the status carried per error rather than per class.
"""

from __future__ import annotations

from typing import Any

from dsr.db.audited import new_id


class IntentStreamError(ValueError):
    """A request the intent stream will not honour, with the reason on the wire.

    ``correlation_id`` is generated per error rather than per request because the
    router does not thread a request id through; it is still enough for a rep to
    quote in a bug report and for a log line to be joined against.
    """

    #: Default machine-readable code. A subclass narrows it.
    code = "intent_stream_error"
    #: Default HTTP status for the family.
    status = 400

    def __init__(
        self,
        detail: str,
        *,
        code: str | None = None,
        status: int | None = None,
        remediation: str | None = None,
        correlation_id: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(detail)
        self.detail = detail
        if code is not None:
            self.code = code
        if status is not None:
            self.status = status
        self.remediation = remediation or ""
        self.correlation_id = correlation_id or new_id("corr")
        self.headers = dict(headers or {})

    def to_payload(self) -> dict[str, Any]:
        """The body this error becomes."""
        return {
            "error": self.code,
            "detail": self.detail,
            "remediation": self.remediation,
            "correlation_id": self.correlation_id,
            "status": self.status,
        }

    def __str__(self) -> str:  # pragma: no cover - exercised through to_payload
        return self.detail


class TargetError(IntentStreamError):
    """The destination URL is not something this product can POST to.

    Sourced: "you'll need a destination that can handle receiving the data object
    being sent via the webhooks. This can be a public API for a third party tool
    or a custom solution." What that rules out is a URL with no HTTP scheme or a
    host, not a scheme the vendor did not name - see the ``http-scheme-allowed``
    entry in :mod:`dsr.intent_stream.inferences`.
    """

    code = "invalid_target"
    status = 400


class SegmentError(IntentStreamError):
    """A saved Segment cannot be created, changed, or evaluated.

    Sourced: the conditions a Webhook workflow sorts leads with are "based on
    saved Segments from your account", so a Segment is a first-class object this
    product owns and a rule about what one may say.
    """

    code = "invalid_segment"
    status = 422


class WorkflowError(IntentStreamError):
    """A Webhook workflow cannot be created or changed as asked.

    Sourced: steps 3 to 6 of the researched flow - a name, a destination URL,
    conditions from saved Segments, a once-or-updates choice, a payload choice,
    and a token.
    """

    code = "invalid_workflow"
    status = 422


class LeadError(IntentStreamError):
    """A company lead, its contacts, or a visit is not usable as given.

    Sourced: the data flow starts "A company visit matches a saved Segment", so
    a visit without a lead, or a lead without a company to send, is a request
    the researched flow does not describe.
    """

    code = "invalid_lead"
    status = 422


class DeliveryError(IntentStreamError):
    """A delivery cannot be read or re-attempted as asked.

    ``409`` rather than ``404``/``400``: the row exists and the request is
    well formed, but it conflicts with what already happened.
    """

    code = "delivery_conflict"
    status = 409
