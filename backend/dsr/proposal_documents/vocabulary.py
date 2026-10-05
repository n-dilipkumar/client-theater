"""WF-103: every researched term, quoted from the specification.

The specification is ``docs/research/digital-sales-room-workflows/wf/WF-103.md``,
quoted in full in issue 145, and the underlying research is
``docs/research/raw/quoting-proposals.md`` section 19. Every constant below carries
the sentence it came from, because a state name or a refusal code that nobody can
trace to its source is a name somebody will change one day without knowing why.

Nothing here reads or writes. These are the words, the states, the codes and the
evidence.

The state machine is the spine of this file
-------------------------------------------

The data flow enumerates it exactly: ``uploaded -> draft -> waiting_approval ->
approved/rejected -> sent -> viewed -> completed``, with ``waiting_pay``/``paid`` and
``cancelled``/``declined`` as branches. Those are the vendor's own spellings, and they
are encoded here verbatim rather than renamed, because a room that reported a
``waiting_approval`` document as "pending review" could not be compared with what the
vendor would say.

Where the specification left a joint open, the gap is recorded in
:mod:`dsr.proposal_documents.inferences` rather than quietly filled in here, so the
difference between what the evidence says and what this build chose stays readable.
"""

from __future__ import annotations

from typing import Any

# --------------------------------------------------------------------------- #
# Collections
#
# Ticket-prefixed, which is the uniform convention in this repository. The host has no
# collection-collision check, so the prefix is what keeps two features from quietly
# sharing one.

#: The document itself: its vendor id, its state, the request body that would be
#: POSTed to ``/public/v1/documents``, and the approval gate.
DOCUMENTS = "wf103_proposal_document"

#: One row per state the document walked through. The document row carries the current
#: state; this is the history, and it is what makes "a signed webhook changed the CRM"
#: readable after the fact.
STATE_CHANGES = "wf103_document_state"

#: One row per recipient, with the ``role`` the research names (signer, cc, approver)
#: and the per-recipient status the webhook reports.
RECIPIENTS = "wf103_document_recipient"

#: The internal approval gate. Separate from the document because the gate is a
#: decision somebody makes, and a decision is its own record.
APPROVALS = "wf103_proposal_approval"

#: The pricing table the document was built from: sections, rows, currency, discount,
#: tax and fee. Written on the document and mirrored here so the numbers a buyer signed
#: can be compared with the numbers in the CRM.
PRICING_TABLES = "wf103_pricing_table"

#: The writeback into the CRM: opportunity stage, close date, and a note carrying the
#: document. This is the half of the workflow that happens *in* this product rather
#: than at a vendor, so unlike the document itself it is a real write.
CRM_SYNC = "wf103_crm_sync"

#: A webhook that arrived, de-duplicated on ``X-PandaDoc-Webhook-Event-Id``.
WEBHOOK_EVENTS = "wf103_webhook_event"

#: The audit trail, as this workflow records it. The vendor exposes
#: ``GET /public/v1/documents/{id}/audit-trail``; this is the same idea over the
#: writes this room actually made.
ACTIVITY = "wf103_proposal_activity"

#: The deal/opportunity the document is built from, read as data. The research says
#: "In your CRM, the deal/opportunity is won or a quote is created", so this is the
#: record that already exists when the workflow runs. This workflow reads it and
#: writes the sync back; it does not author the deal.
SOURCE_DEALS = "deal"


# --------------------------------------------------------------------------- #
# The state machine
#
# "the document moves through the state machine `uploaded -> draft -> waiting_approval
# -> approved/rejected -> sent -> viewed -> completed` (with `waiting_pay`/`paid` and
# `cancelled`/`declined` branches)"
#
# Encoded exactly as named. The full vendor spelling is ``document.uploaded`` and so
# on; the room stores the bare state and names the prefix separately, because every
# state in the machine carries it and writing it into eleven constants is how a typo
# gets in.

STATE_PREFIX = "document."

STATE_UPLOADED = "uploaded"
STATE_DRAFT = "draft"
STATE_WAITING_APPROVAL = "waiting_approval"
STATE_APPROVED = "approved"
STATE_REJECTED = "rejected"
STATE_SENT = "sent"
STATE_VIEWED = "viewed"
STATE_COMPLETED = "completed"

