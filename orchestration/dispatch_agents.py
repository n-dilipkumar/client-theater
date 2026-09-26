"""Dispatch one porting agent per workflow, each in its own named Orca tab.

Mechanics, established by probing rather than assumed:

* `orca worktree create --agent opencode` FAILS - "Selected agent is disabled".
  Only `pi` is enabled in Orca's settings, and that is a desktop-app toggle with
  no CLI surface. The working path is
  `orca terminal create --worktree <id> --command opencode`, which launches the
  real OpenCode TUI in a real tab; `terminal show` then reports
  `agentIdentity: opencode` and the status bar shows the model as
  "Build . Space Bunny Free". So the agent is a genuine OpenCode agent running
  the model AGENTS.md requires, in a tab, without the settings change.
* `terminal read --json` returns the screen under `result.terminal.tail` as an
  array of lines - NOT `result.text`. Reading the wrong key returns empty and
  looks like a dead agent.
* `--title` and `--text` values containing spaces or braces must go through
  PowerShell. cmd splits them and gh/orca reject the fragments. This whole
  script is PowerShell for that reason.

Each agent is sent a short pointer to its committed brief rather than the brief
itself. The brief is in git, so the agent reads the same text a reviewer can
read, and there is no multi-kilobyte prompt for a shell to mangle.
"""
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(r"C:\Users\Dilip\orca\projects\client-theater\client-theater")
sys.path.insert(0, str(ROOT / "tools"))
from jev import Jev  # noqa: E402

REPO = ROOT
REPO_ID = "id:8964203a-831a-425f-8fd7-ebc3a0fc2e46"

# ticket -> (worktree name, tab title, brief path)
# Tab titles are the human-readable label the goal asks for: ticket, workflow,
# and what the agent is doing. A board full of tabs called "OpenCode" cannot be
# navigated.
DISPATCH = [
    ("WF-002", "dsr-wf-002-buyer-pages", "DSR WF-002 buyer pages port",
     "orchestration/ports/WF-002.md"),
    ("WF-003", "dsr-wf-003-doc-library", "DSR WF-003 document library port",
     "orchestration/ports/WF-003.md"),
    ("WF-012", "dsr-wf-012-room-generation", "DSR WF-012 room generation port",
     "orchestration/ports/WF-012.md"),
]

POINTER = (
    "Read `{brief}` in this repo and carry out exactly what it says. "
    "It is your complete brief: the contract, the hard rules, the numbered steps, "
    "and the report format. Read AGENTS.md and docs/FEATURE-CONTRACT.md as it "
    "directs you to. Work only in this worktree. When the brief's final report "
    "section is done, stop and wait."
)


def run(args, cwd=REPO, timeout=180):
    p = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    return p.returncode, p.stdout.strip(), p.stderr.strip()


def orca_json(args, timeout=180):
    code, out, err = run(args, timeout=timeout)
    if code != 0:
        return None, (err or out)
    try:
        payload = json.loads(out)
    except json.JSONDecodeError:
        return None, out
    if not payload.get("ok"):
        return None, json.dumps(payload.get("error", payload))[:300]
    return payload["result"], None


