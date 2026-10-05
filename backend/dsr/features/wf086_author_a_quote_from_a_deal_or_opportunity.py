"""WF-086: author a quote from a deal or opportunity.

The rules live in :mod:`dsr.quote_authoring` and are not restated here. This
module is the three things a feature contributes, and the three things it must
never contribute.

What this module contributes
----------------------------

* The route table, under a prefix this feature owns.
* The mapping from this workflow's error types to responses, exported as
  :data:`EXCEPTION_HANDLERS` because FastAPI accepts handlers on the app object
  only and this feature may not edit the app.
* The demo rows, as ``seed(db, context)`` rather than as an edit to the shared
  ``backend/seed.py``.

What it must never contribute
----------------------------

* No shared file. ``dsr/api.py``, ``dsr/deps.py``, ``dsr/store.py``,
  ``dsr/db/audited.py``, ``seed.py``, ``App.jsx``, ``main.jsx``, ``lib/api.js``,
  ``lib/features.js``, ``components/ui.jsx``, ``vite.config.js``.
* No import of ``dsr.api``. Dependencies come from ``dsr.deps``.
* No hand-written ``source=`` string. :func:`_source` builds every one from
  :data:`router`, so the audit row names the route that actually served the
  write.

The dependency that is not here
-------------------------------

WF-087 provisions the product library and the price books. It has not shipped.
Every read of the catalogue is therefore optional and the responses carry
``catalogue_available`` so a reviewer can see the degradation rather than infer
it from a price that came from somewhere unexpected.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Query, Request
from fastapi.responses import JSONResponse

from dsr.db.audited import AuditedDatabase
from dsr.deps import StoreDep
from dsr.quote_authoring import QuoteEngine
from dsr.quote_authoring.deals import catalogue_available
from dsr.quote_authoring.errors import QuoteConflict, QuoteError, QuoteNotFound
from dsr.store import RecordStore

FEATURE = {
    "id": "wf-086-author-a-quote-from-a-deal-or-opportunity",
    "ticket": "WF-086",
    "name": "Author a quote from a deal",
    "description": (
        "Author a quote from a deal or opportunity: prefill the header from the deal, "
        "clone the deal's line items onto the quote with their own record ids, price each "
        "line against the product catalogue, recompute the totals, and on publish copy only "
        "the total contract value onto the deal amount."
    ),
    "nav": [{"id": "wf-086-author", "label": "Author a quote"}],
}

#: Ticket-derived, so it cannot collide with the feature-shaped prefixes several
#: workflows already share. Every path below sits under it.
router = APIRouter(prefix="/api/wf-086", tags=["WF-086"])


def _source(method: str, path: str) -> str:
    """The audit ``source`` for a route, built from the router.

    Deliberately derived, never written as a literal: the defect this prevents is
    a domain function hardcoding a URL string, which leaves the audit log naming a
    route the app stopped serving.
    """
    return f"{method} {router.prefix}{path}"


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #


def _quote_error(request: Request, exc: QuoteError) -> JSONResponse:
    """422 with a field-keyed map, so each message lands beside its input."""
    return JSONResponse(
        status_code=422,
        content={"error": "quote_invalid", "detail": str(exc), "errors": exc.errors},
    )


def _quote_not_found(request: Request, exc: QuoteNotFound) -> JSONResponse:
    """404, worded so it does not confirm what a caller may not see."""
    return JSONResponse(
        status_code=404,
        content={"error": "not_found", "detail": "No such quote record."},
    )


def _quote_conflict(request: Request, exc: QuoteConflict) -> JSONResponse:
    """409 with a stable ``reason`` token the page branches on.

    409 rather than 400 because nothing about the request is malformed. Retrying
    the same call returns the same answer, which is what makes it a conflict.
    """
    return JSONResponse(
        status_code=409,
        content={"error": "quote_conflict", "reason": exc.reason, "detail": str(exc)},
    )


EXCEPTION_HANDLERS = {
    QuoteError: _quote_error,
    QuoteNotFound: _quote_not_found,
    QuoteConflict: _quote_conflict,
}


def _engine(store: RecordStore) -> QuoteEngine:
    """The engine, with the clock left at its production default.

    The real clock here rather than a request-supplied value: a caller that could
    pass its own ``now`` could pass a past one and make an expired quote look
    live. Tests inject the clock into the engine directly.
    """
    return QuoteEngine(store)


# --------------------------------------------------------------------------- #
# The board
# --------------------------------------------------------------------------- #


@router.get("/summary")
def summary(room_id: str | None = Query(None), store: RecordStore = StoreDep) -> dict[str, Any]:
    """The board's headline numbers. Reads only."""
    return _engine(store).summary(room_id)


