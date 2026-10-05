"""WF-086: the quoting rules, exercised as a domain module with no server.

The rules in ``dsr.quote_authoring`` touch the
:class:`~dsr.store.RecordStore` and nothing else, so every rule below is
reachable without a ``TestClient``. These tests are what make each of the
issue's open decisions checkable at the instant it is stated about.

What is under test, and why each one matters
--------------------------------------------

* **Money rounds half up, two places.** A quote off by a cent per line is off by
  a pound on a hundred-line quote, and ``round()`` on a binary float cannot say
  half up at all.
* **Tax is charged on the net amount.** The rule Jev selected over
  ``tax_on_gross`` (audit ``jev-20261004T181435-29468-75674``). A discount must
  reduce the tax as well as the subtotal, or a buyer is taxed on money they did
  not agree to pay.
* **The total contract value is the total plus the payments dated after today.**
  A payment due today is not a future payment. And this is the only figure
  written back to the deal amount.
* **A cloned line item has its own record id.** The research states it directly
  and the issue calls it the load-bearing rule. A clone that reused the deal's
  id would break the replace-on-publish and the audit trail at once.
* **A quantity change re-resolves the tier.** The research says tier boundaries
  re-evaluate on every quantity change, so the tier is a function of quantity
  and not a price typed once.
* **An empty catalogue degrades rather than fails.** WF-087 has not shipped. A
  quote created before the catalogue exists must still be creatable from the
  unit prices the deal carried, and the response must say the catalogue was
  absent.
* **Custom-coded modules cannot be authored through the API.** The research says
  so in as many words. A custom module a person built in the editor is accepted,
  and must declare where it came from.
* **Publishing writes back the contract value and nothing else.** The research is
  narrow on purpose: "only the TCV is copied back to the deal amount".
* **Every record is plain JSON in ``data``.** A team adding a field must need no
  coordination, so this module adds no migration and no typed column.

Every test names the property it protects, because a test named
``test_quote_flow`` tells a reviewer nothing about which rule they can stop
worrying about.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from dsr.features import load_feature
from dsr.quote_authoring import engine as quotes, pricing, vocabulary as vocab
from dsr.quote_authoring.errors import QuoteConflict, QuoteError, QuoteNotFound
from dsr.store import RecordStore

MODULE = "wf086_author_a_quote_from_a_deal_or_opportunity"

NOW = datetime(2026, 5, 4, 9, 0, tzinfo=timezone.utc)
TODAY = NOW.date().isoformat()
LATER = (NOW + timedelta(days=40)).date().isoformat()


@pytest.fixture()
def engine(store: RecordStore) -> quotes.QuoteEngine:
    """The engine, with the clock pinned so every date boundary is exact."""
    return quotes.QuoteEngine(store, now=lambda: NOW)


@pytest.fixture()
def room(store: RecordStore) -> str:
    return store.create("room", {"name": "Northwind"}, actor="test", source="test")["id"]


def _deal(store: RecordStore, room_id: str, **overrides: object) -> str:
    """One deal in the CRM mirror, with three priced line items."""
    data = {
        "name": "Northwind renewal",
        "account": "Northwind Traders",
        "owner": "dana",
        "currency": "EUR",
        "amount": 24000.0,
        "stage": "negotiation",
        "address": {"city": "Seattle"},
    }
    data.update(overrides)
    deal = store.create("crm_deal", data, room_id=room_id, actor="test", source="test")
    for position, (name, price, quantity, tax) in enumerate(
        [
            ("Platform, annual", 4800.0, 30, 10.0),
            ("Support, annual", 2400.0, 1, 10.0),
            ("Onboarding", 1500.0, 2, 0.0),
        ]
    ):
        store.create(
            "crm_deal_line_item",
            {
                "deal_id": deal["id"],
                "position": position,
                "name": name,
                "sku": name.split(",")[0].upper()[:6],
                "quantity": quantity,
                "unit_price": price,
                "tax_rate": tax,
            },
            room_id=room_id,
            actor="test",
            source="test",
        )
    return str(deal["id"])


def _product(store: RecordStore, **overrides: object) -> str:
    data = {
        "name": "Platform, annual",
        "sku": "PLAT",
        "unit_price": 5000.0,
        "tiers": [{"min_qty": 1, "unit_price": 5000.0}, {"min_qty": 25, "unit_price": 4200.0}],
    }
    data.update(overrides)
    return str(store.create("crm_product", data, actor="test", source="test")["id"])


def _line(store: RecordStore, quote_id: str, **extra: object) -> dict:
    """Add a line to a quote through the engine, with a pinned clock."""
    return quotes.QuoteEngine(store, now=lambda: NOW).add_line_item(
        quote_id,
        {"name": "Extra", "quantity": 1, "unit_price": 100.0, **extra},
        source="test",
        actor="test",
    )


# --------------------------------------------------------------------------- #
# Money
# --------------------------------------------------------------------------- #


def test_money_rounds_half_up_rather_than_to_even():
    """Half up, not banker's rounding, and not binary-float rounding.

    ``round(2.675, 2)`` is 2.67 in Python because 2.675 is not exactly 2.675. A
    quote whose figures cannot be stated exactly is a quote whose total does not
    equal the sum of its printed lines.
    """
    assert pricing.money(2.675) == 2.68
    assert pricing.money(1.005) == 1.01
    assert pricing.money(0.125) == 0.13
    assert pricing.money(2.665) == 2.67


def test_money_reads_a_number_a_person_typed():
    """A value that arrived as text still prices.

    A quote is assembled from a deal mirror, a price book and a typed form, so
    ``"4200"`` is a normal thing to be handed.
    """
    assert pricing.money("4200") == 4200.0
    assert pricing.money(" 12.5 ") == 12.5
    assert pricing.money(None) == 0.0
    assert pricing.money("not a number") == 0.0


def test_a_non_finite_number_reads_as_zero_rather_than_poisoning_the_total():
    """An optional field that is malformed must not make the quote unreadable."""
    assert pricing.money(float("inf")) == 0.0
    assert pricing.money(float("nan")) == 0.0


# --------------------------------------------------------------------------- #
# Tiers
# --------------------------------------------------------------------------- #


def test_the_highest_tier_the_quantity_reaches_wins():
    """Tier boundaries are a function of the quantity, which is what makes them
    boundaries. Picking the first matching tier in file order would make a
    quantity of 30 price at the 1-unit rate whenever the tiers were written
    smallest-first.
    """
    tiers = [{"min_qty": 1, "unit_price": 5000.0}, {"min_qty": 25, "unit_price": 4200.0}]
    assert pricing.resolve_unit_price({"tiers": tiers}, 1)[0] == 5000.0
    assert pricing.resolve_unit_price({"tiers": tiers}, 24)[0] == 5000.0
    assert pricing.resolve_unit_price({"tiers": tiers}, 25)[0] == 4200.0
    assert pricing.resolve_unit_price({"tiers": tiers}, 30)[0] == 4200.0


def test_tiers_written_out_of_order_still_resolve():
    """The tiers are sorted before they are compared.

    A price book is written by a person. Sorting here means the order they were
    saved in cannot change a quote.
    """
    tiers = [{"min_qty": 25, "unit_price": 4200.0}, {"min_qty": 1, "unit_price": 5000.0}]
    price, label, kind = pricing.resolve_unit_price({"tiers": tiers}, 30)
    assert (price, kind) == (4200.0, "tier")
    assert label == "from 25"


def test_a_tier_spellings_a_catalogue_might_use_are_all_read():
    """WF-087 has not shipped, so three spellings are accepted for each field."""
    price, _, _ = pricing.resolve_unit_price({"quantity_tiers": [{"from": 10, "price": 7.5}]}, 10)
    assert price == 7.5
    price, _, _ = pricing.resolve_unit_price(
        {"price_tiers": [{"min_quantity": 10, "amount": 7.5}]}, 10
    )
    assert price == 7.5


def test_a_product_with_no_tiers_prices_at_its_own_unit_price():
    """Tiered and flat products are both ordinary in a price book."""
    price, label, kind = pricing.resolve_unit_price({"unit_price": 99.0}, 5)
    assert (price, label, kind) == (99.0, "list", "list")


def test_an_absent_product_resolves_to_zero_rather_than_raising():
    """The catalogue has not been provisioned.

    Raising here would make every quote creation fail on a dependency that has
    not merged. Zero with the source ``missing`` lets the caller decide, and the
    response says which it was.
    """
    assert pricing.resolve_unit_price(None, 5) == (0.0, None, "missing")
    assert pricing.resolve_unit_price({"name": "no price anywhere"}, 5) == (0.0, None, "missing")


# --------------------------------------------------------------------------- #
# One line
# --------------------------------------------------------------------------- #


def test_a_line_tax_is_charged_on_the_net_amount_not_the_gross():
    """The rule Jev selected over taxing the gross.

    A 10 percent discount and a 10 percent tax on 300.00 is 30.00 of tax gross
    and 27.00 net. Taxing the gross overstates what the buyer owes by exactly the
    tax on the discount.
    """
    amounts = pricing.line_amounts(
        {
            "quantity": 3,
            "unit_price": 100.0,
            "discount_type": "percentage",
            "discount_value": 10,
            "tax_rate": 10,
        }
    )
    assert amounts["subtotal"] == 300.0
    assert amounts["discount"] == 30.0
    assert amounts["net"] == 270.0
    assert amounts["tax"] == 27.0
    assert amounts["total"] == 297.0


def test_a_currency_discount_is_taken_off_the_line_directly():
    """The research names both forms of unit discount."""
    amounts = pricing.line_amounts(
        {
            "quantity": 3,
            "unit_price": 100.0,
            "discount_type": "currency",
            "discount_value": 50,
            "tax_rate": 0,
        }
    )
    assert amounts["discount"] == 50.0
    assert amounts["total"] == 250.0


def test_a_percentage_discount_above_one_hundred_clamps_rather_than_going_negative():
    """A negative total is a figure no buyer can be shown."""
    amounts = pricing.line_amounts(
        {"quantity": 1, "unit_price": 100.0, "discount_type": "percentage", "discount_value": 150}
    )
    assert amounts["discount"] == 100.0
    assert amounts["net"] == 0.0
    assert amounts["total"] == 0.0


def test_a_currency_discount_larger_than_the_line_clamps_to_the_line():
    """Same rule, other form. Otherwise the discount exceeds what is owed."""
    amounts = pricing.line_amounts(
        {"quantity": 1, "unit_price": 100.0, "discount_type": "currency", "discount_value": 500}
    )
    assert amounts["discount"] == 100.0
    assert amounts["net"] == 0.0


# --------------------------------------------------------------------------- #
# The quote's totals
# --------------------------------------------------------------------------- #


def test_the_total_contract_value_is_the_total_plus_the_payments_still_to_come():
    """The definition the publish write-back depends on."""
    totals = pricing.totals_for(
        [{"quantity": 3, "unit_price": 100.0, "tax_rate": 0}],
        [{"due_on": LATER, "amount": 15000.0}],
        TODAY,
    )
    assert totals["total"] == 300.0
    assert totals["future_payments"] == 15000.0
    assert totals["total_contract_value"] == 15300.0


def test_a_payment_due_today_is_not_a_future_payment():
    """It falls due today, which is the present.

    Counting it as future would tell a seller the contract value includes money
    the buyer already owes, which is the number that goes onto the deal.
    """
    totals = pricing.totals_for(
        [{"quantity": 1, "unit_price": 100.0}],
        [{"due_on": TODAY, "amount": 500.0}, {"due_on": LATER, "amount": 700.0}],
        TODAY,
    )
    assert totals["future_payments"] == 700.0


def test_a_payment_dated_in_the_past_is_not_a_future_payment():
    """A schedule carried over from a previous quote does not inflate the value."""
    past = (NOW - timedelta(days=5)).date().isoformat()
    totals = pricing.totals_for(
        [{"quantity": 1, "unit_price": 100.0}], [{"due_on": past, "amount": 900.0}], TODAY
    )
    assert totals["future_payments"] == 0.0
    assert totals["total_contract_value"] == 100.0


def test_the_total_equals_the_sum_of_the_printed_lines():
    """The sum of the rounded line figures, not a running float rounded once.

    A buyer who adds up the column must arrive at the total, or the quote is not
    a document anybody can check.
    """
    lines = [
        {"quantity": 3, "unit_price": 33.33, "tax_rate": 7.5},
        {"quantity": 7, "unit_price": 11.11, "tax_rate": 7.5},
        {"quantity": 1, "unit_price": 0.07, "tax_rate": 7.5},
    ]
    totals = pricing.totals_for(lines, [], TODAY)
    assert totals["subtotal"] == round(
        sum(pricing.line_amounts(line)["subtotal"] for line in lines), 2
    )
    assert totals["total"] == round(sum(pricing.line_amounts(line)["total"] for line in lines), 2)


def test_a_schedule_entry_that_is_not_a_mapping_is_ignored():
    """A hand-edited schedule must not take the totals down with it."""
    totals = pricing.totals_for(
        [{"quantity": 1, "unit_price": 100.0}], ["not a payment", {"amount": 5.0}], TODAY
    )
    assert totals["future_payments"] == 0.0


# --------------------------------------------------------------------------- #
# Vocabulary
# --------------------------------------------------------------------------- #


def test_the_vocabulary_reports_the_totals_contract_rather_than_only_the_names():
    """A figure whose meaning lives only in the source cannot be labelled by the
    page and cannot be checked against the research."""
    served = vocab.vocabulary()
    for figure in (
        "subtotal",
        "discount",
        "tax",
        "total",
        "future_payments",
        "total_contract_value",
        "rounding",
    ):
        assert served["totals"][figure], figure


def test_the_vocabulary_records_what_the_research_says_cannot_be_done():
    """A reader who cannot tell "we decided not to" from "we forgot to" has to
    open the source, and that is a cost paid every time the question comes up."""
    items = {entry["item"]: entry for entry in vocab.vocabulary()["not_implemented"]}
    assert "Custom-coded quote modules" in items
    assert "isn't possible" in items["Custom-coded quote modules"]["source"]
    assert items["Custom-coded quote modules"]["decision"]
    assert "Product library and tiered price books" in items
    assert "not built" in items["Product library and tiered price books"]["source"]


def test_every_refusal_reason_the_engine_can_raise_has_a_sentence_for_the_page():
    """The page branches on the token and shows its own words, so a token with no
    sentence is a reason the interface can only print raw."""
    reasons = vocab.vocabulary()["refusal_reasons"]
    for reason in (
        vocab.REASON_CUSTOM_MODULE_NOT_API_AUTHORABLE,
        vocab.REASON_CUSTOM_MODULE_NEEDS_UI_PROVENANCE,
        vocab.REASON_MODULE_KEY_UNKNOWN,
        vocab.REASON_ALREADY_PUBLISHED,
        vocab.REASON_QUOTE_EXPIRED,
        vocab.REASON_QUOTE_IS_EMPTY,
        vocab.REASON_QUOTE_FROZEN,
    ):
        assert reasons[reason], reason


# --------------------------------------------------------------------------- #
# Creating a quote from a deal
# --------------------------------------------------------------------------- #


def test_a_quote_takes_its_header_from_the_deal(engine, store, room):
    """The research says the quote form "is prefilled with details from the
    opportunity record". Owner, currency, potential customer and address are the
    four the data flow names."""
    quote = engine.create_quote(room, {"deal_id": _deal(store, room)}, source="t", actor="t")[
        "quote"
    ]
    assert quote["account"] == "Northwind Traders"
    assert quote["owner"] == "dana"
    assert quote["currency"] == "EUR"
    assert quote["address"] == {"city": "Seattle"}
    assert quote["title"] == "Northwind renewal"
    assert quote["status"] == vocab.STATUS_DRAFT


def test_creating_a_quote_clones_every_deal_line_onto_it(engine, store, room):
    """The clone is the whole point of the workflow."""
    created = engine.create_quote(room, {"deal_id": _deal(store, room)}, source="t", actor="t")
    assert created["cloned_line_items"] == 3
    assert [line["name"] for line in created["quote"]["line_items"]] == [
        "Platform, annual",
        "Support, annual",
        "Onboarding",
    ]


def test_a_cloned_line_has_its_own_record_id_and_names_the_one_it_came_from(engine, store, room):
    """The load-bearing rule.

    "Line items on quotes have their own record IDs, separate to the deal line
    item record IDs." A clone that reused the id would make the clone invisible in
    the audit log, would leave the replace-on-publish unable to tell which rows it
    replaced, and a later edit would rewrite the deal's line through a quote
    route.
    """
    deal_id = _deal(store, room)
    deal_lines = store.find("crm_deal_line_item", {"deal_id": deal_id}, limit=10)
    quote = engine.create_quote(room, {"deal_id": deal_id}, source="t", actor="t")["quote"]
    quote_ids = {line["id"] for line in quote["line_items"]}
    deal_ids = {row["id"] for row in deal_lines}
    assert quote_ids.isdisjoint(deal_ids), "a quote line reused a deal line's record id"
    assert {line["source_line_item_id"] for line in quote["line_items"]} == deal_ids


def test_the_cloned_lines_keep_the_deal_line_order(engine, store, room):
    """``find`` returns rows newest first.

    Without a stored position the last line the seller added would appear at the
    top of the quote. The same defect was found in WF-124's reader.
    """
    quote = engine.create_quote(room, {"deal_id": _deal(store, room)}, source="t", actor="t")[
        "quote"
    ]
    assert [line["position"] for line in quote["line_items"]] == [0, 1, 2]


def test_a_quote_cannot_be_created_from_a_deal_that_does_not_exist(engine, room):
    """And the error names the field, so the form can put it beside its input."""
    with pytest.raises(QuoteError) as caught:
        engine.create_quote(room, {"deal_id": "nope"}, source="t", actor="t")
    assert "deal_id" in caught.value.errors


def test_an_expiration_date_must_be_a_date(engine, store, room):
    """A malformed date would sort against ``today`` as a string and silently
    mark every quote expired, or none."""
    with pytest.raises(QuoteError) as caught:
        engine.create_quote(
            room, {"deal_id": _deal(store, room), "expires_on": "31/12/2099"}, source="t", actor="t"
        )
    assert "expires_on" in caught.value.errors


def test_a_quote_with_no_template_still_gets_the_builtin_module_list(engine, store, room):
    """The module editor needs a list to show on open."""
    quote = engine.create_quote(room, {"deal_id": _deal(store, room)}, source="t", actor="t")[
        "quote"
    ]
    assert [module["key"] for module in quote["modules"]] == list(vocab.BUILTIN_MODULE_KEYS)
    assert all(module["visible"] for module in quote["modules"])


def test_a_template_supplies_the_module_list_and_its_position(engine, store, room):
    """Picking a template from the dropdown is the first step of the flow."""
    template = engine.create_template(
        {"name": "Standard quote", "modules": ["header", "line_items", "totals"]},
        source="t",
        actor="t",
    )["template"]
    quote = engine.create_quote(
        room, {"deal_id": _deal(store, room), "template_id": template["id"]}, source="t", actor="t"
    )["quote"]
    assert [module["key"] for module in quote["modules"]] == ["header", "line_items", "totals"]
    assert quote["template_type"] == vocab.QUOTE_TEMPLATE_TYPE


def test_a_template_records_that_it_was_authored_in_the_editor(engine):
    """The CRM API offers a read and a search on templates and no create
    endpoint, so nothing here may claim to be an API-authored template."""
    template = engine.create_template({"name": "Standard"}, source="t", actor="t")["template"]
    assert template["authored_via"] == "ui"


def test_a_template_needs_a_name(engine):
    with pytest.raises(QuoteError) as caught:
        engine.create_template({"description": "no name"}, source="t", actor="t")
    assert "name" in caught.value.errors


# --------------------------------------------------------------------------- #
# Line item identity and the catalogue
# --------------------------------------------------------------------------- #


def test_a_line_typed_by_hand_is_priced_from_what_the_seller_entered(engine, store, room):
    """The alternative to selecting from the product library."""
    quote = engine.create_quote(room, {"deal_id": _deal(store, room)}, source="t", actor="t")[
        "quote"
    ]
    result = _line(store, quote["id"], unit_price=250.0, name="Consulting day")
    added = result["quote"]["line_items"][-1]
    assert added["unit_price"] == 250.0
    assert added["price_source"] == vocab.PRICE_FROM_MANUAL


def test_a_line_selected_from_the_catalogue_prices_off_its_tier(engine, store, room):
    """Thirty units crosses the 25-unit boundary, so it prices at the tier price."""
    product = _product(store)
    quote = engine.create_quote(room, {"deal_id": _deal(store, room)}, source="t", actor="t")[
        "quote"
    ]
    result = _line(store, quote["id"], product_id=product, quantity=30, name="Platform, annual")
    added = result["quote"]["line_items"][-1]
    assert added["unit_price"] == 4200.0
    assert added["price_source"] == vocab.PRICE_FROM_CATALOG
    assert added["tier_label"] == "from 25"


def test_changing_the_quantity_re_resolves_the_tier(engine, store, room):
    """The research: pricing "recalculates automatically whenever a line item
    quantity changes (tier boundaries re-evaluate)".

    Twenty-four units and twenty-five units straddle the boundary. If the tier
    were resolved once at creation, both would price at the 25-unit rate.
    """
    product = _product(store)
    quote = engine.create_quote(room, {"deal_id": _deal(store, room)}, source="t", actor="t")[
        "quote"
    ]
    line = _line(store, quote["id"], product_id=product, quantity=30, name="Platform, annual")
    line_id = line["quote"]["line_items"][-1]["id"]

    below = engine.update_line_item(line_id, {"quantity": 24}, source="t", actor="t")
    assert below["quote"]["line_items"][-1]["unit_price"] == 5000.0

    at = engine.update_line_item(line_id, {"quantity": 25}, source="t", actor="t")
    assert at["quote"]["line_items"][-1]["unit_price"] == 4200.0


def test_a_quote_created_before_the_catalogue_exists_still_prices_from_the_deal(
    engine, store, room
):
    """WF-087 has not shipped, so this is the normal case today, not an edge.

    The clone falls back to the unit price the deal carried and the quote records
    that the catalogue was absent, so a reviewer can see the degradation rather
    than infer it from a price that came from somewhere unexpected.
    """
    assert store.list("crm_product", limit=1) == []
    quote = engine.create_quote(room, {"deal_id": _deal(store, room)}, source="t", actor="t")[
        "quote"
    ]
    assert quote["catalogue_available"] is False
    assert [line["unit_price"] for line in quote["line_items"]] == [4800.0, 2400.0, 1500.0]
    assert all(line["price_source"] == vocab.PRICE_FROM_DEAL for line in quote["line_items"])


def test_the_catalogue_search_is_empty_rather_than_failing_when_there_is_no_catalogue(store):
    """The dropdown has nothing in it, and the page must be able to say so."""
    result = quotes.QuoteEngine.search_catalogue(store, "")
    assert result["catalogue_available"] is False
    assert result["products"] == []


def test_the_catalogue_search_matches_a_name_a_description_or_a_sku(store):
    """The research: search "by name/description/SKU"."""
    _product(store, name="Platform, annual", sku="NWD-PLAT", description="Per year, 40 seats.")
    assert len(quotes.QuoteEngine.search_catalogue(store, "nwd-plat")["products"]) == 1
    assert len(quotes.QuoteEngine.search_catalogue(store, "40 seats")["products"]) == 1
    assert quotes.QuoteEngine.search_catalogue(store, "nothing like it")["products"] == []


def test_a_line_item_that_is_not_valid_names_every_offending_field(engine, store, room):
    """One message per field, so a form can put each beside its own input."""
    quote = engine.create_quote(room, {"deal_id": _deal(store, room)}, source="t", actor="t")[
        "quote"
    ]
    with pytest.raises(QuoteError) as caught:
        engine.add_line_item(
            quote["id"],
            {
                "name": "",
                "quantity": -1,
                "unit_price": -5,
                "discount_type": "free",
                "discount_value": -1,
                "tax_rate": 500,
            },
            source="t",
            actor="t",
        )
    assert set(caught.value.errors) == {
        "name",
        "quantity",
        "unit_price",
        "discount_type",
        "discount_value",
        "tax_rate",
    }


def test_a_percentage_discount_above_one_hundred_is_refused_rather_than_clamped(
    engine, store, room
):
    """On input, not on arithmetic.

    ``line_amounts`` clamps so a stored line can never produce a negative total,
    but a seller who typed 150 percent has made a mistake and should be told,
    not silently given a free line.
    """
    quote = engine.create_quote(room, {"deal_id": _deal(store, room)}, source="t", actor="t")[
        "quote"
    ]
    with pytest.raises(QuoteError) as caught:
        _line(store, quote["id"], discount_type="percentage", discount_value=150)
    assert "discount_value" in caught.value.errors


def test_a_removed_line_stays_in_the_audit_trail(engine, store, room):
    """Soft-deleted, never hard.

    The audit row is the record of what the quote once contained. A hard delete
    would leave the totals on that row referring to a line nobody can read.
    """
    quote = engine.create_quote(room, {"deal_id": _deal(store, room)}, source="t", actor="t")[
        "quote"
    ]
    line_id = quote["line_items"][0]["id"]
    engine.delete_line_item(line_id, source="t", actor="t")
    assert store.get(line_id) is None
    # Through ``db``, because ``RecordStore.get`` takes no ``include_deleted``.
    with_deleted = store.db.get(line_id, include_deleted=True)
    assert with_deleted is not None and with_deleted["deleted_at"] is not None


def test_a_removed_line_leaves_the_quote_total_smaller(engine, store, room):
    quote = engine.create_quote(room, {"deal_id": _deal(store, room)}, source="t", actor="t")[
        "quote"
    ]
    before = quote["totals"]["total"]
    line_id = quote["line_items"][0]["id"]
    after = engine.delete_line_item(line_id, source="t", actor="t")["quote"]["totals"]["total"]
    assert after < before


def test_the_totals_a_quote_reports_agree_with_the_lines_returned_beside_them(engine, store, room):
    """Recomputed on read, not read back.

    A response that returned a stored total beside a freshly listed set of lines
    could disagree with itself, and the disagreement is what a buyer notices.
    """
    quote = engine.create_quote(room, {"deal_id": _deal(store, room)}, source="t", actor="t")[
        "quote"
    ]
    served = engine.read_quote(quote["id"])
    assert served["totals"] == pricing.totals_for(served["line_items"], [], TODAY)


def test_a_field_this_module_has_never_heard_of_round_trips_untouched(engine, store, room):
    """Schema flexibility, proved rather than asserted.

    A team adding a field must need no coordination with anyone: no migration, no
    typed column, no change to this module.
    """
    quote = engine.create_quote(
        room,
        {"deal_id": _deal(store, room), "buyer_note": "procurement asked for net 60"},
        source="t",
        actor="t",
    )["quote"]
    assert engine.read_quote(quote["id"])["buyer_note"] == "procurement asked for net 60"


# --------------------------------------------------------------------------- #
# Modules
# --------------------------------------------------------------------------- #


def test_a_custom_coded_module_cannot_be_authored_through_the_api(engine, store, room):
    """The research says it "isn't possible to create or add custom coded modules
    to a quote using the API". Storing one anyway would make this feature claim a
    capability its source says does not exist."""
    quote = engine.create_quote(room, {"deal_id": _deal(store, room)}, source="t", actor="t")[
        "quote"
    ]
    with pytest.raises(QuoteConflict) as caught:
        engine.update_quote(
            quote["id"],
            {"modules": [{"key": "footer", "custom_coded": True}]},
            source="t",
            actor="t",
        )
    assert caught.value.reason == vocab.REASON_CUSTOM_MODULE_NOT_API_AUTHORABLE


def test_a_custom_module_is_accepted_when_it_says_it_came_from_the_editor(engine, store, room):
    """People do build custom modules, in the editor. The quote records that."""
    quote = engine.create_quote(room, {"deal_id": _deal(store, room)}, source="t", actor="t")[
        "quote"
    ]
    served = engine.update_quote(
        quote["id"],
        {"modules": [{"key": "footer", "kind": "custom", "authored_via": "ui", "label": "Footer"}]},
        source="t",
        actor="t",
    )["quote"]
    assert served["modules"] == [
        {
            "key": "footer",
            "label": "Footer",
            "kind": "custom",
            "visible": True,
            "position": 0,
            "authored_via": "ui",
        }
    ]


def test_a_custom_module_without_provenance_is_refused(engine, store, room):
    """So nothing here can be mistaken for a module the editor built."""
    quote = engine.create_quote(room, {"deal_id": _deal(store, room)}, source="t", actor="t")[
        "quote"
    ]
    with pytest.raises(QuoteConflict) as caught:
        engine.update_quote(
            quote["id"], {"modules": [{"key": "footer", "kind": "custom"}]}, source="t", actor="t"
        )
    assert caught.value.reason == vocab.REASON_CUSTOM_MODULE_NEEDS_UI_PROVENANCE


def test_an_unknown_builtin_module_key_is_refused(engine, store, room):
    """A module the renderer has no description for is a module that would
    silently vanish from the quote."""
    quote = engine.create_quote(room, {"deal_id": _deal(store, room)}, source="t", actor="t")[
        "quote"
    ]
    with pytest.raises(QuoteConflict) as caught:
        engine.update_quote(
            quote["id"], {"modules": [{"key": "weather_widget"}]}, source="t", actor="t"
        )
    assert caught.value.reason == vocab.REASON_MODULE_KEY_UNKNOWN


def test_a_module_can_be_hidden_and_reordered_by_the_order_it_arrives_in(engine, store, room):
    """Show, hide, and drag to reorder, as the module editor does."""
    quote = engine.create_quote(room, {"deal_id": _deal(store, room)}, source="t", actor="t")[
        "quote"
    ]
    served = engine.update_quote(
        quote["id"],
        {
            "modules": [
                {"key": "totals", "visible": False},
                {"key": "header"},
                {"key": "terms", "visible": False},
            ]
        },
        source="t",
        actor="t",
    )["quote"]
    assert [module["key"] for module in served["modules"]] == ["totals", "header", "terms"]
    assert [module["visible"] for module in served["modules"]] == [False, True, False]


def test_a_module_kind_outside_the_vocabulary_is_a_malformed_request(engine, store, room):
    """422 rather than 409: nothing about the request is a conflict."""
    quote = engine.create_quote(room, {"deal_id": _deal(store, room)}, source="t", actor="t")[
        "quote"
    ]
    with pytest.raises(QuoteError) as caught:
        engine.update_quote(
            quote["id"], {"modules": [{"key": "header", "kind": "dynamic"}]}, source="t", actor="t"
        )
    assert "modules" in caught.value.errors


# --------------------------------------------------------------------------- #
# The header
# --------------------------------------------------------------------------- #


def test_a_payment_schedule_may_be_set_and_moves_the_contract_value(engine, store, room):
    """A schedule is a fact about when money moves."""
    quote = engine.create_quote(room, {"deal_id": _deal(store, room)}, source="t", actor="t")[
        "quote"
    ]
    served = engine.update_quote(
        quote["id"],
        {"payment_schedule": [{"due_on": LATER, "amount": 25000.0}]},
        source="t",
        actor="t",
    )["quote"]
    assert served["totals"]["future_payments"] == 25000.0
    assert served["totals"]["total_contract_value"] == (served["totals"]["total"] + 25000.0)


def test_a_payment_without_a_due_date_is_refused(engine, store, room):
    quote = engine.create_quote(room, {"deal_id": _deal(store, room)}, source="t", actor="t")[
        "quote"
    ]
    with pytest.raises(QuoteError) as caught:
        engine.update_quote(
            quote["id"], {"payment_schedule": [{"amount": 500.0}]}, source="t", actor="t"
        )
    assert "payment_schedule" in caught.value.errors


def test_a_quote_needs_a_title(engine, store, room):
    quote = engine.create_quote(room, {"deal_id": _deal(store, room)}, source="t", actor="t")[
        "quote"
    ]
    with pytest.raises(QuoteError) as caught:
        engine.update_quote(quote["id"], {"title": "  "}, source="t", actor="t")
    assert "title" in caught.value.errors


def test_a_malformed_expiration_date_is_refused_on_edit_too(engine, store, room):
    quote = engine.create_quote(room, {"deal_id": _deal(store, room)}, source="t", actor="t")[
        "quote"
    ]
    with pytest.raises(QuoteError) as caught:
        engine.update_quote(quote["id"], {"expires_on": "next tuesday"}, source="t", actor="t")
    assert "expires_on" in caught.value.errors


def test_a_quote_past_its_expiration_date_says_so(engine, store, room):
    yesterday = (NOW - timedelta(days=1)).date().isoformat()
    quote = engine.create_quote(
        room, {"deal_id": _deal(store, room), "expires_on": yesterday}, source="t", actor="t"
    )["quote"]
    assert quote["expired"] is True


def test_a_quote_due_to_expire_today_is_not_expired(engine, store, room):
    """The date is compared as a date. A quote due to expire today is live
    today."""
    quote = engine.create_quote(
        room, {"deal_id": _deal(store, room), "expires_on": TODAY}, source="t", actor="t"
    )["quote"]
    assert quote["expired"] is False


# --------------------------------------------------------------------------- #
# Publish
# --------------------------------------------------------------------------- #


def test_publishing_writes_the_contract_value_onto_the_deal_amount(store, engine, room):
    """The write-back the research describes."""
    deal_id = _deal(store, room)
    quote = engine.create_quote(room, {"deal_id": deal_id}, source="t", actor="t")["quote"]
    outcome = engine.publish(quote["id"], source="t", actor="t")
    deal = store.get(deal_id)
    assert deal["data"]["amount"] == outcome["quote"]["totals"]["total_contract_value"]
    assert deal["data"]["amount_source"] == "quote_tcv"
    assert deal["data"]["published_quote_id"] == quote["id"]


def test_publishing_copies_only_the_contract_value_and_not_the_other_totals(store, engine, room):
    """The research is narrow on purpose: "only the TCV is copied back to the deal
    amount".

    Copying subtotal, discount or tax onto the deal would overwrite the deal's
    own amount semantics with a document's arithmetic.
    """
    deal_id = _deal(store, room)
    quote = engine.create_quote(room, {"deal_id": deal_id}, source="t", actor="t")["quote"]
    engine.update_quote(
        quote["id"],
        {"payment_schedule": [{"due_on": LATER, "amount": 9000.0}]},
        source="t",
        actor="t",
    )
    totals = engine.read_quote(quote["id"])["totals"]
    assert totals["total_contract_value"] != totals["total"]

    engine.publish(quote["id"], source="t", actor="t")
    written = store.get(deal_id)["data"]
    assert written["amount"] == totals["total_contract_value"]
    for figure in ("subtotal", "discount", "tax", "total", "future_payments"):
        assert figure not in written, f"{figure} was copied onto the deal"


def test_publishing_replaces_the_deal_lines_with_fresh_ones_carrying_the_quote_line_ids(
    store, engine, room
):
    """Replace, and every replacement row has its own record id.

    The research: "the quote's line items replace the deal's line items (deal and
    quote line items get different record IDs)".
    """
    deal_id = _deal(store, room)
    original = {
        row["id"] for row in store.find("crm_deal_line_item", {"deal_id": deal_id}, limit=10)
    }
    quote = engine.create_quote(room, {"deal_id": deal_id}, source="t", actor="t")["quote"]
    quote_line_ids = {line["id"] for line in quote["line_items"]}

    outcome = engine.publish(quote["id"], source="t", actor="t")
    assert outcome["replaced_deal_line_items"] == 3

    live = store.find("crm_deal_line_item", {"deal_id": deal_id}, limit=10)
    live_ids = {row["id"] for row in live}
    assert live_ids.isdisjoint(original), "an original deal line survived the replace"
    assert live_ids.isdisjoint(quote_line_ids), "a deal line reused a quote line's record id"
    assert {row["data"]["quote_line_item_id"] for row in live} == quote_line_ids


def test_a_replaced_deal_line_is_soft_deleted_so_the_audit_trail_survives(store, engine, room):
    deal_id = _deal(store, room)
    original = store.find("crm_deal_line_item", {"deal_id": deal_id}, limit=1)[0]
    quote = engine.create_quote(room, {"deal_id": deal_id}, source="t", actor="t")["quote"]
    engine.publish(quote["id"], source="t", actor="t")
    assert store.get(original["id"]) is None
    assert store.db.get(original["id"], include_deleted=True) is not None


def test_a_published_quote_is_marked_published_and_stamped(store, engine, room):
    deal_id = _deal(store, room)
    quote = engine.create_quote(room, {"deal_id": deal_id}, source="t", actor="t")["quote"]
    served = engine.publish(quote["id"], source="t", actor="t")["quote"]
    assert served["status"] == vocab.STATUS_PUBLISHED
    assert served["published_at"] == NOW.isoformat(timespec="seconds")
    assert served["deal_amount_written"] == served["totals"]["total_contract_value"]


def test_publishing_twice_is_refused(store, engine, room):
    """Otherwise the write-back runs twice and the deal's lines are replaced by a
    second generation."""
    deal_id = _deal(store, room)
    quote = engine.create_quote(room, {"deal_id": deal_id}, source="t", actor="t")["quote"]
    engine.publish(quote["id"], source="t", actor="t")
    with pytest.raises(QuoteConflict) as caught:
        engine.publish(quote["id"], source="t", actor="t")
    assert caught.value.reason == vocab.REASON_ALREADY_PUBLISHED


def test_publishing_an_expired_quote_is_refused(store, engine, room):
    """A buyer cannot be held to a date that has passed."""
    deal_id = _deal(store, room)
    yesterday = (NOW - timedelta(days=1)).date().isoformat()
    quote = engine.create_quote(
        room, {"deal_id": deal_id, "expires_on": yesterday}, source="t", actor="t"
    )["quote"]
    with pytest.raises(QuoteConflict) as caught:
        engine.publish(quote["id"], source="t", actor="t")
    assert caught.value.reason == vocab.REASON_QUOTE_EXPIRED
    assert store.get(deal_id)["data"]["amount"] == 24000.0


def test_publishing_a_quote_with_no_lines_is_refused(store, engine, room):
    """Its contract value is zero, so publishing it would write zero onto the
    deal. That is how a deal's amount silently becomes nothing."""
    deal = store.create(
        "crm_deal", {"name": "Bare deal", "amount": 5000.0}, room_id=room, actor="t", source="t"
    )
    quote = engine.create_quote(room, {"deal_id": deal["id"]}, source="t", actor="t")["quote"]
    with pytest.raises(QuoteConflict) as caught:
        engine.publish(quote["id"], source="t", actor="t")
    assert caught.value.reason == vocab.REASON_QUOTE_IS_EMPTY
    assert store.get(deal["id"])["data"]["amount"] == 5000.0


