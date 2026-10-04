"""Every judgement call this workflow rests on, and how to change each one.

The research for WF-067 is unusually explicit about the vendor's behaviour and
almost silent about what a sales room should do with it. It says what
Documenso will accept and what its webhooks report, and it leaves open the parts
this workflow actually turns on. The issue's own notes are blunt about it: "This
is a thin spec. The statement above the evidence block is not a formality... Most
of the decisions below are yours to derive."

So every derivation is recorded here, named, bounded and served, rather than left
as a comment in a function body where the next reader cannot find it or disagree
with it. Each entry states what the research fixed, what it left open, which
reading this build took, and what it would cost to choose differently.

Nothing here is a preference dressed as a rule. Where the research states
something, the entry says so and cites it. Where it does not, the entry says that
too, and the reading is defensible rather than arbitrary.
"""

from __future__ import annotations

from typing import Any

#: Each entry: the decision, the quote that fixes it (or ``None``), the reading
#: this build took, and what would go wrong otherwise. Served whole by
#: :func:`inferences` so a reviewer reads the list rather than reconstructing it
#: from a diff.
REGISTER: tuple[dict[str, Any], ...] = (
    {
        "id": "never-sign-for-a-recipient",
        "question": "What happens when a signer should have signed but did not?",
        "research_says": (
            'The API cannot: "Sign documents on behalf of recipients (recipients must '
            'sign themselves)."'
        ),
        "reading": (
            "This product never writes a signature. A recipient's status changes only "
            "because a verified event said it did. No route accepts a status a caller "
            "asserts, and there is no route that could complete a plan on the buyer's "
            "behalf."
        ),
        "otherwise": (
            "A mutual action plan whose approval the seller can produce on demand is "
            "not mutual, and the guardrail the vendor publishes would be the one part "
            "of it this product did not keep."
        ),
        "changeable_by": "Not a judgement call. The research states it as a limit.",
    },
    {
        "id": "external-id-is-derived",
        "question": "What format does externalId take?",
        "research_says": (
            'The research names the key and not its format: "externalId is the join key '
            "back to the deal room\", and it publishes the vendor's own example "
            '"?externalId=order-12345", which is a vendor transaction id this product '
            "cannot produce."
        ),
        "reading": (
            "dsr-map.<room>.<plan>.<token>, with a 12-character unguessable token. It "
            "carries every part the join must survive (which room, which plan) and stays "
            "one URL query parameter. A caller with its own transaction id sets "
            "external_id on the plan and the derivation never runs."
        ),
        "otherwise": (
            "A join key of just the room id would let a stale or forged event naming a "
            "different room resolve to this room's plan. The token is what makes two "
            "plans in one room unconfusable."
        ),
        "changeable_by": "Set external_id on the plan. The derivation is the default, not a policy.",
    },
    {
        "id": "approver-refusal-differs-from-signer-refusal",
        "question": "Does a DOCUMENT_REJECTED from an approver mean the same as one from a signer?",
        "research_says": '"APPROVER | Must approve before signers can sign".',
        "reading": (
            "No. Two milestones: refused_by_approver and refused_by_signer. An approver "
            "refuses before any signature is gathered, so the seller can fix the plan and "
            "send it again. A signer refuses the terms, so it does not go back out "
            "unchanged. Recording both as rejected loses exactly the distinction the "
            "seller needs."
        ),
        "otherwise": (
            "A seller reading 'rejected' cannot tell whether to edit the plan and resend "
            "or to abandon it, and the approver gate - the one role the research "
            "emphasises - would be indistinguishable from any other refusal."
        ),
        "changeable_by": "Refusal milestones are served from vocabulary.MILESTONES.",
    },
    {
        "id": "invitation-path-is-the-embed",
        "question": "Email invite, redirect, or embed?",
        "research_says": (
            "The research offers all three and then says the embed is the one that lets "
            '"a MAP approval live inside the sales room": "either iframe '
            "https://app.documenso.com/embed/direct/{token} or redirect to "
            'https://app.documenso.com/d/{token}", and separately that "After '
            'distribution, recipients receive an email with a link to sign the document."'
        ),
        "reading": (
            "Embed is the default, because the research names in-room approval as the "
            "reason this workflow exists. Email and redirect are both offered as "
            "distributable alternatives on the plan rather than left out, because the "
            "research quotes them as first-class."
        ),
        "otherwise": (
            "Choosing email alone would make this a workflow that emails a PDF, which is "
            "what the research says this workflow is *not* for. Choosing embed alone would "
            "refuse a path the vendor's own documentation calls a normal option."
        ),
        "changeable_by": "distribution_method on the plan.",
    },
    {
        "id": "webhook-events-are-accepted-unknown-ones-refused",
        "question": "What happens to a webhook whose event name this build does not know?",
        "research_says": (
            "The research enumerates fourteen event names and says 'Process "
            "idempotently - Webhooks may be retried, so handle duplicate events', and "
            "'Verify the signature - Check the X-Documenso-Secret header matches your "
            "configured secret'."
        ),
        "reading": (
            "An unknown name is refused with 422 and writes nothing. A caller holding the "
            "right secret can send any string, and storing it would let that string be "
            "rendered on a page as though it were a real event. The fourteen are a closed "
            "list because the research closes it."
        ),
        "otherwise": (
            "A vendor that adds a fifteenth event would silently stop being reported, and "
            "a sender holding the secret would be able to write arbitrary text into the "
            "event log."
        ),
        "changeable_by": "vocabulary.EVENTS.",
    },
    {
        "id": "duplicate-events-are-not-refusals",
        "question": "Is a retried webhook an error?",
        "research_says": "Process idempotently - Webhooks may be retried, so handle duplicate events.",
        "reading": (
            "It is answered 200 and reported as a duplicate. The event is recorded once "
            "with a count of the attempts, and the second delivery changes no milestone "
            "and writes no second notice. A retry is not the vendor doing anything wrong."
        ),
        "otherwise": (
            "Answering an error would make the vendor retry harder for a delivery that "
            "succeeded, and treating it as new would flip an approved plan back to "
            "awaiting signature."
        ),
        "changeable_by": "events.event_fingerprint.",
    },
    {
        "id": "an-unauthenticated-event-writes-nothing",
        "question": "What does a failed secret check leave behind?",
        "research_says": "Verify the signature - Check the X-Documenso-Secret header matches your configured secret.",
        "reading": (
            "Nothing at all. Not the event, not the recipient's status, not the "
            "milestone. An event that failed authentication is the one input to this "
            "product that has not been shown to be entitled to anything, and the row it "
            "would leave is indistinguishable from a real one to everyone reading later."
        ),
        "otherwise": (
            "Any unauthenticated caller could mark a buyer's plan approved, and the "
            "audit log would show a plausible event rather than a forged one."
        ),
        "changeable_by": "Not a judgement call. It is the meaning of 'verify'.",
    },
    {
        "id": "a-plan-with-no-secret-can-never-authenticate",
        "question": "What if a plan was created without a webhook secret?",
        "research_says": "The research requires the header check but does not say a secret is optional.",
        "reading": (
            "Refused, with a message naming the missing key. An unconfigured plan has no "
            "way to know who may move its milestone, and accepting any secret for it would "
            "be exactly the hole the check exists to close."
        ),
        "otherwise": (
            "A plan created from a template that forgot its secret would accept the first "
            "event anybody sent it."
        ),
        "changeable_by": "webhook_secret on the plan.",
    },
    {
        "id": "events-before-distribution-are-noted-not-applied",
        "question": "Can a plan be approved before it was sent?",
        "research_says": (
            "The research's own order: create, then distribute (status DRAFT to PENDING), "
            'then "Buyer opens".'
        ),
        "reading": (
            "An event about somebody acting on a plan that has not been distributed is "
            "recorded and applied to nobody. Believing it would approve a plan no buyer "
            "was ever sent, which is the failure this product exists to prevent."
        ),
        "otherwise": (
            "A spoofed or misrouted event would approve an unsent plan, and the milestone "
            "would claim a buyer approved something they never saw."
        ),
        "changeable_by": "events.apply_event checks plan.distributed_at.",
    },
    {
        "id": "signers-blocked-is-a-conflict-not-a-refusal",
        "question": "What does a signer see while the approver has not approved?",
        "research_says": "APPROVER | Must approve before signers can sign",
        "reading": (
            "409 with the approvers still blocking named in the body. The caller's "
            "credentials are not in question - the plan simply is not ready - so 403 would "
            "be lying about whose problem it is."
        ),
        "otherwise": (
            "A signer told they are forbidden would look for a permission they do not have, "
            "rather than for the one person who has to act first."
        ),
        "changeable_by": "envelopes.signing_unlocked.",
    },
    {
        "id": "a-template-event-changes-no-plan",
        "question": "What do TEMPLATE_CREATED, UPDATED, DELETED and USED do?",
        "research_says": "The research lists the four template events alongside the document events.",
        "reading": (
            "Recorded and reported, and applied to nothing. They describe the vendor's "
            "template library, and this workflow tracks one plan's milestone. Treating a "
            "template update as a plan event would move a milestone on the strength of a "
            "document edit."
        ),
        "otherwise": (
            "Editing a template would appear to approve or reopen a plan, and a page "
            "would show a milestone that changed for no reason a seller could explain."
        ),
        "changeable_by": "events.apply_event.",
    },
    {
        "id": "reminders-are-noted-not-applied",
        "question": "Does DOCUMENT_REMINDER_SENT change a recipient's state?",
        "research_says": (
            "DOCUMENT_REMINDER_SENT is listed among the events, and the research notes "
            '"Signing Reminders: Automatically email recipients who have not yet signed on a '
            'configurable schedule."'
        ),
        "reading": (
            "Recorded, counted on the recipient, and it moves no milestone. A reminder is "
            "the vendor chasing somebody; it is not that somebody did anything."
        ),
        "otherwise": (
            "A plan could show progress from reminders alone, and 'how many people have "
            "signed' would stop meaning what it says."
        ),
        "changeable_by": "events.apply_event.",
    },
    {
        "id": "cc-and-viewer-never-hold-a-plan-open",
        "question": "Do non-signing recipients block completion?",
        "research_says": (
            "The research names CC and VIEWER as roles and separates them from the "
            "signing roles by saying a signer's status becomes SIGNED, and that "
            '"Retrieve the signed PDF until all recipients have completed signing" is not '
            "possible before then."
        ),
        "reading": (
            "Only SIGNER and APPROVER are counted towards completion. A CC who never opens "
            "the document cannot hold a mutual action plan open forever, and the research "
            "never says they are asked to act."
        ),
        "otherwise": (
            "One copied colleague who never clicked the link would leave a plan pending "
            "indefinitely, and the seller could see no way to finish it."
        ),
        "changeable_by": "vocabulary.SIGNING_ROLES.",
    },
    {
        "id": "the-signed-pdf-is-not-retrievable-before-completion",
        "question": "Does the room fetch the signed PDF?",
        "research_says": (
            'The API cannot "Retrieve the signed PDF until all recipients have completed signing."'
        ),
        "reading": (
            "This build stores no document bytes and offers no download. There is nothing "
            "to fetch before completion and nothing to store after it, because the room "
            "tracks the milestone and the audit trail rather than the file."
        ),
        "otherwise": (
            "A room that stored a PDF would hold buyer personal data this workflow has no "
            "stated basis to retain, and would have to answer what happens to it on a "
            "withdrawn plan."
        ),
        "changeable_by": "Not built. Recorded so its absence is a decision.",
    },
    {
        "id": "white-labelling-is-not-used",
        "question": "Does this product white-label the signing surface?",
        "research_says": '"CSS variables for white-labelling the signing surface".',
        "reading": (
            "Not by this build. The vendor can do it, and the evidence names it as an "
            "available feature rather than as a requirement, and no other workflow in this "
            "product passes CSS variables across a boundary. A page records the option "
            "exists so a reviewer can see it was considered."
        ),
        "otherwise": (
            "Injecting brand tokens into a third-party iframe is a platform-level concern "
            "that belongs to WF-017, which already owns white-labelling. Two features doing "
            "it would be the collision the feature host exists to prevent."
        ),
        "changeable_by": "A decision for WF-017, not for this workflow.",
    },
)


def inferences() -> dict[str, Any]:
    """The whole register, served as data.

    The page renders this on its last tab, so a reviewer reads the list rather
    than reconstructing it from a diff, and the sourced half sits beside the
    inferred half - because the point of the register is to see where the line
    between the research and this build falls.
    """
    return {
        "ticket": "WF-067",
        "count": len(REGISTER),
        "decisions": [dict(entry) for entry in REGISTER],
        "note": (
            "Each decision names what the research fixed, what it left open, the reading "
            "this build took, and what would go wrong otherwise. A decision with no "
            "research_says is one the evidence does not cover."
        ),
    }
