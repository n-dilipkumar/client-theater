"""WF-098: every researched term, quoted from the specification.

The specification is ``docs/research/digital-sales-room-workflows/wf/WF-098.md``,
quoted in full in issue 130, and the underlying research is
``docs/research/raw/quoting-proposals.md`` section 14. Every constant below carries
the sentence it came from, because a number in a governance rule that nobody can trace
to its source is a number somebody will change one day without knowing why.

Nothing here reads or writes. These are the words, the caps and the evidence.

Where the specification left a joint open, the gap is recorded in
:mod:`dsr.quoting_proposals.quote_expiry_inferences` rather than quietly filled in
here, so the difference between what the evidence says and what this build chose stays
readable.
"""

from __future__ import annotations

from typing import Any

# --------------------------------------------------------------------------- #
# Collections
# --------------------------------------------------------------------------- #
#
# Ticket-prefixed, which is the uniform convention in this repository: WF-093's
# vocabulary module in this same package counted 59 distinct prefixed collection
# strings under ``dsr/`` and none of them was a bare noun. The host has no
# collection-collision check, so the prefix is what keeps two features from quietly
# sharing one.

#: The quote's expiry and delivery state, which is what this workflow owns.
#:
#: WF-086 provisions the authored quote: its line items, its totals and the
#: template association that the evidence says is "settable only at quote creation".
#: WF-094 provisions the publish and the hosted link. This workflow owns neither. It
#: owns the question "does this quote close, when, and what reminder goes out", and
#: that state is its own record. A quote it tracks names the record it came from in
#: :data:`SOURCE_QUOTE_REF` when one was provisioned, and works without one.
QUOTES = "wf098_quote"

#: The reminder schedule. One row per rule, because the research says "Multiple
#: independent reminder rules" and "``+ Add reminder`` / delete icon to manage
#: several", so a rule is an addressable row with its own delete rather than a list
#: buried inside the settings blob.
REMINDER_RULES = "wf098_reminder_rule"

#: One row per reminder decision, sent or skipped. A skip is a row for the same
#: reason a send is: the rules that suppress a reminder are the ones hardest to
#: verify, and a suppression nobody can see is a suppression nobody can check.
REMINDERS = "wf098_reminder"

#: The quote activity log. "Reminder + expiration events are exposed as quote
#: activities that can drive workflows", so they are rows rather than log lines.
ACTIVITIES = "wf098_activity"

#: The account settings: the default expiration period, the reminder send time, the
#: account time zone and the automated-reminders toggle.
#:
#: The research is explicit that this store is this workflow's own: "The reminder
#: schedule has no documented public write API on the pages read." See
#: ``DERIVED_SETTINGS_ROUTE_IS_THE_STORE`` in the inferences module.
SETTINGS = "wf098_settings"

#: The collection this workflow names but never writes through a route: WF-086's
#: authored quote. Read as data so a tracked quote can name what it tracks.
SOURCE_QUOTES = "wf086_quote"

#: WF-094's publish and share state. Named for the same reason.
SOURCE_SHARES = "wf094_share"


# --------------------------------------------------------------------------- #
# The expiration property
# --------------------------------------------------------------------------- #

#: The payload key, in the vendor's own spelling. "``hs_expiration_date`` is a
#: required property on ``POST /crm/objects/{version}/quotes``", so a client written
#: from the vendor's documentation sends this name and finds it here.
EXPIRATION_DATE = "hs_expiration_date"

#: "Under *Expiration date* click the **date picker** to set a specific date, edit
#: the **Label**, or toggle the **Expiration date** switch off." Three separate
#: controls, so three separate stored fields.
EXPIRATION_LABEL = "expiration_label"

#: The switch. ``False`` means the quote carries no deadline and the expiry check
#: leaves it alone, which is a state a seller chose rather than a field that was
#: never filled in.
EXPIRATION_ENABLED = "expiration_enabled"

#: "HubSpot: because a past *Effective date* is allowed". A past effective date is
#: legal and it changes what the expiration date means, so the field is stored and
#: never rejected.
EFFECTIVE_DATE = "effective_date"

#: "Set a default expiration period for quotes... enter a default expiration time
#: period between 1 and 365 days." Both bounds are the research's and neither is a
#: figure this build chose. Validated on the settings route, which is where the
#: research says the number is entered.
MIN_DEFAULT_EXPIRATION_DAYS = 1
MAX_DEFAULT_EXPIRATION_DAYS = 365

DEFAULT_EXPIRATION_DAYS = "default_expiration_days"

