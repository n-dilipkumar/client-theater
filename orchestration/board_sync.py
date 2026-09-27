"""Set the Orca board from measured state, and close finished agent tabs.

The board's value is that it is true. Eleven cards read `completed` but only
nine features are on main, and two agents' worktrees are still marked
`in-progress` when their ports merged hours ago.

The mapping is derived, not asserted: a worktree is `completed` when its branch
has been merged into `origin/main`, `in-progress` when it has commits or
uncommitted work and its agent tab is live, and `todo` otherwise. That is
measurable, so it is measured.
"""
import json
import re
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

TICKET_RE = re.compile(r"wf-(\d{3})", re.I)


def orca(args, timeout=120):
    p = subprocess.run(args, cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout)
    try:
        return json.loads(p.stdout)
    except json.JSONDecodeError:
        return {"ok": False}


def git(*args, cwd=ROOT):
    p = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=60)
    return (p.stdout + p.stderr).strip()


listing = orca(["orca", "worktree", "list", "--repo", REPO_ID, "--json"])
worktrees = listing.get("result", {}).get("worktrees", [])

# Which features are actually on main? Ask the repo, not the board.
main_features = set()
for f in git("ls-tree", "-r", "--name-only", "origin/main", "backend/dsr/features").splitlines():
    m = re.search(r"wf(\d{3})", f)
    if m:
        main_features.add(f"WF-{m.group(1)}")

print("=" * 74)
print(f"  features on main: {len(main_features)}  {sorted(main_features)}")
print("=" * 74)

changed = 0
for wt in worktrees:
    path = Path(wt["path"])
    branch = (wt.get("branch") or "").replace("refs/heads/", "")
    if not branch.startswith("n-dilipkumar/dsr-"):
        continue
    name = path.name
    m = TICKET_RE.search(name)
    if not m:
        continue
    ticket = f"WF-{m.group(1)}"
    if not path.exists():
        continue

    # Merged? Two separate questions, and conflating them is what made the first
    # version of this demote nine shipped features to `todo`:
    #
    #   * is the workflow live on main?  -> the feature module exists there
    #   * is this branch still ahead?     -> usually YES, because a port branch is
    #     merged into a *merge* branch, not fast-forwarded into main, so its own
    #     commits stay reachable from the branch and `ahead` never reaches 0.
    #
    # The first is what the card should say. A workflow that shipped is completed
    # whether or not its authoring branch was deleted afterwards.
    shipped = ticket in main_features
    ahead = git("rev-list", "--count", f"origin/main..{branch}", cwd=path) or "0"
    dirty = len([l for l in git("status", "--porcelain", cwd=path).splitlines() if l.strip()])
    terms = orca(["orca", "terminal", "list", "--worktree", wt["id"], "--json"])
    live = [t for t in terms.get("result", {}).get("terminals", [])
            if t.get("agentIdentity") == "opencode"]

    if shipped:
        status = "completed"
        note = (f"{ticket} is MERGED and live on main - the feature module is there. "
                f"Authoring branch {branch} is kept for history: it still reads "
                f"{ahead} commit(s) ahead because a port is merged through a merge "
                f"branch rather than fast-forwarded, so `ahead` never reaches 0 here. "
                f"That is expected, not outstanding work.")
    elif live:
        status = "in-progress"
        note = (f"{ticket}: live OpenCode agent porting {ticket}. "
                f"Brief orchestration/ports/{ticket}.md. {ahead} commit(s) ahead, "
                f"{dirty} uncommitted. Commits locally and stops; a human reviews, "
                f"pushes and opens the PR.")
    else:
        status = "todo"
        note = (f"{ticket}: not on main, no live agent. {ahead} commit(s) ahead, "
                f"{dirty} uncommitted. Needs dispatch or a decision.")

    if wt.get("workspaceStatus") != status or wt.get("comment") != note:
        orca(["orca", "worktree", "set", "--worktree", wt["id"],
              "--workspace-status", status, "--comment", note, "--json"])
        changed += 1
        print(f"  {ticket}  {wt.get('workspaceStatus')} -> {status}   ({name})")
    else:
        print(f"  {ticket}  {status}  (unchanged)")

    # A finished agent left running is a stale terminal holding memory.
    if status == "completed":
        for t in live:
            orca(["orca", "terminal", "close", "--terminal", t["handle"], "--json"])
            print(f"       closed agent tab {t['handle']}")

print()
print(f"  cards updated: {changed}")

listing = orca(["orca", "worktree", "list", "--repo", REPO_ID, "--json"])
cards = [w for w in listing.get("result", {}).get("worktrees", [])
         if (w.get("branch") or "").startswith(("refs/heads/feature/", "refs/heads/n-dilipkumar/"))]
counts = {}
for c in cards:
    counts[c.get("workspaceStatus")] = counts.get(c.get("workspaceStatus"), 0) + 1
print()
print("  board:")
for k, v in sorted(counts.items()):
    print(f"    {k:<12} {v}")
