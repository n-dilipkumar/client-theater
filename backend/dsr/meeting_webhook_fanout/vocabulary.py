"""The published vocabulary of WF-067, and the rules the research quotes.

Everything a client needs to render the signer list, the envelope timeline and the
role picker lives here rather than being compiled into a page. A vocabulary this
build adds server-side therefore reaches every client at once, and the editor can
never disagree with the validator about what is legal. :func:`describe` serves the
whole thing.

Each constant below carries the sentence from
``docs/research/raw/scheduling-meetings.md`` section 17 that makes it part of the
specification rather than a preference of this build.
"""

from __future__ import annotations

from typing import Any

# --------------------------------------------------------------------------- #
# Collections
# --------------------------------------------------------------------------- #

#: The seller-built template. "Seller builds the MAP/agreement **Template** (a PDF)
#: with recipient roles and fields."
COLLECTION_TEMPLATE = "map_template"

#: One MAP sent out for approval. "Seller creates the envelope via the API in one
#: call" - so the envelope and its recipients and its fields are one seller action.
COLLECTION_PLAN = "map_plan"

#: One party on one plan, with the role the research assigns them.
COLLECTION_RECIPIENT = "map_recipient"

#: Every event that arrived from the vendor, including repeats. The research says
#: "Process idempotently - Webhooks may be retried, so handle duplicate events",
#: and the only way to prove idempotence is to keep the repeats.
COLLECTION_EVENT = "map_event"

#: What an event told the owner.
COLLECTION_NOTICE = "map_notice"

COLLECTIONS: tuple[str, ...] = (
    COLLECTION_TEMPLATE,
    COLLECTION_PLAN,
    COLLECTION_RECIPIENT,
    COLLECTION_EVENT,
    COLLECTION_NOTICE,
)

# --------------------------------------------------------------------------- #
# Recipient roles
# --------------------------------------------------------------------------- #

#: "Roles include ``SIGNER``, **``APPROVER``** ("Must approve before signers can
#: sign"), ``CC``, ``VIEWER``, ``ASSISTANT``." Five roles, named as the research
#: names them. A role this build does not serve is refused rather than stored
#: looking armed - see :class:`~dsr.scheduling_meetings.errors.UnknownRecipientRole`.
SIGNER = "SIGNER"
APPROVER = "APPROVER"
CC = "CC"
VIEWER = "VIEWER"
ASSISTANT = "ASSISTANT"
RECIPIENT_ROLES: tuple[str, ...] = (SIGNER, APPROVER, CC, VIEWER, ASSISTANT)

ROLE_LABELS: dict[str, str] = {
    SIGNER: "Signer",
    APPROVER: "Approver",
    CC: "Copied",
    VIEWER: "Viewer",
    ASSISTANT: "Assistant",
}

#: The research's own gloss on the one role that carries a gate. This is the whole
#: reason the role exists: "APPROVER | Must approve before signers can sign".
APPROVER_QUOTE = "APPROVER | Must approve before signers can sign"

#: Which roles put ink on the document, and so which ones have to act for the plan
#: to complete. Derived from the roles themselves: a CC and a VIEWER are not
#: asked to sign, so their absence cannot hold a plan open.
SIGNING_ROLES: tuple[str, ...] = (SIGNER, APPROVER)

SIGNING_ROLE_QUOTE = "Recipient signingStatus: SIGNED and signedAt set once they sign."

#: The roles a plan cannot go out without. A mutual action plan with nobody to sign
#: it is not a mutual action plan. The research's step 2 requires ``recipients[]``
#: "each with ``email``, ``name``, ``role``", so at least one signing role is the
#: floor rather than this build's preference.
REQUIRED_SIGNING_ROLE_QUOTE = (
    "Seller creates the envelope via the API in one call, supplying recipients[] each with "
    "email, name, role and fields[]"
)

# --------------------------------------------------------------------------- #
# Envelope status
# --------------------------------------------------------------------------- #

#: "the status changes from ``DRAFT`` to ``PENDING``". Two states named by the
#: research. The two the room derives from events are added beside them because
#: "when all recipients are done -> ``DOCUMENT_COMPLETED`` with ``completedAt``" and
#: a buyer "can instead **reject**".
STATUS_DRAFT = "DRAFT"
STATUS_PENDING = "PENDING"
STATUS_COMPLETED = "COMPLETED"
STATUS_REJECTED = "REJECTED"
STATUS_CANCELLED = "CANCELLED"
STATUSES: tuple[str, ...] = (
    STATUS_DRAFT,
    STATUS_PENDING,
    STATUS_COMPLETED,
    STATUS_REJECTED,
    STATUS_CANCELLED,
)

