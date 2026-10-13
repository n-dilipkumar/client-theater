"""Every researched term WF-068 validates against.

Sourced from the specification of ``WF-068`` (issue 165), which is itself quoted
from ``docs/research/digital-sales-room-workflows/wf/WF-068.md``. Where a value
here is a quote, the comment says so. Where it is a derivation, the comment names
the derivation. Nothing in this module reads or writes anything.
"""

from __future__ import annotations

# --------------------------------------------------------------------------- #
# Provenance
# --------------------------------------------------------------------------- #

#: The ticket and the issue this build came from.
TICKET = "WF-068"
ISSUE_URL = "https://github.com/n-dilipkumar/client-theater/issues/165"
SPEC_PATH = "docs/research/digital-sales-room-workflows/wf/WF-068.md"

# --------------------------------------------------------------------------- #
# Vendors
# --------------------------------------------------------------------------- #
# The evidence mixes two vendors and the issue requires each capability to be
# attributed. "The evidence mixes two vendors. Gong supplies the prep page, the
# flows and the CRM upload. Cal supplies the follow-up webhooks and recording
# download links."

VENDORS: dict[str, tuple[str, ...]] = {
    "gong": ("meeting_prep", "dry_run", "engage_flows", "crm_upload", "digital_interactions"),
    "cal": ("followup_webhooks", "recording_downloads", "after_event_workflows"),
    "none": ("to_do_queue", "ask_anything", "forecast_reminders"),
}

#: Read from :data:`VENDORS`, so the two cannot drift.
GONG_CAPABILITIES: tuple[str, ...] = VENDORS["gong"]
CAL_CAPABILITIES: tuple[str, ...] = VENDORS["cal"]

# --------------------------------------------------------------------------- #
# CRM entities
# --------------------------------------------------------------------------- #
# The evidence names the object types in the upload path:
# "POST /v2/crm/entities?objectType=ACCOUNT|CONTACT|DEAL|LEAD".

OBJECT_TYPES: tuple[str, ...] = ("ACCOUNT", "CONTACT", "DEAL", "LEAD")
LEAD_OBJECT_TYPE = "LEAD"
CONTACT_OBJECT_TYPE = "CONTACT"
ACCOUNT_OBJECT_TYPE = "ACCOUNT"
DEAL_OBJECT_TYPE = "DEAL"

#: The upload endpoint, quoted: "POST /v2/crm/entities?objectType=...".
CRM_ENTITIES_ENDPOINT = "/v2/crm/entities"
CRM_INTEGRATIONS_ENDPOINT = "/v2/crm/integrations"
CRM_ENTITY_SCHEMA_ENDPOINT = "/v2/crm/entity-schema"

#: "we recommend updating between every 1-5 minutes". Both bounds are enforced.
CRM_SYNC_MIN_MINUTES = 1
CRM_SYNC_MAX_MINUTES = 5
CRM_SYNC_RECOMMENDED_RANGE: tuple[int, int] = (CRM_SYNC_MIN_MINUTES, CRM_SYNC_MAX_MINUTES)

#: "modificationDate is mandatory on updates". On a create it is absent.
MODIFICATION_DATE_FIELD = "modificationDate"
MODIFICATION_DATE_MANDATORY_ON = "update"

# --------------------------------------------------------------------------- #
# Flow automations: When / If / Then
# --------------------------------------------------------------------------- #
# The evidence names the triggers: "a CRM entity (Lead/Contact/Account/
# Opportunity) is created / is updated with / is created or updated with /
# (Opportunity only) current date is; or an AI tracker detects a concept on a
# call."

CRM_CREATED = "crm_entity_created"
CRM_UPDATED_WITH = "crm_entity_updated_with"
CRM_CREATED_OR_UPDATED = "crm_entity_created_or_updated"
CURRENT_DATE_IS = "current_date_is"
TRACKER_DETECTED = "tracker_detected"

#: The four CRM triggers. ``CURRENT_DATE_IS`` is deliberately absent: the
#: research restricts it to Opportunity.
CRM_TRIGGERS: tuple[str, ...] = (CRM_CREATED, CRM_UPDATED_WITH, CRM_CREATED_OR_UPDATED)
TRIGGERS: tuple[str, ...] = (*CRM_TRIGGERS, CURRENT_DATE_IS, TRACKER_DETECTED)

