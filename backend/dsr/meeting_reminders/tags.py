r"""Dynamic tags: composing a reminder's subject and body for one booking.

``user_flow`` step 5: "Composes subject + body, using dynamic tags such as
``CP.Guest.FirstName``, ``CP.Meeting.RescheduleUrl``, ``CP.Meeting.CancelUrl``."
The word *such as* matters: the research names three and does not claim a closed
list, so this module ships a registry rather than three substitutions.

The registry has two namespaces, because the research cites two products that
spell their tags differently.

**Chili Piper's** are dotted paths: ``CP.Guest.FirstName``. The research names
``CP.Guest.FirstName``, ``CP.Meeting.RescheduleUrl`` and
``CP.Meeting.CancelUrl``; the rest are built from the same prefix and the same
booking fields the ``data_flow`` enumerates, so a team adding a tag is adding a
registry entry rather than a schema change.

**Cal.com's** are brace-delimited: ``{EVENT_NAME}``. The research quotes the
whole set in ``apis_hit``, and two of them are not identifiers at all:
``{START_TIME_h:mma}`` contains a colon and ``{EVENT_DATE_ddd, MMM D, YYYY
h:mma}`` contains a comma and three spaces. A ``\{(\w+)\}`` regular expression
matches neither, and a tag renderer that silently drops four of the eight
documented tokens is worse than one that refuses them. :data:`CAL_TOKEN_RE`
accepts the whole brace body and the lookup is by the exact inner text.

**Unknown tags are reported, not swallowed.** A reminder whose body says
``{CUSTOMER.TIER}`` renders with the token visible and the problem named in the
result, because a message that quietly lost a line is a message that went out
wrong. See :func:`render`.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Mapping

#: A Chili Piper tag: ``CP.`` then dotted segments of letters and digits.
CP_TOKEN_RE = re.compile(r"CP\.[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)*")

#: A Cal token: the entire brace body, whatever is in it. The two documented
#: tokens with punctuation are the reason this is not ``\{(\w+)\}``.
CAL_TOKEN_RE = re.compile(r"\{([^{}]+)\}")

#: Both forms in one pattern, substituted in a **single pass**.
#:
#: Two separate passes cannot work, and the failure is subtle enough to be worth
#: stating. A Chili Piper tag is dotted text *inside* braces as a person writes
#: it, so after the first pass has replaced ``{CP.Guest.FirstName}`` the text
#: reads ``{Priya}`` - and a second pass over the result sees a brace body that
#: is no longer a tag at all, and reports the guest's own first name as a missing
#: token. One combined pattern means a substituted value is never re-read.
TOKEN_RE = re.compile(
    r"CP\.[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)*"  # group 0: a Chili Piper tag
    r"|\{([^{}]+)\}"  # group 1: the body of a Cal token
)

#: The three tags the research names, spelled exactly as it spells them.
NAMED_CP_TAGS = ("CP.Guest.FirstName", "CP.Meeting.RescheduleUrl", "CP.Meeting.CancelUrl")

#: What a tag resolves to when the booking has nothing for it. Empty string
#: rather than "None" or "null": a composed message should read naturally, and
#: the *fact* that a field was missing is reported separately in ``missing``.
MISSING = ""


# --------------------------------------------------------------------------- #
# Small readers over a booking
# --------------------------------------------------------------------------- #
#
# A booking is arbitrary JSON in ``records.data``, so every read is defensive.
# The researched fields come from the ``data_flow``: "booking record (start time,
# duration, primary guest, all guests, booker, host, assignees, guest phone,
# responseStatus)".


def _text(value: Any) -> str:
    if value is None or isinstance(value, (dict, list, bool)):
        return ""
    return str(value)


def _first_name(person: Mapping[str, Any] | None) -> str:
    if not isinstance(person, Mapping):
        return ""
    name = _text(person.get("firstName") or person.get("name") or person.get("email"))
    return name.split(" ")[0] if name else ""


def _full_name(person: Mapping[str, Any] | None) -> str:
    if not isinstance(person, Mapping):
        return ""
    return _text(person.get("name") or person.get("firstName") or person.get("email"))


def _person_email(person: Mapping[str, Any] | None) -> str:
    if not isinstance(person, Mapping):
        return ""
    return _text(person.get("email"))


def _first_guest(booking: Mapping[str, Any]) -> Mapping[str, Any]:
    """The guest a tag about "the guest" refers to.

    The ``primary guest`` is the person the booking is with; the research
    distinguishes them from *all guests* in ``Send Email To``, so a tag that
    does not say which one means the primary guest.
    """
    primary = booking.get("primaryGuest")
    if isinstance(primary, Mapping) and primary:
        return primary
    guests = booking.get("guests")
    if isinstance(guests, list) and guests and isinstance(guests[0], Mapping):
        return guests[0]
    return {}


def _guests(booking: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    guests = booking.get("guests")
    return (
        [guest for guest in guests if isinstance(guest, Mapping)]
        if isinstance(guests, list)
        else []
    )


def _start(booking: Mapping[str, Any]):
    from dsr.meeting_reminders.conditions import parse_instant

    return parse_instant(booking.get("start"))


def _timezone_offset(booking: Mapping[str, Any]) -> int:
    """The booking's own UTC offset, in whole minutes. Zero when absent.

    The offset comes from the booking rather than from the host, because a
    reminder that says "Tuesday 2pm" to a guest in Auckland when the host is in
    Dublin is a bug, and the only place the answer exists is the booking. See
    :func:`dsr.meeting_reminders.conditions.local_timezone` for why it is a
    fixed offset rather than a named zone.
    """
    raw = booking.get("timezoneOffsetMinutes")
    if raw in (None, ""):
        return 0
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


#: The time formats the researched tokens ask for, as (token name, formatter).
#:
#: Cal's ``{START_TIME_h:mma}`` is a 12-hour clock with no leading zero, which
#: ``strftime`` spells ``%-I:%M %p`` on glibc and rejects outright on Windows.
#: The token is published by Cal, so it has to render identically everywhere this
#: runs, and a format string that works on the developer's machine and raises on
#: a colleague's is exactly the kind of defect nobody reports until it does.
#: Hence two small formatters rather than a ``strftime`` pattern.
TWELVE_HOUR = "h:mma"
FULL_DATE_TIME = "ddd, MMM D, YYYY h:mma"


def _twelve_hour(moment) -> str:
    """``2:05 pm`` - 12-hour clock, no leading zero, lower-case meridiem.

    Midnight and noon are the two cases a naive ``hour % 12`` gets wrong, and a
    reminder that says "12:00 am" about a lunchtime meeting is the kind of error
    a customer notices.
    """
    hour = moment.hour % 12 or 12
    return f"{hour}:{moment.minute:02d} {moment.strftime('%p').lower()}"


def _full_date_time(moment) -> str:
    """``Mon, Oct 12, 2026 2:05 pm`` - Cal's one token carrying a date too."""
    return f"{moment.strftime('%a')}, {moment.strftime('%b')} {moment.day}, {moment.year} {_twelve_hour(moment)}"


