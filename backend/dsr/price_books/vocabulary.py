"""The words this workflow uses, and the one place each word is defined.

Nothing here decides anything. The collections, the enumerations and the error
codes live in this module so that the rules, the engine, the routes, the seed and
the tests cannot drift apart: there is exactly one definition of "a tiered
product" and one of every refusal message.

Where the words come from
-------------------------

The research quotes HubSpot, Dynamics 365 Sales and PandaDoc. This build models
the HubSpot objects, and records that choice in :data:`PLATFORM`. The reason is
in the research itself: the user flow steps 1 to 5 are the HubSpot product
builder, the pricing-configuration tabs, the Active toggle, the Manage price
books index and the line-item picker, and the price-book override rule in the data
flow is a HubSpot rule. Dynamics uses a price *list* and PandaDoc uses a *catalog
item*; both are the same shape under a different name and are recorded as
vocabulary synonyms rather than as a second implementation.

Collections
-----------

Three collections, each prefixed ``wf087_`` so this workflow can be found by its
ticket in a database that holds a hundred workflows' rows. The names were put to
Jev as a choice between them and a set of names guessed by already-merged sibling
workflows that read this catalogue, and this set was selected at confidence 0.97,
audit ``jev-20261013T111835-7396-15231``. The siblings degrade to an empty
catalogue until their own owners widen their alias lists, which is a change in
their files and not in this one.
"""

from __future__ import annotations

from typing import Any

# --------------------------------------------------------------------------- #
# Provenance
# --------------------------------------------------------------------------- #

#: The platform this build models. See the module docstring for the reason.
PLATFORM = "hubspot_products_and_price_books"

#: The research documents three platforms. Recorded so a reader can tell which
#: one this workflow copied and which two it deliberately did not.
PLATFORM_ALTERNATIVES: dict[str, str] = {
    "dynamics_365_sales": (
        "Models the same objects as the Dynamics pricelevel and pricelevelproduct "
        "tables, plus a discountlist. Dynamics adds a pricing method that computes "
        "from cost, for example margin current cost. That is a cost accounting rule "
        "and this workflow does not model it: nothing in the research names a cost "
        "field on the product."
    ),
    "pandadoc_catalog": (
        "Models the same objects as a PandaDoc product catalog item, with type "
        "regular or bundle and billing_type one_time or recurring. PandaDoc is the "
        "source of the tiers field name pair min_qty and value, which this workflow "
        "accepts as a synonym."
    ),
}

# --------------------------------------------------------------------------- #
# Collections
# --------------------------------------------------------------------------- #

#: A product in the library: name, SKU, description, tax category, billing
#: frequency, active flag, and a pricing configuration that is either flat or
#: tiered. Chosen as the name at confidence 0.97, audit
#: ``jev-20261013T111835-7396-15231``.
PRODUCTS = "wf087_product"

#: A price book: name, description, one currency, an active flag, and the
#: segment or region it targets. The research calls this "price book" in HubSpot,
#: "price list" in Dynamics and "crm_price_book" in two sibling workflows that
#: guessed before this workflow shipped.
PRICE_BOOKS = "wf087_price_book"

#: One product's row inside one price book. It carries the prices that override
#: the product defaults, the overridden billing frequency and the term, and it is
#: where a tiered configuration can override the product's own tiers.
PRICE_BOOK_ITEMS = "wf087_price_book_item"

#: Every collection this workflow writes. A test asserts the set, so adding a
#: collection without recording it here fails rather than slips through.
OWNED_COLLECTIONS: tuple[str, ...] = (PRODUCTS, PRICE_BOOKS, PRICE_BOOK_ITEMS)

# --------------------------------------------------------------------------- #
# Product status
# --------------------------------------------------------------------------- #

#: A product offered on a new quote or line item. The research names the toggle
#: Active; the default for a record a person just created is on.
ACTIVE = "active"

#: Retired. The quote is "toggle Active off to retire a product from deals,
#: quotes and subscriptions without deleting it", so the record and its history
#: stay and the product is simply not offered for new work. Chosen at confidence
#: 0.97, audit ``jev-20261013T111944-8144-84487``.
RETIRED = "retired"

PRODUCT_STATUSES: tuple[str, ...] = (ACTIVE, RETIRED)

# --------------------------------------------------------------------------- #
# Product type
# --------------------------------------------------------------------------- #

#: The HubSpot default. One thing a buyer can order.
PRODUCT_TYPE_REGULAR = "regular"

