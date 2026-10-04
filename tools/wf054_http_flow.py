"""Drive the WF-054 flow over real HTTP against a running server.

    python -m uvicorn dsr.api:app --app-dir backend --host 127.0.0.1 --port 8000
    C:\\Users\\Dilip\\dsrvenv\\Scripts\\python.exe tools\\wf054_http_flow.py

Every call goes over a socket rather than through TestClient, because the point
is to prove the routes the host actually serves answer, not that the handlers do.
The script prints one line per step and exits non-zero on the first failure, so
it is usable as a check in a script as well as readable by a person.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any

BASE = "http://127.0.0.1:8000"
PREFIX = f"{BASE}/api/wf054"


def call(path: str, method: str = "GET", body: dict[str, Any] | None = None) -> Any:
    """One HTTP call, raising when the status is not what the step expects."""
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        f"{BASE}{path}",
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request) as response:
            payload = response.read().decode()
            return json.loads(payload) if payload else {}
    except urllib.error.HTTPError as error:
        payload = error.read().decode()
        raise AssertionError(f"{method} {path} -> {error.code}: {payload}") from error


def expect_status(path: str, wanted: int, method: str = "GET", body=None) -> dict[str, Any]:
    """Call a route that is expected to be refused, and return its error body."""
    request = urllib.request.Request(
        f"{BASE}{path}",
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request) as response:
            raise AssertionError(f"{method} {path} answered {response.status}, expected {wanted}")
    except urllib.error.HTTPError as error:
        if error.status != wanted:
            raise AssertionError(
                f"{method} {path} answered {error.status}, expected {wanted}"
            ) from error
        return json.loads(error.read().decode())


def report(label: str, detail: str) -> None:
    print(f"  {label:<34} {detail}")


def main() -> int:
    interval_start = (datetime.now(timezone.utc) + timedelta(days=20)).replace(
        hour=9, minute=0, second=0, microsecond=0
    )
    interval = {
        "start": interval_start.isoformat(),
        "end": (interval_start + timedelta(days=5)).isoformat(),
        "duration_minutes": 30,
        "min_notice_minutes": 0,
        "max_days": 14,
    }

    features = call("/api/features")
    mine = [entry for entry in features["features"] if entry["id"] == "wf-054-round-robin-booking"]
    if len(mine) != 1:
        print(f"the feature is not mounted exactly once: {mine}", file=sys.stderr)
        return 1
    report("feature mounted by discovery", f"{mine[0]['prefix']} ({len(mine[0]['routes'])} routes)")
    if features.get("failed"):
        print(f"the host reports failed features: {features['failed']}", file=sys.stderr)
        return 1

    vocabulary = call("/api/wf054/vocabulary")
    report("vocabulary", f"modes={vocabulary['mode_names']}")

    inferences = call("/api/wf054/inferences")
    derivation = next(
        entry
        for entry in inferences["inferences"]
        if entry["id"] == "inference_calendar_combination"
    )
    report("calendar derivation", f"{derivation['decision'][:28]}... {derivation['jev_audit_id']}")

    # The booking paths are room-scoped, so this script needs a live room. It reuses
    # one the seeder created rather than minting a second: creating a room needs a
    # live account, and WF-001 owns that route. This workflow is not about rooms.
    rooms = call("/api/wf-005/rooms", "GET")["rooms"]
    if not rooms:
        print("the database has no rooms; run backend/seed.py first", file=sys.stderr)
        return 1
    room = rooms[0]["id"]
    report("room reused from the seed", room)

    team = call(
        "/api/wf054/teams",
        "POST",
        {
            "name": "Enterprise HTTP team",
            "members": [
                {"member_id": "http-a", "name": "Ada"},
                {"member_id": "http-b", "name": "Ben"},
                {"member_id": "http-ghost", "name": "Gil", "licensed": False},
            ],
        },
    )
    report("team declared", team["id"])
    excluded = [
        row for row in call(f"/api/wf054/teams/{team['id']}")["members"] if not row["eligible"]
    ]
    report(
        "unlicensed member excluded",
        f"{excluded[0]['member_id']}: {excluded[0]['excluded_reason']}",
    )

    distribution = call(
        "/api/wf054/distributions",
        "POST",
        {
            "name": "Strict HTTP rotation",
            "mode": "strict",
            "team_ref": team["id"],
            "interval": interval,
            "credit_back_on_no_show": True,
        },
    )
    report("distribution declared", distribution["id"])

    preview = call(
        f"/api/wf054/rooms/{room}/check", "POST", {"distribution_id": distribution["id"]}
    )
    # Scoped to this distribution, not to the whole product: the seeder already
    # opened routing sessions of its own, so a global count would prove nothing
    # about whether *this* preview wrote.
    before = call(f"/api/wf054/routes?distribution_id={distribution['id']}")["count"]
    assert before == 0, "this distribution has no routes before the preview"
    report(
        "preview writes nothing",
        f"reaches {preview['chosen']['member_id']}, {preview['window']['slot_count']} slots",
    )
    after = call(f"/api/wf054/routes?distribution_id={distribution['id']}")["count"]
    assert after == 0, f"the preview opened {after - before} routing session(s)"

    opened = call(
        f"/api/wf054/rooms/{room}/init-simple",
        "POST",
        {
            "distribution_id": distribution["id"],
            "guestEmail": "prospect@example.test",
            "interval": interval,
        },
    )
    report("init-simple", f"outcome={opened['outcome']}, routing={opened['routing_id']}")

    booking = call(
        f"/api/wf054/rooms/{room}/schedule-simple",
        "POST",
        {
            "routing_id": opened["routing_id"],
            "startTime": opened["start_times"][0],
            "guestEmail": "prospect@example.test",
        },
    )
    report(
        "booked",
        f"member={booking['member_id']}, cursor={booking['cursor']}, recheck={booking['rechecked_at_booking']}",
    )

    conflict = expect_status(
        f"/api/wf054/rooms/{room}/schedule-simple",
        409,
        "POST",
        {
            "routing_id": opened["routing_id"],
            "startTime": opened["start_times"][0],
            "guestEmail": "prospect@example.test",
        },
    )
    report("booking twice refused", conflict["error"])

    no_show = call(
        f"/api/wf054/bookings/{booking['booking_id']}/no-show", "POST", {"note": "did not join"}
    )
    report("no-show credits back", f"{no_show['credited_back']} credit to {no_show['member_id']}")

    movements = call(f"/api/wf054/credits?distribution_id={distribution['id']}")
    directions = sorted(entry["data"]["direction"] for entry in movements["movements"])
    report("credit movements", f"{movements['count']} rows, directions={directions}")

    ledger = call(f"/api/wf054/distributions/{distribution['id']}")["ledger"]
    returned = [row for row in ledger if row["credits_returned"]]
    report(
        "ledger after the return",
        f"{returned[0]['member_id']}: consumed={returned[0]['credits_consumed']}",
    )

    blocked_team = call(
        "/api/wf054/teams",
        "POST",
        {"name": "Ghosts only", "members": [{"member_id": "g", "licensed": False}]},
    )
    blocked = call(
        "/api/wf054/distributions",
        "POST",
        {
            "name": "Nobody assignable",
            "mode": "strict",
            "team_ref": blocked_team["id"],
            "interval": interval,
        },
    )
    refused = expect_status(
        f"/api/wf054/rooms/{room}/init-simple",
        409,
        "POST",
        {"distribution_id": blocked["id"], "guestEmail": "nobody@example.test"},
    )
    report("licence gate refuses", f"{refused['error']}")

    bad_mode = expect_status(
        "/api/wf054/distributions", 400, "POST", {"mode": "sideways", "team_ref": team["id"]}
    )
    report("bad mode refused", bad_mode["error"])

    missing = expect_status("/api/wf054/routes/no-such-route", 404)
    report("missing route 404", missing["error"])

    summary = call("/api/wf054/summary")
    report(
        "summary",
        f"teams={summary['teams']}, distributions={summary['distributions']}, "
        f"bookings={summary['bookings']}, no-shows={summary['no_shows']}",
    )

    print("\nWF054 HTTP FLOW: every step answered as expected.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
