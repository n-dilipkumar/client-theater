#!/usr/bin/env python3
"""Create an Orca worktree and an OpenCode sub-agent tab per workflow.

This is the batch runner for Phase 5. Each workflow gets:

  * its own Orca worktree (isolated checkout on its own branch)
  * its own terminal tab running ``opencode run`` on Space Bunny Free
  * a prompt built from the ticket's researched evidence

Sub-agents are told explicitly not to push or open a PR. Merging is a
deliberate later step, after review and the Jev gate, so that no sub-agent can
land unreviewed code.

    python tools/spawn_batch.py --from WF-001 --to WF-010
    python tools/spawn_batch.py --from WF-001 --count 1 --dry-run
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "docs" / "research" / "digital-sales-room-workflows"
MODEL = "opencode/space-bunny-free"
REPO_ID = "8964203a-831a-425f-8fd7-ebc3a0fc2e46"

PROMPT = """You are the implementation sub-agent for ticket {ticket} of an open-source Digital Sales Room.

Work ONLY inside this worktree. Do not touch any other directory.

1. Read AGENTS.md and obey it. It defines the storage, validation, schema-flexibility and design-system rules.
2. Read docs/research/digital-sales-room-workflows/wf/{ticket}.md. This is the researched workflow you are implementing, with its sources. If it records a capability as unsourced, treat it as a design inference and say so in your report.
3. Create and switch to branch feature/{ticket}-{slug}.
4. Implement the workflow: React + Tailwind UI, Python FastAPI backend, SQLite only through the audited wrapper, schema-flexible APIs at every layer.
5. Follow design-system/digital-sales-room/MASTER.md for the UI.
6. Write tests. A feature without tests is not finished.
7. Run the backend suite. The shared virtualenv is already linked into this worktree, so run it from the backend directory:

       cd backend
       ../.venv/Scripts/python -m pytest

   Running pytest from the repository root fails with `ModuleNotFoundError: No module named 'dsr'`, because the package root is `backend/`. Frontend dependencies are linked too, so `cd frontend && npm run build` works without an install.

Do NOT push. Do NOT open a PR. Do NOT merge. The Orchestrator handles that after review.

Do NOT run a server, and do NOT verify over HTTP. A shared server already owns
port 8000 and it runs the MAIN checkout, not your worktree, so any endpoint you
added will 404 there and any check you make against it is meaningless. Several
agents have already reported false failures this way. Verify with the pytest
suite instead: it runs in-process against your worktree's own code.

Commit your work on the feature branch before you finish, so the work is not
lost when this session ends.

