"""WF-130 domain tests: the assembly engine, the personalisation, the synced
section, the stage reveal, and the review/publish lifecycle.

These drive :mod:`dsr.collateral_generation` directly, over a temporary database,
with no HTTP client. The HTTP surface has its own module, ``test_wf130_http.py``.

Every test names the rule it protects, quoting
``docs/research/digital-sales-room-workflows/wf/WF-130.md`` where the research is
the source. The clock is frozen at :data:`NOW`, so no test depends on today's
date - the brief calls that a time bomb.

The database is a fresh one per test, through the shared ``store`` fixture, so
this file passes alone and under ``pytest-xdist`` in any order.
"""

from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path

import dsr.collateral_generation as domain
import pytest
from dsr.collateral_generation import (
    ASSET_COLLECTION,
    COLLATERAL_COLLECTION,
    EVENT_COLLECTION,
    CollateralBook,
    CollateralNotFound,
    CollateralRefused,
    CollateralValidationError,
    apply_stage,
    assemble,
    is_revealed,
    merge_tokens,
    normalise_deal,
    normalise_section_spec,
    normalise_template,
    render_section,
    stage_rank,
    summarise,
    token_values,
    visible_section_keys,
)
from dsr.store import RecordStore

NOW = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)

DOMAIN_DIR = Path(domain.__file__).parent
FEATURE_MODULE = (
    Path(domain.__file__).parent.parent
    / "features"
    / "wf130_generated_personalised_collateral_from_deal_data.py"
)

DEAL = {"account": "Northwind", "contact": "Ava Chen", "product": "DSR", "stage": "discovery"}


# --------------------------------------------------------------------------- #
# Fixtures and helpers
# --------------------------------------------------------------------------- #


@pytest.fixture()
def room(store: RecordStore) -> dict:
    return store.create("room", {"name": "Northwind"}, actor="dana", source="test")


@pytest.fixture()
def book(store: RecordStore) -> CollateralBook:
    """The book with a frozen clock, so every timestamp is exact."""
    return CollateralBook(store, now=lambda: NOW)


def register_asset(book: CollateralBook, **overrides) -> dict:
    payload = {"key": "northwind-case-study", "body": "Case body for {{account}}."}
    payload.update(overrides)
    return book.register_asset(payload, actor="dana", source="POST /api/wf-130/assets")


def make_collateral(book: CollateralBook, room_id: str, **overrides) -> dict:
    payload = {"deal": dict(DEAL), "template_key": "proposal_document"}
    payload.update(overrides)
    return book.create_collateral(
        room_id, payload, actor="dana", source="POST /api/wf-130/collateral"
    )


def domain_feature_seed(db, context):
    """Call the feature module's ``seed`` through its real module path."""
    import importlib

    module = importlib.import_module(
        "dsr.features.wf130_generated_personalised_collateral_from_deal_data"
    )
    return module.seed(db, context)


# --------------------------------------------------------------------------- #
# The scalar helpers
# --------------------------------------------------------------------------- #


class TestTheScalarHelpers:
    def test_a_token_is_replaced_with_the_deal_value(self):
        values = token_values({"account": "Northwind", "contact": "Ava Chen"})
        rendered, used, unresolved = merge_tokens("Hi {{contact_first}} of {{account}}.", values)
        assert rendered == "Hi Ava of Northwind."
        assert set(used) == {"contact_first", "account"}
        assert unresolved == ()

    def test_an_unknown_token_is_refused(self):
        with pytest.raises(CollateralValidationError) as caught:
            merge_tokens("Hi {{accout}}.", token_values({"account": "Northwind"}))
        assert caught.value.payload()["error"] == "invalid_collateral"
        assert caught.value.payload()["token"] == "accout"

    def test_a_token_with_no_value_is_named_not_silently_blank(self):
        rendered, _used, unresolved = merge_tokens(
            "Hi {{contact}}.", token_values({"account": "X"})
        )
        assert rendered == "Hi ."
        assert unresolved == ("contact",)

    def test_the_stage_ladder_is_ordered(self):
        assert stage_rank("discovery") < stage_rank("procurement") < stage_rank("closed_won")
        assert stage_rank("nonsense") == -1

    def test_a_section_is_revealed_at_or_past_its_stage(self):
        assert is_revealed("discovery", None)
        assert not is_revealed("discovery", "procurement")
        assert is_revealed("procurement", "procurement")
        assert is_revealed("closed_won", "procurement")

    def test_a_deal_needs_an_account(self):
        with pytest.raises(CollateralValidationError):
            normalise_deal({"stage": "discovery"})

    def test_an_unknown_stage_is_refused(self):
        with pytest.raises(CollateralValidationError):
            normalise_deal({"account": "Northwind", "stage": "galaxy"})

    def test_a_deal_defaults_to_discovery_and_derives_the_first_name(self):
        deal = normalise_deal({"account": "Northwind", "contact": "Ava Chen"})
        assert deal["stage"] == "discovery"
        assert deal["contact_first"] == "Ava"


