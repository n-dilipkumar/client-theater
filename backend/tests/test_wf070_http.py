"""WF-070 over HTTP: the routes the host actually mounted.

``test_wf070.py`` proves the rules. This file proves the wiring: that discovery
mounted the router, that each error type became the status code a client should
branch on, that the seller's and the viewer's routes are kept apart, and that
every audit row a write left behind names a concrete route the app serves.

That last one is the property the whole feature host exists to protect, so it is
asserted against the mounted route table rather than against a literal written in
the test. A literal would agree with a typo.

Each test file must pass on its own, because the suite runs under ``pytest-xdist``
and a test that only passes in one order fails intermittently. Nothing here
depends on another test's rows: every test builds its own room, agreement and link.
"""

from __future__ import annotations

import pytest
from dsr.features import load_feature
from dsr.link_gating import agreement as nda, gate as link_gate
from dsr.store import RecordStore
from fastapi.testclient import TestClient

MODULE = "wf070_require_nda_acceptance-before_viewing".replace("-", "_")
PREFIX = "/api/wf-070"
FEATURE_ID = "wf-070-require-nda-acceptance-before-viewing"

NDA_BODY = "MUTUAL NON-DISCLOSURE AGREEMENT. Each party keeps confidential information."
AMENDED_BODY = "MUTUAL NON-DISCLOSURE AGREEMENT, AMENDED. Retention is five years."


@pytest.fixture()
def feature():
    return load_feature(MODULE)


@pytest.fixture()
def live_store(db) -> RecordStore:
    """A store over the *same* database the ``client`` fixture serves.

    The shared ``store`` fixture is backed by ``memory_db``, while ``client`` swaps
    ``app.state.store`` onto the file-backed ``db``. Building fixtures from ``store``
    and then asserting through ``client`` therefore reads a database nothing wrote
    to, and every such test fails for the same uninteresting reason. One database
    per test is the whole of the isolation this file needs.
    """
    return RecordStore(db)


@pytest.fixture()
def room_id(live_store: RecordStore) -> str:
    return live_store.create("room", {"name": "Northwind"}, actor="test", source="test")["id"]


@pytest.fixture()
def agreement_id(client: TestClient, room_id: str) -> str:
    """A real agreement, made through the real route.

    Built over HTTP rather than through the engine so every later test in this file
    starts from a row the routes themselves produced.
    """
    response = client.post(
        f"{PREFIX}/rooms/{room_id}/agreements",
        json={"title": "Northwind mutual NDA", "body": NDA_BODY},
    )
    assert response.status_code == 200, response.text
    return response.json()["id"]


@pytest.fixture()
def link_id(live_store: RecordStore, room_id: str) -> str:
    """A real gated link, made through WF-069's engine.

    The link is WF-069's row, not this feature's. Building it through that engine
    is what makes the two gates share one link rather than two parallel models of
    one thing.
    """
    engine = link_gate.GateEngine(live_store)
    return engine.create_link(
        room_id,
        {"title": "Northwind deal link", "dataroom_id": room_id, "password": "northwind"},
        source="test",
        actor="test",
    )["id"]


@pytest.fixture()
def gated_link(client: TestClient, link_id: str, agreement_id: str) -> str:
    """A link with the agreement gate on, made the way the CLI flag makes it."""
    response = client.patch(f"{PREFIX}/links/{link_id}/agreement", json={"agreement": agreement_id})
    assert response.status_code == 200, response.text
    return link_id


def accept_through(client: TestClient, link_id: str, session_id: str) -> dict:
    response = client.post(
        f"{PREFIX}/links/{link_id}/gate/agreement",
        json={"session_id": session_id, "accepted": True},
    )
    assert response.status_code == 200, response.text
    return response.json()


def open_gate(client: TestClient, link_id: str) -> dict:
    response = client.get(f"{PREFIX}/links/{link_id}/gate")
    assert response.status_code == 200, response.text
    return response.json()


# --------------------------------------------------------------------------- #
# Discovery
# --------------------------------------------------------------------------- #


def test_the_router_is_mounted_by_discovery_alone(client: TestClient):
    """`dsr/api.py` is not edited. The host found the module and mounted it."""
    record = client.get(f"/api/features/{FEATURE_ID}").json()
    assert record["loaded"] is True
    assert record["prefix"] == PREFIX
    assert record["ticket"] == "WF-070"