When finished, report concisely: files changed, test results (paste the summary line), and anything you could not complete or that looked ambiguous in the research."""


def orca(*args: str) -> tuple[int, str]:
    """Run an orca CLI command, returning (exit code, combined output).

    orca writes a crashpad banner to stderr on this platform, so stdout and
    stderr cannot be concatenated before parsing: the banner is not JSON.
    """
    completed = subprocess.run(
        ["orca", *args],
        capture_output=True,
        text=True,
        errors="replace",
        timeout=300,
    )
    return completed.returncode, (completed.stdout or "") + (completed.stderr or "")


def parse_json(output: str) -> dict:
    """Extract the first complete JSON object from CLI output.

    Leading noise is skipped and trailing noise ignored, so a crashpad banner
    or a post-run log line cannot break parsing.
    """
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
    raise ValueError("no JSON object found in output")


def load_tickets() -> dict[str, dict]:
    """Read the corpus index produced by build_corpus.py."""
    index = CORPUS / "workflows.json"
    if not index.is_file():
        raise SystemExit(f"missing {index}; run tools/build_corpus.py first")
    return {entry["ticket"]: entry for entry in json.loads(index.read_text(encoding="utf-8"))}


def spawn(ticket: str, entry: dict) -> dict:
    """Create the worktree and launch the sub-agent tab for one ticket."""
    slug = entry["slug"]
    name = ticket.lower()

    code, out = orca("worktree", "create", "--repo", f"id:{REPO_ID}", "--name", name, "--no-parent", "--json")
    if code != 0:
        return {"ticket": ticket, "ok": False, "stage": "worktree", "error": out[-400:]}

    try:
        worktree = parse_json(out)["result"]["worktree"]
    except (ValueError, KeyError, TypeError) as exc:
        return {"ticket": ticket, "ok": False, "stage": "worktree-parse", "error": f"{exc}: {out[-300:]}"}

    selector = f"id:{REPO_ID}::{worktree['path']}"
    prompt = PROMPT.format(ticket=ticket, slug=slug)
    command = f"opencode run -m {MODEL} --title {ticket} \"{prompt}\""

    code, out = orca(
        "terminal", "create", "--worktree", selector, "--title", f"{ticket} agent", "--command", command, "--json"
    )
    if code != 0:
        return {
            "ticket": ticket,
            "ok": False,
            "stage": "terminal",
            "worktree": worktree["path"],
            "error": out[-400:],
        }

    try:
        terminal = parse_json(out)["result"]["terminal"]
    except (ValueError, KeyError, TypeError) as exc:
        return {"ticket": ticket, "ok": False, "stage": "terminal-parse", "error": f"{exc}: {out[-300:]}"}

    # Record the work so status can be checked later without re-deriving ids.
    orca("worktree", "set", "--worktree", selector, "--workspace-status", "in-progress", "--json")
    orca("worktree", "set", "--worktree", selector, "--comment", f"{ticket}: sub-agent implementing {entry['name']}", "--json")

    return {
        "ticket": ticket,
        "ok": True,
        "name": entry["name"],
        "worktree": worktree["path"],
        "branch": worktree["git"]["branch"],
        "head": worktree["head"][:8],
        "handle": terminal["handle"],
    }


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--from", dest="start", required=True, help="first ticket, e.g. WF-001")
    parser.add_argument("--to", dest="end", help="last ticket inclusive")
    parser.add_argument("--count", type=int, help="spawn this many tickets from --from")
    parser.add_argument("--dry-run", action="store_true", help="print the plan without spawning")
    parser.add_argument("--delay", type=float, default=2.0, help="pause between spawns")
    args = parser.parse_args()

    tickets = load_tickets()
    ordered = sorted(tickets)

    if args.start not in tickets:
        raise SystemExit(f"unknown ticket {args.start}; corpus has {ordered[0]}..{ordered[-1]}")

    index = ordered.index(args.start)
    if args.end:
        if args.end not in tickets:
            raise SystemExit(f"unknown ticket {args.end}")
        selected = ordered[index : ordered.index(args.end) + 1]
    elif args.count:
        selected = ordered[index : index + args.count]
    else:
        selected = ordered[index:]

    print(f"batch: {len(selected)} workflow(s), {selected[0]}..{selected[-1]}\n")
    if args.dry_run:
        for ticket in selected:
            entry = tickets[ticket]
            print(f"  would spawn {ticket}  {entry['name']}")
            print(f"             branch feature/{ticket}-{entry['slug']}")
        return 0

    results = []
    for ticket in selected:
        result = spawn(ticket, tickets[ticket])
        results.append(result)
        if result["ok"]:
            print(f"  [ok]   {ticket}  {result['branch'].split('/')[-1]}  head={result['head']}  {result['handle']}")
        else:
            print(f"  [FAIL] {ticket}  at {result['stage']}: {result.get('error', '')[:200]}")
        time.sleep(args.delay)

    ok = sum(1 for r in results if r["ok"])
    print(f"\nspawned {ok}/{len(results)}")

    # Merge into the existing ledger rather than replacing it: each spawn run
    # covers a different range, and overwriting would orphan earlier handles.
    ledger = ROOT / "orchestration" / "batch-state.json"
    existing: dict[str, dict] = {}
    if ledger.is_file():
        try:
            for row in json.loads(ledger.read_text(encoding="utf-8")).get("results", []):
                existing[row["ticket"]] = row
        except (json.JSONDecodeError, KeyError, AttributeError):
            existing = {}
    for row in results:
        existing[row["ticket"]] = row
    ledger.write_text(
        json.dumps({"results": [existing[k] for k in sorted(existing)]}, indent=2), encoding="utf-8"
    )
    print(f"ledger now tracks {len(existing)} ticket(s) in orchestration/batch-state.json")
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
