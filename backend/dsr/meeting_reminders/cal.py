"""The same reminder, as a Cal.com Workflow.

``user_flow`` step 8: "For a portable implementation, the same logic is modelled
as a Cal.com **Workflow**: trigger + ordered steps with templates." Step 8 is
not decoration - the research's ``apis_hit`` section quotes the create-workflow
DTO and four enums, and a team already on Cal.com should be able to move this
configuration across without retyping it.

What is projected, and what is not.

**The trigger.** The three researched conditions map onto two Cal triggers,
because Cal has no response-conditional pre-meeting trigger. The two "Before"
conditions both become ``beforeEvent``; "After the Meeting" becomes
``afterEvent``. The response gate is *not* lost - it becomes a ``filter`` step
in front of the action, which is the right place for it and is exactly what
Cal's own step kinds are for.

**The offset.** Cal's unit enum is ``hour|minute|day`` and this product's is
minutes/hours/days/**weeks**. A weeks offset therefore has no direct Cal
equivalent, and the conversion is exact: one week is seven days. That is a
conversion this build performs rather than a gap it declares, and it is named in
the inferences registry because a reviewer should be able to disagree.

**The action.** One step, addressed to the attendee: ``email_attendee`` or
``sms_attendee``. A reminder is never addressed to the host, so Cal's
``email_host`` and the two ``whatsapp_*`` and ``cal_ai_phone_call`` actions are
published but unreachable from this workflow. Publishing them matters: the
research says a deployment may add channels as workflow steps, and a client
rendering the vocabulary should see what Cal offers rather than what this
particular workflow uses.

**The template.** Cal's ``template`` enum is
``reminder, custom, rescheduled, completed, rating, cancelled``. A pre-meeting
reminder using the default copy is ``reminder``; anything the administrator
composed is ``custom``.

**Not projected.** ``paths``, ``delay`` and ``lead_enrichment`` steps, and the
four triggers this workflow has no equivalent of (``newEvent``,
``eventCancelled``, the no-show pair, the booking-state four). Cal models them;
this product's research does not, and inventing a cancellation path would be
building a workflow nobody asked for.

The projection is a *read*: it returns the DTO a ``POST /v2/workflows`` would
carry, with the required ``cal-api-version`` header, and it opens no socket. The
research documents the endpoint and the header; it documents no credential this
product could use, and calling a real Cal instance from a demo is a claim the
product cannot back. Everything above this module is the researched part, so a
real transport is a change to one function.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Mapping

from dsr.meeting_reminders import conditions
from dsr.meeting_reminders import vocabulary as vocab
from dsr.meeting_reminders.errors import ReminderError


def cal_offset(reminder: Mapping[str, Any]) -> dict[str, Any]:
    """Project this product's offset onto Cal's ``{value, unit}``.

    Cal's ``unit`` is singular (``hour``, not ``hours``) and has no week. A week
    becomes ``7 * value`` days, which is exact rather than approximate, so a
    "one week before" reminder survives the move with its meaning intact.
    """
    offset = vocab.require_offset(
        reminder.get("offset", {}).get("value", reminder.get("offsetValue", vocab.DEFAULT_OFFSET)),
        reminder.get("offset", {}).get("unit", reminder.get("offsetUnit", vocab.DEFAULT_UNIT)),
    )
    if offset["unit"] == vocab.WEEKS:
        return {"value": offset["value"] * vocab.CAL_DAYS_PER_WEEK, "unit": "day"}
    return {"value": offset["value"], "unit": vocab.CAL_UNIT_FROM[offset["unit"]]}


def _unit_count(unit: str, minutes: int) -> int:
    """How many whole ``unit``s cover ``minutes``, rounded **up**.

    Rounded up because rounding a follow-up down would project a workflow that
    fires *before* the one it was exported from, which is the worse of the two
    errors: an early follow-up lands while the meeting is still running.
    """
    size = {"minute": 1, "hour": 60, "day": 60 * 24}.get(unit)
    if size is None:
        raise ReminderError(f"cannot fold a duration into a {unit!r} offset")
    return -(-minutes // size)


def cal_trigger(reminder: Mapping[str, Any], booking: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Project the firing condition onto a Cal trigger.

    The offset's sign is carried by the *trigger*, not the number: Cal's
    ``beforeEvent`` offset is how far before, and ``afterEvent`` offset is how
    far after. So both are positive, and the anchor is what differs.

    Which is why the booking is optional. This product anchors ``after_meeting``
    on the meeting **end** - a follow-up follows the meeting, not its first
    minute - while Cal's ``afterEvent`` fires relative to the event. Supply a
    booking and the duration is folded into the offset, so the projected workflow
    fires at the same *instant*; omit it and the offset is projected as authored,
    which is the honest answer for a reusable asset not yet attached to a
    specific booking.
    """
    condition = vocab.require_condition(reminder.get("condition"))
    offset = cal_offset(reminder)
    if condition != vocab.AFTER:
        return {"type": vocab.CAL_TRIGGER_BEFORE, "offset": offset}
    if booking is not None:
        raw = (booking or {}).get("durationMinutes")
        try:
            minutes = int(raw) if raw not in (None, "") else 0
        except (TypeError, ValueError):
            minutes = 0
        if minutes:
            offset = {
                "value": offset["value"] + _unit_count(offset["unit"], minutes),
                "unit": offset["unit"],
            }
    return {"type": vocab.CAL_TRIGGER_AFTER, "offset": offset}


