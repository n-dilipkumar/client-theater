"""Nurture series: rule, content, wait and tag blocks with channel fallback (WF-104).

The domain lives here and imports nothing but the store. This package is shared
ground: WF-105 extends the same rule-block vocabulary - contact attributes and
company attributes - so those live in :mod:`dsr.nurture_series.vocabulary` and are
exported rather than inlined at a call site.

Modules:

* :mod:`dsr.nurture_series.vocabulary` - the researched terms, the block kinds, the
  filter fields, the event vocabulary and the published failure codes.
* :mod:`dsr.nurture_series.errors` - the refusals this workflow raises.
* :mod:`dsr.nurture_series.rules` - the pure rules: block validation, filter
  evaluation, the send-window and offline-branch decisions, the poll interval.
* :mod:`dsr.nurture_series.inferences` - every judgement call the research left open.
* :mod:`dsr.nurture_series.engine` - the state machine over the store.
"""

from __future__ import annotations

from dsr.nurture_series.engine import NurtureSeriesEngine
from dsr.nurture_series.errors import RunNotFound, SeriesNotFound, SeriesRefused
from dsr.nurture_series.vocabulary import (
    BLOCK_KINDS,
    CHANNELS,
    COMPANY_ATTRIBUTES,
    CONTACT_ATTRIBUTES,
    DISPATCH_COLLECTION,
    ENGAGEMENT_COLLECTION,
    ENGAGEMENT_EVENTS,
    FILTER_OPERATORS,
    POLL_INTERVAL_SECONDS,
    RUN_COLLECTION,
    SERIES_COLLECTION,
)

__all__ = [
    "BLOCK_KINDS",
    "CHANNELS",
    "COMPANY_ATTRIBUTES",
    "CONTACT_ATTRIBUTES",
    "DISPATCH_COLLECTION",
    "ENGAGEMENT_COLLECTION",
    "ENGAGEMENT_EVENTS",
    "FILTER_OPERATORS",
    "NurtureSeriesEngine",
    "POLL_INTERVAL_SECONDS",
    "RUN_COLLECTION",
    "RunNotFound",
    "SERIES_COLLECTION",
    "SeriesNotFound",
    "SeriesRefused",
]
