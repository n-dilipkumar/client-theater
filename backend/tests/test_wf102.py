"""WF-102 domain tests: the rules and the engine, with no HTTP.

The store is the conftest ``store`` fixture for most tests, so each test sees
only its own rows and the file passes on its own and under ``pytest-xdist``.
The clock is frozen in every engine, so nothing here depends on what day it is.
"""

from __future__ import annotations

import ast
import importlib
import pathlib
from datetime import datetime, timezone

import pytest
from dsr.quote_revision import QuoteRevisionEngine, rules, vocabulary as vocab
from dsr.quote_revision.errors import RevisionConflict, RevisionError, RevisionNotFound

QUOTE = "wf086_quote"
LINE = "wf086_line_item"

ROOT = pathlib.Path(__file__).resolve().parents[2]
DOMAIN_DIR = ROOT / "backend" / "dsr" / "quote_revision"
FEATURE_MODULE = "dsr.features.wf102_revise_a_quote_to_create_a_new_revision"

#: Every engine in this file runs at this instant, so a closed date, a revision
#: stamp and a re-publish artefact are all reproducible.
FIXED = datetime(2026, 5, 1, 9, 30, tzinfo=timezone.utc)
TODAY = "2026-05-01"


def build_engine(store, at: datetime = FIXED) -> QuoteRevisionEngine:
    return QuoteRevisionEngine(store, now=lambda: at)


def make_quote(store, *, status="draft", title="Northwind annual quote", room_id="room_1", **extra):
    data = {
        "title": title,
        "status": status,
        "revision_number": 0,
        "payment_schedule": [],
        "totals": {},
    }
    data.update(extra)
    return store.create(QUOTE, data, room_id=room_id, actor="dana", source="seed")


def make_line(
    store,
    quote_id,
    *,
    position=0,
    name="Platform licence",
    sku="NWD-PLAT-ANN",
    quantity=2,
    unit_price=100.0,
    discount_type="percentage",
    discount_value=10.0,
    tax_rate=10.0,
    price_source="manual",
    room_id="room_1",
):
    return store.create(
        LINE,
        {
            "quote_id": quote_id,
            "position": position,
            "name": name,
            "sku": sku,
            "quantity": quantity,
            "unit_price": unit_price,
            "discount_type": discount_type,
            "discount_value": discount_value,
            "tax_rate": tax_rate,
            "price_source": price_source,
            "amounts": {},
        },
        room_id=room_id,
        actor="dana",
        source="seed",
    )


def make_catalogue(store, *, sku="NWD-PLAT-ANN", name="Platform licence", unit_price=250.0):
    return store.create(
        "wf087_product",
        {"name": name, "sku": sku, "unit_price": unit_price},
        actor="dana",
        source="seed",
    )


REVISED = vocab.REVISED
LOST = vocab.LOST
CANCELED = vocab.CANCELED


# --------------------------------------------------------------------------- #
# Pure rules
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "data, expected",
    [
        ({}, 0),
        ({"revision_number": 3}, 3),
        ({"revision_id": 5}, 5),
        ({"revision_number": "7"}, 7),
        ({"revision_number": "not a number"}, 0),
    ],
)
def test_revision_number_reads_every_spelling(data, expected):
    assert rules.revision_number(data) == expected


def test_next_revision_increments_by_one():
    assert rules.next_revision({}) == 1
    assert rules.next_revision({"revision_number": 4}) == 5


@pytest.mark.parametrize("status", [vocab.DRAFT, vocab.PUBLISHED])
def test_open_statuses_are_open(status):
    assert rules.is_open(status)
    assert not rules.is_closed(status)


@pytest.mark.parametrize("status", [REVISED, LOST, CANCELED])
def test_close_reasons_are_closed(status):
    assert rules.is_closed(status)
    assert not rules.is_open(status)


def test_pricing_is_live_for_a_draft_and_frozen_for_every_other_status():
    assert rules.pricing_mode(vocab.DRAFT) == "live"
    for status in (vocab.PUBLISHED, REVISED, LOST, CANCELED):
        assert rules.pricing_mode(status) == "frozen"