def test_a_published_quote_will_not_accept_a_line_change(store, engine, room):
    """It is what the buyer agreed to, and the deal amount was computed from it."""
    deal_id = _deal(store, room)
    quote = engine.create_quote(room, {"deal_id": deal_id}, source="t", actor="t")["quote"]
    line_id = quote["line_items"][0]["id"]
    engine.publish(quote["id"], source="t", actor="t")
    with pytest.raises(QuoteConflict) as caught:
        engine.update_line_item(line_id, {"quantity": 999}, source="t", actor="t")
    assert caught.value.reason == vocab.REASON_QUOTE_FROZEN
    with pytest.raises(QuoteConflict) as caught:
        engine.delete_line_item(line_id, source="t", actor="t")
    assert caught.value.reason == vocab.REASON_QUOTE_FROZEN


def test_a_published_quote_still_accepts_a_payment_schedule_change(store, engine, room):
    """A schedule is when money moves, not what the buyer was quoted, so it is not
    frozen. A line item is frozen."""
    deal_id = _deal(store, room)
    quote = engine.create_quote(room, {"deal_id": deal_id}, source="t", actor="t")["quote"]
    engine.publish(quote["id"], source="t", actor="t")
    served = engine.update_quote(
        quote["id"],
        {"payment_schedule": [{"due_on": LATER, "amount": 500.0}]},
        source="t",
        actor="t",
    )["quote"]
    assert served["totals"]["future_payments"] == 500.0


