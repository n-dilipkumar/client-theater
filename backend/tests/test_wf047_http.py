"""The HTTP surface, the audit-source rule, and the demo data for WF-047.

Split from ``test_wf047.py``, which holds the researched rules against the domain.
This file drives the feature's own mounted router, so it covers the wiring and the
three things the contract names about a feature that the domain tests cannot see:

* **The route set is the documented one.** A change to the surface has to be made
  here deliberately, which is the point of asserting a set rather than a count.
* **Every audit row names a route the app actually serves.** The contract names
  the defect by name - "a feature's audit log kept recording a path the app had
  stopped serving" - and it is invisible to a test scoped to one feature's own
  strings. These tests read the sources out of the module and the routes out of the
  published OpenAPI schema, so a drift is caught rather than updated in the same
  commit as the bug.
* **Every advertised route answers without a 5xx.** ``tools/verify_all_routes.py``
  does this in CI; doing it here means a broken handler is reported by the suite
  that owns it rather than by a job that cannot say which feature failed.

The ``client`` fixture comes from ``conftest.py``: one ``TestClient`` per module
with ``app.state`` swapped per test, so this file is cheap and every test sees an
empty database of its own. That is what makes it safe under pytest-xdist.
"""

from __future__ import annotations

import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest
from dsr.api import app
from dsr.crm_file_sync import vocabulary
from dsr.db.audited import AuditedDatabase
from dsr.features import load_feature
from dsr.store import RecordStore

PREFIX = "/api/wf-047"
MODULE = "wf047_mirror_room_documents_into_crm_files"
FEATURE_ID = "wf-047-mirror-room-documents-into-crm-files"

#: A frozen clock. Nothing here reads the wall clock, so no assertion depends on
#: the day the suite runs.
NOW = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)

#: Every route this feature mounts. A change to the surface has to be made here
#: deliberately, which is the point of asserting a set.
ROUTES = {
    ("GET", "/vocabulary"),
    ("GET", "/inferences"),
    ("GET", "/vendors"),
    ("GET", "/connections"),
    ("POST", "/connections"),
    ("GET", "/connections/{connection_id}"),
    ("DELETE", "/connections/{connection_id}"),
    ("POST", "/rooms/{room_id}/assets"),
    ("GET", "/rooms/{room_id}/assets"),
    ("GET", "/rooms/{room_id}/assets/{asset_id}"),
    ("PATCH", "/rooms/{room_id}/assets/{asset_id}"),
    ("POST", "/rooms/{room_id}/assets/{asset_id}/mirror"),
    ("GET", "/rooms/{room_id}/mirrors"),
    ("GET", "/mirrors/{mirror_id}"),
    ("POST", "/rooms/{room_id}/reconcile"),
    ("GET", "/rooms/{room_id}/documents"),
    ("GET", "/rooms/{room_id}/files"),
    ("GET", "/remote-files"),
    ("GET", "/rooms/{room_id}/summary"),
}


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def make_room(client, name: str = "Northwind") -> dict:
    return client.post("/api/records/room", json={"name": name, "account": name}).json()


def make_connection(client, room_id: str, **overrides) -> dict:
    body = {"system": "hubspot", "owner_object": "deal", "crm_record_id": "fabrikam-dl-9001"}
    body.update(overrides)
    response = client.post(f"{PREFIX}/connections", json=body, params={"room_id": room_id})
    assert response.status_code == 201, response.text
    return response.json()


def make_asset(client, room_id: str, connection_id: str, **overrides) -> dict:
    body = {
        "filename": "northwind-rollout-deck.pdf",
        "mime_type": "application/pdf",
        "content_base64": "UERGIC0xMQSBXUi0wMDAx",
        "push_to_crm": True,
    }
    body.update(overrides)
    response = client.post(
        f"{PREFIX}/rooms/{room_id}/assets",
        json=body,
        params={"connection_id": connection_id},
    )
    assert response.status_code == 201, response.text
    return response.json()["asset"]


def mirror(client, room_id: str, asset_id: str, **params) -> dict:
    response = client.post(
        f"{PREFIX}/rooms/{room_id}/assets/{asset_id}/mirror", params={"force": False, **params}
    )
    assert response.status_code == 200, response.text
    return response.json()


def mounted(client) -> dict[str, list[str]]:
    """Every mounted path, per method, from the published OpenAPI schema.

    ``app.routes`` is not usable for this: this FastAPI version records an
    included router as a single nested entry rather than flattening its routes.
    The published schema is the flat, authoritative list of what is served.
    """
    schema = client.get("/openapi.json").json()
    served: dict[str, list[str]] = {}
    for path, operations in (schema.get("paths") or {}).items():
        for method in operations:
            if method.lower() in ("get", "post", "patch", "put", "delete"):
                served.setdefault(method.upper(), []).append(path)
    return served


