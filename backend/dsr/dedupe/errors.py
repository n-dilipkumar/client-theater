"""Domain errors for WF-041 duplicate detection.

One error type for the package, mapped once by the feature module. Every refusal
is the caller's to fix, so all of them answer 400.
"""

from __future__ import annotations


class DedupeError(ValueError):
    """A duplicate-detection request cannot be honoured as written."""


class AmbiguousMatch(DedupeError):
    """More than one record matched, so there is no single record to act on.

    Distinct from :class:`DedupeError` because this one is not the caller's typo:
    it is a legitimate state of the data, and the researched behaviour is a hard
    block rather than a refusal. Kept as its own type so the engine can catch it
    and turn it into a decision instead of letting it become a 400.
    """

    def __init__(self, message: str, *, matched: list[dict] | None = None) -> None:
        super().__init__(message)
        self.matched = matched or []


class UniqueIndexViolation(DedupeError):
    """A write was refused because a unique index forbids the duplicate.

    "The `Unique` attribute prevents the creation of duplicates." Surfacing this
    as its own type matters because it is the one case where a policy the
    administrator chose deliberately cannot be honoured.
    """
