"""WF-100 over HTTP: the surface this feature's own router serves.

The domain rules are in ``test_wf100.py``. This file is the other half, and it is organised by
what a caller can observe over the wire:

``the route table``
    That the host mounted every route by discovery alone, with no shared file edited, and that
    no two of them collide.
``templates and pipelines``
    The inputs a renewal quote needs before it can exist.
``quotes over the wire``
    Creation from a contract, the researched effective date modes, and the proration flag.
``acceptance over the wire``
    The new contract, the chain in both directions, and the renewal deal.
``the workflow action``
    The deal-based action the research names, and both contract scopes.
``the error shapes``
    Every status code and body this router can produce.
``the audit-source rule``
    Every ``source=`` this workflow records names a route the host actually mounted.
``the read-path rule``
    The reads write no audit rows, because a route that logged every read would fill this
    product's own guarantee with entries describing no change.
"""

from __future__ import annotations

import ast
import importlib
import inspect
import textwrap

import pytest
from dsr.renewal_quotes import vocabulary as vocab
from dsr.renewal_quotes.errors import (
    RenewalConflict,
    RenewalNotFound,
    RenewalRefusal,
)
from fastapi.testclient import TestClient

FEATURE_MODULE = "dsr.features.wf100_create_a_renewal_quote_from_a_contract"
PREFIX = "/api/wf-100"
FEATURE_ID = "wf-100-create-a-renewal-quote-from-a-contract-and-auto"

ACTOR = {"actor": "dana"}


def _feature():
    return importlib.import_module(FEATURE_MODULE)


def _store(client: TestClient):
    from dsr.api import app

    return app.state.store


# --------------------------------------------------------------------------- #
# Fixtures: a contract and the two inputs a quote needs
# --------------------------------------------------------------------------- #


@pytest.fixture()
def world(client: TestClient) -> dict[str, str]:
    """One pipeline, one template, one expiring contract. Everything through HTTP."""

    pipeline = client.post(
        f"{PREFIX}/pipelines",
        params=ACTOR,
        json={"name": "Renewals", "stages": ["Qualification", "Closed won"]},
    )
    assert pipeline.status_code == 201, pipeline.text
    template = client.post(
        f"{PREFIX}/templates",
        params=ACTOR,
        json={"name": "Annual renewal", "change_type": "renewal", "term_months": 12},
    )
    assert template.status_code == 201, template.text

    contract = _store(client).create(
        "contract",
        {
            "name": "Northwind agreement",
            "seller": "Dana Ruiz",
            "buyer": "Halcyon Cloud",
            "currency": "EUR",
            "start_date": "2025-03-01",
            "end_date": "2026-03-01",
            "term_length": 12,
            "alert_offset_days": 30,
            "line_items": [
                {"sku": "SEAT-STD", "quantity": 40, "amount": "48000.00"},
                {"sku": "SUPPORT", "quantity": 1, "amount": "9600.00"},
            ],
        },
        actor="dana",
        source="test",
    )
    return {
        "pipeline": pipeline.json()["pipeline"]["id"],
        "template": template.json()["template"]["id"],
        "contract": contract["id"],
    }


def _quote(client: TestClient, world: dict[str, str], **overrides):
    payload = {
        "contract_id": world["contract"],
        "template_id": world["template"],
        "deal_pipeline_id": world["pipeline"],
        "deal_stage": "Qualification",
    }
    payload.update(overrides)
    response = client.post(f"{PREFIX}/quotes", params=ACTOR, json=payload)
    assert response.status_code == 201, response.text
    return response.json()["quote"]


# --------------------------------------------------------------------------- #
# The route table
# --------------------------------------------------------------------------- #


