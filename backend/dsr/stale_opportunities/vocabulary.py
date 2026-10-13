"""WF-117: the researched vocabulary for a stale-opportunity sweep.

The workflow is "Sweep stale opportunities and escalate the pipeline". The
research is ``docs/research/digital-sales-room-workflows/wf/WF-117.md``, itself
built from section 14 of ``docs/research/raw/automation-engagement.md``.

Every term a page renders or a validator enforces is declared here beside the
sentence it came from. Three kinds of term live here:

* a **researched** term carries a quote: the research states it.
* an **inferred** term carries nothing and is recorded in
  :mod:`dsr.stale_opportunities.inferences` with what would change it.
* a **collection name** is this build's own storage key and carries neither.

Reuse, not re-declaration
-------------------------

The sweep evaluates opportunity records and escalates them with a task, so it
depends on two merged workflows rather than inventing either shape:

* WF-039 (``dsr.atomic_bundle``) owns the CRM records the bundle writes.
  :data:`OPPORTUNITY_COLLECTION` is an alias onto its
  :data:`~dsr.atomic_bundle.transport.TARGET_COLLECTION`, and
  :data:`OPPORTUNITY_OBJECT` matches the object name its subrequests carry. A
  mismatch would silently find no opportunities, so the two spellings are one.
* WF-116 (``dsr.seller_tasks``) owns the workflow task store. The owner task the
  sweep creates is written through that workflow's engine, so this package does
  not re-declare a task status, priority or type.

Nothing here imports the framework. See
:mod:`dsr.stale_opportunities.engine` for the only module that reads or writes.
"""

from __future__ import annotations

from typing import Any

from dsr.atomic_bundle.transport import TARGET_COLLECTION

# --------------------------------------------------------------------------- #
# Identity
# --------------------------------------------------------------------------- #

TICKET = "WF-117"
FEATURE_ID = "wf-117-stale-opportunities"
NAME = "Sweep stale opportunities and escalate the pipeline"

#: Where an opportunity row lives. This is WF-039's collection, read by name and
#: not re-declared: the object the data flow evaluates is the row the bundle
#: wrote.
OPPORTUNITY_COLLECTION = TARGET_COLLECTION

#: The object name a CRM opportunity row carries. WF-039 writes the subrequest's
#: record type verbatim, and the researched bundle names it ``Opportunity``.
OPPORTUNITY_OBJECT = "Opportunity"

#: Collections this workflow owns. All three are ordinary JSON in
#: ``records.data``: no migration, no typed column.
SWEEP_COLLECTION = "wf117_sweep"
RUN_COLLECTION = "wf117_sweep_run"
ESCALATION_COLLECTION = "wf117_escalation"
NOTIFICATION_COLLECTION = "wf117_notification"

COLLECTIONS: dict[str, str] = {
    "opportunity_read": OPPORTUNITY_COLLECTION,
    "sweep": SWEEP_COLLECTION,
    "run": RUN_COLLECTION,
    "escalation": ESCALATION_COLLECTION,
    "notification": NOTIFICATION_COLLECTION,
}

# --------------------------------------------------------------------------- #
# The trigger family
# --------------------------------------------------------------------------- #

#: "Stale Opportunities: Fires when opportunities meet your inactivity/stale
#: rule."
TRIGGER_STALE = "Stale Opportunities"
#: "Opportunity Status Changed: Fires when an opportunity's status changes."
TRIGGER_STATUS_CHANGED = "Opportunity Status Changed"
#: "Opportunity Created".
TRIGGER_CREATED = "Opportunity Created"
#: "Opportunity Changed".
TRIGGER_CHANGED = "Opportunity Changed"
#: "Pipeline Stage Changed: Fires when an opportunity moves to a different
#: pipeline stage."
TRIGGER_STAGE_CHANGED = "Pipeline Stage Changed"

#: The trigger the sweep is created under, and the family of related automatic
#: triggers the research names beside it.
TRIGGERS: tuple[str, ...] = (
    TRIGGER_STALE,
    TRIGGER_STATUS_CHANGED,
    TRIGGER_CREATED,
    TRIGGER_CHANGED,
    TRIGGER_STAGE_CHANGED,
)

#: The two events the data flow says can re-enter other workflows.
EMITTED_EVENTS: tuple[str, ...] = (TRIGGER_STAGE_CHANGED, TRIGGER_CHANGED)

# --------------------------------------------------------------------------- #
# Opportunity status, quoted from the trigger evidence
# --------------------------------------------------------------------------- #

