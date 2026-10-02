"""Every decision this workflow makes that the research does not make, in one list.

The research for WF-037 is unusually specific about three things - the three create
endpoints, what each vendor returns, and where the id comes from - and silent about
almost everything around them. It does not say how long to wait, which statuses are worth
retrying, how a field map should treat a source it cannot find, what a successful create
with no id in it means, or who runs the queue worker. Those are our decisions, and a
judgement call left as a comment in a function body is one nobody re-reads - a wrong one
becomes product behaviour without anyone noticing.

So each entry is:

* **named**, so it can be argued with by name;
* **traceable** - ``basis`` says what the research does and does not say, quoting it;
* **bounded** - ``value`` is what this build chose and ``change_it`` says how to change
  it without editing a function body;
* **visible** - :func:`describe` is served at ``GET /api/wf-037/inferences`` with the
  sourced quotes beside it, so a reviewer can see exactly where the line falls.

Nothing here is a migration, a typed column, or a new required field. It is a list of
ordinary JSON served from code, and it is a *record* of a judgement rather than a
mechanism that enforces one: every entry points at the constant or function that would
have to change, so disagreeing with an entry is a one-line edit rather than an argument
about a diff.
"""

from __future__ import annotations

from typing import Any

from dsr.crm_engagement.payloads import success_codes
from dsr.crm_engagement.vocabulary import (
    CREATE_ENDPOINTS,
    RECORD_ID_LOCATIONS,
    SOURCED_QUOTES,
    TRANSFORM_NAMES,
)