def test_a_deal_with_no_line_items_yields_a_quote_with_no_line_items(store, engine, room):
    """The alternative to Dynamics' Get Products: the seller adds the lines."""
    deal = store.create("crm_deal", {"name": "Bare deal"}, room_id=room, actor="t", source="t")
    created = engine.create_quote(room, {"deal_id": deal["id"]}, source="t", actor="t")
    assert created["cloned_line_items"] == 0
    assert created["quote"]["line_items"] == []


def test_line_items_embedded_in_the_deal_record_are_read_too(store, engine, room):
    """Some mirrors hold the array inside the deal.

    Refusing a deal that carries its own lines would fail on a shape this
    repository already produces.
    """
    deal = store.create(
        "crm_deal",
        {
            "name": "Embedded deal",
            "line_items": [
                {"name": "Widget", "quantity": 2, "unit_price": 100.0, "tax_rate": 5.0},
                {"name": "Support", "quantity": 1, "unit_price": 50.0},
            ],
        },
        room_id=room,
        actor="t",
        source="t",
    )
    quote = engine.create_quote(room, {"deal_id": deal["id"]}, source="t", actor="t")["quote"]
    assert [line["name"] for line in quote["line_items"]] == ["Widget", "Support"]
    # 2 x 100.00 at 5 percent tax is 210.00, and 1 x 50.00 untaxed is 50.00.
    assert quote["totals"]["total"] == 260.0


