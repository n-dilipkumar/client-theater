#!/usr/bin/env python3
"""Fail if a feature branch edits a file every feature shares.

The feature host exists so that a hundred features written by a hundred
agents in a hundred worktrees can merge without conflict. That only holds if
features keep to adding files. This check turns the rule in
docs/FEATURE-CONTRACT.md into something a reviewer or CI can run instead of
remember.

    python tools/check_feature_diff.py                    # vs origin/main
    python tools/check_feature_diff.py --base main
    python tools/check_feature_diff.py --files a.py b.py  # explicit list
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

# The shared-file list lives in exactly one place. This module used to carry its
# own copy of the literal, which meant a shared file added to the guard but not
# to the merge scripts - or the reverse - failed silently, and the guard is the
# only thing standing between a hundred parallel feature branches and a merge
# collision. See tools/contract.py.
#
# The import is sys.path-relative rather than a bare `from contract import ...`
# because this file runs from the repo root (`python tools/check_feature_diff.py`)
# and the same pattern is used by the orchestration/ scripts, which run from the
# repo root and from backend/. Deriving the repo root from __file__ makes it
# independent of the caller's working directory.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.contract import PLATFORM_PREFIXES, SHARED  # noqa: E402


def changed_files(base: str) -> list[str]:
    result = subprocess.run(
        ["git", "diff", "--name-only", f"{base}...HEAD"],
        capture_output=True,
        text=True,
        check=True,
    )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", default="origin/main", help="ref to compare against")
    parser.add_argument(
        "--files",
        nargs="*",
        help="check an explicit file list instead of git diff (pass --files with no values to check nothing)",
    )
    parser.add_argument(
        "--allow-shared",
        action="store_true",
        help="platform change: shared-file edits are intentional",
    )
    parser.add_argument(
        "--platform-change",
        action="store_true",
        help=(
            "CI-only: the PR carries the 'platform-change' label, so shared-file "
            "edits are a recorded decision rather than a violation. Never pass this "
            "by hand - the label is the record, and it is what a reviewer sees."
        ),
    )
    args = parser.parse_args(argv)

    # `args.files` is [] both when --files was omitted and when it was passed
    # with no values, so ask the parser which happened. Guessing here is how
    # `--files` with no arguments silently checked the diff instead.
    files = args.files if args.files is not None else changed_files(args.base)
    normalised = [f.replace("\\", "/") for f in files]
    offenders = sorted(set(normalised) & SHARED)

    # A guard that passes on nothing measures nothing. On an uncommitted branch
    # `git diff base...HEAD` is empty, so this used to print "OK: 0 changed
    # file(s), none shared" -- and a release-bar record then quoted that string
    # as evidence of contract compliance. Zero changed files is a finding, not
    # a clean bill of health.
    if not normalised:
        scope = "the explicit --files list was empty" if args.files is not None else f"no changed files between {args.base} and HEAD"
        print(
            f"FAIL: {scope}.\n"
            "\n"
            "This guard can only report on a committed diff. Either the work is not\n"
            "committed yet, or the branch has nothing of its own.\n"
            "\n"
            "  commit it, then re-run:   git add -A && git commit && "
            f"python tools/check_feature_diff.py --base {args.base}\n"
            "\n"
            "If you are checking a file list explicitly, pass --files instead."
        )
        return 1

    if not offenders:
        print(f"OK: {len(normalised)} changed file(s), none shared")
        return 0

    if args.allow_shared or args.platform_change:
        reason = "--allow-shared" if args.allow_shared else "the platform-change label"
        joined = "\n  - ".join(offenders)
        print(f"NOTICE: platform change ({reason}) edits shared file(s):\n  - {joined}")
        print(
            "\nThis is allowed ONLY for deliberate platform work that adds an extension "
            "point. A feature that needs a shared-file change is a finding to report, "
            "not a change to make."
        )
        return 0

    print(
        f"FAIL: {len(offenders)} shared file(s) edited. Every feature branch that "
        "touches these conflicts with every other one.\n"
    )
    for offender in offenders:
        print(f"  - {offender}")
    print(
        "\nSee docs/FEATURE-CONTRACT.md. Features register by adding\n"
        "backend/dsr/features/<ticket>_<slug>.py and\n"
        "frontend/src/features/<id>/index.jsx -- never by editing the host.\n"
        "If this really is a platform change, re-run with --allow-shared."
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
