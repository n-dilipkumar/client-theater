"""The three error types this domain raises.

None of them is a builtin, and that is the point. The feature host attaches a
handler per error type, so a handler registered for ``ValueError`` or
``PermissionError`` would intercept those exceptions across the whole product:
one feature's typo would turn every unrelated 500 into a 422. These three are
declared here, raised only from here, and mapped by exactly one feature.

Three types because three different things are wrong:

``QuoteError``
    The request is malformed. 422, with a field-keyed map so each message lands
    beside the input that caused it.
``QuoteNotFound``
    The record does not exist, or the caller may not know that it does. 404.
``QuoteConflict``
    The request is well-formed and the quote's current state refuses it. 409.
"""

from __future__ import annotations

from typing import Any, Mapping


class QuoteError(Exception):
    """A malformed request, with the offending fields named.

    ``errors`` maps a field name to the message for that field, so a form can
    put each message next to its input instead of printing one sentence about a
    row of inputs.
    """

    def __init__(self, message: str, errors: Mapping[str, str] | None = None) -> None:
        super().__init__(message)
        self.errors: dict[str, str] = dict(errors or {})

    def as_dict(self) -> dict[str, Any]:
        return {"detail": str(self), "errors": self.errors}


class QuoteNotFound(Exception):
    """No such quote, line item, template or deal.

    Carries nothing but a message. A 403 would confirm that a record exists
    behind an id, and a caller must not be able to probe for one.
    """

    def __init__(self, message: str = "No such quote record.") -> None:
        super().__init__(message)


class QuoteConflict(Exception):
    """The quote's state refuses this change.

    409 rather than 400 because nothing about the request is malformed.
    Retrying the same call returns the same answer, which is what makes it a
    conflict rather than a mistake.

    ``reason`` is a stable token from
    :mod:`dsr.quote_authoring.vocabulary`, never an English sentence. The page
    branches on the token and shows its own words, so a server reword does not
    turn into a stale sentence in the interface.
    """

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason

    def as_dict(self) -> dict[str, Any]:
        return {"reason": self.reason, "detail": str(self)}
