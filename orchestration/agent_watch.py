"""Is every dispatched agent actually working, or did some quietly stop?

Seven of the ten build agents came back `active=False` from the dispatch's own
check, and that number is not trustworthy either way:

  * the check looked for a fixed set of words - Thought, Explored, Read, pytest,
    git status - and an agent that has just started may have produced none of them
    yet. A false negative here is harmless.
  * it did NOT look at a spinner, deliberately, because a stopped OpenCode turn
    keeps animating its status bar and a dead agent reads as a busy one if
    activity is tested first. A false positive here is the expensive direction.

So this checks in the order that cannot mislead: the `interrupted` marker first,
then whether the worktree has moved, then the screen. And it reports the worktree
state for every agent, because a worktree that has gained files is the only thing
that actually proves work happened - screens lie, mangle, and time out.
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
WORKSPACES = Path(r"C:\Users\Dilip\orca\workspaces\client-theater")
PY = ROOT / ".venv" / "Scripts" / "python.exe"

# A spinner alone proves nothing, so it is reported separately and never used to
# conclude an agent is alive.
SPINNER = re.compile(r"[⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏]")
ACTIVITY = re.compile(r"Thought|Explored|Read |Edit |Write |Thinking|pytest|git status|bash")


def orca(args, timeout=120):
    p = subprocess.run(args, cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout)
    try:
        return json.loads(p.stdout)
    except json.JSONDecodeError:
        return {"ok": False}


def git(*args, cwd):
    p = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=60)
    return (p.stdout + p.stderr).strip()


def load_dispatches():
    out = []
    for name in ("dispatched_batch.json", "dispatched_set4.json"):
        p = ROOT / "data" / name
        if p.exists():
            out.extend(json.loads(p.read_text(encoding="utf-8")))
    return out


def main():
    agents = load_dispatches()
    print("=" * 96)
    print(f"  {len(agents)} dispatched agents")
    print("=" * 96)
    print(f"  {'ticket':8} {'ahead':>5} {'dirty':>5}  {'stopped':8} {'moved':6} {'dialog':7} {'verdict'}")
    print("  " + "-" * 92)

    rows = []
    for a in agents:
        wt = WORKSPACES / a["worktree"]
        if not wt.exists():
            rows.append((a["ticket"], "-", "-", "-", "NO-WORKTREE", "-", "worktree missing"))
            continue

        ahead = git("rev-list", "--count", "origin/main..HEAD", cwd=wt)
        dirty = len([l for l in git("status", "--porcelain", cwd=wt).splitlines() if l.strip()])
        ahead = int(ahead) if ahead.isdigit() else -1

        r = orca(["orca", "terminal", "read", "--terminal", a["handle"], "--json"])
        tail = (r.get("result", {}).get("terminal", {}).get("tail") or []) if r.get("ok") else []
        text = "\n".join(tail)

        stopped = re.search(r"·\s*interrupted", text) is not None
        dialog = "Permission required" in text
        moved = dirty > 0 or ahead > 0
        spinner = bool(SPINNER.search(text))
        activity = bool(ACTIVITY.search(text))

        if dialog:
            verdict = "BLOCKED on a permission dialog"
        elif stopped and not moved:
            verdict = "STOPPED with no work"
        elif stopped and moved:
            verdict = "stopped, has work - may be finishing up"
        elif moved and activity:
            verdict = "working"
        elif moved:
            verdict = "has work, screen quiet - check it"
        elif activity or spinner:
            verdict = "starting"
        else:
            verdict = "NO WORK YET"

        rows.append((a["ticket"], ahead, dirty, stopped, moved, dialog, verdict))
        print(f"  {a['ticket']:8} {ahead:>5} {dirty:>5}  {str(stopped):8} "
              f"{str(moved):6} {str(dialog):7} {verdict}")

    print()
    print("=" * 96)
    print("  summary")
    print("=" * 96)
    blocked = [r for r in rows if r[5]]
    stopped = [r for r in rows if r[3]]
    worked = [r for r in rows if r[4]]
    quiet = [r for r in rows if r[6].startswith(("NO WORK", "has work, screen quiet", "STOPPED"))]
    print(f"  total dispatched     : {len(rows)}")
    print(f"  have written files   : {len(worked)}")
    print(f"  blocked on a dialog  : {len(blocked)}  {[r[0] for r in blocked]}")
    print(f"  stopped              : {len(stopped)}  {[r[0] for r in stopped]}")
    print(f"  need a look          : {len(quiet)}  {[r[0] for r in quiet]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
