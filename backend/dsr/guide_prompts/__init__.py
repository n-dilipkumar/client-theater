"""WF-118: order, throttle and repeat in-app guide prompts.

The domain package for the workflow. A guide is ranked in an application's order
list, an app-level throttle sets the minimum gap between one automatic guide and
the next, and a per-guide repeat relaunches a guide after the visitor dismissed
it, on an interval the activation card sets.

Why a package and not one module
--------------------------------

The feature module that owns the HTTP surface
(``dsr.features.wf118_order_throttle_and_repeat_in_app_guide_prompts``) is a set
of route declarations. Everything a test wants to exercise without a request
lives here, and the pieces are split so the dependency direction stays one-way:

* :mod:`dsr.guide_prompts.vocabulary` - collections, units, the sourced rules and
  the recorded decisions. It imports nothing from ``dsr``.
* :mod:`dsr.guide_prompts.errors` - the refusal hierarchy. It imports nothing
  from ``dsr``.
* :mod:`dsr.guide_prompts.rules` - pure validation, ordering and eligibility over
  plain data and an explicit moment. It imports nothing from ``dsr``.
* :mod:`dsr.guide_prompts.engine` - :class:`GuidePrompts`, the only module that
  touches :class:`dsr.store.RecordStore`, plus ``seed``.

The package reaches nothing else: no FastAPI, no ``dsr.deps``, no ``dsr.api``,
no bare ``sqlite3``. A test in ``backend/tests/test_wf118.py`` asserts that off
the package on disk, because the rule is about the dependency direction and about
writes going through the audited wrapper, not about banning the standard library.
"""

from __future__ import annotations

from .engine import GuidePrompts, seed
from .errors import (
    EventInvalid,
    GuideInvalid,
    GuideNotFound,
    GuidePromptsRefusal,
    KeyImmutable,
    KeyInvalid,
    KeyTaken,
    OrderInvalid,
)
from .vocabulary import COLLECTIONS, MECHANISMS, SOURCES, decisions, vocabulary

__all__ = [
    "COLLECTIONS",
    "EventInvalid",
    "GuideInvalid",
    "GuideNotFound",
    "GuidePrompts",
    "GuidePromptsRefusal",
    "KeyImmutable",
    "KeyInvalid",
    "KeyTaken",
    "MECHANISMS",
    "OrderInvalid",
    "SOURCES",
    "decisions",
    "seed",
    "vocabulary",
]
