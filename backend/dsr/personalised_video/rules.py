"""The workflow's pure rules. No store, no framework, no I/O.

Everything here is a function of its arguments, so the rules can be tested
without a database and read without following a call graph.

The rules that carry a judgement the research left open are named in
:data:`dsr.personalised_video.vocabulary.INFERENCES` rather than buried here:

1. A viewer counts as complete at :data:`COMPLETE_PERCENT` watched, or on an
   explicit ``complete`` event.
2. The rep is alerted below :data:`LOW_COMPLETION_PERCENT` watched, and only
   once the viewer has actually started.
3. One video per hero, and a second placement supersedes the first.
4. A recording may only be placed on the hero of its own room.
5. Personalising for a buyer requires a buyer the room already declares, unless
   the room declares nobody.
"""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from typing import Any, Mapping, Sequence

from dsr.personalised_video import vocabulary as v
from dsr.personalised_video.errors import VideoValidationError

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
        raise VideoValidationError({field: "expected a timestamp, not a boolean"})
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
            raise VideoValidationError(
                {field: f"{value!r} is not an ISO date or a number"}
            ) from exc
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return _ms_from_datetime(parsed)
    raise VideoValidationError({field: f"cannot read a timestamp from {type(value).__name__}"})


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


def _percent(value: Any, field: str, errors: dict[str, str]) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        errors[field] = "must be a number between 0 and 100"
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        errors[field] = "must be a number between 0 and 100"
        return None
    if number < 0 or number > 100:
        errors[field] = "must be between 0 and 100"
        return None
    return int(number) if float(number).is_integer() else number


def normalise_emails(value: Any) -> list[str]:
    """Read a buyer list written as a JSON array or a comma-separated string.

    Emails are lower-cased and de-duplicated in the order given, so the same
    buyer always compares equal whatever spelling the caller used. This mirrors
    WF-004, which is where the buyer identity comes from.
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
# Payload normalisation
# --------------------------------------------------------------------------- #


def validate_recording(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Read a create payload into the exact fields the store holds.

    Raises :class:`VideoValidationError` with one entry per bad field, so a page
    can mark every field at once rather than one per round trip.
    """

    payload = dict(payload or {})
    errors: dict[str, str] = {}

    title = _text(payload.get("title"), "title", errors, required=True)
    kind = coerce_choice(payload.get("kind"), v.KINDS, "kind", errors, default=v.DEFAULT_KIND)
    source = coerce_choice(
        payload.get("source"), v.CAPTURE_SOURCES, "source", errors, default=v.DEFAULT_SOURCE
    )
    duration = _whole_number(payload.get("duration_seconds"), "duration_seconds", errors, minimum=0)
    media_ref = _text(payload.get("media_ref"), "media_ref", errors)
    stream_ref = _text(payload.get("stream_ref"), "stream_ref", errors)
    note = _text(payload.get("note"), "note", errors)
    recorded_by = _text(payload.get("recorded_by"), "recorded_by", errors)
    room_id = _text(payload.get("room_id"), "room_id", errors)

    if errors:
        raise VideoValidationError(errors)

    return {
        "title": title,
        "kind": kind,
        "source": source,
        "duration_seconds": duration,
        "media_ref": media_ref,
        "stream_ref": stream_ref,
        "note": note,
        "recorded_by": recorded_by,
        "room_id": room_id,
    }