#: Which level supplied a quote's expiration date. The research fixes the direction
#: for a stored date ("properties set on the quote overriding the quote template's
#: settings", WF-093's own evidence) and fixes the scope for the default ("Any new
#: quotes created after the setting is turned on will automatically use the configured
#: expiration date"), so the two are recorded separately rather than one boolean.
EXPIRY_SOURCE_QUOTED = "stated_on_quote"
EXPIRY_SOURCE_ACCOUNT_DEFAULT = "account_default"
EXPIRY_SOURCE_NONE = "expiration_off"

EXPIRY_SOURCES = (EXPIRY_SOURCE_QUOTED, EXPIRY_SOURCE_ACCOUNT_DEFAULT, EXPIRY_SOURCE_NONE)

EXPIRY_SOURCE_LABELS: dict[str, str] = {
    EXPIRY_SOURCE_QUOTED: "The quote's own expiration date. It wins over any default.",
    EXPIRY_SOURCE_ACCOUNT_DEFAULT: (
        "The account default window, applied when the quote was created after the default was set."
    ),
    EXPIRY_SOURCE_NONE: (
        "The seller turned the Expiration date switch off. This quote never expires."
    ),
}


# --------------------------------------------------------------------------- #
# Quote states
# --------------------------------------------------------------------------- #

QUOTE_DRAFT = "draft"
QUOTE_SENT = "sent"
QUOTE_PUBLISHED = "published"
QUOTE_ACCEPTED = "accepted"
QUOTE_SIGNED = "signed"
QUOTE_EXPIRED = "expired"
QUOTE_VOIDED = "voided"
QUOTE_ARCHIVED = "archived"

#: The eight states a quote this workflow tracks can be in.
#:
#: ``voided`` and ``archived`` are separate states because the research gives them
#: separate sentences with different consequences. "**Void:** ... The quote link URL
#: will be deactivated." and "**Archive:** ... The quote is unpublished, hidden from
#: the default index page view, and prevents buyers from accessing it." One boolean
#: called ``inactive`` would lose the difference between a dead link and a hidden
#: document.
QUOTE_STATES = (
    QUOTE_DRAFT,
    QUOTE_SENT,
    QUOTE_PUBLISHED,
    QUOTE_ACCEPTED,
    QUOTE_SIGNED,
    QUOTE_EXPIRED,
    QUOTE_VOIDED,
    QUOTE_ARCHIVED,
)

QUOTE_STATE_LABELS: dict[str, str] = {
    QUOTE_DRAFT: "Draft. Not sent, so no reminder offset has an anchor.",
    QUOTE_SENT: "Sent to the buyer. Reminder offsets count from this send.",
    QUOTE_PUBLISHED: "Published as a hosted link. Reminder offsets count from this publish.",
    QUOTE_ACCEPTED: "Accepted by the buyer. The quote survives its expiration date.",
    QUOTE_SIGNED: "Signed by the buyer. The quote survives its expiration date.",
    QUOTE_EXPIRED: "Expired. The buyer can no longer accept it.",
    QUOTE_VOIDED: "Voided. The quote link URL is deactivated.",
    QUOTE_ARCHIVED: "Archived. Unpublished, hidden from the index, and buyers cannot reach it.",
}

#: The states in which the quote no longer waits on the buyer. A quote in one of
#: these is not swept and is not reminded.
TERMINAL_QUOTE_STATES = (
    QUOTE_ACCEPTED,
    QUOTE_SIGNED,
    QUOTE_EXPIRED,
    QUOTE_VOIDED,
    QUOTE_ARCHIVED,
)

#: The states the research's reminder and expiry jobs evaluate: "a scheduled job
#: evaluates sent/published quotes relative to that date".
EVALUATED_QUOTE_STATES = (QUOTE_SENT, QUOTE_PUBLISHED)


# --------------------------------------------------------------------------- #
# Acceptance
# --------------------------------------------------------------------------- #

#: "Expiry semantics depend on acceptance method: e-signature/click-to-accept/
#: print-and-sign each have their own 'won't expire if' rule." The three methods,
#: spelled as the research spells them.
METHOD_E_SIGNATURE = "e_signature"
METHOD_CLICK_TO_ACCEPT = "click_to_accept"
METHOD_PRINT_AND_SIGN = "print_and_sign"

ACCEPTANCE_METHODS = (METHOD_E_SIGNATURE, METHOD_CLICK_TO_ACCEPT, METHOD_PRINT_AND_SIGN)

