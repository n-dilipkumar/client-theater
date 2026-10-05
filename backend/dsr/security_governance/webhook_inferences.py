"""Every judgement call WF-082 made, with the alternative it rejected.

The specification for this workflow instructs an implementer directly, in the same
words it uses for every other workflow in the research: an implementer who needs a
flow the evidence does not contain "must derive it and record the derivation, not
assume it". This module is that record.

Each entry names the open question, the evidence that left it open, the options, the
one this build took, and - the part that matters - what the rejected options would
have cost. A derivation with no rejected alternative recorded is a guess wearing a
derivation's clothes, and a reviewer cannot tell the two apart.

The HTTP layer serves this table at ``GET /decisions`` under
:data:`~dsr.security_governance.webhook_vocabulary.ROUTER_PREFIX`, so the record is
readable by whoever reviews the feature rather than buried in a docstring that
nobody opens. ``GET /decisions/{id}`` returns one. The prefix is named as a constant
rather than written into the sentence above, because a docstring that cites a URL the
app does not serve is the same defect the contract forbids in an audit ``source``.

The three decisions that shaped the workflow
--------------------------------------------

**An inbound verifier shares the key material of an outbound signing vault.** The
specification says nothing about how this product should store the account's API key,
which it names as the HMAC secret ("the account's API key (HMAC secret)"). Storing it
in the clear in the same record a settings page reads is the obvious implementation
and it is wrong, so the secret is sealed at rest with the vault the product already
ships. The cost is a rejected alternate and a named reason; see the entry.

**A verification handler fetches nothing.** See :func:`refresh_ranges` in
:mod:`dsr.security_governance.webhook_rules` for the argument, and the entry here for
what the alternative would have cost.

**A refused delivery answers ``403`` for the IP check and ``401`` for the two
digests.** The specification documents no status codes at all. The distinction is the
interesting part: an IP refusal is a policy decision about a well-formed request, and
a digest refusal is a failure to authenticate one.
"""

from __future__ import annotations

from typing import Any

