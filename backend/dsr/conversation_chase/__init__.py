"""WF-107: chase unresponsive buyers and reroute unattended conversations.

Automations and engagement. The research is
``docs/research/digital-sales-room-workflows/wf/WF-107.md``, quoted in full in issue
158.

Four modules carry the workflow, and none of them knows about HTTP:

``vocabulary``
    Every researched term this workflow enforces against: the two purely time-based
    triggers, the two anchors, the nine step blocks, the two interruption events, the
    conversation states, the origins, the run states, the tags, the office-hours
    vocabulary, and every refusal code. Every constant carries the sentence it came from.
``rules``
    The pure rules: the exclusive duration bounds, the anchor choice per trigger, the
    once-per-customer-message arm token, the API-created exemption, the Wait/Snooze
    precedence over the global auto-close setting, the interruption rules, and the
    office-hours walk. Plus the four error types this workflow raises.
``inferences``
    The eight judgement calls the specification left open, each recorded with the
    alternative rejected, what the rejection would have cost, and the Jev audit that
    chose it.
``engine``
    The reads and the writes: conversations, parts, triggers, runs, activity rows and
    the office-hours schedule.

Nothing here imports ``dsr.api``, and nothing here opens SQLite. Every read and write
goes through the :class:`~dsr.store.RecordStore` the HTTP layer hands in, so the audit
row is written in the same transaction as the change.

What this workflow does not claim
---------------------------------

**That a message left this product.** The research's extensibility names
``POST /messages`` replay, Data Connectors and ``X-Hub-Signature`` webhooks, all of which
need an Intercom credential this product does not hold. Every message is written as a
conversation part and every response carries ``sent_by_this_product: False``.

**Inbound webhook signature verification.** ``X-Hub-Signature`` belongs to WF-082, which
already owns that surface. This workflow names the webhooks it observes and does not
verify them a second time.

What the office-hours model is
------------------------------

Derived, not researched. The specification names "Office hours configuration" as a data
source and sources no model, so one is derived and the derivation is published in
``vocabulary`` and recorded as the ``office-hours-model`` entry in ``inferences``. The
derived default is Monday to Friday 09:00 to 18:00, taken from the research corpus's own
worked example rather than from a convention. A schedule is stored per room and is
editable, so a team whose hours differ replaces the default without a code change.
"""

from dsr.conversation_chase import (  # noqa: F401
    engine,
    inferences,
    rules,
    vocabulary,
)

__all__ = ["engine", "inferences", "rules", "vocabulary"]
