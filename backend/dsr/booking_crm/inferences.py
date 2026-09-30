"""Every inference this package makes, in one inspectable place.

The research for WF-065 is unusually specific about the *nodes* and about the two
selection rules, and silent about almost everything around them. It names eight
node kinds, six branch labels, four related objects, three ownership identities,
one campaign status, three router paths, one org-wide toggle and one ordering
constraint. It does not say what a flow row is called here, which record type
"Create Contact or Lead" produces, what "nearest Close Date" is nearest *to*, or
what the ``Delete Event`` behaviour fires on.

A judgement call left as a comment in a function body is one nobody re-reads, and
a wrong one becomes product behaviour without anyone noticing. Collected here
instead, each inference is:

* **named**, so it can be argued with by name;
* **traceable** - ``basis`` says what the research does and does not say;
* **bounded** - ``value`` is what this build chose, and ``change_it`` says how to
  change it without editing a function body;
* **visible** - :func:`describe` is served at ``/api/wf-065/inferences``, so a
  reviewer or a client reads the whole list instead of inferring it from a diff.

Nothing here is a migration, a typed column, or a new required field. It is a list
of ordinary JSON, exactly like everything else this package stores, and it is a
*record* of a judgement rather than a mechanism that enforces one.
"""

from __future__ import annotations

from typing import Any

from dsr.booking_crm.flow import (
    ANCHOR_NODE,
    EVENT_NODE,
    FIELD_NODE,
    MATCH_ORDER,
    RELATED_RECORD_TYPE,
    RELATED_TYPES,
    SINGLETON_NODES,
)
from dsr.booking_crm.local_crm import OPEN_STATUS
from dsr.booking_crm.vocabulary import (
    ACTIVITY_ASSIGNED_TO,
    CAMPAIGN_MEMBER_STATUS,
    CREATE_ALWAYS_LEAD,
    CREATE_BRANCHES,
    CREATE_CONTACT_OR_LEAD,
    CREATE_LEAD,
    DELETE_EVENT_ON_FAILURE,
    DELETE_EVENT_ON_RETRY,
    HUBSPOT,
    PATHS,
    RELATED_REQUIRES_CONTACT_QUOTE,
    SALESFORCE,
    SELECTION_RULES,
    UPDATE_BRANCHES,
    VENDORS,
)


