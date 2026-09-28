"""Minting a fresh conference for one booking, and never for two.

This is researched user_flow step 3 - "On booking, the system creates a fresh
conference and writes it into the invite's Location field" - and it is the whole
point of the workflow.

The prohibition, and why it is a rule
-------------------------------------
Google's own documentation is the source, and it is unusually direct: "**Warning:**
Reusing Google Meet conference data across different events can cause access
issues and expose meeting details to unintended users." That sentence describes a
security failure, not an inefficiency, and the docs pair it with the instruction
that makes the safe path the only path: "always generate a unique conference for
each event by using the ``createRequest`` field."

So :func:`mint` is built so that reuse is not merely discouraged but
*unrepresentable*: a conference's identity is derived from the booking it
belongs to, and :func:`claim` refuses a second booking that arrives holding a
conference id already spoken for. The check lives in
:mod:`dsr.conference_links.engine` against the store, because "already spoken
for" is a fact about what has been written; this module owns the deterministic
identity and the decision of whether an offered id may be claimed.

What "writes it into the Location field" means here
---------------------------------------------------
Two destinations are named in the researched data_flow - booking ``location`` and
meeting ``meetingLocation`` - and both are written. The Chili Piper
``For New Meeting`` webhook carries ``meetingLocation`` as e.g.
``https://example.zoom.us/j/1234567890``; Cal's booking carries ``location`` as
the integration-typed object. One conference, two fields, and the package never
lets them disagree: :func:`apply_to_booking` is the single place a conference
reaches a booking, so there is no second code path that can write one and forget
the other.

Nothing here opens a socket
---------------------------
The researched endpoints are Google's and Cal's. This product is the source of
the booking, not a proxy for either vendor, so what is real is the *decision* -
which conference, minted with which identity, written to which fields, refused
if it already exists - and those are stored and audited rather than sent. A
:class:`Mint` records the outbound call it *would* make, so a reviewer can see
the exact request without a network call happening, and
:func:`outbound_request` is what a real transport would send.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

from dsr.conference_links import vocabulary as vocab
from dsr.conference_links.errors import ConferenceReuse, LocationError

#: The deterministic prefix of every conference this package mints.
#:
#: Prefixed rather than bare so a conference id in a log is identifiable as ours
#: at a glance, which matters because the researched warning is specifically
#: about a Meet conference id being reused - and a reused id is much easier to
#: spot in an audit log when it is prefixed.
CONFERENCE_PREFIX = "conf"

#: A host hint used when the caller does not name one. Google's URL is
#: "``/j/`` + meeting id" and the researched example is
#: ``https://example.zoom.us/j/1234567890``, so the shape is a per-provider path
#: under a host. A default host keeps the example shape meaningful without
#: claiming any provider's real domain.
ZOOM_LIKE_HOST = "example.zoom.us"


def conference_id(provider: str, booking_uid: str, scope: str = "") -> str:
    """The identity a conference gets, derived from its provider, room and booking.

    Deterministic, which is what makes the reuse prohibition enforceable: two
    attempts at the same booking on the same provider converge on one identity
    rather than racing to mint two, and a mint for a *different* booking cannot
    collide because the booking is in the hash.

    ``scope`` is the room, and it is in the hash because a booking in this
    product is identified by *where it was taken* as well as by its uid - two
    rooms can both hold ``bk_a``. Google's warning is about reuse "across
    different events", and two meetings in two rooms are two events; leaving the
    room out would hand the second buyer's guests the first buyer's link, which
    is the exact exposure the warning describes. :func:`claim` reads across
    rooms for the same reason.

    Derived rather than random on purpose. A random id would make "has this
    conference been used before?" a store lookup, and a lookup that can be
    forgotten; a derived id is the same answer computed by both sides. The
    short hash is a display length, not a security boundary - the guarantee is
    "distinct events get distinct ids", which holds for any practical fleet,
    and a 128-bit id would make the audit log unreadable for no gain.
    """
    if not provider:
        raise LocationError("a conference needs a provider before it can be given an id")
    if not booking_uid:
        raise LocationError("a conference needs a booking before it can be given an id")
    digest = hashlib.sha256(f"{provider}:{scope}:{booking_uid}".encode("utf-8")).hexdigest()
    return f"{CONFERENCE_PREFIX}_{digest[:16]}"


def meeting_number(conference: str) -> str:
    """The trailing digits a Zoom-shaped link needs.

    The researched example is ``https://example.zoom.us/j/1234567890``, so the
    join path is the conference's own identity reduced to digits. Providers
    other than Zoom-shaped ones still get one, because the ``meeting_id`` is
    recorded on every conference as an opaque handle and only Zoom-shaped links
    read it.
    """
    digits = "".join(ch for ch in str(conference) if ch.isdigit())
    if not digits:
        digest = hashlib.sha256(str(conference).encode("utf-8")).hexdigest()
        digits = "".join(ch for ch in digest if ch.isdigit())
    # Ten digits, the shape of the researched example, taken from the end so the
    # result is stable for a given id.
    return (digits or "0")[-10:].ljust(10, "0")


def link_for(provider: str, conference: str, *, host: str | None = None) -> str:
    """The join URL for a conference on one provider.

    Shaped from the researched example rather than invented: the Chili Piper
    ``For New Meeting`` webhook's ``meetingLocation`` is
    ``https://example.zoom.us/j/1234567890``, so a Zoom-shaped provider gets
    ``https://<host>/j/<meeting number>`` and every other provider gets a
    provider-scoped path on the same host. The host is recorded on the
    connection, so a deployment that knows its real domains sets them once in
    Integrations and every link follows.
    """
    resolved_host = str(host or "").strip() or ZOOM_LIKE_HOST
    scheme_host = resolved_host if "://" in resolved_host else f"https://{resolved_host}"
    if provider == "gong":
        # "This one generates a one-time Gong link; however, when clicked, Gong
        # will redirect you to Zoom." The link a guest clicks is the Gong one;
        # the redirect is the provider's business, and what it lands on is
        # recorded on the conference so a reviewer can see both ends.
        return f"{scheme_host}/g/{meeting_number(conference)}"
    return f"{scheme_host}/j/{meeting_number(conference)}"


def mint(
    provider: str,
    booking_uid: str,
    *,
    scope: str = "",
    conference_id_hint: str | None = None,
    host: str | None = None,
    calendar_id: str | None = None,
    start: str | None = None,
) -> dict[str, Any]:
    """Mint a fresh conference for one booking.

    The returned ``create_request`` is what a transport would POST: Google's
    ``conferenceData.createRequest`` with the requestId pinned to the
    conference's own identity, or a Cal-shaped integration payload. It is built
    and stored rather than sent, for the reason in this module's docstring.

    ``scope`` is the room, threaded into :func:`conference_id` - see there for
    why two rooms' identically-named bookings are two events.

    ``conference_id_hint`` is how a caller supplies a conference id it already
    holds - a swap, or a provider that minted one itself.
    :func:`claim` is what decides whether that id may be used; this function
    only builds.
    """
    if not provider:
        raise LocationError("a conference needs a provider")
    if not booking_uid:
        raise LocationError("a conference needs a booking")

    identity = str(conference_id_hint or "").strip() or conference_id(
        provider, booking_uid, scope
    )
    link = link_for(provider, identity, host=host)

    return {
        "conference_id": identity,
        "provider": provider,
        "booking_uid": booking_uid,
        "room_id": scope or None,
        "meeting_id": meeting_number(identity),
        "url": link,
        # The researched prohibition, enforced rather than quoted: `unique` is
        # what `claim` reads, and it is a fact about this conference's scope
        # rather than an instruction to whoever reads the row.
        "unique": True,
        "scope": "per-booking",
        "google_conference_data_version": vocab.GOOGLE_CONFERENCE_DATA_VERSION,
        "create_request": outbound_request(
            provider,
            identity,
            calendar_id=calendar_id,
            start=start,
        ),
        "gateway_url": link_for("zoom", identity, host=host) if provider == "gong" else None,
    }


def outbound_request(
    provider: str,
    conference: str,
    *,
    calendar_id: str | None = None,
    start: str | None = None,
) -> dict[str, Any]:
    """The exact request a transport would make to mint this conference.

    Google's shape comes from the researched ``apis_hit`` verbatim: ``POST``
    ``https://www.googleapis.com/calendar/v3/calendars/{calendarId}/events``
    with ``?conferenceDataVersion=1``, and a body whose ``conferenceData`` uses
    ``createRequest`` because "Reusing Google Meet conference data across
    different events can cause access issues and expose meeting details to
    unintended users."

    A non-Google provider has no researched request of its own - the research
    states plainly that it "did not read Zoom's ``POST /users/{userId}/meetings``
    or Graph ``POST /me/events`` reference" - so its entry carries the Cal
    integration payload, which *is* researched, and says so in
    ``evidence``. Inventing a Zoom request body would be asserting a shape no
    source in this project supports.
    """
    if provider == "google-meet":
        return {
            "method": "POST",
            "url": vocab.GOOGLE_EVENTS_PATH.replace("{calendarId}", str(calendar_id or "primary")),
            "query": {"conferenceDataVersion": vocab.GOOGLE_CONFERENCE_DATA_VERSION},
            "body": {
                "conferenceData": {
                    vocab.GOOGLE_CONFERENCE_CREATE_FIELD: {
                        "requestId": conference,
                    }
                }
            },
            "evidence": vocab.UNIQUE_CONFERENCE_QUOTE,
        }

    return {
        "method": "POST",
        "url": None,
        "query": {},
        "body": {
            "location": {
                "type": "integration",
                "integration": vocab.PROVIDER_WIRE.get(provider, provider),
            }
        },
        "cal_api_version_header": {
            vocab.CAL_API_VERSION_HEADER: vocab.CAL_API_VERSION,
        },
        "evidence": (
            "The research documents Cal's integration enum and its swap endpoint but states it "
            "did not read this provider's own meeting-creation reference, so no request body is "
            "asserted for it beyond the Cal integration location shape."
        ),
    }


def claim(
    existing: list[Mapping[str, Any]],
    conference: str,
    booking_uid: str,
) -> dict[str, Any]:
    """Decide whether ``conference`` may be claimed by ``booking_uid``.

    The researched warning, enforced. ``existing`` is every conference already
    written; the rule is narrow on purpose - a conference held by *the same
    booking* is fine, because that is the create-then-retry path and the docs'
    prohibition is specifically about reuse "across different events".

    Raises :class:`~dsr.conference_links.errors.ConferenceReuse` naming both
    bookings, because a message that only says "already in use" leaves a
    reviewer with no way to tell a correct retry from the failure the warning
    describes.
    """
    for record in existing:
        if str(record.get("conference_id") or "") != str(conference):
            continue
        holder = str(record.get("booking_uid") or "")
        if holder == str(booking_uid):
            return {
                "claimed": False,
                "reason": "this booking already holds this conference",
                "conference_id": conference,
                "booking_uid": booking_uid,
            }
        raise ConferenceReuse(
            f"conference {conference} is already provisioned for booking {holder}; "
            "reusing conference data across different events can cause access issues and "
            "expose meeting details to unintended users, so booking "
            f"{booking_uid} needs a conference of its own"
        )

    return {
        "claimed": True,
        "reason": "no other booking holds this conference",
        "conference_id": conference,
        "booking_uid": booking_uid,
    }


def apply_to_booking(
    conference: Mapping[str, Any],
    booking: Mapping[str, Any],
    *,
    location: Mapping[str, Any] | None = None,
    wire: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Write one conference into a booking's two researched Location fields.

    The researched data_flow names both - "URL written to booking ``location`` /
    meeting ``meetingLocation``" - and the researched webhook carries the second
    as a plain URL. They are written together here and nowhere else, so there is
    no path that sets one and forgets the other, and the returned dict is the
    patch the booking record takes.

    ``location`` overrides the conference's own URL, which is how a swap writes
    a *different* link onto a booking that already had one.
    """
    url = str((location or {}).get("url") or conference.get("url") or "")
    provider = str(conference.get("provider") or "")
    if not url:
        raise LocationError("a provisioned conference must carry a join URL")

    patch: dict[str, Any] = {
        vocab.BOOKING_LOCATION_FIELD: dict(wire) if wire else {"type": "integration", "integration": provider},
        vocab.MEETING_LOCATION_FIELD: url,
        "location_provider": provider,
        "conference_id": conference.get("conference_id"),
    }
    if provider == "gong":
        # "when clicked, Gong will redirect you to Zoom" - recorded so the page
        # can say what a guest will actually land on.
        patch["redirects_to"] = conference.get("gateway_url")
    return patch


__all__ = [
    "CONFERENCE_PREFIX",
    "ZOOM_LIKE_HOST",
    "apply_to_booking",
    "claim",
    "conference_id",
    "link_for",
    "meeting_number",
    "mint",
    "outbound_request",
]
