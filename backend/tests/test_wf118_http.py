"""WF-118 through the HTTP surface.

The domain rules live in ``tests/test_wf118.py``. What is here is the half the
domain cannot see:

* the router is mounted by **discovery alone**, with no edit to ``dsr/api.py``
* every refusal reaches the client with the status and code it carries, through
  **one** registered handler
* every write's audit row **names a route this router actually serves**, checked
  against the live route table rather than against a list in this file
* the routes answer **no 5xx when called the way ``tools/verify_all_routes.py``
  calls them** (a substituted id and an empty body)

The ``client`` fixture from ``conftest.py`` enters one ``TestClient`` per module
and points ``app.state`` at a fresh, empty database per test, so this file passes
on its own and under ``pytest-xdist``.
"""

from __future__ import annotations

from typing import Any

from dsr.features import wf118_order_throttle_and_repeat_in_app_guide_prompts as feature
from fastapi.testclient import TestClient

PREFIX = "/api/wf-118"
FEATURE_ID = "wf-118-guide-prompts"

WELCOME = {
    "key": "welcome_tour",
    "name": "Welcome tour",
    "status": "public",
    "activation": {
        "repeat": {"enabled": True, "every": 1, "unit": "day"},
        "stop_after": {"mode": "count", "count": 3},
        "ignore_throttling": False,
        "delay_seconds": 0,
    },
}

CRITICAL = {
    "key": "critical_notice",
    "name": "Critical notice",
    "status": "public",
    "activation": {"ignore_throttling": True},
}

AT = "2026-03-01T09:00:00+00:00"


def served_routes(client: TestClient) -> list[dict[str, Any]]:
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


def make_guide(client: TestClient, body: dict[str, Any] | None = None) -> dict[str, Any]:
    response = client.post(f"{PREFIX}/guides", json=body or WELCOME)
    assert response.status_code == 201, response.text
    return response.json()


def configure(client: TestClient, app_id: str = "app", **overrides) -> dict[str, Any]:
    body = {
        "guide_keys": ["welcome_tour", "critical_notice"],
        "throttling": {"enabled": True, "count": 1, "unit": "day"},
    }
    body.update(overrides)
    response = client.put(f"{PREFIX}/applications/{app_id}/order", json=body)
    assert response.status_code == 200, response.text
    return response.json()


# --------------------------------------------------------------------------- #
# Registration
# --------------------------------------------------------------------------- #


def test_the_feature_loads_with_no_failure(client: TestClient):
    body = client.get("/api/features").json()
    assert body["failed_count"] == 0
    mine = [entry for entry in body["features"] if entry["id"] == FEATURE_ID]
    assert len(mine) == 1
    assert mine[0]["prefix"] == PREFIX
    assert mine[0]["loaded"] is True
    assert "GuidePromptsRefusal" in mine[0]["exception_handlers"]


def test_every_route_this_feature_serves_is_under_its_own_prefix(client: TestClient):
    body = client.get("/api/features").json()
    mine = next(entry for entry in body["features"] if entry["id"] == FEATURE_ID)
    paths = {route["path"] for route in mine["routes"]}
    assert paths, "the feature mounts no routes"
    assert all(path.startswith(PREFIX) for path in paths), paths


def test_the_feature_declares_the_prefix_the_brief_names():
    assert feature.router.prefix == PREFIX
    assert feature.FEATURE["ticket"] == "WF-118"
    assert feature.FEATURE["id"] == FEATURE_ID
    assert feature.FEATURE["prefix"] == PREFIX
    assert feature.FEATURE["name"]


def test_one_handler_covers_the_whole_refusal_hierarchy():
    from dsr.guide_prompts.errors import GuidePromptsRefusal

    assert list(feature.EXCEPTION_HANDLERS) == [GuidePromptsRefusal]


def test_the_feature_module_never_imports_the_app_or_opens_sqlite():
    from pathlib import Path

    text = Path(feature.__file__).read_text(encoding="utf-8")
    assert "from dsr.api" not in text and "import dsr.api" not in text
    assert "import sqlite3" not in text
    assert "sqlite3.connect" not in text


