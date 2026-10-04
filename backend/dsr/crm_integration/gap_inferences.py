"""Every judgement call WF-050 rests on, named and served.

The research fixes the flow, the two event shapes, the two timestamp comparisons,
the delete diff and the seven-day deadline. It leaves six things open, and this
module is where each is recorded rather than left for a reader to reconstruct from
a diff. It is served at the feature's ``/inferences`` route.

Each entry separates the *sourced* half from the *inferred* half, so the line
between "the vendor said this" and "this build decided this" stays visible.
"""

from __future__ import annotations

from typing import Any

# --------------------------------------------------------------------------- #
# The sourced half
# --------------------------------------------------------------------------- #

#: The sentence that governs the gap enumeration.
GAP_TYPES_QUOTE = (
    "The changeType field in the gap event header identifies the gap event and the "
    "associated operation, and can take one of these values: GAP_CREATE, "
    "GAP_UPDATE, GAP_DELETE, GAP_UNDELETE."
)

#: The sentence that governs the overflow, and the number that fixes its threshold.
OVERFLOW_QUOTE = (
    "Overflow events are generated when a single transaction involves more than "
    "100,000 changes. The first 100,000 changes generate change events. The set of "
    "changes beyond that amount generates one overflow event for each entity type "
    "included in that set."
)

#: The sentence that draws the line between the two shapes.
RECORD_ID_DIFFERENCE_QUOTE = (
    "Gap events don't contain record data, but they contain the record ID, which "
    "enables you to retrieve the record from Salesforce. Overflow events include "
    "header fields but no record data and no record ID."
)

#: The sentence that governs the dirty flag.
DIRTY_FLAG_QUOTE = (
    "For the gap event, mark the corresponding record as dirty locally as of the "
    "date of the gap event."
)

#: The sentence that governs the drop, and both comparisons it needs.
DROP_AND_ORDER_QUOTE = (
    "If you receive change events for new changes for the same record before the "
    "data has been reconciled, don't process them. To ensure that the change is "
    "after the gap event, compare the commitTimestamp fields of both events. To "
    "ensure that the change occurred before the data is reconciled, compare the "
    "LastModifiedDate fields on the change event and the record retrieved in the "
    "next step."
)

#: The sentence that governs the repair itself.
REPAIR_QUOTE = (
    "Reconcile the data for record C. Make a Salesforce API call, such as a REST API "
    "call, to retrieve the full data for record C, and save it in your system. Then "
    "clear the dirty flag on that record."
)

#: The overflow procedure, all five steps.
OVERFLOW_PROCEDURE_QUOTE = (
    "1. After you receive an overflow event in your subscriber, unsubscribe from "
    "the channel, and stop processing further events. 2. Store the Replay ID of the "
    "overflow event. This ID is the starting point for the data reconciliation. "
    "3. Reconcile the data for new, updated, and undeleted records. 4. Reconcile the "
    "data for deleted records by performing one of the following steps: a. Get the "
    "non-deleted records from Salesforce, and synchronize. b. Or get the deleted "
    "records from Salesforce, and synchronize."
)

#: The sentence that names the Recycle Bin as the deleted query.
RECYCLE_BIN_QUOTE = (
    "Query all records for the entity with isDeleted=true. You get all the "
    "soft-deleted records for that entity that are in the Recycle Bin."
)

#: The sentence that carries Dataverse's deletions inside the same cursor.
DATAVERSE_DELETES_QUOTE = (
    'The delta-link response surfaces deletes inline: {"@odata.context": '
    '".../accounts/$deletedEntity", "id": "2e451703-...", "reason": "deleted"}'
)

#: The sentence that fixes the deadline.
DEADLINE_QUOTE = (
    "Changes are returned if the last token is within a default value of seven days. "
    "If unprocessed changes are older than the configured value, the system throws an "
    "exception."
)

#: The sentence that names the resubscription and the audit line.
RESUBSCRIBE_QUOTE = (
    "Room resubscribes and records a reconciliation event in the sync log for audit."
)

#: The research's own recorded gap, carried here so it is not rediscovered.
RESEARCH_GAP_QUOTE = (
    "The overflow Replay ID mechanism is described for the Salesforce Pub/Sub/CometD "
    "subscriber model; the equivalent resumable-position primitive for Dataverse "
    "(delta link) is a different shape (opaque token, no per-entity replay ids) - the "
    "two were not reconciled in a single source."
)

