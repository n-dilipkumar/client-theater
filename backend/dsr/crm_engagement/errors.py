"""This workflow's own error types.

Every one descends from :class:`EngagementSyncError`, which is a *domain* type rather
than :class:`Exception`. That matters for how they reach the wire: FastAPI only accepts
exception handlers on the app object, so the feature exports ``EXCEPTION_HANDLERS`` and
the plugin host attaches them process-wide. A handler registered for ``Exception`` would
intercept every unrelated failure in the product. A handler registered for a type
declared here cannot.

The five refusals this package can make, and what each one means to the caller:

``EngagementSyncError``
    The base. A body this layer will not act on at all - 400, the caller's to fix from
    the request alone.
``InvalidConnector``
    A connector that cannot be written down: no vendor, a vendor this build does not
    speak, or no target object. 422, because the body parsed and named something
    impossible rather than something malformed.
``InvalidEventType``
    An event-catalogue row with no event type, or one whose name collides with a row
    already there. 422.
``InvalidFieldMap``
    A field map with no fields, a field with no target property, two fields claiming the
    same target, or a sync key naming a target that is not mapped. 422. The last one is
    the researched consequence of the sync key: it is a property on the CRM object, so a
    field map that never sends it cannot satisfy the uniqueness the CRM is meant to
    enforce.
``SyncNotConfigured``
    Well formed, but this installation is not set up to answer it yet - no connector for
    the room, or the only connector is switched off. 428, so a client can say "finish the
    setup" rather than "you got the request wrong". Distinct from 400 for exactly that
    reason, and ``apiRequest`` in the frontend carries the status.
``UnknownRoom``
    A room id that does not resolve to a live record. 404.

``RecordNotFound`` is deliberately **not** re-declared here. The core app already maps it
to 404, and two handlers for one type is a collision the feature host refuses to mount,
which would take the whole feature offline.
"""

from __future__ import annotations


class EngagementSyncError(Exception):
    """Base of every refusal this workflow makes."""


class InvalidConnector(EngagementSyncError):
    """A connector naming a vendor this build cannot write to, or no target object."""


class InvalidEventType(EngagementSyncError):
    """An event-catalogue row that cannot be saved as asked."""


class InvalidFieldMap(EngagementSyncError):
    """A field map that could not produce a complete, non-colliding create payload."""


class SyncNotConfigured(EngagementSyncError):
    """No usable connector for the room, so there is nothing to drain into.

    Its own type and its own status, because it is the one refusal a caller fixes by
    configuring something rather than by changing the request. Starlette picks the most
    specific registered handler by MRO, so this wins over the base for the same
    exception.
    """


class UnknownRoom(EngagementSyncError):
    """A room id that does not resolve to a live record."""
