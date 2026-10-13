"""WF-097 vocabulary: the quote activity events, the counters and the reasons.

Every name in this file is either quoted from the researched specification or is
a recorded decision, and the docstring of each one names which. The researched
labels are prose (``Quote viewed``), so they are kept verbatim rather than
slugified: a seller reading the activity timeline in the researched platform
reads exactly these strings, and an activity log whose labels are rewritten is a
log nobody can match against what they saw.

Source: ``docs/research/digital-sales-room-workflows/wf/WF-097.md``, which quotes
HubSpot's quote management documentation.
"""

from __future__ import annotations

# --------------------------------------------------------------------------- #
# Collections
# --------------------------------------------------------------------------- #
#
# Ordinary strings, not schema. The generic ``/api/records`` endpoints accept
# any of them, so a team can add its own field to any of these collections
# without coordination and without a migration.

#: The quote object. This workflow only ever reads it and patches its
#: ``engagement`` block. It never creates, deletes or re-states a quote: a quote
#: belongs to the workflow that authors or publishes it, and the researched
#: specification names this workflow as a reader of the quote object.
QUOTE = "quote"

#: Where this workflow writes its activity records.
#:
#: The ticket note says this workflow "owns its own activity records and
#: counters inside records.data", and several pending workflows touch a quote
#: without any of them claiming the activity log. A collection of its own is what
#: that means in practice: rows in another workflow's collection would make two
#: features answer for one timeline, and a reader could not tell which shape a
#: row had. Every row here carries ``quote_id``, so the timeline is one query.
EVENT = "quote_engagement_event"

#: Where the workspace's analysis settings live, as an ordinary record.
#:
#: "Settings > Objects > Quotes > Analyze quote activity level tracking
#: threshold". A setting is a record in this product, not a column, so a team
#: that wants its own analysis configuration adds its own record rather than
#: waiting for a migration that would change every workspace's row.
SETTINGS = "quote_engagement_settings"


# --------------------------------------------------------------------------- #
# Buyer events
# --------------------------------------------------------------------------- #
#
# "Buyer events include ``Quote sent``, ``Quote viewed``, ``Quote downloaded``,
# ``Quote printed``, ``Quote buyer signed``, ``Quote manually signed``,
# ``Quote accepted``." These seven labels are the test surface and are stored
# verbatim.

QUOTE_SENT = "Quote sent"
QUOTE_VIEWED = "Quote viewed"
QUOTE_DOWNLOADED = "Quote downloaded"
QUOTE_PRINTED = "Quote printed"
QUOTE_BUYER_SIGNED = "Quote buyer signed"
QUOTE_MANUALLY_SIGNED = "Quote manually signed"
QUOTE_ACCEPTED = "Quote accepted"

#: "Buyer events include ..." The list is transcribed in the order the research
#: gives it, which is the order a seller sees on a timeline.
BUYER_EVENTS = (
    QUOTE_SENT,
    QUOTE_VIEWED,
    QUOTE_DOWNLOADED,
    QUOTE_PRINTED,
    QUOTE_BUYER_SIGNED,
    QUOTE_MANUALLY_SIGNED,
    QUOTE_ACCEPTED,
)


# --------------------------------------------------------------------------- #
# Seller events
# --------------------------------------------------------------------------- #
#
# "The seller activity list includes ``Quote published``,
# ``Quote approval requested``, ``Quote approval rejected / requested changes``,
# ``Quote approved``, ``Quote recalled``, ``Quote countersigned``,
# ``Quote reassigned``, ``Signing attempt failed``, ``Chat started`` (buyer
# started a chat with the closing agent) and ``Quote expired``."
#
# ``Chat started`` sits in the seller's list but the research defines it as a
# buyer action, so it is recorded here and its side is stated explicitly rather
# than left to a reader to guess from which list it came.

QUOTE_PUBLISHED = "Quote published"
QUOTE_APPROVAL_REQUESTED = "Quote approval requested"
QUOTE_APPROVAL_REJECTED = "Quote approval rejected / requested changes"
QUOTE_APPROVED = "Quote approved"
QUOTE_RECALLED = "Quote recalled"
QUOTE_COUNTERSIGNED = "Quote countersigned"
QUOTE_REASSIGNED = "Quote reassigned"
SIGNING_ATTEMPT_FAILED = "Signing attempt failed"
CHAT_STARTED = "Chat started"
QUOTE_EXPIRED = "Quote expired"