class TestTemplates:
    def test_a_shipped_template_loads_by_key(self):
        template = normalise_template("proposal_document")
        assert template["kind"] == "document"
        assert [section["key"] for section in template["sections"]][0] == "overview"

    def test_the_shipped_template_marks_its_synced_section(self):
        template = normalise_template("proposal_document")
        synced = [section for section in template["sections"] if section["synced"]]
        assert [section["asset_ref"] for section in synced] == ["northwind-case-study"]

    def test_an_unknown_template_key_is_refused(self):
        with pytest.raises(CollateralValidationError):
            normalise_template("no_such_template")

    def test_a_custom_template_is_validated_like_a_shipped_one(self):
        template = normalise_template(
            {
                "key": "custom",
                "kind": "deck",
                "sections": [{"key": "a", "title": "A", "body": "hi"}],
            }
        )
        assert template["kind"] == "deck"
        assert template["sections"][0]["kind"] == "narrative"

    def test_a_template_needs_at_least_one_section(self):
        with pytest.raises(CollateralValidationError):
            normalise_template({"key": "x", "kind": "deck", "sections": []})

    def test_two_sections_may_not_share_a_key(self):
        with pytest.raises(CollateralValidationError):
            normalise_template(
                {
                    "key": "x",
                    "kind": "document",
                    "sections": [
                        {"key": "a", "title": "A"},
                        {"key": "a", "title": "B"},
                    ],
                }
            )

    def test_a_section_needs_a_key_and_a_title(self):
        with pytest.raises(CollateralValidationError):
            normalise_section_spec({"title": "A"}, 0)
        with pytest.raises(CollateralValidationError):
            normalise_section_spec({"key": "a"}, 0)

    def test_an_unknown_section_kind_is_refused(self):
        with pytest.raises(CollateralValidationError):
            normalise_section_spec({"key": "a", "title": "A", "kind": "hologram"}, 0)

    def test_a_section_reveals_at_a_known_stage_only(self):
        with pytest.raises(CollateralValidationError):
            normalise_section_spec({"key": "a", "title": "A", "reveal_at_stage": "galaxy"}, 0)


class TestAssembly:
    def test_a_document_is_assembled_from_the_deal(self):
        template = normalise_template("proposal_document")
        data = assemble(
            template, normalise_deal(DEAL), {"northwind-case-study": {"version": 1, "body": "x"}}
        )
        assert data["kind"] == "document"
        assert data["engine"] == "rules"
        assert data["section_count"] == 5

    def test_the_pricing_section_is_hidden_until_procurement(self):
        template = normalise_template("proposal_document")
        assets = {"northwind-case-study": {"version": 1, "body": "x"}}
        early = assemble(template, normalise_deal({**DEAL, "stage": "discovery"}), assets)
        late = assemble(template, normalise_deal({**DEAL, "stage": "procurement"}), assets)
        assert [s["key"] for s in early["sections"] if not s["visible"]] == ["pricing"]
        assert "pricing" in visible_section_keys(late["sections"])

    def test_a_missing_source_asset_is_refused(self):
        template = normalise_template("proposal_document")
        with pytest.raises(CollateralValidationError) as caught:
            assemble(template, normalise_deal(DEAL), {})
        assert caught.value.payload()["asset_ref"] == "northwind-case-study"

    def test_the_personalisation_reaches_the_body(self):
        template = normalise_template("proposal_document")
        data = assemble(
            template,
            normalise_deal({**DEAL, "stage": "procurement"}),
            {"northwind-case-study": {"version": 3, "body": "Proof"}},
        )
        overview = next(s for s in data["sections"] if s["key"] == "overview")
        assert "Northwind" in overview["body"]
        assert "Ava" in overview["body"]
        assert overview["unresolved_tokens"] == []

    def test_the_synced_section_carries_the_asset_version(self):
        template = normalise_template("proposal_document")
        data = assemble(
            template,
            normalise_deal(DEAL),
            {"northwind-case-study": {"version": 7, "body": "Proof"}},
        )
        section = next(s for s in data["sections"] if s["key"] == "case_study")
        assert section["synced"] is True
        assert section["asset_version"] == 7
        assert section["body"].endswith("Proof")

    def test_render_section_keeps_the_un_rendered_template_body(self):
        spec = normalise_section_spec(
            {"key": "a", "title": "A", "body": "Hi {{account}}", "asset_ref": "cs"}, 0
        )
        section = render_section(spec, normalise_deal(DEAL), {"version": 1, "body": "asset"})
        assert section["template_body"] == "Hi {{account}}"
        assert section["body"] == "Hi Northwind\n\nasset"

    def test_apply_stage_does_not_mutate_and_reveals(self):
        sections = [{"key": "p", "reveal_at_stage": "procurement", "visible": False}]
        moved = apply_stage(sections, "procurement")
        assert moved[0]["visible"] is True
        assert sections[0]["visible"] is False

    def test_summarise_counts(self):
        sections = [
            {"key": "a", "visible": True, "synced": False},
            {"key": "b", "visible": False, "synced": True},
        ]
        assert summarise(sections) == {
            "section_count": 2,
            "visible_count": 1,
            "hidden_count": 1,
            "synced_count": 1,
        }


