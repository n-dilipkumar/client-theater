"""Every judgement call in this package, in one inspectable place.

The research for WF-043 is unusually specific about the mechanics and silent
about the edges. It names the six fields of a change event, the four change types,
the buffering rule, the channel-name case rule, the enrichment isolation rule and
the enrichment-by-change-type rule, the Pub/Sub flow control, the 3 MB buffer
recommendation, the Dataverse header, the four refused query options, the
annotation, and HubSpot's cap and exemption. What it does *not* do is say what
happens to the last transaction in a stream, where a buyer comes from, or what an
event whose sync key is missing should do. Those edges are where a build has to
decide something, and a decision nobody can find is a decision nobody can argue
with.

So they are collected here rather than left as comments in function bodies. Each
entry is:

* **named**, so it can be argued with by name;
* **traceable** - ``basis`` says what the research does and does not say;
* **bounded** - ``value`` is what this build chose, and ``change_it`` says how to
  change it without editing a function body;
* **visible** - :func:`describe` is served at ``GET /api/wf-043/inferences``, so a
  reviewer reads the list instead of reconstructing it from a diff.

Nothing here is a migration, a typed column, or a new required field. It is a list
of ordinary JSON, exactly like everything else this product stores, and it is a
*record* of a judgement rather than a mechanism that enforces one.

Four entries are not inferences but boundaries, and are listed for that reason:
the Avro decoder, gap reconciliation, the HubSpot subscription REST surface, and
the edition of a real Salesforce org. Each exists so the absence reads as a
decision rather than as an oversight.
"""

from __future__ import annotations

from typing import Any

from dsr.change_stream import vocabulary
from dsr.change_stream.fieldmap import DEFAULT_SYNC_KEY_FIELD

#: The sentence from the research that governs the buffering behaviour, quoted so
#: the reading below can be checked against the original rather than against a
#: paraphrase of it.
SOURCED_QUOTE = (
    "On each event the room checks changeType, buffers the change under its "
    "transactionKey, and only commits to the room's local replica when the key changes."
)

#: The second sentence the build leans on hardest: the reason event enrichment
#: exists at all.
ENRICHMENT_QUOTE = (
    "If the room needs an unchanged field (e.g. the external ID) to resolve the record, "
    "that field is added as an enriched field on the channel."
)

