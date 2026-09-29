"""One error hierarchy for the conference-link workflow.

Every refusal this package makes is caused by something the caller sent or
asked for, so the types share a base and the feature module registers a single
handler for it. Anything that is *not* a :class:`ConferenceLinkError` is a bug
and must propagate.

``code`` and ``status`` ride on the exception rather than being decided in the
handler, for the reason the rest of the codebase uses: a Location kind that
names no provider and a Location kind whose provider has not been connected are
both this package's domain errors, but the first is a malformed body and the
second conflicts with state that already exists, and a handler that answered
400 for both would be lying about the second.

The distinction that matters most here is *refused* and *reported*. A booking
whose Location is ``Conference Details`` is not an error - the research says
that option is "for those who don't want to use one-time links" - and neither is
a booking whose Location is ``Ask the Guest``; the guest has simply not answered
yet. Both are provisioning *outcomes*, and both are named ones. What refuses is
a caller mistake, and a missing prerequisite the research calls mandatory.
"""

from __future__ import annotations


class ConferenceLinkError(ValueError):
    """A conference-link request cannot be honoured as written.

    A ``ValueError`` because every subclass is caused by something the caller
    sent or asked for. Nothing in this package raises for a fault of its own.
    """

    code = "conference_link_error"
    status = 400


# --------------------------------------------------------------------------- #
# The Meeting Type Location
# --------------------------------------------------------------------------- #


class LocationError(ConferenceLinkError):
    """A Location configuration cannot be created, amended or removed as described."""


class UnknownLocationKind(LocationError):
    """The Location names an option the researched picker does not contain.

    400, and the message lists what the picker does contain. A kind that is not
    one of the seven named options is a caller mistake, not a conflict with
    stored state, so this must not be answered 409 - a client that catches the
    one status and shows a "someone else is editing this" message would be
    lying about a typo.
    """

    code = "unknown_location_kind"
    status = 400


class LocationNotFound(LocationError):
    """The Location named by a path does not exist, or has been removed.

    404, and a type of this package's own rather than the core
    :class:`~dsr.db.audited.RecordNotFound`. Claiming the core type would be an
    exception-handler collision the host refuses, and this feature has no use
    for the core one.
    """

    code = "location_not_found"
    status = 404


class DefaultLocationRequired(LocationError):
    """A Location that is the default could not be made so, or stopped being it.

    Two situations, one error, because both are the same broken state seen from
    either side: the picker has no default, and the picker has two. "multiple
    locations with a 'Set as Default'" means exactly one is in force, so a
    request that would leave the Meeting Type with no default - or would leave
    it with two - is refused rather than silently resolved.
    """

    code = "default_location_required"
    status = 409


# --------------------------------------------------------------------------- #
# The Integrations tab
# --------------------------------------------------------------------------- #


class ConnectionError(ConferenceLinkError):
    """An Integrations connection body is malformed or unusable."""


class UnknownProvider(ConnectionError):
    """The connection names a provider this workflow does not offer.

    400 rather than 409. The provider catalogue is a fixed, researched list and
    a name outside it is a caller mistake; whether a connection of this kind
    already exists is a different question and is answered by
    :class:`DuplicateProviderConnection`.
    """

    code = "unknown_provider"
    status = 400


class DuplicateProviderConnection(ConnectionError):
    """This host already has a connection for that provider.

    409, because the request is well formed and conflicts with state that
    already exists. "provider OAuth connection" is one per host per provider -
    the research describes swapping a *rep's* integration, not accumulating
    several - so a second one would leave the picker choosing between two
    credentials with nothing to choose on.
    """

    code = "provider_already_connected"
    status = 409


class ConnectionNotFound(ConnectionError):
    """The connection named by a path does not exist, or has been removed."""

    code = "connection_not_found"
    status = 404


class ProviderNotConnected(ConferenceLinkError):
    """The provider a Location needs has not been connected.

    This is the researched mandatory step, and it is a refusal rather than a
    warning because the research says so: "Connecting Zoom on the Integrations
    tab is mandatory for this one to work". 409 - the request is perfectly well
    formed, and what it conflicts with is the state of the store, which is the
    same shape as a Play registered against a signal registration that does not
    exist.
    """

    code = "provider_not_connected"
    status = 409


# --------------------------------------------------------------------------- #
# Bookings and their conferences
# --------------------------------------------------------------------------- #


class BookingError(ConferenceLinkError):
    """A booking cannot be created, provisioned or swapped as described."""


class UnknownLocation(BookingError):
    """The booking names a Location this product does not hold.

    409 rather than 400. The researched flow starts at the Meeting Type's
    Location setting, so a booking pointing at no Location is a missing
    prerequisite rather than a typo, and the message names the collection the
    lookup searched so a caller can tell a wrong id from a wrong stage.
    """

    code = "location_not_found"
    status = 409


class BookingNotFound(BookingError):
    """The booking named by a path does not exist, or is in another room.

    404, and deliberately *not* a room-membership error. This product's rooms
    carry no ACL, so "not in this room" and "not there at all" are the same
    answer and saying otherwise would be a claim the store cannot back.
    """

    code = "booking_not_found"
    status = 404


class ConferenceReuse(BookingError):
    """A conference was offered that another event already holds.

    409, and this is the one refusal in the package that is quoting a warning
    rather than a rule: "**Warning:** Reusing Google Meet conference data across
    different events can cause access issues and expose meeting details to
    unintended users." Offering the same conference to two bookings is not a
    style problem, it is the failure the warning describes, so it is refused and
    the message names both bookings.
    """

    code = "conference_already_in_use"
    status = 409


class ConferenceNotFound(BookingError):
    """The conference named by a path does not exist for this booking."""

    code = "conference_not_found"
    status = 404


class LocationUnchanged(BookingError):
    """The swap was asked for a location the booking already has.

    409, and the smallest refusal in the package. The researched swap notifies
    attendees by email; a swap to the location a booking already has would
    email every attendee that nothing changed, so it is refused rather than
    answered as a no-op. A caller retrying after a timeout gets told why rather
    than sending a second round of mail.
    """

    code = "location_unchanged"
    status = 409
