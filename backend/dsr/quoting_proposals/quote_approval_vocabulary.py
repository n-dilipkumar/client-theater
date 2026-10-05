"""WF-091: every researched term for standard quote approval, quoted from the source.

The specification is ``docs/research/digital-sales-room-workflows/wf/WF-091.md``,
quoted in full in issue 179, and the underlying research is
``docs/research/raw/quoting-proposals.md`` section 6. Every constant below carries
the sentence it came from, because a number in a control that gates money that
nobody can trace to its source is a number somebody will change one day without
knowing why.

Nothing here reads or writes. These are the words, the caps and the evidence.

Where the specification left a joint open, the gap is recorded in
:mod:`dsr.quoting_proposals.quote_approval_inferences` rather than quietly filled
in here, so the difference between what the evidence says and what this build
chose stays readable.

This module is one of four for WF-091 and it is owned by WF-091 alone. The
package it lives in is shared with WF-088, WF-093 and WF-097, so nothing here
edits a module another ticket owns. It adds four files:
``quote_approval_vocabulary`` (this one), ``quote_approval_rules``,
``quote_approval_inferences`` and ``quote_approval_engine``.
"""

from __future__ import annotations

from typing import Any

# --------------------------------------------------------------------------- #
# Collections
# --------------------------------------------------------------------------- #
#
# Ticket-prefixed, which is the uniform convention in this repository. The host
# has no collection-collision check, so the prefix is what keeps two features
# from quietly sharing one.

#: A configured approval rule: the filters, the approvers and the requirement.
#:
#: One row per rule, because the flow is a builder that supports "+ Add filter"
#: more than once and an "Edit" beside the object. A rule is therefore an
#: addressable row with an on/off switch, not a list buried inside a settings blob.
APPROVAL_RULES = "wf091_approval_rule"

#: One enrolment. Created when a matching quote is submitted and released when an
#: approver decides.
#:
#: The data flow says "an approval record + note + notification is created", so the
#: enrolment is its own record rather than a field on the quote. A quote can be
#: submitted, rejected, edited and submitted again, and each of those is a
#: separate enrolment with its own decision history.
APPROVAL_REQUESTS = "wf091_approval_request"

#: One approver's vote on one enrolment. A row rather than a field, because the
#: research requires either "All approvers required" or "At least one approver
#: required", and counting votes is the whole of that requirement.
APPROVAL_DECISIONS = "wf091_approval_decision"

#: The quote activity log. "Quote approval requested", "Quote approval rejected /
#: requested changes" and "Quote approved" are the three names the research quotes,
#: and they are rows rather than log lines so a page can filter them.
ACTIVITIES = "wf091_activity"

#: The recorded notification. "a notification is created" and "bell/email
#: notification" are named, and the channels are named but not chosen. See
#: ``NOTIFICATION_CHANNELS``.
NOTIFICATIONS = "wf091_notification"

#: The quote this workflow reads as data. It writes no quote and owns no quote
#: record. See ``DERIVED_THE_QUOTE_IS_READ_AS_DATA`` in the inferences module.
SOURCE_QUOTES = "wf086_quote"

#: WF-094's publish and share state, read so a share attempt can be refused for a
#: quote that has not been approved. Named for the same reason.
SOURCE_SHARES = "wf094_share"


# --------------------------------------------------------------------------- #
# The properties a filter reads
# --------------------------------------------------------------------------- #
#
# "click **+ Add filter** -> **Filtering on** dropdown (quote, line item, deal,
# ...) -> **[Object] properties** -> search and pick a property (e.g. quote
# amount, discount level, SKU)". Three objects and any property, so the object is a
# stored field rather than a schema, and the property is a dotted JSON path
# resolved against the object the filter names.

FILTER_OBJECT_QUOTE = "quote"
FILTER_OBJECT_LINE_ITEM = "line_item"
FILTER_OBJECT_DEAL = "deal"

