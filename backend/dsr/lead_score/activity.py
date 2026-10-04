"""Reading a DSR activity event as one of the five researched Dock properties.

The source side is explicit: DSR engagement arrives as Views, Clicks, Downloads,
Interactions and MAP task activity, and the research's data flow puts all five on
the contact. So the unit this package evaluates is an *activity event* belonging to
a *contact*, in a room.

Why the shape is loose on purpose
---------------------------------

A webhook payload and this product's own ``activity`` rows are the same fact under
two vocabularies. Rather than pick one and refuse the other, every read here goes
through :data:`~dsr.lead_score.vocabulary.FIELD_ALIASES`: the event may call the
buyer ``person``, ``user``, ``buyer`` or ``contact_email``, and the timestamp
``occurred_at``, ``at``, ``timestamp`` or ``time``. A caller sending the vendor's
shape and a caller sending this product's shape both land in the same function.

When an alias resolves to nothing, the field is reported as **absent** rather than
defaulted to a plausible value, and the matcher turns that into
``refinement_unverifiable``. That distinction is the difference between "this
criterion does not match" and "this criterion cannot be checked", and collapsing the
two would let a criterion score a contact on evidence nobody supplied.

Nothing here writes. Storing an event is the engine's job, through the store, with
an audited source.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Mapping

from dsr.lead_score.errors import LeadScoreError
from dsr.lead_score.vocabulary import (
    FILTER_FAMILIES,
    family_for_action,
    first_present,
    pick,
)


class MalformedActivity(LeadScoreError):
    """A DSR activity event cannot be read as activity.

    Refused rather than stored-and-ignored: an event with no contact could not move
    any contact's score, and storing it would make the room's activity count disagree
    with the number of events that can do anything.
    """

    code = "malformed_activity"


#: The five properties, re-exported so "which properties exist" has one import site
#: rather than two places for it to change.
FAMILIES: tuple[str, ...] = FILTER_FAMILIES


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def parse_timestamp(value: Any) -> datetime | None:
    """Read a timestamp out of whatever a caller put in an arbitrary payload.

    Accepts an ISO string, a date, a ``datetime``, or a Unix epoch in seconds or
    milliseconds. A naive string is read as **UTC**, and the reason is worth stating:
    this product stores UTC and the research's own filters are date filters, so a
    naive local reading would move a buyer's event across a day boundary and score
    them on a filter that did not match.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, date):
        return datetime.combine(value, time.min, tzinfo=timezone.utc)
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        seconds = float(value)
        # Anything past this is milliseconds, not seconds: 1e12 seconds is the year
        # 33658, and no DSR event is from the future.
        if abs(seconds) > 1e11:
            seconds = seconds / 1000.0
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.endswith(("Z", "z")):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


def iso(value: datetime | None) -> str | None:
    return value.astimezone(timezone.utc).isoformat() if value else None


def _is_date_only(value: Any) -> bool:
    return isinstance(value, str) and len(value.strip()) == 10 and value.strip().count("-") == 2


def occurred_window(value: Any) -> tuple[datetime, datetime | None] | None:
    """Read a criterion's ``occurred`` value as a UTC window.

    Two shapes are accepted, because "filter by date" and the ``Occurred`` control in
    a CRM are both this:

    * a bare date or timestamp, which is that **whole UTC day**; and
    * ``{"from": ..., "to": ...}``, either endpoint of which may be absent for an
      open end.

    Returns ``(start, end)`` or ``None`` if nothing readable was given. A date-only
    upper bound includes the whole day, so ``{"from": "2026-09-01", "to":
    "2026-09-01"}`` means exactly that day rather than the single instant of midnight
    - the reading a person means, and the one that does not silently lose a day's
    events.
    """
    if value is None:
        return None
    if isinstance(value, Mapping):
        raw_from = value.get("from", value.get("start"))
        raw_to = value.get("to", value.get("end"))
        start = parse_timestamp(raw_from) if raw_from is not None else None
        end = parse_timestamp(raw_to) if raw_to is not None else None
        if start is None and end is None:
            return None
        if start is not None and end is not None and end < start:
            return None
        if end is not None and _is_date_only(raw_to):
            end = end + timedelta(days=1) - timedelta(microseconds=1)
        return start, end
    parsed = parse_timestamp(value)
    if parsed is None:
        return None
    if _is_date_only(value):
        return parsed, parsed + timedelta(days=1) - timedelta(microseconds=1)
    return parsed, None


