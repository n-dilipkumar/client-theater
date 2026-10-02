"""The researched contract for WF-065, as data.

The research for this workflow is specific about the *nodes* and about the two
selection rules that decide which existing record a new Event hangs off, and
silent about everything else. It names eight router nodes, two create branches,
two update branches, four related objects, three ownership assignments, one
campaign status and one ordering constraint. It says nothing about what a flow
row is called in this product, which of two owners wins a tie, or what a rep sees
when the Event write fails.

Everything in this module is therefore one of two things, and the difference is
kept visible:

* **sourced** - a ``[sourced]``-marked constant, quoted from
  ``docs/research/digital-sales-room-workflows/wf/WF-065.md``, and traceable to
  the primary documentation the research cites;
* **designed** - this build's own vocabulary, always named in
  :mod:`dsr.booking_crm.inferences` with the reasoning.

The reason for the split is not tidiness. The sourced strings here are what a
rep actually types into a router, and a reader has to be able to tell
"Chili Piper documents this node name" from "this build decided this label".
:func:`describe` serves both groups separately so a reviewer can disagree with
one without arguing with the other.
"""

from __future__ import annotations

from typing import Any

# --------------------------------------------------------------------------- #
# Vendors
# --------------------------------------------------------------------------- #

#: [sourced] The two CRM dialects the research names, one sentence at a time:
#: "``Create Event`` (Salesforce) or ``Create Engagement`` (HubSpot)", and "the
#: Salesforce Package (Concierge / Handoff for Salesforce Package)".
SALESFORCE = "salesforce"
HUBSPOT = "hubspot"

VENDORS: tuple[str, ...] = (SALESFORCE, HUBSPOT)

#: [sourced] The vendor each node name belongs to. The research writes the
#: Salesforce and HubSpot names side by side and never mixes them:
#:
#:   "``Create Event`` (Salesforce) or ``Create Engagement`` (HubSpot)"
#:   "``Update Field``/``Update Property``"
#:   "``Create or Update Record`` (Salesforce) and ``Create or Update Contact``
#:    (HubSpot)"
#:
#: so a Salesforce flow declaring ``create_engagement`` is a mistake this build
#: refuses rather than silently renames.
VENDOR_NODES: dict[str, tuple[str, ...]] = {
    SALESFORCE: (
        "create_or_update_record",
        "create_event",
        "related_object",
        "update_field",
        "add_to_campaign",
        "update_ownership",
    ),
    HUBSPOT: (
        "create_or_update_contact",
        "create_engagement",
        "related_object",
        "update_property",
        "add_to_campaign",
        "update_ownership",
    ),
}

#: [sourced] The one node name the research uses for both vendors without
#: qualifying it. "``Add to Campaign``", "``Update Ownership``".
SHARED_NODES: tuple[str, ...] = ("add_to_campaign", "update_ownership")

#: [sourced] The related objects each vendor's Event node offers, quoted
#: verbatim: "Account, Case, Opportunity, Campaign / Deal, Ticket".
SALESFORCE_RELATED: tuple[str, ...] = ("Account", "Case", "Opportunity", "Campaign")
HUBSPOT_RELATED: tuple[str, ...] = ("Deal", "Ticket")

# --------------------------------------------------------------------------- #
# Router paths
# --------------------------------------------------------------------------- #

#: [sourced] "On a scheduled / not-scheduled / disqualified path in the router".
#: Three paths, named as the research names them, and every one of them fires the
#: write: "writes fire on the scheduled, not-scheduled and disqualified paths
#: automatically".
PATH_SCHEDULED = "scheduled"
PATH_NOT_SCHEDULED = "not_scheduled"
PATH_DISQUALIFIED = "disqualified"

PATHS: tuple[str, ...] = (PATH_SCHEDULED, PATH_NOT_SCHEDULED, PATH_DISQUALIFIED)

# --------------------------------------------------------------------------- #
# The ordering rule
# --------------------------------------------------------------------------- #

#: [sourced] "Note this node must precede the **Create Event**, **Update Field**,
#: **Add to Campaign**, and **Update Ownership** nodes".
#:
#: This is the single most load-bearing sentence in the research: every other
#: node in this workflow needs the id of the record the create node produced, so
#: a flow that declares them in the other order cannot be executed. The refusal
#: quotes it verbatim rather than paraphrasing it.
ORDERING_QUOTE = (
    "Note this node must precede the Create Event, Update Field, Add to Campaign, "
    "and Update Ownership nodes"
)

