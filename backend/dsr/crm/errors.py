"""One error type for the CRM sync package.

Every refusal this package makes is the caller's fault and maps to ``400``, so
they share a base class and the API registers a single handler for it. Anything
that is *not* a ``CrmError`` is a bug and must propagate.
"""

from __future__ import annotations


class CrmError(ValueError):
    """A request cannot be honoured as written."""
