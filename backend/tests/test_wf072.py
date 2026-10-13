"""WF-072 in the domain: the rules, the grants, the AND, the download path and the seed.

The HTTP surface is in ``test_wf072_http.py``. This file drives the domain package directly,
so a rule can be exercised without a router and the download path can be checked for what it
writes and for what it refuses to write.

The tests are organised by the question they answer:

``the flagged booleans``
    That an absent field reads as off, that the CLI's on/off spelling is accepted, and that a
    typo is refused rather than guessed at.
``the three-way AND``
    That a download needs every one of the three controls, that one alone is not enough, and
    that the refusal names which gate withheld the file.
``the per-item grant``
    That a live grant, a revoked row and a missing row are three states, and that a live row
    saying false is a grant that grants nothing.
``the room and link policies``
    That a policy validates only the fields it owns, that the safe default survives a missing
    field, and that a write is an audited insert or update.
``the download path``
    That a permitted fetch writes a view row with downloaded_at and download_type, and that a
    refused fetch writes nothing.
``the seed``
    That it returns a cp1252-encodable string and creates the states it names.
``the architectural guards``
    That the domain package depends on the store and on itself, and on nothing else in dsr.
"""

from __future__ import annotations

import ast
import importlib
from datetime import datetime, timezone
from pathlib import Path

import pytest
from dsr.store import RecordStore
from dsr.view_only_access import inferences, rules, vocabulary as vocab
from dsr.view_only_access.engine import ViewOnlyAccessEngine

DOMAIN_PACKAGE = "dsr.view_only_access"
FEATURE_MODULE = "dsr.features.wf072_enforce_view_only_access_and_block_bulk"
FEATURE_ID = "wf-072-enforce-view-only-access-and-block-bulk"
PREFIX = "/api/wf-072"

FIXED_NOW = datetime(2026, 10, 13, 12, 0, 0, tzinfo=timezone.utc)


def fixed_engine(store: RecordStore) -> ViewOnlyAccessEngine:
    """An engine whose clock is fixed, so a test never depends on today's date."""

    return ViewOnlyAccessEngine(store, now=lambda: FIXED_NOW)


def domain_paths() -> list[Path]:
    package = Path(importlib.import_module(DOMAIN_PACKAGE).__file__ or "").parent
    return sorted(package.glob("*.py"))


def feature_path() -> Path:
    module = importlib.import_module(FEATURE_MODULE)
    return Path(module.__file__ or "")


# --------------------------------------------------------------------------- #
# the flagged booleans
# --------------------------------------------------------------------------- #


class TestCoerceFlag:
    def test_an_absent_field_is_off(self):
        """The documented default is view-only, so missing must mean no download."""
        assert rules.coerce_flag(None, vocab.ALLOW_DOWNLOAD) is False

    @pytest.mark.parametrize("token", ["on", "ON", "true", "True", "1", "yes"])
    def test_the_true_spellings(self, token):
        assert rules.coerce_flag(token, vocab.ALLOW_DOWNLOAD) is True

    @pytest.mark.parametrize("token", ["off", "OFF", "false", "False", "0", "no", ""])
    def test_the_false_spellings(self, token):
        assert rules.coerce_flag(token, vocab.ALLOW_DOWNLOAD) is False

    def test_json_booleans_pass_through(self):
        assert rules.coerce_flag(True, vocab.BULK_DOWNLOAD) is True
        assert rules.coerce_flag(False, vocab.BULK_DOWNLOAD) is False

    def test_numbers_zero_and_one_are_accepted(self):
        assert rules.coerce_flag(1, vocab.BULK_DOWNLOAD) is True
        assert rules.coerce_flag(0, vocab.BULK_DOWNLOAD) is False

    def test_a_typo_is_refused_rather_than_guessed(self):
        with pytest.raises(rules.PolicyRefused) as caught:
            rules.coerce_flag("maybe", vocab.ALLOW_DOWNLOAD)
        assert vocab.ALLOW_DOWNLOAD in caught.value.errors

    def test_an_arbitrary_string_is_refused(self):
        with pytest.raises(rules.PolicyRefused):
            rules.coerce_flag("download", vocab.CAN_DOWNLOAD)


