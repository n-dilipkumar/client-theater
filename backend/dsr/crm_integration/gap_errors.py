"""One error hierarchy for the change-stream repair package.

Every refusal this package makes is a caller's mistake, or a conflict with state
that already exists, so the types share a base and the feature module registers a
single handler for it. Anything that is not a :class:`GapReconcileError` is a bug
and must propagate.

``code`` and ``status`` ride on the exception rather than being decided inside the
handler. Asking to repair a record with no gap open against it, and asking to
resume from a cursor the vendor has stopped answering for, are both this
package's errors, and only one of them is a malformed request.

A second base class, deliberately
--------------------------------

WF-042's inbound read package hangs its hierarchy off ``CrmIntegrationError`` and
registers a handler for it. The host refuses two features mapping one exception
type, so this package hangs its own hierarchy off a new
:class:`GapReconcileError` rather than reusing one that is already claimed. The two
features share a directory and share no exception type.
"""

from __future__ import annotations


class GapReconcileError(ValueError):
    """A change-stream repair cannot be honoured as written.

    A ``ValueError`` because every subclass is caused by something the caller sent,
    or by a conflict between that and a state that already exists. Nothing in this
    package raises for a fault of its own.
    """

    code = "gap_reconcile_error"
    status = 400


# --------------------------------------------------------------------------- #
# The event header
# --------------------------------------------------------------------------- #


class GapEventError(GapReconcileError):
    """A gap or overflow header cannot be read as the research describes it."""

    code = "gap_event_error"


class UnknownGapType(GapEventError):
    """No researched change type answers to that name.

    The message names the five values the vendor uses, because a caller who sent a
    gap type to a different workflow deserves to learn that the enumeration exists
    rather than to guess at it.
    """

    code = "unknown_gap_change_type"


class MissingRecordId(GapEventError):
    """A gap event carries no record id.

    400 rather than 404. The gap event "contain[s] the record ID, which enables you
    to retrieve the record from Salesforce", so an event without one is not a
    request this API can fulfil. Saying "not found" would send the caller looking
    for a row that does not exist instead of for the field they left out.
    """

    code = "gap_record_id_required"


class MissingEntity(GapEventError):
    """The header names no entity type.

    An overflow emits "one overflow event for each entity type included in that
    set", so an event with no entity cannot say which entity it lost.
    """

    code = "gap_entity_required"


class MalformedGapEvent(GapEventError):
    """A header field is present but cannot be read.

    Covers the timestamps and the change count. A half-read header is refused
    rather than stored, because the dirty marker's whole value is the gap's commit
    timestamp: "mark the corresponding record as dirty locally as of the date of
    the gap event".
    """

    code = "malformed_gap_event"


class UnsupportedVendor(GapEventError):
    """This vendor's stream has no gap or overflow mechanism the research describes.

    The message quotes the research, because the operator who hits it will want the
    sentence rather than a bare rejection.
    """

    code = "unsupported_gap_vendor"


# --------------------------------------------------------------------------- #
# The dirty set
# --------------------------------------------------------------------------- #


class DirtySetError(GapReconcileError):
    """A record cannot be marked, found, or cleared in the dirty set."""

    code = "dirty_set_error"


class NoDirtyRecord(DirtySetError):
    """The room asked to repair a record that is not dirty.

    409, because the request is well formed and what it conflicts with is the
    room's own state. Repairing a clean record is not harmless: it would apply a
    delete diff across an entity the room has no reason to be reconciling, and the
    research ties the repair to the gap that opened it.
    """

    code = "no_dirty_record_to_reconcile"
    status = 409


class UnknownEntity(DirtySetError):
    """No entity type answers to that name in this room.

    404, because the entity is not a thing this workflow repairs, which is
    different from a request that is malformed.
    """

    code = "unknown_gap_entity"
    status = 404


# --------------------------------------------------------------------------- #
# The cursors
# --------------------------------------------------------------------------- #


class CursorError(GapReconcileError):
    """A resumable position cannot be stored, read, or resumed from.

    There is no "cursor expired" error here, and the absence is deliberate: an
    expired delta link is not a refusal. A reconcile that meets one ends in the
    ``expired_cursor`` run state and names the full re-read as its remedy, because
    the research calls that window "the hard deadline after which recovery must
    fall back to a full re-read" rather than an error to be reported.
    """

    code = "gap_cursor_error"


class UnknownCursorKind(CursorError):
    """No researched cursor answers to that name.

    The two are not interchangeable and the research says so: the Salesforce Replay
    ID and the Dataverse delta link "were not reconciled in a single source".
    """

    code = "unknown_gap_cursor_kind"


class CursorMissing(CursorError):
    """The room holds no cursor of that kind, so there is nothing to resume from."""

    code = "no_gap_cursor_stored"
    status = 409


class MismatchedCursorVendor(CursorError):
    """The stored cursor belongs to a vendor the request does not name.

    409 rather than 400, for the same reason: the room's state is what conflicts.
    A Salesforce Replay ID cannot resume a Dataverse poll, and letting a caller
    pair them would skip or repeat every row in between.
    """

    code = "gap_cursor_vendor_mismatch"
    status = 409


# --------------------------------------------------------------------------- #
# The run
# --------------------------------------------------------------------------- #


class ReconcileError(GapReconcileError):
    """A reconciliation cannot be planned or completed as asked."""

    code = "gap_reconcile_run_error"


class UnknownEvent(ReconcileError):
    """No gap or overflow event matches that id in this room."""

    code = "gap_event_not_found"
    status = 404


class UnknownRun(ReconcileError):
    """No reconciliation run matches that id in this room."""

    code = "gap_reconcile_run_not_found"
    status = 404


class UnknownDeletedSource(ReconcileError):
    """No researched way of deriving the deleted set answers to that name.

    The research offers exactly two for Salesforce and one shape for Dataverse, so
    a fourth would be a guess at what the room deletes from the replica.
    """

    code = "unknown_deleted_source"


class DeletedSourceVendorMismatch(ReconcileError):
    """The deleted source does not belong to the vendor this run reads.

    The Recycle Bin is a Salesforce query and the delta link is a Dataverse cursor.
    Pairing either with the other vendor reads an empty answer, and an empty answer
    read as "nothing was deleted" would delete nothing when the truth is that the
    room never asked the right question.
    """

    code = "deleted_source_vendor_mismatch"


__all__ = [
    "CursorError",
    "CursorMissing",
    "DirtySetError",
    "GapEventError",
    "GapReconcileError",
    "MalformedGapEvent",
    "MismatchedCursorVendor",
    "MissingEntity",
    "MissingRecordId",
    "NoDirtyRecord",
    "ReconcileError",
    "UnknownCursorKind",
    "UnknownDeletedSource",
    "UnknownEntity",
    "UnknownEvent",
    "UnknownGapType",
    "UnknownRun",
    "UnsupportedVendor",
    "DeletedSourceVendorMismatch",
]
