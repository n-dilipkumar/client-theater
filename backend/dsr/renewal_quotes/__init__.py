"""WF-100: renewal quoting.

A seller opens an expiring contract and creates a renewal quote from it against a chosen
template and a chosen change effective date. On acceptance the room creates the new
contract, links it back to the prior contract as a renewal chain, and creates the renewal
deal.

This module holds the rules and the vocabulary. It holds no framework: no FastAPI, no
HTTP, no clock of its own that a caller cannot replace.
"""

from __future__ import annotations

from dsr.renewal_quotes.engine import RenewalQuoteEngine
from dsr.renewal_quotes.errors import (
    RenewalConflict,
    RenewalNotFound,
    RenewalRefusal,
)
from dsr.renewal_quotes.vocabulary import describe as describe_vocabulary

__all__ = [
    "RenewalConflict",
    "RenewalNotFound",
    "RenewalQuoteEngine",
    "RenewalRefusal",
    "describe_vocabulary",
]
