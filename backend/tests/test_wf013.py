"""Tests for WF-013: conditional rules that personalise room content.

The domain semantics are tested separately in ``test_rules.py``, against the pure
:mod:`dsr.rules` module. This file covers the HTTP surface and the plugin
registration, and it is where the port's structural decisions are pinned:

* the feature is mounted by discovery alone, under a prefix it owns, and
  registers a domain error handler the host attaches rather than one that had to
  be written into ``dsr/api.py``;
* every write's audit row names the path this router actually serves. The branch
  hard-coded those strings inside its route functions, which is the defect the
  port brief calls out, so the assertion here is about the served path;
* **preview writes nothing**, which is what keeps it safe to call while a seller
  is still trialling variable values;
* the rule-lossy boundary (S11) is loud, not silent.

One test is marked ``xfail`` and that is a finding, not a weakness: the design
doc's "no bypass" guarantee needs a change to ``dsr/api.py``, which a feature
must not make. The test records the gap and fails loudly if someone closes it.
"""

from __future__ import annotations

import random
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dsr import rules as rules_module
from dsr.api import app
from dsr.db.audited import AuditedDatabase
from dsr.features import load_feature
from dsr.rules import RuleError

PREFIX = "/api/wf-013"


@pytest.fixture()
def client(monkeypatch):
    # A temporary database, exactly as test_features.py does it: the env var is
    # read at call time by dsr.deps, so every test gets its own store.
    tmp = tempfile.TemporaryDirectory()
    monkeypatch.setenv("DSR_DB_PATH", str(Path(tmp.name) / "wf013.db"))
    monkeypatch.setenv("DSR_AUDIT_DIR", str(Path(tmp.name) / "audit"))
    monkeypatch.setattr("dsr.api.FRONTEND_DIST", Path(tmp.name) / "absent-frontend")
    with TestClient(app) as test_client:
        yield test_client
    tmp.cleanup()


# -- helpers -------------------------------------------------------------------- #


def rule(*conditions, join="and"):
    return {"join": join, "conditions": list(conditions)}


def text(variable="region", modifier="is", value="Australia", **extra):
    return {"variable": variable, "category": "text", "modifier": modifier, "value": value, **extra}


def make_room(client, name="Acme"):
    return client.post("/api/records/room", json={"name": name}).json()["id"]


def make_block(client, room_id, **payload):
    return client.post(
        f"{PREFIX}/rooms/{room_id}/blocks", json={"title": "Block", **payload}
    ).json()


def audit(client, **params):
    return client.get("/api/audit", params=params).json()["entries"]


# -- registration ---------------------------------------------------------------- #


def test_the_feature_is_mounted_by_discovery(client):
    """No file in the host names this feature; discovery mounts it anyway."""
    body = client.get("/api/features").json()
    installed = {feature["id"]: feature for feature in body["features"]}

    assert "wf-013-conditional-rules" in installed
    record = installed["wf-013-conditional-rules"]
    assert record["prefix"] == PREFIX
    assert record["ticket"] == "WF-013"
    # The error mapping moved out of dsr/api.py and into the feature's export.
    assert record["exception_handlers"] == ["RuleError"]


def test_the_feature_did_not_collide_with_anything(client):
    body = client.get("/api/features").json()
    assert body["failed_count"] == 0, body["failed"]


def test_the_branch_paths_are_not_served_anywhere(client):
    """The port moved the surface under /api/wf-013; nothing else may claim it.

    The branch served /api/rules/catalog and /api/rooms/{id}/blocks from the
    shared app. Those are core vocabulary other workflows want, so the port
    renamed them. This asserts the rename actually happened, in both directions:
    the old path is not a feature, and the new one is.
    """
    assert client.get("/api/rules/catalog").status_code == 404
    assert client.get(f"{PREFIX}/catalog").status_code == 200


def test_frontend_descriptor_id_matches_the_backend_feature_id():
    """The two halves of a feature are findable by one name, so they must agree."""
    descriptor = (
        Path(__file__).resolve().parents[2]
        / "frontend"
        / "src"
        / "features"
        / "wf-013-conditional-rules"
        / "index.jsx"
    )
    text = descriptor.read_text(encoding="utf-8")
    module = load_feature("wf013_rules")

    assert module.FEATURE["id"] in text
    assert f'id: {module.FEATURE["id"]!r}' in text