def test_a_deal_is_found_by_the_crm_id_rather_than_the_record_id(store, engine, room):
    """A caller holding a CRM id is the common case, and this product's record id
    is not the CRM's."""
    deal = store.create(
        "crm_deal",
        {"name": "By CRM id", "crm_id": "deal-9911"},
        room_id=room,
        actor="t",
        source="t",
    )
    quote = engine.create_quote(room, {"deal_id": "deal-9911"}, source="t", actor="t")["quote"]
    assert quote["deal_id"] == deal["id"]


def test_a_deal_is_found_through_an_alias_collection(store, engine, room):
    """A workflow that mirrors a deal as an opportunity must still be readable."""
    deal = store.create("crm_opportunity", {"name": "Aliased"}, room_id=room, actor="t", source="t")
    quote = engine.create_quote(room, {"deal_id": deal["id"]}, source="t", actor="t")["quote"]
    assert quote["deal_collection"] == "crm_opportunity"


# --------------------------------------------------------------------------- #
# The summary and the contract this module is held to
# --------------------------------------------------------------------------- #


def test_the_summary_counts_drafts_published_and_expiry_separately(store, engine, room):
    """The board's headline numbers, in the states the research names."""
    deal_id = _deal(store, room)
    live = engine.create_quote(room, {"deal_id": deal_id}, source="t", actor="t")["quote"]
    engine.create_quote(room, {"deal_id": deal_id, "expires_on": TODAY}, source="t", actor="t")
    engine.create_quote(
        room,
        {"deal_id": deal_id, "expires_on": (NOW - timedelta(days=1)).date().isoformat()},
        source="t",
        actor="t",
    )
    engine.publish(live["id"], source="t", actor="t")
    counted = engine.summary()
    assert counted["quotes"] == 3
    assert counted["published"] == 1
    assert counted["drafts"] == 2
    assert counted["expired"] == 1
    assert counted["catalogue_available"] is False


