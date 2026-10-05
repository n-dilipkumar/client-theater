"""WF-095: collect acceptance by e-signature, countersignature and identity verification.

Pricing, quoting and proposals. The research is
``docs/research/raw/quoting-proposals.md`` section 10, quoted in full in issue 166.

Four modules carry the workflow, and none of them knows about HTTP:

``vocabulary``
    Every researched term this workflow enforces against: the three acceptance methods, the
    two signer roles, the four signing statuses and the single transition each one allows,
    the four activity log entries, the one-hour verification window, the 40 MB PDF cap, the
    signature modes, and the quota rules with their evidence. Every constant carries the
    sentence it came from.
``rules``
    The pure rules: acceptance configuration validation, the *In signing* attachment
    constraint, the document size cap, the status machine, the buyer-first signing order,
    reassignment validation, the verification window and token check, and the quota counting.
    Plus the four error types this workflow raises.
``inferences``
    The six judgement calls the specification left open, each recorded with the alternative
    rejected, what the rejection would have cost, and the Jev audit that chose it.
``engine``
    The reads and the writes: the signing envelope, its signers, the signature events and the
    month's quota.

Nothing here imports the application module, and nothing here opens SQLite. Every read and write goes
through the :class:`~dsr.store.RecordStore` the HTTP layer hands in, so the audit row is
written in the same transaction as the change.

What this workflow reads and does not own
------------------------------------------

**The quote.** WF-086 provisions ``wf086_quote``. This workflow reads the quote as data and
never writes one through its own acceptance routes, because a signature must not be able to
rewrite the quote it is bound to.

**The proposal document.** WF-093 provisions ``wf093_document``, and the data flow says the
signature "is bound to the quote document". This workflow reads that document as data, never
writes one, and records the document's size against the researched 40 MB cap.

**Identity verification.** WF-078 provisions the out-of-band identity proof and stamps the
verification result onto the recipient session. This workflow models the envelope's own
verification step — the one-hour window the evidence fixes on the *Verify email* click — and
does not import WF-078's engine, because a feature must not import a feature.

**The shared link.** WF-094 provisions the hosted link the buyer opens. This workflow never
mints one.

What this workflow does not claim
---------------------------------

**The contract and the order.** The data flow's last clause creates a contract (and, in
Connected CPQ, an order) when *Automatically create contracts from accepted quotes* is on. The
issue names WF-099 as that consumer, so it is downstream of this ticket. This workflow writes
the accepted quote and the ``hs_payment_status`` that consumer reads.

**The quota ceiling.** The research says limits are "pooled per account by subscription and
seat count" and states no number, so this workflow counts usage and asserts no ceiling. See
:data:`~dsr.quote_acceptance.inferences.DERIVED_QUOTA_CEILING_IS_UNSPECIFIED`.
"""

from dsr.quote_acceptance import (  # noqa: F401
    engine,
    errors,
    inferences,
    rules,
    vocabulary,
)

__all__ = ["engine", "errors", "inferences", "rules", "vocabulary"]
