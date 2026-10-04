"""WF-093 domain rules: templates, bindings, branding, totals and the frozen rule.

The rules are in ``dsr.quoting_proposals.rules``, the vocabulary in ``vocabulary``, the
judgement calls in ``inferences``, and the writes in ``engine``. This file tests them
without HTTP and is organised by the claims the specification makes:

``a quote's properties beat the template's``
    The evidence is explicit: "with properties set on the quote overriding the quote
    template's settings". These tests fail if the precedence ever reverses, and they check
    that the resolved field says which level answered.
``a published document is never rewritten``
    "Updating your logo and branding won't update existing published quotes, only
    currently drafted quotes and quotes created after updating."
``a line-items module is capped, and the drop is reported``
    "you can only return up to 100 related records for each relationship".
``an unbindable field renders empty and is named``
    A template may bind to a path no schema declares, so one missing field must not cost
    a buyer the whole proposal.
``a custom-coded module is selected, never authored``
    "It isn't possible to create or add custom coded modules to a quote using the API."
``the store is reached only through the audited wrapper``
    No module in the package imports ``dsr.api`` or opens SQLite.

Every test here passes with the file run on its own. Nothing in this module depends on a
test that ran before it.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from dsr.db.audited import AuditedDatabase
from dsr.quoting_proposals import (
    inferences,
    rules,
    vocabulary as vocab,
)
from dsr.quoting_proposals.engine import ProposalEngine
from dsr.store import RecordStore

NOW = datetime(2026, 10, 4, 9, 0, tzinfo=timezone.utc)
MODULES = ("vocabulary.py", "rules.py", "inferences.py", "engine.py")
PACKAGE = Path(__file__).resolve().parents[1] / "dsr" / "quoting_proposals"
FEATURE_ID = "wf-093-build-a-branded-proposal-from-a-template"


@pytest.fixture
def engine(store: RecordStore) -> ProposalEngine:
    """An engine with a clock the test moves by hand.

    The expiration date is the one rendered value that depends on the current instant, and
    the boundaries worth testing are the ones a real clock cannot be steered to.
    """

    return ProposalEngine(store, now=lambda: NOW)


def make_quote(
    store: RecordStore,
    room_id: str = "room_a",
    *,
    title: str = "Northwind renewal",
    **extra: object,
) -> dict:
    """A quote row, written the way WF-086 writes one.

    This workflow reads ``wf086_quote`` and never writes it through its own routes, so the
    fixture writes the row as data. That is the dependency the issue names: WF-086
    provisions the quote and this ticket is not that producer.
    """

    data = {
        "title": title,
        "deal": {"seller": {"name": "Halcyon Cloud"}},
        "company": {"name": "Northwind Logistics"},
        "currency_label": "USD",
        "issue_date": NOW.isoformat(),
        **extra,
    }
    return store.create(vocab.QUOTES, data, room_id=room_id, actor="dana", source="test fixture")


def make_line_items(
    store: RecordStore,
    quote_id: str,
    amounts: tuple[float, ...],
    *,
    room_id: str = "room_a",
) -> list[dict]:
    rows = []
    for position, amount in enumerate(amounts):
        rows.append(
            store.create(
                vocab.LINE_ITEMS,
                {
                    "quote_id": quote_id,
                    "name": f"Item {position + 1}",
                    "amount": amount,
                    "position": position + 1,
                },
                room_id=room_id,
                actor="dana",
                source="test fixture",
            )
        )
    return rows


def make_template(
    engine: ProposalEngine,
    *,
    room_id: str = "room_a",
    **extra: object,
) -> dict:
    payload = {"name": "Standard proposal"}
    payload.update(extra)
    return engine.save_template(payload, room_id=room_id, actor="dana", source="test")


# --------------------------------------------------------------------------- #
# The isolation rule
# --------------------------------------------------------------------------- #


def test_no_domain_module_imports_the_app_or_opens_sqlite():
    """The package depends on nothing inside dsr but the store and itself.

    The defect this prevents is a domain function reaching around the audited wrapper,
    which would write data with no audit row and break the guarantee the product is built
    on. A handler for a shared type would be the same defect at the HTTP layer.
    """

    for name in MODULES:
        path = PACKAGE / name
        assert path.is_file(), path
        text = path.read_text(encoding="utf-8")
        assert "from dsr.api" not in text, name
        assert "import dsr.api" not in text, name
        assert "import sqlite3" not in text, name
        assert "sqlite3.connect" not in text, name


def test_the_package_imports_only_the_store_and_itself():
    """Every dsr import in the package is either the store or this package."""

    for name in MODULES:
        text = (PACKAGE / name).read_text(encoding="utf-8")
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped.startswith(("import ", "from ")):
                continue
            if "dsr." not in stripped:
                continue
            assert "dsr.store" in stripped or "dsr.quoting_proposals" in stripped, (
                f"{name}: {stripped}"
            )


# --------------------------------------------------------------------------- #
# The seed return string
# --------------------------------------------------------------------------- #


def test_the_seed_return_string_is_encodable_by_cp1252():
    """Every character the seeder prints must survive a Windows console.

    One U+2192 RIGHTWARDS ARROW in a recovered feature's return string broke the entire
    seeder on a Windows console, so this is asserted rather than assumed.
    """

    from dsr.features import wf093_build_a_branded_proposal_from_a_template as feature

    db = AuditedDatabase(":memory:", actor="test")
    try:
        store = RecordStore(db)
        room = store.create("room", {"name": "Demo"}, actor="test", source="test")
        message = feature.seed(db, {"room_ids": [(room["id"], "Demo")], "now": NOW, "rng": None})
    finally:
        db.close()

    assert message, "the seed must describe the states it created"
    assert message.encode("cp1252")
    # The point of the assertion is that encoding does not raise, so printing it here is
    # the same code path the seeder takes on a Windows console.
    print(message)


def test_the_seed_names_states_rather_than_only_successes():
    """The brief asks for states that are not all successes.

    The seeded quote carries a path it does not have, so the merge reports unresolved
    bindings, and one document is published and therefore frozen. A seed whose every
    count were a success would not demonstrate either.
    """

    from dsr.features import wf093_build_a_branded_proposal_from_a_template as feature

    db = AuditedDatabase(":memory:", actor="test")
    try:
        store = RecordStore(db)
        room = store.create("room", {"name": "Demo"}, actor="test", source="test")
        message = feature.seed(db, {"room_ids": [(room["id"], "Demo")], "now": NOW, "rng": None})
    finally:
        db.close()

    assert "resolved against nothing" in message.lower()
    assert "published" in message.lower()


# --------------------------------------------------------------------------- #
# Precedence: a quote's own properties beat the template's
# --------------------------------------------------------------------------- #


def test_a_property_set_on_the_quote_overrides_the_templates_value():
    """The evidence: "properties set on the quote overriding the quote template's settings"."""

    resolved = rules.resolve(
        {"po_number": {"field": "po_number", "path": "po_number", "literal": False}},
        {"quote": {"po_number": "PO-4417"}, "record": {"po_number": "PO-4417"}},
    )
    assert resolved["po_number"]["value"] == "PO-4417"
    assert resolved["po_number"]["status"] == "resolved"
    assert resolved["po_number"]["level"] == vocab.PRECEDENCE_QUOTE_OVER_TEMPLATE


def test_a_binding_with_no_path_is_literal_not_unresolved():
    """A terms clause is usually fixed text, so an empty path is literal rather than broken."""

    resolved = rules.resolve(
        {"body": {"field": "body", "path": "", "literal": True}}, {"quote": {}, "record": {}}
    )
    assert resolved["body"]["status"] == "literal"


def test_a_path_the_record_does_not_carry_is_reported_and_renders_empty():
    """One unbindable field must not cost a buyer the whole proposal."""

    resolved = rules.resolve(
        {"buyer": {"field": "buyer", "path": "company.does_not_exist", "literal": False}},
        {"quote": {"company": {"name": "Northwind"}}, "record": {}},
    )
    assert resolved["buyer"]["value"] is None
    assert resolved["buyer"]["status"] == vocab.UNRESOLVED
    assert resolved["buyer"]["path"] == "company.does_not_exist"


def test_a_binding_resolves_against_a_nested_path_in_the_quote_record():
    """A dotted path into arbitrary JSON is the whole point of the schema-flexible store."""

    resolved = rules.resolve(
        {"seller": {"field": "seller", "path": "deal.seller.name", "literal": False}},
        {"quote": {"deal": {"seller": {"name": "Halcyon Cloud"}}}, "record": {}},
    )
    assert resolved["seller"]["value"] == "Halcyon Cloud"
    assert resolved["seller"]["status"] == "resolved"


def test_a_binding_whose_root_is_unreadable_is_refused_at_save_time():
    """A root check at save time, so a typo root never reaches the render."""

    with pytest.raises(rules.ProposalRefusal) as caught:
        rules.validate_bindings([{"field": "buyer", "path": "nonsense.buyer"}])
    assert "nonsense.buyer" in str(caught.value)


def test_a_binding_with_no_field_is_refused():
    """A binding that does not say where it goes cannot be rendered."""

    with pytest.raises(rules.ProposalRefusal):
        rules.validate_bindings([{"path": "company.name"}])


def test_bindings_accept_an_object_keyed_by_field():
    """The object form is the compact one, and it normalises to the same result."""

    result = rules.validate_bindings({"buyer": "company.name"})
    assert result["buyer"] == {
        "field": "buyer",
        "path": "company.name",
        "literal": False,
    }


def test_bindings_reject_a_shape_that_is_neither_object_nor_list():
    with pytest.raises(rules.ProposalRefusal):
        rules.validate_bindings("company.name")


def test_bindings_reject_a_non_object_entry():
    with pytest.raises(rules.ProposalRefusal):
        rules.validate_bindings(["company.name"])


# --------------------------------------------------------------------------- #
# Module order, hiding and the eight kinds
# --------------------------------------------------------------------------- #


def test_the_specifications_eight_module_kinds_are_all_valid():
    for module in vocab.MODULES:
        assert rules.normalise_module(module) == module


def test_an_unknown_module_kind_is_refused_and_names_the_known_set():
    with pytest.raises(rules.ProposalRefusal) as caught:
        rules.normalise_module("signature_block")
    assert "header" in caught.value.errors["modules"]


def test_module_order_appends_every_module_a_template_omitted():
    """A template naming one module still renders a complete proposal."""

    order = rules.module_order([vocab.MODULE_HEADER])
    assert order[0] == vocab.MODULE_HEADER
    assert set(order) == set(vocab.MODULES)


def test_module_order_keeps_the_order_the_template_chose():
    order = rules.module_order([vocab.MODULE_TERMS, vocab.MODULE_HEADER])
    assert order[:2] == [vocab.MODULE_TERMS, vocab.MODULE_HEADER]


def test_a_hidden_module_is_absent_from_the_order_entirely():
    """Dropped rather than kept-and-skipped, so the two lists cannot disagree.

    Hiding is recorded in the template's own ``hidden`` list, so a module left in the order
    would render unless every reader remembered to skip it too.
    """

    order = rules.module_order(
        [
            {"module": vocab.MODULE_HEADER},
            {"module": vocab.MODULE_TERMS, "hidden": True},
            {"module": vocab.MODULE_ACCEPTANCE},
        ]
    )
    assert vocab.MODULE_TERMS not in order
    assert order[:2] == [vocab.MODULE_HEADER, vocab.MODULE_ACCEPTANCE]


def test_a_module_named_in_the_hidden_argument_is_not_appended():
    """Hiding a module the caller never listed still works."""

    order = rules.module_order([vocab.MODULE_HEADER], [vocab.MODULE_TOTALS])
    assert vocab.MODULE_TOTALS not in order
    assert set(order) | {vocab.MODULE_TOTALS} == set(vocab.MODULES)


def test_a_disabled_module_entry_is_treated_as_hidden():
    order = rules.module_order([{"module": vocab.MODULE_TERMS, "enabled": False}])
    assert vocab.MODULE_TERMS not in order


def test_a_module_entry_that_is_neither_string_nor_object_is_refused():
    with pytest.raises(rules.ProposalRefusal):
        rules.module_order([7])


# --------------------------------------------------------------------------- #
# Branding and the three logo sources
# --------------------------------------------------------------------------- #


def test_the_brand_sets_a_token_when_the_template_has_none():
    branding = rules.resolve_branding({}, {"accent": "#10506f"}, {})
    assert branding["tokens"]["accent"]["value"] == "#10506f"
    assert branding["tokens"]["accent"]["level"] == vocab.LOGO_SOURCE_BRAND_KIT


def test_override_brand_kit_lets_the_template_win_over_the_brand():
    """The specification gives the toggle, and its meaning is that the template wins."""

    branding = rules.resolve_branding(
        {vocab.OVERRIDE_BRAND_KIT: True, "accent": "#2a7fae"},
        {"accent": "#10506f"},
        {},
    )
    assert branding["tokens"]["accent"]["value"] == "#2a7fae"
    assert branding["tokens"]["accent"]["level"] == "template_override"


def test_the_brand_wins_when_override_brand_kit_is_off():
    branding = rules.resolve_branding(
        {vocab.OVERRIDE_BRAND_KIT: False, "accent": "#2a7fae"},
        {"accent": "#10506f"},
        {},
    )
    assert branding["tokens"]["accent"]["value"] == "#10506f"


def test_the_quote_beats_every_level_for_branding():
    """The evidence's precedence rule reaches branding as well as content."""

    branding = rules.resolve_branding(
        {vocab.OVERRIDE_BRAND_KIT: True, "accent": "#2a7fae"},
        {"accent": "#10506f"},
        {"accent": "#0c1620"},
    )
    assert branding["tokens"]["accent"]["value"] == "#0c1620"
    assert branding["tokens"]["accent"]["level"] == vocab.LOGO_SOURCE_QUOTE_BRANDING


