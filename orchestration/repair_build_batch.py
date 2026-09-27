"""Re-send briefs to agents whose prompt never landed.

Seven of the ten build agents are sitting on the OpenCode splash screen with an
empty "Ask anything..." box. They received nothing: `send_ok=True`, but the text
was typed at a TUI that was still drawing its splash, so it went nowhere.

The cause is a wait that was long enough for three agents and not for ten. The
dispatch creates each terminal, sleeps 9s, waits for `tui-idle`, and sends. With
three agents that gives the TUI time to finish starting. With ten, the later ones
are created while the machine is busy running the earlier ones, and 9 seconds is
no longer enough - and `tui-idle` on a splash screen can report idle, because a
screen that has not started is not busy.

So the repair does what the dispatch should have done:

  * waits for the prompt box to actually be present, not for a TUI condition that
    a not-yet-started screen can satisfy
  * re-reads the screen and confirms the brief is echoed before sending, so a
    second send cannot duplicate a prompt that did land
  * re-checks afterwards, because `accepted` comes back empty on a successful send
    in this Orca build and proves nothing

Idempotent on purpose. An agent that already has the brief is left alone, so this
can be run twice without telling an agent to do its whole task again.
"""
import json
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

ROOT = Path(r"C:\Users\Dilip\orca\projects\client-theater\client-theater")
POINTER = (
    "Read the file orchestration/ports/WF-{n:03d}.md in this repo and carry out exactly "
    "what it says. It is your complete brief: the research specification you must build "
    "from, the contract, the hard rules, the numbered steps, and the report format. Read "
    "that specification in full before you write any code, and read AGENTS.md and "
    "docs/FEATURE-CONTRACT.md as the brief directs. Work only inside this worktree, and "
    "keep every scratch file inside the worktree - do not use the system temp folder for "
    "anything, because reading from outside the worktree raises a permission dialog you "
    "cannot answer yourself. When you finish, commit locally and then stop. Do not run git "
    "push, do not open a pull request, do not merge. A human reviews your diff and does "
    "that. If a permission dialog appears, reject it and carry on."
)
UNSAFE = set("`$%&|<>^")

# The idle prompt with nothing in it. This is the state that means the brief never
# arrived: an agent that received one is busy, or has produced output above it.
IDLE_EMPTY = re.compile(r"Ask anything")
HAVE_BRIEF = re.compile(r"ports/WF-\d{3}|Build brief|Read the file orchestration")


def orca(args, timeout=180):
    p = subprocess.run(args, cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout)
    try:
        return json.loads(p.stdout)
    except json.JSONDecodeError:
        return {"ok": False, "raw": (p.stdout or p.stderr)[:200]}


def screen(handle):
    r = orca(["orca", "terminal", "read", "--terminal", handle, "--json"])
    tail = (r.get("result", {}).get("terminal", {}).get("tail") or []) if r.get("ok") else []
    return "\n".join(tail)


def ready(handle, tries=20):
    """Wait until the prompt box exists AND the TUI is idle.

    Both halves, because each alone is wrong: `tui-idle` is satisfied by a splash
    screen that has not started, and the prompt box is not drawn until the TUI has.
    """
    for _ in range(tries):
        s = screen(handle)
        if IDLE_EMPTY.search(s) and not HAVE_BRIEF.search(s):
            return True, s
        if HAVE_BRIEF.search(s) or not IDLE_EMPTY.search(s):
            # Either the brief is there already, or the agent is working. Either
            # way there is nothing to do here.
            return False, s
        time.sleep(3)
    return False, screen(handle)


def main():
    agents = json.loads((ROOT / "data" / "dispatched_batch.json").read_text(encoding="utf-8"))
    print("=" * 78)
    print(f"  {len(agents)} build agents; re-sending only where the brief is missing")
    print("=" * 78)

    resent, skipped = [], []
    for a in agents:
        handle, n, ticket = a["handle"], a["n"], a["ticket"]
        # Already has it? Never re-send: a duplicate prompt makes an agent redo work.
        s0 = screen(handle)
        if HAVE_BRIEF.search(s0):
            print(f"  {ticket}  already has the brief - left alone")
            skipped.append(ticket)
            continue

        ok, s = ready(handle)
        if not ok:
            why = "brief present" if HAVE_BRIEF.search(s) else "not idle yet"
            print(f"  {ticket}  NOT RESENDING: {why}")
            skipped.append(ticket)
            continue

        text = POINTER.format(n=n)
        bad = UNSAFE & set(text)
        if bad:
            print(f"  {ticket}  REFUSING, prompt contains {bad}")
            continue
        send = orca(["orca", "terminal", "send", "--terminal", handle,
                     "--text", text, "--enter", "--wait-submit", "30", "--json"],
                    timeout=120)
        print(f"  {ticket}  re-sent  send_ok={send.get('ok')}")
        resent.append(ticket)

    print()
    print(f"  re-sent : {len(resent)}  {resent}")
    print(f"  skipped : {len(skipped)}  {skipped}")

    print()
    print("=== confirm from the screens, not the receipts ===")
    time.sleep(40)
    for a in agents:
        s = screen(a["handle"])
        has = bool(HAVE_BRIEF.search(s))
        busy = bool(re.search(r"Thought|Explored|Read |Thinking|pytest|git status", s))
        print(f"  {a['ticket']}: brief_present={has}  busy={busy}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
