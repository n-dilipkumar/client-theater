"""Every researched term WF-095 enforces against, with the evidence it came from.

The specification is ``docs/research/raw/quoting-proposals.md`` section 10, quoted in full in
issue 166. Every value below is either quoted from that document or derived from a quote by a
derivation recorded in :mod:`dsr.quote_acceptance.inferences`. Nothing here is a house
opinion.

Where this package's rules live
-------------------------------

The rules live in :mod:`dsr.quote_acceptance`, which owns collection names prefixed
``wf095_``. The package was created rather than appended to an existing one, on the Jev
decision recorded at ``GET /api/wf-095/decisions`` as
:data:`~dsr.quote_acceptance.inferences.DERIVED_NEW_PACKAGE_QUOTE_ACCEPTANCE`, audit
``jev-20261005T075612-8560-72925``, confidence 1.00. The short reason: no package on main
holds acceptance-method or signing-status vocabulary, and two workflow branches appending to
one package initializer collide on the same lines.

What this build does not claim
------------------------------

Three of the specification's own facts are marked inferred by the research, and the markers
are the specification's:

* the countersigners are "from your organisation", so the pool is the room's own users;
* the seller reads *E-signature usage this month* from a pooled, subscription-derived quota
  that the research never bounds numerically;
* "automatically create contracts from accepted quotes" is a downstream consumer, and issue
 166 names WF-099 as that consumer rather than this ticket.

So :data:`QUOTA_UNSPECIFIED` and :data:`COUNTERSIGNER_POOL_IS_THIS_ROOM` carry those three to
every response. A reader of any answer from this workflow learns which parts are evidenced
and which parts this build assumed.

Who owns the identity duty
-------------------------

The specification quotes the vendor and the quote is unambiguous: "It is your responsibility
to verify the identity of any user who views a document within the signing session. Some use
cases legally require Identity Verification to be enabled." So this room owns that decision.
:data:`AUTHENTICATION_OWNER` says so in every response, and nothing here delegates it to a
provider by default.
"""

from __future__ import annotations

# --------------------------------------------------------------------------- #
# Collections
# --------------------------------------------------------------------------- #
#
# Namespaced with the ticket, because every feature shares one `records` table and `find()`
# matches on collection before it matches on anything else.

ENVELOPE_COLLECTION = "wf095_signing_envelope"
SIGNER_COLLECTION = "wf095_signer"
EVENT_COLLECTION = "wf095_signature_event"
QUOTA_COLLECTION = "wf095_esign_quota"

#: The two collections this workflow reads as data and never writes through its own
#: acceptance routes. WF-086 provisions the quote and WF-093 provisions the document, and
#: they are spelled here exactly as those packages spell them so one reader can see the
#: dependency without opening either package.
#:
#: The feature module writes a demo row into each so the flow is demonstrable before those
#: tickets land. A signature must never rewrite the document it is bound to, so the
#: acceptance routes in this package never touch either collection.
QUOTES_COLLECTION = "wf086_quote"
DOCUMENTS_COLLECTION = "wf093_document"

ALL_COLLECTIONS = (
    ENVELOPE_COLLECTION,
    SIGNER_COLLECTION,
    EVENT_COLLECTION,
    QUOTA_COLLECTION,
)

#: The payload-side twin of the envelope's ``room_id``.
#:
#: Not ``room_id``, and that is not a style preference. ``room_id`` is part of the record
#: *envelope*, so the store strips it out of ``data`` before the dynamic index is built. A
#: record that stored its room there would be unfilterable by ``find()``, and a filter by
#: room that silently returns nothing is the kind of defect that ships.
ROOM_REF = "room_ref"

# --------------------------------------------------------------------------- #
# The acceptance method
# --------------------------------------------------------------------------- #
#
# The research names the three methods on one property: "``hs_acceptance_method`` =
# ``clickwrap`` | ``esignature`` | ``print_and_sign``". The seller's sidebar calls the
# middle one *E-signature* and the third *Print and sign*, and it calls the first *Accept
# without signature*. Both spellings of every method are accepted, because a caller who read
# the vendor documentation sends ``esignature`` and a caller who read this product's prose
# sends ``e_signature``.

METHOD_ESIGNATURE = "esignature"
METHOD_CLICKWRAP = "clickwrap"
METHOD_PRINT_AND_SIGN = "print_and_sign"

ACCEPTANCE_METHODS: tuple[str, ...] = (
    METHOD_ESIGNATURE,
    METHOD_CLICKWRAP,
    METHOD_PRINT_AND_SIGN,
)

ACCEPTANCE_METHOD_LABELS: dict[str, str] = {
    METHOD_ESIGNATURE: "E-signature",
    METHOD_CLICKWRAP: "Accept without signature",
    METHOD_PRINT_AND_SIGN: "Print and sign",
}

