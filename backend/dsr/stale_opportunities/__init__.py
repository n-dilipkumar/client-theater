"""WF-117: sweep stale opportunities by an inactivity rule and escalate.

Module map, in dependency order:

``vocabulary``
    The researched terms: the trigger family, the opportunity statuses, the
    action set, the stale-rule references, the cadence, the re-fire control, the
    collections, and every sourced quote.
``errors``
    One error per distinct answer, each with a stable code.
``rules``
    The pure rules: timestamp reading, payload normalisation, the opportunity
    view, the staleness evaluation, and the escalation's own payloads. No store,
    no framework.
``engine``
    :class:`~dsr.stale_opportunities.engine.StaleOpportunitySweepEngine` - the
    only module that reads or writes. Everything goes through the audited store.
``inferences``
    Every judgement call the research left open, named and served over HTTP.

Nothing here imports ``dsr.api``. The store seam is
:class:`~dsr.store.RecordStore`. The opportunity vocabulary comes from WF-039 and
the task vocabulary comes from WF-116, so this package re-declares neither.
"""

from __future__ import annotations

from dsr.stale_opportunities import errors, inferences, rules, vocabulary
from dsr.stale_opportunities.engine import REASON_LABELS, StaleOpportunitySweepEngine
from dsr.stale_opportunities.errors import (
    StaleOpportunityError,
    SweepConflict,
    SweepNotFound,
    SweepValidationError,
)
from dsr.stale_opportunities.inferences import INFERENCES, by_id, describe_inferences
from dsr.stale_opportunities.rules import (
    DAY_MS,
    already_escalated,
    cadence_step_for,
    coerce_choice,
    escalation_patch,
    evaluate_opportunity,
    idle_days,
    normalise_cadence,
    normalise_filters,
    normalise_sweep_payload,
    notification_message,
    opportunity_view,
    parse_timestamp,
    reference_ms,
    stage_filter_ok,
    task_payload,
    to_iso,
    value_band_ok,
)
from dsr.stale_opportunities.vocabulary import (
    COLLECTIONS,
    DEFAULT_CADENCE,
    DEFAULT_REFIRE_CONTROL,
    DEFAULT_STALE_REFERENCE,
    DEFAULT_THRESHOLD_DAYS,
    ESCALATION_ACTIONS,
    ESCALATION_COLLECTION,
    FEATURE_ID,
    MARKER_AT_MS,
    MARKER_KEYS,
    MARKER_SWEEP_ID,
    NAME,
    NOTIFICATION_COLLECTION,
    OPPORTUNITY_COLLECTION,
    OPPORTUNITY_OBJECT,
    REASONS,
    REFIRE_CONTROLS,
    RUN_COLLECTION,
    SOURCED_GAPS,
    SOURCED_QUOTES,
    SOURCES,
    STALE_REFERENCES,
    STATUSES,
    SWEEP_COLLECTION,
    TICKET,
    TRIGGER_STALE,
    TRIGGERS,
    USER_FLOW,
    published_vocabulary,
)

__all__ = [
    "COLLECTIONS",
    "DEFAULT_CADENCE",
    "DEFAULT_REFIRE_CONTROL",
    "DEFAULT_STALE_REFERENCE",
    "DEFAULT_THRESHOLD_DAYS",
    "DAY_MS",
    "ESCALATION_ACTIONS",
    "ESCALATION_COLLECTION",
    "FEATURE_ID",
    "INFERENCES",
    "MARKER_AT_MS",
    "MARKER_KEYS",
    "MARKER_SWEEP_ID",
    "NAME",
    "NOTIFICATION_COLLECTION",
    "OPPORTUNITY_COLLECTION",
    "OPPORTUNITY_OBJECT",
    "REASONS",
    "REASON_LABELS",
    "REFIRE_CONTROLS",
    "RUN_COLLECTION",
    "SOURCED_GAPS",
    "SOURCED_QUOTES",
    "SOURCES",
    "STALE_REFERENCES",
    "STATUSES",
    "SWEEP_COLLECTION",
    "StaleOpportunityError",
    "StaleOpportunitySweepEngine",
    "SweepConflict",
    "SweepNotFound",
    "SweepValidationError",
    "TICKET",
    "TRIGGERS",
    "TRIGGER_STALE",
    "USER_FLOW",
    "already_escalated",
    "by_id",
    "cadence_step_for",
    "coerce_choice",
    "describe_inferences",
    "errors",
    "escalation_patch",
    "evaluate_opportunity",
    "idle_days",
    "inferences",
    "normalise_cadence",
    "normalise_filters",
    "normalise_sweep_payload",
    "notification_message",
    "opportunity_view",
    "parse_timestamp",
    "published_vocabulary",
    "reference_ms",
    "rules",
    "stage_filter_ok",
    "task_payload",
    "to_iso",
    "value_band_ok",
    "vocabulary",
]
