# The release-bar question set is pinned in code

`tools/jev.py` exposes `release_bar(ticket, evidence)`, which asks the same
questions for every landing: `contract_compliance`, `claims_backed_by_measurement`,
`coverage_of_the_change`, `audit_integrity`, `design_floor`, then a `verdict` of
`merge` / `fix` / `reject`. Threshold stays at 0.75.

Until this, each landing hand-assembled a freeform `decide` payload. The audit
log stored the answers but not the questions, so two gates could not be compared
even when they ran on the same ticket. That is a worse problem than the gates
failing: without comparable gates there is no way to tell whether a low pass
rate means strict judging or thin evidence.

**The evidence questions were re-specified after five landings.** The original
single `evidence_measured` question asked both whether claims were measured *and*
how much of the change was measured. It scored **0.11 to 0.23 on every feature
gated under it** — WF-033, WF-043, WF-058, WF-063, WF-065 — while
`contract_compliance`, `audit_integrity` and `design_floor` scored 0.84 to 0.95 on
the same runs. The cause was consistent: the evidence declared its own limits
(one browser engine, no screen-reader testing, synthetic clicks, code recovered
from a base ~50 commits behind `main`) and the question scored that disclosure as
missing evidence. Four features landed on recorded human overrides because of it,
and it would have blocked the remaining 53 identically.

A check that punishes disclosure trains people to conceal limits, which is worse
than having no check at all — the outcome the gate exists to prevent is the one it
was producing. So it is now two questions with distinct jobs.
`claims_backed_by_measurement` asks only whether each claim traces to output that
was actually produced, and says in its own instructions that declared limits are
not a failure of that question. `coverage_of_the_change` asks the breadth
question on its own terms, where a large unexamined surface still counts against
a change whether or not it was declared. The `verdict` question was reworded to
match: unverified items are context to weigh, not defects in themselves.

**Considered options.** Removing the evidence questions entirely and leaving the
judgement to `verdict`; and leaving the original question alone and accepting an
override per recovered port. Both were put to the model alongside the re-scope,
which chose the re-scope at 0.94 confidence, a 0.92 margin over the runner-up.

**Considered options for the threshold.** Adjusting it once the new slots had
data. Deferred: the threshold is the wrong instrument, because the question was
wrong, and moving it would have hidden that. Revisit after ten landings under the
re-specified set.

**Consequences.** Landings after this decision are comparable with each other
and not with the 42 that preceded the pinning, nor with the five gated under the
original evidence question. That discontinuity is intended: the earlier records
remain in `jev-audit.jsonl` unaltered, and aggregate verdict counts spanning
either boundary are not meaningful. The five overrides recorded against the old
question stand as a record of what it cost.
