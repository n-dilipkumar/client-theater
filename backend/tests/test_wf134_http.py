"""WF-134 over HTTP: the routes the host actually mounted.

``test_wf134.py`` proves the rules. This file proves the wiring: that discovery
mounted the router, that each error type became the status code a client should
branch on, that an engagement event scores the deal in the same call, that a
coaching outcome closes a cue, and that every audit row a write left behind names
a concrete route the app serves.

That last one is the property the whole feature host exists to protect, so it is
asserted against the mounted route table rather than against a literal written in
the test. A literal would agree with a typo.

Each test file must pass on its own, because the suite runs under ``pytest-xdist``
and a test that only passes in one order fails intermittently. Nothing here
depends on another test's rows: every test builds its own room and its own deal.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from dsr.competitive_baseline import deal_health as domain
from dsr.competitive_baseline.deal_health import DealHealthRefused
from dsr.db.audited import RecordNotFound
from dsr.features import load_feature
from dsr.store import RecordStore
from fastapi.testclient import TestClient

MODULE = "wf134_engagement_driven_deal_health_scoring_risk_detection_and_coaching"
PREFIX = "/api/wf-134"
FEATURE_ID = "wf-134-deal-health"


@pytest.fixture()
def feature():
    return load_feature(MODULE)


@pytest.fixture()
def live_store(db) -> RecordStore:
    """A store over the *same* database the ``client`` fixture serves.

    The shared ``store`` fixture is backed by ``memory_db``, while ``client``
    swaps ``app.state.store`` onto the file-backed ``db``. A fixture built from
    ``store`` and then asserted through ``client`` would read a database nothing
    wrote to. One database per test is all the isolation this file needs.
    """
    return RecordStore(db)


@pytest.fixture()
def room_id(live_store: RecordStore) -> str:
    return live_store.create("room", {"name": "Northwind"}, actor="test", source="test")["id"]


def _deal(client: TestClient, room_id: str, name: str = "Northwind", **extra: object) -> dict:
    response = client.post(f"{PREFIX}/rooms/{room_id}/deals", json={"name": name, **extra})
    assert response.status_code == 200, response.text
    return response.json()


def _event(client: TestClient, deal_id: str, **extra: object) -> dict:
    payload = {"kind": "meeting", "person": "Priya Raman", **extra}
    response = client.post(f"{PREFIX}/deals/{deal_id}/events", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def _stakeholder(client: TestClient, deal_id: str, name: str = "Priya Raman", **extra: object):
    response = client.post(f"{PREFIX}/deals/{deal_id}/stakeholders", json={"name": name, **extra})
    assert response.status_code == 200, response.text
    return response.json()


def _content(client: TestClient, deal_id: str, name: str = "Overview Deck", **extra: object):
    response = client.post(f"{PREFIX}/deals/{deal_id}/content", json={"name": name, **extra})
    assert response.status_code == 200, response.text
    return response.json()


# --------------------------------------------------------------------------- #
# Discovery and mounting
# --------------------------------------------------------------------------- #


def test_discovery_mounted_this_feature_by_its_prefix(client: TestClient, feature):
    """The router was mounted by discovery alone. No shared file was edited."""
    listed = client.get("/api/features").json()
    found = [entry for entry in listed["features"] if entry["id"] == FEATURE_ID]
    assert found, "the feature registry did not list this feature"
    assert found[0]["prefix"] == PREFIX
    assert found[0]["loaded"] is True
    assert client.get(f"{PREFIX}/vocabulary").status_code == 200


def test_no_feature_failed_to_load_because_of_this_workflow(client: TestClient):
    """A broken feature is reported and skipped, so a failure here would be silent."""
    listed = client.get("/api/features").json()
    failures = [entry for entry in listed["failed"] if entry["module"].endswith(MODULE)]
    assert not failures, failures


def test_the_feature_maps_only_error_types_it_raises_itself(feature):
    """A handler for a builtin type would intercept that exception product-wide."""
    builtins_ = (ValueError, LookupError, PermissionError, KeyError, TypeError)
    assert len(feature.EXCEPTION_HANDLERS) == 3
    for error_type in feature.EXCEPTION_HANDLERS:
        assert error_type.__module__ == "dsr.competitive_baseline.deal_health", error_type
        assert error_type not in builtins_, error_type
        assert error_type is not RecordNotFound, error_type


def test_the_feature_reports_every_route_it_mounts(client: TestClient):
    """The registry is what ``tools/verify_all_routes.py`` walks.

    A route missing from it is a route nobody checks for a 5xx, so the set is
    asserted exactly: adding a route without listing it here, or listing one the
    feature does not serve, both fail.
    """
    shapes = client.get(f"/api/features/{FEATURE_ID}").json()["routes"]
    served = {f"{method} {shape['path']}" for shape in shapes for method in shape["methods"]}
    assert served == {
        f"GET {PREFIX}/summary",
        f"GET {PREFIX}/vocabulary",
        f"GET {PREFIX}/rooms/{{room_id}}/deals",
        f"POST {PREFIX}/rooms/{{room_id}}/deals",
        f"GET {PREFIX}/deals/{{deal_id}}",
        f"PATCH {PREFIX}/deals/{{deal_id}}",
        f"GET {PREFIX}/deals/{{deal_id}}/score",
        f"POST {PREFIX}/deals/{{deal_id}}/score",
        f"GET {PREFIX}/deals/{{deal_id}}/history",
        f"GET {PREFIX}/deals/{{deal_id}}/events",
        f"POST {PREFIX}/deals/{{deal_id}}/events",
        f"GET {PREFIX}/deals/{{deal_id}}/stakeholders",
        f"POST {PREFIX}/deals/{{deal_id}}/stakeholders",
        f"PATCH {PREFIX}/deals/{{deal_id}}/stakeholders/{{contact_id}}",
        f"GET {PREFIX}/deals/{{deal_id}}/coverage",
        f"GET {PREFIX}/deals/{{deal_id}}/content",
        f"POST {PREFIX}/deals/{{deal_id}}/content",
        f"PATCH {PREFIX}/deals/{{deal_id}}/content/{{asset_id}}",
        f"GET {PREFIX}/deals/{{deal_id}}/alerts",
        f"POST {PREFIX}/alerts/resolve",
        f"GET {PREFIX}/deals/{{deal_id}}/coaching",
        f"POST {PREFIX}/coaching/outcome",
        f"GET {PREFIX}/readiness",
    }


def test_the_module_never_imports_the_app_or_opens_sqlite(feature):
    """The enforced rule, restated so the reason is on the record for this workflow."""
    from pathlib import Path

    source = Path(feature.__file__).read_text(encoding="utf-8")
    assert "import sqlite3" not in source
    assert "from dsr.api" not in source
    assert "import dsr.api" not in source
    assert source.count("_source(") >= 10  # every write derives its audit source


def test_no_route_answers_with_a_server_error(client: TestClient, room_id: str, feature):
    """Every mounted route is reached, and none of them 5xx's.

    This is the API half of the release bar, run in the suite. A 404 or a 400 is
    the API correctly refusing; a 5xx or a request that never answers means a
    mounted route has no working handler behind it. Placeholders are filled the
    way ``tools/verify_all_routes.py`` fills them, except a real room id, so a
    room-scoped read exercises real data.
    """
    substitutes = {
        "room_id": room_id,
        "deal_id": "wf134_deal_absent",
        "contact_id": "con_absent",
        "asset_id": "asset_absent",
    }
    paths: set[tuple[str, str]] = set()
    for shape in client.get(f"/api/features/{FEATURE_ID}").json()["routes"]:
        path = shape["path"]
        for key, value in substitutes.items():
            path = path.replace("{" + key + "}", value)
        for method in shape["methods"]:
            paths.add((method, path))

    assert len(paths) == 23, sorted(paths)
    for method, path in sorted(paths):
        body = {} if method in ("POST", "PATCH", "PUT") else None
        response = client.request(method, path, json=body)
        assert response.status_code < 500, (method, path, response.status_code, response.text)


# --------------------------------------------------------------------------- #
# The board
# --------------------------------------------------------------------------- #


def test_the_summary_route_counts_each_band(client: TestClient, room_id: str):
    _deal(client, room_id, "Contoso")
    body = client.get(f"{PREFIX}/summary", params={"room_id": room_id}).json()
    assert body["deals"] == 1
    assert body["weight_percent"] == {"engagement": 50, "content": 30, "stakeholder": 20}
    assert body["generated_at"]


def test_the_vocabulary_route_publishes_the_issues_open_decisions(client: TestClient):
    """A reviewer can see each derivation without reading the source."""
    body = client.get(f"{PREFIX}/vocabulary").json()
    assert body["frameworks"] == list(domain.FRAMEWORKS)
    assert body["bands"] == list(domain.BANDS)
    assert body["decisions"]["score_formula"].startswith("The score is a weighted sum")
    assert any("Live coaching" in note for note in body["not_implemented"])


# --------------------------------------------------------------------------- #
# Errors become the status codes a client branches on
# --------------------------------------------------------------------------- #


def test_an_invalid_deal_is_400_with_a_field_keyed_map(client: TestClient, room_id: str):
    response = client.post(f"{PREFIX}/rooms/{room_id}/deals", json={"framework": "guesswork"})
    assert response.status_code == 400, response.text
    body = response.json()
    assert body["error"] == "deal_health_invalid"
    assert "name" in body["errors"]
    assert "framework" in body["errors"]


def test_an_unknown_deal_is_404(client: TestClient):
    response = client.get(f"{PREFIX}/deals/wf134_deal_absent")
    assert response.status_code == 404, response.text
    assert response.json()["error"] == "not_found"


def test_resolving_an_unknown_alert_is_404(client: TestClient):
    response = client.post(f"{PREFIX}/alerts/resolve", json={"alert_id": "wf134_alert_absent"})
    assert response.status_code == 404, response.text


def test_resolving_without_an_alert_id_is_400(client: TestClient):
    response = client.post(f"{PREFIX}/alerts/resolve", json={})
    assert response.status_code == 400, response.text
    assert "alert_id" in response.json()["errors"]


def test_closing_a_cue_that_already_has_an_outcome_is_409(client: TestClient, room_id: str):
    """409, because nothing is malformed: the cue's state refuses the change."""
    deal = _deal(client, room_id, "Contoso")
    cue = client.post(f"{PREFIX}/deals/{deal['id']}/score").json()["coaching"][0]
    assert (
        client.post(
            f"{PREFIX}/coaching/outcome", json={"cue_id": cue["id"], "state": "actioned"}
        ).status_code
        == 200
    )
    refused = client.post(
        f"{PREFIX}/coaching/outcome", json={"cue_id": cue["id"], "state": "dismissed"}
    )
    assert refused.status_code == 409, refused.text
    assert refused.json()["reason"] == DealHealthRefused.REASON_ALREADY_CLOSED