# --------------------------------------------------------------------------- #
# the three-way AND
# --------------------------------------------------------------------------- #


class TestThreeWayAnd:
    def test_all_three_permit(self):
        decision = rules.decide_download(True, True, True)
        assert decision[vocab.DOWNLOAD_FIELD] is True
        assert decision[vocab.STATE_FIELD] == vocab.STATE_DOWNLOAD
        assert decision[vocab.BLOCKED_BY_FIELD] == []

    @pytest.mark.parametrize(
        "room,link,item",
        [
            (True, True, False),
            (True, False, True),
            (False, True, True),
        ],
    )
    def test_one_control_alone_is_not_enough(self, room, link, item):
        """The issue's own requirement: a test must fail when only one control permits."""
        decision = rules.decide_download(room, link, item)
        assert decision[vocab.DOWNLOAD_FIELD] is False
        assert decision[vocab.STATE_FIELD] == vocab.STATE_VIEW_ONLY
        assert len(decision[vocab.BLOCKED_BY_FIELD]) == 1

    def test_none_of_the_three_permits(self):
        decision = rules.decide_download(False, False, False)
        assert decision[vocab.BLOCKED_BY_FIELD] == list(vocab.GATES)

    def test_the_refusal_names_the_gate_that_withheld(self):
        decision = rules.decide_download(True, True, False)
        assert decision[vocab.BLOCKED_BY_FIELD] == [vocab.GATE_ITEM]
        assert vocab.GATE_LABELS[vocab.GATE_ITEM] in decision[vocab.REASON_FIELD]

    def test_every_gate_is_reported_separately(self):
        decision = rules.decide_download(False, True, True)
        assert decision[vocab.GATES_FIELD] == {
            vocab.GATE_ROOM: False,
            vocab.GATE_LINK: True,
            vocab.GATE_ITEM: True,
        }

    def test_the_safe_default_applies_through_the_and(self):
        """A payload with none of the fields set permits no download."""
        assert rules.decide_download(None, None, None)[vocab.DOWNLOAD_FIELD] is False


class TestBulkDecision:
    def test_a_zip_needs_the_room_and_the_link_and_every_item(self):
        items = [
            {vocab.ITEM_ID: "a", vocab.CAN_DOWNLOAD: True},
            {vocab.ITEM_ID: "b", vocab.CAN_DOWNLOAD: True},
        ]
        decision = rules.decide_bulk(True, True, items)
        assert decision[vocab.DOWNLOAD_FIELD] is True
        assert decision["blocked_items"] == []

    def test_the_room_control_alone_cannot_build_a_zip(self):
        decision = rules.decide_bulk(True, False, [])
        assert decision[vocab.DOWNLOAD_FIELD] is False
        assert decision[vocab.BLOCKED_BY_FIELD] == [vocab.GATE_LINK]

    def test_a_withheld_item_blocks_the_whole_zip_and_is_named(self):
        items = [
            {vocab.ITEM_ID: "a", vocab.CAN_DOWNLOAD: True},
            {vocab.ITEM_ID: "b", vocab.CAN_DOWNLOAD: False},
        ]
        decision = rules.decide_bulk(True, True, items)
        assert decision[vocab.DOWNLOAD_FIELD] is False
        assert decision["blocked_items"] == ["b"]


# --------------------------------------------------------------------------- #
# the per-item grant
# --------------------------------------------------------------------------- #


def permission_row(item_id: str, can_download: bool, *, revoked: str | None = None) -> dict:
    """A WF-074 permission row, shaped as that workflow writes it."""

    data = {
        vocab.ITEM_ID: item_id,
        vocab.ITEM_TYPE: vocab.ITEM_TYPE_DOCUMENT,
        "can_view": True,
        vocab.CAN_DOWNLOAD: can_download,
    }
    if revoked:
        data[vocab.REVOKED_AT] = revoked
    return {
        "id": f"row_{item_id}",
        "collection": vocab.WF074_LINK_PERMISSION_COLLECTION,
        "data": data,
    }


