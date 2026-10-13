"""One error per distinct answer, so the HTTP layer maps each to one status.

Three answers exist for this workflow:

* the payload is wrong              -> :class:`EnrollValidationError`  422
* the schedule, enrollment or run is absent -> :class:`EnrollNotFound`  404
* the action contradicts state      -> :class:`EnrollConflict`        409

Every error carries a stable ``code``. A page branches on the code and never on
the message text.
"""

from __future__ import annotations

from typing import Any, Mapping


class ScheduleEnrollmentError(Exception):
    """Base type for every refusal this workflow makes."""

    code = "schedule_enrollment_error"

    def __init__(self, detail: str, *, code: str | None = None) -> None:
        super().__init__(detail)
        self.detail = detail
        if code:
            self.code = code

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "detail": self.detail}


class EnrollValidationError(ScheduleEnrollmentError):
    """A field is missing or outside its closed vocabulary."""

    code = "enrollment_invalid"

    def __init__(self, errors: Mapping[str, str], *, detail: str | None = None) -> None:
        self.errors: dict[str, str] = dict(errors)
        message = detail or "; ".join(f"{field}: {why}" for field, why in self.errors.items())
        super().__init__(message, code=self.code)

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "detail": self.detail, "errors": dict(self.errors)}


class EnrollNotFound(ScheduleEnrollmentError):
    """No live record resolves to the given id."""

    code = "enrollment_not_found"

    def __init__(self, record_id: str, *, collection: str = "schedule") -> None:
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


class EnrollConflict(ScheduleEnrollmentError):
    """The action contradicts the current state of the schedule."""

    code = "enrollment_conflict"

    def __init__(self, reason: str, detail: str, *, extra: Mapping[str, Any] | None = None) -> None:
        super().__init__(detail, code=self.code)
        self.reason = reason
        self.extra: dict[str, Any] = dict(extra or {})

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "reason": self.reason, "detail": self.detail, **self.extra}


__all__ = [
    "EnrollConflict",
    "EnrollNotFound",
    "EnrollValidationError",
    "ScheduleEnrollmentError",
]
