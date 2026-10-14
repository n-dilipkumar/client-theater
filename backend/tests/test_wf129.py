"""WF-129 domain tests: the rules, the engine, the audit trail and the seed.

The file builds its own records and its own clock, so it passes alone and under
pytest-xdist without depending on another module's fixture. The clock is frozen
at :data:`FROZEN`, because signing and filing stamp a date and a rule that reads
"today" must never be tested against a date that moves.
"""

from __future__ import annotations

import importlib
import pathlib
from datetime import datetime, timezone

import pytest
from dsr.inroom_ordering import OrderFormEngine, rules, vocabulary as vocab
from dsr.inroom_ordering.errors import (
    OrderFormConflict,
    OrderFormError,
    OrderFormNotFound,
)
from dsr.store import RecordStore

FROZEN = datetime(2026, 10, 14, 9, 0, tzinfo=timezone.utc)
FEATURE_MODULE = "dsr.features.wf129_quote_to_order_form_inside_the_room"
SOURCE = "POST /api/wf-129/order-forms"


# --------------------------------------------------------------------------- #
# Fixtures and helpers
# --------------------------------------------------------------------------- #


@pytest.fixture
def engine(db) -> OrderFormEngine:
    return OrderFormEngine(RecordStore(db), now=lambda: FROZEN)


@pytest.fixture
def store(db) -> RecordStore:
    return RecordStore(db)


def make_quote(store: RecordStore, *, lines: int = 2, **overrides) -> str:
    """A quote with ``lines`` line items, in room_1, priced and taxed."""
    payload = {
        "title": "Northwind annual order",
        "status": "draft",
        "currency": "USD",
        "account": "Northwind Traders",
        "company_name": "Northwind Traders",
        "owner": "dana",
        "deal_id": "deal_1",
    }
    payload.update(overrides)
    quote = store.create(
        vocab.QUOTE_COLLECTION, payload, room_id="room_1", actor="dana", source="test"
    )
    catalogue = [
        ("Northwind Platform, annual", "NWD-PLAT-ANN", 10, 4800.0, "recurring", 10.0),
        ("Onboarding workshop", "NWD-IMP-ONB", 2, 1500.0, "one_time", 0.0),
    ]
    for position, (name, sku, qty, price, billing, tax) in enumerate(catalogue[:lines]):
        store.create(
            vocab.QUOTE_LINE_ITEM_COLLECTION,
            {
                "quote_id": quote["id"],
                "position": position,
                "name": name,
                "sku": sku,
                "quantity": qty,
                "unit_price": price,
                "billing_frequency": billing,
                "tax_rate": tax,
            },
            room_id="room_1",
            actor="dana",
            source="test",
        )
    return quote["id"]


def make_deal(store: RecordStore, **overrides) -> str:
    payload = {
        "name": "Northwind Traders renewal",
        "account": "Northwind Traders",
        "currency": "USD",
        "amount": 0.0,
        "stage": "negotiation",
    }
    payload.update(overrides)
    deal = store.create(
        vocab.DEAL_COLLECTION, payload, room_id="room_1", actor="dana", source="test"
    )
    return deal["id"]


def new_form(engine: OrderFormEngine, store: RecordStore, **overrides) -> dict:
    quote_id = make_quote(store)
    payload = {"quote_id": quote_id}
    payload.update(overrides)
    return engine.create_order_form("room_1", payload, source=SOURCE, actor="rep")


def accepted_form(engine: OrderFormEngine, store: RecordStore, **overrides) -> dict:
    result = new_form(engine, store, **overrides)
    form_id = result["order_form"]["id"]
    engine.accept(form_id, {"accepted_by": "Ada Byron"}, source=SOURCE, actor="buyer")
    return engine.read_order_form(form_id)


SIGNER = {"name": "Ada Byron", "email": "ada@northwind.example"}


