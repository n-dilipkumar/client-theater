"""One error per distinct answer, so the HTTP layer maps each to one status.

Four answers exist for this workflow:

* a payload is wrong                       -> :class:`FormValidationError`   422
* a form, room, deal or submission is absent -> :class:`CaptureNotFound`    404
* the action contradicts state             -> :class:`CaptureConflict`      409
* everything else                          -> :class:`QualificationError`   422

Every error carries a stable ``code``. A page branches on the code and never on
the message text.
"""

from __future__ import annotations

from typing import Any, Mapping


class QualificationError(Exception):
    """Base type for every refusal this workflow makes."""

    code = "qualification_error"
    status_code = 422

    def __init__(self, detail: str, *, code: str | None = None) -> None:
        super().__init__(detail)
        self.detail = detail
        if code:
            self.code = code

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "detail": self.detail}


class FormValidationError(QualificationError):
    """A field is missing or outside its closed vocabulary."""

    code = "form_invalid"

    def __init__(self, errors: Mapping[str, str], *, detail: str | None = None) -> None:
        self.errors: dict[str, str] = dict(errors)
        message = detail or "; ".join(f"{field}: {why}" for field, why in self.errors.items())
        super().__init__(message, code=self.code)

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "detail": self.detail, "errors": dict(self.errors)}


class CaptureNotFound(QualificationError):
    """No live form, room, deal or submission resolves to the given id."""

    code = "qualification_not_found"
    status_code = 404

    def __init__(self, record_id: str, *, collection: str = "form") -> None:
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


class CaptureConflict(QualificationError):
    """The action contradicts the current state of the form or submission."""

    code = "qualification_conflict"
    status_code = 409

    def __init__(self, reason: str, detail: str, *, extra: Mapping[str, Any] | None = None) -> None:
        super().__init__(detail, code=self.code)
        self.reason = reason
        self.extra: dict[str, Any] = dict(extra or {})

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "reason": self.reason, "detail": self.detail, **self.extra}


__all__ = [
    "CaptureConflict",
    "CaptureNotFound",
    "FormValidationError",
    "QualificationError",
]