#: The two branches the data flow names alongside the spine.
STATE_WAITING_PAY = "waiting_pay"
STATE_PAID = "paid"
STATE_CANCELLED = "cancelled"
STATE_DECLINED = "declined"

#: The spine, in the order the data flow states it.
MAIN_STATES = (
    STATE_UPLOADED,
    STATE_DRAFT,
    STATE_WAITING_APPROVAL,
    STATE_APPROVED,
    STATE_REJECTED,
    STATE_SENT,
    STATE_VIEWED,
    STATE_COMPLETED,
)

#: The branches, kept apart from the spine so a reader can see which states the
#: research called branches and which it called the path.
BRANCH_STATES = (STATE_WAITING_PAY, STATE_PAID, STATE_CANCELLED, STATE_DECLINED)

DOCUMENT_STATES = MAIN_STATES + BRANCH_STATES

#: The states a document is still moving in. ``uploaded`` is included because it is
#: the state a document is *created* in, and the research is explicit that sending
#: from it is a 409 rather than a send.
IN_FLIGHT_STATES = (
    STATE_UPLOADED,
    STATE_DRAFT,
    STATE_WAITING_APPROVAL,
    STATE_SENT,
    STATE_VIEWED,
    STATE_WAITING_PAY,
)

#: A document in one of these has left the room's control: the buyer is holding it.
DELIVERED_STATES = (STATE_SENT, STATE_VIEWED, STATE_COMPLETED, STATE_PAID)

#: A document in one of these is finished, one way or another.
TERMINAL_STATES = (
    STATE_COMPLETED,
    STATE_PAID,
    STATE_CANCELLED,
    STATE_DECLINED,
    STATE_REJECTED,
)


def vendor_state(state: str) -> str:
    """The vendor's own spelling: ``draft`` is ``document.draft`` on the wire.

    The research quotes both forms, and the payload this build records has to match
    what a webhook would carry, so the conversion lives in one function rather than
    being spelled into the payload at each call site.
    """
    text = str(state or "").strip()
    if not text:
        return ""
    return text if text.startswith(STATE_PREFIX) else f"{STATE_PREFIX}{text}"


def normalise_state(value: Any) -> str:
    """Read a state written either way, and return the bare form.

    The webhook carries ``document.viewed`` and this build stores ``viewed``, so
    every state read has to survive both spellings. An unrecognised value is returned
    unchanged rather than refused, because payloads are open JSON and a state this
    build has not seen is a fact, not an error.
    """
    text = str(value or "").strip()
    if text.startswith(STATE_PREFIX):
        text = text[len(STATE_PREFIX) :]
    return text


# --------------------------------------------------------------------------- #
# The send gate
#
# Two separate gates, from two separate sentences, and the room must not merge them.
#
# 1. "Attempting to send a document that is still in `document.uploaded` status
#    returns a `409 Conflict` response." That is a vendor refusal about a document
#    that is not rendered yet.
# 2. "If the document's template has an approval workflow enabled, sending moves the
#    document to `document.waiting_approval` instead of `document.sent`." That is the
#    internal gate, and the *second* send is what delivers it.

CONFLICT_SEND_TOO_EARLY = (
    "Attempting to send a document that is still in document.uploaded status returns a "
    "409 Conflict response."
)
APPROVAL_GATE_QUOTE = (
    "If the document's template has an approval workflow enabled, sending moves the "
    "document to document.waiting_approval instead of document.sent."
)
SEND_AGAIN_QUOTE = (
    "Once approved, you need to call the Send endpoint again to move it to document.sent."
)


# --------------------------------------------------------------------------- #
# The internal approval gate
#
# This is a room-side gate on who may approve before the document is delivered. It is
# not the vendor's template approval workflow, though it is what the vendor's
# approval workflow *stands in for* here, and the room records the difference.
#
# The two decisions are named as the research names them: the template has an approval
# workflow, and an approver approves or rejects it.

APPROVAL_DECISION_APPROVE = "approve"
APPROVAL_DECISION_REJECT = "reject"
APPROVAL_DECISIONS = (APPROVAL_DECISION_APPROVE, APPROVAL_DECISION_REJECT)

#: Where the gate stands, in the room's own words. Distinct from the document state,
#: because the gate is open while the document sits in ``draft`` and closes while it
#: moves to ``waiting_approval``.
GATE_OPEN = "open"
GATE_APPROVED = "approved"
GATE_REJECTED = "rejected"
GATE_STATES = (GATE_OPEN, GATE_APPROVED, GATE_REJECTED)

