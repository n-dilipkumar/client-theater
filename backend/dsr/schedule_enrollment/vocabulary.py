"""WF-120: the researched vocabulary for scheduled enrollment.

The workflow is "Enroll records on a schedule with re-enrollment control". The
research is ``docs/research/digital-sales-room-workflows/wf/WF-120.md``, itself
built from section 17 of ``docs/research/raw/automation-engagement.md``.

Every term a page renders or a validator enforces is declared here beside the
sentence it came from. Three kinds of term live here:

* a **researched** term carries a quote: the research states it.
* an **inferred** term carries nothing and is recorded in
  :mod:`dsr.schedule_enrollment.inferences` with what would change it.
* a **collection name** is this build's own storage key and carries neither.

Reuse, not re-declaration
-------------------------

The schedule scans CRM records and its first action writes a task, so it depends
on two merged workflows rather than inventing either shape:

* WF-039 (``dsr.atomic_bundle``) owns the CRM records the bundle writes.
  :data:`CRM_RECORD_COLLECTION` is an alias onto its
  :data:`~dsr.atomic_bundle.transport.TARGET_COLLECTION`, so a mismatch cannot
  leave the scan looking at a collection nobody writes.
* WF-116 (``dsr.seller_tasks``) owns the workflow task store. The task the first
  action creates is written through that workflow's engine, so this package does
  not re-declare a task status, priority or type.

Nothing here imports the framework. See
:mod:`dsr.schedule_enrollment.engine` for the only module that reads or writes.
"""

from __future__ import annotations

from typing import Any

from dsr.atomic_bundle.transport import TARGET_COLLECTION

# --------------------------------------------------------------------------- #
# Identity
# --------------------------------------------------------------------------- #

TICKET = "WF-120"
FEATURE_ID = "wf-120-schedule-enrollment"
NAME = "Enroll records on a schedule with re-enrollment control"

#: Where a CRM record row lives. This is WF-039's collection, read by name and
#: not re-declared: the records the schedule scans are the rows the bundle wrote.
CRM_RECORD_COLLECTION = TARGET_COLLECTION

#: The object families the data sources name. A schedule enrolls one of them.
CRM_OBJECTS: tuple[str, ...] = (
    "Contact",
    "Company",
    "Deal",
    "Ticket",
    "Quote",
    "Custom object",
)

DEFAULT_OBJECT = "Contact"

#: Collections this workflow owns. All of them are ordinary JSON in
#: ``records.data``: no migration, no typed column.
SCHEDULE_COLLECTION = "wf120_schedule"
ENROLLMENT_COLLECTION = "wf120_enrollment"
RUN_COLLECTION = "wf120_run"
EVENT_COLLECTION = "wf120_event"
NOTIFICATION_COLLECTION = "wf120_notification"

COLLECTIONS: dict[str, str] = {
    "crm_record_read": CRM_RECORD_COLLECTION,
    "schedule": SCHEDULE_COLLECTION,
    "enrollment": ENROLLMENT_COLLECTION,
    "run": RUN_COLLECTION,
    "event": EVENT_COLLECTION,
    "notification": NOTIFICATION_COLLECTION,
}

# --------------------------------------------------------------------------- #
# The trigger family
# --------------------------------------------------------------------------- #

#: "When setting up enrollment triggers, you can use the following enrollment
#: trigger types: Filter-based, Event-based, Based on a schedule, Based on
#: webhooks." This workflow is the scheduled one. The other three are context for
#: the Settings tab, not separate scope.
TRIGGER_FILTER_BASED = "Filter-based"
TRIGGER_EVENT_BASED = "Event-based"
TRIGGER_SCHEDULE = "Based on a schedule"
TRIGGER_WEBHOOK = "Based on webhooks"

TRIGGER_TYPES: tuple[str, ...] = (
    TRIGGER_FILTER_BASED,
    TRIGGER_EVENT_BASED,
    TRIGGER_SCHEDULE,
    TRIGGER_WEBHOOK,
)