#: The three objects the research names in the **Filtering on** dropdown.
#:
#: A team may store more. Nothing here validates an object into this tuple, so a
#: rule that filters on a team-defined object works without a change to this
#: module. The tuple exists so the page can offer the three the research names.
FILTER_OBJECTS = (FILTER_OBJECT_QUOTE, FILTER_OBJECT_LINE_ITEM, FILTER_OBJECT_DEAL)

FILTER_OBJECT_LABELS: dict[str, str] = {
    FILTER_OBJECT_QUOTE: "Quote properties. Amount, discount level, owner, status.",
    FILTER_OBJECT_LINE_ITEM: "Line item properties. SKU, quantity, unit price, discount.",
    FILTER_OBJECT_DEAL: "Deal properties. Amount, stage, pipeline, close date.",
}


# --------------------------------------------------------------------------- #
# Filter operators
# --------------------------------------------------------------------------- #
#
# The flow says "configure the filter" and does not enumerate the operators. So the
# operators below are this build's set, chosen because they are the operators a
# property filter needs to express "above a certain discount amount" and the
# equality the same builder needs. They are named here, served on the page, and
# recorded as this build's choice rather than as sourced vocabulary. See
# ``DERIVED_FILTER_OPERATORS`` in the inferences module.

OPERATOR_IS = "is"
OPERATOR_IS_NOT = "is_not"
OPERATOR_GREATER_THAN = "gt"
OPERATOR_GREATER_THAN_OR_EQUAL = "gte"
OPERATOR_LESS_THAN = "lt"
OPERATOR_LESS_THAN_OR_EQUAL = "lte"
OPERATOR_CONTAINS = "contains"
OPERATOR_DOES_NOT_CONTAIN = "does_not_contain"
OPERATOR_IN = "in"
OPERATOR_NOT_IN = "not_in"

OPERATORS = (
    OPERATOR_IS,
    OPERATOR_IS_NOT,
    OPERATOR_GREATER_THAN,
    OPERATOR_GREATER_THAN_OR_EQUAL,
    OPERATOR_LESS_THAN,
    OPERATOR_LESS_THAN_OR_EQUAL,
    OPERATOR_CONTAINS,
    OPERATOR_DOES_NOT_CONTAIN,
    OPERATOR_IN,
    OPERATOR_NOT_IN,
)

OPERATOR_LABELS: dict[str, str] = {
    OPERATOR_IS: "is",
    OPERATOR_IS_NOT: "is not",
    OPERATOR_GREATER_THAN: "is greater than",
    OPERATOR_GREATER_THAN_OR_EQUAL: "is at least",
    OPERATOR_LESS_THAN: "is less than",
    OPERATOR_LESS_THAN_OR_EQUAL: "is at most",
    OPERATOR_CONTAINS: "contains",
    OPERATOR_DOES_NOT_CONTAIN: "does not contain",
    OPERATOR_IN: "is any of",
    OPERATOR_NOT_IN: "is none of",
}

#: The operators whose right-hand side is a list rather than one value. "is any of"
#: with a scalar would match nothing and read as a broken rule.
LIST_OPERATORS = (OPERATOR_IN, OPERATOR_NOT_IN)

#: The operators that compare numbers. The evidence names one that needs it: "Require
#: approval on quotes where a specific line item is above a certain discount amount."
NUMERIC_OPERATORS = (
    OPERATOR_GREATER_THAN,
    OPERATOR_GREATER_THAN_OR_EQUAL,
    OPERATOR_LESS_THAN,
    OPERATOR_LESS_THAN_OR_EQUAL,
)

FILTER_OPERATORS_CHOICE_NOTE = (
    "The research says 'configure the filter' and does not enumerate the operators. "
    "This set is this build's, chosen to express 'above a certain discount amount' "
    "and plain equality."
)