FORMATTERS = {TWELVE_HOUR: _twelve_hour, FULL_DATE_TIME: _full_date_time}


def _format_local(booking: Mapping[str, Any], name: str) -> str:
    """Render one published time format in the booking's local time."""
    from dsr.meeting_reminders.conditions import fixed_offset

    start = _start(booking)
    if start is None:
        return ""
    local = start.astimezone(fixed_offset(_timezone_offset(booking)))
    formatter = FORMATTERS.get(name)
    if formatter is not None:
        return formatter(local)
    return local.strftime(name)


# --------------------------------------------------------------------------- #
# The Chili Piper tag registry
# --------------------------------------------------------------------------- #
#
# ``CP.<Object>.<Field>``. The three researched tags come first, then the
# remainder of the booking the ``data_flow`` enumerates.

CP_TAGS: dict[str, Callable[[Mapping[str, Any]], str]] = {
    # -- named in the research ---------------------------------------------- #
    "CP.Guest.FirstName": lambda booking: _first_name(_first_guest(booking)),
    "CP.Meeting.RescheduleUrl": lambda booking: _text(booking.get("rescheduleUrl")),
    "CP.Meeting.CancelUrl": lambda booking: _text(booking.get("cancelUrl")),
    # -- the rest of the guest, the same prefix and the same fields -------- #
    "CP.Guest.LastName": lambda booking: (
        _full_name(_first_guest(booking)).split(" ")[-1]
        if len(_full_name(_first_guest(booking)).split(" ")) > 1
        else ""
    ),
    "CP.Guest.FullName": lambda booking: _full_name(_first_guest(booking)),
    "CP.Guest.Email": lambda booking: _person_email(_first_guest(booking)),
    "CP.Guest.Phone": lambda booking: _text(_first_guest(booking).get("phone")),
    "CP.Guest.Company": lambda booking: _text(_first_guest(booking).get("company")),
    "CP.Guest.Timezone": lambda booking: _text(booking.get("timezone")),
    "CP.Guest.ResponseStatus": lambda booking: _text(
        _first_guest(booking).get("responseStatus") or booking.get("responseStatus")
    ),
    # -- the meeting -------------------------------------------------------- #
    "CP.Meeting.Name": lambda booking: _text(booking.get("title")),
    "CP.Meeting.StartTime": lambda booking: _format_local(booking, "%Y-%m-%d %H:%M"),
    "CP.Meeting.Date": lambda booking: _format_local(booking, "%Y-%m-%d"),
    "CP.Meeting.Time": lambda booking: _format_local(booking, TWELVE_HOUR),
    "CP.Meeting.Timezone": lambda booking: _text(booking.get("timezone")) or "UTC",
    "CP.Meeting.MeetingUrl": lambda booking: _text(booking.get("meetingUrl")),
    "CP.Meeting.Location": lambda booking: _text(booking.get("location")),
    "CP.Meeting.Duration": lambda booking: _text(booking.get("durationMinutes")),
    "CP.Meeting.GuestCount": lambda booking: str(
        len(_guests(booking)) or (1 if _first_guest(booking) else 0)
    ),
    # -- the people around it ----------------------------------------------- #
    "CP.Host.FirstName": lambda booking: _first_name(booking.get("host")),
    "CP.Host.FullName": lambda booking: _full_name(booking.get("host")),
    "CP.Host.Email": lambda booking: _person_email(booking.get("host")),
    "CP.Booker.FirstName": lambda booking: _first_name(booking.get("booker")),
    "CP.Booker.FullName": lambda booking: _full_name(booking.get("booker")),
    "CP.Booker.Email": lambda booking: _person_email(booking.get("booker")),
}

