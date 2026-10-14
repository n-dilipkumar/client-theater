"""WF-132: in-room conversation and champion self-service - the domain package.

Four modules, in the order they depend on each other:

* :mod:`~dsr.inroom_collaboration.errors` - one error per refusal, with the HTTP
  status it maps to. No builtins, because a feature may only map its own types.
* :mod:`~dsr.inroom_collaboration.vocabulary` - the researched terms made
  executable, including the contested flag and what is deliberately absent.
* :mod:`~dsr.inroom_collaboration.rules` - the pure rules: comment bodies,
  mentions, anchors, roles, sides and unanswered questions.
* :mod:`~dsr.inroom_collaboration.engine` - the only module that reads or writes,
  over :class:`~dsr.store.RecordStore`.

The package imports nothing but the standard library and the store. It has no
FastAPI import, no SQLite handle, and no reference to the app, so the whole
workflow is testable without a server and the dependency direction stays one-way.
"""

from __future__ import annotations

from dsr.inroom_collaboration import rules, vocabulary
from dsr.inroom_collaboration.engine import InRoomEngine
from dsr.inroom_collaboration.errors import (
    InRoomConflict,
    InRoomDenied,
    InRoomError,
    InRoomNotFound,
    InRoomValidation,
)

__all__ = [
    "InRoomConflict",
    "InRoomDenied",
    "InRoomEngine",
    "InRoomError",
    "InRoomNotFound",
    "InRoomValidation",
    "rules",
    "vocabulary",
]
