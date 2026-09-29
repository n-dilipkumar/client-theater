"""Domain errors for WF-056 headless booking.

Three types, three distinct HTTP answers, and all three are this feature's own.
``RecordNotFound`` and ``AuditError`` are deliberately *not* claimed: the core app
already maps both correctly, and the host refuses a second handler for a type that
is already handled, so claiming them would turn this feature into a load failure.

The split is the researched one, not an arbitrary one:

* :class:`HeadlessBookingError` - well-formed JSON asking for something this
  layer will not do. 400. Every refusal in this package that is the caller's to
  fix: an unknown section, a slot that was never offered, a session that has
  already been used.
* :class:`PermissionDenied` - the call is well formed but this identity may not
  make it. 403. A token without the ``Schedule`` permission for the section it
  is being used on, or a non-admin trying to generate one. The research separates
  these from ordinary errors by making them about *who is asking*: "Only users
  with the **Admin** role can generate API tokens in Command Center."
* :class:`NotFound` - an id that does not resolve. 404.

The subclasses exist so a blanket ``except HeadlessBookingError`` still catches
everything in this package, while the HTTP layer can still tell the three apart.
"""

from __future__ import annotations


class HeadlessBookingError(ValueError):
    """A headless booking request cannot be honoured as written."""


class PermissionDenied(HeadlessBookingError):
    """The caller's credentials do not permit this call.

    Kept apart from :class:`HeadlessBookingError` because the remedy is
    different: a 400 says "fix your request", a 403 says "get a token that can
    do this". The research makes the distinction explicit when it says a token
    needs "the ``Schedule`` permission for the relevant section ... plus ``Read``
    where listing assets is needed" - the scope is per-section, so refusing is
    about the credential, not about the payload.
    """

    def __init__(self, message: str, *, required: str = "", section: str = "") -> None:
        super().__init__(message)
        #: The permission that would have allowed the call, e.g. ``"schedule"``.
        self.required = required
        #: The section whose scope was checked.
        self.section = section


class NotFound(HeadlessBookingError):
    """A room, asset, session, meeting or credential id does not resolve."""

    def __init__(self, message: str, *, resource: str = "", record_id: str = "", room_id: str = "") -> None:
        super().__init__(message)
        self.resource = resource
        self.record_id = record_id
        self.room_id = room_id


class Refusal(HeadlessBookingError):
    """A refusal that names its own researched reason code.

    The reason is one of the keys in
    :data:`dsr.headless_booking.vocabulary.SCHEDULE_FAILURES` or
    :data:`~dsr.headless_booking.vocabulary.INIT_FAILURES`, and it travels in
    the HTTP body so a client can branch on it instead of pattern-matching prose.

    This exists as a type rather than as a convention because the reason has to
    survive being written to the call log. An earlier version derived the reason
    by searching the message for words like "expired", which meant a reworded
    message silently changed a recorded outcome - a call log that renames its own
    failures is not evidence of anything.
    """

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


def refusal(reason: str, message: str) -> Refusal:
    """Shorthand for :class:`Refusal`, so a raise site reads as one call."""
    return Refusal(reason, message)