#: A rejected approval needs a reason. "A change request with nothing in it is not an
#: instruction the seller can act on" is the sibling workflow's rule, and it is the
#: same rule here for the same reason.
APPROVAL_REASON_KEY = "reason"

#: An approval may name the approver and a message; a rejection may only carry a
#: reason. Named here because the engine branches on it.
APPROVAL_ACTOR_KEY = "approver"
APPROVAL_NOTE_KEY = "note"


# --------------------------------------------------------------------------- #
# Recipients
#
# "recipients[] (email, first_name, last_name, `role`)" and, in the extensibility
# section, "per-recipient `role`/`roles` (signer, cc, approver) and `signing_order`".
#
# The three roles are the researched set. ``signing_order`` is an integer rather than
# a boolean, because "signing order" is an order.

ROLE_SIGNER = "signer"
ROLE_CC = "cc"
ROLE_APPROVER = "approver"
RECIPIENT_ROLES = (ROLE_SIGNER, ROLE_CC, ROLE_APPROVER)

#: What a webhook reports per recipient: "with `recipients[].has_completed`,
#: `signature_date`".
RECIPIENT_HAS_COMPLETED = "has_completed"
RECIPIENT_SIGNATURE_DATE = "signature_date"


# --------------------------------------------------------------------------- #
# Webhooks
#
# "Consume the `document_state_changed` webhook (`document.viewed` /
#: `document.completed`, ...)" and "retries are handled by de-duplicating on
#: `X-PandaDoc-Webhook-Event-Id`".
#
# Only the events the research names for *this* workflow are declared. The full
#: subscription list in the apis_hit field is much longer and belongs to a console
#: this build does not have; see ``NOT_BUILT_WEBHOOK_CONSOLE`` in the inferences
#: module.

WEBHOOK_EVENT = "document_state_changed"

#: The de-duplication header, in the vendor's spelling. The room de-duplicates on
#: exactly this name, because a retry is the same event with the same id.
WEBHOOK_EVENT_ID_HEADER = "X-PandaDoc-Webhook-Event-Id"

#: "All webhook requests include an HMAC-SHA256 signature for verification using your
#: shared key." The header name is not quoted in the research; see
#: ``DERIVED_SIGNATURE_HEADER_NAME`` in the inferences module.
WEBHOOK_SIGNATURE_HEADER = "X-PandaDoc-Signature"
WEBHOOK_SIGNATURE_ALGORITHM = "HMAC-SHA256"

#: The states a ``document_state_changed`` webhook can report. "(`document.viewed` /
#: `document.completed`)" names two; the machine in the data flow enumerates the rest.
WEBHOOK_STATES = (
    STATE_UPLOADED,
    STATE_DRAFT,
    STATE_WAITING_APPROVAL,
    STATE_APPROVED,
    STATE_REJECTED,
    STATE_SENT,
    STATE_VIEWED,
    STATE_COMPLETED,
    STATE_CANCELLED,
    STATE_DECLINED,
)

#: What a replayed webhook does. Not a state: an outcome, returned to the caller so a
#: retry that changed nothing says so rather than looking like a fresh write.
WEBHOOK_APPLIED = "applied"
WEBHOOK_DUPLICATE = "duplicate"
WEBHOOK_OUTCOMES = (WEBHOOK_APPLIED, WEBHOOK_DUPLICATE)


# --------------------------------------------------------------------------- #
# ``shared_link`` and its fallback
#
# "`shared_link` field in the `recipients` array may be empty in webhook payloads due
# to asynchronous processing." and the automation note names the fallback: "fall back
# to Document Details".

#: The two sources a shared link can come from, in the order they are tried.
SHARED_LINK_SOURCE_WEBHOOK = "webhook"
SHARED_LINK_SOURCE_DETAILS = "document_details"
SHARED_LINK_SOURCE_DERIVED = "derived_stand_in"
SHARED_LINK_SOURCES = (
    SHARED_LINK_SOURCE_WEBHOOK,
    SHARED_LINK_SOURCE_DETAILS,
    SHARED_LINK_SOURCE_DERIVED,
)

