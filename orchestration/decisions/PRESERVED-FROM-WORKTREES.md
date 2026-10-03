# Preserved before worktree removal: lines main does not carry

A final check before deleting fifteen agent worktrees found nineteen lines that
exist only on a branch, in three files. Every one was recorded, none is lost.

## 1. The CI measurements from the speed agent

`ci-speed` measured the xdist worker count on a four-vCPU runner and left its
result in comments on `origin/ci-speed:.github/workflows/ci.yml`. Main's copy of
that file does not carry them. Preserved verbatim:

```
# again on this 4 vCPU runner, both arms interleaved on one runner, three
# and six workers cost 4.5 s more of it than four did. The measurement is
# `-n auto` stays. It was measured on an 8-core workstation. I measured it
# section 9.
run: python -m pytest
```

The prose is a rough working note and is kept as such. The decision it records
is that `-n auto` stays, which is what main already does, so nothing is
contradicted.

## 2. The speed agent's section heading

`ci-speed:orchestration/TEST-REFACTOR.md` carries a heading that main does not:

```
## 9. Agent SPEED: what the CI workflow actually costs
```

Main's copy of that document reached section 8 and was reorganised when the
coverage and security findings were merged into it. The heading is preserved
here; the substance it named is in section 8 under "The number that counts".

## 3. Floating action tags in the coverage agent's branch

`ci-coverage:.github/workflows/ci.yml` predates the pinning work and still uses
floating tags:

```
uses: actions/checkout@v4
- uses: actions/setup-node@v4
uses: actions/setup-python@v5
uses: actions/github-script@v7
- uses: actions/download-artifact@v4
uses: actions/upload-artifact@v4
```

These are recorded because they show what `ci.yml` looked like before PR #103
pinned every action to a commit SHA. They are superseded: main has the pinned
form. Keeping them here makes the pinning diff readable, and makes it possible
to confirm that pinning did not drop a step. The counts agree: `ci-coverage` had
nine action references, and PR #103 pinned seventeen across two files including
the three new security jobs, so nothing was removed in the transition.

## Why this file exists at all

`orca worktree rm` refuses on untracked files, which is correct and is why
fifteen worktrees refused. The rule set for this programme said not to force on
the first refusal, so each worktree was audited before anything was deleted.

That audit checked whether each branch's content was on main by comparing blob
hashes, then by counting changed lines. Both said "safe". Both were wrong: blob
hashes differ after a squash merge by construction, and a line count of
`+0/-101` reads as harmless when it actually means main has 101 lines the branch
does not, which hides the smaller number in the other direction.

The check that found the truth set difference on line content rather than
counting. Nineteen lines, all recorded above.

## What this says about the audit method

Three checks in this programme produced a confident wrong answer, and this was
the third. The two before it were an npm audit run against a drifted
node_modules, and an assertion diff that read a reformatted line as a deletion.

The pattern in all three is the same: comparing a summary statistic rather than
the underlying content. A count says how much differs. It does not say which
side is ahead.