def test_close_reason_accepts_the_three_and_refuses_a_fourth():
    for reason in (LOST, CANCELED, REVISED):
        assert rules.normalise_close_reason(reason) == reason
    with pytest.raises(RevisionError) as caught:
        rules.normalise_close_reason("voided")
    assert "reason" in caught.value.errors


def test_close_fields_stamp_closed_on_for_all_three_reasons():
    for reason in (LOST, CANCELED, REVISED):
        fields = rules.close_fields(reason, today=TODAY, successor_id=None)
        assert fields[vocab.FIELD_CLOSED_ON] == TODAY
        assert fields["status"] == reason
        assert fields[vocab.FIELD_STATUS_REASON] == reason


def test_close_fields_stamp_creation_method_only_for_a_revision():
    revised = rules.close_fields(REVISED, today=TODAY, successor_id="q_2")
    assert revised[vocab.FIELD_QUOTE_CREATION_METHOD] == vocab.QUOTE_CREATION_METHOD_REVISION
    assert revised[vocab.FIELD_SUPERSEDED_BY] == "q_2"
    for reason in (LOST, CANCELED):
        fields = rules.close_fields(reason, today=TODAY, successor_id=None)
        assert vocab.FIELD_QUOTE_CREATION_METHOD not in fields


@pytest.mark.parametrize(
    "data",
    [
        {"quote_scope": "project_quote"},
        {"quote_kind": "project"},
        {"project_quote": True},
    ],
)
def test_a_project_quote_is_refused(data):
    assert rules.project_quote_reason(data) == vocab.REASON_PROJECT_QUOTE_UNSUPPORTED


def test_a_sales_quote_is_not_refused():
    assert rules.project_quote_reason({"quote_scope": "sales_quote"}) is None
    assert rules.project_quote_reason({"project_quote": False}) is None


@pytest.mark.parametrize("state", ["approved", "accepted", "paid"])
def test_recall_is_blocked_once_approved_accepted_or_paid(state):
    assert (
        rules.recall_block_reason({"hs_payment_status": state})
        == vocab.REASON_RECALL_BLOCKED_AFTER_APPROVAL
    )


@pytest.mark.parametrize("field", [vocab.ONLINE_PAYMENTS_FIELD, vocab.ESIGNATURE_FIELD])
def test_recall_is_blocked_when_the_quote_cannot_be_restored(field):
    assert rules.recall_block_reason({field: True}) == vocab.REASON_RECALL_BLOCKED_NO_RESTORE


def test_recall_is_allowed_when_nothing_blocks_it():
    assert rules.recall_block_reason({"hs_status": "published"}) is None


def test_cloned_line_copies_price_terms_and_discount():
    line = {
        "id": "line_1",
        "name": "Platform licence",
        "sku": "NWD-PLAT-ANN",
        "quantity": 2,
        "unit_price": 100.0,
        "discount_type": "percentage",
        "discount_value": 10.0,
        "tax_rate": 10.0,
    }
    copied = rules.cloned_line(line, 0)
    assert copied["unit_price"] == 100.0
    assert copied["discount_value"] == 10.0
    assert copied["discount_type"] == "percentage"
    assert copied["tax_rate"] == 10.0
    assert copied["source_line_item_id"] == "line_1"
    assert copied["reprice"]["mode"] == "copied"
    assert copied["amounts"]["total"] == 198.0


def test_revised_line_reprices_from_the_catalogue():
    line = {"id": "line_1", "name": "Platform licence", "quantity": 1, "unit_price": 100.0}
    repriced = rules.revised_line(line, 0, {"unit_price": 250.0})
    assert repriced["unit_price"] == 250.0
    assert repriced["price_source"] == "catalog"
    assert repriced["reprice"]["adjusted"] is True
    assert repriced["reprice"]["from"] == 100.0
    assert repriced["reprice"]["to"] == 250.0


def test_revised_line_keeps_the_price_without_a_catalogue():
    line = {"id": "line_1", "name": "Platform licence", "quantity": 1, "unit_price": 100.0}
    repriced = rules.revised_line(line, 0, None)
    assert repriced["unit_price"] == 100.0
    assert repriced["price_source"] == "manual"
    assert repriced["reprice"]["adjusted"] is False


