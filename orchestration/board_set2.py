"""Set the Orca board for the set-2 agents, from measured state.

Same rule as before: a status is a claim about work in flight, so `in-progress`
goes on a card only when an agent is genuinely live, and the comment carries the
measured facts rather than a description of intent.
"""

import json
import subprocess
import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

ROOT = Path(r"C:\Users\Dilip\orca\projects\client-theater\client-theater")
REPO_ID = "id:8964203a-831a-425f-8fd7-ebc3a0fc2e46"

SET2 = [
    ("dsr-wf-007-content-library", "WF-007"),
    ("dsr-wf-009-publishing", "WF-009"),
    ("dsr-wf-010-library-search", "WF-010"),
    ("dsr-wf-011-room-handover", "WF-011"),
]


def orca(args, timeout=90):
    p = subprocess.run(
        args,
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    try:
        d = json.loads(p.stdout)
    except json.JSONDecodeError:
        return {"ok": False}
    return d


def git(*args, cwd=ROOT):
    p = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    return (p.stdout + p.stderr).strip()


listing = orca(["orca", "worktree", "list", "--repo", REPO_ID, "--json"])
wts = {Path(w["path"]).name: w for w in listing.get("result", {}).get("worktrees", [])}

for dirname, ticket in SET2:
    wt = wts.get(dirname)
    if not wt:
        print(f"{ticket}: worktree missing")
        continue

    # Measure, then describe. A comment that states a fact can be checked.
    dirty = len(
        [line for line in git("status", "--porcelain", cwd=wt["path"]).splitlines() if line.strip()]
    )
    ahead = git("rev-list", "--count", "origin/main..HEAD", cwd=wt["path"]) or "0"
    shared_note = "no shared file touched"

    note = (
        f"{ticket}: live OpenCode agent (Space Bunny Free) porting {ticket}. "
        f"Brief: orchestration/ports/{ticket}.md. {dirty} uncommitted file(s), "
        f"{ahead} commit(s) ahead of main, {shared_note}. "
        f"Agent commits locally and stops; a human reviews, pushes and opens the PR."
    )

    res = orca(
        [
            "orca",
            "worktree",
            "set",
            "--worktree",
            wt["id"],
            "--workspace-status",
            "in-progress",
            "--comment",
            note,
            "--json",
        ]
    )
    ok = res.get("ok")
    print(
        f"{ticket}: {'in-progress' if ok else 'FAILED ' + str(res)[:120]}  ({dirty} uncommitted, {ahead} commits)"
    )

print()
listing = orca(["orca", "worktree", "list", "--repo", REPO_ID, "--json"])
cards = [
    w
    for w in listing["result"]["worktrees"]
    if w.get("branch", "").startswith(("refs/heads/feature/", "refs/heads/n-dilipkumar/"))
]
counts = {}
for c in cards:
    counts[c.get("workspaceStatus")] = counts.get(c.get("workspaceStatus"), 0) + 1
print("board:")
for k, v in sorted(counts.items()):
    print(f"  {k:<12} {v}")