STATUS_LABELS: dict[str, str] = {
    STATUS_DRAFT: "Draft",
    STATUS_PENDING: "Awaiting signatures",
    STATUS_COMPLETED: "Completed",
    STATUS_REJECTED: "Rejected",
    STATUS_CANCELLED: "Cancelled",
}

STATUS_QUOTE = "After distribution, recipients receive an email with a link to sign the document."

#: The transition the research names exactly. Creating a plan does not move it, and
#: distributing does.
DISTRIBUTE_TRANSITION_QUOTE = (
    "Seller distributes: POST /api/v2/envelope/distribute with {envelopeId} -> status "
    "DRAFT -> PENDING, recipients get a signing link."
)

#: "per-recipient ``signingOrder`` + ``PARALLEL``/``SEQUENTIAL``". Two orders, and
#: the research names both.
SIGNING_ORDERS: tuple[str, ...] = ("PARALLEL", "SEQUENTIAL")

SIGNING_ORDER_LABELS: dict[str, str] = {
    "PARALLEL": "Everyone signs at once",
    "SEQUENTIAL": "One at a time, in order",
}

SIGNING_ORDER_QUOTE = "signingOrder: PARALLEL | SEQUENTIAL"

#: "``documentMeta{subject, message, signingOrder, redirectUrl, distributionMethod}``".
#: Both distribution methods the research names for the envelope create call.
DISTRIBUTION_METHODS: tuple[str, ...] = ("email", "direct_link")

DISTRIBUTION_METHOD_LABELS: dict[str, str] = {
    "email": "Email every recipient a signing link",
    "direct_link": "Send a direct link that opens the plan in the sales room",
}

# --------------------------------------------------------------------------- #
# Field types and coordinates
# --------------------------------------------------------------------------- #

#: "``fields[]`` (``type`` ``SIGNATURE``/``NAME``/``DATE``/..., ``page``,
#: ``positionX``, ``positionY``, ``width``, ``height`` as percentages,
#: ``identifier`` = file index)". The three the research spells out are the floor.
#: The rest are the ones the vendor documents as standard on a signing field.
FIELD_TYPES: tuple[str, ...] = (
    "SIGNATURE",
    "NAME",
    "DATE",
    "INITIAL",
    "TEXT",
    "NUMBER",
    "RADIO",
    "CHECKBOX",
    "SELECT",
    "ATTACHMENT",
)

FIELD_TYPE_LABELS: dict[str, str] = {
    "SIGNATURE": "Signature",
    "NAME": "Full name",
    "DATE": "Date",
    "INITIAL": "Initials",
    "TEXT": "Text",
    "NUMBER": "Number",
    "RADIO": "Choice",
    "CHECKBOX": "Checkbox",
    "SELECT": "Dropdown",
    "ATTACHMENT": "Attachment",
}

#: The research quotes both axes as percentages: "``positionX`` | Horizontal
#: position from left edge (0 = left, 100 = right)" and the same for Y, with
#: ``width`` and ``height`` in the same unit. A coordinate outside that scale is
#: not a field that would land on the page.
COORDINATE_MIN = 0.0
COORDINATE_MAX = 100.0

COORDINATE_QUOTE = (
    "positionX | Horizontal position from left edge (0 = left, 100 = right); "
    "positionY, width and height are percentages of the same page."
)

#: "``identifier`` | Index of the file (0 for first file, 1 for second, etc.)".
#: Indices start at zero and name one of the files sent with the envelope.
IDENTIFIER_QUOTE = "identifier | Index of the file (0 for first file, 1 for second, etc.)"

#: The one sentence that says why a field's geometry is checked at all, named so a
#: test can point at the rule rather than at a number.
MALFORMED_FIELD_GUARD = (
    "A field's type and its four coordinates are percentages of the page, and its "
    "identifier is a file index. A value outside those ranges is not a field the "
    "vendor could place."
)

# --------------------------------------------------------------------------- #
# Events
# --------------------------------------------------------------------------- #

