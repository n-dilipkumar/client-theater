"""Per-Meeting-Type configuration for WF-064.

Step 2 of the researched flow says a reschedule "re-opens the same
Distribution/Meeting Type context", and the research carries two settings that
live on that context rather than on a booking:

* ``Expire Reschedule Link`` - "allows you to decide if the reschedule link
  should expire after a meeting has happened".
* ``Delete Event`` - "You can define if the Salesforce Event will be deleted if
  the meeting is canceled from the Dashboard or deleted from your calendar
  provider."

Both are booleans an administrator sets, so both are validated here and both
default to a value this build had to choose. The choices are named inferences
(``expire-reschedule-link-default`` and ``delete-event-default`` in
:mod:`dsr.scheduling.inferences`), not quiet constants: this build is allowed to
choose, and a reader is entitled to know it did.
"""

from __future__ import annotations

from typing import Any, Mapping

from dsr.scheduling.errors import MeetingChangeError
from dsr.scheduling.vocabulary import DELETE_EVENT, EXPIRE_RESCHEDULE_LINK

#: The two settings, and what each one is worth when a meeting type does not say.
#:
#: Both are inferences, both are named, and both live here rather than in the
#: engine so there is exactly one place to change either of them.
DEFAULT_EXPIRE_RESCHEDULE_LINK = False
DEFAULT_DELETE_EVENT = True

#: Meeting length, in minutes, when a meeting type does not say. 30 is the most
#: common default demo length and nothing in the research contradicts it; the
#: inference registry says so.
DEFAULT_DURATION_MINUTES = 30

#: A booking longer than this is a typo rather than a workshop, and a booking of
#: zero minutes would make every slot a zero-length slot.
MIN_DURATION_MINUTES = 5
MAX_DURATION_MINUTES = 8 * 60

#: How far ahead a booking may be moved. Named, bounded, and an inference: the
#: research has nothing to say about a horizon, and an unbounded one lets a
#: booking be moved to a year out, which no scheduling product means by
#: "reschedule".
MAX_RESCHEDULE_HORIZON_DAYS = 400

#: The default window, in minutes from midnight, used when a meeting type
#: declares no availability of its own. 09:00 to 17:00 UTC, on weekdays.
DEFAULT_BUSINESS_HOURS = (9 * 60, 17 * 60)
DEFAULT_BUSINESS_DAYS = (0, 1, 2, 3, 4)


def _clean_email(value: Any, label: str) -> str:
    text = str(value or "").strip().lower()
    if not text:
        raise MeetingChangeError(f"{label} is required")
    if "@" not in text or text.startswith("@") or text.endswith("@") or " " in text:
        raise MeetingChangeError(f"{label} must be an email address; got {value!r}")
    return text


