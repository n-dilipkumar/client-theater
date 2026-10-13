"""WF-109: escalate on SLA breach with a pre-warning head start.

Automations and engagement. The research is quoted in full in issue #139.

Four modules carry the workflow, and none of them knows about HTTP:

``vocabulary``
    Every term this workflow enforces against: the two triggers under the **During a
    conversation** category, the four SLA targets, the five action kinds, the escalation
    levels, the three cancellation conditions, the Data Connector execution result and
    every refusal code. The SLA metrics and timer are WF-108's and the conversation
    vocabulary is WF-107's; both are re-exported rather than re-declared, so three
    workflows cannot disagree about what ``snoozed`` means or which metric ``frt`` names.
``rules``
    The pure rules: the lead-shorter validation, the watched target, the trigger instant,
    the three cancellations, the fire decision, the Data Connector execution result and
    the inbox ranking. Plus the error types this workflow raises.
``inferences``
    The judgement calls the specification left open, each recorded with the alternative
    rejected.
``engine``
    The reads and the writes: escalation workflows, the sweep that fires them, the runs,
    the alert executions and the escalated inbox.

Nothing here imports ``dsr.api``, and nothing here opens SQLite. Every read and write goes
through the :class:`~dsr.store.RecordStore` the HTTP layer hands in, so the audit row is
written in the same transaction as the change.

What this workflow does not claim
---------------------------------

**That a Slack message left this product.** The alert is recorded as attempted, with the
outcome this build derives from the action's configuration, and every execution carries
``sent_by_this_product: False``.

**A worker process.** "Intercom checks SLAs about once a minute." This product runs no
worker, so the sweep is an explicit route that a driver calls, and the clock is one a test
can freeze. The gap is reported by the sweep rather than hidden.
"""

from dsr.sla_escalation import (  # noqa: F401
    engine,
    inferences,
    rules,
    vocabulary,
)

__all__ = ["engine", "inferences", "rules", "vocabulary"]
