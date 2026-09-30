# The release-bar question set is pinned in code

`tools/jev.py` exposes `release_bar(ticket, evidence)`, which asks the same five
questions for every landing: `contract_compliance`, `evidence_measured`,
`audit_integrity`, `design_floor`, then a `verdict` of `merge` / `fix` /
`reject`. Threshold stays at 0.75.

Until this, each landing hand-assembled a freeform `decide` payload. The audit
log stored the answers but not the questions, so two gates could not be compared
even when they ran on the same ticket. That is a worse problem than the gates
failing: without comparable gates there is no way to tell whether a low pass
rate means strict judging or thin evidence.

`evidence_measured` earns its place by splitting a question that used to be
bundled into one. The first recorded gate of this shape returned `fix` at 0.70
against `merge` at 0.23, and the honest reason was that the evidence declared
several things unchecked — no browser pass, touch targets only spot-checked.
That is a gap in the evidence, not a defect in the code, and the distinction is
what the four separate questions make visible.

**Considered options.** Adjusting the threshold once the new slots had data.
Rejected for now: without ten data points, a threshold change is a guess about
the gate rather than a measurement of it. Recorded as a deliberate deferral, to
be revisited after ten landings under the pinned set.

**Consequences.** Landings after this decision are comparable with each other
and not with the 42 that preceded it. That discontinuity is intended: the
earlier records remain in `jev-audit.jsonl` unaltered, and the aggregate verdict
counts spanning both are not meaningful.