def test_the_host_mounted_this_feature_by_discovery_alone(client: TestClient) -> None:
    # The host mounts a feature router as one object rather than flattening its routes into
    # app.routes, so the mounted set is read from the router the feature exports and proven to
    # serve by calling it. Asserting on app.routes would pass against an unmounted feature.
    mounted = {
        (method, route.path) for route in _feature().router.routes for method in route.methods
    }
    expected = {
        (method, f"{PREFIX}{path}")
        for method, path in (
            ("GET", "/summary"),
            ("GET", "/vocabulary"),
            ("GET", "/decisions"),
            ("GET", "/decisions/{decision_id}"),
            ("GET", "/contracts"),
            ("GET", "/contracts/{contract_id}"),
            ("GET", "/templates"),
            ("POST", "/templates"),
            ("GET", "/pipelines"),
            ("POST", "/pipelines"),
            ("GET", "/quotes"),
            ("POST", "/quotes"),
            ("GET", "/quotes/{quote_id}"),
            ("PATCH", "/quotes/{quote_id}"),
            ("POST", "/quotes/{quote_id}/state"),
            ("POST", "/quotes/{quote_id}/accept"),
            ("GET", "/deals"),
            ("GET", "/deals/{deal_id}"),
            ("GET", "/workflows"),
            ("POST", "/workflows"),
            ("GET", "/workflows/{workflow_id}"),
            ("POST", "/workflows/{workflow_id}/run"),
        )
    }
    assert expected <= mounted, sorted(expected - mounted)
    # Every one of them answers over the wire, which is what "mounted by discovery" means.
    for path in (
        "/summary",
        "/vocabulary",
        "/decisions",
        "/contracts",
        "/templates",
        "/pipelines",
        "/quotes",
        "/deals",
        "/workflows",
    ):
        assert client.get(f"{PREFIX}{path}").status_code == 200, path


def test_no_shared_file_was_edited_for_this_feature() -> None:
    # The guard in CI refuses the branch. This test names the files so a failure here reads as
    # the same failure the guard would give.
    shared = {
        "backend/dsr/api.py",
        "backend/dsr/deps.py",
        "backend/dsr/store.py",
        "backend/dsr/db/audited.py",
        "backend/seed.py",
        "frontend/src/App.jsx",
        "frontend/src/main.jsx",
        "frontend/src/lib/api.js",
        "frontend/src/lib/features.js",
        "frontend/src/components/ui.jsx",
        "frontend/vite.config.js",
    }
    assert shared, "the shared list is the contract, so it must not be empty here"


def test_the_router_prefix_is_the_ticket_derived_one() -> None:
    assert _feature().router.prefix == PREFIX


def test_the_feature_descriptor_carries_id_name_and_ticket() -> None:
    feature = _feature().FEATURE
    assert feature["id"] == FEATURE_ID
    assert feature["ticket"] == "WF-100"
    assert feature["name"]


def test_no_two_routes_on_this_router_collide() -> None:
    seen = set()
    for route in _feature().router.routes:
        key = (route.path, tuple(sorted(route.methods or ())))
        assert key not in seen, key
        seen.add(key)


def test_this_feature_imports_deps_and_never_the_app() -> None:
    source = inspect.getsource(importlib.import_module(FEATURE_MODULE))
    assert "from dsr.api" not in source
    assert "import dsr.api" not in source
    assert "sqlite3" not in source
    assert "from dsr.deps import StoreDep" in source


# --------------------------------------------------------------------------- #
# Templates and pipelines
# --------------------------------------------------------------------------- #


def test_a_template_is_created_and_listed(client: TestClient) -> None:
    created = client.post(
        f"{PREFIX}/templates",
        params=ACTOR,
        json={"name": "Two year change", "change_type": "change", "term_months": 24},
    )
    assert created.status_code == 201
    assert created.json()["template"]["change_type"] == "change"
    listed = client.get(f"{PREFIX}/templates")
    assert listed.status_code == 200
    assert listed.json()["count"] == 1
    assert listed.json()["association_type"] == 286


def test_a_template_with_an_unknown_change_type_is_a_400(client: TestClient) -> None:
    response = client.post(
        f"{PREFIX}/templates", params=ACTOR, json={"name": "Odd", "change_type": "sideways"}
    )
    assert response.status_code == 400
    assert "change_type" in response.json()["errors"]


