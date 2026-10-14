"""The workflow's pure rules. No store, no framework, no I/O.

Everything here is a function of its arguments, so the rules can be tested
without a database and read without following a call graph.

The rules that carry a judgement the research left open are named in
:data:`dsr.live_sessions.vocabulary.INFERENCES` rather than buried here:

1. No realtime transport exists, so the presenter's cursor is state a follower
   polls. Sync is recorded state.
2. A session has exactly one presenter.
3. The presenter reveals the agenda one step at a time forward, and may step
   back to any earlier step. A forward jump reveals out of order and is refused.
4. The agenda is the room's own pages; a room with no pages gets one screen step.
5. Attendance and questions are written to the room record, not to a CRM.
6. A participant counts as attended when they join at all.
7. The recording reference is opaque and the playback URL is a stand-in.
8. With no explicit invitees, the invitees are the room's recipients.
"""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from typing import Any, Mapping, Sequence

from dsr.live_sessions import vocabulary as v
from dsr.live_sessions.errors import (
    REASON_FOLLOWER_CANNOT_CONTROL,
    REASON_NOT_PRESENTER,
    REASON_OUT_OF_RANGE,
    REASON_SKIPPED_REVEAL,
    SessionValidationError,
)

EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


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
        raise SessionValidationError({field: "expected a timestamp, not a boolean"})
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
            raise SessionValidationError(
                {field: f"{value!r} is not an ISO date or a number"}
            ) from exc
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return _ms_from_datetime(parsed)
    raise SessionValidationError({field: f"cannot read a timestamp from {type(value).__name__}"})


def to_iso(milliseconds: int | None) -> str | None:
    """The ISO 8601 copy stored beside a millisecond value, for a reader."""

    if milliseconds is None:
        return None
    return datetime.fromtimestamp(milliseconds / 1000, tz=timezone.utc).isoformat(
        timespec="milliseconds"
    )


# --------------------------------------------------------------------------- #
# Small readers over a caller's payload
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


def _text(
    value: Any,
    field: str,
    errors: dict[str, str],
    *,
    required: bool = False,
) -> str | None:
    if value is None:
        if required:
            errors[field] = "is required"
        return None
    text = str(value).strip()
    if not text:
        if required:
            errors[field] = "is required"
        return None
    return text


def _whole_number(
    value: Any,
    field: str,
    errors: dict[str, str],
    *,
    minimum: int = 0,
    maximum: int | None = None,
) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        errors[field] = "must be a whole number"
        return None
    if isinstance(value, float) and not float(value).is_integer():
        errors[field] = "must be a whole number"
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        errors[field] = "must be a whole number"
        return None
    if number < minimum:
        errors[field] = f"must be at least {minimum}"
        return None
    if maximum is not None and number > maximum:
        errors[field] = f"must be at most {maximum}"
        return None
    return number


def normalise_emails(value: Any) -> list[str]:
    """Read a buyer list written as a JSON array or a comma-separated string.

    Emails are lower-cased and de-duplicated in the order given, so the same
    buyer always compares equal whatever spelling the caller used. This mirrors
    WF-004, which is where the invitee identity comes from.
    """

    if value is None or value == "":
        return []
    if isinstance(value, str):
        parts = [part.strip() for part in value.split(",")]
    elif isinstance(value, (list, tuple, set)):
        parts = [str(part).strip() for part in value]
    else:
        parts = [str(value).strip()]
    out: list[str] = []
    for part in parts:
        if not part:
            continue
        email = part.lower()
        if email not in out:
            out.append(email)
    return out


# --------------------------------------------------------------------------- #
# The agenda
# --------------------------------------------------------------------------- #


def plan_agenda(pages: Sequence[Mapping[str, Any]] | None) -> list[dict[str, Any]]:
    """Build the reveal order from the room's own pages.

    "follows the presenter through the deck or the actual product". The deck is
    the room's pages (WF-002), so the steps are its pages in the order given. A
    room with no pages gets one screen step, so a session always has something
    to reveal and start never fails on an empty agenda.
    """

    steps: list[dict[str, Any]] = []
    for page in pages or []:
        data = dict(page.get("data") or {})
        label = data.get("title") or data.get("slug") or "Room page"
        steps.append(
            {
                "index": len(steps),
                "kind": v.STEP_PAGE,
                "ref": str(page.get("id")),
                "label": str(label),
            }
        )
    if not steps:
        steps.append(
            {
                "index": 0,
                "kind": v.STEP_SCREEN,
                "ref": "screen",
                "label": v.SCREEN_STEP_LABEL,
            }
        )
    return steps


