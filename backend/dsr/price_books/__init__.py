"""WF-087: the product and price-book catalogue, and the arithmetic behind it.

The workflow an admin performs in the research is: build a library of products,
give each one a flat price or a tier table, then group products into price books
that override the default price for one buyer segment or region. Quote lines then
resolve a price by evaluating the tier table against the quantity and letting the
price-book layer override the result.

What this package is
--------------------

Four modules, each with one job:

``vocabulary``
    The collections, the enumerations, the field descriptions and the refusal
    messages. No rule lives here.
``errors``
    The two exception types this domain raises. Neither is a builtin, because
    the feature host attaches one handler per type and a handler for
    ``ValueError`` would intercept that exception across the whole product.
``rules``
    The pricing arithmetic over plain values. No store, no HTTP, no clock.
``engine``
    The one module that touches the store. Every write passes an ``actor`` and a
    ``source`` naming the route that caused it, and this package imports nothing
    from ``dsr`` except :mod:`dsr.store`.

What this package deliberately does not build
---------------------------------------------

**Price-book auto-assignment.** The research says "price book auto-assignment
assigns a book at deal creation" and names no ticket and no filter for the rule.
Building a guess here would collide with the workflow that owns it, so it is
recorded as out of scope in :mod:`dsr.price_books.inferences` and nowhere else.

**Bundle pricing.** The vendor refuses a bundle in a price book outright
(``BUNDLE_PRODUCTS_NOT_ALLOWED``), and the research says nothing about how a
bundle prices. The type round-trips so a mirror of a real catalogue reads, and
nothing more is built.

**Currency conversion.** The vendor copies a price per currency and never
converts. A converted price would be a number this product could not explain to a
buyer.

**Import.** The research states the rule and this workflow enforces it, because
it is a property of a product row: "tier priced products cannot be imported". It
does not build the CSV importer, which belongs to the workflow that owns file
upload.
"""

from __future__ import annotations

from dsr.price_books.engine import CatalogueEngine
from dsr.price_books.errors import CatalogueNotFound, CatalogueRefusal, refuse
from dsr.price_books.rules import (
    as_number,
    book_currencies,
    book_item_price,
    effective_unit_price,
    is_bundle,
    is_importable,
    is_tiered,
    missing_currencies,
    money,
    normalise_billing,
    normalise_book_currencies,
    normalise_currency,
    normalise_pricing_method,
    price_in_currency,
    price_line,
    price_map,
    require_money,
    require_quantity,
    select_tier,
    summary_row,
    tier_label,
    tiers,
)

__all__ = [
    "CatalogueEngine",
    "CatalogueNotFound",
    "CatalogueRefusal",
    "as_number",
    "book_currencies",
    "book_item_price",
    "effective_unit_price",
    "is_bundle",
    "is_importable",
    "is_tiered",
    "missing_currencies",
    "money",
    "normalise_billing",
    "normalise_book_currencies",
    "normalise_currency",
    "normalise_pricing_method",
    "price_in_currency",
    "price_line",
    "price_map",
    "refuse",
    "require_money",
    "require_quantity",
    "select_tier",
    "summary_row",
    "tier_label",
    "tiers",
]
