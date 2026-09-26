"""Report the state of every dispatched porting agent from evidence.

Screens are read on demand rather than polled; this exists so one command answers
"where is each agent" instead of three screen reads and a mental model. It reads
three independent signals per agent and is explicit when they disagree, because
the failure mode that matters is an agent that stopped without saying so:

  * the screen        - what the agent is doing right now
  * git              - whether it produced commits, and whether it touched a
                       shared file, which is the one thing that must never happen
  * the branch       - whether it pushed, which is where a port is supposed to end

An agent that is quiet, has no commits, and has not pushed is stalled. An agent
with a commit on a branch containing a shared file has failed the contract even
if it reports success, so that is called out separately and loudly.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

# The agent screens are full of box-drawing and braille characters. Windows Python
# defaults stdout to cp1252 and raises on them, so reconfigure both streams once
# here rather than wrapping every print.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

ROOT = Path(r"C:\Users\Dilip\orca\projects\client-theater\client-theater")
WORKSPACES = Path(r"C:\Users\Dilip\orca\workspaces\client-theater")
REPO_ID = "id:8964203a-831a-425f-8fd7-ebc3a0fc2e46"

SHARED = {
    "backend/dsr/api.py", "backend/dsr/deps.py", "backend/dsr/store.py",
    "backend/dsr/db/audited.py", "backend/seed.py", "frontend/src/App.jsx",
    "frontend/src/main.jsx", "frontend/src/lib/api.js",
    "frontend/src/lib/features.js", "frontend/src/components/ui.jsx",
    "frontend/vite.config.js",
}

MAP = ROOT / "data" / "dispatched.json"
if not MAP.exists():
    print("data/dispatched.json missing - no dispatched agents recorded")
    sys.exit(1)

# utf-8-sig, not utf-8: PowerShell's `Set-Content -Encoding utf8` writes a BOM on
# Windows, and a BOM is a JSON parse error rather than a hint.
agents = json.loads(MAP.read_text(encoding="utf-8-sig"))
if isinstance(agents, dict):
    agents = agents.get("agents", agents)


def run(args, cwd=ROOT, timeout=60):
    p = subprocess.run(args, cwd=cwd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout)
    return (p.stdout + p.stderr).strip()


def screen(handle):
    # utf-8 with replacement, not the platform default: the TUI paints box-drawing
    # and braille characters, and cp1252 raises UnicodeDecodeError on them, which
    # surfaces as a confusing NoneType further down rather than as an encoding bug.
    p = subprocess.run(["orca", "terminal", "read", "--terminal", handle, "--json"],
                       cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=60)
    if not p.stdout:
        return []
    try:
        data = json.loads(p.stdout)
    except json.JSONDecodeError:
        return []
    if not data.get("ok"):
        return []
    return data["result"]["terminal"].get("tail") or []


IDLE = re.compile(r"(Ask anything|waiting for your input)", re.I)
PERM = re.compile(r"Permission required", re.I)
WORKING = re.compile(r"(⠋|⠙|⠹|⠸|⠼|⠴|⠦|⠧|⠇|⠏|Thinking|Explored|Thought|execute|fetch)", re.I)

# The last thing an OpenCode TUI prints when a turn ends, whether it finished or
# was interrupted. This has to be checked BEFORE the spinner test: an interrupted
# turn leaves "Build - Space Bunny Free - 10m 57s - interrupted" in the scrollback
# while the status bar below it still shows the spinner strip, so a spinner-first
# test reports an agent that stopped dead as busy. That is the exact failure this
# tool exists to prevent, so it is checked first and reported as its own state.
STOPPED = re.compile(r"·\s*interrupted\s*$|·\s*(?:[\d.]+m?s)\s*·\s*[\d.]+\s*tok/s", re.M)

print("=" * 78)
print("  DISPATCHED PORT AGENTS")
print("=" * 78)

for a in agents:
    ticket = a["ticket"]
    folder = a.get("worktree") or a.get("worktree_id", "").split("::")[-1]
    path = WORKSPACES / Path(folder).name
    handle = a["handle"]

    print(f"\n  {ticket}   {a.get('title', '')}")
    print(f"    tab     : {handle}")

    # -- signal 1: the screen
    tail = screen(handle)
    text = "\n".join(tail)
    if not tail:
        state = "NO SCREEN OUTPUT"
    elif PERM.search(text):
        state = "BLOCKED ON PERMISSION PROMPT"
    elif STOPPED.search(text):
        state = "STOPPED - last turn ended, needs a follow-up"
    elif IDLE.search(text):
        state = "idle at prompt"
    elif WORKING.search(text):
        state = "working"
    else:
        state = "unknown"
    print(f"    screen  : {state}")

    # Last thing it said that is not spinner noise, so there is a real progress line.
    for line in reversed(tail):
        s = line.strip()
        if not s or s.startswith(("┃", "╹", "   ⬝", "Build ·")):
            continue
        if re.fullmatch(r"[⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏\s.▀▄█]*", s):
            continue
        print(f"    last    : {s[:100]}")
        break

    if not path.exists():
        print(f"    git     : worktree missing at {path}")
        continue

    # -- signal 2 and 3: git state
    branch = a.get("branch") or run(["git", "rev-parse", "--abbrev-ref", "HEAD"], path)
    ahead = run(["git", "rev-list", "--count", "origin/main..HEAD"], path)
    dirty = run(["git", "status", "--porcelain"], path)
    files = [f for f in run(["git", "diff", "--name-only", "origin/main...HEAD"], path).splitlines() if f]
    dirty_n = len([l for l in dirty.splitlines() if l.strip()])

    pushed = run(["git", "rev-parse", "--verify", "--quiet", f"origin/{branch}"], path)
    untracked = [l for l in dirty.splitlines() if l.startswith("??")]

    print(f"    branch  : {branch}")
    print(f"    commits : {ahead or '0'} ahead of origin/main, "
          f"{len(files)} file(s) changed, {dirty_n} uncommitted")
    if untracked:
        print(f"    new     : {', '.join(l[3:] for l in untracked[:4])}"
              + (f" (+{len(untracked) - 4} more)" if len(untracked) > 4 else ""))
    print(f"    pushed  : {'yes' if pushed else 'NOT YET'}")

    # -- the one thing that must never happen
    offenders = sorted(set(files) & SHARED)
    if offenders:
        print(f"    !! SHARED FILES TOUCHED: {offenders}")
    else:
        print("    contract: no shared file touched")

    if state in ("NO SCREEN OUTPUT", "BLOCKED ON PERMISSION PROMPT") or state.startswith("STOPPED"):
        print("    >>> NEEDS ATTENTION")

print()
print("=" * 78)
