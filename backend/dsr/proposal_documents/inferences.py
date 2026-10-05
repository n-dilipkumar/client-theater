"""WF-103: every judgement call this workflow makes, in one inspectable place.

The research for WF-103 pins the *surface* tightly. It enumerates the state machine,
names the create body, the approval gate, the ``409`` on an early send, the
destructive quote update, the webhook de-duplication header, the HMAC signature, the
empty ``shared_link`` and its fallback, and two authentication methods. What it does
not do is decide the joints those statements leave open, and the joints are where a
build has to choose something.

Those decisions are collected here rather than left as comments in function bodies,
because a judgement call in a comment is one nobody re-reads and a wrong one becomes
product behaviour without anyone noticing. Each entry is:

* **named**, so it can be argued with by name;
* **traceable** - ``basis`` says what the research does and does not say;
* **bounded** - ``value`` is what this build chose, and ``change_it`` says how to change
  it without editing a function body;
* **visible** - :func:`describe` is served at ``GET /api/wf-103/inferences``, so a
  reviewer reads the list instead of reconstructing it from a diff.

One group is not inferences but boundaries, and is listed for that reason:
``NOT_BUILT`` records what this build deliberately does not do, because a feature
whose page does not show its own edges overstates itself.
"""

from __future__ import annotations

from typing import Any

from dsr.proposal_documents import vocabulary as vocab

#: The sentences from the research that govern the surface below.
SOURCED_QUOTES: tuple[str, ...] = (
    "Response returns status `document.uploaded`; poll `GET /public/v1/documents/{id}/status` "
    "or wait for the `document_state_changed` webhook until it reaches `document.draft`.",
    "If the document's template has an approval workflow, `POST /public/v1/documents/{id}/send` "
    "moves the document to `document.waiting_approval` instead of `document.sent`; ... after "
    "approval, call **send** again to move to `document.sent`.",
    "Attempting to send a document that is still in `document.uploaded` status returns a `409 "
    "Conflict` response.",
    "Any section or item omitted from the payload will be deleted.",
    "All webhook requests include an HMAC-SHA256 signature for verification using your shared key.",
    "retries are handled by de-duplicating on `X-PandaDoc-Webhook-Event-Id`",
    "`shared_link` field in the `recipients` array may be empty in webhook payloads due to "
    "asynchronous processing, and the fallback is Document Details.",
)

