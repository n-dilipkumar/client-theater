"""Heartbeat monitor for the clean-up programme.

Third programme, third monitor. Both earlier defects are fixed here by
construction rather than by patch:

  * matches on sender and content, never on an exact subject string, so an
    agent that mis-spells its subject is still counted as reporting;
  * keeps the FIRST hit per handle, because the inbox is newest-first, so the
    age shown is the newest and not the oldest.

A missing line is the finding.
"""

import json
import subprocess
import sys
from datetime import datetime, timezone

RUN = "run_a753c94f0894"

AGENTS = {
    "branches": "term_83f9f0b6-4e75-495c-8496-9672029a7287",
    "files": "term_248c15fd-a492-485f-b6e8-9a9bd20b04f0",
    "orca": "term_d8d5cde1-cac6-48e7-8863-ad6894d1af79",
}

# The previous programme's agents are idle and finished. They stay listed so a
# silent one is not mistaken for a departed one.
PREVIOUS = {
    "coverage(old)": "term_a549081c-66b0-4aeb-aac7-b0c424ccc80c",
    "security(old)": "term_43ee2a4b-b4fb-4848-aa9a-4fac49ab893c",
    "speed(old)": "term_1644de72-4878-425b-8d91-d5733ea94303",
}

OVERDUE_SECONDS = 12 * 60


def inbox():
    r = subprocess.run(
        ["orca", "orchestration", "inbox", "--json", "--limit", "60"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if r.returncode != 0:
        print("INBOX FAILED:", (r.stderr or "")[:400])
        return []
    try:
        return json.loads(r.stdout).get("result", {}).get("messages", [])
    except json.JSONDecodeError as e:
        print("INBOX JSON FAILED:", e)
        return []


def is_report(m):
    if m.get("from_handle") not in set(AGENTS.values()) | set(PREVIOUS.values()):
        return False
    text = f"{m.get('subject') or ''} {m.get('body') or ''}".upper()
    return any(k in text for k in ("HEARTBEAT", "AGENT:", "ELAPSED", "BLOCKER", "WORKING"))


def main():
    now = datetime.now(timezone.utc)
    mine = [m for m in inbox() if m.get("run_id") == RUN and is_report(m)]

    seen = {}
    for m in mine:
        seen.setdefault(m["from_handle"], m)

    print(f"run {RUN}    reports {len(mine)}")
    print()

    ok = True
    for name, handle in AGENTS.items():
        m = seen.get(handle)
        if not m:
            print(f"  {name:<10} NO REPORT")
            ok = False
            continue
        age = (now - datetime.fromisoformat(m["created_at"].replace("Z", "+00:00"))).total_seconds()
        late = age > OVERDUE_SECONDS
        ok = ok and not late
        print(f"  {name:<10} {'LATE' if late else 'OK  '} {age / 60:6.1f} min")
        body = (m.get("body") or "").strip()
        for line in body.splitlines()[:2]:
            if line.strip():
                print(f"              {line.strip()[:140]}")

    print()
    print("ALL CLEAN-UP AGENTS REPORTING" if ok else "SOME SILENT - investigate")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