def sign_form(engine: OrderFormEngine, form_id: str, **extra) -> dict:
    payload = {"mode": "typed", "text": "Ada Byron", "signer": SIGNER}
    payload.update(extra)
    return engine.sign(form_id, payload, source=SOURCE, actor="buyer")


# --------------------------------------------------------------------------- #
# Coercion
# --------------------------------------------------------------------------- #


def test_coercion_helpers_accept_the_spellings_a_record_may_carry():
    assert rules.as_bool("yes") is True
    assert rules.as_bool("off") is False
    assert rules.as_bool(None, True) is True
    assert rules.normalise_billing("recurring") == "recurring"
    assert rules.normalise_billing("Monthly") == "recurring"
    assert rules.normalise_billing("one-time") == "one_time"
    assert rules.normalise_billing("nonsense") == "one_time"
    assert rules.normalise_signature_mode("typed-photo") == "typed_photo"
    assert rules.normalise_signature_mode("draw") == "drawn"
    assert rules.normalise_signature_mode("telepathy") == "telepathy"


# --------------------------------------------------------------------------- #
# The arithmetic
# --------------------------------------------------------------------------- #


def test_line_amounts_apply_a_percentage_discount_before_tax():
    amounts = rules.line_amounts(
        {
            "quantity": 10,
            "unit_price": 100.0,
            "discount_type": "percentage",
            "discount_value": 10,
            "tax_rate": 10,
        }
    )
    assert amounts["subtotal"] == 1000.0
    assert amounts["discount"] == 100.0
    assert amounts["net"] == 900.0
    assert amounts["tax"] == 90.0
    assert amounts["total"] == 990.0


def test_a_currency_discount_never_takes_the_line_below_zero():
    amounts = rules.line_amounts(
        {"quantity": 1, "unit_price": 50.0, "discount_type": "currency", "discount_value": 80.0}
    )
    assert amounts["discount"] == 50.0
    assert amounts["net"] == 0.0
    assert amounts["total"] == 0.0


def test_totals_split_by_billing_frequency():
    totals = rules.totals_for(
        [
            {"name": "A", "quantity": 1, "unit_price": 100.0, "billing_frequency": "recurring"},
            {"name": "B", "quantity": 2, "unit_price": 50.0, "billing_frequency": "one_time"},
        ]
    )
    assert totals["subtotal"] == 200.0
    assert totals["total"] == 200.0
    assert totals["line_count"] == 2
    assert totals["by_billing_frequency"] == {"one_time": 100.0, "recurring": 100.0}


def test_the_totals_contract_names_its_rounding():
    totals = rules.totals_for([])
    assert totals["total"] == 0.0
    assert "two decimals" in totals["rounding"]


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #


def test_validate_line_names_every_field_that_is_wrong():
    _clean, errors = rules.validate_line(
        {"name": "", "quantity": -1, "unit_price": -1, "tax_rate": 200, "discount_value": 150}
    )
    assert set(errors) == {"name", "quantity", "unit_price", "tax_rate", "discount_value"}


def test_validate_line_defaults_a_missing_billing_frequency():
    clean, errors = rules.validate_line({"name": "X", "quantity": 1, "unit_price": 1})
    assert errors == {}
    assert clean["billing_frequency"] == "one_time"
    assert clean["amounts"]["total"] == 1.0


# --------------------------------------------------------------------------- #
# Signatures: the derived mode selector
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "mode,payload,ok",
    [
        ("typed", {"text": "Ada"}, True),
        ("typed", {}, False),
        ("drawn", {"strokes": [[0, 0], [1, 1]]}, True),
        ("drawn", {"strokes": []}, False),
        ("typed_photo", {"text": "Ada", "image": "scan.png"}, True),
        ("typed_photo", {"text": "Ada"}, False),
        ("typed_photo", {"image": "scan.png"}, False),
        ("telepathy", {"text": "Ada"}, False),
    ],
)
def test_the_mode_is_valid_only_with_its_own_artefact(mode, payload, ok):
    _normalised, errors = rules.validate_signature(mode, payload)
    assert (errors == {}) is ok