#: The one trigger type this workflow implements. A schedule is refused if it
#: names another, because the other three are different workflows.
DEFAULT_TRIGGER = TRIGGER_SCHEDULE

# --------------------------------------------------------------------------- #
# The enrollment state machine
# --------------------------------------------------------------------------- #

#: An enrolled record is held in the enrollment queue for the configured delay,
#: then its actions fire and it completes. Unenrollment removes it from the
#: workflow. Three states, and no other.
STATUS_QUEUED = "queued"
STATUS_COMPLETED = "completed"
STATUS_UNENROLLED = "unenrolled"

STATUSES: tuple[str, ...] = (STATUS_QUEUED, STATUS_COMPLETED, STATUS_UNENROLLED)

#: A record in one of these states is currently enrolled. The research: "Records
#: that are currently enrolled in a workflow cannot be re-enrolled into that same
#: workflow until they complete the workflow."
ACTIVE_STATUSES: tuple[str, ...] = (STATUS_QUEUED,)

#: A record in one of these states has left the workflow and may re-enter.
TERMINAL_STATUSES: tuple[str, ...] = (STATUS_COMPLETED, STATUS_UNENROLLED)

# --------------------------------------------------------------------------- #
# Re-enrollment control (the Settings tab's Re-enroll toggle)
# --------------------------------------------------------------------------- #

#: Off: a record enrolls once and never again. "By default, records will only
#: enroll in a workflow the first time they meet the enrollment triggers."
REENROLL_SINGLE = "single"

#: On: a record may enroll again only after it has completed the workflow.
#: "A record can only re-enroll if it has completed the workflow, and is enrolled
#: again." A record currently enrolled is refused in both modes.
REENROLL_AFTER_COMPLETION = "after_completion"

REENROLL_MODES: tuple[str, ...] = (REENROLL_SINGLE, REENROLL_AFTER_COMPLETION)

DEFAULT_REENROLL_MODE = REENROLL_SINGLE

# --------------------------------------------------------------------------- #
# The publish-time backfill choice (the Review and publish panel)
# --------------------------------------------------------------------------- #

#: "Yes, enroll existing [objects] which meet the trigger criteria as of now."
BACKFILL_EXISTING = "existing_and_future"

#: "No, only enroll [objects] which meet the trigger criteria after turning the
#: workflow on." The records that match today are deferred, not excluded.
BACKFILL_FUTURE_ONLY = "future_only"

BACKFILL_CHOICES: tuple[str, ...] = (BACKFILL_EXISTING, BACKFILL_FUTURE_ONLY)

#: The choice this build makes when the publish panel does not state one. This
#: workflow's purpose is scheduled enrollment with backfill, so the default
#: enrols the records that already match. Recorded as an inference.
DEFAULT_BACKFILL = BACKFILL_EXISTING

#: The two backfill choices in the words the publish panel offers.
BACKFILL_LABELS: dict[str, str] = {
    BACKFILL_EXISTING: "Yes, enroll existing objects which meet the trigger criteria as of now",
    BACKFILL_FUTURE_ONLY: (
        "No, only enroll objects which meet the trigger criteria after turning the workflow on"
    ),
}

# --------------------------------------------------------------------------- #
# The action set the data flow names
# --------------------------------------------------------------------------- #

ACTION_CREATE_TASK = "Create task"
ACTION_NOTIFY = "Send internal notification"
ACTION_UPDATE_FIELD = "Field update"
ACTION_UPDATE_ASSOCIATED = "Associated-record update"

ACTIONS: tuple[str, ...] = (
    ACTION_CREATE_TASK,
    ACTION_NOTIFY,
    ACTION_UPDATE_FIELD,
    ACTION_UPDATE_ASSOCIATED,
)

