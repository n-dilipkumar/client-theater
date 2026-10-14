"""WF-136: the errors this workflow refuses with.

One base type and one subclass per refusal a caller can act on, so the page can
say which of two different problems it is showing. None of them is a builtin,
deliberately: a handler registered for ``ValueError`` or ``PermissionError``
would intercept those exceptions across the whole product.

The same rule applies to the reason tokens. Each refusal that a page has to
branch on carries a stable token in :attr:`reason`, so the UI does not parse an
English sentence and hope the server did not reword it.

Each subclass also carries the status it answers with, in :attr:`http_status`.
The feature module has one handler for the whole hierarchy and reads the status
off the exception, so a new refusal cannot ship without someone deciding whether
it is a malformed request, a missing row, or a conflict with stored state. That
decision used to be taken silently in the handler, where it was invisible.
"""

from __future__ import annotations


class InRoomBookingError(Exception):
    """Base type for every refusal this workflow makes."""

    #: A stable token a client branches on. Empty where nothing branches.
    reason = "in_room_booking_error"

    #: The status this refusal answers with. 409 by default: nothing here is a
    #: builtin, and the common case is that the request is well formed and the
    #: proposal's state makes it wrong.
    http_status = 409
    #: A short machine code the client also receives in the body.
    code = "in_room_booking_error"

    def __init__(
        self,
        message: str,
        *,
        reason: str | None = None,
        http_status: int | None = None,
    ) -> None:
        super().__init__(message)
        if reason is not None:
            self.reason = reason
        if http_status is not None:
            self.http_status = http_status


class ProposalInvalid(InRoomBookingError):
    """The proposal body cannot be read as a next step.

    A field-keyed map rides on the exception so a form can place each message
    beside the input that caused it, the same shape WF-124's validator uses.
    400, because the request itself is malformed.
    """

    reason = "proposal_invalid"
    http_status = 400
    code = "proposal_invalid"

    def __init__(self, message: str, errors: dict[str, str] | None = None) -> None:
        super().__init__(message)
        self.errors: dict[str, str] = dict(errors or {})


class ProposalNotFound(InRoomBookingError, LookupError):
    """No proposal carries that id. 404, worded so it does not confirm more."""

    reason = "proposal_not_found"
    http_status = 404
    code = "proposal_not_found"


class MeetingNotFound(InRoomBookingError, LookupError):
    """No meeting carries that id. 404."""

    reason = "meeting_not_found"
    http_status = 404
    code = "meeting_not_found"


class ProposalExpired(InRoomBookingError):
    """The proposal lapsed before the buyer answered it.

    409 rather than 400: nothing about the request is malformed. The proposal is
    in a state that makes the answer wrong, and a client that retries the same
    call will get the same answer.
    """

    reason = "proposal_expired"
    http_status = 409
    code = "proposal_expired"


class ProposalAnswered(InRoomBookingError):
    """The proposal was already accepted or declined.

    A second accept is refused rather than ignored. Booking a meeting twice
    because a buyer double-clicked is the exact failure this refusal exists for,
    and silence would leave the second booking looking like a success.
    """

    reason = "proposal_answered"
    http_status = 409
    code = "proposal_answered"


class RoomNotBookable(InRoomBookingError):
    """The room has no embed, or its token is not live.

    The refusal names which of the two it is through ``reason``: a room with no
    embed needs WF-058's install step, and a room whose token lapsed needs a
    grant. The two are fixed by different people, so they are different reasons.
    """

    reason = "room_not_bookable"
    http_status = 409
    code = "room_not_bookable"


class SlotNotOffered(InRoomBookingError):
    """The slot the buyer picked is not on the room's grid any more.

    Raised rather than substituting the nearest slot: a slot that some other
    buyer holds in the meantime is not the slot this buyer chose, and booking
    them into a different time is worse than a refusal they can act on.
    """

    reason = "slot_not_offered"
    http_status = 409
    code = "slot_not_offered"


class AttendeeRequired(InRoomBookingError):
    """The click named no attendee, and WF-058 will not invite a placeholder.

    A calendar invite with no attendee is unusable, so the buyer's name and email
    are required on the accept. 400, because the request is what is incomplete -
    the proposal and the room are both fine.
    """

    reason = "attendee_required"
    http_status = 400
    code = "attendee_required"


class BookingRefused(InRoomBookingError):
    """The room's booking rules refused the request for a reason of their own.

    A catch-all for the scheduling refusals that are neither "the room cannot
    book" nor "the slot is gone". 409, because the shape a caller has to act on
    is the same: the stored state does not admit this booking.
    """

    reason = "booking_refused"
    http_status = 409
    code = "booking_refused"


class NextStepTaskNotFound(InRoomBookingError, LookupError):
    """The proposal names a MAP task that does not exist on this room's plan."""

    reason = "next_step_task_not_found"
    http_status = 404
    code = "next_step_task_not_found"


class RecordingNotAttached(InRoomBookingError):
    """A recording could not be attached to a meeting.

    The recording is an artefact this workflow attaches rather than produces, so
    the only failure available is a malformed or absent URL. The meeting itself
    stands.
    """

    reason = "recording_not_attached"
    http_status = 400
    code = "recording_not_attached"


__all__ = [
    "AttendeeRequired",
    "BookingRefused",
    "InRoomBookingError",
    "MeetingNotFound",
    "NextStepTaskNotFound",
    "ProposalAnswered",
    "ProposalExpired",
    "ProposalInvalid",
    "ProposalNotFound",
    "RecordingNotAttached",
    "RoomNotBookable",
    "SlotNotOffered",
]