INFERENCES: tuple[dict[str, Any], ...] = (
    {
        "id": "commit-when-the-key-changes",
        "topic": "what happens to the last transaction in a stream",
        "topic_note": "the largest inference in the package",
        "basis": (
            "The research states the commit rule once - \"only commits to the room's local "
            'replica when the key changes" - and that rule has no terminal case. A stream '
            "that ends with a transaction still parked has a change that no later key will ever "
            "close."
        ),
        "value": {
            "commit_trigger": "an event arrives under a different transactionKey",
            "last_transaction": "stays parked until the subscription is closed, then flushes",
            "drain_point": "POST /subscriptions/{id}/close, and nothing else",
        },
        "why": (
            "Dropping it instead would silently lose a change, which is the one outcome a "
            "'near real time' promise must never produce and the reason this workflow exists "
            "at all rather than a nightly export. Flushing on close is also the only drain "
            "point the research implies: a subscription is described as long-lived, and long-"
            "lived is what eventually ends."
        ),
        "change_it": "BufferSet.flush in dsr/change_stream/buffering.py, and close in engine.py.",
        "blast_radius": "Which transactions reach the replica, and when.",
    },
    {
        "id": "failed-commit-stays-parked",
        "topic": "what happens to a transaction that cannot be applied to the replica",
        "basis": (
            "The research states the commit rule and says nothing about a commit that "
            "cannot complete. It does say the commit happens as a unit, which is the only "
            "thing here with any force behind it."
        ),
        "value": {
            "planning": "every event in the transaction is planned before any of them is written",
            "on_failure": "the transaction goes back where it was, still parked, and the error propagates",
            "close": "a flush that cannot be applied refuses the close and leaves the subscription open",
            "events": "stay in the room's change log in state buffered, so nothing is invisible",
        },
        "why": (
            "Three options existed: apply what could be applied, drop the transaction, or "
            "refuse. Applying what could be applied contradicts the transaction key's whole "
            "purpose and would leave a replica half a transaction ahead of the CRM. Dropping "
            "loses a change the room already accepted and told a client it had received. "
            "Refusing keeps the change committable, and the researched remedy - enrich the "
            "field the room needs to resolve a record - is something a room can only do while "
            "it still has a subscription to commit on, which is why the close is refused "
            "rather than completed."
        ),
        "change_it": "BufferSet.restore_front in buffering.py, and _commit_many in engine.py.",
        "blast_radius": "Which transactions are lost, and whether a close can strand a change.",
    },
    {
        "id": "buffer-is-per-process",
        "topic": "where a parked transaction lives between a restart and its commit",
        "basis": (
            "The research describes a long-lived subscription and says nothing about "
            "reconnecting mid-transaction, or about whether a half-parked change should be "
            "replayed, dropped, or re-requested."
        ),
        "value": {
            "lives_in": "process memory, one BufferSet per subscription, shared across engine instances",
            "why_shared": (
                "the engine is built per request from StoreDep, so a buffer held on the engine "
                "would be discarded between the event that parked a change and the event that "
                "closes its transaction - two HTTP requests apart, and the commit rule would "
                "never fire at all"
            ),
            "across_restart": "lost, and reported as lost rather than re-requested",
            "stored": "the events themselves, so a transaction is auditable after it commits",
        },
        "why": (
            "A buffer that survived a restart would have to decide what to do with a "
            "transaction that was half-received, and the research offers nothing to decide "
            "with. Re-requesting from the delta link is a separate researched workflow. The "
            "commit itself is durable, so what a reviewer loses by a restart is the parked "
            "half, not the replica."
        ),
        "change_it": "BufferSet._registry in dsr/change_stream/buffering.py.",
        "blast_radius": "Which changes survive a process restart.",
    },
    {
        "id": "unresolvable-record-needs-enrichment",
        "topic": "what to do with an update event whose sync key is not in the payload",
        "topic_note": "the rule the whole enrichment feature exists for",
        "basis": (
            "The research names the case in the user flow and again in the enrichment "
            f"evidence: {ENRICHMENT_QUOTE!r}. It does not say what the room does in the "
            "meantime."
        ),
        "value": {
            "on_the_commit_path": "refused, 409 enrichment_required, naming the field to add",
            "resolution_order": "payload first, enriched fields second",
            "create_and_undelete": "refused with the enrichment advice withheld, because enrichment does not apply",
        },
        "why": (
            "The three options were guess a record, apply the change to nothing, or refuse. "
            "Guessing is the one that corrupts a replica silently. Applying to nothing is the "
            "one that loses a change. Refusing names the fix - add this field to this "
            "channel's enriched fields - which is the sentence the research supplies. "
            "Resolution order is payload before enrichment because if the sync key itself "
            "changed, the new value is the row the room should find, and the enriched copy "
            "still holds the old one."
        ),
        "change_it": "plan_event in dsr/change_stream/replica.py.",
        "blast_radius": "Which transactions commit, and what a room is told when one cannot.",
    },
    {
        "id": "enrichment-on-the-standard-channel-is-refused",
        "topic": "whether configuring enrichment on /data/ChangeEvents is a warning or a refusal",
        "basis": (
            "The research recommends against it: 'We recommend that you configure event "
            "enrichment on a custom channel and not the standard /data/ChangeEvents channel. "
            "This way, other subscribers that receive change events on the standard channel "
            "don't receive unchanged fields that they don't expect.' A recommendation, "
            "against a harm this product cannot see."
        ),
        "value": {
            "standard_channel": "refused, 409",
            "custom_channel": "accepted",
            "enrichment_on_a_case_variant": "also refused: the standard channel is identified by its name, folded for this check only",
        },
        "why": (
            "The harm named in the documentation lands on *other subscribers of the standard "
            "channel*. This product cannot enumerate them, so nobody here can weigh the cost "
            "of a warning against the harm of ignoring one - and the parties who would be "
            "harmed never see this room's UI. A refusal is the only answer available to a "
            "party that cannot see the other parties. The researched remedy is built instead "
            "of just quoted: create a custom channel and enrich that, which the same sentence "
            "describes and which the API now does."
        ),
        "change_it": "add_enrichment in dsr/change_stream/channels.py.",
        "blast_radius": "Which channels can carry enriched fields.",
    },
    {
        "id": "enrichment-applies-to-update-and-delete-only",
        "topic": "what to do with enriched fields on a create or undelete event",
        "basis": (
            'Fully sourced: "Fields that you select for enrichment are included in change '
            "events for update and delete operations. Enriched fields aren't included in "
            "change events for create and undelete operations because these events contain "
            'all the populated fields."'
        ),
        "value": {
            "enriched_change_types": list(vocabulary.ENRICHED_CHANGE_TYPES),
            "ignored_change_types": list(vocabulary.UNENRICHED_CHANGE_TYPES),
            "ignored_fields": "dropped, with the reason reported on the event",
            "resolution": "the payload wins, always",
        },
        "why": (
            "The research states the rule; the only judgement is what to do with a create "
            "event that arrives carrying enriched fields anyway, which the vendor's own "
            "sentence says cannot happen. Dropping them is the safe reading, because a "
            "create event 'contains all the populated fields' and letting a stale enriched "
            "copy win would overwrite the complete record with a partial one. Dropping is "
            "reported rather than silent so a transport that does send them is visible."
        ),
        "change_it": "enriched_fields_in_effect in dsr/change_stream/events.py.",
        "blast_radius": "What a create or undelete event writes to the replica.",
    },
    {
        "id": "enrichment-transport-set",
        "topic": "which transports may be enriched",
        "basis": (
            'Sourced and closed: "Event enrichment is supported for subscribers that use '
            'Pub/Sub API, CometD (Streaming API), or event relays." A delta-link poll and a '
            "workflow webhook are not in that list."
        ),
        "value": {
            "supported": list(vocabulary.ENRICHMENT_TRANSPORTS),
            "refused": "any other transport, 400, naming the three",
        },
        "why": (
            "The list is closed in the research, so this build does not widen it. Refusing is "
            "the reading that makes the closed list mean something - a delta-link subscriber "
            "that asked for an enriched field would get nothing and no way to tell."
        ),
        "change_it": "ENRICHMENT_TRANSPORTS in dsr/change_stream/vocabulary.py.",
        "blast_radius": "Which channels can carry enriched fields at all.",
    },
    {
        "id": "channel-name-is-case-sensitive",
        "topic": "whether /data/ChangeEvents and /data/changeevents are one channel or two",
        "basis": 'Sourced, in one clause: "The channel name is case-sensitive."',
        "value": {
            "resolution": "exact, byte for byte",
            "collision_check": "case-sensitive, so the two names are two channels",
            "is_standard_channel": "case-folded, and only to decide the enrichment rule",
        },
        "why": (
            "The consequence is that this API will happily let a room create a channel named "
            "/data/changeevents beside /data/ChangeEvents. That is what the vendor's rule "
            "says, and the only place case is folded is the check that identifies the "
            "standard channel - because a room that created /data/changeevents expecting the "
            "standard channel and then enriched it would harm the standard channel's "
            "subscribers without ever naming it."
        ),
        "change_it": "channel_name_taken and vocabulary.is_standard_channel.",
        "blast_radius": "Which channels collide, and which the enrichment rule protects.",
    },
    {
        "id": "delete-is-a-tombstone",
        "topic": "what the room's replica does with a deleted CRM record",
        "basis": (
            "The research says Change Data Capture fires on delete and undelete and that the "
            "room commits to 'a local replica'. It does not say whether the replica row is "
            "removed, kept, or marked."
        ),
        "value": {
            "delete": "the row becomes a tombstone, keeping its sync key and account",
            "undelete": "the tombstone is found and restored from the event",
            "hard_delete": "never",
        },
        "why": (
            "A hard delete would make UNDELETE unresolvable: an undelete event is a row coming "
            "back, and a replica with no row to match it against would have to guess. The "
            "research guarantees undelete events exist, so the room has to be able to receive "
            "one, and a tombstone is the cheapest way to be able to. It also answers a "
            "question a room actually asks - which records has the CRM taken away."
        ),
        "change_it": "plan_event and merge_into in dsr/change_stream/replica.py.",
        "blast_radius": "The replica states, and what an undelete restores.",
    },
    {
        "id": "changed-fields-is-not-cleared",
        "topic": "what an update event does to a field it does not mention",
        "basis": (
            "The research says an update event carries changedFields, and separately that a "
            "field the room needs but the change did not touch is an 'unchanged but needed' "
            "gap. It does not spell out the consequence."
        ),
        "value": {
            "changed_fields_absent": "every mapped field in the payload is applied",
            "changed_fields_empty": "nothing is applied; the transport said nothing changed",
            "field_not_listed": "the replica keeps the value it already has",
        },
        "why": (
            "The alternative - treating an unlisted field as absent and writing null - is "
            "data loss caused by the vendor's own size optimisation, which is the whole "
            "reason enrichment exists as a remedy. Absent and empty are kept apart because "
            "they mean different things: a transport that sends no list is saying nothing, "
            "and one that sends an empty list is saying nothing changed."
        ),
        "change_it": "apply_field_map in dsr/change_stream/fieldmap.py.",
        "blast_radius": "Every replica write from an update event.",
    },
    {
        "id": "pubsub-fetch-budget",
        "topic": "what happens to an event delivered with no FetchRequest outstanding",
        "basis": (
            'Sourced: "The Subscribe method uses bidirectional streaming, enabling the client '
            "to request more events as it consumes events. The client can control the flow of "
            "events received by setting the number of requested events in the FetchRequest "
            'parameter."'
        ),
        "value": {
            "a_subscription_starts_with": "nothing outstanding",
            "delivery_requires": "an outstanding request",
            "without_one": "refused, 409 no_outstanding_fetch_request",
            "counters": "events_requested and events_delivered are kept apart",
        },
        "why": (
            "The flow control is the point of a bidirectional stream: a client asks for as "
            "much as it wants and asks again as it consumes. An event arriving with nothing "
            "outstanding is an event the subscriber was not authorised to receive, and "
            "accepting it would make the requested-versus-delivered counters on /usage a lie. "
            "The research publishes no default fetch size, so there is none; a subscription "
            "that wants events sends a FetchRequest first."
        ),
        "change_it": "NoOutstandingFetchRequest in errors.py, and request_fetch in usage.py.",
        "blast_radius": "Which events a subscription can receive, and the usage counters.",
    },
    {
        "id": "buffer-bytes-default-3mb",
        "topic": "the default Pub/Sub buffer size, and what a deviation means",
        "basis": 'Sourced: "We recommend you set the buffer size to 3 MB", and "Pub/Sub buffer sizing is also tunable."',
        "value": {
            "default_bytes": vocabulary.RECOMMENDED_BUFFER_BYTES,
            "deviation": "permitted, and reported on the channel and on /usage",
            "recommendation_is": "a recommendation, so it produces a note and not a refusal",
        },
        "why": (
            "The research says both recommend and tunable, and a rule that refused a value "
            "the vendor calls tunable would be this build's opinion rather than the "
            "research's. The default is the recommendation so a room that says nothing gets "
            "the vendor's number, and a room that chooses differently is visible rather than "
            "silently permitted."
        ),
        "change_it": "RECOMMENDED_BUFFER_BYTES in dsr/change_stream/vocabulary.py.",
        "blast_radius": "The default buffer, and the recommendation flag on every channel.",
    },
    {
        "id": "room-panel-refresh",
        "topic": "how the affected buyer's deal panel is identified from a change event",
        "basis": (
            "The user flow says 'The room refreshes the affected buyer's deal panel' and no "
            "more. It does not say where the buyer comes from, and this product's rooms do "
            "not map buyers to CRM records by anything but a field a room declares."
        ),
        "value": {
            "resolution": "an account_field the channel's field_map may declare",
            "unresolved_when": "the field map declares no account_field",
            "reported_as": "resolved: false on the invalidation, never invented",
            "every_change_type_refreshes": True,
        },
        "why": (
            "An invalidation addressed to nobody is a bug that should be visible, and "
            "picking a default buyer would put a real buyer's deal change in front of the "
            "wrong rep. Reporting resolved: false is honest and costs a room one field "
            "declaration to fix; guessing is free until it is wrong."
        ),
        "change_it": "fieldmap.apply_field_map, and the invalidation written in engine.py.",
        "blast_radius": "The account on every invalidation, and the deal panel's grouping.",
    },
    {
        "id": "unmapped-crm-fields-are-recorded",
        "topic": "what happens to a CRM field the channel's field map does not name",
        "basis": (
            "The research says the event payload is mapped 'through the same field map' and "
            "says nothing about fields outside it. The product's standing rule points the "
            "other way: a team adding a field must not need coordination with anyone."
        ),
        "value": {
            "dropped": False,
            "recorded_on": "the replica row and the invalidation, as unmapped_crm_fields",
            "refused": "nothing",
        },
        "why": (
            "Silently dropping a field a team just added in the CRM is how a room stops "
            "agreeing with the CRM without anyone noticing. Recording the names costs "
            "nothing, is queryable through the dynamic index, and makes the gap visible "
            "before the team has decided where the field belongs."
        ),
        "change_it": "unmapped_crm_fields in dsr/change_stream/fieldmap.py.",
        "blast_radius": "The unmapped_crm_fields list on every replica row.",
    },
    {
        "id": "hubspot-rate-limit-budget",
        "topic": "the budget a workflow webhook call is exempt from",
        "basis": (
            'Sourced: "Webhook calls made via workflows do not count towards the API rate '
            'limit." The research publishes no limit to be exempt from, and no other '
            "vendor's budget either."
        ),
        "value": {
            "exempt": True,
            "default_budget": vocabulary.DEFAULT_API_CALL_BUDGET,
            "counters": "spent, and exempt calls counted beside it so the exemption is auditable",
        },
        "why": (
            "The exemption is the research's; the budget is not, and it is declared here "
            "rather than left implicit so the exemption has something to be an exemption "
            "from. Reporting the exempt calls as well as the charged ones is the point: a "
            "usage report showing only the remaining budget would make the exemption "
            "invisible, and an invisible exemption cannot be audited."
        ),
        "change_it": "charge_or_exempt and DEFAULT_API_CALL_BUDGET.",
        "blast_radius": "The HubSpot usage report, and the call budget on every webhook target.",
    },
    {
        "id": "edition-is-declared-not-detected",
        "topic": "where an org's Salesforce edition comes from",
        "basis": (
            'Sourced: CDC is "Available in: Enterprise, Performance, Unlimited, and Developer '
            "editions.\" The research says nothing about how a room learns its org's edition."
        ),
        "value": {
            "source": "declared by the caller when the org is registered",
            "verified": "not verified against the vendor",
            "gate": "a non-listed edition cannot enable Change Data Capture, 409",
        },
        "why": (
            "This product holds no OAuth connection to a Salesforce org - that is a different "
            "researched workflow - so it cannot ask. A declared value that is wrong enables "
            "nothing that would not have worked anyway, and a real org on Professional fails "
            "at the vendor rather than here. Refusing beats guessing an edition, because a "
            "room that believes it is on Unlimited and is not finds out from the vendor."
        ),
        "change_it": "edition_supports_cdc in vocabulary.py, and the org registration route.",
        "blast_radius": "Who may enable Change Data Capture, and the refusal message.",
    },
    {
        "id": "avro-is-the-transport-clients-job",
        "topic": "whether this package decodes Avro",
        "basis": (
            "A boundary, not a judgement call. The data flow names the wire formats once - "
            "'subscriber deserialisation (Avro for Pub/Sub, JSON for CometD)' - and publishes "
            "no schema, no encoding, and no framing."
        ),
        "value": {
            "records": "the wire format per transport, on the subscription and on the event",
            "accepts": "a decoded change event, in the six fields the research names",
            "does_not": "decode Avro, or re-frame a gRPC stream",
        },
        "why": (
            "Deserialisation is the transport client's job, and the research describes the "
            "boundary rather than the format - it names which format each transport uses and "
            "nothing that would let this package parse one. Recording the wire format per "
            "transport is what the sentence supports, and it is enough for a client to know "
            "what it must hand over. Writing an Avro decoder here would be an invented "
            "contract, and an invented decoder that silently mis-parses a change is worse than "
            "none."
        ),
        "change_it": "WIRE_FORMATS in dsr/change_stream/vocabulary.py.",
        "blast_radius": "Nothing in this package parses a wire format; it is reported data.",
    },
    {
        "id": "no-gap-reconciliation",
        "topic": "why a gap in sequenceNumber is reported and not repaired",
        "basis": (
            "A boundary, not a judgement call. Section 18 of the same research file is "
            '"Reconcile gaps and overflows after a dropped change stream", and this workflow '
            "is section 10."
        ),
        "value": {
            "on_a_gap": "reported on the buffer as sequence_gaps",
            "repaired": False,
            "overflow_markers": "refused: not one of the four researched change types",
        },
        "why": (
            "Building the repair here would implement another ticket's specification inside "
            "this one, and would make this feature's audit log name reconciliations that the "
            "page above it never offered. Refusing the vendor's gap and overflow markers is "
            "the other half of the same decision: the research's change-type vocabulary is "
            "four words, and a fifth would be a change type with no rule behind it."
        ),
        "change_it": "sequence_gaps in buffering.py, and CHANGE_TYPES in vocabulary.py.",
        "blast_radius": "What a gap does, and which change types are accepted.",
    },
    {
        "id": "no-hubspot-subscription-rest",
        "topic": "why there is no HubSpot webhook subscription REST surface",
        "basis": (
            "A boundary, and the research's own gap: the webhook subscriptions REST guide "
            "page is client-rendered and its body could not be read, so 'the subscription "
            "endpoints/methods are therefore not claimed.'"
        ),
        "value": {
            "workflow_webhook": "built - it is the only HubSpot seam the research claims",
            "subscription_endpoints": "not built",
            "subscription_methods": "not built",
        },
        "why": (
            "The research is explicit that this half could not be read, and an invented REST "
            "contract for a vendor integration is exactly the sort of thing that looks right "
            "and is wrong. The cap and the rate-limit fact are built because they were "
            "read, and the cap needs a place to be counted."
        ),
        "change_it": "Nothing to revert; the entry exists so the absence reads as a decision.",
        "blast_radius": "Nothing in this feature.",
    },
    {
        "id": "entity-is-filtered-not-relabelled",
        "topic": "what happens to an event for an entity the channel does not stream",
        "basis": (
            'Sourced: "A subscription channel is a stream of change events that correspond to '
            'one or more entities." The research does not say what a subscriber does when an '
            "entity arrives that the channel does not list."
        ),
        "value": {
            "on_a_foreign_entity": "refused, 409 entity_not_on_channel",
            "dropped": False,
            "when_the_channel_lists_nothing": "no check applies",
        },
        "why": (
            "The three ways to handle a foreign entity are to apply it, to drop it, and to "
            "refuse it. Applying it writes to a replica row whose field map was never meant "
            "for that object. Dropping it is a silent loss - and the research's whole promise "
            "is that the replica stays current. Refusing names the mismatch, which is the one "
            "outcome that lets somebody fix the channel."
        ),
        "change_it": "applies_to_entity in dsr/change_stream/events.py.",
        "blast_radius": "Which events a channel can receive.",
    },
    {
        "id": "usage-metric-shape",
        "topic": "the shape of PlatformEventUsageMetric",
        "basis": (
            'The data sources list names it once - "PlatformEventUsageMetric for delivery '
            'usage" - and publishes no fields.'
        ),
        "value": {
            "source": "PlatformEventUsageMetric",
            "schema_published": False,
            "counters": "served at GET /usage and described at GET /vocabulary",
        },
        "why": (
            "A metric with no fields is not usable, and inventing fields in a comment is the "
            "same invisibility this file exists to prevent. Served as data with the sourced "
            "sentence beside it, so the shape can be disagreed with by name."
        ),
        "change_it": "EMPTY_USAGE in dsr/change_stream/usage.py.",
        "blast_radius": "The /usage report and the counters on every subscription.",
    },
)


