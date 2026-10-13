"""The vocabulary and the sourced rules of WF-115.

Everything a reader needs to check a claim in this feature against the research
it came from lives here, so the page and the tests render the rules rather than
restating them. The spec was quoted into the GitHub issue in full, and where the
spec is silent this module records the reading taken and the reason, marked as an
inference.

No framework and no store: this module imports nothing from ``dsr``.
"""

from __future__ import annotations

from typing import Any

# --------------------------------------------------------------------------- #
# Collections
# --------------------------------------------------------------------------- #

#: The authored module. One row per trigger or action, in any review state.
#: Its payload is ordinary JSON in ``records.data`` -- no migration, no typed
#: column -- so a builder can add a field without coordinating with anyone.
MODULES = "wf115_marketplace_module"

#: One immutable snapshot per submission. A submission carries a changelog and a
#: version number, which is what "version the module" requires.
VERSIONS = "wf115_marketplace_version"

#: One row per review action or lifecycle transition, so the review history is
#: auditable as a sequence rather than as a single overwritten field.
REVIEWS = "wf115_marketplace_review"

#: One row per subscription delivery. The platform POSTs trigger configuration
#: to the builder's Subscription URL; the room records that inbound call here.
DELIVERIES = "wf115_marketplace_delivery"

COLLECTIONS: dict[str, str] = {
    "modules": MODULES,
    "versions": VERSIONS,
    "reviews": REVIEWS,
    "deliveries": DELIVERIES,
}

# --------------------------------------------------------------------------- #
# Kinds
# --------------------------------------------------------------------------- #

KIND_TRIGGER = "trigger"
KIND_ACTION = "action"
KINDS: tuple[str, ...] = (KIND_TRIGGER, KIND_ACTION)

# --------------------------------------------------------------------------- #
# The review state machine
#
# The spec names the review step but does not define its states. The reading
# taken here is recorded on ``/api/wf-115/decisions``. Chosen by Jev
# (audit jev-20261013T183143-14096-03213, confidence 0.94).
# --------------------------------------------------------------------------- #

#: Authored but not submitted. Implied by "Submit for Review": there is a state
#: before the submission.
STATE_DRAFT = "draft"

#: Submitted for review with a changelog, awaiting a reviewer.
STATE_SUBMITTED = "submitted"

#: A reviewer has picked the submission up. This is the state the spec calls
#: "review" ("HighLevel Marketplace review").
STATE_IN_REVIEW = "in_review"

#: The reviewer sent it back. Inference: a reviewer needs a non-approval outcome
#: that is not a terminal refusal, because the builder must be able to fix and
#: resubmit.
STATE_CHANGES_REQUESTED = "changes_requested"

#: "Once approved, the version submitted for review will be published live."
STATE_APPROVED = "approved"

#: Live to every sub-account: "published trigger appears in every sub-account's
#: add a trigger menu".
STATE_PUBLISHED = "published"

#: The builder pulled the submission back before it was decided. Inference.
STATE_WITHDRAWN = "withdrawn"

#: The reviewer's terminal refusal. Inference: distinct from
#: ``changes_requested``, which invites a resubmission.
STATE_REJECTED = "rejected"

#: Never stored. A module whose row is soft-deleted reads as ``deleted``; the
#: row and its key survive so a workflow can still be told the trigger was
#: deleted ("Any workflows using the deleted trigger will skip its execution").
STATE_DELETED = "deleted"

#: The eight states a live module can hold.
STORED_STATES: tuple[str, ...] = (
    STATE_DRAFT,
    STATE_SUBMITTED,
    STATE_IN_REVIEW,
    STATE_CHANGES_REQUESTED,
    STATE_APPROVED,
    STATE_PUBLISHED,
    STATE_WITHDRAWN,
    STATE_REJECTED,
)

#: Every state a client may see, including the derived one.
REVIEW_STATES: tuple[str, ...] = (*STORED_STATES, STATE_DELETED)

