"""Relaunch the set-3 agents whose tabs were closed by mistake.

The previous script closed the handles in `data/dispatched_set3.json` believing
they were the finished set-2 agents. They were not - those handles belong to
WF-008, WF-013 and WF-016, which are still working. Their worktrees are intact
(WF-016 still has its 10 uncommitted files), but each agent's terminal is gone,
so each needs a fresh tab and its brief re-sent.

Same discipline as `repair_set2.py`: no character cmd.exe treats specially in the
prompt, assert that before sending, and read the screen back afterwards rather
than trusting the send receipt.
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

# Plain words only. No backticks, no $ % & | < > ^ - all of which a Windows
# terminal treats as syntax rather than text.
POINTER = (
    "You are resuming. Your terminal was closed by mistake, not because your work "
    "was rejected. Everything you had written is still in this worktree - check "
    "git status and continue from there.\n"
    "\n"
    "Read the file orchestration/ports/{ticket}.md in this repo and carry out "
    "exactly what it says. It is your complete brief. Read AGENTS.md and "
    "docs/FEATURE-CONTRACT.md as it directs you to. Work only inside this "
    "worktree. When you finish, commit locally and then stop. Do not run git "
    "push, do not open a pull request, do not merge. A human reviews your diff "
    "and does that."
)

UNSAFE = set("`$%&|<>^")

TARGETS = [
    ("WF-008", "dsr-wf-008-external-sync", "DSR WF-008 external sync port"),
    ("WF-013", "dsr-wf-013-conditional-rules", "DSR WF-013 conditional rules port"),
    ("WF-016", "dsr-wf-016-crm-sync", "DSR WF-016 CRM sync port"),
]


def orca(args, timeout=150):
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
        return json.loads(p.stdout)
    except json.JSONDecodeError:
        return {"ok": False, "raw": (p.stdout or p.stderr)[:200]}


def main():
    listing = orca(["orca", "worktree", "list", "--repo", REPO_ID, "--json"])
    wts = {Path(w["path"]).name: w for w in listing.get("result", {}).get("worktrees", [])}

    relaunched = []
    for ticket, dirname, title in TARGETS:
        print(f"=== {ticket} ===")
        wt = wts.get(dirname)
        if not wt:
            print("  worktree missing")
            continue

        # If a live agent tab somehow survived, leave it alone.
        terms = orca(["orca", "terminal", "list", "--worktree", wt["id"], "--json"])
        existing = [
            t
            for t in terms.get("result", {}).get("terminals", [])
            if t.get("agentIdentity") == "opencode"
        ]
        if existing:
            print(f"  agent tab already present: {existing[0]['handle']}")
            relaunched.append(
                {
                    "ticket": ticket,
                    "handle": existing[0]["handle"],
                    "worktree": dirname,
                    "title": title,
                }
            )
            continue

        text = POINTER.format(ticket=ticket)
        bad = UNSAFE & set(text)
        if bad:
            print(f"  REFUSING TO SEND, prompt contains {bad}")
            continue

        res = orca(
            [
                "orca",
                "terminal",
                "create",
                "--worktree",
                wt["id"],
                "--title",
                title,
                "--command",
                "opencode",
                "--json",
            ]
        )
        if not res.get("ok"):
            print(f"  terminal create failed: {str(res)[:150]}")
            continue
        handle = res["result"]["terminal"]["handle"]

        # A fresh TUI drops an early prompt.
        time.sleep(10)
        orca(
            [
                "orca",
                "terminal",
                "wait",
                "--terminal",
                handle,
                "--for",
                "tui-idle",
                "--timeout-ms",
                "45000",
                "--json",
            ],
            timeout=70,
        )

        send = orca(
            [
                "orca",
                "terminal",
                "send",
                "--terminal",
                handle,
                "--text",
                text,
                "--enter",
                "--wait-submit",
                "25",
                "--json",
            ],
            timeout=100,
        )
        print(f"  tab {handle}  sent={send.get('ok')}")
        relaunched.append({"ticket": ticket, "handle": handle, "worktree": dirname, "title": title})

    (ROOT / "data" / "dispatched_set3.json").write_text(
        json.dumps(relaunched, indent=2), encoding="utf-8"
    )

    print()
    print("=== read back: is each agent actually running? ===")
    time.sleep(30)
    for a in relaunched:
        r = orca(["orca", "terminal", "read", "--terminal", a["handle"], "--json"])
        tail = (r.get("result", {}).get("terminal", {}).get("tail") or []) if r.get("ok") else []
        text = "\n".join(tail)
        mangled = re.search(r"'Read' is not recognized", text) is not None
        got_brief = "ports/" in text
        active = re.search(r"[⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏]|Thought|Explored|git status", text) is not None
        print(f"  {a['ticket']}: mangled={mangled} brief_echoed={got_brief} active={active}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
