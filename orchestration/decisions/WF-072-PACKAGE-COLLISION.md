# WF-072 and WF-073 both claimed `security_governance`

Audit `jev-20261004T145853-27760-33511`, `rename_wf072_package`, confidence 0.98,
margin 0.99 over the runner-up. `fold_into_security_governance` scored 0.01 and
`let_wf073_win_and_drop_wf072_package` scored 0.00.

The same question asked twice in one decision came back **0.25 on `is_one_domain`**:
these are two domains, not one. And `is_confident` was **0.50**, so Jev picked the
reversible option and said it was not certain.

## What happened

Two workflows independently created `backend/dsr/security_governance`. WF-073
merged first, as PR 219, and is green on all ten checks. WF-072 was rebasing onto
`main` when the collision surfaced, and the two packages are not mergeable:
WF-072's feature module imports `ALL_COLLECTIONS` and other names that only its
own version of the package defines.

Forcing either side loses real work. Keeping WF-072's package overwrites a merged,
tested workflow. Keeping WF-073's leaves WF-072 unable to import, so its 1,892
lines of tests cannot run.

The decisive evidence was WF-073's own documentation, which states that gating a
link belongs to another workflow's domain and that this package *never refuses a
viewer*. That reads as a boundary the workflow drew on purpose rather than an
accident of naming, which is why folding the two together would have been a
category error against a rule the merged code states about itself.

## The ruling

WF-072's package is renamed to `dsr/view_only_access/`, which is what it actually
owns: view-only access and blocking bulk download. Its imports, its tests and its
feature module move with it.

WF-073 keeps `dsr/security_governance/` untouched. It is merged, green, and its
documentation already draws the line this decision confirms.

The frontend folders and the two feature modules already carry distinct names, so
nothing else moves. **No merged file is edited.**

## Why rename rather than fold

Folding would have edited four files belonging to a merged, tested, green workflow,
and would have forced one design answer on a question the two specifications leave
open. Jev's `is_one_domain` score of 0.25 says the answer is no.

Renaming costs discoverability: two packages cover adjacent subjects, and a reader
has to be told which is which. That cost is recoverable without touching anything
merged. A regression in WF-073 would not be.

## Note on how this was caught

Running the tests, not reading the diff. An earlier resolution using
`git checkout --theirs` during a rebase took the wrong side -- in a rebase
`--theirs` is *your* commit, not `main`'s -- and produced a truncated docstring. The
diff still looked like a merge. The `SyntaxError` on the first test run is what
exposed it.