def test_typed_photo_is_the_researched_spelling_of_typed_photo():
    normalised, errors = rules.validate_signature("typed-photo", {"text": "A", "photo": "p.png"})
    assert normalised == "typed_photo"
    assert errors == {}


# --------------------------------------------------------------------------- #
# Redlining
# --------------------------------------------------------------------------- #


def test_one_open_revision_per_line_conflicts_with_a_second():
    open_rows = [
        {"line_id": "l1", "state": "proposed"},
        {"line_id": "l2", "state": "accepted"},
    ]
    assert rules.revision_conflict(open_rows, "l1") is True
    assert rules.revision_conflict(open_rows, "l2") is False
    assert rules.revision_conflict(open_rows, "l3") is False


def test_apply_revision_revalidates_the_whole_line():
    clean, errors = rules.apply_revision(
        {"name": "X", "quantity": 1, "unit_price": 5.0}, "quantity", 3
    )
    assert errors == {}
    assert clean["quantity"] == 3.0
    assert clean["amounts"]["subtotal"] == 15.0


def test_apply_revision_reports_the_field_a_bad_value_makes_invalid():
    clean, errors = rules.apply_revision(
        {"name": "X", "quantity": 1, "unit_price": 5.0}, "quantity", -3
    )
    assert "quantity" in errors
    assert clean["quantity"] == -3.0


def test_only_the_named_fields_can_be_redlined():
    assert rules.revision_is_known_field("quantity") is True
    assert rules.revision_is_known_field("amounts") is False


# --------------------------------------------------------------------------- #
# The document, its hash and the certificate
# --------------------------------------------------------------------------- #


def test_the_document_hash_is_canonical_and_content_addressed():
    form = {"title": "T", "quote_id": "q", "room_id": "r", "currency": "USD", "version": 1}
    lines = [{"name": "A", "quantity": 1, "unit_price": 10.0, "billing_frequency": "one_time"}]
    totals = rules.totals_for(lines)
    model = rules.document_model(form, lines, totals)
    first = rules.document_hash(model)
    shuffled = {key: model[key] for key in reversed(list(model))}
    assert rules.document_hash(shuffled) == first
    changed = dict(model)
    changed["title"] = "Different"
    assert rules.document_hash(changed) != first


def test_the_certificate_names_the_document_the_signer_and_the_no_provider_statement():
    form = {"id": "f1", "quote_id": "q1", "room_id": "room_1", "currency": "USD", "version": 2}
    signature = {"signer": SIGNER, "mode": "typed", "signed_at": "2026-10-14T09:00:00"}
    certificate = rules.certificate_contents(form, signature, "deadbeef")
    assert certificate["document_sha256"] == "deadbeef"
    assert certificate["document_version"] == 2
    assert certificate["signer"]["email"] == SIGNER["email"]
    assert certificate["verified_by_provider"] is False
    assert "No third-party" in certificate["statement"]


# --------------------------------------------------------------------------- #
# Filing: the derived amount and close-date rules
# --------------------------------------------------------------------------- #


def test_the_signed_document_total_is_what_is_filed():
    writeback = rules.deal_writeback(
        {"id": "f1"}, {"total": 1980.0}, "2026-10-14", {"close_date": "2099-12-31"}
    )
    assert writeback["amount"] == 1980.0
    assert writeback["amount_source"] == "signed_order_form_total"


def test_the_close_date_advances_to_the_signature_and_never_backwards():
    later = rules.deal_writeback(
        {"id": "f1"}, {"total": 1.0}, "2026-10-14", {"close_date": "2099-12-31"}
    )
    assert later["close_date"] == "2026-10-14"
    earlier = rules.deal_writeback(
        {"id": "f1"}, {"total": 1.0}, "2026-10-14", {"close_date": "2026-01-01"}
    )
    assert earlier["close_date"] == "2026-01-01"
    none = rules.deal_writeback({"id": "f1"}, {"total": 1.0}, "2026-10-14", None)
    assert none["close_date"] == "2026-10-14"


