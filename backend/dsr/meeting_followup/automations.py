"""The When / If / Then rule evaluation, and the daily date-trigger schedule.

The evidence gives the triggers exactly:

    Flow automations automatically add contacts and leads to flows when predefined
    conditions are met. Triggers can be based on CRM updates, scheduled dates, or
    AI AI tracker detections.

    Common use cases include: Starting onboarding flows when an opportunity is
    marked Closed Won. Triggering renewal flows before a renewal date. Adding
    contacts to follow-up flows when specific concepts are detected on calls.

    (Opportunity only) current date is

One quoted line constrains the schedule and is a test case in its own right:

    Date-based triggers run on a daily schedule and are evaluated each morning.
    They do not run immediately when an automation is created or edited.

:func:`evaluate` is therefore split in two. :func:`matches` decides whether one
event fires one automation, and :func:`due_on_morning_run` decides which
date-triggered automations are due on a given morning. Nothing calls the first
from the second, which is what makes "they do not run immediately when an
automation is created or edited" a property of the shape rather than a comment.

The typo in the trigger quote is reproduced in
:data:`~dsr.meeting_followup.vocabulary.TRACKER_QUOTE_WITH_TYPO` rather than
corrected, because the issue asks for a quote as written.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Mapping

from dsr.meeting_followup import crm, vocabulary as vocab
from dsr.meeting_followup.errors import (
    AssigneeNotLicensed,
    AutomationInvalid,
    DateTriggerObjectRefused,
)

# --------------------------------------------------------------------------- #
# Comparison
# --------------------------------------------------------------------------- #


def _as_text(value: Any) -> str:
    """A comparison value as the lower-case text both sides are compared as."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value).strip().lower()


def compare(observed: Any, operator: str, expected: Any) -> bool:
    """Evaluate one field comparison.

    Operators are a closed set, served by the vocabulary route. An unknown
    operator is False rather than an exception: a condition that cannot be
    evaluated must not fire a follow-up sequence, because firing one sends email
    to a buyer.
    """
    op = str(operator or "").strip().lower()
    if op == "equals":
        return _as_text(observed) == _as_text(expected)
    if op == "not_equals":
        return _as_text(observed) != _as_text(expected)
    if op == "contains":
        return _as_text(expected) in _as_text(observed)
    if op == "greater_than":
        try:
            return float(observed) > float(expected)
        except (TypeError, ValueError):
            return False
    if op == "less_than":
        try:
            return float(observed) < float(expected)
        except (TypeError, ValueError):
            return False
    if op == "is_true":
        return _as_text(observed) == "true"
    if op == "is_false":
        return _as_text(observed) == "false"
    return False


def _match_all(conditions: list[Mapping[str, Any]], event: Mapping[str, Any]) -> bool:
    """Every condition must hold.

    "predefined conditions are met" reads as a conjunction. Derived: an empty
    list matches, because "is created" with no further condition is the simplest
    automation the evidence names.
    """
    return all(
        compare(event.get(str(c.get("field") or "")), str(c.get("operator") or ""), c.get("value"))
        for c in conditions
    )


def _match_any(conditions: list[Mapping[str, Any]], event: Mapping[str, Any]) -> bool:
    """At least one condition must hold.

    Only reachable when the automation asks for it, so an automation never fires
    on a disjunction the seller did not choose.
    """
    if not conditions:
        return False
    return any(
        compare(event.get(str(c.get("field") or "")), str(c.get("operator") or ""), c.get("value"))
        for c in conditions
    )


def conditions_hold(
    conditions: list[Mapping[str, Any]],
    event: Mapping[str, Any],
    match: str = "all",
) -> bool:
    """Whether an automation's If-clause holds for one event.

    ``match`` is ``all`` unless the automation says ``any``. It is a parameter
    rather than a field read off the event, because it belongs to the automation
    and an event must not be able to relax the seller's own rule.
    """
    mode = str(match or "all").strip().lower()
    if mode == "any":
        return _match_any(conditions, event)
    return _match_all(conditions, event)


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #


