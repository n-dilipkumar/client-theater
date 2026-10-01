#!/usr/bin/env python3
"""The single source of truth for the feature contract's shared-file list.

SINGLE SOURCE OF TRUTH. Every consumer of the shared-file list imports
`SHARED` (and, where it needs them, `PLATFORM_PREFIXES`) from this module
rather than repeating the literal:

    tools/check_feature_diff.py       the CI guard
    orchestration/land_features.py    the merge pipeline
    orchestration/merge_ports.py      the port merge loop
    orchestration/verify_batches.py   the pre-merge review gate

Why a module rather than a shared constant that was already "shared"
---------------------------------------------------------------
The list was duplicated in those four files (and, at the time of writing, in
five more under `orchestration/`). Duplication is not a style preference here,
it is a correctness hazard: the CI guard is the only thing standing between a
hundred parallel feature branches and a merge collision, so the moment one
copy gains an eleventh shared file and the guard's copy does not, the guard
reports a clean bill of health on exactly the branch it exists to refuse.
Nothing fails, because the two lists are separate pieces of text.

One definition cannot drift from itself. `backend/tests/test_shared_contract.py`
asserts all four consumers resolve to the same set, so a consumer that goes
back to a local literal fails the suite rather than passing silently.

Adding a shared file
--------------------
Add it here, once, and note why it is shared. Every consumer picks it up on
the next run. Do not add it to a consumer.
"""
from __future__ import annotations

# Editing any of these from a feature branch means N branches will collide at
# the same line. The feature host already gives features a way to register
# without touching them.
SHARED: frozenset[str] = frozenset({
    "backend/dsr/api.py",
    "backend/dsr/deps.py",
    "backend/dsr/store.py",
    "backend/dsr/db/audited.py",
    # Rewritten by ten of the first twelve features, purely to add their own
    # demo rows. Features export seed(db, context) in their own module instead.
    "backend/seed.py",
    "frontend/src/App.jsx",
    "frontend/src/main.jsx",
    "frontend/src/lib/api.js",
    "frontend/src/lib/features.js",
    "frontend/src/components/ui.jsx",
    "frontend/vite.config.js",
})

# Platform work is allowed to touch these; it just is not a feature branch.
PLATFORM_PREFIXES: tuple[str, ...] = (
    "orchestration/",
    "docs/",
    "tools/",
    "design-system/",
)

#: The size of `SHARED`, asserted by the guard's own tests so that a deletion
#: cannot quietly reduce the set of protected files.
SHARED_COUNT = len(SHARED)


def is_shared(path: str) -> bool:
    """Is `path` one of the files a feature branch may not edit?

    Windows callers hand back backslash-separated paths from `git diff`, so the
    comparison normalises separators rather than requiring every caller to
    remember to.
    """
    return path.replace("\\", "/") in SHARED