EVENT_DOCUMENT_CREATED = "DOCUMENT_CREATED"
EVENT_DOCUMENT_SENT = "DOCUMENT_SENT"
EVENT_DOCUMENT_OPENED = "DOCUMENT_OPENED"
EVENT_DOCUMENT_SIGNED = "DOCUMENT_SIGNED"
EVENT_DOCUMENT_RECIPIENT_COMPLETED = "DOCUMENT_RECIPIENT_COMPLETED"
EVENT_DOCUMENT_COMPLETED = "DOCUMENT_COMPLETED"
EVENT_DOCUMENT_REJECTED = "DOCUMENT_REJECTED"
EVENT_DOCUMENT_CANCELLED = "DOCUMENT_CANCELLED"
EVENT_RECIPIENT_EXPIRED = "RECIPIENT_EXPIRED"
EVENT_DOCUMENT_REMINDER_SENT = "DOCUMENT_REMINDER_SENT"
EVENT_TYPE_TEMPLATE_CREATED = "TEMPLATE_CREATED"
EVENT_TYPE_TEMPLATE_UPDATED = "TEMPLATE_UPDATED"
EVENT_TYPE_TEMPLATE_DELETED = "TEMPLATE_DELETED"
EVENT_TYPE_TEMPLATE_USED = "TEMPLATE_USED"

#: The researched event list, verbatim and in the research's own order.
EVENTS: tuple[str, ...] = (
    EVENT_DOCUMENT_CREATED,
    EVENT_DOCUMENT_SENT,
    EVENT_DOCUMENT_OPENED,
    EVENT_DOCUMENT_SIGNED,
    EVENT_DOCUMENT_RECIPIENT_COMPLETED,
    EVENT_DOCUMENT_COMPLETED,
    EVENT_DOCUMENT_REJECTED,
    EVENT_DOCUMENT_CANCELLED,
    EVENT_RECIPIENT_EXPIRED,
    EVENT_DOCUMENT_REMINDER_SENT,
    EVENT_TYPE_TEMPLATE_CREATED,
    EVENT_TYPE_TEMPLATE_UPDATED,
    EVENT_TYPE_TEMPLATE_DELETED,
    EVENT_TYPE_TEMPLATE_USED,
)

EVENT_LABELS: dict[str, str] = {
    EVENT_DOCUMENT_CREATED: "Document created",
    EVENT_DOCUMENT_SENT: "Sent to recipients",
    EVENT_DOCUMENT_OPENED: "Recipient opened the document",
    EVENT_DOCUMENT_SIGNED: "Recipient signed",
    EVENT_DOCUMENT_RECIPIENT_COMPLETED: "Recipient finished their part",
    EVENT_DOCUMENT_COMPLETED: "Everyone finished. The plan is approved",
    EVENT_DOCUMENT_REJECTED: "Rejected",
    EVENT_DOCUMENT_CANCELLED: "Cancelled",
    EVENT_RECIPIENT_EXPIRED: "A recipient let the deadline pass",
    EVENT_DOCUMENT_REMINDER_SENT: "A reminder was sent",
    EVENT_TYPE_TEMPLATE_CREATED: "Template created",
    EVENT_TYPE_TEMPLATE_UPDATED: "Template updated",
    EVENT_TYPE_TEMPLATE_DELETED: "Template deleted",
    EVENT_TYPE_TEMPLATE_USED: "Template used",
}

#: The events that describe a recipient acting, and so carry a recipient in the
#: body. A ``DOCUMENT_SIGNED`` with no recipient named is not a signature, it is
#: a malformed event.
RECIPIENT_EVENTS: tuple[str, ...] = (
    EVENT_DOCUMENT_OPENED,
    EVENT_DOCUMENT_SIGNED,
    EVENT_DOCUMENT_RECIPIENT_COMPLETED,
    EVENT_DOCUMENT_REJECTED,
    EVENT_RECIPIENT_EXPIRED,
    EVENT_DOCUMENT_REMINDER_SENT,
)