def test_the_feature_reports_every_route_it_mounts(client: TestClient):
    """The registry is what `tools/verify_all_routes.py` walks, so a route missing
    from it is a route nobody checks for a 5xx."""
    paths = {route["path"] for route in client.get(f"/api/features/{FEATURE_ID}").json()["routes"]}
    assert paths == {
        f"{PREFIX}/summary",
        f"{PREFIX}/vocabulary",
        f"{PREFIX}/agreements",
        f"{PREFIX}/agreements/{{agreement_id}}",
        f"{PREFIX}/rooms/{{room_id}}/agreements",
        f"{PREFIX}/rooms/{{room_id}}/gates",
        f"{PREFIX}/rooms/{{room_id}}/acceptances",
        f"{PREFIX}/rooms/{{room_id}}/sessions",
        f"{PREFIX}/links/{{link_id}}/agreement",
        f"{PREFIX}/links/{{link_id}}/gate",
        f"{PREFIX}/links/{{link_id}}/gate/agreement",
        f"{PREFIX}/links/{{link_id}}/content",
    }


def test_the_prefix_is_unique_across_every_mounted_feature(client: TestClient):
    """Two features may share a prefix while their concrete paths differ, but a
    second feature on the same prefix and the same path would be dead code."""
    paths: dict[str, str] = {}
    for record in client.get("/api/features").json()["features"]:
        for route in record.get("routes", []):
            for method in route.get("methods", []):
                key = f"{method} {route['path']}"
                assert key not in paths, f"{key} claimed by {paths[key]} and {record['id']}"
                paths[key] = record["id"]
    assert f"GET {PREFIX}/summary" in paths


def test_this_feature_maps_only_its_own_error_types(client: TestClient):
    """Registering a handler for a shared or builtin type would intercept that
    exception across the whole product."""
    handlers = client.get(f"/api/features/{FEATURE_ID}").json()["exception_handlers"]
    assert handlers == ["AgreementDenied", "AgreementError", "AgreementNotFound"]


def test_it_does_not_collide_with_wf069s_error_handlers(client: TestClient):
    """WF-069 maps `GateError`, `GateDenied` and `LinkNotFound`. These are distinct
    classes, not subclasses, and the host would have refused this feature outright
    if they were."""
    wf069 = client.get("/api/features/wf-069-gate-each-buyer-link-with-a-password-a").json()
    assert wf069["exception_handlers"] == ["GateDenied", "GateError", "LinkNotFound"]
    mine = set(client.get(f"/api/features/{FEATURE_ID}").json()["exception_handlers"])
    assert not mine & set(wf069["exception_handlers"])


# --------------------------------------------------------------------------- #
# Errors become the status a client branches on
# --------------------------------------------------------------------------- #


def test_an_invalid_agreement_is_a_400_with_a_field_keyed_map(client: TestClient, room_id: str):
    """The map is what lets a form put each message beside the input that caused it."""
    response = client.post(f"{PREFIX}/rooms/{room_id}/agreements", json={})
    assert response.status_code == 400
    body = response.json()
    assert body["error"] == "agreement_invalid"
    assert set(body["errors"]) == {"title", "body"}


def test_a_refused_viewer_is_a_403_with_a_stable_reason(client: TestClient, gated_link: str):
    """The reason is a token, so the page never has to match an English sentence."""
    opened = open_gate(client, gated_link)
    response = client.get(
        f"{PREFIX}/links/{gated_link}/content", params={"session_id": opened["session_id"]}
    )
    assert response.status_code == 403
    body = response.json()
    assert body["error"] == "agreement_denied"
    assert body["reason"] == nda.AgreementDenied.REASON_NOT_ACCEPTED
    assert body["detail"]


def test_a_missing_agreement_or_link_is_a_404(client: TestClient):
    assert client.get(f"{PREFIX}/agreements/agr_absent").status_code == 404
    assert client.get(f"{PREFIX}/links/ln_absent/agreement").status_code == 404


