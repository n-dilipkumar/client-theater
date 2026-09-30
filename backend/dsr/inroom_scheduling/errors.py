"""Domain errors for WF-058, the bookable calendar embedded in the sales room.

One base type, mapped once by the feature module to 400, and a subclass for each
refusal a caller can actually act on. Keeping them distinct is not decoration: the
page renders a different message for "the hold expired, pick another slot" than it
does for "this is a personal event, so it cannot be an instant booking", and a
single message for both is a page nobody can use.

``RecordNotFound`` is deliberately *not* claimed. The core app already maps it to
404, and a second handler for one type is a collision the plugin host refuses.

Every one of these is a refusal rather than a crash, so the caller can correct the
request and try again; nothing here is raised for a state the caller cannot see.
"""

from __future__ import annotations

from typing import Any


class SchedulingError(ValueError):
    """An embed, slot, hold or booking request cannot be honoured as written."""


class SelectorError(SchedulingError):
    """The slot query did not name an event type in exactly one researched way.

    ``GET /v2/slots`` accepts an ``eventTypeId``, a ``username`` with a slug, a
    ``teamSlug`` with a slug, or a ``usernames`` list. Naming none of them, or more
    than one, cannot be resolved to a single schedule.
    """


class UnknownEventType(SchedulingError):
    """The named event type, host, or routing form does not exist."""


class SlotUnavailable(SchedulingError):
    """The requested start is not an offered slot, or something else holds it.

    Carries the reason and the alternatives, because the researched flow is a
    grid: a refused slot is only useful to a prospect if the page can offer the
    next one. The keyword arguments are all optional so the type also works for a
    caller that only has a message.
    """

    def __init__(
        self,
        message: str,
        *,
        reason: str = "unavailable",
        slot: dict | None = None,
        alternatives: list[dict] | None = None,
    ) -> None:
        super().__init__(message)
        self.reason = reason
        self.slot = slot or {}
        self.alternatives = alternatives or []


class HoldExpired(SchedulingError):
    """The reservation's ``reservationUntil`` has passed.

    "no user action needed for the hold to expire" means the *hold* needs no
    action; the booking that was waiting on it does. This is the error that tells
    the page to re-offer slots, and it carries the moment it lapsed so the client
    can show a countdown that has already ended rather than a bare failure.
    """

    def __init__(
        self,
        message: str,
        *,
        uid: str = "",
        until: str = "",
        alternatives: list[dict] | None = None,
    ) -> None:
        super().__init__(message)
        self.uid = uid
        self.until = until
        self.alternatives = alternatives or []


class HoldRequired(SchedulingError):
    """The room's embed requires a hold before a booking, and none was supplied.

    Optional in Cal.com - ``POST /v2/bookings`` books a free slot directly - so
    this is only raised where a deployment has deliberately turned the hold into a
    gate. Configured, not assumed.
    """


class RecurrenceOutOfRange(SchedulingError):
    """``recurrenceCount`` is below 1 or above the documented maximum of 32.

    Carries the offending value and the maximum, because "recurrence is out of
    range" is not actionable and 40-versus-32 is.
    """

    def __init__(
        self,
        message: str,
        *,
        limit: str = "recurrenceCount",
        value: Any = None,
        maximum: int = 0,
    ) -> None:
        super().__init__(message)
        self.limit = limit
        self.value = value
        self.maximum = maximum


class InstantNeedsTeamEvent(SchedulingError):
    """``instant: true`` on anything but a team event.

    Sourced: instant bookings are documented as team events only. Carries the
    event type and its kind so the page can say which of the two to change.
    """

    def __init__(self, message: str, *, event_type: str = "", kind: str = "") -> None:
        super().__init__(message)
        self.event_type = event_type
        self.kind = kind


class BookingConflict(SchedulingError):
    """The start is already taken, or an instant booking has nothing to take.

    Carries the reason, because "no free slot" and "that slot is gone" need
    different answers from a page: the first widens the window, the second
    re-offers the grid.
    """

    def __init__(self, message: str, *, reason: str = "conflict") -> None:
        super().__init__(message)
        self.reason = reason


class MetadataOutOfRange(SchedulingError):
    """Booking metadata broke one of the three documented limits.

    Carries which limit, because "metadata is too big" is useless to a caller
    holding fifty-one keys. All three limits are quoted in the message: the
    refusal is the only place a caller who has not read the source will see the
    numbers.
    """

    def __init__(
        self,
        message: str,
        *,
        limit: str = "",
        key: str = "",
        value: Any = None,
        maximum: int = 0,
    ) -> None:
        super().__init__(message)
        self.limit = limit
        self.key = key
        self.value = value
        self.maximum = maximum


class BookingFieldRejected(SchedulingError):
    """A booking field was missing, unknown, or tried to change a read-only value."""


class TokenExpired(SchedulingError):
    """The embed cannot book, and the reason is the token.

    The research's step 1 is the whole reason a token exists: "stands up an OAuth
    client so the app can act on behalf of a scheduling user". Without a live token
    there is no scheduling user to act as, so a booking is refused rather than
    written as if there were.

    The reason is carried because the three ways this happens need three different
    fixes, and sending somebody to reconnect for a scope problem wastes an afternoon.
    """

    def __init__(self, message: str, *, reason: str = "no_token", expires_at: str = "") -> None:
        super().__init__(message)
        self.reason = reason
        self.expires_at = expires_at


class EmbedConfigError(SchedulingError):
    """The room's embed configuration names a component, token or event type that
    does not exist, or a CSS custom property the embed does not publish."""


class RoutingError(SchedulingError):
    """A routing form, or a routing answer, cannot be used as written.

    Separate from :class:`SchedulingError` because routing has the one hard
    structural requirement in the whole package - a form must end in a catch-all -
    and a reviewer reading about the fall-through should not have to find it in a
    generic message.
    """