class TestGrant:
    def test_a_live_grant_is_in_force(self):
        grant = rules.grant_from_rows([permission_row("doc", True)], "doc")
        assert grant[vocab.GRANT_FIELD] == vocab.GRANT_LIVE
        assert grant[vocab.CAN_DOWNLOAD] is True
        assert grant[vocab.ROW_PRESENT_FIELD] is True

    def test_a_live_row_saying_false_grants_nothing(self):
        grant = rules.grant_from_rows([permission_row("doc", False)], "doc")
        assert grant[vocab.GRANT_FIELD] == vocab.GRANT_LIVE
        assert grant[vocab.CAN_DOWNLOAD] is False

    def test_a_revoked_row_is_out_of_force(self):
        grant = rules.grant_from_rows(
            [permission_row("doc", True, revoked="2026-01-01T00:00:00+00:00")], "doc"
        )
        assert grant[vocab.GRANT_FIELD] == vocab.GRANT_REVOKED
        assert grant[vocab.CAN_DOWNLOAD] is False

    def test_a_missing_row_is_the_document_default(self):
        grant = rules.grant_from_rows([permission_row("other", True)], "doc")
        assert grant[vocab.GRANT_FIELD] == vocab.GRANT_MISSING
        assert grant[vocab.ROW_PRESENT_FIELD] is False
        assert grant[vocab.CAN_DOWNLOAD] is False

    def test_a_revoked_row_and_a_live_row_prefer_the_live_one(self):
        rows = [
            permission_row("doc", False, revoked="2026-01-01T00:00:00+00:00"),
            permission_row("doc", True),
        ]
        grant = rules.grant_from_rows(rows, "doc")
        assert grant[vocab.GRANT_FIELD] == vocab.GRANT_LIVE
        assert grant[vocab.CAN_DOWNLOAD] is True


# --------------------------------------------------------------------------- #
# the download types
# --------------------------------------------------------------------------- #


class TestDownloadTypes:
    @pytest.mark.parametrize("kind", list(vocab.DOWNLOAD_TYPES))
    def test_a_known_kind_is_accepted(self, kind):
        assert rules.coerce_download_type(kind) == kind

    def test_none_defaults_to_original(self):
        assert rules.coerce_download_type(None) == vocab.DOWNLOAD_TYPE_ORIGINAL

    def test_an_unknown_kind_is_refused(self):
        with pytest.raises(rules.PolicyRefused):
            rules.coerce_download_type("screenshot")


# --------------------------------------------------------------------------- #
# the room and link policies
# --------------------------------------------------------------------------- #


class TestPolicyValidation:
    def test_the_room_policy_interprets_only_bulk_download(self):
        changes = rules.validate_room_policy(
            {
                vocab.BULK_DOWNLOAD: "on",
                "conversations": True,
                "agents": False,
            }
        )
        assert changes[vocab.BULK_DOWNLOAD] is True
        assert changes["conversations"] is True
        assert changes["agents"] is False

    def test_the_link_policy_interprets_only_allow_download(self):
        changes = rules.validate_link_policy({vocab.ALLOW_DOWNLOAD: "off", "welcome_message": "hi"})
        assert changes[vocab.ALLOW_DOWNLOAD] is False
        assert changes["welcome_message"] == "hi"

    def test_a_non_object_payload_is_refused(self):
        with pytest.raises(rules.PolicyRefused):
            rules.validate_room_policy(["not", "an", "object"])


