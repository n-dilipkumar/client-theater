"""WF-101: convert an accepted quote into an order and lock the price.

The rules live in :mod:`dsr.order_conversion`, which knows nothing about HTTP.
This module is the four things a feature adds and nothing else:

* the route table, an ``APIRouter`` this feature owns;
* the error mapping, an ``EXCEPTION_HANDLERS`` export the host attaches, because
  FastAPI accepts exception handlers on the app object only;
* the demo rows, a ``seed(db, context)`` hook the seeder calls;
* the settings and the judgement calls the page reads, served from one place so
  a panel cannot drift from the rules that enforce it.

Dependencies come from :mod:`dsr.deps`. Nothing here imports ``dsr.api`` and
nothing opens SQLite, so the enforced test that greps feature modules for those
two things passes by construction.

Every write passes a ``source`` built from ``router.prefix`` and the route's own
path, so each audit row names a URL the app actually serves.

Source: ``docs/research/digital-sales-room-workflows/wf/WF-101.md``.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, Query, Request
from fastapi.responses import JSONResponse

from dsr.deps import StoreDep
from dsr.order_conversion import vocabulary as vocab
from dsr.order_conversion.engine import OrderConversionEngine
from dsr.order_conversion.errors import OrderConflict, OrderError, OrderNotFound
from dsr.store import RecordStore

FEATURE = {
    "id": "wf-101-quote-to-order",
    "ticket": "WF-101",
    "name": "Convert an accepted quote into an order and lock the price",
    "description": (
        "Clone an accepted and activated quote onto a sales order: price list, currency, "
        "products, addresses, shipping and totals. Stamp the price lock the two "
        "organisation settings decide, and let a later price-list edit apply or be "
        "ignored according to the pricing mode."
    ),
    "nav": [{"id": "wf-101-quote-to-order", "label": "Quote to order"}],
}

router = APIRouter(prefix="/api/wf-101", tags=["WF-101"])


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #


def _order_error(request: Request, exc: OrderError) -> JSONResponse:
    """A malformed request. 422, with each message beside its field."""

    return JSONResponse(status_code=422, content={"error": "order_invalid", **exc.as_dict()})


def _order_not_found(request: Request, exc: OrderNotFound) -> JSONResponse:
    """A quote or order that does not exist. 404."""

    return JSONResponse(status_code=404, content={"error": "order_not_found", "detail": str(exc)})


def _order_conflict(request: Request, exc: OrderConflict) -> JSONResponse:
    """The record's state refuses the change. 409, with a stable reason token."""

    return JSONResponse(status_code=409, content={"error": "order_conflict", **exc.as_dict()})


EXCEPTION_HANDLERS = {
    OrderError: _order_error,
    OrderNotFound: _order_not_found,
    OrderConflict: _order_conflict,
}


# --------------------------------------------------------------------------- #
# Composition
# --------------------------------------------------------------------------- #


def get_engine(store: RecordStore = StoreDep) -> OrderConversionEngine:
    """The engine, built on the shared audited store."""

    return OrderConversionEngine(store)


EngineDep = Depends(get_engine)


def _source(verb: str, suffix: str) -> str:
    """The audit ``source`` for a write: the path this router actually serves."""

    return f"{verb} {router.prefix}{suffix}"


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #


@router.get("/vocabulary")
def vocabulary() -> dict[str, Any]:
    """Every word, mode, setting and refusal reason the rules use, from one place."""

    return vocab.vocabulary()


@router.get("/decisions")
def decisions() -> dict[str, Any]:
    """Every judgement call this workflow made, with the alternative it rejected."""

    picks = OrderConversionEngine.decisions()
    return {"count": len(picks), "entries": picks}


@router.get("/summary")
def summary(
    room_id: str | None = Query(default=None),
    engine: OrderConversionEngine = EngineDep,
) -> dict[str, Any]:
    """Counts a seller needs before opening an order."""

    return engine.summary(room_id=room_id)


@router.get("/settings")
def get_settings(
    room_id: str | None = Query(default=None),
    engine: OrderConversionEngine = EngineDep,
) -> dict[str, Any]:
    """The two organisation settings and the Order table default, with the defaults marked."""

    return engine.settings(room_id=room_id)


@router.post("/settings")
def update_settings(
    payload: dict[str, Any] = Body(default_factory=dict),
    room_id: str | None = Query(default=None),
    engine: OrderConversionEngine = EngineDep,
) -> dict[str, Any]:
    """Set the switches that gate the surface. Unknown keys are ignored."""

    return engine.update_settings(
        payload,
        room_id=room_id,
        actor=payload.get("actor"),
        source=_source("POST", "/settings"),
    )


@router.get("/orders")
def list_orders(
    room_id: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
    engine: OrderConversionEngine = EngineDep,
) -> dict[str, Any]:
    """Orders, each with its lines, its price lock and its derived pricing mode."""

    records = engine.orders(room_id=room_id, limit=limit)
    return {"count": len(records), "entries": records}


@router.get("/orders/{order_id}")
def get_order(
    order_id: str,
    engine: OrderConversionEngine = EngineDep,
) -> dict[str, Any]:
    """One order with its lines and its price lock."""

    return engine.order(order_id)


