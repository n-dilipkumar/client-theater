"""WF-054: the HTTP surface, driven through the mounted routes.

These call the URLs the host actually serves rather than importing handlers and
calling them directly. The difference is load-bearing three times over:

* ``backend/dsr/api.py`` is not edited by this feature, so the router is mounted
  by discovery alone. If discovery failed, every test here would 404 and the
  domain tests would still pass, which is exactly the failure a feature-only
  test suite cannot see.
* The audit-source rule is checked against the **live route registry** the host
  reports, not against a list written by hand. A written list goes stale the
  moment a route is renamed, and then the check keeps passing while the audit log
  names a path the app stopped serving.
* The error statuses are asserted as HTTP statuses, through the one handler the
  feature registers.

``client`` comes from ``conftest.py``: one ``TestClient`` per module with the
store swapped per test, so these tests are isolated without paying for a lifespan
each. Every test builds its own rows, so none depends on another's.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient

PREFIX = "/api/wf054"
ROOM = "room-wf054"
OTHER_ROOM = "room-other"


#: A window five working days out, in the future relative to any test clock, so
#: the seeded rows below stay bookable however long this file lives.
def _interval(days_out: int = 30) -> dict[str, Any]:
    from datetime import timedelta as td

    start = datetime.now(timezone.utc) + td(days=days_out)
    start = start.replace(hour=9, minute=0, second=0, microsecond=0)
    return {
        "start": start.isoformat(),
        "end": (start + td(days=5)).isoformat(),
        "duration_minutes": 30,
        "min_notice_minutes": 0,
        "max_days": 14,
    }


INTERVAL = _interval()


def served_routes(client: TestClient) -> list[dict[str, Any]]:
    """Every route the host reports, from the live registry."""
    return [
        route
        for entry in client.get("/api/features").json()["features"]
        for route in entry["routes"]
    ]


def names_a_served_route(source: str, routes: list[dict[str, Any]]) -> bool:
    """True when an audit ``source`` names a mounted route, segment by segment."""
    method, _, path = source.partition(" ")
    actual = [segment for segment in path.split("/") if segment]
    for route in routes:
        if method not in route["methods"]:
            continue
        template = [segment for segment in route["path"].split("/") if segment]
        if len(template) != len(actual):
            continue
        if all(
            expected.startswith("{") or expected == found
            for expected, found in zip(template, actual, strict=False)
        ):
            return True
    return False


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture(autouse=True)
def room(client: TestClient) -> TestClient:
    """A room under the fixed id the booking paths are scoped to.

    Autouse, so every test in this module has the room its paths name and no
    test has to remember to ask for it. Created through the store rather than
    ``POST /api/rooms`` because the core route mints its own id, and these tests
    need the id to be a known constant in order to assert that a booking is
    refused against a room that does not exist.
    """
    client.app.state.store.create(
        "room", {"name": "Round robin room"}, record_id=ROOM, source="test"
    )
    return client


@pytest.fixture
def team(client: TestClient) -> str:
    """A team of two licensed reps, both free across the interval."""
    response = client.post(
        f"{PREFIX}/teams",
        json={
            "name": "Enterprise",
            "members": [
                {"member_id": "rr-a", "name": "Ada", "email": "ada@example.test"},
                {"member_id": "rr-b", "name": "Ben", "email": "ben@example.test"},
            ],
        },
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


@pytest.fixture
def distribution(client: TestClient, team: str) -> str:
    """A Strict distribution over the two-rep team."""
    response = client.post(
        f"{PREFIX}/distributions",
        json={
            "name": "Strict rotation",
            "mode": "strict",
            "team_ref": team,
            "interval": INTERVAL,
            "credit_back_on_no_show": True,
        },
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def open_route(client: TestClient, distribution_id: str, guest: str = "p@example.test") -> dict:
    """Open a routing session and return the response body."""
    response = client.post(
        f"{PREFIX}/rooms/{ROOM}/init-simple",
        json={"distribution_id": distribution_id, "guestEmail": guest, "interval": INTERVAL},
    )
    assert response.status_code == 201, response.text
    return response.json()


# --------------------------------------------------------------------------- #
# Discovery: the router is mounted without editing api.py
# --------------------------------------------------------------------------- #


def test_the_router_is_mounted_by_discovery_alone(client: TestClient):
    """ "The router is mounted by discovery alone. api.py is not edited"."""
    entries = [
        entry
        for entry in client.get("/api/features").json()["features"]
        if entry["id"] == "wf-054-round-robin-booking"
    ]
    assert len(entries) == 1
    entry = entries[0]
    assert entry["ticket"] == "WF-054"
    assert entry["prefix"] == PREFIX
    assert entry["routes"], "a mounted feature must report its routes"


def test_the_feature_reports_no_load_failure(client: TestClient):
    body = client.get("/api/features").json()
    assert not body.get("failed"), body.get("failed")


def test_no_two_features_claim_the_same_route(client: TestClient):
    seen: set[tuple[str, str]] = set()
    for entry in client.get("/api/features").json()["features"]:
        for route in entry["routes"]:
            for method in route["methods"]:
                key = (method, route["path"])
                assert key not in seen, f"{entry['id']} duplicates {key}"
                seen.add(key)


# --------------------------------------------------------------------------- #
# Reference data
# --------------------------------------------------------------------------- #


def test_the_vocabulary_is_served_and_names_the_two_modes(client: TestClient):
    body = client.get(f"{PREFIX}/vocabulary").json()
    assert body["mode_names"] == ["strict", "flexible"]
    assert body["calendar_combination"]["operation"] == "union"
    assert body["license_gate"]["is_a_warning"] is False
    assert body["credit_back_flag"]["standing_rule"] is True


def test_the_inferences_are_served_and_name_the_derivation(client: TestClient):
    body = client.get(f"{PREFIX}/inferences").json()
    assert body["count"] == len(body["inferences"])
    ids = {entry["id"] for entry in body["inferences"]}
    assert "inference_calendar_combination" in ids
    derivation = next(e for e in body["inferences"] if e["id"] == "inference_calendar_combination")
    assert derivation["jev_audit_id"] == "jev-20261004T045227-22564-47815"


def test_the_reference_reads_need_no_room(client: TestClient):
    for path in ("/vocabulary", "/inferences", "/summary", "/catalog", "/credits"):
        assert client.get(f"{PREFIX}{path}").status_code == 200, path


# --------------------------------------------------------------------------- #
# Teams
# --------------------------------------------------------------------------- #


def test_declaring_a_team_returns_the_normalised_members(client: TestClient):
    response = client.post(
        f"{PREFIX}/teams",
        json={"name": "Commercial", "members": [{"member_id": "rr-c", "name": "Cy"}]},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["data"]["members"][0]["member_id"] == "rr-c"
    assert body["data"]["members"][0]["licensed"] is True


def test_listing_teams_annotates_an_unlicensed_member_with_its_reason(client: TestClient):
    client.post(
        f"{PREFIX}/teams",
        json={
            "name": "Mixed",
            "members": [
                {"member_id": "ok"},
                {"member_id": "ghost", "licensed": False},
            ],
        },
    )
    body = client.get(f"{PREFIX}/teams").json()
    assert body["count"] == 1
    members = body["teams"][0]["members"]
    assert [m["member_id"] for m in members] == ["ok", "ghost"]
    assert members[1]["eligible"] is False
    assert members[1]["excluded_reason"] == "no Concierge license"


def test_reading_one_team(client: TestClient, team: str):
    body = client.get(f"{PREFIX}/teams/{team}").json()
    assert body["eligibility"]["eligible"] == 2


def test_reading_a_team_that_does_not_exist_is_404(client: TestClient):
    assert client.get(f"{PREFIX}/teams/no-such-team").status_code == 404


def test_a_team_with_no_name_is_refused_with_400(client: TestClient):
    response = client.post(f"{PREFIX}/teams", json={"members": [{"member_id": "a"}]})
    assert response.status_code == 400
    assert response.json()["error"] == "round_robin_error"


def test_a_team_with_no_members_is_refused_with_400(client: TestClient):
    response = client.post(f"{PREFIX}/teams", json={"name": "T", "members": []})
    assert response.status_code == 400


def test_a_duplicate_member_is_refused_with_400(client: TestClient):
    response = client.post(
        f"{PREFIX}/teams", json={"name": "T", "members": [{"member_id": "a"}, {"member_id": "a"}]}
    )
    assert response.status_code == 400
    assert "twice" in response.json()["detail"]


def test_an_unreadable_busy_block_is_refused_with_400(client: TestClient):
    response = client.post(
        f"{PREFIX}/teams",
        json={"name": "T", "members": [{"member_id": "a", "busy": [{"start": "nope"}]}]},
    )
    assert response.status_code == 400


# --------------------------------------------------------------------------- #
# Distributions
# --------------------------------------------------------------------------- #


def test_declaring_a_distribution_returns_the_mode_and_an_empty_ledger(
    client: TestClient, team: str
):
    response = client.post(
        f"{PREFIX}/distributions",
        json={"name": "D", "mode": "strict", "team_ref": team, "interval": INTERVAL},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["data"]["mode"] == "strict"
    assert body["data"]["credits"] == {}
    assert body["data"]["cursor"] == 0
    assert body["data"]["link_type"] == "RoundRobin"


def test_declaring_a_distribution_over_a_missing_team_is_404(client: TestClient):
    response = client.post(
        f"{PREFIX}/distributions",
        json={"name": "D", "mode": "strict", "team_ref": "no-such-team", "interval": INTERVAL},
    )
    assert response.status_code == 404
    assert response.json()["error"] == "round_robin_not_found"


def test_a_distribution_with_a_bad_mode_is_refused_with_400(client: TestClient, team: str):
    response = client.post(
        f"{PREFIX}/distributions",
        json={"name": "D", "mode": "weighted", "team_ref": team, "interval": INTERVAL},
    )
    assert response.status_code == 400
    assert "mode must be one of" in response.json()["detail"]


def test_a_distribution_for_another_link_type_is_refused_with_400(client: TestClient, team: str):
    response = client.post(
        f"{PREFIX}/distributions",
        json={
            "name": "D",
            "mode": "strict",
            "team_ref": team,
            "link_type": "Ownership",
            "interval": INTERVAL,
        },
    )
    assert response.status_code == 400
    assert "RoundRobin" in response.json()["detail"]


def test_listing_distributions_can_filter_by_mode(client: TestClient, team: str):
    for mode in ("strict", "flexible"):
        client.post(
            f"{PREFIX}/distributions",
            json={"name": mode, "mode": mode, "team_ref": team, "interval": INTERVAL},
        )
    assert client.get(f"{PREFIX}/distributions", params={"mode": "flexible"}).json()["count"] == 1
    assert client.get(f"{PREFIX}/distributions", params={"mode": "strict"}).json()["count"] == 1
    assert client.get(f"{PREFIX}/distributions").json()["count"] == 2
    assert client.get(f"{PREFIX}/distributions", params={"team_ref": team}).json()["count"] == 2
    assert client.get(f"{PREFIX}/distributions", params={"team_ref": "other"}).json()["count"] == 0


def test_reading_one_distribution_renders_the_ledger_against_the_team(
    client: TestClient, distribution: str, team: str
):
    body = client.get(f"{PREFIX}/distributions/{distribution}").json()
    assert [row["member_id"] for row in body["ledger"]] == ["rr-a", "rr-b"]
    assert body["eligibility"]["eligible"] == 2


def test_reading_a_distribution_that_does_not_exist_is_404(client: TestClient):
    assert client.get(f"{PREFIX}/distributions/no-such").status_code == 404


def test_the_catalog_lists_the_supported_link_type(client: TestClient):
    body = client.get(f"{PREFIX}/catalog").json()
    assert body["supported_link_types"] == ["RoundRobin"]


# --------------------------------------------------------------------------- #
# The researched calls
# --------------------------------------------------------------------------- #


def test_the_preview_returns_the_combined_window_and_writes_nothing(
    client: TestClient, distribution: str
):
    response = client.post(f"{PREFIX}/rooms/{ROOM}/check", json={"distribution_id": distribution})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["outcome"] == "allocation"
    assert body["window"]["operation"] == "union"
    assert body["chosen"]["member_id"] in {"rr-a", "rr-b"}
    assert client.get(f"{PREFIX}/routes").json()["count"] == 0
    assert client.get(f"{PREFIX}/bookings").json()["count"] == 0


def test_the_preview_against_a_room_that_does_not_exist_is_404(
    client: TestClient, distribution: str
):
    response = client.post(
        f"{PREFIX}/rooms/{OTHER_ROOM}/check", json={"distribution_id": distribution}
    )
    assert response.status_code == 404
    assert "room" in response.json()["detail"]


def test_the_preview_needs_a_distribution_id(client: TestClient):
    response = client.post(f"{PREFIX}/rooms/{ROOM}/check", json={})
    assert response.status_code == 400
    assert "distribution_id is required" in response.json()["detail"]


def test_the_preview_needs_a_distribution_that_exists(client: TestClient):
    response = client.post(f"{PREFIX}/rooms/{ROOM}/check", json={"distribution_id": "no-such"})
    assert response.status_code == 404


def test_init_simple_answers_with_a_routing_id_and_start_times(
    client: TestClient, distribution: str
):
    body = open_route(client, distribution)
    assert body["outcome"] == "allocation"
    assert body["routing_id"]
    assert body["start_times"], "a combined window with two free reps must offer slots"
    assert body["start_times"] == sorted(body["start_times"]), "slots are in order"
    assert all("T" in start for start in body["start_times"])


def test_init_simple_writes_one_route_holding_the_offered_slots(
    client: TestClient, distribution: str
):
    opened = open_route(client, distribution)
    routes = client.get(f"{PREFIX}/routes").json()
    assert routes["count"] == 1
    route = routes["routes"][0]
    assert route["id"] == opened["routing_id"]
    assert route["data"]["state"] == "open"
    assert route["data"]["guest_email"] == "p@example.test"
    assert route["data"]["offered_slots"][0]["free_member_ids"]


def test_init_simple_needs_a_guest_email(client: TestClient, distribution: str):
    response = client.post(
        f"{PREFIX}/rooms/{ROOM}/init-simple", json={"distribution_id": distribution}
    )
    assert response.status_code == 400
    assert "guestEmail is required" in response.json()["detail"]


def test_init_simple_against_a_room_that_does_not_exist_is_404(
    client: TestClient, distribution: str
):
    response = client.post(
        f"{PREFIX}/rooms/{OTHER_ROOM}/init-simple",
        json={"distribution_id": distribution, "guestEmail": "p@example.test"},
    )
    assert response.status_code == 404


def test_init_simple_refuses_a_team_with_nobody_assignable(client: TestClient):
    """The researched Not Scheduled path with nothing left on it, answered 409."""
    blocked = client.post(
        f"{PREFIX}/teams",
        json={"name": "Ghosts", "members": [{"member_id": "g", "licensed": False}]},
    ).json()
    distribution = client.post(
        f"{PREFIX}/distributions",
        json={
            "name": "Nobody",
            "mode": "strict",
            "team_ref": blocked["id"],
            "interval": INTERVAL,
        },
    ).json()
    response = client.post(
        f"{PREFIX}/rooms/{ROOM}/init-simple",
        json={"distribution_id": distribution["id"], "guestEmail": "p@example.test"},
    )
    assert response.status_code == 409
    assert response.json()["error"] == "no_eligible_member"
    assert "no Concierge license" in response.json()["detail"]
    assert client.get(f"{PREFIX}/routes").json()["count"] == 0


def test_an_evaluation_of_a_fully_booked_team_answers_eligible_with_no_slots(client: TestClient):
    """A busy week is a legitimate answer, not an error."""
    start = INTERVAL["start"]
    end = INTERVAL["end"]
    team = client.post(
        f"{PREFIX}/teams",
        json={
            "name": "All busy",
            "members": [{"member_id": "busy1", "busy": [{"start": start, "end": end}]}],
        },
    ).json()
    distribution = client.post(
        f"{PREFIX}/distributions",
        json={"name": "D", "mode": "strict", "team_ref": team["id"], "interval": INTERVAL},
    ).json()
    body = open_route(client, distribution["id"])
    assert body["outcome"] == "eligible"
    assert body["start_times"] == []
    assert body["routing_id"] is None


# --------------------------------------------------------------------------- #
# Booking
# --------------------------------------------------------------------------- #


def test_booking_consumes_a_credit_and_closes_the_route(client: TestClient, distribution: str):
    opened = open_route(client, distribution)
    response = client.post(
        f"{PREFIX}/rooms/{ROOM}/schedule-simple",
        json={
            "routing_id": opened["routing_id"],
            "startTime": opened["start_times"][0],
            "guestEmail": "p@example.test",
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["booking"]["data"]["status"] == "confirmed"
    assert body["rechecked_at_booking"] is False
    assert client.get(f"{PREFIX}/routes").json()["routes"][0]["data"]["state"] == "booked"

    ledger_rows = client.get(f"{PREFIX}/distributions/{distribution}").json()["ledger"]
    consumed = [row for row in ledger_rows if row["credits_consumed"]]
    assert len(consumed) == 1
    assert consumed[0]["member_id"] == body["member_id"]


def test_booking_a_route_twice_is_409(client: TestClient, distribution: str):
    opened = open_route(client, distribution)
    payload = {
        "routing_id": opened["routing_id"],
        "startTime": opened["start_times"][0],
        "guestEmail": "p@example.test",
    }
    assert client.post(f"{PREFIX}/rooms/{ROOM}/schedule-simple", json=payload).status_code == 201
    second = client.post(f"{PREFIX}/rooms/{ROOM}/schedule-simple", json=payload)
    assert second.status_code == 409
    assert second.json()["error"] == "round_robin_conflict"
    assert "cannot be booked again" in second.json()["detail"]


def test_booking_a_slot_the_route_never_offered_is_400(client: TestClient, distribution: str):
    opened = open_route(client, distribution)
    response = client.post(
        f"{PREFIX}/rooms/{ROOM}/schedule-simple",
        json={
            "routing_id": opened["routing_id"],
            "startTime": opened["start_times"][0][:17] + "45:00+00:00",
        },
    )
    assert response.status_code == 400
    assert "not on offer" in response.json()["detail"]


def test_booking_a_route_for_another_guest_is_400(client: TestClient, distribution: str):
    opened = open_route(client, distribution)
    response = client.post(
        f"{PREFIX}/rooms/{ROOM}/schedule-simple",
        json={
            "routing_id": opened["routing_id"],
            "startTime": opened["start_times"][0],
            "guestEmail": "someone.else@example.test",
        },
    )
    assert response.status_code == 400
    assert "opened for" in response.json()["detail"]


def test_booking_needs_a_routing_id(client: TestClient):
    response = client.post(f"{PREFIX}/rooms/{ROOM}/schedule-simple", json={})
    assert response.status_code == 400
    assert "routing_id is required" in response.json()["detail"]


def test_booking_against_a_route_that_does_not_exist_is_404(client: TestClient):
    response = client.post(
        f"{PREFIX}/rooms/{ROOM}/schedule-simple",
        json={"routing_id": "no-such-route", "startTime": "2026-03-02T09:00:00+00:00"},
    )
    assert response.status_code == 404


def test_booking_against_a_room_that_does_not_exist_is_404(client: TestClient, distribution: str):
    opened = open_route(client, distribution)
    response = client.post(
        f"{PREFIX}/rooms/{OTHER_ROOM}/schedule-simple",
        json={"routing_id": opened["routing_id"], "startTime": opened["start_times"][0]},
    )
    assert response.status_code == 404


def test_the_strict_rotation_visits_each_rep_in_turn(client: TestClient, distribution: str):
    """ "strict (equal turns)" over two reps, four bookings."""
    taken = []
    for index in range(4):
        opened = open_route(client, distribution, guest=f"p{index}@example.test")
        body = client.post(
            f"{PREFIX}/rooms/{ROOM}/schedule-simple",
            json={"routing_id": opened["routing_id"], "startTime": opened["start_times"][0]},
        ).json()
        taken.append(body["member_id"])
    assert taken == ["rr-a", "rr-b", "rr-a", "rr-b"]


def test_a_flexible_distribution_weights_by_availability(client: TestClient):
    """A member with three times the free time is offered more of the window."""
    start = datetime.fromisoformat(INTERVAL["start"])
    end = datetime.fromisoformat(INTERVAL["end"])
    # rr-heavy is free throughout; rr-light is booked for the first half.
    team = client.post(
        f"{PREFIX}/teams",
        json={
            "name": "Weighted",
            "members": [
                {"member_id": "rr-heavy"},
                {
                    "member_id": "rr-light",
                    "busy": [
                        {"start": start.isoformat(), "end": (start + (end - start) / 2).isoformat()}
                    ],
                },
            ],
        },
    ).json()
    distribution = client.post(
        f"{PREFIX}/distributions",
        json={"name": "Flexible", "mode": "flexible", "team_ref": team["id"], "interval": INTERVAL},
    ).json()
    body = open_route(client, distribution["id"])
    assert body["mode"] == "flexible"
    assert body["chosen"]["member_id"] == "rr-heavy"
    assert body["chosen"]["rule"] == "largest weight times free share"


def test_a_weighted_distribution_honours_the_declared_weight(client: TestClient, team: str):
    """The admin's declared weight is what Flexible reads.

    Checked on the scores the selection reports rather than on who wins, because
    two reps with identical availability make the weight decisive and the ranking
    then depends on which of them the test happens to book against first.
    """
    distribution = client.post(
        f"{PREFIX}/distributions",
        json={
            "name": "Weighted",
            "mode": "flexible",
            "team_ref": team,
            "interval": INTERVAL,
            "members": [
                {"member_id": "rr-a", "weight": 1.0},
                {"member_id": "rr-b", "weight": 100.0},
            ],
        },
    ).json()
    body = open_route(client, distribution["id"])
    scores = {score["member_id"]: score for score in body["chosen"]["scores"]}
    assert scores["rr-b"]["weight"] == 100.0
    assert scores["rr-a"]["weight"] == 1.0
    assert scores["rr-b"]["score"] > scores["rr-a"]["score"]


# --------------------------------------------------------------------------- #
# Step 5: the no-show
# --------------------------------------------------------------------------- #


def _book_one(client: TestClient, distribution: str, guest: str = "p@example.test") -> dict:
    opened = open_route(client, distribution, guest=guest)
    return client.post(
        f"{PREFIX}/rooms/{ROOM}/schedule-simple",
        json={"routing_id": opened["routing_id"], "startTime": opened["start_times"][0]},
    ).json()


def test_marking_a_no_show_credits_the_member_back(client: TestClient, distribution: str):
    taken = _book_one(client, distribution)
    response = client.post(
        f"{PREFIX}/bookings/{taken['booking_id']}/no-show", json={"note": "did not join"}
    )
    assert response.status_code == 201, response.text
    assert response.json()["credited_back"] == 1

    rows = client.get(f"{PREFIX}/distributions/{distribution}").json()["ledger"]
    member = next(row for row in rows if row["member_id"] == taken["member_id"])
    assert member["credits_consumed"] == 0
    assert member["credits_returned"] == 1
    assert client.get(f"{PREFIX}/no-shows").json()["count"] == 1


def test_marking_a_no_show_twice_is_409(client: TestClient, distribution: str):
    taken = _book_one(client, distribution)
    client.post(f"{PREFIX}/bookings/{taken['booking_id']}/no-show", json={})
    second = client.post(f"{PREFIX}/bookings/{taken['booking_id']}/no-show", json={})
    assert second.status_code == 409
    assert "already been marked No-Show" in second.json()["detail"]


def test_a_no_show_against_a_booking_that_does_not_exist_is_404(client: TestClient):
    assert client.post(f"{PREFIX}/bookings/no-such/no-show", json={}).status_code == 404


def test_a_no_show_refuses_when_the_distribution_does_not_credit_back(
    client: TestClient, team: str
):
    """An admin who pressed the button expects a credit back or an explanation."""
    distribution = client.post(
        f"{PREFIX}/distributions",
        json={
            "name": "No credit back",
            "mode": "strict",
            "team_ref": team,
            "interval": INTERVAL,
            "credit_back_on_no_show": False,
        },
    ).json()
    taken = _book_one(client, distribution["id"])
    response = client.post(f"{PREFIX}/bookings/{taken['booking_id']}/no-show", json={})
    assert response.status_code == 400
    assert "credit_back_on_no_show" in response.json()["detail"]
    assert client.get(f"{PREFIX}/no-shows").json()["count"] == 0
    assert (
        client.get(f"{PREFIX}/bookings/{taken['booking_id']}").json()["data"]["status"]
        == "confirmed"
    )


# --------------------------------------------------------------------------- #
# Bookings
# --------------------------------------------------------------------------- #


def test_cancelling_a_booking_marks_it_cancelled_and_returns_no_credit(
    client: TestClient, distribution: str
):
    taken = _book_one(client, distribution)
    assert client.post(f"{PREFIX}/bookings/{taken['booking_id']}/cancel").status_code == 200
    assert (
        client.get(f"{PREFIX}/bookings/{taken['booking_id']}").json()["data"]["status"]
        == "cancelled"
    )
    rows = client.get(f"{PREFIX}/distributions/{distribution}").json()["ledger"]
    member = next(row for row in rows if row["member_id"] == taken["member_id"])
    assert member["credits_consumed"] == 1
    assert member["credits_returned"] == 0


def test_cancelling_twice_is_409(client: TestClient, distribution: str):
    taken = _book_one(client, distribution)
    client.post(f"{PREFIX}/bookings/{taken['booking_id']}/cancel")
    assert client.post(f"{PREFIX}/bookings/{taken['booking_id']}/cancel").status_code == 409


def test_cancelling_a_booking_that_does_not_exist_is_404(client: TestClient):
    assert client.post(f"{PREFIX}/bookings/no-such/cancel").status_code == 404


def test_reading_a_booking_that_does_not_exist_is_404(client: TestClient):
    assert client.get(f"{PREFIX}/bookings/no-such").status_code == 404


def test_the_bookings_and_routes_lists_filter(client: TestClient, distribution: str):
    first = _book_one(client, distribution)
    open_route(client, distribution, guest="q@example.test")

    assert client.get(f"{PREFIX}/routes", params={"state": "booked"}).json()["count"] == 1
    assert client.get(f"{PREFIX}/routes", params={"state": "open"}).json()["count"] == 1
    assert client.get(f"{PREFIX}/bookings", params={"status": "confirmed"}).json()["count"] == 1
    assert client.get(f"{PREFIX}/bookings", params={"status": "cancelled"}).json()["count"] == 0
    assert (
        client.get(f"{PREFIX}/bookings", params={"member_id": first["member_id"]}).json()["count"]
        == 1
    )
    assert client.get(f"{PREFIX}/bookings", params={"member_id": "nobody"}).json()["count"] == 0


def test_the_credit_movements_are_served_in_both_directions(client: TestClient, distribution: str):
    taken = _book_one(client, distribution)
    client.post(f"{PREFIX}/bookings/{taken['booking_id']}/no-show", json={})

    body = client.get(f"{PREFIX}/credits", params={"distribution_id": distribution}).json()
    assert body["count"] == 2
    assert {entry["data"]["direction"] for entry in body["movements"]} == {"consumed", "returned"}


def test_reading_a_route_that_does_not_exist_is_404(client: TestClient):
    assert client.get(f"{PREFIX}/routes/no-such").status_code == 404


def test_the_summary_counts_the_states_the_page_shows(client: TestClient, distribution: str):
    taken = _book_one(client, distribution)
    client.post(f"{PREFIX}/bookings/{taken['booking_id']}/no-show", json={})
    body = client.get(f"{PREFIX}/summary").json()
    assert body["distributions_by_mode"] == {"strict": 1}
    assert body["routes"] == 1
    assert body["routes_open"] == 0
    assert body["bookings"] == 1
    assert body["no_shows"] == 1


# --------------------------------------------------------------------------- #
# The audit rules
# --------------------------------------------------------------------------- #


def test_the_audit_source_names_the_route_that_served_the_write(
    client: TestClient, distribution: str
):
    """Every audit row this feature writes names a route the host mounted.

    Checked against the live registry rather than a written list: a list written
    by hand goes stale the moment a route is renamed, and then this check keeps
    passing while the audit log names a path the app stopped serving. That has
    shipped in this codebase before.
    """
    taken = _book_one(client, distribution)
    client.post(f"{PREFIX}/bookings/{taken['booking_id']}/no-show", json={})
    client.post(f"{PREFIX}/bookings/{taken['booking_id']}/cancel")

    routes = served_routes(client)
    assert routes, "the host reported no routes, so this check would pass vacuously"

    # The audit rows for this feature's own records, by collection.
    from dsr.api import app

    store = app.state.store
    for collection in (
        "round_robin_team",
        "round_robin_distribution",
        "round_robin_route",
        "round_robin_booking",
        "round_robin_no_show",
        "round_robin_credit_movement",
    ):
        rows = store.audit(collection=collection, limit=100)
        for row in rows:
            source = row.get("source")
            if not source:
                continue
            assert names_a_served_route(source, routes), (
                f"audit row in {collection} names {source!r}, which is not a route this app serves"
            )


def test_every_write_is_audited(client: TestClient, distribution: str):
    """A feature without its audit rows is a feature that bypassed the guarantee."""
    from dsr.api import app

    store = app.state.store
    taken = _book_one(client, distribution)
    client.post(f"{PREFIX}/bookings/{taken['booking_id']}/no-show", json={})

    for collection, minimum in (
        ("round_robin_route", 1),
        ("round_robin_booking", 1),
        ("round_robin_no_show", 1),
        ("round_robin_credit_movement", 2),
    ):
        assert store.db.audit_count(collection=collection) >= minimum, collection


def test_a_write_names_the_route_that_served_it(client: TestClient, distribution: str):
    """The source is the route, not a hand-written string that can drift."""
    from dsr.api import app

    taken = _book_one(client, distribution)
    assert taken["booking_id"]

    rows = app.state.store.audit(collection="round_robin_booking", limit=10)
    assert rows, "booking writes nothing audited"
    sources = {row["source"] for row in rows}
    assert sources == {f"POST {PREFIX}/rooms/{{room_id}}/schedule-simple"}


# --------------------------------------------------------------------------- #
# The seed, through the mounted routes
# --------------------------------------------------------------------------- #


def test_the_seeded_feature_answers_its_own_routes(client: TestClient, tmp_path):
    """The seeder's rows must be readable over HTTP, not only in the database."""
    from dsr.api import app

    store = app.state.store
    feature = __import__("dsr.features.wf054_round_robin_booking", fromlist=["seed"])
    feature.seed(
        app.state.db,
        {"room_ids": [], "now": datetime.now(timezone.utc) + timedelta(days=1)},
    )
    assert store.list("round_robin_team")
    assert client.get(f"{PREFIX}/teams").json()["count"] >= 3
    assert client.get(f"{PREFIX}/distributions").json()["count"] >= 2
    assert client.get(f"{PREFIX}/bookings").json()["count"] >= 1
    assert client.get(f"{PREFIX}/no-shows").json()["count"] >= 1