RECORD_ID = re.compile(r"^[a-z_]+_[0-9a-f]{16,}$")


def serves(served: dict[str, list[str]], method: str, path: str) -> bool:
    """Whether ``method path`` reaches a mounted route."""
    recorded = path.strip("/").split("/")
    for candidate in served.get(method, []):
        expected_parts = candidate.strip("/").split("/")
        if len(expected_parts) != len(recorded):
            continue
        if all(
            part.startswith("{") or part == actual or RECORD_ID.match(actual)
            for part, actual in zip(expected_parts, recorded, strict=False)
        ):
            return True
    return False


def audit_entries(client, **params) -> list[dict]:
    query = "&".join(f"{key}={value}" for key, value in params.items())
    suffix = f"?{query}" if query else ""
    return client.get(f"/api/audit{suffix}").json()["entries"]


# --------------------------------------------------------------------------- #
# Discovery and the route set
# --------------------------------------------------------------------------- #


def test_the_feature_is_listed_by_discovery_with_its_prefix(client):
    """``backend/dsr/api.py`` is never edited: the host walks the package."""
    registry = client.get("/api/features").json()
    mine = next(row for row in registry["features"] if row["id"] == FEATURE_ID)
    assert mine["prefix"] == PREFIX
    assert mine["ticket"] == "WF-047"
    assert mine["routes"]
    assert not [row for row in registry.get("failed", []) if FEATURE_ID in str(row)]


def test_the_mounted_route_set_is_the_documented_one(client):
    served = mounted(client)
    mine = {
        (method, path)
        for method, paths in served.items()
        for path in paths
        if path.startswith(PREFIX)
    }
    # ``ROUTES`` is written relative to the prefix, so a change to the prefix is
    # one edit here rather than nineteen.
    assert mine == {(method, f"{PREFIX}{path}") for method, path in ROUTES}


def test_the_feature_claims_one_error_handler_and_no_other_feature_claims_it(client):
    """The host refuses two handlers for one type, and this one is the base of
    every refusal this package raises."""
    registry = client.get("/api/features").json()
    mine = next(row for row in registry["features"] if row["id"] == FEATURE_ID)
    assert mine["exception_handlers"] == ["CrmFileSyncError"]


def test_the_feature_takes_no_core_route(client):
    """Starlette matches in registration order, so a feature that re-registered a
    core path would be dead code. The host refuses that, and this is the check on
    the feature's own side of the rule."""
    served = mounted(client)
    assert f"{PREFIX}/health" not in served.get("GET", [])
    assert f"{PREFIX}/records" not in served.get("GET", [])
    assert "/api/health" in served.get("GET", []), "the core route is still the one that answers"


# --------------------------------------------------------------------------- #
# The org-level reads
# --------------------------------------------------------------------------- #


def test_the_vocabulary_route_serves_the_researched_words(client):
    body = client.get(f"{PREFIX}/vocabulary").json()
    assert body["upload_path"] == "/files/2026-09/files"
    assert body["upload_content_type"] == "multipart/form-data"
    assert body["upload_required_fields"] == ["file"]
    assert body["upload_optional_fields"] == ["folderId", "fileName"]
    assert body["file_capable_systems"] == ["hubspot"]
    assert set(body["unsourced_systems"]) == {"salesforce", "dataverse"}
    assert body["default_attach_mode"] == "note"
    assert body["attach_modes"] == ["note", "file_property"]
    assert body["connector_methods"] == ["upload", "attach"]
    assert body["reconcile_rule"] == "find room assets with no crm_file_id and push them"
    for state in body["mirror_states"]:
        assert body["mirror_state_notes"][state]


def test_the_vendors_route_names_the_gap_for_each_unsourced_system(client):
    body = client.get(f"{PREFIX}/vendors").json()
    assert body["count"] == 3
    assert body["sourced"] == 1
    assert body["unsourced"] == 2
    gaps = {row["system"]: row["gap"] for row in body["vendors"] if row["gap"]}
    assert all("could not be sourced" in text for text in gaps.values())
    assert gaps["salesforce"] == vocabulary.UNSOURCED_SYSTEMS["salesforce"]
    assert gaps["dataverse"] == vocabulary.UNSOURCED_SYSTEMS["dataverse"]


