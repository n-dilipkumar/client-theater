"""Every researched word WF-102 enforces against, in one place.

The research is ``docs/research/raw/quoting-proposals.md`` section 18, quoted in
full in issue 196. Everything below is either a sentence from that research, a
value the issue states exactly (the two ``quotecreationmethod`` option numbers),
or a status this workflow reuses from an already-merged ticket rather than
re-declaring.

Reuse rather than re-declaration
--------------------------------

WF-086 (author a quote) merged first and owns the ``wf086_quote`` and
``wf086_line_item`` collections and the ``draft`` / ``published`` status
spellings. WF-099 (contract from an accepted quote) reads the same quote row.
Issue 196 says to reuse both rather than invent a second quote vocabulary, so
the collection names and the two open statuses are imported from
:mod:`dsr.quote_authoring.vocabulary` and only the values this workflow adds are
declared here. A revision that spelled the collection ``quote`` would create a
second, invisible quote universe beside the one two merged tickets already read.

The three close reasons
-----------------------

The research states the close choices exactly: "When you close a quote, you have
three choices: Lost, Canceled, or Revised." They are one enumeration and not
three features, because the same ``quoteclose`` activity is written for all three
and the same ``closedon`` field is stamped. :data:`CLOSE_REASONS` is that
enumeration, in the researcher's order.
"""

from __future__ import annotations

from typing import Any

from dsr.quote_authoring.vocabulary import (  # noqa: F401  (re-exported on purpose)
    LINE_ITEM_COLLECTION,
    QUOTE_COLLECTION,
    STATUS_DRAFT as DRAFT,
    STATUS_PUBLISHED as PUBLISHED,
)

# --------------------------------------------------------------------------- #
# Provenance: the import above is the reuse. Nothing is re-declared here.
# --------------------------------------------------------------------------- #

TICKET = "WF-102"

# --------------------------------------------------------------------------- #
# Collections
# --------------------------------------------------------------------------- #

#: The ``quoteclose`` activity stream and the recall / re-publish trail. This
#: workflow owns it: the research names a ``quoteclose`` activity and the
#: HubSpot quote activity log, and no merged ticket provisions either.
ACTIVITY_COLLECTION = "wf102_quote_activity"

#: The re-generated share link and PDF. The research says re-publish
#: "re-generates the link/PDF", so an artefact is a fresh row rather than an
#: edit of the one the recall invalidated.
ARTEFACT_COLLECTION = "wf102_quote_artefact"

# --------------------------------------------------------------------------- #
# Status
# --------------------------------------------------------------------------- #

#: A status a quote still holds while it can be edited, revised or closed.
OPEN_STATUSES: tuple[str, ...] = (DRAFT, PUBLISHED)

#: The three close reasons, written as statuses. Closing a quote sets its
#: ``status`` to the reason, so one word names both the close and the state.
REVISED = "revised"
LOST = "lost"
CANCELED = "canceled"

CLOSED_STATUSES: tuple[str, ...] = (REVISED, LOST, CANCELED)

#: The three choices the research names, in its own order. "Lost, Canceled, or
#: Revised."
CLOSE_REASONS: tuple[str, ...] = (LOST, CANCELED, REVISED)

ALL_STATUSES: tuple[str, ...] = OPEN_STATUSES + CLOSED_STATUSES

STATUS_LABELS: dict[str, str] = {
    DRAFT: "Draft",
    PUBLISHED: "Active",
    REVISED: "Revised",
    LOST: "Lost",
    CANCELED: "Canceled",
}

# --------------------------------------------------------------------------- #
# quotecreationmethod
# --------------------------------------------------------------------------- #

#: The two option values the issue records exactly. They are kept as named
#: constants because the issue says so, and because an integer literal at a use
#: site is a value the page cannot label.
QUOTE_CREATION_METHOD_UNKNOWN = 776160000
QUOTE_CREATION_METHOD_REVISION = 776160001

QUOTE_CREATION_METHODS: dict[int, str] = {
    QUOTE_CREATION_METHOD_UNKNOWN: "Unknown",
    QUOTE_CREATION_METHOD_REVISION: "Revision",
}

