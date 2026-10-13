"""The three error types this domain raises.

None of them is a builtin, and that is the point. The feature host attaches a
handler per error type, so a handler registered for ``ValueError`` would
intercept that exception across the whole product. These three are declared
here, raised only from here, and mapped by exactly one feature.

Three types because three different things are wrong:

``OrderError``
    The request is malformed. 422, with a field-keyed map so each message lands
    beside the input that caused it.
``OrderNotFound``
    The record does not exist. 404.
``OrderConflict``
    The request is well-formed and the order's current state refuses it. 409.
"""

from __future__ import annotations

from typing import Any, Mapping


class OrderError(Exception):
    """A malformed request, with the offending fields named."""

    def __init__(self, message: str, errors: Mapping[str, str] | None = None) -> None:
        super().__init__(message)
        self.errors: dict[str, str] = dict(errors or {})

    def as_dict(self) -> dict[str, Any]:
        return {"detail": str(self), "errors": self.errors}


class OrderNotFound(Exception):
    """No such quote, order or order line.

    Carries nothing but a message, so a caller cannot probe for a record id by
    telling a 403 from a 404.
    """

    def __init__(self, message: str = "No such order record.") -> None:
        super().__init__(message)


class OrderConflict(Exception):
    """The order's or the quote's state refuses this change.

    409 rather than 400 because nothing about the request is malformed.
    ``reason`` is a stable token from
    :mod:`dsr.order_conversion.vocabulary`, never an English sentence, so the
    page branches on the token and the server can be reworded freely.
    """

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason

    def as_dict(self) -> dict[str, Any]:
        return {"reason": self.reason, "detail": str(self)}
