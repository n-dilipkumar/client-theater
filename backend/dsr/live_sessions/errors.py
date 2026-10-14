"""One error per distinct answer, so the HTTP layer maps each to one status.

Three answers exist for this workflow:

* the payload is wrong                      -> :class:`SessionValidationError`   422
* the session, participant or question is absent -> :class:`SessionNotFound`     404
* the action contradicts state              -> :class:`SessionConflict`          409

Every error carries a stable ``code``. A page branches on the code and never on
the message text.
"""

from __future__ import annotations

from typing import Any, Mapping


class SessionError(Exception):
    """Base type for every refusal this workflow makes."""

    code = "live_session_error"

    def __init__(self, detail: str, *, code: str | None = None) -> None:
        super().__init__(detail)
        self.detail = detail
        if code:
            self.code = code

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "detail": self.detail}


class SessionValidationError(SessionError):
    """A field is missing or outside its closed vocabulary."""

    code = "live_session_invalid"

    def __init__(self, errors: Mapping[str, str], *, detail: str | None = None) -> None:
        self.errors: dict[str, str] = dict(errors)
        message = detail or "; ".join(f"{field}: {why}" for field, why in self.errors.items())
        super().__init__(message, code=self.code)

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "detail": self.detail, "errors": dict(self.errors)}


class SessionNotFound(SessionError):
    """No live record resolves to the given id."""

    code = "live_session_not_found"

    def __init__(self, record_id: str, *, collection: str = "session") -> None:
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


class SessionConflict(SessionError):
    """The action contradicts the current state of the session or a participant."""

    code = "live_session_conflict"

    def __init__(self, reason: str, detail: str, *, extra: Mapping[str, Any] | None = None) -> None:
        super().__init__(detail, code=self.code)
        self.reason = reason
        self.extra: dict[str, Any] = dict(extra or {})

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "reason": self.reason, "detail": self.detail, **self.extra}


#: The conflict tokens the page branches on. They mirror the ``REASON_*`` words in
#: :mod:`dsr.live_sessions.vocabulary`, re-exported here so the HTTP layer maps a
#: token and not a prose string.
REASON_NOT_PRESENTER = "not_the_presenter"
REASON_SKIPPED_REVEAL = "reveal_out_of_order"
REASON_OUT_OF_RANGE = "step_out_of_range"
REASON_SESSION_NOT_LIVE = "session_not_live"
REASON_ALREADY_STARTED = "session_already_started"
REASON_ALREADY_ENDED = "session_already_ended"
REASON_FOLLOWER_CANNOT_CONTROL = "follower_cannot_control"
REASON_PARTICIPANT_ALREADY_LEFT = "participant_already_left"
REASON_QUESTION_ALREADY_ANSWERED = "question_already_answered"
REASON_SECOND_PRESENTER = "second_presenter_refused"

__all__ = [
    "REASON_ALREADY_ENDED",
    "REASON_ALREADY_STARTED",
    "REASON_FOLLOWER_CANNOT_CONTROL",
    "REASON_NOT_PRESENTER",
    "REASON_OUT_OF_RANGE",
    "REASON_PARTICIPANT_ALREADY_LEFT",
    "REASON_QUESTION_ALREADY_ANSWERED",
    "REASON_SECOND_PRESENTER",
    "REASON_SESSION_NOT_LIVE",
    "REASON_SKIPPED_REVEAL",
    "SessionConflict",
    "SessionError",
    "SessionNotFound",
    "SessionValidationError",
]