INFERENCES: tuple[dict[str, Any], ...] = (
    {
        "id": "no-outbound-document-api-call",
        "topic": "whether this workflow reaches a document API at all",
        "basis": (
            "The research documents ten sources, the create body, the full endpoint list and "
            "an HMAC-signed webhook. It documents no credential this product holds, and the "
            "product has no HTTP client for a document vendor. Writing a fake client against a "
            "real vendor's host would be a claim the product cannot back."
        ),
        "chosen": "derive_everything_locally",
        "value": (
            "A document, its id, its per-recipient shared link and its status are computed from "
            "the CRM record and written to the audited store. Every derived identifier is built "
            "on the reserved .invalid host, every document row carries "
            f"{vocab.DOCUMENT_URL_IS_DERIVED}: True, and every response carries a prose note "
            "saying so."
        ),
        "rejected": "a real HTTP client with a configured API key",
        "rejection_cost": (
            "There is no credential to configure and no environment that holds one, so a client "
            "would fail at connect time and make every assertion about the workflow fail for a "
            "reason that has nothing to do with the workflow."
        ),
        "change_it": "the derived_* functions in proposal_documents.vocabulary",
    },
    {
        "id": "DERIVED_AUTH_IS_THE_API_KEY_HEADER",
        "topic": "which of the two researched authentication methods the room records",
        "basis": (
            "'Auth: `API-Key` header or OAuth2 (`read write` scopes, `/oauth2/authorize` + "
            "`/oauth2/access_token`).' Two are offered and the room has to pick one. Both are "
            "sourced; the research does not say which an integration should choose."
        ),
        "chosen": "api_key_header",
        "value": (
            f"{vocab.AUTH_METHOD_CHOSEN} using the {vocab.AUTH_API_KEY_HEADER} header. The OAuth2 "
            "scopes, authorize path and token path are published alongside it so a deployment "
            "switching to OAuth2 has the researched values without reading the issue."
        ),
        "rejected": "the OAuth2 authorization-code flow",
        "rejection_cost": (
            "OAuth2 needs a redirect URI, a token store and a refresh schedule, none of which "
            "this product has. A recorded auth method that named it would advertise a capability "
            "the room does not have."
        ),
        "change_it": "AUTH_METHOD_CHOSEN and AUTH_API_KEY_HEADER in proposal_documents.vocabulary",
    },
    {
        "id": "DERIVED_SIGNATURE_HEADER_NAME",
        "topic": "the header the HMAC signature arrives in",
        "basis": (
            "'All webhook requests include an HMAC-SHA256 signature for verification using your "
            "shared key.' The research names the algorithm and the key and not the header."
        ),
        "chosen": "x_pandadoc_signature",
        "value": (
            f"The room reads {vocab.WEBHOOK_SIGNATURE_HEADER} and compares a hex digest with "
            "hmac.compare_digest. The de-duplication header is the one the research *does* name."
        ),
        "rejected": "reading the signature from a header name invented per call site",
        "rejection_cost": (
            "A signature check is only meaningful if the header is a single agreed name; a "
            "per-call-site spelling is how a receiver ends up accepting an unsigned request on "
            "one route and refusing it on another."
        ),
        "change_it": "WEBHOOK_SIGNATURE_HEADER in proposal_documents.vocabulary",
    },
    {
        "id": "DERIVED_CANONICAL_SIGNATURE_BYTES",
        "topic": "what bytes a webhook signature is computed over",
        "basis": (
            "The research names the algorithm and the shared key. A body arriving at a receiver "
            "is parsed JSON by the time the workflow sees it, and two parsers may order the same "
            "object differently."
        ),
        "chosen": "sorted_compact_json",
        "value": (
            "verify_signature re-serialises the body with sorted keys and compact separators, so "
            "the check is independent of key order. A raw body is signed as received."
        ),
        "rejected": "signing the stored record or the canonical form of the patched document",
        "rejection_cost": (
            "The signature covers what the sender sent, not what this room later computed from "
            "it; verifying anything else would pass a request whose payload had been altered."
        ),
        "change_it": "_canonical in proposal_documents.rules",
    },
    {
        "id": "DERIVED_STAGE_MAPPING",
        "topic": "which CRM opportunity stage each document state writes",
        "basis": (
            "'Update Opportunity status when PandaDoc status is updated' is the instruction. The "
            "research names no stage vocabulary, and the deal record in this product owns its own."
        ),
        "chosen": "map_every_researched_state",
        "value": (
            "stage_for covers all eleven researched states onto six room stages, ending a deal "
            "only for completed, paid, cancelled and declined. A caller may pass its own stage, "
            "and the result reports stage_source so an override is visible."
        ),
        "rejected": "write only a boolean 'proposal sent' flag",
        "rejection_cost": (
            "The instruction is to update the opportunity *status*. A flag cannot hold a viewed "
            "or a paid proposal, and a seller reading a deal with a flag and no stage learns "
            "nothing."
        ),
        "change_it": "stage_for in proposal_documents.rules",
    },
    {
        "id": "DERIVED_CLOSE_DATE_ONLY_WHEN_THE_DEAL_ENDS",
        "topic": "when a close date is written back",
        "basis": (
            "'opportunity stage/close date' names both. Nothing in the research says a document "
            "that has merely been sent closes a deal."
        ),
        "chosen": "only_for_won_and_lost",
        "value": (
            "sync_from_document proposes a close date only when the derived stage is closed_won or "
            "closed_lost, and otherwise returns None so the caller leaves the field alone."
        ),
        "rejected": "stamp a close date on every sync",
        "rejection_cost": (
            "A proposal that was sent an hour ago would close the opportunity, and the seller's "
            "forecast would change on a read the buyer never responded to."
        ),
        "change_it": "sync_from_document in proposal_documents.rules",
    },
    {
        "id": "DERIVED_DELETIONS_MUST_BE_ACKNOWLEDGED",
        "topic": "how the destructive quote update is made explicit",
        "basis": (
            "'Any section or item omitted from the payload will be deleted.' The research states "
            "the behaviour and says nothing about consent. A write that deletes by omission is "
            "the one default in this workflow that is invisible to the caller who triggered it."
        ),
        "chosen": "require_an_explicit_acknowledgement",
        "value": (
            "require_quote_update returns the exact sections and items the payload would remove, "
            f"and refuses with {vocab.ACKNOWLEDGE_DELETIONS_KEY} set only when the payload is "
            "destructive and the caller has not acknowledged it."
        ),
        "rejected": "honour the omission silently, as the vendor does",
        "rejection_cost": (
            "A payload that forgets one section is indistinguishable from one that meant to "
            "remove it, and the research gives the caller no way to say which. Making the "
            "deletion explicit costs one boolean and turns a silent data loss into a decision."
        ),
        "change_it": "require_quote_update in proposal_documents.rules",
    },
    {
        "id": "DERIVED_EMPTY_SHARED_LINK_FALLS_BACK_BEFORE_IT_IS_DERIVED",
        "topic": "the order the shared link is resolved in",
        "basis": (
            "The research names two sources: the webhook's own `shared_link`, which 'may be empty "
            "... due to asynchronous processing', and Document Details, which the automation note "
            "names as the fallback. It does not describe a document that has neither."
        ),
        "chosen": "webhook_then_details_then_derived",
        "value": (
            "shared_link_for tries the webhook link, then the document's details link, then the "
            "derived stand-in, and returns which source it used. The fallback note is served to "
            "the page so a reader shown a Document Details link knows it is a fallback."
        ),
        "rejected": "returning the empty string the webhook sent",
        "rejection_cost": (
            "An empty link is what the research warns about, and handing it to a page as if it "
            "were a link produces a button that goes nowhere. A derived stand-in is only reached "
            "after both researched sources, and it announces itself."
        ),
        "change_it": "shared_link_for in proposal_documents.rules",
    },
    {
        "id": "DERIVED_WEBHOOK_REPLAY_WRITES_NOTHING",
        "topic": "what a replayed webhook does to the document",
        "basis": (
            "'retries are handled by de-duplicating on `X-PandaDoc-Webhook-Event-Id`' says the "
            "id is the key. It does not say what the receiver does when the key repeats."
        ),
        "chosen": "return_duplicate_and_change_nothing",
        "value": (
            "A repeated event id returns outcome 'duplicate', writes no state change, no recipient "
            "row, no CRM sync and no activity, and leaves the document's revision untouched."
        ),
        "rejected": "re-apply the payload and let the new state win",
        "rejection_cost": (
            "Webhooks are retried, so a replayed earlier event would move a completed document "
            "back to sent. De-duplication has to be idempotent, which means doing nothing on the "
            "second arrival, not writing the same value twice."
        ),
        "change_it": "is_duplicate and the consume path in proposal_documents.engine",
    },
    {
        "id": "DERIVED_ROOM_SIDE_GATE_STANDS_IN_FOR_THE_TEMPLATE_WORKFLOW",
        "topic": "whose approval the gate represents",
        "basis": (
            "'If the document's template has an approval workflow, sending moves the document to "
            "document.waiting_approval.' The approval workflow belongs to the template at the "
            "vendor, and the room cannot read or configure it."
        ),
        "chosen": "a_room_side_gate_recording_the_vendor_outcome",
        "value": (
            "The room records the approver, the decision and the state, and the send routes move "
            "the document between waiting_approval, sent and rejected using the researched state "
            "names. The gate is the room's record of the template's workflow, not a configuration "
            "of it."
        ),
        "rejected": "configuring a vendor approval workflow and polling it",
        "rejection_cost": (
            "That would be an outbound call, and it would put the gate's correctness outside the "
            "room's audit log, which is the one guarantee the product makes."
        ),
        "change_it": "the gate fields on the document record, and decide_approval in the engine",
    },
    {
        "id": "DERIVED_PRICING_TABLE_IS_COMPUTED_HERE",
        "topic": "where the pricing table's totals come from",
        "basis": (
            "The data flow reads 'CRM deal/quote record + product catalog'. The product catalog "
            "is the vendor's, reached over `GET /public/v2/product-catalog/items/search`, and the "
            "research does not say the room supplies pricing from its own product records. This "
            "build calls no vendor, so it cannot read that catalog."
        ),
        "chosen": "compute_totals_from_the_deals_own_line_items",
        "value": (
            "build_pricing_table sums price times qty, applies the row and table discount, then tax "
            "and fee, and rounds to two places. It reads only the line items the deal record "
            "carries."
        ),
        "rejected": "an unsourced guess that a separate product-catalog ticket provisions the rows",
        "rejection_cost": (
            "The research names no such ticket, and naming one that does not exist would make a "
            "missing input look like somebody else's problem. The totals are computed here and "
            "say so, and a deployment with a real catalog supplies its own line items."
        ),
        "change_it": "build_pricing_table in proposal_documents.rules",
    },
)