def test_the_summary_reports_a_line_that_priced_at_zero(store, engine, room):
    """A line with nothing to price from is a line a seller has to fix, and the
    board should say how many there are."""
    deal = store.create("crm_deal", {"name": "Unpriced"}, room_id=room, actor="t", source="t")
    store.create(
        "crm_deal_line_item",
        {"deal_id": deal["id"], "position": 0, "name": "Thing", "quantity": 1, "unit_price": 0.0},
        room_id=room,
        actor="t",
        source="t",
    )
    quote = engine.create_quote(room, {"deal_id": deal["id"]}, source="t", actor="t")["quote"]
    assert engine.summary()["zero_priced_lines"] == 1
    assert quote["line_items"][0]["unit_price"] == 0.0


def test_quotes_are_listed_oldest_first(store, engine, room):
    """A board read newest-first would reorder itself between two reads."""
    deal_id = _deal(store, room)
    first = engine.create_quote(room, {"deal_id": deal_id, "title": "One"}, source="t", actor="t")
    engine.create_quote(room, {"deal_id": deal_id, "title": "Two"}, source="t", actor="t")
    assert [q["title"] for q in engine.list_quotes()] == ["One", "Two"]
    assert engine.list_quotes(status=vocab.STATUS_DRAFT)[0]["title"] == "One"
    assert len(first["quote"]["line_items"]) == 3