def test_the_three_logo_sources_are_resolved_in_the_order_the_specification_lists_them():
    """Quote branding first, then the brand, then account branding."""

    all_three = rules.resolve_branding(
        {"account_logo_url": "https://x.example/account.png"},
        {"logo_url": "https://x.example/brand.png"},
        {"logo_url": "https://x.example/quote.png"},
    )
    assert all_three["logo_url"] == "https://x.example/quote.png"
    assert all_three[vocab.REPORT_LOGO_SOURCE] == vocab.LOGO_SOURCE_QUOTE_BRANDING

    two = rules.resolve_branding(
        {"account_logo_url": "https://x.example/account.png"},
        {"logo_url": "https://x.example/brand.png"},
        {},
    )
    assert two[vocab.REPORT_LOGO_SOURCE] == vocab.LOGO_SOURCE_BRAND_KIT

    one = rules.resolve_branding({"account_logo_url": "https://x.example/account.png"}, {}, {})
    assert one[vocab.REPORT_LOGO_SOURCE] == vocab.LOGO_SOURCE_ACCOUNT_BRANDING


def test_the_company_name_fallback_applies_only_when_the_toggle_is_set():
    """The toggle is the seller's, so the fallback never happens silently."""

    off = rules.resolve_branding(
        {vocab.FALLBACK_NAME_WHEN_LOGO_ABSENT: False},
        {"company_name": "Halcyon Cloud"},
        {},
    )
    assert off["logo_url"] is None
    assert off["logo_fallback_applied"] is False

    on = rules.resolve_branding(
        {vocab.FALLBACK_NAME_WHEN_LOGO_ABSENT: True},
        {"company_name": "Halcyon Cloud"},
        {},
    )
    assert on["logo_url"] == "Halcyon Cloud"
    assert on["logo_fallback_applied"] is True


