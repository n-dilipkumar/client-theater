"""WF-073: apply confidential view and block screenshot shortcuts.

Security, access and governance. The researched specification for this workflow is
``docs/research/digital-sales-room-workflows/wf/WF-073.md``, quoted in full in issue
176, and it says of itself: "Screenshot blocking is largely unenforceable from a
browser; treat as deterrence, and do not sell it as protection."

That sentence is the design constraint every module in this package obeys. The
controls here reduce what a buyer can capture in the ordinary case. They do not
stop a determined capture, and no vocabulary in this package, no API response and
no page copy claims otherwise.

Four modules carry the workflow, and none of them knows about HTTP:

``vocabulary``
    Every researched term, quoted from the specification, with the evidence it came
    from. The defaults, the two flag names, the rendering shapes and the shortlist
    of capture shortcuts the specification names.
``rules``
    The link's two booleans, how they are validated, what a focus band resolves to
    for a given viewport, and the errors this workflow raises.
``inferences``
    The judgement calls the specification left open, each recorded with the
    alternative rejected and why.
``engine``
    The writes: create a governed link, toggle a flag in place, evaluate a page
    against a viewport, and record a capture attempt the client reported.

Nothing here imports ``dsr.api``, and nothing here opens SQLite. Every read and
write goes through the :class:`~dsr.store.RecordStore` the HTTP layer hands in, so
the audit row is written in the same transaction as the change.
"""

from dsr.security_governance import engine, inferences, rules, vocabulary  # noqa: F401

__all__ = ["engine", "inferences", "rules", "vocabulary"]