def test_an_unknown_outcome_state_is_400(client: TestClient, room_id: str):
    deal = _deal(client, room_id, "Contoso")
    cue = client.post(f"{PREFIX}/deals/{deal['id']}/score").json()["coaching"][0]
    response = client.post(
        f"{PREFIX}/coaching/outcome", json={"cue_id": cue["id"], "state": "ignored"}
    )
    assert response.status_code == 400, response.text
    assert "state" in response.json()["errors"]


def test_a_write_that_does_not_read_back_is_409_not_a_success(
    client: TestClient, room_id: str, monkeypatch
):
    """The seed for the release-bar claim that every clear change is verified.

    The store's update is replaced with a no-op, so the write appears to succeed
    and the record does not change. The route must refuse to report success.
    """
    deal = _deal(client, room_id, "Northwind")
    monkeypatch.setattr(client.app.state.store, "update", lambda *args, **kwargs: None)

    response = client.patch(f"{PREFIX}/deals/{deal['id']}", json={"name": "Renamed"})
    assert response.status_code == 409, response.text
    assert response.json()["reason"] == DealHealthRefused.REASON_WRITE_NOT_VISIBLE


# --------------------------------------------------------------------------- #
# Scoring, engagement intake and alerts
# --------------------------------------------------------------------------- #