INFERENCES: tuple[dict[str, Any], ...] = (
    {
        "id": "node-order-is-checked-not-sorted",
        "topic": "what happens to a flow that declares a downstream node first",
        "basis": (
            '[sourced] "Note this node must precede the **Create Event**, **Update '
            'Field**, **Add to Campaign**, and **Update Ownership** nodes". The '
            "research states the rule as a requirement on the admin's declaration."
        ),
        "value": {
            "when": "at declaration, on create and on patch",
            "refusal": "a 400 quoting the sentence, naming the node and both positions",
            "reordering": "never - the declared order is the order that will run",
            "also_refused": "a dependent node with no anchor anywhere in the list",
        },
        "why": (
            "The alternative is to accept the list and sort it, which is a plausible "
            "connector. It is rejected because the declared order is the thing an admin "
            "reads to understand their own router: a flow that has to be reordered to be "
            "valid is a flow whose stored declaration is a lie about what it does. The "
            "check is at declaration rather than at run time so a stored flow is always "
            "a runnable one."
        ),
        "change_it": "validate_nodes in dsr/booking_crm/flow.py.",
        "blast_radius": "Flow creation and patching. Nothing already valid changes.",
    },
    {
        "id": "related-object-is-gated-on-a-contact",
        "topic": "what the extra relation needs before it can happen",
        "basis": (
            f'[sourced] "All created Events will be related to the Contact or Lead by '
            f'default. If we have found a contact, you can additionally relate the Event '
            f'to an **Account**, **Case**, **Opportunity**, or **Campaign**." The gate is '
            f'"{RELATED_REQUIRES_CONTACT_QUOTE}" - a contact, not a record.'
        ),
        "value": {
            "requires": "Contact",
            "lead_match": "skipped with the sentence quoted, reason 'If we have found a contact'",
            "default_relation": "always applied, to whatever record the create node produced",
            "account": "resolves off a Lead that went through L2A, because L2A gives the Lead an account_id",
        },
        "why": (
            "The natural reading of that paragraph is that the second relation is merely "
            "optional, and that is the mistake this guards against: a Lead match is not a "
            "Contact match, and the research is explicit. Silently relating a Case to a "
            "Lead would put a support ticket on the wrong record, which is worse than not "
            "relating it. The default relation still happens, so the Event is never "
            "orphaned - it is related to the Lead, which is the researched default."
        ),
        "change_it": "BookingWriteback._run_related in dsr/booking_crm/engine.py.",
        "blast_radius": "The related_object node on a Lead match, and nothing else.",
    },
    {
        "id": "nearest-close-date-is-measured-against-the-meeting",
        "topic": "what 'the Opportunity with the nearest Close Date' is nearest to",
        "basis": (
            '[sourced] "for **Opportunities**, we will relate with the one that has the '
            'nearest Close Date". The research gives the rule and not the reference point.'
        ),
        "value": {
            "reference": "the booking's starts_at",
            "measure": "smallest absolute gap in days",
            "tie_break": "the earlier close date",
            "no_meeting_date": "the earliest close date, and the run says which rule it used",
            "no_candidate_with_a_date": "skipped with a named reason",
        },
        "why": (
            "The workflow is a *booking* writeback, so the meeting is the only time in the "
            "room's hands that the phrase 'nearest' could be measured against - and a rule "
            "that silently degraded to 'soonest' would rel late every opportunity in a "
            "closed-won quarter. Absolute difference rather than 'after the meeting' "
            "because the research does not say to exclude past ones, and dropping a "
            "candidate the vendor might have chosen is the more damaging error. The "
            "tie-break is the earlier date because a nearer tie should not be decided by "
            "storage order."
        ),
        "change_it": "LocalCrm.select_related, the nearest_close_date branch.",
        "blast_radius": "The Opportunity and Deal related objects only.",
    },
    {
        "id": "case-and-opportunity-rules-extend-to-deal-and-ticket",
        "topic": "which rule applies to HubSpot's Deal and Ticket",
        "basis": (
            "[sourced] the Related Object list is 'Account, Case, Opportunity, Campaign / "
            "Deal, Ticket', and the two selection rules are stated for **Cases** and "
            "**Opportunities**. Deal and Ticket are named; their rules are not."
        ),
        "value": SELECTION_RULES,
        "why": (
            "The research's two rules are about a *shape of field*, not a vendor's object "
            "name: 'most recently created Open one' needs a status and a creation date, "
            "and 'nearest Close Date' needs a close date. A Deal has a close date and a "
            "Ticket has a status, so applying the same rule to each is the reading the "
            "evidence supports. Inventing a different rule for the HubSpot half would "
            "mean the two vendors answered the same question differently with no "
            "sourced reason. The mapping is data, so a reviewer who disagrees changes "
            "one dict entry."
        ),
        "change_it": "SELECTION_RULES in dsr/booking_crm/vocabulary.py.",
        "blast_radius": "The Deal and Ticket related objects only.",
    },
    {
        "id": "case-candidates-must-be-open",
        "topic": "whether a Closed Case can win 'most recently created'",
        "basis": (
            f'[sourced] "For **Cases**, we will relate with the most recently created '
            f'Open one". The word Open is doing the work, and it is quoted as a filter.'
        ),
        "value": {
            "filter": f"status == {OPEN_STATUS!r}",
            "tie_break": "newest created_on",
            "no_open_case": "skipped, with the count of excluded Closed cases in the message",
            "unparseable_created_on": "sorts oldest, so it cannot win",
        },
        "why": (
            "Reading 'the most recently created Open one' as 'the most recent, preferring "
            "an Open one' would relate a meeting to a ticket somebody closed last week, "
            "which is the opposite of what the rule is for. The message reports how many "
            "Closed cases were excluded, because 'nothing was found' and 'nothing was "
            "found because everything is closed' are different conversations with a rep. "
            "A row with no parseable date sorts oldest so it can never win a 'most "
            "recently created' rule by accident."
        ),
        "change_it": "LocalCrm.select_related, the most_recent_open branch, and _date_key.",
        "blast_radius": "The Case and Ticket related objects only.",
    },
    {
        "id": "create-branches",
        "topic": "what the three create labels actually do, and why they are three",
        "basis": (
            "[sourced] the labels are 'Create Contact or Lead / Create Lead / Always "
            "create Lead'. The research lists them and does not define them. The two "
            "update labels beside them ('Update matched Contact or Lead / Only update "
            "matched Lead') *are* self-defining, which is the contrast that makes the "
            "create set's shape visible."
        ),
        "value": {
            CREATE_CONTACT_OR_LEAD: (
                "create only when nothing matched; the type is the node's record_type"
            ),
            CREATE_LEAD: "create only when nothing matched; always a Lead",
            CREATE_ALWAYS_LEAD: "create a Lead even when something matched, and the new Lead is the record",
            "read_as": "two independent switches, not a three-way enum",
        },
        "why": (
            "The update labels read as *modifiers* - 'update X' and 'only update Y' - so "
            "the create labels beside them read the same way: 'create this' and 'always "
            "create that'. The alternative reading - a three-way enum, where "
            "'contact_or_lead' means 'make whichever the situation calls for' - would make "
            "'Always' a redundant word, since 'Create Lead' would already cover the "
            "always case. So the branches are independent of whether anything matched, "
            "and the only difference between the first two is the type. A flow that "
            "declares no create branch at all is legal: matching and updating without "
            "creating is a real thing a router does, and it is how the no-record "
            "catch-all becomes reachable rather than hypothetical."
        ),
        "change_it": "CREATE_MEANING in vocabulary.py, and BookingWriteback._run_anchor.",
        "blast_radius": "Every anchor node whose create branch is not contact_or_lead.",
    },
    {
        "id": "match-order-leads-before-contacts",
        "topic": "which record type wins when an email matches both",
        "basis": (
            '[sourced] "matched/created CRM record (Lead or Contact, matched by email; '
            'Salesforce L2A matching applied)". The research names the two record types '
            "and one key, and no precedence."
        ),
        "value": {
            SALESFORCE: list(MATCH_ORDER[SALESFORCE]),
            HUBSPOT: list(MATCH_ORDER[HUBSPOT]),
            "comparison": "case-insensitive, whitespace-trimmed",
            "both_match": "the first in the order wins",
            "no_match": "reported as a named reason on the node, never a silent pass",
        },
        "why": (
            "The order has to be Lead first or 'Only update matched Lead' cannot do its "
            "job: if a Contact and a Lead share an address and the Contact wins, the "
            "branch that exists to update a Lead never fires, and the branch that exists "
            "to leave Contacts alone is the one that has to be chosen deliberately. Lead "
            "is also the pre-Account record, and the same sentence names L2A - the "
            "conversion that turns one into the other - so reading the Lead first is the "
            "order the research's own conversion implies. HubSpot has no Lead, so it "
            "searches Contact alone. The order is reported on every run, so a deployment "
            "that disagrees can see which rule fired before changing it."
        ),
        "change_it": "MATCH_ORDER in dsr/booking_crm/flow.py.",
        "blast_radius": "Matching, and therefore which record every downstream node writes to.",
    },
    {
        "id": "record-type-is-a-node-setting-not-a-branch",
        "topic": "how 'Create Contact or Lead' says which one",
        "basis": (
            "[sourced] the label pairs two record types in one branch name, the way the "
            "related-object list pairs two vendors' objects in one setting."
        ),
        "value": {
            "setting": "nodes[].record_type",
            "values": ["contact", "lead"],
            "required_when": CREATE_CONTACT_OR_LEAD,
            "refused_when": f"the branch is {CREATE_LEAD!r} or {CREATE_ALWAYS_LEAD!r} and record_type is not 'lead'",
            "refusal_message": "quotes the branch label",
        },
        "why": (
            "Modelling it as a third record type would be a third thing to configure for "
            "no gain, and would make the two single-type branches unreachable. As a "
            "setting it is one field with two values, and a flow that declares it wrongly "
            "against 'Create Lead' is refused with the label quoted - because 'Create Lead' "
            "means a Lead, and a flow that says otherwise has not been understood."
        ),
        "change_it": "_anchor_settings in flow.py.",
        "blast_radius": "Anchor node declarations only.",
    },
    {
        "id": "delete-event-trigger",
        "topic": "what the researched 'Delete Event' behaviour fires on",
        "basis": (
            '[sourced] the research names the setting once and never says what it does: '
            '"Optionally configures **Create child Event** per additional guest, and the '
            '**Delete Event** behaviour." There is no sentence anywhere in the research '
            "about a cancellation, a reschedule, or a re-sync."
        ),
        "value": {
            "modes": ["never", DELETE_EVENT_ON_FAILURE, DELETE_EVENT_ON_RETRY],
            "default": "never",
            DELETE_EVENT_ON_FAILURE: "delete the Events this run created when a later node in the same run failed",
            DELETE_EVENT_ON_RETRY: "delete what a failed attempt left before the retry creates its own",
            "compensation_reported": True,
            "history_row_annotated": "deleted, deleted_crm_id, deleted_reason",
        },
        "why": (
            "The research names the setting, so it has to exist; naming it without "
            "implementing it would be the quietest kind of omission. The trigger is a "
            "judgement call, and it is kept to the two readings this workflow's own "
            "evidence reaches. The retry reading follows step 5 of the flow - 'Admin later "
            "retries any failed CRM Event' - and the thing that makes a retry necessary is "
            "a half-written Event. The failure reading follows the Events History's own "
            "purpose, which is to show partial writes. A third reading, deletion on "
            "cancellation, is *not* implemented: cancellation is section 14 of the "
            "research, a different workflow, and borrowing it here would be inventing a "
            "trigger the research does not mention. The default is 'never', because an "
            "unstated default should be the one that does nothing."
        ),
        "change_it": "DELETE_EVENT_MODES in vocabulary.py, and _honour_delete_event / _clean_on_retry.",
        "blast_radius": "The event node's delete_event setting, and nothing else.",
    },
    {
        "id": "child-events-are-their-own-history-rows",
        "topic": "what a child Event is, and whether it is retryable on its own",
        "basis": (
            "[sourced] 'Optionally configures **Create child Event** per additional "
            "guest' and 'Admin later retries any failed **CRM Event**' - singular, and "
            "Events History lists Events."
        ),
        "value": {
            "count": "one per additional guest, plus the primary",
            "no_additional_guests": "no children, and the node says so",
            "history": "one row per child, each with is_child and guest_index",
            "retry": "per row, so a failed child is retried without re-running the booking",
        },
        "why": (
            "The retry sentence is the load-bearing one. A history with one row per run "
            "could not offer a retry for 'any failed CRM Event' - only for a failed run - "
            "so a guest who mistyped their address would force a re-run that duplicates "
            "the Events that already succeeded. Per-Event rows also make the failure "
            "countable, which is the researched Events History's job."
        ),
        "change_it": "BookingWriteback._run_event and _create_event.",
        "blast_radius": "The event node's child_events setting, and the history shape.",
    },
    {
        "id": "retry-appends-a-row",
        "topic": "whether a retry rewrites the failed history row",
        "basis": (
            "[sourced] 'If the Event failed to be created, we will also show when it "
            "happened, alongside the detailed error' and 'Admin later retries any failed "
            "CRM Event from Meetings Activity → Events History'."
        ),
        "value": {
            "appends": True,
            "carries": ["attempt", "retried_from", "cleaned_previous", "when", "status", "error"],
            "original_row": "kept, and marked retried only when the retry also failed",
            "offered_on": "a failure only",
            "offered_on_success": "refused with a message quoting the retry sentence",
        },
        "why": (
            "A history that rewrites itself is not a history, and the quote is a statement "
            "about the first attempt: 'we will also show when it happened' - happened, "
            "past tense, and a row that changed its status would have a single ``when`` "
            "for two events. Two rows make the pair 'failed at T, retried at T, succeeded' "
            "readable, which is the conversation a rep is having. Refusing a retry on a "
            "success follows the research literally: it offers retry on a failure and "
            "names nothing else, and a re-create would put a second meeting in the CRM."
        ),
        "change_it": "BookingWriteback.retry.",
        "blast_radius": "The retry route and the history shape.",
    },
    {
        "id": "owner-fallback-mechanics",
        "topic": "what Cal's two crmRecordOwnerFallbackMode values do",
        "basis": (
            "[sourced] Cal exposes '``crmRecordOwnerFallbackMode`` (``relationship`` | "
            '``attributeRules``)" and "``routing.skipContactOwner`` (Whether to skip '
            "contact owner assignment from CRM integration).\" The research names both "
            "values and defines neither."
        ),
        "value": {
            "relationship": "read the owner of the record the matched record hangs off (its Account, or its Company)",
            "attributeRules": "first declared rule whose field matches the record's fields wins",
            "neither": "the assignee - [sourced] 'ownership can be transferred to whoever took the meeting'",
            "skip_contact_owner": "do not consult the CRM's own owner; write the assignee outright",
            "already_the_owner": "skipped, not written",
            "nothing_resolved": "skipped with a named reason, never a write to nobody",
        },
        "why": (
            "The two names are the vendor's own, and each one says what it keys on: "
            "'relationship' is a lookup through a relation, 'attributeRules' is a "
            "condition on a field. Implementing them that way is the only reading the "
            "names support, and implementing them as anything else would be inventing a "
            "contract. The assignee last is the researched behaviour and it is a good "
            "default: a booking that reached a human should end up owned by one. The "
            "'already the owner' skip exists because a no-op write produces an audit row "
            "describing a change that did not happen."
        ),
        "change_it": "BookingWriteback._run_ownership and _fallback_owner.",
        "blast_radius": "The update_ownership node only.",
    },
    {
        "id": "sync-toggle-is-off-by-default-and-org-wide",
        "topic": "what a meeting type with no toggle set does, and whether a run may override",
        "basis": (
            "[sourced] '**Sync Meeting Type to the CRM** … your Admins can define other "
            "behaviors to be taken when a meeting is booked' and 'your links will follow "
            'this pre-defined behavior, as these settings are applied to all users in your '
            "org'."
        ),
        "value": {
            "default": False,
            "lives_on": "the meeting type",
            "per_run_override": "refused with the org-wide sentence quoted",
            "run_when_off": "a run row is written, skipped, reason meeting_type_sync_off",
        },
        "why": (
            "Off by default, because the research says the behaviours are admin-defined: a "
            "meeting type nobody opted in has not been opted in. The override is refused "
            "rather than ignored, because silently ignoring a field a client sent is how a "
            "per-user setting gets built by accident - and the research says explicitly "
            "that the setting is applied to all users in the org, so honouring a per-run "
            "value would contradict the sentence that makes the toggle meaningful. The run "
            "is still written when the toggle is off: a booking that produced no CRM write "
            "is a fact a rep would otherwise have to infer from an absence."
        ),
        "change_it": "_meeting_type_payload and BookingWriteback.writeback.",
        "blast_radius": "Meeting types, and every run against one.",
    },
    {
        "id": "no-record-falls-through",
        "topic": "what the flow does when nothing matched and nothing was created",
        "basis": (
            "[sourced] the ordering sentence makes every downstream node depend on the "
            "create node, and the Related Object sentence gates the extra relation on a "
            "contact. Nothing in the research says what happens when the create node "
            "produces nothing."
        ),
        "value": {
            "outcome": "skipped, with a named reason - never a success, never a crash",
            "reason": "nothing_matched_and_no_create_branch",
            "downstream": "each later node skips with the same reason",
            "run_ok": False,
            "crm_writes": "none",
        },
        "why": (
            "This is the catch-all the researched rule forces into existence, and the one a "
            "build is most likely to get wrong: a flow whose fourth node has no record to "
            "write to will 'succeed' quite happily if the nodes are written to treat an "
            "absent record as a no-op, and the rep finds out when the CRM has no meeting on "
            "it. Every node skips with the reason, the run is not ok, and the message says "
            "why. The refusal is also the honest answer to a flow declared with no create "
            "branch at all, which is legal and is exactly the flow that produces it."
        ),
        "change_it": "BookingWriteback._run_anchor's None branch, and NO_RECORD_MESSAGE.",
        "blast_radius": "Every flow whose create node produced no record.",
    },
    {
        "id": "event-details-need-a-global-connection",
        "topic": "what the global Salesforce connection actually gates",
        "basis": (
            "[sourced] 'Global Salesforce connection required for Event details in "
            "Events History'."
        ),
        "value": {
            "gates": "the Event details on a history row",
            "does_not_gate": "the timestamp, the status, or the detailed error",
            "without_it": "the history still works; /events-history-availability says so",
            "hubspot": "not gated - the research attaches the sentence to Salesforce",
        },
        "why": (
            "The sentence says 'for Event details', and the Events History quote says a "
            "failure shows 'when it happened, alongside the detailed error'. Those are two "
            "different things, so gating the whole history on a connection would refuse to "
            "show an error the research says must be shown. Gating the details alone is the "
            "reading that keeps both sentences true."
        ),
        "change_it": "BookingWriteback.events_history_available.",
        "blast_radius": "The availability route, and nothing in the write path.",
    },
    {
        "id": "one-flow-per-path-per-meeting-type",
        "topic": "which flow serves a booking",
        "basis": (
            "[sourced] 'On a scheduled / not-scheduled / disqualified path in the router, "
            "admin adds a `Create or Update Record` node' - the nodes are added *to a "
            "path*, and 'writes fire on the scheduled, not-scheduled and disqualified "
            "paths automatically'."
        ),
        "value": {
            "resolution": "the newest flow declared for (room, path, meeting type)",
            "no_flow": "428 NotConfigured, naming the path it looked for",
            "path_mismatch": "refused, because a flow belongs to one path",
            "index_used": "the dynamic index, on the flow row's own path and meeting_type_id",
        },
        "why": (
            "A booking knows its path but not its flow, and the research says the write "
            "fires automatically - so something has to resolve one to the other. Newest "
            "wins, because a tenant that adds a second flow for a path means to use it. A "
            "path with no flow is a real state - a tenant that only wired up the scheduled "
            "path - and saying so is the difference between a rep learning it now and "
            "learning it from a booking that wrote nothing."
        ),
        "change_it": "BookingWriteback.flow_for and list_flows.",
        "blast_radius": "Flow resolution only. The filters are indexed JSON paths, so a "
        "fourth path needs no code change.",
    },
    {
        "id": "vendor-response-is-not-parsed",
        "topic": "what a run records when a real CRM answered",
        "basis": (
            "[not sourced] The research quotes the nodes, the branches, the related "
            "objects and the two selection rules. It quotes no vendor response body."
        ),
        "value": {
            "raw_response": "not stored - this build has no transport",
            "per_node_outcomes": "produced by the engine that executed each node",
            "note": "attached to the module and served from /vocabulary",
        },
        "why": (
            "Writing a parser for a Salesforce or HubSpot response shape from memory would "
            "produce a confident answer to a question this build has not sourced, which is "
            "worse than an empty list with a note. The engine's own per-node outcomes are "
            "the honest record, and they are what a client branches on. A deployment that "
            "adds a transport stores the raw body on the run and this inference is revisited."
        ),
        "change_it": "RESPONSE_NOT_PARSED_NOTE in dsr/booking_crm/local_crm.py.",
        "blast_radius": "Nothing today. There is no transport to change.",
    },
    {
        "id": "no-transport-is-built",
        "topic": "whether this feature talks to a real CRM over HTTP",
        "basis": (
            "[sourced] the research names Chili Piper's *router nodes* and Cal's booking "
            "fields. It cites a Salesforce integration guide and one Cal endpoint - "
            "'``GET /v2/event-types/{id}/crm-sync-errors``' - and no request body, no "
            "endpoint path for the writes, and no auth flow."
        ),
        "value": {
            "built": "the local CRM, the flow model, the run, Events History and retry",
            "not_built": "an HTTP transport to Salesforce or HubSpot",
            "reason": "the research gives no endpoint for the writes, so one would be invented",
            "seam": "LocalCrm is injectable on BookingWriteback, which is where a transport goes",
        },
        "why": (
            "Every other claim in the research is about *which* record the node writes and "
            "*which* record it relates to, and all of that is testable against an in-process "
            "CRM. An invented HTTP path would add a URL this build cannot source, and the "
            "brief's rule is not to invent a requirement the research does not mention. The "
            "seam is real and named: a deployment that has the endpoint documentation "
            "injects a transport and nothing above it changes."
        ),
        "change_it": "BookingWriteback.__init__ takes crm=; LocalCrm is the default.",
        "blast_radius": "Nothing today. The seam is the constructor.",
    },
    {
        "id": "singleton-nodes-and-repeating-field-nodes",
        "topic": "how many of each node a flow may declare",
        "basis": (
            "[sourced] the user flow says 'admin adds a `Create or Update Record` node', "
            "'``Create Event`` (Salesforce) or ``Create Engagement`` (HubSpot)', "
            "'``Add to Campaign``' and '``Update Ownership``' - each named once, and each "
            "paired with a single purpose. '``Update Field``/``Update Property``' is named "
            "the same way, and its own example is one field."
        ),
        "value": {
            "singletons": sorted(SINGLETON_NODES),
            "repeatable": ["update_field", "update_property"],
            "refusal": "a 400 naming both positions",
        },
        "why": (
            "A second Create Event node has no meaning - the flow writes one meeting - and a "
            "second Add to Campaign node would create two members. Update Field is the "
            "exception because its example is a single field, which implies a node per "
            "field rather than a node per node: '``Contact.Status = \"Sales Qualified\"``' is "
            "one of several such assignments. That reading is also the extensibility claim "
            "made concrete, since a custom field is another Update Field node rather than a "
            "code change."
        ),
        "change_it": "SINGLETON_NODES in dsr/booking_crm/flow.py.",
        "blast_radius": "Flow declaration only.",
    },
    {
        "id": "path-normalisation",
        "topic": "how a caller's spelling of a router path is read",
        "basis": (
            "[sourced] three names: 'scheduled / not-scheduled / disqualified'."
        ),
        "value": {
            "accepted": list(PATHS),
            "normalised": ["not-scheduled and 'not scheduled' -> not_scheduled", "disqualify -> disqualified"],
            "refused": "anything else, with the three listed",
        },
        "why": (
            "The research writes the middle path hyphenated and the others as single words, "
            "so a literal reading would make 'not-scheduled' and 'not_scheduled' two paths. "
            "Hyphens, spaces and case are all folded; anything beyond that is refused, "
            "because a fourth path would mean a fourth piece of researched behaviour and "
            "this build does not have one."
        ),
        "change_it": "normalise_path in dsr/booking_crm/flow.py, and PATHS in vocabulary.py.",
        "blast_radius": "Flow declaration and run filtering.",
    },
)


