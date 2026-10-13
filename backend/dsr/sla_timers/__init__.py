"""WF-108: run SLA response timers with office-hour awareness.

Automations and engagement. The research is quoted in full in issue #151.

Four modules carry the workflow, and none of them knows about HTTP:

``vocabulary``
    Every term this workflow enforces against: the four metrics, the two object kinds,
    the two pause rules, the three badges, the three report outcomes, and every refusal
    code. The conversation states and the office-hours vocabulary are WF-107's and are
    re-exported rather than re-declared, so two workflows cannot disagree about what
    ``snoozed`` means.
``rules``
    The pure rules: the office-hours working-time arithmetic, the pause intervals, the
    office-hours-aware deadline, the badge, the timer state, the reconciliation of the
    pause rule with the auto-unsnooze rule, and the hit/miss/fixed classification. Plus
    the error types this workflow raises.
``inferences``
    The judgement calls the specification left open, each recorded with the alternative
    rejected and the Jev audit that settled the mechanism question.
``engine``
    The reads and the writes: policies, one active timer per conversation, the sweep, the
    inbox view and the report.

Nothing here imports ``dsr.api``, and nothing here opens SQLite. Every read and write
goes through the :class:`~dsr.store.RecordStore` the HTTP layer hands in, so the audit
row is written in the same transaction as the change.

What this workflow does not claim
---------------------------------

**That a message left this product.** This feature records when a reply is due; delivery
is the integrator's. Every response that could be read as a send carries the same
``sent_by_this_product: False`` honesty flag WF-107 uses.

**An inbound webhook surface.** The research's extensibility names webhook topics. The
product's inbound-webhook surface is WF-082; this feature publishes the topic names it
observes and serves no receiver of its own.
"""

from dsr.sla_timers import (  # noqa: F401
    engine,
    inferences,
    rules,
    vocabulary,
)

__all__ = ["engine", "inferences", "rules", "vocabulary"]