def validate_trigger(trigger: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a When-clause into a normalised dict.

    ``object_type`` is optional for the three CRM triggers, because
    "Contact is created" names its object and "an AI tracker detects a concept on a
    call" names none. It is required for ``current_date_is``, which the evidence
    restricts to Opportunity.
    """
    kind = str(trigger.get("kind") or "").strip()
    if kind not in vocab.TRIGGERS:
        raise AutomationInvalid(
            f"trigger kind {kind!r} is not one of the researched triggers",
            {"trigger.kind": f"one of: {', '.join(vocab.TRIGGERS)}"},
        )

    out: dict[str, Any] = {"kind": kind}
    if kind in vocab.CRM_TRIGGERS:
        raw_object = str(trigger.get("object_type") or "").strip()
        if raw_object:
            out["object_type"] = crm.normalise_object_type(raw_object)
        if kind == vocab.CRM_UPDATED_WITH and not trigger.get("conditions"):
            raise AutomationInvalid(
                "'is updated with' needs at least one condition, or it matches every update",
                {"trigger.conditions": "at least one condition"},
            )
    if kind == vocab.CURRENT_DATE_IS:
        raw_object = str(trigger.get("object_type") or "").strip()
        if not raw_object:
            raise AutomationInvalid(
                "'current date is' names an object type",
                {"trigger.object_type": "required"},
            )
        object_type = crm.normalise_object_type(raw_object)
        if object_type != vocab.DATE_TRIGGER_OBJECT_TYPE:
            raise DateTriggerObjectRefused(
                f"'current date is' is available for Opportunity only; {object_type} was given",
                {"trigger.object_type": "DEAL"},
            )
        out["object_type"] = object_type
        out["date_field"] = str(trigger.get("date_field") or "close_date").strip()
        out["offset_days"] = int(trigger.get("offset_days") or 0)
    if kind == vocab.TRACKER_DETECTED:
        out["tracker_name"] = str(trigger.get("tracker_name") or "").strip()
        if not out["tracker_name"]:
            raise AutomationInvalid(
                "an AI tracker trigger names the tracker",
                {"trigger.tracker_name": "required"},
            )

    conditions = trigger.get("conditions") or []
    if not isinstance(conditions, list):
        raise AutomationInvalid(
            "conditions must be a list",
            {"trigger.conditions": "a list of field comparisons"},
        )
    out["conditions"] = [dict(item) for item in conditions]
    return out


def validate_automation(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a whole automation into a normalised dict.

    The researched rules that depend on other records are checked by the engine,
    which is the only module that reads the store. This validates the shape.
    """
    name = str(payload.get("name") or "").strip()
    if not name:
        raise AutomationInvalid("name is required", {"name": "required"})

    flow_id = str(payload.get("flow_id") or "").strip()
    if not flow_id:
        raise AutomationInvalid(
            "flow_id is required: an automation assigns to a flow",
            {"flow_id": "required"},
        )

    match = str(payload.get("match") or "all").strip().lower()
    if match not in ("all", "any"):
        raise AutomationInvalid(
            f"match {match!r} is not all or any",
            {"match": "all or any"},
        )

    trigger = validate_trigger(payload.get("trigger") or {})
    return {
        "name": name,
        "description": str(payload.get("description") or "").strip(),
        "flow_id": flow_id,
        "assignee_email": str(payload.get("assignee_email") or "").strip(),
        "trigger": trigger,
        "match": match,
        "enabled": bool(payload.get("enabled", True)),
        "run_count": 0,
        # The researched schedule rule, made a field rather than a comment: an
        # automation that has never been evaluated carries None here.
        "last_evaluated_at": None,
        "pending_date_evaluation": trigger["kind"] == vocab.CURRENT_DATE_IS,
    }


# --------------------------------------------------------------------------- #
# The When-clause
# --------------------------------------------------------------------------- #


def trigger_matches(
    trigger: Mapping[str, Any],
    event: Mapping[str, Any],
    *,
    today: date | None = None,
) -> bool:
    """Whether an event fires this automation's When-clause.

    The five cases are separate rather than one expression, because they differ on
    the dimensions that matter: three CRM triggers differ on whether an update
    event counts, the date trigger needs the calendar, and the tracker trigger
    matches on the tracker's name rather than on a field.
    """
    kind = str(trigger.get("kind") or "")
    event_kind = str(event.get("kind") or "")
    event_object = str(event.get("object_type") or "").strip().upper()
    wanted_object = str(trigger.get("object_type") or "").strip().upper()

    if kind in vocab.CRM_TRIGGERS:
        if event_kind != "crm_entity":
            return False
        operation = str(event.get("operation") or "")
        if kind == vocab.CRM_CREATED and operation != "create":
            return False
        if kind == vocab.CRM_UPDATED_WITH and operation != "update":
            return False
        if wanted_object and event_object and event_object != wanted_object:
            return False
        return True

    if kind == vocab.CURRENT_DATE_IS:
        # Deliberately never true here. A date trigger is evaluated by
        # due_on_morning_run and by nothing else, because the research says it
        # "does not run immediately".
        return False

    if kind == vocab.TRACKER_DETECTED:
        if event_kind != vocab.TRACKER_DETECTED:
            return False
        if str(event.get("tracker_name") or "") != str(trigger.get("tracker_name") or ""):
            return False
        return True

    return False


def evaluate(
    automation: Mapping[str, Any],
    event: Mapping[str, Any],
) -> dict[str, Any]:
    """Evaluate one event against one automation.

    Returns a decision record rather than a bool, because a caller needs to tell
    "disabled" from "wrong object type" from "the condition did not hold". Every
    refusal reason is named, and the researched gates are applied here so no route
    can evaluate an automation without them.
    """
    reason_codes: list[str] = []
    if not automation.get("enabled", True):
        return _decision(automation, event, False, ["automation_disabled"])
    if event.get("kind") == vocab.TRACKER_DETECTED and str(event.get("tracker_name") or "") == "":
        return _decision(automation, event, False, ["tracker_name_missing"])

    if not trigger_matches(automation.get("trigger") or {}, event):
        return _decision(automation, event, False, ["trigger_not_matched"])

    conditions = list((automation.get("trigger") or {}).get("conditions") or [])
    if not conditions_hold(conditions, event, str(automation.get("match") or "all")):
        return _decision(automation, event, False, ["condition_not_met"])

    reason_codes.append("matched")
    return _decision(automation, event, True, reason_codes)


def _decision(
    automation: Mapping[str, Any],
    event: Mapping[str, Any],
    fires: bool,
    reasons: list[str],
) -> dict[str, Any]:
    return {
        "automation_id": automation.get("id"),
        "name": automation.get("name"),
        "fires": fires,
        "reasons": reasons,
        "flow_id": automation.get("flow_id"),
        "assignee_email": automation.get("assignee_email"),
        "trigger": automation.get("trigger"),
    }


# --------------------------------------------------------------------------- #
# The daily date schedule
# --------------------------------------------------------------------------- #


def due_on_morning_run(
    automation: Mapping[str, Any],
    today: date,
    deals: list[Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """The deals a date-triggered automation enrols on one morning.

    ``today`` is injected rather than read from the clock. Every rule here is a
    statement about a calendar date, so the calendar has to be the test's: a test
    that depended on the machine's date would pass today and fail in a month.
    """
    trigger = automation.get("trigger") or {}
    if trigger.get("kind") != vocab.CURRENT_DATE_IS:
        return []
    if not automation.get("enabled", True):
        return []

    date_field = str(trigger.get("date_field") or "close_date")
    offset = int(trigger.get("offset_days") or 0)
    # The researched examples read "start a flow 20 days before a renewal date"
    # and "30 days after a contract is signed", so the field value carries the
    # offset: a -30 offset on a 2026-11-30 close date is due on 2026-10-31.
    target = today - timedelta(days=offset)
    out: list[dict[str, Any]] = []
    for deal in deals or []:
        raw = str((deal.get("fields") or {}).get(date_field) or deal.get(date_field) or "")
        if not raw:
            continue
        if _as_date(raw) == target:
            out.append(dict(deal))
    return out


def _as_date(raw: str) -> date | None:
    """A date field read as a calendar date.

    Accepts an ISO date and a full ISO timestamp, and returns ``None`` for
    anything else. A malformed date must not enrol a buyer, so the comparison
    treats it as absent rather than raising.
    """
    text = raw.strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def morning_run_time(today: date) -> datetime:
    """The instant the daily schedule evaluates on one morning.

    Derived: the research says "evaluated each morning" and names no time, so the
    first minute of the morning window is used and the value is served.
    """
    return datetime(
        today.year,
        today.month,
        today.day,
        vocab.DATE_TRIGGER_EVALUATION_HOUR,
        vocab.DATE_TRIGGER_EVALUATION_MINUTE,
    )


def created_or_edited_at_was_not_evaluated(automation: Mapping[str, Any]) -> bool:
    """Whether an automation is still waiting for its first morning.

    Always true after a create or an edit on a date-triggered automation, which is
    the researched rule stated as a function rather than a comment.
    """
    if not vocab.DATE_TRIGGER_NOT_EVALUATED_ON_EDIT:
        return False
    trigger = automation.get("trigger") or {}
    return trigger.get("kind") == vocab.CURRENT_DATE_IS and not automation.get("last_evaluated_at")


def refuse_without_gates(
    automation: Mapping[str, Any],
    assignee: Mapping[str, Any] | None,
    prospect: Mapping[str, Any] | None,
) -> None:
    """Apply both researched permission gates and raise on either.

    Kept as one function so no caller can apply one gate and forget the other. The
    assignee is checked first because it is a property of the automation rather
    than of the prospect.
    """
    try:
        crm.assert_engaged(assignee)
    except AssigneeNotLicensed:
        raise
    if prospect is not None:
        crm.assert_parent_account(prospect)