# --------------------------------------------------------------------------- #
# The book, over the audited store
# --------------------------------------------------------------------------- #


class TestAssets:
    def test_a_source_asset_is_registered_at_version_one(self, book: CollateralBook):
        asset = register_asset(book)
        assert asset["key"] == "northwind-case-study"
        assert asset["version"] == 1

    def test_a_duplicate_asset_key_is_refused(self, book: CollateralBook):
        register_asset(book)
        with pytest.raises(CollateralRefused):
            register_asset(book)

    def test_an_asset_without_a_key_is_refused(self, book: CollateralBook):
        with pytest.raises(CollateralValidationError):
            book.register_asset({"body": "x"}, actor="dana", source="test")

    def test_updating_an_asset_bumps_its_version(self, book: CollateralBook):
        asset = register_asset(book)
        updated = book.update_asset(asset["id"], {"body": "new"}, actor="dana", source="test")
        assert updated["version"] == 2


class TestGeneration:
    def test_a_collateral_is_assembled_in_draft_with_its_sections(
        self, book: CollateralBook, room: dict
    ):
        register_asset(book)
        collateral = make_collateral(book, room["id"])
        assert collateral["state"] == "draft"
        assert collateral["engine"] == "rules"
        assert collateral["section_count"] == 5
        assert collateral["hidden_count"] == 1

    def test_a_collateral_for_an_unknown_room_is_not_found(self, book: CollateralBook):
        with pytest.raises(CollateralNotFound):
            make_collateral(book, "room_absent")

    def test_an_unknown_collateral_is_not_found(self, book: CollateralBook):
        with pytest.raises(CollateralNotFound):
            book.read("wf130_collateral_absent")

    def test_generation_writes_a_generated_event(self, book: CollateralBook, room: dict):
        register_asset(book)
        collateral = make_collateral(book, room["id"])
        kinds = [event["kind"] for event in book.list_events(collateral["id"])]
        assert kinds == ["generated"]


class TestReviewAndReveal:
    def test_an_edit_changes_a_section_body(self, book: CollateralBook, room: dict):
        register_asset(book)
        collateral = make_collateral(book, room["id"])
        edited = book.update_collateral(
            collateral["id"],
            {"sections": [{"key": "overview", "body": "Rewritten."}]},
            actor="dana",
            source="test",
        )
        overview = next(s for s in edited["sections"] if s["key"] == "overview")
        assert overview["body"] == "Rewritten."
        assert edited["version"] == 2

    def test_an_edit_to_an_unknown_section_is_refused(self, book: CollateralBook, room: dict):
        register_asset(book)
        collateral = make_collateral(book, room["id"])
        with pytest.raises(CollateralValidationError):
            book.update_collateral(
                collateral["id"], {"sections": [{"key": "ghost"}]}, actor="dana", source="test"
            )

    def test_advancing_the_stage_reveals_the_pricing_section(
        self, book: CollateralBook, room: dict
    ):
        register_asset(book)
        collateral = make_collateral(book, room["id"])
        assert collateral["visible_count"] == 4
        advanced = book.update_collateral(
            collateral["id"], {"deal": {"stage": "procurement"}}, actor="dana", source="test"
        )
        assert advanced["visible_count"] == 5
        assert "pricing" in advanced["visible_sections"]

    def test_advancing_the_stage_records_the_reveal(self, book: CollateralBook, room: dict):
        register_asset(book)
        collateral = make_collateral(book, room["id"])
        book.update_collateral(
            collateral["id"], {"deal": {"stage": "procurement"}}, actor="dana", source="test"
        )
        events = {event["kind"]: event for event in book.list_events(collateral["id"])}
        assert "revealed" in events
        assert events["revealed"]["detail"]["sections"] == ["pricing"]


