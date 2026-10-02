"""WF-040: surface partial failures, and reject invalid writes before commit.

The published surface is :class:`~dsr.partial_failures.log.SyncLog`. The HTTP
surface is not in this package: the plugin host mounts
``dsr/features/wf040_surface_partial_failures_and_reject_in.py``, which owns the
routes under ``/api/wf-040`` and the mapping of :class:`PartialFailureError` to a
response.

Built from ``docs/research/digital-sales-room-workflows/wf/WF-040.md``, whose
evidence is three vendors' own error contracts and whose flow is five steps::

    1. A sync batch runs and some rows fail.
    2. Instead of failing the whole batch, the connector asks for per-record
       outcomes (multi-status / continue-on-error).
    3. The room's Sync log lists each failed row with a human-readable reason and
       the offending property.
    4. Admin clicks a failed row -> Field-level error detail (which property, what
       was sent, what was expected).
    5. Admin fixes the mapping or the data, and clicks Retry failed rows only -
       successes are not re-sent.

Where each step landed:

* :mod:`dsr.partial_failures.vocabulary` - step two, per vendor. What to ask for
  (HubSpot's 207 Multi-Status with a unique ``objectWriteTraceId`` per input;
  Dataverse's ``Prefer: odata.continue-on-error`` and
  ``odata.include-annotations="*"``; Salesforce's ``allOrNone: false``), what each
  answers with, and how a result is keyed back to a row.
* :mod:`dsr.partial_failures.normalise` - step two's payoff and the research's
  transformation. Three vendor shapes collapse into one room error model, the five
  keys the extensibility note names: ``{retryable, field, code, message,
  docLink}``.
* :mod:`dsr.partial_failures.log` - steps three, four and five. The Sync log, the
  field-level detail, and the retry endpoints whose input is the failed rows alone.
* :mod:`dsr.partial_failures.retry` - the researched automation. Retryable classes
  drain; terminal ones wait for a person.
* :mod:`dsr.partial_failures.validation` - the "before commit" half. The room
  checks a proposed batch against its own rules so a write it can refuse costs no
  CRM call and never reaches the Sync log.
* :mod:`dsr.partial_failures.rules` - the researched extension point, as a record
  rather than a code change: "a deployment can add a rule ... without changing the
  transport".
* :mod:`dsr.partial_failures.inferences` - every judgement call the research's
  silences forced, named, bounded and served at ``/api/wf-040/inferences``.

A deliberate boundary: :mod:`dsr.crm` pushes room events *out* to a CRM over
webhooks, and its Activity Log is about whether a delivery reached a URL. This
workflow is about the *other* direction of the same problem - a batch that went
out and came back partly refused - and reads different records. Neither reads the
other's data, and WF-040's retry policy is not WF-016's delivery retry.
"""

from __future__ import annotations

from dsr.partial_failures.errors import (
    InvalidPayload,
    InvalidRule,
    PartialFailureError,
    UnknownConnector,
    UnknownRoom,
    UnknownRow,
    UnknownRun,
)
from dsr.partial_failures.inferences import (
    INFERENCES,
    SOURCED_AUTOMATION,
    SOURCED_EXTENSIBILITY,
    SOURCED_GAP,
    by_id as inference_by_id,
    describe as describe_inferences,
)
from dsr.partial_failures.log import HISTORY_LIMIT, SyncLog
from dsr.partial_failures.normalise import (
    CLASSIFICATION,
    DEFAULT_BASIS,
    DEFAULT_CLASSIFICATION,
    HELP_LINK_ANNOTATION,
    MULTI_STATUS,
    OVERRIDE_MATCH_KEYS,
    OVERRIDE_THEN_KEYS,
    BatchOutcome,
    ClassificationEntry,
    NormalisedError,
    RowOutcome,
    classify,
    dataverse_field_from_message,
    help_link,
    normalise,
)
from dsr.partial_failures.retry import (
    BACKOFF_LABEL,
    BASE_BACKOFF_SECONDS,
    MAX_ATTEMPTS,
    MAX_BACKOFF_SECONDS,
    backoff_seconds,
    can_retry,
    disposition_for,
    is_retryable,
    next_attempt_at,
    plan as retry_plan,
)
from dsr.partial_failures.rules import (
    DEFAULT_RULES,
    ROUTING_RULE_KEYS,
    RULE_KEYS,
    describe as describe_rules,
    effective as effective_rules,
    merge as merge_rules,
    validate as validate_rules_record,
    validate_routing,
    validate_routing_rule,
)
from dsr.partial_failures.timestamps import (
    FUTURE_TOLERANCE_SECONDS,
    check_not_ahead,
    iso,
    parse_instant,
    plus_seconds,
)
from dsr.partial_failures.validation import (
    ANY,
    DEFAULT_PREFLIGHT_RULES,
    PREFLIGHT_KINDS,
    SENT_PREVIEW_CHARS,
    check_batch,
    check_row,
    covers,
    defaults as preflight_defaults,
    expectation_text,
    expectations_for,
    merge_rules as merge_preflight_rules,
    validate_rule,
    validate_rules,
)
from dsr.partial_failures.vocabulary import (
    AUTOMATION_QUOTE,
    CONNECTOR_LABELS,
    CONNECTOR_QUOTES,
    CONNECTORS,
    CORRELATION,
    DISPOSITION_LABELS,
    DISPOSITION_MEANING,
    DISPOSITIONS,
    DOC_LINK_SOURCE,
    ERROR_MODEL_KEYS,
    ERROR_MODEL_SPELLING,
    EXTENSIBILITY_QUOTE,
    FIELD_SOURCE,
    HUBSPOT_VALIDATION_ENFORCEMENT,
    PER_RECORD_REQUEST,
    PER_RECORD_STATUS,
    ROW_COLLECTION,
    ROW_STATUS_LABELS,
    ROW_STATUSES,
    RULES_COLLECTION,
    RULES_RECORD_ID,
    RUN_COLLECTION,
    require_connector,
    vocabulary,
)