#: "(Opportunity only) current date is". A date trigger on anything else is a
#: refusal, not a warning.
DATE_TRIGGER_OBJECT_TYPE = DEAL_OBJECT_TYPE

#: Quoted verbatim: "Date-based triggers run on a daily schedule and are
#: evaluated each morning. They do not run immediately when an automation is
#: created or edited." The second sentence is the rule the tests assert.
DATE_TRIGGER_SCHEDULE_QUOTE = (
    "Date-based triggers run on a daily schedule and are evaluated each morning. "
    "They do not run immediately when an automation is created or edited."
)

#: The daily schedule the quote names. The hour is a derivation: the research says
#: "each morning" and does not name a time, so the first minute of the morning
#: window is used and the value is served so a page can show it.
DATE_TRIGGER_EVALUATION_HOUR = 6
DATE_TRIGGER_EVALUATION_MINUTE = 0

#: Derived: an automation created or edited at this instant has not been
#: evaluated, because the earliest evaluation is the next morning.
DATE_TRIGGER_NOT_EVALUATED_ON_EDIT = True

#: The trigger labels the evidence gives, served so a page does not have to
#: re-invent the product's wording.
TRIGGER_LABELS: dict[str, str] = {
    CRM_CREATED: "is created",
    CRM_UPDATED_WITH: "is updated with",
    CRM_CREATED_OR_UPDATED: "is created or updated with",
    CURRENT_DATE_IS: "current date is",
    TRACKER_DETECTED: "an AI tracker detects a concept on a call",
}

#: Quoted with the typo, because the issue says: "One evidence line contains a
#: typo in the source: 'Triggers can be based on CRM updates, scheduled dates, or
#: AI AI tracker detections'. Quote it as written if you cite it."
TRACKER_QUOTE_WITH_TYPO = (
    "Triggers can be based on CRM updates, scheduled dates, or AI AI tracker detections"
)
TRACKER_QUOTE_TYPO_NOTE = (
    "The duplicated 'AI' is a typo in the source quote. It is reproduced as "
    "written so a reviewer can match the evidence line."
)

# --------------------------------------------------------------------------- #
# Flow instance status
# --------------------------------------------------------------------------- #
# Quoted: "flowInstanceStatus ... enum: Running, Pending, Paused". The issue says
# "Model those three and no others."

STATUS_RUNNING = "Running"
STATUS_PENDING = "Pending"
STATUS_PAUSED = "Paused"
FLOW_INSTANCE_STATUSES: tuple[str, ...] = (STATUS_RUNNING, STATUS_PENDING, STATUS_PAUSED)

#: The three permitted transitions. Derived, because the research names the enum
#: and not its graph. ``Pending`` is reachable in both directions because it
#: describes a sequence whose first touch has not been delivered yet, and a
#: sequence can reach that state again if the owner pauses it and every
#: undelivered touch is re-armed. ``Running`` returns to ``Pending`` for the same
#: reason.
FLOW_INSTANCE_TRANSITIONS: dict[str, tuple[str, ...]] = {
    STATUS_RUNNING: (STATUS_PENDING, STATUS_PAUSED),
    STATUS_PENDING: (STATUS_RUNNING, STATUS_PAUSED),
    STATUS_PAUSED: (STATUS_RUNNING, STATUS_PENDING),
}

#: A new instance starts ``Pending``, and becomes ``Running`` when its first touch
#: is delivered. Derived: "Pending" is the only one of the three statuses that
#: describes a sequence which has not begun, and starting a five-touch sequence at
#: day zero as Running would claim the first email had already been sent.
FLOW_INSTANCE_INITIAL_STATUS = STATUS_PENDING

# --------------------------------------------------------------------------- #
# Assignment
# --------------------------------------------------------------------------- #
# Quoted: "You can assign up to 200 prospects to a flow in a single request.
# Required authorizaton scope: 'api:flows:write'." (The typo "authorizaton" is in
# the source and is corrected here to "authorization" because it is not a quote.)

ASSIGN_ENDPOINT = "/v2/flows/prospects/assign"
ASSIGN_SCOPE = "api:flows:write"
MAX_PROSPECTS_PER_REQUEST = 200

