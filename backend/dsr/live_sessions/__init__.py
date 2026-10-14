"""WF-128: in-room live presentation / co-browsing.

The workflow is issue #138, whose source is
``docs/research/digital-sales-room-workflows/wf/WF-128.md``. This package is its
domain: pure rules, a closed vocabulary and one error per distinct answer.

* :mod:`dsr.live_sessions.vocabulary` - the closed word set, the sourced quotes
  and the two inferences the research left open, including the stack choice the
  issue asks for (SFU and deck engine, each with its licence and reason).
* :mod:`dsr.live_sessions.errors` - one error per answer, so each maps to one
  HTTP status.
* :mod:`dsr.live_sessions.rules` - pure functions of their arguments. No store,
  no framework, no I/O.
* :mod:`dsr.live_sessions.engine` - the only module that reads or writes, and it
  does so through :class:`~dsr.store.RecordStore` alone.

No realtime transport exists in this product, so the session is modelled as
state, roles and an event log. A follower reads the presenter's cursor by
polling. The live link and the recording are stand-ins, and the vocabulary says
so.
"""

from __future__ import annotations

from dsr.live_sessions import rules, vocabulary
from dsr.live_sessions.engine import LiveSessionBook
from dsr.live_sessions.errors import (
    SessionConflict,
    SessionError,
    SessionNotFound,
    SessionValidationError,
)

__all__ = [
    "LiveSessionBook",
    "SessionConflict",
    "SessionError",
    "SessionNotFound",
    "SessionValidationError",
    "rules",
    "vocabulary",
]
