"""Every judgement call in WF-041, in one inspectable place.

The research for WF-041 is unusually explicit about its own limits, and the
build brief asks for that distinction to be kept rather than blurred. Sourced
and assumed are kept apart, and this module is the "assumed" half, served at
``/api/wf-041/inferences`` so a reviewer can disagree with a *named* entry
instead of hunting through a diff.

The sourced half is in :mod:`dsr.dedupe.vocabulary` and
:mod:`dsr.dedupe.rules`, and :func:`describe` returns both together - the point
of the endpoint is showing the reader where the line falls, which means showing
what is on each side of it.

A judgement call left as a comment in a function body is one nobody re-reads,
and a wrong one becomes product behaviour without anyone noticing. Each entry
here is named, traceable to what the research does and does not say, bounded by a
``value`` saying what this build chose, and carries a ``change_it`` so it can be
changed without editing a function body.
"""

from __future__ import annotations

from typing import Any

#: The line that governs the largest judgement in the package: the merge.
MERGE_GAP = (
    "Salesforce auto-merge (merging duplicates automatically) lives in help.salesforce.com "
    "duplicate-management docs, which are JS-rendered and unreadable; the merge action is "
    "therefore *not* claimed."
)

#: The line that governs the hard block.
MULTIPLE_QUOTE = (
    "If the external ID matches multiple existing records, then a 300 error is returned, and no "
    "records are created or updated."
)

