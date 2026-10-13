"""The workflow's pure rules. No store, no framework, no I/O.

Everything here is a function of its arguments, so the rules can be tested
without a database and read without following a call graph.

Five rules carry a judgement the research left open. Each is named and recorded
in :mod:`dsr.schedule_enrollment.inferences`:

1. The publish panel's default is the backfill: existing matches enrol now.
2. Re-enrollment is the Setting's narrow reading: a record that is currently
   enrolled is never re-enrolled, and a completed record re-enrols only when the
   mode is ``after_completion``.
3. An unenrolled record may re-enrol, because unenrollment removes it from the
   workflow rather than completing it.
4. The queue delay is a per-schedule setting measured in minutes.
5. Deferral is not exclusion: a schedule published with ``future_only`` enrols
   the same records on its next run.
"""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from typing import Any, Mapping, Sequence

from dsr.schedule_enrollment import vocabulary as v
from dsr.schedule_enrollment.errors import EnrollValidationError
from dsr.seller_tasks import vocabulary as task_vocabulary

EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
MINUTE_MS = 60 * 1000
DAY_MS = 24 * 60 * 60 * 1000


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
        raise EnrollValidationError({field: "expected a timestamp, not a boolean"})
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
            raise EnrollValidationError(
                {field: f"{value!r} is not an ISO date or a number"}
            ) from exc
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return _ms_from_datetime(parsed)
    raise EnrollValidationError({field: f"cannot read a timestamp from {type(value).__name__}"})


def to_iso(milliseconds: int | None) -> str | None:
    """The ISO 8601 copy stored beside a millisecond value, for a reader."""

    if milliseconds is None:
        return None
    return datetime.fromtimestamp(milliseconds / 1000, tz=timezone.utc).isoformat(
        timespec="milliseconds"
    )


def next_run_at_ms(last_ms: int | None, interval_minutes: int | None) -> int | None:
    """The instant the schedule next runs, or ``None`` before its first run."""

    if last_ms is None:
        return None
    return int(last_ms) + int(interval_minutes or v.DEFAULT_INTERVAL_MINUTES) * MINUTE_MS


def retention_cutoff_ms(now_ms: int, kind: str) -> int:
    """The oldest instant still inside a researched retention window.

    ``kind`` is one of ``action_log``, ``enrollment_history`` or
    ``enrollments``, matching :data:`dsr.schedule_enrollment.vocabulary.RETENTION`.
    """

    days = v.RETENTION.get(f"{kind}_days", v.RETENTION_ACTION_LOG_DAYS)
    return int(now_ms) - int(days) * DAY_MS


# --------------------------------------------------------------------------- #
# Closed vocabularies and small readers
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
    errors[field] = f"must be one of {', '.join(allowed)}"
    return None


def _whole_number(
    value: Any, field: str, errors: dict[str, str], *, minimum: int = 0
) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        errors[field] = "must be a whole number"
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        errors[field] = "must be a whole number"
        return None
    if isinstance(value, float) and not float(value).is_integer():
        errors[field] = "must be a whole number"
        return None
    if number < minimum:
        errors[field] = f"must be at least {minimum}"
        return None
    return number


def read_field(data: Any, field: str) -> Any:
    """Read one field from a record's payload.

    A dotted name resolves through nested objects, and a plain name resolves at
    the record's top level first and then inside the WF-039 ``body``. So a
    bundle-written row and a team-written row both resolve without a second shape
    being declared.
    """

    if not isinstance(data, Mapping):
        return None
    if "." in field:
        node: Any = data
        for part in field.split("."):
            if isinstance(node, Mapping) and part in node:
                node = node[part]
            else:
                return None
        return node
    if data.get(field) not in (None, ""):
        return data[field]
    body = data.get("body")
    if isinstance(body, Mapping) and body.get(field) not in (None, ""):
        return body[field]
    value = data.get(field)
    return None if value in (None, "") else value


def _as_number(value: Any) -> float | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _equal(left: Any, right: Any) -> bool:
    left_number = _as_number(left)
    right_number = _as_number(right)
    if left_number is not None and right_number is not None:
        return left_number == right_number
    if left is None or right is None:
        return left is right
    return str(left).strip() == str(right).strip()