NOT_BUILT: tuple[dict[str, Any], ...] = (
    {
        "id": "no-outbound-request-to-pandadoc",
        "what_is_not_built": "Any HTTP call to a document API.",
        "why": (
            "The research documents the endpoints and no credential this product holds. A request "
            "would fail at connect time, so the workflow is derived locally and says so on every "
            "artefact."
        ),
        "instead": "the derived document body, id and links, each marked as derived and built on a reserved .invalid host",
    },
    {
        "id": "no-webhook-console-or-subscription-management",
        "what_is_not_built": "The webhooks console, the subscription history, and `POST /public/v1/webhook-subscriptions`.",
        "why": (
            "The apis_hit field lists nine webhook events and a subscription endpoint. This "
            "workflow consumes one of them, and building a console for the rest would be a "
            "surface with no consumer."
        ),
        "instead": "one receiver for document_state_changed, de-duplicated on the researched header",
    },
    {
        "id": "no-embedded-signing-session",
        "what_is_not_built": "`POST /public/v1/documents/{id}/session` and the pandadoc-signing JavaScript library.",
        "why": (
            "Embedded signing needs a vendor-issued session token and a vendor script, and both "
            "are outbound. The region vocabulary ('com' | 'eu') is published so a deployment can "
            "use it, but the session is not minted here."
        ),
        "instead": "the per-recipient shared link, resolved through the researched fallback",
    },
    {
        "id": "no-signed-pdf-e-vault-attachment",
        "what_is_not_built": "`POST /public/v1/documents/{id}/files` and `GET /public/v1/documents/{id}/files/{attachment_id}`.",
        "why": (
            "The signed PDF is fetched from the vendor's e-vault, and there is no e-vault here. "
            "A note record names where the file would be attached without claiming a file exists."
        ),
        "instead": "the CRM sync note names the document and its state, and no attachment is claimed",
    },
    {
        "id": "deal-authoring-is-not-here",
        "what_is_not_built": "Creating the deal or opportunity the document is built from.",
        "why": (
            "The user flow starts 'In your CRM, the deal/opportunity is won or a quote is "
            "created' - the record already exists when this workflow runs."
        ),
        "instead": "the deal collection is read as data, and the sync writes back to the deal that was named",
    },
)


def describe() -> dict[str, Any]:
    """The judgement calls, served so a reviewer reads the list instead of the diff."""
    return {
        "sourced_quotes": list(SOURCED_QUOTES),
        "inferences": [dict(entry) for entry in INFERENCES],
        "not_built": [dict(entry) for entry in NOT_BUILT],
    }