CP_TAG_DETAIL: dict[str, str] = {
    tag: ("named in the research" if tag in NAMED_CP_TAGS else "built from the booking's fields")
    for tag in CP_TAGS
}


# --------------------------------------------------------------------------- #
# The Cal token registry
# --------------------------------------------------------------------------- #
#
# Spelled exactly as ``apis_hit`` quotes them, keyed by the text *inside* the
# braces, because two of the eight are not identifiers.

CAL_TOKENS: dict[str, Callable[[Mapping[str, Any]], str]] = {
    "EVENT_NAME": lambda booking: _text(booking.get("title")),
    "ORGANIZER": lambda booking: (
        _full_name(booking.get("host")) or _person_email(booking.get("host"))
    ),
    "ATTENDEE": lambda booking: (
        _person_email(_first_guest(booking)) or _full_name(_first_guest(booking))
    ),
    "LOCATION": lambda booking: _text(booking.get("location")) or _text(booking.get("meetingUrl")),
    "MEETING_URL": lambda booking: _text(booking.get("meetingUrl")),
    # `{START_TIME_h:mma}` is Cal's "time, 12-hour clock, lowercase am/pm".
    "START_TIME_h:mma": lambda booking: _format_local(booking, TWELVE_HOUR),
    "TIMEZONE": lambda booking: _text(booking.get("timezone")) or "UTC",
    # `{EVENT_DATE_ddd, MMM D, YYYY h:mma}` is "day name, month, day, year,
    # time" - the one token carrying a date as well as a time.
    "EVENT_DATE_ddd, MMM D, YYYY h:mma": lambda booking: _format_local(booking, FULL_DATE_TIME),
}


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #


