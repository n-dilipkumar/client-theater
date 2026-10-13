"""WF-068: prepare for the meeting, then run the follow-up sequence.

A rep opens a meeting prep page before the call, and after the call an automation
enrols the prospect in a multi-touch follow-up sequence that owns its to-dos and
its emails. The researched specification is
``docs/research/digital-sales-room-workflows/wf/WF-068.md``, quoted in full in
issue 165, and it says of itself: "This spec does not state the flow as separate
fields. The evidence below is the specification. An implementer who needs a flow
the evidence does not contain must derive it and record the derivation, not
assume it."

So this package's first job is the derivation, and
:mod:`~dsr.meeting_followup.inferences` records it as data. The fifteen entries
there are every judgement call, each with the evidence it rests on, the reading it
rejected, what that reading would have cost, and the residual risk.
``GET /wf-068/decisions`` serves them.

Module map, in dependency order:

``vocabulary``
    Every researched term: the five trigger kinds, the three statuses and their
    transitions, the assignment endpoint with its scope and its 200-prospect cap,
    the three error codes, the two permission gates with the vendor's own
    sentences, the six prep sections, the four transcription formats, the four Cal
    webhooks, the vendor attribution table, and the seventeen collections.
``errors``
    One hierarchy, so the feature module registers one handler for the base and a
    specific one per refusal.
``crm``
    The four CRM object types, the sync cadence, the mandatory modification date,
    and the three researched permission gates as refusals.
``automations``
    The When / If / Then evaluation and the daily date-trigger schedule. Split in
    two so "they do not run immediately when an automation is created or edited" is
    a property of the shape rather than a comment.
``instances``
    The three-status state machine, and the fact that it has no terminal state.
``flows``
    The batch assignment split and the ordered to-do queue, with the researched
    ``"1/5"`` position label.
``prep``
    The prep page, the derived dry run, and Ask anything answered from context.
``reminders``
    The three researched Forecast timings, one implemented and two refused.
``cal``
    The four webhooks, the recording and transcription payloads, and the
    afterEvent workflow body.
``inferences``
    Every judgement call, named and served.
``engine``
    The facade the HTTP layer calls, and the only module that stores anything.

Four boundaries are worth stating before reading any of it.

**This package imports the store and nothing else.** No framework, no HTTP, no
sqlite3, and no other feature's module. Every rule is a function from a mapping to
a mapping, so each one is testable without a server and without a database.

**Nothing here opens a socket.** The evidence cites the full Gong and Cal web
APIs, and this product is a client of neither. The requests those endpoints would
carry are recorded on the record - :func:`~dsr.meeting_followup.crm.entities_request`
and :func:`~dsr.meeting_followup.cal.after_event_workflow` - so a reviewer can read
the researched call without a network request having happened.

**The clock is injected.** Every rule here is a statement about an instant: a
30-minute push window, a morning evaluation, a 24-hour reminder, a touch due on a
date. The engine takes a clock, and the rules take dates, so a test freezes the
calendar rather than reading the machine's.

**The researched enum has three values and no terminal one.** A finished sequence
stays Running. :mod:`~dsr.meeting_followup.instances` states that and serves
``terminal: false`` on every view, because inventing a fourth status would break the
one rule the issue fixes exactly.
"""

from __future__ import annotations

