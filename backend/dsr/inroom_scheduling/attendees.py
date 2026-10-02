"""The attendee object every ``POST /v2/bookings`` call carries.

The research names the object and one of its fields - "``attendee``" in the
data_flow, and "the booking's unique identifier" for the reschedule exclusion - and
does not describe the rest. So this module is almost entirely the "assumed" half,
and everything it decides is registered in :mod:`dsr.inroom_scheduling.inferences`
rather than left in a function body.

What the research does fix:

* ``attendee`` is a top-level field of the create-booking payload.
* ``metadata`` is a sibling of it, and is where deal-room context belongs - the
  extensibility note names ``bookingFieldsResponses`` **and** ``metadata`` as "the
  first-class seams" for "carry deal-room context into every booking".
* ``metadata`` has three hard limits, quoted verbatim in
  :mod:`dsr.inroom_scheduling.vocabulary`, and they are limits on the *sent*
  metadata. A booking that silently dropped the keys that broke them would be a
  booking the CRM cannot join back to the room that produced it, which is worse
  than a 400.

What this build chose, and says so:

* ``email`` is required and must contain an ``@`` with text on both sides. A
  calendar invite needs one, and refusing here is friendlier than an invite that
  bounces.
* ``name`` is required. The research does not say, but an invite with no
  attendee name is unusable and a blank is a form bug worth surfacing.
* ``timeZone`` and ``language`` are optional, with a documented fallback. The
  research's slot query uses ``timeZone``; the booking payload's use of it is not
  documented, so an absent one is the room's configured zone rather than a guess
  at the prospect's.
* Numbers and booleans in ``metadata`` are checked as their string form, because
  the documented limit is on "string values up to 500 characters" and a caller
  sending ``{"seats": 4}`` means the same thing as ``{"seats": "4"}``.
"""

from __future__ import annotations

from typing import Any, Mapping

from dsr.inroom_scheduling.errors import BookingFieldRejected, MetadataOutOfRange, SchedulingError
from dsr.inroom_scheduling.vocabulary import (
    METADATA_LIMITS,
    METADATA_QUOTE,
    RESCHEDULE_PARAM,
)

#: The metadata keys this product puts on every booking, so a reviewer can see the
#: room context the extensibility note asks for without diffing a payload. All
#: three fit the documented 40-character key limit and 500-character value limit
#: with room to spare, which is checked rather than assumed.
ROOM_METADATA_KEYS: tuple[str, ...] = ("dsr_room_id", "dsr_booking_source", "dsr_account")


def normalise_attendee(
    payload: Any, *, default_time_zone: str = "UTC", field: str = "attendee"
) -> dict[str, Any]:
    """The attendee object, resolved.

    Accepts the research's nested object and, because a browser form posts flat
    fields, the two common flat spellings (``attendeeName`` / ``attendeeEmail``).
    A form that has to be reshaped by hand before it can book is a form somebody
    will not finish.
    """
    if payload is None:
        raise SchedulingError(
            f"{field} is required: POST /v2/bookings takes an attendee with a name and an email"
        )
    if isinstance(payload, str):
        raise SchedulingError(f"{field} must be an object, not a string")
    if not isinstance(payload, Mapping):
        raise SchedulingError(f"{field} must be an object with name, email and timeZone")

    body = dict(payload)
    name = str(body.get("name") or body.get("attendeeName") or "").strip()
    email = str(body.get("email") or body.get("attendeeEmail") or "").strip()
    time_zone = str(body.get("timeZone") or body.get("timezone") or default_time_zone).strip()
    language = body.get("language") or body.get("locale")

    if not name:
        raise SchedulingError(
            f"{field}.name is required; a calendar invite with no attendee name is unusable"
        )
    if not _looks_like_email(email):
        raise SchedulingError(
            f"{field}.email is required and must be an email address, got {email!r}"
        )
    if not time_zone:
        raise SchedulingError(
            f"{field}.timeZone is required when it cannot be inherited from the room"
        )

    resolved: dict[str, Any] = {"name": name, "email": email, "timeZone": time_zone}
    if language:
        resolved["language"] = str(language)
    # `attendeePhoneNumber` is not in the research, but Cal accepts it and a
    # phone-only prospect is a real case. Carried through when present, never
    # required - see the `attendee-optional-fields` inference.
    phone = body.get("phoneNumber") or body.get("attendeePhoneNumber")
    if phone:
        resolved["phoneNumber"] = str(phone)
    return resolved


