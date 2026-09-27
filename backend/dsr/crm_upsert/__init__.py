"""WF-038: batch-upsert engagement rows keyed on the external ID.

The domain behind the researched workflow. The research document is the
specification:
``docs/research/digital-sales-room-workflows/wf/WF-038.md``
(source: ``docs/research/raw/crm-integration.md`` section 5).

The researched flow, as the spec states it
------------------------------------------
1. The room's queue accumulates up to 200 pending engagement rows (or the nightly
   backlog).
2. Admin-triggered **Sync now** (or the scheduled job) opens **Sync -> Run
   upsert**.
3. The connector chunks rows into batches (200 for Salesforce collections, 100
   for HubSpot) and sends one upsert per chunk, keyed on the sync key chosen in
   W2.
4. Each row either **updates** the existing CRM record (key found) or **creates**
   a new one (key not found).
5. Per-row outcomes are written back to the room; failures appear in the sync log
   with the row's error text.

Sourced behaviour this package implements
-----------------------------------------
* **The caps are per request and per vendor.** "The list can contain up to 200
  objects" (Salesforce) and "Batch operations are limited to 100 records at a
  time" (HubSpot). Enforced, not clamped: a connection asking for more is
  refused, because a silent clamp would report a plan the connector did not run.
  See :func:`dsr.crm_upsert.capabilities.resolve_batch_size`.
* **The payload carries the external-ID field and no ``id`` field.** Checked
  twice - on the field map when a connection is saved, and again on the built
  request body, because the room's own ``crm_record_id`` write-back is one
  reflection away from being echoed into the next request and turning the upsert
  into an update-by-record-id. See
  :func:`dsr.crm_upsert.payloads._assert_no_record_id`.
* **Only external ids are supported. Don't use record ids.** A key field that is
  a record id is refused on save, before a sync has to discover it.
* **One object type per request.** "The list can contain objects only of the type
  indicated in the request URI."
* **Results come back in request order.** "Objects are created or updated in the
  order they're listed in the request body. The `UpsertResult` objects are
  returned in the same order." So outcomes are matched by position and a
  wrong-length response fails the chunk rather than truncating it.
* **A duplicate key is an error, not a second write.** "If the external ID
  matches multiple existing records, then a 300 error is returned, and no records
  are created or updated."
* **``updateOnly`` prevents the create.** "If the external ID doesn't match an
  existing record, then a new record is created according to the request body. To
  prevent a new record from being created, use the `updateOnly` parameter."
* **``allOrNone`` is about the whole request.** "You can choose whether to roll
  back the entire request when an error occurs." One failed item means nothing in
  that chunk was written, so nothing in it is written back as synced.
* **HubSpot will not take a partial upsert by email.** "Partial upserts are not
  supported when using `email` as the `idProperty` for contacts." Rows that
  cannot supply a complete property set are refused rather than sent, because the
  vendor would treat the missing properties as empty.
* **Dataverse confirms nothing.** "The `UpsertMultiple` action returns
  `204 NoContent`", so those rows are recorded as submitted, not synced. See
  :mod:`dsr.crm_upsert.inferences`.
* **The fallback is automatic.** "A third party can register a vendor-specific
  'bulk capability' (max batch size, supported key types) and the scheduler
  adapts - e.g. it auto-falls back from `UpsertMultiple` to per-row `PATCH` for
  tables that don't support bulk upsert." The registry is
  :mod:`dsr.crm_upsert.capabilities`, reachable both in-process and as a stored
  record.
* **The two triggers.** "Nightly/backlog upsert runs on the room's scheduler;
  the room may also upsert opportunistically when the queue exceeds N rows."
  :func:`dsr.crm_upsert.connections.backlog`.
* **Nothing runs inside the CRM.** "Nothing runs inside the CRM in this
  workflow", so a run writes to the room's rows and its own log and nothing else.

Schema flexibility
------------------
Everything this package stores is ordinary JSON in ``records.data``. The
engagement table it reads is named in the config rather than hard-coded, field
locations are named per connection rather than assumed, and a team that adds a
field to its engagement rows adds one line to a connection's field map. No
migration, no typed column, and a connection may carry keys this package has
never heard of.
"""

from __future__ import annotations

