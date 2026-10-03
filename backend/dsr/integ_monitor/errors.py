"""The refusals WF-049 can make, in one hierarchy.

Every one of them is either a caller error (a payload the room cannot turn into a
reading, a rule the room cannot enforce) or a lookup that found nothing, so the
feature module maps the whole hierarchy with one registered handler and branches
on the type to pick a status. Two properties make that safe:

* the base is :class:`MonitorError`, a type this workflow owns, so registering it
  globally cannot intercept an exception raised anywhere else in the product;
* every subclass is also a builtin the caller would recognise - ``LookupError``
  for a connector or a rule that does not exist, ``ValueError`` for a payload or
  a threshold that cannot be read - so a direct caller of the domain layer still
  catches something meaningful without importing this module.

There is deliberately no error for "the quota is exhausted" or "the stream lags".
A starved connector is the researched subject of this workflow, not a fault in
it: it is recorded as an observation, surfaced on the dashboard, and - past an
alert rule's threshold - fires an alert. Only a request this layer cannot make
sense of is refused.
"""

from __future__ import annotations


class MonitorError(RuntimeError):
    """Base of every refusal :mod:`dsr.integ_monitor` makes."""


class UnknownRoom(MonitorError, LookupError):
    """The room the caller named is not in the store."""


class UnknownVendor(MonitorError, LookupError):
    """A vendor name outside the researched three was asked for."""


class UnknownConnector(MonitorError, LookupError):
    """The connector the caller named is not in the store, or not on that room."""


class UnknownRule(MonitorError, LookupError):
    """The alert rule the caller named is not in the store, or not on that room."""


class InvalidPayload(MonitorError, ValueError):
    """A payload the room cannot turn into an observation or a rule."""


class InvalidQuotaSurface(MonitorError, ValueError):
    """A vendor quota surface the room cannot read: unparseable, or lying."""


class InvalidRule(MonitorError, ValueError):
    """An alert rule this room cannot evaluate: unknown metric, bad threshold."""


class InvalidTelemetry(MonitorError, ValueError):
    """A telemetry sample the room cannot aggregate."""


__all__ = [
    "MonitorError",
    "UnknownRoom",
    "UnknownVendor",
    "UnknownConnector",
    "UnknownRule",
    "InvalidPayload",
    "InvalidQuotaSurface",
    "InvalidRule",
    "InvalidTelemetry",
]