def test_a_colour_that_is_not_a_hex_value_is_refused():
    """A document must never carry a colour a browser cannot draw."""

    with pytest.raises(rules.ProposalRefusal):
        rules.normalise_colour("steel blue", "accent")


def test_a_hex_colour_is_lower_cased():
    assert rules.normalise_colour("#10506F", "accent") == "#10506f"


def test_a_three_digit_hex_colour_is_accepted():
    assert rules.normalise_colour("#abc", "accent") == "#abc"


# --------------------------------------------------------------------------- #
# Line items, the researched cap, and totals
# --------------------------------------------------------------------------- #


def test_a_line_item_with_no_amount_is_computed_from_quantity_and_unit_price():
    """WF-087 provisions these rows and is not merged, so both shapes must be readable."""

    total = rules.line_total({"quantity": 10, "unit_price": "25.50"})
    assert total["amount"] == 255.0
    assert total["derived"] is True


def test_a_line_item_with_neither_an_amount_nor_a_price_is_refused():
    """A total cannot be computed from nothing, and guessing one would be worse."""

    with pytest.raises(rules.ProposalRefusal):
        rules.line_total({"name": "Item"})


def test_a_negative_amount_is_refused_rather_than_signed():
    with pytest.raises(rules.ProposalRefusal):
        rules.coerce_money(-1, "amount")


def test_a_boolean_is_not_an_amount():
    with pytest.raises(rules.ProposalRefusal):
        rules.coerce_money(True, "amount")


def test_a_non_finite_amount_is_refused():
    with pytest.raises(rules.ProposalRefusal):
        rules.coerce_money(float("inf"), "amount")
    with pytest.raises(rules.ProposalRefusal):
        rules.coerce_money(float("nan"), "amount")


def test_a_non_numeric_amount_is_refused():
    with pytest.raises(rules.ProposalRefusal):
        rules.coerce_money("twelve", "amount")


def test_the_line_items_module_is_capped_at_the_researched_figure():
    """The evidence caps a relationship at 100 related records."""

    assert vocab.RELATED_RECORD_CAP == 100
    items = [{"amount": 1.0} for _ in range(150)]
    kept, dropped = rules.cap_line_items(items)
    assert len(kept) == vocab.RELATED_RECORD_CAP
    assert dropped == 50


def test_the_truncation_is_reported_and_never_silent():
    kept, dropped = rules.cap_line_items([{"amount": 1.0} for _ in range(101)])
    assert dropped == 1
    assert kept[-1]["position"] == 100


def test_a_line_item_below_the_cap_reports_no_truncation():
    kept, dropped = rules.cap_line_items([{"amount": 1.0}])
    assert dropped == 0
    assert kept[0]["position"] == 1


