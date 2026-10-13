"""WF-116: the researched vocabulary for a seller follow-up task.

The research fixes four closed vocabularies and this module is the one place
they are written down. Do not extend a closed set here without a source.

    status    NOT_STARTED / COMPLETED          (HubSpot ``hs_task_status``)
    priority  LOW / MEDIUM / HIGH              (HubSpot ``hs_task_priority``)
    type      EMAIL / CALL / TODO              (HubSpot ``hs_task_type``)
    trigger   Task Added / Task Reminder / Task Completed

The reminder is a second timestamp and not a flag. Its unit is fixed: HubSpot
requires "Unix timestamp in milliseconds". This build stores both the due time
and the reminder time as Unix epoch milliseconds, and also writes an ISO 8601
copy of each. The milliseconds are the wire contract. The ISO copies are for a
reader, so a page does not have to convert a number to render a date.

Two upstream systems are described and they disagree. HighLevel has a fixed
``Add Task`` step. HubSpot's ``Create task`` action can be "fed variables from
different object sources". This build follows the HighLevel model: one signal
in, one deterministic task rule out, recorded in
:data:`~dsr.seller_tasks.rules.SIGNAL_TASK_RULES`.

Nothing here imports the framework. See :mod:`dsr.seller_tasks.engine` for the
only module that reads or writes.
"""

from __future__ import annotations

from typing import Any

# --------------------------------------------------------------------------- #
# Identity
# --------------------------------------------------------------------------- #

TICKET = "WF-116"
FEATURE_ID = "wf-116-seller-followup-task"
NAME = "Create an automated seller follow-up task with a reminder"

# --------------------------------------------------------------------------- #
# The four closed vocabularies
# --------------------------------------------------------------------------- #

STATUS_NOT_STARTED = "NOT_STARTED"
STATUS_COMPLETED = "COMPLETED"

#: ``hs_task_status``. The research names exactly two values.
STATUSES: tuple[str, ...] = (STATUS_NOT_STARTED, STATUS_COMPLETED)

PRIORITY_LOW = "LOW"
PRIORITY_MEDIUM = "MEDIUM"
PRIORITY_HIGH = "HIGH"

#: ``hs_task_priority``. The research names exactly three values.
PRIORITIES: tuple[str, ...] = (PRIORITY_LOW, PRIORITY_MEDIUM, PRIORITY_HIGH)

TYPE_EMAIL = "EMAIL"
TYPE_CALL = "CALL"
TYPE_TODO = "TODO"

#: ``hs_task_type``. The research names exactly three values.
TASK_TYPES: tuple[str, ...] = (TYPE_EMAIL, TYPE_CALL, TYPE_TODO)

TRIGGER_TASK_ADDED = "Task Added"
TRIGGER_TASK_REMINDER = "Task Reminder"
TRIGGER_TASK_COMPLETED = "Task Completed"

#: The three documented lifecycle triggers. Task lifecycle drives the next
#: automation, so a trigger name is an event a consumer reads.
TRIGGERS: tuple[str, ...] = (
    TRIGGER_TASK_ADDED,
    TRIGGER_TASK_REMINDER,
    TRIGGER_TASK_COMPLETED,
)

# --------------------------------------------------------------------------- #
# The reminder contract
# --------------------------------------------------------------------------- #

#: ``hs_task_reminders`` must be a "Unix timestamp in milliseconds".
REMINDER_UNIT = "unix_ms"

REMINDER_UNIT_NOTE = (
    "Both timestamps are Unix epoch milliseconds. The due time is required. "
    "The reminder time is optional. ISO 8601 copies are stored for reading."
)

#: The due time is the only required field. ``hs_timestamp`` "marks the task's
#: due date" and the research makes every other property optional.
REQUIRED_FIELDS: tuple[str, ...] = ("due_at",)


# --------------------------------------------------------------------------- #
# Engagement signals (the workflow's inputs)
# --------------------------------------------------------------------------- #

SIGNAL_TASK_ADDED = "task_added"
SIGNAL_TASK_REMINDER_DUE = "task_reminder_due"
SIGNAL_CONTACT_TAG_ADDED = "contact_tag_added"
SIGNAL_ENGAGEMENT_SCORE_RULE_MET = "engagement_score_rule_met"
SIGNAL_OPPORTUNITY_STAGE_CHANGED = "opportunity_stage_changed"
SIGNAL_INBOUND_WEBHOOK = "inbound_webhook"