#: Which target states each source state may move to. ``deleted`` is listed here
#: and is reached through the soft-delete route, not through a review decision.
TRANSITIONS: dict[str, tuple[str, ...]] = {
    STATE_DRAFT: (STATE_SUBMITTED, STATE_DELETED),
    STATE_SUBMITTED: (
        STATE_IN_REVIEW,
        STATE_APPROVED,
        STATE_CHANGES_REQUESTED,
        STATE_REJECTED,
        STATE_WITHDRAWN,
        STATE_DELETED,
    ),
    STATE_IN_REVIEW: (
        STATE_APPROVED,
        STATE_CHANGES_REQUESTED,
        STATE_REJECTED,
        STATE_WITHDRAWN,
        STATE_DELETED,
    ),
    STATE_CHANGES_REQUESTED: (STATE_SUBMITTED, STATE_WITHDRAWN, STATE_DELETED),
    STATE_APPROVED: (STATE_PUBLISHED, STATE_WITHDRAWN, STATE_DELETED),
    # A published module may be withdrawn (unpublished) so a builder can edit it,
    # or deleted. Withdrawal is the reversible route back to editing.
    STATE_PUBLISHED: (STATE_WITHDRAWN, STATE_DELETED),
    STATE_WITHDRAWN: (STATE_SUBMITTED, STATE_DELETED),
    STATE_REJECTED: (STATE_SUBMITTED, STATE_DELETED),
}

#: The states a module may be submitted from. Read off ``TRANSITIONS`` so the
#: two can never disagree.
SUBMITTABLE_STATES: tuple[str, ...] = tuple(
    state for state, targets in TRANSITIONS.items() if STATE_SUBMITTED in targets
)

#: The states that may still be withdrawn from.
WITHDRAWABLE_STATES: tuple[str, ...] = tuple(
    state for state, targets in TRANSITIONS.items() if STATE_WITHDRAWN in targets
)

#: The review decisions a reviewer can take, mapped to the state they move to.
REVIEW_DECISIONS: dict[str, str] = {
    "start_review": STATE_IN_REVIEW,
    "approve": STATE_APPROVED,
    "request_changes": STATE_CHANGES_REQUESTED,
    "reject": STATE_REJECTED,
}

#: The decisions that must carry a note. "Start review" and "approve" may omit
#: one; a send-back or a refusal without a reason helps nobody.
DECISIONS_REQUIRING_NOTE: tuple[str, ...] = ("request_changes", "reject")

# --------------------------------------------------------------------------- #
# Subscription events and the filter vocabulary
# --------------------------------------------------------------------------- #

#: The three events the platform sends to the builder's Subscription URL when a
#: trigger's configuration is created, updated, or deleted in a workflow.
EVENT_CREATED = "CREATED"
EVENT_UPDATED = "UPDATED"
EVENT_DELETED = "DELETED"
EVENT_TYPES: tuple[str, ...] = (EVENT_CREATED, EVENT_UPDATED, EVENT_DELETED)

#: Filter field types. String / Select / Multi-Select / Dynamic.
FILTER_STRING = "String"
FILTER_SELECT = "Select"
FILTER_MULTI_SELECT = "Multi-Select"
FILTER_DYNAMIC = "Dynamic"
FILTER_TYPES: tuple[str, ...] = (
    FILTER_STRING,
    FILTER_SELECT,
    FILTER_MULTI_SELECT,
    FILTER_DYNAMIC,
)

#: Where a Select or Multi-Select filter's options come from.
OPTION_SOURCES: dict[str, str] = {
    "constants": "Constants",
    "internal_reference": "Internal Reference",
    "external_api": "External API GET",
}

#: "Only one Dynamic type can be created per trigger" / "per action". Enforced
#: as a validation error.
DYNAMIC_FILTER_LIMIT = 1

#: How an action executes. ``custom_code`` is recorded but never run: the spec
#: does not say the room runs third-party JavaScript, so the room does not.
EXECUTION_URL = "url"
EXECUTION_CUSTOM_CODE = "custom_code"
EXECUTION_METHODS: tuple[str, ...] = (EXECUTION_URL, EXECUTION_CUSTOM_CODE)

#: Distribution Type: Agency vs Sub-Account.
DISTRIBUTIONS: tuple[str, ...] = ("agency", "sub_account")

#: The immutable key: a unique identifier used to reference the module within
#: workflows. Lowercase, starts with a letter, underscores allowed.
KEY_PATTERN = r"^[a-z][a-z0-9_]{2,63}$"

#: The key field is immutable. An edit that changes it is refused.
IMMUTABLE_FIELDS: tuple[str, ...] = ("key", "kind")

# --------------------------------------------------------------------------- #
# Where the research left a gap
# --------------------------------------------------------------------------- #