# --------------------------------------------------------------------------- #
# Filters and how several of them combine
# --------------------------------------------------------------------------- #
#
# "*And these conditions are met* click **+ Add filter** ... **+ Add filter** for
# additional criteria." The word in the source is and, so the filters combine with
# AND and one rule needs every one of them to hold. There is no sourced OR, and a
# rule cannot express one.

FILTER_MATCH_ALL = "all"
FILTER_MATCH_ANY = "any"

FILTER_MATCH_MODES = (FILTER_MATCH_ALL, FILTER_MATCH_ANY)

#: The research's conjunction, and therefore the default.
#:
#: ``any`` exists as a value a caller may send, because this product is
#: schema-flexible and refusing it would be a rule the evidence does not state. It
#: is not the default and the page does not offer it, so a rule that reaches it did
#: so deliberately.
FILTER_MATCH_MODE_LABELS: dict[str, str] = {
    FILTER_MATCH_ALL: "Every filter must be met. This is what the research says.",
    FILTER_MATCH_ANY: "Any one filter is enough. The research does not describe this.",
}


# --------------------------------------------------------------------------- #
# Approvers
# --------------------------------------------------------------------------- #

#: "Assign up to 10 approvers to review quotes that match your configured filters."
#: A hard cap, quoted whole, and the reason this workflow validates the count on
#: the rule rather than at enrolment.
MAX_APPROVERS = 10

#: "Assign up to 10 approvers" leaves zero open as a statement about the rule.
#: A rule with no approver would either enrol nobody or enrol everybody, and both
#: are worse than a rule that does not exist yet. So the floor is one.
MIN_APPROVERS = 1

APPROVER_CAP_QUOTE = (
    "Assign up to 10 approvers to review quotes that match your configured filters."
)


# --------------------------------------------------------------------------- #
# Approver requirements
# --------------------------------------------------------------------------- #
#
# "Under *Approver requirements* choose **All approvers required** or **At least one
# approver required**." Two values, both named in the source, and they are not
# interchangeable: one is a unanimous gate and the other is a single signature.

REQUIREMENT_ALL = "all"
REQUIREMENT_ANY = "any"

APPROVER_REQUIREMENTS = (REQUIREMENT_ALL, REQUIREMENT_ANY)

APPROVER_REQUIREMENT_LABELS: dict[str, str] = {
    REQUIREMENT_ALL: "All approvers required",
    REQUIREMENT_ANY: "At least one approver required",
}

APPROVER_REQUIREMENT_QUOTE = (
    "Under Approver requirements choose All approvers required or At least one approver required."
)


# --------------------------------------------------------------------------- #
# Quote states
# --------------------------------------------------------------------------- #
#
# "Quote state is exposed on the object: ``hs_status`` (DRAFT / PENDING_APPROVAL /
# APPROVED / REJECTED / ACCEPTED ...)". The payload key is the vendor's own spelling,
# so a client written against the vendor documentation finds it here.

#: The payload key for the quote state, in the vendor's spelling.
HS_STATUS = "hs_status"

#: The payload key for the lock the research fixes: "``hs_locked`` ... Set to
#: ``true``. To modify any properties after you've published a quote, you must first
#: update the ``hs_status`` of the quote back to ``DRAFT``, ``PENDING_APPROVAL``, or
#: ``REJECTED``."
HS_LOCKED = "hs_locked"

STATE_DRAFT = "DRAFT"
STATE_PENDING_APPROVAL = "PENDING_APPROVAL"
STATE_APPROVED = "APPROVED"
STATE_REJECTED = "REJECTED"
STATE_SHARED = "SHARED"
STATE_ACCEPTED = "ACCEPTED"

#: The five states this workflow can put a quote into or find it in. ACCEPTED is
#: carried because the vendor enumerates it, and this workflow never writes it.
QUOTE_STATES = (
    STATE_DRAFT,
    STATE_PENDING_APPROVAL,
    STATE_APPROVED,
    STATE_REJECTED,
    STATE_SHARED,
    STATE_ACCEPTED,
)

