"""The three error types this domain raises, and the refusal helper.

None of the three is a builtin, and that is the point. The feature host attaches
a handler per error type, so a handler registered for ``ValueError`` or
``PermissionError`` would intercept those exceptions across the whole product:
one feature's typo would turn every unrelated 500 into a 422. These three are
declared here, raised only from here, and mapped by exactly one feature.

Three types because three different things are wrong:

``OrderFormError``
    The request is malformed. 422, with a field-keyed map so each message lands
    beside the input that caused it.
``OrderFormNotFound``
    The record does not exist, or the caller may not know that it does. 404.
``OrderFormConflict``
    The request is well-formed and the record's current state refuses it. 409.
"""

from __future__ import annotations

from typing import Any, Mapping

from dsr.inroom_ordering.vocabulary import ERROR_CODES


class OrderFormError(Exception):
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


class OrderFormNotFound(Exception):
    """No such order form, line, revision or quote.

    Carries nothing but a message. A 403 would confirm that a record exists
    behind an id, and a caller must not be able to probe for one.
    """

    def __init__(self, message: str = "No such order form.") -> None:
        super().__init__(message)


class OrderFormConflict(Exception):
    """The record's state refuses this change.

    409 rather than 400 because nothing about the request is malformed.
    Retrying the same call returns the same answer, which is what makes it a
    conflict rather than a mistake.

    ``reason`` is a stable token from :mod:`dsr.inroom_ordering.vocabulary`,
    never an English sentence. The page branches on the token and shows its own
    words, so a server reword does not turn into a stale sentence in the
    interface.
    """

    def __init__(self, reason: str, message: str | None = None) -> None:
        if message is None:
            message = ERROR_CODES.get(reason, (409, reason))[1]
        super().__init__(message)
        self.reason = reason

    def as_dict(self) -> dict[str, Any]:
        return {"reason": self.reason, "detail": str(self)}


def refuse(reason: str, **fields: str) -> OrderFormError:
    """An :class:`OrderFormError` for a named refusal.

    The message and the field-keyed map come from :data:`ERROR_CODES`, so a
    refusal a route can produce and the sentence the page shows beside an input
    are one definition. ``fields`` names the inputs the refusal is about.
    """
    _status, message = ERROR_CODES[reason]
    return OrderFormError(message, fields or {"form": message})
