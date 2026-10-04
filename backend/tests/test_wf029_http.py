"""The HTTP surface and the audit-source rule for WF-029.

Split from ``test029.py``, which tests the researched rules against the domain. This
file drives the feature's own mounted router, so it covers the wiring and, at the
end, hard rule 4 of the build brief: **the audit row must name the route that actually
served the write**.

The audit-source tests are the ones worth reading twice. The contract names the
defect by name - "a feature's audit log kept recording a path the app had stopped
serving" - and it is invisible to a test scoped to one feature's own strings, so
these tests read the sources out of the module and out of the published OpenAPI
schema rather than asserting a hand-written list that would be updated in the same
commit as the bug.

Isolation: every test gets a fresh on-disk database through ``monkeypatch``, so the
file passes on its own and under ``pytest-xdist`` in any order. Nothing here reads a
file, reads an environment variable set by another test, or depends on a module-level
store.
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path

import pytest
from dsr.db.audited import AuditedDatabase
from dsr.features import load_feature
from fastapi.testclient import TestClient

PREFIX = "/api/wf-029"
MODULE = "wf029_score_dsr_activity_as_crm_lead_score_cr"
FEATURE_ID = "wf-029-score-dsr-activity-as-crm-lead-score"

REQUIRED_SCOPES = ["crm.objects.contacts.read", "crm.objects.contacts.write"]


@pytest.fixture()
def client(monkeypatch):
    tmp = tempfile.TemporaryDirectory()
    monkeypatch.setenv("DSR_DB_PATH", str(Path(tmp.name) / "wf029http.db"))
    monkeypatch.setenv("DSR_AUDIT_DIR", str(Path(tmp.name) / "audit"))
    monkeypatch.setattr("dsr.api.FRONTEND_DIST", Path(tmp.name) / "absent-frontend")
    from dsr.api import app

    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        tmp.cleanup()


def make_room(client, name="Northwind") -> dict:
    return client.post("/api/records/room", json={"name": name, "account": name}).json()


def register(client, room_id=None, **spec):
    body = {"vendor": "hubspot", "scopes": list(REQUIRED_SCOPES), **spec}
    if room_id is not None:
        body["deal_connections"] = {room_id: {"deal_id": "deal_1"}}
    response = client.post(f"{PREFIX}/integrations", json=body)
    assert response.status_code == 201, response.text
    return response.json()["integration"]


def provision(client, *families):
    for family in families:
        response = client.post(
            f"{PREFIX}/properties", json={"family": family, "name": f"dsr_{family}"}
        )
        assert response.status_code == 201, response.text


def make_criterion(client, **spec):
    body = {"family": "downloads", "bucket": "positive", "score": 20, **spec}
    response = client.post(f"{PREFIX}/criteria", json=body)
    assert response.status_code == 201, response.text
    return response.json()["criterion"]


def send(client, room_id, **payload):
    response = client.post(f"{PREFIX}/rooms/{room_id}/activity", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def armed(client, room_id, properties=("views", "clicks", "downloads", "interactions")):
    """A room with step 1 and step 1b done. Returns the registered organisation."""
    organisation = register(client, room_id)
    provision(client, *properties)
    return organisation


# --------------------------------------------------------------------------- #
# Discovery and identity
# --------------------------------------------------------------------------- #


def test_the_feature_is_mounted_by_discovery_alone(client):
    registry = client.get("/api/features").json()
    mine = next(f for f in registry["features"] if f["id"] == FEATURE_ID)
    assert mine["prefix"] == PREFIX
    assert mine["ticket"] == "WF-029"
    assert mine["routes"]
    assert mine["exception_handlers"] == ["LeadScoreError"]
    assert FEATURE_ID not in [f["id"] for f in registry["failed"]]


def test_the_prefix_is_the_ticket_derived_one():
    """The contract requires ``prefix`` to be ``/api/<your-ticket-slug>`` and unique."""
    module = load_feature(MODULE)
    assert module.router.prefix == PREFIX
    assert module.FEATURE["id"] == FEATURE_ID


def test_no_feature_failed_to_load(client):
    """A plugin that raised on import is skipped and reported at /api/features, so an
    unhandled failure would look like a passing build."""
    failed = client.get("/api/features").json()["failed"]
    assert [f["id"] for f in failed] == []


def test_the_descriptor_carries_a_description_and_a_nav_entry():
    module = load_feature(MODULE)
    assert module.FEATURE["description"].strip()
    assert module.FEATURE["nav"][0]["id"] == "lead-score"


# --------------------------------------------------------------------------- #
# Vocabulary and inferences
# --------------------------------------------------------------------------- #


def test_vocabulary_publishes_the_five_properties_and_the_two_buckets(client):
    body = client.get(f"{PREFIX}/vocabulary").json()
    assert body["score_property"] == "HubSpot Score"
    assert [entry["name"] for entry in body["families"]] == [
        "views",
        "clicks",
        "downloads",
        "interactions",
        "map_activity",
    ]
    assert [entry["name"] for entry in body["buckets"]] == ["positive", "negative"]
    assert body["integration"]["required_scopes"] == REQUIRED_SCOPES
    assert body["family_count"] == 5


def test_vocabulary_publishes_the_refinement_matrix_the_validator_enforces(client):
    body = client.get(f"{PREFIX}/vocabulary").json()
    assert body["refinement_matrix"] == {
        "views": ["occurred"],
        "clicks": ["occurred", "link_name"],
        "downloads": ["occurred", "file_name"],
        "interactions": ["occurred", "link_name"],
        "map_activity": ["occurred", "task_name"],
    }
    assert body["matcher"]["unverifiable_policy"].startswith("A filter the event does not")


def test_vocabulary_carries_the_crm_endpoints_and_the_execution_note(client):
    body = client.get(f"{PREFIX}/vocabulary").json()
    assert body["crm_plan"]["write_contact"] == "PATCH /crm/v3/objects/contacts/{contactId}"
    assert body["execution_note"].startswith("Recorded, not executed")


def test_inferences_are_served_with_the_sourced_half_beside_the_inferred_one(client):
    body = client.get(f"{PREFIX}/inferences").json()
    assert body["count"] == len(body["inferences"])
    assert body["sourced"]["score_property"] == "HubSpot Score"
    assert body["sourced"]["required_scopes"] == REQUIRED_SCOPES
    ids = {entry["id"] for entry in body["inferences"]}
    assert "score-is-recomputed-from-the-whole-history" in ids
    assert "not-built" in ids


def test_the_three_jev_decisions_are_published_with_their_audit_ids(client):
    body = client.get(f"{PREFIX}/inferences").json()
    audits = {entry["id"]: entry["audit"] for entry in body["jev_decisions"]}
    assert audits == {
        "score-is-recomputed-from-the-whole-history": "jev-20261004T024258-6152-78859",
        "no-outbound-crm-call": "jev-20261004T024259-6152-79173",
        "self-contained-domain-package": "jev-20261004T024139-28028-99762",
    }


# --------------------------------------------------------------------------- #
# Step 1: the CRM organisation
# --------------------------------------------------------------------------- #


def test_registering_an_integration_reports_the_scopes_it_still_needs(client):
    response = client.post(f"{PREFIX}/integrations", json={"scopes": ["crm.objects.contacts.read"]})
    assert response.status_code == 201
    organisation = response.json()["integration"]
    assert response.json()["created"] is True
    assert organisation["missing_scopes"] == ["crm.objects.contacts.write"]
    assert organisation["writable"] is False


def test_no_token_is_stored_and_no_token_is_claimed(client):
    """The row records the organisation and its scopes. Storing a token nobody can use
    would be a claim the product cannot keep."""
    organisation = register(client)
    body = client.get(f"{PREFIX}/integrations").json()["integrations"][0]
    assert "token" not in str(body).replace("token_note", "")
    assert organisation["scopes"] == sorted(REQUIRED_SCOPES)


def test_amending_an_integration_switches_it_on(client):
    organisation = register(client, enabled=False)
    response = client.patch(f"{PREFIX}/integrations/{organisation['id']}", json={"enabled": True})
    assert response.status_code == 200
    assert response.json()["integration"]["enabled"] is True
    assert response.json()["integration"]["writable"] is True


def test_amending_an_unknown_integration_is_409_not_404(client):
    """It names the step to perform rather than a row that does not exist, because
    'register one' is the answer a caller needs."""
    response = client.patch(f"{PREFIX}/integrations/lead_score_integration_nope", json={})
    assert response.status_code == 409
    assert response.json()["error"] == "crm_org_not_connected"


def test_saving_a_criterion_with_no_integration_is_409_and_names_the_step(client):
    response = client.post(
        f"{PREFIX}/criteria", json={"family": "views", "bucket": "positive", "score": 5}
    )
    assert response.status_code == 409
    assert response.json()["error"] == "crm_org_not_connected"
    assert "no CRM organisation is registered" in response.json()["detail"]


def test_saving_a_criterion_against_a_disabled_integration_is_409(client):
    register(client, enabled=False)
    response = client.post(
        f"{PREFIX}/criteria", json={"family": "views", "bucket": "positive", "score": 5}
    )
    assert response.status_code == 409
    assert response.json()["error"] == "crm_org_disabled"


def test_saving_a_criterion_on_a_read_only_token_is_409_and_names_the_scope(client):
    register(client, scopes=["crm.objects.contacts.read"])
    response = client.post(
        f"{PREFIX}/criteria", json={"family": "views", "bucket": "positive", "score": 5}
    )
    assert response.status_code == 409
    assert response.json()["error"] == "missing_crm_scope"
    assert "crm.objects.contacts.write" in response.json()["detail"]


# --------------------------------------------------------------------------- #
# Step 1b: the Dock properties
# --------------------------------------------------------------------------- #


def test_provisioning_reports_what_is_still_to_provision(client):
    register(client)
    provision(client, "views", "clicks")
    body = client.get(f"{PREFIX}/properties").json()
    assert body["provisioned"] == ["views", "clicks"]
    assert body["awaiting_provisioning"] == ["downloads", "interactions", "map_activity"]


def test_provisioning_a_property_with_no_family_is_422(client):
    register(client)
    response = client.post(f"{PREFIX}/properties", json={})
    assert response.status_code == 422
    assert "five published properties" in response.json()["detail"]


def test_provisioning_the_same_property_twice_is_not_a_second_row(client):
    register(client)
    first = client.post(f"{PREFIX}/properties", json={"family": "views", "name": "dsr_views"})
    again = client.post(f"{PREFIX}/properties", json={"family": "views", "name": "dsr_views"})
    assert first.json()["created"] is True
    assert again.json()["created"] is False
    assert client.get(f"{PREFIX}/properties").json()["count"] == 1


# --------------------------------------------------------------------------- #
# Steps 2 to 5: the criteria
# --------------------------------------------------------------------------- #


def test_saving_a_criterion_returns_it_with_its_lint_and_its_armed_state(client):
    room = make_room(client)["id"]
    armed(client, room)
    criterion = make_criterion(
        client, refinements={"file_name": "Pricing One-Pager", "occurred": "2026-10-01"}
    )
    assert criterion["id"]
    assert criterion["armed"] is True
    assert criterion["lint"] == []
    assert criterion["sign"] == 1


def test_a_criterion_without_the_occurred_baseline_is_saved_with_a_warning(client):
    """A recommendation is not a requirement, so this must not be a 4xx."""
    room = make_room(client)["id"]
    armed(client, room)
    criterion = make_criterion(client, family="views", refinements={})
    codes = {warning["code"] for warning in criterion["lint"]}
    assert "no_occurred_filter" in codes and "no_refinement" in codes


def test_a_criterion_on_an_unprovisioned_property_saves_but_does_not_arm(client):
    room = make_room(client)["id"]
    armed(client, room)
    criterion = make_criterion(
        client, family="map_activity", refinements={"task_name": "Intro call"}
    )
    assert criterion["family"] == "map_activity"
    assert criterion["armed"] is False
    assert client.get(f"{PREFIX}/criteria").json()["armed"] == 0


def test_a_criterion_naming_a_third_party_property_is_saved_and_reported_unresolved(client):
    room = make_room(client)["id"]
    armed(client, room)
    criterion = make_criterion(client, score_property="Showing SMB Intent")
    assert criterion["score_property"] == "Showing SMB Intent"
    assert criterion["property_resolved"] is False
    assert "unresolved_property" in {w["code"] for w in criterion["lint"]}


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        ({"family": "shares", "bucket": "positive", "score": 5}, "unknown_score_family"),
        ({"family": "views", "bucket": "sideways", "score": 5}, "unknown_score_bucket"),
        ({"family": "views", "bucket": "positive", "score": 0}, "invalid_score_value"),
        ({"family": "views", "bucket": "positive", "score": -5}, "invalid_score_value"),
        ({"family": "views", "bucket": "positive", "score": 2.5}, "invalid_score_value"),
        ({"family": "views", "bucket": "positive"}, "invalid_score_value"),
        (
            {
                "family": "views",
                "bucket": "positive",
                "score": 5,
                "refinements": {"file_name": "x"},
            },
            "unsupported_refinement",
        ),
        (
            {"family": "downloads", "bucket": "positive", "score": 5, "occured": "2026-10-01"},
            "unsupported_refinement",
        ),
        ({"bucket": "positive", "score": 5}, "unknown_score_family"),
    ],
)
def test_every_criterion_refusal_is_a_4xx_carrying_its_own_code(client, payload, code):
    room = make_room(client)["id"]
    armed(client, room)
    response = client.post(f"{PREFIX}/criteria", json=payload)
    assert response.status_code == 400, response.text
    assert response.json()["error"] == code
    assert response.json()["status"] == 400


def test_reading_a_criterion_returns_it_and_an_unknown_one_is_404(client):
    room = make_room(client)["id"]
    armed(client, room)
    criterion = make_criterion(client)
    assert client.get(f"{PREFIX}/criteria/{criterion['id']}").json()["id"] == criterion["id"]
    missing = client.get(f"{PREFIX}/criteria/lead_score_criterion_nope")
    assert missing.status_code == 404
    assert missing.json()["detail"] == "criterion lead_score_criterion_nope not found"


def test_a_criterion_can_be_amended_while_armed(client):
    room = make_room(client)["id"]
    armed(client, room)
    criterion = make_criterion(client)
    response = client.patch(f"{PREFIX}/criteria/{criterion['id']}", json={"score": 75})
    assert response.status_code == 200
    assert response.json()["criterion"]["score"] == 75
    assert response.json()["criterion"]["armed"] is True


def test_amending_a_criterion_revalidates_the_whole_thing(client):
    room = make_room(client)["id"]
    armed(client, room)
    criterion = make_criterion(client)
    refused = client.patch(f"{PREFIX}/criteria/{criterion['id']}", json={"family": "shares"})
    assert refused.status_code == 400
    assert refused.json()["error"] == "unknown_score_family"


def test_amending_an_unknown_criterion_is_404_over_http(client):
    room = make_room(client)["id"]
    armed(client, room)
    response = client.patch(f"{PREFIX}/criteria/lead_score_criterion_nope", json={"score": 5})
    assert response.status_code == 404
    assert response.json()["error"] == "unknown_criterion"


def test_withdrawing_a_criterion_removes_it_from_the_list_and_keeps_the_row(client):
    room = make_room(client)["id"]
    armed(client, room)
    criterion = make_criterion(client)
    response = client.delete(f"{PREFIX}/criteria/{criterion['id']}")
    assert response.status_code == 200
    assert response.json()["withdrawn"] is True
    assert client.get(f"{PREFIX}/criteria").json()["count"] == 0
    assert client.get(f"{PREFIX}/criteria/{criterion['id']}").status_code == 404


def test_withdrawing_an_unknown_criterion_is_404_over_http(client):
    assert client.delete(f"{PREFIX}/criteria/nope").status_code == 404


def test_the_criterion_list_can_be_filtered_by_bucket_and_by_property(client):
    room = make_room(client)["id"]
    armed(client, room)
    make_criterion(client)
    make_criterion(client, family="clicks", bucket="negative", score=5)
    assert client.get(f"{PREFIX}/criteria").json()["count"] == 2
    assert client.get(f"{PREFIX}/criteria", params={"bucket": "negative"}).json()["count"] == 1
    assert client.get(f"{PREFIX}/criteria", params={"family": "views"}).json()["count"] == 0


# --------------------------------------------------------------------------- #
# Preview
# --------------------------------------------------------------------------- #


def test_preview_matches_without_saving_or_sending_anything(client):
    room = make_room(client)["id"]
    armed(client, room)
    response = client.post(
        f"{PREFIX}/preview",
        json={
            "criterion": {
                "family": "downloads",
                "bucket": "positive",
                "score": 15,
                "refinements": {"file_name": "Pricing One-Pager"},
            },
            "event": {
                "contact": "buyer@example.test",
                "action": "downloaded",
                "file_name": "Pricing One-Pager",
                "occurred_at": "2026-10-01T09:00:00Z",
            },
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["sends_nothing"] is True
    assert body["verdict"]["matched"] is True
    assert body["would_add"] == 15
    assert client.get(f"{PREFIX}/criteria").json()["count"] == 0


def test_preview_reports_a_miss_as_zero_points(client):
    room = make_room(client)["id"]
    armed(client, room)
    body = client.post(
        f"{PREFIX}/preview",
        json={
            "criterion": {"family": "views", "bucket": "negative", "score": 4},
            "event": {"contact": "buyer@example.test", "action": "downloaded"},
        },
    ).json()
    assert body["would_add"] == 0
    assert body["verdict"]["reason"] == "family_mismatch"


def test_preview_with_an_empty_payload_is_a_4xx_not_a_500(client):
    register(client)
    response = client.post(f"{PREFIX}/preview", json={})
    assert response.status_code == 400
    assert response.json()["error"] == "unknown_score_family"


# --------------------------------------------------------------------------- #
# The source side and the continuous rule
# --------------------------------------------------------------------------- #


def test_an_event_scores_its_contact_and_the_response_carries_the_plan(client):
    room = make_room(client)["id"]
    armed(client, room)
    make_criterion(client, refinements={"file_name": "Pricing One-Pager"})
    body = send(
        client,
        room,
        contact="priya.raman@northwind.example",
        action="downloaded",
        file_name="Pricing One-Pager",
        occurred_at="2026-10-01T09:00:00Z",
    )
    assert body["created"] is True
    assert body["score"]["score"] == 20
    assert body["score"]["crm_plan"]["executed"] is False
    assert body["score"]["crm_plan"]["requests"][0]["path"].startswith("/crm/v3/objects/contacts/")
    assert body["score"]["batch_plan"]["count"] == 1


def test_the_second_matching_event_raises_the_score_again(client):
    room = make_room(client)["id"]
    armed(client, room)
    make_criterion(client, refinements={"file_name": "Pricing One-Pager"})
    for hour in ("09", "11"):
        send(
            client,
            room,
            contact="buyer@example.test",
            action="downloaded",
            file_name="Pricing One-Pager",
            occurred_at=f"2026-10-01T{hour}:00:00Z",
        )
    scores = client.get(f"{PREFIX}/rooms/{room}/scores").json()
    assert scores["scores"][0]["score"] == 40


def test_a_repeated_event_with_the_same_key_is_a_counter(client):
    room = make_room(client)["id"]
    armed(client, room)
    make_criterion(client, refinements={"file_name": "Pricing One-Pager"})
    payload = {
        "contact": "buyer@example.test",
        "action": "downloaded",
        "file_name": "Pricing One-Pager",
        "occurred_at": "2026-10-01T09:00:00Z",
        "idempotency_key": "wh-1",
    }
    send(client, room, **payload)
    again = send(client, room, **payload)
    assert again["duplicate"] is True
    assert again["score"]["score"] == 20
    assert client.get(f"{PREFIX}/rooms/{room}/activity").json()["count"] == 1


def test_withdrawing_a_criterion_takes_its_points_off_at_the_next_event(client):
    """The consequence of recomputing rather than accumulating."""
    room = make_room(client)["id"]
    armed(client, room)
    criterion = make_criterion(client, refinements={"file_name": "Pricing One-Pager"})
    send(
        client,
        room,
        contact="buyer@example.test",
        action="downloaded",
        file_name="Pricing One-Pager",
        occurred_at="2026-10-01T09:00:00Z",
    )
    assert client.get(f"{PREFIX}/rooms/{room}/scores/buyer@example.test").json()["score"] == 20
    client.delete(f"{PREFIX}/criteria/{criterion['id']}")
    body = client.post(
        f"{PREFIX}/rooms/{room}/score", json={"contact": "buyer@example.test"}
    ).json()
    assert body["score"] == 0
    assert body["delta"] == -20


def test_an_event_in_one_room_does_not_score_a_contact_in_another(client):
    """Room scoping has a real trap: ``room_id`` is a column, not part of ``data``, so
    an index filter on it matches nothing and reports nothing."""
    first = make_room(client, "A")["id"]
    second = make_room(client, "B")["id"]
    organisation = register(client)
    client.patch(
        f"{PREFIX}/integrations/{organisation['id']}",
        json={"deal_connections": {first: {"deal_id": "d1"}, second: {"deal_id": "d2"}}},
    )
    provision(client, "views", "clicks", "downloads", "interactions")
    make_criterion(client, refinements={"file_name": "Pricing One-Pager"})
    send(
        client,
        first,
        contact="buyer@example.test",
        action="downloaded",
        file_name="Pricing One-Pager",
        occurred_at="2026-10-01T09:00:00Z",
    )
    assert client.get(f"{PREFIX}/rooms/{first}/scores/buyer@example.test").json()["score"] == 20
    assert client.get(f"{PREFIX}/rooms/{second}/scores/buyer@example.test").status_code == 404
    assert client.get(f"{PREFIX}/rooms/{second}/scores").json()["total"] == 0
    assert client.get(f"{PREFIX}/rooms/{second}/activity").json()["count"] == 0


def test_an_event_with_no_contact_is_422_and_explains_why(client):
    room = make_room(client)["id"]
    armed(client, room)
    response = client.post(f"{PREFIX}/rooms/{room}/activity", json={"action": "viewed"})
    assert response.status_code == 400
    assert response.json()["error"] == "malformed_activity"
    assert "must name a contact" in response.json()["detail"]


def test_a_run_without_a_contact_is_refused(client):
    room = make_room(client)["id"]
    armed(client, room)
    response = client.post(f"{PREFIX}/rooms/{room}/score", json={})
    assert response.status_code == 400
    assert "needs the contact" in response.json()["detail"]


def test_a_run_in_a_room_with_no_deal_reports_it_rather_than_failing(client):
    room = make_room(client)["id"]
    organisation = register(client)
    provision(client, "views", "clicks", "downloads", "interactions")
    assert organisation["deal_connections"] == {}
    make_criterion(client)
    body = client.post(
        f"{PREFIX}/rooms/{room}/score", json={"contact": "buyer@example.test"}
    ).json()
    assert "room_not_deal_connected" in {f["code"] for f in body["findings"]}


def test_a_lifecyclestage_value_is_reported_rather_than_written(client):
    room = make_room(client)["id"]
    armed(client, room)
    make_criterion(client)
    body = client.post(
        f"{PREFIX}/rooms/{room}/score",
        json={
            "contact": "buyer@example.test",
            "lifecyclestage": "lead",
            "current_lifecycle_stage": "opportunity",
        },
    ).json()
    dropped = body["crm_plan"]["dropped_properties"]
    assert len(dropped) == 1
    assert dropped[0]["accepted"] is False
    assert "at or behind" in dropped[0]["comparison"]
    assert "lifecyclestage" not in body["crm_plan"]["requests"][0]["body"]["properties"]


# --------------------------------------------------------------------------- #
# Reads
# --------------------------------------------------------------------------- #


def test_the_score_list_is_ordered_and_a_contact_can_be_read_on_its_own(client):
    room = make_room(client)["id"]
    armed(client, room)
    make_criterion(client, refinements={})
    for contact in ("low@example.test", "high@example.test", "mid@example.test"):
        send(
            client,
            room,
            contact=contact,
            action="downloaded",
            occurred_at="2026-10-01T09:00:00Z",
        )
    listed = client.get(f"{PREFIX}/rooms/{room}/scores").json()
    assert listed["total"] == 3
    assert [row["score"] for row in listed["scores"]] == [20, 20, 20]
    one = client.get(f"{PREFIX}/rooms/{room}/scores/high@example.test").json()
    assert one["contact"] == "high@example.test"
    assert one["history"]


def test_an_unknown_contact_is_404_over_http(client):
    room = make_room(client)["id"]
    armed(client, room)
    response = client.get(f"{PREFIX}/rooms/{room}/scores/nobody@example.test")
    assert response.status_code == 404
    assert "has no score" in response.json()["detail"]


def test_the_activity_list_is_room_scoped_and_filterable_by_contact(client):
    room = make_room(client)["id"]
    armed(client, room)
    send(client, room, contact="a@example.test", action="viewed")
    send(client, room, contact="b@example.test", action="viewed")
    assert client.get(f"{PREFIX}/rooms/{room}/activity").json()["count"] == 2
    assert (
        client.get(f"{PREFIX}/rooms/{room}/activity", params={"contact": "a@example.test"}).json()[
            "count"
        ]
        == 1
    )
    assert client.get(f"{PREFIX}/rooms/{room}/activity", params={"limit": 1}).json()["count"] == 1


def test_history_can_be_filtered_to_the_runs_that_moved(client):
    room = make_room(client)["id"]
    armed(client, room)
    make_criterion(client, refinements={"file_name": "Pricing One-Pager"})
    send(
        client,
        room,
        contact="buyer@example.test",
        action="downloaded",
        file_name="Pricing One-Pager",
        occurred_at="2026-10-01T09:00:00Z",
    )
    send(
        client,
        room,
        contact="buyer@example.test",
        action="viewed",
        occurred_at="2026-10-01T11:00:00Z",
    )
    assert client.get(f"{PREFIX}/rooms/{room}/history").json()["count"] == 2
    moved = client.get(f"{PREFIX}/rooms/{room}/history", params={"moved_only": True}).json()
    assert moved["count"] == 1
    assert moved["runs"][0]["delta"] == 20


def test_the_summary_reports_the_numbers_and_the_states_that_are_not_successes(client):
    room = make_room(client)["id"]
    armed(client, room, properties=("views", "clicks", "downloads"))
    make_criterion(client, family="clicks", bucket="negative", score=10, refinements={})
    make_criterion(client, family="map_activity", refinements={"task_name": "Intro call"})
    send(client, room, contact="buyer@example.test", action="opened_link")
    body = client.get(f"{PREFIX}/rooms/{room}/summary").json()
    assert body["criteria_count"] == 2
    assert body["criteria_by_bucket"] == {"positive": 1, "negative": 1}
    assert body["contacts_below_zero"] == 1
    assert body["integration_state"] == "writable"
    assert body["deal_connected"] is True
    assert body["awaiting_provisioning"] == ["interactions", "map_activity"]
    codes = {entry["code"] for entry in body["states"]}
    assert {"property_not_provisioned", "negative_scores", "runs_that_moved_nothing"} <= codes


def test_a_summary_of_a_room_with_no_deal_says_so(client):
    room = make_room(client)["id"]
    armed(client, room)
    assert client.get(f"{PREFIX}/rooms/{room}/summary").json()["deal_connected"] is True
    other = make_room(client, "Unconnected")["id"]
    assert client.get(f"{PREFIX}/rooms/{other}/summary").json()["deal_connected"] is False


# --------------------------------------------------------------------------- #
# The audit source rule
# --------------------------------------------------------------------------- #


def audit_entries(client, **params) -> list[dict]:
    return client.get("/api/audit", params={"limit": 1000, **params}).json()["entries"]


def my_sources(client) -> set[str]:
    return {
        entry["source"] for entry in audit_entries(client) if PREFIX in (entry.get("source") or "")
    }


def test_every_write_names_the_route_that_served_it(client):
    room = make_room(client)["id"]
    organisation = armed(client, room)
    criterion = make_criterion(client, refinements={"file_name": "Pricing One-Pager"})
    client.patch(f"{PREFIX}/criteria/{criterion['id']}", json={"score": 30})
    client.patch(f"{PREFIX}/integrations/{organisation['id']}", json={"label": "Renamed"})
    send(
        client,
        room,
        contact="buyer@example.test",
        action="downloaded",
        file_name="Pricing One-Pager",
        occurred_at="2026-10-01T09:00:00Z",
    )
    client.post(f"{PREFIX}/rooms/{room}/score", json={"contact": "buyer@example.test"})
    client.delete(f"{PREFIX}/criteria/{criterion['id']}")

    assert my_sources(client) == {
        f"POST {PREFIX}/integrations",
        f"POST {PREFIX}/properties",
        f"POST {PREFIX}/properties",
        f"POST {PREFIX}/properties",
        f"POST {PREFIX}/properties",
        f"POST {PREFIX}/criteria",
        f"PATCH {PREFIX}/criteria/{{criterion_id}}",
        f"PATCH {PREFIX}/integrations/{{integration_id}}",
        f"POST {PREFIX}/rooms/{{room_id}}/activity",
        f"POST {PREFIX}/rooms/{{room_id}}/score",
        f"DELETE {PREFIX}/criteria/{{criterion_id}}",
    }


def test_the_score_write_and_the_event_that_caused_it_share_the_routes_source(client):
    """Both were caused by the same request, so both name it."""
    room = make_room(client)["id"]
    armed(client, room)
    make_criterion(client, refinements={"file_name": "Pricing One-Pager"})
    send(
        client,
        room,
        contact="buyer@example.test",
        action="downloaded",
        file_name="Pricing One-Pager",
        occurred_at="2026-10-01T09:00:00Z",
    )
    run = client.get(f"{PREFIX}/rooms/{room}/history").json()["runs"][0]
    assert run["driver"] == "activity_event"
    entries = audit_entries(client, collection="lead_score_run")
    assert entries[0]["source"] == f"POST {PREFIX}/rooms/{{room_id}}/activity"


def test_a_write_records_the_actor_it_was_given(client):
    room = make_room(client)["id"]
    armed(client, room)
    client.post(
        f"{PREFIX}/criteria",
        json={"family": "views", "bucket": "positive", "score": 5},
        params={"actor": "dana"},
    )
    entry = audit_entries(client, collection="lead_score_criterion")[0]
    assert entry["actor"] == "dana"


def test_every_source_this_feature_can_write_is_one_of_its_own_routes():
    """The source strings are derived from the mounted router, not written out.

    Read out of the module rather than asserted one by one, because the defect this
    guards is a *drift* between the recorded source and the mounted path: a
    hand-written list in the test would be updated in the same commit as the bug and
    would never catch it.
    """
    module = load_feature(MODULE)
    source = Path(module.__file__).read_text(encoding="utf-8")
    expressions = re.findall(r'source=f?"([^"]*\{router\.prefix\}[^"]*)"', source)
    assert expressions, "no source is built from router.prefix"

    write_routes = {
        (sorted(route.methods - {"HEAD", "OPTIONS"})[0], route.path)
        for route in module.router.routes
        if sorted(route.methods - {"HEAD", "OPTIONS"})[0] in ("POST", "PATCH", "PUT", "DELETE")
    }
    recorded = {
        (
            expression.split(" ", 1)[0],
            # The source is an f-string, so a literal path parameter is written with
            # doubled braces in the source text and unescaped in the string it renders.
            # This reads the rendered shape, which is what the audit row carries.
            f"{PREFIX}{expression.split('{router.prefix}')[1]}".replace("{{", "{").replace(
                "}}", "}"
            ),
        )
        for expression in expressions
    }
    assert {method for method, _path in recorded} <= {method for method, _path in write_routes}
    for method, path in recorded:
        assert (method, path) in write_routes, f"{method} {path} is not a route this feature serves"


def test_the_audit_source_is_built_from_the_live_prefix_not_a_hardcoded_url():
    """The defect the contract names by name: a row that names a path nobody called.

    Asserted as "every ``source=`` in the module interpolates ``router.prefix``",
    which is the property that matters. A test that simply banned the literal
    ``/api/wf-029`` would fail on the router's own ``prefix=`` argument and so could
    not be written at all.
    """
    source = Path(load_feature(MODULE).__file__).read_text(encoding="utf-8")
    every_source = re.findall(r'source=f"([^"]*)"', source)
    assert every_source, "no source is written with an f-string"
    for expression in every_source:
        assert "{router.prefix}" in expression, expression


def mounted_routes(client) -> dict[str, list[list[str]]]:
    """Every mounted path, per method, split into segments.

    ``app.routes`` is not usable for this: this FastAPI version records an included
    router as a single nested entry rather than flattening its routes. The published
    schema is the flat, authoritative list of what is served.
    """
    schema = client.get("/openapi.json").json()
    mounted: dict[str, list[list[str]]] = {}
    for path, operations in (schema.get("paths") or {}).items():
        for method in operations:
            if method.lower() in ("get", "post", "patch", "put", "delete"):
                mounted.setdefault(method.upper(), []).append(path.strip("/").split("/"))
    return mounted


#: The record-id shapes the core ``AuditedDatabase`` mints. A recorded source carries
#: the id that filled a parameter, so a segment matching this is a value where the
#: route declares a parameter.
RECORD_ID = re.compile(r"^[a-z_]+_[0-9a-f]{16,}$")


def serves(mounted: dict[str, list[list[str]]], method: str, path: str) -> bool:
    """Whether ``method path`` reaches a mounted route.

    Segment by segment, because a path parameter fills exactly one segment: the two
    must line up in number as well as in shape.
    """
    for candidate in mounted.get(method, []):
        recorded = path.strip("/").split("/")
        if len(candidate) != len(recorded):
            continue
        if all(
            expected.startswith("{") or expected == actual or RECORD_ID.match(actual)
            for expected, actual in zip(candidate, recorded, strict=False)
        ):
            return True
    return False


def test_every_audit_row_in_the_whole_log_names_a_mounted_route(client):
    """The invariant, product-wide: an audit ``source`` is always callable.

    This drives writes through this feature *and* through the core record routes,
    then checks every row the log holds. The defect the contract names by name is
    "a feature's audit log kept recording a path the app had stopped serving", which
    is invisible to a test scoped to one feature's own sources.
    """
    room = make_room(client)["id"]
    armed(client, room)
    criterion = make_criterion(client)
    send(client, room, contact="buyer@example.test", action="viewed")
    client.post("/api/records/room", json={"name": "Core room", "account": "Core"})
    client.delete(f"{PREFIX}/criteria/{criterion['id']}")

    mounted = mounted_routes(client)
    assert serves(mounted, "POST", f"{PREFIX}/rooms/{{room_id}}/activity")
    assert serves(mounted, "POST", f"{PREFIX}/criteria")

    entries = audit_entries(client)
    assert entries
    for entry in entries:
        method, _, path = str(entry["source"]).partition(" ")
        assert serves(mounted, method, path), (
            f"audit row {entry['seq']} names {method} {path}, which is not a mounted route"
        )


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #


def seed_database(count_rooms: int = 4):
    """A throwaway database with demo rooms and this feature's seed run over it."""
    import random
    from datetime import datetime, timezone

    tmp = tempfile.TemporaryDirectory()
    db = AuditedDatabase(Path(tmp.name) / "seed.db", actor="test")
    room_ids = []
    for index, name in enumerate(
        ["Northwind Traders", "Contoso Health", "Fabrikam Logistics", "Adventure Works"]
    ):
        room = db.create("room", {"name": name, "account": name}, source="test")
        room_ids.append((room["id"], name))
        if index >= count_rooms - 1:
            break
    summary = load_feature(MODULE).seed(
        db,
        {
            "room_ids": room_ids,
            "now": datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc),
            "rng": random.Random(1),
        },
    )
    return tmp, db, room_ids, summary