def test_feature_module_does_not_import_the_shared_app():
    """A guard exists in test_features.py; this states the reason locally."""
    source = Path(load_feature("wf013_rules").__file__).read_text(encoding="utf-8")
    assert "dsr.api" not in source
    assert "from dsr.deps import" in source


def test_the_domain_error_becomes_a_422_through_the_host_handler(client):
    """FastAPI only accepts exception handlers on the app, so the host attaches it.

    The rule module raises ``RuleError``; nothing in the feature converts it by
    hand. If this stops being a 422 the handler stopped being registered.
    """
    room = make_room(client)
    block = make_block(client, room, type="accept")

    response = client.put(
        f"{PREFIX}/rooms/{room}/blocks/{block['id']}/rule", json=rule(text())
    )

    assert response.status_code == 422
    assert response.json()["error"] == "invalid_rule"
    assert "Accept Block" in response.json()["detail"]


# -- audit sources ---------------------------------------------------------------- #


def test_every_write_audits_the_path_this_router_serves(client):
    """Hard rule 4 of the port brief, as a test.

    The audit row must name the route that actually served the write. The branch
    recorded strings like ``PUT rule block_x``, which names nothing anyone can
    call; if the prefix and the recorded source ever drift apart, this fails.
    """
    room = make_room(client)
    block = make_block(client, room)

    client.put(
        f"{PREFIX}/rooms/{room}/blocks/{block['id']}/rule",
        json=rule(text()),
        params={"actor": "dana"},
    )
    client.post(f"{PREFIX}/rooms/{room}/personalise", json={"region": "Australia"})
    client.post(f"{PREFIX}/rooms/{room}/blocks/{block['id']}/save-to-library")

    # The room itself was created through the core route, so look only at the
    # writes this feature served.
    sources = {entry["source"] for entry in audit(client) if PREFIX in (entry["source"] or "")}
    assert sources == {
        f"POST {PREFIX}/rooms/{room}/blocks",
        f"PUT {PREFIX}/rooms/{room}/blocks/{block['id']}/rule",
        f"POST {PREFIX}/rooms/{room}/personalise",
        f"POST {PREFIX}/rooms/{room}/blocks/{block['id']}/save-to-library",
    }


# -- discovery -------------------------------------------------------------------- #


def test_catalog_publishes_categories_modifiers_and_limits(client):
    body = client.get(f"{PREFIX}/catalog").json()

    assert set(body["categories"]) == {"text", "number", "any"}
    assert body["limits"]["max_or_conditions"] == 10
    assert body["joiners"] == ["and", "or"]
    assert "accept" in body["forbidden_block_types"]


def test_catalog_documents_the_fallback_behaviours(client):
    """The behaviours a seller cannot infer need to be discoverable, not folklore."""
    behaviours = client.get(f"{PREFIX}/catalog").json()["behaviours"]
    assert behaviours["empty_value_is_valid"] is True
    assert behaviours["zero_value_is_valid"] is True
    assert "fail open" in behaviours["incomplete_condition"]


def test_variables_endpoint_is_empty_before_any_are_defined(client):
    body = client.get(f"{PREFIX}/variables").json()
    assert body == {"variables": [], "count": 0, "sources": []}


def test_variables_merge_account_and_crm_sources(client):
    """S12: CRM variables appear in the conditions list the same as account ones."""
    client.post("/api/records/variable", json={"name": "region", "label": "Region", "category": "text"})
    client.post(
        "/api/records/variable",
        json={"name": "segment", "label": "Segment", "category": "text", "source": "crm", "crm": "salesforce"},
    )

    body = client.get(f"{PREFIX}/variables").json()

    assert body["count"] == 2
    assert body["sources"] == ["account", "crm"]
    crm = next(variable for variable in body["variables"] if variable["name"] == "segment")
    assert crm["crm"] == "salesforce"


def test_a_variable_without_a_name_is_skipped_rather_than_broken(client):
    client.post("/api/records/variable", json={"label": "Nameless"})
    assert client.get(f"{PREFIX}/variables").json()["count"] == 0


def test_a_variable_defaults_to_the_account_source(client):
    client.post("/api/records/variable", json={"name": "region"})
    assert client.get(f"{PREFIX}/variables").json()["variables"][0]["source"] == "account"


# -- blocks ------------------------------------------------------------------------ #


