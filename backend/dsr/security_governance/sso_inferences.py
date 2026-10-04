"""Every judgement call WF-084 made, with the alternative it rejected.

The specification for this workflow instructs an implementer directly: "An implementer who
needs a flow the evidence does not contain must derive it and record the derivation, not
assume it." This module is that record.

Each entry names the open question, the evidence that left it open, the options, the one this
build took, and - the part that matters - what the rejected options would have cost. A
derivation with no rejected alternative recorded is a guess wearing a derivation's clothes,
and a reviewer cannot tell the two apart.

The HTTP layer serves this table at ``GET /api/wf-084/decisions`` so the record is readable
by whoever reviews the feature, rather than buried in a docstring that nobody opens.
``GET /api/wf-084/decisions/{id}`` returns one.
"""

from __future__ import annotations

from typing import Any

DECISIONS: dict[str, dict[str, Any]] = {
    "DERIVED_TENANT_MEMBERSHIP_CHECK": {
        "question": (
            "The specification says to assert the profile's organization id against the "
            "expected tenant. What happens when the two agree and this app has no record of "
            "that tenant?"
        ),
        "left_open_by": (
            "The specification's data flow stops at the assertion: the app 'asserts "
            "`profile.organizationId` matches the expected tenant and only then creates a "
            "session'. It names no third step."
        ),
        "options": {
            "assert_only": (
                "Assert the ids and grant the session. A correct id for an unknown tenant is "
                "enough, because the assertion is the whole requirement."
            ),
            "assert_and_require_a_tenant_record": (
                "Refuse a profile whose organization id no tenant record here names, and say "
                "so as a distinct reason."
            ),
            "auto_create_the_tenant": (
                "Create a tenant record on first sight of an organization id, so the "
                "assertion always finds a row."
            ),
        },
        "chosen": "assert_and_require_a_tenant_record",
        "jev_audit_id": "jev-20261004T181259-19372-79165",
        "jev_verdict": "pass",
        "jev_confidence": 0.99,
        "rejected_because": (
            "Asserting alone means any IdP the app trusts can mint a session for a tenant "
            "nobody configured, because the assertion only compares two strings the IdP "
            "controls. Auto-creating the tenant makes that worse: it turns the assertion "
            "into a no-op for an id nobody has seen. Requiring a record keeps the assertion "
            "and adds the provisioning question the specification's own Directory Sync flow "
            "already answers - a tenant exists here because an administrator connected it, "
            "and a user exists here because the directory named them."
        ),
        "cost_of_the_choice": (
            "A tenant that connects an IdP and expects sign-in before anybody opens the "
            "admin page is refused until its record exists. That refusal is the correct "
            "one, and its reason is reported separately so a caller can tell 'wrong tenant' "
            "from 'tenant not set up here'."
        ),
    },
    "DERIVED_CODE_LIFECYCLE": {
        "question": "What does a callback do with an authorization code that has expired?",
        "left_open_by": (
            "The evidence gives the bound and nothing else: 'The authorization code is valid "
            "for 10 minutes.' It does not say what a callback does with one that is past it."
        ),
        "options": {
            "reject": "Refuse the code and require the user to start a new sign-in.",
            "retry": "Attempt the exchange again and see whether the IdP accepts it.",
            "extend": "Accept it and treat the ten minutes as advisory.",
        },
        "chosen": "reject",
        "rejected_because": (
            "Retrying a credential the IdP has already retired spends a second attempt on a "
            "dead code and, on an IdP that counts attempts, turns an expiry into a lockout. "
            "Extending the bound contradicts the one number the research actually quotes, and "
            "it does so silently - the page would hold a session for a code the IdP considers "
            "spent, which is exactly the gap an authorization code exists to close. Rejecting "
            "costs the user one sign-in and keeps the property the code was minted for."
        ),
        "cost_of_the_choice": (
            "A user who leaves the tab open for eleven minutes sees a refusal rather than a "
            "session. The response carries the deadline and the seconds remaining at issue, "
            "so the login surface can explain it instead of showing a bare failure."
        ),
    },
    "DERIVED_DELIVERY_METHOD": {
        "question": (
            "The specification names two ways directory updates arrive and chooses neither. "
            "Which is built?"
        ),
        "left_open_by": (
            "The specification's evidence: 'Directory updates can be delivered to you via "
            "webhooks or retrieved using the Events API.' Named, not chosen."
        ),
        "options": {
            "webhook": (
                "One route the directory provider posts each change to, reconciled as it arrives."
            ),
            "events_api": (
                "A poll the app makes, reading the same changes from a cursor it keeps."
            ),
            "both_first_class": (
                "Two independent routes, each with its own reconciliation, and a tenant "
                "configures which one is live."
            ),
        },
        "chosen": "webhook",
        "rejected_because": (
            "The specification's motivation is drift: changes entered by hand are "
            "'error-prone and can lead to security vulnerabilities'. A webhook reconciles "
            "inside the same request that carries the change, so the window in which the "
            "directory and this app disagree is one request rather than one poll interval. "
            "Building only the Events API would accept that window on purpose and would need "
            "a cursor to be durable across restarts. The Events API is still reachable here "
            "as a pull of the same records, so a tenant that cannot expose an inbound route "
            "polls instead and reaches the same state."
        ),
        "cost_of_the_choice": (
            "A webhook is a public endpoint, so a directory provider has to be authenticated "
            "before its posts are believed. That check is this build's design rather than "
            "the specification's, and it is recorded as "
            "DERIVED_WEBHOOK_AUTHENTICATION rather than presented as sourced."
        ),
    },
    "DERIVED_WEBHOOK_AUTHENTICATION": {
        "question": "How does a directory provider's webhook post prove it is the provider?",
        "left_open_by": (
            "Nothing in the specification. It says updates 'can be delivered to you via "
            "webhooks' and names no signature, no token and no handshake."
        ),
        "options": {
            "shared_secret_header": (
                "Each directory holds a token this build generated, and the provider sends "
                "it as a header. A post without it is refused."
            ),
            "no_authentication": "Accept any post that names a directory this build knows.",
            "signed_body": "Verify a signature over the raw request body.",
        },
        "chosen": "shared_secret_header",
        "rejected_because": (
            "No authentication would let anyone who learns a directory id post a "
            "deprovision, and a deprovision is a write that removes access - the cheapest "
            "attack on this workflow is the one that locks every user out. A signed body is "
            "the stronger construction, and it is what a mature deployment would use, but it "
            "is not what the research describes and implementing HMAC verification here would "
            "be inventing a protocol the specification never mentions. A per-directory token "
            "is the smallest mechanism that closes the hole the no-authentication option "
            "leaves open."
        ),
        "cost_of_the_choice": (
            "A token proves the sender knows a secret, not that a person authorised the "
            "change. It is the honest limit of what a webhook can assert, and the page says "
            "so rather than describing the endpoint as verified."
        ),
    },
    "DERIVED_DEPROVISION_SEMANTICS": {
        "question": "What has to change when a directory removes a user?",
        "left_open_by": (
            "The specification quotes the operation and its definition - 'Deprovisioning is "
            "a process of removing a user from an app' - and its three SCIM operations, and "
            "names no field."
        ),
        "options": {
            "mark_inactive": (
                "Set an inactive flag on the user row and leave the row and its sessions."
            ),
            "soft_delete_the_row": "Remove the user from the live list and keep the history.",
            "revoke_everything": (
                "Remove the user from the directory list, revoke every session the user "
                "holds, and drop the groups they were in."
            ),
        },
        "chosen": "revoke_everything",
        "rejected_because": (
            "The issue's own note is the deciding evidence: a deprovision 'must remove "
            "access, not merely mark the user inactive in a way a session check ignores'. An "
            "inactive flag that a session check ignores is a leaver with access, which is the "
            "outcome the operation exists to prevent. The history is not thrown away: the row "
            "is soft-deleted, so the audit trail and the group membership at the moment of "
            "removal are still readable, and every session the user held is revoked rather "
            "than left to expire on its own."
        ),
        "cost_of_the_choice": (
            "A user the directory re-adds is provisioned again as a new row rather than "
            "reinstated in place, so a re-hire shows as a create rather than a restore. That "
            "is left as it is because the directory is the source of truth and a re-hire is a "
            "new directory event, and a reinstatement would need a rule for which of the two "
            "readings is right."
        ),
    },
    "DERIVED_GROUP_RESOLUTION": {
        "question": "A user is in several directory groups. Which access does that give them?",
        "left_open_by": (
            "The specification's automation note says directory groups 'inform access "
            "rules' and names no combination rule."
        ),
        "options": {
            "first_match": "The first group in the order the directory listed them wins.",
            "last_match": "The last listed group wins.",
            "strongest_wins": (
                "The most privileged role any matched group grants, whatever the order."
            ),
        },
        "chosen": "strongest_wins",
        "rejected_because": (
            "A directory is free to report membership in any order, and SCIM delivers group "
            "membership as a set rather than a list. First-match and last-match therefore "
            "make the same directory state resolve two different ways depending on an "
            "ordering the provider did not promise, which means the same person can be an "
            "admin on Monday and a member on Tuesday with nothing having changed. Taking the "
            "strongest role makes the answer a function of the memberships alone."
        ),
        "cost_of_the_choice": (
            "The privilege order is a list of three roles in this build rather than something "
            "the research ranks, and a tenant adding a role below member has to be told the "
            "order. It is served with the vocabulary, and the page shows the order rather "
            "than leaving it in the code."
        ),
    },
    "DERIVED_NO_MANUAL_OVERRIDE": {
        "question": "May an administrator grant one person access the directory has not given them?",
        "left_open_by": (
            "The specification describes the directory as the source of truth and motivates "
            "the whole workflow with the risk of manual entry. It does not say outright that "
            "an override is refused."
        ),
        "options": {
            "allow_with_expiry": (
                "Let an administrator grant access by hand, with an expiry date, and record "
                "that it was manual."
            ),
            "allow_permanently": "Let an administrator grant access by hand, indefinitely.",
            "refuse": "Store no manual access field at all, and say why on every response.",
        },
        "chosen": "refuse",
        "rejected_because": (
            "An override that survives a directory change defeats the workflow it appears to "
            "satisfy, which is the exact harm the research names: unauthorized access to "
            "resources. An override with an expiry is narrower but not safer, because the "
            "expiry is another date somebody has to remember and the next directory change "
            "would not touch it either. Refusing keeps one answer to 'who has access' and "
            "that answer is the directory, and it keeps the fix for a genuinely missing user "
            "in the place that can produce them, which is the directory itself."
        ),
        "cost_of_the_choice": (
            "An administrator cannot unblock a user before the directory names them, which "
            "is a real cost during an incident. The route that would carry such a request is "
            "not built, and the refusal names the directory as the place to fix it, so the "
            "answer to 'why can I not grant this' is on the response."
        ),
    },
    "DERIVED_HOSTED_SURFACES": {
        "question": "What does this build offer in place of the two vendor-hosted surfaces?",
        "left_open_by": (
            "The specification's product surfaces name an 'Admin Portal' for self-service "
            "setup and a 'Test SSO page in the WorkOS Dashboard'. Both belong to the vendor's "
            "own hosted product and neither can be built here."
        ),
        "options": {
            "omit_silently": "Build the API and say nothing about the two missing surfaces.",
            "reimplement_vendor_portal": (
                "Build a setup wizard that tries to stand in for the hosted Admin Portal."
            ),
            "record_the_substitute": (
                "Build the API and this page, and state on every response what each hosted "
                "surface would have done and what replaces it here."
            ),
        },
        "chosen": "record_the_substitute",
        "rejected_because": (
            "Silently omitting them leaves a reviewer reading a specification with ten product "
            "surfaces and nine implemented, with no way to tell which gap was deliberate. "
            "Reimplementing the portal would be a second surface that configures the same "
            "objects and can drift from the API. Recording the substitute keeps one "
            "configuration path and names the other one as somebody else's, which is the "
            "truth about both hosted surfaces."
        ),
        "cost_of_the_choice": (
            "The substitute is a set of API calls and a page rather than a hosted wizard, so "
            "it is less convenient than the surface it stands in for. The vocabulary says so "
            "rather than describing the page as an Admin Portal."
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