def _clean_name(value: Any, label: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise MeetingChangeError(f"{label} is required")
    if len(text) > 200:
        raise MeetingChangeError(f"{label} must be 200 characters or fewer")
    return text


def _clean_duration(value: Any) -> int:
    if value in (None, ""):
        return DEFAULT_DURATION_MINUTES
    try:
        minutes = int(value)
    except (TypeError, ValueError) as exc:
        raise MeetingChangeError(f"duration_minutes must be a whole number of minutes; got {value!r}") from exc
    if minutes < MIN_DURATION_MINUTES or minutes > MAX_DURATION_MINUTES:
        raise MeetingChangeError(
            f"duration_minutes must be between {MIN_DURATION_MINUTES} and {MAX_DURATION_MINUTES}; got {minutes}"
        )
    return minutes


def _clean_window(value: Any, label: str) -> tuple[int, int]:
    """Accept ``"09:00-17:00"`` or ``{"start": 540, "end": 1020}``.

    Both spellings because a team configuring availability from a form writes the
    first and a team importing it writes the second, and this package is not the
    place to make that choice for them.
    """
    if value in (None, ""):
        raise MeetingChangeError(f"{label} is required")
    if isinstance(value, Mapping):
        start, end = value.get("start"), value.get("end")
    else:
        text = str(value).strip()
        if "-" not in text:
            raise MeetingChangeError(f"{label} must look like 09:00-17:00; got {value!r}")
        left, _, right = text.partition("-")
        start, end = left.strip(), right.strip()

    def minutes_of(stamp: Any) -> int:
        text = str(stamp).strip()
        if ":" not in text:
            try:
                return int(text)
            except ValueError as exc:
                raise MeetingChangeError(f"{label} has an unreadable time: {stamp!r}") from exc
        hours, _, minutes = text.partition(":")
        try:
            return int(hours) * 60 + int(minutes or 0)
        except ValueError as exc:
            raise MeetingChangeError(f"{label} has an unreadable time: {stamp!r}") from exc

    start_minute, end_minute = minutes_of(start), minutes_of(end)
    if not 0 <= start_minute < end_minute <= 24 * 60:
        raise MeetingChangeError(f"{label} must run forwards inside a single day; got {value!r}")
    return start_minute, end_minute


def clean_day(value: Any) -> int:
    """A weekday, 0 = Monday, matching ISO ordering.

    ISO rather than ``date.weekday`` naming conventions elsewhere in the stack:
    the value is stored in ``data`` and read back by whoever wants to configure
    availability, so it needs a documented order rather than a Python one.
    """
    try:
        day = int(value)
    except (TypeError, ValueError) as exc:
        raise MeetingChangeError(f"day must be 0 (Monday) to 6 (Sunday); got {value!r}") from exc
    if not 0 <= day <= 6:
        raise MeetingChangeError(f"day must be 0 (Monday) to 6 (Sunday); got {value!r}")
    return day


def normalise_meeting_type(spec: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and complete a Meeting Type.

    Every field is optional except the name and the host, so a team adding a field
    later ships a record rather than a code change - which is the whole point of
    storing payloads as open JSON. What comes back is the stored shape: no id, no
    envelope.
    """
    body = dict(spec or {})

    if not str(body.get("name") or "").strip():
        raise MeetingChangeError("a meeting type needs a name")

    payload: dict[str, Any] = {
        "name": _clean_name(body.get("name"), "name"),
        "host_email": _clean_email(body.get("host_email"), "host_email"),
        "duration_minutes": _clean_duration(body.get("duration_minutes")),
    }

    # The Distribution is the named context a reschedule re-opens, so it is kept
    # verbatim: the research names it as a thing that exists, and this package
    # does not get to decide what values it may take.
    distribution = str(body.get("distribution") or "").strip()
    if distribution:
        payload["distribution"] = distribution
    else:
        payload["distribution"] = payload["name"]

    for optional in ("slug", "timezone", "calendar_provider"):
        text = str(body.get(optional) or "").strip()
        if text:
            payload[optional] = text

    if not payload.get("timezone"):
        payload["timezone"] = "UTC"

    # The two researched settings. `in` so a false is respected rather than
    # replaced by the default.
    payload[EXPIRE_RESCHEDULE_LINK] = _clean_flag(
        body, EXPIRE_RESCHEDULE_LINK, DEFAULT_EXPIRE_RESCHEDULE_LINK
    )
    payload[DELETE_EVENT] = _clean_flag(body, DELETE_EVENT, DEFAULT_DELETE_EVENT)

    # Availability: a per-type weekly window, falling back to a business week.
    window = body.get("hours")
    if window not in (None, "", []):
        payload["hours"] = {
            "start": _clean_window(window, "hours")[0],
            "end": _clean_window(window, "hours")[1],
        }
    days = body.get("days")
    if days not in (None, "", []):
        cleaned = [clean_day(day) for day in days]
        payload["days"] = sorted(set(cleaned))
    else:
        payload["days"] = list(DEFAULT_BUSINESS_DAYS)

    # Two pass-through configuration fields, validated here rather than trusted.
    # Both reach :mod:`dsr.scheduling.propagation`, and both are stored in
    # `data` like everything else: a team that wants a fourth notification channel
    # ships a record and a vocabulary entry, not a migration.
    if "notify_channels" in body and body["notify_channels"] not in (None, "", []):
        from dsr.scheduling.propagation import channels_for

        # Validated against the body, not the half-built payload: the payload has
        # no `notify_channels` yet at this point, so checking it there would read
        # the field as absent and fall back to the default - turning a misspelled
        # channel into a silent "both channels", which is the opposite of what a
        # validation is for.
        payload["notify_channels"] = channels_for(dict(body))
    if "reminder_offsets" in body and body["reminder_offsets"] not in (None, "", []):
        payload["reminder_offsets"] = _clean_offsets(body["reminder_offsets"])

    for optional in ("title_template",):
        text = str(body.get(optional) or "").strip()
        if text:
            payload[optional] = text

    return payload


def _clean_offsets(value: Any) -> list[int]:
    """Reminder offsets in minutes before the meeting.

    A non-integer or negative offset is refused rather than coerced: a reminder
    scheduled *after* the meeting it belongs to is a reminder that fires while
    nobody is in the room, and a coerced zero would turn it into one that fires
    exactly on time - which reads as working.
    """
    if not isinstance(value, (list, tuple)):
        raise MeetingChangeError("reminder_offsets must be a list of whole minutes")
    offsets: list[int] = []
    for raw in value:
        try:
            offset = int(raw)
        except (TypeError, ValueError) as exc:
            raise MeetingChangeError(f"reminder offset must be a whole number of minutes; got {raw!r}") from exc
        if offset < 0:
            raise MeetingChangeError(
                f"reminder offset must be zero or positive minutes before the meeting; got {offset}"
            )
        if offset not in offsets:
            offsets.append(offset)
    return offsets


def _clean_flag(body: Mapping[str, Any], key: str, default: bool) -> bool:
    """A boolean setting, where an absent key means the default.

    Strings are accepted because a query parameter and a form both arrive as
    ``"true"``; anything unrecognisable is a refusal rather than a silent ``False``,
    because a mistyped ``delete_event`` would quietly keep a cancelled meeting on
    somebody's CRM calendar.
    """
    if key not in body or body[key] is None:
        return default
    value = body[key]
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("true", "1", "yes", "on"):
        return True
    if text in ("false", "0", "no", "off"):
        return False
    raise MeetingChangeError(f"{key} must be a boolean; got {value!r}")


def window_for(data: Mapping[str, Any] | None) -> tuple[int, int, list[int]]:
    """``(start_minute, end_minute, days)`` for a meeting type, with the fallback."""
    body = dict(data or {})
    hours = body.get("hours") or {}
    start = hours.get("start")
    end = hours.get("end")
    if start is None or end is None:
        start, end = DEFAULT_BUSINESS_HOURS
    days = [int(day) for day in (body.get("days") or DEFAULT_BUSINESS_DAYS)]
    if not days:
        days = list(DEFAULT_BUSINESS_DAYS)
    return (int(start), int(end), sorted(set(days)))


def expire_reschedule_link(data: Mapping[str, Any] | None) -> bool:
    return bool(dict(data or {}).get(EXPIRE_RESCHEDULE_LINK, DEFAULT_EXPIRE_RESCHEDULE_LINK))


def delete_event(data: Mapping[str, Any] | None) -> bool:
    return bool(dict(data or {}).get(DELETE_EVENT, DEFAULT_DELETE_EVENT))
