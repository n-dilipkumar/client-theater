"""Every judgement call WF-095 made, with the alternative it rejected.

The specification leaves several joints open, and its own issue says so: "Quota accounting is
not decided by the research for this implementation." A derivation with no rejected
alternative is a guess wearing a derivation's clothes, so every entry here names at least two
options and says which one was taken and what the rejection would have cost.

The HTTP layer serves this table at ``GET /api/wf-095/decisions`` so the record is readable by
whoever reviews the feature, rather than buried in a docstring.
"""

from __future__ import annotations

from typing import Any

DECISIONS: dict[str, dict[str, Any]] = {
    "DERIVED_NEW_PACKAGE_QUOTE_ACCEPTANCE": {
        "question": "Which domain package should hold the WF-095 rules?",
        "left_open_by": (
            "The task brief lists seven packages as extendable (crm_integration, throttle, "
            "lead_score, visitor_identification, lead_qualification, round_robin, "
            "concierge_router) and names the same workflow, so the package choice was left to "
            "the implementer. No package on main held acceptance-method or signing-status "
            "vocabulary."
        ),
        "options": {
            "new_package_quote_acceptance": (
                "Create backend/dsr/quote_acceptance/ holding vocabulary, rules, inferences "
                "and engine, with its own wf095_ collection names."
            ),
            "extend_quoting_proposals": (
                "Append WF-095 modules to backend/dsr/quoting_proposals, which already reads "
                "wf086_quote and owns the proposal document the signature binds to."
            ),
            "extend_identity_verification": (
                "Append WF-095 modules to backend/dsr/identity_verification, which already "
                "owns the recipient verification gate."
            ),
            "extend_mutual_action": (
                "Append WF-095 modules to backend/dsr/mutual_action, the other e-signature-"
                "adjacent package on main."
            ),
        },
        "chosen": "new_package_quote_acceptance",
        "rejected_because": (
            "quoting_proposals owns rendering a proposal from a template, not collecting a "
            "signature, so extending it would make one package own two subjects and force two "
            "branches to edit one initializer, which is the collision this repository already "
            "suffered. identity_verification deliberately re-asserts its gate on every attempt "
            "and owns no document lifecycle, so the signing status machine and quota "
            "accounting sit outside its subject. mutual_action owns a task graph, a different "
            "subject again. A new package gives acceptance configuration, envelope, signers, "
            "signature events and signing status exactly one owner and lets no other branch "
            "collide with it."
        ),
        "audit_id": "jev-20261005T075612-8560-72925",
        "confidence": 1.0,
    },
    "DERIVED_TWO_PARTIES_ONE_BUYER_SIGNER": {
        "question": "How many signers does one e-signature envelope carry?",
        "left_open_by": (
            "The research describes 'Buyer contacts required to sign' as a ticked list (so "
            "more than one is possible) and 'Countersigners' drawn from the organisation (so "
            "more than one is possible), and it never bounds either. Its quota sentence names "
            "three signatures on one quote, so three is a figure the research treats as real."
        ),
        "options": {
            "two_parties_one_signer_each": (
                "An envelope carries at most two signers: one buyer contact and one "
                "countersigner. This build models the two-party acceptance the status machine "
                "describes."
            ),
            "n_party_envelope": (
                "An envelope carries any number of buyer signers and any number of "
                "countersigners, with the status machine advancing on the last of each."
            ),
            "single_signer_no_countersigner": (
                "An envelope carries one signer and countersignature is a separate envelope."
            ),
        },
        "chosen": "two_parties_one_signer_each",
        "rejected_because": (
            "The status machine the research names is strictly two-party: it moves to "
            "'Pending countersignature' on the buyer's signature and to 'Accepted' on the "
            "countersigner's. An n-party envelope would have to invent an 'n-th signature' "
            "state that the research does not name, and the three-signature quota sentence is "
            "about counting, not about the envelope's shape. Modelling the two parties the "
            "status machine actually describes keeps every transition sourced."
        ),
        "audit_id": None,
        "confidence": None,
    },
    "DERIVED_SIGNING_IMPLIES_VIEWING": {
        "question": "Must a buyer view the quote before signing, or does signing imply viewing?",
        "left_open_by": (
            "The data flow names the chain 'Pending signature -> Viewed - pending signature -> "
            "Pending countersignature -> Accepted' and the user flow has the buyer 'opens the "
            "shared link' before signing, so viewing is a step. It does not say what happens "
            "when a signature arrives while the status is still the first one."
        ),
        "options": {
            "signing_implies_viewing": (
                "Apply the status table until it stops changing, so a signature from "
                "pending_signature passes through viewed_pending_signature and lands on "
                "pending_countersignature."
            ),
            "require_viewing_first": (
                "Refuse a signature from pending_signature and answer that the buyer must open "
                "the quote first."
            ),
        },
        "chosen": "signing_implies_viewing",
        "rejected_because": (
            "Refusing the signature is the stricter reading, but it reaches a dead end the "
            "permissive reading does not. Under the strict reading a signature recorded at "
            "pending_signature leaves the envelope at pending_signature, and the buyer then "
            "opens the quote, which advances it to viewed_pending_signature. From there the "
            "buyer already signed so cannot sign again, and the countersigner is refused by "
            "the order rule, so nothing can ever move the envelope to Accepted. The permissive "
            "reading reaches the same state the user's own sequence would: the buyer opened the "
            "quote, then signed."
        ),
        "audit_id": None,
        "confidence": None,
    },
    "DERIVED_QUOTA_CEILING_IS_UNSPECIFIED": {
        "question": "What e-signature usage limit does this room enforce?",
        "left_open_by": (
            "The research states that limits 'are pooled per account by subscription and seat "
            "count and reset on the 1st' and gives no number, and the issue says outright "
            "'Quota accounting is not decided by the research for this implementation.'"
        ),
        "options": {
            "count_only_no_ceiling": (
                "Count each envelope as one usage and report the count with limit None, "
                "because the research states no number."
            ),
            "assume_a_fixed_default_ceiling": (
                "Enforce a fixed monthly ceiling such as 10 or 100 envelopes, chosen as a "
                "house default."
            ),
            "seat_count_derived_ceiling": (
                "Derive the ceiling from a seat count the room carries, mirroring the "
                "pooled-per-seat sentence."
            ),
        },
        "chosen": "count_only_no_ceiling",
        "rejected_because": (
            "Both rejected options invent a vendor limit the sources do not support. A fixed "
            "default would refuse valid envelopes at an arbitrary boundary, and a seat-derived "
            "ceiling would rest on a subscription model the research never quantifies. "
            "Counting usage and asserting no ceiling is the only answer the evidence supports, "
            "and it is recorded in QUOTA_UNSPECIFIED rather than hidden."
        ),
        "audit_id": None,
        "confidence": None,
    },
    "DERIVED_VERIFICATION_WINDOW_OPENS_ON_REQUEST": {
        "question": "When does the one-hour verification window start?",
        "left_open_by": (
            "The evidence says 'Buyers have one hour to complete the signature process after "
            "clicking Verify email', which fixes the end of the window relative to the click "
            "but does not say the window opens at the click or at the envelope send."
        ),
        "options": {
            "opens_on_verify_email_click": (
                "The window opens when the buyer clicks Verify email and the token is minted then."
            ),
            "opens_at_envelope_send": (
                "The window opens when the envelope is sent, whether or not the buyer has "
                "clicked Verify email."
            ),
        },
        "chosen": "opens_on_verify_email_click",
        "rejected_because": (
            "The sentence measures the hour 'after clicking Verify email', so the click is "
            "the event the hour follows. Opening at send would expire the window for a buyer "
            "who read the quote a day later and then clicked, which is the opposite of what the "
            "evidence says."
        ),
        "audit_id": None,
        "confidence": None,
    },
    "DERIVED_ACTIVITY_LOG_HAS_FOUR_ENTRIES": {
        "question": "Which signature events write a quote activity row?",
        "left_open_by": (
            "The research names four activities (Quote buyer signed, Quote countersigned, "
            "Quote reassigned, Signing attempt failed) but the status machine also advances on "
            "'viewed', which is not among them."
        ),
        "options": {
            "four_named_activities_only": (
                "Only the four named activities are written. 'Viewed' advances the status "
                "without an activity row, and 'Verified' does too."
            ),
            "add_viewed_and_verified_activities": (
                "Add a 'Quote viewed' and a 'Quote verified' activity so every status change "
                "has a log row."
            ),
        },
        "chosen": "four_named_activities_only",
        "rejected_because": (
            "The research names four activities and no others, and inventing two more would "
            "claim the product logs something the sources do not describe. A viewer is still "
            "visible on the board because 'viewed' advances the signing status, so nothing is "
            "hidden; only the un-named activity rows are withheld."
        ),
        "audit_id": None,
        "confidence": None,
    },
    "DERIVED_ACCEPTANCE_METHOD_DEFAULT_IS_ESIGNATURE": {
        "question": "What acceptance method does a quote carry when the payload names none?",
        "left_open_by": (
            "hs_acceptance_method is an optional property in the sense that a quote need not "
            "be sent for signature, and the research does not say what the method is when it "
            "is absent."
        ),
        "options": {
            "default_esignature": (
                "A payload that reaches this router with no method is treated as an "
                "e-signature, and validate_acceptance still refuses it unless a buyer signer "
                "is named."
            ),
            "default_clickwrap": (
                "An absent method means Accept without signature, the loosest method."
            ),
            "refuse_an_absent_method": (
                "Refuse a payload with no method and require the caller to name one."
            ),
        },
        "chosen": "default_esignature",
        "rejected_because": (
            "A caller reaching this router has already chosen an e-signature flow, so defaulting "
            "to the loosest method (clickwrap) would silently let an unsigned quote be accepted. "
            "Refusing an absent method is also safe but pushes a decision onto every caller for "
            "a field this workflow's own subject implies. Defaulting to e-signature, then "
            "refusing a signerless e-signature, keeps the tightest reading."
        ),
        "audit_id": None,
        "confidence": None,
    },
}


def decision_list() -> list[dict[str, Any]]:
    """Every decision, in the order they were made, for the /decisions route."""

    return [dict(value) for value in DECISIONS.values()]
