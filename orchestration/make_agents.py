"""Generate the live agent dashboard, and assert it against what it measured.

The requirement is that the agent dashboard stays current WHILE the agents work -
so
this has to be cheap enough to re-run continuously. That is the whole design
constraint, and it is why it is separate from the programme dashboard: that one
runs the test suite, which takes minutes, and this one takes seconds because it
reads only what is already on disk and already running.

  * who is running       - orca terminal list, and the worktree each is in
  * what each is doing   - the worktree's git state, which is the only honest
                           answer: an agent's screen can look busy while it has
                           written nothing
  * what is finished     - features actually on origin/main, not commits that
                           merely exist in a worktree
  * what is blocked      - a tab whose process is gone, or a worktree that has
                           stopped changing
  * the board            - from Orca, not from a local guess

The assertion at the end checks the rendered file against what was just measured,
because a dashboard nobody checks goes stale, and a dashboard that reports
success while the thing it watches has failed is worse than no dashboard at all -
which is the defect this project has now found in three separate tools.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

# Derived from this file, not named. This script used to write its dashboard
# into one machine's orca checkout, so running it from a worktree produced a
# correct file in the wrong repository and left this one stale - which is how it
# ended up quoting a commit from three merged PRs ago.
ROOT = Path(__file__).resolve().parent.parent
WS = Path(os.environ.get("DSR_WORKSPACES") or ROOT.parent)
REPO = "id:8964203a-831a-425f-8fd7-ebc3a0fc2e46"
OUT = ROOT / "orchestration" / "AGENTS.md"

#: The target is the researched corpus, not a round number. It used to be 100,
#: which meant 38 workflows were researched, judged and spec'd and then silently
#: excluded from "to go" - the queue could be worked to empty and the dashboard
#: would still read incomplete.
_CORPUS = ROOT / "docs" / "research" / "digital-sales-room-workflows" / "workflows.json"
TARGET = len(json.loads(_CORPUS.read_text(encoding="utf-8"))) if _CORPUS.exists() else 138


def run(args, cwd=ROOT, timeout=120):
    p = subprocess.run(args, cwd=cwd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout)
    return p.stdout if p.returncode == 0 else ""


def orca(args, timeout=180):
    p = subprocess.run(["orca", *args], cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout)
    try:
        return json.loads(p.stdout or "{}")
    except json.JSONDecodeError:
        return {}


def git(*args, cwd=ROOT, timeout=120):
    return run(["git", *args], cwd=cwd, timeout=timeout).strip()


def features_on_main() -> set[str]:
    out = git("ls-tree", "-r", "--name-only", "origin/main", "backend/dsr/features")
    found = set()
    for line in out.splitlines():
        m = re.search(r"wf[_-]?(\d{3})", line)
        if m:
            found.add(f"WF-{m.group(1)}")
    return found


def main():
    started = time.time()
    live = features_on_main()

    # The handle log is the record of every agent ever dispatched, so nothing
    # silently disappears from the dashboard just because its tab closed. It lives
    # under data/, which is gitignored, so a fresh clone legitimately has none.
    handles_path = ROOT / "data" / "dispatched_batch.json"
    known = {}
    handles_present = handles_path.exists()
    if handles_present:
        try:
            for rec in json.loads(handles_path.read_text(encoding="utf-8")):
                if isinstance(rec, dict) and rec.get("ticket"):
                    known[rec["ticket"]] = rec
        except (json.JSONDecodeError, OSError):
            handles_present = False

    terms = orca(["terminal", "list", "--json"]).get("result", {}).get("terminals", []) or []
    by_worktree = {}
    for t in terms:
        wp = t.get("worktreePath") or ""
        if wp:
            by_worktree[wp.replace("\\", "/").lower()] = t

    board = orca(["worktree", "list", "--repo", REPO, "--json"]) \
        .get("result", {}).get("worktrees", []) or []
    board_counts = {}
    for w in board:
        s = w.get("workspaceStatus") or "?"
        board_counts[s] = board_counts.get(s, 0) + 1

    rows = []
    for ticket, rec in sorted(known.items()):
        wt_name = rec.get("worktree", "")
        wt = WS / wt_name
        term = by_worktree.get(str(wt).replace("\\", "/").lower())

        ahead = uncommitted = 0
        exists = wt.exists()
        if exists:
            a = git("rev-list", "--count", "origin/main..HEAD", cwd=wt)
            ahead = int(a) if a.isdigit() else 0
            uncommitted = len([l for l in git("status", "--porcelain", cwd=wt).splitlines()
                               if l.strip()])

        if ticket in live:
            state = "MERGED"
        elif not exists:
            state = "WORKTREE GONE"
        elif ahead and not uncommitted:
            state = "READY TO MERGE"
        elif ahead:
            state = "committed, still editing"
        elif uncommitted:
            state = "WRITING"
        else:
            state = "no work yet"

        if term is None and ticket not in live and exists and not ahead and not uncommitted:
            state = "stopped, no output"

        rows.append({
            "ticket": ticket, "worktree": wt_name, "title": rec.get("title", ""),
            "handle": rec.get("handle", ""), "commits": ahead,
            "uncommitted": uncommitted, "state": state,
            "tab": "open" if term else "closed",
        })

    order = {"MERGED": 0, "READY TO MERGE": 1, "committed, still editing": 2,
             "WRITING": 3, "no work yet": 4, "stopped, no output": 5,
             "WORKTREE GONE": 6}
    rows.sort(key=lambda r: (order.get(r["state"], 9), r["ticket"]))

    def n(state):
        return sum(1 for r in rows if r["state"] == state)

    merged = n("MERGED")
    ready = n("READY TO MERGE")
    writing = n("WRITING") + n("committed, still editing")
    quiet = n("no work yet")
    stopped = n("stopped, no output") + n("WORKTREE GONE")
    tabs_open = sum(1 for r in rows if r["tab"] == "open")

    stamp = git("log", "-1", "--format=%cI", "origin/main")
    tip = git("log", "-1", "--format=%h %s", "origin/main")

    L = []
    A = L.append
    A("# Agents")
    A("")
    A("<!-- GENERATED by orchestration/make_agents.py. Do not edit by hand.")
    A("     Regenerate with .venv/Scripts/python orchestration/make_agents.py -->")
    A("")
    A("## Right now")
    A("")
    if not handles_present:
        # Printing a column of zeros here would be a claim about the world that
        # nobody re-checks: the previous version of this file read "agents
        # dispatched 31, ready to merge 24" for three weeks after every one of
        # those worktrees was gone. The absence of the log is stated instead of
        # being reported as a measurement.
        A("**No dispatch log in this clone.** `data/dispatched_batch.json` is")
        A("gitignored, and without it this generator cannot know which agents")
        A("were ever dispatched. It reports no counts rather than reporting zero:")
        A("zero is a measurement, and no measurement was taken. The programme")
        A("state it can measure is below.")
        A("")
    if handles_present:
        A(f"    agents dispatched   {len(rows)}")
        A(f"    agent tabs open     {tabs_open}")
        A(f"    merged into main    {merged}")
        A(f"    ready to merge      {ready}")
        A(f"    writing             {writing}")
        A(f"    no work yet         {quiet}")
        A(f"    stopped or gone     {stopped}")
        A("")
    A(f"`main` at `{tip}`")
    A(f"measured {stamp} in {time.time() - started:.0f}s")
    A("")
    A(f"**{len(live)}** features on `main` of {TARGET}. Board: " +
      ", ".join(f"{v} {k}" for k, v in sorted(board_counts.items())) +
      f" ({len(board)} cards).")
    A("")
    A("## Every agent dispatched")
    A("")
    if handles_present:
        A("| Workflow | What it is doing | State | Commits | Uncommitted | Tab |")
        A("|---|---|---|---|---|---|")
    else:
        A("Unknown. This generator cannot list agents it has no record of being")
        A("dispatched, and it will not reconstruct the list from worktree names.")
        A("")
    for r in rows:
        A(f"| {r['ticket']} | {r['title'][:44] or '-'} | **{r['state']}** | "
          f"{r['commits']} | {r['uncommitted']} | {r['tab']} |")
    A("")
    A("## What the states mean")
    A("")
    A("| State | What it actually says |")
    A("|---|---|")
    A("| MERGED | its feature module is on `origin/main`, read out of git |")
    A("| READY TO MERGE | committed, nothing uncommitted - waiting on the merge |")
    A("| committed, still editing | has a commit AND is still writing |")
    A("| WRITING | files changed, nothing committed yet |")
    A("| no work yet | worktree exists, nothing written |")
    A("| stopped, no output | tab closed with no files written and no commit |")
    A("")
    A("`WRITING` is measured from the worktree's git state, not from the agent's")
    A("screen. A screen can look busy while nothing has been written, which is")
    A("exactly the state that is hardest to tell from working - so the dashboard")
    A("reads what is on disk.")
    A("")
    A("A **tab closed with no output** is the one state worth acting on: that")
    A("agent's work does not exist anywhere, and re-dispatching its workflow is")
    A("cheaper than waiting.")
    A("")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(L), encoding="utf-8")

    back = OUT.read_text(encoding="utf-8")
    # Without a dispatch log the count checks are vacuous - asserting "0" is
    # present passes for a file that made no measurement. They are replaced by
    # the check that matters in that case: the absence is declared.
    count_checks = (
        {
            "the absence of a dispatch log is declared": "No dispatch log in this clone" in back,
            "no zero counts presented as measurements": "agents dispatched   0" not in back,
        }
        if not handles_present
        else {
            "the dispatched count": f"agents dispatched   {len(rows)}" in back,
        }
    )
    checks = {
        **count_checks,
        "main's commit": tip.split()[0] in back if tip else False,
        "the feature count": f"**{len(live)}** features on `main`" in back,
        "the target is the corpus": f"of {TARGET}" in back,
        "the board is reported": f"({len(board)} cards)" in back,
    }
    if handles_present:
        checks |= {
            "the merged count": f"merged into main    {merged}" in back,
            "the ready count": f"ready to merge      {ready}" in back,
            "the writing count": f"writing             {writing}" in back,
            "the stopped count": f"stopped or gone     {stopped}" in back,
            "the open tab count": f"agent tabs open     {tabs_open}" in back,
            "every agent is listed": all(r["ticket"] in back for r in rows),
        }
    bad = [k for k, v in checks.items() if not v]
    for k, v in checks.items():
        print(f"  {k:26} {'OK' if v else 'MISMATCH'}")
    print(f"  wrote orchestration/AGENTS.md ({len(back):,} chars) in {time.time() - started:.0f}s")
    if bad:
        print(f"  {len(bad)} MISMATCH")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