class TestRoomAndLinkPolicyWrites:
    def _room(self, store: RecordStore) -> str:
        return str(store.create("room", {"name": "Northwind"}, actor="dana", source="seed")["id"])

    def test_a_room_with_no_policy_reads_the_default(self, store):
        room_id = self._room(store)
        policy = fixed_engine(store).room_policy(room_id)
        assert policy["configured"] is False
        assert policy[vocab.BULK_DOWNLOAD] is False

    def test_setting_the_room_policy_creates_then_updates_one_row(self, store):
        room_id = self._room(store)
        engine = fixed_engine(store)
        first = engine.set_room_policy(
            room_id, {vocab.BULK_DOWNLOAD: True}, actor="dana", source="seed"
        )
        assert first[vocab.BULK_DOWNLOAD] is True
        second = engine.set_room_policy(
            room_id, {vocab.BULK_DOWNLOAD: False}, actor="dana", source="seed"
        )
        assert second[vocab.BULK_DOWNLOAD] is False
        assert second["policy_id"] == first["policy_id"]
        assert len(store.list(vocab.ROOM_POLICY_COLLECTION)) == 1

    def test_a_policy_for_a_missing_room_is_refused(self, store):
        with pytest.raises(rules.RoomNotFound):
            fixed_engine(store).set_room_policy(
                "room_missing", {vocab.BULK_DOWNLOAD: True}, actor="dana", source="seed"
            )

    def test_a_link_defaults_to_view_only(self, store):
        room_id = self._room(store)
        link = fixed_engine(store).create_link(
            room_id, {vocab.NAME_FIELD: "Round"}, actor="dana", source="seed"
        )
        assert link[vocab.ALLOW_DOWNLOAD] is False

    def test_a_link_can_be_escalated_and_rotated_back(self, store):
        room_id = self._room(store)
        engine = fixed_engine(store)
        link = engine.create_link(room_id, {vocab.NAME_FIELD: "Round"}, actor="dana", source="seed")
        on = engine.update_link(
            link["id"], {vocab.ALLOW_DOWNLOAD: "on"}, actor="dana", source="seed"
        )
        assert on[vocab.ALLOW_DOWNLOAD] is True
        off = engine.update_link(
            link["id"], {vocab.ALLOW_DOWNLOAD: "off"}, actor="dana", source="seed"
        )
        assert off[vocab.ALLOW_DOWNLOAD] is False

    def test_an_unknown_link_is_a_404(self, store):
        with pytest.raises(rules.LinkNotFound):
            fixed_engine(store).read_link("link_missing")

    def test_links_can_be_filtered_by_room(self, store):
        room_a = self._room(store)
        room_b = self._room(store)
        engine = fixed_engine(store)
        engine.create_link(room_a, {vocab.NAME_FIELD: "A"}, actor="dana", source="seed")
        engine.create_link(room_b, {vocab.NAME_FIELD: "B"}, actor="dana", source="seed")
        assert len(engine.links(room_a)) == 1
        assert len(engine.links()) == 2


# --------------------------------------------------------------------------- #
# the download path
# --------------------------------------------------------------------------- #


