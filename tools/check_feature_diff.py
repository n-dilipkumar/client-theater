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

# Editing any of these from a feature branch means N branches will collide at
# the same line. The feature host already gives features a way to register
# without touching them.
SHARED = {
    "backend/dsr/api.py",
    "backend/dsr/deps.py",
    "backend/dsr/store.py",
    "backend/dsr/db/audited.py",
    "frontend/src/App.jsx",
    "frontend/src/main.jsx",
    "frontend/src/lib/api.js",
    "frontend/src/lib/features.js",
    "frontend/src/components/ui.jsx",
    "frontend/vite.config.js",
}

# Platform work is allowed to touch these; it just is not a feature branch.
PLATFORM_PREFIXES = (
    "orchestration/",
    "docs/",
    "tools/",
    "design-system/",
)


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
    parser.add_argument("--files", nargs="*", help="check an explicit file list instead of git diff")
    parser.add_argument(
        "--allow-shared",
        action="store_true",
        help="platform change: shared-file edits are intentional",
    )
    args = parser.parse_args(argv)

    files = args.files if args.files else changed_files(args.base)
    normalised = [f.replace("\\", "/") for f in files]
    offenders = sorted(set(normalised) & SHARED)

    if not offenders:
        print(f"OK: {len(normalised)} changed file(s), none shared")
        return 0

    if args.allow_shared:
        joined = "\n  - ".join(offenders)
        print(f"NOTICE: platform change (--allow-shared) edits shared file(s):\n  - {joined}")
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
