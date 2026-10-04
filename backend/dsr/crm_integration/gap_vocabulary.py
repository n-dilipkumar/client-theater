"""The values WF-050 fixes by name, and the sentences that fix them.

Everything in this module is either quoted from the research for this workflow
(``docs/research/digital-sales-room-workflows/wf/WF-050.md``, built from
``docs/research/raw/crm-integration.md`` section 18) or is the smallest
vocabulary that has to exist for one of those quotes to be enforceable. Nothing
here is a preference. If a value is not on this list, the API refuses it rather
than storing something the research never described.

The four facts that do the work
-------------------------------

**The gap types are a closed enumeration of four.** "The ``changeType`` field in
the gap event header identifies the gap event and the associated operation, and
can take one of these values: ``GAP_CREATE``, ``GAP_UPDATE``, ``GAP_DELETE``,
``GAP_UNDELETE``." That sentence is the whole of
:data:`GAP_CHANGE_TYPES`.

**An overflow is a fifth value, and it is not a gap.** "The ``changeType`` field
header value is ``GAP_OVERFLOW``." The two are kept apart because they repair
different things: a gap names one record, an overflow names none.

**An overflow names no record at all.** "Gap events don't contain record data,
but they contain the record ID, which enables you to retrieve the record from
Salesforce." / "Overflow events include header fields but no record data and no
record ID." :func:`classify` is where that difference becomes a rule rather than
a comment, and :data:`OVERFLOW_CHANGE_THRESHOLD` is the number that turns a
header's change count into a decision.

**The two resumable positions are not the same shape.** The research's own gaps
section says the Salesforce Replay ID and the Dataverse delta link "were not
reconciled in a single source", so they are two named cursors here rather than
one, each with its own scope.
"""

from __future__ import annotations

from typing import Any

#: "The ``changeType`` field in the gap event header identifies the gap event and
#: the associated operation, and can take one of these values: ``GAP_CREATE``,
#: ``GAP_UPDATE``, ``GAP_DELETE``, ``GAP_UNDELETE``."
GAP_CHANGE_TYPES: tuple[str, ...] = (
    "GAP_CREATE",
    "GAP_UPDATE",
    "GAP_DELETE",
    "GAP_UNDELETE",
)

#: "The ``changeType`` field header value is ``GAP_OVERFLOW``." A fifth value, and
#: not a member of :data:`GAP_CHANGE_TYPES`, because it names no operation and no
#: record.
OVERFLOW_CHANGE_TYPE = "GAP_OVERFLOW"

#: Every value a gap or overflow header may carry. The order is the research's
#: order for the four, with the overflow last, and it is what the vocabulary route
#: serves so a client renders its picker from this rather than from a list compiled
#: into a page.
CHANGE_TYPES: tuple[str, ...] = GAP_CHANGE_TYPES + (OVERFLOW_CHANGE_TYPE,)

#: "Overflow events are generated when a single transaction involves more than
#: 100,000 changes. The first 100,000 changes generate change events."
#:
#: A constant and not a literal in a condition, because the research states it as a
#: fact about the vendor rather than as a setting a deployment may choose.
OVERFLOW_CHANGE_THRESHOLD = 100_000

#: The gap event tells the room which operation it could not emit, which decides
#: what a full re-read is expected to find. ``GAP_DELETE`` expects the record to be
#: gone; ``GAP_UNDELETE`` expects it back.
GAP_OPERATION: dict[str, str] = {
    "GAP_CREATE": "create",
    "GAP_UPDATE": "update",
    "GAP_DELETE": "delete",
    "GAP_UNDELETE": "undelete",
}

#: The two vendors the research gives a gap or overflow mechanism.
GAP_VENDORS: tuple[str, ...] = ("salesforce", "dataverse")

#: The vendor the research records as having no such mechanism. Kept as a named
#: value rather than omitted, because a caller that asks is owed a sentence rather
#: than a silent 404.
UNSUPPORTED_GAP_VENDORS: tuple[str, ...] = ("hubspot",)

#: "HubSpot has no documented gap/overflow analogue (it uses webhook redelivery
#: instead, see W11's retry evidence)". Quoted in the refusal, so the operator can
#: search the research for it.
UNSUPPORTED_VENDOR_QUOTE = (
    "HubSpot has no documented gap/overflow analogue (it uses webhook redelivery instead)"
)

#: The two named resumable positions, and what each one is a position *within*.
#:
#: A single ``cursor`` field would have to lie about one of them. Salesforce issues
#: "one overflow event for each entity type included in that set", so its Replay ID
#: is per entity type. Dataverse returns one opaque ``@odata.deltaLink`` for the
#: whole response, with no per-entity position at all.
CURSOR_KINDS: dict[str, str] = {
    "replay_id": (
        "The Salesforce Replay ID of the overflow event. One per entity type, "
        "because the vendor emits one overflow event per entity type."
    ),
    "delta_link": (
        "The Dataverse delta link. One per organization, opaque, with no per-entity position."
    ),
}

