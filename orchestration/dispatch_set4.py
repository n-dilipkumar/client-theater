"""Dispatch set 4: WF-004, WF-015, WF-017 - one agent, one tab, one worktree.

Same discipline as the three batches before it, and every item in it is there
because something went wrong the time before:

  * No character cmd.exe treats specially in the prompt, asserted before sending.
    A Windows terminal reads a backtick as command substitution and executes the
    first word, which ate two agent briefs in set 2. The prompt is a single line
    of plain words, so there is nothing in it for a shell to misread.

  * The prompt says commit locally and stop, and do not push. Set 1's prompt said
    to push, which the agents cannot do, so they deadlocked on a permission dialog
    they have no way to answer.

  * The screen is read back afterwards. A send receipt is a claim, not evidence,
    and the `accepted` field comes back empty on a successful send anyway.

  * The mangled-prompt detector matches only the word that starts the prompt. A
    broad "not recognized as an internal or external command" also fires when an
    agent tries a Unix idiom in cmd - `| tail -n 20` - which WF-016 did and
    recovered from by itself. Reporting that as a mangled brief is a false
    positive, and a check that cries wolf gets ignored.

WF-004 and WF-015 are dispatched together on purpose. They were the collision -
both wanted backend/dsr/access.py - and it is already settled: WF-004 renames to
roles.py, WF-015 keeps access.py. Running them at once is the test that the
decision holds, and if it does not, the conflict shows up in two worktrees rather
than in a merge.
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

SET4 = [
    ("WF-004", "dsr-wf-004-invite-buyer", "DSR WF-004 invite buyer with a role port"),
    ("WF-015", "dsr-wf-015-identity-gate", "DSR WF-015 identity gate port"),
    ("WF-017", "dsr-wf-017-white-label", "DSR WF-017 white-label custom domain port"),
]

# Plain words, one line, no metacharacters. Asserted below before sending.
POINTER = (
    "Read the file orchestration/ports/{ticket}.md in this repo and carry out "
    "exactly what it says. It is your complete brief: your environment and the "
    "absolute python path, the contract, the hard rules, the numbered steps, the "
    "exact list of files to carry across, and the report format. Read AGENTS.md "
    "and docs/FEATURE-CONTRACT.md as it directs you to. Work only inside this "
    "worktree. When you finish, commit locally and then stop. Do not run git "
    "push, do not open a pull request, do not merge. A human reviews your diff "
    "and does that. If a permission dialog appears, reject it and carry on."
)

# Anything cmd.exe would act on rather than pass through.
UNSAFE = set("`$%&|<>^")


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
    launched = []
    for ticket, dirname, title in SET4:
        print(f"=== {ticket} ===")
        text = POINTER.format(ticket=ticket)
        bad = UNSAFE & set(text)
        if bad:
            print(f"  REFUSING TO SEND: prompt contains {bad}")
            continue

        wt = orca(
            [
                "orca",
                "worktree",
                "create",
                "--name",
                dirname,
                "--setup",
                "skip",
                "--no-parent",
                "--json",
            ]
        )
        if not wt.get("ok"):
            print(f"  worktree failed: {str(wt)[:200]}")
            continue
        wt_id = wt["result"]["worktree"]["id"]

        term = orca(
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
        if not term.get("ok"):
            print(f"  terminal failed: {str(term)[:200]}")
            continue
        handle = term["result"]["terminal"]["handle"]

        # A fresh TUI drops an early prompt. Wait for idle before sending.
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
        print(f"  tab {handle}  send_ok={send.get('ok')}")

        launched.append(
            {
                "ticket": ticket,
                "handle": handle,
                "worktree": dirname,
                "worktree_id": wt_id,
                "title": title,
                "brief": f"orchestration/ports/{ticket}.md",
            }
        )

    (ROOT / "data" / "dispatched_set4.json").write_text(
        json.dumps(launched, indent=2), encoding="utf-8"
    )

    print()
    print(f"=== {len(launched)} agent tab(s) launched ===")
    for a in launched:
        print(f"  {a['ticket']}  {a['handle']}  {a['title']}")

    # Read back, because "sent" is a claim and the screen is the evidence.
    print()
    time.sleep(30)
    for a in launched:
        r = orca(["orca", "terminal", "read", "--terminal", a["handle"], "--json"])
        tail = (r.get("result", {}).get("terminal", {}).get("tail") or []) if r.get("ok") else []
        text = "\n".join(tail)
        mangled = re.search(r"'Read' is not recognized", text) is not None
        got_brief = "ports/" in text
        active = re.search(r"[⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏]|Thought|Explored|pytest|git status", text) is not None
        print(
            f"  {a['ticket']}: prompt_mangled={mangled}  brief_echoed={got_brief}  active={active}"
        )

    return 0 if len(launched) == len(SET4) else 1


if __name__ == "__main__":
    sys.exit(main())