ACCEPTANCE_METHOD_LABELS: dict[str, str] = {
    METHOD_E_SIGNATURE: "E-signature. The buyer signs, so the quote does not expire.",
    METHOD_CLICK_TO_ACCEPT: (
        "Click to accept. The buyer accepts with one click, so the quote does not expire."
    ),
    METHOD_PRINT_AND_SIGN: (
        "Print and sign. The buyer signs on paper, so the quote does not expire."
    ),
}

#: What the buyer did, as the data flow names the three ways a quote stops waiting:
#: "if the buyer has not accepted/e-signed/marked-signed by the expiration date".
ACTION_ACCEPTED = "accepted"
ACTION_E_SIGNED = "e_signed"
ACTION_MARKED_SIGNED = "marked_signed"

#: The three actions that save a quote from its deadline, and the only three the
#: research names. Everything else a quote may have done - countersigned, paid - is
#: deliberately absent, because of the hard sentence: "If a quote is accepted or
#: signed before the expiration date, but hasn't been countersigned or paid, the
#: quote won't expire." Those two are not on this list and that is the rule.
SURVIVING_ACTIONS = (ACTION_ACCEPTED, ACTION_E_SIGNED, ACTION_MARKED_SIGNED)

#: "status filters for \"expiring soon\"" names the filter and not the window, so the
#: figure below is this build's own. It is served on the page so a reader can see it is
#: chosen rather than sourced, and a caller may pass its own window to the quote list
#: instead of relying on it.
EXPIRING_SOON_DAYS = 7

#: Named here because the research names them, and because they are the reason the
#: surviving list above is short. A quote that reached either of these and was never
#: accepted or signed is still expiring.
ACTION_COUNTERSIGNED = "countersigned"
ACTION_PAID = "paid"

#: Everything the research says a buyer may do to a quote. Stored as data so the
#: page reports what happened without a schema.
ALL_ACTIONS = SURVIVING_ACTIONS + (ACTION_COUNTERSIGNED, ACTION_PAID)

ACTION_LABELS: dict[str, str] = {
    ACTION_ACCEPTED: "Accepted.",
    ACTION_E_SIGNED: "E-signed.",
    ACTION_MARKED_SIGNED: "Marked signed.",
    ACTION_COUNTERSIGNED: "Countersigned.",
    ACTION_PAID: "Paid.",
}

#: The hard sentence, quoted whole. It is the single researched statement that
#: decides the survival predicate, and the page shows it beside the result.
SURVIVAL_QUOTE = (
    "If a quote is accepted or signed before the expiration date, but hasn't been "
    "countersigned or paid, the quote won't expire."
)

#: The sign-by-deadline sentence. A past effective date is legal and it repurposes
#: the field: "the **Expiration date** will be treated as the buyer's sign-by
#: deadline."
SIGN_BY_DEADLINE_QUOTE = "The Expiration date will be treated as the buyer's sign-by deadline."


# --------------------------------------------------------------------------- #
# Sending
# --------------------------------------------------------------------------- #

SENT_AT = "sent_at"
PUBLISHED_AT = "published_at"

#: "Resending counts as a new send (consuming e-signature quota again)." A count and
#: not a boolean, because the sentence is about consumption and the second send costs
#: the same as the first.
SEND_COUNT = "send_count"
ESIGNATURE_QUOTA_CONSUMED = "esignature_quota_consumed"

RESEND_QUOTE = "Resending counts as a new send (consuming e-signature quota again)."


# --------------------------------------------------------------------------- #
# Void and archive
# --------------------------------------------------------------------------- #

#: "**Void:** ... The quote link URL will be deactivated."
LINK_ACTIVE = "link_active"
VOIDED_AT = "voided_at"

#: "**Archive:** ... The quote is unpublished, hidden from the default index page
#: view, and prevents buyers from accessing it." Three consequences, so three
#: fields rather than one flag.
PUBLISHED = "published"
HIDDEN_FROM_INDEX = "hidden_from_index"
BUYER_ACCESS = "buyer_access"
ARCHIVED_AT = "archived_at"

VOID_QUOTE = "Void: the quote link URL will be deactivated."
ARCHIVE_QUOTE = (
    "Archive: the quote is unpublished, hidden from the default index page view, and "
    "prevents buyers from accessing it."
)

#: "expired quotes can still be downloaded, cloned, voided or archived". The four
#: things an expired quote still supports. Download and clone are reads this workflow
#: records as available rather than performing, because it owns no document body.
SURVIVABLE_ACTIONS = ("download", "clone", "void", "archive")

