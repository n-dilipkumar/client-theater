"""WF-129: quote-to-order-form inside the room, with a stand-in signature.

The domain package. It holds pure rules, vocabulary and errors, and it imports
the record store and nothing else: no FastAPI, no HTTP, no database module. The
feature module owns the routes.

The package is split rather than kept in one file because each part answers a
different question:

``vocabulary``
    The words. Collections, statuses, signature modes, refusal codes, and the
    ``not_implemented`` list that records what the research names and this
    repository does not build.
``errors``
    The three error types this domain raises. None is a builtin, deliberately.
``rules``
    The pure functions: the line arithmetic, the totals contract, the signature
    and redline rules, the document hash, the audit certificate, and the deal
    write-back.
``engine``
    The writes. Reads the store, produces records, never touches HTTP.

Nothing here is a migration and nothing here is a typed column. Every field is
ordinary JSON inside ``records.data``, so a team can add one without asking
anyone.

The exports
-----------

``vocabulary`` is deliberately **not** re-exported. The submodule
``dsr.inroom_ordering.vocabulary`` and a function named ``vocabulary`` share a
name, and re-exporting the function from the package binds the submodule name to
the function. It is reachable as ``OrderFormEngine.vocabulary()``.
"""

from __future__ import annotations

from dsr.inroom_ordering.engine import OrderFormEngine
from dsr.inroom_ordering.errors import OrderFormConflict, OrderFormError, OrderFormNotFound

__all__ = [
    "OrderFormConflict",
    "OrderFormEngine",
    "OrderFormError",
    "OrderFormNotFound",
]