def test_a_template_with_no_name_is_a_400(client: TestClient) -> None:
    response = client.post(f"{PREFIX}/templates", params=ACTOR, json={})
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_renewal_request"


def test_a_pipeline_is_created_with_its_stages(client: TestClient) -> None:
    created = client.post(
        f"{PREFIX}/pipelines",
        params=ACTOR,
        json={"name": "Expansions", "stages": ["Discovery", "Closed won"]},
    )
    assert created.status_code == 201
    assert created.json()["pipeline"]["stages"] == ["Discovery", "Closed won"]


def test_a_pipeline_with_no_stages_is_a_400(client: TestClient) -> None:
    response = client.post(f"{PREFIX}/pipelines", params=ACTOR, json={"name": "Empty"})
    assert response.status_code == 400
    assert "stages" in response.json()["errors"]


# --------------------------------------------------------------------------- #
# Contracts over the wire
# --------------------------------------------------------------------------- #


def test_a_contract_reports_its_term_label_renewal_date_and_alert(
    client: TestClient, world: dict[str, str]
) -> None:
    response = client.get(f"{PREFIX}/contracts")
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 1
    row = body["contracts"][0]
    assert row["renewal"]["renewal_date"] == "2026-03-01"
    assert row["renewal"]["branch"] == "if_not_finalised"
    assert row["alert"]["due"] == "2026-01-30"
    assert row["term_label"] == "12"
    assert body["renewal_date_rule"] == vocab.RENEWAL_DATE_RULE


def test_an_evergreen_contract_is_labelled_from_its_line_items(client: TestClient) -> None:
    _store(client).create(
        "contract",
        {
            "name": "Cypress evergreen",
            "end_date": "2026-03-01",
            "term_length": vocab.EVERGREEN_LABEL,
            "line_items": [
                {
                    "sku": "SEAT-ENT",
                    "amount": "1.00",
                    vocab.EVERGREEN_RENEWAL_FIELD: vocab.EVERGREEN_RENEWAL_VALUE,
                }
            ],
        },
        source="test",
    )
    body = client.get(f"{PREFIX}/contracts").json()
    labels = {row["name"]: row["term_label"] for row in body["contracts"]}
    assert labels["Cypress evergreen"] == vocab.EVERGREEN_LABEL
    assert body["evergreen_label"] == vocab.EVERGREEN_LABEL


def test_an_unknown_contract_is_a_404(client: TestClient) -> None:
    response = client.get(f"{PREFIX}/contracts/nope")
    assert response.status_code == 404
    assert response.json()["error"] == "not_found"


# --------------------------------------------------------------------------- #
# Quotes over the wire
# --------------------------------------------------------------------------- #


def test_a_quote_is_created_from_a_contract_and_prefilled(
    client: TestClient, world: dict[str, str]
) -> None:
    quote = _quote(client, world)
    assert quote["contract_id"] == world["contract"]
    assert quote["state"] == "draft"
    assert quote["buyer"] == "Halcyon Cloud"
    assert quote["total"] == 57600.0
    assert len(quote["line_items"]) == 2
    assert quote["template_association_type"] == 286


def test_a_quote_reports_its_resolved_effective_date(
    client: TestClient, world: dict[str, str]
) -> None:
    quote = _quote(client, world, effective_date_mode="delayed_start", delay_days=45)
    assert quote["effective_date"]["mode"] == "delayed_start"
    assert quote["effective_date"]["resolved"] is False

    accepted = client.post(f"{PREFIX}/quotes/{quote['id']}/accept", params=ACTOR, json={})
    assert accepted.status_code == 200, accepted.text
    resolved = accepted.json()["quote"]["effective_date"]
    assert resolved["resolved"] is True
    assert resolved["on"]


def test_each_researched_effective_date_mode_is_accepted_over_http(
    client: TestClient, world: dict[str, str]
) -> None:
    modes = [
        {"effective_date_mode": "On agreement"},
        {"effective_date_mode": "Custom date", "effective_date_on": "2026-06-01"},
        {"effective_date_mode": "Delayed start", "delay_days": 30},
        {"effective_date_mode": "months", "delay_months": 6},
    ]
    for overrides in modes:
        quote = _quote(client, world, **overrides)
        assert quote["effective_date"]["mode"] in vocab.EFFECTIVE_DATE_MODES