# --------------------------------------------------------------------------- #
# Fields
# --------------------------------------------------------------------------- #

#: The research's own property names, kept as it spells them so a reader of the
#: vendor documentation finds the same word here.
FIELD_QUOTE_CREATION_METHOD = "quotecreationmethod"
FIELD_CLOSED_ON = "closedon"
FIELD_REVISION_NUMBER = "revision_number"
FIELD_STATUS_REASON = "status_reason"

#: The lineage. The research calls revisions "a first-class record lineage", so
#: both ends of a revision are written: the new draft names the record it
#: replaced and the closed record names its successor. The chain is then a walk
#: in either direction rather than a search over the collection.
FIELD_ROOT_QUOTE_ID = "root_quote_id"
FIELD_SUPERSEDES = "supersedes_quote_id"
FIELD_SUPERSEDED_BY = "superseded_by_quote_id"

#: Set when a quote is created by Clone rather than by Revise. A clone is a new
#: quote with its own lineage; this field is what lets a reader tell the two
#: apart. See :data:`CLONE_IS_NOT_A_REVISION`.
FIELD_CLONED_FROM = "cloned_from_quote_id"

#: Read as a fallback so a quote written against the research's word "revision
#: ID" is read the same as one written against this workflow's field.
REVISION_NUMBER_ALIASES: tuple[str, ...] = ("revision_number", "revision_id", "revisionnumber")

# --------------------------------------------------------------------------- #
# Activities
# --------------------------------------------------------------------------- #

ACTIVITY_QUOTE_CLOSE = "quote_close"
ACTIVITY_QUOTE_RECALLED = "quote_recalled"
ACTIVITY_QUOTE_REPUBLISHED = "quote_republished"

ACTIVITY_ACTIONS: tuple[str, ...] = (
    ACTIVITY_QUOTE_CLOSE,
    ACTIVITY_QUOTE_RECALLED,
    ACTIVITY_QUOTE_REPUBLISHED,
)

ACTIVITY_LABELS: dict[str, str] = {
    ACTIVITY_QUOTE_CLOSE: "Quote closed",
    ACTIVITY_QUOTE_RECALLED: "Quote recalled",
    ACTIVITY_QUOTE_REPUBLISHED: "Quote re-published",
}

#: The event the research fixes on the revise action: "ReviseQuote ...
#: Event: False". CloseQuote is "Event: True". Recorded here because it decides
#: whether the revise write produces an event row: the revise action itself does
#: not, the close it triggers does.
REVISE_IS_AN_EVENT = False
CLOSE_IS_AN_EVENT = True
EVENT_POLICY = {
    "revise": REVISE_IS_AN_EVENT,
    "close": CLOSE_IS_AN_EVENT,
    "rationale": (
        "The research records ReviseQuote as Event: False and CloseQuote as Event: True. "
        "A revise therefore writes no event row of its own; the close it triggers writes "
        "the quoteclose activity, which is the row an event consumer reads."
    ),
}

# --------------------------------------------------------------------------- #
# The recall gate
# --------------------------------------------------------------------------- #

#: HubSpot recall is "blocked once approved, accepted or paid". These are the
#: states that block it.
RECALL_BLOCKED_STATES: tuple[str, ...] = ("approved", "accepted", "paid")

#: The fields a caller may have used to record one of those states. Read as
#: synonyms because the merged tickets spell the payment status differently
#: (WF-095 writes ``hs_payment_status``).
RECALL_STATE_FIELDS: tuple[str, ...] = (
    "hs_status",
    "quote_status",
    "status_reason",
    "payment_status",
    "hs_payment_status",
)

#: "published quotes with online payments or e-signature enabled can't be
#: restored". Both flags block the recall-edit-restore path.
ONLINE_PAYMENTS_FIELD = "online_payments_enabled"
ESIGNATURE_FIELD = "esignature_enabled"

# --------------------------------------------------------------------------- #
# Scope
# --------------------------------------------------------------------------- #

#: "A project quote doesn't support those processes." Activation and revision
#: are sales-quote processes, so a project quote is refused rather than silently
#: revised.
SALES_QUOTE_SCOPE = "sales_quote"
PROJECT_QUOTE_SCOPE = "project_quote"
QUOTE_SCOPE_FIELDS: tuple[str, ...] = (
    "quote_scope",
    "quote_kind",
    "quote_type",
    "project_quote",
)

