"""WF-059: provision a per-booking video-conference link (Meet / Zoom / Teams / Gong).

The workflow that decides *where a booked meeting happens* and makes sure the
guest has a fresh, per-booking way into it. Where :mod:`dsr.plays` decides what a
seller is given because of a signal, this package decides what a *prospect* is
given because of a booking: a Google Meet link, a Zoom link, a Gong link that
redirects to Zoom, a static link, a room, or a question the guest is asked to
answer.

The module layout, and why each piece is separate:

``vocabulary``       the values the research fixes by name, served as data
``errors``           one hierarchy, so the feature module registers one handler
``locations``        the researched Location picker, and the wire shape it makes
``connections``      the Integrations tab, and the researched mandatory connection
``minting``          minting a fresh conference, and refusing to reuse one
``swapping``         moving a booking to a different tool, and notifying
``provider_status``  the researched appsStatus report, and the retry it drives
``inferences``       every judgement call, named and served
``engine``           the facade the HTTP layer calls; owns the three collections

Three boundaries are worth stating before reading any of it.

**This package does not import another feature.** It reads and writes only its
own three collections. It never reaches into ``dsr.signals``, ``dsr.plays`` or
``dsr.crm_oauth``, because two features authored independently must not share a
Python module - and a Meeting Type, a provider connection and a booking are all
objects this workflow owns outright.

**Nothing here opens a socket.** The researched endpoints are Google's and Cal's.
This product is the *source* of the booking, not a proxy for either vendor, and
the research states plainly that it did not read Zoom's or Graph's own
meeting-creation references. What is real is the decision - which conference,
minted with which identity, written into which researched Location fields,
refused if another booking already holds it - and those are stored and audited.
:meth:`~dsr.conference_links.minting.mint` records the exact outbound request it
*would* send, Google's included, so a reviewer can read the researched call
without a network call happening.

**The reuse prohibition is enforced, not quoted.** Google's own warning -
"Reusing Google Meet conference data across different events can cause access
issues and expose meeting details to unintended users" - describes a security
failure. :func:`~dsr.conference_links.minting.claim` refuses a second booking
offering a conference id another booking holds, and names both bookings in the
message, because the difference between a correct retry and the failure the
warning describes is entirely in which booking holds the id.
"""

from __future__ import annotations

from dsr.conference_links import (
    connections,
    inferences,
    locations,
    minting,
    provider_status,
    swapping,
    vocabulary,
)
from dsr.conference_links.engine import (
    BOOKINGS,
    CONNECTIONS,
    DEFAULT_LOCATION_KIND,
    MEETING_LOCATIONS,
    ProvisioningEngine,
    booking_state,
)
from dsr.conference_links.errors import (
    BookingError,
    BookingNotFound,
    ConferenceLinkError,
    ConferenceNotFound,
    ConferenceReuse,
    ConnectionError,
    ConnectionNotFound,
    DefaultLocationRequired,
    DuplicateProviderConnection,
    LocationError,
    LocationNotFound,
    LocationUnchanged,
    ProviderNotConnected,
    UnknownLocation,
    UnknownLocationKind,
    UnknownProvider,
)
from dsr.conference_links.locations import (
    catalogue,
    describe_kind,
    expected_outcome,
    missing_for,
    needs_conference,
    normalise_kind,
    provider_for,
    wire_location,
)
from dsr.conference_links.minting import (
    claim,
    conference_id,
    link_for,
    meeting_number,
    mint,
    outbound_request,
)
from dsr.conference_links.provider_status import (
    APP_STATES,
    RETRY_ATTEMPTS,
    STATUS_FIELDS,
    fallback_for,
    judge,
)
from dsr.conference_links.swapping import (
    SWAP_REASONS,
    plan_swap,
    render_invite,
    same_location,
)
from dsr.conference_links.vocabulary import (
    BOOKING_LOCATION_UPDATED,
    CAL_BOOKING_WRITE_SCOPE,
    CANCEL_TAG,
    DYNAMIC_TAGS,
    GOOGLE_CONFERENCE_DATA_VERSION,
    LOCATION_KINDS,
    LOCATION_TYPES,
    ONE_TIME_KINDS,
    RESCHEDULE_TAG,
)

__all__ = [
    "APP_STATES",
    "BOOKINGS",
    "BOOKING_LOCATION_UPDATED",
    "BookingError",
    "BookingNotFound",
    "CAL_BOOKING_WRITE_SCOPE",
    "CANCEL_TAG",
    "CONNECTIONS",
    "ConferenceLinkError",
    "ConferenceNotFound",
    "ConferenceReuse",
    "ConnectionError",
    "ConnectionNotFound",
    "DEFAULT_LOCATION_KIND",
    "DYNAMIC_TAGS",
    "DefaultLocationRequired",
    "DuplicateProviderConnection",
    "GOOGLE_CONFERENCE_DATA_VERSION",
    "LOCATION_KINDS",
    "LOCATION_TYPES",
    "LocationError",
    "LocationNotFound",
    "LocationUnchanged",
    "MEETING_LOCATIONS",
    "ONE_TIME_KINDS",
    "ProviderNotConnected",
    "ProvisioningEngine",
    "RESCHEDULE_TAG",
    "RETRY_ATTEMPTS",
    "STATUS_FIELDS",
    "SWAP_REASONS",
    "UnknownLocation",
    "UnknownLocationKind",
    "UnknownProvider",
    "booking_state",
    "catalogue",
    "claim",
    "connections",
    "conference_id",
    "describe_kind",
    "expected_outcome",
    "fallback_for",
    "inferences",
    "judge",
    "link_for",
    "locations",
    "meeting_number",
    "mint",
    "minting",
    "missing_for",
    "needs_conference",
    "normalise_kind",
    "outbound_request",
    "plan_swap",
    "provider_for",
    "provider_status",
    "render_invite",
    "same_location",
    "swapping",
    "vocabulary",
    "wire_location",
]
