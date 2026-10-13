"""WF-112: the HTTP surface of a Slack notification from a buyer signal.

What is different from ``test_wf112.py``
----------------------------------------

The domain tests build the engine and call it directly. This file goes through the
router, so it is where four things are only observable:

* **The error mapping.** One handler is registered for the whole
  ``SignalSlackError`` hierarchy and it answers with the status and the code the
  exception carries. The two connect-time refusals the issue names are 422 with their
  own codes; an absent id is 404; a duplicate is 409.
* **The audit source.** Every write a route makes passes ``router.prefix``, so the
  audit row names the route that served it. Asserted against the routes the host
  actually mounted.
* **The query-string surface.** A room id is a query parameter here, and a filter
  arrives as one, so a client that already holds a room list does not have to build
  a path per filter.
* **The refusals that are correct.** ``tools/verify_all_routes.py`` calls every
  advertised route with no parameters at all, so every write route here is called
  empty and has to answer 4xx rather than 5xx.

The honest limit is asserted here too, once, through HTTP: a recorded decision says
what would be posted and that nothing was sent.
"""

from __future__ import annotations

import dsr.features as host
import pytest
from dsr.features import load_feature
from dsr.signal_slack.vocabulary import SIGNAL_COLLECTION

MODULE = "wf112_notify_the_internal_team_in_slack_from_a_buyer"
FEATURE_ID = "wf-112-slack-signal-notify"
PREFIX = "/api/wf-112"
ROOM = "room-1"


# --------------------------------------------------------------------------- #
# Provisioning over the store the routes share
# --------------------------------------------------------------------------- #


def _store(client):
    from dsr.api import app

    return app.state.store


def _signal(client, room_id=ROOM, **overrides):
    data = {
        "type": "pricing_page_view",
        "urgency": "high",
        "signal_name": "Buyer returned to pricing",
        "rendered": {"text": "Returned three times."},
        "idempotency_key": "wf112-http-signal",
    }
    data.update(overrides)
    return _store(client).create(SIGNAL_COLLECTION, data, room_id=room_id, source="test")


def connect(client, **overrides):
    body = {
        "workspace": "acme",
        "channel": "sales-signals",
        "channel_kind": "public",
        "scopes": ["bot", "chat:write"],
    }
    body.update(overrides)
    response = client.post(f"{PREFIX}/connection", json=body, params={"room_id": ROOM})
    assert response.status_code == 201, response.text
    return response.json()


def make_automation(client, **overrides):
    body = {
        "name": "High-intent buyer signal",
        "trigger": "signal",
        "state": "live",
        "channel": "sales-signals",
        "message": "New signal for {{customer.username}}. See <https://example.co/room|the room>.",
    }
    body.update(overrides)
    response = client.post(f"{PREFIX}/automations", json=body, params={"room_id": ROOM})
    assert response.status_code == 201, response.text
    return response.json()


def notify(client, signal_id, **overrides):
    body = {
        "automation_id": overrides.pop("automation_id", ""),
        "customer": {"customer": {"username": "Dana"}},
    }
    body.update(overrides)
    return client.post(f"{PREFIX}/signals/{signal_id}/notify", json=body, params={"room_id": ROOM})


@pytest.fixture
def wired(client):
    """A connection and a live block, the two prerequisites a notification needs."""
    connect(client)
    make_automation(client)
    return client


# --------------------------------------------------------------------------- #
# Discovery
# --------------------------------------------------------------------------- #


def test_the_registry_lists_this_feature_with_its_prefix_and_routes(client):
    listing = client.get("/api/features").json()
    record = next(f for f in listing["features"] if f["id"] == FEATURE_ID)
    assert record["ticket"] == "WF-112"
    assert record["prefix"] == PREFIX
    assert len(record["routes"]) == 16
    assert record["exception_handlers"] == ["SignalSlackError"]


def test_no_feature_failed_to_load(client):
    listing = client.get("/api/features").json()
    assert listing["failed_count"] == 0, [f["id"] for f in listing["failed"]]


def test_the_feature_lookup_route_answers(client):
    assert client.get(f"/api/features/{FEATURE_ID}").json()["prefix"] == PREFIX