def _order_key(value: Any) -> tuple[int, Any] | None:
    """A comparable key. Numbers sort among numbers, text among text."""

    if value is None or value == "" or isinstance(value, bool):
        return None
    number = _as_number(value)
    if number is not None:
        return (0, number)
    return (1, str(value).lower())


# --------------------------------------------------------------------------- #
# The enrollment filter
# --------------------------------------------------------------------------- #


def normalise_conditions(raw: Any, errors: dict[str, str], name: str) -> list[dict[str, Any]]:
    """Read a filter into a list of ``{field, op, value}`` conditions.

    Accepts the long form (a list of objects) and the shorthand (an object of
    field/value pairs, or of field/condition pairs). One bad condition is
    reported with its index and the rest of the filter still reads, so a page can
    mark every bad row at once.
    """

    if raw in (None, ""):
        return []
    if isinstance(raw, Mapping):
        raw = [
            {"field": key, **(value if isinstance(value, Mapping) else {"value": value})}
            for key, value in raw.items()
        ]
    if not isinstance(raw, (list, tuple)):
        errors[name] = "must be a list of conditions or an object of field/value pairs"
        return []

    out: list[dict[str, Any]] = []
    for index, item in enumerate(raw):
        prefix = f"{name}[{index}]"
        if not isinstance(item, Mapping):
            errors[prefix] = "must be an object with field, op and value"
            continue
        field = str(item.get("field") or "").strip()
        if not field:
            errors[f"{prefix}.field"] = "is required"
            continue
        op = coerce_choice(item.get("op"), v.OPS, f"{prefix}.op", errors, default=v.OP_EQ)
        if op is None:
            continue
        value = item.get("value")
        if op in (v.OP_IN, v.OP_NOT_IN):
            if isinstance(value, str):
                value = [part.strip() for part in value.split(",") if part.strip()]
            if not isinstance(value, (list, tuple)):
                errors[f"{prefix}.value"] = f"must be a list of values for op {op}"
                continue
            value = list(value)
        if op != v.OP_EXISTS and value in (None, ""):
            errors[f"{prefix}.value"] = "is required for this op"
            continue
        out.append({"field": field, "op": op, "value": value})
    return out


def match_condition(value: Any, condition: Mapping[str, Any]) -> bool:
    """Does one value satisfy one condition?"""

    op = condition.get("op", v.OP_EQ)
    target = condition.get("value")
    if op == v.OP_EXISTS:
        return value not in (None, "")
    if op == v.OP_EQ:
        return _equal(value, target)
    if op == v.OP_NE:
        return not _equal(value, target)
    if op == v.OP_IN:
        return any(_equal(value, item) for item in (target or []))
    if op == v.OP_NOT_IN:
        return not any(_equal(value, item) for item in (target or []))
    if op == v.OP_CONTAINS:
        if value in (None, ""):
            return False
        return str(target).lower() in str(value).lower()

    left = _order_key(value)
    right = _order_key(target)
    if left is None or right is None or left[0] != right[0]:
        return False
    if op == v.OP_GT:
        return left[1] > right[1]
    if op == v.OP_GTE:
        return left[1] >= right[1]
    if op == v.OP_LT:
        return left[1] < right[1]
    if op == v.OP_LTE:
        return left[1] <= right[1]
    return False


def record_matches(data: Any, conditions: Sequence[Mapping[str, Any]]) -> bool:
    """Does a record satisfy every condition? An empty filter matches everything."""

    for condition in conditions or ():
        if not match_condition(read_field(data, str(condition["field"])), condition):
            return False
    return True


# --------------------------------------------------------------------------- #
# Payload normalisation
# --------------------------------------------------------------------------- #


def normalise_actions(raw: Any, errors: dict[str, str]) -> list[str]:
    """Read the scheduled actions. Only the executed two are accepted."""

    if raw in (None, ""):
        return list(v.DEFAULT_ACTIONS)
    if isinstance(raw, str):
        raw = [part.strip() for part in raw.split(",") if part.strip()]
    if not isinstance(raw, (list, tuple)):
        errors["actions"] = "must be a list of action names"
        return []

    out: list[str] = []
    for index, item in enumerate(raw):
        text = str(item)
        if text in v.ACTIONS and text not in v.IMPLEMENTED_ACTIONS:
            errors[f"actions[{index}]"] = f"{text!r} is declared but not executed by this build"
            continue
        action = coerce_choice(item, v.IMPLEMENTED_ACTIONS, f"actions[{index}]", errors)
        if action is not None and action not in out:
            out.append(action)
    if not out and not errors:
        errors["actions"] = "must name at least one action"
    return out