#: The actions this build actually performs. The other two are declared in the
#: vocabulary and refused at execution, so a schedule that asks for one says so
#: rather than appearing to have done it.
IMPLEMENTED_ACTIONS: tuple[str, ...] = (ACTION_CREATE_TASK, ACTION_NOTIFY)

DEFAULT_ACTIONS: tuple[str, ...] = (ACTION_CREATE_TASK, ACTION_NOTIFY)

# --------------------------------------------------------------------------- #
# The enrollment filter
# --------------------------------------------------------------------------- #

#: The operators a filter condition may use. Closed, so a page cannot ask for a
#: comparison the matcher does not implement.
OP_EQ = "eq"
OP_NE = "ne"
OP_IN = "in"
OP_NOT_IN = "not_in"
OP_CONTAINS = "contains"
OP_EXISTS = "exists"
OP_GT = "gt"
OP_GTE = "gte"
OP_LT = "lt"
OP_LTE = "lte"

OPS: tuple[str, ...] = (
    OP_EQ,
    OP_NE,
    OP_IN,
    OP_NOT_IN,
    OP_CONTAINS,
    OP_EXISTS,
    OP_GT,
    OP_GTE,
    OP_LT,
    OP_LTE,
)

OP_LABELS: dict[str, str] = {
    OP_EQ: "equals",
    OP_NE: "is not",
    OP_IN: "is one of",
    OP_NOT_IN: "is none of",
    OP_CONTAINS: "contains",
    OP_EXISTS: "is set",
    OP_GT: "is greater than",
    OP_GTE: "is at least",
    OP_LT: "is less than",
    OP_LTE: "is at most",
}

# --------------------------------------------------------------------------- #
# Scheduling
# --------------------------------------------------------------------------- #

#: How often the schedule runs, in minutes. The research fixes no interval, so
#: this is an inferred default and a per-schedule setting.
DEFAULT_INTERVAL_MINUTES = 60

#: How long an enrolled record is held in the queue before its actions fire, in
#: minutes. The research names a "configured delay" and no unit.
DEFAULT_DELAY_MINUTES = 0

#: The run kinds a run log distinguishes.
RUN_ACTIVATION = "activation"
RUN_SCHEDULE = "schedule"
RUN_BACKFILL = "backfill"

RUN_KINDS: tuple[str, ...] = (RUN_ACTIVATION, RUN_SCHEDULE, RUN_BACKFILL)

# --------------------------------------------------------------------------- #
# The workflow log and the marker on the record
# --------------------------------------------------------------------------- #

#: Every reason a record is enrolled or skipped, mirrored in the page's labels.
REASON_MATCHED = "matched"
REASON_RE_ENROLLED = "re_enrolled"
REASON_NO_FILTER_MATCH = "no_filter_match"
REASON_ALREADY_ENROLLED = "already_enrolled"
REASON_SINGLE_ENROLLMENT = "single_enrollment"
REASON_DEFERRED = "deferred"
REASON_UNENROLLED = "unenrolled"

REASONS: tuple[str, ...] = (
    REASON_MATCHED,
    REASON_RE_ENROLLED,
    REASON_NO_FILTER_MATCH,
    REASON_ALREADY_ENROLLED,
    REASON_SINGLE_ENROLLMENT,
    REASON_DEFERRED,
    REASON_UNENROLLED,
)

#: The marker keys written onto a CRM record row by the researched "field
#: updates" action. They are what makes the enrollment history readable from the
#: record and what powers a workflow-based filter. All are ordinary JSON.
MARKER_AT_MS = "wf120_last_enrolled_at_ms"
MARKER_SCHEDULE = "wf120_last_enrolled_schedule"
MARKER_STATE = "wf120_enrollment_state"
MARKER_KEYS: tuple[str, ...] = (MARKER_AT_MS, MARKER_SCHEDULE, MARKER_STATE)