#: Why the fallback exists, in the vendor's words. Served to the page so a reader who
#: is shown a Document Details link knows it is a fallback and not the webhook's own.
SHARED_LINK_FALLBACK_NOTE = (
    "The shared_link field in the recipients array may be empty in webhook payloads due to "
    "asynchronous processing, so an empty link falls back to the link on Document Details."
)


# --------------------------------------------------------------------------- #
# The destructive update
#
# "Omitting a section/item deletes it." That is the research, word for word, and it is
# a destructive default. It is named in the request type rather than left as a
# surprise, and the engine refuses an update that does not say it means it.

QUOTE_UPDATE_DELETES_OMISSIONS = "Any section or item omitted from the payload will be deleted."

#: The key a caller sets on a quote update to acknowledge the destructive default.
#: Named as a constant because a string written once per call site is how two call
#: sites end up disagreeing about what the flag is called.
ACKNOWLEDGE_DELETIONS_KEY = "acknowledge_deletions"

#: The researched line-item fields, from the update-quotes reference. Encoded because
#: the pricing table is built from them and a field added later is data, not a column.
QUOTE_ITEM_FIELDS = (
    "sku",
    "name",
    "qty",
    "price",
    "cost",
    "billing_frequency",
    "contract_term",
    "pricing_method",
    "reference_type",
    "reference_id",
    "discounts",
    "taxes",
    "fees",
    "total",
)

#: The pricing-table options, from the create-from-content-placeholders reference:
#: "pricing_tables[] with `options.currency`, `options.discount`".
PRICING_OPTION_KEYS = ("currency", "discount")

#: The sections of a pricing table. "sections[].rows[].data`/`options`".
PRICING_ROW_KEYS = ("data", "options")


# --------------------------------------------------------------------------- #
# Authentication
#
# "Auth: `API-Key` header or OAuth2 (`read write` scopes, `/oauth2/authorize` +
#: `/oauth2/access_token`)." Two are offered and the room has to pick one.
#: See ``DERIVED_AUTH_IS_THE_API_KEY_HEADER`` in the inferences module.

AUTH_API_KEY = "api_key"
AUTH_OAUTH2 = "oauth2"
AUTH_METHODS = (AUTH_API_KEY, AUTH_OAUTH2)
AUTH_METHOD_CHOSEN = AUTH_API_KEY

#: The header the chosen method uses, in the vendor's spelling.
AUTH_API_KEY_HEADER = "API-Key"

#: The OAuth2 scopes, kept because they are quoted even though the room does not use
#: that method, and a reader deciding to switch needs them.
AUTH_OAUTH2_SCOPES = ("read", "write")
AUTH_OAUTH2_AUTHORIZE_PATH = "/oauth2/authorize"
AUTH_OAUTH2_TOKEN_PATH = "/oauth2/access_token"

#: The base URL the researched endpoints hang off, named once.
DOCUMENT_API_BASE = "https://api.pandadoc.com"

#: The regions the signing library takes: "(`region: "com" | "eu"`)".
REGION_COM = "com"
REGION_EU = "eu"
REGIONS = (REGION_COM, REGION_EU)


# --------------------------------------------------------------------------- #
# The derived stand-in
#
# This build calls no vendor. The research documents the endpoints, the state
# machine, the HMAC signature and the OAuth2 flow, and it documents no credential
# this product holds, so every document here is *derived* from the CRM record rather
# than rendered by PandaDoc.
#
# The discipline is the one ``headless_booking.assets.meeting_link`` established, and
# it is four separate declarations so that a reader who ignores the metadata still
# cannot mistake the artefact for a real one:
#
#   1. the host is ``.invalid`` (RFC 2606), so the link cannot resolve at all;
#   2. the record carries ``<field>_is_derived: True``;
#   3. the response carries a prose ``*_note`` saying so;
#   4. a named inference explains why no call is made.
#
# "A demo that showed a plausible document without saying so is worse than one that
# says it is derived."

#: The suffix on every derived identifier. RFC 2606 reserves ``.invalid`` precisely
#: so that a hostname can never resolve, which is what makes a derived link safe to
#: render.
DERIVED_HOST_SUFFIX = ".invalid"

#: The vendor host this build would call, and does not.
VENDOR_HOST = "pandadoc.com"
DERIVED_VENDOR_HOST = f"pandadoc{DERIVED_HOST_SUFFIX}"