#: What each recipient-bearing event means, quoted from the research's event table.
RECIPIENT_EVENT_MEANINGS: dict[str, str] = {
    EVENT_DOCUMENT_OPENED: "Recipient opens the document",
    EVENT_DOCUMENT_SIGNED: (
        "Recipient signs document. Recipient signingStatus becomes SIGNED and signedAt is set."
    ),
    EVENT_DOCUMENT_RECIPIENT_COMPLETED: "Recipient completes their action.",
    EVENT_DOCUMENT_REJECTED: "A recipient rejects the document, with a reason.",
    EVENT_RECIPIENT_EXPIRED: (
        "Recipient signing deadline passes. Recipient expiresAt has passed and "
        "expirationNotifiedAt is set."
    ),
    EVENT_DOCUMENT_REMINDER_SENT: "A reminder was emailed to a recipient who has not signed.",
}

#: The event the room waits for. "The sales room consumes ``DOCUMENT_COMPLETED``"
#: and it is what "flip the MAP milestone to *Approved*" hangs on.
EVENTS_THIS_BUILD_HANDLES: tuple[str, ...] = EVENTS

#: The header carrying the shared secret. "Verify the signature - Check the
#: ``X-Documenso-Secret`` header matches your configured secret".
WEBHOOK_SECRET_HEADER = "X-Documenso-Secret"

WEBHOOK_VERIFICATION_QUOTE = (
    "Verify the signature - Check the X-Documenso-Secret header matches your configured secret."
)

IDEMPOTENCE_QUOTE = "Process idempotently - Webhooks may be retried, so handle duplicate events."

#: The vendor's own list filter. "``GET /api/v2/envelope`` (list; filter
#: ``?source=TEMPLATE_DIRECT_LINK``)" - so a room can also reconcile by polling.
EVENT_SOURCE_DIRECT_LINK = "TEMPLATE_DIRECT_LINK"

#: The three keys a webhook body may carry the join key under. The research names
#: one spelling - "The external ID is stored with the created document and included
#: in webhook payloads" - and a payload nests it, so both are looked for rather
#: than the one the vendor happens to use today.
EXTERNAL_ID_KEYS: tuple[str, ...] = ("externalId", "external_id", "externalid")

# --------------------------------------------------------------------------- #
# Milestones: the room's own state
# --------------------------------------------------------------------------- #

#: What a MAP milestone is before anything goes out.
MILESTONE_DRAFT = "draft"
#: Out for signature, waiting on people.
MILESTONE_AWAITING_SIGNATURE = "awaiting_signature"
#: "the sales room consumes ``DOCUMENT_COMPLETED`` ... to flip the MAP milestone to
#: *Approved*".
MILESTONE_APPROVED = "approved"
#: A buyer refused it. The two refusals are separate milestones because the
#: research makes them different problems - see :data:`REFUSAL_MILESTONES`.
MILESTONE_REFUSED_BY_SIGNER = "refused_by_signer"
MILESTONE_REFUSED_BY_APPROVER = "refused_by_approver"
#: A recipient let the deadline pass. Not a refusal: the buyer never said no.
MILESTONE_EXPIRED = "expired"
#: The seller pulled it.
MILESTONE_CANCELLED = "cancelled"

MILESTONES: tuple[str, ...] = (
    MILESTONE_DRAFT,
    MILESTONE_AWAITING_SIGNATURE,
    MILESTONE_APPROVED,
    MILESTONE_REFUSED_BY_SIGNER,
    MILESTONE_REFUSED_BY_APPROVER,
    MILESTONE_EXPIRED,
    MILESTONE_CANCELLED,
)

MILESTONE_LABELS: dict[str, str] = {
    MILESTONE_DRAFT: "Draft",
    MILESTONE_AWAITING_SIGNATURE: "Awaiting signatures",
    MILESTONE_APPROVED: "Approved",
    MILESTONE_REFUSED_BY_SIGNER: "Refused by a signer",
    MILESTONE_REFUSED_BY_APPROVER: "Blocked by the approver",
    MILESTONE_EXPIRED: "Expired",
    MILESTONE_CANCELLED: "Cancelled",
}

#: The two refusals, and why they are not one milestone. "APPROVER | Must approve
#: before signers can sign": an approver who says no stops the plan before any
#: signature is gathered, so the seller can fix the plan and send it again. A
#: signer who says no is refusing the terms themselves, so the plan is not going
#: back out unchanged. Recording both as "rejected" would lose exactly the
#: distinction the seller needs to know which one happened.
REFUSAL_MILESTONES: dict[str, str] = {
    APPROVER: MILESTONE_REFUSED_BY_APPROVER,
    SIGNER: MILESTONE_REFUSED_BY_SIGNER,
}

