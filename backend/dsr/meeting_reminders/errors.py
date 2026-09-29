"""Domain errors for WF-061: conditional meeting reminders and SMS nudges.

One base type for the package, mapped once by the feature module. Every refusal
here is the caller's to fix, so they all answer 400 - a reminder that cannot be
configured or a delivery that cannot be attempted is a bad request, not a server
fault, and not a 404 either.

The hierarchy is deliberately shallow. The host registers one handler for
:class:`ReminderError`, and Starlette resolves a handler by walking the raised
exception's MRO, so a subclass is answered by the same handler with the same
status. A subclass earns its keep when a *test* or a *caller* wants to catch one
narrowly:

* :class:`ConfigurationRefused` - the organisation has not finished the setup
  the researched flow makes a precondition, so the write is refused at the door
  rather than recorded as a skip. The researched preconditions are the Twilio
  connection ("A Chili Piper Admin must connect Twilio to your company's Command
  Center Integrations page"), the custom no-reply domain, and the phone number
  the guest form must collect before an SMS reminder can be enabled.

:class:`DeliveryError` is *not* defined here. A reminder that cannot be sent is
never an exception: the research says it is a **status**, with a documented skip
reason, and an exception would throw away the audit trail this product is built
on. That is the whole point of the five skip reasons.
"""

from __future__ import annotations


class ReminderError(ValueError):
    """A reminder configuration or delivery request cannot be honoured as written."""


class ConfigurationRefused(ReminderError):
    """A researched organisational precondition is not met.

    Raised at configuration time rather than at delivery time. "A Chili Piper
    Admin must connect Twilio to your company's Command Center Integrations
    page" is a statement about whether the feature can be turned on at all, so
    turning it on without the connection is refused rather than accepted and
    silently failing at the send.
    """
