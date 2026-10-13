"""WF-110: the HTTP surface, driven through the mounted routes.

These call the URLs the host actually serves rather than importing handlers and
calling them directly. The difference is load-bearing three times over:

* ``backend/dsr/api.py`` is not edited by this feature, so the router is mounted
  by discovery alone. If discovery failed, every test here would 404 and the
  domain tests would still pass, which is exactly the failure a feature-only test
  suite cannot see.
* The audit-source rule is checked against the **live route registry** the host
  reports, not against a list written by hand. A written list goes stale the moment
  a route is renamed, and then the check keeps passing while the audit log names a
  path the app stopped serving.
* The error statuses are asserted as HTTP statuses, through the one handler the
  feature registers.

``client`` comes from ``conftest.py``: one ``TestClient`` per module with the store
swapped per test, so these tests are isolated without paying for a lifespan each.
Every test builds its own rows, so none depends on another's.
"""

from __future__ import annotations

from typing import Any

from dsr.conversation_tickets import vocabulary as vocab
from fastapi.testclient import TestClient

PREFIX = "/api/wf-110"

WF110_COLLECTIONS = {
    "wf110_ticket",
    "wf110_tag",
    "wf110_sla_policy",
    "wf110_trigger",
    "wf110_evaluation",
    "wf110_notification",
    "wf110_webhook",
    "wf110_ticket_activity",
}


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


def make_conversation(client: TestClient, subject: str = "Availability") -> str:
    """One WF-107 conversation, written through the audited store.

    The conversation is WF-107's record, so this test writes it directly rather
    than depending on WF-107's routes. The source is ``test`` and the audit-source
    assertion below ignores it, because it is not a route call.
    """

    record = client.app.state.store.create(
        vocab.CONVERSATION_COLLECTION,
        {
            "state": vocab.CONVERSATION_OPEN,
            "origin": vocab.CONVERSATION_ORIGIN_API,
            "tags": [],
            "subject": subject,
        },
        actor="dana",
        source="test",
    )
    return str(record["id"])


def make_workspace(client: TestClient, *, notify_only: bool = False) -> str:
    """A tag, an SLA policy and one live escalation trigger. Returns the trigger id."""

    assert client.post(f"{PREFIX}/tags", json={"name": "priority customer"}).status_code == 201
    assert (
        client.post(
            f"{PREFIX}/sla-policies",
            json={"name": "Escalation response", "metric": "time_to_resolution", "target_hours": 8},
        ).status_code
        == 201
    )
    actions: list[dict[str, Any]] = [{"kind": "notify_team"}]
    if not notify_only:
        actions = [
            {"kind": "assign_team", "team": "escalations"},
            {"kind": "notify_team"},
            {"kind": "apply_sla", "policy": "Escalation response"},
            {"kind": "tag_conversation", "tag": "priority customer"},
        ]
    created = client.post(
        f"{PREFIX}/triggers",
        json={
            "name": "Escalate a created escalation ticket",
            "kind": "ticket_created",
            "channel": "#escalations",
            "conditions": {"ticket_types": ["escalation"]},
            "actions": actions,
        },
    )
    assert created.status_code == 201, created.text
    trigger_id = str(created.json()["id"])
    live = client.post(f"{PREFIX}/triggers/{trigger_id}/go-live")
    assert live.status_code == 200, live.text
    return trigger_id


# --------------------------------------------------------------------------- #
# Discovery: the router is mounted without editing api.py
# --------------------------------------------------------------------------- #


def test_the_router_is_mounted_by_discovery_alone(client: TestClient):
    entries = [
        entry
        for entry in client.get("/api/features").json()["features"]
        if entry["id"] == "wf-110-conversation-ticket"
    ]
    assert len(entries) == 1
    entry = entries[0]
    assert entry["ticket"] == "WF-110"
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


def test_every_advertised_route_answers_without_a_server_error(client: TestClient):
    """A route with no working handler behind it is the fault this guards."""

    for method, path in sorted(
        (method, route["path"])
        for route in served_routes(client)
        if route["path"].startswith(PREFIX)
        for method in route["methods"]
    ):
        url = (
            path.replace("{ticket_id}", "absent")
            .replace("{trigger_id}", "absent")
            .replace("{tag_name}", "absent")
            .replace("{decision_id}", "absent")
        )
        response = client.request(method, url, json={})
        assert response.status_code < 500, f"{method} {path} -> {response.status_code}"


# --------------------------------------------------------------------------- #
# Reference data
# --------------------------------------------------------------------------- #