#: The seller list, in the researched order. The one entry the research defines
#: as a buyer action is listed here because the research lists it here, and
#: :data:`EVENT_SIDES` states its real side.
SELLER_EVENTS = (
    QUOTE_PUBLISHED,
    QUOTE_APPROVAL_REQUESTED,
    QUOTE_APPROVAL_REJECTED,
    QUOTE_APPROVED,
    QUOTE_RECALLED,
    QUOTE_COUNTERSIGNED,
    QUOTE_REASSIGNED,
    SIGNING_ATTEMPT_FAILED,
    CHAT_STARTED,
    QUOTE_EXPIRED,
)

#: Which side an event belongs to, as the research states it.
#:
#: Every label above appears in exactly one list except ``Chat started``, which
#: the research prints inside the seller list and defines as "buyer started a
#: chat with the closing agent". The definition is more specific than the
#: placement, so the side is the definition's. Recording that here rather than
#: inferring it from list membership is the whole reason this table exists.
EVENT_SIDES = {
    **{event: "buyer" for event in BUYER_EVENTS},
    **{event: "seller" for event in SELLER_EVENTS if event != CHAT_STARTED},
    CHAT_STARTED: "buyer",
}

#: Every event this workflow will record, buyer first.
ALL_EVENTS = tuple(BUYER_EVENTS) + tuple(SELLER_EVENTS)

#: The two sides, as the ``side`` field reads on a stored row.
BUYER = "buyer"
SELLER = "seller"


# --------------------------------------------------------------------------- #
# The one event that counts a view, and the surface it arrived on
# --------------------------------------------------------------------------- #

#: "**Quote viewed:** the buyer has opened the quote using the quote link."
#:
#: This is the only event that increments the View Count, and the next constant
#: is the only thing that decides whether one was counted at all.
VIEW_EVENT = QUOTE_VIEWED

#: The buyer opened the quote through its link. This is a view.
SURFACE_QUOTE_LINK = "quote_link"

#: "If a buyer opens the PDF attached to the email, this doesn't count as a
#: quote view." The event still happens and is still on the timeline; it does
#: not increment the View Count. The distinction is encoded as a surface rather
#: than as a separate event label, because the researched vocabulary has one
#: label for it and a second label would make the timeline disagree with the
#: platform a seller is reading.
SURFACE_EMAIL_PDF = "email_pdf_attachment"

#: Every surface an event can arrive on. A surface this workflow does not know
#: is refused rather than assumed to be a view, so a caller cannot make a count
#: by naming a surface the rules never heard of.
KNOWN_SURFACES = (
    SURFACE_QUOTE_LINK,
    SURFACE_EMAIL_PDF,
    "buyer_portal",
    "seller_console",
    "email",
    "automation",
)


# --------------------------------------------------------------------------- #
# Activity classes
# --------------------------------------------------------------------------- #

#: "Published quotes with activity below this threshold are classified *Low
#: activity*." The researched label is kept verbatim, including the capital L.
ACTIVITY_LOW = "Low activity"

#: The class above the threshold. The research names only the low class and
#: describes the high points as "green", so this label is a recorded decision:
#: a chart that renders a point green with no word beside it tells a seller
#: nothing they can repeat to a colleague.
ACTIVITY_HIGH = "High activity"

#: The class for a quote the threshold cannot be applied to, because no
#: threshold is configured for the workspace.
#:
#: "The activity threshold is configurable" and the research gives no default
#: number, so this workflow ships no number. A quote with no threshold to compare
#: against is not Low activity and not High activity: calling it Low would state
#: a finding the workspace never asked for. Jev chose this over a hardcoded
#: default (audit ``jev-20261013T111655-4412-15602``).
ACTIVITY_UNCLASSIFIED = "Not classified"

ACTIVITY_HIGHEST = ACTIVITY_HIGH
ACTIVITY_LOWEST = ACTIVITY_LOW
ACTIVITY_UNCLASSIFIED_LABEL = ACTIVITY_UNCLASSIFIED

#: Every class, for the vocabulary route and the page's filter.
ACTIVITY_CLASSES = (ACTIVITY_HIGH, ACTIVITY_LOW, ACTIVITY_UNCLASSIFIED)

#: The tone a class renders with, so a chip is never colour alone. The page
#: prints the class name beside the tone, which is what makes the tone safe.
ACTIVITY_TONES = {
    ACTIVITY_HIGH: "success",
    ACTIVITY_LOW: "warning",
    ACTIVITY_UNCLASSIFIED: "neutral",
}


# --------------------------------------------------------------------------- #
# Counters
# --------------------------------------------------------------------------- #
#
# "counters (``Last Activity Date``, ``Activity Count``, ``Activity Count Since
# Last Communication``, ``View Count``) are maintained". Four counters, kept
# separate because the index page reads them as four columns and three of them
# answer different questions.
#
# The researched column names are camel case with spaces. This codebase is
# snake_case, so each constant keeps the researched name in its docstring and
# the key below is the snake_case spelling.