def test_a_gate_pointed_at_nothing_that_exists_is_a_404(client: TestClient, link_id: str):
    response = client.patch(
        f"{PREFIX}/links/{link_id}/agreement",
        json={"enable_agreement": True, "agreement_id": "agr_absent"},
    )
    assert response.status_code == 404


def test_enabling_the_gate_with_no_agreement_is_a_400(client: TestClient, link_id: str):
    """OpenAPI: `agreement_id` is "Required when `enable_agreement` is true`."""
    response = client.patch(f"{PREFIX}/links/{link_id}/agreement", json={"enable_agreement": True})
    assert response.status_code == 400
    assert "agreement_id" in response.json()["errors"]


def test_a_revoked_link_and_an_expired_link_are_worded_identically(
    client: TestClient,
    live_store: RecordStore,
    room_id: str,
    gated_link: str,
):
    """A viewer holding a forwarded URL must not be able to tell "this deal closed"
    from "you were cut off"."""
    expired = link_gate.GateEngine(live_store).create_link(
        room_id,
        {
            "title": "Expired",
            "dataroom_id": room_id,
            "expires_at": "2020-01-01T00:00:00+00:00",
        },
        source="test",
        actor="test",
    )["id"]

    revoked_response = client.get(f"{PREFIX}/links/{gated_link}/gate")
    live_store.delete(gated_link, actor="test", source="test")
    revoked = client.get(f"{PREFIX}/links/{gated_link}/gate")
    closed = client.get(f"{PREFIX}/links/{expired}/gate")

    assert revoked_response.status_code == 200
    assert revoked.status_code == 403
    assert closed.status_code == 403
    assert revoked.json()["reason"] == closed.json()["reason"]
    assert revoked.json()["detail"] == closed.json()["detail"]


# --------------------------------------------------------------------------- #
# The seller's side
# --------------------------------------------------------------------------- #


def test_creating_an_agreement_returns_the_text_a_viewer_is_shown(client: TestClient, room_id: str):
    response = client.post(
        f"{PREFIX}/rooms/{room_id}/agreements",
        json={"title": "Northwind mutual NDA", "body": NDA_BODY, "governing_law": "England"},
    )
    body = response.json()
    assert response.status_code == 200
    assert body["body"] == NDA_BODY
    assert body["version"] == 1
    assert body["governing_law"] == "England"
    assert body["body_digest"] == nda.body_digest(NDA_BODY)


def test_the_agreement_list_omits_the_bodies(client: TestClient, room_id: str, agreement_id: str):
    """Shipping every NDA's full text to render a list is how legal text ends up in
    a browser cache nobody chose to put it in."""
    body = client.get(f"{PREFIX}/agreements", params={"room_id": room_id}).json()
    assert body["room_id"] == room_id
    assert body["agreements"][0]["id"] == agreement_id
    assert "body" not in body["agreements"][0]
    assert client.get(f"{PREFIX}/agreements/{agreement_id}").json()["body"] == NDA_BODY


def test_editing_the_text_returns_the_new_version(client: TestClient, agreement_id: str):
    response = client.patch(f"{PREFIX}/agreements/{agreement_id}", json={"body": AMENDED_BODY})
    assert response.status_code == 200
    assert response.json()["version"] == 2
    assert response.json()["body_digest"] == nda.body_digest(AMENDED_BODY)


def test_the_link_gate_is_confirmed_on_read(client: TestClient, gated_link: str, agreement_id: str):
    """Mirrors `GET /v1/links/{id}`, which the research names as the way a seller
    confirms the gate state."""
    body = client.get(f"{PREFIX}/links/{gated_link}/agreement").json()
    assert body["gate"]["enabled"] is True
    assert body["gate"]["agreement_id"] == agreement_id
    assert body["gate"]["agreement_ok"] is True
    assert body["gate"]["fields"] == ["enable_agreement", "agreement_id"]
    # The list carries no body.
    assert "body" not in body["agreement"]


