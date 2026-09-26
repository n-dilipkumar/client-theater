"""Headless verification of what a browser would actually load and do.

Checks, in order:
  1. the SPA shell is served at /
  2. the hashed JS and CSS assets referenced by the shell resolve
  3. the API the SPA calls at runtime responds
  4. a full write -> read -> audit round trip works through HTTP
"""

import json
import os
import re
import sys
import urllib.error
import urllib.request

# Overridable so a worktree can verify against its own server when port 8000 is
# already taken by another checkout.
BASE = os.environ.get("DSR_VERIFY_BASE", "http://127.0.0.1:8000").rstrip("/")
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

print("\n=== 6. white-label room on a custom domain (WF-017) ===")
# Runs the researched flow against the live server: verify a domain, save it,
# read the share link back, then confirm the link still resolves after the
# domain is released. Fixtures stand in for DNS so this is deterministic.
try:
    status, body = get("/api/white-label/config")
    config = json.loads(body)
    check("GET /api/white-label/config", status == 200 and "cname_target" in config)

    status, room = post("/api/records/room", {"name": "Verify White Label", "account": "Acme"})
    room_id = room["id"]

    status, minted = post(f"/api/rooms/{room_id}/white-label/link-secret", {})
    white = json.loads(minted) if isinstance(minted, str) else minted
    check("a share link is minted", white.get("has_link_secret") is True, white.get("share_url", ""))
    check(
        "the default-host link is usable before any domain is set",
        white.get("share_url", "").startswith(BASE),
        white.get("share_url", ""),
    )

    secret_host = BASE.split("//", 1)[-1]

    status, report = post("/api/white-label/verify", {"domain": "proposals.acme.com"})
    report = json.loads(report) if isinstance(report, str) else report
    check("a propagated CNAME verifies", report.get("ready") is True, json.dumps(report.get("checks")))

    status, report = post("/api/white-label/verify", {"domain": "wrong.acme.com"})
    report = json.loads(report) if isinstance(report, str) else report
    check("a wrong CNAME is refused with a reason", report.get("ready") is False)

    status, claimed = post(
        f"/api/rooms/{room_id}/white-label/domain", {"domain": "proposals.acme.com"}
    )
    claimed = json.loads(claimed) if isinstance(claimed, str) else claimed
    check("the domain is saved", claimed.get("domain") == "proposals.acme.com")
    check(
        "the share link moves to the custom host",
        claimed.get("share_url", "").startswith("https://proposals.acme.com/"),
        claimed.get("share_url", ""),
    )
    check(
        "the slug and secret are preserved across the host change",
        claimed.get("slug") in claimed.get("share_url", ""),
        claimed.get("slug", ""),
    )

    secret = claimed["slug"].rsplit("-", 1)[-1]
    status, resolved = get(f"/api/white-label/links/{secret}?host=proposals.acme.com")
    resolved = json.loads(resolved) if isinstance(resolved, str) else resolved
    check("the link resolves on the custom host", resolved.get("room_id") == room_id)

    status, resolved = get(f"/api/white-label/links/{secret}?host={secret_host}")
    resolved = json.loads(resolved) if isinstance(resolved, str) else resolved
    check(
        "the same link still resolves on the default host",
        resolved.get("room_id") == room_id,
        "sourced: old links keep working",
    )

    # The researched hazard: re-pointing a domain must not break shared links.
    request = urllib.request.Request(f"{BASE}/api/rooms/{room_id}/white-label/domain", method="DELETE")
    with urllib.request.urlopen(request, timeout=15) as response:
        released = json.loads(response.read())
    check("the domain is released", released.get("domain") is None)

    status, resolved = get(f"/api/white-label/links/{secret}?host={secret_host}")
    resolved = json.loads(resolved) if isinstance(resolved, str) else resolved
    check("releasing a domain does not break the link", resolved.get("room_id") == room_id)

    request = urllib.request.Request(
        f"{BASE}/api/rooms/{room_id}/white-label/branding",
        data=json.dumps({"accent": "url(https://evil.example.net/x)"}).encode(),
        headers={"Content-Type": "application/json"},
        method="PATCH",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            check("an unsafe colour is refused", False, f"status={response.status}")
    except urllib.error.HTTPError as exc:
        check("an unsafe colour is refused", exc.code == 422, f"status={exc.code}")

    status, audit = get(f"/api/audit?record_id={room_id}")
    sources = [e["source"] for e in json.loads(audit)["entries"]]
    check(
        "every white-label change is audited",
        any("custom domain" in s for s in sources) and any("link secret" in s for s in sources),
        "; ".join(sorted(set(sources))),
    )
except Exception as exc:  # noqa: BLE001
    check("white-label flow", False, str(exc))

print("\n" + "=" * 60)
if failures:
    print(f"FAILED: {len(failures)} check(s): {failures}")
    sys.exit(1)
print("All headless browser-equivalent checks passed.")