def cal_filter_steps(reminder: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The gates, as Cal ``filter`` steps.

    Cal's trigger cannot express "only if the primary guest has not responded",
    so the response gate becomes a filter in front of the action. The two step-6
    gates become filters too, which is what they are.

    Returned before the action, in the order :func:`conditions.decide` evaluates
    them, so a projected workflow and this product refuse the same bookings for
    the same reasons.
    """
    steps: list[dict[str, Any]] = []

    if vocab.require_condition(reminder.get("condition")) == vocab.BEFORE_IF_NO_RESPONSE:
        steps.append(
            {
                "id": len(steps),
                "type": "filter",
                "filter": {
                    "field": "responseStatus",
                    "operator": "not_in",
                    "value": sorted(vocab.RESPONSED_STATUSES),
                },
                "note": (
                    "Cal has no response-conditional pre-meeting trigger, so the research's "
                    '"only if the Primary Guest has not responded (Accepted or Declined)" becomes '
                    "a filter step."
                ),
            }
        )

    group = reminder.get("conditions") or {}
    for index, rule in enumerate(group.get("rules") or []):
        kind = str((rule or {}).get("kind") or (rule or {}).get("type") or "").strip().lower()
        if kind not in conditions.RULE_KINDS:
            raise ReminderError(
                f"rule {index} names kind {kind!r}; published kinds are {list(conditions.RULE_KINDS)}"
            )
        steps.append(
            {
                "id": len(steps),
                "type": "filter",
                "filter": _cal_filter_for(kind, rule),
                "note": "projects dsr.meeting_reminders.conditions.evaluate_rule",
            }
        )

    return steps


def _cal_filter_for(kind: str, rule: Mapping[str, Any]) -> dict[str, Any]:
    if kind == vocab.RULE_WEEKDAY:
        return {
            "field": "start",
            "operator": "weekday_in",
            "value": list(vocab.require_weekdays(rule.get("weekdays"))),
        }
    offset = vocab.require_offset(
        rule.get("value", vocab.DEFAULT_OFFSET), rule.get("unit", vocab.DEFAULT_UNIT)
    )
    return {
        "field": "bookedAt",
        "operator": "at_least_before_start_by",
        "value": {"minutes": offset["minutes"], "unit": offset["unit"]},
    }


def cal_template(reminder: Mapping[str, Any]) -> str:
    """The Cal step template this reminder uses.

    A pre-meeting reminder on default copy is Cal's own ``reminder``. Anything
    the administrator composed - a subject, a body, either - is ``custom``,
    because Cal's ``reminder`` template carries Cal's copy and this reminder
    does not. A post-meeting follow-up defaults to ``completed``, which is the
    template Cal publishes for the after-the-event case, but a composed one
    overrides it the same way.
    """
    explicit = str(reminder.get("calTemplate") or "").strip()
    if explicit:
        if explicit not in vocab.CAL_STEP_TEMPLATES:
            raise ReminderError(
                f"calTemplate must be one of {list(vocab.CAL_STEP_TEMPLATES)}; got {explicit!r}"
            )
        return explicit
    composed = bool(str(reminder.get("subject") or "").strip() or str(reminder.get("body") or "").strip())
    if composed:
        return "custom"
    return vocab.CAL_TEMPLATE_DEFAULT[vocab.require_condition(reminder.get("condition"))]


def cal_step(reminder: Mapping[str, Any]) -> dict[str, Any]:
    """The single action step, addressed to the attendee."""
    channel = vocab.require_channel(reminder.get("channel"))
    return {
        "id": 0,
        "type": "action",
        "action": vocab.CAL_ACTION_FROM_CHANNEL[channel],
        "template": cal_template(reminder),
        "emailSubject": str(reminder.get("subject") or ""),
        "emailBody": str(reminder.get("body") or ""),
        "includeCalendarEvent": vocab.include_calendar_event(reminder.get("includeCalendarEvent")),
        "autoTranslateEnabled": vocab.auto_translate_enabled(reminder.get("autoTranslateEnabled")),
        vocab.SOURCE_LOCALE: vocab.require_source_locale(reminder.get(vocab.SOURCE_LOCALE)),
    }


def activation(reminder: Mapping[str, Any], meeting_type_ids: list[str] | None = None) -> dict[str, Any]:
    """Cal's ``activation`` block, from the reminder's own reach.

    A reminder is "a reusable asset attachable to many Meeting Types" per the
    research, so it projects as active on the event types it is attached to. An
    attachment list that came back empty means the reminder is attached nowhere
    yet, and Cal's own flag for that is ``isActiveOnAllEventTypes: false`` with
    no ids - a workflow that exists but fires for nobody, which is exactly the
    state a freshly created and not-yet-attached reminder is in.
    """
    ids = [str(value) for value in (meeting_type_ids or []) if str(value).strip()]
    return {"isActiveOnAllEventTypes": not ids, "activeOnEventTypeIds": ids}


def workflow(
    reminder: Mapping[str, Any],
    *,
    meeting_type_ids: list[str] | None = None,
    booking: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """The whole DTO a ``POST /v2/workflows`` would carry, plus its header.

    The header is returned alongside rather than inside the body because that is
    where it goes: the Cal API reference requires
    ``cal-api-version: 2024-08-13`` on the request, not in the payload, and a
    projection that buried it in the body would be wrong in a way that only
    shows up at the vendor.
    """
    name = str(reminder.get("name") or "Reminder").strip() or "Reminder"
    steps = [*cal_filter_steps(reminder), cal_step(reminder)]
    return {
        "method": "POST",
        "path": vocab.CAL_WORKFLOWS_PATH,
        "headers": {vocab.CAL_API_VERSION_HEADER: vocab.CAL_API_VERSION},
        "body": {
            "name": name,
            "activation": activation(reminder, meeting_type_ids),
            "trigger": cal_trigger(reminder, booking),
            "steps": steps,
        },
    }


# --------------------------------------------------------------------------- #
# The other direction
# --------------------------------------------------------------------------- #


def _step_action(step: Mapping[str, Any]) -> str:
    return str(step.get("action") or "").strip()


def _step_template(step: Mapping[str, Any]) -> str:
    return str(step.get("template") or "").strip()


def validate_workflow(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Check an inbound Cal workflow against the researched enums.

    The research publishes four enums, and a Cal instance will accept a document
    that uses them all - most of which this product cannot act on. Validating on
    the way in is what turns that into a stated finding rather than a reminder
    that silently never fires.

    Returns ``ok`` plus one ``problem`` per thing that would not survive. A
    workflow this product cannot run is *not* an error: it is a workflow for
    Cal.com, and Cal.com is where it belongs.
    """
    body = payload.get("body") if isinstance(payload.get("body"), Mapping) else payload
    problems: list[dict[str, Any]] = []

    trigger = body.get("trigger") if isinstance(body.get("trigger"), Mapping) else {}
    trigger_type = str(trigger.get("type") or "").strip()
    if trigger_type not in vocab.CAL_TRIGGERS:
        problems.append(
            {
                "field": "trigger.type",
                "value": trigger_type or None,
                "message": f"not one of the {len(vocab.CAL_TRIGGERS)} published Cal triggers",
            }
        )
    elif trigger_type not in (vocab.CAL_TRIGGER_BEFORE, vocab.CAL_TRIGGER_AFTER):
        problems.append(
            {
                "field": "trigger.type",
                "value": trigger_type,
                "message": (
                    "Cal supports this trigger and this product's research does not; a reminder "
                    f"workflow uses {vocab.CAL_TRIGGER_BEFORE} or {vocab.CAL_TRIGGER_AFTER}"
                ),
            }
        )

    offset = trigger.get("offset") if isinstance(trigger.get("offset"), Mapping) else {}
    unit = str(offset.get("unit") or "").strip()
    if unit and unit not in vocab.CAL_OFFSET_UNITS:
        problems.append(
            {
                "field": "trigger.offset.unit",
                "value": unit,
                "message": f"not one of {list(vocab.CAL_OFFSET_UNITS)}",
            }
        )
    steps = body.get("steps")
    if not isinstance(steps, list) or not steps:
        problems.append({"field": "steps", "value": None, "message": "a workflow needs ordered steps"})
        steps = []

    action_steps = 0
    for index, step in enumerate(steps):
        if not isinstance(step, Mapping):
            problems.append({"field": f"steps[{index}]", "value": None, "message": "not an object"})
            continue
        kind = str(step.get("type") or "action").strip()
        if kind not in vocab.CAL_STEP_KINDS:
            problems.append(
                {
                    "field": f"steps[{index}].type",
                    "value": kind,
                    "message": f"not one of {list(vocab.CAL_STEP_KINDS)}",
                }
            )
        if kind != "action":
            if kind in ("paths", "delay", "lead_enrichment"):
                problems.append(
                    {
                        "field": f"steps[{index}].type",
                        "value": kind,
                        "message": (
                            "Cal models this step kind and this product's research does not, so "
                            "it is not projected in either direction"
                        ),
                    }
                )
            continue
        action_steps += 1
        action = _step_action(step)
        if action not in vocab.CAL_STEP_ACTIONS:
            problems.append(
                {
                    "field": f"steps[{index}].action",
                    "value": action or None,
                    "message": f"not one of {list(vocab.CAL_STEP_ACTIONS)}",
                }
            )
        elif action not in vocab.CAL_ACTION_FROM_CHANNEL.values():
            problems.append(
                {
                    "field": f"steps[{index}].action",
                    "value": action,
                    "message": (
                        "a reminder is addressed to the guest, so only "
                        f"{sorted(set(vocab.CAL_ACTION_FROM_CHANNEL.values()))} are reachable"
                    ),
                }
            )
        template = _step_template(step)
        if template and template not in vocab.CAL_STEP_TEMPLATES:
            problems.append(
                {
                    "field": f"steps[{index}].template",
                    "value": template,
                    "message": f"not one of {list(vocab.CAL_STEP_TEMPLATES)}",
                }
            )

    if action_steps == 0 and not problems:
        problems.append(
            {
                "field": "steps",
                "value": None,
                "message": "no action step, so the workflow sends nothing",
            }
        )

    blocking = [problem for problem in problems if "does not" not in problem["message"]]
    return {
        "ok": not blocking,
        "published": {
            "triggers": list(vocab.CAL_TRIGGERS),
            "offset_units": list(vocab.CAL_OFFSET_UNITS),
            "step_actions": list(vocab.CAL_STEP_ACTIONS),
            "step_templates": list(vocab.CAL_STEP_TEMPLATES),
            "step_kinds": list(vocab.CAL_STEP_KINDS),
            "reachable_actions": sorted(set(vocab.CAL_ACTION_FROM_CHANNEL.values())),
            "reachable_triggers": [vocab.CAL_TRIGGER_BEFORE, vocab.CAL_TRIGGER_AFTER],
        },
        "problems": problems,
        "count": len(problems),
    }


# --------------------------------------------------------------------------- #
# Conversions
# --------------------------------------------------------------------------- #


def offset_from_cal(offset: Mapping[str, Any]) -> dict[str, Any]:
    """A Cal ``{value, unit}`` as this product's offset, for the inbound path.

    The inverse of :func:`cal_offset` where one exists. Cal's ``day`` unit is
    ambiguous between one day and one week, so it stays ``days`` - the exact
    reading - and a deployment that wants weeks says ``weeks`` in its own
    reminder.
    """
    unit = str(offset.get("unit") or "hour").strip().lower()
    inverse = {value: key for key, value in vocab.CAL_UNIT_FROM.items()}
    if unit not in inverse:
        raise ReminderError(f"offset unit must be one of {list(vocab.CAL_OFFSET_UNITS)}; got {unit!r}")
    return vocab.require_offset(offset.get("value", vocab.DEFAULT_OFFSET), inverse[unit])


def condition_from_cal(trigger_type: str, offset: Mapping[str, Any]) -> dict[str, Any]:
    """A Cal trigger and offset as this product's condition and offset.

    A ``beforeEvent`` becomes the plain "Before the Meeting", unless the
    workflow also carries a response filter - in which case the conditional
    pre-meeting variant is the only reading that reproduces the behaviour, and
    it is the reading the projection itself writes.
    """
    if trigger_type not in (vocab.CAL_TRIGGER_BEFORE, vocab.CAL_TRIGGER_AFTER):
        raise ReminderError(
            f"trigger must be {vocab.CAL_TRIGGER_BEFORE} or {vocab.CAL_TRIGGER_AFTER} to become a "
            f"reminder condition; got {trigger_type!r}"
        )
    return {
        "condition": vocab.BEFORE if trigger_type == vocab.CAL_TRIGGER_BEFORE else vocab.AFTER,
        "offset": offset_from_cal(offset),
    }


def fire_time_matches(projected: Mapping[str, Any], reminder: Mapping[str, Any], booking: Mapping[str, Any]) -> bool:
    """Whether a projected workflow would fire at the same instant as this one.

    Used by the tests as the round-trip check: project, convert back, and compare
    the computed fire times. For ``after_meeting`` the two differ by the meeting
    duration unless it is folded in, which is why :func:`cal_trigger` takes the
    booking into account when the caller wants an exact round trip.
    """
    here = conditions.fire_at(reminder, booking)
    if here is None:
        return False
    offset = projected.get("trigger", {}).get("offset", {})
    try:
        converted = offset_from_cal(offset)
    except ReminderError:
        return False
    anchor = booking.get("start")
    from dsr.meeting_reminders.conditions import parse_instant

    start = parse_instant(anchor)
    if start is None:
        return False
    if projected.get("trigger", {}).get("type") == vocab.CAL_TRIGGER_AFTER:
        try:
            start = start + timedelta(minutes=int(booking.get("durationMinutes") or 0))
        except (TypeError, ValueError):
            pass
        return start + timedelta(minutes=converted["minutes"]) == here
    return start - timedelta(minutes=converted["minutes"]) == here
