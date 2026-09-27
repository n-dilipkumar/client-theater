"""One error hierarchy for the backfill package.

Every refusal this package makes about a *request* is a caller's mistake, so the
types share a base and the feature module registers a single handler for it.
Anything that is not a :class:`BackfillError` is a bug and must propagate.

``code`` and ``status`` ride on the exception rather than being decided inside the
handler, because these refusals are not all the same kind of thing. A malformed
date range is a 400. A request for a connection that does not exist, a run that
is already complete, or a Dataverse token that has aged out is a 409: the
request was well formed and what it conflicts with is the state of the system.
A handler that answered 400 for both would be lying about the second.

FastAPI only accepts exception handlers on the app object, so the feature module
exports this mapping as ``EXCEPTION_HANDLERS``; two features may not map the same
type, which is why the whole hierarchy hangs off one base class.
"""

from __future__ import annotations


class BackfillError(ValueError):
    """A backfill request cannot be honoured as written.

    A ``ValueError`` because every subclass is caused by something the caller
    sent. Nothing in this package raises for a fault of its own.
    """

    code = "backfill_error"
    status = 400


class PlanError(BackfillError):
    """The requested backfill cannot be turned into a plan.

    Raised for a range that runs backwards, a field map that names no key, an
    object the vendor's own addressing rules do not let you name, or a volume
    for which no supported strategy exists.
    """

    code = "backfill_plan_error"


class InvalidRange(PlanError):
    """The date range does not describe a range.

    ``from`` after ``to``, an unparseable timestamp, or a range that starts in
    the future. Named separately because the remedy is a different field each
    time, and the wizard shows the message next to the picker.
    """

    code = "backfill_range_invalid"


class UnaddressableObject(PlanError):
    """The vendor's own rules do not let this object be named as asked.

    HubSpot's exports API: "For standard objects, you can use the object's name
    (e.g., ``CONTACT``), but for custom objects, you must use the ``objectTypeId``
    value." A custom object named by its label is a request the vendor will
    refuse, so it is refused here first, with the field to use instead.
    """

    code = "backfill_object_not_addressable"


class PrerequisiteError(BackfillError):
    """A prerequisite the researched flow assumes is not in place.

    409 rather than 400: the request is well formed, and what it lacks is a
    relationship that the rest of the system owns - a connection, an adapter, a
    granted scope, a strategy this vendor implements.
    """

    code = "backfill_prerequisite_missing"
    status = 409


class UnknownConnection(PrerequisiteError):
    """No connection with that id exists."""

    code = "backfill_connection_not_found"


class UnsupportedVendor(PrerequisiteError):
    """No registered adapter handles the connection's vendor.

    The research's extensibility note says a third party "can add a new vendor
    by implementing only 'create job' and 'read page'". This is the refusal that
    note implies: until such an adapter is registered, the vendor is unknown, and
    saying so is better than answering a backfill with an empty replica.
    """

    code = "backfill_vendor_not_supported"


class UnsupportedStrategy(PrerequisiteError):
    """This vendor does not implement the strategy the plan needs."""

    code = "backfill_strategy_not_supported"


class DirectionNotSupported(PrerequisiteError):
    """A reverse backfill was asked of a vendor that cannot receive one.

    The researched data flow ends "or push into CRM for a reverse backfill". The
    direction is therefore part of the vocabulary, and an adapter that only ever
    sources history declares that it cannot receive it.
    """

    code = "backfill_direction_not_supported"


class MissingScope(PrerequisiteError):
    """The connection's token cannot reach the operation being asked for.

    HubSpot: "When using an OAuth access token to authenticate requests to the
    exports API, the user installing the app must be a Super Admin to grant the
    ``crm.export`` scope." A backfill started without it produces a job that
    fails after the fact; refusing here names the grant instead.
    """

    code = "backfill_scope_not_granted"


class QuotaExceeded(PrerequisiteError):
    """The backfill cannot fit inside what is left of today's allowance.

    HubSpot: "The **daily** limit resets at midnight based on your time zone
    setting." A run sized past the remaining calls would be cut off mid-page,
    leaving a cursor parked against a window that is about to close.
    """

    code = "backfill_quota_exceeded"


class CursorExpired(BackfillError):
    """The stored cursor is older than the vendor will still answer for.

    Dataverse: "Changes are returned if the last token is within a default value
    of seven days. The value of the **Organization** table
    ``ExpireChangeTrackingInDays`` column controls this duration and can be
    changed. If unprocessed changes are older than the configured value, the
    system throws an exception."

    409, and never a silent restart. A full re-read is a different backfill: it
    would write every row again and would make "no duplicates, no gaps" untrue
    for the run that recorded the cursor. The remedy is a new full-history
    backfill, which the run's own error message says.
    """

    code = "backfill_cursor_expired"
    status = 409


class RunStateError(BackfillError):
    """The run is not in a state where this operation means anything.

    Polling a finished run, resuming a live one, cancelling one already
    cancelled. 409 for the same reason as the rest of the hierarchy: the request
    is fine, the state is what conflicts.
    """

    code = "backfill_run_state_conflict"
    status = 409


class RunNotFound(BackfillError):
    """No run with that id exists in this room."""

    code = "backfill_run_not_found"
    status = 404


class UnkeyedRow(BackfillError):
    """A row from the vendor carries no value for the replica's key.

    A row that cannot be keyed cannot be upserted, and a backfill that stored it
    anyway would break the one property the research insists on: that a restart
    produces "no duplicates, no gaps". The page is not stalled for it; the row is
    rejected, counted, and named in the run's log.
    """

    code = "backfill_row_not_keyed"
