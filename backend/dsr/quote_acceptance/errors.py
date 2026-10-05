"""The errors this workflow raises, declared in one place.

All four are declared here and raised by nothing else in the product. That is what makes it
safe for the HTTP layer to map them: FastAPI accepts an exception handler on the app object
only, the host attaches the ones a feature exports, and the host refuses a second feature
registering a handler for a type this one already claimed.

Four types, because four different things can be wrong and a caller acts differently on each:

``AcceptanceRefused``
    A value or a step this workflow will not accept: an unknown acceptance method, an
    e-signature quote with no buyer signer, an *In signing* attachment on a quote that is not
    an e-signature, a document over the 40 MB cap, a signer count the envelope cannot carry, a
    transition the status table does not authorise, and a reassignment the quote forbids.
    400.
``EnvelopeNotFound``
    A signing envelope, signer or signature event that does not exist. 404. Not a 500,
    because a feature must never turn a missing row into a server fault.
``SignerNotFound``
    A signer that does not exist, or one that belongs to a different envelope. 404. Kept
    apart from :class:`EnvelopeNotFound` because the remedy differs: a missing envelope is a
    wrong id, and a missing signer is a wrong party on a real envelope.
``QuotaRefused``
    A room that has reached a stated e-signature ceiling for the month. 409, because the
    request was well formed and conflicts with the room's state.

Why a refused *signature* is not one of these
---------------------------------------------

The research says "Signing attempt failures are logged automatically", and it names the
activity ``Signing attempt failed``. So a signature that cannot be recorded — an unverified
buyer, an expired window, a countersignature before the buyer's — is an **outcome**, not an
error: the attempt happened, it produced a definite result, and it wrote an activity row.
:func:`dsr.quote_acceptance.rules.verify_token` returns a pass or a fail rather than raising,
and the engine writes the fail as an event carrying
:data:`~dsr.quote_acceptance.vocabulary.ACTIVITY_ATTEMPT_FAILED`.

Turning it into an HTTP error would be wrong in a way a reviewer would see immediately: a
caller could not tell the difference between "this buyer has not verified yet" and "this
endpoint is broken", and a retry loop would hammer a route that is behaving correctly.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from dsr.quote_acceptance import vocabulary as vocab


class AcceptanceRefused(Exception):
    """A value or a step this workflow will not accept, with a field-keyed map of reasons."""

    def __init__(self, detail: str, errors: Mapping[str, str] | None = None) -> None:
        super().__init__(detail)
        self.detail = detail
        self.errors: dict[str, str] = dict(errors or {})

    def to_dict(self) -> dict[str, Any]:
        return {"error": "invalid_acceptance_request", "detail": self.detail, "errors": self.errors}


class EnvelopeNotFound(LookupError):
    """A signing envelope or signature event that does not exist."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail

    def to_dict(self) -> dict[str, Any]:
        return {"error": "not_found", "detail": self.detail}


class SignerNotFound(LookupError):
    """A signer that does not exist, or that is not a party on this envelope."""

    def __init__(self, detail: str, *, envelope_id: str | None = None) -> None:
        super().__init__(detail)
        self.detail = detail
        self.envelope_id = envelope_id

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": "signer_not_found",
            "detail": self.detail,
            "envelope_id": self.envelope_id,
        }


class QuotaRefused(Exception):
    """A room at its stated e-signature ceiling for this month."""

    def __init__(self, detail: str, *, used: int, limit: int, month: str) -> None:
        super().__init__(detail)
        self.detail = detail
        self.used = used
        self.limit = limit
        self.month = month

    def to_dict(self) -> dict[str, Any]:
        """The body names the way out, because a bare 409 is a support ticket."""

        return {
            "error": "esign_quota_exhausted",
            "detail": self.detail,
            "quota_used": self.used,
            "quota_limit": self.limit,
            "quota_month": self.month,
            "reset_day": vocab.QUOTA_RESET_DAY,
            "remedy": "Wait for the quota to reset on the 1st, or raise the subscription.",
        }


ERROR_TYPES = (AcceptanceRefused, EnvelopeNotFound, SignerNotFound, QuotaRefused)