EXPIRY_SURVIVES_QUOTE = "An expired quote can still be downloaded, cloned, voided or archived."

#: The one thing expiry takes away: "the buyer loses the ability to accept".
ACCEPTANCE_CLOSED_QUOTE = (
    "An expired quote cannot be accepted. The buyer has lost the ability to accept."
)


# --------------------------------------------------------------------------- #
# The reminder schedule
# --------------------------------------------------------------------------- #

#: "set the number of days and choose **Days after sending quote** or **Days before
#: expiration date**". Two offsets, named as the UI names them. They are not
#: interchangeable and this build does not convert between them.
OFFSET_AFTER_SEND = "after_send"
OFFSET_BEFORE_EXPIRY = "before_expiry"

OFFSET_KINDS = (OFFSET_AFTER_SEND, OFFSET_BEFORE_EXPIRY)

OFFSET_LABELS: dict[str, str] = {
    OFFSET_AFTER_SEND: "Days after sending quote.",
    OFFSET_BEFORE_EXPIRY: "Days before expiration date.",
}

#: The researched sentences for the two offsets, served on the page so the picker
#: shows the vendor's own words.
OFFSET_QUOTES: dict[str, str] = {
    OFFSET_AFTER_SEND: "Days after sending quote",
    OFFSET_BEFORE_EXPIRY: "Days before expiration date",
}

#: A rule's own days. The research puts no bound on the number, so none is imposed
#: here: a bound the vendor does not state is a rule this product would invent.
MIN_OFFSET_DAYS = 0

#: "set *Reminder send time* (in the account time zone)".
REMINDER_SEND_TIME = "reminder_send_time"

#: The wall-clock shape the send time is stored in. A time of day and nothing else,
#: because the research says a time in the account time zone and not an instant.
SEND_TIME_FORMAT = "%H:%M"

SEND_TIME_QUOTE = "Reminder send time, in the account time zone."

#: "A reminder scheduled at 09:00 local is not 09:00 UTC." Stated because it is the
#: whole content of the timezone rule and it is the rule most often implemented as if
#: it did not exist.
TIMEZONE_QUOTE = (
    "A reminder scheduled at 09:00 local is not 09:00 UTC. The send time is a "
    "wall-clock reading in the account time zone."
)

#: "toggle **Send automated reminders to quote recipients**".
AUTOMATED_REMINDERS_ENABLED = "automated_reminders_enabled"

AUTOMATED_REMINDERS_QUOTE = "Send automated reminders to quote recipients."

#: The account time zone field.
ACCOUNT_TIMEZONE = "account_timezone"

#: What this build says when it cannot resolve a named time zone. The instant is
#: still exact, and only the local reading fell back.
NOTE_UNRESOLVED_TIMEZONE = (
    "This build has no timezone database, so the send time is shown in UTC. The "
    "instant itself is exact."
)


# --------------------------------------------------------------------------- #
# Reminder outcomes
# --------------------------------------------------------------------------- #

OUTCOME_SENT = "sent"
OUTCOME_SKIPPED = "skipped"

#: Every reason this workflow declines to send, as a stored value so the ledger and
#: the page agree on the word rather than each inventing one.
SKIP_NO_SEND_ANCHOR = "no_send_anchor"
SKIP_NO_EXPIRATION_DATE = "no_expiration_date"
SKIP_EXPIRATION_OFF = "expiration_off"
SKIP_QUOTE_CLOSED = "quote_closed"
SKIP_QUOTE_ACCEPTED = "quote_accepted"
SKIP_RULE_ALREADY_SENT = "rule_already_sent"
SKIP_AUTOMATED_DISABLED = "automated_reminders_disabled"

SKIP_REASONS = (
    SKIP_NO_SEND_ANCHOR,
    SKIP_NO_EXPIRATION_DATE,
    SKIP_EXPIRATION_OFF,
    SKIP_QUOTE_CLOSED,
    SKIP_QUOTE_ACCEPTED,
    SKIP_RULE_ALREADY_SENT,
    SKIP_AUTOMATED_DISABLED,
)