from dsr.crm_upsert.capabilities import (
    BUILT_IN,
    COLLECTION_CAPABILITY,
    DATAVERSE,
    HUBSPOT,
    KEY_TYPES,
    LEGACY_TABLE,
    RECORD_ID_FIELDS,
    SALESFORCE,
    Capability,
    catalogue,
    is_record_id_field,
    register_bulk_capability,
    resolve_batch_size,
    resolve_capability,
    resolve_connection_key_type,
    resolve_key_type,
    resolve_mode,
    supported_vendors,
    unregister_bulk_capability,
)
from dsr.crm_upsert.connections import (
    COLLECTION_CONNECTION,
    COLLECTION_ENGAGEMENT,
    COLLECTION_RUN,
    DEFAULT_CONFIG,
    QUEUE_STATUSES,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_SYNCED,
    STATUS_UNCONFIRMED,
    UNSENT_STATUSES,
    Connection,
    backlog,
    connection_state,
    delete_connection,
    lint,
    list_connections,
    load_config,
    load_connection,
    pending_rows,
    queue_view,
    row_status,
    save_config,
    save_connection,
    validate_connection,
)
from dsr.crm_upsert.errors import (
    BatchTooLarge,
    MixedObjectTypes,
    NoUpsertPath,
    UnknownConnection,
    UnknownRun,
    UnsupportedKey,
    UpsertError,
)
from dsr.crm_upsert.inferences import INFERENCES, describe as describe_inferences
from dsr.crm_upsert.payloads import (
    OUTCOMES,
    REJECTION_REASONS,
    RowRejected,
    build_bulk_request,
    build_payload,
    build_single_request,
    key_value,
    mapped_fields,
    preflight_row,
)
from dsr.crm_upsert.runs import (
    DRIVERS,
    WRITTEN_COLLECTIONS,
    RowOutcome,
    RunResult,
    chunk,
    list_runs,
    load_run,
    preview,
    run_upsert,
)
from dsr.crm_upsert.transport import (
    OutboundRequest,
    OutboundResponse,
    RecordingTransport,
    ScriptedTransport,
    SimulatedTransport,
    Transport,
    dataverse_upsert_multiple,
    hubspot_upsert_results,
    salesforce_error,
    salesforce_single,
    salesforce_upsert_results,
)

__all__ = [
    # capabilities
    "BUILT_IN",
    "CAPABILITY",
    "Capability",
    "DATAVERSE",
    "HUBSPOT",
    "KEY_TYPES",
    "LEGACY_TABLE",
    "RECORD_ID_FIELDS",
    "SALESFORCE",
    "catalogue",
    "is_record_id_field",
    "register_bulk_capability",
    "resolve_batch_size",
    "resolve_capability",
    "resolve_connection_key_type",
    "resolve_key_type",
    "resolve_mode",
    "supported_vendors",
    "unregister_bulk_capability",
    # collections
    "COLLECTION_CAPABILITY",
    "COLLECTION_CONNECTION",
    "COLLECTION_ENGAGEMENT",
    "COLLECTION_RUN",
    # connections and config
    "Connection",
    "DEFAULT_CONFIG",
    "QUEUE_STATUSES",
    "STATUS_FAILED",
    "STATUS_PENDING",
    "STATUS_SYNCED",
    "STATUS_UNCONFIRMED",
    "UNSENT_STATUSES",
    "backlog",
    "connection_state",
    "delete_connection",
    "lint",
    "list_connections",
    "load_config",
    "load_connection",
    "pending_rows",
    "queue_view",
    "row_status",
    "save_config",
    "save_connection",
    "validate_connection",
    # errors
    "BatchTooLarge",
    "MixedObjectTypes",
    "NoUpsertPath",
    "UnknownConnection",
    "UnknownRun",
    "UnsupportedKey",
    "UpsertError",
    # payloads
    "OUTCOMES",
    "REJECTION_REASONS",
    "RowRejected",
    "build_bulk_request",
    "build_payload",
    "build_single_request",
    "key_value",
    "mapped_fields",
    "preflight_row",
    # runs
    "DRIVERS",
    "WRITTEN_COLLECTIONS",
    "RowOutcome",
    "RunResult",
    "chunk",
    "list_runs",
    "load_run",
    "preview",
    "run_upsert",
    # transport
    "OutboundRequest",
    "OutboundResponse",
    "RecordingTransport",
    "ScriptedTransport",
    "SimulatedTransport",
    "Transport",
    "dataverse_upsert_multiple",
    "hubspot_upsert_results",
    "salesforce_error",
    "salesforce_single",
    "salesforce_upsert_results",
    # inferences
    "INFERENCES",
    "describe_inferences",
    "CAPABILITY",
]

#: The collection name is both a constant and a default. Kept as one alias so a
#: caller that has only the package can reach it without importing a submodule.
CAPABILITY = COLLECTION_CAPABILITY
