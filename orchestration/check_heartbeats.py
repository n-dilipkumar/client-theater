"""Verify that heartbeats from every agent reach the orchestrator inbox.

Prints one line per agent: age of its most recent heartbeat, and whether the
agent is inside the 10-minute cadence. A missing line is itself the finding.

Run with:  python check_heartbeats.py
"""

import json
import subprocess
import sys
from datetime import datetime, timezone

RUN = "run_6e0bc978dd0e"
SUBJECT = "HEARTBEAT"

AGENTS = {
    "harness": "term_e2755632-1410-479f-9ab6-129bd7693c0c",
    "core": "term_350fd287-1759-40dc-9cc8-2e21cc9b170d",
    "features-a": "term_5fbc0ed0-8e93-44a7-9397-8a084be65ad5",
    "features-b": "term_866f33ca-c921-4153-8ee7-d488a7293222",
}

# How long to allow before a missing heartbeat is a finding, not a cadence gap.
OVERDUE_SECONDS = 12 * 60


def inbox():
    r = subprocess.run(
        ["orca", "orchestration", "inbox", "--json", "--limit", "60"],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        print("INBOX FAILED:", r.stderr.strip()[:400])
        return []
    try:
        return json.loads(r.stdout).get("result", {}).get("messages", [])
    except json.JSONDecodeError as e:
        print("INBOX JSON FAILED:", e)
        return []


def is_report(m):
    """Does this message count as a status report from one of my agents?

    Matches on who sent it and what it says, not on the exact subject string.
    An agent that writes HEARTBEAT with stray quotes is still reporting, and a
    monitor that reports it as silent would send me chasing a live agent. The
    failure that matters is silence, not spelling.
    """
    if m.get("from_handle") not in AGENTS.values():
        return False
    text = f"{m.get('subject') or ''} {m.get('body') or ''}".upper()
    # A report names its agent, or reports on its own task, or announces a
    # blocker. Any of those means the agent is talking to me.
    return any(k in text for k in ("HEARTBEAT", "AGENT:", "ELAPSED", "BLOCKER", "WORKING"))


def main():
    now = datetime.now(timezone.utc)
    msgs = [m for m in inbox() if m.get("run_id") == RUN]
    mine = [m for m in msgs if is_report(m)]

    print(f"run {RUN}")
    print(f"heartbeats received: {len(mine)} of {len(AGENTS)} agents")
    print()

    # The inbox is newest first, so the FIRST hit per handle is the newest.
    # Overwriting instead would report an age hours stale and make a live agent
    # look like it had gone quiet.
    seen = {}
    for m in mine:
        seen.setdefault(m["from_handle"], m)

    ok = True
    for name, handle in AGENTS.items():
        m = seen.get(handle)
        if not m:
            print(f"  {name:<12} NO HEARTBEAT")
            ok = False
            continue
        ts = datetime.fromisoformat(m["created_at"].replace("Z", "+00:00"))
        age = (now - ts).total_seconds()
        flag = "OK  " if age <= OVERDUE_SECONDS else "LATE"
        if age > OVERDUE_SECONDS:
            ok = False
        print(f"  {name:<12} {flag} {age / 60:6.1f} min ago")
        body = (m.get("body") or "").strip()
        for line in body.splitlines()[:2]:
            if line.strip():
                print(f"               {line.strip()[:150]}")

    print()
    print("ALL AGENTS REPORTING" if ok else "SOME AGENTS SILENT - investigate")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())