#: "Open to Won/Lost." The research names exactly three statuses.
STATUS_OPEN = "Open"
STATUS_WON = "Won"
STATUS_LOST = "Lost"

STATUSES: tuple[str, ...] = (STATUS_OPEN, STATUS_WON, STATUS_LOST)

# --------------------------------------------------------------------------- #
# The action set the research names
# --------------------------------------------------------------------------- #

ACTION_UPDATE_OPPORTUNITY = "Create/Update Opportunity"
ACTION_REMOVE_OPPORTUNITY = "Remove Opportunity"
ACTION_ADD_TASK = "Add Task"
ACTION_NOTIFY = "Send Internal Notification"
ACTION_EMAIL = "Send Email"
ACTION_WEBHOOK = "Custom Webhook"
ACTION_SHEETS = "Google Sheets"
ACTION_WAIT = "Wait Step"
ACTION_IF_ELSE = "If Else"
ACTION_REMOVE_FROM_WORKFLOW = "Remove from Workflow"
ACTION_GOAL = "Goal Event"
ACTION_SPLIT = "Split"
ACTION_ARRAYS = "Arrays"
ACTION_TEXT = "Text Formatter"
ACTION_CODE = "Custom Code"

ACTIONS: tuple[str, ...] = (
    ACTION_UPDATE_OPPORTUNITY,
    ACTION_REMOVE_OPPORTUNITY,
    ACTION_ADD_TASK,
    ACTION_NOTIFY,
    ACTION_EMAIL,
    ACTION_WEBHOOK,
    ACTION_SHEETS,
    ACTION_WAIT,
    ACTION_IF_ELSE,
    ACTION_REMOVE_FROM_WORKFLOW,
    ACTION_GOAL,
    ACTION_SPLIT,
    ACTION_ARRAYS,
    ACTION_TEXT,
    ACTION_CODE,
)

#: The three actions one escalation performs, in the order the data flow names
#: them: an opportunity write, a task for the owner, a notification to the team.
ESCALATION_ACTIONS: tuple[str, ...] = (
    ACTION_UPDATE_OPPORTUNITY,
    ACTION_ADD_TASK,
    ACTION_NOTIFY,
)

#: "Remove from Workflow: Removes contacts from a workflow. ... Useful: Helps
#: manage communication and actions by removing contacts from specific
#: workflows, ensuring targeted interactions." The research uses it so "the
#: sweep does not re-fire on the same record".
ACTION_REMOVE_QUOTE = (
    "Remove from Workflow: Removes contacts from a workflow. ... Helps manage "
    "communication and actions by removing contacts from specific workflows, "
    "ensuring targeted interactions."
)

# --------------------------------------------------------------------------- #
# The stale rule: which instant is measured, and how far
# --------------------------------------------------------------------------- #

#: The two fields the data flow names as the staleness evidence.
REFERENCE_LAST_ACTIVITY = "last_activity"
REFERENCE_STAGE_ENTRY = "stage_entry"
#: The chosen reading: the more recent of the two named fields. Recorded, with
#: its reasoning, in :mod:`dsr.stale_opportunities.inferences`.
REFERENCE_LATEST_OF_BOTH = "latest_of_both"

#: The reference readings this build accepts.
STALE_REFERENCES: tuple[str, ...] = (
    REFERENCE_LAST_ACTIVITY,
    REFERENCE_STAGE_ENTRY,
    REFERENCE_LATEST_OF_BOTH,
)

#: The reading this build measures against, and the one Jev selected at
#: confidence 0.98 (audit ``jev-20261013T192612-13876-72068``).
DEFAULT_STALE_REFERENCE = REFERENCE_LATEST_OF_BOTH

#: The default inactivity threshold, in days. The research gives the cadence as
#: an example and sets no rule, so this is an inferred default.
DEFAULT_THRESHOLD_DAYS = 7

#: The default graduated cadence, quoted from the user flow: "a nudge at 7 days,
#: escalate at 21 days". The numbers are the research's own example.
NUDGE_AFTER_DAYS = 7
ESCALATE_AFTER_DAYS = 21

DEFAULT_CADENCE: tuple[dict[str, Any], ...] = (
    {"after_days": NUDGE_AFTER_DAYS, "action": "nudge"},
    {"after_days": ESCALATE_AFTER_DAYS, "action": "escalate"},
)

#: The two cadence steps one sweep may take. A "Wait" step is the mechanism the
#: research names for the graduated cadence; the step's action is what the wait
#: leads to.
STEP_NUDGE = "nudge"
STEP_ESCALATE = "escalate"
STEP_KINDS: tuple[str, ...] = (STEP_NUDGE, STEP_ESCALATE)