# --------------------------------------------------------------------------- #
# Refusal reasons
# --------------------------------------------------------------------------- #

REASON_QUOTE_ALREADY_CLOSED = "quote_already_closed"
REASON_CLOSE_REASON_UNKNOWN = "close_reason_unknown"
REASON_PROJECT_QUOTE_UNSUPPORTED = "project_quote_unsupported"
REASON_QUOTE_FROZEN = "quote_frozen"
REASON_QUOTE_NOT_PUBLISHED = "quote_not_published"
REASON_RECALL_BLOCKED_AFTER_APPROVAL = "recall_blocked_after_approval"
REASON_RECALL_BLOCKED_NO_RESTORE = "recall_blocked_no_restore"
REASON_REPUBLISH_NEEDS_A_RECALL = "republish_needs_a_recall"
REASON_QUOTE_IS_EMPTY = "quote_is_empty"

REFUSAL_REASONS: dict[str, str] = {
    REASON_QUOTE_ALREADY_CLOSED: (
        "This quote is closed. Revise or clone it to start a new version instead of "
        "changing a closed record."
    ),
    REASON_CLOSE_REASON_UNKNOWN: "Close a quote as Lost, Canceled or Revised.",
    REASON_PROJECT_QUOTE_UNSUPPORTED: (
        "A project quote does not support activation and revision. This workflow handles "
        "sales quotes only."
    ),
    REASON_QUOTE_FROZEN: (
        "This quote is not a draft, so its prices are frozen and are not adjusted to the "
        "price list."
    ),
    REASON_QUOTE_NOT_PUBLISHED: "Only an active quote can be recalled.",
    REASON_RECALL_BLOCKED_AFTER_APPROVAL: (
        "This quote is approved, accepted or paid, so it cannot be recalled."
    ),
    REASON_RECALL_BLOCKED_NO_RESTORE: (
        "This quote has online payments or e-signature enabled, so it cannot be restored "
        "or recalled."
    ),
    REASON_REPUBLISH_NEEDS_A_RECALL: ("Only a quoted recalled back to draft can be re-published."),
    REASON_QUOTE_IS_EMPTY: "A quote with no line items cannot be revised.",
}

# --------------------------------------------------------------------------- #
# Evidence, quoted
# --------------------------------------------------------------------------- #

EVIDENCE: dict[str, str] = {
    "draft_revision_zero": (
        "When you create a new quote, it is automatically set to Draft, and the revision ID "
        "is set to 0."
    ),
    "revise_creates_a_new_quote": (
        "When you close a quote, you have three choices: Lost, Canceled, or Revised. If you "
        "revise it, a new quote is created, set to Draft status, and you can start over."
    ),
    "revisequote_action": "ReviseQuote\nAddress: ReviseQuoteRequest\nEvent: False",
    "closequote_action": "CloseQuote\nAddress: CloseQuoteRequest\nEvent: True",
    "quotecreationmethod": ("quotecreationmethod\n776160000 Unknown\n776160001 Revision"),
    "closedon": (
        "closedon: Enter the date when the quote was closed to indicate the expiration, "
        "revision, or cancellation date."
    ),
    "draft_pricing_is_live": (
        "In the draft state, the prices are adjusted to the actual prices on the price list "
        "when you open or edit the quote."
    ),
    "closed_pricing_is_frozen": (
        "In the active and closed states, the prices aren't adjusted to the actual prices on "
        "the price list when you open or edit the quote."
    ),
    "clone_copies_line_items": (
        "Cloned quotes include the original quote's record associations and the property "
        "values of the line items, including price, terms, and discount"
    ),
    "recall_is_blocked": "blocked once approved, accepted or paid",
    "restore_is_blocked": (
        "published quotes with online payments or e-signature enabled can't be restored"
    ),
    "republish_regenerates": "re-publish re-generates the link/PDF",
    "quote_recalled_activity": "Quote recalled",
    "project_quote_scope": "A project quote doesn't support those processes.",
}

