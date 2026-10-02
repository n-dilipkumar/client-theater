"""Ports awaiting merge, read from data rather than baked into the script.

This list has been hand-edited in the source at least three times and reverted
twice by shell restarts, once silently - which cost a merge cycle each time. The
list is *data about the current state of the programme*, not logic, so it belongs
in a file that can be regenerated and diffed rather than in a constant that gets
overwritten.

Regenerate with:  .venv/Scripts/python orchestration/pending_ports.py
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

# Derived from this file, not named: a hardcoded orca checkout made this write
# its output into a different clone than the one it was run from.
ROOT = Path(__file__).resolve().parent.parent
WORKSPACES = Path(os.environ.get("DSR_WORKSPACES") or ROOT.parent)
OUT = Path(os.environ.get("DSR_PENDING_PORTS") or ROOT / "data" / "pending_ports.json")

# One definition of the shared-file list, in tools/contract.py.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.contract import SHARED  # noqa: E402


def git(*args, cwd=ROOT):
    p = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=90,
    )
    return (p.stdout + p.stderr).strip()


def main():
    on_main = set()
    for f in git(
        "ls-tree", "-r", "--name-only", "origin/main", "backend/dsr/features"
    ).splitlines():
        m = re.search(r"wf[_-]?(\d{3})", f)
        if m:
            on_main.add(f"WF-{m.group(1)}")

    candidates = []
    if WORKSPACES.exists():
        for d in sorted(WORKSPACES.iterdir()):
            m = re.match(r"dsr-wf-(\d{3})", d.name)
            if not m or not d.is_dir():
                continue
            ticket = f"WF-{m.group(1)}"
            if ticket in on_main:
                continue
            branch = git("branch", "--show-current", cwd=d)
            ahead = git("rev-list", "--count", "origin/main..HEAD", cwd=d) or "0"
            dirty = [ln for ln in git("status", "--porcelain", cwd=d).splitlines() if ln.strip()]
            files = [
                f
                for f in git("diff", "--name-only", "origin/main...HEAD", cwd=d).splitlines()
                if f.strip()
            ]
            if not files and not dirty:
                continue  # nothing written yet
            offenders = sorted(set(files) & SHARED)
            candidates.append(
                {
                    "ticket": ticket,
                    "worktree": d.name,
                    "branch": branch,
                    "commits_ahead": int(ahead) if ahead.isdigit() else 0,
                    "uncommitted": len(dirty),
                    "changed_files": len(files),
                    "touches_shared": offenders,
                }
            )

    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(candidates, indent=2), encoding="utf-8")

    print(f"features on main : {len(on_main)}  {sorted(on_main)}")
    print(f"pending ports    : {len(candidates)}")
    for c in candidates:
        flag = f"  !! SHARED: {c['touches_shared']}" if c["touches_shared"] else ""
        print(
            f"  {c['ticket']}  {c['worktree']:<30} "
            f"{c['commits_ahead']} commit(s), {c['uncommitted']} uncommitted, "
            f"{c['changed_files']} file(s){flag}"
        )
    print(f"\nwritten to {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