def test_the_subtotal_sums_the_rendered_rows():
    block = rules.totals([{"amount": 100.0}, {"amount": 50.5}], {})
    assert block["rows"]["subtotal"]["value"] == 150.5


def test_an_absent_discount_is_reported_as_not_stated_rather_than_as_zero():
    """An absent value and a zero one must stay different on the page."""

    block = rules.totals([{"amount": 100.0}], {})
    assert block["rows"]["discount_total"]["value"] == 0.0
    assert block["rows"]["discount_total"]["stated"] is False


def test_a_stated_discount_is_reported_as_stated():
    block = rules.totals([{"amount": 100.0}], {"discount": 10})
    assert block["rows"]["discount_total"]["value"] == 10.0
    assert block["rows"]["discount_total"]["stated"] is True


def test_the_grand_total_sums_the_four_rows_and_is_unstated_when_a_row_is_unstated():
    block = rules.totals([{"amount": 100.0}], {"tax": 8})
    assert block["rows"]["grand_total"]["value"] == 108.0
    assert block["rows"]["grand_total"]["stated"] is False

    both = rules.totals([{"amount": 100.0}], {"discount": 10, "tax": 8})
    assert both["rows"]["grand_total"]["value"] == 98.0
    assert both["rows"]["grand_total"]["stated"] is True


def test_the_totals_rows_render_in_the_order_the_vocabulary_names():
    block = rules.totals([], {})
    assert [row["label"] for row in block["ordered"]] == [
        vocab.TOTAL_LABELS[row] for row in vocab.TOTAL_ROWS
    ]


def test_the_currency_label_is_carried_from_the_quote():
    assert rules.totals([], {"currency_label": "EUR"})["currency_label"] == "EUR"


# --------------------------------------------------------------------------- #
# Instants and the derived expiration date
# --------------------------------------------------------------------------- #


def test_an_iso_instant_and_unix_milliseconds_both_parse():
    from_iso = rules.coerce_instant("2026-10-04T09:00:00.000+00:00", "issue_date")
    from_millis = rules.coerce_instant(int(NOW.timestamp() * 1000), "issue_date")
    assert from_iso == from_millis == NOW


def test_a_zulu_suffix_parses():
    assert rules.coerce_instant("2026-10-04T09:00:00Z", "issue_date").year == 2026


def test_an_absent_instant_is_none_rather_than_an_error():
    """A quote with no expiry does not expire, and an empty header field is not a refusal."""

    assert rules.coerce_instant(None, "expiration_date") is None
    assert rules.coerce_instant("", "expiration_date") is None


def test_an_unreadable_instant_is_refused_rather_than_treated_as_the_epoch():
    with pytest.raises(rules.ProposalRefusal):
        rules.coerce_instant("last Tuesday", "issue_date")


def test_a_boolean_is_not_an_instant():
    with pytest.raises(rules.ProposalRefusal):
        rules.coerce_instant(True, "issue_date")


def test_a_digit_string_reads_as_unix_milliseconds():
    """A bare number is how a millisecond timestamp survives a JSON round trip."""

    assert rules.coerce_instant(str(int(NOW.timestamp() * 1000)), "issue_date") == NOW


def test_the_default_expiry_is_derived_from_the_issue_date():
    assert rules.default_expiry(NOW, 30) == NOW + timedelta(days=30)


def test_a_stamp_keeps_the_milliseconds():
    """Two writes inside one second must stay distinguishable."""

    assert rules.stamp(NOW).endswith(".000+00:00")


# --------------------------------------------------------------------------- #
# The non-retroactive rule
# --------------------------------------------------------------------------- #


def test_a_published_document_is_frozen():
    assert rules.is_frozen(vocab.STATE_PUBLISHED) is True
    report = rules.rerenderable({"state": vocab.STATE_PUBLISHED})
    assert report["rerenderable"] is False
    assert report["evidence"] == vocab.NO_RETROACTIVE_APPLICATION_QUOTE


def test_a_draft_document_takes_the_change():
    assert rules.is_frozen(vocab.STATE_DRAFT) is False
    assert rules.rerenderable({"state": vocab.STATE_DRAFT})["rerenderable"] is True


def test_an_instantiated_document_takes_the_change_too():
    """The evidence names published quotes and drafted quotes and is silent on the middle."""

    assert rules.is_frozen(vocab.STATE_INSTANTIATED) is False


def test_a_document_with_no_state_is_treated_as_a_draft():
    assert rules.rerenderable({})["rerenderable"] is True


def test_an_unknown_state_is_not_frozen_but_is_refused_by_the_normaliser():
    assert rules.is_frozen("archived") is False
    with pytest.raises(rules.ProposalRefusal):
        rules.normalise_state("archived")


def test_a_frozen_document_refuses_a_re_render_with_a_remediation():
    with pytest.raises(rules.DocumentFrozen) as caught:
        rules.require_rerenderable({"id": "doc_1", "state": vocab.STATE_PUBLISHED})
    body = caught.value.to_dict()
    assert body["error"] == "document_frozen"
    assert "Issue the quote again" in body["remediation"]
    assert body["evidence"] == vocab.NO_RETROACTIVE_APPLICATION_QUOTE


def test_a_draft_document_passes_the_re_render_check():
    assert rules.require_rerenderable({"state": vocab.STATE_DRAFT}) == vocab.STATE_DRAFT


def test_publishing_moves_a_draft_and_an_instantiated_document_to_published():
    assert rules.next_state(vocab.STATE_DRAFT, "publish") == vocab.STATE_PUBLISHED
    assert rules.next_state(vocab.STATE_INSTANTIATED, "publish") == vocab.STATE_PUBLISHED


def test_a_published_document_cannot_be_walked_back_to_draft():
    """Otherwise a caller could un-publish what the buyer already has."""

    with pytest.raises(rules.ProposalRefusal) as caught:
        rules.next_state(vocab.STATE_PUBLISHED, "draft")
    assert vocab.STATE_PUBLISHED in str(caught.value)


