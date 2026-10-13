"""The workflow's pure rules. No store, no framework, no I/O.

Everything here is a function of its arguments, so the rules can be tested
without a database and read without following a call graph.

Four rules carry a judgement the research left open. Each is named and recorded
in :mod:`dsr.stale_opportunities.inferences`:

1. Staleness is measured from the more recent of the two fields the data flow
   names, ``last activity date`` and ``date entered stage``.
2. A sweep re-fires on an opportunity only after the opportunity's reference
   instant moves past the marker the last escalation wrote. This models the
   researched ``Remove from Workflow``.
3. The 7/21-day cadence is a default, because the research gives it as an
   example.
4. An opportunity with neither evidence field is not evaluable and is reported
   rather than treated as stale.
"""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from typing import Any, Mapping, Sequence

from dsr.seller_tasks import vocabulary as task_vocabulary
from dsr.stale_opportunities import vocabulary as v
from dsr.stale_opportunities.errors import SweepValidationError

EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
DAY_MS = 24 * 60 * 60 * 1000

#: The opportunity fields the data flow names, with every spelling a bundle or a
#: team may have used. Read from the record's top level first, then from its
#: WF-039 ``body``.
_FIELD_SPELLINGS: dict[str, tuple[str, ...]] = {
    "name": ("name", "Name", "title", "opportunity_name"),
    "stage": ("stage", "StageName", "stage_name", "pipeline_stage"),
    "stage_entered": (
        "stage_entered_at",
        "stage_entered_at_ms",
        "date_entered_stage",
        "StageEnteredAt",
        "stage_entered",
    ),
    "last_activity": (
        "last_activity_at",
        "last_activity_at_ms",
        "last_activity_date",
        "LastActivityDate",
        "last_activity",
    ),
    "value": ("value", "amount", "Amount", "Value", "opportunity_value", "deal_value"),
    "owner": ("owner", "owner_id", "OwnerId", "assigned_user", "assigned_to"),
}

#: The stage-mode words a filter accepts.
STAGE_MODE_INCLUDE = "include"
STAGE_MODE_EXCLUDE = "exclude"
STAGE_MODES: tuple[str, ...] = (STAGE_MODE_INCLUDE, STAGE_MODE_EXCLUDE)


# --------------------------------------------------------------------------- #
# Timestamps
# --------------------------------------------------------------------------- #


def _ms_from_datetime(moment: datetime) -> int:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return int(round((moment - EPOCH).total_seconds() * 1000))


def parse_timestamp(value: Any, field: str) -> int | None:
    """Turn one caller value into Unix epoch milliseconds.

    Accepts an integer or float (milliseconds), a numeric string, an ISO 8601
    string, a ``date`` or a ``datetime``. ``None`` or an empty string returns
    ``None`` so the caller can decide whether the field is required.
    """

    if value is None:
        return None
    if isinstance(value, bool):
        raise SweepValidationError({field: "expected a timestamp, not a boolean"})
    if isinstance(value, datetime):
        return _ms_from_datetime(value)
    if isinstance(value, date):
        return _ms_from_datetime(datetime.combine(value, time.min, tzinfo=timezone.utc))
    if isinstance(value, (int, float)):
        return int(round(float(value)))
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.isdigit() or (text.startswith("-") and text[1:].isdigit()):
            return int(text)
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError as exc:
            raise SweepValidationError(
                {field: f"{value!r} is not an ISO date or a number"}
            ) from exc
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return _ms_from_datetime(parsed)
    raise SweepValidationError({field: f"cannot read a timestamp from {type(value).__name__}"})


def to_iso(milliseconds: int | None) -> str | None:
    """The ISO 8601 copy stored beside a millisecond value, for a reader."""

    if milliseconds is None:
        return None
    return datetime.fromtimestamp(milliseconds / 1000, tz=timezone.utc).isoformat(
        timespec="milliseconds"
    )


# --------------------------------------------------------------------------- #
# Closed vocabularies
# --------------------------------------------------------------------------- #


