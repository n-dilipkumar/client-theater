"""Event types, calendar connections, and the vocabulary the embed renders from.

An event type is the thing being booked. The research names four kinds -
"team event types" and "seated events" in ``features_tools``, a personal event by
implication, and a routing event as the target a routing form routes to - and
publishes the fields that matter to the embed: the length, the slug and username or
team slug by which ``GET /v2/slots`` can find it, and whether it takes a payment
("Stripe (optional payment)" in the data_sources line).

Everything a slot grid needs is on the event type, deliberately, rather than spread
across a schedule table:

* ``hosts`` - each with a username, an IANA zone, the ISO weekdays it works, its
  working hours, its grid interval and its minimum notice. A dynamic
  ``usernames=alice,bob`` query resolves its hosts from the same list, so a
  personal event and a multi-person one go through identical arithmetic.
* ``length_minutes`` - how long the meeting is, and therefore the end of every
  slot on the grid.
* ``seats`` - present only for a seated event, and the reason a slot can be
  unavailable for a reason that is neither busy nor held.
* ``booking_fields`` - "booking fields (prefill / read-only)" from
  ``features_tools``. A prefilled field is answered by the embed rather than by the
  prospect; a read-only field is shown and cannot be changed. The distinction is
  enforced in :func:`apply_booking_fields`, because a read-only field that a
  prospect can change is a field the vendor will not honour.
* ``price`` - a number for a paid event, absent otherwise, which is what the
  embed's payment-form rule reads.
* ``conference`` - one of Zoom, Google Meet, Teams or Webex, which is what decides
  whether a booking gets a video link.

Calendar connections are the "calendar-connect buttons for Google/Outlook/Apple"
and the "connected Google/Outlook/Apple calendars" of the data_sources line. A
connection is a record of which provider is connected, for which host, and since
when. It is a *record* rather than a live OAuth flow with Cal: the research
documents no endpoint for calendar connections that this product can reach, and
the buttons are part of the embed, not of the booking path - so a connection
affects nothing a booking depends on, and is stored as configuration.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from dsr.inroom_scheduling.errors import BookingFieldRejected, SchedulingError
from dsr.inroom_scheduling.schedules import normalise_host
from dsr.inroom_scheduling.vocabulary import (
    CALENDAR_PROVIDERS,
    CONFERENCE_PROVIDERS,
    NON_VIDEO_LOCATIONS,
    require_calendar_provider,
    require_conference_provider,
    require_event_type_kind,
)

#: The booking-field types the embed renders an input for. ``text`` and ``select``
#: cover the research's own example of a qualification question; the rest exist so
#: a form author is not forced to ask a headcount as free text.
BOOKING_FIELD_TYPES: tuple[str, ...] = ("text", "textarea", "select", "number", "checkbox")


def normalise_booking_fields(raw: Any, *, field: str = "booking_fields") -> list[dict[str, Any]]:
    """The event type's booking fields, validated.

    ``prefilled`` and ``read_only`` interact, and the interaction is the whole
    point: a read-only field must be prefilled, or the embed would show a value the
    prospect cannot change and cannot be told where it came from. So
    ``read_only`` without ``prefilled`` is refused.
    """
    if raw in (None, ""):
        return []
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise SchedulingError(f"{field} must be a list of booking field definitions")

    fields: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, entry in enumerate(raw):
        if not isinstance(entry, Mapping):
            raise SchedulingError(f"{field}[{index}] must be an object")
        name = str(entry.get("name") or "").strip()
        if not name:
            raise BookingFieldRejected(
                f"{field}[{index}].name is required; a booking field with no name cannot be answered"
            )
        if name in seen:
            raise BookingFieldRejected(
                f"{field} has two fields called {name!r}; bookingFieldsResponses is keyed by name, "
                "so the second answer would silently replace the first"
            )
        seen.add(name)

        field_type = str(entry.get("type") or "text").strip()
        if field_type not in BOOKING_FIELD_TYPES:
            raise BookingFieldRejected(
                f"{field}[{index}].type is {field_type!r}; the published set is "
                + ", ".join(BOOKING_FIELD_TYPES)
            )

        read_only = bool(entry.get("read_only") or entry.get("readOnly"))
        prefilled = entry.get("prefilled", entry.get("prefill"))
        if read_only and prefilled in (None, ""):
            raise BookingFieldRejected(
                f"{field} {name!r} is read_only but has no prefilled value. A read-only field the "
                "prospect cannot change must be prefilled, or the embed shows a value it cannot "
                "explain."
            )

        options: list[str] = []
        raw_options = entry.get("options")
        if isinstance(raw_options, Sequence) and not isinstance(raw_options, (str, bytes)):
            options = [str(option) for option in raw_options]
        if field_type == "select" and not options:
            raise BookingFieldRejected(
                f"{field} {name!r} is a select with no options; a select with nothing to pick from "
                "is a dead question"
            )

        fields.append(
            {
                "name": name,
                "label": entry.get("label") or name,
                "type": field_type,
                "required": bool(entry.get("required")),
                "prefilled": prefilled,
                "read_only": read_only,
                "options": options,
                "hidden": bool(entry.get("hidden")),
            }
        )
    return fields


def apply_booking_fields(
    event_type: Mapping[str, Any], responses: Mapping[str, Any]
) -> dict[str, Any]:
    """Merge the prospect's answers with the event type's field definitions.

    Four rules, in this order, each from ``features_tools``' "booking fields
    (prefill / read-only)":

    1. a **required** field with no answer is a refusal. Booking without asking the
       qualification question the rep configured would collect nothing.
    2. a **prefilled** field is answered by the embed when the prospect did not
       answer it, so the prospect is not asked for something already known.
    3. a **read-only** field is never taken from the prospect. A submitted value
       that differs from the prefilled one is a refusal, not a silent override: the
       prospect is trying to change something the seller set, and quietly
       ignoring it means the booking records a different value from the one the
       prospect believed they submitted.
    4. an answer to a field the event type does not declare is a refusal. It would
       otherwise be stored on the booking and read back as if the vendor had asked
       for it.

    A hidden field is answered from its prefilled value if it has one and is
    otherwise not required to be answered - a hidden question is not shown, so its
    absence is not the prospect's doing.
    """
    declared = {str(entry["name"]): entry for entry in (event_type.get("booking_fields") or [])}

    unknown = [str(name) for name in responses if str(name) not in declared]
    if unknown:
        raise BookingFieldRejected(
            f"bookingFieldsResponses answers {unknown}, which the event type does not declare. "
            "Its fields are " + (", ".join(sorted(declared)) or "none")
        )

    resolved: dict[str, Any] = {}
    for name, entry in declared.items():
        submitted = responses.get(name)
        if entry.get("read_only"):
            if submitted not in (None, "") and str(submitted) != str(entry.get("prefilled")):
                raise BookingFieldRejected(
                    f"booking field {name!r} is read-only and prefilled with "
                    f"{entry.get('prefilled')!r}; the submitted value {submitted!r} cannot be changed"
                )
            resolved[name] = entry.get("prefilled")
            continue
        if submitted in (None, ""):
            if entry.get("prefilled") not in (None, ""):
                resolved[name] = entry.get("prefilled")
                continue
            if entry.get("required") and not entry.get("hidden"):
                raise BookingFieldRejected(
                    f"booking field {name!r} is required and was not answered; "
                    f"options: " + (", ".join(entry.get("options") or []) or "a free-text answer")
                )
            if entry.get("prefilled") in (None, "") and not entry.get("hidden"):
                resolved[name] = ""
            continue
        if entry.get("options") and str(submitted) not in {
            str(option) for option in entry["options"]
        }:
            raise BookingFieldRejected(
                f"booking field {name!r} takes one of "
                + ", ".join(entry["options"])
                + f"; got {submitted!r}"
            )
        resolved[name] = submitted
    return resolved


def normalise_event_type(spec: Mapping[str, Any], *, field: str = "event_type") -> dict[str, Any]:
    """An event type, validated.

    Three requirements are structural rather than cosmetic, and each of them is
    because the slot query cannot work without it:

    * **at least one host.** "there is no specific event but we just want to know
      when 2 or more people are available" describes the dynamic case as the
      exception, so the normal case is an event with people attached. An event type
      with no host has no working hours and would offer no slots at all.
    * **a length.** Every slot's end is ``start + length_minutes``.
    * **an identifier the query can use.** ``eventTypeId`` for the id form, and a
      ``slug`` with a ``username`` or a ``teamSlug`` for the other three, matching
      the four researched selectors.

    ``seats`` is required for a seated event and refused for a non-seated one: a
    seat count on a personal event would cap bookings nobody intended to cap.
    """
    body = dict(spec or {})
    kind = require_event_type_kind(str(body.get("kind") or "personal").strip())

    event_type_id = str(body.get("eventTypeId") or body.get("event_type_id") or "").strip()
    if not event_type_id:
        raise SchedulingError(
            f"{field}.eventTypeId is required; it is what eventTypeId= queries resolve to"
        )
    slug = str(body.get("slug") or "").strip()
    if not slug:
        raise SchedulingError(
            f"{field}.slug is required; the research reaches an event type by "
            "eventTypeSlug+username+organizationSlug or by teamSlug as well as by id"
        )

    team_slug = str(body.get("teamSlug") or body.get("team_slug") or "").strip() or None
    host_username = str(body.get("host") or body.get("username") or "").strip() or None
    if kind == "team" and not team_slug:
        raise SchedulingError(
            f"{field}.teamSlug is required for a team event; team events are reached by teamSlug"
        )
    if kind == "personal" and not host_username:
        raise SchedulingError(
            f"{field}.host is required for a personal event; it is reached by username"
        )

    raw_hosts = body.get("hosts")
    if raw_hosts in (None, ""):
        # A single-host event type: the host name plus the event's own working
        # hours. This is the shape most event types have, and making it explicit
        # here means the slot grid has exactly one code path.
        fallback_name = host_username or (team_slug or "")
        if not fallback_name:
            raise SchedulingError(
                f"{field}.hosts is required; an event type with no host has no working hours and would "
                "offer no slots at all"
            )
        raw_hosts = [
            {
                "username": fallback_name,
                "time_zone": body.get("time_zone") or body.get("timeZone") or "UTC",
                "days": body.get("days"),
                "start": body.get("working_hours_start", "09:00"),
                "end": body.get("working_hours_end", "17:00"),
                "slot_interval_minutes": body.get("slot_interval_minutes", 30),
                "minimum_notice_minutes": body.get("minimum_notice_minutes", 0),
            }
        ]
    if not isinstance(raw_hosts, Sequence) or isinstance(raw_hosts, (str, bytes)):
        raise SchedulingError(f"{field}.hosts must be a list of host working-hour definitions")
    hosts = [
        normalise_host(entry, field=f"{field}.hosts[{index}]")
        for index, entry in enumerate(raw_hosts)
    ]
    if not hosts:
        raise SchedulingError(f"{field}.hosts must name at least one host")
    names = [host["username"] for host in hosts]
    if len(set(names)) != len(names):
        raise SchedulingError(f"{field}.hosts names a username twice: {names}")

    try:
        length_minutes = int(body.get("length_minutes", body.get("lengthMinutes", 30)))
    except (TypeError, ValueError) as exc:
        raise SchedulingError(f"{field}.length_minutes must be a whole number of minutes") from exc
    if length_minutes < 5:
        raise SchedulingError(
            f"{field}.length_minutes must be at least 5; a shorter meeting is not bookable"
        )
    if length_minutes > 8 * 60:
        raise SchedulingError(f"{field}.length_minutes may not exceed a working day")

    seats = body.get("seats")
    if kind == "seated":
        try:
            seats = int(seats)
        except (TypeError, ValueError) as exc:
            raise SchedulingError(
                f"{field}.seats is required for a seated event; 'seated events' is the researched term"
            ) from exc
        if seats < 2:
            raise SchedulingError(f"{field}.seats must be at least 2 for a seated event")
    elif seats not in (None, "", 0):
        raise SchedulingError(
            f"{field}.seats is only meaningful on a seated event, and this is a {kind} event. A seat "
            "count on a personal event would cap bookings nobody intended to cap."
        )
    else:
        seats = None

    location = str(body.get("location") or "phone").strip()
    conference = body.get("conference")
    if conference:
        conference = require_conference_provider(str(conference).strip())
    if location in CONFERENCE_PROVIDERS and not conference:
        conference = location
    if location not in CONFERENCE_PROVIDERS and location not in NON_VIDEO_LOCATIONS:
        raise SchedulingError(
            f"{field}.location is {location!r}; it must be one of "
            + ", ".join(CONFERENCE_PROVIDERS + NON_VIDEO_LOCATIONS)
        )

    price = body.get("price")
    if price not in (None, ""):
        try:
            price = float(price)
        except (TypeError, ValueError) as exc:
            raise SchedulingError(f"{field}.price must be a number when set") from exc
        if price <= 0:
            raise SchedulingError(f"{field}.price must be greater than zero when set")
    else:
        price = None

    return {
        "eventTypeId": event_type_id,
        "slug": slug,
        "kind": kind,
        "title": body.get("title") or f"{slug} meeting",
        "description": body.get("description"),
        "hosts": hosts,
        "host": host_username or (team_slug or names[0]),
        "teamSlug": team_slug,
        "length_minutes": length_minutes,
        "seats": seats,
        "location": location,
        "conference": conference,
        "price": price,
        "currency": body.get("currency") or ("USD" if price else None),
        "booking_fields": normalise_booking_fields(body.get("booking_fields")),
        "instant_bookable": bool(body.get("instant_bookable", kind == "team")),
        "autoConfirm": bool(body.get("autoConfirm", True)),
    }


def normalise_calendar_connection(
    spec: Mapping[str, Any], *, field: str = "calendar"
) -> dict[str, Any]:
    """A calendar connection, validated.

    Google, Outlook and Apple, from the researched list. A connection without a
    host is refused, because a hostless connection says "somebody's calendar is
    connected" and could then be read as "the room's host is free", which is a
    different and stronger claim.
    """
    body = dict(spec or {})
    provider = require_calendar_provider(str(body.get("provider") or "").strip())
    host = str(body.get("host") or "").strip()
    if not host:
        raise SchedulingError(
            f"{field}.host is required; a calendar connection is for a named host"
        )
    return {
        "provider": provider,
        "host": host,
        "label": body.get("label") or f"{provider.title()} ({host})",
        "read_only": bool(body.get("read_only", False)),
        "write_enabled": bool(body.get("write_enabled", not body.get("read_only", False))),
    }


def calendar_summary(connections: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Which providers are connected, for the page's connect buttons.

    Accepts records or bare payloads, because the engine passes rows out of the
    store and a caller testing the summary passes dicts, and the difference should
    not change the answer.

    ``connected`` is the set the UI disables, and ``missing`` the set it offers.
    Naming both matters: a page showing three enabled buttons for three calendars
    that are already connected is a page that looks broken when a rep clicks one.
    """
    providers: set[str] = set()
    for record in connections:
        data = record.get("data") if isinstance(record.get("data"), Mapping) else record
        provider = str(data.get("provider") or "").strip()
        if provider:
            providers.add(provider)
    connected = sorted(providers)
    return {
        "connected": connected,
        "missing": [provider for provider in CALENDAR_PROVIDERS if provider not in connected],
        "count": len(connections),
    }