def by_id(inference_id: str) -> dict[str, Any] | None:
    """One entry by id, or ``None``."""
    for entry in INFERENCES:
        if entry["id"] == inference_id:
            return dict(entry)
    return None


def describe() -> dict[str, Any]:
    """The whole register, alongside the half of the workflow that is sourced.

    Both halves in one payload on purpose. The point of this endpoint is that a
    reader can see where the line falls, and showing the quoted mechanics next to
    the inferred edges is what lets them.
    """
    return {
        "count": len(INFERENCES),
        "sourced": {
            "buffering_quote": SOURCED_QUOTE,
            "enrichment_quote": ENRICHMENT_QUOTE,
            "change_types": list(vocabulary.CHANGE_TYPES),
            "standard_channel": vocabulary.STANDARD_CHANNEL,
            "channel_name_is_case_sensitive": True,
            "enrichment_transports": list(vocabulary.ENRICHMENT_TRANSPORTS),
            "enriched_change_types": list(vocabulary.ENRICHED_CHANGE_TYPES),
            "unenriched_change_types": list(vocabulary.UNENRICHED_CHANGE_TYPES),
            "cdc_editions": list(vocabulary.CDC_EDITIONS),
            "recommended_buffer_bytes": vocabulary.RECOMMENDED_BUFFER_BYTES,
            "delta_query_options_refused": list(
                vocabulary.UNSUPPORTED_DELTA_QUERY_OPTIONS.values()
            ),
            "delta_query_option_message": vocabulary.UNSUPPORTED_DELTA_QUERY_OPTION_MESSAGE,
            "change_tracking_annotation": vocabulary.CHANGE_TRACKING_ANNOTATION,
            "hubspot_workflow_calls_exempt": (
                vocabulary.HUBSPOT_WORKFLOW_CALLS_EXEMPT_FROM_RATE_LIMIT
            ),
            "hubspot_webhook_subscription_limit": (vocabulary.HUBSPOT_WEBHOOK_SUBSCRIPTION_LIMIT),
        },
        "worked_example": {
            "note": (
                "the two-event stream the research's buffering rule produces, end to end, with "
                "no writes: two changes in one transaction park, and the arrival of a third "
                "under a new key commits them both."
            ),
            "sync_key": "External_Id__c",
            "sync_key_field": DEFAULT_SYNC_KEY_FIELD,
            "events": [
                {
                    "changeType": "UPDATE",
                    "transactionKey": "txn-A",
                    "sequenceNumber": 1,
                    "commitTimestamp": "2026-09-26T12:00:00+00:00",
                    "changedFields": ["StageName"],
                    "payload": {"StageName": "Negotiation"},
                },
                {
                    "changeType": "UPDATE",
                    "transactionKey": "txn-A",
                    "sequenceNumber": 2,
                    "commitTimestamp": "2026-09-26T12:00:01+00:00",
                    "changedFields": ["Amount"],
                    "payload": {"Amount": 48000},
                },
                {
                    "changeType": "UPDATE",
                    "transactionKey": "txn-B",
                    "sequenceNumber": 3,
                    "commitTimestamp": "2026-09-26T12:00:02+00:00",
                    "changedFields": ["CloseDate"],
                    "payload": {"CloseDate": "2026-11-30"},
                },
            ],
            "buffered_after_each": [
                {"after_event": 1, "parked": ["txn-A#1"], "committed": []},
                {"after_event": 2, "parked": ["txn-A#1", "txn-A#2"], "committed": []},
                {
                    "after_event": 3,
                    "parked": ["txn-B#3"],
                    "committed": ["txn-A#1", "txn-A#2"],
                    "reason": "transactionKey changed",
                },
            ],
            "event_1_resolves_its_record": (
                "not from the payload - it carries only StageName, which changed - but from "
                f"the {DEFAULT_SYNC_KEY_FIELD} the channel carries as an enriched field, which "
                "is exactly the case the research's enrichment rule is written for."
            ),
        },
        "inferences": [dict(entry) for entry in INFERENCES],
    }
