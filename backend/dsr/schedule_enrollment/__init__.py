"""WF-120: enroll records on a schedule with re-enrollment control.

Module map, in dependency order:

``vocabulary``
    The researched terms: the four enrollment trigger types, the enrollment state
    machine, the re-enrollment modes, the publish-panel backfill choices, the
    action set, the filter operators, the researched retention windows, the
    workflow-log kinds and every sourced quote.
``errors``
    One error per distinct answer, each with a stable code.
``rules``
    The pure rules: timestamp reading, the enrollment filter matcher, payload
    normalisation, the re-enrollment decision, the queue delay and the action
    payloads. No store, no framework.
``engine``
    :class:`~dsr.schedule_enrollment.engine.ScheduleEnrollmentEngine` - the only
    module that reads or writes. Everything goes through the audited store.
``inferences``
    Every judgement call the research left open, named and served over HTTP.

Nothing here imports ``dsr.api``. The store seam is
:class:`~dsr.store.RecordStore`. The CRM records come from WF-039 and the task
vocabulary comes from WF-116, so this package does not re-declare either shape.
"""

from __future__ import annotations

from dsr.schedule_enrollment import errors, inferences, rules, vocabulary
from dsr.schedule_enrollment.engine import REASON_LABELS, ScheduleEnrollmentEngine
from dsr.schedule_enrollment.errors import (
    EnrollConflict,
    EnrollNotFound,
    EnrollValidationError,
    ScheduleEnrollmentError,
)
from dsr.schedule_enrollment.inferences import INFERENCES, by_id, describe_inferences
from dsr.schedule_enrollment.rules import (
    eligibility,
    normalise_conditions,
    normalise_schedule_payload,
    parse_timestamp,
    record_matches,
    record_view,
    retention_cutoff_ms,
    to_iso,
)
from dsr.schedule_enrollment.vocabulary import (
    ACTIONS,
    ACTIVE_STATUSES,
    BACKFILL_CHOICES,
    BACKFILL_EXISTING,
    BACKFILL_FUTURE_ONLY,
    COLLECTIONS,
    CRM_OBJECTS,
    CRM_RECORD_COLLECTION,
    DEFAULT_BACKFILL,
    DEFAULT_DELAY_MINUTES,
    DEFAULT_INTERVAL_MINUTES,
    DEFAULT_OBJECT,
    DEFAULT_REENROLL_MODE,
    ENROLLMENT_COLLECTION,
    EVENT_COLLECTION,
    FEATURE_ID,
    IMPLEMENTED_ACTIONS,
    NAME,
    NOTIFICATION_COLLECTION,
    OPS,
    REASON_ALREADY_ENROLLED,
    REASON_DEFERRED,
    REASON_MATCHED,
    REASON_NO_FILTER_MATCH,
    REASON_RE_ENROLLED,
    REASON_SINGLE_ENROLLMENT,
    REASON_UNENROLLED,
    REASONS,
    REENROLL_AFTER_COMPLETION,
    REENROLL_MODES,
    REENROLL_SINGLE,
    RETENTION,
    RUN_COLLECTION,
    RUN_KINDS,
    SCHEDULE_COLLECTION,
    SOURCED_GAPS,
    SOURCED_QUOTES,
    SOURCES,
    STATUS_COMPLETED,
    STATUS_QUEUED,
    STATUS_UNENROLLED,
    STATUSES,
    TERMINAL_STATUSES,
    TICKET,
    TRIGGER_SCHEDULE,
    TRIGGER_TYPES,
    USER_FLOW,
    WEBHOOK_CONTENT_TYPE,
    WEBHOOK_EVENT_LIMIT,
    published_vocabulary,
)

__all__ = [
    "ACTIONS",
    "ACTIVE_STATUSES",
    "BACKFILL_CHOICES",
    "BACKFILL_EXISTING",
    "BACKFILL_FUTURE_ONLY",
    "COLLECTIONS",
    "CRM_OBJECTS",
    "CRM_RECORD_COLLECTION",
    "DEFAULT_BACKFILL",
    "DEFAULT_DELAY_MINUTES",
    "DEFAULT_INTERVAL_MINUTES",
    "DEFAULT_OBJECT",
    "DEFAULT_REENROLL_MODE",
    "ENROLLMENT_COLLECTION",
    "EVENT_COLLECTION",
    "FEATURE_ID",
    "IMPLEMENTED_ACTIONS",
    "INFERENCES",
    "NAME",
    "NOTIFICATION_COLLECTION",
    "OPS",
    "REASONS",
    "REASON_ALREADY_ENROLLED",
    "REASON_DEFERRED",
    "REASON_LABELS",
    "REASON_MATCHED",
    "REASON_NO_FILTER_MATCH",
    "REASON_RE_ENROLLED",
    "REASON_SINGLE_ENROLLMENT",
    "REASON_UNENROLLED",
    "REENROLL_AFTER_COMPLETION",
    "REENROLL_MODES",
    "REENROLL_SINGLE",
    "RETENTION",
    "RUN_COLLECTION",
    "RUN_KINDS",
    "SCHEDULE_COLLECTION",
    "SOURCED_GAPS",
    "SOURCED_QUOTES",
    "SOURCES",
    "STATUSES",
    "STATUS_COMPLETED",
    "STATUS_QUEUED",
    "STATUS_UNENROLLED",
    "ScheduleEnrollmentEngine",
    "ScheduleEnrollmentError",
    "TERMINAL_STATUSES",
    "TICKET",
    "TRIGGER_SCHEDULE",
    "TRIGGER_TYPES",
    "USER_FLOW",
    "WEBHOOK_CONTENT_TYPE",
    "WEBHOOK_EVENT_LIMIT",
    "EnrollConflict",
    "EnrollNotFound",
    "EnrollValidationError",
    "by_id",
    "describe_inferences",
    "eligibility",
    "errors",
    "inferences",
    "normalise_conditions",
    "normalise_schedule_payload",
    "parse_timestamp",
    "published_vocabulary",
    "record_matches",
    "record_view",
    "retention_cutoff_ms",
    "rules",
    "to_iso",
    "vocabulary",
]