#: Quoted: "AssignedFlowFailure.errorCode enum: InvalidArgument, InvalidState,
#: UnexpectedError".
ERROR_INVALID_ARGUMENT = "InvalidArgument"
ERROR_INVALID_STATE = "InvalidState"
ERROR_UNEXPECTED_ERROR = "UnexpectedError"
ASSIGN_ERROR_CODES: tuple[str, ...] = (
    ERROR_INVALID_ARGUMENT,
    ERROR_INVALID_STATE,
    ERROR_UNEXPECTED_ERROR,
)

#: The request body the evidence names: "AssignFlowRequestV2{crmProspectsIds[],
#: flowId, flowInstanceOwnerEmail}".
ASSIGN_REQUEST_FIELDS: tuple[str, ...] = (
    "crmProspectsIds",
    "flowId",
    "flowInstanceOwnerEmail",
)

#: The response split the evidence names: "prospectsAssigned[] (flowInstanceId,
#: flowInstanceStatus ..., exclusive, workspaceId) vs prospectsNotAssigned[]
#: (errorCode ...)".
ASSIGNED_FIELDS: tuple[str, ...] = (
    "crmProspectId",
    "flowInstanceId",
    "flowInstanceStatus",
    "exclusive",
    "workspaceId",
)
NOT_ASSIGNED_FIELDS: tuple[str, ...] = ("crmProspectId", "errorCode", "detail")

# --------------------------------------------------------------------------- #
# Permission gates
# --------------------------------------------------------------------------- #
# Both gates are refusals, not warnings. The issue is explicit: "Two documented
# permission gates must both be honoured ... Implement both as refusals, not as
# warnings."

#: First gate, quoted: "The selected user must have an Engage license. If they
#: don't, the automation won't run."
ENGAGE_LICENSE_QUOTE = (
    "The selected user must have an Engage license. If they don't, the automation won't run."
)
ENGAGE_LICENSE_FIELD = "engage_licensed"

#: Second gate, quoted: "If the automation selects a contact without a parent
#: account, the automation will fail to run."
PARENT_ACCOUNT_QUOTE = (
    "If the automation selects a contact without a parent account, the automation will fail to run."
)
PARENT_ACCOUNT_FIELD = "parent_account_id"

#: Quoted: "Personal flows cannot be used with automations."
PERSONAL_FLOW_QUOTE = "Personal flows cannot be used with automations."
PERSONAL_FLOW_FIELD = "personal"

# --------------------------------------------------------------------------- #
# To-do queue
# --------------------------------------------------------------------------- #
# Quoted: "To-dos are grouped into a queue that shows your position as you move
# through them. For example, 1/5 means you're working on the first of five to-dos
# in that queue."

TODO_QUEUE_QUOTE = (
    "To-dos are grouped into a queue that shows your position as you move through "
    "them. For example, 1/5 means you're working on the first of five to-dos in that queue."
)

TODO_KINDS: tuple[str, ...] = ("task", "email", "call")
TODO_ACTIVITIES: tuple[str, ...] = (
    "calls",
    "emails",
    "digital_interactions",
    "crm_changes",
    "texts",
)

# --------------------------------------------------------------------------- #
# The prep page
# --------------------------------------------------------------------------- #
# Quoted: "pre-meeting summary (objectives, key topics, prior discussion
# highlights, Action items for you, suggested questions, reminders)".

PREP_SECTIONS: tuple[str, ...] = (
    "objectives",
    "key_topics",
    "prior_discussion_highlights",
    "action_items_for_you",
    "suggested_questions",
    "reminders",
)

#: Quoted: "a recent-activity feed (calls, emails, digital interactions, CRM
#: changes, texts)".
RECENT_ACTIVITY_KINDS: tuple[str, ...] = TODO_ACTIVITIES

#: Quoted: "The meeting prep page appears slightly differently in the mobile app
#: and does not display recent activities."
MOBILE_APP_OMITS_RECENT_ACTIVITY = True

#: Quoted: "Push notifications ... 30 minutes before each external meeting".
PUSH_NOTIFICATION_MINUTES = 30

#: The push window is measured against the meeting start.
PUSH_NOTIFICATION_SCHEDULE = "before_meeting_start"

#: Quoted: keyboard "Shift+M".
PREP_KEYBOARD_SHORTCUT = "Shift+M"

