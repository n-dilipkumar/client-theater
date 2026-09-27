"""One error type for the whole event-stream package.

Every refusal this package makes is a *client* mistake: an event type that is not
a string, a target URL that is not HTTPS, a subscription with no types, a form
response that does not match its question, a caller who is not an account admin.
Every one of them belongs to the caller, in one of four shapes:

* ``400`` - the request cannot be honoured as written. Fix the request.
* ``403`` - the caller is not an account ``admin``. This one is sourced
  verbatim: "You must be an account ``admin`` to create a webhook." See
  :class:`NotPermitted`.
* ``409`` - the request is well formed but conflicts with the current state
  (rotating a key that is already mid-rotation, retrying a delivery that is not
  retryable).
* ``429`` - the pull-based backfill rate limit, which the research documents as
  a ``429`` on the REST seam.

So one base class carries a status, a machine-readable code, a remediation, and
a correlation id, and the feature module registers exactly one handler for it.

One handler for the whole hierarchy, not one per subclass, is deliberate. It
also means this package never claims a type another feature or the core app
already handles: the host refuses a second handler for the same exception type,
and ``RecordNotFound`` is deliberately *not* claimed here because the core app
already maps it to ``404``.
"""

from __future__ import annotations

from typing import Any

from dsr.db.audited import new_id


class EventStreamError(ValueError):
    """A request the event stream will not honour, with the reason on the wire.

    ``correlation_id`` is generated per error rather than per request because
    the router does not thread a request id through; it is still enough for a
    rep to quote in a bug report and for a log line to be joined against, and
    it is the one handle the research's own tooling would have wanted.
    """

    #: Default machine-readable code. A subclass narrows it.
    code = "event_stream_error"
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


class VocabularyError(EventStreamError):
    """An event type or associated object is not usable as given."""

    code = "unknown_event_type"
    status = 400


class FilterError(EventStreamError):
    """A subscription filter is not an expression this product can evaluate.

    Raised at *compile* time rather than at delivery time, on purpose. A filter
    that silently matched nothing would look exactly like "no activity", and
    the operator would go looking for a bug in their own systems. See
    :mod:`dsr.event_stream.filters`.
    """

    code = "invalid_filter"
    status = 400


class TargetError(EventStreamError):
    """The subscriber URL is unusable, or would not accept a verification POST.

    The research says Dock "verifies the URL with a POST" when the webhook is
    created, so a URL that cannot be verified is a webhook that is not created.
    """

    code = "invalid_target"
    status = 400


class NotPermitted(EventStreamError):
    """The caller is not an account ``admin``.

    Sourced: "You must be an account ``admin`` to create a webhook." Applied to
    webhook *creation* only, because that is the sentence the research quotes;
    see ``inferences.py`` for why the other webhook routes do not require it.
    """

    code = "not_permitted"
    status = 403


class SubscriptionError(EventStreamError):
    """A subscription request cannot be honoured."""

    code = "invalid_subscription"
    status = 400


class EventPayloadError(EventStreamError):
    """A ``webhook-event`` payload is missing something the research requires.

    Every code here corresponds to a sentence in the research, not to a taste
    judgement. The class is the base for the individual rules so the handler
    stays one, and each rule narrows ``code`` so a client can branch on it.
    """

    code = "invalid_event"
    status = 400


class DeliveryError(EventStreamError):
    """A delivery cannot be attempted, retried, or read as asked."""

    code = "delivery_conflict"
    status = 409


class RateLimited(EventStreamError):
    """The pull-based backfill rate limit was hit.

    Sourced only as "``429`` on rate limit" on the REST seam. The specific
    allowance is an inference; see ``inferences.py`` entry
    ``backfill-rate-limit``.
    """

    code = "rate_limited"
    status = 429