def test_the_seed_returns_a_string_naming_the_states_it_created():
    tmp, db, room_ids, summary = seed_database()
    try:
        assert isinstance(summary, str)
        assert "criteria" in summary and "Dock properties provisioned" in summary
        assert "score below zero" in summary
    finally:
        db.close()
        tmp.cleanup()


def test_every_character_of_the_seed_string_is_encodable_by_cp1252():
    """A single U+2192 RIGHTWARDS ARROW in one recovered feature broke the entire
    seeder on a Windows console, so this is a test rather than a review item."""
    tmp, db, _room_ids, summary = seed_database()
    try:
        assert summary == summary.encode("cp1252").decode("cp1252")
        summary.encode("cp1252")
    finally:
        db.close()
        tmp.cleanup()


def test_the_seed_shows_a_score_that_moved_up_and_one_below_zero():
    tmp, db, room_ids, _summary = seed_database()
    try:
        module = load_feature(MODULE)
        from dsr.lead_score.engine import LeadScoreEngine
        from dsr.store import RecordStore

        engine = LeadScoreEngine(RecordStore(db))
        scores = engine.scores(room_ids[0][0])["scores"]
        by_contact = {row["contact"]: row["score"] for row in scores}
        assert by_contact["priya.raman@northwind.example"] == 30
        assert by_contact["dana.kelly@northwind.example"] == -10
        assert module.DEMO_ACTIVITY
    finally:
        db.close()
        tmp.cleanup()