def test_an_unknown_move_is_refused_and_names_where_the_document_is():
    with pytest.raises(rules.ProposalRefusal) as caught:
        rules.next_state(vocab.STATE_INSTANTIATED, "archive")
    assert "instantiated" in str(caught.value)


# --------------------------------------------------------------------------- #
# The custom-module limit
# --------------------------------------------------------------------------- #


def test_a_custom_coded_module_may_be_selected_but_never_authored():
    """The evidence says both halves, so both are asserted."""

    advisory = rules.custom_module_advisory()
    assert advisory["custom_modules"] == "select_only"
    assert advisory["authored_via_api"] is False
    assert "isn't possible to create or add custom coded modules" in advisory["evidence"]


# --------------------------------------------------------------------------- #
# Keys
# --------------------------------------------------------------------------- #


def test_a_key_is_lower_cased_and_spaces_become_hyphens():
    assert rules.normalise_key("Standard Proposal") == "standard-proposal"


def test_an_empty_key_is_refused():
    with pytest.raises(rules.ProposalRefusal):
        rules.normalise_key("  ")


def test_a_punctuated_key_is_refused():
    """A key appears in a URL this product has to route on."""

    with pytest.raises(rules.ProposalRefusal):
        rules.normalise_key("not/a/key")


def test_a_one_character_key_is_refused():
    with pytest.raises(rules.ProposalRefusal):
        rules.normalise_key("a")


def test_the_header_field_and_party_role_normalisers_name_their_sets():
    assert rules.normalise_header_field("Issue-Date") == "issue_date"
    assert rules.normalise_party_role("Bill To") == "bill_to"
    with pytest.raises(rules.ProposalRefusal):
        rules.normalise_header_field("signature")
    with pytest.raises(rules.ProposalRefusal):
        rules.normalise_party_role("carrier")
    with pytest.raises(rules.ProposalRefusal):
        rules.normalise_brand_token("font")


# --------------------------------------------------------------------------- #
# The inferred decisions
# --------------------------------------------------------------------------- #


def test_every_recorded_decision_names_options_and_a_choice():
    """A derivation with no rejected alternative is a guess in a derivation's clothes."""

    assert inferences.count() >= 8
    for key, decision in inferences.DECISIONS.items():
        assert decision["options"], key
        assert decision["chosen"] in decision["options"], key
        assert decision["rejected_because"], key
        assert decision["cost_of_the_choice"], key


def test_the_document_model_choice_names_the_jev_audit_that_made_it():
    assert (
        "jev-20261004T212018-20336-18854"
        in (inferences.DECISIONS["DERIVED_DOCUMENT_MODEL_IS_HTML_JSON"]["rejected_because"])
    )


def test_the_ticket_order_choice_names_both_jev_audits():
    """The first ask came back uncertain and was re-asked with measured evidence."""

    text = inferences.DECISIONS["DERIVED_QUOTE_IS_READ_AS_DATA"]["rejected_because"]
    assert "jev-20261004T212044-29964-44792" in text
    assert "jev-20261004T212223-11296-43126" in text


def test_describe_returns_every_decision_with_its_id():
    rows = inferences.describe()
    assert len(rows) == inferences.count()
    assert all("id" in row for row in rows)
    assert [row["id"] for row in rows] == sorted(row["id"] for row in rows)


def test_describe_one_returns_none_for_an_unknown_decision():
    assert inferences.describe_one("NOT_A_DECISION") is None
    assert inferences.describe_one("DERIVED_DOCUMENT_MODEL_IS_HTML_JSON")["chosen"]


# --------------------------------------------------------------------------- #
# The served vocabulary
# --------------------------------------------------------------------------- #


def test_the_vocabulary_names_the_chosen_model_and_both_rejected_ones():
    served = vocab.vocabulary()
    models = served["document_models"]
    chosen = [row for row in models if row["chosen"]]
    assert len(chosen) == 1
    assert chosen[0]["id"] == vocab.DOCUMENT_MODEL_HTML_JSON
    rejected = [row for row in models if not row["chosen"]]
    assert len(rejected) == 2
    assert all(row["rejection"] for row in rejected)


def test_the_vocabulary_serves_both_researched_caps():
    served = vocab.vocabulary()
    assert served["related_record_cap"] == 100
    assert served["placeholder_min_items"] == 1
    assert served["placeholder_max_items"] == 10


def test_the_vocabulary_serves_the_association_type_and_both_quoted_sentences():
    served = vocab.vocabulary()
    assert served["association_type_id"] == "286"
    assert "won't update existing published quotes" in (served["no_retroactive_application_quote"])
    assert "custom coded modules" in served["custom_module_api_limit"]


def test_the_vocabulary_names_the_five_generation_inputs():
    assert set(vocab.vocabulary()["generation_inputs"]) == {
        "line_items",
        "deal_activities",
        "meeting_transcripts",
        "notes",
        "emails",
    }


def test_the_vocabulary_lists_every_binding_root():
    served = vocab.vocabulary()
    for root in ("quote", "deal", "company", "contact", "line_item", "product"):
        assert root in served["binding_roots"]


def test_the_feature_id_is_unique_and_ticket_derived():
    """The frontend id must match the backend id or the page will not be discovered."""

    from dsr.features import wf093_build_a_branded_proposal_from_a_template as feature

    assert feature.FEATURE["id"] == FEATURE_ID
    assert feature.FEATURE["ticket"] == "WF-093"


# --------------------------------------------------------------------------- #
# The engine
# --------------------------------------------------------------------------- #


def test_a_template_saving_creates_and_then_updates_by_key(engine: ProposalEngine):
    first = make_template(engine)
    assert first["action"] == "created"
    second = engine.save_template(
        {"name": "Standard proposal", "terms": "Net 45."},
        room_id="room_a",
        source="test",
    )
    assert second["action"] == "updated"
    assert second["id"] == first["id"]
    assert second["revision"] == first["revision"] + 1


def test_a_template_with_no_name_is_refused(engine: ProposalEngine):
    with pytest.raises(rules.ProposalRefusal):
        engine.save_template({}, room_id="room_a", source="test")