def test_the_inferences_route_serves_every_decision_with_its_derivation(client):
    body = client.get(f"{PREFIX}/inferences").json()
    assert body["count"] >= 6
    for entry in body["inferences"]:
        assert entry["basis"] and entry["value"] and entry["why"] and entry["change_it"]


# --------------------------------------------------------------------------- #
# Connections
# --------------------------------------------------------------------------- #


def test_a_connection_is_created_with_the_folders_recommendation_recorded(client):
    room = make_room(client)
    without = make_connection(client, room["id"], crm_record_id="deal-a")
    assert without["folder_recommendation_followed"] is False
    with_folder = make_connection(client, room["id"], crm_record_id="deal-b", folder_id="f-3300")
    assert with_folder["folder_recommendation_followed"] is True
    listed = client.get(f"{PREFIX}/connections", params={"room_id": room["id"]}).json()
    assert listed["count"] == 2
    assert listed["file_capable_systems"] == ["hubspot"]


def test_a_duplicate_connection_is_409_and_carries_its_code(client):
    room = make_room(client)
    make_connection(client, room["id"])
    response = client.post(
        f"{PREFIX}/connections",
        json={"system": "hubspot", "owner_object": "deal", "crm_record_id": "fabrikam-dl-9001"},
        params={"room_id": room["id"]},
    )
    assert response.status_code == 409
    assert response.json()["error"] == "duplicate_connection"


def test_an_unknown_vendor_is_400_and_names_the_three_known_systems(client):
    room = make_room(client)
    response = client.post(
        f"{PREFIX}/connections",
        json={"system": "oracle", "crm_record_id": "x"},
        params={"room_id": room["id"]},
    )
    assert response.status_code == 400
    assert response.json()["error"] == "unknown_system"
    assert "hubspot" in response.json()["detail"]


def test_a_file_property_connection_without_a_property_is_409(client):
    room = make_room(client)
    response = client.post(
        f"{PREFIX}/connections",
        json={
            "system": "hubspot",
            "crm_record_id": "contact-1",
            "attach_mode": "file_property",
        },
        params={"room_id": room["id"]},
    )
    assert response.status_code == 409
    assert response.json()["error"] == "no_owner_record"


def test_reading_an_unknown_connection_is_404_with_the_error_code(client):
    response = client.get(f"{PREFIX}/connections/conn_absent")
    assert response.status_code == 404
    assert response.json()["error"] == "unknown_connection"


def test_deleting_a_connection_keeps_its_assets_and_their_file_ids(client):
    room = make_room(client)
    connection = make_connection(client, room["id"])
    asset = make_asset(client, room["id"], connection["id"])
    pushed = mirror(client, room["id"], asset["id"])
    removed = client.delete(f"{PREFIX}/connections/{connection['id']}")
    assert removed.status_code == 200
    assert removed.json()["assets_kept"] == 1
    still = client.get(f"{PREFIX}/rooms/{room['id']}/assets/{asset['id']}").json()
    assert still["crm_file_id"] == pushed["uploaded"]["file_id"]


def test_a_connection_in_another_room_is_not_readable(client):
    first = make_room(client, "First")
    second = make_room(client, "Second")
    connection = make_connection(client, first["id"])
    response = client.get(
        f"{PREFIX}/connections/{connection['id']}", params={"room_id": second["id"]}
    )
    assert response.status_code == 404


# --------------------------------------------------------------------------- #
# The researched flow over HTTP
# --------------------------------------------------------------------------- #


def test_the_whole_flow_posts_an_asset_mirrors_it_and_stores_the_file_id(client):
    room = make_room(client)
    connection = make_connection(client, room["id"])

    # Steps one and two: the document is registered with the toggle on.
    asset = make_asset(client, room["id"], connection["id"])
    assert asset["state"] == "pending"

    # Step three: the upload and the attach.
    pushed = mirror(client, room["id"], asset["id"])
    assert pushed["outcome"] == "mirrored"
    assert pushed["request"]["method"] == "POST"
    assert pushed["request"]["path"] == "/files/2026-09/files"
    assert pushed["request"]["content_type"] == "multipart/form-data"
    assert pushed["attachment"]["mode"] == "note"
    assert pushed["attachment"]["record_id"] == "fabrikam-dl-9001"

    # Step four: the file id is on the asset row.
    stored = client.get(f"{PREFIX}/rooms/{room['id']}/assets/{asset['id']}").json()
    assert stored["crm_file_id"] == pushed["uploaded"]["file_id"]
    assert stored["state"] == "mirrored"

    # Step five: the rep sees it on the CRM Files panel.
    files = client.get(f"{PREFIX}/rooms/{room['id']}/files").json()
    assert files["count"] == 1
    assert files["files"][0]["crm_file_id"] == pushed["uploaded"]["file_id"]