def test_a_new_deal_has_a_live_reading_but_no_recorded_score(client: TestClient, room_id: str):
    deal = _deal(client, room_id, "Contoso")
    card = client.get(f"{PREFIX}/deals/{deal['id']}/score").json()
    assert card["recorded"] is False
    assert card["score"]["band"] == domain.BAND_AT_RISK
    assert client.get(f"{PREFIX}/deals/{deal['id']}/history").json()["history"] == []


def test_an_event_scores_the_deal_and_raises_the_alert_in_one_call(
    client: TestClient, room_id: str
):
    """A quiet room with unreviewed material and no buyer is at risk on the spot."""
    deal = _deal(client, room_id, "Contoso")
    _content(client, deal["id"], "Pilot Scope", required=True)
    body = _event(client, deal["id"], kind="view", at=datetime.now(timezone.utc).isoformat())

    assert body["event"]["kind"] == "view"
    assert body["score"]["band"] == domain.BAND_AT_RISK
    assert [row["kind"] for row in body["alerts"]] == [domain.ALERT_BAND]
    reasons = {row["reason"] for row in body["coaching"]}
    assert domain.REASON_UNREVIEWED_KEY_MATERIAL in reasons
    assert domain.REASON_MISSING_STAKEHOLDERS in reasons


def test_recording_an_event_with_a_bad_kind_is_400(client: TestClient, room_id: str):
    deal = _deal(client, room_id, "Contoso")
    response = client.post(
        f"{PREFIX}/deals/{deal['id']}/events", json={"kind": "telepathy", "person": ""}
    )
    assert response.status_code == 400, response.text
    assert set(response.json()["errors"]) == {"kind", "person"}