#: What each cursor is scoped by, and so what makes it unique in a room.
CURSOR_SCOPE: dict[str, str] = {
    "replay_id": "entity",
    "delta_link": "org",
}

#: The field each cursor kind is stored in the standard cursor record. The record
#: shape is the one WF-045's backfill already established, so a room reads one
#: cursor vocabulary rather than two.
CURSOR_FIELDS: tuple[str, ...] = ("vendor", "connectionId", "cursor", "updatedAt")

#: A dirty marker's states. A marker is kept after the repair rather than deleted,
#: so the data health view can answer which run reconciled which record.
DIRTY_STATES: tuple[str, ...] = ("dirty", "reconciled")

#: Reconciliation run states. ``expired_cursor`` is the one the seven-day rule
#: forces into existence: a delta link past its window is not a failure of the run,
#: it is a run that cannot move.
RUN_STATES: tuple[str, ...] = ("open", "complete", "expired_cursor", "refused")

#: A run in one of these is finished.
TERMINAL_RUN_STATES: frozenset[str] = frozenset({"complete", "expired_cursor", "refused"})

#: The three ways the deleted set is derived. The first two are the research's two
#: options for step 4 of the overflow procedure, and the third is what Dataverse
#: needs because its delta response already carries the deletions.
DELETED_SOURCES: dict[str, str] = {
    "difference": (
        "Option a: read the non-deleted records and delete whatever the room held "
        "that the read did not return."
    ),
    "recycle_bin": (
        "Option b: query the entity with isDeleted=true and delete exactly what "
        "that query returned."
    ),
    "dataverse_delta": (
        "Dataverse returns the deletions inside the same cursor, as rows whose "
        "context ends in $deletedEntity and whose reason is deleted."
    ),
}

#: Which vendor each deleted source belongs to, so a caller that pairs the wrong two
#: is refused rather than silently reading an empty Recycle Bin.
DELETED_SOURCE_VENDOR: dict[str, str] = {
    "difference": "",
    "recycle_bin": "salesforce",
    "dataverse_delta": "dataverse",
}

#: The sync-log line kinds a run writes, in the order the research fixes them. Every
#: state change writes one, so "records a reconciliation event in the sync log for
#: audit" is a queryable collection rather than a field somebody has to remember to
#: update.
LOG_EVENTS: tuple[str, ...] = (
    "run_opened",
    "unsubscribed",
    "replay_id_stored",
    "delta_link_expired",
    "record_read",
    "replica_overwritten",
    "replica_deleted",
    "rows_written",
    "rows_deleted",
    "dirty_flag_cleared",
    "resubscribed",
    "run_complete",
    "run_refused",
    "run_expired_cursor",
)

#: The researched numbers, each with the sentence that fixes it.
NUMBERS: dict[str, dict[str, Any]] = {
    "overflow_change_threshold": {
        "value": OVERFLOW_CHANGE_THRESHOLD,
        "quote": (
            "Overflow events are generated when a single transaction involves more "
            "than 100,000 changes."
        ),
        "meaning": (
            "The change count in one transaction above which Salesforce stops "
            "emitting change events and emits an overflow event instead."
        ),
    },
    "change_tracking_expiry_days": {
        "value": 7,
        "quote": (
            "Changes are returned if the last token is within a default value of "
            "seven days. If unprocessed changes are older than the configured value, "
            "the system throws an exception."
        ),
        "meaning": (
            "How long a stored Dataverse delta link stays resumable. Past this the "
            "room falls back to a full re-read rather than asking for a token the "
            "vendor has stopped answering for."
        ),
    },
}

#: The header fields the research's data flow names. Accepted in both the vendor's
#: camelCase and this product's snake_case, because the research writes the names in
#: the vendor's spelling and a client that has to remember which is which is a
#: client that gets one wrong.
HEADER_FIELDS: dict[str, tuple[str, ...]] = {
    "change_type": ("change_type", "changeType"),
    "transaction_key": ("transaction_key", "transactionKey"),
    "commit_timestamp": ("commit_timestamp", "commitTimestamp"),
    "record_ids": ("record_ids", "recordIds", "recordIds"),
    "replay_id": ("replay_id", "replayId"),
    "entity": ("entity", "sobject", "changeOrigin", "changeOriginEntity"),
    "change_count": ("change_count", "changeCount", "numChanges"),
    "sequence_number": ("sequence_number", "sequenceNumber"),
}

#: The record payload's own alias, kept out of the table above only so that table
#: reads as the research wrote it.
RECORD_ALIASES: tuple[str, ...] = ("record", "recordId", "record_id", "id")

#: The field on a CRM record that the research names as the one to compare against a
#: change event. Spelled as the vendor spells it, and compared as an instant.
LAST_MODIFIED_FIELD = "LastModifiedDate"