class TestPublishAndWithdraw:
    def test_a_draft_publishes_and_becomes_live(self, book: CollateralBook, room: dict):
        register_asset(book)
        collateral = make_collateral(book, room["id"])
        published = book.publish(collateral["id"], actor="dana", source="test")
        assert published["state"] == "published"
        assert published["published_at"] == NOW.isoformat(timespec="milliseconds")

    def test_a_publish_with_an_empty_always_visible_section_is_refused(
        self, book: CollateralBook, room: dict
    ):
        register_asset(book)
        collateral = make_collateral(book, room["id"])
        book.update_collateral(
            collateral["id"],
            {"sections": [{"key": "overview", "body": ""}]},
            actor="dana",
            source="test",
        )
        with pytest.raises(CollateralRefused) as caught:
            book.publish(collateral["id"], actor="dana", source="test")
        assert "overview" in caught.value.payload()["sections"]

    def test_a_withdrawn_collateral_refuses_an_edit(self, book: CollateralBook, room: dict):
        register_asset(book)
        collateral = make_collateral(book, room["id"])
        book.publish(collateral["id"], actor="dana", source="test")
        withdrawn = book.withdraw(collateral["id"], actor="dana", source="test")
        assert withdrawn["state"] == "withdrawn"
        with pytest.raises(CollateralRefused):
            book.update_collateral(collateral["id"], {"title": "Nope"}, actor="dana", source="test")

    def test_a_withdrawn_collateral_refuses_a_publish(self, book: CollateralBook, room: dict):
        register_asset(book)
        collateral = make_collateral(book, room["id"])
        book.withdraw(collateral["id"], actor="dana", source="test")
        with pytest.raises(CollateralRefused):
            book.publish(collateral["id"], actor="dana", source="test")


class TestSyncedRefresh:
    def test_a_changed_asset_refreshes_every_live_collateral(
        self, book: CollateralBook, room: dict
    ):
        asset = register_asset(book)
        collateral = make_collateral(book, room["id"])
        book.publish(collateral["id"], actor="dana", source="test")
        book.update_asset(asset["id"], {"body": "Revised proof."}, actor="dana", source="test")
        result = book.refresh_from_asset(asset["id"], actor="dana", source="test")
        assert result["refreshed"] == 1
        refreshed = book.read(collateral["id"])
        section = next(s for s in refreshed["sections"] if s["key"] == "case_study")
        assert section["asset_version"] == 2
        assert section["body"].endswith("Revised proof.")

    def test_a_draft_is_not_refreshed(self, book: CollateralBook, room: dict):
        asset = register_asset(book)
        make_collateral(book, room["id"])
        result = book.refresh_from_asset(asset["id"], actor="dana", source="test")
        assert result["refreshed"] == 0

    def test_refreshing_an_unknown_asset_is_not_found(self, book: CollateralBook):
        with pytest.raises(CollateralNotFound):
            book.refresh_from_asset("wf130_asset_absent", actor="dana", source="test")


class TestSummaryAndContract:
    def test_the_summary_counts_by_state_and_section(self, book: CollateralBook, room: dict):
        register_asset(book)
        collateral = make_collateral(book, room["id"])
        book.publish(collateral["id"], actor="dana", source="test")
        summary = book.summary(room["id"])
        assert summary["collaterals"] == 1
        assert summary["published"] == 1
        assert summary["hidden_sections"] == 1
        assert summary["assets"] == 1

    def test_the_vocabulary_and_decisions_are_served(self, book: CollateralBook):
        vocabulary = book.vocabulary()
        assert vocabulary["states"] == ["draft", "published", "withdrawn"]
        assert vocabulary["deal_stages"][0] == "discovery"
        decisions = book.decisions()
        assert "assembly_engine" in decisions["decisions"]
        assert decisions["not_implemented"]


