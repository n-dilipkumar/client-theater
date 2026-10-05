"""WF-090: the HTTP surface.

The domain tests in ``test_wf090.py`` prove the grammar and the evaluator behave. This
file proves the routes answer, that a refusal reaches the client as the right status
code, and that the audit rows name URLs the app actually serves.

What is tested here, and why each matters:

* every advertised route answers with no 5xx, which is the release check
  ``tools/verify_all_routes.py`` performs over real HTTP;
* a rule the editor cannot read is 422, a missing rule is 404, and a blocked publish is
  409, because those three are different faults and one status code would hide two of
  them;
* the blocked publish carries the blocking rule's own message, which is what the seller
  acts on;
* a dry-run evaluation writes nothing, because "rules evaluate continuously" must not
  mean a row per request;
* every write lands in the audit log with a source naming a route this router serves,
  which is the product's central promise;
* the seed runs and its return string is encodable by cp1252, because one arrow glyph in
  one recovered feature broke the entire seeder on a Windows console.
"""

from __future__ import annotations

from dsr.db.audited import AuditedDatabase
from dsr.store import RecordStore
from fastapi.testclient import TestClient

PREFIX = "/api/wf-090"

DEEP_DISCOUNT = "SUM([discount]) FROM line_item > 60"
BUNDLE = 'SOLD_TOGETHER FROM line_item WHERE [hs_product_id] IN ("ENT-LICENSE", "SUPPORT-ADDON", "TRAINING")'


