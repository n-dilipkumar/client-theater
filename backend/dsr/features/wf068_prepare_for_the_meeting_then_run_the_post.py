"""WF-068: prepare for the meeting, then run the follow-up sequence.

A rep opens a meeting prep page before the call, and after the call an automation
enrols the prospect in a multi-touch follow-up sequence that owns its to-dos and
its emails.

The rules live in :mod:`dsr.meeting_followup` and are not restated here. This module
is the three things a feature contributes and the three things it must never
contribute.

What this module contributes
----------------------------

* The route table, under a prefix this feature owns.
* The mapping from this workflow's own error types to responses, exported as
  :data:`EXCEPTION_HANDLERS` because FastAPI accepts handlers on the app object
  only and this feature may not edit the app.
* The demo rows, as ``seed(db, context)`` rather than as an edit to the shared
  ``backend/seed.py``.

What it must never contribute
----------------------------

* No shared file. ``dsr/api.py``, ``dsr/deps.py``, ``dsr/store.py``,
  ``dsr/db/audited.py``, ``backend/seed.py``, ``App.jsx``, ``main.jsx``,
  ``lib/api.js``, ``lib/features.js``, ``components/ui.jsx``, ``vite.config.js``.
  Twelve workflow branches each editing those files is why none of the original
  twelve merged.
* No import of ``dsr.api``. Dependencies come from ``dsr.deps``. A test enforces
  it.
* No hand-written ``source=`` string. :func:`_source` builds every one from
  :data:`router`, so the audit row names a route the host actually mounted.

One status code is worth naming here
------------------------------------

Every refusal in this workflow carries its own status on its own class, and the
feature module reads it rather than deciding it. That is why
:class:`~dsr.meeting_followup.errors.AssigneeNotLicensed` is a 403 and
:class:`~dsr.meeting_followup.errors.ContactWithoutParentAccount` is a 409: the
caller did nothing wrong in the first case and the organisation's licence does not
cover the assignee they named, while in the second the contact is valid and the
assignment is not. Neither is a 400, which would tell the caller to fix their
request when the thing to fix is not in the request.

The research's own 200-prospect cap is a refusal rather than a truncation. A
request over the cap is not a partial failure; it is a request the vendor does not
accept at all.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from dsr.db.audited import AuditedDatabase
from dsr.deps import StoreDep
from dsr.meeting_followup import (
    automations as rules,
    cal,
    crm,
    inferences,
    instances,
    prep,
    reminders,
    vocabulary as vocab,
)
from dsr.meeting_followup.engine import FollowupEngine
from dsr.meeting_followup.errors import (
    AssigneeNotLicensed,
    FlowInstanceTransitionRefused,
    FollowupError,
    PersonalFlowRefused,
    SlackChannelRefused,
)
from dsr.store import RecordStore

FEATURE = {
    "id": "wf-068-prepare-for-the-meeting-then-run-the-post",
    "ticket": "WF-068",
    "name": "Prepare for the meeting, then run the follow-up sequence",
    "description": (
        "Assemble a pre-meeting prep page from stored facts, rehearse it with a "
        "derived participant, and let a When / If / Then automation enrol the "
        "prospect in a multi-touch follow-up sequence that owns its to-dos and its "
        "emails. The two researched permission gates are refusals, the batch "
        "assignment splits per prospect rather than failing the batch, and a "
        "date-triggered automation waits for the next morning. Every derivation "
        "the research leaves open is named and served."
    ),
    "nav": [{"id": "wf-068-meeting-followup", "label": "Meeting follow-up"}],
}

#: Ticket-derived, so it cannot collide with the feature-shaped prefixes several
#: workflows already share (``/api/library``, ``/api/publishing``, ``/api/access``).
router = APIRouter(prefix="/api/wf-068", tags=["WF-068"])


def _source(method: str, path: str) -> str:
    """The audit ``source`` for a route, built from the router.

    Deliberately derived, never written as a literal. The defect this prevents is
    a route hardcoding a URL string, which leaves the audit log naming a route the
    app stopped serving. ``tests/test_wf068_http.py`` asserts every source this
    router can record matches a concrete ``(method, path)`` the host mounted.
    """
    return f"{method} {router.prefix}{path}"


def get_engine(store: RecordStore = StoreDep) -> FollowupEngine:
    """A :class:`FollowupEngine` over the process-wide audited store.

    Per request, so both the store and the clock stay overridable in a test
    instead of hanging a long-lived object off ``app.state`` - which is a shared
    file this feature may not edit.
    """
    return FollowupEngine(store)


EngineDep = Depends(get_engine)


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
#
# Every type here is declared in dsr.meeting_followup.errors and raised by nothing
# else in the product, which is what makes it safe to map them: the host refuses a
# second feature registering a handler for the same type, and a handler for
# ValueError would intercept that exception everywhere.
#
# One handler reads `exc.status`, so each class's own status is honoured rather
# than restated here. A second handler catches the transition refusal, whose body
# carries the alternatives a client needs to render a control.
#
# `RecordNotFound` is deliberately not claimed. The core app already maps it to
# 404, and two handlers for one type is a collision the host refuses.


def _followup_error(request: Request, exc: FollowupError) -> JSONResponse:
    """The one mapping, driven by the class's own ``status``.

    ``errors`` travels with every refusal that can say more than one thing at a
    time, keyed by field, so a form can put each message beside the input that
    caused it.
    """
    body: dict[str, Any] = {"error": exc.code, "detail": str(exc)}
    if exc.errors:
        body["errors"] = exc.errors
    return JSONResponse(status_code=exc.status, content=body)


def _transition_refused(request: Request, exc: FlowInstanceTransitionRefused) -> JSONResponse:
    """409 for a status change the three-value enum does not permit.

    The body carries the current status, the requested one and the alternatives,
    so a client can tell the reader which status is available rather than only
    that this one is not.
    """
    return JSONResponse(
        status_code=exc.status,
        content={
            "error": exc.code,
            "detail": str(exc),
            "status": exc.current,
            "requested": exc.requested,
            "allowed": list(exc.allowed),
            "statuses": list(vocab.FLOW_INSTANCE_STATUSES),
        },
    )


EXCEPTION_HANDLERS = {
    FlowInstanceTransitionRefused: _transition_refused,
    # Registered last and mapped by the class's own status: every subclass above
    # is more specific, so this only catches a type added to the hierarchy without
    # a handler of its own. Without it such a type would be a 500.
    FollowupError: _followup_error,
}


# --------------------------------------------------------------------------- #
# The board, the vocabulary, and the derivation record
# --------------------------------------------------------------------------- #


@router.get("/summary", summary="Counts for the page header")
def summary(
    room_id: str | None = Query(default=None),
    engine: FollowupEngine = EngineDep,
) -> dict[str, Any]:
    """Counts for the page header, and the automations waiting for a morning.

    Every count is computed over exactly the rows the same filters would return, so
    a room-scoped total above an unscoped list cannot be misread as a product-wide
    one.
    """
    return engine.summary(room_id=room_id)


@router.get("/vocabulary", summary="Every researched term this workflow validates against")
def vocabulary() -> dict[str, Any]:
    """Every researched term, served rather than duplicated in the frontend.

    A dropdown listing four triggers when the server accepts five is a form that
    submits something the server will refuse, so the closed sets come from the same
    tables the validators use.
    """
    return {
        "ticket": vocab.TICKET,
        "issue": vocab.ISSUE_URL,
        "spec": vocab.SPEC_PATH,
        "vendors": {name: list(caps) for name, caps in vocab.VENDORS.items()},
        "object_types": list(vocab.OBJECT_TYPES),
        "triggers": list(vocab.TRIGGERS),
        "crm_triggers": list(vocab.CRM_TRIGGERS),
        "trigger_labels": dict(vocab.TRIGGER_LABELS),
        "trigger_operators": COMPARISON_OPERATORS,
        "match_modes": ["all", "any"],
        "date_trigger_schedule_quote": vocab.DATE_TRIGGER_SCHEDULE_QUOTE,
        "date_trigger_object_type": vocab.DATE_TRIGGER_OBJECT_TYPE,
        "date_trigger_evaluation_hour": vocab.DATE_TRIGGER_EVALUATION_HOUR,
        "date_trigger_evaluation_minute": vocab.DATE_TRIGGER_EVALUATION_MINUTE,
        "statuses": list(vocab.FLOW_INSTANCE_STATUSES),
        "status_transitions": {
            key: list(value) for key, value in vocab.FLOW_INSTANCE_TRANSITIONS.items()
        },
        "status_initial": vocab.FLOW_INSTANCE_INITIAL_STATUS,
        "assignment": {
            "endpoint": vocab.ASSIGN_ENDPOINT,
            "scope": vocab.ASSIGN_SCOPE,
            "max_prospects": vocab.MAX_PROSPECTS_PER_REQUEST,
            "request_fields": list(vocab.ASSIGN_REQUEST_FIELDS),
            "assigned_fields": list(vocab.ASSIGNED_FIELDS),
            "not_assigned_fields": list(vocab.NOT_ASSIGNED_FIELDS),
            "error_codes": list(vocab.ASSIGN_ERROR_CODES),
        },
        "gates": {
            "engage_license": vocab.ENGAGE_LICENSE_QUOTE,
            "parent_account": vocab.PARENT_ACCOUNT_QUOTE,
            "personal_flow": vocab.PERSONAL_FLOW_QUOTE,
            "engage_license_field": vocab.ENGAGE_LICENSE_FIELD,
            "parent_account_field": vocab.PARENT_ACCOUNT_FIELD,
        },
        "crm_sync": {
            "recommended_range_minutes": list(vocab.CRM_SYNC_RECOMMENDED_RANGE),
            "default_cadence_minutes": vocab.DEFAULT_SYNC_CADENCE_MINUTES,
            "modification_date_field": vocab.MODIFICATION_DATE_FIELD,
            "modification_date_mandatory_on": vocab.MODIFICATION_DATE_MANDATORY_ON,
            "entities_endpoint": vocab.CRM_ENTITIES_ENDPOINT,
            "integrations_endpoint": vocab.CRM_INTEGRATIONS_ENDPOINT,
            "entity_schema_endpoint": vocab.CRM_ENTITY_SCHEMA_ENDPOINT,
        },
        "todo_queue_quote": vocab.TODO_QUEUE_QUOTE,
        "todo_kinds": list(vocab.TODO_KINDS),
        "prep": {
            "sections": list(vocab.PREP_SECTIONS),
            "recent_activity_kinds": list(vocab.RECENT_ACTIVITY_KINDS),
            "entry_points": list(vocab.PREP_ENTRY_POINTS),
            "keyboard_shortcut": vocab.PREP_KEYBOARD_SHORTCUT,
            "push_notification_minutes": vocab.PUSH_NOTIFICATION_MINUTES,
            "push_notification_schedule": vocab.PUSH_NOTIFICATION_SCHEDULE,
            "mobile_omits_recent_activity": vocab.MOBILE_APP_OMITS_RECENT_ACTIVITY,
        },
        "dry_run": {
            "voices": list(vocab.DRY_RUN_VOICES),
            "again_label": vocab.DRY_RUN_AGAIN_LABEL,
            "feedback_fields": list(vocab.DRY_RUN_FEEDBACK_FIELDS),
            "derived": vocab.DRY_RUN_IS_DERIVED,
        },
        "ask_anything_sources": list(vocab.ASK_ANYTHING_ANSWER_SOURCES),
        "forecast": {
            "channels": list(vocab.FORECAST_CHANNELS),
            "implemented_channels": list(vocab.FORECAST_IMPLEMENTED_CHANNELS),
            "in_app_hours_before": vocab.FORECAST_IN_APP_HOURS_BEFORE,
            "slack_hours_before": vocab.FORECAST_SLACK_HOURS_BEFORE,
            "slack_summary_hours_after": vocab.FORECAST_SLACK_SUMMARY_HOURS_AFTER,
            "reminders": reminders.served(),
        },
        "cal": {
            "webhook_events": list(vocab.CAL_WEBHOOK_EVENTS),
            "recording_download_link_field": vocab.RECORDING_DOWNLOAD_LINK_FIELD,
            "transcription_download_links_field": vocab.TRANSCRIPTION_DOWNLOAD_LINKS_FIELD,
            "transcription_formats": list(vocab.TRANSCRIPTION_FORMATS),
            "workflow_endpoint": vocab.CAL_WORKFLOW_ENDPOINT,
            "trigger": vocab.CAL_TRIGGER_AFTER_EVENT,
            "templates": list(vocab.CAL_TEMPLATES),
        },
        "digital_interaction_types": list(vocab.DIGITAL_INTERACTION_TYPES),
        "tracker_quote_with_typo": vocab.TRACKER_QUOTE_WITH_TYPO,
        "tracker_quote_note": vocab.TRACKER_QUOTE_TYPO_NOTE,
        "collections": list(vocab.ALL_COLLECTIONS),
        "default_touch_count": vocab.DEFAULT_TOUCH_COUNT,
        "default_offset_unit": vocab.DEFAULT_OFFSET_UNIT,
    }


#: The closed set of comparison operators a condition may use. Declared here rather
#: than in the vocabulary module because it is a property of the evaluator, and
#: served here because the vocabulary route is the one a form fetches.
COMPARISON_OPERATORS: tuple[str, ...] = (
    "equals",
    "not_equals",
    "contains",
    "greater_than",
    "less_than",
    "is_true",
    "is_false",
)


@router.get("/decisions", summary="Every derivation this workflow made")
def list_decisions() -> dict[str, Any]:
    """Every judgement call, with what it rejected.

    The research requires the derivation to be recorded rather than assumed. This
    route is that record, and it is served rather than buried in a docstring so a
    reviewer reads the decision instead of the code.
    """
    items = inferences.describe()
    return {"count": len(items), "decisions": items}


@router.get("/decisions/{decision_id}", summary="One derivation, by id")
def read_decision(decision_id: str) -> dict[str, Any]:
    """One judgement call by id."""
    return inferences.describe_one(decision_id)


# --------------------------------------------------------------------------- #
# The seller directory, which the Engage licence gate resolves against
# --------------------------------------------------------------------------- #


@router.get("/users", summary="The seller directory")
def list_users(engine: FollowupEngine = EngineDep) -> dict[str, Any]:
    """Every seller, with whether they hold an Engage licence.

    Served rather than filtered client-side, because an unlicensed assignee is a
    refusal and a page that offers one in a dropdown produces a 403 at submit.
    """
    rows = engine.directory_users()
    return {
        "count": len(rows),
        "users": rows,
        "licensed": sum(1 for row in rows if crm.has_engage_license(row.get("data") or {})),
        "engage_license_quote": vocab.ENGAGE_LICENSE_QUOTE,
    }


@router.post("/users", status_code=201, summary="Register a seller and their licence")
def add_user(
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(default=None),
    actor: str | None = Query(default=None),
    engine: FollowupEngine = EngineDep,
) -> dict[str, Any]:
    """Register one seller. A person and a boolean, never a credential."""
    return engine.add_user(payload, room_id=room_id, actor=actor, source=_source("POST", "/users"))


# --------------------------------------------------------------------------- #
# The CRM entity records
# --------------------------------------------------------------------------- #


@router.get("/crm/records", summary="Every CRM entity record")
def list_crm_records(
    object_type: str | None = Query(default=None),
    engine: FollowupEngine = EngineDep,
) -> dict[str, Any]:
    """The account, contact, deal and lead records the automations read.

    One route for the four object types rather than four routes, because the
    workflows read them identically and the only difference is the type. The
    collection is resolved from a table, so a caller cannot name a collection this
    workflow does not own.
    """
    rows = engine.crm_records(object_type)
    by_type: dict[str, int] = {}
    for row in rows:
        kind = str((row.get("data") or {}).get("object_type") or "")
        by_type[kind] = by_type.get(kind, 0) + 1
    return {
        "count": len(rows),
        "object_types": list(vocab.OBJECT_TYPES),
        "by_object_type": by_type,
        "records": rows,
    }


@router.post("/crm/records", status_code=201, summary="Create or update one CRM record")
def upsert_crm_record(
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(default=None),
    actor: str | None = Query(default=None),
    engine: FollowupEngine = EngineDep,
) -> dict[str, Any]:
    """Create or update one entity, through the same route.

    The vendor's own path is one, and the only difference is the operation and the
    mandatory modification date. Both are fields on the payload, so the rule is
    enforced once rather than in two handlers that could drift.
    """
    return engine.upsert_crm_record(
        payload.get("object_type"),
        payload,
        room_id=room_id,
        actor=actor,
        source=_source("POST", "/crm/records"),
    )


@router.post("/crm/sync", status_code=201, summary="Record a CRM sync run")
def sync_crm(
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(default=None),
    actor: str | None = Query(default=None),
    engine: FollowupEngine = EngineDep,
) -> dict[str, Any]:
    """Record one sync run and the outbound request it would carry.

    Two researched rules are refused here rather than warned about: a cadence
    outside one to five minutes, and an update with no ``modificationDate``. The
    response carries the exact request the Gong endpoint would receive, marked as
    not sent, because this product is not a Gong client.
    """
    return engine.sync_crm(
        payload, room_id=room_id, actor=actor, source=_source("POST", "/crm/sync")
    )


@router.get("/crm/sync/runs", summary="The recorded sync runs")
def list_sync_runs(engine: FollowupEngine = EngineDep) -> dict[str, Any]:
    """Every recorded sync run, newest first, with the request it would carry."""
    rows = engine.sync_runs()
    return {"count": len(rows), "runs": rows}


# --------------------------------------------------------------------------- #
# Flows
# --------------------------------------------------------------------------- #


@router.get("/flows", summary="Every follow-up flow")
def list_flows(engine: FollowupEngine = EngineDep) -> dict[str, Any]:
    """Every declared multi-touch flow, with the touch count it produces."""
    rows = engine.flows()
    return {
        "count": len(rows),
        "flows": [
            {
                **row,
                "data": {
                    **(row.get("data") or {}),
                    "touch_count": len(row["data"].get("steps") or []),
                },
            }
            for row in rows
        ],
    }


@router.post("/flows", status_code=201, summary="Declare a follow-up flow")
def create_flow(
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(default=None),
    actor: str | None = Query(default=None),
    engine: FollowupEngine = EngineDep,
) -> dict[str, Any]:
    """Declare the sequence: its steps, their offsets, and whether it is personal.

    Steps are stored in offset order, so the queue a seller reads is the sequence
    they wrote rather than the order a form posted it.
    """
    return engine.create_flow(
        payload, room_id=room_id, actor=actor, source=_source("POST", "/flows")
    )


@router.get("/flows/{flow_id}", summary="One flow")
def read_flow(flow_id: str, engine: FollowupEngine = EngineDep) -> dict[str, Any]:
    """One flow, resolved through its business id rather than its record id.

    ``flow_id`` is a field the seller chose, so it is a payload field and not the
    store's primary key. Resolving it through ``find`` is what lets a caller hold
    the id it sent rather than the id the store assigned.
    """
    return engine.get_flow(flow_id)


@router.get("/flows/{flow_id}/cal-workflow", summary="This flow as a Cal.com workflow")
def cal_workflow(
    flow_id: str,
    booking_uid: str = Query(default="", description="Anchor the offset on one booking"),
    event_ended_at: str = Query(default="", description="When the meeting ended"),
    engine: FollowupEngine = EngineDep,
) -> dict[str, Any]:
    """The ``POST /v2/workflows`` body this flow would carry.

    The offset is measured from the meeting **end**, which is why the ended-at
    instant travels with the request. This is the researched Cal half of the
    follow-up, and it is recorded rather than sent.
    """
    record = engine.get_flow(flow_id)
    return cal.after_event_workflow(
        record.get("data") or {},
        booking_uid=booking_uid or flow_id,
        event_ended_at=event_ended_at,
    )


# --------------------------------------------------------------------------- #
# Automations: When / If / Then
# --------------------------------------------------------------------------- #


@router.get("/automations", summary="Every flow automation")
def list_automations(engine: FollowupEngine = EngineDep) -> dict[str, Any]:
    """Every automation, with whether it is still waiting for its first morning."""
    rows = engine.automations()
    return {
        "count": len(rows),
        "triggers": list(vocab.TRIGGERS),
        "trigger_labels": dict(vocab.TRIGGER_LABELS),
        "automations": [
            {
                **row,
                "data": {
                    **(row.get("data") or {}),
                    "awaiting_morning_run": rules.created_or_edited_at_was_not_evaluated(
                        row.get("data") or {}
                    ),
                },
            }
            for row in rows
        ],
    }


@router.post("/automations", status_code=201, summary="Declare a flow automation")
def create_automation(
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(default=None),
    actor: str | None = Query(default=None),
    engine: FollowupEngine = EngineDep,
) -> dict[str, Any]:
    """``Assets > Automations`` > New: a When, an If, and the flow to enrol into.

    Three researched refusals fire before the record is written. The flow must
    exist and must not be personal, and the assignee must hold an Engage licence:
    "If they don't, the automation won't run."

    A date-triggered automation is accepted and **not run**. That is the researched
    behaviour, not an omission: "They do not run immediately when an automation is
    created or edited."
    """
    return engine.create_automation(
        payload, room_id=room_id, actor=actor, source=_source("POST", "/automations")
    )


@router.post("/automations/evaluate", summary="Evaluate one event against every automation")
def evaluate_event(
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: FollowupEngine = EngineDep,
) -> dict[str, Any]:
    """Evaluate one CRM or tracker event against every automation.

    Returns every decision rather than raising for an automation that did not fire.
    A buyer's activity failing to match a filter is a fact about the buyer, and a
    caller who got a 400 for it would retry forever.

    A date-triggered automation is deliberately not evaluated here. Its only path
    is ``POST /automations/morning-run``.
    """
    return engine.evaluate_event(
        payload, actor=actor, source=_source("POST", "/automations/evaluate")
    )


@router.post("/automations/morning-run", summary="The daily date-trigger schedule")
def morning_run(
    on: str | None = Query(default=None, description="The morning to evaluate, ISO date"),
    actor: str | None = Query(default=None),
    engine: FollowupEngine = EngineDep,
) -> dict[str, Any]:
    """Evaluate every date-triggered automation, once, for one morning.

    The only path that can enrol a deal on a date trigger. Quoted: "Date-based
    triggers run on a daily schedule and are evaluated each morning. They do not run
    immediately when an automation is created or edited."

    ``on`` is a parameter so a caller and a test can name the morning rather than
    inherit the machine's calendar. Omitting it uses the engine's clock.
    """
    day: date | None = None
    if on:
        try:
            day = date.fromisoformat(on)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"on must be an ISO date: {exc}") from exc
    return engine.morning_run(
        on=day, actor=actor, source=_source("POST", "/automations/morning-run")
    )


@router.get("/automations/{automation_id}", summary="One automation")
def read_automation(automation_id: str, engine: FollowupEngine = EngineDep) -> dict[str, Any]:
    """One automation, with the morning it will next be evaluated on."""
    record = engine.get_automation(automation_id)
    data = record.get("data") or {}
    waiting = rules.created_or_edited_at_was_not_evaluated(data)
    return {
        **record,
        "awaiting_morning_run": waiting,
        "next_evaluation": (
            rules.morning_run_time(engine.clock().date() + _one_day()).isoformat()
            if waiting and (data.get("trigger") or {}).get("kind") == vocab.CURRENT_DATE_IS
            else None
        ),
    }


def _one_day():
    """One day. Named so the next-evaluation arithmetic above reads as arithmetic."""
    from datetime import timedelta

    return timedelta(days=1)


# --------------------------------------------------------------------------- #
# The batch assignment
# --------------------------------------------------------------------------- #


@router.post("/flows/prospects/assign", summary="Assign prospects to a flow")
def assign_prospects(
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(default=None),
    actor: str | None = Query(default=None),
    engine: FollowupEngine = EngineDep,
) -> dict[str, Any]:
    """The researched programmatic seam: ``POST /v2/flows/prospects/assign``.

    Up to 200 prospects in one request, scope ``api:flows:write``. The response
    splits exactly as the vendor documents, and a failure is per prospect: "Failures
    are returned per-prospect with an error code rather than failing the batch."

    Two faults refuse the whole request rather than splitting it, because both are
    properties of the request and not of a prospect: an assignee with no Engage
    licence, and a personal flow. Everything else is per prospect.
    """
    return engine.enrol(
        payload, room_id=room_id, actor=actor, source=_source("POST", "/flows/prospects/assign")
    )


@router.get("/assignments", summary="Every recorded assignment")
def list_assignments(engine: FollowupEngine = EngineDep) -> dict[str, Any]:
    """Every batch assignment, with its split response."""
    rows = engine.store.list(vocab.ASSIGN_COLLECTION, limit=200)
    return {
        "count": len(rows),
        "max_prospects_per_request": vocab.MAX_PROSPECTS_PER_REQUEST,
        "error_codes": list(vocab.ASSIGN_ERROR_CODES),
        "assignments": rows,
    }


# --------------------------------------------------------------------------- #
# Flow instances and the to-do queue
# --------------------------------------------------------------------------- #


@router.get("/instances", summary="Every flow instance")
def list_instances(
    owner_email: str | None = Query(default=None),
    engine: FollowupEngine = EngineDep,
) -> dict[str, Any]:
    """Every enrolled prospect, with its status and its permitted transitions."""
    rows = engine.instances(owner_email=owner_email)
    return {
        "count": len(rows),
        "statuses": list(vocab.FLOW_INSTANCE_STATUSES),
        "instances": [
            {
                "id": row["id"],
                "data": (row.get("data") or {}),
                "view": instances.status_view(row.get("data") or {}),
            }
            for row in rows
        ],
    }


@router.get("/instances/{flow_instance_id}", summary="One flow instance")
def read_instance(flow_instance_id: str, engine: FollowupEngine = EngineDep) -> dict[str, Any]:
    """One instance, with its status, its alternatives and its touches."""
    record = engine.get_instance(flow_instance_id)
    return {**record, "view": instances.status_view(record.get("data") or {})}


@router.post("/instances/{flow_instance_id}/status", summary="Pause, resume or start an instance")
def set_instance_status(
    flow_instance_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: FollowupEngine = EngineDep,
) -> dict[str, Any]:
    """Move an instance between the three researched statuses.

    Refused with the alternatives attached when the transition is not in the enum.
    A refusal writes nothing: an audit row for a change that did not happen would be
    a lie about the record.
    """
    record = engine.set_instance_status(
        flow_instance_id,
        str(payload.get("status") or ""),
        actor=actor,
        source=_source("POST", "/instances/{flow_instance_id}/status"),
    )
    return {**record, "view": instances.status_view(record.get("data") or {})}


@router.get("/todos", summary="The to-do rows")
def list_todos(
    owner_email: str | None = Query(default=None),
    queue_id: str | None = Query(default=None),
    engine: FollowupEngine = EngineDep,
) -> dict[str, Any]:
    """The ordered to-dos, with the flow reference each one carries.

    Quoted: "the flow reference appears with the activity". Every row carries it, so
    a rep working the queue always knows which sequence the to-do came from.
    """
    rows = engine.todos(owner_email=owner_email, queue_id=queue_id)
    return {
        "count": len(rows),
        "queue_quote": vocab.TODO_QUEUE_QUOTE,
        "todos": [row.get("data") or row for row in rows],
    }


@router.post("/todos/{todo_id}/complete", summary="Complete one to-do in its queue")
def complete_todo(
    todo_id: str,
    actor: str | None = Query(default=None),
    engine: FollowupEngine = EngineDep,
) -> dict[str, Any]:
    """Complete one to-do. This is what moves the position in the queue."""
    return engine.complete_todo(
        todo_id, actor=actor, source=_source("POST", "/todos/{todo_id}/complete")
    )


@router.get("/queue", summary="One seller's grouped to-do queue")
def read_queue(
    owner_email: str = Query(...),
    flow_reference: str = Query(default=""),
    engine: FollowupEngine = EngineDep,
) -> dict[str, Any]:
    """The grouped queue, with each row's position as the researched label.

    Quoted: "To-dos are grouped into a queue that shows your position as you move
    through them. For example, 1/5 means you're working on the first of five to-dos
    in that queue." The position travels as the string ``1/5``, which is what the
    product shows.
    """
    return engine.queue(owner_email, flow_reference=flow_reference)


# --------------------------------------------------------------------------- #
# The prep page
# --------------------------------------------------------------------------- #


@router.get("/prep", summary="Every prep page")
def list_prep_pages(engine: FollowupEngine = EngineDep) -> dict[str, Any]:
    """Every meeting prep page."""
    rows = engine.prep_pages()
    return {
        "count": len(rows),
        "sections": list(vocab.PREP_SECTIONS),
        "pages": [row.get("data") or row for row in rows],
    }


@router.post("/prep", status_code=201, summary="Open a prep page")
def create_prep_page(
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(default=None),
    actor: str | None = Query(default=None),
    engine: FollowupEngine = EngineDep,
) -> dict[str, Any]:
    """Open a prep page for a meeting.

    The six researched sections are stored as given. Any other key under ``summary``
    is kept too, because the payload is ordinary JSON: a team adding a seventh
    section needs no migration and no coordination with anyone.
    """
    return engine.create_prep_page(
        payload, room_id=room_id, actor=actor, source=_source("POST", "/prep")
    )


@router.get("/prep/{meeting_id}", summary="The prep page as a rep reads it")
def read_prep_page(
    meeting_id: str,
    mobile: bool = Query(
        default=False, description="Answer as the mobile app, which omits the activity feed"
    ),
    engine: FollowupEngine = EngineDep,
) -> dict[str, Any]:
    """The prep page: summary, participants, and the recent-activity feed.

    ``mobile`` drops the feed, which the research says the mobile app does: "The
    meeting prep page appears slightly differently in the mobile app and does not
    display recent activities." A server that guessed would silently withhold a
    section a desktop rep needed, so the flag travels with the request.
    """
    return engine.prep_view(meeting_id, mobile=mobile)


@router.post("/prep/{meeting_id}/dry-run", status_code=201, summary="Rehearse the meeting")
def dry_run(
    meeting_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: FollowupEngine = EngineDep,
) -> dict[str, Any]:
    """Rehearse with an AI participant built from the meeting context.

    Derived, and marked as such on the record. The research names no voice
    provider and no synthesis model, so nothing is spoken: the participant is built
    from the stored context and the feedback is a checklist over the prep
    sections. Quoted: "iterates with 'Go again'"; the attempt number is recorded so
    a second rehearsal is visible as a second one.
    """
    return engine.dry_run(
        {"meeting_id": meeting_id, **payload},
        actor=actor,
        source=_source("POST", "/prep/{meeting_id}/dry-run"),
    )


@router.post("/prep/{meeting_id}/ask", status_code=201, summary="Ask anything about the deal")
def ask_anything(
    meeting_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(default=None),
    engine: FollowupEngine = EngineDep,
) -> dict[str, Any]:
    """Answer a question from the prep context, and record it.

    The research gives "Ask anything" a name and no behaviour. This build answers
    from the prep sections and names every section it used, because an invented
    answer in a meeting room is worse than a listed set of sources.
    """
    return engine.ask(
        meeting_id,
        str(payload.get("question") or ""),
        actor=actor,
        source=_source("POST", "/prep/{meeting_id}/ask"),
    )


@router.get("/prep/{meeting_id}/push-window", summary="Whether the push window is open")
def push_window(meeting_id: str, engine: FollowupEngine = EngineDep) -> dict[str, Any]:
    """Whether the "30 minutes before each external meeting" push is due.

    The window opens 30 minutes before the start and closes at it: a push that
    arrives after the call is noise.
    """
    record = engine.get_prep_page(meeting_id)
    data = record.get("data") or record
    due = prep.push_reminder_due(str(data.get("starts_at") or ""), engine.clock())
    return {
        "meeting_id": meeting_id,
        "starts_at": data.get("starts_at"),
        "due": due,
        "minutes": vocab.PUSH_NOTIFICATION_MINUTES,
        "schedule": vocab.PUSH_NOTIFICATION_SCHEDULE,
        "delivery": "recorded, not sent",
    }


# --------------------------------------------------------------------------- #
# AI tracker detections
# --------------------------------------------------------------------------- #


@router.post("/trackers", status_code=201, summary="Record an AI tracker detection")
def record_tracker(
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(default=None),
    actor: str | None = Query(default=None),
    engine: FollowupEngine = EngineDep,
) -> dict[str, Any]:
    """Record a detection and evaluate it against the automations, in one call.

    A tracker detection is one of the five researched triggers, so a caller that
    recorded one and did not evaluate the rule depending on it would have a
    detection that drove nothing. Both halves happen here.
    """
    return engine.record_tracker(
        payload, room_id=room_id, actor=actor, source=_source("POST", "/trackers")
    )


@router.get("/trackers", summary="Every recorded tracker detection")
def list_trackers(engine: FollowupEngine = EngineDep) -> dict[str, Any]:
    """The recorded detections, newest first."""
    rows = engine.detections()
    return {"count": len(rows), "detections": [row.get("data") or row for row in rows]}


# --------------------------------------------------------------------------- #
# Cal: the follow-up webhooks and the recording links
# --------------------------------------------------------------------------- #


@router.post("/webhooks/cal", status_code=201, summary="Record a Cal webhook")
def receive_cal_webhook(
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(default=None),
    actor: str | None = Query(default=None),
    engine: FollowupEngine = EngineDep,
) -> dict[str, Any]:
    """One of the four researched Cal webhooks.

    A recording or transcription link is recorded and not fetched. The token in the
    link expires, so a stored link is evidence that a recording existed and nothing
    more; pretending to have fetched it would produce a link that fails the first
    time a rep clicked it.
    """
    return engine.receive_webhook(
        payload, room_id=room_id, actor=actor, source=_source("POST", "/webhooks/cal")
    )


@router.get("/webhooks", summary="Every recorded webhook")
def list_webhooks(engine: FollowupEngine = EngineDep) -> dict[str, Any]:
    """Every recorded webhook, with the formats its transcription arrived in."""
    rows = engine.webhooks()
    return {
        "count": len(rows),
        "events": list(vocab.CAL_WEBHOOK_EVENTS),
        "transcription_formats": list(vocab.TRANSCRIPTION_FORMATS),
        "webhooks": [row.get("data") or row for row in rows],
    }


@router.post("/cal-workflows/validate", summary="Check a Cal workflow against the published enums")
def validate_cal_workflow(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    """Validate an inbound Cal workflow.

    A workflow this product cannot run is not an error: it is a workflow for Cal,
    and Cal is where it belongs. So the answer lists what would not survive and
    separates the blocking problems from the ones this product does not act on.
    """
    return cal.validate_workflow(payload)


# --------------------------------------------------------------------------- #
# Gong Forecast reminders
# --------------------------------------------------------------------------- #


@router.get("/reminders", summary="The Forecast reminder timings, and which are sent")
def list_reminders(engine: FollowupEngine = EngineDep) -> dict[str, Any]:
    """The three researched timings, and the reminders this build holds.

    Quoted: "in-app 24h before due, Slack 1h before to AEs, Slack summary 2h after to
    managers". The in-app one is configured. The two Slack rows are refused at
    creation, because the product has no confirmed Slack surface, and they are
    listed here so the rule is visible rather than silently absent.
    """
    return {
        "researched": reminders.served(),
        "implemented_channels": list(vocab.FORECAST_IMPLEMENTED_CHANNELS),
        "not_implemented": [
            entry["id"] for entry in reminders.served() if not entry["implemented"]
        ],
        "configured": engine.reminder_rows(),
    }


@router.post("/reminders", status_code=201, summary="Configure a Forecast reminder")
def add_reminder(
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(default=None),
    actor: str | None = Query(default=None),
    engine: FollowupEngine = EngineDep,
) -> dict[str, Any]:
    """Configure one in-app reminder.

    The two Slack rows are refused rather than stored. A reminder that posts nowhere
    is a rule an operator will trust in and cannot verify, and the message names the
    researched timing it would have carried.
    """
    return engine.add_reminder(
        payload, room_id=room_id, actor=actor, source=_source("POST", "/reminders")
    )


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #
#
# Every row is produced by calling the real FollowupEngine, so the demo cannot show
# a shape, a state or an audit row the HTTP routes would not produce. `source="seed"`
# rather than a route string: no route served this, and claiming one would be the lie
# the audit rule exists to prevent.
#
# The demo reaches every researched status, both refusals, all three error codes and
# both halves of the batch split, because a demo of the happy path alone teaches a
# reviewer nothing about a workflow whose most quoted rules are its refusals.

#: The participants, the sections and the activities one demo meeting carries.
DEMO_PARTICIPANTS: tuple[dict[str, Any], ...] = (
    {
        "email": "priya.raman@northwind.example",
        "name": "Priya Raman",
        "role": "buyer",
        "activity": [
            {
                "kind": "calls",
                "summary": "Discovery call, 32 minutes",
                "at": "2026-09-29T14:00:00Z",
            },
            {
                "kind": "emails",
                "summary": "Replied to the pricing question",
                "at": "2026-10-02T09:12:00Z",
            },
        ],
    },
    {
        "email": "marcus.webb@northwind.example",
        "name": "Marcus Webb",
        "role": "buyer",
        "activity": [
            {
                "kind": "digital_interactions",
                "summary": "Viewed the security overview",
                "at": "2026-10-06T11:40:00Z",
            },
            {
                "kind": "crm_changes",
                "summary": "Stage moved to Negotiation",
                "at": "2026-10-07T08:05:00Z",
            },
        ],
    },
    {
        "email": "alba.ries@northwind.example",
        "name": "Alba Ries",
        "role": "internal",
        "activity": [
            {"kind": "texts", "summary": "Asked for the renewal date", "at": "2026-10-08T16:20:00Z"}
        ],
    },
)

DEMO_SUMMARY: dict[str, Any] = {
    "objectives": [
        "Agree the renewal timeline",
        "Confirm the security review is unblocked",
    ],
    "key_topics": ["Renewal pricing", "SSO rollout", "Data residency"],
    "prior_discussion_highlights": [
        "Northwind asked for SSO in the 2026-08 call and again on 2026-09-12.",
    ],
    "action_items_for_you": [
        "Send the data residency questionnaire before the call",
        "Bring the revised seat count",
    ],
    "suggested_questions": [
        "Who signs off on the residency questionnaire?",
        "Does the security review gate the renewal date?",
    ],
    "reminders": ["Bring the revised seat count", "Confirm the call recording consent"],
}


def seed(db: AuditedDatabase, context: dict[str, Any]) -> str:
    """Seed the sellers, the CRM records, the flows, the automations and the states.

    The order matters and is the point of the demo. The sellers must exist before
    an automation can name a licensed assignee. The CRM records must exist before
    an automation can react to one. The flows must exist before the automations and
    before the assignment, and the licensed seller must exist before the flow.

    Nine states are seeded, and every one of them exists because a researched rule
    makes it reachable:

    * a **prep page** with all six sections and a recent-activity feed across all
      five researched kinds, plus a **dry run** and an **Ask anything** answer;
    * a **date-triggered automation still awaiting its first morning**, which is the
      researched rule "They do not run immediately when an automation is created or
      edited" made visible as a state rather than a comment;
    * a **running instance** whose owner has a five-touch queue, so the researched
      "1/5" position has something to count;
    * a **paused instance**, the third researched status;
    * an assignment whose batch **split**: one contact with no parent account comes
      back as ``InvalidArgument`` while the rest are assigned, which is the
      researched per-prospect failure;
    * an assignment refused because the **assignee has no Engage licence**, which is
      the first researched permission gate;
    * a **CRM sync run** carrying the mandatory ``modificationDate`` and the exact
      request the Gong endpoint would receive, marked not sent;
    * a **Cal recording and transcription webhook**, with the four researched
      transcription formats;
    * a **tracker detection** that fires an automation.

    Every character below is encodable by cp1252. That is a hard requirement rather
    than a style preference: the seeder prints this string to a Windows console, and
    one RIGHTWARDS ARROW in a recovered feature's return string broke the entire
    seeder. ``tests/test_wf068_http.py`` asserts it, character by character.
    """
    store = RecordStore(db)
    now: Any = context["now"]
    room_ids: list[tuple[str, str]] = list(context.get("room_ids") or [])
    if not room_ids:
        return ""
    room_id = room_ids[0][0]
    source = "seed"

    engine = FollowupEngine(store, clock=lambda: now)

    # -- the sellers, and the researched licence split --------------------- #

    licensed = engine.add_user(
        {
            "email": "dana@northwind.example",
            "name": "Dana Okafor",
            vocab.ENGAGE_LICENSE_FIELD: True,
        },
        room_id=room_id,
        actor="dana",
        source=source,
    )
    del licensed
    # The first researched permission gate, made reachable: this seller has no
    # Engage licence, so "the automation won't run" is a state a reviewer can see.
    engine.add_user(
        {
            "email": "sam@northwind.example",
            "name": "Sam Rivera",
            vocab.ENGAGE_LICENSE_FIELD: False,
        },
        room_id=room_id,
        actor="dana",
        source=source,
    )

    # -- the CRM records ----------------------------------------------------- #

    engine.upsert_crm_record(
        "ACCOUNT",
        {"crm_id": "acc-1001", "name": "Northwind Traders", "operation": "create"},
        room_id=room_id,
        actor="dana",
        source=source,
    )
    engine.upsert_crm_record(
        "CONTACT",
        {
            "crm_id": "con-2001",
            "name": "Priya Raman",
            vocab.PARENT_ACCOUNT_FIELD: "acc-1001",
            "operation": "create",
        },
        room_id=room_id,
        actor="dana",
        source=source,
    )
    # The second researched permission gate, made reachable: no parent account.
    engine.upsert_crm_record(
        "CONTACT",
        {"crm_id": "con-2002", "name": "Marcus Webb", "operation": "create"},
        room_id=room_id,
        actor="dana",
        source=source,
    )
    engine.upsert_crm_record(
        "DEAL",
        {
            "crm_id": "deal-3001",
            "name": "Northwind renewal",
            "operation": "update",
            vocab.MODIFICATION_DATE_FIELD: "2026-10-08T00:00:00Z",
            "fields": {"stage": "Negotiation", "close_date": "2026-11-30"},
        },
        room_id=room_id,
        actor="dana",
        source=source,
    )
    engine.upsert_crm_record(
        "LEAD",
        {"crm_id": "lead-4001", "name": "Contoso evaluation", "operation": "create"},
        room_id=room_id,
        actor="dana",
        source=source,
    )

    # -- the flows ------------------------------------------------------------ #

    engine.create_flow(
        {
            "flow_id": "seed-wf068-renewal",
            "name": "Renewal follow-up, five touches",
            "description": "Sent after the renewal call closes.",
            "steps": [
                {"kind": "task", "subject": "Book the renewal call", "offset_days": 1},
                {"kind": "email", "subject": "Send the summary", "offset_days": 2},
                {
                    "kind": "call",
                    "subject": "Confirm the residency questionnaire",
                    "offset_days": 5,
                },
                {"kind": "email", "subject": "Nudge on the seat count", "offset_days": 9},
                {"kind": "task", "subject": "Close out or re-plan", "offset_days": 14},
            ],
        },
        room_id=room_id,
        actor="dana",
        source=source,
    )
    engine.create_flow(
        {
            "flow_id": "seed-wf068-short",
            "name": "Short follow-up, two touches",
            "steps": [
                # Offset zero on purpose: a first touch due today has started, so
                # an instance enrolled in this flow is Running rather than Pending.
                # Without it the demo would show two of the three researched
                # statuses and leave the third unreachable.
                {"kind": "email", "subject": "Recap", "offset_days": 0},
                {"kind": "task", "subject": "Confirm next step", "offset_days": 4},
            ],
        },
        room_id=room_id,
        actor="dana",
        source=source,
    )
    # The researched "Personal flows cannot be used with automations", made
    # reachable as a row.
    engine.create_flow(
        {
            "flow_id": "seed-wf068-personal",
            "name": "Dana's own sequence",
            vocab.PERSONAL_FLOW_FIELD: True,
            "steps": [{"kind": "task", "subject": "Personal follow-up", "offset_days": 3}],
        },
        room_id=room_id,
        actor="dana",
        source=source,
    )

    # -- the automations ------------------------------------------------------ #

    engine.create_automation(
        {
            "name": "Enrol a contact created in the CRM",
            "flow_id": "seed-wf068-renewal",
            "assignee_email": "dana@northwind.example",
            "trigger": {"kind": vocab.CRM_CREATED, "object_type": "CONTACT"},
        },
        room_id=room_id,
        actor="dana",
        source=source,
    )
    engine.create_automation(
        {
            "name": "Enrol a deal once it reaches Negotiation",
            "flow_id": "seed-wf068-short",
            "assignee_email": "dana@northwind.example",
            "trigger": {
                "kind": vocab.CRM_UPDATED_WITH,
                "object_type": "DEAL",
                "conditions": [{"field": "stage", "operator": "equals", "value": "Negotiation"}],
            },
        },
        room_id=room_id,
        actor="dana",
        source=source,
    )
    # The researched daily schedule, as a state: created, and not run.
    date_automation = engine.create_automation(
        {
            "name": "Renewal reminder 30 days before the close date",
            "flow_id": "seed-wf068-short",
            "assignee_email": "dana@northwind.example",
            "trigger": {
                "kind": vocab.CURRENT_DATE_IS,
                "object_type": "DEAL",
                "date_field": "close_date",
                "offset_days": -30,
            },
        },
        room_id=room_id,
        actor="dana",
        source=source,
    )
    engine.create_automation(
        {
            "name": "Enrol when pricing was mentioned on a call",
            "flow_id": "seed-wf068-short",
            "assignee_email": "dana@northwind.example",
            "trigger": {"kind": vocab.TRACKER_DETECTED, "tracker_name": "Pricing Objection"},
        },
        room_id=room_id,
        actor="dana",
        source=source,
    )

    # -- the two researched refusals ----------------------------------------- #

    gate_one_refused = False
    try:
        engine.create_automation(
            {
                "name": "Enrol for an unlicensed assignee",
                "flow_id": "seed-wf068-short",
                "assignee_email": "sam@northwind.example",
                "trigger": {"kind": vocab.CRM_CREATED, "object_type": "LEAD"},
            },
            room_id=room_id,
            actor="dana",
            source=source,
        )
    except AssigneeNotLicensed:
        gate_one_refused = True

    # The second researched gate is a *per-prospect* refusal, not a request-level
    # one, so it comes back inside the split answer rather than as an exception.
    # Reaching for the exception would have got this backwards: the research says
    # failures are returned per prospect rather than failing the batch, so a
    # parentless contact must not stop the others in the batch.
    gate_two_refused = False
    gate_two = engine.enrol(
        {
            "crmProspectsIds": ["con-2002"],
            "flowId": "seed-wf068-short",
            "flowInstanceOwnerEmail": "dana@northwind.example",
        },
        prospect=engine.find_crm_record("con-2002"),
        room_id=room_id,
        actor="dana",
        source=source,
    )
    gate_two_refused = any(
        entry["crmProspectId"] == "con-2002" for entry in gate_two["prospectsNotAssigned"]
    )

    personal_refused = False
    try:
        engine.enrol(
            {
                "crmProspectsIds": ["con-2001"],
                "flowId": "seed-wf068-personal",
                "flowInstanceOwnerEmail": "dana@northwind.example",
            },
            prospect=engine.find_crm_record("con-2001"),
            room_id=room_id,
            actor="dana",
            source=source,
        )
    except PersonalFlowRefused:
        personal_refused = True

    # -- the prep page, the dry run and Ask anything ------------------------- #

    engine.create_prep_page(
        {
            "meeting_id": "seed-wf068-meeting",
            "title": "Northwind renewal",
            "starts_at": "2026-10-14T14:00:00Z",
            "objective": "Agree the renewal timeline",
            "summary": dict(DEMO_SUMMARY),
            "participants": [dict(p) for p in DEMO_PARTICIPANTS],
            "entry_point": vocab.PREP_ENTRY_POINTS[0],
        },
        room_id=room_id,
        actor="dana",
        source=source,
    )
    engine.dry_run(
        {
            "meeting_id": "seed-wf068-meeting",
            "voice": vocab.DRY_RUN_VOICES[0],
            "covered": DEMO_SUMMARY["objectives"][:1],
        },
        actor="dana",
        source=source,
    )
    engine.ask(
        "seed-wf068-meeting",
        "what did we agree about the residency questionnaire?",
        actor="dana",
        source=source,
    )

    # -- the assignment that splits ------------------------------------------ #

    split = engine.enrol(
        {
            "crmProspectsIds": ["con-2001", "con-2002", "lead-4001"],
            "flowId": "seed-wf068-renewal",
            "flowInstanceOwnerEmail": "dana@northwind.example",
        },
        room_id=room_id,
        actor="dana",
        source=source,
    )

    # -- a paused instance: the third researched status ---------------------- #

    running_ids = [entry["flowInstanceId"] for entry in split["prospectsAssigned"]]
    if running_ids:
        engine.set_instance_status(running_ids[1], vocab.STATUS_PAUSED, actor="dana", source=source)

    # -- the CRM sync run ----------------------------------------------------- #

    engine.sync_crm(
        {
            "operation": "update",
            "cadence_minutes": vocab.CRM_SYNC_MAX_MINUTES,
            "object_type": "DEAL",
            vocab.MODIFICATION_DATE_FIELD: "2026-10-08T00:00:00Z",
            "records": [
                {
                    "crm_id": "deal-3001",
                    "name": "Northwind renewal",
                    "operation": "update",
                    vocab.MODIFICATION_DATE_FIELD: "2026-10-08T00:00:00Z",
                    "fields": {"stage": "Negotiation"},
                }
            ],
        },
        room_id=room_id,
        actor="dana",
        source=source,
    )

    # -- the Cal webhooks ----------------------------------------------------- #

    engine.receive_webhook(
        {
            "event": vocab.WEBHOOK_RECORDING_READY,
            "bookingUid": "bk-9001",
            "payload": {
                "bookingUid": "bk-9001",
                "downloadLink": "https://app.cal.com/api/video/recording?token=demo",
            },
        },
        room_id=room_id,
        actor="cal",
        source=source,
    )
    engine.receive_webhook(
        {
            "event": vocab.WEBHOOK_TRANSCRIPTION_GENERATED,
            "bookingUid": "bk-9001",
            "payload": {
                "bookingUid": "bk-9001",
                "downloadLinks": {
                    "transcription": [
                        {"format": fmt, "link": f"https://app.cal.com/transcript.{fmt}"}
                        for fmt in vocab.TRANSCRIPTION_FORMATS
                    ]
                },
            },
        },
        room_id=room_id,
        actor="cal",
        source=source,
    )

    # -- a tracker detection that fires an automation ------------------------ #

    detection = engine.record_tracker(
        {
            "tracker_name": "Pricing Objection",
            "meeting_id": "seed-wf068-meeting",
            "concept": "price increase",
            "crm_id": "con-2001",
        },
        room_id=room_id,
        actor="gong",
        source=source,
    )

    # -- the in-app Forecast reminder, and the refused Slack row ------------- #

    engine.add_reminder(
        {"channel": "in_app", "due_at": "2026-11-30T00:00:00Z"},
        room_id=room_id,
        actor="dana",
        source=source,
    )
    slack_refused = False
    try:
        engine.add_reminder(
            {"channel": "slack", "due_at": "2026-11-30T00:00:00Z"},
            room_id=room_id,
            actor="dana",
            source=source,
        )
    except SlackChannelRefused:
        slack_refused = True

    # -- the summary line ----------------------------------------------------- #
    #
    # Read back from the store rather than written by hand, so the line cannot
    # describe a state the seed did not produce.
    counts = engine.summary(room_id=room_id)
    statuses = counts["instances_by_status"]
    # Read back, not counted by hand: the queue grouping is what the researched
    # quote describes, so a line that said "1 queue" while the store held three
    # would misdescribe the very rule the demo exists to show.
    queue_count = len(
        {str((row.get("data") or {}).get("queue_id")) for row in engine.todos(limit=1000)}
    )
    parts = [
        f"{counts['prep_pages']} prep page with 1 dry run and 1 Ask anything answer",
        f"{counts['flows']} flows, {counts['automations']} automations "
        f"({counts['automations_awaiting_morning_run']} awaiting the next morning run)",
        f"{counts['instances']} flow instances: "
        f"{statuses.get('Running', 0)} Running, "
        f"{statuses.get('Pending', 0)} Pending, "
        f"{statuses.get('Paused', 0)} Paused",
        f"{counts['todos']} to-dos in {queue_count} grouped queues, "
        f"{counts['todos_open']} still open",
        f"{counts['assignments']} assignments with {counts['assignment_failures']} "
        "per-prospect failure",
        f"{len(split['prospectsNotAssigned'])} prospects refused with "
        f"{sorted({f['errorCode'] for f in split['prospectsNotAssigned']}) or ['none']}",
        f"{counts['webhooks']} Cal webhooks, "
        f"{len(vocab.TRANSCRIPTION_FORMATS)} transcription formats recorded",
        f"{counts['crm_records']['DEAL']} deal sync run at "
        f"{vocab.CRM_SYNC_MAX_MINUTES} minute cadence, not sent",
        f"1 tracker detection fired {len(detection['evaluated']['enrolled'])} assignment(s)",
        "statuses present: "
        + ", ".join(f"{name} {statuses.get(name, 0)}" for name in vocab.FLOW_INSTANCE_STATUSES),
    ]
    if gate_one_refused:
        parts.append("1 automation refused: the assignee has no Engage licence")
    if gate_two_refused:
        parts.append("1 contact refused: it has no parent account")
    if personal_refused:
        parts.append("1 personal flow refused by an assignment")
    if slack_refused:
        parts.append("1 Slack reminder refused: no Slack surface in this product")
    if not rules.created_or_edited_at_was_not_evaluated(date_automation.get("data") or {}):
        parts.append("NOT reached: a date automation awaiting its first morning run")
    return "; ".join(parts)
