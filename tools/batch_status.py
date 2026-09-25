#!/usr/bin/env python3
"""Report the state of every sub-agent tab and feature branch.

`opencode run` exits when the agent finishes, leaving the shell prompt visible in
the terminal. That is the completion signal; while the agent is working the
tail shows its last action instead.

    python tools/batch_status.py
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "orchestration" / "batch-state.json"
REPO_ID = "8964203a-831a-425f-8fd7-ebc3a0fc2e46"

PROMPT_RE = re.compile(r"^>+\s*[\w-]+\s*[·|]\s*(.+?)\s*$")


def orca(*args: str) -> str:
    completed = subprocess.run(
        ["orca", *args], capture_output=True, text=True, errors="replace", timeout=120
    )
    return completed.stdout or ""


def parse_json(output: str) -> dict:
    decoder = json.JSONDecoder()
    for index, char in enumerate(output):
        if char != "{":
            continue
        try:
            value, _ = decoder.raw_decode(output, index)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return {}


def git(*args: str) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, errors="replace", timeout=60
    )
    return (completed.stdout or "").strip()


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass

    if not STATE.is_file():
        print("no batch-state.json; nothing spawned yet")
        return 1

    results = json.loads(STATE.read_text(encoding="utf-8"))["results"]

    # Branches that exist and how far ahead of main they are. Feature branches
    # are named feature/<ticket>-<slug>, so match on the ticket segment rather
    # than a prefix of the whole ref.
    branches = {}
    for line in git("for-each-ref", "--format=%(refname:short)", "refs/heads").splitlines():
        if line.startswith("feature/"):
            ahead = git("rev-list", "--count", f"main..{line}")
            branches[line] = int(ahead) if ahead.isdigit() else 0

    rows = []
    for result in results:
        handle = result.get("handle")
        ticket = result["ticket"]
        branch = next((b for b in branches if f"/{ticket}-" in b), None)
        commits = branches.get(branch, 0) if branch else 0

        status, last = "unknown", ""
        if handle:
            payload = parse_json(orca("terminal", "read", "--terminal", handle, "--json"))
            terminal = (payload.get("result") or {}).get("terminal") or {}
            tail = [line for line in terminal.get("tail", []) if line.strip()]
            status = terminal.get("status", "unknown")
            # A shell prompt at the end means `opencode run` returned.
            if tail and re.search(r"[A-Za-z]:\\.*>\s*$", tail[-1]):
                status = "FINISHED"
                for line in reversed(tail[:-1]):
                    if line.strip():
                        last = line.strip()
                        break
            elif tail:
                last = tail[-1].strip()
        rows.append((ticket, status, commits, branch or "-", last[:70]))

    print(f"{'TICKET':8} {'STATUS':10} {'COMMITS':8} BRANCH")
    print("-" * 100)
    done = 0
    for ticket, status, commits, branch, last in sorted(rows):
        mark = "done" if (commits > 0 and status == "FINISHED") else status
        if mark == "done":
            done += 1
        print(f"{ticket:8} {mark:10} {commits:8} {branch}")
        if last:
            print(f"{'':8} {'':<10} {'':8} | {last}")
    print("-" * 100)
    print(f"complete (committed and agent exited): {done}/{len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
