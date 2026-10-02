"""Report the state of the set-2 port agents.

PowerShell kept getting interrupted mid-run, so this is Python, which has been
reliable for every other orchestration tool in this project.

It reports the same three independent signals as agent_status.py - screen, git,
pushed - and additionally flags the one defect specific to this dispatch: the
pointer prompt contained backticks around the brief path, and backticks written
into a cmd.exe pty are command substitution, so cmd tried to *execute* the first
word. WF-007's screen showed exactly that.
"""
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

ROOT = Path(__file__).resolve().parent.parent
WORKSPACES = Path(os.environ.get("DSR_WORKSPACES") or ROOT.parent)
REPO_ID = "id:8964203a-831a-425f-8fd7-ebc3a0fc2e46"

# One definition of the shared-file list, in tools/contract.py.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.contract import SHARED  # noqa: E402

NAMES = [
    "dsr-wf-007-content-library",
    "dsr-wf-009-publishing",
    "dsr-wf-010-library-search",
    "dsr-wf-011-room-handover",
]


def run(args, cwd=ROOT, timeout=90):
    p = subprocess.run(args, cwd=cwd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout)
    return (p.stdout + p.stderr).strip()


def jrun(args, timeout=90):
    out = run(args, timeout=timeout)
    if not out:
        return None
    try:
        d = json.loads(out)
    except json.JSONDecodeError:
        return None
    return d if d.get("ok") else None


def main():
    listing = jrun(["orca", "worktree", "list", "--repo", REPO_ID, "--json"])
    if not listing:
        print("could not list worktrees")
        return 1
    worktrees = {Path(w["path"]).name: w for w in listing["result"]["worktrees"]}

    for name in NAMES:
        print("=" * 74)
        wt = worktrees.get(name)
        if not wt:
            print(f"  {name}: NOT CREATED")
            continue

        terms = jrun(["orca", "terminal", "list", "--worktree", wt["id"], "--json"])
        agents = [t for t in (terms["result"]["terminals"] if terms else [])
                  if t.get("agentIdentity") == "opencode"]
        if not agents:
            print(f"  {name}: no opencode terminal")
            continue
        handle = agents[0]["handle"]

        rd = jrun(["orca", "terminal", "read", "--terminal", handle, "--json"])
        tail = (rd["result"]["terminal"].get("tail") or []) if rd else []
        text = "\n".join(tail)

        flags = []
        if "not recognized as an internal or external command" in text:
            flags.append("PROMPT-MANGLED (backticks -> cmd executed the first word)")
        if "Permission required" in text:
            flags.append("PERMISSION-BLOCKED")
        if "Ask anything" in text:
            flags.append("IDLE-NO-BRIEF")
        if re.search(r"·\s*interrupted\s*$", text, re.M):
            flags.append("STOPPED")
        working = bool(re.search(r"[⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏]|Thinking|Explored|Thought", text))
        flags.append("working" if working and not flags else ("idle" if not working else ""))

        path = Path(wt["path"])
        ahead = run(["git", "rev-list", "--count", "origin/main..HEAD"], path)
        dirty = run(["git", "status", "--porcelain"], path)
        dirty_n = len([l for l in dirty.splitlines() if l.strip()])
        files = [f for f in run(["git", "diff", "--name-only", "origin/main...HEAD"], path).splitlines() if f]
        offenders = sorted(set(files) & SHARED)

        print(f"  {name}")
        print(f"    handle  : {handle}")
        print(f"    branch  : {wt.get('branch','').replace('refs/heads/','')}")
        print(f"    state   : {', '.join(f for f in flags if f)}")
        print(f"    commits : {ahead or 0} ahead, {len(files)} file(s) changed, {dirty_n} uncommitted")
        if offenders:
            print(f"    !! SHARED FILES: {offenders}")
        else:
            print("    contract: no shared file touched")
        print("    --- last 6 lines ---")
        for line in tail[-6:]:
            print(f"    | {line}")
        print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