def test_the_gate_update_is_tri_state_over_http(client: TestClient, gated_link: str):
    """`{}` changes nothing, a boolean sets it, an explicit null clears it."""
    before = client.get(f"{PREFIX}/links/{gated_link}/agreement").json()["gate"]

    unchanged = client.patch(f"{PREFIX}/links/{gated_link}/agreement", json={}).json()["gate"]
    assert unchanged == before

    off = client.patch(
        f"{PREFIX}/links/{gated_link}/agreement", json={"enable_agreement": "off"}
    ).json()["gate"]
    assert off["enabled"] is False
    assert off["agreement_id"] == before["agreement_id"]

    cleared = client.patch(
        f"{PREFIX}/links/{gated_link}/agreement",
        json={"enable_agreement": False, "agreement_id": None},
    ).json()["gate"]
    assert cleared["agreement_id"] is None


def test_the_cli_on_off_spelling_is_accepted(client: TestClient, link_id: str, agreement_id: str):
    """CLI update table: `--enable-agreement <on|off>`."""
    on = client.patch(
        f"{PREFIX}/links/{link_id}/agreement",
        json={"enable_agreement": "on", "agreement_id": agreement_id},
    ).json()["gate"]
    assert on["enabled"] is True

    off = client.patch(
        f"{PREFIX}/links/{link_id}/agreement", json={"enable_agreement": "off"}
    ).json()["gate"]
    assert off["enabled"] is False


def test_the_room_gate_list_names_the_fail_closed_links(
    client: TestClient, room_id: str, gated_link: str, agreement_id: str
):
    """A seller has to be able to see the worst state this workflow can be in."""
    healthy = client.get(f"{PREFIX}/rooms/{room_id}/gates").json()
    assert healthy["room_id"] == room_id
    assert [g["id"] for g in healthy["gates"]] == [gated_link]
    assert healthy["gates"][0]["gate"]["agreement_ok"] is True

    client.delete(f"{PREFIX}/agreements/{agreement_id}")

    broken = client.get(f"{PREFIX}/rooms/{room_id}/gates").json()
    assert broken["gates"][0]["gate"]["enabled"] is True
    assert broken["gates"][0]["gate"]["agreement_ok"] is False


def test_retiring_an_agreement_names_the_gates_it_broke(
    client: TestClient, gated_link: str, agreement_id: str
):
    """The seller decides whether each link points somewhere else or stops asking.
    Repairing them silently would be the one change nobody could audit."""
    body = client.delete(f"{PREFIX}/agreements/{agreement_id}").json()
    assert body["retired"] is True
    assert body["gates_now_closed"] == [gated_link]


def test_retiring_an_agreement_keeps_the_text_and_the_acceptances(
    client: TestClient, gated_link: str, agreement_id: str
):
    """WF-069's rule, reused: revocation is expiry, not disappearance. "Who accepted
    what" has to stay answerable after the NDA is retired."""
    opened = open_gate(client, gated_link)
    accept_through(client, gated_link, opened["session_id"])

    client.delete(f"{PREFIX}/agreements/{agreement_id}")

    rows = client.get(f"{PREFIX}/rooms/{_room_of(client, gated_link)}/acceptances").json()[
        "acceptances"
    ]
    assert len(rows) == 1
    assert rows[0]["agreement_id"] == agreement_id


def _room_of(client: TestClient, link_id: str) -> str:
    return client.get(f"{PREFIX}/links/{link_id}/agreement").json()["room_id"]


def test_the_summary_and_the_vocabulary_are_served(
    client: TestClient, room_id: str, gated_link: str
):
    summary = client.get(f"{PREFIX}/summary", params={"room_id": room_id}).json()
    assert summary["gated"] == 1
    assert summary["links"] == 1
    assert summary["broken_gates"] == 0

    vocabulary = client.get(f"{PREFIX}/vocabulary").json()
    assert set(vocabulary["fields"]) == {"enable_agreement", "agreement_id"}
    assert vocabulary["not_implemented"]


def test_the_session_list_carries_ids_and_no_token_hashes(
    client: TestClient, room_id: str, gated_link: str
):
    """A list with no ids is a list nobody can act on. A list with hashes is a leak."""
    open_gate(client, gated_link)
    rows = client.get(f"{PREFIX}/rooms/{room_id}/sessions").json()["sessions"]
    assert rows and rows[0]["id"]
    assert rows[0]["accepted"] is False
    assert "token_hash" not in str(rows)


# --------------------------------------------------------------------------- #
# The viewer's side
# --------------------------------------------------------------------------- #


