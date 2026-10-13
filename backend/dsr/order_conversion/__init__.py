"""WF-101: convert an accepted quote into an order and lock the price.

The domain package for the quote-to-order workflow. It holds the pure rules, the
vocabulary, the errors and the judgement calls, and it reads and writes only
through the record store the feature hands in: no FastAPI, no HTTP, no database
module. The feature module owns the routes.

``vocabulary``
    Every word this workflow speaks, with the sentence it came from: the two
    organisation settings, the pricing modes, the seven clone components, the
    order statuses and the refusal reasons.
``errors``
    The three error types this domain raises. None is a builtin, deliberately.
``rules``
    The pure rules: what accepted and activated mean, the price-lock default,
    the clone, the refusal order and the repricing plan.
``engine``
    The reads and the writes: the settings, the conversion, the order, its
    lines, the pricing modes, the reprice, fulfilment and Get Products.
``inferences``
    The judgement calls the research left open, with the alternative each one
    rejected.

Nothing here is a migration and nothing here is a typed column. Every field is
ordinary JSON inside ``records.data``, so a team can add one without asking
anyone.

The research is ``docs/research/digital-sales-room-workflows/wf/WF-101.md``,
quoted in full in GitHub issue 192.
"""

from __future__ import annotations

from dsr.order_conversion import errors, inferences, rules, vocabulary
from dsr.order_conversion.engine import OrderConversionEngine
from dsr.order_conversion.errors import OrderConflict, OrderError, OrderNotFound

__all__ = [
    "OrderConflict",
    "OrderConversionEngine",
    "OrderError",
    "OrderNotFound",
    "errors",
    "inferences",
    "rules",
    "vocabulary",
]