#: The field that marks a derived identifier. The naming convention is
#: ``<thing>_is_derived``, which is what ``meeting_link_is_derived`` established.
DOCUMENT_URL_IS_DERIVED = "document_url_is_derived"
SHARED_LINK_IS_DERIVED = "shared_link_is_derived"

#: Nothing here was transmitted. "The room never calls a vendor API in a test", and
#: it does not in production either. The delivery marker is ``simulated``, which is
#: what ``booking_approval`` and ``connector_sandbox`` converged on.
DELIVERY_SIMULATED = "simulated"
DELIVERY_RECORDED = "recorded"

#: The note that rides with every derived document. Paired with the booleans, because
#: a boolean is read by a machine and this is read by a person.
DOCUMENT_DERIVED_NOTE = (
    "Derived stand-in: this document was not rendered by a document API. It is computed from "
    "the CRM record, and its id, link and status are local to this room. Nothing was sent to "
    "any vendor."
)
DELIVERY_NOTE = (
    "Recorded, not transmitted: the document state and its webhook are written in the same "
    "transaction as the change, and this product opens no outbound connection."
)


def derived_document_url(document_id: str) -> str:
    """A deterministic stand-in for the vendor's document URL.

    The research names ``GET /public/v1/documents/{id}/details`` and the shared link a
    recipient is sent, and this build calls neither. So the URL is *derived* from the
    local document id rather than fetched, it is built on the reserved ``.invalid``
    host so it cannot resolve even in principle, and the document row says which it
    is. A demo that showed a plausible-looking document link without saying so would
    be worse than one that shows a derived one.
    """
    return f"https://{DERIVED_VENDOR_HOST}/public/v1/documents/{document_id}"


def derived_shared_link(document_id: str, email: str) -> str:
    """A deterministic stand-in for the per-recipient shared link.

    The recipient's link is a vendor artifact, exactly as the join link is for
    ``headless_booking.meeting_link``, and it is derived for the same reason and with
    the same reserved host.
    """
    who = str(email or "recipient").strip().lower()
    return f"https://{DERIVED_VENDOR_HOST}/public/v1/documents/{document_id}/shared/{who}"


# --------------------------------------------------------------------------- #
# The writeback
#
# "the integration writes status back to the CRM (opportunity stage/close date, notes &
#: attachments including the signed PDF) and, via `linked_objects`, makes the document
#: discoverable from the CRM record."
#
# This is the one part of the workflow that happens in this product, so unlike the
# document it is a real write and its rows say so.

#: The deal stage each terminal document state maps to. A derived mapping: the
#: research says "Update Opportunity status when PandaDoc status is updated" without
#: naming the stage names, and the deal record owns its own stage vocabulary. See
#: ``DERIVED_STAGE_MAPPING`` in the inferences module.
CRM_STAGE_DRAFT = "draft"
CRM_STAGE_PROPOSAL_SENT = "proposal_sent"
CRM_STAGE_PROPOSAL_VIEWED = "proposal_viewed"
CRM_STAGE_PROPOSAL_COMPLETED = "proposal_completed"
CRM_STAGE_CLOSED_WON = "closed_won"
CRM_STAGE_CLOSED_LOST = "closed_lost"
CRM_STAGES = (
    CRM_STAGE_DRAFT,
    CRM_STAGE_PROPOSAL_SENT,
    CRM_STAGE_PROPOSAL_VIEWED,
    CRM_STAGE_PROPOSAL_COMPLETED,
    CRM_STAGE_CLOSED_WON,
    CRM_STAGE_CLOSED_LOST,
)

#: The two-way association, from ``linked_objects``. "``linked_objects`` provides
#: two-way CRM association" and "makes the document discoverable from the CRM record".
LINKED_OBJECT_PROVIDER = "crm"
LINKED_OBJECT_ENTITY_TYPE = "opportunity"
LINKED_OBJECT_KEY = "linked_objects"


# --------------------------------------------------------------------------- #
# Activity names
#
# The log the panel reads. The first three are the researched verbs; the rest are this
# build naming its own steps, and they carry ``sourced: False`` when served so a
# reader can tell a quoted name from one this build chose.
#:from_research = the names the research states outright.
#:this_build = the names added here for steps the research describes but does not name.