#: The research names six entry points into the prep page. All six are recorded
#: so a page can say where a rep came from without inventing a seventh.
PREP_ENTRY_POINTS: tuple[str, ...] = (
    "homepage_conversations",
    "quick_access_upcoming",
    "keyboard_shortcut",
    "account_console",
    "activity_calendar",
    "digest_email",
    "mobile_push_notification",
)

# --------------------------------------------------------------------------- #
# Dry run
# --------------------------------------------------------------------------- #
# Quoted: "rehearse with an AI participant built from the meeting context,
# chooses a voice, gets feedback, and iterates with 'Go again'."

#: Derived. The research says "chooses a voice" and names no provider, no voice
#: catalogue and no synthesis. These are labels this build accepts; they are
#: names a UI renders, not claims about any vendor's catalogue.
DRY_RUN_VOICES: tuple[str, ...] = ("neutral", "warm", "direct")

#: Quoted: "iterates with 'Go again'".
DRY_RUN_AGAIN_LABEL = "Go again"

DRY_RUN_FEEDBACK_FIELDS: tuple[str, ...] = (
    "objectives_covered",
    "suggested_questions_used",
    "open_items",
)

#: The research names no summarisation model and no synthesis provider, so the
#: participant is a derived persona and the feedback is a derived checklist.
DRY_RUN_IS_DERIVED = True

# --------------------------------------------------------------------------- #
# Ask anything
# --------------------------------------------------------------------------- #
# The research gives "Ask anything" a name and nothing else: no inputs, no
# answers, no retrieval contract. This build records the question and answers
# from the prep context rather than pretending to a retrieval system.

ASK_ANYTHING_ANSWER_SOURCES: tuple[str, ...] = PREP_SECTIONS

# --------------------------------------------------------------------------- #
# Gong Forecast reminder timings
# --------------------------------------------------------------------------- #
# Quoted exactly: "in-app 24h before due, Slack 1h before to AEs, Slack summary
# 2h after to managers". The issue asks whether they are implemented.

FORECAST_IN_APP_HOURS_BEFORE = 24
FORECAST_SLACK_HOURS_BEFORE = 1
FORECAST_SLACK_SUMMARY_HOURS_AFTER = 2

#: Only the in-app reminder is modelled. The two Slack rows are refused at
#: validation because the product has no confirmed Slack surface, which the
#: issue names: "Slack appears in the evidence for Forecast reminders only. The
#: product has no confirmed Slack surface. Record what you left out."
FORECAST_CHANNELS: tuple[str, ...] = ("in_app", "slack")
FORECAST_IMPLEMENTED_CHANNELS: tuple[str, ...] = ("in_app",)

# --------------------------------------------------------------------------- #
# Cal: follow-up webhooks and recording downloads
# --------------------------------------------------------------------------- #
# Quoted: "webhooks MEETING_ENDED, RECORDING_READY,
# RECORDING_TRANSCRIPTION_GENERATED, BOOKING_NO_SHOW_UPDATED".

WEBHOOK_MEETING_ENDED = "MEETING_ENDED"
WEBHOOK_RECORDING_READY = "RECORDING_READY"
WEBHOOK_TRANSCRIPTION_GENERATED = "RECORDING_TRANSCRIPTION_GENERATED"
WEBHOOK_NO_SHOW_UPDATED = "BOOKING_NO_SHOW_UPDATED"
CAL_WEBHOOK_EVENTS: tuple[str, ...] = (
    WEBHOOK_MEETING_ENDED,
    WEBHOOK_RECORDING_READY,
    WEBHOOK_TRANSCRIPTION_GENERATED,
    WEBHOOK_NO_SHOW_UPDATED,
)

#: Quoted: "RECORDING_READY payload includes 'downloadLink': 'https://app.cal.com/
#: api/video/recording?token=...'" and "RECORDING_TRANSCRIPTION_GENERATED
#: includes downloadLinks.transcription[] in json/srt/txt/vtt".
RECORDING_DOWNLOAD_LINK_FIELD = "downloadLink"
TRANSCRIPTION_DOWNLOAD_LINKS_FIELD = "downloadLinks"
TRANSCRIPTION_FORMATS: tuple[str, ...] = ("json", "srt", "txt", "vtt")
TRANSCRIPTION_FORMAT_KEY = "transcription"

