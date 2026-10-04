"""Every researched term WF-080 enforces against, with the evidence it came from.

This is the researched specification for WF-080 made executable, and it is the only
place a constant named after the specification lives. The specification is
``docs/research/digital-sales-room-workflows/wf/WF-080.md``, quoted in full in issue
128. Every value below is either quoted from that document or derived from a quote by
the arithmetic shown beside it. Nothing here is a house opinion.

The four sentences that govern the whole workflow
-------------------------------------------------

**Subscribe to the ready event, never poll.** The extensibility note says it directly:
"subscribe to the *ready* event rather than polling status". So the trigger this
workflow requires is :data:`PDF_READY_TRIGGER` and nothing in this package sleeps,
schedules, or asks a document how it is doing.

**202 is an outcome, not a failure.** The evidence quotes the vendor: "The signed
document file is not ready yet... Retry after the indicated number of seconds. No
response body is returned." So a fetch while the PDF is still being produced answers
202, sets ``Retry-After``, and sends no body at all. :data:`BACK_PRESSURE` is a state
this workflow stores, not an exception it raises into a generic failure page.

**Dedupe on the delivery id, not on the document id.** The evidence says the
``X-PandaDoc-Webhook-Event-Id`` header exists "to process each webhook notification
once... even when PandaDoc retries delivery". A document id therefore cannot be the
dedupe key: one document produces several notifications, and a document id as the key
would drop every delivery after the first.

**The two download endpoints are not interchangeable.** The guide says it plainly:
"the ``/download-protected`` endpoint always returns the same digitally sealed PDF
file, while ``/download`` allows for watermark customization." Sealed is byte-stable
and immutable. Plain is editable. They are two variants with different rules, never
one variant with a flag.

Environment gating is the fourth sourced constraint, and it is explicit rather than
implied: "Production key only - This endpoint only works with a Production key. You'll
get a 401 Unauthorized error when trying to use a Sandbox key." So
:data:`SEALED_ENVIRONMENT` is ``production`` and nothing else, and the sandbox case is
a named outcome rather than a configuration mistake.
"""

from __future__ import annotations

# --------------------------------------------------------------------------- #
# Collections
# --------------------------------------------------------------------------- #
#
# Namespaced, because every feature shares one `records` table and `find()` matches on
# collection before it matches on anything else. The five collections are the five
# things the specification's data-sources line names, kept apart so a caller can count
# one without counting the others.

SUBSCRIPTION_COLLECTION = "wf080_webhook_subscription"
DOCUMENT_COLLECTION = "wf080_executed_document"
ARTIFACT_COLLECTION = "wf080_vault_artifact"
DELIVERY_COLLECTION = "wf080_delivery"
ATTEMPT_COLLECTION = "wf080_fetch_attempt"

ALL_COLLECTIONS = (
    SUBSCRIPTION_COLLECTION,
    DOCUMENT_COLLECTION,
    ARTIFACT_COLLECTION,
    DELIVERY_COLLECTION,
    ATTEMPT_COLLECTION,
)

# --------------------------------------------------------------------------- #
# The trigger
# --------------------------------------------------------------------------- #
#
# The one event name the specification quotes: "This webhook is triggered when a
# document has been completed and a PDF has been generated and securely saved to
# PandaDoc's e-vault." The developer's dashboard names the same event in prose: "PDF of
# completed document available for download".

PDF_READY_TRIGGER = "document_completed_pdf_ready"

#: The trigger this workflow requires a subscription to carry. A subscription without it
#: is refused rather than created, because a subscription that cannot hear the ready
#: event is a room waiting for a PDF that will never be announced.
REQUIRED_TRIGGERS = (PDF_READY_TRIGGER,)

#: The vendor's event name as it arrives inside the payload. The specification writes
#: the event in prose and in the subscription's ``triggers`` array, and the handler
#: "extracts the document ``id`` from the payload", so the payload carries the name
#: under a key of its own. Read defensively and record which key was used.
EVENT_KEYS = ("event", "eventType", "event_type")