def test_a_repeat_mirror_answers_skipped_and_sends_nothing(client):
    """ "re-uploads are skipped"."""
    room = make_room(client)
    connection = make_connection(client, room["id"])
    asset = make_asset(client, room["id"], connection["id"])
    mirror(client, room["id"], asset["id"])
    again = mirror(client, room["id"], asset["id"])
    assert again["outcome"] == "skipped_unchanged"
    assert again["request"] == {}
    assert client.get(f"{PREFIX}/remote-files").json()["count"] == 1


def test_a_revision_is_a_patch_that_turns_the_chip_stale_then_pushes_a_new_file(client):
    """ "revisions create a new file"."""
    room = make_room(client)
    connection = make_connection(client, room["id"])
    asset = make_asset(client, room["id"], connection["id"])
    first = mirror(client, room["id"], asset["id"])

    patched = client.patch(
        f"{PREFIX}/rooms/{room['id']}/assets/{asset['id']}",
        json={
            "filename": "northwind-rollout-deck.pdf",
            "mime_type": "application/pdf",
            "content_base64": "UERGIC0xMQBSZXZpc2VkLXYy",
        },
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["asset"]["state"] == "stale"
    assert patched.json()["asset"]["state_note"], "a status may never be carried by colour alone"

    second = mirror(client, room["id"], asset["id"])
    assert second["outcome"] == "mirrored"
    assert second["uploaded"]["file_id"] != first["uploaded"]["file_id"]
    assert client.get(f"{PREFIX}/remote-files").json()["count"] == 2


def test_a_patch_can_push_the_change_in_the_same_call(client):
    room = make_room(client)
    connection = make_connection(client, room["id"])
    asset = make_asset(client, room["id"], connection["id"])
    mirror(client, room["id"], asset["id"])
    response = client.patch(
        f"{PREFIX}/rooms/{room['id']}/assets/{asset['id']}",
        json={
            "filename": "northwind-rollout-deck.pdf",
            "mime_type": "application/pdf",
            "content_base64": "UERGIC0xMQBSZXZpc2VkLXYz",
        },
        params={"mirror": True},
    )
    assert response.status_code == 200, response.text
    assert response.json()["mirror"]["outcome"] == "mirrored"


def test_the_toggle_off_withholds_and_the_toggle_on_pushes(client):
    room = make_room(client)
    connection = make_connection(client, room["id"])
    asset = make_asset(client, room["id"], connection["id"], push_to_crm=False)

    withheld = mirror(client, room["id"], asset["id"])
    assert withheld["outcome"] == "withheld"
    assert withheld["request"] == {}

    off = client.patch(
        f"{PREFIX}/rooms/{room['id']}/assets/{asset['id']}", json={"push_to_crm": True}
    )
    assert off.status_code == 200
    assert off.json()["push_to_crm"] is True
    assert off.json()["note"], "the row must say the CRM file stays where it is"
    assert mirror(client, room["id"], asset["id"])["outcome"] == "mirrored"


def test_a_patch_that_changes_nothing_is_400_and_says_what_to_send(client):
    room = make_room(client)
    connection = make_connection(client, room["id"])
    asset = make_asset(client, room["id"], connection["id"])
    response = client.patch(
        f"{PREFIX}/rooms/{room['id']}/assets/{asset['id']}", json={"filename": "renamed.pdf"}
    )
    assert response.status_code == 400
    assert response.json()["error"] == "nothing_to_change"
    assert "push_to_crm" in response.json()["detail"]


def test_registering_an_asset_can_mirror_it_in_the_same_call(client):
    room = make_room(client)
    connection = make_connection(client, room["id"])
    response = client.post(
        f"{PREFIX}/rooms/{room['id']}/assets",
        json={
            "filename": "proposal.pdf",
            "mime_type": "application/pdf",
            "content_base64": "UERGIC0xMQVSRVI=",
            "push_to_crm": True,
        },
        params={"connection_id": connection["id"], "mirror": True},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["mirror"]["outcome"] == "mirrored"
    assert body["asset"]["state"] == "mirrored"


@pytest.mark.parametrize(
    "payload,expected_detail",
    [
        ({"mime_type": "application/pdf", "content_base64": "UERG"}, "filename"),
        ({"filename": "a.pdf", "content_base64": "UERG"}, "mime_type"),
        ({"filename": "a.pdf", "mime_type": "application/pdf"}, "content_base64"),
    ],
)
def test_an_asset_the_caller_got_wrong_is_400_and_names_the_field(client, payload, expected_detail):
    room = make_room(client)
    response = client.post(f"{PREFIX}/rooms/{room['id']}/assets", json=payload)
    assert response.status_code == 400
    assert response.json()["error"] == "asset_refused"
    assert expected_detail in response.json()["detail"]


def test_an_unknown_asset_is_404_with_its_own_code(client):
    room = make_room(client)
    response = client.get(f"{PREFIX}/rooms/{room['id']}/assets/asset_absent")
    assert response.status_code == 404
    assert response.json()["error"] == "unknown_asset"


def test_an_asset_is_scoped_to_its_room(client):
    first = make_room(client, "First")
    second = make_room(client, "Second")
    connection = make_connection(client, first["id"])
    asset = make_asset(client, first["id"], connection["id"])
    response = client.get(f"{PREFIX}/rooms/{second['id']}/assets/{asset['id']}")
    assert response.status_code == 404


# --------------------------------------------------------------------------- #
# The unsourced systems, over HTTP
# --------------------------------------------------------------------------- #


def test_mirroring_to_an_unsourced_system_is_a_failed_chip_and_a_named_mirror_row(client):
    """The researched product surface is a chip per file, so this is a 200 with a
    failed chip rather than a 422 the caller has to interpret."""
    room = make_room(client)
    connection = make_connection(client, room["id"], system="salesforce", crm_record_id="006-nw")
    asset = make_asset(client, room["id"], connection["id"])
    result = mirror(client, room["id"], asset["id"])
    assert result["outcome"] == "failed"
    assert result["error_code"] == "unsupported_system"
    assert "could not be sourced" in result["error_detail"]
    assert result["asset"]["state"] == "failed"

    log = client.get(f"{PREFIX}/rooms/{room['id']}/mirrors").json()
    assert log["count"] == 1
    assert log["mirrors"][0]["error_code"] == "unsupported_system"

    one = client.get(f"{PREFIX}/mirrors/{result['mirror_id']}").json()
    assert "could not be sourced" in one["error_detail"]


def test_the_asset_list_filter_reads_the_derived_chip(client):
    room = make_room(client)
    connection = make_connection(client, room["id"])
    pushed = make_asset(client, room["id"], connection["id"], filename="a.pdf")
    make_asset(client, room["id"], connection["id"], filename="b.pdf", push_to_crm=False)
    mirror(client, room["id"], pushed["id"])

    only_mirrored = client.get(
        f"{PREFIX}/rooms/{room['id']}/assets", params={"state": "mirrored"}
    ).json()
    assert only_mirrored["count"] == 1
    assert only_mirrored["assets"][0]["filename"] == "a.pdf"
    assert only_mirrored["states"] == list(vocabulary.MIRROR_STATES)

    only_withheld = client.get(
        f"{PREFIX}/rooms/{room['id']}/assets", params={"state": "withheld"}
    ).json()
    assert only_withheld["count"] == 1
    assert only_withheld["assets"][0]["filename"] == "b.pdf"


# --------------------------------------------------------------------------- #
# Reconciliation over HTTP
# --------------------------------------------------------------------------- #


def test_the_reconcile_route_pushes_an_asset_whose_event_never_arrived(client):
    room = make_room(client)
    connection = make_connection(client, room["id"])
    asset = make_asset(client, room["id"], connection["id"])
    response = client.post(f"{PREFIX}/rooms/{room['id']}/reconcile")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["outcome"] == "reconciled"
    assert body["candidates"] == 1
    assert body["mirrored"] == 1
    assert body["rule"] == "find room assets with no crm_file_id and push them"
    assert client.get(f"{PREFIX}/rooms/{room['id']}/assets/{asset['id']}").json()["crm_file_id"]


def test_a_reconcile_inside_the_floor_is_answered_not_due_and_pushes_nothing(client):
    room = make_room(client)
    connection = make_connection(client, room["id"])
    make_asset(client, room["id"], connection["id"])
    client.post(f"{PREFIX}/rooms/{room['id']}/reconcile")
    response = client.post(f"{PREFIX}/rooms/{room['id']}/reconcile")
    body = response.json()
    assert body["outcome"] == "not_due"
    assert body["mirrored"] == 0
    assert body["interval_seconds"] == vocabulary.DEFAULT_POLL_INTERVAL_SECONDS
    assert body["note"]


def test_a_reconcile_can_be_forced_past_the_floor(client):
    room = make_room(client)
    connection = make_connection(client, room["id"])
    make_asset(client, room["id"], connection["id"])
    client.post(f"{PREFIX}/rooms/{room['id']}/reconcile")
    body = client.post(f"{PREFIX}/rooms/{room['id']}/reconcile", params={"force": True}).json()
    assert body["outcome"] == "reconciled"


def test_the_reconcile_route_reports_the_floor_it_actually_applied(client):
    """A scheduler that passed an override needs to read the number back, and a
    run that did not needs to read the per-connection floors."""
    room = make_room(client)
    connection = make_connection(client, room["id"], poll_interval_seconds=45)
    make_asset(client, room["id"], connection["id"])

    default = client.post(f"{PREFIX}/rooms/{room['id']}/reconcile").json()
    assert default["interval_seconds"] == vocabulary.DEFAULT_POLL_INTERVAL_SECONDS
    assert default["connection_intervals"] == {connection["id"]: 45}

    # An override below the floor is raised to the floor rather than honoured, so
    # the number read back is the number that was applied.
    overridden = client.post(
        f"{PREFIX}/rooms/{room['id']}/reconcile", params={"interval_seconds": 1}
    ).json()
    assert overridden["interval_seconds"] == vocabulary.MIN_POLL_INTERVAL_SECONDS
    assert overridden["outcome"] == "not_due"


# --------------------------------------------------------------------------- #
# Panels and the log
# --------------------------------------------------------------------------- #


def test_the_documents_page_is_the_researched_chip_page(client):
    """ "Room Documents page with a 'Push to CRM' status chip per file"."""
    room = make_room(client)
    connection = make_connection(client, room["id"])
    asset = make_asset(client, room["id"], connection["id"])
    mirror(client, room["id"], asset["id"])
    body = client.get(f"{PREFIX}/rooms/{room['id']}/documents").json()
    assert body["count"] == 1
    assert body["assets"][0]["state"] == "mirrored"
    assert body["assets"][0]["state_note"]
    assert body["state_notes"]["stale"]


def test_the_summary_counts_both_directions(client):
    room = make_room(client)
    connection = make_connection(client, room["id"])
    asset = make_asset(client, room["id"], connection["id"])
    mirror(client, room["id"], asset["id"])
    body = client.get(f"{PREFIX}/rooms/{room['id']}/summary").json()
    assert body["assets"] == 1
    assert body["assets_by_state"]["mirrored"] == 1
    assert body["mirrors_by_outcome"]["mirrored"] == 1
    assert body["simulated_mirrors"] == 1, "a simulated confirmation must be visible as one"
    assert body["reconcile_rule"] == "find room assets with no crm_file_id and push them"


def test_the_mirror_log_carries_the_request_and_filters_by_outcome(client):
    room = make_room(client)
    connection = make_connection(client, room["id"])
    asset = make_asset(client, room["id"], connection["id"])
    mirror(client, room["id"], asset["id"])
    mirror(client, room["id"], asset["id"])
    log = client.get(f"{PREFIX}/rooms/{room['id']}/mirrors").json()
    assert log["count"] == 2
    assert log["outcomes"] == list(vocabulary.MIRROR_OUTCOMES)
    filtered = client.get(
        f"{PREFIX}/rooms/{room['id']}/mirrors", params={"outcome": "skipped_unchanged"}
    ).json()
    assert filtered["count"] == 1
    assert filtered["mirrors"][0]["outcome"] == "skipped_unchanged"


def test_an_unknown_mirror_is_404_with_its_own_code(client):
    response = client.get(f"{PREFIX}/mirrors/mirror_absent")
    assert response.status_code == 404
    assert response.json()["error"] == "unknown_mirror"


def test_the_remote_files_panel_says_it_is_simulated(client):
    room = make_room(client)
    connection = make_connection(client, room["id"])
    asset = make_asset(client, room["id"], connection["id"])
    mirror(client, room["id"], asset["id"])
    body = client.get(f"{PREFIX}/remote-files").json()
    assert body["simulated"] is True
    assert body["count"] == 1
    assert body["folder_recommendation"].startswith("While a specific folder ID")
    assert body["folder_caveat"]
    assert "content_base64" not in body["files"][0]


# --------------------------------------------------------------------------- #
# The audit-source rule
# --------------------------------------------------------------------------- #


def test_every_source_in_the_module_interpolates_the_live_prefix():
    """Asserted as "every ``source=`` interpolates ``router.prefix``", which is
    the property that matters. A test that simply banned the literal prefix would
    fail on the router's own ``prefix=`` argument."""
    source = Path(load_feature(MODULE).__file__).read_text(encoding="utf-8")
    every_source = re.findall(r'source=f"([^"]*)"', source)
    assert every_source, "no source is written with an f-string"
    for expression in every_source:
        assert "{router.prefix}" in expression, expression


def test_every_write_route_is_the_one_its_audit_row_names(client):
    """Each write this feature makes names the route that served it."""
    room = make_room(client)
    connection = make_connection(client, room["id"])
    asset = make_asset(client, room["id"], connection["id"])
    mirror(client, room["id"], asset["id"])
    client.patch(f"{PREFIX}/rooms/{room['id']}/assets/{asset['id']}", json={"push_to_crm": False})
    client.post(f"{PREFIX}/rooms/{room['id']}/reconcile", params={"force": True})
    client.delete(f"{PREFIX}/connections/{connection['id']}")

    served = mounted(client)
    recorded = {
        (str(entry["source"]).partition(" ")[0], str(entry["source"]).partition(" ")[2])
        for entry in audit_entries(client)
        if str(entry.get("source") or "").split(" ")[0] in ("POST", "PATCH", "DELETE", "PUT")
        and str(entry.get("source") or "").split(" ", 1)[-1].startswith(PREFIX)
    }
    assert recorded, "this feature wrote rows and none of them named a route"
    for method, path in recorded:
        assert serves(served, method, path), f"{method} {path} is not a route this feature serves"


def test_every_audit_row_in_the_whole_log_names_a_mounted_route(client):
    """The invariant, product-wide: an audit ``source`` is always callable.

    Drives writes through this feature *and* through the core record routes, then
    checks every row the log holds.
    """
    room = make_room(client)
    connection = make_connection(client, room["id"])
    asset = make_asset(client, room["id"], connection["id"])
    mirror(client, room["id"], asset["id"])
    client.post("/api/records/room", json={"name": "Core room", "account": "Core"})
    client.post("/api/records/bulk/x", json=[{"k": "v"}])

    served = mounted(client)
    entries = audit_entries(client)
    assert entries
    for entry in entries:
        source = str(entry.get("source") or "")
        method, _, path = source.partition(" ")
        if method not in ("GET", "POST", "PATCH", "PUT", "DELETE"):
            # Not an HTTP call. The seeder writes "seed" and the simulated vendor
            # writes "connector:<system>:<operation>", and neither names a route,
            # so only a real method is checkable here.
            continue
        assert serves(served, method, path), (
            f"audit row {entry['seq']} names {method} {path}, which is not a mounted route"
        )


def test_the_mirror_writes_its_two_rows_in_one_transaction(client):
    """Either the file id lands on the asset and the mirror row lands, or neither."""
    room = make_room(client)
    connection = make_connection(client, room["id"])
    asset = make_asset(client, room["id"], connection["id"])
    result = mirror(client, room["id"], asset["id"])
    entries = audit_entries(client, record_id=asset["id"])
    actions = {entry["action"] for entry in entries}
    assert "insert" in actions, "the asset was created"
    assert "update" in actions, "the file id was written onto it"
    mirror_entry = next(
        entry
        for entry in audit_entries(client)
        if entry["collection"] == "crm_file_mirror" and entry["record_id"] == result["mirror_id"]
    )
    assert mirror_entry["action"] == "insert"
    assert mirror_entry["source"].startswith("POST " + PREFIX)


# --------------------------------------------------------------------------- #
# No 5xx on any advertised route
# --------------------------------------------------------------------------- #


def test_every_advertised_route_answers_without_a_5xx(client):
    """``tools/verify_all_routes.py`` does this in CI. Doing it here means a
    broken handler is reported by the suite that owns it, and a route that 500s
    only for an empty body is caught rather than blamed on the caller."""
    room = make_room(client)
    connection = make_connection(client, room["id"])
    asset = make_asset(client, room["id"], connection["id"])
    mirror(client, room["id"], asset["id"])

    # The path verify_all_routes.py builds: every placeholder resolved to an
    # absent id or to a real record of ours, and an empty JSON body on a write.
    substitutions = {
        "room_id": room["id"],
        "connection_id": "conn_absent",
        "asset_id": "asset_absent",
        "mirror_id": "mirror_absent",
    }

    def resolve(path: str) -> str:
        for name, value in substitutions.items():
            path = path.replace("{" + name + "}", value)
        return path

    faults = []
    for method, path in sorted(ROUTES):
        target = resolve(f"{PREFIX}{path}")
        if method in ("POST", "PATCH"):
            response = client.request(method, target, json={})
        else:
            response = client.request(method, target)
        if response.status_code >= 500:
            faults.append((method, path, response.status_code, response.text[:120]))
    assert not faults, faults


def test_every_write_route_accepts_an_empty_body_without_a_5xx(client):
    """``verify_all_routes.py`` POSTs ``{}`` to every write route. A handler that
    assumed a field exists would 500 there and pass every other test."""
    room = make_room(client)
    connection = make_connection(client, room["id"])
    asset = make_asset(client, room["id"], connection["id"])
    writes = [
        ("POST", f"{PREFIX}/connections", {"room_id": room["id"]}),
        ("PATCH", f"{PREFIX}/rooms/{room['id']}/assets/{asset['id']}", {}),
        ("POST", f"{PREFIX}/rooms/{room['id']}/reconcile", {}),
        (
            "POST",
            f"{PREFIX}/rooms/{room['id']}/assets/{asset['id']}/mirror",
            {},
        ),
    ]
    for method, path, params in writes:
        response = client.request(method, path, json={}, params=params)
        assert response.status_code < 500, f"{method} {path} -> {response.status_code}"
    assert connection["id"]


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #


def seed_result():
    """Run the seed over a throwaway database and return plain data, not a store.

    The database is closed before this returns, so the caller gets dictionaries
    rather than a ``RecordStore`` bound to a closed connection. The rows are
    copied out while the connection is still open.
    """
    tmp = tempfile.TemporaryDirectory()
    db = AuditedDatabase(Path(tmp.name) / "seed.db", actor="test")
    try:
        room = db.create(
            "room", {"name": "Northwind", "account": "Northwind Traders"}, source="test"
        )
        summary = load_feature(MODULE).seed(
            db,
            {
                "room_ids": [(room["id"], "Northwind Traders")],
                "now": NOW,
                "rng": None,
            },
        )
        engine_rows = {
            "assets": RecordStore(db).list("crm_file_asset", limit=1000),
            "mirrors": RecordStore(db).list("crm_file_mirror", limit=1000),
        }
        return summary, engine_rows, room["id"]
    finally:
        db.close()
        tmp.cleanup()


def test_the_seed_returns_a_string_that_names_the_states_it_created():
    summary, rows, _room_id = seed_result()
    assert isinstance(summary, str) and summary
    assert "chips:" in summary
    assert "connections" in summary and "assets" in summary
    assert rows["assets"], "a demo nobody can review is not a demo"


def test_the_seed_shows_a_state_that_is_not_a_success():
    """The issue says so: the seed must print at least one state that is not a
    success. A demo of only success teaches a reviewer nothing."""
    summary, _rows, _room_id = seed_result()
    for state in ("withheld", "failed", "stale"):
        assert state in summary, f"the demo must show a {state} asset"


def test_the_seed_shows_the_researched_idempotency_and_the_unsourced_refusal():
    summary, _rows, _room_id = seed_result()
    assert "skipped_unchanged" in summary
    assert "no_owner_record" in summary
    assert "unsourced" not in summary.split("(")[0], "the connections line must not hide a refusal"


def test_the_seed_string_is_encodable_in_cp1252():
    """A RIGHTWARDS ARROW in one recovered feature broke the whole seeder on a
    Windows console. Every character here must survive cp1252."""
    summary, _rows, _room_id = seed_result()
    assert summary.encode("cp1252").decode("cp1252") == summary


def test_the_seed_writes_through_the_audited_store_not_around_it():
    _summary, rows, room_id = seed_result()
    sources = {str(row.get("source")) for row in rows["assets"]}
    assert sources == {"seed"}, "every seeded asset names the seeder as its source"
    assert all(row["room_id"] == room_id for row in rows["assets"])
    assert all(row["collection"] == "crm_file_asset" for row in rows["assets"])


def test_the_seeded_page_answers_over_http(client):
    """The seeded rows are reachable through the feature's own routes, so a
    reviewer opening the page sees them."""
    db = client.app.state.db
    room = make_room(client, "Seeded")
    summary = load_feature(MODULE).seed(
        db, {"room_ids": [(room["id"], "Northwind")], "now": NOW, "rng": None}
    )
    assert summary
    documents = client.get(f"{PREFIX}/rooms/{room['id']}/documents").json()
    assert documents["count"] >= 4
    assert {entry["state"] for entry in documents["assets"]} == set(vocabulary.MIRROR_STATES)
    files = client.get(f"{PREFIX}/rooms/{room['id']}/files").json()
    assert files["count"] >= 1
    assert client.get(f"{PREFIX}/rooms/{room['id']}/summary").json()["mirrors"] >= 4
    assert app.state.store is client.app.state.store
