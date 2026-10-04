"""Every judgement call WF-080 made, with the alternative it rejected.

The specification for this workflow instructs an implementer through the issue's
"Notes for the implementer", and its extensibility section names what it left open: the
reference pattern is "subscribe to the *ready* event rather than polling status, handle
``202`` with ``Retry-After`` as a first-class case, and dedupe on a delivery id".

Each entry below names the open question, the evidence that left it open, the options,
the one this build took, and - the part that matters - what the rejected options would
have cost. A derivation with no rejected alternative recorded is a guess wearing a
derivation's clothes, and a reviewer cannot tell the two apart.

The HTTP layer serves this table at ``GET /api/wf-080/decisions`` so the record is
readable by whoever reviews the feature, rather than buried in a docstring that nobody
opens. ``GET /api/wf-080/decisions/{id}`` returns one.
"""

from __future__ import annotations

from typing import Any

DECISIONS: dict[str, dict[str, Any]] = {
    "DERIVED_EVENT_PAYLOAD_SHAPE": {
        "question": (
            "Where in the webhook payload does the document id live, and how does a "
            "handler find it?"
        ),
        "left_open_by": (
            'The user flow says only "The handler extracts the document `id` from the '
            'payload". No source quoted in the issue shows the payload shape, so the '
            "envelope is not known."
        ),
        "options": {
            "read_data_id_first": (
                "Read `data.id`, then `documentId`, then `document.id`, then a bare `id`."
            ),
            "read_flat_id_only": "Read only a top-level `id` and require that shape.",
            "require_an_explicit_mapping": (
                "Refuse every payload unless the caller names the key on the subscription."
            ),
        },
        "chosen": "read_data_id_first",
        "rejected_because": (
            "A single fixed key would fail on a payload that nests the document, which is "
            "the shape the vendor's own payloads are known to use, and a failure here is a "
            "missed PDF rather than a loud error. Requiring the caller to name the key adds "
            "a configuration surface the specification never describes, and a "
            "misconfiguration there would silently stop the workflow. Reading four shapes in "
            "a fixed order costs one function and works on all of them. The order matters "
            "and is documented: `data.id` is the document, while a bare top-level `id` in a "
            "webhook envelope is more often the notification's own identifier, which is "
            "what the dedupe header is for."
        ),
        "cost_of_the_choice": (
            "A payload that nests the document somewhere this build does not look is "
            "refused with `no_document_id` rather than resolved. That is a visible failure "
            "with a named cause, and the response lists the four shapes that were tried, so "
            "the next person to see one adds the fifth."
        ),
    },
    "DERIVED_RETRY_AFTER_SECONDS": {
        "question": "How many seconds does a 202 tell the client to wait?",
        "left_open_by": (
            'The evidence quotes the vendor: "The signed document file is not ready yet... '
            'Retry after the indicated number of seconds." No source gives the number.'
        ),
        "options": {
            "five_seconds": "Five seconds, raised per subscription if a room wants longer.",
            "thirty_seconds": "Thirty seconds, the interval most retry ladders use.",
            "one_second": "One second, the shortest wait that is not an immediate retry.",
        },
        "chosen": "five_seconds",
        "rejected_because": (
            "One second against a vendor that has already said the file is not ready is a "
            "busy loop, and the specification calls the signal a back-pressure mechanism "
            "rather than a suggestion. Thirty seconds is defensible for a batch job and "
            "wrong for an interactive room: a seller who watched a seller page go blank for "
            "half a minute after every signature would conclude the feature was broken. Five "
            "seconds keeps a client inside a couple of attempts and keeps a room quiet. It is "
            "a per-subscription field rather than a constant, because the room knows its own "
            "vault and the vendor does not."
        ),
        "cost_of_the_choice": (
            "The number is a judgement, not a measurement of the vendor's generation time, "
            "which the research does not report. A room with a slower vault will see more "
            "back-pressure responses than it needs to. Changing it is one field on one "
            "subscription, or one constant here."
        ),
    },
    "INFERRED_SUBSCRIPTION_SHARED_KEY": {
        "question": "Where does a subscription's shared key come from?",
        "left_open_by": (
            'The data-sources line marks it inferred: "webhook subscription record with a '
            "shared key `[inferred - the subscription model has a shared key per the docs "
            'index, e.g. "Update Webhook Subscription Shared Key"]`." The marker is the '
            "specification's, not this build's."
        ),
        "options": {
            "derive_from_document": (
                "Derive it as a hash of the vendor document id under a fixed label, so it "
                "is stable per document and differs between documents."
            ),
            "caller_supplied": "Let the caller supply the key on creation and store it.",
            "random_per_subscription": (
                "Generate a random key per subscription and never derive it."
            ),
        },
        "chosen": "derive_from_document",
        "rejected_because": (
            "A caller-supplied key is how the marker gets inherited by accident: a key that "
            "belongs to the vendor's own dashboard would be copied in, and this build would "
            "then store a credential it cannot rotate. A random per-subscription key cannot "
            "answer the question the specification implies the key exists for, which is "
            "joining a delivery to the document it concerns. A derivation is stable, "
            "reproducible, and carries nothing secret: it is a join value that two code "
            "paths can compute identically, and nothing authenticates with it."
        ),
        "cost_of_the_choice": (
            "The key is predictable to anyone who knows the document id, which is why it is "
            "used only for equality and never as a credential. If a future workflow needs a "
            "genuine shared secret for signing a delivery, it must be a separate derived "
            "value, and the placeholder has to be distinguished from it."
        ),
    },
    "DERIVED_PRE_COMPLETION_REFUSAL": {
        "question": (
            "What does the download route answer for a document whose signers have not "
            "all finished?"
        ),
        "left_open_by": (
            "The specification documents a 202 for a file still being produced and says "
            "nothing about a document that has not started producing one. The user flow puts "
            '"all signers complete" at step two, before generation, so the two cases are '
            "distinct and only the first is documented."
        ),
        "options": {
            "refuse_409": (
                "Answer 409 with the state, on the reasoning that back-pressure means work "
                "in progress and no work is in progress."
            ),
            "back_pressure_202": (
                "Answer 202 with Retry-After for every not-yet-ready document, so a client "
                "has one code path."
            ),
            "poll_until_ready": (
                "Hold the request open and poll the document until the PDF appears."
            ),
        },
        "chosen": "refuse_409",
        "rejected_because": (
            "A 202 here would tell a client to retry a PDF nobody has begun producing, and "
            "the client would do so for as long as the buyer takes to sign, which is days. "
            "That is a back-pressure signal used for a case it does not describe, and the "
            "distinction is the whole reason the specification calls 202 a first-class case "
            "rather than a generic wait. Polling is refused outright: 'subscribe to the "
            "*ready* event rather than polling status' is the extensibility note's own "
            "instruction, and a request that sleeps is polling by another name."
        ),
        "cost_of_the_choice": (
            "A client that treats every non-200 as retryable has two branches to write where "
            "one would do. That is the cost of telling the truth about the difference "
            "between 'not started' and 'in progress', and the response names which one it "
            "is in a `code` field."
        ),
    },
    "DERIVED_EMPTY_BODY_ON_202": {
        "question": (
            "The vendor sends no body with a 202. What does this room's route send, and "
            "how does the page learn the wait?"
        ),
        "left_open_by": (
            'The evidence is explicit: "Retry after the indicated number of seconds. No '
            "response body is returned.\" That fixes the vendor's shape and says nothing "
            "about a page in this product that has to render something."
        ),
        "options": {
            "mirror_the_vendor": (
                "Send 202, set Retry-After, send no body. The page reads the seconds from "
                "the attempt log rather than from the response."
            ),
            "answer_with_json": "Send 202 with a JSON body naming the wait.",
            "answer_200_with_a_status": (
                "Answer 200 with a body saying the PDF is not ready, and let the client decide."
            ),
        },
        "chosen": "mirror_the_vendor",
        "rejected_because": (
            "A JSON body would make this room's route incompatible with the vendor's own "
            "client, and the compatibility is the point of the reference pattern: a client "
            "written against the vendor would break here. Answering 200 would be worse than "
            "both, because it turns a documented back-pressure signal into an ordinary "
            "success and a client would cache it as an artifact. The cost of mirroring is "
            "that a page cannot read the wait from the response, so the route records every "
            "fetch as an attempt row and the page reads the most recent one. The log is a "
            "better source than the response anyway, because it survives the tab closing."
        ),
        "cost_of_the_choice": (
            "The page needs a second request after any 202 to learn what happened, where a "
            "JSON body would have answered in one. That is one extra round trip on the rare "
            "outcome, in exchange for byte-level compatibility with the vendor."
        ),
    },
    "DERIVED_THROTTLE_WINDOW": {
        "question": "When is a room throttled, and for how long?",
        "left_open_by": (
            'The specification records the mapping "429 -> `throttled`" and the '
            'instruction "Do not surface it as a generic failure", and names no limit, no '
            "window and no count."
        ),
        "options": {
            "ten_per_minute": (
                "Ten retrievals for one document inside a sixty second sliding window."
            ),
            "five_per_minute": "Five retrievals for one document inside a sixty second window.",
            "one_at_a_time": (
                "One outstanding retrieval per document, refusing a second while one is unanswered."
            ),
        },
        "chosen": "ten_per_minute",
        "rejected_because": (
            "One at a time is the most faithful reading of back-pressure and the least "
            "useful one: this room has no long-lived request to hold open, so a single "
            "outstanding call would mean a lock flag this build would then have to expire, "
            "which is a queue wearing a throttle's name. Five a minute refuses a client "
            "that is working correctly - a client that follows the 202 signal and retries "
            "after five seconds clears four windows' worth of headroom. Ten leaves room for "
            "a page that refetches on every state change while still bounding the damage a "
            "misbehaving caller can do."
        ),
        "cost_of_the_choice": (
            "The limit and the window are both judgements. The window is sliding rather "
            "than fixed so a client cannot burst twice the limit across a boundary. A "
            "throttled attempt is deliberately not counted, so a client that keeps retrying "
            "recovers instead of extending the window that refused it."
        ),
    },
    "DERIVED_ARTIFACT_BYTES": {
        "question": (
            "The workflow downloads a PDF from a vendor. This build has no vendor. What "
            "does the e-vault hold?"
        ),
        "left_open_by": (
            "The specification names the endpoints and their behaviour and does not say "
            "where the room's own copy lives. Nothing in the research covers storage."
        ),
        "options": {
            "metadata_only": (
                "Record the length, the media type and the digest, and serve nothing."
            ),
            "store_the_bytes": (
                "Store the artifact bytes in `records.data`, base64 encoded, and serve "
                "exactly those bytes."
            ),
            "record_a_remote_pointer": (
                "Record a vault path and a fetch URL, and fetch through on demand."
            ),
        },
        "chosen": "store_the_bytes",
        "rejected_because": (
            "Metadata only cannot demonstrate the property the specification's own sentence "
            "turns on - that `/download-protected` 'always returns the same digitally sealed "
            "PDF file' - because with no bytes there is nothing to compare and the claim "
            "becomes an assertion. Storing the bytes makes byte-stability a test rather "
            "than a promise, and makes the digest something a caller can check against the "
            "response it received. A remote pointer would keep this room out of the "
            "artifact's custody, which is the safer production shape and the one this "
            "workflow's real deployment would take; it is rejected here because it would "
            "make every property untestable and because nothing would prevent a caller from "
            "reading a pointer to a file they should not have."
        ),
        "cost_of_the_choice": (
            "A real executed agreement is tens of pages, and this store is not built for "
            "megabytes of base64 in a JSON column. The bytes here are a minimal generated "
            "PDF that stands in for the artifact, and the response says so in "
            "`generated: true` so no reader mistakes the demo file for a signed agreement. "
            "A production deployment should take the pointer option and move the bytes to "
            "object storage; that is platform work, not a change this workflow can make "
            "inside the feature contract."
        ),
    },
    "DERIVED_GENERATED_ARTIFACT": {
        "question": "Is the generated PDF a sealed artifact or a stand-in?",
        "left_open_by": (
            'The specification calls the result "a digitally sealed, verifiable artifact". '
            "This build has no signing key and no certificate chain, so it cannot produce "
            "one."
        ),
        "options": {
            "call_it_sealed": "Store it as the sealed artifact and describe it as sealed.",
            "call_it_generated": (
                "Call it generated, mark every response `generated: true`, and scope the "
                "claim to byte-stability."
            ),
            "store_no_artifact": "Refuse to serve bytes at all until a real vault exists.",
        },
        "chosen": "call_it_generated",
        "rejected_because": (
            "Describing a locally generated file as a digitally sealed artifact is a false "
            "claim about a signature that does not exist, and the specification's own "
            "criticality discipline elsewhere in this domain is that a control is never "
            "described as more than it is. Storing no artifact would make the whole workflow "
            "unreviewable, which is the failure the contract warns about when it says a "
            "feature whose page is empty is a feature nobody can review. So the bytes are "
            "stored, marked generated, and the scope of the claim is stated in SEAL_SCOPE on "
            "every response: what this room can prove is that the file has not changed."
        ),
        "cost_of_the_choice": (
            "The page carries a caveat it would not need against a real vendor, and a "
            "reviewer has to read past it to judge the workflow. That is the right direction "
            "of the error: a caveat that should not be there is noise, and a claim that "
            "should have been there is a defect."
        ),
    },
    "DERIVED_ROOM_SCOPE": {
        "question": (
            "The specification writes about a Document and a subscription. How does this "
            "product's room-scoped store hold them?"
        ),
        "left_open_by": (
            "The specification names `/public/v1/documents/{id}` and a subscription record, "
            "and this product's core dataset has rooms and documents rather than a "
            "vendor-document table. WF-067 already owns the e-signature envelope and writes "
            "its own plan rows under its own prefix."
        ),
        "options": {
            "own_document_rows": (
                "Store a `wf080_executed_document` row per room, keyed on this room's own "
                "record id, carrying the vendor document id as the join key."
            ),
            "read_wf067_rows": "Read WF-067's plan records as the documents.",
            "room_settings_row": "Store one settings record per room rather than per document.",
        },
        "chosen": "own_document_rows",
        "rejected_because": (
            "Reading WF-067's rows would couple this workflow to another ticket's schema and "
            "to its idea of what a document is, and WF-067's own documentation draws its own "
            "boundary. A settings record per room would make two agreements in one room "
            "indistinguishable, which is the case that matters: a room holds a room-level "
            "subscription and several executed agreements, and the whole workflow is the "
            "join between them."
        ),
        "cost_of_the_choice": (
            "Two workflows can now hold rows that both call themselves a document. Every "
            "collection is namespaced with the ticket, and the page states this workflow's "
            "own counts rather than claiming a product-wide one."
        ),
    },
    "DERIVED_CANCEL_KEEPS_THE_ROW": {
        "question": "What does cancelling a webhook subscription do to its row?",
        "left_open_by": (
            "The APIs list says `GET|PATCH|DELETE /public/v1/webhook-subscriptions/{uuid}` - "
            "manage. It does not say what the room keeps afterwards, and this product's "
            "guarantee is an audit trail that a reader can trust."
        ),
        "options": {
            "soft_delete": "Soft-delete the row and let it vanish from every read.",
            "cancel_in_place": (
                "Set `active: false` and `cancelled_at`, keep the row readable, and answer "
                "the vendor's DELETE verb."
            ),
            "delete_permanently": "Remove the row outright.",
        },
        "chosen": "cancel_in_place",
        "rejected_because": (
            "A soft delete would make the record of having listened unreachable, and the "
            "question a reader of an e-signature trail asks first is which deliveries arrived "
            "under a subscription that is now gone. Cancelling in place keeps the evidence "
            "and still gives the caller the semantics it expects from DELETE. A hard delete "
            "would break the guarantee outright."
        ),
        "cost_of_the_choice": (
            "Cancelled rows stay in the room's list and have to be filtered or badged there, "
            "so the page renders a cancelled state rather than hiding the row. That is one "
            "more state on the page and it is the state a seller most needs to see."
        ),
    },
}


def describe() -> list[dict[str, Any]]:
    """Every recorded decision, in a stable order."""
    return [{"id": key, **value} for key, value in DECISIONS.items()]


def describe_one(decision_id: str) -> dict[str, Any]:
    """One decision by id, or an empty mapping the HTTP layer turns into a 404."""
    found = DECISIONS.get(decision_id)
    if found is None:
        return {}
    return {"id": decision_id, **found}


def count() -> int:
    return len(DECISIONS)