__all__ = [
    # service
    "SyncLog",
    "HISTORY_LIMIT",
    # errors
    "PartialFailureError",
    "UnknownConnector",
    "UnknownRun",
    "UnknownRow",
    "UnknownRoom",
    "InvalidPayload",
    "InvalidRule",
    # vocabulary
    "CONNECTORS",
    "CONNECTOR_LABELS",
    "CONNECTOR_QUOTES",
    "PER_RECORD_REQUEST",
    "PER_RECORD_STATUS",
    "CORRELATION",
    "DOC_LINK_SOURCE",
    "FIELD_SOURCE",
    "HUBSPOT_VALIDATION_ENFORCEMENT",
    "ROW_STATUSES",
    "ROW_STATUS_LABELS",
    "DISPOSITIONS",
    "DISPOSITION_LABELS",
    "DISPOSITION_MEANING",
    "ERROR_MODEL_KEYS",
    "ERROR_MODEL_SPELLING",
    "AUTOMATION_QUOTE",
    "EXTENSIBILITY_QUOTE",
    "RUN_COLLECTION",
    "ROW_COLLECTION",
    "RULES_COLLECTION",
    "RULES_RECORD_ID",
    "vocabulary",
    "require_connector",
    # normalisation
    "NormalisedError",
    "RowOutcome",
    "BatchOutcome",
    "ClassificationEntry",
    "CLASSIFICATION",
    "DEFAULT_CLASSIFICATION",
    "DEFAULT_BASIS",
    "OVERRIDE_MATCH_KEYS",
    "OVERRIDE_THEN_KEYS",
    "HELP_LINK_ANNOTATION",
    "MULTI_STATUS",
    "normalise",
    "classify",
    "dataverse_field_from_message",
    "help_link",
    # validation
    "ANY",
    "PREFLIGHT_KINDS",
    "SENT_PREVIEW_CHARS",
    "DEFAULT_PREFLIGHT_RULES",
    "validate_rule",
    "validate_rules",
    "merge_preflight_rules",
    "preflight_defaults",
    "expectation_text",
    "check_row",
    "check_batch",
    "expectations_for",
    "covers",
    # retry
    "MAX_ATTEMPTS",
    "BASE_BACKOFF_SECONDS",
    "MAX_BACKOFF_SECONDS",
    "BACKOFF_LABEL",
    "backoff_seconds",
    "next_attempt_at",
    "is_retryable",
    "disposition_for",
    "retry_plan",
    "can_retry",
    # rules
    "DEFAULT_RULES",
    "RULE_KEYS",
    "ROUTING_RULE_KEYS",
    "validate_routing_rule",
    "validate_routing",
    "validate_rules_record",
    "merge_rules",
    "effective_rules",
    "describe_rules",
    # timestamps
    "FUTURE_TOLERANCE_SECONDS",
    "parse_instant",
    "check_not_ahead",
    "iso",
    "plus_seconds",
    # inferences
    "INFERENCES",
    "SOURCED_AUTOMATION",
    "SOURCED_EXTENSIBILITY",
    "SOURCED_GAP",
    "describe_inferences",
    "inference_by_id",
]
