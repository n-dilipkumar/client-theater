"""WF-018: read per-page dwell time and drop-off inside a PDF.

A build, not a port. The researched document
(``docs/research/digital-sales-room-workflows/wf/WF-018.md``) *is* the
specification, and this package is the domain it describes: the library asset
objects, the per-page telemetry a viewer emits, and the three analytics blocks
the source product puts under **Advanced Analytics**.

======================  ===================================================
Researched behaviour    Where it lives
======================  ===================================================
"Time spent per page"   :func:`metrics.dwell_per_page`
"Drop off per page"     :func:`metrics.drop_off_per_page`
"average watch time"    :func:`metrics.average_watch_time`
"Core Analytics" bars   :func:`metrics.core_counts`, :func:`metrics.bucket_series`
External users only,    :func:`vocabulary.audience_of` and
Shares the exception    :data:`vocabulary.INTERNAL_ONLY_METRICS`
"for multi-page PDFs"   :meth:`book.AnalyticsBook._pdf_reason`
"self-hosted videos"    :meth:`book.AnalyticsBook._video_reason`
No per-page endpoint    this package defines the ingest, in
                        :mod:`.inferences`
======================  ===================================================

Two things are deliberately absent, and their absence is the point.

**No migration, no typed column, no required field.** Four open collections of
arbitrary JSON, filtered with ``find()`` over dotted paths. A team that adds
``pricingTier`` to an asset filters on it the same day, with no coordination.

**No cached aggregate.** The research says "Analytics are computed continuously;
the *action* on them is manual", so every read here is a pure read of the
telemetry. There is no rollup row to keep in step and no cache to invalidate,
and the suite asserts that reading writes nothing.
"""

from __future__ import annotations

from .book import (
    ASSET_COLLECTION,
    EVENT_COLLECTION,
    MAX_TELEMETRY_ROWS,
    TIMING_COLLECTION,
    WATCH_COLLECTION,
    AnalyticsBook,
)
from .errors import (
    AnalyticsError,
    NotFound,
    PdfAnalyticsUnavailable,
    TrackingDisabled,
    UnknownEventType,
    ValidationError,
    VideoAnalyticsUnavailable,
)
from .inferences import INFERENCES, describe as describe_inferences
from .vocabulary import (
    ASSET_SNAPSHOT_FIELDS,
    ASSET_TYPES,
    AUDIENCES,
    GRAINS,
    INTERNAL_ONLY_METRICS,
    MIN_PAGES_FOR_PDF_ANALYTICS,
    SESSION_GAP_SECONDS,
    WEBHOOK_EVENT_TYPES,
)

__all__ = [
    "ASSET_COLLECTION",
    "ASSET_SNAPSHOT_FIELDS",
    "ASSET_TYPES",
    "AUDIENCES",
    "AnalyticsBook",
    "AnalyticsError",
    "EVENT_COLLECTION",
    "GRAINS",
    "INFERENCES",
    "INTERNAL_ONLY_METRICS",
    "MAX_TELEMETRY_ROWS",
    "MIN_PAGES_FOR_PDF_ANALYTICS",
    "NotFound",
    "PdfAnalyticsUnavailable",
    "SESSION_GAP_SECONDS",
    "TIMING_COLLECTION",
    "TrackingDisabled",
    "UnknownEventType",
    "ValidationError",
    "VideoAnalyticsUnavailable",
    "WATCH_COLLECTION",
    "WEBHOOK_EVENT_TYPES",
    "describe_inferences",
]