def test_the_viewer_is_shown_the_agreement_before_anything_else(
    client: TestClient, gated_link: str
):
    """Step three of the flow: "before any document renders, is shown the NDA`."""
    body = open_gate(client, gated_link)
    assert body["state"] == nda.STATE_AGREEMENT
    assert body["gate_required"] is True
    assert body["agreement"]["body"] == NDA_BODY
    assert body["session_id"]
    assert body["session_token"]


def test_an_ungated_link_releases_content_with_no_session_at_all(
    client: TestClient,
    live_store: RecordStore,
    room_id: str,
):
    """The control case, over HTTP."""
    link = link_gate.GateEngine(live_store).create_link(
        room_id,
        {"title": "Open", "dataroom_id": room_id, "email_protected": False},
        source="test",
        actor="test",
    )["id"]

    opened = client.get(f"{PREFIX}/links/{link}/gate").json()
    assert opened["state"] == nda.STATE_OPEN
    assert opened["session_id"] is None

    released = client.get(f"{PREFIX}/links/{link}/content").json()
    assert released["released"] is True


def test_content_is_403_until_the_viewer_accepts(client: TestClient, gated_link: str):
    opened = open_gate(client, gated_link)

    refused = client.get(
        f"{PREFIX}/links/{gated_link}/content", params={"session_id": opened["session_id"]}
    )
    assert refused.status_code == 403
    assert refused.json()["reason"] == nda.AgreementDenied.REASON_NOT_ACCEPTED

    accept_through(client, gated_link, opened["session_id"])

    released = client.get(
        f"{PREFIX}/links/{gated_link}/content", params={"session_id": opened["session_id"]}
    )
    assert released.status_code == 200
    assert released.json()["released"] is True
    assert released.json()["gate_satisfied"] is True


def test_acceptance_must_be_explicit_over_http(client: TestClient, gated_link: str):
    """A request that merely arrives at the route has not accepted anything."""
    opened = open_gate(client, gated_link)
    response = client.post(
        f"{PREFIX}/links/{gated_link}/gate/agreement",
        json={"session_id": opened["session_id"]},
    )
    assert response.status_code == 400
    assert "accepted" in response.json()["errors"]


def test_acceptance_records_the_version_and_returns_the_ids(
    client: TestClient, gated_link: str, agreement_id: str
):
    opened = open_gate(client, gated_link)
    accepted = accept_through(client, gated_link, opened["session_id"])
    assert accepted["agreement_id"] == agreement_id
    assert accepted["agreement_version"] == 1
    assert accepted["acceptance_id"]
    assert accepted["state"] == nda.STATE_OPEN


def test_a_second_viewer_gets_their_own_session(client: TestClient, gated_link: str):
    """Nothing a viewer proved on an earlier pass carries into this one."""
    first = open_gate(client, gated_link)
    second = open_gate(client, gated_link)
    assert first["session_id"] != second["session_id"]

    accept_through(client, gated_link, first["session_id"])

    refused = client.get(
        f"{PREFIX}/links/{gated_link}/content", params={"session_id": second["session_id"]}
    )
    assert refused.status_code == 403


def test_a_session_from_one_link_is_refused_at_another(
    client: TestClient, live_store: RecordStore, room_id: str, gated_link: str
):
    other = link_gate.GateEngine(live_store).create_link(
        room_id, {"title": "Other", "dataroom_id": room_id}, source="test", actor="test"
    )["id"]
    client.patch(
        f"{PREFIX}/links/{other}/agreement",
        json={
            "agreement": client.get(f"{PREFIX}/links/{gated_link}/agreement").json()["gate"][
                "agreement_id"
            ]
        },
    )

    opened = open_gate(client, gated_link)
    response = client.get(
        f"{PREFIX}/links/{other}/content", params={"session_id": opened["session_id"]}
    )
    assert response.status_code == 403
    assert response.json()["reason"] == nda.AgreementDenied.REASON_SESSION_UNKNOWN