def validate_playback(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Read a playback event into the exact fields the store holds."""

    payload = dict(payload or {})
    errors: dict[str, str] = {}

    event = coerce_choice(payload.get("event"), v.EVENTS, "event", errors)
    if event is None and payload.get("event") in (None, ""):
        errors["event"] = f"is required; one of {', '.join(v.EVENTS)}"
    viewer = _text(payload.get("viewer"), "viewer", errors, required=True)
    if viewer:
        # The buyer identity comes from WF-004, where an email is compared
        # case-insensitively. Lower-casing here keeps one spelling in the store.
        viewer = viewer.lower()
    watched = _percent(payload.get("watched_percent"), "watched_percent", errors)
    position = _whole_number(payload.get("position_seconds"), "position_seconds", errors, minimum=0)
    placement_id = _text(payload.get("placement_id"), "placement_id", errors)

    if event == v.EVENT_PROGRESS and watched is None and "watched_percent" not in errors:
        errors["watched_percent"] = "is required for a progress event"
    if event == v.EVENT_COMPLETE and watched is None and "watched_percent" not in errors:
        # A completion with no percentage is a completion; it defaults to 100.
        watched = 100

    if errors:
        raise VideoValidationError(errors)

    return {
        "event": event,
        "viewer": viewer,
        "watched_percent": watched,
        "position_seconds": position,
        "placement_id": placement_id,
    }


# --------------------------------------------------------------------------- #
# Engagement: the roll-up and the two thresholds
# --------------------------------------------------------------------------- #


def blank_engagement(recording_id: str, viewer: str) -> dict[str, Any]:
    """The roll-up a viewer starts from before any event arrives."""

    return {
        "recording_id": recording_id,
        "viewer": viewer,
        "events": 0,
        "starts": 0,
        "progress_events": 0,
        "completes": 0,
        "replays": 0,
        "max_percent": 0,
        "last_position_seconds": None,
        "first_at_ms": None,
        "last_at_ms": None,
        "completed": False,
    }


def apply_event(
    engagement: Mapping[str, Any], event: Mapping[str, Any], at_ms: int
) -> dict[str, Any]:
    """Fold one playback event into a roll-up and return the new roll-up.

    Pure: the input is not mutated, so a caller can replay the same event twice
    and see the same result.
    """

    result = dict(engagement)
    kind = event.get("event")
    result["events"] = int(result.get("events") or 0) + 1
    result["last_at_ms"] = at_ms
    if result.get("first_at_ms") is None:
        result["first_at_ms"] = at_ms

    percent = event.get("watched_percent")
    if percent is not None:
        result["max_percent"] = max(float(result.get("max_percent") or 0), float(percent))

    position = event.get("position_seconds")
    if position is not None:
        result["last_position_seconds"] = position

    if kind == v.EVENT_START:
        result["starts"] = int(result.get("starts") or 0) + 1
    elif kind == v.EVENT_PROGRESS:
        result["progress_events"] = int(result.get("progress_events") or 0) + 1
    elif kind == v.EVENT_COMPLETE:
        result["completes"] = int(result.get("completes") or 0) + 1
        result["completed"] = True
        result["max_percent"] = max(
            float(result.get("max_percent") or 0), float(v.COMPLETE_PERCENT)
        )
    elif kind == v.EVENT_REPLAY:
        result["replays"] = int(result.get("replays") or 0) + 1

    # A percentage at or above the completion threshold completes the video even
    # without an explicit complete event. Recorded as an inference.
    if float(result.get("max_percent") or 0) >= v.COMPLETE_PERCENT:
        result["completed"] = True
    return result


def engagement_state(engagement: Mapping[str, Any]) -> str:
    """The three-state view of a roll-up: not started, started, completed."""

    if engagement.get("completed"):
        return v.STATE_COMPLETED
    if int(engagement.get("events") or 0) > 0 or engagement.get("first_at_ms") is not None:
        return v.STATE_STARTED
    return v.STATE_NOT_STARTED


def watched_percent(engagement: Mapping[str, Any]) -> float:
    return float(engagement.get("max_percent") or 0)


def should_alert(engagement: Mapping[str, Any], threshold: int | None = None) -> bool:
    """Is this roll-up a low-completion signal worth alerting the rep about?

    A viewer who has not started is *not* alerted: the research's automation is
    "alert the rep on low completion", and completion is not yet possible. A
    viewer whose percentage is above the threshold is not alerted either. So the
    check needs a real percentage below the threshold on a started viewer.
    """

    limit = v.LOW_COMPLETION_PERCENT if threshold is None else int(threshold)
    percent = watched_percent(engagement)
    return engagement_state(engagement) == v.STATE_STARTED and 0 < percent < limit


# --------------------------------------------------------------------------- #
# The viewer-visible personalisation message
# --------------------------------------------------------------------------- #


def personalisation_message(
    recording: Mapping[str, Any], placement: Mapping[str, Any] | None = None
):
    """The "why I'm reaching out" line a buyer reads above the video.

    The rep's own note wins, because the user flow says the seller's message is
    the point. When there is no note the line is generated from the recording,
    so the buyer still sees a reason rather than an empty band.
    """

    note = None
    if placement is not None:
        note = (placement.get("data") or {}).get("note") if placement.get("collection") else None
        note = note or placement.get("note")
    if not note:
        note = (recording.get("data") or {}).get("note") if recording.get("collection") else None
        note = note or recording.get("note")
    if note:
        return str(note)
    title = (recording.get("data") or {}).get("title") if recording.get("collection") else None
    title = title or recording.get("title") or "this walkthrough"
    return f"A short message from your account team about {title}."


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


def recording_view(record: Mapping[str, Any]) -> dict[str, Any]:
    data = dict(record.get("data") or {})
    return {
        **_envelope(record),
        "title": data.get("title"),
        "kind": data.get("kind"),
        "kind_label": v.KIND_LABELS.get(str(data.get("kind")), str(data.get("kind"))),
        "source": data.get("source"),
        "source_label": v.SOURCE_LABELS.get(str(data.get("source")), str(data.get("source"))),
        "duration_seconds": data.get("duration_seconds"),
        "media_ref": data.get("media_ref"),
        "stream_ref": data.get("stream_ref"),
        "note": data.get("note"),
        "recorded_by": data.get("recorded_by"),
        "transcode": data.get("transcode") or {},
        "status": data.get("status"),
        "placed_at_ms": data.get("placed_at_ms"),
        "published_at_ms": data.get("published_at_ms"),
        "created_at_ms": data.get("created_at_ms"),
        "updated_at_ms": data.get("updated_at_ms"),
        # The media reference is opaque and the stream URL is a stand-in. The
        # view says so rather than implying a working pipeline.
        "hosted": False,
        "hosting_note": "Media hosting, transcode and streaming are stand-ins: no object store exists in this build.",
    }


def placement_view(record: Mapping[str, Any]) -> dict[str, Any]:
    data = dict(record.get("data") or {})
    status = str(data.get("status"))
    return {
        **_envelope(record),
        "recording_id": data.get("recording_id"),
        "surface": data.get("surface"),
        "page_id": data.get("page_id"),
        "status": status,
        "status_label": v.PLACEMENT_STATUS_LABELS.get(status, status),
        "personalised_for": list(data.get("personalised_for") or []),
        "note": data.get("note"),
        "player": dict(data.get("player") or {}),
        "placed_by": data.get("placed_by"),
        "placed_at_ms": data.get("placed_at_ms"),
        "placed_at": data.get("placed_at"),
        "active": status == v.PLACEMENT_LIVE,
    }


def event_view(record: Mapping[str, Any]) -> dict[str, Any]:
    data = dict(record.get("data") or {})
    kind = str(data.get("event"))
    return {
        **_envelope(record),
        "recording_id": data.get("recording_id"),
        "placement_id": data.get("placement_id"),
        "viewer": data.get("viewer"),
        "event": kind,
        "event_label": v.EVENT_LABELS.get(kind, kind),
        "watched_percent": data.get("watched_percent"),
        "position_seconds": data.get("position_seconds"),
        "at_ms": data.get("at_ms"),
        "at": data.get("at"),
    }


def engagement_view(record: Mapping[str, Any]) -> dict[str, Any]:
    data = dict(record.get("data") or {})
    state = engagement_state(data)
    return {
        **_envelope(record),
        "recording_id": data.get("recording_id"),
        "viewer": data.get("viewer"),
        "state": state,
        "state_label": v.STATE_LABELS.get(state, state),
        "events": int(data.get("events") or 0),
        "max_percent": watched_percent(data),
        "completed": bool(data.get("completed")),
        "completes": int(data.get("completes") or 0),
        "replays": int(data.get("replays") or 0),
        "last_position_seconds": data.get("last_position_seconds"),
        "first_at_ms": data.get("first_at_ms"),
        "last_at_ms": data.get("last_at_ms"),
        "low_completion": should_alert(data),
    }


def alert_view(record: Mapping[str, Any]) -> dict[str, Any]:
    data = dict(record.get("data") or {})
    return {
        **_envelope(record),
        "recording_id": data.get("recording_id"),
        "placement_id": data.get("placement_id"),
        "viewer": data.get("viewer"),
        "status": data.get("status"),
        "reason": data.get("reason"),
        "max_percent": data.get("max_percent"),
        "threshold": data.get("threshold"),
        "opened_at_ms": data.get("opened_at_ms"),
        "opened_at": data.get("opened_at"),
        "cleared_at_ms": data.get("cleared_at_ms"),
        "cleared_at": data.get("cleared_at"),
        "message": data.get("message"),
    }


def notification_view(record: Mapping[str, Any]) -> dict[str, Any]:
    data = dict(record.get("data") or {})
    return {
        **_envelope(record),
        "recording_id": data.get("recording_id"),
        "placement_id": data.get("placement_id"),
        "recipient": data.get("recipient"),
        "channel": data.get("channel"),
        "message": data.get("message"),
        "at_ms": data.get("at_ms"),
        "at": data.get("at"),
    }


__all__ = [
    "EPOCH",
    "alert_view",
    "apply_event",
    "blank_engagement",
    "coerce_choice",
    "engagement_state",
    "engagement_view",
    "event_view",
    "notification_view",
    "normalise_emails",
    "parse_timestamp",
    "personalisation_message",
    "placement_view",
    "recording_view",
    "should_alert",
    "to_iso",
    "validate_playback",
    "validate_recording",
    "watched_percent",
]