#: A set of products sold together. The research names it for PandaDoc, where a
#: catalog item carries type regular or bundle. Recorded as a vocabulary value so
#: the field round-trips; this workflow does not price a bundle by adding its
#: members, because the research says nothing about how.
PRODUCT_TYPE_BUNDLE = "bundle"

PRODUCT_TYPES: tuple[str, ...] = (PRODUCT_TYPE_REGULAR, PRODUCT_TYPE_BUNDLE)

# --------------------------------------------------------------------------- #
# Billing frequency
# --------------------------------------------------------------------------- #

#: Charged once. PandaDoc spells it one_time, which is accepted as a synonym.
BILLING_ONE_TIME = "one_time"

#: Charged each period.
BILLING_RECURRING = "recurring"

BILLING_FREQUENCIES: tuple[str, ...] = (BILLING_ONE_TIME, BILLING_RECURRING)

#: Spellings accepted on the way in, mapped onto the value above. The research
#: names "billing frequency" without fixing the words, and the CRM mirror a
#: deployment brings in may use any of these.
BILLING_SYNONYMS: dict[str, str] = {
    "one_time": BILLING_ONE_TIME,
    "onetime": BILLING_ONE_TIME,
    "one-time": BILLING_ONE_TIME,
    "once": BILLING_ONE_TIME,
    "recurring": BILLING_RECURRING,
    "subscription": BILLING_RECURRING,
    "monthly": BILLING_RECURRING,
}

# --------------------------------------------------------------------------- #
# Pricing method
# --------------------------------------------------------------------------- #
#
# The three the research names, with the arithmetic each one uses. The research
# quotes a sentence per method and no formula, so each formula below is a
# derivation from its own sentence. Chosen at confidence 1.00, audit
# ``jev-20261013T111921-7676-61571``.

#: "The entire line item is priced based on the total quantity bought." Every
#: unit is priced at the single rate of the range the total quantity reaches, so
#: the line is quantity times that rate. With tiers 1-9 at 100, 10-24 at 80 and 25
#: upward at 60, a quantity of 12 costs 960.
METHOD_VOLUME_BASED = "volume_based"

#: "Units are priced per tier." Each unit is priced at the rate of the range its
#: own position falls in, so the line is the sum of one figure per range. The
#: same tiers and a quantity of 12 cost 9 times 100 plus 3 times 80, which is
#: 1140.
METHOD_GRADUATED = "graduated"

#: "One flat fee is charged based on the quantity range." The range selects a fee
#: and the fee is charged once, not multiplied by the quantity. With a fee of
#: 800 on the 10-24 range, a quantity of 12 and a quantity of 24 both cost 800.
METHOD_STAIR_STEP = "stair_step"

#: Flat pricing. The research's first branch: "for flat rate, enter price in each
#: account currency field". No tier table takes part.
METHOD_FLAT = "flat"

PRICING_METHODS: tuple[str, ...] = (
    METHOD_FLAT,
    METHOD_VOLUME_BASED,
    METHOD_GRADUATED,
    METHOD_STAIR_STEP,
)

#: The three that need a tier table. Flat does not, and a flat product may be
#: bulk-imported while a tiered one may not.
TIERED_PRICING_METHODS: tuple[str, ...] = (
    METHOD_VOLUME_BASED,
    METHOD_GRADUATED,
    METHOD_STAIR_STEP,
)

#: Spellings accepted on the way in. The research writes "volume-based",
#: "graduated" and "stair-step" with hyphens; a person typing into a form may use
#: either, and a CRM mirror may use neither.
PRICING_METHOD_SYNONYMS: dict[str, str] = {
    "flat": METHOD_FLAT,
    "flat_rate": METHOD_FLAT,
    "flat rate": METHOD_FLAT,
    "volume": METHOD_VOLUME_BASED,
    "volume_based": METHOD_VOLUME_BASED,
    "volume-based": METHOD_VOLUME_BASED,
    "graduated": METHOD_GRADUATED,
    "tiered": METHOD_GRADUATED,
    "incremental": METHOD_GRADUATED,
    "stair_step": METHOD_STAIR_STEP,
    "stair-step": METHOD_STAIR_STEP,
    "stairstep": METHOD_STAIR_STEP,
    "stair step": METHOD_STAIR_STEP,
}

