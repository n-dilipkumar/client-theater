"""The refusals this workflow can make, in one hierarchy.

Every one of them is a caller error rather than a server fault, so the feature
module maps the whole hierarchy with a single registered handler and branches on
the type to pick a status. Two properties make that safe:

* the base is :class:`TrendError`, a type this workflow owns, so registering it
  globally cannot intercept an exception raised anywhere else in the product;
* every subclass is also a builtin the caller would recognise - ``LookupError``
  for a room that does not exist, ``ValueError`` for a payload or a rule that
  cannot be parsed - so a direct caller of the domain layer still catches
  something meaningful without importing this module.
"""

from __future__ import annotations


class TrendError(RuntimeError):
    """Base of every refusal :mod:`dsr.trend_health` makes."""


class UnknownRoom(TrendError, LookupError):
    """The room a caller named is not in the store."""


class UnknownEventType(TrendError, ValueError):
    """An engagement event arrived that is not in the researched vocabulary."""


class InvalidTimestamp(TrendError, ValueError):
    """An ``occurredAt`` could not be read, or is further ahead than we allow."""


class InvalidRules(TrendError, ValueError):
    """A rules patch would produce a configuration the ladder cannot use."""


class InvalidSort(TrendError, ValueError):
    """A dashboard sort key or bucket filter is not one this workflow serves."""


__all__ = [
    "TrendError",
    "UnknownRoom",
    "UnknownEventType",
    "InvalidTimestamp",
    "InvalidRules",
    "InvalidSort",
]
