"""The errors this workflow raises, declared in one place.

All four are declared here and raised by nothing else in the product. That is what makes it
safe for the HTTP layer to map them: FastAPI accepts an exception handler on the app object
only, the host attaches the ones a feature exports, and the host refuses a second feature
registering a handler for a type this one already claimed.

Four types, because four different things can be wrong and a caller acts differently on each:

``PaymentRefused``
    A value this workflow will not accept: an unknown acceptance method, *Print and sign* on a
    quote with online payments, a fractional line-item quantity while billing is on, an unknown
    or disallowed payment method, an invalid effective date, a billing frequency outside the
    derived set, or a fourth tax ID. 400. The body carries a field-keyed ``errors`` map so each
    message lands beside its input.
``QuoteNotFound``
    A quote that does not exist. 404. A missing row must never become a 500.
``SetupNotFound``
    A payment setup, acceptance, charge, invoice, subscription or tax id that does not exist,
    or a quote that exists but was never provisioned for payments. 404. Kept apart from
    :class:`QuoteNotFound` because the remedy differs: a missing quote is a wrong id, and a
    missing setup is a real quote that was never published with payments.
``StateConflict``
    A well-formed request that contradicts the record's state: a second acceptance, a charge on
    a quote that is not accepted, a second charge, or a void or delete of an accepted quote.
    409, because the request was understood and the record refuses it.

Why a declined charge is not one of these
-----------------------------------------

The minimum-charge constraint is the payment processor's own outcome — "the total amount due
must be more than $0.50" — and a processor declining a charge is an ordinary result, not a
malformed request. :func:`dsr.quote_payment.rules.charge_outcome` therefore returns a declined
outcome rather than raising, the engine writes a ``declined`` charge row, and the route answers
200 with ``outcome: declined`` and the named reason. That mirrors
:mod:`dsr.quote_acceptance`'s treatment of a failed signature, and it is what lets the board
show a refused payment instead of an empty list.

Why the void and delete refusals are 409 and not 400
----------------------------------------------------

"Quotes can't be voided or deleted after they have been accepted." The request is well formed
and names a real quote; the quote's state is what refuses it. A 400 would say the caller sent
something wrong, which is false and would send a reviewer looking for a bad field.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


class PaymentRefused(Exception):
    """A value this workflow will not accept, with a field-keyed map of reasons."""

    def __init__(
        self,
        detail: str,
        errors: Mapping[str, str] | None = None,
        *,
        reason: str | None = None,
    ) -> None:
        super().__init__(detail)
        self.detail = detail
        self.reason = reason
        self.errors: dict[str, str] = dict(errors or {})

    def to_dict(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "error": "invalid_payment_request",
            "detail": self.detail,
            "errors": self.errors,
        }
        if self.reason:
            body["reason"] = self.reason
        return body


class QuoteNotFound(LookupError):
    """A quote that does not exist."""

    def __init__(self, detail: str, *, quote_id: str | None = None) -> None:
        super().__init__(detail)
        self.detail = detail
        self.quote_id = quote_id

    def to_dict(self) -> dict[str, Any]:
        return {"error": "quote_not_found", "detail": self.detail, "quote_id": self.quote_id}


class SetupNotFound(LookupError):
    """A payment setup, acceptance, charge, invoice or tax id that does not exist."""

    def __init__(
        self,
        detail: str,
        *,
        quote_id: str | None = None,
        reason: str | None = None,
    ) -> None:
        super().__init__(detail)
        self.detail = detail
        self.quote_id = quote_id
        self.reason = reason

    def to_dict(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "error": "payment_setup_not_found",
            "detail": self.detail,
            "quote_id": self.quote_id,
        }
        if self.reason:
            body["reason"] = self.reason
        return body


class StateConflict(Exception):
    """A well-formed request the record's state refuses."""

    def __init__(
        self,
        reason: str,
        detail: str,
        *,
        quote_id: str | None = None,
        errors: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(detail)
        self.reason = reason
        self.detail = detail
        self.quote_id = quote_id
        self.errors: dict[str, str] = dict(errors or {})

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": "payment_state_conflict",
            "reason": self.reason,
            "detail": self.detail,
            "quote_id": self.quote_id,
            "errors": self.errors,
        }


#: Every error type this feature maps to a response. A test pins the set so a later error
#: cannot be raised without a handler and turned into a 500.
ERROR_TYPES = (PaymentRefused, QuoteNotFound, SetupNotFound, StateConflict)