def test_root_quote_id_defaults_to_the_record_itself():
    assert rules.root_quote_id({"id": "q_1", "data": {}}) == "q_1"
    assert rules.root_quote_id({"id": "q_2", "data": {"root_quote_id": "q_1"}}) == "q_1"


# --------------------------------------------------------------------------- #
# The engine: revise
# --------------------------------------------------------------------------- #


def test_revise_creates_a_new_record_and_leaves_the_prior_lines_untouched(store):
    engine = build_engine(store)
    quote = make_quote(store)
    line = make_line(store, quote["id"])
    before = dict(store.get(line["id"])["data"])

    result = engine.revise(quote["id"], {}, source="POST /api/wf-102/x", actor="dana")

    assert result["quote"]["id"] != quote["id"]
    assert store.get(line["id"])["data"] == before, "a revision must not edit the prior line"


def test_revise_sequence_is_zero_one_two(store):
    engine = build_engine(store)
    quote = make_quote(store)
    make_line(store, quote["id"])

    first = engine.revise(quote["id"], {}, source="s", actor="dana")
    second = engine.revise(first["quote"]["id"], {}, source="s", actor="dana")
    third = engine.revise(second["quote"]["id"], {}, source="s", actor="dana")

    assert quote["data"]["revision_number"] == 0
    assert first["new_revision_number"] == 1
    assert second["new_revision_number"] == 2
    assert third["new_revision_number"] == 3
    assert first["quote"]["revision_number"] == 1
    assert second["quote"]["revision_number"] == 2
    assert third["quote"]["revision_number"] == 3


def test_revise_stamps_the_prior_as_revised(store):
    engine = build_engine(store)
    quote = make_quote(store)
    make_line(store, quote["id"])

    result = engine.revise(quote["id"], {}, source="s", actor="dana")
    prior = store.get(quote["id"])["data"]

    assert prior["status"] == REVISED
    assert prior[vocab.FIELD_STATUS_REASON] == REVISED
    assert prior[vocab.FIELD_CLOSED_ON] == TODAY
    assert prior[vocab.FIELD_QUOTE_CREATION_METHOD] == vocab.QUOTE_CREATION_METHOD_REVISION
    assert prior[vocab.FIELD_SUPERSEDED_BY] == result["quote"]["id"]
    assert result["quote"]["status"] == vocab.DRAFT


def test_revise_writes_a_quoteclose_activity(store):
    engine = build_engine(store)
    quote = make_quote(store)
    make_line(store, quote["id"])

    engine.revise(quote["id"], {}, source="s", actor="dana")
    activity = engine.activity(quote["id"])

    assert len(activity) == 1
    assert activity[0]["action"] == vocab.ACTIVITY_QUOTE_CLOSE
    assert activity[0]["reason"] == REVISED
    assert activity[0]["revision_number"] == 0


def test_revise_copies_the_line_items_as_new_records(store):
    engine = build_engine(store)
    quote = make_quote(store)
    original = make_line(store, quote["id"])

    result = engine.revise(quote["id"], {}, source="s", actor="dana")
    new_lines = result["quote"]["line_items"]

    assert len(new_lines) == 1
    assert new_lines[0]["id"] != original["id"], "a quote line item gets its own record id"
    assert new_lines[0]["quote_id"] == result["quote"]["id"]
    assert new_lines[0]["source_line_item_id"] == original["id"]
    assert len(store.find(LINE, {"quote_id": quote["id"]})) == 1, "the prior line stays put"


def test_revise_carries_extra_fields_onto_the_new_draft(store):
    engine = build_engine(store)
    quote = make_quote(store, buyer_note="ring me on Friday")
    make_line(store, quote["id"])

    result = engine.revise(quote["id"], {}, source="s", actor="dana")

    assert result["quote"]["buyer_note"] == "ring me on Friday"


def test_revise_reprices_the_new_draft_from_the_catalogue(store):
    engine = build_engine(store)
    make_catalogue(store, unit_price=250.0)
    quote = make_quote(store)
    make_line(store, quote["id"], unit_price=100.0)

    result = engine.revise(quote["id"], {}, source="s", actor="dana")

    assert result["repriced_line_items"] == 1
    assert result["quote"]["line_items"][0]["unit_price"] == 250.0
    assert result["quote"]["line_items"][0]["price_source"] == "catalog"