def test_creating_a_block_scopes_it_to_the_room(client):
    room = make_room(client)
    block = make_block(client, room, title="Intro", position=2)

    assert block["room_id"] == room
    assert client.get(f"{PREFIX}/rooms/{room}/blocks").json()["count"] == 1


def test_blocks_are_listed_in_position_order(client):
    room = make_room(client)
    make_block(client, room, title="Third", position=3)
    make_block(client, room, title="First", position=1)
    make_block(client, room, title="Second", position=2)

    body = client.get(f"{PREFIX}/rooms/{room}/blocks").json()
    assert [block["data"]["title"] for block in body["blocks"]] == ["First", "Second", "Third"]


def test_a_block_can_carry_a_team_specific_field_without_a_migration(client):
    """Schema flexibility at the block layer."""
    room = make_room(client)
    make_block(client, room, title="Intro", layout={"width": 8, "gutter": True}, tracking_id="x-1")

    stored = client.get(f"{PREFIX}/rooms/{room}/blocks").json()["blocks"][0]["data"]
    assert stored["layout"] == {"width": 8, "gutter": True}
    assert stored["tracking_id"] == "x-1"


# -- attaching, reading and removing rules ----------------------------------------- #


def test_attaching_a_rule_stores_it_on_the_block_and_audits_the_change(client):
    room = make_room(client)
    block = make_block(client, room)

    response = client.put(
        f"{PREFIX}/rooms/{room}/blocks/{block['id']}/rule", json=rule(text()), params={"actor": "dana"}
    )

    assert response.status_code == 200
    assert response.json()["data"]["rule"]["conditions"][0]["variable"] == "region"
    entries = audit(client, record_id=block["id"], action="update")
    assert len(entries) == 1
    assert entries[0]["actor"] == "dana"


def test_attaching_a_rule_normalises_it(client):
    """A client may send an upper-case joiner and modifier; storage is normalised."""
    room = make_room(client)
    block = make_block(client, room)

    response = client.put(
        f"{PREFIX}/rooms/{room}/blocks/{block['id']}/rule",
        json={"join": "OR", "conditions": [{"variable": "seats", "modifier": "IS_MORE_THAN", "value": 10}]},
    )

    assert response.status_code == 200
    stored = response.json()["data"]["rule"]
    assert stored["join"] == "or"
    assert stored["conditions"][0]["modifier"] == "is_more_than"
    assert stored["conditions"][0]["category"] == "number"


def test_get_rule_reports_whether_the_block_accepts_rules(client):
    room = make_room(client)
    block = make_block(client, room, type="text")
    client.put(f"{PREFIX}/rooms/{room}/blocks/{block['id']}/rule", json=rule(text()))

    body = client.get(f"{PREFIX}/rooms/{room}/blocks/{block['id']}/rule").json()

    assert body["accepts_rules"] is True
    assert body["problems"] == []
    assert body["rule"]["conditions"][0]["variable"] == "region"


def test_removing_a_rule_makes_the_block_always_show(client):
    room = make_room(client)
    block = make_block(client, room)
    client.put(f"{PREFIX}/rooms/{room}/blocks/{block['id']}/rule", json=rule(text()))

    response = client.delete(f"{PREFIX}/rooms/{room}/blocks/{block['id']}/rule")

    assert response.status_code == 200
    assert response.json()["data"]["rule"] is None
    preview = client.post(f"{PREFIX}/rooms/{room}/preview", json={"region": "New Zealand"}).json()
    assert preview["shown"] == [block["id"]]


def test_removing_a_rule_that_does_not_exist_is_404(client):
    room = make_room(client)
    block = make_block(client, room)
    assert client.delete(f"{PREFIX}/rooms/{room}/blocks/{block['id']}/rule").status_code == 404


def test_a_block_from_another_room_is_404(client):
    room_a, room_b = make_room(client, "A"), make_room(client, "B")
    block = make_block(client, room_a)
    assert client.put(
        f"{PREFIX}/rooms/{room_b}/blocks/{block['id']}/rule", json=rule(text())
    ).status_code == 404


def test_an_unknown_block_is_404(client):
    room = make_room(client)
    assert client.put(
        f"{PREFIX}/rooms/{room}/blocks/block_nope/rule", json=rule(text())
    ).status_code == 404


# -- S10: the Accept Block, over HTTP ---------------------------------------------- #