REFUSAL_RATIONALE = (
    "An approver refuses before signers can sign, so the seller can fix the plan and "
    "send it again. A signer refuses the terms, so it does not go back out unchanged. "
    "The research's APPROVER rule is the reason these are two milestones."
)

#: "advance the plan" - the research says completion advances the plan, so the
#: milestone flip is not the last thing an approval does.
MILESTONE_ADVANCE_QUOTE = (
    "The sales room consumes DOCUMENT_COMPLETED ... to flip the MAP milestone to Approved, "
    "advance the plan, and notify the owner."
)

# --------------------------------------------------------------------------- #
# Invitation path
# --------------------------------------------------------------------------- #

#: The research offers both. "either iframe ``https://app.documenso.com/embed/
#: direct/{token}`` or redirect to ``https://app.documenso.com/d/{token}``" and the
#: email invite, and then says the embed is what lets "a MAP approval live
#: **inside** the sales room".
INVITE_PATH_EMBED = "embed"
INVITE_PATH_REDIRECT = "redirect"
INVITE_PATH_EMAIL = "email"
INVITE_PATHS: tuple[str, ...] = (INVITE_PATH_EMBED, INVITE_PATH_REDIRECT, INVITE_PATH_EMAIL)

INVITE_PATH_LABELS: dict[str, str] = {
    INVITE_PATH_EMBED: "Open the plan inside this sales room",
    INVITE_PATH_REDIRECT: "Open the plan in a full window",
    INVITE_PATH_EMAIL: "Email every recipient a signing link",
}

#: The vendor's two direct-link URLs. "URL ``https://app.documenso.com/d/{token}``,
#: embed ``https://app.documenso.com/embed/direct/{token}``".
DIRECT_LINK_URL = "https://app.documenso.com/d/{token}"
DIRECT_EMBED_URL = "https://app.documenso.com/embed/direct/{token}"

# --------------------------------------------------------------------------- #
# Notice reasons
# --------------------------------------------------------------------------- #

#: The researched reasons the event vocabulary the room keeps. Served so a page
#: labels a reason without compiling a list of its own.
REASONS: dict[str, str] = {
    "plan_distributed": "The plan went out. Recipients can sign now.",
    "recipient_opened": "A recipient opened the plan.",
    "recipient_signed": "A recipient signed.",
    "approver_approved": "The approver approved. Signers can sign now.",
    "plan_approved": "Every recipient finished. The milestone is approved.",
    "refused_by_signer": "A signer refused the plan.",
    "refused_by_approver": "The approver refused the plan. Signers never got to sign.",
    "recipient_expired": "A recipient let the signing deadline pass.",
    "plan_cancelled": "The seller cancelled the plan.",
    "reminder_sent": "A reminder went to a recipient who has not signed.",
    "signers_blocked": "Signers cannot sign yet. The approver has not approved.",
    "duplicate_event": "The vendor sent this event again. Nothing changed.",
    "unauthenticated_event": "An event arrived without the right secret. Nothing was recorded.",
    "event_not_yet_due": "An event arrived before the plan went out.",
}

# --------------------------------------------------------------------------- #
# The invariants, restated for a client
# --------------------------------------------------------------------------- #

#: The guardrail the research states as a limit on the API. It is restated here as
#: a rule this product keeps, because a workflow that could sign for a buyer would
#: not be a mutual approval at all.
NEVER_SIGN_FOR_A_RECIPIENT = (
    "This product never signs for a buyer. Recipients sign themselves, and the "
    "vendor will not let an API sign on their behalf."
)

NEVER_SIGN_QUOTE = (
    "The API cannot: Sign documents on behalf of recipients (recipients must sign themselves)."
)

SIGNED_PDF_RULE = (
    "Retrieve the signed PDF until all recipients have completed signing is not "
    "possible before then. The plan is only approved once every recipient has finished."
)

SIGNED_PDF_QUOTE = "Retrieve the signed PDF until all recipients have completed signing."

#: What the room does with the external id. "``externalId`` is the join key back to
#: the deal room". Its shape here is derived, and the derivation is recorded in
#: :mod:`dsr.scheduling_meetings.inferences`.
EXTERNAL_ID_QUOTE = (
    "Track which document belongs to which transaction in your system: "
    "https://app.documenso.com/d/abc123xyz?externalId=order-12345"
)