@router.get("/vocabulary")
def vocabulary(store: RecordStore = StoreDep) -> dict[str, Any]:
    """The vocabulary this workflow enforces, so the UI need not hard-code it.

    Includes what is deliberately absent, under ``not_implemented``. A reviewer
    who cannot tell "we decided not to" from "we forgot to" has to read the
    source, and that is a cost paid every time the question comes up.
    """
    served = _engine(store).vocabulary()
    served["catalogue_available"] = catalogue_available(store)
    return served


# --------------------------------------------------------------------------- #
# Templates
# --------------------------------------------------------------------------- #


@router.post("/templates")
def create_template(
    payload: dict[str, Any] = Body(...),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Record a quote template for the dropdown.

    Stamped ``authored_via: ui``. The CRM API offers a read and a search on
    quote templates and no create endpoint, so nothing here claims to be the
    vendor's create path.
    """
    return _engine(store).create_template(
        payload, source=_source("POST", "/templates"), actor="rep"
    )


@router.get("/templates")
def list_templates(
    room_id: str | None = Query(None), store: RecordStore = StoreDep
) -> dict[str, Any]:
    """Every template, for the *Select a quote template* dropdown."""
    return {"templates": _engine(store).list_templates(room_id)}


# --------------------------------------------------------------------------- #
# The product library
# --------------------------------------------------------------------------- #


@router.get("/catalog")
def search_catalog(
    term: str = Query("", description="Name, description or SKU"),
    limit: int = Query(25, ge=1, le=100),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Search the product library, as the *Select from product library* step does.

    Returns an empty list with ``catalogue_available: false`` when WF-087 has not
    provisioned one. That is a correct answer, not a failure: the dropdown has
    nothing in it, and the page says so instead of showing an empty dialog with
    no explanation.
    """
    return _engine(store).search_catalogue(store, term, limit)


# --------------------------------------------------------------------------- #
# Quotes
# --------------------------------------------------------------------------- #


@router.post("/quotes")
def create_quote(
    payload: dict[str, Any] = Body(...),
    room_id: str | None = Query(None),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Create a quote from a deal and clone the deal's line items onto it.

    This is the *Create quote* button on the deal's Quotes card. The header is
    prefilled from the deal and every deal line becomes a quote line with its own
    record id, which is the rule the research states and the one a clone that
    reused an id would break.
    """
    return _engine(store).create_quote(
        room_id, payload, source=_source("POST", "/quotes"), actor="rep"
    )


@router.get("/quotes")
def list_quotes(
    room_id: str | None = Query(None),
    status: str | None = Query(None),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Every quote, or one room's, oldest first."""
    return {"quotes": _engine(store).list_quotes(room_id, status)}


@router.get("/quotes/{quote_id}")
def read_quote(quote_id: str, store: RecordStore = StoreDep) -> dict[str, Any]:
    """One quote, with its line items and its recomputed totals."""
    return _engine(store).read_quote(quote_id)


@router.patch("/quotes/{quote_id}")
def update_quote(
    quote_id: str,
    payload: dict[str, Any] = Body(...),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Edit the title, the expiration date, the module list or the payment schedule."""
    return _engine(store).update_quote(
        quote_id, payload, source=_source("PATCH", "/quotes/{quote_id}"), actor="rep"
    )


@router.post("/quotes/{quote_id}/publish")
def publish_quote(
    quote_id: str,
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Publish the quote: the *Activate Quote* command.

    Copies only the total contract value onto the deal amount, then replaces the
    deal's line items with fresh rows cloned from the quote's lines. Each fresh
    row has its own record id and carries the quote line's id.
    """
    return _engine(store).publish(
        quote_id, source=_source("POST", "/quotes/{quote_id}/publish"), actor="rep"
    )


# --------------------------------------------------------------------------- #
# Line items
# --------------------------------------------------------------------------- #


@router.post("/quotes/{quote_id}/line-items")
def add_line_item(
    quote_id: str,
    payload: dict[str, Any] = Body(...),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Add a line to a draft quote, from the catalogue or typed by hand.

    ``product_id`` selects from the product library and brings the tier lookup
    with it. Without it the line is a custom line item, which the research names
    as the alternative entry point.
    """
    return _engine(store).add_line_item(
        quote_id, payload, source=_source("POST", "/quotes/{quote_id}/line-items"), actor="rep"
    )


@router.get("/quotes/{quote_id}/line-items")
def list_line_items(quote_id: str, store: RecordStore = StoreDep) -> dict[str, Any]:
    """A quote's line items, in the order the seller put them in."""
    engine = _engine(store)
    engine.read_quote(quote_id)  # 404 rather than an empty list for a bad id
    return {"quote_id": quote_id, "line_items": engine.lines_of(quote_id)}


@router.patch("/line-items/{line_id}")
def update_line_item(
    line_id: str,
    payload: dict[str, Any] = Body(...),
    store: RecordStore = StoreDep,
) -> dict[str, Any]:
    """Set quantity, unit price, unit discount or tax rate on one line.

    A change of quantity re-resolves the tier when the line is priced from the
    catalogue, which is the "tier boundaries re-evaluate" rule.
    """
    return _engine(store).update_line_item(
        line_id, payload, source=_source("PATCH", "/line-items/{line_id}"), actor="rep"
    )


@router.delete("/line-items/{line_id}")
def delete_line_item(line_id: str, store: RecordStore = StoreDep) -> dict[str, Any]:
    """Remove a line from a draft quote.

    Refused on a published quote, because a published quote is what the buyer
    agreed to and the deal amount was computed from it.
    """
    return _engine(store).delete_line_item(
        line_id, source=_source("DELETE", "/line-items/{line_id}"), actor="rep"
    )


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #


def seed(db: AuditedDatabase, context: dict[str, Any]) -> str:
    """Seed the quote states the research says matter, not just the happy path.

    Produced by calling the real engine, so the demo cannot show a shape, a total
    or an audit row the HTTP routes would not produce. ``source="seed"`` rather
    than a route string: no route served this, and claiming one would be the lie
    hard rule 4 exists to prevent.

    The states seeded, and why each is here:

    * a quote **in draft** with cloned line items, a percentage discount and tax,
      so the totals column has something to add up;
    * a quote **in draft** with a future payment on its schedule, so the total
      contract value differs from the total and the difference is visible;
    * a quote with a **catalogue-priced line**, so the tier label appears. The
      catalogue is provisioned here because WF-087 has not shipped and without
      one this feature's page has nothing to price from;
    * a quote that has already been **published**, so the deal's amount and its
      replaced line items are visible and the write-back can be read.

    The deal and its line items are provisioned only when the CRM mirror holds
    none. Another workflow owns ``crm_deal`` and a rollup of its own counts
    those rows, so writing a deal into a populated mirror would move somebody
    else's numbers to make this page look fuller. When the mirror is already
    populated this feature's lines are attached to the deal that is already
    there.

    Every character of the returned string is ASCII, so the seeder can print it
    on a Windows console. A single non-cp1252 character in one feature broke the
    whole seeder once.
    """
    store = RecordStore(db)
    engine = QuoteEngine(store, now=lambda: context["now"])
    source, actor = "seed", "dana"
    room_ids: list[tuple[str, str]] = list(context.get("room_ids") or [])
    if not room_ids:
        return ""
    room_id = room_ids[0][0]
    today = context["now"].date().isoformat()

    # -- the product catalogue, because WF-087 has not shipped -------------- #
    products = [
        (
            "Northwind Platform, annual",
            "NWD-PLAT-ANN",
            "Per year, 40 seats minimum.",
            4800.0,
            [{"min_qty": 1, "unit_price": 4800.0}, {"min_qty": 25, "unit_price": 4200.0}],
        ),
        (
            "Priority support, annual",
            "NWD-SUP-PRI",
            "Per year. Named contacts and a four hour response.",
            2400.0,
            [{"min_qty": 1, "unit_price": 2400.0}],
        ),
    ]
    for name, sku, description, price, tiers in products:
        store.create(
            "crm_product",
            {
                "name": name,
                "sku": sku,
                "description": description,
                "unit_price": price,
                "price_book": "standard",
                "tiers": tiers,
            },
            actor=actor,
            source=source,
        )

    # -- the deal, only when the mirror holds none ---------------------------- #
    existing_deals = store.list("crm_deal", limit=1)
    provisioned_deal = not existing_deals
    deal_id = _provision_deal(store, room_id, source, actor) if provisioned_deal else None
    if deal_id is None:
        rows = store.list("crm_deal", limit=1)
        if not rows:
            return "no crm_deal record to quote from; the CRM mirror is empty"
        deal_id = str(rows[0]["id"])
        room_id = rows[0].get("room_id") or room_id
        if not store.find("crm_deal_line_item", {"deal_id": deal_id}, limit=1):
            _provision_deal_lines(store, deal_id, source, actor)

    # -- 1. a draft whose totals are worth adding up ------------------------- #
    draft = engine.create_quote(
        room_id,
        {"deal_id": deal_id, "title": "Northwind annual quote", "expires_on": "2099-06-30"},
        source=source,
        actor=actor,
    )
    draft_id = draft["quote"]["id"]
    engine.add_line_item(
        draft_id,
        {
            "name": "Northwind Platform, annual",
            "sku": "NWD-PLAT-ANN",
            "product_id": _product_id(store, "NWD-PLAT-ANN"),
            "quantity": 30,
            "discount_type": "percentage",
            "discount_value": 10,
            "tax_rate": 10,
        },
        source=source,
        actor=actor,
    )
    engine.add_line_item(
        draft_id,
        {
            "name": "Priority support, annual",
            "quantity": 1,
            "unit_price": 2400.0,
            "tax_rate": 10,
        },
        source=source,
        actor=actor,
    )

    # -- 2. a draft with a future payment, so TCV differs from the total ---- #
    scheduled = engine.create_quote(
        room_id,
        {
            "deal_id": deal_id,
            "title": "Northwind three year quote",
            "expires_on": "2099-12-31",
            "payment_schedule": [
                {"due_on": today, "amount": 5000.0},
                {"due_on": "2099-01-15", "amount": 15000.0},
            ],
        },
        source=source,
        actor=actor,
    )
    scheduled_id = scheduled["quote"]["id"]
    engine.add_line_item(
        scheduled_id,
        {
            "name": "Northwind Platform, annual",
            "quantity": 3,
            "unit_price": 4800.0,
            "tax_rate": 10,
        },
        source=source,
        actor=actor,
    )

    # -- 3. a published quote, so the write-back onto the deal is readable --- #
    published = engine.create_quote(
        room_id,
        {"deal_id": deal_id, "title": "Northwind activated quote", "expires_on": "2099-03-31"},
        source=source,
        actor=actor,
    )
    published_id = published["quote"]["id"]
    engine.add_line_item(
        published_id,
        {
            "name": "Priority support, annual",
            "sku": "NWD-SUP-PRI",
            "product_id": _product_id(store, "NWD-SUP-PRI"),
            "quantity": 1,
            "discount_type": "currency",
            "discount_value": 200.0,
            "tax_rate": 10,
        },
        source=source,
        actor=actor,
    )
    engine.publish(published_id, source=source, actor=actor)

    counted = engine.summary()
    return (
        f"{counted['quotes']} quotes ({counted['drafts']} draft, "
        f"{counted['published']} published, {counted['expired']} expired), "
        f"{counted['line_items']} quote line items each with their own record id, "
        f"{counted['templates']} quote templates, "
        f"{counted['total_contract_value']:.2f} in total contract value, "
        f"catalogue {'provisioned' if catalogue_available(store) else 'absent'}, "
        f"deal {'provisioned by this feature' if provisioned_deal else 'reused from the CRM mirror'}"
    )


def _product_id(store: RecordStore, sku: str) -> str | None:
    rows = store.find("crm_product", {"sku": sku}, limit=1)
    return str(rows[0]["id"]) if rows else None


def _provision_deal(store: RecordStore, room_id: str, source: str, actor: str) -> str:
    """One deal for the demo, with line items that carry real prices."""
    deal = store.create(
        "crm_deal",
        {
            "name": "Northwind Traders renewal and expansion",
            "account": "Northwind Traders",
            "owner": "dana",
            "currency": "USD",
            "amount": 24000.0,
            "stage": "negotiation",
            "address": {"city": "Seattle", "country": "US"},
        },
        room_id=room_id,
        actor=actor,
        source=source,
    )
    _provision_deal_lines(store, str(deal["id"]), source, actor)
    return str(deal["id"])


def _provision_deal_lines(store: RecordStore, deal_id: str, source: str, actor: str) -> None:
    for position, (name, sku, price, qty, tax) in enumerate(
        [
            ("Northwind Platform, annual", "NWD-PLAT-ANN", 4800.0, 30, 10.0),
            ("Priority support, annual", "NWD-SUP-PRI", 2400.0, 1, 10.0),
            ("Onboarding workshop", "NWD-IMP-ONB", 1500.0, 2, 0.0),
        ]
    ):
        store.create(
            "crm_deal_line_item",
            {
                "deal_id": deal_id,
                "position": position,
                "name": name,
                "sku": sku,
                "quantity": qty,
                "unit_price": price,
                "tax_rate": tax,
            },
            actor=actor,
            source=source,
        )