def coerce_choice(
    value: Any,
    allowed: Sequence[str],
    field: str,
    errors: dict[str, str],
    *,
    default: str | None = None,
) -> str | None:
    """Accept a value only from a closed set. Record the field on a miss."""

    if value is None or value == "":
        return default
    text = str(value)
    if text in allowed:
        return text
    if text.upper() in allowed:
        return text.upper()
    errors[field] = f"must be one of {', '.join(allowed)}"
    return None


def _whole_days(value: Any, field: str, errors: dict[str, str], *, minimum: int = 0) -> int | None:
    if value is None or value == "":
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        errors[field] = "must be a whole number of days"
        return None
    if isinstance(value, float) and not float(value).is_integer():
        errors[field] = "must be a whole number of days"
        return None
    if number < minimum:
        errors[field] = f"must be at least {minimum}"
        return None
    return number


def _number(value: Any, field: str, errors: dict[str, str]) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        errors[field] = "must be a number"
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        errors[field] = "must be a number"
        return None


# --------------------------------------------------------------------------- #
# Payload normalisation
# --------------------------------------------------------------------------- #


def normalise_cadence(
    steps: Any,
    errors: dict[str, str],
) -> list[dict[str, Any]]:
    """Validate a graduated cadence, oldest step first.

    A step is ``{"after_days": n, "action": "nudge"|"escalate"}``. The order is
    the research's - a Wait step schedules the next step - so the list is sorted
    by ``after_days`` and a duplicate day is refused rather than silently
    dropped.
    """

    if steps is None or steps == "":
        return [dict(step) for step in v.DEFAULT_CADENCE]
    if not isinstance(steps, (list, tuple)):
        errors["cadence"] = "must be a list of steps"
        return []
    out: list[dict[str, Any]] = []
    seen: set[int] = set()
    for index, raw in enumerate(steps):
        if not isinstance(raw, Mapping):
            errors["cadence"] = f"step {index} must be an object with after_days and action"
            return []
        after = _whole_days(raw.get("after_days"), f"cadence[{index}].after_days", errors)
        action = coerce_choice(raw.get("action"), v.STEP_KINDS, f"cadence[{index}].action", errors)
        if after is None or action is None:
            continue
        if after in seen:
            errors["cadence"] = f"two steps cannot share after_days={after}"
            return []
        seen.add(after)
        out.append({"after_days": after, "action": action})
    out.sort(key=lambda step: step["after_days"])
    return out


def normalise_filters(raw: Any, errors: dict[str, str]) -> dict[str, Any]:
    """Read the optional If/Else filters: a stage set and a value band."""

    filters = dict(raw) if isinstance(raw, Mapping) else {}
    stages_raw = filters.get("stages")
    stages: list[str] = []
    if stages_raw:
        if isinstance(stages_raw, str):
            stages = [part.strip() for part in stages_raw.split(",") if part.strip()]
        elif isinstance(stages_raw, (list, tuple)):
            stages = [str(part).strip() for part in stages_raw if str(part).strip()]
        else:
            errors["filters.stages"] = "must be a list of stage names"
    mode = coerce_choice(
        filters.get("stage_mode"),
        STAGE_MODES,
        "filters.stage_mode",
        errors,
        default=STAGE_MODE_INCLUDE,
    )
    band = filters.get("value_band") if isinstance(filters.get("value_band"), Mapping) else {}
    value_min = _number(
        filters.get("value_min", band.get("min") if band else None),
        "filters.value_min",
        errors,
    )
    value_max = _number(
        filters.get("value_max", band.get("max") if band else None),
        "filters.value_max",
        errors,
    )
    if value_min is not None and value_max is not None and value_min > value_max:
        errors["filters.value_min"] = "must not exceed filters.value_max"
    return {
        "stages": stages,
        "stage_mode": mode or STAGE_MODE_INCLUDE,
        "value_min": value_min,
        "value_max": value_max,
    }