#: The kinds of line the workflow log carries.
EVENT_ENROLLED = "enrolled"
EVENT_RE_ENROLLED = "re_enrolled"
EVENT_QUEUED = "queued"
EVENT_RELEASED = "actions_fired"
EVENT_COMPLETED = "completed"
EVENT_UNENROLLED = "unenrolled"
EVENT_DEFERRED = "deferred"
EVENT_RUN = "run"

EVENT_KINDS: tuple[str, ...] = (
    EVENT_ENROLLED,
    EVENT_RE_ENROLLED,
    EVENT_QUEUED,
    EVENT_RELEASED,
    EVENT_COMPLETED,
    EVENT_UNENROLLED,
    EVENT_DEFERRED,
    EVENT_RUN,
)

# --------------------------------------------------------------------------- #
# The researched limits and retention windows
# --------------------------------------------------------------------------- #

#: "You can create up to 100 unique webhook events."
WEBHOOK_EVENT_LIMIT = 100

#: "Webhook triggers only support incoming data sent with the application/json
#: content-type."
WEBHOOK_CONTENT_TYPE = "application/json"

#: "HubSpot will store workflow action logs data within the below time periods:
#: 90 days ... 6 months ... 2 years+." Six months is stored as its approximate
#: day count so the cutoff helper works in days.
RETENTION_ACTION_LOG_DAYS = 90
RETENTION_ENROLLMENT_HISTORY_DAYS = 183
RETENTION_ENROLLMENTS_DAYS = 730

RETENTION: dict[str, int] = {
    "action_log_days": RETENTION_ACTION_LOG_DAYS,
    "enrollment_history_days": RETENTION_ENROLLMENT_HISTORY_DAYS,
    "enrollments_days": RETENTION_ENROLLMENTS_DAYS,
}

RETENTION_QUOTE = (
    "[sourced] HubSpot will store workflow action logs data within the below time "
    "periods: 90 days: all workflow action log data; 6 months: workflow enrollment "
    "history; 2 years+: historical data of enrollments to enable workflow-based filters."
)

# --------------------------------------------------------------------------- #
# Sourced quotes, gaps and sources
# --------------------------------------------------------------------------- #

SOURCED_QUOTES: dict[str, str] = {
    "trigger_types": (
        "[sourced] When setting up enrollment triggers, you can use the following "
        "enrollment trigger types: Filter-based, Event-based, Based on a schedule, "
        "Based on webhooks."
    ),
    "default_single_enrollment": (
        "[sourced] By default, records will only enroll in a workflow the first time "
        "they meet the enrollment triggers."
    ),
    "reenroll_after_completion": (
        "[sourced] Records that are currently enrolled in a workflow cannot be "
        "re-enrolled into that same workflow until they complete the workflow."
    ),
    "reenroll_is_not_automatic": (
        "[sourced] Turning on re-enrollment does not automatically enroll records into "
        "the workflow multiple times. A record can only re-enroll if it has completed "
        "the workflow, and is enrolled again."
    ),
    "scheduled_deferral": (
        "[sourced] For scheduled workflows, if you choose not to enroll existing "
        "records at activation, those records are not enrolled immediately, but they "
        "can still be enrolled the next time the workflow runs on its schedule if they "
        "still meet the enrollment criteria."
    ),
    "backfill_choice": (
        "[sourced] Review and publish offers: Yes, enroll existing objects which meet "
        "the trigger criteria as of now; or No, only enroll objects which meet the "
        "trigger criteria after turning the workflow on."
    ),
    "queue_delay": (
        "[sourced] Each enrolled record is held in an enrollment queue for the "
        "configured delay, then the actions fire in order."
    ),
    "webhook_limit": "[sourced] You can create up to 100 unique webhook events.",
    "webhook_content_type": (
        "[sourced] Webhook triggers only support incoming data sent with the "
        "application/json content-type."
    ),
    "unique_property": (
        "[sourced] The workflow will only trigger if there is an existing record with "
        "a corresponding unique property value in your HubSpot account."
    ),
    "retention": RETENTION_QUOTE,
}

