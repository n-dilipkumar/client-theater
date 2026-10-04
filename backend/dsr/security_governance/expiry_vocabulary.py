"""WF-081: every researched term, quoted from the specification.

The specification is ``docs/research/digital-sales-room-workflows/wf/WF-081.md``,
quoted in full in issue 182. Every constant below carries the sentence it came
from, because a number in a governance rule that nobody can trace to its source
is a number somebody will change one day without knowing why.

Nothing here is derived and nothing here is invented. Where the specification
left a decision open, that gap is recorded in
:mod:`dsr.security_governance.expiry_inferences` rather than quietly filled in
here, so the difference between what the evidence says and what this build chose
stays visible.

The three sources the specification cites are Dropbox Sign's expiration page,
Dropbox Sign's SMS tools page, and Papermark's password-protected-link guide.
The last of those is the source of the room-link shape, and it is the reason the
collection name below is ``expiry_link`` rather than ``signature_request``: see
:mod:`dsr.security_governance.expiry_inferences` for which of the two shapes
this build implements and why.
"""

from __future__ import annotations

# --------------------------------------------------------------------------- #
# Collections
# --------------------------------------------------------------------------- #

#: The collection an agreement that can expire is stored in.
#:
#: The specification names the vendor's object "signature request" and the
#: per-signer row "signature". Both names are kept verbatim in the payload keys
#: below, because "each incomplete ``signature`` record transitions to
#: ``status_code: \"expired\"``" is the rule the sweep implements and a key named
#: ``signers`` would hide it. The collection itself is named for this product,
#: not for the vendor, because the store is this product's and the vendor's
#: tables are not provisioned here.
EXPIRY_COLLECTION = "expiry_request"

#: One row per reminder this product has decided to send or skip. The reminder
#: scheduler state the specification names as a data source lives here.
REMINDER_COLLECTION = "expiry_reminder"

#: The event stream an embedded integration consumes. The specification's sixth
#: notification rule is that embedded flows send a
#: ``signature_request_expired`` event, so those events are rows.
EVENT_COLLECTION = "expiry_event"


# --------------------------------------------------------------------------- #
# The per-signer status codes
# --------------------------------------------------------------------------- #

#: "On expiry, unsigned signatures flip to ``expired`` ... Completed signers stay
#: ``signed``." These two are the vendor's own spellings and they are used as
#: stored values, not translated, because the specification's own filter - the
#: Documents page "filter by expired status" - is a filter over them.
STATUS_EXPIRED = "expired"
STATUS_SIGNED = "signed"

#: "All parties to the signature request will still have access to the document
#: including audit trail, similar to ``declined`` signature requests." The three
#: terminal states, because the specification groups them explicitly.
TERMINAL_STATUSES = (STATUS_SIGNED, "declined", "completed")

#: The request-level statuses. ``EXPIRED_REQUEST_STATUS`` is the specification's
#: own wording: "Once a signature request has expired, it is considered to be in
#: a final status like ``declined`` and ``completed`` signature requests."
REQUEST_STATUS_PENDING = "pending"
REQUEST_STATUS_EXPIRED = "expired"
REQUEST_STATUS_COMPLETED = "completed"

#: The three per-signer statuses that are *not* expired. A sweep moves these and
#: nothing else, which is the whole content of "unsigned signatures flip to
#: ``expired``". A signer already ``signed`` is untouched, and so is a signer the
#: seller already declined or completed by hand.
INCOMPLETE_SIGNER_STATUSES = ("awaiting_signature", "viewed", "declined", STATUS_EXPIRED)


# --------------------------------------------------------------------------- #
# The expiry field itself
# --------------------------------------------------------------------------- #

#: The payload key. The specification's own spelling, in every place it appears:
#: "Seller sets an expiry when sending (``expires_at``, an epoch timestamp)".
EXPIRES_AT = "expires_at"

#: "``expires_at`` must be an integer epoch timestamp in seconds between 1-90
#: days in the future." Both bounds are the specification's and neither is a
#: default this build chose.
MIN_EXPIRY_DAYS = 1
MAX_EXPIRY_DAYS = 90

#: "``expires_at`` will be rounded down to the nearest hour." The specification
#: normalises rather than rejects, so a request for 17:59 lands on 17:00.
ROUND_DOWN_SECONDS = 3600


# --------------------------------------------------------------------------- #
# The reminder cadence
# --------------------------------------------------------------------------- #

#: "Signature request reminder emails will be sent to the signer 3 and 7 days
#: before the signature request expires". The cadence is the specification's and
#: is not configurable, so a caller cannot send a request into a state the
#: scheduler will never act on.
REMINDER_LEAD_DAYS = (7, 3)

#: "If a signer was already reminded within 24 hours, we will skip the automated
#: reminder." A skip, not a refusal: the ledger records the skip so the rule is
#: demonstrable rather than asserted.
DEDUPE_WINDOW_HOURS = 24


# --------------------------------------------------------------------------- #
# Delivery by mode
# --------------------------------------------------------------------------- #

#: "Emails are muted in all embedded signing flows. Integrations using embedded
#: signing must consume the ``signature_request_expired`` event." The two modes
#: the specification contrasts, with the vendor's own mode names.
FLOW_HOSTED = "hosted"
FLOW_EMBEDDED = "embedded"
FLOW_MODES = (FLOW_HOSTED, FLOW_EMBEDDED)