def test_the_feature_exports_seed_for_the_seeder():
    assert callable(feature.seed)


# --------------------------------------------------------------------------- #
# Vocabulary and reads
# --------------------------------------------------------------------------- #


def test_the_vocabulary_route_serves_the_rules_without_a_store(client: TestClient):
    body = client.get(f"{PREFIX}/vocabulary").json()
    assert body["ticket"] == "WF-118"
    assert body["repeat_units"] == ["day", "hour"]
    assert body["throttle_units"] == ["minute", "hour", "day"]
    assert body["min_stop_after"] == 2
    assert body["delay_max_seconds"] == 60
    assert body["order_tiers"] == ["ignore_throttling", "throttled"]
    assert body["decisions"]


def test_the_decisions_route_records_the_gates(client: TestClient):
    body = client.get(f"{PREFIX}/decisions").json()
    assert body["count"] == len(body["decisions"])
    ids = {item["id"] for item in body["decisions"]}
    assert {"programmatic_launch", "order_list_platform_scope"} <= ids
    audit_ids = [item["audit_id"] for item in body["decisions"] if item["audit_id"]]
    assert audit_ids and all(a.startswith("jev-") for a in audit_ids)


def test_summary_starts_empty(client: TestClient):
    body = client.get(f"{PREFIX}/summary").json()
    assert body["total"] == 0
    assert body["applications"] == 0


def test_guides_list_create_and_filter(client: TestClient):
    make_guide(client)
    client.post(f"{PREFIX}/guides", json=CRITICAL)
    listed = client.get(f"{PREFIX}/guides").json()
    assert listed["count"] == 2
    assert client.get(f"{PREFIX}/guides", params={"q": "critical"}).json()["count"] == 1
    assert client.get(f"{PREFIX}/guides", params={"status": "public"}).json()["count"] == 2
    empty = client.get(f"{PREFIX}/guides", params={"category": "nope"}).json()
    assert empty["count"] == 0


def test_get_guide_returns_its_activation_and_stop_label(client: TestClient):
    make_guide(client)
    body = client.get(f"{PREFIX}/guides/welcome_tour").json()
    assert body["activation"]["repeat"]["enabled"] is True
    assert body["stop_after_label"] == "After 3 dismissals"


def test_a_missing_guide_is_a_clean_404(client: TestClient):
    assert client.get(f"{PREFIX}/guides/absent").status_code == 404
    response = client.get(f"{PREFIX}/guides/absent")
    assert response.json()["error"] == "guide_not_found"


# --------------------------------------------------------------------------- #
# Authoring and refusals
# --------------------------------------------------------------------------- #


def test_a_duplicate_key_is_a_409(client: TestClient):
    make_guide(client)
    response = client.post(f"{PREFIX}/guides", json=WELCOME)
    assert response.status_code == 409
    assert response.json()["error"] == "key_taken"


def test_a_malformed_key_is_a_422(client: TestClient):
    response = client.post(f"{PREFIX}/guides", json={**WELCOME, "key": "Bad Key"})
    assert response.status_code == 422
    assert response.json()["error"] == "key_invalid"


def test_an_invalid_activation_is_a_422_with_every_problem(client: TestClient):
    response = client.post(
        f"{PREFIX}/guides",
        json={
            "key": "okay_key",
            "name": "Okay",
            "activation": {
                "repeat": {"enabled": True, "every": 0, "unit": "minute"},
                "stop_after": {"mode": "count", "count": 1},
                "delay_seconds": 90,
            },
        },
    )
    assert response.status_code == 422
    body = response.json()
    assert body["error"] == "guide_invalid"
    assert len(body["problems"]) == 3


def test_patch_edits_the_activation_and_refuses_a_key_change(client: TestClient):
    make_guide(client)
    edited = client.patch(
        f"{PREFIX}/guides/welcome_tour",
        json={"activation": {"stop_after": {"mode": "count", "count": 4}}},
    )
    assert edited.status_code == 200
    assert edited.json()["stop_after_label"] == "After 4 dismissals"
    refused = client.patch(f"{PREFIX}/guides/welcome_tour", json={"key": "renamed"})
    assert refused.status_code == 409
    assert refused.json()["error"] == "key_immutable"


