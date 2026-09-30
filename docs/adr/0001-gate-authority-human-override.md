# The release bar is mandatory, with a recorded human override

A change lands on `main` only when the release-bar judgment returns `pass`, or
when a named person records an override. The override is not a fallback for a
gate that is inconvenient; it is the mechanism the gate was designed around,
because `uncertain` is documented to mean "escalate to a person" and the person
is a human.

This was decided after measuring what had actually happened. Of the 42 features
landed between 2026-09-27 and 2026-09-29, 21 never had a gate record at all and
12 ended on a final `fail` or `uncertain`. Only 9 ended on `pass`. The rule was
being followed in roughly a fifth of landings — but the twelve non-pass features
did not merge themselves, so a human was already deciding. The gap was that the
decision left no record, which left `AGENTS.md` claiming a gate that the audit
log did not corroborate.

**Considered options.** Grandfathering the 33 existing landings, on the grounds
that re-judging shipped code produces evidence nobody can act on. Rejected: it
leaves the trail ambiguous, and it would have to be repeated for every batch.
Treating the gate as advisory was rejected because the typed judgment is one of
the three pillars the project claims, and demoting it quietly is a decision
nobody would make deliberately.

**Consequences.** Overrides live in their own append-only log
(`orchestration/decisions/human-overrides.jsonl`), never in
`jev-audit.jsonl`, which `tools/jev.py` owns. An override records who decided,
which criterion went unmet, why it was acceptable anyway, and what closes it.
See [0003](0003-the-release-bar-question-set-is-pinned.md) for what the
judgment asks.
