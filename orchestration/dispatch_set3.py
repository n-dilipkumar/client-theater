"""Dispatch set 3: WF-008, WF-013, WF-016 - one agent each, one tab each.

Set 1's prompt said to push, which the agents cannot do, so they deadlocked on a
permission dialog. Set 2's prompt wrapped the brief path in backticks, and a
Windows terminal is cmd.exe, where backticks are command substitution - so cmd
tried to *execute* the word "Read" and two agents never received a brief at all.

Both defects are designed out here rather than fixed after the fact:

  * no backticks, and no character cmd treats specially. Asserted before sending.
  * commit locally and stop. Pushing is the reviewer's job.

The prompt is a single line of plain words. There is nothing in it for a shell to
misinterpret.
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
REPO_ID = "id:8964203a-831a-425f-8fd7-ebc3a0fc2e46"

SET3 = [
    ("WF-008", "dsr-wf-008-external-sync", "DSR WF-008 external sync port", "WF-008"),
    ("WF-013", "dsr-wf-013-conditional-rules", "DSR WF-013 conditional rules port", "WF-013"),
    ("WF-016", "dsr-wf-016-crm-sync", "DSR WF-016 CRM sync port", "WF-016"),
]

# Plain words, one line, no metacharacters. assert() below proves it before sending.
POINTER = (
    "Read the file orchestration/ports/{ticket}.md in this repo and carry out exactly "
    "what it says. It is your complete brief: your environment and the absolute python "
    "path, the contract, the hard rules, the numbered steps, and the report format. "
    "Read AGENTS.md and docs/FEATURE-CONTRACT.md as it directs you to. Work only "
    "inside this worktree. When you finish, commit locally and then stop. Do not "
    "run git push, do not open a pull request, do not merge. A human reviews your "
    "diff and does that. If a permission dialog appears, reject it and carry on."
)

# Anything cmd.exe would act on rather than pass through.
UNSAFE = set("`$%&|<>^")


def orca(args, timeout=150):
    p = subprocess.run(args, cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout)
    try:
        d = json.loads(p.stdout)
    except json.JSONDecodeError:
        return {"ok": False, "raw": (p.stdout or p.stderr)[:200]}
    return d


def main():
    launched = []
    for ticket, dirname, title, brief in SET3:
        print(f"=== {ticket} ===")
        text = POINTER.format(ticket=brief)
        bad = UNSAFE & set(text)
        if bad:
            print(f"  REFUSING TO SEND: prompt contains {bad}")
            continue

        wt = orca(["orca", "worktree", "create", "--name", dirname,
                   "--setup", "skip", "--no-parent", "--json"])
        if not wt.get("ok"):
            print(f"  worktree failed: {str(wt)[:160]}")
            continue
        wt_id = wt["result"]["worktree"]["id"]

        term = orca(["orca", "terminal", "create", "--worktree", wt_id,
                     "--title", title, "--command", "opencode", "--json"])
        if not term.get("ok"):
            print(f"  terminal failed: {str(term)[:160]}")
            continue
        handle = term["result"]["terminal"]["handle"]

        # A fresh TUI drops an early prompt. Wait for idle before sending.
        time.sleep(10)
        orca(["orca", "terminal", "wait", "--terminal", handle,
              "--for", "tui-idle", "--timeout-ms", "45000", "--json"], timeout=70)

        send = orca(["orca", "terminal", "send", "--terminal", handle,
                     "--text", text, "--enter", "--wait-submit", "25", "--json"], timeout=100)
        if not send.get("ok"):
            print(f"  send failed: {str(send)[:160]}")
            continue
        print(f"  tab {handle}  brief sent")

        launched.append({"ticket": ticket, "handle": handle, "worktree": dirname,
                         "worktree_id": wt_id, "title": title,
                         "brief": f"orchestration/ports/{brief}.md"})

    (ROOT / "data" / "dispatched_set3.json").write_text(
        json.dumps(launched, indent=2), encoding="utf-8")

    print()
    print(f"=== {len(launched)} agent tab(s) launched ===")
    for a in launched:
        print(f"  {a['ticket']}  {a['handle']}  {a['title']}")

    # Read back, because "sent" is a claim and the screen is the evidence.
    #
    # The mangled-prompt detector looks for cmd reporting that the FIRST WORD of my
    # prompt was not a command. A broad "not recognized as an internal or external
    # command" fires on any typo the agent itself makes - WF-016 tried `| tail -n 20`,
    # a Unix idiom, and the agent recovered on its own. Detecting that as a mangled
    # brief is a false positive that trains you to ignore the check, so the test is
    # narrowed to the word that actually starts the prompt.
    print()
    time.sleep(25)
    for a in launched:
        r = orca(["orca", "terminal", "read", "--terminal", a["handle"], "--json"])
        tail = (r.get("result", {}).get("terminal", {}).get("tail") or []) if r.get("ok") else []
        text = "\n".join(tail)
        mangled = re.search(r"'Read' is not recognized", text) is not None
        got_brief = "ports/" in text
        working = re.search(r"[⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏]|Thought|Explored|pytest", text) is not None
        print(f"  {a['ticket']}: prompt_mangled={mangled}  "
              f"brief_echoed={got_brief}  active={working}")

    return 0 if len(launched) == len(SET3) else 1


if __name__ == "__main__":
    sys.exit(main())