#: What this workflow decided rather than sourced. Recorded so a reader can tell
#: a researched rule from an implementation choice.
INFERENCES: dict[str, str] = {
    "clone_shares_the_line_copy": (
        "Clone and revise share one line-item copy helper and nothing else. Revise also "
        "closes the prior record and reprices the new draft; clone leaves the prior record "
        "open and copies the line-item price, terms and discount exactly as the research "
        "says. The two are tested apart so the difference stays visible."
    ),
    "activity_row_mirrors_quoteclose": (
        "The research names a quoteclose activity and does not name its shape. This workflow "
        "writes one activity collection for close, recall and re-publish, with an action "
        "token, the reason, the revision number and the timestamp. A single stream is what "
        "makes a deal's history readable in one place."
    ),
    "closed_on_is_a_date": (
        "closedon is a date, not a timestamp: the research says it indicates the expiration, "
        "revision or cancellation date. It is written for every close reason."
    ),
}

#: What the research records and this workflow deliberately does not build.
NOT_IMPLEMENTED: list[dict[str, str]] = [
    {
        "item": "Win the quote (WinQuote)",
        "source": (
            "The issue names WinQuote alongside ReviseQuote and CloseQuote as an API the "
            "spec touches."
        ),
        "decision": (
            "Not built. Winning a quote creates an order and is WF-101's surface; this ticket "
            "revises a quote, and a revise that also won the deal would merge two lifecycles."
        ),
    },
    {
        "item": "Project Operations activation and revisions",
        "source": "A project quote doesn't support those processes.",
        "decision": (
            "Refused with project_quote_unsupported rather than modelled. The research states "
            "the boundary, so crossing it would be a claim the source does not support."
        ),
    },
    {
        "item": "Restoring a deleted or archived quote",
        "source": "published quotes with online payments or e-signature enabled can't be restored",
        "decision": (
            "The restore gate is encoded on the recall path, because both are the "
            "recall-edit-reissue route. A separate archive/restore lifecycle is not modelled."
        ),
    },
]


def vocabulary() -> dict[str, Any]:
    """Everything the page needs to label this workflow, and nothing it must guess."""

    return {
        "ticket": TICKET,
        "statuses": list(ALL_STATUSES),
        "status_labels": dict(STATUS_LABELS),
        "open_statuses": list(OPEN_STATUSES),
        "closed_statuses": list(CLOSED_STATUSES),
        "close_reasons": list(CLOSE_REASONS),
        "quote_creation_methods": {str(k): v for k, v in QUOTE_CREATION_METHODS.items()},
        "revision_starts_at": 0,
        "fields": {
            "status": "status",
            "status_reason": FIELD_STATUS_REASON,
            "quote_creation_method": FIELD_QUOTE_CREATION_METHOD,
            "closed_on": FIELD_CLOSED_ON,
            "revision_number": FIELD_REVISION_NUMBER,
            "root_quote_id": FIELD_ROOT_QUOTE_ID,
            "supersedes": FIELD_SUPERSEDES,
            "superseded_by": FIELD_SUPERSEDED_BY,
            "cloned_from": FIELD_CLONED_FROM,
        },
        "activities": list(ACTIVITY_ACTIONS),
        "activity_labels": dict(ACTIVITY_LABELS),
        "recall": {
            "blocked_states": list(RECALL_BLOCKED_STATES),
            "online_payments_field": ONLINE_PAYMENTS_FIELD,
            "esignature_field": ESIGNATURE_FIELD,
        },
        "scope": {
            "sales_quote": SALES_QUOTE_SCOPE,
            "project_quote": PROJECT_QUOTE_SCOPE,
            "fields": list(QUOTE_SCOPE_FIELDS),
        },
        "event_policy": dict(EVENT_POLICY),
        "refusal_reasons": dict(REFUSAL_REASONS),
        "evidence": dict(EVIDENCE),
        "inferences": dict(INFERENCES),
        "not_implemented": [dict(entry) for entry in NOT_IMPLEMENTED],
        "collections": {
            "quote": QUOTE_COLLECTION,
            "line_item": LINE_ITEM_COLLECTION,
            "activity": ACTIVITY_COLLECTION,
            "artefact": ARTEFACT_COLLECTION,
        },
    }