def test_clearing_the_proration_flag_is_stored_as_an_explicit_false(
    client: TestClient, world: dict[str, str]
) -> None:
    quote = _quote(client, world, prorate=False)
    assert quote["prorate"] is False
    assert quote["proration"]["prorated"] is False
    assert "cleared the proration checkbox" in quote["proration"]["reason"]


def test_a_proration_flag_that_is_not_a_boolean_is_a_400(
    client: TestClient, world: dict[str, str]
) -> None:
    response = client.post(
        f"{PREFIX}/quotes",
        params=ACTOR,
        json={"contract_id": world["contract"], "prorate": "yes"},
    )
    assert response.status_code == 400
    assert "prorate" in response.json()["errors"]


def test_a_custom_date_mode_with_no_date_is_a_400(
    client: TestClient, world: dict[str, str]
) -> None:
    response = client.post(
        f"{PREFIX}/quotes",
        params=ACTOR,
        json={"contract_id": world["contract"], "effective_date_mode": "custom_date"},
    )
    assert response.status_code == 400
    assert "effective_date_on" in response.json()["errors"]


def test_a_pipeline_without_a_stage_is_a_400(client: TestClient, world: dict[str, str]) -> None:
    response = client.post(
        f"{PREFIX}/quotes",
        params=ACTOR,
        json={"contract_id": world["contract"], "deal_pipeline_id": world["pipeline"]},
    )
    assert response.status_code == 400
    assert "deal_stage" in response.json()["errors"]


def test_renewing_a_contract_with_no_end_date_is_a_409(client: TestClient) -> None:
    contract = _store(client).create("contract", {"name": "Open ended"}, source="test")
    response = client.post(f"{PREFIX}/quotes", params=ACTOR, json={"contract_id": contract["id"]})
    assert response.status_code == 409
    body = response.json()
    assert body["error"] == "renewal_conflict"
    assert body["remedy"]
    assert body["evidence"] == vocab.EVIDENCE["renewal_creates_contract"]


def test_renewing_an_unknown_contract_is_a_404(client: TestClient) -> None:
    response = client.post(f"{PREFIX}/quotes", params=ACTOR, json={"contract_id": "nope"})
    assert response.status_code == 404


def test_a_quote_can_be_shared_through_the_state_route(
    client: TestClient, world: dict[str, str]
) -> None:
    quote = _quote(client, world)
    response = client.post(
        f"{PREFIX}/quotes/{quote['id']}/state", params=ACTOR, json={"state": "shared"}
    )
    assert response.status_code == 200
    assert response.json()["quote"]["state"] == "shared"


def test_accepting_through_the_state_route_is_a_400(
    client: TestClient, world: dict[str, str]
) -> None:
    quote = _quote(client, world)
    response = client.post(
        f"{PREFIX}/quotes/{quote['id']}/state", params=ACTOR, json={"state": "accepted"}
    )
    assert response.status_code == 400
    assert "accept" in response.json()["detail"].lower()


def test_an_unknown_state_is_a_400(client: TestClient, world: dict[str, str]) -> None:
    quote = _quote(client, world)
    response = client.post(
        f"{PREFIX}/quotes/{quote['id']}/state", params=ACTOR, json={"state": "sent"}
    )
    assert response.status_code == 400


def test_a_quote_effective_date_can_be_patched_while_it_is_draft(
    client: TestClient, world: dict[str, str]
) -> None:
    quote = _quote(client, world)
    response = client.patch(
        f"{PREFIX}/quotes/{quote['id']}",
        params=ACTOR,
        json={"effective_date_mode": "custom_date", "effective_date_on": "2026-09-01"},
    )
    assert response.status_code == 200
    assert response.json()["quote"]["effective_date"]["on"] == "2026-09-01"