QUOTE_STATE_LABELS: dict[str, str] = {
    STATE_DRAFT: "Draft. Editable, and not shareable with a buyer.",
    STATE_PENDING_APPROVAL: (
        "Pending approval. Waiting on approvers. Editable, because the research puts "
        "PENDING_APPROVAL among the statuses that release the lock."
    ),
    STATE_APPROVED: "Approved. The only state from which the quote may be shared.",
    STATE_REJECTED: (
        "Rejected. Changes were requested. Editable, because the research puts "
        "REJECTED among the statuses that release the lock."
    ),
    STATE_SHARED: "Shared. Sent to the buyer.",
    STATE_ACCEPTED: "Accepted by the buyer. This workflow never writes this state.",
}

#: "To modify any properties after you've published a quote, you must first update the
#: ``hs_status`` of the quote back to ``DRAFT``, ``PENDING_APPROVAL``, or
#: ``REJECTED``." The three statuses that release a lock, quoted whole.
UNLOCK_TARGET_STATES = (STATE_DRAFT, STATE_PENDING_APPROVAL, STATE_REJECTED)

UNLOCK_TARGET_QUOTE = (
    "To modify any properties after you've published a quote, you must first update "
    "the hs_status of the quote back to DRAFT, PENDING_APPROVAL, or REJECTED."
)

#: The states a quote this workflow owns can sit in while it is under approval.
PENDING_STATES = (STATE_PENDING_APPROVAL,)

#: The decision a quote reaches when its approval requirement is met.
SATISFIED_STATES = (STATE_APPROVED,)

#: The state a quote reaches when an approver requests changes.
REFUSED_STATES = (STATE_REJECTED,)

#: The only state from which "only on approval can the quote be **Share**d" holds.
#: So a share of any other state is refused, and the refusal is a row.
SHAREABLE_STATES = (STATE_APPROVED, STATE_SHARED, STATE_ACCEPTED)

SHARE_ON_APPROVAL_QUOTE = "only on approval can the quote be Share-d and sent to the buyer"


# --------------------------------------------------------------------------- #
# Decisions
# --------------------------------------------------------------------------- #

DECISION_APPROVE = "approve"
DECISION_REQUEST_CHANGES = "request_changes"

DECISIONS = (DECISION_APPROVE, DECISION_REQUEST_CHANGES)

DECISION_LABELS: dict[str, str] = {
    DECISION_APPROVE: "Approved. The quote may be shared.",
    DECISION_REQUEST_CHANGES: ("Changes requested. The quote is REJECTED and the seller edits it."),
}

#: The wire spelling, matching the two buttons in the flow: "**Approve** (with an
#: optional message) or **Request changes**".
DECISION_APPROVE_WIRE = "approve"
DECISION_REJECT_WIRE = "reject"


# --------------------------------------------------------------------------- #
# Re-submission
# --------------------------------------------------------------------------- #

#: "if there's more than one approver, and one approver requests changes, every
#: approver will need to approve the quote again when the quote is re-submitted."
#: Quoted whole, and it is the rule that decides whether an earlier approval
#: survives a rejection.
RESUBMISSION_QUOTE = (
    "If there's more than one approver, and one approver requests changes, every "
    "approver will need to approve the quote again when the quote is re-submitted."
)

#: The single-approver case of that sentence, derived rather than sourced. With one
#: approver there is nobody left to approve again, and the research says the quote
#: goes back to Pending approval on re-submission, so the requirement is met at
#: once. Recorded because it is this build's reading of an edge the source leaves
#: open. See ``DERIVED_RESUBMISSION_SINGLE_APPROVER`` in the inferences module.
SINGLE_APPROVER_RESUBMISSION_NOTE = (
    "With one approver there is nobody left to approve again, so the re-submitted "
    "quote satisfies its requirement at once. The research's sentence names the "
    "case of more than one approver."
)


# --------------------------------------------------------------------------- #
# Notifications
# --------------------------------------------------------------------------- #