#: The decisions this build recorded, with the audit id where Jev decided and a
#: plain reading where the evidence did not warrant a gate. Served over HTTP so
#: a reviewer reads the reasoning next to the code that enforces it.
DECISIONS: tuple[dict[str, Any], ...] = (
    {
        "id": "third_party_endpoints",
        "decision": (
            "The Subscription URL, Dynamic filter URL and Action execution URL are "
            "third-party endpoints the room does not own. The room records them as "
            "builder configuration in the module payload and never calls them. One "
            "route models the inbound direction: it records and evaluates what the "
            "platform POSTs to the Subscription URL."
        ),
        "alternative_rejected": (
            "The room calling the third-party URLs itself. Rejected because the spec "
            "places those calls on the marketplace and the app, not on the room, and "
            "because an outbound caller would make the test suite and the demo seed "
            "depend on live third-party services."
        ),
        "gate": "option",
        "verdict": "pass",
        "selected": "option_a",
        "confidence": 1.0,
        "audit_id": "jev-20261013T183138-8840-98712",
    },
    {
        "id": "review_state_machine",
        "decision": (
            "The review step is modelled as eight stored states (draft, submitted, "
            "in_review, changes_requested, approved, published, withdrawn, rejected) "
            "with deleted derived from the soft-delete timestamp. Submission carries "
            "a changelog and increments a version."
        ),
        "alternative_rejected": (
            "A five-state machine (draft, submitted, approved, published, deleted). "
            "Rejected because it gives a reviewer no send-back outcome and cannot "
            "show a builder pulling a submission back."
        ),
        "gate": "confidence",
        "verdict": "pass",
        "selected": "option_a",
        "confidence": 0.94,
        "audit_id": "jev-20261013T183143-14096-03213",
    },
    {
        "id": "soft_delete",
        "decision": (
            "Deletion is a soft delete. The row and its immutable key survive, and a "
            "deleted module reads as state 'deleted' to a client that asks for it. "
            "This is what lets a workflow be told that a deleted trigger will skip "
            "its execution."
        ),
        "alternative_rejected": "A hard delete, which would lose the key a workflow still references.",
        "gate": "derived",
        "verdict": "recorded",
        "selected": "soft_delete",
        "confidence": None,
        "audit_id": None,
    },
    {
        "id": "custom_code_out_of_scope",
        "decision": (
            "Action custom code (JS) is recorded as the action's declared execution "
            "method and is never executed by the room. The response always reports "
            "runs_custom_code false."
        ),
        "alternative_rejected": (
            "Running builder JavaScript in the room. Rejected because the spec names "
            "custom code only in an extensibility line and never in the data flow, "
            "and the room has no sandbox for third-party code."
        ),
        "gate": "derived",
        "verdict": "recorded",
        "selected": "record_only",
        "confidence": None,
        "audit_id": None,
    },
    {
        "id": "immutable_key",
        "decision": (
            "The key is immutable. An edit whose payload carries a different key is "
            "refused with 409, because the key is what a workflow references and a "
            "mutable key would silently repoint every workflow that used it."
        ),
        "alternative_rejected": "Allowing a key change and rewriting references. Rejected: the room cannot reach a third party's workflows to rewrite them.",
        "gate": "derived",
        "verdict": "recorded",
        "selected": "refuse_the_edit",
        "confidence": None,
        "audit_id": None,
    },
    {
        "id": "dynamic_filter_limit",
        "decision": (
            "At most one Dynamic filter per module is allowed, for a trigger and for "
            "an action alike."
        ),
        "alternative_rejected": "Silently keeping the first Dynamic filter and dropping the rest, which would hide a builder's mistake.",
        "gate": "derived",
        "verdict": "recorded",
        "selected": "refuse_the_second",
        "confidence": None,
        "audit_id": None,
        "source_quote": "Only one Dynamic type can be created per trigger.",
    },
)

#: The sources the issue quotes, reproduced verbatim. Two of the six entries are
#: the same URL and the last carries a stray trailing backtick. That is how the
#: spec writes the block; it is a quotation and is not corrected here.
SOURCES: tuple[str, ...] = (
    "https://marketplace.gohighlevel.com/docs/marketplace-modules/CustomActions/",
    "https://marketplace.gohighlevel.com/docs/marketplace-modules/CustomTriggers/",
    "https://marketplace.gohighlevel.com/docs/marketplace-modules/Snapshots",
    "https://marketplace.gohighlevel.com/docs/marketplace-modules/WorkflowActionsAndTriggers/",
    "https://services.leadconnectorhq.com/workflows-marketplace/triggers/execute/abc/def",
    "https://services.leadconnectorhq.com/workflows-marketplace/triggers/execute/abc/def`",
)