def test_a_template_hides_a_module_and_the_view_reports_both_orders(
    engine: ProposalEngine,
):
    template = make_template(engine, hidden=[vocab.MODULE_ACCEPTANCE])
    assert vocab.MODULE_ACCEPTANCE in template["hidden"]
    assert vocab.MODULE_ACCEPTANCE not in template["rendered"]
    assert vocab.MODULE_HEADER in template["rendered"]


def test_a_template_defaults_its_expiry_window_to_thirty_days(engine: ProposalEngine):
    assert make_template(engine)["default_expiry_days"] == 30


def test_a_template_states_its_own_expiry_window(engine: ProposalEngine):
    assert make_template(engine, default_expiry_days=45)["default_expiry_days"] == 45


def test_a_template_expiry_window_must_be_a_positive_whole_number(engine: ProposalEngine):
    for bad in (0, -1, "soon", True):
        with pytest.raises(rules.ProposalRefusal):
            make_template(engine, default_expiry_days=bad)


def test_a_template_reports_whether_it_carries_a_custom_coded_module(
    engine: ProposalEngine,
):
    template = make_template(engine, carry_custom_modules=True)
    assert template["carry_custom_modules"] is True
    assert template["custom_module_advisory"]["authored_via_api"] is False


def test_a_brand_reports_its_tokens_with_their_labels(engine: ProposalEngine):
    brand = engine.save_brand(
        {"name": "Halcyon", "accent": "#10506F"}, room_id="room_a", source="test"
    )
    assert brand["action"] == "created"
    assert brand["tokens"]["accent"]["value"] == "#10506f"
    assert brand["tokens"]["accent"]["label"] == vocab.BRAND_TOKEN_LABELS["accent"]


def test_a_brand_with_no_name_is_refused(engine: ProposalEngine):
    with pytest.raises(rules.ProposalRefusal):
        engine.save_brand({"accent": "#10506f"}, room_id="room_a", source="test")


def test_a_brand_with_an_unparseable_colour_is_refused(engine: ProposalEngine):
    with pytest.raises(rules.ProposalRefusal):
        engine.save_brand({"name": "Bad", "accent": "navy"}, room_id="room_a", source="test")


def test_a_quote_that_does_not_exist_is_a_not_found_naming_its_collection(
    engine: ProposalEngine,
):
    """The 404 tells a caller where quotes come from rather than just that one is absent."""

    with pytest.raises(rules.ProposalNotFound) as caught:
        engine.quote("wf086_quote_missing")
    assert vocab.QUOTES in str(caught.value)
    assert "WF-086" in str(caught.value)


def test_a_record_in_another_collection_is_not_a_quote(engine: ProposalEngine):
    """A feature may not read across into another workflow's collection."""

    other = engine.store.create(vocab.TEMPLATES, {"name": "x"}, room_id="room_a", source="test")
    with pytest.raises(rules.ProposalNotFound):
        engine.quote(other["id"])


def test_line_items_are_read_on_whichever_reference_the_rows_carry(
    engine: ProposalEngine, store: RecordStore
):
    """WF-086 is not merged, so no column of its rows is guaranteed."""

    quote = make_quote(store)
    engine.store.create(
        vocab.LINE_ITEMS, {"quote": quote["id"], "amount": 10.0}, room_id="room_a", source="test"
    )
    engine.store.create(
        vocab.LINE_ITEMS,
        {"parent_id": quote["id"], "amount": 20.0},
        room_id="room_a",
        source="test",
    )
    engine.store.create(
        vocab.LINE_ITEMS,
        {"quote_id": "another_quote", "amount": 99.0},
        room_id="room_a",
        source="test",
    )
    amounts = sorted(item["amount"] for item in engine.line_items(quote["id"]))
    assert amounts == [10.0, 20.0]


def test_line_items_render_in_position_order(engine: ProposalEngine, store: RecordStore):
    quote = make_quote(store)
    for position, amount in ((3, 30.0), (1, 10.0), (2, 20.0)):
        engine.store.create(
            vocab.LINE_ITEMS,
            {"quote_id": quote["id"], "amount": amount, "position": position},
            room_id="room_a",
            source="test",
        )
    assert [item["amount"] for item in engine.line_items(quote["id"])] == [10.0, 20.0, 30.0]


def test_the_merge_renders_every_module_a_template_kept(engine: ProposalEngine, store: RecordStore):
    template = make_template(engine)
    quote = make_quote(store)
    make_line_items(store, quote["id"], (100.0,))
    document = engine.instantiate(template["id"], quote["id"], source="test")
    assert [module["module"] for module in document["modules"]] == [
        name for name in template["modules"] if name not in template["hidden"]
    ]


def test_the_merge_resolves_a_binding_against_the_quote_records_nested_json(
    engine: ProposalEngine, store: RecordStore
):
    template = make_template(
        engine,
        bindings={"parties": [{"field": "seller", "path": "deal.seller.name"}]},
    )
    quote = make_quote(store)
    document = engine.instantiate(template["id"], quote["id"], source="test")
    parties = next(m for m in document["modules"] if m["module"] == vocab.MODULE_PARTIES)
    seller = next(p for p in parties["parties"] if p["role"] == "seller")
    assert seller["value"] == "Halcyon Cloud"


def test_the_merge_reports_an_unresolved_binding_rather_than_failing(
    engine: ProposalEngine, store: RecordStore
):
    template = make_template(
        engine,
        bindings={"parties": [{"field": "buyer", "path": "company.no_such_field"}]},
    )
    quote = make_quote(store)
    document = engine.instantiate(template["id"], quote["id"], source="test")
    assert document[vocab.REPORT_UNRESOLVED] == [
        {
            "module": vocab.MODULE_PARTIES,
            "field": "buyer",
            "path": "company.no_such_field",
        }
    ]


def test_the_stored_document_holds_no_copy_of_the_quote_record(
    engine: ProposalEngine, store: RecordStore
):
    """The document is a rendered result, not a second copy of the record."""

    template = make_template(engine)
    quote = make_quote(store, po_number="PO-4417")
    engine.instantiate(template["id"], quote["id"], source="test")
    stored = engine.store.list(vocab.DOCUMENTS)[0]["data"]
    # The merge reads the raw record as its scope and strips it before storing, so the
    # saved row must not carry a second copy of the quote's JSON under any key.
    assert "_raw" not in stored
    assert "deal" not in stored


