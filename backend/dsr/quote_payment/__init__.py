"""WF-096: accept a quote without a signature and take payment in the quote.

The research is ``docs/research/raw/quoting-proposals.md`` section 11, quoted in full in issue
178. This package holds the rules for the buyer's side of a quote: publishing the payment
configuration, accepting by clickwrap (no signature), creating the invoices acceptance
triggers, and recording the charge that pays them.

Five modules carry the workflow, and none of them knows about HTTP:

``vocabulary``
    Every researched term this workflow enforces against: the three acceptance methods, the
    five payment methods, the two payment types, the payment status written on publish, the
    five billing frequencies with their month counts, the four effective-date modes, the net
    payment terms calendar, the three-tax-ID cap, the strict minimum charge, the ten-day
    invoice lead, every refusal reason code, and the sentences each constant came from.
``rules``
    The pure rules: the amount due with its minimum-charge constraint, the whole-number
    quantity rule, the *Print and sign* refusal, the publish validation that derives
    ``hs_payment_type``, the effective-date resolution, the first invoice and the recurring
    schedule, the tax-id cap, and the state guards. Every function raises nothing but this
    workflow's own errors, and most raise nothing at all.
``inferences``
    The judgement calls the specification left open, each with the alternative rejected and the
    Jev audit that chose it.
``errors``
    The four error types, declared once so the HTTP layer can map them.
``engine``
    The reads and the writes, over the audited store the HTTP layer hands in.

Nothing here imports the application module, and nothing here opens SQLite. Every read and
write goes through the :class:`~dsr.store.RecordStore` the HTTP layer hands in, so the audit
row is written in the same transaction as the change.

The two acts, and why they are two
----------------------------------

The buyer **accepts** and then, separately, **pays**. Where the e-signature workflow this
package sits beside makes the acceptance itself the signature, this one makes the acceptance a
click and the payment a second step the buyer may take now or later. See
:data:`~dsr.quote_payment.inferences.DERIVED_PAYMENT_IS_A_SEPARATE_BUYER_STEP`. A declined
charge is therefore a recorded outcome on the charge row and never undoes the acceptance.

What this workflow reads and does not own
-----------------------------------------

**The quote and its line items.** WF-086 provisions ``wf086_quote`` and ``wf086_line_item``.
This workflow reads both and patches exactly the properties the researched data flow names onto
the quote — ``hs_status`` and ``hs_clickwrap_accepted_by`` on acceptance, and the payment
properties on publish. It never writes a line item outside its own demo route and never writes
an amount onto the quote.

**The acceptance the e-signature path takes.** WF-095 owns ``esignature`` acceptance and its
``quote_acceptance`` package. This workflow refuses an *E-signature* acceptance with
:data:`~dsr.quote_payment.vocabulary.REASON_E_SIGNATURE_BELONGS_TO_WF095` rather than
reimplementing it, because a clickwrap flow that also ran a signing envelope would be two
owners for one agreement. ``print_and_sign`` is modelled as custom language and refused while
online payments are on, exactly as the research says.

**The contract and the order.** The data flow's final clause creates a contract (and, in
Connected CPQ, an order) when *Automatically create contracts from accepted quotes* is on. The
issue names that path out of scope, so this workflow writes the accepted quote and the
payment properties its consumer reads, and stops there. See
:data:`~dsr.quote_payment.inferences.DERIVED_CONNECTED_CPQ_IS_OUT_OF_SCOPE`.

What this workflow does not claim
---------------------------------

It records a charge's outcome; it moves no money. ``hs_payment_status`` therefore stays at the
``PENDING`` the publish step writes, and the outcome lives on this workflow's own charge row.
See :data:`~dsr.quote_payment.inferences.DERIVED_PAYMENT_STATUS_STAYS_PENDING`.
"""

from dsr.quote_payment import (  # noqa: F401
    engine,
    errors,
    inferences,
    rules,
    vocabulary,
)

__all__ = ["engine", "errors", "inferences", "rules", "vocabulary"]
