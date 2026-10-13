"""One error hierarchy, so the feature module registers one handler per class.

Every type here is raised by :mod:`dsr.meeting_followup` and by nothing else in
the product. That is what makes it safe to map them in the feature module: the
feature host refuses a second feature registering a handler for the same type,
and a handler for ``ValueError`` or ``KeyError`` would intercept those exceptions
across the whole application.

Each class carries a ``status``, so the HTTP layer maps a refusal without being
told which one it is. The status codes are this build's choice except where the
research names the vendor's own:

* :class:`AutomationInvalid` is 400. The request is malformed.
* :class:`PersonalFlowRefused` is 409. The flow exists and cannot be used with
  an automation, which is a conflict between two things that are each valid.
* :class:`AssigneeNotLicensed` is 403. The caller did nothing wrong; the
  organisation's licence does not cover the assignee they named.
* :class:`ContactWithoutParentAccount` is 409 for the same reason as the personal
  flow: the contact is valid and the assignment is not.
* :class:`AssignBatchTooLarge` is 400 and carries the researched cap of 200.

``RecordNotFound`` is deliberately not claimed here. The core app already maps it
to 404, and two handlers for one type is a collision the host refuses.
"""

from __future__ import annotations


class FollowupError(Exception):
    """Base for every refusal this workflow raises."""

    #: Stable machine token. A client branches on this, never on ``detail``.
    code = "followup_error"

    #: HTTP status the feature module answers with.
    status = 400

    def __init__(self, detail: str, errors: dict[str, str] | None = None) -> None:
        super().__init__(detail)
        self.detail = detail
        self.errors: dict[str, str] = dict(errors or {})

    def __str__(self) -> str:
        return self.detail


class AutomationInvalid(FollowupError):
    """The automation payload is not one this workflow accepts.

    Carries ``errors`` as a field-keyed map so a form can put each message beside
    the input that caused it.
    """

    code = "automation_invalid"
    status = 400


class FlowInvalid(FollowupError):
    """The flow payload is not one this workflow accepts."""

    code = "flow_invalid"
    status = 400


class PrepPageInvalid(FollowupError):
    """The prep page payload is not one this workflow accepts."""

    code = "prep_page_invalid"
    status = 400


class CrmRecordInvalid(FollowupError):
    """A CRM entity payload is not one this workflow accepts.

    Two researched rules land here rather than in their own classes, because both
    are field errors on the same payload: ``modificationDate`` is mandatory on an
    update, and a sync cadence outside one to five minutes is refused.
    """

    code = "crm_record_invalid"
    status = 400


class AssigneeNotLicensed(FollowupError):
    """The assignee has no Engage licence, so the automation will not run.

    The research states the consequence rather than leaving it to be inferred:
    "The selected user must have an Engage license. If they don't, the automation
    won't run." A rule whose failure mode is silent is not a rule, so this is a
    refusal and the message quotes the vendor's own sentence.
    """

    code = "assignee_not_licensed"
    status = 403


class ContactWithoutParentAccount(FollowupError):
    """The selected contact has no parent account, so the automation will not run.

    The research says exactly this: "If the automation selects a contact without a
    parent account, the automation will fail to run."
    """

    code = "contact_without_parent_account"
    status = 409


class PersonalFlowRefused(FollowupError):
    """A personal flow was offered to an automation.

    Quoted: "Personal flows cannot be used with automations." 409 rather than
    400, because both objects are individually valid and the conflict is between
    them.
    """

    code = "personal_flow_refused"
    status = 409


class DateTriggerObjectRefused(FollowupError):
    """A date-based trigger was offered an object type other than Opportunity.

    The evidence restricts it: "(Opportunity only) current date is".
    """

    code = "date_trigger_object_refused"
    status = 400


class AssignBatchTooLarge(FollowupError):
    """More than 200 prospects in one assignment request.

    Quoted: "You can assign up to 200 prospects to a flow in a single request."
    The cap is the researched one, not a server limit.
    """

    code = "assign_batch_too_large"
    status = 400


class FlowInstanceNotFound(FollowupError):
    """No flow instance by that id."""

    code = "flow_instance_not_found"
    status = 404


class TodoNotFound(FollowupError):
    """No to-do by that id."""

    code = "flow_todo_not_found"
    status = 404


class AutomationNotFound(FollowupError):
    """No automation by that id."""

    code = "automation_not_found"
    status = 404


class FlowNotFound(FollowupError):
    """No flow by that id."""

    code = "flow_not_found"
    status = 404


class PrepPageNotFound(FollowupError):
    """No prep page by that id."""

    code = "prep_page_not_found"
    status = 404


class FlowInstanceTransitionRefused(FollowupError):
    """A status change the three-value enum does not permit.

    The body carries ``status`` and ``allowed`` so a client can tell the reader
    which status is available rather than only that this one is not.
    """

    code = "flow_instance_transition_refused"
    status = 409

    def __init__(
        self,
        detail: str,
        *,
        current: str = "",
        requested: str = "",
        allowed: tuple[str, ...] = (),
    ) -> None:
        super().__init__(detail)
        self.current = current
        self.requested = requested
        self.allowed = tuple(allowed)


class SlackChannelRefused(FollowupError):
    """A Forecast reminder was configured for Slack.

    The product has no confirmed Slack surface. The research gives the timings
    and this build records them, but a reminder that posts nowhere is a rule that
    looks armed and does nothing, so the configuration is refused rather than
    stored.
    """

    code = "slack_channel_refused"
    status = 400


class WebhookInvalid(FollowupError):
    """An inbound Cal webhook is not one this workflow models.

    Raised for an unknown event name as well as for a missing booking id. An
    event this workflow does not model would otherwise be stored as a row that
    drove nothing, which reads on the webhook log like a webhook that arrived and
    was handled.
    """

    code = "webhook_invalid"
    status = 400
