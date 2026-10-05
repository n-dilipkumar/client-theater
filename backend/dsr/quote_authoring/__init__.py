"""WF-086: author a quote from a deal or opportunity.

The domain package for pricing, quoting and proposals. It holds pure rules,
vocabulary and errors. It imports the record store and nothing else: no FastAPI,
no HTTP, no database module. The feature module owns the routes.

The package is split rather than kept in one file because each part answers a
different question, and a reader who only wants the price arithmetic should not
have to read the publish rule to find it:

``vocabulary``
    The words. Statuses, discount types, module keys, the collection names, and
    the ``not_implemented`` list that records what the research says cannot be
    done through an API.
``errors``
    The three error types this domain raises. None is a builtin, deliberately.
``pricing``
    Money, tiers, discounts, tax and the totals contract.
``engine``
    The writes. Reads the store, produces records, never touches HTTP.

Nothing here is a migration and nothing here is a typed column. Every field is
ordinary JSON inside ``records.data``, so a team can add one without asking
anyone.

The exports
-----------

``vocabulary`` is deliberately **not** re-exported. The submodule
``dsr.quote_authoring.vocabulary`` and the function
``dsr.quote_authoring.vocabulary.vocabulary`` share a name, and re-exporting
the function from the package bound ``dsr.quote_authoring.vocabulary`` to the
function. ``from dsr.quote_authoring import vocabulary as vocab`` then handed
back the function, and every ``vocab.BUILTIN_MODULE_KEYS`` in a test raised
``AttributeError``. It is reachable as ``QuoteEngine.vocabulary()``.
"""

from __future__ import annotations

from dsr.quote_authoring.engine import QuoteEngine
from dsr.quote_authoring.errors import QuoteConflict, QuoteError, QuoteNotFound
from dsr.quote_authoring.pricing import (
    as_number,
    as_text,
    line_amounts,
    money,
    resolve_unit_price,
    totals_for,
)

__all__ = [
    "QuoteConflict",
    "QuoteEngine",
    "QuoteError",
    "QuoteNotFound",
    "as_number",
    "as_text",
    "line_amounts",
    "money",
    "resolve_unit_price",
    "totals_for",
]
