"""Headless verification of what a browser would actually load and do.

Checks, in order:
  1. the SPA shell is served at /
  2. the hashed JS and CSS assets referenced by the shell resolve
  3. the API the SPA calls at runtime responds
  4. a full write -> read -> audit round trip works through HTTP
  5. the WF-015 identity gate: policy, verification email round trip, refusal
"""

import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:8000"
failures = []

# A real browser's User-Agent. The access gate flags link scanners and mail
# previewers, so the verifier has to present itself as the buyer it stands in
# for rather than as a script.
BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def get(path):
    with urllib.request.urlopen(urllib.request.Request(BASE + path, headers={"User-Agent": BROWSER_UA}), timeout=15) as response:
        return response.status, response.read().decode("utf-8", "replace")


def post(path, payload, method="POST"):
    request = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "User-Agent": BROWSER_UA},
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

print("\n=== 6. WF-015 identity gate round trip (what a buyer click does) ===")
try:
    status, room = post("/api/records/room", {"name": "Verifier Room", "stage": "discovery"})
    room_id = room["id"]

    status, _ = post(
        f"/api/rooms/{room_id}/access",
        {
            "mode": "verify_email",
            "collect_name": True,
            "collect_email": True,
            "domain_security": True,
            "allowed_domains": "Northwind.example, @contoso.example",
        },
        method="PUT",
    )
    check("PUT access policy", status == 200)

    status, effective = get(f"/api/rooms/{room_id}/access")
    policy = json.loads(effective)
    check(
        "allowlist normalised (no @, lowercased)",
        policy["policy"]["allowed_domains"] == ["northwind.example", "contoso.example"],
        json.dumps(policy["policy"]["allowed_domains"]),
    )
    check("policy resolved from the room", policy["level"] == "room", policy["level"])

    status, body = get(f"/api/rooms/{room_id}/access/requirements")
    requirements = json.loads(body)
    check(
        "buyer form is not told the allowlist",
        "northwind.example" not in body and "allowed_domains" not in requirements,
    )

    # A domain that is not on the list is refused, and no mail is sent.
    try:
        post(f"/api/rooms/{room_id}/access/sessions", {"name": "Outsider", "email": "who@elsewhere.example"})
        check("disallowed domain is refused", False, "request unexpectedly succeeded")
    except urllib.error.HTTPError as exc:
        refused = json.loads(exc.read())
        check("disallowed domain is refused", exc.code == 403 and refused["error"] == "domain_not_allowed", str(exc.code))

    status, outbox_body = get(f"/api/rooms/{room_id}/access/outbox")
    outbox = json.loads(outbox_body)
    check("refused attempt sent no verification email", outbox["count"] == 0, str(outbox["count"]))

    # The happy path, following the link exactly as the buyer would.
    status, submitted = post(
        f"/api/rooms/{room_id}/access/sessions",
        {"name": "Alex Buyer", "email": "alex@northwind.example"},
    )
    check("allowed domain issues a pending session", status == 201 and submitted["status"] == "pending_verification")

    status, outbox_body = get(f"/api/rooms/{room_id}/access/outbox")
    outbox = json.loads(outbox_body)
    check("one verification email queued", outbox["count"] == 1, str(outbox["count"]))
    link = outbox["messages"][0]["data"]["link"]
    token = urllib.parse.parse_qs(urllib.parse.urlparse(link).query)["token"][0]

    status, before = get(f"/api/rooms/{room_id}/access/session?token={token}")
    check("room is still closed before verification", json.loads(before)["status"] == "pending")

    status, verified_body = get(f"/api/rooms/{room_id}/access/verify?token={token}")
    verified = json.loads(verified_body)
    check("following the link verifies the session", status == 200 and verified["status"] == "verified")
    check("identity is returned", verified.get("identity", {}).get("email") == "alex@northwind.example")

    status, granted_body = get(f"/api/rooms/{room_id}/access/session?token={token}")
    granted = json.loads(granted_body)
    check("gate grants the room after verification", granted["status"] == "granted", granted["status"])
    check("first view is stamped", bool(granted.get("viewed_at")))

    status, sessions_body = get(f"/api/rooms/{room_id}/access/sessions")
    sessions = json.loads(sessions_body)
    check("seller sees one verified identity", sessions["verified"] == 1, json.dumps(sessions["by_status"]))

    status, activity_body = get("/api/records/activity?where=" + urllib.parse.quote('{"action":"viewed"}'))
    activity = json.loads(activity_body)
    check(
        "the verified identity reached analytics",
        activity["count"] >= 1
        and activity["records"][0]["data"].get("identity_verified") is True,
        str(activity["count"]),
    )

    status, audit_body = get("/api/audit?collection=access_session")
    audit_count = json.loads(audit_body)["count"]
    check("the session state changes are audited", audit_count >= 2, str(audit_count))

    # A scanner is refused and excluded from what the seller sees.
    scanner = urllib.request.Request(
        BASE + f"/api/rooms/{room_id}/access/sessions",
        data=json.dumps({"name": "Preview", "email": "preview@contoso.example"}).encode(),
        headers={"Content-Type": "application/json", "User-Agent": "Microsoft Outlook preview scanner"},
        method="POST",
    )
    try:
        urllib.request.urlopen(scanner, timeout=15)
        check("a link scanner is refused", False, "request unexpectedly succeeded")
    except urllib.error.HTTPError as exc:
        check("a link scanner is refused", exc.code == 403, str(exc.code))

    status, sessions_body = get(f"/api/rooms/{room_id}/access/sessions")
    sessions = json.loads(sessions_body)
    check(
        "scanner traffic is excluded from the seller's list",
        sessions["excluded_bots"] == 1 and sessions["count"] == 1,
        f"excluded={sessions['excluded_bots']} shown={sessions['count']}",
    )
except Exception as exc:  # noqa: BLE001
    check("identity gate round trip", False, str(exc))

print("\n" + "=" * 60)
if failures:
    print(f"FAILED: {len(failures)} check(s): {failures}")
    sys.exit(1)
print("All headless browser-equivalent checks passed.")