def test_reading_a_quote_that_does_not_exist_says_nothing_about_whether_it_does(store, engine):
    with pytest.raises(QuoteNotFound):
        engine.read_quote("no-such-quote")


def test_the_feature_module_imports_without_the_app_and_exports_the_contract(store):
    """The feature's shape, asserted against the module rather than a literal.

    ``backend/dsr/features/test_features.py`` already refuses a feature that
    imports ``dsr.api`` across the whole package; this is the positive half, that
    the module this ticket names exists and carries what the host reads.
    """
    module = load_feature(MODULE)
    assert module.FEATURE["ticket"] == "WF-086"
    assert module.FEATURE["id"] == "wf-086-author-a-quote-from-a-deal-or-opportunity"
    assert module.router.prefix == "/api/wf-086"
    assert set(module.EXCEPTION_HANDLERS) == {QuoteError, QuoteConflict, QuoteNotFound}
    assert callable(module.seed)


def test_every_audit_row_this_feature_writes_names_a_route_the_app_serves(store, room):
    """The guarantee that makes the audit log worth reading.

    The source is built from ``router.prefix`` at every call site, so it cannot
    name a route the module stopped serving. This asserts that against the
    router's own table rather than against a literal, so renaming a path fails
    here too, and it reads the row back out of the audit log rather than
    asserting on the string the route would have passed.
    """
    module = load_feature(MODULE)
    served = {
        (method, route.path)
        for route in module.router.routes
        for method in (getattr(route, "methods", None) or set())
    }
    assert ("POST", "/api/wf-086/quotes") in served
    assert ("POST", "/api/wf-086/quotes/{quote_id}/publish") in served

    engine = quotes.QuoteEngine(store, now=lambda: NOW)
    engine.create_quote(
        room,
        {"deal_id": _deal(store, room)},
        source=f"POST {module.router.prefix}/quotes",
        actor="test",
    )
    written = {entry["source"] for entry in store.audit(limit=50)}
    assert f"POST {module.router.prefix}/quotes" in written
    assert ("POST", f"{module.router.prefix}/quotes") in served