# --------------------------------------------------------------------------- #
# Architecture: the dependency direction the contract fixes
# --------------------------------------------------------------------------- #

#: The only ``dsr`` packages a module in this domain package may import. This
#: workflow is standalone, so the sibling domain packages are absent.
ALLOWED_DSR_PREFIXES = ("dsr.store", "dsr.collateral_generation")


def _domain_modules() -> list[Path]:
    return sorted(DOMAIN_DIR.glob("*.py"))


class TestArchitecture:
    def test_the_domain_package_never_imports_the_app_or_a_feature(self):
        for path in _domain_modules():
            # The import statements are what matter, so the check is on the AST
            # rather than on the raw text: a docstring that names ``dsr.api`` to
            # explain why it is not imported is not an import of it.
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                names: list[str] = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module]
                for name in names:
                    assert name != "dsr.api" and not name.startswith("dsr.api."), path.name
                    assert name != "dsr.features" and not name.startswith("dsr.features."), (
                        path.name
                    )
                    assert not name.startswith("fastapi"), path.name
            text = path.read_text(encoding="utf-8")
            assert "import sqlite3" not in text, path.name
            assert "sqlite3.connect" not in text, path.name

    def test_the_domain_package_imports_only_allowed_dsr_packages(self):
        for path in _domain_modules():
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                names: list[str] = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module]
                for name in names:
                    if not name.startswith("dsr"):
                        continue
                    assert name.startswith(ALLOWED_DSR_PREFIXES), f"{path.name} imports {name}"

    def test_the_feature_module_takes_its_dependencies_from_deps(self):
        text = FEATURE_MODULE.read_text(encoding="utf-8")
        assert "from dsr.deps import" in text
        assert "from dsr.api" not in text and "import dsr.api" not in text
        assert "import sqlite3" not in text

    def test_the_feature_module_declares_its_ticket_prefix_and_handler(self):
        import importlib

        module = importlib.import_module(
            "dsr.features.wf130_generated_personalised_collateral_from_deal_data"
        )
        assert module.FEATURE["id"] == "wf-130-collateral-generation"
        assert module.FEATURE["ticket"] == "WF-130"
        assert module.router.prefix == "/api/wf-130"
        assert list(module.EXCEPTION_HANDLERS) == [domain.CollateralError]

    def test_the_error_type_is_this_workflows_own(self):
        assert domain.CollateralError.__module__ == "dsr.collateral_generation.errors"


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #


class TestSeed:
    def test_the_seed_generates_publishes_and_refreshes(self, db, store: RecordStore):
        scoped = RecordStore(db)
        room = scoped.create("room", {"name": "Northwind"}, actor="dana", source="test")
        summary = domain_feature_seed(
            db, {"room_ids": [(room["id"], "Northwind")], "now": NOW, "rng": None}
        )
        assert isinstance(summary, str) and summary
        # A Windows console prints this string; a non-cp1252 character once broke
        # the whole seeder, so the encoding is asserted rather than assumed.
        summary.encode("cp1252")
        collateral = scoped.find(COLLATERAL_COLLECTION, {})
        assert len(collateral) == 2
        assert {row["data"]["state"] for row in collateral} == {"published", "withdrawn"}
        assert scoped.find(ASSET_COLLECTION, {})
        assert len(scoped.find(EVENT_COLLECTION, {})) >= 5

    def test_the_seed_names_its_non_success_states(self, db, store: RecordStore):
        scoped = RecordStore(db)
        room = scoped.create("room", {"name": "Northwind"}, actor="dana", source="test")
        summary = domain_feature_seed(
            db, {"room_ids": [(room["id"], "Northwind")], "now": NOW, "rng": None}
        )
        assert "hidden section" in summary
        assert "duplicate refused" in summary
        assert "withdrawn artefact refused an edit" in summary

    def test_the_seed_reports_nothing_when_there_is_no_room(self, db):
        summary = domain_feature_seed(db, {"room_ids": [], "now": NOW, "rng": None})
        assert "0 generated collateral" in summary
        summary.encode("cp1252")


# --------------------------------------------------------------------------- #
# The error envelope
# --------------------------------------------------------------------------- #


def test_an_error_can_carry_its_own_code_and_context():
    error = domain.CollateralValidationError("nope", code="custom_code", field="x")
    assert error.status_code == 422
    assert error.payload()["error"] == "custom_code"
    assert error.payload()["field"] == "x"
    assert error.payload()["detail"] == "nope"