class TestDownloadPath:
    def _setup(self, store: RecordStore, *, bulk: bool, allow: bool, grant: bool):
        room = store.create("room", {"name": "Northwind"}, actor="dana", source="seed")
        engine = fixed_engine(store)
        engine.set_room_policy(room["id"], {vocab.BULK_DOWNLOAD: bulk}, actor="dana", source="seed")
        link = engine.create_link(
            room["id"],
            {vocab.NAME_FIELD: "Round", vocab.ALLOW_DOWNLOAD: allow},
            actor="dana",
            source="seed",
        )
        store.create(
            vocab.WF074_LINK_PERMISSION_COLLECTION,
            permission_row("doc_1", grant)["data"],
            room_id=room["id"],
            actor="dana",
            source="seed",
        )
        return room["id"], link["id"], engine

    def test_a_permitted_fetch_writes_a_view_row_with_the_two_nullables(self, store):
        _, link_id, engine = self._setup(store, bulk=True, allow=True, grant=True)
        result = engine.fetch(
            link_id,
            {vocab.ITEM_ID: "doc_1", vocab.DOWNLOAD_TYPE: vocab.DOWNLOAD_TYPE_PDF},
            actor="viewer",
            source="POST /api/wf-072/links/{link_id}/download",
        )
        view = result["view"]
        assert view[vocab.ITEM_ID] == "doc_1"
        assert view[vocab.DOWNLOADED_AT] is not None
        assert view[vocab.DOWNLOAD_TYPE] == vocab.DOWNLOAD_TYPE_PDF
        assert view["downloaded"] is True
        assert len(store.list(vocab.VIEW_COLLECTION)) == 1

    def test_the_view_row_is_audited_with_the_route_that_served_it(self, store):
        _, link_id, engine = self._setup(store, bulk=True, allow=True, grant=True)
        source = "POST /api/wf-072/links/{link_id}/download"
        engine.fetch(link_id, {vocab.ITEM_ID: "doc_1"}, actor="viewer", source=source)
        audit = store.audit(collection=vocab.VIEW_COLLECTION)
        assert audit[0]["source"] == source

    @pytest.mark.parametrize(
        "bulk,allow,grant",
        [
            (True, True, False),
            (True, False, True),
            (False, True, True),
        ],
    )
    def test_a_refused_fetch_writes_nothing(self, store, bulk, allow, grant):
        _, link_id, engine = self._setup(store, bulk=bulk, allow=allow, grant=grant)
        with pytest.raises(rules.DownloadRefused) as caught:
            engine.fetch(
                link_id,
                {vocab.ITEM_ID: "doc_1"},
                actor="viewer",
                source="POST /api/wf-072/links/{link_id}/download",
            )
        assert caught.value.decision[vocab.DOWNLOAD_FIELD] is False
        assert store.list(vocab.VIEW_COLLECTION) == []
        assert store.audit(collection=vocab.VIEW_COLLECTION) == []

    def test_a_fetch_must_name_an_item(self, store):
        _, link_id, engine = self._setup(store, bulk=True, allow=True, grant=True)
        with pytest.raises(rules.PolicyRefused):
            engine.fetch(link_id, {}, actor="viewer", source="POST /x")

    def test_a_revoked_grant_refuses_the_fetch(self, store):
        room_id, link_id, engine = self._setup(store, bulk=True, allow=True, grant=True)
        # Revoke the live row in place, which is what WF-074's full-replace does: the row
        # stays and carries a stamp, so the grant is out of force rather than replaced.
        rows = store.find(vocab.WF074_LINK_PERMISSION_COLLECTION, {vocab.ITEM_ID: "doc_1"})
        store.update(
            rows[0]["id"],
            {vocab.REVOKED_AT: FIXED_NOW.isoformat(), vocab.CAN_DOWNLOAD: False},
            actor="dana",
            source="seed",
        )
        with pytest.raises(rules.DownloadRefused):
            engine.fetch(link_id, {vocab.ITEM_ID: "doc_1"}, actor="viewer", source="POST /x")

    def test_evaluate_reports_every_gate(self, store):
        _, link_id, engine = self._setup(store, bulk=False, allow=True, grant=True)
        decision = engine.evaluate(link_id, "doc_1")
        assert decision["controls"][vocab.BULK_DOWNLOAD] is False
        assert decision[vocab.BLOCKED_BY_FIELD] == [vocab.GATE_ROOM]

    def test_the_read_source_is_on_every_decision(self, store):
        _, link_id, engine = self._setup(store, bulk=True, allow=True, grant=True)
        decision = engine.evaluate(link_id, "doc_1")
        assert decision[vocab.OWNER_FIELD] == vocab.SCOPE_OWNER

    def test_a_view_without_a_download_has_a_null_type(self, store):
        """The OpenAPI schema fixes download_type as nullable; a row without one stays null."""
        store.create(
            vocab.VIEW_COLLECTION,
            {vocab.ITEM_ID: "doc_1", vocab.VIEW_TYPE: vocab.VIEW_TYPE_DOCUMENT},
            actor="viewer",
            source="seed",
        )
        view = fixed_engine(store).views()[0]
        assert view[vocab.DOWNLOAD_TYPE] is None
        assert view["downloaded"] is False


class TestBoard:
    def test_summary_counts_the_states(self, store):
        room = store.create("room", {"name": "Northwind"}, actor="dana", source="seed")
        engine = fixed_engine(store)
        engine.set_room_policy(room["id"], {vocab.BULK_DOWNLOAD: True}, actor="dana", source="seed")
        engine.create_link(
            room["id"],
            {vocab.NAME_FIELD: "On", vocab.ALLOW_DOWNLOAD: True},
            actor="dana",
            source="seed",
        )
        engine.create_link(room["id"], {vocab.NAME_FIELD: "Off"}, actor="dana", source="seed")
        board = engine.summary()
        assert board["links"] == 2
        assert board["allow_download"] == 1
        assert board["view_only_links"] == 1
        assert board["bulk_download"] == 1


