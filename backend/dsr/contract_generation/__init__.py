"""WF-099: auto-create a contract from an accepted quote.

The domain package for pricing, quoting and proposals. It holds the pure rules,
the vocabulary and the errors, and it reads and writes only through the record
store the feature hands in: no FastAPI, no HTTP, no database module. The feature
module owns the routes.

``vocabulary``
    Every word this workflow speaks, with the sentence it came from. The setting,
    the four effective start date modes and the field each one needs, the flow
    the scenario matrix produces, the legacy rule, and the refusal reasons.
``errors``
    The three error types this domain raises. None is a builtin, deliberately.
``rules``
    The pure rules: the quote's state, how a contract inherits the quote's line
    items and terms, the effective start date, and the scenario matrix.
``engine``
    The reads and the writes: the setting, the acceptance ledger, the contract,
    the order and the generation record.

Nothing here is a migration and nothing here is a typed column. Every field is
ordinary JSON inside ``records.data``, so a team can add one without asking
anyone.

The research is ``docs/research/digital-sales-room-workflows/wf/WF-099.md``,
quoted in full in GitHub issue 195.
"""

from __future__ import annotations

from dsr.contract_generation import errors, rules, vocabulary
from dsr.contract_generation.engine import ContractEngine
from dsr.contract_generation.errors import ContractConflict, ContractError, ContractNotFound

__all__ = [
    "ContractConflict",
    "ContractEngine",
    "ContractError",
    "ContractNotFound",
    "errors",
    "rules",
    "vocabulary",
]