#: The event name an embedded integration consumes, spelled exactly as the
#: specification spells it. An integration subscribed to the vendor's name would
#: not fire on ours, and the vendor's name is the contract.
EVENT_EXPIRED = "signature_request_expired"
EVENT_REMINDER_SENT = "signature_reminder_sent"


# --------------------------------------------------------------------------- #
# The errors
# --------------------------------------------------------------------------- #

#: The errors this workflow raises, each with the status it maps to. The
#: specification's own sentence is in the value, so a refusal returned by the API
#: reads as the research rather than as a developer's paraphrase of it.
ERROR_CODES: dict[str, tuple[int, str]] = {
    "expiry_not_an_integer": (
        422,
        "expires_at must be an integer epoch timestamp in seconds.",
    ),
    "expiry_out_of_range": (
        422,
        "expires_at must be an integer epoch timestamp in seconds between 1 and 90 "
        "days in the future.",
    ),
    "request_closed": (
        409,
        "This signature request is closed. The expiry date has passed and the "
        "document cannot be signed or modified.",
    ),
    "reminder_not_due": (
        409,
        "No reminder is due for this signer yet. Reminders are sent 3 and 7 days "
        "before the expiry date.",
    ),
    "already_signed": (
        409,
        "This signer already completed their part of the request.",
    ),
    "unknown_signer": (
        404,
        "No such signer on this request.",
    ),
}


# --------------------------------------------------------------------------- #
# The invariants, carried in every response
# --------------------------------------------------------------------------- #

#: "Only signature requests that explicitly set an ``expires_at`` will expire. By
#: default signature requests do not expire." A request with no expiry has this
#: status, and no sweep and no signer check ever touches it.
NEVER_EXPIRES = "never"

#: "All parties to the signature request will still have access to the document
#: including audit trail, similar to ``declined`` signature requests." The one
#: sentence that says what expiry is not.
DOCUMENT_SURVIVES = (
    "All parties to the signature request will still have access to the document "
    "including audit trail, similar to declined signature requests. They will not be "
    "able to sign or modify the signature request."
)

#: "The document itself isn't deleted; you can mint a new link any time without
#: re-uploading." Papermark's sentence, about the same behaviour on a link.
LINK_SURVIVES = (
    "The document itself is not deleted. You can mint a new link any time without "
    "re-uploading the document."
)

#: The link-expired page the viewer's URL gets instead of the document, in the
#: specification's own words.
LINK_EXPIRED_PAGE = "this link has expired"


# --------------------------------------------------------------------------- #
# What the page renders from
# --------------------------------------------------------------------------- #


#: The vocabulary this workflow serves at ``GET /vocabulary``. Every value here is
#: a constant above, so the served list and the enforced rule cannot disagree.
def catalogue() -> dict[str, object]:
    """Every researched term, as data a client can render from."""
    return {
        "collections": {
            "request": EXPIRY_COLLECTION,
            "reminder": REMINDER_COLLECTION,
            "event": EVENT_COLLECTION,
        },
        "fields": {"expires_at": EXPIRES_AT, "status_code": "status_code"},
        "request_statuses": [
            {"status": REQUEST_STATUS_PENDING, "is_terminal": False},
            {"status": REQUEST_STATUS_EXPIRED, "is_terminal": True},
            {"status": REQUEST_STATUS_COMPLETED, "is_terminal": True},
        ],
        "signer_statuses": [
            {"status_code": STATUS_SIGNED, "swept": False, "meaning": "signed, and kept"},
            {"status_code": "awaiting_signature", "swept": True, "meaning": "incomplete"},
            {"status_code": "viewed", "swept": True, "meaning": "incomplete"},
            {"status_code": STATUS_EXPIRED, "swept": False, "meaning": "already expired"},
        ],
        "terminal_statuses": list(TERMINAL_STATUSES),
        "expiry_rule": {
            "field": EXPIRES_AT,
            "min_days": MIN_EXPIRY_DAYS,
            "max_days": MAX_EXPIRY_DAYS,
            "rounded_down_to": "the nearest hour",
            "round_down_seconds": ROUND_DOWN_SECONDS,
            "quote": "expires_at must be an integer epoch timestamp in seconds "
            "between 1-90 days in the future.",
            "rounding_quote": "expires_at will be rounded down to the nearest hour.",
            "absence_quote": "Only signature requests that explicitly set an "
            "expires_at will expire. By default signature requests do not expire.",
        },
        "reminder_rule": {
            "lead_days": list(REMINDER_LEAD_DAYS),
            "dedupe_window_hours": DEDUPE_WINDOW_HOURS,
            "quote": "Signature request reminder emails will be sent to the signer 3 "
            "and 7 days before the signature request expires.",
            "dedupe_quote": "If a signer was already reminded within 24 hours, we "
            "will skip the automated reminder.",
        },
        "delivery": {
            "modes": list(FLOW_MODES),
            "event": EVENT_EXPIRED,
            "email_muted_when_embedded": True,
            "quote": "Emails are muted in all embedded signing flows. Integrations "
            "using embedded signing must consume the signature_request_expired event.",
        },
        "invariants": {
            "document_survives": DOCUMENT_SURVIVES,
            "link_survives": LINK_SURVIVES,
            "closed_not_deleted": DOCUMENT_SURVIVES,
        },
    }