CADENCE_QUOTE = (
    "Wait Step - Delays the workflow for a specific time. ... Useful: scheduling "
    "actions or communications at a later time. ... add a Wait step to add a "
    "graduated cadence (e.g. nudge at 7 days, escalate at 21 days)."
)

# --------------------------------------------------------------------------- #
# The re-fire control
# --------------------------------------------------------------------------- #

#: The chosen control: the escalation is written onto the opportunity row by the
#: researched Create/Update Opportunity action, then a match whose reference
#: instant is not newer than that marker is skipped. Jev selected this at
#: confidence 1.00 (audit ``jev-20261013T192612-13876-72068``); the research
#: names Remove from Workflow as the idempotency control.
REFIRE_MARKER_ON_OPPORTUNITY = "marker_on_opportunity"
REFIRE_CONTROLS: tuple[str, ...] = ("marker_on_opportunity", "separate_ledger_row", "no_control")
DEFAULT_REFIRE_CONTROL = REFIRE_MARKER_ON_OPPORTUNITY

#: The marker keys written onto an opportunity row. All four are ordinary JSON.
MARKER_AT_MS = "wf117_last_escalated_at_ms"
MARKER_SWEEP_ID = "wf117_last_escalated_sweep"
MARKER_STEP = "wf117_last_escalated_step"
MARKER_FLAG = "wf117_escalated"
MARKER_KEYS: tuple[str, ...] = (MARKER_AT_MS, MARKER_SWEEP_ID, MARKER_STEP, MARKER_FLAG)

#: Every reason an opportunity can be skipped, mirrored in the page's labels.
REASON_BELOW_THRESHOLD = "below_threshold"
REASON_NO_EVIDENCE = "no_activity_evidence"
REASON_STAGE_FILTERED = "stage_filtered"
REASON_VALUE_FILTERED = "value_filtered"
REASON_ALREADY_ESCALATED = "already_escalated"
REASON_MATCHED = "matched"

REASONS: tuple[str, ...] = (
    REASON_NO_EVIDENCE,
    REASON_BELOW_THRESHOLD,
    REASON_STAGE_FILTERED,
    REASON_VALUE_FILTERED,
    REASON_ALREADY_ESCALATED,
    REASON_MATCHED,
)

# --------------------------------------------------------------------------- #
# Sourced quotes, gaps and sources
# --------------------------------------------------------------------------- #

SOURCED_QUOTES: dict[str, str] = {
    "trigger": (
        "[sourced] Stale Opportunities: Fires when opportunities meet your inactivity/stale rule."
    ),
    "status_changed": (
        "[sourced] Opportunity Status Changed: Fires when an opportunity's status "
        "changes (e.g., Open to Won/Lost)."
    ),
    "stage_changed": (
        "[sourced] Pipeline Stage Changed: Fires when an opportunity moves to a "
        "different pipeline stage."
    ),
    "update_opportunity": (
        "[sourced] Create/Update Opportunity: Creates or updates an opportunity in the pipeline."
    ),
    "remove_from_workflow": "[sourced] " + ACTION_REMOVE_QUOTE,
    "wait_step": "[sourced] " + CADENCE_QUOTE,
    "reenter": (
        "[sourced] the opportunity's Pipeline Stage Changed and Opportunity Changed "
        "events can re-enter other workflows."
    ),
}

#: What this build could not source. A reader must treat each line as a
#: hypothesis and not as a specification.
SOURCED_GAPS: tuple[str, ...] = (
    "The research names no public HighLevel REST endpoint for opportunity "
    "objects: 'No public HighLevel REST endpoint for opportunity objects is "
    "documented on the pages read. The automation surface is the workflow "
    "builder.' This build stores opportunities locally, in the same collection "
    "WF-039 writes, rather than calling a vendor.",
    "The research names no pipeline stage vocabulary. A sweep carries its own "
    "'escalate_to_stage' and the value vocabulary is left open.",
    "The research states what happens on a second sweep only through Remove from "
    "Workflow. This build models that as an escalation marker on the opportunity "
    "row and records the choice as an inference.",
    "The research gives the 7/21-day cadence as an example, not a rule. This "
    "build makes the cadence a per-sweep setting defaulting to the example.",
)

SOURCES: tuple[str, ...] = (
    "https://help.gohighlevel.com/support/solutions/articles/155000002292-a-list-of-workflow-triggers",
    "https://help.gohighlevel.com/support/solutions/articles/155000002294-what-are-workflow-actions-complete-list-",
    "https://help.gohighlevel.com/support/solutions/articles/155000002471-workflow-action-if-else",
    "https://marketplace.gohighlevel.com/docs/marketplace-modules/CustomActions/",
)

