"""Every judgement call this workflow rests on, served as data.

The research leaves gaps. Where it does, this build chose a rule, and every one of
those choices is recorded here with the quote it came from and the audit id that
settled it. ``GET /inferences`` serves this file, so a reviewer reads the decisions
without reading the code, and a later workflow can see what it must not re-decide.

Two kinds of entry are recorded, and the distinction is the point:

* **Decided.** A gap the research left, closed by a derivation plus a Jev audit.
  The rule is implemented. Cite the audit id when arguing with it.
* **Not built.** A thing the research names that this workflow deliberately does
  not implement. Nothing is quietly dropped: each entry says where the work went
  and why building it here would be wrong.

The workflow this one waits for, and the ones that wait for it, are recorded too.
Two issues, 192 and 196, name WF-087 as the workflow that provisions the product,
price-list and price-list-item records they read.
"""

from __future__ import annotations

from typing import Any

#: The audit ids this workflow's rules rest on, newest last. Kept as data so the
#: route that serves them cannot drift from the module docstrings that cite them.
AUDITS: dict[str, str] = {
    "collections": "jev-20261013T111835-7396-15231",
    "tier_arithmetic": "jev-20261013T111921-7676-61571",
    "tier_maximum": "jev-20261013T111921-7676-61571",
    "override_precedence": "jev-20261013T111944-8144-84487",
    "retirement": "jev-20261013T111944-8144-84487",
    "currency": "jev-20261013T112401-10796-41499",
    "bundle": "jev-20261013T112401-10796-41499",
}