#: Last Activity Date. The most recent event of any kind, as an ISO timestamp.
LAST_ACTIVITY_AT = "last_activity_at"

#: Activity Count. Every recorded event, buyer and seller alike.
ACTIVITY_COUNT = "activity_count"

#: Activity Count Since Last Communication. Events recorded since the last
#: communication, which the research attributes to "connected email channel".
ACTIVITY_SINCE_COMMUNICATION = "activity_count_since_last_communication"

#: View Count. Only events the rules accepted as a view.
VIEW_COUNT = "view_count"

#: The four counters, in the order the index page lists them.
COUNTERS = (LAST_ACTIVITY_AT, ACTIVITY_COUNT, ACTIVITY_SINCE_COMMUNICATION, VIEW_COUNT)

#: The researched column names, served on the vocabulary route so the page
#: renders the researched label beside each figure instead of inventing one.
COUNTER_LABELS = {
    LAST_ACTIVITY_AT: "Last Activity Date",
    ACTIVITY_COUNT: "Activity Count",
    ACTIVITY_SINCE_COMMUNICATION: "Activity Count Since Last Communication",
    VIEW_COUNT: "View Count",
}

#: Where the counters live on the quote record: one nested block under this key
#: in ``records.data``. A nested block keeps a feature's fields off the quote's
#: own key space, so two features can patch the same quote without one of them
#: deleting the other's numbers.
COUNTERS_KEY = "engagement"

#: What the third counter reports when the workspace has no email channel.
#:
#: The research names "connected email channel (for 'since last communication')"
#: and this product has no mail transport, so the figure cannot be supported.
#: The counter is reported as unavailable with this reason rather than as a
#: number derived from outbound events, because an inbound buyer reply is a
#: communication this workflow never records, and a number that overstates the
#: silence produces a follow up that should not have been sent. Jev chose this
#: over falling back to the last recorded send (audit
#: ``jev-20261013T111835-5236-15033``).
SINCE_COMMUNICATION_UNAVAILABLE = "no_email_channel_connected"

#: The first communication a workspace records. Until one does, the third
#: counter has no baseline and reports the same unavailable reason, because
#: "since" a communication that never happened has no answer either way.
SINCE_COMMUNICATION_NO_BASELINE = "no_communication_recorded"


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #

#: The key for the researched threshold setting.
#:
#: "Analyze quote activity level tracking threshold". There is no default value
#: for this constant and none in the rules: a workspace that has not set one
#: classifies nothing. See :data:`ACTIVITY_UNCLASSIFIED`.
HIGH_ACTIVITY_THRESHOLD = "high_activity_threshold"

#: Whether an email channel is connected, which is what makes the third counter
#: answerable at all. It is false in every workspace in this build, and it is a
#: setting rather than a constant so a workspace that connects a channel can
#: record it without a code change.
EMAIL_CHANNEL_CONNECTED = "email_channel_connected"

#: The most recent communication on record, as an ISO timestamp. It is the
#: baseline the third counter counts from, and it is absent in a workspace that
#: has connected no channel.
LAST_COMMUNICATION_AT = "last_communication_at"

#: The settings a workspace may configure. Each is optional and each has its own
#: rule, so a workspace that sets one and not the others gets the ones it set.
ANALYSIS_SETTINGS = (
    HIGH_ACTIVITY_THRESHOLD,
    EMAIL_CHANNEL_CONNECTED,
    LAST_COMMUNICATION_AT,
)

#: The smallest and largest threshold a workspace may set. The lower bound stops
#: a threshold of zero, which would classify every quote high and make the chart
#: say nothing. The upper bound stops a value that no quote could reach, which
#: would classify every quote Low.
MIN_THRESHOLD = 1
MAX_THRESHOLD = 1000


# --------------------------------------------------------------------------- #
# Follow-up reasons
# --------------------------------------------------------------------------- #
#
# "**Recommended follow-ups,** view recommended follow-ups for quotes based on
# activity on the quote, and expiration dates (e.g., quotes with no activity in
# a long time, or quotes that'll expire soon)."
#
# Two reasons are named and two are built. The two built ones are the same two
# named conditions with a second edge each, because the research says
# "e.g." and the examples are the shape rather than the whole list.

#: No activity for the configured window.
REASON_NO_ACTIVITY = "no_activity_recently"

#: The expiration date falls inside the configured window.
REASON_EXPIRING_SOON = "expiring_soon"