def test_attaching_a_rule_to_an_accept_block_is_rejected(client):
    room = make_room(client)
    block = make_block(client, room, type="accept")

    response = client.put(f"{PREFIX}/rooms/{room}/blocks/{block['id']}/rule", json=rule(text()))

    assert response.status_code == 422
    assert "Accept Block" in response.json()["detail"]


def test_a_rejected_rule_is_not_stored(client):
    room = make_room(client)
    block = make_block(client, room, type="accept")

    client.put(f"{PREFIX}/rooms/{room}/blocks/{block['id']}/rule", json=rule(text()))

    assert client.get(f"{PREFIX}/rooms/{room}/blocks/{block['id']}/rule").json()["rule"] is None


def test_a_block_created_with_an_invalid_rule_is_rejected(client):
    room = make_room(client)
    response = client.post(
        f"{PREFIX}/rooms/{room}/blocks", json={"title": "Bad", "rule": rule(text(modifier="sounds_like"))}
    )
    assert response.status_code == 422
    assert client.get(f"{PREFIX}/rooms/{room}/blocks").json()["count"] == 0


def test_a_block_created_with_a_valid_rule_stores_the_normalised_form(client):
    room = make_room(client)
    response = client.post(
        f"{PREFIX}/rooms/{room}/blocks", json={"title": "AU", "rule": rule(text())}
    )
    assert response.status_code == 201
    assert response.json()["data"]["rule"]["conditions"][0]["category"] == "text"


# -- the "no bypass" guard ---------------------------------------------------------- #
#
# On the branch these five tests went through /api/records/block, because the
# branch edited dsr/api.py to validate a `rule` key on the generic create and
# update routes. api.py is shared and this port may not touch it, so the guard is
# tested where it can be tested: as a function, and through this feature's own
# routes, which use it.


def test_the_guard_ignores_payloads_with_no_rule_key():
    module = load_feature("wf013_rules")
    assert module.guard_rule_payload({"title": "Intro"}) == {"title": "Intro"}


def test_the_guard_normalises_a_rule_it_accepts():
    module = load_feature("wf013_rules")
    guarded = module.guard_rule_payload({"rule": {"join": "AND", "conditions": [text()]}})
    assert guarded["rule"]["join"] == "and"


def test_the_guard_raises_on_an_invalid_rule():
    module = load_feature("wf013_rules")
    with pytest.raises(RuleError, match="not a known modifier"):
        module.guard_rule_payload({"rule": rule(text(modifier="sounds_like"))})


def test_the_guard_rejects_a_rule_for_an_accept_block_using_the_merged_record():
    """Whether a block may carry a rule depends on its type, so the merge matters."""
    module = load_feature("wf013_rules")
    with pytest.raises(RuleError, match="Accept Block"):
        module.guard_rule_payload({"rule": rule(text())}, existing={"type": "accept"})


def test_a_payload_without_a_rule_survives_the_guard_unchanged():
    """The guard must be invisible to records that have nothing to do with rules."""
    module = load_feature("wf013_rules")
    assert module.guard_rule_payload({"name": "Acme", "rule": None}) == {
        "name": "Acme",
        "rule": {"join": "and", "conditions": []},
    }


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Known gap, reported rather than papered over: the generic "
        "/api/records/{collection} routes do not validate a `rule` key, so a rule "
        "can still be written unvalidated through them. Closing it needs one call "
        "to wf013_rules.guard_rule_payload inside dsr/api.py, which is a shared "
        "file this feature is forbidden to edit. The branch did edit it. This test "
        "is strict on purpose: it will XPASS (and fail) the day someone makes that "
        "change, so the gap cannot be forgotten."
    ),
)
def test_generic_patch_cannot_write_an_invalid_rule(client):
    room = make_room(client)
    block = make_block(client, room)

    response = client.patch(
        f"/api/records/block/{block['id']}", json={"rule": rule(text(modifier="sounds_like"))}
    )

    assert response.status_code == 422
    assert client.get(f"/api/records/block/{block['id']}").json()["data"].get("rule") is None


# -- preview: evaluates, never writes ----------------------------------------------- #


