"""WF-037: log a single buyer engagement event into the CRM.

Built from ``docs/research/digital-sales-room-workflows/wf/WF-037.md``, which is the
specification. The researched decisions are the product: the two-stage write (the room's
own row first, the CRM write enqueued), the worker's resolution of the buyer's CRM record
from the field mapping and sync key, three genuinely different create surfaces, and three
genuinely different answers to "how do I know it worked, and what is the new row's id".

Seven modules, each with one job:

``errors``
    The five refusals this workflow makes, under one domain base type.
``vocabulary``
    The researched contract as data: the three create endpoints, their success codes, where
    each returns an id, the ``Prefer`` tokens, the queue states, the block and failure
    reasons, and the quoted sentences all of it is measured against.
``mapping``
    The named, versioned transforms and the field-map application. Read a room event's own
    fields, translate them, and say what could not be translated.
``payloads``
    Per-vendor request shaping and id extraction.
``delivery``
    The transport, the retry ladder, and the per-attempt record the Sync log reads.
``queue``
    Every record this workflow writes and every state it can be in.
``engine``
    The five researched steps, as a plan and an execute.
``inferences``
    Every decision the research does not make, named and served at ``/inferences``.

Schema flexibility
------------------
No migration, no typed column, no new required field. A room engagement event is whatever
the room recorded, stored verbatim; a field map row is arbitrary JSON naming whatever the
CRM calls its properties; and a new event type is a row rather than a code path, which is
the extensibility the research promises.

``source`` on every write
-------------------------
Every method that writes takes a **required keyword-only** ``source``, and the routes
pass the route that served the request, built from ``router.prefix``. Hardcoding a source
string in a domain function puts a path in the audit log that the app might have stopped
serving, and that class of bug has shipped in this codebase before.
"""

from __future__ import annotations

from dsr.crm_engagement import delivery, inferences, mapping, payloads, queue, vocabulary
from dsr.crm_engagement.delivery import (
    CreateReport,
    CreateResult,
    Transport,
    UrllibTransport,
    post_create,
)
from dsr.crm_engagement.engine import EngagementSync, Plan
from dsr.crm_engagement.errors import (
    EngagementSyncError,
    InvalidConnector,
    InvalidEventType,
    InvalidFieldMap,
    SyncNotConfigured,
    UnknownRoom,
)
from dsr.crm_engagement.inferences import describe as describe_inferences
from dsr.crm_engagement.mapping import Mapped, map_event, normalise_field_map, read_source
from dsr.crm_engagement.payloads import (
    CreateRequest,
    build_create,
    extract_record_id,
    success_codes,
)
from dsr.crm_engagement.queue import (
    CONNECTOR_COLLECTION,
    CRM_RECORD_ID_FIELD,
    ENGAGEMENT_COLLECTION,
    EVENT_TYPE_COLLECTION,
    FIELD_MAP_COLLECTION,
    QUEUE_COLLECTION,
    SYNC_LOG_COLLECTION,
    SYNC_STATE_FIELD,
    SyncBook,
    normalise_connector,
    normalise_event_type,
)
from dsr.crm_engagement.vocabulary import (
    BLOCK_REASONS,
    CREATE_ENDPOINTS,
    FAILURE_REASONS,
    PREFERENCES,
    QUEUE_STATES,
    RECORD_ID_LOCATIONS,
    SOURCED_QUOTES,
    TRANSFORM_NAMES,
    VENDORS,
    describe as describe_vocabulary,
)
from dsr.store import RecordStore

__all__ = [
    "BLOCK_REASONS",
    "CONNECTOR_COLLECTION",
    "CREATE_ENDPOINTS",
    "CRM_RECORD_ID_FIELD",
    "ENGAGEMENT_COLLECTION",
    "EVENT_TYPE_COLLECTION",
    "FAILURE_REASONS",
    "FIELD_MAP_COLLECTION",
    "PREFERENCES",
    "QUEUE_COLLECTION",
    "QUEUE_STATES",
    "RECORD_ID_LOCATIONS",
    "SOURCED_QUOTES",
    "SYNC_LOG_COLLECTION",
    "SYNC_STATE_FIELD",
    "TRANSFORM_NAMES",
    "VENDORS",
    "CreateReport",
    "CreateRequest",
    "CreateResult",
    "EngagementSync",
    "EngagementSyncError",
    "InvalidConnector",
    "InvalidEventType",
    "InvalidFieldMap",
    "Mapped",
    "Plan",
    "RecordStore",
    "SyncBook",
    "SyncNotConfigured",
    "Transport",
    "UnknownRoom",
    "UrllibTransport",
    "build_create",
    "delivery",
    "describe_inferences",
    "describe_vocabulary",
    "extract_record_id",
    "inferences",
    "map_event",
    "mapping",
    "normalise_connector",
    "normalise_event_type",
    "normalise_field_map",
    "payloads",
    "post_create",
    "queue",
    "read_source",
    "success_codes",
    "vocabulary",
]