INFERENCES: tuple[dict[str, Any], ...] = (
    {
        "id": "multiple-matches-hard-block-regardless-of-key",
        "topic": "whether a multi-match on a non-external-ID key is also a hard block",
        "basis": MULTIPLE_QUOTE,
        "value": {
            "external_id_multi_match": "300, nothing created or updated",
            "other_key_multi_match": "hard block, nothing created or updated, no status invented",
            "policy_consulted": False,
        },
        "why": (
            "The research only numbers the external-ID case, but step 3 of the flow says the rule "
            "returns 'a duplicate alert with the matching record id' - singular. A result naming "
            "several records is not that, and the researched remedy for such a result is to write "
            "nothing. A multi-match on email is the same situation with no documented number, so "
            "no number is invented for it."
        ),
        "change_it": "The 'multiple' branch of rules.decide, and the status argument in rules.multiple.",
        "blast_radius": "Every multi-match decision, whatever key produced it.",
    },
    {
        "id": "ambiguous-key-match-is-a-hard-block",
        "topic": "what happens when two different keys match two different records",
        "basis": (
            "The research says the duplicate alert carries 'the matching record id' and that a "
            "unique index 'prevents the creation of duplicates'. It does not describe the case "
            "where the email matches one record and the external ID matches another."
        ),
        "value": {"rule": "two keys matching different records is a hard block", "status": None},
        "why": (
            "Either answer would be a guess. Updating the email match discards the external ID "
            "identity the connector itself wrote; updating the external ID match loses the CRM's "
            "own primary unique identifier. Writing nothing and saying so is the only honest "
            "outcome, and it matches how the researched multi-match case is handled."
        ),
        "change_it": "matching.decisive, and the 'multiple' branch of rules.decide.",
        "blast_radius": "Only rows where more than one key fires, which is the rarest path.",
    },
    {
        "id": "unique-index-outranks-allow-policy",
        "topic": "whether allowSave can force a duplicate through a unique index",
        "basis": (
            "Salesforce: 'The Unique attribute prevents the creation of duplicates.' The same page "
            "documents allowSave as 'allow the user to acknowledge the alert and save the duplicate "
            "record'. The research does not say which wins when both are configured."
        ),
        "value": {"rule": "allow + a matching unique key is a hard block, not a created duplicate"},
        "why": (
            "The research is explicit that the Unique attribute prevents the duplicate, and says "
            "nothing at all about allowSave overriding an index. Letting the write through would "
            "contradict the one statement that is sourced. A database that was told to enforce "
            "uniqueness does not enforce it because a request asked nicely."
        ),
        "change_it": "The 'allow' branch of rules.decide.",
        "blast_radius": "Connections with allowSave and a unique key on the matched key.",
    },
    {
        "id": "merge-escalates-and-does-not-merge",
        "topic": "what the merge policy actually does",
        "basis": MERGE_GAP,
        "value": {
            "outcome": "escalated",
            "writes": False,
            "needs_human": True,
            "detail_included": True,
        },
        "why": (
            "The research names auto-merge as the escalation target and then says the merge action "
            "is not claimed. Implementing a merge would be inventing a requirement the research "
            "declines to make, and a wrong merge is destructive in a way a blocked write is not. "
            "includeRecordDetails is requested so the human who picks this up can see both sides."
        ),
        "change_it": "The final branch of rules.decide, and the merge entry in policy.POLICY_DETAILS.",
        "blast_radius": "Only connections configured with the merge policy.",
    },
    {
        "id": "local-precheck",
        "topic": "what 'evaluated in the room before calling the CRM' means here",
        "basis": (
            "extensibility: 'A third party can register additional matchers (fuzzy domain + name) "
            "evaluated in the room *before* calling the CRM, reducing wasted API calls.' The "
            "research does not define what the in-room step can see."
        ),
        "value": {
            "scope": "the room's own already-synced rows",
            "short_circuits_when": "the local check finds a match and the policy is block",
            "records": "crm_called=false on the decision when it short-circuits",
        },
        "why": (
            "The stated payoff is fewer API calls, which only happens if the local answer is "
            "authoritative enough to stop the round trip. Scoping it to the room's own rows is the "
            "conservative reading: the local set is a subset of the CRM's, so a local match is "
            "certainly also a CRM match, and short-circuiting on it can never miss a duplicate. It "
            "can only be conservative in the direction of blocking more often."
        ),
        "change_it": "The short-circuit in DedupeEngine.evaluate, and the scope filter beside it.",
        "blast_radius": "Every block-policy decision on a room with prior syncs.",
    },
    {
        "id": "key-precedence",
        "topic": "which key decides when several keys match different records",
        "basis": (
            "The research names the four matching keys and says duplicate rules decide, but does "
            "not publish a precedence between them. Salesforce duplicate rules do have an order, "
            "which is the shape being mirrored, but its values are admin-configured."
        ),
        "value": {"order": ["external_id", "email", "account_number", "domain"]},
        "why": (
            "An external ID is a value the connector itself wrote, so a match on it is a statement "
            "of identity. Email is the CRM's own 'primary unique identifier' per HubSpot. An "
            "account number identifies an organisation. A domain is shared by every person at a "
            "company and is the weakest signal of the four."
        ),
        "change_it": "MATCH_KEYS in vocabulary.py. The order is the precedence; nothing else reads it.",
        "blast_radius": "Every decision where more than one key could have fired.",
    },
    {
        "id": "unique-key-defaults",
        "topic": "which keys carry a unique index by default",
        "basis": (
            "HubSpot documents email as the primary unique identifier and domain as an additional "
            "one for companies. Salesforce and Dataverse let an administrator choose. The research "
            "does not publish a default set."
        ),
        "value": {
            "default_unique_keys": ["email"],
            "reason": "email is the only one documented as primary",
        },
        "why": (
            "Defaulting to email matches the one vendor statement that exists. Marking domain "
            "unique as well would be defensible for a company-shaped connection, and the "
            "'domain' entry in MATCH_KEYS says exactly that, so a deployment can widen it per "
            "connection rather than this build guessing for it."
        ),
        "change_it": "DEFAULT_UNIQUE_KEYS in vocabulary.py.",
        "blast_radius": "Which policies get refused by the unique-index rule, on new connections.",
    },
    {
        "id": "default-policy-is-block",
        "topic": "the policy a connection gets when it does not ask for one",
        "basis": (
            "The workflow is named 'Detect and block duplicate records during sync' and all four "
            "policies are documented, but the research does not name a default."
        ),
        "value": {"default": "block"},
        "why": (
            "Blocking is the only default that is reversible. A wrong 'update' has already "
            "overwritten a CRM row, and a wrong 'allow' has already created a duplicate; a wrong "
            "'block' is a row the connector declined to write, which a rep can then resolve. It "
            "also matches the ticket's own name."
        ),
        "change_it": "DEFAULT_POLICY in vocabulary.py.",
        "blast_radius": "Every connection created without an explicit policy.",
    },
    {
        "id": "decision-logs-the-room-row",
        "topic": "where the decision and matched record id are recorded",
        "basis": (
            "user_flow step 5: 'The decision and the matched record id are logged on the room row'. "
            "data_flow: 'room row annotated with match id and outcome'."
        ),
        "value": {
            "dedicated_record": "crm_dedupe_decision, room-scoped",
            "room_annotation": "room data gains dedupe.last_outcome, dedupe.last_match_id, dedupe.updated_at",
            "room_annotation_capped": True,
        },
        "why": (
            "The research says the annotation lands on the room row, and a bounded annotation is "
            "kept there because that is where a rep looks. The full decision goes in its own record "
            "as well, because an array on a room is a poor place to keep the full match detail and "
            "a capped one cannot answer a question about the eleventh decision."
        ),
        "change_it": "The annotation block in DedupeEngine.record, and ROOM_ANNOTATION_LIMIT.",
        "blast_radius": "The room row, and the size of every decision record.",
    },
    {
        "id": "matched-payloads-need-include-record-details",
        "topic": "what a decision stores about a matched record",
        "basis": (
            "Salesforce: includeRecordDetails is 'return all fields in the duplicate record', and "
            "'The default value for all fields is false'. So with the researched default, a "
            "duplicate alert carries the matching record id and nothing more."
        ),
        "value": {
            "stores_payloads_only_when": "includeRecordDetails was requested",
            "stores_ids": "always",
        },
        "why": (
            "Recording payloads the request did not ask for would be inventing evidence about what "
            "the CRM sent back. The decision keeps ids either way, because step 3 and step 5 of the "
            "flow both name the matched record id as the thing worth logging."
        ),
        "change_it": "The details check in rules.decide.",
        "blast_radius": "The size of every decision record with a match.",
    },
    {
        "id": "vendor-is-configuration-not-behaviour",
        "topic": "whether the vendor changes the decision",
        "basis": (
            "The research documents a different duplicate mechanism per vendor: Salesforce's "
            "Duplicate Management rules and its header, HubSpot's hasUniqueValue properties, "
            "Dataverse's alternate keys."
        ),
        "value": {
            "changes_the_decision": False,
            "stored_as": "the connection's vendor, shown in the UI and the decision",
            "mechanism_published_at": "/api/wf-041/vocabulary",
        },
        "why": (
            "All three mechanisms produce the same three answers - clean, one match, several - so "
            "branding the decision by vendor would make three code paths agree by coincidence. The "
            "vendor is kept because an administrator debugging a duplicate needs to know which admin "
            "page to open, and that is a UI concern rather than a behavioural one."
        ),
        "change_it": "VENDOR_MECHANISMS in vocabulary.py, and the vendor field on a connection.",
        "blast_radius": "Nothing in the decision. The vendor label on a connection and its decisions.",
    },
    {
        "id": "fuzzy-matcher-is-registered-not-special",
        "topic": "why the research's own example matcher ships enabled",
        "basis": (
            "extensibility: 'A third party can register additional matchers (fuzzy domain + name)'. "
            "The research names the example but does not specify its scoring."
        ),
        "value": {
            "id": "fuzzy:domain_name",
            "score": "Jaccard overlap of name tokens, only when the domains are equal",
            "threshold": 0.85,
            "enabled_by_default": True,
        },
        "why": (
            "A registration seam nothing uses is a seam nothing tests. Shipping the named example "
            "means the demo exercises the extension point, and a reviewer can see the shape a "
            "third party would fill in. A domain alone never scores: one company has hundreds of "
            "people, so domain-plus-name is the research's combination rather than either half."
        ),
        "change_it": "The Matcher registration in matching.builtin_matchers, or the registry at runtime.",
        "blast_radius": "Room-scope matchers only, and only rows that carry both a domain and a name.",
    },
    {
        "id": "min-score-is-per-connection",
        "topic": "what the match threshold applies to",
        "basis": (
            "The research says a third party can register matchers and that a duplicate is a "
            "matter of confidence ('high-confidence cases'). It does not publish a threshold."
        ),
        "value": {
            "field": "min_score on the connection",
            "default": None,
            "default_meaning": "each matcher uses its own threshold",
            "applies_to": "every registered matcher, overriding the matcher's threshold",
            "example": "1.0 demands exact matches and silences every fuzzy matcher",
        },
        "why": (
            "An exact matcher scores 1.0 or nothing, so leaving the threshold alone keeps every "
            "built-in behaviour exactly as the sources describe and lets the registered fuzzy "
            "matcher do its job at its own 0.85. The knob overrides rather than combines with a "
            "matcher's threshold, so one number can both demand exactness and silence a fuzzy "
            "matcher - which is the 'high-confidence' dial the research names, tunable without a "
            "deploy."
        ),
        "change_it": "The min_score field in policy.normalise_connection and the threshold in matching.match_rows.",
        "blast_radius": "Any connection that sets it. Connections that do not are unaffected.",
    },
    {
        "id": "no-outbound-crm-call",
        "topic": "whether this feature calls a real CRM",
        "basis": (
            "The research documents Salesforce, HubSpot and Dataverse request and response "
            "shapes. It documents no endpoint this product can reach, and no credentials, and the "
            "product has no CRM client."
        ),
        "value": {
            "calls_outbound": False,
            "crm_recorded": "crm_record, seeded and readable over HTTP",
            "the_crm_is": "the audited store, queried the way a duplicate rule would query it",
        },
        "why": (
            "Writing a fake HTTP client to a real vendor would be a claim the product cannot back. "
            "The decision logic is the researched part and it is exercised against real rows "
            "through the same query path a CRM rule would use, so swapping in a transport later is "
            "a change to one class rather than a rewrite."
        ),
        "change_it": "StoreCrm in engine.py. Nothing else in the package knows where rows come from.",
        "blast_radius": "Nothing today. It is the seam a real connector would use.",
    },
)


def by_id(inference_id: str) -> dict[str, Any] | None:
    for entry in INFERENCES:
        if entry["id"] == inference_id:
            return entry
    return None


def describe() -> dict[str, Any]:
    """The whole registry, beside the half of the workflow that is sourced."""
    from dsr.dedupe import rules
    from dsr.dedupe.vocabulary import (
        DUPLICATE_RULE_HEADER,
        MATCH_KEYS,
        MULTIPLE_MATCH_STATUS,
        VENDORS,
        build_duplicate_rule_header,
    )

    return {
        "count": len(INFERENCES),
        "sourced_quote": MERGE_GAP,
        "sourced": {
            "duplicate_rule_header": list(DUPLICATE_RULE_HEADER),
            "match_keys": [entry["key"] for entry in MATCH_KEYS],
            "vendors": list(VENDORS),
            "multiple_match_status": MULTIPLE_MATCH_STATUS,
            "allow_save_header": build_duplicate_rule_header("allow"),
            "block_header": build_duplicate_rule_header("block"),
        },
        "inferences": [dict(entry) for entry in INFERENCES],
        "outcomes": rules.outcome_table(),
    }
