"""The errors this workflow raises, declared in one place.

Three types, because three different things can be wrong and a caller acts
differently on each. The feature module registers one handler for the base type;
Starlette resolves a handler by walking the raised class's MRO, so the four
subclasses below are all covered by that one registration and stay
distinguishable by their ``status``.

``SeriesRefused``
    A value or a step this workflow will not accept: a series with no name, no
    starting rule block, or no path; a path that names a tag the workspace does
    not have; a goal window that is not a positive number of days. **400**, with
    a field-keyed map so each message lands next to its input.
``SeriesConflict``
    A well-formed request that conflicts with state that already exists: a
    workspace tag with a name that is already in use, a series that is not live
    being polled, an exit rule evaluated against a contact that does not match
    it. **409**, because the request was fine and the state is what refuses it.
``SeriesNotFound``
    A series, tag, enrollment or tag application that does not exist. **404**.
    Never a 500, because a feature must never turn a missing row into a server
    fault.

Why a skipped entry is not one of these
---------------------------------------

It is the point of the workflow. A contact that does not match the starting rule
block, a contact that has already exited and cannot re-enter, and a contact that
is already active are all **outcomes**, not errors: the poll reports them rather
than raising. :func:`dsr.automation_engagement.series.rules.entry_decision`
returns a decision rather than raising, and the engine records it. Turning a skip
into an HTTP error would make a correctly-behaving automation look broken to
whoever reads the logs.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


class SeriesError(ValueError):
    """A series request cannot be honoured as written.

    The status rides on the exception rather than being chosen per raise site, so
    "this is a conflict, not a typo" is decided once where the distinction is
    drawn rather than at every call site.
    """

    status = 400
    code = "series_error"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code


class SeriesRefused(SeriesError):
    """A value or a step this workflow will not accept."""

    status = 400
    code = "invalid_series_request"

    def __init__(
        self, detail: str, errors: Mapping[str, str] | None = None, *, code: str | None = None
    ) -> None:
        super().__init__(detail, code=code)
        self.detail = detail
        self.errors: dict[str, str] = dict(errors or {})

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": self.code,
            "detail": self.detail,
            "errors": self.errors,
        }


class SeriesConflict(SeriesError):
    """A well-formed request that conflicts with current state."""

    status = 409
    code = "series_conflict"

    def __init__(self, detail: str, *, code: str | None = None, **extra: Any) -> None:
        super().__init__(detail, code=code)
        self.detail = detail
        self.extra = extra

    def to_dict(self) -> dict[str, Any]:
        body: dict[str, Any] = {"error": self.code, "detail": self.detail}
        body.update(self.extra)
        return body


class SeriesNotFound(SeriesError):
    """The named series, tag, enrollment or tag application does not exist."""

    status = 404
    code = "series_not_found"

    def __init__(self, detail: str, *, kind: str = "series") -> None:
        super().__init__(detail)
        self.detail = detail
        self.kind = kind

    def to_dict(self) -> dict[str, Any]:
        return {"error": f"{self.kind}_not_found", "detail": self.detail}


class TagNotInWorkspace(SeriesRefused):
    """A tag block named a tag the workspace does not have.

    The research is explicit: "You won't be able to create a new tag here, but
    can use any existing tag from the workspace." So a tag block selects from the
    workspace vocabulary and cannot introduce a new name; that refusal is a 400
    with the field named, not a silent creation.
    """

    code = "tag_not_in_workspace"

    def to_dict(self) -> dict[str, Any]:
        body = super().to_dict()
        body["error"] = self.code
        return body


ERROR_TYPES = (SeriesError, SeriesRefused, SeriesConflict, SeriesNotFound, TagNotInWorkspace)