INFERENCES: tuple[dict[str, Any], ...] = (
    {
        "id": "success-is-per-vendor-not-2xx",
        "topic": "which status codes count as a successful create",
        "basis": (
            "The research quotes the codes for two of the three vendors and none for the "
            "third. Dataverse: 'a successful response has status 201 Created … Without this "
            "preference, both operations return status 204 No Content.' Salesforce: '`201` — "
            "'Created' success code, for POST requests' and '`204` — 'No Content' success "
            "code, for DELETE requests and some PATCH requests.' HubSpot: the endpoint and "
            "the id, no code."
        ),
        "value": {
            "hubspot": "any 2xx, because no code is sourced for it",
            "dataverse": [201, 204],
            "salesforce": [201],
            "generalised_2xx": False,
        },
        "why": (
            "The obvious implementation - '2xx means done' - is wrong on Salesforce in a way "
            "the research's own quotation forbids: its reference puts 204 among the success "
            "codes for DELETE and some PATCH, not for a create. Reading 204 as a created row "
            "would mark an engagement synced on a response that returned nothing, with no id "
            "to store. It is also wrong on Dataverse in the other direction, where 204 is the "
            "*most common* success and a narrower set that forgot it would fail every create "
            "that did not opt into `return=representation`."
        ),
        "change_it": "success_codes() in dsr/crm_engagement/payloads.py, and CREATE_ENDPOINTS in vocabulary.py.",
        "blast_radius": "Which creates are treated as synced, and therefore what the Sync log calls a failure.",
    },
    {
        "id": "accepted-without-an-id-is-a-failure",
        "topic": "what a create the vendor accepted but returned no id for means",
        "basis": (
            "The researched step 5 is unconditional: 'On success the worker writes the returned "
            "CRM record id into its local row (`crm_record_id`) and marks the event as synced'. "
            "The data flow names where two of three vendors return that id - HubSpot's `id`, "
            "Dataverse's `OData-EntityId` header - and says nothing about Salesforce. The gap "
            "note admits the Salesforce claim 'rests on the status-code reference plus the "
            "general resource model, not a `sobjects` create page'."
        ),
        "value": {
            "state": "failed",
            "reason": "crm_id_absent",
            "needs_manual_update": True,
            "retried": False,
            "crm_row": "may well exist; this build cannot address it again",
        },
        "why": (
            "Marking such a row synced would assert something the row cannot support: the "
            "research says the id is stored 'for future updates', and with no id there is no "
            "update - the next time this event changes, the only honest options are a second "
            "create or a lookup, and both are a different workflow. So the row is failed, says "
            "why, and waits for a human who can read the CRM directly. Reporting it as a "
            "success would hide the one case a rep genuinely has to look at."
        ),
        "change_it": "_failure_reason() in dsr/crm_engagement/engine.py, and RECORD_ID_LOCATIONS in vocabulary.py.",
        "blast_radius": "The Salesforce path especially, and any Dataverse create whose OData-EntityId header goes missing.",
    },
    {
        "id": "dataverse-body-is-a-fallback-not-the-source",
        "topic": "reading a Dataverse id from a response body at all",
        "basis": (
            "The quoted create response is '`HTTP/1.1 204 No Content` … `OData-EntityId: "
            "[Organization URI]/api/data/v9.2/accounts(00aa00aa-…)`', and `return=representation` "
            "is documented to turn that into a 201 that returns data. The research does not name "
            "any field in that body."
        ),
        "value": {
            "order": ["header:OData-EntityId", "body:accountid"],
            "primary": "header:OData-EntityId",
            "sourced": {"header:OData-EntityId": True, "body:accountid": False},
        },
        "why": (
            "The header is the only place the research actually shows the id, so it is read "
            "first and is the one the Sync log names as sourced. The body is tried second only "
            "because a 201 provably carries data, and the row records that it guessed - so a "
            "reader can tell a sourced id from a hopeful one. Guessing first would mean the "
            "common case, a 204 with the header, worked by luck of field naming."
        ),
        "change_it": "RECORD_ID_LOCATIONS['dataverse'] in dsr/crm_engagement/vocabulary.py.",
        "blast_radius": "Dataverse id extraction, and what the Sync log reports as sourced.",
    },
    {
        "id": "the-entity-uri-carries-a-guid-not-an-id",
        "topic": "what counts as 'the id' inside the OData-EntityId header",
        "basis": (
            "The quoted header value is a URI ending in 'accounts(00aa00aa-…)'. The research does "
            "not say what a consumer should store."
        ),
        "value": {
            "stored": "the value inside the final parentheses, lower-cased when it is a GUID",
            "not_stored": "the whole entity URI, which embeds the organisation's own hostname",
            "non_guid": "stored verbatim, not reshaped",
        },
        "why": (
            "The header holds two different things: an identifier, and the organisation's "
            "address. Storing the URI would write one tenant's hostname into another tenant's "
            "row and make the field impossible to compare or index. The parentheses are read as "
            "a pair rather than pattern-matched for a GUID shape, so a header carrying something "
            "unexpected is stored as it arrived instead of being forced into a GUID - a value "
            "this build does not recognise is better preserved than mangled."
        ),
        "change_it": "id_from_entity_id() in dsr/crm_engagement/payloads.py.",
        "blast_radius": "Every Dataverse crm_record_id.",
    },
    {
        "id": "unresolved-sources-are-omitted-not-sent-null",
        "topic": "what a create does with a field whose source is not on the event",
        "basis": (
            "The research's field-mapping step says the room 'flags unknown properties, wrong "
            "types, and unsupported option values before any data is written'. It says flags, not "
            "refuse, and it says nothing about a property whose value the room does not have."
        ),
        "value": {
            "value": "the property is left out of the payload",
            "recorded": "a finding on the Sync log row, naming the field, the source and the code",
            "default": "used when the field map supplies one, and the finding says so",
            "whole_map_refused": False,
        },
        "why": (
            "Sending null is the worse of the two available answers: a CRM either rejects the "
            "create over a field the room never had, or accepts it and has now had a real value "
            "overwritten with nothing. Omitting the property and saying so keeps the other four "
            "fields of a five-field map working, which is what a flag is for. Refusing the whole "
            "create would be the other defensible choice, and it would mean one unmapped field "
            "silently costs every event of that type."
        ),
        "change_it": "map_event() in dsr/crm_engagement/mapping.py.",
        "blast_radius": "The properties on every create, and the findings column of the Sync log.",
    },
    {
        "id": "buyer-resolution-is-required-only-when-a-field-map-asks",
        "topic": "when the researched step 3 must produce a buyer",
        "basis": (
            "Step 3 is 'a background worker resolves the buyer's CRM record using the field "
            "mapping / sync key', and the data sources name 'buyer identity (CRM record id or "
            "email from W1/W2)'. The research does not say what happens when the mapping sends "
            "no buyer field, nor when neither identity is present."
        ),
        "value": {
            "required_when": "the field map has an out/both field whose source is a buyer identity",
            "order": ["buyer_crm_id", "buyer_email"],
            "synonyms_apply": True,
            "neither_present": "blocked on buyer_unresolved",
            "not_required_when": "the map sends no buyer field - a room-level event",
        },
        "why": (
            "Requiring a buyer unconditionally would block a legitimate room-level engagement "
            "that the team chose to write without one, and would do it with a reason that reads "
            "as a bug. Requiring it never would send engagement rows belonging to nobody, which is "
            "the case the step exists to prevent. Tying the requirement to the mapping makes the "
            "team's own field map the switch, which is the same place they already express the "
            "decision. A CRM record id is tried before an email because an id is an answer and "
            "an email is a lookup."
        ),
        "change_it": "_resolve_buyer() and IDENTITY_SOURCES in dsr/crm_engagement/engine.py.",
        "blast_radius": "Which events are blocked on buyer_unresolved, and the resolution block on every plan.",
    },
    {
        "id": "blocked-rows-are-re-evaluated",
        "topic": "what a drain does with a row it previously could not send",
        "basis": (
            "The extensibility note promises that adding 'download', 'pricing-view', "
            "'cta-click' needs 'a mapping row, not a code path'. If a blocked row were terminal, "
            "adding the mapping row would not send the events that were already waiting, and the "
            "promise would only hold for events that arrive afterwards."
        ),
        "value": {
            "drain_treats": "pending only",
            "blocked_rows": "re-planned on every drain and on retry, never remembered as blocked",
            "fixing_the_configuration": "adding the mapping row and draining again is enough",
            "consequence": "an unfixed block is reported on every drain rather than once",
        },
        "why": (
            "The alternative - a blocked row as terminal - makes the researched extensibility "
            "promise half true, which is worse than not making it: a team adds the mapping row, "
            "runs the queue, sees nothing happen, and concludes the promise was wrong. Repeating "
            "the block is the honest cost, because it is the state that still needs a human. The "
            "alternative to repeating it is a queue that forgets, and a queue that forgets is how "
            "engagement events go missing without anyone being able to say which."
        ),
        "change_it": "pending_rows() in dsr/crm_engagement/queue.py, and drain() in engine.py.",
        "blast_radius": "GET /queue, POST /queue/drain, and the blocked count in every drain summary.",
    },
    {
        "id": "no-connector-is-a-state-not-a-404",
        "topic": "what a room with no CRM connector answers",
        "basis": (
            "The research's flow starts from a room event and the whole write depends on a CRM to "
            "write to, but it never says what the room does before one is connected."
        ),
        "value": {
            "drain": "428 SyncNotConfigured",
            "record_event": "the engagement row is still written and the queue row blocked",
            "readiness_view": "names what is missing",
        },
        "why": (
            "428 exists for this: 'well formed, but this installation is not set up to answer it "
            "yet' is a different instruction to a client than 'you got the request wrong', and the "
            "frontend's request helper carries the status precisely so a page can tell them "
            "apart. Refusing to record the event would be worse - the buyer's action happened, and "
            "the room is the system of record for it. So the row is written, the block is named, "
            "and the setup work is visible rather than inferred from a 404."
        ),
        "change_it": "require_configured() and _resolve_connector() in dsr/crm_engagement/engine.py.",
        "blast_radius": "POST /rooms/{room_id}/engagements and POST /rooms/{room_id}/queue/drain.",
    },
    {
        "id": "retry-ladder",
        "topic": "which failures are worth a second attempt",
        "basis": (
            "The research says only that the worker 'retries with backoff' and 'retries on "
            "failure'. It names no status, no attempt count, and no delay."
        ),
        "value": {
            "retryable": [408, 425, 429, 500, 502, 503, 504],
            "not_retryable_409": (
                "the sync key is unique by design, so a 409 is the uniqueness working and a "
                "second identical create cannot change it"
            ),
            "transport_error": "retryable, because a refused connection is this second's problem",
            "max_attempts": 3,
            "backoff": "exponential, 0.5s doubling, capped at 30s",
        },
        "why": (
            "The 409 exclusion is the researched one rather than a guess: the sync key is chosen "
            "to be unique 'so the CRM itself rejects collisions', so a collision is the mechanism "
            "working and retrying it would burn quota to arrive at the same answer. Repeating a "
            "400 or a 403 is worse still - the request is wrong, and a CRM's rate limit is a cost "
            "somebody else pays. Three attempts with doubling backoff is the ordinary reading of "
            "'retries with backoff' and keeps a seeder or a test from waiting minutes."
        ),
        "change_it": "RETRYABLE_STATUS in dsr/crm_engagement/vocabulary.py, and CreateResult.retryable in delivery.py.",
        "blast_radius": "How many attempts a failure takes, and whether a row waits for a human.",
    },
    {
        "id": "timeout",
        "topic": "how long to wait for a create",
        "basis": "The research says nothing about a timeout.",
        "value": {
            "seconds": 10.0,
            "note": "not a knob in the vocabulary; a constant in delivery.py",
        },
        "why": (
            "10 seconds is long enough for a third-party API on a slow day and short enough that a "
            "dead host does not hold a queue worker. It is deliberately *not* the 5 seconds the "
            "sibling Outreach workflow documents: that number belongs to a different vendor's "
            "documented budget, and borrowing it would imply a source that does not exist here."
        ),
        "change_it": "DEFAULT_TIMEOUT in dsr/crm_engagement/vocabulary.py.",
        "blast_radius": "How long a drain can take when a CRM is unreachable.",
    },
    {
        "id": "the-worker-runs-inline",
        "topic": "where the researched background worker actually is",
        "basis": (
            "The automation note is explicit: the write is 'asynchronous: the room's queue worker "
            "fires it without further user input, with retry on failure'."
        ),
        "value": {
            "queue": "a persisted record per event, not a counter",
            "default": "record_event fires the worker as the last step of the request",
            "deferred": "record_event(fire_queue=False) leaves the row for POST /queue/drain",
            "external_worker_possible": "yes - the queue row is the durable hand-off",
        },
        "why": (
            "This product has no scheduler, so a 'background worker' has to be something. Running "
            "it inline at the end of the enqueue still satisfies the researched sentence exactly: "
            "one user action - a buyer opening an asset - produces the CRM write, with no second "
            "user input. The queue is a real row rather than an in-memory list precisely so that "
            "moving the worker out of the request, onto a cron or a task queue, is a change to who "
            "calls drain and not a change to what is stored."
        ),
        "change_it": "record_event() and drain() in dsr/crm_engagement/engine.py.",
        "blast_radius": "When a create happens; nothing about what it carries.",
    },
    {
        "id": "unverifiable-properties-are-findings",
        "topic": "what a field map can and cannot be checked against at save time",
        "basis": (
            "The mapping step says the room 'reads the CRM's property/type metadata and flags "
            "unknown properties, wrong types, and unsupported option values before any data is "
            "written'. Reading a CRM's property metadata is that step's own API surface, and "
            "WF-037 does not cite it."
        ),
        "value": {
            "refused_at_save": [
                "no fields",
                "no target property",
                "a target mapped twice",
                "an unknown direction",
                "a picklist.map with no options",
                "a sync key that collides with a field",
            ],
            "flagged_at_send": [
                "a target the CRM may not have",
                "a type the CRM may not accept",
                "a picklist value not in the table",
            ],
            "unverifiable_here": "anything requiring a live CRM's metadata",
        },
        "why": (
            "Refusing at save what can only be checked against a live CRM would make it impossible "
            "to save a mapping before the CRM is provisioned - and the researched order is that the "
            "catalogue and map come first. So the structural refusals stay, which are the ones that "
            "would produce a wrong payload no matter what the CRM looks like, and the "
            "metadata-dependent checks become findings on the create, where the vendor's own error "
            "is the authority and costs nothing to obtain."
        ),
        "change_it": "normalise_field_map() and map_event() in dsr/crm_engagement/mapping.py.",
        "blast_radius": "Which field maps can be saved, and what the Sync log reports as a finding.",
    },
    {
        "id": "unit-variants-are-not-synonyms",
        "topic": "whether dwell_ms is another spelling of dwell_seconds",
        "basis": (
            "The data flow names 'dwell time' as a field of the sales-room event and does not give "
            "its unit. Every other field here has a spelling list; this one could have had a unit "
            "conversion in it."
        ),
        "value": {
            "dwell_seconds_synonyms": ["dwell", "dwell_time", "seconds_on_asset", "time_on_asset"],
            "dwell_ms_is_a_synonym": False,
        },
        "why": (
            "Every other synonym in the list is a different way of spelling the same number. "
            "``dwell_ms`` is a different number, and treating it as a synonym would send a value "
            "off by a factor of a thousand with nothing in the row to say so - a 4-minute visit "
            "recorded as 240,000 seconds, in a CRM, permanently. A team that stores milliseconds "
            "writes a field map with a transform, where the conversion is visible."
        ),
        "change_it": "FIELD_SYNONYMS in dsr/crm_engagement/vocabulary.py.",
        "blast_radius": "Any field map whose source is dwell_seconds.",
    },
    {
        "id": "bearer-auth-for-all-three",
        "topic": "how the connector's token is presented",
        "basis": (
            "The research for this workflow never mentions authentication. Its data sources name "
            "the buyer identity coming 'from W1/W2', and the OAuth authorization-code flow is W1's "
            "subject, not this one's."
        ),
        "value": {
            "header": "Authorization: Bearer <token>",
            "vendor_specific": False,
            "stored": False,
        },
        "why": (
            "All three vendors' documented APIs take an OAuth bearer token on the same header, so "
            "one shape is correct for all three and a per-vendor branch would be a guess. The token "
            "is never written to a row and never returned by a read - it appears only in the "
            "Authorization header of the recorded request, redacted at the point of recording, "
            "because that header *is* the token."
        ),
        "change_it": "build_create() in dsr/crm_engagement/payloads.py, and CreateRequest.to_dict in the same file.",
        "blast_radius": "Every create's headers, and nothing stored.",
    },
    {
        "id": "an-async-preference-has-no-poll",
        "topic": "what setting respond-async actually does here",
        "basis": (
            "The extensibility note names the preference: 'Optional `Prefer: "
            "return=representation` / `respond-async` style preferences let a connector opt into "
            "returning created data.' It does not describe an async response, a job, or a status "
            "endpoint to poll."
        ),
        "value": {
            "sent": "yes - the token goes on the Prefer header as configured",
            "polled": False,
            "job_status_endpoint": None,
            "202_treated_as": "not a create success on any vendor; it is not in the documented set",
        },
        "why": (
            "Sending the header honours the researched extension point. Polling would be inventing "
            "an API: this build has read no job-status endpoint for any of the three, and a worker "
            "that invented one would be making requests to a URL nobody has verified. So the token "
            "is passed, the preference is explained on the connector read, and a 202 is reported "
            "rather than resolved - which is honest and is why the preference carries that warning."
        ),
        "change_it": "PREFERENCES and preference_list() in dsr/crm_engagement/vocabulary.py and payloads.py.",
        "blast_radius": "A connector that configures respond-async, and what a 202 does to its rows.",
    },
)


def by_id(inference_id: str) -> dict[str, Any] | None:
    for entry in INFERENCES:
        if entry["id"] == inference_id:
            return entry
    return None


def describe() -> dict[str, Any]:
    """The whole registry, beside the sourced half it is contrasted against.

    Both halves in one payload on purpose. The point of the endpoint is that a reader can
    see where the line falls, and that means showing what was quoted next to what was
    chosen rather than only the latter.
    """
    return {
        "count": len(INFERENCES),
        "sourced_quotes": dict(SOURCED_QUOTES),
        "sourced": {
            "vendors": sorted(CREATE_ENDPOINTS),
            "success_codes": {
                vendor: list(success_codes(vendor)) for vendor in sorted(CREATE_ENDPOINTS)
            },
            "record_id_locations": {
                vendor: [
                    {
                        "where": entry["where"],
                        "path": entry.get("path") or entry.get("name"),
                        "sourced": entry["sourced"],
                    }
                    for entry in entries
                ]
                for vendor, entries in RECORD_ID_LOCATIONS.items()
            },
            "transforms": list(TRANSFORM_NAMES),
        },
        "inferences": [dict(entry) for entry in INFERENCES],
    }