def test_editing_the_nda_over_http_re_opens_the_gate(
    client: TestClient, gated_link: str, agreement_id: str
):
    """The rule the whole workflow turns on, end to end over HTTP."""
    opened = open_gate(client, gated_link)
    accept_through(client, gated_link, opened["session_id"])
    assert (
        client.get(
            f"{PREFIX}/links/{gated_link}/content", params={"session_id": opened["session_id"]}
        ).status_code
        == 200
    )

    amended = client.patch(f"{PREFIX}/agreements/{agreement_id}", json={"body": AMENDED_BODY})
    assert amended.json()["version"] == 2

    refused = client.get(
        f"{PREFIX}/links/{gated_link}/content", params={"session_id": opened["session_id"]}
    )
    assert refused.status_code == 403
    assert refused.json()["reason"] == nda.AgreementDenied.REASON_SUPERSEDED

    # And the loop is escapable: the same viewer accepts the current text.
    accept_through(client, gated_link, opened["session_id"])
    assert (
        client.get(
            f"{PREFIX}/links/{gated_link}/content", params={"session_id": opened["session_id"]}
        ).status_code
        == 200
    )


def test_a_gate_whose_agreement_is_retired_releases_nothing_over_http(
    client: TestClient, gated_link: str, agreement_id: str
):
    """Fails closed from the very next request, and says why."""
    opened = open_gate(client, gated_link)
    accept_through(client, gated_link, opened["session_id"])

    client.delete(f"{PREFIX}/agreements/{agreement_id}")

    opened_response = client.get(f"{PREFIX}/links/{gated_link}/gate")
    assert opened_response.status_code == 403
    assert opened_response.json()["reason"] == nda.AgreementDenied.REASON_UNAVAILABLE

    content = client.get(
        f"{PREFIX}/links/{gated_link}/content", params={"session_id": opened["session_id"]}
    )
    assert content.status_code == 403
    assert content.json()["reason"] == nda.AgreementDenied.REASON_UNAVAILABLE


def test_the_response_names_the_gates_this_workflow_does_not_own(
    client: TestClient,
    live_store: RecordStore,
    gated_link: str,
):
    """Accepting an NDA is not the same as clearing a password, and the response
    says so rather than letting the buyer find out at the document."""
    opened = open_gate(client, gated_link)
    accept_through(client, gated_link, opened["session_id"])

    body = client.get(
        f"{PREFIX}/links/{gated_link}/content", params={"session_id": opened["session_id"]}
    ).json()
    data = live_store.get(gated_link)["data"]
    assert body["remaining_gates"] == list(link_gate.rules.steps_required(data))
    assert body["remaining_gates"] == ["email", "password"]
    assert body["note"]


# --------------------------------------------------------------------------- #
# Audit
# --------------------------------------------------------------------------- #


def test_every_audit_row_a_write_left_names_a_route_the_app_serves(
    client: TestClient, live_store: RecordStore, room_id: str
):
    """Hard rule 4, checked against the mounted route table.

    Every route is called, then every audit row this feature wrote is matched back
    to a concrete ``(method, path)`` the host mounted. A feature whose domain
    function hardcoded a URL string leaves the audit log naming a path the app
    stopped serving, and that is invisible to the import check.
    """
    engine = link_gate.GateEngine(live_store)
    link = engine.create_link(
        room_id, {"title": "Audited", "dataroom_id": room_id}, source="test", actor="test"
    )["id"]

    client.post(
        f"{PREFIX}/rooms/{room_id}/agreements",
        json={"title": "Audited NDA", "body": NDA_BODY},
    )
    body = client.get(f"{PREFIX}/agreements").json()["agreements"][0]["id"]
    client.patch(f"{PREFIX}/agreements/{body}", json={"title": "Audited NDA v2"})
    client.patch(f"{PREFIX}/links/{link}/agreement", json={"agreement": body})
    opened = client.get(f"{PREFIX}/links/{link}/gate").json()
    client.post(
        f"{PREFIX}/links/{link}/gate/agreement",
        json={"session_id": opened["session_id"], "accepted": True},
    )
    client.get(f"{PREFIX}/links/{link}/content?session_id={opened['session_id']}")
    client.delete(f"{PREFIX}/agreements/{body}")

    mounted = {
        f"{method} {route['path']}"
        for record in client.get("/api/features").json()["features"]
        for route in record.get("routes", [])
        for method in route.get("methods", [])
    }
    route_prefixes = tuple(f"{method} {PREFIX}" for method in ("GET", "POST", "PATCH", "DELETE"))
    ours = {
        row["source"]
        for row in live_store.db.audit()
        if str(row.get("source") or "").startswith(route_prefixes)
    }
    assert ours, "no audit row named a WF-070 route"
    for source in ours:
        assert source in mounted, f"{source} is not a mounted route"
        # With every placeholder filled in, the concrete path names this feature's
        # prefix and carries no template left over. A source built from the router
        # satisfies both by construction; a hand-typed one need not.
        concrete = (
            source.replace("{room_id}", room_id)
            .replace("{agreement_id}", body)
            .replace("{link_id}", link)
        )
        _method, _space, path = concrete.partition(" ")
        assert path.startswith(f"{PREFIX}/"), concrete
        assert "{" not in path, concrete