#: The evidence quotes the module is built from, so a reviewer checks the rules
#: against the sentences they came from.
EVIDENCE: tuple[dict[str, str], ...] = (
    {
        "claim": "The key is immutable and references the trigger inside workflows.",
        "quote": (
            "Assign a unique identifier (e.g., mycustomtrigger). This key is "
            "immutable and used to reference the trigger within workflows."
        ),
    },
    {
        "claim": "The Subscription URL receives the trigger configuration on every change.",
        "quote": (
            "The Subscription URL is an API endpoint that receives trigger "
            "configuration details whenever the trigger is created, updated, or "
            "deleted in a workflow."
        ),
    },
    {
        "claim": "A trigger fires through a target URL where the app signals satisfaction.",
        "quote": '"targetUrl": "https://services.leadconnectorhq.com/workflows-marketplace/triggers/execute/abc/def"',
    },
    {
        "claim": "The published version is live to every sub-account.",
        "quote": (
            "Once approved, the version submitted for review will be published live "
            "to all Sub-accounts."
        ),
    },
    {
        "claim": "A deleted trigger is skipped, not silently re-run.",
        "quote": "Any workflows using the deleted trigger will skip its execution.",
    },
)


def vocabulary() -> dict[str, Any]:
    """The rules this feature enforces, readable without a store."""
    return {
        "ticket": "WF-115",
        "kinds": list(KINDS),
        "collections": dict(COLLECTIONS),
        "review_states": list(REVIEW_STATES),
        "stored_states": list(STORED_STATES),
        "deleted_state": STATE_DELETED,
        "transitions": {state: list(targets) for state, targets in TRANSITIONS.items()},
        "submittable_states": list(SUBMITTABLE_STATES),
        "withdrawable_states": list(WITHDRAWABLE_STATES),
        "review_decisions": dict(REVIEW_DECISIONS),
        "decisions_requiring_note": list(DECISIONS_REQUIRING_NOTE),
        "event_types": list(EVENT_TYPES),
        "filter_types": list(FILTER_TYPES),
        "option_sources": dict(OPTION_SOURCES),
        "dynamic_filter_limit": DYNAMIC_FILTER_LIMIT,
        "execution_methods": list(EXECUTION_METHODS),
        "distributions": list(DISTRIBUTIONS),
        "key_pattern": KEY_PATTERN,
        "immutable_fields": list(IMMUTABLE_FIELDS),
        "decisions": [dict(item) for item in DECISIONS],
        "sources": list(SOURCES),
        "evidence": [dict(item) for item in EVIDENCE],
    }


__all__ = [
    "COLLECTIONS",
    "DECISIONS",
    "DECISIONS_REQUIRING_NOTE",
    "DELIVERIES",
    "DYNAMIC_FILTER_LIMIT",
    "EVIDENCE",
    "EXECUTION_CUSTOM_CODE",
    "EXECUTION_METHODS",
    "EXECUTION_URL",
    "EVENT_CREATED",
    "EVENT_DELETED",
    "EVENT_TYPES",
    "EVENT_UPDATED",
    "FILTER_DYNAMIC",
    "FILTER_MULTI_SELECT",
    "FILTER_SELECT",
    "FILTER_STRING",
    "FILTER_TYPES",
    "IMMUTABLE_FIELDS",
    "KINDS",
    "KIND_ACTION",
    "KIND_TRIGGER",
    "KEY_PATTERN",
    "MODULES",
    "OPTION_SOURCES",
    "REVIEWS",
    "REVIEW_DECISIONS",
    "REVIEW_STATES",
    "SOURCES",
    "STATE_APPROVED",
    "STATE_CHANGES_REQUESTED",
    "STATE_DELETED",
    "STATE_DRAFT",
    "STATE_IN_REVIEW",
    "STATE_PUBLISHED",
    "STATE_REJECTED",
    "STATE_SUBMITTED",
    "STATE_WITHDRAWN",
    "STORED_STATES",
    "SUBMITTABLE_STATES",
    "TRANSITIONS",
    "VERSIONS",
    "WITHDRAWABLE_STATES",
    "vocabulary",
]