def test_the_domain_module_imports_nothing_but_the_store():
    """The dependency direction, asserted on the source.

    An enforced test elsewhere refuses ``dsr.api`` and bare ``sqlite3`` across the
    feature package. This is the domain half: no framework at all, so the rules
    are unit-testable without a server.
    """
    from pathlib import Path

    import dsr.quote_authoring as package

    root = Path(package.__file__).parent
    for path in sorted(root.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        assert "import fastapi" not in text, path.name
        assert "from fastapi" not in text, path.name
        assert "sqlite3" not in text, path.name
        assert "dsr.api" not in text, path.name


# --------------------------------------------------------------------------- #
# The contract three merged readers depend on
# --------------------------------------------------------------------------- #


def test_the_quote_is_written_under_the_name_the_merged_readers_look_for():
    """Three packages already on main read this workflow's rows and write none.

    ``dsr/quoting_proposals`` (WF-093) sets ``QUOTES = "wf086_quote"`` and
    ``LINE_ITEMS = "wf086_line_item"``; ``quote_expiry_vocabulary`` and
    ``renewal_quotes`` read the same two names. Each says in its own source that
    WF-086 provisions them. A different spelling here would leave three merged
    readers pointing at a collection nobody writes, and every one of them raises
    a named not-found error rather than an empty page, so the disagreement would
    look like missing data rather than like a naming mismatch.
    """
    assert vocab.QUOTE_COLLECTION == "wf086_quote"
    assert vocab.LINE_ITEM_COLLECTION == "wf086_line_item"


def test_a_quote_is_readable_by_a_merged_reader(engine, store, room):
    """The shape WF-093's engine binds against, checked field by field.

    Its quote view reads ``deal_name or deal``, ``company_name or company``,
    ``currency_label or currency`` and ``expiration_date``. This workflow writes
    ``account``, ``currency`` and ``expires_on``, so without the second spelling
    of each, a proposal built from this quote renders a blank counterparty and no
    expiry. The assertion is against the field names that reader uses, not against
    this workflow's own vocabulary.
    """
    quote = engine.create_quote(
        room,
        {"deal_id": _deal(store, room), "expires_on": LATER},
        source="t",
        actor="t",
    )["quote"]
    assert quote["deal_name"] == "Northwind renewal"
    assert quote["company_name"] == "Northwind Traders"
    assert quote["currency_label"] == "EUR"
    assert quote["expiration_date"] == LATER
    # And the names this workflow's own page and routes use are still there.
    assert quote["account"] == quote["company_name"]
    assert quote["currency"] == quote["currency_label"]
    assert quote["expires_on"] == quote["expiration_date"]


def test_moving_the_expiration_date_moves_both_spellings(engine, store, room):
    """Otherwise the two drift and a proposal expires on a date the seller moved.

    The two names are duplicated values rather than aliases, so the only thing
    bounding the drift is that one write sets both. That is the property here.
    """
    quote = engine.create_quote(
        room, {"deal_id": _deal(store, room), "expires_on": LATER}, source="t", actor="t"
    )["quote"]
    assert quote["expiration_date"] == quote["expires_on"]
    moved = (NOW + timedelta(days=90)).date().isoformat()
    served = engine.update_quote(quote["id"], {"expires_on": moved}, source="t", actor="t")["quote"]
    assert served["expires_on"] == moved
    assert served["expiration_date"] == moved


def test_a_line_item_carries_the_reference_a_merged_reader_matches_on(engine, store, room):
    """WF-093 matches on any of ``quote_id``, ``quote``, ``parent_id`` or
    ``quote_record_id``, and sorts on ``position`` then ``name``.

    It reads every row in the collection rather than filtering, so a line item in
    the right collection without one of those keys would be dropped from every
    proposal this workflow produced.
    """
    quote = engine.create_quote(room, {"deal_id": _deal(store, room)}, source="t", actor="t")[
        "quote"
    ]
    for line in quote["line_items"]:
        assert line["quote_id"] == quote["id"]
        assert isinstance(line["position"], int)
        assert line["name"]
    assert [line["position"] for line in quote["line_items"]] == sorted(
        line["position"] for line in quote["line_items"]
    )


def test_the_package_lives_beside_the_merged_one_and_not_inside_it():
    """The rename, asserted so it cannot be undone by a later move.

    ``dsr/quoting_proposals`` belongs to WF-093 and is merged, green and
    untouched. This workflow's package is ``dsr/quote_authoring``. The ruling is
    audit ``jev-20261005T080141-11308-01406``, which chose
    ``rename_wf086_package`` at confidence 1.00 against folding at 0.00.
    """
    from pathlib import Path

    import dsr.quote_authoring as package

    root = Path(package.__file__).resolve().parent
    assert root.name == "quote_authoring"
    assert root.parent.name == "dsr"
    # The merged package is a sibling, and this package names neither it nor any
    # module inside it. A cross-import would be the coupling the rename avoided.
    for path in sorted(root.glob("*.py")):
        assert "dsr.quoting_proposals" not in path.read_text(encoding="utf-8"), path.name
