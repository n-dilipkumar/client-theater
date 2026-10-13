"""The errors WF-110 raises, declared in one place.

Four types, because four different things can be wrong and a caller acts
differently on each. The feature module registers one handler for the base type;
Starlette resolves a handler by walking the raised class's method-resolution
order, so every subclass is covered by that one registration and stays
distinguishable by its ``status``.

``TicketRefused``
    A value or a step this workflow will not accept: a ticket with no ticket
    type, an unknown team, an unknown ticket type or state, a trigger with no
    name, a tag the workspace does not have. **400**, with a field-keyed map so
    each message lands next to its input.
``TicketConflict``
    A well-formed request that conflicts with state that already exists: a
    workspace tag name already in use, a ticket already resolved, a conversation
    that is already linked to this ticket. **409**, because the request was fine
    and the state is what refuses it.
``TicketNotFound``
    A ticket, trigger, tag or SLA policy that does not exist. **404**. Never a
    500, because a feature must never turn a missing row into a server fault.

Why a skipped trigger or a skipped action is not one of these
-------------------------------------------------------------

It is the point of the workflow. A ticket of a type no trigger watches, a
tracker-created ticket, and an action the converted-conversation gate blocks are
all **outcomes**, not errors: the evaluation records them rather than raising.
Turning a skip into an HTTP error would make a correctly-behaving automation
look broken to whoever reads the logs.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from dsr.conversation_tickets import vocabulary as vocab


class TicketError(ValueError):
    """A ticket request cannot be honoured as written.

    The status rides on the exception rather than being chosen at every raise
    site, so "this is a conflict, not a typo" is decided once.
    """

    status = 400
    code = "ticket_error"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code


class TicketRefused(TicketError):
    """A value or a step this workflow will not accept. **400**."""

    status = 400
    code = "invalid_ticket_request"

    def __init__(
        self, detail: str, errors: Mapping[str, str] | None = None, *, code: str | None = None
    ) -> None:
        super().__init__(detail, code=code)
        self.detail = detail
        self.errors: dict[str, str] = dict(errors or {})

    def to_dict(self) -> dict[str, Any]:
        return {"error": self.code, "detail": self.detail, "errors": self.errors}


class TicketConflict(TicketError):
    """A well-formed request that conflicts with current state. **409**."""

    status = 409
    code = "ticket_conflict"

    def __init__(self, detail: str, *, code: str | None = None, **extra: Any) -> None:
        super().__init__(detail, code=code)
        self.detail = detail
        self.extra = extra

    def to_dict(self) -> dict[str, Any]:
        body: dict[str, Any] = {"error": self.code, "detail": self.detail}
        body.update(self.extra)
        return body


class TicketNotFound(TicketError):
    """The named ticket, trigger, tag or SLA policy does not exist. **404**."""

    status = 404
    code = "ticket_not_found"

    def __init__(self, detail: str, *, kind: str = "ticket") -> None:
        super().__init__(detail)
        self.detail = detail
        self.kind = kind

    def to_dict(self) -> dict[str, Any]:
        return {"error": f"{self.kind}_not_found", "detail": self.detail}


class ConversationNotFound(TicketNotFound):
    """The conversation a caller asked to link does not exist. **404**.

    Its own type because it is the one not-found that names a record another
    workflow owns, so a reader can tell "your ticket id is wrong" from "your
    conversation id is wrong".
    """

    def __init__(self, detail: str) -> None:
        super().__init__(detail, kind="conversation")


class TagNotInWorkspace(TicketRefused):
    """A tag was named that the workspace does not have. **400**."""

    code = "tag_not_in_workspace"

    def to_dict(self) -> dict[str, Any]:
        body = super().to_dict()
        body["error"] = self.code
        return body


class TeamUnknown(TicketRefused):
    """A team outside the known set. **400**."""

    code = "team_unknown"

    def to_dict(self) -> dict[str, Any]:
        body = super().to_dict()
        body["error"] = self.code
        return body


def error_codes() -> dict[str, dict[str, Any]]:
    """Every published refusal code, with its status and its sentence."""

    return {
        name: {"status": status, "detail": detail}
        for name, (status, detail) in sorted(vocab.ERROR_CODES.items())
    }


ERROR_TYPES: tuple[type[TicketError], ...] = (
    TicketError,
    TicketRefused,
    TicketConflict,
    TicketNotFound,
    ConversationNotFound,
    TagNotInWorkspace,
    TeamUnknown,
)
