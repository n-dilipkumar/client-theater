"""WF-132: one error per distinct refusal, so the HTTP layer maps each to a status.

Every type here is declared by this package and raised by nothing else in the
product. A feature may register an exception handler only for a type it raises
itself: a handler for ``ValueError`` or ``PermissionError`` would intercept those
exceptions across every other feature, and the host refuses the second feature
that tries to claim one. So these are plain ``Exception`` subclasses and none of
them is a builtin.

The status each one carries is the status the feature module answers with. It
lives on the class rather than in the route table so the two cannot drift: there
is exactly one place that says a malformed comment is 422 and a refused action is
403.
"""

from __future__ import annotations

from typing import Any, Mapping


class InRoomError(Exception):
    """Base class for every refusal this workflow makes.

    422 for the base, because the only thing that reaches it unclassified is a
    request whose values the rules reject. A subclass that means something more
    specific overrides the code.
    """

    status_code = 422

    def __init__(self, message: str, errors: Mapping[str, str] | None = None) -> None:
        super().__init__(message)
        #: Field-keyed detail, rendered by the HTTP layer as ``errors``. A person
        #: filling in a comment box needs the message next to the box.
        self.errors: dict[str, Any] = dict(errors or {})


class InRoomValidation(InRoomError):
    """The request is well-formed but its values break a rule.

    422 rather than 400: the body parsed, and what is wrong is a value, which is
    exactly what 422 is for. Examples: an empty comment, an unknown anchor kind,
    a mention with no text.
    """

    status_code = 422


class InRoomDenied(InRoomError):
    """The actor's role does not permit the action.

    403. The rule is the roles vocabulary this workflow reuses from WF-004: every
    room role may comment and reply, and only a role that manages content may
    edit a section. A person with no role in the room is denied rather than
    treated as a viewer.
    """

    status_code = 403


class InRoomNotFound(InRoomError):
    """No such thread, comment, conversion or room, as far as this workflow knows.

    Its own type rather than the store's ``RecordNotFound``, for the same reason
    as above: this workflow may only map the error types it declares.
    """

    status_code = 404


class InRoomConflict(InRoomError):
    """The action contradicts the state of the thing it names.

    409, not 400: nothing about the request is malformed. The object is in a
    state that makes the change wrong, and retrying the same call would get the
    same answer. ``reason`` is a stable machine token the UI branches on.
    """

    status_code = 409

    #: A comment arrived for a thread that is already resolved.
    REASON_THREAD_RESOLVED = "thread_resolved"

    #: A resolve or reopen arrived for a thread already in that state.
    REASON_THREAD_ALREADY_RESOLVED = "thread_already_resolved"
    REASON_THREAD_NOT_RESOLVED = "thread_not_resolved"

    #: A thread that is already linked to a MAP task was converted again.
    REASON_ALREADY_CONVERTED = "thread_already_converted"

    _MESSAGES = {
        REASON_THREAD_RESOLVED: "That thread is resolved. Reopen it before you comment.",
        REASON_THREAD_ALREADY_RESOLVED: "That thread is already resolved.",
        REASON_THREAD_NOT_RESOLVED: "That thread is not resolved, so there is nothing to reopen.",
        REASON_ALREADY_CONVERTED: "That thread is already linked to a MAP task.",
    }

    def __init__(self, reason: str, message: str | None = None) -> None:
        super().__init__(message or self._MESSAGES.get(reason, "That change was refused."))
        self.reason = reason