# --------------------------------------------------------------------------- #
# Tier field names
# --------------------------------------------------------------------------- #
#
# The research names the PandaDoc shape as {"min_qty", "value"} and the HubSpot
# shape as a minimum-units input with the maximum auto-computed. Already-merged
# sibling workflows read min_qty with unit_price. All four spellings are accepted
# on the way in so a catalogue written against any of them resolves, and the one
# this workflow writes is min_qty with unit_price.

#: Keys read as the tier threshold, in the order they are tried.
MIN_QUANTITY_KEYS: tuple[str, ...] = ("min_qty", "min_quantity", "from")

#: Keys read as the tier unit price, in the order they are tried.
UNIT_PRICE_KEYS: tuple[str, ...] = ("unit_price", "price", "amount", "value")

#: Keys read as a tier's flat fee, for the stair-step method. "value" is read
#: here too because the PandaDoc example uses one number per tier and cannot
#: distinguish a rate from a fee; a stair-step tier that carries a unit_price is
#: read as a fee, because the method does not scale it.
TIER_FEE_KEYS: tuple[str, ...] = ("fee", "flat_fee", "unit_price", "price", "value")

#: Keys read as the product flat price, in the order they are tried.
PRODUCT_PRICE_KEYS: tuple[str, ...] = ("unit_price", "price")

#: Keys read as the tier table, in the order they are tried. The PandaDoc example
#: spells it "tiers"; a sibling workflow also accepts the two longer spellings.
TIER_TABLE_KEYS: tuple[str, ...] = ("tiers", "price_tiers", "quantity_tiers")

#: Keys read as the product's price-per-currency map. The research's flat branch
#: is "enter price in each account currency field", so a product holds one price
#: per currency rather than a single price.
PRICE_MAP_KEYS: tuple[str, ...] = ("prices", "price_by_currency", "price_by_currency_code")

#: The field name a validation message uses when it is complaining about the one
#: price on a product that named no currency map. Named here so the message and
#: the form agree on which input it is about.
CURRENCY_KEY_FOR_PRICE = "unit_price"

#: Keys read as a product's price-book override, in the order they are tried.
PRICE_BOOK_KEYS: tuple[str, ...] = ("price_book", "price_book_id", "price_list")

# --------------------------------------------------------------------------- #
# Price-book row state
# --------------------------------------------------------------------------- #

#: The research's wording for a price-book row that is switched on: "toggle
#: Inactive off to activate". A row that is inactive is skipped and the product
#: own price is used instead.
BOOK_ITEM_ACTIVE = "active"
BOOK_ITEM_INACTIVE = "inactive"

BOOK_ITEM_STATES: tuple[str, ...] = (BOOK_ITEM_ACTIVE, BOOK_ITEM_INACTIVE)

# --------------------------------------------------------------------------- #
# Where a unit price came from
# --------------------------------------------------------------------------- #

#: A price-book row supplied the price. Highest precedence, because the data flow
#: says the book layer "overrides the product default price for the specific buyer
#: segment or region". Chosen at confidence 0.94, audit
#: ``jev-20261013T111944-8144-84487``.
SOURCE_PRICE_BOOK = "price_book"

#: The product tier table supplied the price.
SOURCE_TIER = "tier"

#: The product flat price supplied the price.
SOURCE_PRODUCT_DEFAULT = "product_default"

#: Nothing supplied a price. The caller decides what that means; the response
#: says so rather than the arithmetic raising, because an empty catalogue is this
#: workflow's absence and not a broken quote.
SOURCE_MISSING = "missing"

PRICE_SOURCES: tuple[str, ...] = (
    SOURCE_PRICE_BOOK,
    SOURCE_TIER,
    SOURCE_PRODUCT_DEFAULT,
    SOURCE_MISSING,
)

# --------------------------------------------------------------------------- #
# Currencies
# --------------------------------------------------------------------------- #
#
# The research quotes the price-book builder as a Currencies dropdown with "one
# currency per price book". The vendor's own API reference contradicts that, and
# the contradiction is recorded here rather than resolved silently.
#
# The quoted sentence comes from the knowledge-base article, which describes the
# screen: "Click the Currencies dropdown menu and select a currency"
# (knowledge.hubspot.com/products/use-price-books). The API reference describes
# the object: "supportedCurrencies | Array | An array of currency code strings
# (e.g., "USD", "CAD")" and a worked example named "Multi-currency Launchbook"
# with "USD;CAD" (developers.hubspot.com/docs/api-reference/latest/revenue/
# price-books/guide). The same guide lists the error CANNOT_REMOVE_LAST_CURRENCY,
# which only makes sense for several.
#
# So a price book here holds a LIST of supported currencies, at least one. What
# the quoted sentence got right is the direction of travel: a price book names
# its currencies, and a product priced outside every one of them has no price in
# that book.

