"""WF-033: auto-add and continuously track in-market companies from intent signals.

The buyer-intent workflow, as a package rather than as one module, so no two
features can claim one path and so the pieces can be tested on their own:

============================  ==========================================
:mod:`~dsr.market_intent.domains`   the hierarchical root-domain model
:mod:`~dsr.market_intent.timeframe`  the 90-day, midnight-UTC, last-visit window
:mod:`~dsr.market_intent.criteria`   intent criteria and the five path filters
:mod:`~dsr.market_intent.observations` page views and research observations
:mod:`~dsr.market_intent.table`      the buyer-intent table, computed
:mod:`~dsr.market_intent.views`      saved views, and when a company entered one
:mod:`~dsr.market_intent.credits`    the one-row-per-company-per-period ledger
:mod:`~dsr.market_intent.vocabulary` every published value, served as data
:mod:`~dsr.market_intent.inferences` every decision the research does not make
:mod:`~dsr.market_intent.engine`     the façade the HTTP layer calls
============================  ==========================================

The specification is ``docs/research/digital-sales-room-workflows/wf/WF-033.md``,
which is section 18 of ``docs/research/raw/analytics-intent.md``. Every rule in
this package cites the sentence it implements, in the docstring of the function
that implements it, so a reviewer can check the behaviour against the
specification without reading the tests first.

The one rule that shapes the rest of the package is the vendor's own warning:
"auto-add will only add companies that enter your saved views after enabling the
auto-add. It will not add all existing companies in your saved views." It is why
:meth:`~dsr.market_intent.engine.MarketIntentEngine.run` reports what it *did
not* add, and why :func:`dsr.market_intent.views.entered_at` derives an entry
time from the observation stream rather than stamping it at run time.
"""

from __future__ import annotations

from dsr.market_intent.credits import charge, period_for, summarise
from dsr.market_intent.criteria import (
    PATH_OPERATOR_LABELS,
    PATH_OPERATORS,
    Criterion,
    PageFilter,
    derived_properties,
    parse_page_filters,
    qualify_view,
)
from dsr.market_intent.domains import (
    DomainInfo,
    display_name,
    is_valid_domain,
    resolve,
    root_domain,
    same_company,
)
from dsr.market_intent.engine import MarketIntentEngine
from dsr.market_intent.errors import (
    AlreadyExcluded,
    CreditsRequired,
    DomainExcluded,
    DuplicateViewName,
    EnrichmentPermissionRequired,
    InvalidAutomation,
    InvalidConfiguration,
    InvalidObservation,
    InvalidPathFilter,
    InvalidSort,
    InvalidTimeframe,
    LifecycleStageRegression,
    MarketIntentError,
    TimeframeTooLong,
    UnknownCategory,
    UnknownVocabularyValue,
)
from dsr.market_intent.inferences import INFERENCES, describe as describe_inferences
from dsr.market_intent.observations import (
    normalise_research,
    normalise_visit,
    parse_country,
)
from dsr.market_intent.table import Snapshot, build_rows, matches_filters, sort_rows
from dsr.market_intent.timeframe import (
    DEFAULT_DAYS,
    MAX_DAYS,
    Window,
    midnight_utc,
    resolve_window,
)
from dsr.market_intent.views import FilterSet, Membership, entered_at, memberships
from dsr.market_intent.vocabulary import (
    AUTOMATION_ADD,
    AUTOMATION_LABELS,
    AUTOMATION_TRACK,
    CATEGORY_IDS,
    CATEGORY_REQUIREMENTS,
    CREDIT_COST_ADD,
    CREDIT_COST_TRACK,
    NEWS_SIGNAL_TYPES,
    RECORD_SOURCE_BUYER_INTENT,
    SORT_KEYS,
    TRAFFIC_SOURCES,
    describe as describe_vocabulary,
)

__all__ = [
    "AUTOMATION_ADD",
    "AUTOMATION_LABELS",
    "AUTOMATION_TRACK",
    "AlreadyExcluded",
    "CATEGORY_IDS",
    "CATEGORY_REQUIREMENTS",
    "CREDIT_COST_ADD",
    "CREDIT_COST_TRACK",
    "CreditsRequired",
    "Criterion",
    "DEFAULT_DAYS",
    "DomainExcluded",
    "DomainInfo",
    "DuplicateViewName",
    "EnrichmentPermissionRequired",
    "FilterSet",
    "INFERENCES",
    "InvalidAutomation",
    "InvalidConfiguration",
    "InvalidObservation",
    "InvalidPathFilter",
    "InvalidSort",
    "InvalidTimeframe",
    "LifecycleStageRegression",
    "MAX_DAYS",
    "MarketIntentEngine",
    "MarketIntentError",
    "Membership",
    "NEWS_SIGNAL_TYPES",
    "PATH_OPERATORS",
    "PATH_OPERATOR_LABELS",
    "PageFilter",
    "RECORD_SOURCE_BUYER_INTENT",
    "SORT_KEYS",
    "Snapshot",
    "TimeframeTooLong",
    "TRAFFIC_SOURCES",
    "UnknownCategory",
    "UnknownVocabularyValue",
    "Window",
    "build_rows",
    "charge",
    "derived_properties",
    "describe_inferences",
    "describe_vocabulary",
    "display_name",
    "entered_at",
    "is_valid_domain",
    "matches_filters",
    "memberships",
    "midnight_utc",
    "normalise_research",
    "normalise_visit",
    "parse_country",
    "parse_page_filters",
    "period_for",
    "qualify_view",
    "resolve",
    "resolve_window",
    "root_domain",
    "same_company",
    "sort_rows",
    "summarise",
]