def describe() -> dict[str, Any]:
    """The whole registry, plus the sourced facts it is measured against."""
    from dsr.booking_crm.vocabulary import SOURCED_GAPS, SOURCED_QUOTES

    return {
        "sourced_quotes": [dict(entry) for entry in SOURCED_QUOTES],
        "sourced_gaps": [dict(entry) for entry in SOURCED_GAPS],
        "count": len(INFERENCES),
        "inferences": [dict(entry) for entry in INFERENCES],
    }


def by_id(inference_id: str) -> dict[str, Any] | None:
    return next((entry for entry in INFERENCES if entry["id"] == inference_id), None)


def node_vocabulary() -> dict[str, Any]:
    """The node table the frontend renders its picker from.

    Served with the inferences because a node name is a vocabulary question and a
    picker built from a hand-written list in the client is a second source of
    truth that will drift from the palette the validator enforces.
    """
    return {
        "vendors": list(VENDORS),
        "anchor": {vendor: ANCHOR_NODE[vendor] for vendor in VENDORS},
        "event": {vendor: EVENT_NODE[vendor] for vendor in VENDORS},
        "field": {vendor: FIELD_NODE[vendor] for vendor in VENDORS},
        "related_types": {vendor: list(RELATED_TYPES[vendor]) for vendor in VENDORS},
        "related_record_type": dict(RELATED_RECORD_TYPE),
        "update_branches": list(UPDATE_BRANCHES),
        "create_branches": list(CREATE_BRANCHES),
        "activity_assigned_to": list(ACTIVITY_ASSIGNED_TO),
        "campaign_member_status": CAMPAIGN_MEMBER_STATUS,
        "paths": list(PATHS),
    }


__all__ = ["INFERENCES", "by_id", "describe", "node_vocabulary"]