#: The researched property name, plus the product's own spelling. ``hs_acceptance_method``
#: is what the vendor documentation names and what an integration would send, so it wins
#: when both are present.
ACCEPTANCE_METHOD_FIELD = "hs_acceptance_method"
ACCEPTANCE_METHOD_PLAIN = "acceptance_method"

#: "``hs_esign_num_signers_required``". The number of signatures the envelope is waiting
#: for, and it is a field on the quote rather than a derived count, because a seller may
#: require fewer signatures than the envelope carries.
SIGNERS_REQUIRED_FIELD = "hs_esign_num_signers_required"

#: "``hs_clickwrap_accepted_by``". The contact that accepted without signing. It is only
#: meaningful for ``clickwrap``, and a reader must never see it set on an e-signature quote.
ACCEPTED_BY_FIELD = "hs_clickwrap_accepted_by"

#: "``hs_payment_status``". Written on acceptance, so an accepted quote carries the payment
#: status its own acceptance produced.
PAYMENT_STATUS_FIELD = "hs_payment_status"

#: The contact association type the research names for a signer: "contact association type
#: 702 (signer)". Read, and recorded, rather than reinterpreted.
SIGNER_ASSOCIATION_TYPE = "702"

# --------------------------------------------------------------------------- #
# Who signs, and in what order
# --------------------------------------------------------------------------- #

ROLE_BUYER = "buyer"
ROLE_COUNTERSIGNER = "countersigner"

SIGNER_ROLES: tuple[str, ...] = (ROLE_BUYER, ROLE_COUNTERSIGNER)

SIGNER_ROLE_LABELS: dict[str, str] = {
    ROLE_BUYER: "Buyer contact required to sign",
    ROLE_COUNTERSIGNER: "Countersigner",
}

#: Two parties, so a signature index above two is refused rather than accepted as a typo.
MAX_SIGNERS = 2

#: The seller picks countersigners "from your organisation", so the pool is this room's own
#: users. The research names no provider and no directory, and the pool is the one thing
#: this build can actually resolve.
COUNTERSIGNER_POOL_IS_THIS_ROOM = (
    "Countersigners are drawn from your own users. This build resolves that pool from this "
    "room's own users, because the research names no directory and no external provider."
)

#: "tick the buyer contacts under **Buyer contacts required to sign**". So a seller may
#: require zero buyer contacts only under ``clickwrap``, and at least one under
#: ``esignature``.
BUYER_SIGNERS_FIELD = "buyer_signers"

#: "optionally enable **Quote signer(s) can reassign**". Per-quote, and off by default.
REASSIGN_ALLOWED_FIELD = "reassign_allowed"

#: A sender who has signed is not reassignable. The research says "**Reassign this quote
#: signer**" appears in the buyer's own acceptance step, so the reassignment happens before
#: that party signs.
REASSIGN_AFTER_SIGN_REFUSAL = (
    "This quote signer has already signed, so the signature can no longer be reassigned. A "
    "buyer may reassign their own signing step before they sign it."
)

# --------------------------------------------------------------------------- #
# The signing status machine
# --------------------------------------------------------------------------- #
#
# The data flow names the four states in order: "`Pending signature` -> `Viewed - pending
# signature` -> `Pending countersignature` -> `Accepted`". The state names are stored in the
# vendor's own spelling because that is what a seller reads on the index page, and the
# labels carry the product's capitalisation.

STATUS_PENDING_SIGNATURE = "pending_signature"
STATUS_VIEWED_PENDING_SIGNATURE = "viewed_pending_signature"
STATUS_PENDING_COUNTERSIGNATURE = "pending_countersignature"
STATUS_ACCEPTED = "accepted"

SIGNING_STATUSES: tuple[str, ...] = (
    STATUS_PENDING_SIGNATURE,
    STATUS_VIEWED_PENDING_SIGNATURE,
    STATUS_PENDING_COUNTERSIGNATURE,
    STATUS_ACCEPTED,
)

SIGNING_STATUS_LABELS: dict[str, str] = {
    STATUS_PENDING_SIGNATURE: "Pending signature",
    STATUS_VIEWED_PENDING_SIGNATURE: "Viewed - pending signature",
    STATUS_PENDING_COUNTERSIGNATURE: "Pending countersignature",
    STATUS_ACCEPTED: "Accepted",
}

#: The one step each status allows from, and the event that causes it. ``None`` means the
#: status is terminal. This is the whole state machine, and
#: :func:`dsr.quote_acceptance.rules.next_status` is the only function that reads it.
STATUS_TRANSITIONS: dict[str, tuple[str | None, str]] = {
    STATUS_PENDING_SIGNATURE: (STATUS_VIEWED_PENDING_SIGNATURE, "viewed"),
    STATUS_VIEWED_PENDING_SIGNATURE: (STATUS_PENDING_COUNTERSIGNATURE, "buyer_signed"),
    STATUS_PENDING_COUNTERSIGNATURE: (STATUS_ACCEPTED, "countersigned"),
    STATUS_ACCEPTED: (None, "accepted"),
}