#: The key a single currency is stored under on a product or a price-book row.
CURRENCY_FIELD = "currency"

#: The key a price book's list of supported currencies is stored under. Matches
#: the vendor's property name so a mirror of a real price book reads without a
#: translation layer.
SUPPORTED_CURRENCIES = "supported_currencies"

#: Keys read as a price book's supported-currency list, in the order they are
#: tried. A mirror of the older single-currency shape stores one code under
#: ``currency`` instead, which is read as a list of one.
BOOK_CURRENCY_LIST_KEYS: tuple[str, ...] = (SUPPORTED_CURRENCIES, "currencies", "currency")

#: Currencies a deployment commonly holds. Read as a suggestion list for a form,
#: never as a constraint: the account currency list is the deployment's to set, so
#: a currency this tuple omits is still accepted.
COMMON_CURRENCIES: tuple[str, ...] = ("USD", "EUR", "GBP", "CAD", "AUD", "INR", "JPY")

# --------------------------------------------------------------------------- #
# Field descriptions, served by GET /vocabulary
# --------------------------------------------------------------------------- #

#: What every field on a product means. The page reads this rather than
#: hard-coding labels, so a field cannot drift from its description.
PRODUCT_FIELDS: dict[str, str] = {
    "name": "The product name a buyer sees on a quote.",
    "sku": "The stock keeping unit. Unique within the catalogue.",
    "description": "What the product is and what it includes.",
    "tax_category": "The tax category the product is charged under.",
    "billing_frequency": "one_time or recurring.",
    "product_type": "regular or bundle.",
    "active": "False retires the product from new quotes without deleting it.",
    "pricing_method": "flat, volume_based, graduated or stair_step.",
    "prices": "A price per currency code, for a flat product.",
    "currency": "The currency a tiered product is priced in.",
    "tiers": "The tier table. Each tier carries min_qty and unit_price.",
    "image_url": "A product image, from the HubSpot Files tool.",
}

#: What every field on a price book means.
PRICE_BOOK_FIELDS: dict[str, str] = {
    "name": "The price book name.",
    "description": "What the book covers.",
    "supported_currencies": "The currencies this book prices in. At least one.",
    "segment": "The buyer segment the book targets.",
    "region": "The region the book targets.",
    "active": (
        "False keeps the book. A book cannot be activated until every product in "
        "it has a price in every currency the book supports."
    ),
}

#: What every field on a price-book row means.
PRICE_BOOK_ITEM_FIELDS: dict[str, str] = {
    "product_id": "The product this row prices.",
    "price_book_id": "The price book this row belongs to.",
    "prices": "The price that overrides the product default, per currency.",
    "billing_frequency": "Overrides the product billing frequency.",
    "term": "The contract term the price applies to.",
    "tiers": "A tier table that overrides the product tier table.",
    "state": "active or inactive. An inactive row is skipped.",
}

#: What every field on a tier means. ``max_units`` is here because the page shows
#: it, and it is derived rather than authored: see
#: :func:`dsr.price_books.rules.tiers`.
TIER_FIELDS: dict[str, str] = {
    "min_qty": "The lowest quantity this tier prices. The author types this.",
    "max_qty": "The highest quantity this tier prices, derived from the next "
    "tier's min_qty. The final tier has none, which is the open-ended range the "
    "research calls infinity.",
    "unit_price": "The price of one unit inside this range.",
    "fee": "The flat fee for this range. Only stair_step reads it.",
}

# --------------------------------------------------------------------------- #
# Refusals
# --------------------------------------------------------------------------- #

