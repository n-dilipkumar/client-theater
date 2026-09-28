"""Domain errors for WF-064: reschedule or cancel a meeting, and propagate it.

One error type is registered with the host, mapped once by the feature module.
Starlette resolves a handler by walking the raised class's MRO, so the two
subclasses below are covered by the same registration and stay distinguishable by
their ``status``.

Two of the three statuses are the point of the subclasses:

* **400** - the request cannot be honoured as written. A malformed token, a
  reschedule to a slot that is not in the recomputed availability, a recurrence
  scope naming a series the booking is not part of.
* **404** - nothing to act on. The booking does not exist at all.
* **409** - the request is well formed and conflicts with current state. A
  booking that has already been cancelled, a recurring instance that was already
  cancelled by an earlier ``all`` sweep.

``RecordNotFound`` is deliberately *not* claimed. The core app already maps it to
404, and two handlers for one type is a collision the feature host refuses.
"""

from __future__ import annotations


class MeetingChangeError(ValueError):
    """A reschedule or cancel cannot be honoured as written.

    The status rides on the exception rather than being chosen per raise site, so
    "this is a conflict, not a typo" is decided once where the distinction is
    drawn rather than at twenty call sites.
    """

    status = 400
    code = "meeting_change_error"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code


class MeetingNotFound(MeetingChangeError):
    """The named booking does not exist."""

    status = 404
    code = "booking_not_found"


class BookingConflict(MeetingChangeError):
    """The request is well formed but the booking has moved on.

    A booking that was already cancelled, a link whose booking is no longer live,
    a second attempt to complete a reschedule request that has been completed.
    """

    status = 409
    code = "booking_conflict"


class LinkExpired(MeetingChangeError):
    """The reschedule link has expired.

    A distinct type, not a generic 400, because the researched setting behind it
    is explicit - "Expire Reschedule Link ... allows you to decide if the reschedule
    link should expire after a meeting has happened" - and because it is a
    *setting* doing what it was configured to do rather than a malformed request.
    It is a 410 rather than a 400: the link was valid once, and the answer to
    "what now?" is that a rep has to act, not that the caller typed it wrong.
    """

    status = 410
    code = "reschedule_link_expired"
