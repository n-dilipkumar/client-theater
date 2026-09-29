"""The researched vocabulary of WF-059: what a meeting's Location may be.

Everything fixed *by name* in the research lives here, and nothing else in this
package is allowed to hard-code one of these strings. A client renders its
pickers from :func:`describe` rather than from a list compiled into the page, so
a value added here reaches every client at once.

The sourced half
----------------

**The Location picker.** user_flow step 1 names seven options on the Meeting
Type: ``Google Meet``, ``Zoom``, ``Gong``, ``Conference Details``,
``In-Person Meeting``, ``Custom`` and ``Ask the Guest (Provide My Own)``.
features_tools adds the two facts that make it a picker rather than a field:
"multiple locations with a 'Set as Default'", and the ``Conference Details``
text field.

**What each one generates**, quoted from the Chili Piper Meeting Types page:

* "**Google Meet:** This option generates a one-time Google Meet link to be
  displayed in the Location."
* "**Zoom:** This one generates a one-time Zoom link."
* "**Gong**: This one generates a one-time Gong link; however, when clicked,
  Gong will redirect you to Zoom."
* "**Conference Details:** This is a text field where you can manually enter the
  Location details. This option is normally used to include links, like static
  Zoom ones, for those who don't want to use one-time links"
* "**Ask the Guest (Provide My Own):** This field will enable your prospects to
  provide the Location themselves."

That is the whole shape of this workflow: three kinds that mint a *fresh,
per-booking* conference, three that do not, and one that is decided by somebody
who is not us.

**The mandatory connection.** step 2: "Connecting Zoom on the Integrations tab
is mandatory for this one to work", and automations says the same is true of
Meet and Gong. So a provider connection is a *prerequisite*, not a nicety -
see :mod:`dsr.conference_links.links`.

**The wire forms.** Cal's booking-create accepts these location types -
``address, attendeeAddress, attendeeDefined, attendeePhone, integration, link,
phone, organizersDefaultApp`` - and an ``integration`` location carries an
``integration`` enum of about thirty members. Cal's
``PATCH /v2/bookings/{bookingUid}/location`` "also provisions a conference
link" for integration locations and emails the attendees; its documented header
is ``cal-api-version: 2024-08-13`` and its scope is ``BOOKING_WRITE``.

**Google's own rule**, which is the one this workflow exists to satisfy: "To
create new conference details use the ``createRequest`` field. To persist your
changes, remember to set the ``conferenceDataVersion`` request parameter to
``1`` for all event modification requests. **Warning:** Reusing Google Meet
conference data across different events can cause access issues and expose
meeting details to unintended users." The warning is a prohibition, and
:mod:`dsr.conference_links.links` enforces it as one.

**The swap.** "``BOOKING_LOCATION_UPDATED`` ... The payload mirrors the standard
booking payload, with one addition: ``previousLocation`` holds the location
before the change and existing ``location`` holds the new location."

**The failure report.** "on provider failure Cal reports ``appsStatus[]`` per app
(``appName``, ``success``, ``failures``, ``errors``) in the booking/webhook
payload, which is what an integration should watch to retry or fall back."
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

# --------------------------------------------------------------------------- #
# The Location picker
# --------------------------------------------------------------------------- #

#: The seven options user_flow step 1 names, in the order it names them.
LOCATION_KINDS: tuple[str, ...] = (
    "google-meet",
    "zoom",
    "gong",
    "conference-details",
    "in-person",
    "custom",
    "attendee-defined",
)

#: The label each option carries in the admin's picker, from the same page.
LOCATION_LABELS: Mapping[str, str] = {
    "google-meet": "Google Meet",
    "zoom": "Zoom",
    "gong": "Gong",
    "conference-details": "Conference Details",
    "in-person": "In-Person Meeting",
    "custom": "Custom",
    "attendee-defined": "Ask the Guest (Provide My Own)",
}

#: The three kinds that mint a fresh conference for each booking.
#:
#: Read straight off the evidence: "generates a one-time Google Meet link",
#: "generates a one-time Zoom link", "generates a one-time Gong link". Nothing
#: else on the picker creates a conference, which is why the other four are
#: refused a connection rather than quietly provisioned against one.
ONE_TIME_KINDS: frozenset[str] = frozenset({"google-meet", "zoom", "gong"})

#: Kinds whose location is supplied by somebody other than the organizer.
#:
#: "This field will enable your prospects to provide the Location themselves."
#: A booking on this kind is *waiting*, not *broken*, and no conference exists
#: until the guest supplies one.
GUEST_SUPPLIED_KINDS: frozenset[str] = frozenset({"attendee-defined"})

#: The Cal booking-location ``type`` each kind produces.
#:
#: Four of the seven map to a type the research lists by name:
#: ``integration`` for the three one-time providers, ``attendeeDefined`` for Ask
#: the Guest, and ``link`` for Conference Details - extensibility calls the
#: static link "a `link` escape hatch". ``in-person`` and ``custom`` are this
#: build's reading and are recorded as the ``location-type-mapping``
#: inference in :mod:`dsr.conference_links.inferences`.
LOCATION_TYPE_WIRE: Mapping[str, str] = {
    "google-meet": "integration",
    "zoom": "integration",
    "gong": "integration",
    "conference-details": "link",
    "in-person": "address",
    "custom": "address",
    "attendee-defined": "attendeeDefined",
}

#: The Cal ``integration`` enum value each one-time kind sends.
#:
#: ``google-meet`` and ``zoom`` are members of the researched enum. ``gong`` is
#: **not**: the thirty values the research lists contain no Gong, and Gong is a
#: Chili Piper provider rather than a Cal one. It is carried anyway, because
#: step 1 offers Gong as a first-class option and dropping it would drop a
#: requirement the research states plainly. See the ``gong-enum-value`` entry in
#: :mod:`dsr.conference_links.inferences`.
PROVIDER_WIRE: Mapping[str, str] = {
    "google-meet": "google-meet",
    "zoom": "zoom",
    "gong": "gong",
}

# --------------------------------------------------------------------------- #
# The provider catalogue
# --------------------------------------------------------------------------- #

#: Cal's documented ``integration`` enum, verbatim and in the order the research
#: lists it. "Cal exposes ~30 video integrations plus a ``link`` escape hatch" -
#: this is the ~30, and the escape hatch is :data:`LOCATION_TYPE_WIRE`'s ``link``.
CAL_INTEGRATION_ENUM: tuple[str, ...] = (
    "cal-video",
    "google-meet",
    "zoom",
    "whereby-video",
    "whatsapp-video",
    "webex-video",
    "telegram-video",
    "tandem",
    "sylaps-video",
    "skype-video",
    "sirius-video",
    "signal-video",
    "shimmer-video",
    "salesroom-video",
    "roam-video",
    "riverside-video",
    "ping-video",
    "office365-video",
    "mirotalk-video",
    "jitsi",
    "jelly-video",
    "jelly-conferencing",
    "huddle",
    "facetime-video",
    "element-call-video",
    "eightxeight-video",
    "discord-video",
    "demodesk-video",
    "campfire-video",
)

#: The location types Cal accepts on booking create, in the researched order.
LOCATION_TYPES: tuple[str, ...] = (
    "address",
    "attendeeAddress",
    "attendeeDefined",
    "attendeePhone",
    "integration",
    "link",
    "phone",
    "organizersDefaultApp",
)

#: The three providers this workflow's own picker can provision, keyed by the
#: Location kind that provisions them. "providers are swappable per Meeting
#: Type" is the extensibility note, and these are the three it means.
PICKER_PROVIDERS: tuple[str, ...] = ("google-meet", "zoom", "gong")

# --------------------------------------------------------------------------- #
# The outbound wire shapes
# --------------------------------------------------------------------------- #

#: Cal's documented API version header value, from the update-booking-location
#: page. Sent on the location swap; recorded so no caller hard-codes the string.
CAL_API_VERSION_HEADER = "cal-api-version"
CAL_API_VERSION = "2024-08-13"

#: The OAuth scope the researched swap requires.
CAL_BOOKING_WRITE_SCOPE = "BOOKING_WRITE"

#: Google's conference-creation call, and the query parameter its docs say is
#: "for all event modification requests". A conference that is not persisted
#: with ``conferenceDataVersion=1`` is a conference that does not exist.
GOOGLE_EVENTS_PATH = "https://www.googleapis.com/calendar/v3/calendars/{calendarId}/events"
GOOGLE_CONFERENCE_DATA_VERSION = 1

#: "always generate a unique conference for each event by using the
#: ``createRequest`` field." The field is named here because the researched rule
#: is about *using* it rather than supplying conference data.
GOOGLE_CONFERENCE_CREATE_FIELD = "createRequest"

#: The dynamic tags the researched invite body can embed.
#:
#: "the invite body can embed reschedule/cancel URLs via dynamic tags", and
#: features_tools names them: ``CP.Meeting.RescheduleUrl`` and
#: ``CP.Meeting.CancelUrl``.
RESCHEDULE_TAG = "CP.Meeting.RescheduleUrl"
CANCEL_TAG = "CP.Meeting.CancelUrl"
DYNAMIC_TAGS: tuple[str, ...] = (RESCHEDULE_TAG, CANCEL_TAG)

#: The webhook that announces a location change, and the field that carries the
#: old value beside the new one.
BOOKING_LOCATION_UPDATED = "BOOKING_LOCATION_UPDATED"
PREVIOUS_LOCATION_FIELD = "previousLocation"

#: The per-app failure report automations names, field for field.
APPS_STATUS_FIELDS: tuple[str, ...] = ("appName", "success", "failures", "errors")

#: The fields the researched `For New Meeting` webhook carries, of which
#: ``meetingLocation`` is the one this workflow writes: the Chili Piper example
#: is "https://example.zoom.us/j/1234567890".
MEETING_LOCATION_FIELD = "meetingLocation"
BOOKING_LOCATION_FIELD = "location"

# --------------------------------------------------------------------------- #
# The four provisioning outcomes
# --------------------------------------------------------------------------- #

#: What provisioning a booking's location did.
#:
#: Three of the four are not outcomes at all - a static link, a room, and a
#: waiting guest are all the researched *absence* of a conference - and they are
#: named here rather than represented by an empty result, because a booking
#: that will never get a link and a booking nobody has looked at yet are very
#: different things to a seller reading a meetings list.
PROVISION_OUTCOMES: tuple[str, ...] = (
    "conference-provisioned",
    "static",
    "in-person",
    "awaiting-guest",
)

#: What a booked meeting's Location is doing right now.
LOCATION_STATES: tuple[str, ...] = (
    "unprovisioned",
    "provisioned",
    "provision-failed",
    "static",
    "in-person",
    "awaiting-guest",
    "swapped",
)

# --------------------------------------------------------------------------- #
# The quotes, so a test can assert the rule still has its source
# --------------------------------------------------------------------------- #

#: Google's warning, which is the prohibition this workflow exists to enforce.
REUSE_WARNING = (
    "Reusing Google Meet conference data across different events can cause access "
    "issues and expose meeting details to unintended users."
)

#: Google's instruction that makes a per-booking conference rather than a
#: configured one the only correct answer.
UNIQUE_CONFERENCE_QUOTE = (
    "always generate a unique conference for each event by using the `createRequest` field."
)

#: "Connecting Zoom on the Integrations tab is mandatory for this one to work."
CONNECTION_MANDATORY_QUOTE = (
    "Connecting Zoom on the Integrations tab is mandatory for this one to work"
)

#: Cal's swap, which provisions *and* notifies in one call.
SWAP_PROVISIONS_QUOTE = (
    "For integration locations (e.g. Zoom, Google Meet, Cal Video), the endpoint also "
    "provisions a conference link. Attendees are notified of the location change by email."
)

#: The webhook's shape, including the field that makes a swap auditable.
PREVIOUS_LOCATION_QUOTE = (
    "`BOOKING_LOCATION_UPDATED` ... The payload mirrors the standard booking payload, with one "
    "addition: `previousLocation` holds the location before the change and existing `location` "
    "holds the new location."
)

#: Why there is a `link` escape hatch at all.
STATIC_LINK_QUOTE = (
    "This option is normally used to include links, like static Zoom ones, for those who "
    "don't want to use one-time links"
)

#: Why Gong is on the picker at all despite its redirect.
GONG_REDIRECT_QUOTE = (
    "This one generates a one-time Gong link; however, when clicked, Gong will redirect you "
    "to Zoom."
)

#: What the failure report is *for*, in the research's own words.
APPS_STATUS_QUOTE = (
    "on provider failure Cal reports `appsStatus[]` per app (`appName`, `success`, `failures`, "
    "`errors`) in the booking/webhook payload, which is what an integration should watch to "
    "retry or fall back"
)


def describe() -> dict[str, Any]:
    """Every researched value this workflow fixes by name, served as data.

    Returned whole from ``GET /api/wf-059/vocabulary``. The point of serving it
    is that the Location picker on the admin page, the provider checklist, the
    invite-template editor's tag list and the wire-shape preview all read from
    this one function, so a value added here reaches all of them at once - and
    so a reviewer can read the whole sourced vocabulary in one place instead of
    grepping for it.
    """
    return {
        "location_kinds": list(LOCATION_KINDS),
        "location_labels": dict(LOCATION_LABELS),
        "one_time_kinds": sorted(ONE_TIME_KINDS),
        "guest_supplied_kinds": sorted(GUEST_SUPPLIED_KINDS),
        "location_type_wire": dict(LOCATION_TYPE_WIRE),
        "provider_wire": dict(PROVIDER_WIRE),
        "cal_integration_enum": list(CAL_INTEGRATION_ENUM),
        "cal_integration_enum_count": len(CAL_INTEGRATION_ENUM),
        "location_types": list(LOCATION_TYPES),
        "picker_providers": list(PICKER_PROVIDERS),
        "provision_outcomes": list(PROVISION_OUTCOMES),
        "location_states": list(LOCATION_STATES),
        "apps_status_fields": list(APPS_STATUS_FIELDS),
        "dynamic_tags": list(DYNAMIC_TAGS),
        "google": {
            "events_path": GOOGLE_EVENTS_PATH,
            "conference_data_version": GOOGLE_CONFERENCE_DATA_VERSION,
            "create_request_field": GOOGLE_CONFERENCE_CREATE_FIELD,
        },
        "cal": {
            "api_version_header": CAL_API_VERSION_HEADER,
            "api_version": CAL_API_VERSION,
            "booking_write_scope": CAL_BOOKING_WRITE_SCOPE,
        },
        "webhook": {
            "event": BOOKING_LOCATION_UPDATED,
            "previous_location_field": PREVIOUS_LOCATION_FIELD,
            "new_location_field": BOOKING_LOCATION_FIELD,
        },
        "quotes": {
            "reuse_warning": REUSE_WARNING,
            "unique_conference": UNIQUE_CONFERENCE_QUOTE,
            "connection_mandatory": CONNECTION_MANDATORY_QUOTE,
            "swap_provisions": SWAP_PROVISIONS_QUOTE,
            "previous_location": PREVIOUS_LOCATION_QUOTE,
            "static_link": STATIC_LINK_QUOTE,
            "gong_redirect": GONG_REDIRECT_QUOTE,
            "apps_status": APPS_STATUS_QUOTE,
        },
    }
