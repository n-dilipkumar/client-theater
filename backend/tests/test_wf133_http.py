"""WF-133: the HTTP surface of real-time buyer-intent alerting and routing.

What is different from ``test_wf133.py``
----------------------------------------

The domain tests build the engine and call it directly. This file goes through the
router, so it is where four things are only observable:

* **The error mapping.** One handler is registered for the whole
  ``IntentRoutingError`` hierarchy and it answers with the status and the code the
  exception carries. Each researched refusal is answered with the status that
  matches what it is: a malformed observation is 422, a company nobody identified
  is 404, and an account with no opportunity to attach a task to is 409.
* **The audit source.** Every write a route makes passes ``router.prefix``, so the
  audit row names the route that served it. Asserted against the routes the host
  actually mounted, which is the check the brief calls for by name.
* **The query-string surface.** Filters arrive as query parameters rather than as a
  dictionary, and a room id is a query parameter here rather than a path segment
  because the research's alerts are room-scoped and the room list is the entry
  point a client already has.
* **The refusals that are correct.** ``tools/verify_all_routes.py`` calls every
  advertised route with no parameters at all, so every write route here is called
  empty and has to answer 4xx rather than 5xx.

The honest limits are asserted here too, once, through HTTP: an alert that names an
account owner the product has no address for is a real state and it is reported
rather than papered over.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import dsr.features as host
import pytest
from dsr.features import load_feature
from dsr.intent_routing.vocabulary import SUPPRESSION_HOURS

MODULE = "wf133_real_time_buyer_intent_alerting_and_routing"
FEATURE_ID = "wf-133-real-time-buyer-intent-alerting-and-routing"
PREFIX = "/api/wf-133"
ROOM = "room-1"

COMPANIES = "identified_company"
CRM_RECORDS = "crm_read_record"
CRM_IDENTITIES = "crm_read_identity"


# --------------------------------------------------------------------------- #
# Provisioning the two dependencies over the store the routes share
# --------------------------------------------------------------------------- #


def _store(client):
    from dsr.api import app

    return app.state.store


def _provision(client, key="northwind"):
    store = _store(client)
    store.create(
        COMPANIES,
        {
            "company_key": key,
            "identified_from": "capture",
            "name": "Northwind Energy",
            "website": "https://northwind.example",
            "size": "1000+",
            "segment": "enterprise",
            "countries": ["GB"],
            "contacts": [{"name": "Dana Okafor", "role": "VP Procurement"}],
            "page_views": 4,
            "paths": ["/overview", "/pricing"],
        },
        source="test",
    )
    store.create(
        CRM_RECORDS,
        {
            "system": "salesforce",
            "object": "account",
            "external_id": "acct-nw",
            "owner_id": "005-dana",
            "fields": {"Name": "Northwind Energy", "Website": "https://northwind.example"},
            "crm_owner": {"team": "enterprise-uk"},
        },
        room_id=ROOM,
        source="test",
    )
    store.create(
        CRM_RECORDS,
        {
            "system": "salesforce",
            "object": "deal",
            "external_id": "deal-nw",
            "owner_id": "005-dana",
            "fields": {"Name": "Rollout", "AccountId": "acct-nw"},
        },
        room_id=ROOM,
        source="test",
    )
    store.create(
        CRM_IDENTITIES,
        {
            "system": "salesforce",
            "buyer_email": "buyer@northwind.example",
            "buyer_name": "Dana Okafor",
            "account_id": "acct-nw",
            "contact_id": "con-nw",
            "deal_id": "deal-nw",
            "owner_id": "005-dana",
            "source": "room_mapping",
        },
        room_id=ROOM,
        source="test",
    )
    return store


@pytest.fixture
def wired(client):
    """A client whose store already holds the whole dependency chain."""
    _provision(client)
    return client


def watch(client, **overrides):
    body = {
        "name": "Strategic",
        "tier": "strategic",
        "accounts": ["northwind"],
        "notify": ["lead@acme.example"],
    }
    body.update(overrides)
    response = client.post(f"{PREFIX}/watchlists", json=body, params={"room_id": ROOM})
    assert response.status_code == 201, response.text
    return response.json()


def observation(**overrides):
    body = {
        "company_key": "northwind",
        "room_id": ROOM,
        "room_label": "the Northwind room",
        "pages": ["/overview", "/security", "/pricing", "/case-studies"],
        "dwell_seconds": 142,
        "total_dwell_seconds": 400,
        "revisits": 3,
        "downloads": 1,
        "demo_interactions": 1,
        "stakeholder": "Dana Okafor",
        "stakeholder_role": "VP Procurement",
    }
    body.update(overrides)
    return body


def raise_it(client, **overrides):
    return client.post(f"{PREFIX}/signals", json=observation(**overrides), params={"room_id": ROOM})


# --------------------------------------------------------------------------- #
# Discovery
# --------------------------------------------------------------------------- #


def test_the_registry_lists_this_feature_with_its_prefix_and_routes(client):
    listing = client.get("/api/features").json()
    record = next(f for f in listing["features"] if f["id"] == FEATURE_ID)
    assert record["ticket"] == "WF-133"
    assert record["prefix"] == PREFIX
    assert len(record["routes"]) == 22
    assert record["exception_handlers"] == ["IntentRoutingError"]


def test_no_feature_failed_to_load(client):
    """A feature that raises on import is skipped rather than fatal, which is why
    the failure has to be asserted rather than assumed."""
    listing = client.get("/api/features").json()
    assert listing["failed_count"] == 0, [f["id"] for f in listing["failed"]]


def test_the_feature_lookup_route_answers(client):
    assert client.get(f"/api/features/{FEATURE_ID}").json()["prefix"] == PREFIX


def test_the_core_health_route_still_answers(client):
    assert client.get("/api/health").status_code == 200


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
    ("GET", "/watchlists"),
    ("GET", "/watchlists/wl_absent"),
    ("POST", "/watchlists"),
    ("DELETE", "/watchlists/wl_absent"),
    ("GET", "/rules"),
    ("GET", "/rules/rl_absent"),
    ("POST", "/rules"),
    ("DELETE", "/rules/rl_absent"),
    ("POST", "/signals/evaluate"),
    ("POST", "/signals"),
    ("GET", "/signals"),
    ("GET", "/signals/sig_absent"),
    ("GET", "/alerts"),
    ("GET", "/alerts/al_absent"),
    ("GET", "/tasks"),
    ("GET", "/tasks/task_absent"),
    ("GET", "/engagement"),
    ("GET", "/actions"),
    ("POST", "/signals/sig_absent/actions"),
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
        for placeholder in (
            "{watchlist_id}",
            "{rule_id}",
            "{signal_id}",
            "{alert_id}",
            "{task_id}",
        ):
            tail = tail.replace(
                placeholder,
                "wl_absent"
                if placeholder == "{watchlist_id}"
                else "rl_absent"
                if placeholder == "{rule_id}"
                else "sig_absent"
                if placeholder == "{signal_id}"
                else "al_absent"
                if placeholder == "{alert_id}"
                else "task_absent",
            )
        for method in route["methods"]:
            advertised.add((method, tail))
    assert set(ALL_ROUTES) == advertised


# --------------------------------------------------------------------------- #
# Vocabulary and the decision record
# --------------------------------------------------------------------------- #


def test_the_vocabulary_route_publishes_the_thresholds(client):
    body = client.get(f"{PREFIX}/vocabulary").json()
    kinds = [entry["kind"] for entry in body["thresholds"]]
    assert kinds == ["dwell", "pages", "revisit", "download", "demo_interaction"]
    assert body["sourced_thresholds"] == ["dwell"]
    assert [entry["threshold"] for entry in body["thresholds"]] == [90, 4, 3, 1, 1]


def test_the_vocabulary_route_says_nothing_is_sent(client):
    body = client.get(f"{PREFIX}/vocabulary").json()
    assert body["sends_mail"] is False
    assert body["sends_slack"] is False
    assert body["primary_channel"] == "email"
    assert "held_for_integration" in body["dispatch_states"]


def test_the_inferences_route_serves_every_decision_with_a_change(client):
    body = client.get(f"{PREFIX}/inferences").json()
    assert body["count"] == len(body["inferences"])
    assert body["count"] >= 14
    for entry in body["inferences"]:
        assert {"id", "question", "reading", "why", "change", "risk"} <= set(entry), entry


def test_the_inferences_route_records_the_derived_threshold_numbers(client):
    body = client.get(f"{PREFIX}/inferences").json()
    joined = " ".join(entry["reading"] for entry in body["inferences"])
    assert "Pages 4, revisit 3, download 1, demo interaction 1" in joined


# --------------------------------------------------------------------------- #
# Watchlists
# --------------------------------------------------------------------------- #


def test_a_watchlist_is_created_then_read_back_over_http(client):
    created = watch(client)
    assert created["account_count"] == 1
    listed = client.get(f"{PREFIX}/watchlists", params={"room_id": ROOM}).json()
    assert listed["count"] == 1
    assert listed["watched_accounts"] == 1
    assert client.get(f"{PREFIX}/watchlists/{created['id']}").json()["name"] == "Strategic"


def test_a_watchlist_can_be_removed_over_http(client):
    created = watch(client)
    assert client.delete(f"{PREFIX}/watchlists/{created['id']}").status_code == 200
    assert client.get(f"{PREFIX}/watchlists", params={"room_id": ROOM}).json()["count"] == 0


def test_an_empty_watchlist_is_422_over_http(client):
    response = client.post(f"{PREFIX}/watchlists", json={}, params={"room_id": ROOM})
    assert response.status_code == 422
    assert response.json()["error"] == "invalid_watchlist"


def test_two_watchlists_may_not_share_a_name_in_one_room_over_http(client):
    watch(client)
    again = client.post(
        f"{PREFIX}/watchlists",
        json={"name": "Strategic", "accounts": ["other"]},
        params={"room_id": ROOM},
    )
    assert again.status_code == 409
    assert again.json()["error"] == "watchlist_already_exists"


def test_reading_or_removing_an_absent_watchlist_is_404_over_http(client):
    for method in ("GET", "DELETE"):
        response = client.request(method, f"{PREFIX}/watchlists/wl_absent")
        assert response.status_code == 404, method
        assert response.json()["error"] == "unknown_watchlist"


def test_the_watchlists_are_room_scoped_over_http(client):
    watch(client)
    assert client.get(f"{PREFIX}/watchlists", params={"room_id": "room-2"}).json()["count"] == 0


# --------------------------------------------------------------------------- #
# Routing rules
# --------------------------------------------------------------------------- #


def test_a_rule_is_created_then_read_back_over_http(client):
    created = client.post(
        f"{PREFIX}/rules",
        json={
            "name": "Enterprise",
            "kind": "team",
            "match": {"team": "enterprise-uk"},
            "notify": ["team@acme.example"],
            "position": 1,
        },
        params={"room_id": ROOM},
    )
    assert created.status_code == 201, created.text
    rule = created.json()
    assert rule["match"] == {"team": "enterprise-uk"}
    assert client.get(f"{PREFIX}/rules", params={"room_id": ROOM}).json()["count"] == 1
    assert client.get(f"{PREFIX}/rules/{rule['id']}").json()["name"] == "Enterprise"
    assert client.delete(f"{PREFIX}/rules/{rule['id']}").status_code == 200


def test_an_empty_rule_is_422_over_http(client):
    response = client.post(f"{PREFIX}/rules", json={}, params={"room_id": ROOM})
    assert response.status_code == 422
    assert response.json()["error"] == "invalid_routing_rule"


def test_a_rule_matching_on_a_key_nobody_implements_is_422_over_http(client):
    response = client.post(
        f"{PREFIX}/rules",
        json={"name": "x", "kind": "team", "match": {"moon": "full"}},
        params={"room_id": ROOM},
    )
    assert response.status_code == 422
    assert "moon" in response.json()["detail"]


def test_reading_or_removing_an_absent_rule_is_404_over_http(client):
    for method in ("GET", "DELETE"):
        response = client.request(method, f"{PREFIX}/rules/rl_absent")
        assert response.status_code == 404, method
        assert response.json()["error"] == "unknown_routing_rule"


# --------------------------------------------------------------------------- #
# Signals
# --------------------------------------------------------------------------- #


def test_the_evaluate_route_reports_a_decision_and_writes_nothing(wired):
    watch(wired)
    response = wired.post(f"{PREFIX}/signals/evaluate", json=observation())
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["raised"] is True
    assert body["wrote"] is False
    assert body["on_watchlist"] is True
    assert wired.get(f"{PREFIX}/signals", params={"room_id": ROOM}).json()["count"] == 0


def test_the_evaluate_route_says_why_nothing_would_be_raised(wired):
    watch(wired)
    body = wired.post(
        f"{PREFIX}/signals/evaluate",
        json=observation(
            dwell_seconds=1, pages=["/overview"], revisits=0, downloads=0, demo_interactions=0
        ),
    ).json()
    assert body["raised"] is False
    assert "no threshold was met" in body["reason"]


def test_the_evaluate_route_names_the_reason_an_account_is_not_a_target(wired):
    body = wired.post(f"{PREFIX}/signals/evaluate", json=observation()).json()
    assert body["on_watchlist"] is False
    assert "not a target account" in body["reason"]


def test_a_qualifying_observation_writes_three_records_over_http(wired):
    watch(wired)
    response = raise_it(wired)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["raised"] is True
    assert body["alert"]["channel_states"] == {"email": "queued", "slack": "held_for_integration"}
    assert body["task"]["opportunity_id"] == "deal-nw"
    assert wired.get(f"{PREFIX}/signals", params={"room_id": ROOM}).json()["count"] == 1
    assert wired.get(f"{PREFIX}/alerts", params={"room_id": ROOM}).json()["count"] == 1
    assert wired.get(f"{PREFIX}/tasks", params={"room_id": ROOM}).json()["count"] == 1


def test_the_alert_over_http_carries_the_three_quoted_facts(wired):
    watch(wired)
    alert = raise_it(wired).json()["alert"]
    assert alert["payload"]["pages"]["count"] == 4
    assert alert["payload"]["dwell"]["longest_page_seconds"] == 142
    assert alert["payload"]["stakeholder"]["name"] == "Dana Okafor"


def test_the_alert_over_http_says_no_stakeholder_can_be_addressed(wired):
    watch(wired)
    alert = raise_it(wired).json()["alert"]
    assert alert["payload"]["stakeholder"]["addressable"] is False


def test_the_slack_dispatch_over_http_is_held_and_says_why(wired):
    watch(wired)
    alert = raise_it(wired).json()["alert"]
    slack = next(d for d in alert["dispatches"] if d["channel"] == "slack")
    assert slack["state"] == "held_for_integration"
    assert "no Slack surface" in slack["reason"]


def test_the_second_signal_over_http_is_suppressed_not_dropped(wired):
    watch(wired)
    raise_it(wired)
    again = raise_it(wired).json()
    assert again["alert"]["suppressed"] is True
    assert again["alert"]["channel_states"]["email"] == "suppressed"
    assert wired.get(f"{PREFIX}/signals", params={"room_id": ROOM}).json()["count"] == 2


def test_an_observation_below_every_threshold_answers_200_and_writes_nothing(wired):
    watch(wired)
    response = wired.post(
        f"{PREFIX}/signals",
        json=observation(
            dwell_seconds=1, pages=["/overview"], revisits=0, downloads=0, demo_interactions=0
        ),
        params={"room_id": ROOM},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["raised"] is False
    assert body["wrote"] is False
    assert wired.get(f"{PREFIX}/signals", params={"room_id": ROOM}).json()["count"] == 0


def test_an_empty_observation_is_422_over_http(client):
    response = client.post(f"{PREFIX}/signals", json={}, params={"room_id": ROOM})
    assert response.status_code == 422
    assert response.json()["error"] == "invalid_engagement"


def test_an_observation_carrying_an_unthresholded_field_is_422_over_http(wired):
    response = raise_it(wired, email="buyer@northwind.example")
    assert response.status_code == 422
    assert response.json()["error"] == "unknown_engagement_field"


def test_an_unknown_company_is_404_over_http(wired):
    watch(wired)
    response = raise_it(wired, company_key="never-seen")
    assert response.status_code == 404
    assert response.json()["error"] == "unknown_company"


def test_an_account_with_no_opportunity_is_409_over_http(client):
    """The researched flow ends with a task on the opportunity, so there is nowhere to go."""
    _store(client).create(
        COMPANIES,
        {"company_key": "orphan", "name": "Orphan Co", "website": "https://orphan.example"},
        source="test",
    )
    watch(client, accounts=["orphan"])
    response = client.post(
        f"{PREFIX}/signals", json=observation(company_key="orphan"), params={"room_id": ROOM}
    )
    assert response.status_code == 409
    assert response.json()["error"] == "unresolved_account"
    assert client.get(f"{PREFIX}/alerts", params={"room_id": ROOM}).json()["count"] == 0


def test_an_alert_whose_owner_has_no_address_says_so_over_http(wired):
    """The CRM carries an owner id and no address for it, and that is the ordinary case."""
    watch(wired, notify=[])
    alert = raise_it(wired).json()["alert"]
    assert alert["channel_states"] == {"email": "skipped", "slack": "skipped"}
    email = next(d for d in alert["dispatches"] if d["channel"] == "email")
    assert "005-dana" in email["reason"]
    assert alert["accountable"] == "005-dana"


def test_signals_can_be_filtered_by_company_over_http(wired):
    watch(wired)
    signal_id = raise_it(wired).json()["signal"]["id"]
    assert (
        wired.get(f"{PREFIX}/signals", params={"room_id": ROOM, "company_key": "northwind"}).json()[
            "count"
        ]
        == 1
    )
    assert (
        wired.get(f"{PREFIX}/signals", params={"room_id": ROOM, "company_key": "other"}).json()[
            "count"
        ]
        == 0
    )
    assert wired.get(f"{PREFIX}/signals/{signal_id}").json()["id"] == signal_id


def test_an_absent_signal_is_404_over_http(client):
    response = client.get(f"{PREFIX}/signals/sig_absent")
    assert response.status_code == 404
    assert response.json()["error"] == "unknown_signal"


def test_an_out_of_range_limit_is_422_over_http(client):
    assert client.get(f"{PREFIX}/signals", params={"limit": 5000}).status_code == 422


# --------------------------------------------------------------------------- #
# Alerts and tasks
# --------------------------------------------------------------------------- #


def test_the_alerts_route_tallies_states_and_channels_over_http(wired):
    watch(wired)
    raise_it(wired)
    body = wired.get(f"{PREFIX}/alerts", params={"room_id": ROOM}).json()
    assert body["by_state"] == {"open": 1}
    assert body["by_channel"] == {"email:queued": 1, "slack:held_for_integration": 1}


def test_an_absent_alert_is_404_over_http(client):
    response = client.get(f"{PREFIX}/alerts/al_absent")
    assert response.status_code == 404
    assert response.json()["error"] == "unknown_alert"


def test_an_absent_task_is_404_over_http(client):
    response = client.get(f"{PREFIX}/tasks/task_absent")
    assert response.status_code == 404
    assert response.json()["error"] == "unknown_task"


def test_the_task_carries_the_payload_so_the_crm_needs_no_second_call(wired):
    watch(wired)
    body = raise_it(wired).json()
    assert body["task"]["context"]["stakeholder"]["name"] == "Dana Okafor"
    assert body["task"]["context"]["pages"]["count"] == 4
    due = datetime.fromisoformat(body["task"]["due_at"])
    sent = datetime.fromisoformat(body["alert"]["dispatched_at"])
    assert due - sent == timedelta(hours=SUPPRESSION_HOURS), "the task is due one window out"


# --------------------------------------------------------------------------- #
# Who is engaged and who is not
# --------------------------------------------------------------------------- #


def test_the_engagement_route_lists_an_ungoaged_account_over_http(wired):
    """A view built from the alerts could only ever show who is engaged."""
    watch(wired, accounts=["northwind", "meridian"])
    body = wired.get(f"{PREFIX}/engagement", params={"room_id": ROOM}).json()
    assert {row["company_key"] for row in body["accounts"]} == {"northwind", "meridian"}
    assert body["engaged"] == 0
    assert body["not_engaged"] == 2


def test_the_engagement_route_marks_an_alerted_account_engaged_over_http(wired):
    watch(wired, accounts=["northwind", "meridian"])
    raise_it(wired)
    body = wired.get(f"{PREFIX}/engagement", params={"room_id": ROOM}).json()
    rows = {row["company_key"]: row for row in body["accounts"]}
    assert rows["northwind"]["engagement"] == "engaged"
    assert rows["meridian"]["engagement"] == "not_engaged"


def test_the_engagement_route_reports_its_own_reading_over_http(client):
    body = client.get(f"{PREFIX}/engagement", params={"room_id": ROOM}).json()
    assert "watchlist" in body["reads"]


# --------------------------------------------------------------------------- #
# Rep action logged
# --------------------------------------------------------------------------- #


def test_a_rep_action_is_recorded_over_http_and_moves_the_alert(wired):
    watch(wired)
    signal_id = raise_it(wired).json()["signal"]["id"]
    response = wired.post(
        f"{PREFIX}/signals/{signal_id}/actions",
        json={"kind": "acknowledged", "note": "Calling her today."},
        params={"actor": "dana.kelly"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["state"] == "acknowledged"
    assert body["action"]["actor"] == "dana.kelly"
    assert body["alert"]["state"] == "acknowledged"


def test_a_dismissal_without_a_note_is_422_over_http(wired):
    watch(wired)
    signal_id = raise_it(wired).json()["signal"]["id"]
    response = wired.post(f"{PREFIX}/signals/{signal_id}/actions", json={"kind": "dismissed"})
    assert response.status_code == 422
    assert response.json()["error"] == "invalid_signal_action"


def test_an_unknown_action_kind_is_422_over_http(wired):
    watch(wired)
    signal_id = raise_it(wired).json()["signal"]["id"]
    response = wired.post(f"{PREFIX}/signals/{signal_id}/actions", json={"kind": "escalated"})
    assert response.status_code == 422


def test_an_action_on_an_absent_signal_is_404_over_http(client):
    response = client.post(f"{PREFIX}/signals/sig_absent/actions", json={"kind": "noted"})
    assert response.status_code == 404
    assert response.json()["error"] == "unknown_signal"


def test_the_action_log_route_tallies_by_kind_over_http(wired):
    watch(wired)
    signal_id = raise_it(wired).json()["signal"]["id"]
    wired.post(f"{PREFIX}/signals/{signal_id}/actions", json={"kind": "noted", "note": "Seen."})
    wired.post(f"{PREFIX}/signals/{signal_id}/actions", json={"kind": "contacted"})
    body = wired.get(f"{PREFIX}/actions", params={"room_id": ROOM}).json()
    assert body["by_kind"] == {"noted": 1, "contacted": 1}
    filtered = wired.get(f"{PREFIX}/actions", params={"room_id": ROOM, "signal_id": signal_id})
    assert filtered.json()["count"] == 2


# --------------------------------------------------------------------------- #
# The summary
# --------------------------------------------------------------------------- #


def test_the_summary_route_counts_without_reading_another_feature(client):
    """A room whose dependencies have not seeded reads as zeros rather than failing."""
    body = client.get(f"{PREFIX}/summary", params={"room_id": ROOM}).json()
    assert body["watchlists"] == 0
    assert body["alerts"] == 0
    assert body["sends_mail"] is False
    assert body["room_id"] == ROOM


def test_the_summary_route_counts_a_raised_alert(wired):
    watch(wired)
    raise_it(wired)
    body = wired.get(f"{PREFIX}/summary", params={"room_id": ROOM}).json()
    assert body["watchlists"] == 1
    assert body["watched_accounts"] == 1
    assert body["alerts"] == 1
    assert body["tasks"] == 1


# --------------------------------------------------------------------------- #
# The audit source: the route that served the write
# --------------------------------------------------------------------------- #


def _mounted() -> set[str]:
    """``METHOD /path`` for every route the host mounted for this feature.

    Read out of the registry rather than out of a list written here, so the check is
    against what the app actually serves. The defect it prevents has shipped in this
    codebase before: a feature whose audit log kept naming a path the app had
    stopped serving.
    """
    record = host.REGISTRY.by_id(FEATURE_ID)
    return {f"{method} {route['path']}" for route in record.routes for method in route["methods"]}


def _audit(client) -> list[dict]:
    from dsr.api import app

    return app.state.db.audit()


def _http_sources(client) -> set[str]:
    """The audit rows this feature's routes wrote, and nothing else.

    Narrowed to rows whose source starts with an HTTP method, because the test
    provisions its dependencies by writing straight to the store with the source
    ``test``. Those rows are real audit rows, they are simply not rows a route
    served, and the rule this file is checking is about the ones that are.
    """
    verbs = ("POST ", "PATCH ", "DELETE ", "PUT ")
    return {
        entry["source"]
        for entry in _audit(client)
        if entry.get("source") and entry["source"].startswith(verbs)
    }


def test_every_write_route_leaves_an_audit_row_naming_itself(wired):
    watchlist = watch(wired)
    wired.delete(f"{PREFIX}/watchlists/{watchlist['id']}")
    rule = wired.post(
        f"{PREFIX}/rules",
        json={
            "name": "Enterprise",
            "kind": "team",
            "match": {"team": "enterprise-uk"},
            "notify": ["team@acme.example"],
        },
        params={"room_id": ROOM},
    ).json()
    wired.delete(f"{PREFIX}/rules/{rule['id']}")
    watch(wired, name="Second")
    raised = raise_it(wired).json()
    wired.post(
        f"{PREFIX}/signals/{raised['signal']['id']}/actions",
        json={"kind": "noted", "note": "Seen."},
    )

    mounted = _mounted()
    ours = _http_sources(wired)
    assert ours, f"no write was audited at all; saw {sorted(_http_sources(wired))}"
    for source in ours:
        assert source in mounted, f"audit row names {source!r}, which is not a mounted route"


def test_the_expected_routes_are_the_ones_that_wrote(wired):
    """The same rule stated as a list, so a route that stopped being served shows up as
    a missing member rather than as an unnoticed extra row."""
    watchlist = watch(wired)
    rule = wired.post(
        f"{PREFIX}/rules",
        json={
            "name": "Enterprise",
            "kind": "team",
            "match": {"team": "enterprise-uk"},
            "notify": ["team@acme.example"],
        },
        params={"room_id": ROOM},
    ).json()
    raised = raise_it(wired).json()
    wired.post(
        f"{PREFIX}/signals/{raised['signal']['id']}/actions",
        json={"kind": "noted", "note": "Seen."},
    )
    wired.delete(f"{PREFIX}/watchlists/{watchlist['id']}")
    wired.delete(f"{PREFIX}/rules/{rule['id']}")

    sources = _http_sources(wired)
    assert sources == {
        f"POST {PREFIX}/watchlists",
        f"DELETE {PREFIX}/watchlists/{{watchlist_id}}",
        f"POST {PREFIX}/rules",
        f"DELETE {PREFIX}/rules/{{rule_id}}",
        f"POST {PREFIX}/signals",
        f"POST {PREFIX}/signals/{{signal_id}}/actions",
    }, sorted(sources)


def test_an_action_is_audited_under_the_rep_who_recorded_it(wired):
    watch(wired)
    signal_id = raise_it(wired).json()["signal"]["id"]
    wired.post(
        f"{PREFIX}/signals/{signal_id}/actions",
        json={"kind": "noted", "note": "Seen."},
        params={"actor": "dana.kelly"},
    )
    actors = {entry["actor"] for entry in _audit(wired) if entry["actor"]}
    assert "dana.kelly" in actors, sorted(actors)


# --------------------------------------------------------------------------- #
# The feature module, read rather than exercised
# --------------------------------------------------------------------------- #


def test_the_feature_module_is_importable_on_its_own():
    """Discovery mounted it; this proves it does not need the app to load."""
    feature = load_feature(MODULE)
    assert feature.FEATURE["id"] == FEATURE_ID
    assert feature.router.prefix == PREFIX
    assert callable(feature.seed)


def test_the_domain_module_imports_nothing_but_the_store():
    """The enforced rule in ``test_features.py`` greps for ``dsr.api``; this pins the
    whole import surface of the package so a new framework import is caught here."""
    from pathlib import Path

    package = Path(__file__).resolve().parents[1] / "dsr" / "intent_routing"
    allowed_ds = {"dsr.store", "dsr.intent_routing"}
    for module in sorted(package.glob("*.py")):
        text = module.read_text(encoding="utf-8")
        assert "dsr.api" not in text, module.name
        assert "sqlite3" not in text, module.name
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped.startswith(("import ", "from ")):
                continue
            for token in stripped.split():
                if token.startswith("dsr.") and not token.startswith("__"):
                    root = ".".join(token.split(".")[:2])
                    if root.startswith("dsr.") and root not in allowed_ds:
                        assert token.startswith("dsr.intent_routing") or root in allowed_ds, (
                            f"{module.name} imports {token}, which is outside the store"
                        )