# --------------------------------------------------------------------------- #
# Creating the order form
# --------------------------------------------------------------------------- #


def test_creating_a_form_clones_quote_lines_with_their_own_ids(engine, store):
    quote_id = make_quote(store)
    result = engine.create_order_form("room_1", {"quote_id": quote_id}, source=SOURCE, actor="rep")
    form = result["order_form"]
    assert result["cloned_line_items"] == 2
    assert form["status"] == vocab.STATUS_DRAFT
    assert form["buyer_editable"] is True
    assert form["signed_in_room"] is True
    assert form["line_count"] == 2
    quote_line_ids = {
        row["id"] for row in store.find(vocab.QUOTE_LINE_ITEM_COLLECTION, {"quote_id": quote_id})
    }
    assert {line["id"] for line in form["lines"]}.isdisjoint(quote_line_ids)
    assert {line["source_quote_line_item_id"] for line in form["lines"]} == quote_line_ids


def test_a_form_needs_a_quote(engine):
    with pytest.raises(OrderFormError):
        engine.create_order_form("room_1", {}, source=SOURCE, actor="rep")


def test_a_form_falls_back_to_the_quotes_room(engine, store):
    quote_id = make_quote(store)
    # The quote is in room_1, so an explicit empty room still resolves to it.
    result = engine.create_order_form(None, {"quote_id": quote_id}, source=SOURCE, actor="rep")
    assert result["order_form"]["room_id"] == "room_1"


def test_a_form_for_an_absent_quote_is_not_found(engine):
    with pytest.raises(OrderFormNotFound):
        engine.create_order_form("room_1", {"quote_id": "q_absent"}, source=SOURCE, actor="rep")


# --------------------------------------------------------------------------- #
# Editing and redlining through the engine
# --------------------------------------------------------------------------- #


def test_a_draft_line_can_be_edited_and_the_totals_follow(engine, store):
    result = new_form(engine, store)
    form = result["order_form"]
    line = form["lines"][0]
    updated = engine.update_line(
        form["id"], line["id"], {"quantity": 20}, source=SOURCE, actor="buyer"
    )
    assert updated["order_form"]["lines"][0]["quantity"] == 20.0
    assert updated["order_form"]["totals"]["total"] > form["totals"]["total"]


def test_a_frozen_line_cannot_be_edited(engine, store):
    form = accepted_form(engine, store)
    line = form["lines"][0]
    with pytest.raises(OrderFormConflict) as caught:
        engine.update_line(form["id"], line["id"], {"quantity": 1}, source=SOURCE, actor="buyer")
    assert caught.value.reason == "form_not_editable"


def test_an_accepted_redline_bumps_the_version_and_the_history(engine, store):
    result = new_form(engine, store)
    form = result["order_form"]
    line = form["lines"][0]
    proposal = engine.propose_revision(
        form["id"],
        {"line_id": line["id"], "field": "unit_price", "proposed_value": 4000.0},
        source=SOURCE,
        actor="buyer",
    )
    assert proposal["revision"]["state"] == vocab.REVISION_PROPOSED
    resolved = engine.resolve_revision(
        proposal["revision"]["id"], {"decision": "accept"}, source=SOURCE, actor="dana"
    )
    assert resolved["revision"]["state"] == vocab.REVISION_ACCEPTED
    assert resolved["order_form"]["version"] == 2
    assert resolved["order_form"]["history"][0]["field"] == "unit_price"
    assert resolved["order_form"]["lines"][0]["unit_price"] == 4000.0


