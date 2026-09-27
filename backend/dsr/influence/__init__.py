"""WF-019: rank content influence and associate revenue with assets.

Built from ``docs/research/digital-sales-room-workflows/wf/WF-019.md``, which is
the specification. The researched flow, in the order the report presents it:

1. portfolio metrics - number of assets, content shares, content client views,
   utilization rate, engagement rate;
2. "Content engagement over time" at day / week / month / quarter / year;
3. "Top content" - most-viewed assets plus shares, total time spent, downloads,
   last share, last view, sortable by any column;
4. "Content & Sales Influence" - revenue and deals per asset, which needs a CRM
   integration and accounts and deals connected to workspaces;
5. filters by collection, a client-activity time range, and a shares time range.

The modules, and why each is separate:

``errors``
    This feature's own exception types. Separate because ``EXCEPTION_HANDLERS``
    maps them, and a handler for a type nothing else raises cannot intercept an
    unrelated error anywhere in the product.
``vocab``
    The collections, the researched vocabulary, time parsing and bucketing, the
    filter set, and the paged scan. The one place that knows what a "week" is.
``events``
    The two researched sources of occurrences - the ``asset.*`` webhooks and the
    product's own ``activity`` collection - reduced to one event log.
``metrics``
    The three report surfaces, all projections of one compilation, so they
    cannot disagree with each other.
``sales``
    The revenue join, and the writes that create it. The only module that reads
    the CRM collections.
``inferences``
    Every choice the research left open, named, with what would change it.

No migration, no typed column, no new required field: the library is read as
``document`` records and the CRM links live in their own collections, both as
arbitrary JSON that a team can reshape without coordinating with anyone.
"""

from __future__ import annotations

from dsr.influence.errors import (
    AssetOutOfScope,
    CrmNotLinked,
    InfluenceError,
    InfluenceNotLinked,
    InvalidWindow,
    UnknownAsset,
    UnknownChoice,
    UnknownRoom,
    UnparseableTime,
)
from dsr.influence.events import (
    ingest_activity,
    known_actions,
    load_events,
    normalize_action,
    record_event,
    summarise_vocabulary,
)
from dsr.influence.inferences import describe as describe_inferences
from dsr.influence.metrics import (
    AssetRow,
    Report,
    asset_detail,
    collections,
    compile_report,
    engagement,
    portfolio,
    top_content,
    vocabulary,
)
from dsr.influence.sales import (
    link_account,
    link_deal,
    preconditions,
    sales_influence,
    summarise_links,
)
from dsr.influence.vocab import (
    ACCOUNT_COLLECTION,
    ACTIONS,
    ACTIVITY_COLLECTION,
    ASSET_COLLECTION,
    AUDIENCES,
    DEAL_COLLECTION,
    DOWNLOADED,
    EVENT_COLLECTION,
    EXTERNAL,
    GRAINS,
    INTERNAL,
    MAX_EVENTS,
    PORTFOLIO_METRICS,
    ROOM_COLLECTION,
    SHARED,
    TOP_CONTENT_COLUMNS,
    VIEWED,
    Filters,
    asset_collections,
    asset_title,
    bucket_key,
    bucket_range,
    bucket_span,
    iso,
    parse_time,
    scan,
    utcnow,
)

__all__ = [
    "ACCOUNT_COLLECTION",
    "ACTIONS",
    "ACTIVITY_COLLECTION",
    "ASSET_COLLECTION",
    "AUDIENCES",
    "AssetOutOfScope",
    "AssetRow",
    "CrmNotLinked",
    "DEAL_COLLECTION",
    "DOWNLOADED",
    "EVENT_COLLECTION",
    "EXTERNAL",
    "Filters",
    "GRAINS",
    "INTERNAL",
    "InfluenceError",
    "InfluenceNotLinked",
    "InvalidWindow",
    "MAX_EVENTS",
    "PORTFOLIO_METRICS",
    "ROOM_COLLECTION",
    "Report",
    "SHARED",
    "TOP_CONTENT_COLUMNS",
    "UnknownAsset",
    "UnknownChoice",
    "UnknownRoom",
    "UnparseableTime",
    "VIEWED",
    "asset_collections",
    "asset_detail",
    "asset_title",
    "bucket_key",
    "bucket_range",
    "bucket_span",
    "collections",
    "compile_report",
    "describe_inferences",
    "engagement",
    "ingest_activity",
    "iso",
    "known_actions",
    "link_account",
    "link_deal",
    "load_events",
    "normalize_action",
    "parse_time",
    "portfolio",
    "preconditions",
    "record_event",
    "sales_influence",
    "scan",
    "summarise_links",
    "summarise_vocabulary",
    "top_content",
    "utcnow",
    "vocabulary",
]