# --------------------------------------------------------------------------- #
# Dedupe
# --------------------------------------------------------------------------- #
#
# Quoted: "a dedup header ``X-PandaDoc-Webhook-Event-Id`` 'to process each webhook
# notification once... even when PandaDoc retries delivery'".

DEDUPE_HEADER = "X-PandaDoc-Webhook-Event-Id"

#: Payload keys accepted as the delivery id when the header is absent. The header is
#: the documented mechanism; these are the fallbacks, and the stored row says which one
#: supplied the value so a later reader can tell a header-sourced id from a guessed one.
DELIVERY_ID_KEYS = ("eventId", "event_id", "id")

DELIVERY_FROM_HEADER = "header"
DELIVERY_FROM_PAYLOAD = "payload"

# --------------------------------------------------------------------------- #
# Back-pressure
# --------------------------------------------------------------------------- #
#
# The vendor's sentence is "Retry after the indicated number of seconds" and the
# specification marks no number. :data:`RETRY_AFTER_SECONDS` is therefore derived, and
# the derivation is recorded in dsr.security_governance.evault_inferences as
# DERIVED_RETRY_AFTER_SECONDS.
#
# Five seconds is short enough that a client retrying on the documented signal sees the
# artifact arrive within a couple of attempts, and long enough that a room with a slow
# vault does not spin. It is a default the caller may raise per room, which is why it is
# a constant here and a field on the subscription rather than a literal in a route.

RETRY_AFTER_SECONDS = 5

#: The header name the vendor sends, and the one this router sends back.
RETRY_AFTER_HEADER = "Retry-After"

#: The outcome stored for a fetch that was told to wait. Named, because the
#: specification's extensibility note requires "202 with Retry-After as a first-class
#: case" and a first-class case is one the store can name and the page can render.
BACK_PRESSURE = "back_pressure"

# --------------------------------------------------------------------------- #
# The two variants
# --------------------------------------------------------------------------- #
#
# The guide names both endpoints and says they are not interchangeable: "**For
# Protected PDF (recommended):** ``/download-protected`` - Returns digitally sealed
# PDF", and "the ``/download-protected`` endpoint always returns the same digitally
# sealed PDF file, while ``/download`` allows for watermark customization".

VARIANT_SEALED = "sealed"
VARIANT_PLAIN = "plain"

VARIANTS = (VARIANT_SEALED, VARIANT_PLAIN)

#: The vendor's own paths, quoted. They are the reason the room's routes are named what
#: they are named: a reader comparing the two should find the same shape on both sides.
SEALED_PATH = "/public/v1/documents/{id}/download-protected"
PLAIN_PATH = "/public/v1/documents/{id}/download"

VARIANT_ENDPOINTS = {
    VARIANT_SEALED: SEALED_PATH,
    VARIANT_PLAIN: PLAIN_PATH,
}

#: What each variant is, in the specification's own words. Rendered on the page rather
#: than paraphrased there, so the page cannot drift from the evidence.
VARIANT_SUMMARIES = {
    VARIANT_SEALED: (
        "The vendor's sealed endpoint returns a digitally sealed and verifiable file, and "
        "the same bytes for every request. What this room stores is generated, so what it can "
        "prove is that those bytes have not changed."
    ),
    VARIANT_PLAIN: (
        "The plain PDF. A watermark or branding can be applied, so the bytes differ "
        "between requests and the artifact is not the sealed record."
    ),
}

#: The deliberate trade-off the specification names, stated once and rendered beside both
#: variants so neither can be presented as the other.
VARIANT_TRADEOFF = (
    "The sealed endpoint at /public/v1/documents/{id}/download-protected is the one to "
    "keep. It is byte-stable and immutable, and the bytes are the evidence. The plain "
    "endpoint at /public/v1/documents/{id}/download is the one to use when a watermark is "
    "required, and it is not a sealed artifact: applying a watermark changes the bytes, so "
    "a watermarked copy is a derived file and not the executed agreement."
)

