"""Dispatch set 2: four more ports, one agent per workflow, one tab each.

Set 1 established the pipeline end to end and found three environment defects
along the way, all now fixed on main:

  * a worktree has no `.venv`, and the docs told agents to use one
  * `opencode.json` used V1 `permission`/`bash` syntax in a V2 config, so its
    rules - including the `external_directory` allow that stops the stray-read
    prompt - were never in force
  * the brief told agents to `git push`, which they cannot do, so they deadlocked
    on a permission dialog; a port now ends at a local commit

`opencode.json` is only read when a session starts, so agents launched now inherit
the fix for the first time.

Set 2 is the four workflows that were held back as collisions - WF-007, WF-009,
WF-010, WF-011. Measurement showed 0 identical `(method, path)` pairs between each
pair, and Jev answered `port_all_four_as_is` at 0.79. Two of them deliberately
share the /api/library prefix and two share /api/publishing, which is safe
precisely because their concrete paths differ - the case the host was built for.
"""

import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(r"C:\Users\Dilip\orca\projects\client-theater\client-theater")
REPO_ID = "id:8964203a-831a-425f-8fd7-ebc3a0fc2e46"

SET2 = [
    (
        "WF-007",
        "dsr-wf-007-content-library",
        "DSR WF-007 content library port",
        "orchestration/ports/WF-007.md",
    ),
    (
        "WF-010",
        "dsr-wf-010-library-search",
        "DSR WF-010 library search port",
        "orchestration/ports/WF-010.md",
    ),
    (
        "WF-009",
        "dsr-wf-009-publishing",
        "DSR WF-009 publishing port",
        "orchestration/ports/WF-009.md",
    ),
    (
        "WF-011",
        "dsr-wf-011-room-handover",
        "DSR WF-011 room handover port",
        "orchestration/ports/WF-011.md",
    ),
]

POINTER = (
    "Read `{brief}` in this repo and carry out exactly what it says. It is your "
    "complete brief: your environment, the contract, the hard rules, the numbered "
    "steps, and the report format. Read AGENTS.md and docs/FEATURE-CONTRACT.md as "
    "it directs you to.\n\n"
    "Work only inside this worktree. Commit locally when you finish and then stop - "
    "do not push, do not open a pull request, do not merge. A human reviews your "
    "diff and does that. If any permission dialog appears, reject it and carry on "
    "with the brief."
)


def orca_json(args, timeout=180):
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
        payload = json.loads(p.stdout)
    except json.JSONDecodeError:
        return None, (p.stdout or p.stderr)[:300]
    if not payload.get("ok"):
        return None, json.dumps(payload.get("error", payload))[:300]
    return payload["result"], None


def main():
    launched = []
    for ticket, wt_name, title, brief in SET2:
        print(f"=== {ticket} ===")

        res, err = orca_json(
            [
                "orca",
                "worktree",
                "create",
                "--name",
                wt_name,
                "--setup",
                "skip",
                "--no-parent",
                "--json",
            ]
        )
        if res is None:
            print(f"  worktree FAILED: {err}")
            continue
        wt_id = res["worktree"]["id"]

        res2, err2 = orca_json(
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
        if res2 is None:
            print(f"  terminal FAILED: {err2}")
            continue
        handle = res2["terminal"]["handle"]

        # A fresh TUI drops an early prompt. Wait for idle before sending.
        time.sleep(8)
        orca_json(
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

        res3, err3 = orca_json(
            [
                "orca",
                "terminal",
                "send",
                "--terminal",
                handle,
                "--text",
                POINTER.format(brief=brief),
                "--enter",
                "--wait-submit",
                "25",
                "--json",
            ],
            timeout=100,
        )
        if res3 is None:
            print(f"  send FAILED: {err3}")
            continue
        print(f"  tab {handle}  sent (accepted={res3.get('accepted')})")

        launched.append(
            {
                "ticket": ticket,
                "handle": handle,
                "worktree": wt_name,
                "worktree_id": wt_id,
                "title": title,
                "brief": brief,
            }
        )

    out = ROOT / "data" / "dispatched.json"
    existing = []
    if out.exists():
        try:
            existing = json.loads(out.read_text(encoding="utf-8-sig"))
            if isinstance(existing, dict):
                existing = existing.get("agents", [])
        except json.JSONDecodeError:
            existing = []
    out.write_text(json.dumps(launched, indent=2), encoding="utf-8")

    print()
    print(f"=== {len(launched)} agent tab(s) launched ===")
    for a in launched:
        print(f"  {a['ticket']}  {a['handle']}  {a['title']}")
    return 0 if len(launched) == len(SET2) else 1


if __name__ == "__main__":
    sys.exit(main())
