"""Heartbeat monitor for the CI-hardening programme.

Same design as the one used for the test-reduction programme, with its two known
defects already fixed:

  * it matches on sender and content, not on an exact subject string, so an
    agent that writes HEARTBEAT with stray quotes is still counted as reporting;
  * it keeps the FIRST hit per handle, because the inbox is newest-first, so the
    reported age is the newest and not the oldest.

A missing line is the finding. Silence is what this exists to detect.
"""

import json
import subprocess
import sys
from datetime import datetime, timezone

RUN = "run_a753c94f0894"

AGENTS = {
    "coverage": "term_a549081c-66b0-4aeb-aac7-b0c424ccc80c",
    "security": "term_43ee2a4b-b4fb-4848-aa9a-4fac49ab893c",
    "speed": "term_1644de72-4878-425b-8d91-d5733ea94303",
}

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

    Matches on who sent it and what it says, not on the exact subject. An agent
    that mis-spells its subject is still reporting, and a monitor that calls it
    silent would send me chasing a live agent.
    """
    if m.get("from_handle") not in AGENTS.values():
        return False
    text = f"{m.get('subject') or ''} {m.get('body') or ''}".upper()
    return any(k in text for k in ("HEARTBEAT", "AGENT:", "ELAPSED", "BLOCKER", "WORKING"))


def main():
    now = datetime.now(timezone.utc)
    msgs = [m for m in inbox() if m.get("run_id") == RUN]
    mine = [m for m in msgs if is_report(m)]

    print(f"run {RUN}")
    print(f"reports received: {len(mine)}")
    print()

    # Newest first, so the first hit per handle is the newest.
    seen = {}
    for m in mine:
        seen.setdefault(m["from_handle"], m)

    ok = True
    for name, handle in AGENTS.items():
        m = seen.get(handle)
        if not m:
            print(f"  {name:<10} NO REPORT")
            ok = False
            continue
        ts = datetime.fromisoformat(m["created_at"].replace("Z", "+00:00"))
        age = (now - ts).total_seconds()
        late = age > OVERDUE_SECONDS
        if late:
            ok = False
        print(f"  {name:<10} {'LATE' if late else 'OK  '} {age / 60:6.1f} min ago")
        body = (m.get("body") or "").strip()
        for line in body.splitlines()[:2]:
            if line.strip():
                print(f"              {line.strip()[:150]}")

    print()
    print("ALL AGENTS REPORTING" if ok else "SOME AGENTS SILENT - investigate")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