#: [sourced] The node the sentence calls "this node": the create-or-update
#: record node. Salesforce names it "Create or Update Record", HubSpot names it
#: "Create or Update Contact"; the constraint is on the *role*, not the label, so
#: both names are accepted as the anchor.
ANCHOR_NODES: frozenset[str] = frozenset({"create_or_update_record", "create_or_update_contact"})

#: [sourced] The nodes the sentence names as needing to come after it, plus the
#: two HubSpot spellings of the same two Salesforce nodes.
DEPENDENT_NODES: frozenset[str] = frozenset(
    {
        "create_event",
        "create_engagement",
        "related_object",
        "update_field",
        "update_property",
        "add_to_campaign",
        "update_ownership",
    }
)

#: The nodes this build knows, in the order the research's user flow adds them:
#: the anchor, then the Event/Engagement node, then the optional Related Object,
#: then the three optional downstream ones.
ALL_NODES: tuple[str, ...] = (
    "create_or_update_record",
    "create_or_update_contact",
    "create_event",
    "create_engagement",
    "related_object",
    "update_field",
    "update_property",
    "add_to_campaign",
    "update_ownership",
)

NODE_PURPOSE: dict[str, str] = {
    "create_or_update_record": (
        "[sourced] Create or Update Record (Salesforce). Matches a Lead or Contact "
        "by email, and updates and/or creates it. Must precede every other node."
    ),
    "create_or_update_contact": (
        "[sourced] Create or Update Contact (HubSpot). The HubSpot spelling of the "
        "same anchor node, and the same rule."
    ),
    "create_event": (
        "[sourced] Create Event (Salesforce). 'All created Events will be related to "
        "the Contact or Lead by default.'"
    ),
    "create_engagement": (
        "[sourced] Create Engagement (HubSpot). The HubSpot spelling of Create Event."
    ),
    "related_object": (
        "[sourced] Related Object (Account, Case, Opportunity, Campaign / Deal, "
        "Ticket). 'If we have found a contact, you can additionally relate the Event "
        "to an Account, Case, Opportunity, or Campaign.'"
    ),
    "update_field": (
        "[sourced] Update Field (Salesforce). 'selected fields updated (e.g. "
        'Contact.Status = "Sales Qualified")\'.'
    ),
    "update_property": (
        "[sourced] Update Property (HubSpot). The HubSpot spelling of Update Field."
    ),
    "add_to_campaign": (
        "[sourced] Add to Campaign. 'CampaignMember created/updated with status Booked'."
    ),
    "update_ownership": ("[sourced] Update Ownership. 'record Owner reassigned to the assignee.'"),
}

# --------------------------------------------------------------------------- #
# The create node's two branches
# --------------------------------------------------------------------------- #

#: [sourced] The update branch the research names first: "Update matched Contact
#: or Lead". Both record types are updated when either matched.
UPDATE_MATCHED = "matched_contact_or_lead"

#: [sourced] The update branch that restricts the same thing to one record type:
#: "Only update matched Lead". A Contact match is not updated.
UPDATE_MATCHED_LEAD = "matched_lead_only"

#: [sourced] The update branches, in the order the research lists them.
UPDATE_BRANCHES: tuple[str, ...] = (UPDATE_MATCHED, UPDATE_MATCHED_LEAD)

#: [sourced] The create branch that picks the record type: "Create Contact or
#: Lead". The record type comes from the node's ``record_type`` setting, so the
#: branch is "create one of these" rather than a single fixed type.
CREATE_CONTACT_OR_LEAD = "contact_or_lead"

#: [sourced] "Create Lead" - a Lead, never a Contact, whatever matched.
CREATE_LEAD = "lead"

#: [sourced] "Always create Lead" - a Lead is created *even when* something
#: matched. The word "Always" is the whole difference from ``CREATE_LEAD``, and it
#: is the branch most implementations collapse into the other two.
CREATE_ALWAYS_LEAD = "always_lead"

CREATE_BRANCHES: tuple[str, ...] = (
    CREATE_CONTACT_OR_LEAD,
    CREATE_LEAD,
    CREATE_ALWAYS_LEAD,
)

