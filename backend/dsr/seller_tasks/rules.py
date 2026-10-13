"""The workflow's pure rules. No store, no framework, no I/O.

Everything here is a function of its arguments, so the rules can be tested
without a database and read without following a call graph.

Three rules carry a judgement the research left open. Each is named and
recorded in :mod:`dsr.seller_tasks.inferences`:

1. A reminder may not fall after the due time. A reminder exists to fire before
   the due time.
2. A due time given as a date-only string means midnight UTC on that date.
3. The assignee role comes from WF-077's vocabulary rather than a second copy.
"""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from typing import Any, Mapping

from dsr.atomic_bundle.transport import TARGET_COLLECTION
from dsr.seller_tasks import vocabulary as v
from dsr.seller_tasks.errors import TaskValidationError

# WF-077 owns the internal-role words. This build reuses them so a task cannot
# name an assignee role that the workspace role feature never created.
from dsr.workspace_roles.vocabulary import ROLES_INCLUDING_OWNER

EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)

#: The contact records live in WF-039's collection. This build attaches a task to
#: an existing contact rather than re-declaring a contact shape.
CONTACT_COLLECTION = TARGET_COLLECTION
CONTACT_OBJECT = "contact"

#: A task rule per engagement signal. One signal in, one deterministic task out.
#: ``due_in_days`` and ``reminder_hours`` are offsets from the signal time.
SIGNAL_TASK_RULES: dict[str, dict[str, Any]] = {
    v.SIGNAL_TASK_ADDED: {
        "subject": "Follow up after the task was added",
        "task_type": v.TYPE_TODO,
        "priority": v.PRIORITY_MEDIUM,
        "due_in_days": 2,
        "reminder_hours": 24,
    },
    v.SIGNAL_TASK_REMINDER_DUE: {
        "subject": "Task reminder reached",
        "task_type": v.TYPE_TODO,
        "priority": v.PRIORITY_HIGH,
        "due_in_days": 0,
        "reminder_hours": 0,
    },
    v.SIGNAL_CONTACT_TAG_ADDED: {
        "subject": "Contact was tagged",
        "task_type": v.TYPE_TODO,
        "priority": v.PRIORITY_LOW,
        "due_in_days": 3,
        "reminder_hours": 48,
    },
    v.SIGNAL_ENGAGEMENT_SCORE_RULE_MET: {
        "subject": "Engagement score rule was met",
        "task_type": v.TYPE_EMAIL,
        "priority": v.PRIORITY_HIGH,
        "due_in_days": 1,
        "reminder_hours": 12,
    },
    v.SIGNAL_OPPORTUNITY_STAGE_CHANGED: {
        "subject": "Opportunity stage changed",
        "task_type": v.TYPE_CALL,
        "priority": v.PRIORITY_MEDIUM,
        "due_in_days": 1,
        "reminder_hours": 8,
    },
    v.SIGNAL_INBOUND_WEBHOOK: {
        "subject": "Inbound webhook received",
        "task_type": v.TYPE_TODO,
        "priority": v.PRIORITY_MEDIUM,
        "due_in_days": 2,
        "reminder_hours": 24,
    },
}

#: The default subject when a caller sets none. The due time is the only
#: required field, so a title-less task must still read as a task.
DEFAULT_SUBJECT = "Follow up"

# --------------------------------------------------------------------------- #
# Timestamps
# --------------------------------------------------------------------------- #


def _ms_from_datetime(moment: datetime) -> int:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return int(round((moment - EPOCH).total_seconds() * 1000))


def parse_timestamp(value: Any, field: str) -> int | None:
    """Turn one caller value into Unix epoch milliseconds.

    Accepts an integer or a float (milliseconds), a numeric string, an ISO 8601
    string, a ``date`` or a ``datetime``. A ``None`` or an empty string returns
    ``None`` so the caller can decide whether the field is required.
    """

    if value is None:
        return None
    if isinstance(value, bool):
        raise TaskValidationError({field: "expected a timestamp, not a boolean"})
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
        candidate = text.replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError as exc:
            raise TaskValidationError({field: f"{value!r} is not an ISO date or a number"}) from exc
        # A date-only string parses to midnight. That is the recorded rule.
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return _ms_from_datetime(parsed)
    raise TaskValidationError({field: f"cannot read a timestamp from {type(value).__name__}"})


def to_iso(milliseconds: int | None) -> str | None:
    """The ISO 8601 copy stored beside a millisecond value, for a reader."""

    if milliseconds is None:
        return None
    moment = datetime.fromtimestamp(milliseconds / 1000, tz=timezone.utc)
    return moment.isoformat(timespec="milliseconds")


# --------------------------------------------------------------------------- #
# Closed vocabularies
# --------------------------------------------------------------------------- #


def coerce_choice(
    value: Any,
    allowed: tuple[str, ...],
    field: str,
    errors: dict[str, str],
    *,
    default: str | None = None,
) -> str | None:
    """Accept a value only from a closed set. Record the field on a miss."""

    if value is None or value == "":
        return default
    text = str(value)
    if text.upper() in allowed and field != "signal":
        normalised = text.upper()
        if normalised in allowed:
            return normalised
    if text in allowed:
        return text
    errors[field] = f"must be one of {', '.join(allowed)}"
    return None


def is_known_signal(signal: Any) -> bool:
    return str(signal) in v.SIGNALS


def is_permitted_role(role: Any) -> bool:
    """Does WF-077 know this internal role?"""

    return str(role) in ROLES_INCLUDING_OWNER


# --------------------------------------------------------------------------- #
# Signal to task
# --------------------------------------------------------------------------- #


def signal_rule(signal: Any) -> dict[str, Any] | None:
    """The deterministic rule for one engagement signal, or ``None``."""

    return SIGNAL_TASK_RULES.get(str(signal))


