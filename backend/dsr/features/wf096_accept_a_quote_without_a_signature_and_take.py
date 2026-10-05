"""WF-096: accept a quote without a signature and take payment in the quote.

The rules live in :mod:`dsr.quote_payment` and are not restated here. This module is the three
things a feature contributes and the three things it must never contribute.

What this module contributes
----------------------------

* The route table, under the ticket-derived prefix ``/api/wf-096`` this feature owns.
* The mapping from this workflow's own error types to responses, exported as
  :data:`EXCEPTION_HANDLERS` because FastAPI accepts handlers on the app object only and this
  feature may not edit the app.
* The demo rows, as ``seed(db, context)`` rather than as an edit to the shared
  ``backend/seed.py``.

What it must never contribute
-----------------------------

* No shared file. ``dsr/api.py``, ``dsr/deps.py``, ``dsr/store.py``, ``dsr/db/audited.py``,
  ``backend/seed.py``, ``App.jsx``, ``main.jsx``, ``lib/api.js``, ``lib/features.js``,
  ``components/ui.jsx``, ``vite.config.js``.
* No import of the application module. Dependencies come from ``dsr.deps``. A test enforces it.
* No hand-written ``source=`` route string. :func:`_source` builds every one from
  :data:`router`, so the audit row names the route that actually served the write.

The status codes here are the product's own
-------------------------------------------

The specification documents no status codes for this workflow, so every one follows the
conventions the rest of this product uses:

``200``
    Everything, including a declined charge. "the total amount due must be more than $0.50"
    is a payment-processor outcome, and a declined charge is a definite result rather than a
    malformed request, so the answer is ``200`` with ``outcome: declined`` and the named
    reason. A caller can tell a declined payment from a broken route by reading the body.
``201``
    A resource was created: a quote, a line item, a tax ID.
``400``
    A value this workflow will not accept: an unknown acceptance method, *Print and sign* with
    online payments, *E-signature* on this workflow's clickwrap route, a fractional line-item
    quantity while billing is on, an unknown or disallowed payment method, a bad effective
    date, and a fourth tax ID.
``404``
    A quote, a payment setup, a charge or an invoice that does not exist. Never a 500.
``409``
    A well-formed request that conflicts with the record's state: a second acceptance, a
    second charge, a charge before acceptance, and a void or delete of an accepted quote.

What this workflow reads and does not own
-----------------------------------------

**The quote and its line items.** WF-086 provisions ``wf086_quote`` and ``wf086_line_item``.
This workflow reads both and patches exactly the properties in
:data:`~dsr.quote_payment.vocabulary.QUOTE_PROPERTIES_WRITTEN` onto the quote. It exposes two
demo routes that create a quote and a line item so the flow is demonstrable and testable
before WF-086 is driven from the UI; when it is, it writes the same two collections and this
workflow reads what it finds.

**The acceptance the e-signature path takes.** WF-095 owns ``esignature`` acceptance. This
workflow refuses it with a reason that names where it belongs rather than reimplementing it,
because a clickwrap flow that also ran a signing envelope would be two owners for one
agreement.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, Query, Request
from fastapi.responses import JSONResponse

from dsr.db.audited import AuditedDatabase
from dsr.deps import StoreDep
from dsr.quote_payment import vocabulary as vocab
from dsr.quote_payment.engine import QuotePaymentEngine
from dsr.quote_payment.errors import (
    PaymentRefused,
    QuoteNotFound,
    SetupNotFound,
    StateConflict,
)
from dsr.store import RecordStore

FEATURE = {
    "id": "wf-096-accept-a-quote-without-a-signature-and-take",
    "ticket": "WF-096",
    "prefix": "/api/wf-096",
    "name": "Accept a quote without a signature and take payment in the quote",
    "description": (
        "Publish a quote for online payments with the researched payment configuration, accept "
        "it by clickwrap when a signature is not needed, create the first invoice and the "
        "recurring subscriptions acceptance triggers, charge the amount due against the strict "
        "minimum, add buyer tax IDs, and record every refusal with a published reason code."
    ),
    "nav": [{"id": "wf-096-accept-and-pay", "label": "Accept and pay"}],
}

#: Ticket-derived, so it cannot collide with the feature-shaped prefixes several workflows
#: already share.
router = APIRouter(prefix="/api/wf-096", tags=["WF-096"])


def _source(method: str, path: str) -> str:
    """The audit ``source`` for a route, built from the router.

    Deliberately derived, never written as a literal: the defect this prevents is a domain
    function hardcoding a URL string, which leaves the audit log naming a route the app
    stopped serving. ``tests/test_wf096_http.py`` asserts every source this router can record
    matches a concrete ``(method, path)`` the host mounted.
    """

    return f"{method} {router.prefix}{path}"


def get_engine(store: RecordStore = StoreDep) -> QuotePaymentEngine:
    """A :class:`QuotePaymentEngine` over the process-wide audited store.

    Per request, for the same reason the rest of this product builds its engine per request:
    the engine holds nothing beyond the store and a clock, so building it here leaves every
    seam overridable in a test instead of hanging a long-lived object off ``app.state``, which
    is a shared file this feature may not edit.
    """

    return QuotePaymentEngine(store)


EngineDep = Depends(get_engine)


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
#
# All four types are declared in dsr.quote_payment.errors and raised by nothing else in the
# product. That is what makes it safe to map them here: the host refuses a second feature
# registering a handler for the same type, and a handler for ValueError would intercept that
# exception across the whole product.


def _payment_refused(request: Request, exc: PaymentRefused) -> JSONResponse:
    """400 with a field-keyed map, so each message lands next to its input."""

    return JSONResponse(status_code=400, content=exc.to_dict())


def _quote_not_found(request: Request, exc: QuoteNotFound) -> JSONResponse:
    return JSONResponse(status_code=404, content=exc.to_dict())


def _setup_not_found(request: Request, exc: SetupNotFound) -> JSONResponse:
    return JSONResponse(status_code=404, content=exc.to_dict())


def _state_conflict(request: Request, exc: StateConflict) -> JSONResponse:
    """409 naming the reason code, because the request was understood and the record refuses it."""

    return JSONResponse(status_code=409, content=exc.to_dict())


EXCEPTION_HANDLERS = {
    PaymentRefused: _payment_refused,
    QuoteNotFound: _quote_not_found,
    SetupNotFound: _setup_not_found,
    StateConflict: _state_conflict,
}


# --------------------------------------------------------------------------- #
# The board and the research
# --------------------------------------------------------------------------- #


@router.get("/summary")
def summary(
    room_id: str | None = Query(None), engine: QuotePaymentEngine = EngineDep
) -> dict[str, Any]:
    """The headline numbers, read back from the store. Reads only, so safe to poll."""

    return engine.summary(room_id)


@router.get("/vocabulary")
def vocabulary(engine: QuotePaymentEngine = EngineDep) -> dict[str, Any]:
    """The researched and derived vocabulary, so the page cannot drift from the rules behind it."""

    return engine.vocabulary()


@router.get("/decisions")
def decisions(engine: QuotePaymentEngine = EngineDep) -> dict[str, Any]:
    """Every judgement call this workflow made, with the alternative it rejected."""

    return {"count": len(engine.decisions()), "decisions": engine.decisions()}


# --------------------------------------------------------------------------- #
# The quote this workflow pays
# --------------------------------------------------------------------------- #
#
# WF-086 provisions the quote and its line items. These two routes exist so the flow is
# demonstrable and testable before WF-086 is driven from the UI, and so a payment has something
# to attach to. They write the same two collections WF-086 writes, and once it lands they are
# simply other writers of the same collections.


@router.post("/rooms/{room_id}/quotes", status_code=201)
def create_quote(
    room_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(None),
    engine: QuotePaymentEngine = EngineDep,
) -> dict[str, Any]:
    """A quote to accept and pay. Provisioned by WF-086 in production."""

    return engine.create_quote(
        room_id, payload, actor=actor, source=_source("POST", "/rooms/{room_id}/quotes")
    )


@router.post("/quotes/{quote_id}/line-items", status_code=201)
def create_line_item(
    quote_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(None),
    engine: QuotePaymentEngine = EngineDep,
) -> dict[str, Any]:
    """A line item on a quote, priced the way WF-086 prices one. Provisioned by WF-086."""

    return engine.create_line_item(
        quote_id, payload, actor=actor, source=_source("POST", "/quotes/{quote_id}/line-items")
    )


# --------------------------------------------------------------------------- #
# Publishing the payment configuration
# --------------------------------------------------------------------------- #


@router.post("/quotes/{quote_id}/publish")
def publish(
    quote_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(None),
    engine: QuotePaymentEngine = EngineDep,
) -> dict[str, Any]:
    """Publish a quote for online payments: the researched payment configuration.

    The acceptance method, the allowed payment methods, the billing frequency, the net terms
    and the effective date are validated first and nothing is written if any value is refused.
    The derived ``hs_payment_type`` is written to the quote, and ``hs_payment_status`` is set
    to ``PENDING``; no later step in the researched flow moves it.
    """

    return engine.publish(
        quote_id, payload, actor=actor, source=_source("POST", "/quotes/{quote_id}/publish")
    )


@router.get("/quotes")
def list_quotes(
    room_id: str | None = Query(None), engine: QuotePaymentEngine = EngineDep
) -> dict[str, Any]:
    """Every provisioned quote, each with its payment setup, acceptance, charges and invoices."""

    rows = engine.setups(room_id)
    return {"count": len(rows), "quotes": rows}


@router.get("/quotes/{quote_id}")
def read_quote(quote_id: str, engine: QuotePaymentEngine = EngineDep) -> dict[str, Any]:
    """One quote's whole payment picture: the setup, the acceptance, the charges, the invoices.

    A quote that exists but was never published for payments answers with ``setup: null``
    rather than a 404, because the quote is real and the absence of a setup is its true state.
    """

    return engine.quote_detail(quote_id)


# --------------------------------------------------------------------------- #
# The buyer's two steps
# --------------------------------------------------------------------------- #


@router.post("/quotes/{quote_id}/accept")
def accept(
    quote_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(None),
    engine: QuotePaymentEngine = EngineDep,
) -> dict[str, Any]:
    """Accept a quote without a signature, and create the invoices acceptance triggers.

    The research's buyer step: "clicks Accept (click-to-accept)". Acceptance writes
    ``hs_clickwrap_accepted_by`` and flips ``hs_status`` to ``ACCEPTED`` on the quote, then the
    first invoice is generated and sent immediately and each recurring line gets a subscription
    and a schedule. Payment is *not* taken here; it is a separate step the buyer may take now or
    revisit, so a declined charge can never undo an acceptance the buyer already gave.
    """

    return engine.accept(
        quote_id, payload, actor=actor, source=_source("POST", "/quotes/{quote_id}/accept")
    )


@router.post("/quotes/{quote_id}/payment")
def take_payment(
    quote_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(None),
    engine: QuotePaymentEngine = EngineDep,
) -> dict[str, Any]:
    """Record the payment processor's outcome for the quote's amount due.

    The research's buyer step: "then optionally Set up payment at the top of the quote; they can
    close and revisit the quote later to set up payment." A charge below the strict minimum is a
    recorded ``declined`` outcome and a 200, because a processor declining a charge is an
    ordinary result; an unknown or disallowed payment method is a 400, because that is a value
    this workflow will not accept.
    """

    return engine.record_charge(
        quote_id, payload, actor=actor, source=_source("POST", "/quotes/{quote_id}/payment")
    )


# --------------------------------------------------------------------------- #
# Invoices, tax IDs, void and delete
# --------------------------------------------------------------------------- #


@router.get("/quotes/{quote_id}/invoices")
def list_invoices(quote_id: str, engine: QuotePaymentEngine = EngineDep) -> dict[str, Any]:
    """The first invoice and every scheduled later invoice, oldest date first."""

    engine.quote_view(quote_id)
    rows = engine.invoices(quote_id)
    return {"quote_id": quote_id, "count": len(rows), "invoices": rows}


@router.get("/quotes/{quote_id}/charges")
def list_charges(quote_id: str, engine: QuotePaymentEngine = EngineDep) -> dict[str, Any]:
    """Every charge attempt, whether it was recorded or declined."""

    engine.quote_view(quote_id)
    rows = engine.charges(quote_id)
    return {"quote_id": quote_id, "count": len(rows), "charges": rows}


@router.post("/quotes/{quote_id}/tax-ids", status_code=201)
def add_tax_id(
    quote_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    actor: str | None = Query(None),
    engine: QuotePaymentEngine = EngineDep,
) -> dict[str, Any]:
    """Add a buyer tax ID, up to the researched cap of three."""

    return engine.add_tax_id(
        quote_id, payload, actor=actor, source=_source("POST", "/quotes/{quote_id}/tax-ids")
    )


@router.post("/quotes/{quote_id}/void")
def void(
    quote_id: str,
    actor: str | None = Query(None),
    engine: QuotePaymentEngine = EngineDep,
) -> dict[str, Any]:
    """Void a quote. It cannot be voided after it has been accepted."""

    return engine.void(quote_id, actor=actor, source=_source("POST", "/quotes/{quote_id}/void"))


@router.delete("/quotes/{quote_id}")
def delete(
    quote_id: str,
    actor: str | None = Query(None),
    engine: QuotePaymentEngine = EngineDep,
) -> dict[str, Any]:
    """Delete a quote. It cannot be deleted after it has been accepted."""

    return engine.delete(quote_id, actor=actor, source=_source("DELETE", "/quotes/{quote_id}"))


# --------------------------------------------------------------------------- #
# Demo data
# --------------------------------------------------------------------------- #


def seed(db: AuditedDatabase, context: dict[str, Any]) -> str:
    """Create the states this workflow exists to show, on the first room.

    The states seeded, and why each is here:

    * **accepted and charged** — the whole flow carried through, so the board has a completed
      row with an acceptance, a charge, a first invoice and a subscription;
    * **accepted, charge declined** — an amount below the strict minimum, so the
      ``amount_due_below_minimum`` outcome is visible as a recorded decline rather than an
      empty list;
    * **publish refused for a fractional quantity** — the whole-number rule fired, so a
      reviewer sees the rule refuse rather than only its passing case;
    * **a fourth tax ID refused** — the three-tax-ID cap fired, for the same reason.

    Every state except the refused publishes is reached by calling the engine, not by writing
    rows directly. That is deliberate: it proves the rules produce these states, rather than
    the seed asserting them.

    The return string is ASCII and is asserted encodable by cp1252 in ``tests/test_wf096.py``:
    the seeder prints it to a Windows console, and one RIGHTWARDS ARROW in a recovered feature's
    return string broke the whole seeder.
    """

    store = RecordStore(db)
    room_ids: list[tuple[str, str]] = list(context.get("room_ids") or [])
    if not room_ids:
        return ""

    room_id = room_ids[0][0]
    now = context["now"]
    source = "wf-096 seed"
    engine = QuotePaymentEngine(store, now=lambda: now)

    def _quote(title: str) -> str:
        record = store.create(
            vocab.QUOTE_COLLECTION,
            {
                "title": title,
                "company_name": "Northwind Logistics",
                "currency": "USD",
                "status": "published",
            },
            room_id=room_id,
            actor="dana",
            source=source,
        )
        return record["id"]

    def _line(quote_id: str, **fields: Any) -> None:
        line = {
            "quote_id": quote_id,
            "name": fields.pop("name", "Line"),
            "quantity": fields.pop("quantity", 1),
            "unit_price": fields.pop("unit_price", 0),
            "tax_rate": fields.pop("tax_rate", 0),
            "discount_type": fields.pop("discount_type", "percentage"),
            "discount_value": fields.pop("discount_value", 0),
            "billing_frequency": fields.pop("billing_frequency", "one_time"),
            "position": fields.pop("position", 0),
        }
        line.update(fields)
        engine.create_line_item(quote_id, line, actor="dana", source=source)

    # 1. Accepted and charged. A one-time line and a monthly line, so a subscription and a
    #    schedule both exist beside the immediate first invoice.
    accepted_id = _quote("Northwind platform renewal")
    _line(accepted_id, name="Implementation", quantity=1, unit_price=1200, position=0)
    _line(
        accepted_id,
        name="Platform seats",
        quantity=25,
        unit_price=12,
        billing_frequency="monthly",
        position=1,
    )
    engine.publish(
        accepted_id,
        {"acceptance_method": "clickwrap", "allowed_payment_methods": ["CREDIT_OR_DEBIT_CARD"]},
        actor="dana",
        source=source,
    )
    engine.accept(accepted_id, {"accepted_by": "Ada Byron"}, actor="ada", source=source)
    engine.record_charge(
        accepted_id,
        {"payment_method": "CREDIT_OR_DEBIT_CARD"},
        actor="ada",
        source=source,
    )

    # 2. Accepted, charge declined. Twenty cents is not more than the fifty-cent minimum.
    declined_id = _quote("Orbis sample order")
    _line(declined_id, name="Sample kit", quantity=1, unit_price=0.2, position=0)
    engine.publish(
        declined_id,
        {"acceptance_method": "clickwrap"},
        actor="dana",
        source=source,
    )
    engine.accept(declined_id, {}, actor="rui", source=source)
    engine.record_charge(declined_id, {}, actor="rui", source=source)

    # 3. Publish refused for a fractional quantity. Caught, not swallowed: the refusal is the
    #    state being demonstrated, and the summary names it.
    fractional_id = _quote("Halcyon part-day engagement")
    _line(fractional_id, name="Consulting days", quantity=1.5, unit_price=900, position=0)
    try:
        engine.publish(fractional_id, {}, actor="dana", source=source)
    except PaymentRefused:
        pass

    # 4. A fourth tax ID refused.
    capped_id = _quote("Cobalt renewal")
    _line(capped_id, name="Licence", quantity=1, unit_price=500, position=0)
    engine.publish(capped_id, {}, actor="dana", source=source)
    for index in range(vocab.TAX_ID_LIMIT):
        engine.add_tax_id(
            capped_id,
            {"value": f"US-{index + 1:03d}"},
            actor="rui",
            source=source,
        )
    try:
        engine.add_tax_id(capped_id, {"value": "US-004"}, actor="rui", source=source)
    except PaymentRefused:
        pass

    summary = engine.summary(room_id)
    return (
        f"{summary['quotes']} published quotes: {summary['accepted']} accepted, "
        f"{summary['charges_recorded']} charged, {summary['charges_declined']} declined, "
        f"{summary['invoices']} invoices, {summary['subscriptions']} subscriptions, "
        f"{summary['tax_ids']} tax IDs; 1 publish refused for a fractional quantity, "
        f"1 tax ID refused above the cap of {vocab.TAX_ID_LIMIT}"
    )