def normalise_sweep_payload(
    payload: Mapping[str, Any],
    *,
    now_ms: int | None = None,
) -> dict[str, Any]:
    """Read one create or patch payload into the exact fields the store holds.

    Raises :class:`SweepValidationError` with one entry per bad field, so a page
    can mark every field at once rather than one per round trip.
    """

    payload = dict(payload or {})
    errors: dict[str, str] = {}

    threshold = _whole_days(
        payload.get("threshold_days"),
        "threshold_days",
        errors,
        minimum=1,
    )
    if threshold is None and payload.get("threshold_days") is None:
        threshold = v.DEFAULT_THRESHOLD_DAYS

    basis = coerce_choice(
        payload.get("inactivity_basis", payload.get("stale_reference")),
        v.STALE_REFERENCES,
        "inactivity_basis",
        errors,
        default=v.DEFAULT_STALE_REFERENCE,
    )

    cadence = normalise_cadence(payload.get("cadence"), errors)
    if (
        threshold is not None
        and cadence
        and all(step["after_days"] > threshold for step in cadence)
    ):
        errors["cadence"] = "at least one step must be due at or before the threshold"

    filters = normalise_filters(payload.get("filters"), errors)

    refire = coerce_choice(
        payload.get("refire_control"),
        v.REFIRE_CONTROLS,
        "refire_control",
        errors,
        default=v.DEFAULT_REFIRE_CONTROL,
    )

    trigger = coerce_choice(
        payload.get("trigger"),
        v.TRIGGERS,
        "trigger",
        errors,
        default=v.TRIGGER_STALE,
    )

    room_id = payload.get("room_id")
    room_id = str(room_id).strip() if room_id not in (None, "") else None

    name = str(payload.get("name") or "").strip() or "Stale opportunity sweep"

    escalate_to_stage = payload.get("escalate_to_stage")
    escalate_to_stage = (
        str(escalate_to_stage).strip() if escalate_to_stage not in (None, "") else None
    )

    value_bump = _number(payload.get("value_bump"), "value_bump", errors)

    assignee = payload.get("assignee")
    assignee = str(assignee).strip() if assignee not in (None, "") else None

    notify_team = str(payload.get("notify_team") or "").strip() or "sales"

    if errors:
        raise SweepValidationError(errors)

    return {
        "name": name,
        "trigger": trigger,
        "room_id": room_id,
        "inactivity_basis": basis,
        "threshold_days": threshold,
        "cadence": cadence,
        "filters": filters,
        "refire_control": refire,
        "escalate_to_stage": escalate_to_stage,
        "value_bump": value_bump,
        "assignee": assignee,
        "notify_team": notify_team,
        "published": bool(payload.get("published", False)),
    }


# --------------------------------------------------------------------------- #
# Reading an opportunity
# --------------------------------------------------------------------------- #


def _pick(data: Mapping[str, Any], keys: Sequence[str]) -> Any:
    body = data.get("body")
    body = body if isinstance(body, Mapping) else {}
    for key in keys:
        value = data.get(key)
        if value not in (None, ""):
            return value
    for key in keys:
        value = body.get(key)
        if value not in (None, ""):
            return value
    return None


def opportunity_view(record: Mapping[str, Any]) -> dict[str, Any]:
    """Normalise one opportunity row into the fields the data flow names.

    The record is read from its top level and from its WF-039 ``body``, so a
    bundle-written row and a team-written row both resolve without a second
    shape being declared.
    """

    data = record.get("data") or {}
    stage_entered = parse_timestamp(_pick(data, _FIELD_SPELLINGS["stage_entered"]), "stage_entered")
    last_activity = parse_timestamp(_pick(data, _FIELD_SPELLINGS["last_activity"]), "last_activity")
    value = _pick(data, _FIELD_SPELLINGS["value"])
    try:
        value = float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        value = None
    return {
        "id": record["id"],
        "room_id": record.get("room_id"),
        "name": _pick(data, _FIELD_SPELLINGS["name"]) or record["id"],
        "stage": _pick(data, _FIELD_SPELLINGS["stage"]),
        "owner": _pick(data, _FIELD_SPELLINGS["owner"]),
        "value": value,
        "stage_entered_at_ms": stage_entered,
        "last_activity_at_ms": last_activity,
        "data": dict(data),
    }