#: The signals the data flow names. This build accepts all six.
SIGNALS: tuple[str, ...] = (
    SIGNAL_TASK_ADDED,
    SIGNAL_TASK_REMINDER_DUE,
    SIGNAL_CONTACT_TAG_ADDED,
    SIGNAL_ENGAGEMENT_SCORE_RULE_MET,
    SIGNAL_OPPORTUNITY_STAGE_CHANGED,
    SIGNAL_INBOUND_WEBHOOK,
)

# --------------------------------------------------------------------------- #
# Scopes
# --------------------------------------------------------------------------- #

#: The research names contact scopes and no task scope. This build requires
#: both, because a contact-attached task reads and writes a contact. The
#: contact-less path reads only the task store and needs no contact scope.
REQUIRED_SCOPES: tuple[str, ...] = (
    "crm.objects.contacts.read",
    "crm.objects.contacts.write",
)

SCOPES_NOTE = (
    "The research names contact scopes, not task scopes. A contact-attached "
    "task needs both. A contact-less internal task needs neither."
)

# --------------------------------------------------------------------------- #
# Collections and attachment
# --------------------------------------------------------------------------- #

#: Collections this feature owns. The contact collection belongs to WF-039 and
#: is imported from :mod:`dsr.atomic_bundle` rather than re-declared.
TASK_COLLECTION = "wf116_task"
EVENT_COLLECTION = "wf116_task_event"
NOTIFICATION_COLLECTION = "wf116_task_notification"

COLLECTIONS: dict[str, str] = {
    "task": TASK_COLLECTION,
    "event": EVENT_COLLECTION,
    "notification": NOTIFICATION_COLLECTION,
}

#: A task attaches to a contact or stands alone. The research makes the
#: contact-less path a required path, not a fallback.
ATTACHMENT_CONTACT = "contact"
ATTACHMENT_STANDALONE = "standalone"

ATTACHMENTS: tuple[str, ...] = (ATTACHMENT_CONTACT, ATTACHMENT_STANDALONE)

#: The soft-deleted row set is the recycling bin. ``DELETE`` on the vendor
#: "will add the task to the recycling bin", so this build never hard-deletes on
#: a delete request. It purges a binned row only after this many days.
RECYCLING_BIN_RETENTION_DAYS = 30

BIN_NOTE = (
    "A delete is soft. The row stays in the recycling bin. A purge removes a "
    "row only after the retention window ends."
)

# --------------------------------------------------------------------------- #
# The researched user flow and the quotes behind it
# --------------------------------------------------------------------------- #

USER_FLOW: tuple[str, ...] = (
    "Add the Add Task action to a workflow.",
    "Attach the task to a contact, or create a contact-less task.",
    "Set the task title, the body, the due date and the reminder time.",
    "Optionally send an internal notification to the assigned user.",
    "Publish the workflow. The task appears in the assignee's task list.",
    "A Task Reminder trigger fires at the reminder time.",
    "A Task Completed trigger can fire the next automation.",
)

SOURCED_QUOTES: dict[str, str] = {
    "add_task": (
        "[sourced] Add Task: Creates a task related to a contact. Attaches to the "
        "contact if one exists; creates a contact-less task if the workflow is "
        "running without a contact."
    ),
    "reminder_trigger": (
        "[sourced] Task Reminder: Fires when the task's reminder time is reached."
    ),
    "completed_trigger": (
        "[sourced] Task Completed: Fires when a task for the contact is marked completed."
    ),
    "reminder_unit": (
        "[sourced] hs_task_reminders: The timestamp for when to send a reminder "
        "for the due date of the task. You must use Unix timestamp in milliseconds."
    ),
    "due_is_required": ("[sourced] hs_timestamp: This field marks the task's due date."),
    "recycling_bin": (
        "[sourced] DELETE /crm/v3/objects/tasks/{taskId} will add the task to the recycling bin."
    ),
    "task_id_join_key": (
        "[sourced] Task ID appears in task-related workflows, such as task triggers "
        "or after an Add Task step."
    ),
    "contact_scopes": ("[sourced] Scopes: crm.objects.contacts.read, crm.objects.contacts.write."),
    "pinning": (
        "[sourced] Tasks can be pinned to the record timeline by writing the task "
        "id into hs_pinned_engagement_id on the object APIs."
    ),
}