def step_at(agenda: Sequence[Mapping[str, Any]], position: int) -> dict[str, Any] | None:
    """The step at ``position``, or ``None`` when the position is off the end."""

    if not agenda:
        return None
    if 0 <= position < len(agenda):
        return dict(agenda[position])
    return None


def reveal_decision(*, role: str, current: int, target: int, last: int) -> tuple[bool, str | None]:
    """Can this participant move the reveal cursor to ``target``?

    "a reveal order is respected". Only the presenter controls the cursor (a
    follower cannot drive at all). The presenter moves forward by at most
    :data:`dsr.live_sessions.vocabulary.REVEAL_STEP` step, and may move back to
    any earlier step. A forward jump would reveal a step before an earlier one,
    so it is refused with a stable token rather than silently clipped.
    """

    if role != v.ROLE_PRESENTER:
        return False, REASON_NOT_PRESENTER
    if target < 0 or target > last:
        return False, REASON_OUT_OF_RANGE
    if target > current + v.REVEAL_STEP:
        return False, REASON_SKIPPED_REVEAL
    return True, None


def follower_can_control() -> tuple[bool, str]:
    """A follower may never move the cursor. Named so the reason is not a literal."""

    return False, REASON_FOLLOWER_CANNOT_CONTROL


# --------------------------------------------------------------------------- #
# Payload normalisation
# --------------------------------------------------------------------------- #


