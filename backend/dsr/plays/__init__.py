"""WF-028: turn a signal into an automatic seller action (Play registration).

The workflow that closes the loop on a buyer-intent signal. A *Play* is "an
automation that generates a one-off action in response to an internal or external
signal" - so where :mod:`dsr.signals` decides that a buyer did something worth
knowing, this package decides what a seller is *given* because of it: a call, an
email, or a membership of a cadence.

The module layout, and why each piece is separate:

``vocabulary``    the values the research fixes by name, served as data
``errors``       one hierarchy, so the feature module registers one handler
``framework``    registering a Play, and what may be changed about one afterwards
``activation``   the enable switch - registration is not activation
``matching``     which Plays a signal fires, and why each of the others did not
``assignment``   User -> Content -> Person -> Account, and the Account fallback
``tasks``        the one-off action itself
``events``       the outcome events, and the researched webhook retry policy
``inferences``   every judgement call, named and served
``engine``       the facade the HTTP layer calls; owns the four collections

Two boundaries are worth stating before reading any of it.

**This package does not import :mod:`dsr.signals`.** It reads the
``signal_registration`` collection through the store, because a Play needs the
registration's indicator list and the signals' attribution, and because two
features authored independently must not share a Python module. The collection
name is :data:`dsr.plays.engine.SIGNAL_REGISTRATIONS` and the lookup is
:meth:`dsr.plays.engine.PlayEngine.declared_indicators`.

**Nothing here opens a socket.** The researched endpoints are the vendor's; this
product is the *source* of the buyer's event, not a proxy for the vendor. What is
real is the decision - which Plays match, who the task is assigned to, what a
retried webhook does next - and those are stored and audited rather than sent.
"""

from __future__ import annotations

from dsr.plays.activation import state as activation_state
from dsr.plays.assignment import describe as describe_assignment, resolve_assignment, window_start
from dsr.plays.engine import (
    EVENTS,
    FRAMEWORKS,
    SIGNAL_REGISTRATIONS,
    TASK,
    WEBHOOKS,
    PlayEngine,
)
from dsr.plays.errors import (
    ActivationError,
    DeliveryConflict,
    DispatchError,
    EventError,
    EventNotFound,
    FrameworkError,
    FrameworkInUse,
    PlayError,
    PlayNotFound,
    SubscriptionError,
    SubscriptionNotFound,
    TaskNotFound,
    UndeclaredTrigger,
    UnknownSignal,
    UnknownSignalRegistration,
)
from dsr.plays.events import delivery, next_attempt_at, normalise_subscription
from dsr.plays.framework import (
    amendment_findings,
    normalise_framework,
)
from dsr.plays.matching import match_plays, signal_indicator_keys
from dsr.plays.tasks import build_task, missing_for, render_subject

__all__ = [
    "EVENTS",
    "FRAMEWORKS",
    "SIGNAL_REGISTRATIONS",
    "TASK",
    "WEBHOOKS",
    "ActivationError",
    "DeliveryConflict",
    "DispatchError",
    "EventError",
    "EventNotFound",
    "FrameworkError",
    "FrameworkInUse",
    "PlayEngine",
    "PlayError",
    "PlayNotFound",
    "SubscriptionError",
    "SubscriptionNotFound",
    "TaskNotFound",
    "UnknownSignal",
    "UnknownSignalRegistration",
    "UndeclaredTrigger",
    "activation_state",
    "amendment_findings",
    "build_task",
    "delivery",
    "describe_assignment",
    "match_plays",
    "missing_for",
    "next_attempt_at",
    "normalise_framework",
    "normalise_subscription",
    "render_subject",
    "resolve_assignment",
    "signal_indicator_keys",
    "window_start",
]
