"""WF-021: classify workspace engagement health as Hot / Warm / Cooling / Cold.

The published surface is :class:`~dsr.trend_health.health.TrendHealth`. The HTTP
surface is not in this package: the plugin host mounts
``dsr/features/wf021_classify_workspace_engagement_health_h.py``, which owns the
routes under ``/api/wf-021`` and the mapping of :class:`TrendError` to a
response.

Built from ``docs/research/digital-sales-room-workflows/wf/WF-021.md``, which
quotes its own source for the four buckets and the three windows::

    Hot = workspaces that have tons of recent engagement within the last 7 days.
    Warm = workspaces that have a decent amount of engagement within the last 14
    days. Cooling = workspaces that previously had engagement, but none within
    the last 14 days. Cold = workspace with no engagement within the last month.

The four windows' worth of sourced behaviour, and what this package adds on top:

* :mod:`dsr.trend_health.windows` - the ladder. Total by construction, decays
  through all four states with no new activity, and never stores a value.
* :mod:`dsr.trend_health.vocabulary` - the four Trend values with their sourced
  sentences, the five researched event shapes, and the collections this workflow
  owns.
* :mod:`dsr.trend_health.rules` - the windows (sourced) and the volume floors
  (not sourced), as a record a team can retune without a migration.
* :mod:`dsr.trend_health.timestamps` - the researched camelCase ``occurredAt``,
  and a refusal rather than a guess.
* :mod:`dsr.trend_health.inferences` - every judgement call, named, bounded and
  served at ``/api/wf-021/inferences``.

A deliberate boundary: :mod:`dsr.analytics` also carries a classifier called
``classify_trend``. That one is three-state (Cold, Warm, Hot) and is sourced to
the *Liferay* Room Trend widget this research also cites; this one is four-state
and is sourced to Dock's Trend column, with a Cooling state the Liferay widget
does not have. They read different event collections and answer different
questions, so they are two workflows and neither reads the other's data.
"""

from __future__ import annotations

from dsr.trend_health.errors import (
    InvalidRules,
    InvalidSort,
    InvalidTimestamp,
    TrendError,
    UnknownEventType,
    UnknownRoom,
)
from dsr.trend_health.health import DASHBOARD_SORTS, SORT_KEYS, TrendHealth
from dsr.trend_health.inferences import INFERENCES, SOURCED_QUOTE, describe
from dsr.trend_health.rules import (
    DEFAULT_COUNT_ONLY_EXTERNAL,
    DEFAULT_MIN_EVENTS,
    DEFAULT_RULES,
    DEFAULT_WINDOWS,
    effective,
    merge,
    validate,
)
from dsr.trend_health.timestamps import CLOCK_SKEW_TOLERANCE_SECONDS, parse_timestamp
from dsr.trend_health.vocabulary import (
    AUDIENCES,
    CLIENT_VIEW_EVENT,
    DECAY_PATH,
    ENGAGEMENT_COLLECTION,
    ENGAGEMENT_EVENT_TYPES,
    ORDER_FORM_PREFIX,
    RULES_COLLECTION,
    RULES_RECORD_ID,
    TREND_LABELS,
    TREND_RULES,
    TREND_VALUES,
    WINDOW_KEYS,
    is_engagement_event,
    require_audience,
    require_event_type,
    vocabulary,
)
from dsr.trend_health.windows import (
    EngagementEvent,
    bucket_of,
    classify,
    decay,
    in_window,
    window_counts,
)

__all__ = [
    "TrendHealth",
    "TrendError",
    "UnknownRoom",
    "UnknownEventType",
    "InvalidTimestamp",
    "InvalidRules",
    "InvalidSort",
    "EngagementEvent",
    "classify",
    "bucket_of",
    "decay",
    "in_window",
    "window_counts",
    "TREND_VALUES",
    "TREND_LABELS",
    "TREND_RULES",
    "DECAY_PATH",
    "WINDOW_KEYS",
    "ENGAGEMENT_EVENT_TYPES",
    "ORDER_FORM_PREFIX",
    "CLIENT_VIEW_EVENT",
    "AUDIENCES",
    "ENGAGEMENT_COLLECTION",
    "RULES_COLLECTION",
    "RULES_RECORD_ID",
    "DEFAULT_RULES",
    "DEFAULT_WINDOWS",
    "DEFAULT_MIN_EVENTS",
    "DEFAULT_COUNT_ONLY_EXTERNAL",
    "CLOCK_SKEW_TOLERANCE_SECONDS",
    "DASHBOARD_SORTS",
    "SORT_KEYS",
    "INFERENCES",
    "SOURCED_QUOTE",
    "vocabulary",
    "describe",
    "effective",
    "merge",
    "validate",
    "parse_timestamp",
    "is_engagement_event",
    "require_event_type",
    "require_audience",
]
