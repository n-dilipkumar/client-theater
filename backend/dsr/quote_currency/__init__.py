"""WF-089: quote in a transaction currency with FX conversion.

The researched specification is ``docs/research/digital-sales-room-workflows/wf/WF-089.md``,
quoted in full in issue 146. This package is that specification made executable, and nothing
here knows about HTTP.

The modules, and what each one owns
-----------------------------------

``vocabulary``
    Every constant named after the specification: the collections, the money field names, the
    two ``PricingErrorCode`` values, the four recalculation triggers, and the two sourced
    sentences the whole workflow turns on. Nothing is a house opinion.
``errors``
    The four error types this workflow raises, declared in one place so the HTTP layer can map
    them without any chance of two features claiming the same type.
``rules``
    The pure arithmetic and the two refusals. Decimal money, one rate direction, one pricing
    order, and no framework.
``inferences``
    Every judgement call with the alternative it rejected and the cost of that rejection.
``engine``
    The workflow over an audited store: currencies, price lists, quotes, and a pricing run
    that records itself whether it priced or refused.

The dependency direction
------------------------

This package imports ``dsr.store`` and nothing else inside ``dsr``. It never imports
``dsr.api`` and never opens SQLite, which ``backend/tests/test_wf089.py`` enforces with an
AST walk rather than a substring search.

Read the module docstring of :mod:`dsr.quote_currency.vocabulary` for the two sourced
sentences this workflow is built on, and :mod:`dsr.quote_currency.inferences` for the nine
decisions the research left open.
"""

from __future__ import annotations

from dsr.quote_currency.engine import QuoteCurrencyEngine
from dsr.quote_currency.errors import (
    CurrencyChangeRefused,
    CurrencyRefusal,
    QuoteNotFound,
    RateUnavailable,
)

__all__ = [
    "CurrencyChangeRefused",
    "CurrencyRefusal",
    "QuoteCurrencyEngine",
    "QuoteNotFound",
    "RateUnavailable",
]
