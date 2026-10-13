"""WF-115: the domain package for publishing custom workflow triggers and actions.

A third-party builder authors a **trigger** or an **action** for the workflow
marketplace, submits it for review, and once it is approved and published the
module becomes a reusable primitive for every sub-account. This package is the
whole of that domain model: the vocabulary, the validation rules, the review
state machine, and a service object over the record store.

Why a package and not one module
--------------------------------

The feature module that owns the HTTP surface (``dsr.features.wf115_...``) is a
set of route declarations. Everything a test wants to exercise without a request
lives here, and the pieces are split so the dependency direction stays one-way:

* :mod:`dsr.marketplace_primitives.vocabulary` - constants and the sourced rules.
  It imports nothing from ``dsr``.
* :mod:`dsr.marketplace_primitives.errors` - the refusal hierarchy. It imports
  nothing from ``dsr``.
* :mod:`dsr.marketplace_primitives.rules` - pure validation and evaluation
  functions over plain data. It imports nothing from ``dsr``.
* :mod:`dsr.marketplace_primitives.engine` - :class:`Marketplace`, the only
  module here that touches :class:`dsr.store.RecordStore`.

The package reaches nothing else: no FastAPI, no ``dsr.deps``, no ``dsr.api``, no
bare ``sqlite3``. A test in ``backend/tests/test_wf115.py`` asserts that off the
package on disk, because the contract's rule is about the dependency direction
and about writes going through the audited wrapper, not about banning the
standard library.
"""

from __future__ import annotations

from .engine import Marketplace, seed
from .errors import (
    DynamicFilterLimitExceeded,
    KeyImmutable,
    KeyInvalid,
    KeyTaken,
    MarketplaceRefusal,
    ModuleInvalid,
    ModuleNotFound,
    ModuleNotPublished,
    TransitionRefused,
)
from .vocabulary import COLLECTIONS, KINDS, REVIEW_STATES, vocabulary

__all__ = [
    "COLLECTIONS",
    "KINDS",
    "REVIEW_STATES",
    "DynamicFilterLimitExceeded",
    "KeyImmutable",
    "KeyInvalid",
    "KeyTaken",
    "Marketplace",
    "MarketplaceRefusal",
    "ModuleInvalid",
    "ModuleNotFound",
    "ModuleNotPublished",
    "TransitionRefused",
    "seed",
    "vocabulary",
]
