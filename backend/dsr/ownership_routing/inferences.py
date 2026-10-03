"""Every judgement call in WF-053, in one inspectable place.

The research for WF-053 is precise about three things - the link type, the
required ``guestEmail``, and the forbidden nodes - and silent about most of what a
build of it has to decide. Those silences are product behaviour, not comments, so
they are named here and served at ``GET /api/wf-053/inferences`` for a reviewer to
disagree with by *name* rather than by hunting through a diff.

The sourced half is in :mod:`dsr.ownership_routing.vocabulary` and
:mod:`dsr.ownership_routing.rules`, and :func:`describe` returns both together -
the point of the endpoint is showing the reader where the line falls, which means
showing what is on each side of it.
"""

from __future__ import annotations

from typing import Any

from dsr.ownership_routing import rules as rules_module
from dsr.ownership_routing.vocabulary import (
    CATCH_ALL,
    CRM_OBJECT_TYPES,
    DISCOVERY_OPERATION,
    FORBIDDEN_ON_OWNERSHIP_PATH,
    FORBIDDEN_QUOTE,
    RESOLUTION_ORDER_RATIONALE,
    published_vocabulary,
)

#: The three sentences this build rests on, quoted so the reader does not have to
#: open the research document to check what was sourced and what was chosen.
OWNERSHIP_QUOTE = (
    "**Ownership** - routes to the owner of the guest's CRM record (lead, contact, or account "
    "owner), resolved at booking time."
)
GUEST_EMAIL_QUOTE = (
    "For **Ownership** links, also pass `guestEmail` in the init call - it is required so Chili "
    "Piper can resolve the owner from your CRM."
)