def _looks_like_email(value: str) -> bool:
    if "@" not in value or value.startswith("@") or value.endswith("@"):
        return False
    local, _, domain = value.partition("@")
    if not local.strip() or not domain.strip() or "." not in domain:
        return False
    return " " not in value


def validate_metadata(metadata: Any, *, extra: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Booking metadata, checked against the three documented limits.

    All three, each naming itself in the refusal, because a payload can satisfy
    two of them and break the third. "Metadata must have at most 50 keys, each key
    up to 40 characters, and string values up to 500 characters" - so 51 keys is a
    different problem from one 41-character key, and the caller needs to know
    which.

    ``extra`` is what the product itself contributes: the room context the
    extensibility note asks for. It is merged **before** the limits are applied,
    not after. Adding the room id to an already-full payload would push a booking
    over a limit the caller was inside, which is a bug that would only appear on
    the busiest rooms.
    """
    if metadata in (None, ""):
        body: dict[str, Any] = {}
    elif isinstance(metadata, Mapping):
        body = {str(key): value for key, value in metadata.items()}
    else:
        raise SchedulingError("metadata must be an object of string keys and values")

    if extra:
        for key, value in extra.items():
            body.setdefault(str(key), value)

    if len(body) > METADATA_LIMITS["max_keys"]:
        raise MetadataOutOfRange(
            f"metadata has {len(body)} keys; {METADATA_QUOTE}",
            limit="max_keys",
            value=len(body),
            maximum=METADATA_LIMITS["max_keys"],
        )

    for key, value in body.items():
        if len(key) > METADATA_LIMITS["max_key_length"]:
            raise MetadataOutOfRange(
                f"metadata key {key!r} is {len(key)} characters; the limit is "
                f"{METADATA_LIMITS['max_key_length']}. {METADATA_QUOTE}",
                limit="max_key_length",
                key=key,
                value=len(key),
                maximum=METADATA_LIMITS["max_key_length"],
            )
        text = value if isinstance(value, str) else _stringify(value)
        if len(text) > METADATA_LIMITS["max_value_length"]:
            raise MetadataOutOfRange(
                f"metadata value for {key!r} is {len(text)} characters; the limit is "
                f"{METADATA_LIMITS['max_value_length']}. {METADATA_QUOTE}",
                limit="max_value_length",
                key=key,
                value=len(text),
                maximum=METADATA_LIMITS["max_value_length"],
            )
    return body


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def room_metadata(
    *, room_id: str | None, account: str | None = None, source: str = "in-room"
) -> dict[str, str]:
    """The room context every in-room booking carries.

    Three keys, all inside the documented limits, and all present whether or not
    the caller sent any metadata of its own. This is the extensibility note's
    "carry deal-room context into every booking" made concrete: a booking record
    in the scheduling system names the room it came from, so a webhook consumer
    does not need a join table to answer "which room booked this".
    """
    body = {"dsr_booking_source": source}
    if room_id:
        body["dsr_room_id"] = str(room_id)
    if account:
        body["dsr_account"] = str(account)[: METADATA_LIMITS["max_value_length"]]
    return body


def reschedule_uid(payload: Mapping[str, Any]) -> str | None:
    """The ``bookingUidToReschedule`` on a create-booking payload, if any.

    The research writes the field in camelCase and describes exactly one purpose
    for it. Both spellings are accepted, for the same reason the slot query accepts
    both: a client copying a Cal request will send the researched one.
    """
    for key in (RESCHEDULE_PARAM, "booking_uid_to_reschedule", "reschedule", "rescheduleUid"):
        value = payload.get(key)
        if value not in (None, ""):
            return str(value)
    return None


def booking_fields_responses(payload: Mapping[str, Any]) -> dict[str, Any]:
    """``bookingFieldsResponses`` from a create-booking payload.

    Named in the data_flow as one of the two seams for carrying deal-room context
    into a booking, beside ``metadata``. Kept separate from metadata because they
    are separate things: booking fields are the event type's own questions asked of
    the attendee, metadata is this product's context about the deal. A caller that
    conflated them would put "how big is your team" into metadata, where nothing
    validates it against the event type's field definitions.
    """
    for key in ("bookingFieldsResponses", "booking_fields_responses", "bookingFields"):
        value = payload.get(key)
        if value is None:
            continue
        if not isinstance(value, Mapping):
            raise BookingFieldRejected(f"{key} must be an object of field name to answer")
        return {str(name): answer for name, answer in value.items()}
    return {}
