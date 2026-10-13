"""The three error types this domain raises.

None of them is a builtin, and that is deliberate. The feature host attaches one
handler per error type, so a handler registered for ``ValueError`` or
``LookupError`` would intercept those exceptions across the whole product, and
two features cannot map the same type. WF-086 already owns ``QuoteError``,
``QuoteNotFound`` and ``QuoteConflict``, so this workflow declares its own three
rather than sharing them and colliding on the handler.

Three types because three different things are wrong:

``RevisionError``
    The request is malformed. 422, with a field-keyed map so each message lands
    beside the input that caused it.
``RevisionNotFound``
    The quote does not exist, or the caller may not know that it does. 404.
``RevisionConflict``
    The request is well-formed and the quote's current state refuses it. 409,
    because retrying the same call returns the same answer.
"""

from __future__ import annotations

from typing import Any, Mapping


class RevisionError(Exception):
    """A malformed request, with the offending fields named."""

    def __init__(self, message: str, errors: Mapping[str, str] | None = None) -> None:
        super().__init__(message)
        self.errors: dict[str, str] = dict(errors or {})

    def as_dict(self) -> dict[str, Any]:
        return {"detail": str(self), "errors": self.errors}


class RevisionNotFound(Exception):
    """No such quote."""

    def __init__(self, message: str = "No such quote record.") -> None:
        super().__init__(message)


class RevisionConflict(Exception):
    """The quote's state refuses this change.

    409 rather than 400 because nothing about the request is malformed.
    ``reason`` is a stable token from
    :mod:`dsr.quote_revision.vocabulary`, never an English sentence, so the page
    branches on the token and a server reword does not stale the interface.
    """

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason

    def as_dict(self) -> dict[str, Any]:
        return {"reason": self.reason, "detail": str(self)}