# --------------------------------------------------------------------------- #
# the seed
# --------------------------------------------------------------------------- #


class TestSeed:
    def test_the_seed_string_is_ascii_and_cp1252_encodable(self, store):
        module = importlib.import_module(FEATURE_MODULE)
        room = store.create("room", {"name": "Northwind"}, actor="dana", source="seed")
        text = module.seed(
            store.db,
            {"room_ids": [(room["id"], "dana")], "now": FIXED_NOW},
        )
        assert isinstance(text, str) and text
        text.encode("cp1252")  # raises if the console could not print it
        assert text.isascii()

    def test_the_seed_creates_the_states_it_names(self, store):
        module = importlib.import_module(FEATURE_MODULE)
        room = store.create("room", {"name": "Northwind"}, actor="dana", source="seed")
        text = module.seed(store.db, {"room_ids": [(room["id"], "dana")], "now": FIXED_NOW})
        assert store.find(vocab.ROOM_POLICY_COLLECTION, {vocab.BULK_DOWNLOAD: True})
        assert store.list(vocab.LINK_COLLECTION)
        assert store.list(vocab.VIEW_COLLECTION)
        assert "2 refused downloads wrote nothing: True" in text

    def test_the_seed_writes_nothing_without_a_room(self, store):
        module = importlib.import_module(FEATURE_MODULE)
        assert module.seed(store.db, {"room_ids": [], "now": FIXED_NOW}) == ""


# --------------------------------------------------------------------------- #
# the architectural guards
# --------------------------------------------------------------------------- #


class TestArchitecture:
    def test_the_domain_package_depends_only_on_the_store_and_itself(self):
        """The dependency-direction guard the brief names by name.

        A domain module that reached for ``dsr.api`` would reintroduce the coupling the host
        removes, and one that reached for another workflow's package would tie two tickets'
        releases together. Every ``dsr`` import is checked against the list this workflow may
        depend on.
        """

        allowed = {"dsr.store", DOMAIN_PACKAGE}
        for path in domain_paths():
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                names: list[str] = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module]
                for name in names:
                    if not name.startswith("dsr"):
                        continue
                    assert name in allowed, f"{path.name} imports {name}"

    def test_the_domain_package_never_imports_the_app_or_sqlite(self):
        for path in domain_paths():
            text = path.read_text(encoding="utf-8")
            assert "from dsr.api" not in text and "import dsr.api" not in text
            assert "import sqlite3" not in text

    def test_the_domain_package_does_not_import_the_permission_package_it_reads(self):
        """The per-item grant belongs to WF-074. Reading it through the store is the seam;
        importing that package would tie the two releases together."""
        for path in domain_paths():
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                names: list[str] = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module]
                for name in names:
                    assert "audience_permissions" not in name, f"{path.name} imports {name}"

    def test_the_feature_module_exports_the_documented_surface(self):
        module = importlib.import_module(FEATURE_MODULE)
        assert module.FEATURE["id"] == FEATURE_ID
        assert module.FEATURE["ticket"] == "WF-072"
        assert module.router.prefix == PREFIX

    def test_the_feature_module_takes_its_dependencies_from_deps(self):
        assert "from dsr.deps import" in feature_path().read_text(encoding="utf-8")

    def test_the_feature_module_never_imports_the_app(self):
        text = feature_path().read_text(encoding="utf-8")
        assert "from dsr.api" not in text and "import dsr.api" not in text

    def test_the_domain_package_has_no_literal_route(self):
        """A domain function hardcoding a URL leaves the audit log naming a route the app
        stopped serving."""
        for path in domain_paths():
            assert "/api/" not in path.read_text(encoding="utf-8"), path.name

    def test_the_inference_register_is_non_empty(self):
        assert inferences.count() >= 3
        assert inferences.describe()[0]["id"]
        assert inferences.describe_one("no_such_id") == {}