def validate_session(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Read a create payload into the exact fields the store holds.

    Raises :class:`SessionValidationError` with one entry per bad field, so a
    page can mark every field at once rather than one per round trip.
    """

    payload = dict(payload or {})
    errors: dict[str, str] = {}

    room_id = _text(payload.get("room_id"), "room_id", errors, required=True)
    title = _text(payload.get("title"), "title", errors)
    presenter = _text(payload.get("presenter"), "presenter", errors)
    if presenter:
        presenter = presenter.lower()
    invitees = normalise_emails(payload.get("invitees"))
    scheduled_for = parse_timestamp(payload.get("scheduled_for"), "scheduled_for")

    if errors:
        raise SessionValidationError(errors)

    return {
        "room_id": room_id,
        "title": title,
        "presenter": presenter,
        "invitees": invitees,
        "scheduled_for_ms": scheduled_for,
    }


def validate_invite(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Read an invite payload into the fields a participant row holds."""

    payload = dict(payload or {})
    errors: dict[str, str] = {}

    email = _text(payload.get("email"), "email", errors, required=True)
    if email:
        email = email.lower()
    role = coerce_choice(
        payload.get("role"), v.PARTICIPANT_ROLES, "role", errors, default=v.ROLE_FOLLOWER
    )
    name = _text(payload.get("name"), "name", errors)

    if errors:
        raise SessionValidationError(errors)

    return {"email": email, "role": role, "name": name}


def validate_question(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Read a question payload into the fields a question row holds."""

    payload = dict(payload or {})
    errors: dict[str, str] = {}

    asker = _text(payload.get("asker"), "asker", errors, required=True)
    if asker:
        asker = asker.lower()
    text = _text(payload.get("text"), "text", errors, required=True)

    if errors:
        raise SessionValidationError(errors)

    return {"asker": asker, "text": text}


def validate_answer(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Read an answer payload into the fields an answered question holds."""

    payload = dict(payload or {})
    errors: dict[str, str] = {}
    text = _text(payload.get("answer") or payload.get("text"), "answer", errors, required=True)
    if errors:
        raise SessionValidationError(errors)
    return {"answer": text}


def validate_move(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Read a reveal move. ``position`` is the step index to move to."""

    payload = dict(payload or {})
    errors: dict[str, str] = {}
    position = _whole_number(payload.get("position"), "position", errors, minimum=0)
    if position is None and "position" not in errors:
        errors["position"] = "is required"
    if errors:
        raise SessionValidationError(errors)
    return {"position": position}


# --------------------------------------------------------------------------- #
# Attendance
# --------------------------------------------------------------------------- #


def attendance_state(joined_at_ms: int | None) -> str:
    """A participant counts as attended when they join at all.

    "per-participant attendance". The research names no minimum dwell, so none is
    invented; :data:`dsr.live_sessions.vocabulary.INFERENCES` records the choice.
    """

    return v.ATTENDANCE_ATTENDED if joined_at_ms is not None else v.ATTENDANCE_INVITED_ONLY


def attended_seconds(joined_at_ms: int | None, left_at_ms: int | None, now_ms: int) -> int:
    """The seconds a participant was present, floored at zero.

    A participant who has not joined counts zero. A participant who has not left
    is counted up to ``now_ms``, so a live row still answers "how long so far".
    """

    if joined_at_ms is None:
        return 0
    end = left_at_ms if left_at_ms is not None else now_ms
    return max(0, int(round((end - joined_at_ms) / 1000)))


# --------------------------------------------------------------------------- #
# Links
# --------------------------------------------------------------------------- #


def join_url(token: str) -> str:
    """A stand-in per-participant join link.

    "a whole buying committee, each with their own link". One token per
    participant, so one link per participant. No host serves this path: the real
    access rules belong to the link-gating and revocation workflows, which this
    feature does not edit.
    """

    return f"{v.JOIN_PATH}/{token}"


def replay_url(session_id: str) -> str:
    """A stand-in replay URL. ``.invalid`` is reserved, so it never resolves."""

    return f"{v.REPLAY_HOST}/{session_id}.m3u8"


# --------------------------------------------------------------------------- #
# Record views
# --------------------------------------------------------------------------- #


def _envelope(record: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": record.get("id"),
        "room_id": record.get("room_id"),
        "revision": record.get("revision"),
        "created_at": record.get("created_at"),
        "updated_at": record.get("updated_at"),
    }


def session_view(record: Mapping[str, Any]) -> dict[str, Any]:
    data = dict(record.get("data") or {})
    state = str(data.get("state"))
    agenda = list(data.get("agenda") or [])
    position = int(data.get("position") or 0)
    current = step_at(agenda, position)
    return {
        **_envelope(record),
        "title": data.get("title"),
        "state": state,
        "state_label": v.SESSION_STATE_LABELS.get(state, state),
        "presenter": data.get("presenter"),
        "agenda": agenda,
        "step_count": len(agenda),
        "position": position,
        "current_step": current,
        "revealed": int(data.get("revealed") or 0),
        "deal_ref": data.get(v.DEAL_FIELD),
        "transport": data.get("transport") or v.TRANSPORT_STAND_IN,
        "scheduled_for_ms": data.get("scheduled_for_ms"),
        "started_at_ms": data.get("started_at_ms"),
        "started_at": data.get("started_at"),
        "ended_at_ms": data.get("ended_at_ms"),
        "ended_at": data.get("ended_at"),
        "created_at_ms": data.get("created_at_ms"),
        "recording_id": data.get("recording_id"),
        "live": state == v.STATE_LIVE,
        "ended": state == v.STATE_ENDED,
        # No SFU and no stream exist. The view says so rather than implying one.
        "streamed": False,
        "transport_note": v.TRANSPORT_NOTE,
    }


def participant_view(record: Mapping[str, Any]) -> dict[str, Any]:
    data = dict(record.get("data") or {})
    state = str(data.get("status"))
    role = str(data.get("role"))
    return {
        **_envelope(record),
        "session_id": data.get("session_id"),
        "email": data.get("email"),
        "name": data.get("name"),
        "role": role,
        "role_label": v.PARTICIPANT_ROLE_LABELS.get(role, role),
        "status": state,
        "status_label": v.PARTICIPANT_STATE_LABELS.get(state, state),
        "join_token": data.get("join_token"),
        "join_url": data.get("join_url"),
        "joined_at_ms": data.get("joined_at_ms"),
        "joined_at": data.get("joined_at"),
        "left_at_ms": data.get("left_at_ms"),
        "left_at": data.get("left_at"),
        "last_seen_position": data.get("last_seen_position"),
        "last_seen_at_ms": data.get("last_seen_at_ms"),
        "link_issued": bool(data.get("join_token")),
    }


def event_view(record: Mapping[str, Any]) -> dict[str, Any]:
    data = dict(record.get("data") or {})
    kind = str(data.get("kind"))
    return {
        **_envelope(record),
        "session_id": data.get("session_id"),
        "kind": kind,
        "kind_label": v.EVENT_LABELS.get(kind, kind),
        "actor": data.get("actor"),
        "subject": data.get("subject"),
        "detail": data.get("detail"),
        "position": data.get("position"),
        "at_ms": data.get("at_ms"),
        "at": data.get("at"),
    }


def question_view(record: Mapping[str, Any]) -> dict[str, Any]:
    data = dict(record.get("data") or {})
    state = str(data.get("status"))
    return {
        **_envelope(record),
        "session_id": data.get("session_id"),
        "asker": data.get("asker"),
        "text": data.get("text"),
        "status": state,
        "status_label": v.QUESTION_STATE_LABELS.get(state, state),
        "answer": data.get("answer"),
        "asked_at_ms": data.get("asked_at_ms"),
        "asked_at": data.get("asked_at"),
        "answered_at_ms": data.get("answered_at_ms"),
        "answered_at": data.get("answered_at"),
    }


def attendance_view(record: Mapping[str, Any]) -> dict[str, Any]:
    data = dict(record.get("data") or {})
    state = str(data.get("state"))
    return {
        **_envelope(record),
        "session_id": data.get("session_id"),
        "participant_id": data.get("participant_id"),
        "email": data.get("email"),
        "role": data.get("role"),
        "state": state,
        "state_label": v.ATTENDANCE_STATE_LABELS.get(state, state),
        "joined_at_ms": data.get("joined_at_ms"),
        "joined_at": data.get("joined_at"),
        "left_at_ms": data.get("left_at_ms"),
        "left_at": data.get("left_at"),
        "seconds": data.get("seconds"),
        "attended": state == v.ATTENDANCE_ATTENDED,
        "written_to": data.get("written_to"),
    }


def recording_view(record: Mapping[str, Any]) -> dict[str, Any]:
    data = dict(record.get("data") or {})
    state = str(data.get("status"))
    return {
        **_envelope(record),
        "session_id": data.get("session_id"),
        "status": state,
        "status_label": v.RECORDING_STATE_LABELS.get(state, state),
        "replay_ref": data.get("replay_ref"),
        "playback_url": data.get("playback_url"),
        "duration_seconds": data.get("duration_seconds"),
        "started_at_ms": data.get("started_at_ms"),
        "published_at_ms": data.get("published_at_ms"),
        "published_at": data.get("published_at"),
        "artefact_store": v.ARTEFACT_STORE_NOTE,
        # The artefact is binary and no object store exists in this build.
        "hosted": False,
        "hosting_note": "No media is hosted: the reference and the replay URL are stand-ins.",
    }


def notification_view(record: Mapping[str, Any]) -> dict[str, Any]:
    data = dict(record.get("data") or {})
    return {
        **_envelope(record),
        "session_id": data.get("session_id"),
        "participant_id": data.get("participant_id"),
        "recipient": data.get("recipient"),
        "kind": data.get("kind"),
        "channel": data.get("channel"),
        "message": data.get("message"),
        "at_ms": data.get("at_ms"),
        "at": data.get("at"),
    }


__all__ = [
    "EPOCH",
    "attendance_state",
    "attendance_view",
    "attended_seconds",
    "coerce_choice",
    "event_view",
    "follower_can_control",
    "join_url",
    "notification_view",
    "normalise_emails",
    "parse_timestamp",
    "participant_view",
    "plan_agenda",
    "question_view",
    "recording_view",
    "replay_url",
    "reveal_decision",
    "session_view",
    "step_at",
    "to_iso",
    "validate_answer",
    "validate_invite",
    "validate_move",
    "validate_question",
    "validate_session",
]