#: Quoted: "POST /v2/workflows with trigger afterEvent (offset) and template
#: completed / rating".
CAL_WORKFLOW_ENDPOINT = "/v2/workflows"
CAL_TRIGGER_AFTER_EVENT = "afterEvent"
CAL_TEMPLATES: tuple[str, ...] = ("completed", "rating")

# --------------------------------------------------------------------------- #
# Digital interactions
# --------------------------------------------------------------------------- #
# Quoted: "Content Shared / Content Viewed / Custom Action".

DIGITAL_INTERACTION_CONTENT_SHARED = "Content Shared"
DIGITAL_INTERACTION_CONTENT_VIEWED = "Content Viewed"
DIGITAL_INTERACTION_CUSTOM_ACTION = "Custom Action"
DIGITAL_INTERACTION_TYPES: tuple[str, ...] = (
    DIGITAL_INTERACTION_CONTENT_SHARED,
    DIGITAL_INTERACTION_CONTENT_VIEWED,
    DIGITAL_INTERACTION_CUSTOM_ACTION,
)

# --------------------------------------------------------------------------- #
# Collections
# --------------------------------------------------------------------------- #
# Every collection this package owns, named so no two features can collide.
# No migration and no typed column: the payload is ordinary JSON in
# ``records.data`` and a team adding a field needs no coordination.

ACCOUNT_COLLECTION = "wf068_crm_account"
CONTACT_COLLECTION = "wf068_crm_contact"
DEAL_COLLECTION = "wf068_crm_deal"
LEAD_COLLECTION = "wf068_crm_lead"
PREP_COLLECTION = "wf068_prep_page"
DRY_RUN_COLLECTION = "wf068_dry_run"
ASK_COLLECTION = "wf068_ask_anything"
AUTOMATION_COLLECTION = "wf068_flow_automation"
FLOW_COLLECTION = "wf068_flow"
INSTANCE_COLLECTION = "wf068_flow_instance"
TODO_COLLECTION = "wf068_flow_todo"
ACTIVITY_COLLECTION = "wf068_prep_activity"
TRACKER_COLLECTION = "wf068_ai_tracker_detection"
WEBHOOK_COLLECTION = "wf068_cal_webhook"
CRM_SYNC_COLLECTION = "wf068_crm_sync_run"
ASSIGN_COLLECTION = "wf068_flow_assignment"
REMINDER_COLLECTION = "wf068_forecast_reminder"

ALL_COLLECTIONS: tuple[str, ...] = (
    ACCOUNT_COLLECTION,
    ACTIVITY_COLLECTION,
    ASK_COLLECTION,
    ASSIGN_COLLECTION,
    AUTOMATION_COLLECTION,
    CONTACT_COLLECTION,
    CRM_SYNC_COLLECTION,
    DEAL_COLLECTION,
    DRY_RUN_COLLECTION,
    FLOW_COLLECTION,
    INSTANCE_COLLECTION,
    LEAD_COLLECTION,
    PREP_COLLECTION,
    REMINDER_COLLECTION,
    TODO_COLLECTION,
    TRACKER_COLLECTION,
    WEBHOOK_COLLECTION,
)

#: The collection each object type reads. Resolved from a table rather than
#: built in a route, so a caller cannot name a collection that is not ours.
COLLECTION_BY_OBJECT_TYPE: dict[str, str] = {
    ACCOUNT_OBJECT_TYPE: ACCOUNT_COLLECTION,
    CONTACT_OBJECT_TYPE: CONTACT_COLLECTION,
    DEAL_OBJECT_TYPE: DEAL_COLLECTION,
    LEAD_OBJECT_TYPE: LEAD_COLLECTION,
}

# --------------------------------------------------------------------------- #
# Derived defaults
# --------------------------------------------------------------------------- #

#: Derived. The research names "up to 200 prospects" and "assign to-dos grouped
#: into a queue" but no default cadence, so the default is the low end of the
#: researched CRM sync range and is served so a page can show it.
DEFAULT_SYNC_CADENCE_MINUTES = CRM_SYNC_MIN_MINUTES

#: Derived: a multi-touch sequence. The research says "multi-touch follow-up
#: sequence" and names no length.
DEFAULT_TOUCH_COUNT = 3

#: Derived: the offset unit. Cal's ``afterEvent`` takes an offset and the
#: research gives no vocabulary for it, so days are used and the unit is served.
DEFAULT_OFFSET_UNIT = "days"