#: The fourth state the create node accepts, which is *not* one of the researched
#: labels: never create. The research says the admin "chooses whether to **create**"
#: - "whether" is a yes/no, so declining to create is a choice the sentence already
#: allows, and it is the choice that makes the no-record catch-all reachable rather
#: than hypothetical. Deliberately *not* added to :data:`CREATE_BRANCHES`, which
#: stays the three labels the research prints.
CREATE_NONE = "none"

CREATE_STATES: tuple[str, ...] = (*CREATE_BRANCHES, CREATE_NONE)

#: The researched label for each create branch, as the research prints it. Used in
#: refusal messages, so the admin is told which of *their* three options
#: contradicts what they wrote rather than which of this build's constants does.
CREATE_BRANCH_LABEL: dict[str, str] = {
    CREATE_CONTACT_OR_LEAD: "Create Contact or Lead",
    CREATE_LEAD: "Create Lead",
    CREATE_ALWAYS_LEAD: "Always create Lead",
}

#: What each branch means, served at ``/vocabulary``. The labels are sourced; the
#: three-way reading of "Create Contact or Lead" is this build's, and it is the
#: interpretation the whole package runs on - see the ``create-branches`` entry in
#: :mod:`dsr.booking_crm.inferences`.
CREATE_MEANING: dict[str, str] = {
    CREATE_CONTACT_OR_LEAD: (
        "[sourced] 'Create Contact or Lead'. A branch rather than a type: the "
        "node's record_type says which of the two it makes, so 'Contact or Lead' "
        "is one setting with two values instead of two settings."
    ),
    CREATE_LEAD: (
        "[sourced] 'Create Lead'. A Lead, and only a Lead, and only when nothing matched."
    ),
    CREATE_ALWAYS_LEAD: (
        "[sourced] 'Always create Lead'. A Lead is created *even when* something "
        "matched. The word 'Always' is the whole difference from 'Create Lead'."
    ),
}

#: What each update branch means, served at ``/vocabulary``.
UPDATE_MEANING: dict[str, str] = {
    UPDATE_MATCHED: (
        "[sourced] 'Update matched Contact or Lead'. Both record types are "
        "updated when either matched."
    ),
    UPDATE_MATCHED_LEAD: (
        "[sourced] 'Only update matched Lead'. A Contact match is not updated, "
        "which is the whole difference from the branch above."
    ),
}

#: [sourced] The record types a create branch can produce.
RECORD_CONTACT = "contact"
RECORD_LEAD = "lead"
RECORD_TYPES: tuple[str, ...] = (RECORD_CONTACT, RECORD_LEAD)

#: [sourced] "matched by email". The only match key the research names, so a flow
#: that asks to match on anything else is refused rather than guessed at.
MATCH_KEY_EMAIL = "email"
MATCH_KEYS: tuple[str, ...] = (MATCH_KEY_EMAIL,)

# --------------------------------------------------------------------------- #
# Related objects
# --------------------------------------------------------------------------- #

#: [sourced] "All created Events will be related to the Contact or Lead **by
#: default**. If we have found a contact, you can additionally relate the Event to
#: an **Account**, **Case**, **Opportunity**, or **Campaign**."
#:
#: The first sentence is the default relation, which always happens. The second
#: is the optional one, and it is gated on *a contact* - a matched Lead is not
#: enough. This is the constraint most easily lost, because the sentence reads
#: naturally as though the second relation is simply optional.
RELATED_DEFAULT_QUOTE = (
    "All created Events will be related to the Contact or Lead by default. If we "
    "have found a contact, you can additionally relate the Event to an Account, "
    "Case, Opportunity, or Campaign."
)

#: [sourced] "If we have found a contact" - the gate on every additional
#: relation. Quoted as its own constant because the refusal message uses it.
RELATED_REQUIRES_CONTACT_QUOTE = "If we have found a contact"

#: [sourced] "💡 For **Cases**, we will relate with the most recently created Open
#: one, and for **Opportunities**, we will relate with the one that has the
#: nearest Close Date".
RELATED_SELECTION_QUOTE = (
    "For Cases, we will relate with the most recently created Open one, and for "
    "Opportunities, we will relate with the one that has the nearest Close Date"
)

#: [sourced] The two researched selection rules, by the record type they apply to.
#: The key is the Related Object name; the value is what "the one" means.
SELECTION_RULES: dict[str, str] = {
    "Account": "the_contact_account",
    "Case": "most_recent_open",
    "Opportunity": "nearest_close_date",
    "Campaign": "most_recent",
    "Deal": "nearest_close_date",
    "Ticket": "most_recent_open",
}

