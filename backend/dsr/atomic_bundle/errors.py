"""One error hierarchy for the atomic bundle package (WF-039).

Every refusal this package makes is a caller's to fix, so they share a base class
and the HTTP layer registers one handler per *distinct HTTP answer* rather than
one per message. Anything that is not a :class:`BundleError` is a bug and must
propagate.

The split is driven by what the caller can do about it:

``BundleError``
    The bundle cannot be honoured as written. ``400``.
``BundleShapeError``
    A declared record has no ``reference_id`` or no type, or the declaration is
    not a list. Structural: the bundle is not the shape the research describes.
``ReferenceError``
    A declared dependency names a record that does not exist, names itself, or
    names one *later* in the list. The research is explicit that subrequests are
    built "in dependency order" and that a later subrequest references an earlier
    one, so a forward reference is a contradiction rather than something to
    reorder silently.
``PolicyError``
    A rollback policy this dialect cannot honour. The sourced reason is the
    sObject Tree endpoint: "If an error occurs while creating a record, the
    entire request fails" - there is no partial mode to ask for.
``LimitExceeded``
    The bundle is larger than a documented limit for the chosen dialect - 25
    subrequests, 5 sObject Collections, 200 records across all trees, five record
    types, five levels deep, or 1,000 batch requests. Refusing at plan time means
    nothing goes over the wire and the room learns the reason.
``BundleNotConfigured``
    Well formed, but this installation cannot answer it yet - no connector, or
    the connector has no base URL. ``428``, so a client can say "finish the
    setup" rather than "you got the request wrong".
``NotFound``
    A room, bundle, connector or run id does not resolve, or does not resolve
    *for that room*. ``404``. One type carrying the resource name rather than
    four near-identical ones, because the only thing that differs is a word and
    the word belongs in the message.

The two error types the *core* app already maps - ``RecordNotFound`` and
``AuditError`` - are deliberately not claimed here. Two handlers for one type is
a collision the feature host refuses, and the core mappings are already correct.
"""

from __future__ import annotations

from typing import Any


class BundleError(ValueError):
    """The bundle cannot be honoured as written."""


class BundleShapeError(BundleError):
    """The declaration is not the ordered record set the research describes."""


class ReferenceError(BundleError):  # noqa: A001 - shadowing a builtin is deliberate
    """A declared dependency does not resolve to an earlier record."""


class PolicyError(BundleError):
    """This dialect cannot honour the requested rollback policy."""


class LimitExceeded(BundleError):
    """The bundle is larger than a documented limit for this dialect."""


class BundleNotConfigured(BundleError):
    """This installation is not set up to answer the request yet."""


class NotFound(LookupError):
    """A record id does not resolve - here, or under the room asked for.

    Carries the resource name and the id as attributes rather than only in the
    message, so a handler can put them in the body instead of parsing prose.
    """

    def __init__(self, resource: str, record_id: Any, room_id: str | None = None) -> None:
        self.resource = resource
        self.record_id = str(record_id)
        self.room_id = str(room_id) if room_id is not None else None
        where = f" in room {self.room_id}" if self.room_id else ""
        super().__init__(f"{resource} {self.record_id} not found{where}")
