"""WF-102: revise a quote to create a new revision instead of losing the deal.

The research is ``docs/research/raw/quoting-proposals.md`` section 18, quoted in
full in issue 196. A revision is a new quote record carrying the next revision
id while the prior record closes as Revised, so the deal history stays readable
instead of one record being overwritten.

The package holds the rules, the vocabulary and the errors. It holds no
framework: no FastAPI, no HTTP, and no clock a caller cannot replace. Every read
and write goes through the :class:`~dsr.store.RecordStore` the feature module
hands in, so the audit row is written in the same transaction as the change.

``vocabulary``
    The words. Statuses, the three close reasons, the two
    ``quotecreationmethod`` option values, the activity tokens, the recall gate
    and the evidence each value came from.
``errors``
    The three error types this workflow raises. None is a builtin, and none is
    shared with WF-086, so the feature host can map all three without a handler
    collision.
``rules``
    The pure rules: the revision increment, the open/closed boundary, the pricing
    boundary, the close fields, the recall gate, the project-quote scope and the
    line-copy helpers.
``engine``
    The reads and the writes, and the one place a transaction is opened.
"""

from __future__ import annotations

from dsr.quote_revision.engine import QuoteRevisionEngine
from dsr.quote_revision.errors import RevisionConflict, RevisionError, RevisionNotFound

#: The function, under an alias. Exporting it as ``vocabulary`` would bind the
#: package attribute ``dsr.quote_revision.vocabulary`` to the function and shadow
#: the submodule: the same collision WF-086's package documents at length.
from dsr.quote_revision.vocabulary import vocabulary as describe_vocabulary

__all__ = [
    "QuoteRevisionEngine",
    "RevisionConflict",
    "RevisionError",
    "RevisionNotFound",
    "describe_vocabulary",
]
