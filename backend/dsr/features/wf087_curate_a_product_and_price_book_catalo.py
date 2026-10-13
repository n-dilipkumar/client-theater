"""WF-087: curate a product and price-book catalogue with tiered pricing.

The workflow the research describes, in the order an admin performs it:

1. Create a product with a name, SKU, description, tax category, billing
   frequency and an optional image.
2. Give it a pricing configuration: a flat price in each account currency, or a
   tier table with a pricing method and a minimum per range.
3. Toggle Active off to retire a product from deals and quotes without deleting
   it.
4. Create a price book with a name, a description and the currencies it prices
   in, then add products to it and override Price, Billing frequency and Term
   per row.
5. At quote-line creation, evaluate the tier table against the quantity to
   select the effective unit price, then let the price-book layer override the
   product default price for that buyer segment or region.

What is modelled, and what is not
----------------------------------

The research documents HubSpot, Dynamics 365 Sales and PandaDoc. This build models
the HubSpot product and price-book objects, recorded in
``vocabulary.PLATFORM``. The other two are recorded as vocabulary synonyms:
Dynamics calls the same objects a price list and price list item, and PandaDoc
calls them catalog items with ``regular`` and ``bundle`` types.

Three things the research names are deliberately not built, and each is recorded
in :mod:`dsr.price_books.inferences` with where the work went: price-book
auto-assignment, which belongs to the workflow that owns the assignment rule;
bulk import, which belongs to the workflow that owns file upload, though the rule
this workflow does enforce is that a tier-priced product may not be imported; and
currency conversion, which the vendor's own price-book guide never mentions.

The rules the research left open, and how they were closed
----------------------------------------------------------

Each of these is an implementation decision with a Jev audit behind it. They are
recorded in full in :mod:`dsr.price_books.inferences` and served at
``/inferences``; the short form is here so a reader of this file knows what is
quoted and what is derived.

* **Tier arithmetic.** The research names volume-based, graduated and stair-step
  and quotes one sentence each, with no formula. Volume-based prices every unit at
  the rate the total quantity reaches, graduated prices each unit at the rate of
  the range its own position falls in, and stair-step charges the reached range's
  fee once. Audit ``jev-20261013T111921-7676-61571``, confidence 1.00.
* **Tier maximum.** Derived from the next tier's minimum, never authored, so the
  two cannot disagree. Same audit.
* **Override precedence.** The product is the default and an active price-book
  row is the override, confirmed by the vendor's "the source product is never
  modified by price book operations". Audit ``jev-20261013T111944-8144-84487``,
  confidence 0.94.
* **Currencies.** The research quotes a single-currency dropdown; the vendor API
  types ``supportedCurrencies`` as an array. The array wins, a missing price is a
  gap that blocks activation rather than a zero, and nothing is ever converted.
  Audit ``jev-20261013T112401-10796-41499``, confidence 1.00.
* **Retirement.** Active off is a flag. Delete is a separate, audited soft delete.
  Audit ``jev-20261013T111944-8144-84487``, confidence 0.97.
* **Collections.** ``wf087_product``, ``wf087_price_book`` and
  ``wf087_price_book_item``. Audit ``jev-20261013T111835-7396-15231``,
  confidence 0.97.

The hard rules this module holds itself to
------------------------------------------

1. Dependencies come from ``dsr.deps``, never from ``dsr.api``. Importing the
   app from a feature reintroduces the coupling the feature host exists to
   remove, and there is a test in ``test_features.py`` that fails if it happens.
2. Every read and write goes through ``StoreDep``, so every write is made by
   ``AuditedDatabase`` in the same transaction as its audit row.
3. Every write is handed ``source=f"{METHOD} {router.prefix}{path}"``, built from
   ``router.prefix`` so the audit row and the route table cannot drift. The path
   substitutes the concrete ids, so a row reads ``POST /api/wf-087/products/p1``
   rather than naming a template nobody can call. ``test_wf087_http.py`` matches
   every audit row this module writes against the routes the host actually
   mounted.
4. No migration and no typed column. Every field is JSON in ``records.data``,
   because a team adding a field must not need coordination with anyone.
5. ``seed(db, context)`` returns a string every character of which ``cp1252``
   can encode. A RIGHTWARDS ARROW in one recovered feature's return string broke
   the whole seeder on a Windows console.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, Query, Request
from fastapi.responses import JSONResponse

from dsr.deps import StoreDep
from dsr.price_books import inferences as inference_module, vocabulary as vocab
from dsr.price_books.engine import CatalogueEngine
from dsr.price_books.errors import CatalogueNotFound, CatalogueRefusal
from dsr.store import RecordStore

FEATURE = {
    "id": "wf-087-curate-a-product-and-price-book-catalo",
    "ticket": "WF-087",
    "name": "Curate a product and price-book catalogue with tiered pricing",
    "description": (
        "Build a product library whose prices are flat per currency or tiered by "
        "quantity, then group products into price books that override the default "
        "price for one buyer segment or region."
    ),
    "nav": [{"id": "catalogue-products", "label": "Catalogue products"}],
}

# The prefix is the ticket slug lower-cased. The feature host matches paths
# literally, and the two already-merged quoting workflows sit under /api/WF-086
# and /api/WF-094 in upper case, so the case is not interchangeable and this
# module's own tests assert the exact spelling.
router = APIRouter(prefix="/api/wf-087", tags=["WF-087"])


def get_service(store: RecordStore = StoreDep) -> CatalogueEngine:
    """The engine for this request.

    Built per request from ``StoreDep`` rather than cached on ``app.state``.
    Caching it would mean the engine pinned whichever store built it first, and a
    test swapping the database would have to reach into the app as well as into
    this object.
    """
    return CatalogueEngine(store)


ServiceDep = Depends(get_service)


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #


def _refusal(request: Request, exc: CatalogueRefusal) -> JSONResponse:
    """A refused request, answered with the status and code it carries.

    The status comes off the exception rather than off this function, because
    ``ERROR_CODES`` in the vocabulary is the one place that pairs a refusal with
    its status. A handler that decided statuses itself would be a second list to
    keep in step.
    """
    return JSONResponse(status_code=exc.status, content=exc.as_dict())


def _not_found(request: Request, exc: CatalogueNotFound) -> JSONResponse:
    """No such product, price book or price-book row."""
    return JSONResponse(status_code=exc.status, content=exc.as_dict())


# Only this feature's own two types. ``RecordNotFound`` is deliberately not
# claimed: the core maps it to a 404 already, and two handlers for one type is a
# collision the host refuses.
EXCEPTION_HANDLERS = {
    CatalogueRefusal: _refusal,
    CatalogueNotFound: _not_found,
}


# --------------------------------------------------------------------------- #
# The vocabulary and the decisions
# --------------------------------------------------------------------------- #


@router.get("/vocabulary", summary="Every published vocabulary")
def vocabulary() -> dict[str, Any]:
    """The words this workflow uses, as one document.

    The page reads its enumerations, its field labels and its refusal messages
    from here rather than hard-coding them, so a change to the rules cannot leave
    the page calling a state the API does not serve.
    """
    return vocab.describe()


@router.get("/inferences", summary="Every judgement call this workflow rests on")
def inferences() -> dict[str, Any]:
    """What was decided, what was not built, and what waits on this workflow.

    Served so a reviewer reads the decisions without importing the code, and a
    workflow built later can see what it must not re-decide.
    """
    return inference_module.describe()


# --------------------------------------------------------------------------- #
# Products
# --------------------------------------------------------------------------- #


@router.get("/products", summary="Search the product library")
def products(
    service: CatalogueEngine = ServiceDep,
    term: str = Query(default=""),
    active_only: bool = Query(default=False),
    room_id: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
) -> dict[str, Any]:
    """The catalogue, newest first, each row carrying its resolved pricing.

    ``term`` matches the name, the SKU or the description. ``active_only`` hides
    retired products, which is the difference between the whole library and the
    products a quote may use today.
    """
    rows = service.list_products(term=term, active_only=active_only, room_id=room_id, limit=limit)
    return {"products": rows, "count": len(rows), "term": term, "active_only": active_only}


@router.post("/products", status_code=201, summary="Add a product to the library")
def create_product(
    payload: dict[str, Any] = Body(default_factory=dict),
    service: CatalogueEngine = ServiceDep,
    actor: str | None = Query(default=None),
    room_id: str | None = Query(default=None),
) -> dict[str, Any]:
    """Create one product with its flat price or its tier table."""
    return service.create_product(
        payload,
        actor=actor,
        source=f"POST {router.prefix}/products",
        room_id=room_id,
    )


@router.get("/products/{product_id}", summary="One product")
def product(
    product_id: str,
    service: CatalogueEngine = ServiceDep,
) -> dict[str, Any]:
    """One product, with its tier table normalised and its maximums derived."""
    return service.get_product(product_id)


@router.patch("/products/{product_id}", summary="Edit a product")
def edit_product(
    product_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    service: CatalogueEngine = ServiceDep,
    actor: str | None = Query(default=None),
) -> dict[str, Any]:
    """Merge a patch into a product, revalidating the whole record.

    Revalidating the merged record rather than the patch is what makes a patch of
    ``{"pricing_method": "graduated"}`` on a flat product a refusal rather than a
    product with a graduated method and no tiers.
    """
    return service.edit_product(
        product_id,
        payload,
        actor=actor,
        source=f"PATCH {router.prefix}/products/{product_id}",
    )


@router.post("/products/{product_id}/active", summary="Retire a product, or bring it back")
def set_product_active(
    product_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    service: CatalogueEngine = ServiceDep,
    actor: str | None = Query(default=None),
) -> dict[str, Any]:
    """Switch the Active toggle.

    "Toggle Active off to retire a product from deals, quotes and subscriptions
    without deleting it." The record and its history stay; the product simply
    stops being offered for new work.
    """
    active = bool(payload.get("active", True))
    return service.retire_product(
        product_id,
        active=active,
        actor=actor,
        source=f"POST {router.prefix}/products/{product_id}/active",
    )


@router.delete("/products/{product_id}", summary="Remove a product from the catalogue")
def remove_product(
    product_id: str,
    service: CatalogueEngine = ServiceDep,
    actor: str | None = Query(default=None),
) -> dict[str, Any]:
    """Delete a product and every price-book row that prices it, in one transaction.

    Separate from the Active toggle: a retired product is still a product that
    exists and can be brought back, and a deleted one leaves the catalogue.
    """
    return service.remove_product(
        product_id,
        actor=actor,
        source=f"DELETE {router.prefix}/products/{product_id}",
    )


@router.post("/products/{product_id}/price", summary="Price one quantity of a product")
def price_product(
    product_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    service: CatalogueEngine = ServiceDep,
    price_book_id: str = Query(default=""),
    currency: str = Query(default=""),
) -> dict[str, Any]:
    """The unit price a quote line pays, and every step that produced it.

    This is the researched moment: "at quote-line creation the tier table is
    evaluated against quantity to select the effective unit price; the price-book
    layer then overrides the product default price". Reads and writes nothing, so
    it writes no audit row: a price lookup is a question, not a change.
    """
    return service.price(
        product_id,
        payload.get("quantity", 1),
        price_book_id=price_book_id,
        currency=currency,
    )


# --------------------------------------------------------------------------- #
# Price books
# --------------------------------------------------------------------------- #


@router.get("/price-books", summary="Every price book")
def price_books(
    service: CatalogueEngine = ServiceDep,
    room_id: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
) -> dict[str, Any]:
    """Every price book, each with its currencies, its row count and its gaps."""
    rows = service.list_price_books(room_id=room_id, limit=limit)
    return {"price_books": rows, "count": len(rows)}


@router.post("/price-books", status_code=201, summary="Create a price book")
def create_price_book(
    payload: dict[str, Any] = Body(default_factory=dict),
    service: CatalogueEngine = ServiceDep,
    actor: str | None = Query(default=None),
    room_id: str | None = Query(default=None),
) -> dict[str, Any]:
    """Create one price book with the currencies it prices in."""
    return service.create_price_book(
        payload,
        actor=actor,
        source=f"POST {router.prefix}/price-books",
        room_id=room_id,
    )


@router.get("/price-books/{book_id}", summary="One price book")
def price_book(
    book_id: str,
    service: CatalogueEngine = ServiceDep,
) -> dict[str, Any]:
    """One price book, with the currencies each of its rows is missing."""
    return service.get_price_book(book_id)


@router.patch("/price-books/{book_id}", summary="Edit a price book")
def edit_price_book(
    book_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    service: CatalogueEngine = ServiceDep,
    actor: str | None = Query(default=None),
) -> dict[str, Any]:
    """Merge a patch into a price book."""
    return service.edit_price_book(
        book_id,
        payload,
        actor=actor,
        source=f"PATCH {router.prefix}/price-books/{book_id}",
    )


@router.post("/price-books/{book_id}/active", summary="Activate a price book, or switch it off")
def set_price_book_active(
    book_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    service: CatalogueEngine = ServiceDep,
    actor: str | None = Query(default=None),
) -> dict[str, Any]:
    """Switch a price book on or off.

    Switching it on is refused while any product in it lacks a price in a
    currency the book supports, which is the vendor's activation validation:
    "every product has a price for each of the book's supported currencies".
    """
    active = bool(payload.get("active", True))
    return service.activate_price_book(
        book_id,
        active=active,
        actor=actor,
        source=f"POST {router.prefix}/price-books/{book_id}/active",
    )


# --------------------------------------------------------------------------- #
# Price-book rows
# --------------------------------------------------------------------------- #


@router.get("/price-book-items", summary="The price-book rows, filterable")
def price_book_items(
    service: CatalogueEngine = ServiceDep,
    price_book_id: str = Query(default=""),
    product_id: str = Query(default=""),
    limit: int = Query(default=500, ge=1, le=1000),
) -> dict[str, Any]:
    """The rows, filtered by book or by product.

    Both filters go through ``find``, which resolves a dotted JSON path through
    the dynamic index, so a filter on a field no schema declares still works.
    """
    rows = service.list_book_items(price_book_id, product_id=product_id, limit=limit)
    return {"items": rows, "count": len(rows), "price_book_id": price_book_id or None}


@router.post("/price-book-items", status_code=201, summary="Add a product to a price book")
def add_price_book_item(
    payload: dict[str, Any] = Body(default_factory=dict),
    service: CatalogueEngine = ServiceDep,
    actor: str | None = Query(default=None),
    room_id: str | None = Query(default=None),
) -> dict[str, Any]:
    """Add one product to one price book, refusing a second row for the same pair.

    "Only one instance of each product can appear in a price book at a time." A
    row may be saved with a price in only some of the book's currencies; the book
    cannot go live until every gap is filled.
    """
    return service.add_book_item(
        payload,
        actor=actor,
        source=f"POST {router.prefix}/price-book-items",
        room_id=room_id,
    )


@router.get("/price-book-items/{item_id}", summary="One price-book row")
def price_book_item(
    item_id: str,
    service: CatalogueEngine = ServiceDep,
) -> dict[str, Any]:
    """One price-book row, with the product and the book it names."""
    rows = service.list_book_items(limit=1000)
    for row in rows:
        if row.get("id") == item_id:
            return row
    raise CatalogueNotFound("not_a_price_book_item", "Price book row", item_id)


@router.patch("/price-book-items/{item_id}", summary="Edit a price-book row")
def edit_price_book_item(
    item_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    service: CatalogueEngine = ServiceDep,
    actor: str | None = Query(default=None),
) -> dict[str, Any]:
    """Merge a patch into one price-book row."""
    return service.edit_book_item(
        item_id,
        payload,
        actor=actor,
        source=f"PATCH {router.prefix}/price-book-items/{item_id}",
    )


@router.delete("/price-book-items/{item_id}", summary="Remove a price-book row")
def remove_price_book_item(
    item_id: str,
    service: CatalogueEngine = ServiceDep,
    actor: str | None = Query(default=None),
) -> dict[str, Any]:
    """Remove one price-book row, keeping it in the audit trail."""
    return service.remove_book_item(
        item_id,
        actor=actor,
        source=f"DELETE {router.prefix}/price-book-items/{item_id}",
    )


# --------------------------------------------------------------------------- #
# The page's header
# --------------------------------------------------------------------------- #


@router.get("/summary", summary="What the catalogue holds")
def summary(service: CatalogueEngine = ServiceDep) -> dict[str, Any]:
    """Counts and the currencies in use, for the page header.

    Read on every render of the page, so it is four counts and a currency list
    rather than a figure computed from every row: a header that took a second to
    draw would be a header nobody reads.
    """
    return service.summary()


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #


def _demo_rooms(context: dict[str, Any]) -> list[str]:
    """The room ids the seeder offers, whichever shape it passed them in.

    The seeder calls a feature seed with ``(room_id, name)`` pairs on some paths
    and bare ids on others, and a seed that raises is skipped loudly rather than
    aborting the whole run. Normalising here means a change to the seeder's
    calling convention does not silently empty this feature's demo page.
    """
    rooms: list[str] = []
    for entry in context.get("room_ids") or []:
        room_id = entry[0] if isinstance(entry, (tuple, list)) and entry else entry
        if room_id:
            rooms.append(str(room_id))
    return rooms


def seed(db, context: dict[str, Any]) -> str:
    """Seed a catalogue that shows every rule this workflow implements.

    Drives the real engine rather than hand-composing rows, because a seeded row
    and an API-created row cannot then disagree: a demo page that shows a tier
    table the API would refuse is a demo nobody can review.

    The returned string is plain ASCII. Every character of it is encodable by
    cp1252, because a single RIGHTWARDS ARROW in one recovered feature's return
    string broke the entire seeder on a Windows console.
    """
    rooms = _demo_rooms(context)
    room_id = rooms[0] if rooms else None
    actor = "dana"
    source = "seed"

    from dsr.store import RecordStore  # local import keeps the module import cheap

    base: Any = context.get("now")
    service = CatalogueEngine(RecordStore(db), now=(lambda: base) if base else None)

    if service.store.count_where(vocab.PRODUCTS, {}):
        return "0 products, 0 price books, 0 rows (the catalogue already holds products)"

    products = [
        (
            "Northwind Platform, annual",
            "NWD-PLAT-ANN",
            "Per year, 40 seats minimum. Volume pricing: the whole order takes the rate the quantity reaches.",
            vocab.METHOD_VOLUME_BASED,
            "USD",
            [
                {"min_qty": 1, "unit_price": 4800.0},
                {"min_qty": 25, "unit_price": 4200.0},
                {"min_qty": 100, "unit_price": 3600.0},
            ],
        ),
        (
            "Northwind Platform, monthly",
            "NWD-PLAT-MON",
            "Per month, same seats. Graduated pricing: each unit takes the rate of its own range.",
            vocab.METHOD_GRADUATED,
            "USD",
            [
                {"min_qty": 1, "unit_price": 450.0},
                {"min_qty": 25, "unit_price": 400.0},
                {"min_qty": 100, "unit_price": 340.0},
            ],
        ),
        (
            "Priority support, annual",
            "NWD-SUP-PRI",
            "Per year. Flat priced. Named contacts and a four hour response.",
            vocab.METHOD_FLAT,
            "USD",
            [],
        ),
        (
            "Onboarding workshop, per seat",
            "NWD-ONB-SEA",
            "One flat fee for each seat. Stair-step: the range picks the fee and the fee is charged once.",
            vocab.METHOD_STAIR_STEP,
            "USD",
            [
                {"min_qty": 1, "fee": 400.0},
                {"min_qty": 10, "fee": 300.0},
                {"min_qty": 40, "fee": 180.0},
            ],
        ),
    ]

    created: dict[str, str] = {}
    for name, sku, description, method, currency, tiers in products:
        payload: dict[str, Any] = {
            "name": name,
            "sku": sku,
            "description": description,
            "pricing_method": method,
            "currency": currency,
            "billing_frequency": vocab.BILLING_RECURRING
            if "annual" in sku or "MON" in sku
            else vocab.BILLING_ONE_TIME,
            "tax_category": "Software",
            "product_type": vocab.PRODUCT_TYPE_REGULAR,
        }
        if tiers:
            payload["tiers"] = tiers
        else:
            payload["prices"] = {"USD": 2400.0}
        record = service.create_product(payload, actor=actor, source=source, room_id=room_id)
        created[sku] = str(record["id"])

    # A product that was retired, so the demo shows the Active toggle rather
    # than only describing it.
    service.retire_product(
        created["NWD-ONB-SEA"],
        active=False,
        actor=actor,
        source=source,
    )

    standard = service.create_price_book(
        {
            "name": "Standard list",
            "description": "The default rate for everyone, in US dollars and euro.",
            vocab.SUPPORTED_CURRENCIES: ["USD", "EUR"],
            "segment": "All buyers",
        },
        actor=actor,
        source=source,
        room_id=room_id,
    )

    # A book left deliberately incomplete, so the demo shows the activation rule
    # refusing a gap rather than only describing it.
    partner = service.create_price_book(
        {
            "name": "Partner and referral rates",
            "description": "Discounted rates for referred buyers and resellers.",
            vocab.SUPPORTED_CURRENCIES: ["USD", "GBP"],
            "segment": "Partners",
        },
        actor=actor,
        source=source,
        room_id=room_id,
    )

    service.add_book_item(
        {
            "product_id": created["NWD-PLAT-ANN"],
            "price_book_id": str(standard["id"]),
            "prices": {"USD": 4400.0, "EUR": 4100.0},
            "billing_frequency": vocab.BILLING_RECURRING,
            "term": "12 months",
        },
        actor=actor,
        source=source,
    )
    service.add_book_item(
        {
            "product_id": created["NWD-SUP-PRI"],
            "price_book_id": str(standard["id"]),
            "prices": {"USD": 2400.0, "EUR": 2250.0},
            "billing_frequency": vocab.BILLING_RECURRING,
            "term": "12 months",
        },
        actor=actor,
        source=source,
    )
    # One row with a price in only one of the book's two currencies, so the
    # activation refusal has something real to refuse.
    service.add_book_item(
        {
            "product_id": created["NWD-PLAT-MON"],
            "price_book_id": str(partner["id"]),
            "prices": {"USD": 380.0},
            "billing_frequency": vocab.BILLING_RECURRING,
            "term": "12 months",
        },
        actor=actor,
        source=source,
    )

    service.activate_price_book(str(standard["id"]), active=True, actor=actor, source=source)

    return (
        "4 products (1 retired, 3 tiered, 1 flat), "
        "2 price books (1 active, 1 with a missing GBP price), "
        "3 price book rows"
    )