def test_a_rejected_redline_leaves_the_line_alone(engine, store):
    result = new_form(engine, store)
    form = result["order_form"]
    line = form["lines"][0]
    proposal = engine.propose_revision(
        form["id"],
        {"line_id": line["id"], "field": "quantity", "proposed_value": 1},
        source=SOURCE,
        actor="buyer",
    )
    resolved = engine.resolve_revision(
        proposal["revision"]["id"], {"decision": "reject"}, source=SOURCE, actor="dana"
    )
    assert resolved["revision"]["state"] == vocab.REVISION_REJECTED
    assert resolved["order_form"]["version"] == 1
    assert resolved["order_form"]["lines"][0]["quantity"] == float(line["quantity"])


def test_a_second_open_redline_on_one_line_conflicts(engine, store):
    result = new_form(engine, store)
    form = result["order_form"]
    line = form["lines"][0]
    engine.propose_revision(
        form["id"],
        {"line_id": line["id"], "field": "quantity", "proposed_value": 1},
        source=SOURCE,
        actor="buyer",
    )
    with pytest.raises(OrderFormConflict) as caught:
        engine.propose_revision(
            form["id"],
            {"line_id": line["id"], "field": "unit_price", "proposed_value": 1},
            source=SOURCE,
            actor="seller",
        )
    assert caught.value.reason == "revision_conflict"


def test_a_redline_with_no_value_or_unknown_field_is_refused(engine, store):
    result = new_form(engine, store)
    form = result["order_form"]
    line = form["lines"][0]
    with pytest.raises(OrderFormError):
        engine.propose_revision(
            form["id"], {"line_id": line["id"], "field": "quantity"}, source=SOURCE, actor="buyer"
        )
    with pytest.raises(OrderFormError):
        engine.propose_revision(
            form["id"],
            {"line_id": line["id"], "field": "amounts", "proposed_value": 1},
            source=SOURCE,
            actor="buyer",
        )


def test_resolving_an_already_resolved_redline_is_a_conflict(engine, store):
    result = new_form(engine, store)
    form = result["order_form"]
    line = form["lines"][0]
    proposal = engine.propose_revision(
        form["id"],
        {"line_id": line["id"], "field": "quantity", "proposed_value": 3},
        source=SOURCE,
        actor="buyer",
    )
    engine.resolve_revision(
        proposal["revision"]["id"], {"decision": "reject"}, source=SOURCE, actor="dana"
    )
    with pytest.raises(OrderFormConflict):
        engine.resolve_revision(
            proposal["revision"]["id"], {"decision": "accept"}, source=SOURCE, actor="dana"
        )


# --------------------------------------------------------------------------- #
# Accepting, signing and filing
# --------------------------------------------------------------------------- #


def test_accepting_a_draft_moves_it_to_accepted_and_freezes_it(engine, store):
    result = new_form(engine, store)
    accepted = engine.accept(
        result["order_form"]["id"], {"accepted_by": "Ada"}, source=SOURCE, actor="buyer"
    )
    assert accepted["order_form"]["status"] == vocab.STATUS_ACCEPTED
    assert accepted["order_form"]["buyer_editable"] is False


def test_signing_requires_an_accepted_form(engine, store):
    result = new_form(engine, store)
    with pytest.raises(OrderFormConflict) as caught:
        sign_form(engine, result["order_form"]["id"])
    assert caught.value.reason == "form_not_awaiting_signature"


def test_signing_writes_the_signature_the_artefact_and_the_certificate(engine, store):
    form = accepted_form(engine, store)
    result = sign_form(engine, form["id"])
    assert result["order_form"]["status"] == vocab.STATUS_SIGNED
    assert result["order_form"]["document_sha256"]
    assert result["certificate"]["document_sha256"] == result["order_form"]["document_sha256"]
    assert result["artefact"]["format"] == "json"
    assert result["artefact"]["render_formats_unavailable"] == ["pdf", "docx"]
    assert result["certificate"]["verified_by_provider"] is False