SELECTION_RULE_MEANING: dict[str, str] = {
    "the_contact_account": (
        "The Account the matched Contact belongs to. Not a choice among candidates: "
        "the Contact already names it."
    ),
    "most_recent_open": (
        "[sourced] 'For Cases, we will relate with the most recently created Open "
        "one' - so Closed cases are not candidates at all, and the newest Open one "
        "wins."
    ),
    "nearest_close_date": (
        "[sourced] 'for Opportunities, we will relate with the one that has the "
        "nearest Close Date' - nearest to the meeting being booked, measured as the "
        "smallest absolute gap in days."
    ),
    "most_recent": (
        "The most recently created one. The research names the newest rule for "
        "Cases and the nearest rule for Opportunities and names nothing for "
        "Campaign; this build uses the newest rule because a Campaign has no date "
        "to be near."
    ),
}

#: The status the Researched **Add to Campaign** node writes, quoted:
#: "CampaignMember created/updated with status ``Booked``".
CAMPAIGN_MEMBER_STATUS = "Booked"

# --------------------------------------------------------------------------- #
# Ownership
# --------------------------------------------------------------------------- #

#: [sourced] "record Owner reassigned to the assignee", and the automation line
#: "ownership can be transferred to whoever took the meeting". The assignee is
#: therefore the default and the researched default.
OWNER_ASSIGNEE = "assignee"

#: [sourced] The three identities the research says an ownership node can
#: assign to. "assignee" is the assignee; "host" is the meeting's host; "booker"
#: is whoever booked the meeting. All three are named in the research, in the
#: Activity Assigned to enumeration.
OWNER_HOST = "host"
OWNER_BOOKER = "booker"
OWNER_IDENTITIES: tuple[str, ...] = (OWNER_ASSIGNEE, OWNER_HOST, OWNER_BOOKER)

# --------------------------------------------------------------------------- #
# The Delete Event behaviour
# --------------------------------------------------------------------------- #

#: [sourced] The research names the setting once, in the user flow's step 4, and
#: never says what it does: "Optionally configures ``Create child Event`` per
#: additional guest, and the ``Delete Event`` behaviour."
#:
#: Both halves of that sentence are configurations *of the Create Event node*, so
#: that is where both live. What the trigger is, the research does not say. The
#: two readings this build can reach from its own evidence are named here rather
#: than guessed at in a function body:
#:
#: * ``on_failure`` - the Event this run created is deleted when a *later* node
#:   in the same run fails. Reachable from this workflow's own data, because a
#:   partial run is the thing Events History exists to show.
#: * ``on_retry`` - when an admin retries a failed Event from Events History -
#:   "[sourced] Admin later retries any failed CRM Event" - any Event the failed
#:   attempt left behind is deleted first, so a retry cannot double-book.
#:
#: ``never`` is the default, because an unstated default should be the one that
#: does nothing.
DELETE_EVENT_NEVER = "never"
DELETE_EVENT_ON_FAILURE = "on_failure"
DELETE_EVENT_ON_RETRY = "on_retry"
DELETE_EVENT_MODES: tuple[str, ...] = (
    DELETE_EVENT_NEVER,
    DELETE_EVENT_ON_FAILURE,
    DELETE_EVENT_ON_RETRY,
)

#: What each Delete Event mode does, served at ``/vocabulary``.
DELETE_EVENT_MEANING: dict[str, str] = {
    DELETE_EVENT_NEVER: "Nothing is deleted. The default, because the research names the setting and not its trigger.",
    DELETE_EVENT_ON_FAILURE: (
        "The Events this run created are deleted when a later node in the same "
        "run fails, so a failed write leaves no half-written meeting behind."
    ),
    DELETE_EVENT_ON_RETRY: (
        "Any Event a previous failed attempt left behind is deleted before the "
        "retry creates its own, so a retry cannot double-book the meeting."
    ),
}

#: [sourced] ``Create child Event`` "per additional guest" - so the count of
#: children is the count of additional guests, and a booking with none has none.
CHILD_EVENTS_NOTE = "[sourced] Create child Event, per additional guest."

# --------------------------------------------------------------------------- #
# Owner fallback
# --------------------------------------------------------------------------- #