#: What this build could not source. A reader must treat each line as a
#: hypothesis and not as a specification.
SOURCED_GAPS: tuple[str, ...] = (
    "The data flow names four actions - task creation, internal notification, "
    "field updates and associated-record updates - and no workflow that provisions a "
    "field-update or associated-record surface. This build performs the first two and "
    "declares the other two in the vocabulary without executing them.",
    "The research names no vendor endpoint for scheduled enrollment. This build "
    "stores schedules, enrollments, runs and the workflow log locally, in the same "
    "records store every other workflow uses, rather than calling a vendor.",
    "The research names a configured queue delay and no unit for it. This build "
    "stores the delay in minutes and records the choice as an inference.",
    "The research names unenrollment criteria and does not specify them. This build "
    "reads them as a second record filter and records the choice as an inference.",
    "The research names ticket, quote and custom object records and no workflow that "
    "provisions them. This build scans whatever object a schedule names, so the three "
    "scan without this workflow claiming they exist.",
)

SOURCES: tuple[str, ...] = (
    "https://knowledge.hubspot.com/workflows/create-workflows",
    "https://knowledge.hubspot.com/workflows/set-when-a-webhook-is-received-workflow-triggers",
    "https://developers.hubspot.com/docs/api-reference/crm-tasks-v3/basic/post-crm-v3-objects-tasks",
)

#: The researched user flow, verbatim, as the order the page walks a seller
#: through.
USER_FLOW: tuple[str, ...] = (
    "Automation > Workflows > Create workflow > From scratch (or With AI, or From template).",
    "In the Trigger enrollment for [object] card, choose the Based on a schedule trigger type.",
    "In the Settings tab, toggle Re-enroll on and choose the re-enrollment triggers; "
    "optionally set unenrollment criteria.",
    "Add actions via the + icon (e.g. Create task, Send internal notification).",
    "Review with Review and publish: enroll existing matches now, or only those that "
    "match after turning the workflow on.",
    "Review Timing & Notifications and Connections, then Turn on workflow.",
    "Monitor via the workflow history.",
)


#: The user flow, numbered, as a page renders it.
def describe_user_flow() -> list[dict[str, str]]:
    return [{"step": str(index), "text": text} for index, text in enumerate(USER_FLOW, start=1)]


def published_vocabulary() -> dict[str, Any]:
    """Every closed word and every sourced rule, in one payload.

    The page reads this instead of repeating the words, so a panel cannot drift
    from the rules the engine enforces.
    """

    return {
        "ticket": TICKET,
        "feature_id": FEATURE_ID,
        "name": NAME,
        "trigger_types": list(TRIGGER_TYPES),
        "default_trigger": DEFAULT_TRIGGER,
        "crm_objects": list(CRM_OBJECTS),
        "default_object": DEFAULT_OBJECT,
        "statuses": list(STATUSES),
        "active_statuses": list(ACTIVE_STATUSES),
        "terminal_statuses": list(TERMINAL_STATUSES),
        "reenroll_modes": list(REENROLL_MODES),
        "default_reenroll_mode": DEFAULT_REENROLL_MODE,
        "backfill_choices": list(BACKFILL_CHOICES),
        "backfill_labels": dict(BACKFILL_LABELS),
        "default_backfill": DEFAULT_BACKFILL,
        "actions": list(ACTIONS),
        "implemented_actions": list(IMPLEMENTED_ACTIONS),
        "default_actions": list(DEFAULT_ACTIONS),
        "ops": list(OPS),
        "op_labels": dict(OP_LABELS),
        "run_kinds": list(RUN_KINDS),
        "reasons": list(REASONS),
        "marker_keys": list(MARKER_KEYS),
        "event_kinds": list(EVENT_KINDS),
        "default_interval_minutes": DEFAULT_INTERVAL_MINUTES,
        "default_delay_minutes": DEFAULT_DELAY_MINUTES,
        "webhook_event_limit": WEBHOOK_EVENT_LIMIT,
        "webhook_content_type": WEBHOOK_CONTENT_TYPE,
        "retention": dict(RETENTION),
        "collections": dict(COLLECTIONS),
        "user_flow": describe_user_flow(),
        "sourced_quotes": dict(SOURCED_QUOTES),
        "sourced_gaps": list(SOURCED_GAPS),
        "sources": list(SOURCES),
    }


