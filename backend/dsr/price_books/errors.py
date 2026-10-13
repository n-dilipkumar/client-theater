"""The error types this domain raises.

None of them is a builtin, and that is the point. The feature host attaches one
handler per error type, so a handler registered for ``ValueError`` would
intercept that exception across the whole product: one feature's typo would turn
every unrelated 500 into a 422. These two are declared here, raised only from
:mod:`dsr.price_books`, and mapped by exactly one feature.

Two types because two different things are wrong:

:class:`CatalogueRefusal`
    The request is malformed, or its state refuses it. The status and the code
    come from ``ERROR_CODES`` in :mod:`dsr.price_books.vocabulary`, so the status
    a refusal answers with lives with the sentence a page shows for it.
:class:`CatalogueNotFound`
    The record does not exist. Carries nothing but a message, because a 403 would
    confirm that a record exists behind an id.
"""

from __future__ import annotations

from typing import Any, Mapping

from dsr.price_books import vocabulary as vocab


class CatalogueRefusal(ValueError):
    """A refused request, carrying the code, the status and the field messages.

    ``code`` is the stable token from ``ERROR_CODES``. A page branches on the
    token and supplies its own words, so rewording the sentence in the vocabulary
    cannot leave the interface calling a state that no longer exists.

    ``errors`` maps a field name to the message for that field, so a form can put
    each message beside the input that caused it rather than printing one
    sentence about a row of inputs.
    """

    def __init__(
        self,
        code: str,
        message: str | None = None,
        errors: Mapping[str, str] | None = None,
    ) -> None:
        status, detail = vocab.ERROR_CODES.get(code, (422, code))
        super().__init__(message or detail)
        self.code = code
        self.status = status
        self.detail = message or detail
        self.errors: dict[str, str] = dict(errors or {})

    def as_dict(self) -> dict[str, Any]:
        return {
            "error": self.code,
            "detail": self.detail,
            "status": self.status,
            "errors": self.errors,
        }


class CatalogueNotFound(LookupError):
    """No such product, price book or price-book row."""

    code = "not_found"
    status = 404

    def __init__(self, code: str, label: str, record_id: str) -> None:
        super().__init__(f"{label} {record_id} not found")
        self.code = code
        self.label = label
        self.record_id = record_id

    def as_dict(self) -> dict[str, Any]:
        return {
            "error": self.code,
            "detail": str(self),
            "status": self.status,
            "id": self.record_id,
        }


def refuse(code: str, message: str | None = None, **errors: str) -> CatalogueRefusal:
    """A refusal of a known code, built at the call site that raised it.

    The keyword form exists so a validator reads as one line per field::

        raise refuse("tier_needs_a_price", **{"tiers[2].unit_price": "Give it a price."})
    """
    return CatalogueRefusal(code, message, errors or None)