#: [sourced] Cal's owner fallback strategies, quoted: ``crmRecordOwnerFallbackMode``
#: is ``relationship`` or ``attributeRules``.
OWNER_FALLBACK_RELATIONSHIP = "relationship"
OWNER_FALLBACK_ATTRIBUTE_RULES = "attributeRules"
OWNER_FALLBACK_MODES: tuple[str, ...] = (
    OWNER_FALLBACK_RELATIONSHIP,
    OWNER_FALLBACK_ATTRIBUTE_RULES,
)

# --------------------------------------------------------------------------- #
# Activity Assigned to
# --------------------------------------------------------------------------- #

#: [sourced] "HubSpot ``Activity Assigned to`` (Host / Booker / Assignee -
#: 'This changes who hosts the Engagement inside the activity tab and can be useful
#: for reporting purposes')".
ACTIVITY_ASSIGNED_TO_HOST = "host"
ACTIVITY_ASSIGNED_TO_BOOKER = "booker"
ACTIVITY_ASSIGNED_TO_ASSIGNEE = "assignee"
ACTIVITY_ASSIGNED_TO: tuple[str, ...] = (
    ACTIVITY_ASSIGNED_TO_HOST,
    ACTIVITY_ASSIGNED_TO_BOOKER,
    ACTIVITY_ASSIGNED_TO_ASSIGNEE,
)

#: [sourced] The whole quoted sentence about Activity Assigned to.
ACTIVITY_ASSIGNED_TO_QUOTE = (
    "It passes the email of the booker or the assignee to the 'Activity Assigned to' "
    "field inside the engagement created in Hubspot for the booked meeting. This "
    "changes who hosts the Engagement inside the activity tab and can be useful for "
    "reporting purposes."
)

# --------------------------------------------------------------------------- #
# Sync Meeting Type to the CRM
# --------------------------------------------------------------------------- #

#: [sourced] "**Sync Meeting Type to the CRM** … your Admins can define other
#: behaviors to be taken when a meeting is booked via Personal Scheduling Links,
#: such as creating Events, which Object will be associated with an Event, and
#: many more."
#:
#: And the scoping sentence that makes it a *toggle* rather than a field: "your
#: links will follow this pre-defined behavior, as these settings are applied to
#: all users in your org". It is a per-meeting-type switch, and it applies
#: org-wide - which is exactly why a run cannot carry its own override.
SYNC_TOGGLE_QUOTE = (
    "your Admins can define other behaviors to be taken when a meeting is booked "
    "via Personal Scheduling Links, such as creating Events, which Object will be "
    "associated with an Event, and many more"
)

#: [sourced] The org-wide scoping sentence, quoted on its own because it is the
#: reason a per-run override is refused rather than merely ignored.
SYNC_TOGGLE_ORG_WIDE_QUOTE = (
    "your links will follow this pre-defined behavior, as these settings are "
    "applied to all users in your org"
)

# --------------------------------------------------------------------------- #
# Events History
# --------------------------------------------------------------------------- #

#: [sourced] "If the Event is successfully created, we will show when it happened.
#: If the Event failed to be created, we will also show when it happened,
#: alongside the detailed error."
HISTORY_SHOWS_WHEN_QUOTE = (
    "If the Event is successfully created, we will show when it happened. If the "
    "Event failed to be created, we will also show when it happened, alongside the "
    "detailed error."
)

#: [sourced] "Admin later retries any failed CRM Event from Meetings Activity →
#: Events History."
HISTORY_RETRY_QUOTE = (
    "Admin later retries any failed CRM Event from Meetings Activity → Events History."
)

#: [sourced] Cal's error surface, quoted: "``GET /v2/event-types/{id}/crm-sync-
#: errors`` ('List CRM sync errors for an event type')". The event type is what
#: scopes the list, which is why the history carries one.
CAL_SYNC_ERRORS_PATH = "/v2/event-types/{event_type_id}/crm-sync-errors"
CAL_SYNC_ERRORS_DESCRIPTION = "List CRM sync errors for an event type"

#: [sourced] "Admin later retries any failed CRM Event" - so a retry is offered
#: on a *failure*, and the research does not describe a retry on a success. This
#: build follows that and says so rather than making retry unconditional.
RETRY_OFFERED_ON = "failed"

# --------------------------------------------------------------------------- #
# The sourced gaps
# --------------------------------------------------------------------------- #

