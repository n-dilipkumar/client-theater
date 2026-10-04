"""WF-078: require recipient identity verification before open or sign.

The researched specification is ``docs/research/digital-sales-room-workflows/wf/WF-078.md``,
quoted in full in issue 172. This package is that specification made executable, and it is
where the workflow lives: the router in ``dsr.features`` is HTTP, and everything a caller
actually relies on is here and testable without a request.

Four modules carry it, and none of them knows about HTTP.

``vocabulary``
    Every researched term, quoted from the specification, with the evidence it came
    from. The four methods, the two gates, the passcode bound, the E.164 bound, the
    SMS role axis, and the two honesty sentences every response carries.
``rules``
    The two independent axes and how they compose, the validation of each method's
    own configuration, the evaluation of one attempt, and the errors this workflow
    raises.
``inferences``
    Every judgement call the specification left open, each recorded with the
    alternative this build rejected and what that alternative would have cost.
``engine``
    The writes: a document, a recipient with its verification settings, an attempt
    that passes or fails, and the withholding of the body until it clears.

Where the integer audit codes come from
---------------------------------------

They are not defined here, and that is deliberate.

``dsr.audit_export.vocabulary`` is WF-079's and it already owns the action-code enum:
it already lays out 47-50 as four pass codes and 51-54 as four fail codes, already
orders the methods ``(kba, passcode, sms, id)``, and already publishes
``verification_code(method, outcome)``. Minting a second table here would produce two
sources for the same integer, and the second one would drift the first time a team
reordered a method.

So this package carries the *facts* of an attempt - which method, which gate, which
outcome, when - and the caller supplies the integer. :meth:`IdentityVerificationEngine.attempt`
takes a ``code_for`` callable and calls it with the method and the outcome it just
decided. ``dsr.features.wf078_require_recipient_identity_verification`` passes
``dsr.audit_export.vocabulary.verification_code``. The engine therefore has no import
edge to another workflow's package, and the codes still have exactly one definition.

Nothing here imports ``dsr.api``, and nothing here opens SQLite. Every read and write
goes through the :class:`~dsr.store.RecordStore` the HTTP layer hands in, so the audit
row is written in the same transaction as the change.
"""

from __future__ import annotations

from dsr.identity_verification import engine, inferences, rules, vocabulary  # noqa: F401

__all__ = ["engine", "inferences", "rules", "vocabulary"]
