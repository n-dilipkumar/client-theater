"""WF-106: the HTTP surface of browsing-triggered outreach.

What is different from ``test_wf106.py``
----------------------------------------

The domain tests build the engine and call it directly. This file goes through the
router, so it is where five things are only observable:

* **The error mapping.** One handler is registered for the whole
  ``PageOutreachError`` hierarchy and it answers with the status and the code the
  exception carries. Each researched refusal is answered with the status that matches
  what it is: a page view that cannot be read is 422, an unknown workflow is 404, and
  a workflow name already taken in that room is 409.
* **The audit source.** Every write a route makes passes ``router.prefix``, so the
  audit row names the route that served it. Asserted against the routes the host
  actually mounted, which is the check the brief calls for by name.
* **The query-string surface.** Filters arrive as query parameters rather than as a
  dictionary, and room is a query parameter here rather than a path segment because
  the research's blocks are room-scoped and the room list is the entry point a client
  already has.
* **The refusals that are correct.** ``tools/verify_all_routes.py`` calls every
  advertised route with no parameters at all, so every write route here is called
  empty and has to answer 4xx rather than 5xx.
* **The registry.** The feature is mounted by discovery alone, its eighteen routes are
  the ones this file sweeps, and nothing failed to load.

The honest limits are asserted here too, once, through HTTP: a block that would be
shown is a record and not a message, and the vocabulary route says so in a field the
page renders.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import dsr.features as host
import pytest
from dsr.features import load_feature

MODULE = "wf106_trigger_outreach_on_high_intent_page_visits"
FEATURE_ID = "wf-106-trigger-outreach-on-high-intent-page-visits"
PREFIX = "/api/wf-106"
ROOM = "room-1"

NOW = datetime(2026, 10, 5, 9, 0, tzinfo=timezone.utc)

WORKFLOW_BODY: dict[str, object] = {
    "name": "Upgrade page repeaters",
    "frequency": "engaged_with",
    "state": "live",
    "repeat_visits": 2,
    "repeat_window_days": 7,
    "dwell_seconds": 60,
    "rules": [
        {"kind": "url", "mode": "prefix", "value": "/upgrade"},
        {"kind": "dwell", "value": "60"},
    ],
    "audience": {"company_keys": [], "tags": [], "segments": []},
    "goal_name": "meeting_booked",
    "blocks": [
        {
            "kind": "message",
            "text": "You have been back to this page a few times.",
            "apps": [{"kind": "video", "title": "Walkthrough", "url": "https://x.example/v"}],
        }
    ],
    "paths": [
        {"key": "yes_upgrade", "label": "Yes, let's talk about upgrading", "next": "book"},
        {"key": "not_right_now", "label": "Not right now", "closes": True},
    ],
    "note": "Two matching visits in seven days.",
}


def view_body(workflow_id: str, **overrides: object) -> dict[str, object]:
    """One page view against that workflow, defaulting to one that satisfies both rules."""
    body: dict[str, object] = {
        "workflow_id": workflow_id,
        "visitor_key": "visitor-1",
        "session_id": "sess-1",
        "path": "/upgrade/plans",
        "dwell_seconds": 74,
        "company_key": "northwind-energy",
        "visited_at": NOW.isoformat(),
    }
    body.update(overrides)
    return body


def save_workflow(client, **overrides: object) -> dict:
    """Save one workflow over HTTP and return it."""
    body = {**WORKFLOW_BODY, **overrides}
    response = client.post(f"{PREFIX}/workflows", json=body, params={"room_id": ROOM})
    assert response.status_code == 201, response.text
    return response.json()


def send_view(client, workflow_id: str, **overrides: object):
    """Post one page view over HTTP."""
    return client.post(
        f"{PREFIX}/views",
        json=view_body(workflow_id, **overrides),
        params={"room_id": ROOM},
    )


def qualify_over_http(client, workflow_id: str, **overrides: object) -> dict:
    """Post enough views to cross the workflow's two-visit threshold, return the last."""
    response = send_view(client, workflow_id, session_id="sess-1", **overrides)
    assert response.status_code == 201, response.text
    final = client.post(
        f"{PREFIX}/views",
        json=view_body(workflow_id, session_id="sess-2", **overrides),
        params={"room_id": ROOM},
    )
    assert final.status_code == 201, final.text
    return final.json()


@pytest.fixture()
def wired(client):
    """A client with one live workflow already saved in ROOM."""
    save_workflow(client)
    return client


# --------------------------------------------------------------------------- #
# Discovery
# --------------------------------------------------------------------------- #


def test_the_registry_lists_this_feature_with_its_prefix_and_routes(client):
    listing = client.get("/api/features").json()
    record = next(f for f in listing["features"] if f["id"] == FEATURE_ID)
    assert record["ticket"] == "WF-106"
    assert record["prefix"] == PREFIX
    assert len(record["routes"]) == 18
    assert record["exception_handlers"] == ["PageOutreachError"]
    assert record["loaded"] is True


def test_no_feature_failed_to_load(client):
    """A feature that raises on import is skipped rather than fatal, which is why the
    failure has to be asserted rather than assumed."""
    listing = client.get("/api/features").json()
    assert listing["failed_count"] == 0, [f["id"] for f in listing["failed"]]