def test_the_vocabulary_route_serves_the_researched_terms(client: TestClient):
    body = client.get(f"{PREFIX}/vocabulary").json()
    assert body["background_only"]["value"] is True
    assert body["tracker_exemption"]["value"] == "tracker"
    assert body["closed_payload_shape"]["value"] == "nested_under_ticket"
    assert set(body["trigger_kinds"]["value"]) == {"ticket_created", "ticket_state_changed"}


def test_the_inferences_route_serves_every_judgement_call(client: TestClient):
    body = client.get(f"{PREFIX}/inferences").json()
    assert body["count"] == len(body["inferences"])
    assert body["jev"]["audit_id"].startswith("jev-")
    assert any(e["id"] == "inference_second_workflow_is_shipped" for e in body["inferences"])


def test_one_decision_can_be_read_and_an_unknown_one_is_refused(client: TestClient):
    known = client.get(f"{PREFIX}/inferences").json()["ids"][0]
    assert client.get(f"{PREFIX}/decisions/{known}").status_code == 200
    missing = client.get(f"{PREFIX}/decisions/ghost")
    assert missing.status_code == 400
    assert missing.json()["errors"]["decision_id"] == "unknown"


def test_the_reads_answer_without_writes(client: TestClient):
    assert client.get(f"{PREFIX}/teams").status_code == 200
    assert client.get(f"{PREFIX}/summary").status_code == 200
    assert client.get(f"{PREFIX}/catalog").status_code == 200
    assert client.get(f"{PREFIX}/tags").status_code == 200
    assert client.get(f"{PREFIX}/sla-policies").status_code == 200
    assert client.get(f"{PREFIX}/triggers").status_code == 200
    assert client.get(f"{PREFIX}/tickets").status_code == 200
    assert client.get(f"{PREFIX}/notifications").status_code == 200
    assert client.get(f"{PREFIX}/webhooks").status_code == 200
    assert client.get(f"{PREFIX}/evaluations").status_code == 200


# --------------------------------------------------------------------------- #
# Tags and SLA policies
# --------------------------------------------------------------------------- #


def test_a_tag_can_be_registered_and_a_duplicate_is_refused(client: TestClient):
    assert client.post(f"{PREFIX}/tags", json={"name": "priority customer"}).status_code == 201
    again = client.post(f"{PREFIX}/tags", json={"name": "PRIORITY customer"})
    assert again.status_code == 409
    assert again.json()["error"] == "tag_name_already_used"


def test_a_tag_with_no_name_is_refused_with_the_field(client: TestClient):
    response = client.post(f"{PREFIX}/tags", json={})
    assert response.status_code == 400
    assert response.json()["errors"]["name"] == "required"


def test_an_sla_policy_can_be_declared_and_a_bad_target_is_refused(client: TestClient):
    assert (
        client.post(
            f"{PREFIX}/sla-policies", json={"name": "Escalation response", "target_hours": 8}
        ).status_code
        == 201
    )
    bad = client.post(f"{PREFIX}/sla-policies", json={"name": "Bad", "target_hours": 0})
    assert bad.status_code == 400
    assert bad.json()["errors"]["target_hours"] == "invalid"


# --------------------------------------------------------------------------- #
# Triggers
# --------------------------------------------------------------------------- #