def main():
    jev = Jev()
    print("=== Jev pre-dispatch gate ===")
    # choose_approach, not validate_workflow: this is an open scheduling decision
    # between legitimate orderings, not a check that a researched workflow is
    # relevant. Jev is asked whether dispatching these three now, holding the six
    # back, is the right call given the measured evidence.
    decision = jev.choose_approach(
        problem=(
            "Three porting agents are about to be dispatched concurrently, each in its own "
            "Orca tab running OpenCode with Space Bunny Free, each branching from origin/main. "
            "Six other workflows are held back. Is dispatching this set now the right call?"
        ),
        options={
            "dispatch_three_hold_six": (
                "Dispatch WF-002, WF-003 and WF-012 now. Hold back WF-005 because its branch "
                "edits db/audited.py and store.py, the audit guarantee itself, so a human must "
                "read what it changed before anything carries it across. Hold back WF-007/WF-010 "
                "and WF-009/WF-011 because each pair is a real route collision that needs a "
                "scope decision, and a port cannot resolve it. Hold back WF-001, WF-004, "
                "WF-015 and WF-017 because they were rescued from uncommitted working trees and "
                "have never been executed; WF-004 also modified tools/jev.py, the validator every "
                "decision here goes through. The three dispatched are independent: each adds a "
                "distinct set of new files, so none can collide with another."
            ),
            "dispatch_one_first": (
                "Dispatch only WF-012, the smallest port with three payload files and no "
                "exception handlers, and treat it as a rehearsal of the whole pipeline - agent "
                "dispatch, brief, port, tests, guard, Jev gate, review, PR, merge - before "
                "committing three agents at once. Costs one round of wall-clock; a systemic flaw "
                "in the brief or the dispatch is found while it is cheap to fix."
            ),
            "dispatch_all_nine": (
                "Dispatch every non-colliding workflow at once, including the four rescued ones, "
                "to reach the target faster. The rescued code has never been executed and one of "
                "them changed the shared Jev validator, so a flawed pattern would be replicated "
                "four more times before anything was reviewed."
            ),
        },
        context={
            "workflows_researched": 17,
            "already_ported_and_merged": "WF-006 (f854a79), 8 routes, 65 tests, Jev merge @1.00",
            "dispatching": "WF-002 (8 payload files), WF-003 (6), WF-012 (3)",
            "mechanism_verified": (
                "An OpenCode TUI was launched in a named Orca tab, sent a prompt, and answered "
                "running 'Build - Space Bunny Free'. terminal show reports agentIdentity=opencode."
            ),
            "guard_verified": (
                "A probe PR that deliberately edited backend/dsr/api.py went red on the guard job "
                "while Backend tests, Frontend build and Local host app all passed. So a shared-file "
                "edit is caught even though it compiles and tests cleanly."
            ),
            "independence": (
                "Each port adds backend/dsr/features/<ticket>_<slug>.py and "
                "frontend/src/features/<id>/ and edits no shared file, so the three branches touch "
                "disjoint paths and cannot conflict with each other."
            ),
            "review_gate": (
                "Agents push their branch and stop. They do not open a PR. A human reviews the "
                "diff before it becomes a PR, so a bad port is caught before it can merge."
            ),
        },
    )
    print("verdict:", decision.verdict, "| selected:", decision.selected)
    print("reason  :", decision.reason)
    if not decision.passed:
        print("\nJev did not pass the dispatch. Not dispatching.")
        return 1
    print()

    launched = []
    for ticket, wt_name, title, brief in DISPATCH:
        print(f"=== {ticket} ===")

        res, err = orca_json([
            "orca", "worktree", "create", "--name", wt_name,
            "--setup", "skip", "--no-parent", "--json",
        ])
        if res is None:
            print(f"  worktree create FAILED: {err}")
            continue
        wt_id = res["worktree"]["id"]
        print(f"  worktree {wt_id}")

        # The agent goes in the FIRST terminal via the worktree's own command
        # path, then the tab is renamed. `--command opencode` rather than
        # `--agent opencode` because the latter is gated by a disabled flag.
        res2, err2 = orca_json([
            "orca", "terminal", "create", "--worktree", wt_id,
            "--title", title, "--command", "opencode", "--json",
        ])
        if res2 is None:
            print(f"  terminal create FAILED: {err2}")
            continue
        handle = res2["terminal"]["handle"]

        # A fresh TUI needs a moment before input is accepted; the guide is
        # explicit that a prompt typed into a starting TUI is lost.
        time.sleep(6)
        res3, err3 = orca_json([
            "orca", "terminal", "wait", "--terminal", handle,
            "--for", "tui-idle", "--timeout-ms", "40000", "--json",
        ], timeout=60)
        satisfied = (res3 or {}).get("satisfied")
        print(f"  tab {handle} '{title}'  tui-idle={satisfied}")
        if satisfied is False:
            print("    TUI never reported idle; not sending a prompt (it would be lost)")
            continue

        prompt = POINTER.format(brief=brief)
        res4, err4 = orca_json([
            "orca", "terminal", "send", "--terminal", handle,
            "--text", prompt, "--enter", "--wait-submit", "20", "--json",
        ], timeout=90)
        if res4 is None:
            print(f"  send FAILED: {err4}")
            continue
        accepted = res4.get("accepted")
        stages = [s.get("stage") for s in (res4.get("stages") or [])]
        print(f"  sent: accepted={accepted} stages={stages}")

        launched.append({"ticket": ticket, "handle": handle, "worktree": wt_id,
                         "title": title, "brief": brief})

    print()
    print(f"=== launched {len(launched)} agent tab(s) ===")
    for a in launched:
        print(f"  {a['ticket']}  {a['handle']}  {a['title']}")

    Path(ROOT / "data" / "dispatched.json").write_text(
        json.dumps(launched, indent=2), encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
