"""Set every Orca board card from measured state.

Three versions of the landing test have been wrong, in both directions, and each
produced output that looked like a considered decision:

  1. "the workflow is on main AND this branch is ahead of zero" -> demoted nine
     shipped features to `todo`. A port merged through a merge branch keeps its
     own commits forever, so `ahead` is never 0.

  2. "is the workflow on main" alone -> promoted a DUPLICATE port to
     `completed`. Two worktrees can claim one ticket and only one can have
     shipped, so a question about the workflow is the wrong question for a card
     about a worktree.

  3. "does origin/main contain this branch's tip" -> demoted all eleven
     shipped features. PRs here are SQUASH-merged, so a port's own commit SHA is
     never an ancestor of main - only its squashed tree is. Verified: the WF-007
     tip 9b931c49 is contained in no remote branch at all.

So commit identity cannot answer this in either direction. The question that
does is a CONTENT one: are this worktree's own files present on main?

That is also the only test that separates two worktrees claiming one ticket.
They write the same paths, so path existence and commit reachability are
identical for them, and only their content differs. WF-008 has exactly this
shape, because two agents were dispatched for it and ported it independently:

    dsr-wf-008-external-sync     13 files,  0 absent from main  -> shipped
    dsr-wf-008-external-sync-2   14 files,  7 absent from main  -> duplicate
        (it wrote backend/dsr/library/ where main has external_library/)

The duplicate is not outstanding work and must not be merged on top of the one
that shipped, but `completed` would say its work shipped and `in-progress` would
say an agent is on it. `todo` is the only honest answer, with a comment saying
what a person has to decide.
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

# Not this feature's work. `orchestration/` is process, `docs/` is design notes;
# their absence from main says nothing about whether a port landed.
NOT_THE_FEATURES = ("orchestration/", "docs/", ".github/", "tools/")


def git(*args, cwd=ROOT):
    p = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=120)
    return p.stdout


def on_main(path):
    """Does this file path exist on origin/main?"""
    p = subprocess.run(["git", "cat-file", "-e", f"origin/main:{path}"], cwd=ROOT,
                       capture_output=True, timeout=60)
    return p.returncode == 0


def orca(args, timeout=120):
    p = subprocess.run(args, cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout)
    try:
        return json.loads(p.stdout)
    except json.JSONDecodeError:
        return {"ok": False}


def classify(wt, path, branch):
    """(status, note) for one worktree, from what is actually on disk."""
    ticket = f"WF-{TICKET_RE.search(path.name).group(1)}"
    own = [f.strip().replace("\\", "/")
           for f in git("diff", "--name-only", f"origin/main...HEAD", cwd=path).splitlines()
           if f.strip() and not f.strip().replace("\\", "/").startswith(NOT_THE_FEATURES)]
    absent = [f for f in own if not on_main(f)]

    dirty = len([l for l in git("status", "--porcelain", cwd=path).splitlines() if l.strip()])
    terms = orca(["orca", "terminal", "list", "--worktree", wt["id"], "--json"])
    live = [t for t in terms.get("result", {}).get("terminals", [])
            if t.get("agentIdentity") == "opencode"]

    if absent:
        # Some of this worktree's own files are not on main.
        if live:
            return "in-progress", (
                f"{ticket}: live OpenCode agent porting it. Brief "
                f"orchestration/ports/{ticket}.md. {len(own) - len(absent)}/{len(own)} of "
                f"its files already on main, {len(absent)} still to write, {dirty} "
                f"uncommitted. Commits locally and stops; a human reviews and merges.")
        return "todo", (
            f"{ticket}: not landed. {len(absent)} of its {len(own)} file(s) are absent from "
            f"main ({', '.join(Path(a).name for a in absent[:3])}"
            f"{', ...' if len(absent) > 3 else ''}), {dirty} uncommitted. No live agent. "
            f"Needs dispatch, or a decision if it duplicates work that already shipped.")

    if own:
        return "completed", (
            f"{ticket} is MERGED and live: all {len(own)} of its feature files are on main. "
            f"0 uncommitted. The branch itself is kept for history and is expected to read "
            f"as ahead of origin/main forever, because PRs here are squash-merged and a "
            f"squash does not carry the branch's commits into main. Not outstanding work.")

    if live:
        return "in-progress", (
            f"{ticket}: live OpenCode agent, nothing committed yet. Brief "
            f"orchestration/ports/{ticket}.md.")

    return "todo", f"{ticket}: no live agent and no work. Needs dispatch or a decision."


def main():
    listing = orca(["orca", "worktree", "list", "--repo", REPO_ID, "--json"])
    worktrees = listing.get("result", {}).get("worktrees", [])

    print("=" * 78)
    print("  board, set from what is on main")
    print("=" * 78)
    print("  (content test: a worktree's own files, not its commit graph - PRs here")
    print("   are squash-merged, so a port's commit is never an ancestor of main)")
    print()

    changed = 0
    for wt in worktrees:
        path = Path(wt["path"])
        branch = (wt.get("branch") or "").replace("refs/heads/", "")
        if not branch.startswith("n-dilipkumar/dsr-"):
            continue
        if not TICKET_RE.search(path.name) or not path.exists():
            continue

        status, note = classify(wt, path, branch)
        if wt.get("workspaceStatus") != status or wt.get("comment") != note:
            orca(["orca", "worktree", "set", "--worktree", wt["id"],
                  "--workspace-status", status, "--comment", note, "--json"])
            changed += 1
            arrow = f"{wt.get('workspaceStatus')} -> {status}"
        else:
            arrow = status
        print(f"  {TICKET_RE.search(path.name).group(1)}  {arrow:22} {path.name}")

        if status == "completed":
            terms = orca(["orca", "terminal", "list", "--worktree", wt["id"], "--json"])
            for t in terms.get("result", {}).get("terminals", []):
                if t.get("agentIdentity") == "opencode":
                    # A finished agent left running is a stale terminal holding memory.
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
    print(f"    {'total':<12} {len(cards)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