def test_a_trigger_is_built_as_a_draft_and_can_go_live(client: TestClient):
    created = client.post(
        f"{PREFIX}/triggers",
        json={
            "name": "Escalate",
            "kind": "ticket_created",
            "conditions": {"ticket_types": ["escalation"]},
            "actions": [{"kind": "notify_team"}],
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["data"]["state"] == "draft"
    trigger_id = str(created.json()["id"])
    assert client.post(f"{PREFIX}/triggers/{trigger_id}/go-live").status_code == 200
    again = client.post(f"{PREFIX}/triggers/{trigger_id}/go-live")
    assert again.status_code == 409
    assert again.json()["error"] == "trigger_already_live"


def test_a_trigger_with_no_name_is_refused(client: TestClient):
    response = client.post(f"{PREFIX}/triggers", json={"actions": [{"kind": "notify_team"}]})
    assert response.status_code == 400
    assert response.json()["errors"]["name"] == "required"


def test_a_trigger_with_an_unknown_action_is_refused(client: TestClient):
    response = client.post(
        f"{PREFIX}/triggers", json={"name": "x", "actions": [{"kind": "explode"}]}
    )
    assert response.status_code == 400
    assert "actions[0].kind" in response.json()["errors"]


def test_a_trigger_can_be_read_edited_and_removed(client: TestClient):
    trigger_id = make_workspace(client, notify_only=True)
    assert client.get(f"{PREFIX}/triggers/{trigger_id}").status_code == 200
    edited = client.patch(
        f"{PREFIX}/triggers/{trigger_id}", json={"conditions": {"ticket_types": ["support"]}}
    )
    assert edited.status_code == 200
    assert edited.json()["data"]["conditions"]["ticket_types"] == ["support"]
    assert client.delete(f"{PREFIX}/triggers/{trigger_id}").status_code == 200
    assert client.get(f"{PREFIX}/triggers/{trigger_id}").status_code == 404


def test_an_unknown_trigger_is_not_found(client: TestClient):
    assert client.get(f"{PREFIX}/triggers/ghost").status_code == 404
    assert client.patch(f"{PREFIX}/triggers/ghost", json={}).status_code == 404
    assert client.post(f"{PREFIX}/triggers/ghost/go-live").status_code == 404


def test_listing_triggers_rejects_an_unknown_kind(client: TestClient):
    response = client.get(f"{PREFIX}/triggers?kind=nonsense")
    assert response.status_code == 400
    assert response.json()["errors"]["kind"] == "unknown"


# --------------------------------------------------------------------------- #
# Creating tickets: the researched flow
# --------------------------------------------------------------------------- #


def test_a_converted_ticket_runs_every_action(client: TestClient):
    make_workspace(client)
    conversation_id = make_conversation(client)
    response = client.post(
        f"{PREFIX}/tickets",
        json={
            "subject": "Platform availability for 400 seats",
            "ticket_type": "escalation",
            "conversation_id": conversation_id,
            "contact_email": "buyer@northwind.example",
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["ticket"]["converted"] is True
    assert body["ticket"]["owning_team"] == "escalations"
    assert [action["outcome"] for action in body["evaluations"][0]["data"]["actions"]] == [
        "ran"
    ] * 4
    assert len(body["notifications"]) == 1
    assert client.get(f"{PREFIX}/notifications").json()["count"] == 1


def test_a_tracker_ticket_is_created_and_the_trigger_is_skipped(client: TestClient):
    make_workspace(client)
    response = client.post(
        f"{PREFIX}/tickets",
        json={"subject": "Tracker", "ticket_type": "escalation", "created_via": "tracker"},
    )
    assert response.status_code == 201
    assert response.json()["evaluations"][0]["data"]["reason"] == "tracker_created_ticket"
    assert client.get(f"{PREFIX}/notifications").json()["count"] == 0


def test_an_unconverted_ticket_skips_the_gated_actions(client: TestClient):
    make_workspace(client)
    response = client.post(
        f"{PREFIX}/tickets",
        json={
            "subject": "Inbound",
            "ticket_type": "escalation",
            "created_via": "inbound",
            "team": "sales",
        },
    )
    assert response.status_code == 201
    outcomes = {
        a["kind"]: a["outcome"] for a in response.json()["evaluations"][0]["data"]["actions"]
    }
    assert outcomes["apply_sla"] == "skipped_not_converted"
    assert outcomes["tag_conversation"] == "skipped_not_converted"
    assert outcomes["notify_team"] == "ran"


def test_a_ticket_outside_the_trigger_types_fires_nothing(client: TestClient):
    make_workspace(client)
    response = client.post(
        f"{PREFIX}/tickets",
        json={"subject": "Billing", "ticket_type": "billing", "created_via": "api"},
    )
    assert response.status_code == 201
    assert response.json()["evaluations"][0]["data"]["reason"] == "ticket_type_not_matched"
    assert response.json()["notifications"] == []


def test_a_conversion_with_no_conversation_is_refused(client: TestClient):
    response = client.post(
        f"{PREFIX}/tickets",
        json={"subject": "x", "ticket_type": "escalation", "created_via": "conversation"},
    )
    assert response.status_code == 400
    assert response.json()["errors"]["conversation_id"]


def test_a_ticket_with_an_unknown_team_is_refused(client: TestClient):
    response = client.post(
        f"{PREFIX}/tickets",
        json={
            "subject": "x",
            "ticket_type": "escalation",
            "created_via": "api",
            "team": "marketing",
        },
    )
    assert response.status_code == 400
    assert response.json()["errors"]["team"] == "unknown"


def test_an_unknown_ticket_is_not_found(client: TestClient):
    assert client.get(f"{PREFIX}/tickets/ghost").status_code == 404


# --------------------------------------------------------------------------- #
# The state change, the two events, and the payload shapes
# --------------------------------------------------------------------------- #


def test_a_state_change_emits_resolved_and_notifies_the_team(client: TestClient):
    make_workspace(client)
    created = client.post(
        f"{PREFIX}/triggers",
        json={
            "name": "Tell the owner",
            "kind": "ticket_state_changed",
            "channel": "#escalations",
            "conditions": {"to_states": ["resolved"]},
            "actions": [{"kind": "notify_team"}],
        },
    )
    trigger_id = str(created.json()["id"])
    assert client.post(f"{PREFIX}/triggers/{trigger_id}/go-live").status_code == 200

    ticket = client.post(
        f"{PREFIX}/tickets",
        json={
            "subject": "Escalate",
            "ticket_type": "escalation",
            "created_via": "api",
            "team": "sales",
        },
    ).json()
    response = client.post(
        f"{PREFIX}/tickets/{ticket['ticket']['id']}/state", json={"state": "resolved"}
    )
    assert response.status_code == 200, response.text
    topics = [w["data"]["topic"] for w in response.json()["webhooks"]]
    assert topics == ["ticket.state.updated", "ticket.resolved"]
    assert response.json()["notifications"], "the state-changed trigger notified the owner"
    assert client.get(f"{PREFIX}/webhooks?topic=ticket.resolved").json()["count"] == 1


def test_closing_the_conversation_emits_the_nested_closed_payload(client: TestClient):
    make_workspace(client)
    conversation_id = make_conversation(client)
    ticket = client.post(
        f"{PREFIX}/tickets",
        json={"subject": "x", "ticket_type": "escalation", "conversation_id": conversation_id},
    ).json()
    response = client.post(f"{PREFIX}/tickets/{ticket['ticket']['id']}/close_conversation", json={})
    assert response.status_code == 200, response.text
    assert response.json()["webhooks"][0]["data"]["topic"] == "ticket.closed"
    assert response.json()["webhooks"][0]["data"]["shape"] == "nested_under_ticket"

    listed = client.get(f"{PREFIX}/webhooks?topic=ticket.closed").json()
    assert listed["count"] == 1
    # The one reader parsed the nested shape, so the ticket is not empty.
    assert listed["webhooks"][0]["parsed_ticket"]["id"] == ticket["ticket"]["id"]


def test_closing_the_conversation_of_an_unconverted_ticket_is_refused(client: TestClient):
    ticket = client.post(
        f"{PREFIX}/tickets",
        json={"subject": "x", "ticket_type": "escalation", "created_via": "api"},
    ).json()
    response = client.post(f"{PREFIX}/tickets/{ticket['ticket']['id']}/close_conversation", json={})
    assert response.status_code == 400


def test_listing_webhooks_rejects_an_unknown_topic(client: TestClient):
    response = client.get(f"{PREFIX}/webhooks?topic=nonsense")
    assert response.status_code == 400
    assert response.json()["errors"]["topic"] == "unknown"


# --------------------------------------------------------------------------- #
# Linking, tags, replies, type and search
# --------------------------------------------------------------------------- #


def test_a_conversation_can_be_linked_and_unlinked(client: TestClient):
    ticket = client.post(
        f"{PREFIX}/tickets",
        json={"subject": "x", "ticket_type": "escalation", "created_via": "api"},
    ).json()
    ticket_id = ticket["ticket"]["id"]
    conversation_id = make_conversation(client)
    linked = client.post(
        f"{PREFIX}/tickets/{ticket_id}/link_conversation", json={"conversation_id": conversation_id}
    )
    assert linked.status_code == 200
    assert linked.json()["ticket"]["converted"] is True
    again = client.post(
        f"{PREFIX}/tickets/{ticket_id}/link_conversation", json={"conversation_id": conversation_id}
    )
    assert again.status_code == 409
    unlinked = client.delete(f"{PREFIX}/tickets/{ticket_id}/unlink_conversation")
    assert unlinked.status_code == 200
    assert unlinked.json()["ticket"]["converted"] is False


def test_linking_an_absent_conversation_is_not_found(client: TestClient):
    ticket = client.post(
        f"{PREFIX}/tickets",
        json={"subject": "x", "ticket_type": "escalation", "created_via": "api"},
    ).json()
    response = client.post(
        f"{PREFIX}/tickets/{ticket['ticket']['id']}/link_conversation",
        json={"conversation_id": "ghost"},
    )
    assert response.status_code == 404


def test_a_ticket_can_be_tagged_and_untagged(client: TestClient):
    client.post(f"{PREFIX}/tags", json={"name": "priority customer"})
    ticket = client.post(
        f"{PREFIX}/tickets",
        json={"subject": "x", "ticket_type": "escalation", "created_via": "api"},
    ).json()
    ticket_id = ticket["ticket"]["id"]
    tagged = client.post(f"{PREFIX}/tickets/{ticket_id}/tags", json={"tag": "priority customer"})
    assert tagged.status_code == 200
    assert tagged.json()["ticket"]["tags"] == ["priority customer"]
    untagged = client.request("DELETE", f"{PREFIX}/tickets/{ticket_id}/tags/priority customer")
    assert untagged.status_code == 200
    conflict = client.request("DELETE", f"{PREFIX}/tickets/{ticket_id}/tags/priority customer")
    assert conflict.status_code == 409
    assert conflict.json()["error"] == "tag_not_on_ticket"


def test_a_ticket_can_be_replied_to_and_the_admin_webhook_fires(client: TestClient):
    ticket = client.post(
        f"{PREFIX}/tickets",
        json={"subject": "x", "ticket_type": "escalation", "created_via": "api"},
    ).json()
    response = client.post(
        f"{PREFIX}/tickets/{ticket['ticket']['id']}/reply",
        json={"body": "On it", "admin": "dana"},
    )
    assert response.status_code == 200
    assert response.json()["ticket"]["admin"] == "dana"
    assert response.json()["webhooks"][0]["data"]["topic"] == "ticket.admin.assigned"


def test_a_reply_without_a_body_is_refused(client: TestClient):
    ticket = client.post(
        f"{PREFIX}/tickets",
        json={"subject": "x", "ticket_type": "escalation", "created_via": "api"},
    ).json()
    response = client.post(f"{PREFIX}/tickets/{ticket['ticket']['id']}/reply", json={})
    assert response.status_code == 400


def test_change_type_needs_a_ticket_and_an_unknown_type_is_refused(client: TestClient):
    assert client.post(f"{PREFIX}/tickets/change_type", json={}).status_code == 400
    ticket = client.post(
        f"{PREFIX}/tickets",
        json={"subject": "x", "ticket_type": "escalation", "created_via": "api"},
    ).json()
    changed = client.post(
        f"{PREFIX}/tickets/change_type",
        json={"ticket_id": ticket["ticket"]["id"], "ticket_type": "billing"},
    )
    assert changed.status_code == 200
    assert changed.json()["ticket"]["ticket_type"] == "billing"
    bad = client.post(
        f"{PREFIX}/tickets/change_type",
        json={"ticket_id": ticket["ticket"]["id"], "ticket_type": "nonsense"},
    )
    assert bad.status_code == 400


def test_search_finds_a_ticket_by_subject(client: TestClient):
    client.post(
        f"{PREFIX}/tickets",
        json={
            "subject": "Platform availability",
            "ticket_type": "escalation",
            "created_via": "api",
        },
    )
    found = client.post(f"{PREFIX}/tickets/search", json={"query": "availability"})
    assert found.status_code == 200
    assert found.json()["count"] == 1
    none = client.post(f"{PREFIX}/tickets/search", json={"query": "nothing"})
    assert none.json()["count"] == 0


def test_the_ticket_activity_log_lists_the_events(client: TestClient):
    ticket = client.post(
        f"{PREFIX}/tickets",
        json={"subject": "x", "ticket_type": "escalation", "created_via": "api"},
    ).json()
    activity = client.get(f"{PREFIX}/tickets/{ticket['ticket']['id']}/activity")
    assert activity.status_code == 200
    kinds = {record["data"]["kind"] for record in activity.json()["activity"]}
    assert "Ticket created" in kinds


# --------------------------------------------------------------------------- #
# The audit-source rule, checked against the live route registry
# --------------------------------------------------------------------------- #


def test_every_audit_source_names_a_route_the_host_serves(client: TestClient):
    make_workspace(client)
    conversation_id = make_conversation(client)
    ticket = client.post(
        f"{PREFIX}/tickets",
        json={"subject": "x", "ticket_type": "escalation", "conversation_id": conversation_id},
    ).json()
    ticket_id = ticket["ticket"]["id"]
    client.post(f"{PREFIX}/tickets/{ticket_id}/state", json={"state": "resolved"})
    client.post(f"{PREFIX}/tickets/{ticket_id}/close_conversation", json={})
    client.post(f"{PREFIX}/tickets/{ticket_id}/reply", json={"body": "done"})

    routes = served_routes(client)
    audit = client.app.state.db.audit(limit=1000)
    sources = {
        str(row["source"])
        for row in audit
        if row["collection"] in WF110_COLLECTIONS and row["source"] and row["source"] != "test"
    }
    assert sources, "the flow wrote no audited rows"
    for source in sorted(sources):
        assert names_a_served_route(source, routes), source