def test_the_feature_lookup_route_answers(client):
    assert client.get(f"/api/features/{FEATURE_ID}").json()["prefix"] == PREFIX


def test_the_core_health_route_still_answers(client):
    assert client.get("/api/health").status_code == 200


def test_the_module_is_mounted_by_discovery_alone(client):
    """It is named in no shared file, so the route resolving is the whole proof."""
    module = load_feature(MODULE)
    assert module.FEATURE["id"] == FEATURE_ID
    assert module.router.prefix == PREFIX


def test_the_workflow_is_registered_once(client):
    """One workflow, one registration. A second copy would double every block a seller sets."""
    ids = [f["id"] for f in client.get("/api/features").json()["features"]]
    assert ids.count(FEATURE_ID) == 1


# --------------------------------------------------------------------------- #
# Every route answers, and none of them 5xx
# --------------------------------------------------------------------------- #

#: Every route, with the method that answers it. Called with no parameters at all,
#: which is what ``tools/verify_all_routes.py`` does, so a 4xx is the correct answer
#: for the write routes and a 200 for the reads.
ALL_ROUTES: tuple[tuple[str, str], ...] = (
    ("GET", "/vocabulary"),
    ("GET", "/inferences"),
    ("GET", "/summary"),
    ("GET", "/workflows"),
    ("POST", "/workflows"),
    ("GET", "/workflows/wf_absent"),
    ("PUT", "/workflows/wf_absent"),
    ("DELETE", "/workflows/wf_absent"),
    ("POST", "/workflows/wf_absent/state"),
    ("POST", "/views"),
    ("POST", "/views/evaluate"),
    ("GET", "/views"),
    ("GET", "/views/view_absent"),
    ("GET", "/deliveries"),
    ("GET", "/deliveries/del_absent"),
    ("POST", "/deliveries/del_absent/interactions"),
    ("GET", "/receipts"),
    ("GET", "/prospects"),
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
        for placeholder in ("{workflow_id}", "{view_id}", "{delivery_id}"):
            tail = tail.replace(
                placeholder,
                "wf_absent"
                if placeholder == "{workflow_id}"
                else "view_absent"
                if placeholder == "{view_id}"
                else "del_absent",
            )
        for method in route["methods"]:
            advertised.add((method, tail))
    assert set(ALL_ROUTES) == advertised


# --------------------------------------------------------------------------- #
# Vocabulary and the decision record
# --------------------------------------------------------------------------- #


def test_the_vocabulary_route_publishes_the_thresholds_as_unsourced(client):
    body = client.get(f"{PREFIX}/vocabulary").json()
    assert [entry["threshold"] for entry in body["thresholds"]] == [3, 7, 60]
    assert body["sourced_thresholds"] == []
    assert body["derived_thresholds"] == ["repeat_visits", "repeat_window", "dwell"]
    for entry in body["thresholds"]:
        assert entry["sourced"] is False
        assert entry["derivation"]
        assert entry["risk"]


def test_the_vocabulary_route_says_nothing_is_sent(client):
    body = client.get(f"{PREFIX}/vocabulary").json()
    assert body["calls_vendor"] is False
    assert body["sends_email"] is False
    assert body["sends_sms"] is False
    assert body["renders_in_browser_messenger"] is False
    assert body["channels"] == ["in_app"]
    assert "posts no message to any vendor" in body["reads"]


def test_the_vocabulary_route_publishes_the_three_sourced_modes_with_their_quotes(client):
    body = client.get(f"{PREFIX}/vocabulary").json()
    modes = body["frequency_modes"]
    assert [entry["mode"] for entry in modes] == ["seen", "any_interaction", "engaged_with"]
    assert body["default_frequency"] == "seen"
    for entry in modes:
        assert entry["sourced"] is True, entry["mode"]
        assert entry["quote"], entry["mode"]


def test_the_vocabulary_route_publishes_the_five_trigger_panes(client):
    body = client.get(f"{PREFIX}/vocabulary").json()
    panes = [entry["pane"] for entry in body["trigger_panes"]]
    assert panes == ["when_to_send", "where_to_send", "audience", "scheduling", "goal"]
    assert all(entry["sourced"] is False for entry in body["trigger_panes"])
    for entry in body["trigger_panes"]:
        assert entry["reading"], entry["pane"]
        assert entry["fields"], entry["pane"]


def test_the_vocabulary_route_publishes_the_two_trigger_signals_and_the_rule_table(client):
    body = client.get(f"{PREFIX}/vocabulary").json()
    assert body["trigger_signals"] == ["visited_url", "time_on_page"]
    assert [entry["kind"] for entry in body["rule_table"]] == [
        "url",
        "dwell",
        "utm_source",
        "utm_campaign",
    ]
    assert body["url_match_modes"] == ["exact", "prefix", "contains"]


def test_the_vocabulary_route_lists_the_limits_the_research_left_open(client):
    body = client.get(f"{PREFIX}/vocabulary").json()
    limits = {entry["limit"] for entry in body["unsourced_limits"]}
    assert {
        "repeat_count",
        "repeat_window",
        "dwell_threshold",
        "trigger_pane_contents",
        "ingest_contract",
    } <= limits


def test_the_inferences_route_serves_every_decision_with_a_change_and_a_risk(client):
    body = client.get(f"{PREFIX}/inferences").json()
    assert body["count"] == len(body["inferences"])
    assert body["count"] >= 17
    assert body["sourced_count"] + body["inferred_count"] == body["count"]
    for entry in body["inferences"]:
        assert {"id", "question", "reading", "why", "change", "risk", "sourced"} <= set(entry)


def test_the_inferences_route_names_the_vendor_threshold_as_unsourced(client):
    body = client.get(f"{PREFIX}/inferences").json()
    joined = " ".join(f"{entry['reading']} {entry['why']}" for entry in body["inferences"])
    assert "states no count" in joined
    assert "states no window" in joined or "states no repeat window" in joined
    assert "gives no number" in joined


def test_the_summary_route_counts_only_this_features_collections(client):
    body = client.get(f"{PREFIX}/summary", params={"room_id": ROOM}).json()
    assert body["room_id"] == ROOM
    assert body["workflows"] == 0
    assert body["calls_vendor"] is False


def test_the_summary_route_of_an_empty_room_is_all_zeroes_and_not_an_error(client):
    body = client.get(f"{PREFIX}/summary", params={"room_id": "room-empty"}).json()
    assert body["workflows"] == 0
    assert body["deliveries"] == 0
    assert body["receipt_kinds"] == {}


# --------------------------------------------------------------------------- #
# Workflows over HTTP
# --------------------------------------------------------------------------- #


def test_a_workflow_is_created_then_read_back_over_http(client):
    created = save_workflow(client)
    assert created["name"] == "Upgrade page repeaters"
    assert created["rule_count"] == 2
    assert created["path_count"] == 2
    listed = client.get(f"{PREFIX}/workflows", params={"room_id": ROOM}).json()
    assert listed["count"] == 1
    assert listed["by_state"] == {"live": 1}
    assert client.get(f"{PREFIX}/workflows/{created['id']}").json()["id"] == created["id"]


def test_a_workflow_is_a_draft_over_http_unless_the_caller_says_live(client):
    created = save_workflow(client, state="")
    assert created["state"] == "draft"


def test_a_workflow_can_be_replaced_over_http(client):
    created = save_workflow(client)
    response = client.put(
        f"{PREFIX}/workflows/{created['id']}",
        json={**WORKFLOW_BODY, "rules": [{"kind": "url", "mode": "prefix", "value": "/pricing"}]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["rule_count"] == 1
    assert (
        client.get(f"{PREFIX}/workflows/{created['id']}").json()["rules"][0]["value"] == "/pricing"
    )


def test_a_workflow_can_be_set_live_over_http(client):
    created = save_workflow(client, state="draft")
    response = client.post(
        f"{PREFIX}/workflows/{created['id']}/state", json={"state": "live"}, params={"actor": "sam"}
    )
    assert response.status_code == 201, response.text
    assert response.json()["state"] == "live"


def test_setting_an_unpublished_state_over_http_is_422(client):
    created = save_workflow(client)
    response = client.post(f"{PREFIX}/workflows/{created['id']}/state", json={"state": "paused"})
    assert response.status_code == 422
    assert response.json()["error"] == "invalid_workflow"


def test_a_workflow_can_be_deleted_over_http(client):
    created = save_workflow(client)
    assert client.delete(f"{PREFIX}/workflows/{created['id']}").status_code == 200
    assert client.get(f"{PREFIX}/workflows", params={"room_id": ROOM}).json()["count"] == 0


def test_an_empty_workflow_over_http_is_422(client):
    response = client.post(f"{PREFIX}/workflows", json={}, params={"room_id": ROOM})
    assert response.status_code == 422
    assert response.json()["error"] == "invalid_workflow"
    assert "name is required" in response.json()["detail"]


def test_a_second_workflow_on_one_name_over_http_is_409(client):
    save_workflow(client)
    again = client.post(f"{PREFIX}/workflows", json=WORKFLOW_BODY, params={"room_id": ROOM})
    assert again.status_code == 409
    assert again.json()["error"] == "workflow_already_exists"


def test_a_channel_this_build_cannot_honour_over_http_is_422_and_says_why(client):
    response = client.post(
        f"{PREFIX}/workflows", json={**WORKFLOW_BODY, "channel": "email"}, params={"room_id": ROOM}
    )
    assert response.status_code == 422
    assert response.json()["error"] == "invalid_workflow"
    assert "no outbound transport" in response.json()["detail"]


def test_a_rule_on_a_kind_no_matcher_implements_over_http_is_422(client):
    response = client.post(
        f"{PREFIX}/workflows",
        json={**WORKFLOW_BODY, "rules": [{"kind": "referrer_host", "value": "x"}]},
        params={"room_id": ROOM},
    )
    assert response.status_code == 422
    assert "implements" in response.json()["detail"]


def test_two_paths_on_one_key_over_http_is_422(client):
    response = client.post(
        f"{PREFIX}/workflows",
        json={
            **WORKFLOW_BODY,
            "paths": [{"key": "y", "label": "Yes"}, {"key": "y", "label": "Yes"}],
        },
        params={"room_id": ROOM},
    )
    assert response.status_code == 422
    assert "cannot both be right" in response.json()["detail"]


@pytest.mark.parametrize("method", ["GET", "PUT", "DELETE"])
def test_reading_replacing_or_deleting_an_absent_workflow_over_http_is_404(client, method):
    response = client.request(method, f"{PREFIX}/workflows/wf_absent", json={})
    assert response.status_code == 404, method
    assert response.json()["error"] == "unknown_workflow"


def test_setting_the_state_of_an_absent_workflow_over_http_is_404(client):
    response = client.post(f"{PREFIX}/workflows/wf_absent/state", json={"state": "live"})
    assert response.status_code == 404
    assert response.json()["error"] == "unknown_workflow"


def test_workflows_are_room_scoped_over_http(client):
    save_workflow(client)
    assert client.get(f"{PREFIX}/workflows", params={"room_id": "room-2"}).json()["count"] == 0


# --------------------------------------------------------------------------- #
# Page views over HTTP
# --------------------------------------------------------------------------- #


def test_the_evaluate_route_reports_a_decision_and_writes_nothing(wired):
    workflow = client_get_workflow(wired)
    response = wired.post(f"{PREFIX}/views/evaluate", json=view_body(workflow["id"]))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["show"] is False
    assert body["wrote"] is False
    assert body["stopped_by"] == "matched_below_repeat"
    assert wired.get(f"{PREFIX}/views", params={"room_id": ROOM}).json()["count"] == 0


def test_the_evaluate_route_reports_that_the_block_would_be_shown(wired):
    workflow = client_get_workflow(wired)
    qualify_over_http(wired, workflow["id"])
    body = wired.post(
        f"{PREFIX}/views/evaluate", json=view_body(workflow["id"], session_id="sess-9")
    ).json()
    assert body["show"] is True
    assert body["stopped_by"] == "show"
    assert body["wrote"] is False


def test_the_evaluate_route_names_the_gate_that_stopped_it(wired):
    workflow = client_get_workflow(wired)
    body = wired.post(
        f"{PREFIX}/views/evaluate", json=view_body(workflow["id"], path="/about/team")
    ).json()
    assert body["stopped_by"] == "rules_not_matched"
    assert "/about/team" in body["reason"]


def test_the_evaluate_route_on_a_draft_says_the_workflow_is_not_live(client):
    created = save_workflow(client, state="draft")
    body = client.post(f"{PREFIX}/views/evaluate", json=view_body(created["id"])).json()
    assert body["stopped_by"] == "not_live"
    assert body["state"] == "draft"
    assert "only a live workflow" in body["reason"]


def test_the_evaluate_route_against_an_absent_workflow_over_http_is_404(client):
    response = client.post(f"{PREFIX}/views/evaluate", json=view_body("nope"))
    assert response.status_code == 404
    assert response.json()["error"] == "unknown_workflow"


def test_an_empty_page_view_over_http_is_422(client):
    response = client.post(f"{PREFIX}/views", json={}, params={"room_id": ROOM})
    assert response.status_code == 422
    assert response.json()["error"] == "invalid_page_view"


def test_a_page_view_carrying_an_unread_field_over_http_is_422(wired):
    workflow = client_get_workflow(wired)
    response = send_view(wired, workflow["id"], user_agent="Mozilla/5.0")
    assert response.status_code == 422
    assert response.json()["error"] == "unknown_page_view_field"


def test_a_view_against_an_absent_workflow_over_http_is_404(client):
    response = send_view(client, "nope")
    assert response.status_code == 404
    assert response.json()["error"] == "unknown_workflow"


def test_the_first_view_over_http_is_recorded_and_shows_nothing(wired):
    workflow = client_get_workflow(wired)
    response = send_view(wired, workflow["id"], session_id="sess-1")
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["show"] is False
    assert body["wrote"] is True
    assert body["delivery"] is None
    # The view is written even though nothing was shown.
    assert wired.get(f"{PREFIX}/views", params={"room_id": ROOM}).json()["count"] == 1


def test_the_second_matching_view_over_http_shows_the_block(wired):
    workflow = client_get_workflow(wired)
    body = qualify_over_http(wired, workflow["id"])
    assert body["show"] is True
    assert body["delivery"]["state"] == "shown"
    assert body["delivery"]["channel"] == "in_app"
    assert body["delivery"]["message_type"] == "in_app"
    assert wired.get(f"{PREFIX}/deliveries", params={"room_id": ROOM}).json()["count"] == 1


def test_the_delivery_over_http_carries_the_blocks_and_the_trigger(wired):
    workflow = client_get_workflow(wired)
    delivery = qualify_over_http(wired, workflow["id"])["delivery"]
    assert delivery["blocks"][0]["apps"][0]["kind"] == "video"
    assert delivery["trigger"]["matching_visits"] == 2
    assert delivery["trigger"]["visits_required"] == 2
    assert delivery["trigger"]["window_days"] == 7
    assert delivery["first_path_key"] == "yes_upgrade"


def test_the_delivery_over_http_reports_each_rule_and_whether_it_held(wired):
    workflow = client_get_workflow(wired)
    delivery = qualify_over_http(wired, workflow["id"])["delivery"]
    held = {entry["kind"]: entry["met"] for entry in delivery["trigger"]["rule_matches"]}
    assert held == {"url": True, "dwell": True}


def test_a_view_whose_dwell_is_short_over_http_says_which_rule_failed(wired):
    workflow = client_get_workflow(wired)
    send_view(wired, workflow["id"], session_id="sess-1", dwell_seconds=12)
    body = send_view(wired, workflow["id"], session_id="sess-2", dwell_seconds=12).json()
    assert body["show"] is False
    assert body["stopped_by"] == "rules_not_matched"
    assert "dwell expected 60 and saw 12" in body["reason"]


def test_the_damper_over_http_hides_the_block_for_the_rest_of_the_session(wired):
    workflow = client_get_workflow(wired)
    shown = qualify_over_http(wired, workflow["id"])
    delivery_id = shown["delivery"]["id"]
    wired.post(
        f"{PREFIX}/deliveries/{delivery_id}/interactions",
        json={"kind": "messenger_opened"},
        params={"actor": "dana"},
    )
    blocked = wired.post(
        f"{PREFIX}/views",
        json=view_body(workflow["id"], session_id="sess-2"),
        params={"room_id": ROOM},
    ).json()
    assert blocked["stopped_by"] == "hidden_for_session"
    assert "opened the Messenger" in blocked["reason"]


def test_the_damper_over_http_expires_in_the_next_session(wired):
    workflow = client_get_workflow(wired)
    shown = qualify_over_http(wired, workflow["id"])
    wired.post(
        f"{PREFIX}/deliveries/{shown['delivery']['id']}/interactions",
        json={"kind": "messenger_opened"},
    )
    fresh = wired.post(
        f"{PREFIX}/views",
        json=view_body(workflow["id"], session_id="sess-fresh"),
        params={"room_id": ROOM},
    ).json()
    assert fresh["show"] is True
    assert wired.get(f"{PREFIX}/deliveries", params={"room_id": ROOM}).json()["count"] == 2


def test_the_engaged_with_mode_over_http_stops_once_the_buyer_engages(wired):
    workflow = client_get_workflow(wired)
    shown = qualify_over_http(wired, workflow["id"])
    wired.post(
        f"{PREFIX}/deliveries/{shown['delivery']['id']}/interactions",
        json={"kind": "path_selected", "path_key": "yes_upgrade"},
    )
    later = wired.post(
        f"{PREFIX}/views",
        json=view_body(workflow["id"], session_id="sess-later"),
        params={"room_id": ROOM},
    ).json()
    assert later["stopped_by"] == "frequency_mode"
    assert "engaged by selecting a path" in later["reason"]


def test_the_seen_mode_over_http_shows_once(wired):
    created = save_workflow(wired, name="Seen once", frequency="seen", repeat_visits=1)
    first = send_view(wired, created["id"], session_id="sess-a").json()
    assert first["show"] is True
    second = send_view(wired, created["id"], session_id="sess-b").json()
    assert second["show"] is False
    assert second["stopped_by"] == "frequency_mode"
    assert wired.get(f"{PREFIX}/deliveries", params={"room_id": ROOM}).json()["count"] == 1


def test_a_draft_workflow_over_http_never_shows_a_block(client):
    created = save_workflow(client, state="draft", repeat_visits=1)
    body = send_view(client, created["id"]).json()
    assert body["show"] is False
    assert body["stopped_by"] == "not_live"
    assert client.get(f"{PREFIX}/deliveries", params={"room_id": ROOM}).json()["count"] == 0


def test_a_company_audience_over_http_excludes_a_visitor_and_says_why(client):
    created = save_workflow(
        client, audience={"company_keys": ["northwind-energy"], "tags": [], "segments": []}
    )
    body = send_view(client, created["id"], company_key="meridian-foods").json()
    assert body["stopped_by"] == "audience"
    assert "not on the audience company list" in body["reason"]


def test_the_views_route_over_http_groups_by_the_gate_that_stopped_them(wired):
    workflow = client_get_workflow(wired)
    send_view(wired, workflow["id"], session_id="sess-1")
    send_view(wired, workflow["id"], session_id="sess-2", path="/about/team")
    body = wired.get(f"{PREFIX}/views", params={"room_id": ROOM}).json()
    assert body["count"] == 2
    assert body["matched"] == 1
    assert body["by_stopped_by"]["matched_below_repeat"] == 1
    assert body["by_stopped_by"]["rules_not_matched"] == 1


def test_the_views_route_over_http_can_be_filtered_by_workflow_and_visitor(wired):
    workflow = client_get_workflow(wired)
    send_view(wired, workflow["id"], visitor_key="visitor-1")
    send_view(wired, workflow["id"], visitor_key="visitor-2")
    assert (
        wired.get(f"{PREFIX}/views", params={"room_id": ROOM, "visitor_key": "visitor-1"}).json()[
            "count"
        ]
        == 1
    )
    assert (
        wired.get(
            f"{PREFIX}/views", params={"room_id": ROOM, "workflow_id": workflow["id"]}
        ).json()["count"]
        == 2
    )
    assert (
        wired.get(f"{PREFIX}/views", params={"room_id": ROOM, "visitor_key": "nobody"}).json()[
            "count"
        ]
        == 0
    )


def test_reading_one_view_over_http_returns_the_decision_snapshot(wired):
    workflow = client_get_workflow(wired)
    send_view(wired, workflow["id"], session_id="sess-1")
    row = wired.get(f"{PREFIX}/views", params={"room_id": ROOM}).json()["views"][0]
    read = wired.get(f"{PREFIX}/views/{row['id']}")
    assert read.status_code == 200, read.text
    body = read.json()
    assert body["stopped_by"] == "matched_below_repeat"
    assert body["counts"]["matching_visits"] == 1
    assert body["counts"]["visits_required"] == 2


def test_reading_an_absent_view_over_http_is_404(client):
    response = client.get(f"{PREFIX}/views/view_absent")
    assert response.status_code == 404
    assert response.json()["error"] == "unknown_page_view"


def test_an_out_of_range_views_limit_over_http_is_422(client):
    assert client.get(f"{PREFIX}/views", params={"limit": 5000}).status_code == 422


# --------------------------------------------------------------------------- #
# Deliveries over HTTP
# --------------------------------------------------------------------------- #


def test_the_deliveries_route_over_http_groups_by_state(wired):
    workflow = client_get_workflow(wired)
    qualify_over_http(wired, workflow["id"])
    body = wired.get(f"{PREFIX}/deliveries", params={"room_id": ROOM}).json()
    assert body["count"] == 1
    assert body["by_state"] == {"shown": 1}


def test_reading_one_delivery_over_http_carries_its_receipts(wired):
    workflow = client_get_workflow(wired)
    shown = qualify_over_http(wired, workflow["id"])
    delivery_id = shown["delivery"]["id"]
    wired.post(f"{PREFIX}/deliveries/{delivery_id}/interactions", json={"kind": "clicked"})
    body = wired.get(f"{PREFIX}/deliveries/{delivery_id}").json()
    assert body["receipt_count"] == 1
    assert body["receipt_kinds"] == ["clicked"]
    assert body["state"] == "interacted"


def test_the_delivery_state_over_http_is_derived_from_its_receipts(wired):
    workflow = client_get_workflow(wired)
    shown = qualify_over_http(wired, workflow["id"])
    delivery_id = shown["delivery"]["id"]
    wired.post(f"{PREFIX}/deliveries/{delivery_id}/interactions", json={"kind": "dismissed"})
    assert wired.get(f"{PREFIX}/deliveries/{delivery_id}").json()["state"] == "hidden_for_session"
    wired.post(
        f"{PREFIX}/deliveries/{delivery_id}/interactions",
        json={"kind": "path_selected", "path_key": "not_right_now"},
    )
    final = wired.get(f"{PREFIX}/deliveries/{delivery_id}").json()
    assert final["state"] == "engaged"
    assert final["session_hidden"] is True


def test_reading_an_absent_delivery_over_http_is_404(client):
    response = client.get(f"{PREFIX}/deliveries/del_absent")
    assert response.status_code == 404
    assert response.json()["error"] == "unknown_delivery"


# --------------------------------------------------------------------------- #
# Receipts over HTTP
# --------------------------------------------------------------------------- #


def test_a_receipt_over_http_records_the_published_content_stat_topic(wired):
    workflow = client_get_workflow(wired)
    shown = qualify_over_http(wired, workflow["id"])
    delivery_id = shown["delivery"]["id"]
    for kind, topic in (
        ("messenger_opened", "open"),
        ("clicked", "click"),
        ("dismissed", "receipt"),
        ("path_selected", "receipt"),
        ("goal_reached", "goal"),
    ):
        body: dict[str, str] = {"kind": kind}
        if kind == "path_selected":
            body["path_key"] = "yes_upgrade"
        response = wired.post(f"{PREFIX}/deliveries/{delivery_id}/interactions", json=body)
        assert response.status_code == 201, response.text
        assert response.json()["receipt"]["data"]["content_stat"] == topic, kind


def test_the_receipts_route_over_http_groups_by_kind_and_counts_engagement(wired):
    workflow = client_get_workflow(wired)
    shown = qualify_over_http(wired, workflow["id"])
    delivery_id = shown["delivery"]["id"]
    wired.post(f"{PREFIX}/deliveries/{delivery_id}/interactions", json={"kind": "clicked"})
    wired.post(
        f"{PREFIX}/deliveries/{delivery_id}/interactions",
        json={"kind": "path_selected", "path_key": "yes_upgrade"},
    )
    body = wired.get(f"{PREFIX}/receipts", params={"room_id": ROOM}).json()
    assert body["count"] == 2
    assert body["by_kind"] == {"clicked": 1, "path_selected": 1}
    assert body["engaged"] == 1
    assert body["hides_for_session"] == 0


def test_a_dismissal_over_http_is_counted_as_hiding_the_session(wired):
    workflow = client_get_workflow(wired)
    shown = qualify_over_http(wired, workflow["id"])
    wired.post(
        f"{PREFIX}/deliveries/{shown['delivery']['id']}/interactions",
        json={"kind": "dismissed"},
    )
    body = wired.get(f"{PREFIX}/receipts", params={"room_id": ROOM}).json()
    assert body["hides_for_session"] == 1


def test_a_receipt_on_an_unpublished_kind_over_http_is_422(wired):
    workflow = client_get_workflow(wired)
    shown = qualify_over_http(wired, workflow["id"])
    response = wired.post(
        f"{PREFIX}/deliveries/{shown['delivery']['id']}/interactions", json={"kind": "shrugged"}
    )
    assert response.status_code == 422
    assert response.json()["error"] == "invalid_interaction"


def test_a_receipt_on_an_absent_delivery_over_http_is_404(client):
    response = client.post(f"{PREFIX}/deliveries/del_absent/interactions", json={"kind": "clicked"})
    assert response.status_code == 404
    assert response.json()["error"] == "unknown_delivery"


def test_a_path_selection_naming_no_declared_branch_over_http_is_422(wired):
    workflow = client_get_workflow(wired)
    shown = qualify_over_http(wired, workflow["id"])
    response = wired.post(
        f"{PREFIX}/deliveries/{shown['delivery']['id']}/interactions",
        json={"kind": "path_selected", "path_key": "maybe"},
    )
    assert response.status_code == 422
    assert response.json()["error"] == "unknown_path"
    assert "yes_upgrade" in response.json()["detail"]


def test_the_receipts_route_over_http_can_be_filtered_by_delivery(wired):
    workflow = client_get_workflow(wired)
    shown = qualify_over_http(wired, workflow["id"])
    wired.post(
        f"{PREFIX}/deliveries/{shown['delivery']['id']}/interactions", json={"kind": "clicked"}
    )
    body = wired.get(
        f"{PREFIX}/receipts", params={"room_id": ROOM, "delivery_id": shown["delivery"]["id"]}
    ).json()
    assert body["count"] == 1
    assert (
        wired.get(
            f"{PREFIX}/receipts", params={"room_id": ROOM, "delivery_id": "del_absent"}
        ).json()["count"]
        == 0
    )


# --------------------------------------------------------------------------- #
# Prospects over HTTP
# --------------------------------------------------------------------------- #


def test_the_prospects_route_over_http_includes_a_buyer_who_never_qualified(wired):
    workflow = client_get_workflow(wired)
    send_view(wired, workflow["id"], visitor_key="quiet-one")
    body = wired.get(f"{PREFIX}/prospects", params={"room_id": ROOM}).json()
    assert body["count"] == 1
    assert body["shown_to"] == 0
    assert body["not_shown_to"] == 1
    assert body["prospects"][0]["visitor_key"] == "quiet-one"


def test_the_prospects_route_over_http_says_what_a_prospect_is(wired):
    body = wired.get(f"{PREFIX}/prospects", params={"room_id": ROOM}).json()
    assert "whether or not the block was shown" in body["reads"]
    assert body["as_of"]


# --------------------------------------------------------------------------- #
# The audit trail, over HTTP
# --------------------------------------------------------------------------- #


def test_every_audit_row_this_workflow_wrote_names_a_route_it_serves(wired, db):
    """The product guarantee is that the audit row names the route that served the write."""
    workflow = client_get_workflow(wired)
    shown = qualify_over_http(wired, workflow["id"])
    wired.post(
        f"{PREFIX}/deliveries/{shown['delivery']['id']}/interactions",
        json={"kind": "clicked"},
        params={"actor": "dana"},
    )
    rows = db.audit(limit=200)
    mine = [entry for entry in rows if PREFIX in str(entry.get("source") or "")]
    assert mine, "no audit row named this feature's prefix"

    # The source is already the full route, so it is split here rather than joined onto
    # PREFIX again, and it keeps the path placeholder. So the expected set is read from
    # the registry's own advertised paths, which is also what makes this a claim about
    # the mounted app rather than about a list written beside it.
    served = set()
    for entry in mine:
        method, _, tail = str(entry["source"]).partition(" ")
        assert tail.startswith(PREFIX), entry["source"]
        served.add((method, tail))

    advertised = {
        (method, route["path"])
        for route in host.REGISTRY.by_id(FEATURE_ID).routes
        for method in route["methods"]
        if method != "GET"
    }
    assert served <= advertised, served - advertised
    assert ("POST", f"{PREFIX}/views") in served
    assert ("POST", f"{PREFIX}/deliveries/{{delivery_id}}/interactions") in served


def client_get_workflow(client) -> dict:
    """The one workflow ``wired`` saved, read back over HTTP."""
    listed = client.get(f"{PREFIX}/workflows", params={"room_id": ROOM}).json()
    assert listed["count"] == 1, listed
    return listed["workflows"][0]


def test_the_demo_seed_produces_states_that_are_not_all_successes(db):
    """The seeder prints this string on a Windows console, so both claims are checked here."""
    module = load_feature(MODULE)
    db.create("room", {"name": "Northwind Traders"}, actor="seed", source="seed")
    room_id = db.list("room")[0]["id"]
    summary = module.seed(db, {"room_ids": [(room_id, "Northwind Traders")], "now": NOW})

    assert summary
    assert summary.encode("cp1252").decode("cp1252") == summary, summary
    assert "1 draft" in summary
    assert "engaged" in summary
    assert "dismissed" in summary
    assert "hidden for session" in summary
    assert "shown and nothing done" in summary
    assert "not qualified" in summary

    again = module.seed(db, {"room_ids": [(room_id, "Northwind Traders")], "now": NOW})
    assert "already seeded" in again


def test_the_demo_seed_writes_four_workflows_in_four_states(db):
    module = load_feature(MODULE)
    db.create("room", {"name": "Northwind Traders"}, actor="seed", source="seed")
    room_id = db.list("room")[0]["id"]
    module.seed(db, {"room_ids": [(room_id, "Northwind Traders")], "now": NOW})

    from dsr.page_outreach import PageOutreach
    from dsr.store import RecordStore

    outreach = PageOutreach(RecordStore(db))
    workflows = outreach.workflows(room_id=room_id)
    assert len(workflows) == 4
    assert len([entry for entry in workflows if entry["state"] == "draft"]) == 1
    assert len([entry for entry in workflows if entry["state"] == "live"]) == 3
    assert {str(entry["frequency"]) for entry in workflows} == {
        "seen",
        "any_interaction",
        "engaged_with",
    }

    body = outreach.summary(room_id=room_id)
    assert body["views"] >= 10
    assert body["deliveries"] >= 3
    assert body["receipts"] >= 3
    assert body["engaged_receipts"] >= 1
    assert body["hidden_for_session"] >= 1


def test_the_demo_seed_shows_a_block_to_a_buyer_who_never_qualified(db):
    """The not-qualified row is the one a seller most needs and the easiest to leave out."""
    from dsr.page_outreach import PageOutreach
    from dsr.store import RecordStore

    module = load_feature(MODULE)
    db.create("room", {"name": "Northwind Traders"}, actor="seed", source="seed")
    room_id = db.list("room")[0]["id"]
    module.seed(db, {"room_ids": [(room_id, "Northwind Traders")], "now": NOW})

    outreach = PageOutreach(RecordStore(db))
    body = outreach.prospects(room_id=room_id, now=NOW)
    assert body["not_shown_to"] >= 1
    quiet = [row for row in body["prospects"] if not row["deliveries"]]
    assert quiet, "the demo seeded no prospect who browsed and did not qualify"
    assert quiet[0]["matched_visits"] >= 1


def test_the_demo_seed_writes_only_through_the_audited_store(db):
    """Every seeded row has an audit row, because the seeder has no other write path."""
    module = load_feature(MODULE)
    db.create("room", {"name": "Northwind Traders"}, actor="seed", source="seed")
    room_id = db.list("room")[0]["id"]
    module.seed(db, {"room_ids": [(room_id, "Northwind Traders")], "now": NOW})

    collections = {
        entry["collection"]
        for entry in db.audit(limit=500)
        if str(entry["collection"] or "").startswith("page_outreach_")
    }
    assert collections == {
        "page_outreach_workflow",
        "page_outreach_view",
        "page_outreach_delivery",
        "page_outreach_receipt",
    }


def test_the_seed_returns_an_empty_string_when_there_is_no_room():
    """No room, nothing to attach rows to, and an empty string rather than a crash."""
    module = load_feature(MODULE)
    assert module.seed(None, {"room_ids": [], "now": NOW}) == ""


def test_every_demo_prospect_names_a_workflow_the_seed_creates():
    """The invariant that makes the seed's unknown-workflow guard unreachable.

    A demo row naming a workflow that was not saved would be skipped silently, so the
    seed would print fewer prospects than it declares. This asserts the naming instead
    of leaving the guard to hide a mistake.
    """
    module = load_feature(MODULE)
    saved = {str(entry["name"]) for entry in module._SEED_WORKFLOWS}
    named = {str(entry["workflow"]) for entry in module._SEED_PROSPECTS}
    assert named <= saved, sorted(named - saved)


def test_the_demo_seed_shows_the_block_to_a_buyer_who_does_nothing(db):
    """The commonest real case, and the only state no other demo row shows."""
    from dsr.page_outreach import PageOutreach
    from dsr.store import RecordStore

    module = load_feature(MODULE)
    db.create("room", {"name": "Northwind Traders"}, actor="seed", source="seed")
    room_id = db.list("room")[0]["id"]
    module.seed(db, {"room_ids": [(room_id, "Northwind Traders")], "now": NOW})

    outreach = PageOutreach(RecordStore(db))
    silent = [
        row
        for row in outreach.prospects(room_id=room_id, now=NOW)["prospects"]
        if row["deliveries"] and not row["engaged"] and not row["hidden_for_session"]
    ]
    assert silent, "the demo seeded no prospect who was shown the block and did nothing"


def test_the_demo_visit_times_are_inside_the_repeat_window(db):
    """A seeded view outside its own workflow's window would not qualify, and the demo
    would show fewer blocks than its own summary claims."""
    from dsr.page_outreach import PageOutreach
    from dsr.store import RecordStore

    module = load_feature(MODULE)
    db.create("room", {"name": "Northwind Traders"}, actor="seed", source="seed")
    room_id = db.list("room")[0]["id"]
    module.seed(db, {"room_ids": [(room_id, "Northwind Traders")], "now": NOW})

    outreach = PageOutreach(RecordStore(db))
    cutoff = NOW - timedelta(days=7)
    inside = [
        row
        for row in outreach.views(room_id=room_id)
        if datetime.fromisoformat(str(row["visited_at"])) >= cutoff
    ]
    assert len(inside) == len(outreach.views(room_id=room_id))
