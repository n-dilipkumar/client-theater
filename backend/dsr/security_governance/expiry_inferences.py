"""WF-081: every judgement call this workflow rests on, and the alternative rejected.

The specification is unusually precise about the boundaries and silent about the
mechanism. It states the day counts, the dedupe window, the rounding rule and the
90-day ceiling as quoted facts, and then says nothing at all about where a
reminder is kept, who sweeps the expiry, or what an integrator receives in place
of the email the embedded flow mutes.

Those gaps are decisions. They are collected here, each with the alternative this
build rejected and what would have gone wrong, so a reviewer reads the list
instead of reconstructing it from a diff. Every entry names the evidence it was
taken from, and where the specification says nothing, that is stated rather than
papered over.
"""

from __future__ import annotations

from typing import Any

TICKET = "WF-081"


def _decision(
    decision_id: str,
    question: str,
    *,
    evidence: str,
    chosen: str,
    rejected: str,
    consequence: str,
    unsourced: bool = False,
) -> dict[str, Any]:
    return {
        "id": decision_id,
        "question": question,
        "evidence": evidence,
        "chosen": chosen,
        "rejected": rejected,
        "consequence": consequence,
        "unsourced": unsourced,
    }


#: Every decision, in the order a reader should meet them.
DECISIONS: list[dict[str, Any]] = [
    _decision(
        "which-expiry-shape",
        "The specification offers two shapes of expiry. Which one does this build?",
        evidence=(
            "Both are in the research. The signature-request shape: 'each incomplete "
            'signature record transitions to status_code: "expired"\'. The room-link '
            "shape: 'the seller sets expires_at on the link and the viewer's URL starts "
            "refusing access after the deadline'. The issue's own note asks the "
            "implementer to choose and to record the choice."
        ),
        chosen=(
            "The signature-request shape. A request carries expires_at, a list of "
            "signatures with their own status_code, and a reminder ledger."
        ),
        rejected=(
            "The room-link shape. It is the Papermark analogue and it is a thinner "
            "rule: one boolean and one refusal. It has no per-signer status to sweep, "
            "so it exercises neither of the two mechanisms the specification spends "
            "most of its automations section on."
        ),
        consequence=(
            "The 3-and-7-day cadence, the 24-hour dedupe, the terminal state and the "
            "surviving document are all observable. The Papermark rule is not built. A "
            "link can still be given an expiry through the same validation, but nothing "
            "refuses a viewer a link."
        ),
    ),
    _decision(
        "rounding-rounds-down-not-up",
        "The specification says expires_at 'will be rounded down to the nearest hour'.",
        evidence=(
            "'expires_at will be rounded down to the nearest hour.' The direction is "
            "named, not merely implied."
        ),
        chosen=(
            "Round down to the hour. The stored value is the rounded one and the "
            "response reports both the requested and the stored instant."
        ),
        rejected=(
            "Rounding to the nearest hour, which is what the phrase sounds like in "
            "ordinary English, and rounding up, which never shortens a deadline."
        ),
        consequence=(
            "A request for 17:59 expires at 17:00. Rounding up would grant a signer "
            "up to 59 extra minutes past the instant the seller named, which is the "
            "direction a rounding rule must never move a deadline."
        ),
    ),
    _decision(
        "the-bound-is-applied-before-rounding",
        "Is the 1-to-90-day range checked against the value the seller sent, or "
        "against the rounded value?",
        evidence=(
            "'must be an integer epoch timestamp in seconds between 1-90 days in the "
            "future' and 'will be rounded down to the nearest hour' are two separate "
            "sentences and the specification does not order them."
        ),
        chosen=(
            "Check the range against the value the seller sent, then round. A value "
            "inside the range but inside the final hour of its window still passes."
        ),
        rejected=(
            "Round first and check the rounded value, which would refuse a request for "
            "23 hours hence as being inside the hour that rounds below one day."
        ),
        consequence=(
            "The check reads as written: what the caller sent is what is validated. A "
            "caller whose value rounds below the minimum is still accepted, which is "
            "correct because the value the caller asked for was inside the rule."
        ),
    ),
    _decision(
        "no-expiry-means-never",
        "What happens to a request created without expires_at?",
        evidence=(
            "'Only signature requests that explicitly set an expires_at will expire. By "
            "default signature requests do not expire.' The specification states this "
            "as a boundary rule, in its own extensibility section."
        ),
        chosen=(
            "Absence is stored as null and read as never. The sweep skips it, the "
            "reminder scheduler skips it, and the signing check ignores it."
        ),
        rejected=(
            "A product-wide default expiry. Every room would then have agreements "
            "closing themselves without a seller asking for it, which is the opposite "
            "of what the sentence above forbids."
        ),
        consequence=(
            "A request with no expiry never closes. It stays pending until its signers "
            "act, which is the documented behaviour and is tested."
        ),
    ),
    _decision(
        "the-ledger-is-a-collection-not-a-field",
        "Where does the reminder scheduler state live?",
        evidence=(
            "The specification names 'reminder scheduler state' as a data source and "
            "does not say how it is stored."
        ),
        chosen=(
            "One record per reminder in its own collection, keyed to the request and "
            "the signer. A skip writes a row with outcome 'skipped'."
        ),
        rejected=(
            "A last_reminded_at field on each signature inside the request payload. It "
            "is fewer rows, and it makes the dedupe unreadable afterwards: the state "
            "that mattered is overwritten and nothing records that a skip happened."
        ),
        consequence=(
            "The dedupe is demonstrable. A reader can see the send and the skip as two "
            "rows and can see why the second was skipped. Storage is one row per "
            "reminder rather than one field per signer, which the schema-flexible store "
            "absorbs without a migration."
        ),
        unsourced=True,
    ),
    _decision(
        "reminders-are-computed-not-scheduled",
        "Who sends the 3-and-7-day reminder?",
        evidence=(
            "'As the deadline approaches, a reminder scheduler fires 3- and 7-day "
            "notices (with a 24-hour dedupe window).' The specification describes a "
            "scheduler and says nothing about this product having a background worker."
        ),
        chosen=(
            "No background thread. The rules are a pure function of the stored expiry "
            "and the ledger, and a route evaluates them. Time moves because a caller "
            "asks, not because a thread woke up."
        ),
        rejected=(
            "A background scheduler thread started at import time. It would need a "
            "clock the tests cannot move, it would write to the database with no "
            "request to attribute it to, and its audit rows would name no route."
        ),
        consequence=(
            "A request sent 6 days before expiry is reminded the first time it is "
            "read, and the 7-day reminder is skipped by the dedupe when it is read "
            "again inside 24 hours. The behaviour is the specification's and it is "
            "reproducible in a test, which a timer is not."
        ),
        unsourced=True,
    ),
    _decision(
        "sweep-is-an-explicit-route",
        "Does the expiry sweep run on its own?",
        evidence=(
            "'The expiry sweep marks incomplete signatures expired'. The specification "
            "names the sweep and does not say what triggers it."
        ),
        chosen=(
            "An explicit POST route that sweeps every due request in a room, and the "
            "same engine method a single request is swept through. The route names "
            "itself in the audit row."
        ),
        rejected=(
            "A sweep on every read of the request, or a sweep inside the signing check. "
            "A read that changes state is a write that lies about being a read, and the "
            "audit row would name a GET."
        ),
        consequence=(
            "Nothing expires until somebody sweeps. A seller who never calls the sweep "
            "route sees a request still pending past its deadline, which is a visible "
            "gap in the product rather than a silent one."
        ),
        unsourced=True,
    ),
    _decision(
        "sms-reminders-are-not-built",
        "The specification mentions SMS reminders. Are they built?",
        evidence=(
            "'if enabled on your account, they will see the 3 and 7 day signature "
            "request reminders via text message as well.' The sentence is "
            "conditional on the sender's account setting, and the specification does "
            "not say whether this workflow owns that setting."
        ),
        chosen=(
            "Not built. The reminder ledger records the channel as email, and the "
            "vocabulary names SMS as a source-documented channel this build does not "
            "send."
        ),
        rejected=(
            "Recording a per-signer SMS flag and pretending to send. A governance "
            "record claiming a text message was sent to a buyer, when this product has "
            "no SMS gateway, is the worst failure this workflow could ship."
        ),
        consequence=(
            "The cadence and the dedupe are right and the channel is email only. A "
            "buyer on an account with SMS enabled would still get only the email here.",
        ),
        unsourced=True,
    ),
    _decision(
        "the-wall-clock-carries-the-timezone",
        "The signer 'sees the expiry date ... in their own timezone'. Where does the "
        "conversion happen?",
        evidence=(
            "'During signing, the signer will see the signature request expiration date "
            "in the banner next to the number of required fields.' The specification "
            "does not name a timezone field, although 'signer email/preferred timezone' "
            "appears among its data sources."
        ),
        chosen=(
            "The stored instant is the only truth. A view is formatted in the timezone "
            "the caller names, and an unknown timezone falls back to UTC with the "
            "offset stated. The instant never moves."
        ),
        rejected=(
            "Storing a per-signer local deadline string. Two signers then hold two "
            "deadlines for one request, and the sweep would have to decide which of "
            "them is the real one."
        ),
        consequence=(
            "A timezone changes how a deadline reads, never when it fires. The sweep "
            "compares against the stored epoch seconds, which is the only comparison "
            "that cannot be wrong by an offset.",
        ),
    ),
    _decision(
        "timezone-comes-from-the-signature-not-a-parameter",
        "Does the API take a timezone parameter on the signing check?",
        evidence=(
            "The same sentence above. The specification describes the signer's view, "
            "and does not describe an API parameter."
        ),
        chosen=(
            "The preferred timezone is stored on the signature when it is sent, and the "
            "banner view reads it. A caller may override it for one read."
        ),
        rejected=(
            "A required timezone query parameter on the banner route. It makes the one "
            "route a client cannot call correctly fail, and it puts the deadline's "
            "correctness in the hands of the caller."
        ),
        consequence=(
            "The banner works with no parameters. A wrong stored timezone shows the "
            "wrong local time and the sweep is still right, which is the asymmetry "
            "this design accepts on purpose.",
        ),
        unsourced=True,
    ),
    _decision(
        "expiry-closes-the-mutation-path",
        "What exactly does expiry close?",
        evidence=(
            "'They will not be able to sign or modify the signature request.' and 'All "
            "parties to the signature request will still have access to the document "
            "including audit trail, similar to declined signature requests.'"
        ),
        chosen=(
            "The signing route and the expiry-update route both refuse on an expired "
            "request. The read route, the document and the audit trail stay open."
        ),
        rejected=(
            "Only refusing the signing route. A seller could then clear the expiry from "
            "an expired request and reopen it, which makes the terminal state "
            "terminal in name only."
        ),
        consequence=(
            "Expired is terminal in the sense the specification means. It cannot be "
            "undone by the seller, and the document is still readable, which is what "
            "the specification says a declined request gets."
        ),
    ),
    _decision(
        "a-completed-signer-is-not-swept",
        "Does the sweep touch a signer who has already signed?",
        evidence=(
            "'On expiry, unsigned signatures flip to expired ... Completed signers stay "
            "signed.' Named in the specification's own flow, step five."
        ),
        chosen="The sweep moves incomplete statuses only. A signed row is left alone.",
        rejected=(
            "Setting every status to expired at the deadline. It would erase the fact "
            "that a buyer signed, and the specification says twice that it does not "
            "happen."
        ),
        consequence=(
            "A request whose last signer signed before the deadline is completed "
            "rather than expired, and the signer's own status_code stays signed.",
        ),
    ),
    _decision(
        "the-event-stream-is-a-collection",
        "The embedded flow mutes email and sends an event. What serves that event?",
        evidence=(
            "'Emails are muted in all embedded signing flows. Integrations using "
            "embedded signing must consume the signature_request_expired event.' The "
            "Dropbox Sign event stream is named as a data source."
        ),
        chosen=(
            "The event is a stored row in this workflow's event collection, readable "
            "per request and per room. The page shows the delivery each signer got."
        ),
        rejected=(
            "An outbound webhook POST. This product sends no HTTP to a third party, and "
            "an integration cannot consume a row it has no way to read."
        ),
        consequence=(
            "An integration polls this product's event list rather than receiving a "
            "call. The event name is the vendor's, so a consumer written against the "
            "vendor's docs finds the name it expects.",
        ),
        unsourced=True,
    ),
]


def register() -> dict[str, Any]:
    """The whole register, as served at ``GET /inferences``."""
    return {
        "ticket": TICKET,
        "spec": "docs/research/digital-sales-room-workflows/wf/WF-081.md",
        "issue": 182,
        "count": len(DECISIONS),
        "decisions": [dict(entry) for entry in DECISIONS],
        "sources": [
            "https://developers.hellosign.com/api/manual-reference-pages/expiration.md",
            "https://developers.hellosign.com/docs/guides/sms-tools.md",
            "https://www.papermark.com/docs/guides/share-password-protected-link.mdx",
        ],
    }
