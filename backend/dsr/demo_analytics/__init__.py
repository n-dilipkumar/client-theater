"""WF-127: interactive demo / sandbox analytics with stakeholder discovery.

A product recording becomes a branching, sandboxed interactive demo. The demo is
placed in a room or sent as a link. When a buyer re-shares it internally, the
workflow surfaces the newly discovered stakeholders and their engagement. The
demo's own click-path tree and its per-viewer event stream produce an activity
heatmap and a six-level analytics roll-up.

The specification is ``docs/research/digital-sales-room-workflows/wf/WF-127.md``,
quoted in full on issue #152. It is the weakest-evidenced spec in the set: three
pages of vendor marketing copy and two GitHub API records, with no open-source
reference to port. Every value the evidence does not give is therefore a stated
substitution in :mod:`.inferences`, not a port.

==============================  ===============================================
Researched behaviour            Where it lives
==============================  ===============================================
branching click-path tree       :func:`.rules.normalise_nodes`,
                                :func:`.rules.resolve_path`
per-viewer event stream         :func:`.rules.normalise_event`,
                                :class:`.book.DemoAnalyticsBook.record_event`
stakeholder discovery           :func:`.rules.stakeholder_role`
activity heatmap                :func:`.rules.heatmap`
six drill-down levels           :func:`.rules.rollup`
metric vocabulary               :func:`.rules.metrics`
DQL score                       :func:`.rules.dql_score`
==============================  ===============================================

No migration, no typed column, no required field. Four open collections of
arbitrary JSON (demos, events, shares, stakeholders), filtered with ``find()``
over dotted paths. A team that adds ``buyer_group`` to an event filters on it the
same day.

The privacy boundary comes from the research's open-source analogue: a "custom
event schema like DeckLens'", which "explicitly does not transmit document bodies
or raw form values". :func:`.rules.normalise_event` refuses those keys by name,
so the ingest cannot accept a recording body even if a caller sends one.

The judgement calls the research leaves open - the discovery attribution, both
rate denominators, the heatmap axes, the DQL formula - are collected in
:mod:`.inferences` and served at ``GET /api/wf-127/inferences``.
"""

from __future__ import annotations

from .book import MAX_ROWS, DemoAnalyticsBook
from .errors import (
    DemoAnalyticsError,
    DemoArchived,
    DemoNotFound,
    DemoValidationError,
    EventRefused,
    ShareRefused,
)
from .inferences import INFERENCES, describe as describe_inferences
from .rules import (
    branch_options,
    buyer_group,
    dql_score,
    heatmap,
    metrics,
    node_index,
    normalise_demo,
    normalise_email,
    normalise_event,
    normalise_nodes,
    normalise_share,
    reach_counts,
    resolve_path,
    rollup,
    section_counts,
    stakeholder_role,
)
from .vocabulary import (
    DEFAULT_CLICK_PATH,
    DEFAULT_DQL_THRESHOLD,
    DEMO_COLLECTION,
    DEMO_STATES,
    DEMO_TYPES,
    DISCOVERY_SOURCES,
    DQL_MAX,
    DQL_WEIGHTS,
    EVENT_COLLECTION,
    EVENT_TYPES,
    HEATMAP_BUCKET_HOURS,
    LEVELS,
    METRIC_NAMES,
    NODE_KINDS,
    REFUSED_PAYLOAD_KEYS,
    ROOM_REF,
    SHARE_COLLECTION,
    STAKEHOLDER_COLLECTION,
    STAKEHOLDER_ROLES,
    WEEKDAY_NAMES,
    describe as describe_vocabulary,
)

__all__ = [
    "DEFAULT_CLICK_PATH",
    "DEFAULT_DQL_THRESHOLD",
    "DEMO_COLLECTION",
    "DEMO_STATES",
    "DEMO_TYPES",
    "DISCOVERY_SOURCES",
    "DQL_MAX",
    "DQL_WEIGHTS",
    "DemoAnalyticsBook",
    "DemoAnalyticsError",
    "DemoArchived",
    "DemoNotFound",
    "DemoValidationError",
    "EVENT_COLLECTION",
    "EVENT_TYPES",
    "EventRefused",
    "HEATMAP_BUCKET_HOURS",
    "INFERENCES",
    "LEVELS",
    "MAX_ROWS",
    "METRIC_NAMES",
    "NODE_KINDS",
    "REFUSED_PAYLOAD_KEYS",
    "ROOM_REF",
    "SHARE_COLLECTION",
    "STAKEHOLDER_COLLECTION",
    "STAKEHOLDER_ROLES",
    "ShareRefused",
    "WEEKDAY_NAMES",
    "branch_options",
    "buyer_group",
    "describe_inferences",
    "describe_vocabulary",
    "dql_score",
    "heatmap",
    "metrics",
    "node_index",
    "normalise_demo",
    "normalise_email",
    "normalise_event",
    "normalise_nodes",
    "normalise_share",
    "reach_counts",
    "resolve_path",
    "rollup",
    "section_counts",
    "stakeholder_role",
]