#: True only for the sealed variant, and the reason the digest is a proof at all.
BYTE_STABLE_VARIANTS = (VARIANT_SEALED,)


def byte_stable(variant: str) -> bool:
    """Does this variant return the same bytes for the same document, every time?"""
    return variant in BYTE_STABLE_VARIANTS


def watermarkable(variant: str) -> bool:
    """Can a watermark or branding be applied to this variant?"""
    return variant == VARIANT_PLAIN


# --------------------------------------------------------------------------- #
# Environments
# --------------------------------------------------------------------------- #
#
# "Production key only - This endpoint only works with a Production key. You'll get a
# 401 Unauthorized error when trying to use a Sandbox key."

ENVIRONMENT_PRODUCTION = "production"
ENVIRONMENT_SANDBOX = "sandbox"

ENVIRONMENTS = (ENVIRONMENT_PRODUCTION, ENVIRONMENT_SANDBOX)

#: The only environment the sealed endpoint answers in. The specification's extensibility
#: note draws the consequence: "sandbox-based integration tests must use the plain
#: download endpoint".
SEALED_ENVIRONMENT = ENVIRONMENT_PRODUCTION

#: What a sandbox caller gets instead, named rather than left as a bare 401. The
#: specification says "Do not surface it as a generic failure" about 429; the same rule
#: applies here, and the recommendation it gives is the plain endpoint.
SANDBOX_REMEDY = (
    "The sealed endpoint needs a production key. In sandbox, use the plain download "
    "endpoint at /public/v1/documents/{id}/download. It returns the PDF without the "
    "digital seal."
)

# --------------------------------------------------------------------------- #
# Document states
# --------------------------------------------------------------------------- #
#
# The specification's data flow is the ordering: "Completion -> async PDF generation ->
# e-vault storage -> ``document_completed_pdf_ready`` event". Three states follow from
# that, plus the one that says the flow has not started.

STATE_AWAITING_SIGNATURES = "awaiting_signatures"
STATE_GENERATING = "generating"
STATE_SEALED = "sealed"
STATE_FAILED = "failed"

DOCUMENT_STATES = (
    STATE_AWAITING_SIGNATURES,
    STATE_GENERATING,
    STATE_SEALED,
    STATE_FAILED,
)

#: The state a document is created in. "All signers complete" is step two of the user
#: flow, so a document that exists but whose signers have not all finished has not
#: begun generating anything.
DEFAULT_DOCUMENT_STATE = STATE_AWAITING_SIGNATURES

STATE_LABELS = {
    STATE_AWAITING_SIGNATURES: "Awaiting signatures",
    STATE_GENERATING: "PDF generating",
    STATE_SEALED: "Sealed in the e-vault",
    STATE_FAILED: "Generation failed",
}

#: Only the sealed state has an artifact to serve. Every other state is a reason the
#: download route cannot return bytes, and which reason decides whether the answer is
#: back-pressure or a refusal.
ARTIFACT_READY_STATES = (STATE_SEALED,)

# --------------------------------------------------------------------------- #
# Fetch outcomes
# --------------------------------------------------------------------------- #
#
# Every way a retrieval can end. The specification names five of them across three
# quoted sentences, and the rule is that none of them may be reported as a generic
# failure: 202 is a documented back-pressure signal, 401 is a documented environment
# gate, and "429 -> ``throttled``" is the specification's own wording.

OUTCOME_RETRIEVED = "retrieved"
OUTCOME_BACK_PRESSURE = BACK_PRESSURE
OUTCOME_DUPLICATE = "duplicate"
OUTCOME_SANDBOX_REJECTED = "sandbox_key_rejected"
OUTCOME_THROTTLED = "throttled"

