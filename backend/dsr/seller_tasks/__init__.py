"""WF-116: create an automated seller follow-up task with a reminder.

Module map, in dependency order:

``vocabulary``
    The researched terms: four closed vocabularies, the reminder unit, the
    engagement signals, the scopes, the collections, and every sourced quote.
``errors``
    One error per distinct answer, each with a stable code.
``rules``
    The pure rules: timestamp reading, closed-vocabulary checks, the signal to
    task map, and payload normalisation. No store, no framework.
``engine``
    :class:`~dsr.seller_tasks.engine.SellerTaskEngine` - the only module that
    reads or writes. Everything goes through the audited store.
``inferences``
    Every judgement call the research left open, named and served over HTTP.

Nothing here imports ``dsr.api``. The store seam is
:class:`~dsr.store.RecordStore`. The contact vocabulary comes from WF-039 and
the internal-role vocabulary comes from WF-077, so this package does not
re-declare a contact shape or a role shape.
"""

from __future__ import annotations

from dsr.seller_tasks import errors, inferences, rules, vocabulary
from dsr.seller_tasks.engine import SellerTaskEngine
from dsr.seller_tasks.errors import (
    SellerTaskError,
    TaskConflict,
    TaskNotFound,
    TaskValidationError,
)
from dsr.seller_tasks.inferences import INFERENCES, by_id, describe_inferences
from dsr.seller_tasks.rules import (
    CONTACT_COLLECTION,
    CONTACT_OBJECT,
    DEFAULT_SUBJECT,
    SIGNAL_TASK_RULES,
    is_known_signal,
    is_permitted_role,
    normalise_task_payload,
    parse_timestamp,
    retention_cutoff_ms,
    signal_defaults,
    signal_rule,
    to_iso,
)
from dsr.seller_tasks.vocabulary import (
    ATTACHMENT_CONTACT,
    ATTACHMENT_STANDALONE,
    ATTACHMENTS,
    COLLECTIONS,
    EVENT_COLLECTION,
    FEATURE_ID,
    NAME,
    NOTIFICATION_COLLECTION,
    PRIORITIES,
    RECYCLING_BIN_RETENTION_DAYS,
    REMINDER_UNIT,
    REQUIRED_SCOPES,
    SIGNALS,
    SOURCED_GAPS,
    SOURCED_QUOTES,
    STATUS_COMPLETED,
    STATUS_NOT_STARTED,
    STATUSES,
    TASK_COLLECTION,
    TASK_TYPES,
    TICKET,
    TRIGGER_TASK_ADDED,
    TRIGGER_TASK_COMPLETED,
    TRIGGER_TASK_REMINDER,
    TRIGGERS,
    TYPE_CALL,
    TYPE_EMAIL,
    TYPE_TODO,
    published_vocabulary,
)

__all__ = [
    "ATTACHMENTS",
    "ATTACHMENT_CONTACT",
    "ATTACHMENT_STANDALONE",
    "COLLECTIONS",
    "CONTACT_COLLECTION",
    "CONTACT_OBJECT",
    "DEFAULT_SUBJECT",
    "EVENT_COLLECTION",
    "FEATURE_ID",
    "INFERENCES",
    "NAME",
    "NOTIFICATION_COLLECTION",
    "PRIORITIES",
    "RECYCLING_BIN_RETENTION_DAYS",
    "REMINDER_UNIT",
    "REQUIRED_SCOPES",
    "SIGNALS",
    "SIGNAL_TASK_RULES",
    "SOURCED_GAPS",
    "SOURCED_QUOTES",
    "STATUSES",
    "STATUS_COMPLETED",
    "STATUS_NOT_STARTED",
    "SellerTaskEngine",
    "SellerTaskError",
    "TASK_COLLECTION",
    "TASK_TYPES",
    "TICKET",
    "TRIGGERS",
    "TRIGGER_TASK_ADDED",
    "TRIGGER_TASK_COMPLETED",
    "TRIGGER_TASK_REMINDER",
    "TYPE_CALL",
    "TYPE_EMAIL",
    "TYPE_TODO",
    "TaskConflict",
    "TaskNotFound",
    "TaskValidationError",
    "by_id",
    "describe_inferences",
    "errors",
    "inferences",
    "is_known_signal",
    "is_permitted_role",
    "normalise_task_payload",
    "parse_timestamp",
    "published_vocabulary",
    "retention_cutoff_ms",
    "rules",
    "signal_defaults",
    "signal_rule",
    "to_iso",
    "vocabulary",
]