INFERENCES: tuple[dict[str, Any], ...] = (
    {
        "id": "resolution-order",
        "topic": "the order lead, contact and account are tried in",
        "basis": (
            "The evidence sentence is 'lead, contact, or account owner' - alphabetical, so it fixes "
            "the set of objects and not the order. Nothing in the research states a precedence."
        ),
        "value": {"order": list(CRM_OBJECT_TYPES), "configurable_per_link": True},
        "why": (
            "Lead first because it is the only one of the three that is about a person who has not "
            "been through conversion, so its owner is the rep who was working the record. Contact "
            "before Account because a Contact is a person with an owner while an Account owner is "
            "the account team - a coarser answer to the same question. The order is exposed as "
            "`resolution_order` on the link, because a deployment whose CRM keeps owners in a "
            "different order should not have to patch this package to get there."
        ),
        "change_it": "CRM_OBJECT_TYPES in vocabulary.py, or resolution_order on a link.",
        "blast_radius": "Every ownership resolution, and which rep a prospect is routed to.",
    },
    {
        "id": "guest-email-is-the-only-lookup-key",
        "topic": "whether an Ownership link accepts a request with no guest email",
        "basis": (
            "GuestEmailQuote: 'it is required so Chili Piper can resolve the owner from your CRM'. "
            "The research says required and does not describe a fallback."
        ),
        "value": {
            "missing_guest_email": "refused, 400",
            "record_id_substitute": "accepted, because the researched data flow reads 'guest email "
            "(or CRM record id)'",
            "blank_string": "treated as missing, not as an address that matches nothing",
        },
        "why": (
            "The research says required, so this build refuses rather than resolving to nobody. A "
            "record id is accepted as a substitute because the researched data flow itself offers "
            "the alternative, and a caller that already knows the record should not have to derive "
            "an address from it."
        ),
        "change_it": "require_guest_email in engine.py.",
        "blast_radius": "Every init call on an Ownership link.",
    },
    {
        "id": "interval-required-for-this-workflow",
        "topic": "whether an init call may omit the interval",
        "basis": (
            "The researched init payload includes 'interval': {...} and the flow says availability "
            "is read from the owner's calendar and 'the prospect books'. The research does not say "
            "what the init call answers with when the interval is absent."
        ),
        "value": {
            "missing_interval": "refused, 400",
            "empty_start_times": "accepted - a real window with nothing free in it is an answer",
            "alternative_reading": "the other researched workflow returns a booking URL when no "
            "interval is passed, which is what a redirect-style caller wants",
        },
        "why": (
            "Two researched workflows share the same init endpoint and differ on exactly this: one "
            "returns startTimes plus a routingId, the other returns a booking URL. This workflow "
            "is the first, because its whole subject is availability from the resolved owner's "
            "calendar - a session with no slots has nothing to route. A caller wanting a URL is "
            "calling the other workflow, and saying so in the error message beats answering with "
            "an empty slot list."
        ),
        "change_it": "require_interval in engine.py, and INIT_REQUIRED_FIELDS in vocabulary.py.",
        "blast_radius": "Every init call; a UI that wants the URL flow cannot use this router.",
    },
    {
        "id": "account-matches-by-domain-when-it-has-no-email",
        "topic": "how an Account record is matched to a guest who gave an email",
        "basis": (
            "The research says the owner is the 'lead, contact, or account owner' but names no "
            "field on the Account that carries the prospect's address."
        ),
        "value": {
            "account_match": "email domain against the record's domain / website",
            "lead_and_contact_match": "normalised email equality",
            "no_domain_on_record": "the account is simply not a candidate",
        },
        "why": (
            "An Account has a website, not a person's mailbox, so domain equality is the only "
            "thing it can be compared on. A record carrying neither an address nor a domain is not "
            "matched by guessing - a wrong match here sends a prospect to a rep they never spoke "
            "to."
        ),
        "change_it": "find_by_guest in crm.py.",
        "blast_radius": "Which Account, if any, a guest resolves to.",
    },
    {
        "id": "l2a-off-when-the-workspace-declares-nothing",
        "topic": "what Lead-to-Account matching means when the workspace has not set it",
        "basis": (
            "The research says 'For Lead-to-Account (L2A) Matching in **Salesforce**, we will use "
            "the existing settings defined in your workspace' - it uses the workspace's setting and "
            "does not say what happens when there is none."
        ),
        "value": {"default": False, "declared_true": "a lead is followed to its account id"},
        "why": (
            "Absent is not the same as yes. Walking from every Lead to an Account on a shared email "
            "domain is a bigger change of behaviour than not walking, so the conservative reading "
            "is off, and a workspace that wants it declares it."
        ),
        "change_it": "lead_to_account_matching in crm.py.",
        "blast_radius": "Only workspaces with no setting row, and only for a lead whose own owner is empty.",
    },
    {
        "id": "slots-grid-on-the-meeting-duration",
        "topic": "what times the owner's calendar is offered",
        "basis": (
            "The research names the source - 'Google/Outlook calendar of the resolved owner' - and "
            "publishes no slot algorithm, rounding rule, or buffer."
        ),
        "value": {
            "grid": "interval.duration_minutes",
            "alignment": "measured from the epoch, so two callers get the same lattice",
            "buffer_minutes": "0 by default, per owner, widened onto both sides of a busy block",
            "max_slots": 60,
            "default_horizon": "min_days 60, or the interval's max_days",
            "working_hours": "09:00-17:00 on Monday to Friday unless the owner says otherwise",
        },
        "why": (
            "The grid is the duration rather than a fixed quarter hour because two slots that "
            "overlap each other is a bug a rep hits immediately, and 45-minute meetings on a "
            "30-minute grid produce them by construction. The defaults are named constants served at "
            "/vocabulary, so a deployment changes behaviour by declaring it rather than by patching "
            "the package."
        ),
        "change_it": "available_slots in calendars.py and the DEFAULT_* constants beside it.",
        "blast_radius": "Every slot list this workflow shows.",
    },
    {
        "id": "a-route-is-consumed-by-one-booking",
        "topic": "what a second schedule-simple call on the same routingId does",
        "basis": (
            "The researched payload is a routingId 'for the second call' carrying a specific "
            "startTime. The research says nothing about calling it twice."
        ),
        "value": {
            "second_call_same_session": "refused, 409 route_consumed",
            "unknown_start_time": "refused, 409 slot_not_offered",
            "different_guest_email": "refused, 409 guest_mismatch",
        },
        "why": (
            "Two bookings on one session would put two prospects in one slot on one owner's "
            "calendar, and the owner's calendar is the thing the whole workflow is built to keep "
            "honest. Re-answering would also make the slot list a lie: it listed a time that is now "
            "taken."
        ),
        "change_it": "book in engine.py.",
        "blast_radius": "Every booking, and any client retrying a schedule call.",
    },
    {
        "id": "update-ownership-is-refused-not-warned",
        "topic": "what happens when an Ownership path carries an Update Ownership or Assign To node",
        "basis": FORBIDDEN_QUOTE,
        "value": {
            "on_save": "refused, 400 forbidden_node_on_ownership_path",
            "on_preview": "reported as advice, so the page can show which node is the problem",
            "assign_to_family": "assign_to, assign_to_team, assign_to_distribution",
        },
        "why": (
            "The research says 'could prevent your Router from being published'. A router that will "
            "not publish fails at ship time, not at save time, and by then nobody remembers which "
            "node was added. Refusing where the node is added turns a future deploy failure into an "
            "immediate, named one. The advisory form exists so the page can still show the problem "
            "before the save."
        ),
        "change_it": "check_nodes in rules.py, and the FORBIDDEN_ON_OWNERSHIP_PATH tuple.",
        "blast_radius": "Any ownership-path configuration carrying those nodes.",
    },
    {
        "id": "a-chain-without-a-catch-all-is-undeclirable",
        "topic": "what a routing chain with no terminal catch-all does",
        "basis": "Admin adds `Routing Rule` / `Catch All` nodes.",
        "value": {
            "missing_catch_all": "refused on save, 400 rules_no_catch_all",
            "catch_all_without_owner": "refused on save, same error",
            "catch_all_not_last": "refused on save - a catch-all in the middle makes later rules dead",
            "unroutable_at_runtime": "unreachable for a chain that saved",
        },
        "why": (
            "The catch-all is the difference between a prospect who books with the wrong rep and a "
            "prospect who books with nobody. Refusing the declaration means the broken configuration "
            "never exists, rather than existing and being discovered by a prospect."
        ),
        "change_it": "require_catch_all in rules.py.",
        "blast_radius": "Every routing decision on every chain.",
    },
    {
        "id": "pre-resolved-owner-skips-the-crm-lookup",
        "topic": "whether a caller may name the owner itself",
        "basis": (
            "The extensibility note: routes 'may be pre-resolved from your own CRM (a lead-owner "
            "link resolved from your CRM) rather than letting Chili Piper do the lookup'."
        ),
        "value": {
            "resolution_source": "pre_resolved",
            "effect": "the CRM lookup is skipped and the supplied owner_id is used",
            "still_required": "the owner id must name a rep in this workspace, and that rep must "
            "have a connected calendar",
        },
        "why": (
            "Pre-resolving is only useful if the workspace's calendar is still the source of "
            "availability - that is the researched data flow's second arrow and nothing in the note "
            "disturbs it. So a pre-resolved id skips the *lookup*, not the calendar check: it "
            "cannot route to someone with no calendar, because then there is no slot to book."
        ),
        "change_it": "resolve in engine.py.",
        "blast_radius": "Only links declaring resolution_source: pre_resolved.",
    },
    {
        "id": "no-reassignment-on-an-ownership-path",
        "topic": "whether a booking can reassign the CRM record's owner to the rep who took the meeting",
        "basis": (
            "The researched data flow ends '(optionally) Update Ownership node reassigns the CRM "
            "record to the rep who got the meeting', and the automations section forbids that very "
            "node on an Ownership path."
        ),
        "value": {
            "reassignment_on_ownership_path": "not offered",
            "recorded_instead": "the routing decision names the resolved owner and the record it "
            "came from, so the history of who owned it is readable",
        },
        "why": (
            "The data flow describes the node as available and the automations section forbids it "
            "here, and the guardrail is the more specific statement about the more specific path. "
            "Reassigning on an Ownership path would also be self-defeating: the point of the link "
            "is that the prospect reaches the rep who already owns them, and writing the record "
            "back would overwrite the ownership the next resolution is supposed to read."
        ),
        "change_it": "check_nodes in rules.py, which already refuses the node on this path.",
        "blast_radius": "Any ownership-path configuration; the decision record is unaffected.",
    },
)


def by_id(inference_id: str) -> dict[str, Any] | None:
    """One inference, or None."""
    for entry in INFERENCES:
        if entry["id"] == inference_id:
            return entry
    return None


def describe() -> dict[str, Any]:
    """The whole registry, beside the half of the workflow that is sourced.

    A read with no side effect, so it needs no store.
    """
    return {
        "count": len(INFERENCES),
        "sourced_quotes": {"ownership": OWNERSHIP_QUOTE, "guest_email": GUEST_EMAIL_QUOTE},
        "sourced": {
            "link_types": published_vocabulary()["supported_link_types"],
            "crm_object_types": list(CRM_OBJECT_TYPES),
            "catch_all": CATCH_ALL,
            "forbidden_on_ownership_path": list(FORBIDDEN_ON_OWNERSHIP_PATH),
            "discovery_operation": DISCOVERY_OPERATION,
        },
        "inferences": [dict(entry) for entry in INFERENCES],
        "resolution_order_rationale": RESOLUTION_ORDER_RATIONALE,
        "rules": {
            "team_rule": rules_module.TEAM_RULE,
            "value_rule": rules_module.VALUE_RULE,
            "catch_all": rules_module.CATCH_ALL_RULE,
        },
    }