WHITE_LABEL_QUOTE = "CSS variables for white-labelling the signing surface"


def describe() -> dict[str, Any]:
    """The whole vocabulary, served as data."""
    return {
        "collections": list(COLLECTIONS),
        "roles": [
            {
                "role": role,
                "label": ROLE_LABELS[role],
                "signs": role in SIGNING_ROLES,
                "gates_signers": role == APPROVER,
                "meaning": APPROVER_QUOTE if role == APPROVER else ROLE_LABELS[role],
            }
            for role in RECIPIENT_ROLES
        ],
        "approver_quote": APPROVER_QUOTE,
        "signing_roles": list(SIGNING_ROLES),
        "required_roles_quote": REQUIRED_SIGNING_ROLE_QUOTE,
        "statuses": [
            {
                "status": status,
                "label": STATUS_LABELS[status],
                "meaning": (
                    DISTRIBUTE_TRANSITION_QUOTE
                    if status in (STATUS_DRAFT, STATUS_PENDING)
                    else STATUS_QUOTE
                ),
            }
            for status in STATUSES
        ],
        "distribute_quote": DISTRIBUTE_TRANSITION_QUOTE,
        "signing_orders": [
            {"order": order, "label": SIGNING_ORDER_LABELS[order]} for order in SIGNING_ORDERS
        ],
        "signing_order_quote": SIGNING_ORDER_QUOTE,
        "distribution_methods": [
            {"method": method, "label": DISTRIBUTION_METHOD_LABELS[method]}
            for method in DISTRIBUTION_METHODS
        ],
        "field_types": [
            {"type": field, "label": FIELD_TYPE_LABELS[field]} for field in FIELD_TYPES
        ],
        "coordinate_rule": {
            "min": COORDINATE_MIN,
            "max": COORDINATE_MAX,
            "unit": "percent of the page",
            "quote": COORDINATE_QUOTE,
        },
        "identifier_quote": IDENTIFIER_QUOTE,
        "events": [
            {
                "event": event,
                "label": EVENT_LABELS[event],
                "names_a_recipient": event in RECIPIENT_EVENTS,
                "meaning": RECIPIENT_EVENT_MEANINGS.get(event, EVENT_LABELS[event]),
            }
            for event in EVENTS
        ],
        "webhook_secret_header": WEBHOOK_SECRET_HEADER,
        "webhook_verification_quote": WEBHOOK_VERIFICATION_QUOTE,
        "idempotence_quote": IDEMPOTENCE_QUOTE,
        "event_source_direct_link": EVENT_SOURCE_DIRECT_LINK,
        "external_id_keys": list(EXTERNAL_ID_KEYS),
        "external_id_quote": EXTERNAL_ID_QUOTE,
        "milestones": [
            {
                "milestone": milestone,
                "label": MILESTONE_LABELS[milestone],
                "is_a_refusal": milestone
                in (MILESTONE_REFUSED_BY_SIGNER, MILESTONE_REFUSED_BY_APPROVER),
                "is_terminal": milestone
                in (
                    MILESTONE_APPROVED,
                    MILESTONE_REFUSED_BY_SIGNER,
                    MILESTONE_REFUSED_BY_APPROVER,
                    MILESTONE_CANCELLED,
                ),
            }
            for milestone in MILESTONES
        ],
        "refusal_rationale": REFUSAL_RATIONALE,
        "milestone_advance_quote": MILESTONE_ADVANCE_QUOTE,
        "invite_paths": [
            {"path": path, "label": INVITE_PATH_LABELS[path]} for path in INVITE_PATHS
        ],
        "direct_link_url": DIRECT_LINK_URL,
        "direct_embed_url": DIRECT_EMBED_URL,
        "reasons": dict(REASONS),
        "invariants": {
            "never_sign_for_a_recipient": NEVER_SIGN_FOR_A_RECIPIENT,
            "never_sign_quote": NEVER_SIGN_QUOTE,
            "signed_pdf_rule": SIGNED_PDF_RULE,
            "signed_pdf_quote": SIGNED_PDF_QUOTE,
        },
        "white_label_quote": WHITE_LABEL_QUOTE,
    }
