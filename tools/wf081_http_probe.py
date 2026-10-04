"""Drive WF-081's own routes over real HTTP, and print what each answered.

The repo-wide sweep says no route returned 5xx. This says the same thing about
this feature's thirteen routes specifically, and prints each status so a reviewer
can read the shape of the answer rather than trust a count.

    C:\\Users\\Dilip\\dsrvenv\\Scripts\\python.exe tools\\wf081_http_probe.py \\
        --base http://127.0.0.1:8123
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

PREFIX = "/api/wf-081"


def call(base: str, method: str, path: str, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"Accept": "application/json"}
    if data:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", "replace")
        try:
            return error.code, json.loads(body)
        except json.JSONDecodeError:
            return error.code, body[:160]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8123")
    args = parser.parse_args()
    base = args.base.rstrip("/")

    rooms = json.load(urllib.request.urlopen(f"{base}/api/records/room?limit=5", timeout=20))
    room_id = (rooms.get("records") or [{}])[0].get("id", "room_absent")

    # A deadline well inside the researched window, read from the live clock.
    import time

    deadline = int(time.time()) + 10 * 86400

    sent = call(
        base,
        "POST",
        f"{PREFIX}/rooms/{room_id}/requests",
        {
            "subject": "HTTP probe agreement",
            "expires_at": deadline,
            "signatures": [{"email": "probe@example.com", "preferred_timezone": "+05:30"}],
        },
    )
    print("POST   /requests                                    ", sent[0])
    request_id = sent[1].get("id", "absent") if isinstance(sent[1], dict) else "absent"

    refused = call(
        base, "POST", f"{PREFIX}/rooms/{room_id}/requests", {"expires_at": deadline + 400 * 86400}
    )
    print(
        "POST   /requests  (deadline past 90 days)            ", refused[0], refused[1].get("error")
    )

    checks = [
        ("GET", f"{PREFIX}/vocabulary", None),
        ("GET", f"{PREFIX}/inferences", None),
        ("GET", f"{PREFIX}/rooms/{room_id}/requests", None),
        ("GET", f"{PREFIX}/rooms/{room_id}/requests?status=pending", None),
        ("GET", f"{PREFIX}/rooms/{room_id}/requests/{request_id}?tz=+05:30", None),
        ("GET", f"{PREFIX}/rooms/{room_id}/requests/absent-id", None),
        (
            "PUT",
            f"{PREFIX}/rooms/{room_id}/requests/{request_id}/expiry",
            {"expires_at": deadline + 86400},
        ),
        ("POST", f"{PREFIX}/rooms/{room_id}/requests/{request_id}/reminders", {}),
        ("GET", f"{PREFIX}/rooms/{room_id}/reminders", None),
        (
            "GET",
            f"{PREFIX}/rooms/{room_id}/requests/{request_id}/can-sign?email=probe@example.com",
            None,
        ),
        (
            "POST",
            f"{PREFIX}/rooms/{room_id}/requests/{request_id}/sign",
            {"email": "probe@example.com"},
        ),
        (
            "POST",
            f"{PREFIX}/rooms/{room_id}/requests/{request_id}/sign",
            {"email": "probe@example.com"},
        ),
        ("POST", f"{PREFIX}/rooms/{room_id}/sweep", {}),
        ("GET", f"{PREFIX}/rooms/{room_id}/events", None),
        ("GET", f"{PREFIX}/rooms/{room_id}/summary", None),
    ]
    faults = []
    for method, path, payload in checks:
        status, body = call(base, method, path, payload)
        note = body.get("error") if isinstance(body, dict) else ""
        print(f"{method:6} {path[len(PREFIX) :]:45}", status, note or "")
        if status == 0 or status >= 500:
            faults.append((method, path, status))

    print()
    print("routes checked:", len(checks) + 1)
    print("faults:", faults or "none")
    return 1 if faults else 0


if __name__ == "__main__":
    sys.exit(main())
