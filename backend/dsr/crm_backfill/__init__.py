"""WF-045: backfill historical records on a schedule with a resumable cursor.

The CRM-integration domain for the Digital Sales Room, kept in its own package so
no two features claim one path. A backfill reads a room's history out of a CRM -
asynchronously through a job, or through a delta token or a paged read - writes
it into the room's replica, and keeps a cursor so that a crash resumes where it
stopped with no duplicates and no gaps.

The module layout, and why each piece is separate:

``vocabulary``    the values and numbers the research fixes by name, served as data
``errors``        one hierarchy, so the feature registers a single handler
``cursors``       the standard ``{vendor, connectionId, cursor, updatedAt}`` record
``vendors``       the "create job" / "read page" seam three researched vendors implement
``plan``          range or full history, the volume rule, the field map, the call estimate
``quota``         the daily allowance and the local-midnight window it resets in
``transform``     the field map, the upsert key, and what counts as unchanged
``inferences``    every judgement call, named and served
``engine``        the façade the HTTP layer calls; owns the five collections

:data:`dsr.crm_backfill.inferences.SOURCED_QUOTE` is the sentence from the
research that governs the whole package, and
:func:`dsr.crm_backfill.inferences.describe` is served at the feature's
``/inferences`` route so a reviewer can see which parts are researched and which
are this build's judgement without reading the diff.
"""

from __future__ import annotations

from dsr.crm_backfill.cursors import (
    CURSOR_FIELDS,
    build as build_cursor,
    default_expiry_days,
    default_page_size,
    describe as describe_cursor,
    is_expired,
    require_resumable,
)
from dsr.crm_backfill.engine import (
    CONNECTIONS,
    CURSORS_STORE,
    EVENTS,
    REPLICA,
    RUNS,
    BackfillEngine,
    progress,
)
from dsr.crm_backfill.errors import (
    BackfillError,
    CursorExpired,
    DirectionNotSupported,
    InvalidRange,
    MissingScope,
    PlanError,
    PrerequisiteError,
    QuotaExceeded,
    RunNotFound,
    RunStateError,
    UnaddressableObject,
    UnkeyedRow,
    UnknownConnection,
    UnsupportedStrategy,
    UnsupportedVendor,
)
from dsr.crm_backfill.inferences import INFERENCES, SOURCED_QUOTE, by_id as inference_by_id
from dsr.crm_backfill.plan import bulk_threshold, choose_strategy, normalise_scope
from dsr.crm_backfill.quota import CALL_REASONS
from dsr.crm_backfill.transform import DEFAULT_KEY_FIELD, RESERVED_FIELDS
from dsr.crm_backfill.vendors import (
    HUBSPOT_EXPORT_STATUSES,
    HistorySource,
    SimulatedHistory,
    VendorAdapter,
    VendorPage,
    default_registry,
)
from dsr.crm_backfill.vocabulary import (
    CURSOR_KINDS,
    DIRECTIONS,
    NUMBERS,
    RUN_STATES,
    SCOPES,
    STRATEGIES,
    TERMINAL_STATES,
    VENDORS,
    describe as describe_vocabulary,
)

__all__ = [
    "CALL_REASONS",
    "CONNECTIONS",
    "CORSORS_STORE",
    "CURSOR_FIELDS",
    "CURSOR_KINDS",
    "CURSORS_STORE",
    "DEFAULT_KEY_FIELD",
    "DIRECTIONS",
    "EVENTS",
    "HUBSPOT_EXPORT_STATUSES",
    "INFERENCES",
    "NUMBERS",
    "REPLICA",
    "RESERVED_FIELDS",
    "RUNS",
    "RUN_STATES",
    "SCOPES",
    "SOURCED_QUOTE",
    "STRATEGIES",
    "TERMINAL_STATES",
    "VENDORS",
    "BackfillEngine",
    "BackfillError",
    "CursorExpired",
    "DirectionNotSupported",
    "HistorySource",
    "InvalidRange",
    "MissingScope",
    "PlanError",
    "PrerequisiteError",
    "QuotaExceeded",
    "RunNotFound",
    "RunStateError",
    "SimulatedHistory",
    "UnknownConnection",
    "UnaddressableObject",
    "UnkeyedRow",
    "UnsupportedStrategy",
    "UnsupportedVendor",
    "VendorAdapter",
    "VendorPage",
    "build_cursor",
    "bulk_threshold",
    "choose_strategy",
    "default_expiry_days",
    "default_page_size",
    "default_registry",
    "describe_cursor",
    "describe_vocabulary",
    "inference_by_id",
    "is_expired",
    "normalise_scope",
    "progress",
    "require_resumable",
]
