"""WF-061: send conditional pre- and post-meeting reminders and SMS nudges.

The researched workflow, in the order the research states it: an administrator
declares a reusable **reminder asset** (a channel, a firing condition with an
offset, a delivery configuration, a composed message and the two advanced
gates); attaches it to one or more **Meeting Types**; a **booking** is recorded
against a meeting type; the reminder is **planned** onto that booking; and at its
fire time it is either **sent** or **skipped** with one of the five documented
reasons - which an administrator later reads in *Meetings Activity*, per-reminder
status, exactly as step 7 describes.

Module map, in dependency order:

``errors``
    The one domain error type, and the subclass that marks a refusal which is
    about organisational setup rather than a bad request.
``vocabulary``
    The researched terms: channels, conditions, offset units, the two advanced
    gates, the three statuses, the five skip reasons, and Cal's four enums.
``tags``
    Dynamic tags in both of the research's spellings - Chili Piper's dotted
    ``CP.Guest.FirstName`` and Cal's brace-delimited ``{EVENT_NAME}``, two of
    which are not identifiers.
``conditions``
    When a reminder fires and whether it may: the firing condition, the response
    gate, the advanced gates, recipient resolution, and the decision.
``cal``
    The same reminder as a Cal.com Workflow, and a validator for one arriving
    from Cal.
``engine``
    The flow over the audited store, with no outbound socket.
``inferences``
    Every judgement call, named and served over HTTP.

The split is deliberate and follows the port brief's rule: the researched
decisions are the product, so they live in the pure half where they can be
tested without a request, and this package's top level holds only what needs a
database.
"""

from __future__ import annotations

from dsr.meeting_reminders import cal, conditions, tags, vocabulary
from dsr.meeting_reminders.engine import (
    ATTACHMENT_COLLECTION,
    BOOKING_COLLECTION,
    DELIVERY_COLLECTION,
    MEETING_TYPE_COLLECTION,
    ORG_COLLECTION,
    ORG_KEY,
    REMINDER_COLLECTION,
    REPLY_COLLECTION,
    REPLY_HISTORY_LIMIT,
    ReminderEngine,
)
from dsr.meeting_reminders.errors import ConfigurationRefused, ReminderError
from dsr.meeting_reminders.inferences import INFERENCES, describe, by_id

__all__ = [
    "ATTACHMENT_COLLECTION",
    "BOOKING_COLLECTION",
    "ConfigurationRefused",
    "DELIVERY_COLLECTION",
    "INFERENCES",
    "MEETING_TYPE_COLLECTION",
    "ORG_COLLECTION",
    "ORG_KEY",
    "REMINDER_COLLECTION",
    "REPLY_COLLECTION",
    "REPLY_HISTORY_LIMIT",
    "ReminderEngine",
    "ReminderError",
    "by_id",
    "cal",
    "conditions",
    "describe",
    "tags",
    "vocabulary",
]
