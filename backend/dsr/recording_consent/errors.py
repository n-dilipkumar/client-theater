"""One error hierarchy, so the feature module registers one handler.

Every type here is raised by :mod:`dsr.recording_consent` and by nothing else in
the product. That is what makes it safe to map them in the feature module: the
feature host refuses a second feature registering a handler for the same type,
and a handler for ``ValueError`` or ``PermissionError`` would intercept those
exceptions across the whole application.
"""

from __future__ import annotations


class ConsentError(Exception):
    """Base for every refusal this workflow raises.

    Carries ``errors`` for the subclasses that can say more than one thing at
    once, so a form can put each message beside the input that caused it rather
    than next to the submit button.
    """

    #: Stable machine token. The frontend branches on this, never on ``detail``.
    code = "consent_error"

    def __init__(self, detail: str, errors: dict[str, str] | None = None) -> None:
        super().__init__(detail)
        self.detail = detail
        self.errors: dict[str, str] = dict(errors or {})

    def __str__(self) -> str:
        return self.detail


class ProfileInvalid(ConsentError):
    """The consent profile payload is not one this workflow accepts."""

    code = "consent_profile_invalid"


class ProfileNotFound(ConsentError):
    """No consent profile by that id."""

    code = "consent_profile_not_found"


class BookingNotFound(ConsentError):
    """No consent record for that booking.

    Distinct from "the booking has no link yet", which is
    :class:`LinkSuperseded`. One is a booking nobody opened and the other is a
    booking whose link has been replaced, and the two need different status codes:
    a caller that asks about a booking which does not exist gets a 404, while a
    caller holding a stale link gets a 409 it can act on by issuing a new one.
    """

    code = "consent_record_not_found"


class ConsentPageDisabled(ConsentError):
    """The consent page is off, so no consent-enabled link can be issued.

    This is the researched 409: "Conflict, e.g. consent page is not enabled in
    your company". It is raised before any outbound request would be made,
    because this product reads the switch and does not have to spend a call to
    learn it.
    """

    code = "consent_page_disabled"


class OrganizerUnmapped(ConsentError):
    """The organiser email has no user behind it.

    This is the researched 404: "No Gong user found corresponding to the
    provided organizer email". It matters more than a plain 404 usually does,
    because the research resolves the consent profile *by user*, so an unmapped
    organiser has no profile and therefore no consent rule to apply.
    """

    code = "organizer_unmapped"


class JoinWithoutConsentRefused(ConsentError):
    """A participant tried to join without consent where the profile forbids it.

    Raised only when ``allow_join_without_consent`` is off. With it on, the same
    event is a valid transition that cancels the recording instead.
    """

    code = "join_without_consent_refused"


class LinkSuperseded(ConsentError):
    """The consent link has been replaced and no longer joins the call."""

    code = "link_superseded"


class IllegalTransition(ConsentError):
    """The requested step is not a step this machine has.

    The message names both the state it was in and the step that was asked for,
    because a state machine whose rejection does not say where it was is a state
    machine nobody can debug from a log.
    """

    code = "illegal_transition"

    def __init__(self, current: str, step: str, allowed: tuple[str, ...]) -> None:
        super().__init__(
            f"cannot {step} from state {current!r}; allowed here: {', '.join(allowed) or 'nothing'}",
            errors={"state": f"current state is {current}", "step": f"{step} is not allowed here"},
        )
        self.current = current
        self.step = step
        self.allowed = tuple(allowed)