def test_revise_refuses_an_already_closed_quote(store):
    engine = build_engine(store)
    quote = make_quote(store, status=REVISED)
    with pytest.raises(RevisionConflict) as caught:
        engine.revise(quote["id"], {}, source="s", actor="dana")
    assert caught.value.reason == vocab.REASON_QUOTE_ALREADY_CLOSED


def test_revise_refuses_a_project_quote(store):
    engine = build_engine(store)
    quote = make_quote(store, quote_scope="project_quote")
    make_line(store, quote["id"])
    with pytest.raises(RevisionConflict) as caught:
        engine.revise(quote["id"], {}, source="s", actor="dana")
    assert caught.value.reason == vocab.REASON_PROJECT_QUOTE_UNSUPPORTED


def test_revise_refuses_a_quote_with_no_lines(store):
    engine = build_engine(store)
    quote = make_quote(store)
    with pytest.raises(RevisionConflict) as caught:
        engine.revise(quote["id"], {}, source="s", actor="dana")
    assert caught.value.reason == vocab.REASON_QUOTE_IS_EMPTY


def test_revise_an_unknown_quote_is_not_found(store):
    engine = build_engine(store)
    with pytest.raises(RevisionNotFound):
        engine.revise("q_absent", {}, source="s", actor="dana")


def test_lineage_walks_the_whole_chain(store):
    engine = build_engine(store)
    quote = make_quote(store)
    make_line(store, quote["id"])
    second = engine.revise(quote["id"], {}, source="s", actor="dana")["quote"]["id"]
    engine.revise(second, {}, source="s", actor="dana")

    lineage = engine.lineage(quote["id"])

    assert lineage["count"] == 3
    assert [step["revision_number"] for step in lineage["revisions"]] == [0, 1, 2]
    assert [step["status"] for step in lineage["revisions"]] == [REVISED, REVISED, vocab.DRAFT]
    assert lineage["revisions"][0]["closed_on"] == TODAY
    assert lineage["revisions"][2]["closed_on"] is None


# --------------------------------------------------------------------------- #
# The engine: close, clone, reprice, recall, re-publish
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("reason", [LOST, CANCELED, REVISED])
def test_close_marks_each_reason_and_writes_one_activity(store, reason):
    engine = build_engine(store)
    quote = make_quote(store, status=vocab.PUBLISHED)

    result = engine.close(quote["id"], {"reason": reason}, source="s", actor="dana")

    assert result["quote"]["status"] == reason
    assert result["quote"][vocab.FIELD_CLOSED_ON] == TODAY
    activity = engine.activity(quote["id"])
    assert [entry["action"] for entry in activity] == [vocab.ACTIVITY_QUOTE_CLOSE]
    assert activity[0]["reason"] == reason


def test_close_refuses_an_unknown_reason(store):
    engine = build_engine(store)
    quote = make_quote(store)
    with pytest.raises(RevisionError):
        engine.close(quote["id"], {"reason": "voided"}, source="s", actor="dana")


def test_close_refuses_an_already_closed_quote(store):
    engine = build_engine(store)
    quote = make_quote(store, status=LOST)
    with pytest.raises(RevisionConflict):
        engine.close(quote["id"], {"reason": LOST}, source="s", actor="dana")


def test_clone_leaves_the_prior_open_and_copies_the_price(store):
    engine = build_engine(store)
    make_catalogue(store, unit_price=250.0)
    quote = make_quote(store)
    make_line(store, quote["id"], unit_price=100.0)

    result = engine.clone(quote["id"], {}, source="s", actor="dana")

    assert store.get(quote["id"])["data"]["status"] == vocab.DRAFT, "a clone closes nothing"
    clone = result["quote"]
    assert clone["id"] != quote["id"]
    assert clone["revision_number"] == 0, "a clone is a new quote, not a revision"
    assert clone["cloned_from_quote_id"] == quote["id"]
    assert clone["line_items"][0]["unit_price"] == 100.0, "a clone does not reprice"
    assert clone["line_items"][0]["reprice"]["mode"] == "copied"


def test_clone_refuses_a_project_quote(store):
    engine = build_engine(store)
    quote = make_quote(store, quote_scope="project_quote")
    with pytest.raises(RevisionConflict):
        engine.clone(quote["id"], {}, source="s", actor="dana")


