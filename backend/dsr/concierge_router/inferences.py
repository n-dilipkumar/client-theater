"""The decisions the research left open, each with its reason and its rival.

The research states in four places that a field is not published: the expiry
state, the ``routingLink`` sample, the ``assignment.type`` enumeration, and the
``primaryGuestDataFields`` payload. This module holds the reading taken for each,
the sentence from the research that forced the question, the reasoning that
settled it, and the reading that was rejected and why.

They are here rather than inline in the code because a reviewer needs to see the
rejected option to trust the chosen one. Each is served at
``GET /api/wf-051/inferences`` so the page shows the same reasoning the domain
enforces, and so a later change to a reading is one edit in one place.
"""

from __future__ import annotations

from typing import Any

INFERENCES: tuple[dict[str, Any], ...] = (
    {
        "id": "time-elapsed-makes-a-booking-not-scheduled",
        "question": (
            "The Display Calendar node carries a Time Elapsed timer. The research quotes only "
            "the consequence: 'when it expires the meeting will be considered not scheduled, "
            "and you can notify your rep to follow up'. It does not say which state a "
            "not-scheduled booking lands in."
        ),
        "decision": (
            "A booking the timer expires becomes the terminal state 'not_scheduled'. The route "
            "session stays in the store."
        ),
        "because": (
            "The researched Not Scheduled path runs 'Assign To' to distribute the prospect and "
            "'Send Notification' by email or Slack. Both need the prospect and the seller the "
            "rule assigned. Deleting the session would destroy the only record saying who to "
            "chase. Terminal means the session cannot be booked afterwards, which is what 'the "
            "meeting will be considered not scheduled' says."
        ),
        "rejected": (
            "Deletion, which loses the prospect and the assigned seller. A live "
            "not-scheduled state, which would let a not-scheduled prospect be booked later and "
            "contradict the researched outcome."
        ),
        "researched": False,
    },
    {
        "id": "the-time-elapsed-timer-is-computed-on-read",
        "question": (
            "The research names the timer and its consequence but not the mechanism. It does not "
            "say whether the expiry is scheduled or computed."
        ),
        "decision": (
            "Nothing polls. The deadline is stored on the session and expiry is computed when a "
            "session is read. A read that finds an expired session performs the transition as an "
            "audited write."
        ),
        "because": (
            "A workflow plugin cannot own a background thread or a server timer without becoming "
            "infrastructure the host has to supervise. A lazy timer still guarantees the effect "
            "the research describes by the time anyone observes the session, and it never "
            "speculatively fires against a prospect who never arrived. Writing the transition "
            "rather than returning a computed flag keeps the state change in the audit trail, "
            "which is what lets a follow-up notification see it happened."
        ),
        "rejected": (
            "A background sweep thread, which would need lifecycle handling the feature host "
            "does not provide. Returning a computed state without writing it, which would leave "
            "the Not Scheduled path with nothing to read and keep the audit trail silent about "
            "a state change a caller can observe."
        ),
        "researched": False,
    },
    {
        "id": "routing-link-is-a-relative-path",
        "question": (
            "The route response sample carries a routingLink pointing at "
            "https://your-tenant.chilipiper.com/. The sample value is a tenant URL."
        ),
        "decision": (
            "The stored routingLink is the path component only. The tenant base is held once on "
            "the router as routing_link_base, and a response joins the two."
        ),
        "because": (
            "Storing the sample verbatim would put one tenant's host into another tenant's "
            "record, because the sample's host is a placeholder rather than a real value. "
            "Splitting it keeps the tenant choice where it belongs, on the router that owns it, "
            "and leaves the session holding only what identifies this route."
        ),
        "rejected": (
            "Storing the full sample URL, which carries a placeholder host. Deriving the host "
            "from a request, which would make the stored value depend on who called rather than "
            "on how the router is deployed."
        ),
        "researched": False,
    },
    {
        "id": "assignment-type-enumeration",
        "question": (
            "The route response sample carries assignment.type 'user'. The research names three "
            "assignment choices: Owner, Round-Robin and Individual user."
        ),
        "decision": (
            "type carries the vendor wire spelling. individual_user maps to 'user', which is the "
            "sampled value. owner maps to 'owner' and round_robin maps to 'round_robin'."
        ),
        "because": (
            "The sample is the only sourced spelling of the wire field, so the enumeration is "
            "anchored to it. Owner and Round-Robin name a policy rather than a user, which the "
            "researched extensibility note explains: 'Assignment Tables let one path serve every "
            "territory'. So those two carry a policy reference and the resolved user lands "
            "beside them once the engine has run the policy."
        ),
        "rejected": (
            "Using 'user' for all three, which would make the researched distinction between "
            "Owner, Round-Robin and Individual user unobservable in the response the research "
            "sampled."
        ),
        "researched": False,
    },
    {
        "id": "primary-guest-data-fields",
        "question": (
            "The research names the primaryGuestDataFields webhook payload and no field of it."
        ),
        "decision": (
            "Seven fields: firstName, lastName, email, company and phone from the mapped form "
            "fields, plus ownerId and meetingType from the routing result. email is the only "
            "required one."
        ),
        "because": (
            "The researched data flow reads 'webform POST -> Trigger (form-field -> Data Field "
            "mapping) -> routing rule evaluation against (a) Chili Piper Data Fields and (b) live "
            "CRM object values'. The form fields are what the mapping can carry, and the two "
            "routing-result fields are what the response already resolved. email is required "
            "because a CRM object is found by address, so without it the CRM half of the rule "
            "evaluation has nothing to match."
        ),
        "rejected": (
            "An open field list, which would let a guest payload differ between two routers. "
            "Marking every field required, which would refuse a webform that asks only for an "
            "email address, which the research's own flow allows."
        ),
        "researched": False,
    },
    {
        "id": "lead-case-opportunity",
        "question": (
            "The researched data flow names five live CRM objects: Lead, Contact, Account, "
            "Opportunity and Case. The CRM Ownership rule kind names only three."
        ),
        "decision": (
            "A CRM Ownership rule may check the owner of lead, contact or account only. "
            "Opportunity and Case are reachable as Without Ownership field values instead."
        ),
        "because": (
            "A rule kind that says 'check Lead/Contact/Account owner' defines the object set a "
            "rule may check the owner of. Opportunity and Case are named by the data flow as "
            "live object values, which is the Without Ownership half: 'CRM values or Data Field "
            "values'. Refusing them as ownership objects with a message naming that route keeps "
            "both objects usable without inventing an owner semantic the research does not "
            "state."
        ),
        "rejected": (
            "Widening CRM Ownership to all five, which would invent an owner semantic for "
            "Opportunity and Case that the rule kind does not name. Refusing them outright, "
            "which would drop two objects the researched data flow names."
        ),
        "researched": False,
    },
    {
        "id": "post-booking-nodes-are-declared-not-executed",
        "question": (
            "The research says the router's default post-booking nodes fire: Create Event, Update "
            "Field, Update Ownership. It publishes no payload for any of them."
        ),
        "decision": (
            "A router declares which post-booking nodes it carries, and the booking records "
            "which fired. The nodes are not executed."
        ),
        "because": (
            "Update Field and Update Ownership write to CRM objects, which WF-042 owns and which "
            "this feature reads but does not write. Executing them here would give two features "
            "write ownership of one record set. Create Event has no destination in this product: "
            "the researched calendar is Google or Outlook, which this build does not connect to. "
            "Recording the declaration keeps the routing decision visible without claiming an "
            "effect that did not happen."
        ),
        "rejected": (
            "Executing a CRM writeback here, which would duplicate WF-042's ownership of those "
            "records and make two features responsible for one audit trail."
        ),
        "researched": False,
    },
    {
        "id": "notification-is-recorded-not-sent",
        "question": (
            "The researched Not Scheduled path runs Send Notification by email or Slack. The "
            "research names the channels and no delivery contract."
        ),
        "decision": (
            "The notification is recorded on the booking with its channel, recipient and moment. "
            "It is not sent."
        ),
        "because": (
            "This product has no outbound mail or Slack transport, and the research publishes no "
            "endpoint, credential or delivery guarantee for one. A record saying a notification "
            "was raised is something an operator can act on and a test can assert. A record "
            "claiming to have sent mail would be an unbacked claim about the outside world."
        ),
        "rejected": (
            "Integrating an outbound transport, which is platform work and needs a credential "
            "and a delivery contract the research does not supply."
        ),
        "researched": False,
    },
)


def published() -> list[dict[str, Any]]:
    """Every inference, as data, for the page that shows the reasoning."""
    return [dict(entry) for entry in INFERENCES]


def by_id(inference_id: str) -> dict[str, Any]:
    """One inference by id, or an empty dict when the id names none."""
    for entry in INFERENCES:
        if entry["id"] == inference_id:
            return dict(entry)
    return {}
