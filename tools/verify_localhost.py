"""Headless verification of what a browser would actually load and do.

Checks, in order:
  1. the SPA shell is served at /
  2. the hashed JS and CSS assets referenced by the shell resolve
  3. the API the SPA calls at runtime responds
  4. a full write -> read -> audit round trip works through HTTP
"""

import json
import re
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8000"
failures = []


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=15) as response:
        return response.status, response.read().decode("utf-8", "replace")


def post(path, payload, method="POST"):
    request = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method=method,
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        return response.status, json.loads(response.read())


def check(label, condition, detail=""):
    mark = "PASS" if condition else "FAIL"
    print(f"[{mark}] {label}{(' -> ' + detail) if detail else ''}")
    if not condition:
        failures.append(label)


print("=== 1. SPA shell ===")
try:
    status, html = get("/")
    check("GET / returns 200", status == 200, f"status={status}")
    check("shell has #root mount point", 'id="root"' in html)
    check("shell loads the JS bundle", bool(re.search(r'src="/assets/[^"]+\.js"', html)))
    check("shell loads the CSS bundle", bool(re.search(r'href="/assets/[^"]+\.css"', html)))
    check("shell sets a responsive viewport", 'name="viewport"' in html)
except (urllib.error.URLError, OSError) as exc:
    check("GET / returns 200", False, str(exc))
    html = ""

print("\n=== 2. hashed assets resolve ===")
for asset in re.findall(r'(?:src|href)="(/assets/[^"]+)"', html):
    try:
        status, body = get(asset)
        check(f"GET {asset}", status == 200, f"{len(body)} bytes")
    except (urllib.error.URLError, OSError) as exc:
        check(f"GET {asset}", False, str(exc))

print("\n=== 3. deep link falls back to the SPA ===")
try:
    status, body = get("/audit")
    check("GET /audit serves the SPA (deep link works)", status == 200 and 'id="root"' in body)
except (urllib.error.URLError, OSError) as exc:
    check("GET /audit serves the SPA", False, str(exc))

print("\n=== 4. API the SPA depends on ===")
for path, key in [
    ("/api/health", "status"),
    ("/api/stats", "records"),
    ("/api/collections", "collections"),
    ("/api/records/room", "records"),
    ("/api/records/document", "records"),
    ("/api/audit?limit=5", "entries"),
]:
    try:
        status, body = get(path)
        parsed = json.loads(body)
        check(f"GET {path}", status == 200 and key in parsed)
    except Exception as exc:  # noqa: BLE001
        check(f"GET {path}", False, str(exc))

print("\n=== 5. write -> read -> audit round trip (what a click does) ===")
try:
    status, room = post("/api/records/room", {"name": "Verify Bot Room", "stage": "discovery", "smoke": True})
    room_id = room["id"]
    check("POST /api/records/room", status == 201 and room_id.startswith("room_"), room_id)

    status, updated = post(
        f"/api/records/room/{room_id}", {"stage": "evaluation"}, method="PATCH"
    )
    check("PATCH advances the stage", status == 200 and updated["data"]["stage"] == "evaluation")
    check("revision incremented", updated["revision"] == 2, f"revision={updated['revision']}")

    status, found = get(f"/api/records/room/{room_id}")
    check("GET returns the updated record", json.loads(found)["data"]["stage"] == "evaluation")

    status, audit = get(f"/api/audit?record_id={room_id}")
    entries = json.loads(audit)["entries"]
    actions = [e["action"] for e in entries]
    check("audit captured insert + update", set(actions) == {"insert", "update"}, str(actions))

    diff_entry = next(e for e in entries if e["action"] == "update")
    check(
        "audit diff records the changed field",
        diff_entry["diff"].get("stage") == {"from": "discovery", "to": "evaluation"},
        json.dumps(diff_entry["diff"]),
    )

    request = urllib.request.Request(f"{BASE}/api/records/room/{room_id}", method="DELETE")
    with urllib.request.urlopen(request, timeout=15) as response:
        check("DELETE soft-deletes", response.status == 200)

    status, audit = get(f"/api/audit?record_id={room_id}")
    check(
        "audit captured the delete",
        "delete" in [e["action"] for e in json.loads(audit)["entries"]],
    )
except Exception as exc:  # noqa: BLE001
    check("write/read/audit round trip", False, str(exc))

print("\n" + "=" * 60)
if failures:
    print(f"FAILED: {len(failures)} check(s): {failures}")
    sys.exit(1)
print("All headless browser-equivalent checks passed.")