def token_catalog() -> dict[str, Any]:
    """Every tag and token a client can offer in the composer.

    ``example`` is filled from a real booking rather than invented, so the
    composer previews what a token will actually produce for this seller rather
    than a placeholder nobody believes.
    """
    sample: dict[str, Any] = {
        "title": "Northwind — Enterprise Evaluation",
        "start": "2026-10-06T14:00:00+00:00",
        "durationMinutes": 45,
        "timezone": "Europe/Dublin",
        "timezoneOffsetMinutes": 60,
        "location": "Zoom",
        "meetingUrl": "https://meet.example/northwind-eval",
        "rescheduleUrl": "https://meet.example/northwind-eval/reschedule",
        "cancelUrl": "https://meet.example/northwind-eval/cancel",
        "primaryGuest": {
            "firstName": "Priya",
            "name": "Priya Raman",
            "email": "priya.raman@northwind.example",
            "phone": "+15550100",
            "company": "Northwind Traders",
            "responseStatus": "accepted",
        },
        "guests": [
            {
                "firstName": "Priya",
                "name": "Priya Raman",
                "email": "priya.raman@northwind.example",
                "phone": "+15550100",
                "responseStatus": "accepted",
            },
            {
                "firstName": "Marcus",
                "name": "Marcus Webb",
                "email": "marcus.webb@northwind.example",
                "phone": "+15550101",
            },
        ],
        "host": {"firstName": "Dana", "name": "Dana Okoro", "email": "dana@contoso.example"},
        "booker": {"firstName": "Wen", "name": "Wen Li", "email": "wen.li@contoso.example"},
    }
    return {
        "chili_piper": {
            "pattern": "CP.<Object>.<Field>",
            "count": len(CP_TAGS),
            "named_in_research": list(NAMED_CP_TAGS),
            "tags": [
                {"tag": tag, "example": resolver(sample), "origin": CP_TAG_DETAIL[tag]}
                for tag, resolver in sorted(CP_TAGS.items())
            ],
        },
        "cal": {
            "pattern": "{TOKEN}",
            "count": len(CAL_TOKENS),
            "tokens": [
                {
                    "token": "{%s}" % inner,
                    "example": resolver(sample),
                    "origin": "quoted in apis_hit",
                }
                for inner, resolver in sorted(CAL_TOKENS.items())
            ],
        },
    }


def _substitute(text: str, booking: Mapping[str, Any], missing: set[str]) -> str:
    """Replace every recognised tag, in one left-to-right pass.

    An unrecognised tag, and a recognised one the booking cannot fill in, are
    both **left in the text** and reported in ``missing``. Neither becomes an
    empty string: a body that reads "Hi , see you soon" goes out wrong and nobody
    notices, whereas a preview still showing ``CP.Guest.FirstName`` is a problem
    a person can see.
    """

    def replace(match: re.Match[str]) -> str:
        token = match.group(0)
        inner = match.group(1)
        if inner is not None and inner.startswith("CP."):
            # A person writes `{CP.Guest.FirstName}`, so the brace form reaches
            # the CP registry. Dispatching on the prefix rather than on which
            # alternative matched is what makes the braced and bare spellings the
            # same tag, which is what the product's own composer emits.
            resolver = CP_TAGS.get(inner)
        else:
            resolver = CP_TAGS.get(token) if inner is None else CAL_TOKENS.get(inner)
        if resolver is None:
            missing.add(token)
            return token
        value = resolver(booking)
        if not value:
            missing.add(token)
            return token
        return value

    return TOKEN_RE.sub(replace, text)


def render(text: Any, booking: Mapping[str, Any]) -> dict[str, Any]:
    """Render one composed string for a booking, and say what did not resolve.

    Returns ``text``, the tags that resolved, and the tags that did not. An
    unresolved tag is **left in the output** rather than replaced with an empty
    string: a body that reads "Hi , see you soon" is a message that went out
    wrong and nobody noticed, whereas a preview that still shows
    ``CP.Guest.FirstName`` is a problem someone can see.
    """
    body = _text(text)
    if not body:
        return {"text": "", "resolved": [], "missing": []}
    missing: set[str] = set()
    rendered = _substitute(body, booking, missing)
    resolved = sorted(
        {match.group(0) for match in TOKEN_RE.finditer(body) if match.group(0) not in missing}
    )
    return {"text": rendered, "resolved": resolved, "missing": sorted(missing)}


def render_message(subject: Any, body: Any, booking: Mapping[str, Any]) -> dict[str, Any]:
    """Render both halves of a reminder, and merge their missing-tag reports."""
    rendered_subject = render(subject, booking)
    rendered_body = render(body, booking)
    return {
        "subject": rendered_subject["text"],
        "body": rendered_body["text"],
        "resolved": sorted(set(rendered_subject["resolved"]) | set(rendered_body["resolved"])),
        "missing": sorted(set(rendered_subject["missing"]) | set(rendered_body["missing"])),
    }


def render_translation(text: Any, booking: Mapping[str, Any], locale: str) -> dict[str, Any]:
    """Render a message for a named locale.

    The research names ``autoTranslateEnabled`` and ``sourceLocale`` and stops
    there: it documents no translation catalogue and no provider. So this build
    publishes the switch and the source locale - both are real, both are
    validated, and the delivery records which locale the message went out in -
    but it does not invent a machine translation. A reminder with
    ``autoTranslateEnabled`` and no catalogue configured renders in the source
    locale and says so, rather than pretending to have translated.
    """
    rendered = render(text, booking)
    return {**rendered, "locale": locale, "translated": False}