#: The expiration date has passed and the buyer has not accepted. The research
#: names the pair "quotes that'll expire soon", and a quote past its date with
#: no acceptance is that condition plus one edge.
REASON_EXPIRED_UNACCEPTED = "expired_unaccepted"

#: The buyer has not answered since the last communication and the seller has
#: not touched the quote. This is "no activity in a long time" measured over the
#: communication rather than over any event, which is the only way the named
#: reason can use the one figure the research attributes to the email channel.
REASON_NO_REPLY = "no_reply_since_last_communication"

FOLLOW_UP_REASONS = (
    REASON_NO_ACTIVITY,
    REASON_EXPIRING_SOON,
    REASON_EXPIRED_UNACCEPTED,
    REASON_NO_REPLY,
)

#: How each reason reads in a sentence, with no number baked in, because the
#: windows behind the reasons are settings and a workspace may set them.
FOLLOW_UP_REASON_LABELS = {
    REASON_NO_ACTIVITY: "No activity for a while",
    REASON_EXPIRING_SOON: "Expires soon",
    REASON_EXPIRED_UNACCEPTED: "Expired and not accepted",
    REASON_NO_REPLY: "No reply since the last communication",
}

#: Which counter each reason is measured against, so a reader can see what a
#: recommendation is made of.
FOLLOW_UP_REASON_BASIS = {
    REASON_NO_ACTIVITY: LAST_ACTIVITY_AT,
    REASON_EXPIRING_SOON: "expires_at",
    REASON_EXPIRED_UNACCEPTED: "expires_at",
    REASON_NO_REPLY: LAST_ACTIVITY_AT,
}

#: The two states the research names for a quote whose send can be renewed.
#: "generat[es] a new ``Quote sent`` activity and (if previously expired/recalled)
#: a new send." These are the event labels that put a quote in one of those
#: states.
RESENDABLE_STATES = (QUOTE_EXPIRED, QUOTE_RECALLED)

#: The quote statuses that mean the same two things.
#:
#: They are kept apart from :data:`RESENDABLE_STATES` because they are compared
#: against different things. An event label is checked against the labels already
#: recorded against the quote, and a status is checked against the quote's own
#: field. A quote that says ``EXPIRED`` and has no ``Quote expired`` event has
#: still expired, and one that recorded the event and carries a stale status has
#: too, so both are read.
RESENDABLE_STATUSES = ("EXPIRED", "RECALLED")


# --------------------------------------------------------------------------- #
# Quote fields this workflow reads
# --------------------------------------------------------------------------- #
#
# A team may name any of these fields anything. Each list is tried in order and
# the first key the quote carries wins, so a workspace that keeps its expiration
# date somewhere the list does not name is not blocked by this workflow.

#: Where a quote may keep its expiration date. Read-only. Another workflow owns
#: writing it, and this one refuses to set one so it cannot expire a quote it
#: does not own.
EXPIRATION_FIELDS = ("expires_at", "expiration_date", "hs_expiration_date", "expiry_date")

#: Where a quote may keep its owner, for the index-page owner filter.
OWNER_FIELDS = ("owner", "rep", "assigned_to", "hs_owner_id")

#: Where a quote may keep its signing status, for the index-page signing filter.
#: The signing workflow owns writing it; this one only filters on it.
SIGNING_STATUS_FIELDS = ("signing_status", "hs_signing_status", "signature_status")

#: The quote statuses that mean the buyer answered. "Quotes can have the
#: following statuses, with a corresponding color indicator: ... **Pending
#: approval** ... **Changes requested** ... **Accepted**." A quote the buyer
#: accepted is never recommended for a follow up, so this set is the first thing
#: the follow-up rules test.
ACCEPTED_STATUSES = ("ACCEPTED", "SIGNED", "COUNTERSIGNED", "CLOSED_WON")

#: The quote statuses where nothing has reached the buyer yet, so a follow up is
#: not a follow up.
#:
#: The panel the research describes lives under "Analyze your published quotes"
#: and its action is "**Send a follow up** which opens the **Send quote** dialog".
#: A draft or a rejected quote has never been published, shared or emailed, so
#: emailing it is a first send rather than a follow up, and recommending it puts a
#: card on a seller's list that says "no activity in a long time" about a quote
#: that was only created a moment ago and was never visible to anybody.
#:
#: ``REJECTED`` and its researched synonym are both here because the researched
#: status vocabulary is not enumerated and a workspace may spell it either way.
NOT_CHASABLE_STATUSES = ("DRAFT", "REJECTED", "APPROVAL_REJECTED", "CHANGES_REQUESTED")
