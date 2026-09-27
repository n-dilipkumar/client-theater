"""This workflow's own error types.

Every one of them descends from :class:`SalesImpactError`, which is a *domain* type
rather than :class:`Exception`. That matters for how they reach the wire: FastAPI only
accepts exception handlers on the app object, so the feature exports
``EXCEPTION_HANDLERS`` and the plugin host attaches them process-wide. A handler
registered for ``Exception`` would therefore intercept every unrelated failure in the
product. A handler registered for a type declared here cannot.

The four refusals this package can make, and what each one means to the caller:

``InvalidFilter``
    The request asked for a report that cannot exist - a date range that runs backwards,
    or a date that is not a date. The caller's fault, and fixable from the request
    alone, so 422.
``InvalidDeal``
    The request described a deal with neither an external id nor a name, which is not
    enough to record or to find again. 422.
``DealConflict``
    The CRM id in the request is already attached. Re-posting it would be a second row
    counting the same deal twice in every rollup, so the request is refused and the
    existing record is named. 409.
``UnknownWorkspace``
    A deal was attached to a workspace that does not resolve to a live room. 404.

``RecordNotFound`` is deliberately **not** re-declared here. The core app already maps
it to 404, and two handlers for one type is a collision the feature host refuses to mount,
which would take the whole feature offline.
"""

from __future__ import annotations


class SalesImpactError(Exception):
    """Base of every refusal this workflow makes."""


class InvalidFilter(SalesImpactError):
    """A report filter that cannot be satisfied - a reversed or unparseable range."""


class InvalidDeal(SalesImpactError):
    """A deal payload with neither a CRM id nor a name to key it by."""


class DealConflict(SalesImpactError):
    """A CRM id that is already attached to a workspace.

    ``existing_id`` is the record already holding the id, so a client that receives
    this can patch it instead of guessing.
    """

    def __init__(self, message: str, *, existing_id: str = "", existing_room_id: str = "") -> None:
        super().__init__(message)
        self.existing_id = existing_id
        self.existing_room_id = existing_room_id


class UnknownWorkspace(SalesImpactError):
    """A workspace id that does not resolve to a live room record."""