CHANNEL_IN_APP = "in_app"
CHANNEL_EMAIL = "email"
CHANNEL_SLACK = "slack"
CHANNEL_GOOGLE_CHAT = "google_chat"
CHANNEL_TEAMS = "teams"

#: The channels the research names. "Approval notifications can be delivered to
#: third-party apps (Google Chat, Microsoft Teams, Slack) via Settings ->
#: Notifications -> **Other apps**", and the bell and the email are named
#: separately in step five.
#:
#: All five are recorded rather than sent. This product has no mail transport and no
#: chat integration, so a response that read like a delivery receipt would be
#: something a reviewer has to find in a diff rather than being told. See
#: ``DERIVED_NOTIFICATIONS_ARE_RECORDED`` in the inferences module.
NOTIFICATION_CHANNELS = (
    CHANNEL_IN_APP,
    CHANNEL_EMAIL,
    CHANNEL_SLACK,
    CHANNEL_GOOGLE_CHAT,
    CHANNEL_TEAMS,
)

NOTIFICATION_CHANNEL_LABELS: dict[str, str] = {
    CHANNEL_IN_APP: "The bell notification in the sales room.",
    CHANNEL_EMAIL: "The email notification with a Go to quote action.",
    CHANNEL_SLACK: "Slack, under Settings, Notifications, Other apps.",
    CHANNEL_GOOGLE_CHAT: "Google Chat, under Settings, Notifications, Other apps.",
    CHANNEL_TEAMS: "Microsoft Teams, under Settings, Notifications, Other apps.",
}

#: What a recorded notification says about itself. Spelled out because every value
#: of it is the difference between a record and a claim.
DISPATCH_DISPATCHED_BY = "recorded"
DISPATCH_NOT_DELIVERED = "recorded_not_delivered"

RECORDED_NOT_DELIVERED_NOTE = (
    "This notification is recorded, not delivered. This product has no mail "
    "transport and no chat integration."
)

#: The three researched notification moments, in the order the flow raises them.
NOTIFY_APPROVAL_REQUESTED = "approval_requested"
NOTIFY_APPROVAL_DECIDED = "approval_decided"
NOTIFY_CREATOR_NOTIFIED = "creator_notified"

NOTIFICATION_MOMENTS = (
    NOTIFY_APPROVAL_REQUESTED,
    NOTIFY_APPROVAL_DECIDED,
    NOTIFY_CREATOR_NOTIFIED,
)

#: The researched recipients. An approver is told the quote is waiting, and the
#: creator is told the outcome.
RECIPIENT_APPROVER = "approver"
RECIPIENT_CREATOR = "creator"

NOTIFICATION_RECIPIENTS = (RECIPIENT_APPROVER, RECIPIENT_CREATOR)


# --------------------------------------------------------------------------- #
# The approval note
# --------------------------------------------------------------------------- #

#: "Enter **With this approval note**." The note is part of the rule, not part of a
#: submission, and "the approval note is templated per rule" says the rule is where
#: it is written. So it is stored on the rule and copied onto each enrolment, which
#: is what makes a past enrolment readable on its own.
APPROVAL_NOTE = "approval_note"

APPROVAL_NOTE_QUOTE = "With this approval note."

#: The research also names a per-request note: "click **Request approval** -> add
#: **Notes to approver**". Two notes with two owners, so two fields.
NOTES_TO_APPROVER = "notes_to_approver"

NOTES_TO_APPROVER_QUOTE = "Notes to approver"


# --------------------------------------------------------------------------- #
# Activities
# --------------------------------------------------------------------------- #

#: The three activity names, in the research's own spelling. "Quote approval
#: requested", "Quote approval rejected / requested changes" and "Quote approved".
ACTIVITY_REQUESTED = "Quote approval requested"
ACTIVITY_REJECTED = "Quote approval rejected / requested changes"
ACTIVITY_APPROVED = "Quote approved"