DECIDED: tuple[dict[str, str], ...] = (
    {
        "id": "collections",
        "question": (
            "Which collections does the catalogue live in, given that already-merged "
            "sibling workflows read it under names they guessed before this workflow "
            "shipped?"
        ),
        "gap": (
            "Three merged workflows name the catalogue differently. "
            "quoting_proposals reads wf087_product. quote_authoring reads crm_product. "
            "price_book_vocabulary reads crm_price_book. Each of those docstrings says "
            "WF-087 had not shipped, so each is a guess."
        ),
        "decision": ("Write only wf087_product, wf087_price_book and wf087_price_book_item."),
        "why": (
            "Every merged feature namespaces its collections with its ticket, so these "
            "are the names the convention produces. A mirror written for the guessed "
            "names keeps degrading to an empty catalogue, and widening those alias "
            "lists is an edit to files this workflow does not own."
        ),
        "rejected": (
            "Writing a second projection into crm_product and crm_price_book so the "
            "siblings resolve today. It doubles the write count and the audit rows to "
            "serve a naming mistake in other files, and the two copies could drift."
        ),
        "audit": AUDITS["collections"],
        "confidence": 0.97,
    },
    {
        "id": "tier_arithmetic",
        "question": (
            "The research names volume-based, graduated and stair-step and quotes one "
            "sentence each. What is the arithmetic?"
        ),
        "gap": (
            "The quoted evidence is three sentences and no formula. Volume-based is "
            "the entire line item priced on the total quantity bought. Graduated is "
            "units priced per tier. Stair-step is one flat fee charged on the quantity "
            "range."
        ),
        "decision": (
            "Volume-based prices every unit at the one rate the total quantity reaches. "
            "Graduated prices each unit at the rate of the range its own position falls "
            "in. Stair-step charges the reached range's fee once, not multiplied by the "
            "quantity."
        ),
        "why": (
            "Tiers 1-9 at 100, 10-24 at 80 and 25 upward at 60 give a quantity of 12 "
            "three different figures: 960, 1140 and the 10-24 fee. Each quoted sentence "
            "describes exactly one of them and no two describe the same arithmetic."
        ),
        "rejected": (
            "Treating volume-based and stair-step as the same method differing only in "
            "wording. Stair-step's sentence says one flat fee, which does not scale with "
            "quantity, and volume-based's says the entire line item, which does."
        ),
        "audit": AUDITS["tier_arithmetic"],
        "confidence": 1.0,
    },
    {
        "id": "tier_maximum",
        "question": (
            "The research says a tier's maximum auto-computes and that infinity is the "
            "maximum creating an open-ended final tier. Computed how?"
        ),
        "gap": "The research states that the maximum is derived and never says the rule.",
        "decision": (
            "A tier's maximum is the next tier's minimum minus one. The final tier has "
            "no maximum and reads as an open-ended range. A quantity below the first "
            "minimum prices at the first tier's rate."
        ),
        "why": (
            "Two authored numbers can disagree, and when they do the range a quantity "
            "falls in is ambiguous: a buyer ordering 24 is charged one price or another "
            "depending on which number is trusted. Deriving the maximum means the two "
            "cannot disagree."
        ),
        "rejected": (
            "Letting the author type both a minimum and a maximum and refusing a pair "
            "that leaves a gap or an overlap. That keeps the contradiction and adds a "
            "validation rule to catch it."
        ),
        "audit": AUDITS["tier_maximum"],
        "confidence": 1.0,
    },
    {
        "id": "override_precedence",
        "question": "In what order is a unit price chosen when a price book applies?",
        "gap": "The data flow says the book layer overrides the product default, but not what happens when the book has no row.",
        "decision": (
            "An active price-book row with a price or a tier table of its own wins. "
            "Otherwise the product tier table is evaluated against the quantity. "
            "Otherwise the product flat price in the wanted currency is used. "
            "Otherwise there is no price and the source is reported as missing."
        ),
        "why": (
            "The vendor states it: the source product is never modified by price book "
            "operations, and the knowledge base calls a book row an override of a "
            "product's default price within a price book. So the product is the default "
            "and the row is the override."
        ),
        "rejected": (
            "Refusing to price a product the assigned book has no row for. A book is a "
            "segment and a region, not a whitelist, and a buyer in that segment must "
            "still be able to buy the catalogue."
        ),
        "audit": AUDITS["override_precedence"],
        "confidence": 0.94,
    },
    {
        "id": "currency",
        "question": (
            "The research quotes a Currencies dropdown and says one currency per price "
            "book. What happens to a product with no price in it?"
        ),
        "gap": (
            "The quoted sentence is from the knowledge-base article, which describes "
            "the screen. The vendor API reference types supportedCurrencies as an array "
            "and works through it with a book carrying USD and CAD. The two disagree, "
            "and the docs say nothing at all about what a row renders as when its "
            "currency is missing."
        ),
        "decision": (
            "A price book names a list of supported currencies, at least one. A row "
            "may carry a price in only some of them; the missing ones are recorded as "
            "gaps, the book cannot be activated while any gap exists, and a gap is "
            "skipped when a line is priced so the product's own price stands. No "
            "currency is ever converted and no missing price is read as zero."
        ),
        "why": (
            "The vendor validates that every product has a price for each of the book's "
            "supported currencies and blocks activation with "
            "PRICE_BOOK_PRODUCTS_MISSING_PRICES. It never mentions an exchange rate in "
            "the price-book guide, so a converted price would be a number this product "
            "could not explain to a buyer."
        ),
        "rejected": (
            "Refusing the save of an incomplete row. The vendor blocks activation "
            "rather than the save, and an administrator building a book row by row "
            "needs the incomplete rows to exist to be able to complete them."
        ),
        "audit": AUDITS["currency"],
        "confidence": 1.0,
    },
    {
        "id": "retirement",
        "question": "How does retiring a product differ from deleting it?",
        "gap": "The research names the Active toggle and says a retired product is not deleted. It does not say what delete means.",
        "decision": (
            "Active off keeps the record and its history and stops the product being "
            "offered on new work. Delete is a soft delete through the audited store, "
            "which takes the row out of the catalogue and leaves the audit trail "
            "intact. Removing a product removes its price-book rows in the same "
            "transaction."
        ),
        "why": (
            "The quote is toggle Active off to retire a product from deals, quotes and "
            "subscriptions without deleting it. So retirement is a flag and deletion is "
            "a separate act, and a product removed out from under a live price book "
            "would leave that book naming a product that does not exist."
        ),
        "rejected": (
            "Making retirement the only way a product leaves the catalogue and refusing "
            "a delete. The store soft-deletes and audits as a matter of design, and "
            "refusing it here would leave no way to correct a product created in error."
        ),
        "audit": AUDITS["retirement"],
        "confidence": 0.97,
    },
    {
        "id": "bundle",
        "question": "How does a bundle price inside a price book?",
        "gap": "The research names a bundle item type and says nothing about how it prices.",
        "decision": (
            "A bundle cannot be added to a price book. The product type round-trips so a "
            "mirror of a real catalogue reads, and nothing more is built."
        ),
        "why": (
            "The vendor refuses it outright: bundle products cannot be added to a price "
            "book, error BUNDLE_PRODUCTS_NOT_ALLOWED. So there is nothing to price, and "
            "guessing a bundle total would invent an arithmetic rule no source states."
        ),
        "rejected": (
            "Pricing a bundle as the sum of its members. No source describes the members "
            "or their combination, and a wrong bundle total is worse than a refusal."
        ),
        "audit": AUDITS["bundle"],
        "confidence": 1.0,
    },
)