def test_the_seed_covers_the_states_that_are_not_successes():
    """A demo of only green teaches a reviewer nothing."""
    tmp, db, room_ids, _summary = seed_database()
    try:
        from dsr.lead_score.engine import LeadScoreEngine
        from dsr.store import RecordStore

        engine = LeadScoreEngine(RecordStore(db))
        # A criterion whose Dock property was never provisioned.
        assert engine.properties()["awaiting_provisioning"] == ["map_activity"]
        unarmed = [c for c in engine.criteria()["criteria"] if not c["armed"]]
        assert [c["family"] for c in unarmed] == ["map_activity"]
        # A criterion writing to a third-party contact property, reported unresolved.
        unresolved = [c for c in engine.criteria()["criteria"] if not c["property_resolved"]]
        assert [c["score_property"] for c in unresolved] == ["Showing SMB Intent"]
        # A room with no deal connected.
        unconnected = [
            code
            for run in engine.history(room_ids[1][0], limit=50)["runs"]
            for code in [f["code"] for f in run["findings"]]
        ]
        assert "room_not_deal_connected" in unconnected
        assert engine.summary(room_ids[1][0])["deal_connected"] is False
    finally:
        db.close()
        tmp.cleanup()


def test_the_seed_never_leaves_every_state_a_success():
    tmp, db, room_ids, _summary = seed_database()
    try:
        from dsr.lead_score.engine import LeadScoreEngine
        from dsr.store import RecordStore

        engine = LeadScoreEngine(RecordStore(db))
        summary = engine.summary(room_ids[0][0])
        counts = {entry["code"]: entry["count"] for entry in summary["states"]}
        assert counts["property_not_provisioned"] >= 1
        assert counts["negative_scores"] >= 1
    finally:
        db.close()
        tmp.cleanup()


def test_the_seed_writes_only_audited_rows_and_attributes_them_to_the_seeder():
    tmp, db, _room_ids, _summary = seed_database()
    try:
        seeded = {
            "lead_score_integration",
            "lead_score_property",
            "lead_score_criterion",
            "lead_score_activity",
            "lead_score_contact",
            "lead_score_run",
        }
        entries = [entry for entry in db.audit(limit=1000) if entry["collection"] in seeded]
        assert entries
        assert all(entry["source"] == "seed" for entry in entries)
    finally:
        db.close()
        tmp.cleanup()


def test_the_seed_says_so_when_there_are_no_demo_rooms():
    import random
    from datetime import datetime, timezone

    tmp = tempfile.TemporaryDirectory()
    db = AuditedDatabase(Path(tmp.name) / "seed.db", actor="test")
    try:
        summary = load_feature(MODULE).seed(
            db, {"room_ids": [], "now": datetime.now(timezone.utc), "rng": random.Random(1)}
        )
        assert "no demo rooms" in summary
    finally:
        db.close()
        tmp.cleanup()