@router.patch("/orders/{order_id}")
def update_order(
    order_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    engine: OrderConversionEngine = EngineDep,
) -> dict[str, Any]:
    """Edit the order header. A closed order refuses the edit."""

    return engine.update_order(
        order_id,
        payload,
        actor=payload.get("actor"),
        source=_source("PATCH", f"/orders/{order_id}"),
    )


@router.get("/quotes/{quote_id}/readiness")
def quote_readiness(
    quote_id: str,
    engine: OrderConversionEngine = EngineDep,
) -> dict[str, Any]:
    """What would happen if this quote were converted now, and why."""

    return engine.readiness(quote_id)


@router.post("/quotes/{quote_id}/order")
def convert_quote(
    quote_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    engine: OrderConversionEngine = EngineDep,
) -> dict[str, Any]:
    """Convert the accepted and activated quote into an order.

    The date won defaults to today, and a description is carried onto the order.
    Idempotent: a quote that already produced an order returns it unchanged.
    """

    return engine.convert(
        quote_id,
        payload,
        actor=payload.get("actor"),
        source=_source("POST", f"/quotes/{quote_id}/order"),
    )


@router.post("/orders/{order_id}/pricing-mode")
def set_pricing_mode(
    order_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    engine: OrderConversionEngine = EngineDep,
) -> dict[str, Any]:
    """Lock Pricing or Use Current Pricing on the order."""

    return engine.set_pricing_mode(
        order_id,
        str(payload.get("mode") or ""),
        actor=payload.get("actor"),
        source=_source("POST", f"/orders/{order_id}/pricing-mode"),
    )


@router.post("/orders/{order_id}/reprice")
def reprice_order(
    order_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    engine: OrderConversionEngine = EngineDep,
) -> dict[str, Any]:
    """Apply the current price list to the order's lines.

    A locked order is not repriced; the attempt is recorded and applied to
    nothing. An overridden line is never touched.
    """

    return engine.reprice(
        order_id,
        actor=payload.get("actor"),
        source=_source("POST", f"/orders/{order_id}/reprice"),
    )


@router.post("/orders/{order_id}/lines")
def add_line(
    order_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    engine: OrderConversionEngine = EngineDep,
) -> dict[str, Any]:
    """Add a write-in product. Allowed only while Prices Locked is No."""

    return engine.add_line(
        order_id,
        payload,
        actor=payload.get("actor"),
        source=_source("POST", f"/orders/{order_id}/lines"),
    )


@router.patch("/orders/{order_id}/lines/{line_id}")
def update_line(
    order_id: str,
    line_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    engine: OrderConversionEngine = EngineDep,
) -> dict[str, Any]:
    """Edit one order line. A locked order refuses a price edit."""

    return engine.update_line(
        order_id,
        line_id,
        payload,
        actor=payload.get("actor"),
        source=_source("PATCH", f"/orders/{order_id}/lines/{line_id}"),
    )


@router.post("/orders/{order_id}/close")
def close_order(
    order_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    engine: OrderConversionEngine = EngineDep,
) -> dict[str, Any]:
    """Close the order as fulfilled or cancelled. The order becomes read-only."""

    return engine.close_order(
        order_id,
        payload,
        actor=payload.get("actor"),
        source=_source("POST", f"/orders/{order_id}/close"),
    )


@router.post("/orders/{order_id}/products")
def get_products(
    order_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    engine: OrderConversionEngine = EngineDep,
) -> dict[str, Any]:
    """Copy an opportunity's products onto the order.

    When the opportunity is closed, its final revenue is recorded on the order.
    """

    return engine.get_products(
        order_id,
        payload,
        actor=payload.get("actor"),
        source=_source("POST", f"/orders/{order_id}/products"),
    )


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #


def seed(db, context):
    """Seed the states the price-lock rule and the refusals name.

    Five quotes cover the interesting cases, and not all of them convert: one
    produces a locked order, one produces an unlocked order that a reprice
    updates and that is then fulfilled, and three are refused for the three
    reasons a reviewer most needs to see (not activated, not accepted, creation
    switched off). A standalone happy path teaches a reviewer nothing.

    Every character of the returned string is cp1252-encodable. The seeder
    prints it on a Windows console, and one arrow character in one recovered
    feature broke the whole seed.
    """

    rooms = context.get("room_ids") or []
    if not rooms:
        return None
    room_id = rooms[0][0]
    now = context["now"]
    store = RecordStore(db)
    engine = OrderConversionEngine(store, now=lambda: now)

    # A small catalogue, so a reprice has a price list to read.
    product_record = db.create(
        "wf087_product",
        {
            "name": "Platform licence",
            "sku": "SKU-101",
            "pricing_method": "flat",
            "prices": {"EUR": 1200.0},
            "active": True,
        },
        room_id=room_id,
        actor="dana",
        source="seed",
    )
    book_record = db.create(
        "wf087_price_book",
        {"name": "Enterprise EUR", "supported_currencies": ["EUR"], "active": True},
        room_id=room_id,
        actor="dana",
        source="seed",
    )
    product_id = product_record["id"]
    book_id = book_record["id"]
    db.create(
        "wf087_price_book_item",
        {
            "price_book_id": book_id,
            "product_id": product_id,
            "state": "active",
            "prices": {"EUR": 1200.0},
        },
        room_id=room_id,
        actor="dana",
        source="seed",
    )

    def quote(title: str, **extra: Any) -> str:
        record = db.create(
            "wf086_quote",
            {
                "title": title,
                "status": "published",
                "template_type": "CPQ_QUOTE",
                "currency": "EUR",
                "currency_label": "EUR",
                "account": "Northwind Traders",
                "company_name": "Northwind Traders",
                "owner": "dana",
                "deal_id": "deal_northwind",
                "hs_status": "ACCEPTED",
                "price_list": book_id,
                "expires_on": "2035-12-31",
                "totals": {"total": 0.0, "total_contract_value": 0.0},
                **extra,
            },
            room_id=room_id,
            actor="dana",
            source="seed",
        )
        return record["id"]

    def line(quote_id: str, position: int, name: str, sku: str, qty: float, price: float) -> None:
        total = round(qty * price, 2)
        db.create(
            "wf086_line_item",
            {
                "quote_id": quote_id,
                "position": position,
                "name": name,
                "sku": sku,
                "product_id": product_id if sku == "SKU-101" else None,
                "quantity": qty,
                "unit_price": price,
                "discount_type": "percentage",
                "discount_value": 0.0,
                "tax_rate": 21.0,
                "price_source": "catalog",
                "amounts": {
                    "quantity": qty,
                    "unit_price": price,
                    "subtotal": total,
                    "discount": 0.0,
                    "net": total,
                    "tax": round(total * 0.21, 2),
                    "total": round(total * 1.21, 2),
                },
            },
            room_id=room_id,
            actor="dana",
            source="seed",
        )

    # Creation on, integration off: the price-lock rule takes its first branch.
    engine.update_settings(
        {
            "enable_creation_of_orders_from_quotes": True,
            "enable_sales_order_processing_integration": False,
            "ispricelocked_default": False,
        },
        room_id=room_id,
        actor="dana",
        source="seed",
    )

    # 1. An accepted and activated quote: a locked order.
    first = quote("Northwind annual licence")
    line(first, 1, "Platform licence", "SKU-101", 2, 1000.0)
    line(first, 2, "Onboarding", "SKU-102", 1, 2500.0)
    engine.convert(first, {"date_won": now.date().isoformat()}, actor="dana", source="seed")

    # Creation on, integration on with the table default No: an unlocked order.
    engine.update_settings(
        {"enable_sales_order_processing_integration": True, "ispricelocked_default": False},
        room_id=room_id,
        actor="dana",
        source="seed",
    )
    second = quote("Contoso onboarding")
    line(second, 1, "Platform licence", "SKU-101", 3, 1000.0)
    order = engine.convert(second, {}, actor="dana", source="seed")["order"]
    # A price-list edit applies under current pricing.
    if product_id and book_id and order:
        item = db.find(
            "wf087_price_book_item",
            {"price_book_id": book_id, "product_id": product_id},
            limit=1,
        )
        if item:
            db.update(item[0]["id"], {"prices": {"EUR": 900.0}}, actor="dana", source="seed")
    if order:
        # A write-in line, which a later reprice must never touch.
        engine.add_line(
            order["id"],
            {"name": "Expedited delivery", "quantity": 1, "unit_price": 450.0, "tax_rate": 21.0},
            actor="dana",
            source="seed",
        )
        engine.reprice(order["id"], actor="dana", source="seed")
        engine.close_order(
            order["id"],
            {"status": "fulfilled", "reason": "Delivered and invoiced by billing."},
            actor="dana",
            source="seed",
        )

    # 2. Accepted but not activated: the quote is not frozen yet.
    third = quote("Fabrikam negotiation", status="draft")
    line(third, 1, "Pilot seat", "SKU-103", 5, 300.0)
    engine.convert(third, {}, actor="dana", source="seed")

    # 3. Activated but not accepted: the customer has not agreed yet.
    fourth = quote("Adventure Works quote", hs_status=None)
    line(fourth, 1, "Legacy support", "SKU-104", 1, 900.0)
    engine.convert(fourth, {}, actor="dana", source="seed")

    # 4. Creation switched off: even an accepted and activated quote is refused.
    engine.update_settings(
        {"enable_creation_of_orders_from_quotes": False},
        room_id=room_id,
        actor="dana",
        source="seed",
    )
    fifth = quote("Wingtip evaluation")
    line(fifth, 1, "Evaluation seat", "SKU-105", 4, 200.0)
    engine.convert(fifth, {}, actor="dana", source="seed")
    engine.update_settings(
        {"enable_creation_of_orders_from_quotes": True},
        room_id=room_id,
        actor="dana",
        source="seed",
    )

    return (
        "5 quotes (1 locked order, 1 repriced and fulfilled order, 3 refused: not "
        "activated, not accepted, creation disabled)"
    )