#: The researched user flow, verbatim, as the order the page walks a rep through.
USER_FLOW: tuple[str, ...] = (
    "Create a workflow and choose the trigger Stale Opportunities.",
    "Define the inactivity/stale rule (age since last activity / stage entry) and "
    "add filters (Pipeline Stage Changed context, Pipeline Stage Changed = a "
    "specific stage, opportunity value band).",
    "Add actions: Create/Update Opportunity, Add Task for the owner, Send Internal "
    "Notification to the rep/manager, optionally Send Email to the buyer, and "
    "Remove from Workflow so the sweep does not re-fire on the same record.",
    "Publish. The re-fire interval is controlled by the stale rule; add a Wait "
    "step to add a graduated cadence (e.g. nudge at 7 days, escalate at 21 days).",
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
        "triggers": list(TRIGGERS),
        "emitted_events": list(EMITTED_EVENTS),
        "statuses": list(STATUSES),
        "actions": list(ACTIONS),
        "escalation_actions": list(ESCALATION_ACTIONS),
        "stale_references": list(STALE_REFERENCES),
        "default_stale_reference": DEFAULT_STALE_REFERENCE,
        "default_threshold_days": DEFAULT_THRESHOLD_DAYS,
        "step_kinds": list(STEP_KINDS),
        "default_cadence": [dict(step) for step in DEFAULT_CADENCE],
        "refire_controls": list(REFIRE_CONTROLS),
        "default_refire_control": DEFAULT_REFIRE_CONTROL,
        "marker_keys": list(MARKER_KEYS),
        "reasons": list(REASONS),
        "collections": dict(COLLECTIONS),
        "opportunity_object": OPPORTUNITY_OBJECT,
        "user_flow": describe_user_flow(),
        "sourced_quotes": dict(SOURCED_QUOTES),
        "sourced_gaps": list(SOURCED_GAPS),
        "sources": list(SOURCES),
    }


__all__ = [
    "ACTION_ADD_TASK",
    "ACTION_IF_ELSE",
    "ACTION_NOTIFY",
    "ACTION_REMOVE_FROM_WORKFLOW",
    "ACTION_REMOVE_QUOTE",
    "ACTION_UPDATE_OPPORTUNITY",
    "ACTION_WAIT",
    "ACTIONS",
    "CADENCE_QUOTE",
    "COLLECTIONS",
    "DEFAULT_CADENCE",
    "DEFAULT_REFIRE_CONTROL",
    "DEFAULT_STALE_REFERENCE",
    "DEFAULT_THRESHOLD_DAYS",
    "EMITTED_EVENTS",
    "ESCALATE_AFTER_DAYS",
    "ESCALATION_ACTIONS",
    "ESCALATION_COLLECTION",
    "FEATURE_ID",
    "MARKER_AT_MS",
    "MARKER_FLAG",
    "MARKER_KEYS",
    "MARKER_STEP",
    "MARKER_SWEEP_ID",
    "NAME",
    "NOTIFICATION_COLLECTION",
    "NUDGE_AFTER_DAYS",
    "OPPORTUNITY_COLLECTION",
    "OPPORTUNITY_OBJECT",
    "REASONS",
    "REASON_ALREADY_ESCALATED",
    "REASON_BELOW_THRESHOLD",
    "REASON_MATCHED",
    "REASON_NO_EVIDENCE",
    "REASON_STAGE_FILTERED",
    "REASON_VALUE_FILTERED",
    "REFERENCE_LAST_ACTIVITY",
    "REFERENCE_LATEST_OF_BOTH",
    "REFERENCE_STAGE_ENTRY",
    "REFIRE_CONTROLS",
    "REFIRE_MARKER_ON_OPPORTUNITY",
    "RUN_COLLECTION",
    "SOURCED_GAPS",
    "SOURCED_QUOTES",
    "SOURCES",
    "STATUSES",
    "STATUS_LOST",
    "STATUS_OPEN",
    "STATUS_WON",
    "STEP_ESCALATE",
    "STEP_KINDS",
    "STEP_NUDGE",
    "STALE_REFERENCES",
    "SWEEP_COLLECTION",
    "TICKET",
    "TRIGGERS",
    "TRIGGER_CHANGED",
    "TRIGGER_CREATED",
    "TRIGGER_STAGE_CHANGED",
    "TRIGGER_STALE",
    "TRIGGER_STATUS_CHANGED",
    "USER_FLOW",
    "describe_user_flow",
    "published_vocabulary",
]
