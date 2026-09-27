"""One error hierarchy for the buyer-intent signal package.

Every refusal this package makes is a caller's mistake, so the types share a base
and the feature module registers a single handler for it. Anything that is *not*
a :class:`SignalError` is a bug and must propagate.

``code`` and ``status`` ride on the exception rather than being decided inside the
handler. A duplicate registration and a missing required field are both this
package's domain errors, but only one of them is a conflict with state that
already exists, and a handler that answered 400 for both would be lying about the
second. FastAPI only accepts exception handlers on the app object, so the feature
module exports this mapping as ``EXCEPTION_HANDLERS``; two features may not map
the same type, which is why the whole hierarchy hangs off one base class.
"""

from __future__ import annotations


class SignalError(ValueError):
    """A signal request cannot be honoured as written.

    A ``ValueError`` because every subclass is caused by something the caller
    sent. Nothing in this package raises for a fault of its own.
    """

    code = "signal_error"
    status = 400


class RegistrationError(SignalError):
    """A signal type cannot be registered as described."""

    code = "signal_registration_error"


class DuplicateSignalType(RegistrationError):
    """"Per integration, the signal type can only be registered once."

    The research states this as a property of the vendor's registry, and it is
    also the property that makes a registration a *contract* rather than a
    template: two registrations of the same type would give one type two
    incompatible shapes and no way to tell which one a signal follows.
    """

    code = "signal_type_already_registered"
    status = 409


class ImmutableContractError(RegistrationError):
    """"Globally installed signals ... only additive changes will be allowed."

    Raised by :func:`dsr.signals.registration.amendment_findings` when a patch
    would invalidate something that was already valid, or would rewrite a string
    a seller has already read. The findings name every offending path at once so
    one attempt does not have to be repeated once per field.
    """

    code = "signal_contract_change_refused"
    status = 409


class EmissionError(SignalError):
    """A signal cannot be delivered as described."""

    code = "signal_emission_error"


class UnregisteredSignalType(EmissionError):
    """No registration matches the ``type`` being emitted.

    409 rather than 400: the request is well formed, and what it conflicts with
    is the state of the registry. The researched flow registers a signal type
    once and then emits against it, so this is a missing prerequisite rather than
    a typo - though the message says which integration was searched, because the
    same ``type`` is legitimately registrable on two different integrations.
    """

    code = "signal_type_not_registered"
    status = 409


class UndeclaredIndicator(EmissionError):
    """A signal named an indicator its registration does not declare.

    "Signals must follow the structure defined on the Signal Registration." An
    indicator is the part of a signal a seller reads, so an undeclared one cannot
    be rendered and must not be stored.
    """

    code = "indicator_not_declared"


class IndicatorNotQualified(EmissionError):
    """A signal's own indicator evidence does not support the indicator's claim.

    "Indicators should be very specific." A specific claim carries its bound, and
    an observation that fails the bound is a signal that says something untrue.
    Refusing is the right answer on the strict emit route; the classifier route in
    :mod:`dsr.signals.engine` is where a non-qualifying interaction belongs.
    """

    code = "indicator_bound_not_met"


class RenderError(SignalError):
    """A localized description could not be rendered at all.

    Distinct from an argument going missing. A missing argument degrades the
    sentence and is reported as a warning; this is raised only when the shape of
    the message is not one the renderer understands, which is a defect in the
    registration rather than in the caller's signal.
    """

    code = "signal_render_error"
