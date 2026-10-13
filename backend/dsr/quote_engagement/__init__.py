"""WF-097: track buyer engagement on a shared quote and drive follow-up.

The researched specification for this workflow is
``docs/research/digital-sales-room-workflows/wf/WF-097.md``, built from section
13 of ``docs/research/raw/quoting-proposals.md``. Three modules carry it, and none
of them knows about HTTP:

``vocabulary``
    The buyer and seller event labels, the two surfaces that decide whether an
    event counts as a view, the four counters, the activity classes and the
    follow-up reasons, each traced to the sentence in the research that states it.
``rules``
    The pure decisions: whether an event is a real view, how a count classifies
    against the threshold, what the four counters come to, and which quotes need a
    follow-up.
``engagement``
    The engine that applies those decisions. It records activity, maintains the
    counters on the quote record, and drives the follow-up send.

Nothing here imports ``dsr.api`` and nothing here opens SQLite. Every read and
write goes through the :class:`~dsr.store.RecordStore` the HTTP layer hands in, so
the audit row is written in the same transaction as the change.

Three decisions the research left open were put to Jev before the rules were
written, and each is recorded beside the rule it governs:

* where a count exactly equal to the threshold lands, audit
  ``jev-20261013T111832-11972-12671``, verdict ``pass`` at confidence 0.92;
* what the third counter reports with no email channel connected, audit
  ``jev-20261013T111835-5236-15033``, verdict ``pass`` at confidence 1.00;
* whether to ship a default threshold at all, audit
  ``jev-20261013T111655-4412-15602``, which chose to ship none.
"""

from dsr.quote_engagement.engagement import (
    EXPIRING_WITHIN_DAYS,
    NO_ACTIVITY_AFTER_DAYS,
    QuoteEngagementService,
    vocabulary,
)
from dsr.quote_engagement.rules import QuoteEngagementRuleError
from dsr.quote_engagement.vocabulary import ALL_EVENTS, BUYER_EVENTS, SELLER_EVENTS

__all__ = (
    "ALL_EVENTS",
    "BUYER_EVENTS",
    "EXPIRING_WITHIN_DAYS",
    "NO_ACTIVITY_AFTER_DAYS",
    "SELLER_EVENTS",
    "QuoteEngagementRuleError",
    "QuoteEngagementService",
    "vocabulary",
)
