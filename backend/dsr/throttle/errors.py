"""The refusals the throttle layer can make, in one hierarchy.

Every one of them is a caller error rather than a server fault, so the feature
module maps the whole hierarchy with a single registered handler and branches on
the type to pick a status. Two properties make that safe:

* the base is :class:`ThrottleError`, a type this package owns, so registering it
  globally cannot intercept an exception raised anywhere else in the product;
* every subclass is also a builtin a caller would recognise - ``LookupError`` for
  a connection or a batch that does not exist, ``ValueError`` for a payload or a
  policy that cannot be read - so a direct caller of the domain layer still
  catches something meaningful without importing this module.

There is deliberately no error for "the vendor throttled us". A throttle is the
subject of this workflow, not a fault in it: it is answered as a decision, and
the decision is recorded on the batch. Only a request this layer cannot make
sense of is refused.
"""

from __future__ import annotations


class ThrottleError(RuntimeError):
    """Base of every refusal :mod:`dsr.throttle` makes."""


class UnknownConnection(ThrottleError, LookupError):
    """A connection the caller named is not in the store."""


class UnknownBatch(ThrottleError, LookupError):
    """A batch the caller named is not in the store, or not on that room."""


class UnknownRoom(ThrottleError, LookupError):
    """The room the caller named is not in the store."""


class UnknownPolicy(ThrottleError, LookupError):
    """A rate-limit policy the caller named is not a registered one."""


class InvalidPayload(ThrottleError, ValueError):
    """A batch, a vendor response or a policy patch this layer cannot read."""


__all__ = [
    "ThrottleError",
    "UnknownConnection",
    "UnknownBatch",
    "UnknownRoom",
    "UnknownPolicy",
    "InvalidPayload",
]