# --------------------------------------------------------------------------- #
# The inferred half
# --------------------------------------------------------------------------- #

#: The key a dirty marker is stored under.
DIRTY_KEY_DECISION = {
    "id": "dirty-marker-key",
    "decision": "key_the_marker_by_room_entity_and_record",
    "question": (
        "The research says 'mark the corresponding record as dirty locally'. What is "
        "the key of that marker?"
    ),
    "chosen": "room_id, entity and record_id",
    "because": (
        "A room holds several buyers and a CRM id is unique per org, not per room, "
        "so a record id alone cannot identify a marker. The entity is in the key "
        "because an overflow emits one event per entity type and a dirty marker "
        "belongs to the entity whose stream missed the change."
    ),
    "reported_on": "the /dirty route, and every dirty marker row",
    "source": "docs/research/raw/crm-integration.md section 18, user_flow step 2",
}

#: Two cursors rather than one.
TWO_CURSORS = {
    "id": "two-named-cursors",
    "decision": "two_named_cursors",
    "question": (
        "Salesforce resumes an overflow from a Replay ID per entity type and "
        "Dataverse from one opaque delta link. Is that one cursor or two?"
    ),
    "chosen": "two, named replay_id and delta_link",
    "because": (
        "The research's own gaps section says the two 'were not reconciled in a "
        "single source' and are 'a different shape'. A single cursor field would "
        "have to pretend a Salesforce Replay ID is scoped to an entity and a "
        "Dataverse delta link is scoped to an entity, and the second is false: the "
        "delta link carries no per-entity position at all."
    ),
    "consequence": (
        "A room may hold both at once. The expiry test is kind-aware, because the "
        "seven-day sentence is about a Dataverse 'last token' and says nothing "
        "about a Replay ID."
    ),
    "source": "docs/research/raw/crm-integration.md section 18, gaps",
}

#: What happens at the seven-day deadline.
DEADLINE_FALLBACK = {
    "id": "expired-cursor-fallback",
    "decision": "fall_back_to_a_full_reread",
    "question": (
        "The research says the vendor throws when a token is too old, and calls "
        "seven days 'the hard deadline after which recovery must fall back to a "
        "full re-read'. What does the room actually do at the deadline?"
    ),
    "chosen": "report the cursor unresumable and reconcile by a full re-read",
    "because": (
        "The room refuses before it asks. Discovering the vendor's exception in the "
        "middle of a transaction would leave a run half applied, and the run is the "
        "audit unit. Reporting unresumable with the remedy named also means an "
        "operator reading the sync log can see why a run ended where it did."
    ),
    "recorded_on": "the run state expired_cursor, and a delta_link_expired log line",
    "source": "docs/research/raw/crm-integration.md section 18, automations",
}

#: HubSpot is out of scope.
VENDOR_SCOPE = {
    "id": "vendor-scope",
    "decision": "salesforce_and_dataverse_only",
    "question": ("The research covers three vendors. Does this workflow have a HubSpot gap path?"),
    "chosen": "no. A HubSpot gap or overflow is refused with the reason quoted.",
    "because": (
        "The research's gaps section states it: 'HubSpot has no documented "
        "gap/overflow analogue (it uses webhook redelivery instead)'. Inventing a "
        "mechanism for a vendor the source declines to describe would put an "
        "unevidenced behaviour in a recovery path, which is the one path where a "
        "wrong guess costs data."
    ),
    "reported_on": "the refusal message, and the /vocabulary route",
    "source": "docs/research/raw/crm-integration.md section 18, gaps",
}

#: The room holds the vendor's tables rather than calling them.
LOCAL_SOURCE = {
    "id": "local-vendor-tables",
    "decision": "local_vendor_tables",
    "question": "This product holds no OAuth connection. What does a re-read read?",
    "chosen": "the room's own copy of the vendor's tables, behind a reader seam",
    "because": (
        "Connecting an org is a different researched workflow, and a network call in "
        "a repair path is untestable and unsafe. The wire shapes stay the researched "
        "ones - a per-record REST read, an isDeleted=true query against the Recycle "
        "Bin, and Dataverse's delta response - so a room pointed at a live vendor is "
        "a change to one module and nothing above it."
    ),
    "seam": "dsr.crm_integration.gap_sources.CrmReader",
    "source": "docs/research/raw/crm-integration.md section 18, data_sources",
}