#: Two this build adds, because the flow describes both and the research names the
#: log but does not name them. They are marked as this build's where the inferences
#: module lists them.
ACTIVITY_ENROLLED_WITHOUT_APPROVAL = "Quote published without approval"
ACTIVITY_SHARE_REFUSED = "Quote share refused, not approved"

ACTIVITY_TYPES = (
    ACTIVITY_REQUESTED,
    ACTIVITY_REJECTED,
    ACTIVITY_APPROVED,
    ACTIVITY_ENROLLED_WITHOUT_APPROVAL,
    ACTIVITY_SHARE_REFUSED,
)

#: The three quoted names, for a reader who wants only sourced vocabulary.
SOURCED_ACTIVITY_TYPES = (
    ACTIVITY_REQUESTED,
    ACTIVITY_REJECTED,
    ACTIVITY_APPROVED,
)


# --------------------------------------------------------------------------- #
# Why approval was required
# --------------------------------------------------------------------------- #
#
# Step four of the flow: "hover **Request approval** -> **View approval conditions**
# to see why approval is required". So the reason a quote matched is stored on the
# enrolment and is readable without re-running the filters. One matched rule and one
# unmatched rule are not the same report, so both are kept.

MATCHED_FILTERS = "matched_filters"
MATCHED_RULES = "matched_rules"

#: Why an enrolment needed no approver at all. The only sourced way this happens is
#: the sole-approver case.
ENROLMENT_EXEMPT_SOLE_APPROVER = "sole_approver_is_the_creator"
ENROLMENT_EXEMPT_NO_MATCH = "no_rule_matched"
ENROLMENT_EXEMPT_RULE_INACTIVE = "matched_rule_is_inactive"

ENROLMENT_EXEMPTIONS = (
    ENROLMENT_EXEMPT_SOLE_APPROVER,
    ENROLMENT_EXEMPT_NO_MATCH,
    ENROLMENT_EXEMPT_RULE_INACTIVE,
)

ENROLMENT_EXEMPTION_LABELS: dict[str, str] = {
    ENROLMENT_EXEMPT_SOLE_APPROVER: (
        "The only configured approver created this quote, so the research says the "
        "quote does not require approval."
    ),
    ENROLMENT_EXEMPT_NO_MATCH: (
        "No configured filter matched this quote, so approval was never required."
    ),
    ENROLMENT_EXEMPT_RULE_INACTIVE: (
        "A rule matched this quote but its switch is off, so it is not active."
    ),
}


# --------------------------------------------------------------------------- #
# Self approval
# --------------------------------------------------------------------------- #
#
# The two sourced sentences that do not agree, quoted whole, and served on the page
# beside the rule this build follows. See
# ``SELF_APPROVAL_DECISION`` in :mod:`dsr.quoting_proposals.quote_approval_inferences`
# for the reading that was chosen and the alternative that was rejected.

SELF_APPROVAL_EXEMPTION_QUOTE = (
    "If a designated approver creates a quote, and they're the only approver, the "
    "quote won't require approval. If there are multiple approvers, they'll be "
    "removed from the approval process."
)

NO_SELF_APPROVAL_QUOTE = "Approvers can't approve their own quotes."

#: The reading this build implements, in one sentence, for a page to show.
SELF_APPROVAL_RULE_TEXT = (
    "Every approver who created the quote is removed from the approver list. The "
    "remaining approvers decide. If none remain, the quote needs no approval."
)


# --------------------------------------------------------------------------- #
# Filters and approvals are evaluated on submit and on publish
# --------------------------------------------------------------------------- #

TRIGGER_SUBMIT = "submit_approval"
TRIGGER_PUBLISH = "publish"
TRIGGER_SHARE = "share"

TRIGGERS = (TRIGGER_SUBMIT, TRIGGER_PUBLISH, TRIGGER_SHARE)