SKIP_REASON_TEXT: dict[str, str] = {
    SKIP_NO_SEND_ANCHOR: (
        "This quote has not been sent or published, so a 'days after sending' rule has "
        "no day to count from."
    ),
    SKIP_NO_EXPIRATION_DATE: (
        "This quote carries no expiration date, so a 'days before expiration' rule has "
        "no day to count back from."
    ),
    SKIP_EXPIRATION_OFF: ("The seller turned the Expiration date switch off on this quote."),
    SKIP_QUOTE_CLOSED: (
        "This quote is expired, voided or archived, so it is no longer waiting on the buyer."
    ),
    SKIP_QUOTE_ACCEPTED: "The buyer already accepted or signed this quote.",
    SKIP_RULE_ALREADY_SENT: (
        "This reminder rule has already sent once for this quote. Each rule fires once per quote."
    ),
    SKIP_AUTOMATED_DISABLED: (
        "Automated reminders are turned off for this account. A manual reminder still goes out."
    ),
}


# --------------------------------------------------------------------------- #
# Activities
# --------------------------------------------------------------------------- #

#: The activity names, in the research's own spelling. "Quote expired" is quoted in
#: the data flow, and the send and publish names are the ones WF-094 records.
ACTIVITY_SENT = "Quote sent"
ACTIVITY_PUBLISHED = "Quote published"
ACTIVITY_ACCEPTED = "Quote accepted"
ACTIVITY_SIGNED = "Quote signed"
ACTIVITY_EXPIRED = "Quote expired"
ACTIVITY_EXPIRATION_SET = "Expiration date set"
ACTIVITY_EXPIRATION_OFF = "Expiration date turned off"
ACTIVITY_REMINDER_SENT = "Reminder sent"
ACTIVITY_VOIDED = "Quote voided"
ACTIVITY_ARCHIVED = "Quote archived"

#: Every activity this workflow writes. Served as data so the page renders the list
#: from the server rather than from a copy compiled into it.
ACTIVITY_TYPES = (
    ACTIVITY_SENT,
    ACTIVITY_PUBLISHED,
    ACTIVITY_ACCEPTED,
    ACTIVITY_SIGNED,
    ACTIVITY_EXPIRED,
    ACTIVITY_EXPIRATION_SET,
    ACTIVITY_EXPIRATION_OFF,
    ACTIVITY_REMINDER_SENT,
    ACTIVITY_VOIDED,
    ACTIVITY_ARCHIVED,
)

#: The activity the data flow names for the expiry transition, verbatim.
QUOTE_EXPIRED_ACTIVITY = "Quote expired"

#: The PandaDoc state-change event name, for a consumer written against the vendor's
#: documentation. "``expiration_date`` appears in the ``document_state_changed``
#: webhook payload".
WEBHOOK_DOCUMENT_STATE_CHANGED = "document_state_changed"

#: The payload key the vendor's webhook carries.
WEBHOOK_EXPIRATION_FIELD = "expiration_date"


# --------------------------------------------------------------------------- #
# The errors
# --------------------------------------------------------------------------- #

#: Each error this workflow raises, with the status it maps to. The specification's
#: own sentence is in the value, so a refusal returned by the API reads as the
#: research rather than as a developer's paraphrase of it.
ERROR_CODES: dict[str, tuple[int, str]] = {
    "default_expiration_out_of_range": (
        422,
        "Enter a default expiration time period between 1 and 365 days.",
    ),
    "expiration_not_an_instant": (
        422,
        "The expiration date must be an ISO 8601 date or datetime.",
    ),
    "offset_days_not_an_integer": (
        422,
        "A reminder rule needs a whole number of days.",
    ),
    "unknown_offset_kind": (
        422,
        "Choose either Days after sending quote or Days before expiration date.",
    ),
    "send_time_not_a_time": (
        422,
        "The reminder send time must be a wall-clock time as HH:MM.",
    ),
    "unknown_acceptance_method": (
        422,
        "Choose one of e_signature, click_to_accept or print_and_sign.",
    ),
    "unknown_buyer_action": (
        422,
        "Record the buyer's action as accepted, e_signed or marked_signed.",
    ),
    "quote_not_editable": (
        409,
        "This quote can no longer be edited. Its expiration date is fixed.",
    ),
    "quote_acceptance_closed": (
        409,
        "An expired quote cannot be accepted. The buyer has lost the ability to accept.",
    ),
    "unknown_quote": (404, "No such quote."),
    "unknown_reminder_rule": (404, "No such reminder rule on this account."),
}


# --------------------------------------------------------------------------- #
# What the page renders from
# --------------------------------------------------------------------------- #


