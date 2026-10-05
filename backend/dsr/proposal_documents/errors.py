"""WF-103: the error types this workflow raises, and the one place a code becomes a status.

The reason code travels on the exception rather than being searched for in the message.
An earlier version of this codebase derived a recorded outcome by scanning the prose
for words like "expired" and "single-use", which meant a reworded sentence silently
changed a recorded outcome. A call log that renames its own failures is not evidence of
anything.

Both types are this workflow's own, which is what makes them safe to map. The host
refuses a second feature that maps an error type another feature already handles, and
mapping a builtin such as ``ValueError`` or ``RecordNotFound`` would intercept it
app-wide. Nothing here subclasses either.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from dsr.proposal_documents import vocabulary as vocab


class ProposalRefusal(ValueError):
    """A researched rule, a state or a signature this workflow will not accept.

    The status and the sentence are looked up from
    :data:`~dsr.proposal_documents.vocabulary.ERROR_CODES` inside the constructor, so a
    raise site names only a code and cannot drift from the status the page expects or
    from the published sentence. That dictionary is the one place a refusal code is
    named, and it is served at ``GET /api/wf-103/vocabulary``.

    ``errors`` is keyed by field so a page puts each message beside the input that
    caused it, rather than combining several problems into one sentence a reader
    cannot act on.
    """

    def __init__(
        self, code: str, message: str | None = None, errors: Mapping[str, str] | None = None
    ) -> None:
        status, detail = vocab.ERROR_CODES.get(code, (422, code))
        super().__init__(message or detail)
        self.code = code
        self.status = status
        self.detail = message or detail
        self.errors: dict[str, str] = dict(errors or {})

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": self.code,
            "detail": self.detail,
            "status": self.status,
            "errors": self.errors,
        }


class ProposalNotFound(LookupError):
    """No such document, approval or deal.

    Its own type rather than the store's ``RecordNotFound``, for the same reason as
    every other workflow in this repository: a feature may only map error types it
    raises itself, and the core app already maps ``RecordNotFound`` to a 404.
    """

    code = "unknown_document"
    status = 404

    def __init__(self, code: str, label: str, record_id: str) -> None:
        super().__init__(f"{label} {record_id} not found")
        self.code = code
        self.detail = str(self)
        self.record_id = record_id

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": self.code,
            "detail": self.detail,
            "status": self.status,
            "id": self.record_id,
        }


def refuse(code: str, message: str | None = None, **errors: str) -> ProposalRefusal:
    """A refusal of a published code, built at the call site that raised it.

    The keyword form is what a raise site reads as, and it exists so the field a
    message belongs to is written next to the message::

        raise refuse("unknown_recipient_role", role=str(role))
    """
    return ProposalRefusal(code, message, errors or None)