__all__ = [
    "ACTION_CREATE_TASK",
    "ACTION_NOTIFY",
    "ACTION_UPDATE_ASSOCIATED",
    "ACTION_UPDATE_FIELD",
    "ACTIONS",
    "ACTIVE_STATUSES",
    "BACKFILL_CHOICES",
    "BACKFILL_EXISTING",
    "BACKFILL_FUTURE_ONLY",
    "BACKFILL_LABELS",
    "COLLECTIONS",
    "CRM_OBJECTS",
    "CRM_RECORD_COLLECTION",
    "DEFAULT_ACTIONS",
    "DEFAULT_BACKFILL",
    "DEFAULT_DELAY_MINUTES",
    "DEFAULT_INTERVAL_MINUTES",
    "DEFAULT_OBJECT",
    "DEFAULT_REENROLL_MODE",
    "DEFAULT_TRIGGER",
    "ENROLLMENT_COLLECTION",
    "EVENT_COLLECTION",
    "EVENT_COMPLETED",
    "EVENT_DEFERRED",
    "EVENT_ENROLLED",
    "EVENT_KINDS",
    "EVENT_QUEUED",
    "EVENT_RELEASED",
    "EVENT_RE_ENROLLED",
    "EVENT_RUN",
    "EVENT_UNENROLLED",
    "FEATURE_ID",
    "IMPLEMENTED_ACTIONS",
    "MARKER_AT_MS",
    "MARKER_KEYS",
    "MARKER_SCHEDULE",
    "MARKER_STATE",
    "NAME",
    "NOTIFICATION_COLLECTION",
    "OPS",
    "OP_LABELS",
    "OP_CONTAINS",
    "OP_EQ",
    "OP_EXISTS",
    "OP_GT",
    "OP_GTE",
    "OP_IN",
    "OP_LT",
    "OP_LTE",
    "OP_NE",
    "OP_NOT_IN",
    "REASONS",
    "REASON_ALREADY_ENROLLED",
    "REASON_DEFERRED",
    "REASON_MATCHED",
    "REASON_NO_FILTER_MATCH",
    "REASON_RE_ENROLLED",
    "REASON_SINGLE_ENROLLMENT",
    "REASON_UNENROLLED",
    "REENROLL_AFTER_COMPLETION",
    "REENROLL_MODES",
    "REENROLL_SINGLE",
    "RETENTION",
    "RETENTION_ACTION_LOG_DAYS",
    "RETENTION_ENROLLMENTS_DAYS",
    "RETENTION_ENROLLMENT_HISTORY_DAYS",
    "RETENTION_QUOTE",
    "RUN_ACTIVATION",
    "RUN_BACKFILL",
    "RUN_COLLECTION",
    "RUN_KINDS",
    "RUN_SCHEDULE",
    "SCHEDULE_COLLECTION",
    "SOURCED_GAPS",
    "SOURCED_QUOTES",
    "SOURCES",
    "STATUSES",
    "STATUS_COMPLETED",
    "STATUS_QUEUED",
    "STATUS_UNENROLLED",
    "TERMINAL_STATUSES",
    "TICKET",
    "TRIGGER_EVENT_BASED",
    "TRIGGER_FILTER_BASED",
    "TRIGGER_SCHEDULE",
    "TRIGGER_TYPES",
    "TRIGGER_WEBHOOK",
    "USER_FLOW",
    "WEBHOOK_CONTENT_TYPE",
    "WEBHOOK_EVENT_LIMIT",
    "describe_user_flow",
    "published_vocabulary",
]
