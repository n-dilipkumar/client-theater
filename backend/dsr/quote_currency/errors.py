"""The errors this workflow raises, declared in one place.

All four are declared here and raised by nothing else in the product. That is what makes
it safe for the HTTP layer to map them: FastAPI accepts an exception handler on the app
object only, the host attaches the ones a feature exports, and the host refuses a second
feature registering a handler for a type this one already claimed.

Four types, because four different things can be wrong and a caller acts differently on
each:

``CurrencyRefusal``
    A value this workflow will not accept: an ISO code it does not recognise, a precision
    outside 0 to 6, a non-positive exchange rate, a negative quantity. 400.
``QuoteNotFound``
    A quote, price list or currency record that does not exist. 404. Not a 500, because a
    feature must never turn a missing row into a server fault.
``CurrencyChangeRefused``
    A change to a quote's currency while that quote still holds line items. 409, because
    the request was well formed and conflicts with the state of the record. The evidence
    is explicit: "You can't change the currency of the base record (in this case, an
    quote), unless you remove all the line items associated with the record."
``RateUnavailable``
    A quote stamped in a transaction currency with no usable rate to convert into the base
    currency. 409 as well, and deliberately not a refusal: the value is well formed and the
    state is wrong, which is the definition of a conflict.

Why the refusal to price is *not* one of these
-----------------------------------------------

The specification says that on a wrong-currency combination "the platform refuses to price
and sets ``pricingerrorcode``". A refusal to price is therefore an **outcome**, not an
error: the pricing run happened, it produced no totals, and it recorded why. So it is a
normal 200 response carrying ``outcome: refused`` and the named code, and the run writes an
audit row exactly as a successful run does, because "Both success and failure produce audit
actions" is the same principle the rest of this product applies to a failed attempt.

Turning it into an HTTP error instead would be wrong in a way a reviewer would see
immediately: a caller could not tell the difference between "this quote is misconfigured"
and "this endpoint is broken", and a retry loop would hammer a route that is behaving
correctly.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


class CurrencyRefusal(Exception):
    """A value this workflow will not accept, with a field-keyed map of reasons."""

    def __init__(self, detail: str, errors: Mapping[str, str] | None = None) -> None:
        super().__init__(detail)
        self.detail = detail
        self.errors: dict[str, str] = dict(errors or {})

    def to_dict(self) -> dict[str, Any]:
        return {"error": "invalid_currency_request", "detail": self.detail, "errors": self.errors}


class QuoteNotFound(LookupError):
    """A quote, price list or currency record that does not exist."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail

    def to_dict(self) -> dict[str, Any]:
        return {"error": "not_found", "detail": self.detail}


class CurrencyChangeRefused(Exception):
    """A currency change refused because the quote still holds line items."""

    def __init__(self, detail: str, *, quote_id: str, line_count: int) -> None:
        super().__init__(detail)
        self.detail = detail
        self.quote_id = quote_id
        self.line_count = line_count

    def to_dict(self) -> dict[str, Any]:
        """The body names the way out, because a bare 409 is a support ticket."""

        return {
            "error": "currency_change_refused",
            "detail": self.detail,
            "quote_id": self.quote_id,
            "line_items": self.line_count,
            "evidence": (
                "You can't change the currency of the base record (in this case, an quote), "
                "unless you remove all the line items associated with the record."
            ),
            "remedy": "Remove every line item from the quote, then set the currency again.",
        }


class RateUnavailable(Exception):
    """A transaction currency with no usable rate into the base currency."""

    def __init__(self, detail: str, *, iso_code: str) -> None:
        super().__init__(detail)
        self.detail = detail
        self.iso_code = iso_code

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": "exchange_rate_unavailable",
            "detail": self.detail,
            "iso_code": self.iso_code,
            "remedy": (
                "Stamp an exchange rate on the currency record, or set its currency_type to "
                "Custom and stamp the rate as a custom value."
            ),
        }


ERROR_TYPES = (CurrencyRefusal, QuoteNotFound, CurrencyChangeRefused, RateUnavailable)
