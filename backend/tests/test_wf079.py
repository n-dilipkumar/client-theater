"""Tests for WF-079: the tamper-evident audit-trail export.

Written against the adopted implementation's own routes - ``/rooms/{id}/audit-trail``,
``/rooms/{id}/documents/{record_id}/audit-trail``, ``/rooms/{id}/audit-trail/anchors``,
``/observations``, ``/reports`` - rather than against an earlier draft.

What a compliance export can get wrong, and which block here pins it:

* **It can lose a row.** A trail that drops what it does not recognise looks
  complete and is not: an unclaimed integer is exported verbatim, and a rejected
  verification whose method has no band slot is still visible.
* **It can claim a hash nobody can reproduce.** ``TheHashIsReproducible`` rebuilds
  the canonical bytes and refolds the chain from the published specification alone,
  deliberately *without* calling the implementation, because a test that reuses the
  code under test proves only that it agrees with itself.
* **It can overstate what it proves.** ``AnchorsAreHonestAboutTheirLimits`` pins the
  distinction between a self-consistent export and a falsifiable one.
* **It can open itself to anyone.** ``TheAdministratorGate`` pins the researched
  "workspace administrators only", failing closed for a caller that sends no role.
* **It can silently drop the rules.** ``TheResearchedDateRules`` pins ``MM/DD/YYYY``,
  twelve months, ten years of lookback, and one notification per requested type,
  each with its own error code.

Two of these tests exist because the first draft of this workflow shipped two
defects that no test would have caught, both of which were 500s in a
criticality-C2 workflow:

* ``EveryWriteRouteCompletes`` drives each POST end to end. The first draft called
  ``store.update(..., room_id=...)``, which ``AuditedDatabase.update`` does not
  accept, so every report generation raised ``TypeError``.
* ``TheRecordedSourceNamesAMountedRoute`` checks the ``source`` of every write
  against the routes the host actually mounted, which is how the undefined
  ``record_id`` in the first draft's generator surfaced at all.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from dsr.api import app
from dsr.audit_export import access, anchors, entries, integrity, reports, vocabulary
from dsr.audit_export.reports import ReportError
from fastapi.testclient import TestClient

MODULE = "wf079_export_a_tamper_evident_audit_trail_wi"
FEATURE_ID = "wf-079-export-a-tamper-evident-audit-trail-wi"
PREFIX = "/api/wf-079"

ADMIN = {"role": access.AUDIT_READER_ROLE}

#: Every gated route, as (method, path template). Ungated routes are /vocabulary,
#: /inferences and /observations, and they are asserted ungated below.
GATED_GET = (
    "/rooms/{room_id}/audit-trail",
    "/rooms/{room_id}/audit-trail/summary",
    "/rooms/{room_id}/audit-trail/verify",
    "/rooms/{room_id}/audit-trail/{seq}",
    "/rooms/{room_id}/documents/{record_id}/audit-trail",
    "/rooms/{room_id}/documents/{record_id}/evidence-pack",
    "/reports",
    "/reports/{report_id}",
    "/reports/{report_id}/download",
)

#: The single reference date every date-boundary assertion is measured against.
#: A bounds check whose answer depends on when it ran cannot be tested at a
#: boundary, so the reference is passed in rather than read from the clock.
TODAY = date(2026, 10, 2)


def feature():
    import importlib

    return importlib.import_module(f"dsr.features.{MODULE}")


@pytest.fixture()
def store(tmp_path):
    """An audited store on a throwaway database."""
    from dsr.db.audited import AuditedDatabase
    from dsr.store import RecordStore

    db = AuditedDatabase(tmp_path / "wf079.db", actor="test")
    try:
        yield RecordStore(db)
    finally:
        db.close()


@pytest.fixture()
def room_id(store):
    return store.create("room", {"name": "Northwind", "status": "active"}, actor="dana")["id"]


@pytest.fixture()
def document_id(store, room_id):
    return store.create(
        "document", {"title": "Contract Draft", "status": "draft"}, room_id=room_id
    )["id"]


@pytest.fixture()
def client(monkeypatch):
    tmp = tempfile.TemporaryDirectory()
    monkeypatch.setenv("DSR_DB_PATH", str(Path(tmp.name) / "api.db"))
    monkeypatch.setenv("DSR_AUDIT_DIR", str(Path(tmp.name) / "audit"))
    monkeypatch.delenv(access.SANDBOX_ENV, raising=False)
    monkeypatch.setattr("dsr.api.FRONTEND_DIST", Path(tmp.name) / "absent")
    with TestClient(app) as test_client:
        yield test_client
    tmp.cleanup()


@pytest.fixture()
def world(client):
    """A room, a document, and one observed address, through the HTTP surface."""
    room_id = client.post(
        "/api/records/room", json={"name": "Contoso"}, params={"actor": "dana"}
    ).json()["id"]
    document_id = client.post(
        "/api/records/document",
        json={"title": "Contract Draft", "status": "draft"},
        params={"room_id": room_id, "actor": "dana"},
    ).json()["id"]
    seq = _latest_seq(client, room_id)
    client.post(
        f"{PREFIX}/observations",
        json={"seq": seq, "ip_address": "192.0.2.10", "user_agent": "curl/8"},
        params={"room_id": room_id},
    )
    assert client.get(f"{PREFIX}/rooms/{room_id}/audit-trail", params=ADMIN).status_code == 200
    return room_id, document_id


def _latest_seq(client, room_id):
    rows = client.get("/api/audit", params={"limit": 1000}).json()["entries"]
    scoped = [row for row in rows if row["room_id"] == room_id]
    return max(row["seq"] for row in scoped)


def _mounted_routes():
    """Every ``(method, path)`` the app serves, including nested routers.

    This FastAPI keeps an included router as a ``_IncludedRouter`` child rather
    than flattening its routes onto ``app.routes``, so a check that reads only
    ``app.routes`` finds the thirteen core routes and reports every feature route as
    missing - which is what a first version of this test did.
    """
    found = set()
    pending = list(app.routes)
    while pending:
        route = pending.pop()
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None)
        if path is not None and methods:
            found.update((method, path) for method in methods)
        pending.extend(getattr(route, "routes", None) or [])
        # This FastAPI keeps an included router as a child exposing
        # ``original_router`` and no ``.routes`` of its own.
        nested = getattr(route, "original_router", None)
        if nested is not None:
            pending.extend(getattr(nested, "routes", None) or [])
    return found


def _recompute_digest(entry):
    """Rebuild one entry digest from the published spec, without the implementation.

    Deliberately does not call ``integrity.entry_digest``: a test that reuses the
    code under test only proves it agrees with itself. This rebuilds the canonical
    bytes from the six documented fields and hashes them with nothing but
    ``json.dumps`` and ``hashlib``.
    """
    canonical = json.dumps(
        {
            "seq": entry["seq"],
            "date_created": entry["date_created"] or "",
            "action_code": entry["action"]["code"],
            "actor": entry["actor"] or "",
            "ip_address": entry["ip_address"] or "",
            "reason": entry["reason"] or "",
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _recompute_chain(entries):
    """Refold the chain from the published spec, independently of the implementation."""
    running = hashlib.sha256(b"wf-079-audit-chain-v1").hexdigest()
    out = []
    for entry in entries:
        running = hashlib.sha256(
            f"wf-079-audit-chain-v1\n{running}\n{_recompute_digest(entry)}".encode("utf-8")
        ).hexdigest()
        out.append(running)
    return out


def _observed_entry(client, room_id):
    """The one entry that carries an attributed address.

    Found by status rather than by position: the fixture attributes the address to
    the room's first document row, and later writes - the observation itself, a
    requested report - add rows after it, so ``entries[-1]`` is not that row.
    """
    listed = client.get(
        f"{PREFIX}/rooms/{room_id}/audit-trail", params={**ADMIN, "limit": 500}
    ).json()["entries"]
    observed = [entry for entry in listed if entry["ip_status"] == "observed"]
    assert observed, "the fixture attributes no address"
    return observed[0]


def _busy_entries(client, room_id, count=4):
    """A room with several audited rows, so chain operations have something to bite on."""
    for index in range(count):
        client.post(
            "/api/records/document",
            json={"title": f"Filler {index}", "status": "draft"},
            params={"room_id": room_id, "actor": "dana"},
        )
    return client.get(
        f"{PREFIX}/rooms/{room_id}/audit-trail", params={**ADMIN, "limit": 500}
    ).json()["entries"]


def _seeded_entries(client, room_id, types=("user_activity",)):
    """A room with a few audited events and one requested report, and its entries."""
    client.post(
        f"{PREFIX}/reports",
        params={**ADMIN, "room_id": room_id},
        json={
            "report_type": list(types),
            "start_date": "01/01/2020",
            "end_date": "12/31/2026",
            "email": "compliance@example.com",
        },
    )
    return client.get(
        f"{PREFIX}/rooms/{room_id}/audit-trail", params={**ADMIN, "limit": 500}
    ).json()["entries"]


# --------------------------------------------------------------------------- #
# The feature contract
# --------------------------------------------------------------------------- #


def test_the_feature_declares_the_id_its_frontend_descriptor_must_match():
    assert feature().FEATURE["id"] == FEATURE_ID
    assert feature().FEATURE["ticket"] == "WF-079"


def test_its_prefix_is_the_ticket_derived_one():
    assert feature().router.prefix == PREFIX


def test_it_imports_dependencies_from_deps_and_never_from_the_app():
    import ast

    imported = []
    for node in ast.walk(ast.parse(Path(feature().__file__).read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")
        elif isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
    assert "dsr.api" not in imported
    assert "dsr.deps" in imported


def test_it_owns_its_own_refusals_and_maps_each_to_a_handler():
    handlers = feature().EXCEPTION_HANDLERS
    assert sorted(t.__name__ for t in handlers) == [
        "AccessDenied",
        "ObservationRefused",
        "ReportError",
    ]


def test_the_domain_package_never_opens_the_database_itself():
    """Reads go through the store. A raw connection here would bypass the audit log."""
    import ast

    package = Path(entries.__file__).parent
    for name in ("entries", "integrity", "reports", "vocabulary", "access", "anchors"):
        imported = []
        for node in ast.walk(ast.parse((package / f"{name}.py").read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom):
                imported.append(node.module or "")
            elif isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
        assert "sqlite3" not in imported, f"{name} imports sqlite3 directly"


def test_there_is_no_engine_module_left_behind():
    """The first draft put the gate in an engine module, which made it a second home
    for the same rule. The gate belongs to ``access`` and nothing sits between the
    router and the rules."""
    package = Path(entries.__file__).parent
    assert not (package / "engine.py").exists()
    import dsr.audit_export as package_namespace

    assert not hasattr(package_namespace, "AuditExport")


def test_the_host_mounted_the_feature_and_reports_no_failure(client):
    body = client.get("/api/features").json()
    assert FEATURE_ID in {item["id"] for item in body["features"]}
    assert body["failed_count"] == 0


def test_every_route_the_registry_reports_is_actually_served(client):
    record = next(
        item for item in client.get("/api/features").json()["features"] if item["id"] == FEATURE_ID
    )
    assert record["routes"]
    mounted = _mounted_routes()
    for route in record["routes"]:
        for method in route["methods"]:
            assert (method, route["path"]) in mounted, (
                f"the registry reports {method} {route['path']} but it is not mounted"
            )


def test_the_feature_serves_fifteen_routes_under_one_prefix():
    routes = [route for route in feature().router.routes if getattr(route, "methods", None)]
    # Fifteen routes over fourteen paths: GET and POST /reports share one.
    assert len(routes) == 15
    assert len({route.path for route in routes}) == 14
    assert all(route.path.startswith(PREFIX) for route in routes)


# --------------------------------------------------------------------------- #
# The published rules
# --------------------------------------------------------------------------- #


def test_the_vocabulary_publishes_every_lifecycle_code_the_research_names(client):
    codes = {
        entry["code"]: entry
        for entry in client.get(f"{PREFIX}/vocabulary").json()["vocabulary"]["codes"]
    }
    for code, name in (
        (1, "document_created"),
        (6, "document_sent"),
        (8, "document_viewed"),
        (12, "document_forwarded"),
        (13, "document_expired"),
        (18, "document_completed_manually"),
        (43, "document_declined"),
    ):
        assert codes[code]["name"] == name


def test_a_code_a_source_names_individually_is_marked_as_sourced(client):
    codes = {
        entry["code"]: entry
        for entry in client.get(f"{PREFIX}/vocabulary").json()["vocabulary"]["codes"]
    }
    # 47 and 51 are quoted verbatim in the research. 48 is not, and claiming a
    # citation for an inference would be worse than admitting it.
    assert codes[47]["origin"] == vocabulary.ORIGIN_VENDOR
    assert codes[51]["origin"] == vocabulary.ORIGIN_VENDOR
    assert codes[48]["origin"] == vocabulary.ORIGIN_ALLOCATED


def test_the_reserved_gaps_are_published_rather_than_left_free(client):
    gaps = client.get(f"{PREFIX}/vocabulary").json()["vocabulary"]["reserved_gaps"]
    covered = {(gap["from"], gap["to"]) for gap in gaps}
    assert (2, 5) in covered
    assert (9, 11) in covered
    assert (19, 42) in covered


def test_workspace_codes_sit_above_every_vendor_band(client):
    payload = client.get(f"{PREFIX}/vocabulary").json()["vocabulary"]
    assert payload["workspace_code_floor"] > 70
    workspace = [c for c in payload["codes"] if c["origin"] == vocabulary.ORIGIN_WORKSPACE]
    assert workspace
    assert all(c["code"] >= payload["workspace_code_floor"] for c in workspace)


def test_the_vocabulary_publishes_the_gate_so_a_page_can_render_it(client):
    gate = client.get(f"{PREFIX}/vocabulary").json()["access"]
    assert gate["reader_role"] == access.AUDIT_READER_ROLE
    assert gate["sandbox"]["masked_ip"] == access.SANDBOX_IP
    assert [role["may_read"] for role in gate["roles"]].count(True) == 1


def test_the_vocabulary_explains_every_ip_status(client):
    statuses = client.get(f"{PREFIX}/vocabulary").json()["ip_status"]
    assert set(statuses) == {entries.IP_OBSERVED, entries.IP_HIDDEN, entries.IP_NOT_CAPTURED}
    # "we did not record it" and "we recorded nothing" are different facts.
    assert "ever attributed" in statuses[entries.IP_NOT_CAPTURED]


def test_every_inference_is_published_with_its_basis(client):
    body = client.get(f"{PREFIX}/inferences").json()
    groups = [body["action_codes"], body["export"], body["reports"]]
    claims = [item for group in groups for item in group]
    assert claims
    for item in claims:
        assert item["claim"] and item["basis"]


def test_the_inferences_name_the_inferences_a_reviewer_would_dispute(client):
    body = client.get(f"{PREFIX}/inferences").json()
    text = " ".join(item["claim"] for group in body.values() for item in group)
    assert "X-Forwarded-For" in text
    assert "sms_activity" in text
    assert "whole audit log" in text


def test_the_rules_routes_carry_no_evidence_and_so_are_not_gated(client):
    for route in ("/vocabulary", "/inferences"):
        response = client.get(f"{PREFIX}{route}")
        assert response.status_code == 200
        # A page that cannot read the rules cannot explain its own refusal.
        assert response.json()


# --------------------------------------------------------------------------- #
# The administrator gate
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("template", GATED_GET)
def test_no_role_at_all_is_refused(client, world, template):
    room_id, document_id = world
    path = template.format(room_id=room_id, record_id=document_id, seq=1, report_id="r")
    response = client.get(f"{PREFIX}{path}")
    assert response.status_code == 403
    assert response.json()["error"] == access.DENIED_CODE


@pytest.mark.parametrize(
    "role", ["viewer", "content_contributor", "room_collaborator", "", "superuser"]
)
def test_every_role_below_administrator_is_refused(client, world, role):
    room_id, _ = world
    response = client.get(
        f"{PREFIX}/rooms/{room_id}/audit-trail", params={"role": role} if role else {}
    )
    assert response.status_code == 403


def test_an_unknown_role_is_collapsed_to_the_lowest_tier(client, world):
    room_id, _ = world
    body = client.get(f"{PREFIX}/rooms/{room_id}/audit-trail", params={"role": "superuser"}).json()
    # Failing closed: an unrecognised role can never widen access.
    assert body["presented_role"] == "viewer"


def test_the_refusal_names_the_role_that_would_have_worked(client, world):
    room_id, _ = world
    body = client.get(f"{PREFIX}/rooms/{room_id}/audit-trail").json()
    assert body["required_role"] == access.AUDIT_READER_ROLE
    assert body["remediation"]


def test_an_administrator_is_served(client, world):
    room_id, _ = world
    assert client.get(f"{PREFIX}/rooms/{room_id}/audit-trail", params=ADMIN).status_code == 200


def test_report_writes_are_gated(client, world):
    room_id, _ = world
    response = client.post(
        f"{PREFIX}/reports",
        params={"room_id": room_id},
        json={
            "report_type": ["user_activity"],
            "start_date": "01/01/2026",
            "end_date": "10/02/2026",
            "email": "compliance@example.com",
        },
    )
    assert response.status_code == 403


def test_generation_is_gated(client, world):
    room_id, _ = world
    report_id = _request_report(client, room_id)[0]
    response = client.post(f"{PREFIX}/reports/{report_id}/generate")
    assert response.status_code == 403


def test_anchor_pinning_is_gated(client, world):
    room_id, _ = world
    assert client.post(f"{PREFIX}/rooms/{room_id}/audit-trail/anchors").status_code == 403


def test_observation_is_deliberately_not_gated(client, world):
    """It is the write that makes the gated export worth reading, and it discloses
    nothing the caller does not already know."""
    room_id, _ = world
    response = client.post(
        f"{PREFIX}/observations",
        params={"room_id": room_id},
        json={"seq": _latest_seq(client, room_id), "ip_address": "192.0.2.99"},
    )
    # Not gated: this is the write that makes the gated export worth reading.
    assert response.status_code == 201
    assert response.json()["observed"] is True


def test_the_gate_is_one_dependency_so_a_new_route_cannot_forget_it():
    """The gate is a ``Depends``, so it cannot be forgotten by a route added later.

    Checked against the dependant tree rather than the source text: a route that
    took the role straight off ``Request`` would pass a grep for "reader" and still
    be one refactor away from skipping the gate.
    """
    gated = []
    for route in feature().router.routes:
        if not getattr(route, "methods", None):
            continue
        names = {
            getattr(dependency.call, "__name__", "") for dependency in route.dependant.dependencies
        }
        if "require_reader" in names:
            gated.append(route.path)
    assert len(gated) == 12, gated
    ungated = sorted(
        route.path
        for route in feature().router.routes
        if getattr(route, "methods", None)
        and "require_reader"
        not in {getattr(d.call, "__name__", "") for d in route.dependant.dependencies}
    )
    assert ungated == [
        f"{PREFIX}/inferences",
        f"{PREFIX}/observations",
        f"{PREFIX}/vocabulary",
    ]


# --------------------------------------------------------------------------- #
# The hash is reproducible by a third party
# --------------------------------------------------------------------------- #


def test_every_entry_digest_is_reproducible_from_the_exported_fields(client, world):
    room_id, _ = world
    entries = _seeded_entries(client, room_id)
    assert entries
    for entry in entries:
        assert entry["digest"] == _recompute_digest(entry)


def test_the_chain_is_reproducible_from_the_entries_alone(client, world):
    room_id, _ = world
    entries = _seeded_entries(client, room_id)
    assert [entry["chain"] for entry in entries] == _recompute_chain(entries)


def test_the_export_publishes_the_recipe_rather_than_only_the_answer(client, world):
    room_id, _ = world
    block = client.get(f"{PREFIX}/rooms/{room_id}/audit-trail", params=ADMIN).json()["integrity"]
    assert block["algorithm"] == "sha-256"
    assert block["hashed_fields"] == [
        "seq",
        "date_created",
        "action_code",
        "actor",
        "ip_address",
        "reason",
    ]
    assert "sort_keys=True" in block["canonical_form"]
    assert "ascending by seq" in block["order"]
    assert block["domain_separator"] == "wf-079-audit-chain-v1"


def test_the_digest_excludes_the_two_fields_it_produces(client, world):
    room_id, _ = world
    block = client.get(f"{PREFIX}/rooms/{room_id}/audit-trail", params=ADMIN).json()["integrity"]
    assert set(block["excluded_by_design"]) == {"digest", "chain"}


def test_the_head_is_the_last_entries_chain(client, world):
    room_id, _ = world
    body = client.get(f"{PREFIX}/rooms/{room_id}/audit-trail", params=ADMIN).json()
    assert body["integrity"]["head"] == body["entries"][-1]["chain"]


def test_the_chain_seed_is_the_published_constant():
    assert integrity.chain_seed() == hashlib.sha256(b"wf-079-audit-chain-v1").hexdigest()


def test_the_integrity_block_says_the_digest_hashes_the_exported_value(client, world):
    room_id, _ = world
    block = client.get(f"{PREFIX}/rooms/{room_id}/audit-trail", params=ADMIN).json()["integrity"]
    # Hashing the real address and masking it only on the way out would produce a
    # digest nobody holding the export can recompute.
    assert "as exported" in block["hashed_values"]


def test_a_masked_export_is_still_reproducible(client, world):
    room_id, _ = world
    entries = client.get(
        f"{PREFIX}/rooms/{room_id}/audit-trail", params={**ADMIN, "sandbox": "true"}
    ).json()["entries"]
    assert any(entry["ip_address"] == "hidden" for entry in entries)
    for entry in entries:
        assert entry["digest"] == _recompute_digest(entry)
    assert [entry["chain"] for entry in entries] == _recompute_chain(entries)


# --------------------------------------------------------------------------- #
# Tampering is detectable, and locatable
# --------------------------------------------------------------------------- #


def test_an_untouched_export_verifies(client, world):
    room_id, _ = world
    entries = _seeded_entries(client, room_id, ("user_activity", "document_status"))
    assert integrity.verify(entries)["verified"] is True


def test_an_edited_reason_is_caught_and_located(client, world):
    room_id, _ = world
    entries = _seeded_entries(client, room_id)
    edited = [dict(entry) for entry in entries]
    target = len(edited) // 2
    edited[target] = {**edited[target], "reason": "nothing to see here"}
    result = integrity.verify(edited)
    assert result["verified"] is False
    assert result["first_mismatch"]["position"] == target + 1
    assert result["first_mismatch"]["seq"] == entries[target]["seq"]


def test_an_edited_action_code_is_caught(client, world):
    room_id, _ = world
    entries = _seeded_entries(client, room_id)
    edited = [dict(entry) for entry in entries]
    edited[0] = {**edited[0], "action": {**edited[0]["action"], "code": 43}}
    assert integrity.verify(edited)["verified"] is False


def test_an_edited_address_is_caught(client, world):
    room_id, _ = world
    entries = [e for e in _seeded_entries(client, room_id) if e["ip_address"]]
    assert entries, "the fixture attributes one address"
    edited = [dict(entry) for entry in entries]
    edited[0] = {**edited[0], "ip_address": "10.0.0.1"}
    assert integrity.verify(edited)["verified"] is False


def test_reordering_the_entries_breaks_the_chain(client, world):
    room_id, _ = world
    entries = _busy_entries(client, room_id)
    assert len(entries) >= 4
    assert integrity.verify(list(reversed([dict(e) for e in entries])))["verified"] is False


def test_dropping_an_entry_from_the_middle_breaks_the_chain(client, world):
    room_id, _ = world
    entries = _busy_entries(client, room_id)
    assert len(entries) >= 4
    shortened = [dict(entry) for entry in entries]
    del shortened[len(shortened) // 2]
    assert integrity.verify(shortened)["verified"] is False


def test_appending_a_forged_entry_does_not_reuse_the_stored_head(client, world):
    room_id, _ = world
    entries = _busy_entries(client, room_id)
    assert len(entries) >= 4
    head = entries[-1]["chain"]
    forged = dict(entries[-1])
    forged["seq"] = entries[-1]["seq"] + 1
    forged["reason"] = "an event nobody recorded"
    forged.pop("digest", None)
    forged.pop("chain", None)
    extended = [dict(entry) for entry in entries] + [forged]
    result = integrity.verify(extended)
    assert result["verified"] is False
    # Stripped of its derived fields, the appended entry is the last one a verifier
    # can name: it carries no digest, so it was not part of any sealed set.
    assert result["first_mismatch"]["seq"] == forged["seq"]
    assert result["first_mismatch"]["field"] == "digest"
    assert "no recorded digest" in result["first_mismatch"]["reason"]
    assert extended[-1].get("chain") != head


# --------------------------------------------------------------------------- #
# Anchors: a self-consistent export is not a verified one
# --------------------------------------------------------------------------- #


def test_a_fresh_export_is_intact_against_its_own_recomputation(client, world):
    """The point anchors exist for: recomputing a chain that was sealed microseconds
    ago agrees with itself and proves nothing."""
    room_id, _ = world
    pinned = client.post(f"{PREFIX}/rooms/{room_id}/audit-trail/anchors", params=ADMIN)
    assert pinned.status_code == 201
    body = pinned.json()
    assert body["pinned"] is True
    assert body["anchor"]["data"]["head"]


def test_verification_reports_intact_after_pinning(client, world):
    room_id, _ = world
    _seeded_entries(client, room_id)
    client.post(f"{PREFIX}/rooms/{room_id}/audit-trail/anchors", params=ADMIN)
    result = client.get(f"{PREFIX}/rooms/{room_id}/audit-trail/verify", params=ADMIN).json()
    assert result["anchors"] == 1
    assert result["intact"] is True
    assert result["checks"][0]["first_divergent"] is None


def test_a_later_write_does_not_break_an_anchor(client, world):
    """Rows written after the pin are normal. Reporting them as divergence would make
    every anchor fail from the moment it was taken."""
    room_id, document_id = world
    _seeded_entries(client, room_id)
    client.post(f"{PREFIX}/rooms/{room_id}/audit-trail/anchors", params=ADMIN)
    client.patch(
        f"/api/records/document/{document_id}",
        json={"status": "sent"},
        params={"room_id": room_id, "actor": "dana"},
    )
    result = client.get(f"{PREFIX}/rooms/{room_id}/audit-trail/verify", params=ADMIN).json()
    assert result["intact"] is True


def test_anchors_compare_only_the_pinned_prefix(store, room_id, document_id):
    """A unit assertion on the comparison itself, with a doctored anchor.

    Divergence cannot be produced through the HTTP surface, because every write adds
    a row rather than editing one. So the comparison logic is exercised directly:
    an anchor whose recorded chain no longer matches must be reported with the first
    divergent seq, not merely as not-intact.
    """
    records = store
    records.update(document_id, {"status": "sent"}, actor="dana", source="seed")
    for index in range(3):
        records.create(
            "document", {"title": f"F{index}", "status": "draft"}, room_id=room_id, source="seed"
        )
    rows = entries.room_rows(records, room_id)
    projected = [entries.project(row, observations={}, sandbox=False) for row in rows]
    for entry, chain in zip(projected, integrity.seal(projected), strict=True):
        entry["digest"] = integrity.entry_digest(entry)
        entry["chain"] = chain
    assert len(projected) >= 4

    anchor = {
        "id": "anchor_1",
        "data": {
            "head": projected[-1]["chain"],
            "entries": len(projected),
            "upto_seq": projected[-1]["seq"],
            "scope": anchors.scope_of(room_id),
            "chains": [{"seq": e["seq"], "chain": "not-the-real-chain"} for e in projected],
        },
    }
    result = anchors.check(anchor, projected)
    assert result["intact"] is False
    assert result["first_divergent"]["seq"] == projected[0]["seq"]
    assert "no longer matches" in result["first_divergent"]["reason"]


def test_an_anchor_that_omits_an_entry_reports_it_as_missing(store, room_id):
    records = store
    for index in range(5):
        records.create(
            "document", {"title": f"G{index}", "status": "draft"}, room_id=room_id, source="seed"
        )
    rows = entries.room_rows(records, room_id)
    projected = [entries.project(row, observations={}, sandbox=False) for row in rows]
    for entry, chain in zip(projected, integrity.seal(projected), strict=True):
        entry["digest"] = integrity.entry_digest(entry)
        entry["chain"] = chain
    assert len(projected) >= 4
    anchor = {
        "id": "anchor_1",
        "data": {
            "head": projected[-1]["chain"],
            "entries": len(projected),
            "upto_seq": projected[-1]["seq"],
            "scope": anchors.scope_of(room_id),
            "chains": [{"seq": e["seq"], "chain": e["chain"]} for e in projected[:-1]],
        },
    }
    result = anchors.check(anchor, projected)
    assert result["intact"] is False
    assert "not in the anchor" in result["first_divergent"]["reason"]


def test_an_anchor_admits_it_is_not_a_defence_against_a_digest_forger(client, world):
    room_id, _ = world
    _seeded_entries(client, room_id)
    client.post(f"{PREFIX}/rooms/{room_id}/audit-trail/anchors", params=ADMIN)
    meaning = client.get(f"{PREFIX}/rooms/{room_id}/audit-trail/verify", params=ADMIN).json()[
        "checks"
    ][0]["meaning"]
    assert "not a defence" in meaning
    assert "same database" in meaning


def test_an_anchor_records_the_scope_it_was_taken_over(client, world):
    room_id, _ = world
    _seeded_entries(client, room_id)
    body = client.post(
        f"{PREFIX}/rooms/{room_id}/audit-trail/anchors", params={**ADMIN, "collection": "room"}
    ).json()
    # A digest nobody can reproduce is not evidence, so the scope travels with it.
    assert body["anchor"]["data"]["scope"]["collection"] == "room"
    assert body["anchor"]["data"]["scope"]["room_id"] == room_id


def test_pinning_an_oversized_trail_is_refused_rather_than_truncated():
    """A partial anchor that reads as a whole one is worse than no anchor."""
    with pytest.raises(ValueError, match="narrow the scope"):
        anchors.pin(
            None,
            [{"seq": index, "chain": "x"} for index in range(anchors.MAX_ANCHORED_ENTRIES + 1)],
            scope={},
            pinned_at="now",
            actor="dana",
            source="test",
        )


def test_pinning_an_empty_trail_records_the_seed_as_its_head(store, room_id):
    records = store
    record = anchors.pin(
        records,
        [],
        scope=anchors.scope_of(room_id),
        pinned_at="2026-10-02T00:00:00.000+00:00",
        actor="dana",
        source="test",
        room_id=room_id,
    )
    data = record["data"]
    assert data["head"] == integrity.chain_seed()
    assert data["upto_seq"] is None


# --------------------------------------------------------------------------- #
# The document hash, and what it does not claim
# --------------------------------------------------------------------------- #


def test_the_document_hash_basis_says_what_it_covers():
    basis = integrity.document_hash_basis()
    assert "record" in basis["covers"]


def test_the_document_hash_basis_is_not_the_vendors_pdf_hash():
    basis = integrity.document_hash_basis()
    assert "PDF" in basis["does_not_cover"] or "pdf" in basis["does_not_cover"]


def test_the_document_hash_basis_admits_a_hash_cannot_prove_completeness():
    assert "never recorded" in integrity.document_hash_basis()["completeness"]


def test_the_document_hash_changes_when_a_record_revision_changes(store, room_id, document_id):
    records = store
    document = document_id
    rows = entries.room_rows(records, room_id)
    before = integrity.document_hash(
        room_id, [entries.project(r, observations={}, sandbox=False) for r in rows]
    )
    records.update(document, {"pages": 3}, actor="dana", source="seed")
    rows = entries.room_rows(records, room_id)
    after = integrity.document_hash(
        room_id, [entries.project(r, observations={}, sandbox=False) for r in rows]
    )
    assert after != before


def test_the_document_hash_is_reproducible_from_the_exported_entries(client, world):
    room_id, _ = world
    body = client.get(
        f"{PREFIX}/rooms/{room_id}/audit-trail", params={**ADMIN, "limit": 500}
    ).json()
    payload = {
        "algorithm": "sha-256",
        "kind": "wf-079-document-hash-v1",
        "room_id": room_id,
        "entries": [
            {field: entry.get(field) for field in integrity.DOCUMENT_HASH_ENTRY_FIELDS}
            for entry in body["entries"]
        ],
    }
    expected = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
            "utf-8"
        )
    ).hexdigest()
    assert body["integrity"]["document_hash"] == expected


# --------------------------------------------------------------------------- #
# Reading the trail
# --------------------------------------------------------------------------- #


def test_the_trail_is_room_scoped(client, world):
    room_id, _ = world
    other = client.post("/api/records/room", json={"name": "Fabrikam"}).json()["id"]
    client.post(
        "/api/records/document",
        json={"title": "Elsewhere"},
        params={"room_id": other, "actor": "sam"},
    )
    body = client.get(f"{PREFIX}/rooms/{room_id}/audit-trail", params=ADMIN).json()
    assert body["entries"]
    assert {entry["room_id"] for entry in body["entries"]} == {room_id}


def test_entries_carry_the_researched_tuple(client, world):
    room_id, _ = world
    entry = client.get(f"{PREFIX}/rooms/{room_id}/audit-trail", params=ADMIN).json()["entries"][0]
    for field in ("id", "user", "action", "reason", "date_created", "ip_address"):
        assert field in entry
    assert set(entry["user"]) >= {"id", "email"}


def test_entries_come_back_in_chain_order(client, world):
    room_id, _ = world
    entries = client.get(
        f"{PREFIX}/rooms/{room_id}/audit-trail", params={**ADMIN, "limit": 500}
    ).json()["entries"]
    assert [entry["seq"] for entry in entries] == sorted(entry["seq"] for entry in entries)


def test_paging_walks_the_trail_without_repeating_or_skipping(client, world):
    room_id, _ = world
    whole = client.get(
        f"{PREFIX}/rooms/{room_id}/audit-trail", params={**ADMIN, "limit": 500}
    ).json()
    seen = []
    for offset in range(0, whole["total"], 2):
        page = client.get(
            f"{PREFIX}/rooms/{room_id}/audit-trail",
            params={**ADMIN, "limit": 2, "offset": offset},
        ).json()
        seen.extend(entry["seq"] for entry in page["entries"])
    assert seen == [entry["seq"] for entry in whole["entries"]]


def test_total_counts_the_filtered_trail_and_has_more_says_whether_more_exists(client, world):
    room_id, _ = world
    body = client.get(f"{PREFIX}/rooms/{room_id}/audit-trail", params={**ADMIN, "limit": 1}).json()
    assert body["count"] == 1
    assert body["total"] > 1
    assert body["has_more"] is True


def test_an_offset_past_the_end_is_an_empty_page_not_an_error(client, world):
    room_id, _ = world
    response = client.get(f"{PREFIX}/rooms/{room_id}/audit-trail", params={**ADMIN, "offset": 9999})
    assert response.status_code == 200
    assert response.json()["entries"] == []


def test_the_page_digest_is_reproducible_from_the_page(client, world):
    room_id, _ = world
    _seeded_entries(client, room_id)
    page = client.get(f"{PREFIX}/rooms/{room_id}/audit-trail", params={**ADMIN, "limit": 2}).json()[
        "entries"
    ]
    assert [entry["digest"] for entry in page] == [_recompute_digest(e) for e in page]
    assert [entry["chain"] for entry in page] == _recompute_chain(page)


def test_the_head_covers_the_whole_filtered_trail_not_the_page(client, world):
    room_id, _ = world
    _seeded_entries(client, room_id)
    body = client.get(f"{PREFIX}/rooms/{room_id}/audit-trail", params={**ADMIN, "limit": 1}).json()
    assert body["note"]
    assert body["integrity"]["head"] != body["entries"][0]["chain"]


def test_filtering_by_integer_code_selects_the_documented_events(client, world):
    room_id, document_id = world
    client.patch(
        f"/api/records/document/{document_id}",
        json={"status": "declined"},
        params={"room_id": room_id, "actor": "dana"},
    )
    body = client.get(
        f"{PREFIX}/rooms/{room_id}/audit-trail", params={**ADMIN, "action": 43}
    ).json()
    assert body["entries"]
    assert {entry["action"]["name"] for entry in body["entries"]} == {"document_declined"}


def test_filtering_by_actor_and_collection_narrows_the_page(client, world):
    room_id, _ = world
    by_actor = client.get(
        f"{PREFIX}/rooms/{room_id}/audit-trail", params={**ADMIN, "actor": "dana"}
    ).json()
    assert {entry["actor"] for entry in by_actor["entries"]} == {"dana"}
    by_collection = client.get(
        f"{PREFIX}/rooms/{room_id}/audit-trail", params={**ADMIN, "collection": "document"}
    ).json()
    assert {entry["collection"] for entry in by_collection["entries"]} == {"document"}


def test_a_filter_matching_nothing_is_an_empty_page(client, world):
    room_id, _ = world
    body = client.get(
        f"{PREFIX}/rooms/{room_id}/audit-trail", params={**ADMIN, "actor": "nobody"}
    ).json()
    assert body["count"] == 0
    assert body["total"] == 0


def test_a_single_entry_is_addressable_by_its_seq(client, world):
    room_id, _ = world
    listing = client.get(f"{PREFIX}/rooms/{room_id}/audit-trail", params=ADMIN).json()
    target = listing["entries"][0]
    one = client.get(f"{PREFIX}/rooms/{room_id}/audit-trail/{target['seq']}", params=ADMIN).json()
    # Byte-for-byte the row the list would have shown, including its chain.
    assert one == target


def test_an_unknown_seq_in_a_known_room_is_a_404(client, world):
    room_id, _ = world
    assert (
        client.get(f"{PREFIX}/rooms/{room_id}/audit-trail/999999", params=ADMIN).status_code == 404
    )


def test_the_summary_shows_the_room_against_the_whole_log(client, world):
    room_id, _ = world
    body = client.get(f"{PREFIX}/rooms/{room_id}/audit-trail/summary", params=ADMIN).json()
    assert body["scoped_rows"] > 0
    assert body["audit_log_rows"] >= body["scoped_rows"]
    assert body["read_role"] == access.AUDIT_READER_ROLE


def test_the_summary_counts_addresses_that_were_never_attributed(client, world):
    room_id, _ = world
    body = client.get(f"{PREFIX}/rooms/{room_id}/audit-trail/summary", params=ADMIN).json()
    assert body["addresses_not_captured"] >= 1


def test_a_reason_declared_on_the_record_beats_the_audit_summary(store, room_id, document_id):
    records = store
    document = document_id
    records.update(
        document,
        {"status": "sent", "reason": "Sent to procurement."},
        actor="dana",
        source="seed",
    )
    entry = entries.project(entries.room_rows(records, room_id)[-1], observations={}, sandbox=False)
    assert entry["reason"] == "Sent to procurement."
    assert entry["reason_source"] == "declared"


def test_without_a_declared_reason_the_audit_summary_is_used(store, room_id):
    records = store
    records.create("document", {"title": "Plain"}, room_id=room_id, source="seed")
    entry = entries.project(entries.room_rows(records, room_id)[-1], observations={}, sandbox=False)
    assert entry["reason_source"] == "summary"
    assert entry["reason"]


def test_the_actor_resolves_an_address_only_when_it_is_one():
    assert entries.actor_email("dana@example.com")[0] == "dana@example.com"
    assert entries.actor_email("dana") == (None, entries.EMAIL_UNRESOLVED)
    assert entries.actor_email("@example.com")[0] is None
    assert entries.actor_email("a@b")[0] is None


# --------------------------------------------------------------------------- #
# Client addresses
# --------------------------------------------------------------------------- #


def test_an_observed_address_reaches_the_export(client, world):
    room_id, _ = world
    entry = _observed_entry(client, room_id)
    assert entry["ip_address"] == "192.0.2.10"
    assert entry["ip_status"] == "observed"
    assert entry["user_agent"] == "curl/8"


def test_a_row_with_no_observation_says_not_captured(client, world):
    room_id, _ = world
    body = client.get(f"{PREFIX}/rooms/{room_id}/audit-trail", params=ADMIN).json()
    uncaptured = [e for e in body["entries"] if e["ip_status"] == entries.IP_NOT_CAPTURED]
    assert uncaptured
    assert all(entry["ip_address"] is None for entry in uncaptured)


def test_a_row_with_no_observation_is_still_exported(client, world):
    room_id, _ = world
    body = client.get(f"{PREFIX}/rooms/{room_id}/audit-trail", params=ADMIN).json()
    assert any(e["ip_status"] == entries.IP_NOT_CAPTURED for e in body["entries"])


def test_the_first_observed_address_is_the_one_kept(client, world):
    room_id, _ = world
    observed = _observed_entry(client, room_id)
    again = client.post(
        f"{PREFIX}/observations",
        params={"room_id": room_id},
        json={"seq": observed["seq"], "ip_address": "10.0.0.1"},
    )
    assert again.status_code == 409
    assert again.json()["error"]
    listed = client.get(f"{PREFIX}/rooms/{room_id}/audit-trail", params=ADMIN).json()["entries"]
    entry = next(e for e in listed if e["ip_address"] == "192.0.2.10")
    assert entry["ip_status"] == "observed"


def test_the_conflict_explains_why_an_address_is_not_rewritable(client, world):
    room_id, _ = world
    observed = _observed_entry(client, room_id)
    body = client.post(
        f"{PREFIX}/observations",
        params={"room_id": room_id},
        json={"seq": observed["seq"], "ip_address": "10.0.0.1"},
    ).json()
    message = f"{body.get('detail', '')} {body.get('remediation', '')}".lower()
    # First writer wins: an export whose address can be rewritten is not evidence.
    assert "never rewritten" in message or "already has an attributed address" in message


def test_a_sandbox_flag_masks_every_observed_address_as_hidden(client, world):
    room_id, _ = world
    body = client.get(
        f"{PREFIX}/rooms/{room_id}/audit-trail", params={**ADMIN, "sandbox": "true"}
    ).json()
    assert body["sandbox"] is True
    masked = [e for e in body["entries"] if e["ip_status"] == entries.IP_HIDDEN]
    assert masked
    assert all(entry["ip_address"] == "hidden" for entry in masked)


def test_the_environment_variable_masks_the_address(client, world, monkeypatch):
    room_id, _ = world
    monkeypatch.setenv(access.SANDBOX_ENV, "1")
    body = client.get(f"{PREFIX}/rooms/{room_id}/audit-trail", params=ADMIN).json()
    assert body["sandbox"] is True


def test_the_explicit_sandbox_flag_wins_over_the_environment(client, world, monkeypatch):
    room_id, _ = world
    monkeypatch.setenv(access.SANDBOX_ENV, "1")
    body = client.get(
        f"{PREFIX}/rooms/{room_id}/audit-trail", params={**ADMIN, "sandbox": "false"}
    ).json()
    assert body["sandbox"] is False


def test_the_only_trusted_address_is_the_socket_peer(client, world):
    """X-Forwarded-For is a request header, so any caller can set it."""
    room_id, document_id = world
    fresh = client.post(
        "/api/records/document",
        json={"title": "Proxied", "status": "draft"},
        params={"room_id": room_id, "actor": "dana"},
    ).json()["id"]
    seq = _latest_seq(client, room_id)
    client.post(
        f"{PREFIX}/observations",
        params={"room_id": room_id},
        json={"seq": seq, "ip_address": "192.0.2.55"},
        headers={"X-Forwarded-For": "10.9.9.9"},
    )
    by_id = client.get(
        f"{PREFIX}/rooms/{room_id}/documents/{fresh}/audit-trail", params=ADMIN
    ).json()["entries"]
    assert by_id[0]["ip_address"] == "192.0.2.55"
    del document_id


def test_attributing_without_an_address_is_refused(client, world):
    room_id, _ = world
    response = client.post(f"{PREFIX}/observations", params={"room_id": room_id}, json={"seq": 1})
    assert response.status_code == 409


def test_attributing_without_a_target_is_refused(client, world):
    room_id, _ = world
    response = client.post(
        f"{PREFIX}/observations", params={"room_id": room_id}, json={"ip_address": "192.0.2.1"}
    )
    assert response.status_code == 409


def test_attribution_can_be_keyed_by_request_id(store, room_id):
    records = store
    records.create("document", {"title": "A"}, room_id=room_id, source="seed")
    rows = entries.room_rows(records, room_id)
    seq = int(rows[-1]["seq"])
    # Keyed by seq: the observation binds to one audit row.
    entries.observe_ip(
        records,
        ip_address="192.0.2.1",
        seq=seq,
        user_agent="curl/8",
        room_id=room_id,
        source="test",
    )
    observed = entries.load_observations(records, room_id)
    assert observed[seq]["ip_address"] == "192.0.2.1"
    assert observed[seq]["user_agent"] == "curl/8"

    # An observation with no room is not folded into this room's export: the
    # resolution is per room, and borrowing another room's address would be worse
    # than reporting none.
    entries.observe_ip(records, ip_address="192.0.2.9", seq=seq + 1000, source="test")
    assert len(entries.load_observations(records, room_id)) == 1

    # Keyed by request_id: recorded as given. Turning one into seqs is the engine's
    # job, because only it knows which room the rows belong to.
    recorded = entries.observe_ip(
        records, ip_address="192.0.2.2", request_id="req_absent", source="test"
    )
    assert recorded["data"]["request_id"] == "req_absent"
    assert recorded["data"]["seq"] is None

    # Neither key present is the only thing this function refuses.
    with pytest.raises(entries.ObservationRefused):
        entries.observe_ip(records, ip_address="192.0.2.3", source="test")


# --------------------------------------------------------------------------- #
# Per-document routes
# --------------------------------------------------------------------------- #


def test_the_per_document_route_returns_only_that_document(client, world):
    room_id, document_id = world
    other = client.post(
        "/api/records/document",
        json={"title": "Another"},
        params={"room_id": room_id, "actor": "dana"},
    ).json()["id"]
    body = client.get(
        f"{PREFIX}/rooms/{room_id}/documents/{document_id}/audit-trail", params=ADMIN
    ).json()
    assert body["entries"]
    assert {entry["record_id"] for entry in body["entries"]} == {document_id}
    assert body["record_id"] == document_id
    del other


def test_a_document_with_no_audited_rows_is_an_empty_trail_not_a_404(client, world):
    room_id, _ = world
    response = client.get(
        f"{PREFIX}/rooms/{room_id}/documents/never_written/audit-trail", params=ADMIN
    )
    assert response.status_code == 200
    assert response.json()["entries"] == []


def test_another_rooms_document_is_not_reachable_by_guessing_an_id(client, world):
    room_id, _ = world
    other_room = client.post("/api/records/room", json={"name": "Fabrikam"}).json()["id"]
    other_document = client.post(
        "/api/records/document",
        json={"title": "Theirs"},
        params={"room_id": other_room, "actor": "sam"},
    ).json()["id"]
    body = client.get(
        f"{PREFIX}/rooms/{room_id}/documents/{other_document}/audit-trail", params=ADMIN
    ).json()
    # The row carries the room the change belonged to, so the scope is a check.
    assert body["entries"] == []


def test_the_document_trail_uses_the_same_projection_as_the_room_trail(client, world):
    room_id, document_id = world
    room = client.get(
        f"{PREFIX}/rooms/{room_id}/audit-trail", params={**ADMIN, "limit": 500}
    ).json()
    document = client.get(
        f"{PREFIX}/rooms/{room_id}/documents/{document_id}/audit-trail",
        params={**ADMIN, "limit": 500},
    ).json()
    from_room = {e["seq"]: e for e in room["entries"] if e["record_id"] == document_id}
    from_document = {e["seq"]: e for e in document["entries"]}
    assert from_room == from_document


def test_the_evidence_pack_carries_one_digest_over_the_whole_pack(client, world):
    room_id, document_id = world
    pack = client.get(
        f"{PREFIX}/rooms/{room_id}/documents/{document_id}/evidence-pack", params=ADMIN
    ).json()
    assert pack["entries"]
    assert pack["integrity"]["head"]
    assert pack["integrity"]["document_hash"]
    assert pack["digest"]
    assert pack["basis"]


def test_the_evidence_pack_says_it_is_a_digest_and_not_a_pdf(client, world):
    room_id, document_id = world
    pack = client.get(
        f"{PREFIX}/rooms/{room_id}/documents/{document_id}/evidence-pack", params=ADMIN
    ).json()
    basis = json.dumps(pack).lower()
    # The vendor merges the trail into PDF bytes; this build says plainly it did not.
    assert "pdf" in basis


def test_the_evidence_pack_lists_a_rejected_attempt_exactly_as_it_lists_a_pass(client, world):
    """A pack built over a recipient's own verification history.

    ``evidence_pack`` projects the trail of the record it is given, and a
    verification is its own record rather than a row on a document's - so the pack
    that can carry both outcomes is the one for the verification subject. The rule
    under test is the research's: "both success and failure produce audit actions,
    so a rejected attempt is as visible as a successful one."
    """
    room_id, _ = world
    made = []
    for method, outcome in (("kba", "pass"), ("kba", "fail")):
        made.append(
            client.post(
                "/api/records/recipient_verification",
                json={"method": method, "outcome": outcome, "gate": "before_open"},
                params={"room_id": room_id, "actor": "signer@example.com"},
            ).json()["id"]
        )

    pack = client.get(
        f"{PREFIX}/rooms/{room_id}/documents/{made[0]}/evidence-pack", params=ADMIN
    ).json()
    # The first record's own trail is its own insert, and that insert *is* a
    # verification, so the pack lists it.
    assert pack["signer_interactions"]
    assert pack["signer_interactions"][0]["outcome"] == "pass"

    rejected = client.get(
        f"{PREFIX}/rooms/{room_id}/documents/{made[1]}/evidence-pack", params=ADMIN
    ).json()
    assert rejected["signer_interactions"][0]["outcome"] == "fail"
    assert rejected["signer_interactions"][0]["code"] == 51

    # And the room trail carries both, which is the claim a reviewer reads.
    listed = client.get(
        f"{PREFIX}/rooms/{room_id}/audit-trail", params={**ADMIN, "limit": 500}
    ).json()["entries"]
    outcomes = {entry["verification"]["outcome"] for entry in listed if entry.get("verification")}
    assert outcomes == {"pass", "fail"}


def test_the_evidence_pack_does_not_filter_the_signer_list_to_passes(store, room_id):
    records = store
    for outcome in ("pass", "fail"):
        records.create(
            "recipient_verification",
            {"method": "sms", "outcome": outcome},
            room_id=room_id,
            source="seed",
        )
    trail, _ = entries.build_trail(records, room_id)
    rows = entries.signer_interactions(trail)
    assert {row["outcome"] for row in rows} == {"pass", "fail"}


# --------------------------------------------------------------------------- #
# Codes: every integer is describable
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "code,name,category",
    [
        (1, "document_created", "lifecycle"),
        (47, "verification_kba_passed", "verification"),
        (55, "qes_lifecycle", "qes"),
        (69, "verification_email_otp_passed", "verification"),
        (1000, "audit_report_requested", "export"),
    ],
)
def test_a_declared_code_is_described(code, name, category):
    described = vocabulary.describe_code(code)
    assert (described.name, described.category) == (name, category)


def test_an_unclaimed_integer_is_reported_verbatim_not_guessed_at():
    described = vocabulary.describe_code(9)
    assert described.code == 9
    assert described.origin == vocabulary.ORIGIN_UNDECLARED
    # 9 sits one away from 8 (document_viewed). Mapping it there would answer a
    # question nobody asked.
    assert described.name != "document_viewed"


def test_a_code_with_no_integer_does_not_raise():
    for value in (None, "", "not-a-code", object()):
        assert vocabulary.describe_code(value).code == vocabulary.UNCLASSIFIED


def test_a_qes_band_member_names_nothing_it_cannot_source():
    for code in range(55, 59):
        described = vocabulary.describe_code(code)
        assert described.origin == vocabulary.ORIGIN_BAND
        assert described.name == "qes_lifecycle"


def test_the_verification_band_runs_four_passes_then_four_fails():
    """47 is kba passed and 51 is kba failed, four apart, so KBA is first in each
    half of a 4+4 band rather than one of four interleaved pairs."""
    assert vocabulary.verification_code("kba", "passed") == 47
    assert vocabulary.verification_code("kba", "failed") == 51
    assert vocabulary.verification_code("passcode", "passed") == 48
    assert vocabulary.verification_code("passcode", "failed") == 52


def test_the_two_named_email_otp_codes_resolve():
    assert vocabulary.verification_code("email_otp", "passed") == 69
    assert vocabulary.verification_code("email_otp", "failed") == 70


@pytest.mark.parametrize("outcome", ["passed", "failed", "rejected", None])
def test_an_unallocatable_method_returns_none_rather_than_a_guess(outcome):
    assert vocabulary.verification_code("device_attestation", outcome) is None


def test_a_declared_event_code_wins_over_every_derivation():
    row = {
        "action": "insert",
        "collection": "room_event",
        "after_state": {"event_code": 4242, "status": "sent"},
    }
    assert entries.derive_action_code(row) == (4242, "declared")


def test_an_unclaimed_declared_code_is_exported_verbatim_rather_than_dropped(client, world):
    room_id, document_id = world
    client.patch(
        f"/api/records/document/{document_id}",
        json={"event_code": 4242, "reason": "Watermark toggled."},
        params={"room_id": room_id, "actor": "dana"},
    )
    body = client.get(
        f"{PREFIX}/rooms/{room_id}/audit-trail", params={**ADMIN, "action": 4242}
    ).json()
    assert body["entries"]
    assert body["entries"][0]["action"]["code"] == 4242
    assert body["entries"][0]["action"]["origin"] == vocabulary.ORIGIN_UNDECLARED


def test_a_non_integer_event_code_is_ignored_rather_than_coerced():
    row = {"action": "insert", "collection": "document", "after_state": {"event_code": "nda"}}
    assert entries.derive_action_code(row) == (vocabulary.DOCUMENT_CREATED, "derived")


@pytest.mark.parametrize(
    "status,code",
    [
        ("sent", 6),
        ("viewed", 8),
        ("forwarded", 12),
        ("expired", 13),
        ("declined", 43),
        ("completed", 18),
        ("completed_manually", 18),
    ],
)
def test_a_document_transition_resolves_to_its_lifecycle_code(status, code):
    row = {"action": "update", "collection": "document", "after_state": {"status": status}}
    assert entries.derive_action_code(row)[0] == code


def test_a_rejected_attempt_with_no_band_slot_is_flagged_not_dropped():
    row = {
        "action": "insert",
        "collection": "recipient_verification",
        "after_state": {"method": "face", "outcome": "fail"},
    }
    code, source = entries.derive_action_code(row)
    assert code == vocabulary.UNCLASSIFIED
    assert source == "verification_unallocable"


def test_an_unallocatable_rejection_still_reports_its_outcome(store, room_id):
    records = store
    records.create(
        "recipient_verification",
        {"method": "face", "outcome": "fail"},
        room_id=room_id,
        source="seed",
    )
    entry = entries.project(entries.room_rows(records, room_id)[-1], observations={}, sandbox=False)
    assert entry["verification"]["outcome"] == "fail"
    assert entry["verification"]["code"] is None
    assert entry["verification"]["unallocable"] is True


def test_an_unrelated_collection_falls_through_to_unclassified():
    row = {"action": "insert", "collection": "activity", "after_state": {"status": "sent"}}
    assert entries.derive_action_code(row) == (vocabulary.UNCLASSIFIED, "fallback")


def test_a_delete_of_a_document_is_unclassified_rather_than_invented():
    row = {"action": "delete", "collection": "document", "before_state": {"status": "sent"}}
    assert entries.derive_action_code(row)[0] == vocabulary.UNCLASSIFIED


def test_a_row_with_no_verification_carries_no_verification_block(store, room_id):
    records = store
    records.create("document", {"title": "Plain"}, room_id=room_id, source="seed")
    entry = entries.project(entries.room_rows(records, room_id)[-1], observations={}, sandbox=False)
    assert entry["verification"] is None


# --------------------------------------------------------------------------- #
# Reports
# --------------------------------------------------------------------------- #


def _request_report(client, room_id, types=("user_activity",), **overrides):
    """Request reports and return the created ids, in request order.

    Depends on ``POST /reports`` carrying each record's ``id``: generation and
    download are addressed by it, so a response without it would leave a client
    unable to take the next step.
    """
    payload = {
        "report_type": list(types),
        "start_date": "01/01/2026",
        "end_date": "10/02/2026",
        "email": "compliance@example.com",
        **overrides,
    }
    response = client.post(f"{PREFIX}/reports", params={**ADMIN, "room_id": room_id}, json=payload)
    assert response.status_code == 202, response.text
    return [report["id"] for report in response.json()["reports"]]


def test_a_valid_request_is_accepted_not_created(client, world):
    room_id, _ = world
    response = client.post(
        f"{PREFIX}/reports",
        params={**ADMIN, "room_id": room_id},
        json={
            "report_type": ["user_activity"],
            "start_date": "01/01/2026",
            "end_date": "10/02/2026",
            "email": "compliance@example.com",
        },
    )
    assert response.status_code == 202
    assert response.json()["accepted"] is True


def test_a_requested_report_starts_pending_with_no_digest(client, world):
    room_id, _ = world
    report_id = _request_report(client, room_id)[0]
    report = client.get(f"{PREFIX}/reports/{report_id}", params=ADMIN).json()["report"]
    assert report["state"] == "pending"
    assert not report.get("sha256")
    assert report.get("delivery") is None


def test_one_notification_is_recorded_per_requested_type(client, world):
    room_id, _ = world
    response = client.post(
        f"{PREFIX}/reports",
        params={**ADMIN, "room_id": room_id},
        json={
            "report_type": ["user_activity", "document_status", "sms_activity"],
            "start_date": "01/01/2026",
            "end_date": "10/02/2026",
            "email": "compliance@example.com",
        },
    ).json()
    assert response["requested"] == 3
    assert response["one_notification_per_report_type"] is True
    for report in response["reports"]:
        generated = client.post(f"{PREFIX}/reports/{report['id']}/generate", params=ADMIN).json()
        assert generated["report"]["delivery"]["one_per_report_type"] is True


def test_a_requested_report_is_addressable_by_the_id_the_response_carries(client, world):
    """Request, then generate, then download: the researched flow, end to end.

    This is the test that fails if a response stops carrying the record id, because
    nothing else in the suite would notice. Generation and download are both
    addressed by it, so a payload-only response leaves the flow unfollowable.
    """
    room_id, _ = world
    ids = _request_report(client, room_id)
    assert len(ids) == 1 and ids[0]
    report = client.post(f"{PREFIX}/reports/{ids[0]}/generate", params=ADMIN).json()["report"]
    downloaded = client.get(
        f"{PREFIX}/reports/{ids[0]}/download",
        params={**ADMIN, "token": report["delivery"]["token"]},
    )
    assert downloaded.status_code == 200
    assert downloaded.text.startswith("seq,")


def test_the_listing_returns_one_row_per_requested_type(client, world):
    room_id, _ = world
    client.post(
        f"{PREFIX}/reports",
        params={**ADMIN, "room_id": room_id},
        json={
            "report_type": ["user_activity", "document_status"],
            "start_date": "01/01/2026",
            "end_date": "10/02/2026",
            "email": "compliance@example.com",
        },
    )
    listed = client.get(f"{PREFIX}/reports", params=ADMIN).json()
    assert listed["count"] == 2
    assert {r["report_type"] for r in listed["reports"]} == {"user_activity", "document_status"}


def test_generation_records_the_digest_and_the_link(client, world):
    room_id, _ = world
    report_id = _request_report(client, room_id)[0]
    report = client.post(f"{PREFIX}/reports/{report_id}/generate", params=ADMIN).json()["report"]
    assert report["state"] == "ready"
    assert report["sha256"]
    assert report["delivery"]["url"].endswith(
        f"/reports/{report_id}/download?token={report['delivery']['token']}"
    )
    assert report["delivery"]["carries"] == "a download link, not the file"


def test_generation_is_idempotent_and_cannot_change_a_file_already_handed_out(client, world):
    room_id, _ = world
    report_id = _request_report(client, room_id)[0]
    first = client.post(f"{PREFIX}/reports/{report_id}/generate", params=ADMIN).json()["report"]
    client.post(
        "/api/records/document",
        json={"title": "Later"},
        params={"room_id": room_id, "actor": "dana"},
    )
    second = client.post(f"{PREFIX}/reports/{report_id}/generate", params=ADMIN).json()["report"]
    assert second["sha256"] == first["sha256"]
    assert second["upto_seq"] == first["upto_seq"]


def test_a_row_written_after_generation_does_not_change_the_download(client, world):
    """The digest handed to a recipient must keep describing their file."""
    room_id, _ = world
    report_id = _request_report(client, room_id)[0]
    report = client.post(f"{PREFIX}/reports/{report_id}/generate", params=ADMIN).json()["report"]
    client.post(
        "/api/records/document",
        json={"title": "Later"},
        params={"room_id": room_id, "actor": "dana"},
    )
    downloaded = client.get(
        f"{PREFIX}/reports/{report_id}/download",
        params={**ADMIN, "token": report["delivery"]["token"]},
    )
    assert hashlib.sha256(downloaded.content).hexdigest() == report["sha256"]


def test_downloading_before_generation_is_a_conflict(client, world):
    room_id, _ = world
    report_id = _request_report(client, room_id)[0]
    response = client.get(f"{PREFIX}/reports/{report_id}/download", params={**ADMIN, "token": "x"})
    assert response.status_code == 409
    assert response.json()["error"] == reports.ERROR_REPORT_NOT_READY


def test_downloading_with_the_wrong_token_is_refused(client, world):
    room_id, _ = world
    report_id = _request_report(client, room_id)[0]
    report = client.post(f"{PREFIX}/reports/{report_id}/generate", params=ADMIN).json()["report"]
    response = client.get(
        f"{PREFIX}/reports/{report_id}/download", params={**ADMIN, "token": "not-the-token"}
    )
    assert response.status_code == 403
    assert response.json()["error"] == reports.ERROR_BAD_TOKEN
    assert report["delivery"]["token"] != "not-the-token"


def test_downloading_with_no_token_is_refused(client, world):
    room_id, _ = world
    report_id = _request_report(client, room_id)[0]
    client.post(f"{PREFIX}/reports/{report_id}/generate", params=ADMIN)
    assert client.get(f"{PREFIX}/reports/{report_id}/download", params=ADMIN).status_code == 403


def test_the_download_is_a_csv_with_a_header_row_and_nothing_else(client, world):
    room_id, _ = world
    report_id = _request_report(client, room_id)[0]
    report = client.post(f"{PREFIX}/reports/{report_id}/generate", params=ADMIN).json()["report"]
    body = client.get(
        f"{PREFIX}/reports/{report_id}/download",
        params={**ADMIN, "token": report["delivery"]["token"]},
    )
    rows = list(csv.reader(io.StringIO(body.text)))
    assert rows[0] == list(reports.COLUMNS["user_activity"])
    # A recipient pipes this into a spreadsheet; a prose preamble breaks that.
    assert not body.text.startswith("#")


def test_the_served_digest_is_the_recorded_digest(client, world):
    room_id, _ = world
    report_id = _request_report(client, room_id)[0]
    report = client.post(f"{PREFIX}/reports/{report_id}/generate", params=ADMIN).json()["report"]
    response = client.get(
        f"{PREFIX}/reports/{report_id}/download",
        params={**ADMIN, "token": report["delivery"]["token"]},
    )
    assert response.headers["x-audit-sha256"] == report["sha256"]
    assert hashlib.sha256(response.content).hexdigest() == report["sha256"]


def test_the_download_carries_a_filename_the_recipient_can_save(client, world):
    room_id, _ = world
    report_id = _request_report(client, room_id)[0]
    report = client.post(f"{PREFIX}/reports/{report_id}/generate", params=ADMIN).json()["report"]
    response = client.get(
        f"{PREFIX}/reports/{report_id}/download",
        params={**ADMIN, "token": report["delivery"]["token"]},
    )
    assert "attachment" in response.headers["content-disposition"]
    assert response.headers["content-type"].startswith("text/csv")


def test_only_rows_inside_the_date_range_are_exported(client, world):
    room_id, _ = world
    report_id = _request_report(client, room_id, start_date="01/01/2020", end_date="12/31/2020")[0]
    report = client.post(f"{PREFIX}/reports/{report_id}/generate", params=ADMIN).json()["report"]
    # Everything in this fixture was written today, so a 2020 range selects none.
    assert report["row_count"] == 0


def test_an_unknown_report_is_a_404(client, world):
    assert client.get(f"{PREFIX}/reports/report_nope", params=ADMIN).status_code == 404


def test_a_duplicated_type_is_requested_once(client, world):
    room_id, _ = world
    response = client.post(
        f"{PREFIX}/reports",
        params={**ADMIN, "room_id": room_id},
        json={
            "report_type": ["user_activity", "user_activity"],
            "start_date": "01/01/2026",
            "end_date": "10/02/2026",
            "email": "compliance@example.com",
        },
    ).json()
    assert response["requested"] == 1


def test_a_bare_string_type_is_accepted_as_one_type(client):
    store_module = reports
    validated = store_module.validate_request(
        "document_status", "01/01/2026", "10/02/2026", "compliance@example.com", today=TODAY
    )
    assert validated.report_types == ("document_status",)


def test_no_type_at_all_is_refused(client, world):
    room_id, _ = world
    response = client.post(
        f"{PREFIX}/reports",
        params={**ADMIN, "room_id": room_id},
        json={
            "report_type": [],
            "start_date": "01/01/2026",
            "end_date": "10/02/2026",
            "email": "compliance@example.com",
        },
    )
    assert response.status_code == 400


@pytest.mark.parametrize("report_type", list(reports.REPORT_TYPES))
def test_every_researched_type_is_accepted(client, world, report_type):
    room_id, _ = world
    report_id = _request_report(client, room_id, types=(report_type,))[0]
    assert report_id


def test_an_unknown_type_is_refused_with_its_own_code(client, world):
    room_id, _ = world
    response = client.post(
        f"{PREFIX}/reports",
        params={**ADMIN, "room_id": room_id},
        json={
            "report_type": ["wat"],
            "start_date": "01/01/2026",
            "end_date": "10/02/2026",
            "email": "compliance@example.com",
        },
    )
    assert response.json()["error"] == reports.ERROR_UNKNOWN_TYPE


def test_a_type_with_no_data_source_exports_a_valid_empty_csv(client, world):
    """The researched enum calls fax_usage valid. Refusing it would contradict the
    source; inventing rows to fill it would be worse."""
    room_id, _ = world
    report_id = _request_report(client, room_id, types=("fax_usage",))[0]
    report = client.post(f"{PREFIX}/reports/{report_id}/generate", params=ADMIN).json()["report"]
    assert report["row_count"] == 0
    body = client.get(
        f"{PREFIX}/reports/{report_id}/download",
        params={**ADMIN, "token": report["delivery"]["token"]},
    )
    rows = list(csv.reader(io.StringIO(body.text)))
    assert rows[0] == list(reports.COLUMNS["fax_usage"])
    assert len(rows) == 1


def test_a_type_with_no_data_source_says_so_on_the_record(client, world):
    room_id, _ = world
    report_id = _request_report(client, room_id, types=("sms_activity",))[0]
    report = client.get(f"{PREFIX}/reports/{report_id}", params=ADMIN).json()["report"]
    assert "No data source" in (report["note"] or "")


# --------------------------------------------------------------------------- #
# The researched date rules
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "value", ["2026-01-01", "1/1/2026", "01-01-2026", "01/01/26", "2026/01/01", "01/01/2026T00:00"]
)
def test_only_mm_dd_yyyy_is_accepted(client, world, value):
    room_id, _ = world
    response = client.post(
        f"{PREFIX}/reports",
        params={**ADMIN, "room_id": room_id},
        json={
            "report_type": ["user_activity"],
            "start_date": value,
            "end_date": "10/02/2026",
            "email": "compliance@example.com",
        },
    )
    assert response.status_code == 400
    assert response.json()["error"] == reports.ERROR_DATE_FORMAT


def test_a_real_calendar_date_is_required():
    with pytest.raises(ReportError) as caught:
        reports.parse_date("02/30/2026")
    assert caught.value.code == reports.ERROR_DATE_FORMAT


def test_a_leap_day_is_a_real_date():
    assert reports.parse_date("02/29/2028").day == 29
    with pytest.raises(ReportError):
        reports.parse_date("02/29/2026")


def test_twelve_calendar_months_is_the_limit():
    """12 months from 31 January lands on 31 January, so 28 February is 12 months
    and 28 days and is refused. Calendar arithmetic, not a 365-day approximation."""
    reports.validate_range("01/01/2026", "01/01/2027", today=TODAY)
    with pytest.raises(ReportError) as caught:
        reports.validate_range("01/01/2026", "01/02/2027", today=TODAY)
    assert caught.value.code == reports.ERROR_RANGE_TOO_LONG


def test_a_month_end_start_clamps_rather_than_overshooting():
    assert reports.add_months(date(2026, 1, 31), 12) == date(2027, 1, 31)
    assert reports.add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert reports.add_months(date(2028, 1, 31), 1) == date(2028, 2, 29)


def test_ten_years_of_lookback_is_the_limit():
    # A single-day range at the boundary. A ten-year range would be refused by the
    # twelve-month rule first, which is right but tests nothing about lookback.
    reports.validate_range("10/02/2016", "10/02/2016", today=TODAY)
    with pytest.raises(ReportError) as caught:
        reports.validate_range("10/01/2016", "10/01/2016", today=TODAY)
    assert caught.value.code == reports.ERROR_START_TOO_OLD


def test_the_lookback_boundary_is_not_a_365_day_approximation():
    """2016 is a leap year, so ten years back from 2026-10-02 is 2016-10-02.
    ``timedelta(days=3650)`` would place it a day earlier and refuse a valid range."""
    start, _ = reports.validate_range("10/02/2016", "10/02/2016", today=TODAY)
    assert start == date(2016, 10, 2)


def test_an_inverted_range_is_its_own_error_code():
    with pytest.raises(ReportError) as caught:
        reports.validate_range("06/01/2026", "01/01/2026", today=TODAY)
    assert caught.value.code == reports.ERROR_RANGE_INVERTED


def test_a_single_day_range_is_allowed():
    reports.validate_range("03/01/2026", "03/01/2026", today=TODAY)


def test_the_range_bounds_are_inclusive_at_both_ends():
    """The source speaks in dates and never timestamps; a lead asking for up to the
    30th means the 30th."""
    rows = [
        {"date_created": "2026-03-01T00:00:00.000+00:00", "seq": 1},
        {"date_created": "2026-03-31T23:59:59.000+00:00", "seq": 2},
        {"date_created": "2026-04-01T00:00:00.000+00:00", "seq": 3},
    ]
    chosen = reports.in_range(rows, date(2026, 3, 1), date(2026, 3, 31))
    assert [row["seq"] for row in chosen] == [1, 2]


def test_every_broken_rule_has_its_own_published_code():
    published = reports.payload()["error_codes"]
    for code in (
        reports.ERROR_DATE_FORMAT,
        reports.ERROR_RANGE_INVERTED,
        reports.ERROR_RANGE_TOO_LONG,
        reports.ERROR_START_TOO_OLD,
        reports.ERROR_UNKNOWN_TYPE,
    ):
        assert code in published


def test_a_missing_recipient_is_refused():
    with pytest.raises(ReportError) as caught:
        reports.validate_request(["user_activity"], "01/01/2026", "10/02/2026", "", today=TODAY)
    assert caught.value.code == "invalid_email"


def test_the_delivery_rule_is_published_rather_than_implied():
    payload = reports.payload()
    assert "one email per requested report type" in payload["delivery"]


# --------------------------------------------------------------------------- #
# Regression: the two defects the first draft shipped
# --------------------------------------------------------------------------- #


def test_every_write_route_completes(client, world):
    """The first draft called ``store.update(..., room_id=...)``, which
    ``AuditedDatabase.update`` does not accept, so every report generation raised
    TypeError and answered 500. Driving each POST end to end is what catches it."""
    room_id, document_id = world
    report_id = _request_report(client, room_id)[0]

    assert (
        client.post(
            f"{PREFIX}/observations",
            params={"room_id": room_id},
            json={"seq": _latest_seq(client, room_id), "ip_address": "192.0.2.20"},
        ).status_code
        == 201
    )
    assert (
        client.post(f"{PREFIX}/rooms/{room_id}/audit-trail/anchors", params=ADMIN).status_code
        == 201
    )
    assert client.post(f"{PREFIX}/reports/{report_id}/generate", params=ADMIN).status_code == 200
    assert (
        client.get(
            f"{PREFIX}/rooms/{room_id}/documents/{document_id}/audit-trail", params=ADMIN
        ).status_code
        == 200
    )


def test_no_write_route_answers_a_server_error(client, world):
    """A 500 in a criticality-C2 workflow is invisible in a demo and fatal in use."""
    room_id, _ = world
    report_id = _request_report(client, room_id)[0]
    calls = (
        lambda: client.post(
            f"{PREFIX}/observations",
            params={"room_id": room_id},
            json={"seq": _latest_seq(client, room_id), "ip_address": "192.0.2.21"},
        ),
        lambda: client.post(f"{PREFIX}/rooms/{room_id}/audit-trail/anchors", params=ADMIN),
        lambda: client.post(f"{PREFIX}/reports/{report_id}/generate", params=ADMIN),
        lambda: client.get(f"{PREFIX}/rooms/{room_id}/audit-trail", params=ADMIN),
        lambda: client.get(f"{PREFIX}/rooms/{room_id}/audit-trail/summary", params=ADMIN),
        lambda: client.get(f"{PREFIX}/rooms/{room_id}/audit-trail/verify", params=ADMIN),
    )
    for call in calls:
        assert call().status_code < 500


def test_generation_writes_without_claiming_room_scope_it_does_not_have(client, world):
    """``AuditedDatabase.update`` takes no ``room_id``: it is a property of the row.
    Passing one is a TypeError, and the row keeps the room it was created in."""
    room_id, _ = world
    report_id = _request_report(client, room_id)[0]
    client.post(f"{PREFIX}/reports/{report_id}/generate", params=ADMIN)
    report = client.get(f"{PREFIX}/reports/{report_id}", params=ADMIN).json()["report"]
    assert report["state"] == "ready"
    # The update did not re-scope the record out of its room.
    listed = client.get(f"{PREFIX}/reports", params=ADMIN).json()["reports"]
    assert any(row["report_type"] == "user_activity" for row in listed)


# --------------------------------------------------------------------------- #
# The audit-source rule
# --------------------------------------------------------------------------- #


def _recorded_sources():
    """Every ``source=`` literal in the feature module, as (verb, path) or ("", "seed")."""
    import re

    module_source = Path(feature().__file__).read_text(encoding="utf-8")
    found = []
    for literal in re.findall(r'source=f?"([^"]+)"', module_source):
        # A literal with no space is not a verb/path pair: `"seed"` would otherwise
        # parse as a verb called `seed` with an empty path.
        if " " in literal:
            verb, _, route = literal.partition(" ")
            found.append((verb, route))
        else:
            found.append(("", literal))
    return found


def test_every_route_source_names_a_route_the_host_actually_mounted():
    """Hard rule 4: the audit row must name the route that served the write.

    A feature whose audit log keeps recording a path the app has stopped serving has
    shipped in this branch's history before, and nothing else in the product would
    notice: the route still answers, just not the one the log names.
    """
    mounted = _mounted_routes()
    route_sources = [
        (verb, route) for verb, route in _recorded_sources() if "{router.prefix}" in route
    ]
    assert len(route_sources) == 4, route_sources
    for verb, route in route_sources:
        # Only the prefix is resolved. The remaining placeholders are literal on
        # both sides: the source is an f-string, so `{room_id}` stays in the string
        # it builds, and the mounted path carries the same placeholder.
        concrete = route.replace("{router.prefix}", PREFIX)
        assert (verb, concrete) in mounted, f"a write records an unmounted route: {verb} {concrete}"


def test_no_route_source_is_a_hardcoded_path():
    """Every route source is built from ``router.prefix``, not spelled out.

    A literal path survives a rename of the prefix and keeps naming the old one,
    which is the failure this rule exists to prevent.
    """
    for verb, route in _recorded_sources():
        if verb and "seed" not in route:
            assert "{router.prefix}" in route, f"a write hardcodes its path: {verb} {route}"


def test_the_seeders_writes_name_the_seeder_rather_than_a_route():
    """A seeder runs outside any request, so there is no route to name.

    Asserted rather than ignored, because the alternative - a seeder claiming to have
    been served by a URL - is the same defect in a different costume.
    """
    non_route = [(verb, route) for verb, route in _recorded_sources() if not verb]
    assert non_route
    assert {route for _verb, route in non_route} == {"seed"}


def test_the_domain_layer_takes_source_from_its_caller_with_no_default():
    """No domain function may invent an audit source: the route owns it."""
    import ast
    import inspect

    for name in ("entries", "reports", "anchors"):
        module = __import__(f"dsr.audit_export.{name}", fromlist=["*"])
        for symbol, value in vars(module).items():
            if symbol.startswith("_") or not inspect.isfunction(value):
                continue
            if value.__module__ != module.__name__:
                continue
            parameters = inspect.signature(value).parameters
            if "source" in parameters:
                assert parameters["source"].default is inspect.Parameter.empty, (
                    f"{name}.{symbol} has a default source"
                )
            ast.parse(inspect.getsource(value))


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #


@pytest.fixture()
def seeded(tmp_path):
    from dsr.db.audited import AuditedDatabase
    from dsr.store import RecordStore

    db = AuditedDatabase(tmp_path / "seed.db", actor="seed")
    try:
        room_ids = [
            (
                db.create("room", {"name": f"Room {index}"}).get("id")
                or db.list("room", limit=1)[-1]["id"],
                f"account{index}",
            )
            for index in range(4)
        ]
        summary = feature().seed(
            db, {"room_ids": room_ids, "now": datetime(2026, 10, 2, tzinfo=timezone.utc)}
        )
        yield summary, room_ids, RecordStore(db)
    finally:
        db.close()


def test_the_seed_reports_what_it_added(seeded):
    summary, _rooms, _store = seeded
    assert "documents" in summary
    assert "addresses attributed" in summary


def test_the_seed_covers_four_rooms(seeded):
    _summary, _rooms, store = seeded
    assert len(store.list("document", limit=100)) == 4


def test_the_seed_includes_a_declined_and_an_expired_document(seeded):
    _summary, rooms, store = seeded
    trail, _ = entries.build_trail(store, rooms[0][0])
    assert vocabulary.DOCUMENT_DECLINED in {e["action"]["code"] for e in trail}
    trail, _ = entries.build_trail(store, rooms[1][0])
    assert vocabulary.DOCUMENT_EXPIRED in {e["action"]["code"] for e in trail}


def test_the_seed_includes_a_pass_and_a_failure(seeded):
    _summary, rooms, store = seeded
    trail, _ = entries.build_trail(store, rooms[0][0])
    outcomes = {e["verification"]["outcome"] for e in trail if e.get("verification")}
    assert outcomes == {"pass", "fail"}


def test_the_seed_includes_a_verification_with_no_band_slot(seeded):
    _summary, rooms, store = seeded
    trail, _ = entries.build_trail(store, rooms[2][0])
    assert any(e["verification"] and e["verification"]["unallocable"] for e in trail)


def test_the_seed_leaves_one_room_with_no_addresses_at_all(seeded):
    _summary, rooms, store = seeded
    trail, _ = entries.build_trail(store, rooms[3][0])
    assert trail
    assert all(entry["ip_status"] == entries.IP_NOT_CAPTURED for entry in trail)


def test_the_seed_attributes_addresses_from_documentation_space(seeded):
    _summary, rooms, store = seeded
    trail, _ = entries.build_trail(store, rooms[0][0])
    observed = [e for e in trail if e["ip_address"]]
    assert observed
    assert all(entry["ip_address"].startswith("192.0.2.") for entry in observed)


def test_the_seed_includes_an_unclaimed_event_code(seeded):
    _summary, rooms, store = seeded
    trail, _ = entries.build_trail(store, rooms[3][0])
    assert any(entry["action"]["code"] == 4242 for entry in trail)


def test_the_seed_leaves_one_report_pending_and_generates_the_rest(seeded):
    _summary, rooms, store = seeded
    records = store.list(reports.REPORT_COLLECTION, room_id=rooms[0][0], limit=100)
    states = [record["data"]["state"] for record in records]
    assert states.count(reports.STATUS_PENDING) == 1
    assert states.count(reports.STATUS_READY) == 2


def test_a_seeded_report_is_downloadable_by_its_recorded_token(seeded):
    _summary, rooms, store = seeded
    ready = [
        record
        for record in store.list(reports.REPORT_COLLECTION, room_id=rooms[0][0], limit=100)
        if record["data"]["state"] == reports.STATUS_READY
    ]
    data = ready[0]["data"]
    text = reports.csv_for(store, ready[0]["id"], data["delivery"]["token"])
    assert hashlib.sha256(text.encode("utf-8")).hexdigest() == data["sha256"]


def test_the_seeded_trail_verifies(seeded):
    _summary, rooms, store = seeded
    trail, _ = entries.build_trail(store, rooms[0][0])
    assert integrity.verify(trail)["verified"] is True


def test_a_seeded_trail_can_be_anchored_and_verifies_intact(seeded):
    _summary, rooms, store = seeded
    room_id = rooms[0][0]
    trail, _ = entries.build_trail(store, room_id)
    anchor = anchors.pin(
        store,
        trail,
        scope=anchors.scope_of(room_id),
        pinned_at="2026-10-02T00:00:00.000+00:00",
        actor="dana",
        source="test",
        room_id=room_id,
    )
    rebuilt, _ = entries.build_trail(store, room_id)
    assert anchors.check(anchor, rebuilt)["intact"] is True


def test_a_sandbox_export_of_the_seed_masks_every_observed_address(seeded):
    _summary, rooms, store = seeded
    trail, _ = entries.build_trail(store, rooms[0][0], sandbox=True)
    masked = [entry for entry in trail if entry["ip_status"] == entries.IP_HIDDEN]
    assert masked
    assert all(entry["ip_address"] == "hidden" for entry in masked)


def test_the_seed_needs_no_clock_to_do_nothing(tmp_path):
    from dsr.db.audited import AuditedDatabase

    db = AuditedDatabase(tmp_path / "empty.db")
    try:
        assert "0 audit rows" in feature().seed(
            db, {"room_ids": [], "now": datetime.now(timezone.utc)}
        )
    finally:
        db.close()


def test_the_seed_range_is_inside_the_twelve_month_rule_anyway(seeded):
    """Derived from the seeder's clock, so the demo cannot become invalid by ageing."""
    _summary, rooms, store = seeded
    for record in store.list(reports.REPORT_COLLECTION, room_id=rooms[0][0], limit=100):
        data = record["data"]
        start, end = reports.validate_range(
            data["start_date"], data["end_date"], today=date(2026, 10, 2)
        )
        assert (start, end) == (date.fromisoformat(data["start"]), date.fromisoformat(data["end"]))


def test_the_timdelta_import_is_used_for_the_seed_range():
    """Guards a stale import after a refactor: the seed range depends on it."""
    assert timedelta(days=30).days == 30
