"""WF-106: trigger outreach on high-intent page visits.

The researched workflow, in full. A prospect browses a seller's pricing page more
than once. The room matches the page view against the workflow's targeting rules,
counts the visits inside the window, checks the *Show workflow until* mode and the
session damper, and shows an in-app block. The buyer's answer is recorded as a
``content_stat`` receipt, and the block is hidden for the rest of the session.

What is in here
---------------

:mod:`dsr.page_outreach.vocabulary`
    Every published name, number and threshold, with the quote beside each sourced
    one and the derivation beside each derived one. ``sourced: False`` is the label
    that matters: the vendor threshold for a repeat count was never published.

:mod:`dsr.page_outreach.rules`
    The pure rules. A page view in, a show decision and a reason out. No store, no
    framework and no clock: the moment and the visit history are arguments.

:mod:`dsr.page_outreach.errors`
    One hierarchy, so the feature module registers a single handler.

:mod:`dsr.page_outreach.engine`
    The only module that writes. Every writing call takes ``source`` as a required
    keyword so the audit row names the route that served it.

:mod:`dsr.page_outreach.inferences`
    Seventeen judgements the research left open, each with the reading taken, what
    would change it and the risk of having got it wrong. Served at
    ``GET /api/wf-106/inferences``.

The package imports nothing but the store and the standard library.

What this build deliberately does not do
-----------------------------------------

It posts no message to any vendor. The research describes the Messenger in the
vendor's product and ``POST /messages`` with ``message_type: in_app`` as its API
equivalent; this product is a sales room with no outbound transport, so a delivery
record says what would be shown and ``DELIVERY_LIMITS`` says so in words on the
vocabulary route and at the foot of the page.

It runs the snippet. The page view is collected by an external JavaScript snippet on
the seller's site, and the room owns the rules and the receipts rather than the
collection. The ingest route therefore takes one page view per call and lets the
caller own batching.

Both refusals are recorded in :mod:`dsr.page_outreach.inferences` rather than left
as omissions, so neither is invisible to a seller reading the page.
"""

from __future__ import annotations

from dsr.page_outreach.engine import INTERACTION_KINDS, MAX_WINDOW_DAYS, PageOutreach
from dsr.page_outreach.errors import (
    DuplicateWorkflow,
    InvalidInteraction,
    InvalidPageView,
    InvalidWorkflow,
    PageOutreachError,
    UnknownDelivery,
    UnknownPageView,
    UnknownPageViewField,
    UnknownPath,
    UnknownWorkflow,
)
from dsr.page_outreach.inferences import INFERENCES, inferences
from dsr.page_outreach.rules import (
    PageView,
    RuleMatch,
    ShowDecision,
    count_matching_visits,
    decide,
    frequency_decision,
    is_engagement,
    is_session_hiding,
    match_rule,
    match_rules,
    mode_stops_on,
    normalise_path,
    parse_page_view,
    path_matches,
    rule_table,
    rules_matched,
    session_decision,
    utm_matches,
)
from dsr.page_outreach.vocabulary import (
    APP_KINDS,
    BLOCK_KINDS,
    CHANNELS,
    COLLECTIONS,
    DELIVERY_LIMITS,
    DELIVERY_STATES,
    DWELL_SECONDS,
    ENGAGEMENT_INTERACTIONS,
    FREQUENCY_MODES,
    INTERACTIONS,
    REPEAT_VISITS,
    REPEAT_WINDOW_DAYS,
    SESSION_HIDING_INTERACTIONS,
    TARGET_RULE_KINDS,
    THRESHOLDS,
    TRIGGER_PANES,
    UNSOURCED_LIMITS,
    URL_MATCH_MODES,
    WORKFLOW_STATES,
)

__all__ = [
    "APP_KINDS",
    "BLOCK_KINDS",
    "CHANNELS",
    "COLLECTIONS",
    "DELIVERY_LIMITS",
    "DELIVERY_STATES",
    "DWELL_SECONDS",
    "DuplicateWorkflow",
    "ENGAGEMENT_INTERACTIONS",
    "FREQUENCY_MODES",
    "INFERENCES",
    "INTERACTIONS",
    "INTERACTION_KINDS",
    "InvalidInteraction",
    "InvalidPageView",
    "InvalidWorkflow",
    "MAX_WINDOW_DAYS",
    "PageOutreach",
    "PageOutreachError",
    "PageView",
    "REPEAT_VISITS",
    "REPEAT_WINDOW_DAYS",
    "RuleMatch",
    "SESSION_HIDING_INTERACTIONS",
    "ShowDecision",
    "TARGET_RULE_KINDS",
    "THRESHOLDS",
    "TRIGGER_PANES",
    "UNSOURCED_LIMITS",
    "URL_MATCH_MODES",
    "UnknownDelivery",
    "UnknownPageView",
    "UnknownPageViewField",
    "UnknownPath",
    "UnknownWorkflow",
    "WORKFLOW_STATES",
    "count_matching_visits",
    "decide",
    "frequency_decision",
    "inferences",
    "is_engagement",
    "is_session_hiding",
    "match_rule",
    "match_rules",
    "mode_stops_on",
    "normalise_path",
    "parse_page_view",
    "path_matches",
    "rule_table",
    "rules_matched",
    "session_decision",
    "utm_matches",
]