#: Named by the research, deliberately not built here, and where each went.
NOT_BUILT: tuple[dict[str, str], ...] = (
    {
        "id": "price_book_auto_assignment",
        "what": "Assigning a price book to a deal when the deal is created.",
        "evidence": (
            "The research says price book auto-assignment assigns a book at deal "
            "creation, and points at workflow 3 for the rule."
        ),
        "why_not_here": (
            "The issue for this workflow says the spec names no ticket for the rule and "
            "does not define the filter, and says do not build assignment here. Guessing "
            "the filter would collide with the workflow that owns it."
        ),
        "goes_to": "The workflow that owns price book assignment rules.",
    },
    {
        "id": "bulk_import",
        "what": "Importing flat-rate products from a file.",
        "evidence": (
            "The research says import bulk-imports flat-rate products and that tier "
            "priced products cannot be imported."
        ),
        "why_not_here": (
            "This workflow enforces the rule, because the rule is a property of a "
            "product row and every save can check it. The importer itself reads a file "
            "and belongs to the workflow that owns file upload."
        ),
        "goes_to": "The workflow that owns file upload and import.",
    },
    {
        "id": "currency_conversion",
        "what": "Converting a price between currencies.",
        "evidence": "The research names an account currency list and per-currency price fields.",
        "why_not_here": (
            "The vendor copies a price per currency into a price book and never mentions "
            "an exchange rate in the price-book guide. Exchange rates are documented for "
            "deals. A converted price would be a number this product cannot explain to a "
            "buyer."
        ),
        "goes_to": "The workflow that owns transaction currency, which exists.",
    },
    {
        "id": "product_images",
        "what": "Uploading and browsing product images from the Files tool.",
        "evidence": "The research names an image field on the product and the Files tool.",
        "why_not_here": (
            "The image_url field round-trips so a mirror of a real product reads. File "
            "storage and browsing belong to the workflow that owns the Files tool."
        ),
        "goes_to": "The workflow that owns file storage.",
    },
)

#: The workflows whose records this one reads and writes.
DEPENDS_ON: tuple[dict[str, str], ...] = ()

#: The workflows that name this one as the workflow that provisions their records.
WAITS_FOR_THIS: tuple[dict[str, str], ...] = (
    {
        "ticket": "WF-101",
        "issue": 192,
        "name": "Convert an accepted quote into an order and lock the price",
        "needs": (
            "It names WF-087 as the workflow that provisions the product, price list and "
            "price list item records the order carries forward, and the price-lock rule "
            "has nothing to lock against without them."
        ),
    },
    {
        "ticket": "WF-102",
        "issue": 196,
        "name": "Revise a quote to create a new revision instead of losing the deal",
        "needs": (
            "It names WF-087 as the workflow that provisions the priced catalogue its "
            "revision re-prices from."
        ),
    },
)

#: The shape this workflow's rows take, for a workflow that reads them as data.
#: Every field another workflow is documented to look for is named here.
RECORD_SHAPE: dict[str, Any] = {
    "product": {
        "collection": "wf087_product",
        "fields": {
            "name": "str",
            "sku": "str",
            "description": "str",
            "tax_category": "str",
            "billing_frequency": "one_time | recurring",
            "product_type": "regular | bundle",
            "active": "bool, false retires the product",
            "pricing_method": "flat | volume_based | graduated | stair_step",
            "prices": "{CURRENCY: number}, the flat price per currency",
            "currency": "str, the currency a tiered product is priced in",
            "tiers": "[{min_qty, max_qty, unit_price, fee}], max_qty null on the last",
        },
        "read_as": (
            "tiers also read under price_tiers and quantity_tiers; a threshold also "
            "reads under min_quantity and from; a tier price also reads under price, "
            "amount and value; a flat price also reads under unit_price. These are the "
            "spelling aliases an already-merged sibling workflow accepts."
        ),
    },
    "price_book": {
        "collection": "wf087_price_book",
        "fields": {
            "name": "str",
            "description": "str",
            "supported_currencies": "[str], at least one three-letter code",
            "segment": "str, the buyer segment this book targets",
            "region": "str, the region this book targets",
            "active": "bool, false keeps the book but stops it offering its rows",
        },
    },
    "price_book_item": {
        "collection": "wf087_price_book_item",
        "fields": {
            "product_id": "str, the id of a wf087_product record",
            "price_book_id": "str, the id of a wf087_price_book record",
            "prices": "{CURRENCY: number}, the override per currency",
            "billing_frequency": "one_time | recurring",
            "term": "str",
            "tiers": "[{min_qty, max_qty, unit_price, fee}], overrides the product's own",
            "state": "active | inactive",
        },
    },
}


def describe() -> dict[str, Any]:
    """Every judgement call, every exclusion and the record shape, as data.

    Served by ``GET /inferences``. The route returns this and nothing else, so the
    reviewable decisions live in one place that a person can read without
    importing anything.
    """

    return {
        "decided": [dict(entry) for entry in DECIDED],
        "not_built": [dict(entry) for entry in NOT_BUILT],
        "depends_on": [dict(entry) for entry in DEPENDS_ON],
        "waits_for_this": [dict(entry) for entry in WAITS_FOR_THIS],
        "record_shape": {name: dict(shape) for name, shape in RECORD_SHAPE.items()},
        "audits": dict(AUDITS),
    }
