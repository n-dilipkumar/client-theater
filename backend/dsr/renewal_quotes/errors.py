"""The errors this workflow raises, declared in one place.

Three types, because three different things can be wrong and a caller acts differently on
each. All three are declared here and raised by nothing else in the product, which is what
makes it safe to map them in the HTTP layer: FastAPI accepts an exception handler on the
app object only, the host attaches the ones a feature exports, and the host refuses a second
feature claiming a type this one already claimed.

``RenewalRefusal``
    A value this workflow will not accept. 400. A term length of zero, a proration flag that
    is not a boolean, a deal pipeline named without a stage, a renewal workflow pointed at a
    contract that does not exist.
``RenewalNotFound``
    A contract, template, quote, deal or pipeline that does not exist. 404. Never a 500,
    because a feature must not turn a missing row into a server fault.
``RenewalConflict``
    A well-formed request that conflicts with the state of a record. 409. Renewing a
    contract that is not on an expiring term, accepting a quote twice, or accepting a quote
    whose contract already has a finalised renewal.

The status codes follow the rest of this product rather than the vendor documentation. The
research names no status code for this workflow, so the distinction is recorded rather than
invented, and the reasoning is in the feature module.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


class RenewalRefusal(ValueError):
    """A value this workflow will not accept, with a field-keyed map of reasons."""

    def __init__(self, detail: str, errors: Mapping[str, str] | None = None) -> None:
        super().__init__(detail)
        self.detail = detail
        self.errors: dict[str, str] = dict(errors or {})

    def to_dict(self) -> dict[str, Any]:
        return {"error": "invalid_renewal_request", "detail": self.detail, "errors": self.errors}


class RenewalNotFound(LookupError):
    """A contract, template, quote, deal or pipeline that does not exist."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail

    def to_dict(self) -> dict[str, Any]:
        return {"error": "not_found", "detail": self.detail}


class RenewalConflict(Exception):
    """A well-formed request that conflicts with the state of the record."""

    def __init__(self, detail: str, *, remedy: str, evidence: str = "") -> None:
        super().__init__(detail)
        self.detail = detail
        self.remedy = remedy
        self.evidence = evidence

    def to_dict(self) -> dict[str, Any]:
        """The body names the way out, because a bare 409 is a support ticket."""

        body: dict[str, Any] = {
            "error": "renewal_conflict",
            "detail": self.detail,
            "remedy": self.remedy,
        }
        if self.evidence:
            body["evidence"] = self.evidence
        return body


ERROR_TYPES = (RenewalRefusal, RenewalNotFound, RenewalConflict)