#: The statuses that mean the envelope still needs a signature, and therefore the statuses
#: in which the signature widget is live.
OPEN_STATUSES: tuple[str, ...] = (
    STATUS_PENDING_SIGNATURE,
    STATUS_VIEWED_PENDING_SIGNATURE,
    STATUS_PENDING_COUNTERSIGNATURE,
)

#: The status a quote carrying an e-signature envelope holds before anyone has opened it.
#: The research names the sequence, so it is reproduced rather than invented.
SIGNING_STATUS_FIELD = "signing_status"

# --------------------------------------------------------------------------- #
# Identity verification on the envelope
# --------------------------------------------------------------------------- #

#: "Buyers have one hour to complete the signature process after clicking **Verify email**."
#: The window is the evidence's, not a house default, and it is measured from the moment the
#: verification was requested rather than from the moment the envelope was sent.
VERIFICATION_WINDOW_MINUTES = 60

VERIFICATION_WINDOW_QUOTE = (
    "Buyers have one hour to complete the signature process after clicking Verify email."
)

#: "clicked **Verify email** in the *Acceptance* section". The verification step is a request
#: the buyer makes, and a buyer who never requests it cannot sign when verification is on.
VERIFICATION_REQUIRED = "identity_verification_required"

#: The verification is an emailed one-time link, so the token is minted on request and the
#: window opens then.
VERIFICATION_REQUEST_FIELD = "verification_requested_at"

#: Countersigners are emailed automatically when the buyer signs, and the research says a
#: countersigner who starts first does not have to verify: "Countersigners who start first
#: do not need to verify." So the verification gate binds the buyer, and a countersigner who
#: signs before the buyer is not asked for it.
VERIFICATION_BINDS_BUYER_ONLY = "Countersigners who start first do not need to verify."

#: The signed PDF that is bound to the envelope is capped, and the evidence is exact:
#: "Quote PDFs larger than 40 MB may not be successfully verified or signed."
PDF_SIZE_CAP_MB = 40

PDF_SIZE_CAP_QUOTE = "Quote PDFs larger than 40 MB may not be successfully verified or signed."

#: A signature is drawn, typed or uploaded, so all three are accepted and none is required
#: over the others.
SIGNATURE_MODES: tuple[str, ...] = ("draw", "type", "upload")

SIGNATURE_MODE_LABELS: dict[str, str] = {
    "draw": "Draw",
    "type": "Type",
    "upload": "Upload",
}

# --------------------------------------------------------------------------- #
# Quota
# --------------------------------------------------------------------------- #

#: "An e-signature will count toward the limit as soon as the e-signature option is turned
#: on for a published quote. The quote doesn't need to be signed to apply to the signature
#: limit."
QUOTA_CONSUMED_ON_ENABLE = (
    "An e-signature will count toward the limit as soon as the e-signature option is turned "
    "on for a published quote. The quote doesn't need to be signed to apply to the signature "
    "limit."
)

#: "if a published quote with e-signatures enabled requires three signatures, this only counts
#: as one usage toward your limit." So the count is one per envelope, never one per signer.
#: This build has two parties, so a two-party envelope is still one usage.
QUOTA_COUNTS_ENVELOPE_NOT_SIGNER = (
    "if a published quote with e-signatures enabled requires three signatures, this only "
    "counts as one usage toward your limit."
)

#: "Resending after expiry/recall costs another." A resend is a new envelope, so it is a new
#: usage.
QUOTA_COUNTS_EACH_RESEND = "Resending after expiry/recall costs another."

#: The month the usage resets on: "limits are pooled per account by subscription and seat
#: count and reset on the 1st".
QUOTA_RESET_DAY = 1

#: The research never states a number, so no limit is asserted. ``limit: None`` is the
#: honest answer and is what every response carries.
QUOTA_UNSPECIFIED = (
    "The research states that limits are pooled per account by subscription and seat count "
    "and reset on the 1st, and it states no number. This build counts usage and reports no "
    "ceiling, because inventing one would be a vendor claim the sources do not support."
)

#: The countersigners and the seal of the signed document both name a vendor the research
#: cites: "HubSpot's e-sign feature is powered by Dropbox Sign (formerly HelloSign)."
SIGNING_PROVIDER = "dropbox_sign"

#: The research names the document model the signature is bound to only indirectly, through
#: WF-093: "the drawn/typed/uploaded signature is bound to the quote document and a signature
#: field record is written". The signature field record is therefore this workflow's own row,
#: and the document it binds to is WF-093's ``wf093_document``.
SIGNATURE_FIELD_RECORD = "a signature field record is written"