def test_the_core_health_route_still_answers(client):
    assert client.get("/api/health").status_code == 200


# --------------------------------------------------------------------------- #
# Every route answers, and none of them 5xx
# --------------------------------------------------------------------------- #

ALL_ROUTES: tuple[tuple[str, str], ...] = (
    ("GET", "/vocabulary"),
    ("GET", "/inferences"),
    ("GET", "/summary"),
    ("GET", "/connection"),
    ("POST", "/connection"),
    ("DELETE", "/connection/conn_absent"),
    ("GET", "/automations"),
    ("POST", "/automations"),
    ("GET", "/automations/auto_absent"),
    ("PUT", "/automations/auto_absent"),
    ("DELETE", "/automations/auto_absent"),
    ("POST", "/automations/auto_absent/test"),
    ("POST", "/notify/preview"),
    ("POST", "/signals/sig_absent/notify"),
    ("GET", "/dispatches"),
    ("GET", "/dispatches/disp_absent"),
)


@pytest.mark.parametrize("method,path", ALL_ROUTES, ids=[f"{m} {p}" for m, p in ALL_ROUTES])
def test_every_route_answers_with_no_5xx(client, method, path):
    response = client.request(method, f"{PREFIX}{path}", json={})
    assert response.status_code < 500, f"{method} {path} -> {response.status_code} {response.text}"


def test_every_route_this_feature_advertises_is_called_by_this_file():
    """The list above has to keep up with the router, or the 5xx sweep is theatre."""
    advertised = set()
    for route in host.REGISTRY.by_id(FEATURE_ID).routes:
        tail = route["path"][len(PREFIX) :]
        for placeholder, replacement in (
            ("{connection_id}", "conn_absent"),
            ("{automation_id}", "auto_absent"),
            ("{signal_id}", "sig_absent"),
            ("{dispatch_id}", "disp_absent"),
        ):
            tail = tail.replace(placeholder, replacement)
        for method in route["methods"]:
            advertised.add((method, tail))
    assert set(ALL_ROUTES) == advertised


# --------------------------------------------------------------------------- #
# Vocabulary and the decision record
# --------------------------------------------------------------------------- #


def test_the_vocabulary_route_publishes_the_two_rate_limits(client):
    body = client.get(f"{PREFIX}/vocabulary").json()
    assert body["rate_limits"]["app_api"] == {
        "requests": 10,
        "per_seconds": 1,
        "quote": "App API 10 requests per second",
        "sourced": True,
    }
    assert body["rate_limits"]["broadcast"]["per_seconds"] == 10
    assert body["limit_for_kind"] == {"live": "broadcast", "test": "app_api"}


def test_the_vocabulary_route_says_nothing_is_sent(client):
    body = client.get(f"{PREFIX}/vocabulary").json()
    assert body["sends_slack"] is False
    assert body["delivery"] == "not_sent"
    assert body["attribution_excluded"] is True
    assert set(body["researched_refusals"]) == {
        "missing_bot_scope",
        "channel_required",
        "bot_not_in_channel",
    }


def test_the_inferences_route_serves_every_decision_with_a_change(client):
    body = client.get(f"{PREFIX}/inferences").json()
    assert body["count"] >= 15
    assert body["sourced_count"] >= 6
    for entry in body["inferences"]:
        assert {"id", "question", "reading", "why", "change", "risk"} <= set(entry), entry


# --------------------------------------------------------------------------- #
# The connection
# --------------------------------------------------------------------------- #


def test_a_connection_is_created_then_read_back_over_http(client):
    created = connect(client)
    assert created["workspace"] == "acme"
    assert created["channel"] == "sales-signals"
    body = client.get(f"{PREFIX}/connection", params={"room_id": ROOM}).json()
    assert body["connected"] is True
    assert body["connection"]["id"] == created["id"]


def test_a_credential_without_the_bot_scope_is_422_over_http(client):
    response = client.post(
        f"{PREFIX}/connection",
        json={"workspace": "acme", "channel": "sales", "scopes": ["chat:write"]},
        params={"room_id": ROOM},
    )
    assert response.status_code == 422
    assert response.json()["error"] == "missing_bot_scope"