def test_signing_needs_a_signer(engine, store):
    form = accepted_form(engine, store)
    with pytest.raises(OrderFormError):
        engine.sign(form["id"], {"mode": "typed", "text": "Ada"}, source=SOURCE, actor="buyer")


def test_signing_with_an_unknown_mode_is_refused(engine, store):
    form = accepted_form(engine, store)
    with pytest.raises(OrderFormError):
        sign_form(engine, form["id"], mode="telepathy")


def test_signing_with_a_missing_artefact_is_refused(engine, store):
    form = accepted_form(engine, store)
    with pytest.raises(OrderFormError) as caught:
        sign_form(engine, form["id"], mode="drawn")
    assert "strokes" in caught.value.errors


def test_a_signed_form_cannot_be_signed_twice(engine, store):
    form = accepted_form(engine, store)
    sign_form(engine, form["id"])
    with pytest.raises(OrderFormConflict) as caught:
        sign_form(engine, form["id"])
    assert caught.value.reason == "form_already_signed"


def test_filing_requires_a_signature(engine, store):
    form = accepted_form(engine, store)
    with pytest.raises(OrderFormConflict) as caught:
        engine.file_back(form["id"], source=SOURCE, actor="rep")
    assert caught.value.reason == "form_not_signed"


def test_filing_writes_the_total_and_advances_the_close_date(engine, store):
    deal_id = make_deal(store, close_date="2099-12-31")
    result = new_form(engine, store, deal_id=deal_id)
    form_id = result["order_form"]["id"]
    engine.accept(form_id, {"accepted_by": "Ada"}, source=SOURCE, actor="buyer")
    sign_form(engine, form_id)
    filed = engine.file_back(form_id, source=SOURCE, actor="rep")
    assert filed["deal_filed"] is True
    assert filed["deal"]["amount"] == filed["order_form"]["totals"]["total"]
    assert filed["deal"]["close_date"] == "2026-10-14"
    assert filed["map_advanced"] is False
    assert filed["payment_in_scope"] is False
    deal = store.get(deal_id)
    assert deal["data"]["amount"] == filed["order_form"]["totals"]["total"]


def test_filing_without_a_deal_records_the_gap_rather_than_failing(engine, store):
    form = accepted_form(engine, store)
    sign_form(engine, form["id"])
    filed = engine.file_back(form["id"], source=SOURCE, actor="rep")
    assert filed["deal_filed"] is False
    assert filed["order_form"]["status"] == vocab.STATUS_FILED


def test_a_filed_form_cannot_be_filed_twice(engine, store):
    form = accepted_form(engine, store)
    sign_form(engine, form["id"])
    engine.file_back(form["id"], source=SOURCE, actor="rep")
    with pytest.raises(OrderFormConflict) as caught:
        engine.file_back(form["id"], source=SOURCE, actor="rep")
    assert caught.value.reason == "form_already_filed"


# --------------------------------------------------------------------------- #
# Reads and the board
# --------------------------------------------------------------------------- #


def test_the_summary_counts_the_states(engine, store):
    result = new_form(engine, store)
    form_id = result["order_form"]["id"]
    engine.accept(form_id, {"accepted_by": "Ada"}, source=SOURCE, actor="buyer")
    sign_form(engine, form_id)
    summary = engine.summary("room_1")
    assert summary["order_forms"] == 1
    assert summary["by_status"][vocab.STATUS_SIGNED] == 1
    assert summary["signed"] == 1
    assert summary["map_advanced"] is False
    assert summary["render_formats_unavailable"] == ["pdf", "docx"]


def test_reading_an_absent_form_is_not_found(engine):
    with pytest.raises(OrderFormNotFound):
        engine.read_order_form("form_absent")


def test_the_certificate_is_readable_after_signing(engine, store):
    form = accepted_form(engine, store)
    assert engine.certificate_for(form["id"]) is None
    sign_form(engine, form["id"])
    certificate = engine.certificate_for(form["id"])
    assert certificate is not None
    assert certificate["signer"]["email"] == SIGNER["email"]


