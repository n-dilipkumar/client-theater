"""WF-114: the domain error hierarchy, and nothing else.

One base class carries the HTTP status and the machine-readable code, so the
feature module needs one exception handler rather than one per refusal. That is
the shape ``dsr.webhook_ingest`` uses, and it is deliberate: an inbound webhook
is answered by an external service that reads a status and a code, not a stack
trace.

Every refusal here is something the sender can act on, except the two ``5xx``
configuration faults this side owns. A delivery whose mappings all declined is
deliberately **not** in this hierarchy: it is recorded as ``unmapped`` and
answered with a success, because the sender did nothing wrong.
"""

from __future__ import annotations

from typing import Any


class WebhookWorkflowError(Exception):
    """Base of every refusal this workflow makes."""

    status = 400
    code = "webhook_workflow_error"

    def __init__(self, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.extra = dict(extra)

    def details(self) -> dict[str, Any]:
        """The body shape the feature's one exception handler serves."""
        return {"error": self.code, "detail": str(self), "status": self.status, **self.extra}


class UnknownEndpoint(WebhookWorkflowError):
    """The token in the URL names no generated webhook URL. Maps to 404."""

    status = 404
    code = "unknown_endpoint"


class UnknownWorkflow(WebhookWorkflowError):
    """No workflow row with that id. Maps to 404."""

    status = 404
    code = "unknown_workflow"


class UnknownTrigger(WebhookWorkflowError):
    """No trigger row with that id. Maps to 404."""

    status = 404
    code = "unknown_trigger"


class UnknownInvocation(WebhookWorkflowError):
    """No invocation row with that id. Maps to 404."""

    status = 404
    code = "unknown_invocation"


class UnknownRun(WebhookWorkflowError):
    """No run row with that id. Maps to 404."""

    status = 404
    code = "unknown_run"


class UnknownVariable(WebhookWorkflowError):
    """No variable row with that id. Maps to 404."""

    status = 404
    code = "unknown_variable"


class InvalidWorkflow(WebhookWorkflowError):
    """A workflow registration this build will not accept. Maps to 422."""

    status = 422
    code = "invalid_workflow"


class InvalidTrigger(WebhookWorkflowError):
    """A trigger or mapping registration this build will not accept. Maps to 422."""

    status = 422
    code = "invalid_trigger"


class InvalidVariable(WebhookWorkflowError):
    """A variable registration this build will not accept. Maps to 422."""

    status = 422
    code = "invalid_variable"


class TriggerNotSaved(WebhookWorkflowError):
    """The trigger exists but Save Trigger has not run. Maps to 409.

    Step 6 of the researched flow is *"Click Save Trigger, then add downstream
    actions and publish."* The URL is generated at step 2 and copied before that,
    so the URL exists while the trigger is unsaved. A live delivery into an
    unsaved trigger is refused rather than silently dropped, because a dropped
    delivery is indistinguishable to the sender from a successful one.
    """

    status = 409
    code = "trigger_not_saved"


class WorkflowNotPublished(WebhookWorkflowError):
    """The workflow is still a draft. Maps to 409.

    The data flow runs with no operator action only once the workflow is
    published. A live delivery into a draft is refused for the same reason a
    delivery into an unsaved trigger is.
    """

    status = 409
    code = "workflow_not_published"


class SignatureMissing(WebhookWorkflowError):
    """The delivery carried no signature. Maps to 401.

    The research names no authentication rule for the generated URL, and this
    build fails closed: a URL anyone can call is a published write endpoint.
    """

    status = 401
    code = "signature_missing"


class SignatureMismatch(WebhookWorkflowError):
    """The signature does not verify, or is outside the window. Maps to 401."""

    status = 401
    code = "signature_mismatch"


class MalformedPayload(WebhookWorkflowError):
    """The body is not JSON. Maps to 400."""

    status = 400
    code = "malformed_payload"


class BodyNotAllowedOnGet(WebhookWorkflowError):
    """A GET delivery carried a body. Maps to 400.

    HTTP defines no request body for GET, so its payload is the query string. A
    GET that carries a body is refused rather than one source silently winning,
    because a sender that put its payload in the body would otherwise watch a
    delivery that read nothing succeed.
    """

    status = 400
    code = "body_not_allowed_on_get"


class MethodNotAllowed(WebhookWorkflowError):
    """A method outside the researched POST/GET/PUT. Maps to 405."""

    status = 405
    code = "method_not_allowed"


class RequiredMappingUnresolved(WebhookWorkflowError):
    """A mapping step required a key the payload did not carry. Maps to 422.

    The payload shape is unvalidated, so nothing is refused for a key this build
    was not told to require. A mapping row that names a variable does require its
    path, and a payload missing it would start a workflow with an unbound
    variable, which is worse than an error the sender can act on.
    """

    status = 422
    code = "required_mapping_unresolved"


__all__ = [
    "BodyNotAllowedOnGet",
    "InvalidTrigger",
    "InvalidVariable",
    "InvalidWorkflow",
    "MalformedPayload",
    "MethodNotAllowed",
    "RequiredMappingUnresolved",
    "SignatureMismatch",
    "SignatureMissing",
    "TriggerNotSaved",
    "UnknownEndpoint",
    "UnknownInvocation",
    "UnknownRun",
    "UnknownTrigger",
    "UnknownVariable",
    "UnknownWorkflow",
    "WebhookWorkflowError",
    "WorkflowNotPublished",
]
