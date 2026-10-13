"""The errors this workflow raises, declared in one place.

Every refusal is a :class:`SeriesRefused` and each carries a published ``code`` and an HTTP
``status``. That is what lets one handler map the whole hierarchy: a bad filter value (400)
and a run that is already waiting (409) are both this package's errors, and only one of them
is about a value the caller sent.

``SeriesNotFound`` and ``RunNotFound`` are separate types so the host can answer 404 for a row
that does not exist rather than 400 for a request that is malformed. All three are claimed
only by this feature, which is what makes mapping them safe: FastAPI accepts a handler on the
app object only, the host attaches the ones a feature exports, and the host refuses a second
feature registering a handler for a type this one already claimed.
"""

from __future__ import annotations

from typing import Any, Mapping

from dsr.nurture_series import vocabulary as vocab


class SeriesRefused(Exception):
    """A value or a step this workflow will not accept.

    Carries the published ``code`` and ``status`` so one handler answers every refusal in the
    hierarchy, and a field-keyed ``errors`` map so each message lands next to its input.
    """

    def __init__(
        self,
        detail: str,
        *,
        code: str = "invalid_series_request",
        status: int = 400,
        errors: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(detail)
        self.detail = detail
        self.code = code
        self.status = status
        self.errors: dict[str, str] = dict(errors or {})

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": self.code,
            "detail": self.detail,
            "status": self.status,
            "errors": self.errors,
            "sourced": vocab.series_failures().get(self.code, {}).get("sourced", "n/a"),
        }


class SeriesNotFound(SeriesRefused):
    """No such series."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail, code="series_not_found", status=404)


class RunNotFound(SeriesRefused):
    """No such run."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail, code="run_not_found", status=404)


#: Every type this feature maps, in one tuple so a test can assert on the set.
ERROR_TYPES = (SeriesRefused, SeriesNotFound, RunNotFound)
