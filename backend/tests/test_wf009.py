"""HTTP tests for WF-009: approve and publish library content.

Converted from ``backend/tests/test_wf009_publishing.py`` on
``feature/WF-009-approve-and-publish-library-content-immediately-or-on``. The
domain rules are unchanged, so the assertions are unchanged; what changed is
where they are reached from:

* the routes are this feature's own ``APIRouter``, mounted by discovery, so the
  fixture builds a client from the mounted app and these tests exercise the same
  path a real client does. If the feature failed to register, or collided with
  another feature's route, every test here would 404;
* the paths themselves are the same ``/api/publishing/...`` the branch served.
  The prefix is unchanged by the port, unlike the other ports in this programme,
  because WF-011 shares it and their concrete paths are disjoint;
* ``source`` is now supplied by the HTTP layer, so the audit assertions here can
  check the thing the port brief cares about: that the row names the route that
  actually served the write.

Each test names the rule it is protecting in its docstring, so a reviewer can
check the assertion against the source in
``docs/research/digital-sales-room-workflows/wf/WF-009.md`` rather than taking
the implementation's word for it.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from dsr.api import app
from dsr.features import load_feature
from dsr.features.wf009_publishing_domain import (
    APPROVED,
    IMMEDIATE_BATCH_LIMIT,
    PENDING,
    REJECTED,
    SCHEDULED_BATCH_LIMIT,
)
from fastapi.testclient import TestClient

PREFIX = "/api/publishing"
FEATURE_ID = "wf-009-publishing"


@pytest.fixture()
def client(monkeypatch):
    # A temporary database, exactly as test_features.py does it: the env var is
    # read at call time by dsr.deps, so every test gets its own store.
    tmp = tempfile.TemporaryDirectory()
    monkeypatch.setenv("DSR_DB_PATH", str(Path(tmp.name) / "wf009.db"))
    monkeypatch.setenv("DSR_AUDIT_DIR", str(Path(tmp.name) / "audit"))
    monkeypatch.setattr("dsr.api.FRONTEND_DIST", Path(tmp.name) / "absent-frontend")
    with TestClient(app) as test_client:
        yield test_client
    tmp.cleanup()


# --------------------------------------------------------------------------- #
# Fixtures / helpers
# --------------------------------------------------------------------------- #

TWO_STEP_PROCESS = {
    "name": "Standard content review",
    "steps": [
        {
            "key": "legal",
            "label": "Legal review",
            "approvers": ["sam"],
            "watchers": ["dana"],
            "approve_button_label": "Sign off",
            "reject_button_label": "Send back",
        },
        {
            "key": "brand",
            "label": "Brand review",
            "approvers": ["dana"],
            "order": 2,
        },
    ],
}


def make_room(client, **data):
    return client.post("/api/records/room", json={"name": "Acme", **data}).json()


def make_document(client, *, room_id=None, status="Draft", **data):
    body = {"title": "Security Pack", "status": status, **data}
    return client.post("/api/records/document", json=body, params={"room_id": room_id}).json()


def make_process(client, payload=None, **params):
    return client.post(f"{PREFIX}/processes", json=payload or TWO_STEP_PROCESS, params=params)


def submit(client, document_id, process_id, **params):
    return client.post(
        f"{PREFIX}/submissions",
        json={"content": [document_id], "process_id": process_id},
        params=params,
    )


def decide(client, workflow_id, step_key, decision, comment=None, **params):
    return client.post(
        f"{PREFIX}/workflows/{workflow_id}/steps/{step_key}",
        json={"decision": decision, "comment": comment},
        params=params,
    )


def approve_whole_workflow(client, workflow_id):
    """Drive a two-step workflow to Approved, one step at a time."""
    first = decide(client, workflow_id, "legal", "approve", actor="sam")
    assert first.status_code == 200
    second = decide(client, workflow_id, "brand", "approve", actor="dana")
    assert second.status_code == 200
    return second.json()


# --------------------------------------------------------------------------- #
# Registration
# --------------------------------------------------------------------------- #


def test_the_feature_is_mounted_by_discovery(client):
    """No file in the host names this feature; discovery mounts it anyway."""
    body = client.get("/api/features").json()
    installed = {feature["id"]: feature for feature in body["features"]}

    assert FEATURE_ID in installed
    record = installed[FEATURE_ID]
    assert record["prefix"] == PREFIX
    assert record["ticket"] == "WF-009"
    assert record["exception_handlers"] == ["ApprovalConflict", "PublishError"]
    # A router that reported no routes would be a feature that registers but
    # serves nothing, which the host's own invariant forbids.
    assert record["routes"]


def test_the_domain_module_sits_beside_the_feature_not_at_the_package_root(client):
    """The branch's ``dsr/publishing.py`` path is WF-011's to take as well.

    Two branches that each add ``backend/dsr/publishing.py``, with a different
    ``PublishingService`` in each, would collide on a file the CI guard does not
    watch. Keeping the domain module inside the feature folder is what stops
    that, so the location is asserted rather than left to drift.
    """
    module = load_feature("wf009_publishing")
    assert module.PublishingService.__module__ == "dsr.features.wf009_publishing_domain"


def test_the_prefix_is_shared_with_wf011_without_a_concrete_collision(client):
    """A shared prefix is legal; a shared (method, path) is not.

    The host allows two features under ``/api/publishing`` because none of their
    concrete paths overlap. This pins the paths this feature owns, so a later
    edit that reuses one of WF-011's is caught here rather than at load time.
    """
    paths = {
        (route["methods"][0], route["path"])
        for route in next(
            f for f in client.get("/api/features").json()["features"] if f["id"] == FEATURE_ID
        )["routes"]
        if route["methods"]
    }

    assert ("GET", f"{PREFIX}/processes") in paths
    assert ("POST", f"{PREFIX}/submissions") in paths
    assert ("GET", f"{PREFIX}/workflows") in paths
    assert ("POST", f"{PREFIX}/publish") in paths
    assert ("GET", f"{PREFIX}/publications") in paths
    assert ("POST", f"{PREFIX}/publications/due") in paths
    assert ("GET", f"{PREFIX}/folders") in paths
    assert ("GET", f"{PREFIX}/subscriptions") in paths
    # WF-011's paths, none of which this feature claims.
    assert ("GET", f"{PREFIX}/rooms") not in paths
    assert ("GET", f"{PREFIX}/events") not in paths
    assert ("GET", f"{PREFIX}/webhooks") not in paths


# --------------------------------------------------------------------------- #
# Approval processes
# --------------------------------------------------------------------------- #


def test_process_requires_a_name(client):
    response = client.post(f"{PREFIX}/processes", json={"steps": [{"label": "Review"}]})
    assert response.status_code == 400
    assert "name" in response.json()["detail"]


def test_process_requires_at_least_one_step(client):
    response = client.post(f"{PREFIX}/processes", json={"name": "Empty"})
    assert response.status_code == 400
    assert "step" in response.json()["detail"]


def test_process_preserves_configured_button_labels(client):
    """The researched schema names approveButtonLabel / rejectButtonLabel per step."""
    body = make_process(client).json()

    legal = body["data"]["steps"][0]
    assert legal["approve_button_label"] == "Sign off"
    assert legal["reject_button_label"] == "Send back"


def test_process_steps_default_to_approve_and_reject_labels(client):
    body = make_process(client, {"name": "Bare", "steps": [{"label": "Review"}]}).json()

    step = body["data"]["steps"][0]
    assert step["approve_button_label"] == "Approve"
    assert step["reject_button_label"] == "Reject"
    assert step["key"] == "review"


def test_process_steps_are_ordered(client):
    """A template that lists steps out of order still runs in declared order."""
    body = make_process(
        client,
        {
            "name": "Reordered",
            "steps": [
                {"key": "second", "order": 2},
                {"key": "first", "order": 1},
            ],
        },
    ).json()

    assert [s["key"] for s in body["data"]["steps"]] == ["first", "second"]


def test_process_accepts_arbitrary_routing_fields_without_migration(client):
    """Schema flexibility: unknown keys are stored, not rejected or pruned."""
    payload = {
        "name": "With routing",
        "sla_hours": 24,
        "routing": {"escalate_to": "ciso", "regions": ["emea", "apac"]},
        "steps": [{"label": "Review", "teams": ["legal"]}],
    }

    body = make_process(client, payload).json()

    assert body["data"]["sla_hours"] == 24
    assert body["data"]["routing"] == {"escalate_to": "ciso", "regions": ["emea", "apac"]}
    assert body["data"]["steps"][0]["teams"] == ["legal"]


# --------------------------------------------------------------------------- #
# Submissions
# --------------------------------------------------------------------------- #


def test_submit_snapshots_steps_as_pending(client):
    process = make_process(client).json()
    document = make_document(client)

    workflow = submit(client, document["id"], process["id"]).json()

    assert workflow["data"]["status"] == PENDING
    assert [s["status"] for s in workflow["data"]["steps"]] == [PENDING, PENDING]
    assert workflow["data"]["approval_process"] == {
        "id": process["id"],
        "name": "Standard content review",
    }


def test_submit_snapshots_steps_so_template_edits_do_not_reach_an_open_review(client):
    """Editing a template must not rewrite a review already in flight."""
    process = make_process(client).json()
    document = make_document(client)
    workflow = submit(client, document["id"], process["id"]).json()

    client.patch(
        f"/api/records/approval_process/{process['id']}",
        json={"steps": [{"key": "rewritten", "label": "Rewritten"}]},
    )

    reread = client.get(f"{PREFIX}/workflows/{workflow['id']}").json()
    assert [s["key"] for s in reread["data"]["steps"]] == ["legal", "brand"]


def test_submit_records_the_content_and_its_latest_version(client):
    process = make_process(client).json()
    document = make_document(
        client,
        versions=[{"version_id": "v1"}, {"version_id": "v2"}],
    )

    workflow = submit(client, document["id"], process["id"]).json()

    assert workflow["data"]["content"] == [
        {"id": document["id"], "title": "Security Pack", "version_id": "v2"}
    ]


def test_submit_unknown_document_is_rejected(client):
    process = make_process(client).json()

    response = submit(client, "document_missing", process["id"])

    assert response.status_code == 400
    assert "not found" in response.json()["detail"]


def test_submit_unknown_process_is_rejected(client):
    document = make_document(client)

    response = submit(client, document["id"], "approval_process_missing")

    assert response.status_code == 400


def test_submit_already_published_content_is_rejected(client):
    process = make_process(client).json()
    document = make_document(client, status="published")

    response = submit(client, document["id"], process["id"])

    assert response.status_code == 400
    assert "already published" in response.json()["detail"]


def test_submitting_twice_is_a_conflict(client):
    """A document cannot sit in two open queues at once."""
    process = make_process(client).json()
    document = make_document(client)
    assert submit(client, document["id"], process["id"]).status_code == 201

    response = submit(client, document["id"], process["id"])

    assert response.status_code == 409


def test_submit_with_no_content_is_rejected(client):
    process = make_process(client).json()

    response = client.post(
        f"{PREFIX}/submissions", json={"content": [], "process_id": process["id"]}
    )

    assert response.status_code == 400


# --------------------------------------------------------------------------- #
# The queue
# --------------------------------------------------------------------------- #


def test_queue_is_paginated_with_total_count(client):
    """Follows the documented GET /approvalWorkflows contract: limit, offset, total."""
    process = make_process(client).json()
    for index in range(3):
        document = make_document(client, title=f"Doc {index}")
        submit(client, document["id"], process["id"])

    page = client.get(f"{PREFIX}/workflows", params={"limit": 2, "offset": 0}).json()

    assert page["total_count"] == 3
    assert page["limit"] == 2
    assert page["offset"] == 0
    assert len(page["entries"]) == 2
    # The link is built from the router prefix, so it names a route that exists.
    assert page["next_page"] == f"{PREFIX}/workflows?limit=2&offset=2"
    assert client.get(page["next_page"]).status_code == 200


def test_queue_next_page_is_null_on_the_last_page(client):
    process = make_process(client).json()
    document = make_document(client)
    submit(client, document["id"], process["id"])

    page = client.get(f"{PREFIX}/workflows", params={"limit": 10}).json()

    assert page["next_page"] is None


def test_queue_defaults_match_the_documented_contract(client):
    page = client.get(f"{PREFIX}/workflows").json()

    assert page["limit"] == 100
    assert page["offset"] == 0
    assert page["total_count"] == 0


def test_queue_filters_by_status(client):
    process = make_process(client).json()
    rejected_doc = make_document(client, title="Rejected")
    approved_doc = make_document(client, title="Approved")

    rejected = submit(client, rejected_doc["id"], process["id"]).json()
    decide(client, rejected["id"], "legal", "reject", actor="sam")

    approved = submit(client, approved_doc["id"], process["id"]).json()
    approve_whole_workflow(client, approved["id"])

    pending = client.get(f"{PREFIX}/workflows", params={"status": PENDING}).json()
    assert pending["total_count"] == 0

    rejected_page = client.get(f"{PREFIX}/workflows", params={"status": REJECTED}).json()
    assert rejected_page["total_count"] == 1
    assert rejected_page["entries"][0]["id"] == rejected["id"]


def test_queue_entry_exposes_derived_state_without_mutating_stored_data(client):
    process = make_process(client).json()
    document = make_document(client)
    workflow = submit(client, document["id"], process["id"]).json()

    derived = workflow["derived"]

    assert derived["current_step_key"] == "legal"
    assert derived["total_steps"] == 2
    assert derived["approved_steps"] == 0
    assert derived["terminal"] is False
    # Exactly one step is actionable: the earliest still-Pending one.
    assert [s["key"] for s in derived["steps"] if s["actionable"]] == ["legal"]
    # `data` is returned exactly as persisted.
    assert "derived" not in workflow["data"]
    assert "actionable" not in workflow["data"]["steps"][0]


def test_get_unknown_workflow_is_404(client):
    assert client.get(f"{PREFIX}/workflows/approval_workflow_missing").status_code == 404


# --------------------------------------------------------------------------- #
# Decisions
# --------------------------------------------------------------------------- #


def test_steps_advance_in_order(client):
    """A later step must not be actionable before the earlier one is decided."""
    process = make_process(client).json()
    document = make_document(client)
    workflow = submit(client, document["id"], process["id"]).json()

    response = decide(client, workflow["id"], "brand", "approve", actor="dana")

    assert response.status_code == 409
    assert "not the step awaiting" in response.json()["detail"]


def test_approving_the_final_step_completes_the_workflow(client):
    process = make_process(client).json()
    document = make_document(client)
    workflow = submit(client, document["id"], process["id"]).json()

    final = approve_whole_workflow(client, workflow["id"])

    assert final["data"]["status"] == APPROVED
    assert final["data"]["decided_at"] is not None
    assert final["derived"]["current_step_key"] is None
    assert final["derived"]["approved_steps"] == 2
    assert final["derived"]["terminal"] is True


def test_rejecting_a_step_ends_the_workflow(client):
    process = make_process(client).json()
    document = make_document(client)
    workflow = submit(client, document["id"], process["id"]).json()

    response = decide(client, workflow["id"], "legal", "reject", actor="sam", comment="needs work")  # noqa: E501
    body = response.json()

    assert body["data"]["status"] == REJECTED
    assert body["data"]["steps"][0]["status"] == REJECTED
    assert body["data"]["steps"][0]["comment"] == "needs work"


def test_a_decision_is_refused_after_the_workflow_is_terminal(client):
    process = make_process(client).json()
    document = make_document(client)
    workflow = submit(client, document["id"], process["id"]).json()
    decide(client, workflow["id"], "legal", "reject", actor="sam")

    response = decide(client, workflow["id"], "legal", "approve", actor="sam")

    assert response.status_code == 409
    assert "no longer accepts decisions" in response.json()["detail"]


def test_only_an_assigned_approver_may_decide(client):
    process = make_process(client).json()
    document = make_document(client)
    workflow = submit(client, document["id"], process["id"]).json()

    response = decide(client, workflow["id"], "legal", "approve", actor="mallory")

    assert response.status_code == 409
    assert "not assigned" in response.json()["detail"]


def test_assigned_to_also_confers_the_decision(client):
    process = make_process(
        client, {"name": "Single owner", "steps": [{"key": "one", "assigned_to": "sam"}]}
    ).json()
    document = make_document(client)
    workflow = submit(client, document["id"], process["id"]).json()

    assert decide(client, workflow["id"], "one", "approve", actor="sam").status_code == 200
    assert decide(client, workflow["id"], "one", "approve", actor="dana").status_code == 409


def test_a_step_with_nobody_assigned_is_open_to_any_reviewer(client):
    process = make_process(client, {"name": "Open", "steps": [{"key": "anyone"}]}).json()
    document = make_document(client)
    workflow = submit(client, document["id"], process["id"]).json()

    assert decide(client, workflow["id"], "anyone", "approve", actor="whoever").status_code == 200


def test_decision_on_an_unknown_step_is_rejected(client):
    process = make_process(client).json()
    document = make_document(client)
    workflow = submit(client, document["id"], process["id"]).json()

    response = decide(client, workflow["id"], "nope", "approve", actor="sam")

    assert response.status_code == 400
    assert "not part of" in response.json()["detail"]


def test_an_unknown_verdict_is_rejected(client):
    process = make_process(client).json()
    document = make_document(client)
    workflow = submit(client, document["id"], process["id"]).json()

    response = decide(client, workflow["id"], "legal", "maybe", actor="sam")

    assert response.status_code == 400
    assert "approve" in response.json()["detail"]


def test_terminal_decision_notifies_watchers_once(client):
    process = make_process(client).json()
    document = make_document(client)
    workflow = submit(client, document["id"], process["id"]).json()

    decide(client, workflow["id"], "legal", "approve", actor="sam")
    mid = len(client.get("/api/records/notification").json()["records"])
    assert mid == 0, "watchers are not notified on an intermediate step"

    decide(client, workflow["id"], "brand", "approve", actor="dana")

    notes = client.get("/api/records/notification").json()["records"]
    assert [n["data"]["subscriber"] for n in notes] == ["dana"]
    assert notes[0]["data"]["kind"] == "workflow_approved"


def test_every_decision_is_audited(client):
    process = make_process(client).json()
    document = make_document(client)
    workflow = submit(client, document["id"], process["id"]).json()

    decide(client, workflow["id"], "legal", "approve", actor="sam")

    entries = client.get("/api/audit", params={"record_id": workflow["id"]}).json()["entries"]
    assert [e["action"] for e in entries] == ["update", "insert"]
    assert entries[0]["summary"].startswith("updated approval_workflow")


# --------------------------------------------------------------------------- #
# The audit source names the route that served the write
# --------------------------------------------------------------------------- #


def test_every_write_names_the_route_that_served_it(client):
    """The port brief's hard rule 4, pinned.

    The branch hard-coded a label (``"WF-009 publish"``) into each write, so the
    audit row did not say which route served it and could not drift away from
    what the app actually serves. Now the HTTP layer builds the string from
    ``router.prefix``, and the rows name real paths.
    """
    process = make_process(client).json()
    document = make_document(client)
    workflow = submit(client, document["id"], process["id"]).json()
    decide(client, workflow["id"], "legal", "approve", actor="sam")
    client.post(f"{PREFIX}/publish", json={"content": [document["id"]]})

    entries = client.get("/api/audit", params={"limit": 1000}).json()["entries"]
    sources = {entry["source"] for entry in entries}

    assert f"POST {PREFIX}/processes" in sources
    assert f"POST {PREFIX}/submissions" in sources
    assert f"POST {PREFIX}/workflows/{workflow['id']}/steps/legal" in sources
    assert f"POST {PREFIX}/publish" in sources

    # The branch's hard-coded labels are gone. Each of these was a literal in a
    # domain function, naming no route at all.
    assert not [s for s in sources if s.startswith("WF-009 ")], sources

    # Every source this feature produced names a path its router serves. The step
    # path is dynamic, so it is matched as ``<served template>/<anything>``.
    served = [route["path"] for route in client.get(f"/api/features/{FEATURE_ID}").json()["routes"]]
    mine = [s for s in sources if s.startswith(f"POST {PREFIX}")]
    assert mine, "no write in this flow was attributed to this feature"
    for source in mine:
        _, _, path = source.partition(" ")
        assert any(path == route or path.startswith(f"{route}/") for route in served), (
            f"{source!r} names a path this feature does not serve"
        )


def test_the_due_sweep_is_the_audited_source_of_a_scheduled_publish(client):
    """A write is attributed to the request that performed it.

    The schedule was created by ``/publish``, but the document is released later
    by the sweep, so the sweep is the route that served the write.
    """
    document = make_document(client, status="Draft")
    client.post(
        f"{PREFIX}/publish",
        json={"content": [document["id"]], "publish_at": "2030-01-01 09:00 AM"},
    )
    client.post(f"{PREFIX}/publications/due", json={"now": "2030-01-02T00:00:00Z"})

    entries = client.get("/api/audit", params={"record_id": document["id"], "limit": 100}).json()[
        "entries"
    ]
    assert entries[0]["source"] == f"POST {PREFIX}/publications/due"


# --------------------------------------------------------------------------- #
# Publishing immediately
# --------------------------------------------------------------------------- #


def test_publish_immediately_transitions_draft_to_published(client):
    document = make_document(client, status="Draft", versions=[{"version_id": "v1"}])

    body = client.post(f"{PREFIX}/publish", json={"content": [document["id"]]}).json()

    assert body["mode"] == "immediate"
    assert body["total_requests"] == 1
    assert body["total_succeeded"] == 1
    assert body["total_errors"] == 0

    reread = client.get(f"/api/records/document/{document['id']}").json()["data"]
    assert reread["status"] == "Published"
    assert reread["content_status"] == "Published"
    assert reread["published_version_id"] == "v1"
    assert reread["published_at"] is not None


def test_publish_releases_the_latest_version_regardless_of_request(client):
    """The researched endpoint 'always publishes the latest version'."""
    document = make_document(client, versions=[{"version_id": "v1"}, {"version_id": "v2"}])

    client.post(
        f"{PREFIX}/publish",
        json={"content": [{"id": document["id"], "version_id": "v1"}]},
    )

    reread = client.get(f"/api/records/document/{document['id']}").json()["data"]
    assert reread["published_version_id"] == "v2"


def test_publish_reports_already_published_as_an_error(client):
    document = make_document(client, status="published")

    body = client.post(f"{PREFIX}/publish", json={"content": [document["id"]]}).json()

    assert body["total_succeeded"] == 0
    assert body["total_errors"] == 1
    assert "already published" in body["errors"][0]


def test_publish_partly_succeeds_and_names_the_failure(client):
    """Documented partial-success semantics: totals plus per-item messages."""
    good = make_document(client, title="Good")
    bad = "document_missing"

    body = client.post(f"{PREFIX}/publish", json={"content": [good["id"], bad]}).json()

    assert body["total_requests"] == 2
    assert body["total_succeeded"] == 1
    assert body["total_errors"] == 1
    assert "not found" in body["errors"][0]
    # The good half really did land.
    assert client.get(f"/api/records/document/{good['id']}").json()["data"]["status"] == "Published"


def test_publish_rejects_an_empty_batch(client):
    response = client.post(f"{PREFIX}/publish", json={"content": []})

    assert response.status_code == 400
    assert "at least one item" in response.json()["detail"]


def test_immediate_publish_is_capped_at_ten_items(client):
    """'The documented maximum of 10 items per request applies to immediate publishing.'"""
    ids = [make_document(client, title=f"Doc {i}")["id"] for i in range(IMMEDIATE_BATCH_LIMIT + 1)]

    response = client.post(f"{PREFIX}/publish", json={"content": ids})

    assert response.status_code == 400
    assert "at most 10 items" in response.json()["detail"]


def test_exactly_ten_items_is_accepted(client):
    ids = [make_document(client, title=f"Doc {i}")["id"] for i in range(IMMEDIATE_BATCH_LIMIT)]

    body = client.post(f"{PREFIX}/publish", json={"content": ids}).json()

    assert body["total_succeeded"] == IMMEDIATE_BATCH_LIMIT


def test_scheduled_publish_is_capped_at_fifty_items(client):
    """'when publishAt is supplied for scheduled publishing the backend accepts up to 50 items.'"""
    ids = [make_document(client, title=f"Doc {i}")["id"] for i in range(SCHEDULED_BATCH_LIMIT + 1)]

    response = client.post(
        f"{PREFIX}/publish",
        json={"content": ids, "publish_at": "2030-01-01 09:00 AM"},
    )

    assert response.status_code == 400
    assert "at most 50 items" in response.json()["detail"]


def test_publish_lands_content_in_matching_dynamic_folders(client):
    document = make_document(client, metadata={"region": "emea", "audience": "external"})
    make_document(client, title="Other", metadata={"region": "amer"})
    folder = client.post(
        f"{PREFIX}/folders",
        json={"name": "EMEA external", "profile": "buyers", "matches": {"metadata.region": "emea"}},
    ).json()

    client.post(f"{PREFIX}/publish", json={"content": [document["id"]]})

    folders = client.get(f"{PREFIX}/folders").json()["entries"]
    assert folders[0]["id"] == folder["id"]
    assert folders[0]["data"]["content"] == [document["id"]]


def test_publish_records_the_comment_in_the_publication_history(client):
    """The researched `comment` is 'visible in content history'."""
    document = make_document(client)

    client.post(
        f"{PREFIX}/publish",
        json={"content": [document["id"]], "comment": "Cleared for release"},
    )

    history = client.get(f"{PREFIX}/publications").json()["entries"]
    assert history[0]["data"]["comment"] == "Cleared for release"


def test_publish_result_is_audited(client):
    document = make_document(client)

    client.post(f"{PREFIX}/publish", json={"content": [document["id"]]})

    publication = client.get(f"{PREFIX}/publications").json()["entries"][0]
    entries = client.get("/api/audit", params={"record_id": publication["id"]}).json()["entries"]
    assert [e["action"] for e in entries] == ["update", "insert"]


# --------------------------------------------------------------------------- #
# Publishing on a schedule
# --------------------------------------------------------------------------- #


def test_scheduled_publish_defers_the_transition(client):
    document = make_document(client, status="Draft")

    body = client.post(
        f"{PREFIX}/publish",
        json={"content": [document["id"]], "publish_at": "2030-01-01 09:00 AM"},
    ).json()

    assert body["mode"] == "scheduled"
    assert body["status"] == "scheduled"
    assert body["publish_at"] == "2030-01-01T09:00:00+00:00"
    # Nothing has happened to the document yet.
    assert client.get(f"/api/records/document/{document['id']}").json()["data"]["status"] == "Draft"


def test_documented_publish_at_form_is_read_as_utc(client):
    document = make_document(client)

    body = client.post(
        f"{PREFIX}/publish",
        json={"content": [document["id"]], "publish_at": "2030-01-01 09:00 AM"},
    ).json()

    assert body["publish_at"] == "2030-01-01T09:00:00+00:00"


def test_browser_datetime_local_form_is_read_as_utc(client):
    """`datetime-local` is what a browser date input produces; it has no offset."""
    document = make_document(client)

    body = client.post(
        f"{PREFIX}/publish",
        json={"content": [document["id"]], "publish_at": "2030-01-01T09:30"},
    ).json()

    assert body["publish_at"] == "2030-01-01T09:30:00+00:00"


def test_scheduled_publish_is_applied_once_the_instant_passes(client):
    document = make_document(client, status="Draft")
    client.post(
        f"{PREFIX}/publish",
        json={"content": [document["id"]], "publish_at": "2030-01-01 09:00 AM"},
    )

    body = client.post(f"{PREFIX}/publications/due", json={"now": "2030-01-02T00:00:00Z"}).json()

    assert body["count"] == 1
    assert body["applied"][0]["total_succeeded"] == 1
    assert (
        client.get(f"/api/records/document/{document['id']}").json()["data"]["status"]
        == "Published"
    )


def test_due_sweep_leaves_a_future_publication_alone(client):
    document = make_document(client, status="Draft")
    client.post(
        f"{PREFIX}/publish",
        json={"content": [document["id"]], "publish_at": "2030-01-01 09:00 AM"},
    )

    body = client.post(f"{PREFIX}/publications/due", json={"now": "2029-01-01T00:00:00Z"}).json()

    assert body["count"] == 0
    assert client.get(f"/api/records/document/{document['id']}").json()["data"]["status"] == "Draft"


def test_a_schedule_is_not_applied_twice(client):
    document = make_document(client, status="Draft")
    client.post(
        f"{PREFIX}/publish",
        json={"content": [document["id"]], "publish_at": "2030-01-01 09:00 AM"},
    )

    first = client.post(f"{PREFIX}/publications/due", json={"now": "2030-01-02T00:00:00Z"}).json()
    second = client.post(f"{PREFIX}/publications/due", json={"now": "2030-01-03T00:00:00Z"}).json()

    assert first["count"] == 1
    assert second["count"] == 0
    assert client.get("/api/records/document").json()["count"] == 1


def test_a_schedule_covering_unpublishable_content_is_refused_whole(client):
    """A promise about a specific instant is not made partly."""
    good = make_document(client, title="Good", status="Draft")
    bad = "document_missing"

    body = client.post(
        f"{PREFIX}/publish",
        json={"content": [good["id"], bad], "publish_at": "2030-01-01 09:00 AM"},
    ).json()

    assert body["status"] == "rejected"
    assert body["total_errors"] == 1
    assert body["publication"] is None
    assert client.get(f"{PREFIX}/publications").json()["count"] == 0


def test_unparseable_publish_at_is_rejected(client):
    document = make_document(client)

    response = client.post(
        f"{PREFIX}/publish",
        json={"content": [document["id"]], "publish_at": "next tuesday"},
    )

    assert response.status_code == 400
    assert "not a recognised date" in response.json()["detail"]


# --------------------------------------------------------------------------- #
# Subscriber notification
# --------------------------------------------------------------------------- #


def test_publish_notifies_subscribers_by_default(client):
    """`IsSendNotification` defaults to true."""
    room = make_room(client)
    client.post(
        f"{PREFIX}/subscriptions",
        json={"subscriber": "buyer@example.com"},
        params={"room_id": room["id"]},
    )
    document = make_document(client, room_id=room["id"])

    client.post(f"{PREFIX}/publish", json={"content": [document["id"]]})

    notes = client.get("/api/records/notification").json()["records"]
    assert [n["data"]["subscriber"] for n in notes] == ["buyer@example.com"]
    assert notes[0]["data"]["kind"] == "publish"


def test_publish_can_be_silent(client):
    room = make_room(client)
    client.post(
        f"{PREFIX}/subscriptions",
        json={"subscriber": "buyer@example.com"},
        params={"room_id": room["id"]},
    )
    document = make_document(client, room_id=room["id"])

    client.post(
        f"{PREFIX}/publish",
        json={"content": [document["id"]], "is_send_notification": False},
    )

    assert client.get("/api/records/notification").json()["count"] == 0
    # Still published: the silence is about notification, not publication.
    assert (
        client.get(f"/api/records/document/{document['id']}").json()["data"]["status"]
        == "Published"
    )


def test_a_subscriber_that_does_not_resolve_yields_the_documented_warning(client):
    """'Content was published but notification delivery failed' - as a warning."""
    room = make_room(client)
    document = make_document(client, room_id=room["id"])

    body = client.post(
        f"{PREFIX}/publish",
        json={"content": [document["id"]], "subscribers": ["gone@example.com"]},
    ).json()

    assert body["total_succeeded"] == 1
    assert body["total_errors"] == 0
    assert body["total_warnings"] == 1
    assert "notification delivery failed" in body["warnings"][0]


def test_a_deactivated_subscription_is_an_opt_out_not_a_failure(client):
    room = make_room(client)
    subscription = client.post(
        f"{PREFIX}/subscriptions",
        json={"subscriber": "buyer@example.com", "active": False},
        params={"room_id": room["id"]},
    ).json()
    document = make_document(client, room_id=room["id"])

    body = client.post(
        f"{PREFIX}/publish",
        json={"content": [document["id"]], "subscribers": [subscription["data"]["subscriber"]]},
    ).json()

    assert body["total_succeeded"] == 1
    assert body["total_warnings"] == 0
    assert client.get("/api/records/notification").json()["count"] == 0


def test_a_room_with_no_subscribers_publishes_without_warning(client):
    document = make_document(client)

    body = client.post(f"{PREFIX}/publish", json={"content": [document["id"]]}).json()

    assert body["total_succeeded"] == 1
    assert body["total_warnings"] == 0


# --------------------------------------------------------------------------- #
# Dynamic folders
# --------------------------------------------------------------------------- #


def test_folder_requires_a_name_and_a_match_rule(client):
    assert client.post(f"{PREFIX}/folders", json={"matches": {"a": 1}}).status_code == 400
    assert client.post(f"{PREFIX}/folders", json={"name": "F"}).status_code == 400


def test_folder_rejects_a_rule_that_the_index_cannot_compare(client):
    response = client.post(f"{PREFIX}/folders", json={"name": "F", "matches": {"a": None}})

    assert response.status_code == 400
    assert "dotted path" in response.json()["detail"]


def test_folder_rules_match_nested_metadata_paths(client):
    """Folder rules mean exactly what ?where= means, including nested paths."""
    matching = make_document(client, metadata={"region": "emea"})
    other = make_document(client, title="Other", metadata={"region": "amer"})
    client.post(
        f"{PREFIX}/folders",
        json={"name": "EMEA", "matches": {"metadata.region": "emea"}},
    )

    client.post(f"{PREFIX}/publish", json={"content": [matching["id"], other["id"]]})

    landed = client.get(f"{PREFIX}/folders").json()["entries"][0]["data"]["content"]
    assert landed == [matching["id"]]


def test_folder_requires_every_rule_to_match(client):
    document = make_document(client, metadata={"region": "emea", "audience": "internal"})
    client.post(
        f"{PREFIX}/folders",
        json={
            "name": "EMEA external",
            "matches": {"metadata.region": "emea", "metadata.audience": "external"},
        },
    )

    client.post(f"{PREFIX}/publish", json={"content": [document["id"]]})

    assert client.get(f"{PREFIX}/folders").json()["entries"][0]["data"]["content"] == []


def test_a_folder_rule_is_scoped_to_library_content(client):
    """A rule is asked of the content collection, not of a global index.

    The branch asked a global index question ("which record ids have
    ``metadata.region = emea``") through ``store.db.query_index``, which the
    ``RecordStore`` facade does not forward. The rule is now asked as
    ``find(document, {metadata.region: emea})``: the same dynamic index, the same
    nested-path semantics, scoped to the collection a folder can actually
    collect. This pins the scope, because a record of some other collection
    carrying the same metadata no longer satisfies a folder rule.
    """
    room = make_room(client)
    document = make_document(client, room_id=room["id"], metadata={"region": "emea"})
    # A different collection that happens to carry the same path and value.
    client.post(
        "/api/records/promotion",
        json={"title": "EMEA campaign", "metadata": {"region": "emea"}},
        params={"room_id": room["id"]},
    )
    client.post(
        f"{PREFIX}/folders",
        json={"name": "EMEA", "matches": {"metadata.region": "emea"}},
    )

    client.post(f"{PREFIX}/publish", json={"content": [document["id"]]})

    landed = client.get(f"{PREFIX}/folders").json()["entries"][0]["data"]["content"]
    assert landed == [document["id"]]


def test_a_document_lands_in_a_folder_only_once(client):
    document = make_document(client, metadata={"region": "emea"})
    client.post(f"{PREFIX}/folders", json={"name": "EMEA", "matches": {"metadata.region": "emea"}})

    # Publish twice: the second attempt is an error, and must not duplicate.
    client.post(f"{PREFIX}/publish", json={"content": [document["id"]]})
    second = client.post(f"{PREFIX}/publish", json={"content": [document["id"]]}).json()

    assert second["total_errors"] == 1
    assert client.get(f"{PREFIX}/folders").json()["entries"][0]["data"]["content"] == [
        document["id"]
    ]


# --------------------------------------------------------------------------- #
# Cross-cutting promises
# --------------------------------------------------------------------------- #


def test_end_to_end_approve_then_publish_is_fully_audited(client):
    """The whole workflow, and every write in it, is visible in the audit log."""
    process = make_process(client).json()
    document = make_document(client, status="Draft")
    workflow = submit(client, document["id"], process["id"], actor="dana").json()
    approve_whole_workflow(client, workflow["id"])

    client.post(f"{PREFIX}/publish", json={"content": [document["id"]]}, params={"actor": "dana"})

    by_collection = {}
    for entry in client.get("/api/audit", params={"limit": 1000}).json()["entries"]:
        by_collection.setdefault(entry["collection"], []).append(entry["action"])

    assert by_collection["approval_workflow"] == ["update", "update", "insert"]
    assert by_collection["publication"] == ["update", "insert"]
    assert by_collection["document"] == ["update", "insert"]


def test_wf009_adds_no_migration(client):
    """Schema flexibility is a hard requirement: no typed column, no new table."""
    document = make_document(client, status="Draft", something_new={"deeply": {"nested": 7}})

    client.post(f"{PREFIX}/publish", json={"content": [document["id"]]})

    reread = client.get(f"/api/records/document/{document['id']}").json()
    assert reread["data"]["something_new"] == {"deeply": {"nested": 7}}
    assert reread["collection"] == "document"


def test_a_team_added_step_field_survives_the_decision_round_trip(client):
    """A field a team invents on a step must not be dropped by the review flow."""
    process = make_process(
        client,
        {
            "name": "With a custom field",
            "steps": [{"key": "legal", "soc2_control": "CC6.1", "reviewer_note": None}],
        },
    ).json()
    document = make_document(client)
    workflow = submit(client, document["id"], process["id"]).json()

    stored = workflow["data"]["steps"][0]
    assert stored["soc2_control"] == "CC6.1"

    after = decide(client, workflow["id"], "legal", "approve", actor="sam").json()
    assert after["data"]["steps"][0]["soc2_control"] == "CC6.1"


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #


def test_the_seed_hook_populates_every_panel_of_the_page(client):
    """A feature whose page is empty in the demo is a feature nobody can review.

    The seeder calls this rather than the branch editing ``backend/seed.py``, so
    the demo rows live in the feature. Driven directly here because the test
    suite does not run the whole seeder.
    """
    from datetime import datetime, timezone

    from dsr.db.audited import AuditedDatabase
    from dsr.features.wf009_publishing import seed

    tmp = tempfile.TemporaryDirectory()
    try:
        db = AuditedDatabase(
            str(Path(tmp.name) / "seed.db"), mirror_dir=str(Path(tmp.name) / "audit")
        )
        try:
            room = db.create("room", {"name": "Northwind"}, actor="dana", source="seed")
            summary = seed(
                db, {"room_ids": [(room["id"], "Northwind")], "now": datetime.now(timezone.utc)}
            )

            assert "approval process" in summary
            assert db.count("approval_process") == 1
            assert db.count("dynamic_folder") == 2
            assert db.count("subscription") == 2
            assert db.count("publication") == 0, "seeding must not publish anything"

            pending = db.find("approval_workflow", {"status": PENDING})
            assert len(pending) == 1, "the queue is not empty on first load"
            cleared = db.find("approval_workflow", {"status": APPROVED})
            assert len(cleared) == 1, "the publish action is reachable without clicking"
        finally:
            db.close()
    finally:
        tmp.cleanup()
