"""The errors this workflow raises, declared in one place.

All three are declared here and raised by nothing else in the product. That is what makes it
safe for the HTTP layer to map them: FastAPI accepts an exception handler on the app object
only, the host attaches the ones a feature exports, and the host refuses a second feature
registering a handler for a type this one already claimed.

Three types, because three different things can be wrong and a caller acts differently on each:

``ReminderRefused``
    A value or a step this workflow will not accept: an inbox with no name, a reminder with no
    person, a push with no title, the copy toggle on a reminder with no push to copy, and an
    unknown channel, provider, message kind or engagement event. 400, with a field-keyed map so
    each message lands next to its input.
``InboxStateRefused``
    A well-formed request that conflicts with the state of the inbox: a second inbox in a
    workspace that already has its one, and a send into a draft inbox. 409, because the request
    was fine and the state is what refuses it. The body names the way out, because a bare 409
    is a support ticket.
``RecordNotFound``
    A reminder or an inbox message that does not exist. 404. Never a 500, because a feature
    must never turn a missing row into a server fault.

Why a *failed push* is not one of these
---------------------------------------

It is the point of the workflow. The research says the inbox copy exists because "the push
delivery failed" is one of the three reasons it exists, so a push that cannot be delivered is
an **outcome**, not an error: the reminder happened, the push missed, and the inbox copy is
exactly the thing that saves the message. :func:`dsr.automation_engagement.rules.decide_push`
returns an outcome rather than raising, and :meth:`~dsr.automation_engagement.engine.ReminderEngine.send_reminder`
writes the reminder either way.

Turning it into an HTTP error would invert the workflow: a caller could not tell the difference
between "the push missed, and the fallback did its job" and "this endpoint is broken", and a
retry loop would hammer a route that is behaving correctly.

Why the design studio is not an error either
--------------------------------------------

Step 2 of the research's user flow opens a hosted visual editor. This build does not implement
it and does not fake it, and a caller that sends the named styling fields has them stored rather
than refused. That is recorded as
:data:`~dsr.automation_engagement.inferences.DERIVED_NO_HOSTED_DESIGN_STUDIO` rather than as an
error type, because the research does not require an editor - it describes one.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from dsr.automation_engagement import vocabulary as vocab


class ReminderRefused(Exception):
    """A value or a step this workflow will not accept, with a field-keyed map of reasons."""

    def __init__(self, detail: str, errors: Mapping[str, str] | None = None) -> None:
        super().__init__(detail)
        self.detail = detail
        self.errors: dict[str, str] = dict(errors or {})

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": "invalid_reminder_request",
            "detail": self.detail,
            "errors": self.errors,
            "published_failures": vocab.reminder_failures(),
        }


class InboxStateRefused(Exception):
    """A well-formed request that conflicts with the state of the inbox."""

    def __init__(
        self, detail: str, *, reason: str, remedy: str | None = None, **extra: Any
    ) -> None:
        super().__init__(detail)
        self.detail = detail
        self.reason = reason
        self.remedy = remedy
        self.extra = extra

    def to_dict(self) -> dict[str, Any]:
        """The body names the reason code and the way out, because a bare 409 is a support ticket."""

        published = vocab.reminder_failures().get(self.reason, {})
        body: dict[str, Any] = {
            "error": self.reason,
            "detail": self.detail,
            "reason": self.reason,
            "sourced": published.get("sourced", "n/a"),
        }
        if self.remedy or published.get("remedy"):
            body["remedy"] = self.remedy or published["remedy"]
        body.update(self.extra)
        return body


class RecordNotFound(LookupError):
    """A reminder or an inbox message that does not exist."""

    def __init__(self, detail: str, *, kind: str = "reminder") -> None:
        super().__init__(detail)
        self.detail = detail
        self.kind = kind

    def to_dict(self) -> dict[str, Any]:
        return {"error": f"{self.kind}_not_found", "detail": self.detail}


ERROR_TYPES = (ReminderRefused, InboxStateRefused, RecordNotFound)
