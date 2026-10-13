"""The three error types this domain raises.

None of them is a builtin, and that is the point. The feature host attaches one
handler per error type, so a handler registered for ``ValueError`` or
``KeyError`` would intercept those exceptions across the whole product: one
feature's typo would turn every unrelated 500 into a 422. These three are
declared here, raised only from here, and mapped by exactly one feature.

Three types because three different things are wrong:

``ContractError``
    The request is malformed, with the offending fields named. 422.
``ContractNotFound``
    The quote or contract does not exist. 404.
``ContractConflict``
    The request is well-formed and the record's current state refuses it. 409,
    with a stable ``reason`` token from
    :mod:`dsr.contract_generation.vocabulary`.
"""

from __future__ import annotations

from typing import Any, Mapping


class ContractError(Exception):
    """A malformed request, with the offending fields named.

    ``errors`` maps a field name to the message for that field, so a form can put
    each message beside its input instead of printing one sentence about a row of
    inputs.
    """

    def __init__(self, message: str, errors: Mapping[str, str] | None = None) -> None:
        super().__init__(message)
        self.errors: dict[str, str] = dict(errors or {})

    def as_dict(self) -> dict[str, Any]:
        return {"detail": str(self), "errors": self.errors}


class ContractNotFound(Exception):
    """No such quote or contract."""

    def __init__(self, message: str = "No such record.") -> None:
        super().__init__(message)


class ContractConflict(Exception):
    """The record's state refuses this change, with a stable reason token."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason

    def as_dict(self) -> dict[str, Any]:
        return {"reason": self.reason, "detail": str(self)}
