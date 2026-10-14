"""WF-119: gate an automation on external human approval and resume.

The domain package for the workflow. An automation reaches a pause node and the
contact is **held** there. A person decides in an external approval system, and
that system calls one of the two resume webhooks with the documented ``extras``
body. The workflow then continues on the branch the decision selected.

Why a package and not one module
--------------------------------

The feature module that owns the HTTP surface
(``dsr.features.wf119_approval_gate``) is a set of route declarations. Everything
a test wants to exercise without a request lives here, and the pieces are split
so the dependency direction stays one-way:

* :mod:`dsr.approval_gate.vocabulary` - the collections, the envelope, the
  outcomes, the hold window and every recorded decision. It imports nothing from
  ``dsr``.
* :mod:`dsr.approval_gate.errors` - the refusal hierarchy. It imports nothing
  from ``dsr``.
* :mod:`dsr.approval_gate.rules` - pure validation and the four-case resume
  decision over plain data and an explicit moment. It imports nothing from
  ``dsr``.
* :mod:`dsr.approval_gate.engine` - :class:`ApprovalGate`, the only module that
  touches :class:`dsr.store.RecordStore`, plus ``seed``.

The package reaches nothing else: no FastAPI, no ``dsr.deps``, no ``dsr.api``,
no bare ``sqlite3``. A test in ``backend/tests/test_wf119.py`` asserts that off
the package on disk, because the rule is about the dependency direction and about
writes going through the audited wrapper, not about banning the standard library.

What this workflow reads and does not own
-----------------------------------------

**The workflow and its branches.** WF-114 owns inbound workflow triggers and
WF-115 owns marketplace triggers and actions; neither owns a pause or a resume.
Nothing in this package writes to another feature's collections. The gate stores a
``workflow_id`` and an ``action_key`` as data, so a workflow that references this
pause node can be found, and no second writer of another feature's rows is
created.

**The approval decision itself.** WF-092 owns a sequential quote approval chain.
Its data flow ends in ``APPROVED``/``REJECTED`` on a *quote* and its research
never mentions a held contact or a resume webhook, so it is a vocabulary
neighbour and not a dependency. This workflow holds a contact and records the
branch, and does not touch WF-092's rows.

Verified against ``main`` at the branch base: no feature publishes a pause or a
resume route, so this workflow provisions the pause it needs rather than mounting
on a surface that does not exist.
"""

from __future__ import annotations

from . import errors, rules, vocabulary
from .engine import ApprovalGate, seed
from .errors import (
    REFUSAL_STATUS,
    ApprovalGateRefusal,
    BranchUnknown,
    EnvelopeInvalid,
    GateInvalid,
    GateNotFound,
    HoldConflict,
    HoldExpired,
    HoldNotFound,
    KeyTaken,
    OutcomeConflict,
    PauseDisabled,
    ResumeConflict,
)
from .vocabulary import COLLECTIONS, PREFIX, TICKET, decisions, not_built

__all__ = [
    "COLLECTIONS",
    "PREFIX",
    "REFUSAL_STATUS",
    "TICKET",
    "ApprovalGate",
    "ApprovalGateRefusal",
    "BranchUnknown",
    "EnvelopeInvalid",
    "GateInvalid",
    "GateNotFound",
    "HoldConflict",
    "HoldExpired",
    "HoldNotFound",
    "KeyTaken",
    "OutcomeConflict",
    "PauseDisabled",
    "ResumeConflict",
    "decisions",
    "errors",
    "not_built",
    "rules",
    "seed",
    "vocabulary",
]