def normalise_schedule_payload(
    payload: Mapping[str, Any],
    *,
    now_ms: int | None = None,
) -> dict[str, Any]:
    """Read one create or patch payload into the exact fields the store holds.

    Raises :class:`EnrollValidationError` with one entry per bad field, so a page
    can mark every field at once rather than one per round trip.
    """

    payload = dict(payload or {})
    errors: dict[str, str] = {}

    trigger = coerce_choice(
        payload.get("trigger"), v.TRIGGER_TYPES, "trigger", errors, default=v.DEFAULT_TRIGGER
    )
    if trigger is not None and trigger != v.TRIGGER_SCHEDULE:
        errors["trigger"] = (
            "this workflow enrols on a schedule; the other trigger types are different workflows"
        )
        trigger = v.TRIGGER_SCHEDULE

    obj = payload.get("object")
    obj = str(obj).strip() if obj not in (None, "") else v.DEFAULT_OBJECT

    filters = normalise_conditions(payload.get("filters"), errors, "filters")
    unenroll = normalise_conditions(payload.get("unenroll_filter"), errors, "unenroll_filter")

    interval = _whole_number(payload.get("interval_minutes"), "interval_minutes", errors, minimum=1)
    if interval is None and payload.get("interval_minutes") is None:
        interval = v.DEFAULT_INTERVAL_MINUTES

    delay = _whole_number(payload.get("delay_minutes"), "delay_minutes", errors, minimum=0)
    if delay is None and payload.get("delay_minutes") is None:
        delay = v.DEFAULT_DELAY_MINUTES

    mode = coerce_choice(
        payload.get("re_enroll"),
        v.REENROLL_MODES,
        "re_enroll",
        errors,
        default=v.DEFAULT_REENROLL_MODE,
    )
    backfill = coerce_choice(
        payload.get("backfill"), v.BACKFILL_CHOICES, "backfill", errors, default=v.DEFAULT_BACKFILL
    )
    actions = normalise_actions(payload.get("actions"), errors)

    room_id = payload.get("room_id")
    room_id = str(room_id).strip() if room_id not in (None, "") else None
    name = str(payload.get("name") or "").strip() or "Scheduled enrollment"

    assignee = payload.get("assignee")
    assignee = str(assignee).strip() if assignee not in (None, "") else None

    if errors:
        raise EnrollValidationError(errors)

    return {
        "name": name,
        "trigger": trigger,
        "object": obj,
        "filters": filters,
        "unenroll_filter": unenroll,
        "interval_minutes": interval,
        "delay_minutes": delay,
        "re_enroll": mode,
        "backfill": backfill,
        "actions": actions,
        "assignee": assignee,
        "room_id": room_id,
        "published": bool(payload.get("published", False)),
    }


# --------------------------------------------------------------------------- #
# Reading a record and deciding its enrollment
# --------------------------------------------------------------------------- #


def record_view(record: Mapping[str, Any]) -> dict[str, Any]:
    """Normalise one CRM record row into the fields the schedule reads."""

    data = record.get("data") or {}
    label = None
    for key in ("name", "Name", "title", "subject", "email", "label"):
        label = read_field(data, key)
        if label not in (None, ""):
            break
    owner = None
    for key in ("owner", "owner_id", "OwnerId", "assigned_user", "assigned_to"):
        owner = read_field(data, key)
        if owner not in (None, ""):
            break
    return {
        "id": record["id"],
        "room_id": record.get("room_id"),
        "object": data.get("object"),
        "name": label or record["id"],
        "owner": owner,
        "data": dict(data),
    }