def test_a_connect_without_a_channel_is_422_over_http(client):
    response = client.post(
        f"{PREFIX}/connection",
        json={"workspace": "acme", "scopes": ["bot"]},
        params={"room_id": ROOM},
    )
    assert response.status_code == 422
    assert response.json()["error"] == "channel_required"


def test_a_second_connection_in_one_room_is_409_over_http(client):
    connect(client)
    again = client.post(
        f"{PREFIX}/connection",
        json={"workspace": "acme", "channel": "other", "scopes": ["bot"]},
        params={"room_id": ROOM},
    )
    assert again.status_code == 409
    assert again.json()["error"] == "connection_already_exists"


def test_a_connection_can_be_revoked_over_http(client):
    created = connect(client)
    assert client.delete(f"{PREFIX}/connection/{created['id']}").status_code == 200
    assert client.get(f"{PREFIX}/connection", params={"room_id": ROOM}).json()["connected"] is False


def test_an_absent_connection_is_404_over_http(client):
    assert client.delete(f"{PREFIX}/connection/conn_absent").status_code == 404


# --------------------------------------------------------------------------- #
# Blocks
# --------------------------------------------------------------------------- #


def test_a_block_is_created_then_read_and_updated_over_http(client):
    created = make_automation(client)
    assert created["state"] == "live"
    listed = client.get(f"{PREFIX}/automations", params={"room_id": ROOM}).json()
    assert listed["count"] == 1
    assert listed["live"] == 1
    patched = client.put(
        f"{PREFIX}/automations/{created['id']}", json={"state": "draft"}, params={"actor": "dana"}
    )
    assert patched.status_code == 200
    assert patched.json()["state"] == "draft"


def test_two_blocks_may_not_share_a_name_over_http(client):
    make_automation(client)
    again = client.post(
        f"{PREFIX}/automations",
        json={"name": "High-intent buyer signal", "message": "hi"},
        params={"room_id": ROOM},
    )
    assert again.status_code == 409
    assert again.json()["error"] == "automation_already_exists"


def test_a_block_with_a_malformed_mention_is_422_over_http(client):
    response = client.post(
        f"{PREFIX}/automations",
        json={"name": "x", "message": "Hey <@dana>"},
        params={"room_id": ROOM},
    )
    assert response.status_code == 422
    assert response.json()["error"] == "invalid_automation"


def test_a_block_can_be_removed_over_http(client):
    created = make_automation(client)
    assert client.delete(f"{PREFIX}/automations/{created['id']}").status_code == 200
    assert client.get(f"{PREFIX}/automations", params={"room_id": ROOM}).json()["count"] == 0


def test_an_absent_block_is_404_over_http(client):
    assert client.get(f"{PREFIX}/automations/auto_absent").status_code == 404
    assert client.delete(f"{PREFIX}/automations/auto_absent").status_code == 404