# --------------------------------------------------------------------------- #
# Vocabulary and decisions
# --------------------------------------------------------------------------- #


def test_the_vocabulary_reuses_the_quote_and_price_book_collections():
    served = vocab.vocabulary()
    assert served["collections"]["quote"] == "wf086_quote"
    assert served["collections"]["quote_line_item"] == "wf086_line_item"
    assert served["collections"]["product"] == "wf087_product"
    assert served["collections"]["deal"] == "crm_deal"


def test_the_vocabulary_records_the_four_things_it_does_not_build():
    items = {entry["item"] for entry in vocab.vocabulary()["not_implemented"]}
    assert items == {
        "E-signature provider",
        "PDF/DOCX rendering",
        "Mutual action plan",
        "Payment in flow",
    }


def test_every_decision_names_a_chosen_option_and_a_rejected_one():
    picks = OrderFormEngine.decisions()
    assert len(picks) >= 6
    for pick in picks:
        assert pick["chosen"] in pick["options"]
        assert pick["rejected"] in pick["options"]
        assert len(pick["options"]) >= 2
        assert pick["reason"]


# --------------------------------------------------------------------------- #
# Architectural guards
# --------------------------------------------------------------------------- #


def test_the_domain_package_imports_nothing_but_the_store():
    package = pathlib.Path(__file__).resolve().parents[1] / "dsr" / "inroom_ordering"
    for path in package.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "from dsr.api" not in text, path.name
        assert "import dsr.api" not in text, path.name
        assert "import sqlite3" not in text, path.name


def test_the_feature_module_does_not_import_the_app_or_sqlite():
    module = importlib.import_module(FEATURE_MODULE)
    assert module.FEATURE["id"] == "wf-129-inroom-order-form"
    assert module.FEATURE["ticket"] == "WF-129"
    assert module.router.prefix == "/api/wf-129"
    feature_path = (
        pathlib.Path(__file__).resolve().parents[1]
        / "dsr"
        / "features"
        / "wf129_quote_to_order_form_inside_the_room.py"
    )
    text = feature_path.read_text(encoding="utf-8")
    assert "from dsr.api" not in text
    assert "import dsr.api" not in text
    assert "import sqlite3" not in text


def test_every_write_names_the_route_that_served_it(engine, store):
    result = new_form(engine, store)
    form_id = result["order_form"]["id"]
    rows = store.audit(record_id=form_id)
    assert any(row["source"] == SOURCE for row in rows)


# --------------------------------------------------------------------------- #
# The seed
# --------------------------------------------------------------------------- #


def test_the_seed_creates_the_named_states_and_returns_a_cp1252_string(db):
    module = importlib.import_module(FEATURE_MODULE)
    context = {"room_ids": [("room_seed", "Northwind")], "now": FROZEN}
    message = module.seed(db, context)
    assert message
    # The seeder prints this on a Windows console; cp1252 cannot encode an arrow.
    assert message.encode("cp1252")
    assert "MAP not advanced" in message
    assert "payment links out of scope" in message
    print(f"wf-129 seed: {message}")
    engine = OrderFormEngine(RecordStore(db), now=lambda: FROZEN)
    summary = engine.summary("room_seed")
    assert summary["order_forms"] == 4
    assert summary["by_status"][vocab.STATUS_DRAFT] == 1
    assert summary["by_status"][vocab.STATUS_ACCEPTED] == 1
    assert summary["by_status"][vocab.STATUS_SIGNED] == 1
    assert summary["by_status"][vocab.STATUS_FILED] == 1
    assert summary["revisions_accepted"] == 1
    assert summary["filed"] == 1


def test_the_seed_returns_nothing_without_a_room(db):
    module = importlib.import_module(FEATURE_MODULE)
    assert module.seed(db, {"room_ids": [], "now": FROZEN}) == ""