def eligibility(
    prior: Mapping[str, Any] | None,
    mode: str,
) -> tuple[bool, str, bool]:
    """Whether a matching record enrolls, why, and whether it is a re-enrollment.

    ``prior`` is the record's most recent enrollment on this schedule, or
    ``None``. The order is the researched rule: a currently enrolled record is
    refused in both modes, a completed record re-enrols only in
    ``after_completion``, and in ``single`` mode any earlier enrollment blocks a
    second one.
    """

    if not prior:
        return True, v.REASON_MATCHED, False
    status = prior.get("status")
    if status in v.ACTIVE_STATUSES:
        return False, v.REASON_ALREADY_ENROLLED, False
    if mode == v.REENROLL_AFTER_COMPLETION and status in v.TERMINAL_STATUSES:
        return True, v.REASON_RE_ENROLLED, True
    return False, v.REASON_SINGLE_ENROLLMENT, False


def is_released(enrollment: Mapping[str, Any], now_ms: int) -> bool:
    """Has this queued enrollment's delay elapsed?"""

    release = enrollment.get("release_at_ms")
    if release is None:
        return True
    return int(release) <= int(now_ms)


def enrollment_payload(
    schedule: Mapping[str, Any],
    view: Mapping[str, Any],
    now_ms: int,
    *,
    run_id: str | None,
    reason: str,
    re_enrollment: bool,
    delay_minutes: int,
) -> dict[str, Any]:
    """The enrollment row, with the queue delay already applied."""

    release_at = now_ms + int(delay_minutes or 0) * MINUTE_MS
    return {
        "schedule_id": schedule["id"],
        "record_id": view["id"],
        "object": view.get("object") or schedule.get("object"),
        "room_id": schedule.get("room_id"),
        "status": v.STATUS_QUEUED,
        "reason": reason,
        "re_enrollment": bool(re_enrollment),
        "delay_minutes": int(delay_minutes or 0),
        "enrolled_at_ms": now_ms,
        "enrolled_at": to_iso(now_ms),
        "release_at_ms": release_at,
        "release_at": to_iso(release_at),
        "completed_at_ms": None,
        "completed_at": None,
        "unenrolled_at_ms": None,
        "unenrolled_at": None,
        "run_id": run_id,
        "actions": list(schedule.get("actions") or v.DEFAULT_ACTIONS),
        "task_id": None,
        "notification_id": None,
    }


def marker_patch(schedule: Mapping[str, Any], state: str, now_ms: int) -> dict[str, Any]:
    """The researched field update that records enrollment state on the record.

    It is also the enrolment history a workflow-based filter reads, which is why
    it is written on every transition and not only on enrollment.
    """

    return {
        v.MARKER_AT_MS: now_ms,
        v.MARKER_SCHEDULE: schedule["id"],
        v.MARKER_STATE: state,
    }


def task_payload(
    schedule: Mapping[str, Any], view: Mapping[str, Any], now_ms: int
) -> dict[str, Any]:
    """The researched Create task payload, ready for WF-116's task engine."""

    label = view.get("name") or view["id"]
    return {
        "subject": f"Scheduled enrollment: {label}",
        "body": (
            f"{label} ({view.get('object') or schedule.get('object')}) was enrolled by the "
            f"schedule {schedule.get('name')}."
        ),
        "due_at": to_iso(now_ms + 2 * DAY_MS),
        "priority": task_vocabulary.PRIORITY_MEDIUM,
        "task_type": task_vocabulary.TYPE_TODO,
        "assignee": schedule.get("assignee") or view.get("owner"),
        "room_id": schedule.get("room_id"),
    }


def notification_message(schedule: Mapping[str, Any], view: Mapping[str, Any]) -> str:
    """The researched Send internal notification body."""

    label = view.get("name") or view["id"]
    return (
        f"[{schedule.get('name')}] {label} ({view.get('object') or schedule.get('object')}) "
        "completed the scheduled enrollment."
    )


__all__ = [
    "DAY_MS",
    "EPOCH",
    "MINUTE_MS",
    "coerce_choice",
    "eligibility",
    "enrollment_payload",
    "is_released",
    "marker_patch",
    "match_condition",
    "next_run_at_ms",
    "normalise_actions",
    "normalise_conditions",
    "normalise_schedule_payload",
    "notification_message",
    "parse_timestamp",
    "read_field",
    "record_matches",
    "record_view",
    "retention_cutoff_ms",
    "task_payload",
    "to_iso",
]