DECISIONS: dict[str, dict[str, Any]] = {
    "INFERRED_INBOUND_KEY_VAULT": {
        "question": (
            "How does this product hold the account's API key, which the specification "
            "names as the HMAC secret?"
        ),
        "left_open_by": (
            "The specification names the key twice and says nothing about storing it: the "
            "data sources line lists \"the account's API key (HMAC secret)\", and the data "
            "flow says to recompute \"HMAC-SHA256(api_key, event_time + event_type)\". No "
            "sentence describes key storage, key rotation or key scope."
        ),
        "options": {
            "seal_in_the_registration": (
                "Store the key sealed at rest, using the keyed encrypt-then-MAC vault the "
                "product already ships for CRM credentials, keyed by the room."
            ),
            "plaintext_in_the_registration": (
                "Store the key as an ordinary JSON field on the callback registration."
            ),
            "hash_the_key": (
                "Store only a digest of the key, exactly as the scoped API tokens do."
            ),
        },
        "chosen": "seal_in_the_registration",
        "rejected_because": (
            "Plaintext was rejected because a registration is readable through the core "
            "records API and through anyone holding the database file, and a secret an "
            "operator can re-read is not being used as a secret. Hashing was rejected "
            "because HMAC-SHA256 needs the key itself: a digest verifies a bearer token "
            "by re-hashing what the caller presents, and there is no re-hash here, the "
            "product has to *produce* a digest from the key on every delivery. "
            "Reusing the CRM vault's seal and open, rather than writing a second sealed "
            "format, is what keeps this from being a fourth crypto implementation in a "
            "product that already has one."
        ),
        "cost_of_the_choice": (
            "The vault falls back to a published demo key when no environment variable is "
            "set, so a fresh checkout verifies against a key that is in the source. Every "
            "surface that reads a registration says which key sealed it, and the fallback "
            "is published as :data:`~dsr.security_governance.webhook_vocabulary.DEMO_API_KEY` "
            "rather than being a quiet default."
        ),
    },
    "INFERRED_BOUNDED_ALLOWLIST_REFRESH": {
        "question": (
            "Does the verification handler fetch the published IP range file itself, or "
            "does it read a snapshot someone else refreshed?"
        ),
        "left_open_by": (
            'The specification says only "We recommend checking this list periodically to '
            'ensure your callback handler is secure" and that the file is "automatically '
            "updated if the IP addresses change\". It names no refresh interval, no fetch "
            "and no relationship between the handler and the file."
        ),
        "options": {
            "bounded_snapshot": (
                "The handler reads a stored snapshot. Refresh is a separate, explicit call "
                "that fetches, normalises, and stores the file with the time it was taken."
            ),
            "fetch_on_every_delivery": (
                "The handler fetches the range file before it checks anything."
            ),
            "fetch_in_a_background_task": (
                "The handler keeps the file fresh on a timer and never blocks a delivery."
            ),
        },
        "chosen": "bounded_snapshot",
        "rejected_because": (
            "Fetching per delivery was rejected because it puts a third party's uptime on "
            "the critical path of every delivery, inside a thirty second budget the "
            "provider enforces, and because a fetch that fails at that moment would have "
            "to choose between refusing every delivery and verifying nothing. A background "
            "timer was rejected because this product has no background worker to run it: "
            "the application is a single audited process, and adding a scheduler to a "
            "feature module is platform work a feature branch may not do. So the snapshot "
            "is explicit, and the answer carries its age on every decision - which is what "
            "makes the next two options answerable by a person at three in the morning."
        ),
        "cost_of_the_choice": (
            "A snapshot can be old, and an old allowlist is a real risk: it can refuse a "
            "delivery from an address the provider has since started using, or admit one it "
            "has stopped using. The workflow therefore stamps every IP decision with the "
            "snapshot's age and marks it stale past "
            ":data:`~dsr.security_governance.webhook_rules.DEFAULT_STALE_AFTER_SECONDS` s, "
            "and the frontend shows the age. The staleness window is itself an inference: "
            "the specification says \"periodically\" and gives no number."
        ),
    },
    "INFERRED_DUPLICATE_IS_ACKNOWLEDGED": {
        "question": (
            "What does the handler answer when a delivery passes all three checks but its "
            "event id has already been recorded?"
        ),
        "left_open_by": (
            'The specification says the handler "de-duplicates on event id" and separately '
            'that the callback URL "must return an HTTP ``200`` with a response body that '
            'contains the string ``Hello API Event Received``". It does not say whether the '
            "second of two identical deliveries gets the acknowledgement."
        ),
        "options": {
            "acknowledge_as_duplicate": (
                "Answer 200 with the magic string, record the delivery as a duplicate, and "
                "run no downstream effect."
            ),
            "refuse_as_duplicate": (
                "Answer 4xx, on the reasoning that the handler has nothing to do."
            ),
        },
        "chosen": "acknowledge_as_duplicate",
        "rejected_because": (
            "Refusing was rejected because the provider's stated model is that anything "
            "other than 200 plus the magic string \"will be sent again later\" - up to six "
            "times on a ladder that reaches twenty hours and fifteen minutes. A duplicate "
            "is a normal delivery under that model, not a fault, so refusing one spends "
            "the whole ladder on an event this handler has already accepted. It also fails "
            "sharply at the limit: after ten consecutive failures the provider "
            "\"automatically\" clears the callback URL, which is silent data loss with no "
            "error on either side. So the duplicate is acknowledged, and what it is is "
            "recorded so a person reading the log can still tell a re-delivery from a "
            "first delivery."
        ),
        "cost_of_the_choice": (
            "The dedupe store grows without bound unless something prunes it, and the "
            "specification gives no retention window, so the engine keeps every id it has "
            "seen and reports the count. Two consequences follow from the same rule and "
            "are recorded here rather than left for the next reader. A delivery with no "
            "event id cannot be de-duplicated at all, so it is refused with "
            "``event_id_missing`` rather than accepted unbounded: the specification says "
            "the handler de-duplicates on event id, and accepting an event it cannot "
            "recognise a second time is not honouring that. And the key is the "
            "registration as well as the id, so a room that registers twice for one "
            "provider does not de-duplicate the second registration's deliveries against "
            "the first's: two callbacks are two independent deliveries of the same event."
        ),
    },
    "INFERRED_REGISTRATION_MATCHES_ONE_KEY": {
        "question": (
            "Which callback registration verifies a delivery when more than one could match?"
        ),
        "left_open_by": (
            "The specification names two registration scopes, \"at the account level "
            "(`callback_url` on `/account`) or per API app (`callback_url` on "
            "`/api_app/{client_id}`)\", and a room in this product may hold several. It "
            "gives no header, no path segment and no other means of choosing between them."
        ),
        "options": {
            "named_header": (
                "The caller names the registration in a header, and the handler refuses a "
                "delivery that names none when more than one is bound to the room."
            ),
            "most_recent_wins": (
                "The newest matching registration verifies every delivery."
            ),
            "all_matching_registrations_verify": (
                "Every registration for the room is tried, and the delivery is accepted if "
                "any one of them verifies."
            ),
        },
        "chosen": "named_header",
        "rejected_because": (
            "\"Most recent wins\" was rejected because it silently changes which secret a "
            "delivery must be signed with, and the symptom is a total outage that looks "
            "like a provider fault. \"Try every registration\" was rejected for the same "
            "reason in reverse: it turns the room's secret count into a verification oracle, "
            "because a delivery is then accepted if **any** of N keys verifies, so an "
            "attacker is told something by how many keys a room holds. Naming the "
            "registration makes the choice explicit, and refusing an ambiguous delivery "
            "fails closed rather than guessing."
        ),
        "cost_of_the_choice": (
            "The header does not exist in the provider's protocol, so an integrator "
            "pointing a real provider at this handler has to set it in front of the "
            "delivery. That is a one-line proxy rule, it is named in the response as "
            ":data:`~dsr.security_governance.webhook_vocabulary.CREDENTIAL_HEADER`, and it "
            "is the honest cost of failing closed."
        ),
    },
    "INFERRED_REFUSAL_STATUS_CODES": {
        "question": "Which HTTP status does each refusal carry?",
        "left_open_by": (
            "The specification documents status codes for the provider's own API and none "
            "for a handler. It does say a non-200 is a failed callback, and the three "
            "checks are named as distinct steps."
        ),
        "options": {
            "distinct_codes": (
                "400 for a registration or payload this build will not accept, 403 for an "
                "IP that is not allowlisted, and 401 for a digest that does not verify."
            ),
            "one_code": (
                "One status for every refusal, so nothing is revealed about which check "
                "ran."
            ),
        },
        "chosen": "distinct_codes",
        "rejected_because": (
            "One code was rejected because this endpoint is not a login. There is no "
            "session to protect, no user account to enumerate, and the caller is a machine "
            "that already holds the secret - the information the distinct codes reveal is "
            "the difference between 'your proxy mangled the body' and 'the sender is not "
            "on the list', and that difference is the only thing that makes the error "
            "actionable. The catalogue publishes the remediation for every code, so the "
            "distinction is available without guessing."
        ),
        "cost_of_the_choice": (
            "The codes are this product's conventions rather than researched codes, and a "
            "reviewer who wants the provider's own codebook has none to copy. Each entry in "
            "the catalogue names this in its ``cause`` rather than implying the provider "
            "specified it."
        ),
    },
    "INFERRED_SOURCE_IP_IS_A_DECLARED_INPUT": {
        "question": "Where does the source address come from on a delivery that arrived by POST?",
        "left_open_by": (
            "The specification requires an IP-allowlist check and says the request is a "
            "multipart POST to the callback URL. It does not say how a handler behind a "
            "proxy reads the client address, and this product may sit behind one."
        ),
        "options": {
            "client_host_plus_declared_override": (
                "Read the socket peer address, and accept a declared address for the "
                "mounted route only, with the override recorded on every decision."
            ),
            "socket_peer_only": (
                "Read the socket peer address and ignore anything the request declares."
            ),
            "forwarded_header_only": (
                "Trust the forwarded header whenever it is present."
            ),
        },
        "chosen": "client_host_plus_declared_override",
        "rejected_because": (
            "Socket-only was rejected because behind a proxy the peer address is the "
            "proxy, so every delivery would be refused against an allowlist of provider "
            "addresses and the integration would look broken rather than misconfigured. "
            "Header-only was rejected for the opposite and more dangerous reason: a "
            "declared address is chosen by whoever sent the request, so trusting it alone "
            "means the allowlist checks nothing at all - an attacker sends the header "
            "themselves and passes. The compromise is to accept a declared address only "
            "where this product explicitly accepts one, and to write down on the delivery "
            "that it did."
        ),
        "cost_of_the_choice": (
            "A deployment that mounts this route on the public internet has turned off the "
            "first check, and nothing here can tell it that it did. The delivery log "
            "records ``source`` and ``source_is_declared`` on every row so the fact is at "
            "least visible after the fact, and the retry policy names the risk in one line."
        ),
    },
}


def count() -> int:
    """How many decisions are recorded."""
    return len(DECISIONS)


def describe() -> list[dict[str, Any]]:
    """Every decision, in a stable order, as the HTTP layer serves it."""
    return [
        {"id": decision_id, **decision} for decision_id, decision in sorted(DECISIONS.items())
    ]


def describe_one(decision_id: str) -> dict[str, Any] | None:
    """One decision by id, or ``None``."""
    decision = DECISIONS.get(decision_id)
    return {"id": decision_id, **decision} if decision else None


def ids() -> list[str]:
    """Every decision id, sorted."""
    return sorted(DECISIONS)