def reference_ms(view: Mapping[str, Any], basis: str) -> tuple[int | None, str]:
    """The instant staleness is measured from, and which field supplied it."""

    last_activity = view.get("last_activity_at_ms")
    stage_entry = view.get("stage_entered_at_ms")
    if basis == v.REFERENCE_LAST_ACTIVITY:
        return last_activity, v.REFERENCE_LAST_ACTIVITY
    if basis == v.REFERENCE_STAGE_ENTRY:
        return stage_entry, v.REFERENCE_STAGE_ENTRY
    candidates = [value for value in (last_activity, stage_entry) if value is not None]
    if not candidates:
        return None, v.REFERENCE_LATEST_OF_BOTH
    chosen = max(candidates)
    field = v.REFERENCE_LAST_ACTIVITY if chosen == last_activity else v.REFERENCE_STAGE_ENTRY
    return chosen, field


def idle_days(reference: int, now_ms: int) -> float:
    """How long an opportunity has been idle, in fractional days."""

    return max(0.0, (now_ms - reference) / DAY_MS)


def stage_filter_ok(stage: Any, filters: Mapping[str, Any]) -> bool:
    stages = list(filters.get("stages") or [])
    if not stages:
        return True
    if stage is None:
        return False
    if filters.get("stage_mode") == STAGE_MODE_EXCLUDE:
        return str(stage) not in stages
    return str(stage) in stages


def value_band_ok(value: Any, filters: Mapping[str, Any]) -> bool:
    value_min = filters.get("value_min")
    value_max = filters.get("value_max")
    if value_min is None and value_max is None:
        return True
    if value is None:
        return False
    if value_min is not None and value < value_min:
        return False
    return not (value_max is not None and value > value_max)