def test_the_score_history_is_a_time_series_oldest_first(client: TestClient, room_id: str):
    deal = _deal(client, room_id, "Northwind")
    _event(client, deal["id"], kind="view")
    _event(client, deal["id"], kind="meeting")
    history = client.get(f"{PREFIX}/deals/{deal['id']}/history").json()["history"]
    assert len(history) == 2
    assert history[0]["value"] <= history[1]["value"]


def test_scoring_a_deal_now_records_a_reading(client: TestClient, room_id: str):
    deal = _deal(client, room_id, "Contoso")
    body = client.post(f"{PREFIX}/deals/{deal['id']}/score").json()
    assert body["score"]["band"] == domain.BAND_AT_RISK
    assert client.get(f"{PREFIX}/deals/{deal['id']}/score").json()["recorded"] is True


def test_a_resolved_alert_stops_being_reported_as_open(client: TestClient, room_id: str):
    deal = _deal(client, room_id, "Contoso")
    alert = client.post(f"{PREFIX}/deals/{deal['id']}/score").json()["alerts"][0]
    resolved = client.post(f"{PREFIX}/alerts/resolve", json={"alert_id": alert["id"]}).json()
    assert resolved["open"] is False
    listed = client.get(f"{PREFIX}/deals/{deal['id']}/alerts").json()["alerts"]
    assert [row["open"] for row in listed] == [False]


# --------------------------------------------------------------------------- #
# The buying group and the coverage map
# --------------------------------------------------------------------------- #


def test_the_coverage_map_reports_who_engaged_over_http(client: TestClient, room_id: str):
    deal = _deal(client, room_id, "Northwind")
    _stakeholder(
        client, deal["id"], "Priya Raman", engaged_at=datetime.now(timezone.utc).isoformat()
    )
    _stakeholder(client, deal["id"], "Ben Silva")

    coverage = client.get(f"{PREFIX}/deals/{deal['id']}/coverage").json()
    assert [row["name"] for row in coverage["engaged"]] == ["Priya Raman"]
    assert [row["name"] for row in coverage["not_engaged"]] == ["Ben Silva"]
    assert coverage["met"] is False


