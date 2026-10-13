"""WF-113: the domain error hierarchy, and nothing else.

One base class carries the HTTP status and the machine-readable code, so the
feature module needs one exception handler rather than one per refusal. That is
the same shape ``dsr.meeting_webhook_fanout`` and ``dsr.security_governance``
use, and it is deliberate: an inbound webhook is answered by a caller that reads
a status and a code, not a stack trace.

Every refusal here is a *client* mistake the external service can act on, except
the two ``5xx`` configuration faults this side owns. A duplicate is deliberately
**not** in this hierarchy: the research says a duplicate is recorded as a
duplicate rather than dropped, so it is an outcome of a successful call, not an
error.
"""

from __future__ import annotations

from typing import Any


class WebhookIngestError(Exception):
    """Base of every refusal this workflow makes."""

    status = 400
    code = "webhook_ingest_error"

    def __init__(self, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.extra = dict(extra)

    def details(self) -> dict[str, Any]:
        """The body shape the feature's one exception handler serves."""
        return {"error": self.code, "detail": str(self), "status": self.status, **self.extra}


class UnknownEndpoint(WebhookIngestError):
    """The token in the URL names no generated webhook URL. Maps to 404."""

    status = 404
    code = "unknown_endpoint"


class UnknownAutomation(WebhookIngestError):
    """The automation behind an endpoint does not exist. Maps to 404."""

    status = 404
    code = "unknown_automation"


class AutomationNotStarted(WebhookIngestError):
    """The automation exists but is still a draft.

    The researched flow is *Review items -> Start Automation*, and the data flow
    says the whole thing runs with "no operator action" only *once started*. An
    ingest into a draft is refused rather than silently buffered, because a
    buffered event with no automation to run it is indistinguishable to the
    sender from a lost one. Maps to 409.
    """

    status = 409
    code = "automation_not_started"


class MalformedPayload(WebhookIngestError):
    """The body is not JSON. Maps to 400."""

    status = 400
    code = "malformed_payload"


class SignatureMissing(WebhookIngestError):
    """A secret is configured and the request carried no signature. Maps to 401."""

    status = 401
    code = "signature_missing"


class SignatureMismatch(WebhookIngestError):
    """The signature does not verify, or is outside the freshness window. Maps to 401."""

    status = 401
    code = "signature_mismatch"


class BatchLimitExceeded(WebhookIngestError):
    """A Batch Update named more than the stated 1,000 people. Maps to 422.

    The research states the ceiling as a fact - *"A batch update lets you
    associate your incoming data with a group of up to 1000 people"* - so this
    build refuses an over-limit batch rather than truncating it silently.
    """

    status = 422
    code = "batch_limit_exceeded"


class UnknownAction(WebhookIngestError):
    """An automation carries an action this build does not serve. Maps to 422."""

    status = 422
    code = "unknown_action"


class InvalidValueType(WebhookIngestError):
    """A mapping names a value type outside Liquid and JavaScript. Maps to 422."""

    status = 422
    code = "invalid_value_type"


class InvalidAutomation(WebhookIngestError):
    """An automation registration this build will not accept. Maps to 422."""

    status = 422
    code = "invalid_automation"


class UnknownTrigger(WebhookIngestError):
    """No trigger row with that id. Maps to 404."""

    status = 404
    code = "unknown_trigger"


class UnknownEvent(WebhookIngestError):
    """No event row with that id. Maps to 404."""

    status = 404
    code = "unknown_event"


class UnknownDelivery(WebhookIngestError):
    """No delivery row with that id. Maps to 404."""

    status = 404
    code = "unknown_delivery"


__all__ = [
    "AutomationNotStarted",
    "BatchLimitExceeded",
    "InvalidAutomation",
    "InvalidValueType",
    "MalformedPayload",
    "SignatureMismatch",
    "SignatureMissing",
    "UnknownAction",
    "UnknownAutomation",
    "UnknownDelivery",
    "UnknownEndpoint",
    "UnknownEvent",
    "UnknownTrigger",
    "WebhookIngestError",
]