def test_delete_is_a_soft_delete_and_restore_undoes_it(client: TestClient):
    make_guide(client)
    deleted = client.delete(f"{PREFIX}/guides/welcome_tour")
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True
    assert client.get(f"{PREFIX}/guides/welcome_tour").status_code == 404
    assert (
        client.get(f"{PREFIX}/guides/welcome_tour", params={"include_deleted": "true"}).status_code
        == 200
    )
    restored = client.post(f"{PREFIX}/guides/welcome_tour/restore")
    assert restored.status_code == 200
    assert restored.json()["deleted"] is False


# --------------------------------------------------------------------------- #
# Ordering and throttling
# --------------------------------------------------------------------------- #


def test_an_unconfigured_order_reads_clean(client: TestClient):
    body = client.get(f"{PREFIX}/applications/app/order").json()
    assert body["configured"] is False
    assert body["guide_keys"] == []


def test_set_order_rejects_an_unknown_guide(client: TestClient):
    response = client.put(
        f"{PREFIX}/applications/app/order",
        json={"guide_keys": ["ghost"], "throttling": None},
    )
    assert response.status_code == 422
    assert response.json()["error"] == "order_invalid"


def test_set_order_validates_the_throttle_interval(client: TestClient):
    make_guide(client)
    response = client.put(
        f"{PREFIX}/applications/app/order",
        json={
            "guide_keys": ["welcome_tour"],
            "throttling": {"enabled": True, "count": 0, "unit": "day"},
        },
    )
    assert response.status_code == 422
    assert response.json()["error"] == "order_invalid"


def test_order_round_trips_and_move_applies_a_row_arrow(client: TestClient):
    make_guide(client)
    client.post(f"{PREFIX}/guides", json=CRITICAL)
    client.post(f"{PREFIX}/guides", json={**CRITICAL, "key": "other_guide", "name": "Other"})
    configure(client, guide_keys=["welcome_tour", "critical_notice", "other_guide"])
    moved = client.post(
        f"{PREFIX}/applications/app/order/move",
        json={"key": "other_guide", "move": "top"},
    )
    assert moved.status_code == 200
    assert moved.json()["guide_keys"] == ["other_guide", "welcome_tour", "critical_notice"]


def test_move_refuses_an_unknown_arrow(client: TestClient):
    make_guide(client)
    configure(client, guide_keys=["welcome_tour"])
    response = client.post(
        f"{PREFIX}/applications/app/order/move",
        json={"key": "welcome_tour", "move": "sideways"},
    )
    assert response.status_code == 422
    assert response.json()["error"] == "order_invalid"


# --------------------------------------------------------------------------- #
# Eligibility and the session simulator
# --------------------------------------------------------------------------- #


def test_ranking_puts_the_unthrottled_guide_first(client: TestClient):
    make_guide(client)
    client.post(f"{PREFIX}/guides", json=CRITICAL)
    configure(client)
    body = client.get(
        f"{PREFIX}/applications/app/ranking", params={"user_id": "dana", "at": AT}
    ).json()
    assert [row["key"] for row in body["ranking"]] == ["critical_notice", "welcome_tour"]
    assert body["ranking"][0]["tier"] == 0


def test_next_returns_the_first_eligible_guide(client: TestClient):
    make_guide(client)
    client.post(f"{PREFIX}/guides", json=CRITICAL)
    configure(client)
    body = client.post(f"{PREFIX}/applications/app/next", json={"user_id": "dana", "at": AT}).json()
    assert body["guide"]["key"] == "critical_notice"


def test_a_dismissal_starts_the_throttle_and_releases_after_the_interval(client: TestClient):
    make_guide(client)
    client.post(f"{PREFIX}/guides", json=CRITICAL)
    configure(client)
    client.post(
        f"{PREFIX}/applications/app/events",
        json={"user_id": "dana", "event": "dismissed", "key": "critical_notice", "at": AT},
    )
    one_hour = client.post(
        f"{PREFIX}/applications/app/next",
        json={"user_id": "dana", "at": "2026-03-01T10:00:00+00:00"},
    ).json()
    assert one_hour["guide"] is None
    a_day = client.post(
        f"{PREFIX}/applications/app/next",
        json={"user_id": "dana", "at": "2026-03-02T09:00:00+00:00"},
    ).json()
    assert a_day["guide"]["key"] == "welcome_tour"

    session = client.get(f"{PREFIX}/applications/app/sessions/dana").json()
    assert session["last_dismissed_at"] is not None
    assert session["guides"]["critical_notice"]["dismissed_count"] == 1


