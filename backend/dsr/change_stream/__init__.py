"""WF-043: stream CRM record changes into the room in near real time.

The change-stream domain for the Digital Sales Room, kept in its own package so
no two features claim one path. This is the machinery behind a Change Data
Capture stream: a channel, a long-lived subscription, a per-transaction buffer,
the room's local replica, and the three vendor mechanisms the research names
beside Salesforce - Dataverse's delta link and HubSpot's workflow webhook.

The module layout, and why each piece is separate:

``vocabulary``  the values the research fixes by name, served as data
``errors``      one hierarchy, so the feature module exports one handler
``events``      normalising one change event from whatever the transport sent
``fieldmap``    the transformation, and the "unchanged but needed" gap
``channels``    channel names, the standard-channel rule, and enrichment
``buffering``   the commit rule, and the sequence order inside a transaction
``replica``     what each of the four change types does to the room's replica
``dataverse``   the one-way Track changes property, and the delta-link poll
``hubspot``     the workflow webhook, the 1,000 cap, the rate-limit exemption
``usage``       PlatformEventUsageMetric, for delivery usage
``inferences``  every judgement call, named and served
``engine``      the façade the HTTP layer calls; owns the eight collections

:data:`dsr.change_stream.inferences.SOURCED_QUOTE` is the sentence from the
research that governs the buffering behaviour, and
:func:`dsr.change_stream.inferences.describe` is served at the feature's
``/inferences`` route so a reviewer can see which parts are sourced and which are
this build's judgement without reading the diff.
"""

from __future__ import annotations

from dsr.change_stream.buffering import BufferSet, TransactionBuffer, sequence_gaps
from dsr.change_stream.channels import add_enrichment, normalise_channel, remove_enrichment
from dsr.change_stream.dataverse import change_count, normalise_table, poll
from dsr.change_stream.engine import (
    CHANGE_EVENTS,
    CHANNELS,
    HUBSPOT_SUBSCRIPTIONS,
    INVALIDATIONS,
    ORGS,
    OWNED_COLLECTIONS,
    REPLICA,
    SUBSCRIPTIONS,
    TABLES,
    ChangeStreamEngine,
)
from dsr.change_stream.errors import (
    CdcNotEnabled,
    ChangeStreamError,
    ChangeTrackingDisabled,
    ChangeTrackingIrreversible,
    ChannelError,
    ChannelInUse,
    DataverseError,
    DuplicateChannelName,
    DuplicateOrg,
    EditionDoesNotSupportCdc,
    EntityNotOnChannel,
    EventError,
    FieldMapError,
    MalformedEvent,
    MissingTrackChangesPreference,
    MissingTransactionKey,
    NoOutstandingFetchRequest,
    OrgError,
    RecordUnresolvable,
    ReplicaWriteError,
    StandardChannelEnrichmentRefused,
    SubscriptionClosed,
    SubscriptionError,
    SubscriptionLimitExceeded,
    UnknownChangeType,
    UnknownChannel,
    UnknownOrg,
    UnknownSubscription,
    UnknownTable,
    UnsupportedDeltaQueryOption,
    UnsupportedEnrichmentTransport,
)
from dsr.change_stream.events import (
    applies_to_entity,
    enriched_fields_in_effect,
    normalise_event,
    parse_sequence_number,
    parse_timestamp,
)
from dsr.change_stream.fieldmap import (
    apply_field_map,
    field_map_findings,
    normalise_field_map,
    resolve_external_id,
    sync_key_field,
    unmapped_crm_fields,
)
from dsr.change_stream.replica import deal_panel_row, merge_into, plan_event, replica_findings
from dsr.change_stream.usage import describe as describe_usage
from dsr.change_stream.vocabulary import (
    CDC_EDITIONS,
    CHANGE_TRACKING_ANNOTATION,
    CHANGE_TRACKING_PREFERENCE,
    CHANGE_TYPES,
    CHANNEL_KINDS,
    CRM_SYSTEMS,
    DATAVERSE_API_VERSION,
    DEAL_PANEL,
    ENRICHED_CHANGE_TYPES,
    ENRICHMENT_TRANSPORTS,
    EVENT_STATES,
    HUBSPOT_WEBHOOK_SUBSCRIPTION_LIMIT,
    HUBSPOT_WORKFLOW_CALLS_EXEMPT_FROM_RATE_LIMIT,
    RECOMMENDED_BUFFER_BYTES,
    REPLICA_STATES,
    STANDARD_CHANNEL,
    SUBSCRIPTION_STATES,
    TRANSPORTS,
    UNENRICHED_CHANGE_TYPES,
    UNSUPPORTED_DELTA_QUERY_OPTION_MESSAGE,
    UNSUPPORTED_DELTA_QUERY_OPTIONS,
    edition_supports_cdc,
    is_enriched_change_type,
    is_standard_channel,
    normalise_change_type,
    supports_enrichment,
    wire_format_for,
)