def _make_rule(client: TestClient, definition: str = DEEP_DISCOUNT, **overrides) -> dict:
    body = {
        "name": "Deep line discount",
        "rule_definition": definition,
        "outcome": "block_publish",
        "message": "The total line discount is above 60 percent.",
    }
    body.update(overrides)
    response = client.post(f"{PREFIX}/rules", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def _make_quote(client: TestClient, **data) -> str:
    body = {"name": "Acme", "hs_quote_amount": 1000, **data}
    response = client.post("/api/records/wf086_quote", json=body, params={"source": "test"})
    assert response.status_code in (200, 201), response.text
    return response.json()["id"]


def _make_item(client: TestClient, quote_id: str, **item) -> None:
    response = client.post(
        "/api/records/wf086_line_item",
        json={"quote_id": quote_id, **item},
        params={"source": "test"},
    )
    assert response.status_code in (200, 201), response.text


def _deep_discount_quote(client: TestClient) -> str:
    quote_id = _make_quote(client)
    _make_item(client, quote_id, discount=40)
    _make_item(client, quote_id, discount=30)
    return quote_id


# --------------------------------------------------------------------------- #
# Discovery and the served surface
# --------------------------------------------------------------------------- #


def test_the_feature_is_mounted_by_discovery_alone(client: TestClient):
    response = client.get("/api/features")
    assert response.status_code == 200
    body = response.json()
    features = body.get("features", body) if isinstance(body, dict) else body
    match = [f for f in features if "wf-090" in str(f.get("id"))]
    assert match, "the feature module must be discovered with no registration step"


def test_the_vocabulary_route_serves_the_grammar_and_its_limits(client: TestClient):
    body = client.get(f"{PREFIX}/vocabulary").json()
    assert body["grammar"]["scopes"][0]["scope"] == "quote"
    assert body["grammar"]["aggregates"] == ["SUM", "MIN", "MAX", "AVG", "COUNT"]
    assert body["grammar"]["quantifiers"] == ["SOLD_TOGETHER", "INCOMPATIBLE"]
    assert body["grammar"]["examples"], "the five quoted forms must be served"
    assert {one["outcome"] for one in body["rule"]["outcomes"]} == {"show_warning", "block_publish"}
    codes = {one["code"] for one in body["limitations"]}
    assert codes == {"guardrail_quote_discount_unavailable", "guardrail_arithmetic_unsupported"}


def test_every_reason_code_carries_a_published_sentence(client: TestClient):
    body = client.get(f"{PREFIX}/vocabulary").json()
    for entry in body["reason_codes"]:
        assert entry["code"].startswith("guardrail_")
        assert entry["text"], f"{entry['code']} must carry a sentence a page can show"


def test_the_inferences_route_names_the_judgement_calls(client: TestClient):
    body = client.get(f"{PREFIX}/inferences").json()
    ids = {entry["id"] for entry in body["decisions"]["inferences"]}
    assert "DERIVED_SOLD_TOGETHER_MEANS_ALL_PRESENT" in ids
    assert "DERIVED_INCOMPATIBLE_MEANS_MIXED_VALUES" in ids
    assert body["decisions"]["not_built"], "what this build leaves out must be visible"
    assert len(body["decisions"]["jev"]) == 2, "both Jev-validated decisions must be shown"


def test_every_advertised_route_answers_with_no_5xx(client: TestClient):
    """The release check ``tools/verify_all_routes.py`` performs, run in-process."""
    body = client.get("/api/features").json()
    features = body.get("features", body) if isinstance(body, dict) else body
    match = [f for f in features if "wf-090" in str(f.get("id"))][0]
    seen = 0
    for route in match["routes"]:
        path = route["path"]
        if "{" in path:
            continue
        for verb in route.get("methods", ["GET"]):
            if verb not in ("GET", "POST"):
                continue
            probe = client.request(verb, path)
            assert probe.status_code < 500, f"{verb} {path} returned {probe.status_code}"
            seen += 1
    assert seen >= 5, f"expected several routes to be probed, probed {seen}"


# --------------------------------------------------------------------------- #
# Rules
# --------------------------------------------------------------------------- #


def test_a_rule_is_created_with_its_normalised_definition(client: TestClient):
    rule = _make_rule(client)
    stored = rule["rule"] if "rule" in rule else rule
    data = stored["data"]
    assert data["status"] == "enabled"
    assert data["normalised_definition"]
    listing = client.get(f"{PREFIX}/rules").json()
    assert listing["count"] == 1


def test_a_rule_with_an_unknown_outcome_is_refused_with_422(client: TestClient):
    response = client.post(
        f"{PREFIX}/rules",
        json={
            "name": "x",
            "rule_definition": "[quote.a] > 1",
            "outcome": "banish",
            "message": "",
        },
    )
    assert response.status_code == 422
    body = response.json()
    assert body["error"] == "guardrail_rule_invalid"
    assert {one["field"] for one in body["errors"]} >= {"outcome", "message"}


def test_a_rule_with_an_unsupported_form_is_refused_and_names_the_limit(client: TestClient):
    response = client.post(
        f"{PREFIX}/rules",
        json={
            "name": "x",
            "rule_definition": "SUM([quantity] * [price]) FROM line_item > 5",
            "outcome": "block_publish",
            "message": "m",
        },
    )
    assert response.status_code == 422
    codes = {one.get("error") for one in response.json()["errors"]}
    assert "guardrail_arithmetic_unsupported" in codes


def test_the_validate_route_answers_both_ways_with_200(client: TestClient):
    good = client.post(f"{PREFIX}/rules/validate", json={"rule_definition": DEEP_DISCOUNT})
    assert good.status_code == 200
    assert good.json()["valid"] is True
    assert good.json()["normalised"]
    bad = client.post(
        f"{PREFIX}/rules/validate", json={"rule_definition": "[quote.hs_discount] > 1"}
    )
    assert bad.status_code == 200
    assert bad.json()["valid"] is False
    assert bad.json()["error"] == "guardrail_quote_discount_unavailable"


def test_a_rule_can_be_read_patched_and_deleted(client: TestClient):
    rule = _make_rule(client)
    rule_id = rule["id"]
    read = client.get(f"{PREFIX}/rules/{rule_id}")
    assert read.status_code == 200

    patched = client.patch(f"{PREFIX}/rules/{rule_id}", json={"status": "disabled"})
    assert patched.status_code == 200
    assert patched.json()["data"]["enabled"] is False

    removed = client.delete(f"{PREFIX}/rules/{rule_id}")
    assert removed.status_code == 200
    assert client.get(f"{PREFIX}/rules/{rule_id}").status_code == 404


def test_reading_a_missing_rule_is_404_with_its_own_code(client: TestClient):
    response = client.get(f"{PREFIX}/rules/wf090_quote_rule_absent")
    assert response.status_code == 404
    assert response.json()["error"] == "guardrail_rule_not_found"


def test_patching_to_an_invalid_rule_is_refused(client: TestClient):
    rule_id = _make_rule(client)["id"]
    response = client.patch(f"{PREFIX}/rules/{rule_id}", json={"outcome": "nope"})
    assert response.status_code == 422


# --------------------------------------------------------------------------- #
# Evaluation and the publish gate
# --------------------------------------------------------------------------- #


def test_evaluation_writes_nothing_and_splits_the_verdicts(client: TestClient):
    _make_rule(client)
    quote_id = _deep_discount_quote(client)
    body = client.get(f"{PREFIX}/quotes/{quote_id}/evaluate").json()
    assert body["blocked"] is True
    assert body["reason_code"] == "guardrail_block_publish"
    assert client.get(f"{PREFIX}/evaluations").json()["evaluations"] == []


def test_evaluating_a_quote_that_does_not_exist_is_404(client: TestClient):
    response = client.get(f"{PREFIX}/quotes/wf086_quote_absent/evaluate")
    assert response.status_code == 404


def test_a_blocked_publish_is_409_and_names_the_rule(client: TestClient):
    _make_rule(client, message="Reduce the discount before publishing.")
    quote_id = _deep_discount_quote(client)
    response = client.post(f"{PREFIX}/quotes/{quote_id}/publish")
    assert response.status_code == 409
    body = response.json()
    assert body["error"] == "guardrail_block_publish"
    assert "Reduce the discount before publishing." in body["detail"]
    assert body["violations"], "the blocking rule must be named, not just a status code"


def test_a_clean_publish_is_allowed_and_reports_warnings(client: TestClient):
    _make_rule(
        client,
        definition="[quote.hs_quote_amount] > 100000",
        outcome="show_warning",
        message="Large quote.",
    )
    quote_id = _make_quote(client, hs_quote_amount=150000)
    response = client.post(f"{PREFIX}/quotes/{quote_id}/publish")
    assert response.status_code == 200
    body = response.json()
    assert body["allowed"] is True
    assert [one["rule_name"] for one in body["warnings"]] == ["Deep line discount"]


def test_every_text_outcome_can_publish_a_clean_quote(client: TestClient):
    _make_rule(client)
    quote_id = _make_quote(client)
    _make_item(client, quote_id, discount=5)
    response = client.post(f"{PREFIX}/quotes/{quote_id}/publish")
    assert response.status_code == 200
    assert response.json()["allowed"] is True


def test_a_publish_attempt_is_recorded_either_way(client: TestClient):
    _make_rule(client)
    blocked_quote = _deep_discount_quote(client)
    assert client.post(f"{PREFIX}/quotes/{blocked_quote}/publish").status_code == 409
    clean_quote = _make_quote(client)
    _make_item(client, clean_quote, discount=5)
    assert client.post(f"{PREFIX}/quotes/{clean_quote}/publish").status_code == 200
    history = client.get(f"{PREFIX}/quotes/{blocked_quote}/evaluations").json()
    assert history["count"] >= 1
    summary = client.get(f"{PREFIX}/summary").json()
    assert summary["publish_attempts"]["total"] == 2
    assert summary["publish_attempts"]["blocked"] == 1
    assert summary["publish_attempts"]["allowed"] == 1


def test_the_summary_counts_the_configured_rules(client: TestClient):
    _make_rule(client)
    _make_rule(
        client,
        definition="[quote.hs_quote_amount] > 1",
        outcome="show_warning",
        status="disabled",
    )
    body = client.get(f"{PREFIX}/summary").json()
    assert body["rules"]["total"] == 2
    assert body["rules"]["enabled"] == 1
    assert body["rules"]["disabled"] == 1


# --------------------------------------------------------------------------- #
# The audit promise
# --------------------------------------------------------------------------- #


def test_every_write_lands_in_the_audit_log_naming_a_served_route(
    client: TestClient, db: AuditedDatabase
):
    rule_id = _make_rule(client)["id"]
    client.patch(f"{PREFIX}/rules/{rule_id}", json={"status": "disabled"})
    quote_id = _deep_discount_quote(client)
    client.post(f"{PREFIX}/quotes/{quote_id}/publish")
    rows = db.audit()
    assert rows, "every write must be audited"
    mine = [row for row in rows if PREFIX in str(row.get("source") or "")]
    assert len(mine) >= 3, f"expected the rule, the patch and the attempt, saw {len(mine)}"
    for row in mine:
        source = str(row.get("source") or "")
        assert source.startswith(("POST ", "PATCH ", "DELETE ")), source
        assert source.split(" ", 1)[1].startswith(PREFIX), source


# --------------------------------------------------------------------------- #
# The seeder contract
# --------------------------------------------------------------------------- #


def _feature_module():
    """The feature module, imported the way the host imports it."""
    from dsr.features import iter_feature_modules

    for _name, module, error in iter_feature_modules():
        if error or module is None:
            continue
        if str(getattr(module, "FEATURE", {}).get("id", "")).startswith("wf-090"):
            return module
    raise AssertionError("the WF-090 feature module was not discovered")


def test_the_seed_returns_a_cp1252_encodable_string(db: AuditedDatabase):
    """One RIGHTWARDS ARROW in one feature broke the entire seeder on Windows."""
    from datetime import datetime, timezone

    summary = _feature_module().seed(
        db, {"now": datetime.now(timezone.utc), "rng": None, "room_ids": [("room-1", "Room One")]}
    )
    assert summary
    summary.encode("cp1252")
    assert "quote rules" in summary


def test_the_seed_creates_the_demo_rules_and_quotes(db: AuditedDatabase):
    from datetime import datetime, timezone

    _feature_module().seed(
        db, {"now": datetime.now(timezone.utc), "rng": None, "room_ids": [("room-1", "Room One")]}
    )
    store = RecordStore(db)
    assert store.count_where("wf090_quote_rule", {}) == 6
    assert store.count_where("wf086_quote", {}) == 4
    evaluations = store.list("wf090_rule_evaluation")
    assert evaluations, "the seed must record the verdicts it computed"
    assert any(one["data"]["blocked"] for one in evaluations)
    assert any(not one["data"]["blocked"] for one in evaluations), "one quote must be publishable"


def test_the_seed_works_with_no_demo_rooms(db: AuditedDatabase):
    """A demo rule with no room is global, which is a legitimate state."""
    from datetime import datetime, timezone

    summary = _feature_module().seed(db, {"now": datetime.now(timezone.utc), "rng": None})
    assert summary
    assert RecordStore(db).count_where("wf090_quote_rule", {}) == 6