#: The key a dirty marker is stored under. A room holds several buyers, so a record
#: id alone cannot identify a dirty row.
DIRTY_KEY: tuple[str, ...] = ("room_id", "entity", "record_id")


def normalise_change_type(value: Any) -> str | None:
    """The canonical spelling of a change type, or ``None`` if it is not one.

    The case is folded before the comparison because the research's
    case-sensitivity rule is about channel names and says nothing about how a
    client spells ``changeType``. Folding the case and refusing an unknown word are
    different decisions, and only the second one is the research's.
    """
    if not isinstance(value, str):
        return None
    upper = value.strip().upper()
    return upper if upper in CHANGE_TYPES else None


def is_overflow(change_type: Any) -> bool:
    """Whether this value is the overflow marker."""
    return normalise_change_type(change_type) == OVERFLOW_CHANGE_TYPE


def is_gap(change_type: Any) -> bool:
    """Whether this value is one of the four per-record gap types."""
    return normalise_change_type(change_type) in GAP_CHANGE_TYPES


def classify(change_type: Any) -> str:
    """``"overflow"`` or ``"gap"``, the word the whole engine branches on.

    The classification is deliberately two-valued rather than five-valued, because
    the two repair shapes are what every rule in this package keys on. A gap names
    one record and repairs it with one read. An overflow names none and repairs a
    whole entity with a read plus a delete diff.
    """
    return "overflow" if is_overflow(change_type) else "gap"


def operation_for(change_type: Any) -> str:
    """The operation a gap type says the CRM could not emit.

    ``""`` for the overflow marker, which "include[s] header fields but no record
    data and no record ID" and therefore names no operation either.
    """
    return GAP_OPERATION.get(str(change_type), "")


def default_expiry_days() -> int:
    """The researched default for a Dataverse cursor the vendor will age out.

    Seven days. A connection may override it, because the research says the
    Organization column "controls this duration and can be changed", but the value
    it falls back to is the researched one.
    """
    return int(NUMBERS["change_tracking_expiry_days"]["value"])


def cursor_supports_gap(vendor: Any) -> bool:
    """Whether this vendor's stream can report a gap or an overflow at all."""
    if not isinstance(vendor, str):
        return False
    return vendor.strip().casefold() in GAP_VENDORS


def cursor_kind_for(vendor: Any) -> str | None:
    """The named cursor a vendor's overflow resumes from, or ``None`` if none.

    The two answers are not the same shape, which is the reason this is a function
    rather than a lookup the caller writes inline.
    """
    if not isinstance(vendor, str):
        return None
    folded = vendor.strip().casefold()
    if folded == "salesforce":
        return "replay_id"
    if folded == "dataverse":
        return "delta_link"
    return None


def exceeds_overflow_threshold(change_count: Any) -> bool:
    """Whether a header's change count is past the researched threshold.

    ``False`` for anything that is not a number, because a header that does not
    report a count is a header that did not overflow. The threshold is read from
    :data:`NUMBERS` rather than written into this condition, which is the whole
    reason it is a named constant.
    """
    if isinstance(change_count, bool) or change_count is None:
        return False
    try:
        count = int(change_count)
    except (TypeError, ValueError):
        return False
    return count > int(NUMBERS["overflow_change_threshold"]["value"])


def describe() -> dict[str, Any]:
    """Every vocabulary this package enforces, served as data."""
    return {
        "gap_change_types": list(GAP_CHANGE_TYPES),
        "overflow_change_type": OVERFLOW_CHANGE_TYPE,
        "change_types": list(CHANGE_TYPES),
        "gap_operations": dict(GAP_OPERATION),
        "vendors": list(GAP_VENDORS),
        "unsupported_vendors": list(UNSUPPORTED_GAP_VENDORS),
        "unsupported_vendor_quote": UNSUPPORTED_VENDOR_QUOTE,
        "cursor_kinds": [
            {
                "kind": kind,
                "meaning": meaning,
                "scope": CURSOR_SCOPE[kind],
                "fields": list(CURSOR_FIELDS),
            }
            for kind, meaning in CURSOR_KINDS.items()
        ],
        "dirty_states": list(DIRTY_STATES),
        "dirty_key": list(DIRTY_KEY),
        "run_states": [
            {"value": state, "terminal": state in TERMINAL_RUN_STATES} for state in RUN_STATES
        ],
        "deleted_sources": [
            {
                "value": value,
                "meaning": meaning,
                "vendor": DELETED_SOURCE_VENDOR[value] or "any",
            }
            for value, meaning in DELETED_SOURCES.items()
        ],
        "log_events": list(LOG_EVENTS),
        "numbers": {name: dict(entry) for name, entry in NUMBERS.items()},
        "last_modified_field": LAST_MODIFIED_FIELD,
        "classification": (
            "a gap event names one record and is repaired by one read; an overflow "
            "event names no record and is repaired by a whole-entity read plus a "
            "delete diff"
        ),
    }