#: Every refusal, with the status it answers and the sentence the page shows.
#: Keyed by a stable token rather than by an English sentence, so the page can
#: branch on the token and the server can be reworded without breaking it.
ERROR_CODES: dict[str, tuple[int, str]] = {
    "product_needs_a_name": (
        422,
        "Give the product a name so a seller can find it in the library.",
    ),
    "product_needs_a_sku": (422, "Give the product a SKU so a quote line can name it."),
    "sku_is_taken": (409, "Another product already uses that SKU."),
    "product_needs_a_price": (
        422,
        "A product needs a price. Add a flat price in a currency, or a tier table.",
    ),
    "tier_needs_a_minimum": (
        422,
        "Give the tier a minimum quantity. It is the only number the author types.",
    ),
    "tier_minimum_must_be_whole": (422, "A tier minimum is a whole number of units."),
    "tier_minimum_must_be_positive": (422, "A tier minimum is 1 unit or more."),
    "tier_needs_a_price": (422, "Give the tier a unit price or a fee."),
    "tier_minimums_must_rise": (
        422,
        "Tier minimums must rise. Put them in order, smallest first, with no repeat.",
    ),
    "unknown_pricing_method": (422, "Choose flat, volume-based, graduated or stair-step."),
    "price_book_needs_a_name": (422, "Give the price book a name."),
    "price_book_needs_a_currency": (
        422,
        "Choose at least one currency for the price book. A price book names the "
        "currencies it prices in.",
    ),
    "currency_is_not_a_code": (422, "Use a three-letter currency code, such as USD or EUR."),
    "price_book_row_missing_a_price": (
        422,
        "Give the row a price in every currency the price book supports.",
    ),
    "price_book_is_not_complete": (
        409,
        "Every product in the price book needs a price in every currency it "
        "supports before the book can be activated.",
    ),
    "price_book_row_needs_a_product": (422, "Choose the product this row prices."),
    "price_book_row_needs_a_book": (422, "Choose the price book this row belongs to."),
    "one_row_per_product_and_book": (
        409,
        "That product is already in this price book. Edit that row instead of adding a second.",
    ),
    "bundle_products_are_not_in_a_price_book": (
        422,
        "A bundle cannot be added to a price book. Add the products inside the bundle instead.",
    ),
    "not_a_product": (404, "No such product."),
    "not_a_price_book": (404, "No such price book."),
    "not_a_price_book_item": (404, "No such price-book row."),
    "price_is_negative": (422, "A price cannot be negative."),
    "price_is_not_a_number": (422, "A price must be a number."),
    "price_is_not_finite": (422, "A price must be a finite number."),
    "quantity_must_be_positive": (422, "A quantity is 1 unit or more."),
    "retired_product_is_not_priced": (
        409,
        "This product is retired. Turn Active back on before pricing it.",
    ),
}

#: The fields this workflow owns, served by GET /vocabulary so the page can label
#: a form without repeating the words.
FIELD_CATALOGUE: dict[str, Any] = {
    "product": dict(PRODUCT_FIELDS),
    "price_book": dict(PRICE_BOOK_FIELDS),
    "price_book_item": dict(PRICE_BOOK_ITEM_FIELDS),
    "tier": dict(TIER_FIELDS),
}


def describe() -> dict[str, Any]:
    """The vocabulary as plain data, so ``GET /vocabulary`` serves one definition.

    Every value here is a constant from this module or a copy of one. No rule is
    applied here: a reader that wants to know what a tier costs reads
    :mod:`dsr.price_books.rules`, not this file.
    """

    return {
        "platform": PLATFORM,
        "platform_alternatives": dict(PLATFORM_ALTERNATIVES),
        "collections": {
            "products": PRODUCTS,
            "price_books": PRICE_BOOKS,
            "price_book_items": PRICE_BOOK_ITEMS,
        },
        "product_statuses": list(PRODUCT_STATUSES),
        "product_types": list(PRODUCT_TYPES),
        "billing_frequencies": list(BILLING_FREQUENCIES),
        "billing_synonyms": dict(BILLING_SYNONYMS),
        "pricing_methods": list(PRICING_METHODS),
        "tiered_pricing_methods": list(TIERED_PRICING_METHODS),
        "pricing_method_synonyms": dict(PRICING_METHOD_SYNONYMS),
        "price_sources": list(PRICE_SOURCES),
        "book_item_states": list(BOOK_ITEM_STATES),
        "common_currencies": list(COMMON_CURRENCIES),
        "supported_currencies": SUPPORTED_CURRENCIES,
        "book_currency_list_keys": list(BOOK_CURRENCY_LIST_KEYS),
        "min_quantity_keys": list(MIN_QUANTITY_KEYS),
        "unit_price_keys": list(UNIT_PRICE_KEYS),
        "tier_table_keys": list(TIER_TABLE_KEYS),
        "price_map_keys": list(PRICE_MAP_KEYS),
        "price_book_keys": list(PRICE_BOOK_KEYS),
        "fields": {name: dict(fields) for name, fields in FIELD_CATALOGUE.items()},
        "error_codes": {
            code: dict(status=status, message=message)
            for code, (status, message) in ERROR_CODES.items()
        },
    }