def test_the_header_renders_the_quote_properties_it_bound(
    engine: ProposalEngine, store: RecordStore
):
    template = make_template(engine)
    quote = make_quote(store, po_number="PO-4417")
    document = engine.instantiate(template["id"], quote["id"], source="test")
    header = next(m for m in document["modules"] if m["module"] == vocab.MODULE_HEADER)
    assert header["po_number"] == "PO-4417"
    assert header["quote_reference"] == quote["id"]


def test_the_header_renders_a_derived_expiration_and_says_so(
    engine: ProposalEngine, store: RecordStore
):
    template = make_template(engine, default_expiry_days=45)
    quote = make_quote(store)
    document = engine.instantiate(template["id"], quote["id"], source="test")
    header = next(m for m in document["modules"] if m["module"] == vocab.MODULE_HEADER)
    assert header["expiration_derived"] is True
    assert header["expiration_source"] == "derived_default"
    assert header["expiration_date"].startswith("2026-11-18")


def test_a_stated_expiration_is_rendered_as_stated(engine: ProposalEngine, store: RecordStore):
    template = make_template(engine)
    quote = make_quote(store, expiration_date="2026-11-01T00:00:00+00:00")
    document = engine.instantiate(template["id"], quote["id"], source="test")
    header = next(m for m in document["modules"] if m["module"] == vocab.MODULE_HEADER)
    assert header["expiration_derived"] is False
    assert header["expiration_source"] == "quote_property"


def test_the_header_carries_the_quote_reference_and_po_number(
    engine: ProposalEngine, store: RecordStore
):
    template = make_template(engine)
    quote = make_quote(store, po_number="PO-4417")
    document = engine.instantiate(template["id"], quote["id"], source="test")
    header = next(m for m in document["modules"] if m["module"] == vocab.MODULE_HEADER)
    assert header["quote_reference"] == quote["id"]
    assert header["po_number"] == "PO-4417"


def test_the_totals_module_renders_the_four_rows(engine: ProposalEngine, store: RecordStore):
    template = make_template(engine)
    quote = make_quote(store)
    make_line_items(store, quote["id"], (100.0, 50.0))
    document = engine.instantiate(template["id"], quote["id"], source="test")
    totals = next(m for m in document["modules"] if m["module"] == vocab.MODULE_TOTALS)
    assert [row["label"] for row in totals["totals"]] == [
        vocab.TOTAL_LABELS[row] for row in vocab.TOTAL_ROWS
    ]
    assert totals["totals"][-1]["value"] == 150.0


def test_the_executive_summary_says_this_product_generated_nothing(
    engine: ProposalEngine, store: RecordStore
):
    """The evidence names a vendor capability this repository does not have."""

    template = make_template(engine, executive_summary="A renewal.")
    quote = make_quote(store)
    document = engine.instantiate(template["id"], quote["id"], source="test")
    summary = next(m for m in document["modules"] if m["module"] == vocab.MODULE_EXECUTIVE_SUMMARY)
    assert summary["generated_by_this_product"] is False
    assert summary["source"] == vocab.GENERATION_SOURCE_TEMPLATE
    assert set(summary["inputs"]) == set(vocab.GENERATION_INPUTS)


def test_a_caller_supplied_summary_is_reported_as_caller_supplied(
    engine: ProposalEngine, store: RecordStore
):
    template = make_template(engine, executive_summary="A renewal.")
    quote = make_quote(store, executive_summary="Three seats renew for a year.")
    document = engine.instantiate(template["id"], quote["id"], source="test")
    summary = next(m for m in document["modules"] if m["module"] == vocab.MODULE_EXECUTIVE_SUMMARY)
    assert summary["source"] == vocab.GENERATION_SOURCE_CALLER


def test_the_document_records_the_cap_it_applied(engine: ProposalEngine, store: RecordStore):
    template = make_template(engine)
    quote = make_quote(store)
    make_line_items(store, quote["id"], tuple([10.0] * 120))
    document = engine.instantiate(template["id"], quote["id"], source="test")
    truncated = document[vocab.REPORT_TRUNCATED]
    assert truncated["dropped"] == 20
    assert truncated["cap"] == 100
    assert "100 related records" in truncated["evidence"]


def test_the_document_records_the_placeholder_bound_without_enforcing_it(
    engine: ProposalEngine, store: RecordStore
):
    """There is no content library here, so the bound is reported rather than enforced."""

    template = make_template(engine)
    quote = make_quote(store)
    document = engine.instantiate(template["id"], quote["id"], source="test")
    bounds = document["placeholder_bounds"]
    assert bounds["min"] == 1
    assert bounds["max"] == 10
    assert bounds["enforced"] is False
    assert "no content library" in bounds["note"]


def test_a_rendered_document_records_the_association_type(
    engine: ProposalEngine, store: RecordStore
):
    """The association is settable only at quote creation, so the document records it."""

    template = make_template(engine)
    quote = make_quote(store)
    document = engine.instantiate(template["id"], quote["id"], source="test")
    assert document["association_type_id"] == vocab.ASSOCIATION_TYPE_ID


def test_a_quote_may_carry_its_own_association_type(engine: ProposalEngine, store: RecordStore):
    template = make_template(engine)
    quote = make_quote(store, **{vocab.TEMPLATE_ASSOC_TYPE_FIELD: "286"})
    document = engine.instantiate(template["id"], quote["id"], source="test")
    assert document["association_type_id"] == "286"


def test_a_rendered_document_is_a_snapshot_of_its_branding(
    engine: ProposalEngine, store: RecordStore
):
    """The rule is only true of this store if the branding is stored, not resolved live."""

    brand = engine.save_brand(
        {"name": "Halcyon", "accent": "#10506f"},
        room_id="room_a",
        source="test",
    )
    template = make_template(engine, brand_id=brand["id"])
    quote = make_quote(store)
    document = engine.instantiate(template["id"], quote["id"], source="test")
    engine.save_brand(
        {"name": "Halcyon", "accent": "#000000"},
        brand_id=brand["id"],
        source="test",
    )
    reread = engine.document(document["id"])
    assert reread["branding"]["tokens"]["accent"]["value"] == "#10506f"