#: "if you download a signed quote, Dropbox Sign removes links from any hyperlinked text
#: included in the *Cover letter*, *Executive summary*, or *Terms* sections from the PDF
#: version." So an export is lossy in a named way, and this build says so rather than
#: pretending the PDF is the document.
PDF_EXPORT_IS_LOSSY_QUOTE = (
    "if you download a signed quote, Dropbox Sign removes links from any hyperlinked text "
    "included in the Cover letter, Executive summary, or Terms sections from the PDF version."
)

#: The attachment rule constrains the acceptance configuration: "Attachments can be marked as
#: *In signing* to be included in the signing envelope of an e-signature. If an attachment is
#: marked *In signing*, e-signature must be used for the quote." So an envelope with an
#: in-signing attachment refuses the other two methods.
IN_SIGNING_ATTACHMENT_FORCES_ESIGNATURE = (
    "Attachments can be marked as In signing to be included in the signing envelope of an "
    "e-signature. If an attachment is marked In signing, e-signature must be used for the "
    "quote."
)

#: The contract is downstream, not here. The data flow's last clause is a consumer this
#: ticket does not implement, and issue 166 says so: WF-099 creates the contract.
CONTRACT_IS_DOWNSTREAM = (
    "if Automatically create contracts from accepted quotes is on, a contract is created. That "
    "consumer is WF-099, which is downstream of this ticket. This workflow writes the "
    "acceptance this consumer reads."
)

# --------------------------------------------------------------------------- #
# The activity log
# --------------------------------------------------------------------------- #
#
# The research names four activities and no others: "Quote buyer signed", "Quote countersigned",
# "Quote reassigned", "Signing attempt failed".

ACTIVITY_BUYER_SIGNED = "quote_buyer_signed"
ACTIVITY_COUNTERSIGNED = "quote_countersigned"
ACTIVITY_REASSIGNED = "quote_reassigned"
ACTIVITY_ATTEMPT_FAILED = "signing_attempt_failed"

ACTIVITY_LABELS: dict[str, str] = {
    ACTIVITY_BUYER_SIGNED: "Quote buyer signed",
    ACTIVITY_COUNTERSIGNED: "Quote countersigned",
    ACTIVITY_REASSIGNED: "Quote reassigned",
    ACTIVITY_ATTEMPT_FAILED: "Signing attempt failed",
}

#: The activities are append-only. An activity row is never edited and never deleted, which
#: is why a reassignment writes a new row rather than changing the previous one.
ACTIVITIES: tuple[str, ...] = (
    ACTIVITY_BUYER_SIGNED,
    ACTIVITY_COUNTERSIGNED,
    ACTIVITY_REASSIGNED,
    ACTIVITY_ATTEMPT_FAILED,
)

#: The event one signature attempt makes when the buyer opens the verification link. It is
#: not called ``verified`` because the token is minted before anyone has verified anything:
#: minting is the buyer clicking *Verify email*, and proving is a later step.
EVENT_VERIFICATION_REQUESTED = "verification_requested"

#: The activity each signature event writes.
#:
#: An empty value means the event writes no activity row. ``viewed`` and
#: ``verification_requested`` are empty because neither is among the four activities the
#: research names. Both still advance the signing status or unlock the widget, so a viewer is
#: visible on the board without inventing a fifth activity.
EVENT_ACTIVITIES: dict[str, str] = {
    "viewed": "",
    EVENT_VERIFICATION_REQUESTED: "",
    "buyer_signed": ACTIVITY_BUYER_SIGNED,
    "countersigned": ACTIVITY_COUNTERSIGNED,
    "reassigned": ACTIVITY_REASSIGNED,
    "attempt_failed": ACTIVITY_ATTEMPT_FAILED,
}

#: The events that write an activity row, in the order the research lists them.
ACTIVITY_EVENTS: tuple[str, ...] = (
    "buyer_signed",
    "countersigned",
    "reassigned",
    "attempt_failed",
)

# --------------------------------------------------------------------------- #
# What a buyer sees after the last signature
# --------------------------------------------------------------------------- #

#: "the customer receives a copy of the signed document (link expires with the quote) plus a
#: PDF. The seller can **Download** the signed PDF from the index page." So the sealed copy
#: and the seller's download are one artefact read two ways, and the link's life is the
#: quote's life.
SEALED_COPY_EXPIRES_WITH_QUOTE = (
    "the customer receives a copy of the signed document (link expires with the quote) plus a PDF"
)

#: Every response from this workflow carries this. The evidence assigns the decision to the
#: integrator and the quote is the integrator.
AUTHENTICATION_OWNER = (
    "It is your responsibility to verify the identity of any user who views a document "
    "within the signing session. Some use cases legally require Identity Verification to be "
    "enabled."
)
