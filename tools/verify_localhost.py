"""Headless verification of what a browser would actually load and do.

Checks, in order:
  1. the SPA shell is served at /
  2. the hashed JS and CSS assets referenced by the shell resolve
  3. the API the SPA calls at runtime responds
  4. a full write -> read -> audit round trip works through HTTP
  5. the WF-001 wizard endpoints do what the three wizard steps need

Point it at a non-default instance with DSR_BASE_URL, which matters when
another worktree already holds port 8000.
"""

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = os.environ.get("DSR_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
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
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        # A refused request is a result to assert on, not an error to propagate.
        return exc.code, json.loads(exc.read() or b"{}")


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

print("\n=== 6. WF-001 wizard: account -> template -> name/URL ===")
try:
    status, body = get("/api/room-templates")
    templates = json.loads(body)["templates"]
    check("step 2 offers templates", status == 200 and len(templates) > 0, f"{len(templates)} templates")
    check(
        "every template carries an id and a pinned version",
        all(t.get("template_id") and t.get("template_version_id") for t in templates),
    )
    template_id = next((t["template_id"] for t in templates if t["template_id"] == "tpl_standard"), templates[0]["template_id"])

    status, body = get("/api/accounts")
    accounts = json.loads(body)["accounts"]
    check("step 1 lists accounts", status == 200, f"{len(accounts)} accounts")
    if not accounts:
        # A fresh database is a legitimate state: the wizard must say so rather
        # than fail, and creating a room must then be refused.
        status, refusal = post(
            "/api/rooms", {"name": "Verify Bot Room", "account_id": "account_missing", "template_id": template_id}
        )
        check(
            "refuses a room with no valid account",
            status == 404 and refusal["error"] == "account_not_found",
            f"status={status}",
        )
    else:
        account_id = accounts[0]["id"]
        status, created = post(
            f"/api/rooms?actor=verify-bot&request_id=verify-wf001",
            {
                "name": "Verify Bot Room",
                "account_id": account_id,
                "template_id": template_id,
                "smoke": True,
                "team_routing": {"queue": "enterprise"},
            },
        )
        room = created
        data = room.get("data", {})
        check("POST /api/rooms", status == 201 and data.get("name") == "Verify Bot Room", f"status={status}")
        check("room is bound to the chosen account", data.get("account_id") == account_id)
        check("room pins the chosen template version", data.get("template_id") == template_id and bool(data.get("template_version_id")))
        check("room is active by default", data.get("status") == "active")
        check("room carries a friendly URL", bool(data.get("friendly_url")), data.get("friendly_url", ""))
        check(
            "room is bound to exactly one site",
            room["site"]["id"] == data.get("site_id") and room["site"]["room_id"] == room["id"],
        )

        status, body = get(f"/api/rooms?q={urllib.parse.quote('Verify Bot')}")
        check("the new room appears in the list", json.loads(body)["count"] >= 1)

        status, body = get('/api/records/room?where={"team_routing.queue":"enterprise"}')
        check(
            "an unknown field is queryable without a migration",
            room["id"] in [r["id"] for r in json.loads(body)["records"]],
        )

        status, body = get("/api/audit?request_id=verify-wf001")
        entries = json.loads(body)["entries"]
        check(
            "room and site share one request id, one audit row each",
            sorted(e["collection"] for e in entries) == ["room", "site"],
            str([(e["collection"], e["action"]) for e in entries]),
        )
        check("audit records the actor", all(e["actor"] == "verify-bot" for e in entries))

        # A duplicate friendly URL is a conflict, and must write nothing.
        before = json.loads(get("/api/stats")[1])
        status, refusal = post(
            "/api/rooms",
            {
                "name": "Verify Bot Room Two",
                "account_id": account_id,
                "template_id": template_id,
                "friendly_url": data.get("friendly_url"),
            },
        )
        check(
            "a taken friendly URL is a 409, not a silent rewrite",
            status == 409 and refusal.get("error") == "friendly_url_taken",
            f"status={status} {refusal.get('error')}",
        )
        after = json.loads(get("/api/stats")[1])
        check(
            "a refused creation writes nothing",
            after["records"] == before["records"] and after["audit_entries"] == before["audit_entries"],
            f"records {before['records']}->{after['records']}, audit {before['audit_entries']}->{after['audit_entries']}",
        )
except urllib.error.HTTPError as exc:
    check("WF-001 wizard round trip", False, f"HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')[:200]}")
except Exception as exc:  # noqa: BLE001
    check("WF-001 wizard round trip", False, str(exc))

print("\n" + "=" * 60)
if failures:
    print(f"FAILED: {len(failures)} check(s): {failures}")
    sys.exit(1)
print("All headless browser-equivalent checks passed.")