def catalogue() -> dict[str, Any]:
    """Every researched term, as data a client can render from.

    Every value here is a constant above, so the served list and the enforced rule
    cannot disagree. The frontend reads this rather than hard-coding a list, so a term
    added here appears on the page with no edit to the feature module.
    """

    return {
        "collections": {
            "quotes": QUOTES,
            "reminder_rules": REMINDER_RULES,
            "reminders": REMINDERS,
            "activities": ACTIVITIES,
            "settings": SETTINGS,
            "source_quotes_read_only": SOURCE_QUOTES,
            "source_shares_read_only": SOURCE_SHARES,
        },
        "fields": {
            "expiration_date": EXPIRATION_DATE,
            "expiration_label": EXPIRATION_LABEL,
            "expiration_enabled": EXPIRATION_ENABLED,
            "effective_date": EFFECTIVE_DATE,
            "send_count": SEND_COUNT,
            "esignature_quota_consumed": ESIGNATURE_QUOTA_CONSUMED,
        },
        "quote_states": [
            {
                "state": state,
                "label": QUOTE_STATE_LABELS[state],
                "terminal": state in TERMINAL_QUOTE_STATES,
            }
            for state in QUOTE_STATES
        ],
        "terminal_states": list(TERMINAL_QUOTE_STATES),
        "evaluated_states": list(EVALUATED_QUOTE_STATES),
        "acceptance": {
            "methods": [
                {"method": method, "label": ACCEPTANCE_METHOD_LABELS[method]}
                for method in ACCEPTANCE_METHODS
            ],
            "surviving_actions": list(SURVIVING_ACTIONS),
            "all_actions": list(ALL_ACTIONS),
            "action_labels": dict(ACTION_LABELS),
            "survival_quote": SURVIVAL_QUOTE,
            "sign_by_deadline_quote": SIGN_BY_DEADLINE_QUOTE,
        },
        "expiration_rule": {
            "field": EXPIRATION_DATE,
            "min_default_days": MIN_DEFAULT_EXPIRATION_DAYS,
            "max_default_days": MAX_DEFAULT_EXPIRATION_DAYS,
            "sources": [
                {"source": source, "label": EXPIRY_SOURCE_LABELS[source]}
                for source in EXPIRY_SOURCES
            ],
            "default_quote": (
                "Any new quotes created after the setting is turned on will "
                "automatically use the configured expiration date."
            ),
            "range_quote": (
                "Set a default expiration period for quotes: enter a default "
                "expiration time period between 1 and 365 days."
            ),
            "past_effective_date_allowed": True,
        },
        "reminder_rule": {
            "offset_kinds": [{"kind": kind, "label": OFFSET_LABELS[kind]} for kind in OFFSET_KINDS],
            "offset_quotes": dict(OFFSET_QUOTES),
            "min_offset_days": MIN_OFFSET_DAYS,
            "send_time_quote": SEND_TIME_QUOTE,
            "timezone_quote": TIMEZONE_QUOTE,
            "automated_quote": AUTOMATED_REMINDERS_QUOTE,
            "accounts_per_rule": 1,
            "skip_reasons": [
                {"reason": reason, "text": SKIP_REASON_TEXT[reason]} for reason in SKIP_REASONS
            ],
            "settings_api_gap": (
                "The reminder schedule has no documented public write API on the pages "
                "read. This product's settings route is the store."
            ),
        },
        "sending": {
            "send_count_field": SEND_COUNT,
            "quota_field": ESIGNATURE_QUOTA_CONSUMED,
            "quote": RESEND_QUOTE,
        },
        "void_and_archive": {
            "link_active_field": LINK_ACTIVE,
            "published_field": PUBLISHED,
            "hidden_from_index_field": HIDDEN_FROM_INDEX,
            "buyer_access_field": BUYER_ACCESS,
            "void_quote": VOID_QUOTE,
            "archive_quote": ARCHIVE_QUOTE,
        },
        "expiring_soon_days": EXPIRING_SOON_DAYS,
        "expiring_soon_note": (
            "The research names an 'expiring soon' status filter and does not say how soon "
            "is soon. This window is this build's figure, not a sourced one."
        ),
        "activities": list(ACTIVITY_TYPES),
        "webhook": {
            "event": WEBHOOK_DOCUMENT_STATE_CHANGED,
            "expiration_field": WEBHOOK_EXPIRATION_FIELD,
        },
        "invariants": {
            "expiry_survives": EXPIRY_SURVIVES_QUOTE,
            "acceptance_closed": ACCEPTANCE_CLOSED_QUOTE,
            "survival": SURVIVAL_QUOTE,
            "resend_is_a_new_send": RESEND_QUOTE,
            "expiration_off": "A quote whose Expiration date switch is off never expires.",
        },
    }