__all__ = [
    "CDC_EDITIONS",
    "CHANGE_EVENTS",
    "CHANGE_TRACKING_ANNOTATION",
    "CHANGE_TRACKING_PREFERENCE",
    "CHANGE_TYPES",
    "CHANNELS",
    "CHANNEL_KINDS",
    "CRM_SYSTEMS",
    "CdcNotEnabled",
    "DATAVERSE_API_VERSION",
    "DEAL_PANEL",
    "DataverseError",
    "ChangeStreamEngine",
    "ChangeStreamError",
    "ChangeTrackingDisabled",
    "ChangeTrackingIrreversible",
    "ChannelError",
    "ChannelInUse",
    "DuplicateChannelName",
    "DuplicateOrg",
    "ENRICHED_CHANGE_TYPES",
    "ENRICHMENT_TRANSPORTS",
    "EVENT_STATES",
    "EditionDoesNotSupportCdc",
    "EntityNotOnChannel",
    "EventError",
    "FieldMapError",
    "HUBSPOT_SUBSCRIPTIONS",
    "HUBSPOT_WEBHOOK_SUBSCRIPTION_LIMIT",
    "HUBSPOT_WORKFLOW_CALLS_EXEMPT_FROM_RATE_LIMIT",
    "INVALIDATIONS",
    "MalformedEvent",
    "MissingTrackChangesPreference",
    "MissingTransactionKey",
    "NoOutstandingFetchRequest",
    "ORGS",
    "OWNED_COLLECTIONS",
    "OrgError",
    "RECOMMENDED_BUFFER_BYTES",
    "REPLICA",
    "REPLICA_STATES",
    "RecordUnresolvable",
    "ReplicaWriteError",
    "STANDARD_CHANNEL",
    "SUBSCRIPTIONS",
    "SUBSCRIPTION_STATES",
    "StandardChannelEnrichmentRefused",
    "SubscriptionClosed",
    "SubscriptionError",
    "SubscriptionLimitExceeded",
    "TABLES",
    "TRANSPORTS",
    "TransactionBuffer",
    "UNENRICHED_CHANGE_TYPES",
    "UNSUPPORTED_DELTA_QUERY_OPTIONS",
    "UNSUPPORTED_DELTA_QUERY_OPTION_MESSAGE",
    "UnknownChangeType",
    "UnknownChannel",
    "UnknownOrg",
    "UnknownSubscription",
    "UnknownTable",
    "UnsupportedDeltaQueryOption",
    "UnsupportedEnrichmentTransport",
    "BufferSet",
    "add_enrichment",
    "applies_to_entity",
    "apply_field_map",
    "change_count",
    "deal_panel_row",
    "describe_usage",
    "edition_supports_cdc",
    "enriched_fields_in_effect",
    "field_map_findings",
    "is_enriched_change_type",
    "is_standard_channel",
    "merge_into",
    "normalise_change_type",
    "normalise_channel",
    "normalise_event",
    "normalise_field_map",
    "normalise_table",
    "parse_sequence_number",
    "parse_timestamp",
    "plan_event",
    "poll",
    "replica_findings",
    "remove_enrichment",
    "resolve_external_id",
    "sequence_gaps",
    "supports_enrichment",
    "sync_key_field",
    "unmapped_crm_fields",
    "wire_format_for",
]