def test_the_seed_writes_are_not_attributed_to_a_route(client: TestClient):
    """`source="seed"` rather than a route string: no route served it, and claiming
    one would be the lie hard rule 4 exists to prevent."""
    from datetime import datetime, timezone

    from dsr.db.audited import AuditedDatabase

    database = AuditedDatabase(":memory:", actor="test")
    try:
        store_for_seed = RecordStore(database)
        room = store_for_seed.create("room", {"name": "Seeded"}, source="test")["id"]
        second = store_for_seed.create("room", {"name": "Second"}, source="test")["id"]
        load_feature(MODULE).seed(
            database,
            {
                "room_ids": [(room, "A"), (second, "B")],
                "now": datetime.now(timezone.utc),
                "rng": None,
            },
        )
        sources = [str(row.get("source") or "") for row in database.audit()]
        assert "seed" in sources
        # Nothing the seed wrote claims to have come from a route.
        assert not [s for s in sources if s.split(" ")[0] in ("GET", "POST", "PATCH", "DELETE")]
    finally:
        database.close()


# --------------------------------------------------------------------------- #
# Shape checks a change could quietly break
# --------------------------------------------------------------------------- #


def test_the_feature_module_names_no_shared_file(client: TestClient, feature):
    """The contract's own list, restated so a change that touches one fails here."""
    from pathlib import Path

    source = Path(feature.__file__).read_text(encoding="utf-8")
    assert "from dsr.api" not in source
    assert "import dsr.api" not in source
    assert "import sqlite3" not in source
    assert "from dsr.deps import" in source


def test_every_route_answers_without_a_5xx_on_an_empty_request(client: TestClient, room_id: str):
    """`tools/verify_all_routes.py` calls every advertised route with `{}` and
    substitutes placeholders, so a route that raises on an empty body would show up
    there as a 500. This is the same pass, made explicit and per-assertion."""
    calls = [
        ("GET", f"{PREFIX}/summary"),
        ("GET", f"{PREFIX}/vocabulary"),
        ("GET", f"{PREFIX}/agreements"),
        ("GET", f"{PREFIX}/agreements/agr_absent"),
        ("POST", f"{PREFIX}/rooms/{room_id}/agreements"),
        ("GET", f"{PREFIX}/rooms/{room_id}/gates"),
        ("GET", f"{PREFIX}/rooms/{room_id}/acceptances"),
        ("GET", f"{PREFIX}/rooms/{room_id}/sessions"),
        ("GET", f"{PREFIX}/links/ln_absent/agreement"),
        ("PATCH", f"{PREFIX}/links/ln_absent/agreement"),
        ("GET", f"{PREFIX}/links/ln_absent/gate"),
        ("POST", f"{PREFIX}/links/ln_absent/gate/agreement"),
        ("GET", f"{PREFIX}/links/ln_absent/content"),
        ("PATCH", f"{PREFIX}/agreements/agr_absent"),
        ("DELETE", f"{PREFIX}/agreements/agr_absent"),
    ]
    for method, path in calls:
        response = client.request(method, path, json={})
        assert response.status_code < 500, f"{method} {path} -> {response.status_code}"
        # 404 is the correct answer for a placeholder id, so only assert the
        # successful and refused shapes, never a server error.
        assert response.status_code in (200, 400, 403, 404), f"{method} {path}"