#: This feature repairs a replica it does not own.
REPLICA_OWNERSHIP = {
    "id": "replica-ownership",
    "decision": "own_a_namespaced_replica",
    "question": (
        "The research says to overwrite 'its replica'. The streaming replica "
        "collection is already owned by another shipped feature, and an enforced "
        "test refuses a second feature writing into it. Who owns the repaired rows?"
    ),
    "chosen": "WF-050 owns its own collection and writes into no other feature's",
    "because": (
        "Two features writing one collection is invisible to the route-collision "
        "check and is the exact defect the ownership test exists to prevent. A "
        "namespaced collection is also what WF-043 and WF-045 each already do, so "
        "this is the established pattern in this repository rather than a new one."
    ),
    "consequence": (
        "A room holds two partial replica views and a later surface merges them by "
        "the newer row. The research's extensibility note invites exactly that: 'A "
        "third party can add a clock skew detector ... the same primitive the docs "
        "use to decide whether a new change is older than the reconciliation.'"
    ),
    "rejected": [
        "overwrite the streaming replica in place",
        "call the streaming engine's replica writer",
    ],
    "source": "docs/design/WF-050-reconcile-gaps-after-a-dropped-change.md section 4",
}

INFERENCES: tuple[dict[str, Any], ...] = (
    DIRTY_KEY_DECISION,
    TWO_CURSORS,
    DEADLINE_FALLBACK,
    VENDOR_SCOPE,
    LOCAL_SOURCE,
    REPLICA_OWNERSHIP,
)


def by_id(entry_id: str) -> dict[str, Any] | None:
    """One entry of the register, by its ``id``, or ``None``.

    Every entry carries a short slug, so a page or a review can link a decision to
    one place rather than quoting a whole question string.
    """
    for entry in INFERENCES:
        if entry.get("id") == entry_id:
            return dict(entry)
    return None


def ids() -> tuple[str, ...]:
    """Every entry's slug, in register order."""
    return tuple(str(entry.get("id")) for entry in INFERENCES)


def describe() -> dict[str, Any]:
    """Every judgement call, with the sourced sentences it sits beside."""
    return {
        "sourced": {
            "gap_types": GAP_TYPES_QUOTE,
            "overflow": OVERFLOW_QUOTE,
            "record_id_difference": RECORD_ID_DIFFERENCE_QUOTE,
            "dirty_flag": DIRTY_FLAG_QUOTE,
            "drop_and_order": DROP_AND_ORDER_QUOTE,
            "repair": REPAIR_QUOTE,
            "overflow_procedure": OVERFLOW_PROCEDURE_QUOTE,
            "recycle_bin": RECYCLE_BIN_QUOTE,
            "dataverse_deletes": DATAVERSE_DELETES_QUOTE,
            "deadline": DEADLINE_QUOTE,
            "resubscribe": RESUBSCRIBE_QUOTE,
        },
        "research_gap": RESEARCH_GAP_QUOTE,
        "inferred": [dict(entry) for entry in INFERENCES],
        "ids": list(ids()),
        "count": len(INFERENCES),
    }


__all__ = [
    "DATAVERSE_DELETES_QUOTE",
    "DEADLINE_FALLBACK",
    "DEADLINE_QUOTE",
    "DIRTY_FLAG_QUOTE",
    "DIRTY_KEY_DECISION",
    "DROP_AND_ORDER_QUOTE",
    "GAP_TYPES_QUOTE",
    "INFERENCES",
    "LOCAL_SOURCE",
    "OVERFLOW_PROCEDURE_QUOTE",
    "OVERFLOW_QUOTE",
    "RECORD_ID_DIFFERENCE_QUOTE",
    "REPAIR_QUOTE",
    "REPLICA_OWNERSHIP",
    "RESEARCH_GAP_QUOTE",
    "RESUBSCRIBE_QUOTE",
    "RECYCLE_BIN_QUOTE",
    "TWO_CURSORS",
    "VENDOR_SCOPE",
    "by_id",
    "describe",
    "ids",
]
