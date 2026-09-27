"""One error hierarchy for the Play framework.

Every refusal this package makes is something the caller sent or asked for, so
the types share a base and the feature module registers a single handler for it.
Anything that is *not* a :class:`PlayError` is a bug and must propagate.

``code`` and ``status`` ride on the exception rather than being decided inside
the handler, for the same reason as in :mod:`dsr.signals.errors`: a play naming a
signal registration that does not exist and a play whose indicator list is empty
are both this package's domain errors, but only the first conflicts with state
that already exists, and a handler that answered 400 for both would be lying
about the second.

The distinction that matters most in this workflow is between *refused* and
*reported*. A play that is registered but not enabled is not an error - it is the
researched state of every new Play. A task whose assignment cannot be resolved is
not an error either - the task still exists, and saying so is more honest than
refusing to create it. Only a caller mistake raises.
"""

from __future__ import annotations


class PlayError(ValueError):
    """A play request cannot be honoured as written.

    A ``ValueError`` because every subclass is caused by something the caller
    sent or asked for. Nothing in this package raises for a fault of its own.
    """

    code = "play_error"
    status = 400


class FrameworkError(PlayError):
    """A Play framework cannot be registered, amended, or destroyed as described."""

    code = "play_framework_error"


class UnknownSignalRegistration(FrameworkError):
    """The play names a signal registration this product does not hold.

    "After registering a signal (see #12), register a Play" - the registration
    comes first, so a play pointing at nothing is a missing prerequisite rather
    than a typo. 409 rather than 400, and the message names the collection the
    lookup searched so a caller can tell a wrong id from a wrong integration.
    """

    code = "signal_registration_not_found"
    status = 409


class UndeclaredTrigger(FrameworkError):
    """A play names an indicator its signal registration does not declare.

    "When a matching signal arrives, Salesloft creates the task", and a signal
    can only carry indicators its registration declares. A trigger that can never
    appear on a signal is a play that can never fire, which is a mistake worth
    naming at registration rather than discovering in production.
    """

    code = "trigger_indicator_not_declared"
    status = 409


class FrameworkInUse(FrameworkError):
    """A Play framework is live and may not be removed.

    "After registration, the registered Play must be enabled in the Salesloft UI"
    and a live Play is this workflow's whole point - it creates tasks with no
    human in the loop. Disabling is the way to stop it; destroying is how a
    template is retired. The refusal is a reading of the activation sentence
    rather than a quotation, and is recorded as the ``destroy-requires-disable``
    inference.
    """

    code = "play_still_enabled"
    status = 409


class ActivationError(PlayError):
    """A Play framework cannot be switched on or off in the state it is in."""

    code = "play_activation_error"
    status = 409


class PlayNotFound(PlayError):
    """The Play named by a path does not exist, or has been destroyed.

    404, and a type of this package's own rather than the core
    :class:`~dsr.db.audited.RecordNotFound`. Claiming the core type would be an
    exception-handler collision the host refuses, and this feature has no use for
    the core one.
    """

    code = "play_not_found"
    status = 404


class DispatchError(PlayError):
    """A signal cannot be run against the registered Plays."""

    code = "play_dispatch_error"


class UnknownSignal(DispatchError):
    """The dispatch named a signal id that does not resolve to a stored signal.

    409, because the request is well formed and what it conflicts with is the
    state of the store - the same shape as the refusal WF-027 raises for an
    unregistered signal type.
    """

    code = "signal_not_found"
    status = 409


class EventError(PlayError):
    """An outcome event or a subscription body is malformed.

    400: everything raised from here is something the caller sent, and a caller who
    sends ``{}`` to record a delivery outcome has a bug to fix, not a conflict with
    state that already exists.
    """

    code = "play_event_error"


class TaskNotFound(PlayError):
    """The task named by a path does not exist, or is in another room.

    404, and deliberately *not* a room-membership error. This product's rooms carry
    no ACL - see the note on room scoping in
    :meth:`dsr.plays.engine.PlayEngine.generated_tasks` - so "not in this room" and
    "not there at all" are the same answer, and saying otherwise would be a claim
    the store cannot back.
    """

    code = "task_not_found"
    status = 404


class EventNotFound(PlayError):
    """The outcome event named by a path does not exist, or is in another room."""

    code = "play_event_not_found"
    status = 404


class DeliveryConflict(EventError):
    """A delivery is not in a state that can take the attempt being recorded.

    Distinct from :class:`EventError` because the status differs, and the two are
    the difference between "fix your request" and "this is already finished". A
    delivery that is ``delivered``, one that is ``failed``, and one whose next
    attempt is not yet due are all conflicts with state that exists.
    """

    code = "play_delivery_conflict"
    status = 409


class SubscriptionError(PlayError):
    """A webhook subscription body is malformed."""

    code = "play_webhook_error"


class SubscriptionNotFound(SubscriptionError):
    """The webhook subscription named by a path does not exist."""

    code = "webhook_subscription_not_found"
    status = 404