ACTIVITY_DOCUMENT_CREATED = "Proposal document created"
ACTIVITY_DOCUMENT_RENDERED = "Proposal document ready to send"
ACTIVITY_GATE_OPENED = "Proposal held for internal approval"
ACTIVITY_GATE_APPROVED = "Proposal approved for send"
ACTIVITY_GATE_REJECTED = "Proposal send refused by approver"
ACTIVITY_QUOTE_UPDATED = "Proposal pricing table updated"
ACTIVITY_SENT = "Proposal sent for signature"
ACTIVITY_WEBHOOK_APPLIED = "Document status synced from webhook"
ACTIVITY_WEBHOOK_DUPLICATE = "Duplicate webhook ignored"
ACTIVITY_CRM_SYNCED = "CRM opportunity updated from document status"
ACTIVITY_SEND_TOO_EARLY_REFUSED = "Send refused, document not yet rendered"

#: The names the research states outright, for a reader who wants only sourced
#: vocabulary.
SOURCED_ACTIVITY_TYPES = (
    ACTIVITY_DOCUMENT_CREATED,
    ACTIVITY_SENT,
    ACTIVITY_CRM_SYNCED,
)

ACTIVITY_TYPES = (
    ACTIVITY_DOCUMENT_CREATED,
    ACTIVITY_DOCUMENT_RENDERED,
    ACTIVITY_GATE_OPENED,
    ACTIVITY_GATE_APPROVED,
    ACTIVITY_GATE_REJECTED,
    ACTIVITY_QUOTE_UPDATED,
    ACTIVITY_SENT,
    ACTIVITY_WEBHOOK_APPLIED,
    ACTIVITY_WEBHOOK_DUPLICATE,
    ACTIVITY_CRM_SYNCED,
    ACTIVITY_SEND_TOO_EARLY_REFUSED,
)


# --------------------------------------------------------------------------- #
# Refusal codes
#
# One dict: code -> (status, sentence). The status is looked up *inside* the exception
# constructor, so a raise site names only the code and cannot drift from the status or
# the sentence. Every code here is published at ``GET /vocabulary``, so a client
# branches on a code rather than pattern-matching prose.

ERROR_CODES: dict[str, tuple[int, str]] = {
    # -- 404: the thing addressed is not there.
    "unknown_document": (404, "No such proposal document."),
    "unknown_approval": (404, "No such approval record for this document."),
    "unknown_deal": (404, "No such deal or opportunity."),
    # -- 422: the request is well formed and the rules refuse it.
    "deal_needs_a_name": (422, "Give the document a name so a reader can find it."),
    "template_uuid_required": (
        422,
        "Name the template. A document is created from a template, and the research's create "
        "call requires a template_uuid.",
    ),
    "unknown_recipient_role": (
        422,
        "A recipient role is one of: signer, cc, approver.",
    ),
    "recipient_needs_an_email": (422, "A recipient needs an email address."),
    "unknown_document_state": (422, "That is not a state the researched state machine contains."),
    "no_approval_gate": (
        422,
        "This document has no approval gate open, so there is no approval to decide.",
    ),
    "reason_required_to_reject": (
        422,
        "Enter a reason, then reject. A rejection with nothing in it is not an instruction the "
        "seller can act on.",
    ),
    "acknowledgement_required_for_deletions": (
        422,
        "Any section or item omitted from the payload will be deleted. Acknowledge that to "
        "proceed.",
    ),
    "quote_update_needs_sections": (422, "A quote update carries a sections list."),
    "unknown_webhook_event": (
        422,
        f"The webhook this room consumes is {WEBHOOK_EVENT}.",
    ),
    "webhook_signature_missing": (
        422,
        "All webhook requests include an HMAC-SHA256 signature for verification using your "
        "shared key, so a request without one is refused.",
    ),
    "webhook_signature_invalid": (
        422,
        "The HMAC-SHA256 signature does not match the shared key, so the request is refused.",
    ),
    "webhook_document_unknown": (404, "The webhook names a document this room has never seen."),
    # -- 409: well formed, and it conflicts with the document's current state.
    "document_not_yet_rendered": (
        409,
        CONFLICT_SEND_TOO_EARLY + " Create the document, wait for document.draft, then send.",
    ),
    "document_already_approved": (
        409,
        APPROVAL_GATE_QUOTE + " " + SEND_AGAIN_QUOTE,
    ),
    "document_already_sent": (
        409,
        "The document is already document.sent, so there is nothing to send again.",
    ),
    "document_send_not_permitted": (
        409,
        "This document's template has no approval workflow, so an internal approval is not what "
        "gates the send.",
    ),
}