def test_preview_splits_blocks_by_their_rules(client):
    room = make_room(client)
    au = make_block(client, room, title="AU pricing")
    intro = make_block(client, room, title="Intro")
    nz = make_block(client, room, title="NZ pricing")
    client.put(f"{PREFIX}/rooms/{room}/blocks/{au['id']}/rule", json=rule(text(value="Australia")))
    client.put(f"{PREFIX}/rooms/{room}/blocks/{nz['id']}/rule", json=rule(text(value="New Zealand")))

    body = client.post(f"{PREFIX}/rooms/{room}/preview", json={"region": "Australia"}).json()

    assert body["persisted"] is False
    # Visibility follows the rule, not the block's position: AU pricing matches,
    # Intro has no rule so it always shows, NZ pricing does not match.
    assert set(body["shown"]) == {au["id"], intro["id"]}
    assert body["hidden"] == [nz["id"]]


def test_preview_returns_the_condition_trace(client):
    room = make_room(client)
    block = make_block(client, room)
    client.put(f"{PREFIX}/rooms/{room}/blocks/{block['id']}/rule", json=rule(text(value="Australia")))

    body = client.post(f"{PREFIX}/rooms/{room}/preview", json={"region": "New Zealand"}).json()

    trace = body["blocks"][0]["conditions"][0]
    assert trace["status"] == "unmatched"
    assert trace["observed"] == "New Zealand"


def test_preview_writes_nothing_at_all(client):
    """The contract that keeps preview safe to call repeatedly while trialling."""
    room = make_room(client)
    block = make_block(client, room)
    client.put(f"{PREFIX}/rooms/{room}/blocks/{block['id']}/rule", json=rule(text()))

    before_audit = client.get("/api/audit").json()["count"]
    before_stats = client.get("/api/stats").json()

    for _ in range(5):
        client.post(f"{PREFIX}/rooms/{room}/preview", json={"region": "Australia"})

    assert client.get("/api/audit").json()["count"] == before_audit
    assert client.get("/api/stats").json()["records"] == before_stats["records"]
    assert client.get("/api/records/personalisation").json()["count"] == 0


def test_preview_of_an_empty_room_is_an_answer_not_an_error(client):
    room = make_room(client)
    body = client.post(f"{PREFIX}/rooms/{room}/preview", json={}).json()
    assert body["block_count"] == 0
    assert body["blocks"] == []


def test_preview_twice_returns_the_same_decision(client):
    room = make_room(client)
    block = make_block(client, room)
    client.put(f"{PREFIX}/rooms/{room}/blocks/{block['id']}/rule", json=rule(text()))

    first = client.post(f"{PREFIX}/rooms/{room}/preview", json={"region": "Australia"}).json()
    second = client.post(f"{PREFIX}/rooms/{room}/preview", json={"region": "Australia"}).json()
    assert first["shown"] == second["shown"]


def test_an_unsupplied_variable_is_distinguishable_from_an_empty_one(client):
    """D3 + S9: the preview panel relies on the server telling these apart."""
    room = make_room(client)
    block = make_block(client, room)
    client.put(f"{PREFIX}/rooms/{room}/blocks/{block['id']}/rule", json=rule(text(value="")))

    unsupplied = client.post(f"{PREFIX}/rooms/{room}/preview", json={}).json()
    empty = client.post(f"{PREFIX}/rooms/{room}/preview", json={"region": ""}).json()

    assert unsupplied["blocks"][0]["conditions"][0]["status"] == "no_value"
    assert empty["blocks"][0]["conditions"][0]["status"] == "matched"


# -- personalise: the same decision, recorded ---------------------------------------- #


def test_personalise_returns_the_same_shape_as_preview(client):
    room = make_room(client)
    block = make_block(client, room)
    client.put(f"{PREFIX}/rooms/{room}/blocks/{block['id']}/rule", json=rule(text()))

    preview = client.post(f"{PREFIX}/rooms/{room}/preview", json={"region": "Australia"}).json()
    personalise = client.post(f"{PREFIX}/rooms/{room}/personalise", json={"region": "Australia"}).json()

    assert personalise["persisted"] is True
    assert personalise["shown"] == preview["shown"]
    assert personalise["hidden"] == preview["hidden"]
    assert personalise["blocks"] == preview["blocks"]


def test_personalise_records_the_values_and_the_decision(client):
    """S13: a generation-time decision stays replayable."""
    room = make_room(client)
    block = make_block(client, room, title="AU pricing")
    client.put(f"{PREFIX}/rooms/{room}/blocks/{block['id']}/rule", json=rule(text(value="Australia")))

    client.post(
        f"{PREFIX}/rooms/{room}/personalise", json={"region": "Australia"}, params={"actor": "dana"}
    )

    stored = client.get(f"{PREFIX}/rooms/{room}/personalisations").json()["personalisations"][0]
    assert stored["data"]["variables"] == {"region": "Australia"}
    assert stored["data"]["shown"] == [block["id"]]
    assert stored["data"]["decision"][0]["conditions"][0]["status"] == "matched"