#: The research's own gaps list, verbatim in the two entries that bear on this
#: build. Restated here so a reader of ``/vocabulary`` sees what is *not*
#: evidenced without opening the research document.
SOURCED_GAPS: tuple[dict[str, str], ...] = (
    {
        "id": "hubspot-meetings-api",
        "gap": (
            "knowledge.hubspot.com/meetings-tool 404s and the developer reference "
            "redirected to a legacy crm/activities/meetings page."
        ),
        "consequence": (
            "HubSpot appears only through Chili Piper's own node names. This build "
            "does not claim a HubSpot Meetings API endpoint shape, and the "
            "engagement it writes is the one the node is documented to create."
        ),
    },
    {
        "id": "salesforce-native-meeting-scheduling",
        "gap": (
            "No Salesforce product documentation page for meeting booking/routing in "
            "a sales room was found."
        ),
        "consequence": (
            "The CRM writeback half is built from Chili Piper's and Cal.com's "
            "documented Salesforce nodes/integrations, which is what the research "
            "says stands in for it."
        ),
    },
)

#: Every sentence the rest of this package measures itself against, served at
#: ``/vocabulary`` so a deployment reads the rules from the build rather than from
#: a comment.
SOURCED_QUOTES: tuple[dict[str, str], ...] = (
    {"id": "ordering", "quote": ORDERING_QUOTE},
    {"id": "related_default", "quote": RELATED_DEFAULT_QUOTE},
    {"id": "related_requires_contact", "quote": RELATED_REQUIRES_CONTACT_QUOTE},
    {"id": "related_selection", "quote": RELATED_SELECTION_QUOTE},
    {"id": "activity_assigned_to", "quote": ACTIVITY_ASSIGNED_TO_QUOTE},
    {"id": "sync_toggle", "quote": SYNC_TOGGLE_QUOTE},
    {"id": "sync_toggle_org_wide", "quote": SYNC_TOGGLE_ORG_WIDE_QUOTE},
    {"id": "history_shows_when", "quote": HISTORY_SHOWS_WHEN_QUOTE},
    {"id": "history_retry", "quote": HISTORY_RETRY_QUOTE},
    {"id": "cal_sync_errors", "quote": CAL_SYNC_ERRORS_DESCRIPTION},
)


def describe() -> dict[str, Any]:
    """The whole researched contract, served at ``/api/wf-065/vocabulary``."""
    return {
        "vendors": list(VENDORS),
        "vendor_nodes": {vendor: list(nodes) for vendor, nodes in VENDOR_NODES.items()},
        "paths": list(PATHS),
        "nodes": list(ALL_NODES),
        "node_purpose": dict(NODE_PURPOSE),
        "anchor_nodes": sorted(ANCHOR_NODES),
        "dependent_nodes": sorted(DEPENDENT_NODES),
        "ordering_quote": ORDERING_QUOTE,
        "update_branches": list(UPDATE_BRANCHES),
        "update_branch_meaning": dict(UPDATE_MEANING),
        "create_branches": list(CREATE_BRANCHES),
        "create_branch_meaning": dict(CREATE_MEANING),
        "record_types": list(RECORD_TYPES),
        "match_keys": list(MATCH_KEYS),
        "related_objects": {
            SALESFORCE: list(SALESFORCE_RELATED),
            HUBSPOT: list(HUBSPOT_RELATED),
        },
        "selection_rules": dict(SELECTION_RULES),
        "selection_rule_meaning": dict(SELECTION_RULE_MEANING),
        "campaign_member_status": CAMPAIGN_MEMBER_STATUS,
        "owner_identities": list(OWNER_IDENTITIES),
        "owner_fallback_modes": list(OWNER_FALLBACK_MODES),
        "delete_event_modes": list(DELETE_EVENT_MODES),
        "delete_event_meaning": dict(DELETE_EVENT_MEANING),
        "child_events_note": CHILD_EVENTS_NOTE,
        "activity_assigned_to": list(ACTIVITY_ASSIGNED_TO),
        "cal_sync_errors_path": CAL_SYNC_ERRORS_PATH,
        "retry_offered_on": RETRY_OFFERED_ON,
        "sourced_quotes": [dict(entry) for entry in SOURCED_QUOTES],
        "sourced_gaps": [dict(entry) for entry in SOURCED_GAPS],
    }


__all__ = [name for name in dir() if not name.startswith("_")]