def test_reprice_adjusts_a_draft_to_the_price_list(store):
    engine = build_engine(store)
    make_catalogue(store, unit_price=250.0)
    quote = make_quote(store)
    make_line(store, quote["id"], unit_price=100.0)

    result = engine.reprice(quote["id"], source="s", actor="dana")

    assert result["adjusted"] is True
    assert result["adjusted_lines"] == 1
    assert result["quote"]["line_items"][0]["unit_price"] == 250.0


def test_reprice_leaves_a_published_quote_frozen(store):
    engine = build_engine(store)
    make_catalogue(store, unit_price=250.0)
    quote = make_quote(store, status=vocab.PUBLISHED)
    line = make_line(store, quote["id"], unit_price=100.0)

    result = engine.reprice(quote["id"], source="s", actor="dana")

    assert result["adjusted"] is False
    assert result["frozen"] is True
    assert result["reason"] == vocab.REASON_QUOTE_FROZEN
    assert store.get(line["id"])["data"]["unit_price"] == 100.0


def test_reprice_leaves_a_closed_quote_frozen(store):
    engine = build_engine(store)
    make_catalogue(store, unit_price=250.0)
    quote = make_quote(store, status=LOST)
    make_line(store, quote["id"], unit_price=100.0)

    assert engine.reprice(quote["id"], source="s", actor="dana")["frozen"] is True


def test_recall_returns_a_published_quote_to_draft(store):
    engine = build_engine(store)
    quote = make_quote(store, status=vocab.PUBLISHED)

    result = engine.recall(quote["id"], source="s", actor="dana")

    assert result["quote"]["status"] == vocab.DRAFT
    activity = engine.activity(quote["id"])
    assert [entry["action"] for entry in activity] == [vocab.ACTIVITY_QUOTE_RECALLED]


def test_recall_refuses_once_approved_accepted_or_paid(store):
    engine = build_engine(store)
    quote = make_quote(store, status=vocab.PUBLISHED, hs_payment_status="paid")
    with pytest.raises(RevisionConflict) as caught:
        engine.recall(quote["id"], source="s", actor="dana")
    assert caught.value.reason == vocab.REASON_RECALL_BLOCKED_AFTER_APPROVAL


def test_recall_refuses_when_the_quote_cannot_be_restored(store):
    engine = build_engine(store)
    quote = make_quote(store, status=vocab.PUBLISHED, esignature_enabled=True)
    with pytest.raises(RevisionConflict) as caught:
        engine.recall(quote["id"], source="s", actor="dana")
    assert caught.value.reason == vocab.REASON_RECALL_BLOCKED_NO_RESTORE


def test_recall_refuses_a_draft(store):
    engine = build_engine(store)
    quote = make_quote(store)
    with pytest.raises(RevisionConflict) as caught:
        engine.recall(quote["id"], source="s", actor="dana")
    assert caught.value.reason == vocab.REASON_QUOTE_NOT_PUBLISHED


def test_republish_generates_a_fresh_artefact_and_activity(store):
    engine = build_engine(store)
    quote = make_quote(store, status=vocab.PUBLISHED)
    engine.recall(quote["id"], source="s", actor="dana")

    result = engine.republish(quote["id"], source="s", actor="dana")

    assert result["quote"]["status"] == vocab.PUBLISHED
    assert {artefact["kind"] for artefact in result["artefacts"]} == {"link", "pdf"}
    actions = [entry["action"] for entry in engine.activity(quote["id"])]
    assert vocab.ACTIVITY_QUOTE_REPUBLISHED in actions


def test_republish_requires_a_draft(store):
    engine = build_engine(store)
    quote = make_quote(store, status=vocab.PUBLISHED)
    with pytest.raises(RevisionConflict) as caught:
        engine.republish(quote["id"], source="s", actor="dana")
    assert caught.value.reason == vocab.REASON_REPUBLISH_NEEDS_A_RECALL


