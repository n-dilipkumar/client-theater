#!/usr/bin/env python3
"""End-to-end check of WF-004 against a running server.

Mirrors tools/verify_localhost.py but for the WF-004 routes and against a
chosen port, because the shared verifier hard-codes 8000 and that port belongs
to another worktree. Throwaway: the pytest suite is the real gate.

    ./.venv/Scripts/python tools/wf004_smoke.py http://127.0.0.1:8014
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8014"
# Unique per run: once an address has accepted, a re-run would find it already
# in the room and take the immediate-join path instead of the invite path.
TAG = str(int(time.time()))
ONE = f"smoke.one.{TAG}@example.com"
TWO = f"smoke.two.{TAG}@example.com"
failures: list[str] = []


def call(path, payload=None, method="GET"):
    request = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Content-Type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            body = response.read().decode("utf-8", "replace")
            try:
                return response.status, (json.loads(body) if body else {})
            except json.JSONDecodeError:
                return response.status, {"raw": body}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        try:
            return exc.code, json.loads(body)
        except json.JSONDecodeError:
            return exc.code, {"raw": body[:200]}


def check(label, condition, detail=""):
    print(f"[{'PASS' if condition else 'FAIL'}] {label}{(' -> ' + str(detail)) if detail else ''}")
    if not condition:
        failures.append(label)


status, health = call("/api/health")
check("health", status == 200 and health.get("status") == "ok")

# The SPA catch-all is registered last; these routes must not be shadowed by it.
status, shell = call("/")
check("SPA shell served", status == 200, f"{len(shell.get('raw', ''))} bytes" if "raw" in shell else "html")

rooms = call("/api/records/room")[1].get("records", [])
check("seeded rooms present", len(rooms) >= 4, f"{len(rooms)} rooms")
room = next(r for r in rooms if r["data"].get("owner"))

status, roles = call("/api/access/roles?actor_role=room_owner")
check("role vocabulary is not shadowed by the SPA route", status == 200 and len(roles["roles"]) == 3)

room_id = room["id"]
status, snapshot = call(f"/api/rooms/{room_id}/access?actor={room['data']['owner']}")
check("access snapshot", status == 200, f"{len(snapshot['members'])} members, banner={snapshot['banner']!r}")
check("owner may share", snapshot["actor"]["can_share"] is True)

# A viewer in the seed data must not be able to share.
viewer = next((m for m in snapshot["members"] if m["role"] == "viewer"), None)
if viewer:
    status, v = call(f"/api/rooms/{room_id}/access?actor={viewer['principal']}")
    check("viewer has no Share capability", v["actor"]["can_share"] is False, f"role={v['actor']['role']}")
    status, denied = call(
        f"/api/rooms/{room_id}/invitations?actor={viewer['principal']}",
        {"emails": ["smoke@example.com"]},
        "POST",
    )
    check("viewer inviting is refused", status == 403, denied.get("detail"))
else:
    check("seed contains a viewer", False, "no viewer found")

# The full documented flow.
status, invited = call(
    f"/api/rooms/{room_id}/invitations?actor={room['data']['owner']}",
    {"emails": [ONE, TWO], "role": "content_contributor"},
    "POST",
)
check("invite accepted", status == 201, invited.get("message"))
check("two invitations pending", len(invited["pending"]) == 2, invited.get("pending"))

status, after = call(f"/api/rooms/{room_id}/access?actor={room['data']['owner']}")
pending = after["pending_invitations"]
# Each invitation carries its own 48-hour window from its own send time, so an
# older pending invitation legitimately shows less than 48 remaining hours.
fresh = next(i for i in pending if i["email"] == ONE)
check(
    "a fresh invitation has a 48h window",
    47.0 < fresh["hours_until_expiry"] <= 48.0 and not fresh["expired"],
    f"{fresh['hours_until_expiry']}h",
)
check(
    "every pending invitation is inside its own window",
    all(i["hours_until_expiry"] <= 48.0 and i["hours_until_expiry"] >= 0 for i in pending),
)

first = next(i for i in pending if i["email"] == ONE)
status, accepted = call(f"/api/invitations/{first['id']}/accept", None, "POST")
check("accept creates a grant", status == 200 and accepted["access"]["data"]["role"] == "content_contributor", accepted.get("detail") or status)

status, after = call(f"/api/rooms/{room_id}/access?actor={room['data']['owner']}")
member = next((m for m in after["members"] if m["principal"] == ONE), None)
check("accepted invitee is in Who Has Access", member is not None)
if member is None:
    print("  (stopping: cannot continue without the accepted member)")
    sys.exit(1)
check("No Expiration row", member["access_valid_until"] is None, member["access_valid_until"])

# Destructive change without confirmation.
status, needs = call(
    f"/api/access/{member['id']}?actor={room['data']['owner']}", {"role": "viewer"}, "PATCH"
)
check("role change without confirm is 428", status == 428, needs.get("detail"))

status, done = call(
    f"/api/access/{member['id']}?actor={room['data']['owner']}&confirm=true",
    {"role": "viewer"},
    "PATCH",
)
check("confirmed role change succeeds", status == 200 and done["access"]["role"] == "viewer")

status, removed = call(f"/api/access/{member['id']}?actor={room['data']['owner']}&confirm=true", None, "DELETE")
check("confirmed removal succeeds", status == 200, removed.get("principal"))

# The audit trail shows the whole story.
status, audit = call(f"/api/audit?collection=room_access&limit=100")
check("removal is audited", status == 200 and any(e["action"] == "delete" for e in audit["entries"]))

print()
print(f"{'FAILED: ' + ', '.join(failures) if failures else 'all checks passed'}")
sys.exit(1 if failures else 0)
