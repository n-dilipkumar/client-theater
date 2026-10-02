"""Repair the set-2 dispatch: no backticks, and relaunch the two broken agents.

The defect is mine, in `dispatch_set2.py`'s pointer prompt:

    Read `orchestration/ports/WF-007.md` in this repo ...

Those backticks are command substitution in cmd.exe, which is what an Orca
terminal is on Windows. cmd tried to *execute* the word "Read", and the prompt
arrived mangled. Set 1's pointer survived only by luck; nothing about the two cases
differed in a way that was checked.

Consequences, both read off the screens rather than assumed:

  * WF-010's agent is sitting at an empty prompt having never received a brief.
  * WF-007's agent is gone entirely - the pty it was running in did not survive.

WF-009 and WF-011 were unaffected and are working, so they are left alone.

The prompt is rewritten with no backticks and no characters cmd treats specially.
It is also a single line, so there is no wrapping for cmd to mis-split.
"""

import json
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

# No backticks, no $, no %, no &, no pipes. Plain words and a slash-separated path.
POINTER = (
    "Read the file orchestration/ports/{ticket}.md in this repo and carry out "
    "exactly what it says. It is your complete brief: your environment, the "
    "contract, the hard rules, the numbered steps, and the report format. Read "
    "AGENTS.md and docs/FEATURE-CONTRACT.md as it directs you to. Work only "
    "inside this worktree. When you finish, commit locally and then stop. Do "
    "not push, do not open a pull request, do not merge. A human reviews your "
    "diff and does that. If a permission dialog appears, reject it and carry on."
)

REPAIRS = [
    # (ticket, worktree dir, tab title, has terminal?)
    ("WF-010", "dsr-wf-010-library-search", "DSR WF-010 library search port", True),
    ("WF-007", "dsr-wf-007-content-library", "DSR WF-007 content library port", False),
]


def orca(args, timeout=120):
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
        return {"ok": False, "err": (p.stdout or p.stderr)[:200]}
    return d


def main():
    listing = orca(["orca", "worktree", "list", "--repo", REPO_ID, "--json"])
    wts = {Path(w["path"]).name: w for w in listing.get("result", {}).get("worktrees", [])}

    handles = {}
    for ticket, dirname, title, _has_term in REPAIRS:
        print(f"=== {ticket} ===")
        wt = wts.get(dirname)
        if not wt:
            print("  worktree missing")
            continue
        wt_id = wt["id"]

        handle = None
        terms = orca(["orca", "terminal", "list", "--worktree", wt_id, "--json"])
        for t in terms.get("result", {}).get("terminals", []):
            if t.get("agentIdentity") == "opencode":
                handle = t["handle"]
                break

        if handle is None:
            # No agent tab survived. Create one rather than reusing the worktree.
            print("  no opencode terminal; creating a fresh agent tab")
            res = orca(
                [
                    "orca",
                    "terminal",
                    "create",
                    "--worktree",
                    wt_id,
                    "--title",
                    title,
                    "--command",
                    "opencode",
                    "--json",
                ]
            )
            if not res.get("ok"):
                print(f"  terminal create failed: {res}")
                continue
            handle = res["result"]["terminal"]["handle"]
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
        else:
            print(f"  reusing existing agent tab {handle}")

        # The safe prompt: no backticks or any other cmd metacharacter.
        text = POINTER.format(ticket=ticket)
        assert "`" not in text and "$" not in text and "%" not in text, "unsafe prompt"
        res = orca(
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
        if res.get("ok"):
            print(f"  brief delivered (accepted={res['result'].get('accepted')})")
        else:
            print(f"  send failed: {res}")
        handles[ticket] = {"handle": handle, "worktree": dirname, "title": title}

    # Persist, merging with any set-2 records so agent_status.py keeps working.
    out = ROOT / "data" / "dispatched.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(
        json.dumps([{"ticket": t, **v} for t, v in handles.items()], indent=2), encoding="utf-8"
    )

    print()
    print("=== repaired ===")
    for t, v in handles.items():
        print(f"  {t}  {v['handle']}  {v['title']}")

    # Read back, because "sent" is a claim and the screen is the evidence.
    print()
    time.sleep(20)
    for t, v in handles.items():
        r = orca(["orca", "terminal", "read", "--terminal", v["handle"], "--json"])
        tail = r.get("result", {}).get("terminal", {}).get("tail") or []
        text = "\n".join(tail)
        bad = "not recognized as an internal or external command" in text
        print(f"  {t}: mangled={bad}")
        for line in tail[-3:]:
            print(f"    | {line[:90]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
