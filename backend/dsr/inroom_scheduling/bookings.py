"""``POST /v2/bookings``: the three booking kinds, and the one automation.

The research's sentence is dense and every clause in it is a rule:

    ``POST /v2/bookings`` - header ``cal-api-version: 2026-02-25``; supports
    standard, recurring (``recurrenceCount``, max 32), and instant
    (``"instant": true``, team events only) bookings.

**Three kinds, one payload.** A request is standard, recurring, or instant, and
the kind is read off the payload rather than asked for: ``recurrenceCount``
present makes it recurring, ``instant: true`` makes it instant, neither makes it
standard. Two of those together is a refusal, because the research lists them as
alternatives and a payload that is both is a caller's bug rather than a fourth
kind.

**Recurrence is capped at 32, not truncated.** A request for 40 is a 400. Booking
32 of the 40 meetings somebody asked for would leave them with a calendar holding
meetings they did not agree to, and a silent truncation is indistinguishable from
success.

**Instant means a team event.** "team events only" is the parenthetical the
research gives, and it is the reason :class:`InstantNeedsTeamEvent` exists. The
alternative - accepting ``instant`` on a personal event and treating it as
standard - would answer a booking a caller believes is immediate with one that
respects a host's calendar, which is the opposite of what they asked for.

**``BOOKING_CREATED`` fires on success.** "On success, ``BOOKING_CREATED``
webhook fires; downstream automations can chain (see #16)." This build records
the event as a record in the same transaction as the booking, with the payload a
webhook consumer would receive, because the automations that chain off it are
other workflows in this product (WF-044 emits webhooks, WF-028 turns signals into
actions) and a record is what they read. Whether the event fires *after* the
booking is committed matters: an automation that ran against a booking that then
failed would be worse than no automation.

What this build chose where the research is silent, every item registered in
:mod:`dsr.inroom_scheduling.inferences`:

* a reschedule moves the *existing* booking rather than creating a second one, so
  the history of a meeting stays in one record and ``BOOKING_CREATED`` is not
  re-fired for a move;
* a cancelled booking releases its slot back to the grid, and a cancellation after
  the start is still recorded;
* instant bookings are refused when the event type is not a team event even if the
  caller is the host, because the research's rule is about the event type.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Mapping, Sequence

from dsr.inroom_scheduling.attendees import (
    booking_fields_responses,
    normalise_attendee,
    reschedule_uid,
    validate_metadata,
)
from dsr.inroom_scheduling.errors import (
    BookingConflict,
    InstantNeedsTeamEvent,
    RecurrenceOutOfRange,
    SchedulingError,
)
from dsr.inroom_scheduling.schedules import iso, parse_instant
from dsr.inroom_scheduling.vocabulary import (
    BOOKING_CREATED,
    BOOKING_KINDS,
    CONFERENCE_PROVIDERS,
    MAX_RECURRENCE_COUNT,
    NON_VIDEO_LOCATIONS,
)

STANDARD = "standard"
RECURRING = "recurring"
INSTANT = "instant"

CANCELLED = "cancelled"

#: The fields a stored booking carries, in the order the research's data_flow
#: names them. The research's own list is the schema, more or less.
BOOKING_FIELDS: tuple[str, ...] = (
    "uid",
    "eventTypeId",
    "title",
    "start",
    "end",
    "attendee",
    "bookingFieldsResponses",
    "metadata",
    "location",
    "video",
    "status",
)


@dataclass(frozen=True)
class BookingRequest:
    """A create-booking payload, read once and validated once.

    Holding the whole request as one object rather than threading six arguments
    through is what lets :func:`read_booking_request` be the single place the
    researched rules live, so a second code path cannot forget the recurrence cap.
    """

    kind: str
    start: str
    attendee: dict[str, Any]
    metadata: dict[str, Any]
    booking_fields: dict[str, Any]
    recurrence_count: int
    reschedule: str | None
    instant: bool
    title: str | None
    location: str | None
    video: str | None
    payment: Any
    language: str | None


def read_booking_request(
    payload: Mapping[str, Any],
    *,
    default_time_zone: str = "UTC",
) -> BookingRequest:
    """Read a ``POST /v2/bookings`` payload, applying every researched limit.

    Order matters here and is deliberate. The kind is read first, because
    ``recurrenceCount`` and ``instant`` are what *decide* the kind; then the
    attendee, because nothing else can be checked without a person to book for;
    then the recurrence cap and the team-event rule, which are properties of the
    kind; then metadata, because the room context has to merge with the caller's
    before the limits apply.
    """
    body = dict(payload or {})

    raw_count = body.get("recurrenceCount", body.get("recurrence_count"))
    has_recurrence = raw_count not in (None, "")
    instant = bool(body.get("instant"))

    if has_recurrence and instant:
        raise SchedulingError(
            "a booking is either recurring or instant, not both; the research lists "
            f"{', '.join(BOOKING_KINDS)} as alternatives"
        )
    if instant:
        kind = INSTANT
    elif has_recurrence:
        kind = RECURRING
    else:
        kind = STANDARD

    start = body.get("start")
    if start in (None, ""):
        if kind != INSTANT:
            raise SchedulingError(
                "start is required for a standard or recurring booking; an instant "
                "booking is the one kind that may be booked without one"
            )
        # An instant booking with no start is resolved later, against the grid -
        # "the soonest slot this team event can take" - rather than defaulted to
        # now, which would be a meeting in the past.
        start = ""

    count = 1
    if has_recurrence:
        try:
            count = int(raw_count)
        except (TypeError, ValueError) as exc:
            raise RecurrenceOutOfRange(
                f"recurrenceCount must be a whole number, got {raw_count!r}",
                limit="recurrenceCount",
            ) from exc
        if count < 1:
            raise RecurrenceOutOfRange(
                f"recurrenceCount must be at least 1, got {count}",
                limit="recurrenceCount",
                value=count,
            )
        if count > MAX_RECURRENCE_COUNT:
            raise RecurrenceOutOfRange(
                f"recurrenceCount is {count}; the research documents a maximum of {MAX_RECURRENCE_COUNT}. "
                "It is refused rather than truncated, because a booking that silently made "
                f"{MAX_RECURRENCE_COUNT} of the {count} meetings asked for would be a calendar full of "
                "meetings nobody agreed to.",
                limit="recurrenceCount",
                value=count,
                maximum=MAX_RECURRENCE_COUNT,
            )

    location = body.get("location")
    if location is not None and not isinstance(location, str):
        location = str(location)
    video = body.get("video")
    if video is not None and not isinstance(video, str):
        video = str(video)

    return BookingRequest(
        kind=kind,
        start=str(start),
        attendee=normalise_attendee(body.get("attendee"), default_time_zone=default_time_zone),
        metadata=validate_metadata(body.get("metadata")),
        booking_fields=booking_fields_responses(body),
        recurrence_count=count,
        reschedule=reschedule_uid(body),
        instant=instant,
        title=body.get("title"),
        location=location,
        video=video,
        payment=body.get("payment"),
        language=body.get("language") or body.get("locale"),
    )


def require_team_event_for_instant(request: BookingRequest, event_type: Mapping[str, Any]) -> None:
    """Refuse an instant booking on anything but a team event.

    Sourced: "instant (``\"instant\": true``, team events only) bookings". A team
    event is one whose ``kind`` is ``team``; a ``routing`` or ``seated`` event type
    routes or seats rather than being a team, and is refused for the same reason -
    the research's parenthetical is not satisfied by anything else.
    """
    if not request.instant:
        return
    if str(event_type.get("kind")) != "team":
        raise InstantNeedsTeamEvent(
            "an instant booking is team events only; "
            f"event type {event_type.get('slug') or event_type.get('eventTypeId')!r} is "
            f"a {event_type.get('kind')} event. Either book it as a standard booking, or "
            "point the room at a team event type.",
            event_type=str(event_type.get("slug") or event_type.get("eventTypeId")),
            kind=str(event_type.get("kind")),
        )


def resolve_instant_start(request: BookingRequest, slots: Sequence[Mapping[str, Any]]) -> str:
    """The start an instant booking takes when the caller named none.

    The soonest bookable slot on the grid, or a refusal naming the state of the
    data. Refusing rather than defaulting to ``now`` matters: an instant booking at
    a start in the past would create a meeting that already happened, and the audit
    row would show a booking created before the request that created it.
    """
    from dsr.inroom_scheduling.availability import first_free

    slot = first_free(slots)
    if slot is None:
        raise BookingConflict(
            "an instant booking needs a bookable slot and the team event has none in the "
            "requested window; no start was given and there is nothing to book",
            reason="no_free_slot",
        )
    return str(slot["start"])


def describe_window(start: str, *, count: int, interval_days: int = 7) -> list[str]:
    """The starts of a recurring series.

    Weekly, because the research says ``recurrenceCount`` and nothing else: there
    is no frequency, no interval and no end date in the documented payload, so
    weekly is the reading that needs the fewest assumptions and is stated as an
    inference (``recurrence-interval``) rather than buried. The series is a
    *description*: the booking record carries every start, and availability for the
    later ones is the caller's to re-check, because a series booked in September
    may collide with something booked in October.
    """
    first = parse_instant(start, field="start")
    return [iso(first + timedelta(days=interval_days * step)) for step in range(max(1, count))]


def booking_payload(
    request: BookingRequest,
    *,
    uid: str,
    event_type: Mapping[str, Any],
    start: str,
    end: str,
    room_id: str | None,
    actor: str | None = None,
    account: str | None = None,
) -> dict[str, Any]:
    """The stored booking body, in the research's own field names.

    ``eventTypeId`` rather than ``event_type_id``, ``bookingFieldsResponses``
    rather than ``booking_fields_responses``: this is the payload a Cal client
    would recognise, and the store is schema-flexible so keeping the researched
    spelling costs nothing and saves a mapping layer in every consumer.
    """
    length = parse_instant(end, field="end") - parse_instant(start, field="start")
    location = request.location or str(event_type.get("location") or "phone")
    series = (
        describe_window(start, count=request.recurrence_count)
        if request.kind == RECURRING
        else None
    )
    video = request.video or _conference_link(event_type, location, uid)

    return {
        "uid": uid,
        "eventTypeId": event_type.get("eventTypeId") or event_type.get("event_type_id"),
        "title": request.title or event_type.get("title"),
        "start": iso(parse_instant(start, field="start")),
        "end": iso(parse_instant(end, field="end")),
        "duration_minutes": int(length.total_seconds() // 60),
        "attendee": request.attendee,
        "bookingFieldsResponses": request.booking_fields,
        "metadata": request.metadata,
        "location": location,
        "video": None if location in NON_VIDEO_LOCATIONS else video,
        "status": "confirmed",
        "kind": request.kind,
        "instant": bool(request.instant),
        "recurrence": (
            {"count": request.recurrence_count, "interval": "weekly", "occurrences": series}
            if series
            else None
        ),
        "host": event_type.get("host"),
        "room_id": room_id,
        "actor": actor,
        "account": account,
        "rescheduled_from": request.reschedule,
        "payment": request.payment,
        "reschedule_param": "bookingUidToReschedule" if request.reschedule else None,
    }


def _conference_link(event_type: Mapping[str, Any], location: str, uid: str) -> str | None:
    """The conference link for a booking, or ``None`` when there is none.

    Zoom, Google Meet, Teams and Webex are the four the data_sources line names, and
    a location of ``phone`` or ``address`` has no link - which the vocabulary
    publishes so a client does not render an empty join button.
    """
    provider = str(event_type.get("conference") or location or "").strip()
    if provider not in CONFERENCE_PROVIDERS:
        return None
    token = uid.replace("_", "")
    return {
        "zoom": f"https://zoom.us/j/{abs(hash(uid)) % 10_000_000_000:011d}",
        "google_meet": f"https://meet.google.com/{token[:3]}-{token[3:7]}-{token[7:10]}",
        "ms_teams": f"https://teams.microsoft.com/l/meetup-join/{token}",
        "webex": f"https://webex.com/meet/{token}",
    }[provider]


def booking_created_event(booking: Mapping[str, Any], *, booking_record_id: str) -> dict[str, Any]:
    """The ``BOOKING_CREATED`` payload, shaped the way a webhook consumer reads it.

    The research names the event and says downstream automations chain off it. The
    payload is the booking's own fields plus the record id in this product, so a
    consumer can act on the event and then read the durable record - and so a test
    can assert the event names the booking it belongs to.
    """
    return {
        "event": BOOKING_CREATED,
        "bookingUid": booking.get("uid"),
        "eventTypeId": booking.get("eventTypeId"),
        "start": booking.get("start"),
        "end": booking.get("end"),
        "attendee": booking.get("attendee"),
        "metadata": booking.get("metadata"),
        "video": booking.get("video"),
        "room_id": booking.get("room_id"),
        "booking_record_id": booking_record_id,
        "booked_at": booking.get("booked_at"),
    }


def cancel_payload(booking: Mapping[str, Any], *, reason: str, now: datetime) -> dict[str, Any]:
    """The patch that cancels a booking.

    A shallow merge, so only the status and the cancellation detail change. The
    occurrence starts of a recurring series survive: a cancelled series is a
    cancelled series, and rewriting nine future starts to say "cancelled" would
    make the record unreadable and the next occurrence indistinguishable from the
    first.
    """
    return {
        "status": CANCELLED,
        "cancellation": {
            "reason": reason,
            "at": iso(now),
            "uid": booking.get("uid"),
        },
    }


def require_cancellable(booking: Mapping[str, Any]) -> None:
    """A live booking, or a refusal.

    Cancelling something already cancelled would rewrite the first cancellation's
    reason and timestamp, so the second attempt is refused rather than absorbed.
    """
    if str(booking.get("status")) == CANCELLED:
        raise SchedulingError(
            f"booking {booking.get('uid')} is already cancelled; a second cancellation would "
            "overwrite why and when it was cancelled the first time"
        )