def signal_defaults(signal: Any, at_ms: int) -> dict[str, Any]:
    """The task a signal creates, with due and reminder times from the offsets."""

    rule = signal_rule(signal)
    if rule is None:
        raise TaskValidationError({"signal": f"unknown signal {signal!r}"})
    day = 24 * 60 * 60 * 1000
    hour = 60 * 60 * 1000
    due_at_ms = at_ms + int(rule["due_in_days"]) * day
    reminder_at_ms = due_at_ms - int(rule["reminder_hours"]) * hour
    if reminder_at_ms > due_at_ms:
        reminder_at_ms = due_at_ms
    return {
        "subject": rule["subject"],
        "task_type": rule["task_type"],
        "priority": rule["priority"],
        "due_at_ms": due_at_ms,
        "due_at": to_iso(due_at_ms),
        "reminder_at_ms": reminder_at_ms,
        "reminder_at": to_iso(reminder_at_ms),
    }


# --------------------------------------------------------------------------- #
# Payload normalisation
# --------------------------------------------------------------------------- #


def normalise_task_payload(
    payload: Mapping[str, Any],
    *,
    signal_defaults_map: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Read one create payload into the exact fields the store will hold.

    Raises :class:`TaskValidationError` with one entry per bad field, so a page
    can mark every field at once rather than one per round trip.
    """

    errors: dict[str, str] = {}
    payload = dict(payload or {})

    signal = payload.get("signal")
    if signal is not None and not is_known_signal(signal):
        errors["signal"] = f"must be one of {', '.join(v.SIGNALS)}"
    if signal:
        signal = str(signal)

    # Due time: required. A signal may supply it.
    due_raw = payload.get("due_at", payload.get("hs_timestamp"))
    due_at_ms = parse_timestamp(due_raw, "due_at") if due_raw is not None else None
    if due_at_ms is None and signal_defaults_map is not None:
        due_at_ms = signal_defaults_map.get("due_at_ms")
    if due_at_ms is None:
        errors["due_at"] = "the due time is required"

    # Reminder time: optional. It must not fall after the due time.
    reminder_raw = payload.get("reminder_at", payload.get("hs_task_reminders"))
    reminder_at_ms = parse_timestamp(reminder_raw, "reminder_at") if reminder_raw else None
    if reminder_at_ms is None and signal_defaults_map is not None and reminder_raw is None:
        reminder_at_ms = signal_defaults_map.get("reminder_at_ms")
    if reminder_at_ms is not None and due_at_ms is not None and reminder_at_ms > due_at_ms:
        errors["reminder_at"] = "the reminder must not fall after the due time"

    status = coerce_choice(
        payload.get("status", payload.get("hs_task_status")),
        v.STATUSES,
        "hs_task_status",
        errors,
        default=v.STATUS_NOT_STARTED,
    )
    priority = coerce_choice(
        payload.get("priority", payload.get("hs_task_priority"))
        or (signal_defaults_map or {}).get("priority"),
        v.PRIORITIES,
        "hs_task_priority",
        errors,
        default=v.PRIORITY_MEDIUM,
    )
    task_type = coerce_choice(
        payload.get("task_type", payload.get("hs_task_type"))
        or (signal_defaults_map or {}).get("task_type"),
        v.TASK_TYPES,
        "hs_task_type",
        errors,
        default=v.TYPE_TODO,
    )

    contact_id = payload.get("contact_id")
    if contact_id is not None:
        contact_id = str(contact_id).strip() or None
    attachment = v.ATTACHMENT_CONTACT if contact_id else v.ATTACHMENT_STANDALONE

    assignee = payload.get("assignee", payload.get("hubspot_owner_id"))
    if assignee is not None:
        assignee = str(assignee).strip() or None

    assignee_role = payload.get("assignee_role")
    if assignee_role is not None and not is_permitted_role(assignee_role):
        errors["assignee_role"] = f"must be one of {', '.join(ROLES_INCLUDING_OWNER)}"

    notify = bool(payload.get("notify", False))
    if notify and not assignee:
        errors["assignee"] = "an internal notification needs an assignee"

    if errors:
        raise TaskValidationError(errors)

    subject = str(payload.get("subject", payload.get("hs_task_subject")) or "").strip()
    if not subject:
        subject = (signal_defaults_map or {}).get("subject") or DEFAULT_SUBJECT

    body = str(payload.get("body", payload.get("hs_task_body")) or "").strip()

    return {
        "subject": subject,
        "body": body,
        "due_at_ms": due_at_ms,
        "due_at": to_iso(due_at_ms),
        "reminder_at_ms": reminder_at_ms,
        "reminder_at": to_iso(reminder_at_ms),
        "status": status,
        "priority": priority,
        "task_type": task_type,
        "contact_id": contact_id,
        "attachment": attachment,
        "assignee": assignee,
        "assignee_role": assignee_role,
        "notify": notify,
        "signal": signal,
        "source_signal": payload.get("source_signal"),
    }


def retention_cutoff_ms(now_ms: int, days: int = v.RECYCLING_BIN_RETENTION_DAYS) -> int:
    """The oldest a binned row may be before a purge removes it."""

    return now_ms - int(days) * 24 * 60 * 60 * 1000


__all__ = [
    "CONTACT_COLLECTION",
    "CONTACT_OBJECT",
    "DEFAULT_SUBJECT",
    "EPOCH",
    "SIGNAL_TASK_RULES",
    "coerce_choice",
    "is_known_signal",
    "is_permitted_role",
    "normalise_task_payload",
    "parse_timestamp",
    "retention_cutoff_ms",
    "signal_defaults",
    "signal_rule",
    "to_iso",
]