def describe() -> dict[str, Any]:
    """The researched surface, served so a page renders it rather than repeating it.

    Every value is a constant above, so the served list and the enforced rule cannot
    disagree. The frontend renders its state rail, its role picker and its error
    handling from this rather than from a list compiled into a component, so a term
    added here appears on the page with no edit to the feature module.
    """
    return {
        "state_machine": {
            "prefix": STATE_PREFIX,
            "main": list(MAIN_STATES),
            "branches": list(BRANCH_STATES),
            "all": list(DOCUMENT_STATES),
            "in_flight": list(IN_FLIGHT_STATES),
            "delivered": list(DELIVERED_STATES),
            "terminal": list(TERMINAL_STATES),
        },
        "send_gate": {
            "conflict_quote": CONFLICT_SEND_TOO_EARLY,
            "approval_quote": APPROVAL_GATE_QUOTE,
            "send_again_quote": SEND_AGAIN_QUOTE,
            "conflict_code": "document_not_yet_rendered",
        },
        "approval": {
            "decisions": list(APPROVAL_DECISIONS),
            "gate_states": list(GATE_STATES),
            "reason_key": APPROVAL_REASON_KEY,
        },
        "recipients": {
            "roles": list(RECIPIENT_ROLES),
            "has_completed": RECIPIENT_HAS_COMPLETED,
            "signature_date": RECIPIENT_SIGNATURE_DATE,
        },
        "webhook": {
            "event": WEBHOOK_EVENT,
            "event_id_header": WEBHOOK_EVENT_ID_HEADER,
            "signature_header": WEBHOOK_SIGNATURE_HEADER,
            "signature_algorithm": WEBHOOK_SIGNATURE_ALGORITHM,
            "states": list(WEBHOOK_STATES),
            "outcomes": list(WEBHOOK_OUTCOMES),
        },
        "shared_link": {
            "sources": list(SHARED_LINK_SOURCES),
            "fallback_note": SHARED_LINK_FALLBACK_NOTE,
        },
        "quote_update": {
            "deletes_omissions": QUOTE_UPDATE_DELETES_OMISSIONS,
            "acknowledge_key": ACKNOWLEDGE_DELETIONS_KEY,
            "item_fields": list(QUOTE_ITEM_FIELDS),
            "pricing_option_keys": list(PRICING_OPTION_KEYS),
            "pricing_row_keys": list(PRICING_ROW_KEYS),
        },
        "auth": {
            "methods": list(AUTH_METHODS),
            "chosen": AUTH_METHOD_CHOSEN,
            "api_key_header": AUTH_API_KEY_HEADER,
            "oauth2_scopes": list(AUTH_OAUTH2_SCOPES),
            "oauth2_authorize_path": AUTH_OAUTH2_AUTHORIZE_PATH,
            "oauth2_token_path": AUTH_OAUTH2_TOKEN_PATH,
            "base": DOCUMENT_API_BASE,
            "regions": list(REGIONS),
        },
        "derived": {
            "vendor_host": VENDOR_HOST,
            "derived_host": DERIVED_VENDOR_HOST,
            "document_url_is_derived": DOCUMENT_URL_IS_DERIVED,
            "shared_link_is_derived": SHARED_LINK_IS_DERIVED,
            "document_note": DOCUMENT_DERIVED_NOTE,
            "delivery_note": DELIVERY_NOTE,
            "delivery_marker": DELIVERY_SIMULATED,
        },
        "crm": {
            "stages": list(CRM_STAGES),
            "linked_object_provider": LINKED_OBJECT_PROVIDER,
            "linked_object_entity_type": LINKED_OBJECT_ENTITY_TYPE,
        },
        "collections": {
            "documents": DOCUMENTS,
            "state_changes": STATE_CHANGES,
            "recipients": RECIPIENTS,
            "approvals": APPROVALS,
            "pricing_tables": PRICING_TABLES,
            "crm_sync": CRM_SYNC,
            "webhook_events": WEBHOOK_EVENTS,
            "activity": ACTIVITY,
        },
        "activities": [
            {"activity": name, "sourced": name in SOURCED_ACTIVITY_TYPES} for name in ACTIVITY_TYPES
        ],
        "error_codes": {
            name: {"status": status, "detail": detail}
            for name, (status, detail) in ERROR_CODES.items()
        },
    }