def cadence_step_for(idle: float, cadence: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    """The furthest cadence step reached by this idle time, or ``None``."""

    reached = [dict(step) for step in cadence if idle >= step["after_days"]]
    return reached[-1] if reached else None


def already_escalated(data: Mapping[str, Any], reference: int | None) -> bool:
    """Has this opportunity been escalated since its reference instant?"""

    marker = data.get(v.MARKER_AT_MS)
    if marker is None:
        return False
    try:
        marker_ms = int(marker)
    except (TypeError, ValueError):
        return False
    if reference is None:
        return True
    return marker_ms >= reference


def evaluate_opportunity(
    view: Mapping[str, Any],
    sweep: Mapping[str, Any],
    now_ms: int,
) -> dict[str, Any]:
    """Decide what one sweep does to one opportunity, and why.

    The order is the researched data flow: the inactivity threshold first, then
    the optional If/Else branch on stage and value, then the idempotency control.
    """

    basis = sweep.get("inactivity_basis", v.DEFAULT_STALE_REFERENCE)
    threshold = int(sweep.get("threshold_days") or v.DEFAULT_THRESHOLD_DAYS)
    reference, reference_field = reference_ms(view, basis)

    decision: dict[str, Any] = {
        "opportunity_id": view["id"],
        "name": view.get("name"),
        "stage": view.get("stage"),
        "owner": view.get("owner"),
        "value": view.get("value"),
        "reference_ms": reference,
        "reference_field": reference_field,
        "matched": False,
        "reason": v.REASON_MATCHED,
        "step": None,
        "idle_days": None,
    }

    if reference is None:
        decision["reason"] = v.REASON_NO_EVIDENCE
        return decision

    idle = idle_days(reference, now_ms)
    decision["idle_days"] = round(idle, 2)

    if idle < threshold:
        decision["reason"] = v.REASON_BELOW_THRESHOLD
        return decision
    if not stage_filter_ok(view.get("stage"), sweep.get("filters") or {}):
        decision["reason"] = v.REASON_STAGE_FILTERED
        return decision
    if not value_band_ok(view.get("value"), sweep.get("filters") or {}):
        decision["reason"] = v.REASON_VALUE_FILTERED
        return decision
    if already_escalated(view.get("data") or {}, reference):
        decision["reason"] = v.REASON_ALREADY_ESCALATED
        return decision

    step = cadence_step_for(idle, sweep.get("cadence") or v.DEFAULT_CADENCE)
    if step is None:
        step = {"after_days": threshold, "action": v.STEP_ESCALATE}
    decision["matched"] = True
    decision["reason"] = v.REASON_MATCHED
    decision["step"] = step
    return decision


# --------------------------------------------------------------------------- #
# The escalation's own writes
# --------------------------------------------------------------------------- #


def escalation_patch(
    view: Mapping[str, Any],
    sweep: Mapping[str, Any],
    step: Mapping[str, Any],
    now_ms: int,
) -> dict[str, Any]:
    """The researched Create/Update Opportunity write.

    It always writes the escalation marker, because that is the idempotency
    control the research names as Remove from Workflow. It writes the stage and
    the value only when the sweep asks for them.
    """

    patch: dict[str, Any] = {
        v.MARKER_AT_MS: now_ms,
        v.MARKER_SWEEP_ID: sweep["id"],
        v.MARKER_STEP: step["action"],
        v.MARKER_FLAG: True,
        "last_escalation_action": step["action"],
        "updated_at_ms": now_ms,
    }
    stage = sweep.get("escalate_to_stage")
    if stage:
        patch["stage"] = stage
        patch["stage_entered_at"] = to_iso(now_ms)
        patch["stage_entered_at_ms"] = now_ms
    value_bump = sweep.get("value_bump")
    if value_bump:
        patch["value"] = (view.get("value") or 0) + value_bump
    return patch


def task_payload(
    view: Mapping[str, Any],
    sweep: Mapping[str, Any],
    decision: Mapping[str, Any],
    now_ms: int,
) -> dict[str, Any]:
    """The researched Add Task payload, ready for WF-116's task engine."""

    step = decision.get("step") or {"action": v.STEP_ESCALATE}
    action = step.get("action", v.STEP_ESCALATE)
    idle = decision.get("idle_days")
    name = view.get("name") or view["id"]
    assignee = sweep.get("assignee") or view.get("owner")
    escalate = action == v.STEP_ESCALATE
    subject = f"{'Escalate' if escalate else 'Nudge'}: {name} has been idle"
    body = (
        f"{name} has been idle for {idle} days in stage {view.get('stage') or 'unknown'}. "
        f"The stale-opportunity sweep ({sweep.get('name')}) raised this task."
    )
    return {
        "subject": subject,
        "body": body,
        "due_at": to_iso(now_ms + 2 * DAY_MS),
        "priority": task_vocabulary.PRIORITY_HIGH if escalate else task_vocabulary.PRIORITY_MEDIUM,
        "task_type": task_vocabulary.TYPE_TODO,
        "assignee": assignee,
        "assignee_role": "Manager" if escalate else None,
        "room_id": sweep.get("room_id"),
    }


def notification_message(
    view: Mapping[str, Any],
    sweep: Mapping[str, Any],
    decision: Mapping[str, Any],
) -> str:
    """The researched Send Internal Notification body."""

    step = decision.get("step") or {"action": v.STEP_ESCALATE}
    name = view.get("name") or view["id"]
    return (
        f"[{step.get('action')}] {name} has been idle {decision.get('idle_days')} days in "
        f"stage {view.get('stage') or 'unknown'}, owner {view.get('owner') or 'unassigned'}."
    )


__all__ = [
    "DAY_MS",
    "EPOCH",
    "STAGE_MODES",
    "STAGE_MODE_EXCLUDE",
    "STAGE_MODE_INCLUDE",
    "already_escalated",
    "cadence_step_for",
    "coerce_choice",
    "escalation_patch",
    "evaluate_opportunity",
    "idle_days",
    "normalise_cadence",
    "normalise_filters",
    "normalise_sweep_payload",
    "notification_message",
    "opportunity_view",
    "parse_timestamp",
    "reference_ms",
    "stage_filter_ok",
    "task_payload",
    "to_iso",
    "value_band_ok",
]