def test_a_test_send_is_recorded_over_http(wired):
    block = wired.get(f"{PREFIX}/automations", params={"room_id": ROOM}).json()["automations"][0]
    response = wired.post(
        f"{PREFIX}/automations/{block['id']}/test",
        json={"channel": "sales-signals", "customer": {"customer": {"username": "Dana"}}},
        params={"room_id": ROOM},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["kind"] == "test"
    assert body["state"] == "recorded"
    assert "Dana" in body["message"]["text"]


# --------------------------------------------------------------------------- #
# The decision from a signal
# --------------------------------------------------------------------------- #


def test_a_signal_records_a_deliverable_decision_over_http(wired):
    signal = _signal(wired)
    block = wired.get(f"{PREFIX}/automations", params={"room_id": ROOM}).json()["automations"][0]
    response = notify(wired, signal["id"], automation_id=block["id"])
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["state"] == "recorded"
    assert body["channel"] == "sales-signals"
    assert body["signal"]["type"] == "pricing_page_view"
    assert body["delivery"] == "not_sent"
    assert "Dana" in body["message"]["text"]


def test_a_recorded_decision_carries_no_conversion_over_http(wired):
    signal = _signal(wired)
    block = wired.get(f"{PREFIX}/automations", params={"room_id": ROOM}).json()["automations"][0]
    body = notify(wired, signal["id"], automation_id=block["id"]).json()
    assert body["attribution_excluded"] is True
    assert not any("conversion" in key for key in body)


def test_an_unset_tag_renders_a_fallback_over_http(wired):
    block = wired.get(f"{PREFIX}/automations", params={"room_id": ROOM}).json()["automations"][0]
    body = notify(wired, _signal(wired)["id"], automation_id=block["id"], customer={}).json()
    assert "{{" not in body["message"]["text"]
    assert "the buyer" in body["message"]["text"]


def test_a_second_live_post_is_rate_limited_over_http(wired):
    signal = _signal(wired)
    block = wired.get(f"{PREFIX}/automations", params={"room_id": ROOM}).json()["automations"][0]
    first = notify(wired, signal["id"], automation_id=block["id"]).json()
    second = notify(wired, signal["id"], automation_id=block["id"]).json()
    assert first["state"] == "recorded"
    assert second["state"] == "refused"
    assert second["refusal"] == "rate_limited"
    assert second["retry_after_seconds"] > 0


def test_a_bot_not_in_a_private_channel_is_refused_over_http(client):
    connect(client, channel="deal-desk", channel_kind="private")
    block = make_automation(client, channel="deal-desk")
    signal = _signal(client)
    body = notify(client, signal["id"], automation_id=block["id"]).json()
    assert body["state"] == "refused"
    assert body["refusal"] == "bot_not_in_channel"
    assert "/invite" in body["reason"]


def test_a_room_with_no_connection_is_refused_over_http(client):
    signal = _signal(client)
    body = notify(client, signal["id"]).json()
    assert body["state"] == "refused"
    assert body["refusal"] == "no_connection"


def test_a_live_notification_without_a_signal_is_422_over_http(wired):
    response = wired.post(
        f"{PREFIX}/notify/preview", json={"kind": "live"}, params={"room_id": ROOM}
    )
    assert response.status_code == 422
    assert response.json()["error"] == "invalid_dispatch"


def test_an_unknown_signal_is_404_over_http(wired):
    response = notify(wired, "sig_absent")
    assert response.status_code == 404
    assert response.json()["error"] == "unknown_signal"


def test_the_preview_reports_a_decision_and_writes_nothing_over_http(wired):
    signal = _signal(wired)
    block = wired.get(f"{PREFIX}/automations", params={"room_id": ROOM}).json()["automations"][0]
    body = wired.post(
        f"{PREFIX}/notify/preview",
        json={"kind": "live", "signal_id": signal["id"], "automation_id": block["id"]},
        params={"room_id": ROOM},
    ).json()
    assert body["state"] == "recorded"
    assert wired.get(f"{PREFIX}/dispatches", params={"room_id": ROOM}).json()["count"] == 0


# --------------------------------------------------------------------------- #
# Recorded decisions
# --------------------------------------------------------------------------- #


def test_the_dispatches_route_tallies_states_and_refusals_over_http(wired):
    signal = _signal(wired)
    block = wired.get(f"{PREFIX}/automations", params={"room_id": ROOM}).json()["automations"][0]
    notify(wired, signal["id"], automation_id=block["id"])
    notify(wired, signal["id"], automation_id=block["id"])
    body = wired.get(f"{PREFIX}/dispatches", params={"room_id": ROOM}).json()
    assert body["count"] == 2
    assert body["by_state"] == {"recorded": 1, "refused": 1}
    assert body["by_refusal"] == {"rate_limited": 1}


def test_a_dispatch_is_filterable_by_kind_over_http(wired):
    signal = _signal(wired)
    block = wired.get(f"{PREFIX}/automations", params={"room_id": ROOM}).json()["automations"][0]
    notify(wired, signal["id"], automation_id=block["id"])
    wired.post(
        f"{PREFIX}/automations/{block['id']}/test",
        json={"channel": "sales-signals"},
        params={"room_id": ROOM},
    )
    assert (
        wired.get(f"{PREFIX}/dispatches", params={"room_id": ROOM, "kind": "test"}).json()["count"]
        == 1
    )
    assert (
        wired.get(f"{PREFIX}/dispatches", params={"room_id": ROOM, "kind": "live"}).json()["count"]
        == 1
    )


def test_an_absent_dispatch_is_404_over_http(client):
    response = client.get(f"{PREFIX}/dispatches/disp_absent")
    assert response.status_code == 404
    assert response.json()["error"] == "unknown_dispatch"


def test_an_out_of_range_limit_is_422_over_http(client):
    assert client.get(f"{PREFIX}/dispatches", params={"limit": 5000}).status_code == 422


def test_the_summary_route_counts_without_reading_another_feature(client):
    body = client.get(f"{PREFIX}/summary", params={"room_id": ROOM}).json()
    assert body["connected"] is False
    assert body["automations"] == 0
    assert body["sends_slack"] is False


# --------------------------------------------------------------------------- #
# The audit source: the route that served the write
# --------------------------------------------------------------------------- #


def _mounted() -> set[str]:
    record = host.REGISTRY.by_id(FEATURE_ID)
    return {f"{method} {route['path']}" for route in record.routes for method in route["methods"]}


def _audit(client) -> list[dict]:
    from dsr.api import app

    return app.state.db.audit()


def _http_sources(client) -> set[str]:
    verbs = ("POST ", "PATCH ", "DELETE ", "PUT ")
    return {
        entry["source"]
        for entry in _audit(client)
        if entry.get("source") and entry["source"].startswith(verbs)
    }


def test_every_write_route_leaves_an_audit_row_naming_itself(wired):
    connection = wired.get(f"{PREFIX}/connection", params={"room_id": ROOM}).json()["connection"]
    wired.delete(f"{PREFIX}/connection/{connection['id']}")
    block = make_automation(wired, name="Second block")
    wired.put(f"{PREFIX}/automations/{block['id']}", json={"note": "edited"})
    wired.post(f"{PREFIX}/automations/{block['id']}/test", json={}, params={"room_id": ROOM})
    signal = _signal(wired)
    notify(wired, signal["id"], automation_id=block["id"])
    wired.delete(f"{PREFIX}/automations/{block['id']}")

    mounted = _mounted()
    ours = _http_sources(wired)
    assert ours, "no write was audited at all"
    for source in ours:
        assert source in mounted, f"audit row names {source!r}, which is not a mounted route"


def test_the_expected_routes_are_the_ones_that_wrote(wired):
    block = make_automation(wired, name="Second block")
    wired.put(f"{PREFIX}/automations/{block['id']}", json={"note": "edited"})
    wired.post(f"{PREFIX}/automations/{block['id']}/test", json={}, params={"room_id": ROOM})
    signal = _signal(wired)
    notify(wired, signal["id"], automation_id=block["id"])
    wired.delete(f"{PREFIX}/automations/{block['id']}")

    assert _http_sources(wired) == {
        f"POST {PREFIX}/connection",
        f"POST {PREFIX}/automations",
        f"PUT {PREFIX}/automations/{{automation_id}}",
        f"DELETE {PREFIX}/automations/{{automation_id}}",
        f"POST {PREFIX}/automations/{{automation_id}}/test",
        f"POST {PREFIX}/signals/{{signal_id}}/notify",
    }


# --------------------------------------------------------------------------- #
# The feature module, read rather than exercised
# --------------------------------------------------------------------------- #


def test_the_feature_module_is_importable_on_its_own():
    feature = load_feature(MODULE)
    assert feature.FEATURE["id"] == FEATURE_ID
    assert feature.router.prefix == PREFIX
    assert callable(feature.seed)


def test_the_seed_returns_a_cp1252_string_of_mixed_states(memory_db):
    """``seed.py`` prints this string on a Windows console.

    A rightwards arrow in one recovered feature broke the whole seeder, so every
    character is asserted encodable rather than assumed, and the demo is asserted not
    to be all successes.
    """
    from datetime import datetime, timezone

    feature = load_feature(MODULE)
    text = feature.seed(
        memory_db,
        {
            "room_ids": [("room-1", "A"), ("room-2", "B"), ("room-3", "C")],
            "now": datetime(2026, 10, 13, 9, 0, tzinfo=timezone.utc),
        },
    )
    assert text
    assert text.isascii()
    assert text.encode("cp1252").decode("cp1252") == text
    assert "recorded" in text and "refused" in text