def test_republish_a_second_time_generates_a_new_link(store):
    engine = build_engine(store)
    quote = make_quote(store, status=vocab.PUBLISHED)
    engine.recall(quote["id"], source="s", actor="dana")
    first = engine.republish(quote["id"], source="s", actor="dana")
    engine.recall(quote["id"], source="s", actor="dana")
    second = engine.republish(quote["id"], source="s", actor="dana")

    assert first["artefacts"][0]["url"] != second["artefacts"][0]["url"]


# --------------------------------------------------------------------------- #
# Summary and vocabulary
# --------------------------------------------------------------------------- #


def test_summary_counts_the_states(store):
    engine = build_engine(store)
    quote = make_quote(store)
    make_line(store, quote["id"])
    engine.revise(quote["id"], {}, source="s", actor="dana")
    engine.close(
        make_quote(store, status=vocab.PUBLISHED)["id"], {"reason": LOST}, source="s", actor="dana"
    )

    summary = engine.summary("room_1")

    assert summary["revised"] == 1
    assert summary["lost"] == 1
    assert summary["closed"] == 2
    assert summary["activities"] == 2


def test_vocabulary_serves_the_three_reasons_and_the_option_values():
    served = QuoteRevisionEngine.vocabulary()
    assert served["ticket"] == "WF-102"
    assert served["close_reasons"] == [LOST, CANCELED, REVISED]
    assert served["quote_creation_methods"]["776160001"] == "Revision"
    assert served["collections"]["quote"] == "wf086_quote"


# --------------------------------------------------------------------------- #
# The architectural guards
# --------------------------------------------------------------------------- #

#: A domain module may import the store, its own package, and the WF-086 helpers
#: issue 196 says to reuse. Anything else under ``dsr`` is a defect: a second
#: workflow's engine, or the app itself.
ALLOWED_PREFIXES = ("dsr.store", "dsr.quote_revision", "dsr.quote_authoring")


def _imported_dsr_names(path: pathlib.Path) -> list[str]:
    names: list[str] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    return [name for name in names if name.startswith("dsr")]


class TestArchitecture:
    def test_the_domain_package_imports_nothing_but_the_store_and_its_reuse(self):
        for path in sorted(DOMAIN_DIR.glob("*.py")):
            for name in _imported_dsr_names(path):
                assert name == "dsr.store" or name.startswith(ALLOWED_PREFIXES), (
                    f"{path.name} imports {name}"
                )

    def test_the_domain_package_never_imports_the_app(self):
        for path in sorted(DOMAIN_DIR.glob("*.py")):
            text = path.read_text(encoding="utf-8")
            assert "from dsr.api" not in text and "import dsr.api" not in text

    def test_the_domain_package_never_opens_sqlite(self):
        for path in sorted(DOMAIN_DIR.glob("*.py")):
            assert "import sqlite3" not in path.read_text(encoding="utf-8")

    def test_the_feature_module_never_imports_the_app(self):
        module = importlib.import_module(FEATURE_MODULE)
        text = pathlib.Path(module.__file__).read_text(encoding="utf-8")
        assert "from dsr.api" not in text and "import dsr.api" not in text

    def test_the_prefix_is_the_ticket_slug(self):
        module = importlib.import_module(FEATURE_MODULE)
        assert module.router.prefix == "/api/wf-102"

    def test_the_feature_exports_a_descriptor(self):
        module = importlib.import_module(FEATURE_MODULE)
        assert module.FEATURE["id"] == "wf-102-quote-revision"
        assert module.FEATURE["ticket"] == "WF-102"


# --------------------------------------------------------------------------- #
# The seed
# --------------------------------------------------------------------------- #


def test_seed_returns_a_cp1252_string_and_creates_the_states(db):
    module = importlib.import_module(FEATURE_MODULE)
    db.create("room", {"name": "Northwind"}, record_id="room_seed", actor="dana", source="seed")

    summary = module.seed(db, {"room_ids": ["room_seed"], "now": FIXED, "rng": None})

    assert summary
    summary.encode("cp1252")  # the Windows console cannot print an arrow
    assert "revised twice" in summary
    store_rows = db.list(QUOTE, limit=100)
    assert len(store_rows) >= 5
    assert any(row["data"].get("status") == LOST for row in store_rows)
    assert any(row["data"].get("status") == CANCELED for row in store_rows)
    assert db.list("wf102_quote_artefact", limit=10)