def test_instantiating_an_unknown_template_is_a_not_found(engine: ProposalEngine):
    with pytest.raises(rules.ProposalNotFound):
        engine.instantiate("wf093_template_missing", "wf086_quote_missing", source="test")


def test_reading_an_unknown_document_is_a_not_found(engine: ProposalEngine):
    with pytest.raises(rules.ProposalNotFound):
        engine.document("wf093_document_missing")


def test_reading_an_unknown_template_is_a_not_found(engine: ProposalEngine):
    with pytest.raises(rules.ProposalNotFound):
        engine.template("wf093_template_missing")


def test_publishing_an_unpublished_document_freezes_it(engine: ProposalEngine, store: RecordStore):
    template = make_template(engine)
    quote = make_quote(store)
    document = engine.instantiate(template["id"], quote["id"], source="test")
    published = engine.transition(document["id"], "publish", source="test")
    assert published["state"] == vocab.STATE_PUBLISHED
    assert published["retroactivity"]["rerenderable"] is False
    assert published["published_at"]


def test_a_published_document_refuses_a_re_render(engine: ProposalEngine, store: RecordStore):
    template = make_template(engine)
    quote = make_quote(store)
    document = engine.instantiate(template["id"], quote["id"], source="test")
    engine.transition(document["id"], "publish", source="test")
    with pytest.raises(rules.DocumentFrozen):
        engine.transition(document["id"], "rerender", source="test")


def test_an_unpublished_document_re_renders_into_a_new_row(
    engine: ProposalEngine, store: RecordStore
):
    template = make_template(engine)
    quote = make_quote(store)
    document = engine.instantiate(template["id"], quote["id"], source="test")
    again = engine.transition(document["id"], "re_render", source="test")
    assert again["id"] != document["id"]
    assert again["state"] == vocab.STATE_INSTANTIATED


def test_an_instantiated_document_may_return_to_draft(engine: ProposalEngine, store: RecordStore):
    template = make_template(engine)
    quote = make_quote(store)
    document = engine.instantiate(template["id"], quote["id"], source="test")
    drafted = engine.transition(document["id"], "draft", source="test")
    assert drafted["state"] == vocab.STATE_DRAFT
    assert drafted["retroactivity"]["rerenderable"] is True


def test_publishing_an_already_published_document_is_a_no_op_that_still_answers(
    engine: ProposalEngine, store: RecordStore
):
    template = make_template(engine)
    quote = make_quote(store)
    document = engine.instantiate(template["id"], quote["id"], source="test")
    engine.transition(document["id"], "publish", source="test")
    again = engine.transition(document["id"], "publish", source="test")
    assert again["id"] == document["id"]
    assert again["state"] == vocab.STATE_PUBLISHED


def test_transitioning_an_unknown_document_is_a_not_found(engine: ProposalEngine):
    with pytest.raises(rules.ProposalNotFound):
        engine.transition("wf093_document_missing", "publish", source="test")


def test_the_summary_counts_the_states_it_reads(engine: ProposalEngine, store: RecordStore):
    template = make_template(engine)
    quote = make_quote(store)
    make_line_items(store, quote["id"], (10.0,))
    document = engine.instantiate(template["id"], quote["id"], source="test")
    engine.transition(document["id"], "publish", source="test")
    board = engine.summary("room_a")
    assert board["templates"] == 1
    assert board["documents"] == 1
    assert board["by_state"][vocab.STATE_PUBLISHED] == 1
    assert board["quotes_read"] == 1
    assert board["line_items_read"] == 1
    assert board["document_model"] == vocab.DOCUMENT_MODEL_HTML_JSON


def test_the_summary_counts_unresolved_bindings_outside_the_document_count(
    engine: ProposalEngine, store: RecordStore
):
    """A proposal that rendered with an empty field is still a proposal."""

    template = make_template(
        engine, bindings={"parties": [{"field": "buyer", "path": "company.missing"}]}
    )
    quote = make_quote(store)
    engine.instantiate(template["id"], quote["id"], source="test")
    board = engine.summary("room_a")
    assert board["documents"] == 1
    assert board["unresolved_bindings"] == 1


def test_an_empty_store_summarises_to_zero_rather_than_failing(engine: ProposalEngine):
    """A board that 500s on a fresh room is a broken feature."""

    board = engine.summary("room_b")
    assert board["templates"] == 0
    assert board["documents"] == 0
    assert board["unresolved_bindings"] == 0


def test_the_summary_names_the_collections_it_reads(engine: ProposalEngine):
    collections = engine.summary()["collections"]
    assert collections["quotes_read_only"] == vocab.QUOTES
    assert collections["templates"] == vocab.TEMPLATES


def test_this_workflow_never_creates_a_quote_through_its_own_routes(
    engine: ProposalEngine, store: RecordStore
):
    """The read-only decision, asserted on the store rather than described in a docstring."""

    make_quote(store)
    make_template(engine)
    before = len(store.list(vocab.QUOTES))
    template = engine.templates()[0]
    engine.instantiate(template["id"], store.list(vocab.QUOTES)[0]["id"], source="test")
    assert len(store.list(vocab.QUOTES)) == before


def test_a_preview_writes_nothing(engine: ProposalEngine, store: RecordStore):
    """The merge is pure, so a preview changes no record and records no audit row."""

    template = make_template(engine)
    quote = make_quote(store)
    make_line_items(store, quote["id"], (10.0,))
    before = len(store.list(vocab.DOCUMENTS))
    engine.render(template, engine.quote_for_merge(quote["id"]))
    assert len(store.list(vocab.DOCUMENTS)) == before


def test_every_write_goes_through_the_audited_wrapper(engine: ProposalEngine, store: RecordStore):
    """The product guarantee is that the audit row lands with the change."""

    template = make_template(engine)
    quote = make_quote(store)
    document = engine.instantiate(template["id"], quote["id"], source="POST /documents")
    rows = store.audit(collection=vocab.DOCUMENTS)
    assert rows
    assert rows[0]["source"] == "POST /documents"
    assert rows[0]["record_id"] == document["id"]
    assert rows[0]["action"] == "insert"
