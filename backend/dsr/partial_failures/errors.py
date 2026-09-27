"""The refusals WF-040 can make, in one hierarchy.

Every one of them is a caller error rather than a server fault, so the feature
module maps the whole hierarchy with a single registered handler and branches on
the type to pick a status. Two properties make that safe:

* the base is :class:`PartialFailureError`, a type this workflow owns, so
  registering it globally cannot intercept an exception raised anywhere else in
  the product;
* every subclass is also a builtin the caller would recognise - ``LookupError``
  for a run or a row that does not exist, ``ValueError`` for a payload or a rule
  that cannot be read - so a direct caller of the domain layer still catches
  something meaningful without importing this module.

There is deliberately no error for "this row failed". A vendor rejecting a write
is the researched subject of this workflow, not a fault in it: it is recorded as
a row with a :class:`~dsr.partial_failures.normalise.NormalisedError` on it and
never raised. Only a request this layer cannot make sense of is refused.
"""

from __future__ import annotations


class PartialFailureError(RuntimeError):
    """Base of every refusal :mod:`dsr.partial_failures` makes."""


class UnknownConnector(PartialFailureError, LookupError):
    """A connector name outside the researched three was asked for."""


class UnknownRun(PartialFailureError, LookupError):
    """The sync run the caller named is not in the store."""


class UnknownRow(PartialFailureError, LookupError):
    """The row the caller named is not in the store, or not on that room."""


class UnknownRoom(PartialFailureError, LookupError):
    """The room the caller named is not in the store."""


class InvalidPayload(PartialFailureError, ValueError):
    """A sync-run result payload the room cannot turn into per-row outcomes."""


class InvalidRule(PartialFailureError, ValueError):
    """A rules patch would produce a configuration the classifier cannot use."""


__all__ = [
    "PartialFailureError",
    "UnknownConnector",
    "UnknownRun",
    "UnknownRow",
    "UnknownRoom",
    "InvalidPayload",
    "InvalidRule",
]