from dsr.meeting_followup import (
    automations,
    cal,
    crm,
    flows,
    inferences,
    instances,
    prep,
    reminders,
    vocabulary,
)
from dsr.meeting_followup.automations import (
    compare,
    conditions_hold,
    created_or_edited_at_was_not_evaluated,
    due_on_morning_run,
    evaluate,
    morning_run_time,
    trigger_matches,
    validate_automation,
    validate_trigger,
)
from dsr.meeting_followup.cal import (
    after_event_workflow,
    recording_payload,
    transcription_payload,
    validate_event,
    validate_workflow,
)
from dsr.meeting_followup.crm import (
    assert_engaged,
    assert_not_personal,
    assert_parent_account,
    collection_for,
    entities_request,
    has_engage_license,
    has_parent_account,
    normalise_object_type,
    normalise_record,
    validate_sync_run,
)
from dsr.meeting_followup.engine import FollowupEngine
from dsr.meeting_followup.errors import (
    AssignBatchTooLarge,
    AssigneeNotLicensed,
    AutomationInvalid,
    AutomationNotFound,
    ContactWithoutParentAccount,
    CrmRecordInvalid,
    DateTriggerObjectRefused,
    FlowInstanceNotFound,
    FlowInstanceTransitionRefused,
    FlowInvalid,
    FlowNotFound,
    FollowupError,
    PersonalFlowRefused,
    PrepPageInvalid,
    PrepPageNotFound,
    SlackChannelRefused,
    TodoNotFound,
    WebhookInvalid,
)
from dsr.meeting_followup.flows import (
    assign,
    build_touches,
    new_flow_instance_id,
    normalise_request,
    queue_position,
    queue_view,
    workspace_id,
)
from dsr.meeting_followup.inferences import INFERENCES, by_id, describe, describe_one
from dsr.meeting_followup.instances import (
    advance,
    allowed_transitions,
    can_transition,
    initial_status,
    status_view,
    valid_status,
)
from dsr.meeting_followup.prep import (
    answer,
    dry_run_feedback,
    dry_run_participant,
    group_activities,
    normalise_dry_run,
    normalise_page,
    page_view,
    push_reminder_due,
)
from dsr.meeting_followup.reminders import (
    FORECAST_REMINDERS,
    reminder_moment,
    validate_reminder,
)
from dsr.meeting_followup.vocabulary import (
    ALL_COLLECTIONS,
    ASSIGN_ERROR_CODES,
    CAL_WEBHOOK_EVENTS,
    CRM_SYNC_RECOMMENDED_RANGE,
    DATE_TRIGGER_SCHEDULE_QUOTE,
    FLOW_INSTANCE_STATUSES,
    FLOW_INSTANCE_TRANSITIONS,
    MAX_PROSPECTS_PER_REQUEST,
    OBJECT_TYPES,
    PREP_SECTIONS,
    TODO_QUEUE_QUOTE,
    TRANSCRIPTION_FORMATS,
    TRIGGERS,
    VENDORS,
)

__all__ = [
    "ALL_COLLECTIONS",
    "AssignBatchTooLarge",
    "AssigneeNotLicensed",
    "ASSIGN_ERROR_CODES",
    "AutomationInvalid",
    "AutomationNotFound",
    "automations",
    "after_event_workflow",
    "advance",
    "allowed_transitions",
    "answer",
    "assign",
    "assert_engaged",
    "assert_not_personal",
    "assert_parent_account",
    "build_touches",
    "by_id",
    "CAL_WEBHOOK_EVENTS",
    "cal",
    "can_transition",
    "collection_for",
    "compare",
    "conditions_hold",
    "ContactWithoutParentAccount",
    "created_or_edited_at_was_not_evaluated",
    "crm",
    "CrmRecordInvalid",
    "CRM_SYNC_RECOMMENDED_RANGE",
    "DATE_TRIGGER_SCHEDULE_QUOTE",
    "DateTriggerObjectRefused",
    "describe",
    "describe_one",
    "dry_run_feedback",
    "dry_run_participant",
    "due_on_morning_run",
    "entities_request",
    "evaluate",
    "FLOW_INSTANCE_STATUSES",
    "FLOW_INSTANCE_TRANSITIONS",
    "FlowInstanceNotFound",
    "FlowInstanceTransitionRefused",
    "FlowInvalid",
    "FlowNotFound",
    "flows",
    "FollowupEngine",
    "FollowupError",
    "FORECAST_REMINDERS",
    "group_activities",
    "has_engage_license",
    "has_parent_account",
    "inferences",
    "INFERENCES",
    "initial_status",
    "instances",
    "MAX_PROSPECTS_PER_REQUEST",
    "morning_run_time",
    "new_flow_instance_id",
    "normalise_dry_run",
    "normalise_object_type",
    "normalise_page",
    "normalise_record",
    "normalise_request",
    "OBJECT_TYPES",
    "page_view",
    "PersonalFlowRefused",
    "PREP_SECTIONS",
    "PrepPageInvalid",
    "PrepPageNotFound",
    "prep",
    "push_reminder_due",
    "queue_position",
    "queue_view",
    "recording_payload",
    "reminder_moment",
    "reminders",
    "SlackChannelRefused",
    "status_view",
    "TodoNotFound",
    "TODO_QUEUE_QUOTE",
    "transcription_payload",
    "TRANSCRIPTION_FORMATS",
    "TRIGGERS",
    "trigger_matches",
    "validate_automation",
    "validate_event",
    "validate_reminder",
    "validate_sync_run",
    "validate_trigger",
    "validate_workflow",
    "valid_status",
    "VENDORS",
    "vocabulary",
    "WebhookInvalid",
    "workspace_id",
]
