"""Dispatch a batch of build agents: one tab each, from the committed briefs.

The goal asks for ten sets of ten. The port programme delivered fourteen features
across four ad-hoc batches, so this is the first batch sized to the target rather
than to what happened to be in flight - WF-018 through WF-027, ten builds, from
research that already exists.

Same discipline as every batch before it, and every item is there because
something went wrong the time before:

  * No character cmd.exe treats specially in the prompt, asserted before sending.
    A Windows terminal reads a backtick as command substitution and executes the
    first word, which ate two agent briefs in set 2. The prompt is one line of
    plain words, so there is nothing in it for a shell to misread.

  * The prompt says commit locally and stop, and do not push. Set 1's prompt said
    to push, which the agents cannot do, so they deadlocked on a permission dialog
    they have no way to answer.

  * Do not use the system temp folder. A set-4 agent used %TEMP% as a scratch
    directory, blocked on a permission dialog, and needed a reviewer to answer it
    by sending raw escape sequences. The brief now warns, and so does the prompt,
    because the cost of that one is a whole agent's turn.

  * The screen is read back afterwards. A send receipt is a claim; the `accepted`
    field comes back empty on a successful send in this Orca build, so it is not
    evidence of anything.

  * The mangled-prompt detector matches only the word that starts the prompt. A
    broad "not recognized as an internal or external command" also fires when an
    agent tries a Unix idiom in cmd - `| tail -n 20` - which one did and recovered
    from unaided. A check that cries wolf gets ignored.

The batches are sized to ten because the goal says ten sets of ten, not because
ten is the most the machine can hold. If a batch comes back hot - slow, failing,
or half-blocked on dialogs - the next one is smaller, and the reason goes in the
dispatch record.
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

# The batch is sized to ten because the goal asks for ten sets of ten, and the
# range is a command-line argument so the next batch is a run rather than an edit
# - edits to this file have been reverted by shell restarts repeatedly this
# session, and a reverted batch number would silently re-dispatch work that has
# already landed.
#
#   python orchestration/dispatch_build_batch.py 28 38
BATCH = list(
    range(
        int(sys.argv[1]) if len(sys.argv) > 1 else 18, int(sys.argv[2]) if len(sys.argv) > 2 else 28
    )
)

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


def orca(args, timeout=180):
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


def brief_title(n):
    p = ROOT / "orchestration" / "ports" / f"WF-{n:03d}.md"
    if not p.exists():
        return None
    first = p.read_text(encoding="utf-8", errors="replace").splitlines()[0]
    # Tolerant of the brief KIND as well as the ticket spelling, because there
    # are two kinds of brief in this directory and a build dispatcher that
    # silently skips every port brief looks exactly like a dispatcher that found
    # no work. WF-001, WF-005 and WF-014 are ports whose headers say
    # "Port brief:" - a strict "Build brief" match skipped all three, quietly.
    m = re.match(r"#\s*(?:Build|Port)\s*brief:\s*WF[-_]?\d+\s*-\s*(.+)", first, re.I)
    return m.group(1).strip() if m else None


def main():
    launched = []
    for n in BATCH:
        ticket = f"WF-{n:03d}"
        title = brief_title(n)
        if not title:
            print(f"=== {ticket} === SKIPPED: no brief on main")
            continue
        print(f"=== {ticket}  {title} ===")

        text = POINTER.format(n=n)
        bad = UNSAFE & set(text)
        if bad:
            print(f"  REFUSING TO SEND: prompt contains {bad}")
            continue

        dirname = f"dsr-wf-{n:03d}-build"
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

        # A meaningful tab name: the ticket and what the workflow does, so the
        # board is readable without cross-referencing anything.
        tab = f"DSR {ticket} {title[:44]}"
        term = orca(
            [
                "orca",
                "terminal",
                "create",
                "--worktree",
                wt_id,
                "--title",
                tab,
                "--command",
                "opencode",
                "--json",
            ]
        )
        if not term.get("ok"):
            print(f"  terminal failed: {str(term)[:200]}")
            continue
        handle = term["result"]["terminal"]["handle"]

        time.sleep(9)
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
            timeout=110,
        )
        print(f"  tab {handle}  send_ok={send.get('ok')}")
        launched.append(
            {
                "ticket": ticket,
                "n": n,
                "handle": handle,
                "worktree": dirname,
                "worktree_id": wt_id,
                "title": tab,
                "workflow": title,
                "brief": f"orchestration/ports/{ticket}.md",
            }
        )

    # MERGE into the handle file, do not overwrite it. It used to write the
    # current batch only, so every dispatch erased the record of the agents
    # already running - which is exactly how agent_watch came to report 13
    # dispatched when 20 were in flight, and why a batch of ten looked like it
    # had never started. A log that forgets is worse than no log.
    handles_path = ROOT / "data" / "dispatched_batch.json"
    known: dict[str, dict] = {}
    if handles_path.exists():
        try:
            for rec in json.loads(handles_path.read_text(encoding="utf-8")):
                if isinstance(rec, dict) and rec.get("ticket"):
                    known[rec["ticket"]] = rec
        except (json.JSONDecodeError, OSError):
            pass  # a corrupt log must not stop a dispatch
    for a in launched:
        known[a["ticket"]] = a
    handles_path.write_text(json.dumps(list(known.values()), indent=2), encoding="utf-8")

    print()
    print(f"=== {len(launched)} agent tab(s) launched this run, {len(known)} known in total ===")
    for a in launched:
        print(f"  {a['ticket']}  {a['handle']}  {a['title']}")

    print()
    print("=== read back: is each one actually running? ===")
    time.sleep(35)
    for a in launched:
        r = orca(["orca", "terminal", "read", "--terminal", a["handle"], "--json"])
        tail = (r.get("result", {}).get("terminal", {}).get("tail") or []) if r.get("ok") else []
        text = "\n".join(tail)
        # Narrow on purpose: a broad "not recognized" also fires on an agent's own
        # Unix idiom in cmd, which it recovers from by itself.
        mangled = re.search(r"'Read' is not recognized", text) is not None
        got_brief = "ports/" in text
        active = re.search(r"Thought|Explored|Read |pytest|git status|Thinking", text) is not None
        print(
            f"  {a['ticket']}: prompt_mangled={mangled}  brief_echoed={got_brief}  active={active}"
        )
    return 0 if len(launched) == len(BATCH) else 1


if __name__ == "__main__":
    sys.exit(main())