def task_name_from_text(text: Any) -> str | None:
    """The task name inside a documented MAP activity text.

    The evidence gives two worked examples verbatim: ``completed task "Sign up for
    free account"`` and ``completed task "Intro call"``. Both are the same shape - a
    ``completed task '...'`` sentence with the name quoted - so the quoted name is
    what is extracted and compared, which is what makes ``task_name: "completed task
    'Intro call'"`` and ``task_name: "Intro call"`` name the same criterion. Text in
    any other shape is compared whole, so a filter naming a literal string this
    product did not document still works rather than being refused for being
    unfamiliar.
    """
    if not isinstance(text, str):
        return None
    stripped = text.strip()
    if not stripped:
        return None
    lowered = stripped.lower()
    for lead in ("completed task ", "completed section ", "completed plan task "):
        if lowered.startswith(lead):
            tail = stripped[len(lead) :].strip()
            for quote in ('"', "'", "\u201c", "\u201d"):
                if len(tail) >= 2 and tail[0] == quote and tail[-1] == quote:
                    return tail[1:-1]
            return tail
    return None


def criterion_task_name(value: Any) -> str | None:
    """The task name a ``task_name`` filter is asking for.

    The documented sentence and the bare name name the same thing, so both resolve to
    the task name. Text that is not in the documented shape resolves to itself, so a
    literal a team invented still compares as a literal instead of silently becoming
    a no-match against everything.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    extracted = task_name_from_text(value)
    return extracted if extracted is not None else " ".join(value.split()).strip()


def normalise_activity(
    payload: Mapping[str, Any] | None,
    *,
    room_id: str | None = None,
    record_id: str | None = None,
) -> dict[str, Any]:
    """Read an event into the shape every part of this package evaluates.

    Returns a dict with the resolved fields, the family, and a ``warnings`` list. It
    raises :class:`MalformedActivity` for exactly one thing: an event with no
    contact. The research ties every filterable activity to the contact record, so an
    event nobody owns could not move anybody's score, and storing it would make the
    room's activity count disagree with the number of events that can do anything.
    """
    if not isinstance(payload, Mapping):
        raise MalformedActivity("an activity event must be a JSON object")

    data: Mapping[str, Any] = (
        payload.get("data") if isinstance(payload.get("data"), Mapping) else payload
    )
    contact = pick(data, "contact")
    if not isinstance(contact, str) or not contact.strip():
        raise MalformedActivity(
            "an activity event must name a contact. The research ties every scoreable "
            "activity to the contact record and the score lands on a contact property, so "
            "an event with no contact could not move anybody's score."
        )
    contact = contact.strip()

    raw_action = pick(data, "action")
    action = str(raw_action).strip() if isinstance(raw_action, (str, int, float)) else ""
    action_word = action.lower()

    warnings: list[dict[str, Any]] = []
    family = family_for_action(action_word)

    asserted = payload.get("action_family")
    if isinstance(asserted, str) and asserted.strip():
        asserted_name = asserted.strip().lower()
        if asserted_name not in FAMILIES:
            warnings.append(
                {
                    "code": "unknown_asserted_family",
                    "severity": "warning",
                    "field": "action_family",
                    "message": (
                        f"action_family {asserted!r} is not one of the five published Dock "
                        f"properties ({', '.join(FAMILIES)}), so it is ignored."
                    ),
                }
            )
        else:
            if family and family != asserted_name:
                warnings.append(
                    {
                        "code": "family_assertion_conflicts",
                        "severity": "warning",
                        "field": "action_family",
                        "message": (
                            f"action {action!r} maps to {family!r} in this product's activity "
                            f"vocabulary, but the event asserts {asserted_name!r}. The "
                            f"assertion is used, because a webhook sender knows the vendor's "
                            f"taxonomy better than this table does."
                        ),
                    }
                )
            family = asserted_name

    if not action_word:
        warnings.append(
            {
                "code": "no_action",
                "severity": "warning",
                "field": "action",
                "message": (
                    "The event names no action, so it belongs to no Dock property and no "
                    "criterion can match it."
                ),
            }
        )
    elif family is None:
        warnings.append(
            {
                "code": "action_unclassified",
                "severity": "warning",
                "field": "action",
                "message": (
                    f"action {action!r} is not in the published action-to-property table, so "
                    f"this event belongs to no Dock property and no criterion can match it. "
                    f"The event is kept and the word named rather than the event being dropped."
                ),
            }
        )

    occurred = parse_timestamp(first_present(data, "occurred_at"))
    if occurred is None:
        warnings.append(
            {
                "code": "no_occurred_at",
                "severity": "warning",
                "field": "occurred_at",
                "message": (
                    "The event carries no readable timestamp, so a criterion filtered by "
                    "'Occurred' cannot be checked against it and will not match."
                ),
            }
        )

    target = pick(data, "target")
    link_name = pick(data, "link_name")
    file_name = pick(data, "file_name")
    raw_text = pick(data, "task_name")

    return {
        "id": record_id,
        "room_id": room_id,
        "contact": contact,
        "account": str(pick(data, "account")),
        "action": action or "unknown",
        "action_family": family,
        "target": str(target),
        "link_name": str(link_name) if link_name else None,
        "file_name": str(file_name) if file_name else None,
        "activity_text": str(raw_text) if raw_text else None,
        "task_name": task_name_from_text(raw_text),
        "occurred_at": iso(occurred),
        "idempotency_key": payload.get("idempotency_key"),
        "warnings": warnings,
        "data": dict(data),
    }