TRIGGER_LABELS: dict[str, str] = {
    TRIGGER_SUBMIT: ("The seller clicked Request approval. The research's step four."),
    TRIGGER_PUBLISH: (
        "The seller tried to publish or share. 'Approval enrolment and notification "
        "fire on the publish/share attempt.'"
    ),
    TRIGGER_SHARE: (
        "The seller tried to share the quote. 'only on approval can the quote be Share-d'."
    ),
}


# --------------------------------------------------------------------------- #
# The errors
# --------------------------------------------------------------------------- #
#
# Each error this workflow raises, with the status it maps to and the
# specification's own sentence in the value, so a refusal returned by the API
# reads as the research rather than as a developer's paraphrase of it.

ERROR_CODES: dict[str, tuple[int, str]] = {
    "rule_needs_a_name": (422, "Give the rule a name so a page can list it."),
    "rule_needs_a_filter": (
        422,
        "A rule needs at least one filter. A rule that matches everything approves every quote.",
    ),
    "rule_needs_at_least_one_approver": (
        422,
        "Assign at least one approver. A rule with no approver cannot be satisfied.",
    ),
    "too_many_approvers": (
        422,
        "Assign up to 10 approvers to review quotes that match your configured filters.",
    ),
    "approver_repeated": (422, "The same approver is listed twice."),
    "unknown_filter_object": (
        422,
        "A filter names an object. The research names quote, line item and deal, and "
        "a team may add its own.",
    ),
    "filter_needs_a_property": (
        422,
        "Choose a property for the filter. The flow picks one from the object's property list.",
    ),
    "unknown_operator": (
        422,
        f"Known operators are: {', '.join(OPERATORS)}.",
    ),
    "filter_needs_a_value": (
        422,
        "A filter needs a value to compare the property with.",
    ),
    "numeric_filter_value_is_not_a_number": (
        422,
        "This operator compares numbers, so the value must be a number.",
    ),
    "list_operator_needs_a_list": (
        422,
        "An 'is any of' or 'is none of' filter needs a list of values.",
    ),
    "unknown_requirement": (
        422,
        "Choose All approvers required or At least one approver required.",
    ),
    "unknown_decision": (
        422,
        "Approve the quote, or request changes.",
    ),
    "unknown_trigger": (
        422,
        "Evaluate on submit, on publish, or on share.",
    ),
    "unknown_channel": (
        422,
        f"Known channels are: {', '.join(NOTIFICATION_CHANNELS)}.",
    ),
    "unknown_approval_rule": (404, "No such approval rule."),
    "unknown_approval_request": (404, "No such approval request."),
    "unknown_quote": (404, "No such quote."),
    "approver_is_not_on_this_request": (
        403,
        "You are not one of the approvers on this request.",
    ),
    "approver_is_the_creator": (
        403,
        "Approvers can't approve their own quotes.",
    ),
    "quote_is_not_pending": (
        409,
        "This quote is not pending approval, so it has no decision to make.",
    ),
    "request_already_decided": (
        409,
        "This approval request already has its outcome. Submit the quote again to start a new one.",
    ),
    "quote_not_approved_to_share": (
        409,
        "Only on approval can the quote be Share-d and sent to the buyer.",
    ),
    "reason_required_to_request_changes": (
        422,
        "Enter the changes, then Reject. A change request with nothing in it is not "
        "an instruction.",
    ),
}


# --------------------------------------------------------------------------- #
# What the page renders from
# --------------------------------------------------------------------------- #