def test_a_stakeholder_can_be_marked_engaged_over_http(client: TestClient, room_id: str):
    deal = _deal(client, room_id, "Northwind")
    person = _stakeholder(client, deal["id"], "Ben Silva")
    updated = client.patch(
        f"{PREFIX}/deals/{deal['id']}/stakeholders/{person['id']}", json={"engagement_count": 3}
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["engaged"] is True


def test_a_stakeholder_from_another_deal_is_404(client: TestClient, room_id: str):
    first = _deal(client, room_id, "Northwind")
    second = _deal(client, room_id, "Fabrikam")
    person = _stakeholder(client, first["id"], "Ben Silva")
    response = client.patch(
        f"{PREFIX}/deals/{second['id']}/stakeholders/{person['id']}", json={"role": "finance"}
    )
    assert response.status_code == 404, response.text


# --------------------------------------------------------------------------- #
# Key material
# --------------------------------------------------------------------------- #


def test_unreviewed_key_material_clears_when_the_asset_is_reviewed(
    client: TestClient, room_id: str
):
    deal = _deal(client, room_id, "Contoso")
    now = datetime.now(timezone.utc).isoformat()
    _stakeholder(client, deal["id"], "Ada Chen", engaged_at=now)
    _event(client, deal["id"], at=now)
    asset = _content(client, deal["id"], "Pilot Scope", required=True)

    before = client.get(f"{PREFIX}/deals/{deal['id']}/score").json()["score"]["reasons"]
    assert domain.REASON_UNREVIEWED_KEY_MATERIAL in before

    reviewed = client.patch(f"{PREFIX}/deals/{deal['id']}/content/{asset['id']}", json={})
    assert reviewed.status_code == 200, reviewed.text
    assert reviewed.json()["reviewed"] is True
    after = client.get(f"{PREFIX}/deals/{deal['id']}/score").json()["score"]["reasons"]
    assert domain.REASON_UNREVIEWED_KEY_MATERIAL not in after

    unreviewed = client.patch(
        f"{PREFIX}/deals/{deal['id']}/content/{asset['id']}", json={"reviewed": False}
    )
    assert unreviewed.json()["reviewed"] is False


def test_key_material_without_a_name_is_400(client: TestClient, room_id: str):
    deal = _deal(client, room_id, "Contoso")
    response = client.post(f"{PREFIX}/deals/{deal['id']}/content", json={"kind": "deck"})
    assert response.status_code == 400, response.text
    assert "name" in response.json()["errors"]


# --------------------------------------------------------------------------- #
# Coaching and the closed loop
# --------------------------------------------------------------------------- #


def test_a_cue_can_be_listed_open_only_and_closed_by_an_outcome(client: TestClient, room_id: str):
    deal = _deal(client, room_id, "Contoso")
    cues = client.post(f"{PREFIX}/deals/{deal['id']}/score").json()["coaching"]
    assert len(cues) >= 2

    listed = client.get(
        f"{PREFIX}/deals/{deal['id']}/coaching", params={"open_only": "true"}
    ).json()["coaching"]
    assert [row["id"] for row in listed] == [row["id"] for row in cues]

    closed = client.post(
        f"{PREFIX}/coaching/outcome", json={"cue_id": cues[0]["id"], "state": "actioned"}
    ).json()
    assert closed["cue"]["open"] is False
    assert closed["outcome"]["state"] == "actioned"

    still_open = client.get(
        f"{PREFIX}/deals/{deal['id']}/coaching", params={"open_only": "true"}
    ).json()["coaching"]
    assert cues[0]["id"] not in [row["id"] for row in still_open]


def test_the_readiness_route_reports_the_closed_loop(client: TestClient, room_id: str):
    deal = _deal(client, room_id, "Contoso", rep="dana")
    cue = client.post(f"{PREFIX}/deals/{deal['id']}/score").json()["coaching"][0]
    client.post(f"{PREFIX}/coaching/outcome", json={"cue_id": cue["id"], "state": "actioned"})

    body = client.get(f"{PREFIX}/readiness").json()
    assert body["outcomes"] == 1
    assert body["readiness"] == 1.0
    assert client.get(f"{PREFIX}/readiness", params={"rep": "sam"}).json()["outcomes"] == 0


# --------------------------------------------------------------------------- #
# Deal edits
# --------------------------------------------------------------------------- #


def test_a_deal_can_be_patched_over_http(client: TestClient, room_id: str):
    deal = _deal(client, room_id, "Northwind")
    updated = client.patch(f"{PREFIX}/deals/{deal['id']}", json={"framework": "bant"}).json()
    assert updated["framework"] == "bant"
    assert client.get(f"{PREFIX}/deals/{deal['id']}").json()["name"] == "Northwind"


def test_the_room_deal_list_is_room_scoped(client: TestClient, room_id: str, live_store):
    other = live_store.create("room", {"name": "Vantage"}, actor="test", source="test")["id"]
    _deal(client, room_id, "Northwind")
    _deal(client, other, "Vantage deal")
    listed = client.get(f"{PREFIX}/rooms/{room_id}/deals").json()["deals"]
    assert [row["name"] for row in listed] == ["Northwind"]


# --------------------------------------------------------------------------- #
# Every write's audit row names a route the app actually serves
# --------------------------------------------------------------------------- #


def test_a_read_writes_no_audit_row(client: TestClient, live_store: RecordStore, room_id: str):
    """A GET must not leave an audit row behind, which is why score_card computes."""
    deal = _deal(client, room_id, "Contoso")
    before = len(live_store.db.audit())
    assert client.get(f"{PREFIX}/deals/{deal['id']}/score").status_code == 200
    assert client.get(f"{PREFIX}/deals/{deal['id']}/coverage").status_code == 200
    assert client.get(f"{PREFIX}/summary").status_code == 200
    assert len(live_store.db.audit()) == before


def test_every_audit_row_this_feature_wrote_names_a_route_the_app_serves(
    client: TestClient, live_store: RecordStore, room_id: str
):
    """The property the feature host exists to protect.

    Asserted against the mounted route table rather than a literal, because a
    literal in the test would agree with a typo in the module. Served paths come
    from the feature registry, which is also what ``tools/verify_all_routes.py``
    walks.
    """
    served = {
        (method, shape["path"])
        for shape in client.get(f"/api/features/{FEATURE_ID}").json()["routes"]
        for method in shape["methods"]
    }
    assert served, "the registry reports no route for this feature"

    # Drive one write of every kind this feature makes.
    deal = _deal(client, room_id, "Northwind")
    now = datetime.now(timezone.utc).isoformat()
    _event(client, deal["id"], at=now)
    person = _stakeholder(client, deal["id"], "Priya Raman")
    asset = _content(client, deal["id"], "Overview Deck")
    client.patch(f"{PREFIX}/deals/{deal['id']}", json={"stage": "evaluation"})
    client.patch(
        f"{PREFIX}/deals/{deal['id']}/stakeholders/{person['id']}", json={"role": "champion"}
    )
    client.patch(f"{PREFIX}/deals/{deal['id']}/content/{asset['id']}", json={})
    client.post(f"{PREFIX}/deals/{deal['id']}/score")

    alert = client.get(f"{PREFIX}/deals/{deal['id']}/alerts").json()["alerts"]
    if alert:
        client.post(f"{PREFIX}/alerts/resolve", json={"alert_id": alert[0]["id"]})
    cue = client.get(f"{PREFIX}/deals/{deal['id']}/coaching").json()["coaching"]
    if cue:
        client.post(
            f"{PREFIX}/coaching/outcome", json={"cue_id": cue[0]["id"], "state": "actioned"}
        )

    rows = [
        row for row in live_store.db.audit() if str(row.get("collection") or "").startswith("wf134")
    ]
    assert rows, "no audit rows were written for this feature's collections"

    checked = 0
    for row in rows:
        source = str(row.get("source") or "")
        if not source.startswith(("POST ", "PATCH ", "PUT ", "DELETE ")):
            # A write made by the seeder or by a test fixture. Not a route, and
            # correctly so: nothing served it.
            continue
        method, _, path = source.partition(" ")
        assert (method, path) in served, f"audit row names a route the app does not serve: {source}"
        checked += 1

    assert checked >= 8, f"only {checked} route-sourced audit rows were checked"


def test_the_seed_runs_over_this_features_routes_by_engine_only(client: TestClient, live_store):
    """The demo is produced by the engine, never by a route, so its source is 'seed'."""
    feature = load_feature(MODULE)
    room_ids = [
        (
            live_store.create("room", {"name": "Northwind"}, actor="seed", source="seed")["id"],
            "Northwind",
        )
    ]
    reported = feature.seed(
        live_store.db, {"room_ids": room_ids, "now": datetime.now(timezone.utc)}
    )
    assert reported
    measured = live_store.db.audit()
    seed_rows = [row for row in measured if str(row.get("source")) == "seed"]
    assert seed_rows, "the seed left no audit rows"


def test_a_second_reading_of_an_unchanged_deal_does_not_report_a_drop(
    client: TestClient, room_id: str
):
    """A guard against a time bomb: the drop reason uses a recorded value, not a date.

    The engine compares the new value with the previous *recorded value*, so a
    reading that happens to run on a particular weekday cannot change the answer.
    """
    deal = _deal(client, room_id, "Northwind")
    now = datetime.now(timezone.utc).isoformat()
    _stakeholder(client, deal["id"], "Ada Chen", engaged_at=now)
    _content(client, deal["id"], "Overview Deck", required=True, reviewed=True)
    _event(client, deal["id"], at=now)
    first = client.post(f"{PREFIX}/deals/{deal['id']}/score").json()["score"]
    second = client.post(f"{PREFIX}/deals/{deal['id']}/score").json()["score"]
    # Nothing changed, so nothing dropped, whatever today's date is.
    assert second["delta"] == 0
    assert second["value"] == first["value"]
    assert domain.REASON_SCORE_DROP not in second["reasons"]
