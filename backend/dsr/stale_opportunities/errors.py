"""One error per distinct answer, so the HTTP layer maps each to one status.

Three answers exist for this workflow:

* the payload is wrong            -> :class:`SweepValidationError`  422
* the sweep or opportunity is absent -> :class:`SweepNotFound`      404
* the action contradicts state    -> :class:`SweepConflict`         409
* everything else                 -> :class:`StaleOpportunityError` 422

Every error carries a stable ``code``. A page branches on the code and never on
the message text.
"""

from __future__ import annotations

from typing import Any, Mapping


class StaleOpportunityError(Exception):
    """Base type for every refusal this workflow makes."""

    code = "stale_opportunity_error"

    def __init__(self, detail: str, *, code: str | None = None) -> None:
        super().__init__(detail)
        self.detail = detail
        if code:
            self.code = code

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "detail": self.detail}


class SweepValidationError(StaleOpportunityError):
    """A field is missing or outside its closed vocabulary."""

    code = "sweep_invalid"

    def __init__(self, errors: Mapping[str, str], *, detail: str | None = None) -> None:
        self.errors: dict[str, str] = dict(errors)
        message = detail or "; ".join(f"{field}: {why}" for field, why in self.errors.items())
        super().__init__(message, code=self.code)

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "detail": self.detail, "errors": dict(self.errors)}


class SweepNotFound(StaleOpportunityError):
    """No live sweep or opportunity resolves to the given id."""

    code = "sweep_not_found"

    def __init__(self, record_id: str, *, collection: str = "sweep") -> None:
        super().__init__(f"no {collection} record with id {record_id!r}", code=self.code)
        self.record_id = record_id
        self.collection = collection

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "detail": self.detail,
            "record_id": self.record_id,
            "collection": self.collection,
        }


class SweepConflict(StaleOpportunityError):
    """The action contradicts the current state of the sweep."""

    code = "sweep_conflict"

    def __init__(self, reason: str, detail: str, *, extra: Mapping[str, Any] | None = None) -> None:
        super().__init__(detail, code=self.code)
        self.reason = reason
        self.extra: dict[str, Any] = dict(extra or {})

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "reason": self.reason, "detail": self.detail, **self.extra}


__all__ = [
    "StaleOpportunityError",
    "SweepConflict",
    "SweepNotFound",
    "SweepValidationError",
]