def test_an_event_names_a_known_event_and_a_known_guide(client: TestClient):
    make_guide(client)
    bad_event = client.post(
        f"{PREFIX}/applications/app/events",
        json={"user_id": "dana", "event": "clicked", "key": "welcome_tour", "at": AT},
    )
    assert bad_event.status_code == 422
    assert bad_event.json()["error"] == "event_invalid"
    bad_guide = client.post(
        f"{PREFIX}/applications/app/events",
        json={"user_id": "dana", "event": "shown", "key": "ghost", "at": AT},
    )
    assert bad_guide.status_code == 404


def test_a_programmatic_launch_is_recorded_as_such(client: TestClient):
    make_guide(client)
    body = client.post(
        f"{PREFIX}/applications/app/events",
        json={
            "user_id": "dana",
            "event": "shown",
            "key": "welcome_tour",
            "at": AT,
            "programmatic": True,
        },
    ).json()
    assert body["programmatic"] is True
    assert body["session"]["programmatic_launches"] == 1
    assert body["restarted_clocks"] is False


# --------------------------------------------------------------------------- #
# The audit guarantee
# --------------------------------------------------------------------------- #


def test_every_audit_row_names_a_route_this_feature_serves(client: TestClient):
    make_guide(client)
    client.post(f"{PREFIX}/guides", json=CRITICAL)
    client.patch(f"{PREFIX}/guides/welcome_tour", json={"name": "Edited"})
    configure(client)
    client.post(
        f"{PREFIX}/applications/app/order/move", json={"key": "welcome_tour", "move": "down"}
    )
    client.post(
        f"{PREFIX}/applications/app/events",
        json={"user_id": "dana", "event": "dismissed", "key": "welcome_tour", "at": AT},
    )
    client.delete(f"{PREFIX}/guides/welcome_tour")
    client.post(f"{PREFIX}/guides/welcome_tour/restore")

    rows = client.get("/api/audit?limit=500").json()["entries"]
    mine = [row for row in rows if PREFIX in str(row.get("source") or "")]
    assert mine, "no audit row from this feature was written"
    routes = served_routes(client)
    for row in mine:
        assert names_a_served_route(str(row["source"]), routes), (
            f"audit row names {row['source']!r}, which is not a route this app serves"
        )


def test_the_recorded_actor_is_the_caller(client: TestClient):
    client.post(f"{PREFIX}/guides", json=WELCOME, headers={"X-Actor": "builder"})
    rows = client.get("/api/audit?limit=50").json()["entries"]
    mine = [
        row for row in rows if PREFIX in str(row.get("source") or "") and row["action"] == "insert"
    ]
    assert mine and mine[0]["actor"] == "builder"


# --------------------------------------------------------------------------- #
# The routes, called the way tools/verify_all_routes.py calls them
# --------------------------------------------------------------------------- #


def test_every_route_answers_without_a_5xx_when_called_with_an_empty_body(client: TestClient):
    faults: list[tuple[str, str, int]] = []
    for route in served_routes(client):
        if not route["path"].startswith(PREFIX):
            continue
        path = (
            route["path"]
            .replace("{key}", "absent")
            .replace("{app_id}", "app_absent")
            .replace("{user_id}", "usr_absent")
        )
        for method in route["methods"]:
            if method not in {"GET", "POST", "PATCH", "DELETE", "PUT"}:
                continue
            body = {} if method in {"POST", "PATCH", "PUT"} else None
            response = client.request(method, path, json=body)
            if response.status_code == 0 or response.status_code >= 500:
                faults.append((method, path, response.status_code))
    assert faults == [], f"routes answered 5xx when called with an empty body: {faults}"