def catalogue() -> dict[str, Any]:
    """Every researched term, as data a client can render from.

    Every value here is a constant above, so the served list and the enforced rule
    cannot disagree. The frontend reads this rather than hard-coding a list, so a
    term added here appears on the page with no edit to the feature module.
    """

    return {
        "collections": {
            "approval_rules": APPROVAL_RULES,
            "approval_requests": APPROVAL_REQUESTS,
            "approval_decisions": APPROVAL_DECISIONS,
            "activities": ACTIVITIES,
            "notifications": NOTIFICATIONS,
            "source_quotes_read_only": SOURCE_QUOTES,
            "source_shares_read_only": SOURCE_SHARES,
        },
        "filters": {
            "objects": [
                {"object": name, "label": FILTER_OBJECT_LABELS[name]} for name in FILTER_OBJECTS
            ],
            "operators": [{"operator": name, "label": OPERATOR_LABELS[name]} for name in OPERATORS],
            "list_operators": list(LIST_OPERATORS),
            "numeric_operators": list(NUMERIC_OPERATORS),
            "match_modes": [
                {"mode": name, "label": FILTER_MATCH_MODE_LABELS[name]}
                for name in FILTER_MATCH_MODES
            ],
            "default_match_mode": FILTER_MATCH_ALL,
            "operators_choice_note": FILTER_OPERATORS_CHOICE_NOTE,
            "conjunction_quote": (
                "And these conditions are met. Every filter in a rule must be met."
            ),
        },
        "approvers": {
            "max": MAX_APPROVERS,
            "min": MIN_APPROVERS,
            "cap_quote": APPROVER_CAP_QUOTE,
            "requirements": [
                {"requirement": name, "label": APPROVER_REQUIREMENT_LABELS[name]}
                for name in APPROVER_REQUIREMENTS
            ],
            "default_requirement": REQUIREMENT_ALL,
            "requirement_quote": APPROVER_REQUIREMENT_QUOTE,
            "self_approval_exemption_quote": SELF_APPROVAL_EXEMPTION_QUOTE,
            "no_self_approval_quote": NO_SELF_APPROVAL_QUOTE,
            "self_approval_rule": SELF_APPROVAL_RULE_TEXT,
        },
        "quote_states": [
            {
                "state": state,
                "label": QUOTE_STATE_LABELS[state],
                "shareable": state in SHAREABLE_STATES,
            }
            for state in QUOTE_STATES
        ],
        "unlock_target_states": list(UNLOCK_TARGET_STATES),
        "unlock_target_quote": UNLOCK_TARGET_QUOTE,
        "shareable_states": list(SHAREABLE_STATES),
        "share_on_approval_quote": SHARE_ON_APPROVAL_QUOTE,
        "decisions": [{"decision": name, "label": DECISION_LABELS[name]} for name in DECISIONS],
        "triggers": [{"trigger": name, "label": TRIGGER_LABELS[name]} for name in TRIGGERS],
        "notes": {
            "approval_note_field": APPROVAL_NOTE,
            "approval_note_quote": APPROVAL_NOTE_QUOTE,
            "notes_to_approver_field": NOTES_TO_APPROVER,
            "notes_to_approver_quote": NOTES_TO_APPROVER_QUOTE,
        },
        "resubmission": {
            "quote": RESUBMISSION_QUOTE,
            "single_approver_note": SINGLE_APPROVER_RESUBMISSION_NOTE,
            "every_approver_approves_again": True,
        },
        "notifications": {
            "channels": [
                {"channel": name, "label": NOTIFICATION_CHANNEL_LABELS[name]}
                for name in NOTIFICATION_CHANNELS
            ],
            "moments": list(NOTIFICATION_MOMENTS),
            "recipients": list(NOTIFICATION_RECIPIENTS),
            "dispatched_by": DISPATCH_DISPATCHED_BY,
            "recorded_only_note": RECORDED_NOT_DELIVERED_NOTE,
        },
        "activities": list(ACTIVITY_TYPES),
        "sourced_activities": list(SOURCED_ACTIVITY_TYPES),
        "enrolment_exemptions": [
            {"exemption": name, "label": ENROLMENT_EXEMPTION_LABELS[name]}
            for name in ENROLMENT_EXEMPTIONS
        ],
        "fields": {
            "status": HS_STATUS,
            "locked": HS_LOCKED,
        },
        "error_codes": {
            name: {"status": status, "detail": detail}
            for name, (status, detail) in ERROR_CODES.items()
        },
    }