#: What this build could not source. A reader must treat each line as a
#: hypothesis and not as a specification.
SOURCED_GAPS: tuple[str, ...] = (
    "The research names an inbound webhook signal and no endpoint for it. This "
    "build accepts the signal by name and adds no webhook route.",
    "The research names no unit for hs_timestamp. This build stores it as Unix "
    "epoch milliseconds, the unit the reminder property fixes.",
    "The research names no recycling-bin retention rule. This build uses a "
    "30-day window and records the choice as an inference.",
    "The research names no ordering rule for a reminder against the due time. "
    "This build refuses a reminder that falls after the due time and records the "
    "choice as an inference.",
    "The research names hs_pinned_engagement_id on a different object. This build "
    "records the pin intent and writes it to the task row; it does not write to a "
    "foreign object.",
)

SOURCES: tuple[str, ...] = (
    "https://developers.hubspot.com/docs/api-reference/crm-tasks-v3/basic/post-crm-v3-objects-tasks",
    "https://help.gohighlevel.com/support/solutions/articles/155000002292-a-list-of-workflow-triggers",
    "https://help.gohighlevel.com/support/solutions/articles/155000002294-what-are-workflow-actions-complete-list-",
    "https://knowledge.hubspot.com/workflows/create-workflows",
)


def published_vocabulary() -> dict[str, Any]:
    """Every closed word and every sourced rule, in one payload.

    The page reads this instead of repeating the words, so a panel cannot drift
    from the rules the engine enforces.
    """

    return {
        "ticket": TICKET,
        "feature_id": FEATURE_ID,
        "name": NAME,
        "statuses": list(STATUSES),
        "priorities": list(PRIORITIES),
        "task_types": list(TASK_TYPES),
        "triggers": list(TRIGGERS),
        "signals": list(SIGNALS),
        "attachments": list(ATTACHMENTS),
        "required_fields": list(REQUIRED_FIELDS),
        "reminder_unit": REMINDER_UNIT,
        "reminder_unit_note": REMINDER_UNIT_NOTE,
        "required_scopes": list(REQUIRED_SCOPES),
        "scopes_note": SCOPES_NOTE,
        "recycling_bin_retention_days": RECYCLING_BIN_RETENTION_DAYS,
        "bin_note": BIN_NOTE,
        "collections": dict(COLLECTIONS),
        "user_flow": list(USER_FLOW),
        "sourced_quotes": dict(SOURCED_QUOTES),
        "sourced_gaps": list(SOURCED_GAPS),
        "sources": list(SOURCES),
    }


__all__ = [
    "ATTACHMENTS",
    "ATTACHMENT_CONTACT",
    "ATTACHMENT_STANDALONE",
    "BIN_NOTE",
    "COLLECTIONS",
    "EVENT_COLLECTION",
    "FEATURE_ID",
    "NAME",
    "NOTIFICATION_COLLECTION",
    "PRIORITIES",
    "PRIORITY_HIGH",
    "PRIORITY_LOW",
    "PRIORITY_MEDIUM",
    "RECYCLING_BIN_RETENTION_DAYS",
    "REMINDER_UNIT",
    "REMINDER_UNIT_NOTE",
    "REQUIRED_FIELDS",
    "REQUIRED_SCOPES",
    "SCOPES_NOTE",
    "SIGNALS",
    "SIGNAL_CONTACT_TAG_ADDED",
    "SIGNAL_ENGAGEMENT_SCORE_RULE_MET",
    "SIGNAL_INBOUND_WEBHOOK",
    "SIGNAL_OPPORTUNITY_STAGE_CHANGED",
    "SIGNAL_TASK_ADDED",
    "SIGNAL_TASK_REMINDER_DUE",
    "SOURCED_GAPS",
    "SOURCED_QUOTES",
    "SOURCES",
    "STATUSES",
    "STATUS_COMPLETED",
    "STATUS_NOT_STARTED",
    "TASK_COLLECTION",
    "TASK_TYPES",
    "TICKET",
    "TRIGGERS",
    "TRIGGER_TASK_ADDED",
    "TRIGGER_TASK_COMPLETED",
    "TRIGGER_TASK_REMINDER",
    "TYPE_CALL",
    "TYPE_EMAIL",
    "TYPE_TODO",
    "USER_FLOW",
    "published_vocabulary",
]