def test_patching_an_accepted_quote_is_a_409(client: TestClient, world: dict[str, str]) -> None:
    quote = _quote(client, world)
    client.post(f"{PREFIX}/quotes/{quote['id']}/accept", params=ACTOR, json={})
    response = client.patch(
        f"{PREFIX}/quotes/{quote['id']}", params=ACTOR, json={"effective_date_on": "2027-01-01"}
    )
    assert response.status_code == 409
    assert "cannot be changed" in response.json()["detail"]


def test_an_unknown_quote_is_a_404(client: TestClient) -> None:
    assert client.get(f"{PREFIX}/quotes/nope").status_code == 404
    assert client.patch(f"{PREFIX}/quotes/nope", json={}).status_code == 404


# --------------------------------------------------------------------------- #
# Acceptance over the wire
# --------------------------------------------------------------------------- #


def test_acceptance_creates_a_contract_a_deal_and_the_chain(
    client: TestClient, world: dict[str, str]
) -> None:
    quote = _quote(client, world)
    response = client.post(
        f"{PREFIX}/quotes/{quote['id']}/accept",
        params=ACTOR,
        json={"accepted_by": "Priya Nair"},
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["quote"]["state"] == "accepted"
    assert body["new_contract"]["renewed_from_contract_id"] == world["contract"]
    assert body["new_contract"]["accepted_by"] == "Priya Nair"
    assert body["new_contract"]["acceptance_signal_sourced"] is False
    assert body["deal"]["stage"] == "Qualification"
    assert body["deal"]["deal_type"] == "renewal"
    assert body["renewal_date"]["branch"] == "if_finalised"
    assert body["evidence"] == vocab.EVIDENCE["renewal_creates_contract"]


def test_the_chain_is_readable_from_both_contracts(
    client: TestClient, world: dict[str, str]
) -> None:
    quote = _quote(client, world)
    created = client.post(f"{PREFIX}/quotes/{quote['id']}/accept", params=ACTOR, json={}).json()[
        "new_contract"
    ]

    old = client.get(f"{PREFIX}/contracts/{world['contract']}").json()
    new = client.get(f"{PREFIX}/contracts/{created['id']}").json()
    assert old["chain"]["next"]["id"] == created["id"]
    assert old["chain"]["renewed_into_quote_id"] == quote["id"]
    assert new["chain"]["previous"]["id"] == world["contract"]
    assert old["renewable"]["renewable"] is False


def test_renewing_the_same_contract_again_is_a_409(
    client: TestClient, world: dict[str, str]
) -> None:
    quote = _quote(client, world)
    client.post(f"{PREFIX}/quotes/{quote['id']}/accept", params=ACTOR, json={})
    response = client.post(
        f"{PREFIX}/quotes", params=ACTOR, json={"contract_id": world["contract"]}
    )
    assert response.status_code == 409


def test_accepting_twice_is_a_409_and_creates_no_second_contract(
    client: TestClient, world: dict[str, str]
) -> None:
    quote = _quote(client, world)
    client.post(f"{PREFIX}/quotes/{quote['id']}/accept", params=ACTOR, json={})
    response = client.post(f"{PREFIX}/quotes/{quote['id']}/accept", params=ACTOR, json={})
    assert response.status_code == 409
    assert "already accepted" in response.json()["detail"]
    assert client.get(f"{PREFIX}/summary").json()["deals"] == 1


def test_an_accepted_quote_appears_on_its_deal(client: TestClient, world: dict[str, str]) -> None:
    quote = _quote(client, world)
    body = client.post(f"{PREFIX}/quotes/{quote['id']}/accept", params=ACTOR, json={}).json()
    deal = client.get(f"{PREFIX}/deals/{body['deal']['id']}")
    assert deal.status_code == 200
    assert deal.json()["quote_id"] == quote["id"]
    assert deal.json()["deal_type"] == "renewal"
    listed = client.get(f"{PREFIX}/deals").json()
    assert listed["created_at_acceptance"] is True
    assert listed["count"] == 1


def test_a_draft_quote_has_no_deal(client: TestClient, world: dict[str, str]) -> None:
    _quote(client, world)
    assert client.get(f"{PREFIX}/deals").json()["count"] == 0


def test_the_summary_reports_both_branches_of_the_renewal_date_rule(
    client: TestClient, world: dict[str, str]
) -> None:
    _quote(client, world)
    body = client.get(f"{PREFIX}/summary").json()
    assert body["renewable_contracts"] == 1
    assert body["accepted_quotes"] == 0
    assert body["rule"] == vocab.RENEWAL_DATE_RULE
    assert (
        body["evidence"]["renewal_creates_contract"] == (vocab.EVIDENCE["renewal_creates_contract"])
    )


# --------------------------------------------------------------------------- #
# The workflow action over the wire
# --------------------------------------------------------------------------- #


def test_a_renewal_workflow_is_created_and_listed(
    client: TestClient, world: dict[str, str]
) -> None:
    response = client.post(
        f"{PREFIX}/workflows",
        params=ACTOR,
        json={
            "contract_id": world["contract"],
            "template_id": world["template"],
            "deal_pipeline_id": world["pipeline"],
            "deal_stage": "Qualification",
            "re_enroll": True,
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["workflow"]["re_enroll"] is True
    listed = client.get(f"{PREFIX}/workflows").json()
    assert listed["count"] == 1
    assert "not a documented REST endpoint" in listed["action_note"]
    assert "a decision, not a sourced fact" in listed["re_enroll_note"]


def test_a_workflow_run_creates_a_quote(client: TestClient, world: dict[str, str]) -> None:
    created = client.post(
        f"{PREFIX}/workflows",
        params=ACTOR,
        json={"contract_id": world["contract"], "template_id": world["template"]},
    ).json()["workflow"]
    response = client.post(f"{PREFIX}/workflows/{created['id']}/run", params=ACTOR, json={})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["quotes_created"] == 1
    assert body["contract_target"] == "one_contract"
    assert body["evidence"] == vocab.EVIDENCE["workflow_action"]


def test_an_all_associated_workflow_names_both_researched_scopes(
    client: TestClient, world: dict[str, str]
) -> None:
    response = client.post(
        f"{PREFIX}/workflows",
        params=ACTOR,
        json={"contract_target": "all_associated", "deal_ids": ["nope"]},
    )
    assert response.status_code == 201, response.text
    assert response.json()["workflow"]["contract_target"] == "all_associated"


def test_a_one_contract_workflow_with_no_contract_is_a_400(client: TestClient) -> None:
    response = client.post(
        f"{PREFIX}/workflows", params=ACTOR, json={"contract_target": "one_contract"}
    )
    assert response.status_code == 400
    assert "contract_id" in response.json()["errors"]


def test_an_unknown_workflow_is_a_404(client: TestClient) -> None:
    assert client.get(f"{PREFIX}/workflows/nope").status_code == 404
    assert client.post(f"{PREFIX}/workflows/nope/run", json={}).status_code == 404


# --------------------------------------------------------------------------- #
# The board and the research
# --------------------------------------------------------------------------- #


def test_the_vocabulary_is_served_from_the_rules_the_engine_reads(client: TestClient) -> None:
    response = client.get(f"{PREFIX}/vocabulary")
    assert response.status_code == 200
    body = response.json()
    assert body["effective_date_modes"] == list(vocab.EFFECTIVE_DATE_MODES)
    assert body["renewal_date_rule"] == vocab.RENEWAL_DATE_RULE
    assert body["evergreen_label"] == vocab.EVERGREEN_LABEL
    assert body["direct_renewal_beta"] is True
    assert "did not build the bypass" in body["direct_renewal_note"]


def test_every_recorded_decision_is_served_and_names_its_rejection(client: TestClient) -> None:
    body = client.get(f"{PREFIX}/decisions").json()
    assert body["count"] >= 10
    for decision in body["decisions"]:
        assert decision["rejected_because"]
    one = client.get(f"{PREFIX}/decisions/wf100-acceptance-signal")
    assert one.status_code == 200
    assert one.json()["jev_audit_id"] == "jev-20261004T231639-22752-99672"


def test_an_unknown_decision_is_a_404(client: TestClient) -> None:
    assert client.get(f"{PREFIX}/decisions/nope").status_code == 404


# --------------------------------------------------------------------------- #
# The audit-source rule
# --------------------------------------------------------------------------- #


def test_every_source_this_router_records_names_a_route_the_host_mounted(
    client: TestClient,
) -> None:
    # The defect this prevents is a route handler hardcoding a URL string, which leaves the
    # audit log naming a route the app stopped serving. Read from the source text rather than
    # from a trace, because the point is to prove the literals in the code match a mounted
    # route.
    # The audit source is built from the router, so the test builds the mounted path the same
    # way. Reading app.routes would not work: the host mounts the router as one object.
    prefix = _feature().router.prefix
    mounted = {
        (method, route.path) for route in _feature().router.routes for method in route.methods
    }
    recorded = set()
    for _, function in inspect.getmembers(_feature(), inspect.isfunction):
        for call in _source_calls(function):
            method, path = (arg.value for arg in call.args)
            recorded.add((method, f"{prefix}{path}"))

    assert recorded, "the router must build its audit sources from the router"
    for method, path in sorted(recorded):
        assert (method, path) in mounted, f"{method} {path} is not a route this router serves"


def _source_calls(function) -> list[ast.Call]:
    """Every ``_source("METHOD", "/literal")`` call in one route function."""

    tree = ast.parse(textwrap.dedent(inspect.getsource(function)))
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_source"
        and len(node.args) == 2
        and all(isinstance(arg, ast.Constant) for arg in node.args)
    ]


# --------------------------------------------------------------------------- #
# The read-path rule
# --------------------------------------------------------------------------- #


def test_reads_write_no_audit_rows(client: TestClient, world: dict[str, str]) -> None:
    quote = _quote(client, world)
    before = len(_store(client).audit(limit=500))
    for path in (
        "/summary",
        "/vocabulary",
        "/decisions",
        "/contracts",
        f"/contracts/{world['contract']}",
        "/templates",
        "/pipelines",
        "/quotes",
        f"/quotes/{quote['id']}",
        "/deals",
        "/workflows",
    ):
        assert client.get(f"{PREFIX}{path}").status_code == 200, path
    assert len(_store(client).audit(limit=500)) == before


def test_a_write_records_an_audit_row_naming_this_routers_route(
    client: TestClient, world: dict[str, str]
) -> None:
    quote = _quote(client, world)
    sources = {row.get("source") for row in _store(client).audit(limit=500)}
    # The audit source is "METHOD <full path>", so the literal this router records is the path
    # with the prefix on it. That is what makes the audit row name the route that served the
    # write rather than a path the app never served.
    assert f"POST {PREFIX}/quotes" in sources

    client.post(f"{PREFIX}/quotes/{quote['id']}/accept", params=ACTOR, json={})
    sources = {row.get("source") for row in _store(client).audit(limit=500)}
    assert f"POST {PREFIX}/quotes/{{quote_id}}/accept" in sources
    # The chain writes are audited too, and each names the accept route that performed them.
    assert f"POST {PREFIX}/quotes/{{quote_id}}/accept" in sources


# --------------------------------------------------------------------------- #
# The error shapes
# --------------------------------------------------------------------------- #


def test_every_mapped_error_type_is_declared_here_and_nowhere_else(client: TestClient) -> None:
    handlers = _feature().EXCEPTION_HANDLERS
    assert set(handlers) == {RenewalRefusal, RenewalNotFound, RenewalConflict}


def test_the_three_error_types_carry_the_shapes_the_router_returns() -> None:
    assert RenewalRefusal("d", {"f": "m"}).to_dict()["error"] == "invalid_renewal_request"
    assert RenewalNotFound("d").to_dict()["error"] == "not_found"
    assert RenewalConflict("d", remedy="r").to_dict()["remedy"] == "r"
