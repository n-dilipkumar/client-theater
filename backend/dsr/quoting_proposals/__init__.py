"""WF-093: build a branded proposal from a template.

Pricing, quoting and proposals. The research is
``docs/research/raw/quoting-proposals.md`` section 8, quoted in full in issue 164.

Four modules carry the workflow, and none of them knows about HTTP:

``vocabulary``
    Every researched term this workflow enforces against: the eight module kinds the
    template editor configures, the six header fields, the three party roles, the four
    totals rows, the brand-kit tokens, the three logo sources and their precedence, the
    document lifecycle, the two researched caps, and the five inputs a generated summary
    may be derived from. Every constant carries the sentence it came from.
``rules``
    The pure rules: template validation, module order and hiding, binding validation and
    resolution, branding precedence, the line-item cap, the totals arithmetic, and the
    non-retroactive rule. Plus the three error types this workflow raises.
``inferences``
    The eight judgement calls the specification left open, each recorded with the
    alternative rejected, what the rejection would have cost, and the Jev audit that
    chose it.
``engine``
    The reads and the writes: templates, brand kits, the rendered documents, and the
    quote and line items this workflow reads as data.

Nothing here imports ``dsr.api``, and nothing here opens SQLite. Every read and write
goes through the :class:`~dsr.store.RecordStore` the HTTP layer hands in, so the audit
row is written in the same transaction as the change.

What this workflow reads and does not own
------------------------------------------

**The quote and its line items.** WF-086 provisions those rows. This workflow reads
``wf086_quote`` and ``wf086_line_item`` as data and never creates one through its own
routes, because the evidence says the quote-to-template association is "settable only at
quote creation" and that field must have one writer. The decision and what the alternative
would have cost are recorded as ``DERIVED_QUOTE_IS_READ_AS_DATA``, put to Jev as audit
``jev-20261004T212223-11296-43126``, which selected it at confidence 1.00.

**The priced catalogue.** WF-087 provisions products and price books, which the
line-items module resolves against. The issue claims that dependency.

What this workflow does not claim
---------------------------------

**The public URL.** The data flow sets the public URL from ``hs_domain`` and ``hs_slug``,
and the specification does not say which workflow provisions those two properties. The
issue declines to claim that dependency, so this workflow neither provisions nor reads
them.
"""

from dsr.quoting_proposals import (  # noqa: F401
    engine,
    inferences,
    rules,
    vocabulary,
)

__all__ = ["engine", "inferences", "rules", "vocabulary"]