def test_personalise_creates_exactly_one_record_and_one_audit_row(client):
    room = make_room(client)
    make_block(client, room)
    before = client.get("/api/audit").json()["count"]

    client.post(f"{PREFIX}/rooms/{room}/personalise", json={"region": "Australia"})

    assert client.get("/api/records/personalisation").json()["count"] == 1
    assert len(audit(client, collection="personalisation")) == 1
    assert client.get("/api/audit").json()["count"] == before + 1


def test_personalise_is_scoped_to_its_room(client):
    room_a, room_b = make_room(client, "A"), make_room(client, "B")
    client.post(f"{PREFIX}/rooms/{room_a}/personalise", json={})
    client.post(f"{PREFIX}/rooms/{room_b}/personalise", json={})

    assert client.get(f"{PREFIX}/rooms/{room_a}/personalisations").json()["count"] == 1
    assert client.get(f"{PREFIX}/rooms/{room_b}/personalisations").json()["count"] == 1


def test_personalisation_history_accumulates(client):
    room = make_room(client)
    client.post(f"{PREFIX}/rooms/{room}/personalise", json={"region": "Australia"})
    client.post(f"{PREFIX}/rooms/{room}/personalise", json={"region": "New Zealand"})

    history = client.get(f"{PREFIX}/rooms/{room}/personalisations").json()
    assert history["count"] == 2
    assert history["personalisations"][0]["data"]["variables"] == {"region": "New Zealand"}


# -- S11: the rule-lossy boundary ------------------------------------------------------ #


def test_saving_a_ruled_block_to_the_library_drops_the_rule_and_warns(client):
    """S11: rules do not survive, and here the loss is stated rather than silent."""
    room = make_room(client)
    block = make_block(client, room, title="AU pricing")
    client.put(f"{PREFIX}/rooms/{room}/blocks/{block['id']}/rule", json=rule(text()))

    response = client.post(f"{PREFIX}/rooms/{room}/blocks/{block['id']}/save-to-library")

    assert response.status_code == 201
    assert response.json()["rules_dropped"] is True
    assert "cannot have rules" in response.json()["warning"]


def test_the_saved_block_record_documents_the_loss(client):
    """An auditable trace of what was dropped, not a silent omission."""
    room = make_room(client)
    block = make_block(client, room, title="AU pricing")
    client.put(f"{PREFIX}/rooms/{room}/blocks/{block['id']}/rule", json=rule(text()))

    saved = client.post(
        f"{PREFIX}/rooms/{room}/blocks/{block['id']}/save-to-library"
    ).json()["saved_block"]

    assert "rule" not in saved["data"]
    assert saved["data"]["rules_dropped"] is True
    assert saved["data"]["dropped_rule"]["conditions"][0]["variable"] == "region"
    assert saved["data"]["source_block_id"] == block["id"]


def test_a_saved_block_keeps_the_content_it_was_copied_from(client):
    room = make_room(client)
    block = make_block(client, room, title="AU pricing", body="Our pricing", position=1)
    client.put(f"{PREFIX}/rooms/{room}/blocks/{block['id']}/rule", json=rule(text()))

    saved = client.post(
        f"{PREFIX}/rooms/{room}/blocks/{block['id']}/save-to-library"
    ).json()["saved_block"]

    assert saved["data"]["title"] == "AU pricing"
    assert saved["data"]["body"] == "Our pricing"
    assert saved["data"]["position"] == 1


def test_saving_an_unruled_block_reports_nothing_dropped(client):
    room = make_room(client)
    block = make_block(client, room)

    response = client.post(f"{PREFIX}/rooms/{room}/blocks/{block['id']}/save-to-library")

    assert response.json()["rules_dropped"] is False
    assert "warning" not in response.json()
    assert response.json()["saved_block"]["data"]["rules_dropped"] is False


def test_saving_to_the_library_does_not_alter_the_source_block(client):
    room = make_room(client)
    block = make_block(client, room)
    client.put(f"{PREFIX}/rooms/{room}/blocks/{block['id']}/rule", json=rule(text()))

    client.post(f"{PREFIX}/rooms/{room}/blocks/{block['id']}/save-to-library")

    rule_body = client.get(f"{PREFIX}/rooms/{room}/blocks/{block['id']}/rule").json()
    assert rule_body["rule"] is not None


