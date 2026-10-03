"""WF-049: monitor integration health and remaining API quota.

The published surface is :class:`~dsr.integ_monitor.engine.IntegrationMonitor`.
The HTTP surface is not in this package: the plugin host mounts
``dsr/features/wf049_monitor_integration_health_and_remaini.py``, which owns the
routes under ``/api/wf-049`` and the mapping of :class:`MonitorError` to a
response.

Built from ``docs/research/digital-sales-room-workflows/wf/WF-049.md``, whose
flow is four steps::

    1. Operator opens the room's Integrations → <connection> → Monitoring
       dashboard.
    2. Dashboard shows remaining daily + burst quota, sync success rate, mean
       latency, error-class breakdown (validation / throttle / auth /
       vendor-5xx), and the live change-stream lag.
    3. Quota is read from the vendor's own surfaces: Salesforce GET /limits/
       and the Sforce-Limit-Info header; HubSpot response headers plus the
       account-information endpoint and its Monitoring pages; Dataverse's
       EntityDefinitions change-tracking audit and globalmetadataversion.
    4. If a connector is starved, the operator lowers its concurrency or
       pauses it from the same page.

Where each step landed:

* :mod:`dsr.integ_monitor.quota` - steps 2 and 3's transformation. Heterogeneous
  vendor quota models (daily+burst, per-10s, per-app, per-account) normalise
  into one "remaining today / remaining this window" pair.
* :mod:`dsr.integ_monitor.health` - step 2's telemetry: success rate, mean
  latency, the four researched error classes, and the change-stream lag.
* :mod:`dsr.integ_monitor.alerts` - the automations: alert rules fire when
  remaining budget crosses a threshold or when the change-stream lag exceeds
  N seconds; Slack/email/webhook channels, a cooldown, and a fire history.
* :mod:`dsr.integ_monitor.engine` - the room metrics store: connectors, quota
  observations, telemetry, stream observations, change-tracking audits and the
  rules over them, every write audited with the route that served it.
* :mod:`dsr.integ_monitor.inferences` - every judgement call the research's
  silences forced, named, bounded and served at ``/api/wf-049/inferences``.

Two deliberate boundaries, both stated in the research rather than chosen:

* **The room transports nothing.** Every ``apis_hit`` is a call a connector
  makes, and this product holds no vendor credentials. The quota and telemetry
  endpoints take the vendor's answer back rather than fetching it.
* **Unknown is not zero.** A HubSpot OAuth connection has no daily header
  (sourced), a Dataverse org has no sourced numbers at all (the research's own
  gap), and a Salesforce limit row the connector cannot read is not an
  allocation of zero. Every unknown half says why, because a monitoring
  dashboard that answered "0 remaining" for an org it could not read would be
  the loudest false alarm in the product.
"""

from __future__ import annotations

from dsr.integ_monitor.alerts import (
    DEFAULT_BUDGET_THRESHOLD_PCT,
    DEFAULT_COOLDOWN_MINUTES,
    DEFAULT_LAG_THRESHOLD_SECONDS,
    FIRE_HISTORY_LIMIT,
    METRIC_UNITS,
    check_rule,
    evaluate_rules,
    merge_rule,
    metric_value,
    record_fire,
)
from dsr.integ_monitor.engine import (
    CONCURRENCY_MAX,
    CONCURRENCY_MIN,
    DEFAULT_POLICIES,
    DEFAULT_WINDOW_SECONDS,
    IntegrationMonitor,
)
from dsr.integ_monitor.errors import (
    InvalidPayload,
    InvalidQuotaSurface,
    InvalidRule,
    InvalidTelemetry,
    MonitorError,
    UnknownConnector,
    UnknownRoom,
    UnknownRule,
    UnknownVendor,
)
from dsr.integ_monitor.health import (
    aggregate,
    check_lag,
    check_samples,
    class_from_status,
)
from dsr.integ_monitor.inferences import (
    INFERENCES,
    SOURCED_AUTOMATION,
    SOURCED_EXTENSIBILITY,
    SOURCED_GAP,
    by_id as inference_by_id,
    describe as describe_inferences,
)
from dsr.integ_monitor.quota import empty_half, half, normalise
from dsr.integ_monitor.vocabulary import (
    CHANNELS,
    CHANGE_COLLECTION,
    CONNECTOR_COLLECTION,
    ERROR_CLASSES,
    ERROR_CLASS_LABELS,
    METRIC_COMPARISON,
    METRICS,
    METRIC_LABELS,
    QUOTA_SURFACES,
    RESEARCH_GAPS,
    RULE_COLLECTION,
    STREAM_COLLECTION,
    TELEMETRY_COLLECTION,
    QUOTA_COLLECTION,
    VENDORS,
    require_vendor,
    vocabulary,
)

__all__ = [
    # service
    "IntegrationMonitor",
    "DEFAULT_POLICIES",
    "DEFAULT_WINDOW_SECONDS",
    "CONCURRENCY_MIN",
    "CONCURRENCY_MAX",
    # errors
    "MonitorError",
    "UnknownRoom",
    "UnknownVendor",
    "UnknownConnector",
    "UnknownRule",
    "InvalidPayload",
    "InvalidQuotaSurface",
    "InvalidRule",
    "InvalidTelemetry",
    # vocabulary
    "VENDORS",
    "ERROR_CLASSES",
    "ERROR_CLASS_LABELS",
    "METRICS",
    "METRIC_LABELS",
    "METRIC_COMPARISON",
    "METRIC_UNITS",
    "CHANNELS",
    "QUOTA_SURFACES",
    "RESEARCH_GAPS",
    "CONNECTOR_COLLECTION",
    "QUOTA_COLLECTION",
    "TELEMETRY_COLLECTION",
    "STREAM_COLLECTION",
    "CHANGE_COLLECTION",
    "RULE_COLLECTION",
    "require_vendor",
    "vocabulary",
    # quota normalisation
    "normalise",
    "half",
    "empty_half",
    # health
    "class_from_status",
    "check_samples",
    "check_lag",
    "aggregate",
    # alerts
    "check_rule",
    "merge_rule",
    "evaluate_rules",
    "record_fire",
    "metric_value",
    "DEFAULT_COOLDOWN_MINUTES",
    "DEFAULT_BUDGET_THRESHOLD_PCT",
    "DEFAULT_LAG_THRESHOLD_SECONDS",
    "FIRE_HISTORY_LIMIT",
    # inferences
    "INFERENCES",
    "SOURCED_AUTOMATION",
    "SOURCED_EXTENSIBILITY",
    "SOURCED_GAP",
    "describe_inferences",
    "inference_by_id",
]