FETCH_OUTCOMES = (
    OUTCOME_RETRIEVED,
    OUTCOME_BACK_PRESSURE,
    OUTCOME_DUPLICATE,
    OUTCOME_SANDBOX_REJECTED,
    OUTCOME_THROTTLED,
)

#: The three outcomes a vendor answered with, as opposed to the two this room decided.
#: A fetch attempt records which, because "the vendor said wait" and "the room said you
#: are going too fast" are different facts and a reader of the log needs to tell them.
VENDOR_ANSWERED_OUTCOMES = (OUTCOME_RETRIEVED, OUTCOME_BACK_PRESSURE)

#: What each outcome means for the caller, in one sentence. Served with every response.
OUTCOME_SUMMARIES = {
    OUTCOME_RETRIEVED: "The PDF was returned. The bytes and the digest are recorded.",
    OUTCOME_BACK_PRESSURE: (
        "The file is still being produced. Retry after the seconds in the Retry-After "
        "header. No body was returned."
    ),
    OUTCOME_DUPLICATE: (
        "This delivery was already applied. The retry was counted and nothing changed "
        "a second time."
    ),
    OUTCOME_SANDBOX_REJECTED: SANDBOX_REMEDY,
    OUTCOME_THROTTLED: (
        "Too many retrievals for this document in a short window. Wait before the next one."
    ),
}

# --------------------------------------------------------------------------- #
# The HTTP statuses the vendor documents
# --------------------------------------------------------------------------- #

STATUS_READY = 200
STATUS_ACCEPTED = 202
STATUS_SANDBOX = 401
STATUS_THROTTLED = 429

#: The media type the vendor returns, quoted from the data flow: "binary PDF returned
#: (``application/pdf``)". The room's download route sends the same one, so a client that
#: can read the vendor's response can read this one.
PDF_MEDIA_TYPE = "application/pdf"

# --------------------------------------------------------------------------- #
# Throttling
# --------------------------------------------------------------------------- #
#
# The specification records "429 -> ``throttled``" without saying when a room is
#: throttled. The window and the count are derived together, and the derivation is recorded
#: as DERIVED_THROTTLE_WINDOW in dsr.security_governance.evault_inferences.

#: The width of the sliding window, in seconds.
THROTTLE_WINDOW_SECONDS = 60

#: How many retrievals one document may have inside the window.
THROTTLE_LIMIT = 10

# --------------------------------------------------------------------------- #
# The room's honesty sentences
# --------------------------------------------------------------------------- #
#
# Carried on every response, for the same reason WF-073 carries its effect and its
# limitation: a caller cannot read a sealed artifact without also reading what the seal
# is worth here.

#: What the seal means. The specification calls the artifact "digitally sealed,
#: verifiable artifact" and calls the endpoint "digitally sealed PDF". This build stores
#: the digest and the bytes, so the verification a reader can do here is a digest
#: comparison, not a certificate-chain validation. Saying so is the difference between
#: describing the artifact and over-claiming it.
SEAL_SCOPE = (
    "A sealed artifact is byte-stable and immutable. This room records the bytes and "
    "their SHA-256 digest, so what it can prove is that the file has not changed. It "
    "does not validate a certificate chain, and it does not verify a signature it "
    "cannot see."
)

#: The polling rule, quoted, because it is the rule a reader is most likely to break.
NO_POLLING = (
    "This room subscribes to the ready event and does not poll. Nothing here asks a "
    "vendor how a document is doing, and no schedule in this package sleeps waiting "
    "for a PDF."
)

EFFECT_FIELD = "effect"
TRADEOFF_FIELD = "tradeoff"
SEAL_SCOPE_FIELD = "seal_scope"
NO_POLLING_FIELD = "no_polling"

#: The value every response carries under ``effect``. One word, so no caller can read a
#: control here and conclude the room vouches for a vendor it has never spoken to.
EFFECT = "recorded_not_verified"