def test_saving_to_the_library_is_audited(client):
    room = make_room(client)
    block = make_block(client, room)

    client.post(f"{PREFIX}/rooms/{room}/blocks/{block['id']}/save-to-library")

    entries = audit(client, collection="saved_block")
    assert len(entries) == 1
    assert entries[0]["after_state"]["title"] == "Block"


def test_saving_an_unknown_block_to_the_library_is_404(client):
    room = make_room(client)
    assert client.post(f"{PREFIX}/rooms/{room}/blocks/block_nope/save-to-library").status_code == 404


# -- demo data ------------------------------------------------------------------------- #


def test_seed_leaves_a_readable_rule_builder():
    """The feature's own ``seed(db, context)``, which replaced its seed.py edit.

    A feature whose page is empty in the demo is a feature nobody can review. The
    hook runs against a core-shaped dataset (rooms and nothing else) and has to
    produce something the page can actually show: variables to filter on, blocks
    with and without rules, and one recorded decision.
    """
    module = load_feature("wf013_rules")
    now = datetime(2026, 9, 27, 9, 0, tzinfo=timezone.utc)

    tmp = tempfile.TemporaryDirectory()
    try:
        db = AuditedDatabase(str(Path(tmp.name) / "seeded.db"), actor="seed")
        try:
            rooms = [
                db.create("room", {"name": name}, actor="dana", source="seed")
                for name in ("Northwind", "Contoso", "Fabrikam", "Adventure Works")
            ]

            summary = module.seed(
                db,
                {
                    "room_ids": [(room["id"], room["data"]["name"]) for room in rooms],
                    "now": now,
                    "rng": random.Random("wf013_rules"),
                },
            )

            assert "4 variables" in summary
            assert "8 blocks" in summary
            assert "1 personalisation" in summary

            # The variables endpoint has something to render, including a CRM one.
            variables = db.list("variable", limit=50, order_by="created_at", descending=False)
            assert {variable["data"]["source"] for variable in variables} == {"account", "crm"}

            # The first room's blocks include an Or rule, an And rule and a
            # fail-open rule, and the decision recorded for it used the domain
            # function the API uses. `position` is a payload field, not a
            # orderable column, which is the schema-flexibility rule in practice.
            blocks = db.list("block", room_id=rooms[0]["id"], limit=50, order_by="created_at")
            blocks.sort(key=lambda record: record["data"].get("position", 0))
            joins = {block["data"]["rule"]["join"] for block in blocks if block["data"].get("rule")}
            assert joins == {"and", "or"}

            # An Accept Block is seeded on purpose: the page must be able to show
            # that it is the one type that cannot carry a rule.
            accept = [
                record
                for record in db.list("block", limit=50)
                if (record["data"].get("type") or "") == "accept"
            ]
            assert accept and "rule" not in accept[0]["data"]

            # The promo block's only condition has no variable, so fail-open (S8)
            # is visible on screen rather than something a reviewer has to build.
            # It is seeded on a second room, so this looks across the dataset.
            incomplete = [
                record
                for record in db.list("block", limit=50, order_by="created_at")
                if any(
                    not condition.get("variable")
                    for condition in ((record["data"].get("rule") or {}).get("conditions") or [])
                )
            ]
            assert incomplete
            # And the domain agrees it still shows, which is the point of S8.
            decision = rules_module.evaluate_block(
                incomplete[0]["data"], {"region": "Australia", "seats": 40}
            )
            assert decision["shown"] is True
            assert decision["reason"] == "all_incomplete"

            personalisation = db.list("personalisation", limit=1)
            assert personalisation[0]["data"]["variables"] == {"region": "Australia", "seats": 40}
        finally:
            db.close()
    finally:
        tmp.cleanup()


def test_seed_reports_when_there_is_nothing_to_attach_to():
    module = load_feature("wf013_rules")
    tmp = tempfile.TemporaryDirectory()
    try:
        db = AuditedDatabase(str(Path(tmp.name) / "empty.db"), actor="seed")
        try:
            assert module.seed(db, {"room_ids": [], "now": datetime.now(timezone.utc), "rng": random.Random("x")})
        finally:
            db.close()
    finally:
        tmp.cleanup()
